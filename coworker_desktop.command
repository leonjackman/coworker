#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${COWORKER_BACKEND_PORT:-8000}"

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
lsof -ti :"$BACKEND_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
"$ROOT_DIR/backend/venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT" --app-dir "$ROOT_DIR/backend" &
BACKEND_PID="$!"

cleanup() {
  echo "Stopping Coworker backend..."
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in {1..40}; do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null

echo "[5/5] Launching desktop window..."
if [[ "${COWORKER_SKIP_DESKTOP:-0}" == "1" ]]; then
  echo "Desktop launch skipped by COWORKER_SKIP_DESKTOP=1"
  exit 0
fi

COWORKER_BACKEND_HOST=127.0.0.1 COWORKER_BACKEND_PORT="$BACKEND_PORT" npm run desktop

echo "Coworker Desktop stopped"
