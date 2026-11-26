"""This computer (Mac or Windows): every place a deleted picture can linger.

* Trash / Recycle Bin. Windows keeps the original path and deletion time in
  a small $I file next to each $R file; we read both.
* Mac Photos library: items in Recently Deleted (30 days) are flagged in
  Photos.sqlite and their originals are still inside the library package.
* Thumbnail caches: Windows Explorer's thumbcache_*.db and macOS QuickLook
  keep previews of pictures you once looked at, long after the picture is
  deleted. Small, but often the only copy left.
* Messaging apps on the desktop: iMessage attachments, WhatsApp Desktop,
  Telegram Desktop / Telegram for macOS media folders.
* Downloads, and the browser's own cache (pictures you viewed on the web
  versions of Instagram, WhatsApp Web, Google Photos...).
"""
import glob
import os
import sqlite3
import struct
import subprocess
import sys
import zlib
import datetime as dt

from . import photos_db
from .base import walk

INFO = {
    "id": "computer",
    "title": "This Mac / PC",
    "group": "Devices",
    "blurb": "Trash, Photos' Recently Deleted, hidden thumbnail caches, iMessage, WhatsApp and Telegram "
             "desktop folders, Downloads and browser caches on this computer.",
    "fields": [
        {"name": "locations", "label": "Places to search", "type": "multi", "discover": "computer"},
    ],
}

H = os.path.expanduser("~")


def candidates():
    """List of (id, label, path, kind) that exist on this machine."""
    c = []
    if sys.platform == "darwin":
        c += [
            ("mac-trash", "Trash", H + "/.Trash", "trash"),
            ("mac-photos", "Photos library (incl. Recently Deleted)", H + "/Pictures/Photos Library.photoslibrary", "photos"),
            ("mac-pictures", "Pictures folder", H + "/Pictures", "library"),
            ("mac-desktop", "Desktop", H + "/Desktop", "library"),
            ("mac-downloads", "Downloads", H + "/Downloads", "library"),
            ("imessage", "iMessage attachments", H + "/Library/Messages/Attachments", "library"),
            ("wa-mac", "WhatsApp for Mac media", H + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message/Media", "library"),
            ("tg-mac", "Telegram for Mac cache", H + "/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram", "cache"),
            ("tg-desktop", "Telegram Desktop downloads", H + "/Downloads/Telegram Desktop", "library"),
            ("chrome-cache", "Chrome cache", H + "/Library/Caches/Google/Chrome", "cache"),
            ("safari-cache", "Safari cache", H + "/Library/Caches/com.apple.Safari", "cache"),
        ]
        try:
            cache_dir = subprocess.run(["getconf", "DARWIN_USER_CACHE_DIR"], capture_output=True,
                                       text=True, timeout=10).stdout.strip()
            c.append(("quicklook", "QuickLook thumbnail cache",
                      os.path.join(cache_dir, "com.apple.QuickLook.thumbnailcache"), "quicklook"))
        except Exception:
            pass
    elif os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        up = os.environ.get("USERPROFILE", H)
        for drive in "CDEFGH":
            rb = "%s:\\$Recycle.Bin" % drive
            if os.path.isdir(rb):
                c.append(("recycle-" + drive, "Recycle Bin (%s:)" % drive, rb, "recycle"))
        c += [
            ("thumbcache", "Windows thumbnail cache", la + "\\Microsoft\\Windows\\Explorer", "thumbcache"),
            ("win-pictures", "Pictures", up + "\\Pictures", "library"),
            ("win-onedrive", "OneDrive folder", up + "\\OneDrive", "library"),
            ("win-downloads", "Downloads", up + "\\Downloads", "library"),
            ("wa-win", "WhatsApp Desktop", la + "\\Packages\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\\LocalState", "cache"),
            ("tg-win", "Telegram Desktop downloads", up + "\\Downloads\\Telegram Desktop", "library"),
            ("chrome-cache", "Chrome cache", la + "\\Google\\Chrome\\User Data\\Default\\Cache", "cache"),
            ("edge-cache", "Edge cache", la + "\\Microsoft\\Edge\\User Data\\Default\\Cache", "cache"),
        ]
    else:
        c += [("linux-trash", "Trash", H + "/.local/share/Trash", "trash"),
              ("linux-pictures", "Pictures", H + "/Pictures", "library"),
              ("linux-thumbs", "Thumbnail cache", H + "/.cache/thumbnails", "cache")]
    return [x for x in c if os.path.exists(x[2])]


def run(ctx, params):
    wanted = set(params.get("locations") or [])
    from . import folder
    for cid, label, path, kind in candidates():
        if wanted and cid not in wanted:
            continue
        if ctx.stopped():
            return
        ctx.log("Searching %s" % label)
        try:
            if kind == "photos":
                mac_photos_library(ctx, path)
            elif kind == "recycle":
                windows_recycle_bin(ctx, path)
            elif kind == "thumbcache":
                for db in glob.glob(os.path.join(path, "thumbcache_*.db")):
                    windows_thumbcache(ctx, db)
            elif kind == "quicklook":
                quicklook_cache(ctx, path)
            elif kind == "trash":
                folder.run(ctx, {"path": path, "role": "search", "deep": False})
            else:
                folder.run(ctx, {"path": path, "role": "library" if kind == "library" else "search",
                                 "deep": kind == "cache"})
        except PermissionError:
            ctx.log("macOS blocked %s. Give Terminal 'Full Disk Access' (see the guide) and scan again." % label)
        except Exception as e:
            ctx.log("Skipped %s: %s" % (label, e))
    # anything found inside a Trash is deleted by definition
    ctx.cat.con().execute(
        "UPDATE items SET hint='trash', evidence='In the Trash' WHERE scan_id=? AND "
        "(location LIKE '%/.Trash/%' OR location LIKE '%/.local/share/Trash/%')", (ctx.scan_id,))
    ctx.cat.con().commit()


# ----------------------------------------------------------------- Mac Photos

def mac_photos_library(ctx, lib, role="library"):
    db = os.path.join(lib, "database", "Photos.sqlite")
    if not os.path.exists(db):
        raise ValueError("No Photos database inside the library")
    assets = photos_db.read_assets(db)
    trashed = [a for a in assets if a["trashed"]]
    ctx.log("Photos library: %d items, %d in Recently Deleted." % (len(assets), len(trashed)))
    for a in assets:
        orig = os.path.join(lib, "originals", a["directory"], a["filename"])
        loc = "Photos library › " + ("Recently Deleted › " if a["trashed"] else "") + a["filename"]
        if os.path.exists(orig):
            ctx.ingest_file(orig, "trash" if a["trashed"] else ("present" if role == "library" else "backup"),
                            location=loc, name=a["filename"],
                            taken=a["created"], deleted_at=a["trashed_at"],
                            evidence="In Recently Deleted (restorable for ~30 days)" if a["trashed"]
                            else ("In your Photos library" if role == "library" else "In an older copy of your Photos library"))
        elif a["trashed"]:
            ctx.add_missing(a["filename"], loc, "In Recently Deleted, but the original is only in iCloud. "
                            "Open Photos › Recently Deleted to recover it.", kind=a["kind"],
                            taken=a["created"], deleted_at=a["trashed_at"])
    deriv = os.path.join(lib, "resources", "derivatives")
    if os.path.isdir(deriv):
        for p in walk(deriv, ctx):
            ctx.ingest_file(p, "thumbnail", location="Photos library preview › " + os.path.basename(p),
                            evidence="Preview Photos generated for this picture")


# --------------------------------------------------------- Windows Recycle Bin

def _filetime(v):
    try:
        return (dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=v // 10)).isoformat(timespec="seconds")
    except Exception:
        return None


def parse_recycle_info(data):
    """Parse a $I file -> (original_path, size, deleted_at)."""
    ver, size, ft = struct.unpack("<QQQ", data[:24])
    if ver == 1:
        name = data[24:24 + 520].decode("utf-16-le", "ignore").split("\x00")[0]
    else:
        n = struct.unpack("<I", data[24:28])[0]
        name = data[28:28 + 2 * n].decode("utf-16-le", "ignore").split("\x00")[0]
    return name, size, _filetime(ft)


def windows_recycle_bin(ctx, root):
    for info in glob.glob(os.path.join(root, "*", "$I*")):
        try:
            with open(info, "rb") as f:
                orig, _size, when = parse_recycle_info(f.read())
        except Exception:
            continue
        rfile = os.path.join(os.path.dirname(info), "$R" + os.path.basename(info)[2:])
        if os.path.isdir(rfile):
            for p in walk(rfile, ctx):
                ctx.ingest_file(p, "trash", location="Recycle Bin › " + orig, deleted_at=when,
                                evidence="In the Recycle Bin (deleted %s)" % (when or "?"))
        elif os.path.exists(rfile):
            ctx.ingest_file(rfile, "trash", location="Recycle Bin › " + orig, name=os.path.basename(orig),
                            deleted_at=when, evidence="In the Recycle Bin (deleted %s)" % (when or "?"))


# ------------------------------------------------------ Windows thumbcache

def windows_thumbcache(ctx, path):
    """Walk thumbcache_*.db entries (Vista..Win11) and keep each picture."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"CMMM":
        return 0
    ver = struct.unpack("<I", d[4:8])[0]
    first = struct.unpack("<I", d[12:16] if ver in (20, 21) else d[16:20])[0]
    pos = first if 0 < first < len(d) else 24
    n = 0
    while pos + 56 < len(d):
        if d[pos:pos + 4] != b"CMMM":
            nxt = d.find(b"CMMM", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        esize = struct.unpack("<I", d[pos + 4:pos + 8])[0]
        if esize < 24 or pos + esize > len(d):
            break
        o = pos + 16 + (8 if ver == 20 else 0)
        idlen, padlen, dlen = struct.unpack("<III", d[o:o + 12])
        o += 12 + (8 if ver >= 30 else 0) + 4 + 16
        data = d[o + idlen + padlen:o + idlen + padlen + dlen]
        ident = d[o:o + idlen].decode("utf-16-le", "ignore")
        ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png" if data[:4] == b"\x89PNG" else (
            ".bmp" if data[:2] == b"BM" else None)
        if ext and dlen > 512:
            if ctx.ingest_bytes(data, ext, "thumbnail", "Windows thumbnail cache %s › %s" % (
                    os.path.basename(path), ident), name="thumb_" + ident[:16],
                    evidence="Preview Windows Explorer cached; it stays after the picture is deleted",
                    subdir="thumbcache"):
                n += 1
        pos += esize
    if n == 0:  # unknown layout: fall back to raw carving
        n = ctx.carve_file(path, "Windows thumbnail cache", hint="thumbnail", min_size=512)
    ctx.log("%s: %d previews" % (os.path.basename(path), n))
    return n


# ----------------------------------------------------------- macOS QuickLook

def _png_rgba(w, h, rgba, bpr):
    raw = b"".join(b"\x00" + rgba[y * bpr:y * bpr + w * 4] for y in range(h))

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def quicklook_cache(ctx, root):
    idx = os.path.join(root, "index.sqlite")
    blob = os.path.join(root, "thumbnails.data")
    if not (os.path.exists(idx) and os.path.exists(blob)):
        ctx.log("QuickLook cache layout not found on this macOS version.")
        return
    con = sqlite3.connect("file:%s?mode=ro" % idx, uri=True)
    try:
        rows = con.execute(
            "SELECT f.folder, f.file_name, t.width, t.height, t.bytesperrow, t.bitmapdata_location, "
            "t.bitmapdata_length FROM thumbnails t JOIN files f ON f.rowid = t.file_id").fetchall()
    except Exception as e:
        ctx.log("QuickLook cache not readable: %s" % e)
        return
    finally:
        con.close()
    with open(blob, "rb") as f:
        for folder_, name, w, h, bpr, loc, ln in rows:
            ext = os.path.splitext(name or "")[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".heic", ".gif", ".mov", ".mp4", ".tif", ".webp"):
                continue
            original = os.path.join(folder_ or "", name or "")
            if original and os.path.exists(original):
                continue  # picture still exists, nothing to rescue
            f.seek(loc)
            data = f.read(ln)
            if not w or not h or len(data) < w * 4 * h:
                continue
            png = _png_rgba(w, h, data, bpr or w * 4)
            ctx.ingest_bytes(png, ".png", "thumbnail", "QuickLook preview of %s" % original,
                             name=name, evidence="macOS kept this preview after the original was deleted",
                             subdir="quicklook")
