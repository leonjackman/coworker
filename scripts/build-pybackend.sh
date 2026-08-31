#!/usr/bin/env bash
# Build the bundled Python backend (PyInstaller) with dependency checks.
#
# Ensures the venv exists, its requirements are installed, and PyInstaller is
# present before freezing — so a build never fails with a confusing
# "No module named X" from a stale venv.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1" 1>&2; }
fail() { echo -e "  ${RED}✗${NC} $1" 1>&2; exit 1; }

# shellcheck source=scripts/check-deps.sh
source "$ROOT_DIR/scripts/check-deps.sh"

PYTHON_BIN=""
ensure_python_venv "$ROOT_DIR/backend/venv" "$ROOT_DIR/backend/requirements.txt" || exit 1
[[ -x "$PYTHON_BIN" ]] || fail "Python venv not found at $PYTHON_BIN"

if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
  warn "PyInstaller missing in venv — installing it"
  "$PYTHON_BIN" -m pip install pyinstaller || fail "PyInstaller install failed"
  ok "PyInstaller installed"
fi

(cd "$ROOT_DIR/backend" && rm -rf dist build && "$PYTHON_BIN" -m PyInstaller --clean --noconfirm pybackend.spec 2>&1 | tail -20)
ok "Backend bundled"
