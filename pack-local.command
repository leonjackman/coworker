#!/usr/bin/env bash

# Local macOS packaging test script.
# Builds the frontend + bundled Python backend, produces an unpacked
# CoWorker.app via electron-builder --dir (no DMG/ZIP), then opens it.
#
# Usage:
#   ./pack-local.command                 # full build + open (unsigned app)
#   ./pack-local.command --no-open       # build but don't launch
#   ./pack-local.command --skip-frontend --skip-backend   # reuse existing build outputs
#   ./pack-local.command --clean         # wipe release/ before packaging
#   ./pack-local.command --sign          # sign with the discovered Developer ID cert
#
# Local builds are unsigned by default (CSC_IDENTITY_AUTO_DISCOVERY=false) to
# avoid codesign blocking on keychain authorization. Signing/notarization is
# handled by CI (release.yml) for actual releases.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$ROOT_DIR/backend/venv/bin/python"
APP_PATH=""

OPEN_APP=1
SKIP_FRONTEND=0
SKIP_BACKEND=0
CLEAN=0
SIGN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open)          OPEN_APP=0; shift ;;
    --skip-frontend)    SKIP_FRONTEND=1; shift ;;
    --skip-backend)     SKIP_BACKEND=1; shift ;;
    --clean)            CLEAN=1; shift ;;
    --sign)             SIGN=1; shift ;;
    -h|--help)
      grep '^# ' "$0" | sed 's/^# //'
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Run '$0 --help' for usage."
      exit 1
      ;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; BOLD='\033[1m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1" 1>&2; }
fail() { echo -e "  ${RED}✗${NC} $1" 1>&2; exit 1; }

echo -e "${CYAN}=== CoWorker Local Package (mac --dir) ===${NC}\n"

if [[ "$CLEAN" == "1" ]]; then
  echo "[0/4] Cleaning release/ output..."
  rm -rf "$ROOT_DIR/release"
  ok "release/ removed"
fi

if [[ "$SKIP_FRONTEND" == "1" ]]; then
  echo "[1/4] Skipping frontend build (reusing frontend/dist)"
else
  echo "[1/4] Building frontend..."
  (cd "$ROOT_DIR/frontend" && npm run build)
  ok "Frontend built"
fi

if [[ "$SKIP_BACKEND" == "1" ]]; then
  echo "[2/4] Skipping backend build (reusing backend/dist)"
else
  echo "[2/4] Building Python backend (PyInstaller)..."
  [[ -x "$PYTHON_BIN" ]] || fail "Python venv not found at $PYTHON_BIN (run: cd backend && python3 -m venv venv)"
  (cd "$ROOT_DIR/backend" && rm -rf dist build && "$PYTHON_BIN" -m PyInstaller --clean --noconfirm pybackend.spec 2>&1 | tail -20)
  ok "Backend bundled"
fi

echo "[3/4] Packaging app (electron-builder --dir)..."
if [[ "$SIGN" == "1" ]]; then
  echo "  Signing enabled (Developer ID auto-discovery)."
else
  echo "  Signing skipped (unsigned local build)."
  export CSC_IDENTITY_AUTO_DISCOVERY=false
fi
(cd "$ROOT_DIR" && npx electron-builder --mac --config electron-builder.config.json --dir)
ok "Package created"

APP_PATH="$(find "$ROOT_DIR/release" -maxdepth 2 -name 'CoWorker.app' -type d | head -1 || true)"
if [[ -z "$APP_PATH" ]]; then
  warn "Could not locate CoWorker.app under release/ — not launching."
  exit 0
fi

echo "[4/4] Result: $APP_PATH"
if [[ "$OPEN_APP" == "1" ]]; then
  # If a previous instance is running, quit it first so the fresh build loads.
  if pgrep -f "CoWorker.app/Contents/MacOS/CoWorker" >/dev/null 2>&1; then
    osascript -e 'quit app "CoWorker"' 2>/dev/null || true
    sleep 1
  fi
  open "$APP_PATH"
  ok "Launched CoWorker.app"
else
  echo "  Skipped launch (--no-open)."
fi

echo -e "\n${BOLD}Done${NC}"