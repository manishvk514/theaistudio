"""NTFS (Windows system disks, most external hard drives).

Every file is a record in the Master File Table. Deleting a file clears one flag
bit in its record ("in use") and frees its clusters in $Bitmap -- the record
itself, with the name, timestamps, exact size and the full *run list* saying
where each fragment of the content lives, stays until the slot is recycled for a
new file. So a deleted NTFS file can be rebuilt exactly, fragmentation included,
as long as its record and clusters have not been reused.

Files emptied from the Recycle Bin are records named $Rxxxxxx.ext; the matching
$Ixxxxxx.ext record (tiny, stored inside the MFT) still holds the original path.
"""
from __future__ import annotations

import struct

from .fsbase import FsEntry, filetime, merge_runs


class NtfsVolume:
    kind = "NTFS"
    label = ""

    def __init__(self, src):
        self.src = src
        b = src.read(0, 512)
        if len(b) < 512 or b[3:11] != b"NTFS    ":
            raise ValueError("not NTFS")
        self.bps, spc = struct.unpack_from("<HB", b, 11)
        if spc > 128:
            spc = 1 << (256 - spc)
        self.cluster_size = self.bps * spc
        self.total = struct.unpack_from("<Q", b, 40)[0] * self.bps
        mft_lcn = struct.unpack_from("<Q", b, 48)[0]
        rs = struct.unpack_from("<b", b, 64)[0]
        self.rec_size = 1 << -rs if rs < 0 else rs * self.cluster_size
        if self.bps not in (512, 1024, 2048, 4096) or not 256 <= self.rec_size <= 65536:
            raise ValueError("not NTFS")
        rec0 = self._fix(src.read(mft_lcn * self.cluster_size, self.rec_size))
        if not rec0:
            raise ValueError("unreadable $MFT")
        data = self._attrs(rec0).get("data")
        self.mft_runs = data["runs"] if data and data.get("runs") else [(mft_lcn * self.cluster_size, self.rec_size * 64)]
        self.mft_size = sum(l for _, l in self.mft_runs)
        if data and data.get("size"):
            self.mft_size = min(self.mft_size, data["size"])
        self.bitmap = b""
        rec6 = self.record(6)
        if rec6:
            bm = self._attrs(rec6).get("data")
            if bm and bm.get("runs"):
                self.bitmap = b"".join(src.read(o, l) for o, l in bm["runs"])[:bm["size"]]
        self._names = {}

    # -- low level ---------------------------------------------------------

    def _fix(self, rec: bytes):
        """Apply the update-sequence fixups that protect each sector of a record."""
        if len(rec) < self.rec_size or rec[:4] != b"FILE":
            return None
        rec = bytearray(rec)
        uoff, ucnt = struct.unpack_from("<HH", rec, 4)
        if uoff + ucnt * 2 > len(rec) or ucnt < 2:
            return None
        for i in range(1, ucnt):
            end = i * 512
            if end > len(rec):
                break
            rec[end - 2:end] = rec[uoff + 2 * i:uoff + 2 * i + 2]
        return bytes(rec)

    def record(self, n: int):
        off = n * self.rec_size
        if off + self.rec_size > self.mft_size:
            return None
        pos = 0
        for ro, rl in self.mft_runs:
            if off < pos + rl:
                return self._fix(self.src.read(ro + off - pos, self.rec_size))
            pos += rl
        return None

    def _runlist(self, b: bytes, real: int):
        runs, lcn, pos, sparse = [], 0, 0, False
        while pos < len(b) and b[pos]:
            ls, os_ = b[pos] & 15, b[pos] >> 4
            pos += 1
            if ls == 0 or pos + ls + os_ > len(b):
                break
            length = int.from_bytes(b[pos:pos + ls], "little")
            pos += ls
            if os_ == 0:
                sparse = True
                runs.append((None, length * self.cluster_size))
            else:
                lcn += int.from_bytes(b[pos:pos + os_], "little", signed=True)
                runs.append((lcn * self.cluster_size, length * self.cluster_size))
            pos += os_
        return runs, sparse

    def _attrs(self, rec: bytes) -> dict:
        out = {"names": []}
        pos, = struct.unpack_from("<H", rec, 20)
        while pos + 16 <= len(rec):
            atype, alen = struct.unpack_from("<II", rec, pos)
            if atype == 0xFFFFFFFF or alen < 16 or pos + alen > len(rec):
                break
            nonres, namelen = rec[pos + 8], rec[pos + 9]
            flags, = struct.unpack_from("<H", rec, pos + 12)
            if not nonres:
                clen, coff = struct.unpack_from("<IH", rec, pos + 16)
                content = rec[pos + coff:pos + coff + clen]
            else:
                content = None
            if atype == 0x10 and content and len(content) >= 16:
                out["created"], out["modified"] = struct.unpack_from("<QQ", content)
            elif atype == 0x30 and content and len(content) >= 66:
                parent = struct.unpack_from("<Q", content)[0] & 0xFFFFFFFFFFFF
                n, ns = content[64], content[65]
                out["names"].append((ns, parent, content[66:66 + n * 2].decode("utf-16-le", "replace")))
            elif atype == 0x20:
                out["attr_list"] = True
            elif atype == 0x80 and namelen == 0:
                if not nonres:
                    out["data"] = {"resident": content, "size": len(content)}
                else:
                    vcn0, = struct.unpack_from("<Q", rec, pos + 16)
                    roff, = struct.unpack_from("<H", rec, pos + 32)
                    real, = struct.unpack_from("<Q", rec, pos + 48)
                    runs, sparse = self._runlist(rec[pos + roff:pos + alen], real)
                    if vcn0 == 0:
                        out["data"] = {"runs": runs, "size": real, "sparse": sparse,
                                       "special": bool(flags & 0xC001)}   # compressed / encrypted
            pos += alen
        return out

    def is_free(self, off: int) -> bool:
        c = off // self.cluster_size
        return c // 8 < len(self.bitmap) and not self.bitmap[c // 8] >> (c % 8) & 1

    def free_runs(self):
        runs, start = [], None
        n = min(len(self.bitmap) * 8, self.total // self.cluster_size)
        i = 0
        while i < n:
            byte = self.bitmap[i // 8]
            if i % 8 == 0 and byte in (0, 0xFF) and i + 8 <= n:   # fast path
                free = byte == 0
                step = 8
            else:
                free = not byte >> (i % 8) & 1
                step = 1
            if free and start is None:
                start = i
            elif not free and start is not None:
                runs.append((start * self.cluster_size, (i - start) * self.cluster_size))
                start = None
            i += step
        if start is not None:
            runs.append((start * self.cluster_size, (n - start) * self.cluster_size))
        return runs

    def _best_name(self, attrs):
        names = sorted(attrs["names"], key=lambda t: t[0] == 2)   # prefer non-DOS names
        return (names[0][2], names[0][1]) if names else ("", 0)

    def _path(self, parent: int) -> str:
        parts, seen = [], set()
        while parent not in (5, 0) and parent not in seen and len(parts) < 40:
            seen.add(parent)
            if parent not in self._names:
                rec = self.record(parent)
                self._names[parent] = self._best_name(self._attrs(rec)) if rec else ("", 5)
            name, up = self._names[parent]
            if not name:
                parts.append("?")
                break
            parts.append(name)
            parent = up
        return "/" + "/".join(reversed(parts))

    # -- enumeration -------------------------------------------------------

    def entries(self):
        total = self.mft_size // self.rec_size
        recycle_names = {}     # '$Rxxxx.ext' -> original path, from deleted $I records
        pending = []
        for n in range(24, total):
            rec = self.record(n)
            if not rec:
                continue
            flags, = struct.unpack_from("<H", rec, 22)
            base_ref = struct.unpack_from("<Q", rec, 32)[0] & 0xFFFFFFFFFFFF
            if flags & 2 or base_ref:
                continue               # directory, or extension record
            a = self._attrs(rec)
            name, parent = self._best_name(a)
            data = a.get("data")
            if not name or not data or not data["size"]:
                continue
            deleted = not flags & 1
            if name.upper().startswith("$I") and "resident" in data:
                orig = _parse_recycle_info(data["resident"])
                if orig:
                    recycle_names["$R" + name[2:]] = orig
                continue
            pending.append((name, parent, a, data, deleted))
        for name, parent, a, data, deleted in pending:
            size = data["size"]
            intact, note, extra = "good", "", {}
            if "resident" in data:
                runs = []
                extra["inline"] = data["resident"]
            else:
                runs = [(o, l) for o, l in data["runs"]]
                if data.get("special") or data.get("sparse"):
                    intact, note = "overwritten", "compressed/encrypted/sparse file (not supported)"
                    runs = []
                elif a.get("attr_list"):
                    intact, note = "partial", "extremely fragmented file: only the first extent map is read"
                if sum(l for _, l in runs) < size and intact == "good":
                    intact, note = "partial", "run list shorter than file size"
                if deleted and runs and intact != "overwritten":
                    nclu = sum(l for _, l in runs) // self.cluster_size
                    reused = sum(1 for o, l in runs for c in range(o, o + l, self.cluster_size) if not self.is_free(c))
                    if reused and not self.is_free(runs[0][0]):
                        intact, note = "overwritten", "clusters reused by newer files"
                    elif reused:
                        intact, note = "partial", f"{reused} of {nclu} clusters reused by newer files"
            path = self._path(parent)
            if name.upper().startswith("$R") and name in recycle_names:
                orig = recycle_names[name].replace("\\", "/")
                path, _, name = orig.rpartition("/")
                note = (note + "; " if note else "") + "emptied from Recycle Bin"
            yield FsEntry(name, path, size, deleted, merge_runs(runs),
                          modified=filetime(a.get("modified", 0)), created=filetime(a.get("created", 0)),
                          intact=intact, note=note, extra=extra)


def _parse_recycle_info(b: bytes) -> str:
    """$I file from the Windows Recycle Bin -> original full path."""
    try:
        ver, = struct.unpack_from("<Q", b, 0)
        if ver == 2:
            n, = struct.unpack_from("<I", b, 24)
            raw = b[28:28 + n * 2]
        elif ver == 1:
            raw = b[24:24 + 520]
        else:
            return ""
        return raw.decode("utf-16-le", "replace").split("\0")[0]
    except struct.error:
        return ""


def parse_recycle_info(b: bytes) -> tuple[str, str]:
    """(original path, deletion time) from a $I file."""
    path = _parse_recycle_info(b)
    when = filetime(struct.unpack_from("<Q", b, 16)[0]) if len(b) >= 24 else ""
    return path, when
