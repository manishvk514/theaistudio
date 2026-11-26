"""Find, without being told, every place on this computer where a copy of a
picture that was permanently deleted elsewhere may still be sitting.

A phone photo that is gone from the phone and from every bin can still exist
because, at some point, this computer displayed it, synced it, backed it up,
received it in a message or mailed it. Each entry below is one such mechanism.

noisy=True marks places that also hold masses of unrelated web images.
"""
from __future__ import annotations

import glob
import os
import sys


def _expand(pattern: str):
    pattern = os.path.expandvars(os.path.expanduser(pattern))
    if "%" in pattern or "$" in pattern:
        return []
    return [p for p in glob.glob(pattern) if os.path.isdir(p)]


# (label, pattern, role, noisy, why)
_WINDOWS = [
    ("Pictures", r"%USERPROFILE%\Pictures", "current", False, ""),
    ("Videos", r"%USERPROFILE%\Videos", "current", False, ""),
    ("OneDrive pictures", r"%USERPROFILE%\OneDrive*\Pictures", "current", False, ""),
    ("Recycle Bin", r"?:\$Recycle.Bin", "evidence", False, "deleted files, incl. ones removed by cloud sync"),
    ("Explorer thumbnail cache", r"%LOCALAPPDATA%\Microsoft\Windows\Explorer", "evidence", False,
     "a small copy of every picture Explorer ever displayed"),
    ("iPhone backups", r"%APPDATA%\Apple Computer\MobileSync\Backup", "evidence", False, "camera roll at backup time"),
    ("iPhone backups", r"%USERPROFILE%\Apple\MobileSync\Backup", "evidence", False, "camera roll at backup time"),
    ("Phone Link cache", r"%LOCALAPPDATA%\Packages\Microsoft.YourPhone_*\LocalCache", "evidence", False,
     "recent phone photos mirrored to the PC"),
    ("Windows Photos app", r"%LOCALAPPDATA%\Packages\Microsoft.Windows.Photos_*\LocalState", "evidence", False, "photo index and previews"),
    ("WhatsApp Desktop", r"%LOCALAPPDATA%\Packages\*WhatsApp*\LocalState", "evidence", False, "media viewed in WhatsApp on this PC"),
    ("WhatsApp Desktop", r"%APPDATA%\WhatsApp", "evidence", False, "media viewed in WhatsApp on this PC"),
    ("Dropbox cache", r"%USERPROFILE%\Dropbox*\.dropbox.cache", "evidence", False, "recently deleted synced files"),
    ("Google Drive cache", r"%LOCALAPPDATA%\Google\DriveFS", "evidence", False, "locally cached Drive files"),
    ("OneDrive client data", r"%LOCALAPPDATA%\Microsoft\OneDrive", "evidence", False, "sync client caches"),
    ("Mail (Thunderbird)", r"%APPDATA%\Thunderbird\Profiles", "evidence", False, "pictures sent or received by email"),
    ("Temp files", r"%LOCALAPPDATA%\Temp", "evidence", False, "copies made while viewing/editing"),
    ("Chrome cache", r"%LOCALAPPDATA%\Google\Chrome\User Data\*\Cache", "evidence", True, "pictures viewed in web apps"),
    ("Edge cache", r"%LOCALAPPDATA%\Microsoft\Edge\User Data\*\Cache", "evidence", True, "pictures viewed in web apps"),
    ("Firefox cache", r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*\cache2", "evidence", True, "pictures viewed in web apps"),
]
_MAC = [
    ("Pictures", "~/Pictures", "current", False, ""),
    ("Movies", "~/Movies", "current", False, ""),
    ("Trash", "~/.Trash", "evidence", False, "deleted files"),
    ("iPhone backups", "~/Library/Application Support/MobileSync/Backup", "evidence", False, "camera roll at backup time"),
    ("Messages attachments", "~/Library/Messages/Attachments", "evidence", False, "every picture sent or received in iMessage"),
    ("Mail", "~/Library/Mail", "evidence", False, "pictures sent or received by email"),
    ("WhatsApp", "~/Library/Group Containers/*WhatsApp*", "evidence", False, "media viewed in WhatsApp on this Mac"),
    ("WhatsApp", "~/Library/Containers/*WhatsApp*", "evidence", False, "media viewed in WhatsApp on this Mac"),
    ("Quick Look thumbnails", "~/Library/Caches/com.apple.QuickLook.thumbnailcache", "evidence", False, "previews of viewed files"),
    ("Dropbox cache", "~/Dropbox*/.dropbox.cache", "evidence", False, "recently deleted synced files"),
    ("Google Drive cache", "~/Library/Application Support/Google/DriveFS", "evidence", False, "locally cached Drive files"),
    ("Mail (Thunderbird)", "~/Library/Thunderbird/Profiles", "evidence", False, "pictures sent or received by email"),
    ("Chrome cache", "~/Library/Caches/Google/Chrome/*/Cache", "evidence", True, "pictures viewed in web apps"),
    ("Safari cache", "~/Library/Caches/com.apple.Safari", "evidence", True, "pictures viewed in web apps"),
    ("Firefox cache", "~/Library/Caches/Firefox/Profiles/*/cache2", "evidence", True, "pictures viewed in web apps"),
]
_LINUX = [
    ("Pictures", "~/Pictures", "current", False, ""),
    ("Videos", "~/Videos", "current", False, ""),
    ("Trash", "~/.local/share/Trash", "evidence", False, "deleted files"),
    ("Thumbnail cache", "~/.cache/thumbnails", "evidence", False, "a small copy of every picture the file manager displayed"),
    ("Thumbnail cache", "~/.thumbnails", "evidence", False, "a small copy of every picture the file manager displayed"),
    ("Mail (Thunderbird)", "~/.thunderbird", "evidence", False, "pictures sent or received by email"),
    ("Dropbox cache", "~/Dropbox*/.dropbox.cache", "evidence", False, "recently deleted synced files"),
    ("Chrome cache", "~/.cache/google-chrome/*/Cache", "evidence", True, "pictures viewed in web apps"),
    ("Chromium cache", "~/.cache/chromium/*/Cache", "evidence", True, "pictures viewed in web apps"),
    ("Firefox cache", "~/.cache/mozilla/firefox/*/cache2", "evidence", True, "pictures viewed in web apps"),
]


def hiding_places(include_noisy: bool = False):
    table = _WINDOWS if os.name == "nt" else _MAC if sys.platform == "darwin" else _LINUX
    out, seen = [], set()
    for label, pattern, role, noisy, why in table:
        if noisy and not include_noisy:
            continue
        for p in _expand(pattern):
            real = os.path.realpath(p)
            if real in seen:
                continue
            seen.add(real)
            out.append({"label": label, "path": p, "role": role, "noisy": noisy, "why": why})
    # the reference library first, so everything after it can be judged against it
    out.sort(key=lambda x: x["role"] != "current")
    return out
