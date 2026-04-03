"""
receipt_saver.py
----------------
Runs on Windows startup via Task Scheduler.
Scans three Gmail accounts and saves receipt attachments.

Folder format: YYYY-MM-DD - Seller - Product - [account]
  account labels: ofek | family | yuval

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
                google-api-python-client requests plyer weasyprint
"""

import re
import base64
import json
import logging
import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import requests

try:
    from plyer import notification as _plyer_notification
    _PLYER_OK = True
except ImportError:
    _PLYER_OK = False

try:
    from weasyprint import HTML as _WeasyprintHTML
    _WEASYPRINT_OK = True
except ImportError:
    _WEASYPRINT_OK = False

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
RECEIPTS_DIR        = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR          = RECEIPTS_DIR / "_לטיפול ידני"
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
        "creds_file":  SCRIPT_DIR / "credentials_ofek.json",
        "token_file":  SCRIPT_DIR / "token_ofek.json",
    },
    {
        "label":       "family",
        "email":       "shmuelfamily21@gmail.com",
        "creds_file":  SCRIPT_DIR / "credentials_family.json",
        "token_file":  SCRIPT_DIR / "token_family.json",
    },
    {
        "label":       "yuval",
        "email":       "yuvalritsker@gmail.com",
        "creds_file":  SCRIPT_DIR / "credentials_yuval.json",
        "token_file":  SCRIPT_DIR / "token_yuval.json",
    },
]

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

GMAIL_QUERY = (
    '-in:sent has:attachment newer_than:60d '
    '(subject:receipt OR subject:invoice OR subject:קבלה OR subject:חשבונית '
    'OR subject:אישור OR subject:הזמנה OR subject:purchase OR subject:payment)'
)

# ══════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s", encoding="utf-8",
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# DESKTOP NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════

APP_NAME = "Receipt Saver"

def notify(title: str, message: str, timeout: int = 6):
    if not _PLYER_OK:
        return
    try:
        _plyer_notification.notify(
            app_name=APP_NAME,
            title=title,
            message=message,
            timeout=timeout,
        )
    except Exception as e:
        log.warning(f"Notification failed: {e}")

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip(" .")

def extract_display_name(sender: str) -> str:
    m = re.match(r'^"?([^"<\n]+)"?\s*<', sender)
    return sanitize(m.group(1).strip()) if m else sanitize(sender.split("@")[0])

def parse_date(date_raw: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_raw).strftime("%Y_%m_%d")
    except Exception:
        return datetime.date.today().strftime("%Y_%m_%d")

def sender_contains(sender: str, *fragments: str) -> bool:
    return any(f in sender.lower() for f in fragments)

def first_attachment_name(payload: dict) -> str:
    for part in payload.get("parts", []):
        if part.get("filename"):
            return part["filename"]
    return ""

# ══════════════════════════════════════════════════════════════════════════
# HARDCODED KNOWN RULES
# ══════════════════════════════════════════════════════════════════════════

def _wolt_product(subject, att):
    if att:
        return sanitize(re.split(r"_", att)[0].strip())
    return "משלוח"

def _cardcom_seller(sender, subject):
    m = re.search(r"מ(.+?)(?:\s*[-–]\s*עבור|$)", subject)
    return sanitize(m.group(1).strip()) if m else extract_display_name(sender)

def _cardcom_product(subject, att):
    m = re.search(r"(חשבונית[^\d]*)", subject)
    return sanitize(m.group(1).strip()) if m else "חשבונית"

def _israelpost_product(subject, att):
    cleaned = re.sub(r"דואר ישראל[\s\-–]*", "", subject, flags=re.IGNORECASE)
    return sanitize(re.sub(r"\s{2,}", " ", cleaned).strip(" -–")) or "הזמנה"

def _stripe_seller(sender, subject):
    m = re.search(r"receipt from (.+?)(?:\s#|\s*$)", subject, re.IGNORECASE)
    return sanitize(m.group(1).strip()) if m else extract_display_name(sender)

KNOWN_RULES = [
    # (match_fn, seller, product_fn, category_or_None)
    (lambda s, sub: sender_contains(s, "wolt.com"),         "Wolt",               _wolt_product,                       None),
    (lambda s, sub: sender_contains(s, "ksp.co.il"),        "KSP",                lambda sub, att: "חשבונית וקבלה",   None),
    (lambda s, sub: sender_contains(s, "paneco.com"),       "פאנקו",              lambda sub, att: "הזמנה",            None),
    (lambda s, sub: sender_contains(s, "cellcominv.co.il"), "סלקום",              lambda sub, att: "חשבונית חודשית",  "חשבנות/אינטרנט"),
    (lambda s, sub: sender_contains(s, "yesplanet.co.il"),  "Yes Planet",         lambda sub, att: "כרטיסים",         None),
    (lambda s, sub: sender_contains(s, "mhc.org.il"),       "מדיטק",              lambda sub, att: "הזמנה",            None),
    (lambda s, sub: sender_contains(s, "israelpost.co.il"), "דואר ישראל",         _israelpost_product,                 None),
    (lambda s, sub: sender_contains(s, "cardcom.co.il"),    _cardcom_seller,      _cardcom_product,                    None),
    (lambda s, sub: sender_contains(s, "flymoney.com"),     "FlyMoney",           lambda sub, att: 'מט"ח',             None),
    (lambda s, sub: sender_contains(s, "fattal.co.il") or "nyx" in sub.lower(),
                                                             extract_display_name, lambda sub, att: "חשבונית",         None),
    (lambda s, sub: sender_contains(s, "stripe.com"),       _stripe_seller,       lambda sub, att: "מנוי",             None),
]

def match_hardcoded(sender: str, subject: str):
    for match_fn, seller_val, product_fn, category in KNOWN_RULES:
        if match_fn(sender, subject):
            seller = seller_val(sender, subject) if callable(seller_val) else seller_val
            return seller, product_fn, category
    return None

# ══════════════════════════════════════════════════════════════════════════
# ICOUNT SPECIAL HANDLING
# Emails from iCount contain a PDF link inside the body — no useful
# attachments. We save the folder, skip attachments, and create a
# TickTick task with a direct Gmail link so the user can open and
# download the PDF manually.
# ══════════════════════════════════════════════════════════════════════════

def is_icount(sender: str, subject: str) -> bool:
    return "icount.co.il" in sender.lower()

def gmail_link(msg_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"

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
        "priority": 3,
        "timeZone": "Asia/Jerusalem",
    }
    resp = requests.post(
        "https://api.ticktick.com/open/v1/task",
        headers={
            "Authorization": f"Bearer {token_data.get('access_token', '')}",
            "Content-Type": "application/json",
        },
        json=task, timeout=10,
    )
    if resp.ok:
        log.info("  ✓ iCount TickTick task created")
    else:
        log.warning(f"  ⚠ TickTick failed: {resp.status_code}")

# ══════════════════════════════════════════════════════════════════════════
# EMAIL → PDF
# ══════════════════════════════════════════════════════════════════════════

def get_body_html(payload: dict) -> str:
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

def save_email_pdf(payload: dict, folder: Path,
                   subject: str, sender: str, date_str: str):
    """Render the email HTML to email.pdf inside the folder."""
    if not _WEASYPRINT_OK:
        log.warning("weasyprint not installed — skipping email PDF")
        return
    try:
        body_html = get_body_html(payload)
        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; margin: 30px; direction: auto; }}
  .header {{ background: #f5f5f5; padding: 12px; border-radius: 4px;
             margin-bottom: 20px; font-size: 13px; line-height: 1.6; }}
</style></head><body>
<div class="header">
  <b>מאת:</b> {sender}<br>
  <b>תאריך:</b> {date_str}<br>
  <b>נושא:</b> {subject}
</div>
{body_html}
</body></html>"""
        dest = folder / "email.pdf"
        _WeasyprintHTML(string=full_html).write_pdf(str(dest))
        log.info("    ✓ email.pdf")
    except Exception as e:
        log.warning(f"    ⚠ email PDF failed: {e}")

# ══════════════════════════════════════════════════════════════════════════
# CUSTOM RULES  (managed via chat with Claude)
# ══════════════════════════════════════════════════════════════════════════

def load_custom_rules() -> list:
    if CUSTOM_RULES_FILE.exists():
        try:
            return json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not load custom_rules.json: {e}")
    return []

def match_custom(sender: str, subject: str):
    for rule in load_custom_rules():
        sender_frag  = rule.get("match_sender_contains", "")
        subject_frag = rule.get("match_subject_contains") or ""
        sender_ok  = sender_frag.lower()  in sender.lower()  if sender_frag  else True
        subject_ok = subject_frag.lower() in subject.lower() if subject_frag else True
        if sender_ok and subject_ok:
            return rule["seller"], rule["product"], rule.get("category")
    return None

# ══════════════════════════════════════════════════════════════════════════
# FALLBACK LOG
# ══════════════════════════════════════════════════════════════════════════

def load_fallback_log() -> list:
    if FALLBACK_LOG_FILE.exists():
        try:
            return json.loads(FALLBACK_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def append_fallback_log(entry: dict):
    log_entries  = load_fallback_log()
    existing_ids = {e.get("message_id") for e in log_entries}
    if entry["message_id"] not in existing_ids:
        log_entries.append(entry)
        FALLBACK_LOG_FILE.write_text(
            json.dumps(log_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

# ══════════════════════════════════════════════════════════════════════════
# PROCESSED IDS
# ══════════════════════════════════════════════════════════════════════════

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")))
    return set()

def save_processed(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)), encoding="utf-8")

# ══════════════════════════════════════════════════════════════════════════
# GMAIL
# ══════════════════════════════════════════════════════════════════════════

def get_gmail_service(account: dict):
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
            # Hint the browser to use the right account
            creds = flow.run_local_server(
                port=0,
                login_hint=account["email"],
            )
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)

def save_attachments(service, msg_id: str, payload: dict, folder: Path) -> list:
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
                dest = folder / sanitize(filename)
                dest.write_bytes(data)
                log.info(f"    ✓ {filename}")
                saved.append(filename)
            if part.get("parts"):
                walk(part["parts"])
    walk(payload.get("parts", [payload]))
    return saved

# ══════════════════════════════════════════════════════════════════════════
# TICKTICK
# ══════════════════════════════════════════════════════════════════════════

def create_ticktick_task(folder_name: str, folder_path: Path, account_label: str):
    if not TICKTICK_TOKEN_FILE.exists():
        log.warning("ticktick_token.json missing — skipping TickTick task")
        return
    token_data = json.loads(TICKTICK_TOKEN_FILE.read_text(encoding="utf-8"))
    task = {
        "title": f"טפל בקבלה: {folder_name}",
        "content": (
            f"חשבון: {account_label}\n"
            f"תיקייה: {folder_path}\n\n"
            f"פתח Claude ואמור 'handle my fallback emails' כדי לטפל בה."
        ),
        "priority": 1,
        "timeZone": "Asia/Jerusalem",
    }
    resp = requests.post(
        "https://api.ticktick.com/open/v1/task",
        headers={
            "Authorization": f"Bearer {token_data.get('access_token', '')}",
            "Content-Type": "application/json",
        },
        json=task, timeout=10,
    )
    if resp.ok:
        log.info("  ✓ TickTick task created")
    else:
        log.warning(f"  ⚠ TickTick failed: {resp.status_code}")

# ══════════════════════════════════════════════════════════════════════════
# PROCESS ONE EMAIL
# ══════════════════════════════════════════════════════════════════════════

def process_message(service, msg_id: str, account: dict) -> dict:
    label = account["label"]

    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    if "SENT" in msg.get("labelIds", []):
        return {"status": "skipped"}

    headers   = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    subject   = headers.get("Subject", "(no subject)")
    sender    = headers.get("From", "")
    date_str  = parse_date(headers.get("Date", ""))
    first_att = first_attachment_name(msg["payload"])

    # ── iCount special case ────────────────────────────────────────────────
    # PDF is inside a link in the email body — skip attachments (just logo),
    # save email as PDF, create TickTick task with direct Gmail link.
    if is_icount(sender, subject):
        # Extract seller from subject: "חשבונית מס קבלה 7721 מאת יפנולוגי"
        m = re.search(r"מאת\s+(.+?)$", subject)
        seller  = sanitize(m.group(1).strip()) if m else "iCount"
        product = "חשבונית מס קבלה"
        # Check custom rules for a category override
        custom_match = match_custom(sender, subject)
        category = custom_match[2] if custom_match else None
        base_dir    = RECEIPTS_DIR / category if category else RECEIPTS_DIR
        folder_name = f"{date_str} - {seller} - {product} - {label}"
        folder      = base_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        log.info(f"[ICOUNT]   {folder_name}")
        save_email_pdf(msg["payload"], folder, subject, sender, date_str)
        create_icount_ticktick_task(folder_name, folder, label, msg_id, subject)
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
        log.info(f"[KNOWN]    {folder_name}")
        save_attachments(service, msg_id, msg["payload"], folder)
        save_email_pdf(msg["payload"], folder, subject, sender, date_str)
        return {"status": "saved", "folder_name": folder_name}

    # ── Step 2: custom rules ───────────────────────────────────────────────
    custom = match_custom(sender, subject)
    if custom:
        seller, product, category = custom
        base_dir    = RECEIPTS_DIR / category if category else RECEIPTS_DIR
        folder_name = f"{date_str} - {sanitize(seller)} - {sanitize(product)} - {label}"
        folder      = base_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        log.info(f"[CUSTOM]   {folder_name}")
        save_attachments(service, msg_id, msg["payload"], folder)
        save_email_pdf(msg["payload"], folder, subject, sender, date_str)
        return {"status": "saved", "folder_name": folder_name}

    # ── Step 3: fallback ───────────────────────────────────────────────────
    sender_name   = extract_display_name(sender)
    subject_clean = sanitize(subject[:60])
    folder_name   = f"{date_str} - {sender_name} - {subject_clean} - {label}"
    folder        = MANUAL_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    log.info(f"[FALLBACK] {folder_name}")
    save_attachments(service, msg_id, msg["payload"], folder)
    save_email_pdf(msg["payload"], folder, subject, sender, date_str)
    create_ticktick_task(folder_name, folder, label)
    append_fallback_log({
        "message_id":    msg_id,
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

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    log.info("═" * 60)
    log.info(f"Receipt Saver started — {datetime.datetime.now():%Y-%m-%d %H:%M}")

    notify("Receipt Saver מופעל", "בודק תיבות דואר לקבלות חדשות...")

    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)

    processed      = load_processed()
    saved_folders  = []
    fallback_items = []

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
            userId="me", q=GMAIL_QUERY, maxResults=300
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

    save_processed(processed)

    # Summary notification
    if saved_folders:
        names = ", ".join(
            f.split(" - ")[1] if f.count(" - ") >= 1 else f
            for f in saved_folders
        )
        notify(f"📥 {len(saved_folders)} קבלות נשמרו", names[:200], timeout=8)
    elif not fallback_items:
        notify("Receipt Saver", "לא נמצאו קבלות חדשות.", timeout=4)

    log.info(f"Done — {len(saved_folders)} saved, {len(fallback_items)} fallback.\n")


if __name__ == "__main__":
    main()
