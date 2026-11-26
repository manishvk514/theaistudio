"""Rewind running ON an Android phone (inside Termux): scan the phone itself.

Same places as the USB route (camera, hidden .trashed- files, gallery bins,
thumbnail caches, WhatsApp/Telegram/Snapchat folders, LOST.DIR), read
directly from the phone's shared storage after you allow storage access.
Newer Android versions block every app from Android/data, so those few
app caches are only reachable over USB from a computer.
"""
import os

from .android_adb import TARGETS

INFO = {
    "id": "this-phone",
    "title": "This phone",
    "group": "Phones",
    "blurb": "Scans this Android phone's camera, hidden trash files, gallery bins, thumbnail caches and "
             "WhatsApp, Telegram and Snapchat folders.",
    "fields": [
        {"name": "everything", "label": "Search the whole phone storage (slower, most thorough)",
         "type": "bool", "default": False},
    ],
}

ROOTS = ("/sdcard", "/storage/emulated/0", os.path.expanduser("~/storage/shared"))


def is_android():
    return "ANDROID_ROOT" in os.environ or os.path.exists("/system/build.prop")


def storage_root():
    for r in ROOTS:
        if os.path.isdir(r) and os.access(r, os.R_OK):
            try:
                os.listdir(r)
                return r
            except OSError:
                continue
    return None


def run(ctx, params):
    from . import folder
    root = storage_root()
    if not root:
        raise ValueError("Rewind can't see the phone's storage yet. In Termux run  termux-setup-storage  "
                         "and tap Allow, then start Rewind again.")
    if params.get("everything"):
        folder.run(ctx, {"path": root, "role": "library", "deep": True})
        return
    for i, (rel, role) in enumerate(TARGETS):
        if ctx.stopped():
            return
        p = os.path.join(root, rel)
        if not os.path.isdir(p):
            continue
        ctx.progress(i, len(TARGETS), rel)
        try:
            folder.run(ctx, {"path": p, "role": role, "deep": True})
        except Exception as e:
            ctx.log("Android blocked %s (%s). Use the USB route from a computer for this one." % (rel, e))
