"""iPhone / iPad backup made by Finder (Mac) or iTunes / Apple Devices (Windows).

Why it matters: a local backup is a frozen copy of the phone. It holds the
camera roll *including the Recently Deleted album*, the Photos database with
the "trashed" flag on each deleted item, preview thumbnails that can outlive
the originals, edited versions, iMessage attachments, and the media folders
of WhatsApp, Telegram and other apps, plus their databases (whose free pages
can still hold thumbnails of deleted messages).

Old backups are time machines: a photo deleted in 2021 is still inside a
backup made in 2020.
"""
import glob
import os
import plistlib
import shutil
import sqlite3
import sys
import tempfile

from ..core import media
from . import photos_db, whatsapp

INFO = {
    "id": "iphone-backup",
    "title": "iPhone backup (Finder / iTunes)",
    "group": "Phones",
    "blurb": "Reads the camera roll, Recently Deleted, previews, iMessage, WhatsApp and Telegram media "
             "from a local iPhone backup, including old backups from years ago.",
    "fields": [
        {"name": "path", "label": "Backup folder", "type": "folder", "discover": "ios_backups"},
        {"name": "role", "label": "How old is this backup?", "type": "choice", "choices": [
            ["library", "It is my latest backup (treat its photos as my current library)"],
            ["search", "It is an older backup (search it for things I deleted since)"]],
         "default": "library"},
        {"name": "password", "label": "Backup password (only if the backup is encrypted)", "type": "password"},
    ],
}

APP_DOMAINS = {
    "whatsapp": ("WhatsApp", "group.net.whatsapp"),
    "telegra": ("Telegram", "telegra"),
    "picaboo": ("Snapchat", "picaboo"),
    "instagram": ("Instagram", "burbn"),
    "facebook": ("Messenger/Facebook", "facebook"),
    "google": ("Google app", "google"),
}


def default_backup_dirs():
    roots = []
    if sys.platform == "darwin":
        roots.append(os.path.expanduser("~/Library/Application Support/MobileSync/Backup"))
    elif os.name == "nt":
        roots.append(os.path.join(os.environ.get("APPDATA", ""), "Apple Computer", "MobileSync", "Backup"))
        roots.append(os.path.join(os.environ.get("USERPROFILE", ""), "Apple", "MobileSync", "Backup"))
    out = []
    for r in roots:
        for d in glob.glob(os.path.join(r, "*")):
            if os.path.exists(os.path.join(d, "Manifest.db")) or os.path.exists(os.path.join(d, "Manifest.plist")):
                out.append(d)
    return out


def describe(path):
    """Name, date and encryption state of a backup folder."""
    info = {"path": path}
    try:
        with open(os.path.join(path, "Info.plist"), "rb") as f:
            p = plistlib.load(f)
        info["device"] = p.get("Device Name") or p.get("Display Name")
        info["date"] = str(p.get("Last Backup Date", ""))
    except Exception:
        pass
    try:
        with open(os.path.join(path, "Manifest.plist"), "rb") as f:
            m = plistlib.load(f)
        info["encrypted"] = bool(m.get("IsEncrypted"))
    except Exception:
        info["encrypted"] = None
    return info


def _app_label(domain):
    d = domain.lower()
    for key, (label, _x) in APP_DOMAINS.items():
        if key in d:
            return label
    return None


def run(ctx, params):
    root = os.path.expanduser(params["path"])
    role = params.get("role", "library")
    desc = describe(root)
    ctx.log("Backup: %s, made %s" % (desc.get("device") or "iPhone", desc.get("date") or "unknown date"))
    opener = None
    manifest = os.path.join(root, "Manifest.db")
    if desc.get("encrypted"):
        pw = params.get("password")
        if not pw:
            raise ValueError("This backup is encrypted. Enter the backup password you chose in Finder/iTunes.")
        opener = _Encrypted(root, pw, ctx)
        manifest = opener.manifest
    elif not os.path.exists(manifest):
        raise ValueError("No Manifest.db here. Pick the folder that has a long name made of letters and numbers.")

    con = sqlite3.connect("file:%s?mode=ro" % manifest, uri=True)
    rows = con.execute("SELECT fileID, domain, relativePath FROM Files WHERE flags=1").fetchall()
    con.close()
    by_rel = {(d, r): fid for fid, d, r in rows}
    ctx.log("%d files in the backup's index." % len(rows))

    def local(fid, domain=None, rel=None):
        if opener:
            return opener.extract(fid, domain, rel)
        p = os.path.join(root, fid[:2], fid)
        return p if os.path.exists(p) else None

    # 1) Photos database: which camera-roll items are in Recently Deleted
    assets = {}
    pdb_id = by_rel.get(("CameraRollDomain", "Media/PhotoData/Photos.sqlite"))
    if pdb_id:
        p = local(pdb_id, "CameraRollDomain", "Media/PhotoData/Photos.sqlite")
        if p:
            tmpdir = tempfile.mkdtemp()
            tmp = os.path.join(tmpdir, "Photos.sqlite")
            shutil.copy(p, tmp)
            for suf in ("-wal", "-shm"):
                sid = by_rel.get(("CameraRollDomain", "Media/PhotoData/Photos.sqlite" + suf))
                sp = sid and local(sid, "CameraRollDomain", "Media/PhotoData/Photos.sqlite" + suf)
                if sp:
                    shutil.copy(sp, tmp + suf)
            try:
                for a in photos_db.read_assets(tmp):
                    assets["Media/%s/%s" % (a["directory"], a["filename"])] = a
                ctx.log("Photos database lists %d items, %d of them in Recently Deleted." % (
                    len(assets), sum(1 for a in assets.values() if a["trashed"])))
            except Exception as e:
                ctx.log("Could not read the Photos database: %s" % e)
            ctx.carve_file(tmp, "iPhone Photos database", hint="thumbnail",
                           evidence="Preview embedded in the Photos database", min_size=2048)
            shutil.rmtree(tmpdir, ignore_errors=True)

    dcim_seen = set()
    wa_db = None
    total = len(rows)
    for i, (fid, domain, rel) in enumerate(rows):
        if ctx.stopped():
            return
        if i % 500 == 0:
            ctx.progress(i, total, "reading backup")
        low = rel.lower()
        app = _app_label(domain)
        is_media_ext = os.path.splitext(low)[1] in media.MEDIA_EXTS
        if domain == "CameraRollDomain":
            if not (low.startswith("media/dcim/") or "/thumbnails/" in low or "/mutations/" in low
                    or low.startswith("media/photodata/cplassets") or is_media_ext):
                continue
        elif domain == "MediaDomain" and "sms/attachments" in low:
            pass
        elif app:
            if low.endswith(("chatstorage.sqlite", "msgstore.db")) and app == "WhatsApp":
                wa_db = (fid, domain, rel)
            if low.endswith((".sqlite", ".db", ".sqlite-wal", ".db-wal")):
                p = local(fid, domain, rel)
                if p:
                    ctx.carve_file(p, "%s database in backup" % app, hint="thumbnail",
                                   evidence="Preview kept inside %s's database (can include deleted "
                                            "messages)" % app, min_size=2048)
                continue
            if not is_media_ext and opener:  # cannot cheaply sniff encrypted files
                continue
        elif not is_media_ext:
            continue
        p = local(fid, domain, rel)
        if not p:
            continue
        name = os.path.basename(rel)
        location = "%s backup › %s › %s" % (desc.get("device") or "iPhone", domain, rel)
        if domain == "CameraRollDomain" and low.startswith("media/dcim/"):
            a = assets.get(rel)
            dcim_seen.add(rel)
            if a and a["trashed"]:
                ctx.ingest_file(p, "trash", location=location, name=name, taken=a["created"],
                                deleted_at=a["trashed_at"],
                                evidence="In the iPhone's Recently Deleted album when this backup was made")
            else:
                ctx.ingest_file(p, "present" if role == "library" else "backup", location=location,
                                name=name, taken=a and a["created"],
                                evidence="Camera roll in this backup")
        elif "/thumbnails/" in low:
            parent = os.path.basename(os.path.dirname(rel))
            ctx.ingest_file(p, "thumbnail", location=location, name=parent or name,
                            evidence="Preview the iPhone kept of this photo")
        elif "/mutations/" in low:
            ctx.ingest_file(p, "backup", location=location, name=name,
                            evidence="Edited version saved by the Photos app")
        elif domain == "MediaDomain":
            ctx.ingest_file(p, "present" if role == "library" else "backup", location=location, name=name,
                            evidence="iMessage / SMS attachment")
        else:
            hint = "cache" if "cache" in low or "tmp" in low else (
                "present" if role == "library" else "backup")
            ctx.ingest_file(p, hint, location=location, name=name, evidence="%s media" % (app or domain))

    for rel, a in assets.items():
        if a["trashed"] and rel not in dcim_seen:
            ctx.add_missing(a["filename"], "iPhone Photos › Recently Deleted",
                            "Listed as deleted on %s but the original was not in this backup "
                            "(the phone had already purged it, or it lives only in iCloud)." % (
                                (a["trashed_at"] or "")[:10]),
                            kind=a["kind"], taken=a["created"], deleted_at=a["trashed_at"])
    if assets and not dcim_seen:
        ctx.log("Note: the backup holds the Photos database but no camera-roll files. "
                "This usually means iCloud Photos is on, so originals live in iCloud "
                "(check Recently Deleted at icloud.com/photos).")
    if wa_db:
        p = local(*wa_db)
        if p:
            whatsapp.report_missing_from_db(ctx, p, present_names=None, platform="ios")
    if opener:
        opener.cleanup()


class _Encrypted:
    """Thin wrapper over iphone_backup_decrypt (optional dependency)."""

    def __init__(self, root, password, ctx):
        try:
            from iphone_backup_decrypt import EncryptedBackup  # type: ignore
        except Exception:
            raise ValueError("Encrypted backups need one extra piece: run  pip install iphone_backup_decrypt  "
                             "(see the guide), then scan again.")
        self.ctx = ctx
        self.b = EncryptedBackup(backup_directory=root, passphrase=password)
        self.tmp = ctx.cat.dir("tmp", "ios_dec")
        self.manifest = os.path.join(self.tmp, "Manifest.db")
        self.b.save_manifest_file(self.manifest)
        self.out = ctx.out_dir("decrypted")

    def extract(self, fid, domain, rel):
        dest = os.path.join(self.out, fid)
        if os.path.exists(dest):
            return dest
        try:
            self.b.extract_file(relative_path=rel, domain_like=domain, output_filename=dest)
        except Exception as e:
            self.ctx.log("Could not decrypt %s: %s" % (rel, e))
            return None
        return dest if os.path.exists(dest) else None

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
