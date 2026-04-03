"""
move_fallbacks.py
-----------------
Run ONCE to move the 8 resolved fallback folders into the main קבלות folder
with their correct names, and mark them as resolved in fallback_log.json.
"""

import json
import shutil
from pathlib import Path

RECEIPTS_DIR      = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR        = RECEIPTS_DIR / "_לטיפול ידני"
SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"

# Map: old folder name → new folder name
MOVES = {
    "2026-03-10 - noreply - אישור תשלום ביט - ofek":
        "2026-03-10 - עיריית ראשון לציון - אישור תשלום - ofek",

    "2026-03-10 - מירי נחום - חשבונית מס קבלה 7721 מאת יפנולוגי - ofek":
        "2026-03-10 - יפנולוגי - חשבונית מס קבלה - ofek",

    "2026-03-03 - מירי נחום - חשבונית מס קבלה 7706 מאת יפנולוגי - ofek":
        "2026-03-03 - יפנולוגי - חשבונית מס קבלה - ofek",

    "2026-02-10 - מירי נחום - חשבונית מס קבלה 7633 מאת יפנולוגי - ofek":
        "2026-02-10 - יפנולוגי - חשבונית מס קבלה - ofek",

    "2026-04-02 - Heshbon - חשבונית חשמל סופרפאוור 55955672 - family":
        "2026-04-02 - אלקטרה פאוור - חשבונית חשמל - family",

    "2026-03-26 - פזגז - חשבונית הגז שלך מפזגז לתקופה 16_01_2026-17_03_2026 ממתינה לך - family":
        "2026-03-26 - פזגז - חשבונית גז - family",

    "2026-03-05 - Heshbon - חשבונית חשמל סופרפאוור 55897104 - family":
        "2026-03-05 - אלקטרה פאוור - חשבונית חשמל - family",

    "2026-02-19 - Elal-Invoice - Payment receipt and useful information - family":
        "2026-02-19 - אל על - כרטיס טיסה - family",
}

def main():
    moved = 0
    for old_name, new_name in MOVES.items():
        src = MANUAL_DIR / old_name
        dst = RECEIPTS_DIR / new_name

        if not src.exists():
            print(f"⚠️  Not found (already moved?): {old_name}")
            continue
        if dst.exists():
            print(f"⚠️  Destination already exists: {new_name}")
            continue

        shutil.move(str(src), str(dst))
        print(f"✓  {old_name}\n   → {new_name}\n")
        moved += 1

    # Mark all as resolved in fallback_log.json
    if FALLBACK_LOG_FILE.exists():
        entries = json.loads(FALLBACK_LOG_FILE.read_text(encoding="utf-8"))
        for entry in entries:
            entry["resolved"] = True
        FALLBACK_LOG_FILE.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n✓  fallback_log.json updated — all marked resolved")

    print(f"\nDone — {moved}/{len(MOVES)} folders moved.")

if __name__ == "__main__":
    main()
