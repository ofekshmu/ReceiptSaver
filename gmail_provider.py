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
