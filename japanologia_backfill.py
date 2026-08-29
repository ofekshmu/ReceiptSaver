"""
japanologia_backfill.py

One-time backfill script that searches ofek.shmuel1@gmail.com for emails
with subject matching "סיכום שיעור יפנית DD.MM" (since April 15, 2026),
parses the lesson date from the subject, and saves all attachments to:
  C:/Users/ofeks/OneDrive/Ofek/Japanese Lessons/Japanologia/YYYY_MM_DD/

Usage:
    python japanologia_backfill.py

Requirements:
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
"""

import os
import re
import base64
import logging
from pathlib import Path
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR      = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
CREDS_FILE      = SCRIPT_DIR / "credentials_ofek.json"
TOKEN_FILE      = SCRIPT_DIR / "token_ofek.json"

OUTPUT_BASE     = Path(r"C:\Users\ofeks\OneDrive\Ofek\Japanese Lessons\Japanologia")

# Search all emails since this date
SEARCH_AFTER    = "2026/04/15"

SCOPES          = ["https://www.googleapis.com/auth/gmail.readonly"]

# Matches subjects like "סיכום שיעור יפנית 1.6" or "סיכום שיעור יפנית 13.4"
SUBJECT_PATTERN = re.compile(r"סיכום שיעור יפנית\s+(\d{1,2})\.(\d{1,2})")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    """Authenticate with Gmail and return a service object.
    
    Uses existing token if valid, refreshes if expired, or opens
    a browser flow for first-time authorization.
    
    Returns:
        googleapiclient.discovery.Resource: Authenticated Gmail API service.
    """
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Gmail token...")
            creds.refresh(Request())
        else:
            log.info("Opening browser for Gmail authorization...")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


# ── Subject parsing ───────────────────────────────────────────────────────────

def parse_lesson_date(subject: str) -> str | None:
    """Extract the lesson date from the email subject and return as YYYY_MM_DD.

    Expects subject format: "סיכום שיעור יפנית D.M" or "DD.MM"
    Year is assumed to be 2026.

    Args:
        subject: The email subject string.

    Returns:
        Date string in YYYY_MM_DD format, or None if pattern not found.
    """
    match = SUBJECT_PATTERN.search(subject)
    if not match:
        return None

    day   = int(match.group(1))
    month = int(match.group(2))
    year  = 2026

    try:
        dt = datetime(year, month, day)
        return dt.strftime("%Y_%m_%d")
    except ValueError:
        log.warning(f"Invalid date in subject: day={day} month={month}")
        return None


# ── Attachment saving ─────────────────────────────────────────────────────────

def save_attachments(service, message_id: str, folder: Path) -> int:
    """Download and save all attachments from a Gmail message.

    Args:
        service: Authenticated Gmail API service.
        message_id: Gmail message ID.
        folder: Destination folder path (will be created if needed).

    Returns:
        Number of attachments saved.
    """
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    parts = msg.get("payload", {}).get("parts", [])
    saved = 0

    for part in parts:
        filename = part.get("filename", "")
        body     = part.get("body", {})

        if not filename:
            continue

        attachment_id = body.get("attachmentId")
        if not attachment_id:
            # Data may be inline
            data = body.get("data", "")
        else:
            att = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
            data = att.get("data", "")

        if not data:
            log.warning(f"  No data for attachment: {filename}")
            continue

        file_bytes = base64.urlsafe_b64decode(data)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / filename

        # Avoid overwriting — append a counter if needed
        if dest.exists():
            stem   = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = folder / f"{stem}_{counter}{suffix}"
                counter += 1

        dest.write_bytes(file_bytes)
        log.info(f"  Saved: {dest.name}")
        saved += 1

    return saved


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    """Run the one-time backfill.
    
    Searches Gmail for Japanese lesson summary emails since SEARCH_AFTER,
    parses the lesson date from each subject, and saves attachments to
    the appropriate dated folder under OUTPUT_BASE.
    """
    service = get_gmail_service()
    log.info("Gmail connected.")

    query = f'subject:"סיכום שיעור יפנית" after:{SEARCH_AFTER.replace("/", "/")}'
    log.info(f"Searching: {query}")

    results    = service.users().messages().list(userId="me", q=query).execute()
    messages   = results.get("messages", [])
    next_page  = results.get("nextPageToken")

    # Paginate to get all results
    while next_page:
        page      = service.users().messages().list(
            userId="me", q=query, pageToken=next_page
        ).execute()
        messages  += page.get("messages", [])
        next_page  = page.get("nextPageToken")

    log.info(f"Found {len(messages)} matching message(s).")

    total_saved  = 0
    skipped      = 0

    for msg_ref in messages:
        msg_id = msg_ref["id"]

        # Fetch headers only to read subject
        meta = service.users().messages().get(
            userId="me", id=msg_id, format="metadata",
            metadataHeaders=["Subject", "Date"]
        ).execute()

        headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "")
        date_hdr = headers.get("Date", "")

        log.info(f"Processing: {subject!r}  ({date_hdr})")

        folder_name = parse_lesson_date(subject)
        if not folder_name:
            log.warning(f"  Could not parse date from subject, skipping.")
            skipped += 1
            continue

        dest_folder = OUTPUT_BASE / folder_name
        n = save_attachments(service, msg_id, dest_folder)

        if n == 0:
            log.info("  No attachments found in this email.")
        else:
            total_saved += n

    log.info("─" * 60)
    log.info(f"Done. {total_saved} attachment(s) saved, {skipped} email(s) skipped.")


if __name__ == "__main__":
    main()
