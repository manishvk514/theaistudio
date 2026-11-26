# Resurface

Recover deleted pictures and videos, and separate what was deleted from what you still have.

```
python run.py          # opens http://127.0.0.1:8765
```

Needs Python 3.10+. No install step. Optional: `pip install pillow` (thumbnails, look-alike
matching) and `ffmpeg` on the PATH (video thumbnails). Everything runs on your computer; the only
network traffic is to a cloud account you connect yourself.

## The idea

Deleting never erases. It flips a flag or drops a reference, and the bytes stay until something
needs the space. And by the time a picture is deleted, the system has usually copied it — a
gallery thumbnail, a chat-app cache, a recycle folder, a backup, a sync copy. Each copy has to die
separately. So recovery is two searches:

1. **Bytes whose reference is gone** — deleted file records and unallocated space on a disk.
2. **Copies nobody remembered** — trash folders, caches, databases, backups, exports, cloud bins.

The second search is what brings back things deleted years ago.

Three things make a file truly unrecoverable: it was **overwritten**, the storage ran **TRIM**
(SSDs, phone flash), or it was **encrypted with a per-file key that was destroyed** (every modern
iPhone and Android phone). When all copies are gone too, no software can bring it back.

## What it does

| Place | Method | Result |
|---|---|---|
| SD card, USB drive, camera, hard disk, disk image | Parses FAT12/16/32, exFAT and NTFS directly: reads deleted file records (name, dates, exact cluster layout), then carves unallocated space | Full-quality files, original names where the filesystem kept them |
| Quick-formatted or corrupted volume, unknown filesystem | Signature carving with structural validation of each format | Full-quality files, no names |
| Phone storage copy / PC folder | Trash and recycle folders (Windows `$RECYCLE.BIN` with original paths, Android `.trashed-*`, Linux, macOS, NAS, gallery apps), thumbnail stores, app caches, pictures stored inside SQLite databases, pictures embedded in any other file | Full files from trash; often thumbnails from caches |
| Old backup or export (Google Takeout, iPhone/iTunes backup, Snapchat/Telegram export, any zip or folder) | Everything in it is compared with what still exists | Whatever is in the backup and no longer in your library |
| Google Drive, Dropbox | Lists the account's trash / deleted revisions; download or restore in place | Full files while the provider still holds them |

**Formats:** JPEG, PNG, GIF, WebP, BMP, HEIC/HEIF, AVIF, CR3, MP4, MOV, M4V, 3GP, AVI, MKV, WebM.

### Permanently deleted photos (not on the phone, not in any bin)

The original cannot be undeleted. What can be found is a copy made earlier by something else, so
the app searches for those:

- **Search this computer** (one button): finds and scans the thumbnail caches, recycle bin, iPhone
  backups, Phone Link / WhatsApp / iMessage data, Dropbox and Drive caches, mailboxes and temp
  folders that exist on this machine, with your Pictures/Videos folders as the reference.
  Optionally browser caches too (pictures viewed in Google Photos, iCloud.com, WhatsApp Web).
- **Email attachments**: reads mbox, `.eml` and Apple `.emlx` mail and pulls out every attached
  picture and video with its file name, sender, subject and date.
- **Base64 text**: pictures stored as text inside chat exports, JSON/HTML dumps and contact cards.
- **Old devices, cards, drives, backups and exports**: scanned as described in the table above.

"How recovery works" in the app has the full checklist, including the routes only you can take
(other people's copies, other cloud services, places you posted it). If no copy survives anywhere,
nothing can recover the picture.

### Deleted vs. existing

Each finding gets a verdict from evidence, not from guesswork:

- **Deleted** — the filesystem marks it deleted, it lies in unallocated space, it is in a trash
  folder or cloud bin; or it was found in a cache/backup and nothing matching exists any more.
- **Still existing** — it is a live file, or an identical/look-alike copy of one (SHA-256, then a
  perceptual hash that survives resizing and recompression, so a thumbnail of a photo you still
  have is not reported as deleted).
- **Unsorted** — found in raw bytes or a backup with nothing to compare against. Add your current
  library under "What I still have" to resolve these.

Low-resolution copies of a deleted photo are hidden when the full-resolution version was also found.

## What it cannot do

- **Internal storage of a modern phone, after the trash is emptied.** Encrypted and TRIMmed; the
  original is gone for every tool. The app recovers the *copies*: trash, thumbnails, app caches,
  SD card, backups.
- **Google Photos, OneDrive (personal), iCloud Photos bins.** These providers offer no API for
  deleted items (Google removed library access for third-party apps in March 2025). Use their own
  trash while it lasts (Google Photos 60 days, OneDrive 30, iCloud 30); the app covers the local
  and backup routes. The in-app "How recovery works" page lists every route per service.
- **WhatsApp, Snapchat, Telegram servers.** No account-side copy of deleted media is available
  to anyone. Recovery comes from the device media folders, caches, backups and exports.
- **Anything purged by the provider and absent from every device and backup.**
- Encrypted iPhone backups, encrypted zips, BitLocker/LUKS/APFS volumes (unlock or decrypt first).
- Files fragmented into non-adjacent pieces on a volume with no usable filesystem records.
- RAW camera formats other than CR3; NTFS compressed/encrypted files.

Use it on your own devices and accounts.

## Using it

1. **Stop using the device or card** you want to recover from. Every new file can overwrite an old one.
2. Run `python run.py`. To read a raw device, run it as administrator/root
   (`/dev/sdX`, `/dev/rdiskN`, `\\.\E:`). An image file needs no special rights.
3. Add every place a copy could be: the card or drive, a copy of the phone's storage, old backups,
   your current library, cloud accounts.
4. Review under **Results**, select, and save to a **different drive** than the one scanned.

For a phone: connect by USB in file-transfer mode and copy its storage to the computer (or
`adb pull /sdcard phone-copy`), then scan that folder. Put the SD card in a card reader and scan
it as a drive.

Cloud sign-in uses an OAuth app you register yourself (Google and Dropbox require this), or a
pasted access token. Tokens stay in memory and are never written to disk.

Command line:

```
python -m resurface.cli scan card.img
python -m resurface.cli scan --role evidence ~/Takeout
python -m resurface.cli scan --role current ~/Pictures
python -m resurface.cli restore --to /mnt/other-drive/recovered
```

## Safety

- Sources are opened read-only; nothing is ever written to a scanned device.
- Saving into a scanned folder is refused.
- The server listens on 127.0.0.1 only, requires a per-session token, and rejects requests under
  any other host name.

## Tests

```
python -m unittest discover -s tests
```

The filesystem tests build real FAT12/16/32, NTFS and exFAT volumes with real drivers (mtools,
ntfs-3g, exfat-fuse), write media, delete it — including files fragmented around live ones — and
require byte-exact recovery plus correct deleted/existing separation. Also covered: quick-format
recovery, raw carving, trash folders, thumbnail matching, SQLite blobs, iOS backup manifests,
zip exports, email attachments, base64 media, the automatic search, and the cloud connectors.

**Verified here:** everything above on Linux against generated volumes and files. **Not verified
here:** the Google Drive and Dropbox connectors against the live services (tested against a local
mock of their documented APIs — no account was available), raw device access on Windows and macOS,
the Windows and macOS location lists used by "Search this computer" (only the Linux list ran here),
and real-world phone dumps. Treat those as untested until you have tried them.

## Layout

```
run.py                    launcher
resurface/formats.py      structural parsers: where each file ends, dimensions, capture date
resurface/carve.py        signature carving over any byte stream
resurface/fat.py exfat.py ntfs.py volumes.py    filesystem parsers, partition tables
resurface/residue.py      trash folders, caches, iOS backups, Takeout sidecars, email, base64
resurface/discover.py     known hiding places on Windows / macOS / Linux
resurface/catalog.py      findings database and the deleted/existing verdict
resurface/engine.py       scans, thumbnails, restore
resurface/cloud/          Google Drive, Dropbox, OAuth
resurface/knowledge.py    per-app recovery routes shown in the UI
resurface/server.py ui/   local web app
```
