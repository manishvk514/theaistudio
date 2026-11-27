#!/bin/bash
# Exercises the Mac launcher's logic on any Unix: fresh start, reuse, stale engine, busy port, crash.
# macOS-only commands (open, osascript) are replaced by recorders through the launcher's test hooks.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d /tmp/rewind_launch_XXXX)"
export HOME="$T/home"; mkdir -p "$HOME"
RES="$T/Rewind.app/Contents/Resources"; mkdir -p "$RES/app"
cp -R "$ROOT/rewind" "$RES/app/"
cp "$ROOT/tools/mac-launcher/launcher.sh" "$RES/launcher.sh"
printf '#!/bin/bash\necho "$@" >>"%s/open.calls"\n' "$T" >"$T/open"; chmod +x "$T/open"
printf '#!/bin/bash\necho "$@" >>"%s/osa.calls"\n' "$T" >"$T/osa"; chmod +x "$T/osa"
export REWIND_OPEN="$T/open" REWIND_OSASCRIPT="$T/osa" REWIND_PY="$(command -v python3)"
LOG="$HOME/Library/Logs/Rewind/rewind.log"
FAILS=0
ok() { if eval "$2"; then echo "  PASS $1"; else echo "  FAIL $1"; FAILS=$((FAILS+1)); fi; }
port() { grep '^PORT=' "$HOME/Library/Application Support/Rewind/engine.txt" | cut -d= -f2; }
cleanup() { pkill -f -- "-m rewind( |$)" 2>/dev/null; }
trap cleanup EXIT

# an "old version" engine still running on 8765 (as Rewind 2 left behind)
(cd "$ROOT" && python3 -m rewind --no-browser --port 8765 --workspace "$T/oldws" >/dev/null 2>&1 &)
# another program sitting on 8777
python3 -m http.server 8777 --bind 127.0.0.1 >/dev/null 2>&1 & OTHER=$!
sleep 2

bash "$RES/launcher.sh"; rc=$?
P1=$(port)
ok "launcher exits cleanly after opening the window" '[ $rc = 0 ]'
ok "window opened only after the engine answered" 'grep -q "http://127.0.0.1:$P1/" "$T/open.calls" && curl -s --noproxy "*" "http://127.0.0.1:$P1/health" | grep -q "\"version\": \"5.2\""'
ok "skipped the busy port 8777 (picked $P1)" '[ "$P1" != 8777 ] && [ -n "$P1" ]'
ok "stopped the old engine left on 8765" '! curl -s -m 2 --noproxy "*" http://127.0.0.1:8765/health | grep -q rewind'
ok "the other program on 8777 was left alone" 'kill -0 $OTHER'
ok "log written to ~/Library/Logs/Rewind/rewind.log" 'grep -q "engine answers on" "$LOG" && grep -q "Rewind 5.2 starting" "$LOG"'

N1=$(pgrep -f -- "-m rewind( |$)" | wc -l)
bash "$RES/launcher.sh"
ok "second open reuses the running engine" '[ "$(pgrep -f -- "-m rewind( |$)" | wc -l)" = "$N1" ] && [ "$(grep -c 127.0.0.1 "$T/open.calls")" = 2 ]'

cleanup; sleep 1
REWIND_PY=/bin/false bash "$RES/launcher.sh"; rc=$?
ok "a crashing engine shows an error dialog instead of a blank window" '[ $rc = 1 ] && grep -q "Rewind could not start" "$T/osa.calls" && [ "$(grep -c 127.0.0.1 "$T/open.calls")" = 2 ]'
ok "the dialog gives the reason and the log" 'grep -q "stopped right after starting" "$T/osa.calls" && grep -q "Open log" "$T/osa.calls"'

kill $OTHER 2>/dev/null
echo
[ $FAILS = 0 ] && echo "ALL PASSED" || { echo "$FAILS FAILED"; exit 1; }
