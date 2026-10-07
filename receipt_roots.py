"""
receipt_roots.py
----------------
Discover every destination root receipts can land in — the fixed dirs plus
every root folder in rules.json that isn't already inside one of them — and
guard filesystem access so the UI's browse() can never walk outside them.
"""

import os
from pathlib import Path

import receipt_saver
import rules

RECEIPTS_DIR    = receipt_saver.RECEIPTS_DIR
MANUAL_DIR      = receipt_saver.MANUAL_DIR
JAPANOLOGIA_DIR = receipt_saver.JAPANOLOGIA_DIR

_FIXED = [
    ("קבלות", RECEIPTS_DIR),
    ("לטיפול ידני", MANUAL_DIR),
    ("Japanologia", JAPANOLOGIA_DIR),
]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _inside(path, root) -> bool:
    a, b = _norm(path), _norm(root)
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def discover_roots(rules_path: Path = None) -> list:
    out = [{"label": label, "path": str(path)} for label, path in _FIXED]
    dests = [r["folder"] for r in rules.load(rules_path).get("roots", []) if r.get("folder")]
    # outermost first, so a nested destination folds into its parent root
    for d in sorted(dests, key=lambda p: len(_norm(p))):
        if any(_inside(d, r["path"]) for r in out):
            continue
        out.append({"label": Path(d).name, "path": str(d)})
    return out


def is_within_roots(path: str, roots: list = None) -> bool:
    roots = roots or discover_roots()
    try:
        target = os.path.realpath(path)
    except OSError:
        return False
    for r in roots:
        root = os.path.realpath(r["path"])
        try:
            if os.path.commonpath([target, root]) == root:
                return True
        except ValueError:
            continue  # different drive
    return False
