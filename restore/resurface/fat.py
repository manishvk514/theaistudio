"""FAT12/16/32 -- what deletion really does, and how to reverse it.

Deleting a file on FAT changes exactly two things:
  1. the first byte of its directory entry becomes 0xE5 ("this slot is free"),
  2. its clusters are marked free (0) in the allocation table.
The rest of the directory entry -- name (minus one letter), size, timestamps and
the *starting cluster* -- stays, and so does every byte of the content until
something new is written over it. The cluster chain is lost, so we rebuild it:
start at the recorded cluster and take the following clusters that are still
free, skipping any that belong to live files (the fragments of a file written
around existing data are separated by exactly those in-use clusters).
"""
from __future__ import annotations

import struct

from .fsbase import FsEntry, dos_time, merge_runs


class FatVolume:
    kind = "FAT"

    def __init__(self, src):
        self.src = src
        b = src.read(0, 512)
        if len(b) < 512 or b[510:512] != b"\x55\xaa":
            raise ValueError("not FAT")
        self.bps, self.spc, self.reserved, self.nfats, self.root_entries = struct.unpack_from("<HBHBH", b, 11)
        total16, _, fatsz16 = struct.unpack_from("<HBH", b, 19)
        total32 = struct.unpack_from("<I", b, 32)[0]
        fatsz32 = struct.unpack_from("<I", b, 36)[0]
        if self.bps not in (512, 1024, 2048, 4096) or self.spc not in (1, 2, 4, 8, 16, 32, 64, 128) \
                or not self.reserved or self.nfats not in (1, 2):
            raise ValueError("not FAT")
        self.fatsz = fatsz16 or fatsz32
        total = total16 or total32
        if not self.fatsz or not total:
            raise ValueError("not FAT")
        self.root_secs = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.data_start = (self.reserved + self.nfats * self.fatsz + self.root_secs) * self.bps
        self.cluster_size = self.bps * self.spc
        self.nclusters = (total * self.bps - self.data_start) // self.cluster_size
        if self.nclusters <= 0:
            raise ValueError("not FAT")
        self.bits = 12 if self.nclusters < 4085 else 16 if self.nclusters < 65525 else 32
        self.kind = f"FAT{self.bits}"
        self.root_cluster = struct.unpack_from("<I", b, 44)[0] if self.bits == 32 else 0
        self.label = b[71:82].decode("ascii", "replace").strip() if self.bits == 32 else b[43:54].decode("ascii", "replace").strip()
        self._load_fat()

    def _load_fat(self):
        raw = self.src.read(self.reserved * self.bps, self.fatsz * self.bps)
        n = self.nclusters + 2
        if self.bits == 32:
            n = min(n, len(raw) // 4)
            self.fat = [v & 0x0FFFFFFF for v in struct.unpack_from(f"<{n}I", raw)]
        elif self.bits == 16:
            n = min(n, len(raw) // 2)
            self.fat = list(struct.unpack_from(f"<{n}H", raw))
        else:
            self.fat = []
            for i in range(min(n, len(raw) * 2 // 3)):
                o = i + i // 2
                v = raw[o] | (raw[o + 1] << 8)
                self.fat.append(v >> 4 if i & 1 else v & 0xFFF)
        self.eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.bits]

    def cluster_off(self, c: int) -> int:
        return self.data_start + (c - 2) * self.cluster_size

    def is_free(self, c: int) -> bool:
        return 2 <= c < len(self.fat) and self.fat[c] == 0

    def chain(self, c: int, limit: int = 1 << 24):
        out, seen = [], set()
        while 2 <= c < len(self.fat) and c < self.eoc and c not in seen and len(out) < limit:
            out.append(c)
            seen.add(c)
            c = self.fat[c]
        return out

    def free_runs(self):
        runs, start = [], None
        for c in range(2, len(self.fat)):
            if self.fat[c] == 0:
                if start is None:
                    start = c
            elif start is not None:
                runs.append((self.cluster_off(start), (c - start) * self.cluster_size))
                start = None
        if start is not None:
            runs.append((self.cluster_off(start), (len(self.fat) - start) * self.cluster_size))
        return runs

    # -- directory walking -------------------------------------------------

    def _dir_bytes(self, clusters) -> bytes:
        return b"".join(self.src.read(self.cluster_off(c), self.cluster_size) for c in clusters)

    def _guess_deleted_clusters(self, start: int, count: int):
        """Rebuild a lost chain: `count` clusters from `start`, skipping in-use ones."""
        out, c = [], start
        while len(out) < count and c < len(self.fat):
            if self.fat[c] == 0:
                out.append(c)
            c += 1
        return out

    def entries(self):
        """Yield FsEntry for every file, live and deleted."""
        if self.bits == 32:
            root = self._dir_bytes(self.chain(self.root_cluster))
        else:
            root = self.src.read((self.reserved + self.nfats * self.fatsz) * self.bps, self.root_secs * self.bps)
        stack = [(root, "", False)]
        visited = set()
        budget = 200000
        while stack and budget > 0:
            data, path, parent_deleted = stack.pop()
            lfn = []
            for i in range(0, len(data) - 31, 32):
                e = data[i:i + 32]
                first = e[0]
                if first == 0:
                    break
                attr = e[11]
                if attr & 0x3F == 0x0F:            # long-name fragment
                    lfn.append(e)
                    continue
                gone = first == 0xE5
                long_name = _lfn_name(lfn)
                lfn = []
                if attr & 0x08:                    # volume label
                    continue
                base = e[0:8].decode("latin-1").rstrip()
                ext = e[8:11].decode("latin-1").rstrip()
                if gone:
                    base = "_" + base[1:]
                elif first == 0x05:
                    base = "\xe5" + base[1:]
                short = base + ("." + ext if ext else "")
                if e[12] & 0x08:
                    short = base.lower() + ("." + (ext.lower() if e[12] & 0x10 else ext) if ext else "")
                name = long_name or short
                if name in (".", "..") or base.startswith(".") and not long_name and e[1:8].strip() in (b"", b"."):
                    continue
                hi, = struct.unpack_from("<H", e, 20)
                ctime, cdate = struct.unpack_from("<HH", e, 14)
                mtime, mdate, lo, size = struct.unpack_from("<HHHI", e, 22)
                cluster = (hi << 16 | lo) if self.bits == 32 else lo
                deleted = gone or parent_deleted
                budget -= 1
                if attr & 0x10:                    # directory
                    if not 2 <= cluster < len(self.fat) or cluster in visited:
                        continue
                    visited.add(cluster)
                    if deleted:
                        # A removed directory's own cluster usually still holds
                        # the (also deleted) entries of what was inside it.
                        if not self.is_free(cluster):
                            continue
                        sub = self.src.read(self.cluster_off(cluster), self.cluster_size)
                        if sub[:11] != b".          ":
                            continue
                    else:
                        sub = self._dir_bytes(self.chain(cluster, 65536))
                    stack.append((sub, path + "/" + name, deleted))
                    continue
                if size == 0:
                    continue
                need = -(-size // self.cluster_size)
                intact, note = "good", ""
                if not deleted:
                    clusters = self.chain(cluster)[:need]
                    if len(clusters) < need:
                        intact, note = "partial", "cluster chain shorter than file size"
                else:
                    if not 2 <= cluster < len(self.fat):
                        intact, clusters = "overwritten", []
                        note = "start cluster not recorded"
                    elif not self.is_free(cluster):
                        intact, clusters = "overwritten", []
                        note = "first cluster has been reused by another file"
                    else:
                        clusters = self._guess_deleted_clusters(cluster, need)
                        if len(clusters) < need:
                            intact, note = "partial", "not enough free clusters after start"
                        elif clusters[-1] - clusters[0] + 1 != need:
                            note = "rebuilt around in-use clusters"
                        if gone and not long_name:
                            note = (note + "; " if note else "") + "FAT overwrites the first letter of a deleted name"
                runs = merge_runs((self.cluster_off(c), self.cluster_size) for c in clusters)
                yield FsEntry(name, path or "/", size, deleted, runs,
                              modified=dos_time(mdate, mtime), created=dos_time(cdate, ctime),
                              intact=intact, note=note)


def _lfn_name(parts) -> str:
    if not parts:
        return ""
    chars = b""
    for e in reversed(parts):      # stored last-fragment-first
        chars += e[1:11] + e[14:26] + e[28:32]
    name = chars.decode("utf-16-le", "replace")
    return name.split("\0")[0].rstrip("￿")
