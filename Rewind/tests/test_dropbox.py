"""Dropbox: deleted files come back from their kept revisions; restore puts them back.
Runs against a stand-in for Dropbox's API (same requests and answers as the real one).
Run: python3 tests/test_dropbox.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from rewind.server import App  # noqa: E402
from rewind.sources import cloud, connect  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  -> %s" % (detail,)))
    if not ok:
        FAILS.append(name)


def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.effect_noise((1600, 1200), 60).convert("RGB").save(b, "JPEG")
    return b.getvalue()


PHOTO = jpeg()
RESTORED = []


class FakeDropbox(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer dbx-token"
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.path == "/2/files/list_folder":
            assert body["include_deleted"] and body["recursive"]
            return self._json({"has_more": True, "cursor": "c1", "entries": [
                {".tag": "file", "name": "kept.jpg", "path_display": "/Photos/kept.jpg", "size": 10,
                 "client_modified": "2024-05-01T10:00:00Z"}]})
        if self.path == "/2/files/list_folder/continue":
            return self._json({"has_more": False, "entries": [
                {".tag": "deleted", "name": "beach.jpg", "path_display": "/Photos/beach.jpg"},
                {".tag": "deleted", "name": "old.mp4", "path_display": "/Photos/old.mp4"},
                {".tag": "deleted", "name": "notes.txt", "path_display": "/notes.txt"}]})
        if self.path == "/2/files/list_revisions":
            if body["path"] == "/Photos/beach.jpg":
                return self._json({"is_deleted": True, "server_deleted": "2026-10-01T08:00:00Z",
                                   "entries": [{"rev": "015abc0001", "size": len(PHOTO),
                                                "client_modified": "2023-07-04T18:30:00Z"}]})
            return self._json({"is_deleted": True, "entries": []})       # revisions expired
        if self.path == "/2/files/download":
            arg = json.loads(self.headers["Dropbox-API-Arg"])
            assert arg["path"] == "rev:015abc0001"
            self.send_response(200)
            self.send_header("Content-Length", str(len(PHOTO)))
            self.end_headers()
            self.wfile.write(PHOTO)
            return
        if self.path == "/2/files/restore":
            RESTORED.append(body)
            return self._json({"name": "beach.jpg"})
        self._json({}, 404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeDropbox)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/2" % srv.server_address[1]
    cloud.DBX = cloud.DBX_CONTENT = url
    connect.access_token = lambda cat, service: "dbx-token"
    tmp = tempfile.mkdtemp(prefix="rewind-dbx-")
    app = App(tmp, 0)
    connect._write_remote(app.cat, "dropbox", {"type": "dropbox", "token": "{}"})
    check("Dropbox shows as connected", connect.connected(app.cat)["dropbox"]["state"] == "connected")
    jid = app.start_scan("auto", {"only": ["cloud:dropbox"]})
    while app.jobs[jid].state == "running":
        time.sleep(0.2)
    job = app.jobs[jid]
    check("automatic scan visits Dropbox", job.state == "done" and any(p["id"] == "cloud:dropbox" for p in job.places),
          job.logs[-5:])
    rows = {r["name"]: dict(r) for r in app.cat.con().execute("SELECT * FROM items")}
    b = rows.get("beach.jpg") or {}
    ok = b.get("path") and open(b["path"], "rb").read() == PHOTO
    check("deleted photo downloaded from its last revision, byte for byte", ok, b)
    check("...counted as deleted, from a bin", b.get("verdict") == "deleted" and b.get("category") == "trash",
          (b.get("verdict"), b.get("category")))
    check("...with its dates", b.get("taken", "").startswith("2023-07-04") and b.get("deleted_at", "").startswith("2026-10-01"))
    check("a deleted video whose revisions expired is listed as known but lost",
          rows.get("old.mp4", {}).get("verdict") == "missing")
    check("files still in Dropbox are 'still here'", rows.get("kept.jpg", {}).get("hint") == "present")
    check("non-media files are ignored", "notes.txt" not in rows)
    msg = app.remote_restore(b["id"])
    check("restore puts it back in Dropbox", msg == "Put back in Dropbox" and
          RESTORED == [{"path": "/Photos/beach.jpg", "rev": "015abc0001"}], (msg, RESTORED))
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASSED" if not FAILS else "\nFAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
