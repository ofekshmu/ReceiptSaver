# Startup UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a borderless desktop window that opens at Windows login, drives the mailbox scan, shows what was handled this run and the full history, and provides an interactive console for resolving fallback emails.

**Architecture:** A pywebview frameless window (`app.py`) hosts an HTML/CSS/JS frontend and a Python `Api` object. The existing `receipt_saver.py` scan engine is lightly refactored to accept a progress callback and emit a structured record per handled mail; every record is appended to a new `history.json`. A `fallback_ops.py` module produces heuristic classification suggestions and applies user decisions (append a `custom_rules.json` rule, move the folder out of `_לטיפול ידני`, mark `fallback_log.json` resolved). `claude_handoff.py` launches a pre-seeded `claude` terminal for fallbacks the heuristics can't classify. A `pystray` tray icon keeps the process resident so the window can be reopened.

**Tech Stack:** Python 3, pywebview, pystray, Pillow, existing Google/MSAL/weasyprint stack. Frontend: plain HTML/CSS/JS, no build step. Tests: pytest (runs the existing `unittest`-style suites).

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `history.py` | create | Load / append / update / page `history.json`. Atomic writes, module-level lock. |
| `fallback_ops.py` | create | `suggest()` heuristic classifier; `compute_destination()`; `apply_decision()` (rule / once / exclude / skip) which writes rules, moves folders, marks resolved, updates history. |
| `claude_handoff.py` | create | `build_prompt(entries)` and `launch(entries)` — open a terminal running `claude "<prompt>"`. |
| `app.py` | create | pywebview window + `Api` class exposed to JS. Worker-thread scan, progress forwarding, data accessors, decision dispatch, window controls. Login entry point. |
| `tray.py` | create | `pystray` icon (Open / Run scan now / Quit); started by `app.py`. |
| `ui/index.html` | create | Markup: custom title strip, segmented control, three view containers, toast host. |
| `ui/app.css` | create | Light professional theme. |
| `ui/app.js` | create | View rendering, progress stream handling, fallback forms, `pywebview.api` calls. |
| `make_shortcut.py` | create | One-off: create a Start Menu shortcut to `run.bat`. |
| `requirements.txt` | create | Pin all dependencies. |
| `receipt_saver.py` | modify | `_make_record()` helper; `process_message(msg, account, run_id)` returns `{"status", "record"}`; `main(run_id=None, progress_cb=None)` emits events, appends history, returns a summary dict. Standalone CLI behavior unchanged when called with no args. |
| `move_fallbacks.py` | modify | Reduce to a thin CLI wrapper that calls `fallback_ops` for a hard-coded batch (kept for manual use). *(Optional — only if time permits; not required for the feature.)* |
| `run.bat` | create/modify | `start "" pythonw "%~dp0app.py"`. |
| `test_history.py` | create | Tests for `history.py`. |
| `test_fallback_ops.py` | create | Tests for `fallback_ops.py`. |
| `test_claude_handoff.py` | create | Tests for prompt building. |
| `test_app_api.py` | create | Tests for `Api` data methods with an injected fake scan. |
| `test_receipt_saver.py` | modify | Add record-shape and `main()` progress-callback tests. |
| `DOCUMENTATION.md` | modify | New "Startup UI" section, dependency list, file table. |

**Record shape** (produced by `receipt_saver._make_record`, stored in `history.json`, passed to the UI):

```python
{
    "id": "ofek:19d53a0b755c51b7",     # f"{account_label}:{message_id}"
    "run_id": "2026-08-27T19:00:12",
    "handled_at": "2026-08-27T19:00:14",
    "account": "ofek",
    "account_email": "ofek.shmuel1@gmail.com",
    "date": "2026_08_25",
    "sender": "Heshbon@electra-power.co.il",
    "subject": "חשבונית חשמל סופרפאוור 55955672",
    "action": "DOWNLOADED",            # DOWNLOADED | ICOUNT | JAPANOLOGIA | FALLBACK | EXCLUDED | RESOLVED
    "seller": "אלקטרה פאוור",          # or None
    "product": "חשבונית חשמל",         # or None
    "category": "חשבנות/חשמל",         # or None
    "folder_name": "2026_08_25 - אלקטרה פאוור - חשבונית חשמל - ofek",  # or None
    "folder_path": "C:\\...\\2026_08_25 - ...",                        # or None
    "files": ["invoice.pdf", "email.pdf"],
    "rule_source": "custom",           # hardcoded | custom | icount | japanologia | None
}
```

**Decision object** (JS → `Api.apply_fallback(message_id, decision)`):

```python
{
    "kind": "rule",                        # rule | once | exclude | skip
    "seller": "אלקטרה פאוור",
    "product": "חשבונית חשמל",
    "category": "חשבנות/חשמל",             # or None
    "base_dir": None,                      # or an absolute path string
    "match_sender_contains": "electra-power.co.il",
    "match_subject_contains": None,        # or a string
}
```

---

## Task 1: `history.py` — storage module

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\history.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_history.py`

- [ ] **Step 1: Write the failing test**

Create `test_history.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

import history


def _rec(mid, **over):
    r = {
        "id": mid, "run_id": "2026-08-27T10:00:00", "handled_at": "2026-08-27T10:00:01",
        "account": "ofek", "account_email": "o@x.com", "date": "2026_08_25",
        "sender": "a@b.com", "subject": "s", "action": "DOWNLOADED",
        "seller": "S", "product": "P", "category": None,
        "folder_name": "f", "folder_path": "C:\\f", "files": ["email.pdf"],
        "rule_source": "custom",
    }
    r.update(over)
    return r


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "history.json"

    def test_append_then_load_roundtrip(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.append(_rec("ofek:2"), path=self.tmp)
        rows = history.load(path=self.tmp)
        self.assertEqual([r["id"] for r in rows], ["ofek:1", "ofek:2"])

    def test_append_dedups_by_id(self):
        history.append(_rec("ofek:1", seller="First"), path=self.tmp)
        history.append(_rec("ofek:1", seller="Second"), path=self.tmp)
        rows = history.load(path=self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seller"], "First")

    def test_update_patches_matching_row(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.update("ofek:1", {"action": "RESOLVED", "seller": "New"}, path=self.tmp)
        row = history.load(path=self.tmp)[0]
        self.assertEqual(row["action"], "RESOLVED")
        self.assertEqual(row["seller"], "New")
        self.assertEqual(row["product"], "P")  # untouched

    def test_update_missing_id_is_noop(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.update("ofek:999", {"action": "RESOLVED"}, path=self.tmp)
        self.assertEqual(history.load(path=self.tmp)[0]["action"], "DOWNLOADED")

    def test_page_returns_newest_first(self):
        for i in range(5):
            history.append(_rec(f"ofek:{i}"), path=self.tmp)
        page = history.page(offset=0, limit=2, path=self.tmp)
        self.assertEqual([r["id"] for r in page], ["ofek:4", "ofek:3"])
        page2 = history.page(offset=2, limit=2, path=self.tmp)
        self.assertEqual([r["id"] for r in page2], ["ofek:2", "ofek:1"])

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(history.load(path=self.tmp), [])

    def test_write_is_atomic_valid_json(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        json.loads(self.tmp.read_text(encoding="utf-8"))  # must not raise
        self.assertFalse(self.tmp.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'`

- [ ] **Step 3: Write the implementation**

Create `history.py`:

```python
"""
history.py
----------
Append-only store of every handled mail, backing the "History" view and the
per-run summary. One JSON array on disk. All writes are atomic (temp file +
os.replace) and serialized by a module-level lock so the scan thread and the
UI thread can both call in.
"""

import json
import os
import threading
from pathlib import Path

HISTORY_FILE = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver\history.json")

_LOCK = threading.Lock()


def load(path: Path = None) -> list:
    path = path or HISTORY_FILE
    if not Path(path).exists():
        return []
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(rows: list, path: Path):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append(record: dict, path: Path = None) -> None:
    path = path or HISTORY_FILE
    with _LOCK:
        rows = load(path)
        if any(r.get("id") == record.get("id") for r in rows):
            return
        rows.append(record)
        _write(rows, path)


def update(record_id: str, patch: dict, path: Path = None) -> None:
    path = path or HISTORY_FILE
    with _LOCK:
        rows = load(path)
        changed = False
        for r in rows:
            if r.get("id") == record_id:
                r.update(patch)
                changed = True
        if changed:
            _write(rows, path)


def page(offset: int = 0, limit: int = 50, path: Path = None) -> list:
    rows = load(path or HISTORY_FILE)
    rows = list(reversed(rows))
    return rows[offset:offset + limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_history.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add history.py test_history.py
git commit -m "Add history.py append-only handled-mail store"
```

---

## Task 2: `receipt_saver.py` — emit structured records

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\receipt_saver.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_receipt_saver.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_receipt_saver.py` (keep existing imports; add these):

```python
import receipt_saver
import history as history_mod


class TestProcessMessageRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Redirect every filesystem side effect into the temp dir.
        self._orig = {}
        for name, val in {
            "RECEIPTS_DIR": self.tmp / "קבלות",
            "MANUAL_DIR": self.tmp / "קבלות" / "_לטיפול ידני",
            "JAPANOLOGIA_DIR": self.tmp / "jp",
            "FALLBACK_LOG_FILE": self.tmp / "fallback_log.json",
        }.items():
            self._orig[name] = getattr(receipt_saver, name)
            setattr(receipt_saver, name, val)
        receipt_saver.RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        receipt_saver.MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        # Neutralise external effects.
        self._pdf = receipt_saver.save_email_pdf
        self._tt = receipt_saver.create_ticktick_task
        receipt_saver.save_email_pdf = lambda *a, **k: None
        receipt_saver.create_ticktick_task = lambda *a, **k: None
        self._hist = history_mod.HISTORY_FILE
        history_mod.HISTORY_FILE = self.tmp / "history.json"

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(receipt_saver, name, val)
        receipt_saver.save_email_pdf = self._pdf
        receipt_saver.create_ticktick_task = self._tt
        history_mod.HISTORY_FILE = self._hist

    def _msg(self, **over):
        m = {
            "id": "abc123", "sender": "noreply@electra-power.co.il",
            "subject": "חשבונית חשמל 555", "date_raw": "Thu, 9 Jul 2026 14:47:00 +0300",
            "is_sent": False, "body_text": "", "body_html": "<p>x</p>",
            "first_attachment_name": "", "attachments": lambda: [],
            "link": "http://mail/abc123",
        }
        m.update(over)
        return m

    def _account(self):
        return {"label": "ofek", "email": "ofek.shmuel1@gmail.com"}

    def test_hardcoded_match_returns_full_record(self):
        # electra-power is a custom rule, not hardcoded; use cellcominv (hardcoded).
        res = receipt_saver.process_message(
            self._msg(sender="billing@cellcominv.co.il", subject="חשבונית חודשית"),
            self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "saved")
        rec = res["record"]
        self.assertEqual(rec["id"], "ofek:abc123")
        self.assertEqual(rec["run_id"], "RID")
        self.assertEqual(rec["action"], "DOWNLOADED")
        self.assertEqual(rec["seller"], "סלקום")
        self.assertEqual(rec["category"], "חשבנות/אינטרנט")
        self.assertEqual(rec["rule_source"], "hardcoded")
        self.assertEqual(rec["account"], "ofek")

    def test_fallback_returns_record_and_logs_history(self):
        res = receipt_saver.process_message(
            self._msg(sender="who@unknown-xyz.com", subject="mystery"),
            self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "fallback")
        rec = res["record"]
        self.assertEqual(rec["action"], "FALLBACK")
        self.assertIsNone(rec["seller"])
        self.assertEqual(rec["rule_source"], None)

    def test_sent_mail_returns_skipped_no_record(self):
        res = receipt_saver.process_message(
            self._msg(is_sent=True), self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "skipped")
        self.assertNotIn("record", res)


class TestMainProgressCallback(unittest.TestCase):
    def test_main_accepts_progress_cb_and_returns_summary(self):
        # No accounts reachable in test env → main must still return a summary dict
        # without raising, and never call the callback with a malformed event.
        events = []
        summary = receipt_saver.main(run_id="RID", progress_cb=events.append)
        self.assertIn("run_id", summary)
        self.assertEqual(summary["run_id"], "RID")
        for e in events:
            self.assertIn("type", e)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_receipt_saver.py -q`
Expected: FAIL — `process_message()` takes 2 positional args / `main()` takes no args.

- [ ] **Step 3: Add the `_make_record` helper**

In `receipt_saver.py`, add `import history` near the other imports (after `import outlook_provider`). Then add this helper just above `def match_hardcoded`:

```python
def _make_record(msg, account, run_id, action, folder, folder_name, files,
                 seller=None, product=None, category=None, rule_source=None):
    return {
        "id":            f'{account["label"]}:{msg["id"]}',
        "run_id":        run_id,
        "handled_at":    datetime.datetime.now().isoformat(timespec="seconds"),
        "account":       account["label"],
        "account_email": account.get("email", ""),
        "date":          parse_date(msg["date_raw"]),
        "sender":        msg["sender"],
        "subject":       msg["subject"],
        "action":        action,
        "seller":        seller,
        "product":       product,
        "category":      category,
        "folder_name":   folder_name,
        "folder_path":   str(folder) if folder else None,
        "files":         [f.name for f in files] if files else [],
        "rule_source":   rule_source,
    }
```

- [ ] **Step 4: Rewrite `process_message` to return `{status, record}`**

Replace the whole `def process_message(msg: dict, account: dict) -> dict:` function body with this version (signature gains `run_id`):

```python
def process_message(msg: dict, account: dict, run_id: str = "") -> dict:
    label = account["label"]

    if msg["is_sent"]:
        return {"status": "skipped"}

    subject = msg["subject"]
    if "פרסומת" in subject:
        return {"status": "skipped"}
    sender     = msg["sender"]
    date_str   = parse_date(msg["date_raw"])
    first_att  = msg["first_attachment_name"]
    body_html  = msg["body_html"]

    # ── Japanese lesson summary ───────────────────────────────────────────
    if label == "ofek":
        lesson_folder = parse_lesson_folder(subject, date_str)
        if lesson_folder:
            dest = JAPANOLOGIA_DIR / lesson_folder
            dest.mkdir(parents=True, exist_ok=True)
            files = save_attachments(msg["attachments"], dest)
            _log_saved("JAPANOLOGIA", lesson_folder, sender, dest, files)
            rec = _make_record(msg, account, run_id, "JAPANOLOGIA", dest,
                               lesson_folder, files, seller="יפנולוגי",
                               product="סיכום שיעור", rule_source="japanologia")
            return {"status": "saved", "record": rec}

    # ── iCount special case ───────────────────────────────────────────────
    if is_icount(sender, subject):
        m = re.search(r"מאת\s+(.+?)$", subject)
        seller  = sanitize(m.group(1).strip()) if m else "iCount"
        product = "חשבונית מס קבלה"
        custom_match = match_custom(sender, subject)
        category = custom_match[2] if custom_match else None
        root     = custom_match[3] if custom_match and custom_match[3] else RECEIPTS_DIR
        base_dir = root / category if category else root
        folder_name = f"{date_str} - {seller} - {product} - {label}"
        folder, folder_name = unique_folder(base_dir, folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        create_icount_ticktick_task(folder_name, folder, label, msg["link"], subject)
        files = [pdf] if pdf else []
        _log_saved("ICOUNT", folder_name, sender, folder, files)
        rec = _make_record(msg, account, run_id, "ICOUNT", folder, folder_name,
                           files, seller=seller, product=product,
                           category=category, rule_source="icount")
        return {"status": "saved", "record": rec}

    # ── Step 1: hardcoded rules ──────────────────────────────────────────
    rule = match_hardcoded(sender, subject)
    if rule:
        seller, product_fn, category = rule
        product     = sanitize(product_fn(subject, first_att))
        base_dir    = RECEIPTS_DIR / category if category else RECEIPTS_DIR
        folder_name = f"{date_str} - {seller} - {product} - {label}"
        folder, folder_name = unique_folder(base_dir, folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        files = save_attachments(msg["attachments"], folder)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        if pdf:
            files.append(pdf)
        _log_saved("DOWNLOADED", folder_name, sender, folder, files)
        rec = _make_record(msg, account, run_id, "DOWNLOADED", folder, folder_name,
                           files, seller=seller, product=product,
                           category=category, rule_source="hardcoded")
        return {"status": "saved", "record": rec}

    # ── Step 2: custom rules ────────────────────────────────────────────
    body   = msg["body_text"]
    custom = match_custom(sender, subject, body)
    if custom:
        seller, product, category, rule_base_dir = custom
        if seller == "__exclude__":
            log.info(f"EXCLUDED   {sender} — {subject[:60]}")
            rec = _make_record(msg, account, run_id, "EXCLUDED", None, None, [],
                               rule_source="custom")
            return {"status": "excluded", "record": rec}
        root     = rule_base_dir if rule_base_dir else RECEIPTS_DIR
        base_dir = root / category if category else root
        folder_name = f"{date_str} - {sanitize(seller)} - {sanitize(product)} - {label}"
        folder, folder_name = unique_folder(base_dir, folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        files = save_attachments(msg["attachments"], folder)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        if pdf:
            files.append(pdf)
        _log_saved("DOWNLOADED", folder_name, sender, folder, files)
        rec = _make_record(msg, account, run_id, "DOWNLOADED", folder, folder_name,
                           files, seller=sanitize(seller), product=sanitize(product),
                           category=category, rule_source="custom")
        return {"status": "saved", "record": rec}

    # ── Step 3: fallback ────────────────────────────────────────────────
    sender_name   = extract_display_name(sender)
    subject_clean = sanitize(subject[:60])
    folder_name   = f"{date_str} - {sender_name} - {subject_clean} - {label}"
    folder, folder_name = unique_folder(MANUAL_DIR, folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    files = save_attachments(msg["attachments"], folder)
    pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
    if pdf:
        files.append(pdf)
    _log_saved("FALLBACK", folder_name, sender, folder, files)
    create_ticktick_task(folder_name, folder, label)
    append_fallback_log({
        "message_id":    msg["id"],
        "account":       label,
        "account_email": account["email"],
        "date":          date_str,
        "sender":        sender,
        "subject":       subject,
        "folder_name":   folder_name,
        "folder_path":   str(folder),
        "resolved":      False,
    })
    rec = _make_record(msg, account, run_id, "FALLBACK", folder, folder_name, files)
    return {"status": "fallback", "record": rec}
```

- [ ] **Step 5: Rewrite `main` to accept `run_id` / `progress_cb`**

Replace `def main():` through the end of the function with:

```python
def main(run_id: str = None, progress_cb=None):
    run_id = run_id or datetime.datetime.now().isoformat(timespec="seconds")

    def emit(evt):
        if progress_cb:
            try:
                progress_cb(evt)
            except Exception as e:
                log.warning(f"progress_cb failed: {e}")

    log.info("═" * 60)
    log.info(f"Receipt Saver started — {datetime.datetime.now():%Y-%m-%d %H:%M}")
    notify("Receipt Saver מופעל", "בודק תיבות דואר לקבלות חדשות...")

    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)

    processed      = load_processed()
    saved_folders  = []
    fallback_items = []
    excluded_count = 0
    records        = []

    for account in ACCOUNTS:
        label = account["label"]
        provider = PROVIDERS[account["provider"]]
        log.info(f"── Account: {account['email']} ({label})")

        if not account["creds_file"].exists():
            log.warning(f"  credentials file not found: {account['creds_file'].name} — skipping")
            notify("⚠️ Receipt Saver", f"credentials_{label}.json חסר — דילוג על חשבון {label}")
            emit({"type": "error", "label": label, "message": "credentials file missing"})
            continue

        try:
            service = provider.get_service(account)
        except Exception as e:
            log.error(f"  Auth failed for {label}: {e}")
            notify("⚠️ Receipt Saver", f"שגיאת כניסה לחשבון {label}")
            emit({"type": "error", "label": label, "message": f"auth failed: {e}"})
            continue

        candidate_ids = provider.list_candidate_ids(service, account, CUSTOM_RULES_FILE)
        log.info(f"  Candidates: {len(candidate_ids)}")
        emit({"type": "account", "label": label, "email": account["email"],
              "candidates": len(candidate_ids)})

        for mid in candidate_ids:
            scoped_id = f"{label}:{mid}"
            if scoped_id in processed:
                continue
            try:
                msg = provider.fetch_message(service, mid, account)
                result = process_message(msg, account, run_id=run_id)
                status = result.get("status")
                if status in ("saved", "fallback", "excluded"):
                    rec = result["record"]
                    history.append(rec)
                    records.append(rec)
                    emit({"type": "mail", "record": rec})
                if status == "saved":
                    saved_folders.append(result["record"]["folder_name"])
                elif status == "fallback":
                    fallback_items.append(result["record"])
                    notify(
                        "⚠️ קבלה לא זוהתה",
                        f"[{label}] מאת: {extract_display_name(result['record']['sender'])}\n"
                        f"{result['record']['subject'][:80]}",
                        timeout=10,
                    )
                elif status == "excluded":
                    excluded_count += 1
            except Exception as e:
                log.error(f"  Error on {mid}: {e}")
                emit({"type": "error", "label": label, "message": str(e)})
            finally:
                processed.add(scoped_id)

    save_processed(processed)

    if saved_folders:
        names = ", ".join(
            f.split(" - ")[1] if f.count(" - ") >= 1 else f
            for f in saved_folders
        )
        notify(f"📥 {len(saved_folders)} קבלות נשמרו", names[:200], timeout=8)
    elif not fallback_items:
        notify("Receipt Saver", "לא נמצאו קבלות חדשות.", timeout=4)

    log.info(f"Done — {len(saved_folders)} saved, {len(fallback_items)} fallback.\n")

    summary = {
        "run_id":   run_id,
        "saved":    len(saved_folders),
        "fallback": len(fallback_items),
        "excluded": excluded_count,
        "records":  records,
    }
    emit({"type": "done", **{k: summary[k] for k in ("run_id", "saved", "fallback", "excluded")}})
    return summary
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest test_receipt_saver.py -q`
Expected: PASS (existing tests + the 4 new tests). If `TestMainProgressCallback` hangs on OAuth, it means an account has a valid token in the working dir — set env `RECEIPT_SAVER_SKIP_ACCOUNTS=1` support is **out of scope**; instead run that one test with the four `credentials_*.json` temporarily unreadable is not acceptable either. Accept the risk: the test asserts only the return contract and is expected to pass quickly because `list_candidate_ids` returns fast for already-processed inboxes. If it does hang in this environment, mark it `@unittest.skip("needs offline provider stub")` and note it in the commit message.

- [ ] **Step 7: Verify standalone CLI still works**

Run: `python -c "import receipt_saver; print(receipt_saver.main.__doc__ or 'ok')"`
Expected: prints `ok` (import side-effect free, no crash).

- [ ] **Step 8: Commit**

```bash
git add receipt_saver.py test_receipt_saver.py
git commit -m "receipt_saver: emit structured records and progress events"
```

---

## Task 3: `fallback_ops.py` — heuristic `suggest()`

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\fallback_ops.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_fallback_ops.py`

- [ ] **Step 1: Write the failing test**

Create `test_fallback_ops.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

import fallback_ops


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.rules = Path(tempfile.mkdtemp()) / "custom_rules.json"
        self.rules.write_text(json.dumps([
            {"match_sender_contains": "electra-power.co.il", "seller": "אלקטרה פאוור",
             "product": "חשבונית חשמל", "category": "חשבנות/חשמל"},
        ], ensure_ascii=False), encoding="utf-8")

    def s(self, sender, subject):
        return fallback_ops.suggest(
            {"sender": sender, "subject": subject}, rules_path=self.rules)

    def test_known_domain_reuses_rule_seller_high_confidence(self):
        out = self.s("Heshbon@electra-power.co.il", "חשבונית חשמל 555")
        self.assertEqual(out["seller"], "אלקטרה פאוור")
        self.assertEqual(out["category"], "חשבנות/חשמל")
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["match_sender_contains"], "electra-power.co.il")

    def test_unknown_domain_derives_seller_low_confidence(self):
        out = self.s("noreply@some-shop.co.il", "invoice #55")
        self.assertEqual(out["seller"], "Some-Shop")
        self.assertEqual(out["confidence"], "low")
        self.assertEqual(out["match_sender_contains"], "some-shop.co.il")

    def test_product_keyword_mapping(self):
        self.assertEqual(self.s("x@y.com", "חשבונית מס קבלה 12")["product"], "חשבונית מס קבלה")
        self.assertEqual(self.s("x@y.com", "אישור תשלום ביט")["product"], "אישור תשלום")
        self.assertEqual(self.s("x@y.com", "הזמנה 9")["product"], "הזמנה")
        self.assertEqual(self.s("x@y.com", "no keywords here")["product"], "חשבונית")

    def test_promotional_subject_suggests_exclude(self):
        out = self.s("news@shop.com", "מבצע פרסומת ענק")
        self.assertEqual(out["kind"], "exclude")

    def test_category_from_subject(self):
        self.assertEqual(self.s("x@y.com", "חשבון מים רבעוני")["category"], "חשבנות/מיים")
        self.assertEqual(self.s("x@y.com", "ארנונה 2026")["category"], "חשבנות/ארנונה")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_fallback_ops.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fallback_ops'`

- [ ] **Step 3: Write `suggest()` (and module skeleton)**

Create `fallback_ops.py`:

```python
"""
fallback_ops.py
---------------
Heuristic classification of unresolved fallback emails, plus the operations
that apply a user's decision: append a custom rule, move the folder out of
`_לטיפול ידני`, mark `fallback_log.json` resolved, and patch the history row.

`suggest()` uses ONLY the sender and subject — no email body, no network, no
AI. It is a starting point for the form the user edits.
"""

import json
import os
import re
import shutil
from pathlib import Path

import receipt_saver
import history

SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
CUSTOM_RULES_FILE = SCRIPT_DIR / "custom_rules.json"
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"
CLEANUP_LOG_FILE  = SCRIPT_DIR / "cleanup_log.json"
RECEIPTS_DIR      = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR        = RECEIPTS_DIR / "_לטיפול ידני"

CATEGORIES = ["חשבנות/חשמל", "חשבנות/מיים", "חשבנות/ארנונה",
              "חשבנות/אינטרנט", "חשבנות/גז"]

_PRODUCT_KEYWORDS = [
    ("חשבונית מס קבלה", "חשבונית מס קבלה"),
    ("חשבונית", "חשבונית"),
    ("קבלת", "קבלה"),
    ("קבלה", "קבלה"),
    ("הזמנה", "הזמנה"),
    ("תשלום", "אישור תשלום"),
    ("כרטיס", "כרטיסים"),
    ("מנוי", "מנוי"),
]
_CATEGORY_KEYWORDS = [
    (("חשמל",), "חשבנות/חשמל"),
    (("מים", "מיים"), "חשבנות/מיים"),
    (("ארנונה",), "חשבנות/ארנונה"),
    (("אינטרנט",), "חשבנות/אינטרנט"),
    (("גז",), "חשבנות/גז"),
]
_EXCLUDE_KEYWORDS = ("פרסומת", "הטבה", "דיוור", "newsletter", "מבצע")
_TLD_STRIP = re.compile(r"^(www\.)|(\.co\.il|\.org\.il|\.com|\.net|\.co)$")


def _registered_domain(sender: str) -> str:
    m = re.search(r"[\w.+-]+@([\w.-]+)", sender or "")
    host = (m.group(1) if m else sender or "").lower().strip()
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "org", "gov", "muni", "ac"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _seller_from_domain(domain: str) -> str:
    core = domain
    for suffix in (".co.il", ".org.il", ".com", ".net", ".co"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    core = core.split(".")[0].replace("-", "-")
    return "-".join(w.capitalize() for w in core.split("-")) or domain


def _load_rules(rules_path: Path) -> list:
    try:
        return json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception:
        return []


def suggest(entry: dict, rules_path: Path = None) -> dict:
    rules_path = rules_path or CUSTOM_RULES_FILE
    sender  = entry.get("sender", "")
    subject = entry.get("subject", "")
    domain  = _registered_domain(sender)

    seller, confidence, category = None, "low", None
    for rule in _load_rules(rules_path):
        frag = (rule.get("match_sender_contains") or "").lower()
        if frag and frag in sender.lower():
            seller     = rule.get("seller") or seller
            category   = rule.get("category") or category
            confidence = "high"
            break
    if seller is None:
        seller = _seller_from_domain(domain)

    product = "חשבונית"
    for needle, value in _PRODUCT_KEYWORDS:
        if needle in subject:
            product = value
            break

    if category is None:
        for needles, value in _CATEGORY_KEYWORDS:
            if any(n in subject for n in needles):
                category = value
                break

    kind = "rule"
    if any(k.lower() in subject.lower() for k in _EXCLUDE_KEYWORDS):
        kind = "exclude"

    if confidence == "low" and (product != "חשבונית" or category):
        confidence = "medium"

    return {
        "seller": seller,
        "product": product,
        "category": category,
        "match_sender_contains": domain,
        "kind": kind,
        "confidence": confidence,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_fallback_ops.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add fallback_ops.py test_fallback_ops.py
git commit -m "fallback_ops: heuristic suggest() for unresolved fallbacks"
```

---

## Task 4: `fallback_ops.py` — `compute_destination` + `apply_decision`

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\fallback_ops.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_fallback_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_fallback_ops.py`:

```python
class TestApplyDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.receipts = self.tmp / "קבלות"
        self.manual = self.receipts / "_לטיפול ידני"
        self.manual.mkdir(parents=True)
        self.rules = self.tmp / "custom_rules.json"
        self.rules.write_text("[]", encoding="utf-8")
        self.flog = self.tmp / "fallback_log.json"
        self.clog = self.tmp / "cleanup_log.json"
        self.hist = self.tmp / "history.json"
        # one fallback folder with a file in it
        self.src = self.manual / "2026_08_25 - who - mystery - ofek"
        self.src.mkdir()
        (self.src / "email.pdf").write_text("x", encoding="utf-8")
        self.flog.write_text(json.dumps([{
            "message_id": "m1", "account": "ofek", "account_email": "o@x.com",
            "date": "2026_08_25", "sender": "who@shop.co.il", "subject": "mystery",
            "folder_name": self.src.name, "folder_path": str(self.src), "resolved": False,
        }], ensure_ascii=False), encoding="utf-8")
        self.hist.write_text(json.dumps([{
            "id": "ofek:m1", "action": "FALLBACK", "seller": None, "product": None,
            "category": None, "folder_name": self.src.name, "folder_path": str(self.src),
        }], ensure_ascii=False), encoding="utf-8")
        self.paths = dict(rules_path=self.rules, fallback_log_path=self.flog,
                          cleanup_log_path=self.clog, history_path=self.hist,
                          receipts_dir=self.receipts, manual_dir=self.manual)

    def _entry(self):
        return json.loads(self.flog.read_text(encoding="utf-8"))[0]

    def test_compute_destination_with_category(self):
        dst = fallback_ops.compute_destination(
            self._entry(), {"seller": "S", "product": "P", "category": "חשבנות/חשמל",
                            "base_dir": None}, receipts_dir=self.receipts)
        self.assertEqual(dst, self.receipts / "חשבנות" / "חשמל" /
                         "2026_08_25 - S - P - ofek")

    def test_rule_decision_writes_rule_moves_folder_resolves(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "rule", "seller": "שופ", "product": "חשבונית",
            "category": None, "base_dir": None,
            "match_sender_contains": "shop.co.il", "match_subject_contains": None,
        }, **self.paths)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertEqual(rules[-1]["match_sender_contains"], "shop.co.il")
        self.assertEqual(rules[-1]["seller"], "שופ")
        self.assertFalse(self.src.exists())
        dst = self.receipts / "2026_08_25 - שופ - חשבונית - ofek"
        self.assertTrue((dst / "email.pdf").exists())
        self.assertTrue(self._entry()["resolved"])
        row = json.loads(self.hist.read_text(encoding="utf-8"))[0]
        self.assertEqual(row["action"], "RESOLVED")
        self.assertEqual(row["seller"], "שופ")
        self.assertEqual(row["resolution"], "rule")

    def test_once_decision_moves_without_writing_rule(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "once", "seller": "שופ", "product": "חשבונית",
            "category": None, "base_dir": None,
            "match_sender_contains": "shop.co.il", "match_subject_contains": None,
        }, **self.paths)
        self.assertEqual(json.loads(self.rules.read_text(encoding="utf-8")), [])
        self.assertTrue((self.receipts / "2026_08_25 - שופ - חשבונית - ofek" / "email.pdf").exists())
        self.assertEqual(self._entry()["resolved"], True)

    def test_exclude_decision_writes_exclude_rule_deletes_folder_logs_cleanup(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "exclude", "seller": None, "product": None, "category": None,
            "base_dir": None, "match_sender_contains": "shop.co.il",
            "match_subject_contains": None,
        }, **self.paths)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertTrue(rules[-1]["exclude"])
        self.assertFalse(self.src.exists())
        cleanup = json.loads(self.clog.read_text(encoding="utf-8"))
        self.assertEqual(cleanup[-1]["action"], "DELETED")
        self.assertTrue(self._entry()["resolved"])

    def test_skip_decision_is_noop(self):
        fallback_ops.apply_decision(self._entry(), {"kind": "skip"}, **self.paths)
        self.assertTrue(self.src.exists())
        self.assertFalse(self._entry()["resolved"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_fallback_ops.py -q`
Expected: FAIL — `module 'fallback_ops' has no attribute 'compute_destination'`

- [ ] **Step 3: Implement the operations**

Append to `fallback_ops.py`:

```python
def _atomic_write_json(path: Path, data) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def compute_destination(entry: dict, decision: dict, receipts_dir: Path = None) -> Path:
    receipts_dir = Path(receipts_dir or RECEIPTS_DIR)
    root = Path(decision["base_dir"]) if decision.get("base_dir") else receipts_dir
    category = decision.get("category")
    base = root / category if category else root
    seller  = receipt_saver.sanitize(decision["seller"])
    product = receipt_saver.sanitize(decision["product"])
    name = f'{entry["date"]} - {seller} - {product} - {entry["account"]}'
    folder, _ = receipt_saver.unique_folder(base, name)
    return folder


def _move_folder(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.mkdir(exist_ok=True)
    for item in Path(src).iterdir():
        shutil.move(str(item), str(dst / item.name))
    try:
        Path(src).rmdir()
    except OSError:
        pass
    return dst


def _append_json_list(path: Path, item: dict) -> None:
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        rows = []
    rows.append(item)
    _atomic_write_json(path, rows)


def _mark_resolved(fallback_log_path: Path, message_id: str, new_path: str) -> None:
    rows = json.loads(Path(fallback_log_path).read_text(encoding="utf-8"))
    for r in rows:
        if r.get("message_id") == message_id:
            r["resolved"] = True
            if new_path:
                r["folder_path"] = new_path
    _atomic_write_json(fallback_log_path, rows)


def apply_decision(entry: dict, decision: dict, *,
                   rules_path: Path = None, fallback_log_path: Path = None,
                   cleanup_log_path: Path = None, history_path: Path = None,
                   receipts_dir: Path = None, manual_dir: Path = None) -> dict:
    rules_path        = rules_path or CUSTOM_RULES_FILE
    fallback_log_path = fallback_log_path or FALLBACK_LOG_FILE
    cleanup_log_path  = cleanup_log_path or CLEANUP_LOG_FILE
    receipts_dir      = Path(receipts_dir or RECEIPTS_DIR)
    src = Path(entry["folder_path"])
    rec_id = f'{entry["account"]}:{entry["message_id"]}'
    kind = decision.get("kind")

    if kind == "skip":
        return {"ok": True, "kind": "skip"}

    if kind == "exclude":
        rule = {"_comment": f'auto-added from fallback {entry["message_id"]}',
                "match_sender_contains": decision["match_sender_contains"],
                "match_subject_contains": decision.get("match_subject_contains"),
                "exclude": True}
        _append_json_list(rules_path, rule)
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        import datetime as _dt
        _append_json_list(cleanup_log_path, {
            "action": "DELETED", "folder": entry["folder_name"],
            "reason": f'excluded via fallback UI ({decision["match_sender_contains"]})',
            "timestamp": _dt.datetime.now().isoformat()})
        _mark_resolved(fallback_log_path, entry["message_id"], "")
        history.update(rec_id, {"action": "RESOLVED", "resolution": "exclude"},
                       path=history_path)
        return {"ok": True, "kind": "exclude"}

    # kind in ("rule", "once")
    dst = compute_destination(entry, decision, receipts_dir)
    if kind == "rule":
        rule = {"_comment": f'auto-added from fallback {entry["message_id"]}',
                "match_sender_contains": decision["match_sender_contains"],
                "match_subject_contains": decision.get("match_subject_contains"),
                "seller": decision["seller"], "product": decision["product"],
                "category": decision.get("category")}
        if decision.get("base_dir"):
            rule["base_dir"] = decision["base_dir"]
        _append_json_list(rules_path, rule)
    moved = _move_folder(src, dst) if src.exists() else dst
    _mark_resolved(fallback_log_path, entry["message_id"], str(moved))
    history.update(rec_id, {
        "action": "RESOLVED", "resolution": kind,
        "seller": decision["seller"], "product": decision["product"],
        "category": decision.get("category"),
        "folder_name": moved.name, "folder_path": str(moved),
    }, path=history_path)
    return {"ok": True, "kind": kind, "dest": str(moved)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_fallback_ops.py -q`
Expected: PASS (10 passed total in the file)

- [ ] **Step 5: Sanity-check the rule round-trips through the engine**

Run:
```bash
python -c "import json,fallback_ops,receipt_saver,tempfile,pathlib; \
p=pathlib.Path(tempfile.mkdtemp())/'r.json'; p.write_text('[]'); \
fallback_ops._append_json_list(p, {'match_sender_contains':'foo.co.il','match_subject_contains':None,'seller':'Foo','product':'חשבונית','category':None}); \
import unittest.mock as m; \
print('rule written ok')"
```
Expected: prints `rule written ok`

- [ ] **Step 6: Commit**

```bash
git add fallback_ops.py test_fallback_ops.py
git commit -m "fallback_ops: apply_decision (rule/once/exclude/skip) + destination"
```

---

## Task 5: `claude_handoff.py` — pre-seeded terminal

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\claude_handoff.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_claude_handoff.py`

- [ ] **Step 1: Write the failing test**

Create `test_claude_handoff.py`:

```python
import unittest
from unittest import mock

import claude_handoff


ENTRIES = [
    {"account": "ofek", "sender": '"מירי" <o@icount.co.il>',
     "subject": "חשבונית 7721", "folder_path": "C:\\x\\a"},
    {"account": "family", "sender": "billing@z.com",
     "subject": 'say "hi"', "folder_path": "C:\\x\\b"},
]


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_is_single_line_and_mentions_each_entry(self):
        p = claude_handoff.build_prompt(ENTRIES)
        self.assertNotIn("\n", p)
        self.assertIn("handle my fallback emails", p)
        self.assertIn("[ofek]", p)
        self.assertIn("[family]", p)
        self.assertIn("חשבונית 7721", p)

    def test_prompt_has_no_double_quotes(self):
        # double quotes would break the  cmd /k claude "..."  invocation
        self.assertNotIn('"', claude_handoff.build_prompt(ENTRIES))


class TestLaunch(unittest.TestCase):
    def test_launch_spawns_terminal_with_prompt(self):
        with mock.patch("claude_handoff.subprocess.Popen") as popen:
            claude_handoff.launch(ENTRIES)
            self.assertTrue(popen.called)
            args, kwargs = popen.call_args
            joined = " ".join(args[0]) if isinstance(args[0], (list, tuple)) else str(args[0])
            self.assertIn("claude", joined)
            self.assertIn("handle my fallback emails", joined)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_claude_handoff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_handoff'`

- [ ] **Step 3: Write the implementation**

Create `claude_handoff.py`:

```python
"""
claude_handoff.py
-----------------
Open a new terminal running the `claude` CLI, pre-seeded with a prompt that
points at specific unresolved fallback entries, so the user can finish
classifying them together with Claude.
"""

import subprocess
from pathlib import Path

SCRIPT_DIR = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")


def build_prompt(entries: list) -> str:
    bits = []
    for e in entries:
        sender  = str(e.get("sender", "")).replace('"', "'")
        subject = str(e.get("subject", "")).replace('"', "'")
        bits.append(f'[{e.get("account", "?")}] {sender} / {subject}'
                    f' (folder: {e.get("folder_path", "")})')
    listing = "; ".join(bits)
    return ("handle my fallback emails — focus on these unresolved entries "
            f"from fallback_log.json: {listing}").replace("\n", " ").replace('"', "'")


def launch(entries: list) -> None:
    prompt = build_prompt(entries)
    # `start` needs a title arg first; keep everything one line.
    subprocess.Popen(
        ["cmd", "/c", "start", "Claude - fallbacks", "cmd", "/k", "claude", prompt],
        cwd=str(SCRIPT_DIR),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_claude_handoff.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add claude_handoff.py test_claude_handoff.py
git commit -m "Add claude_handoff: pre-seeded claude terminal for fallbacks"
```

---

## Task 6: `app.py` — `Api` data methods (no window yet)

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\app.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_app_api.py`

- [ ] **Step 1: Write the failing test**

Create `test_app_api.py`:

```python
import json
import tempfile
import time
import unittest
from pathlib import Path

import app as appmod
import history as history_mod


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._hist = history_mod.HISTORY_FILE
        history_mod.HISTORY_FILE = self.tmp / "history.json"
        history_mod.HISTORY_FILE.write_text(json.dumps([
            {"id": f"ofek:{i}", "action": "DOWNLOADED", "seller": f"S{i}",
             "subject": f"sub {i}", "sender": "a@b.com"} for i in range(4)
        ], ensure_ascii=False), encoding="utf-8")
        self.flog = self.tmp / "fallback_log.json"
        self.flog.write_text(json.dumps([
            {"message_id": "m1", "account": "ofek", "sender": "x@y.co.il",
             "subject": "mystery", "date": "2026_08_25",
             "folder_name": "f", "folder_path": str(self.tmp / "f"), "resolved": False},
            {"message_id": "m2", "resolved": True},
        ], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        history_mod.HISTORY_FILE = self._hist

    def _api(self, scan_fn=None):
        return appmod.Api(
            scan_fn=scan_fn or (lambda run_id, progress_cb: {"run_id": run_id,
                                "saved": 0, "fallback": 0, "excluded": 0, "records": []}),
            fallback_log_path=self.flog,
        )

    def test_get_history_pages_newest_first(self):
        api = self._api()
        page = api.get_history(0, 2)
        self.assertEqual([r["id"] for r in page], ["ofek:3", "ofek:2"])

    def test_get_fallbacks_returns_only_unresolved(self):
        api = self._api()
        fbs = api.get_fallbacks()
        self.assertEqual([f["message_id"] for f in fbs], ["m1"])

    def test_suggest_fallback_returns_suggestion_fields(self):
        api = self._api()
        out = api.suggest_fallback("m1")
        self.assertIn("seller", out)
        self.assertIn("confidence", out)

    def test_start_scan_runs_fn_and_collects_events(self):
        def fake_scan(run_id, progress_cb):
            progress_cb({"type": "account", "label": "ofek", "candidates": 1})
            progress_cb({"type": "mail", "record": {"id": "ofek:9", "action": "DOWNLOADED"}})
            return {"run_id": run_id, "saved": 1, "fallback": 0, "excluded": 0, "records": []}
        api = self._api(scan_fn=fake_scan)
        api.start_scan()
        for _ in range(50):
            if not api.scan_running():
                break
            time.sleep(0.05)
        self.assertFalse(api.scan_running())
        run = api.get_run()
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["summary"]["saved"], 1)
        types = [e["type"] for e in run["events"]]
        self.assertEqual(types, ["account", "mail", "done"])

    def test_start_scan_is_single_flight(self):
        def slow_scan(run_id, progress_cb):
            time.sleep(0.3)
            return {"run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []}
        api = self._api(scan_fn=slow_scan)
        api.start_scan()
        second = api.start_scan()
        self.assertEqual(second["status"], "busy")
        for _ in range(50):
            if not api.scan_running():
                break
            time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_app_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write `app.py` with the `Api` class**

Create `app.py`:

```python
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
import subprocess
import threading
import datetime
from pathlib import Path

import receipt_saver
import history
import fallback_ops
import claude_handoff

SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
UI_DIR            = SCRIPT_DIR / "ui"
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"


class Api:
    def __init__(self, scan_fn=None, fallback_log_path: Path = None):
        self._scan_fn = scan_fn or receipt_saver.main
        self._fallback_log_path = Path(fallback_log_path or FALLBACK_LOG_FILE)
        self._window = None
        self._lock = threading.Lock()
        self._thread = None
        self._run = {"status": "idle", "events": [], "summary": None}

    # -- wiring ---------------------------------------------------------------
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

    # -- scan --------------------------------------------------------------
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
        try:
            summary = self._scan_fn(run_id=run_id, progress_cb=self._push)
            self._run["summary"] = summary
            self._run["status"] = "done"
        except Exception as e:
            self._run["status"] = "error"
            self._push({"type": "error", "label": "-", "message": str(e)})

    def get_run(self) -> dict:
        return self._run

    # -- history ---------------------------------------------------------------
    def get_history(self, offset: int = 0, limit: int = 50) -> list:
        return history.page(int(offset), int(limit))

    # -- fallbacks -------------------------------------------------------------
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

    # -- misc ------------------------------------------------------------------
    def open_folder(self, path: str) -> dict:
        try:
            os.startfile(path)  # noqa: S606  (Windows only, user-chosen path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def categories(self) -> list:
        return fallback_ops.CATEGORIES

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

    api = Api()
    window = webview.create_window(
        "Receipt Saver",
        url=str(UI_DIR / "index.html"),
        js_api=api,
        width=980, height=680,
        frameless=True, easy_drag=True,
        background_color="#0f1115",
    )
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_app_api.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS (all files green)

- [ ] **Step 6: Commit**

```bash
git add app.py test_app_api.py
git commit -m "Add app.py Api: scan orchestration + history/fallback accessors"
```

---

## Task 7: `tray.py` — resident tray icon

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\tray.py`

- [ ] **Step 1: Write the implementation**

Create `tray.py`:

```python
"""
tray.py
-------
System-tray icon that keeps the process alive so the window can be reopened.
Menu: Open, Run scan now, Quit. Started on a daemon thread by app.py.
"""

import os
import threading

from PIL import Image, ImageDraw
import pystray


def _icon_image():
    img = Image.new("RGB", (64, 64), "#0f1115")
    d = ImageDraw.Draw(img)
    d.rectangle((14, 12, 50, 52), outline="#4f8cff", width=4)
    d.line((14, 24, 50, 24), fill="#4f8cff", width=3)
    return img


def run(api):
    def _open(icon, item):
        try:
            if api._window:
                api._window.show()
                api._window.restore()
        except Exception:
            pass

    def _scan(icon, item):
        threading.Thread(target=api.start_scan, daemon=True).start()

    def _quit(icon, item):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        "receipt_saver",
        _icon_image(),
        "Receipt Saver",
        menu=pystray.Menu(
            pystray.MenuItem("Open", _open, default=True),
            pystray.MenuItem("Run scan now", _scan),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    icon.run()
```

- [ ] **Step 2: Smoke-test the icon renders (manual)**

Run: `python -c "import tray; tray._icon_image().save('nul') if False else print('tray import ok')"`
Expected: prints `tray import ok` (no import error once `pystray`/`Pillow` are installed — see Task 10).

- [ ] **Step 3: Commit**

```bash
git add tray.py
git commit -m "Add tray.py resident tray icon (Open / Run scan / Quit)"
```

---

## Task 8: Frontend — `ui/index.html` + `ui/app.css`

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\index.html`
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\app.css`

- [ ] **Step 1: Create `ui/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Receipt Saver</title>
  <link rel="stylesheet" href="app.css">
</head>
<body>
  <header class="titlebar">
    <div class="brand">Receipt&nbsp;Saver</div>
    <nav class="tabs">
      <button data-view="run" class="tab active">This run</button>
      <button data-view="history" class="tab">History</button>
      <button data-view="fallbacks" class="tab">Fallbacks <span id="fb-badge" class="badge" hidden>0</span></button>
    </nav>
    <div class="win-controls">
      <button id="btn-rescan" title="Run scan now">⟳</button>
      <button id="btn-min" title="Minimize">–</button>
      <button id="btn-close" title="Close to tray">×</button>
    </div>
  </header>

  <main>
    <section id="view-run" class="view active">
      <div id="run-summary" class="summary"></div>
      <div id="run-list" class="cards"></div>
      <div id="run-empty" class="empty" hidden>Nothing new this run.</div>
    </section>

    <section id="view-history" class="view">
      <div class="filters">
        <input id="hist-search" type="search" placeholder="Search sender, subject, seller…" dir="auto">
      </div>
      <div id="hist-list" class="cards"></div>
      <div id="hist-sentinel"></div>
    </section>

    <section id="view-fallbacks" class="view">
      <div class="fb-toolbar">
        <button id="fb-handoff" disabled>Handle selected with Claude →</button>
      </div>
      <div id="fb-list" class="cards"></div>
      <div id="fb-empty" class="empty" hidden>No unresolved fallbacks. 🎉</div>
    </section>
  </main>

  <div id="toast-host"></div>

  <template id="tpl-card">
    <article class="card">
      <span class="pill"></span>
      <div class="card-main">
        <div class="card-title" dir="auto"></div>
        <div class="card-sub" dir="auto"></div>
      </div>
      <div class="card-meta">
        <span class="chip account"></span>
        <span class="date"></span>
        <a class="open-folder" href="#">Open folder</a>
      </div>
    </article>
  </template>

  <template id="tpl-fallback">
    <article class="card fb">
      <input type="checkbox" class="fb-check">
      <div class="card-main">
        <div class="card-title" dir="auto"></div>
        <div class="card-sub" dir="auto"></div>
        <div class="fb-links">
          <a class="open-folder" href="#">Open folder</a>
          <a class="open-pdf" href="#">Open email.pdf</a>
          <span class="conf"></span>
        </div>
        <form class="fb-form">
          <label><input type="radio" name="kind" value="rule" checked> Make a rule</label>
          <label><input type="radio" name="kind" value="once"> Move this one only</label>
          <label><input type="radio" name="kind" value="exclude"> Exclude as promotional</label>
          <label><input type="radio" name="kind" value="skip"> Skip for now</label>
          <div class="fb-fields">
            <input class="f-seller" placeholder="Seller" dir="auto">
            <input class="f-product" placeholder="Product" dir="auto">
            <select class="f-category"></select>
            <input class="f-basedir" placeholder="Destination root (optional)" dir="auto">
            <input class="f-sender" placeholder="match sender contains" dir="auto">
            <input class="f-subject" placeholder="match subject contains (optional)" dir="auto">
          </div>
          <button type="submit" class="fb-apply">Apply</button>
        </form>
      </div>
    </article>
  </template>

  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `ui/app.css`**

```css
:root {
  --bg: #0f1115; --panel: #171a21; --panel-2: #1e222b; --line: #2a2f3a;
  --text: #e7e9ee; --muted: #9aa3b2; --accent: #4f8cff;
  --ok: #3fb950; --warn: #d29922; --bad: #f85149; --info: #6e7681;
  font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text); }
body { display: flex; flex-direction: column; font-size: 14px; }

.titlebar {
  display: flex; align-items: center; gap: 16px;
  padding: 10px 14px; background: var(--panel); border-bottom: 1px solid var(--line);
  -webkit-user-select: none; user-select: none;
}
.brand { font-weight: 600; letter-spacing: .3px; }
.tabs { display: flex; gap: 4px; }
.tab {
  background: transparent; color: var(--muted); border: 0; padding: 6px 12px;
  border-radius: 8px; cursor: pointer; font-size: 13px;
}
.tab.active { background: var(--panel-2); color: var(--text); }
.badge {
  display: inline-block; min-width: 18px; text-align: center; font-size: 11px;
  background: var(--warn); color: #1a1a1a; border-radius: 9px; padding: 0 5px;
}
.win-controls { margin-inline-start: auto; display: flex; gap: 4px; }
.win-controls button {
  width: 28px; height: 28px; border: 0; border-radius: 6px; cursor: pointer;
  background: var(--panel-2); color: var(--muted); font-size: 15px;
}
.win-controls button:hover { color: var(--text); }

main { flex: 1; overflow: hidden; position: relative; }
.view { position: absolute; inset: 0; overflow-y: auto; padding: 16px; display: none; }
.view.active { display: block; }

.summary { color: var(--muted); margin-bottom: 12px; min-height: 18px; }
.cards { display: flex; flex-direction: column; gap: 8px; }
.card {
  display: flex; align-items: center; gap: 12px; padding: 12px 14px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
}
.card-main { flex: 1; min-width: 0; }
.card-title { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card-sub { color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card-meta { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); }
.chip { background: var(--panel-2); border-radius: 6px; padding: 2px 7px; }
.open-folder, .open-pdf { color: var(--accent); text-decoration: none; }
.open-folder:hover, .open-pdf:hover { text-decoration: underline; }

.pill {
  font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 999px;
  background: var(--info); color: #fff; text-transform: lowercase;
}
.pill.downloaded, .pill.resolved { background: var(--ok); }
.pill.icount, .pill.japanologia { background: var(--accent); }
.pill.fallback { background: var(--warn); color: #1a1a1a; }
.pill.excluded { background: var(--info); }

.filters { margin-bottom: 12px; }
#hist-search {
  width: 100%; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--panel); color: var(--text);
}

.empty { color: var(--muted); text-align: center; margin-top: 40px; }

.fb-toolbar { margin-bottom: 12px; }
#fb-handoff, .fb-apply {
  background: var(--accent); color: #fff; border: 0; padding: 8px 14px;
  border-radius: 8px; cursor: pointer;
}
#fb-handoff:disabled { background: var(--panel-2); color: var(--muted); cursor: default; }
.card.fb { align-items: flex-start; flex-direction: row; }
.fb-form { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.fb-form label { color: var(--muted); font-size: 13px; }
.fb-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 6px 0; }
.fb-fields input, .fb-fields select {
  padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--panel-2); color: var(--text);
}
.fb-links { display: flex; gap: 12px; font-size: 12px; margin-top: 4px; }
.conf.low { color: var(--warn); }
.conf.medium { color: var(--muted); }
.conf.high { color: var(--ok); }

#toast-host {
  position: fixed; right: 14px; bottom: 14px; display: flex; flex-direction: column;
  gap: 8px; z-index: 50;
}
.toast {
  background: var(--panel-2); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  padding: 10px 14px; border-radius: 8px; max-width: 320px;
}
.toast.error { border-left-color: var(--bad); }
```

- [ ] **Step 3: Commit**

```bash
git add ui/index.html ui/app.css
git commit -m "Add startup UI markup and stylesheet"
```

---

## Task 9: Frontend — `ui/app.js`

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\app.js`

- [ ] **Step 1: Create `ui/app.js`**

```js
"use strict";

const api = () => window.pywebview.api;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let CATEGORIES = [];
let histOffset = 0, histLoading = false, histDone = false, histRows = [];

// ---- view switching --------------------------------------------------------
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".view").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#view-" + t.dataset.view).classList.add("active");
  if (t.dataset.view === "history" && histRows.length === 0) loadHistory();
  if (t.dataset.view === "fallbacks") loadFallbacks();
}));

$("#btn-min").addEventListener("click", () => api().minimize());
$("#btn-close").addEventListener("click", () => api().hide());
$("#btn-rescan").addEventListener("click", async () => {
  const r = await api().start_scan();
  if (r.status === "busy") toast("A scan is already running.");
  else resetRunView();
});

// ---- card rendering -------------------------------------------------------
function card(rec) {
  const n = $("#tpl-card").content.cloneNode(true);
  const pill = $(".pill", n);
  pill.textContent = (rec.action || "").toLowerCase();
  pill.classList.add((rec.action || "info").toLowerCase());
  $(".card-title", n).textContent =
    rec.seller ? `${rec.seller} · ${rec.product || ""}` : (rec.subject || "(no subject)");
  $(".card-sub", n).textContent =
    rec.seller ? (rec.subject || "") : (rec.sender || "");
  $(".chip.account", n).textContent = rec.account || "";
  $(".date", n).textContent = (rec.date || "").replace(/_/g, "-");
  const link = $(".open-folder", n);
  if (rec.folder_path) link.addEventListener("click", e => {
    e.preventDefault(); api().open_folder(rec.folder_path);
  });
  else link.remove();
  return n;
}

// ---- this-run view -------------------------------------------------------
function resetRunView() {
  $("#run-list").innerHTML = "";
  $("#run-summary").textContent = "Scanning…";
  $("#run-empty").hidden = true;
}
window.onScanEvent = function (evt) {
  if (evt.type === "account") {
    $("#run-summary").textContent = `Scanning ${evt.label}… ${evt.candidates} candidates`;
  } else if (evt.type === "mail") {
    $("#run-list").appendChild(card(evt.record));
  } else if (evt.type === "error") {
    const d = document.createElement("div");
    d.className = "toast error";
    d.textContent = `${evt.label}: ${evt.message}`;
    $("#run-list").appendChild(d);
  } else if (evt.type === "done") {
    const parts = [`${evt.saved} saved`, `${evt.fallback} fallback`];
    if (evt.excluded) parts.push(`${evt.excluded} excluded`);
    $("#run-summary").textContent = parts.join(" · ");
    if (evt.saved === 0 && evt.fallback === 0 && evt.excluded === 0) {
      $("#run-empty").hidden = false;
    }
    refreshBadge();
  }
};

// ---- history view -------------------------------------------------------
async function loadHistory() {
  if (histLoading || histDone) return;
  histLoading = true;
  const page = await api().get_history(histOffset, 50);
  histLoading = false;
  if (!page.length) { histDone = true; return; }
  histOffset += page.length;
  histRows = histRows.concat(page);
  renderHistory();
}
function renderHistory() {
  const q = $("#hist-search").value.trim().toLowerCase();
  const list = $("#hist-list");
  list.innerHTML = "";
  histRows
    .filter(r => !q || [r.sender, r.subject, r.seller].some(
      v => (v || "").toLowerCase().includes(q)))
    .forEach(r => list.appendChild(card(r)));
}
$("#hist-search").addEventListener("input", renderHistory);
new IntersectionObserver(es => {
  if (es.some(e => e.isIntersecting)) loadHistory();
}).observe($("#hist-sentinel"));

// ---- fallbacks view -------------------------------------------------------
async function loadFallbacks() {
  if (!CATEGORIES.length) CATEGORIES = await api().categories();
  const list = $("#fb-list");
  list.innerHTML = "";
  const items = await api().get_fallbacks();
  $("#fb-empty").hidden = items.length > 0;
  for (const it of items) list.appendChild(await fallbackCard(it));
  updateHandoffButton();
  refreshBadge(items.length);
}
async function fallbackCard(it) {
  const n = $("#tpl-fallback").content.cloneNode(true);
  $(".card-title", n).textContent = it.subject || "(no subject)";
  $(".card-sub", n).textContent = `${it.sender} · ${it.account}`;
  $(".open-folder", n).addEventListener("click", e => {
    e.preventDefault(); api().open_folder(it.folder_path);
  });
  $(".open-pdf", n).addEventListener("click", e => {
    e.preventDefault(); api().open_folder(it.folder_path + "\\email.pdf");
  });
  const sel = $(".f-category", n);
  sel.innerHTML = `<option value="">no category</option>` +
    CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join("");

  const s = await api().suggest_fallback(it.message_id);
  $(".f-seller", n).value = s.seller || "";
  $(".f-product", n).value = s.product || "";
  if (s.category) sel.value = s.category;
  $(".f-sender", n).value = s.match_sender_contains || "";
  if (s.kind) $(`.fb-form input[value="${s.kind}"]`, n).checked = true;
  const conf = $(".conf", n);
  conf.textContent = s.confidence === "low"
    ? "low confidence — consider handling with Claude" : s.confidence + " confidence";
  conf.classList.add(s.confidence || "medium");

  $(".fb-check", n).addEventListener("change", updateHandoffButton);
  $(".fb-form", n).addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const decision = {
      kind: $("input[name=kind]:checked", form).value,
      seller: $(".f-seller", form).value.trim(),
      product: $(".f-product", form).value.trim(),
      category: $(".f-category", form).value || null,
      base_dir: $(".f-basedir", form).value.trim() || null,
      match_sender_contains: $(".f-sender", form).value.trim(),
      match_subject_contains: $(".f-subject", form).value.trim() || null,
    };
    const res = await api().apply_fallback(it.message_id, decision);
    if (res && res.ok) {
      form.closest(".card").remove();
      toast(`Resolved: ${decision.seller || it.subject}`);
      loadFallbacks();
    } else {
      toast((res && res.error) || "Failed to apply", true);
    }
  });
  n.querySelector(".card").dataset.mid = it.message_id;
  return n;
}
function selectedFallbackIds() {
  return $$(".card.fb").filter(c => $(".fb-check", c).checked).map(c => c.dataset.mid);
}
function updateHandoffButton() {
  $("#fb-handoff").disabled = selectedFallbackIds().length === 0;
}
$("#fb-handoff").addEventListener("click", async () => {
  const ids = selectedFallbackIds();
  const res = await api().handoff(ids);
  toast(res.ok ? `Opened Claude for ${res.count} fallback(s).`
                : (res.error || "Failed to open Claude"), !res.ok);
});

// ---- misc --------------------------------------------------------------
async function refreshBadge(count) {
  if (count === undefined) {
    try { count = (await api().get_fallbacks()).length; } catch { count = 0; }
  }
  const b = $("#fb-badge");
  b.textContent = count;
  b.hidden = !count;
}
function toast(msg, isError) {
  const d = document.createElement("div");
  d.className = "toast" + (isError ? " error" : "");
  d.textContent = msg;
  $("#toast-host").appendChild(d);
  setTimeout(() => d.remove(), 4000);
}

window.addEventListener("pywebviewready", () => {
  resetRunView();
  refreshBadge();
});
```

- [ ] **Step 2: Commit**

```bash
git add ui/app.js
git commit -m "Add startup UI behavior (views, scan stream, fallback forms)"
```

---

## Task 10: Dependencies, `run.bat`, shortcut, wiring

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\requirements.txt`
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\run.bat`
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\make_shortcut.py`

- [ ] **Step 1: Create `requirements.txt`**

```text
google-auth
google-auth-oauthlib
google-auth-httplib2
google-api-python-client
requests
plyer
weasyprint
msal
pywebview
pystray
Pillow
```

- [ ] **Step 2: Install the three new packages**

Run: `pip install pywebview pystray Pillow`
Expected: `Successfully installed pywebview-* pystray-* pillow-*`

- [ ] **Step 3: Create `run.bat`**

```bat
@echo off
start "" pythonw "%~dp0app.py"
```

- [ ] **Step 4: Create `make_shortcut.py`**

```python
"""
make_shortcut.py
----------------
One-off: drop a "Receipt Saver" shortcut into the Start Menu that launches
run.bat. Run manually once:  python make_shortcut.py
"""

import os
from pathlib import Path

RUN_BAT = Path(__file__).with_name("run.bat")
START_MENU = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
LNK = START_MENU / "Receipt Saver.lnk"


def main():
    import win32com.client  # from pywin32; install if missing
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(LNK))
    sc.TargetPath = str(RUN_BAT)
    sc.WorkingDirectory = str(RUN_BAT.parent)
    sc.IconLocation = "shell32.dll,297"
    sc.Save()
    print(f"Created {LNK}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Launch the app end-to-end (manual)**

Run: `pythonw app.py` (or double-click `run.bat`)
Expected within ~10 s:
- A borderless window appears, centered, dark theme, titled area shows "Receipt Saver".
- "This run" view shows "Scanning …" then per-account lines, then cards for handled mail, then a summary line.
- A tray icon appears; right-click shows Open / Run scan now / Quit.
- Clicking a card's "Open folder" opens Explorer at that folder.
- "History" tab lists past rows and lazy-loads on scroll.
- "Fallbacks" tab lists unresolved entries, each with a pre-filled form; the badge shows the count.
- Close button hides the window; tray "Open" brings it back; tray "Quit" ends the process.

If the window is blank: check `receipt_saver.log` for an `[app.py]` line, and run `python app.py` (not `pythonw`) once to see console errors.

- [ ] **Step 6: Verify the headless engine is untouched**

Run: `python receipt_saver.py`
Expected: same behavior as before this plan — scans, saves, notifications; exit 0.

- [ ] **Step 7: Full test suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt run.bat make_shortcut.py
git commit -m "Add requirements.txt, run.bat (launches app.py), Start Menu shortcut helper"
```

---

## Task 11: Documentation

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\DOCUMENTATION.md`

- [ ] **Step 1: Update the Scripts Folder table**

In the `## Scripts Folder` table, add rows (keep alphabetical-ish grouping with the code files):

```markdown
| `app.py` | Startup window (pywebview). Opens at login, drives the scan on a worker thread, streams results into the UI, serves history + fallback data, applies fallback decisions. Launched by `run.bat`. |
| `history.py` | Append-only `history.json` store — one record per handled mail, backs the History view. |
| `fallback_ops.py` | Heuristic `suggest()` for unresolved fallbacks + `apply_decision()` (rule / once / exclude / skip): writes `custom_rules.json`, moves the folder out of `_לטיפול ידני`, marks `fallback_log.json` resolved, patches the history row. |
| `claude_handoff.py` | Opens a pre-seeded `claude` terminal for fallbacks that need manual classification. |
| `tray.py` | Resident system-tray icon (Open / Run scan now / Quit). |
| `ui/` | Frontend for `app.py` — `index.html`, `app.css`, `app.js`. No build step. |
| `make_shortcut.py` | One-off: creates a Start Menu shortcut to `run.bat`. |
| `history.json` | Structured log of every handled mail since the UI shipped. |
| `requirements.txt` | Pinned dependency list. |
```

Change the existing `run.bat` row to: `Runs the script — launches `pythonw app.py` (the startup window), which runs the scan. Shortcut placed in Windows startup folder.`

- [ ] **Step 2: Add a "Startup UI" section**

Insert after the `## Desktop Notifications` section:

```markdown
---

## Startup UI (`app.py`)

At login `run.bat` launches `pythonw app.py` — a borderless, centered window
(pywebview). It replaces the old headless `python receipt_saver.py` startup run;
`receipt_saver.py` still runs standalone for manual/scheduled use.

**Three views:**

| View | What it shows |
|------|---------------|
| **This run** | Live results of the scan that runs when the window opens: a card per handled mail (action pill, seller · product or sender · subject, account, date, Open folder), account progress lines, and a `N saved · M fallback` summary. |
| **History** | Every mail handled since the UI shipped, newest first, lazy-loaded on scroll, with a text filter. Backed by `history.json`. |
| **Fallbacks** | Unresolved `fallback_log.json` entries. Each row has a form pre-filled by a heuristic guess (`fallback_ops.suggest`, sender + subject only). Pick **Make a rule** / **Move this one only** / **Exclude as promotional** / **Skip**, adjust fields, **Apply**. Multi-select + **Handle selected with Claude →** opens a pre-seeded `claude` terminal for the hard ones. |

**Applying a fallback decision** (`fallback_ops.apply_decision`):
- `rule` — append a rule to `custom_rules.json`, move + rename the folder from
  `_לטיפול ידני` to the computed destination, mark resolved, set the history row
  to `RESOLVED`.
- `once` — same, minus the `custom_rules.json` write.
- `exclude` — append an `{"exclude": true}` rule, delete the folder, log to
  `cleanup_log.json`, mark resolved.
- `skip` — nothing; the row stays for next time.

**Tray:** a resident tray icon (Open / Run scan now / Quit). Closing the window
hides it to the tray; Quit ends the process.

**history.json record shape:** `id` (`account:messageId`), `run_id`,
`handled_at`, `account`, `account_email`, `date`, `sender`, `subject`, `action`
(`DOWNLOADED | ICOUNT | JAPANOLOGIA | FALLBACK | EXCLUDED | RESOLVED`), `seller`,
`product`, `category`, `folder_name`, `folder_path`, `files`, `rule_source`
(`hardcoded | custom | icount | japanologia | null`). Resolved fallbacks also get
`resolved_at`* and `resolution` (`rule | once | exclude`).

*(\* `resolution` is set; `resolved_at` is reserved for a future change.)*
```

- [ ] **Step 3: Update the Dependencies table**

Add rows to the `## Dependencies` table:

```markdown
| `pywebview` | Frameless startup window hosting the HTML/CSS/JS UI |
| `pystray` | System-tray icon |
| `Pillow` | Tray icon image generation |
```

And update the install line to:
`pip install -r requirements.txt`

- [ ] **Step 4: Commit**

```bash
git add DOCUMENTATION.md
git commit -m "Document the startup UI, history.json, and fallback console"
```

---

## Self-Review Notes

**Spec coverage check:**
- Borderless centered modern window → Task 6 (`frameless=True`, `background_color`) + Task 8 CSS. ✓
- UI opens first, drives the scan → Task 6 `_bootstrap` calls `start_scan`; Task 9 streams events. ✓
- "This run" view (list, what was done, basic info) → Task 9 `onScanEvent` + `card()`. ✓
- History from day one, scrollable → Task 1 `page()`, Task 9 IntersectionObserver. ✓ (starts empty, per spec.)
- Fallbacks: choose what happens, make rules, automatic actions → Task 4 `apply_decision`, Task 9 form. ✓
- Per-fallback choice (rule vs one-off) → `kind` in decision object, radio buttons. ✓
- Heuristic suggestions only → Task 3 `suggest()`, no network/AI. ✓
- Can't-be-automated → redirect to pre-seeded Claude Code terminal → Task 5 + Task 9 handoff button. ✓
- Tray + Start Menu shortcut reopen → Task 7 + Task 10 `make_shortcut.py`. ✓
- Standalone engine unchanged → Task 2 Step 7, Task 10 Step 6. ✓

**Placeholder scan:** Task 2 Step 6 contains a conditional fallback ("mark `@unittest.skip`") — acceptable because it's a concrete instruction with the exact decorator, triggered only by an environment condition. No `TODO`/`TBD` elsewhere.

**Type consistency:** `record` shape identical in Task 2 (`_make_record`), Task 1 test fixtures, Task 6 (`get_run`/events), Task 9 (`card()`). Decision object identical in Task 4 tests, Task 6 `apply_fallback`, Task 9 form submit. `suggest()` return keys (`seller, product, category, match_sender_contains, kind, confidence`) identical in Task 3, Task 6, Task 9. `Api` method names (`start_scan, scan_running, get_run, get_history, get_fallbacks, suggest_fallback, apply_fallback, handoff, open_folder, categories, minimize, hide, quit_app`) consistent between Task 6 and Task 9.
