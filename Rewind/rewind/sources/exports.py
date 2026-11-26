"""Copies that live in other places: data downloads, text messages, Time Machine.

* Data downloads: Instagram, Facebook and Messenger ("Download your
  information"), Apple (privacy.apple.com "Get a copy of your data", which
  includes iCloud Photos), X/Twitter, TikTok, Samsung, Xiaomi, LinkedIn...
  Every photo you ever posted, sent in a DM or stored there comes back in
  full, even when the original on your phone was purged years ago.
* Text messages (MMS): pictures sent by plain text message live in the
  phone's message database. On Android, apps like "SMS Backup & Restore"
  export them as XML with the pictures inside; on iPhone they come in
  through the iPhone backup source.
* Time Machine and other Mac backups: every hourly/daily snapshot holds the
  Photos library as it was then, including photos deleted since.
"""
import base64
import datetime as dt
import json
import os
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
import zipfile

from ..core import media
from .base import path_hint

EXPORT_INFO = {
    "id": "data-download",
    "title": "Instagram, Facebook, Apple & other data downloads",
    "group": "Accounts",
    "blurb": "Every service must let you download your data. Those downloads include photos and videos you "
             "posted or sent there, even if you deleted the originals long ago. Point this at the .zip(s).",
    "fields": [
        {"name": "path", "label": "Downloaded .zip file or its unzipped folder", "type": "folder_or_file"},
    ],
}

MMS_INFO = {
    "id": "mms",
    "title": "Text messages (MMS) backup",
    "group": "Phones",
    "blurb": "Pictures and videos sent by text message. Android: export with the free 'SMS Backup & Restore' "
             "app (XML file). iPhone: already included in the iPhone backup source.",
    "fields": [
        {"name": "path", "label": "Backup file (sms-....xml)", "type": "file"},
    ],
}

TM_INFO = {
    "id": "mac-backups",
    "title": "Time Machine or a copy of a Photos library",
    "group": "Devices",
    "blurb": "Opens every Photos library inside a Time Machine backup (or an old .photoslibrary copy) and "
             "finds photos you have deleted since. Old backups go back years.",
    "fields": [
        {"name": "path", "label": "Time Machine backup folder, or a .photoslibrary", "type": "folder",
         "discover": "time_machine"},
    ],
}

PLATFORM_HINTS = [("instagram", "Instagram"), ("facebook", "Facebook"), ("messenger", "Messenger"),
                  ("icloud", "iCloud (Apple data copy)"), ("apple", "Apple data copy"), ("twitter", "X / Twitter"),
                  ("tiktok", "TikTok"), ("samsung", "Samsung"), ("xiaomi", "Xiaomi"), ("linkedin", "LinkedIn"),
                  ("snapchat", "Snapchat"), ("google", "Google")]


def _platform(name):
    low = name.lower()
    for k, label in PLATFORM_HINTS:
        if k in low:
            return label
    return "Data download"


def _unpack_all(ctx, path):
    """Return a folder holding the media of the export(s), unpacking zips/tars safely."""
    if os.path.isdir(path):
        zips = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith((".zip", ".tar", ".tgz", ".tar.gz"))]
        if not zips:
            return path
    else:
        zips = [path]
    root = ctx.cat.dir("recovered", ctx.source, "unzipped", re.sub(r"\W+", "_", os.path.basename(path))[:40])
    for z in zips:
        ctx.log("Unpacking %s" % os.path.basename(z))
        if zipfile.is_zipfile(z):
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    fn = info.filename
                    if info.is_dir() or ".." in fn.split("/") or fn.startswith("/"):
                        continue
                    if os.path.splitext(fn)[1].lower() in media.MEDIA_EXTS or fn.endswith((".json", ".csv")) \
                            or "." not in os.path.basename(fn):
                        dest = os.path.join(root, fn)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with zf.open(info) as s, open(dest, "wb") as d:
                            shutil.copyfileobj(s, d)
        elif tarfile.is_tarfile(z):
            with tarfile.open(z) as tf:
                for mem in tf.getmembers():
                    if not mem.isfile() or ".." in mem.name.split("/") or mem.name.startswith("/"):
                        continue
                    dest = os.path.join(root, mem.name)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with tf.extractfile(mem) as s, open(dest, "wb") as d:
                        shutil.copyfileobj(s, d)
    return root


def _json_dates(root):
    """Meta-style JSON: {"uri": "media/posts/x.jpg", "creation_timestamp": 1600000000}."""
    dates = {}

    def walk(o):
        if isinstance(o, dict):
            uri = o.get("uri")
            ts = o.get("creation_timestamp") or o.get("taken_at") or o.get("timestamp")
            if isinstance(uri, str) and isinstance(ts, (int, float)) and ts > 0:
                dates[os.path.basename(uri)] = dt.datetime.fromtimestamp(ts).isoformat()
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for d, _s, files in os.walk(root):
        for f in files:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8", errors="ignore") as fh:
                        walk(json.load(fh))
                except Exception:
                    pass
    return dates


def run_export(ctx, params):
    path = os.path.expanduser(params["path"])
    if not os.path.exists(path):
        raise ValueError("Not found: %s" % path)
    platform = _platform(os.path.basename(path))
    root = _unpack_all(ctx, path)
    dates = _json_dates(root)
    n = 0
    for d, _s, files in os.walk(root):
        for f in files:
            if ctx.stopped():
                return
            p = os.path.join(d, f)
            if f.endswith((".json", ".csv", ".html")):
                continue
            rel = os.path.relpath(p, root)
            hint, why = path_hint(rel)
            if hint is None:
                hint, why = "export", "Kept by %s and returned in your data download" % platform
            if ctx.ingest_file(p, hint, location="%s › %s" % (platform, rel), taken=dates.get(f), evidence=why):
                n += 1
    ctx.log("%s: %d photo/video file(s)." % (platform, n))


def run_mms(ctx, params):
    path = os.path.expanduser(params["path"])
    n = 0
    for _ev, el in ET.iterparse(path, events=("end",)):
        if el.tag != "mms":
            continue
        who = el.get("contact_name") or el.get("address") or "unknown"
        when = None
        try:
            when = dt.datetime.fromtimestamp(int(el.get("date")) / 1000).isoformat()
        except Exception:
            pass
        for part in el.iter("part"):
            ct = part.get("ct") or ""
            data = part.get("data")
            if not data or not ct.startswith(("image/", "video/")):
                continue
            try:
                raw = base64.b64decode(data)
            except Exception:
                continue
            _k, ext = media.sniff(raw[:64])
            if ctx.ingest_bytes(raw, ext or ".jpg", "export", "Text message with %s (%s)" % (who, (when or "")[:10]),
                                name=part.get("name") or part.get("cl") or "mms", taken=None, subdir="mms",
                                evidence="Sent or received by text message (MMS)"):
                n += 1
        el.clear()
    ctx.log("%d picture(s)/video(s) found in text messages." % n)


def time_machine_roots():
    out = []
    for vol in ("/Volumes",):
        if not os.path.isdir(vol):
            continue
        for v in os.listdir(vol):
            p = os.path.join(vol, v, "Backups.backupdb")
            if os.path.isdir(p):
                out.append(p)
        tm = os.path.join(vol, ".timemachine")
        if os.path.isdir(tm):
            out.append(tm)
    return out


def run_mac_backups(ctx, params):
    from .computer import mac_photos_library
    from .base import walk
    root = os.path.expanduser(params["path"])
    if root.rstrip("/").endswith(".photoslibrary"):
        mac_photos_library(ctx, root, role="search")
        return
    libs, seen_inodes = [], set()
    for d, dirs, files in os.walk(root, onerror=lambda e: ctx.log("No permission: %s" % e)):
        if ctx.stopped():
            return
        for x in list(dirs):
            if x.endswith(".photoslibrary"):
                libs.append(os.path.join(d, x))
                dirs.remove(x)
        for f in files:
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen_inodes:  # Time Machine hard-links unchanged files between snapshots
                continue
            seen_inodes.add(key)
            ctx.ingest_file(p, "backup", location="Backup › " + os.path.relpath(p, root),
                            evidence="Copy kept in a Mac backup")
    ctx.log("Found %d Photos librar%s in this backup." % (len(libs), "y" if len(libs) == 1 else "ies"))
    for lib in libs:
        ctx.log("Opening %s" % lib)
        try:
            mac_photos_library(ctx, lib, role="search")
        except Exception as e:
            ctx.log("Could not open %s: %s" % (lib, e))
