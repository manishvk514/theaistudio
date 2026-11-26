#!/bin/bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -q --disable-pip-version-check -r requirements.txt 2>/dev/null || echo "(optional helpers not installed)"
python3 -m rewind "$@"
