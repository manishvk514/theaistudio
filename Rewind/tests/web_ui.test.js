// Loads the real Rewind page from a running server (fake home folder) and uses it like a person would.
const {JSDOM} = require("jsdom");
const base = process.argv[2];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ok = (label, c) => { console.log((c ? "  PASS " : "  FAIL ") + label); if (!c) process.exitCode = 1; };
async function until(fn, ms = 30000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await sleep(250); } return false; }

(async () => {
  const html = await (await fetch(base + "/")).text();
  const js = await (await fetch(base + "/static/app.js")).text();
  const dom = new JSDOM(html.replace('<script src="/static/app.js"></script>', ""), {runScripts: "outside-only", url: base + "/"});
  const w = dom.window;
  const errors = [];
  w.addEventListener("error", e => errors.push(e.message));
  w.fetch = (u, o) => fetch(new URL(u, base).toString(), o);
  w.scrollTo = () => {};
  w.confirm = () => true;
  w.navigator.clipboard = {writeText: async () => {}};
  const inline = html.match(/<script>(window\.REWIND_TOKEN[^<]*)<\/script>/);
  w.eval(inline[1]);
  w.eval(js);
  const $ = s => w.document.querySelector(s);
  const $$ = s => [...w.document.querySelectorAll(s)];

  ok("opens on Home with one big Scan button", $("#page-home").classList.contains("on") && !!$("#bigscan"));
  ok("scan starts by itself on open", await until(() => $("#bigscan").classList.contains("busy") || $$(".place").length > 0, 8000));
  ok("places checked are listed live", await until(() => $$(".place").length >= 3, 15000));
  ok("scan finishes and the button offers 'Scan again'", await until(() => $("#bs-label").textContent === "Scan again", 60000));
  ok("every place ends done", $$(".place").every(p => p.classList.contains("done")));
  ok("deleted counter shows found items (" + $("#c-del").textContent + ")", +$("#c-del").textContent >= 3);
  ok("still-here counter shows library items (" + $("#c-here").textContent + ")", +$("#c-here").textContent >= 2);
  ok("headline says what was found", /Found \d+ deleted/.test($("#sh-title").textContent));
  ok("Full Disk Access banner offers the settings button", !$("#fda").classList.contains("hidden") && !!$("#fda-go"));
  ok("accounts: Google Drive, OneDrive, Dropbox and Telegram can be connected",
     ["Google Drive", "OneDrive", "Dropbox", "Telegram"].every(t => $("#accounts").textContent.includes(t)) && $$("[data-conn]").length === 3);
  ok("'Add to Claude' is offered on the Mac", !!$("#claude-add"));
  ok("honest cards for Google Photos, Snapchat and iCloud",
     ["Google Photos", "Snapchat", "iCloud Photos"].every(t => $("#accounts").textContent.includes(t)));
  $("#tg-qr").click();
  ok("Telegram connect asks once for its app ID, not a phone number",
     await until(() => !!$("#tg-form [name=api_id]"), 5000) && !$("#tg-form [name=phone]"));
  $("#tg-form [name=api_id]").value = "abc";
  $("#tg-form").dispatchEvent(new w.Event("submit", {cancelable: true}));
  await until(() => $("#toast") && !$("#toast").classList.contains("hidden"), 5000);
  ok("Telegram form explains a wrong entry", /API ID/.test($("#toast").textContent));
  $("#tg-cancel").click();

  ok("Restore all button is ready", !$("#btn-restore-all").disabled && /Restore all \d+/.test($("#btn-restore-all").textContent));
  $("#btn-restore-all").click();
  ok("Restore all saves the files", await until(() => /Restored \d+ file/.test($("#sheet").textContent), 20000));
  ok("...into Pictures > Rewind Restored", $("#sheet").textContent.includes("Rewind Restored"));
  $("#close").click();

  $('[data-goview="present"]').click(); await sleep(1500);
  ok("'Still here' counter opens the still-here list", $("#page-results").classList.contains("on") && $$(".tile").length >= 2);
  $('#views button[data-view="deleted"]').click(); await sleep(1500);
  ok("deleted list shows tiles (" + $$(".tile").length + ")", $$(".tile").length >= 3);
  $(".tile").click(); await sleep(800);
  ok("an item shows why it counts as deleted and a Download button",
     $("#sheet").textContent.includes("Why") && $("#sheet").textContent.includes("Download"));
  $("#close").click();

  $('#nav button[data-page="advanced"]').click(); await sleep(1500);
  ok("Advanced keeps the manual sources", $$(".card").length >= 12 && !$('.card[data-id="auto"]') && !$('.card[data-id="card-deep"]'));
  $('.card[data-id="email"]').click();
  ok("manual email source still opens its form", $("#sheet").textContent.includes("App password"));
  $("#cancel").click();
  ok("activity log shows the automatic scan", $("#jobs").textContent.includes("Everything"));

  $('#nav button[data-page="phone"]').click(); await sleep(800);
  ok("phone page offers 'Use on my phone'", $("#phone-body").textContent.includes("Use on my phone"));
  ok("no script errors", errors.length === 0);
  if (errors.length) console.log(errors);
  process.exit();
})();
