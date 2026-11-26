"""Shared plumbing for every source: ingest a file, label it, log progress."""
import os
import re
import time

from ..core import media, imaging
from ..core.catalog import file_hashes
from ..core import carve as carver

# Path fragments that tell us how a file got where it is.
TRASH_PATTERNS = [
    (re.compile(r"(^|/)\.trashed-\d+-", re.I), "Android trash (MediaStore keeps it ~30 days, then erases it)"),
    (re.compile(r"(^|/)trashed_", re.I), "Android trash file"),
    (re.compile(r"/\$recycle\.bin/", re.I), "Windows Recycle Bin"),
    (re.compile(r"/\.trashes?/", re.I), "Trash folder"),
    (re.compile(r"/\.trashbin/|/\.globaltrash/|/\.recyclebin/|/\.recycle/", re.I), "Gallery app trash"),
    (re.compile(r"/recently[ _-]?deleted", re.I), "Recently Deleted"),
    (re.compile(r"/(trash|bin|recycle bin|deleted items)/", re.I), "Trash folder"),
    (re.compile(r"/lost\.dir/|/lost\+found/|/found\.\d{3}/", re.I), "File-system repair leftovers"),
]
CACHE_PATTERNS = [
    (re.compile(r"/\.?thumbnails?/|/thumbs?/|/\.thumbcache|/thumbcache", re.I), "thumbnail cache"),
    (re.compile(r"/\.statuses/", re.I), "WhatsApp viewed Status cache"),
    (re.compile(r"/(cache|caches|\.cache|cache_data|gpucache|tmp|temp)/", re.I), "app cache"),
    (re.compile(r"/postbox/media/|/telegram/telegram (images|video)/", re.I), "Telegram media cache"),
]
DB_EXTS = (".db", ".sqlite", ".sqlite3", ".db-wal", ".sqlite-wal", ".db-shm", ".wal")


def path_hint(path):
    p = "/" + path.replace("\\", "/")
    for rx, why in TRASH_PATTERNS:
        if rx.search(p):
            return "trash", why
    for rx, why in CACHE_PATTERNS:
        if rx.search(p):
            return ("thumbnail" if "thumb" in why else "cache"), why
    return None, None


class Context:
    """Passed to every source run: catalog access, logging, progress, stop."""

    def __init__(self, catalog, job, scan_id, source):
        self.cat = catalog
        self.job = job
        self.scan_id = scan_id
        self.source = source
        self.found = 0
        self.seen_paths = set()

    def log(self, msg):
        self.job.log(msg)

    def progress(self, done, total, label=""):
        self.job.progress(done, total, label)

    def stopped(self):
        return self.job.stop_requested

    def out_dir(self, *parts):
        return self.cat.dir("recovered", self.source, *parts)

    def ingest_file(self, path, hint, location=None, name=None, evidence=None,
                    taken=None, deleted_at=None, remote=None, kind=None, ext=None,
                    allow_unknown=False, damaged=False):
        """Fingerprint one file and add it to the catalog. Returns item id or None."""
        if path in self.seen_paths:
            return None
        self.seen_paths.add(path)
        if self.cat.known_path(path):  # already catalogued by an earlier scan
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size < 200:
            return None
        sk, se = media.sniff_file(path)
        if sk is None:
            if not allow_unknown:
                return None
            sk = kind or media.ext_kind(path)
            se = ext or os.path.splitext(path)[1].lower()
            if sk is None:
                return None
        kind, ext = sk, se
        try:
            sha256, md5, sha1 = file_hashes(path)
        except OSError:
            return None
        info = media.quick_info(path, kind, ext)
        dh = imaging.image_dhash(path) if kind == "image" else imaging.video_dhash(path)
        w, h = info.get("width"), info.get("height")
        if not w and kind == "image":
            sz = imaging.image_size(path)
            if sz:
                w, h = sz
        damaged = 1 if damaged else 0
        if not damaged and kind == "image" and hint in ("carved", "cache", "thumbnail"):
            ok = imaging.verify_image(path)
            damaged = 1 if ok is False else 0
        if not taken:
            taken = info.get("taken")
        if not taken and hint == "present":
            try:
                taken = time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(os.path.getmtime(path)))
            except OSError:
                pass
        item = dict(scan_id=self.scan_id, source=self.source,
                    location=location or path, path=path,
                    name=name or os.path.basename(path), hint=hint, kind=kind,
                    ext=ext, size=size, sha256=sha256, md5=md5, sha1=sha1, dhash=dh,
                    width=w, height=h, taken=taken, deleted_at=deleted_at,
                    evidence=evidence, remote=remote, damaged=damaged)
        self.found += 1
        return self.cat.add(item)

    def ingest_bytes(self, data, ext, hint, location, name=None, evidence=None,
                     taken=None, deleted_at=None, subdir=""):
        out = self.out_dir(subdir) if subdir else self.out_dir()
        base = name or "recovered"
        base = re.sub(r"[^\w.\-]+", "_", os.path.splitext(base)[0])[:60]
        path = os.path.join(out, "%s_%d%s" % (base, int(time.time() * 1000) % 10**9 + self.found, ext))
        with open(path, "wb") as f:
            f.write(data)
        return self.ingest_file(path, hint, location=location, name=name or os.path.basename(path),
                                evidence=evidence, taken=taken, deleted_at=deleted_at)

    def add_missing(self, name, location, evidence, kind=None, taken=None,
                    deleted_at=None, sha256=None, size=None, remote=None):
        """Record something we know existed but whose bytes are gone."""
        self.found += 1
        return self.cat.add(dict(scan_id=self.scan_id, source=self.source,
                                 location=location, path=None, name=name, hint="missing",
                                 kind=kind, ext=os.path.splitext(name or "")[1].lower(),
                                 size=size, sha256=sha256, taken=taken,
                                 deleted_at=deleted_at, evidence=evidence, remote=remote))

    def carve_file(self, path, location_prefix, hint="carved", evidence=None, min_size=1024):
        """Carve a file/device/db for embedded media; ingest every hit."""
        n = 0
        try:
            total = os.path.getsize(path)
        except OSError:
            total = 0
        for hit in carver.carve(path, min_size=min_size,
                                progress=lambda d, t: self.progress(d, t or total, "carving"),
                                should_stop=self.stopped):
            dest = os.path.join(self.out_dir("carved"), "carved_%s_%012x%s" % (
                re.sub(r"\W+", "_", os.path.basename(path))[:30], hit["offset"], hit["ext"]))
            carver.extract(path, hit["offset"], hit["size"], dest)
            iid = self.ingest_file(dest, hint, damaged=hit.get("truncated", False),
                                   location="%s @ byte %d" % (location_prefix, hit["offset"]),
                                   evidence=evidence or "Rebuilt from raw bytes left behind after deletion")
            if iid:
                n += 1
        return n


def walk(root, ctx, follow_hidden=True):
    """os.walk that tolerates permission errors and respects stop requests."""
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: ctx.log(
            "Skipped (no permission): %s" % getattr(e, "filename", e))):
        if ctx.stopped():
            return
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        for fn in filenames:
            yield os.path.join(dirpath, fn)
