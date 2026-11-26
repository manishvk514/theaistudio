"""Where deleted pictures leave copies behind.

"Deleted" almost never means "every copy is gone". Before a picture is deleted
it has usually been duplicated by the system without anyone asking: a thumbnail
for the gallery grid, a cached preview in a chat app, a recycle-folder copy, a
nightly backup. This module knows those hiding places and what each one proves.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from .ntfs import parse_recycle_info

# Folder names whose content is, by definition, deleted-but-kept.
TRASH_DIRS = {
    "$recycle.bin", "recycler", "recycled",                    # Windows
    ".trash", ".trashes", "trash",                             # macOS, many Android galleries
    ".trash-1000", ".local/share/trash",                       # Linux (pattern handled below)
    ".globaltrash", ".dustbin", ".recyclebin", ".recycle", "recycle",
    ".trashbin", "#recycle", "@recycle", "@recently-snapshot",  # NAS boxes
    "recently deleted", "recentlydeleted", ".deleted", "deleted items",
    ".gallery2_recyclebin", ".photostrash",
}
# Folder names holding automatic copies (ambiguous: the original may still exist).
CACHE_DIRS = {
    ".thumbnails", ".thumbs", "thumbnails", ".thumbcache", "thumbs", "thumb", ".thumb",
    "cache", ".cache", "caches", "image_cache", "image_manager_disk_cache", "glide", "fresco",
    "picasso-cache", "diskcache", ".statuses", ".shared", "com.apple.quicklook.thumbnailcache",
    "temp", "tmp", ".tmp", "code cache", "previews", "derivatives", "resources/derivatives",
    "photodata", "sent",
}
CACHE_FILES = re.compile(r"(?i)^(\.thumbdata\d*-?-?\d*|thumbcache_.*\.db|iconcache.*\.db|thumbs\.db|ehthumbs\.db|"
                         r"index\.sqlite|imgcache\.\d+|.*\.thumbnails|photo_blob\.\d+)$")

BORING_EXT = {".txt", ".log", ".json", ".xml", ".html", ".htm", ".css", ".js", ".py", ".c", ".h", ".java", ".md",
              ".csv", ".ini", ".cfg", ".plist", ".exe", ".dll", ".so", ".dylib", ".sys", ".apk", ".jar", ".dex",
              ".odex", ".vdex", ".oat", ".art", ".ttf", ".otf", ".woff", ".woff2", ".mp3", ".m4a", ".aac", ".ogg",
              ".opus", ".flac", ".wav", ".amr", ".iso", ".7z", ".rar", ".gz", ".xz", ".bz2", ".zst", ".tar",
              ".zip", ".docx", ".xlsx", ".pptx", ".odt", ".epub", ".crypt12", ".crypt14", ".crypt15", ".enc",
              ".srt", ".vtt", ".lnk", ".url", ".ics", ".vcf"}
SIDECARS = (".supplemental-metadata.json", ".json", ".supplemental-metada.json", ".suppl.json")

APP_HINTS = (
    ("whatsapp", "WhatsApp"), ("telegram", "Telegram"), ("org.telegram", "Telegram"), ("snapchat", "Snapchat"),
    ("com.snap", "Snapchat"), ("instagram", "Instagram"), ("messenger", "Messenger"), ("signal", "Signal"),
    ("viber", "Viber"), ("wechat", "WeChat"), ("tencent/micromsg", "WeChat"), ("dcim", "Camera"),
    ("google takeout", "Google Takeout"), ("takeout/google", "Google Takeout"), ("onedrive", "OneDrive"),
    ("dropbox", "Dropbox"), ("icloud", "iCloud"), ("camerarolldomain", "iPhone camera roll"),
)


def context(rel: str, role: str) -> dict:
    """What does this file's location prove? -> {'origin': ..., 'note': ...}"""
    low = rel.replace("\\", "/").lower()
    parts = low.split("/")
    fname = parts[-1]
    dirs = parts[:-1]
    app = next((label for key, label in APP_HINTS if key in low), "")
    tag = f"{app}: " if app else ""
    out = {}
    m = re.match(r"^\.trashed-(\d{9,11})-(.+)$", fname)
    if m:      # Android 11+ system trash: renamed in place, auto-purged after 30 days
        expiry = int(m.group(1))
        out = {"origin": "trash", "note": tag + "Android system trash (auto-deletes 30 days after removal)",
               "name": rel.replace("\\", "/").split("/")[-1][len(".trashed-") + len(m.group(1)) + 1:],
               "deleted_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(expiry - 30 * 86400))}
    elif any(d in TRASH_DIRS or d.startswith(".trash") or d.startswith("$recycle") for d in dirs) \
            or "/.local/share/trash/" in "/" + low:
        out = {"origin": "trash", "note": tag + "found in a trash/recycle folder"}
    elif fname.startswith(".pending-"):
        out = {"origin": "cache", "note": tag + "unfinished media write (Android .pending file)"}
    elif any(d in CACHE_DIRS for d in dirs) or CACHE_FILES.match(fname):
        out = {"origin": "cache", "note": tag + "automatic copy kept by the system or an app"}
    elif role == "current":
        out = {"origin": "live", "note": ""}
    elif role == "evidence":
        out = {"origin": "backup", "note": tag + "present in this backup/export"}
    else:
        out = {"origin": "live", "note": tag.rstrip(": ")}
    if role == "current" and out["origin"] == "cache":
        out["origin"] = "live"
    return out


def trash_details(full: str, fname: str, ctx: dict) -> dict:
    """Recover original name / path / deletion time recorded next to a trashed file."""
    out = {k: ctx[k] for k in ("name", "deleted_at") if k in ctx}
    if ctx.get("origin") != "trash":
        return out
    folder = os.path.dirname(full)
    try:
        if fname.upper().startswith("$R"):                       # Windows Recycle Bin pair $R / $I
            info = os.path.join(folder, "$I" + fname[2:])
            if os.path.exists(info):
                with open(info, "rb") as fh:
                    orig, when = parse_recycle_info(fh.read(4096))
                if orig:
                    orig = orig.replace("\\", "/")
                    out.update(name=orig.rsplit("/", 1)[-1], path=orig.rsplit("/", 1)[0], deleted_at=when)
        elif os.path.basename(folder) == "files":                # freedesktop trash (Linux)
            info = os.path.join(os.path.dirname(folder), "info", fname + ".trashinfo")
            if os.path.exists(info):
                with open(info, encoding="utf-8", errors="replace") as fh:
                    txt = fh.read()
                from urllib.parse import unquote
                p = re.search(r"^Path=(.*)$", txt, re.M)
                d = re.search(r"^DeletionDate=(.*)$", txt, re.M)
                if p:
                    orig = unquote(p.group(1).strip())
                    out.update(name=os.path.basename(orig), path=os.path.dirname(orig))
                if d:
                    out["deleted_at"] = d.group(1).strip()
    except OSError:
        pass
    return out


def takeout_sidecar(full: str) -> dict:
    for sc in SIDECARS:
        p = full + sc
        if os.path.exists(p) and os.path.getsize(p) < (1 << 20):
            try:
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                return d if isinstance(d, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


TEXT_EXT = {".txt", ".json", ".xml", ".html", ".htm", ".csv", ".log", ".js", ".plist", ".md", ".vcf", ".mht", ".mhtml"}


def worth_base64(fname: str, size: int) -> bool:
    """Text-like or unknown files may carry pictures as base64 (exports, contact cards, web storage)."""
    ext = os.path.splitext(fname)[1].lower()
    return 1024 <= size <= (256 << 20) and (ext in TEXT_EXT or ext not in BORING_EXT)


def worth_carving(fname: str, size: int) -> bool:
    """Should we look *inside* this non-media file for embedded pictures?"""
    if size < 2048 or size > (8 << 30):
        return False
    ext = os.path.splitext(fname)[1].lower()
    return ext not in BORING_EXT


# -- iPhone / iPad backups (Finder / iTunes "MobileSync") ----------------------

def load_ios_manifest(folder: str, say=None):
    """An iOS backup stores every file under its SHA-1 id; Manifest.db maps ids
    back to real paths. Returns {fileID: (domain, relativePath)} or None."""
    mpath = os.path.join(folder, "Manifest.db")
    if not os.path.exists(mpath):
        return None
    try:
        with open(mpath, "rb") as fh:
            if fh.read(15) != b"SQLite format 3":
                if say:
                    say(f"{folder}: this iPhone backup is ENCRYPTED. Its contents cannot be read without the "
                        "backup password; make an unencrypted backup or decrypt it first.")
                return None
        db = sqlite3.connect(f"file:{mpath}?mode=ro", uri=True)
        out = {fid: (dom or "", rel or "") for fid, dom, rel in
               db.execute("SELECT fileID, domain, relativePath FROM Files WHERE flags=1")}
        db.close()
        if say:
            say(f"{folder}: iPhone/iPad backup with {len(out)} files")
        return out
    except sqlite3.Error as e:
        if say:
            say(f"{folder}: could not read iOS backup manifest ({e})")
        return None


def ios_context(entry, role: str) -> dict:
    domain, rel = entry
    ctx = context(domain + "/" + rel, "evidence")
    app = next((label for key, label in APP_HINTS if key in (domain + "/" + rel).lower()), "iPhone backup")
    if "thumbnails" in rel.lower() or "/derivatives/" in rel.lower():
        ctx["origin"] = "cache"
        ctx["note"] = f"{app}: thumbnail kept in the iPhone backup"
    elif ctx["origin"] != "trash":
        ctx["origin"] = "backup"
        ctx["note"] = f"{app}: file inside the iPhone backup"
    ctx["name"] = os.path.basename(rel)
    ctx["path"] = domain + "/" + os.path.dirname(rel)
    return ctx


# -- email ----------------------------------------------------------------------

MEDIA_NAME = re.compile(r"(?i)\.(jpe?g|png|gif|webp|heic|heif|bmp|mp4|mov|m4v|3gp|avi|mkv|webm)$")


def mail_kind(fname: str, head: bytes) -> str:
    low = fname.lower()
    if low.endswith(".emlx"):
        return "emlx"
    if low.endswith(".eml"):
        return "eml"
    if head[:5] == b"From " and (low.endswith((".mbox", ".mbx")) or "." not in low or low.endswith(".sbd")):
        return "mbox"          # Thunderbird folders are extension-less mbox files
    if low.endswith(".mbox") and head[:5] == b"From ":
        return "mbox"
    return ""


def iter_mail(path: str, kind: str, only=None):
    """Yield (key, email.message.Message)."""
    import email
    import mailbox
    if kind == "mbox":
        box = mailbox.mbox(path, create=False)
        try:
            keys = [only] if only is not None else box.keys()
            for k in keys:
                try:
                    yield k, box.get_message(k)
                except Exception:
                    continue
        finally:
            box.close()
        return
    with open(path, "rb") as fh:
        raw = fh.read()
    if kind == "emlx":              # Apple Mail: first line is the byte count of the message
        first, _, rest = raw.partition(b"\n")
        raw = rest[: int(first.strip() or 0) or None] if first.strip().isdigit() else raw
    try:
        yield 0, email.message_from_bytes(raw)
    except Exception:
        return


def mail_meta(msg) -> dict:
    import email.header
    import email.utils

    def text(v):
        try:
            return str(email.header.make_header(email.header.decode_header(v or "")))[:120]
        except Exception:
            return (v or "")[:120]
    date = ""
    try:
        d = email.utils.parsedate_to_datetime(msg.get("Date", ""))
        date = d.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    return {"date": date, "summary": f"\"{text(msg.get('Subject'))}\" from {text(msg.get('From'))}"}


# -- base64-encoded media inside text/binary files -------------------------------

# base64 spellings of the JPEG, PNG, GIF, RIFF and ISO-BMFF ('....ftyp') signatures
B64_RE = re.compile(rb"(?:/9j/|iVBORw0KGgo|R0lGOD[lk]h|UklGR|AAAA[A-Za-z0-9+/]{4}eX)[A-Za-z0-9+/\r\n]{400,}={0,2}")


def b64_decode(chunk: bytes) -> bytes:
    import base64
    import binascii
    chunk = chunk.replace(b"\r", b"").replace(b"\n", b"")
    chunk = chunk[: len(chunk.rstrip(b"=")) // 4 * 4] if len(chunk) % 4 else chunk
    try:
        return base64.b64decode(chunk, validate=False)
    except (binascii.Error, ValueError):
        return b""
