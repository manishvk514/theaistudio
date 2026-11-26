import bz2, json, re, sys

def load(f, sub):
    d = json.loads(bz2.decompress(open(f, "rb").read()))
    out = []
    for key in ("packages", "packages.conda"):
        for fn, m in d.get(key, {}).items():
            m = dict(m); m["fn"] = fn; m["subdir"] = sub
            out.append(m)
    return out

PK = load("osx.json.bz2", "osx-arm64") + load("noarch.json.bz2", "noarch")
BY = {}
for m in PK:
    BY.setdefault(m["name"], []).append(m)

def vkey(v):
    parts = re.split(r"[._-]", v.lower())
    out = []
    for p in parts:
        for t in re.findall(r"\d+|[a-z]+", p):
            out.append((1, int(t)) if t.isdigit() else (0, t))
    return out

def cmp(a, b):
    ka, kb = vkey(a), vkey(b)
    n = max(len(ka), len(kb))
    ka += [(1, 0)] * (n - len(ka)); kb += [(1, 0)] * (n - len(kb))
    return (ka > kb) - (ka < kb)

def ok_one(v, c):
    c = c.strip()
    if not c or c == "*":
        return True
    for op in (">=", "<=", "!=", "==", ">", "<"):
        if c.startswith(op):
            t = c[len(op):]
            if t.endswith(".*"):
                t = t[:-2]
            r = cmp(v, t)
            return {">=": r >= 0, "<=": r <= 0, "!=": r != 0, "==": r == 0, ">": r > 0, "<": r < 0}[op]
    if c.endswith(".*"):
        return v == c[:-2] or v.startswith(c[:-2] + ".")
    if "*" in c:
        return re.fullmatch(c.replace(".", r"\.").replace("*", ".*"), v) is not None
    return v == c or v.startswith(c + ".")

def ok(v, spec):
    return any(all(ok_one(v, c) for c in alt.split(",")) for alt in spec.split("|"))

def pick(dep):
    parts = dep.split()
    name = parts[0]
    vspec = parts[1] if len(parts) > 1 else ""
    bspec = parts[2] if len(parts) > 2 else None
    cands = [m for m in BY.get(name, []) if (not vspec or ok(m["version"], vspec))
             and (bspec is None or re.fullmatch(bspec.replace("*", ".*"), m["build"]))]
    cands.sort(key=lambda m: (vkey(m["version"]), m.get("build_number", 0), m.get("timestamp", 0)))
    return cands[-1] if cands else None

def resolve(root):
    chosen, todo = {}, [root]
    while todo:
        dep = todo.pop(0)
        name = dep.split()[0]
        if name.startswith("__") or name in ("python_abi",):
            continue
        if name in chosen:
            m = chosen[name]
            parts = dep.split()
            if len(parts) > 1 and not ok(m["version"], parts[1]):
                print("CONFLICT", dep, m["version"], file=sys.stderr)
            continue
        m = pick(dep)
        if not m:
            print("MISSING", dep, file=sys.stderr)
            continue
        chosen[name] = m
        todo += m.get("depends", [])
    return chosen

ch = resolve("python 3.12.*")
# python_abi pin
for name, m in sorted(ch.items()):
    print(m["subdir"], m["fn"])
json.dump([[m["subdir"], m["fn"]] for m in ch.values()], open("pkgs.json", "w"))
