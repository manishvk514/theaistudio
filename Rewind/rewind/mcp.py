"""Rewind as an MCP server, so every Claude app can drive it.

Two ways in, same tools:

  stdio  `python3 -m rewind mcp`   Claude Desktop and Claude Code on the Mac start
         Rewind themselves. Runs on the Mac, so it can read the whole computer,
         phone backups, cards and the connected accounts.
  HTTP   POST /mcp on the Hugging Face Space. claude.ai (web and phone apps) adds
         it as a custom connector and signs in with Hugging Face (see space.py).
         Runs in the cloud, so it reaches accounts and uploaded exports only.

The protocol is JSON-RPC 2.0 (https://modelcontextprotocol.io). Only the parts
a tool server needs are implemented: initialize, ping, tools/list, tools/call.
"""
import base64
import json
import os
import sys
import threading
import time

from . import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Rewind finds deleted photos and videos wherever a copy still survives (bins, backups, caches, memory "
    "cards, chat apps, account exports, cloud trash) and separates them from what the user still has. "
    "Typical flow: rewind_status -> rewind_scan -> rewind_progress until done -> rewind_list view=deleted -> "
    "rewind_show to look at items -> rewind_restore. Nothing is ever deleted; restoring copies files out "
    "(or, only when asked, puts an item back inside Google Drive/OneDrive/Dropbox). Be honest about limits: "
    "a photo with no surviving copy anywhere cannot be recovered; use rewind_where_to_look to list every "
    "remaining route for a service.")


def _cloud():
    return os.environ.get("REWIND_CLOUD") == "1"


# ------------------------------------------------------------------ tool table

def _schema(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or [],
            "additionalProperties": False}


TOOLS = [
    {"name": "rewind_status",
     "description": "Counts of deleted / still-here / unrecoverable items, connected accounts, the running scan, "
                    "and where Rewind is running (Mac or cloud). Call this first.",
     "inputSchema": _schema(), "annotations": {"readOnlyHint": True}},
    {"name": "rewind_scan",
     "description": "Start a scan. source='auto' (default) visits every place Rewind knows on this machine plus "
                    "connected accounts and uploaded exports. Other sources need params, e.g. "
                    "source='folder' params={'path': '/Volumes/CARD', 'role': 'search'}; "
                    "source='carve' params={'path': '/path/card.img'} (memory-card or disk image). Returns a job id; follow it with "
                    "rewind_progress.",
     "inputSchema": _schema({"source": {"type": "string", "default": "auto"},
                             "params": {"type": "object", "default": {}},
                             "full": {"type": "boolean", "description": "Re-open exports/backups even if unchanged"}})},
    {"name": "rewind_progress",
     "description": "Progress of a scan job (latest if no id): each place with its status, items found, log tail.",
     "inputSchema": _schema({"job_id": {"type": "integer"}}), "annotations": {"readOnlyHint": True}},
    {"name": "rewind_list",
     "description": "List items. view: deleted (recoverable), trash, recovered (rebuilt), thumbnail (preview only), "
                    "missing (known lost, no copy), present (still here). Optional kind image|video, source, "
                    "sort newest|oldest|biggest, limit (max 200), offset.",
     "inputSchema": _schema({"view": {"type": "string", "default": "deleted"},
                             "kind": {"type": "string", "enum": ["image", "video"]},
                             "source": {"type": "string"},
                             "sort": {"type": "string", "enum": ["newest", "oldest", "biggest"]},
                             "limit": {"type": "integer", "default": 30},
                             "offset": {"type": "integer", "default": 0}}),
     "annotations": {"readOnlyHint": True}},
    {"name": "rewind_show",
     "description": "Details of one item and a preview image of it, so you can see the photo.",
     "inputSchema": _schema({"item_id": {"type": "integer"}}, ["item_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "rewind_restore",
     "description": "Restore deleted items: all recoverable ones, or item_ids. On the Mac they are copied to "
                    "Pictures/Rewind Restored; in the cloud you get a download link to a zip. "
                    "in_service=true instead puts cloud-trash items back inside Google Drive/OneDrive/Dropbox "
                    "(changes the account: ask the user first).",
     "inputSchema": _schema({"item_ids": {"type": "array", "items": {"type": "integer"}},
                             "in_service": {"type": "boolean", "default": False}})},
    {"name": "rewind_connect",
     "description": "Connect an account. gdrive/onedrive/dropbox: on the Mac the sign-in page opens by itself; "
                    "in the cloud it returns sign_in_url for the user to open. If the result says direct=true, "
                    "the account connects by itself after the user presses Allow (check with rewind_status). "
                    "Otherwise the user lands on a 'can't connect' 127.0.0.1/localhost page and gives you its "
                    "address: call again with pasted_url. telegram: returns a QR code image; show it and tell the "
                    "user to scan it in Telegram › Settings › Devices › Link Desktop Device (if it asks for the "
                    "one-time API ID, pass api_id and api_hash; if it says need_password, pass password). "
                    "snapchat: Snapchat has no sign-in for apps; returns the steps for its data download.",
     "inputSchema": _schema({"service": {"type": "string", "enum": ["gdrive", "onedrive", "dropbox", "telegram",
                                                                    "snapchat"]},
                             "api_id": {"type": "string"}, "api_hash": {"type": "string"},
                             "password": {"type": "string", "description": "Telegram two-step verification"},
                             "pasted_url": {"type": "string", "description": "Cloud only: the address the browser "
                                            "ended on after pressing Allow (starts with http://127.0.0.1:53682 or "
                                            "http://localhost:53682)"}}, ["service"])},
    {"name": "rewind_where_to_look",
     "description": "Every route by which a deleted photo/video can still come back for a service or device "
                    "(e.g. whatsapp, snapchat, google photos, iphone, android, onedrive, telegram, "
                    "'permanently deleted'), with what Rewind does automatically and what only the user can do.",
     "inputSchema": _schema({"service": {"type": "string"}}), "annotations": {"readOnlyHint": True}},
]
UPLOAD_TOOL = {"name": "rewind_upload_link",
               "description": "Link where the user can upload account exports (Google Takeout, Snapchat My Data, "
                              "WhatsApp chat export, Instagram download, iPhone backup zip) or a memory-card "
                              "image; Rewind scans each upload by itself.",
               "inputSchema": _schema(), "annotations": {"readOnlyHint": True}}


def tool_list():
    return TOOLS + ([UPLOAD_TOOL] if _cloud() else [])


CLOUD_SOURCES = {"auto", "google-takeout", "snapchat", "whatsapp", "telegram", "data-download", "mms",
                 "carve", "folder", "email", "google-drive", "onedrive", "dropbox"}


class ToolError(Exception):
    pass


# ------------------------------------------------------------------ the tools

class Tools:
    def __init__(self, app, base_url=None):
        self.app = app
        self.base_url = base_url      # set in cloud mode, for links

    def call(self, name, args):
        fn = getattr(self, "t_" + name.replace("rewind_", ""), None)
        if fn is None or not any(t["name"] == name for t in tool_list()):
            raise ToolError("Unknown tool: %s" % name)
        return fn(**(args or {}))

    def t_status(self):
        st = self.app.status()
        st["running_on"] = "Hugging Face (cloud: accounts and uploads only)" if _cloud() else \
            "this computer (%s)" % sys.platform
        st.pop("auto_job", None)
        jobs = [j for j in self.app.jobs.values() if j.state == "running"]
        st["running_scan"] = {"job_id": jobs[-1].id, "label": jobs[-1].label, "step": jobs[-1].step} if jobs else None
        return st

    def t_scan(self, source="auto", params=None, full=False):
        from .server import SOURCES
        params = dict(params or {})
        if full:
            params["full"] = True
        if source not in SOURCES:
            raise ToolError("Unknown source '%s'. Known: %s" % (source, ", ".join(sorted(SOURCES))))
        if _cloud() and source not in CLOUD_SOURCES:
            raise ToolError("'%s' reads a computer's own disk; it only works in Rewind on the Mac." % source)
        if _cloud() and params.get("path"):
            ws = os.path.realpath(self.app.cat.workspace)
            if not os.path.realpath(params["path"]).startswith(ws + os.sep):
                raise ToolError("In the cloud only uploaded files can be scanned (see rewind_upload_link).")
        for j in self.app.jobs.values():
            if j.state == "running" and j.source == source:
                return {"job_id": j.id, "note": "That scan is already running."}
        jid = self.app.start_scan(source, params)
        return {"job_id": jid, "note": "Scan started. Call rewind_progress to follow it."}

    def t_progress(self, job_id=None):
        jobs = self.app.jobs
        if not jobs:
            return {"note": "No scan has run since Rewind started. Use rewind_scan."}
        j = jobs.get(int(job_id)) if job_id else list(jobs.values())[-1]
        if not j:
            raise ToolError("No job %s" % job_id)
        d = j.as_dict()
        d["logs"] = d["logs"][-15:]
        d["places"] = [{k: p.get(k) for k in ("label", "status", "found", "note")} for p in d.get("places") or []]
        d["elapsed_s"] = round(time.time() - d.pop("started"))
        return d

    def t_list(self, view="deleted", kind=None, source=None, sort=None, limit=30, offset=0):
        q = {"view": view, "limit": max(1, min(int(limit), 200)), "offset": max(0, int(offset))}
        for k, v in (("kind", kind), ("source", source), ("sort", sort)):
            if v:
                q[k] = v
        res = self.app.items(q)
        keep = ("id", "name", "kind", "size", "taken", "deleted_at", "location", "evidence", "category",
                "source", "damaged", "restored_to", "copies", "width", "height")
        res["items"] = [{k: r.get(k) for k in keep if r.get(k) not in (None, "", 0) or k == "id"}
                        for r in res["items"]]
        return res

    def t_show(self, item_id):
        it = self.app.cat.get(int(item_id))
        if not it:
            raise ToolError("No item %s" % item_id)
        info = {k: it[k] for k in ("id", "name", "kind", "ext", "size", "width", "height", "taken", "deleted_at",
                                   "location", "evidence", "verdict", "category", "source", "damaged",
                                   "restored_to") if it.get(k) not in (None, "")}
        out = [{"type": "text", "text": json.dumps(info, indent=1)}]
        tp, ct = self.app.thumb(int(item_id))
        if tp and os.path.getsize(tp) <= 4 << 20:
            with open(tp, "rb") as f:
                out.append({"type": "image", "data": base64.b64encode(f.read()).decode(), "mimeType": ct})
        elif not it.get("path"):
            out.append({"type": "text", "text": "No copy of the bytes survives for this item, so there is no preview."})
        if self.base_url and it.get("path"):
            out.append({"type": "text", "text": "Open in Rewind: %s/#item-%d" % (self.base_url, it["id"])})
        return out

    def t_restore(self, item_ids=None, in_service=False):
        ids = [int(i) for i in item_ids] if item_ids else None
        if in_service:
            if not ids:
                raise ToolError("in_service needs item_ids (it changes the account, one item at a time).")
            return {"results": [self._remote(i) for i in ids]}
        if _cloud():
            res = self.app.export_zip(ids)
            url = self.app.signed_download(res["zip"]) if hasattr(self.app, "signed_download") else res["url"]
            return {"download": (self.base_url or "") + url, "files": res.get("copied"),
                    "note": "Link works for 24 hours. Open it to download a zip of the restored photos and videos."}
        return self.app.restore_all(ids)

    def _remote(self, i):
        try:
            return {"item_id": i, "ok": True, "message": self.app.remote_restore(i)}
        except Exception as e:
            return {"item_id": i, "ok": False, "message": str(e)}

    def t_connect(self, service, pasted_url=None, api_id=None, api_hash=None, password=None):
        if service == "telegram":
            return self._telegram(api_id, api_hash, password)
        if service == "snapchat":
            where = ("upload it here: %s/#upload" % self.base_url) if self.base_url else \
                "leave the downloaded zip in Downloads: Rewind opens it on its next scan"
            return {"steps": ["Open https://accounts.snapchat.com and sign in.",
                              "My Data: tick Memories (and Chat Media), choose the JSON format, press Submit.",
                              "Snapchat e-mails a download link (minutes to a day). Download the zip within a few "
                              "days: its Memories links expire.",
                              "Then " + where + "."],
                    "why": "Snapchat gives no app access to accounts; its data download is the only official way. "
                           "Opened snaps that were never saved are deleted from Snapchat's servers."}
        if hasattr(self.app, "connect_link"):           # cloud
            return self.app.connect_link(service, pasted_url)
        from .sources import connect
        if service not in connect.SERVICES:
            raise ToolError("Unknown service")
        connect.start_connect(self.app.cat, service)
        for _ in range(20):                               # wait for rclone's sign-in page
            st = connect.connected(self.app.cat).get(service, {})
            if st.get("url") or st.get("state") in ("connected", "error"):
                break
            time.sleep(0.5)
        st = connect.connected(self.app.cat).get(service, {})
        st["note"] = ("A sign-in page opened in the browser on the Mac. When the user presses Allow, the account "
                      "is connected; then run rewind_scan.")
        return st

    def _telegram(self, api_id, api_hash, password):
        from .sources import telegram_cloud
        tg = telegram_cloud.get(self.app.cat)
        if password:
            st = tg.password(password)
        elif tg.state == "connected" and not (api_id or api_hash):
            st = tg.status()
        else:
            st = tg.qr_start(api_id, api_hash)
        if st["state"] == "connected":
            return {"state": "connected", "note": "Telegram is connected. Run rewind_scan to download chat media."}
        if st["state"] == "need_app":
            return {"state": "need_app", "note": st["message"], "link": "https://my.telegram.org/apps"}
        if st["state"] == "need_qr" and st.get("qr_url"):
            out = [{"type": "text", "text": "Scan this with the Telegram app on your phone: Settings › Devices › "
                                            "Link Desktop Device. It is valid for about 30 seconds; call "
                                            "rewind_connect telegram again for a fresh one. Then check rewind_status."}]
            png = _qr_png(st["qr_url"])
            if png:
                out.append({"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"})
            if self.base_url:
                out.append({"type": "text", "text": "Or open %s and press Connect next to Telegram." % self.base_url})
            return out
        return {"state": st["state"], "note": st.get("message")}

    def t_where_to_look(self, service=None):
        from .knowledge import routes_for
        return routes_for(service)

    def t_upload_link(self):
        return {"url": (self.base_url or "") + "/#upload",
                "note": "Open this link, sign in with Hugging Face, and drop the files there. Large exports can take "
                        "a while to upload; each one is scanned by itself."}


def _qr_png(text):
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=8, border=3)
        return buf.getvalue()
    except Exception:
        return None


def _content(result):
    if isinstance(result, list):
        return result
    return [{"type": "text", "text": json.dumps(result, indent=1, default=str)}]


# ------------------------------------------------------------------ JSON-RPC

def handle(tools, msg):
    """One JSON-RPC message in, response dict out (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:          # notification (e.g. notifications/initialized): nothing to answer
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "rewind", "title": "Rewind: recover deleted photos and videos",
                                  "version": __version__},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        name = params.get("name", "")
        try:
            res = tools.call(name, params.get("arguments") or {})
            out = {"content": _content(res)}
            if isinstance(res, dict):
                out["structuredContent"] = json.loads(json.dumps(res, default=str))
            return ok(out)
        except (ToolError, ValueError, KeyError, RuntimeError, TypeError) as e:
            return ok({"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True})
        except Exception as e:      # never let one tool crash the server
            return ok({"content": [{"type": "text", "text": "Unexpected problem: %s" % e}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def handle_payload(tools, payload):
    """HTTP body (one message or a batch) -> response body or None."""
    if isinstance(payload, list):
        out = [r for r in (handle(tools, m) for m in payload) if r is not None]
        return out or None
    return handle(tools, payload)


# ------------------------------------------------------------------ stdio

def run_stdio(workspace):
    """Claude Desktop / Claude Code start this and talk over stdin/stdout."""
    from .server import App
    real_stdout = sys.stdout
    sys.stdout = sys.stderr          # anything a source prints must not corrupt the protocol stream
    app = App(workspace, 0)
    tools = Tools(app)
    lock = threading.Lock()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            resp = handle_payload(tools, msg)
        if resp is not None:
            with lock:
                real_stdout.write(json.dumps(resp, default=str) + "\n")
                real_stdout.flush()


# ------------------------------------------------------------------ one-step setup for Claude apps

def claude_desktop_config_path():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
    return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")


def server_command(workspace):
    """How Claude should start Rewind: the same Python and code that are running now."""
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return {"command": sys.executable, "args": ["-m", "rewind", "--workspace", workspace, "mcp"],
            "env": {"PYTHONPATH": pkg_root}}


def install_claude(workspace, config_path=None, run=None):
    """Register Rewind with Claude Desktop (config file) and Claude Code (`claude mcp add`)."""
    import shutil
    import subprocess
    run = run or subprocess.run
    done = []
    entry = server_command(workspace)
    path = config_path or claude_desktop_config_path()
    try:
        cfg = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            shutil.copy2(path, path + ".before-rewind")
        cfg.setdefault("mcpServers", {})["rewind"] = entry
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        done.append("Claude Desktop: added (quit and reopen Claude Desktop to load it). Config: %s" % path)
    except (OSError, ValueError) as e:
        done.append("Claude Desktop: could not update %s (%s)" % (path, e))
    claude = shutil.which("claude")
    if claude:
        env_args = sum((["-e", "%s=%s" % kv] for kv in entry["env"].items()), [])
        r = run([claude, "mcp", "add", "--scope", "user", *env_args, "rewind", "--", entry["command"], *entry["args"]],
                capture_output=True, text=True)
        done.append("Claude Code: " + ("added for all your projects." if r.returncode == 0 else
                                        "not added (%s)" % ((r.stderr or r.stdout).strip()[:200] or "error")))
    else:
        done.append("Claude Code: not installed on this computer (skipped).")
    return done
