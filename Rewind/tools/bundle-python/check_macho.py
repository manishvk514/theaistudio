"""Offline check of a Mac bundle: every library each binary loads must exist in the bundle or the OS."""
import os, struct, sys

LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_RPATH, LC_REEXPORT_DYLIB, LC_ID_DYLIB = 0xC, 0x80000018, 0x8000001C, 0x8000001F, 0xD
MH64 = 0xFEEDFACF
FAT = 0xCAFEBABE


def slices(data):
    magic = struct.unpack(">I", data[:4])[0]
    if magic == FAT:
        n = struct.unpack(">I", data[4:8])[0]
        for i in range(n):
            cputype, _sub, off, size, _al = struct.unpack(">iiIII", data[8 + 20 * i:28 + 20 * i])
            yield cputype, data[off:off + size]
    elif struct.unpack("<I", data[:4])[0] == MH64:
        yield struct.unpack("<i", data[4:8])[0], data


def cmds(m):
    ncmds = struct.unpack("<I", m[16:20])[0]
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack("<II", m[off:off + 8])
        body = m[off:off + size]
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_RPATH):
            stroff = struct.unpack("<I", body[8:12])[0]
            s = body[stroff:].split(b"\0")[0].decode()
            yield cmd, s
        off += size


def main(root):
    bad, n = [], 0
    for d, _s, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            with open(p, "rb") as fh:
                head = fh.read(4)
            if head not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                continue
            data = open(p, "rb").read()
            for cpu, m in slices(data):
                if cpu != 0x0100000C:  # arm64 only
                    continue
                n += 1
                rpaths, deps = [], []
                for c, s in cmds(m):
                    (rpaths if c == LC_RPATH else deps).append((c, s))
                rp = [s.replace("@loader_path", d).replace("@executable_path", os.path.join(root, "bin")) for _c, s in rpaths]
                for c, dep in deps:
                    if dep.startswith(("/usr/lib/", "/System/")):
                        continue
                    if dep.startswith("@loader_path"):
                        cands = [dep.replace("@loader_path", d)]
                    elif dep.startswith("@rpath"):
                        cands = [dep.replace("@rpath", r) for r in rp]
                    elif dep.startswith("@executable_path"):
                        cands = [dep.replace("@executable_path", os.path.join(root, "bin"))]
                    else:
                        cands = [dep]
                    if not any(os.path.exists(os.path.normpath(x)) for x in cands) and c != LC_LOAD_WEAK_DYLIB:
                        bad.append((os.path.relpath(p, root), dep))
    print("arm64 binaries checked:", n)
    for b in bad[:40]:
        print("UNRESOLVED", b)
    print("unresolved:", len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
