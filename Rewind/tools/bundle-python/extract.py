"""Unpack .conda packages into a prefix folder, keeping file modes and symlinks."""
import io, json, os, sys, tarfile, zipfile
import zstandard

PREFIX = sys.argv[1]
os.makedirs(PREFIX, exist_ok=True)
for fn in sorted(os.listdir("conda_dl")):
    path = os.path.join("conda_dl", fn)
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.startswith(("pkg-", "info-")) or not name.endswith(".tar.zst"):
                continue
            raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(name)))
            data = raw.read()
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                if name.startswith("pkg-"):
                    t.extractall(PREFIX, filter="tar")
                else:
                    info = os.path.join(PREFIX, "conda-meta-info", fn)
                    os.makedirs(info, exist_ok=True)
                    t.extractall(info, filter="tar")
    print("unpacked", fn)
