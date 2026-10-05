# Fallback Handling — Design Spec
**Date:** 2026-06-27

## Overview

Process 7 unresolved fallback emails by adding rules for 4 new sender patterns, adding path-reachability checks to the script and fallback-handling sessions, and physically moving the existing fallback folders to their correct destinations.

---

## Section 1 — New Rules

### custom_rules.json additions (order matters — first match wins)

Rules are inserted in this order within the file:

| Position | Sender filter | Subject filter | Seller | Product | Destination |
|----------|--------------|---------------|--------|---------|-------------|
| existing | `iec.co.il` | `אישור הפעלת שירות` | — | — | exclude |
| **new** | `iec.co.il` | `349305852` | חברת חשמל לישראל | חשבונית חשמל | `נכסים\שלום שבאזי 7\חשבנות` + category `חשמל` |
| **new** | `iec.co.il` | — | חברת חשמל לישראל | חשבונית חשמל | `קבלות\חשבנות\חשמל` |
| **new** | — | `349305852` | שלום שבאזי 7 | חשבון | `נכסים\שלום שבאזי 7\חשבנות` |
| **new** | `miluim.idf.il` | — | צה״ל | אישור מילואים | `C:\Users\ofeks\OneDrive\Ofek\Documents\צבא\מילואים` |
| **new** | `morning.co` | `בר סרוסי` | קבוצת בר סרוסי | חשבונית | `C:\Users\ofeks\OneDrive\Documents\נכסים` |
| **new** | `planetcinema.co.il` | — | Planet Cinema | כרטיסים | `קבלות` (no subcategory) |

**Note:** IEC is kept entirely in `custom_rules.json` (not `KNOWN_RULES`) so the property-specific 349305852 rule can fire before the general IEC rule. The 349305852 catch-all (no sender filter) handles any future non-IEC bills for that contract (water, gas, etc.).

---

## Section 2 — Path-Reachability Checks

### A — Runtime check (script startup)

At the start of `main()`, after loading `custom_rules.json`:
- Collect all `base_dir` values from every rule + the fixed dirs (`RECEIPTS_DIR`, `JAPANOLOGIA_DIR`)
- For each path that does not exist on disk → send a single grouped desktop notification:
  `"⚠️ Receipt Saver: missing paths: [path1, path2, ...]"`
- Script continues normally (does not abort); individual saves still fall back to `_לטיפול ידני` if their specific `base_dir` is missing

### B — Fallback-handling session (Claude)

At the start of every fallback-handling session with Claude:
- Check all `base_dir` values in `custom_rules.json` and fixed dirs before touching any fallback entries
- Report any unreachable paths to the user so they can be updated before proceeding

### Save-time check (custom rule branch)

In `process_message()`, when a custom rule with a `base_dir` is matched:
- Before `folder.mkdir()`, check `rule_base_dir.exists()`
- If missing → desktop notification naming the path → fall through to `_לטיפול ידני` for that email

---

## Section 3 — Move Existing Fallback Folders

All 7 unresolved folders are moved from `_לטיפול ידני` and renamed to match what the new rules would have generated. `fallback_log.json` entries are updated with `resolved: true` and the new `folder_path`.

| Old name (in `_לטיפול ידני`) | New location | New folder name |
|------------------------------|-------------|-----------------|
| `2026_06_02 - חברת חשמל לישראל - שמואל... - ofek` | `נכסים\שלום שבאזי 7\חשבנות\חשמל` | `2026_06_02 - חברת חשמל לישראל - חשבונית חשמל - ofek` |
| `2026_06_08 - חברת חשמל לישראל - שמואל... - family` | `נכסים\שלום שבאזי 7\חשבנות\חשמל` | `2026_06_08 - חברת חשמל לישראל - חשבונית חשמל - family` |
| `2026_06_07 - donotreply - טופס אישור... - ofek` | `צבא\מילואים` | `2026_06_07 - צה״ל - אישור מילואים - ofek` |
| `2026_06_11 - donotreply - טופס אישור... - ofek` | `צבא\מילואים` | `2026_06_11 - צה״ל - אישור מילואים - ofek` |
| `2026_06_16 - donotreply - טופס אישור... - ofek` | `צבא\מילואים` | `2026_06_16 - צה״ל - אישור מילואים - ofek` |
| `2026_06_10 - קבוצת בר סרוסי... - family` | `נכסים` | `2026_06_10 - קבוצת בר סרוסי - חשבונית - family` |
| `2026_06_15 - Planet Cinema... - yuval` | `קבלות` | `2026_06_15 - Planet Cinema - כרטיסים - yuval` |

---

## Out of Scope

- Renaming or restructuring the existing `KNOWN_RULES` entries beyond IEC
- Adding a UI or automation for the fallback-handling session itself
- Water/gas/other utilities for שלום שבאזי 7 (handled by the 349305852 catch-all when they appear)
