# Startup UI — Design Spec

**Date:** 2026-08-27

## Overview

Add a borderless, centered desktop window that opens at Windows login, drives the
mailbox scan, and shows what happened. Three views:

1. **This run** — mails handled by the scan that just ran, with the action taken
   and basic mail info.
2. **History** — every mail handled since the feature shipped, scrollable back to
   the first entry.
3. **Fallbacks** — an interactive console for the unresolved entries in
   `fallback_log.json`. For each fallback the window suggests a classification
   (heuristics only), and the user chooses what happens: make a permanent rule,
   move this one folder only, exclude it as promotional, or skip. Fallbacks the
   heuristics can't confidently classify can be sent to a pre-seeded Claude Code
   terminal for manual handling together with Claude.

The existing `receipt_saver.py` scan engine keeps working standalone; the window
wraps it.

---

## Architecture

Chosen approach: **thin GUI over a lightly-refactored engine** (pywebview
frontend + Python API, worker thread runs the existing scan).

### Modules

| File | Responsibility |
|------|----------------|
| `app.py` | pywebview frameless window + `Api` class exposed to JS. Runs the scan on a worker thread, forwards progress to the page, serves history/fallback data, applies fallback decisions. Entry point at login. |
| `receipt_saver.py` | Scan engine. `main(progress_cb=None)` refactored to accept an optional callback and return a `RunResult`. `process_message()` returns a full record dict for every non-skipped mail. Every handled mail is appended to `history.json`. Standalone CLI behavior unchanged when `progress_cb` is `None`. |
| `history.py` | Load / append / update / page `history.json`. Single module-level `threading.Lock`; atomic writes. |
| `fallback_ops.py` | Pure-ish operations the UI calls: `suggest(entry)`, `apply_rule(entry, decision)`, `move_folder(entry, decision)`, `resolve(entry, new_path, resolution)`, `exclude(entry, decision)`. Folder-move / resolve logic lifted from `move_fallbacks.py`. |
| `claude_handoff.py` | Build a pre-seeded prompt for selected fallback entries and launch a terminal running `claude "<prompt>"` in the project directory. |
| `tray.py` | `pystray` icon (Open / Run scan now / Quit). Keeps the process resident so the window can be reopened; closing the window hides to tray. |
| `ui/index.html`, `ui/app.css`, `ui/app.js` | Frontend. No build step, no framework. |
| `run.bat` | Launches `pythonw app.py` (was `python receipt_saver.py`). |
| `move_fallbacks.py` | Reduced to a thin CLI wrapper over `fallback_ops.py` (kept for manual use). |
| `requirements.txt` | New. Pins all dependencies. |

### Data flow

```
login → run.bat → pythonw app.py
  app.py: start tray, create frameless window, on ready → start_scan()
  worker thread: receipt_saver.main(progress_cb)
    per account:  progress_cb({"type":"account", "label":..., "candidates":N})
    per mail:     process_message() → record dict
                  history.append(record)
                  progress_cb({"type":"mail", "record":record})
    on account error: progress_cb({"type":"error", "label":..., "message":...})
  app.py forwards each event to JS via window.evaluate_js(...)
  JS renders "This run" live; final summary when the thread returns
```

---

## Data model

### `history.json` (new, append-only array)

One object per handled mail:

```json
{
  "id": "ofek:19d53a0b755c51b7",
  "run_id": "2026-08-27T19:00:12",
  "handled_at": "2026-08-27T19:00:14",
  "account": "ofek",
  "account_email": "ofek.shmuel1@gmail.com",
  "date": "2026_08_25",
  "sender": "Heshbon@electra-power.co.il",
  "subject": "חשבונית חשמל סופרפאוור 55955672",
  "action": "DOWNLOADED",
  "seller": "אלקטרה פאוור",
  "product": "חשבונית חשמל",
  "category": "חשבנות/חשמל",
  "folder_name": "2026_08_25 - אלקטרה פאוור - חשבונית חשמל - ofek",
  "folder_path": "C:\\Users\\ofeks\\OneDrive\\Documents\\קבלות\\חשבנות\\חשמל\\2026_08_25 - ...",
  "files": ["invoice.pdf", "email.pdf"],
  "rule_source": "custom"
}
```

- `action` ∈ `DOWNLOADED | ICOUNT | JAPANOLOGIA | FALLBACK | EXCLUDED`.
  `SKIPPED` mails (in SENT, or `פרסומת` in subject) are **not** recorded.
- `rule_source` ∈ `hardcoded | custom | icount | japanologia | null`.
- `seller` / `product` / `category` are `null` when not known (e.g. `FALLBACK`).
- Append is keyed by `id`; a duplicate `id` is ignored.
- On fallback resolution the existing row is updated in place:
  `action` → `RESOLVED`, plus `resolved_at`, `resolution`
  (`rule | once | exclude`), and refreshed `seller` / `product` / `category` /
  `folder_name` / `folder_path`.

### Run identity

`app.py` generates `run_id` (ISO timestamp) and passes it into `main()`. The
"This run" view is built from the `progress_cb` stream, not by re-querying
`history.json`.

### Fallback decision object (JS → `Api.apply_fallback`)

```json
{
  "kind": "rule",
  "seller": "אלקטרה פאוור",
  "product": "חשבונית חשמל",
  "category": "חשבנות/חשמל",
  "base_dir": null,
  "match_sender_contains": "electra-power.co.il",
  "match_subject_contains": null
}
```

`kind` ∈ `rule | once | exclude | skip`.

---

## Behavior

### Scan flow (`This run` view)

- Window opens, shows a scanning state immediately, then `start_scan()` fires.
- Per-account line appears: `ofek — 3 candidates` with a spinner, then a check.
- Each handled mail appears as a card: colored action pill, `seller · product`
  (or `sender · subject` for fallbacks), account chip, email date, file count,
  **Open folder** link.
- Account errors render as a red line and do not abort the run.
- When the worker returns, a summary header shows `N saved · M fallback · K excluded`.
- If nothing was handled and nothing is unresolved, the view shows a quiet
  "Nothing new" state. The window still opens (user asked for UI-drives-scan).
- **Run scan now** (tray + a button in the window) re-runs; disabled while running.

### History view

- `Api.get_history(offset, limit)` returns newest-first pages of 50.
- Infinite scroll appends pages.
- Filters: account (multi), action (multi), free-text over sender/subject/seller
  (client-side across loaded pages).
- Row style matches the "This run" card; click → **Open folder**.

### Fallbacks view

- Lists unresolved `fallback_log.json` entries (`resolved != true`), newest first,
  with a count badge on the tab.
- Each row expands to a form pre-filled by `fallback_ops.suggest(entry)`:
  - radio: **Make a rule** (default) / **Move this one only** / **Exclude as
    promotional** / **Skip for now**
  - fields: `seller`, `product`, `category` (select from the known category list
    + "none"), `destination root` (optional free path), and for rule/exclude:
    `match sender contains` (pre-filled with the sender domain),
    `match subject contains` (optional)
  - read-only: sender, subject, **Open folder**, **Open email.pdf**
  - a confidence hint: `low confidence — consider handling with Claude` when
    `suggest()` returns `confidence == "low"`
- **Apply** on a row calls `Api.apply_fallback(id, decision)`:
  - `rule`   → append rule to `custom_rules.json`; move + rename folder out of
    `_לטיפול ידני` to the computed destination; mark `fallback_log.json`
    resolved; update the `history.json` row to `RESOLVED`.
  - `once`   → same as `rule` minus the `custom_rules.json` write.
  - `exclude`→ append `{match_sender_contains, match_subject_contains,
    "exclude": true}` to `custom_rules.json`; delete the fallback folder; log to
    `cleanup_log.json`; mark resolved; update the `history.json` row.
  - `skip`   → no-op; row stays.
  - On success the row animates out and the badge decrements; on failure a toast
    shows the error and the row stays.
- Multi-select checkboxes + **Handle selected with Claude →**:
  `Api.handoff(ids)` → `claude_handoff.launch(entries)` opens a new terminal:
  `start "Claude — fallbacks" cmd /k claude "<prompt>"` with `cwd = SCRIPT_DIR`.
  Prompt (single line, `"` replaced with `'`):
  `handle my fallback emails — focus on these unresolved entries: [ofek] <sender> / "<subject>"; [family] ...`

### Heuristic suggestions (`fallback_ops.suggest`)

Inputs: `sender`, `subject` only (no email body is stored, no network, no AI).

1. **seller** — if the sender domain is a substring of any
   `match_sender_contains` in `custom_rules.json`, reuse that rule's `seller`
   (confidence `high`). Otherwise take the domain, strip
   `.co.il`/`.com`/`.org.il`/`.net`/`www.`, title-case (confidence `low`).
2. **product** — first subject keyword hit, in order:
   `חשבונית מס קבלה` → `חשבונית מס קבלה`; `חשבונית` → `חשבונית`;
   `קבלה`/`קבלת` → `קבלה`; `הזמנה` → `הזמנה`; `תשלום` → `אישור תשלום`;
   `כרטיס` → `כרטיסים`; `מנוי` → `מנוי`. Default `חשבונית`.
3. **category** — subject keyword: `חשמל` → `חשבנות/חשמל`; `מים`/`מיים` →
   `חשבנות/מיים`; `ארנונה` → `חשבנות/ארנונה`; `אינטרנט` → `חשבנות/אינטרנט`;
   `גז` → `חשבנות/גז`. Else `null`.
4. **exclude hint** — subject contains `פרסומת`/`הטבה`/`דיוור`/`newsletter`
   → suggested `kind` is `exclude`.
5. **confidence** — `high` if step 1 matched a known domain; `medium` if a
   non-default product or a category was found; else `low`.
6. **match_sender_contains** — the bare registered domain of the sender.

Output: `{seller, product, category, match_sender_contains, kind, confidence}`.
It is only a starting point; every field is editable in the form.

### Destination computation (rule / once)

`dest_root = base_dir or RECEIPTS_DIR`
`dest_dir  = dest_root / category` (if category) `else dest_root`
`folder_name = f"{date} - {seller} - {product} - {account}"`
Collisions resolved with `receipt_saver.unique_folder()`.
All folder contents are moved; the source folder under `_לטיפול ידני` is removed
if left empty.

---

## Window / presentation

- pywebview, `frameless=True`, `easy_drag=True`, `width=980`, `height=680`,
  centered on the primary screen, `on_top=False`.
- Custom top strip: app name on the left; minimize / close buttons on the right
  calling `Api.minimize()` / `Api.hide()` (close hides to tray, does not quit).
- Light "professional" theme: system font stack
  (`Segoe UI, -apple-system, ...`), neutral greys, one accent color, 8–12 px
  card radius, subtle shadows. No window transparency or rounded window corners
  (avoids Windows compositor quirks).
- Segmented control switches views: **This run** · **History** · **Fallbacks (N)**.
- All text that can be Hebrew renders with `dir="auto"`.

---

## Startup, tray, packaging

- `run.bat`: `start "" pythonw "%~dp0app.py"`.
- The Windows **Startup** shortcut already targets `run.bat` (per
  DOCUMENTATION.md) — no change beyond what `run.bat` now launches.
- **Start Menu shortcut**: a one-off `make_shortcut.py` (uses `pywin32` if
  present, else writes a `.url`/`.lnk` via a `WScript.Shell` COM call) creates
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Receipt Saver.lnk` → `run.bat`.
  Documented as a manual one-time step.
- Tray: `pystray` + `Pillow`. Started by `app.py`. Menu: **Open**, **Run scan
  now**, **Quit**. The process stays alive after the window hides; **Quit** ends
  it.
- New dependencies added to `requirements.txt` and DOCUMENTATION.md:
  `pywebview`, `pystray`, `Pillow`.

---

## Error handling

- Per-account auth / fetch errors: already caught in `main()`; also pushed to the
  UI as an `error` event. Run continues.
- `pywebview` import failure in `app.py`: log a clear line to `receipt_saver.log`
  (the console is hidden under `pythonw`) and exit non-zero.
- All JSON writes (`history.json`, `custom_rules.json`, `fallback_log.json`,
  `cleanup_log.json`) go through a write-temp-then-`os.replace` helper.
- `custom_rules.json` is re-parsed after every write; on parse failure the write
  is rolled back and the UI shows an error.
- One scan at a time (button + tray item disabled while running).
- `history.py` serializes all writes with a module lock (scan thread vs. UI
  thread).
- Folder move failure leaves the fallback unresolved and surfaces a toast.

---

## Testing

| Test file | Covers |
|-----------|--------|
| `test_history.py` (new) | append dedups by `id`; `update()` patches the matching row; atomic write always leaves valid JSON; paging returns newest-first. |
| `test_fallback_ops.py` (new) | `suggest()` on representative inputs (electra-power → אלקטרה פאוור + חשמל category; icount subject → seller; `פרסומת` → exclude; unknown domain → low confidence); `apply_rule()` output is matched by `receipt_saver.match_custom()`; destination path + `unique_folder` collision handling. |
| `test_receipt_saver.py` (extend) | `main(progress_cb)` invokes the callback once per handled mail; `process_message()` returns the full record dict with new fields; a history row is written per handled mail; existing `parse_date` tests unchanged. |
| `test_outlook_provider.py` | unchanged. |

Frontend (`ui/`) has no automated tests — kept small and verified manually.

---

## Out of scope

- Any Claude API call — suggestions are heuristic-only; ambiguous fallbacks go to
  a Claude Code terminal.
- Backfilling `history.json` from `receipt_saver.log` or existing folders —
  history starts empty and grows.
- Editing or reordering existing rules from the window (only appending new ones).
- A settings screen, theme switching, or multi-language UI chrome.
- Changing the scan logic, query, or folder conventions beyond the refactor
  needed to emit records and accept a progress callback.
- Scheduling scans from the UI beyond the manual **Run scan now**.
