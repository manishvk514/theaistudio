"""Every route by which a deleted photo or video can still come back, per service
and device. Shared by Rewind's window and the Claude tools (rewind_where_to_look).

`do` values: auto (Rewind does it by itself), disk / folder / evidence (Rewind
scans it once you point it there or upload it), cloud:X (connect the account),
manual (only the account owner can do it, in the service's own app or website).
"""
PRINCIPLES = [
    ("Deleting hides, it does not erase",
     "Storage systems delete by flipping a flag or dropping a reference - rewriting the bytes would be slow. "
     "The content stays until something else needs the space. Recovery = find bytes whose reference is gone."),
    ("Every copy must die separately",
     "By the time you delete a picture, the system has usually copied it: a thumbnail for the gallery grid, a preview "
     "in a chat cache, a sync copy on another device, a nightly backup, the copy the recipient received. "
     "Deleting the original removes one of them."),
    ("Three things make it truly gone",
     "1) Overwritten - new data landed on the same blocks. 2) TRIM - SSDs and phone flash are told to erase freed blocks "
     "right away. 3) Encryption - modern phones encrypt each file with its own key and destroy the key on delete. "
     "When all three have happened and no other copy exists, nothing can recover it, and any tool promising otherwise is lying."),
    ("So: stop writing, then search widely",
     "Stop using the device or card at once (every new photo can overwrite an old one). Then search every place a copy "
     "could have been made. That second step is what recovers things deleted years ago: not the original, but a copy "
     "nobody remembered existed."),
]

MAP = [
    {"id": "gone", "name": "Permanently deleted: not on the phone, not in any bin", "chance": "depends entirely on whether a copy survives",
     "how": "The original cannot be undeleted - on a phone its encryption key is destroyed, in a cloud the provider has purged it. "
            "What can still be found is a copy made earlier by some other mechanism. Work down this list; each line is a separate chance.",
     "routes": [
         {"t": "This computer: thumbnail caches, recycle bin, phone backups, chat apps, sync caches, mailboxes ('Search this computer' does all of these)", "do": "auto", "auto": True},
         {"t": "An old phone, tablet or laptop that used the same account. Keep it OFFLINE (airplane mode) so it cannot sync the deletion, then copy its storage and scan it", "do": "folder", "auto": True},
         {"t": "The memory card that was in the phone or camera at the time, even if since formatted", "do": "disk", "auto": True},
         {"t": "Any old disk, USB stick or external drive the photos ever passed through", "do": "disk", "auto": True},
         {"t": "Email: anything you ever sent or received as an attachment. Export the mailbox (Google Takeout > Mail, or your mail program's folder) and scan it", "do": "evidence", "auto": True},
         {"t": "Chat exports and other account downloads: WhatsApp 'Export chat', Telegram export, Snapchat My Data, Facebook/Instagram 'Download your information', Google Takeout", "do": "evidence", "auto": True},
         {"t": "Old backups: iPhone backups on a computer, Windows File History, Time Machine, NAS, a backup drive", "do": "evidence", "auto": True},
         {"t": "Pictures viewed in a browser (Google Photos, iCloud.com, WhatsApp Web) can remain in the browser cache - tick the browser option in 'Search this computer'", "do": "auto", "auto": True},
         {"t": "Other cloud services the phone may have uploaded to without you remembering: Google Drive, Dropbox, OneDrive camera upload, Amazon Photos, Samsung/Xiaomi/Huawei cloud", "do": "manual"},
         {"t": "Other people: whoever you sent it to, shared albums, partner sharing, family group chats - their copy is unaffected by your deletion", "do": "manual"},
         {"t": "Places you posted or uploaded it: social media, messaging profile pictures, print/photo-book services, marketplace listings", "do": "manual"},
         {"t": "Provider support, only if very recent: Google Drive has a file-recovery request for consumer accounts that sometimes works shortly after a permanent deletion. Ask at once; do not count on it", "do": "manual"},
     ],
     "limits": "If none of these holds a copy, the picture no longer exists anywhere and cannot be recovered by any software or service. "
               "Paid 'recovery' apps that claim to restore permanently deleted photos from a modern phone's internal storage only find the same thumbnails and caches."},
    {"id": "card", "name": "Memory card / USB drive / camera", "chance": "high",
     "how": "FAT32/exFAT. Delete = mark the directory entry and free the clusters. Content untouched, no TRIM on most cards.",
     "routes": [
         {"t": "Read deleted file records (names, dates, exact layout)", "do": "disk", "auto": True},
         {"t": "Rebuild files from unallocated space, including ones fragmented around existing files", "do": "disk", "auto": True},
         {"t": "After a quick format: the file table is new but the old content is still there - raw carving finds it", "do": "disk", "auto": True},
     ],
     "limits": "Anything overwritten by newer photos is gone. Stop using the card immediately."},
    {"id": "pc", "name": "Windows PC / external hard drive", "chance": "high on hard disks, low on SSDs",
     "how": "NTFS. Delete -> Recycle Bin (just a rename). Emptying the bin clears an 'in use' bit; the file record keeps name, size and the map of where every fragment lies.",
     "routes": [
         {"t": "Recycle Bin contents, with original names and deletion dates", "do": "folder", "auto": True},
         {"t": "Deleted MFT records - exact rebuild, even after the bin was emptied", "do": "disk", "auto": True},
         {"t": "Unallocated space carving", "do": "disk", "auto": True},
         {"t": "Thumbnail cache (thumbcache_*.db) keeps a small copy of every picture Explorer ever displayed - often for years", "do": "folder", "auto": True},
         {"t": "File History / Previous Versions / OneDrive sync folder / old backups", "do": "evidence", "auto": True},
     ],
     "limits": "Internal SSDs run TRIM: freed blocks are usually erased within minutes. The indirect routes (thumbnail cache, backups) still work."},
    {"id": "android", "name": "Android phone (internal storage)", "chance": "medium - through copies, not the original",
     "how": "Internal flash is encrypted per file and TRIMmed. Once a file leaves the trash, the original cannot be read back by any app, rooted or not.",
     "routes": [
         {"t": "System trash: files renamed .trashed-<time>-name stay 30 days (Android 11+)", "do": "folder", "auto": True},
         {"t": "Gallery app recycle folders (Samsung, Xiaomi, Oppo ...: up to 30 days)", "do": "folder", "auto": True},
         {"t": "Thumbnail stores: DCIM/.thumbnails and .thumbdata files keep small copies long after the photo is gone", "do": "folder", "auto": True},
         {"t": "Copies inside apps: WhatsApp/Telegram media folders, 'Sent' folders, app caches", "do": "folder", "auto": True},
         {"t": "The SD card, if photos were saved there (full recovery possible)", "do": "disk", "auto": True},
         {"t": "Cloud backup of the gallery (Google Photos, OneDrive camera upload, Samsung Cloud)", "do": "manual"},
     ],
     "limits": "Copy the phone's storage to the computer (USB file transfer, or 'adb pull /sdcard') and scan that folder. Thumbnail copies are low resolution."},
    {"id": "iphone", "name": "iPhone / iPad", "chance": "medium - through backups",
     "how": "Every file has its own encryption key; deleting destroys the key. No raw recovery is possible.",
     "routes": [
         {"t": "Photos > Albums > Recently Deleted: 30 days (also on iCloud.com)", "do": "manual"},
         {"t": "Old Finder/iTunes backups on a computer: contain the camera roll as it was, plus thumbnails", "do": "evidence", "auto": True},
         {"t": "iCloud.com > Account Settings > Data Recovery, and older iCloud backups", "do": "manual"},
         {"t": "Copies sent through chats, AirDrop, email, shared albums", "do": "manual"},
     ],
     "limits": "Encrypted computer backups must be decrypted with their password first."},
    {"id": "gphotos", "name": "Google Photos", "chance": "high within 60 days, otherwise only via copies",
     "how": "Delete = move to Trash for 60 days (30 if the item was never backed up), then purged from Google's servers.",
     "routes": [
         {"t": "photos.google.com > Trash > Restore. (Google closed third-party access to the library in March 2025, so no app can do this step for you.)", "do": "manual"},
         {"t": "Also check Archive and Locked Folder - 'missing' photos are often there", "do": "manual"},
         {"t": "A Google Takeout export made at any time in the past: scan it as evidence", "do": "evidence", "auto": True},
         {"t": "The phone that took the photo: device copy, trash, thumbnails", "do": "folder", "auto": True},
         {"t": "Google Drive trash, if the photo was also in Drive", "do": "cloud:gdrive", "auto": True},
         {"t": "Shared albums / partner sharing: the other person's library keeps its copy", "do": "manual"},
     ],
     "limits": "After the trash is emptied, Google has no self-service way to bring items back; only copies elsewhere remain."},
    {"id": "gdrive", "name": "Google Drive", "chance": "high within 30 days",
     "how": "Delete = trashed flag; purged after 30 days.",
     "routes": [
         {"t": "Connect the account: list the trash, preview, download or put back in place", "do": "cloud:gdrive", "auto": True},
         {"t": "Workspace accounts: an admin can restore for 25 more days after the trash is emptied", "do": "manual"},
         {"t": "Drive for desktop keeps a local cache and the PC Recycle Bin gets synced deletions", "do": "folder", "auto": True},
     ], "limits": ""},
    {"id": "onedrive", "name": "OneDrive", "chance": "high within 30 days (93 for work/school)",
     "how": "Delete = recycle bin for 30 days (personal) / 93 days (work, incl. second-stage bin).",
     "routes": [
         {"t": "onedrive.com > Recycle bin > Restore. (Microsoft offers no API to list a personal recycle bin, so this step is manual.)", "do": "manual"},
         {"t": "Microsoft 365: Settings > 'Restore your OneDrive' rewinds the whole drive to any point in the last 30 days", "do": "manual"},
         {"t": "Deleting online also sends the synced copy to the PC's Recycle Bin - which lives much longer", "do": "folder", "auto": True},
         {"t": "PC thumbnail cache and deleted file records of the sync folder", "do": "disk", "auto": True},
         {"t": "Personal Vault and 'Photos' on-this-day albums are not affected by gallery deletions", "do": "manual"},
     ], "limits": ""},
    {"id": "dropbox", "name": "Dropbox", "chance": "high within 30-365 days depending on plan",
     "how": "Delete = the file's latest revision is marked deleted; all revisions are kept 30 days (Basic/Plus), 180 days (Professional/Business) or 365 days (Advanced/Enterprise).",
     "routes": [
         {"t": "Connect the account: list deleted files, download the last revision or restore in place", "do": "cloud:dropbox", "auto": True},
         {"t": "Local Dropbox folder: .dropbox.cache holds recently deleted files for a few days", "do": "folder", "auto": True},
     ], "limits": ""},
    {"id": "whatsapp", "name": "WhatsApp", "chance": "good if media was downloaded or backed up",
     "how": "WhatsApp's servers drop media after delivery; there is no account-side copy to fetch. Everything is on devices and in your backup.",
     "routes": [
         {"t": "Media folder (Android/media/com.whatsapp/WhatsApp/Media): deleting a chat or message often leaves the file; 'Sent' subfolders keep what you sent", "do": "folder", "auto": True},
         {"t": ".Statuses and cache folders, plus gallery thumbnails of WhatsApp images", "do": "folder", "auto": True},
         {"t": "Reinstall and restore the Google Drive / iCloud chat backup - if the backup is older than the deletion, the media returns", "do": "manual"},
         {"t": "iPhone: WhatsApp media inside an old computer backup", "do": "evidence", "auto": True},
         {"t": "WhatsApp Web/Desktop cache on a computer", "do": "folder", "auto": True},
         {"t": "Ask the other person in the chat - their copy is untouched unless 'delete for everyone' ran before they downloaded it", "do": "manual"},
     ],
     "limits": "Check the backup date BEFORE restoring: a backup made after the deletion does not contain the file, and a new backup overwrites the old one."},
    {"id": "telegram", "name": "Telegram", "chance": "low for deleted cloud chats, good for cached media",
     "how": "Cloud chats live on Telegram's servers; deleting a message removes it there for good. Secret chats exist only on the devices.",
     "routes": [
         {"t": "Local cache: Telegram/Telegram Images|Video, Android/data/org.telegram.messenger/cache, Telegram Desktop 'tdata'", "do": "folder", "auto": True},
         {"t": "Media you saved to the gallery, and its thumbnails", "do": "folder", "auto": True},
         {"t": "A Telegram Desktop 'Export chat history' made earlier", "do": "evidence", "auto": True},
         {"t": "Groups/channels: admins can see recently deleted messages for 48 hours in Recent Actions", "do": "manual"},
         {"t": "Saved Messages and the other participant's copy (if you deleted only for yourself)", "do": "manual"},
     ], "limits": ""},
    {"id": "snapchat", "name": "Snapchat", "chance": "good for Memories, low for opened snaps",
     "how": "Snaps are deleted from Snap's servers once opened by all recipients (unopened: after ~30 days). Memories are stored until you delete them.",
     "routes": [
         {"t": "Memories, including 'My Eyes Only' - open Snapchat and swipe up from the camera", "do": "manual"},
         {"t": "accounts.snapchat.com > My Data: request your data export (contains Memories and chat media Snap still holds), then scan the export", "do": "evidence", "auto": True},
         {"t": "Phone storage: Android/data/com.snapchat.android cache and the 'Snapchat' gallery folder", "do": "folder", "auto": True},
         {"t": "Chat media saved in chat by either person stays until unsaved", "do": "manual"},
     ],
     "limits": "A snap that was opened and never saved leaves no server copy. Only a device cache remnant can exist."},
    {"id": "terabox", "name": "TeraBox", "chance": "high while it is in TeraBox's Recycle Bin, otherwise only via copies",
     "how": "Delete = moved to TeraBox's Recycle Bin, then erased when its timer runs out (reported as about 10 days on free "
            "accounts and 30 on Premium [Unverified: third-party guides, not TeraBox]). TeraBox offers outside apps no official "
            "access to the Recycle Bin.",
     "routes": [
         {"t": "TeraBox app or terabox.com > Recycle Bin: select the photos and videos > Restore. Do this first: it is the only "
               "way back for anything still in the bin", "do": "manual"},
         {"t": "After restoring, download the folder from TeraBox (or let it sync) and give it to Rewind, which sorts deleted "
               "from still here", "do": "evidence", "auto": True},
         {"t": "Phone that uploaded to TeraBox: its camera folder, gallery trash and thumbnails often still hold the original",
          "do": "folder", "auto": True},
         {"t": "Files you shared from TeraBox: whoever received the share link may still have downloaded copies", "do": "manual"},
     ],
     "limits": "Once TeraBox empties its Recycle Bin, the files are gone from TeraBox's servers; only copies elsewhere remain."},
    {"id": "icloud", "name": "iCloud Photos", "chance": "high within 30 days",
     "how": "Delete = Recently Deleted for 30 days on every synced device, then purged.",
     "routes": [
         {"t": "icloud.com/photos > Recently Deleted > Recover", "do": "manual"},
         {"t": "A Mac's Photos library package (originals/derivatives) and its Time Machine backups", "do": "evidence", "auto": True},
         {"t": "Old computer backups of the iPhone", "do": "evidence", "auto": True},
     ], "limits": ""},
]


ALIASES = {
    "photos": "gphotos", "google photos": "gphotos", "google drive": "gdrive", "drive": "gdrive",
    "one drive": "onedrive", "sd": "card", "sd card": "card", "memory card": "card", "usb": "card",
    "camera": "card", "mac": "pc", "windows": "pc", "laptop": "pc", "computer": "pc", "hard drive": "pc",
    "ios": "iphone", "ipad": "iphone", "apple": "icloud", "icloud photos": "icloud", "wa": "whatsapp",
    "snap": "snapchat", "tg": "telegram", "permanent": "gone", "permanently deleted": "gone",
    "not in bin": "gone", "everything": "gone", "realme": "android", "samsung": "android",
    "instagram": "gone", "facebook": "gone", "tera box": "terabox", "terabox cloud": "terabox",
}


def routes_for(service=None):
    """Routes for one service (fuzzy name), or a short index of all of them."""
    if not service:
        return {"principles": [{"title": t, "text": x} for t, x in PRINCIPLES],
                "services": [{"id": m["id"], "name": m["name"], "chance": m["chance"]} for m in MAP],
                "note": "Ask again with a service id for its full list of routes."}
    s = service.strip().lower()
    sid = ALIASES.get(s, s)
    hit = next((m for m in MAP if m["id"] == sid), None) or \
        next((m for m in MAP if sid in m["name"].lower() or sid in m["id"]), None)
    if not hit:
        hit = next(m for m in MAP if m["id"] == "gone")
    out = dict(hit)
    out["routes"] = [dict(r, who="Rewind does this" if r.get("auto") else "Only you, in the service's own app/site")
                     for r in hit["routes"]]
    return out
