"""
ui_state.py
-----------
Small persisted bag of window UI preferences (which explorer roots are hidden,
whether the fallbacks list is in simple mode). Atomic write, single lock.
"""

import json
import os
import threading
from pathlib import Path

UI_STATE_FILE = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver\ui_state.json")
DEFAULTS = {"hidden_roots": [], "fallbacks_simple": False}
_LOCK = threading.Lock()


def load(path: Path = None) -> dict:
    path = path or UI_STATE_FILE
    out = dict(DEFAULTS)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out.update({k: data[k] for k in DEFAULTS if k in data})
    except Exception:
        pass
    return out


def save(patch: dict, path: Path = None) -> dict:
    path = path or UI_STATE_FILE
    with _LOCK:
        merged = load(path)
        merged.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
        p = Path(path)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return merged
