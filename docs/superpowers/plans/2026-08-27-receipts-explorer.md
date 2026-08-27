# Receipts Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only "Receipts" tab to the startup window that browses the main receipts root and every other destination root defined across the rules.

**Architecture:** New `receipt_roots.py` discovers the root list (fixed dirs from `receipt_saver` + `base_dir`s from `custom_rules.json`) and provides a path guard. `app.Api` gets `list_roots()`, `browse(path)`, `open_path(path)` on top of it. The frontend adds one tab, one `display:flex` view (left rail of roots + breadcrumb navigator), some CSS, and JS. No scan-engine changes.

**Tech Stack:** Python 3 stdlib (`pathlib`, `os`), existing pywebview UI, pytest.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `receipt_roots.py` | create | `discover_roots()` (ordered, de-duped `{label, path}` list) and `is_within_roots()` (resolve + `commonpath` guard against traversal / outside access). |
| `test_receipt_roots.py` | create | Discovery order/dedup/labels; guard true/false incl. `..` and prefix-lookalike. |
| `app.py` | modify | `Api`: add `list_roots()`, `browse()`, `open_path()`, and private helpers `_entry()`, `_entry_sort_key` (module fn), `_crumbs()`, `_root_label()`. Import `receipt_roots`. |
| `test_app_api.py` | modify | `list_roots` shape; `browse` sorting/kinds/crumbs/size; outside-root rejection; missing-folder result. |
| `ui/index.html` | modify | 4th tab button; `#view-receipts` section; `tpl-rx-root`, `tpl-rx-row` templates. |
| `ui/app.css` | modify | `.rx-nav`, `.rx-main`, `.rx-crumbs`, `.rx-list`, `.rx-row`, `.rx-root`, `.rx-empty`; `#view-receipts.active { display:flex }`. |
| `ui/app.js` | modify | Tab wiring, `rxInit()`, `rxBrowse()`, row/crumb rendering, `#rx-open`. |
| `DOCUMENTATION.md` | modify | Add the Receipts tab to the Startup UI section + `receipt_roots.py` to the file table. |

**`browse(path)` return shape:**

```python
{
  "path": "C:\\...\\קבלות\\חשבנות",
  "label": "קבלות",                       # containing-root label
  "crumbs": [{"name": "קבלות", "path": "C:\\...\\קבלות"},
             {"name": "חשבנות", "path": "C:\\...\\קבלות\\חשבנות"}],
  "entries": [
    {"name": "2026_08_25 - סלקום - חשבונית - ofek",
     "path": "C:\\...\\2026_08_25 - סלקום - חשבונית - ofek",
     "is_dir": True, "kind": "receipt-folder", "size": None, "mtime": 1750000000.0},
    {"name": "note.pdf", "path": "C:\\...\\note.pdf",
     "is_dir": False, "kind": "pdf", "size": 20481, "mtime": 1750000000.0}
  ]
}
# error form:  {"error": "...", "path": "...", "crumbs": [...], "entries": []}
```

`kind` ∈ `folder | receipt-folder | pdf | file`.

---

## Task 1: `receipt_roots.py` — discovery + guard

**Files:**
- Create: `C:\Users\ofeks\Scripts\ReceiptSaver\receipt_roots.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_receipt_roots.py`

- [ ] **Step 1: Write the failing test**

Create `test_receipt_roots.py`:

```python
import json
import os
import tempfile
import unittest
from pathlib import Path

import receipt_roots


class TestDiscoverRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rules = self.tmp / "custom_rules.json"

    def _write(self, rules):
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    def test_fixed_roots_present_and_ordered(self):
        self._write([])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        labels = [r["label"] for r in roots]
        self.assertEqual(labels[:3], ["קבלות", "לטיפול ידני", "Japanologia"])

    def test_custom_base_dirs_appended_first_seen_order(self):
        self._write([
            {"match_sender_contains": "a.com", "base_dir": r"C:\X\נכסים"},
            {"match_sender_contains": "b.com", "base_dir": r"C:\X\נכסים\שלום שבאזי 7"},
            {"match_sender_contains": "c.com", "base_dir": r"C:\X\נכסים"},  # dup
            {"match_sender_contains": "d.com"},                            # no base_dir
        ])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        tail = [r["label"] for r in roots[3:]]
        self.assertEqual(tail, ["נכסים", "שלום שבאזי 7"])

    def test_base_dir_equal_to_receipts_dir_collapses(self):
        self._write([{"match_sender_contains": "a.com",
                      "base_dir": str(receipt_roots.RECEIPTS_DIR)}])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        paths = [os.path.normcase(os.path.normpath(r["path"])) for r in roots]
        self.assertEqual(len(paths), len(set(paths)))

    def test_unreadable_rules_falls_back_to_fixed_roots(self):
        self.rules.write_text("{ not json", encoding="utf-8")
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        self.assertEqual([r["label"] for r in roots],
                         ["קבלות", "לטיפול ידני", "Japanologia"])


class TestIsWithinRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "root").mkdir()
        (self.tmp / "root" / "sub").mkdir()
        (self.tmp / "root2").mkdir()
        self.roots = [{"label": "r", "path": str(self.tmp / "root")}]

    def test_root_itself_is_within(self):
        self.assertTrue(receipt_roots.is_within_roots(str(self.tmp / "root"), self.roots))

    def test_nested_path_is_within(self):
        self.assertTrue(receipt_roots.is_within_roots(
            str(self.tmp / "root" / "sub"), self.roots))

    def test_sibling_is_not_within(self):
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "root2"), self.roots))

    def test_parent_traversal_is_not_within(self):
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "root" / ".." / "root2"), self.roots))

    def test_prefix_lookalike_is_not_within(self):
        (self.tmp / "rootX").mkdir()
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "rootX"), self.roots))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_receipt_roots.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'receipt_roots'`

- [ ] **Step 3: Write the implementation**

Create `receipt_roots.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_receipt_roots.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add receipt_roots.py test_receipt_roots.py
git commit -m "Add receipt_roots: destination-root discovery + path guard"
```

---

## Task 2: `app.Api` — `list_roots` / `browse` / `open_path`

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\app.py`
- Test: `C:\Users\ofeks\Scripts\ReceiptSaver\test_app_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_app_api.py` (inside the file, before `if __name__`):

```python
class TestExplorerApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "קבלות"
        (self.root / "חשבנות").mkdir(parents=True)
        (self.root / "2026_08_25 - סלקום - חשבונית - ofek").mkdir()
        (self.root / "2026_01_02 - Wolt - x - family").mkdir()
        (self.root / "note.pdf").write_bytes(b"x" * 2048)
        (self.root / "aaa.txt").write_text("hi", encoding="utf-8")
        self._roots = [{"label": "קבלות", "path": str(self.root)}]

    def _api(self):
        import receipt_roots
        a = appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
        self._orig = receipt_roots.discover_roots
        receipt_roots.discover_roots = lambda rules_path=None: self._roots
        self.addCleanup(setattr, receipt_roots, "discover_roots", self._orig)
        return a

    def test_list_roots_shape(self):
        r = self._api().list_roots()
        self.assertEqual(r[0]["label"], "קבלות")
        self.assertTrue(r[0]["exists"])
        self.assertIn("path", r[0])

    def test_browse_sorts_dirs_first_dated_desc_then_files(self):
        entries = self._api().browse(str(self.root))["entries"]
        names = [e["name"] for e in entries]
        self.assertEqual(names, [
            "2026_08_25 - סלקום - חשבונית - ofek",
            "2026_01_02 - Wolt - x - family",
            "חשבנות",
            "aaa.txt",
            "note.pdf",
        ])

    def test_browse_marks_kinds_and_size(self):
        by = {e["name"]: e for e in self._api().browse(str(self.root))["entries"]}
        self.assertEqual(by["2026_08_25 - סלקום - חשבונית - ofek"]["kind"], "receipt-folder")
        self.assertEqual(by["חשבנות"]["kind"], "folder")
        self.assertEqual(by["note.pdf"]["kind"], "pdf")
        self.assertEqual(by["aaa.txt"]["kind"], "file")
        self.assertEqual(by["note.pdf"]["size"], 2048)
        self.assertIsNone(by["חשבנות"]["size"])

    def test_browse_crumbs(self):
        res = self._api().browse(str(self.root / "חשבנות"))
        self.assertEqual([c["name"] for c in res["crumbs"]], ["קבלות", "חשבנות"])
        self.assertEqual(res["crumbs"][-1]["path"], str(self.root / "חשבנות"))
        self.assertEqual(res["label"], "קבלות")

    def test_browse_rejects_path_outside_roots(self):
        res = self._api().browse(str(self.tmp / "elsewhere"))
        self.assertIn("error", res)
        self.assertEqual(res.get("entries", []), [])

    def test_browse_missing_folder_under_root(self):
        res = self._api().browse(str(self.root / "nope"))
        self.assertEqual(res["error"], "folder not found")
        self.assertEqual(res["entries"], [])
        self.assertEqual([c["name"] for c in res["crumbs"]], ["קבלות", "nope"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_app_api.py -q`
Expected: FAIL — `Api` has no attribute `list_roots`.

- [ ] **Step 3: Add the imports and helpers**

In `app.py`, add to the imports block (after `import claude_handoff`):

```python
import receipt_roots
```

Add this module-level function just above `class Api:`:

```python
_DATED_RE = __import__("re").compile(r"^(\d{4})_(\d{2})_(\d{2})")


def _entry_sort_key(e: dict):
    # dirs before files; dated dirs by date desc; then name (case-insensitive)
    is_file = 0 if e["is_dir"] else 1
    m = _DATED_RE.match(e["name"]) if e["is_dir"] else None
    dated = 0 if m else 1
    date_key = (-int(m.group(1) + m.group(2) + m.group(3))) if m else 0
    return (is_file, dated, date_key, e["name"].lower())
```

- [ ] **Step 4: Add the three methods + private helpers to `Api`**

Insert into `class Api`, right after `def open_folder(self, ...)`:

```python
    def open_path(self, path: str) -> dict:
        return self.open_folder(path)

    def list_roots(self) -> list:
        return [{**r, "exists": os.path.isdir(r["path"])}
                for r in receipt_roots.discover_roots()]

    def _root_for(self, p: Path):
        best = None
        for r in receipt_roots.discover_roots():
            rp = Path(r["path"])
            try:
                p.relative_to(rp)
            except ValueError:
                continue
            if best is None or len(str(rp)) > len(str(Path(best["path"]))):
                best = r
        return best

    def _crumbs(self, p: Path) -> list:
        root = self._root_for(p)
        if not root:
            return [{"name": p.name or str(p), "path": str(p)}]
        rp = Path(root["path"])
        crumbs = [{"name": root["label"], "path": str(rp)}]
        rel = p.relative_to(rp)
        acc = rp
        for part in rel.parts:
            acc = acc / part
            crumbs.append({"name": part, "path": str(acc)})
        return crumbs

    def _entry(self, child: Path) -> dict:
        try:
            is_dir = child.is_dir()
        except OSError:
            is_dir = False
        name = child.name
        if is_dir:
            kind = "receipt-folder" if _DATED_RE.match(name) else "folder"
        elif name.lower().endswith(".pdf"):
            kind = "pdf"
        else:
            kind = "file"
        size = mtime = None
        try:
            st = child.stat()
            mtime = st.st_mtime
            if not is_dir:
                size = st.st_size
        except OSError:
            pass
        return {"name": name, "path": str(child), "is_dir": is_dir,
                "kind": kind, "size": size, "mtime": mtime}

    def browse(self, path: str) -> dict:
        if not receipt_roots.is_within_roots(path):
            return {"error": "path is outside the known receipt roots",
                    "path": path, "crumbs": [], "entries": []}
        p = Path(path)
        crumbs = self._crumbs(p)
        root = self._root_for(p)
        label = root["label"] if root else (p.name or str(p))
        if not p.is_dir():
            return {"error": "folder not found", "path": str(p),
                    "label": label, "crumbs": crumbs, "entries": []}
        try:
            entries = [self._entry(c) for c in p.iterdir()]
        except OSError as e:
            return {"error": str(e), "path": str(p),
                    "label": label, "crumbs": crumbs, "entries": []}
        entries.sort(key=_entry_sort_key)
        return {"path": str(p), "label": label, "crumbs": crumbs, "entries": entries}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest test_app_api.py -q`
Expected: PASS (all TestApi + 6 TestExplorerApi).

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add app.py test_app_api.py
git commit -m "app.Api: list_roots / browse / open_path for the Receipts explorer"
```

---

## Task 3: Frontend — tab, view, templates, CSS

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\index.html`
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\app.css`

- [ ] **Step 1: Add the tab button**

In `ui/index.html`, in `<nav class="tabs">`, after the Fallbacks button:

```html
      <button data-view="receipts" class="tab">Receipts</button>
```

- [ ] **Step 2: Add the view section**

In `ui/index.html`, after `<section id="view-fallbacks" ...>...</section>` and before `<div id="toast-host">`:

```html
    <section id="view-receipts" class="view">
      <div class="rx-nav"></div>
      <div class="rx-main">
        <div class="rx-crumbs">
          <div class="rx-crumb-trail"></div>
          <button id="rx-open" title="Open this folder in Windows Explorer">Open in Explorer</button>
        </div>
        <div class="rx-list"></div>
        <div class="rx-empty" hidden></div>
      </div>
    </section>
```

- [ ] **Step 3: Add the templates**

In `ui/index.html`, after the `tpl-fallback` template:

```html
  <template id="tpl-rx-root">
    <button class="rx-root">
      <span class="rx-root-label" dir="auto"></span>
    </button>
  </template>

  <template id="tpl-rx-row">
    <div class="rx-row" tabindex="0">
      <span class="rx-glyph"></span>
      <span class="rx-name" dir="auto"></span>
      <span class="rx-meta"></span>
    </div>
  </template>
```

- [ ] **Step 4: Add CSS**

Append to `ui/app.css`:

```css
/* ---- Receipts explorer ---- */
#view-receipts.view.active { display: flex; gap: 0; padding: 0; }
.rx-nav {
  width: 200px; flex: none; border-inline-end: 1px solid var(--line);
  overflow-y: auto; padding: 10px; background: var(--panel);
}
.rx-root {
  display: block; width: 100%; text-align: start; border: 0; cursor: pointer;
  background: transparent; color: var(--muted); padding: 7px 9px;
  border-radius: 7px; font-size: 13px; margin-bottom: 2px;
}
.rx-root:hover { color: var(--text); background: var(--panel-2); }
.rx-root.active { color: var(--text); background: var(--panel-2); }
.rx-root.missing { opacity: .45; }

.rx-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.rx-crumbs {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
.rx-crumb-trail { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 2px; }
.rx-crumb {
  background: transparent; border: 0; color: var(--accent); cursor: pointer;
  font-size: 13px; padding: 2px 4px; border-radius: 5px;
}
.rx-crumb:hover { background: var(--panel-2); }
.rx-crumb.here { color: var(--text); cursor: default; }
.rx-sep { color: var(--muted); font-size: 12px; }
#rx-open {
  flex: none; background: var(--panel-2); color: var(--muted); border: 0;
  padding: 6px 10px; border-radius: 7px; cursor: pointer; font-size: 12px;
}
#rx-open:hover { color: var(--text); }

.rx-list { flex: 1; overflow-y: auto; padding: 8px; }
.rx-row {
  display: flex; align-items: center; gap: 10px; padding: 7px 10px;
  border-radius: 7px; cursor: default;
}
.rx-row:hover, .rx-row:focus { background: var(--panel-2); outline: none; }
.rx-row.dir { cursor: pointer; }
.rx-glyph { flex: none; width: 18px; text-align: center; }
.rx-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rx-meta { flex: none; color: var(--muted); font-size: 12px; }
.rx-empty { color: var(--muted); text-align: center; margin-top: 40px; }
```

- [ ] **Step 5: Commit**

```bash
git add ui/index.html ui/app.css
git commit -m "Receipts explorer: markup + styles"
```

---

## Task 4: Frontend — `ui/app.js` behavior

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\ui\app.js`

- [ ] **Step 1: Extend the tab switch handler**

In `ui/app.js`, find the `$$(".tab").forEach(...)` click handler and add a branch
alongside the existing `if (t.dataset.view === ...)` lines:

```js
  if (t.dataset.view === "receipts") rxInit();
```

- [ ] **Step 2: Add the explorer module**

Append to `ui/app.js` (before the final `window.addEventListener("pywebviewready", ...)`,
or after it — order does not matter):

```js
// ---- Receipts explorer -------------------------------------------------
let rxRoots = [], rxCurrent = null, rxLoaded = false;
const RX_GLYPH = { folder: "📁", "receipt-folder": "🧾", pdf: "📄", file: "▪" };

function humanSize(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
}

async function rxInit() {
  if (rxLoaded) return;
  rxLoaded = true;
  rxRoots = await api().list_roots();
  const nav = $(".rx-nav");
  nav.innerHTML = "";
  rxRoots.forEach(root => {
    const n = $("#tpl-rx-root").content.cloneNode(true);
    const btn = n.querySelector(".rx-root");
    $(".rx-root-label", n).textContent = root.label;
    if (!root.exists) btn.classList.add("missing");
    btn.title = root.path;
    btn.addEventListener("click", () => rxBrowse(root.path));
    nav.appendChild(n);
  });
  const first = rxRoots.find(r => r.exists) || rxRoots[0];
  if (first) rxBrowse(first.path);
}

async function rxBrowse(path) {
  const res = await api().browse(path);
  if (res.error && !res.crumbs) { toast(res.error, true); return; }
  rxCurrent = res.path || path;

  // rail active state
  $$(".rx-root").forEach((btn, i) => btn.classList.toggle(
    "active", rxRoots[i] && rxCurrent &&
    rxCurrent.toLowerCase().startsWith(rxRoots[i].path.toLowerCase())));

  // crumbs
  const trail = $(".rx-crumb-trail");
  trail.innerHTML = "";
  (res.crumbs || []).forEach((c, i, arr) => {
    if (i) { const s = document.createElement("span"); s.className = "rx-sep"; s.textContent = "›"; trail.appendChild(s); }
    const b = document.createElement("button");
    b.className = "rx-crumb" + (i === arr.length - 1 ? " here" : "");
    b.textContent = c.name;
    if (i < arr.length - 1) b.addEventListener("click", () => rxBrowse(c.path));
    trail.appendChild(b);
  });

  // list
  const list = $(".rx-list");
  list.innerHTML = "";
  const empty = $(".rx-empty");
  if (res.error) {
    empty.hidden = false;
    empty.textContent = res.error === "folder not found"
      ? "This folder doesn't exist yet." : res.error;
    return;
  }
  if (!res.entries.length) {
    empty.hidden = false;
    empty.textContent = "This folder is empty.";
    return;
  }
  empty.hidden = true;
  for (const e of res.entries) {
    const n = $("#tpl-rx-row").content.cloneNode(true);
    const row = n.querySelector(".rx-row");
    $(".rx-glyph", n).textContent = RX_GLYPH[e.kind] || "▪";
    $(".rx-name", n).textContent = e.name;
    $(".rx-meta", n).textContent = e.is_dir ? "" : humanSize(e.size);
    if (e.is_dir) {
      row.classList.add("dir");
      row.addEventListener("click", () => rxBrowse(e.path));
      row.addEventListener("keydown", ev => { if (ev.key === "Enter") rxBrowse(e.path); });
    } else {
      row.addEventListener("dblclick", () => api().open_path(e.path));
      row.addEventListener("keydown", ev => { if (ev.key === "Enter") api().open_path(e.path); });
    }
    list.appendChild(n);
  }
}

$("#rx-open").addEventListener("click", () => {
  if (rxCurrent) api().open_path(rxCurrent);
});
```

- [ ] **Step 3: Manual smoke test (dry-run)**

Run:
```bash
RECEIPT_SAVER_UI_DRYRUN=1 pythonw app.py
```
Verify:
- A 4th tab "Receipts" appears; clicking it shows a left rail listing `קבלות`,
  `לטיפול ידני`, `Japanologia`, and the custom roots; non-existent ones dimmed.
- The first existing root auto-opens; its folders list (dated receipt folders
  first, with the 🧾 glyph), then files with sizes.
- Clicking a folder descends; the breadcrumb grows; clicking an earlier crumb
  goes back; clicking a rail root jumps there and highlights it.
- Double-clicking a PDF opens it; "Open in Explorer" opens the current folder.
- An empty folder shows "This folder is empty."

- [ ] **Step 4: Commit**

```bash
git add ui/app.js
git commit -m "Receipts explorer: rail + breadcrumb navigation behavior"
```

---

## Task 5: Documentation

**Files:**
- Modify: `C:\Users\ofeks\Scripts\ReceiptSaver\DOCUMENTATION.md`

- [ ] **Step 1: Add `receipt_roots.py` to the Scripts Folder table**

After the `fallback_ops.py` row:

```markdown
| `receipt_roots.py` | Discovers every destination root (main `קבלות`, the fallback dir, Japanologia, and each `base_dir` in `custom_rules.json`) and guards `Api.browse` against filesystem access outside them. Backs the Receipts tab. |
```

- [ ] **Step 2: Add the Receipts view to the Startup UI section**

In the "Three views" table in the `## Startup UI (app.py)` section, change the
heading to "Four views" and add a row:

```markdown
| **Receipts** | Read-only explorer. Left rail lists every destination root (`receipt_roots.discover_roots`); the right pane is a breadcrumb navigator over the selected root. Click a folder to descend, a crumb to go back, double-click a file to open it, or **Open in Explorer** for the current folder. No writes. |
```

- [ ] **Step 3: Commit**

```bash
git add DOCUMENTATION.md
git commit -m "Document the Receipts explorer tab"
```

---

## Self-Review Notes

**Spec coverage:**
- Roots = main + fallback dir + Japanologia + custom `base_dir`s → Task 1 `discover_roots` (`_FIXED` + rule loop). ✓
- Read-only navigator, rail + breadcrumb, click-to-descend, dblclick-to-open, Open-in-Explorer → Task 3 markup + Task 4 JS. ✓
- New "Receipts" tab → Task 3 Step 1. ✓
- Path guard (no FS access outside roots) → Task 1 `is_within_roots`, enforced in Task 2 `browse`. ✓
- `kind` classification incl. receipt-folder → Task 2 `_entry` + `_DATED_RE`. ✓
- Sort: dirs first, dated desc, then files → Task 2 `_entry_sort_key` + test. ✓
- Error/missing-folder/empty states → Task 2 `browse` branches + Task 4 `rxBrowse` rendering. ✓
- Tests → Task 1 `test_receipt_roots.py`, Task 2 `test_app_api.py` additions. ✓

**Placeholder scan:** none — every step has full code or an exact command.

**Type consistency:** `browse` return keys (`path`, `label`, `crumbs`, `entries`, `error`) identical across Task 2 impl, Task 2 tests, Task 4 consumer. Entry keys (`name`, `path`, `is_dir`, `kind`, `size`, `mtime`) identical across `_entry`, tests, `rxBrowse`. `list_roots` item keys (`label`, `path`, `exists`) identical across Task 2, Task 4 `rxInit`. `_DATED_RE` defined once in `app.py` (Task 2 Step 3), reused in `_entry`. JS calls only methods defined on `Api`: `list_roots`, `browse`, `open_path` (+ existing `open_folder` via `open_path` delegate).
