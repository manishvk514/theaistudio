#!/bin/bash
# Rewind launcher (v4). Starts Rewind's engine with its built-in Python, waits until the
# engine really answers, and only then opens the window. Every step is written to
# ~/Library/Logs/Rewind/rewind.log, and any failure shows a dialog with the reason.
RES="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$(cd "$RES/../.." && pwd)"
VERSION="5.0"
SUP="$HOME/Library/Application Support/Rewind"
LOGDIR="$HOME/Library/Logs/Rewind"
LOG="$LOGDIR/rewind.log"
STATE="$SUP/engine.txt"
OPEN="${REWIND_OPEN:-/usr/bin/open}"          # test hooks; never set in normal use
OSA="${REWIND_OSASCRIPT:-/usr/bin/osascript}"
mkdir -p "$SUP" "$LOGDIR"
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG" | tr -d ' ')" -gt 2000000 ]; then mv -f "$LOG" "$LOG.old"; fi
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }
log "=== Rewind $VERSION launched from $APPDIR (macOS $(sw_vers -productVersion 2>/dev/null), $(uname -m))"

health() { curl -s --noproxy '*' -m 2 "http://127.0.0.1:$1/health" 2>/dev/null; }
ours() { health "$1" | grep -q "\"version\": \"$VERSION\""; }
page_ok() { curl -s --noproxy '*' -m 8 "http://127.0.0.1:$1/" 2>/dev/null | grep -q "<title>Rewind"; }
in_use() { health "$1" | grep -q . || { [ -x /usr/sbin/lsof ] && /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }; }

open_window() {
  local url="http://127.0.0.1:$1/"
  log "engine answers on $url; opening the window"
  for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
    if [ -d "/Applications/$b.app" ] || [ -d "$HOME/Applications/$b.app" ]; then
      "$OPEN" -na "$b" --args --app="$url" --new-window && return
    fi
  done
  "$OPEN" "$url"
}

fail() {
  log "FAILED: $1"
  local t btn
  t=$(tail -n 8 "$LOG" | tr '"\\' "''" | tr '\n' ' ' | cut -c1-600)
  btn=$("$OSA" -e "button returned of (display dialog \"Rewind could not start.\n\n$1\n\nLast lines of the log:\n$t\" buttons {\"Open log\", \"OK\"} default button \"Open log\" with title \"Rewind\" with icon caution)" 2>/dev/null)
  if [ "$btn" = "Open log" ]; then "$OPEN" -a Console "$LOG" 2>/dev/null || "$OPEN" -a TextEdit "$LOG"; fi
  exit 1
}

# 1. Already running (same version, answering properly)? Just open the window.
OLDPORT=$(grep '^PORT=' "$STATE" 2>/dev/null | cut -d= -f2 | tr -cd '0-9')
if [ -n "$OLDPORT" ] && ours "$OLDPORT" && page_ok "$OLDPORT"; then open_window "$OLDPORT"; exit 0; fi

# 2. Stop any older or stuck Rewind engine (from this or an earlier version).
STALE=$(pgrep -f -- "-m rewind( |$)" 2>/dev/null)
if [ -n "$STALE" ]; then
  for p in $STALE; do log "stopping an older Rewind engine (pid $p)"; kill "$p" 2>/dev/null; done
  sleep 1
  for p in $STALE; do kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null; done
fi

# 3. You approved Rewind once (Open Anyway). Clear the download flag so macOS stops asking.
/usr/bin/xattr -dr com.apple.quarantine "$APPDIR" >/dev/null 2>&1
[ -x "$RES/bin/rclone" ] && export REWIND_RCLONE="$RES/bin/rclone"

# 4. Find a Python that works: the built-in one first.
BPY="$RES/python/bin/python3.12"
PY=""
if [ -n "$REWIND_PY" ]; then
  PY="$REWIND_PY"
elif [ -x "$BPY" ] && PYTHONHOME="$RES/python" "$BPY" -c "import ssl, sqlite3, zlib, asyncio, json" >>"$LOG" 2>&1; then
  PY="$BPY"
  # Point Python at its own folder inside the app, wherever the app was copied to.
  export PYTHONHOME="$RES/python" SSL_CERT_FILE="$RES/python/ssl/cacert.pem" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  log "built-in Python OK: $("$BPY" -c 'import sys; print(sys.version.split()[0], sys.prefix)' 2>&1)"
else
  log "built-in Python could not start (see the lines above); trying an installed Python"
  for c in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$c" ]; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || fail "Its built-in engine was blocked by macOS. Open Terminal and run:  xattr -cr /Applications/Rewind.app  then open Rewind again."
cd "$RES/app" || fail "The app's files are missing. Download Rewind again."

# 5. Start the engine on a free port and wait until it really answers (up to 90 s).
BAD=" "
for attempt in 1 2 3 4 5; do
  PORT=""
  for p in $(seq 8777 8799); do
    case "$BAD" in *" $p "*) continue ;; esac
    if ! in_use "$p" && ! in_use $((p + 1)); then PORT=$p; break; fi
  done
  [ -n "$PORT" ] || fail "Ports 8777 to 8799 are all in use on this Mac."
  log "starting the engine on port $PORT (attempt $attempt) with $PY"
  PYTHONPATH="$RES/app" nohup "$PY" -m rewind --no-browser --port "$PORT" >>"$LOG" 2>&1 &
  SPID=$!
  printf 'PORT=%s\nPID=%s\n' "$PORT" "$SPID" >"$STATE"
  for i in $(seq 1 180); do
    if ! kill -0 "$SPID" 2>/dev/null; then
      wait "$SPID"; CODE=$?
      log "the engine stopped with code $CODE"
      if [ "$CODE" = 3 ]; then BAD="$BAD$PORT "; continue 2; fi   # port taken after all: try the next one
      fail "The engine stopped right after starting (code $CODE)."
    fi
    if ours "$PORT" && page_ok "$PORT"; then open_window "$PORT"; exit 0; fi
    [ "$i" = 6 ] && "$OSA" -e 'display notification "Starting Rewind..." with title "Rewind"' >/dev/null 2>&1
    sleep 0.5
  done
  kill "$SPID" 2>/dev/null
  fail "The engine did not answer within 90 seconds."
done
fail "Could not find a free port to start on."
