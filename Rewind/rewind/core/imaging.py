"""Thumbnails and perceptual fingerprints, with whatever decoder exists.

Order of preference: Pillow (pip), then tools already on the machine:
macOS `sips` / `qlmanage`, then `ffmpeg`. The app keeps working with none of
them; it just falls back to exact-byte matching.

Perceptual fingerprint = dHash (64 bits). Two copies of the same photo at
different sizes or qualities (an original and its thumbnail, a WhatsApp
re-compression) land within a few bits of each other, while byte hashes
would differ completely.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

try:
    from PIL import Image, ImageOps  # type: ignore
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

FFMPEG = shutil.which("ffmpeg")
SIPS = shutil.which("sips") if sys.platform == "darwin" else None
QLMANAGE = shutil.which("qlmanage") if sys.platform == "darwin" else None


def backends():
    return {"pillow": HAVE_PIL, "ffmpeg": bool(FFMPEG), "sips": bool(SIPS),
            "qlmanage": bool(QLMANAGE)}


def _dhash_from_gray(px, w=9, h=8):
    bits = 0
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        for x in range(w - 1):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def _run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout)
    except Exception:
        return None


def _bmp_gray(path):
    with open(path, "rb") as f:
        d = f.read()
    off = struct.unpack("<I", d[10:14])[0]
    w, h = struct.unpack("<ii", d[18:26])
    bpp = struct.unpack("<H", d[28:30])[0]
    bpr = ((w * bpp + 31) // 32) * 4
    px = []
    rows = range(abs(h) - 1, -1, -1) if h > 0 else range(abs(h))
    for y in rows:
        base = off + y * bpr
        for x in range(w):
            p = base + x * (bpp // 8)
            b, g, r = d[p], d[p + 1], d[p + 2]
            px.append((r * 299 + g * 587 + b * 114) // 1000)
    return px, w, abs(h)


def image_dhash(path):
    """64-bit dHash of an image file, or None."""
    if HAVE_PIL:
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("L").resize((9, 8))
                return _dhash_from_gray(list(im.getdata()))
        except Exception:
            pass
    if FFMPEG:
        r = _run([FFMPEG, "-v", "error", "-i", path, "-frames:v", "1", "-vf",
                  "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"])
        if r and len(r.stdout) >= 72:
            return _dhash_from_gray(list(r.stdout[:72]))
    if SIPS:
        tmp = tempfile.mktemp(suffix=".bmp")
        try:
            r = _run([SIPS, "-s", "format", "bmp", "-z", "8", "9", path, "--out", tmp])
            if r and os.path.exists(tmp):
                px, w, h = _bmp_gray(tmp)
                if w == 9 and h == 8:
                    return _dhash_from_gray(px)
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return None


def video_frame(path, dest, size=360):
    """Write one representative JPEG frame of a video to dest. True if ok."""
    if FFMPEG:
        for ss in ("1", "0"):
            r = _run([FFMPEG, "-v", "error", "-y", "-ss", ss, "-i", path, "-frames:v",
                      "1", "-vf", f"scale={size}:-2", dest], timeout=90)
            if r and os.path.exists(dest) and os.path.getsize(dest) > 0:
                return True
    if QLMANAGE:
        outdir = tempfile.mkdtemp()
        _run([QLMANAGE, "-t", "-s", str(size), "-o", outdir, path], timeout=60)
        for name in os.listdir(outdir):
            shutil.move(os.path.join(outdir, name), dest)
            shutil.rmtree(outdir, ignore_errors=True)
            return True
        shutil.rmtree(outdir, ignore_errors=True)
    return False


def video_dhash(path):
    tmp = tempfile.mktemp(suffix=".jpg")
    try:
        if video_frame(path, tmp, size=64):
            return image_dhash(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return None


def image_size(path):
    if HAVE_PIL:
        try:
            with Image.open(path) as im:
                return im.size
        except Exception:
            return None
    return None


def verify_image(path):
    """True if decodable, False if broken, None if we cannot tell."""
    if HAVE_PIL:
        try:
            with Image.open(path) as im:
                im.load()
            return True
        except Exception:
            return False
    return None


def make_thumb(path, kind, dest, size=320):
    """Create a JPEG thumbnail; return dest or None."""
    if kind == "video":
        return dest if video_frame(path, dest, size) else None
    if HAVE_PIL:
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((size, size))
                im.convert("RGB").save(dest, "JPEG", quality=80)
                return dest
        except Exception:
            pass
    if SIPS:
        r = _run([SIPS, "-s", "format", "jpeg", "-Z", str(size), path, "--out", dest])
        if r and os.path.exists(dest):
            return dest
    if FFMPEG:
        r = _run([FFMPEG, "-v", "error", "-y", "-i", path, "-frames:v", "1", "-vf",
                  f"scale='min({size},iw)':-2", dest])
        if r and os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
    return None


def hamming(a, b):
    return bin(a ^ b).count("1")
