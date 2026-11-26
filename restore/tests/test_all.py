"""End-to-end tests. Filesystem tests create real volumes with real drivers
(mtools / ntfs-3g / exfat-fuse), really delete files, and require the engine to
bring them back byte-for-byte.   Run:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import build_images  # noqa: E402
from fixtures import make_media  # noqa: E402
from resurface.carve import carve  # noqa: E402
from resurface.cloud.providers import Dropbox, GoogleDrive  # noqa: E402
from resurface.engine import Engine  # noqa: E402
from resurface.formats import probe  # noqa: E402
from resurface.server import App, make_handler  # noqa: E402
from resurface.source import BytesSource  # noqa: E402

TMP = tempfile.mkdtemp(prefix="resurface-test-")
FILES = make_media(os.path.join(TMP, "media"))
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def rows(eng, where="1=1"):
    return [dict(r) for r in eng.catalog.db.execute(f"SELECT * FROM items WHERE {where}")]


def restored_hashes(eng, state="deleted"):
    out = os.path.join(eng.workdir, "out-" + state)
    ids = eng.catalog.query(state=state, damaged=False, ids_only=True)
    eng.start("restore", eng.restore, ids, out, wait=True)
    found = {}
    for root, _, names in os.walk(out):
        for n in names:
            with open(os.path.join(root, n), "rb") as f:
                found[sha(f.read())] = n
    return found


class Formats(unittest.TestCase):
    def test_every_format_measured_exactly(self):
        for name, data in FILES.items():
            f = probe(BytesSource(data + b"trailing garbage" * 50), 0)
            self.assertIsNotNone(f, name)
            self.assertEqual(f.length, len(data), name)
            self.assertEqual(f.ext, {"clip.mkv": "mkv", "faststart.mp4": "mp4"}.get(name, name.rsplit(".", 1)[1]), name)

    def test_metadata(self):
        f = probe(BytesSource(FILES["photo_1.jpg"]), 0)
        self.assertEqual((f.width, f.height, f.taken, f.camera), (720, 520, "2021-02-15T10:20:30", "TestCam Model 1"))
        if "clip.mp4" in FILES:
            f = probe(BytesSource(FILES["clip.mp4"]), 0)
            self.assertEqual((f.width, f.height, f.taken), (320, 240, "2021-05-04T03:02:01"))

    def test_truncated_and_corrupt_rejected(self):
        for name, data in FILES.items():
            self.assertIsNone(probe(BytesSource(data[: len(data) // 2]), 0), name)
        png = bytearray(FILES["shot_0.png"])
        png[len(png) // 2] ^= 0xFF          # one flipped bit must fail the CRC check
        self.assertIsNone(probe(BytesSource(bytes(png)), 0))

    def test_carve_from_noise(self):
        rnd = random.Random(3)
        blob, offs = bytearray(rnd.randbytes(5000)), {}
        for name, data in FILES.items():
            offs[len(blob)] = data
            blob += data + rnd.randbytes(rnd.randrange(1, 4000))
        hits = {h.offset: h.found.length for h in carve(BytesSource(bytes(blob)))}
        self.assertEqual(hits, {o: len(d) for o, d in offs.items()})


def have(*tools):
    return all(shutil.which(t) for t in tools)


class Filesystems(unittest.TestCase):
    def check(self, builder, **kw):
        img = os.path.join(TMP, f"{builder.__name__}-{kw.get('fat_bits', '')}.img")
        victims = [v for v in builder(img, FILES, **kw) if "wedge" not in v]
        eng = Engine(os.path.join(TMP, "case-" + os.path.basename(img)))
        job = eng.start("scan", eng.scan_folder, img, wait=True)
        self.assertEqual(job.status, "done", job.log)
        got = restored_hashes(eng)
        for v in victims:
            self.assertIn(sha(FILES[v]), got, f"{v} not recovered byte-exact")
        for k in build_images.KEEP:
            self.assertNotIn(sha(FILES[k]), got, f"{k} still exists but was reported deleted")
        existing = {r["sha256"] for r in rows(eng, "state='existing'")}
        self.assertEqual(existing, {sha(FILES[k]) for k in build_images.KEEP})
        named = [r for r in rows(eng, "origin='fs-deleted' AND quality='verified'")]
        self.assertEqual(len(named), len(victims))       # names and folders came back too
        self.assertTrue(all(r["path"] == "/DCIM" for r in named))
        return eng, img

    @unittest.skipUnless(have("mkfs.vfat", "mcopy", "mdel"), "needs dosfstools + mtools")
    def test_fat12_16_32(self):
        self.check(build_images.build_fat, size_mb=6, fat_bits=12)
        self.check(build_images.build_fat, size_mb=24, fat_bits=16)
        self.check(build_images.build_fat, size_mb=40, fat_bits=32)

    @unittest.skipUnless(have("mkfs.ntfs", "ntfs-3g", "fusermount"), "needs ntfs-3g")
    def test_ntfs(self):
        self.check(build_images.build_ntfs)

    @unittest.skipUnless(have("mkfs.exfat", "mount.exfat-fuse", "losetup"), "needs exfat tools")
    def test_exfat(self):
        self.check(build_images.build_exfat)

    @unittest.skipUnless(have("mkfs.vfat", "mcopy", "mdel"), "needs dosfstools + mtools")
    def test_quick_format_then_carve(self):
        """A 'quick format' rewrites the tables, not the data."""
        img = os.path.join(TMP, "formatted.img")
        build_images.build_fat(img, FILES, size_mb=24, fat_bits=16)
        subprocess.run(["mkfs.vfat", "-F", "16", img], check=True, capture_output=True)
        eng = Engine(os.path.join(TMP, "case-formatted"))
        eng.start("scan", eng.scan_folder, img, wait=True)
        got = restored_hashes(eng)
        missing = {n for n, d in FILES.items() if sha(d) not in got}
        # the two files that were fragmented cannot be carved blind; the 80px GIF is filtered as an icon
        self.assertLessEqual(missing, {"bitmap.bmp", "clip.avi", "anim.gif"})

    def test_no_filesystem_at_all(self):
        img = os.path.join(TMP, "raw.img")
        rnd = random.Random(5)
        with open(img, "wb") as f:
            f.write(rnd.randbytes(4096))
            for d in FILES.values():
                f.write(d)
                f.write(b"\0" * (-f.tell() % 512))
        eng = Engine(os.path.join(TMP, "case-raw"))
        eng.start("scan", eng.scan_folder, img, wait=True)
        r = rows(eng)
        self.assertEqual({x["sha256"] for x in r}, {sha(d) for n, d in FILES.items() if n != "anim.gif"})
        self.assertTrue(all(x["state"] == "unsorted" for x in r))   # nothing to compare against: no false claims


class Folders(unittest.TestCase):
    def test_phone_copy_and_backup(self):
        from PIL import Image
        root = os.path.join(TMP, "phone")
        mk = lambda *p: (os.makedirs(os.path.join(root, *p[:-1]), exist_ok=True), os.path.join(root, *p))[1]  # noqa: E731
        media = os.path.join(TMP, "media")
        shutil.copy(os.path.join(media, "photo_0.jpg"), mk("DCIM", "Camera", "IMG_0001.jpg"))
        Image.open(os.path.join(media, "photo_0.jpg")).resize((160, 120)).save(mk("DCIM", ".thumbnails", "1.jpg"))
        Image.open(os.path.join(media, "photo_1.jpg")).resize((160, 120)).save(mk("DCIM", ".thumbnails", "2.jpg"))
        shutil.copy(os.path.join(media, "photo_2.jpg"), mk("Pictures", ".trashed-1790000000-IMG_0003.jpg"))
        shutil.copy(os.path.join(media, "shot_1.png"), mk("$RECYCLE.BIN", "S-1-5", "$RABC.png"))
        orig = "C:\\Users\\me\\Pictures\\holiday.png"
        with open(mk("$RECYCLE.BIN", "S-1-5", "$IABC.png"), "wb") as f:
            f.write(struct.pack("<QQQI", 2, 1, 133500000000000000, len(orig) + 1) + (orig + "\0").encode("utf-16-le"))
        shutil.copy(os.path.join(media, "anim.gif"), mk(".local", "share", "Trash", "files", "anim.gif"))
        with open(mk(".local", "share", "Trash", "info", "anim.gif.trashinfo"), "w") as f:
            f.write("[Trash Info]\nPath=/home/me/My%20Pics/party.gif\nDeletionDate=2023-04-05T06:07:08\n")
        db = sqlite3.connect(mk("Android", "media", "com.whatsapp", "msg.db"))
        db.execute("create table t(b blob)")
        db.execute("insert into t values(?)", (FILES["pic.webp"],))
        db.commit()
        db.close()
        eng = Engine(os.path.join(TMP, "case-phone"))
        eng.start("scan", eng.scan_folder, root, "device", wait=True)
        by = {r["name"] or r["path"]: r for r in rows(eng)}
        self.assertEqual(by["IMG_0001.jpg"]["state"], "existing")
        self.assertEqual(by["1.jpg"]["state"], "existing")       # thumbnail of a photo that still exists
        self.assertEqual(by["2.jpg"]["state"], "deleted")        # thumbnail whose original is gone
        self.assertEqual((by["IMG_0003.jpg"]["state"], by["IMG_0003.jpg"]["origin"]), ("deleted", "trash"))
        self.assertEqual((by["holiday.png"]["path"], by["holiday.png"]["deleted_at"][:4]), ("C:/Users/me/Pictures", "2024"))
        self.assertEqual((by["party.gif"]["path"], by["party.gif"]["deleted_at"]), ("/home/me/My Pics", "2023-04-05T06:07:08"))
        emb = [r for r in rows(eng) if r["origin"] == "embedded"]
        self.assertEqual([r["sha256"] for r in emb], [sha(FILES["pic.webp"])])   # read out of the SQLite file, byte-exact
        self.assertIn(sha(FILES["pic.webp"]), restored_hashes(eng, "deleted"))

        # an old export: one photo still exists, one does not; plus the full original of thumbnail 2
        back = os.path.join(TMP, "backup")
        os.makedirs(back)
        with zipfile.ZipFile(os.path.join(back, "takeout.zip"), "w") as z:
            z.write(os.path.join(media, "photo_0.jpg"), "Takeout/Google Photos/2020/IMG_0001.jpg")
            z.write(os.path.join(media, "shot_2.png"), "Takeout/Google Photos/2020/old.png")
            z.write(os.path.join(media, "photo_1.jpg"), "Takeout/Google Photos/2021/IMG_0002.jpg")
        eng.start("scan", eng.scan_folder, back, "evidence", wait=True)
        by = {r["name"]: r for r in rows(eng, "origin='backup'")}
        self.assertEqual(by["IMG_0001.jpg"]["state"], "existing")
        self.assertEqual(by["old.png"]["state"], "deleted")
        self.assertEqual(by["IMG_0002.jpg"]["state"], "deleted")
        thumb = rows(eng, "name='2.jpg'")[0]
        self.assertEqual(thumb["shadow"], 1)     # hidden now that the full-resolution original turned up
        got = restored_hashes(eng)
        self.assertIn(sha(FILES["photo_1.jpg"]), got)
        self.assertNotIn(sha(FILES["photo_0.jpg"]), got)

    def test_ios_backup_names(self):
        root = os.path.join(TMP, "iosbackup")
        fid = hashlib.sha1(b"CameraRollDomain-Media/DCIM/100APPLE/IMG_0042.JPG").hexdigest()
        os.makedirs(os.path.join(root, fid[:2]))
        with open(os.path.join(root, fid[:2], fid), "wb") as f:
            f.write(FILES["photo_2.jpg"])
        db = sqlite3.connect(os.path.join(root, "Manifest.db"))
        db.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT, flags INT, file BLOB)")
        db.execute("INSERT INTO Files VALUES(?,?,?,1,NULL)", (fid, "CameraRollDomain", "Media/DCIM/100APPLE/IMG_0042.JPG"))
        db.commit()
        db.close()
        eng = Engine(os.path.join(TMP, "case-ios"))
        eng.start("scan", eng.scan_folder, root, "evidence", wait=True)
        r = rows(eng)[0]
        self.assertEqual((r["name"], r["origin"], r["path"]), ("IMG_0042.JPG", "backup", "CameraRollDomain/Media/DCIM/100APPLE"))

    def test_email_attachments_and_base64(self):
        import base64
        import mailbox
        from email.message import EmailMessage
        root = os.path.join(TMP, "mailroot")
        os.makedirs(root)

        def message(subject, name, data, maintype, subtype):
            m = EmailMessage()
            m["Subject"], m["From"], m["To"] = subject, "Asha <asha@example.test>", "me@example.test"
            m["Date"] = "Tue, 03 Mar 2020 10:11:12 +0000"
            m.set_content("see attached")
            m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
            return m
        box = mailbox.mbox(os.path.join(root, "Inbox"))          # Thunderbird-style, no extension
        box.add(message("no attachment here", "note.txt", b"x" * 900, "text", "plain"))
        box.add(message("Goa trip", "beach.jpg", FILES["photo_1.jpg"], "image", "jpeg"))
        box.add(message("the video", "clip.avi", FILES["clip.avi"], "video", "x-msvideo"))
        box.close()
        with open(os.path.join(root, "one.eml"), "wb") as f:
            f.write(message("single", "scan.png", FILES["shot_2.png"], "image", "png").as_bytes())
        with open(os.path.join(root, "chat-export.json"), "w") as f:
            json.dump({"messages": [{"text": "hi"}, {"photo": base64.b64encode(FILES["photo_2.jpg"]).decode()}]}, f)
        with open(os.path.join(root, "contacts.vcf"), "w") as f:
            b = base64.encodebytes(FILES["shot_1.png"]).decode()
            f.write("BEGIN:VCARD\nFN:Someone\nPHOTO;ENCODING=b;TYPE=PNG:" + b + "END:VCARD\n")
        eng = Engine(os.path.join(TMP, "case-mail"))
        job = eng.start("scan", eng.scan_folder, root, "evidence", wait=True)
        self.assertEqual(job.status, "done", job.log)
        by = {r["sha256"]: r for r in rows(eng)}
        r = by[sha(FILES["photo_1.jpg"])]
        self.assertEqual((r["name"], r["modified"]), ("beach.jpg", "2020-03-03T10:11:12"))
        self.assertIn("Goa trip", r["note"])
        self.assertIn("Asha", r["note"])
        for n in ("clip.avi", "shot_2.png", "photo_2.jpg", "shot_1.png"):
            self.assertIn(sha(FILES[n]), by, n)
        self.assertEqual(len(by), 5)
        # every one of them can be written back out byte-exact
        out = os.path.join(TMP, "mail-out")
        eng.start("restore", eng.restore, [r["id"] for r in rows(eng)], out, wait=True)
        got = set()
        for d, _, names in os.walk(out):
            for n in names:
                with open(os.path.join(d, n), "rb") as f:
                    got.add(sha(f.read()))
        self.assertEqual(got, set(by))

    def test_automatic_search_of_this_computer(self):
        from PIL import Image
        from resurface import discover
        home = os.path.join(TMP, "home")
        media = os.path.join(TMP, "media")
        for d in ("Pictures", ".cache/thumbnails/large", ".local/share/Trash/files", ".cache/google-chrome/Default/Cache"):
            os.makedirs(os.path.join(home, d))
        shutil.copy(os.path.join(media, "photo_0.jpg"), os.path.join(home, "Pictures", "kept.jpg"))
        Image.open(os.path.join(media, "photo_0.jpg")).resize((256, 192)).save(os.path.join(home, ".cache/thumbnails/large/a.png"))
        Image.open(os.path.join(media, "photo_2.jpg")).resize((256, 180)).save(os.path.join(home, ".cache/thumbnails/large/b.png"))
        shutil.copy(os.path.join(media, "shot_1.png"), os.path.join(home, ".local/share/Trash/files/old.png"))
        with open(os.path.join(home, ".cache/google-chrome/Default/Cache/f_0001"), "wb") as f:
            f.write(b"\0" * 100 + FILES["pic.webp"])
        old_home = os.environ["HOME"]
        os.environ["HOME"] = home
        try:
            self.assertEqual([p["label"] for p in discover.hiding_places()], ["Pictures", "Trash", "Thumbnail cache"])
            eng = Engine(os.path.join(TMP, "case-auto"))
            job = eng.start("auto", eng.scan_auto, wait=True)
            self.assertEqual(job.status, "done", job.log)
            st = {(r["name"] or r["path"]): r["state"] for r in rows(eng)}
            self.assertEqual(st, {"kept.jpg": "existing", "a.png": "existing", "b.png": "deleted", "old.png": "deleted"})
            eng.start("auto", eng.scan_auto, True, wait=True)       # now with browser caches; nothing scanned twice
            self.assertEqual(len(eng.catalog.sources()), 4)
            self.assertIn(sha(FILES["pic.webp"]), {r["sha256"] for r in rows(eng, "state='deleted'")})
        finally:
            os.environ["HOME"] = old_home

    def test_refuses_to_write_into_scanned_folder(self):
        root = os.path.join(TMP, "self")
        os.makedirs(os.path.join(root, ".Trash"))
        shutil.copy(os.path.join(TMP, "media", "pic.webp"), os.path.join(root, ".Trash", "a.webp"))
        eng = Engine(os.path.join(TMP, "case-self"))
        eng.start("scan", eng.scan_folder, root, wait=True)
        job = eng.start("restore", eng.restore, eng.catalog.query(ids_only=True), os.path.join(root, "out"), wait=True)
        self.assertEqual(job.status, "error")


class MockCloud(BaseHTTPRequestHandler):
    """Speaks just enough Google Drive v3 and Dropbox v2 to exercise the connectors."""
    drive = {"t1": {"name": "beach.jpg", "trashed": True, "data": FILES["photo_1.jpg"]},
             "l1": {"name": "kept.png", "trashed": False, "data": FILES["shot_0.png"]}}
    dbx_restored = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, data):
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good":
            return self._json({"error": "bad token"}, 401)
        if "/about" in self.path:
            return self._json({"user": {"emailAddress": "me@example.test"}})
        if "/files?" in self.path:
            want = "trashed+%3D+true" in self.path
            return self._json({"files": [
                {"id": i, "name": f["name"], "size": str(len(f["data"])), "mimeType": "image/jpeg",
                 "sha256Checksum": sha(f["data"])} for i, f in self.drive.items() if f["trashed"] == want]})
        if "alt=media" in self.path:
            return self._raw(self.drive[self.path.split("/files/")[1].split("?")[0]]["data"])
        self._json({}, 404)

    def do_PATCH(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.drive[self.path.split("/files/")[1]]["trashed"] = body["trashed"]
        self._json({})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else {}
        if self.path.endswith("/users/get_current_account"):
            return self._json({"email": "me@example.test"})
        if self.path.endswith("/files/list_folder"):
            assert body["include_deleted"]
            return self._json({"has_more": False, "entries": [
                {".tag": "deleted", "name": "gone.mp4", "path_display": "/Vids/gone.mp4"},
                {".tag": "deleted", "name": "notes.txt", "path_display": "/notes.txt"},
                {".tag": "file", "name": "here.jpg", "path_display": "/here.jpg", "size": 5, "id": "id:1", "rev": "r1"}]})
        if self.path.endswith("/files/list_revisions"):
            return self._json({"is_deleted": True, "server_deleted": "2026-01-02T03:04:05Z",
                               "entries": [{"rev": "0123abc", "size": len(FILES["clip.avi"]), "id": "id:9"}]})
        if self.path.endswith("/files/download"):
            assert json.loads(self.headers["Dropbox-API-Arg"])["path"] == "rev:0123abc"
            return self._raw(FILES["clip.avi"])
        if self.path.endswith("/files/restore"):
            self.dbx_restored.append(body)
            return self._json({})
        self._json({}, 404)


class Cloud(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockCloud)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_google_drive_flow(self):
        eng = Engine(os.path.join(TMP, "case-gdrive"))
        job = eng.start("cloud", eng.scan_cloud, GoogleDrive("good", self.url), wait=True)
        self.assertEqual(job.status, "done", job.log)
        states = {r["name"]: r["state"] for r in rows(eng)}
        self.assertEqual(states, {"beach.jpg": "deleted", "kept.png": "existing"})
        self.assertIn(sha(FILES["photo_1.jpg"]), restored_hashes(eng))      # downloaded out of the trash
        ids = eng.catalog.query(ids_only=True)
        eng.start("restore", eng.restore, ids, "", True, wait=True)          # and put back in place
        self.assertFalse(MockCloud.drive["t1"]["trashed"])

    def test_dropbox_flow(self):
        eng = Engine(os.path.join(TMP, "case-dropbox"))
        job = eng.start("cloud", eng.scan_cloud, Dropbox("good", self.url, self.url), wait=True)
        self.assertEqual(job.status, "done", job.log)
        states = {r["name"]: r["state"] for r in rows(eng)}
        self.assertEqual(states, {"gone.mp4": "deleted", "here.jpg": "existing"})
        self.assertIn(sha(FILES["clip.avi"]), restored_hashes(eng))
        eng.start("restore", eng.restore, eng.catalog.query(ids_only=True), "", True, wait=True)
        self.assertEqual(MockCloud.dbx_restored, [{"path": "/Vids/gone.mp4", "rev": "0123abc"}])

    def test_bad_token_reported(self):
        eng = Engine(os.path.join(TMP, "case-badtoken"))
        job = eng.start("cloud", eng.scan_cloud, GoogleDrive("wrong", self.url), wait=True)
        self.assertEqual(job.status, "error")
        self.assertIn("401", job.log[0])


class Server(unittest.TestCase):
    def test_local_only_and_token_guard(self):
        app = App(Engine(os.path.join(TMP, "case-server")), 0)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def get(path, **headers):
            try:
                return urllib.request.urlopen(urllib.request.Request(base + path, headers=headers)).status
            except urllib.error.HTTPError as e:
                return e.code
        self.assertEqual(get("/"), 200)
        self.assertEqual(get("/api/state"), 401)                                     # no token
        self.assertEqual(get("/api/state", **{"X-Resurface-Token": app.token}), 200)
        self.assertEqual(get("/", Host="attacker.example"), 403)                      # DNS rebinding
        httpd.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
