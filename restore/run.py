#!/usr/bin/env python3
"""Start Resurface:  python run.py   (opens http://127.0.0.1:8765 in your browser)"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 10):
    sys.exit("Resurface needs Python 3.10 or newer.")

from resurface.server import serve  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Resurface - recover deleted pictures and videos")
    ap.add_argument("--case", default=os.path.join(os.path.expanduser("~"), "Resurface"),
                    help="folder for the catalog and caches (default: ~/Resurface). Keep it OFF the drive being recovered.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    sys.exit(serve(a.case, a.port, not a.no_browser))
