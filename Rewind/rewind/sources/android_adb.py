"""Android phone over USB (adb). Read-only: it copies, never deletes.

Where deleted photos still physically exist on an Android phone without root:
* MediaStore trash (Android 11+): Google Photos / Files "deleted" items are
  renamed to  .trashed-<expiry>-name.jpg  in the SAME folder for ~30 days.
* Gallery app bins: Samsung (Android/.Trash, gallery3d .Trash), Xiaomi
  (MIUI/Gallery/cloud/.trashBin), OnePlus/Oppo (.RecycleBin) ...
* Thumbnail caches: DCIM/.thumbnails and .thumbdata files hold previews of
  photos that were deleted long ago.
* App media folders: WhatsApp (Android/media/com.whatsapp), Telegram
  (Telegram/ and Android/data/org.telegram.messenger cache), Snapchat saves,
  Instagram, screen recordings, Downloads.
* LOST.DIR: fragments the phone rescued after a card error.

What is NOT reachable: the phone's internal flash after a real delete. Modern
Android encrypts each file with its own key and TRIMs freed blocks, so the
raw bytes are gone or unreadable. No app can bring those back.
"""
import os
import shutil
import subprocess

INFO = {
    "id": "android",
    "title": "Android phone (USB cable)",
    "group": "Phones",
    "blurb": "Copies the camera, gallery trash bins, hidden .trashed files, thumbnail caches and "
             "WhatsApp/Telegram media folders from an Android phone. Needs USB debugging on.",
    "fields": [
        {"name": "everything", "label": "Copy the whole internal storage (slow, most thorough)",
         "type": "bool", "default": False},
    ],
}

TARGETS = [
    ("DCIM", "library"), ("Pictures", "library"), ("Movies", "library"), ("Download", "library"),
    ("Android/media/com.whatsapp", "library"), ("Android/media/com.whatsapp.w4b", "library"),
    ("WhatsApp", "library"), ("Telegram", "library"),
    ("Android/data/org.telegram.messenger", "search"),
    ("Android/media/org.telegram.messenger", "search"),
    ("Snapchat", "library"), ("Android/media/com.snapchat.android", "search"),
    ("Instagram", "library"), ("Android/media/com.instagram.android", "search"),
    ("Android/.Trash", "search"), (".Trash", "search"), (".RecycleBin", "search"),
    ("Android/data/com.sec.android.gallery3d", "search"),
    ("Android/data/com.miui.gallery", "search"), ("MIUI/Gallery", "search"),
    ("Android/data/com.google.android.apps.photos", "search"),
    ("Android/data/com.coloros.gallery3d", "search"), (".Gallery2", "search"),
    ("LOST.DIR", "search"), ("Recordings", "library"),
]


def adb():
    exe = shutil.which("adb")
    if exe:
        return exe
    for p in ("~/Library/Android/sdk/platform-tools/adb", "~/platform-tools/adb",
              "~/Downloads/platform-tools/adb", "C:/platform-tools/adb.exe",
              "~/AppData/Local/Android/Sdk/platform-tools/adb.exe"):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return p
    return None


def devices():
    exe = adb()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "devices", "-l"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    return [line.split()[0] + (" (unauthorised: tap Allow on the phone)" if "unauthorized" in line else "")
            for line in out.splitlines()[1:] if line.strip()]


def run(ctx, params):
    exe = adb()
    if not exe:
        raise ValueError("adb is not installed. Download Android Platform Tools from "
                         "https://developer.android.com/tools/releases/platform-tools and unzip it into "
                         "your Downloads folder, then try again.")
    devs = devices() or []
    if not devs:
        raise ValueError("No phone found. Plug it in, turn on USB debugging, and tap Allow on the phone.")
    if any("unauthorised" in d for d in devs):
        raise ValueError("The phone is asking for permission. Unlock it and tap Allow, then try again.")
    dest = ctx.cat.dir("pulled", "android")
    from . import folder  # reuse the folder scanner for classification
    targets = [("", "library")] if params.get("everything") else TARGETS
    for i, (rel, role) in enumerate(targets):
        if ctx.stopped():
            return
        src = "/sdcard/" + rel
        ok = subprocess.run([exe, "shell", "ls", "-d", src], capture_output=True, text=True, timeout=30)
        if ok.returncode != 0 or "No such file" in ok.stdout + ok.stderr:
            continue
        local = os.path.join(dest, rel or "sdcard")
        os.makedirs(os.path.dirname(local), exist_ok=True)
        ctx.progress(i, len(targets), "copying " + (rel or "everything"))
        ctx.log("Copying %s ..." % src)
        r = subprocess.run([exe, "pull", "-a", src, os.path.dirname(local) if rel else dest],
                           capture_output=True, text=True, timeout=6 * 3600)
        if r.returncode != 0:
            ctx.log("Phone refused part of %s (normal on newer Android for app folders): %s" % (
                src, (r.stderr or r.stdout).strip()[-200:]))
        if os.path.isdir(local):
            folder.run(ctx, {"path": local, "role": role, "deep": True})
    ctx.log("Phone copy kept at %s" % dest)
