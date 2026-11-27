# Rewind: bring back deleted photos and videos

Rewind is a Mac app and an Android app (and a Home Screen app on iPhone). Open it and it works
by itself: it finds every place on the computer or phone where a deleted photo or video can still
survive (bins, Photos' Recently Deleted, backups, caches, previews, memory cards, chat folders,
account exports in Downloads, and the cloud accounts you connect), compares everything with what
you still have, and splits it into **Deleted** and **Still here**. One tap restores all of it.

It only reads. It never deletes or changes anything on your phone, card or accounts.
The exceptions are buttons you press yourself: putting an item back into Google Drive or
OneDrive, and on Android, restoring an item from the phone's trash (Android asks you first).

---

## 1. Install it

There are two downloads. Nothing else to install: no Python, no Terminal.

| Download | For | What it does |
|---|---|---|
| **Rewind-Mac-v4.zip** | Mac with Apple Silicon (M1–M4) | Scans the whole Mac and your connected accounts by itself |
| **Rewind-Android-v4.apk** | Android phones (Android 7 to 16) | Scans the phone's trash by itself and restores to the gallery |
| iPhone | (no download) | Uses Rewind on your Mac from the iPhone, added to the Home Screen |

### Mac

1. Double-click **Rewind-Mac-v4.zip**. **Rewind** appears next to it. Drag it into **Applications**
   (replace the older Rewind if asked).
2. Open Rewind. Because it isn't from the App Store, macOS stops it the **first time only**:
   - macOS 15 or newer: click **Done**, open **System Settings › Privacy & Security**, scroll down and click **Open Anyway**, then **Open**.
   - macOS 14 or older: **right-click Rewind › Open › Open**.
   After that first approval Rewind removes macOS's "downloaded from the internet" mark from itself, so it opens
   normally from then on. Only a paid Apple Developer ID (99 USD a year) would remove that first warning completely;
   I didn't buy one or create any developer account.
   If macOS instead says the app "is damaged", open Terminal and run `xattr -cr /Applications/Rewind.app`, then open it again.
3. Rewind opens its window (in Chrome's app mode if you have Chrome, otherwise in Safari) and **starts scanning by itself**.
4. The first time, a yellow box asks for **Full Disk Access**. Press **Open Full Disk Access settings**, switch on
   **Rewind** (press **+** and pick Rewind in Applications if it isn't listed), then quit and reopen Rewind.
   Without it macOS hides Photos' Recently Deleted, Messages and app caches from every app; with it the scan is complete.
   macOS may also ask once each for Desktop, Documents, Downloads and removable drives: press **Allow**.

On an Intel Mac, Rewind's built-in Python doesn't run; it falls back to an installed Python
(https://www.python.org/downloads/macos/) and opens in your browser instead.

### Android

1. Uninstall any older Rewind, and delete older Rewind files (Rewind.apk, index.html, app.js, icon.png) from Downloads.
2. Copy **Rewind-Android-v4.apk** to the phone and tap it. Allow installing from that source (Files or Chrome), then **Install**.
   Version 4 asks only for photo and video access (no "all files" access, no plain-web traffic, no hidden code), has its own
   name and icon, and is signed with a new key, so Play Protect sees it as a new app.
   If Play Protect still blocks it: open the Play Store › your profile picture › **Play Protect** › ⚙ › switch off
   **Scan apps with Play Protect**, install Rewind, then switch it back on. Play Protect only warns because the app isn't from the
   Play Store, whose listing needs a paid Google developer account.
3. Open Rewind and tap **Allow access and scan**. Allow *Photos and videos*; the scan starts by itself, and again every time you open it.
4. You see **Deleted** and **Still here**. Tap **Restore all** (or pick some): items in Android's trash go back to where they were,
   after Android asks you once to confirm.
5. On Android 11 and newer, apps can't open other apps' folders or hidden caches. Rewind on the Mac reads those over a USB cable
   (Advanced › Android phone). To use the Mac from the phone, type the Mac's address into **Connect to Rewind on your Mac**; it opens in your browser.

## If the Mac app doesn't open

Rewind shows a dialog with the reason. Press **Open log**, or in Terminal run `tail -n 60 ~/Library/Logs/Rewind/rewind.log`
and send the output. Rewind stops older copies of itself when it starts, so deleting old versions from Applications is enough.

### iPhone

Apple lets only App Store apps install normally, and no iPhone app, from the App Store or anywhere,
can read other apps' data or deleted files. So on iPhone, Rewind is a remote for the Mac:
1. On the Mac: Rewind › **Phone** › tick **Use on my phone**.
2. Join the same network (same Wi-Fi/router, or iPhone **Personal Hotspot** on and the iPhone plugged into the Mac).
3. Scan the QR code with the Camera, enter the 6-digit code, then **Share › Add to Home Screen**. It opens like an app:
   browse what was found, save photos back into Photos, and send exports you downloaded on the iPhone.
Your iPhone's own deleted photos come in through the Mac (Finder backup, iCloud Recently Deleted, your accounts).

### Phone and Mac on the same network

Only phones that type the 6-digit code can connect, and unticking **Use on my phone** disconnects them.
The connection isn't encrypted, so use it at home or over the USB cable, not on public Wi-Fi.

### Other ways to run it

`start-mac.command`, `start-windows.bat` and `start-linux.sh` run Rewind from the source folder with an installed Python.
Android phones can also run the full Rewind engine in Termux (`install-android.sh`; see the end of this guide).

---

## 2. Use it

**Open Rewind. That's it.** It scans by itself every time it opens (only new or changed places
take time after the first scan). The Home screen shows each place as it is checked:

| Place (found automatically) | What it finds |
|---|---|
| Photos library, Pictures, Desktop, Documents, Downloads, iMessage, WhatsApp | What you **still have**: everything else is compared with these |
| Trash and Photos' **Recently Deleted** | Full originals deleted in the last ~30 days |
| QuickLook, Telegram, WhatsApp and browser caches | Copies and previews that outlive the original |
| iPhone backups made by Finder | The camera roll and app media as they were on each backup day |
| Time Machine (when the disk is plugged in) | Photos deleted after that backup |
| Memory cards and USB drives plugged in | What's on them now; press **Deep scan** to rebuild deleted files from the card's free space (asks for your Mac password, read-only) |
| Account exports in Downloads, Desktop or Documents | Google Takeout, Snapchat My Data, Instagram/Facebook downloads, WhatsApp chat exports, Telegram exports and SMS backups are **opened automatically** |
| Connected accounts | Google Drive, OneDrive, Dropbox and Telegram (see below) |

**Accounts (one click, on Home):**

- **Google Drive › Connect**: Google's normal sign-in page opens in your browser; press Allow. Rewind downloads everything in Drive's Trash
  (kept 30 days) and can put items back in Drive. No developer setup.
- **OneDrive › Connect**: Microsoft's sign-in page opens; press Accept. Rewind learns what's still in OneDrive and from then on catches every
  photo deleted from it, with one-tap **Restore** while it is in the recycle bin (30 days). Microsoft gives apps no list of the recycle bin
  itself, so items deleted *before* you connected are restored at https://onedrive.live.com › **Recycle bin**.
- **Dropbox › Connect**: Dropbox's sign-in page opens; press Allow. Rewind brings back every deleted photo and video Dropbox still keeps
  (30 days on Basic and Plus, 180 on Professional and Business, 365 on Advanced) from its last saved version, and can put it back in Dropbox.
- **Telegram › Connect**: Telegram requires every app to use its own free API ID, so get yours once at https://my.telegram.org
  (API development tools), type it in with your phone number, then the login code Telegram sends you. Rewind downloads every photo
  and video still in your chats.
- **Google Photos, Snapchat, iCloud, Instagram** have no app access to deleted items. Their cards on Home link to the fastest route; when
  the export zip lands in Downloads, Rewind opens it by itself on the next scan.

These sign-ins use [rclone](https://rclone.org) (Google Drive, OneDrive, Dropbox) and [Telethon](https://github.com/LonamiWebs/Telethon) (Telegram),
both open source and built into the app. The access stays on your Mac, in Rewind's workspace, and you can remove it any time
(**Disconnect**, or https://myaccount.google.com/permissions and https://account.live.com/consent/Manage).

**Restore:** press **Restore all deleted**. Copies go to **Pictures › Rewind Restored › Restored (date)**, in three folders
("was in a bin", "rebuilt", "preview only"), grouped by source and month, with original dates put back and `report.csv`
explaining where each came from. Nothing is changed where they were found. To look first, tap a counter:
**Deleted**, **Still here**, or **Can't recover** (Rewind knows these existed but no copy is left; for chats it writes the
message to send the other person, who usually still has them).

**Advanced** keeps the manual options for anything Rewind can't reach on its own: a folder elsewhere, an encrypted
iPhone backup (needs its password), a disk image, a live email account, an Android phone over USB.

---

## 2b. Use it from Claude

Rewind is also a tool Claude can use (an MCP server). Ask Claude "find my deleted photos", "show me what was deleted
from WhatsApp", "restore everything from 2023", and it scans, looks at the photos with you and restores them.

**On the Mac (Claude Desktop and Claude Code, full access to the Mac):** press **Add to Claude** on Rewind's Home
(Accounts column), or run `python3 -m rewind claude-setup` once. Quit and reopen Claude Desktop. Rewind now appears in
Claude's tools. It runs on your Mac, so it reaches everything the app reaches. For the complete scan, give **Claude** Full Disk Access too
(System Settings › Privacy & Security › Full Disk Access), the same way as for Rewind [Inference: macOS checks the app that
started Rewind, here Claude].

**Everywhere else (claude.ai, the Claude phone apps):** use Rewind on Hugging Face (next section), and add its
connector: claude.ai › **Settings › Connectors › Add custom connector**, paste `https://<your-space>.hf.space/mcp`, press
**Connect**, sign in with Hugging Face, press **Allow**. Custom connectors added on claude.ai also show up in the phone apps
[Unverified: per Anthropic's help pages, not tried on your account].

What Claude can do: `rewind_status`, `rewind_scan`, `rewind_progress`, `rewind_list`, `rewind_show` (Claude sees the
picture), `rewind_restore`, `rewind_connect` (Google Drive, OneDrive, Dropbox), `rewind_where_to_look` (every route for
a service, including what only you can do), and in the cloud `rewind_upload_link`.

## 2c. Rewind on Hugging Face

A copy of Rewind that runs on Hugging Face, so you can use it from any browser and from claude.ai. It **cannot see your
Mac or phone** (it runs in Hugging Face's data centre), so it works on:

- your accounts: Google Drive, OneDrive, Dropbox, Telegram;
- exports and images you upload on its **Upload** tab: Google Takeout, Snapchat My Data, Instagram/Facebook, WhatsApp chat
  exports, SMS backups, zipped iPhone backups, memory-card images (with the full card engine below), loose photos/videos.

Only you can open it: it asks you to **Sign in with Hugging Face** and lets in only the Space's owner.

**Connecting accounts there:**

- **Telegram:** press **Connect** and scan the QR code with the Telegram app on your phone (Settings › Devices ›
  Link Desktop Device). No phone number, no code. Telegram requires a free app ID once
  ([my.telegram.org › API development tools](https://my.telegram.org/apps)); type it once, or add it to the Space's
  secrets as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. From Claude: "connect my Telegram" shows the QR code in the chat.
- **OneDrive, Google Drive, Dropbox, one click:** open `https://<your-space>.hf.space/setup` once. It shows, for each
  service, the free app keys to create and the exact address to paste. Add them as Space secrets
  (`MS_CLIENT_ID`/`MS_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, `DROPBOX_APP_KEY`/`DROPBOX_APP_SECRET`).
  Then Connect → sign in → Allow, and you are back in Rewind, connected and scanning. Nothing to copy.
- **Without those keys** Connect still works, with one extra step: after **Allow** the browser shows "This site can't be
  reached". Nothing is wrong. Copy that page's address and paste it into the box Rewind shows (that is how the
  open-source helper rclone signs in without registered app keys).
- **Snapchat** has no sign-in for apps. The Snapchat card has **Request my data** (choose JSON, tick Memories and Chat
  Media) and an upload button for the zip Snapchat e-mails you.
- **OneDrive's recycle bin:** Microsoft gives apps no list of a personal OneDrive's recycle bin. Rewind catches every
  photo deleted *after* you connect (and restores it); for older ones open onedrive.live.com › Recycle bin ›
  **Restore all items**, then scan again.

**Keep results across restarts:** in the Space's Settings, attach a storage bucket mounted at `/data`. Without it, the
Space starts empty after each restart (sleep, rebuild).

Publish or update it with `HF_TOKEN=hf_... python3 tools/publish_space.py <your-name>/rewind`.

---

## 3. Recipes for each account and device

### iPhone
1. Connect the iPhone to the Mac, open **Finder**, click the iPhone, choose **Back up all of the data on your iPhone to this Mac**, then **Back Up Now**.
   An encrypted backup holds more (it includes some app data); Rewind asks for its password.
2. In Rewind: **iPhone backup** › pick the backup (Rewind lists the ones it finds) › *latest backup*.
3. Older backups you kept (old Mac, external drive) are gold: add them as *older backup*.
4. If iCloud Photos is on, the originals live in iCloud, not in the backup. Check **Recently Deleted** at https://www.icloud.com/photos (kept about 30 days).
5. Encrypted backups need one extra helper: open Terminal in the `restore-app` folder and run
   `.venv/bin/pip install iphone_backup_decrypt` ([what it is](https://github.com/jsharkey13/iphone_backup_decrypt)).

### Android
1. Download **Android Platform Tools** from https://developer.android.com/tools/releases/platform-tools and unzip it into your **Downloads** folder (Rewind finds it there).
2. On the phone: Settings › About phone › tap **Build number** 7 times › back › Developer options › turn on **USB debugging**.
3. Plug in the phone, tap **Allow** on the phone, then in Rewind choose **Android phone (USB cable)**.
   It copies the camera, bins, hidden trash files, caches and app media folders to your Mac, then scans the copy.

### WhatsApp
- Android: the Android source already copies `Android/media/com.whatsapp`. To also read the message database
  (thumbnails and the list of media from deleted chats), you need your **64-digit end-to-end backup key**
  (WhatsApp › Settings › Chats › Chat backup › End-to-end encrypted backup) and
  [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools): `.venv/bin/pip install wa-crypt-tools`. Enter the key in the WhatsApp source.
- iPhone: comes in automatically through the iPhone backup.
- Single chats: WhatsApp › chat › ⋮ › More › **Export chat › Include media**, then give Rewind the .zip.

### Google Photos, Drive, Gmail
1. Go to https://takeout.google.com, click **Deselect all**, tick **Google Photos**, **Drive** and **Mail**, export as .zip.
2. Give Rewind the downloaded .zip (or the folder of zips).
3. Google Photos' own Trash (60 days) can't be reached by any app. Open https://photos.google.com/trash and press Restore there, then export again.
4. Leave the .zip in Downloads: Rewind opens it by itself on its next scan.

### Google Drive and OneDrive
Home › **Accounts › Connect**. That's all: no Google Cloud project or Microsoft app registration any more.
(Advanced still has the old manual options if you prefer your own Google or Microsoft app ID.)

### Photos that were "permanently deleted"
When a photo is gone from the phone and every bin, its bytes on the phone are gone too.
What remains are **other copies** that were made before you deleted it. Rewind hunts all of them;
add as many of these as you can:
1. **Your mailbox** (Email source). Every emailed photo still sits on the mail server, in Sent and in the inbox.
2. **Old backups**: every iPhone backup you kept, Time Machine disks, old laptops and old phones in a drawer.
3. **Data downloads** from services the photo passed through: Instagram/Facebook/Messenger, iCloud, Snapchat, Google.
4. **Chats**: WhatsApp's database lists what each chat had. Rewind writes a ready message per chat
   ("ask people to resend.txt", and a Copy button on *Known, bytes gone*), because the other people still have their copies.
5. **Text messages**, **memory cards** from cameras, and **previews** in caches, which often outlive the original by years.

### Email
- **Gmail**: turn on 2-Step Verification, then make an app password at https://myaccount.google.com/apppasswords and paste it into Rewind.
- **iCloud Mail**: make an app-specific password at https://account.apple.com (Sign-In and Security › App-Specific Passwords).
- **Yahoo**: Account security › Generate app password at https://login.yahoo.com/account/security.
- **Outlook / Hotmail**: Microsoft no longer accepts app passwords for mail apps [Unverified: per a third-party mail-app guide; Microsoft says it is retiring them]. This one still needs your own free Microsoft app ID (Advanced › Email); Rewind then signs you in with a code.
Rewind only reads mail. Delete the app password afterwards if you like.

### Instagram, Facebook, Messenger
Request a copy at https://accountscenter.instagram.com/info_and_permissions/ or https://accountscenter.facebook.com/info_and_permissions/
(**Download your information › Download or transfer information**, format JSON, media quality High), then give Rewind the .zip.
Instagram says deleted content does not appear in the download, so first check **Recently deleted** in the app
(Your activity › Recently deleted, about 30 days) and restore from there.

### Apple (iCloud Photos copy)
Go to https://privacy.apple.com › **Request a copy of your data** › tick iCloud Photos. Apple emails you download
links in a few days; give Rewind the downloaded .zip files.

### Text messages (Android)
Install **SMS Backup & Restore** (SyncTech) from Google Play, back up messages including MMS to a local file,
copy the `sms-….xml` to your Mac and give it to Rewind's MMS source.

### Time Machine
Connect the Time Machine disk. In Rewind choose **Time Machine or a copy of a Photos library** and pick the
backup folder (Rewind lists ones it sees). Grant Terminal Full Disk Access first (see above).

### Telegram
- **Connect** Telegram on Home (API ID from https://my.telegram.org, once). Rewind downloads every photo and video still in your chats.
  Messages deleted "for everyone" are erased on Telegram's servers; secret chats are never on the servers. Those can only come back from
  the Telegram cache on a device, which Rewind also scans.
- Or export from Telegram Desktop (https://desktop.telegram.org › Settings › Advanced › **Export Telegram data**); the export folder in
  Downloads is opened automatically.

### Snapchat
- Snapchat gives apps no access. Request your data at https://accounts.snapchat.com › **My Data** (tick Memories and Chat Media).
  When the email arrives, download the .zip into Downloads; Rewind opens it on its next scan and downloads your Memories.
  Do it **within a few days**, because the Memories download links expire. Snaps that were opened and deleted are gone from Snapchat's servers.

### Memory cards, USB sticks, old hard drives
Rewind reads FAT12/16/32 (cards up to 32 GB), exFAT (SDXC cards, most cameras) and NTFS (Windows drives) itself:
first the file system's own list of deleted files, with their names, dates and exact layout, even when a file was split into
pieces around others; then the free space, for anything whose record is gone. Every file is checked against its own format
(each PNG checksum, every JPEG and video structure) so nothing half-overwritten is passed off as whole; photos whose
bottom part was overwritten are kept and marked damaged. Files still on the card count as "still here".
1. Stop using the card. Insert it in the Mac. It appears on Home as **Drive or card: NAME**.
2. Press **Deep scan** next to it. macOS asks for your password; Rewind then copies the card sector by sector (read-only) into its
   workspace and rebuilds deleted photos and videos from the copy, even from years ago. The copy is removed afterwards.
3. Your Mac's own disk can't be deep-scanned: it is an SSD with TRIM and encryption, so files deleted there are erased
   for good once the Trash is emptied. Rewind says so instead of pretending.
For even more file types, install [PhotoRec](https://www.cgsecurity.org/wiki/PhotoRec) and use Advanced › Memory card with "Also run PhotoRec".

---

## 4. Optional helpers (all free)

| Helper | Why | Get it |
|---|---|---|
| Pillow + pillow-heif | Read HEIC, compare pictures by look, make previews | installed by the start script ([Pillow](https://pypi.org/project/pillow/), [pillow-heif](https://pypi.org/project/pillow-heif/)) |
| ffmpeg | Previews and look-matching for videos | `brew install ffmpeg` ([Homebrew](https://formulae.brew.sh/formula/ffmpeg)) |
| iphone_backup_decrypt | Encrypted iPhone backups | https://github.com/jsharkey13/iphone_backup_decrypt |
| wa-crypt-tools | Encrypted WhatsApp databases (with your own key) | https://github.com/ElDavoo/wa-crypt-tools |
| Android Platform Tools (adb) | Talk to an Android phone over USB | https://developer.android.com/tools/releases/platform-tools |
| PhotoRec | Extra-thorough card carving | https://www.cgsecurity.org/wiki/PhotoRec |

On a Mac without Pillow, Rewind uses the built-in `sips` and `qlmanage` tools instead.

## 5. Where your data goes

Nowhere. Rewind listens only on `127.0.0.1` (your own computer). When you tick **Use on my phone**,
it also listens on your home network, but answers only phones that entered the 6-digit code. Scans, previews and the
catalog live in `~/Rewind/workspace` (the Mac app keeps its setup in `~/Library/Application Support/Rewind`) (delete that folder to start over). Sign-in tokens for
Drive/OneDrive are kept there too, so delete it when you're finished if you share the Mac.

## 6. Command line (optional)

```
python3 -m rewind                                   # open the app
python3 -m rewind scan folder path=~/Pictures role=library
python3 -m rewind scan carve path=~/Desktop/card.img
python3 -m rewind export ~/Desktop
python3 tests/run_tests.py                          # self-test with fake phones, backups and cards
```

Read `HOW-DELETION-WORKS.md` for the full reasoning behind every route, and its hard limits.

## 7. Appendix: full Rewind engine on Android (Termux)

1. Install **Termux** from F-Droid: https://f-droid.org/packages/com.termux/ (the version Termux's own developers recommend).
2. Get Rewind onto the phone: in Rewind's **This phone** page on the paired phone, tap **Download Rewind for Android**
   (or copy `Rewind.zip` into the phone's Download folder).
3. Open Termux and type these two lines (tap Allow when Android asks about files):
   ```
   termux-setup-storage
   pkg install -y unzip && cd ~/storage/downloads && unzip -o Rewind.zip && bash restore-app/install-android.sh
   ```
4. Rewind opens in Chrome. Add it to the Home screen. Next time, open Termux and type `rewind`.
   (Install **Termux:Widget** from F-Droid to get a one-tap Rewind button on the home screen.)

On the phone, choose **This phone** to scan the camera, hidden `.trashed-` files, gallery bins, thumbnail caches and
WhatsApp/Telegram/Snapchat folders. Newer Android versions block every app from `Android/data`, so for those few app
caches use the USB route from the Mac.
