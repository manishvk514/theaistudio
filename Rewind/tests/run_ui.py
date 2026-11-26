"""Starts Rewind on a fake home folder and runs the browser-level UI test.
Needs node with jsdom (NODE_PATH or ./node_modules).  Run: python3 tests/run_ui.py"""
import io
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="rewind_ui_")
HOME = os.path.join(TMP, "home")


def photo(i):
    im = Image.new("RGB", (800, 600), ((i * 53) % 255, (i * 97) % 255, (i * 31) % 255))
    d = ImageDraw.Draw(im)
    for k in range(10):
        d.ellipse([k * 60, (k * i * 17) % 500, k * 60 + 120, (k * i * 17) % 500 + 90],
                  fill=(k * 25, 255 - k * 20, i * 20 % 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return b.getvalue()


def put(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


put(os.path.join(HOME, "Pictures", "a.jpg"), photo(1))
put(os.path.join(HOME, "Pictures", "b.jpg"), photo(2))
put(os.path.join(HOME, ".local/share/Trash/files", "c.jpg"), photo(3))
put(os.path.join(HOME, ".local/share/Trash/files", "d.jpg"), photo(4))
z = io.BytesIO()
with zipfile.ZipFile(z, "w") as zf:
    zf.writestr("Takeout/Google Photos/2019/old.jpg", photo(5))
put(os.path.join(HOME, "Downloads", "takeout-20240101T000000Z-001.zip"), z.getvalue())

import socket  # noqa: E402
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
port = _s.getsockname()[1]
_s.close()
env = dict(os.environ, HOME=HOME, REWIND_TEST_FDA="0", PYTHONPATH=ROOT)
srv = subprocess.Popen([sys.executable, "-m", "rewind", "--no-browser", "--port", str(port),
                        "--workspace", os.path.join(TMP, "ws")], env=env, cwd=ROOT)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    paths = [p for p in [os.environ.get("NODE_PATH"), "/home/user/uitest/node_modules",
                         os.path.join(ROOT, "node_modules")] if p]
    nenv = dict(os.environ, NODE_PATH=os.pathsep.join(paths))
    r = subprocess.run(["node", os.path.join(ROOT, "tests", "web_ui.test.js"), "http://127.0.0.1:%d" % port], env=nenv)
    restored = os.path.isdir(os.path.join(HOME, "Pictures", "Rewind Restored"))
    print("  %s restored files are on disk" % ("PASS" if restored else "FAIL"))
    sys.exit(r.returncode or (0 if restored else 1))
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()
