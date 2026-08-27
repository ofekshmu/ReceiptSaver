"use strict";

const api = () => window.pywebview.api;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let CATEGORIES = [];
let histOffset = 0, histLoading = false, histDone = false, histRows = [];

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
function resetRunView() {
  $("#run-list").innerHTML = "";
  $("#run-summary").textContent = "Scanning…";
  $("#run-empty").hidden = true;
}
window.onScanEvent = function (evt) {
  if (evt.type === "account") {
    $("#run-summary").textContent = `Scanning ${evt.label}… ${evt.candidates} candidates`;
  } else if (evt.type === "mail") {
    $("#run-list").appendChild(card(evt.record));
  } else if (evt.type === "error") {
    const d = document.createElement("div");
    d.className = "toast error";
    d.textContent = `${evt.label}: ${evt.message}`;
    $("#run-list").appendChild(d);
  } else if (evt.type === "done") {
    renderRunDone(evt);
  }
};

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
  const list = $("#fb-list");
  list.innerHTML = "";
  const items = await api().get_fallbacks();
  $("#fb-empty").hidden = items.length > 0;
  for (const it of items) list.appendChild(await fallbackCard(it));
  updateHandoffButton();
  refreshBadge(items.length);
}
async function fallbackCard(it) {
  const n = $("#tpl-fallback").content.cloneNode(true);
  $(".card-title", n).textContent = it.subject || "(no subject)";
  $(".card-sub", n).textContent = `${it.sender} · ${it.account}`;
  $(".open-folder", n).addEventListener("click", e => {
    e.preventDefault(); api().open_folder(it.folder_path);
  });
  $(".open-pdf", n).addEventListener("click", e => {
    e.preventDefault(); api().open_folder(it.folder_path + "\\email.pdf");
  });
  const sel = $(".f-category", n);
  sel.innerHTML = `<option value="">no category</option>` +
    CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join("");

  const s = await api().suggest_fallback(it.message_id);
  $(".f-seller", n).value = s.seller || "";
  $(".f-product", n).value = s.product || "";
  if (s.category) sel.value = s.category;
  $(".f-sender", n).value = s.match_sender_contains || "";
  if (s.kind) { const r = $(`.fb-form input[value="${s.kind}"]`, n); if (r) r.checked = true; }
  const conf = $(".conf", n);
  conf.textContent = s.confidence === "low"
    ? "low confidence — consider handling with Claude" : (s.confidence || "") + " confidence";
  conf.classList.add(s.confidence || "medium");

  $(".fb-check", n).addEventListener("change", updateHandoffButton);
  $(".fb-form", n).addEventListener("submit", async e => {
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
  n.querySelector(".card").dataset.mid = it.message_id;
  return n;
}
function selectedFallbackIds() {
  return $$(".card.fb").filter(c => $(".fb-check", c).checked).map(c => c.dataset.mid);
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
function toast(msg, isError) {
  const d = document.createElement("div");
  d.className = "toast" + (isError ? " error" : "");
  d.textContent = msg;
  $("#toast-host").appendChild(d);
  setTimeout(() => d.remove(), 4000);
}

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

async function rxBrowse(path) {
  const res = await api().browse(path);
  if (res.error && (!res.crumbs || !res.crumbs.length)) { toast(res.error, true); return; }
  rxCurrent = res.path || path;

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
    $(".rx-glyph", n).textContent = RX_GLYPH[e.kind] || RX_GLYPH.file;
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

window.addEventListener("pywebviewready", () => {
  resetRunView();
  refreshBadge();
});
