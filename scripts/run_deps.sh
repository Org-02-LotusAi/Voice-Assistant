#!/usr/bin/env bash
# Starts / stops / reports the dependency stack: LiveKit dev server (Docker) + Voicebox headless backend.
# Usage: [WARM=1] [VOICEBOX_DATA_DIR=...] scripts/run_deps.sh [start|stop|status]
#   WARM=1  also POST /models/load chatterbox-turbo (first run downloads ~3.9 GB)
# Voicebox data dir default: deps/voicebox-store. It MUST NOT contain a path component named "data":
# Voicebox rewrites stored paths by stripping everything up to the first "data" component (docs/DEPENDENCIES.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VB="$ROOT/deps/voicebox"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PY="$VB/.venv/Scripts/python.exe" ;;
  *) PY="$VB/.venv/bin/python" ;;
esac
PIDFILE="$ROOT/deps/voicebox.pid"
LOGDIR="$ROOT/deps/logs"
COMPOSE="$ROOT/docker-compose.dev.yml"
PORT=17493
DATA_DIR="${VOICEBOX_DATA_DIR:-$ROOT/deps/voicebox-store}"

NORM="/${DATA_DIR//\\//}/"
if [[ "$NORM" == */data/* ]]; then
  echo "Voicebox data dir must not contain a path component named 'data': $DATA_DIR" >&2
  exit 2
fi

healthy() { curl -fsS -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

case "${1:-start}" in
  status)
    if healthy; then echo "voicebox: UP"; else echo "voicebox: DOWN"; fi
    docker ps --filter name=service015-livekit --format 'livekit: {{.Status}} {{.Ports}}'
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      pid="$(cat "$PIDFILE")"
      kill "$pid" 2>/dev/null || taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true
      rm -f "$PIDFILE"
      echo "stopped voicebox (pid $pid)"
    else
      echo "no voicebox pid file"
    fi
    docker compose -f "$COMPOSE" down
    ;;
  start)
    docker compose -f "$COMPOSE" up -d
    if healthy; then
      echo "voicebox already healthy"
    else
      [ -x "$PY" ] || { echo "missing $PY; run scripts/setup_deps.sh first" >&2; exit 1; }
      mkdir -p "$LOGDIR" "$DATA_DIR"
      (cd "$VB" && PYTHONUNBUFFERED=1 nohup "$PY" -m backend.main --host 127.0.0.1 --port "$PORT" --data-dir "$DATA_DIR" \
        >"$LOGDIR/voicebox.log" 2>"$LOGDIR/voicebox.err.log" & echo $! >"$PIDFILE")
      for _ in $(seq 1 90); do
        healthy && break
        sleep 2
      done
      healthy || { echo "voicebox did not become healthy within 180 s; see $LOGDIR/voicebox.err.log" >&2; exit 1; }
      echo "voicebox up on 127.0.0.1:$PORT (pid $(cat "$PIDFILE"), data $DATA_DIR)"
    fi
    if [ "${WARM:-0}" = "1" ]; then
      echo "loading chatterbox-turbo (downloads weights on first run)..."
      start=$(date +%s)
      curl -fsS -m 3600 -X POST "http://127.0.0.1:$PORT/models/load" -H 'content-type: application/json' \
        -d '{"model_name":"chatterbox-turbo"}' >/dev/null
      echo "chatterbox-turbo loaded in $(( $(date +%s) - start )) s"
    fi
    ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
