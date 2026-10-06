"""
rules.py
--------
**Rules** identify a mail type — how it is matched and how its seller/product
are named — and **roots** are the (few) places receipts are filed into. Every
non-exclude rule points at one root. See
docs/superpowers/specs/2026-10-07-rules-and-roots-design.md.

`rules.json`:

    {
      "roots": [ {"id": "electricity", "name": "חשמל",
                  "folder": "C:\\...\\קבלות\\חשבנות\\חשמל", "color": "#d6efcf"} ],
      "rules": [ {"id": "iec", "name": "חברת חשמל לישראל", "root": "electricity",
                  "seller":  {"mode": "fixed", "value": "חברת חשמל לישראל"},
                  "product": {"mode": "extract", "source": "subject",
                              "regex": "חשבון לתקופה (.+)", "fallback": "חשבונית חשמל"},
                  "exclude": false,
                  "match": [ {"sender_contains": "iec.co.il"},
                             {"sender_contains": ["x.co.il", "billing"],
                              "subject_contains": [...], "exclude_subject_contains": [...],
                              "body_contains": [...], "exclude_body_contains": [...],
                              "attachments": "none" | "one" | "many"} ]} ]
    }

`match` is an OR-list of alternatives; an alternative matches when ALL its
conditions hold. A seller/product *spec* is fixed text, a regex extraction
(subject / body / sender name, optional fallback), or null (= the app's
suggestion, `naming.suggest_names`). First matching rule wins (file order).
Exclude rules have `root: null`.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

import naming

SCRIPT_DIR = Path(__file__).parent
RULES_FILE = SCRIPT_DIR / "rules.json"
RECEIPTS_DIR = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")

EXCLUDE = "__exclude__"
EXCLUDE_RULE_ID = "excluded"

# gentle pastel palette for roots (the UI shows each root in its colour)
PASTELS = ["#f9d5dc", "#fde0c8", "#fbefb8", "#d6efcf", "#cbece8",
           "#d3e3f8", "#e0d9f6", "#f3d6ec"]

# text conditions — each a string or a list of strings
LIST_KEYS = ("sender_contains", "subject_contains", "exclude_subject_contains",
             "body_contains", "exclude_body_contains")
MATCH_COND_KEYS = LIST_KEYS + ("attachments",)
ATTACHMENT_RULES = ("none", "one", "many")
SOURCES = ("subject", "body", "sender_name")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic",
              ".heif", ".tif", ".tiff", ".ico"}


def as_list(value) -> list:
    """A condition value (str | list | None) as a list of non-empty strings."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in items if str(v or "").strip()]


def count_documents(names) -> int:
    """Attachments that count for the `attachments` condition: everything but images."""
    return sum(1 for n in names or [] if os.path.splitext(str(n))[1].lower() not in _IMAGE_EXT)


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

def empty() -> dict:
    return {"roots": [], "rules": []}


def load(path: Path = None) -> dict:
    p = Path(path or RULES_FILE)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return empty()
    if not isinstance(data, dict):
        return empty()
    return {"roots": list(data.get("roots") or []), "rules": list(data.get("rules") or [])}


def save(data: dict, path: Path = None) -> None:
    p = Path(path or RULES_FILE)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"roots": data.get("roots", []), "rules": data.get("rules", [])},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def slugify(name: str, taken: set = None) -> str:
    """A short ascii id from a (possibly Hebrew) name; de-duped against `taken`."""
    norm = unicodedata.normalize("NFKD", str(name or ""))
    ascii_ = norm.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if not base:
        base = "r-" + re.sub(r"\W+", "", str(name or ""))[:12].lower()
    base = base or "item"
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


def resolve_names(rule: dict, sender: str, subject: str, body: str = "") -> tuple:
    """(seller, product) a rule yields for one mail."""
    sug_seller, sug_product = naming.suggest_names(sender, subject)
    return (resolve_spec(rule.get("seller"), sender, subject, body, sug_seller),
            resolve_spec(rule.get("product"), sender, subject, body, sug_product))


def needs_body(rule: dict) -> bool:
    return any((rule.get(k) or {}).get("source") == "body" for k in ("seller", "product"))


def destination_of(rule: dict, data: dict = None) -> Path:
    """The folder a rule files into: its root's folder (קבלות if the root is gone)."""
    root = find_root(data if data is not None else load(), rule.get("root"))
    return Path(root["folder"]) if root and root.get("folder") else RECEIPTS_DIR


# ---------------------------------------------------------------------------
# roots — helpers take the in-memory data dict; callers persist with save()
# ---------------------------------------------------------------------------

def find_root(data: dict, root_id: str) -> dict:
    for r in data.get("roots", []):
        if r.get("id") == root_id:
            return r
    return None


def _next_color(data: dict) -> str:
    used = [r.get("color") for r in data.get("roots", [])]
    return min(PASTELS, key=lambda c: (used.count(c), PASTELS.index(c)))


def new_root(name: str, folder: str, *, color: str = None, data: dict) -> dict:
    folder = (str(folder).strip() if folder else "")
    if not folder:
        raise ValueError("a root needs a folder")
    taken = {r.get("id") for r in data.get("roots", [])}
    return {"id": slugify(name or Path(folder).name, taken),
            "name": (name or "").strip() or Path(folder).name,
            "folder": folder, "color": color or _next_color(data)}


def update_root(data: dict, root_id: str, patch: dict) -> bool:
    root = find_root(data, root_id)
    if root is None:
        return False
    staged = {}
    if "name" in patch:
        staged["name"] = (patch["name"] or "").strip() or root.get("name")
    if "folder" in patch:
        folder = (str(patch["folder"]).strip() if patch["folder"] else "")
        if not folder:
            raise ValueError("a root needs a folder")
        staged["folder"] = folder
    if "color" in patch and patch["color"]:
        staged["color"] = str(patch["color"])
    root.update(staged)
    return True


def rule_counts(data: dict) -> dict:
    out = {}
    for r in data.get("rules", []):
        if r.get("root"):
            out[r["root"]] = out.get(r["root"], 0) + 1
    return out


def delete_root(data: dict, root_id: str, move_to: str = None) -> bool:
    """Delete a root. If rules still point at it, `move_to` (another root) must
    say where they go — otherwise ValueError."""
    if find_root(data, root_id) is None:
        return False
    owned = [r for r in data.get("rules", []) if r.get("root") == root_id]
    if owned:
        if not move_to or move_to == root_id or find_root(data, move_to) is None:
            raise ValueError(f"{len(owned)} rule(s) still file into this root — "
                             "choose another root to move them to")
        for r in owned:
            r["root"] = move_to
    data["roots"] = [r for r in data["roots"] if r.get("id") != root_id]
    return True


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def find_rule(data: dict, rule_id: str) -> dict:
    for r in data.get("rules", []):
        if r.get("id") == rule_id:
            return r
    return None


def new_rule(name: str, *, root: str = None, seller=None, product=None,
             exclude: bool = False, data: dict) -> dict:
    if not exclude and find_root(data, root) is None:
        raise ValueError(f"no root {root!r}")
    taken = {r.get("id") for r in data.get("rules", [])}
    return {
        "id": slugify(name, taken),
        "name": (name or "").strip() or "rule",
        "root": None if exclude else root,
        "seller": validate_spec(seller),
        "product": validate_spec(product),
        "exclude": bool(exclude),
        "match": [],
    }


def clean_match_entry(entry: dict) -> dict:
    """Keep only known conditions with real values. Text conditions are
    de-duplicated and stored as a string when there is one value, else a list."""
    out = {}
    for k in LIST_KEYS:
        vals = []
        for v in as_list((entry or {}).get(k)):
            if v not in vals:
                vals.append(v)
        if vals:
            out[k] = vals[0] if len(vals) == 1 else vals
    if (entry or {}).get("attachments") in ATTACHMENT_RULES:
        out["attachments"] = entry["attachments"]
    return out


def has_anchor(entry: dict) -> bool:
    """A rule must name a sender or subject condition (body/attachments alone
    would match far too much)."""
    return bool(as_list(entry.get("sender_contains")) or as_list(entry.get("subject_contains")))


def add_match(data: dict, rule_id: str, entry: dict) -> bool:
    rule = find_rule(data, rule_id)
    if rule is None:
        return False
    cleaned = clean_match_entry(entry)
    if not has_anchor(cleaned):
        return False
    rule.setdefault("match", [])
    if cleaned not in rule["match"]:
        rule["match"].append(cleaned)
    return True


def remove_match(data: dict, rule_id: str, index: int) -> bool:
    rule = find_rule(data, rule_id)
    if rule is None or not (0 <= index < len(rule.get("match", []))):
        return False
    rule["match"].pop(index)
    return True


def update_rule(data: dict, rule_id: str, patch: dict) -> bool:
    rule = find_rule(data, rule_id)
    if rule is None:
        return False
    staged = {}
    if "name" in patch:
        staged["name"] = (patch["name"] or "").strip() or rule.get("name")
    if "root" in patch and not rule.get("exclude"):
        if find_root(data, patch["root"]) is None:
            raise ValueError(f"no root {patch['root']!r}")
        staged["root"] = patch["root"]
    for k in ("seller", "product"):
        if k in patch:
            staged[k] = validate_spec(patch[k])      # may raise; nothing applied yet
    rule.update(staged)
    return True


def delete_rule(data: dict, rule_id: str) -> bool:
    n = len(data.get("rules", []))
    data["rules"] = [r for r in data.get("rules", []) if r.get("id") != rule_id]
    return len(data["rules"]) != n


def merge_rules(data: dict, src_id: str, dst_id: str) -> bool:
    """Move src's match alternatives into dst, then drop src; dst's naming and
    root apply to them from then on."""
    if src_id == dst_id:
        return False
    src, dst = find_rule(data, src_id), find_rule(data, dst_id)
    if src is None or dst is None:
        return False
    for m in src.get("match", []):
        cleaned = clean_match_entry(m)
        if cleaned and cleaned not in dst.setdefault("match", []):
            dst["match"].append(cleaned)
    return delete_rule(data, src_id)


def exclude_rule(data: dict) -> dict:
    """The shared rule for 'not a receipt' mail; created on first use."""
    rule = find_rule(data, EXCLUDE_RULE_ID)
    if rule is None:
        rule = {"id": EXCLUDE_RULE_ID, "name": "(excluded)", "root": None,
                "seller": None, "product": None, "exclude": True, "match": []}
        data.setdefault("rules", []).append(rule)
    return rule


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def _entry_matches(m: dict, sender: str, subject: str, body_norm: str,
                   attachment_count: int = None) -> bool:
    """ALL positive conditions hold and NONE of the negative ones does.
    Sender/subject are case-insensitive; body is case-sensitive (as before).
    `attachment_count` None = unknown, so the attachments condition is skipped."""
    s, j = (sender or "").lower(), (subject or "").lower()
    if any(f.lower() not in s for f in as_list(m.get("sender_contains"))):
        return False
    if any(f.lower() not in j for f in as_list(m.get("subject_contains"))):
        return False
    if any(f.lower() in j for f in as_list(m.get("exclude_subject_contains"))):
        return False
    if any(f not in body_norm for f in as_list(m.get("body_contains"))):
        return False
    if any(f in body_norm for f in as_list(m.get("exclude_body_contains"))):
        return False
    rule = m.get("attachments")
    if rule in ATTACHMENT_RULES and attachment_count is not None:
        n = int(attachment_count)
        if (rule == "none" and n != 0) or (rule == "one" and n != 1) or \
           (rule == "many" and n < 2):
            return False
    return True


def find_match(sender: str, subject: str = "", body: str = "",
               data: dict = None, attachment_count: int = None):
    """The first rule with a matching alternative, or None."""
    data = data if data is not None else load()
    body_norm = normalize_body(body)
    for rule in data.get("rules", []):
        for m in rule.get("match", []):
            if _entry_matches(m, sender, subject, body_norm, attachment_count):
                return rule
    return None


def match_rule(sender: str, subject: str = "", body: str = "",
               data: dict = None, attachment_count: int = None):
    """`(seller, product, folder)` for the first matching rule — `folder` a
    Path — or `(EXCLUDE, None, None)` for an exclude rule, or None."""
    data = data if data is not None else load()
    rule = find_match(sender, subject, body, data, attachment_count)
    if rule is None:
        return None
    if rule.get("exclude"):
        return EXCLUDE, None, None
    seller, product = resolve_names(rule, sender, subject, body)
    return seller, product, destination_of(rule, data)


def query_terms(data: dict = None) -> list:
    """One `{sender_contains: str, exclude_subject_contains: [str]}` per distinct
    sender fragment of every alternative (exclude rules included) — what the
    providers' mailbox searches use."""
    data = data if data is not None else load()
    out = []
    for rule in data.get("rules", []):
        for m in rule.get("match", []):
            excl = as_list(m.get("exclude_subject_contains"))
            for frag in as_list(m.get("sender_contains")) or [""]:
                t = {"sender_contains": frag, "exclude_subject_contains": excl}
                if t not in out:
                    out.append(t)
    return out
