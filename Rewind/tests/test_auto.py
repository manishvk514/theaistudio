"""Tests for the automatic "scan everything" mode, one-click cloud sign-in and
the new screens' API. Runs against a fake home folder; cloud services are
simulated, so no network or real account is used.

Run:  python3 tests/test_auto.py
"""
import base64
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile

TMP = tempfile.mkdtemp(prefix="rewind_auto_")
HOME = os.path.join(TMP, "home")
os.makedirs(HOME)
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402

from rewind import server  # noqa: E402
from rewind.sources import auto, connect, cloud, telegram_cloud  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


def photo(i, size=(900, 700)):
    im = Image.new("RGB", size, ((i * 53) % 255, (i * 97) % 255, (i * 31) % 255))
    d = ImageDraw.Draw(im)
    for k in range(12):
        d.rectangle([k * 37 % 800, (k * i * 13) % 600, k * 37 % 800 + 90, (k * i * 13) % 600 + 70],
                    fill=((k * 40 + i) % 255, (k * 70) % 255, (i * 90) % 255))
    d.text((30, 30), "AUTO %d" % i, fill=(255, 255, 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ------------------------------------------------------------------ fixtures
P = {i: photo(i) for i in range(1, 12)}
put(os.path.join(HOME, "Pictures", "keep1.jpg"), P[1])
put(os.path.join(HOME, "Pictures", "keep2.jpg"), P[2])
put(os.path.join(HOME, ".local/share/Trash/files", "binned.jpg"), P[3])           # in the Trash
put(os.path.join(HOME, ".cache/thumbnails/large", "abc.png"), photo(4, (256, 200)))  # cache only
# Google Takeout in Downloads: one photo still in library (dup), one only in Takeout
tk = io.BytesIO()
with zipfile.ZipFile(tk, "w") as z:
    z.writestr("Takeout/Google Photos/Photos from 2020/IMG_1.jpg", P[1])
    z.writestr("Takeout/Google Photos/Photos from 2020/IMG_5.jpg", P[5])
put(os.path.join(HOME, "Downloads", "takeout-20250101T000000Z-001.zip"), tk.getvalue())
# Snapchat My Data with media inside and an empty memories list (no network)
sn = io.BytesIO()
with zipfile.ZipFile(sn, "w") as z:
    z.writestr("memories/2021-01-01_memory.jpg", P[6])
    z.writestr("json/memories_history.json", json.dumps({"Saved Media": []}))
put(os.path.join(HOME, "Downloads", "mydata~1700000000000.zip"), sn.getvalue())
# Telegram Desktop export
put(os.path.join(HOME, "Downloads", "Telegram Desktop", "DataExport_2025-01-01", "chats", "chat_001",
                 "photos", "photo_1@01-01-2025_10-00-00.jpg"), P[7])
# a file that only looks like an export must not be picked up
put(os.path.join(HOME, "Downloads", "notes.zip"), b"PK\x05\x06" + b"\0" * 18)

print("== export discovery")
found = auto.find_exports()
kinds = sorted(k for k, _ in found)
check("finds Takeout, Snapchat and Telegram export in Downloads", kinds == ["snapchat", "takeout", "telegram"],
      str(kinds))

print("== fake cloud helper (stands in for rclone)")
FAKE = os.path.join(TMP, "fake-rclone")
TOKEN = {"access_token": "AT-1", "token_type": "Bearer", "refresh_token": "RT", "expiry": "2030-01-01T00:00:00Z"}
blob = base64.b64encode(json.dumps({"token": json.dumps(TOKEN)}).encode()).decode()
with open(FAKE, "w") as f:
    f.write("""#!/usr/bin/env python3
import sys, json, re, os
a = sys.argv[1:]
if a[0] == "authorize":
    print("NOTICE: Please go to the following link: http://127.0.0.1:53682/auth?state=XYZ", flush=True)
    print("NOTICE: Got code")
    print("Paste the following into your remote machine --->")
    print(%r)
    print("<---End paste")
elif a[0] == "about":
    pass
elif a[0] == "config" and a[1] == "dump":
    conf = a[a.index("--config") + 1]
    out, cur = {}, None
    for line in open(conf):
        m = re.match(r"^\\[(.+)\\]$", line.strip())
        if m:
            cur = m.group(1); out[cur] = {}
        elif cur and " = " in line:
            k, v = line.rstrip("\\n").split(" = ", 1); out[cur][k] = v
    print(json.dumps(out))
""" % blob)
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IEXEC)
os.environ["REWIND_RCLONE"] = FAKE

check("parses base64 token output", connect.parse_authorize_output(
    "x\nPaste the following into your remote machine --->\n%s\n<---End paste\n" % blob)["access_token"] == "AT-1")
check("parses plain JSON token output", connect.parse_authorize_output(
    "Paste the following into your remote machine --->\n%s\n<---End paste" % json.dumps(TOKEN))["refresh_token"] == "RT")
check("rejects garbage", connect.parse_authorize_output("no token here") is None)

WS = os.path.join(TMP, "ws")
app = server.App(WS, 18891)
connect.start_connect(app.cat, "gdrive")
connect.start_connect(app.cat, "onedrive", http_get=lambda url, tok: {"id": "DRIVE1", "driveType": "personal"})
for _ in range(50):
    st = connect.connected(app.cat)
    if st["gdrive"]["state"] == "connected" and st["onedrive"]["state"] == "connected":
        break
    time.sleep(0.1)
st = connect.connected(app.cat)
check("Google Drive connects in one click", st["gdrive"]["state"] == "connected", str(st["gdrive"]))
check("OneDrive connects in one click", st["onedrive"]["state"] == "connected", str(st["onedrive"]))
conf = open(connect.conf_path(app.cat)).read()
check("token kept only in the workspace config", "drive_id = DRIVE1" in conf and "AT-1" in conf)
check("config is private (600)", oct(os.stat(connect.conf_path(app.cat)).st_mode & 0o777) == "0o600")
check("access token read back through the helper", connect.access_token(app.cat, "gdrive") == "AT-1")

# simulated Google Drive and Microsoft Graph
CLOUD_CALLS = []


def fake_http(method, url, token=None, data=None, headers=None, raw=False, timeout=120):
    CLOUD_CALLS.append((method, url.split("?")[0], token))
    if "googleapis.com/drive/v3/files" in url and "alt=media" in url:
        return P[8]
    if "googleapis.com/drive/v3/files" in url and method == "GET":
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
        if "trashed = true" in q:
            return {"files": [{"id": "G1", "name": "beach.jpg", "mimeType": "image/jpeg", "size": len(P[8]),
                               "trashedTime": "2025-11-01T10:00:00Z"}]}
        return {"files": [{"id": "G2", "name": "keep1.jpg", "mimeType": "image/jpeg", "size": 1, "md5Checksum": "x"}]}
    if "googleapis.com/drive/v3/files" in url and method == "PATCH":
        return {}
    if "graph.microsoft.com" in url and "/delta" in url:
        return {"value": [
            {"id": "O1", "name": "party.jpg", "file": {"mimeType": "image/jpeg"}, "size": 10},
            {"id": "O2", "name": "gone.jpg", "deleted": {"state": "deleted"}},
        ], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=NEXT"}
    if "graph.microsoft.com" in url and url.endswith("/restore"):
        return {}
    if "graph.microsoft.com" in url and url.endswith("/content"):
        return P[9]
    raise AssertionError("unexpected call " + url)


import urllib.parse  # noqa: E402
cloud._http = fake_http

print("== automatic scan of everything")
places = auto.plan(app.cat)
ids = [p["id"] for p in places]
check("plan reads current library before bins and caches",
      ids.index("linux-pictures") < ids.index("linux-trash"), str(ids))
check("plan includes the exports and both connected clouds",
      sum(i.startswith("export:") for i in ids) == 3 and "cloud:gdrive" in ids and "cloud:onedrive" in ids)

jid = app.start_scan("auto", {})
for _ in range(600):
    j = app.jobs[jid]
    if j.state != "running":
        break
    time.sleep(0.1)
j = app.jobs[jid].as_dict()
check("automatic scan finishes", j["state"] == "done", j["state"] + " " + "\n".join(j["logs"][-8:]))
bad = [p for p in j["places"] if p["status"] not in ("done",)]
check("every place scanned without errors", not bad, json.dumps(bad)[:400])
check("live per-place progress is reported", all("found" in p for p in j["places"]))

con = app.cat.con()


def verdict_of(name):
    r = con.execute("SELECT verdict, category FROM items WHERE (name=? OR location LIKE ?) AND is_primary=1 "
                    "ORDER BY id LIMIT 1", (name, "%" + name)).fetchone()
    return tuple(r) if r else None


check("library photo is 'still here'", verdict_of("keep1.jpg")[0] == "present", str(verdict_of("keep1.jpg")))
check("Trash photo is 'deleted' (in a bin)", verdict_of("binned.jpg") == ("deleted", "trash"), str(verdict_of("binned.jpg")))
check("Takeout-only photo is 'deleted'", verdict_of("IMG_5.jpg")[0] == "deleted", str(verdict_of("IMG_5.jpg")))
dup = con.execute("SELECT verdict FROM items WHERE name='IMG_1.jpg'").fetchone()
check("Takeout copy of a library photo is not counted as deleted", dup and dup[0] != "deleted", str(dup and dup[0]))
check("Snapchat export media found", verdict_of("2021-01-01_memory.jpg") is not None)
check("Telegram export photo found", con.execute("SELECT COUNT(*) FROM items WHERE source='telegram'").fetchone()[0] == 1)
check("Google Drive trash downloaded and marked deleted",
      con.execute("SELECT category FROM items WHERE source='google-drive' AND path IS NOT NULL").fetchone()[0] == "trash")
check("OneDrive deleted item listed", con.execute(
    "SELECT COUNT(*) FROM items WHERE source='onedrive' AND hint='missing'").fetchone()[0] == 1)
check("OneDrive change marker saved for next time", (app.cat.kv_get("onedrive_delta") or {}).get("link", "").endswith("NEXT"))

print("== second automatic scan (as on every app open)")
n_items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
jid2 = app.start_scan("auto", {})
while app.jobs[jid2].state == "running":
    time.sleep(0.1)
j2 = app.jobs[jid2].as_dict()
check("second scan finishes", j2["state"] == "done")
n_items2 = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
check("no duplicate rows after rescanning", n_items2 == n_items, "%d -> %d" % (n_items, n_items2))
skipped = [p for p in j2["places"] if p["kind"].startswith("export:") and "earlier scan" in (p.get("note") or "")]
check("unchanged exports are not reopened", len(skipped) == 3, str([p.get("note") for p in j2["places"]]))

print("== one-tap restore")
summ = app.status()["summary"]
check("summary counts deleted and still here", summ["n_deleted"] >= 4 and summ["n_present"] >= 2, str(summ))
res = app.restore_all()
check("restore all writes to Pictures/Rewind Restored",
      res["folder"].startswith(os.path.join(HOME, "Pictures", "Rewind Restored")) and res["copied"] >= 4, str(res))
check("restored items are remembered", app.status()["summary"]["n_restored"] >= 4)

print("== restore inside the cloud")
gid = con.execute("SELECT id FROM items WHERE source='google-drive' AND path IS NOT NULL").fetchone()[0]
check("Drive file put back in Drive", "Put back" in app.remote_restore(gid))
check("Drive restore used the one-click token",
      any(c[0] == "PATCH" and c[2] == "AT-1" for c in CLOUD_CALLS))
oid = con.execute("SELECT id FROM items WHERE source='onedrive' AND hint='missing'").fetchone()[0]
check("OneDrive item restored and downloaded", "Restored" in app.remote_restore(oid))
check("restored OneDrive file is on disk", os.path.exists(app.cat.get(oid)["path"]))

print("== HTTP API used by the new screens")
srv = server.ThreadingHTTPServer(("127.0.0.1", 18891), server.make_handler(app))
import threading  # noqa: E402
threading.Thread(target=srv.serve_forever, daemon=True).start()


def api(path, body=None, token=app.token):
    req = urllib.request.Request("http://127.0.0.1:18891" + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Rewind-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, st = api("/api/status")
check("status endpoint", code == 200 and st["accounts"]["gdrive"]["state"] == "connected" and "auto_job" in st)
check("status shows the last automatic scan's places", len(st["auto_job"]["places"]) == len(j2["places"]))
code, _ = api("/api/status", token="wrong")
check("status refuses a wrong token", code == 403)
code, plan = api("/api/plan")
check("plan endpoint", code == 200 and isinstance(plan, list) and plan)
code, r = api("/api/disconnect", {"service": "gdrive"})
check("disconnect Google Drive", code == 200 and r["gdrive"]["state"] == "not_connected")
code, r = api("/api/telegram", {"action": "start", "api_id": "abc", "api_hash": "x", "phone": ""})
check("Telegram sign-in checks its fields", code == 400 and "API ID" in r.get("error", ""))
check("Telegram library is bundled", telegram_cloud.available())
code, r = api("/api/deep_scan", {"volume": "/"})
check("deep scan refuses the computer's own disk", code == 400 and "TRIM" in r.get("error", ""))
code, r = api("/api/scan", {"source": "card-deep", "params": {"path": "/"}})
srcs = api("/api/sources")[1]
check("hidden deep-scan source not listed in Advanced as a normal option",
      any(s["id"] == "card-deep" and s.get("hidden") for s in srcs))
srv.shutdown()

print()
if FAILS:
    print("FAILED: %s" % ", ".join(FAILS))
    sys.exit(1)
print("ALL PASSED")
shutil.rmtree(TMP, ignore_errors=True)
