"""Cloud accounts through their official APIs: Google Drive and OneDrive.

First principles:
* Cloud "delete" is almost always a soft delete first: the file moves to a
  Trash / Recycle bin and keeps its bytes on the server for a fixed window
  (Google Drive 30 days; OneDrive personal 30 days; Google Photos 60 days).
  Inside that window the service itself can undelete it perfectly.
* After the window the provider erases it; no client can bring it back.
  Only copies elsewhere (other devices, email, exports, backups) remain.
* Google Photos is a special case: since 31 March 2025 its Library API only
  sees photos an app uploaded itself, and no API reaches Photos' Trash. So
  Google Photos goes through Takeout, and its Trash must be emptied/restored
  at photos.google.com/trash by you.

You sign in with your own account in your own browser; the app only receives
a token for your account and stores it on this computer.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

PENDING = {}  # state -> {"code": ...}  filled by the web server's /oauth/google route

GDRIVE_INFO = {
    "id": "google-drive",
    "title": "Google Drive (live account)",
    "group": "Accounts",
    "blurb": "Signs in to your Google Drive, downloads every photo/video in Drive's Trash, and can put "
             "them back in Drive. Needs a free 'OAuth client' file from Google Cloud (5-minute setup "
             "in the guide).",
    "link": "https://console.cloud.google.com/apis/credentials",
    "fields": [
        {"name": "client_file", "label": "OAuth client file (client_secret_....json)", "type": "file"},
        {"name": "list_live", "label": "Also read the list of photos still in Drive (to compare)",
         "type": "bool", "default": True},
    ],
}

ONEDRIVE_INFO = {
    "id": "onedrive",
    "title": "OneDrive (live account)",
    "group": "Accounts",
    "blurb": "Signs in to your Microsoft account, lists photos and videos deleted from OneDrive and can "
             "restore them from the recycle bin. Needs a free app ID from Microsoft (setup in the guide).",
    "link": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    "fields": [
        {"name": "client_id", "label": "Application (client) ID", "type": "text"},
    ],
}


def _http(method, url, token=None, data=None, headers=None, raw=False, timeout=120):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        if isinstance(data, dict) and h.get("Content-Type") == "application/x-www-form-urlencoded":
            body = urllib.parse.urlencode(data).encode()
        elif isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        else:
            body = data
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = r.read()
    if raw:
        return content
    return json.loads(content) if content else {}


# ================================================================ Google Drive

G_SCOPE = "https://www.googleapis.com/auth/drive"
G_FIELDS = ("nextPageToken,files(id,name,mimeType,size,md5Checksum,createdTime,modifiedTime,"
            "trashedTime,imageMediaMetadata(time,width,height),videoMediaMetadata(width,height))")


def _google_client(path):
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        j = json.load(f)
    c = j.get("installed") or j.get("web") or j
    return c["client_id"], c.get("client_secret", "")


def google_token(ctx, client_file, port):
    cid, csec = _google_client(client_file)
    saved = ctx.cat.kv_get("google_token")
    if saved and saved.get("refresh_token") and saved.get("client_id") == cid:
        try:
            t = _http("POST", "https://oauth2.googleapis.com/token", data={
                "client_id": cid, "client_secret": csec, "refresh_token": saved["refresh_token"],
                "grant_type": "refresh_token"}, headers={"Content-Type": "application/x-www-form-urlencoded"})
            return t["access_token"]
        except Exception:
            pass
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect = "http://127.0.0.1:%d/oauth/google" % port
    PENDING[state] = {}
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": G_SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    ctx.job.ask_user("Sign in to Google in the window that opens, then come back here.", url)
    for _ in range(600):
        if ctx.stopped():
            raise RuntimeError("stopped")
        if PENDING[state].get("code"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Google sign-in timed out")
    ctx.job.ask_user(None)
    t = _http("POST", "https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": csec, "code": PENDING.pop(state)["code"],
        "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    ctx.cat.kv_set("google_token", {"refresh_token": t.get("refresh_token"), "client_id": cid,
                                    "client_file": os.path.expanduser(client_file)})
    return t["access_token"]


def _drive_list(token, q):
    page = None
    while True:
        params = {"q": q, "fields": G_FIELDS, "pageSize": 1000, "spaces": "drive"}
        if page:
            params["pageToken"] = page
        r = _http("GET", "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params), token)
        for f in r.get("files", []):
            yield f
        page = r.get("nextPageToken")
        if not page:
            return


MEDIA_Q = "(mimeType contains 'image/' or mimeType contains 'video/')"


def run_gdrive(ctx, params):
    token = google_token(ctx, params["client_file"], ctx.job.port)
    scan_gdrive(ctx, token, params.get("list_live", True))


def scan_gdrive(ctx, token, list_live=True, via=None):
    out = ctx.out_dir("trash")
    n = 0
    for f in _drive_list(token, "trashed = true and " + MEDIA_Q):
        if ctx.stopped():
            return
        dest = os.path.join(out, "%s_%s" % (f["id"][:8], f["name"].replace("/", "_")))
        if not os.path.exists(dest):
            try:
                data = _http("GET", "https://www.googleapis.com/drive/v3/files/%s?alt=media" % f["id"], token,
                             raw=True, timeout=600)
            except Exception as e:
                ctx.log("Could not download %s: %s" % (f["name"], e))
                continue
            with open(dest, "wb") as fh:
                fh.write(data)
        taken = (f.get("imageMediaMetadata") or {}).get("time")
        if taken:
            taken = taken.replace(":", "-", 2).replace(" ", "T")
        ctx.ingest_file(dest, "trash", location="Google Drive › Trash › " + f["name"], name=f["name"],
                        taken=taken, deleted_at=f.get("trashedTime"),
                        evidence="In Google Drive's Trash (erased 30 days after deletion)",
                        remote={"service": "gdrive", "id": f["id"], "via": via})
        n += 1
    ctx.log("Google Drive Trash: %d photo/video file(s)." % n)
    if list_live:
        con = ctx.cat.con()
        con.execute("DELETE FROM items WHERE source=? AND path IS NULL AND hint='present'", (ctx.source,))
        con.commit()
        live = 0
        for f in _drive_list(token, "trashed = false and " + MEDIA_Q):
            ctx.cat.add(dict(scan_id=ctx.scan_id, source=ctx.source, location="Google Drive › " + f["name"],
                             path=None, name=f["name"], hint="present",
                             kind="video" if f["mimeType"].startswith("video") else "image",
                             ext=os.path.splitext(f["name"])[1].lower(), size=int(f.get("size") or 0),
                             md5=f.get("md5Checksum"), evidence="Still in your Google Drive"))
            live += 1
        ctx.log("Google Drive: %d photo/video file(s) still in Drive." % live)


def gdrive_untrash(cat, remote):
    if remote.get("via") == "connect":
        from . import connect
        _http("PATCH", "https://www.googleapis.com/drive/v3/files/%s" % remote["id"],
              connect.access_token(cat, "gdrive"), data={"trashed": False})
        return "Put back in Google Drive"
    saved = cat.kv_get("google_token") or {}
    cid, csec = _google_client(saved["client_file"])
    t = _http("POST", "https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": csec, "refresh_token": saved["refresh_token"],
        "grant_type": "refresh_token"}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    _http("PATCH", "https://www.googleapis.com/drive/v3/files/%s" % remote["id"], t["access_token"],
          data={"trashed": False})
    return "Put back in Google Drive"


# ================================================================ OneDrive

MS_AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
MS_SCOPE = "Files.ReadWrite offline_access"
GRAPH = "https://graph.microsoft.com/v1.0"


def ms_token(ctx, client_id, scope=MS_SCOPE, key="ms_token"):
    saved = ctx.cat.kv_get(key) if ctx else None
    if saved and saved.get("client_id") == client_id and saved.get("refresh_token"):
        try:
            t = _http("POST", MS_AUTH + "/token", data={
                "client_id": client_id, "grant_type": "refresh_token", "scope": scope,
                "refresh_token": saved["refresh_token"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            _save_ms(ctx.cat, client_id, t, key)
            return t["access_token"]
        except Exception:
            pass
    dc = _http("POST", MS_AUTH + "/devicecode", data={"client_id": client_id, "scope": scope},
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    ctx.job.ask_user("Open the Microsoft page and type this code: %s" % dc["user_code"],
                     dc["verification_uri"], code=dc["user_code"])
    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 900))
    while time.time() < deadline:
        if ctx.stopped():
            raise RuntimeError("stopped")
        time.sleep(interval)
        try:
            t = _http("POST", MS_AUTH + "/token", data={
                "client_id": client_id, "device_code": dc["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        except urllib.error.HTTPError as e:
            err = json.loads(e.read() or b"{}").get("error")
            if err in ("authorization_pending", "slow_down"):
                continue
            raise RuntimeError("Microsoft sign-in failed: %s" % err)
        ctx.job.ask_user(None)
        _save_ms(ctx.cat, client_id, t, key)
        return t["access_token"]
    raise RuntimeError("Microsoft sign-in timed out")


def _save_ms(cat, client_id, t, key="ms_token"):
    cat.kv_set(key, {"client_id": client_id, "refresh_token": t.get("refresh_token")})


def run_onedrive(ctx, params):
    token = ms_token(ctx, params["client_id"].strip())
    scan_onedrive(ctx, token, {"service": "onedrive", "client_id": params["client_id"]})


MEDIA_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".mp4", ".mov", ".gif", ".webp", ".m4v", ".3gp",
             ".avi", ".mkv", ".dng", ".raw", ".cr2", ".nef", ".arw")


def _od_pages(url, token):
    """Yields drive items from a delta query; returns the deltaLink for next time."""
    while url:
        r = _http("GET", url, token)
        for it in r.get("value", []):
            yield it
        if r.get("@odata.deltaLink"):
            return r["@odata.deltaLink"]
        url = r.get("@odata.nextLink")
    return None


def scan_onedrive(ctx, token, remote_base):
    """Lists every photo/video in OneDrive (so the app knows what is still there)
    and every item OneDrive reports as deleted since Rewind last looked.
    OneDrive's API has no 'list recycle bin' call for personal accounts, so
    deletions are caught from the change feed: the first scan sets a marker,
    later scans see everything deleted after it."""
    sel = "?$select=id,name,file,deleted,size,photo,video,createdDateTime,parentReference"
    live = deleted = 0
    con = ctx.cat.con()
    con.execute("DELETE FROM items WHERE source=? AND path IS NULL AND hint='present'", (ctx.source,))
    con.commit()
    seen_deleted = {json.loads(r[0] or "{}").get("id") for r in
                    con.execute("SELECT remote FROM items WHERE source=? AND remote IS NOT NULL", (ctx.source,))}

    def handle(it):
        nonlocal live, deleted
        name = it.get("name") or "(name not kept)"
        f = it.get("file") or {}
        mime = f.get("mimeType", "")
        is_media = mime.startswith(("image/", "video/")) or os.path.splitext(name)[1].lower() in MEDIA_EXT
        if it.get("deleted"):
            if (is_media or not f) and it["id"] not in seen_deleted:
                seen_deleted.add(it["id"])
                ctx.add_missing(name, "OneDrive (deleted)",
                                "OneDrive reports this item as deleted. If it is still in the recycle bin "
                                "(30 days), press Restore to put it back in OneDrive and download it.",
                                kind="video" if mime.startswith("video") else "image",
                                remote=dict(remote_base, id=it["id"]))
                deleted += 1
        elif is_media:
            ctx.cat.add(dict(scan_id=ctx.scan_id, source=ctx.source, location="OneDrive › " + name,
                             path=None, name=name, hint="present",
                             kind="video" if mime.startswith("video") else "image",
                             ext=os.path.splitext(name)[1].lower(), size=it.get("size"),
                             sha1=((f.get("hashes") or {}).get("sha1Hash") or "").lower() or None,
                             sha256=((f.get("hashes") or {}).get("sha256Hash") or "").lower() or None,
                             evidence="Still in your OneDrive"))
            live += 1

    marker = ctx.cat.kv_get("onedrive_delta") or {}
    if marker.get("link"):
        ctx.log("OneDrive: checking what was deleted since the last scan ...")
        try:
            gen = _od_pages(marker["link"], token)
            for it in gen:
                if it.get("deleted"):
                    handle(it)
        except Exception as e:
            ctx.log("OneDrive change feed expired (%s); starting a fresh one." % e)
    gen = _od_pages(GRAPH + "/me/drive/root/delta" + sel, token)
    link = None
    while True:
        try:
            handle(next(gen))
        except StopIteration as stop:
            link = stop.value
            break
        if ctx.stopped():
            return live, deleted
    if link:
        ctx.cat.kv_set("onedrive_delta", {"link": link, "at": time.time()})
    ctx.log("OneDrive: %d photos/videos still there, %d deleted item(s) reported." % (live, deleted))
    if not deleted:
        ctx.log("OneDrive's API does not list the recycle bin itself. Anything you deleted in the last 30 days "
                "can be put back at onedrive.live.com > Recycle bin; Rewind will pick it up on the next scan.")
    return live, deleted


def onedrive_restore(cat, item, workspace_dir):
    remote = json.loads(item["remote"])
    if remote.get("via") == "connect":
        from . import connect
        tok = connect.access_token(cat, "onedrive")
        _http("POST", GRAPH + "/me/drive/items/%s/restore" % remote["id"], tok, data={})
        data = _http("GET", GRAPH + "/me/drive/items/%s/content" % remote["id"], tok, raw=True, timeout=600)
        dest = os.path.join(workspace_dir, "%s_%s" % (remote["id"][:8], item["name"]))
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    saved = cat.kv_get("ms_token") or {}
    t = _http("POST", MS_AUTH + "/token", data={
        "client_id": remote["client_id"], "grant_type": "refresh_token", "scope": MS_SCOPE,
        "refresh_token": saved.get("refresh_token")}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    _save_ms(cat, remote["client_id"], t)
    tok = t["access_token"]
    _http("POST", GRAPH + "/me/drive/items/%s/restore" % remote["id"], tok, data={})
    data = _http("GET", GRAPH + "/me/drive/items/%s/content" % remote["id"], tok, raw=True, timeout=600)
    dest = os.path.join(workspace_dir, "%s_%s" % (remote["id"][:8], item["name"]))
    with open(dest, "wb") as f:
        f.write(data)
    return dest


# ================================================================ Dropbox
#
# A Dropbox delete keeps every earlier revision of the file for the plan's
# retention window (30 days on Basic/Plus, 180 on Professional/Business, 365 on
# Advanced). list_folder(include_deleted) names the deleted files; list_revisions
# gives the last revision, which downloads with path "rev:<rev>" and comes back
# with /files/restore. https://www.dropbox.com/developers/documentation/http/documentation

DBX = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"
MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".dng", ".avif",
             ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts", ".m2ts", ".wmv")


def _dbx(path, token, body, content=False, timeout=120):
    import urllib.request
    url = (DBX_CONTENT if content else DBX) + path
    if content:
        req = urllib.request.Request(url, method="POST", headers={
            "Authorization": "Bearer " + token, "Dropbox-API-Arg": json.dumps(body)})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    return _http("POST", url, token, data=body)


def scan_dropbox(ctx, token, via="connect"):
    entries = []
    d = _dbx("/files/list_folder", token, {"path": "", "recursive": True, "include_deleted": True, "limit": 2000})
    while True:
        entries.extend(d.get("entries", []))
        if not d.get("has_more") or ctx.stopped():
            break
        d = _dbx("/files/list_folder/continue", token, {"cursor": d["cursor"]})
    media = [e for e in entries if e.get("name", "").lower().endswith(MEDIA_EXT)]
    con = ctx.cat.con()
    con.execute("DELETE FROM items WHERE source=? AND path IS NULL AND hint='present'", (ctx.source,))
    con.commit()
    live = 0
    for e in media:
        if e.get(".tag") == "file":
            ctx.cat.add(dict(scan_id=ctx.scan_id, source=ctx.source, location="Dropbox › " + e["path_display"],
                             path=None, name=e["name"], hint="present", kind=_kind(e["name"]),
                             ext=os.path.splitext(e["name"])[1].lower(), size=e.get("size"),
                             taken=(e.get("client_modified") or "")[:19] or None, evidence="Still in your Dropbox"))
            live += 1
    out = ctx.out_dir("dropbox")
    n = gone = 0
    deleted = [e for e in media if e.get(".tag") == "deleted"]
    for i, e in enumerate(deleted):
        if ctx.stopped():
            return
        ctx.progress(i, len(deleted), "Dropbox: " + e["name"])
        path = e.get("path_display") or e.get("path_lower")
        try:
            revs = _dbx("/files/list_revisions", token, {"path": path, "mode": "path", "limit": 10})
        except Exception:
            revs = {}
        last = (revs.get("entries") or [None])[0]
        if not last:
            ctx.add_missing(e["name"], "Dropbox › " + path, "Deleted from Dropbox; its revisions have expired.",
                            kind=_kind(e["name"]))
            gone += 1
            continue
        dest = os.path.join(out, "%s_%s" % (last["rev"][:10], e["name"].replace("/", "_")))
        if not os.path.exists(dest):
            try:
                data = _dbx("/files/download", token, {"path": "rev:" + last["rev"]}, content=True, timeout=600)
            except Exception as ex:
                ctx.log("Could not download %s: %s" % (e["name"], ex))
                continue
            with open(dest, "wb") as fh:
                fh.write(data)
        ctx.ingest_file(dest, "trash", location="Dropbox › deleted › " + path, name=e["name"],
                        taken=(last.get("client_modified") or "")[:19] or None,
                        deleted_at=(revs.get("server_deleted") or "")[:19] or None,
                        evidence="Deleted from Dropbox; Dropbox still keeps its last version",
                        remote={"service": "dropbox", "path": path, "rev": last["rev"], "via": via})
        n += 1
    ctx.log("Dropbox: %d deleted photo/video file(s) recovered, %d expired, %d still in Dropbox." % (n, gone, live))


def _kind(name):
    return "video" if name.lower().endswith((".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts",
                                             ".m2ts", ".wmv")) else "image"


def dropbox_restore(cat, remote):
    from . import connect
    _dbx("/files/restore", connect.access_token(cat, "dropbox"), {"path": remote["path"], "rev": remote["rev"]})
    return "Put back in Dropbox"
