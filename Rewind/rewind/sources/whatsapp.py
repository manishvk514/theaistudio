"""WhatsApp: phone media folders, chat exports, and the message database.

First principles:
* WhatsApp servers do not keep your photos. After delivery the only copies
  are on the devices in the chat (yours AND the other person's) and in
  backups (Google Drive / iCloud / the phone's local daily backups).
* "Delete for me" removes the message row, but the media file in the
  WhatsApp/Media folder often stays (and vice versa).
* The message database (msgstore.db) lists every media file a chat ever had,
  with its SHA-256 and a small JPEG thumbnail. So even when the photo is gone
  from your phone we can: show its thumbnail, tell you which chat it was in,
  and match it byte-for-byte if a copy turns up anywhere else.
* The phone keeps up to 7 daily encrypted database backups
  (msgstore-YYYY-MM-DD.1.db.crypt15). Older ones still list messages you
  have since deleted. Reading them needs your own 64-digit backup key.
"""
import base64
import glob
import os
import shutil
import sqlite3
import subprocess
import zipfile

from .base import path_hint, walk
from ..core import media

INFO = {
    "id": "whatsapp",
    "title": "WhatsApp",
    "group": "Apps",
    "blurb": "WhatsApp folder copied from an Android phone, a chat export (.zip), or WhatsApp's local "
             "database backups. iPhone WhatsApp comes in through the iPhone backup source.",
    "fields": [
        {"name": "path", "label": "WhatsApp folder (Android/media/com.whatsapp/WhatsApp) or chat export .zip",
         "type": "folder_or_file"},
        {"name": "role", "label": "Is this the phone's current WhatsApp folder?", "type": "choice", "choices": [
            ["library", "Yes, it's what's on my phone now"],
            ["search", "No, it's an old copy or export"]], "default": "library"},
        {"name": "key", "label": "Optional: 64-digit backup key or path to encrypted_backup.key "
                                 "(to read deleted messages' media list)", "type": "password"},
    ],
}


def run(ctx, params):
    path = os.path.expanduser(params["path"])
    role = params.get("role", "library")
    if path.lower().endswith(".zip"):
        return _export_zip(ctx, path)
    if not os.path.isdir(path):
        raise ValueError("Not found: %s" % path)
    names = set()
    for p in walk(path, ctx):
        rel = os.path.relpath(p, path)
        low = rel.lower()
        if low.startswith("databases") or low.startswith("backups"):
            continue
        hint, why = path_hint(rel)
        if hint is None:
            hint = "present" if role == "library" else "backup"
            why = "WhatsApp media folder" + (" (Sent)" if "/sent/" in "/" + low else "")
        if ctx.ingest_file(p, hint, location="WhatsApp › " + rel, evidence=why):
            names.add(os.path.basename(p))
    dbs = sorted(glob.glob(os.path.join(path, "Databases", "msgstore*")))
    plain = [d for d in dbs if d.endswith(".db")]
    enc = [d for d in dbs if ".crypt" in d]
    if enc and not plain:
        key = params.get("key")
        if not key:
            ctx.log("Found %d encrypted WhatsApp database backup(s). Add your 64-digit backup key to list "
                    "media from deleted messages." % len(enc))
        else:
            plain = _decrypt_all(ctx, enc, key)
    for db in plain:
        report_missing_from_db(ctx, db, names, platform="android")
        ctx.carve_file(db, "WhatsApp database %s" % os.path.basename(db), hint="thumbnail",
                       evidence="Thumbnail kept in WhatsApp's message database (also from deleted "
                                "messages)", min_size=1024)


def _export_zip(ctx, path):
    out = ctx.out_dir("export", os.path.splitext(os.path.basename(path))[0][:50])
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir() or os.path.splitext(info.filename)[1].lower() not in media.MEDIA_EXTS:
                continue
            dest = os.path.join(out, os.path.basename(info.filename))
            with z.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            ctx.ingest_file(dest, "export", location="%s › %s" % (os.path.basename(path), info.filename),
                            evidence="Included in a WhatsApp chat export")


def _decrypt_all(ctx, files, key):
    exe = shutil.which("wadecrypt")
    if not exe:
        ctx.log("To read encrypted WhatsApp databases, install wa-crypt-tools "
                "(https://github.com/ElDavoo/wa-crypt-tools): pip install wa-crypt-tools")
        return []
    out = []
    tmp = ctx.cat.dir("tmp", "wa")
    for f in files:
        dest = os.path.join(tmp, os.path.basename(f).split(".crypt")[0])
        try:
            subprocess.run([exe, key, f, dest], check=True, timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            out.append(dest)
            ctx.log("Decrypted %s" % os.path.basename(f))
        except Exception as e:
            ctx.log("Could not decrypt %s (wrong key?): %s" % (os.path.basename(f), e))
    return out


def _b64_to_hex(v):
    try:
        raw = base64.b64decode(v)
        return raw.hex() if len(raw) == 32 else None
    except Exception:
        return None


def report_missing_from_db(ctx, db_path, present_names, platform):
    """List media the chats reference whose file is not on the device."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception as e:
        ctx.log("Could not open WhatsApp database: %s" % e)
        return
    rows = []
    try:
        if "message_media" in tables:
            cols = {r[1] for r in con.execute("PRAGMA table_info(message_media)")}
            chat_join = ""
            chat_sel = "NULL"
            if "chat" in tables and "jid" in tables and "chat_row_id" in cols:
                chat_join = ("LEFT JOIN chat c ON c._id = mm.chat_row_id "
                             "LEFT JOIN jid j ON j._id = c.jid_row_id")
                chat_sel = "COALESCE(c.subject, j.user, j.raw_string)"
            q = ("SELECT mm.file_path, %s, %s, %s FROM message_media mm %s" % (
                "mm.file_hash" if "file_hash" in cols else "NULL",
                "mm.mime_type" if "mime_type" in cols else "NULL", chat_sel, chat_join))
            rows = con.execute(q).fetchall()
        elif "messages" in tables:  # older schema
            cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
            if "media_name" in cols:
                rows = con.execute("SELECT media_name, %s, media_mime_type, key_remote_jid FROM messages "
                                   "WHERE media_name IS NOT NULL" % (
                                       "media_hash" if "media_hash" in cols else "NULL")).fetchall()
        elif "ZWAMEDIAITEM" in tables:  # iPhone ChatStorage.sqlite
            rows = con.execute(
                "SELECT mi.ZMEDIALOCALPATH, NULL, NULL, cs.ZPARTNERNAME FROM ZWAMEDIAITEM mi "
                "LEFT JOIN ZWAMESSAGE m ON m.Z_PK = mi.ZMESSAGE "
                "LEFT JOIN ZWACHATSESSION cs ON cs.Z_PK = m.ZCHATSESSION "
                "WHERE mi.ZMEDIALOCALPATH IS NOT NULL").fetchall()
    except Exception as e:
        ctx.log("WhatsApp database layout not recognised (%s); thumbnails are still carved." % e)
    finally:
        con.close()
    missing = 0
    for fp, fhash, mime, chat in rows:
        if not fp:
            continue
        name = os.path.basename(fp)
        ext = os.path.splitext(name)[1].lower()
        if ext not in media.MEDIA_EXTS and not (mime and (mime.startswith("image") or mime.startswith("video"))):
            continue
        if present_names is not None and name in present_names:
            continue
        if present_names is None and platform == "ios":
            continue_flag = ctx.cat.con().execute(
                "SELECT 1 FROM items WHERE name=? AND path IS NOT NULL LIMIT 1", (name,)).fetchone()
            if continue_flag:
                continue
        kind = "video" if (mime or "").startswith("video") or ext in (".mp4", ".3gp", ".mov") else "image"
        ctx.add_missing(name, "WhatsApp chat: %s" % (chat or "unknown chat"),
                        "The chat still lists this %s but the file is no longer on the phone. "
                        "The other person in the chat probably still has it: ask them to resend it." % kind,
                        kind=kind, sha256=_b64_to_hex(fhash) if fhash else None)
        missing += 1
    if missing:
        ctx.log("%d WhatsApp media file(s) are listed in chats but missing from the phone." % missing)
