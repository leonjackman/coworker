#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${COWORKER_BACKEND_PORT:-9527}"

cd "$ROOT_DIR"

echo "=== Coworker Desktop ==="

echo "[0/6] Killing existing Coworker processes..."
pkill -f 'npm run desktop' 2>/dev/null || true
pkill -f 'npm run dev' 2>/dev/null || true
pkill -f 'electron.*coworker\|electron.*--dir.*coworker' 2>/dev/null || true
pkill -f 'uvicorn.*main:app.*coworker' 2>/dev/null || true
pkill -f 'python.*uvicorn.*main:app.*app-dir.*coworker' 2>/dev/null || true
pkill -f 'node.*vite.*coworker' 2>/dev/null || true
for _ in {1..10}; do
  if ! pgrep -f 'npm run desktop\|npm run dev\|electron.*coworker\|uvicorn.*main:app.*app-dir.*coworker\|node.*vite.*coworker' >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
sleep 0.3

echo "[1/6] Preparing Python backend..."
if [[ ! -d "$ROOT_DIR/backend/venv" ]]; then
  python3 -m venv "$ROOT_DIR/backend/venv"
fi
"$ROOT_DIR/backend/venv/bin/python" -m pip install -q -r "$ROOT_DIR/backend/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/

echo "[2/6] Preparing Node dependencies..."
if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
  npm install
fi
if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  (cd "$ROOT_DIR/frontend" && npm install)
fi

echo "[3/6] Building frontend..."
(cd "$ROOT_DIR/frontend" && npm run build)

echo "[4/6] Starting backend..."
# Kill any stale backend holding the port, then wait for the port to free up.
# Only kill pids whose command line actually looks like the Coworker backend, so
# an unrelated process that happens to use the port is never killed.
STALE_PIDS="$(lsof -ti :"$BACKEND_PORT" 2>/dev/null || true)"
for pid in $STALE_PIDS; do
  if ps -p "$pid" -o command= 2>/dev/null | grep -q "uvicorn.*main:app\|coworker.*main"; then
    echo "  Releasing port $BACKEND_PORT held by Coworker backend (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
done
for _ in {1..20}; do
  if ! lsof -ti :"$BACKEND_PORT" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

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

echo "[5/6] Launching desktop window..."
if [[ "${COWORKER_SKIP_DESKTOP:-0}" == "1" ]]; then
  echo "Desktop launch skipped by COWORKER_SKIP_DESKTOP=1 — backend stays up on 127.0.0.1:$BACKEND_PORT for testing."
  echo "Press Ctrl+C to stop the backend."
  # Keep the backend alive instead of exiting (exit would trigger the cleanup
  # trap and kill it). Wait for Ctrl+C.
  while kill -0 "$BACKEND_PID" 2>/dev/null; do
    sleep 1
  done
  exit 0
fi

COWORKER_BACKEND_HOST=127.0.0.1 COWORKER_BACKEND_PORT="$BACKEND_PORT" COWORKER_DEV="1" npm run desktop &
DESKTOP_PID="$!"
monitor_backend &
BACKEND_MONITOR_PID="$!"
wait "$DESKTOP_PID"

echo "Coworker Desktop stopped"
