"""The case catalog: every picture/video found anywhere, and the verdict on each.

The verdict (deleted / existing / unsorted) is not guessed from where the scan
happened to look -- it is derived from evidence:

  * hard evidence of deletion: the filesystem marks the entry deleted, the bytes
    sit in unallocated space, the file is in a trash folder or a cloud bin;
  * hard evidence of existence: the file is live on the filesystem / in the
    account right now;
  * ambiguous evidence (a thumbnail cache, an old backup, bytes carved from a
    database): compared against everything known to exist, first by exact
    content (SHA-256), then by what the picture *looks like* (perceptual hash,
    which survives resizing and recompression). No match among the living means
    the original is gone -> deleted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

DELETED_ORIGINS = ("fs-deleted", "carved-free", "trash", "cloud-trash")
LIVE_ORIGINS = ("live", "cloud-live")
AMBIGUOUS_ORIGINS = ("backup", "cache", "embedded", "carved-raw")
# when the same bytes are found twice, keep the record that knows the most
PRIORITY = {o: i for i, o in enumerate(
    ("fs-deleted", "trash", "cloud-trash", "backup", "carved-free", "cache", "embedded", "carved-raw"))}

ORIGIN_LABEL = {
    "fs-deleted": "Deleted file record (filesystem)",
    "carved-free": "Rebuilt from unallocated space",
    "carved-raw": "Carved from raw bytes",
    "trash": "Trash / recycle folder",
    "cloud-trash": "Cloud trash",
    "backup": "Backup / export archive",
    "cache": "App or thumbnail cache",
    "embedded": "Embedded inside another file",
    "live": "Existing file",
    "cloud-live": "Existing in cloud account",
}

COLUMNS = ("source_id", "origin", "state", "quality", "kind", "ext", "name", "path", "size", "sha256",
           "phash", "width", "height", "taken", "modified", "deleted_at", "note", "loc", "shadow", "restored")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, label TEXT, type TEXT, path TEXT, role TEXT, added TEXT, info TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,
  source_id INT, origin TEXT, state TEXT DEFAULT 'unsorted', quality TEXT, kind TEXT, ext TEXT, name TEXT, path TEXT,
  size INT, sha256 TEXT, phash TEXT, width INT DEFAULT 0, height INT DEFAULT 0, taken TEXT DEFAULT '',
  modified TEXT DEFAULT '', deleted_at TEXT DEFAULT '', note TEXT DEFAULT '', loc TEXT, shadow INT DEFAULT 0, restored TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS items_sha ON items(sha256);
CREATE INDEX IF NOT EXISTS items_state ON items(state, shadow);
"""


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class PhashIndex:
    """Near-duplicate lookup: 64-bit hashes split in 8 bands. Any two hashes
    within 7 bits of each other share at least one band."""

    def __init__(self):
        self.bands = [dict() for _ in range(8)]

    def add(self, h: int, payload):
        for i in range(8):
            self.bands[i].setdefault((h >> (8 * i)) & 0xFF, []).append((h, payload))

    def near(self, h: int, dist: int = 6):
        best = None
        for i in range(8):
            for other, payload in self.bands[i].get((h >> (8 * i)) & 0xFF, ()):
                d = hamming(h, other)
                if d <= dist and (best is None or d < best[0]):
                    best = (d, payload)
        return best


class Catalog:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # -- sources -----------------------------------------------------------

    def add_source(self, label, type_, path, role="", info="") -> int:
        import datetime
        with self.lock:
            cur = self.db.execute("INSERT INTO sources(label,type,path,role,added,info) VALUES(?,?,?,?,?,?)",
                                  (label, type_, path, role, datetime.datetime.now().isoformat(timespec="seconds"), info))
            self.db.commit()
            return cur.lastrowid

    def sources(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM items i WHERE i.source_id=s.id) AS items FROM sources s ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def remove_source(self, sid: int):
        with self.lock:
            self.db.execute("DELETE FROM items WHERE source_id=?", (sid,))
            self.db.execute("DELETE FROM sources WHERE id=?", (sid,))
            self.db.commit()
        self.classify()

    # -- items -------------------------------------------------------------

    def add(self, **f) -> int | None:
        """Insert a finding; merges with an identical one already known."""
        f.setdefault("quality", "intact")
        if isinstance(f.get("loc"), dict):
            f["loc"] = json.dumps(f["loc"])
        if isinstance(f.get("phash"), int):
            f["phash"] = f"{f['phash']:016x}"
        live = f["origin"] in LIVE_ORIGINS
        with self.lock:
            if f.get("sha256"):
                marks = ",".join("?" * len(LIVE_ORIGINS))
                row = self.db.execute(
                    f"SELECT id, origin, name, quality, source_id FROM items WHERE sha256=? AND (origin IN ({marks}))=? LIMIT 1",
                    (f["sha256"], *LIVE_ORIGINS, int(live))).fetchone()
                if row:
                    better = (not live and row["source_id"] == f.get("source_id") and row["quality"] != "damaged" and f["quality"] != "damaged"
                              and PRIORITY[f["origin"]] < PRIORITY[row["origin"]])
                    if better or (row["quality"] == "damaged" and f["quality"] != "damaged"):
                        sets = ",".join(f"{k}=?" for k in f)
                        self.db.execute(f"UPDATE items SET {sets} WHERE id=?", (*f.values(), row["id"]))
                    return row["id"]
            keys = [k for k in f if k in COLUMNS]
            cur = self.db.execute(f"INSERT INTO items({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                                  [f[k] for k in keys])
            return cur.lastrowid

    def commit(self):
        with self.lock:
            self.db.commit()

    def get(self, item_id: int):
        with self.lock:
            r = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None

    def mark_restored(self, item_id: int, where: str):
        with self.lock:
            self.db.execute("UPDATE items SET restored=? WHERE id=?", (where, item_id))

    def query(self, state="deleted", kind="", source=0, q="", show_shadow=False, lowres="", damaged=True,
              order="taken", offset=0, limit=200, ids_only=False):
        where, args = ["1=1"], []
        if state:
            where.append("state=?"); args.append(state)
        if kind:
            where.append("kind=?"); args.append(kind)
        if source:
            where.append("source_id=?"); args.append(source)
        if q:
            where.append("(name LIKE ? OR path LIKE ? OR note LIKE ?)"); args += [f"%{q}%"] * 3
        if not show_shadow:
            where.append("shadow=0")
        if lowres == "hide":
            where.append("NOT (kind='image' AND width>0 AND width<=640 AND height<=640)")
        elif lowres == "only":
            where.append("(kind='image' AND width>0 AND width<=640 AND height<=640)")
        if not damaged:
            where.append("quality!='damaged'")
        w = " AND ".join(where)
        col = {"taken": "COALESCE(NULLIF(taken,''), NULLIF(modified,''), deleted_at) DESC, id",
               "size": "size DESC", "name": "name COLLATE NOCASE", "found": "id"}.get(order, "id")
        with self.lock:
            if ids_only:
                return [r[0] for r in self.db.execute(f"SELECT id FROM items WHERE {w}", args)]
            total = self.db.execute(f"SELECT COUNT(*), COALESCE(SUM(size),0) FROM items WHERE {w}", args).fetchone()
            rows = self.db.execute(f"SELECT * FROM items WHERE {w} ORDER BY {col} LIMIT ? OFFSET ?",
                                   (*args, limit, offset)).fetchall()
        return {"total": total[0], "bytes": total[1], "items": [dict(r) for r in rows]}

    def counts(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT state, COUNT(*) n, COALESCE(SUM(size),0) b FROM items WHERE shadow=0 GROUP BY state").fetchall()
            hidden = self.db.execute("SELECT COUNT(*) FROM items WHERE shadow=1").fetchone()[0]
        out = {s: {"n": 0, "bytes": 0} for s in ("deleted", "existing", "unsorted")}
        for r in rows:
            out[r["state"]] = {"n": r["n"], "bytes": r["b"]}
        out["hidden_duplicates"] = hidden
        return out

    # -- the verdict -------------------------------------------------------

    def classify(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, origin, sha256, phash, width, height, quality, note FROM items").fetchall()
            live_sha = {r["sha256"] for r in rows if r["origin"] in LIVE_ORIGINS and r["sha256"]}
            have_baseline = any(r["origin"] in LIVE_ORIGINS for r in rows)
            live_ph = PhashIndex()
            for r in rows:
                if r["origin"] in LIVE_ORIGINS and r["phash"]:
                    live_ph.add(int(r["phash"], 16), r["id"])
            updates = []
            verdicts = {}
            for r in rows:
                o = r["origin"]
                why = ""
                if o in LIVE_ORIGINS:
                    state = "existing"
                elif r["sha256"] and r["sha256"] in live_sha:
                    state, why = "existing", "identical copy still exists"
                elif o in DELETED_ORIGINS:
                    state = "deleted"
                else:
                    hit = live_ph.near(int(r["phash"], 16)) if r["phash"] else None
                    if hit:
                        state, why = "existing", "looks the same as a picture that still exists"
                    elif have_baseline:
                        state, why = "deleted", "no matching file exists any more"
                    else:
                        state, why = "unsorted", "add the current library/device to tell deleted from existing"
                verdicts[r["id"]] = (state, why)
            # A thumbnail of a deleted photo is noise when the full photo was also recovered.
            best = PhashIndex()
            for r in rows:
                if verdicts[r["id"]][0] == "deleted" and r["phash"] and r["quality"] != "damaged":
                    best.add(int(r["phash"], 16), (r["width"] * r["height"], r["id"]))
            for r in rows:
                state, why = verdicts[r["id"]]
                shadow = 0
                if state == "deleted" and r["phash"]:
                    h = int(r["phash"], 16)
                    area = r["width"] * r["height"]
                    for i in range(8):
                        for other, (oarea, oid) in best.bands[i].get((h >> (8 * i)) & 0xFF, ()):
                            if oid != r["id"] and hamming(h, other) <= 6 and (
                                    oarea > area or (oarea == area and oid < r["id"] and r["origin"] in AMBIGUOUS_ORIGINS)):
                                shadow = 1
                                break
                        if shadow:
                            break
                note = r["note"] or ""
                base = note.split(" | verdict: ")[0]
                note = base + (" | verdict: " + why if why else "")
                updates.append((state, shadow, note, r["id"]))
            self.db.executemany("UPDATE items SET state=?, shadow=?, note=? WHERE id=?", updates)
            self.db.commit()
