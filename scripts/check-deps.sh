#!/usr/bin/env bash
# Shared dependency-verification helpers for Coworker launchers & packagers.
#
# These functions are sourced (`.`/`source`) by the launcher scripts. They
# detect and repair missing/incomplete dependency installs so that a build or
# launch never fails because a package was declared but never installed (the
# classic `docx-preview`/`xlsx` failure) or because a lockfile changed without
# a matching `npm install`.
#
# Every function prints a short status line and returns non-zero on failure.

# ---- colour helpers (guard: don't clash if parent already defined) ---------
if ! command -v ok >/dev/null 2>&1; then
  GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
  ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
  warn() { echo -e "  ${YELLOW}⚠${NC} $1" 1>&2; }
  fail() { echo -e "  ${RED}✗${NC} $1" 1>&2; }
fi

# Verify every package declared in package.json actually has a directory under
# node_modules. This is the authoritative check for "declared but not
# installed" — mtime comparisons alone cannot catch a node_modules that was
# populated once and then the manifest grew (docx-preview/xlsx failure mode).
node_tree_complete() {
  local dir="$1"
  local node_bin
  node_bin="$(command -v node)" || return 1
  (cd "$dir" && "$node_bin" -e '
    const fs = require("fs");
    const path = require("path");
    const manifest = JSON.parse(fs.readFileSync("package.json", "utf8"));
    const deps = { ...(manifest.dependencies || {}), ...(manifest.devDependencies || {}) };
    const missing = Object.keys(deps).filter((name) => {
      try {
        fs.statSync(path.join("node_modules", name));
        return false;
      } catch {
        return true;
      }
    });
    if (missing.length) {
      console.error("  missing packages: " + missing.join(", "));
      process.exit(1);
    }
  ')
}

# True if the Node install is stale: node_modules missing, the lockfile/manifest
# changed after the last install, OR a declared package is absent on disk.
node_tree_stale() {
  local dir="$1"
  local stamp="$dir/node_modules/.package-lock.json"
  local manifest="$dir/package.json"
  local lockfile="$dir/package-lock.json"
  [[ -d "$dir/node_modules" ]] || return 0       # missing entirely → stale
  [[ -f "$manifest" ]] || return 0               # no manifest → treat as stale
  [[ -f "$stamp" ]] || return 0                  # never installed → stale
  [[ -f "$lockfile" && "$lockfile" -nt "$stamp" ]] && return 0
  [[ "$manifest" -nt "$stamp" ]] && return 0
  ! node_tree_complete "$dir" && return 0        # declared-but-missing → stale
  return 1
}

# Ensure a specific Node package tree is installed and in sync with its
# manifest + lockfile. Falls back to npm install (not npm ci) so already-built
# deps are reused. Returns non-zero if install fails.
ensure_node_deps() {
  local dir="$1"
  local what="${2:-Node dependencies}"

  if ! node_tree_stale "$dir"; then
    ok "$what ready"
    return 0
  fi

  warn "$what out of date (manifest/lockfile changed or node_modules incomplete) — running npm install"
  (cd "$dir" && npm install) || { fail "$what install failed"; return 1; }

  if node_tree_stale "$dir"; then
    fail "$what still incomplete after npm install"
    return 1
  fi
  ok "$what installed"
}

# True if the Python venv is missing or its requirements are stale.
# Uses a stamp file (venv/.requirements-stamp) that is touched after a
# successful pip install; reinstall when requirements.txt is newer than the
# stamp, or when the stamp is absent (venv recreated / never installed).
py_venv_stale() {
  local venv_dir="$1"
  local req_file="$2"
  local stamp="$venv_dir/.requirements-stamp"
  [[ -x "$venv_dir/bin/python" ]] || return 0
  [[ -f "$stamp" ]] || return 0
  [[ -f "$req_file" && "$req_file" -nt "$stamp" ]] && return 0
  return 1
}

# Ensure the Python venv exists and its requirements are installed.
# Sets the global PYTHON_BIN to the venv python path; returns non-zero on failure.
# NOTE: pip output is sent to stderr so this function never pollutes stdout
# (callers use the PYTHON_BIN global, not command substitution).
ensure_python_venv() {
  local venv_dir="$1"
  local req_file="$2"

  if [[ ! -x "$venv_dir/bin/python" ]]; then
    warn "Python venv missing at $venv_dir — creating it"
    python3 -m venv "$venv_dir" 1>&2 || { fail "failed to create venv at $venv_dir"; return 1; }
    # NOTE: do NOT touch the stamp here — a fresh venv has no requirements yet,
    # so py_venv_stale must still see it as stale and run pip install below.
  fi
  PYTHON_BIN="$venv_dir/bin/python"

  if py_venv_stale "$venv_dir" "$req_file"; then
    warn "Python dependencies out of date — installing $req_file"
    "$PYTHON_BIN" -m pip install --upgrade pip 1>&2 || { fail "pip upgrade failed"; return 1; }
    "$PYTHON_BIN" -m pip install -r "$req_file" 1>&2 || { fail "pip install failed"; return 1; }
    touch "$venv_dir/.requirements-stamp"
  else
    ok "Python venv ready"
  fi
}

# Ensure the frontend's declared runtime deps actually exist on disk.
verify_frontend_deps() {
  local dir="$1"
  ensure_node_deps "$dir" "Frontend dependencies"
}
