"""
fallback_ops.py
---------------
Heuristic prefill for unresolved fallback emails, plus the operations that
apply a user's decision: create/extend a rule (and maybe a root), move the
folder out of `_לטיפול ידני`, mark `fallback_log.json` resolved, and patch the
history row.

`suggest()` uses ONLY the sender and subject — no email body, no network, no
AI. It is a starting point for the form the user edits.

Decision payload (from the Fallbacks form):

    {"kind": "rule" | "new_rule" | "once" | "exclude" | "skip",
     "rule_id": str,                                      # rule: extend this rule
     "rule_name": str,                                    # new_rule
     "root_id": str | "new_root": {"name", "folder"},     # new_rule
     "destination": str,                                  # once
     "seller":  {"value": str, "every_mail": bool,
                 "extract": null | {"source", "regex", "fallback"},
                 "template": null | str},
     "product": {...same...},
     "match":   {"sender_contains", "subject_contains",
                 "exclude_subject_contains", "body_contains"}}

`value` is always what THIS mail's folder is named with; `every_mail` /
`extract` only shape the spec saved on a new rule.
"""

import json
import os
import shutil
import stat
from pathlib import Path

import receipt_saver
import history
import naming
import rules as _rules

SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"
CLEANUP_LOG_FILE  = SCRIPT_DIR / "cleanup_log.json"
RECEIPTS_DIR      = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR        = RECEIPTS_DIR / "_לטיפול ידני"

_SUBFOLDER_KEYWORDS = [
    (("חשמל",), ("חשבנות", "חשמל")),
    (("מים", "מיים"), ("חשבנות", "מיים")),
    (("ארנונה",), ("חשבנות", "ארנונה")),
    (("אינטרנט",), ("חשבנות", "אינטרנט")),
    (("גז",), ("חשבנות", "גז")),
]
_EXCLUDE_KEYWORDS = ("פרסומת", "הטבה", "דיוור", "newsletter", "מבצע")


def _sender_rule(sender: str, data: dict):
    """A rule that already knows this sender (by its sender fragments alone)."""
    s = (sender or "").lower()
    for rule in data.get("rules", []):
        if rule.get("exclude"):
            continue
        for m in rule.get("match", []):
            frags = _rules.as_list(m.get("sender_contains"))
            if frags and all(f.lower() in s for f in frags):
                return rule
    return None


def _root_for_folder(data: dict, folder) -> dict:
    key = os.path.normcase(os.path.normpath(str(folder)))
    for root in data.get("roots", []):
        if os.path.normcase(os.path.normpath(str(root.get("folder")))) == key:
            return root
    return None


def suggest(entry: dict, rules_path: Path = None, receipts_dir: Path = None) -> dict:
    receipts_dir = Path(receipts_dir or RECEIPTS_DIR)
    sender  = entry.get("sender", "")
    subject = entry.get("subject", "")
    domain  = naming.registered_domain(sender)
    seller, product = naming.suggest_names(sender, subject)
    destination, confidence, rule_id, root_id = receipts_dir, "low", None, None
    data = _rules.load(rules_path)

    known = _sender_rule(sender, data)
    if known:
        seller, product = _rules.resolve_names(known, sender, subject,
                                               date=entry.get("date"))
        destination = _rules.destination_of(known, data)
        confidence, rule_id, root_id = "high", known["id"], known.get("root")
    else:
        for needles, parts in _SUBFOLDER_KEYWORDS:
            if any(n in subject for n in needles):
                destination = receipts_dir.joinpath(*parts)
                break
        root = _root_for_folder(data, destination)
        root_id = root["id"] if root else None

    kind = "file"
    if any(k.lower() in subject.lower() for k in _EXCLUDE_KEYWORDS):
        kind = "exclude"

    if confidence == "low" and (product != naming.DEFAULT_PRODUCT
                                or destination != receipts_dir):
        confidence = "medium"

    return {
        "seller": seller,
        "product": product,
        "destination": str(destination),
        "rule_id": rule_id,
        "root_id": root_id,
        "match_sender_contains": domain,
        "kind": kind,
        "confidence": confidence,
    }


def _atomic_write_json(path: Path, data) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _clear_read_only(path) -> None:
    """OneDrive marks the folders it syncs read-only, and Windows refuses to
    delete a read-only folder ("Access is denied")."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _remove_empty_dir(path: Path):
    """Remove an emptied folder; returns None, or why it couldn't be removed."""
    p = Path(path)
    for attempt in (1, 2):
        try:
            p.rmdir()
            return None
        except FileNotFoundError:
            return None
        except OSError as e:
            if attempt == 2:
                return str(e)
            _clear_read_only(p)


def _remove_tree(path: Path):
    """Delete a folder and everything in it; returns None, or why it failed."""
    def retry_writable(func, p, _exc):
        _clear_read_only(p)
        func(p)                                   # a second failure propagates
    try:
        shutil.rmtree(path, onerror=retry_writable)
        return None
    except FileNotFoundError:
        return None
    except OSError as e:
        return str(e)


def _move_folder(src: Path, dst: Path) -> tuple:
    """Move src's files into dst and remove the emptied src.
    Returns (dst, problem) — problem is None or why src couldn't be removed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.mkdir(exist_ok=True)
    for item in Path(src).iterdir():
        shutil.move(str(item), str(dst / item.name))
    return dst, _remove_empty_dir(src)


def _leftover_warning(src: Path, problem: str) -> str:
    return (f"Filed — but the old folder “{Path(src).name}” couldn't be removed from "
            f"{Path(src).parent.name}: {problem}")


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


def recheck_conflicts(*, fallback_log_path: Path = None, cleanup_log_path: Path = None,
                      history_path: Path = None, rules_path: Path = None,
                      receipts_dir: Path = None) -> dict:
    """Re-evaluate every fallback waiting on a rule conflict against the current
    rules (run after any rule change):
      exactly one rule -> filed by it (or excluded, for an exclude rule)
      no rule          -> becomes an ordinary fallback
      2+ rules         -> keeps waiting; the explanation is refreshed
    Returns {"filed": n, "cleared": n, "still": n}."""
    import datetime as _dt
    fallback_log_path = Path(fallback_log_path or FALLBACK_LOG_FILE)
    cleanup_log_path = cleanup_log_path or CLEANUP_LOG_FILE
    out = {"filed": 0, "cleared": 0, "still": 0}
    try:
        rows = json.loads(fallback_log_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not any(r.get("conflict") and not r.get("resolved") for r in rows):
        return out
    data = _rules.load(rules_path)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    changed = False
    for e in rows:
        c = e.get("conflict")
        if e.get("resolved") or not c:
            continue
        sender, subject, body = e.get("sender", ""), e.get("subject", ""), c.get("body", "")
        hits = _rules.match_all(sender, subject, body, data=data,
                                attachment_count=e.get("attachment_count"))
        if len(hits) > 1:
            info = _rules.conflict_info(hits)
            if info != c.get("rules"):
                c["rules"], changed = info, True
            out["still"] += 1
            continue
        changed = True
        if not hits:
            e.pop("conflict", None)
            out["cleared"] += 1
            continue
        rule = hits[0]["rule"]
        root = _rules.find_root(data, rule.get("root")) or {}
        src = Path(e["folder_path"])
        rec_id = f'{e["account"]}:{e["message_id"]}'
        seed = {"id": rec_id, "account": e.get("account"), "account_email": e.get("account_email", ""),
                "date": e.get("date"), "sender": sender, "subject": subject,
                "action": "RESOLVED", "resolved_at": now, "resolved_by": "rules",
                "rule_id": rule.get("id"), "rule_name": rule.get("name"), "root_name": root.get("name")}
        if rule.get("exclude"):
            if src.exists():
                _remove_tree(src)
            _append_json_list(cleanup_log_path, {
                "action": "DELETED", "folder": e.get("folder_name"),
                "reason": f'excluded by rule “{rule.get("name")}” once a rule conflict was fixed',
                "timestamp": _dt.datetime.now().isoformat()})
            history.upsert(rec_id, {**seed, "resolution": "exclude",
                                    "folder_name": e.get("folder_name"),
                                    "folder_path": e.get("folder_path")}, path=history_path)
        else:
            seller, product = _rules.resolve_names(rule, sender, subject, body, e.get("date"))
            folder = _rules.destination_of(rule, data)
            dst = compute_destination(e, folder, seller, product)
            moved, _problem = _move_folder(src, dst) if src.exists() else (dst, None)
            e["folder_path"] = str(moved)
            history.upsert(rec_id, {
                **seed, "resolution": "rule",
                "seller": receipt_saver.sanitize(seller), "product": receipt_saver.sanitize(product),
                "category": receipt_saver._category_label(folder), "root_id": root.get("id"),
                "folder_name": moved.name, "folder_path": str(moved)}, path=history_path)
        e.update(resolved=True, resolved_at=now, resolved_by="rules")
        c.pop("body", None)                   # the text was only kept for this check
        out["filed"] += 1
    if changed:
        _atomic_write_json(fallback_log_path, rows)
    return out


def compute_destination(entry: dict, base: Path, seller: str, product: str) -> Path:
    name = (f'{entry["date"]} - {receipt_saver.sanitize(seller)} - '
            f'{receipt_saver.sanitize(product)} - {entry["account"]}')
    folder, _ = receipt_saver.unique_folder(Path(base), name)
    return folder


def spec_from(field: dict):
    """The seller/product spec a new rule stores for one form field."""
    field = field or {}
    if (field.get("template") or "").strip():
        return _rules.validate_spec({"mode": "template", "template": field["template"]})
    ex = field.get("extract")
    if ex:
        return _rules.validate_spec({"mode": "extract", "source": ex.get("source"),
                                   "regex": ex.get("regex"), "fallback": ex.get("fallback")})
    if field.get("every_mail"):
        return _rules.validate_spec({"mode": "fixed", "value": field.get("value")})
    return None


def _value(field: dict, default: str) -> str:
    return ((field or {}).get("value") or "").strip() or default


def apply_decision(entry: dict, decision: dict, *,
                   fallback_log_path: Path = None, cleanup_log_path: Path = None,
                   history_path: Path = None, rules_path: Path = None,
                   receipts_dir: Path = None, resolved_by: str = "user") -> dict:
    import datetime as _dt
    fallback_log_path = fallback_log_path or FALLBACK_LOG_FILE
    cleanup_log_path  = cleanup_log_path or CLEANUP_LOG_FILE
    rules_path        = rules_path or _rules.RULES_FILE
    receipts_dir      = Path(receipts_dir or RECEIPTS_DIR)
    src = Path(entry["folder_path"])
    rec_id = f'{entry["account"]}:{entry["message_id"]}'
    kind = decision.get("kind")
    resolved_at = _dt.datetime.now().isoformat(timespec="seconds")
    match = _rules.clean_match_entry(decision.get("match") or {})
    sug_seller, sug_product = naming.suggest_names(entry.get("sender", ""),
                                                   entry.get("subject", ""))

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

    def _resolve(new_path: str = "") -> None:
        _mark_resolved(fallback_log_path, entry["message_id"], new_path,
                       resolved_at=resolved_at, resolved_by=resolved_by)

    if entry.get("conflict") and kind != "skip":
        return {"ok": False, "error": "This email matches several rules — change the rules so "
                                      "only one matches (it's then filed automatically), or Skip it."}

    if kind == "skip":                        # dismiss: folder stays where it is
        _resolve()
        history.upsert(rec_id, {**_history_base(), "action": "RESOLVED",
                                "resolution": "dismissed"}, path=history_path)
        return {"ok": True, "kind": "skip"}

    if kind == "exclude":
        if not _rules.has_anchor(match):
            return {"ok": False, "error": "exclude needs a sender or subject condition"}
        data = _rules.load(rules_path)
        _rules.add_match(data, _rules.exclude_rule(data)["id"], match)
        _rules.save(data, rules_path)
        problem = _remove_tree(src) if src.exists() else None
        _append_json_list(cleanup_log_path, {
            "action": "DELETED", "folder": entry["folder_name"],
            "reason": "excluded via fallback UI (" + ", ".join(
                _rules.as_list(match.get("sender_contains")) + _rules.as_list(match.get("subject_contains"))) + ")",
            "timestamp": _dt.datetime.now().isoformat()})
        _resolve()
        history.upsert(rec_id, {**_history_base(),
                                "action": "RESOLVED", "resolution": "exclude"},
                       path=history_path)
        out = {"ok": True, "kind": "exclude"}
        if problem:
            out["warning"] = (f"Excluded — but the folder “{src.name}” couldn't be deleted "
                              f"from {src.parent.name}: {problem}")
        return out

    seller  = _value(decision.get("seller"), sug_seller)
    product = _value(decision.get("product"), sug_product)

    if kind == "once":
        base = Path(decision.get("destination") or receipts_dir)
        dst = compute_destination(entry, base, seller, product)
        moved, problem = _move_folder(src, dst) if src.exists() else (dst, None)
        _resolve(str(moved))
        history.upsert(rec_id, {
            **_history_base(), "action": "RESOLVED", "resolution": "once",
            "seller": receipt_saver.sanitize(seller),
            "product": receipt_saver.sanitize(product),
            "folder_name": moved.name, "folder_path": str(moved),
        }, path=history_path)
        out = {"ok": True, "kind": "once", "dest": str(moved)}
        if problem:
            out["warning"] = _leftover_warning(src, problem)
        return out

    if kind not in ("rule", "new_rule"):
        return {"ok": False, "error": f"unknown decision kind {kind!r}"}
    if not _rules.has_anchor(match):
        return {"ok": False, "error": "the match rule needs a sender or subject condition"}

    data = _rules.load(rules_path)
    if kind == "rule":
        rule = _rules.find_rule(data, decision.get("rule_id"))
        if rule is None or rule.get("exclude"):
            return {"ok": False, "error": f'no rule {decision.get("rule_id")!r}'}
    else:
        # build everything first (may raise) so a bad input changes nothing
        try:
            root = None
            if decision.get("new_root"):
                nr = decision["new_root"]
                root = _rules.new_root(nr.get("name"), nr.get("folder"),
                                       color=nr.get("color"), data=data)
                data["roots"].append(root)
            root_id = root["id"] if root else decision.get("root_id")
            rule = _rules.new_rule(decision.get("rule_name") or seller or "rule",
                                   root=root_id,
                                   seller=spec_from(decision.get("seller")),
                                   product=spec_from(decision.get("product")), data=data)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        data["rules"].append(rule)

    _rules.add_match(data, rule["id"], match)
    _rules.save(data, rules_path)

    folder = _rules.destination_of(rule, data)
    dst = compute_destination(entry, folder, seller, product)
    moved, problem = _move_folder(src, dst) if src.exists() else (dst, None)
    _resolve(str(moved))
    root = _rules.find_root(data, rule.get("root")) or {}
    history.upsert(rec_id, {
        **_history_base(), "action": "RESOLVED", "resolution": "rule",
        "seller": receipt_saver.sanitize(seller),
        "product": receipt_saver.sanitize(product),
        "category": receipt_saver._category_label(folder),
        "rule_id": rule["id"], "rule_name": rule.get("name"),
        "root_id": root.get("id"), "root_name": root.get("name"),
        "folder_name": moved.name, "folder_path": str(moved),
    }, path=history_path)
    out = {"ok": True, "kind": kind, "rule_id": rule["id"], "root_id": root.get("id"),
           "dest": str(moved)}
    if problem:
        out["warning"] = _leftover_warning(src, problem)
    return out
