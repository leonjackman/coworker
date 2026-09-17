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
#   ./pack-local.command CW_SIGN_UUID=... # force a specific cert UUID
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

# ── shared dependency checks (venv + node_modules sync) ─────────────
# shellcheck source=scripts/check-deps.sh
source "$ROOT_DIR/scripts/check-deps.sh"

if [[ "$CLEAN" == "1" ]]; then
  echo "[0/4] Cleaning release/ output..."
  rm -rf "$ROOT_DIR/release"
  ok "release/ removed"
fi

if [[ "$SKIP_FRONTEND" == "1" ]]; then
  echo "[1/5] Skipping frontend build (reusing frontend/dist)"
else
  echo "[1/5] Preparing + building frontend..."
  ensure_node_deps "$ROOT_DIR/frontend" "Frontend dependencies" || exit 1
  (cd "$ROOT_DIR/frontend" && npm run build)
  ok "Frontend built"
fi

if [[ "$SKIP_BACKEND" == "1" ]]; then
  echo "[2/5] Skipping backend build (reusing backend/dist)"
else
   echo "[2/5] Building Python backend (PyInstaller)..."
   # Ensure the venv exists AND has requirements installed before freezing.
   ensure_python_venv "$ROOT_DIR/backend/venv" "$ROOT_DIR/backend/requirements.txt" || exit 1
   [[ -x "$PYTHON_BIN" ]] || fail "Python venv not found at $PYTHON_BIN"
   if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
     warn "PyInstaller missing in venv — installing it"
     "$PYTHON_BIN" -m pip install pyinstaller || fail "PyInstaller install failed"
   fi
   (cd "$ROOT_DIR/backend" && rm -rf dist build && "$PYTHON_BIN" -m PyInstaller --clean --noconfirm pybackend.spec 2>&1 | tail -20)
   ok "Backend bundled"

  # Sanity check: the frozen backend MUST contain the current source symbols.
  # Catches the exact failure mode where PyInstaller freezes a stale `coworker`
  # package — which would ship outdated behaviour (e.g. the pre-fix 252k window
  # instead of the configured 200k → ~192k). Fail loud here, not in production.
  if [[ -d "$ROOT_DIR/backend/build/pybackend/localpycs" ]]; then
    if ! grep -rlq "ContextGuardMiddleware" "$ROOT_DIR/backend/build/pybackend/localpycs" 2>/dev/null; then
      fail "Frozen backend is missing current source symbols (ContextGuardMiddleware) — PyInstaller did not pick up backend/coworker changes. Aborting before packaging."
    fi
    ok "Frozen backend contains current source symbols"
  else
    warn "PyInstaller localpycs not found; skipping frozen-symbol sanity check"
  fi
fi

echo "[3/5] Building native helper (cw-automa)..."
# The helper was previously never built by this script, so the packaged app
# shipped whatever stale binary happened to sit in electron/cw-automa/build.
# Always rebuild from src so the app never carries an outdated computer-use
# kernel (this is what made the blue virtual cursor never appear).
if ! command -v swiftc >/dev/null 2>&1; then
  fail "swiftc not found — install Xcode Command Line Tools: xcode-select --install"
fi
mkdir -p "$ROOT_DIR/electron/cw-automa/build"
rm -f "$ROOT_DIR/electron/cw-automa/build/cw-automa"
swiftc -O -o "$ROOT_DIR/electron/cw-automa/build/cw-automa" "$ROOT_DIR"/electron/cw-automa/src/*.swift
[[ -x "$ROOT_DIR/electron/cw-automa/build/cw-automa" ]] || fail "Native helper build failed"
ok "Native helper built"

# Stable-sign the helper so macOS TCC grants survive rebuilds. macOS keys
# Accessibility/Screen Recording to the binary's code-signing identity: an
# ad-hoc signature (codesign -s -) rotates that identity on every rebuild and
# forces the user to re-grant. We use a stable --identifier and, when a
# Developer ID / Apple Development cert is available, a stable identity too.
#
# security find-identity returns duplicate entries for the same cert (different
# keychain items → same display name). Passing the display name to codesign
# triggers "ambiguous" errors. We extract the UUID (fingerprint) instead.
HELPER_BIN="$ROOT_DIR/electron/cw-automa/build/cw-automa"
HELPER_ID="com.coworker.cw-automa"
HELPER_UUID="${CW_SIGN_UUID:-}"
if [[ -z "$HELPER_UUID" ]]; then
  HELPER_UUID="$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -E 'Developer ID Application|Apple Development' | head -1 \
    | awk '{print $2}' || true)"
fi

helper_signed=0
if [[ -n "$HELPER_UUID" ]]; then
  # Sign with the cert UUID (fingerprint) — unique, avoids "ambiguous" errors
  # from duplicate keychain entries with the same display name.
  for attempt in "--timestamp" ""; do
    # shellcheck disable=SC2086
    if codesign --force --options runtime $attempt --identifier "$HELPER_ID" \
         --sign "$HELPER_UUID" "$HELPER_BIN" >/dev/null 2>&1; then
      helper_signed=1
      break
    fi
  done
fi
if [[ "$helper_signed" != "1" ]]; then
  # Ad-hoc with a stable identifier: at least the identifier is constant, but
  # the cdhash still rotates, so TCC may re-prompt after each rebuild.
  codesign --force --identifier "$HELPER_ID" --sign - "$HELPER_BIN" >/dev/null 2>&1 || true
fi
ACTUAL_ID="$(codesign -dv "$HELPER_BIN" 2>&1 | sed -n 's/^Identifier=//p')"
if [[ "$helper_signed" == "1" ]]; then
  ok "Native helper signed (id: ${ACTUAL_ID:-?}) — TCC grants persist across rebuilds"
else
  warn "Native helper ad-hoc signed (id: ${ACTUAL_ID:-?}). No stable cert reachable — macOS may re-prompt for Accessibility/Screen Recording after each rebuild (set CW_SIGN_UUID to fix)."
fi

echo "[4/5] Packaging app (electron-builder --dir)..."
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

echo "[5/5] Result: $APP_PATH"
if [[ "$OPEN_APP" == "1" ]]; then
  # Fully terminate any running CoWorker instance (main + helpers + bundled
  # backend) BEFORE opening the fresh build. A graceful `osascript quit` can
  # leave helper processes alive, causing `open` to reactivate the OLD (stale)
  # binary — which would show outdated behaviour (e.g. the pre-fix 252k window
  # instead of the configured 200k). Kill the whole process tree, then wait
  # until it is truly gone.
  pkill -9 -f "CoWorker.app/Contents" 2>/dev/null || true
  for _ in $(seq 1 20); do
    pgrep -f "CoWorker.app/Contents" >/dev/null 2>&1 || break
    sleep 0.5
  done
  open "$APP_PATH"
  ok "Launched CoWorker.app (fresh build)"
else
  echo "  Skipped launch (--no-open)."
fi

echo -e "\n${BOLD}Done${NC}"