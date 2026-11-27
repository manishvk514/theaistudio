"""Telegram account (cloud) through Telegram's official API, using Telethon
(https://github.com/LonamiWebs/Telethon, MIT licence).

First principles:
* Telegram keeps every chat on its servers until someone deletes the message.
  A photo you deleted from your phone's gallery is usually still inside the
  chat you sent or received it in, and can be downloaded again at full quality.
* A message deleted "for everyone" is erased on Telegram's servers. No client
  and no API can bring it back; only copies elsewhere (your phone's Telegram
  cache, a chat export, the other person's phone) can.
* Secret chats are never on the server, so they cannot be downloaded here.

Telegram's rules say every app must use its own API ID, so you create one once
at https://my.telegram.org (free, two minutes), or put it in the TELEGRAM_API_ID /
TELEGRAM_API_HASH settings. Signing in is then the same as adding Telegram
Desktop: scan a QR code with the Telegram app on your phone (Settings › Devices ›
Link Desktop Device). The phone-number-and-code route is kept as a fallback.
The session is saved in Rewind's workspace only.
"""
import asyncio
import os
import threading
import time

_VENDOR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_vendor")

INFO = {
    "id": "telegram-cloud",
    "title": "Telegram account",
    "group": "Accounts",
    "blurb": "Downloads every photo and video still in your Telegram chats, so pictures deleted from your "
             "phone come back at full quality.",
    "link": "https://my.telegram.org/apps",
}


def available():
    try:
        _import()
        return True
    except Exception:
        return False


def _import():
    import sys
    if _VENDOR not in sys.path and os.path.isdir(_VENDOR):
        sys.path.append(_VENDOR)
    import telethon  # noqa: F401
    return telethon


class Telegram:
    """Keeps one Telethon client alive on its own event loop thread so the
    sign-in can span several screens (phone number, code, password)."""

    def __init__(self, cat):
        self.cat = cat
        self.loop = None
        self.client = None
        self.phone = None
        self.code_hash = None
        self.state = "not_connected"
        self.message = ""
        self.lock = threading.Lock()
        self.qr = None
        self.qr_url = None
        self.qr_gen = 0
        if self.app_creds() and os.path.exists(self.session_path() + ".session"):
            self.state = "connected"

    def app_creds(self):
        """The Telegram app ID: from the Space/computer settings if given there, else what was typed once."""
        env_id = os.environ.get("TELEGRAM_API_ID", "").strip()
        env_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        if env_id.isdigit() and len(env_hash) >= 16:
            return {"api_id": env_id, "api_hash": env_hash}
        saved = self.cat.kv_get("telegram_app") or {}
        return saved if saved.get("api_id") and saved.get("api_hash") else None

    def save_creds(self, api_id, api_hash):
        api_id, api_hash = str(api_id or "").strip(), str(api_hash or "").strip()
        if not (api_id.isdigit() and len(api_hash) >= 16):
            raise ValueError("The API ID is a number and the API hash is a 32-character code, both from "
                             "my.telegram.org › API development tools.")
        self.cat.kv_set("telegram_app", {"api_id": api_id, "api_hash": api_hash})
        self.client = None

    def session_path(self):
        return os.path.join(self.cat.dir("cloud"), "telegram")

    def _ensure_loop(self):
        if self.loop:
            return
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro, timeout=300):
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _client(self):
        if self.client is None:
            tl = _import()
            app = self.app_creds() or {}
            if not app:
                raise ValueError("Telegram needs its one-time API ID first.")
            self.client = tl.TelegramClient(self.session_path(), int(app["api_id"]), app["api_hash"],
                                            device_model="Rewind", app_version="3.0")
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    def status(self):
        out = {"state": self.state, "message": self.message, "available": available(),
               "has_app": bool(self.app_creds())}
        if self.state == "need_qr" and self.qr_url:
            from ..server import qr_svg
            out["qr_url"] = self.qr_url
            out["qr_svg"] = qr_svg(self.qr_url)
        return out

    # ---------------------------------------------------------------- QR sign-in (no phone number, no code)
    def qr_start(self, api_id=None, api_hash=None):
        if api_id or api_hash:
            self.save_creds(api_id, api_hash)
        if not self.app_creds():
            self.state = "need_app"
            self.message = ("Telegram asks every app for its own free API ID, once. Get it at my.telegram.org › "
                            "API development tools (any app name), then paste the two values here.")
            return self.status()
        if not available():
            raise RuntimeError("The Telegram library is missing from this copy of Rewind.")
        tl = _import()

        async def go():
            c = await self._client()
            if await c.is_user_authorized():
                return None
            return await c.qr_login()

        try:
            qr = self.run(go(), timeout=60)
        except Exception as e:
            raise ValueError(_friendly(e)) from None
        if qr is None:
            self.state, self.message = "connected", "Telegram connected."
            return self.status()
        self.qr_gen += 1
        gen = self.qr_gen
        self.qr, self.qr_url = qr, qr.url
        self.state = "need_qr"
        self.message = ("On your phone open Telegram › Settings › Devices › Link Desktop Device, and scan this code. "
                        "It refreshes by itself.")

        async def wait():
            for _ in range(20):                     # each code lasts about 30 s; keep offering fresh ones ~10 min
                if gen != self.qr_gen:
                    return
                try:
                    await self.qr.wait(timeout=35)
                    self.state, self.message = "connected", "Telegram connected."
                    self.qr = self.qr_url = None
                    return
                except asyncio.TimeoutError:
                    try:
                        await self.qr.recreate()
                        self.qr_url = self.qr.url
                    except Exception as e:
                        self.state, self.message = "error", _friendly(e)
                        return
                except tl.errors.SessionPasswordNeededError:
                    self.state = "need_password"
                    self.message = "Your Telegram has two-step verification. Type that password to finish."
                    self.qr = self.qr_url = None
                    return
                except Exception as e:
                    self.state, self.message = "error", _friendly(e)
                    self.qr = self.qr_url = None
                    return
            if gen == self.qr_gen and self.state == "need_qr":
                self.state, self.message = "not_connected", "The QR code expired. Press Connect to get a new one."
                self.qr = self.qr_url = None

        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(wait(), self.loop)
        return self.status()

    # ---------------------------------------------------------------- sign-in
    def start(self, api_id, api_hash, phone):
        phone = str(phone or "").strip().replace(" ", "")
        if api_id or api_hash:
            self.save_creds(api_id, api_hash)
        if not self.app_creds():
            raise ValueError("Enter the API ID and API hash from my.telegram.org first.")
        if not phone.startswith("+") or len(phone) < 8:
            raise ValueError("Type your phone number with the country code, e.g. +91 98765 43210.")
        self.qr_gen += 1                                # stop any QR sign-in that was waiting

        async def go():
            c = await self._client()
            if await c.is_user_authorized():
                return None
            sent = await c.send_code_request(phone)
            return sent.phone_code_hash

        try:
            self.code_hash = self.run(go())
        except Exception as e:
            raise ValueError(_friendly(e)) from None
        self.phone = phone
        if self.code_hash is None:
            self.state, self.message = "connected", "Telegram connected."
        else:
            self.state = "need_code"
            self.message = "Telegram sent a login code to your Telegram app. Type it here."
        return self.status()

    def code(self, code):
        tl = _import()

        async def go():
            c = await self._client()
            try:
                await c.sign_in(self.phone, str(code).strip(), phone_code_hash=self.code_hash)
                return "connected"
            except tl.errors.SessionPasswordNeededError:
                return "need_password"

        try:
            self.state = self.run(go())
        except Exception as e:
            raise ValueError(_friendly(e)) from None
        self.message = ("Telegram connected." if self.state == "connected" else
                        "Your account has two-step verification. Type your Telegram password.")
        return self.status()

    def password(self, pw):
        async def go():
            c = await self._client()
            await c.sign_in(password=pw)

        try:
            self.run(go())
        except Exception as e:
            raise ValueError(_friendly(e)) from None
        self.state, self.message = "connected", "Telegram connected."
        return self.status()

    def disconnect(self):
        async def go():
            c = await self._client()
            await c.log_out()

        try:
            self.run(go(), timeout=60)
        except Exception:
            pass
        self.client = None
        for ext in (".session", ".session-journal"):
            try:
                os.remove(self.session_path() + ext)
            except OSError:
                pass
        self.state, self.message = "not_connected", "Telegram disconnected."
        return self.status()

    # ------------------------------------------------------------------- scan
    def scan(self, ctx, max_video_mb=200):
        tl = _import()
        from telethon.tl.types import InputMessagesFilterPhotoVideo
        out_root = ctx.out_dir()

        async def go():
            c = await self._client()
            if not await c.is_user_authorized():
                raise RuntimeError("Telegram is not signed in any more. Connect it again.")
            n = skipped = 0
            dialogs = [d async for d in c.iter_dialogs()]
            for i, d in enumerate(dialogs):
                if ctx.stopped():
                    break
                chat = (d.name or "Chat").replace("/", "_").strip() or "Chat"
                ctx.progress(i, len(dialogs), "Telegram: " + chat)
                folder = os.path.join(out_root, chat[:60])
                async for m in c.iter_messages(d, filter=InputMessagesFilterPhotoVideo):
                    if ctx.stopped():
                        break
                    f = m.file
                    if f is None:
                        continue
                    if m.video and (f.size or 0) > max_video_mb * 1024 * 1024:
                        skipped += 1
                        continue
                    ext = f.ext or (".jpg" if m.photo else ".mp4")
                    os.makedirs(folder, exist_ok=True)
                    dest = os.path.join(folder, "%d_%d%s" % (d.id, m.id, ext))
                    if not os.path.exists(dest):
                        try:
                            await m.download_media(file=dest)
                        except Exception as e:
                            ctx.log("Could not download a file from %s: %s" % (chat, e))
                            continue
                    who = "you sent" if m.out else "you received"
                    taken = m.date.strftime("%Y-%m-%dT%H:%M:%S") if m.date else None
                    if ctx.ingest_file(dest, "backup", location="Telegram › %s" % chat,
                                       name=f.name or os.path.basename(dest), taken=taken,
                                       evidence="Still in your Telegram chat '%s' (%s it)" % (chat, who)):
                        n += 1
            return n, skipped

        n, skipped = self.run(go(), timeout=24 * 3600)
        ctx.log("Telegram: %d photo(s)/video(s) downloaded from your chats.%s" % (
            n, (" %d video(s) over %d MB were skipped." % (skipped, max_video_mb)) if skipped else ""))
        return n


def _friendly(e):
    """Telegram's error names, in plain words."""
    name = e.__class__.__name__
    secs = getattr(e, "seconds", None)
    plain = {
        "ApiIdInvalidError": "Telegram says the API ID / API hash pair is wrong. Copy both again from my.telegram.org.",
        "PhoneNumberInvalidError": "Telegram doesn't recognise that phone number. Include the country code, e.g. +91.",
        "PhoneCodeInvalidError": "That login code is wrong. Check the newest message from Telegram and try again.",
        "PhoneCodeExpiredError": "That login code expired. Ask for a new one. (Telegram also blocks a code that was "
                                 "sent to anyone in a chat, so only type it here.)",
        "PasswordHashInvalidError": "That two-step verification password is wrong.",
        "AuthTokenExpiredError": "The QR code expired before it was scanned. A new one appears by itself; scan that.",
        "FloodWaitError": "Telegram asks to wait %s seconds before trying again." % secs,
        "PhoneNumberBannedError": "Telegram reports this number as banned.",
    }.get(name)
    if plain:
        return plain
    if isinstance(e, (ConnectionError, OSError)):
        return "Could not reach Telegram (%s). Check the internet connection and try again." % e
    return "Telegram: %s" % (str(e) or name)


_INSTANCES = {}


def get(cat):
    key = cat.workspace
    if key not in _INSTANCES:
        _INSTANCES[key] = Telegram(cat)
    return _INSTANCES[key]
