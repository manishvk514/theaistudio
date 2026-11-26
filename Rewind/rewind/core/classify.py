"""Decide, for every item found, whether it is deleted or still present.

Rule from first principles: "deleted" is not a property of a file, it is a
relationship between a copy and your current library. A picture is deleted
if a copy of it survives somewhere (trash, cache, backup, card, email) but
no copy of it is in the library any more. So:

1. Build the library = every item marked 'present' (your phone/computer/
   cloud as they are now).
2. For every other copy, look for the same picture in the library:
   exact bytes (SHA-256 / MD5 / SHA-1), or the same image at another size or
   quality (perceptual dHash within a few bits, e.g. a thumbnail of it).
3. Match found  -> 'duplicate' (you still have it; hidden by default).
   No match     -> 'deleted'.
4. Group the deleted copies of the same picture together and keep the best
   one (largest, undamaged) as the one to restore.
"""
from collections import defaultdict

from .imaging import hamming

BANDS = 8              # 8 bands x 8 bits; any two hashes within 7 bits share a band
SAME_PICTURE = 6       # max differing bits to call two pictures the same
THUMB_MAX_EDGE = 640


def _bands(h):
    return [(i, (h >> (8 * i)) & 0xFF) for i in range(BANDS)]


class PIndex:
    def __init__(self):
        self.buckets = defaultdict(list)

    def add(self, h, ref):
        for b in _bands(h):
            self.buckets[b].append((h, ref))

    def near(self, h, maxd=SAME_PICTURE, ok=None):
        best = None
        seen = set()
        for b in _bands(h):
            for hh, ref in self.buckets.get(b, ()):
                if ref in seen:
                    continue
                seen.add(ref)
                d = hamming(h, hh)
                if d <= maxd and (best is None or d < best[0]) and (ok is None or ok(ref)):
                    best = (d, ref)
        return best


def compatible(a, b):
    """May two different files be treated as the same picture by look alone?

    Only when one is a resized/re-compressed version of the other. Two
    full-size files with identical dimensions but different bytes are usually
    different shots (bursts, near-identical poses), so we never merge those:
    showing an extra item is safer than hiding a deleted photo.
    """
    aw, ah, bw, bh = a.get("width"), a.get("height"), b.get("width"), b.get("height")
    if aw and ah and bw and bh:
        if (aw, ah) == (bw, bh) or (aw, ah) == (bh, bw):
            return False
        ra, rb = aw / ah, bw / bh
        if abs(ra - rb) / max(ra, rb) > 0.04 and abs(ra - 1 / rb) / max(ra, 1 / rb) > 0.04:
            return False
        return True
    # dimensions unknown: allow only if sizes clearly differ (one is a preview)
    sa, sb = a.get("size") or 0, b.get("size") or 0
    return bool(sa and sb and max(sa, sb) > 2.5 * min(sa, sb))


def _quality(r):
    if r["hint"] == "missing" or not r["path"]:
        return "missing"
    edge = max(r["width"] or 0, r["height"] or 0)
    if r["hint"] == "thumbnail" or (r["kind"] == "image" and 0 < edge < THUMB_MAX_EDGE) or (
            r["kind"] == "image" and not edge and (r["size"] or 0) < 60_000 and r["hint"] in ("cache", "carved")):
        return "thumbnail"
    return "full"


def classify(cat):
    con = cat.con()
    rows = [dict(r) for r in con.execute("SELECT * FROM items")]
    byid = {r["id"]: r for r in rows}
    lib_hash = {}
    lib_p = PIndex()
    for r in rows:
        if r["hint"] != "present":
            continue
        for k in ("sha256", "md5", "sha1"):
            if r[k]:
                lib_hash[(k, r[k])] = r["id"]
        if r["dhash"] and r["kind"] == "image":
            lib_p.add(int(r["dhash"], 16), r["id"])
    have_library = any(r["hint"] == "present" for r in rows)

    updates = []
    deleted = []
    for r in rows:
        if r["hint"] == "present":
            updates.append(("present", "present", None, 1, r["id"]))
            continue
        match = None
        for k in ("sha256", "md5", "sha1"):
            if r[k] and (k, r[k]) in lib_hash:
                match = ("same file as one in your library", lib_hash[(k, r[k])])
                break
        if not match and r["dhash"] and r["kind"] == "image":
            near = lib_p.near(int(r["dhash"], 16), ok=lambda ref, r=r: compatible(r, byid[ref]))
            if near:
                match = ("same picture as one in your library (another size/quality)", near[1])
        if match:
            updates.append(("duplicate", "duplicate", match[1], 0, r["id"]))
            continue
        q = _quality(r)
        if r["hint"] == "missing":
            cat_ = "missing"
        elif r["hint"] in ("trash", "remote-trash"):
            cat_ = "trash" if q != "thumbnail" else "thumbnail"
        elif q == "thumbnail":
            cat_ = "thumbnail"
        else:
            cat_ = "recovered"
        r["_cat"] = cat_
        r["_q"] = q
        deleted.append(r)

    # group deleted copies of the same picture
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_hash = {}
    p = PIndex()
    for r in deleted:
        parent.setdefault(r["id"], r["id"])
        if r["sha256"]:
            if r["sha256"] in by_hash:
                union(by_hash[r["sha256"]], r["id"])
            else:
                by_hash[r["sha256"]] = r["id"]
        if r["dhash"] and r["kind"] == "image":
            h = int(r["dhash"], 16)
            near = p.near(h, ok=lambda ref, r=r: compatible(r, byid[ref]))
            if near:
                union(near[1], r["id"])
            p.add(h, r["id"])
    groups = defaultdict(list)
    for r in deleted:
        groups[find(r["id"])].append(r)
    rank = {"trash": 0, "recovered": 1, "thumbnail": 2, "missing": 3}
    for gid, members in groups.items():
        def score(m):
            return (m["_q"] == "missing", bool(m["damaged"]), m["_q"] == "thumbnail",
                    -((m["width"] or 0) * (m["height"] or 0)), -(m["size"] or 0))
        members.sort(key=score)
        best = members[0]
        # the whole group's category: best available copy, but "in trash" wins if any copy is in a trash
        cat_ = best["_cat"]
        if any(m["_cat"] == "trash" for m in members) and best["_q"] == "full":
            cat_ = "trash"
        for m in members:
            primary = 1 if m is best else 0
            updates.append(("deleted" if m["_cat"] != "missing" else "missing",
                            cat_ if primary else m["_cat"], gid, primary, m["id"]))
    with con:
        con.executemany("UPDATE items SET verdict=?, category=?, group_id=?, is_primary=? WHERE id=?", updates)
    return summary(cat, have_library)


def summary(cat, have_library=None):
    con = cat.con()
    out = {r[0] or "unclassified": r[1] for r in con.execute(
        "SELECT category, COUNT(*) FROM items WHERE is_primary=1 GROUP BY category")}
    if have_library is None:
        have_library = con.execute("SELECT 1 FROM items WHERE hint='present' LIMIT 1").fetchone() is not None
    out["have_library"] = bool(have_library)
    out["total_items"] = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    for v in ("deleted", "missing"):
        out["n_" + v] = con.execute("SELECT COUNT(*) FROM items WHERE verdict=? AND is_primary=1",
                                    (v,)).fetchone()[0]
    out["n_present"] = con.execute("SELECT COUNT(*) FROM items WHERE verdict='present'").fetchone()[0]
    out["n_restored"] = con.execute("SELECT COUNT(*) FROM items WHERE restored_to IS NOT NULL").fetchone()[0]
    return out
