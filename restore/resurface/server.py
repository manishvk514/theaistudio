"""Local web app. Listens on 127.0.0.1 only; nothing leaves the computer except
calls you make to your own cloud accounts."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import string
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import knowledge
from .catalog import ORIGIN_LABEL
from .cloud import providers as cloud
from .engine import FFMPEG, Engine, Image

UI = os.path.join(os.path.dirname(__file__), "ui", "index.html")
MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
        "heic": "image/heic", "avif": "image/avif", "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime",
        "3gp": "video/3gpp", "mkv": "video/x-matroska", "webm": "video/webm", "avi": "video/x-msvideo"}


def list_devices():
    """Removable drives / disks the user could scan raw."""
    out = []
    try:
        if sys.platform.startswith("linux"):
            for name in sorted(os.listdir("/sys/block")):
                if name.startswith(("loop", "ram", "zram", "dm-")):
                    continue
                def rd(f, name=name):
                    try:
                        with open(f"/sys/block/{name}/{f}") as fh:
                            return fh.read().strip()
                    except OSError:
                        return ""
                size = int(rd("size") or 0) * 512
                if size:
                    out.append({"path": f"/dev/{name}", "label": (rd("device/model") or name),
                                "size": size, "removable": rd("removable") == "1"})
        elif sys.platform == "darwin":
            txt = subprocess.run(["diskutil", "list"], capture_output=True, text=True, timeout=10).stdout
            for line in txt.splitlines():
                if line.startswith("/dev/disk"):
                    dev = line.split()[0]
                    out.append({"path": dev.replace("/dev/disk", "/dev/rdisk"), "label": line.strip(), "size": 0,
                                "removable": "external" in line})
        elif os.name == "nt":
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask >> i & 1:
                    kind = ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")
                    if kind in (2, 3):
                        out.append({"path": f"\\\\.\\{letter}:", "label": f"Drive {letter}:", "size": 0, "removable": kind == 2})
    except Exception:
        pass
    return out


def browse(path):
    if not path:
        roots = [os.path.expanduser("~")]
        if os.name == "nt":
            roots += [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
        else:
            roots += [p for p in ("/", "/media", "/mnt", "/Volumes", "/run/media") if os.path.isdir(p)]
        return {"path": "", "parent": "", "entries": [{"name": r, "path": r, "dir": True} for r in roots]}
    path = os.path.abspath(path)
    entries = []
    try:
        for name in sorted(os.listdir(path), key=str.lower)[:3000]:
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            if is_dir or os.path.isfile(full):
                entries.append({"name": name, "path": full, "dir": is_dir,
                                "size": 0 if is_dir else os.path.getsize(full)})
    except OSError as e:
        return {"path": path, "parent": os.path.dirname(path), "entries": [], "error": str(e)}
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    parent = os.path.dirname(path)
    return {"path": path, "parent": "" if parent == path else parent, "entries": entries}


class App:
    def __init__(self, engine: Engine, port: int):
        self.engine = engine
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.oauth = {}       # state -> pending sign-in

    @property
    def redirect(self):
        return f"http://localhost:{self.port}/oauth/callback"

    # ---- API ---------------------------------------------------------------

    def api(self, name, a):
        e = self.engine
        if name == "state":
            return {"counts": e.catalog.counts(), "sources": e.catalog.sources(),
                    "jobs": [j.as_dict() for j in list(e.jobs.values())[-12:]],
                    "caps": {"pillow": Image is not None, "ffmpeg": bool(FFMPEG)},
                    "connected": list(e.providers.keys()), "redirect": self.redirect,
                    "home": os.path.expanduser("~"), "workdir": e.workdir}
        if name == "items":
            res = e.catalog.query(state=a.get("state", "deleted"), kind=a.get("kind", ""), source=int(a.get("source") or 0),
                                  q=a.get("q", ""), show_shadow=bool(a.get("shadow")), lowres=a.get("lowres", ""),
                                  damaged=not a.get("hide_damaged"), order=a.get("order", "taken"),
                                  offset=int(a.get("offset") or 0), limit=min(int(a.get("limit") or 120), 500))
            for it in res["items"]:
                it["origin_label"] = ORIGIN_LABEL.get(it["origin"], it["origin"])
                it["cloud"] = it["loc"].startswith('{"t": "cloud"')
                del it["loc"]
            return res
        if name == "scan":
            path = (a.get("path") or "").strip().strip('"')
            if not path or not os.path.exists(path):
                raise ValueError(f"Path not found: {path or '(empty)'}")
            role = a.get("role", "device")
            if a.get("mode") == "disk" or (os.path.isfile(path) and not path.lower().endswith(".zip")) or not (os.path.isdir(path) or os.path.isfile(path)):
                job = e.start(f"Scanning disk {os.path.basename(path) or path}", e.scan_image, path, bool(a.get("deep")), a.get("label") or None)
            else:
                job = e.start(f"Scanning {os.path.basename(path.rstrip('/' + chr(92))) or path}", e.scan_folder, path, role, True, a.get("label") or None)
            return job.as_dict()
        if name == "places":
            from .discover import hiding_places
            return {"places": hiding_places(True)}
        if name == "auto":
            return e.start("Searching this computer's hiding places", e.scan_auto, bool(a.get("noisy"))).as_dict()
        if name == "cloud_connect":
            prov = a.get("provider")
            if prov not in cloud.PROVIDERS:
                raise ValueError("unknown provider")
            if a.get("token"):
                return self._cloud_scan(prov, a["token"].strip())
            if not a.get("client_id"):
                raise ValueError("Enter the client ID (app key) of your own OAuth app, or paste an access token.")
            state = secrets.token_urlsafe(16)
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.oauth[state] = {"provider": prov, "client_id": a["client_id"].strip(),
                                 "client_secret": (a.get("client_secret") or "").strip(), "verifier": verifier}
            return {"url": cloud.oauth_url(prov, a["client_id"].strip(), self.redirect, state, challenge)}
        if name == "restore":
            ids = a.get("ids")
            if not ids:
                f = a.get("filter") or {}
                ids = e.catalog.query(state=f.get("state", "deleted"), kind=f.get("kind", ""), source=int(f.get("source") or 0),
                                      q=f.get("q", ""), show_shadow=bool(f.get("shadow")), lowres=f.get("lowres", ""),
                                      damaged=not f.get("hide_damaged"), ids_only=True)
            if not ids:
                raise ValueError("Nothing selected.")
            if not a.get("in_place") and not a.get("dest"):
                raise ValueError("Choose a destination folder.")
            return e.start(f"Restoring {len(ids)} item(s)", e.restore, [int(i) for i in ids], a.get("dest", ""),
                           bool(a.get("in_place"))).as_dict()
        if name == "cancel":
            job = e.jobs.get(int(a.get("id") or 0))
            if job:
                job.cancelled = True
            return {}
        if name == "remove_source":
            e.catalog.remove_source(int(a["id"]))
            e.providers.pop(int(a["id"]), None)
            return {}
        if name == "devices":
            return {"devices": list_devices()}
        if name == "browse":
            return browse(a.get("path", ""))
        if name == "map":
            return {"principles": knowledge.PRINCIPLES, "map": knowledge.MAP}
        if name == "reveal":
            item = e.catalog.get(int(a["id"]))
            target = item and item["restored"]
            if target and os.path.exists(target):
                folder = os.path.dirname(target)
                try:
                    if os.name == "nt":
                        os.startfile(folder)     # noqa
                    else:
                        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
                except OSError:
                    pass
            return {"path": target or ""}
        raise KeyError(name)

    def _cloud_scan(self, prov, token):
        p = cloud.PROVIDERS[prov](token)
        p.account()          # fail fast on a bad token
        return self.engine.start(f"Reading {p.title} account", self.engine.scan_cloud, p).as_dict()

    def oauth_callback(self, q):
        pending = self.oauth.pop(q.get("state", ""), None)
        if not pending:
            return "Sign-in session not found or already used. Start again from the app."
        if "code" not in q:
            return "Sign-in was cancelled: " + q.get("error_description", q.get("error", "no code returned"))
        try:
            token = cloud.oauth_exchange(pending["provider"], pending["client_id"], pending["client_secret"],
                                         self.redirect, q["code"], pending["verifier"])
            self._cloud_scan(pending["provider"], token)
        except Exception as e:
            return f"Sign-in failed: {e}"
        return "Connected. You can close this tab and return to Resurface."


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _host_ok(self):
            # Refuse requests that reached us under any other name (DNS rebinding).
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self, q):
            given = self.headers.get("X-Resurface-Token") or q.get("k", "")
            return secrets.compare_digest(given, app.token)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if not self._host_ok():
                return self._send(403, "forbidden", "text/plain")
            try:
                if u.path == "/":
                    with open(UI, encoding="utf-8") as fh:
                        html = fh.read().replace("__TOKEN__", app.token)
                    return self._send(200, html, "text/html; charset=utf-8")
                if u.path == "/oauth/callback":
                    msg = app.oauth_callback(q)
                    return self._send(200, f"<body style='font:16px system-ui;padding:3em'>{msg}</body>", "text/html; charset=utf-8")
                if not self._authed(q):
                    return self._send(401, {"error": "unauthorized"})
                if u.path.startswith("/api/thumb/"):
                    p = app.engine.thumbnail(int(u.path.rsplit("/", 1)[1]))
                    if not p:
                        return self._send(404, b"", "text/plain")
                    with open(p, "rb") as fh:
                        return self._send(200, fh.read(), "image/jpeg", {"Cache-Control": "private, max-age=86400"})
                if u.path.startswith("/api/media/"):
                    return self._media(int(u.path.rsplit("/", 1)[1]))
                if u.path.startswith("/api/"):
                    return self._send(200, app.api(u.path[5:], q))
                self._send(404, "not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self._send(400, {"error": str(e)})

        do_HEAD = do_GET

        def do_POST(self):
            u = urllib.parse.urlsplit(self.path)
            if not self._host_ok() or not self._authed({}):
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                self._send(200, app.api(u.path[5:], body))
            except Exception as e:
                self._send(400, {"error": str(e)})

        def _media(self, item_id):
            item = app.engine.catalog.get(item_id)
            if not item:
                return self._send(404, b"", "text/plain")
            view, size = app.engine.open_item(item)
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            code = 200
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                if a:
                    start = int(a)
                    end = int(b) if b else end
                elif b:
                    start = max(0, size - int(b))
                end = min(end, size - 1)
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", MIME.get(item["ext"], mimetypes.guess_type("x." + item["ext"])[0] or "application/octet-stream"))
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            pos = start
            while pos <= end:
                b = view.read(pos, min(1 << 20, end - pos + 1))
                if not b:
                    break
                self.wfile.write(b)
                pos += len(b)
    return H


def serve(workdir: str, port: int = 8765, open_browser: bool = True):
    engine = Engine(workdir)
    app = App(engine, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"Resurface is running at {url}\nCase folder: {engine.workdir}\nPress Ctrl+C to stop.")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    serve(os.path.join(os.path.expanduser("~"), "Resurface"))
