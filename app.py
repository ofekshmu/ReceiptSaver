"""
app.py
------
Frameless pywebview window for Receipt Saver. Opens at login, drives the
mailbox scan on a worker thread, streams progress into the page, serves the
history and fallback data, and applies fallback decisions.

Run:  pythonw app.py
"""

import json
import os
import re
import threading
import datetime
from pathlib import Path

import receipt_saver
import history
import fallback_ops
import claude_handoff
import receipt_roots
import ui_state

SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
UI_DIR            = SCRIPT_DIR / "ui"
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"

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
        return fallback_ops.suggest(entry) if entry else {}

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

    # -- misc ------------------------------------------------------------
    def open_folder(self, path: str) -> dict:
        try:
            os.startfile(path)  # noqa: S606  (Windows only, user-chosen path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_path(self, path: str) -> dict:
        return self.open_folder(path)

    def categories(self) -> list:
        return fallback_ops.CATEGORIES

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

    def minimize(self):
        if self._window:
            self._window.minimize()

    def hide(self):
        if self._window:
            self._window.hide()

    def quit_app(self):
        os._exit(0)


def main():
    try:
        import webview
    except Exception as e:
        with open(SCRIPT_DIR / "receipt_saver.log", "a", encoding="utf-8") as f:
            f.write(f"\n[app.py] pywebview not available: {e}\n")
        raise SystemExit(1)

    # RECEIPT_SAVER_UI_DRYRUN=1 boots the window without touching any mailbox —
    # used to smoke-test the UI. The scan reports "nothing new".
    if os.environ.get("RECEIPT_SAVER_UI_DRYRUN") == "1":
        api = Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
    else:
        api = Api()

    try:
        scr = webview.screens[0]
        win_x = max(0, (scr.width  - 980) // 2)
        win_y = max(0, (scr.height - 680) // 2)
    except Exception:
        win_x, win_y = 120, 120

    window = webview.create_window(
        "Receipt Saver",
        url=str(UI_DIR / "index.html"),
        js_api=api,
        width=980, height=680, x=win_x, y=win_y,
        frameless=True, easy_drag=False,
        background_color="#0f1115",
    )
    api._win_x, api._win_y = win_x, win_y
    api.bind(window)

    def _bootstrap():
        api.start_scan()

    try:
        import tray
        threading.Thread(target=tray.run, args=(api,), daemon=True).start()
    except Exception:
        pass

    webview.start(_bootstrap)


if __name__ == "__main__":
    main()
