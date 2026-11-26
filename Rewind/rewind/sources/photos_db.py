"""Read Apple's Photos.sqlite (iPhone backups and the Mac Photos library).

What it tells us, from first principles: Photos never "deletes" straight
away. A deleted picture gets ZTRASHEDSTATE=1 and a ZTRASHEDDATE, stays in
the Recently Deleted album for about 30 days, and its original file stays
in DCIM (iPhone) or originals/ (Mac) until the purge. Even after the purge,
the database row and its small preview files can outlive the original.
"""
import datetime as dt
import sqlite3

APPLE_EPOCH = dt.datetime(2001, 1, 1)


def apple_time(v):
    try:
        if v is None:
            return None
        return (APPLE_EPOCH + dt.timedelta(seconds=float(v))).isoformat(timespec="seconds")
    except Exception:
        return None


def read_assets(db_path):
    """Return list of dicts: directory, filename, trashed, trashed_at, created,
    kind ('image'|'video'), uuid, width, height."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = "ZASSET" if "ZASSET" in tables else ("ZGENERICASSET" if "ZGENERICASSET" in tables else None)
        if not table:
            return []
        cols = {r[1].upper() for r in con.execute("PRAGMA table_info(%s)" % table)}

        def col(name, alt="NULL"):
            return name if name in cols else alt

        q = ("SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s FROM %s" % (
            col("ZDIRECTORY"), col("ZFILENAME"), col("ZTRASHEDSTATE", "0"),
            col("ZTRASHEDDATE"), col("ZDATECREATED"), col("ZKIND", "0"), col("ZUUID"),
            col("ZWIDTH"), col("ZHEIGHT"), table))
        out = []
        for d, f, tr, trd, cr, kind, uuid, w, h in con.execute(q):
            if not f:
                continue
            out.append(dict(directory=d or "", filename=f, trashed=bool(tr),
                            trashed_at=apple_time(trd), created=apple_time(cr),
                            kind="video" if kind == 1 else "image", uuid=uuid,
                            width=w, height=h))
        return out
    finally:
        con.close()
