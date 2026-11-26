"""Copy recovered items out to a clean, dated folder with a report.

Nothing is ever written back to the place it was found (no phone, card or
account is changed by exporting). Restoring inside a cloud service (Drive
Trash -> Drive) is a separate, explicit button.
"""
import csv
import datetime as dt
import os
import re
import shutil
import time

FOLDERS = {
    "trash": "1 - Was in a trash bin",
    "recovered": "2 - Recovered deleted files",
    "thumbnail": "3 - Only a preview survived",
}


def _safe(s, n=80):
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", s or "").strip(" .")
    return s[:n] or "file"


def export(cat, dest_root, ids=None, include_secondary=False):
    con = cat.con()
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H.%M")
    dest = os.path.join(os.path.expanduser(dest_root), "Restored %s" % stamp)
    os.makedirs(dest, exist_ok=True)
    if ids:
        q = "SELECT * FROM items WHERE id IN (%s)" % ",".join("?" * len(ids))
        rows = [dict(r) for r in con.execute(q, ids)]
    else:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM items WHERE verdict='deleted'" + ("" if include_secondary else " AND is_primary=1"))]
    report = []
    copied = 0
    for r in rows:
        if not r["path"] or not os.path.exists(r["path"]):
            continue
        folder = FOLDERS.get(r["category"], "2 - Recovered deleted files")
        when = r["taken"] or r["deleted_at"] or ""
        sub = when[:7] if re.match(r"\d{4}-\d{2}", when) else "Unknown date"
        src_label = _safe(r["source"], 30)
        out_dir = os.path.join(dest, folder, src_label, sub)
        os.makedirs(out_dir, exist_ok=True)
        base, ext = os.path.splitext(_safe(r["name"] or "file"))
        if ext.lower() not in (r["ext"] or "", ".jpeg") and r["ext"]:
            ext = r["ext"]
        target = os.path.join(out_dir, base + ext)
        k = 1
        while os.path.exists(target):
            k += 1
            target = os.path.join(out_dir, "%s (%d)%s" % (base, k, ext))
        shutil.copy2(r["path"], target)
        if re.match(r"\d{4}-\d{2}-\d{2}", when):
            try:
                t = time.mktime(dt.datetime.fromisoformat(when[:19]).timetuple())
                os.utime(target, (t, t))
            except Exception:
                pass
        copied += 1
        with cat.con() as c:
            c.execute("UPDATE items SET restored_to=? WHERE id=?", (target, r["id"]))
        report.append([os.path.relpath(target, dest), r["category"], r["source"], r["location"],
                       r["taken"] or "", r["deleted_at"] or "", r["width"] or "", r["height"] or "",
                       r["size"] or "", "yes" if r["damaged"] else "", r["evidence"] or "", r["sha256"] or ""])
    with open(os.path.join(dest, "report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "category", "source", "found at", "taken", "deleted", "width", "height", "bytes",
                    "damaged", "why we think so", "sha256"])
        w.writerows(report)
    missing = [dict(r) for r in con.execute(
        "SELECT * FROM items WHERE verdict='missing' AND is_primary=1 ORDER BY location")]
    if missing:
        with open(os.path.join(dest, "known deleted - where to ask.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "where it was", "taken", "deleted", "what you can do"])
            for m in missing:
                w.writerow([m["name"], m["location"], m["taken"] or "", m["deleted_at"] or "", m["evidence"]])
    ask = ask_people_text(cat)
    if ask:
        with open(os.path.join(dest, "ask people to resend.txt"), "w", encoding="utf-8") as f:
            f.write(ask)
    with open(os.path.join(dest, "READ ME.txt"), "w", encoding="utf-8") as f:
        f.write("Restored by Rewind on %s\n\n"
                "1 - Was in a trash bin: full originals that were waiting in a trash/Recently Deleted.\n"
                "2 - Recovered deleted files: full files rebuilt from backups, caches, cards or exports.\n"
                "3 - Only a preview survived: small thumbnails; the original is gone.\n\n"
                "report.csv says where each file was found and why it counts as deleted.\n"
                "'known deleted - where to ask.csv' lists pictures we know existed but whose bytes are gone;\n"
                "for chats, the other person usually still has them.\n" % stamp)
    return {"folder": dest, "copied": copied, "missing_listed": len(missing)}


def ask_people_text(cat):
    """Group lost chat media by chat and write a message the user can send.

    First principle: a chat photo always had at least two copies, yours and
    the other person's (every member's, in a group). Yours is gone; theirs
    usually is not.
    """
    con = cat.con()
    rows = con.execute("SELECT location, name, taken, kind FROM items WHERE verdict='missing' "
                       "AND location LIKE '%chat%' ORDER BY location, taken").fetchall()
    by_chat = {}
    for loc, name, taken, kind in rows:
        chat = loc.split(":", 1)[-1].strip() if ":" in loc else loc
        by_chat.setdefault(chat, []).append((name, taken, kind))
    if not by_chat:
        return ""
    out = ["Photos and videos your chats still list, but your phone lost.",
           "The other people in these chats most likely still have them.", ""]
    for chat, items in by_chat.items():
        photos = sum(1 for i in items if i[2] != "video")
        videos = len(items) - photos
        dates = sorted({(t or "")[:10] for _n, t, _k in items if t})
        span = (" from %s to %s" % (dates[0], dates[-1])) if len(dates) > 1 else (
            (" from %s" % dates[0]) if dates else "")
        what = ", ".join(x for x in ("%d photo%s" % (photos, "" if photos == 1 else "s") if photos else "",
                                     "%d video%s" % (videos, "" if videos == 1 else "s") if videos else "") if x)
        out.append("=== %s (%s%s) ===" % (chat, what, span))
        out.append("Message you could send:")
        out.append("  Hey, I lost some photos and videos from our chat%s. If you still have them, could you "
                   "send them again? Thank you!" % span)
        out.append("Files: " + ", ".join(n for n, _t, _k in items[:30]) + (" ..." if len(items) > 30 else ""))
        out.append("")
    return "\n".join(out)
