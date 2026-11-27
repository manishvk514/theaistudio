"""Account connection, the smooth way.

* Telegram: QR-code sign-in (no phone number, no code), two-step password, the
  one-time API ID from settings, plain-English errors, and Claude getting the QR image.
* One-click sign-in on Hugging Face with Rewind's own app keys: Connect sends the
  browser to the provider, the provider sends it straight back, the account is
  connected (no "can't connect" page, nothing to copy), and rclone gets the keys
  it needs to keep the sign-in fresh.
Run: python3 tests/test_connect.py
"""
import asyncio
import http.cookiejar
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["REWIND_CLOUD"] = "1"

from rewind import mcp, space  # noqa: E402
from rewind.server import App  # noqa: E402
from rewind.sources import connect, oauth_direct, telegram_cloud  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  -> %s" % (detail,)))
    if not ok:
        FAILS.append(name)


def until(fn, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------ fake Telegram (Telethon-shaped)

class FakeQR:
    def __init__(self, outcome):
        self.n = 0
        self.url = "tg://login?token=AAA0"
        self.outcome = outcome            # list of what each wait() does
        self.scanned = asyncio.Event()

    async def wait(self, timeout=None):
        step = self.outcome.pop(0) if self.outcome else "hang"
        if step == "expire":
            raise asyncio.TimeoutError()
        if step == "2fa":
            import telethon
            raise telethon.errors.SessionPasswordNeededError(request=None)
        if step == "scan":
            await asyncio.wait_for(self.scanned.wait(), 5)
            return object()
        await asyncio.sleep(3600)

    async def recreate(self):
        self.n += 1
        self.url = "tg://login?token=AAA%d" % self.n


class FakeClient:
    def __init__(self, outcome, authorized=False):
        self.authorized = authorized
        self.qr = FakeQR(outcome)
        self.passwords = []

    async def is_user_authorized(self):
        return self.authorized

    async def qr_login(self):
        return self.qr

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        self.passwords.append(password)
        if password != "right":
            import telethon
            raise telethon.errors.PasswordHashInvalidError(request=None)
        self.authorized = True


def telegram_tests(tmp):
    print("Telegram")
    app = App(os.path.join(tmp, "tg"), 0)
    tg = telegram_cloud.get(app.cat)
    st = tg.qr_start()
    check("without the one-time API ID it asks for just that (no phone number)", st["state"] == "need_app"
          and "my.telegram.org" in st["message"])
    try:
        tg.qr_start("abc", "short")
        check("a wrong-looking API ID is refused with a plain message", False)
    except ValueError as e:
        check("a wrong-looking API ID is refused with a plain message", "number" in str(e))

    fake = FakeClient(["expire", "scan"])

    async def client():
        return fake
    tg._client = client
    st = tg.qr_start("12345", "0123456789abcdef0123")
    check("then it shows a QR code to scan", st["state"] == "need_qr" and st.get("qr_svg", "").startswith("<?xml")
          and st["qr_url"].startswith("tg://login"), st)
    check("the QR code refreshes by itself when it expires", until(lambda: tg.qr_url == "tg://login?token=AAA1"),
          tg.qr_url)
    tg.loop.call_soon_threadsafe(fake.qr.scanned.set)
    check("scanning it in the Telegram app connects, with nothing typed", until(lambda: tg.state == "connected"),
          tg.state)
    check("the API ID is remembered", tg.app_creds()["api_id"] == "12345")

    fake2 = FakeClient(["2fa"])

    async def client2():
        return fake2
    tg._client = client2
    tg.state = "not_connected"
    tg.qr_start()
    check("an account with two-step verification asks only for that password",
          until(lambda: tg.state == "need_password") and "password" in tg.message.lower(), tg.state)
    try:
        tg.password("wrong")
        check("a wrong password gets a plain-English error", False)
    except ValueError as e:
        check("a wrong password gets a plain-English error", "password is wrong" in str(e), e)
    st = tg.password("right")
    check("the right password finishes the sign-in", st["state"] == "connected")

    os.environ["TELEGRAM_API_ID"], os.environ["TELEGRAM_API_HASH"] = "999", "fedcba9876543210fedc"
    app2 = App(os.path.join(tmp, "tg2"), 0)
    tg2 = telegram_cloud.get(app2.cat)
    check("an API ID in the Space settings is used without asking", tg2.app_creds()["api_id"] == "999")
    fake3 = FakeClient(["hang"])

    async def client3():
        return fake3
    tg2._client = client3
    out = mcp.Tools(app2, "https://example.hf.space").call("rewind_connect", {"service": "telegram"})
    kinds = [c["type"] for c in out]
    img = next((c for c in out if c["type"] == "image"), {})
    check("Claude gets the QR code as a picture to show you", "image" in kinds and img.get("mimeType") == "image/png"
          and len(img.get("data", "")) > 100, kinds)
    del os.environ["TELEGRAM_API_ID"], os.environ["TELEGRAM_API_HASH"]

    import telethon
    e = telethon.errors.FloodWaitError(request=None, capture=42)
    check("Telegram's 'wait' error is explained in words", "wait 42 seconds" in telegram_cloud._friendly(e),
          telegram_cloud._friendly(e))
    snap = mcp.Tools(app2, "https://example.hf.space").call("rewind_connect", {"service": "snapchat"})
    check("Snapchat gives the exact data-download steps and the upload link",
          any("JSON" in x for x in snap["steps"]) and "example.hf.space/#upload" in snap["steps"][-1])


# ------------------------------------------------------------ one-click sign-in

TOKEN_CALLS = []


class FakeProvider(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        TOKEN_CALLS.append(form)
        if form.get("code") == "good-code" and form.get("client_secret") == "dbx-secret":
            body, code = {"access_token": "dbx-at", "refresh_token": "dbx-rt", "expires_in": 14400,
                          "token_type": "bearer"}, 200
        else:
            body, code = {"error": "invalid_grant", "error_description": "code doesn't exist or has expired"}, 400
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def req(opener, url, data=None, headers=None):
    r = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        resp = opener.open(r, timeout=30)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def one_click_tests(tmp):
    print("One-click sign-in (Hugging Face)")
    prov = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=prov.serve_forever, daemon=True).start()
    oauth_direct.PROVIDERS["dropbox"]["token"] = "http://127.0.0.1:%d/token" % prov.server_address[1]
    os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"] = "dbx-key", "dbx-secret"
    probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()
    base = "http://127.0.0.1:%d" % port
    cfg = space.Config({"REWIND_BASE_URL": base, "SPACE_AUTHOR_NAME": "manishvk18", "SPACE_SIGNING_SECRET": "t",
                        "OAUTH_CLIENT_ID": "x"})
    app = App(os.path.join(tmp, "oc"), port)
    app.start_scan = lambda source, params: 0          # don't run a real scan against Dropbox here
    sp = space.SpaceApp(app, cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", port), space.make_space_handler(app, sp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    jar = http.cookiejar.CookieJar()
    b = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    sess = sp.signer.sign("s", {"u": "manishvk18"}, 3600)
    ck = {"Cookie": "rw_s=" + sess, "Content-Type": "application/json"}

    st, h, body = req(b, base + "/api/connect", json.dumps({"service": "dropbox"}).encode(), ck)
    j = json.loads(body)
    url = j.get("sign_in_url", "")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    check("Connect goes to Dropbox's own page with Rewind's key", url.startswith("https://www.dropbox.com/oauth2/authorize")
          and q.get("client_id") == "dbx-key", url)
    check("...asking Dropbox to come straight back to Rewind (no 127.0.0.1 page)",
          q.get("redirect_uri") == base + "/oauth/dropbox/callback" and q.get("token_access_type") == "offline", q)
    check("...and the card says it will connect by itself", j["dropbox"].get("direct") is True)
    st, h, body = req(b, base + "/oauth/dropbox/callback?code=good-code&state=forged", None, ck)
    check("a forged return is refused", st == 400 and b"expired" in body)
    st, h, body = req(b, base + "/oauth/dropbox/callback?" + urllib.parse.urlencode(
        {"code": "good-code", "state": q["state"]}))
    check("a return without being signed in to Rewind is refused", st == 400 and b"Sign in to Rewind" in body)
    st, h, body = req(b, base + "/oauth/dropbox/callback?" + urllib.parse.urlencode(
        {"code": "good-code", "state": q["state"]}), None, ck)
    check("coming back from Dropbox connects the account", st == 200 and b"Connected" in body, body[:300])
    check("...and the sign-in tab tells Rewind and closes itself", b"rewind-connected" in body and b"window.close" in body)
    conf = open(connect.conf_path(app.cat)).read()
    check("rclone gets Rewind's keys so it can keep the sign-in fresh",
          "client_id = dbx-key" in conf and "client_secret = dbx-secret" in conf and "dbx-rt" in conf, conf)
    check("Dropbox shows as connected", connect.connected(app.cat)["dropbox"]["state"] == "connected")
    sent = TOKEN_CALLS[-1]
    check("the code was exchanged with the same return address", sent.get("redirect_uri") ==
          base + "/oauth/dropbox/callback" and sent.get("grant_type") == "authorization_code", sent)

    st, h, body = req(b, base + "/api/connect", json.dumps({"service": "dropbox"}).encode(), ck)
    q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(json.loads(body)["sign_in_url"]).query))
    st, h, body = req(b, base + "/oauth/dropbox/callback?" + urllib.parse.urlencode(
        {"code": "stale", "state": q2["state"]}), None, ck)
    check("if Dropbox refuses, Rewind says why in words", st == 400 and b"expired" in body, body[:300])
    st, h, body = req(b, base + "/oauth/dropbox/callback?" + urllib.parse.urlencode(
        {"error": "access_denied", "state": q2["state"]}), None, ck)
    check("pressing Cancel on Dropbox's page is explained", st == 400 and b"cancelled" in body)

    st, h, body = req(b, base + "/setup", None, ck)
    check("the setup page shows the exact return address for each service", st == 200 and
          (base + "/oauth/onedrive/callback").encode() in body and b"one-click is on" in body
          and b"MS_CLIENT_ID" in body, body[:200])
    st, h, body = req(b, base + "/api/space", None, ck)
    check("Rewind knows which services are one-click", json.loads(body)["one_click"] ==
          {"onedrive": False, "gdrive": False, "dropbox": True}, body)
    srv.shutdown()
    prov.shutdown()
    del os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"]


def main():
    tmp = tempfile.mkdtemp(prefix="rewind-connect-")
    try:
        telegram_tests(tmp)
        one_click_tests(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASSED" if not FAILS else "\nFAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
