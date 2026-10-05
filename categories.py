"""
categories.py
-------------
A *category* is a complete filing recipe: it **matches** incoming mail and says
**where** the receipt goes and **how** its seller/product are named.

Shape of one category (`categories.json` is a list of these):

    {
      "id":          "electricity",            # stable slug
      "name":        "חשמל",                   # display name
      "destination": "C:\\...\\קבלות\\חשבנות\\חשמל",   # full folder path; null => קבלות
      "seller":      {"mode": "fixed", "value": "חברת חשמל לישראל"},
      "product":     {"mode": "extract", "source": "subject",
                      "regex": "חשבון לתקופה (.+)", "fallback": "חשבונית חשמל"},
      "exclude":     false,                    # true => matched mail is dropped
      "match": [                               # OR-list; an entry matches if ALL its keys hold
        { "sender_contains": "iec.co.il" },
        { "sender_contains": "x.co.il", "subject_contains": "...",
          "exclude_subject_contains": "...", "body_contains": "..." }
      ]
    }

A seller/product *spec* is one of:
  * ``{"mode": "fixed", "value": str}``
  * ``{"mode": "extract", "source": "subject"|"body"|"sender_name", "regex": str,
     "fallback"?: str}`` — first capture group (or the whole match), sanitized
  * ``null`` — use the app's suggestion (`naming.suggest_names`)

Resolution: fixed value → extraction → extraction fallback → app suggestion.
First category with a matching `match[]` entry wins.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

import naming

SCRIPT_DIR = Path(__file__).parent
CATEGORIES_FILE = SCRIPT_DIR / "categories.json"
RECEIPTS_DIR = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")

EXCLUDE = "__exclude__"
EXCLUDE_CATEGORY_ID = "excluded"

MATCH_COND_KEYS = ("sender_contains", "subject_contains", "exclude_subject_contains",
                   "body_contains")
SOURCES = ("subject", "body", "sender_name")


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# seller / product specs
# ---------------------------------------------------------------------------

def normalize_body(body: str) -> str:
    # HTML-to-text conversion (e.g. Outlook's Graph API) can leave non-breaking
    # spaces and irregular line wraps that would otherwise defeat body matching.
    return re.sub(r"[\s\xa0]+", " ", body or "")


def _source_text(source: str, sender: str, subject: str, body: str) -> str:
    if source == "subject":
        return subject or ""
    if source == "body":
        return normalize_body(body)
    if source == "sender_name":
        return naming.display_name(sender)
    raise ValueError(f"unknown source {source!r}")


def extract(source: str, regex: str, sender: str, subject: str, body: str):
    """Run `regex` over the chosen part of the mail. Returns the sanitized first
    capture group (or whole match when the regex has no group), or None on a
    miss. Raises ValueError on an invalid regex or unknown source."""
    text = _source_text(source, sender, subject, body)
    try:
        rx = re.compile(regex)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}") from None
    hit = rx.search(text)
    if not hit:
        return None
    value = hit.group(1) if rx.groups else hit.group(0)
    value = naming.sanitize((value or "").strip())
    return value or None


def resolve_spec(spec, sender: str, subject: str, body: str, suggestion: str) -> str:
    """The value a spec yields for one mail; never raises, never empty."""
    if spec and spec.get("mode") == "fixed" and (spec.get("value") or "").strip():
        return spec["value"].strip()
    if spec and spec.get("mode") == "extract":
        try:
            hit = extract(spec.get("source"), spec.get("regex") or "", sender, subject, body)
        except ValueError:
            hit = None
        if hit:
            return hit
        if (spec.get("fallback") or "").strip():
            return spec["fallback"].strip()
    return suggestion


def validate_spec(spec):
    """Normalized copy of a seller/product spec, or None for 'use suggestion'.
    Raises ValueError for anything malformed (bad mode/source, invalid regex)."""
    if not spec:
        return None
    mode = spec.get("mode")
    if mode == "fixed":
        value = (spec.get("value") or "").strip()
        return {"mode": "fixed", "value": value} if value else None
    if mode == "extract":
        source, regex = spec.get("source"), spec.get("regex") or ""
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        if not regex:
            raise ValueError("extraction needs a regex")
        try:
            re.compile(regex)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from None
        out = {"mode": "extract", "source": source, "regex": regex}
        if (spec.get("fallback") or "").strip():
            out["fallback"] = spec["fallback"].strip()
        return out
    raise ValueError(f"unknown mode {mode!r}")


def resolve_names(cat: dict, sender: str, subject: str, body: str = "") -> tuple:
    """(seller, product) a category yields for one mail."""
    sug_seller, sug_product = naming.suggest_names(sender, subject)
    return (resolve_spec(cat.get("seller"), sender, subject, body, sug_seller),
            resolve_spec(cat.get("product"), sender, subject, body, sug_product))


def needs_body(cat: dict) -> bool:
    return any((cat.get(k) or {}).get("source") == "body" for k in ("seller", "product"))


def destination_of(cat: dict) -> Path:
    return Path(cat["destination"]) if cat.get("destination") else RECEIPTS_DIR


# ---------------------------------------------------------------------------
# mutation helpers — each takes the in-memory list and returns a bool (or the
# new dict); callers persist with save_categories().
# ---------------------------------------------------------------------------

def find(categories: list, category_id: str) -> dict:
    for c in categories:
        if c.get("id") == category_id:
            return c
    return None


def new_category(name: str, *, destination=None, seller=None, product=None,
                 exclude=False, categories: list = None) -> dict:
    taken = {c.get("id") for c in (categories or [])}
    return {
        "id": slugify(name, taken),
        "name": (name or "").strip() or "category",
        "destination": (str(destination).strip() if destination else None) or None,
        "seller": validate_spec(seller),
        "product": validate_spec(product),
        "exclude": bool(exclude),
        "match": [],
    }


def clean_match_entry(entry: dict) -> dict:
    """Keep only known condition keys with real values."""
    out = {}
    for k in MATCH_COND_KEYS:
        v = (entry or {}).get(k)
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, "", []):
            out[k] = v
    return out


def add_match(categories: list, category_id: str, entry: dict) -> bool:
    cat = find(categories, category_id)
    if cat is None:
        return False
    cleaned = clean_match_entry(entry)
    if not cleaned.get("sender_contains") and not cleaned.get("subject_contains"):
        return False
    cat.setdefault("match", [])
    if cleaned not in cat["match"]:
        cat["match"].append(cleaned)
    return True


def remove_match(categories: list, category_id: str, index: int) -> bool:
    cat = find(categories, category_id)
    if cat is None or not (0 <= index < len(cat.get("match", []))):
        return False
    cat["match"].pop(index)
    return True


def update_category(categories: list, category_id: str, patch: dict) -> bool:
    cat = find(categories, category_id)
    if cat is None:
        return False
    staged = {}
    if "name" in patch:
        staged["name"] = (patch["name"] or "").strip() or cat.get("name")
    if "destination" in patch:
        staged["destination"] = (str(patch["destination"]).strip()
                                 if patch["destination"] else None) or None
    for k in ("seller", "product"):
        if k in patch:
            staged[k] = validate_spec(patch[k])      # may raise; nothing applied yet
    if "exclude" in patch:
        staged["exclude"] = bool(patch["exclude"])
    cat.update(staged)
    return True


def delete_category(categories: list, category_id: str) -> bool:
    n = len(categories)
    categories[:] = [c for c in categories if c.get("id") != category_id]
    return len(categories) != n


def merge_categories(categories: list, src_id: str, dst_id: str) -> bool:
    """Move src's match entries into dst, then drop src. dst's destination and
    naming apply to the moved senders from then on."""
    if src_id == dst_id:
        return False
    src, dst = find(categories, src_id), find(categories, dst_id)
    if src is None or dst is None:
        return False
    for m in src.get("match", []):
        cleaned = clean_match_entry(m)
        if cleaned and cleaned not in dst.setdefault("match", []):
            dst["match"].append(cleaned)
    return delete_category(categories, src_id)


def exclude_category(categories: list) -> dict:
    """The shared bucket for 'not a receipt' senders; created on first use."""
    cat = find(categories, EXCLUDE_CATEGORY_ID)
    if cat is None:
        cat = {"id": EXCLUDE_CATEGORY_ID, "name": "(excluded)", "destination": None,
               "seller": None, "product": None, "exclude": True, "match": []}
        categories.append(cat)
    return cat


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

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


def find_match(sender: str, subject: str = "", body: str = "",
               categories: list = None):
    """The first category with a matching `match[]` entry, or None."""
    cats = categories if categories is not None else load_categories()
    body_norm = normalize_body(body)
    for cat in cats:
        for m in cat.get("match", []):
            if _entry_matches(m, sender, subject, body_norm):
                return cat
    return None


def match_category(sender: str, subject: str = "", body: str = "",
                   categories: list = None):
    """`(seller, product, destination)` for the first matching category —
    `destination` a Path — or `(EXCLUDE, None, None)` for an exclude category,
    or None when nothing matches."""
    cat = find_match(sender, subject, body, categories)
    if cat is None:
        return None
    if cat.get("exclude"):
        return EXCLUDE, None, None
    seller, product = resolve_names(cat, sender, subject, body)
    return seller, product, destination_of(cat)


def query_terms(categories: list = None) -> list:
    """One `{sender_contains, exclude_subject_contains}` per distinct match entry
    (exclude categories included) — what the providers' mailbox searches use."""
    cats = categories if categories is not None else load_categories()
    out = []
    for cat in cats:
        for m in cat.get("match", []):
            t = {"sender_contains": m.get("sender_contains") or "",
                 "exclude_subject_contains": m.get("exclude_subject_contains") or ""}
            if t not in out:
                out.append(t)
    return out
