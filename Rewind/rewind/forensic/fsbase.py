"""Shared types for filesystem parsers."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FsEntry:
    name: str
    path: str                 # directory path inside the volume, best effort
    size: int
    deleted: bool
    runs: list                # [(byte offset in volume, length)] where the data lives / lived
    modified: str = ""
    created: str = ""
    intact: str = "good"      # 'good' | 'partial' (some clusters reused) | 'overwritten'
    note: str = ""
    extra: dict = field(default_factory=dict)


def merge_runs(runs):
    out = []
    for o, l in runs:
        if l <= 0:
            continue
        if out and out[-1][0] + out[-1][1] == o:
            out[-1] = (out[-1][0], out[-1][1] + l)
        else:
            out.append((o, l))
    return out


def dos_time(date: int, time: int) -> str:
    y, m, d = 1980 + (date >> 9), (date >> 5) & 15, date & 31
    hh, mm, ss = time >> 11, (time >> 5) & 63, (time & 31) * 2
    if not (1 <= m <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60):
        return ""
    return f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}:{min(ss, 59):02d}"


def filetime(ft: int) -> str:
    import datetime
    try:
        unix = ft / 1e7 - 11644473600
        if not 0 < unix < 4102444800:
            return ""
        return datetime.datetime.fromtimestamp(unix, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""
