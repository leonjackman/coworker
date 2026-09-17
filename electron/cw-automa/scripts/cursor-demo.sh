#!/usr/bin/env bash
#
# Visually verify the blue virtual cursor (no Electron needed). It shows for a
# few seconds at the centre of the main display, asserts it is visible while
# control is happening, and asserts it disappears when control ends.
#
# Usage:  electron/cw-automa/scripts/cursor-demo.sh [binary] [seconds]
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${1:-${CW_AUTOMA_BIN:-$HERE/build/cw-automa}}"
SECS="${2:-4}"
if (( SECS < 2 )); then SECS=2; fi

if [[ ! -x "$BIN" ]]; then
  echo "helper binary not found/executable at $BIN" >&2
  echo "build it: (cd electron/cw-automa && swiftc -O -o build/cw-automa src/*.swift)" >&2
  exit 1
fi

OUTPUT="$(
  {
    printf '%s\n' '{"id":1,"method":"cursor_debug","params":{}}'
    printf '%s\n' "{\"id\":2,\"method\":\"cursor_demo\",\"params\":{\"seconds\":${SECS}}}"
    sleep 1
    printf '%s\n' '{"id":3,"method":"cursor_debug","params":{}}'
    sleep "$SECS"
    printf '%s\n' '{"id":4,"method":"cursor_debug","params":{}}'
  } | "$BIN"
)"

echo "$OUTPUT"

python3 - "$OUTPUT" <<'PY'
import json, sys

lines = [json.loads(l) for l in sys.argv[1].splitlines() if l.strip()]
by_id = {l["id"]: l.get("result", {}) for l in lines}

during = by_id.get(3, {})
after = by_id.get(4, {})

assert during.get("hasLayer"), f"cursor layer never created: {during}"
assert during.get("visible") or during.get("layerVisible"), \
    f"cursor not visible while controlling: {during}"
assert not after.get("visible"), \
    f"cursor still visible after control ended: {after}"

print("OK: cursor visible during control, hidden after:", {"during": during.get("visible"),
                                                          "after": after.get("visible")})
PY
