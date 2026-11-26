"""exFAT (SDXC cards, large USB drives, most cameras and phones' removable storage).

Deletion on exFAT is even gentler than on FAT: the directory entries only get
their "in use" bit (0x80 of the type byte) cleared, and the clusters are cleared
in the allocation *bitmap*. Name, exact size, timestamps and first cluster are
untouched; for contiguous files (flag NoFatChain -- the common case) the layout
is fully described, and for fragmented ones the FAT chain is normally left behind.
"""
from __future__ import annotations

import struct

from .fsbase import FsEntry, dos_time, merge_runs


class ExfatVolume:
    kind = "exFAT"

    def __init__(self, src):
        self.src = src
        b = src.read(0, 512)
        if len(b) < 512 or b[3:11] != b"EXFAT   ":
            raise ValueError("not exFAT")
        fat_off, fat_len, heap_off, self.nclusters, self.root = struct.unpack_from("<IIIII", b, 80)
        self.bps = 1 << b[108]
        self.cluster_size = self.bps << b[109]
        self.heap = heap_off * self.bps
        raw = src.read(fat_off * self.bps, min(fat_len * self.bps, (self.nclusters + 2) * 4))
        self.fat = struct.unpack_from(f"<{len(raw) // 4}I", raw)
        self.bitmap = b""
        self.label = ""
        self._root_data = self._read_clusters(self._chain(self.root))
        for i in range(0, len(self._root_data) - 31, 32):
            e = self._root_data[i:i + 32]
            if e[0] == 0x81 and not self.bitmap:
                c, ln = struct.unpack_from("<IQ", e, 20)
                self.bitmap = self._read_clusters(self._chain(c))[:ln]
            elif e[0] == 0x83:
                self.label = e[2:2 + e[1] * 2].decode("utf-16-le", "replace")

    def cluster_off(self, c: int) -> int:
        return self.heap + (c - 2) * self.cluster_size

    def is_free(self, c: int) -> bool:
        i = c - 2
        if not 0 <= i < self.nclusters or i // 8 >= len(self.bitmap):
            return False
        return not self.bitmap[i // 8] >> (i % 8) & 1

    def _chain(self, c: int, limit: int = 1 << 22):
        out, seen = [], set()
        while 2 <= c < len(self.fat) and c not in seen and len(out) < limit:
            out.append(c)
            seen.add(c)
            c = self.fat[c]
        return out

    def _read_clusters(self, clusters) -> bytes:
        return b"".join(self.src.read(self.cluster_off(c), self.cluster_size) for c in clusters)

    def free_runs(self):
        runs, start = [], None
        for i in range(self.nclusters):
            free = i // 8 < len(self.bitmap) and not self.bitmap[i // 8] >> (i % 8) & 1
            if free and start is None:
                start = i
            elif not free and start is not None:
                runs.append((self.cluster_off(start + 2), (i - start) * self.cluster_size))
                start = None
        if start is not None:
            runs.append((self.cluster_off(start + 2), (self.nclusters - start) * self.cluster_size))
        return runs

    def _clusters_for(self, first: int, size: int, contiguous: bool, deleted: bool):
        need = -(-size // self.cluster_size)
        if not 2 <= first < self.nclusters + 2:
            return [], "overwritten", "no valid first cluster"
        if contiguous:
            clusters = list(range(first, min(first + need, self.nclusters + 2)))
        else:
            clusters = self._chain(first, need)
            if len(clusters) < need and deleted:
                # chain was wiped: fall back to "next free clusters" like FAT
                clusters, c = [], first
                while len(clusters) < need and c < self.nclusters + 2:
                    if self.is_free(c):
                        clusters.append(c)
                    c += 1
        if len(clusters) < need:
            return clusters, "partial", "could not locate every cluster"
        if deleted:
            reused = sum(1 for c in clusters if not self.is_free(c))
            if reused and not self.is_free(first):
                return clusters, "overwritten", "first cluster has been reused by another file"
            if reused:
                return clusters, "partial", f"{reused} of {need} clusters reused by newer files"
        return clusters, "good", ""

    def entries(self):
        stack = [(self._root_data, "", False)]
        visited = {self.root}
        budget = 200000
        while stack and budget > 0:
            data, path, parent_deleted = stack.pop()
            i = 0
            while i + 32 <= len(data):
                t = data[i]
                if t == 0:
                    break
                if t & 0x7F != 0x05:
                    i += 32
                    continue
                gone = not t & 0x80
                count = data[i + 1]
                attrs, = struct.unpack_from("<H", data, i + 4)
                ctime, cdate, mtime, mdate = struct.unpack_from("<HHHH", data, i + 8)
                s = data[i + 32:i + 64]
                if len(s) < 32 or s[0] & 0x7F != 0x40 or not 2 <= count <= 18:
                    i += 32
                    continue
                name_len = s[3]
                first, size = struct.unpack_from("<IQ", s, 20)
                contiguous = bool(s[1] & 2)
                name = ""
                for k in range(2, count + 1):
                    ne = data[i + 32 * k:i + 32 * k + 32]
                    if len(ne) == 32 and ne[0] & 0x7F == 0x41:
                        name += ne[2:32].decode("utf-16-le", "replace")
                name = name[:name_len]
                i += 32 * (count + 1)
                deleted = gone or parent_deleted
                budget -= 1
                if not name:
                    continue
                if attrs & 0x10:
                    if first in visited or not 2 <= first < self.nclusters + 2:
                        continue
                    visited.add(first)
                    cl, state, _ = self._clusters_for(first, size or self.cluster_size, contiguous, deleted)
                    if state == "overwritten":
                        continue
                    stack.append((self._read_clusters(cl[:4096]), path + "/" + name, deleted))
                    continue
                if not size:
                    continue
                clusters, intact, note = self._clusters_for(first, size, contiguous, deleted)
                yield FsEntry(name, path or "/", size, deleted,
                              merge_runs((self.cluster_off(c), self.cluster_size) for c in clusters),
                              modified=dos_time(mdate, mtime), created=dos_time(cdate, ctime),
                              intact=intact, note=note)
