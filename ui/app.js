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
}));

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
  $(".card-sub", n).textContent =
    rec.seller ? (rec.subject || "") : (rec.sender || "");
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
  $("#run-list").innerHTML = "";
  $("#run-accounts").innerHTML = "";
  runAccts.clear();
  $("#run-summary").textContent = "Scanning…";
  $("#run-empty").hidden = true;
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

$("#fb-viewtoggle").addEventListener("click", async () => {
  await api().set_ui_state({ fallbacks_simple: !fbSimple });
  loadFallbacks();
});

// Per-entry "Claude" button — opens a `claude` terminal seeded with just this
// one fallback (same handoff path as the multi-select button, list of one).
function fallbackClaudeButton(it) {
  const b = document.createElement("button");
  b.className = "ask-claude";
  b.type = "button";
  b.title = "Handle this fallback with Claude";
  b.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true" width="13" height="13">' +
    '<path fill="currentColor" d="M12 1.5l1.9 5.1 5.1 1.9-5.1 1.9L12 15.5l-1.9-5.1L5 8.5l5.1-1.9z' +
    'M18.5 14l1 2.6 2.6 1-2.6 1-1 2.6-1-2.6-2.6-1 2.6-1z' +
    'M5 15l.8 2 2 .8-2 .8L5 21.5l-.8-2-2-.8 2-.8z"/></svg>' +
    '<span>Claude</span>';
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

function wireForm(scope, it, s) {
  const sel = $(".f-category", scope);
  sel.innerHTML = `<option value="">no category</option>` +
    CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join("");
  $(".f-seller", scope).value = s.seller || "";
  $(".f-product", scope).value = s.product || "";
  if (s.category) sel.value = s.category;
  $(".f-sender", scope).value = s.match_sender_contains || "";
  if (s.kind) {
    const r = scope.querySelector(`.fb-form input[value="${s.kind}"]`);
    if (r) r.checked = true;
  }
  $(".fb-form", scope).addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const decision = {
      kind: $("input[name=kind]:checked", form).value,
      seller: $(".f-seller", form).value.trim(),
      product: $(".f-product", form).value.trim(),
      category: $(".f-category", form).value || null,
      base_dir: $(".f-basedir", form).value.trim() || null,
      match_sender_contains: $(".f-sender", form).value.trim(),
      match_subject_contains: $(".f-subject", form).value.trim() || null,
    };
    const res = await api().apply_fallback(it.message_id, decision);
    if (res && res.ok) {
      form.closest(".card").remove();
      toast(`Resolved: ${decision.seller || it.subject}`);
      loadFallbacks();
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
const RX_GLYPH = { folder: "📁", "receipt-folder": "🧾", pdf: "📄", file: "▪" };

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
  if (q) q.value = "";
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
  rxRenderList(q && q.value.trim());
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
  rxSearchTimer = setTimeout(() => rxRenderList(q), 120);
});

window.addEventListener("pywebviewready", () => {
  resetRunView();
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
