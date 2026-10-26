"""
relocate.py
-----------
After a rule moves to another root (or a root gets a new folder), the receipt
folders it already filed stay where they were. This finds them and moves the
ones the user ticks.

A folder counts as filed by a rule when:
  * a History row says so (its `rule_id`, or — for rows that didn't record it —
    today's rules give that mail to exactly this rule), or
  * its name has the shape the scan gives it, `<date> - <seller> - <product> -
    <account>`, with the rule's fixed seller / product text (most folders are
    older than History). Rules whose names are fully computed per mail can only
    be found through History.

Only folders directly inside the old folder are looked at, never another root's
folder or `_לטיפול ידני`.
"""

import os
import re
from pathlib import Path

import history
import rules as R

_DATE = r"\d{4}_\d{2}_\d{2}"
_TAIL = r"\w+(?: \(\d+\))?"            # account label, plus unique_folder's " (2)"


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _part(spec):
    """(regex, has_literal_text) for the seller or product part of a folder name."""
    if spec and spec.get("mode") == "fixed" and (spec.get("value") or "").strip():
        return re.escape(R.naming.sanitize(spec["value"].strip())), True
    if spec and spec.get("mode") == "template":
        pieces, last, literal = [], 0, False
        t = spec.get("template") or ""
        for m in R._TOKEN_RE.finditer(t):
            text = R.naming.sanitize(t[last:m.start()])
            literal = literal or bool(text.strip())
            pieces += [re.escape(text), ".*?"]
            last = m.end()
        text = R.naming.sanitize(t[last:])
        literal = literal or bool(text.strip())
        pieces.append(re.escape(text))
        return "".join(pieces), literal
    return ".+?", False


def name_regex(rule: dict):
    """The folder names this rule produces, or None when nothing in them is fixed."""
    seller, s_lit = _part(rule.get("seller"))
    product, p_lit = _part(rule.get("product"))
    if not (s_lit or p_lit):
        return None
    return re.compile(rf"{_DATE} - {seller} - {product} - {_TAIL}")


def _children(folder: Path, skip: set) -> list:
    try:
        return sorted((p for p in folder.iterdir() if p.is_dir() and _norm(p) not in skip),
                      key=lambda p: p.name)
    except OSError:
        return []


def filed_folders(rule_ids, old_folder, *, data: dict = None, history_rows: list = None,
                  manual_dir: Path = None) -> list:
    """Folders in `old_folder` that the given rules filed, as
    `{path, name, source: "history"|"name", checked, also: [other rule names]}`.
    `checked` is False when another rule in the same folder fits the name too."""
    data = data if data is not None else R.load()
    rows = history_rows if history_rows is not None else history.load()
    ids = set(rule_ids or [])
    old = Path(old_folder)
    if not ids or not old.is_dir():
        return []
    skip = {_norm(r.get("folder")) for r in data.get("roots", []) if r.get("folder")}
    if manual_dir:
        skip.add(_norm(manual_dir))
    here = {_norm(p): p for p in _children(old, skip)}

    # what History knows: folder -> rule id
    owner = {}
    for row in rows:
        key = _norm(row.get("folder_path") or "")
        if key not in here:
            continue
        rid = row.get("rule_id")
        if not rid and row.get("rule_source") in ("custom", "hardcoded"):
            hits = R.match_all(row.get("sender") or "", row.get("subject") or "", data=data)
            rid = hits[0]["rule"]["id"] if len(hits) == 1 else None
        if rid:
            owner[key] = rid

    mine = [r for r in data.get("rules", []) if r.get("id") in ids]
    patterns = [rx for rx in (name_regex(r) for r in mine) if rx]
    # other rules still filing into the same folder could produce the same names
    others = [(r.get("name"), name_regex(r)) for r in data.get("rules", [])
              if r.get("id") not in ids and not r.get("exclude")
              and _norm(R.destination_of(r, data)) == _norm(old)]
    out = []
    for key, path in here.items():
        if key in owner:
            if owner[key] in ids:
                out.append({"path": str(path), "name": path.name, "source": "history",
                            "checked": True, "also": []})
            continue                                   # History says another rule filed it
        if any(rx.fullmatch(path.name) for rx in patterns):
            also = [n for n, rx in others if rx and rx.fullmatch(path.name)]
            out.append({"path": str(path), "name": path.name, "source": "name",
                        "checked": not also, "also": also})
    return sorted(out, key=lambda f: f["name"])


def move_folders(paths, dest_folder, *, history_path: Path = None,
                 manual_dir: Path = None) -> dict:
    """Move each folder (as is, same name; " (2)" on a clash) into `dest_folder`
    and point History at the new place. {moved, failed: [{name, error}]}"""
    import fallback_ops
    import receipt_saver
    dest = Path(dest_folder)
    moved, failed, repath = 0, [], {}
    for p in paths or []:
        src = Path(p)
        if not src.is_dir():
            failed.append({"name": src.name, "error": "not found"}); continue
        if manual_dir and _norm(src).startswith(_norm(manual_dir)):
            failed.append({"name": src.name, "error": "in _לטיפול ידני"}); continue
        if _norm(src.parent) == _norm(dest):
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            dst, _ = receipt_saver.unique_folder(dest, src.name)
            dst, problem = fallback_ops._move_folder(src, dst)
        except OSError as e:
            failed.append({"name": src.name, "error": str(e)}); continue
        moved += 1
        repath[_norm(src)] = dst
        if problem:
            failed.append({"name": src.name, "error": f"moved, but the old folder stayed: {problem}"})
    if repath:
        history.repath(lambda old: repath.get(_norm(old)), path=history_path,
                       category=receipt_saver._category_label(dest))
    return {"moved": moved, "failed": failed}
