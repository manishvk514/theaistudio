"""Structural parsers for picture and video formats.

A deleted file found in raw space has no name and no size -- the only way to know
where it ends is to *understand the format* and walk its internal structure. Each
`_probe_*` function does exactly that: given an offset it follows the format's own
length fields / markers and returns how long the file is, plus whatever metadata
(dimensions, capture date) the file carries about itself.

`verified=True` means the whole file was integrity-checked (e.g. every PNG chunk
CRC matched). Otherwise the structure was merely consistent from start to end.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

MAX_IMAGE = 256 << 20
MAX_VIDEO = 64 << 30


@dataclass
class Found:
    kind: str            # 'image' | 'video'
    ext: str
    length: int
    width: int = 0
    height: int = 0
    taken: str = ""      # ISO-ish capture time if the file records one
    verified: bool = False
    camera: str = ""
    extra: dict = field(default_factory=dict)


# One C-speed regex finds every candidate start in a buffer.
MAGIC_RE = re.compile(
    rb"\xff\xd8\xff|\x89PNG\r\n\x1a\n|GIF8[79]a|RIFF|ftyp|\x1a\x45\xdf\xa3|BM"
)
MAGIC_LOOKBEHIND = 4  # 'ftyp' sits 4 bytes into the file


def candidates(buf: bytes):
    """Yield offsets in buf where a supported file might begin."""
    for m in MAGIC_RE.finditer(buf):
        off = m.start()
        if buf[off:off + 4] == b"ftyp":
            off -= 4
            if off < 0:
                continue
        yield off


def probe(src, off: int, limit: int | None = None, salvage: bool = False) -> Found | None:
    """Parse the file starting at `off`. Returns None if it is not a complete,
    structurally valid picture/video. With salvage=True a JPEG whose end was
    overwritten is returned too, cut where its structure breaks and marked
    extra['truncated'] (the top of the picture still shows)."""
    avail = src.size - off if limit is None else min(limit, src.size - off)
    if avail < 32:
        return None
    head = src.read(off, 16)
    try:
        if head[:3] == b"\xff\xd8\xff":
            return _probe_jpeg(src, off, avail, salvage)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return _probe_png(src, off, avail)
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return _probe_gif(src, off, avail)
        if head[:4] == b"RIFF":
            return _probe_riff(src, off, avail)
        if head[4:8] == b"ftyp":
            return _probe_bmff(src, off, avail)
        if head[:4] == b"\x1a\x45\xdf\xa3":
            return _probe_ebml(src, off, avail)
        if head[:2] == b"BM":
            return _probe_bmp(src, off, avail)
    except (struct.error, IndexError, ValueError):
        return None
    return None


def sniff(head: bytes) -> tuple[str, str] | None:
    """Cheap (kind, ext) guess from the first bytes of a file."""
    if head[:3] == b"\xff\xd8\xff":
        return "image", "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video", "avi"
    if head[4:8] == b"ftyp":
        return _brand(head[8:12])
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video", "mkv"
    if head[:2] == b"BM" and len(head) >= 16 and head[6:10] == b"\0\0\0\0":
        return "image", "bmp"
    if head[4:8] in (b"moov", b"mdat", b"wide") and head[:4] != b"\0\0\0\0":
        return "video", "mov"
    return None


# ---------------------------------------------------------------- JPEG

_JPEG_SCAN_END = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _probe_jpeg(src, off, avail, salvage=False):
    limit = min(avail, MAX_IMAGE)
    found = _jpeg_walk(src, off, limit)
    if isinstance(found, Found):
        return found
    if salvage and found:
        w, h, taken, camera, end = found
        if w and h and end >= 16384:
            return Found("image", "jpg", end, w, h, taken, False, camera, extra={"truncated": True, "weak": True})
    return None


def _jpeg_walk(src, off, limit):
    """Found for a complete JPEG; otherwise (w, h, taken, camera, last good offset)
    if image data had started, else None."""
    pos = 2
    w = h = 0
    seen_sos = False
    taken = camera = ""
    broken = lambda: (w, h, taken, camera, pos) if seen_sos else None  # noqa: E731
    while pos + 2 <= limit:
        b = src.read(off + pos, 4).ljust(4, b"\0")
        if b[0] != 0xFF:
            return broken()
        m = b[1]
        if m == 0xFF:          # fill byte
            pos += 1
            continue
        if m == 0xD9:
            if not (seen_sos and w and h):
                return broken()
            return Found("image", "jpg", pos + 2, w, h, taken, False, camera)
        if m == 0x01 or 0xD0 <= m <= 0xD7:
            pos += 2
            continue
        if m in (0x00, 0xD8):
            return broken()
        seglen = (b[2] << 8) | b[3]
        if seglen < 2:
            return broken()
        if m in _SOF:
            s = src.read(off + pos + 4, 5)
            if len(s) < 5:
                return None
            h, w = (s[1] << 8) | s[2], (s[3] << 8) | s[4]
            if not w:
                return None
        elif m == 0xE1 and not taken and seglen > 8:
            seg = src.read(off + pos + 4, seglen - 2)
            if seg[:6] == b"Exif\0\0":
                taken, camera = _exif(seg[6:])
        pos += 2 + seglen
        if m == 0xDA:
            seen_sos = True
            # Entropy-coded data: runs until a marker that is not a stuffed
            # zero (FF00) or a restart marker (FFD0-D7).
            while True:
                if pos >= limit:
                    return broken()
                chunk = src.read(off + pos, min(1 << 20, limit - pos))
                if len(chunk) < 2:
                    return broken()
                mm = _JPEG_SCAN_END.search(chunk)
                if mm:
                    pos += mm.start()
                    break
                pos += len(chunk) - 1   # keep 1 byte: a marker may straddle chunks
    return broken()


def _exif(tiff: bytes) -> tuple[str, str]:
    """Pull capture time and camera model out of an EXIF (TIFF) block."""
    try:
        if tiff[:2] == b"II":
            e = "<"
        elif tiff[:2] == b"MM":
            e = ">"
        else:
            return "", ""

        def ifd(o):
            out = {}
            n = struct.unpack_from(e + "H", tiff, o)[0]
            for i in range(min(n, 200)):
                tag, typ, cnt, val = struct.unpack_from(e + "HHI4s", tiff, o + 2 + 12 * i)
                out[tag] = (typ, cnt, val)
            return out

        def text(entry):
            typ, cnt, val = entry
            if typ != 2 or cnt > 256:
                return ""
            raw = val[:cnt] if cnt <= 4 else tiff[struct.unpack(e + "I", val)[0]:][:cnt]
            return raw.split(b"\0")[0].decode("ascii", "replace").strip()

        d0 = ifd(struct.unpack_from(e + "I", tiff, 4)[0])
        make = text(d0[0x010F]) if 0x010F in d0 else ""
        model = text(d0[0x0110]) if 0x0110 in d0 else ""
        camera = model if model.lower().startswith(make.lower()) else (make + " " + model).strip()
        when = text(d0[0x0132]) if 0x0132 in d0 else ""
        if 0x8769 in d0:
            sub = ifd(struct.unpack(e + "I", d0[0x8769][2])[0])
            for tag in (0x9003, 0x9004):
                if tag in sub and text(sub[tag]):
                    when = text(sub[tag])
                    break
        if re.fullmatch(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", when) and not when.startswith("0000"):
            when = when[:10].replace(":", "-") + "T" + when[11:]
        else:
            when = ""
        return when, camera
    except (struct.error, IndexError, KeyError):
        return "", ""


# ---------------------------------------------------------------- PNG

def _probe_png(src, off, avail):
    limit = min(avail, MAX_IMAGE)
    pos = 8
    w = h = 0
    first = True
    while pos + 12 <= limit:
        hd = src.read(off + pos, 8)
        if len(hd) < 8:
            return None
        length = struct.unpack(">I", hd[:4])[0]
        ctype = hd[4:8]
        if length > 0x7FFFFFFF or not ctype.isalpha() or pos + 12 + length > limit:
            return None
        if first and (ctype != b"IHDR" or length != 13):
            return None
        body = src.read(off + pos + 8, length + 4)
        if len(body) < length + 4:
            return None
        if zlib.crc32(ctype + body[:length]) != struct.unpack(">I", body[length:])[0]:
            return None
        if first:
            w, h = struct.unpack(">II", body[:8])
            if not w or not h:
                return None
            first = False
        pos += 12 + length
        if ctype == b"IEND":
            return Found("image", "png", pos, w, h, verified=True)
    return None


# ---------------------------------------------------------------- GIF

def _probe_gif(src, off, avail):
    limit = min(avail, MAX_IMAGE)
    hd = src.read(off, 13)
    w, h, flags = struct.unpack_from("<HHB", hd, 6)
    if not w or not h:
        return None
    pos = 13 + (3 << ((flags & 7) + 1) if flags & 0x80 else 0)
    frames = 0

    def sub_blocks(p):
        while p < limit:
            n = src.read(off + p, 1)
            if not n:
                return None
            if n[0] == 0:
                return p + 1
            p += 1 + n[0]
        return None

    while pos < limit:
        b = src.read(off + pos, 1)
        if not b:
            return None
        if b[0] == 0x3B:
            return Found("image", "gif", pos + 1, w, h) if frames else None
        if b[0] == 0x21:
            pos = sub_blocks(pos + 2)
        elif b[0] == 0x2C:
            d = src.read(off + pos, 10)
            if len(d) < 10:
                return None
            f = d[9]
            pos += 10 + (3 << ((f & 7) + 1) if f & 0x80 else 0) + 1
            pos = sub_blocks(pos)
            frames += 1
        else:
            return None
        if pos is None:
            return None
    return None


# ---------------------------------------------------------------- RIFF (WebP, AVI)

def _probe_riff(src, off, avail):
    hd = src.read(off, 32)
    size = struct.unpack_from("<I", hd, 4)[0]
    form = hd[8:12]
    total = size + 8 + (size & 1)
    if form == b"WEBP":
        if hd[12:16] not in (b"VP8 ", b"VP8L", b"VP8X") or total > min(avail, MAX_IMAGE) or size < 20:
            return None
        w = h = 0
        if hd[12:16] == b"VP8X":
            w = 1 + int.from_bytes(hd[24:27], "little")
            h = 1 + int.from_bytes(hd[27:30], "little")
        elif hd[12:16] == b"VP8 " and hd[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack_from("<HH", hd, 26)
            w, h = w & 0x3FFF, h & 0x3FFF
        elif hd[12:16] == b"VP8L" and hd[20] == 0x2F:
            bits = int.from_bytes(hd[21:25], "little")
            w, h = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return Found("image", "webp", total, w, h, extra={"weak": True})
    if form == b"AVI ":
        if hd[12:16] != b"LIST" or total > avail or size < 64:
            return None
        w, h = struct.unpack_from("<II", src.read(off + 64, 8))  # avih width/height
        # OpenDML: files over 1-2 GB continue in further 'RIFF....AVIX' chunks.
        while total + 12 <= avail:
            nx = src.read(off + total, 12)
            if nx[:4] != b"RIFF" or nx[8:12] != b"AVIX":
                break
            total += struct.unpack_from("<I", nx, 4)[0] + 8
        if total > avail:
            return None
        # Walk the chunk list: every chunk header must be sane and the chain must
        # end exactly at the declared size, or this is not one intact file.
        pos, end = 12, size + 8
        while pos + 8 <= end:
            ch = src.read(off + pos, 8)
            if len(ch) < 8 or not all(32 <= c < 127 for c in ch[:4]):
                return None
            pos += 8 + struct.unpack_from("<I", ch, 4)[0]
            pos += pos & 1
        if pos != end + (end & 1) and pos != end:
            return None
        return Found("video", "avi", total, w if w < 20000 else 0, h if h < 20000 else 0)
    return None


# ---------------------------------------------------------------- ISO BMFF (MP4, MOV, HEIC, 3GP, AVIF)

_TOP_BOXES = {
    b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"meta", b"uuid",
    b"moof", b"mfra", b"sidx", b"styp", b"pnot", b"PICT", b"pdin", b"ssix",
    b"prft", b"emsg", b"udta", b"junk", b"bloc", b"mpvd", b"sdtp",
}
_IMAGE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def _brand(brand: bytes) -> tuple[str, str] | None:
    if brand in _IMAGE_BRANDS:
        return "image", "heic"
    if brand in (b"avif", b"avis"):
        return "image", "avif"
    if brand == b"crx ":
        return "image", "cr3"
    if brand == b"qt  ":
        return "video", "mov"
    if brand[:2] == b"3g":
        return "video", "3gp"
    if brand in (b"M4A ", b"M4B ", b"M4P "):
        return None                      # audio only
    if all(32 <= c < 127 for c in brand):
        return "video", "m4v" if brand == b"M4V " else "mp4"
    return None


def _probe_bmff(src, off, avail):
    hd = src.read(off, 16)
    fsize = struct.unpack(">I", hd[:4])[0]
    if not 16 <= fsize <= 1024 or fsize % 4:
        return None
    kind_ext = _brand(hd[8:12])
    if not kind_ext:
        return None
    kind, ext = kind_ext
    limit = min(avail, MAX_VIDEO)
    pos = 0
    seen = set()
    taken = ""
    w = h = 0
    while pos + 8 <= limit:
        b = src.read(off + pos, 16)
        if len(b) < 8:
            break
        size = struct.unpack(">I", b[:4])[0]
        btype = b[4:8]
        if btype not in _TOP_BOXES:
            break
        if size == 1:
            if len(b) < 16:
                break
            size = struct.unpack(">Q", b[8:16])[0]
            if size < 16:
                break
        elif size == 0:
            break    # "to end of file": meaningless in raw space
        elif size < 8:
            break
        if pos + size > limit:
            return None   # box runs past the readable area: truncated
        if btype == b"moov" and size <= (64 << 20):
            taken, w, h = _moov_meta(src.read(off + pos, size))
        seen.add(btype)
        pos += size
    if pos <= fsize:
        return None
    # A playable file needs its index (moov / meta / fragments), not just data.
    if not (seen & {b"moov", b"meta", b"moof"}):
        return None
    if kind == "video" and not (seen & {b"mdat", b"moof"}):
        return None
    return Found(kind, ext, pos, w, h, taken)


def _moov_meta(moov: bytes) -> tuple[str, int, int]:
    taken = ""
    w = h = 0
    i = moov.find(b"mvhd")
    if i > 0:
        ver = moov[i + 4]
        secs = struct.unpack_from(">Q" if ver == 1 else ">I", moov, i + 8)[0]
        unix = secs - 2082844800   # QuickTime epoch is 1904-01-01
        if 0 < unix < 4102444800:
            import datetime
            taken = datetime.datetime.fromtimestamp(unix, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    i = moov.find(b"tkhd")
    while i > 0:
        ver = moov[i + 4]
        base = i + (92 if ver == 1 else 80)
        if base + 8 <= len(moov):
            tw, th = struct.unpack_from(">II", moov, base)
            if tw >> 16 and th >> 16:
                w, h = tw >> 16, th >> 16
                break
        i = moov.find(b"tkhd", i + 4)
    return taken, w, h


# ---------------------------------------------------------------- Matroska / WebM

def _vint(b: bytes, pos: int, keep_marker: bool):
    first = b[pos]
    n = 1
    mask = 0x80
    while n <= 8 and not first & mask:
        n += 1
        mask >>= 1
    if n > 8:
        raise ValueError
    val = first if keep_marker else first & (mask - 1)
    unknown = (first & (mask - 1)) == mask - 1
    for i in range(1, n):
        val = (val << 8) | b[pos + i]
        unknown = unknown and b[pos + i] == 0xFF
    return val, n, unknown


def _probe_ebml(src, off, avail):
    b = src.read(off, 128)
    hsize, n, _ = _vint(b, 4, False)
    if hsize > 100:
        return None
    header = b[4 + n: 4 + n + hsize]
    ext = "webm" if b"webm" in header else "mkv" if b"matroska" in header else None
    if not ext:
        return None
    pos = 4 + n + hsize
    sid, n1, _ = _vint(b, pos, True)
    if sid != 0x18538067:
        return None
    ssize, n2, unknown = _vint(b, pos + n1, False)
    if unknown:
        return None   # live-streamed file with no recorded length
    total = pos + n1 + n2 + ssize
    if total > avail or ssize < 64:
        return None
    return Found("video", ext, total, extra={"weak": True})


# ---------------------------------------------------------------- BMP

def _probe_bmp(src, off, avail):
    b = src.read(off, 30)
    size, r1, dataoff, dib = struct.unpack_from("<IIII", b, 2)
    if r1 or dib not in (40, 52, 56, 108, 124) or not 26 < dataoff < size or size > min(avail, MAX_IMAGE):
        return None
    w, h, planes, bpp = struct.unpack_from("<iiHH", b, 18)
    if planes != 1 or bpp not in (1, 4, 8, 16, 24, 32) or not 0 < w < 30000 or not 0 < abs(h) < 30000:
        return None
    if size < dataoff + (w * abs(h) * bpp) // 8 // 4:   # allow RLE, reject nonsense
        return None
    return Found("image", "bmp", size, w, abs(h), extra={"weak": True})
