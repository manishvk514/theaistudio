How the sandbox assembled Rewind.app's built-in Python (conda-forge osx-arm64, no Mac available):
1. Download repodata: https://conda.anaconda.org/conda-forge/osx-arm64/repodata.json.bz2 (+ noarch), saved as osx.json.bz2 / noarch.json.bz2.
2. `python3 resolve.py` picks python 3.12 + openssl, sqlite, zlib, libffi, etc. and downloads the .conda files into conda_dl/.
3. `python3 extract.py <prefix>` unpacks them (keeps symlinks/modes); Pillow, pillow-heif, segno wheels were installed into site-packages.
4. `python3 check_macho.py Rewind.app` checks every Mach-O dependency resolves inside the bundle or the OS.
On a real Mac it is simpler to use python-build-standalone (uv python install 3.12) instead.
