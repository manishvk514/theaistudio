"""Read-only byte sources.

Everything the engine recovers from is wrapped in a Source: a disk image, a raw
device, a partition inside one, the free space of a filesystem, or a plain file.
Sources are opened read-only and are never written to -- the first rule of
recovery is that every write to the medium can destroy the thing being recovered.
"""
from __future__ import annotations

import bisect
import os
import threading

SECTOR = 512
_WINDOW = 1 << 20


class Source:
    """Random-access read-only view of a file or raw block device."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb", buffering=0)
        self._lock = threading.Lock()
        self._win_off = -1
        self._win = b""
        self.size = self._detect_size()

    def _detect_size(self) -> int:
        try:
            size = self._f.seek(0, os.SEEK_END)
            if size:
                return size
        except OSError:
            pass
        # Some raw devices (notably on Windows) do not report a size: probe for it.
        lo, hi = 0, SECTOR
        while self._can_read(hi) and hi < 1 << 50:
            lo, hi = hi, hi * 2
        while hi - lo > SECTOR:
            mid = (lo + hi) // 2 // SECTOR * SECTOR
            if self._can_read(mid):
                lo = mid
            else:
                hi = mid
        return lo + SECTOR if self._can_read(lo) else 0

    def _can_read(self, off: int) -> bool:
        try:
            self._f.seek(off)
            return len(self._f.read(SECTOR)) > 0
        except OSError:
            return False

    def _raw(self, off: int, n: int) -> bytes:
        # Raw devices require sector-aligned offsets and lengths.
        start = off // SECTOR * SECTOR
        end = -(-(off + n) // SECTOR) * SECTOR
        out = bytearray()
        try:
            self._f.seek(start)
            while len(out) < end - start:
                chunk = self._f.read(end - start - len(out))
                if not chunk:
                    break
                out += chunk
        except OSError:
            # Unreadable sector: return what we have, zero-fill the rest so
            # one bad block does not abort a whole scan.
            out += b"\0" * (end - start - len(out))
        return bytes(out[off - start: off - start + n])

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        n = min(n, self.size - off)
        with self._lock:
            if n >= _WINDOW // 4:
                return self._raw(off, n)
            w = off // _WINDOW * _WINDOW
            if w != self._win_off:
                self._win = self._raw(w, _WINDOW)
                self._win_off = w
            data = self._win[off - w: off - w + n]
            if len(data) < n:  # crosses a window boundary
                data += self._raw(off + len(data), n - len(data))
            return data

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass


class BytesSource:
    def __init__(self, data: bytes, path: str = "<memory>"):
        self.data = data
        self.size = len(data)
        self.path = path

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0:
            return b""
        return self.data[off: off + n]


class SubSource:
    """A window (e.g. one partition) of another source."""

    def __init__(self, parent, base: int, size: int):
        self.parent, self.base = parent, base
        self.size = max(0, min(size, parent.size - base))
        self.path = parent.path

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        return self.parent.read(self.base + off, min(n, self.size - off))

    def physical(self, off: int) -> int:
        return self.base + off


class RunSource:
    """Concatenation of (offset, length) runs of a parent source.

    Used two ways: to read a file whose clusters we worked out, and to present
    *only the unallocated space* of a filesystem as one contiguous stream. The
    second use is what lets the carver put back together a deleted file that was
    fragmented around files which still exist: in the free-space view the live
    files vanish and the pieces sit next to each other again.
    """

    def __init__(self, parent, runs, size: int | None = None):
        self.parent = parent
        self.runs = [(o, l) for o, l in runs if l > 0]
        self.starts = []
        pos = 0
        for _, l in self.runs:
            self.starts.append(pos)
            pos += l
        self.size = pos if size is None else min(size, pos)
        self.path = parent.path

    def _locate(self, off: int):
        i = bisect.bisect_right(self.starts, off) - 1
        return i, off - self.starts[i]

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        n = min(n, self.size - off)
        i, rel = self._locate(off)
        out = []
        while n > 0 and i < len(self.runs):
            ro, rl = self.runs[i]
            take = min(n, rl - rel)
            out.append(self.parent.read(ro + rel, take))
            n -= take
            rel = 0
            i += 1
        return b"".join(out)

    def physical(self, off: int) -> int:
        i, rel = self._locate(min(off, max(self.size - 1, 0)))
        return self.runs[i][0] + rel if self.runs else 0

    def subruns(self, off: int, n: int):
        """Physical (offset, length) runs covering virtual [off, off+n)."""
        res = []
        if not self.runs or n <= 0:
            return res
        i, rel = self._locate(off)
        while n > 0 and i < len(self.runs):
            ro, rl = self.runs[i]
            take = min(n, rl - rel)
            if res and res[-1][0] + res[-1][1] == ro + rel:
                res[-1] = (res[-1][0], res[-1][1] + take)
            else:
                res.append((ro + rel, take))
            n -= take
            rel = 0
            i += 1
        return res


def iter_chunks(src, start: int = 0, length: int | None = None, chunk: int = 4 << 20):
    end = src.size if length is None else min(src.size, start + length)
    pos = start
    while pos < end:
        data = src.read(pos, min(chunk, end - pos))
        if not data:
            break
        yield data
        pos += len(data)
