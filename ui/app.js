"use strict";

const api = () => window.pywebview.api;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let CATEGORIES = [];
let histOffset = 0, histLoading = false, histDone = false, histRows = [];
let fbSimple = false;

// ---- view switching ------------------------------------------------------
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".view").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#view-" + t.dataset.view).classList.add("active");
  if (t.dataset.view === "run") syncRun();
  if (t.dataset.view === "history" && histRows.length === 0) loadHistory();
  if (t.dataset.view === "fallbacks") loadFallbacks();
  if (t.dataset.view === "receipts") rxInit();
  if (t.dataset.view === "categories") catLoad();
}));

// ---- window dragging -------------------------------------------------
// This webview reports screenX/Y window-relative, so absolute math drifts.
// Use origin-independent pointer deltas and let Python keep the position.
//
// Backpressure, not requestAnimationFrame: each `move_by` is a JS<->Python
// bridge round-trip that can take longer than a frame. Firing one per frame
// regardless lets stale calls queue up, so the window replays a backlog and
// trails the cursor by a gap that grows with the drag. Instead we keep at
// most one call in flight; movement that arrives while it's pending just
// accumulates, and the next call sends the whole delta at once — so the
// window stays one (constant) IPC latency behind the mouse, no worse.
(function enableDrag() {
  const bar = $(".titlebar");
  let dragging = false, dx = 0, dy = 0, inflight = false;
  const pump = () => {
    if (inflight || (!dx && !dy)) return;
    const sx = Math.round(dx), sy = Math.round(dy);
    dx -= sx; dy -= sy;                       // carry the sub-pixel remainder
    inflight = true;
    Promise.resolve(api().move_by(sx, sy))
      .catch(() => {})
      .finally(() => { inflight = false; pump(); });
  };
  bar.addEventListener("pointerdown", e => {
    if (e.button !== 0 || e.target.closest("button, .tab")) return;
    dragging = true; dx = dy = 0;
    try { bar.setPointerCapture(e.pointerId); } catch (_) {}
  });
  bar.addEventListener("pointermove", e => {
    if (!dragging) return;
    dx += e.movementX; dy += e.movementY;
    pump();
  });
  const end = e => {
    dragging = false;
    try { bar.releasePointerCapture(e.pointerId); } catch (_) {}
    pump();                                   // flush the final remainder
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
})();

// ---- window resizing ---------------------------------------------------
// Frameless window = no OS resize border, so this corner grip drives
// Api.resize_by with the same origin-independent pointer-delta approach
// as the titlebar drag above.
(function enableResize() {
  const grip = $("#win-resize-grip");
  let dragging = false, dx = 0, dy = 0, inflight = false;
  const pump = () => {                        // same backpressure as the drag
    if (inflight || (!dx && !dy)) return;
    const sx = Math.round(dx), sy = Math.round(dy);
    dx -= sx; dy -= sy;
    inflight = true;
    Promise.resolve(api().resize_by(sx, sy))
      .catch(() => {})
      .finally(() => { inflight = false; pump(); });
  };
  grip.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    dragging = true; dx = dy = 0;
    try { grip.setPointerCapture(e.pointerId); } catch (_) {}
  });
  grip.addEventListener("pointermove", e => {
    if (!dragging) return;
    dx += e.movementX; dy += e.movementY;
    pump();
  });
  const end = e => {
    dragging = false;
    try { grip.releasePointerCapture(e.pointerId); } catch (_) {}
    pump();
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
})();

$("#btn-min").addEventListener("click", () => api().minimize());
$("#btn-close").addEventListener("click", () => api().hide());
$("#btn-rescan").addEventListener("click", async () => {
  const r = await api().start_scan();
  if (r.status === "busy") toast("A scan is already running.");
  else resetRunView();
});

// ---- card rendering ----------------------------------------------------
function card(rec) {
  const n = $("#tpl-card").content.cloneNode(true);
  const pill = $(".pill", n);
  pill.textContent = (rec.action || "").toLowerCase();
  pill.classList.add((rec.action || "info").toLowerCase());
  $(".card-title", n).textContent =
    rec.seller ? `${rec.seller} · ${rec.product || ""}` : (rec.subject || "(no subject)");
  const sub = $(".card-sub", n);
  sub.textContent = rec.seller ? (rec.subject || "") : (rec.sender || "");
  if (rec.resolved_at) {
    const who = { user: "you", claude: "Claude", unknown: "backfill" }[rec.resolved_by]
              || rec.resolved_by || "backfill";
    sub.textContent = (sub.textContent ? sub.textContent + " · " : "")
                    + `resolved ${String(rec.resolved_at).slice(0, 10)} by ${who}`;
  }
  $(".chip.account", n).textContent = rec.account || "";
  $(".date", n).textContent = (rec.date || "").replace(/_/g, "-");
  const link = $(".open-folder", n);
  if (rec.folder_path) link.addEventListener("click", e => {
    e.preventDefault(); api().open_folder(rec.folder_path);
  });
  else link.remove();
  return n;
}

// ---- this-run view ----------------------------------------------------
const runAccts = new Map();  // label -> {state, detail}
const RA_DOT = { connecting: "◌", scanning: "◌", done: "✓", failed: "⚠" };

function resetRunView() {
  $("#run-prompt").hidden = true;
  $("#run-list").innerHTML = "";
  $("#run-accounts").innerHTML = "";
  runAccts.clear();
  $("#run-summary").textContent = "Scanning…";
  $("#run-empty").hidden = true;
}

// Shown on a manual launch, before the user has kicked off a scan.
function showRunPrompt() {
  $("#run-prompt").hidden = false;
  $("#run-list").innerHTML = "";
  $("#run-accounts").innerHTML = "";
  runAccts.clear();
  $("#run-summary").textContent = "";
  $("#run-empty").hidden = true;
}

$("#run-start").addEventListener("click", async () => {
  const r = await api().start_scan();
  if (r.status === "busy") toast("A scan is already running.");
  else resetRunView();
});

// On launch, reflect whatever Api.get_run() already knows: idle (manual
// launch, waiting for the user), running (autostart already kicked off the
// scan), or done/error (rare — a very fast autostart scan beat page load).
async function initRunView() {
  let run;
  try { run = await api().get_run(); } catch (_) { run = { status: "idle" }; }
  if (run.status === "idle") {
    showRunPrompt();
  } else if (run.status === "running") {
    resetRunView();
  } else {
    resetRunView();
    const last = [...(run.events || [])].reverse().find(e => e.type === "done")
               || { status: run.status, ...(run.summary || {}) };
    renderRunDone(last);
  }
}

function runSetAccount(label, state, detail) {
  runAccts.set(label, { state, detail: detail || "" });
  renderRunAccounts();
}

function renderRunAccounts() {
  const host = $("#run-accounts");
  host.innerHTML = "";
  for (const [label, a] of runAccts) {
    const row = document.createElement("div");
    row.className = `run-acct state-${a.state}`;
    row.innerHTML =
      `<span class="ra-dot">${RA_DOT[a.state] || "◌"}</span>` +
      `<span class="ra-name" dir="auto"></span><span class="ra-detail" dir="auto"></span>`;
    row.querySelector(".ra-name").textContent = label;
    row.querySelector(".ra-detail").textContent =
      a.state === "connecting" ? "connecting…"
      : a.state === "scanning" ? a.detail
      : a.state === "done" ? "done"
      : a.detail;
    host.appendChild(row);
  }
}

window.onScanEvent = function (evt) {
  if (evt.type === "connecting") {
    runSetAccount(evt.label, "connecting");
    $("#run-summary").textContent = `Connecting to ${evt.label}…`;
  } else if (evt.type === "account") {
    runSetAccount(evt.label, "scanning", `${evt.candidates} to check`);
    $("#run-summary").textContent = `Scanning ${evt.label}…`;
  } else if (evt.type === "mail") {
    $("#run-list").appendChild(card(evt.record));
    const label = evt.record && evt.record.account;
    if (label) {
      const a = runAccts.get(label) || { hits: 0 };
      a.hits = (a.hits || 0) + 1;
      a.state = "scanning";
      a.detail = `${a.hits} handled`;
      runAccts.set(label, a);
      renderRunAccounts();
    }
  } else if (evt.type === "error") {
    const lbl = evt.label && evt.label !== "-" ? evt.label : null;
    if (lbl) runSetAccount(lbl, "failed", evt.message);
    const d = document.createElement("div");
    d.className = "toast error";
    const span = document.createElement("span");
    span.className = "toast-msg";
    span.textContent = `${evt.label}: ${evt.message}`;
    d.appendChild(span);
    d.appendChild(askClaudeButton(`${evt.label}: ${evt.message}`));
    $("#run-list").appendChild(d);
  } else if (evt.type === "done") {
    for (const [label, a] of runAccts) {
      if (a.state === "connecting" || a.state === "scanning") runSetAccount(label, "done");
    }
    renderRunDone(evt);
  }
};

function runDoneMessage(d) {
  if (d.status === "error") return "Scan stopped early — see errors above.";
  const s = d.saved || 0, f = d.fallback || 0, x = d.excluded || 0;
  if (s > 0) {
    let m = `Scan complete — ${s} new receipt${s === 1 ? "" : "s"} saved`;
    if (f) m += ` · ${f} need${f === 1 ? "s" : ""} review`;
    if (x) m += ` · ${x} skipped`;
    return m + ".";
  }
  if (f > 0) return `Scan complete — no new receipts; ${f} need${f === 1 ? "s" : ""} review.`;
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
    const cur = $("#run-summary").textContent;
    if ((run.status === "done" || run.status === "error") &&
        (cur === "" || cur.startsWith("Scanning"))) {
      const last = [...(run.events || [])].reverse().find(e => e.type === "done")
                 || { status: run.status, ...(run.summary || {}) };
      renderRunDone(last);
    }
  } catch (_) {}
}

// ---- history view ----------------------------------------------------
async function loadHistory() {
  if (histLoading || histDone) return;
  histLoading = true;
  const page = await api().get_history(histOffset, 50);
  histLoading = false;
  if (!page.length) { histDone = true; return; }
  histOffset += page.length;
  histRows = histRows.concat(page);
  renderHistory();
}
// Drop the cached page state and re-fetch from the top. Called after a
// fallback is resolved so its new RESOLVED row appears without reopening
// the window. No-op-safe if the History tab was never opened.
async function refreshHistory() {
  if (histLoading) return;
  histOffset = 0; histDone = false; histRows = [];
  await loadHistory();
  renderHistory();
}

function renderHistory() {
  const q = $("#hist-search").value.trim().toLowerCase();
  const list = $("#hist-list");
  list.innerHTML = "";
  histRows
    .filter(r => !q || [r.sender, r.subject, r.seller].some(
      v => (v || "").toLowerCase().includes(q)))
    .forEach(r => list.appendChild(card(r)));
}
$("#hist-search").addEventListener("input", renderHistory);
new IntersectionObserver(es => {
  if (es.some(e => e.isIntersecting)) loadHistory();
}).observe($("#hist-sentinel"));

// ---- fallbacks view ----------------------------------------------------
function fillSubfolderList() {
  const dl = $("#fb-subfolders");
  if (dl) dl.innerHTML = CATEGORIES.map(c => `<option value="${c}"></option>`).join("");
}

async function loadFallbacks() {
  if (!CATEGORIES.length) CATEGORIES = await api().categories();
  fillSubfolderList();
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

$("#fb-viewtoggle").addEventListener("click", async () => {
  await api().set_ui_state({ fallbacks_simple: !fbSimple });
  loadFallbacks();
});

// Per-entry "Claude" button — opens a `claude` terminal seeded with just this
// one fallback (same handoff path as the multi-select button, list of one).
function fallbackClaudeButton(it) {
  const b = document.createElement("button");
  b.className = "ask-claude icon-only";
  b.type = "button";
  b.title = "Handle this fallback with Claude";
  b.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true" width="13" height="13">' +
    '<path fill="currentColor" d="M12 1.5l1.9 5.1 5.1 1.9-5.1 1.9L12 15.5l-1.9-5.1L5 8.5l5.1-1.9z' +
    'M18.5 14l1 2.6 2.6 1-2.6 1-1 2.6-1-2.6-2.6-1 2.6-1z' +
    'M5 15l.8 2 2 .8-2 .8L5 21.5l-.8-2-2-.8 2-.8z"/></svg>';
  b.addEventListener("click", async (e) => {
    e.stopPropagation();
    e.preventDefault();
    b.disabled = true;
    let res;
    try { res = await api().handoff([it.message_id]); }
    catch (_) { res = { ok: false }; }
    toast(res && res.ok ? "Opening Claude…" : (res && res.error) || "Couldn't open Claude",
          !(res && res.ok));
    setTimeout(() => { b.disabled = false; }, 3000);
  });
  return b;
}

function fbHeader(scope, it) {
  $(".card-title", scope).textContent = it.subject || "(no subject)";
  $(".card-sub", scope).textContent =
    `${it.sender} · ${it.account} · ${(it.date || "").replace(/_/g, "-")}`;
  const of = $(".open-folder", scope);
  of.addEventListener("click", e => {
    e.preventDefault(); api().open_folder(it.folder_path);
  });
  of.after(fallbackClaudeButton(it));
  $(".fb-check", scope).addEventListener("change", updateHandoffButton);
}

// "Destination root" dropdown: the default קבלות, every known root, and a
// "Choose a folder…" sentinel that opens the OS folder picker (Api.pick_folder)
// and adds whatever you pick as a new, selected option.
const FB_BROWSE = "__browse__";
let FB_ROOTS = null;

async function fillBasedirSelect(sel, current) {
  if (!FB_ROOTS) {
    try { FB_ROOTS = await api().list_roots(); } catch (_) { FB_ROOTS = []; }
  }
  const opt = (v, label) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = label;
    return o;
  };
  sel.innerHTML = "";
  sel.appendChild(opt("", "קבלות (default)"));
  const known = new Set();
  for (const r of FB_ROOTS) {
    known.add(r.path);
    sel.appendChild(opt(r.path, `${r.label} — ${r.path}`));
  }
  if (current && !known.has(current)) sel.appendChild(opt(current, current));
  sel.appendChild(opt(FB_BROWSE, "＋ Choose a folder…"));
  sel.value = current || "";
  let last = sel.value;
  sel.addEventListener("change", async () => {
    if (sel.value !== FB_BROWSE) { last = sel.value; return; }
    let res;
    try { res = await api().pick_folder(); } catch (_) { res = { ok: false }; }
    if (res && res.ok && res.path) {
      if (![...sel.options].some(o => o.value === res.path)) {
        sel.insertBefore(opt(res.path, res.path),
                         sel.querySelector(`option[value="${FB_BROWSE}"]`));
      }
      sel.value = res.path;
      last = res.path;
    } else {
      sel.value = last;                 // cancelled — restore previous choice
      if (res && !res.ok) toast((res && res.error) || "Couldn't open folder picker", true);
    }
  });
}

// Minimal searchable combobox over a fixed item list. `root` is a `.combo`
// element holding `.combo-input` + `.combo-list`; the chosen value lives in
// `root.dataset.value`. items: [{value, label}]. The first item stays pinned
// (shown even when the current filter would exclude it). onChange(value) fires
// only on an actual pick, never on mere typing.
function makeCombo(root, { items, onChange }) {
  const input = $(".combo-input", root);
  const list = $(".combo-list", root);
  let view = [];
  let active = -1;

  const labelFor = v => (items.find(it => it.value === v) || {}).label || "";
  const curLabel = () => labelFor(root.dataset.value);

  function render(q) {
    const needle = (q || "").trim().toLowerCase();
    view = items.filter((it, i) =>
      i === 0 || !needle || it.label.toLowerCase().includes(needle));
    list.innerHTML = "";
    if (!view.length) {
      const li = document.createElement("li");
      li.className = "combo-empty";
      li.textContent = "no match";
      list.appendChild(li);
      return;
    }
    view.forEach((it, i) => {
      const li = document.createElement("li");
      li.textContent = it.label;
      if (i === active) li.classList.add("is-active");
      if (it.value === root.dataset.value) li.classList.add("is-current");
      li.addEventListener("mousedown", e => { e.preventDefault(); choose(it); });
      list.appendChild(li);
    });
  }
  function open() { render(input.value === curLabel() ? "" : input.value); list.hidden = false; }
  function close() { list.hidden = true; active = -1; }
  function choose(it) {
    root.dataset.value = it.value;
    input.value = it.label;
    close();
    onChange(it.value);
  }

  input.addEventListener("focus", () => { input.select(); open(); });
  input.addEventListener("input", () => { active = -1; render(input.value); list.hidden = false; });
  input.addEventListener("blur", () => {
    setTimeout(() => { input.value = curLabel(); close(); }, 120);
  });
  input.addEventListener("keydown", e => {
    if (list.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) { open(); return; }
    if (e.key === "ArrowDown") { active = Math.min(active + 1, view.length - 1); render(input.value); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(input.value); e.preventDefault(); }
    else if (e.key === "Enter") {
      // while the popup is open, Enter picks (never submits the form)
      if (!list.hidden) { e.preventDefault(); const pick = view[active] || view[0]; if (pick) choose(pick); }
    }
    else if (e.key === "Escape") { input.value = curLabel(); close(); }
  });

  return { setValue(v) { root.dataset.value = v; input.value = labelFor(v); } };
}

// "File under a category" picker: ＋ New category… (sentinel) + one entry per
// existing category. Picking an existing one prefills seller/product (editable
// per-bill overrides) and pins the route/subfolder to that category.
const FB_NEWCAT = "__new__";
let FB_CATS = null;

async function fillCatAssign(scope, s) {
  const root = $(".f-cat-assign", scope);
  if (!root) return;
  if (!FB_CATS) {
    try { FB_CATS = await api().list_categories(); } catch (_) { FB_CATS = []; }
  }
  const nameInput = $(".f-cat-name", scope);
  const subEl = $(".f-category", scope);
  const baseSel = $(".f-basedir", scope);

  const items = [{ value: FB_NEWCAT, label: "＋ New category…" }];
  FB_CATS.filter(c => !c.exclude).forEach(c =>
    items.push({ value: c.id, label: `${c.name}${c.subfolder ? " — " + c.subfolder : ""}` }));

  const apply = val => {
    const cat = val === FB_NEWCAT ? null : FB_CATS.find(c => c.id === val);
    nameInput.hidden = !!cat;
    // an existing category owns seller/product defaults + the route it prefills
    if (cat) {
      $(".f-seller", scope).value = cat.seller || "";
      $(".f-product", scope).value = cat.product || "";
      if (subEl) subEl.value = cat.subfolder || "";
      if (baseSel && ![...baseSel.options].some(o => o.value === (cat.base_dir || "")))
        baseSel.appendChild(Object.assign(document.createElement("option"),
          { value: cat.base_dir || "", textContent: cat.base_dir || "קבלות (default)" }));
      if (baseSel) baseSel.value = cat.base_dir || "";
    }
    syncFieldsForKind(scope);
  };

  const combo = makeCombo(root, { items, onChange: apply });
  // preselect a category whose route matches the heuristic's category guess
  const guess = s.category
    ? FB_CATS.find(c => !c.exclude && c.subfolder === s.category) : null;
  combo.setValue(guess ? guess.id : FB_NEWCAT);
  apply(root.dataset.value);
}

// Heading + pin/hide for the fields block, driven by the chosen radio and
// (for "File under a category") whether an existing category or a new one is
// picked. An existing category owns its route + match rules, so those fields
// are shown read-only; seller/product stay editable as per-bill overrides.
function syncFieldsForKind(scope) {
  const kind = (scope.querySelector("input[name=kind]:checked") || {}).value;
  const root = $(".f-cat-assign", scope);
  const catVal = root ? root.dataset.value : "";
  const fields = $(".fb-fields", scope);
  const head = $(".fb-fields-head", scope);
  const isNewCat = kind === "category" && catVal === FB_NEWCAT;
  const existing = (kind === "category" && catVal && catVal !== FB_NEWCAT)
    ? (FB_CATS || []).find(c => c.id === catVal) : null;

  if (fields) fields.hidden = (kind === "exclude" || kind === "skip");
  if (fields && fields.hidden) return;

  if (head) {
    head.textContent =
      isNewCat        ? "New category — the fields below define it"
    : existing        ? `Category: ${existing.name} — its route is fixed below`
    : kind === "once" ? "This receipt only — no rule saved"
    : "";
    head.hidden = !head.textContent;
  }

  const dis = (sel, off) => { const el = $(sel, scope); if (el) el.disabled = off; };
  const hide = (sel, hid) => {
    const el = $(sel, scope);
    const field = el && el.closest(".field");
    if (field) field.hidden = hid;
  };
  dis(".f-seller", false);
  dis(".f-product", false);
  dis(".f-category", !!existing);
  dis(".f-basedir", !!existing);
  dis(".f-sender", !!existing);
  dis(".f-subject", !!existing);
  hide(".f-sender", kind === "once");
  hide(".f-subject", kind === "once");
}

function wireForm(scope, it, s) {
  $(".f-category", scope).value = s.category || "";
  $(".f-seller", scope).value = s.seller || "";
  $(".f-product", scope).value = s.product || "";
  fillBasedirSelect($(".f-basedir", scope), s.base_dir || "");
  $(".f-sender", scope).value = s.match_sender_contains || "";
  if (s.kind) {
    const r = scope.querySelector(`.fb-form input[value="${s.kind}"]`);
    if (r) r.checked = true;
  }
  scope.querySelectorAll("input[name=kind]").forEach(r =>
    r.addEventListener("change", () => syncFieldsForKind(scope)));
  fillCatAssign(scope, s);
  syncFieldsForKind(scope);
  $(".fb-form", scope).addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const kind = $("input[name=kind]:checked", form).value;
    const seller  = $(".f-seller", form).value.trim();
    const product = $(".f-product", form).value.trim();
    const subfolder = $(".f-category", form).value || null;
    const base_dir = (v => v && v !== FB_BROWSE ? v : null)($(".f-basedir", form).value.trim());
    const decision = {
      kind, seller, product,
      category: subfolder, base_dir,
      match_sender_contains: $(".f-sender", form).value.trim(),
      match_subject_contains: $(".f-subject", form).value.trim() || null,
    };
    if (kind === "category") {
      const pick = $(".f-cat-assign", form).dataset.value;
      if (pick === FB_NEWCAT) {
        decision.kind = "new_category";
        decision.category_name = $(".f-cat-name", form).value.trim();
        if (!decision.category_name) { toast("Name the new category first", true); return; }
      } else {
        decision.category_id = pick;
      }
    }
    const res = await api().apply_fallback(it.message_id, decision);
    if (res && res.ok) {
      form.closest(".card").remove();
      toast(`Resolved: ${seller || it.subject}`);
      FB_CATS = null;                 // a category may have been created/changed
      loadFallbacks();
      refreshHistory();
    } else {
      toast((res && res.error) || "Failed to apply", true);
    }
  });
}

async function fallbackCard(it) {
  const n = $("#tpl-fallback").content.cloneNode(true);
  fbHeader(n, it);
  $(".open-pdf", n).addEventListener("click", e => {
    e.preventDefault(); api().open_path(it.folder_path + "\\email.pdf");
  });
  const s = await api().suggest_fallback(it.message_id);
  const conf = $(".conf", n);
  conf.textContent = s.confidence === "low"
    ? "low confidence — consider handling with Claude" : (s.confidence || "") + " confidence";
  conf.classList.add(s.confidence || "medium");
  wireForm(n, it, s);
  n.querySelector(".card").dataset.mid = it.message_id;
  return n;
}

function fallbackCompact(it) {
  const n = $("#tpl-fb-compact").content.cloneNode(true);
  fbHeader(n, it);
  n.querySelector(".card").dataset.mid = it.message_id;
  const slot = n.querySelector(".fb-form-slot");
  const caret = n.querySelector(".fb-expand");
  let built = false;
  const toggle = async () => {
    const opening = slot.hidden;
    slot.hidden = !opening;
    caret.classList.toggle("open", opening);
    if (opening && !built) {
      built = true;
      const form = $("#tpl-fallback").content.cloneNode(true).querySelector(".fb-form");
      slot.appendChild(form);
      const s = await api().suggest_fallback(it.message_id);
      wireForm(slot, it, s);
    }
  };
  caret.addEventListener("click", e => { e.stopPropagation(); toggle(); });
  n.querySelector(".card-main").addEventListener("click", toggle);
  return n;
}

function selectedFallbackIds() {
  return $$("#fb-list .card").filter(c => {
    const cb = $(".fb-check", c);
    return cb && cb.checked;
  }).map(c => c.dataset.mid);
}
function updateHandoffButton() {
  $("#fb-handoff").disabled = selectedFallbackIds().length === 0;
}
$("#fb-handoff").addEventListener("click", async () => {
  const ids = selectedFallbackIds();
  const res = await api().handoff(ids);
  toast(res.ok ? `Opened Claude for ${res.count} fallback(s).`
                : (res.error || "Failed to open Claude"), !res.ok);
});

// ---- misc ------------------------------------------------------------
async function refreshBadge(count) {
  if (count === undefined) {
    try { count = (await api().get_fallbacks()).length; } catch (e) { count = 0; }
  }
  const b = $("#fb-badge");
  b.textContent = count;
  b.hidden = !count;
}
// Small Claude-mark button that opens a `claude` terminal in the repo,
// pre-seeded to debug the given error text.
function askClaudeButton(errText) {
  const b = document.createElement("button");
  b.className = "ask-claude";
  b.type = "button";
  b.title = "Ask Claude about this error";
  b.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true" width="13" height="13">' +
    '<path fill="currentColor" d="M12 1.5l1.9 5.1 5.1 1.9-5.1 1.9L12 15.5l-1.9-5.1L5 8.5l5.1-1.9z' +
    'M18.5 14l1 2.6 2.6 1-2.6 1-1 2.6-1-2.6-2.6-1 2.6-1z' +
    'M5 15l.8 2 2 .8-2 .8L5 21.5l-.8-2-2-.8 2-.8z"/></svg>' +
    '<span>Ask Claude</span>';
  b.addEventListener("click", async (e) => {
    e.stopPropagation();
    b.disabled = true;
    let res;
    try { res = await api().ask_claude_error(String(errText || "")); }
    catch (_) { res = { ok: false }; }
    toast(res && res.ok ? "Opening Claude…" : "Couldn't open Claude", !(res && res.ok));
    setTimeout(() => { b.disabled = false; }, 3000);
  });
  return b;
}

function toast(msg, isError) {
  const d = document.createElement("div");
  d.className = "toast" + (isError ? " error" : "");
  const span = document.createElement("span");
  span.className = "toast-msg";
  span.textContent = msg;
  d.appendChild(span);
  if (isError) d.appendChild(askClaudeButton(msg));
  $("#toast-host").appendChild(d);
  setTimeout(() => d.remove(), isError ? 12000 : 4000);
}

// ---- Receipts explorer -------------------------------------------------
let rxRoots = [], rxCurrent = null, rxLoaded = false;
let rxBackStack = [];
let rxEntries = [];         // entries of the folder currently shown
let rxSort = "date_desc";   // <name|date>_<asc|desc>
const RX_GLYPH = { folder: "📁", "receipt-folder": "📁", pdf: "📄", file: "▪" };

function humanSize(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
}

// Build one explorer row from a browse/search entry, with the cleaned
// title, a human date, and an account chip parsed from the folder name.
function rxRowEl(e) {
  const n = $("#tpl-rx-row").content.cloneNode(true);
  const row = n.querySelector(".rx-row");
  $(".rx-glyph", row).textContent = RX_GLYPH[e.kind] || RX_GLYPH.file;
  $(".rx-name", row).textContent = e.title || e.name;
  row.title = e.path;
  const meta = $(".rx-meta", row);
  meta.textContent = "";
  if (e.date_display) {
    const d = document.createElement("span");
    d.className = "rx-date"; d.textContent = e.date_display;
    meta.appendChild(d);
  }
  if (e.account) {
    const a = document.createElement("span");
    a.className = "rx-acct"; a.textContent = e.account;
    meta.appendChild(a);
  }
  if (!e.is_dir && e.size != null) {
    const s = document.createElement("span");
    s.className = "rx-size"; s.textContent = humanSize(e.size);
    meta.appendChild(s);
  }
  row._entry = e;                 // used by the right-click "Ask Claude" menu
  return { n, row };
}

let rxHidden = new Set();

function rxNorm(p) { return (p || "").toLowerCase().replace(/\//g, "\\"); }

async function rxInit() {
  if (rxLoaded) return;
  rxLoaded = true;
  const st = await api().get_ui_state();
  rxHidden = new Set((st.hidden_roots || []).map(rxNorm));
  if (/^(name|date)_(asc|desc)$/.test(st.rx_sort || "")) rxSort = st.rx_sort;
  rxRenderSortBtns();
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

async function rxBrowse(path, opts = {}) {
  const res = await api().browse(path);
  if (res.error && (!res.crumbs || !res.crumbs.length)) { toast(res.error, true); return; }
  const next = res.path || path;
  if (!opts.noHistory && rxCurrent && rxNorm(rxCurrent) !== rxNorm(next)) {
    rxBackStack.push(rxCurrent);
    if (rxBackStack.length > 100) rxBackStack.shift();
  }
  rxCurrent = next;
  rxUpdateBackBtn();

  rxMarkActive();

  const trail = $(".rx-crumb-trail");
  trail.innerHTML = "";
  (res.crumbs || []).forEach((c, i, arr) => {
    if (i) {
      const s = document.createElement("span");
      s.className = "rx-sep"; s.textContent = "›";
      trail.appendChild(s);
    }
    const b = document.createElement("button");
    b.className = "rx-crumb" + (i === arr.length - 1 ? " here" : "");
    b.textContent = c.name;
    b.title = c.path;
    if (i < arr.length - 1) b.addEventListener("click", () => rxBrowse(c.path));
    trail.appendChild(b);
  });

  const empty = $(".rx-empty");
  if (res.error) {
    $(".rx-list").innerHTML = "";
    empty.hidden = false;
    if (res.error === "folder not found") {
      empty.textContent = "This folder doesn't exist yet.";
    } else {
      empty.textContent = res.error + " ";
      empty.appendChild(askClaudeButton("Receipts explorer: " + res.error));
    }
    rxEntries = [];
    return;
  }
  rxEntries = res.entries || [];
  const q = $("#rx-search");
  rxRenderList(q && q.value.trim());
}

// Render the current folder's entries into .rx-list, optionally narrowed to
// those matching `filter` (case-insensitive substring of name / seller / account).
function rxRenderList(filter) {
  const list = $(".rx-list");
  const empty = $(".rx-empty");
  list.innerHTML = "";

  const f = (filter || "").toLowerCase();
  $("#view-receipts").classList.toggle("rx-searching", !!f);

  let entries = rxEntries;
  if (f) {
    entries = entries.filter(e => {
      const p = e.parsed || {};
      return (e.name || "").toLowerCase().includes(f)
          || (p.title || "").toLowerCase().includes(f)
          || (p.account || "").toLowerCase().includes(f);
    });
  }

  if (!rxEntries.length) {
    empty.hidden = false;
    empty.textContent = "This folder is empty.";
    return;
  }
  if (!entries.length) {
    empty.hidden = false;
    empty.textContent = `No items in this folder match “${filter}”.`;
    return;
  }
  empty.hidden = true;
  for (const e of rxSortEntries(entries)) {
    const { n, row } = rxRowEl(e);
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

// ---- explorer: back-history + sort ---------------------------------
function rxUpdateBackBtn() {
  const b = $("#rx-back");
  if (b) b.disabled = rxBackStack.length === 0;
}

function rxGoBack() {
  const prev = rxBackStack.pop();
  if (prev === undefined) return;
  rxUpdateBackBtn();
  const q = $("#rx-search");
  if (q && q.value) { q.value = ""; rxExitSearch({ noBrowse: true }); }
  rxBrowse(prev, { noHistory: true });
}

function rxDateKey(e) {
  const m = /^(\d{4})_(\d{2})_(\d{2})/.exec(e.name || "");
  if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3]) / 1000;
  return e.mtime || 0;
}

function rxSortEntries(list) {
  const [field, dir] = rxSort.split("_");
  const sign = dir === "asc" ? 1 : -1;
  const byName = (a, b) =>
    a.name.toLowerCase().localeCompare(b.name.toLowerCase());
  return list.slice().sort((a, b) => {
    if (!a.is_dir !== !b.is_dir) return a.is_dir ? -1 : 1;   // folders first
    let cmp;
    if (field === "name") {
      cmp = byName(a, b);
    } else {
      const d = rxDateKey(a) - rxDateKey(b);
      cmp = d < 0 ? -1 : d > 0 ? 1 : byName(a, b);
    }
    return cmp * sign;
  });
}

function rxRenderSortBtns() {
  const [field, dir] = rxSort.split("_");
  const fb = $("#rx-sort-field"), db = $("#rx-sort-dir");
  if (fb) fb.textContent = field === "name" ? "Name" : "Date";
  if (db) {
    db.textContent = dir === "asc" ? "↑" : "↓";
    db.title = dir === "asc" ? "Ascending" : "Descending";
  }
}

async function rxSetSort(next) {
  rxSort = next;
  rxRenderSortBtns();
  try { await api().set_ui_state({ rx_sort: rxSort }); } catch (_) {}
  const q = $("#rx-search");
  if ($("#view-receipts").classList.contains("rx-searching") && q && q.value.trim().length >= 2) {
    rxSearch(q.value.trim());
  } else if (rxCurrent) {
    rxRenderList(q && q.value.trim());
  }
}

$("#rx-back").addEventListener("click", rxGoBack);
$("#rx-sort-field").addEventListener("click", () => {
  const [f, d] = rxSort.split("_");
  rxSetSort((f === "name" ? "date" : "name") + "_" + d);
});
$("#rx-sort-dir").addEventListener("click", () => {
  const [f, d] = rxSort.split("_");
  rxSetSort(f + "_" + (d === "asc" ? "desc" : "asc"));
});
window.addEventListener("keydown", e => {
  if (e.altKey && e.key === "ArrowLeft" && $("#view-receipts").classList.contains("active")) {
    e.preventDefault();
    rxGoBack();
  }
});

let rxSearchTimer = 0;
$("#rx-search").addEventListener("input", e => {
  clearTimeout(rxSearchTimer);
  const q = e.target.value.trim();
  rxSearchTimer = setTimeout(() => (q.length >= 2 ? rxSearch(q) : rxExitSearch()), 200);
});

// Recursive cross-root search (Api.search_receipts) — only while the search
// box holds text; clearing it restores the plain current-folder listing.
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
  for (const e of rxSortEntries(res.results)) {
    const { n, row } = rxRowEl(e);
    const sub = document.createElement("span");
    sub.className = "rx-subpath";
    sub.textContent = `${e.root_label} / ${e.rel.split(/[\\/]/).slice(0, -1).join(" / ") || "."}`;
    $(".rx-name", row).appendChild(sub);
    if (e.is_dir) {
      row.classList.add("dir");
      row.addEventListener("click", () => { $("#rx-search").value = ""; rxExitSearch({ noBrowse: true }); rxBrowse(e.path); });
    } else {
      row.addEventListener("dblclick", () => api().open_path(e.path));
    }
    list.appendChild(n);
  }
}

// Leave search mode. By default this re-browses the current folder to restore
// the plain listing; pass { noBrowse: true } when the caller is about to
// navigate somewhere else, so the two navigations don't race (the stale
// re-browse would otherwise win and snap you back to the pre-search folder).
function rxExitSearch(opts = {}) {
  $("#view-receipts").classList.remove("rx-searching");
  if (!opts.noBrowse && rxCurrent) rxBrowse(rxCurrent, { noHistory: true });
}

// ---- explorer: right-click "Ask Claude" menu -------------------------
// Right-click any explorer row (folder or file, plain listing or search
// results) to open a one-item menu; clicking "Ask Claude" opens a `claude`
// terminal seeded with a prompt about that specific entry (Api.ask_claude_receipt).
(function rxContextMenu() {
  const menu = $("#rx-ctx-menu");
  if (!menu) return;
  let target = null;                       // the row's stashed entry

  const hide = () => { menu.hidden = true; target = null; };

  const show = (x, y, entry) => {
    target = entry;
    menu.hidden = false;
    // Clamp to the viewport so the menu never opens off-screen.
    const r = menu.getBoundingClientRect();
    const px = Math.min(x, window.innerWidth  - r.width  - 6);
    const py = Math.min(y, window.innerHeight - r.height - 6);
    menu.style.left = Math.max(6, px) + "px";
    menu.style.top  = Math.max(6, py) + "px";
  };

  $(".rx-list").addEventListener("contextmenu", e => {
    const row = e.target.closest(".rx-row");
    if (!row || !row._entry) return;
    e.preventDefault();
    show(e.clientX, e.clientY, row._entry);
  });

  menu.addEventListener("click", async e => {
    const item = e.target.closest(".rx-ctx-item");
    if (!item || !target) return;
    const entry = target;
    hide();
    if (item.dataset.act === "ask-claude") {
      let res;
      try {
        res = await api().ask_claude_receipt({
          title:   entry.title || entry.parsed && entry.parsed.title || "",
          name:    entry.name || "",
          path:    entry.path || "",
          account: entry.account || "",
          date:    entry.date_display || "",
        });
      } catch (_) { res = { ok: false }; }
      toast(res && res.ok ? "Opening Claude…"
                          : (res && res.error) || "Couldn't open Claude",
            !(res && res.ok));
    }
  });

  // Dismiss on anything that would make the anchored position stale.
  window.addEventListener("pointerdown", e => {
    if (!menu.hidden && !menu.contains(e.target)) hide();
  });
  window.addEventListener("keydown", e => { if (e.key === "Escape") hide(); });
  window.addEventListener("blur", hide);
  document.addEventListener("scroll", () => { if (!menu.hidden) hide(); }, true);
})();

window.addEventListener("pywebviewready", () => {
  initRunView();
  refreshBadge();
  showVersion();
});

async function showVersion() {
  const el = $("#app-version");
  if (!el) return;
  try {
    const v = await api().app_version();          // "1.1.0 (f22d9cb)"
    if (!v) return;
    el.textContent = "v" + v.split(" ")[0];       // compact: "v1.1.0"
    el.title = "Version " + v;                    // full build in the tooltip
  } catch (_) { /* leave blank */ }
}

// ---- Categories view -------------------------------------------------
let catRows = [];

async function catLoad() {
  try { catRows = await api().list_categories(); }
  catch (_) { catRows = []; }
  catRender();
}

function catRender() {
  const list = $("#cat-list");
  list.innerHTML = "";
  $("#cat-empty").hidden = catRows.length > 0;
  for (const c of catRows) list.appendChild(catRowEl(c));
}

function catRowEl(cat) {
  const n = $("#tpl-cat").content.cloneNode(true);
  const art = n.querySelector(".cat");
  art.dataset.id = cat.id;
  if (cat.exclude) art.classList.add("is-exclude");
  const nameEl = $(".cat-name", art), sellerEl = $(".cat-seller", art),
        prodEl = $(".cat-product", art), subEl = $(".cat-subfolder", art);
  nameEl.value = cat.name || "";
  sellerEl.value = cat.seller || "";
  prodEl.value = cat.product || "";
  subEl.value = cat.subfolder || "";
  const matches = cat.match || [];
  $(".cat-count", art).textContent = matches.length + (matches.length === 1 ? " sender" : " senders");
  if (cat.exclude) { sellerEl.hidden = prodEl.hidden = subEl.hidden = true; }

  const merge = $(".cat-merge", art);
  merge.innerHTML = `<option value="">merge into…</option>` +
    catRows.filter(o => o.id !== cat.id && !o.exclude)
           .map(o => `<option value="${o.id}">${o.name}</option>`).join("");
  merge.addEventListener("change", async () => {
    if (!merge.value) return;
    const into = catRows.find(o => o.id === merge.value);
    if (!confirm(`Move all ${matches.length} sender(s) from “${cat.name}” into “${into.name}” and delete “${cat.name}”?`)) {
      merge.value = ""; return;
    }
    const res = await api().category_merge(cat.id, merge.value);
    toast(res.ok ? `Merged into ${into.name}` : (res.error || "Merge failed"), !res.ok);
    if (res.ok) { catRows = res.categories; catRender(); FB_CATS = null; }
  });

  $(".cat-save", art).addEventListener("click", async () => {
    const res = await api().category_update(cat.id, {
      name: nameEl.value.trim(), seller: sellerEl.value.trim(),
      product: prodEl.value.trim(), subfolder: subEl.value.trim(),
    });
    toast(res.ok ? "Saved" : (res.error || "Save failed"), !res.ok);
    if (res.ok) { catRows = res.categories; FB_CATS = null; }
  });

  $(".cat-del", art).addEventListener("click", async () => {
    if (!confirm(`Delete category “${cat.name}”? Its ${matches.length} sender(s) will no longer auto-file.`)) return;
    const res = await api().category_delete(cat.id);
    toast(res.ok ? "Deleted" : (res.error || "Delete failed"), !res.ok);
    if (res.ok) { catRows = res.categories; catRender(); FB_CATS = null; }
  });

  const box = $(".cat-matches", art), caret = $(".cat-expand", art);
  caret.addEventListener("click", () => {
    box.hidden = !box.hidden;
    caret.textContent = box.hidden ? "▸" : "▾";
    if (!box.hidden) catFillMatches(box, cat);
  });
  return n;
}

function catFillMatches(box, cat) {
  box.innerHTML = "";
  (cat.match || []).forEach((m, i) => {
    const row = document.createElement("div");
    row.className = "cat-match";
    const bits = [m.sender_contains && `from ${m.sender_contains}`,
                  m.subject_contains && `subject ~ “${m.subject_contains}”`,
                  m.exclude_subject_contains && `not subject ~ “${m.exclude_subject_contains}”`,
                  m.body_contains && `body ~ “${m.body_contains}”`,
                  m.seller && `seller → ${m.seller}`,
                  m.product && `product → ${m.product}`].filter(Boolean);
    const span = document.createElement("span");
    span.textContent = bits.join("  ·  ") || "(empty match)";
    const del = document.createElement("button");
    del.className = "cat-match-del"; del.textContent = "✕"; del.title = "Remove this sender";
    del.addEventListener("click", async () => {
      const res = await api().category_remove_match(cat.id, i);
      toast(res.ok ? "Removed" : (res.error || "Failed"), !res.ok);
      if (res.ok) { catRows = res.categories; catRender(); FB_CATS = null; }
    });
    row.append(span, del);
    box.appendChild(row);
  });
}

$("#cat-new-btn").addEventListener("click", async () => {
  const inp = $("#cat-new-name");
  const name = inp.value.trim();
  if (!name) { toast("Name the category first", true); return; }
  const res = await api().category_add(name, {});
  toast(res.ok ? `Added ${name}` : (res.error || "Add failed"), !res.ok);
  if (res.ok) { inp.value = ""; catRows = res.categories; catRender(); FB_CATS = null; }
});
