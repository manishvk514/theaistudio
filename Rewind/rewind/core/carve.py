"""File carving: find whole photos and videos inside raw bytes.

First principle: "deleting" a file on most file systems only removes the
pointer to it (the directory entry / inode / MFT record) and marks its blocks
as free. The bytes themselves stay where they were until something else is
written over them. Every photo and video format starts with a recognisable
signature and has an internal structure that tells you where it ends, so we
can walk raw bytes, find each signature and follow the structure to the end.

This works on: disk images, SD cards / USB sticks (when TRIM is not used),
SQLite databases (deleted rows and free pages keep old blobs), SQLite -wal
files, app cache blobs, thumbnail caches, memory dumps, any binary file.

It cannot work where the storage itself wiped or encrypted the blocks:
SSDs after TRIM (most modern laptops, every phone) and file-based-encrypted
phone storage. Those return zeros or ciphertext, never the old photo.

Limit: carving assumes a file's bytes are contiguous. Fragmented files come
out partial; we keep them and flag them as damaged rather than dropping them.
"""
import os
import re
import struct

CHUNK = 32 * 1024 * 1024
OVERLAP = 64

SIGNATURES = re.compile(
    rb"\xff\xd8\xff[\xc0-\xfe]"          # JPEG (SOI + any marker)
    rb"|\x89PNG\r\n\x1a\n"               # PNG
    rb"|GIF8[79]a"                       # GIF
    rb"|RIFF....(?:WEBP|AVI )"           # WebP / AVI
    rb"|....ftyp"                        # MP4 / MOV / 3GP / HEIC / AVIF
    rb"|\x1a\x45\xdf\xa3",               # Matroska / WebM
    re.DOTALL,
)

MAX_SIZES = {".jpg": 80 << 20, ".png": 120 << 20, ".gif": 60 << 20,
             ".webp": 60 << 20, ".mp4": 8 << 30, ".mov": 8 << 30,
             ".heic": 120 << 20, ".avi": 4 << 30, ".mkv": 8 << 30}


class Reader:
    """Random-access reader over a regular file, device or bytes."""

    def __init__(self, src):
        if isinstance(src, (bytes, bytearray, memoryview)):
            self._b = bytes(src)
            self.f = None
            self.size = len(self._b)
        else:
            self._b = None
            self.f = open(src, "rb")
            self.f.seek(0, 2)
            self.size = self.f.tell()
            if self.size == 0:  # some raw devices report 0; read until EOF
                self.size = 1 << 62

    def read(self, off, n):
        if self._b is not None:
            return self._b[off:off + n]
        self.f.seek(off)
        return self.f.read(n)

    def close(self):
        if self.f:
            self.f.close()


# ---------------------------------------------------------------- end finders

_SCAN_OK = {0xC4, 0xDA, 0xDB, 0xDD, 0xFE, 0xC0, 0xC1, 0xC2}


def _jpeg_end(r, start):
    """Follow JPEG segment structure to its EOI marker.

    Returns end offset. If the picture's data stops making sense part-way
    (overwritten or fragmented), returns the last good offset with the
    module flag LAST_TRUNCATED set, so the surviving top of the picture is
    still kept.
    """
    global LAST_TRUNCATED
    LAST_TRUNCATED = False
    limit = min(r.size, start + MAX_SIZES[".jpg"])
    pos = start + 2
    in_scan = False
    scan_start = None
    while pos < limit:
        if not in_scan:
            hdr = r.read(pos, 4)
            bad = len(hdr) < 2 or hdr[0] != 0xFF
            if not bad:
                m = hdr[1]
                if m == 0xFF:
                    pos += 1
                    continue
                if m == 0xD9:
                    return pos + 2
                if m == 0x01 or 0xD0 <= m <= 0xD7:
                    pos += 2
                    continue
                if scan_start is not None and m not in _SCAN_OK:
                    bad = True
                elif len(hdr) < 4 or struct.unpack(">H", hdr[2:4])[0] < 2:
                    bad = True
            if bad:
                if scan_start is not None and pos - scan_start > 4096:
                    LAST_TRUNCATED = True
                    return pos
                return None
            seglen = struct.unpack(">H", hdr[2:4])[0]
            pos += 2 + seglen
            if m == 0xDA:
                in_scan = True
                if scan_start is None:
                    scan_start = pos
            continue
        buf = r.read(pos, 1 << 20)
        if not buf:
            if scan_start is not None and pos - scan_start > 4096:
                LAST_TRUNCATED = True
                return min(pos, r.size)
            return None
        j = 0
        while True:
            j = buf.find(b"\xff", j)
            if j < 0 or j + 1 >= len(buf):
                pos += max(len(buf) - 1, 1)
                break
            nxt = buf[j + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
                j += 1
                continue
            if nxt == 0xD9:
                return pos + j + 2
            pos = pos + j
            in_scan = False
            break
    return None


LAST_TRUNCATED = False


def _png_end(r, start):
    limit = min(r.size, start + MAX_SIZES[".png"])
    pos = start + 8
    while pos + 12 <= limit:
        hdr = r.read(pos, 8)
        if len(hdr) < 8:
            return None
        length, ctype = struct.unpack(">I4s", hdr)
        if not ctype.isalpha() or length > MAX_SIZES[".png"]:
            return None
        pos += 12 + length
        if ctype == b"IEND":
            return pos
    return None


def _gif_end(r, start):
    limit = min(r.size, start + MAX_SIZES[".gif"])
    hdr = r.read(start, 13)
    if len(hdr) < 13:
        return None
    flags = hdr[10]
    pos = start + 13
    if flags & 0x80:
        pos += 3 * (2 ** ((flags & 7) + 1))

    def skip_subblocks(p):
        while p < limit:
            b = r.read(p, 1)
            if not b:
                return None
            if b[0] == 0:
                return p + 1
            p += 1 + b[0]
        return None

    while pos < limit:
        b = r.read(pos, 1)
        if not b:
            return None
        t = b[0]
        if t == 0x3B:
            return pos + 1
        if t == 0x21:
            pos = skip_subblocks(pos + 2)
        elif t == 0x2C:
            d = r.read(pos, 10)
            if len(d) < 10:
                return None
            lflags = d[9]
            pos += 10
            if lflags & 0x80:
                pos += 3 * (2 ** ((lflags & 7) + 1))
            pos = skip_subblocks(pos + 1)
        else:
            return None
        if pos is None:
            return None
    return None


def _riff_end(r, start):
    hdr = r.read(start, 12)
    size = struct.unpack("<I", hdr[4:8])[0]
    if size < 12:
        return None
    return start + 8 + size + (size & 1)


_TOP_BOXES = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"uuid",
              b"meta", b"pnot", b"udta", b"moof", b"mfra", b"sidx", b"styp",
              b"pdin", b"prft", b"emsg", b"meco", b"junk", b"PICT", b"Xtra",
              b"ssix", b"mvex", b"iinf", b"iloc", b"idat", b"iref"}


def _bmff_end(r, start):
    pos = start
    seen = set()
    limit = min(r.size, start + MAX_SIZES[".mp4"])
    while pos + 8 <= limit:
        hdr = r.read(pos, 16)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr[:8])
        if typ not in _TOP_BOXES:
            break
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack(">Q", hdr[8:16])[0]
        elif size == 0:
            size = limit - pos
        if size < 8:
            break
        seen.add(typ)
        pos += size
    if b"ftyp" not in seen:
        return None
    if not (seen & {b"moov", b"mdat", b"meta", b"moof"}):
        return None
    return min(pos, r.size)


def _vint(r, pos):
    b = r.read(pos, 1)
    if not b:
        return None, 0
    first = b[0]
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8:
        return None, 0
    raw = r.read(pos, length)
    val = first & (mask - 1)
    for x in raw[1:]:
        val = (val << 8) | x
    unknown = val == (1 << (7 * length)) - 1
    return (None if unknown else val), length


def _ebml_end(r, start):
    pos = start + 4
    size, ln = _vint(r, pos)
    if size is None:
        return None
    pos += ln + size
    seg = r.read(pos, 4)
    if seg != b"\x18\x53\x80\x67":
        return None
    size, ln = _vint(r, pos + 4)
    if size is None:  # unknown-size live stream: cap
        return min(r.size, pos + MAX_SIZES[".mkv"])
    return pos + 4 + ln + size


def identify(r, off):
    """Return (ext, end) for a signature at off, or (None, None)."""
    head = r.read(off, 16)
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg", _jpeg_end(r, off)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", _png_end(r, off)
    if head[:4] == b"GIF8":
        return ".gif", _gif_end(r, off)
    if head[:4] == b"RIFF":
        ext = ".webp" if head[8:12] == b"WEBP" else ".avi"
        return ext, _riff_end(r, off)
    if head[4:8] == b"ftyp":
        size = struct.unpack(">I", head[:4])[0]
        if not 8 <= size <= 512:
            return None, None
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"avif", b"hevc"):
            ext = ".heic"
        elif brand == b"qt  ":
            ext = ".mov"
        else:
            ext = ".mp4"
        return ext, _bmff_end(r, off)
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".mkv", _ebml_end(r, off)
    return None, None


def carve(src, min_size=1024, progress=None, should_stop=None):
    """Yield dicts {offset, size, ext, data_reader} for media found in src.

    src: file path / device path / bytes. Results whose byte ranges sit inside
    an already-found file (e.g. EXIF thumbnails inside a JPEG) are skipped.
    """
    r = Reader(src)
    try:
        pos = 0
        covered_until = 0
        while pos < r.size:
            if should_stop and should_stop():
                return
            buf = r.read(pos, CHUNK + OVERLAP)
            if not buf:
                break
            for m in SIGNATURES.finditer(buf):
                off = pos + m.start()
                if off < covered_until or m.start() >= CHUNK:
                    continue
                ext, end = identify(r, off)
                if not ext or not end:
                    continue
                size = end - off
                if size < min_size or size > MAX_SIZES.get(ext, 1 << 30):
                    continue
                covered_until = end
                yield {"offset": off, "size": size, "ext": ext,
                       "truncated": ext == ".jpg" and LAST_TRUNCATED}
            pos += CHUNK
            if progress:
                progress(min(pos, r.size), r.size)
            if len(buf) < CHUNK:
                break
    finally:
        r.close()


def extract(src, offset, size, dest):
    r = Reader(src)
    try:
        with open(dest, "wb") as out:
            remaining, p = size, offset
            while remaining > 0:
                chunk = r.read(p, min(remaining, 8 << 20))
                if not chunk:
                    break
                out.write(chunk)
                p += len(chunk)
                remaining -= len(chunk)
    finally:
        r.close()
    return dest


def carve_bytes(data, min_size=256):
    """Carve media out of an in-memory blob; returns list of (ext, bytes)."""
    out = []
    for hit in carve(data, min_size=min_size):
        out.append((hit["ext"], data[hit["offset"]:hit["offset"] + hit["size"]]))
    return out
