"""Scan orchestration: disks/images, folders, archives, cloud accounts -> catalog."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import traceback
import urllib.parse
import zipfile

from . import residue
from .carve import carve
from .catalog import Catalog
from .formats import probe, sniff
from .source import BytesSource, RunSource, Source
from .volumes import open_fs, partitions

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 400_000_000
except Exception:      # Pillow is optional
    Image = None

FFMPEG = shutil.which("ffmpeg")


def sha256_of(src, size: int | None = None) -> str:
    h = hashlib.sha256()
    size = src.size if size is None else size
    pos = 0
    while pos < size:
        b = src.read(pos, min(4 << 20, size - pos))
        if not b:
            break
        h.update(b)
        pos += len(b)
    return h.hexdigest()


def dhash(data_or_path) -> int | None:
    """64-bit difference hash: what the picture looks like, independent of
    resolution and compression."""
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) else data_or_path)
        img.draft("L", (64, 64))
        img = img.convert("L").resize((9, 8), Image.BILINEAR)
        px = img.tobytes()
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
        return bits
    except Exception:
        return None


class Job:
    def __init__(self, jid, title):
        self.id, self.title = jid, title
        self.status = "running"
        self.stage = ""
        self.done = self.total = 0
        self.found = 0
        self.log = []
        self.started = time.time()
        self.cancelled = False

    def say(self, msg):
        self.log.append(msg)
        del self.log[:-200]

    def progress(self, done, total):
        self.done, self.total = done, total

    def as_dict(self):
        return {"id": self.id, "title": self.title, "status": self.status, "stage": self.stage, "done": self.done,
                "total": self.total, "found": self.found, "log": self.log[-30:], "elapsed": round(time.time() - self.started, 1)}


class Engine:
    def __init__(self, workdir: str):
        self.workdir = os.path.abspath(workdir)
        os.makedirs(os.path.join(self.workdir, "thumbs"), exist_ok=True)
        os.makedirs(os.path.join(self.workdir, "cloud"), exist_ok=True)
        self.catalog = Catalog(os.path.join(self.workdir, "case.sqlite"))
        self.jobs: dict[int, Job] = {}
        self._jid = 0
        self._sources: dict[str, Source] = {}
        self._lock = threading.Lock()
        self.providers = {}        # source_id -> cloud provider (tokens live in memory only)

    # -- jobs --------------------------------------------------------------

    def start(self, title, fn, *args, wait=False, **kw) -> Job:
        with self._lock:
            self._jid += 1
            job = Job(self._jid, title)
            self.jobs[job.id] = job

        def run():
            try:
                fn(job, *args, **kw)
                job.status = "cancelled" if job.cancelled else "done"
            except Exception as e:
                job.status = "error"
                job.say(f"ERROR: {e}")
                job.say(traceback.format_exc(limit=4))
            finally:
                self.catalog.commit()
                try:
                    self.catalog.classify()
                except Exception as e:
                    job.say(f"classification failed: {e}")
        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()
        return job

    def _src(self, path) -> Source:
        with self._lock:
            if path not in self._sources:
                self._sources[path] = Source(path)
            return self._sources[path]

    # -- shared: turn bytes into a catalog row ------------------------------

    def _record(self, job, view, size, origin, sid, loc, name="", path="", found=None, quality=None, **extra):
        """view: Source-like over exactly the file's bytes."""
        head = view.read(0, 16)
        if found is None:
            found = probe(view, 0, size)
        guess = sniff(head)
        if not found and not guess:
            return None
        kind, ext = (found.kind, found.ext) if found else guess
        if found and origin in ("cache", "embedded", "carved-raw", "carved-free") and 0 < max(found.width, found.height) < 100:
            return None        # icons, emoji, UI sprites: never somebody's photo
        if quality is None:
            quality = ("verified" if found.verified else "intact") if found else "damaged"
        ph = None
        if kind == "image" and Image is not None and size <= (96 << 20) and quality != "damaged":
            ph = dhash(view.read(0, size))
        if not name:
            stamp = (found.taken if found and found.taken else "").replace(":", "").replace("-", "")
            name = f"recovered_{stamp}.{ext}" if stamp else ""
        rid = self.catalog.add(
            source_id=sid, origin=origin, quality=quality, kind=kind, ext=ext, name=name, path=path, size=size,
            sha256=sha256_of(view, size), phash=ph, width=found.width if found else 0, height=found.height if found else 0,
            taken=found.taken if found else "", loc=loc, **extra)
        job.found += 1
        if job.found % 200 == 0:
            self.catalog.commit()
        return rid

    # -- disk / image / memory card ------------------------------------------

    def scan_image(self, job, path, deep=False, label=None):
        src = self._src(path)
        if not src.size:
            raise RuntimeError("cannot read this device/image (no permission, or empty). Raw devices need admin/root.")
        sid = self.catalog.add_source(label or os.path.basename(path) or path, "disk", path, "device")
        job.say(f"{path}: {src.size / 1e9:.2f} GB")
        for plabel, part in partitions(src):
            if job.cancelled:
                return
            fs, why = open_fs(part)
            base = part.base
            if fs is None:
                job.say(f"{plabel}: {why} -> carving every byte")
                job.stage = f"{plabel}: carving raw space"
                self._carve_into(job, RunSource(src, [(base, part.size)]), src, sid, "carved-raw",
                                 align=1 if deep else 512, where=f"{plabel} (raw)")
                continue
            job.say(f"{plabel}: {fs.kind} '{getattr(fs, 'label', '')}', cluster {fs.cluster_size} bytes")
            job.stage = f"{plabel}: reading {fs.kind} file records (live + deleted)"
            n_del = n_live = 0
            for e in fs.entries():
                if job.cancelled:
                    return
                if e.intact == "overwritten" or not e.runs:
                    continue
                runs = [(o + base, l) for o, l in e.runs]
                view = RunSource(src, runs, e.size)
                if view.size < e.size and not e.deleted:
                    continue
                if not sniff(view.read(0, 16)):
                    continue
                size = min(e.size, view.size)
                found = probe(view, 0, size)
                loc = {"t": "runs", "p": path, "runs": view.subruns(0, size), "size": size}
                if e.deleted:
                    # The record says where the file *was*; the structure check says
                    # whether those clusters still hold it.
                    quality = "verified" if found and e.intact == "good" else "damaged"
                    if found and found.length < size * 0.5:
                        quality = "damaged"
                    self._record(job, view, size, "fs-deleted", sid, loc, e.name, e.path, found, quality,
                                 modified=e.modified, note=e.note)
                    n_del += 1
                else:
                    self._record(job, view, size, "live", sid, loc, e.name, e.path, found,
                                 "intact", modified=e.modified)
                    n_live += 1
            job.say(f"{plabel}: {n_live} existing and {n_del} deleted media records")
            free = [(o + base, l) for o, l in fs.free_runs()]
            total_free = sum(l for _, l in free)
            job.say(f"{plabel}: carving {total_free / 1e6:.0f} MB of unallocated space")
            job.stage = f"{plabel}: rebuilding files from unallocated space"
            self._carve_into(job, RunSource(src, free), src, sid, "carved-free",
                             align=1 if deep else 512, where=f"{plabel} (unallocated)")

    def _sqlite_blobs(self, job, full, sid, origin, rel):
        """Pictures stored as BLOBs in a SQLite database (chat apps, galleries and
        thumbnail stores do this). Large blobs are split over linked pages, so they
        must be read through SQLite itself rather than carved."""
        try:
            db = sqlite3.connect("file:" + urllib.parse.quote(full) + "?mode=ro&immutable=1", uri=True)
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                qt = '"' + t.replace('"', '""') + '"'
                try:
                    cols = [r[1] for r in db.execute(f"PRAGMA table_info({qt})")]
                    for c in cols:
                        qc = '"' + c.replace('"', '""') + '"'
                        for rowid, blob in db.execute(
                                f"SELECT rowid, {qc} FROM {qt} WHERE typeof({qc})='blob' AND length({qc})>512"):
                            if job.cancelled:
                                return
                            if sniff(blob[:16]):
                                self._record(job, BytesSource(blob), len(blob), origin, sid,
                                             {"t": "sqlite", "p": full, "table": t, "col": c, "rowid": rowid},
                                             "", rel, note=f"stored in database table {t}.{c}")
                except sqlite3.Error:
                    continue
            page = db.execute("PRAGMA page_size").fetchone()[0]
            db.close()
            return page
        except sqlite3.Error:
            return None

    def _carve_into(self, job, view, src, sid, origin, align, where, path_for_loc=None, max_size=None):
        n = 0
        for hit in carve(view, align=align, max_size=max_size, progress=job.progress, cancel=lambda: job.cancelled):
            runs = view.subruns(hit.offset, hit.found.length) if isinstance(view, RunSource) else [(hit.offset, hit.found.length)]
            loc = {"t": "runs", "p": path_for_loc or src.path, "runs": runs, "size": hit.found.length}
            sub = RunSource(src, runs)
            self._record(job, sub, hit.found.length, origin, sid, loc, "", where, hit.found,
                         note=f"at byte {runs[0][0]}" + (f", {len(runs)} fragments" if len(runs) > 1 else ""))
            n += 1
        return n

    # -- folder / phone storage copy / backup / export -----------------------

    def scan_folder(self, job, path, role="device", deep=True, label=None):
        path = os.path.abspath(path)
        if os.path.isfile(path) and not zipfile.is_zipfile(path):
            return self.scan_image(job, path, deep, label)
        sid = self.catalog.add_source(label or os.path.basename(path.rstrip("/\\")) or path,
                                      "folder" if os.path.isdir(path) else "archive", path, role)
        if os.path.isfile(path):
            self._scan_zip(job, path, sid, role, os.path.basename(path))
            return
        job.stage = "listing files"
        todo = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            if os.path.abspath(root) == self.workdir:
                dirs[:] = []
                continue
            for f in files:
                todo.append((root, f))
            if job.cancelled:
                return
        job.say(f"{len(todo)} files to examine")
        ios = {}
        for i, (root, fname) in enumerate(todo):
            if job.cancelled:
                return
            job.progress(i + 1, len(todo))
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, path)
            job.stage = rel[-70:]
            try:
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                size = os.path.getsize(full)
                if size < 64:
                    continue
                if root not in ios:
                    ios[root] = residue.load_ios_manifest(root, job.say) or ios.get(os.path.dirname(root))
                with open(full, "rb") as fh:
                    head = fh.read(16)
                ctx = residue.context(rel, role)
                if ios.get(root) and fname in ios[root]:
                    ctx = residue.ios_context(ios[root][fname], role)
                if sniff(head):
                    view = self._src(full)
                    info = residue.trash_details(full, fname, ctx)
                    side = residue.takeout_sidecar(full)
                    if side.get("trashed"):
                        ctx = dict(ctx, origin="trash", note="marked trashed in Google Takeout metadata")
                    self._record(job, view, size, ctx["origin"], sid, {"t": "file", "p": full},
                                 info.get("name") or ctx.get("name") or fname, info.get("path") or ctx.get("path") or os.path.dirname(rel),
                                 modified=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(full))),
                                 deleted_at=info.get("deleted_at", ""), note=ctx.get("note", ""))
                    self._drop(full)
                elif head[:4] == b"PK\x03\x04" and fname.lower().endswith(".zip"):
                    self._scan_zip(job, full, sid, role, rel)
                elif deep and residue.worth_carving(fname, size):
                    view = self._src(full)
                    origin = "cache" if ctx["origin"] in ("cache", "trash") else "embedded"
                    page = self._sqlite_blobs(job, full, sid, origin, rel) if head == b"SQLite format 3\0" else None
                    # Inside a database only what fits in one page is contiguous; that
                    # still catches small pictures in deleted rows and free pages.
                    self._carve_into(job, view, view, sid, origin, 1, rel, max_size=page - 35 if page else None)
                    self._drop(full)
            except (OSError, zipfile.BadZipFile) as e:
                job.say(f"skipped {rel}: {e}")

    def _drop(self, path):
        with self._lock:
            s = self._sources.pop(path, None)
        if s:
            s.close()

    def _scan_zip(self, job, zpath, sid, role, rel):
        with zipfile.ZipFile(zpath) as z:
            names = set(z.namelist())
            infos = [i for i in z.infolist() if not i.is_dir() and i.file_size >= 64]
            for n, info in enumerate(infos):
                if job.cancelled:
                    return
                job.progress(n + 1, len(infos))
                if info.flag_bits & 1:
                    job.say(f"{rel}: encrypted zip entries skipped")
                    return
                with z.open(info) as fh:
                    head = fh.read(16)
                if not sniff(head) or info.file_size > (2 << 30):
                    continue
                data = z.read(info)
                ctx = residue.context(info.filename, "evidence" if role != "current" else role)
                for sc in residue.SIDECARS:
                    if info.filename + sc in names:
                        try:
                            if json.loads(z.read(info.filename + sc)).get("trashed"):
                                ctx = dict(ctx, origin="trash", note="marked trashed in Google Takeout metadata")
                        except (ValueError, KeyError):
                            pass
                mt = "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time
                self._record(job, BytesSource(data), len(data), ctx["origin"], sid,
                             {"t": "zip", "p": zpath, "m": info.filename},
                             os.path.basename(info.filename), rel + "/" + os.path.dirname(info.filename),
                             modified=mt, note=ctx.get("note", ""))

    # -- cloud ---------------------------------------------------------------

    def scan_cloud(self, job, provider, label=None):
        who = provider.account()
        sid = self.catalog.add_source(label or f"{provider.title}: {who}", "cloud", provider.name, "cloud", who)
        self.providers[sid] = provider
        job.say(f"connected to {provider.title} as {who}")
        for origin, lister in (("cloud-trash", provider.deleted), ("cloud-live", provider.live)):
            job.stage = "listing " + ("deleted items" if origin == "cloud-trash" else "existing items")
            n = 0
            for e in lister():
                if job.cancelled:
                    return
                self.catalog.add(source_id=sid, origin=origin, quality="intact", kind=e["kind"], ext=e["ext"],
                                 name=e["name"], path=e.get("path", ""), size=e.get("size", 0),
                                 sha256=e.get("sha256") or None, width=e.get("width", 0), height=e.get("height", 0),
                                 taken=e.get("taken", ""), modified=e.get("modified", ""), deleted_at=e.get("deleted_at", ""),
                                 note=e.get("note", ""), loc={"t": "cloud", "sid": sid, "id": e["id"], "rev": e.get("rev", ""),
                                                               "path": e.get("cloud_path", "")})
                n += 1
                job.found += origin == "cloud-trash"
            job.say(f"{n} {'deleted' if origin == 'cloud-trash' else 'existing'} pictures/videos")

    # -- reading an item back ------------------------------------------------

    def open_item(self, item):
        """Returns (Source-like, size) for an item row."""
        loc = json.loads(item["loc"])
        if loc["t"] == "file":
            s = Source(loc["p"])
            return s, s.size
        if loc["t"] == "runs":
            v = RunSource(self._src(loc["p"]), [tuple(r) for r in loc["runs"]], loc["size"])
            return v, v.size
        if loc["t"] == "zip":
            with zipfile.ZipFile(loc["p"]) as z:
                data = z.read(loc["m"])
            return BytesSource(data), len(data)
        if loc["t"] == "sqlite":
            db = sqlite3.connect("file:" + urllib.parse.quote(loc["p"]) + "?mode=ro&immutable=1", uri=True)
            qt, qc = ('"' + loc[k].replace('"', '""') + '"' for k in ("table", "col"))
            data = db.execute(f"SELECT {qc} FROM {qt} WHERE rowid=?", (loc["rowid"],)).fetchone()[0]
            db.close()
            return BytesSource(data), len(data)
        if loc["t"] == "cloud":
            cache = os.path.join(self.workdir, "cloud", f"{item['id']}.{item['ext']}")
            if not os.path.exists(cache):
                prov = self.providers.get(loc["sid"])
                if not prov:
                    raise RuntimeError("cloud session expired: reconnect the account")
                tmp = cache + ".part"
                with open(tmp, "wb") as fh:
                    prov.download(loc, fh)
                os.replace(tmp, cache)
            s = Source(cache)
            return s, s.size
        raise ValueError("unknown location")

    def thumbnail(self, item_id: int) -> str | None:
        """Path of a cached JPEG thumbnail, or None if one cannot be made."""
        out = os.path.join(self.workdir, "thumbs", f"{item_id}.jpg")
        if os.path.exists(out):
            return out if os.path.getsize(out) else None
        item = self.catalog.get(item_id)
        if not item:
            return None
        try:
            if json.loads(item["loc"])["t"] == "cloud" and not os.path.exists(
                    os.path.join(self.workdir, "cloud", f"{item_id}.{item['ext']}")):
                return None        # never download from the cloud just to draw a thumbnail
            view, size = self.open_item(item)
            if item["kind"] == "image" and Image is not None and size <= (128 << 20):
                img = Image.open(io.BytesIO(view.read(0, size)))
                img.draft("RGB", (512, 512))
                img.seek(0)
                img = img.convert("RGB")
                img.thumbnail((360, 360))
                img.save(out, "JPEG", quality=80)
                return out
            if item["kind"] == "video" and FFMPEG and size <= (4 << 30):
                tmp = out + ".src." + item["ext"]
                with open(tmp, "wb") as fh:
                    pos = 0
                    while pos < size:
                        b = view.read(pos, min(8 << 20, size - pos))
                        if not b:
                            break
                        fh.write(b)
                        pos += len(b)
                try:
                    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp, "-frames:v", "1", "-vf",
                                    "scale=360:-2", out], timeout=30, capture_output=True)
                finally:
                    os.remove(tmp)
                if os.path.exists(out) and os.path.getsize(out):
                    return out
        except Exception:
            pass
        open(out, "wb").close()     # remember the failure
        return None

    # -- restore -------------------------------------------------------------

    def restore(self, job, ids, dest, in_place=False):
        dest = os.path.abspath(os.path.expanduser(dest)) if dest else ""
        srcs = {s["id"]: s for s in self.catalog.sources()}
        if dest:
            for s in srcs.values():
                if s["type"] == "folder" and (dest + os.sep).startswith(os.path.abspath(s["path"]) + os.sep):
                    raise RuntimeError(f"Choose a destination outside the scanned folder '{s['path']}': "
                                       "writing recovered files into the place being recovered can overwrite them.")
                if s["type"] == "disk" and os.path.abspath(s["path"]) == dest:
                    raise RuntimeError("Destination cannot be the scanned device.")
            os.makedirs(dest, exist_ok=True)
        ok = fail = 0
        for i, item_id in enumerate(ids):
            if job.cancelled:
                break
            job.progress(i + 1, len(ids))
            item = self.catalog.get(item_id)
            if not item:
                continue
            try:
                loc = json.loads(item["loc"])
                if in_place:
                    if loc["t"] != "cloud":
                        raise RuntimeError("only cloud items can be put back in place")
                    prov = self.providers.get(loc["sid"])
                    if not prov:
                        raise RuntimeError("cloud session expired: reconnect the account")
                    prov.restore(loc)
                    self.catalog.mark_restored(item_id, "restored in the cloud account")
                    ok += 1
                    continue
                view, size = self.open_item(item)
                src = srcs.get(item["source_id"], {})
                group = {"deleted": "Deleted - recovered", "existing": "Still existing", "unsorted": "Unsorted"}[item["state"]]
                year = (item["taken"] or item["modified"] or "")[:4] or "undated"
                name = safe_name(item["name"] or f"recovered_{item_id:06d}.{item['ext']}")
                if "." not in name or name.rsplit(".", 1)[1].lower() not in _exts(item["ext"]):
                    name += "." + item["ext"]
                folder = os.path.join(dest, group, safe_name(src.get("label", "source")), year)
                os.makedirs(folder, exist_ok=True)
                target = os.path.join(folder, name)
                n = 1
                while os.path.exists(target):
                    stem, ext = os.path.splitext(name)
                    target = os.path.join(folder, f"{stem} ({n}){ext}")
                    n += 1
                with open(target, "wb") as fh:
                    pos = 0
                    while pos < size:
                        b = view.read(pos, min(8 << 20, size - pos))
                        if not b:
                            break
                        fh.write(b)
                        pos += len(b)
                stamp = item["taken"] or item["modified"]
                if stamp:
                    try:
                        t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
                        os.utime(target, (t, t))
                    except (ValueError, OverflowError, OSError):
                        pass
                self.catalog.mark_restored(item_id, target)
                ok += 1
                job.found = ok
            except Exception as e:
                fail += 1
                job.say(f"{item['name'] or item_id}: {e}")
        job.say(f"restored {ok} item(s)" + (f", {fail} failed" if fail else "") + (f" to {dest}" if dest else ""))


def _exts(ext):
    return {"jpg": ("jpg", "jpeg", "jpe", "mpo"), "mp4": ("mp4", "m4v", "mov"), "heic": ("heic", "heif"),
            "mkv": ("mkv", "webm"), "mov": ("mov", "mp4", "qt"), "3gp": ("3gp", "3g2", "mp4")}.get(ext, (ext,))


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "unnamed"
