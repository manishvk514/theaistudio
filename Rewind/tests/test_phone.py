"""Phone/laptop features: health, pairing, security, uploads, zip download, quit.

Run:  python3 tests/test_phone.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
from rewind import server  # noqa: E402

PORT = 18765
ok = True


def check(label, cond):
    global ok
    ok &= bool(cond)
    print("  %s %s" % ("PASS" if cond else "FAIL", label))


def req(method, url, body=None, headers=None, raw=False):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            content = resp.read()
            return resp.status, (content if raw else json.loads(content or b"{}") if content[:1] in b"{[" else content)
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    ws = tempfile.mkdtemp()
    app = server.App(ws, PORT)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), server.make_handler(app))
    app.servers.append(httpd)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    L = "http://127.0.0.1:%d" % PORT
    T = {"X-Rewind-Token": app.token}

    code, body = req("GET", L + "/health")
    check("health check answers without a token", code == 200 and body.get("app") == "rewind")
    code, html = req("GET", L + "/", raw=True)
    check("laptop page carries the laptop token", app.token.encode() in html)
    code, _ = req("GET", L + "/manifest.json", raw=True)
    code2, _ = req("GET", L + "/sw.js", raw=True)
    check("installable-app files are served", code == 200 and code2 == 200)

    # phone access off: LAN port closed
    code, st = req("GET", L + "/api/phone", headers=T)
    check("phone access starts off", code == 200 and st["on"] is False)
    code, st = req("POST", L + "/api/phone", {"on": True}, headers=T)
    check("switching phone access on gives a 6-digit code", st["on"] and len(st["code"]) == 6)
    R = "http://127.0.0.1:%d" % app.lan_port  # loopback stands in for the phone's network address
    code, html = req("GET", R + "/", raw=True)
    check("phone page never contains the laptop token", code == 200 and app.token.encode() not in html
          and b'REWIND_TOKEN = ""' in html)
    code, _ = req("GET", R + "/api/summary")
    check("phone without pairing is refused", code == 403)
    code, _ = req("POST", R + "/api/pair", {"code": "000000" if st["code"] != "000000" else "111111"})
    check("wrong code is refused", code == 403)
    code, pr = req("POST", R + "/api/pair", {"code": st["code"]})
    check("right code pairs the phone", code == 200 and pr.get("token"))
    P = {"X-Rewind-Token": pr["token"]}
    code, _ = req("GET", R + "/api/summary", headers=P)
    check("paired phone can read results", code == 200)
    code, _ = req("GET", R + "/api/summary", headers=T)
    check("laptop token does not work from the network", code == 403)
    code, _ = req("POST", R + "/api/reset", {}, headers=P)
    check("phone cannot reset or quit the laptop", code == 403)
    code, _ = req("GET", R + "/", headers={"Host": "evil.example.com"}, raw=True)
    check("requests naming a public host are refused (DNS rebinding)", code == 403)

    # phone uploads a WhatsApp chat export and a photo
    b = io.BytesIO()
    Image.effect_noise((800, 600), 50).convert("RGB").save(b, "JPEG", quality=90)
    photo = b.getvalue()
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("WhatsApp Chat with Didi.txt", "hi")
        z.writestr("IMG-20200101-WA0001.jpg", photo)
    code, up = req("POST", R + "/api/upload?scan=1&name=" + urllib.request.quote("WhatsApp Chat with Didi.zip"),
                   zb.getvalue(), headers=P)
    check("phone upload of a chat export is routed to WhatsApp", code == 200 and up.get("source") == "whatsapp")
    b2 = io.BytesIO()
    Image.effect_noise((640, 480), 80).convert("RGB").save(b2, "JPEG", quality=90)
    code, up2 = req("POST", R + "/api/upload?scan=1&name=lost.jpg", b2.getvalue(), headers=P)
    check("phone upload of a loose photo is scanned", code == 200 and up2.get("source") == "folder")
    for jid in (up["job"], up2["job"]):
        while app.jobs[jid].state == "running":
            time.sleep(0.1)
    code, items = req("GET", R + "/api/items?view=deleted", headers=P)
    check("uploaded pictures show up as found (%d)" % items.get("total", 0), items.get("total", 0) >= 2)
    iid = items["items"][0]["id"]
    code, data = req("GET", R + "/api/file/%d?t=%s&dl=1" % (iid, pr["token"]), raw=True)
    check("phone can download a single restored file", code == 200 and data[:3] == b"\xff\xd8\xff")
    code, ex = req("POST", R + "/api/export", {"zip": True}, headers=P)
    code2, zdata = req("GET", R + ex["url"] + "?t=" + pr["token"], raw=True)
    names = zipfile.ZipFile(io.BytesIO(zdata)).namelist() if code2 == 200 else []
    check("phone can download everything as one zip (%d files)" % len(names),
          code == 200 and any(n.endswith(".jpg") for n in names) and "report.csv" in names)
    code, zdata = req("GET", R + "/api/get-rewind.zip?t=" + pr["token"], raw=True)
    check("paired Android phone can download Rewind itself",
          code == 200 and any(n.endswith("rewind/server.py") for n in zipfile.ZipFile(io.BytesIO(zdata)).namelist()))

    # switching off signs the phone out
    req("POST", L + "/api/phone", {"on": False}, headers=T)
    time.sleep(0.5)
    try:
        code, _ = req("GET", R + "/api/summary", headers=P)
    except Exception:
        code = "closed"
    check("switching phone access off disconnects the phone", code in (403, "closed"))

    code, _ = req("POST", L + "/api/quit", {}, headers=T)
    time.sleep(1.0)
    try:
        req("GET", L + "/health")
        alive = True
    except Exception:
        alive = False
    check("Quit button stops Rewind", code == 200 and not alive)
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
