"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const N = window.Rewind;  // native bridge
let ITEMS = [], STATS = {}, filter = "all", side = "deleted", sel = new Set(), scanned = false, scanning = false;
const LABEL = {trash: "In a bin", recovered: "Copy", thumbnail: "Preview"};

function toast(m, ms = 3500) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }
function show(id) { $$(".screen").forEach(s => s.classList.toggle("on", s.id === id)); $("#bar").classList.toggle("hidden", id !== "results"); $("#btn-rescan").classList.toggle("hidden", id !== "results"); window.scrollTo(0, 0); }

function status() {
  const st = JSON.parse(N.status());
  $("#perm-media").classList.toggle("ok", st.media);
  if ($("#perm-files")) $("#perm-files").classList.toggle("ok", st.allFiles);
  $$(".perm.ok .pill").forEach(b => b.textContent = "✓ Allowed");
  $$(".perm:not(.ok) .pill").forEach(b => b.textContent = "Allow");
  if (st.mac && !$("#mac").value) $("#mac").value = st.mac;
  $("#btn-scan").textContent = st.media || st.allFiles ? "Scan this phone" : "Allow access and scan";
  $("#scan-note").textContent = !st.trashApi ? "This Android version has no system trash, so Rewind searches folders and caches instead." :
    "";
  return st;
}
// The scan starts by itself as soon as access is allowed: on open, and right after you tap Allow.
function autoScan() { const st = status(); if ((st.media || st.allFiles) && !scanned && !scanning) startScan(); return st; }
window.onNativeResume = autoScan;
window.onPermissions = autoScan;

function startScan() {
  scanning = true; scanned = true;
  $("#scan-msg").textContent = "Starting"; $("#scan-count").textContent = "";
  show("scanning"); N.scan();
}
$$("[data-ask]").forEach(b => b.onclick = () => b.dataset.ask === "media" ? N.askMedia() : N.askAllFiles());
$("#btn-scan").onclick = () => {
  const st = status();
  if (!st.media && !st.allFiles) { N.askMedia(); return; }   // asking first; the scan follows on its own
  startScan();
};
$("#btn-rescan").onclick = () => startScan();
$("#btn-mac").onclick = () => {
  const a = $("#mac").value.trim();
  if (!a) return toast("Type the address your Mac shows under Rewind › Phone.");
  N.openMac(a);
};

window.onScanProgress = (msg, n) => { $("#scan-msg").textContent = msg; $("#scan-count").textContent = n ? n + " found so far" : ""; };
window.onScanDone = (list, stats) => { scanning = false; ITEMS = list || []; STATS = stats || {}; sel.clear(); render(); show("results"); };

const deletedItems = () => ITEMS.filter(i => !i.dup);
function visible() {
  const base = side === "deleted" ? deletedItems() : ITEMS.filter(i => i.dup);
  return base.filter(i => filter === "all" || i.category === filter);
}
function render() {
  const del = deletedItems();
  const gallery = STATS.gallery || 0;
  $("#summary").innerHTML = `<div class="del"><b>${del.length}</b><span>deleted, found</span></div>
    <div class="here"><b>${gallery}</b><span>still in gallery</span></div>
    <div class="bin"><b>${del.filter(i => i.category === "trash").length}</b><span>in a bin</span></div>`;
  $$("#sides button").forEach(b => b.classList.toggle("on", b.dataset.side === side));
  $("#filters").classList.toggle("hidden", side !== "deleted");
  $("#side-note").textContent = side === "deleted"
    ? "Gone from your gallery but still somewhere on this phone. Restore puts them back in your gallery."
    : `Copies Rewind found of photos you still have (${gallery} photos and videos are in your gallery). Nothing to restore here.`;
  const v = visible();
  $("#grid").innerHTML = v.map(i => `<div class="tile ${sel.has(i.id) ? "sel" : ""}" data-id="${esc(i.id)}" style="background-image:url('/thumb?id=${encodeURIComponent(i.id)}')">
    <span class="b b-${i.category}">${LABEL[i.category] || ""}</span>${i.kind === "video" ? `<span class="v">▶</span>` : ""}${side === "deleted" ? `<span class="c"></span>` : ""}</div>`).join("");
  $("#empty").classList.toggle("hidden", v.length > 0);
  $("#empty").textContent = side === "here" ? "No extra copies of your current photos were found." : ITEMS.length && del.length ? "Nothing in this group." :
    "Nothing deleted was left on this phone. Android encrypts each file and wipes freed space, so photos that left every bin can only come back from copies elsewhere: your email, chats, Google Photos or a backup. Connect to Rewind on your Mac to search those.";
  $$(".tile").forEach(t => {
    const c = t.querySelector(".c");
    if (c) c.onclick = e => { e.stopPropagation(); toggle(t.dataset.id); };
    t.onclick = () => openViewer(t.dataset.id);
  });
  updateBar();
}
function toggle(id) { sel.has(id) ? sel.delete(id) : sel.add(id); render(); }
function updateBar() {
  const n = deletedItems().length;
  $("#selcount").textContent = sel.size ? sel.size + " selected" : "";
  $("#btn-restore").textContent = sel.size ? "Restore " + sel.size : (n ? "Restore all " + n : "Restore all");
  const can = side === "deleted" && (sel.size || n);
  $("#btn-restore").disabled = !can;
  $("#btn-restore").style.opacity = can ? 1 : .5;
  $("#btn-selall").classList.toggle("hidden", side !== "deleted");
}
$$("#sides button").forEach(b => b.onclick = () => { side = b.dataset.side; sel.clear(); render(); });
$$("#filters button").forEach(b => b.onclick = () => { $$("#filters button").forEach(x => x.classList.toggle("on", x === b)); filter = b.dataset.f; render(); });
$("#btn-selall").onclick = () => { const v = visible(); const all = v.every(i => sel.has(i.id)); v.forEach(i => all ? sel.delete(i.id) : sel.add(i.id)); render(); };
$("#btn-restore").onclick = () => doRestore(sel.size ? [...sel] : deletedItems().map(i => i.id));

function doRestore(ids) {
  if (!ids.length) return;
  const trash = ids.filter(id => id.startsWith("t:")).length;
  toast(trash ? "Android will ask you once to confirm putting " + trash + " item(s) back in the gallery." : "Saving to Pictures › Rewind…");
  N.restore(JSON.stringify(ids));
}
window.onSaved = n => toast(n + " item(s) saved to Pictures › Rewind in your gallery.");
window.onRestored = ok => { toast(ok ? "Back in your gallery." : "Nothing was restored."); if (ok) startScan(); };

function openViewer(id) {
  const i = ITEMS.find(x => x.id === id);
  if (!i) return;
  $("#v-media").innerHTML = i.kind === "video" ? `<video controls playsinline src="/media?id=${encodeURIComponent(id)}"></video>` : `<img src="/media?id=${encodeURIComponent(id)}">`;
  $("#v-info").innerHTML = `<b>${esc(i.name)}</b><div class="muted">${esc(i.why)}</div><div class="muted">${esc(i.date || "")}${i.w ? " · " + i.w + "×" + i.h : ""}${i.size ? " · " + (i.size / 1048576).toFixed(2) + " MB" : ""}</div>
    ${i.dup ? `<div class="muted">This is a copy of a photo you still have in your gallery.</div>` : `<button class="pill solid" id="v-restore">${i.id.startsWith("t:") ? "Restore to gallery" : "Save to gallery"}</button>`}`;
  $("#viewer").classList.remove("hidden");
  if ($("#v-restore")) $("#v-restore").onclick = () => { $("#viewer").classList.add("hidden"); doRestore([id]); };
}
$("#v-close").onclick = () => { $("#viewer").classList.add("hidden"); $("#v-media").innerHTML = ""; };

autoScan();
