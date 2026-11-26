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
at https://my.telegram.org (free, two minutes). You sign in with your phone
number and the code Telegram sends to your Telegram app, exactly as on a new
phone. The session is saved in Rewind's workspace on this computer only.
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
        saved = cat.kv_get("telegram_app") or {}
        if saved.get("api_id") and os.path.exists(self.session_path() + ".session"):
            self.state = "connected"

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
            app = self.cat.kv_get("telegram_app") or {}
            self.client = tl.TelegramClient(self.session_path(), int(app["api_id"]), app["api_hash"],
                                            device_model="Rewind", app_version="3.0")
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    def status(self):
        return {"state": self.state, "message": self.message, "available": available()}

    # ---------------------------------------------------------------- sign-in
    def start(self, api_id, api_hash, phone):
        api_id, api_hash, phone = str(api_id).strip(), str(api_hash).strip(), str(phone).strip().replace(" ", "")
        if not (api_id.isdigit() and len(api_hash) >= 16 and phone):
            raise ValueError("Enter the API ID (a number), the API hash and your phone number with country code.")
        self.cat.kv_set("telegram_app", {"api_id": api_id, "api_hash": api_hash})
        self.client = None

        async def go():
            c = await self._client()
            if await c.is_user_authorized():
                return None
            sent = await c.send_code_request(phone)
            return sent.phone_code_hash

        self.code_hash = self.run(go())
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

        self.state = self.run(go())
        self.message = ("Telegram connected." if self.state == "connected" else
                        "Your account has two-step verification. Type your Telegram password.")
        return self.status()

    def password(self, pw):
        async def go():
            c = await self._client()
            await c.sign_in(password=pw)

        self.run(go())
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


_INSTANCES = {}


def get(cat):
    key = cat.workspace
    if key not in _INSTANCES:
        _INSTANCES[key] = Telegram(cat)
    return _INSTANCES[key]
