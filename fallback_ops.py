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
    core = core.split(".")[0]
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
