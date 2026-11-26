"""The catalog: one SQLite file that remembers every item every scan found."""
import hashlib
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY, source TEXT, label TEXT, params TEXT,
  started REAL, finished REAL, state TEXT, found INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER,
  source TEXT,          -- e.g. 'iphone-backup', 'whatsapp', 'carve'
  location TEXT,        -- human readable "where it was found"
  path TEXT,            -- where the bytes are on this computer now (NULL = missing)
  name TEXT,            -- best original file name
  hint TEXT,            -- present|trash|cache|thumbnail|carved|backup|export|version|missing|remote-trash
  kind TEXT, ext TEXT, size INTEGER,
  sha256 TEXT, md5 TEXT, sha1 TEXT, dhash TEXT,
  width INTEGER, height INTEGER,
  taken TEXT, deleted_at TEXT,
  evidence TEXT,        -- why we believe what we believe
  remote TEXT,          -- JSON: how to restore inside the service (Drive/OneDrive)
  verdict TEXT,         -- present|deleted|duplicate|missing
  category TEXT,        -- trash|recovered|thumbnail|missing|present|duplicate
  group_id INTEGER, is_primary INTEGER DEFAULT 1, damaged INTEGER DEFAULT 0,
  restored_to TEXT
);
CREATE INDEX IF NOT EXISTS i_sha ON items(sha256);
CREATE INDEX IF NOT EXISTS i_md5 ON items(md5);
CREATE INDEX IF NOT EXISTS i_hint ON items(hint);
CREATE INDEX IF NOT EXISTS i_verdict ON items(verdict, category);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

_local = threading.local()
_write_lock = threading.Lock()


class Catalog:
    def __init__(self, workspace):
        self.workspace = workspace
        os.makedirs(workspace, exist_ok=True)
        self.path = os.path.join(workspace, "catalog.db")
        con = self.con()
        con.executescript(SCHEMA)
        con.commit()

    def con(self):
        cache = getattr(_local, "cons", None)
        if cache is None:
            cache = _local.cons = {}
        c = cache.get(self.path)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            cache[self.path] = c
        return c

    def dir(self, *parts):
        p = os.path.join(self.workspace, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    # ------------------------------------------------------------ scans
    def new_scan(self, source, label, params):
        with _write_lock:
            c = self.con()
            safe = {k: v for k, v in (params or {}).items()
                    if "password" not in k and "secret" not in k and "key" not in k}
            cur = c.execute("INSERT INTO scans(source,label,params,started,state) "
                            "VALUES (?,?,?,?,?)",
                            (source, label, json.dumps(safe), time.time(), "running"))
            c.commit()
            return cur.lastrowid

    def finish_scan(self, scan_id, state, found):
        with _write_lock:
            c = self.con()
            c.execute("UPDATE scans SET finished=?, state=?, found=? WHERE id=?",
                      (time.time(), state, found, scan_id))
            c.commit()

    # ------------------------------------------------------------ items
    def add(self, item):
        cols = ["scan_id", "source", "location", "path", "name", "hint", "kind", "ext",
                "size", "sha256", "md5", "sha1", "dhash", "width", "height", "taken",
                "deleted_at", "evidence", "remote", "damaged"]
        vals = []
        for k in cols:
            v = item.get(k)
            if k == "remote" and v is not None and not isinstance(v, str):
                v = json.dumps(v)
            if k == "dhash" and v is not None:
                v = "%016x" % v if isinstance(v, int) else v
            vals.append(v)
        with _write_lock:
            c = self.con()
            cur = c.execute("INSERT INTO items(%s) VALUES (%s)" % (
                ",".join(cols), ",".join("?" * len(cols))), vals)
            c.commit()
            return cur.lastrowid

    def known_path(self, path):
        r = self.con().execute("SELECT id FROM items WHERE path=? LIMIT 1", (path,)).fetchone()
        return r is not None

    def get(self, item_id):
        r = self.con().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None

    def kv_get(self, k, default=None):
        r = self.con().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def kv_set(self, k, v):
        with _write_lock:
            c = self.con()
            c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES (?,?)", (k, json.dumps(v)))
            c.commit()


def file_hashes(path):
    h256, hmd5, hsha1 = hashlib.sha256(), hashlib.md5(), hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 << 20)
            if not b:
                break
            h256.update(b)
            hmd5.update(b)
            hsha1.update(b)
    return h256.hexdigest(), hmd5.hexdigest(), hsha1.hexdigest()
