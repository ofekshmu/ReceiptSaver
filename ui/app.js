"use strict";

const api = () => window.pywebview.api;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

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
async function loadFallbacks() {
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

// Minimal searchable combobox over an item list. `root` is a `.combo` element
// holding `.combo-input` + `.combo-list`; the chosen value lives in
// `root.dataset.value`. items: [{value, label}] — the array may grow later
// (push + setValue). The first item stays pinned (shown even when the current
// filter would exclude it). onChange(value, prev) fires only on an actual
// pick, never on mere typing.
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
      i === 0 || !needle || it.label.toLowerCase().includes(needle)
              || String(it.value).toLowerCase().includes(needle));
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
      if (it.title) li.title = it.title;
      if (i === active) li.classList.add("is-active");
      if (it.value === root.dataset.value) li.classList.add("is-current");
      li.addEventListener("mousedown", e => { e.preventDefault(); choose(it); });
      list.appendChild(li);
    });
  }
  function open() { render(input.value === curLabel() ? "" : input.value); list.hidden = false; }
  function close() { list.hidden = true; active = -1; }
  function setValue(v) {
    root.dataset.value = v;
    input.value = labelFor(v);
    const it = items.find(x => x.value === v);
    input.title = (it && it.title) || "";
  }
  function choose(it) {
    const prev = root.dataset.value;
    setValue(it.value);
    close();
    onChange(it.value, prev);
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

  return {
    setValue,
    get value() { return root.dataset.value; },
    setDisabled(off) { input.disabled = off; root.classList.toggle("is-disabled", off); },
  };
}

// ---- destination picker -------------------------------------------------
// 📁 Browse… (pinned) + every destination ranked by use (Api.destination_suggestions).
const DEST_BROWSE = "__browse__";
let DESTS = null;       // [{path, label, count}] — cleared whenever categories change

async function loadDests() {
  if (!DESTS) {
    try { DESTS = await api().destination_suggestions(); } catch (_) { DESTS = []; }
  }
  return DESTS;
}

// Compact label for a folder: its own suggestion label, else the label of the
// deepest known folder containing it + the remaining parts ("קבלות › a › b").
function destLabel(path) {
  const p = String(path || ""), lp = p.toLowerCase();
  const exact = (DESTS || []).find(x => x.path.toLowerCase() === lp);
  if (exact) return exact.label;
  let best = null;
  for (const d of DESTS || []) {
    const dp = d.path.toLowerCase().replace(/[\\/]+$/, "");
    if (lp.startsWith(dp + "\\") && (!best || dp.length > best.path.length)) best = d;
  }
  if (!best) return p;
  const rest = p.slice(best.path.replace(/[\\/]+$/, "").length + 1).split(/[\\/]+/).filter(Boolean);
  return [best.label, ...rest].join(" › ");
}

async function makeDestPicker(root, current) {
  const dests = await loadDests();
  const items = [{ value: DEST_BROWSE, label: "📁 Browse…", title: "Open the Windows folder picker (it can also create a new folder)" }];
  for (const d of dests) items.push({ value: d.path, label: d.label, title: d.path });
  const ensure = path => {
    if (path && !items.some(i => i.value.toLowerCase() === path.toLowerCase()))
      items.push({ value: path, label: destLabel(path) || path, title: path });
    const hit = items.find(i => i.value.toLowerCase() === String(path || "").toLowerCase());
    return hit ? hit.value : path;
  };
  const combo = makeCombo(root, {
    items,
    onChange: async (val, prev) => {
      if (val !== DEST_BROWSE) return;
      let res;
      try { res = await api().pick_folder(); } catch (_) { res = { ok: false }; }
      if (res && res.ok && res.path) combo.setValue(ensure(res.path));
      else {
        combo.setValue(prev);              // cancelled — restore previous choice
        if (res && !res.ok) toast(res.error || "Couldn't open folder picker", true);
      }
    },
  });
  const fallback = dests.length ? dests[0].path : "";
  combo.setValue(ensure(current || fallback));
  return Object.assign(combo, { set(path) { combo.setValue(ensure(path)); } });
}

// ---- seller / product field --------------------------------------------
// A value, a "use for every mail" tick, or an extraction rule (source + regex)
// with a live preview. preview(source, regex) -> {ok, value, error}; omit it
// where there is no mail to test against (Categories tab).
function makeNameField(slot, { spec, value, preview }) {
  const n = $("#tpl-namefield").content.cloneNode(true).querySelector(".nf");
  slot.innerHTML = "";
  slot.appendChild(n);
  const val = $(".nf-value", n), every = $(".nf-every-cb", n), everyLbl = $(".nf-every", n);
  const xbtn = $(".nf-x-toggle", n), xbox = $(".nf-extract", n);
  const src = $(".nf-source", n), rx = $(".nf-regex", n), out = $(".nf-preview", n);
  let extractOn = false, simple = false, lastHit = null, timer = 0, seq = 0;

  function paint() {
    xbox.hidden = !extractOn || simple;
    xbtn.classList.toggle("on", extractOn);
    xbtn.hidden = simple;
    everyLbl.hidden = simple || extractOn;
    val.placeholder = extractOn && !simple ? "If no match (blank = app suggestion)" : "";
    out.hidden = !preview;
  }
  async function runPreview() {
    if (!preview || !extractOn) return;
    const mine = ++seq;
    if (!rx.value.trim()) { out.textContent = ""; out.className = "nf-preview"; lastHit = null; return; }
    out.textContent = src.value === "body" ? "fetching email…" : "…";
    out.className = "nf-preview";
    let res;
    try { res = await preview(src.value, rx.value); } catch (e) { res = { ok: false, error: String(e) }; }
    if (mine !== seq) return;                       // a newer keystroke won
    lastHit = res && res.ok ? res.value : null;
    if (!res || !res.ok) { out.textContent = (res && res.error) || "preview failed"; out.className = "nf-preview bad"; }
    else if (res.value) { out.textContent = "→ " + res.value; out.className = "nf-preview good"; }
    else { out.textContent = "no match — " + (val.value.trim() ? `uses “${val.value.trim()}”` : "uses the app suggestion"); out.className = "nf-preview warn"; }
  }
  const schedule = () => { clearTimeout(timer); timer = setTimeout(runPreview, 250); };

  xbtn.addEventListener("click", () => { extractOn = !extractOn; paint(); if (extractOn) { rx.focus(); schedule(); } });
  rx.addEventListener("input", schedule);
  src.addEventListener("change", schedule);
  val.addEventListener("input", () => { if (extractOn && !lastHit) schedule(); });

  function load(sp, v) {
    // spec: fixed / extract / null ("app suggestion" → unticked) / undefined (fresh → ticked)
    extractOn = !!(sp && sp.mode === "extract");
    if (sp && sp.mode === "fixed") { val.value = sp.value || ""; every.checked = true; }
    else if (extractOn) {
      src.value = sp.source || "subject"; rx.value = sp.regex || "";
      val.value = sp.fallback || ""; every.checked = true;
    }
    else { val.value = v || ""; every.checked = sp === undefined; }
    lastHit = null; out.textContent = "";
    paint();
    if (extractOn) schedule();
  }
  load(spec, value);

  return {
    load,
    setSimple(on) { simple = on; paint(); },
    setValue(v) { val.value = v || ""; },
    get value() { return val.value.trim(); },
    // what THIS mail is named with + how a new category should remember it
    field() {
      if (extractOn && !simple) {
        return { value: lastHit || val.value.trim(), every_mail: true,
                 extract: { source: src.value, regex: rx.value, fallback: val.value.trim() } };
      }
      return { value: val.value.trim(), every_mail: every.checked, extract: null };
    },
    // the spec to store on a category (Categories tab)
    spec() {
      if (extractOn) {
        const s = { mode: "extract", source: src.value, regex: rx.value };
        if (val.value.trim()) s.fallback = val.value.trim();
        return s;
      }
      return every.checked && val.value.trim() ? { mode: "fixed", value: val.value.trim() } : null;
    },
  };
}

function specSummary(sp) {
  if (!sp) return "app suggestion";
  if (sp.mode === "fixed") return sp.value;
  const where = { subject: "subject", body: "body", sender_name: "sender name" }[sp.source] || sp.source;
  return `from ${where}`;
}

const asList = v => (Array.isArray(v) ? v : (v == null ? [] : [v]))
  .map(x => String(x).trim()).filter(Boolean);
const ATTACH_TEXT = { none: "no attachment", one: "exactly 1 attachment", many: "2+ attachments" };

function matchText(m) {
  const q = (lbl, k) => asList(m[k]).map(v => `${lbl} “${v}”`);
  return [...asList(m.sender_contains).map(v => `from ${v}`),
          ...q("subject ~", "subject_contains"),
          ...q("subject ≁", "exclude_subject_contains"),
          ...q("body ~", "body_contains"),
          ...q("body ≁", "exclude_body_contains"),
          ATTACH_TEXT[m.attachments]].filter(Boolean).join("  ·  ")
         || "(empty rule)";
}

// ---- match-rule widget ------------------------------------------------------
// One pill list per condition (ALL positives must hold, NO negative may), an
// attachments select, and optional keyword chips (sender / subject / body) that
// add/remove pills. ≠ on a subject/body pill flips it between "contains" and
// "must NOT contain". suggest(includeBody) -> Api.keyword_suggestions result.
const MR_FLIP = { subject_contains: "exclude_subject_contains",
                  exclude_subject_contains: "subject_contains",
                  body_contains: "exclude_body_contains",
                  exclude_body_contains: "body_contains" };
const MR_GROUP_KEYS = { sender: ["sender_contains"],
                        subject: ["subject_contains", "exclude_subject_contains"],
                        body: ["body_contains", "exclude_body_contains"] };

function makeMatchRule(slot, { entry, suggest, attachmentHint } = {}) {
  const root = $("#tpl-matchrule").content.cloneNode(true).querySelector(".mr");
  slot.innerHTML = "";
  slot.appendChild(root);
  const vals = {};
  const rows = {};
  $$(".mr-row[data-key]", root).forEach(r => { rows[r.dataset.key] = r; vals[r.dataset.key] = []; });
  const att = $(".mr-attachments", root);
  const sugBox = $(".mr-suggest", root);
  let chips = { sender: [], subject: [], body: [] };
  let bodyState = "idle";                 // idle | loading | done | error

  function renderPills(key) {
    const box = $(".mr-pills", rows[key]);
    box.innerHTML = "";
    vals[key].forEach(v => {
      const pill = document.createElement("span");
      pill.className = "mr-pill" + (key.startsWith("exclude_") ? " neg" : "");
      const t = document.createElement("bdi");
      t.textContent = v;
      pill.appendChild(t);
      if (MR_FLIP[key]) {
        const flip = document.createElement("button");
        flip.type = "button"; flip.className = "mr-flip"; flip.textContent = "≠";
        flip.title = key.startsWith("exclude_") ? "Make it “must contain”" : "Make it “must NOT contain”";
        flip.addEventListener("click", () => { remove(key, v); add(MR_FLIP[key], v); });
        pill.appendChild(flip);
      }
      const x = document.createElement("button");
      x.type = "button"; x.className = "mr-x"; x.textContent = "×"; x.title = "Remove";
      x.addEventListener("click", () => remove(key, v));
      pill.appendChild(x);
      box.appendChild(pill);
    });
  }
  function add(key, v) {
    v = String(v || "").trim();
    if (!v || vals[key].includes(v)) return;
    vals[key].push(v); renderPills(key); renderChips();
  }
  function remove(key, v) {
    vals[key] = vals[key].filter(x => x !== v); renderPills(key); renderChips();
  }
  function renderChips() {
    for (const group of ["sender", "subject", "body"]) {
      const box = $(`.mr-sg[data-group=${group}] .mr-chips`, root);
      box.innerHTML = "";
      if (group === "body" && bodyState !== "done") {
        const note = document.createElement("span");
        note.className = "mr-note";
        note.textContent = { idle: "loads when you start editing this email",
                             loading: "reading the email…",
                             error: chips.bodyError || "couldn't read the email body" }[bodyState] || "";
        box.appendChild(note);
        continue;
      }
      if (!chips[group].length) {
        const note = document.createElement("span");
        note.className = "mr-note"; note.textContent = "nothing distinctive";
        box.appendChild(note);
        continue;
      }
      chips[group].forEach(k => {
        const keys = MR_GROUP_KEYS[group];
        const on = keys.find(key => vals[key].includes(k));
        const c = document.createElement("button");
        c.type = "button";
        c.className = "mr-chip" + (on ? " on" : "") + (on && on.startsWith("exclude_") ? " neg" : "");
        c.textContent = k;
        c.title = on ? "Remove from the rule" : "Add to the rule";
        c.addEventListener("click", () => on ? remove(on, k) : add(keys[0], k));
        box.appendChild(c);
      });
    }
  }
  // typing + Enter adds a pill (never submits the form)
  Object.entries(rows).forEach(([key, row]) => {
    const inp = $(".mr-add", row);
    inp.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); add(key, inp.value); inp.value = ""; }
    });
  });
  if (attachmentHint != null) {
    $(".mr-att-hint", root).textContent =
      `this email: ${attachmentHint} document${attachmentHint === 1 ? "" : "s"}`;
  }

  async function loadSuggestions(includeBody) {
    if (!suggest) return;
    if (includeBody) { bodyState = "loading"; renderChips(); }
    let res;
    try { res = await suggest(includeBody); } catch (e) { res = { ok: false, error: String(e) }; }
    if (res && res.ok) {
      chips.sender = res.sender || []; chips.subject = res.subject || [];
      if (includeBody) {
        chips.body = res.body || [];
        bodyState = res.body_error ? "error" : "done";
        chips.bodyError = res.body_error;
      }
    } else if (includeBody) { bodyState = "error"; chips.bodyError = res && res.error; }
    renderChips();
  }

  const api_ = {
    set(e) {
      e = e || {};
      for (const key of Object.keys(vals)) vals[key] = asList(e[key]);
      Object.keys(vals).forEach(renderPills);
      att.value = e.attachments || "";
      renderChips();
    },
    // the rule as entered, including text typed but not yet Enter-ed
    value() {
      const out = {};
      for (const [key, row] of Object.entries(rows)) {
        const pending = $(".mr-add", row).value.trim();
        out[key] = pending && !vals[key].includes(pending) ? [...vals[key], pending] : [...vals[key]];
      }
      if (att.value) out.attachments = att.value;
      return out;
    },
    clearInputs() { $$(".mr-add", root).forEach(i => { i.value = ""; }); },
    showSuggestions(on) { sugBox.hidden = !on || !suggest; },
    loadBody() { if (bodyState === "idle") loadSuggestions(true); },
  };
  api_.set(entry);
  if (suggest) loadSuggestions(false);
  return api_;
}

const hasAnchor = m => asList(m.sender_contains).length + asList(m.subject_contains).length > 0;

// ---- the fallback form ---------------------------------------------------
const FB_NEWCAT = "__new__";
let FB_CATS = null;

async function loadCats() {
  if (!FB_CATS) {
    try { FB_CATS = await api().list_categories(); } catch (_) { FB_CATS = []; }
  }
  return FB_CATS;
}

function categoriesChanged() { FB_CATS = null; DESTS = null; }

// Shows/hides/pins the fields block for the chosen option:
//   new category  → everything editable (name, destination, seller/product with
//                   tick + extract, match rule)
//   existing      → destination pinned to the category; seller/product show what
//                   THIS mail resolves to (editable one-off); its rules listed
//                   read-only + one new rule row that gets added
//   move once     → destination + plain seller/product, no rule
//   exclude       → the match rule only
//   skip          → nothing
function syncFieldsForKind(form) {
  const st = form._fb;
  const kind = (form.querySelector("input[name=kind]:checked") || {}).value;
  const catVal = $(".f-cat-assign", form).dataset.value;
  const existing = kind === "category" && catVal && catVal !== FB_NEWCAT
    ? (FB_CATS || []).find(c => c.id === catVal) : null;
  const isNew = kind === "category" && !existing;
  const fields = $(".fb-fields", form), head = $(".fb-fields-head", form);
  const show = (sel, on) => { const el = $(sel, form); if (el) el.hidden = !on; };

  $(".f-cat-name", form).hidden = !isNew;
  $$(".opt", form).forEach(o =>
    o.classList.toggle("is-selected", !!$(`input[name=kind][value="${kind}"]`, o)));
  // the fields expand directly under the chosen option
  const slot = $(`input[name=kind][value="${kind}"]`, form)?.closest(".opt")?.querySelector(".opt-slot");
  if (slot && fields.parentElement !== slot) slot.appendChild(fields);
  fields.hidden = kind === "skip";
  if (fields.hidden) return;

  head.textContent =
      isNew             ? "New category — the fields below define it"
    : existing          ? `Category: ${existing.name} — files to its folder`
    : kind === "once"   ? "This receipt only — nothing is remembered"
    : kind === "exclude"? "Exclude emails matching this rule"
    : "";
  head.hidden = !head.textContent;

  show(".fd-dest", kind !== "exclude");
  show(".fd-seller", kind !== "exclude");
  show(".fd-product", kind !== "exclude");
  show(".fd-match", kind === "category" || kind === "exclude");

  if (st.dest) st.dest.setDisabled(!!existing);
  st.seller.setSimple(!isNew);
  st.product.setSimple(!isNew);
  if (st.match) st.match.showSuggestions(kind === "category" || kind === "exclude");

  const box = $(".fb-match-existing", form);
  box.hidden = !existing;
  box.innerHTML = "";
  if (existing) {
    (existing.match || []).forEach(m => {
      const row = document.createElement("div");
      row.className = "cat-match";
      row.textContent = matchText(m);
      box.appendChild(row);
    });
    const note = document.createElement("div");
    note.className = "hint";
    note.textContent = "＋ the rule below will be added:";
    box.appendChild(note);
  }
}

async function wireForm(scope, it, s) {
  const form = $(".fb-form", scope);
  const st = form._fb = {};
  st.seller = makeNameField($(".fd-seller .nf-slot", form), {
    value: s.seller, preview: (src, rx) => api().preview_extract(it.message_id, src, rx) });
  st.product = makeNameField($(".fd-product .nf-slot", form), {
    value: s.product, preview: (src, rx) => api().preview_extract(it.message_id, src, rx) });
  st.match = makeMatchRule($(".fd-match .mr-slot", form), {
    entry: { sender_contains: s.match_sender_contains },
    attachmentHint: s.attachment_count,
    suggest: inclBody => api().keyword_suggestions(it.message_id, inclBody),
  });
  // body keywords need the mailbox — fetch only once the user engages with this email
  ["focusin", "pointerdown"].forEach(ev =>
    form.addEventListener(ev, () => st.match.loadBody(), { once: true }));
  // clicking anywhere on an option (not just its radio) selects it
  $$(".opt", form).forEach(o => o.addEventListener("click", e => {
    const r = $("input[name=kind]", o);
    if (r.checked || e.target.closest(".opt-slot, .opt-body, input, button, select, a")) return;
    r.checked = true;
    syncFieldsForKind(form);
  }));
  if (s.kind) {
    const r = form.querySelector(`input[name=kind][value="${s.kind}"]`);
    if (r) r.checked = true;
  }
  st.dest = await makeDestPicker($(".f-dest", form), s.destination);

  // "File under a category" picker
  const cats = await loadCats();
  const items = [{ value: FB_NEWCAT, label: "＋ New category…" }];
  cats.filter(c => !c.exclude).forEach(c =>
    items.push({ value: c.id, label: `${c.name} — ${destLabel(c.destination) || "קבלות"}`,
                 title: c.destination || "" }));
  const onCat = async val => {
    const cat = val === FB_NEWCAT ? null : cats.find(c => c.id === val);
    if (cat) {
      if (cat.destination) st.dest.set(cat.destination);
      let pv = null;
      try { pv = await api().preview_category(it.message_id, cat.id); } catch (_) {}
      if (pv && pv.ok) {
        st.dest.set(pv.destination);
        st.seller.setValue(pv.seller);
        st.product.setValue(pv.product);
      }
    } else {
      st.seller.load(undefined, s.seller);
      st.product.load(undefined, s.product);
      st.dest.set(s.destination);
    }
    syncFieldsForKind(form);
  };
  const catCombo = makeCombo($(".f-cat-assign", form), { items, onChange: onCat });
  const known = s.category_id && cats.find(c => c.id === s.category_id && !c.exclude);
  catCombo.setValue(known ? known.id : FB_NEWCAT);
  if (known) await onCat(known.id);

  form.querySelectorAll("input[name=kind]").forEach(r =>
    r.addEventListener("change", () => syncFieldsForKind(form)));
  syncFieldsForKind(form);

  form.addEventListener("submit", async e => {
    e.preventDefault();
    let kind = $("input[name=kind]:checked", form).value;
    const decision = { kind };
    if (kind === "category" || kind === "exclude") decision.match = st.match.value();
    if (kind === "category") {
      const pick = $(".f-cat-assign", form).dataset.value;
      if (pick === FB_NEWCAT) {
        decision.kind = "new_category";
        decision.category_name = $(".f-cat-name", form).value.trim();
        if (!decision.category_name) { toast("Name the new category first", true); return; }
        decision.seller = st.seller.field();
        decision.product = st.product.field();
        decision.destination = st.dest.value;
      } else {
        decision.category_id = pick;
        decision.seller = { value: st.seller.value };
        decision.product = { value: st.product.value };
      }
    } else if (kind === "once") {
      decision.destination = st.dest.value;
      decision.seller = { value: st.seller.value };
      decision.product = { value: st.product.value };
    }
    if (decision.match && !hasAnchor(decision.match)) {
      toast("The match rule needs a sender or subject condition", true); return;
    }
    if ((decision.kind === "new_category" || kind === "once")
        && (!decision.destination || decision.destination === DEST_BROWSE)) {
      toast("Pick a destination folder first", true); return;
    }
    const btn = $(".fb-apply", form);
    btn.disabled = true;
    let res;
    try { res = await api().apply_fallback(it.message_id, decision); }
    catch (err) { res = { ok: false, error: String(err) }; }
    btn.disabled = false;
    if (res && res.ok) {
      form.closest(".card").remove();
      toast(kind === "skip" ? "Removed from the list" : `Resolved: ${st.seller.value || it.subject}`);
      if (kind !== "skip" && kind !== "once") categoriesChanged();
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
let RECEIPTS_ROOT = null;   // the קבלות root — where a category without a destination files

async function catLoad() {
  try { catRows = await api().list_categories(); }
  catch (_) { catRows = []; }
  await loadDests();
  if (!RECEIPTS_ROOT) {
    try { RECEIPTS_ROOT = ((await api().list_roots())[0] || {}).path || null; } catch (_) {}
  }
  catRender();
}

function catRender() {
  const list = $("#cat-list");
  list.innerHTML = "";
  $("#cat-empty").hidden = catRows.length > 0;
  for (const c of catRows) list.appendChild(catRowEl(c));
}

function catAfterWrite(res, okMsg, rerender = true) {
  toast(res.ok ? okMsg : (res.error || "Failed"), !res.ok);
  if (!res.ok) return false;
  catRows = res.categories;
  categoriesChanged();
  if (rerender) loadDests().then(catRender);
  return true;
}

// "<folder> · seller: <x> · product: <y>" — each value in its own <bdi> so
// Hebrew values don't reorder the English labels around them.
function catSummary(el, cat) {
  el.textContent = "";
  if (cat.exclude) { el.textContent = "excluded — matching mail is skipped"; return; }
  const parts = [["", destLabel(cat.destination || RECEIPTS_ROOT) || "קבלות"],
                 ["seller: ", specSummary(cat.seller)],
                 ["product: ", specSummary(cat.product)]];
  parts.forEach(([lbl, val], i) => {
    if (i) el.append("  ·  ");
    el.append(lbl);
    const b = document.createElement("bdi");
    b.textContent = val;
    el.appendChild(b);
  });
  el.title = el.textContent;
}

function catRowEl(cat) {
  const n = $("#tpl-cat").content.cloneNode(true);
  const art = n.querySelector(".cat");
  art.dataset.id = cat.id;
  if (cat.exclude) art.classList.add("is-exclude");
  const nameEl = $(".cat-name", art);
  nameEl.value = cat.name || "";
  catSummary($(".cat-summary", art), cat);
  const matches = cat.match || [];
  $(".cat-count", art).textContent = matches.length + (matches.length === 1 ? " rule" : " rules");

  const merge = $(".cat-merge", art);
  merge.innerHTML = `<option value="">merge into…</option>` +
    catRows.filter(o => o.id !== cat.id && !!o.exclude === !!cat.exclude)
           .map(o => `<option value="${o.id}">${o.name}</option>`).join("");
  merge.addEventListener("change", async () => {
    if (!merge.value) return;
    const into = catRows.find(o => o.id === merge.value);
    if (!confirm(`Move all ${matches.length} rule(s) from “${cat.name}” into “${into.name}” and delete “${cat.name}”?\n\nThose emails will then be filed and named the way “${into.name}” says.`)) {
      merge.value = ""; return;
    }
    catAfterWrite(await api().category_merge(cat.id, merge.value), `Merged into ${into.name}`);
  });

  // editor (built lazily on first expand)
  const editor = $(".cat-editor", art), caret = $(".cat-expand", art);
  let ed = null;
  async function build() {
    if (cat.exclude) {
      $(".cat-fields", editor).hidden = true;
    } else {
      ed = {
        dest: await makeDestPicker($(".f-dest", editor), cat.destination || RECEIPTS_ROOT),
        seller: makeNameField($(".fd-seller .nf-slot", editor), { spec: cat.seller ?? null }),
        product: makeNameField($(".fd-product .nf-slot", editor), { spec: cat.product ?? null }),
      };
    }
    const box = $(".cat-matches", editor);
    box.innerHTML = "";
    matches.forEach((m, i) => {
      const row = document.createElement("div");
      row.className = "cat-match";
      const span = document.createElement("span");
      span.textContent = matchText(m);
      const del = document.createElement("button");
      del.className = "cat-match-del"; del.textContent = "✕"; del.title = "Remove this rule";
      del.addEventListener("click", async () =>
        catAfterWrite(await api().category_remove_match(cat.id, i), "Rule removed"));
      row.append(span, del);
      box.appendChild(row);
    });
    const rule = makeMatchRule($(".cat-add-match .mr-slot", editor), {});
    $(".cat-add-match-btn", editor).addEventListener("click", async () => {
      const m = rule.value();
      if (!hasAnchor(m)) {
        toast("A rule needs a sender or subject condition", true); return;
      }
      catAfterWrite(await api().category_add_match(cat.id, m), "Rule added");
    });
  }
  caret.addEventListener("click", async () => {
    editor.hidden = !editor.hidden;
    caret.textContent = editor.hidden ? "▸" : "▾";
    if (!editor.hidden && !editor._built) { editor._built = true; await build(); }
  });

  $(".cat-save", art).addEventListener("click", async () => {
    const patch = { name: nameEl.value.trim() };
    if (ed) {
      const dest = ed.dest.value;
      patch.destination = dest && dest !== DEST_BROWSE ? dest : cat.destination;
      patch.seller = ed.seller.spec();
      patch.product = ed.product.spec();
    }
    catAfterWrite(await api().category_update(cat.id, patch), "Saved");
  });

  $(".cat-del", art).addEventListener("click", async () => {
    if (!confirm(`Delete category “${cat.name}”? Its ${matches.length} rule(s) will no longer auto-file.`)) return;
    catAfterWrite(await api().category_delete(cat.id), "Deleted");
  });
  return n;
}

$("#cat-new-btn").addEventListener("click", async () => {
  const inp = $("#cat-new-name");
  const name = inp.value.trim();
  if (!name) { toast("Name the category first", true); return; }
  const res = await api().category_add(name, { destination: RECEIPTS_ROOT });
  if (catAfterWrite(res, `Added ${name} — expand it to set its folder, names and rules`)) inp.value = "";
});
