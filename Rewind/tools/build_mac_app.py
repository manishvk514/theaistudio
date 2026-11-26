"""Build Rewind.app, a double-click Mac app around the Rewind code.

Run from the project folder:  python3 tools/build_mac_app.py
Produces ./Rewind.app (unsigned; first open needs right-click > Open, or
System Settings > Privacy & Security > Open Anyway on newer macOS).
"""
import os
import plistlib
import shutil
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "Rewind.app")

LAUNCHER = open(os.path.join(ROOT, "tools", "mac-launcher", "launcher.sh")).read()


def build(python_dir=None):
    if os.path.exists(APP):
        shutil.rmtree(APP)
    macos = os.path.join(APP, "Contents", "MacOS")
    res = os.path.join(APP, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(os.path.join(res, "app"))
    with open(os.path.join(APP, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleName": "Rewind", "CFBundleDisplayName": "Rewind",
            "CFBundleIdentifier": "app.rewind.restore", "CFBundleVersion": "5.0",
            "CFBundleShortVersionString": "5.0", "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "Rewind", "CFBundleIconFile": "Rewind.icns",
            "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Runs only on your Mac.",
            "LSArchitecturePriority": ["arm64", "x86_64"],
            "NSAppleEventsUsageDescription": "Rewind uses this to show a folder picker and to ask for your "
                                             "password before a deep scan of a memory card.",
            "NSDesktopFolderUsageDescription": "Rewind looks for photos, videos and account exports on your Desktop.",
            "NSDocumentsFolderUsageDescription": "Rewind looks for photos, videos and account exports in Documents.",
            "NSDownloadsFolderUsageDescription": "Rewind opens account exports (Google Takeout, Snapchat, "
                                                 "Instagram, WhatsApp, Telegram) you downloaded.",
            "NSRemovableVolumesUsageDescription": "Rewind searches memory cards and USB drives for deleted photos.",
            "NSNetworkVolumesUsageDescription": "Rewind searches backup drives for older copies of your photos.",
        }, f)
    # Native universal launcher (tools/mac-launcher/rewind_launcher.c) runs Resources/launcher.sh,
    # so the whole bundle can carry a proper code signature.
    exe = os.path.join(macos, "Rewind")
    shutil.copy(os.path.join(ROOT, "tools", "mac-launcher", "Rewind-universal"), exe)
    os.chmod(exe, 0o755)
    script = os.path.join(res, "launcher.sh")
    with open(script, "w") as f:
        f.write(LAUNCHER)
    os.chmod(script, 0o755)
    shutil.copy(os.path.join(ROOT, "rewind", "web", "icons", "Rewind.icns"), os.path.join(res, "Rewind.icns"))
    shutil.copytree(os.path.join(ROOT, "rewind"), os.path.join(res, "app", "rewind"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(ROOT, "requirements.txt"), os.path.join(res, "app", "requirements.txt"))
    if python_dir:
        shutil.copytree(python_dir, os.path.join(res, "python"), symlinks=True)
        print("Bundled Python from", python_dir)
    rclone = os.environ.get("REWIND_RCLONE_BIN")
    if rclone:
        os.makedirs(os.path.join(res, "bin"))
        shutil.copy(rclone, os.path.join(res, "bin", "rclone"))
        os.chmod(os.path.join(res, "bin", "rclone"), 0o755)
        print("Bundled rclone from", rclone)
    print("Built", APP)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
    sys.exit(0)
