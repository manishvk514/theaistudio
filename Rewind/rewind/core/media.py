"""Identify photo and video files by their bytes, not by their names.

First principle: a file name and extension are just labels. Caches, carved
fragments and app databases often hold perfectly good photos with no
extension at all, so every decision here is made from the file's magic bytes.
"""
import os
import struct
import datetime as _dt

IMAGE = "image"
VIDEO = "video"

# ISO-BMFF brands that mean "still image" (HEIC/HEIF/AVIF)
_IMAGE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1",
                 b"msf1", b"avif", b"avis", b"jpeg"}

MEDIA_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".heic", ".heif", ".avif",
    ".bmp", ".tif", ".tiff", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf",
    ".orf", ".rw2", ".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".mkv", ".webm",
    ".avi", ".wmv", ".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".thm",
}

BROWSER_OK = {".jpg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".webm", ".mov",
              ".m4v", ".heic", ".avif"}


def sniff(head):
    """Return (kind, ext) for the first bytes of a file, or (None, None)."""
    if len(head) < 12:
        return None, None
    if head[:3] == b"\xff\xd8\xff":
        return IMAGE, ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return IMAGE, ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return IMAGE, ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return IMAGE, ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return VIDEO, ".avi"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _IMAGE_BRANDS:
            return IMAGE, ".avif" if brand in (b"avif", b"avis") else ".heic"
        if brand == b"qt  ":
            return VIDEO, ".mov"
        if brand.startswith(b"3g"):
            return VIDEO, ".3gp"
        if brand.startswith(b"crx"):
            return IMAGE, ".cr3"
        return VIDEO, ".mp4"
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free") and _plausible_box(head):
        return VIDEO, ".mov"
    if head[:4] == b"\x1aE\xdf\xa3":
        return VIDEO, ".webm" if b"webm" in head[:64] else ".mkv"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return IMAGE, ".tif"
    if head[:2] == b"BM" and len(head) >= 26:
        size = struct.unpack("<I", head[2:6])[0]
        hdr = struct.unpack("<I", head[14:18])[0]
        if hdr in (12, 40, 52, 56, 108, 124) and 26 < size < 200_000_000:
            return IMAGE, ".bmp"
    if head[:4] == b"\x30\x26\xb2\x75":
        return VIDEO, ".wmv"
    if head[:4] == b"\x00\x00\x01\xba":
        return VIDEO, ".mpg"
    return None, None


def _plausible_box(head):
    size = struct.unpack(">I", head[:4])[0]
    return size >= 8


def sniff_file(path):
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None, None
    return sniff(head)


def ext_kind(path):
    e = os.path.splitext(path)[1].lower()
    if e not in MEDIA_EXTS:
        return None
    if e in {".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".mkv", ".webm", ".avi",
             ".wmv", ".mts", ".m2ts", ".ts", ".mpg", ".mpeg"}:
        return VIDEO
    return IMAGE


# --------------------------------------------------------------------------
# Lightweight metadata: dimensions and "taken" time without any dependency.
# --------------------------------------------------------------------------

def jpeg_info(data):
    """Return dict(width,height,taken) from JPEG bytes (best effort)."""
    out = {}
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i + 4:i + 2 + seglen]
        if marker == 0xE1 and seg[:6] == b"Exif\x00\x00" and "taken" not in out:
            t = _exif_datetime(seg[6:])
            if t:
                out["taken"] = t
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA,
                      0xCB, 0xCD, 0xCE, 0xCF) and len(seg) >= 5:
            out["height"], out["width"] = struct.unpack(">HH", seg[1:5])
            break
        if marker == 0xDA:
            break
        i += 2 + seglen
    return out


def _exif_datetime(tiff):
    try:
        endian = "<" if tiff[:2] == b"II" else ">"
        ifd0 = struct.unpack(endian + "I", tiff[4:8])[0]

        def read_ifd(off):
            cnt = struct.unpack(endian + "H", tiff[off:off + 2])[0]
            tags = {}
            for k in range(cnt):
                e = off + 2 + 12 * k
                tag, typ, count, val = struct.unpack(endian + "HHII", tiff[e:e + 12])
                tags[tag] = (typ, count, val, e + 8)
            return tags

        def ascii_at(t):
            typ, count, val, raw = t
            if count <= 4:
                s = tiff[raw:raw + count]
            else:
                s = tiff[val:val + count]
            return s.split(b"\x00")[0].decode("ascii", "ignore")

        tags = read_ifd(ifd0)
        best = None
        if 0x8769 in tags:  # Exif sub-IFD
            sub = read_ifd(tags[0x8769][2])
            for tag in (0x9003, 0x9004):  # DateTimeOriginal, Digitized
                if tag in sub:
                    best = ascii_at(sub[tag])
                    break
        if not best and 0x0132 in tags:
            best = ascii_at(tags[0x0132])
        if best:
            dt = _dt.datetime.strptime(best.strip()[:19], "%Y:%m:%d %H:%M:%S")
            if 1990 < dt.year < 2100:
                return dt.isoformat()
    except Exception:
        return None
    return None


def png_info(data):
    if len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return {"width": w, "height": h}
    return {}


def gif_info(data):
    if len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return {"width": w, "height": h}
    return {}


def bmff_info(path_or_bytes, limit=64 * 1024 * 1024):
    """Read creation time (and duration) from an MP4/MOV 'mvhd' box."""
    out = {}
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            data = bytes(path_or_bytes[:limit])
            out.update(_walk_mvhd_bytes(data))
        else:
            with open(path_or_bytes, "rb") as f:
                out.update(_walk_mvhd_file(f))
    except Exception:
        pass
    return out


_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta"}
_EPOCH_1904 = _dt.datetime(1904, 1, 1)


def _parse_mvhd(body):
    ver = body[0]
    if ver == 1:
        ctime, _mtime, scale, dur = struct.unpack(">QQIQ", body[4:32])
    else:
        ctime, _mtime, scale, dur = struct.unpack(">IIII", body[4:20])
    out = {}
    if ctime > 0:
        t = _EPOCH_1904 + _dt.timedelta(seconds=ctime)
        if 1995 < t.year < 2100:
            out["taken"] = t.isoformat()
    if scale:
        out["duration"] = round(dur / scale, 2)
    return out


def _walk_mvhd_file(f):
    f.seek(0, 2)
    end = f.tell()
    pos = 0
    stack_end = [end]
    while pos + 8 <= stack_end[-1]:
        f.seek(pos)
        hdr = f.read(16)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr[:8])
        hlen = 8
        if size == 1:
            size = struct.unpack(">Q", hdr[8:16])[0]
            hlen = 16
        elif size == 0:
            size = stack_end[-1] - pos
        if size < hlen:
            break
        if typ == b"mvhd":
            f.seek(pos + hlen)
            return _parse_mvhd(f.read(min(size - hlen, 120)))
        if typ == b"moov":
            stack_end.append(pos + size)
            pos += hlen
            continue
        pos += size
        while len(stack_end) > 1 and pos >= stack_end[-1]:
            stack_end.pop()
    return {}


def _walk_mvhd_bytes(data):
    i = data.find(b"mvhd")
    if i >= 4:
        return _parse_mvhd(data[i + 4:i + 124])
    return {}


def quick_info(path, kind, ext):
    """Best-effort width/height/taken for a file on disk, no dependencies."""
    try:
        if kind == VIDEO or ext in (".mp4", ".mov", ".3gp", ".m4v"):
            return bmff_info(path)
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
        if ext == ".jpg":
            return jpeg_info(head)
        if ext == ".png":
            return png_info(head)
        if ext == ".gif":
            return gif_info(head)
    except OSError:
        pass
    return {}
