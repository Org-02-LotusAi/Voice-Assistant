#!/usr/bin/env bash
# Starts / stops / reports the voice engine sidecar (port 17494) using Voicebox's venv interpreter.
# Usage: [CHATTERBOX_VARIANT=turbo|turbo-nano] [VOICE_ENGINE_DEVICE=cpu|cuda] scripts/run_engine.sh [start|stop|status]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PY="$ROOT/deps/voicebox/.venv/Scripts/python.exe" ;;
  *) PY="$ROOT/deps/voicebox/.venv/bin/python" ;;
esac
PIDFILE="$ROOT/deps/engine.pid"
LOGDIR="$ROOT/deps/logs"
PORT="${VOICE_ENGINE_PORT:-17494}"

case "${1:-start}" in
  status) curl -fsS -m 3 "http://127.0.0.1:$PORT/health" || { echo "engine: DOWN"; exit 1; } ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      pid="$(cat "$PIDFILE")"
      kill "$pid" 2>/dev/null || taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true
      rm -f "$PIDFILE"; echo "stopped engine (pid $pid)"
    else echo "no engine pid file"; fi ;;
  start)
    [ -x "$PY" ] || { echo "missing $PY; run scripts/setup_deps.sh first" >&2; exit 1; }
    if curl -fsS -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then echo "engine already running"; exit 0; fi
    mkdir -p "$LOGDIR"
    SRC="$ROOT/deps/chatterbox-src"
    if [ ! -d "$SRC/src/chatterbox" ]; then   # GitHub master: PyPI 0.1.7 has no Nano
      git clone -q https://github.com/resemble-ai/chatterbox "$SRC"
      git -C "$SRC" checkout -q 5de7a54aa4e5e2baadb0182dde554908b48b85c2
    fi
    export VOICE_ENGINE_PORT="$PORT" VOICE_ENGINE_DEVICE="${VOICE_ENGINE_DEVICE:-cpu}" CHATTERBOX_VARIANT="${CHATTERBOX_VARIANT:-turbo}" \
           VOICEBOX_URL="${VOICEBOX_URL:-http://127.0.0.1:17493}" PYTHONUNBUFFERED=1
    nohup "$PY" "$ROOT/engine/chatterbox_server.py" >"$LOGDIR/engine.log" 2>"$LOGDIR/engine.err.log" &
    echo $! >"$PIDFILE"
    echo "engine starting (pid $(cat "$PIDFILE"), variant $CHATTERBOX_VARIANT, device $VOICE_ENGINE_DEVICE); model load takes minutes: poll /health until status=ok"
    echo "logs: $LOGDIR/engine.log"
    ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
