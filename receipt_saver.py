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
    {
        "label":       "sternum",
        "email":       "ofeks@sternum-sec.com",
        "provider":    "outlook",
        "creds_file":  SCRIPT_DIR / "credentials_sternum.json",
        "token_file":  SCRIPT_DIR / "token_sternum.json",
    },
]

PROVIDERS = {
    "gmail":   gmail_provider,
    "outlook": outlook_provider,
}

_LESSON_SUBJECT_RE = re.compile(r"סיכום שיעור יפנית\s+(\d{1,2})\.(\d{1,2})")

# ══════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════
class _ActionFormatter(logging.Formatter):
    """Show level name only for WARNING and above; omit it for INFO."""
    def format(self, record):
        if record.levelno >= logging.WARNING:
            record.msg = f"{record.levelname}  {record.msg}"
        return super().format(record)

_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_handler.setFormatter(_ActionFormatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
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

def unique_folder(base_dir: Path, folder_name: str) -> tuple:
    """Two unrelated emails can compute the same date/seller/product/label
    folder_name (e.g. two Hyp confirmations for the same gym visit). mkdir's
    exist_ok=True would silently nest/overwrite into the existing folder, so
    append a " (2)", " (3)", ... suffix instead. Returns (folder, folder_name)
    since callers log/report folder_name and it must reflect the real path."""
    folder = base_dir / folder_name
    if not folder.exists():
        return folder, folder_name
    n = 2
    while (base_dir / f"{folder_name} ({n})").exists():
        n += 1
    return base_dir / f"{folder_name} ({n})", f"{folder_name} ({n})"

def extract_display_name(sender: str) -> str:
    m = re.match(r'^"?([^"<\n]+)"?\s*<', sender)
    return sanitize(m.group(1).strip()) if m else sanitize(sender.split("@")[0])

def parse_date(date_raw: str) -> str:
    # Gmail's Date header is RFC 2822; Microsoft Graph's receivedDateTime is ISO 8601.
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

def parse_lesson_folder(subject: str, email_date: str) -> str | None:
    """Return YYYY_MM_DD folder name from lesson subject (e.g. 'סיכום שיעור יפנית 1.6').
    Year is taken from the email send date so this works across year boundaries."""
    m = _LESSON_SUBJECT_RE.search(subject)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(email_date[:4])  # "2026_05_18" -> 2026
    try:
        return datetime.date(year, month, day).strftime("%Y_%m_%d")
    except ValueError:
        return None

def sender_contains(sender: str, *fragments: str) -> bool:
    return any(f in sender.lower() for f in fragments)

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
    (lambda s, sub: sender_contains(s, "wolt.com"),         "Wolt",               _wolt_product,                       "Wolt"),
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

def _log_saved(action: str, folder_name: str, sender: str, folder: Path, files: list):
    lines = [
        f"{action:<10} {folder_name}",
        f"           FROM  {sender}",
        f"           TO    {folder}",
    ]
    for f in files:
        lines.append(f"           FILE  {f}")
    log.info("\n".join(lines))

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

def save_email_pdf(body_html: str, folder: Path,
                   subject: str, sender: str, date_str: str):
    """Render the email HTML to email.pdf inside the folder. Returns saved path or None."""
    if not _WEASYPRINT_OK:
        log.warning("weasyprint not available — skipping email.pdf")
        return None
    try:
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
        return dest
    except Exception as e:
        log.warning(f"email.pdf failed: {e}")
        return None

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

def match_custom(sender: str, subject: str, body: str = ""):
    # Normalize whitespace: HTML-to-text conversion (e.g. Outlook's Graph API)
    # can leave non-breaking spaces and irregular line wraps that would
    # otherwise silently defeat match_body_contains/product_body_regex.
    body = re.sub(r"[\s\xa0]+", " ", body)
    for rule in load_custom_rules():
        sender_frag   = rule.get("match_sender_contains", "")
        subject_frag  = rule.get("match_subject_contains") or ""
        exclude_frag  = rule.get("exclude_subject_contains") or ""
        body_frag     = rule.get("match_body_contains") or ""
        sender_ok   = sender_frag.lower()  in sender.lower()  if sender_frag  else True
        subject_ok  = subject_frag.lower() in subject.lower() if subject_frag else True
        excluded    = exclude_frag.lower() in subject.lower() if exclude_frag else False
        body_ok     = body_frag in body                        if body_frag   else True
        if sender_ok and subject_ok and not excluded and body_ok:
            if rule.get("exclude"):
                return "__exclude__", None, None, None
            base_dir = Path(rule["base_dir"]) if rule.get("base_dir") else None
            product  = rule["product"]
            body_regex = rule.get("product_body_regex") or ""
            if body_regex and body:
                m = re.search(body_regex, body)
                if m:
                    product = sanitize(m.group(1).strip())
            return rule["seller"], product, rule.get("category"), base_dir
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
        folder, folder_name = unique_folder(base_dir, folder_name)
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
        folder, folder_name = unique_folder(base_dir, folder_name)
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
        folder, folder_name = unique_folder(base_dir, folder_name)
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
