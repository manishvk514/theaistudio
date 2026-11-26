#!/data/data/com.termux/files/usr/bin/bash
# Installs Rewind on an Android phone inside Termux, then starts it.
# Run inside Termux:  bash install-android.sh
set -e
echo "Installing Rewind on this phone (a few minutes the first time)..."
pkg update -y
pkg install -y python ffmpeg
pkg install -y python-pillow || pip install pillow || echo "(Pillow not installed; Rewind still works)"
pip install segno >/dev/null 2>&1 || true
echo "Android will now ask to let Termux see your photos and files. Tap Allow."
termux-setup-storage || true
sleep 3
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/rewind-app"
mkdir -p "$DEST"
cp -r "$SRC"/rewind "$SRC"/requirements.txt "$DEST"/
mkdir -p "$HOME/.shortcuts"
printf '#!/data/data/com.termux/files/usr/bin/bash\ncd ~/rewind-app && python -m rewind --phone\n' > "$HOME/.shortcuts/Rewind"
chmod +x "$HOME/.shortcuts/Rewind"
grep -q "alias rewind=" "$HOME/.bashrc" 2>/dev/null || echo 'alias rewind="cd ~/rewind-app && python -m rewind --phone"' >> "$HOME/.bashrc"
echo
echo "Done. Next time just open Termux and type:  rewind"
echo "Starting Rewind now; it opens in your browser."
cd "$DEST" && python -m rewind --phone
