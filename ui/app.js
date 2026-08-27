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
  if (t.dataset.view === "history" && histRows.length === 0) loadHistory();
  if (t.dataset.view === "fallbacks") loadFallbacks();
}));

// ---- window dragging ---------------------------------------------------
// easy_drag drifts; instead track the cursor's grab offset within the window
// and set the absolute window position to (screenPos - grabOffset).
(function enableDrag() {
  const bar = $(".titlebar");
  let dragging = false, grabX = 0, grabY = 0, pending = null, raf = 0;
  const flush = () => {
    raf = 0;
    if (pending) { api().move_window(pending[0], pending[1]); pending = null; }
  };
  bar.addEventListener("pointerdown", e => {
    if (e.button !== 0 || e.target.closest("button, .tab")) return;
    dragging = true;
    grabX = e.clientX;
    grabY = e.clientY;
    try { bar.setPointerCapture(e.pointerId); } catch (_) {}
  });
  bar.addEventListener("pointermove", e => {
    if (!dragging) return;
    pending = [Math.round(e.screenX - grabX), Math.round(e.screenY - grabY)];
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
    const parts = [`${evt.saved} saved`, `${evt.fallback} fallback`];
    if (evt.excluded) parts.push(`${evt.excluded} excluded`);
    $("#run-summary").textContent = parts.join(" · ");
    if (evt.saved === 0 && evt.fallback === 0 && evt.excluded === 0) {
      $("#run-empty").hidden = false;
    }
    refreshBadge();
  }
};

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

window.addEventListener("pywebviewready", () => {
  resetRunView();
  refreshBadge();
});
