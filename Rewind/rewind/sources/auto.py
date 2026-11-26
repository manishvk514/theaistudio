"""Scan everything: the one-button mode.

On open, Rewind works out every place on this computer where a photo or video
(or a trace of one) can live, scans each in the right way, and then sorts
everything into "Deleted" and "Still here". Order matters: your current
libraries are read first, so everything found afterwards can be checked
against them.

Places, and why each one is on the list:
  Current library  Photos library, Pictures, Desktop, Documents, Downloads,
                   iMessage, WhatsApp media. What is here is "still here".
  Bins             The Trash and Photos' Recently Deleted album keep deleted
                   files whole for about 30 days.
  Caches           QuickLook, Telegram, WhatsApp, browser caches keep copies
                   or thumbnails long after the original is gone.
  Phone backups    iPhone backups made by Finder contain the camera roll and
                   app databases as they were on the backup day.
  Time Machine     Old snapshots keep photos that were deleted later.
  Drives & cards   External drives and memory cards plugged in now.
  Downloads        Account exports (Google Takeout, Snapchat My Data,
                   Instagram/Facebook, WhatsApp chats, Telegram exports,
                   SMS backups) are picked up and opened automatically.
  Accounts         Google Drive, OneDrive and Telegram, once connected.
"""
import glob
import os
import plistlib
import re
import subprocess
import sys

from . import (folder, computer, ios_backup, exports, takeout, snapchat, whatsapp, telegram,
               android_adb, cloud, connect, telegram_cloud)

INFO = {
    "id": "auto",
    "title": "Everything",
    "group": "Auto",
    "blurb": "Finds and scans every place on this computer, your phone backups, drives and connected accounts.",
    "fields": [],
}

H = os.path.expanduser("~")

EXPORT_RULES = [  # (regex on lower-case name, kind)
    (r"^takeout.*\.(zip|tgz|tar\.gz)$|^takeout$", "takeout"),
    (r"^mydata.*\.zip$|^mydata~|snapchat.*\.zip$", "snapchat"),
    (r"^whatsapp chat.*\.zip$", "whatsapp"),
    (r"^(dataexport|chatexport)_", "telegram"),
    (r"^(instagram|facebook|meta|messenger|tiktok|twitter|x-archive|icloud|samsung|xiaomi)[-_ ].*\.zip$",
     "data-download"),
    (r"^sms-.*\.xml$", "mms"),
]


def full_disk_access():
    """True/False on a Mac, None elsewhere. macOS only lets an app read the
    Photos database, Messages and other apps' folders after you switch on
    Full Disk Access for it once."""
    if os.environ.get("REWIND_TEST_FDA") in ("0", "1"):
        return os.environ["REWIND_TEST_FDA"] == "1"
    if sys.platform != "darwin":
        return None
    probe = os.path.join(H, "Library", "Application Support", "com.apple.TCC", "TCC.db")
    try:
        with open(probe, "rb") as f:
            f.read(16)
        return True
    except PermissionError:
        return False
    except OSError:
        return None


def find_exports(roots=None, depth=2):
    roots = roots or [os.path.join(H, d) for d in ("Downloads", "Desktop", "Documents")]
    roots.append(os.path.join(H, "Downloads", "Telegram Desktop"))
    found, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base_depth >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith(".") and not x.endswith((".app", ".photoslibrary"))]
            for name in list(dirs) + files:
                low = name.lower()
                for rx, kind in EXPORT_RULES:
                    if re.search(rx, low):
                        p = os.path.join(d, name)
                        if p not in seen:
                            seen.add(p)
                            found.append((kind, p))
                            if name in dirs:
                                dirs.remove(name)
                        break
    return found


def external_volumes():
    out = []
    if not os.path.isdir("/Volumes"):
        return out
    for v in sorted(os.listdir("/Volumes")):
        p = os.path.join("/Volumes", v)
        try:
            if os.path.realpath(p) == "/" or not os.path.isdir(p):
                continue
        except OSError:
            continue
        if os.path.isdir(os.path.join(p, "Backups.backupdb")) or v.startswith(("com.apple", ".")):
            continue
        out.append(p)
    return out


def device_of(volume):
    """Raw device and size behind a mounted external volume (Mac)."""
    try:
        r = subprocess.run(["diskutil", "info", "-plist", volume], capture_output=True, timeout=30)
        info = plistlib.loads(r.stdout)
    except Exception:
        return None
    if info.get("Internal", True) and not info.get("RemovableMedia"):
        return None
    node = info.get("DeviceNode")
    if not node:
        return None
    return {"node": node, "raw": node.replace("/dev/disk", "/dev/rdisk"),
            "size": info.get("TotalSize") or info.get("Size") or 0, "fs": info.get("FilesystemName", "")}


def plan(cat=None):
    """Every place the automatic scan will visit, in order."""
    places = []

    def add(pid, label, kind, path=None, note=None, **extra):
        places.append(dict(id=pid, label=label, kind=kind, path=path, note=note, status="waiting",
                           found=0, **extra))

    if os.environ.get("REWIND_CLOUD") == "1":
        cands = {}          # on Hugging Face there is no computer to search: accounts only
    else:
        cands = {c[0]: c for c in computer.candidates()}
    # 1. current library first
    for cid in ("mac-photos", "mac-pictures", "mac-desktop", "mac-downloads", "imessage", "wa-mac",
                "tg-desktop", "win-pictures", "win-onedrive", "win-downloads", "tg-win", "linux-pictures"):
        if cid in cands:
            _, label, path, kind = cands[cid]
            add(cid, label, "photos" if kind == "photos" else "library", path)
    docs = os.path.join(H, "Documents")
    if os.path.isdir(docs) and os.environ.get("REWIND_CLOUD") != "1":
        add("documents", "Documents", "library", docs)
    # 2. bins and caches
    for cid, (_, label, path, kind) in cands.items():
        if any(p["id"] == cid for p in places):
            continue
        add(cid, label, kind, path)
    local = os.environ.get("REWIND_CLOUD") != "1"
    # 3. phone backups
    for b in (ios_backup.default_backup_dirs() if local else []):
        d = ios_backup.describe(b)
        add("ios:" + os.path.basename(b), "iPhone backup: %s%s" % (d.get("device") or "iPhone",
                                                                  (" (%s)" % d["date"][:10]) if d.get("date") else ""),
            "ios", b, note="Encrypted: needs its backup password (Advanced)" if d.get("encrypted") else None,
            encrypted=bool(d.get("encrypted")))
    for root in (exports.time_machine_roots() if local else []):
        add("tm:" + root, "Time Machine: %s" % root.split("/")[2] if root.startswith("/Volumes/") else root,
            "timemachine", root)
    # 4. drives and cards
    for v in (external_volumes() if local else []):
        add("vol:" + v, "Drive or card: %s" % os.path.basename(v), "volume", v)
    devs = android_adb.devices() if local else None
    for d in devs or []:
        add("adb:" + d, "Android phone on USB: %s" % d, "adb", d)
    # 5. exports waiting in Downloads / Desktop / Documents
    for kind, p in (find_exports() if local else []):
        add("export:" + p, "%s: %s" % (EXPORT_TITLES.get(kind, "Export"), os.path.basename(p)), "export:" + kind, p)
    # 6. connected accounts
    if cat is not None:
        conn = connect.connected(cat)
        if conn.get("gdrive", {}).get("state") == "connected":
            add("cloud:gdrive", "Google Drive (Trash and what's still there)", "gdrive")
        if conn.get("onedrive", {}).get("state") == "connected":
            add("cloud:onedrive", "OneDrive (deleted items and what's still there)", "onedrive")
        if conn.get("dropbox", {}).get("state") == "connected":
            add("cloud:dropbox", "Dropbox (deleted files it still keeps, and what's still there)", "dropbox")
        if telegram_cloud.get(cat).state == "connected":
            add("cloud:telegram", "Telegram chats (photos and videos still on Telegram)", "telegram-cloud")
    return places


EXPORT_TITLES = {"takeout": "Google Takeout", "snapchat": "Snapchat My Data", "whatsapp": "WhatsApp chat export",
                 "telegram": "Telegram export", "data-download": "Account download", "mms": "Text message backup"}

SOURCE_OF_KIND = {"photos": "computer", "library": "computer", "trash": "computer", "cache": "computer",
                  "quicklook": "computer", "recycle": "computer", "thumbcache": "computer", "ios": "ios-backup",
                  "timemachine": "mac-backups", "volume": "folder", "adb": "android-adb", "gdrive": "google-drive",
                  "onedrive": "onedrive", "dropbox": "dropbox", "telegram-cloud": "telegram-cloud", "export:takeout": "google-takeout",
                  "export:snapchat": "snapchat", "export:whatsapp": "whatsapp", "export:telegram": "telegram",
                  "export:data-download": "data-download", "export:mms": "mms"}


def scan_place(ctx, p):
    kind, path = p["kind"], p.get("path")
    if kind == "photos":
        computer.mac_photos_library(ctx, path)
    elif kind == "library":
        folder.run(ctx, {"path": path, "role": "library", "deep": False})
    elif kind == "trash":
        folder.run(ctx, {"path": path, "role": "search", "deep": False})
        con = ctx.cat.con()
        con.execute("UPDATE items SET hint='trash', evidence='In the Trash' WHERE location LIKE ? "
                    "AND hint NOT IN ('present')", (path.rstrip("/") + "/%",))
        con.commit()
    elif kind == "cache":
        folder.run(ctx, {"path": path, "role": "search", "deep": True})
    elif kind == "quicklook":
        computer.quicklook_cache(ctx, path)
    elif kind == "recycle":
        computer.windows_recycle_bin(ctx, path)
    elif kind == "thumbcache":
        for db in glob.glob(os.path.join(path, "thumbcache_*.db")):
            computer.windows_thumbcache(ctx, db)
    elif kind == "ios":
        if p.get("encrypted"):
            raise Skip("This backup is encrypted. Open Advanced › iPhone backup and type its password.")
        ios_backup.run(ctx, {"path": path, "role": "search"})
    elif kind == "timemachine":
        exports.run_mac_backups(ctx, {"path": path})
    elif kind == "volume":
        # what is on the card now counts as "still here"; trash folders on it are deleted
        folder.run(ctx, {"path": path, "role": "library", "deep": True})
    elif kind == "adb":
        android_adb.run(ctx, {})
    elif kind == "gdrive":
        cloud.scan_gdrive(ctx, connect.access_token(ctx.cat, "gdrive"), True, via="connect")
    elif kind == "onedrive":
        cloud.scan_onedrive(ctx, connect.access_token(ctx.cat, "onedrive"),
                            {"service": "onedrive", "via": "connect"})
    elif kind == "dropbox":
        cloud.scan_dropbox(ctx, connect.access_token(ctx.cat, "dropbox"))
    elif kind == "telegram-cloud":
        telegram_cloud.get(ctx.cat).scan(ctx)
    elif kind == "export:takeout":
        takeout.run(ctx, {"path": path, "role": "search"})
    elif kind == "export:snapchat":
        snapchat.run(ctx, {"path": path, "download": True})
    elif kind == "export:whatsapp":
        whatsapp.run(ctx, {"path": path, "role": "search"})
    elif kind == "export:telegram":
        telegram.run(ctx, {"path": path})
    elif kind == "export:data-download":
        exports.run_export(ctx, {"path": path})
    elif kind == "export:mms":
        exports.run_mms(ctx, {"path": path})


class Skip(Exception):
    pass


ONCE_KINDS = ("export:", "ios", "timemachine")   # big, rarely change: reopen only when they change


def signature(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = [int(st.st_mtime), st.st_size]
    if os.path.isdir(path):
        for name in ("Manifest.db", "Info.plist"):
            q = os.path.join(path, name)
            if os.path.exists(q):
                sig.append(int(os.stat(q).st_mtime))
    return sig


def run(ctx, params):
    places = plan(ctx.cat)
    only = set(params.get("only") or [])
    if only:
        places = [p for p in places if p["id"] in only]
    ctx.job.places = places
    fda = full_disk_access()
    total_before = ctx.found
    done_before = ctx.cat.kv_get("auto_done") or {}
    for i, p in enumerate(places):
        if ctx.stopped():
            p["status"] = "skipped"
            continue
        p["status"] = "scanning"
        ctx.progress(i, len(places), p["label"])
        ctx.source = SOURCE_OF_KIND.get(p["kind"], "auto")
        ctx.seen_paths = set()
        before = ctx.found
        sig = signature(p["path"]) if p.get("path") and p["kind"].startswith(ONCE_KINDS) else None
        if sig and done_before.get(p["id"]) == sig and not params.get("full"):
            p["status"], p["note"] = "done", "Opened on an earlier scan; no changes since."
            continue
        try:
            scan_place(ctx, p)
            p["status"] = "done"
            if sig and not ctx.stopped():
                done_before[p["id"]] = sig
                ctx.cat.kv_set("auto_done", done_before)
        except Skip as e:
            p["status"], p["note"] = "skipped", str(e)
        except PermissionError:
            p["status"] = "blocked"
            p["note"] = ("macOS needs Full Disk Access for Rewind to read this." if fda is False
                         else "No permission to read this.")
        except Exception as e:
            msg = str(e)
            if sys.platform == "darwin" and fda is False and re.search(
                    r"not permitted|unable to open|authori[sz]ation denied|permission", msg, re.I):
                p["status"] = "blocked"
                p["note"] = "macOS needs Full Disk Access for Rewind to read this."
            else:
                p["status"], p["note"] = "error", msg[:200]
            ctx.log("%s: %s" % (p["label"], msg))
        p["found"] = ctx.found - before
        ctx.log("%s: %d item(s)" % (p["label"], p["found"]))
    ctx.progress(len(places), len(places), "Sorting deleted from still here")
    ctx.log("Scanned %d place(s), %d item(s) found." % (len(places), ctx.found - total_before))


# ------------------------------------------------------------ deep card scan

NOT_CARD = ("Deep scan works on external drives and memory cards. A Mac's own disk uses TRIM and "
            "encryption, so files deleted there are erased for good once the Trash is emptied.")

CARD_INFO = {"id": "card-deep", "title": "Deep scan of a memory card", "group": "Devices", "hidden": True,
             "blurb": "Copies the card sector by sector (asks for your Mac password) and rebuilds deleted "
                      "photos and videos from the copy.", "fields": []}


def deep_card(ctx, params):
    import shutil
    import time
    from . import carve_source
    volume = params["path"]
    dev = device_of(volume)
    if not dev:
        raise ValueError(NOT_CARD)
    free = shutil.disk_usage(ctx.cat.workspace).free
    if dev["size"] and dev["size"] > free - 2 * 1024 ** 3:
        raise ValueError("Not enough free space on this Mac to copy the %d GB card." % (dev["size"] // 1024 ** 3))
    img = os.path.join(ctx.cat.dir("images"), "card-%d.img" % int(time.time()))
    user = os.environ.get("USER", "")
    cmd = "/bin/dd if=%s of='%s' bs=4m conv=noerror,sync && /usr/sbin/chown %s '%s'" % (dev["raw"], img, user, img)
    script = ('do shell script "%s" with prompt "Rewind needs your password to read the card sector by sector. '
              'Nothing on the card is changed." with administrator privileges' % cmd.replace('"', '\\"'))
    ctx.log("Copying %s sector by sector (%d GB). macOS will ask for your password." % (
        os.path.basename(volume), dev["size"] // 1024 ** 3))
    ctx.progress(0, 0, "Copying the card")
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(img):
        raise ValueError("The card copy was cancelled or failed: %s" % (r.stderr.strip() or "no details"))
    carve_source.run(ctx, {"path": img, "delete_after": True})
