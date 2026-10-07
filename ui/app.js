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
  if (t.dataset.view === "rules") rulesLoad();
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
  syncStickyOffsets();
  updateHandoffButton();
  refreshBadge(items.length);
}

// The pinned option row sits right under the pinned email header, so each card
// needs its header's height (--fbh). Measured on insert + resize; the
// ResizeObserver in fallbackCard keeps it right when the header itself changes.
function syncStickyOffsets() {
  for (const card of $$("#fb-list .card.fb, #fb-list .card.fb-compact")) {
    const head = $(".fb-head", card);
    if (head) card.style.setProperty("--fbh", head.offsetHeight + "px");
  }
}
window.addEventListener("resize", syncStickyOffsets);

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
let DESTS = null;       // [{path, label, count}] — cleared whenever rules change

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
    // what THIS mail is named with + how a new rule should remember it
    field() {
      if (extractOn && !simple) {
        return { value: lastHit || val.value.trim(), every_mail: true,
                 extract: { source: src.value, regex: rx.value, fallback: val.value.trim() } };
      }
      return { value: val.value.trim(), every_mail: every.checked, extract: null };
    },
    // the spec to store on a rule (Rules tab)
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

// ---- tile grid (roots in "File under a root") ------------------------------
// "＋ New …" tile (always shown) + one pastel tile per item; the search box
// filters tiles by `hay`. The chosen value lives in root.dataset.value;
// onChange(value) fires on a pick. items: [{value, name, sub, meta, count,
// color, title, hay}].
const PASTELS = ["#f9d5dc", "#fde0c8", "#fbefb8", "#d6efcf", "#cbece8",
                 "#d3e3f8", "#e0d9f6", "#f3d6ec"];
const NEW = "__new__";
const TEMP = "__temp__";    // a not-yet-saved root picked via ＋ New root

// With `onNew`, the ＋ tile is an action: onNew() -> item | null; a returned
// item is shown as a temporary tile (right after ＋, replacing any previous
// one) and selected.
function makeTileGrid(root, items, { value, onChange, newLabel, newTitle, onNew }) {
  const grid = $(".cat-grid", root), search = $(".cat-search", root);
  const empty = $(".cat-grid-empty", root);
  grid.innerHTML = "";
  const tiles = [];
  const line = (cls, text, el) => {
    const d = document.createElement(el || "div");
    d.className = cls;
    d.textContent = text;
    return d;
  };
  const tile = (val, build, hay, before) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cat-tile";
    b.dataset.value = val;
    b.setAttribute("role", "option");
    build(b);
    b.addEventListener("click", () => (val === NEW && onNew ? addNew() : pick(val)));
    grid.insertBefore(b, before || null);
    const t = { el: b, val, hay: (hay || "").toLowerCase() };
    if (before) tiles.splice(1, 0, t); else tiles.push(t);
  };
  const itemTile = (it, before) => tile(it.value, b => {
    b.style.setProperty("--tile", it.color || PASTELS[0]);
    if (it.value === TEMP) b.classList.add("is-temp");
    const name = line("ct-name", it.name); name.dir = "auto";
    const sub = line("ct-dest", it.sub || ""); sub.dir = "auto";
    const meta = line("ct-meta", "");
    if (it.meta) { const m = line("ct-seller", it.meta, "span"); m.dir = "auto"; meta.append(m); }
    meta.append(line("ct-count", it.count || "", "span"));
    b.append(line("ct-check", "✓"), name, sub, meta);
    b.title = it.title || it.name;
  }, it.hay, before);

  tile(NEW, b => {
    b.classList.add("is-new");
    b.append(line("ct-plus", "＋"), line("ct-name", newLabel));
    b.title = newTitle || "";
  });
  for (const it of items) itemTile(it);

  async function addNew() {
    const it = await onNew();
    if (!it) return;                         // cancelled — keep the current choice
    const old = tiles.findIndex(t => t.val === TEMP);
    if (old >= 0) { tiles[old].el.remove(); tiles.splice(old, 1); }
    itemTile({ ...it, value: TEMP }, tiles[0].el.nextSibling);
    search.value = "";
    filter();
    grid.scrollTop = 0;
    pick(TEMP);
  }

  function paint() {
    for (const t of tiles) {
      const on = t.val === root.dataset.value;
      t.el.classList.toggle("is-selected", on);
      t.el.setAttribute("aria-selected", on ? "true" : "false");
    }
  }
  function pick(val) {
    root.dataset.value = val;
    paint();
    onChange(val);
  }
  function filter() {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    for (const t of tiles) {
      const vis = t.val === NEW || !q || t.hay.includes(q);
      t.el.hidden = !vis;
      if (vis && t.val !== NEW) shown++;
    }
    empty.hidden = !q || shown > 0;
  }
  search.addEventListener("input", filter);
  search.addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    e.preventDefault();                      // never submit the form from here
    const first = tiles.find(t => t.val !== NEW && !t.el.hidden) || tiles[0];
    if (first) { pick(first.val); first.el.focus(); }
  });

  root.dataset.value = value || "";
  paint();
  const sel = tiles.find(t => t.val === value);
  if (sel && value !== NEW) {
    // scroll the grid (only the grid) when the pre-selected tile is below its fold
    requestAnimationFrame(() => {
      if (sel.el.offsetTop + sel.el.offsetHeight > grid.clientHeight)
        grid.scrollTop = sel.el.offsetTop - 8;
    });
  }
  return { get value() { return root.dataset.value; } };
}

// ---- rules data (rules.json) ---------------------------------------------
let RULES = null;       // {roots, rules, counts} — cleared whenever rules change

async function loadRules() {
  if (!RULES) {
    try { RULES = await api().list_rules(); }
    catch (_) { RULES = { roots: [], rules: [], counts: {} }; }
  }
  return RULES;
}

function rulesChanged() { RULES = null; DESTS = null; }

function countsOf(data) {
  const c = {};
  for (const r of data.rules || []) if (r.root) c[r.root] = (c[r.root] || 0) + 1;
  return c;
}

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

// Innermost folder name; when several roots share it, add parent folders
// (2, 3, … levels) until each label is unique. folders: [path] -> [label]
function shortFolderLabels(folders) {
  const parts = folders.map(f => String(f || "").split(/[\\/]+/).filter(Boolean));
  const depth = parts.map(() => 1);
  const label = i => parts[i].slice(-depth[i]).join(" › ");
  for (let guard = 0; guard < 12; guard++) {
    const seen = {};
    parts.forEach((_, i) => { (seen[label(i).toLowerCase()] ||= []).push(i); });
    const clashes = Object.values(seen).filter(g => g.length > 1);
    if (!clashes.length) break;
    let grew = false;
    for (const g of clashes) for (const i of g)
      if (depth[i] < parts[i].length) { depth[i]++; grew = true; }
    if (!grew) break;
  }
  return parts.map((_, i) => label(i));
}

// Does showing a root's folder tell you anything its name doesn't?
function folderAddsInfo(name, folder) {
  const inner = String(folder || "").split(/[\\/]+/).filter(Boolean).pop() || "";
  return !!inner && !String(name || "").toLowerCase().includes(inner.toLowerCase());
}

function rootTileItems(data) {
  const subs = shortFolderLabels(data.roots.map(r => r.folder));
  return data.roots.map((r, i) => {
    const rs = data.rules.filter(x => !x.exclude && x.root === r.id);
    const senders = rs.flatMap(x => (x.match || []).flatMap(m => asList(m.sender_contains)));
    return {
      value: r.id, name: r.name, color: r.color,
      // the folder line only when it adds something: most root names are
      // already derived from their folder (full path stays on hover)
      sub: folderAddsInfo(r.name, r.folder) ? subs[i] : "",
      count: String(rs.length),
      title: `${r.name}\n${r.folder}\n${plural(rs.length, "rule")}`,
      hay: [r.name, r.folder, destLabel(r.folder), ...rs.map(x => x.name),
            ...rs.map(x => specSummary(x.seller)), ...senders].join(" "),
    };
  });
}

// ---- the fallback form ---------------------------------------------------
// "Change/add rule": root tiles → that root's rule chips (＋ New rule first)
// → the fields block, which depends on the choice:
//   new rule      → rule name, seller/product with tick + extract, match rule
//                   (+ root name for a ＋ New root — its folder was picked when
//                   the ＋ tile was clicked; it's saved only on Apply)
//   existing rule → seller/product show what THIS mail resolves to (one-off);
//                   its alternatives listed + this mail's conditions added
//   move once     → destination + plain seller/product, nothing remembered
//   exclude       → the match rule only
//   skip          → nothing
// Switching between the four options animates the change: the option you
// leave closes and the one you pick opens (heights tweened between the
// before/after layouts). Skipped with "reduce motion" and on the first sync.
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

function syncFieldsForKind(form) {
  const st = form._fb || {};
  const kind = (form.querySelector("input[name=kind]:checked") || {}).value;
  const animate = st.lastKind && st.lastKind !== kind && !REDUCE_MOTION.matches;
  const opts = $$(".opt", form);
  const before = animate ? opts.map(o => o.getBoundingClientRect().height) : null;
  applyFieldsForKind(form);
  st.lastKind = kind;
  if (!animate) return;
  // bring the newly opened option up to just under the pinned email header —
  // measured now, on the final layout, before the height tweens start
  const view = form.closest(".view");
  const opened = opts.find(o => o.contains(form.querySelector("input[name=kind]:checked")));
  if (view && opened) {
    const pinned = parseFloat(getComputedStyle(form.closest(".card")).getPropertyValue("--fbh")) || 0;
    const top = view.scrollTop + opened.getBoundingClientRect().top
              - view.getBoundingClientRect().top - pinned;
    view.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }
  opts.forEach((o, i) => {
    const after = o.getBoundingClientRect().height;
    if (Math.abs(after - before[i]) < 1) return;
    o.getAnimations().forEach(a => a.cancel());
    o.style.overflow = "hidden";
    const anim = o.animate([{ height: `${before[i]}px` }, { height: `${after}px` }],
                           { duration: 220, easing: "cubic-bezier(.2, .7, .2, 1)" });
    anim.onfinish = anim.oncancel = () => { o.style.overflow = ""; };
  });
}

function applyFieldsForKind(form) {
  const st = form._fb;
  const kind = (form.querySelector("input[name=kind]:checked") || {}).value;
  const data = st.data || { roots: [], rules: [] };
  const rootVal = $(".f-root-pick", form).dataset.value;
  const isNewRoot = rootVal === TEMP;
  const root = isNewRoot ? null : data.roots.find(r => r.id === rootVal);
  const noRoot = !isNewRoot && !root;
  const existing = kind === "file" && root && st.ruleVal && st.ruleVal !== NEW
    ? data.rules.find(r => r.id === st.ruleVal) : null;
  const isNewRule = kind === "file" && !existing && !noRoot;
  const fields = $(".fb-fields", form), head = $(".fb-fields-head", form);
  const show = (sel, on) => { const el = $(sel, form); if (el) el.hidden = !on; };

  // ① Root collapses to a one-line summary once a root is chosen ("change"
  // reopens it); ② Rule stays collapsed until there is a root.
  const stepRoot = $(".step-root", form), stepRule = $(".step-rule", form);
  stepRoot.classList.toggle("collapsed", !noRoot && !st.rootOpen);
  $(".step-summary", stepRoot).textContent =
    isNewRoot ? `＋ ${$(".f-root-name", form).value.trim() || "new root"}` : root ? root.name : "";
  $(".step-toggle", stepRoot).hidden = noRoot;
  $(".step-toggle", stepRoot).textContent = st.rootOpen ? "done" : "change";
  stepRule.classList.toggle("collapsed", noRoot);
  $(".step-summary", stepRule).textContent =
    noRoot ? "pick a root first" : existing ? existing.name : "＋ new rule";

  show(".f-root-name", kind === "file" && isNewRoot);
  show(".rule-pick", kind === "file" && !!root);
  show(".f-rule-name", isNewRule);
  $$(".opt", form).forEach(o =>
    o.classList.toggle("is-selected", !!$(`input[name=kind][value="${kind}"]`, o)));
  // the fields expand directly under the chosen option
  const slot = $(`input[name=kind][value="${kind}"]`, form)?.closest(".opt")?.querySelector(".opt-slot");
  if (slot && fields.parentElement !== slot) slot.appendChild(fields);
  fields.hidden = kind === "skip";
  if (fields.hidden) return;

  head.textContent =
      kind === "file" && noRoot ? "Pick a root above — or ＋ New root to choose a folder"
    : existing            ? `Rule: ${existing.name} — files to ${root ? root.name : "its root"}`
    : isNewRule && isNewRoot ? "New root + new rule — saved when you Apply"
    : isNewRule           ? `New rule in ${root ? root.name : "this root"}`
    : kind === "once"     ? "This receipt only — nothing is remembered"
    : kind === "exclude"  ? "Exclude emails matching this rule"
    : "";
  head.hidden = !head.textContent;

  show(".fd-dest", kind === "once");
  show(".fd-seller", kind !== "exclude");
  show(".fd-product", kind !== "exclude");
  show(".fd-match", kind === "file" || kind === "exclude");

  st.seller.setSimple(!isNewRule);
  st.product.setSimple(!isNewRule);
  st.match.showSuggestions(kind === "file" || kind === "exclude");

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
    note.textContent = "＋ this email's conditions are added as another alternative:";
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
  const data = st.data = await loadRules();
  const selectFile = () => { $('input[name=kind][value="file"]', form).checked = true; };
  st.rootOpen = false;
  $(".step-root .step-head", form).addEventListener("click", () => {
    if (!$(".step-root", form).classList.contains("collapsed") && !st.rootOpen) return;  // nothing chosen yet
    st.rootOpen = !st.rootOpen;
    syncFieldsForKind(form);
  });
  $(".f-root-name", form).addEventListener("input", () => syncFieldsForKind(form));

  // rule chips for the chosen root: ＋ New rule + the root's rules
  const chipsBox = $(".rule-chips", form);
  function renderRuleChips() {
    const rootVal = $(".f-root-pick", form).dataset.value;
    chipsBox.innerHTML = "";
    if (!data.roots.some(r => r.id === rootVal)) return;
    const add = (val, label, title) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "rule-chip" + (val === NEW ? " is-new" : "") + (val === st.ruleVal ? " on" : "");
      c.textContent = label;
      c.dir = "auto";
      if (title) c.title = title;
      c.addEventListener("click", () => { selectFile(); onRule(val); });
      chipsBox.appendChild(c);
    };
    add(NEW, "＋ New rule", "Create a new rule from this email");
    data.rules.filter(r => !r.exclude && r.root === rootVal).forEach(r =>
      add(r.id, r.name, (r.match || []).map(matchText).join("\n")));
  }
  async function onRule(val) {
    st.ruleVal = val;
    renderRuleChips();
    const rule = val === NEW ? null : data.rules.find(r => r.id === val);
    if (rule) {
      let pv = null;
      try { pv = await api().preview_rule(it.message_id, rule.id); } catch (_) {}
      if (pv && pv.ok) { st.seller.setValue(pv.seller); st.product.setValue(pv.product); }
    } else {
      st.seller.load(undefined, s.seller);
      st.product.load(undefined, s.product);
    }
    syncFieldsForKind(form);
  }
  const knownRule = s.rule_id && data.rules.find(r => r.id === s.rule_id && !r.exclude);
  const startRoot = (knownRule && knownRule.root) || s.root_id;
  const rootOk = startRoot && data.roots.some(r => r.id === startRoot);
  makeTileGrid($(".f-root-pick", form), rootTileItems(data), {
    value: rootOk ? startRoot : "",
    newLabel: "New root",
    newTitle: "Choose a folder (the folder picker can also create one) — it becomes a new root when you Apply",
    // ＋ New root opens the folder picker; the root is only saved on Apply
    onNew: async () => {
      let res;
      try { res = await api().pick_folder(); } catch (_) { res = { ok: false }; }
      if (!res || !res.ok) { if (res && res.error) toast(res.error, true); return null; }
      if (!res.path) return null;
      const name = res.path.split(/[\\/]+/).filter(Boolean).pop() || res.path;
      const used = data.roots.map(r => r.color);
      const color = PASTELS.reduce((best, c) =>
        used.filter(u => u === c).length < used.filter(u => u === best).length ? c : best, PASTELS[0]);
      st.newRoot = { folder: res.path, color };
      $(".f-root-name", form).value = name;
      return { name, color, sub: "", count: "new root", title: `${res.path}\nsaved when you Apply`,
               hay: `${name} ${res.path}` };
    },
    // picking a root collapses step ① so step ② has the room
    onChange: val => { selectFile(); st.rootOpen = false; return onRule(NEW); },
  });
  st.ruleVal = knownRule && rootOk ? knownRule.id : NEW;
  await onRule(st.ruleVal);

  form.querySelectorAll("input[name=kind]").forEach(r =>
    r.addEventListener("change", () => syncFieldsForKind(form)));
  syncFieldsForKind(form);

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const kind = $("input[name=kind]:checked", form).value;
    const decision = { kind };
    if (kind === "file" || kind === "exclude") decision.match = st.match.value();
    if (kind === "file") {
      const rootVal = $(".f-root-pick", form).dataset.value;
      const isRoot = data.roots.some(r => r.id === rootVal);
      if (!isRoot && rootVal !== TEMP) { toast("Pick a root first — or ＋ New root to choose a folder", true); return; }
      if (isRoot && st.ruleVal && st.ruleVal !== NEW) {
        Object.assign(decision, { kind: "rule", rule_id: st.ruleVal,
          seller: { value: st.seller.value }, product: { value: st.product.value } });
      } else {
        Object.assign(decision, { kind: "new_rule",
          rule_name: $(".f-rule-name", form).value.trim(),
          seller: st.seller.field(), product: st.product.field() });
        if (rootVal === TEMP) {
          const name = $(".f-root-name", form).value.trim();
          if (!name) { toast("Name the new root first", true); return; }
          decision.new_root = { name, folder: st.newRoot.folder, color: st.newRoot.color };
        } else {
          decision.root_id = rootVal;
        }
      }
    } else if (kind === "once") {
      decision.destination = st.dest.value;
      decision.seller = { value: st.seller.value };
      decision.product = { value: st.product.value };
      if (!decision.destination || decision.destination === DEST_BROWSE) {
        toast("Pick a destination folder first", true); return;
      }
    }
    if (decision.match && !hasAnchor(decision.match)) {
      toast("The match rule needs a sender or subject condition", true); return;
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
      if (kind !== "skip" && kind !== "once") rulesChanged();
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
  const card = n.querySelector(".card");
  card.dataset.mid = it.message_id;
  // the email's header stays pinned while you scroll its form; the chosen
  // option's row pins right under it, so it needs the header's height
  const head = $(".fb-head", card);
  new ResizeObserver(() => card.style.setProperty("--fbh", head.offsetHeight + "px")).observe(head);
  return n;
}

function fallbackCompact(it) {
  const n = $("#tpl-fb-compact").content.cloneNode(true);
  fbHeader(n, it);
  n.querySelector(".card").dataset.mid = it.message_id;
  const slot = n.querySelector(".fb-form-slot");
  const caret = n.querySelector(".fb-expand");
  let built = false;
  const card = n.querySelector(".card");
  const head = $(".fb-head", card);
  const toggle = async () => {
    const opening = slot.hidden;
    slot.hidden = !opening;
    caret.classList.toggle("open", opening);
    // an opened row is outlined and pins to the top while you scroll its form
    card.classList.toggle("is-open", opening);
    if (opening) card.style.setProperty("--fbh", head.offsetHeight + "px");
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
  new ResizeObserver(() => card.style.setProperty("--fbh", head.offsetHeight + "px")).observe(head);
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

// ---- Rules view ------------------------------------------------------
// Grouped by root (colour, name, folder, rule count, edit / ＋ New rule /
// delete), then an "Excluded" group. Each rule row: name, summary, root select,
// merge, save, delete, ▸ editor (seller/product + match alternatives).
let RV = { roots: [], rules: [], counts: {} };
const RV_EXCLUDED = "__excluded__";
const RV_NOROOT = "__noroot__";

async function rulesLoad() {
  try { RV = await api().list_rules(); }
  catch (_) { RV = { roots: [], rules: [], counts: {} }; }
  await loadDests();
  rulesRender();
}

function rulesAfterWrite(res, okMsg) {
  toast(res.ok ? okMsg : (res.error || "Failed"), !res.ok);
  if (!res.ok) return false;
  RV = { ...res.data, counts: countsOf(res.data) };
  rulesChanged();
  loadDests().then(rulesRender);
  return true;
}

function rulesRender() {
  const list = $("#rules-list");
  const open = new Set($$(".cat-editor:not([hidden])", list).map(e => e.closest(".cat").dataset.id));
  list.innerHTML = "";
  $("#rules-empty").hidden = RV.rules.length + RV.roots.length > 0;
  const rootIds = new Set(RV.roots.map(r => r.id));
  for (const root of RV.roots)
    list.appendChild(rootGroupEl(root, RV.rules.filter(r => !r.exclude && r.root === root.id), open));
  const orphans = RV.rules.filter(r => !r.exclude && !rootIds.has(r.root));
  if (orphans.length)
    list.appendChild(rootGroupEl({ id: RV_NOROOT, name: "No root (files into קבלות)", folder: "", color: "" },
                                 orphans, open));
  const excl = RV.rules.filter(r => r.exclude);
  if (excl.length)
    list.appendChild(rootGroupEl({ id: RV_EXCLUDED, name: "Excluded", folder: "", color: "" }, excl, open));
  rulesFilter();
}

function rulesFilter() {
  const q = $("#rules-search").value.trim().toLowerCase();
  for (const g of $$(".root-group", $("#rules-list"))) {
    const groupHit = !q || (g.dataset.hay || "").includes(q);
    let shown = 0;
    for (const row of $$(".cat", g)) {
      const vis = groupHit || (row.dataset.hay || "").includes(q);
      row.hidden = !vis;
      if (vis) shown++;
    }
    g.hidden = !!q && !groupHit && shown === 0;
  }
}
$("#rules-search").addEventListener("input", rulesFilter);

// colour swatches + name + folder; used for ＋ New root and for editing a root
async function rootEditor(host, { root, onSave, onCancel }) {
  const ed = $("#tpl-root-editor").content.cloneNode(true).querySelector(".root-editor");
  host.innerHTML = "";
  host.appendChild(ed);
  const nameEl = $(".re-name", ed);
  nameEl.value = root ? root.name : "";
  const folder = await makeDestPicker($(".re-folder", ed), root ? root.folder : "");
  let color = root ? root.color : null;
  const sw = $(".re-colors", ed);
  for (const c of PASTELS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "re-swatch" + (c === color ? " on" : "");
    b.style.background = c;
    b.title = c;
    b.addEventListener("click", () => {
      color = c;
      $$(".re-swatch", sw).forEach(x => x.classList.toggle("on", x === b));
    });
    sw.appendChild(b);
  }
  $(".re-save", ed).addEventListener("click", () => {
    const name = nameEl.value.trim(), f = folder.value;
    if (!name) { toast("Name the root first", true); return; }
    if (!f || f === DEST_BROWSE) { toast("Pick the root's folder first", true); return; }
    onSave({ name, folder: f, color });
  });
  $(".re-cancel", ed).addEventListener("click", onCancel);
  nameEl.focus();
}

function rootGroupEl(root, rules, open) {
  const n = $("#tpl-root-group").content.cloneNode(true);
  const g = n.querySelector(".root-group");
  const special = root.id === RV_EXCLUDED || root.id === RV_NOROOT;
  g.dataset.id = root.id;
  g.dataset.hay = [root.name, root.folder, destLabel(root.folder)].join(" ").toLowerCase();
  if (root.color) g.style.setProperty("--root", root.color);
  g.classList.toggle("is-special", special);
  $(".root-name", g).textContent = root.name;
  $(".root-folder", g).textContent = special ? "" : (destLabel(root.folder) || root.folder);
  $(".root-folder", g).title = root.folder || "";
  $(".root-count", g).textContent = plural(rules.length, "rule");
  const host = $(".root-editor-host", g);

  if (special) {
    $$(".root-edit-btn, .root-add-rule, .root-del", g).forEach(b => b.remove());
  } else {
    $(".root-edit-btn", g).addEventListener("click", () => {
      if (!host.hidden) { host.hidden = true; return; }
      host.hidden = false;
      rootEditor(host, {
        root,
        onSave: async patch => rulesAfterWrite(await api().root_update(root.id, patch), "Root saved"),
        onCancel: () => { host.hidden = true; },
      });
    });
    $(".root-add-rule", g).addEventListener("click", () => {
      const box = $(".root-new-rule", g);
      box.hidden = !box.hidden;
      if (!box.hidden) $(".rn-name", box).focus();
    });
    const addRule = async () => {
      const inp = $(".rn-name", g);
      const name = inp.value.trim();
      if (!name) { toast("Name the rule first", true); return; }
      if (rulesAfterWrite(await api().rule_add(name, { root: root.id }),
                          `Added ${name} — expand it to add its match conditions`)) inp.value = "";
    };
    $(".rn-add", g).addEventListener("click", addRule);
    $(".rn-name", g).addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addRule(); } });
    $(".root-del", g).addEventListener("click", async () => {
      if (!rules.length) {
        if (!confirm(`Delete root “${root.name}”? (Its folder on disk is not touched.)`)) return;
        rulesAfterWrite(await api().root_delete(root.id, null), "Root deleted");
        return;
      }
      // rules still file here — ask where they should go
      host.hidden = false;
      host.innerHTML = "";
      const row = document.createElement("div");
      row.className = "re-delete";
      const sel = document.createElement("select");
      RV.roots.filter(r => r.id !== root.id).forEach(r => sel.add(new Option(r.name, r.id)));
      const go = document.createElement("button");
      go.type = "button"; go.textContent = "Move rules + delete root";
      const cancel = document.createElement("button");
      cancel.type = "button"; cancel.textContent = "Cancel";
      row.append(`Move its ${plural(rules.length, "rule")} to `, sel, go, cancel);
      host.appendChild(row);
      if (!sel.options.length) { go.disabled = true; sel.disabled = true; }
      go.addEventListener("click", async () =>
        rulesAfterWrite(await api().root_delete(root.id, sel.value), "Root deleted"));
      cancel.addEventListener("click", () => { host.hidden = true; host.innerHTML = ""; });
    });
  }
  const box = $(".root-rules", g);
  for (const r of rules) box.appendChild(ruleRowEl(r, rules, open));
  return n;
}

// "seller: <x> · product: <y> · N alternatives" — values in <bdi> so Hebrew
// doesn't reorder the English labels around them.
function ruleSummary(el, rule) {
  el.textContent = "";
  const n = (rule.match || []).length;
  if (rule.exclude) { el.textContent = `excluded — matching mail is skipped · ${plural(n, "alternative")}`; return; }
  [["seller: ", specSummary(rule.seller)], ["product: ", specSummary(rule.product)]]
    .forEach(([lbl, val], i) => {
      if (i) el.append("  ·  ");
      el.append(lbl);
      const b = document.createElement("bdi");
      b.textContent = val;
      el.appendChild(b);
    });
  el.append(`  ·  ${plural(n, "alternative")}`);
  el.title = el.textContent;
}

function ruleRowEl(rule, siblings, open) {
  const n = $("#tpl-rule").content.cloneNode(true);
  const art = n.querySelector(".cat");
  art.dataset.id = rule.id;
  const matches = rule.match || [];
  art.dataset.hay = [rule.name, specSummary(rule.seller), specSummary(rule.product),
    ...matches.flatMap(m => Object.values(m).flatMap(asList))].join(" ").toLowerCase();
  if (rule.exclude) art.classList.add("is-exclude");
  const nameEl = $(".cat-name", art);
  nameEl.value = rule.name || "";
  ruleSummary($(".cat-summary", art), rule);

  const rootSel = $(".rule-root", art);
  if (rule.exclude) rootSel.remove();
  else {
    if (!RV.roots.some(r => r.id === rule.root)) rootSel.add(new Option("(no root)", ""));
    RV.roots.forEach(r => rootSel.add(new Option(r.name, r.id)));
    rootSel.value = RV.roots.some(r => r.id === rule.root) ? rule.root : "";
    rootSel.addEventListener("change", async () => {
      if (!rootSel.value) return;
      const to = RV.roots.find(r => r.id === rootSel.value);
      rulesAfterWrite(await api().rule_update(rule.id, { root: rootSel.value }), `Moved to ${to.name}`);
    });
  }

  const merge = $(".cat-merge", art);
  merge.innerHTML = `<option value="">merge into…</option>`;
  siblings.filter(o => o.id !== rule.id).forEach(o => merge.add(new Option(o.name, o.id)));
  merge.hidden = siblings.length < 2;
  merge.addEventListener("change", async () => {
    if (!merge.value) return;
    const into = siblings.find(o => o.id === merge.value);
    if (!confirm(`Move all ${plural(matches.length, "alternative")} of “${rule.name}” into “${into.name}” and delete “${rule.name}”?\n\nThose emails will then be named the way “${into.name}” says.`)) {
      merge.value = ""; return;
    }
    rulesAfterWrite(await api().rule_merge(rule.id, merge.value), `Merged into ${into.name}`);
  });

  // editor (built lazily on first expand)
  const editor = $(".cat-editor", art), caret = $(".cat-expand", art);
  let ed = null;
  function build() {
    if (rule.exclude) $(".cat-fields", editor).hidden = true;
    else ed = {
      seller: makeNameField($(".fd-seller .nf-slot", editor), { spec: rule.seller ?? null }),
      product: makeNameField($(".fd-product .nf-slot", editor), { spec: rule.product ?? null }),
    };
    const box = $(".cat-matches", editor);
    box.innerHTML = "";
    matches.forEach((m, i) => {
      const row = document.createElement("div");
      row.className = "cat-match";
      const span = document.createElement("span");
      span.textContent = matchText(m);
      const del = document.createElement("button");
      del.className = "cat-match-del"; del.textContent = "✕"; del.title = "Remove this alternative";
      del.addEventListener("click", async () =>
        rulesAfterWrite(await api().rule_remove_match(rule.id, i), "Alternative removed"));
      row.append(span, del);
      box.appendChild(row);
    });
    const alt = makeMatchRule($(".cat-add-match .mr-slot", editor), {});
    $(".cat-add-match-btn", editor).addEventListener("click", async () => {
      const m = alt.value();
      if (!hasAnchor(m)) { toast("An alternative needs a sender or subject condition", true); return; }
      rulesAfterWrite(await api().rule_add_match(rule.id, m), "Alternative added");
    });
  }
  const toggle = () => {
    editor.hidden = !editor.hidden;
    caret.textContent = editor.hidden ? "▸" : "▾";
    if (!editor.hidden && !editor._built) { editor._built = true; build(); }
  };
  caret.addEventListener("click", toggle);
  if (open && open.has(rule.id)) toggle();

  $(".cat-save", art).addEventListener("click", async () => {
    const patch = { name: nameEl.value.trim() };
    if (ed) { patch.seller = ed.seller.spec(); patch.product = ed.product.spec(); }
    rulesAfterWrite(await api().rule_update(rule.id, patch), "Saved");
  });
  $(".cat-del", art).addEventListener("click", async () => {
    if (!confirm(`Delete rule “${rule.name}”? Emails it matched will land in fallbacks again.`)) return;
    rulesAfterWrite(await api().rule_delete(rule.id), "Deleted");
  });
  return n;
}

$("#root-new-btn").addEventListener("click", () => {
  const host = $("#root-new-form");
  if (!host.hidden) { host.hidden = true; return; }
  host.hidden = false;
  rootEditor(host, {
    root: null,
    onSave: async ({ name, folder, color }) => {
      if (rulesAfterWrite(await api().root_add(name, folder, color), `Added root ${name}`))
        host.hidden = true;
    },
    onCancel: () => { host.hidden = true; },
  });
});
