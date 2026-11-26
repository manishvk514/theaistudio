"""Cloud accounts whose APIs expose deleted items: Google Drive and Dropbox.

A cloud "delete" is a database flag plus a retention timer. Until the timer
runs out the provider still has the bytes, and these two providers let the
account owner list, download and un-delete them through their public API.
Once the provider purges an item, no software outside that company can bring
it back -- then the only routes left are the local remnants and backups the
rest of this program searches.

Google Photos and OneDrive-personal offer no API for their bins (see
knowledge.py); those are handled through local evidence and guided steps.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "avif", "dng", "cr2", "cr3",
             "nef", "arw", "raf", "orf", "rw2"}
VIDEO_EXT = {"mp4", "mov", "m4v", "avi", "mkv", "webm", "3gp", "3g2", "mts", "m2ts", "wmv", "flv", "mpg", "mpeg"}


def media_kind(name: str):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_EXT:
        return "image", ext
    if ext in VIDEO_EXT:
        return "video", ext
    return None, ext


class ApiError(RuntimeError):
    pass


def call(url, token, method="GET", body=None, headers=None, raw=False, timeout=120):
    h = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read(600).decode("utf-8", "replace")
        hint = " (token expired or lacks permission: reconnect)" if e.code in (401, 403) else ""
        raise ApiError(f"{e.code} from {urllib.parse.urlsplit(url).netloc}{hint}: {detail}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {e.reason}") from None
    if raw:
        return resp
    text = resp.read()
    return json.loads(text) if text else {}


def _pipe(resp, fh):
    while True:
        b = resp.read(1 << 20)
        if not b:
            break
        fh.write(b)


class GoogleDrive:
    name, title = "gdrive", "Google Drive"
    scope = "https://www.googleapis.com/auth/drive"

    def __init__(self, token, api=None):
        self.token = token
        self.api = (api or os.environ.get("RESURFACE_GDRIVE_API") or "https://www.googleapis.com/drive/v3").rstrip("/")

    def account(self):
        return call(self.api + "/about?fields=user(emailAddress,displayName)", self.token)["user"].get("emailAddress", "account")

    def _list(self, trashed: bool):
        q = f"trashed = {'true' if trashed else 'false'} and (mimeType contains 'image/' or mimeType contains 'video/')"
        fields = ("nextPageToken,files(id,name,size,mimeType,sha256Checksum,trashedTime,modifiedTime,createdTime,"
                  "imageMediaMetadata(width,height,time),videoMediaMetadata(width,height))")
        page = ""
        while True:
            qs = urllib.parse.urlencode({"q": q, "fields": fields, "pageSize": 1000, "spaces": "drive",
                                         "corpora": "user", **({"pageToken": page} if page else {})})
            d = call(f"{self.api}/files?{qs}", self.token)
            for f in d.get("files", []):
                kind = "image" if f.get("mimeType", "").startswith("image/") else "video"
                _, ext = media_kind(f.get("name", ""))
                meta = f.get("imageMediaMetadata") or f.get("videoMediaMetadata") or {}
                taken = (meta.get("time") or "")
                if len(taken) >= 19 and taken[4] == ":":
                    taken = taken[:10].replace(":", "-") + "T" + taken[11:19]
                yield {"id": f["id"], "name": f.get("name", f["id"]), "kind": kind,
                       "ext": ext or f.get("mimeType", "/bin").split("/")[1][:5], "size": int(f.get("size", 0) or 0),
                       "sha256": f.get("sha256Checksum"), "width": meta.get("width", 0) or 0, "height": meta.get("height", 0) or 0,
                       "taken": taken, "modified": (f.get("modifiedTime") or "")[:19],
                       "deleted_at": (f.get("trashedTime") or "")[:19],
                       "note": "in Google Drive trash (purged 30 days after deletion)" if trashed else ""}
            page = d.get("nextPageToken", "")
            if not page:
                return

    def deleted(self):
        return self._list(True)

    def live(self):
        return self._list(False)

    def download(self, loc, fh):
        _pipe(call(f"{self.api}/files/{urllib.parse.quote(loc['id'])}?alt=media", self.token, raw=True), fh)

    def restore(self, loc):
        call(f"{self.api}/files/{urllib.parse.quote(loc['id'])}", self.token, "PATCH", {"trashed": False})


class Dropbox:
    name, title = "dropbox", "Dropbox"
    scope = "account_info.read files.metadata.read files.content.read files.content.write"

    def __init__(self, token, api=None, content=None):
        self.token = token
        self.api = (api or os.environ.get("RESURFACE_DROPBOX_API") or "https://api.dropboxapi.com/2").rstrip("/")
        self.content = (content or os.environ.get("RESURFACE_DROPBOX_CONTENT") or "https://content.dropboxapi.com/2").rstrip("/")

    def account(self):
        d = call(self.api + "/users/get_current_account", self.token, "POST")
        return d.get("email", "account")

    def _walk(self):
        d = call(self.api + "/files/list_folder", self.token, "POST",
                 {"path": "", "recursive": True, "include_deleted": True, "limit": 2000})
        while True:
            yield from d.get("entries", [])
            if not d.get("has_more"):
                return
            d = call(self.api + "/files/list_folder/continue", self.token, "POST", {"cursor": d["cursor"]})

    def _entries(self):
        if not hasattr(self, "_cache"):
            self._cache = list(self._walk())
        return self._cache

    def deleted(self):
        for e in self._entries():
            if e.get(".tag") != "deleted":
                continue
            kind, ext = media_kind(e.get("name", ""))
            if not kind:
                continue
            path = e.get("path_display") or e.get("path_lower")
            try:
                revs = call(self.api + "/files/list_revisions", self.token, "POST",
                            {"path": path, "mode": "path", "limit": 10})
            except ApiError:
                continue        # a deleted *folder* placeholder, or revisions already expired
            if not revs.get("is_deleted") or not revs.get("entries"):
                continue
            last = revs["entries"][0]
            yield {"id": last.get("id", path), "rev": last["rev"], "cloud_path": path, "name": e["name"], "kind": kind,
                   "ext": ext, "size": last.get("size", 0), "path": os.path.dirname(path),
                   "modified": (last.get("client_modified") or "")[:19],
                   "deleted_at": (revs.get("server_deleted") or "")[:19],
                   "note": "deleted file with a restorable revision in Dropbox"}

    def live(self):
        for e in self._entries():
            if e.get(".tag") != "file":
                continue
            kind, ext = media_kind(e.get("name", ""))
            if kind:
                yield {"id": e.get("id", ""), "rev": e.get("rev", ""), "cloud_path": e.get("path_display", ""),
                       "name": e["name"], "kind": kind, "ext": ext, "size": e.get("size", 0),
                       "path": os.path.dirname(e.get("path_display", "")), "modified": (e.get("client_modified") or "")[:19]}

    def download(self, loc, fh):
        arg = json.dumps({"path": "rev:" + loc["rev"]})
        _pipe(call(self.content + "/files/download", self.token, "POST",
                   headers={"Dropbox-API-Arg": arg}, raw=True), fh)

    def restore(self, loc):
        call(self.api + "/files/restore", self.token, "POST", {"path": loc["path"], "rev": loc["rev"]})


PROVIDERS = {"gdrive": GoogleDrive, "dropbox": Dropbox}

OAUTH = {
    "gdrive": {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
               "scope": GoogleDrive.scope, "extra": {"access_type": "online", "prompt": "consent"}},
    "dropbox": {"auth": "https://www.dropbox.com/oauth2/authorize", "token": "https://api.dropboxapi.com/oauth2/token",
                "scope": Dropbox.scope, "extra": {"token_access_type": "online"}},
}


def oauth_url(provider, client_id, redirect, state, challenge):
    cfg = OAUTH[provider]
    return cfg["auth"] + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": cfg["scope"],
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **cfg["extra"]})


def oauth_exchange(provider, client_id, client_secret, redirect, code, verifier) -> str:
    form = {"client_id": client_id, "redirect_uri": redirect, "grant_type": "authorization_code",
            "code": code, "code_verifier": verifier}
    if client_secret:
        form["client_secret"] = client_secret
    req = urllib.request.Request(OAUTH[provider]["token"], data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        raise ApiError(f"sign-in failed: {e.read(400).decode('utf-8', 'replace')}") from None
    return d["access_token"]
