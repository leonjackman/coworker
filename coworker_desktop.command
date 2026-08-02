#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${COWORKER_BACKEND_PORT:-9527}"

cd "$ROOT_DIR"

echo "=== Coworker Desktop ==="

echo "[1/5] Preparing Python backend..."
if [[ ! -d "$ROOT_DIR/backend/venv" ]]; then
  python3 -m venv "$ROOT_DIR/backend/venv"
fi
"$ROOT_DIR/backend/venv/bin/python" -m pip install -q -r "$ROOT_DIR/backend/requirements.txt"

echo "[2/5] Preparing Node dependencies..."
if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
  npm install
fi
if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  (cd "$ROOT_DIR/frontend" && npm install)
fi

echo "[3/5] Building frontend..."
(cd "$ROOT_DIR/frontend" && npm run build)

echo "[4/5] Starting backend..."
# Kill any stale backend holding the port, then wait for the port to free up.
STALE_PIDS="$(lsof -ti :"$BACKEND_PORT" 2>/dev/null || true)"
if [[ -n "$STALE_PIDS" ]]; then
  echo "  Releasing port $BACKEND_PORT held by: $STALE_PIDS"
  kill $STALE_PIDS 2>/dev/null || true
  for _ in {1..20}; do
    if ! lsof -ti :"$BACKEND_PORT" >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
fi

"$ROOT_DIR/backend/venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT" --app-dir "$ROOT_DIR/backend" &
BACKEND_PID="$!"
DESKTOP_PID=""
BACKEND_MONITOR_PID=""

cleanup() {
  echo "Stopping Coworker backend..."
  if [[ -n "${BACKEND_MONITOR_PID:-}" ]]; then
    kill "$BACKEND_MONITOR_PID" 2>/dev/null || true
  fi
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

kill_process_tree() {
  local pid="$1"
  local child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_process_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

monitor_backend() {
  while kill -0 "$BACKEND_PID" 2>/dev/null; do
    sleep 1
  done
  if [[ -n "${DESKTOP_PID:-}" ]] && kill -0 "$DESKTOP_PID" 2>/dev/null; then
    echo "  Backend process exited while desktop was running; stopping desktop window."
    kill_process_tree "$DESKTOP_PID"
  fi
}

# Wait for the backend to become healthy, retrying generously.
backend_ready=0
for _ in {1..80}; do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    backend_ready=1
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "  Backend process exited unexpectedly. Check backend startup errors above."
    break
  fi
  sleep 0.25
done

if [[ "$backend_ready" != "1" ]]; then
  echo "  Backend did not become ready on port $BACKEND_PORT within the timeout."
  exit 1
fi

echo "[5/5] Launching desktop window..."
if [[ "${COWORKER_SKIP_DESKTOP:-0}" == "1" ]]; then
  echo "Desktop launch skipped by COWORKER_SKIP_DESKTOP=1"
  exit 0
fi

COWORKER_BACKEND_HOST=127.0.0.1 COWORKER_BACKEND_PORT="$BACKEND_PORT" npm run desktop &
DESKTOP_PID="$!"
monitor_backend &
BACKEND_MONITOR_PID="$!"
wait "$DESKTOP_PID"

echo "Coworker Desktop stopped"
