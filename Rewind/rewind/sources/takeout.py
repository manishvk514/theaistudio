"""Google Takeout (https://takeout.google.com): Google Photos, Drive, Gmail.

First principles:
* Google Photos keeps deleted items in Trash for 60 days (30 if never backed
  up), then erases them. There is no API that can reach that Trash any more,
  and Takeout cannot return permanently deleted items.
* But copies of a photo often exist elsewhere in your Google account:
  - attachments in old emails (Gmail keeps them until you delete the mail),
  - Google Drive files and Drive's own Trash (30 days),
  - shared albums, Archive, Locked Folder (not exported) ...
  Takeout bundles all of these, so we sweep the whole export and let the
  matcher decide what is still in your library and what is not.
* Photo sidecar .json files carry the true taken-time and whether an item
  was archived/trashed, so restored files get their original dates back.
"""
import email
import json
import mailbox
import os
import re
import shutil
import tempfile
import time
import zipfile
import datetime as dt

from ..core import media
from .base import path_hint

INFO = {
    "id": "google-takeout",
    "title": "Google Photos, Drive & Gmail (Google Takeout)",
    "group": "Accounts",
    "blurb": "Download your data at takeout.google.com (choose Google Photos, Drive and Mail), "
             "then point this at the .zip/.tgz files or the unzipped folder.",
    "link": "https://takeout.google.com",
    "fields": [
        {"name": "path", "label": "Takeout .zip file or unzipped Takeout folder", "type": "folder_or_file"},
        {"name": "role", "label": "Treat Google Photos in this export as", "type": "choice", "choices": [
            ["library", "My current Google Photos library (just exported)"],
            ["search", "An old export (search it for things I deleted since)"]], "default": "library"},
    ],
}


def run(ctx, params):
    path = os.path.expanduser(params["path"])
    role = params.get("role", "library")
    zips = []
    if os.path.isdir(path):
        root = path
        zips = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith((".zip", ".tgz"))]
    elif path.lower().endswith((".zip", ".tgz", ".tar.gz")):
        root = None
        d = os.path.dirname(path)
        base = re.sub(r"-\d{3}\.(zip|tgz)$", "", os.path.basename(path))
        zips = sorted(os.path.join(d, f) for f in os.listdir(d) if f.startswith(base))
        if path not in zips:
            zips = [path]
    else:
        raise ValueError("Pick a Takeout .zip or folder.")
    if zips:
        root = ctx.cat.dir("recovered", "google-takeout", "unzipped")
        for z in zips:
            ctx.log("Unpacking %s" % os.path.basename(z))
            _unpack(z, root, ctx)
    sidecars = {}
    mboxes = []
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            if fn.endswith(".json"):
                sidecars[os.path.join(dirpath, fn)] = True
            elif fn.endswith(".mbox"):
                mboxes.append(os.path.join(dirpath, fn))
    n = 0
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            if ctx.stopped():
                return
            p = os.path.join(dirpath, fn)
            if fn.endswith((".json", ".mbox", ".html", ".csv")):
                continue
            rel = os.path.relpath(p, root)
            n += 1
            if n % 100 == 0:
                ctx.progress(n, 0, "Takeout")
            meta = _sidecar(p)
            taken = None
            if meta:
                ts = (meta.get("photoTakenTime") or {}).get("timestamp")
                if ts:
                    taken = dt.datetime.fromtimestamp(int(ts)).isoformat()
            hint, why = path_hint(rel)
            low = rel.lower()
            if meta and meta.get("trashed"):
                hint, why = "trash", "Marked as in Google Photos Trash in the export"
            if hint is None:
                if "google photos" in low or "google foto" in low:
                    hint = "present" if role == "library" else "backup"
                    why = "Google Photos" + (" (Archive)" if meta and meta.get("archived") else "")
                elif "/drive/" in "/" + low:
                    hint = "present" if role == "library" else "backup"
                    why = "Google Drive file"
                else:
                    hint, why = "export", "In Google Takeout export"
            ctx.ingest_file(p, hint, location="Google Takeout › " + rel, name=(meta or {}).get("title") or fn,
                            taken=taken, evidence=why)
    for mb in mboxes:
        _scan_mbox(ctx, mb)


def _unpack(z, root, ctx):
    if z.lower().endswith(".zip"):
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                fn = info.filename
                if info.is_dir() or ".." in fn.split("/"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in media.MEDIA_EXTS or fn.endswith((".json", ".mbox")):
                    dest = os.path.join(root, fn)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(info) as s, open(dest, "wb") as d:
                        shutil.copyfileobj(s, d)
    else:
        import tarfile
        with tarfile.open(z) as tf:
            for m in tf.getmembers():
                if not m.isfile() or ".." in m.name.split("/"):
                    continue
                ext = os.path.splitext(m.name)[1].lower()
                if ext in media.MEDIA_EXTS or m.name.endswith((".json", ".mbox")):
                    dest = os.path.join(root, m.name)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with tf.extractfile(m) as s, open(dest, "wb") as d:
                        shutil.copyfileobj(s, d)


def _sidecar(p):
    """Google writes  photo.jpg.json  or  photo.jpg.supplemental-metadata.json (names may be cut)."""
    d, fn = os.path.split(p)
    cands = [p + ".json", p + ".supplemental-metadata.json"]
    stem = fn
    try:
        for other in os.listdir(d):
            if other.endswith(".json") and other.startswith(stem[:40]) and other not in cands:
                cands.append(os.path.join(d, other))
    except OSError:
        pass
    for c in cands:
        if os.path.exists(c):
            try:
                with open(c, encoding="utf-8") as f:
                    j = json.load(f)
                if isinstance(j, dict) and ("title" in j or "photoTakenTime" in j):
                    return j
            except Exception:
                continue
    return None


def _scan_mbox(ctx, path):
    ctx.log("Looking for photo/video attachments in %s" % os.path.basename(path))
    out = ctx.out_dir("gmail-attachments")
    n = 0
    for msg in mailbox.mbox(path):
        if ctx.stopped():
            return
        subj = str(msg.get("Subject", ""))[:80]
        date = msg.get("Date")
        for part in msg.walk():
            ctype = part.get_content_type() or ""
            fn = part.get_filename() or ""
            if not (ctype.startswith("image/") or ctype.startswith("video/")
                    or os.path.splitext(fn)[1].lower() in media.MEDIA_EXTS):
                continue
            data = part.get_payload(decode=True)
            if not data or len(data) < 4096:  # skip logos and signatures
                continue
            safe = re.sub(r"[^\w.\-]+", "_", fn or "attachment")[:80]
            dest = os.path.join(out, "%d_%s" % (n, safe))
            with open(dest, "wb") as f:
                f.write(data)
            taken = None
            try:
                taken = email.utils.parsedate_to_datetime(date).replace(tzinfo=None).isoformat()
            except Exception:
                pass
            where = "Gmail: \"%s\"%s" % (subj, (" (" + taken[:10] + ")") if taken else "")
            if ctx.ingest_file(dest, "export", location=where, name=fn or safe, evidence="Attachment in an email (a copy that survives "
                                                     "even if the original photo was deleted)"):
                n += 1
    ctx.log("%d attachment(s) found in email." % n)
