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
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _outlook_link(msg_id: str) -> str:
    return f"https://outlook.office.com/mail/inbox/id/{msg_id}"


def fetch_message(service, msg_id: str, account: dict) -> dict:
    access_token = service["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(
        f"{GRAPH_BASE}/me/messages/{msg_id}"
        f"?$select=id,subject,from,receivedDateTime,body,hasAttachments"
        f"&$expand=attachments($select=name)",
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

    # The main GET above returns body content in whatever contentType the
    # message natively has (almost always "html" for real senders), so we
    # can't rely on it for plain text. Do a second lightweight GET with the
    # Prefer header to reliably force Graph to return plain text regardless
    # of the message's native format.
    text_resp = requests.get(
        f"{GRAPH_BASE}/me/messages/{msg_id}?$select=body",
        headers={**headers, "Prefer": 'outlook.body-content-type="text"'},
        timeout=15,
    )
    text_resp.raise_for_status()
    body_text = ((text_resp.json().get("body") or {}).get("content", "")) or ""

    if not body_html and body_text:
        body_html = f"<pre style='font-family:Arial,sans-serif;white-space:pre-wrap'>{body_text}</pre>"

    attachment_names = [a["name"] for a in m.get("attachments", []) if a.get("name")]
    first_attachment_name = attachment_names[0] if attachment_names else ""

    return {
        "id": msg_id,
        "sender": sender,
        "subject": m.get("subject", "(no subject)") or "(no subject)",
        "date_raw": m.get("receivedDateTime", ""),
        "is_sent": False,  # only the Inbox folder is queried; Sent items never appear here
        "body_text": body_text,
        "body_html": body_html,
        "first_attachment_name": first_attachment_name,
        "attachments": lambda: _fetch_attachments(access_token, msg_id),
        "link": _outlook_link(msg_id),
    }
