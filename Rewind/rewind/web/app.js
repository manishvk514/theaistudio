"use strict";
const REMOTE = !!window.REWIND_REMOTE;
const CLOUD = window.REWIND_CLOUD || null;   // set when Rewind runs on Hugging Face
let T = window.REWIND_TOKEN || (REMOTE ? localStorage.getItem("rewind_token") || "" : "");
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, body) {
  const opt = {headers: {"X-Rewind-Token": T}};
  if (body !== undefined) { opt.method = "POST"; opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opt);
  if (r.status === 403 && REMOTE && !path.startsWith("/api/pair")) { localStorage.removeItem("rewind_token"); T = ""; showPairing(); throw new Error("Not connected"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, ms = 4000) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }

const LABEL = {trash: "In a trash bin", recovered: "Recovered", thumbnail: "Preview only", missing: "Bytes gone",
  present: "In library", duplicate: "Duplicate"};
const VIEWNOTE = {
  deleted: "Everything that survives somewhere but is no longer in your library. One tile per picture; extra copies are grouped.",
  trash: "Full originals still sitting in a bin. These are the easiest wins.",
  recovered: "Full files rebuilt from backups, caches, memory cards, exports or email.",
  thumbnail: "Only a small preview survived. Better than nothing, but the original is gone.",
  missing: "We know these existed (a chat, a database or a bin lists them) but their bytes are gone. For chats, the other person usually still has them.",
  present: "Your current library: what we compare against.",
  duplicate: "Copies we found that you still have in your library. Hidden from 'deleted'."
};

let SOURCES = [], DISCOVER = {}, state = {view: "deleted", offset: 0, sel: new Set()};

// ------------------------------------------------------------------ nav
$$("#nav button").forEach(b => b.onclick = () => go(b.dataset.page));
function go(page) {
  $$("#nav button").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "page-" + page));
  if (page === "results") loadItems(true);
  if (page !== "results" && typeof LB !== "undefined" && LB.open) closeLightbox();
  if (page === "advanced" && !SOURCES.length) loadSources().catch(() => {});
}

// ------------------------------------------------------------------ sources
async function loadSources() {
  [SOURCES, DISCOVER] = await Promise.all([api("/api/sources"), api("/api/discover")]);
  const groups = {};
  SOURCES.filter(s => !s.hidden && s.id !== "auto").forEach(s => (groups[s.group] = groups[s.group] || []).push(s));
  const order = ["Phones", "Devices", "Apps", "Accounts"];
  $("#source-groups").innerHTML = order.filter(g => groups[g]).map(g => `
    <div class="group"><h3>${esc(g)}</h3><div class="cards">${groups[g].map(s => `
      <div class="card" data-id="${s.id}"><h4>${esc(s.title)}</h4><p>${esc(s.blurb)}</p>
      ${hintLine(s)}</div>`).join("")}</div></div>`).join("");
  $$(".card").forEach(c => c.onclick = () => openSource(c.dataset.id));
}
function hintLine(s) {
  if (s.id === "iphone-backup" && DISCOVER.ios_backups?.length) return `<div class="done">✓ ${DISCOVER.ios_backups.length} backup(s) found on this computer</div>`;
  if (s.id === "android") return DISCOVER.android === null ? `<div class="muted small" style="margin-top:8px">Needs Android platform tools (adb)</div>` :
    DISCOVER.android?.length ? `<div class="done">✓ Phone connected</div>` : "";
  if (s.id === "computer" && DISCOVER.computer?.length) return `<div class="done">✓ ${DISCOVER.computer.length} places found</div>`;
  return "";
}

function openSource(id) {
  const s = SOURCES.find(x => x.id === id);
  const f = s.fields.map(fieldHTML).join("");
  $("#sheet").innerHTML = `<h3>${esc(s.title)}</h3><p class="muted">${esc(s.blurb)}</p>
    ${s.link ? `<p><a href="${esc(s.link)}" target="_blank" rel="noopener" style="color:var(--accent)">Open ${esc(new URL(s.link).hostname)} ↗</a></p>` : ""}
    <form id="srcform">${f}<div class="row" style="margin-top:18px"><span class="grow"></span>
    <button type="button" class="ghost" id="cancel">Cancel</button><button class="primary">Start scan</button></div></form>`;
  $("#modal").classList.remove("hidden");
  $("#cancel").onclick = closeModal;
  $$("[data-browse]").forEach(b => b.onclick = async () => {
    const r = await api("/api/browse", {kind: b.dataset.browse}); if (r.path) $(`[name="${b.dataset.for}"]`).value = r.path; });
  $$("[data-fill]").forEach(b => b.onclick = () => { $(`[name="${b.dataset.for}"]`).value = b.dataset.fill; });
  $("#srcform").onsubmit = async e => {
    e.preventDefault();
    const params = {};
    s.fields.forEach(fd => {
      if (fd.type === "bool") params[fd.name] = $(`[name="${fd.name}"]`).checked;
      else if (fd.type === "choice") params[fd.name] = ($(`[name="${fd.name}"]:checked`) || {}).value;
      else if (fd.type === "multi") params[fd.name] = $$(`[name="${fd.name}"]:checked`).map(x => x.value);
      else params[fd.name] = $(`[name="${fd.name}"]`).value.trim();
    });
    const needsPath = s.fields.find(fd => ["folder", "file", "folder_or_file"].includes(fd.type));
    if (needsPath && !params[needsPath.name]) return toast("Choose a " + (needsPath.type === "file" ? "file" : "folder") + " first.");
    try { await api("/api/scan", {source: id, params}); closeModal(); go("advanced"); toast("Scan started"); }
    catch (err) { toast(err.message); }
  };
}
function fieldHTML(fd) {
  const n = esc(fd.name), lab = `<label class="l">${esc(fd.label)}</label>`;
  if (["folder", "file", "folder_or_file"].includes(fd.type)) {
    let hints = "";
    if (fd.discover === "ios_backups") hints = (DISCOVER.ios_backups || []).map(b =>
      `<button type="button" data-for="${n}" data-fill="${esc(b.path)}">${esc(b.device || "iPhone")} · ${esc((b.date || "").slice(0, 10))}${b.encrypted ? " · encrypted" : ""}</button>`).join("");
    if (fd.discover === "time_machine") hints = (DISCOVER.time_machine || []).map(p => `<button type="button" data-for="${n}" data-fill="${esc(p)}">${esc(p)}</button>`).join("");
    if (fd.discover === "telegram") hints = (DISCOVER.telegram || []).map(p => `<button type="button" data-for="${n}" data-fill="${esc(p)}">${esc(p)}</button>`).join("");
    const kinds = REMOTE ? "" : fd.type === "folder_or_file" ? `<button type="button" data-browse="folder" data-for="${n}">Folder…</button><button type="button" data-browse="file" data-for="${n}">File…</button>`
      : `<button type="button" data-browse="${fd.type}" data-for="${n}">Browse…</button>`;
    return `<div class="field">${lab}<div class="pick"><input type="text" name="${n}" placeholder="${REMOTE ? "Path on the laptop" : "Paste a path or press Browse"}">${kinds}</div>${hints ? `<div class="hints">${hints}</div>` : ""}</div>`;
  }
  if (fd.type === "choice") return `<div class="field">${lab}${fd.choices.map(([v, t]) =>
    `<label class="opt"><input type="radio" name="${n}" value="${esc(v)}" ${v === fd.default ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>`;
  if (fd.type === "bool") return `<div class="field"><label class="opt"><input type="checkbox" name="${n}" ${fd.default ? "checked" : ""}> ${esc(fd.label)}</label></div>`;
  if (fd.type === "multi") {
    const list = DISCOVER[fd.discover] || [];
    if (!list.length) return `<div class="field muted">Nothing to search was found on this computer.</div>`;
    return `<div class="field">${lab}${list.map(c => `<label class="opt"><input type="checkbox" name="${n}" value="${esc(c.id)}" checked> ${esc(c.label)} <span class="muted small">${esc(c.path)}</span></label>`).join("")}</div>`;
  }
  return `<div class="field">${lab}<input type="${fd.type === "password" ? "password" : "text"}" name="${n}" autocomplete="off"></div>`;
}
function closeModal() { $("#modal").classList.add("hidden"); $("#sheet").innerHTML = ""; }
$("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };

// ------------------------------------------------------------------ summary
async function refreshSummary() {
  const s = await api("/api/summary");
  const del = (s.trash || 0) + (s.recovered || 0) + (s.thumbnail || 0);
  $("#chips").innerHTML = `<span class="chip"><b>${del}</b> deleted found</span><span class="chip"><b>${s.trash || 0}</b> in bins</span>
    <span class="chip"><b>${s.thumbnail || 0}</b> previews</span><span class="chip"><b>${s.missing || 0}</b> known, gone</span><span class="chip"><b>${s.present || 0}</b> in library</span>`;
  const note = $("#libnote");
  note.classList.remove("hidden");
  if (!s.have_library) { note.className = "note"; note.innerHTML = `<b>Start with your current photos.</b> Add the place your photos live now (your phone's latest backup, the phone itself, your Photos library, or a Takeout export) and choose <i>"my current photos"</i>. Rewind compares everything else against it to tell what's really deleted. Until then, everything found counts as "not in your library".`; }
  else { note.className = "note ok"; note.innerHTML = `✓ Your current library is loaded (${s.present} items). Now add places where deleted copies may survive: old backups, cards, chats, exports, accounts.`; }
  const sel = $("#f-source"), cur = sel.value;
  sel.innerHTML = `<option value="">All sources</option>` + (s.sources || []).map(x => `<option ${x === cur ? "selected" : ""}>${esc(x)}</option>`).join("");
}

// ------------------------------------------------------------------ gallery (results)
const STATUS = {trash: ["In a bin", "s-bin"], recovered: ["Rebuilt", "s-rebuilt"], thumbnail: ["Preview only", "s-preview"],
  missing: ["Can't recover", "s-gone"], present: ["Still here", "s-here"], duplicate: ["Copy", "s-here"]};
function statusOf(it) { return STATUS[it.category] || STATUS[it.verdict] || [it.verdict || "", "s-here"]; }
state.items = []; state.total = 0; state.kind = ""; state.loading = false; state.lastMonth = null;

$$("#views button").forEach(b => b.onclick = () => { $$("#views button").forEach(x => x.classList.toggle("on", x === b)); state.view = b.dataset.view; loadItems(true); });
$$("#kinds button").forEach(b => b.onclick = () => { $$("#kinds button").forEach(x => x.classList.toggle("on", x === b)); state.kind = b.dataset.kind; $("#f-kind").value = state.kind; loadItems(true); });
["#f-source", "#f-sort"].forEach(s => $(s).onchange = () => loadItems(true));
$("#btn-more").onclick = () => loadItems(false);
if ("IntersectionObserver" in window) new IntersectionObserver(es => { if (es.some(e => e.isIntersecting) && $("#page-results").classList.contains("on") && state.offset < state.total) loadItems(false); },
  {rootMargin: "800px"}).observe($("#grid-end"));

async function refreshCounts() {
  try {
    const s = await api("/api/summary");
    const c = {all: (s.n_present || 0) + (s.n_deleted || 0), deleted: s.n_deleted || 0, present: s.n_present || 0,
      trash: s.trash || 0, recovered: s.recovered || 0, thumbnail: s.thumbnail || 0, missing: s.n_missing || 0};
    $$("[data-count]").forEach(i => { i.textContent = c[i.dataset.count] ? c[i.dataset.count].toLocaleString() : ""; });
  } catch (e) { /* ignore */ }
}

function monthOf(it) {
  const d = (it.taken || it.deleted_at || "").slice(0, 7);
  if (!/^\d{4}-\d\d$/.test(d)) return "Date unknown";
  return new Date(d + "-01T12:00:00").toLocaleDateString(undefined, {month: "long", year: "numeric"});
}

async function loadItems(reset) {
  if (state.loading && !reset) return;
  state.loading = true;
  const view = state.view, kind = state.kind;
  if (reset) { state.offset = 0; state.items = []; state.lastMonth = null; $("#grid").innerHTML = `<div class="gal-skel">${"<i></i>".repeat(18)}</div>`; refreshCounts(); }
  $("#viewnote").textContent = VIEWNOTE[view] || (view === "all" ? "Everything Rewind found: what you still have and every deleted photo or video that came back." : "");
  try {
    const q = new URLSearchParams({view, kind, source: $("#f-source").value, sort: $("#f-sort").value, offset: state.offset, limit: 120});
    const r = await api("/api/items?" + q);
    if (view !== state.view || kind !== state.kind) return;           // the filter changed while loading
    if (reset) $("#grid").innerHTML = "";
    const byDate = $("#f-sort").value !== "biggest";
    let html = "";
    r.items.forEach((it, k) => {
      const m = monthOf(it);
      if (byDate && m !== state.lastMonth) { html += `<h4 class="gal-month">${esc(m)}</h4>`; state.lastMonth = m; }
      html += tileHTML(it, state.items.length + k);
    });
    state.items.push(...r.items);
    state.offset += r.items.length; state.total = r.total;
    $("#grid").insertAdjacentHTML("beforeend", html);
    $("#gal-total").textContent = r.total ? `${r.total.toLocaleString()} item${r.total === 1 ? "" : "s"}` : "";
    $("#btn-more").classList.toggle("hidden", state.offset >= r.total);
    $("#btn-slideshow").disabled = !state.items.some(it => it.has_file);
    if (!r.total && reset) $("#grid").innerHTML = `<div class="gal-empty"><div class="big">⟲</div><h3>Nothing here yet</h3><p class="muted">${
      view === "present" ? "Your current library appears here once Rewind has read it." : "Run a scan from Home, connect an account, or upload an export, and what comes back shows up here."}</p></div>`;
    if (reset && view === "missing" && r.total) {
      const a = await api("/api/ask");
      if (a.text) $("#grid").insertAdjacentHTML("afterbegin", `<div class="note" id="asknote" style="grid-column:1/-1"><b>These were in chats.</b> The other person usually still has them. <button id="askcopy" class="ghost">Copy messages asking for them</button></div>`);
      if ($("#askcopy")) $("#askcopy").onclick = () => { navigator.clipboard.writeText(a.text); toast("Copied. Paste it into each chat."); };
    }
    $$(".tile:not([data-wired])").forEach(wireTile);
    updateSel();
    if (LB.open) lbRender();
  } catch (e) { toast(e.message); } finally { state.loading = false; }
}

function tileHTML(it, i) {
  const [label, cls] = statusOf(it);
  const media = it.has_file ? `<img loading="lazy" decoding="async" src="/api/thumb/${it.id}?t=${T}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ph',textContent:'${it.kind === "video" ? "▶" : "🖼"}'}))">`
    : `<div class="ph"><span>${it.kind === "video" ? "▶" : "🖼"}</span><small>${esc(it.name || "")}</small><small>${it.verdict === "missing" ? "no copy left" : "in your account"}</small></div>`;
  return `<div class="tile ${state.sel.has(it.id) ? "sel" : ""}" data-id="${it.id}" data-i="${i}" tabindex="0">
    ${media}<span class="pill ${cls}">${esc(label)}</span>
    ${it.kind === "video" ? `<span class="vbadge">▶</span>` : ""}${it.restored_to ? `<span class="done-badge" title="Restored">✓</span>` : ""}
    ${it.has_file ? `<button class="chk" aria-label="Select"></button>` : ""}
    <div class="cap"><b>${esc(it.name || "Unnamed")}</b><span>${esc((it.taken || it.deleted_at || "").slice(0, 10) || "date unknown")} · ${esc(it.source || "")}${it.copies > 1 ? ` · ${it.copies} copies` : ""}</span></div></div>`;
}
function wireTile(el) {
  el.dataset.wired = 1;
  const id = +el.dataset.id;
  el.onclick = e => {
    if (e.target.classList.contains("chk")) { e.stopPropagation(); toggleSel(id, el); return; }
    if (state.sel.size && !e.target.closest(".cap")) { toggleSel(id, el); return; }
    openLightbox(+el.dataset.i);
  };
  el.onkeydown = e => { if (e.key === "Enter") openLightbox(+el.dataset.i); };
}
function toggleSel(id, el) {
  state.sel.has(id) ? state.sel.delete(id) : state.sel.add(id);
  (el || $(`.tile[data-id="${id}"]`))?.classList.toggle("sel", state.sel.has(id));
  updateSel();
}
function updateSel() {
  const n = state.sel.size;
  $("#selcount").textContent = n ? `${n} selected` : "";
  $("#selbar").classList.toggle("hidden", !n);
  $("#btn-save-sel").disabled = !n;
}
$("#btn-sel-clear").onclick = () => { state.sel.clear(); $$(".tile.sel").forEach(t => t.classList.remove("sel")); updateSel(); };
$("#btn-sel-dl").onclick = () => saveZip([...state.sel]);

// ------------------------------------------------------------------ full-screen viewer
const LB = {open: false, i: 0, playing: false, timer: null, info: false};
async function openLightbox(i) {
  LB.open = true; LB.i = i;
  $("#lightbox").classList.remove("hidden"); document.body.classList.add("noscroll");
  lbRender();
}
function closeLightbox() {
  lbStop(); LB.open = false;
  const v = $("#lb-stage video"); if (v) v.pause();
  $("#lightbox").classList.add("hidden"); document.body.classList.remove("noscroll");
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const t = $(`.tile[data-i="${LB.i}"]`); if (t && t.scrollIntoView) t.scrollIntoView({block: "nearest"});
}
function lbGo(d) {
  const n = state.items.length; if (!n) return;
  LB.i = (LB.i + d + n) % n;
  if (LB.i >= n - 10 && state.offset < state.total) loadItems(false);
  lbRender();
}
function lbRender() {
  const it = state.items[LB.i]; if (!it) return;
  const [label, cls] = statusOf(it);
  $("#lb-count").textContent = `${LB.i + 1} / ${state.total.toLocaleString()}`;
  $("#lb-name").textContent = it.name || "Unnamed";
  const dl = $("#lb-dl"); dl.classList.toggle("hidden", !it.has_file); dl.href = `/api/file/${it.id}?t=${T}&dl=1`;
  const st = $("#lb-stage");
  if (st.dataset.id != it.id) {
    st.dataset.id = it.id;
    if (!it.has_file) st.innerHTML = `<div class="lb-none"><div class="big">${it.kind === "video" ? "▶" : "🖼"}</div><h3>${esc(it.name || "")}</h3><p>${esc(it.evidence || "No copy of this one survives.")}</p></div>`;
    else if (it.kind === "video") {
      st.innerHTML = `<video src="/api/view/${it.id}?t=${T}" controls autoplay playsinline poster="/api/thumb/${it.id}?t=${T}"></video>`;
      const v = $("video", st);
      v.onended = () => { if (LB.playing) lbGo(1); };
      v.onerror = () => { st.insertAdjacentHTML("beforeend", `<p class="lb-warn">This browser can't play this video format. Download it to watch.</p>`); };
    } else {
      st.innerHTML = `<img class="lb-img" alt="" src="/api/thumb/${it.id}?t=${T}"><span class="lb-spin"></span>`;
      const full = new Image();
      full.onload = () => { if (st.dataset.id == it.id) { const img = $(".lb-img", st); img.src = full.src; $(".lb-spin", st)?.remove(); } };
      full.onerror = () => $(".lb-spin", st)?.remove();
      full.src = `/api/view/${it.id}?t=${T}`;
      $(".lb-img", st).ondblclick = zoomAt;
    }
    [1, -1].forEach(d => { const nb = state.items[(LB.i + d + state.items.length) % state.items.length];
      if (nb && nb.has_file && nb.kind === "image") new Image().src = `/api/view/${nb.id}?t=${T}`; });
  }
  const remote = it.remote ? JSON.parse(it.remote) : null;
  const svc = remote && {gdrive: "Google Drive", onedrive: "OneDrive", dropbox: "Dropbox"}[remote.service];
  $("#lb-actions").innerHTML = `<span class="pill ${cls}">${esc(label)}</span><span class="lb-why">${esc(it.evidence || "")}</span><span class="grow"></span>
    ${it.has_file ? `<button class="lb-pill-btn ${state.sel.has(it.id) ? "on" : ""}" id="lb-sel">${state.sel.has(it.id) ? "✓ Selected" : "Select"}</button>` : ""}
    ${svc ? `<button class="lb-pill-btn" id="lb-remote">Put back in ${svc}</button>` : ""}
    ${it.has_file && it.verdict === "deleted" ? `<button class="primary" id="lb-restore">${it.restored_to ? "Restore again" : "Restore"}</button>` : ""}`;
  if ($("#lb-sel")) $("#lb-sel").onclick = () => { toggleSel(it.id); lbRender(); };
  if ($("#lb-restore")) $("#lb-restore").onclick = () => { lbStop(); restoreAll([it.id]); };
  if ($("#lb-remote")) $("#lb-remote").onclick = async () => {
    if (!confirm(`Put this back in your ${svc}? It leaves the bin and returns to your files.`)) return;
    try { const m = await api("/api/remote_restore", {id: it.id}); toast(m.message); it.restored_to = m.message; } catch (e) { toast(e.message); }
  };
  if (LB.info) lbInfo(it);
  $("#lb-panel").classList.toggle("hidden", !LB.info);
  $("#lb-play").textContent = LB.playing ? "❚❚" : "▶";
}
async function lbInfo(it) {
  const p = $("#lb-panel");
  p.innerHTML = `<h3>${esc(it.name || "Unnamed")}</h3><div class="kv"><div><b>Status</b>${esc(statusOf(it)[0])}${it.damaged ? " · part of it was overwritten" : ""}</div>
    <div><b>Why</b>${esc(it.evidence || "")}</div><div><b>Found at</b>${esc(it.location || "")}</div>
    <div><b>Taken</b>${esc(it.taken || "unknown")}</div>${it.deleted_at ? `<div><b>Deleted</b>${esc(it.deleted_at)}</div>` : ""}
    <div><b>Size</b>${it.width ? it.width + " × " + it.height + " · " : ""}${it.size ? (it.size / 1048576).toFixed(2) + " MB" : "?"}</div>
    ${it.restored_to ? `<div><b>Restored</b>${esc(it.restored_to)}</div>` : ""}</div><div id="lb-copies"></div>`;
  if (it.group_id && it.copies > 1) {
    const g = await api("/api/items?" + new URLSearchParams({view: "group", group: it.group_id})).catch(() => null);
    if (g && $("#lb-copies")) $("#lb-copies").innerHTML = `<p class="muted small">${g.items.length} copies were found:</p><ul class="small">${g.items.map(c => `<li>${esc(statusOf(c)[0])} · ${esc(c.location)}</li>`).join("")}</ul>`;
  }
}
function zoomAt(e) {
  const img = e.currentTarget, st = $("#lb-stage");
  if (img.classList.toggle("zoomed")) {
    const r = img.getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    requestAnimationFrame(() => { st.scrollLeft = fx * img.scrollWidth - st.clientWidth / 2; st.scrollTop = fy * img.scrollHeight - st.clientHeight / 2; });
  }
  st.classList.toggle("zooming", img.classList.contains("zoomed"));
}
function lbPlay() {
  LB.playing = true; $("#lb-play").textContent = "❚❚";
  const tick = () => {
    clearTimeout(LB.timer);
    const it = state.items[LB.i];
    const prog = $("#lb-prog"); prog.style.transition = "none"; prog.style.width = "0";
    if (it && it.kind === "video" && it.has_file) { LB.timer = setTimeout(() => { if (LB.playing) { lbGo(1); tick(); } }, 120000); return; }
    requestAnimationFrame(() => { prog.style.transition = "width 4s linear"; prog.style.width = "100%"; });
    LB.timer = setTimeout(() => { if (LB.playing) { lbGo(1); tick(); } }, 4000);
  };
  LB.tick = tick; tick();
}
function lbStop() { LB.playing = false; clearTimeout(LB.timer); const p = $("#lb-prog"); if (p) { p.style.transition = "none"; p.style.width = "0"; } if ($("#lb-play")) $("#lb-play").textContent = "▶"; }
$("#lb-prev").onclick = () => { lbGo(-1); if (LB.playing) LB.tick(); };
$("#lb-next").onclick = () => { lbGo(1); if (LB.playing) LB.tick(); };
$("#lb-close").onclick = closeLightbox;
$("#lb-play").onclick = () => LB.playing ? lbStop() : lbPlay();
$("#lb-info").onclick = () => { LB.info = !LB.info; lbRender(); };
$("#lb-fs").onclick = () => document.fullscreenElement ? document.exitFullscreen() : $("#lightbox").requestFullscreen?.().catch(() => {});
$("#lb-stage").onclick = e => { if (e.target.id === "lb-stage") closeLightbox(); };
document.addEventListener("keydown", e => {
  if (!LB.open || e.target.matches("input,textarea")) return;
  if (e.key === "ArrowRight") { lbGo(1); if (LB.playing) LB.tick(); }
  else if (e.key === "ArrowLeft") { lbGo(-1); if (LB.playing) LB.tick(); }
  else if (e.key === "Escape") closeLightbox();
  else if (e.key === " ") { e.preventDefault(); LB.playing ? lbStop() : lbPlay(); }
  else if (e.key === "i") $("#lb-info").click();
  else if (e.key === "f") $("#lb-fs").click();
});
(() => {  // swipe on phones and trackpads
  let x0 = null, y0 = null;
  const lb = $("#lightbox");
  lb.addEventListener("touchstart", e => { if (e.touches.length === 1) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; } }, {passive: true});
  lb.addEventListener("touchend", e => {
    if (x0 === null || $(".lb-img.zoomed")) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { lbGo(dx < 0 ? 1 : -1); if (LB.playing) LB.tick(); }
    else if (dy > 90 && Math.abs(dy) > Math.abs(dx)) closeLightbox();
  }, {passive: true});
})();
$("#btn-slideshow").onclick = () => {
  const first = state.items.findIndex(it => it.has_file);
  if (first < 0) return toast("Nothing to show yet.");
  openLightbox(first); lbPlay();
};
// kept for anything that still calls it (older pages, tests)
async function openItem(id) {
  const i = state.items.findIndex(it => it.id === id);
  if (i >= 0) return openLightbox(i);
  const one = await api("/api/item/" + id).catch(() => null);
  if (one) { state.items.push(one); openLightbox(state.items.length - 1); }
}

$("#btn-save-all").onclick = () => restoreAll(null);
$("#btn-save-sel").onclick = () => restoreAll([...state.sel]);
async function saveItems(ids) {
  if (REMOTE || CLOUD || DISCOVER.android_local) return saveZip(ids);
  $("#sheet").innerHTML = `<h3>Save ${ids ? ids.length + " item(s)" : "everything deleted"}</h3>
  <p class="muted">Copies go into a new dated folder, sorted into "was in a bin", "recovered" and "preview only", with a report of where each came from. Nothing is changed where they were found.</p>
  <div class="field"><label class="l">Save into</label><div class="pick"><input type="text" id="dest" placeholder="Desktop (default)"><button type="button" id="bd">Browse…</button></div></div>
  <div class="row"><span class="grow"></span><button class="ghost" id="close">Cancel</button><button class="primary" id="go">Save</button></div>`;
  $("#modal").classList.remove("hidden");
  $("#close").onclick = closeModal;
  $("#bd").onclick = async () => { const r = await api("/api/browse", {kind: "folder"}); if (r.path) $("#dest").value = r.path; };
  $("#go").onclick = async () => {
    $("#go").disabled = true; $("#go").textContent = "Saving…";
    try {
      const r = await api("/api/export", {ids, dest: $("#dest").value.trim() || null});
      $("#sheet").innerHTML = `<h3>Saved ${r.copied} file(s)</h3><p>${esc(r.folder)}</p>${r.missing_listed ? `<p class="muted">Also wrote a list of ${r.missing_listed} picture(s) we know existed but couldn't recover, with where to ask for them.</p>` : ""}
      <div class="row"><span class="grow"></span><button id="op">Open folder</button><button class="primary" id="close">Done</button></div>`;
      $("#close").onclick = closeModal; $("#op").onclick = () => api("/api/open", {path: r.folder});
      state.sel.clear(); updateSel();
    } catch (e) { toast(e.message); $("#go").disabled = false; $("#go").textContent = "Save"; }
  };
}

// ------------------------------------------------------------------ jobs
async function pollJobs() {
  try {
    const jobs = await api("/api/jobs");
    const running = jobs.some(j => j.state === "running");
    $("#busy").classList.toggle("hidden", !running);
    const ask = jobs.find(j => j.needs_user);
    const si = $("#signin");
    if (ask) {
      const n = ask.needs_user;
      si.classList.remove("hidden");
      si.innerHTML = `<b>Your sign-in is needed.</b> ${esc(n.message)} ${n.code ? `<br><br>Code: <code>${esc(n.code)}</code>` : ""}<br><br>${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener"><button class="primary">Open sign-in page ↗</button></a>` : ""}`;
    } else si.classList.add("hidden");
    $("#jobs").innerHTML = jobs.length ? jobs.map(j => `<div class="job"><div class="row"><b>${esc(j.label)}</b><span class="grow"></span>
      <span class="muted small">${esc(j.state)}${j.found ? " · " + j.found + " found" : ""}</span>${j.state === "running" ? `<button class="danger" data-stop="${j.id}">Stop</button>` : ""}</div>
      ${j.state === "running" ? `<div class="bar ${j.total ? "" : "spin"}"><i style="${j.total ? `width:${Math.min(100, 100 * j.done / j.total)}%` : ""}"></i></div><div class="muted small">${esc(j.step || "")}</div>` : ""}
      ${j.needs_user ? `<div class="signin">${esc(j.needs_user.message)} ${j.needs_user.url ? `<a href="${esc(j.needs_user.url)}" target="_blank" rel="noopener" style="color:var(--accent)">Open ↗</a>` : ""}</div>` : ""}
      <div class="log">${esc(j.logs.join("\n"))}</div></div>`).join("") : `<p class="muted">No scans yet.</p>`;
    $$("[data-stop]").forEach(b => b.onclick = () => api("/api/stop", {job: +b.dataset.stop}));
    if (running || pollJobs._wasRunning) refreshSummary();
    pollJobs._wasRunning = running;
  } catch (e) { /* server restarting */ }
  setTimeout(pollJobs, 1500);
}

function boot() {
  if (CLOUD) {
    $("#nav-phone").textContent = "Upload";
    $("#sh-sub").textContent = "Rewind on Hugging Face searches your connected accounts (Google Drive, OneDrive, Dropbox, Telegram) and every export you upload, then sorts deleted from still here.";
    if (location.hash === "#upload") setTimeout(() => $("#nav-phone").click(), 50);
  }
  loadSources().then(() => { refreshSummary(); if (REMOTE || DISCOVER.android_local) { $("#nav-phone").textContent = "This phone"; } })
    .catch(e => toast("Could not reach Rewind: " + e.message));
  if (!boot._polling) { boot._polling = true; pollJobs(); homeLoop(true); }
}

// ------------------------------------------------------------------ home (v3)
let ST = null, homeAuto = false, tgForm = false;
const PLACE_ICON = {waiting: "", scanning: "", done: "✓", skipped: "–", blocked: "!", error: "!"};

async function startAuto() {
  try { await api("/api/scan", {source: "auto", params: {}}); } catch (e) { toast(e.message); }
  homeLoop(false);
}
$("#bigscan").onclick = () => { if (!$("#bigscan").classList.contains("busy")) startAuto(); else {
  const j = ST && ST.auto_job; if (j && j.state === "running") api("/api/stop", {job: j.id}).then(() => toast("Stopping after the current place…")); } };
$$("[data-goview]").forEach(b => b.onclick = () => {
  go("results"); $$("#views button").forEach(x => x.classList.toggle("on", x.dataset.view === b.dataset.goview));
  state.view = b.dataset.goview; loadItems(true); });
$("#btn-restore-all").onclick = () => restoreAll(null);

async function homeLoop(first) {
  clearTimeout(homeLoop._t);
  try {
    ST = await api("/api/status");
    renderHome();
    if (first && !homeAuto && !ST.auto_job && !REMOTE) { homeAuto = true; return startAuto(); }
  } catch (e) { /* restarting */ }
  const running = ST && ST.auto_job && ST.auto_job.state === "running";
  const waiting = ST && Object.values(ST.accounts || {}).some(a => a && a.state === "waiting");
  homeLoop._t = setTimeout(() => homeLoop(false), running || waiting ? 1200 : 4000);
}

function renderHome() {
  const j = ST.auto_job, s = ST.summary || {};
  const running = j && j.state === "running";
  const bs = $("#bigscan");
  bs.classList.toggle("busy", !!running);
  $("#bs-label").textContent = running ? "Stop" : (j ? "Scan again" : "Scan");
  if (running) {
    const done = (j.places || []).filter(p => !["waiting", "scanning"].includes(p.status)).length, tot = (j.places || []).length;
    $("#sh-title").textContent = "Scanning " + (j.step || "…");
    $("#sh-sub").textContent = `${done} of ${tot || "?"} places checked · ${j.found || 0} item(s) found so far. You can keep using your Mac.`;
    $("#sh-bar").classList.remove("hidden"); $("#sh-bar").className = "bar" + (tot ? "" : " spin");
    $("#sh-bar i").style.width = tot ? Math.max(3, 100 * done / tot) + "%" : "";
  } else if (j) {
    const nd = s.n_deleted || 0;
    $("#sh-title").textContent = nd ? `Found ${nd} deleted photo${nd === 1 ? "" : "s"} and video${nd === 1 ? "" : "s"}` : "Scan finished: nothing deleted was found yet";
    $("#sh-sub").textContent = nd ? "Each one is checked against what you still have, so these are really gone from your library. Restore them all with one tap, or look through them first."
      : CLOUD ? "Connect an account below, or upload an export or memory-card image on the Upload tab: each upload is scanned as it arrives."
      : "Connect an account below, plug in an old phone backup drive or memory card, or put an account export in Downloads, then scan again.";
    $("#sh-bar").classList.add("hidden");
  }
  $("#c-del").textContent = s.n_deleted || 0; $("#c-here").textContent = s.n_present || 0; $("#c-gone").textContent = s.n_missing || 0;
  const rb = $("#btn-restore-all"); const nd = s.n_deleted || 0;
  rb.disabled = !nd || running;
  rb.textContent = nd ? `Restore all ${nd} deleted` : "Restore all deleted";
  $("#restore-where").textContent = REMOTE || CLOUD || ST.is_android ? "Saved as a zip you can download." :
    nd ? "Copies go to Pictures › Rewind Restored, sorted by where they were found. Nothing is changed where they came from." : "";
  // Full Disk Access
  const fda = $("#fda");
  if (ST.full_disk_access === false && !REMOTE && !CLOUD) {
    fda.classList.remove("hidden");
    fda.innerHTML = `<b>One permission makes the scan complete.</b> macOS hides Photos' Recently Deleted, Messages and app caches from every app until you allow it.
      <div class="row" style="margin-top:10px"><button class="primary" id="fda-go">Open Full Disk Access settings</button>
      <span class="muted small">Switch on <b>Rewind</b> (press + and pick Rewind in Applications if it isn't listed), then quit and reopen Rewind.</span></div>`;
    $("#fda-go").onclick = () => api("/api/settings", {what: "fda"});
  } else fda.classList.add("hidden");
  renderPlaces(j ? j.places || [] : []);
  renderAccounts();
}

function renderPlaces(places) {
  const el = $("#places");
  if (!places.length) {
    const msg = CLOUD && ST.auto_job && ST.auto_job.state !== "running"
      ? "Nothing to check yet. Connect an account on the right, or upload an export on the Upload tab."
      : ST.auto_job ? "Listing places…" : "Press Scan to start.";
    el.innerHTML = `<p class="muted small" style="padding:12px 14px">${msg}</p>`; return;
  }
  el.innerHTML = places.map(p => `<div class="place ${esc(p.status)}"><span class="st">${PLACE_ICON[p.status] || ""}</span>
    <div class="pl"><div>${esc(p.label)}</div>${p.note ? `<div class="muted">${esc(p.note)}</div>` : ""}</div>
    ${p.kind === "volume" && !REMOTE && ST.platform === "darwin" && p.status !== "scanning" ? `<button data-deep="${esc(p.path)}" title="Rebuild deleted files from the card's free space">Deep scan</button>` : ""}
    <span class="n">${p.status === "done" || p.found ? (p.found || 0) : ""}</span></div>`).join("");
  $$("[data-deep]").forEach(b => b.onclick = async () => {
    if (!confirm("Deep scan copies the whole card to this Mac (read-only) and rebuilds deleted photos and videos from it. macOS will ask for your password. It can take a while for big cards. Start?")) return;
    try { await api("/api/deep_scan", {volume: b.dataset.deep}); toast("Deep scan started. Follow it under Advanced."); } catch (e) { toast(e.message); }
  });
}

const INFO_CARDS = [
  {t: "Google Photos", p: "Google gives no app access to Photos' Trash. Items deleted in the last 60 days: open Google Photos › Trash › Restore. Older: request a Google Takeout of Google Photos; when the zip lands in Downloads, Rewind opens it by itself.", links: [["Google Photos Trash", "https://photos.google.com/trash"], ["Google Takeout", "https://takeout.google.com/"]]},
  {t: "Snapchat", p: "Snapchat has no app access. Request My Data with Memories and chat media included; when the zip lands in Downloads, Rewind opens it and downloads your Memories by itself. Snaps that were opened and deleted are gone from Snapchat's servers.", links: [["Snapchat accounts page (My Data)", "https://accounts.snapchat.com/"]]},
  {t: "iCloud Photos", p: "Recently Deleted keeps photos for 30 days at icloud.com. iPhone backups made on this Mac are scanned automatically.", links: [["iCloud Photos", "https://www.icloud.com/photos/"]]},
  {t: "Instagram / Facebook", p: "Download your information as a zip; Rewind opens it from Downloads by itself. Instagram's own Recently Deleted keeps posts for 30 days.", links: [["Accounts Center", "https://accountscenter.instagram.com/"]]},
];

function renderAccounts() {
  const el = $("#accounts"); if (!el) return;
  const a = ST.accounts || {}, tg = ST.telegram || {};
  const svc = (id, title, what) => {
    const x = a[id] || {state: "not_connected"};
    if (CLOUD && x.state === "waiting" && x.direct) {
      return `<div class="acct"><div class="row"><h4>${title}</h4><span class="grow"></span><button class="ghost" data-conn="${id}">Start again</button></div>
        <p>Sign in on the ${title} tab that opened and press <b>Allow</b>. Rewind connects by itself and starts scanning.</p></div>`;
    }
    if (CLOUD && x.state === "waiting") {
      return `<div class="acct"><div class="row"><h4>${title}</h4><span class="grow"></span><button class="ghost" data-conn="${id}">Start again</button></div>
        <p>1. <a href="${esc(x.url || "#")}" target="_blank" rel="noopener" style="color:var(--accent)">Open the ${title} sign-in ↗</a> and press <b>Allow</b>.<br>
        2. Your browser then shows <i>"This site can't be reached"</i>. <b>Nothing is wrong</b>: copy that page's whole address and paste it here.</p>
        <form class="pastef" data-svc="${id}"><input type="text" name="url" placeholder="http://127.0.0.1:53682/?state=…&code=…" autocomplete="off"><button class="primary">Finish</button></form>
        <p class="small muted"><a href="/setup" target="_blank" rel="noopener" style="color:var(--accent)">Make sign-in one click (no copying) ↗</a></p></div>`;
    }
    const btn = REMOTE ? "" : x.state === "connected" ? `<button class="ghost" data-disc="${id}">Disconnect</button>`
      : x.state === "waiting" ? (x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener"><button>Open sign-in</button></a>` : `<button disabled>Waiting…</button>`)
      : `<button class="primary" data-conn="${id}" ${a.available === false ? "disabled" : ""}>Connect</button>`;
    return `<div class="acct"><div class="row"><h4>${title}</h4><span class="grow"></span>${x.state === "connected" ? `<span class="ok">✓ Connected</span>` : ""}${btn}</div>
      <p>${x.state === "waiting" || x.state === "error" ? esc(x.message || "") : what}</p></div>`;
  };
  let html = claudeCard() + svc("gdrive", "Google Drive", "Downloads everything in Drive's Trash (kept 30 days) and can put it back in Drive. Also learns what's still in Drive.")
    + svc("onedrive", "OneDrive", "Learns everything still in OneDrive and catches every photo deleted after you connect, with one-tap Restore while it's in the recycle bin (30 days). Microsoft gives apps no list of the recycle bin itself, so for older deletions use <a href=\"https://onedrive.live.com/\" target=\"_blank\" rel=\"noopener\">onedrive.live.com</a> › Recycle bin.")
    + svc("dropbox", "Dropbox", "Brings back deleted files Dropbox still keeps (30 days on Basic and Plus, 180 on Professional and Business, 365 on Advanced) and can put them back in Dropbox.");
  const tgBusy = ["need_qr", "need_app", "need_password", "need_code"].includes(tg.state);
  const tgBtn = REMOTE ? "" : tg.state === "connected" ? `<button class="ghost" data-disc="telegram">Disconnect</button>`
    : tgBusy || tgForm ? `<button class="ghost" id="tg-cancel">Cancel</button>` : `<button class="primary" id="tg-qr">Connect</button>`;
  html += `<div class="acct"><div class="row"><h4>Telegram</h4><span class="grow"></span>${tg.state === "connected" ? `<span class="ok">✓ Connected</span>` : ""}${tgBtn}</div>
    <p>${tgBusy || tg.state === "error" ? esc(tg.message) : "Downloads every photo and video still in your chats at full quality. Messages deleted for everyone are erased on Telegram's servers and can't come back."}</p>
    ${tg.state === "need_qr" && tg.qr_svg ? `<div class="tgqr">${tg.qr_svg}</div>${/Android|iPhone|iPad/.test(navigator.userAgent) ? `<p class="small">On this phone? <a href="${esc(tg.qr_url)}" style="color:var(--accent)">Open in Telegram</a> instead.</p>` : ""}
      <p class="small muted"><a href="#" id="tg-phone" style="color:var(--accent)">Use my phone number instead</a></p>` : ""}
    ${tg.state === "need_app" || tg.state === "need_password" || tg.state === "need_code" || (tgForm && tg.state !== "connected") ? tgFormHTML(tg) : ""}</div>`;
  html += snapCard() + teraCard();
  html += INFO_CARDS.map(c => `<div class="acct info"><h4>${esc(c.t)}</h4><p>${esc(c.p)}</p><p>${c.links.map(([n, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(n)} ↗</a>`).join(" · ")}</p></div>`).join("");
  if (document.activeElement && el.contains(document.activeElement)) return; // don't wipe a field being typed in
  el.innerHTML = html;
  $$("[data-conn]", el).forEach(b => b.onclick = async () => {
    const w = CLOUD ? window.open("about:blank", "_blank") : null;   // opened now, so pop-up blockers allow it
    try {
      const r = await api("/api/connect", {service: b.dataset.conn});
      if (w && r.sign_in_url) { w.location = r.sign_in_url; toast("Sign in on the new tab, press Allow, then paste the address you land on."); }
      else { if (w) w.close(); toast("A sign-in page is opening in your browser."); }
      b.blur();                 // the accounts box only redraws when nothing in it has focus
      homeLoop(false);
    } catch (e) { if (w) w.close(); toast(e.message); } });
  $$(".pastef", el).forEach(f => f.onsubmit = async ev => {
    ev.preventDefault(); const btn = $("button", f); btn.disabled = true; btn.textContent = "Connecting…";
    try { const r = await api("/api/connect_finish", {service: f.dataset.svc, url: f.url.value}); toast(r.note || "Connected."); document.activeElement.blur(); homeLoop(false); }
    catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Finish"; } });
  if ($("#claude-add")) $("#claude-add").onclick = async () => {
    try { const r = await api("/api/claude_setup", {}); $("#claude-msg").innerHTML = r.lines.map(esc).join("<br>"); } catch (e) { toast(e.message); } };
  if ($("#mcp-copy")) $("#mcp-copy").onclick = () => navigator.clipboard.writeText(CLOUD.mcp).then(() => toast("Copied"));
  $$("[data-disc]", el).forEach(b => b.onclick = async () => { if (!confirm("Disconnect this account from Rewind? Files already found stay.")) return; await api("/api/disconnect", {service: b.dataset.disc}); homeLoop(false); });
  if ($("#tg-qr")) $("#tg-qr").onclick = async () => { try { ST.telegram = await api("/api/telegram", {action: "qr"}); $("#tg-qr")?.blur(); renderAccounts(); homeLoop(false); } catch (e) { toast(e.message); } };
  if ($("#tg-cancel")) $("#tg-cancel").onclick = () => { tgForm = false; ST.telegram = {...tg, state: "not_connected", message: ""}; renderAccounts(); };
  if ($("#tg-phone")) $("#tg-phone").onclick = ev => { ev.preventDefault(); tgForm = true; ST.telegram = {...tg, state: "phone"}; renderAccounts(); };
  if ($("#snap-up")) $("#snap-up").onchange = async e => { for (const f of e.target.files) await sendUpload(f, $("#snap-list")); e.target.value = ""; };
  if ($("#tg-form")) $("#tg-form").onsubmit = tgSubmit;
}
function claudeCard() {
  if (CLOUD) return `<div class="acct"><div class="row"><h4>Use with Claude</h4><span class="grow"></span><button id="mcp-copy">Copy link</button></div>
    <p>In <b>claude.ai › Settings › Connectors › Add custom connector</b>, paste <code>${esc(CLOUD.mcp)}</code> and press Connect. Sign in with Hugging Face when asked.
    It then works in Claude on the web, the phone apps and Claude Desktop: ask "find my deleted photos".</p></div>`;
  if (REMOTE || (typeof DISCOVER !== "undefined" && DISCOVER && DISCOVER.android_local)) return "";
  return `<div class="acct"><div class="row"><h4>Use with Claude</h4><span class="grow"></span><button class="primary" id="claude-add">Add to Claude</button></div>
    <p id="claude-msg">Adds Rewind to Claude Desktop and Claude Code on this computer, so you can ask Claude to scan, show and restore. Reopen Claude Desktop afterwards.</p></div>`;
}
function teraCard() {
  return `<div class="acct"><div class="row"><h4>TeraBox</h4><span class="grow"></span><a href="https://www.terabox.com/" target="_blank" rel="noopener"><button>Open TeraBox ↗</button></a></div>
    <p>TeraBox lets no outside app read its Recycle Bin. Open TeraBox › <b>Recycle Bin</b>, select your photos and videos and press <b>Restore</b> (TeraBox keeps them for a limited time). Then download the folder and ${CLOUD ? "upload it on the Upload tab" : "leave it in Downloads"}: Rewind sorts what was deleted from what you still have.</p></div>`;
}
function snapCard() {
  const how = CLOUD ? `<label class="upload small-upload"><input type="file" id="snap-up" accept=".zip" multiple hidden><span class="primary-btn">Upload the Snapchat zip</span></label><div id="snap-list" class="small"></div>`
    : `<p class="small">Leave the downloaded zip in <b>Downloads</b>: Rewind opens it on its next scan.</p>`;
  return `<div class="acct"><div class="row"><h4>Snapchat</h4><span class="grow"></span><a href="https://accounts.snapchat.com/" target="_blank" rel="noopener"><button>Request my data ↗</button></a></div>
    <p>Snapchat lets no app sign in to your account, so Rewind uses Snapchat's own data download: on <b>accounts.snapchat.com › My Data</b> tick <b>Memories</b> and <b>Chat Media</b>, choose <b>JSON</b>, Submit. When Snapchat e-mails you, download the zip within a few days (its Memories links expire).</p>
    ${how}</div>`;
}
function tgFormHTML(tg) {
  if (tg.state === "need_app") return `<form id="tg-form"><p class="small">Once only: <a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org › API development tools</a>, sign in, create any app, copy its two values here.</p>
    <input type="text" name="api_id" placeholder="App api_id (numbers)" inputmode="numeric"><input type="text" name="api_hash" placeholder="App api_hash"><button class="primary">Continue</button></form>`;
  if (tg.state === "need_code") return `<form id="tg-form"><input type="text" name="code" placeholder="Login code from your Telegram app" inputmode="numeric" autocomplete="one-time-code"><button class="primary">Sign in</button></form>`;
  if (tg.state === "need_password") return `<form id="tg-form"><input type="password" name="password" placeholder="Telegram two-step verification password"><button class="primary">Sign in</button></form>`;
  return `<form id="tg-form"><p>Telegram asks every app for its own free API ID. Get yours once at <a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org › API development tools</a>, then:</p>
    <input type="text" name="api_id" placeholder="App api_id (numbers)" inputmode="numeric"><input type="text" name="api_hash" placeholder="App api_hash">
    <input type="text" name="phone" placeholder="Your phone number with country code, e.g. +91…" inputmode="tel"><button class="primary">Send me a login code</button></form>`;
}
async function tgSubmit(e) {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const action = "code" in f ? "code" : "password" in f ? "password" : "phone" in f ? "start" : "qr";
  const btn = $("button", e.target); btn.disabled = true; btn.textContent = "Working…";
  try {
    const r = await api("/api/telegram", {action, ...f});
    ST.telegram = r; document.activeElement.blur();
    if (r.state === "connected") { tgForm = false; toast("Telegram connected. Scanning your chats…"); startAuto(); }
    else if (r.state === "need_qr") tgForm = false;
    renderAccounts();
  } catch (err) { toast(err.message); btn.disabled = false; btn.textContent = "Try again"; }
}

async function restoreAll(ids) {
  if (REMOTE || CLOUD || DISCOVER.android_local) return saveZip(ids);
  $("#sheet").innerHTML = `<h3>Restoring…</h3><div class="bar spin"><i></i></div>`;
  $("#modal").classList.remove("hidden");
  try {
    const r = await api("/api/restore_all", {ids});
    $("#sheet").innerHTML = `<h3>Restored ${r.copied} file${r.copied === 1 ? "" : "s"}</h3><p>Saved in <b>${esc(r.folder)}</b>, sorted into "was in a bin", "rebuilt" and "preview only", with a report of where each came from.</p>
      ${r.missing_listed ? `<p class="muted">${r.missing_listed} more existed but their bytes are gone; the folder has a list of where to ask for them (often the other person in a chat).</p>` : ""}
      <div class="row"><span class="grow"></span><button id="op">Show in Finder</button><button class="primary" id="close">Done</button></div>`;
    $("#close").onclick = closeModal; $("#op").onclick = () => api("/api/open", {path: r.folder});
    state.sel.clear(); updateSel(); homeLoop(false);
  } catch (e) { toast(e.message); closeModal(); }
}


// ------------------------------------------------------------------ phone
async function saveZip(ids) {
  $("#sheet").innerHTML = `<h3>Preparing ${ids ? ids.length + " item(s)" : "everything deleted"}…</h3><div class="bar spin"><i></i></div>`;
  $("#modal").classList.remove("hidden");
  try {
    const r = await api("/api/export", {ids, zip: true});
    $("#sheet").innerHTML = `<h3>${r.copied} file(s) ready</h3><p class="muted">Download the zip, then open it in Files. On iPhone, open each picture and choose Save Image to put it back in Photos.</p>
      <div class="row"><span class="grow"></span><button class="ghost" id="close">Close</button><a href="${r.url}?t=${T}" download><button class="primary">Download zip</button></a></div>`;
    $("#close").onclick = closeModal;
  } catch (e) { toast(e.message); closeModal(); }
}

function showPairing() {
  $("#pairing").classList.remove("hidden");
  const go = async code => {
    $("#pairmsg").textContent = "Connecting…";
    try {
      const r = await fetch("/api/pair", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code})});
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Could not connect");
      T = j.token; localStorage.setItem("rewind_token", T);
      history.replaceState(null, "", "/");
      $("#pairing").classList.add("hidden");
      boot();
    } catch (e) { $("#pairmsg").textContent = e.message; }
  };
  $("#pairgo").onclick = () => go($("#paircode").value.trim());
  const pc = new URLSearchParams(location.search).get("pair");
  if (pc) go(pc);
}

async function renderPhone() {
  const el = $("#phone-body");
  if (CLOUD) {
    el.innerHTML = `<h3>Upload exports and backups</h3>
      <p class="muted">This Rewind runs on Hugging Face, so it can't see your Mac or phone. Give it what they produce: Google Takeout, Snapchat My Data, Instagram/Facebook downloads, WhatsApp chat exports (.zip), an SMS backup (.xml), a zipped iPhone backup, a memory-card image (.img), or loose photos and videos. Each file is scanned as soon as it arrives. Big files are sent in pieces, so a dropped connection only repeats one piece.</p>
      <label class="upload" id="dropzone"><input type="file" id="upfiles" multiple hidden><span class="primary-btn">Choose files</span></label>
      <div id="uplist" class="small"></div>
      <p class="muted small">Signed in as ${esc(CLOUD.user || "")} · <a href="/logout" style="color:var(--accent)">Sign out</a></p>`;
    const send = f => sendUpload(f, $("#uplist"));
    $("#upfiles").onchange = async e => { for (const f of e.target.files) await send(f); e.target.value = ""; };
    const dz = $("#dropzone");
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("on"); };
    dz.ondragleave = () => dz.classList.remove("on");
    dz.ondrop = async e => { e.preventDefault(); dz.classList.remove("on"); for (const f of e.dataTransfer.files) await send(f); };
    return;
  }
  return renderPhoneLocal(el);
}
async function sendUpload(f, list) {
      const row = document.createElement("div"); list.append(row);
      const id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).replace(/-/g, "");
      const CH = 32 << 20;
      try {
        let off = 0, j = null;
        do {
          const part = f.slice(off, off + CH), last = off + CH >= f.size;
          row.textContent = `Sending ${f.name}… ${f.size ? Math.round(100 * off / f.size) : 100}%`;
          let tries = 0;
          for (;;) {
            try {
              const r = await fetch(`/api/upload_part?id=${id}&name=${encodeURIComponent(f.name)}&offset=${off}&last=${last ? 1 : 0}`, {method: "POST", body: part});
              j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); break;
            } catch (err) { if (++tries > 4) throw err; await new Promise(res => setTimeout(res, 1500 * tries)); }
          }
          off += part.size;
        } while (off < f.size);
        row.textContent = "✓ " + f.name + (j && j.source ? " · scanning as " + j.source : "");
        homeLoop(false);
      } catch (err) { row.textContent = "✗ " + f.name + ": " + err.message; }
}
async function renderPhoneLocal(el) {
  if (REMOTE || DISCOVER.android_local) {
    const ios = /iPhone|iPad/.test(navigator.userAgent);
    el.innerHTML = `<div class="note ok">✓ ${REMOTE ? "This phone is connected to Rewind on your laptop." : "Rewind is running on this phone."}</div>
      <h3>Send files to Rewind</h3>
      <p class="muted">Pick exports you downloaded on this phone: Google Takeout, Instagram/Facebook/Apple data downloads, Snapchat My Data, WhatsApp chat exports (.zip), an SMS backup (.xml), or any photos and videos. Rewind scans each one as it arrives.</p>
      <label class="upload"><input type="file" id="upfiles" multiple hidden><span class="primary-btn">Choose files</span></label>
      <div id="uplist" class="small"></div>
      <h3 style="margin-top:28px">Install Rewind on this phone</h3>
      <p class="muted">${ios ? "In Safari tap Share › <b>Add to Home Screen</b>. Rewind then opens like an app." : "In Chrome tap ⋮ › <b>Add to Home screen</b> (or Install app). Rewind then opens like an app."}</p>
      ${REMOTE && /Android/.test(navigator.userAgent) ? `<h3 style="margin-top:28px">Get the Rewind Android app</h3>
      <p class="muted">The app scans this phone by itself (its trash, hidden trash files, previews, WhatsApp and Telegram folders) and restores straight to your gallery. <a href="/api/get-rewind.apk?t=${T}" download style="color:var(--accent)">Download Rewind.apk</a>, open it, and allow installing from your browser when Android asks.</p>` : ""}`;
    $("#upfiles").onchange = async e => {
      for (const f of e.target.files) {
        const row = document.createElement("div"); row.textContent = "Sending " + f.name + "…"; $("#uplist").append(row);
        try {
          const r = await fetch(`/api/upload?scan=1&name=${encodeURIComponent(f.name)}`, {method: "POST", headers: {"X-Rewind-Token": T}, body: f});
          const j = await r.json();
          if (!r.ok) throw new Error(j.error || r.statusText);
          row.textContent = "✓ " + f.name + (j.source ? " · scanning as " + j.source : "");
        } catch (err) { row.textContent = "✗ " + f.name + ": " + err.message; }
      }
      e.target.value = "";
    };
    return;
  }
  const st = await api("/api/phone");
  el.innerHTML = `<h3>Use Rewind on your phone</h3>
    <p class="muted">Your phone opens Rewind through this laptop: browse results, save restored photos back to the phone, and send exports you downloaded on the phone. Both must be on the same network: the same Wi-Fi, or the phone's hotspot over a USB cable (iPhone: Personal Hotspot on, then plug it in).</p>
    <label class="opt"><input type="checkbox" id="phoneon" ${st.on ? "checked" : ""}> Use on my phone</label>
    ${st.on ? `<div class="pairbox">${st.qr ? `<div class="qr">${st.qr}</div>` : ""}<div>
      <p>1. On the phone, ${st.qr ? "scan this QR code with the camera, or " : ""}type this address in the browser:</p>
      ${st.urls.length ? st.urls.map(u => `<p><code>${esc(u)}</code></p>`).join("") : `<p class="bad">This laptop isn't on any network right now. Connect it to Wi-Fi, Ethernet or your phone's USB hotspot.</p>`}
      <p>2. Enter this code: <code class="big">${esc(st.code)}</code></p>
      <p class="muted small">Only phones that enter the code can connect. Switching this off disconnects them. Use it at home or over USB, not on public Wi-Fi.</p></div></div>` : ""}`;
  $("#phoneon").onchange = async e => { try { await api("/api/phone", {on: e.target.checked}); } catch (err) { toast(err.message); } renderPhone(); };
}

$$("#nav button").forEach(b => { if (b.dataset.page === "phone") b.addEventListener("click", renderPhone); });
window.addEventListener("message", e => { if (e.data === "rewind-connected") { toast("Connected. Scanning…"); homeLoop(false); } });
if (!REMOTE && !CLOUD) {
  $("#btn-quit").classList.remove("hidden");
  $("#btn-quit").onclick = async () => { await api("/api/quit", {}); document.body.innerHTML = "<main><h2>Rewind is closed.</h2><p class='muted'>Open the Rewind app again any time.</p></main>"; };
}
// Older versions installed a service worker; remove it so it can never stand between you and the app.
if ("serviceWorker" in navigator) navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister())).catch(() => {});
if (REMOTE && !T) showPairing(); else boot();
