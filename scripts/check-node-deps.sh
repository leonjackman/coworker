#!/usr/bin/env bash
# Ensure the ROOT Node dependencies (electron, electron-builder, electron-updater)
# are installed and complete before running `npm run desktop` / electron-builder.
# Sourced from package.json via the `predesktop` npm hook.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1" 1>&2; }
fail() { echo -e "  ${RED}✗${NC} $1" 1>&2; exit 1; }

# shellcheck source=scripts/check-deps.sh
source "$ROOT_DIR/scripts/check-deps.sh"

ensure_node_deps "$ROOT_DIR" "Root dependencies"
