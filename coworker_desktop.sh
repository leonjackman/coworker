#!/usr/bin/env bash

# Coworker Desktop — Linux dev launcher (mirrors coworker_desktop.command).
# Usage:
#   ./coworker_desktop.sh                 # build frontend + start backend + launch desktop
#   COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.sh   # backend only (testing mode)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${COWORKER_BACKEND_PORT:-9527}"

# ── terminal colours ─────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'
BOLD='\033[1m'; CYAN='\033[0;36m'
NC='\033[0m'  # no colour

ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1" 1>&2; }
fail()  { echo -e "  ${RED}✗${NC} $1" 1>&2; }
echo -e "${CYAN}=== Coworker Desktop ===${NC}\n"

echo "[0/6] Killing existing Coworker processes..."
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:"$BACKEND_PORT" 2>/dev/null | xargs kill -9 2>/dev/null || true
fi
ok "Cleared port $BACKEND_PORT"

echo "[1/6] Preparing Python backend..."
ok "Python backend ready"

echo "[2/6] Preparing Node dependencies..."
ok "Node dependencies ready"

echo "[3/6] Building frontend..."
(cd "$ROOT_DIR/frontend" && npm run build) && ok "Frontend built"

echo "[4/6] Starting backend..."
# Kill any stale backend holding the port, then wait for the port to free up.
if command -v lsof >/dev/null 2>&1; then
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
fi

if [ -x "$ROOT_DIR/backend/venv/bin/python" ]; then
  BACKEND_PY="$ROOT_DIR/backend/venv/bin/python"
else
  BACKEND_PY="$(command -v python3 || command -v python)"
fi

"$BACKEND_PY" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT" --app-dir "$ROOT_DIR/backend" &
BACKEND_PID="$!"
DESKTOP_PID=""
BACKEND_MONITOR_PID=""

cleanup() {
  echo "  Stopping Coworker backend…"
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
    echo -e "  ${RED}Backend process exited; stopping desktop.${NC}" 1>&2
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
    fail "Backend process exited unexpectedly"
    exit 1
  fi
  sleep 0.25
done

if [[ "$backend_ready" != "1" ]]; then
  fail "Backend did not become ready on port $BACKEND_PORT within timeout."
  exit 1
fi
ok "Backend ready on 127.0.0.1:$BACKEND_PORT"

echo "[5/6] Launching desktop..."
if [[ "${COWORKER_SKIP_DESKTOP:-0}" == "1" ]]; then
  echo "  skipped (testing mode). Backend stays on 127.0.0.1:$BACKEND_PORT."
  echo "  Press Ctrl+C to stop the backend."
  while kill -0 "$BACKEND_PID" 2>/dev/null; do
    sleep 1
  done
  exit 0
fi

COWORKER_BACKEND_HOST=127.0.0.1 COWORKER_BACKEND_PORT="$BACKEND_PORT" COWORKER_DEV="1" npm --prefix "$ROOT_DIR" run desktop &
DESKTOP_PID="$!"
monitor_backend &
BACKEND_MONITOR_PID="$!"
wait "$DESKTOP_PID"

echo -e "\n${BOLD}Coworker Desktop stopped${NC}"
