"""
app.py
------
Frameless pywebview window for Receipt Saver. Drives the mailbox scan on a
worker thread, streams progress into the page, serves the history and
fallback data, and applies fallback decisions.

The scan only starts automatically when launched with --autostart (the
logon scheduled task registered by install_startup.py); a manual launch
(desktop/Start Menu shortcut) opens idle and waits for the user to click
"Run scan" so opening the app doesn't always trigger a mailbox scan.

Run:  pythonw app.py [--autostart]
"""

import json
import os
import re
import sys
import threading
import datetime
from pathlib import Path

import receipt_saver
import history
import fallback_ops
import claude_handoff
import receipt_roots
import ui_state
import version

SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
UI_DIR            = SCRIPT_DIR / "ui"
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"
LOG_FILE          = SCRIPT_DIR / "receipt_saver.log"
# Measured with Playwright against ui/index.html: the titlebar (brand + tabs +
# win-controls) needs ~550px before it starts clipping. This floor sits well
# above that, with margin for font/DPI differences between engines — the
# window can be shrunk, but not far enough to break the header.
WIN_MIN_W, WIN_MIN_H = 820, 520


def _log(msg: str) -> None:
    """Append a timestamped [app.py] line to the shared log. Never raises —
    this is the only breadcrumb trail when the window fails to appear at login."""
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts}  [app.py] {msg}\n")
    except Exception:
        pass

_DATED_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})")
_FOLDER_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2}) - (.*)$")
_DUP_RE = re.compile(r"( \(\d+\))$")
_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ACCOUNT_LABELS = tuple(a["label"] for a in receipt_saver.ACCOUNTS)


def _parse_folder_name(name: str) -> dict | None:
    """Split 'YYYY_MM_DD - Seller - Product - label [(n)]' into a clean title,
    a human date, and the account label. Returns None if the name is not in
    that shape (plain folders, files)."""
    m = _FOLDER_RE.match(name)
    if not m:
        return None
    y, mo, d, rest = m.groups()
    dup = ""
    md = _DUP_RE.search(rest)
    if md:
        dup, rest = md.group(1), rest[: md.start()]
    account = ""
    for lbl in _ACCOUNT_LABELS:
        if rest.endswith(f" - {lbl}"):
            account, rest = lbl, rest[: -(len(lbl) + 3)]
            break
    try:
        date_display = f"{int(d)} {_MONTHS[int(mo)]} {y}"
    except (ValueError, IndexError):
        date_display = f"{y}-{mo}-{d}"
    return {"title": (rest.strip() + dup) or name,
            "date_display": date_display, "account": account}


def _entry_sort_key(e: dict):
    # dirs before files; dated dirs by date desc; then name (case-insensitive)
    is_file = 0 if e["is_dir"] else 1
    m = _DATED_RE.match(e["name"]) if e["is_dir"] else None
    dated = 0 if m else 1
    date_key = (-int(m.group(1) + m.group(2) + m.group(3))) if m else 0
    return (is_file, dated, date_key, e["name"].lower())


class Api:
    def __init__(self, scan_fn=None, fallback_log_path: Path = None):
        self._scan_fn = scan_fn or receipt_saver.main
        self._fallback_log_path = Path(fallback_log_path or FALLBACK_LOG_FILE)
        self._window = None
        self._win_x = None
        self._win_y = None
        self._lock = threading.Lock()
        self._thread = None
        self._run = {"status": "idle", "events": [], "summary": None}
        self._body_cache = {}       # message_id -> fetched body text (per session)

    # -- wiring ------------------------------------------------------------
    def bind(self, window):
        self._window = window

    def _push(self, event: dict):
        self._run["events"].append(event)
        if self._window is not None:
            try:
                payload = json.dumps(event, ensure_ascii=False)
                self._window.evaluate_js(f"window.onScanEvent && window.onScanEvent({payload})")
            except Exception:
                pass

    # -- scan -----------------------------------------------------------
    def scan_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_scan(self) -> dict:
        with self._lock:
            if self.scan_running():
                return {"status": "busy"}
            self._run = {"status": "running", "events": [], "summary": None}
            run_id = datetime.datetime.now().isoformat(timespec="seconds")
            self._thread = threading.Thread(
                target=self._run_scan, args=(run_id,), daemon=True)
            self._thread.start()
            return {"status": "running", "run_id": run_id}

    def _run_scan(self, run_id: str):
        summary = {"run_id": run_id, "saved": 0, "fallback": 0,
                   "excluded": 0, "records": []}
        try:
            summary = self._scan_fn(run_id=run_id, progress_cb=self._push) or summary
            self._run["status"] = "done"
        except Exception as e:
            self._run["status"] = "error"
            self._push({"type": "error", "label": "-", "message": str(e)})
        finally:
            self._run["summary"] = summary
            if not any(e.get("type") == "done" for e in self._run["events"]):
                self._push({"type": "done",
                            "run_id": summary.get("run_id", run_id),
                            "saved": summary.get("saved", 0),
                            "fallback": summary.get("fallback", 0),
                            "excluded": summary.get("excluded", 0),
                            "status": self._run["status"]})

    def get_run(self) -> dict:
        return self._run

    # -- history ---------------------------------------------------------
    def get_history(self, offset: int = 0, limit: int = 50) -> list:
        return history.page(int(offset), int(limit))

    # -- fallbacks -----------------------------------------------------
    def _load_fallbacks(self) -> list:
        try:
            return json.loads(self._fallback_log_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def get_fallbacks(self) -> list:
        return [e for e in self._load_fallbacks() if not e.get("resolved")]

    def _fallback_by_id(self, message_id: str):
        for e in self._load_fallbacks():
            if e.get("message_id") == message_id:
                return e
        return None

    def suggest_fallback(self, message_id: str) -> dict:
        entry = self._fallback_by_id(message_id)
        if not entry:
            return {}
        return {**fallback_ops.suggest(entry),
                "attachment_count": self._attachment_count(entry)}

    @staticmethod
    def _attachment_count(entry: dict):
        """Documents attached to a fallback mail: recorded at scan time, else
        counted from its folder (minus email.pdf; images never count)."""
        import categories as C
        if entry.get("attachment_count") is not None:
            return entry["attachment_count"]
        try:
            names = [n for n in os.listdir(entry.get("folder_path") or "")
                     if n.lower() != "email.pdf"]
        except OSError:
            return None
        return C.count_documents(names)

    def apply_fallback(self, message_id: str, decision: dict) -> dict:
        entry = self._fallback_by_id(message_id)
        if not entry:
            return {"ok": False, "error": "entry not found"}
        try:
            return fallback_ops.apply_decision(entry, decision,
                                               fallback_log_path=self._fallback_log_path)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handoff(self, message_ids: list) -> dict:
        entries = [e for e in self._load_fallbacks()
                   if e.get("message_id") in set(message_ids)]
        if not entries:
            return {"ok": False, "error": "no matching entries"}
        try:
            claude_handoff.launch(entries)
            return {"ok": True, "count": len(entries)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def ask_claude_error(self, message: str) -> dict:
        """Open a `claude` terminal in the repo, seeded to debug `message`."""
        try:
            claude_handoff.launch_error(message or "")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def ask_claude_receipt(self, entry: dict) -> dict:
        """Open a `claude` terminal in the repo, seeded to help with one
        Receipts-explorer entry (right-click an entry -> Ask Claude)."""
        try:
            claude_handoff.launch_receipt(entry or {})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -- misc ------------------------------------------------------------
    def open_folder(self, path: str) -> dict:
        try:
            os.startfile(path)  # noqa: S606  (Windows only, user-chosen path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_path(self, path: str) -> dict:
        return self.open_folder(path)

    # -- bill categories (categories.json) ------------------------------
    def _cat_write(self, mutate) -> dict:
        """load -> mutate(cats) -> (save if truthy) -> return {ok, categories}."""
        import categories as C
        try:
            cats = C.load_categories()
            changed = mutate(C, cats)
            if changed is False:
                return {"ok": False, "error": "no change / not found"}
            C.save_categories(cats)
            return {"ok": True, "categories": cats}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_categories(self) -> list:
        import categories as C
        return C.load_categories()

    def category_add(self, name: str, config: dict = None) -> dict:
        cfg = config or {}
        return self._cat_write(lambda C, cats: cats.append(
            C.new_category(name, destination=cfg.get("destination"),
                           seller=cfg.get("seller"), product=cfg.get("product"),
                           exclude=cfg.get("exclude", False), categories=cats)) or True)

    def category_update(self, category_id: str, patch: dict) -> dict:
        return self._cat_write(lambda C, cats: C.update_category(cats, category_id, patch or {}))

    def category_delete(self, category_id: str) -> dict:
        return self._cat_write(lambda C, cats: C.delete_category(cats, category_id))

    def category_merge(self, src_id: str, dst_id: str) -> dict:
        return self._cat_write(lambda C, cats: C.merge_categories(cats, src_id, dst_id))

    def category_remove_match(self, category_id: str, index: int) -> dict:
        return self._cat_write(lambda C, cats: C.remove_match(cats, category_id, int(index)))

    def category_add_match(self, category_id: str, entry: dict) -> dict:
        return self._cat_write(lambda C, cats: C.add_match(cats, category_id, entry or {}))

    # -- fallback form helpers -------------------------------------------
    def _fetch_text(self, entry: dict) -> dict:
        """Fetch one mail from its mailbox (network) — {"body": str}."""
        acct = next((a for a in receipt_saver.ACCOUNTS
                     if a["label"] == entry.get("account")), None)
        if acct is None:
            raise RuntimeError(f'unknown account {entry.get("account")!r}')
        provider = receipt_saver.PROVIDERS[acct["provider"]]
        service = provider.get_service(acct)
        msg = provider.fetch_message(service, entry["message_id"], acct)
        return {"body": msg.get("body_text") or ""}

    def _body(self, entry: dict) -> str:
        mid = entry["message_id"]
        if mid not in self._body_cache:
            self._body_cache[mid] = self._fetch_text(entry).get("body") or ""
        return self._body_cache[mid]

    def preview_extract(self, message_id: str, source: str, regex: str) -> dict:
        """Run an extraction rule against one fallback mail, exactly as the scan
        engine would (Python `re`). value is None on a miss."""
        import categories as C
        entry = self._fallback_by_id(message_id)
        if not entry:
            return {"ok": False, "error": "entry not found"}
        try:
            body = self._body(entry) if source == "body" else ""
        except Exception as e:
            return {"ok": False, "error": f"couldn't fetch the mail body: {e}"}
        try:
            value = C.extract(source, regex or "", entry.get("sender", ""),
                              entry.get("subject", ""), body)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "value": value}

    def keyword_suggestions(self, message_id: str, include_body: bool = False) -> dict:
        """Keyword chips for building a match rule from one fallback mail.
        Without `include_body` this never touches the network."""
        import keywords
        entry = self._fallback_by_id(message_id)
        if not entry:
            return {"ok": False, "error": "entry not found"}
        body, body_error = "", None
        if include_body:
            try:
                body = self._body(entry)
            except Exception as e:
                body_error = f"couldn't fetch the mail body: {e}"
        out = {"ok": True, **keywords.suggest(entry.get("sender", ""),
                                              entry.get("subject", ""), body)}
        if body_error:
            out["body_error"] = body_error
        return out

    def preview_category(self, message_id: str, category_id: str) -> dict:
        """What an existing category would name and file this mail as."""
        import categories as C
        entry = self._fallback_by_id(message_id)
        cat = C.find(C.load_categories(), category_id)
        if not entry or not cat:
            return {"ok": False, "error": "entry or category not found"}
        body = ""
        if C.needs_body(cat):
            try:
                body = self._body(entry)
            except Exception:
                body = ""                     # offline: fall back to the suggestion
        seller, product = C.resolve_names(cat, entry.get("sender", ""),
                                          entry.get("subject", ""), body)
        return {"ok": True, "seller": seller, "product": product,
                "destination": str(C.destination_of(cat))}

    def destination_suggestions(self) -> list:
        """Destinations for the picker, most used first: category destinations +
        the folders History filed into + every root (never the fallback dir)."""
        import categories as C
        manual = receipt_roots.MANUAL_DIR
        roots = [r for r in receipt_roots.discover_roots()
                 if not receipt_roots._inside(r["path"], manual)]
        counts = {}

        def bump(path, n=1):
            if not path or receipt_roots._inside(path, manual):
                return
            key = receipt_roots._norm(path)
            counts.setdefault(key, [str(path), 0])[1] += n

        for cat in C.load_categories():
            if not cat.get("exclude"):
                bump(cat.get("destination"))
        for row in history.load():
            if row.get("action") != "FALLBACK" and row.get("folder_path"):
                bump(os.path.dirname(row["folder_path"]))
        for r in roots:
            bump(r["path"], 0)

        def label(path):
            best = None
            for r in roots:
                if receipt_roots._inside(path, r["path"]) and (
                        best is None or len(r["path"]) > len(best["path"])):
                    best = r
            if best is None:
                return " › ".join(Path(path).parts[-3:])
            rel = os.path.relpath(path, best["path"])
            parts = [] if rel == "." else Path(rel).parts
            return " › ".join([best["label"], *parts])

        out = [{"path": p, "label": label(p), "count": n} for p, n in counts.values()]
        out.sort(key=lambda d: (-d["count"], d["label"].lower()))
        return out

    # -- ui state ---------------------------------------------------------
    def get_ui_state(self) -> dict:
        return ui_state.load()

    def set_ui_state(self, patch: dict) -> dict:
        return ui_state.save(patch or {})

    # -- receipts search ------------------------------------------------
    def search_receipts(self, query: str, limit: int = 200) -> dict:
        q = (query or "").strip().lower()
        if len(q) < 2:
            return {"query": query, "results": [], "truncated": False}
        results, truncated = [], False
        for root in receipt_roots.discover_roots():
            rp = root["path"]
            if not os.path.isdir(rp):
                continue
            base_depth = rp.rstrip("\\/").count(os.sep)
            for cur, dirs, files in os.walk(rp):
                if cur.count(os.sep) - base_depth > 6:
                    dirs[:] = []
                    continue
                for d in list(dirs):
                    if q in d.lower():
                        full = os.path.join(cur, d)
                        results.append(self._search_hit(full, True, root["label"], rp))
                        dirs.remove(d)
                for f in files:
                    if q in f.lower():
                        results.append(self._search_hit(os.path.join(cur, f), False,
                                                        root["label"], rp))
                if len(results) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        results.sort(key=lambda r: (0 if r["is_dir"] else 1,
                                    r["root_label"].lower(), r["rel"].lower()))
        return {"query": query, "results": results[:limit], "truncated": truncated}

    def _search_hit(self, full: str, is_dir: bool, root_label: str, root_path: str) -> dict:
        name = os.path.basename(full)
        if is_dir:
            kind = "receipt-folder" if _DATED_RE.match(name) else "folder"
        elif name.lower().endswith(".pdf"):
            kind = "pdf"
        else:
            kind = "file"
        parsed = _parse_folder_name(name) if is_dir else None
        return {"name": name, "path": full, "is_dir": is_dir, "kind": kind,
                "root_label": root_label, "rel": os.path.relpath(full, root_path),
                "title": parsed["title"] if parsed else name,
                "date_display": parsed["date_display"] if parsed else "",
                "account": parsed["account"] if parsed else ""}

    # -- receipts explorer (read-only) --------------------------------------
    def list_roots(self) -> list:
        return [{**r, "exists": os.path.isdir(r["path"])}
                for r in receipt_roots.discover_roots()]

    def _root_for(self, p: Path):
        best = None
        for r in receipt_roots.discover_roots():
            rp = Path(r["path"])
            try:
                p.relative_to(rp)
            except ValueError:
                continue
            if best is None or len(str(rp)) > len(str(Path(best["path"]))):
                best = r
        return best

    def _crumbs(self, p: Path) -> list:
        root = self._root_for(p)
        if not root:
            return [{"name": p.name or str(p), "path": str(p)}]
        rp = Path(root["path"])
        crumbs = [{"name": root["label"], "path": str(rp)}]
        acc = rp
        for part in p.relative_to(rp).parts:
            acc = acc / part
            crumbs.append({"name": part, "path": str(acc)})
        return crumbs

    def _entry(self, child: Path) -> dict:
        try:
            is_dir = child.is_dir()
        except OSError:
            is_dir = False
        name = child.name
        if is_dir:
            kind = "receipt-folder" if _DATED_RE.match(name) else "folder"
        elif name.lower().endswith(".pdf"):
            kind = "pdf"
        else:
            kind = "file"
        size = mtime = None
        try:
            st = child.stat()
            mtime = st.st_mtime
            if not is_dir:
                size = st.st_size
        except OSError:
            pass
        parsed = _parse_folder_name(name) if is_dir else None
        return {"name": name, "path": str(child), "is_dir": is_dir,
                "kind": kind, "size": size, "mtime": mtime,
                "title": parsed["title"] if parsed else name,
                "date_display": parsed["date_display"] if parsed else "",
                "account": parsed["account"] if parsed else ""}

    def browse(self, path: str) -> dict:
        if not receipt_roots.is_within_roots(path):
            return {"error": "path is outside the known receipt roots",
                    "path": path, "crumbs": [], "entries": []}
        p = Path(path)
        crumbs = self._crumbs(p)
        root = self._root_for(p)
        label = root["label"] if root else (p.name or str(p))
        if not p.is_dir():
            return {"error": "folder not found", "path": str(p),
                    "label": label, "crumbs": crumbs, "entries": []}
        try:
            entries = [self._entry(c) for c in p.iterdir()]
        except OSError as e:
            return {"error": str(e), "path": str(p),
                    "label": label, "crumbs": crumbs, "entries": []}
        entries.sort(key=_entry_sort_key)
        return {"path": str(p), "label": label, "crumbs": crumbs, "entries": entries}

    def _ensure_pos(self):
        if self._win_x is None or self._win_y is None:
            try:
                import webview
                scr = webview.screens[0]
                self._win_x = max(0, (scr.width  - int(self._window.width))  // 2)
                self._win_y = max(0, (scr.height - int(self._window.height)) // 2)
            except Exception:
                self._win_x, self._win_y = 120, 120

    def move_by(self, dx, dy):
        """Relative window move. The page sends origin-independent pointer
        deltas (movementX/movementY); we keep the absolute position here so we
        never depend on the webview's screenX (which is window-relative on this
        backend and made the drag jump)."""
        if not self._window:
            return
        self._ensure_pos()
        self._win_x += int(dx)
        self._win_y += int(dy)
        try:
            self._window.move(self._win_x, self._win_y)
        except Exception:
            pass

    def resize_by(self, dx, dy):
        """Relative window resize, driven by a custom drag-grip in the corner —
        the window is frameless (no OS border), so there's no native resize
        handle. Keeps the top-left corner fixed, matching a bottom-right grip."""
        if not self._window:
            return
        try:
            from webview.window import FixPoint
            new_w = max(WIN_MIN_W, int(self._window.width) + int(dx))
            new_h = max(WIN_MIN_H, int(self._window.height) + int(dy))
            self._window.resize(new_w, new_h, FixPoint.NORTH | FixPoint.WEST)
        except Exception:
            pass

    def pick_folder(self) -> dict:
        """Open the OS folder picker and return the chosen directory. Used by
        the Destination pickers (Fallbacks form, Categories tab).
        `path` is None when the user cancels (or there's no window yet)."""
        if not self._window:
            return {"ok": True, "path": None}
        try:
            import webview
            picked = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            path = picked[0] if picked else None
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def minimize(self):
        if self._window:
            self._window.minimize()

    def hide(self):
        if self._window:
            self._window.hide()

    def quit_app(self):
        os._exit(0)

    def app_version(self) -> str:
        return version.full_version()


def main():
    import traceback
    _log(f"launch v{version.full_version()}  pid={os.getpid()}  cwd={os.getcwd()}")
    try:
        _run()
    except SystemExit:
        raise
    except Exception:
        _log("FATAL during startup:\n" + traceback.format_exc())
        raise


def _run():
    import webview
    _log(f"pywebview ok (backend hint: {getattr(webview, 'platform', '?')})")

    # RECEIPT_SAVER_UI_DRYRUN=1 boots the window without touching any mailbox —
    # used to smoke-test the UI. The scan reports "nothing new". Pass
    # --autostart too if you want it to run automatically like a logon
    # launch would, rather than opening idle for a manual "Run scan" click.
    if os.environ.get("RECEIPT_SAVER_UI_DRYRUN") == "1":
        api = Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
    else:
        api = Api()

    saved = ui_state.load()
    win_w = max(WIN_MIN_W, int(saved["win_w"]))
    win_h = max(WIN_MIN_H, int(saved["win_h"]))

    try:
        scr = webview.screens[0]
        win_x = max(0, (scr.width  - win_w) // 2)
        win_y = max(0, (scr.height - win_h) // 2)
    except Exception:
        win_x, win_y = 120, 120

    window = webview.create_window(
        "Receipt Saver",
        url=str(UI_DIR / "index.html"),
        js_api=api,
        width=win_w, height=win_h, x=win_x, y=win_y,
        min_size=(WIN_MIN_W, WIN_MIN_H),
        frameless=True, easy_drag=False, resizable=True,
        background_color="#0f1115",
        on_top=True,   # surface above the other apps that launch at login
    )
    api._win_x, api._win_y = win_x, win_y
    api.bind(window)
    _log(f"window created at ({win_x},{win_y}) size {win_w}x{win_h}")

    # Debounced: a drag-to-resize fires many events, so only persist the
    # final size once the user has stopped dragging for a moment.
    _resize_timer = None

    def _on_resized(width, height):
        nonlocal _resize_timer
        if _resize_timer is not None:
            _resize_timer.cancel()

        def _save():
            try:
                ui_state.save({"win_w": int(width), "win_h": int(height)})
            except Exception:
                pass
        _resize_timer = threading.Timer(0.5, _save)
        _resize_timer.daemon = True
        _resize_timer.start()

    window.events.resized += _on_resized

    autostart = "--autostart" in sys.argv

    def _bootstrap():
        # Drop always-on-top once we're visible, but keep focus.
        try:
            window.on_top = False
        except Exception:
            pass
        if autostart:
            _log("window shown — starting scan (autostart)")
            api.start_scan()
        else:
            _log("window shown — manual launch, waiting for user to start scan")

    try:
        import tray
        threading.Thread(target=tray.run, args=(api,), daemon=True).start()
    except Exception:
        pass

    webview.start(_bootstrap)


if __name__ == "__main__":
    main()
