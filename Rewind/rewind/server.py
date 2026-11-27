"""Local web app. Listens on this computer only (127.0.0.1); when you switch on
"Use on my phone", a second listener on your home network accepts only paired devices."""
import ipaddress
import json
import re
import socket
import zipfile
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core.catalog import Catalog
from .core import classify as classifier, export as exporter, imaging
from .core.media import BROWSER_OK
from .sources import (folder, carve_source, ios_backup, android_adb, whatsapp, takeout, telegram,
                      snapchat, computer, cloud, email_imap, exports, this_phone, auto, connect,
                      telegram_cloud)
from .sources.base import Context

WEB = os.path.join(os.path.dirname(__file__), "web")

SOURCES = {
    auto.INFO["id"]: (auto.INFO, auto.run),
    auto.CARD_INFO["id"]: (auto.CARD_INFO, auto.deep_card),
    folder.INFO["id"]: (folder.INFO, folder.run),
    computer.INFO["id"]: (computer.INFO, computer.run),
    carve_source.INFO["id"]: (carve_source.INFO, carve_source.run),
    ios_backup.INFO["id"]: (ios_backup.INFO, ios_backup.run),
    android_adb.INFO["id"]: (android_adb.INFO, android_adb.run),
    whatsapp.INFO["id"]: (whatsapp.INFO, whatsapp.run),
    telegram.INFO["id"]: (telegram.INFO, telegram.run),
    takeout.INFO["id"]: (takeout.INFO, takeout.run),
    snapchat.INFO["id"]: (snapchat.INFO, snapchat.run),
    exports.TM_INFO["id"]: (exports.TM_INFO, exports.run_mac_backups),
    exports.MMS_INFO["id"]: (exports.MMS_INFO, exports.run_mms),
    email_imap.INFO["id"]: (email_imap.INFO, email_imap.run),
    exports.EXPORT_INFO["id"]: (exports.EXPORT_INFO, exports.run_export),
    cloud.GDRIVE_INFO["id"]: (cloud.GDRIVE_INFO, cloud.run_gdrive),
    cloud.ONEDRIVE_INFO["id"]: (cloud.ONEDRIVE_INFO, cloud.run_onedrive),
}
if this_phone.is_android():
    SOURCES = {this_phone.INFO["id"]: (this_phone.INFO, this_phone.run), **SOURCES}


class Job:
    def __init__(self, jid, source, label, port):
        self.id = jid
        self.source = source
        self.label = label
        self.port = port
        self.state = "running"
        self.logs = []
        self.done = 0
        self.total = 0
        self.step = ""
        self.found = 0
        self.stop_requested = False
        self.needs_user = None
        self.places = []
        self.started = time.time()

    def log(self, msg):
        self.logs.append(time.strftime("%H:%M:%S ") + str(msg))
        self.logs = self.logs[-400:]

    def progress(self, done, total, label=""):
        self.done, self.total, self.step = done, total, label

    def ask_user(self, message, url=None, code=None):
        self.needs_user = None if message is None else {"message": message, "url": url, "code": code}

    def as_dict(self):
        return {"id": self.id, "source": self.source, "label": self.label, "state": self.state,
                "logs": self.logs[-60:], "done": self.done, "total": self.total, "step": self.step,
                "found": self.found, "needs_user": self.needs_user, "started": self.started,
                "places": self.places}


class App:
    def __init__(self, workspace, port):
        self.cat = Catalog(workspace)
        self.port = port
        self.jobs = {}
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.phone_token = None      # given to paired phones; None = phone access off
        self.pair_code = None
        self.pair_fail = 0
        self.lan_server = None
        self.lan_port = port + 1
        self.servers = []

    def start_scan(self, source, params):
        info, fn = SOURCES[source]
        label = params.get("path") or info["title"]
        jid = len(self.jobs) + 1
        job = Job(jid, source, "%s: %s" % (info["title"], label), self.port)
        self.jobs[jid] = job
        scan_id = self.cat.new_scan(source, job.label, params)

        def work():
            ctx = Context(self.cat, job, scan_id, source)
            try:
                job.log("Started")
                fn(ctx, params)
                job.found = ctx.found
                job.log("Matching against your library ...")
                classifier.classify(self.cat)
                job.state = "stopped" if job.stop_requested else "done"
                job.log("Finished. %d item(s) found." % ctx.found)
            except Exception as e:
                job.state = "error"
                job.log("Problem: %s" % e)
                traceback.print_exc()
            finally:
                job.found = ctx.found
                job.needs_user = None
                self.cat.finish_scan(scan_id, job.state, ctx.found)

        threading.Thread(target=work, daemon=True).start()
        return job.id

    def discover(self):
        out = {"computer": [], "ios_backups": [], "android": None, "telegram": []}
        out["computer"] = [{"id": c[0], "label": c[1], "path": c[2]} for c in computer.candidates()]
        out["ios_backups"] = [ios_backup.describe(p) for p in ios_backup.default_backup_dirs()]
        out["android"] = android_adb.devices()
        tg = [os.path.expanduser(p) for p in ("~/Downloads/Telegram Desktop",
                                               "~/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram")]
        out["telegram"] = [p for p in tg if os.path.exists(p)]
        out["time_machine"] = exports.time_machine_roots()
        out["backends"] = imaging.backends()
        out["platform"] = sys.platform
        return out

    def items(self, q):
        view = q.get("view", "deleted")
        con = self.cat.con()
        where, args = [], []
        if view == "deleted":
            where.append("verdict='deleted' AND is_primary=1")
        elif view in ("trash", "recovered", "thumbnail"):
            where.append("verdict='deleted' AND is_primary=1 AND category=?")
            args.append(view)
        elif view == "missing":
            where.append("verdict='missing' AND is_primary=1")
        elif view == "present":
            where.append("verdict='present' AND path IS NOT NULL")
        elif view == "duplicate":
            where.append("verdict='duplicate'")
        elif view == "group":
            where.append("group_id=?")
            args.append(int(q.get("group", 0)))
        if q.get("kind") in ("image", "video"):
            where.append("kind=?")
            args.append(q["kind"])
        if q.get("source"):
            where.append("source=?")
            args.append(q["source"])
        sql = "SELECT id,source,location,name,hint,kind,ext,size,width,height,taken,deleted_at,evidence,remote," \
              "category,verdict,group_id,damaged,restored_to,path IS NOT NULL AS has_file FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = {"newest": "COALESCE(taken, deleted_at) DESC", "oldest": "COALESCE(taken, deleted_at) ASC",
                 "biggest": "size DESC"}.get(q.get("sort"), "COALESCE(taken, deleted_at) DESC")
        limit, offset = int(q.get("limit", 120)), int(q.get("offset", 0))
        total = con.execute("SELECT COUNT(*) FROM items" + (" WHERE " + " AND ".join(where) if where else ""),
                            args).fetchone()[0]
        rows = [dict(r) for r in con.execute(sql + " ORDER BY %s LIMIT ? OFFSET ?" % order,
                                             args + [limit, offset])]
        for r in rows:
            gid = r.get("group_id")
            if gid and view != "group":
                r["copies"] = con.execute("SELECT COUNT(*) FROM items WHERE group_id=?", (gid,)).fetchone()[0]
        return {"total": total, "items": rows}

    def thumb(self, item_id):
        it = self.cat.get(item_id)
        if not it or not it["path"] or not os.path.exists(it["path"]):
            return None, None
        tdir = self.cat.dir("thumbs")
        tp = os.path.join(tdir, "%d.jpg" % item_id)
        if os.path.exists(tp):
            return tp, "image/jpeg"
        if imaging.make_thumb(it["path"], it["kind"], tp):
            return tp, "image/jpeg"
        if it["kind"] == "image" and it["ext"] in BROWSER_OK:
            return it["path"], mimetypes.guess_type("x" + it["ext"])[0] or "image/jpeg"
        return None, None

    def remote_restore(self, item_id):
        it = self.cat.get(item_id)
        remote = json.loads(it["remote"] or "{}")
        if remote.get("service") == "gdrive":
            msg = cloud.gdrive_untrash(self.cat, remote)
        elif remote.get("service") == "onedrive":
            path = cloud.onedrive_restore(self.cat, it, self.cat.dir("recovered", "onedrive"))
            with self.cat.con() as c:
                c.execute("UPDATE items SET path=?, hint='trash', verdict='deleted', category='trash' WHERE id=?",
                          (path, item_id))
            msg = "Restored in OneDrive and downloaded"
        elif remote.get("service") == "dropbox":
            msg = cloud.dropbox_restore(self.cat, remote)
        else:
            raise ValueError("This item cannot be restored inside a service.")
        with self.cat.con() as c:
            c.execute("UPDATE items SET restored_to=? WHERE id=?", (msg, item_id))
        return msg

    def status(self):
        auto_jobs = [j for j in self.jobs.values() if j.source == "auto"]
        last = auto_jobs[-1].as_dict() if auto_jobs else None
        return {"platform": sys.platform, "full_disk_access": auto.full_disk_access(),
                "accounts": connect.connected(self.cat), "telegram": telegram_cloud.get(self.cat).status(),
                "auto_job": last, "summary": classifier.summary(self.cat),
                "restore_folder": self.restore_root(), "is_android": this_phone.is_android()}

    def restore_root(self):
        return os.path.join(os.path.expanduser("~"), "Pictures", "Rewind Restored")

    def restore_all(self, ids=None):
        root = self.restore_root()
        os.makedirs(root, exist_ok=True)
        res = exporter.export(self.cat, root, ids=ids)
        con = self.cat.con()
        where = "verdict='deleted' AND is_primary=1 AND path IS NOT NULL"
        if ids:
            con.execute("UPDATE items SET restored_to=? WHERE id IN (%s)" % ",".join("?" * len(ids)),
                        [res["folder"]] + list(ids))
        else:
            con.execute("UPDATE items SET restored_to=? WHERE " + where, (res["folder"],))
        con.commit()
        return res

    def open_settings(self, what):
        if sys.platform != "darwin":
            return False
        urls = {"fda": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
                "photos": "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos"}
        subprocess.Popen(["open", urls.get(what, urls["fda"])])
        return True

    def browse(self, kind):
        """Native file/folder picker, run out-of-process so it works from any thread."""
        try:
            if sys.platform == "darwin":
                what = "choose folder" if kind == "folder" else "choose file"
                r = subprocess.run(["osascript", "-e", 'POSIX path of (%s with prompt "Choose")' % what],
                                   capture_output=True, text=True, timeout=600)
                return r.stdout.strip() or None
            code = ("import tkinter as t, tkinter.filedialog as f; r=t.Tk(); r.withdraw(); "
                    "r.attributes('-topmost', True); print(f.%s())" % (
                        "askdirectory" if kind == "folder" else "askopenfilename"))
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
            return r.stdout.strip() or None
        except Exception:
            return None



def lan_addresses():
    """IPv4 addresses of this computer on local networks (Wi-Fi, Ethernet, USB hotspot)."""
    found = set()
    try:
        out = subprocess.run(["ipconfig"] if os.name == "nt" else ["ifconfig"], capture_output=True,
                             text=True, timeout=10).stdout
        found.update(re.findall(r"(?:inet |IPv4[^:]*:\s*)(\d+\.\d+\.\d+\.\d+)", out))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        found.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    keep = []
    for a in found:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not a.startswith("169.254."):
            keep.append(a)
    return sorted(keep)


def qr_svg(text):
    try:
        import segno  # optional
        import io
        buf = io.BytesIO()          # segno writes bytes for SVG
        segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000", light="#fff")
        return buf.getvalue().decode("utf-8")
    except Exception:
        return None


UPLOAD_ROUTES = [  # (pattern in file name, source, extra params)
    (r"takeout", "google-takeout", {"role": "search"}),
    (r"whatsapp chat|whatsapp", "whatsapp", {"role": "search"}),
    (r"mydata|snapchat", "snapchat", {"download": True}),
    (r"^sms-.*\.xml$|\.xml$", "mms", {}),
    (r"instagram|facebook|messenger|meta|icloud|apple|tiktok|twitter|samsung|xiaomi", "data-download", {}),
    (r"\.(img|dd|bin|raw|dmg|iso)$", "carve", {}),
]


def route_upload(name):
    low = name.lower()
    for rx, src, extra in UPLOAD_ROUTES:
        if re.search(rx, low):
            return src, extra
    if low.endswith(".zip"):
        return "data-download", {}
    return None, {}


def make_handler(app, remote=False):
    class H(BaseHTTPRequestHandler):

        def log_message(self, *a):
            pass

        def _local(self):
            return not remote and self.client_address[0] in ("127.0.0.1", "::1")

        def _host_ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if not remote:
                return host in ("127.0.0.1", "localhost")
            if app.phone_token is None:
                return False
            try:
                return ipaddress.ip_address(host).is_private
            except ValueError:
                return False

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
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path, ctype, download_name=None):
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            if download_name:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % re.sub(r'[^\w.\- ]', "_", download_name))
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)

        def _authed(self, qs):
            tok = self.headers.get("X-Rewind-Token") or qs.get("t")
            if not tok:
                return False
            if remote:
                return app.phone_token is not None and secrets.compare_digest(tok, app.phone_token)
            return secrets.compare_digest(tok, app.token)

        def _token_for_page(self):
            return app.token if self._local() else ""

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            u = urllib.parse.urlsplit(self.path)
            qs = dict(urllib.parse.parse_qsl(u.query))
            p = u.path
            if p == "/health":
                from . import __version__
                return self._send(200, {"ok": True, "app": "rewind", "version": __version__, "pid": os.getpid()})
            if p in ("/", "/index.html"):
                with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                    html = f.read().replace("__TOKEN__", self._token_for_page()).replace(
                        "__REMOTE__", "1" if remote else "0")
                return self._send(200, html, "text/html; charset=utf-8")
            if p == "/manifest.json":
                return self._file(os.path.join(WEB, "manifest.json"), "application/manifest+json")
            if p == "/sw.js":
                with open(os.path.join(WEB, "sw.js"), "rb") as f:
                    return self._send(200, f.read(), "text/javascript", {"Service-Worker-Allowed": "/"})
            if p.startswith("/static/"):
                fp = os.path.normpath(os.path.join(WEB, p[len("/static/"):]))
                if not fp.startswith(WEB + os.sep) or not os.path.isfile(fp):
                    return self._send(404, {"error": "not found"})
                return self._file(fp, mimetypes.guess_type(fp)[0] or "application/octet-stream")
            if p == "/oauth/google" and not remote:
                st = qs.get("state")
                if st in cloud.PENDING and qs.get("code"):
                    cloud.PENDING[st]["code"] = qs["code"]
                    return self._send(200, "<h2>Signed in. You can close this tab and go back to Rewind.</h2>",
                                      "text/html")
                return self._send(400, "<h2>Sign-in was not completed.</h2>", "text/html")
            if not self._authed(qs):
                return self._send(403, {"error": "forbidden"})
            try:
                if p == "/api/sources":
                    return self._send(200, [dict(i) for i, _f in SOURCES.values()])
                if p == "/api/discover":
                    d = app.discover()
                    d["remote"] = remote
                    d["android_local"] = this_phone.is_android()
                    return self._send(200, d)
                if p == "/api/status":
                    st = app.status()
                    st["remote"] = remote
                    return self._send(200, st)
                if p == "/api/plan":
                    return self._send(200, auto.plan(app.cat))
                if p == "/api/jobs":
                    return self._send(200, [j.as_dict() for j in app.jobs.values()][::-1])
                if p == "/api/summary":
                    s = classifier.summary(app.cat)
                    s["sources"] = [r[0] for r in app.cat.con().execute("SELECT DISTINCT source FROM items")]
                    return self._send(200, s)
                if p == "/api/ask":
                    return self._send(200, {"text": exporter.ask_people_text(app.cat)})
                if p == "/api/items":
                    return self._send(200, app.items(qs))
                if p == "/api/phone" and not remote:
                    return self._send(200, app.phone_state())
                if p.startswith("/api/item/"):
                    it = app.cat.get(int(p.rsplit("/", 1)[1]))
                    if not it:
                        return self._send(404, {"error": "not found"})
                    it["has_file"] = bool(it["path"] and os.path.exists(it["path"]))
                    for k in ("path", "sha1", "md5"):
                        it.pop(k, None)
                    return self._send(200, it)
                if p.startswith("/api/thumb/"):
                    tp, ct = app.thumb(int(p.rsplit("/", 1)[1]))
                    if not tp:
                        return self._send(404, {"error": "no preview"})
                    return self._file(tp, ct)
                if p.startswith("/api/file/"):
                    it = app.cat.get(int(p.rsplit("/", 1)[1]))
                    if not it or not it["path"] or not os.path.exists(it["path"]):
                        return self._send(404, {"error": "no file"})
                    ct = mimetypes.guess_type("x" + (it["ext"] or ""))[0] or "application/octet-stream"
                    name = it["name"] or ("file" + (it["ext"] or ""))
                    if not os.path.splitext(name)[1] and it["ext"]:
                        name += it["ext"]
                    return self._file(it["path"], ct, name if qs.get("dl") else None)
                if p.startswith("/api/download/"):
                    name = os.path.basename(urllib.parse.unquote(p.rsplit("/", 1)[1]))
                    fp = os.path.join(app.cat.dir("exports"), name)
                    if not os.path.isfile(fp):
                        return self._send(404, {"error": "no file"})
                    return self._file(fp, "application/zip", name)
                if p == "/api/get-rewind.apk":
                    apk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "Rewind.apk")
                    if os.path.exists(apk):
                        return self._file(apk, "application/vnd.android.package-archive", "Rewind.apk")
                    return self._send(404, {"error": "not bundled"})
                if p == "/api/get-rewind.zip":
                    return self._file(app.self_zip(), "application/zip", "Rewind.zip")
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            u = urllib.parse.urlsplit(self.path)
            qs = dict(urllib.parse.parse_qsl(u.query))
            p = u.path
            n = int(self.headers.get("Content-Length") or 0)
            if p == "/api/pair" and remote:
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                return self._send(*app.try_pair(str(body.get("code", ""))))
            if not self._authed({}):
                if n:
                    self.rfile.read(min(n, 1 << 20))
                return self._send(403, {"error": "forbidden"})
            if p == "/api/upload":
                return self._send(200, app.receive_upload(self.rfile, n, qs.get("name") or "upload.bin",
                                                          qs.get("scan") == "1"))
            body = json.loads(self.rfile.read(n) or b"{}")
            local_only = {"/api/browse", "/api/open", "/api/reset", "/api/quit", "/api/phone", "/api/settings",
                          "/api/deep_scan", "/api/connect", "/api/telegram", "/api/claude_setup"}
            if p in local_only and remote:
                return self._send(403, {"error": "Only on the laptop"})
            try:
                if p == "/api/scan":
                    if body.get("source") not in SOURCES:
                        return self._send(400, {"error": "unknown source"})
                    return self._send(200, {"job": app.start_scan(body["source"], body.get("params") or {})})
                if p == "/api/stop":
                    j = app.jobs.get(int(body.get("job", 0)))
                    if j:
                        j.stop_requested = True
                    return self._send(200, {"ok": True})
                if p == "/api/classify":
                    return self._send(200, classifier.classify(app.cat))
                if p == "/api/export":
                    if body.get("zip"):
                        return self._send(200, app.export_zip(body.get("ids") or None))
                    dest = body.get("dest") or os.path.join(os.path.expanduser("~"), "Desktop")
                    if not os.path.isdir(os.path.expanduser(dest)):
                        dest = os.path.expanduser("~")
                    return self._send(200, exporter.export(app.cat, dest, ids=body.get("ids") or None))
                if p == "/api/restore_all":
                    return self._send(200, app.restore_all(body.get("ids") or None))
                if p == "/api/connect":
                    svc = body.get("service")
                    if svc not in connect.SERVICES:
                        return self._send(400, {"error": "unknown service"})
                    connect.start_connect(app.cat, svc)
                    return self._send(200, connect.connected(app.cat))
                if p == "/api/disconnect":
                    svc = body.get("service")
                    if svc == "telegram":
                        return self._send(200, telegram_cloud.get(app.cat).disconnect())
                    if svc in connect.SERVICES:
                        connect.remove(app.cat, svc)
                    return self._send(200, connect.connected(app.cat))
                if p == "/api/telegram":
                    tg = telegram_cloud.get(app.cat)
                    a = body.get("action")
                    if a == "qr":
                        return self._send(200, tg.qr_start(body.get("api_id"), body.get("api_hash")))
                    if a == "start":
                        return self._send(200, tg.start(body.get("api_id"), body.get("api_hash"), body.get("phone")))
                    if a == "code":
                        return self._send(200, tg.code(body.get("code")))
                    if a == "password":
                        return self._send(200, tg.password(body.get("password") or ""))
                    return self._send(200, tg.status())
                if p == "/api/settings" and not remote:
                    return self._send(200, {"ok": app.open_settings(body.get("what", "fda"))})
                if p == "/api/deep_scan" and not remote:
                    if not auto.device_of(body.get("volume", "")):
                        return self._send(400, {"error": auto.NOT_CARD})
                    return self._send(200, {"job": app.start_scan("card-deep", {"path": body["volume"]})})
                if p == "/api/remote_restore":
                    return self._send(200, {"message": app.remote_restore(int(body["id"]))})
                if p == "/api/browse":
                    return self._send(200, {"path": app.browse(body.get("kind", "folder"))})
                if p == "/api/open":
                    target = body.get("path")
                    if target and os.path.exists(target):
                        opener = "open" if sys.platform == "darwin" else ("explorer" if os.name == "nt" else "xdg-open")
                        subprocess.Popen([opener, target])
                    return self._send(200, {"ok": True})
                if p == "/api/phone":
                    return self._send(200, app.set_phone(bool(body.get("on"))))
                if p == "/api/claude_setup" and not remote:
                    from . import mcp as mcp_mod
                    return self._send(200, {"lines": mcp_mod.install_claude(app.cat.workspace)})
                if p == "/api/quit":
                    threading.Timer(0.5, app.shutdown).start()
                    return self._send(200, {"ok": True})
                if p == "/api/reset":
                    with app.cat.con() as c:
                        c.execute("DELETE FROM items")
                        c.execute("DELETE FROM scans")
                    shutil.rmtree(os.path.join(app.cat.workspace, "thumbs"), ignore_errors=True)
                    return self._send(200, {"ok": True})
            except ValueError as e:      # a plain "please fix this" for the person
                print("%s %s -> %s" % (time.strftime("%H:%M:%S"), p, e), flush=True)   # visible in logs
                return self._send(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
            return self._send(404, {"error": "not found"})

    return H


# ---------------------------------------------------------------- App extras

def _phone_state(self):
    on = self.phone_token is not None
    urls = ["http://%s:%d/" % (a, self.lan_port) for a in lan_addresses()] if on else []
    return {"on": on, "code": self.pair_code if on else None, "urls": urls,
            "qr": qr_svg(urls[0] + "?pair=" + self.pair_code) if on and urls else None}


def _set_phone(self, on):
    if on and self.lan_server is None:
        self.phone_token = secrets.token_urlsafe(24)
        self.pair_code = "%06d" % secrets.randbelow(10 ** 6)
        self.pair_fail = 0
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", self.lan_port), make_handler(self, remote=True))
        except OSError as e:
            self.phone_token = None
            raise ValueError("Could not open phone access on port %d: %s" % (self.lan_port, e))
        self.lan_server = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    elif not on and self.lan_server is not None:
        self.phone_token = None  # every paired phone is signed out
        self.pair_code = None
        srv, self.lan_server = self.lan_server, None
        threading.Thread(target=srv.shutdown, daemon=True).start()
    return self.phone_state()


def _try_pair(self, code):
    if self.phone_token is None or not self.pair_code:
        return 403, {"error": "Phone access is off on the laptop."}
    if self.pair_fail >= 8:
        return 429, {"error": "Too many wrong codes. Switch phone access off and on again on the laptop."}
    if secrets.compare_digest(code.strip(), self.pair_code):
        return 200, {"token": self.phone_token}
    self.pair_fail += 1
    return 403, {"error": "That code doesn't match the one on the laptop."}


def _receive_upload(self, stream, length, name, scan):
    safe = re.sub(r"[^\w.\- ]+", "_", os.path.basename(name))[:120] or "upload.bin"
    dest_dir = self.cat.dir("uploads", time.strftime("%Y%m%d-%H%M%S"))
    dest = os.path.join(dest_dir, safe)
    remaining = length
    with open(dest, "wb") as f:
        while remaining > 0:
            chunk = stream.read(min(remaining, 1 << 20))
            if not chunk:
                break
            f.write(chunk)
            remaining -= len(chunk)
    out = {"saved": safe, "size": length - remaining}
    if scan:
        src, extra = route_upload(safe)
        if src is None:  # a loose photo/video or unknown file: scan its folder as a search place
            src, extra = "folder", {"role": "search", "deep": True}
            params = dict(extra, path=dest_dir)
        else:
            params = dict(extra, path=dest)
        out["job"] = self.start_scan(src, params)
        out["source"] = src
    return out


def _export_zip(self, ids):
    tmp = self.cat.dir("exports", "_staging")
    res = exporter.export(self.cat, tmp, ids=ids)
    name = os.path.basename(res["folder"]).replace(" ", "_") + ".zip"
    zp = os.path.join(self.cat.dir("exports"), name)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for d, _s, files in os.walk(res["folder"]):
            for f in files:
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, res["folder"]))
    shutil.rmtree(res["folder"], ignore_errors=True)
    res.update({"zip": name, "url": "/api/download/" + urllib.parse.quote(name)})
    return res


def _self_zip(self):
    """Zip of Rewind itself, so a paired Android phone can install it."""
    zp = os.path.join(self.cat.dir("exports"), "Rewind-app.zip")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", ".venv", "Rewind.app", "tests", "tools")]
            for f in files:
                if f in ("install-android.sh", "Rewind.zip", "Rewind.apk"):
                    continue
                full = os.path.join(d, f)
                z.write(full, os.path.join("restore-app", os.path.relpath(full, root)))
        z.write(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "install-android.sh"),
                "restore-app/install-android.sh")
    return zp


def _shutdown(self):
    for s in self.servers + ([self.lan_server] if self.lan_server else []):
        threading.Thread(target=s.shutdown, daemon=True).start()
    hook = getattr(self, "on_quit", None)
    if hook:
        try:
            hook()
        except Exception:
            pass


App.phone_state = _phone_state
App.set_phone = _set_phone
App.try_pair = _try_pair
App.receive_upload = _receive_upload
App.export_zip = _export_zip
App.self_zip = _self_zip
App.shutdown = _shutdown


def serve(workspace, port=8777, open_browser=True, phone=False):
    from . import __version__
    print("Rewind %s starting: Python %s (%s), pid %d, port %d, workspace %s" % (
        __version__, sys.version.split()[0], sys.executable, os.getpid(), port, workspace), flush=True)
    app = App(workspace, port)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    except OSError as e:
        print("Rewind could not open port %d: %s" % (port, e), file=sys.stderr, flush=True)
        raise SystemExit(3)
    app.servers.append(httpd)
    url = "http://127.0.0.1:%d/" % port
    print("\n  Rewind is running at %s\n  Keep this window open. Press Ctrl+C to quit.\n" % url, flush=True)
    print("READY %s" % url, flush=True)
    if open_browser:
        def _open():
            if phone and shutil.which("termux-open-url"):
                subprocess.Popen(["termux-open-url", url])
            else:
                webbrowser.open(url)
        threading.Timer(1.0, _open).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Bye.")
    return app
