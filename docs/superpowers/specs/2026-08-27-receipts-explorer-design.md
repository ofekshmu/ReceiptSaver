# Receipts Explorer — Design Spec

**Date:** 2026-08-27

## Overview

Add a **Receipts** tab to the startup window: a read-only file explorer for the
main receipts root and every other destination root defined across the rules
(custom-rule `base_dir`s + the fixed special dirs). A left rail lists the roots;
a breadcrumb navigator on the right shows the current folder's contents. Folders
are clickable to descend; files open in their default app; a button opens the
current folder in Windows Explorer. Nothing is ever written, renamed, moved, or
deleted from this view.

---

## Architecture

New module `receipt_roots.py` owns root discovery and the path guard. `app.Api`
gains three thin methods over it. The frontend adds one tab, one view, and a
small amount of CSS/JS. No change to the scan engine.

### `receipt_roots.py`

```python
RECEIPTS_DIR   = receipt_saver.RECEIPTS_DIR
MANUAL_DIR     = receipt_saver.MANUAL_DIR
JAPANOLOGIA_DIR = receipt_saver.JAPANOLOGIA_DIR
CUSTOM_RULES_FILE = receipt_saver.CUSTOM_RULES_FILE

def discover_roots(rules_path: Path = None) -> list[dict]:
    """Ordered, de-duplicated list of {label, path} (path as str, normalized).
    Order: main receipts, fallback dir, Japanologia, then custom base_dirs in
    first-seen order. Duplicates (same normalized path) collapse to the first."""

def is_within_roots(path: str, roots: list[dict] = None) -> bool:
    """True iff the fully-resolved `path` equals or is nested under one of the
    roots. Resolves symlinks/.. first, compares with os.path.commonpath.
    Used to gate Api.browse — JS can never walk outside a root."""
```

**Labels:**

| Source | Label |
|--------|-------|
| `RECEIPTS_DIR` | `קבלות` |
| `MANUAL_DIR` | `לטיפול ידני` |
| `JAPANOLOGIA_DIR` | `Japanologia` |
| a custom-rule `base_dir` | its last path segment (e.g. `נכסים`, `שלום שבאזי 7`, `מילואים`, `משכורות`) |

If two roots resolve to the same path, the first wins (so `MANUAL_DIR`, which is
under `RECEIPTS_DIR`, still appears as its own entry because its path differs).
A custom `base_dir` equal to `RECEIPTS_DIR` would collapse into the main entry.

### `app.Api` additions

```python
def list_roots(self) -> list:
    # [{label, path, exists}] — exists = os.path.isdir(path) at call time
    return [{**r, "exists": os.path.isdir(r["path"])}
            for r in receipt_roots.discover_roots()]

def browse(self, path: str) -> dict:
    # {path, label, crumbs, entries}  or  {error: "..."}
    if not receipt_roots.is_within_roots(path):
        return {"error": "path is outside the known receipt roots"}
    p = Path(path)
    if not p.is_dir():
        return {"error": "folder not found", "path": str(p),
                "crumbs": self._crumbs(p), "entries": []}
    try:
        entries = [self._entry(c) for c in p.iterdir()]
    except OSError as e:
        return {"error": str(e), "path": str(p), "crumbs": self._crumbs(p), "entries": []}
    entries.sort(key=_entry_sort_key)
    return {"path": str(p), "label": self._root_label(p),
            "crumbs": self._crumbs(p), "entries": entries}

def open_path(self, path: str) -> dict:
    # os.startfile on a file OR folder; same body as open_folder
```

**`_entry(child: Path)`** → `{name, path, is_dir, kind, size, mtime}`:

- `kind`: `"receipt-folder"` if `is_dir` and `name` matches `^\d{4}_\d{2}_\d{2} - `;
  else `"folder"` if dir; else `"pdf"` if suffix `.pdf` (case-insensitive);
  else `"file"`.
- `size`: bytes for files, `None` for dirs.
- `mtime`: `os.path.getmtime` as a float (epoch seconds); the UI formats it.
- Unreadable child (`OSError` on `stat`) → still listed with `size=None,
  mtime=None`.

**`_entry_sort_key`**: dirs before files; within dirs, names matching
`^\d{4}_\d{2}_\d{2}` sort by that date descending, then remaining dirs
alphabetical (case-insensitive); files alphabetical.

**`_crumbs(p: Path)`**: from the containing root down to `p`, as
`[{name, path}]`. The first crumb's `name` is the root label; deeper crumbs use
the folder name. If `p` is not under any root (shouldn't happen post-guard),
return a single crumb for `p` itself.

**`_root_label(p)` / containing root**: the root whose path is a prefix of `p`
(longest match wins, so `שלום שבאזי 7` beats `נכסים` for a nested path).

### Frontend

`ui/index.html`:
- 4th tab button: `<button data-view="receipts" class="tab">Receipts</button>`
- `<section id="view-receipts" class="view">` containing:
  - `<div class="rx-nav">` — filled at runtime with one `.rx-root` button per
    root (`.rx-root.active` for the open one, `.rx-root.missing` dimmed for
    `exists:false`).
  - `<div class="rx-main">` with `<div class="rx-crumbs">` (segments +
    right-aligned `#rx-open` "Open in Explorer" button) and `<div class="rx-list">`.
- Two `<template>`s: `tpl-rx-root`, `tpl-rx-row`.

`ui/app.js`:
- On first switch to the Receipts tab: `await api.list_roots()`, render the rail,
  auto-open the first existing root.
- `rxBrowse(path)`: `const r = await api.browse(path)`; render crumbs (each
  segment a button → `rxBrowse(seg.path)`), render rows. On `r.error` with no
  entries, show an inline `.rx-empty` message (`folder not found` etc.) and still
  render crumbs. Toast on hard errors.
- Row render (`tpl-rx-row`): glyph by `kind` (`📁` folder, `🧾` receipt-folder,
  `📄` pdf, `▪` file), `name`, and a meta column: humanized size for files,
  empty for folders (no child-count — avoids a stat per child). Click a dir row
  → `rxBrowse(row.path)`. `dblclick` a file row → `api.open_path(row.path)`.
- `#rx-open` → `api.open_path(currentPath)`.
- `.rx-root` click → `rxBrowse(root.path)` and move `.active`.
- Track `currentPath` in a module var; re-render rail `.active` against it.

`ui/app.css`: `.rx-nav` fixed-width left column (scrolls), `.rx-main` flex
column, `.rx-crumbs` a wrap row, `.rx-list` scroll area, `.rx-row` flex row
(glyph · name · meta) with hover, `.rx-root` full-width button, `.rx-root.missing
{ opacity:.45 }`, `.rx-empty` muted centered. `#view-receipts.view.active {
display:flex }` and the view becomes `display:flex` (rail + main side by side).

---

## Data flow

```
open "Receipts" tab
  → api.list_roots()                → render .rx-nav, pick first existing root
  → api.browse(root.path)           → render .rx-crumbs + .rx-list
click folder row  → api.browse(child.path)      → re-render
click crumb       → api.browse(crumb.path)      → re-render
dblclick file     → api.open_path(file.path)    → OS opens it
"Open in Explorer" → api.open_path(currentPath) → Explorer at the folder
```

All read-only. `browse` is the only new data path and it is guarded by
`is_within_roots`.

---

## Error handling

| Situation | Behavior |
|-----------|----------|
| `path` not under any root | `{error}` → toast; view unchanged |
| root/folder does not exist on disk | `{error:"folder not found", entries:[]}` → `.rx-empty` inline, crumbs still shown; rail entry rendered `.missing` |
| `iterdir()` / `stat()` raises `OSError` | caught; folder shown empty or entry listed with null size/mtime; toast for the folder-level failure |
| `custom_rules.json` unreadable | `discover_roots` falls back to the fixed roots only (same `try/except` pattern as elsewhere) |
| empty folder | `.rx-empty` "This folder is empty." |

---

## Testing

| Test file | Covers |
|-----------|--------|
| `test_receipt_roots.py` (new) | `discover_roots` returns fixed roots + rule `base_dir`s, de-duplicated, in the documented order; custom `base_dir` equal to `RECEIPTS_DIR` collapses; labels are the last path segment. `is_within_roots`: true for a root and a nested path; false for a sibling dir, for `<root>\..\secret`, and for a path whose string prefix matches a root but is a different dir (`קבלות2`). |
| `test_app_api.py` (extend) | `list_roots()` items have `label/path/exists`. `browse()` on a temp tree containing a `2026_08_25 - X - Y - ofek` dir + a `foo.pdf`: dirs sort before files, the dated dir is `kind=="receipt-folder"`, `foo.pdf` is `kind=="pdf"` with a numeric `size`, `crumbs[0].name` is the root label, `crumbs[-1].path == path`. `browse()` on a path outside every root → `{"error": ...}` and no `entries`. `browse()` on a non-existent child of a root → `error=="folder not found"`, `entries==[]`, crumbs present. |

Frontend (`ui/`) verified manually via `RECEIPT_SAVER_UI_DRYRUN=1`.

---

## Out of scope

- Any write operation (rename, move, delete, new folder).
- Search / filter within the explorer.
- File previews inside the window (double-click hands off to the OS).
- Recursive folder counts or a full expandable tree in the rail.
- Watching the filesystem for live updates — the list refreshes when you
  navigate or re-open the tab.
- Roots not derivable from the code or `custom_rules.json`.
