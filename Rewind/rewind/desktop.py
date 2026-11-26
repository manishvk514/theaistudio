"""Rewind as a desktop app: the same screens in Rewind's own window.

Uses the system's web engine (WebKit on Mac) through pywebview. If that is
not available, Rewind opens in the default browser instead.
"""
import threading
import time
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer

from . import server


def _wait(url, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "health", timeout=1):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def run(workspace, port=8777):
    app = server.App(workspace, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(app))
    app.servers.append(httpd)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    _wait(url)
    try:
        import webview  # pywebview
        try:  # show "Rewind" (not "Python") in the menu bar
            from Foundation import NSBundle
            info = NSBundle.mainBundle().infoDictionary()
            info["CFBundleName"] = "Rewind"
        except Exception:
            pass
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["ALLOW_DOWNLOADS"] = True
        win = webview.create_window("Rewind", url, width=1320, height=880, min_size=(900, 620),
                              background_color="#0d0f12", text_select=True)
        app.on_quit = lambda: win.destroy()
        webview.start()
        app.shutdown()
    except Exception as e:  # no native window available: use the browser
        print("Native window unavailable (%s); opening in your browser." % e, flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
