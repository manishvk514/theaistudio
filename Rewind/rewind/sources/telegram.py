"""Telegram: data export from Telegram Desktop, and Telegram's media caches.

First principles:
* Telegram is a cloud messenger: while a message exists, its photo/video is
  on Telegram's servers and the official export (Telegram Desktop ›
  Settings › Advanced › Export Telegram data) downloads it in full.
* When a message is deleted (by you, or "for everyone" by the other side),
  Telegram removes it from the servers; the export cannot return it.
* What survives on devices is the media CACHE: Telegram keeps files you
  viewed in its cache folder (Android: Android/data/org.telegram.messenger,
  Mac app: its group container "postbox/media", Desktop: Downloads/Telegram
  Desktop). Those cached copies can outlive the deleted message until the
  cache is cleared, so we scan caches deeply (files there have no extension).
"""
import json
import os
import datetime as dt

from .base import walk, path_hint

INFO = {
    "id": "telegram",
    "title": "Telegram",
    "group": "Apps",
    "blurb": "A Telegram Desktop export folder (with result.json), or Telegram's cache folder on your "
             "Mac/PC/phone, where viewed media can outlive deleted messages.",
    "fields": [
        {"name": "path", "label": "Export folder or Telegram cache folder", "type": "folder",
         "discover": "telegram"},
    ],
}


def run(ctx, params):
    root = os.path.expanduser(params["path"])
    if not os.path.isdir(root):
        raise ValueError("Folder not found: %s" % root)
    dates = {}
    rj = os.path.join(root, "result.json")
    is_export = os.path.exists(rj)
    if is_export:
        try:
            with open(rj, encoding="utf-8") as f:
                data = json.load(f)
            chats = data.get("chats", {}).get("list", []) if "chats" in data else [data]
            for c in chats:
                for m in c.get("messages", []):
                    for k in ("photo", "file", "thumbnail"):
                        v = m.get(k)
                        if isinstance(v, str) and not v.startswith("("):
                            dates[os.path.normpath(v)] = (m.get("date"), c.get("name"))
            ctx.log("Export lists %d media files." % len(dates))
        except Exception as e:
            ctx.log("Could not read result.json: %s" % e)
    for p in walk(root, ctx):
        rel = os.path.relpath(p, root)
        when, chat = dates.get(os.path.normpath(rel), (None, None))
        taken = None
        if when:
            try:
                taken = dt.datetime.fromisoformat(when).isoformat()
            except Exception:
                pass
        hint, why = path_hint(rel)
        if is_export and hint is None:
            hint, why = "export", "In Telegram export" + (" (chat: %s)" % chat if chat else "")
        elif hint is None:
            hint, why = "cache", "Telegram media cache (can outlive deleted messages)"
        if not ctx.ingest_file(p, hint, location="Telegram › " + rel, taken=taken, evidence=why):
            if not is_export and os.path.getsize(p) > 4096 and ("db" in p.lower() or "cache" in p.lower()):
                ctx.carve_file(p, "Telegram › " + rel, hint="cache",
                               evidence="Picture embedded in a Telegram cache/database file")
