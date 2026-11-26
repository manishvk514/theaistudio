# Rewind — hand-off for Claude Code

Read this first. It is the full state of the project as of 26 Nov 2026, written so a fresh
session can pick up cold. The owner is Manish (Mac: Apple Silicon M4, macOS 15-ish; phone:
Realme / ColorOS Android). He has no traditional coding background: explain in plain words,
do the work, and test on this Mac before telling him something works.

Labels: **[Inference]** = reasoned, not confirmed; **[Unverified]** = not checked against a source or device.

---

## 1. What Rewind is, and what he wants (his words, cleaned up)

A desktop + phone app that brings back deleted photos and videos from every place they can
still survive: the Mac itself, phone backups, memory cards, chat apps, and cloud accounts
(OneDrive, Google, Snapchat, Telegram, WhatsApp...), including "permanently deleted" ones where
any copy still exists somewhere.

His requirements, in order of what he has pushed hardest on:

1. **Fully automatic.** "It should not need me to upload any folder or anything. It goes into my
   Mac or phone by itself, searches every folder, and does it itself." Scan starts on open.
2. **Cloud in-app.** Sign in to OneDrive / Google / Telegram inside the app; use every legitimate
   route (APIs, exports, caches) for Snapchat, Telegram, OneDrive etc. Restore "properly and accurately".
3. **Deleted vs still here.** Separate what is really deleted from what he still has, even for
   things deleted years ago.
4. **One-tap restore.**
5. **Real downloadable apps** (Mac app + Android app), good-looking UI, "smooth".
6. Use open-source repos/tools wherever they help.

Hard limits he agreed to: only his own accounts and devices, official APIs and his own sign-ins and
exports; nothing that bypasses a platform's security. No paid developer accounts were created.

**His latest verdict (26 Nov, 18:13 IST):** still not smooth — scanning/extracting, cloud connections
and overall flow all feel rough. He wants to continue here in Claude Code.

---

## 1b. Version 5 (26 Nov 2026, Claude Code cloud session): what changed

- **Claude can drive Rewind (MCP server, `rewind/mcp.py`).** stdio for Claude Desktop / Claude Code on the Mac
  (`python3 -m rewind mcp`; one-step registration: `python3 -m rewind claude-setup` or the **Add to Claude** button), and
  HTTP `/mcp` on the Hugging Face Space for claude.ai and the phone apps. Tools: status, scan, progress, list, show
  (returns the picture), restore, connect, where_to_look, upload_link (cloud).
- **Hugging Face Space mode (`rewind/space.py`, `space/`, `tools/publish_space.py`).** `python3 -m rewind space`.
  Sign in with Hugging Face (Space owner only, `REWIND_ALLOWED_USERS` to add names); OAuth 2.1 server for MCP
  (protected-resource + authorization-server metadata, dynamic client registration, PKCE, consent page) using HF as the
  login; Claude Code may also send an `hf_` token. Everything signed with `SPACE_SIGNING_SECRET` (survives restarts).
  Accounts connect via rclone's sign-in helper running inside the Space: the user pastes back the 127.0.0.1 address
  (no developer app registration). Chunked uploads (32 MB parts) for big exports. Cloud plan = accounts only; uploads
  scan as they arrive; restore = signed 24 h zip link. Workspace `/data/rewind` when a bucket is mounted at /data.
- **Dropbox** (local via rclone, cloud via the same flow): deleted files from their last revision, restore in place.
- **Card/disk engine replaced** (`rewind/forensic/`, used by `carve` and the Mac Deep scan): reads FAT12/16/32, exFAT,
  NTFS deleted records (names, dates, exact cluster layout incl. fragmented files), carves only free space with full
  structural validation, keeps half-overwritten JPEGs as damaged; unknown layouts carved byte by byte.
- **Knowledge** (`rewind/knowledge.py`): per-service routes, incl. "permanently deleted".
- Tests added: `test_space.py` (42), `test_disk.py` (12, real FAT32/NTFS/exFAT built with mtools/ntfs-3g/exfat-fuse),
  `test_dropbox.py` (9). All suites pass in the Linux sandbox. `tests/package.json` makes Node treat the UI tests as
  CommonJS (the parent repo is ESM).
- Not verified anywhere real: live Hugging Face Space (the session could not reach huggingface.co at the time of
  writing; see section 11), live Google/Microsoft/Dropbox sign-in completion through the paste flow, claude.ai adding the
  connector, Claude Desktop on macOS, Docker image build (no Docker daemon in the sandbox; the staged Space folder was run
  directly and its requirements installed in a clean venv).

## 2. Status in one table

| Part | Built | Tested here (Linux sandbox) | Tested on his real device |
|---|---|---|---|
| Python engine (scan, carve, classify, export) | yes | yes (28 + 21 + 34 checks) | **no** |
| Automatic "scan everything" mode | yes | yes, on a simulated home folder | **no** |
| Web UI (Home / Deleted & still here / Advanced) | yes | yes, jsdom (25 checks) | **no** |
| Mac app bundle (Rewind.app) | v4 | launcher logic only (9 checks) | v2 failed: window showed "can't be reached" on 127.0.0.1:8765; v3 "still not opening"; **v4 untested** |
| Google Drive / OneDrive one-click sign-in (rclone) | yes | with a fake rclone + simulated Google/Microsoft APIs | **no live sign-in ever done** |
| Telegram account (Telethon) | yes | input checks only | **no** |
| Android app | v4 | screens via jsdom (20 checks), signature check | v1 "built for older Android", v3 "Harmful app blocked" by Play Protect; **v4 untested** |

Nothing has yet run end-to-end on a real Mac or phone. Treat every claim of "works" as sandbox-only.

---

## 3. First steps (do these in order)

1. **Run it from source on this Mac** (no app bundle, no Gatekeeper in the way):
   ```bash
   cd Rewind
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python3 -m rewind            # opens http://127.0.0.1:8777/ in the browser
   ```
   Watch the terminal output. Fix what breaks. Then press Scan and watch every place finish.
2. Give Terminal (or the Claude Code host app) **Full Disk Access** (System Settings › Privacy &
   Security › Full Disk Access), restart it, and scan again: Photos library DB, Messages,
   ~/Library/Group Containers and Recently Deleted are invisible without it.
3. Run the tests (section 6) on the Mac and fix failures.
4. Only then rebuild the Mac app (section 7) and test double-click start.
5. Try the cloud sign-ins live: `brew install rclone` (or the bundled one), press Connect for
   Google Drive and OneDrive, check files arrive. See section 8 for known limits.
6. Android: rebuild with Android Studio / Gradle (section 7), install on his Realme.

---

## 4. Architecture

A local Python web app. One engine, two front ends.

```
Rewind/
  rewind/                 Python package (the engine + web UI). Stdlib HTTP server, SQLite catalog.
    __main__.py           CLI: serve (default) | scan <source> k=v | summary | export <dir>
    server.py             ThreadingHTTPServer on 127.0.0.1:<port>, token header X-Rewind-Token,
                          all /api/* routes, Job threads, phone pairing (LAN port = port+1)
    desktop.py            pywebview window (not used by the current Mac launcher)
    core/
      media.py            file-type sniffing, EXIF/MP4 dates
      carve.py            raw carving: JPEG/PNG/GIF/WebP/HEIC/MP4/MOV/AVI/MKV, keeps truncated JPEGs
      imaging.py          thumbnails, dHash (Pillow -> ffmpeg -> sips/qlmanage fallbacks)
      catalog.py          SQLite: scans, items (verdict/category/group/primary/remote), kv store
      classify.py         deleted vs present: exact hashes + dHash (<=6 bits), burst-safe rule
      export.py           Restore: dated folder, 3 sub-folders, report.csv, "ask people" list
    sources/
      auto.py             THE AUTOMATIC MODE: plan() lists every place, run() scans in order
                          (libraries first, then bins/caches, iOS backups, Time Machine,
                          /Volumes cards, adb, exports in Downloads/Desktop/Documents, clouds).
                          Unchanged exports/backups are skipped by signature (kv "auto_done").
                          Also: full_disk_access() probe, deep_card() (osascript admin dd + carve).
      connect.py          one-click cloud sign-in via `rclone authorize drive|onedrive`;
                          token stored in <workspace>/cloud/rclone.conf (mode 600)
      cloud.py            Google Drive trash download/untrash; OneDrive delta feed + restore
                          (also old manual OAuth paths kept under Advanced)
      telegram_cloud.py   Telethon client on its own asyncio loop; needs user's api_id/api_hash
      computer.py         Mac/Win/Linux places: Trash, Photos library (Photos.sqlite trashed
                          state), QuickLook cache, Windows Recycle Bin $I, thumbcache
      ios_backup.py       Finder backups (unencrypted; encrypted via iphone_backup_decrypt)
      whatsapp.py, telegram.py, snapchat.py, takeout.py, exports.py (Meta/Apple/MMS/Time Machine),
      email_imap.py, android_adb.py, folder.py, carve_source.py, photos_db.py, this_phone.py
    web/                  index.html, app.css, app.js (vanilla JS), icons, sw.js (self-removing)
  android-app/            Native Android shell: one WebView + Java bridge (MainActivity.java)
    assets/               the phone UI (index.html, app.js, app.css)
    build.sh              Linux-sandbox build (aapt2 + javac + dx + apksigner); see section 7
  tools/
    build_mac_app.py      builds Rewind.app (Info.plist, launcher, app code, bundled Python, rclone)
    mac-launcher/         rewind_launcher.c (native exe that runs launcher.sh) + launcher.sh
    bundle-python/        how the bundled Python was assembled from conda-forge (see section 7)
  tests/                  see section 6
  README.md               user guide (install, use, per-service recipes)
  HOW-DELETION-WORKS.md   the deletion map: layers, 20 routes, hard limits
```

Key data flow: source.run(ctx, params) -> ctx.ingest_file()/add_missing() -> catalog ->
classify.classify() -> UI lists by verdict -> export.export() on Restore.

Verdicts: present / deleted / duplicate / missing. Categories for deleted: trash / recovered /
thumbnail / missing. "Deleted" is relational: a copy exists somewhere but not in the library.

Workspace (catalog, recovered files, thumbs, tokens): `~/Rewind/workspace` by default.

---

## 5. Run

```bash
python3 -m rewind                          # serve + open browser
python3 -m rewind --no-browser --port 8777
python3 -m rewind scan auto                # headless automatic scan, prints logs + summary
python3 -m rewind scan folder path=~/Pictures role=library
python3 -m rewind export ~/Desktop
```

---

## 6. Tests

```bash
pip install -r requirements.txt            # Pillow, pillow-heif, segno, telethon
python3 tests/run_tests.py                 # engine end-to-end on synthetic phones/backups/cards (28)
python3 tests/test_phone.py                # phone pairing, LAN listener, upload, quit (21)
python3 tests/test_auto.py                 # automatic mode, fake rclone, simulated Drive/Graph (34)
bash   tests/test_launcher.sh              # Mac launcher logic with stubbed open/osascript (9)
npm i jsdom --prefix /tmp/uitest
NODE_PATH=/tmp/uitest/node_modules python3 tests/run_ui.py                       # web UI (25)
NODE_PATH=/tmp/uitest/node_modules node tests/android_ui.test.js android-app/assets   # phone UI (20)
```
All pass in the Linux sandbox, from a clean copy of this folder in a fresh venv. One exception to watch:
`tests/test_auto.py` failed once with `TypeError: 'NoneType' object is not subscriptable` on the first
run, then passed 4 times in a row. [Inference] a check reads an item before it exists (timing);
find and fix it. None of the tests have ever been run on macOS.

---

## 7. Build

### Mac app
`tools/build_mac_app.py <python-prefix>` makes `Rewind.app`:
- `Contents/MacOS/Rewind` = native launcher (compile it on this Mac:
  `clang -O2 -arch arm64 -arch x86_64 -o tools/mac-launcher/Rewind-universal tools/mac-launcher/rewind_launcher.c`)
- `Contents/Resources/launcher.sh` (v4): stops older engines (`pgrep -f "-m rewind"`), picks a free
  port 8777-8799, starts the engine, waits up to 90 s for `/health` version 4.0 **and** the page,
  then opens a Chrome `--app` window (else default browser). Failure -> dialog + log.
  Log: `~/Library/Logs/Rewind/rewind.log`. State: `~/Library/Application Support/Rewind/engine.txt`.
- `Contents/Resources/python` = relocatable CPython 3.12 (in the sandbox it came from conda-forge
  osx-arm64 packages, trimmed; see tools/bundle-python/). **Simpler on a Mac:** use
  python-build-standalone (`uv python install 3.12`, or the "install_only" tarball from
  https://github.com/astral-sh/python-build-standalone/releases) and `pip install --target`.
- `Contents/Resources/bin/rclone` = rclone binary (set `REWIND_RCLONE_BIN=/path/to/rclone`;
  `brew install rclone` or https://rclone.org/downloads/).
- Sign: on a Mac, `codesign --force --deep -s - Rewind.app` (ad-hoc). In the sandbox `rcodesign` was used.
- Zip with `ditto -c -k --keepParent Rewind.app Rewind-Mac.zip` (keeps symlinks/permissions).

### Android app
`android-app/build.sh` only works in the Linux sandbox (Debian aapt2, dalvik-exchange, a
Robolectric android-all jar for API 36 classes, pyaxml to stamp compileSdk 36). **On the Mac,
turn it into a normal Gradle project in Android Studio** (https://developer.android.com/studio):
new "Empty Views Activity" project, package `app.rewind.photos`, minSdk 24, targetSdk 36, then copy
in `src/app/rewind/restore/MainActivity.java` (keep its Java package or move it),
`assets/`, `res/`, and the manifest's permissions. Build a signed release APK
(Build › Generate Signed App Bundle/APK) with a new key.

**Signing key:** the v4 APK was signed with a throwaway key that exists only in the cloud sandbox
and is NOT included here. Any APK you build now must use a new key, so uninstall the old Rewind
from the phone first (Android refuses an update signed by a different key).

---

## 8. Known issues and what was tried

**Mac start-up (most urgent)**
- v1/v2: double-click did nothing / Chrome window showed "This site can't be reached
  http://127.0.0.1:8765/ ERR_FAILED", and macOS said "Rewind 2 is running in the background".
  [Inference] causes: an engine from an older copy still running after the app was deleted, plus a
  service worker registered by v1/v2 at that origin that turned a failed load into ERR_FAILED.
- v3: "still not opening" (no screenshot, no log received).
- v4 fixes (untested on Mac): kill stale engines, new port range, wait for real page, dialog + log,
  PYTHONHOME set to the bundle, sw.js replaced with a self-unregistering worker, app.js unregisters SWs.
- Gatekeeper: unsigned/ad-hoc app needs System Settings › Privacy & Security › **Open Anyway** once;
  launcher then removes `com.apple.quarantine`. Only a paid Apple Developer ID + notarization removes
  the first warning. If macOS says "damaged": `xattr -cr /Applications/Rewind.app`.
- TCC: Full Disk Access is required for the complete scan; the Home screen shows a button that opens
  `x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`. [Unverified] that FDA
  granted to Rewind.app is inherited by its bash -> python children.
- Ask him for `tail -n 60 ~/Library/Logs/Rewind/rewind.log` whenever the app misbehaves.

**Android / Play Protect**
- v1: "built for an older version of Android" -> fixed with targetSdk 36 (v2).
- v3: "Harmful app blocked" (no install option), package `app.rewind.restore`. The dialog showed the
  generic icon and package name.
- v4: new package `app.rewind.photos`, new key, v2+v3 signing only, aapt2 resources (app_name, mipmap
  icons), **removed MANAGE_EXTERNAL_STORAGE**, removed cleartext traffic and DownloadListener, no
  reflection, assets served from `appassets.androidplatform.net`, Mac link opens in external browser.
  Cost: on Android 11+ the phone app only reaches Android's trash (MediaStore trash query) plus the
  gallery for comparison; hidden caches / other apps' folders need the Mac over USB (adb).
  [Speculation] which of these triggered the verdict; Play Protect doesn't say.
- Fallback for his own install: Play Store › Play Protect › ⚙ › turn off "Scan apps with Play
  Protect", install, turn it back on.
- His Downloads had loose index.html/app.js/icon.png: an APK got unzipped at some point; delete them.

**Cloud limits (by design of the services, not bugs)**
- **OneDrive:** Microsoft Graph has no "list recycle bin" call for personal accounts; restore by item
  id exists. Rewind catches deletions from the moment you connect via the delta feed
  (kv `onedrive_delta`); older deletions must be restored at onedrive.live.com › Recycle bin.
  Personal Vault is unreachable by apps. [Inference from Graph docs.]
- **Google Photos:** since 31 Mar 2025 the Library API only sees app-created media and never Trash.
  Route = photos.google.com/trash by hand (60 days) or Google Takeout zip (auto-picked from Downloads).
- **Google Drive:** Trash fully reachable (download + untrash) via rclone's sign-in.
- **Snapchat:** no API. Route = My Data export (accounts.snapchat.com) with Memories; Rewind opens the
  zip from Downloads and downloads Memories from its links (links expire after a few days).
- **Telegram:** needs his own api_id/api_hash from my.telegram.org (Telegram rule). Downloads media
  still in chats. Messages deleted "for everyone" and secret chats are gone from the servers.
- **rclone** uses rclone's public OAuth client; Google may show "rclone" on the consent screen and
  rate limits are shared. A Rewind-owned Google/Microsoft client would need verification.

**"Not smooth" (his complaint) — concrete suspects to check on the Mac**
- First automatic scan walks Pictures, Desktop, Documents, Downloads fully and hashes every media
  file; large libraries will be slow and the progress shows places, not percentages within a place.
- Photos library scan reads Photos.sqlite; without Full Disk Access it fails -> "blocked".
- Thumbnails for HEIC need pillow-heif; without it previews may be missing.
- Cloud Connect depends on rclone authorize's local port 53682 and the browser coming to front.
- The UI polls every 1.2-4 s; no websockets.

---

## 9. Next steps (priority order)

1. Run from source on this Mac; fix every error in the terminal; scan with Full Disk Access on.
2. Make the scan feel fast: incremental (skip unchanged files by mtime+size before hashing), parallel
   hashing, per-place % progress, results appearing live while scanning.
3. Rebuild the Mac app with python-build-standalone + `codesign -s -`, verify double-click start
   from /Applications on this Mac, then from a quarantined download.
4. Live-test Google Drive and OneDrive Connect; fix sign-in UX (bring browser to front, clear
   "waiting" state, error messages).
5. Live-test Telegram sign-in (phone code, 2FA) and a full chat media download with progress.
6. Android: move to Gradle/Android Studio, build signed, install on Realme, test MediaStore trash
   listing and restore (createTrashRequest), then decide whether a separate "deep" variant with
   all-files access is worth Play Protect friction.
7. Polish UI (he asked for a good, smooth UI): loading states, empty states, thumbnails grid
   performance, clear "can't recover and why" messages.
8. Optional: a proper native window (pywebview is already in desktop.py) instead of a Chrome app window.

## 10. Rules for working with Manish

- Label anything not verified with [Inference] / [Speculation] / [Unverified].
- When suggesting a tool or app, include a direct link to it.
- Ask before: spending money, creating developer/store accounts, accepting terms, changing account
  security, or anything that touches accounts other than his own.
- Only his own accounts and devices; official APIs, his own sign-ins and exports.

## 11. Hugging Face Space

- Publish/update: `HF_TOKEN=<write token> python3 tools/publish_space.py manishvk18/rewind` (creates a public Docker
  Space with `hf_oauth: true`; public so claude.ai can reach `/mcp`; every page/API/tool still needs the owner's HF login).
- After the first build: Space Settings › attach a storage bucket at `/data` (otherwise data resets on restart);
  `SPACE_SIGNING_SECRET`, `OAUTH_CLIENT_ID`, `SPACE_HOST`, `SPACE_AUTHOR_NAME` are provided by Hugging Face itself.
- Connector URL for claude.ai: `https://manishvk18-rewind.hf.space/mcp`.
- Logs: Space page › Logs. Health: `/health`.
