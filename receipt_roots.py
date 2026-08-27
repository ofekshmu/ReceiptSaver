"""
receipt_roots.py
----------------
Discover every destination root receipts can land in — the fixed dirs plus
every `base_dir` declared in custom_rules.json — and guard filesystem access
so the UI's browse() can never walk outside one of them.
"""

import json
import os
from pathlib import Path

import receipt_saver

RECEIPTS_DIR    = receipt_saver.RECEIPTS_DIR
MANUAL_DIR      = receipt_saver.MANUAL_DIR
JAPANOLOGIA_DIR = receipt_saver.JAPANOLOGIA_DIR
CUSTOM_RULES_FILE = receipt_saver.CUSTOM_RULES_FILE

_FIXED = [
    ("קבלות", RECEIPTS_DIR),
    ("לטיפול ידני", MANUAL_DIR),
    ("Japanologia", JAPANOLOGIA_DIR),
]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def discover_roots(rules_path: Path = None) -> list:
    rules_path = rules_path or CUSTOM_RULES_FILE
    out, seen = [], set()

    def add(label, path):
        key = _norm(path)
        if key in seen:
            return
        seen.add(key)
        out.append({"label": label, "path": str(path)})

    for label, path in _FIXED:
        add(label, path)

    try:
        rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception:
        rules = []
    for rule in rules:
        bd = rule.get("base_dir")
        if bd:
            add(Path(bd).name, bd)

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
