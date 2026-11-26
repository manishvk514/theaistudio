"""Start Rewind.

  python3 -m rewind                 open the app in your browser
  python3 -m rewind --port 9000     use another port
  python3 -m rewind scan folder path=~/Pictures role=library
  python3 -m rewind export ~/Desktop
  python3 -m rewind mcp             Claude apps talk to Rewind (started by Claude itself)
  python3 -m rewind claude-setup    add Rewind to Claude Desktop and Claude Code, once
  python3 -m rewind space           the Hugging Face (cloud) server
"""
import argparse
import os
import sys
import time

from . import server
from .core import classify, export


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rewind")
    ap.add_argument("--workspace", default=os.path.join(os.path.expanduser("~"), "Rewind", "workspace"))
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--phone", action="store_true", help="running on an Android phone (Termux)")
    ap.add_argument("--app", action="store_true", help="open in Rewind's own window (desktop app)")
    ap.add_argument("cmd", nargs="?", default="serve")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "mcp":
        from . import mcp
        mcp.run_stdio(a.workspace)
        return 0
    if a.cmd == "claude-setup":
        from . import mcp
        for line in mcp.install_claude(a.workspace):
            print(line)
        return 0
    if a.cmd == "space":
        from . import space
        # /data is where a Hugging Face storage bucket is mounted; without one, data lasts until a restart
        ws = os.environ.get("REWIND_WORKSPACE") or ("/data/rewind" if os.path.isdir("/data") else a.workspace)
        space.serve(ws, int(os.environ.get("PORT") or 7860))
        return 0
    if a.cmd == "serve":
        import socket
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", a.port))
        except OSError:  # already running: just open it
            if a.no_browser:
                print("Port %d is already in use by another program." % a.port, file=sys.stderr)
                return 3
            import webbrowser
            print("Rewind is already running; opening it.")
            webbrowser.open("http://127.0.0.1:%d/" % a.port)
            return 0
        finally:
            s.close()
        if a.app:
            from . import desktop
            desktop.run(a.workspace, a.port)
            return 0
        server.serve(a.workspace, a.port, not a.no_browser, phone=a.phone)
        return 0
    app = server.App(a.workspace, a.port)
    if a.cmd == "scan":
        source = a.args[0]
        params = {}
        for kv in a.args[1:]:
            k, _, v = kv.partition("=")
            params[k] = {"true": True, "false": False}.get(v.lower(), v)
        jid = app.start_scan(source, params)
        job = app.jobs[jid]
        shown = 0
        while job.state == "running":
            time.sleep(0.5)
            for line in job.logs[shown:]:
                print(line)
            shown = len(job.logs)
            if job.needs_user:
                print(">>", job.needs_user)
        for line in job.logs[shown:]:
            print(line)
        print(classify.summary(app.cat))
        return 0 if job.state == "done" else 1
    if a.cmd == "summary":
        print(classify.classify(app.cat))
        return 0
    if a.cmd == "export":
        print(export.export(app.cat, a.args[0] if a.args else os.path.expanduser("~/Desktop")))
        return 0
    ap.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
