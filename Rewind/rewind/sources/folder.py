"""Scan any folder: your current library, a phone copied over USB, an SD card,
an old backup drive, an app's data folder.

role='library' -> files here are your CURRENT photos (used to tell what is
                  deleted). Trash and cache paths inside are still flagged.
role='search'  -> everything here is a place deleted copies may survive.
deep=True      -> also open databases, caches and extension-less files and
                  pull out photos embedded inside them (incl. deleted rows).
"""
import os

from ..core import media
from .base import Context, path_hint, walk, DB_EXTS

INFO = {
    "id": "folder",
    "title": "Folder, drive or memory card",
    "group": "Devices",
    "blurb": "Your Photos folder, a phone copied over USB, an SD card, an old hard drive or backup.",
    "fields": [
        {"name": "path", "label": "Folder", "type": "folder"},
        {"name": "role", "label": "What is this folder?", "type": "choice", "choices": [
            ["library", "My current photos (used to tell what's deleted)"],
            ["search", "A place to search for deleted copies (old drive, card, copy, backup)"]],
         "default": "search"},
        {"name": "deep", "label": "Deep scan: look inside caches and app databases too", "type": "bool",
         "default": True},
    ],
}


def run(ctx: Context, params):
    root = os.path.expanduser(params["path"])
    role = params.get("role", "search")
    deep = bool(params.get("deep", True))
    if not os.path.isdir(root):
        raise ValueError("Folder not found: %s" % root)
    ctx.log("Scanning %s (%s)" % (root, "current library" if role == "library" else "search"))
    n = 0
    for path in walk(root, ctx):
        n += 1
        if n % 200 == 0:
            ctx.progress(n, 0, os.path.dirname(path)[-60:])
        rel = os.path.relpath(path, root)
        hint, why = path_hint(rel)
        ekind = media.ext_kind(path)
        skind, _ = media.sniff_file(path)
        if skind:
            if hint is None:
                hint = "present" if role == "library" else "backup"
                why = ("In your current library" if role == "library"
                       else "Copy found in a searched folder")
            ctx.ingest_file(path, hint, location=path, evidence=why)
            continue
        if not deep:
            continue
        low = path.lower()
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size < 2048:
            continue
        is_db = low.endswith(DB_EXTS) or _is_sqlite(path)
        cachey = hint in ("cache", "thumbnail") or ekind is None and size < 64 << 20 and (
            "cache" in low or "thumb" in low or "media" in low)
        if is_db or cachey or low.endswith((".thumbdata", ".thumbdata3", ".thumbdata4",
                                            ".thumbdata5", ".ithmb", ".tmp", ".bin", ".dat")):
            why = ("Picture embedded in an app database (can include deleted rows)" if is_db
                   else "Picture embedded in a cache file")
            got = ctx.carve_file(path, path, hint="thumbnail" if (is_db or "thumb" in low) else "cache",
                                 evidence=why, min_size=1024)
            if got:
                ctx.log("Found %d picture(s) inside %s" % (got, rel))
    ctx.log("Done: %d files looked at." % n)


def _is_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False
