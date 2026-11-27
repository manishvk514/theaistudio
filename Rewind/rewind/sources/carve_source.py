"""Raw recovery from a disk image, memory card image or device.

This is the route for files deleted long ago from cameras, SD cards, USB
sticks and older spinning hard drives: the file system forgot them, the bytes
may still be there. Works best on a byte-for-byte image of the card (see the
guide) so nothing new is written to it while we look.
"""
import os
import re
import shutil
import subprocess

from ..forensic.formats import probe, sniff
from ..forensic.source import RunSource, Source
from ..forensic.volumes import open_fs, partitions
from ..forensic.carve import carve

INFO = {
    "id": "carve",
    "title": "Deep recovery from a memory card, USB stick or disk image",
    "group": "Devices",
    "blurb": "Finds photos and videos in the free space of a card or drive, even years after deletion, "
             "as long as nothing has overwritten them. Not for SSDs or phones (they wipe freed space).",
    "fields": [
        {"name": "path", "label": "Disk image file (.img/.dd/.bin) or device", "type": "file"},
        {"name": "use_photorec", "label": "Also run PhotoRec if it is installed (slower, finds more file types)",
         "type": "bool", "default": False},
    ],
}


def run(ctx, params):
    path = os.path.expanduser(params["path"])
    if not os.path.exists(path):
        raise ValueError("Not found: %s" % path)
    ctx.log("Reading %s (read-only). Large cards take a while." % path)
    try:
        got = forensic_scan(ctx, path)
    except Exception as e:       # never lose the scan: fall back to the plain carver
        ctx.log("File-system reader stopped (%s); carving every byte instead." % e)
        got = ctx.carve_file(path, os.path.basename(path), hint="carved",
                             evidence="Rebuilt from raw bytes on the card/drive.")
    ctx.log("Recovered %d item(s) from %s." % (got, os.path.basename(path)))
    if params.get("delete_after"):
        try:
            os.remove(path)   # the temporary card copy; recovered files are already saved
        except OSError:
            pass
    if params.get("use_photorec"):
        exe = shutil.which("photorec")
        if not exe:
            ctx.log("PhotoRec is not installed; skipped. Get it at https://www.cgsecurity.org/wiki/PhotoRec")
            return
        out = ctx.out_dir("photorec")
        ctx.log("Running PhotoRec (jpg, png, mov, mp4, heic...) into %s" % out)
        cmd = [exe, "/log", "/d", os.path.join(out, "rec"), "/cmd", path,
               "fileopt,everything,disable,jpg,enable,png,enable,gif,enable,mov,enable,"
               "mp4,enable,heic,enable,riff,enable,mkv,enable,tif,enable,search"]
        try:
            subprocess.run(cmd, timeout=24 * 3600, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception as e:
            ctx.log("PhotoRec stopped: %s" % e)
        n = 0
        for root, _d, files in os.walk(out):
            for fn in files:
                if ctx.ingest_file(os.path.join(root, fn), "carved",
                                   location="PhotoRec: %s" % fn,
                                   evidence="Recovered by PhotoRec from free space"):
                    n += 1
        ctx.log("PhotoRec added %d item(s)." % n)


def _extract(view, size, dest):
    with open(dest, "wb") as f:
        pos = 0
        while pos < size:
            b = view.read(pos, min(8 << 20, size - pos))
            if not b:
                break
            f.write(b)
            pos += len(b)


def jpeg_cut_short(path):
    """True when a JPEG's image data stops early. A decoder fills the missing part with one flat grey,
    so the bottom rows come out perfectly uniform; real photos essentially never are. Catches photos
    whose end was overwritten even when stray bytes happen to look like an end-of-image marker."""
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            if im.format != "JPEG":
                return False
            im.draft("L", (max(1, im.width // 8), max(1, im.height // 8)))
            g = im.convert("L")
            w, h = g.size
            if h < 16:
                return False
            band = g.crop((0, h - max(2, h // 12), w, h))
            lo, hi = band.getextrema()
            if hi - lo > 1:
                return False
            # only "cut short" if the rest of the picture is not flat too (a plain grey image is fine)
            top = g.crop((0, 0, w, max(2, h // 3)))
            return ImageStat.Stat(top).stddev[0] > 2
    except Exception:
        return False


def forensic_scan(ctx, path):
    """1. read the file system's own records: files still there (= still here) and
    deleted ones, with their names, dates and exact layout, fragments included;
    2. carve only the unallocated space for anything whose record is gone."""
    src = Source(path)
    if not src.size:
        raise ValueError("Cannot read %s (empty, or no permission)." % path)
    out = ctx.out_dir("disk")
    n = 0
    for plabel, part in partitions(src):
        if ctx.stopped():
            break
        fs, why = open_fs(part)
        tag = "%s %s" % (os.path.basename(path), plabel)
        starts = set()
        if fs is None:
            ctx.log("%s: %s. Carving every sector." % (plabel, why))
            region = RunSource(src, [(part.base, part.size)])
        else:
            ctx.log("%s: %s file system%s." % (plabel, fs.kind, (" '%s'" % fs.label) if getattr(fs, "label", "") else ""))
            live = dead = 0
            for e in fs.entries():
                if ctx.stopped():
                    break
                if e.intact == "overwritten" or not (e.runs or e.extra.get("inline")):
                    continue
                if e.extra.get("inline"):
                    data = e.extra["inline"]
                    if not sniff(data[:16]):
                        continue
                    view, size = None, len(data)
                else:
                    view = RunSource(src, [(o + part.base, l) for o, l in e.runs], e.size)
                    size = min(e.size, view.size)
                    if not sniff(view.read(0, 16)):
                        continue
                safe = re.sub(r"[^\w.\- ]+", "_", e.name)[:80] or "file"
                dest = os.path.join(out, "%s_%d_%s" % ("del" if e.deleted else "live", ctx.found, safe))
                if view is None:
                    with open(dest, "wb") as f:
                        f.write(data)
                else:
                    _extract(view, size, dest)
                    if e.deleted and e.runs:
                        starts.add(e.runs[0][0] + part.base)
                found = probe(Source(dest), 0) if view is not None else None
                where = "%s › %s/%s" % (tag, e.path.rstrip("/"), e.name)
                if e.deleted:
                    damaged = e.intact != "good" or (view is not None and found is None) or \
                        (found is not None and found.ext == "jpg" and jpeg_cut_short(dest))
                    iid = ctx.ingest_file(dest, "carved", location=where, name=e.name,
                                          taken=(found.taken if found and found.taken else None),
                                          damaged=damaged,
                                          evidence="Deleted file whose record the %s file system still keeps "
                                                   "(name, date and exact location)%s" % (
                                                       fs.kind, "; " + e.note if e.note else ""))
                    dead += bool(iid)
                else:
                    iid = ctx.ingest_file(dest, "present", location=where, name=e.name,
                                          evidence="Still on the card/drive")
                    live += bool(iid)
                n += bool(iid)
            ctx.log("%s: %d deleted file record(s) recovered, %d file(s) still there." % (plabel, dead, live))
            free = [(o + part.base, l) for o, l in fs.free_runs()]
            region = RunSource(src, free)
            ctx.log("%s: searching %.0f MB of free space." % (plabel, region.size / 1e6))
        # With a file system, files start on sector boundaries. Without one the layout
        # is unknown (an image cut mid-partition, a damaged card), so look at every byte.
        for hit in carve(region, align=512 if fs is not None else 1, cancel=ctx.stopped, salvage=True,
                         progress=lambda d, t: ctx.progress(d, t, "searching free space")):
            runs = region.subruns(hit.offset, hit.found.length)
            if runs and runs[0][0] in starts:
                continue          # already recovered above, with its name
            dest = os.path.join(out, "carved_%012x.%s" % (runs[0][0], hit.found.ext))
            _extract(RunSource(src, runs), hit.found.length, dest)
            cut = hit.found.extra.get("truncated") or (hit.found.ext == "jpg" and jpeg_cut_short(dest))
            iid = ctx.ingest_file(dest, "carved", location="%s @ byte %d" % (tag, runs[0][0]),
                                  taken=hit.found.taken or None, damaged=bool(cut),
                                  evidence=("Only the first part survived; the rest was overwritten by newer data, "
                                            "so the bottom of the picture is missing." if cut else
                                            "Rebuilt from free space: the file system had forgotten it, "
                                            "but its bytes were never overwritten%s." % (
                                                " (put back together from %d pieces)" % len(runs) if len(runs) > 1 else "")))
            n += bool(iid)
    return n
