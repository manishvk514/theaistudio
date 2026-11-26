"""Hugging Face mode end to end, against a stand-in Hugging Face sign-in server.

Walks the same path claude.ai takes to add Rewind as a connector (discovery ->
registration -> sign-in -> consent -> token -> tools), the web sign-in, chunked
uploads, the scan of an uploaded export, restore through a signed download link,
and the cloud account sign-in that goes through rclone.
Run: python3 tests/test_space.py
"""
import base64
import hashlib
import http.cookiejar
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["REWIND_CLOUD"] = "1"

from rewind import space, mcp  # noqa: E402
from rewind.server import App  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  -> %s" % (detail,)))
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------ fake Hugging Face

HF_CODES = {}


class FakeHF(BaseHTTPRequestHandler):
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
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        if self.path == "/oauth/token":
            want = HF_CODES.pop(form.get("code"), None)
            if not want:
                return self._json({"error": "invalid_grant"}, 400)
            user, challenge = want
            got = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if got != challenge:
                return self._json({"error": "pkce"}, 400)
            return self._json({"access_token": "hfat-" + user, "token_type": "Bearer"})
        self._json({}, 404)

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if self.path == "/oauth/userinfo" and auth.startswith("Bearer hfat-"):
            return self._json({"preferred_username": auth[len("Bearer hfat-"):]})
        if self.path == "/api/whoami-v2":
            return self._json({"name": {"Bearer hf_owner": "manishvk18", "Bearer hf_other": "stranger"}.get(auth)}
                              if auth in ("Bearer hf_owner", "Bearer hf_other") else {"error": "bad"},
                              200 if auth in ("Bearer hf_owner", "Bearer hf_other") else 401)
        self._json({}, 404)


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def browser():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect), jar


def req(opener, url, data=None, headers=None, method=None):
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        resp = opener.open(r, timeout=30)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    tmp = tempfile.mkdtemp(prefix="rewind-space-")
    hf = start(ThreadingHTTPServer(("127.0.0.1", 0), FakeHF))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)   # placeholder to get a port
    port = httpd.server_address[1]
    httpd.server_close()
    base = "http://127.0.0.1:%d" % port
    env = {"REWIND_BASE_URL": base, "OPENID_PROVIDER_URL": hf, "OAUTH_CLIENT_ID": "space-client",
           "SPACE_AUTHOR_NAME": "manishvk18", "SPACE_SIGNING_SECRET": "test-secret"}
    cfg = space.Config(env)
    app = App(os.path.join(tmp, "ws"), port)
    sp = space.SpaceApp(app, cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", port), space.make_space_handler(app, sp))
    start(srv)

    def hf_login(opener, user, next_url="/"):
        """Play the browser + Hugging Face: /login -> HF authorize -> /login/callback."""
        st, h, _ = req(opener, base + "/login?next=" + urllib.parse.quote(next_url, safe=""))
        loc = h.get("Location", "")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))
        code = "hfcode-" + user + str(time.time())
        HF_CODES[code] = (user, q.get("code_challenge"))
        return req(opener, base + "/login/callback?" + urllib.parse.urlencode({"code": code, "state": q.get("state")}))

    print("Web sign-in")
    b, jar = browser()
    st, h, body = req(b, base + "/")
    check("signed-out visitor sees the sign-in page", st == 200 and b"Sign in with Hugging Face" in body)
    st, h, body = req(b, base + "/api/status")
    check("API refuses a signed-out visitor", st == 403)
    st, h, body = req(b, base + "/login?next=/")
    loc = h.get("Location", "")
    check("sign-in goes to Hugging Face with PKCE", loc.startswith(hf + "/oauth/authorize") and "code_challenge=" in loc
          and "redirect_uri=" + urllib.parse.quote(base + "/login/callback", safe="") in loc, loc)
    st, h, body = hf_login(b, "manishvk18")
    check("owner is signed in and sent back", st == 302 and h.get("Location") == "/", (st, body[:200]))
    st, h, body = req(b, base + "/")
    check("owner gets the app, in cloud mode", st == 200 and b"window.REWIND_CLOUD" in body and b"/mcp" in body)
    st, h, body = req(b, base + "/api/status")
    check("owner can use the API with the session cookie", st == 200 and b"summary" in body, st)
    other, _ = browser()
    st, h, body = hf_login(other, "stranger")
    check("someone else's Hugging Face account is refused", st == 403 and b"Not your Rewind" in body, st)
    st, h, body = req(other, base + "/api/status")
    check("...and gets no API access", st == 403)
    bad, _ = browser()
    req(bad, base + "/login?next=/")
    st, h, body = req(bad, base + "/login/callback?code=x&state=forged")
    check("a forged sign-in callback is rejected", st == 400)
    st, h, body = req(b, base + "/api/browse", data=b"{}", headers={"Content-Type": "application/json"})
    check("Mac-only actions are refused in the cloud", st == 403)

    print("Claude connector (MCP authorization)")
    anon = urllib.request.build_opener(NoRedirect)
    st, h, body = req(anon, base + "/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
                      headers={"Content-Type": "application/json"})
    www = h.get("WWW-Authenticate", "")
    check("unauthenticated /mcp answers 401 with resource metadata", st == 401 and "resource_metadata=" in www, (st, www))
    st, h, body = req(anon, base + "/.well-known/oauth-protected-resource")
    prm = json.loads(body)
    check("protected-resource metadata names this server", prm["authorization_servers"] == [base]
          and prm["resource"] == base + "/mcp")
    st, h, body = req(anon, base + "/.well-known/oauth-authorization-server")
    asm = json.loads(body)
    check("authorization-server metadata has PKCE and registration", asm["code_challenge_methods_supported"] == ["S256"]
          and asm["registration_endpoint"].endswith("/oauth/register"))
    claude_cb = "https://claude.ai/api/mcp/auth_callback"
    st, h, body = req(anon, base + "/oauth/register", data=json.dumps({"client_name": "Claude",
                                                                       "redirect_uris": [claude_cb]}).encode(),
                      headers={"Content-Type": "application/json"})
    reg = json.loads(body)
    check("Claude can register itself", st == 201 and reg.get("client_id"), body[:200])
    st, _, _ = req(anon, base + "/oauth/register", data=json.dumps({"redirect_uris": ["http://evil.example/cb"]}).encode(),
                   headers={"Content-Type": "application/json"})
    check("plain-http redirect to another site is refused", st == 400)
    verifier = "v" * 50
    challenge = space.pkce_challenge(verifier)
    auth_q = urllib.parse.urlencode({"response_type": "code", "client_id": reg["client_id"], "redirect_uri": claude_cb,
                                     "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
                                     "resource": base + "/mcp"})
    fresh, _ = browser()
    st, h, body = req(fresh, base + "/oauth/authorize?" + auth_q)
    check("authorize sends a signed-out user to Hugging Face sign-in first", st == 302
          and h["Location"].startswith("/login?next="), (st, h.get("Location")))
    st, h, body = hf_login(fresh, "manishvk18", urllib.parse.unquote(h["Location"].split("next=", 1)[1]))
    check("after sign-in the user comes back to authorize", st == 302 and h["Location"].startswith("/oauth/authorize"))
    st, h, body = req(fresh, base + h["Location"])
    check("a consent page asks before giving Claude access", st == 200 and b"Allow Claude to use Rewind" in body
          and h.get("X-Frame-Options") == "DENY")
    form_req = body.decode().split('name="req" value="', 1)[1].split('"', 1)[0]
    st, h, body = req(fresh, base + "/oauth/approve", data={"req": form_req, "ok": "1"})
    loc = h.get("Location", "")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))
    check("approving redirects to Claude with a code and the same state", loc.startswith(claude_cb) and q.get("code")
          and q.get("state") == "xyz", loc)
    st, h, body = req(anon, base + "/oauth/token", data={"grant_type": "authorization_code", "code": q["code"],
                                                         "client_id": reg["client_id"], "redirect_uri": claude_cb,
                                                         "code_verifier": "wrong" * 10})
    check("a wrong PKCE verifier gets no token", st == 400)
    st, h, body = req(anon, base + "/oauth/token", data={"grant_type": "authorization_code", "code": q["code"],
                                                         "client_id": reg["client_id"], "redirect_uri": claude_cb,
                                                         "code_verifier": verifier})
    tok = json.loads(body)
    check("the right verifier gets access and refresh tokens", st == 200 and tok.get("access_token")
          and tok.get("refresh_token"), body[:200])
    st, _, _ = req(anon, base + "/oauth/token", data={"grant_type": "authorization_code", "code": q["code"],
                                                      "client_id": reg["client_id"], "redirect_uri": claude_cb,
                                                      "code_verifier": verifier})
    check("a code works only once", st == 400)
    st, h, body = req(anon, base + "/oauth/token", data={"grant_type": "refresh_token",
                                                         "refresh_token": tok["refresh_token"]})
    check("the refresh token renews access", st == 200 and json.loads(body).get("access_token"))

    def rpc(method, params=None, bearer=tok["access_token"], mid=1):
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        st, h, body = req(anon, base + "/mcp", data=json.dumps(msg).encode(),
                          headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                   "Authorization": "Bearer " + bearer})
        return st, (json.loads(body) if body else None)

    st, r = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "claude-ai", "version": "1"}})
    check("Claude can initialize", st == 200 and r["result"]["serverInfo"]["name"] == "rewind")
    st, _, _ = req(anon, base + "/mcp", data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok["access_token"]})
    check("notifications get 202 Accepted", st == 202)
    st, r = rpc("tools/list")
    names = [t["name"] for t in r["result"]["tools"]]
    check("cloud tools include the upload link", "rewind_upload_link" in names and "rewind_scan" in names, names)
    st, r = rpc("tools/call", {"name": "rewind_scan", "arguments": {"source": "computer"}})
    check("Mac-only sources are refused with an explanation", r["result"].get("isError")
          and "Mac" in r["result"]["content"][0]["text"])
    st, r = rpc("tools/call", {"name": "rewind_scan", "arguments": {"source": "folder", "params": {"path": "/etc"}}})
    check("paths outside the uploads are refused", r["result"].get("isError"))
    st, r = rpc("tools/list", bearer="hf_owner")
    check("Claude Code can use the owner's Hugging Face token directly", st == 200 and r["result"]["tools"])
    st, r = rpc("tools/list", bearer="hf_other")
    check("another person's Hugging Face token is refused", st == 401)
    st, r = rpc("tools/list", bearer=tok["access_token"][:-3] + "abc")
    check("a tampered token is refused", st == 401)

    print("Uploads, scan, restore")
    photo = make_jpeg()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("Takeout/Google Photos/Trip/IMG_0001.jpg", photo)
        z.writestr("Takeout/Google Photos/Trip/IMG_0001.jpg.supplemental-metadata.json",
                   json.dumps({"title": "IMG_0001.jpg", "trashed": True}))
    data = zbuf.getvalue()
    third = len(data) // 3
    parts = [data[:third], data[third:2 * third], data[2 * third:]]
    off, res = 0, None
    for i, part in enumerate(parts):
        st, h, body = req(b, base + "/api/upload_part?" + urllib.parse.urlencode(
            {"id": "u1", "name": "takeout-20261126.zip", "offset": off, "last": int(i == 2)}), data=part)
        off += len(part)
        res = json.loads(body)
    check("a file sent in three pieces is reassembled and scanned", st == 200 and res.get("source") == "google-takeout",
          res)
    st, h, body = req(b, base + "/api/upload_part?id=u2&name=x.jpg&offset=999&last=1", data=b"x")
    check("an out-of-order piece is refused", st == 409)
    job = app.jobs[res["job"]]
    for _ in range(120):
        if job.state != "running":
            break
        time.sleep(0.25)
    check("the uploaded export scan finishes", job.state == "done", job.logs[-5:])
    st, r = rpc("tools/call", {"name": "rewind_list", "arguments": {"view": "deleted"}})
    items = r["result"]["structuredContent"]["items"]
    check("Claude sees the photo as deleted", any(i.get("name") == "IMG_0001.jpg" for i in items), items)
    st, r = rpc("tools/call", {"name": "rewind_restore", "arguments": {}})
    dl = r["result"]["structuredContent"]["download"]
    check("restore gives a signed download link", dl.startswith(base + "/dl/"), dl)
    st, h, body = req(anon, dl)
    ok = False
    if st == 200:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            ok = any(z.read(n) == photo for n in z.namelist() if n.lower().endswith(".jpg"))
    check("the download holds the photo, byte for byte", ok, st)
    st, h, body = req(anon, dl[:-4] + "AAAA")
    check("a tampered download link is refused", st == 404)

    print("Cloud account sign-in through rclone")
    if shutil.which("rclone"):
        st, h, body = req(b, base + "/api/connect", data=json.dumps({"service": "gdrive"}).encode(),
                          headers={"Content-Type": "application/json"})
        j = json.loads(body)
        check("Connect returns Google's real sign-in page", st == 200 and
              j.get("sign_in_url", "").startswith("https://accounts.google.com/"), body[:300])
        check("...and the account shows as waiting", j.get("gdrive", {}).get("state") == "waiting")
        st, h, body = req(b, base + "/api/connect_finish", data=json.dumps(
            {"service": "gdrive", "url": "http://127.0.0.1:53682/?state=abc"}).encode(),
            headers={"Content-Type": "application/json"})
        check("pasting an address without a code explains what to copy", st == 400 and b"no sign-in code" in body, body)
        st, r = rpc("tools/call", {"name": "rewind_connect", "arguments": {"service": "dropbox"}})
        sc = r["result"].get("structuredContent") or {}
        check("Claude gets Dropbox's real sign-in page", sc.get("sign_in_url", "").startswith("https://www.dropbox.com/"),
              r["result"]["content"][0]["text"][:300])
        from rewind.sources import connect
        connect.headless_cancel("dropbox")
    else:
        print("  SKIP rclone not installed")

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASSED" if not FAILS else "\nFAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


def make_jpeg():
    from PIL import Image
    img = Image.new("RGB", (320, 240))
    px = img.load()
    for x in range(320):
        for y in range(240):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


if __name__ == "__main__":
    sys.exit(main())
