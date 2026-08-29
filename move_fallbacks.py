"""
move_fallbacks.py
-----------------
Run ONCE to move resolved fallback folders into the main קבלות folder
with their correct names, and mark them as resolved in fallback_log.json.

This file is overwritten each time Claude resolves a new batch of
fallback emails — it reflects the most recent batch only.
"""

import json
import shutil
from pathlib import Path

RECEIPTS_DIR      = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
MANUAL_DIR        = RECEIPTS_DIR / "_לטיפול ידני"
SCRIPT_DIR        = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")
FALLBACK_LOG_FILE = SCRIPT_DIR / "fallback_log.json"

# Map: old folder name → (new folder path relative to RECEIPTS_DIR)
MOVES = {
    "2026_06_02 - חברת חשמל לישראל - שמואל אופק  מספר חשבון חוזה 349305852  לתקופה - 01_06_2026 - - ofek":
        r"חשבנות\חשמל\2026_06_02 - חברת חשמל לישראל - חשבונית חשמל - ofek",

    "2026_06_08 - חברת חשמל לישראל - שמואל אופק  עבור תשלום קבלה  349305852 - family":
        r"חשבנות\חשמל\2026_06_08 - חברת חשמל לישראל - חשבונית חשמל - family",

    "2026_06_10 - קבוצת בר סרוסי השקעות  בע״מ - חשבונית מס _ קבלה 60421 - קבוצת בר סרוסי השקעות  בע_מ - family":
        "2026_06_10 - בר סרוסי השקעות - חשבונית - family",

    "2026_06_15 - Planet Cinema - אישור הזמנה פלאנט ראשון לציון - yuval":
        "2026_06_15 - Planet Cinema - כרטיסים - yuval",

    "2026_07_07 - אמריקן דיגיטקס (ישראל) בע״מ - חשבון עסקה 42058 - אמריקן דיגיטקס (ישראל) בע_מ - ofek":
        "2026_07_07 - אמריקן דיגיטקס - חשבונית - ofek",

    "2026_04_12 - Abir - כדור פיזיו - yuval":
        "2026_04_12 - אביר ספורט - כדור פיזיו - yuval",

    "2026_04_12 - upapp - כניסה לחדר כושר אייקון - yuval":
        "2026_04_12 - upapp - כניסה לחדר כושר אייקון - yuval",
}

# The two גן ילדים דיסני ראשון folders (invoice + receipt for the same month)
# merge into a single dated folder instead of a 1:1 rename.
MERGES = {
    "2026_07_07 - גן ילדים דיסני ראשון - שכר לימוד - family": [
        "2026_07_07 - גן ילדים דיסני ראשון - חשבונית מס מס' 5167 - family",
        "2026_07_07 - גן ילדים דיסני ראשון - קבלה מס' 1962 - family",
    ],
}

# Only these message IDs get marked resolved — everything else (e.g. the
# still-undecided miluim.idf.il emails) is left untouched.
RESOLVED_IDS = {
    "19e89352e342de40", "19ea5edd5ff25748", "19eb05e66b32a150",
    "19ecceacc7ab71fc", "19f3cab6e8193864",
    "19f3bf159067d487", "19f3bf09b2b24b3a",
    "19d8004cbb6a0420", "19d7ffe43957dbce",
}

def main():
    moved = 0
    for old_name, new_rel in MOVES.items():
        src = MANUAL_DIR / old_name
        dst = RECEIPTS_DIR / new_rel

        if not src.exists():
            print(f"⚠️  Not found (already moved?): {old_name}")
            continue
        if dst.exists():
            print(f"⚠️  Destination already exists: {new_rel}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(f"✓  {old_name}\n   → {new_rel}\n")
        moved += 1

    for new_name, old_names in MERGES.items():
        dst = RECEIPTS_DIR / new_name
        dst.mkdir(parents=True, exist_ok=True)
        for old_name in old_names:
            src = MANUAL_DIR / old_name
            if not src.exists():
                print(f"⚠️  Not found (already moved?): {old_name}")
                continue
            for item in src.iterdir():
                shutil.move(str(item), str(dst / item.name))
            src.rmdir()
            print(f"✓  {old_name}\n   → merged into {new_name}\n")
            moved += 1

    # Mark all as resolved in fallback_log.json
    if FALLBACK_LOG_FILE.exists():
        entries = json.loads(FALLBACK_LOG_FILE.read_text(encoding="utf-8"))
        for entry in entries:
            if entry.get("message_id") in RESOLVED_IDS:
                entry["resolved"] = True
        FALLBACK_LOG_FILE.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n✓  fallback_log.json updated — {len(RESOLVED_IDS)} entries marked resolved")

    print(f"\nDone — {moved}/{len(MOVES) + sum(len(v) for v in MERGES.values())} folders moved.")

if __name__ == "__main__":
    main()
