"""Generate real media files for tests (needs Pillow; ffmpeg for videos)."""
from __future__ import annotations

import os
import random
import shutil
import subprocess


def make_media(outdir: str, count: int = 3, seed: int = 7) -> dict:
    """Returns {filename: bytes}. Files are noisy so they span many clusters."""
    from PIL import Image
    os.makedirs(outdir, exist_ok=True)
    rnd = random.Random(seed)
    files = {}

    def noise(w, h):
        # a picture with real large-scale structure (like a photo) plus grain, so
        # files are big and look-alike matching has something to recognise
        grain = Image.frombytes("RGB", (w, h), rnd.randbytes(w * h * 3)).resize((w * 2, h * 2))
        scene = Image.frombytes("RGB", (8, 6), rnd.randbytes(8 * 6 * 3)).resize((w * 2, h * 2), Image.BICUBIC)
        return Image.blend(scene, grain, 0.4)

    for i in range(count):
        img = noise(320 + 40 * i, 240 + 20 * i)
        exif = Image.Exif()
        exif[0x010F] = "TestCam"
        exif[0x0110] = f"Model {i}"
        exif[0x0132] = f"202{i}:0{i + 1}:15 10:20:30"
        p = os.path.join(outdir, f"photo_{i}.jpg")
        img.save(p, "JPEG", quality=92, exif=exif)
        p = os.path.join(outdir, f"shot_{i}.png")
        noise(120 + 10 * i, 90).save(p, "PNG")
    noise(200, 150).save(os.path.join(outdir, "pic.webp"), "WEBP", quality=90)
    noise(64, 64).save(os.path.join(outdir, "bitmap.bmp"), "BMP")
    frames = [noise(40, 30).convert("P") for _ in range(4)]
    frames[0].save(os.path.join(outdir, "anim.gif"), save_all=True, append_images=frames[1:], duration=80)
    if shutil.which("ffmpeg"):
        for name, extra in (("clip.mp4", ["-c:v", "mpeg4"]), ("clip.mov", ["-c:v", "mpeg4"]),
                            ("clip.mkv", ["-c:v", "mpeg4"]), ("clip.avi", ["-c:v", "mpeg4"]),
                            ("faststart.mp4", ["-c:v", "mpeg4", "-movflags", "+faststart"])):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=320x240:rate=15:duration=2", *extra,
                            "-metadata", "creation_time=2021-05-04T03:02:01Z",
                            os.path.join(outdir, name)], check=True)
    for n in sorted(os.listdir(outdir)):
        with open(os.path.join(outdir, n), "rb") as f:
            files[n] = f.read()
    return files
