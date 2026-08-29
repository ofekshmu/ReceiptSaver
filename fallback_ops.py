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


def _atomic_write_json(path: Path, data) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def compute_destination(entry: dict, decision: dict, receipts_dir: Path = None) -> Path:
    receipts_dir = Path(receipts_dir or RECEIPTS_DIR)
    root = Path(decision["base_dir"]) if decision.get("base_dir") else receipts_dir
    category = decision.get("category")
    base = root / category if category else root
    seller  = receipt_saver.sanitize(decision["seller"])
    product = receipt_saver.sanitize(decision["product"])
    name = f'{entry["date"]} - {seller} - {product} - {entry["account"]}'
    folder, _ = receipt_saver.unique_folder(base, name)
    return folder


def _move_folder(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.mkdir(exist_ok=True)
    for item in Path(src).iterdir():
        shutil.move(str(item), str(dst / item.name))
    try:
        Path(src).rmdir()
    except OSError:
        pass
    return dst


def _append_json_list(path: Path, item: dict) -> None:
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        rows = []
    rows.append(item)
    _atomic_write_json(path, rows)


def _mark_resolved(fallback_log_path: Path, message_id: str, new_path: str,
                   resolved_at: str = None, resolved_by: str = None) -> None:
    rows = json.loads(Path(fallback_log_path).read_text(encoding="utf-8"))
    for r in rows:
        if r.get("message_id") == message_id:
            r["resolved"] = True
            if new_path:
                r["folder_path"] = new_path
            if resolved_at:
                r["resolved_at"] = resolved_at
            if resolved_by:
                r["resolved_by"] = resolved_by
    _atomic_write_json(fallback_log_path, rows)


def _category_destination(cat: dict, entry: dict, seller: str, product: str,
                          receipts_dir: Path) -> Path:
    root = Path(cat["base_dir"]) if cat.get("base_dir") else Path(receipts_dir)
    base = root / cat["subfolder"] if cat.get("subfolder") else root
    name = (f'{entry["date"]} - {receipt_saver.sanitize(seller)} - '
            f'{receipt_saver.sanitize(product)} - {entry["account"]}')
    folder, _ = receipt_saver.unique_folder(base, name)
    return folder


def apply_decision(entry: dict, decision: dict, *,
                   rules_path: Path = None, fallback_log_path: Path = None,
                   cleanup_log_path: Path = None, history_path: Path = None,
                   categories_path: Path = None,
                   receipts_dir: Path = None, manual_dir: Path = None,
                   resolved_by: str = "user") -> dict:
    import categories as _cat
    rules_path        = rules_path or CUSTOM_RULES_FILE
    fallback_log_path = fallback_log_path or FALLBACK_LOG_FILE
    cleanup_log_path  = cleanup_log_path or CLEANUP_LOG_FILE
    categories_path   = categories_path or _cat.CATEGORIES_FILE
    receipts_dir      = Path(receipts_dir or RECEIPTS_DIR)
    import datetime as _dt
    src = Path(entry["folder_path"])
    rec_id = f'{entry["account"]}:{entry["message_id"]}'
    kind = decision.get("kind")
    resolved_at = _dt.datetime.now().isoformat(timespec="seconds")

    def _history_base() -> dict:
        """Fields to seed a history row from the fallback entry, so resolving a
        fallback that was never recorded during a scan still shows in History."""
        return {
            "id": rec_id,
            "account": entry.get("account"),
            "account_email": entry.get("account_email", ""),
            "date": entry.get("date"),
            "sender": entry.get("sender"),
            "subject": entry.get("subject"),
            "folder_name": entry.get("folder_name"),
            "folder_path": entry.get("folder_path"),
            "resolved_at": resolved_at,
            "resolved_by": resolved_by,
        }

    if kind == "skip":
        return {"ok": True, "kind": "skip"}

    def _match_entry() -> dict:
        e = {"sender_contains": decision.get("match_sender_contains")}
        if decision.get("match_subject_contains"):
            e["subject_contains"] = decision["match_subject_contains"]
        if decision.get("match_exclude_subject_contains"):
            e["exclude_subject_contains"] = decision["match_exclude_subject_contains"]
        if decision.get("product_body_regex"):
            e["product_body_regex"] = decision["product_body_regex"]
        return e

    if kind == "exclude":
        cats = _cat.load_categories(categories_path)
        xc = _cat.exclude_category(cats)
        _cat.add_match(cats, xc["id"], _match_entry())
        _cat.save_categories(cats, categories_path)
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        _append_json_list(cleanup_log_path, {
            "action": "DELETED", "folder": entry["folder_name"],
            "reason": f'excluded via fallback UI ({decision["match_sender_contains"]})',
            "timestamp": _dt.datetime.now().isoformat()})
        _mark_resolved(fallback_log_path, entry["message_id"], "",
                       resolved_at=resolved_at, resolved_by=resolved_by)
        history.upsert(rec_id, {**_history_base(),
                                "action": "RESOLVED", "resolution": "exclude"},
                       path=history_path)
        return {"ok": True, "kind": "exclude"}

    if kind == "once":
        dst = compute_destination(entry, decision, receipts_dir)
        moved = _move_folder(src, dst) if src.exists() else dst
        _mark_resolved(fallback_log_path, entry["message_id"], str(moved),
                       resolved_at=resolved_at, resolved_by=resolved_by)
        history.upsert(rec_id, {
            **_history_base(), "action": "RESOLVED", "resolution": "once",
            "seller": decision["seller"], "product": decision["product"],
            "category": decision.get("category"),
            "folder_name": moved.name, "folder_path": str(moved),
        }, path=history_path)
        return {"ok": True, "kind": "once", "dest": str(moved)}

    # kind in ("category", "new_category", or legacy "rule" == new_category)
    cats = _cat.load_categories(categories_path)
    if kind == "category":
        cat = _cat.find(cats, decision.get("category_id"))
        if cat is None:
            return {"ok": False, "error": f'no category {decision.get("category_id")!r}'}
    else:
        name = decision.get("category_name") or decision.get("seller") or "category"
        cat = _cat.new_category(name, seller=decision.get("seller"),
                                product=decision.get("product"),
                                base_dir=decision.get("base_dir"),
                                subfolder=decision.get("category"), categories=cats)
        cats.append(cat)

    _cat.add_match(cats, cat["id"], {**_match_entry(),
                                     "seller": decision.get("seller"),
                                     "product": decision.get("product")})
    _cat.save_categories(cats, categories_path)

    seller  = decision.get("seller") or cat.get("seller")
    product = decision.get("product") or cat.get("product")
    dst = _category_destination(cat, entry, seller, product, receipts_dir)
    moved = _move_folder(src, dst) if src.exists() else dst
    _mark_resolved(fallback_log_path, entry["message_id"], str(moved),
                   resolved_at=resolved_at, resolved_by=resolved_by)
    history.upsert(rec_id, {
        **_history_base(), "action": "RESOLVED", "resolution": "category",
        "seller": receipt_saver.sanitize(seller),
        "product": receipt_saver.sanitize(product),
        "category": cat.get("subfolder"),
        "category_id": cat["id"], "category_name": cat.get("name"),
        "folder_name": moved.name, "folder_path": str(moved),
    }, path=history_path)
    return {"ok": True, "kind": kind, "category_id": cat["id"], "dest": str(moved)}
