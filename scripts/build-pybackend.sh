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

# Pre-build gate: prove every Office/PDF dependency (and its data files) imports
# and works in the source venv before freezing. A missing template/font/cmap or
# an uninstalled package fails HERE with a clear message, not at user runtime.
(cd "$ROOT_DIR/backend" && "$PYTHON_BIN" -c "from coworker.documents.selfcheck import run_documents_selfcheck; raise SystemExit(run_documents_selfcheck())") \
  || fail "Document dependency self-check failed (fix backend/requirements.txt / run pip install)"
ok "Document dependencies verified"

(cd "$ROOT_DIR/backend" && rm -rf dist build && "$PYTHON_BIN" -m PyInstaller --clean --noconfirm pybackend.spec 2>&1 | tail -20)
ok "Backend bundled"

# Post-build gate: run the FROZEN binary's self-check so bundled data files
# (docx/pptx templates, pdfminer cmaps, reportlab fonts) and the pdfium native
# library are proven present inside the PyInstaller bundle.
FROZEN_BIN="$ROOT_DIR/backend/dist/pybackend"
[[ -x "$FROZEN_BIN" ]] || FROZEN_BIN="$ROOT_DIR/backend/dist/pybackend.exe"
[[ -x "$FROZEN_BIN" ]] || fail "Frozen backend not found at backend/dist/pybackend[.exe]"
"$FROZEN_BIN" --selfcheck-documents || fail "Frozen backend document self-check failed (missing bundled data/native lib)"
ok "Frozen backend document stack verified"
