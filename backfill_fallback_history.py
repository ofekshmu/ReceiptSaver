"""
backfill_fallback_history.py
----------------------------
One-off: create History rows for fallbacks that were resolved *before*
`history.upsert` existed (so the resolution was silently dropped and never
showed in the History tab).

Walks `fallback_log.json` for entries with ``resolved: true`` and upserts a
``RESOLVED`` row per message that isn't already in `history.json`. Safe to run
repeatedly — messages already in history are skipped.

Usage:
    python backfill_fallback_history.py            # apply
    python backfill_fallback_history.py --dry-run  # show what would be added
"""

import json
import sys
from pathlib import Path

import history

# Windows consoles default to cp1252; fallback subjects are often Hebrew.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR   = Path(__file__).parent
FALLBACK_LOG = SCRIPT_DIR / "fallback_log.json"


def _parse_folder(name: str):
    """Best-effort seller/product from a 'YYYY_MM_DD - Seller - Product - acct'
    folder name; (None, None) if it doesn't match that shape."""
    parts = [p.strip() for p in str(name or "").split(" - ")]
    if len(parts) >= 4:
        return parts[1] or None, parts[2] or None
    return None, None


def backfill(fallback_log_path: Path = None, history_path: Path = None,
             dry_run: bool = False) -> dict:
    fallback_log_path = Path(fallback_log_path or FALLBACK_LOG)
    try:
        entries = json.loads(fallback_log_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"can't read {fallback_log_path}: {e}")
        return {"scanned": 0, "added": 0}

    existing = {r.get("id"): r for r in history.load(history_path)}
    added = 0
    for e in entries:
        if not e.get("resolved"):
            continue
        rec_id = f'{e.get("account")}:{e.get("message_id")}'
        have = existing.get(rec_id)
        # Skip only if a row already exists AND already carries resolve fields;
        # otherwise upsert (adds the row, or patches an older row missing them).
        if have is not None and have.get("resolved_by") and have.get("resolved_at"):
            continue
        folder_path = e.get("folder_path") or ""
        seller, product = _parse_folder(e.get("folder_name"))
        # Prefer a resolution stamp the entry already carries (newer resolves
        # via the UI form / move_fallbacks record one); else fall back.
        resolved_at = e.get("resolved_at") or (
            f'{e["date"][:4]}-{e["date"][5:7]}-{e["date"][8:10]}'
            if len(e.get("date", "")) >= 10 else None)
        # "rule"/"once" resolutions are only ever written by the UI form
        # (apply_decision), so an unstamped row with one of those was a user
        # resolve; anything else we genuinely can't tell → "unknown".
        resolved_by = e.get("resolved_by") or (
            "user" if have and have.get("resolution") in ("rule", "once")
            else "unknown")
        # don't clobber a real resolution already on the row; the log itself
        # doesn't record rule-vs-once, so a fresh guess is empty folder == exclude
        resolution = (have or {}).get("resolution") \
            or ("exclude" if not folder_path else "backfilled")
        row = {
            "id":            rec_id,
            "account":       e.get("account"),
            "account_email": e.get("account_email", ""),
            "date":          e.get("date"),
            "sender":        e.get("sender"),
            "subject":       e.get("subject"),
            "action":        "RESOLVED",
            "resolution":    resolution,
            "resolved_at":   resolved_at,
            "resolved_by":   resolved_by,
            "seller":        seller or (have or {}).get("seller"),
            "product":       product or (have or {}).get("product"),
            "folder_name":   e.get("folder_name"),
            "folder_path":   folder_path or None,
        }
        print((("DRY  " if dry_run else "")
               + ("patch " if have is not None else "add   "))
              + f'{rec_id}  {str(e.get("subject", ""))[:60]}')
        if not dry_run:
            history.upsert(rec_id, row, path=history_path)
        existing[rec_id] = row
        added += 1

    verb = "would add" if dry_run else "added"
    print(f"\n{verb} {added} row(s) — {len(entries)} fallback entries scanned")
    return {"scanned": len(entries), "added": added}


if __name__ == "__main__":
    backfill(dry_run="--dry-run" in sys.argv[1:])
