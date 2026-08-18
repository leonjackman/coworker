#!/usr/bin/env bash

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

ok "Killed existing Coworker processes"

ok "Python backend ready"

ok "Node dependencies ready"

echo -n "[3/6] Building frontend... "
BUILD_OUTPUT=$(cd "$ROOT_DIR/frontend" && npm run build 2>&1) && {
  ok "$BUILD_OUTPUT"
}

echo -n "[4/6] Starting backend... "
# Kill any stale backend holding the port, then wait for the port to free up.
# Only kill pids whose command line actually looks like the Coworker backend, so
# an unrelated process that happens to use the port is never killed.
STALE_PIDS="$(lsof -ti :"$BACKEND_PORT" 2>/dev/null || true)"
for pid in $STALE_PIDS; do
  if ps -p "$pid" -o command= 2>/dev/null | grep -q "uvicorn.*main:app\|coworker.*main"; then
    echo -n "releasing port $BACKEND_PORT (pid $pid)..."
    kill "$pid" 2>/dev/null || true
  fi
done
# wait for the port to free up
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

echo -n "[5/6] Launching desktop... "
if [[ "${COWORKER_SKIP_DESKTOP:-0}" == "1" ]]; then
  echo "skipped (testing mode). Backend stays on 127.0.0.1:$BACKEND_PORT."
  echo "  Press Ctrl+C to stop the backend."
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

echo -e "\n${BOLD}Coworker Desktop stopped${NC}"
