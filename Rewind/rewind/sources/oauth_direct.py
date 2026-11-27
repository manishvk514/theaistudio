"""One-click account sign-in on a server (Hugging Face) with Rewind's own app keys.

Why this exists: the no-setup sign-in (rclone's shared app) is only allowed to
send you back to *your own computer* (127.0.0.1). On a server nothing is there,
so the browser shows "can't connect" and you have to copy the address back.
With app keys registered for this Rewind (free; done once), the provider sends
you straight back to Rewind: press Connect, sign in, press Allow, done.

Keys come from settings (Hugging Face: Space › Settings › Variables and secrets):
  Microsoft (OneDrive)  MS_CLIENT_ID, MS_CLIENT_SECRET
  Google (Drive)        GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  Dropbox               DROPBOX_APP_KEY, DROPBOX_APP_SECRET

The token is saved in rclone's own format *with these keys*, so rclone keeps it
fresh and every scan / restore path works exactly as with the shared app.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import connect

PROVIDERS = {
    "onedrive": {
        "title": "OneDrive", "env": ("MS_CLIENT_ID", "MS_CLIENT_SECRET"),
        "auth": "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        "scope": "Files.ReadWrite offline_access User.Read",
        "extra": {"prompt": "select_account"},
        "console": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "steps": [
            "Open the Microsoft app registrations page and press New registration.",
            "Name: Rewind. Supported account types: Personal Microsoft accounts only.",
            "Redirect URI: platform Web, paste the address shown below. Press Register.",
            "Copy the Application (client) ID: that is MS_CLIENT_ID.",
            "Certificates & secrets › New client secret › Add, then copy its Value: that is MS_CLIENT_SECRET.",
            "API permissions › Add › Microsoft Graph › Delegated: Files.ReadWrite, offline_access, User.Read.",
        ],
        "note": "Microsoft may ask you to create a free Microsoft Entra (Azure) account first. If it refuses a personal "
                "account, use OneDrive from Rewind on your Mac, where sign-in is already one click.",
    },
    "gdrive": {
        "title": "Google Drive", "env": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/drive",
        "extra": {"access_type": "offline", "prompt": "consent"},
        "console": "https://console.cloud.google.com/apis/credentials",
        "steps": [
            "In Google Cloud Console create a project named Rewind, then enable the Google Drive API.",
            "OAuth consent screen: External, app name Rewind, your e-mail; add yourself as a test user.",
            "Credentials › Create credentials › OAuth client ID › Web application.",
            "Authorised redirect URI: paste the address shown below. Create.",
            "Copy the Client ID (GOOGLE_CLIENT_ID) and Client secret (GOOGLE_CLIENT_SECRET).",
        ],
        "note": "While the app is in Google's 'Testing' mode, Google ends the sign-in after 7 days and you press "
                "Connect again; publishing the app removes that.",
    },
    "dropbox": {
        "title": "Dropbox", "env": ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET"),
        "auth": "https://www.dropbox.com/oauth2/authorize",
        "token": "https://api.dropboxapi.com/oauth2/token",
        "scope": "account_info.read files.metadata.read files.content.read files.content.write",
        "extra": {"token_access_type": "offline"},
        "console": "https://www.dropbox.com/developers/apps/create",
        "steps": [
            "Create app › Scoped access › Full Dropbox › name it (e.g. Rewind-yourname).",
            "Permissions tab: tick files.metadata.read, files.content.read, files.content.write; Submit.",
            "Settings tab › OAuth 2 › Redirect URIs: paste the address shown below, press Add.",
            "Copy App key (DROPBOX_APP_KEY) and App secret (DROPBOX_APP_SECRET).",
        ],
        "note": "",
    },
}


def keys(service, env=None):
    env = os.environ if env is None else env
    cid_name, sec_name = PROVIDERS[service]["env"]
    cid, sec = env.get(cid_name, "").strip(), env.get(sec_name, "").strip()
    return (cid, sec) if cid and sec else None


def configured(service, env=None):
    return service in PROVIDERS and keys(service, env) is not None


def redirect_uri(base, service):
    return "%s/oauth/%s/callback" % (base.rstrip("/"), service)


def auth_url(service, base, state, env=None):
    p = PROVIDERS[service]
    cid, _ = keys(service, env)
    q = {"client_id": cid, "redirect_uri": redirect_uri(base, service), "response_type": "code",
         "scope": p["scope"], "state": state}
    q.update(p["extra"])
    return p["auth"] + "?" + urllib.parse.urlencode(q)


def exchange(cat, service, base, code, env=None, opener=None, http_get=None):
    """Code -> token, saved for rclone. Returns the service title."""
    p = PROVIDERS[service]
    cid, sec = keys(service, env)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(base, service),
            "client_id": cid, "client_secret": sec}
    if service == "onedrive":
        form["scope"] = p["scope"]
    req = urllib.request.Request(p["token"], data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=60) as r:
            t = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8", "replace")
        raise RuntimeError("%s refused the sign-in: %s" % (p["title"], _explain(body)))
    if "access_token" not in t:
        raise RuntimeError("%s did not return a sign-in token." % p["title"])
    expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + int(t.get("expires_in") or 3600)))
    token = {"access_token": t["access_token"], "token_type": t.get("token_type", "Bearer"),
             "refresh_token": t.get("refresh_token", ""), "expiry": expiry}
    connect.save_token(cat, service, token, http_get, extra={"client_id": cid, "client_secret": sec})
    connect.STATE[service] = {"state": "connected", "message": "%s connected." % p["title"]}
    return p["title"]


def _explain(body):
    try:
        j = json.loads(body)
        return j.get("error_description") or j.get("error_summary") or j.get("error") or body[:200]
    except ValueError:
        return body[:200]

