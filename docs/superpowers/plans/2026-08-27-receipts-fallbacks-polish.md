# Receipts + Fallbacks Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hide-roots + recursive search + boxed rows to the Receipts tab, a simple/detailed toggle to the Fallbacks tab, a definite end-of-scan message on the This-run tab, and fix the window-drag offset.

**Architecture:** New `ui_state.py` (atomic JSON store like `history.py`) persists `hidden_roots` and `fallbacks_simple`. `app.Api` gains `get_ui_state` / `set_ui_state` / `search_receipts`, a `try/finally` in `_run_scan` that guarantees one terminal `done` event, and a relative `move_by(dx,dy)` driving a Python-tracked window position. The frontend consumes all of it.

**Tech Stack:** Python 3 stdlib, existing pywebview UI, pytest.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `ui_state.py` | create | `load()` / `save(patch)` over `ui_state.json`; atomic write + lock; `DEFAULTS = {"hidden_roots": [], "fallbacks_simple": False}`. |
| `test_ui_state.py` | create | defaults / merge / persistence / atomicity. |
| `app.py` | modify | `Api`: `get_ui_state`, `set_ui_state`, `search_receipts`; `_run_scan` `try/finally`; `move_by` + `_ensure_pos` (replaces `move_window`); `main()` passes explicit `x/y`. Import `ui_state`. |
| `test_app_api.py` | modify | ui_state methods; `search_receipts`; `_run_scan` terminal-event guarantee. |
| `ui/index.html` | modify | `#rx-search` input; `#fb-viewtoggle` button; `tpl-fb-compact` template; `rx-root-toggle` inside `tpl-rx-root`. |
| `ui/app.css` | modify | boxed `.rx-row`; `.rx-nav-divider`; `.rx-root-toggle`; `#rx-search`; `.fb-compact` / `.fb-form-slot`. |
| `ui/app.js` | modify | drag rewrite; hidden-roots split + toggle; `rxSearch`; run terminal message + `syncRun`; simple/detailed fallbacks with shared `renderForm`. |
| `.gitignore` | modify | add `ui_state.json`. |
| `DOCUMENTATION.md` | modify | document all six. |

---

## Task 1: `ui_state.py`

**Files:** Create `ui_state.py`, `test_ui_state.py`.

- [ ] **Step 1: Failing test** — create `test_ui_state.py`:

```python
import json, tempfile, unittest
from pathlib import Path
import ui_state


class TestUiState(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / "ui_state.json"

    def test_load_defaults_when_absent(self):
        s = ui_state.load(path=self.p)
        self.assertEqual(s, {"hidden_roots": [], "fallbacks_simple": False})

    def test_load_defaults_when_corrupt(self):
        self.p.write_text("{bad", encoding="utf-8")
        self.assertEqual(ui_state.load(path=self.p)["fallbacks_simple"], False)

    def test_save_merges_partial_patch(self):
        ui_state.save({"fallbacks_simple": True}, path=self.p)
        s = ui_state.load(path=self.p)
        self.assertTrue(s["fallbacks_simple"])
        self.assertEqual(s["hidden_roots"], [])

    def test_second_save_sees_first(self):
        ui_state.save({"hidden_roots": ["c:\\x"]}, path=self.p)
        merged = ui_state.save({"fallbacks_simple": True}, path=self.p)
        self.assertEqual(merged["hidden_roots"], ["c:\\x"])
        self.assertTrue(merged["fallbacks_simple"])

    def test_write_is_atomic(self):
        ui_state.save({"fallbacks_simple": True}, path=self.p)
        json.loads(self.p.read_text(encoding="utf-8"))
        self.assertFalse(self.p.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: ui_state`).
  `python -m pytest test_ui_state.py -q`

- [ ] **Step 3: Implement** — create `ui_state.py`:

```python
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
```

- [ ] **Step 4: Run — expect PASS** (5 passed).

- [ ] **Step 5: `.gitignore`** — add `ui_state.json` under the `# Runtime state` block (after `history.json`).

- [ ] **Step 6: Commit**

```bash
git add ui_state.py test_ui_state.py .gitignore
git commit -m "Add ui_state.py: persisted window UI preferences"
```

---

## Task 2: `app.Api` — ui_state, search, scan-end, drag position

**Files:** Modify `app.py`, `test_app_api.py`.

- [ ] **Step 1: Failing tests** — append to `test_app_api.py` before `if __name__`:

```python
class TestUiStateApi(unittest.TestCase):
    def setUp(self):
        import ui_state
        self.p = Path(tempfile.mkdtemp()) / "ui_state.json"
        self._orig = ui_state.UI_STATE_FILE
        ui_state.UI_STATE_FILE = self.p
        self.addCleanup(setattr, ui_state, "UI_STATE_FILE", self._orig)

    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})

    def test_get_ui_state_default_shape(self):
        s = self._api().get_ui_state()
        self.assertIn("hidden_roots", s)
        self.assertIn("fallbacks_simple", s)

    def test_set_ui_state_merges_and_persists(self):
        api = self._api()
        api.set_ui_state({"fallbacks_simple": True})
        api.set_ui_state({"hidden_roots": ["c:\\x"]})
        s = api.get_ui_state()
        self.assertTrue(s["fallbacks_simple"])
        self.assertEqual(s["hidden_roots"], ["c:\\x"])


class TestSearchReceipts(unittest.TestCase):
    def setUp(self):
        import receipt_roots
        self.tmp = Path(tempfile.mkdtemp())
        self.r1 = self.tmp / "קבלות"
        self.r2 = self.tmp / "נכסים"
        (self.r1 / "חשבנות" / "2026_08_25 - סלקום - חשבונית - ofek").mkdir(parents=True)
        (self.r2 / "2026_07_01 - סלקום - חשבונית - family").mkdir(parents=True)
        (self.r1 / "readme_סלקום.txt").write_text("x", encoding="utf-8")
        self._roots = [{"label": "קבלות", "path": str(self.r1)},
                       {"label": "נכסים", "path": str(self.r2)}]
        self._orig = receipt_roots.discover_roots
        receipt_roots.discover_roots = lambda rules_path=None: self._roots
        self.addCleanup(setattr, receipt_roots, "discover_roots", self._orig)

    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: None)

    def test_finds_matches_across_all_roots(self):
        res = self._api().search_receipts("סלקום")
        names = sorted(r["rel"] for r in res["results"])
        self.assertIn(os.path.join("חשבנות", "2026_08_25 - סלקום - חשבונית - ofek"), names)
        self.assertIn("2026_07_01 - סלקום - חשבונית - family", names)
        kinds = {r["kind"] for r in res["results"] if r["is_dir"]}
        self.assertEqual(kinds, {"receipt-folder"})
        self.assertEqual({r["root_label"] for r in res["results"]}, {"קבלות", "נכסים"})

    def test_short_query_returns_empty(self):
        self.assertEqual(self._api().search_receipts("a")["results"], [])

    def test_limit_and_truncated_flag(self):
        res = self._api().search_receipts("סלקום", limit=1)
        self.assertEqual(len(res["results"]), 1)
        self.assertTrue(res["truncated"])


class TestRunScanTerminates(unittest.TestCase):
    def _run(self, scan_fn):
        api = appmod.Api(scan_fn=scan_fn)
        api._run = {"status": "running", "events": [], "summary": None}
        api._run_scan("RID")
        return api._run

    def test_done_emitted_even_when_scan_raises(self):
        def boom(run_id, progress_cb):
            raise RuntimeError("nope")
        run = self._run(boom)
        dones = [e for e in run["events"] if e["type"] == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(run["status"], "error")

    def test_single_done_on_normal_scan(self):
        def ok(run_id, progress_cb):
            return {"run_id": run_id, "saved": 2, "fallback": 0, "excluded": 0, "records": []}
        run = self._run(ok)
        dones = [e for e in run["events"] if e["type"] == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["saved"], 2)
```

- [ ] **Step 2: Run — expect FAIL** (`Api` has no `get_ui_state` etc.).

- [ ] **Step 3: `app.py` imports + `_run_scan`**

Add `import ui_state` after `import receipt_roots`.

Replace the whole `def _run_scan(self, run_id):` method with:

```python
    def _run_scan(self, run_id: str):
        summary = {"run_id": run_id, "saved": 0, "fallback": 0,
                   "excluded": 0, "records": []}
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

- [ ] **Step 4: `app.py` — window position + `move_by`**

In `Api.__init__`, add after `self._window = None`:

```python
        self._win_x = None
        self._win_y = None
```

Replace the `move_window` method with:

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
        """Relative window move. The page sends origin-independent pointer
        deltas (movementX/movementY); we keep the absolute position here so we
        never depend on the webview's screenX (which is window-relative on this
        backend and caused the drag to jump)."""
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

In `def main()`, replace the `window = webview.create_window(...)` call with:

```python
    try:
        scr = webview.screens[0]
        win_x = max(0, (scr.width  - 980) // 2)
        win_y = max(0, (scr.height - 680) // 2)
    except Exception:
        win_x, win_y = 120, 120

    api = Api() if os.environ.get("RECEIPT_SAVER_UI_DRYRUN") != "1" else Api(
        scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
    window = webview.create_window(
        "Receipt Saver",
        url=str(UI_DIR / "index.html"),
        js_api=api,
        width=980, height=680, x=win_x, y=win_y,
        frameless=True, easy_drag=False,
        background_color="#0f1115",
    )
    api._win_x, api._win_y = win_x, win_y
    api.bind(window)
```

(Delete the now-duplicated earlier `api = Api()...` / `window = ...` / `api.bind(window)` lines that this replaces — keep a single copy.)

- [ ] **Step 5: `app.py` — ui_state + search methods**

Add to `class Api` after `categories()`:

```python
    # -- ui state ---------------------------------------------------------
    def get_ui_state(self) -> dict:
        return ui_state.load()

    def set_ui_state(self, patch: dict) -> dict:
        return ui_state.save(patch or {})

    # -- receipts search ------------------------------------------------
    def search_receipts(self, query: str, limit: int = 200) -> dict:
        q = (query or "").strip().lower()
        if len(q) < 2:
            return {"query": query, "results": [], "truncated": False}
        results, truncated = [], False
        for root in receipt_roots.discover_roots():
            rp = root["path"]
            if not os.path.isdir(rp):
                continue
            base_depth = rp.rstrip("\\/").count(os.sep)
            for cur, dirs, files in os.walk(rp):
                if cur.count(os.sep) - base_depth > 6:
                    dirs[:] = []
                    continue
                for d in list(dirs):
                    if q in d.lower():
                        full = os.path.join(cur, d)
                        results.append(self._search_hit(full, True, root["label"], rp))
                        dirs.remove(d)
                for f in files:
                    if q in f.lower():
                        results.append(self._search_hit(os.path.join(cur, f), False,
                                                        root["label"], rp))
                if len(results) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        results.sort(key=lambda r: (0 if r["is_dir"] else 1,
                                    r["root_label"].lower(), r["rel"].lower()))
        return {"query": query, "results": results[:limit], "truncated": truncated}

    def _search_hit(self, full: str, is_dir: bool, root_label: str, root_path: str) -> dict:
        name = os.path.basename(full)
        if is_dir:
            kind = "receipt-folder" if _DATED_RE.match(name) else "folder"
        elif name.lower().endswith(".pdf"):
            kind = "pdf"
        else:
            kind = "file"
        return {"name": name, "path": full, "is_dir": is_dir, "kind": kind,
                "root_label": root_label, "rel": os.path.relpath(full, root_path)}
```

- [ ] **Step 6: Run — expect PASS**

`python -m pytest test_app_api.py test_ui_state.py -q`  → all pass.

- [ ] **Step 7: Full suite** — `python -m pytest -q` → all green.

- [ ] **Step 8: Commit**

```bash
git add app.py test_app_api.py
git commit -m "app.Api: ui_state, search_receipts, guaranteed scan-end, relative move_by"
```

---

## Task 3: Frontend — window drag rewrite

**Files:** Modify `ui/app.js`.

- [ ] **Step 1: Replace the `enableDrag` IIFE** (the whole `// ---- window dragging` block) with:

```js
// ---- window dragging -------------------------------------------------
// This webview reports screenX/Y window-relative, so absolute math drifts.
// Use origin-independent pointer deltas and let Python keep the position.
(function enableDrag() {
  const bar = $(".titlebar");
  let dragging = false, dx = 0, dy = 0, raf = 0;
  const flush = () => {
    raf = 0;
    if (dx || dy) { api().move_by(Math.round(dx), Math.round(dy)); dx = dy = 0; }
  };
  bar.addEventListener("pointerdown", e => {
    if (e.button !== 0 || e.target.closest("button, .tab")) return;
    dragging = true; dx = dy = 0;
    try { bar.setPointerCapture(e.pointerId); } catch (_) {}
  });
  bar.addEventListener("pointermove", e => {
    if (!dragging) return;
    dx += e.movementX; dy += e.movementY;
    if (!raf) raf = requestAnimationFrame(flush);
  });
  const end = e => {
    dragging = false;
    try { bar.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
})();
```

- [ ] **Step 2: Manual check** — `RECEIPT_SAVER_UI_DRYRUN=1 pythonw app.py`; grab the
  title bar: window follows the cursor with no initial jump and no constant
  offset; releasing stops it.

- [ ] **Step 3: Commit**

```bash
git add ui/app.js
git commit -m "Fix window drag: relative pointer deltas, no screenX"
```

---

## Task 4: Receipts — hidden roots + boxed rows

**Files:** Modify `ui/index.html`, `ui/app.css`, `ui/app.js`.

- [ ] **Step 1: `ui/index.html`** — replace the `tpl-rx-root` template with:

```html
  <template id="tpl-rx-root">
    <div class="rx-root-wrap">
      <button class="rx-root"><span class="rx-root-label" dir="auto"></span></button>
      <button class="rx-root-toggle" tabindex="-1"></button>
    </div>
  </template>
```

- [ ] **Step 2: `ui/app.css`** — replace the `.rx-row` / `.rx-row:hover` rules with
  the boxed version and add rail rules:

```css
.rx-row {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: 8px; cursor: default;
  background: var(--panel-2); border: 1px solid var(--line); margin-bottom: 6px;
}
.rx-row:hover, .rx-row:focus { background: #232834; outline: none; }
.rx-row.dir { cursor: pointer; }
.rx-subpath { color: var(--muted); font-size: 11px; margin-inline-start: 6px; }

.rx-root-wrap { position: relative; display: flex; align-items: center; }
.rx-root-wrap .rx-root { flex: 1; }
.rx-root-toggle {
  position: absolute; inset-inline-end: 4px; border: 0; background: transparent;
  color: var(--muted); cursor: pointer; font-size: 13px; padding: 2px 6px;
  border-radius: 5px; opacity: 0;
}
.rx-root-wrap:hover .rx-root-toggle,
.rx-root-wrap:focus-within .rx-root-toggle { opacity: 1; }
.rx-root-toggle:hover { color: var(--text); background: var(--bg); }
.rx-nav-divider {
  color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .5px; padding: 12px 9px 4px; border-top: 1px solid var(--line);
  margin-top: 8px;
}
.rx-root.hidden-root { opacity: .5; }
```

- [ ] **Step 3: `ui/app.js`** — replace `rxInit` and add hidden-root handling.
  Replace the whole `async function rxInit()` with:

```js
let rxHidden = new Set();

function rxNorm(p) { return (p || "").toLowerCase().replace(/\//g, "\\"); }

async function rxInit() {
  if (rxLoaded) return;
  rxLoaded = true;
  const st = await api().get_ui_state();
  rxHidden = new Set((st.hidden_roots || []).map(rxNorm));
  rxRoots = await api().list_roots();
  rxRenderNav();
  const firstVisible = rxRoots.find(r => r.exists && !rxHidden.has(rxNorm(r.path)))
                    || rxRoots.find(r => r.exists) || rxRoots[0];
  if (firstVisible) rxBrowse(firstVisible.path);
}

function rxRenderNav() {
  const nav = $(".rx-nav");
  nav.innerHTML = "";
  const visible = rxRoots.filter(r => !rxHidden.has(rxNorm(r.path)));
  const hidden = rxRoots.filter(r => rxHidden.has(rxNorm(r.path)));
  visible.forEach(r => nav.appendChild(rxRootEl(r, false)));
  if (hidden.length) {
    const d = document.createElement("div");
    d.className = "rx-nav-divider";
    d.textContent = "Hidden";
    nav.appendChild(d);
    hidden.forEach(r => nav.appendChild(rxRootEl(r, true)));
  }
  rxMarkActive();
}

function rxRootEl(root, isHidden) {
  const n = $("#tpl-rx-root").content.cloneNode(true);
  const btn = n.querySelector(".rx-root");
  const tog = n.querySelector(".rx-root-toggle");
  $(".rx-root-label", n).textContent = root.label;
  btn.title = root.path;
  if (!root.exists) btn.classList.add("missing");
  if (isHidden) btn.classList.add("hidden-root");
  btn.addEventListener("click", () => rxBrowse(root.path));
  tog.textContent = isHidden ? "＋" : "⊘";
  tog.title = isHidden ? "Unhide this root" : "Hide this root";
  tog.addEventListener("click", async (e) => {
    e.stopPropagation();
    const key = rxNorm(root.path);
    if (rxHidden.has(key)) rxHidden.delete(key); else rxHidden.add(key);
    await api().set_ui_state({ hidden_roots: [...rxHidden] });
    rxRenderNav();
  });
  n.querySelector(".rx-root-wrap").dataset.path = root.path;
  return n;
}

function rxMarkActive() {
  $$(".rx-root").forEach(btn => {
    const path = btn.closest(".rx-root-wrap").dataset.path || "";
    btn.classList.toggle("active",
      rxCurrent && rxCurrent.toLowerCase().startsWith(path.toLowerCase()));
  });
}
```

In `rxBrowse`, replace the existing rail-active block

```js
  $$(".rx-root").forEach((btn, i) => btn.classList.toggle(
    "active", rxRoots[i] && rxCurrent &&
    rxCurrent.toLowerCase().startsWith(rxRoots[i].path.toLowerCase())));
```

with:

```js
  rxMarkActive();
```

- [ ] **Step 4: Manual check** — Receipts tab: rows are shaded boxes; hovering a
  root shows a `⊘`; clicking it moves that root under a `HIDDEN` divider dimmed
  with a `＋`; clicking `＋` restores it; restart the app and the hidden set
  persists.

- [ ] **Step 5: Commit**

```bash
git add ui/index.html ui/app.css ui/app.js
git commit -m "Receipts: hide/unhide roots, boxed entry rows"
```

---

## Task 5: Receipts — recursive search bar

**Files:** Modify `ui/index.html`, `ui/app.css`, `ui/app.js`.

- [ ] **Step 1: `ui/index.html`** — inside `<div class="rx-main">`, immediately
  before `<div class="rx-crumbs">`:

```html
        <input id="rx-search" type="search" placeholder="Search all receipts…" dir="auto">
```

- [ ] **Step 2: `ui/app.css`** — add:

```css
#rx-search {
  margin: 10px 14px 0; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--panel-2); color: var(--text);
}
.rx-searching #rx-open { display: none; }
```

- [ ] **Step 3: `ui/app.js`** — add after the `#rx-open` listener:

```js
let rxSearchTimer = 0;
$("#rx-search").addEventListener("input", e => {
  clearTimeout(rxSearchTimer);
  const q = e.target.value.trim();
  rxSearchTimer = setTimeout(() => (q.length >= 2 ? rxSearch(q) : rxExitSearch()), 200);
});

async function rxSearch(q) {
  $("#view-receipts").classList.add("rx-searching");
  const res = await api().search_receipts(q);
  const trail = $(".rx-crumb-trail");
  trail.innerHTML = "";
  const label = document.createElement("span");
  label.className = "rx-crumb here";
  label.textContent = `Search "${q}" — ${res.results.length} result(s)` +
    (res.truncated ? " (first 200)" : "");
  trail.appendChild(label);

  const list = $(".rx-list");
  list.innerHTML = "";
  const empty = $(".rx-empty");
  if (!res.results.length) {
    empty.hidden = false; empty.textContent = "No matches.";
    return;
  }
  empty.hidden = true;
  for (const e of res.results) {
    const n = $("#tpl-rx-row").content.cloneNode(true);
    const row = n.querySelector(".rx-row");
    $(".rx-glyph", n).textContent = RX_GLYPH[e.kind] || RX_GLYPH.file;
    $(".rx-name", n).textContent = e.name;
    const sub = document.createElement("span");
    sub.className = "rx-subpath";
    sub.textContent = `${e.root_label} / ${e.rel}`;
    $(".rx-name", n).appendChild(sub);
    $(".rx-meta", n).textContent = "";
    if (e.is_dir) {
      row.classList.add("dir");
      row.addEventListener("click", () => { $("#rx-search").value = ""; rxExitSearch(); rxBrowse(e.path); });
    } else {
      row.addEventListener("dblclick", () => api().open_path(e.path));
    }
    list.appendChild(n);
  }
}

function rxExitSearch() {
  $("#view-receipts").classList.remove("rx-searching");
  if (rxCurrent) rxBrowse(rxCurrent);
}
```

- [ ] **Step 4: Manual check** — type `סלקום`: main pane shows matches from every
  root with `root / relative\path`; clicking a folder result jumps there and
  clears the box; clearing the box returns to the folder you were in.

- [ ] **Step 5: Commit**

```bash
git add ui/index.html ui/app.css ui/app.js
git commit -m "Receipts: recursive search across all roots"
```

---

## Task 6: This run — definite end message

**Files:** Modify `ui/app.js`.

- [ ] **Step 1: Replace the `done` branch of `window.onScanEvent`** with:

```js
  } else if (evt.type === "done") {
    renderRunDone(evt);
  }
```

- [ ] **Step 2: Add `renderRunDone` + `syncRun`** (near `resetRunView`):

```js
function runDoneMessage(d) {
  if (d.status === "error") return "Scan stopped early — see errors above.";
  const s = d.saved || 0, f = d.fallback || 0, x = d.excluded || 0;
  if (s > 0) {
    let m = `Scan complete — ${s} new receipt${s === 1 ? "" : "s"} saved`;
    if (f) m += ` · ${f} need review`;
    if (x) m += ` · ${x} skipped`;
    return m + ".";
  }
  if (f > 0) return `Scan complete — no new receipts; ${f} need review.`;
  return "Scan complete — no new mail found.";
}

function renderRunDone(d) {
  $("#run-summary").textContent = runDoneMessage(d);
  const nothing = !(d.saved || d.fallback || d.excluded);
  $("#run-empty").hidden = !nothing || d.status === "error";
  refreshBadge();
}

async function syncRun() {
  try {
    const run = await api().get_run();
    if ((run.status === "done" || run.status === "error") &&
        $("#run-summary").textContent.startsWith("Scanning")) {
      const last = [...(run.events || [])].reverse().find(e => e.type === "done")
                 || { status: run.status, ...(run.summary || {}) };
      renderRunDone(last);
    }
  } catch (_) {}
}
```

- [ ] **Step 3: Call `syncRun` on tab activation** — in the `$$(".tab").forEach`
  handler add:

```js
  if (t.dataset.view === "run") syncRun();
```

- [ ] **Step 4: Manual check** — run a scan: the summary ends on a full sentence
  (`Scan complete — no new mail found.` when nothing new); switching away and
  back keeps the final message, never a stuck `Scanning …`.

- [ ] **Step 5: Commit**

```bash
git add ui/app.js
git commit -m "This run: always resolve to a definite end-of-scan message"
```

---

## Task 7: Fallbacks — simple / detailed toggle

**Files:** Modify `ui/index.html`, `ui/app.css`, `ui/app.js`.

- [ ] **Step 1: `ui/index.html`** — in `<div class="fb-toolbar">`, before
  `#fb-handoff`:

```html
        <button id="fb-viewtoggle">Simple view</button>
```

Add a template after `tpl-fallback`:

```html
  <template id="tpl-fb-compact">
    <article class="card fb-compact">
      <input type="checkbox" class="fb-check" title="Select for &quot;Handle selected with Claude&quot;">
      <div class="card-main">
        <div class="card-title" dir="auto"></div>
        <div class="card-sub" dir="auto"></div>
      </div>
      <span class="conf"></span>
      <a class="open-folder" href="#">Open folder</a>
      <button class="fb-expand" title="Show options">▸</button>
      <div class="fb-form-slot" hidden></div>
    </article>
  </template>
```

- [ ] **Step 2: `ui/app.css`** — add:

```css
#fb-viewtoggle {
  background: var(--panel-2); color: var(--muted); border: 0; padding: 8px 14px;
  border-radius: 8px; cursor: pointer; margin-inline-end: 8px;
}
#fb-viewtoggle:hover { color: var(--text); }
.card.fb-compact { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 10px; }
.card.fb-compact .card-main { flex: 1 1 240px; }
.fb-expand {
  background: transparent; border: 0; color: var(--muted); cursor: pointer;
  font-size: 14px; padding: 2px 6px; border-radius: 5px;
}
.fb-expand:hover { color: var(--text); background: var(--panel-2); }
.fb-expand.open { transform: rotate(90deg); }
.fb-form-slot { flex-basis: 100%; }
```

- [ ] **Step 3: `ui/app.js` — refactor `fallbackCard` into a shared form builder.**

Replace the current `async function fallbackCard(it) { ... }` with the three
functions below. `renderForm` is the body of the old function from the
`const sel = ...` category setup through the end of the submit handler, taking
an explicit container.

```js
async function renderForm(slot, it) {
  slot.innerHTML = `
    <form class="fb-form">
      <fieldset class="fb-kinds">
        <legend>What to do with this email</legend>
        <label class="opt"><span class="opt-head"><input type="radio" name="kind" value="rule" checked> Make a rule</span>
          <span class="opt-desc">File this email now <b>and</b> save the settings below as a permanent rule, so future emails like it are filed automatically.</span></label>
        <label class="opt"><span class="opt-head"><input type="radio" name="kind" value="once"> Move this one only</span>
          <span class="opt-desc">File just this one email using the settings below. No rule is saved.</span></label>
        <label class="opt"><span class="opt-head"><input type="radio" name="kind" value="exclude"> Exclude as promotional</span>
          <span class="opt-desc">Not a receipt. Deletes the saved folder and auto-skips future emails from this sender.</span></label>
        <label class="opt"><span class="opt-head"><input type="radio" name="kind" value="skip"> Skip for now</span>
          <span class="opt-desc">Do nothing. The email stays in this list.</span></label>
      </fieldset>
      <div class="fb-fields">
        <div class="field"><label>Seller</label><input class="f-seller" dir="auto"
          title="Business name, as it should appear in the receipt folder name."><span class="hint">Business name shown in the folder name.</span></div>
        <div class="field"><label>Product</label><input class="f-product" dir="auto"
          title="Short description of what the receipt is for."><span class="hint">What the receipt is for.</span></div>
        <div class="field"><label>Category</label><select class="f-category"
          title="Optional sub-folder under קבלות for recurring utility bills."></select><span class="hint">Optional sub-folder under קבלות (utility bills only).</span></div>
        <div class="field"><label>Destination root</label><input class="f-basedir" dir="auto" placeholder="(default: קבלות)"
          title="Optional full path to file this elsewhere than קבלות."><span class="hint">Optional. A different root folder instead of קבלות.</span></div>
        <div class="field"><label>Match sender contains</label><input class="f-sender" dir="auto"
          title="Part of the sender address the rule matches on (usually the domain)."><span class="hint">Text in the sender address the rule matches on.</span></div>
        <div class="field"><label>Match subject contains</label><input class="f-subject" dir="auto" placeholder="(optional)"
          title="Optional. Also require this text in the subject."><span class="hint">Optional. Also require this text in the subject.</span></div>
      </div>
      <button type="submit" class="fb-apply">Apply</button>
    </form>`;
  const form = slot.querySelector(".fb-form");
  const sel = $(".f-category", form);
  if (!CATEGORIES.length) CATEGORIES = await api().categories();
  sel.innerHTML = `<option value="">no category</option>` +
    CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join("");
  const s = await api().suggest_fallback(it.message_id);
  $(".f-seller", form).value = s.seller || "";
  $(".f-product", form).value = s.product || "";
  if (s.category) sel.value = s.category;
  $(".f-sender", form).value = s.match_sender_contains || "";
  if (s.kind) { const r = form.querySelector(`input[value="${s.kind}"]`); if (r) r.checked = true; }
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const decision = {
      kind: form.querySelector("input[name=kind]:checked").value,
      seller: $(".f-seller", form).value.trim(),
      product: $(".f-product", form).value.trim(),
      category: $(".f-category", form).value || null,
      base_dir: $(".f-basedir", form).value.trim() || null,
      match_sender_contains: $(".f-sender", form).value.trim(),
      match_subject_contains: $(".f-subject", form).value.trim() || null,
    };
    const res = await api().apply_fallback(it.message_id, decision);
    if (res && res.ok) {
      slot.closest(".card").remove();
      toast(`Resolved: ${decision.seller || it.subject}`);
      loadFallbacks();
    } else {
      toast((res && res.error) || "Failed to apply", true);
    }
  });
}

function fbFillHeader(n, it, confHint) {
  $(".card-title", n).textContent = it.subject || "(no subject)";
  $(".card-sub", n).textContent = `${it.sender} · ${it.account} · ${(it.date || "").replace(/_/g, "-")}`;
  const of = $(".open-folder", n);
  of.addEventListener("click", e => { e.preventDefault(); api().open_folder(it.folder_path); });
  const conf = $(".conf", n);
  if (confHint) { conf.textContent = confHint.text; conf.classList.add(confHint.cls); }
  $(".fb-check", n).addEventListener("change", updateHandoffButton);
}

async function fallbackCard(it) {
  const n = $("#tpl-fallback").content.cloneNode(true);
  const s = await api().suggest_fallback(it.message_id);
  fbFillHeader(n, it, {
    text: s.confidence === "low" ? "low confidence — consider handling with Claude"
                                 : (s.confidence || "") + " confidence",
    cls: s.confidence || "medium",
  });
  $(".open-pdf", n).addEventListener("click", e => { e.preventDefault(); api().open_path(it.folder_path + "\\email.pdf"); });
  const slot = document.createElement("div");
  n.querySelector(".card-main").appendChild(slot);
  await renderForm(slot, it);
  n.querySelector(".card").dataset.mid = it.message_id;
  return n;
}

function fallbackCompact(it) {
  const n = $("#tpl-fb-compact").content.cloneNode(true);
  fbFillHeader(n, it, null);
  const card = n.querySelector(".card");
  card.dataset.mid = it.message_id;
  const slot = n.querySelector(".fb-form-slot");
  const caret = n.querySelector(".fb-expand");
  let built = false;
  const toggle = async () => {
    const opening = slot.hidden;
    slot.hidden = !opening;
    caret.classList.toggle("open", opening);
    if (opening && !built) { built = true; await renderForm(slot, it); }
  };
  caret.addEventListener("click", toggle);
  n.querySelector(".card-main").addEventListener("click", toggle);
  return n;
}
```

Note the `tpl-fallback` template still contains its own `<form>` markup; since
`fallbackCard` now appends a `slot` and calls `renderForm`, **remove the
`<form class="fb-form"> ... </form>` block from `tpl-fallback` in
`ui/index.html`**, leaving the card head (`.card-title`, `.card-sub`,
`.fb-links` with `.open-folder`/`.open-pdf`/`.conf`) and the `.fb-check`.

- [ ] **Step 4: `ui/app.js` — `loadFallbacks` picks the renderer**

Replace the body of `async function loadFallbacks()` with:

```js
async function loadFallbacks() {
  if (!CATEGORIES.length) CATEGORIES = await api().categories();
  const st = await api().get_ui_state();
  fbSimple = !!st.fallbacks_simple;
  $("#fb-viewtoggle").textContent = fbSimple ? "Detailed view" : "Simple view";
  const list = $("#fb-list");
  list.innerHTML = "";
  const items = await api().get_fallbacks();
  $("#fb-empty").hidden = items.length > 0;
  for (const it of items) {
    list.appendChild(fbSimple ? fallbackCompact(it) : await fallbackCard(it));
  }
  updateHandoffButton();
  refreshBadge(items.length);
}
```

Add near the other `let` declarations at the top: `let fbSimple = false;`

Add the toggle listener (near `#fb-handoff` listener):

```js
$("#fb-viewtoggle").addEventListener("click", async () => {
  await api().set_ui_state({ fallbacks_simple: !fbSimple });
  loadFallbacks();
});
```

- [ ] **Step 5: Manual check** — Fallbacks tab: `Simple view` button collapses
  every entry to one row (subject + `sender · account · date` + confidence +
  Open folder + checkbox + caret); clicking a row expands its full form; Apply
  still resolves and removes it; the button now says `Detailed view` and
  flips back; the choice persists across app restarts; "Handle selected with
  Claude" still works from checked compact rows.

- [ ] **Step 6: Commit**

```bash
git add ui/index.html ui/app.css ui/app.js
git commit -m "Fallbacks: simple/detailed view toggle with per-row expand"
```

---

## Task 8: Documentation

**Files:** Modify `DOCUMENTATION.md`.

- [ ] **Step 1: File table** — after the `receipt_roots.py` row add:

```markdown
| `ui_state.py` | Persists small window UI preferences (`hidden_roots`, `fallbacks_simple`) to `ui_state.json`. |
| `ui_state.json` | Runtime UI preferences (git-ignored). |
```

- [ ] **Step 2: Startup UI section** — update the **Receipts** and **Fallbacks**
  rows in the "Four views" table:

Receipts row — append:

```
 A search box at the top searches every root recursively. Each root can be hidden via the ⊘ button (moves it to a "Hidden" section) and restored with ＋; the choice is saved in ui_state.json. Entries render as shaded boxes.
```

Fallbacks row — append:

```
 A "Simple view" toggle collapses every entry to a one-line row (subject + sender · account · date + confidence); click a row to expand its full form. The toggle is saved in ui_state.json.
```

- [ ] **Step 3: This run row** — append:

```
 The scan always resolves to a definite message ("Scan complete — N new receipts saved" / "…no new mail found"), even if an account errors.
```

- [ ] **Step 4: Commit**

```bash
git add DOCUMENTATION.md
git commit -m "Document Receipts search/hide, Fallbacks simple view, scan-end message"
```

---

## Self-Review Notes

**Spec coverage:** A `ui_state.py`→T1; B hidden roots→T4; C boxed rows→T4 Step 2; D search→T2 (`search_receipts`) + T5 (UI); E scan-end→T2 (`_run_scan`) + T6 (UI); F simple view→T7; G drag→T2 (`move_by`, `main` x/y) + T3 (handler). ✓

**Placeholder scan:** none — every step is full code or an exact command.

**Type consistency:** `search_receipts` result keys (`name, path, is_dir, kind, root_label, rel`) identical in T2 impl, T2 tests, T5 consumer. `done` event keys (`type, run_id, saved, fallback, excluded, status`) identical in T2 `_run_scan` and T6 `runDoneMessage`. `ui_state` keys (`hidden_roots, fallbacks_simple`) identical across T1 DEFAULTS, T2 tests, T4/T7 consumers. `move_by(dx,dy)` defined T2, called T3. `renderForm(slot, it)` defined once in T7, called by both `fallbackCard` and `fallbackCompact`. `_DATED_RE` (already in `app.py`) reused by `_search_hit`.

**Ordering note:** T2 rewrites `_run_scan` and the `main()` window-creation block; T3–T7 are frontend-only and independent of each other but all assume T2 landed (they call `api().move_by`, `api().get_ui_state`, `api().search_receipts`). Execute in order.
