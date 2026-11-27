"""One-click sign-in to cloud accounts, with no developer setup.

How it works: Rewind ships rclone (https://rclone.org, MIT licence), the
well-known open-source cloud tool. `rclone authorize <service>` opens the
normal Google / Microsoft sign-in page in your own browser, you say yes, and
rclone hands back a token for YOUR account. Rewind keeps that token in its
workspace on this computer and uses it only to read your trash/recycle bin,
download your files and put files back when you press Restore.

Nothing here bypasses a service's security: it is the same sign-in you would
do on the service's own website, and you can remove access at any time
(Google: myaccount.google.com/permissions, Microsoft: account.live.com/consent/Manage).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error

SERVICES = {
    # id: (rclone backend, title)
    "gdrive": ("drive", "Google Drive"),
    "onedrive": ("onedrive", "OneDrive"),
    "dropbox": ("dropbox", "Dropbox"),
}

STATE = {}  # service -> {"state": "waiting"|"connected"|"error", "url":..., "message":...}
_LOCK = threading.Lock()


def rclone_bin():
    cand = [os.environ.get("REWIND_RCLONE", "")]
    here = os.path.dirname(os.path.abspath(__file__))
    # inside Rewind.app: Contents/Resources/app/rewind/sources -> Contents/Resources/bin/rclone
    cand.append(os.path.normpath(os.path.join(here, "..", "..", "..", "bin", "rclone")))
    cand.append(shutil.which("rclone") or "")
    for c in cand:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def conf_path(cat):
    return os.path.join(cat.dir("cloud"), "rclone.conf")


def _write_remote(cat, name, fields):
    """Add or replace one [name] section in the rclone config."""
    path = conf_path(cat)
    sections, cur = {}, None
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.rstrip("\n")
            m = re.match(r"^\[(.+)\]$", line.strip())
            if m:
                cur = m.group(1)
                sections[cur] = []
            elif cur and line.strip():
                sections[cur].append(line)
    sections[name] = ["%s = %s" % (k, v) for k, v in fields.items()]
    with open(path, "w", encoding="utf-8") as f:
        for s, lines in sections.items():
            f.write("[%s]\n%s\n\n" % (s, "\n".join(lines)))
    os.chmod(path, 0o600)


def remove(cat, service):
    path = conf_path(cat)
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    text = re.sub(r"\[%s\]\n(?:(?!\[).*\n?)*" % re.escape(service), "", text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    STATE.pop(service, None)


def parse_authorize_output(text):
    """rclone prints the result between '--->' and '<---End paste'. Depending on
    the version it is the token JSON itself or base64 of {"token": "<json>"}."""
    m = re.search(r"--->\s*(.+?)\s*<---End paste", text, re.S)
    if not m:
        return None
    blob = m.group(1).strip()
    if not blob.startswith("{"):
        try:
            blob = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode()
        except Exception:
            try:
                blob = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)).decode()
            except Exception:
                return None
    try:
        obj = json.loads(blob)
    except ValueError:
        return None
    if isinstance(obj, dict) and "token" in obj and "access_token" not in obj:
        tok = obj["token"]
        obj = json.loads(tok) if isinstance(tok, str) else tok
    if not isinstance(obj, dict) or "access_token" not in obj:
        return None
    return obj


def connected(cat):
    out = {}
    path = conf_path(cat)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    for sid, (_, title) in SERVICES.items():
        st = dict(STATE.get(sid) or {})
        if re.search(r"^\[%s\]$" % sid, text, re.M) and st.get("state") != "waiting":
            st["state"] = "connected"
        st.setdefault("state", "not_connected")
        st["title"] = title
        out[sid] = st
    out["available"] = bool(rclone_bin())
    return out


def start_connect(cat, service, http_get=None):
    """Runs `rclone authorize` in the background. The browser opens by itself;
    the UI polls connected() to see progress."""
    rb = rclone_bin()
    if not rb:
        raise RuntimeError("The cloud helper (rclone) is missing from this copy of Rewind.")
    backend, title = SERVICES[service]
    with _LOCK:
        if (STATE.get(service) or {}).get("state") == "waiting":
            return
        STATE[service] = {"state": "waiting", "message": "Opening the %s sign-in page in your browser ..." % title}

    def work():
        try:
            p = subprocess.Popen([rb, "authorize", backend, "--config", conf_path(cat) + ".auth"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            buf = []
            deadline = time.time() + 900
            for line in p.stdout:
                buf.append(line)
                m = re.search(r"(http://127\.0\.0\.1:53682/auth\?state=\S+)", line)
                if m:
                    STATE[service]["url"] = m.group(1)
                    STATE[service]["message"] = ("Sign in to %s in the browser window that opened and press "
                                                 "Allow. If no window opened, use the button." % title)
                if "address already in use" in line.lower():
                    raise RuntimeError("Another sign-in is already open. Finish or close it, then try again.")
                if time.time() > deadline:
                    p.kill()
                    raise RuntimeError("Sign-in timed out")
            p.wait()
            token = parse_authorize_output("".join(buf))
            if not token:
                raise RuntimeError("Sign-in did not finish (%s)" % (buf[-1].strip() if buf else "no answer"))
            save_token(cat, service, token, http_get)
            STATE[service] = {"state": "connected", "message": "%s connected." % title}
        except Exception as e:
            STATE[service] = {"state": "error", "message": str(e)}

    threading.Thread(target=work, daemon=True).start()


def save_token(cat, service, token, http_get=None, extra=None):
    """Store a sign-in for rclone. extra: e.g. {"client_id":..., "client_secret":...} when the
    token came from Rewind's own app keys instead of rclone's shared app."""
    backend = SERVICES[service][0]
    fields = {"type": backend, "token": json.dumps(token)}
    fields.update(extra or {})
    if backend == "drive":
        fields["scope"] = "drive"
    elif backend == "onedrive":
        d = (http_get or _graph_get)("https://graph.microsoft.com/v1.0/me/drive", token["access_token"])
        fields["drive_id"] = d["id"]
        fields["drive_type"] = d.get("driveType", "personal")
    _write_remote(cat, service, fields)


# ---------------------------------------------------------------- sign-in from anywhere (Hugging Face)
#
# On a server there is no browser next to rclone. rclone still runs its sign-in
# helper on 127.0.0.1:53682 *inside the server*. We ask that helper for the real
# Google/Microsoft/Dropbox sign-in address and give it to the person. After they
# press Allow, their browser is sent to 127.0.0.1:53682 on *their* device, which
# shows an error page -- but its address holds the one-time code. They paste that
# address back, we hand it to rclone's helper inside the server, and rclone
# finishes the sign-in exactly as it would on a laptop.

HEADLESS = {}   # service -> {"proc", "buf", "url"}


def headless_start(cat, service, opener=None):
    import urllib.request
    rb = rclone_bin()
    if not rb:
        raise RuntimeError("The cloud helper (rclone) is missing.")
    if service not in SERVICES:
        raise ValueError("Unknown service")
    for other in list(HEADLESS):            # rclone's helper uses one fixed port: one sign-in at a time
        headless_cancel(other)
    backend, title = SERVICES[service]
    p = subprocess.Popen([rb, "authorize", backend, "--auth-no-open-browser", "--config", conf_path(cat) + ".auth"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    buf, local = [], None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        buf.append(line)
        m = re.search(r"(http://127\.0\.0\.1:53682/auth\?state=\S+)", line)
        if m:
            local = m.group(1)
            break
    if not local:
        p.kill()
        raise RuntimeError("rclone did not start its sign-in (%s)" % "".join(buf)[-200:])

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = opener or urllib.request.build_opener(NoRedirect)
    try:
        op.open(local, timeout=15)
        raise RuntimeError("rclone did not hand back a sign-in address")
    except urllib.error.HTTPError as e:
        url = e.headers.get("Location")
    if not url:
        p.kill()
        raise RuntimeError("rclone did not hand back a sign-in address")
    t = threading.Thread(target=lambda: buf.extend(p.stdout), daemon=True)
    t.start()

    def expire():                 # an abandoned sign-in must not hold rclone's port forever
        h = HEADLESS.get(service)
        if h and h["proc"] is p:
            headless_cancel(service)
            STATE[service] = {"state": "error", "message": "Sign-in timed out after 10 minutes. Press Connect again."}
    timer = threading.Timer(600, expire)
    timer.daemon = True
    timer.start()
    HEADLESS[service] = {"proc": p, "buf": buf, "url": url, "reader": t, "started": time.time()}
    STATE[service] = {"state": "waiting", "url": url,
                      "message": "Open the %s sign-in page, press Allow, then paste the address your browser "
                                 "ends up on (it starts with http://127.0.0.1:53682 or http://localhost:53682)." % title}
    return url


def headless_finish(cat, service, pasted, http_get=None):
    import urllib.parse
    import urllib.request
    h = HEADLESS.get(service)
    if not h:
        raise ValueError("Start the sign-in again: no %s sign-in is waiting." % SERVICES[service][1])
    q = urllib.parse.urlsplit(pasted.strip()).query
    args = dict(urllib.parse.parse_qsl(q))
    if "code" not in args:
        raise ValueError("That address has no sign-in code in it. Copy the whole address from the browser's "
                         "address bar after pressing Allow.")
    try:
        urllib.request.urlopen("http://127.0.0.1:53682/?" + q, timeout=30).read()
    except Exception:
        pass
    try:
        h["proc"].wait(timeout=60)
    except subprocess.TimeoutExpired:
        h["proc"].kill()
    h["reader"].join(5)
    HEADLESS.pop(service, None)
    token = parse_authorize_output("".join(h["buf"]))
    if not token:
        tail = [x.strip() for x in h["buf"] if x.strip()]
        STATE[service] = {"state": "error", "message": "Sign-in did not finish: %s" % (tail[-1] if tail else "no answer")}
        raise RuntimeError(STATE[service]["message"])
    save_token(cat, service, token, http_get)
    STATE[service] = {"state": "connected", "message": "%s connected." % SERVICES[service][1]}
    return STATE[service]


def headless_cancel(service):
    h = HEADLESS.pop(service, None)
    if h:
        try:
            h["proc"].kill()
        except OSError:
            pass
    if (STATE.get(service) or {}).get("state") == "waiting":
        STATE.pop(service, None)


def _graph_get(url, token):
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def access_token(cat, service):
    """A fresh access token. rclone refreshes an expired token itself when it
    talks to the service, and writes the new one back to the config."""
    rb = rclone_bin()
    if rb:
        subprocess.run([rb, "about", service + ":", "--config", conf_path(cat)],
                       capture_output=True, text=True, timeout=120)
        r = subprocess.run([rb, "config", "dump", "--config", conf_path(cat)],
                           capture_output=True, text=True, timeout=60)
        conf = json.loads(r.stdout or "{}")
    else:
        conf = {}
    sec = conf.get(service)
    if not sec:
        raise RuntimeError("%s is not connected." % SERVICES[service][1])
    tok = json.loads(sec["token"])
    return tok["access_token"]
