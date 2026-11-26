// Drives the Android app's screens with a fake native bridge and checks the flow.
const fs = require("fs");
const {JSDOM} = require("jsdom");
const dir = process.argv[2];
const ok = (label, c) => { console.log((c ? "  PASS " : "  FAIL ") + label); if (!c) process.exitCode = 1; };

function boot(perms) {
  const html = fs.readFileSync(dir + "/index.html", "utf8").replace('<script src="/app.js"></script>', "");
  const dom = new JSDOM(html, {runScripts: "outside-only", url: "https://rewind.app/index.html"});
  const w = dom.window;
  const calls = [];
  w.Rewind = {
    status: () => JSON.stringify({media: perms.media, allFiles: perms.allFiles, sdk: 34, trashApi: true, mac: ""}),
    askMedia: () => { calls.push("askMedia"); perms.media = true; },
    askAllFiles: () => { calls.push("askAllFiles"); perms.allFiles = true; },
    scan: () => calls.push("scan"),
    restore: ids => calls.push("restore:" + ids),
    openMac: a => calls.push("openMac:" + a),
  };
  w.scrollTo = () => {};
  const errors = [];
  w.addEventListener("error", e => errors.push(e.message));
  w.eval(fs.readFileSync(dir + "/app.js", "utf8"));
  return {w, calls, errors, $: s => w.document.querySelector(s), $$: s => [...w.document.querySelectorAll(s)]};
}

// first open, nothing allowed yet
let A = boot({media: false, allFiles: false});
ok("does not scan before access is allowed", !A.calls.includes("scan") && A.$("#home").classList.contains("on"));
ok("one button says 'Allow access and scan'", A.$("#btn-scan").textContent === "Allow access and scan");
A.$("#btn-scan").click();
ok("the button asks Android for access", A.calls.includes("askMedia"));
A.w.onPermissions();
ok("scan starts by itself right after Allow", A.calls.includes("scan") && A.$("#scanning").classList.contains("on"));

// later opens: access already allowed
const B = boot({media: true, allFiles: true});
ok("scans by itself on open when access is allowed", B.calls.filter(c => c === "scan").length === 1);
B.w.onNativeResume();
ok("returning to the app does not start a second scan", B.calls.filter(c => c === "scan").length === 1);
B.w.onScanProgress("Looking in the phone's trash…", 3);
ok("progress text updates", B.$("#scan-msg").textContent.includes("trash") && B.$("#scan-count").textContent.includes("3"));
B.w.onScanDone([
  {id: "t:image:1", kind: "image", name: "IMG_1.jpg", category: "trash", source: "Phone trash", why: "In Android's trash", date: "2026-11-20", size: 2000000, dup: false, trash: true},
  {id: "f:1", kind: "image", name: "55.jpg", category: "thumbnail", source: "Phone storage", why: "A preview", date: "2026-01-01", size: 9000, dup: false},
  {id: "f:2", kind: "image", name: "kept.jpg", category: "thumbnail", source: "Phone storage", why: "A preview", date: "", size: 9000, dup: true},
  {id: "f:3", kind: "video", name: "clip.mp4", category: "recovered", source: "WhatsApp", why: "cache", date: "", size: 1e6, dup: false},
], {gallery: 812});
const $ = B.$, $$ = B.$$, w = B.w;
ok("results show 3 deleted tiles (the kept photo is under Still here)", $("#results").classList.contains("on") && $$(".tile").length === 3);
ok("counters show deleted, still in gallery and in a bin", $("#summary").textContent.includes("3") && $("#summary").textContent.includes("812"));
ok("one-tap 'Restore all 3' is ready with nothing selected", $("#btn-restore").textContent === "Restore all 3" && !$("#btn-restore").disabled);
$("#btn-restore").click();
ok("Restore all sends every deleted item, not the kept one", B.calls.some(c => c.startsWith("restore:") && c.includes("t:image:1") && c.includes("f:3") && !c.includes("f:2")));
w.document.querySelector('#sides [data-side="here"]').click();
ok("'Still here' lists copies of photos you still have", $$(".tile").length === 1 && $("#side-note").textContent.includes("812"));
ok("nothing to restore on the Still here side", $("#btn-restore").disabled);
w.document.querySelector('#sides [data-side="deleted"]').click();
w.document.querySelector('#filters [data-f="trash"]').click();
ok("bin filter shows only the trash item", $$(".tile").length === 1);
w.document.querySelector('#filters [data-f="all"]').click();
$(".tile .c").click();
ok("selecting one changes the button to 'Restore 1'", $("#btn-restore").textContent === "Restore 1");
$("#btn-restore").click();
ok("restore sends just the selected id", B.calls[B.calls.length - 1] === 'restore:["t:image:1"]');
$(".tile").click();
ok("tapping a picture opens the viewer", !$("#viewer").classList.contains("hidden") && $("#v-media").innerHTML.includes("/media?id="));
$("#v-close").click();
$("#mac").value = "192.168.1.5:8766"; $("#btn-mac").click();
ok("Connect to Mac passes the address to Android", B.calls.includes("openMac:192.168.1.5:8766"));
w.onScanDone([], {gallery: 10});
ok("empty result explains where else copies may be", $("#empty").textContent.includes("email"));
ok("no script errors", A.errors.length === 0 && B.errors.length === 0);
if (A.errors.length || B.errors.length) console.log(A.errors, B.errors);
