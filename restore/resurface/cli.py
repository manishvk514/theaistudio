"""Command line: python -m resurface.cli scan <path> [...] ; restore --to DIR"""
from __future__ import annotations

import argparse
import os
import sys

from .engine import Engine


def main(argv=None):
    ap = argparse.ArgumentParser(prog="resurface", description="Recover deleted pictures and videos.")
    ap.add_argument("--case", default=os.path.join(os.path.expanduser("~"), "Resurface"), help="case folder (catalog + caches)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan a disk image, raw device, folder or zip archive")
    s.add_argument("paths", nargs="+")
    s.add_argument("--role", choices=("device", "current", "evidence"), default="device",
                   help="device: as-is (default). current: what you still have. evidence: old backup/export")
    s.add_argument("--deep", action="store_true", help="also search off sector boundaries (slower)")
    r = sub.add_parser("restore", help="write recovered files out")
    r.add_argument("--to", required=True)
    r.add_argument("--state", default="deleted", choices=("deleted", "existing", "unsorted"))
    r.add_argument("--include-damaged", action="store_true")
    sub.add_parser("list", help="show counts")
    a = ap.parse_args(argv)
    eng = Engine(a.case)
    if a.cmd == "scan":
        for p in a.paths:
            is_dir = os.path.isdir(p)
            job = eng.start(f"scan {p}", eng.scan_folder if is_dir else eng.scan_folder, p,
                            *( [a.role] ), wait=True, deep=True if is_dir else a.deep)
            for line in job.log:
                print("  " + line)
            print(f"{p}: {job.status}, {job.found} items")
    elif a.cmd == "restore":
        ids = eng.catalog.query(state=a.state, damaged=a.include_damaged, ids_only=True)
        job = eng.start("restore", eng.restore, ids, a.to, wait=True)
        for line in job.log:
            print("  " + line)
    c = eng.catalog.counts()
    print(f"deleted: {c['deleted']['n']}   still existing: {c['existing']['n']}   unsorted: {c['unsorted']['n']}"
          f"   (hidden lower-quality duplicates: {c['hidden_duplicates']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
