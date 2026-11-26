"""Snapchat: "My Data" export (accounts.snapchat.com › My Data).

First principles:
* Snaps and chats are designed to disappear; Snapchat deletes opened snaps
  from its servers, so neither the export nor any app can bring them back.
* What Snapchat keeps until YOU delete it: Memories, saved chat media,
  My Eyes Only (needs your passcode inside the app). The export lists every
  Memory with a download link; we fetch each one so you hold a local copy.
* Copies outside Snapchat: anything you saved to the camera roll, screen
  recordings, and the phone's Snapchat cache (scan the phone for those).
"""
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
import datetime as dt

from ..core import media

INFO = {
    "id": "snapchat",
    "title": "Snapchat (My Data export)",
    "group": "Accounts",
    "blurb": "Request your data at accounts.snapchat.com › My Data (tick Memories and Chat Media). "
             "Point this at the downloaded .zip or folder.",
    "link": "https://accounts.snapchat.com/",
    "fields": [
        {"name": "path", "label": "My Data .zip or unzipped folder", "type": "folder_or_file"},
        {"name": "download", "label": "Download Memories from the export's links (needs internet; "
                                      "links expire a few days after the export)", "type": "bool",
         "default": True},
    ],
}


def run(ctx, params):
    path = os.path.expanduser(params["path"])
    root = path
    if path.lower().endswith(".zip"):
        root = ctx.cat.dir("recovered", "snapchat", "unzipped")
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir() or ".." in info.filename.split("/"):
                    continue
                dest = os.path.join(root, info.filename)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(info) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d)
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            if os.path.splitext(fn)[1].lower() in media.MEDIA_EXTS:
                rel = os.path.relpath(p, root)
                ctx.ingest_file(p, "export", location="Snapchat export › " + rel,
                                evidence="Saved in Snapchat (Memories or chat media)")
    hist = None
    for cand in ("json/memories_history.json", "memories_history.json"):
        if os.path.exists(os.path.join(root, cand)):
            hist = os.path.join(root, cand)
    if not hist:
        ctx.log("No memories_history.json in this export.")
        return
    with open(hist, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("Saved Media") or data.get("saved_media") or []
    ctx.log("Export lists %d Memories." % len(entries))
    if not params.get("download", True):
        return
    out = ctx.out_dir("memories")
    ok = 0
    for i, e in enumerate(entries):
        if ctx.stopped():
            return
        ctx.progress(i, len(entries), "downloading Memories")
        when = e.get("Date", "")
        kind = "video" if "video" in str(e.get("Media Type", "")).lower() else "image"
        taken = None
        try:
            taken = dt.datetime.strptime(when[:19], "%Y-%m-%d %H:%M:%S").isoformat()
        except Exception:
            pass
        stem = "memory_%s_%d" % (re.sub(r"\W", "", when)[:14], i)
        if any(f.startswith(stem + ".") or f.startswith(stem + "_") for f in os.listdir(out)):
            continue  # already downloaded on an earlier scan
        try:
            data_bytes = _download(e)
        except Exception as err:
            ctx.add_missing("memory_%s" % re.sub(r"\W", "", when)[:14], "Snapchat Memories",
                            "Listed in your export but the link no longer works (%s). Request a fresh "
                            "export and run this again soon after." % err.__class__.__name__,
                            kind=kind, taken=taken)
            continue
        k, ext = media.sniff(data_bytes[:64])
        if not k and data_bytes[:2] == b"PK":  # overlays come zipped with the media
            ext = ".zip"
        dest = os.path.join(out, "memory_%s_%d%s" % (re.sub(r"\W", "", when)[:14], i, ext or ".bin"))
        with open(dest, "wb") as f:
            f.write(data_bytes)
        if ext == ".zip":
            with zipfile.ZipFile(dest) as z:
                for n in z.namelist():
                    if "main" in n or "-main" in n:
                        mp = os.path.join(out, os.path.basename(n))
                        with z.open(n) as s, open(mp, "wb") as d:
                            shutil.copyfileobj(s, d)
                        ctx.ingest_file(mp, "export", location="Snapchat Memories (%s)" % when,
                                        taken=taken, evidence="Downloaded from your Snapchat Memories")
            continue
        if ctx.ingest_file(dest, "export", location="Snapchat Memories (%s)" % when, taken=taken,
                           evidence="Downloaded from your Snapchat Memories"):
            ok += 1
    ctx.log("Downloaded %d Memories." % ok)


def _download(e):
    url = e.get("Media Download Url")
    if url:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    link = e.get("Download Link")
    if not link:
        raise ValueError("no link")
    parsed = urllib.parse.urlsplit(link)
    req = urllib.request.Request(urllib.parse.urlunsplit(parsed._replace(query="")),
                                 data=parsed.query.encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        real = r.read().decode().strip()
    if not real.startswith("https://"):
        raise ValueError("unexpected answer")
    with urllib.request.urlopen(real, timeout=120) as r:
        return r.read()
