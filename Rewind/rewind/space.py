"""Rewind on Hugging Face Spaces: the cloud half.

What runs here, and why it is different from the Mac:
  * A Space is a computer in Hugging Face's data centre. It cannot see your Mac,
    your phone or your memory cards. It works on what can reach it over the
    internet: your cloud accounts (Google Drive, OneDrive, Dropbox, Telegram,
    email) and files you upload (Google Takeout, Snapchat My Data, WhatsApp
    exports, iPhone backups, memory-card images).
  * Every page and API needs "Sign in with Hugging Face", and only the Space's
    owner (or names in REWIND_ALLOWED_USERS) gets in.
  * Claude connects through /mcp. claude.ai, the Claude phone apps and Claude
    Desktop add it as a custom connector; the sign-in that opens is this same
    Hugging Face login, implemented as a small OAuth 2.1 server (MCP's
    authorization spec: protected-resource metadata, dynamic client
    registration, PKCE). Claude Code can also send a Hugging Face token directly.

Everything that must survive a restart (sessions, Claude's tokens, download
links) is signed with SPACE_SIGNING_SECRET instead of being stored.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from . import __version__, mcp

# ------------------------------------------------------------------ settings


class Config:
    def __init__(self, env=None):
        e = env if env is not None else os.environ
        host = e.get("SPACE_HOST", "").strip()
        self.base = (e.get("REWIND_BASE_URL") or ("https://" + host if host else "http://localhost:%s" %
                                                  e.get("PORT", "7860"))).rstrip("/")
        self.secure = self.base.startswith("https://")
        self.hf = (e.get("OPENID_PROVIDER_URL") or "https://huggingface.co").rstrip("/")
        self.client_id = e.get("OAUTH_CLIENT_ID", "")
        self.client_secret = e.get("OAUTH_CLIENT_SECRET", "")
        users = e.get("REWIND_ALLOWED_USERS") or e.get("SPACE_AUTHOR_NAME") or ""
        self.allowed = {u.strip().lower() for u in users.split(",") if u.strip()}
        secret = e.get("SPACE_SIGNING_SECRET") or e.get("REWIND_SECRET") or ""
        self.ephemeral_secret = not secret
        self.key = hashlib.sha256(("rewind|" + (secret or secrets.token_hex(32))).encode()).digest()

    def user_ok(self, user):
        return bool(user) and user.lower() in self.allowed


# ------------------------------------------------------------------ signed tokens

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    def __init__(self, key):
        self.key = key

    def sign(self, kind, data, ttl):
        body = dict(data, k=kind, exp=int(time.time() + ttl))
        raw = _b64(json.dumps(body, separators=(",", ":")).encode())
        mac = _b64(hmac.new(self.key, raw.encode(), hashlib.sha256).digest()[:24])
        return raw + "." + mac

    def unsign(self, kind, token):
        try:
            raw, mac = (token or "").split(".", 1)
            want = _b64(hmac.new(self.key, raw.encode(), hashlib.sha256).digest()[:24])
            if not hmac.compare_digest(mac, want):
                return None
            body = json.loads(_unb64(raw))
        except (ValueError, TypeError):
            return None
        if body.get("k") != kind or body.get("exp", 0) < time.time():
            return None
        return body


def pkce_challenge(verifier):
    return _b64(hashlib.sha256(verifier.encode()).digest())


# ------------------------------------------------------------------ Hugging Face

def _post_form(url, form, auth=None):
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % auth).encode()).decode()
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _get_json(url, bearer):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class HFTokenCache:
    """Claude Code and scripts may send a Hugging Face access token directly."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = {}

    def user(self, token):
        h = hashlib.sha256(token.encode()).hexdigest()
        hit = self.cache.get(h)
        if hit and hit[1] > time.time():
            return hit[0]
        try:
            name = _get_json(self.cfg.hf + "/api/whoami-v2", token).get("name")
        except Exception:
            name = None
        self.cache[h] = (name, time.time() + 600)
        return name


# ------------------------------------------------------------------ pages

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Rewind</title>
<style>:root{{color-scheme:dark light}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#0d0f12;color:#e8eaed}}
@media(prefers-color-scheme:light){{body{{background:#f5f6f8;color:#16181d}}.c{{background:#fff!important}}}}
.c{{max-width:460px;margin:16px;padding:32px;border-radius:18px;background:#171a1f;box-shadow:0 8px 40px #0004}}
h1{{margin:0 0 6px;font-size:24px}}.logo{{font-size:34px}}p{{opacity:.85}}
a.b,button{{display:inline-block;border:0;border-radius:12px;padding:12px 18px;background:#ffd21e;color:#111;
font:600 16px system-ui;text-decoration:none;cursor:pointer;margin:6px 8px 0 0}}
.g{{background:transparent!important;color:inherit!important;border:1px solid #8886!important}}
small{{opacity:.7}}code{{font-size:13px;word-break:break-all}}</style></head>
<body><div class="c"><div class="logo">⟲</div>{body}</div></body></html>"""


def page(body):
    return PAGE.format(body=body)


def login_page(next_url, cfg, error=""):
    href = "/login?next=" + urllib.parse.quote(next_url, safe="")
    msg = "<p style='color:#f88'>%s</p>" % html.escape(error) if error else ""
    setup = "" if cfg.client_id else ("<p><small>Sign-in is not switched on: add <code>hf_oauth: true</code> to this "
                                      "Space's README.</small></p>")
    return page("""<h1>Rewind</h1><p>Bring back deleted photos and videos from your accounts and exports.</p>%s%s
<a class="b" id="go" href="%s">Sign in with Hugging Face</a>
<p id="frame" hidden><small>This page is shown inside Hugging Face. Sign-in works best in its own tab:</small><br>
<a class="b g" href="%s" target="_blank" rel="noopener">Open Rewind in a new tab ↗</a></p>
<script>if(window.top!==window.self){document.getElementById('frame').hidden=false;
document.getElementById('go').target='_blank';}</script>""" % (msg, setup, html.escape(href), html.escape(cfg.base)))


def setup_page(cfg):
    from .sources import oauth_direct
    sid = os.environ.get("SPACE_ID", "")
    settings = "https://huggingface.co/spaces/%s/settings" % sid if sid else "the Space's Settings page"
    cards = []
    for key, p in oauth_direct.PROVIDERS.items():
        on = oauth_direct.configured(key)
        steps = "".join("<li>%s</li>" % html.escape(x) for x in p["steps"])
        cards.append("""<h2>%s %s</h2>%s<ol>%s</ol>
<p>Address to paste: <code>%s</code></p>
<p>Then in <a href="%s" target="_blank" rel="noopener">%s</a> › Variables and secrets add two <b>secrets</b>:
<code>%s</code> and <code>%s</code>. The Space restarts by itself.</p>%s
<p><a class="b g" href="%s" target="_blank" rel="noopener">Open the %s developer page ↗</a></p><hr>""" % (
            html.escape(p["title"]), "✓ one-click is on" if on else "",
            "" if on else "<p>Right now Connect uses the copy-the-address method. Do this once to make it one click:</p>",
            steps, html.escape(oauth_direct.redirect_uri(cfg.base, key)), html.escape(settings),
            "Space settings", p["env"][0], p["env"][1],
            ("<p><small>%s</small></p>" % html.escape(p["note"])) if p["note"] else "",
            html.escape(p["console"]), html.escape(p["title"])))
    tg = ("<h2>Telegram</h2><p>Optional: add secrets <code>TELEGRAM_API_ID</code> and <code>TELEGRAM_API_HASH</code> "
          "(from <a href='https://my.telegram.org/apps' target='_blank' rel='noopener'>my.telegram.org › API development "
          "tools</a>) so Rewind never asks for them. Sign-in itself is a QR code you scan in the Telegram app.</p>")
    body = ("<h1>One-click sign-in</h1><p>Free, done once. It lets Google, Microsoft and Dropbox send you straight "
            "back to this Rewind after you press Allow.</p><hr>" + "".join(cards) + tg)
    return PAGE.format(body=body).replace("max-width:460px", "max-width:760px")


def consent_page(client_name, user, form_token):
    return page("""<h1>Allow %s to use Rewind?</h1>
<p>It will be able to scan your connected accounts and uploads, look at what was found, and restore photos and
videos for <b>%s</b>. It cannot delete anything.</p>
<form method="post" action="/oauth/approve"><input type="hidden" name="req" value="%s">
<button name="ok" value="1">Allow</button><button class="g" name="ok" value="0">Cancel</button></form>
<p><small>You can remove this access any time by restarting the Space with a new signing secret.</small></p>""" % (
        html.escape(client_name), html.escape(user), html.escape(form_token)))


# ------------------------------------------------------------------ the server


class SpaceApp:
    """Cloud additions bolted onto Rewind's App."""

    def __init__(self, app, cfg):
        self.app, self.cfg = app, cfg
        self.signer = Signer(cfg.key)
        self.hf_tokens = HFTokenCache(cfg)
        self.used_codes = {}
        self.uploads = {}
        self.tools = mcp.Tools(app, cfg.base)
        app.connect_link = self.connect_link
        app.signed_download = self.signed_download

    # -- links the tools hand out

    def signed_download(self, zip_name):
        return "/dl/" + self.signer.sign("dl", {"f": zip_name}, 24 * 3600)

    def connect_link(self, service, pasted_url=None):
        from .sources import connect, oauth_direct
        if service not in connect.SERVICES:
            raise ValueError("Unknown service. Use gdrive, onedrive or dropbox.")
        if not pasted_url and oauth_direct.configured(service):
            state = self.signer.sign("prov", {"s": service}, 900)
            connect.STATE[service] = {"state": "waiting", "direct": True,
                                      "message": "Sign in on the %s page and press Allow; Rewind connects by itself."
                                                 % connect.SERVICES[service][1]}
            return {"sign_in_url": oauth_direct.auth_url(service, self.cfg.base, state), "direct": True,
                    "steps": ["Open sign_in_url, sign in and press Allow. Rewind connects by itself and starts "
                              "scanning; nothing to copy back."]}
        if pasted_url:
            st = connect.headless_finish(self.app.cat, service, pasted_url)
            self.app.start_scan("auto", {})
            return {"state": st["state"], "note": "%s connected; a scan has started." % connect.SERVICES[service][1]}
        url = connect.headless_start(self.app.cat, service)
        return {"sign_in_url": url,
                "steps": ["Open sign_in_url and press Allow.",
                          "Your browser then shows a 'can't connect' page on 127.0.0.1 or localhost: that is expected.",
                          "Copy that page's whole address and give it back (rewind_connect with pasted_url, "
                          "or paste it in Rewind's Accounts box)."],
                "expires": "Finish within 10 minutes.",
                "one_click": "To skip the copy step, add this Space's own app keys once: %s/setup" % self.cfg.base}

    # -- who is asking

    def session_user(self, handler):
        c = _cookies(handler).get("rw_s")
        s = self.signer.unsign("s", c) if c else None
        return s["u"] if s and self.cfg.user_ok(s["u"]) else None

    def bearer_user(self, handler):
        auth = handler.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        tok = auth[7:].strip()
        at = self.signer.unsign("at", tok)
        if at:
            return at["u"] if self.cfg.user_ok(at["u"]) else None
        if tok.startswith("hf_"):
            u = self.hf_tokens.user(tok)
            return u if self.cfg.user_ok(u) else None
        return None


def _cookies(handler):
    out = {}
    for part in (handler.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            out[k] = v
    return out


LOCAL_ONLY = {"/api/browse", "/api/open", "/api/quit", "/api/phone", "/api/settings", "/api/deep_scan",
              "/api/claude_setup"}


def make_space_handler(app, sp):
    from .server import make_handler, WEB
    Base = make_handler(app)
    cfg = sp.cfg

    class H(Base):
        server_version = "Rewind/" + __version__

        # ---- the base handler's checks, swapped for Hugging Face sign-in
        def _host_ok(self):
            return True

        def _local(self):
            return False

        def _authed(self, qs):
            return bool(sp.session_user(self) or sp.bearer_user(self))

        def _token_for_page(self):
            return ""

        # ---- helpers
        def _redirect(self, url, cookies=()):
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _cookie(self, name, value, max_age):
            attrs = ["%s=%s" % (name, value), "Path=/", "HttpOnly", "Max-Age=%d" % max_age]
            attrs += ["Secure", "SameSite=None"] if cfg.secure else ["SameSite=Lax"]
            return "; ".join(attrs)

        def _cors(self):
            return {"Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Session-Id, MCP-Protocol-Version",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Expose-Headers": "WWW-Authenticate, Mcp-Session-Id"}

        def _form(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(n, 1 << 20)).decode()
            if "json" in (self.headers.get("Content-Type") or ""):
                try:
                    return json.loads(raw or "{}")
                except ValueError:
                    return {}
            return dict(urllib.parse.parse_qsl(raw))

        def _oauth_error(self, code, err, desc):
            return self._send(code, {"error": err, "error_description": desc}, extra=self._cors())

        def do_OPTIONS(self):
            self.send_response(204)
            for k, v in self._cors().items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- GET
        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            qs = dict(urllib.parse.parse_qsl(u.query))
            p = u.path
            try:
                if p in ("/", "/index.html"):
                    if not sp.session_user(self):
                        return self._send(200, login_page("/", cfg), "text/html; charset=utf-8")
                    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                        text = f.read().replace("__TOKEN__", "").replace("__REMOTE__", "0").replace(
                            "</head>", "<script>window.REWIND_CLOUD = %s;</script></head>" % json.dumps(
                                {"mcp": cfg.base + "/mcp", "user": sp.session_user(self)}))
                    return self._send(200, text, "text/html; charset=utf-8")
                if p == "/login":
                    return self._login(qs.get("next") or "/")
                if p == "/login/callback":
                    return self._login_callback(qs)
                if p == "/logout":
                    return self._redirect("/", [self._cookie("rw_s", "", 0)])
                if p.startswith("/.well-known/oauth-protected-resource"):
                    return self._send(200, {"resource": cfg.base + "/mcp", "authorization_servers": [cfg.base],
                                            "bearer_methods_supported": ["header"], "scopes_supported": ["rewind"],
                                            "resource_name": "Rewind"}, extra=self._cors())
                if p.startswith("/.well-known/oauth-authorization-server") or \
                        p.startswith("/.well-known/openid-configuration"):
                    return self._send(200, {
                        "issuer": cfg.base, "authorization_endpoint": cfg.base + "/oauth/authorize",
                        "token_endpoint": cfg.base + "/oauth/token", "registration_endpoint": cfg.base + "/oauth/register",
                        "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                        "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                        "scopes_supported": ["rewind"]}, extra=self._cors())
                if p == "/oauth/authorize":
                    return self._authorize(qs)
                if p.startswith("/dl/"):
                    d = sp.signer.unsign("dl", p[4:])
                    fp = os.path.join(app.cat.dir("exports"), os.path.basename(d["f"])) if d else None
                    if not fp or not os.path.isfile(fp):
                        return self._send(404, page("<h1>This download link has expired.</h1><p>Ask Claude to "
                                                    "restore again, or use Rewind's Restore button.</p>"),
                                          "text/html; charset=utf-8")
                    return self._file(fp, "application/zip", os.path.basename(fp))
                if p == "/mcp":
                    return self._send(405, {"error": "Use POST"}, extra=dict(self._cors(), Allow="POST"))
                if p.startswith("/oauth/") and p.endswith("/callback"):
                    return self._provider_callback(p.split("/")[2], qs)
                if p == "/setup":
                    if not sp.session_user(self):
                        return self._redirect("/login?next=/setup")
                    return self._send(200, setup_page(cfg), "text/html; charset=utf-8")
                if p == "/api/space":
                    if not self._authed(qs):
                        return self._send(403, {"error": "forbidden"})
                    from .sources import oauth_direct
                    return self._send(200, {"mcp": cfg.base + "/mcp", "user": sp.session_user(self),
                                            "one_click": {k: oauth_direct.configured(k) for k in oauth_direct.PROVIDERS},
                                            "persistent": app.cat.workspace.startswith("/data"),
                                            "ephemeral_secret": cfg.ephemeral_secret})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
            return super().do_GET()

        # ---- POST
        def do_POST(self):
            u = urllib.parse.urlsplit(self.path)
            p = u.path
            try:
                if p == "/mcp":
                    return self._mcp()
                if p == "/oauth/register":
                    return self._register()
                if p == "/oauth/token":
                    return self._token()
                if p == "/oauth/approve":
                    return self._approve()
                if p in LOCAL_ONLY:
                    return self._send(403, {"error": "This only works in Rewind on your Mac."})
                if p == "/api/connect":
                    if not self._authed({}):
                        return self._send(403, {"error": "forbidden"})
                    body = self._form()
                    from .sources import connect
                    if body.get("service") not in connect.SERVICES:
                        return self._send(400, {"error": "unknown service"})
                    res = sp.connect_link(body["service"])
                    out = connect.connected(app.cat)
                    out["sign_in_url"] = res["sign_in_url"]
                    return self._send(200, out)
                if p == "/api/connect_finish":
                    if not self._authed({}):
                        return self._send(403, {"error": "forbidden"})
                    body = self._form()
                    try:
                        return self._send(200, sp.connect_link(body.get("service"), body.get("url") or ""))
                    except (ValueError, RuntimeError) as e:
                        print("%s connect_finish %s -> %s" % (time.strftime("%H:%M:%S"), body.get("service"), e),
                              flush=True)
                        return self._send(400, {"error": str(e)})
                if p == "/api/upload_part":
                    return self._upload_part(dict(urllib.parse.parse_qsl(u.query)))
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
            return super().do_POST()

        # ---- coming back from Google / Microsoft / Dropbox
        def _provider_callback(self, service, qs):
            from .sources import connect, oauth_direct

            def done(ok, msg):
                script = ("<script>try{if(window.opener){window.opener.postMessage('rewind-connected','*');"
                          "setTimeout(function(){window.close()},1200)}}catch(e){}</script>") if ok else ""
                return self._send(200 if ok else 400, page(
                    "<h1>%s</h1><p>%s</p><a class='b' href='/'>Back to Rewind</a>%s" % (
                        "Connected" if ok else "Not connected", html.escape(msg), script)), "text/html; charset=utf-8")
            st = sp.signer.unsign("prov", qs.get("state", ""))
            if service not in oauth_direct.PROVIDERS or not st or st["s"] != service:
                return done(False, "This sign-in link expired or came from somewhere else. Press Connect again in Rewind.")
            if not sp.session_user(self):
                return done(False, "Sign in to Rewind first, then press Connect again.")
            if "code" not in qs:
                connect.STATE[service] = {"state": "error", "message": "Sign-in was cancelled."}
                return done(False, "Sign-in was cancelled (%s)." % (qs.get("error_description") or qs.get("error") or
                                                                    "no code"))
            try:
                title = oauth_direct.exchange(app.cat, service, cfg.base, qs["code"])
            except Exception as e:
                print("%s oauth %s -> %s" % (time.strftime("%H:%M:%S"), service, e), flush=True)
                connect.STATE[service] = {"state": "error", "message": str(e)}
                return done(False, str(e))
            app.start_scan("auto", {})
            return done(True, "%s is connected. Rewind is scanning it now; you can close this tab." % title)

        # ---- Hugging Face sign-in
        def _login(self, next_url):
            if not cfg.client_id:
                return self._send(503, login_page("/", cfg, "Hugging Face sign-in is not configured."),
                                  "text/html; charset=utf-8")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = "/"
            verifier = secrets.token_urlsafe(48)
            state = secrets.token_urlsafe(16)
            st = sp.signer.sign("login", {"v": verifier, "s": state, "n": next_url}, 900)
            url = cfg.hf + "/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": cfg.client_id, "redirect_uri": cfg.base + "/login/callback", "response_type": "code",
                "scope": "openid profile", "state": state, "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256"})
            return self._redirect(url, [self._cookie("rw_login", st, 900)])

        def _login_callback(self, qs):
            st = sp.signer.unsign("login", _cookies(self).get("rw_login", ""))
            if not st or not secrets.compare_digest(st["s"], qs.get("state", "")):
                return self._send(400, login_page("/", cfg, "Sign-in expired or came from another tab. Try again."),
                                  "text/html; charset=utf-8")
            if "code" not in qs:
                return self._send(400, login_page("/", cfg, "Sign-in was cancelled."), "text/html; charset=utf-8")
            try:
                tok = _post_form(cfg.hf + "/oauth/token", {
                    "client_id": cfg.client_id, "code": qs["code"], "grant_type": "authorization_code",
                    "redirect_uri": cfg.base + "/login/callback", "code_verifier": st["v"]},
                    auth=(cfg.client_id, cfg.client_secret) if cfg.client_secret else None)
                info = _get_json(cfg.hf + "/oauth/userinfo", tok["access_token"])
            except (urllib.error.URLError, KeyError, ValueError) as e:
                return self._send(502, login_page("/", cfg, "Hugging Face sign-in failed: %s" % e),
                                  "text/html; charset=utf-8")
            user = info.get("preferred_username") or info.get("name") or ""
            if not cfg.user_ok(user):
                return self._send(403, page("<h1>Not your Rewind</h1><p>You are signed in as <b>%s</b>. This Rewind "
                                            "belongs to someone else. Duplicate the Space to get your own.</p>"
                                            % html.escape(user)), "text/html; charset=utf-8")
            sess = sp.signer.sign("s", {"u": user}, 30 * 86400)
            return self._redirect(st["n"], [self._cookie("rw_s", sess, 30 * 86400), self._cookie("rw_login", "", 0)])

        # ---- OAuth for Claude (MCP authorization)
        def _register(self):
            body = self._form()
            uris = body.get("redirect_uris") or []
            if not isinstance(uris, list) or not uris or len(uris) > 10:
                return self._oauth_error(400, "invalid_redirect_uri", "redirect_uris is required")
            for r in uris:
                pr = urllib.parse.urlsplit(str(r))
                local = pr.hostname in ("localhost", "127.0.0.1", "::1")
                if not (pr.scheme == "https" or (pr.scheme == "http" and local)) or pr.fragment:
                    return self._oauth_error(400, "invalid_redirect_uri", "redirect URIs must be https or localhost")
            name = str(body.get("client_name") or "Claude")[:80]
            cid = sp.signer.sign("client", {"r": uris, "n": name}, 3650 * 86400)
            return self._send(201, {"client_id": cid, "client_id_issued_at": int(time.time()), "client_name": name,
                                    "redirect_uris": uris, "token_endpoint_auth_method": "none",
                                    "grant_types": ["authorization_code", "refresh_token"],
                                    "response_types": ["code"]}, extra=self._cors())

        def _authorize(self, qs):
            client = sp.signer.unsign("client", qs.get("client_id", ""))
            redirect = qs.get("redirect_uri", "")
            if not client or redirect not in client["r"]:
                return self._send(400, page("<h1>Unknown app</h1><p>This sign-in link is not valid. Add the "
                                            "connector again in Claude.</p>"), "text/html; charset=utf-8")
            if qs.get("response_type") != "code" or qs.get("code_challenge_method") != "S256" or \
                    not qs.get("code_challenge"):
                return self._redirect(_add_query(redirect, {"error": "invalid_request", "state": qs.get("state", ""),
                                                            "error_description": "PKCE S256 is required"}))
            user = sp.session_user(self)
            if not user:
                return self._redirect("/login?next=" + urllib.parse.quote(self.path, safe=""))
            req = sp.signer.sign("consent", {"c": qs["client_id"], "r": redirect, "ch": qs["code_challenge"],
                                             "st": qs.get("state", ""), "u": user, "n": client["n"]}, 900)
            return self._send(200, consent_page(client["n"], user, req), "text/html; charset=utf-8",
                              {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"})

        def _approve(self):
            f = self._form()
            req = sp.signer.unsign("consent", f.get("req", ""))
            if not req or sp.session_user(self) != req["u"]:
                return self._send(400, page("<h1>This request expired.</h1><p>Start again from Claude.</p>"),
                                  "text/html; charset=utf-8")
            if f.get("ok") != "1":
                return self._redirect(_add_query(req["r"], {"error": "access_denied", "state": req["st"]}))
            code = sp.signer.sign("code", {"c": req["c"], "r": req["r"], "ch": req["ch"], "u": req["u"],
                                           "j": secrets.token_hex(8)}, 300)
            return self._redirect(_add_query(req["r"], {"code": code, "state": req["st"]}))

        def _token(self):
            f = self._form()
            gt = f.get("grant_type")
            if gt == "authorization_code":
                c = sp.signer.unsign("code", f.get("code", ""))
                if not c or c["j"] in sp.used_codes:
                    return self._oauth_error(400, "invalid_grant", "code is invalid, expired or already used")
                if f.get("client_id") and f["client_id"] != c["c"]:
                    return self._oauth_error(400, "invalid_grant", "client mismatch")
                if f.get("redirect_uri", c["r"]) != c["r"]:
                    return self._oauth_error(400, "invalid_grant", "redirect_uri mismatch")
                if not f.get("code_verifier") or not hmac.compare_digest(pkce_challenge(f["code_verifier"]), c["ch"]):
                    return self._oauth_error(400, "invalid_grant", "PKCE verification failed")
                now = time.time()
                sp.used_codes = {k: v for k, v in sp.used_codes.items() if v > now}
                sp.used_codes[c["j"]] = now + 600
                user, cid = c["u"], c["c"]
            elif gt == "refresh_token":
                r = sp.signer.unsign("rt", f.get("refresh_token", ""))
                if not r:
                    return self._oauth_error(400, "invalid_grant", "refresh token is invalid or expired")
                user, cid = r["u"], r["c"]
            else:
                return self._oauth_error(400, "unsupported_grant_type", "use authorization_code or refresh_token")
            if not cfg.user_ok(user):
                return self._oauth_error(400, "invalid_grant", "this account may no longer use Rewind")
            return self._send(200, {
                "access_token": sp.signer.sign("at", {"u": user, "c": hashlib.sha256(cid.encode()).hexdigest()[:16]},
                                               3600),
                "token_type": "Bearer", "expires_in": 3600, "scope": "rewind",
                "refresh_token": sp.signer.sign("rt", {"u": user, "c": cid}, 30 * 86400)}, extra=self._cors())

        # ---- the Claude endpoint
        def _mcp(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 8 << 20))
            if not sp.bearer_user(self):
                return self._send(401, {"error": "sign in required"}, extra=dict(self._cors(), **{
                    "WWW-Authenticate": 'Bearer realm="rewind", resource_metadata="%s/.well-known/'
                                        'oauth-protected-resource"' % cfg.base}))
            try:
                payload = json.loads(body or b"null")
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "Parse error"}}, extra=self._cors())
            resp = mcp.handle_payload(sp.tools, payload)
            if resp is None:
                self.send_response(202)
                for k, v in self._cors().items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send(200, json.loads(json.dumps(resp, default=str)), extra=self._cors())

        # ---- large uploads in parts (a Takeout zip can be many GB)
        def _upload_part(self, qs):
            n = int(self.headers.get("Content-Length") or 0)
            if not self._authed({}):
                self.rfile.read(min(n, 1 << 20))
                return self._send(403, {"error": "forbidden"})
            uid = re.sub(r"[^\w-]", "", qs.get("id", ""))[:40]
            name = re.sub(r"[^\w.\- ]+", "_", os.path.basename(qs.get("name") or "upload.bin"))[:120] or "upload.bin"
            if not uid:
                return self._send(400, {"error": "id required"})
            entry = sp.uploads.get(uid)
            if entry is None:
                d = app.cat.dir("uploads", time.strftime("%Y%m%d-%H%M%S") + "-" + uid[:6])
                entry = sp.uploads[uid] = {"path": os.path.join(d, name), "size": 0}
            if int(qs.get("offset", 0)) != entry["size"]:
                return self._send(409, {"error": "out of order", "have": entry["size"]})
            with open(entry["path"], "ab") as f:
                remaining = n
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            entry["size"] += n - remaining
            if qs.get("last") != "1":
                return self._send(200, {"have": entry["size"]})
            sp.uploads.pop(uid, None)
            return self._send(200, _scan_upload(app, entry["path"]))

    return H


def _scan_upload(app, path):
    from .server import route_upload
    name = os.path.basename(path)
    src, extra = route_upload(name)
    if src is None:
        src, extra = "folder", {"role": "search", "deep": True}
        params = dict(extra, path=os.path.dirname(path))
    else:
        params = dict(extra, path=path)
    return {"saved": name, "size": os.path.getsize(path), "source": src, "job": app.start_scan(src, params)}


def _add_query(url, params):
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v is not None]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def serve(workspace, port=7860, env=None):
    os.environ["REWIND_CLOUD"] = "1"
    from .server import App
    try:
        os.makedirs(workspace, exist_ok=True)
        probe = os.path.join(workspace, ".write-test")
        open(probe, "w").close()
        os.remove(probe)
    except OSError:
        print("Cannot write to %s; using /tmp/rewind (lost on restart)." % workspace, flush=True)
        workspace = "/tmp/rewind"
    cfg = Config(env)
    app = App(workspace, port)
    sp = SpaceApp(app, cfg)
    print("Rewind %s (Hugging Face mode) on port %d, workspace %s, base %s, allowed users: %s" % (
        __version__, port, workspace, cfg.base, ", ".join(sorted(cfg.allowed)) or "NOBODY (set REWIND_ALLOWED_USERS)"),
        flush=True)
    if cfg.ephemeral_secret:
        print("No SPACE_SIGNING_SECRET: sign-ins end when the Space restarts.", flush=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_space_handler(app, sp))
    httpd.daemon_threads = True
    app.servers.append(httpd)
    app.space = sp
    print("READY %s" % cfg.base, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return app
