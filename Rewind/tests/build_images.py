"""Build real filesystem images with real deletions, using real drivers.

FAT via mtools, NTFS via ntfs-3g (FUSE), exFAT via exfat-fuse on a loop device.
Scenario on each volume:
  keep_*      written and kept                     -> must be classified "existing"
  photo_1, shot_1, clip.mp4 ... written, deleted   -> must come back byte-exact
  a "wedge" file is deleted before a large file is written, so the large file
  is fragmented around a live file, and is then deleted too.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

KEEP = ["photo_0.jpg", "shot_0.png", "pic.webp"]


def _run(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def plan(files: dict):
    """(steps) list of ('write'|'delete', name)."""
    names = sorted(files)
    victims = [n for n in names if n not in KEEP]
    steps = [("write", "wedge_a.jpg"), ("write", KEEP[0]), ("write", "wedge_b.jpg"), ("write", KEEP[1]),
             ("delete", "wedge_a.jpg"), ("delete", "wedge_b.jpg")]
    steps += [("write", n) for n in victims] + [("write", KEEP[2])]
    steps += [("delete", n) for n in victims]
    return steps, victims


def _with_wedges(files):
    files = dict(files)
    files["wedge_a.jpg"] = files["photo_1.jpg"][:40000]
    files["wedge_b.jpg"] = files["photo_2.jpg"][:70000]
    return files


def build_fat(path: str, files: dict, size_mb: int = 24, fat_bits: int = 16):
    files = _with_wedges(files)
    with open(path, "wb") as f:
        f.truncate(size_mb << 20)
    _run("mkfs.vfat", "-F", str(fat_bits), "-n", "CAMERA", path)
    _run("mmd", "-i", path, "::/DCIM")
    tmp = tempfile.mkdtemp()
    steps, victims = plan(files)
    for op, name in steps:
        if op == "write":
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(files[name])
            _run("mcopy", "-i", path, p, f"::/DCIM/{name}")
        else:
            _run("mdel", "-i", path, f"::/DCIM/{name}")
    shutil.rmtree(tmp)
    return victims


def _populate_mounted(mnt: str, files: dict):
    files = _with_wedges(files)
    os.makedirs(os.path.join(mnt, "DCIM"), exist_ok=True)
    steps, victims = plan(files)
    for op, name in steps:
        p = os.path.join(mnt, "DCIM", name)
        if op == "write":
            with open(p, "wb") as f:
                f.write(files[name])
        else:
            os.remove(p)
    return victims


def build_ntfs(path: str, files: dict, size_mb: int = 32):
    with open(path, "wb") as f:
        f.truncate(size_mb << 20)
    _run("mkfs.ntfs", "-F", "-q", "-f", path)
    mnt = tempfile.mkdtemp()
    _run("ntfs-3g", path, mnt)
    try:
        return _populate_mounted(mnt, files)
    finally:
        _run("fusermount", "-u", mnt)
        os.rmdir(mnt)


def build_exfat(path: str, files: dict, size_mb: int = 32):
    with open(path, "wb") as f:
        f.truncate(size_mb << 20)
    _run("mkfs.exfat", path)
    loop = _run("losetup", "-f", "--show", path).stdout.decode().strip()
    mnt = tempfile.mkdtemp()
    try:
        _run("mount.exfat-fuse", loop, mnt)
        try:
            return _populate_mounted(mnt, files)
        finally:
            _run("fusermount", "-u", mnt)
    finally:
        _run("losetup", "-d", loop)
        os.rmdir(mnt)
