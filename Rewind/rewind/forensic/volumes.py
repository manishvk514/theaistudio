"""Find the volumes inside a disk / image and open the right filesystem parser."""
from __future__ import annotations

import struct

from .exfat import ExfatVolume
from .fat import FatVolume
from .ntfs import NtfsVolume
from .source import SubSource


def open_fs(src):
    """Return a filesystem object for src, or (None, description)."""
    b = src.read(0, 4096)
    if len(b) < 512:
        return None, "empty"
    for cls in (NtfsVolume, ExfatVolume, FatVolume):
        try:
            if cls is NtfsVolume and b[3:11] != b"NTFS    ":
                continue
            if cls is ExfatVolume and b[3:11] != b"EXFAT   ":
                continue
            return cls(src), None
        except (ValueError, struct.error, IndexError):
            continue
    sb = src.read(1024, 128)
    if len(sb) >= 60 and sb[56:58] == b"\x53\xef":
        return None, "ext2/3/4 (Linux/Android): deletion zeroes the block map, so only raw carving is possible"
    if b[32:36] == b"NXSB":
        return None, "APFS (Apple): encrypted/copy-on-write, raw carving only"
    if src.read(1024, 2) in (b"H+", b"HX"):
        return None, "HFS+ (older Mac): raw carving only"
    if b[:4] == b"LUKS" or b[3:11] == b"-FVE-FS-":
        return None, "encrypted volume (LUKS/BitLocker): unlock it first, nothing is recoverable from ciphertext"
    return None, "no known filesystem"


def partitions(src):
    """Yield (label, SubSource). Falls back to the whole source."""
    b = src.read(0, 512)
    found = []
    is_boot = b[3:11] in (b"NTFS    ", b"EXFAT   ") or (b[510:512] == b"\x55\xaa" and b[0] in (0xEB, 0xE9) and b[11:13] in (b"\x00\x02", b"\x00\x04", b"\x00\x08", b"\x00\x10"))
    if len(b) == 512 and b[510:512] == b"\x55\xaa" and not is_boot:
        for i in range(4):
            e = b[446 + 16 * i:462 + 16 * i]
            ptype = e[4]
            lba, count = struct.unpack_from("<II", e, 8)
            if ptype == 0xEE:
                found = list(_gpt(src))
                break
            if ptype and count and lba * 512 < src.size:
                found.append((f"partition {i + 1}", SubSource(src, lba * 512, count * 512)))
    if not found:
        found = [("volume", SubSource(src, 0, src.size))]
    return found


def _gpt(src):
    for sector in (512, 4096):
        h = src.read(sector, 92)
        if h[:8] != b"EFI PART":
            continue
        lba, = struct.unpack_from("<Q", h, 72)
        n, esize = struct.unpack_from("<II", h, 80)
        table = src.read(lba * sector, min(n, 256) * esize)
        for i in range(min(n, 256)):
            e = table[i * esize:(i + 1) * esize]
            if len(e) < 56 or e[:16] == b"\0" * 16:
                continue
            first, last = struct.unpack_from("<QQ", e, 32)
            name = e[56:128].decode("utf-16-le", "replace").split("\0")[0]
            if first * sector < src.size and last >= first:
                yield (name or f"partition {i + 1}", SubSource(src, first * sector, (last - first + 1) * sector))
        return
