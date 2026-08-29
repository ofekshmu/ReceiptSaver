"""
categories.py
-------------
A *category* is a named bundle that both **matches** incoming mail and defines
where matched receipts are filed. It replaces the flat per-sender entries in
`custom_rules.json` (see `migrate_rules_to_categories.py`).

Shape of one category (`categories.json` is a list of these):

    {
      "id":        "electricity",          # stable slug
      "name":      "חשמל",                 # display name
      "seller":    "חברת חשמל לישראל",     # default seller for the folder name
      "product":   "חשבונית חשמל",         # default product
      "base_dir":  null,                   # route root; null => קבלות
      "subfolder": "חשבנות/חשמל",          # folder under the route (old `category` string)
      "exclude":   false,                  # true => matched mail is dropped, not filed
      "match": [                           # OR-list; an entry matches if ALL its keys hold
        { "sender_contains": "iec.co.il" },
        { "sender_contains": "electra-power.co.il",
          "subject_contains": "...", "exclude_subject_contains": "...",
          "body_contains": "...", "product_body_regex": "..." }
      ]
    }

`match_category()` is a drop-in for `receipt_saver.match_custom()` — it returns
the same 4-tuple `(seller, product, subfolder, base_dir)` (or the sentinel
`("__exclude__", None, None, None)`), or `None` when nothing matches.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CATEGORIES_FILE = SCRIPT_DIR / "categories.json"

EXCLUDE = "__exclude__"

# keys allowed on a single `match[]` entry
MATCH_KEYS = ("sender_contains", "subject_contains", "exclude_subject_contains",
              "body_contains", "product_body_regex")


def _sanitize(s: str) -> str:
    """Folder-safe string. Lazily borrows receipt_saver.sanitize so this module
    stays import-light (and free of a circular import) when it's only matching."""
    try:
        from receipt_saver import sanitize
        return sanitize(s)
    except Exception:
        return re.sub(r'[<>:"/\\|?*\n\r\t]', " ", str(s or "")).strip()


def load_categories(path: Path = None) -> list:
    p = Path(path or CATEGORIES_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_categories(categories: list, path: Path = None) -> None:
    p = Path(path or CATEGORIES_FILE)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(categories, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def slugify(name: str, taken: set = None) -> str:
    """A short ascii id from a (possibly Hebrew) name; de-duped against `taken`."""
    norm = unicodedata.normalize("NFKD", str(name or ""))
    ascii_ = norm.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if not base:
        base = "cat-" + re.sub(r"\W+", "", str(name or ""))[:12].lower()
    base = base or "category"
    taken = taken or set()
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _entry_matches(m: dict, sender: str, subject: str, body_norm: str) -> bool:
    sf = m.get("sender_contains") or ""
    jf = m.get("subject_contains") or ""
    xf = m.get("exclude_subject_contains") or ""
    bf = m.get("body_contains") or ""
    if sf and sf.lower() not in (sender or "").lower():
        return False
    if jf and jf.lower() not in (subject or "").lower():
        return False
    if xf and xf.lower() in (subject or "").lower():
        return False
    if bf and bf not in body_norm:          # body match is case-sensitive (as before)
        return False
    return True


def match_category(sender: str, subject: str = "", body: str = "",
                   categories: list = None):
    """First category with a matching `match[]` entry wins. Returns
    `(seller, product, subfolder, base_dir)` — `base_dir` a Path or None —
    or `(EXCLUDE, None, None, None)` for an exclude category, or None."""
    cats = categories if categories is not None else load_categories()
    body_norm = re.sub(r"[\s\xa0]+", " ", body or "")
    for cat in cats:
        for m in cat.get("match", []):
            if not _entry_matches(m, sender, subject, body_norm):
                continue
            if cat.get("exclude"):
                return EXCLUDE, None, None, None
            product = cat.get("product")
            rx = m.get("product_body_regex") or ""
            if rx and body_norm:
                hit = re.search(rx, body_norm)
                if hit:
                    product = _sanitize(hit.group(1).strip())
            base_dir = Path(cat["base_dir"]) if cat.get("base_dir") else None
            return cat.get("seller"), product, cat.get("subfolder"), base_dir
    return None


def to_legacy_rules(categories: list = None) -> list:
    """Flatten categories back into the old `custom_rules.json` dict shape — one
    rule per `match[]` entry, category order then entry order preserved. Lets
    `receipt_saver.match_custom()` and the providers' query builders keep working
    unchanged (they iterate this list; first match wins, identically)."""
    cats = categories if categories is not None else load_categories()
    rules = []
    for cat in cats:
        for m in cat.get("match", []):
            rule = {}
            if m.get("sender_contains"):
                rule["match_sender_contains"] = m["sender_contains"]
            if m.get("subject_contains"):
                rule["match_subject_contains"] = m["subject_contains"]
            if m.get("exclude_subject_contains"):
                rule["exclude_subject_contains"] = m["exclude_subject_contains"]
            if m.get("body_contains"):
                rule["match_body_contains"] = m["body_contains"]
            if m.get("product_body_regex"):
                rule["product_body_regex"] = m["product_body_regex"]
            if cat.get("exclude"):
                rule["exclude"] = True
            else:
                rule["seller"] = cat.get("seller")
                rule["product"] = cat.get("product")
                rule["category"] = cat.get("subfolder")
                if cat.get("base_dir"):
                    rule["base_dir"] = cat["base_dir"]
            rules.append(rule)
    return rules


def sender_fragments(categories: list = None) -> list:
    """Every non-empty `sender_contains` across all non-exclude categories —
    used to build the mailbox search query (was: custom-rule senders)."""
    cats = categories if categories is not None else load_categories()
    out = []
    for cat in cats:
        if cat.get("exclude"):
            continue
        for m in cat.get("match", []):
            frag = (m.get("sender_contains") or "").strip()
            if frag and frag not in out:
                out.append(frag)
    return out
