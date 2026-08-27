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


def _mark_resolved(fallback_log_path: Path, message_id: str, new_path: str) -> None:
    rows = json.loads(Path(fallback_log_path).read_text(encoding="utf-8"))
    for r in rows:
        if r.get("message_id") == message_id:
            r["resolved"] = True
            if new_path:
                r["folder_path"] = new_path
    _atomic_write_json(fallback_log_path, rows)


def apply_decision(entry: dict, decision: dict, *,
                   rules_path: Path = None, fallback_log_path: Path = None,
                   cleanup_log_path: Path = None, history_path: Path = None,
                   receipts_dir: Path = None, manual_dir: Path = None) -> dict:
    rules_path        = rules_path or CUSTOM_RULES_FILE
    fallback_log_path = fallback_log_path or FALLBACK_LOG_FILE
    cleanup_log_path  = cleanup_log_path or CLEANUP_LOG_FILE
    receipts_dir      = Path(receipts_dir or RECEIPTS_DIR)
    src = Path(entry["folder_path"])
    rec_id = f'{entry["account"]}:{entry["message_id"]}'
    kind = decision.get("kind")

    if kind == "skip":
        return {"ok": True, "kind": "skip"}

    if kind == "exclude":
        rule = {"_comment": f'auto-added from fallback {entry["message_id"]}',
                "match_sender_contains": decision["match_sender_contains"],
                "match_subject_contains": decision.get("match_subject_contains"),
                "exclude": True}
        _append_json_list(rules_path, rule)
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        import datetime as _dt
        _append_json_list(cleanup_log_path, {
            "action": "DELETED", "folder": entry["folder_name"],
            "reason": f'excluded via fallback UI ({decision["match_sender_contains"]})',
            "timestamp": _dt.datetime.now().isoformat()})
        _mark_resolved(fallback_log_path, entry["message_id"], "")
        history.update(rec_id, {"action": "RESOLVED", "resolution": "exclude"},
                       path=history_path)
        return {"ok": True, "kind": "exclude"}

    # kind in ("rule", "once")
    dst = compute_destination(entry, decision, receipts_dir)
    if kind == "rule":
        rule = {"_comment": f'auto-added from fallback {entry["message_id"]}',
                "match_sender_contains": decision["match_sender_contains"],
                "match_subject_contains": decision.get("match_subject_contains"),
                "seller": decision["seller"], "product": decision["product"],
                "category": decision.get("category")}
        if decision.get("base_dir"):
            rule["base_dir"] = decision["base_dir"]
        _append_json_list(rules_path, rule)
    moved = _move_folder(src, dst) if src.exists() else dst
    _mark_resolved(fallback_log_path, entry["message_id"], str(moved))
    history.update(rec_id, {
        "action": "RESOLVED", "resolution": kind,
        "seller": decision["seller"], "product": decision["product"],
        "category": decision.get("category"),
        "folder_name": moved.name, "folder_path": str(moved),
    }, path=history_path)
    return {"ok": True, "kind": kind, "dest": str(moved)}
