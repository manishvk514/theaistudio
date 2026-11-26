"""End-to-end test: build fake phones, backups, cards and exports, scan them,
and check that Rewind separates deleted from present correctly.

Run:  python3 tests/run_tests.py
"""
import io
import json
import mailbox
import os
import plistlib
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw  # noqa: E402

from rewind import server  # noqa: E402
from rewind.core import carve, media, classify  # noqa: E402
from rewind.sources import computer  # noqa: E402

TMP = tempfile.mkdtemp(prefix="rewind_test_")
random.seed(7)


def photo(i, size=(1200, 900), date="2021:06:%02d 10:00:00"):
    im = Image.new("RGB", size, ((i * 53) % 255, (i * 97) % 255, (i * 31) % 255))
    d = ImageDraw.Draw(im)
    rnd = random.Random(i)
    for _ in range(25):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.ellipse([x, y, x + rnd.randrange(40, 300), y + rnd.randrange(40, 300)],
                  fill=(rnd.randrange(255), rnd.randrange(255), rnd.randrange(255)))
    d.text((40, 40), "PHOTO %d" % i, fill=(255, 255, 255))
    exif = Image.Exif()
    exif[0x8769] = {0x9003: date % (i % 28 + 1)}
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, exif=exif)
    return buf.getvalue()


def thumb(data, edge=256):
    im = Image.open(io.BytesIO(data))
    im.thumbnail((edge, edge))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=70)
    return b.getvalue()


def png(i):
    im = Image.new("RGB", (640, 480), (i * 20 % 255, 200, 90))
    ImageDraw.Draw(im).rectangle([100, 100, 300 + i * 10, 300], fill=(10, 10, 200))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def video(seconds=2, color="red"):
    out = os.path.join(TMP, "v_%s.mp4" % color)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
                    "-f", "lavfi", "-i", "color=c=%s:size=320x240" % color, "-filter_complex",
                    "[0][1]blend=all_mode=average", "-t", str(seconds), "-pix_fmt", "yuv420p", out], check=True)
    return open(out, "rb").read()


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


P = {i: photo(i) for i in range(1, 26)}
V1, V2 = video(2, "red"), video(3, "blue")


def build():
    # ---- current library (e.g. Pictures folder): photos 1-6 + video 1
    lib = os.path.join(TMP, "library")
    for i in range(1, 7):
        w(os.path.join(lib, "IMG_%04d.jpg" % i), P[i])
    w(os.path.join(lib, "clip1.mp4"), V1)

    # ---- Android phone copy
    ph = os.path.join(TMP, "android")
    w(os.path.join(ph, "DCIM/Camera/IMG_0001.jpg"), P[1])                           # present
    w(os.path.join(ph, "DCIM/Camera/.trashed-1767000000-IMG_0007.jpg"), P[7])      # in trash
    w(os.path.join(ph, "DCIM/.thumbnails/1234.jpg"), thumb(P[8]))                  # preview only
    w(os.path.join(ph, "DCIM/.thumbnails/9999.jpg"), thumb(P[2]))                  # preview of present
    wa = os.path.join(ph, "Android/media/com.whatsapp/WhatsApp")
    w(os.path.join(wa, "Media/WhatsApp Images/IMG-20210603-WA0001.jpg"), P[3])    # present elsewhere
    w(os.path.join(wa, "Media/.Statuses/abc.jpg"), P[9])                           # cache, deleted
    # WhatsApp database: references a missing file; holds thumbnails incl. a deleted row
    db = os.path.join(wa, "Databases/msgstore.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE jid(_id INTEGER PRIMARY KEY, user TEXT, raw_string TEXT);
      CREATE TABLE chat(_id INTEGER PRIMARY KEY, jid_row_id INTEGER, subject TEXT);
      CREATE TABLE message_media(message_row_id INTEGER, chat_row_id INTEGER, file_path TEXT, file_hash TEXT, mime_type TEXT);
      CREATE TABLE message_thumbnail(message_row_id INTEGER, thumbnail BLOB);
      PRAGMA secure_delete=OFF;""")
    con.execute("INSERT INTO jid VALUES (1,'919800000000','919800000000@s.whatsapp.net')")
    con.execute("INSERT INTO chat VALUES (1,1,'Family')")
    import base64, hashlib
    con.execute("INSERT INTO message_media VALUES (1,1,'Media/WhatsApp Images/IMG-20210610-WA0002.jpg',?, 'image/jpeg')",
                (base64.b64encode(hashlib.sha256(P[15]).digest()).decode(),))
    con.execute("INSERT INTO message_thumbnail VALUES (1, ?)", (thumb(P[10], 120) + b"\x00" * 10,))
    con.execute("INSERT INTO message_thumbnail VALUES (2, ?)", (thumb(P[11], 120),))
    con.commit()
    con.execute("DELETE FROM message_thumbnail WHERE message_row_id=2")              # deleted row
    con.commit()
    con.close()

    # ---- SD card raw image with deleted files in free space
    card = bytearray(os.urandom(3 << 20))
    card[0:512] = b"\xeb\x58\x90MSDOS5.0" + bytes(501)
    for off, blob in ((300_000, P[12]), (1_200_000, png(1)), (2_000_000, V2)):
        card[off:off + len(blob)] = blob
    card[2_900_000:2_900_000 + 40000] = P[13][:40000]  # truncated (half overwritten) photo
    sd = w(os.path.join(TMP, "sdcard.img"), bytes(card) + os.urandom(1 << 20))

    # ---- iPhone backup (unencrypted)
    ib = os.path.join(TMP, "ios_backup", "00008110-000A1B2C3D4E")
    os.makedirs(ib)
    with open(os.path.join(ib, "Info.plist"), "wb") as f:
        plistlib.dump({"Device Name": "Manish's iPhone", "Last Backup Date": "2024-01-02"}, f)
    with open(os.path.join(ib, "Manifest.plist"), "wb") as f:
        plistlib.dump({"IsEncrypted": False}, f)
    mcon = sqlite3.connect(os.path.join(ib, "Manifest.db"))
    mcon.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")

    def addf(domain, rel, data):
        import hashlib as hl
        fid = hl.sha1(("%s-%s" % (domain, rel)).encode()).hexdigest()
        w(os.path.join(ib, fid[:2], fid), data)
        mcon.execute("INSERT INTO Files VALUES (?,?,?,1,NULL)", (fid, domain, rel))

    pdb = os.path.join(TMP, "Photos.sqlite")
    pc = sqlite3.connect(pdb)
    pc.execute("CREATE TABLE ZASSET(Z_PK INTEGER PRIMARY KEY, ZDIRECTORY TEXT, ZFILENAME TEXT, ZTRASHEDSTATE INT, "
               "ZTRASHEDDATE REAL, ZDATECREATED REAL, ZKIND INT, ZUUID TEXT, ZWIDTH INT, ZHEIGHT INT)")
    rows = [("DCIM/100APPLE", "IMG_0004.JPG", 0, None), ("DCIM/100APPLE", "IMG_0016.JPG", 1, 724000000.0),
            ("DCIM/100APPLE", "IMG_0017.JPG", 1, 724100000.0)]
    for d, fn, tr, trd in rows:
        pc.execute("INSERT INTO ZASSET(ZDIRECTORY,ZFILENAME,ZTRASHEDSTATE,ZTRASHEDDATE,ZDATECREATED,ZKIND) VALUES (?,?,?,?,?,0)",
                   (d, fn, tr, trd, 690000000.0))
    pc.commit()
    pc.close()
    addf("CameraRollDomain", "Media/PhotoData/Photos.sqlite", open(pdb, "rb").read())
    addf("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0004.JPG", P[4])     # present
    addf("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0016.JPG", P[16])     # recently deleted, still there
    # IMG_0017 trashed and purged -> missing
    addf("CameraRollDomain", "Media/PhotoData/Thumbnails/V2/DCIM/100APPLE/IMG_0018.JPG/5005.JPG", thumb(P[18]))
    addf("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", "Message/Media/1234@s.whatsapp.net/a/b/x.jpg", P[19])
    addf("HomeDomain", "Library/Preferences/x.plist", b"bplist00" + bytes(100))
    mcon.commit()
    mcon.close()

    # ---- Google Takeout zip: Photos (library) + a Gmail attachment of a deleted photo
    mb_path = os.path.join(TMP, "All mail.mbox")
    mb = mailbox.mbox(mb_path)
    msg = EmailMessage()
    msg["Subject"] = "Trip photos"
    msg["Date"] = "Tue, 01 Jun 2021 10:00:00 +0000"
    msg.set_content("see attached")
    msg.add_attachment(P[14], maintype="image", subtype="jpeg", filename="beach.jpg")
    mb.add(msg)
    mb.flush()
    tz = os.path.join(TMP, "takeout-20261126T000000Z-001.zip")
    with zipfile.ZipFile(tz, "w") as z:
        z.writestr("Takeout/Google Photos/Photos from 2021/IMG_0005.jpg", P[5])
        z.writestr("Takeout/Google Photos/Photos from 2021/IMG_0005.jpg.supplemental-metadata.json",
                   json.dumps({"title": "IMG_0005.jpg", "photoTakenTime": {"timestamp": "1622541600"}}))
        z.write(mb_path, "Takeout/Mail/All mail Including Spam and Trash.mbox")

    # ---- Telegram Desktop export
    tg = os.path.join(TMP, "telegram_export")
    w(os.path.join(tg, "photos/photo_1@01-06-2021_10-00-00.jpg"), P[20])
    with open(os.path.join(tg, "result.json"), "w") as f:
        json.dump({"name": "Friends", "messages": [{"id": 1, "date": "2021-06-01T10:00:00",
                                                    "photo": "photos/photo_1@01-06-2021_10-00-00.jpg"}]}, f)

    # ---- Snapchat My Data export (folder with memories file)
    sc = os.path.join(TMP, "snapchat")
    w(os.path.join(sc, "memories/2021-06-01_memory.jpg"), P[6])  # still in library -> duplicate
    w(os.path.join(sc, "json/memories_history.json"), json.dumps({"Saved Media": []}).encode())
    # ---- Instagram data download (zip) with Meta JSON and a recently deleted folder
    ig = os.path.join(TMP, "instagram-manish-2026-11-26.zip")
    with zipfile.ZipFile(ig, "w") as z:
        z.writestr("media/posts/202107/post1.jpg", P[21])
        z.writestr("media/recently_deleted/202111/gone.jpg", P[22])
        z.writestr("your_instagram_activity/content/posts_1.json", json.dumps(
            [{"media": [{"uri": "media/posts/202107/post1.jpg", "creation_timestamp": 1625140800}]}]))

    # ---- Android SMS Backup & Restore XML with an MMS picture
    import base64 as b64
    mms = os.path.join(TMP, "sms-20261126.xml")
    with open(mms, "w") as f:
        f.write('<?xml version="1.0"?><smses count="1"><mms date="1625140800000" address="+919800000001" '
                'contact_name="Didi"><parts><part ct="application/smil" text="x"/><part ct="image/jpeg" '
                'name="IMG_mms.jpg" data="%s"/></parts></mms></smses>' % b64.b64encode(P[23]).decode())

    # ---- Time Machine-like backup holding an old Photos library + hard-linked snapshots
    tm = os.path.join(TMP, "Backups.backupdb", "MacBook")
    lib_old = os.path.join(tm, "2023-01-01-000000", "Macintosh HD", "Users", "m", "Pictures", "Photos Library.photoslibrary")
    w(os.path.join(lib_old, "originals", "A", "IMG_0024.jpeg"), P[24])
    w(os.path.join(lib_old, "originals", "B", "IMG_0001.jpeg"), P[1])
    pdb2 = os.path.join(lib_old, "database", "Photos.sqlite")
    os.makedirs(os.path.dirname(pdb2), exist_ok=True)
    c2 = sqlite3.connect(pdb2)
    c2.execute("CREATE TABLE ZASSET(Z_PK INTEGER PRIMARY KEY, ZDIRECTORY TEXT, ZFILENAME TEXT, ZTRASHEDSTATE INT, "
               "ZTRASHEDDATE REAL, ZDATECREATED REAL, ZKIND INT, ZUUID TEXT, ZWIDTH INT, ZHEIGHT INT)")
    c2.execute("INSERT INTO ZASSET(ZDIRECTORY,ZFILENAME,ZTRASHEDSTATE,ZDATECREATED,ZKIND) VALUES ('A','IMG_0024.jpeg',0,690000000,0)")
    c2.execute("INSERT INTO ZASSET(ZDIRECTORY,ZFILENAME,ZTRASHEDSTATE,ZDATECREATED,ZKIND) VALUES ('B','IMG_0001.jpeg',0,690000000,0)")
    c2.commit(); c2.close()
    snap1 = w(os.path.join(tm, "2023-01-01-000000", "Macintosh HD", "Users", "m", "Desktop", "old.jpg"), P[25])
    snap2 = os.path.join(tm, "2023-02-01-000000", "Macintosh HD", "Users", "m", "Desktop", "old.jpg")
    os.makedirs(os.path.dirname(snap2), exist_ok=True)
    os.link(snap1, snap2)
    return dict(lib=lib, phone=ph, sd=sd, ib=ib, takeout=tz, tg=tg, sc=sc, ig=ig, mms=mms,
                tm=os.path.dirname(tm))


def run(app, source, params):
    jid = app.start_scan(source, params)
    job = app.jobs[jid]
    while job.state == "running":
        time.sleep(0.2)
    print("  [%s] %s  -> %s, found %d" % (source, params.get("path", ""), job.state, job.found))
    if job.state != "done":
        print("\n".join(job.logs))
    assert job.state == "done", source
    return job


def by_name(app):
    con = app.cat.con()
    return [dict(r) for r in con.execute("SELECT name, location, hint, verdict, category, is_primary, source, damaged, "
                                         "path IS NOT NULL AS f FROM items")]


def main():
    paths = build()
    print("Fixtures in", TMP)

    # unit checks -----------------------------------------------------------
    assert media.sniff(P[1][:64]) == ("image", ".jpg")
    assert media.sniff(V1[:64])[0] == "video"
    hits = list(carve.carve(paths["sd"]))
    exts = sorted(h["ext"] for h in hits)
    print("  carver hits on SD image:", exts)
    full = [h for h in hits if h["ext"] == ".jpg" and h["size"] == len(P[12])]
    assert full, "full JPEG not carved with exact size"
    assert any(h["ext"] == ".png" for h in hits) and any(h["ext"] == ".mp4" for h in hits)
    mp4 = [h for h in hits if h["ext"] == ".mp4"][0]
    assert mp4["size"] == len(V2), (mp4["size"], len(V2))

    # Windows Recycle Bin $I parser
    name = "C:\\Users\\M\\Pictures\\beach.jpg"
    rec = struct.pack("<QQQI", 2, 12345, 133500000000000000, len(name) + 1) + (name + "\x00").encode("utf-16-le")
    orig, size, when = computer.parse_recycle_info(rec)
    assert orig == name and size == 12345 and when.startswith("2024"), (orig, size, when)

    # end-to-end ------------------------------------------------------------
    app = server.App(os.path.join(TMP, "workspace"), 0)

    class FakeCtx:
        pass
    run(app, "folder", {"path": paths["lib"], "role": "library", "deep": False})
    run(app, "folder", {"path": paths["phone"], "role": "library", "deep": True})
    run(app, "carve", {"path": paths["sd"]})
    run(app, "iphone-backup", {"path": paths["ib"], "role": "search"})
    run(app, "google-takeout", {"path": paths["takeout"], "role": "library"})
    run(app, "telegram", {"path": paths["tg"]})
    run(app, "snapchat", {"path": paths["sc"], "download": False})
    run(app, "whatsapp", {"path": os.path.join(paths["phone"], "Android/media/com.whatsapp/WhatsApp"),
                          "role": "library"})

    run(app, "data-download", {"path": paths["ig"]})
    run(app, "mms", {"path": paths["mms"]})
    run(app, "mac-backups", {"path": paths["tm"]})
    # email: feed a real MIME message through the attachment saver
    from rewind.sources import email_imap
    em = EmailMessage()
    em["Subject"] = "Old trip"; em["To"] = "friend@example.com"; em["Date"] = "Tue, 01 Jun 2019 10:00:00 +0000"
    em.set_content("x"); em.add_attachment(P[23][:0] + photo(30), maintype="image", subtype="jpeg", filename="trip.jpg")
    os.makedirs(os.path.join(TMP, "nothing"), exist_ok=True)
    jid = app.start_scan("folder", {"path": os.path.join(TMP, "nothing"), "role": "search"})
    while app.jobs[jid].state == "running":
        time.sleep(0.1)
    from rewind.sources.base import Context as _C
    ectx = _C(app.cat, app.jobs[jid], 998, "email")
    assert email_imap._save_parts(ectx, em, ectx.out_dir("email"), "[Gmail]/Sent Mail", False) == 1

    # thumbcache: build a Windows 10 style file with two entries
    tc = os.path.join(TMP, "thumbcache", "thumbcache_256.db")
    entries = b""
    for i, data in enumerate((thumb(P[17]), thumb(P[1]))):
        ident = ("%016x" % (0xabc0 + i)).encode("utf-16-le")
        body = struct.pack("<III", len(ident), 0, len(data)) + struct.pack("<II", 256, 192) + b"\0" * 4 + b"\0" * 16
        entry = b"CMMM" + struct.pack("<I", 0) + b"\0" * 8 + body + ident + data
        entry = entry[:4] + struct.pack("<I", len(entry)) + entry[8:]
        entries += entry
    hdr = b"CMMM" + struct.pack("<IIII", 32, 0, 0, 24) + b"\0" * 4
    w(tc, hdr + entries)
    jid = app.start_scan("folder", {"path": os.path.dirname(tc), "role": "search", "deep": False})
    while app.jobs[jid].state == "running":
        time.sleep(0.2)
    from rewind.sources.base import Context
    ctx = Context(app.cat, app.jobs[jid], 999, "computer")
    n = computer.windows_thumbcache(ctx, tc)
    assert n == 2, n
    summ = classify.classify(app.cat)
    print("  summary:", summ)

    rows = by_name(app)

    def find(pred):
        return [r for r in rows if pred(r)]

    def cat_of(pred):
        hits = find(pred)
        assert hits, "no item matches"
        return {(h["verdict"], h["category"]) for h in hits}

    checks = [
        ("Android .trashed- file -> trash", lambda r: ".trashed-" in r["location"], {("deleted", "trash")}),
        ("Android thumbnail of deleted photo -> preview", lambda r: r["location"].endswith("1234.jpg"), {("deleted", "thumbnail")}),
        ("Android thumbnail of kept photo -> duplicate", lambda r: r["location"].endswith("9999.jpg"), {("duplicate", "duplicate")}),
        ("WhatsApp photo on the phone now -> present", lambda r: "WA0001" in r["location"], {("present", "present")}),
        ("WhatsApp viewed status -> deleted", lambda r: "abc.jpg" in r["location"], {("deleted", "recovered")}),
        ("WhatsApp missing media -> known, bytes gone", lambda r: r["name"] == "IMG-20210610-WA0002.jpg", {("missing", "missing")}),
        ("iPhone Recently Deleted (still in backup) -> trash", lambda r: r["name"] == "IMG_0016.JPG", {("deleted", "trash")}),
        ("iPhone trashed + purged -> known, bytes gone", lambda r: r["name"] == "IMG_0017.JPG", {("missing", "missing")}),
        ("iPhone preview of vanished photo -> preview", lambda r: r["name"] == "IMG_0018.JPG", {("deleted", "thumbnail")}),
        ("iPhone camera roll photo in library -> duplicate", lambda r: r["name"] == "IMG_0004.JPG" and r["source"] == "iphone-backup", {("duplicate", "duplicate")}),
        ("Gmail attachment of deleted photo -> recovered", lambda r: r["name"] == "beach.jpg", {("deleted", "recovered")}),
        ("Takeout photo -> present", lambda r: r["name"] == "IMG_0005.jpg" and r["source"] == "google-takeout", {("present", "present")}),
        ("Telegram export photo not in library -> recovered", lambda r: r["source"] == "telegram", {("deleted", "recovered")}),
        ("Instagram post not in library -> recovered", lambda r: r["name"] == "post1.jpg", {("deleted", "recovered")}),
        ("Instagram recently deleted -> trash", lambda r: r["name"] == "gone.jpg", {("deleted", "trash")}),
        ("MMS picture -> recovered", lambda r: r["source"] == "mms", {("deleted", "recovered")}),
        ("Old Photos library in Time Machine: deleted since -> recovered", lambda r: r["name"] == "IMG_0024.jpeg", {("deleted", "recovered")}),
        ("Old Photos library in Time Machine: still have -> duplicate", lambda r: r["name"] == "IMG_0001.jpeg", {("duplicate", "duplicate")}),
        ("Time Machine hard-linked copy counted once", lambda r: r["name"] == "old.jpg", {("deleted", "recovered")}),
        ("Email attachment in Sent mail -> recovered", lambda r: r["name"] == "trip.jpg", {("deleted", "recovered")}),
        ("Snapchat memory still in library -> duplicate", lambda r: r["source"] == "snapchat", {("duplicate", "duplicate")}),
    ]
    ok = True
    for label, pred, expect in checks:
        try:
            got = cat_of(pred)
        except AssertionError as e:
            got = str(e)
        good = got == expect
        ok &= good
        print("  %s %s%s" % ("PASS" if good else "FAIL", label, "" if good else "  got %s" % got))

    carved = find(lambda r: r["source"] == "carve")
    full = [r for r in carved if r["category"] == "recovered" or r["category"] == "trash"]
    print("  SD card carve: %d item(s): %s" % (len(carved), sorted({(r['category'], r['damaged']) for r in carved})))
    good = len(carved) >= 3 and any(r["damaged"] for r in carved)
    print("  %s SD card: full photo, PNG, video recovered, overwritten photo flagged damaged" % ("PASS" if good else "FAIL"))
    ok &= good
    wa_thumbs = find(lambda r: "WhatsApp database" in r["location"])
    good = len(wa_thumbs) >= 2
    print("  %s WhatsApp DB: thumbnails recovered incl. a deleted row (%d)" % ("PASS" if good else "FAIL", len(wa_thumbs)))
    ok &= good
    tcs = find(lambda r: "thumbnail cache" in r["location"])
    good = {r["verdict"] for r in tcs} == {"deleted", "duplicate"}
    print("  %s Windows thumbcache: deleted preview kept, kept-photo preview marked duplicate" % ("PASS" if good else "FAIL"))
    ok &= good

    good = len(find(lambda r: r["name"] == "old.jpg")) == 1
    print("  %s Time Machine: one item for two hard-linked snapshots" % ("PASS" if good else "FAIL"))
    ok &= good
    from rewind.core.export import ask_people_text
    ask = ask_people_text(app.cat)
    good = "Family" in ask and "send them again" in ask
    print("  %s Ask-people list names the chat (Family) with a ready message" % ("PASS" if good else "FAIL"))
    ok &= good

    res = __import__("rewind.core.export", fromlist=["export"]).export(app.cat, os.path.join(TMP, "out"))
    print("  export:", res)
    tree = []
    for root, _d, files in os.walk(res["folder"]):
        for f in files:
            tree.append(os.path.relpath(os.path.join(root, f), res["folder"]))
    print("   ", "\n    ".join(sorted(tree)[:40]))
    good = res["copied"] > 5 and os.path.exists(os.path.join(res["folder"], "report.csv"))
    print("  %s export folder written" % ("PASS" if good else "FAIL"))
    ok &= good
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
