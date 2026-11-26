"""Signature carving: find pictures/videos in raw bytes with no filesystem help.

Works on anything that is a stream of bytes: a whole disk, the free space of a
volume, a thumbnail cache, a database file, a write-ahead log.
"""
from __future__ import annotations

from dataclasses import dataclass

from .formats import Found, MAGIC_LOOKBEHIND, candidates, probe

CHUNK = 8 << 20
OVERLAP = 64


@dataclass
class Hit:
    offset: int
    found: Found


def carve(src, start: int = 0, end: int | None = None, align: int = 1,
          min_size: int = 256, max_size: int | None = None, progress=None, cancel=None):
    """Yield every structurally complete picture/video in src[start:end].

    align=512 only accepts hits on sector boundaries (files on a disk always
    start on one) -- faster and far fewer false positives. align=1 finds media
    embedded anywhere inside another file.
    """
    end = src.size if end is None else min(end, src.size)
    pos = start
    resume = start          # do not start a new file inside one already carved
    while pos < end:
        if cancel and cancel():
            return
        buf = src.read(max(start, pos - MAGIC_LOOKBEHIND), min(CHUNK + OVERLAP, end - pos) + (pos - max(start, pos - MAGIC_LOOKBEHIND)))
        base = max(start, pos - MAGIC_LOOKBEHIND)
        if not buf:
            break
        for rel in candidates(buf):
            off = base + rel
            if off < resume or off >= pos + CHUNK:
                continue
            if align > 1 and off % align:
                continue
            found = probe(src, off, end - off)
            if found and found.length >= min_size and (max_size is None or found.length <= max_size):
                # Formats whose length is merely declared in a header (not proven by
                # walking the whole structure) may be a fragment glued to foreign data:
                # keep looking inside them so they cannot hide a real file.
                if not found.extra.get("weak"):
                    resume = off + found.length
                yield Hit(off, found)
        pos += CHUNK
        if progress:
            progress(min(pos, end) - start, end - start)
