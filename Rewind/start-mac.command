#!/bin/bash
# Double-click (first time: right-click > Open) to start Rewind.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. Opening the download page..."
  open "https://www.python.org/downloads/macos/"
  read -p "Install it, then press Enter to close this window." _
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First start: setting up (about a minute)..."
  python3 -m venv .venv || { echo "Could not create the app environment."; read _; exit 1; }
fi
source .venv/bin/activate
python3 -m pip install -q --disable-pip-version-check -r requirements.txt 2>/dev/null \
  || echo "(Could not download the optional helpers. Rewind still works, with exact-match only.)"
python3 -m rewind "$@"
