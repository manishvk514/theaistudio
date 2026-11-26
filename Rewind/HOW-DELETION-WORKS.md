# How deletion really works, and every way back

Labels follow your rule: anything not checked against a primary source is marked
**[Inference]**, **[Speculation]** or **[Unverified]**.

## The one law

A deleted photo can come back **only if some copy of its bytes, or of a smaller version of
it, still exists somewhere you are allowed to read.** Nothing else matters. So the job
splits into two questions:

1. Where can bytes or derivatives of a picture survive after "delete"?
2. For each place: can we read it, with what key, and for how long?

## The seven layers a photo lives in

| Layer | What "delete" actually does there | What survives | How long |
|---|---|---|---|
| 7 · Other people | Nothing. Their copy is independent | The other person's phone, email recipients, shared albums, group chats | Until they delete it |
| 6 · Cloud service | Soft delete: moves to Trash/Recycle bin, then a timer purges it from the servers | Full original during the window | Google Photos 60 days if backed up (30 if not), Google Drive 30 days, iCloud Photos about 30 days, Android trash 30 days, OneDrive about 30 days [Unverified for OneDrive] |
| 5 · App | Removes the message row or the media file, often not both | Media files without messages, messages (with thumbnail and SHA-256) without media, caches | Until cache is cleared or overwritten |
| 4 · OS media index | Flags the item as trashed (iOS `ZTRASHEDSTATE`, Android `.trashed-` rename) | Original file in place, database row, thumbnails | Bin window; thumbnails often far longer |
| 3 · File system | Forgets the pointer (directory entry / inode), marks blocks free | All the bytes, untouched | Until new data lands on those blocks |
| 2 · Storage controller | On SSDs and phones, TRIM tells the controller the blocks are free; it erases them and reads back zeros | Nothing readable | Seconds to minutes |
| 1 · Encryption | Phones encrypt each file with its own key; the key goes when the file goes | Ciphertext without a key | Forever unreadable |

Reading the table top to bottom is the strategy: the higher the layer, the more likely a
copy is intact. Layers 3 to 1 decide whether raw carving can work at all: yes on memory
cards, USB sticks and spinning hard drives; no on modern phones and SSD laptops.

## Every route Rewind uses

| # | Route | Layer | Status in Rewind |
|---|---|---|---|
| 1 | Trash bins on devices: iPhone Recently Deleted (via backup), Mac Photos Recently Deleted, Android `.trashed-` files, Samsung / Xiaomi / Oppo gallery bins, Mac Trash, Windows Recycle Bin (with original path and deletion time from `$I` files) | 4 | Built and tested |
| 2 | Cloud trash through official APIs, with one-click sign-in (rclone's public sign-in, no developer setup): Google Drive Trash (download and put back); OneDrive's change feed, which reports every item deleted after you connect, with restore from the recycle bin | 6 | Built and tested against simulated Google and Microsoft servers; a live sign-in can only be tested on your Mac [Unverified live] |
| 3 | Old device backups as time machines: every iPhone backup you ever kept holds the camera roll as it was on that day | 3–4 | Built and tested |
| 4 | Thumbnails that outlive originals: iPhone `PhotoData/Thumbnails`, Mac Photos derivatives, Android `.thumbnails` and `.thumbdata`, Windows `thumbcache_*.db`, macOS QuickLook cache | 4 | Built; Windows thumbcache tested, QuickLook [Unverified] on new macOS versions |
| 5 | Deleted rows inside app databases: SQLite does not wipe deleted rows, it marks their pages free, so old thumbnails stay in the file (also in `-wal` files). Rewind carves every app database | 5 | Built and tested (a deleted WhatsApp thumbnail row came back) |
| 6 | The chat's second endpoint: WhatsApp's database lists every media file with its chat and SHA-256. Rewind lists what your phone lost so you can ask the other person, and links any copy found anywhere by its exact hash | 5–7 | Built and tested |
| 7 | Email as an accidental backup: every photo you ever attached to an email is in Takeout's Gmail export | 7 | Built and tested |
| 8 | Exports: Google Takeout (with original dates from sidecar JSON), Snapchat My Data (Memories downloaded from the export's links), Telegram Desktop export, WhatsApp chat exports | 6 | Built and tested (Snapchat link download needs a real export) |
| 9 | App caches: Telegram's media cache (Android, Mac), WhatsApp viewed Statuses, browser caches, files with no extension identified by their bytes | 5 | Built and tested |
| 10 | Raw carving of cards, sticks and old drives: follows each JPEG, PNG, GIF, WebP, HEIC, MP4/MOV, AVI, MKV structure to its exact end, keeps half-overwritten photos (flagged damaged) | 3 | Built and tested, including on a real FAT card image with 3 deleted photos |
| 11 | Edited versions: iPhone keeps renders of edited photos separately | 4 | Built |
| 12 | Matching by look: the same picture at another size or quality (a thumbnail, a WhatsApp re-compression) is recognised; two different shots at the same size are never merged, so bursts are not hidden | — | Built and tested |
| 13 | Live email: every folder incl. Sent and Trash, any IMAP provider (Gmail app password, Outlook Microsoft sign-in) | 7 | Built; tested against a simulated mail server |
| 14 | Data downloads: Instagram, Facebook, Messenger, Apple iCloud copy, X, TikTok, Samsung... with original dates read from Meta's JSON | 6 | Built and tested |
| 15 | Text-message pictures (MMS) from Android SMS backups | 5 | Built and tested |
| 16 | Old Photos libraries inside Time Machine, each snapshot counted once even though Time Machine hard-links files | 3–4 | Built and tested |
| 17 | "Ask people" list: per chat, what was lost and a message to send | 7 | Built and tested |
| 18 | Telegram's own servers: every photo and video still in your chats, downloaded at full quality through Telegram's official API (Telethon), after you sign in with your own API ID and login code | 6–7 | Built; sign-in checks tested, a live account can only be tested by you [Unverified live] |
| 19 | Automatic discovery: every place above that exists on the computer or phone is found and scanned on open, libraries first, then bins, caches, backups, drives, exports waiting in Downloads and connected accounts | — | Built and tested on a simulated home folder |
| 20 | Deep scan of a plugged-in memory card from the app: a read-only sector copy (macOS asks for your password), then carving | 3 | Built; the Mac password step can only be tested on a Mac [Unverified] |

## "Permanently deleted": what is left to find

Once a photo is out of every bin, the original bytes on the phone or in the cloud are gone
(the hard limits below). The only thing that can bring it back is **a copy that was made
before the delete**. Copies get made far more often than people notice:

| Every time you... | ...a copy was made here | Rewind route |
|---|---|---|
| Emailed it, or someone emailed it to you | The mail server (your Sent, their Inbox) | Email (live), Takeout Mail |
| Sent it in a chat | Every other member's phone; your chat database keeps its thumbnail and fingerprint | WhatsApp (ask list), Telegram cache, iPhone backup |
| Sent it by text message | Message database / SMS backup | MMS, iPhone backup |
| Posted or DM'd it on Instagram / Facebook / Snapchat | The service's servers, returned in "Download your information" | Data downloads, Snapchat |
| Backed up a phone or Mac | The backup, frozen on that day | iPhone backup, Time Machine |
| Viewed it in an app or on a computer | Thumbnail and preview caches | This Mac / PC, Android, deep scans |
| Shot it on a camera or saved it to a card | The card's free space | Deep recovery |
| Shared it to iCloud/Google Photos | The cloud library and its exports | Takeout, Apple data copy |

Rewind sweeps every route you give it, fingerprints each copy, and shows which ones are not in
your library any more. A preview found in one place is upgraded automatically when a full-size
copy of the same picture turns up somewhere else.

## Routes you do by hand (Rewind can then scan the result)

- **Restore an old iCloud backup to a spare iPhone**, then make a Finder backup of that phone and scan it. Apple offers no API for iCloud backups, so this is the only way into them [Inference].
- **WhatsApp's Google Drive / iCloud backup**: install WhatsApp on a spare phone, restore the chat backup, then scan that phone. The backup can only be opened by WhatsApp itself [Inference].
- **Ask the other person** in a chat to resend. Rewind's export includes a list of who to ask for what.
- **Old phones, laptops and cameras in a drawer**: each one is another backup; scan them as folders or cards.
- **Google One / Samsung Cloud / Xiaomi Cloud phone backups**: these restore only onto a phone. Restore one onto a spare or reset phone, then scan that phone with Rewind [Inference].
- **Xiaomi Cloud gallery trash** at https://i.mi.com and **iCloud Recently Deleted** at https://www.icloud.com/photos: check by hand within their windows.

## Hard limits (no app can get past these)

- **Phone internal storage after a real delete.** Phones encrypt files individually and TRIM freed space; the bytes are erased or unreadable. Any app claiming otherwise without root and a lucky timing window is overselling [Inference based on how file-based encryption and TRIM work].
- **SSD laptops after the trash is emptied.** TRIM makes the drive return zeros for freed blocks within minutes.
- **Cloud items past their bin window.** Once Google, Apple or Microsoft purge an item, it is gone from their side. Google states Takeout cannot return permanently deleted content.
- **Google Photos Trash and Locked Folder.** Since 31 March 2025 the Google Photos Library API can only see photos an app uploaded itself, and no API reaches Trash. You restore from https://photos.google.com/trash by hand, within 60 days.
- **OneDrive's recycle bin list.** Microsoft Graph can restore an item from a personal OneDrive's recycle bin, but has no call that lists the bin. So Rewind catches deletions from the moment you connect (through the change feed); for anything deleted before that, open https://onedrive.live.com › Recycle bin. OneDrive's Personal Vault is not reachable by apps at all [Inference from Microsoft's Graph documentation].
- **Snapchat has no app access.** Only the My Data export reaches your Memories; Rewind opens it automatically from Downloads.
- **Telegram messages deleted "for everyone"** are erased on Telegram's servers; secret chats never reach the servers. Only device caches can still hold them.
- **Snapchat snaps and chats.** Designed to vanish; only Memories and saved items are kept [Unverified: based on third-party guides and Snapchat's public description].
- **Encrypted backups without the key.** Rewind uses your own password (iPhone) or your own 64-digit key (WhatsApp). It never tries to guess or break one.
- **Other people's accounts.** Rewind only works with accounts and devices you sign in to yourself.

## What "deleted" means in the results

Deleted is a relationship, not a property: a picture is deleted if a copy survives
somewhere but no copy is in your current library. That's why Rewind asks you to add your
current library first. Without it, everything found is "not in your library", which
includes photos you still have.
