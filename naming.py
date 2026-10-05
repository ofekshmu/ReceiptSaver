"""
naming.py
---------
The app's default seller/product guess for a mail, from the sender and subject
only (no body, no network, no AI). Shared by the scan engine (a category whose
seller/product is unset or whose extraction misses) and the Fallbacks form's
prefill, so the default you see in the form is what a future mail would get.
Import-light on purpose: no dependency on receipt_saver.
"""

import re

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
DEFAULT_PRODUCT = "חשבונית"


def sanitize(name: str) -> str:
    """Folder-safe string (same rule as receipt_saver.sanitize)."""
    return re.sub(r'[\\/:*?"<>|]', "_", str(name or "")).strip(" .")


def registered_domain(sender: str) -> str:
    m = re.search(r"[\w.+-]+@([\w.-]+)", sender or "")
    host = (m.group(1) if m else sender or "").lower().strip()
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "org", "gov", "muni", "ac"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def seller_from_domain(domain: str) -> str:
    core = domain
    for suffix in (".co.il", ".org.il", ".com", ".net", ".co"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    core = core.split(".")[0]
    return "-".join(w.capitalize() for w in core.split("-")) or domain


def product_from_subject(subject: str) -> str:
    for needle, value in _PRODUCT_KEYWORDS:
        if needle in (subject or ""):
            return value
    return DEFAULT_PRODUCT


def display_name(sender: str) -> str:
    """'"Name" <a@b>' -> 'Name'; bare address -> its local part."""
    m = re.match(r'^"?([^"<\n]+)"?\s*<', sender or "")
    return sanitize(m.group(1).strip()) if m else sanitize((sender or "").split("@")[0])


def suggest_names(sender: str, subject: str) -> tuple:
    """(seller, product) the app proposes when nothing better is configured."""
    return seller_from_domain(registered_domain(sender)), product_from_subject(subject)
