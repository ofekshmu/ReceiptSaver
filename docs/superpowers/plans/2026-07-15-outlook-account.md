# Microsoft 365 Mailbox Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `sternum` Microsoft 365 mailbox (ofeks@sternum-sec.com) to Receipt Saver, processed through the same rule pipeline (hardcoded rules, `custom_rules.json`, fallback, TickTick, notifications, folder structure) as the three existing Gmail accounts.

**Architecture:** Extract the Gmail-specific code out of `receipt_saver.py` into `gmail_provider.py` (behavior-preserving refactor), rewrite `receipt_saver.py`'s core logic to consume a provider-agnostic `NormalizedMessage` dict instead of raw Gmail payloads, then add a new `outlook_provider.py` implementing the same three-function interface (`get_service`, `list_candidate_ids`, `fetch_message`) against Microsoft Graph. `receipt_saver.py` dispatches per-account via a `"provider"` field.

**Tech Stack:** Python, `msal` (new dependency, Microsoft's OAuth library, device-code flow), `requests` (already a dependency, used directly against the Graph REST API instead of an SDK).

**Design doc:** `docs/superpowers/specs/2026-07-15-outlook-account-design.md`

---

## Reference: Azure AD app already registered

- Application (client) ID: `53ade867-6e25-4987-a8f2-49238eef8100`
- Directory (tenant) ID: `e96b8461-947b-4d64-936a-ef26513a3b58`
- Public client flow enabled, `Mail.Read` + `offline_access` delegated permissions requested.

---

### Task 1: Extract `gmail_provider.py` (behavior-preserving refactor)

Move every Gmail-specific piece of `receipt_saver.py` into a new file, unchanged in behavior. This isolates the refactor risk from the process_message/provider-interface changes in Task 2.

**Files:**
- Create: `gmail_provider.py`
- Modify: `receipt_saver.py:1-84` (imports, SCOPES, `build_gmail_query`)
- Modify: `receipt_saver.py:180-184` (`first_attachment_name`)
- Modify: `receipt_saver.py:255-256` (`gmail_link`)
- Modify: `receipt_saver.py:292-330` (`get_body_text`, `get_body_html`)
- Modify: `receipt_saver.py:434-471` (`get_gmail_service`, `save_attachments`)

- [ ] **Step 1: Create `gmail_provider.py` with the extracted code**

```python
"""
gmail_provider.py
------------------
Gmail-specific implementation of the provider interface consumed by
receipt_saver.py: get_service(account), list_candidate_ids(service, account,
custom_rules_file), fetch_message(service, msg_id, account).

Extracted from receipt_saver.py with no behavior change.
"""

import base64
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

GMAIL_SUBJECT_KEYWORDS = (
    'subject:receipt OR subject:invoice OR subject:קבלה OR subject:קבלת '
    'OR subject:חשבונית OR subject:אישור OR subject:הזמנה '
    'OR subject:תשלום OR subject:purchase OR subject:payment'
)


def build_gmail_query(custom_rules_file: Path) -> str:
    """Build Gmail search query, adding from: exceptions for domain-based custom rules."""
    base = f'-in:sent -subject:פרסומת newer_than:60d ((has:attachment AND ({GMAIL_SUBJECT_KEYWORDS}))'
    try:
        rules = json.loads(custom_rules_file.read_text(encoding="utf-8"))
        for rule in rules:
            sender  = rule.get("match_sender_contains", "") or ""
            exclude = rule.get("exclude_subject_contains", "") or ""
            if "." in sender:  # domain-based match (e.g. icmega.org)
                clause = f"from:{sender}"
                if exclude:
                    clause = f"({clause} -subject:{exclude})"
                base += f" OR {clause}"
    except Exception:
        pass
    base += ' OR (has:attachment AND subject:"סיכום שיעור יפנית")'
    return base + ")"


def get_service(account: dict):
    creds = None
    token_file = account["token_file"]
    creds_file = account["creds_file"]

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            creds = flow.run_local_server(port=0, login_hint=account["email"])
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def list_candidate_ids(service, account: dict, custom_rules_file: Path) -> list:
    query = build_gmail_query(custom_rules_file)
    results = service.users().messages().list(
        userId="me", q=query, maxResults=300
    ).execute()
    return [m["id"] for m in results.get("messages", [])]


def _first_attachment_name(payload: dict) -> str:
    for part in payload.get("parts", []):
        if part.get("filename"):
            return part["filename"]
    return ""


def _get_body_text(payload: dict) -> str:
    """Extract plain text from email payload (no HTML)."""
    result = []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data and mime == "text/plain":
            result.append(base64.urlsafe_b64decode(data).decode("utf-8", errors="replace"))
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return "\n".join(result)


def _get_body_html(payload: dict) -> str:
    """Extract HTML body, falling back to plain text wrapped in <pre>."""
    html_part  = None
    plain_part = None

    def walk(part):
        nonlocal html_part, plain_part
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/html" and not html_part:
                html_part = decoded
            elif mime == "text/plain" and not plain_part:
                plain_part = decoded
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    if html_part:
        return html_part
    if plain_part:
        return f"<pre style='font-family:Arial,sans-serif;white-space:pre-wrap'>{plain_part}</pre>"
    return "<p>(no body)</p>"


def _fetch_attachments(service, msg_id: str, payload: dict):
    saved = []

    def walk(parts):
        for part in parts:
            filename = part.get("filename", "")
            body = part.get("body", {})
            if filename and body.get("attachmentId"):
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=body["attachmentId"]
                ).execute()
                data = base64.urlsafe_b64decode(att["data"])
                saved.append((filename, data))
            if part.get("parts"):
                walk(part["parts"])

    walk(payload.get("parts", [payload]))
    return saved


def gmail_link(msg_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"


def fetch_message(service, msg_id: str, account: dict) -> dict:
    """Fetch a Gmail message and return it as a provider-agnostic NormalizedMessage dict."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg["payload"]
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

    return {
        "id": msg_id,
        "sender": headers.get("From", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "date_raw": headers.get("Date", ""),
        "is_sent": "SENT" in msg.get("labelIds", []),
        "body_text": _get_body_text(payload),
        "body_html": _get_body_html(payload),
        "first_attachment_name": _first_attachment_name(payload),
        "attachments": lambda: _fetch_attachments(service, msg_id, payload),
        "link": gmail_link(msg_id),
    }
```

- [ ] **Step 2: Verify the new file imports cleanly**

Run: `python -c "import gmail_provider; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add gmail_provider.py
git commit -m "Extract Gmail-specific logic into gmail_provider.py"
```

---

### Task 2: Rewire `receipt_saver.py` to the provider interface (Gmail only for now)

Replace every Gmail-payload-shaped piece of `receipt_saver.py` with the provider-agnostic equivalent, wired only to `gmail_provider` for this task — no Outlook yet. This isolates the "does the refactor preserve behavior" question from "does Outlook work."

**Files:**
- Modify: `receipt_saver.py` (imports, `ACCOUNTS`, removes `GMAIL_SUBJECT_KEYWORDS`/`build_gmail_query`/`first_attachment_name`/`get_body_text`/`get_body_html`, rewrites `save_attachments`, `save_email_pdf`, `create_icount_ticktick_task`, `process_message`, `main`)

- [ ] **Step 1: Replace the top of `receipt_saver.py` (imports through `build_gmail_query`)**

The live file has an uncommitted, unrelated Japanese-lessons feature sitting between `SCOPES` and the end of this range — `_LESSON_SUBJECT_RE` (keep it, it's used later by `parse_lesson_folder`), followed by `GMAIL_SUBJECT_KEYWORDS` and `build_gmail_query` (delete both — they moved to `gmail_provider.py` in Task 1 and `main()` no longer calls `build_gmail_query` directly). Replace lines 1 through the end of `build_gmail_query()` (module docstring through `return base + ")"`) with:

```python
"""
receipt_saver.py
----------------
Runs on Windows startup via Task Scheduler.
Scans four mailboxes (three Gmail, one Microsoft 365) and saves receipt
attachments.

Folder format: YYYY-MM-DD - Seller - Product - [account]
  account labels: ofek | family | yuval | sternum

Decision pipeline per email:
  1. Skip if in SENT folder
  2. Check hardcoded KNOWN_RULES  → save to קבלות\
  3. Check custom_rules.json      → save to קבלות\
  4. Fallback                     → save to קבלות\_לטיפול ידני\
                                    + log to fallback_log.json
                                    + TickTick task
                                    + desktop notification

Requirements:
    pip install google-auth google-auth-oauthlib google-auth-httplib2
                google-api-python-client requests plyer weasyprint msal
"""

import re
import json
import logging
import datetime
from pathlib import Path

import requests

import gmail_provider
import outlook_provider

try:
    from plyer import notification as _plyer_notification
    _PLYER_OK = True
except ImportError:
    _PLYER_OK = False

try:
    from weasyprint import HTML as _WeasyprintHTML
    _WEASYPRINT_OK = True
except Exception:
    _WEASYPRINT_OK = False

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
RECEIPTS_DIR        = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR          = RECEIPTS_DIR / "_לטיפול ידני"
JAPANOLOGIA_DIR     = Path(r"C:\Users\ofeks\OneDrive\Ofek\Japanese Lessons\Japanologia")
SCRIPT_DIR          = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
PROCESSED_FILE      = SCRIPT_DIR / "processed_ids.json"
CUSTOM_RULES_FILE   = SCRIPT_DIR / "custom_rules.json"
FALLBACK_LOG_FILE   = SCRIPT_DIR / "fallback_log.json"
LOG_FILE            = SCRIPT_DIR / "receipt_saver.log"
TICKTICK_TOKEN_FILE = SCRIPT_DIR / "ticktick_token.json"

# One credentials + token file per account
ACCOUNTS = [
    {
        "label":       "ofek",
        "email":       "ofek.shmuel1@gmail.com",
        "provider":    "gmail",
        "creds_file":  SCRIPT_DIR / "credentials_ofek.json",
        "token_file":  SCRIPT_DIR / "token_ofek.json",
    },
    {
        "label":       "family",
        "email":       "shmuelfamily21@gmail.com",
        "provider":    "gmail",
        "creds_file":  SCRIPT_DIR / "credentials_family.json",
        "token_file":  SCRIPT_DIR / "token_family.json",
    },
    {
        "label":       "yuval",
        "email":       "yuvalritsker@gmail.com",
        "provider":    "gmail",
        "creds_file":  SCRIPT_DIR / "credentials_yuval.json",
        "token_file":  SCRIPT_DIR / "token_yuval.json",
    },
]

PROVIDERS = {
    "gmail":   gmail_provider,
    "outlook": outlook_provider,
}

_LESSON_SUBJECT_RE = re.compile(r"סיכום שיעור יפנית\s+(\d{1,2})\.(\d{1,2})")
```

`GMAIL_SUBJECT_KEYWORDS` and `build_gmail_query()` are intentionally **not** carried over — they now live in `gmail_provider.py` (Task 1), and nothing in `receipt_saver.py` calls them anymore since `main()` will go through `provider.list_candidate_ids()` instead (Step 8 below).

(The `sternum` account and its `"outlook"` provider are intentionally left out of `ACCOUNTS` until Task 5 — this task must be verifiable against Gmail alone first.)

- [ ] **Step 2: Remove `first_attachment_name` from the HELPERS section**

In the `HELPERS` section (originally lines 150-184), delete this function (it moved to `gmail_provider._first_attachment_name`):

```python
def first_attachment_name(payload: dict) -> str:
    for part in payload.get("parts", []):
        if part.get("filename"):
            return part["filename"]
    return ""
```

- [ ] **Step 3: Update `parse_date` to accept both Gmail (RFC 2822) and Graph (ISO 8601) timestamps**

Replace:

```python
def parse_date(date_raw: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_raw).strftime("%Y_%m_%d")
    except Exception:
        return datetime.date.today().strftime("%Y_%m_%d")
```

with:

```python
def parse_date(date_raw: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_raw).strftime("%Y_%m_%d")
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(date_raw.replace("Z", "+00:00")).strftime("%Y_%m_%d")
    except Exception:
        pass
    return datetime.date.today().strftime("%Y_%m_%d")
```

This is required before Task 4 — Microsoft Graph returns `receivedDateTime` as ISO 8601 (e.g. `2026-07-09T11:47:23Z`), which `parsedate_to_datetime` cannot parse. Without this, every Outlook email would silently fall back to today's date.

- [ ] **Step 4: Remove `get_body_text` / `get_body_html` from the EMAIL → PDF section**

Delete both functions (moved to `gmail_provider._get_body_text` / `_get_body_html`). Keep `save_email_pdf` but change its signature to take the already-extracted HTML string instead of a raw Gmail payload:

Replace:

```python
def save_email_pdf(payload: dict, folder: Path,
                   subject: str, sender: str, date_str: str):
    """Render the email HTML to email.pdf inside the folder. Returns saved path or None."""
    if not _WEASYPRINT_OK:
        log.warning("weasyprint not available — skipping email.pdf")
        return None
    try:
        body_html = get_body_html(payload)
        full_html = f"""<!DOCTYPE html>
```

with:

```python
def save_email_pdf(body_html: str, folder: Path,
                   subject: str, sender: str, date_str: str):
    """Render the email HTML to email.pdf inside the folder. Returns saved path or None."""
    if not _WEASYPRINT_OK:
        log.warning("weasyprint not available — skipping email.pdf")
        return None
    try:
        full_html = f"""<!DOCTYPE html>
```

(the rest of the function — the `<html>...` string, `_WeasyprintHTML(...).write_pdf(...)`, the `except` clause — is unchanged.)

- [ ] **Step 5: Replace the GMAIL section with a provider-agnostic attachment saver**

Replace the entire `# GMAIL` section:

```python
# ══════════════════════════════════════════════════════════════════════════
# GMAIL
# ══════════════════════════════════════════════════════════════════════════

def get_gmail_service(account: dict):
    ... (whole function) ...

def save_attachments(service, msg_id: str, payload: dict, folder: Path) -> list:
    ... (whole function) ...
```

with:

```python
# ══════════════════════════════════════════════════════════════════════════
# ATTACHMENT SAVING
# ══════════════════════════════════════════════════════════════════════════

def save_attachments(attachments_fn, folder: Path) -> list:
    """attachments_fn is a zero-arg callable returning [(filename, bytes), ...],
    supplied by the provider's fetch_message()."""
    saved = []
    for filename, data in attachments_fn():
        dest = folder / sanitize(filename)
        dest.write_bytes(data)
        saved.append(dest)
    return saved
```

- [ ] **Step 6: Update `create_icount_ticktick_task` to take a link instead of a Gmail message ID**

Replace:

```python
def create_icount_ticktick_task(folder_name: str, folder_path: Path,
                                 account_label: str, msg_id: str, subject: str):
    if not TICKTICK_TOKEN_FILE.exists():
        log.warning("ticktick_token.json missing — skipping TickTick task")
        return
    token_data = json.loads(TICKTICK_TOKEN_FILE.read_text(encoding="utf-8"))
    task = {
        "title": f"הורד PDF: {folder_name}",
        "content": (
            f"חשבונית iCount — הPDF נמצא בקישור בתוך המייל.\n\n"
            f"פתח את המייל וגלול לקישור 'לצפייה':\n"
            f"{gmail_link(msg_id)}\n\n"
            f"שמור את הPDF לתיקייה:\n{folder_path}"
        ),
```

with:

```python
def create_icount_ticktick_task(folder_name: str, folder_path: Path,
                                 account_label: str, link: str, subject: str):
    if not TICKTICK_TOKEN_FILE.exists():
        log.warning("ticktick_token.json missing — skipping TickTick task")
        return
    token_data = json.loads(TICKTICK_TOKEN_FILE.read_text(encoding="utf-8"))
    task = {
        "title": f"הורד PDF: {folder_name}",
        "content": (
            f"חשבונית iCount — הPDF נמצא בקישור בתוך המייל.\n\n"
            f"פתח את המייל וגלול לקישור 'לצפייה':\n"
            f"{link}\n\n"
            f"שמור את הPDF לתיקייה:\n{folder_path}"
        ),
```

(the rest of the function — the `requests.post(...)` call and logging — is unchanged.) Also delete `gmail_link()` from the `# ICOUNT SPECIAL HANDLING` section (moved to `gmail_provider.gmail_link`).

- [ ] **Step 7: Rewrite `process_message` to consume a `NormalizedMessage` dict**

Replace the entire `process_message` function with:

```python
def process_message(msg: dict, account: dict) -> dict:
    label = account["label"]

    if msg["is_sent"]:
        return {"status": "skipped"}

    subject = msg["subject"]
    if "פרסומת" in subject:
        return {"status": "skipped"}
    sender    = msg["sender"]
    date_str  = parse_date(msg["date_raw"])
    first_att = msg["first_attachment_name"]
    body_html = msg["body_html"]

    # ── Japanese lesson summary ───────────────────────────────────────────
    if label == "ofek":
        lesson_folder = parse_lesson_folder(subject, date_str)
        if lesson_folder:
            dest = JAPANOLOGIA_DIR / lesson_folder
            dest.mkdir(parents=True, exist_ok=True)
            files = save_attachments(msg["attachments"], dest)
            _log_saved("JAPANOLOGIA", lesson_folder, sender, dest, files)
            return {"status": "saved", "folder_name": lesson_folder}

    # ── iCount special case ────────────────────────────────────────────────
    # PDF is inside a link in the email body — skip attachments (just logo),
    # save email as PDF, create TickTick task with a direct link to the email.
    if is_icount(sender, subject):
        m = re.search(r"מאת\s+(.+?)$", subject)
        seller  = sanitize(m.group(1).strip()) if m else "iCount"
        product = "חשבונית מס קבלה"
        custom_match = match_custom(sender, subject)
        category = custom_match[2] if custom_match else None
        root     = custom_match[3] if custom_match and custom_match[3] else RECEIPTS_DIR
        base_dir = root / category if category else root
        folder_name = f"{date_str} - {seller} - {product} - {label}"
        folder      = base_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        create_icount_ticktick_task(folder_name, folder, label, msg["link"], subject)
        _log_saved("ICOUNT", folder_name, sender, folder, [pdf] if pdf else [])
        return {"status": "saved", "folder_name": folder_name}

    # ── Step 1: hardcoded rules ────────────────────────────────────────────
    rule = match_hardcoded(sender, subject)
    if rule:
        seller, product_fn, category = rule
        product     = sanitize(product_fn(subject, first_att))
        base_dir    = RECEIPTS_DIR / category if category else RECEIPTS_DIR
        folder_name = f"{date_str} - {seller} - {product} - {label}"
        folder      = base_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        files = save_attachments(msg["attachments"], folder)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        if pdf:
            files.append(pdf)
        _log_saved("DOWNLOADED", folder_name, sender, folder, files)
        return {"status": "saved", "folder_name": folder_name}

    # ── Step 2: custom rules ───────────────────────────────────────────────
    body   = msg["body_text"]
    custom = match_custom(sender, subject, body)
    if custom:
        seller, product, category, rule_base_dir = custom
        if seller == "__exclude__":
            log.info(f"EXCLUDED   {sender} — {subject[:60]}")
            return {"status": "skipped"}
        root     = rule_base_dir if rule_base_dir else RECEIPTS_DIR
        base_dir = root / category if category else root
        folder_name = f"{date_str} - {sanitize(seller)} - {sanitize(product)} - {label}"
        folder      = base_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        files = save_attachments(msg["attachments"], folder)
        pdf = save_email_pdf(body_html, folder, subject, sender, date_str)
        if pdf:
            files.append(pdf)
        _log_saved("DOWNLOADED", folder_name, sender, folder, files)
        return {"status": "saved", "folder_name": folder_name}

    # ── Step 3: fallback ───────────────────────────────────────────────────
    sender_name   = extract_display_name(sender)
    subject_clean = sanitize(subject[:60])
    folder_name   = f"{date_str} - {sender_name} - {subject_clean} - {label}"
    folder        = MANUAL_DIR / folder_name
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
    return {"status": "fallback", "folder_name": folder_name,
            "sender": sender_name, "subject": subject, "account": label}
```

- [ ] **Step 8: Rewrite `main()` to dispatch through `PROVIDERS`**

Replace the account loop inside `main()`:

```python
    for account in ACCOUNTS:
        label = account["label"]
        log.info(f"── Account: {account['email']} ({label})")

        if not account["creds_file"].exists():
            log.warning(f"  credentials file not found: {account['creds_file'].name} — skipping")
            notify("⚠️ Receipt Saver", f"credentials_{label}.json חסר — דילוג על חשבון {label}")
            continue

        try:
            service = get_gmail_service(account)
        except Exception as e:
            log.error(f"  Auth failed for {label}: {e}")
            notify("⚠️ Receipt Saver", f"שגיאת כניסה לחשבון {label}")
            continue

        results  = service.users().messages().list(
            userId="me", q=build_gmail_query(), maxResults=300
        ).execute()
        messages = results.get("messages", [])
        log.info(f"  Candidates: {len(messages)}")

        for m in messages:
            mid = m["id"]
            # Use account-scoped ID to avoid cross-account collisions
            scoped_id = f"{label}:{mid}"
            if scoped_id in processed:
                continue
            try:
                result = process_message(service, mid, account)
                status = result.get("status")
                if status == "saved":
                    saved_folders.append(result["folder_name"])
                elif status == "fallback":
                    fallback_items.append(result)
                    notify(
                        "⚠️ קבלה לא זוהתה",
                        f"[{label}] מאת: {result['sender']}\n{result['subject'][:80]}",
                        timeout=10,
                    )
            except Exception as e:
                log.error(f"  Error on {mid}: {e}")
            finally:
                processed.add(scoped_id)
```

with:

```python
    for account in ACCOUNTS:
        label = account["label"]
        provider = PROVIDERS[account["provider"]]
        log.info(f"── Account: {account['email']} ({label})")

        if not account["creds_file"].exists():
            log.warning(f"  credentials file not found: {account['creds_file'].name} — skipping")
            notify("⚠️ Receipt Saver", f"credentials_{label}.json חסר — דילוג על חשבון {label}")
            continue

        try:
            service = provider.get_service(account)
        except Exception as e:
            log.error(f"  Auth failed for {label}: {e}")
            notify("⚠️ Receipt Saver", f"שגיאת כניסה לחשבון {label}")
            continue

        candidate_ids = provider.list_candidate_ids(service, account, CUSTOM_RULES_FILE)
        log.info(f"  Candidates: {len(candidate_ids)}")

        for mid in candidate_ids:
            # Use account-scoped ID to avoid cross-account collisions
            scoped_id = f"{label}:{mid}"
            if scoped_id in processed:
                continue
            try:
                msg = provider.fetch_message(service, mid, account)
                result = process_message(msg, account)
                status = result.get("status")
                if status == "saved":
                    saved_folders.append(result["folder_name"])
                elif status == "fallback":
                    fallback_items.append(result)
                    notify(
                        "⚠️ קבלה לא זוהתה",
                        f"[{label}] מאת: {result['sender']}\n{result['subject'][:80]}",
                        timeout=10,
                    )
            except Exception as e:
                log.error(f"  Error on {mid}: {e}")
            finally:
                processed.add(scoped_id)
```

- [ ] **Step 9: Create a placeholder `outlook_provider.py` so the import resolves**

This task should not implement Outlook yet — Task 4 does that. Create a minimal stub so `import outlook_provider` in `receipt_saver.py` doesn't break:

```python
"""Placeholder — implemented in Task 4 of the outlook-account plan."""
```

- [ ] **Step 10: Write a small unittest for `parse_date` (both timestamp formats)**

Create `test_receipt_saver.py`:

```python
import unittest
from receipt_saver import parse_date


class TestParseDate(unittest.TestCase):
    def test_rfc2822_gmail_date(self):
        self.assertEqual(parse_date("Thu, 9 Jul 2026 14:47:00 +0300"), "2026_07_09")

    def test_iso8601_graph_date(self):
        self.assertEqual(parse_date("2026-07-09T11:47:23Z"), "2026_07_09")

    def test_garbage_falls_back_to_today(self):
        result = parse_date("not a date")
        self.assertRegex(result, r"^\d{4}_\d{2}_\d{2}$")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 11: Run the test**

Run: `python -m unittest test_receipt_saver.py -v`
Expected: 3 tests, all `ok`

- [ ] **Step 12: Manually verify the refactor preserved behavior for the 3 Gmail accounts**

Run: `python receipt_saver.py`
Expected: same behavior as before this task — check `receipt_saver.log` for `── Account: ofek.shmuel1@gmail.com (ofek)` etc. for all three accounts, no new errors, and (if any new receipts arrived) folders created exactly as before. This is the checkpoint that confirms the refactor is behavior-preserving before Outlook is added.

- [ ] **Step 13: Commit**

```bash
git add receipt_saver.py outlook_provider.py test_receipt_saver.py
git commit -m "Rewire receipt_saver.py to a provider-agnostic message interface"
```

---

### Task 3: Implement `outlook_provider.py` (Microsoft Graph via MSAL device-code flow)

**Files:**
- Modify: `outlook_provider.py` (replace the Task 2 placeholder)
- Test: `test_outlook_provider.py`

- [ ] **Step 1: Write the failing test for the relevance predicate**

Create `test_outlook_provider.py`:

```python
import unittest
from outlook_provider import _is_relevant


class TestIsRelevant(unittest.TestCase):
    def test_matches_subject_keyword_with_attachment(self):
        self.assertTrue(_is_relevant("billing@sternum-sec.com", "חשבונית מס 123", True, []))

    def test_no_attachment_no_keyword_match_is_irrelevant(self):
        self.assertFalse(_is_relevant("someone@example.com", "hello there", False, []))

    def test_custom_rule_domain_always_matches(self):
        self.assertTrue(_is_relevant("billing@sternum-sec.com", "unrelated subject", False, ["sternum-sec.com"]))

    def test_japanese_lesson_subject_matches_without_attachment_flag(self):
        self.assertTrue(_is_relevant("teacher@example.com", "סיכום שיעור יפנית 1.6", False, []))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m unittest test_outlook_provider.py -v`
Expected: `ImportError: cannot import name '_is_relevant' from 'outlook_provider'`

- [ ] **Step 3: Implement `outlook_provider.py`**

```python
"""
outlook_provider.py
--------------------
Microsoft Graph implementation of the provider interface consumed by
receipt_saver.py: get_service(account), list_candidate_ids(service, account,
custom_rules_file), fetch_message(service, msg_id, account).

Auth is MSAL device-code flow against an Azure AD app registration
(public client, no secret) — see docs/superpowers/specs/2026-07-15-outlook-account-design.md
for the app registration steps. The token cache is persisted to the
account's token_file and silently refreshed on subsequent runs, mirroring
the Gmail token-file behavior.
"""

import base64
import datetime
import json
import logging
from pathlib import Path

import msal
import requests

log = logging.getLogger("receipt_saver")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read"]

# Mirrors GMAIL_SUBJECT_KEYWORDS in gmail_provider.py, as a plain word list
# instead of Gmail search syntax.
SUBJECT_KEYWORDS = [
    "receipt", "invoice", "קבלה", "קבלת", "חשבונית",
    "אישור", "הזמנה", "תשלום", "purchase", "payment",
]


def _build_msal_app(account: dict, cache: msal.SerializableTokenCache):
    creds = json.loads(account["creds_file"].read_text(encoding="utf-8"))
    authority = f"https://login.microsoftonline.com/{creds['tenant_id']}"
    return msal.PublicClientApplication(
        client_id=creds["client_id"],
        authority=authority,
        token_cache=cache,
    )


def get_service(account: dict) -> dict:
    """Authenticate via MSAL device-code flow. Returns {"access_token": str}."""
    cache = msal.SerializableTokenCache()
    token_file = account["token_file"]
    if token_file.exists():
        cache.deserialize(token_file.read_text(encoding="utf-8"))

    app = _build_msal_app(account, cache)

    result = None
    accounts = app.get_accounts(username=account["email"])
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow.get('error_description', flow)}")
        print(flow["message"])
        log.warning(flow["message"])
        result = app.acquire_token_by_device_flow(flow)

    if cache.has_state_changed:
        token_file.write_text(cache.serialize(), encoding="utf-8")

    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    return {"access_token": result["access_token"]}


def _custom_rule_domains(custom_rules_file: Path) -> list:
    try:
        rules = json.loads(custom_rules_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    domains = []
    for rule in rules:
        sender = rule.get("match_sender_contains", "") or ""
        if "." in sender:
            domains.append(sender.lower())
    return domains


def _is_relevant(sender: str, subject: str, has_attachment: bool, domains: list) -> bool:
    sender_l, subject_l = sender.lower(), subject.lower()
    if any(d in sender_l for d in domains):
        return True
    if "סיכום שיעור יפנית" in subject:
        return True
    if has_attachment and any(kw.lower() in subject_l for kw in SUBJECT_KEYWORDS):
        return True
    return False


def list_candidate_ids(service, account: dict, custom_rules_file: Path) -> list:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {service['access_token']}"}
    url = (
        f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
        f"?$filter=receivedDateTime ge {since}"
        f"&$orderby=receivedDateTime desc"
        f"&$select=id,subject,from,hasAttachments"
        f"&$top=50"
    )
    domains = _custom_rule_domains(custom_rules_file)
    ids = []
    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("value", []):
            sender = m.get("from", {}).get("emailAddress", {}).get("address", "")
            subject = m.get("subject", "") or ""
            if _is_relevant(sender, subject, m.get("hasAttachments", False), domains):
                ids.append(m["id"])
        url = data.get("@odata.nextLink")
    return ids


def _fetch_attachments(access_token: str, msg_id: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(f"{GRAPH_BASE}/me/messages/{msg_id}/attachments", headers=headers, timeout=15)
    resp.raise_for_status()
    saved = []
    for att in resp.json().get("value", []):
        if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
            data = base64.b64decode(att["contentBytes"])
            saved.append((att["name"], data))
    return saved


def outlook_link(msg_id: str) -> str:
    return f"https://outlook.office.com/mail/inbox/id/{msg_id}"


def fetch_message(service, msg_id: str, account: dict) -> dict:
    access_token = service["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(
        f"{GRAPH_BASE}/me/messages/{msg_id}"
        f"?$select=id,subject,from,receivedDateTime,body,hasAttachments",
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    m = resp.json()

    from_obj = m.get("from", {}).get("emailAddress", {}) or {}
    address  = from_obj.get("address", "")
    name     = from_obj.get("name", "")
    sender   = f'"{name}" <{address}>' if name else address

    body      = m.get("body", {}) or {}
    body_html = body.get("content", "") if body.get("contentType") == "html" else ""
    body_text = body.get("content", "") if body.get("contentType") == "text" else ""
    if not body_html and body_text:
        body_html = f"<pre style='font-family:Arial,sans-serif;white-space:pre-wrap'>{body_text}</pre>"

    atts_cache = _fetch_attachments(access_token, msg_id) if m.get("hasAttachments") else []
    first_attachment_name = atts_cache[0][0] if atts_cache else ""

    return {
        "id": msg_id,
        "sender": sender,
        "subject": m.get("subject", "(no subject)") or "(no subject)",
        "date_raw": m.get("receivedDateTime", ""),
        "is_sent": False,  # only the Inbox folder is queried; Sent items never appear here
        "body_text": body_text,
        "body_html": body_html,
        "first_attachment_name": first_attachment_name,
        "attachments": lambda: atts_cache,
        "link": outlook_link(msg_id),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest test_outlook_provider.py -v`
Expected: 4 tests, all `ok`

- [ ] **Step 5: Install the new dependency**

Run: `pip install msal`
Expected: successful install

- [ ] **Step 6: Verify the module imports cleanly**

Run: `python -c "import outlook_provider; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add outlook_provider.py test_outlook_provider.py
git commit -m "Implement Microsoft Graph provider (MSAL device-code auth)"
```

---

### Task 4: Wire the `sternum` account into `receipt_saver.py`

**Files:**
- Create: `credentials_sternum.json` (gitignored — matches existing `credentials_*.json` pattern)
- Modify: `receipt_saver.py:ACCOUNTS`

- [ ] **Step 1: Create `credentials_sternum.json`**

```json
{
  "client_id": "53ade867-6e25-4987-a8f2-49238eef8100",
  "tenant_id": "e96b8461-947b-4d64-936a-ef26513a3b58"
}
```

- [ ] **Step 2: Verify it's gitignored**

Run: `git check-ignore -v credentials_sternum.json`
Expected: prints a match against the `credentials_*.json` rule in `.gitignore` (exit code 0)

- [ ] **Step 3: Add the `sternum` entry to `ACCOUNTS` in `receipt_saver.py`**

Add this entry to the `ACCOUNTS` list (after the `yuval` entry):

```python
    {
        "label":       "sternum",
        "email":       "ofeks@sternum-sec.com",
        "provider":    "outlook",
        "creds_file":  SCRIPT_DIR / "credentials_sternum.json",
        "token_file":  SCRIPT_DIR / "token_sternum.json",
    },
```

- [ ] **Step 4: Commit**

```bash
git add receipt_saver.py
git commit -m "Add sternum Microsoft 365 account to ACCOUNTS"
```

(`credentials_sternum.json` is intentionally not committed — it's gitignored, matching every other `credentials_*.json` file.)

---

### Task 5: Add the Sternum payslip custom rule

**Files:**
- Modify: `custom_rules.json`

- [ ] **Step 1: Read the current file to get the exact insertion point**

Run: `python -c "import json; print(len(json.load(open('custom_rules.json', encoding='utf-8'))))"` to confirm the current entry count before editing.

- [ ] **Step 2: Add the new rule as the last entry in the JSON array**

```json
  {
    "_comment": "Sternum payslip (billing@sternum-sec.com)",
    "match_sender_contains": "billing@sternum-sec.com",
    "match_subject_contains": null,
    "exclude_subject_contains": null,
    "match_body_contains": "תלוש שכר",
    "product_body_regex": "(תלוש שכר לחודש \\S+ \\d{4})",
    "seller": "משכורת",
    "product": "תלוש שכר",
    "category": null,
    "base_dir": "C:\\Users\\ofeks\\OneDrive\\Ofek\\Work\\Sternum\\משכורות"
  }
```

- [ ] **Step 3: Validate the JSON is well-formed**

Run: `python -c "import json; json.load(open('custom_rules.json', encoding='utf-8')); print('valid')"`
Expected: `valid`

- [ ] **Step 4: Write a unit test locking in the product-extraction regex**

Add to `test_receipt_saver.py`:

```python
from receipt_saver import match_custom


class TestSternumPayslipRule(unittest.TestCase):
    def test_extracts_month_and_year_from_body(self):
        result = match_custom(
            "billing@sternum-sec.com",
            "some subject line",
            'היי אופק,\n\nמצ"ב תלוש שכר לחודש יוני 2026.\n\nבברכה,',
        )
        seller, product, category, base_dir = result
        self.assertEqual(seller, "משכורת")
        self.assertEqual(product, "תלוש שכר לחודש יוני 2026")
        self.assertEqual(base_dir, Path(r"C:\Users\ofeks\OneDrive\Ofek\Work\Sternum\משכורות"))

    def test_falls_back_to_static_product_if_regex_does_not_match(self):
        result = match_custom(
            "billing@sternum-sec.com",
            "some subject line",
            "תלוש שכר בפורמט שונה לגמרי",
        )
        seller, product, category, base_dir = result
        self.assertEqual(product, "תלוש שכר")
```

- [ ] **Step 5: Run the test**

Run: `python -m unittest test_receipt_saver.py -v`
Expected: all tests `ok`, including the 2 new ones

- [ ] **Step 6: Commit**

```bash
git add custom_rules.json test_receipt_saver.py
git commit -m "Add Sternum payslip custom rule"
```

---

### Task 6: Manual end-to-end verification against the live sternum mailbox

This is the one part of the feature that cannot be verified without hitting the real Microsoft Graph API and a real device-code consent prompt — do this manually, not via a subagent.

**Files:** none (verification only)

- [ ] **Step 1: Run the script manually from a visible terminal (not via silent startup)**

Run: `python receipt_saver.py`

The first run for `sternum` has no cached token, so `outlook_provider.get_service` will print and log a message like:
`To sign in, use a web browser to open https://microsoft.com/devicelogin and enter the code XXXXXXXX to authenticate.`

- [ ] **Step 2: Complete the device-code consent in a browser**

Visit the printed URL, enter the code, sign in as ofeks@sternum-sec.com, and approve the requested `Mail.Read` permission.

- [ ] **Step 3: Confirm the script completes without error**

Check `receipt_saver.log` for a `── Account: ofeks@sternum-sec.com (sternum)` line with a candidate count and no `ERROR`/`Auth failed` entries.

If you see an AADSTS error mentioning admin consent, forward the exact error text plus the client ID (`53ade867-6e25-4987-a8f2-49238eef8100`) to your Azure AD admin — it will name the exact permission needing approval.

- [ ] **Step 4: Confirm the payslip email (2026-07-09) was picked up correctly**

Check for a new folder:
`C:\Users\ofeks\OneDrive\Ofek\Work\Sternum\משכורות\2026_07_09 - משכורת - תלוש שכר לחודש יוני 2026 - sternum\`

containing the original payslip PDF attachment (`אופק שמואל 6.26.pdf`) and `email.pdf`.

- [ ] **Step 5: Confirm token persistence (silent refresh on second run)**

Run: `python receipt_saver.py` again immediately.
Expected: no device-code prompt this time (token served from `token_sternum.json` cache), and the payslip email is *not* reprocessed (already in `processed_ids.json`).

---

### Task 7: Update `DOCUMENTATION.md`

**Files:**
- Modify: `DOCUMENTATION.md`

- [ ] **Step 1: Update the Overview line**

Replace:
> Receipt Saver is an automated Python-based system that runs on Windows startup and scans three Gmail accounts for receipt and invoice emails.

with:
> Receipt Saver is an automated Python-based system that runs on Windows startup and scans four mailboxes — three Gmail accounts and one Microsoft 365 account — for receipt and invoice emails.

- [ ] **Step 2: Add the account label and folder example**

In "Account labels", add:
```
- `sternum` → ofeks@sternum-sec.com (Microsoft 365)
```

In "Examples", add:
```
2026_07_09 - משכורת - תלוש שכר לחודש יוני 2026 - sternum
```

- [ ] **Step 3: Update the Scripts Folder table**

Add rows:
```
| `gmail_provider.py` | Gmail-specific auth/listing/parsing (Gmail API) |
| `outlook_provider.py` | Microsoft 365-specific auth/listing/parsing (Microsoft Graph API, MSAL device-code flow) |
| `credentials_sternum.json` | Azure AD app client ID + tenant ID for sternum account |
| `token_sternum.json` | Auto-refreshing MSAL token cache for sternum |
```

- [ ] **Step 4: Add the Sternum payslip rule to the Custom Rules table**

```
| `billing@sternum-sec.com` | — (matched via body) | משכורת | תלוש שכר לחודש X (regex from body) | — | Ofek\Work\Sternum\משכורות |
```

- [ ] **Step 5: Update the Dependencies table**

Add:
```
| `msal` | Microsoft Graph OAuth (device-code flow) for the sternum account |
```
Update the install line to include `msal`.

- [ ] **Step 6: Add a Troubleshooting row for the sternum device-code flow**

```
| Sternum auth error / admin-consent needed | Check `receipt_saver.log` for the AADSTS error text; forward it plus the client ID to the tenant admin if it names a missing consent |
```

- [ ] **Step 7: Commit**

```bash
git add DOCUMENTATION.md
git commit -m "Document the Microsoft 365 provider and sternum account"
```

---

## Self-review notes

- **Spec coverage:** Azure app registration (Task 4 references it, done manually already), provider abstraction (Tasks 1-3), Graph listing/filtering (Task 3), attachments/body handling (Task 3), edge cases — iCount link (Task 2 step 6, Task 3 `outlook_link`), auth failure notification (unchanged, already provider-agnostic in `main()`) — all covered. Day-one custom rule covered in Task 5. Testing plan (behavior-preserving Gmail check, live sternum verification) covered in Task 2 step 12 and Task 6.
- **No placeholders:** every step has complete, runnable code.
- **Type/name consistency checked:** `NormalizedMessage` keys (`id`, `sender`, `subject`, `date_raw`, `is_sent`, `body_text`, `body_html`, `first_attachment_name`, `attachments`, `link`) are identical across `gmail_provider.fetch_message`, `outlook_provider.fetch_message`, and every read site in `receipt_saver.process_message`.
