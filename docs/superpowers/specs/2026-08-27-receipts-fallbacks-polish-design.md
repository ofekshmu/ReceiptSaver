# Receipts + Fallbacks polish — Design Spec

**Date:** 2026-08-27

Seven improvements to the startup window, grouped by area.

---

## A. Persistent UI state — `ui_state.py` / `ui_state.json`

New module mirroring `history.py` (atomic temp-file write, module `threading.Lock`).

```python
UI_STATE_FILE = SCRIPT_DIR / "ui_state.json"
DEFAULTS = {"hidden_roots": [], "fallbacks_simple": False}

def load(path=None) -> dict          # DEFAULTS merged with the file; missing/corrupt -> DEFAULTS copy
def save(patch: dict, path=None) -> dict   # merge patch into current, atomic-write, return the merged dict
```

`ui_state.json` added to `.gitignore` (runtime state, like `history.json`).

`app.Api` gains:

```python
def get_ui_state(self) -> dict:            return ui_state.load()
def set_ui_state(self, patch: dict) -> dict: return ui_state.save(patch or {})
```

`hidden_roots` entries are stored normcased (`os.path.normcase(os.path.normpath(p))`)
so comparison is stable.

---

## B. Receipts — hide / unhide roots

- `Api.list_roots()` is unchanged (returns every root).
- The frontend partitions the roots into **visible** and **hidden** by testing
  `normcase(root.path)` against `get_ui_state().hidden_roots`.
- `.rx-nav` renders: the visible roots, then (only if any are hidden) a
  `<div class="rx-nav-divider">Hidden</div>` and the hidden roots with a
  `.rx-root.hidden-root` class (dimmed).
- Each root row has a trailing `<button class="rx-root-toggle">` shown on
  hover/focus: `⊘` on a visible root ("Hide"), `＋` on a hidden root ("Unhide").
  Clicking it:
  1. toggles that path in a local `hiddenSet`,
  2. `await api.set_ui_state({hidden_roots: [...hiddenSet]})`,
  3. re-renders the rail (without changing which folder is open).
- If the currently-open root is hidden, its contents stay on screen; only its
  rail entry moves to the hidden section (still gets `.active` if it matches).
- Clicking a hidden root still navigates into it normally.

---

## C. Receipts — boxed entries

CSS only. Each `.rx-row` becomes a shaded box:

```css
.rx-row {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 6px;
}
.rx-row:hover, .rx-row:focus { background: #232834; }   /* one step lighter */
```

`.rx-list` keeps its padding; the `gap`/`margin-bottom` separates rows.

---

## D. Receipts — search bar (recursive, all roots)

**Markup:** an `<input id="rx-search" type="search">` in `.rx-main`, above
`.rx-crumbs`.

**`Api.search_receipts(query: str, limit: int = 200) -> dict`:**

```python
{
  "query": "...",
  "results": [
    {"name": "...", "path": "...", "is_dir": True,
     "kind": "receipt-folder", "root_label": "קבלות", "rel": "חשבנות\\חשמל\\..."}
  ],
  "truncated": False
}
```

- `q = query.strip().lower()`; if `len(q) < 2` → `{"query": query, "results": [], "truncated": False}`.
- For each root from `receipt_roots.discover_roots()` where `os.path.isdir(root)`:
  - walk with `os.walk(root)`, but prune (`dirs[:] = []`) once
    `depth > 6` where `depth` = separators in the path relative to the root.
  - a **dir** whose name contains `q` is a result; do **not** descend into it
    (`dirs.remove(d)` after recording) — the folder itself is the hit.
  - a **file** whose name contains `q` is a result.
  - `kind` via the same rules as `Api._entry` (`receipt-folder` for
    `^\d{4}_\d{2}_\d{2} - ` dirs, `pdf`, `folder`, `file`).
  - `rel` = `os.path.relpath(path, root)`.
  - stop the whole walk once `len(results) >= limit`; set `truncated = True`.
- Sort results: dirs before files, then `root_label`, then `rel` (case-insensitive).
- Results are root-scoped by construction; no extra guard needed.

**Frontend:**
- `#rx-search` input, 200 ms debounce.
- Non-empty (`>= 2` chars): `const r = await api.search_receipts(q)`; render a
  flat list into `.rx-list` reusing `.rx-row` — glyph by `kind`, `name`, and a
  muted second line `root_label / rel`. `.rx-crumbs` trail is replaced by
  `Search "<q>" — <n> result(s)` (+ ` (first 200)` if `truncated`).
  Click a dir result → `rxBrowse(result.path)` **and clear the search box**.
  Click a file result → `api.open_path(result.path)`.
- Cleared / `< 2` chars: restore `rxBrowse(rxCurrent)`.
- `#rx-open` still targets `rxCurrent` (unchanged) and is hidden while searching.

---

## E. "This run" — guaranteed terminal state + message

**`app.Api._run_scan`** wraps the scan in `try / finally`:

```python
def _run_scan(self, run_id):
    summary = {"run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []}
    try:
        summary = self._scan_fn(run_id=run_id, progress_cb=self._push) or summary
        self._run["status"] = "done"
    except Exception as e:
        self._run["status"] = "error"
        self._push({"type": "error", "label": "-", "message": str(e)})
    finally:
        self._run["summary"] = summary
        if not any(e.get("type") == "done" for e in self._run["events"]):
            self._push({"type": "done",
                        "run_id": summary.get("run_id", run_id),
                        "saved": summary.get("saved", 0),
                        "fallback": summary.get("fallback", 0),
                        "excluded": summary.get("excluded", 0),
                        "status": self._run["status"]})
```

So a `done` event is **always** emitted exactly once, even if `scan_fn`
raises or returns `None`.

**Frontend `onScanEvent` — `done` branch** sets `#run-summary` to a final sentence:

| Condition | Message |
|-----------|---------|
| `status === "error"` | `Scan stopped early — see errors above.` |
| `saved > 0` | `Scan complete — {saved} new receipt(s) saved` + (`fallback` ? ` · {fallback} need review` : ``) + (`excluded` ? ` · {excluded} skipped` : ``) + `.` |
| `saved === 0 && fallback > 0` | `Scan complete — no new receipts; {fallback} need review.` |
| all zero | `Scan complete — no new mail found.` |

**`syncRun()`** — called when the `This run` tab is activated: reads
`api.get_run()`; if `status` is `done`/`error` and `#run-summary` still starts
with `Scanning`, render the message from the stored `summary`/last `done` event.
Covers a dropped `evaluate_js`.

The per-account progress line stays `Scanning {label}… {n} candidates` **only
while running**; the `done` handler overwrites it.

---

## F. Fallbacks — simple / detailed view toggle

- `.fb-toolbar` gets `<button id="fb-viewtoggle">`. Label reflects state:
  `Simple view` when detailed, `Detailed view` when simple.
- State persisted: `ui_state.fallbacks_simple` (bool). Read on `loadFallbacks()`,
  written on toggle.
- **Detailed** (current): `fallbackCard(it)` → full card + form for every entry.
- **Simple**: `fallbackCompact(it)` → a `.card.fb-compact` row:
  - `.card-title` = subject
  - `.card-sub` = `sender · account · date`
  - `.conf` confidence chip
  - `Open folder` link
  - `.fb-check` select checkbox
  - a `.fb-expand` caret button
  Clicking the row body or `.fb-expand` expands **that entry**: the shared
  form is built into a `.fb-form-slot` under the row (lazy — `suggest()` fetched
  on first expand). Collapsing empties the slot. `Apply` works exactly as now and
  removes the row.
- Refactor: split `fallbackCard` into
  - `renderForm(slotEl, it, suggestion)` — builds the `<form>` (the body of
    today's `fallbackCard` from the `<form>` onward, including the submit
    handler),
  - `fallbackCard(it)` (detailed) — compact header bits + `renderForm` into an
    always-open slot,
  - `fallbackCompact(it)` (simple) — the row + collapsed slot + expand wiring.
- `#fb-handoff` multi-select works in both views (checkbox present in both).
- Toggling re-runs `loadFallbacks()` which picks the renderer by state.

---

## G. Window drag — fix the constant offset

**Cause:** in this webview backend `MouseEvent.screenX/screenY` is measured
relative to the window, not the screen. The old handler computed
`window_left = screenX - grabClientX`; on the first move that collapses to ≈0
(window jumps to the top-left) and thereafter tracks the cursor at a fixed
offset.

**Fix — never use `screenX`:**

1. `app.main()` computes an explicit start position and passes it to
   `create_window`:

   ```python
   try:
       import webview
       scr = webview.screens[0]
       win_x = max(0, (scr.width  - 980) // 2)
       win_y = max(0, (scr.height - 680) // 2)
   except Exception:
       win_x, win_y = 120, 120
   window = webview.create_window(..., x=win_x, y=win_y, ...)
   api._win_x, api._win_y = win_x, win_y
   ```

2. `Api` replaces `move_window(x, y)` with **`move_by(dx, dy)`**:

   ```python
   def _ensure_pos(self):
       if self._win_x is None or self._win_y is None:
           try:
               import webview
               scr = webview.screens[0]
               self._win_x = max(0, (scr.width  - int(self._window.width))  // 2)
               self._win_y = max(0, (scr.height - int(self._window.height)) // 2)
           except Exception:
               self._win_x, self._win_y = 120, 120

   def move_by(self, dx, dy):
       if not self._window:
           return
       self._ensure_pos()
       self._win_x += int(dx)
       self._win_y += int(dy)
       try:
           self._window.move(self._win_x, self._win_y)
       except Exception:
           pass
   ```

   `_win_x/_win_y` initialised to `None` in `__init__`.

3. `ui/app.js` drag handler: on `pointerdown` only `setPointerCapture` + set a
   flag (no move). On `pointermove` accumulate `e.movementX` / `e.movementY`
   (origin-independent deltas). rAF-flush `api().move_by(dx, dy)` and zero the
   accumulator. On `pointerup`/`pointercancel` release capture. Because nothing
   moves on pointer-down, the click-jump is gone; because deltas are relative,
   there is no drift.

---

## Testing

| Test file | Covers |
|-----------|--------|
| `test_ui_state.py` (new) | `load()` returns DEFAULTS when the file is absent or corrupt; `save()` merges a partial patch and persists; a second `save()` sees the first; the temp file is gone after write. |
| `test_app_api.py` (extend) | `get_ui_state()` has `hidden_roots` + `fallbacks_simple`. `set_ui_state({"fallbacks_simple": True})` returns/persists the merge, keeping `hidden_roots`. `search_receipts("סלקום")` over two temp roots each holding a nested `2026_08_25 - סלקום - …` dir finds both, `kind=="receipt-folder"`, `root_label`/`rel` set, dirs not descended. `search_receipts` respects `limit` and sets `truncated`. `search_receipts("a")` (1 char) → empty. `_run_scan` with a `scan_fn` that raises still leaves exactly one `done` event and `status=="error"`; with a normal `scan_fn` exactly one `done`. |

Frontend verified manually via `RECEIPT_SAVER_UI_DRYRUN=1` and the local
`ui/` http-server harness.

---

## Out of scope

- Search inside file contents (names only).
- Reordering roots in the rail.
- Remembering the last-open folder across app restarts.
- Any write/rename/delete in the explorer (still read-only).
- A settings screen — `ui_state.json` is edited only through these toggles.
