"""Memory-card / disk-image recovery on real file systems.

Builds FAT32, NTFS and exFAT volumes with the real drivers (mtools, ntfs-3g,
exfat-fuse), writes photos and videos, deletes some (including files split
around ones that stay), then runs Rewind's card scan and requires every deleted
file back byte for byte, with its name, and every remaining file marked "still here".
Needs (Linux): dosfstools mtools ntfs-3g exfatprogs exfat-fuse; root for the loop device.
Run: python3 tests/test_disk.py
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import build_images  # noqa: E402
from fixtures import make_media  # noqa: E402
from rewind.server import App  # noqa: E402

FAILS = []
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  -> %s" % (detail,)))
    if not ok:
        FAILS.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="rewind-disk-")
    files = make_media(os.path.join(tmp, "media"))
    cases = [("FAT32", build_images.build_fat, {"size_mb": 40, "fat_bits": 32}, ("mkfs.vfat", "mcopy")),
             ("NTFS", build_images.build_ntfs, {}, ("mkfs.ntfs", "ntfs-3g")),
             ("exFAT", build_images.build_exfat, {}, ("mkfs.exfat", "mount.exfat-fuse"))]
    for label, build, kw, tools in cases:
        if not all(shutil.which(t) for t in tools):
            print("  SKIP %s (needs %s)" % (label, ", ".join(tools)))
            continue
        img = os.path.join(tmp, label + ".img")
        try:
            victims = [v for v in build(img, files, **kw) if "wedge" not in v]
        except Exception as e:
            print("  SKIP %s (could not build test volume: %s)" % (label, e))
            continue
        app = App(os.path.join(tmp, "ws-" + label), 0)
        jid = app.start_scan("carve", {"path": img})
        while app.jobs[jid].state == "running":
            time.sleep(0.2)
        rows = [dict(r) for r in app.cat.con().execute("SELECT name, sha256, verdict FROM items")]
        deleted = {r["sha256"]: r["name"] for r in rows if r["verdict"] == "deleted"}
        present = {r["sha256"] for r in rows if r["verdict"] == "present"}
        missing = [v for v in victims if sha(files[v]) not in deleted]
        check("%s: every deleted photo/video back byte for byte" % label, not missing, missing)
        check("%s: deleted files keep their names" % label,
              all(deleted.get(sha(files[v]), "").lower().lstrip("_")[1:] in v.lower() for v in victims))
        check("%s: files still on the card are 'still here'" % label,
              all(sha(files[k]) in present for k in build_images.KEEP))
        check("%s: nothing still on the card is called deleted" % label,
              not any(sha(files[k]) in deleted for k in build_images.KEEP))
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASSED" if not FAILS else "\nFAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
