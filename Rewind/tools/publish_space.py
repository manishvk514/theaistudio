"""Put Rewind on Hugging Face as a Space.

  python3 tools/publish_space.py --stage DIR          only assemble the Space folder
  HF_TOKEN=hf_... python3 tools/publish_space.py USER/rewind

Creates the Space if needed (Docker SDK, sign-in with Hugging Face switched on)
and uploads the app. The Space is public so claude.ai can reach /mcp, but every
page, API and the Claude endpoint require signing in as the Space's owner.
Needs `pip install huggingface_hub` and a token with write access
(https://huggingface.co/settings/tokens).
"""
import argparse
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", "assets"}          # assets = Android/Termux installers, not used in the cloud
SKIP_FILES = {".DS_Store"}


def stage(dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(os.path.join(ROOT, "space")):
        shutil.copy2(os.path.join(ROOT, "space", name), dest)
    src = os.path.join(ROOT, "rewind")
    for d, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        rel = os.path.relpath(d, src)
        os.makedirs(os.path.join(dest, "rewind", rel), exist_ok=True)
        for f in files:
            if f not in SKIP_FILES and not f.endswith(".pyc"):
                shutil.copy2(os.path.join(d, f), os.path.join(dest, "rewind", rel, f))
    return dest


def publish(repo_id, token, folder, private=False):
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=private, exist_ok=True)
    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=folder,
                      commit_message="Rewind cloud: accounts, uploads, Claude connector",
                      delete_patterns=["rewind/**"])
    return "https://huggingface.co/spaces/" + repo_id


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id", nargs="?")
    ap.add_argument("--stage", help="only assemble the Space folder here")
    ap.add_argument("--private", action="store_true",
                    help="private Space (then claude.ai cannot reach it; Claude Code with an HF token still can)")
    a = ap.parse_args(argv)
    if a.stage:
        print(stage(a.stage))
        return 0
    if not a.repo_id:
        ap.error("give USER/SPACE_NAME, or --stage DIR")
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("Set HF_TOKEN to a Hugging Face token with write access.")
    folder = stage(os.path.join(tempfile.mkdtemp(), "space"))
    url = publish(a.repo_id, token, folder, a.private)
    host = a.repo_id.replace("/", "-").replace("_", "-").replace(".", "-").lower() + ".hf.space"
    print("Space: %s\nApp:   https://%s\nClaude connector URL: https://%s/mcp" % (url, host, host))
    return 0


if __name__ == "__main__":
    sys.exit(main())
