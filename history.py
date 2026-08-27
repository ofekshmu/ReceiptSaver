"""
history.py
----------
Append-only store of every handled mail, backing the "History" view and the
per-run summary. One JSON array on disk. All writes are atomic (temp file +
os.replace) and serialized by a module-level lock so the scan thread and the
UI thread can both call in.
"""

import json
import os
import threading
from pathlib import Path

HISTORY_FILE = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver\history.json")

_LOCK = threading.Lock()


def load(path: Path = None) -> list:
    path = path or HISTORY_FILE
    if not Path(path).exists():
        return []
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(rows: list, path: Path):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append(record: dict, path: Path = None) -> None:
    path = path or HISTORY_FILE
    with _LOCK:
        rows = load(path)
        if any(r.get("id") == record.get("id") for r in rows):
            return
        rows.append(record)
        _write(rows, path)


def update(record_id: str, patch: dict, path: Path = None) -> None:
    path = path or HISTORY_FILE
    with _LOCK:
        rows = load(path)
        changed = False
        for r in rows:
            if r.get("id") == record_id:
                r.update(patch)
                changed = True
        if changed:
            _write(rows, path)


def page(offset: int = 0, limit: int = 50, path: Path = None) -> list:
    rows = load(path or HISTORY_FILE)
    rows = list(reversed(rows))
    return rows[offset:offset + limit]
