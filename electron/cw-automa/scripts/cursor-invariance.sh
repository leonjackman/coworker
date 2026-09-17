#!/usr/bin/env bash
#
# Proves the cw-automa helper never moves the REAL OS cursor when it drives the
# virtual one. It records the real cursor (CGEvent location) before and after a
# cursor_show + cursor_move and asserts it is unchanged, while the virtual
# cursor reports the requested point.
#
# Usage:  electron/cw-automa/scripts/cursor-invariance.sh [path-to-binary]
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${1:-${CW_AUTOMA_BIN:-$HERE/build/cw-automa}}"

if [[ ! -x "$BIN" ]]; then
  echo "helper binary not found/executable at $BIN" >&2
  echo "build it: (cd electron/cw-automa && swiftc -O -o build/cw-automa src/*.swift)" >&2
  exit 1
fi

OUTPUT="$(printf '%s\n' \
  '{"id":1,"method":"cursor_position","params":{}}' \
  '{"id":2,"method":"cursor_show","params":{}}' \
  '{"id":3,"method":"cursor_move","params":{"x":500,"y":400}}' \
  '{"id":4,"method":"cursor_position","params":{}}' \
  '{"id":5,"method":"cursor_hide","params":{}}' | "$BIN")"

python3 - "$OUTPUT" <<'PY'
import json, sys

lines = [json.loads(l) for l in sys.argv[1].splitlines() if l.strip()]
by_id = {l["id"]: l for l in lines}

def real(msg_id):
    r = by_id[msg_id]["result"]["real"]
    return (round(float(r["x"])), round(float(r["y"])))

before = real(1)
after = real(4)
virt = by_id[4]["result"]["virtual"]

assert before == after, f"REAL CURSOR MOVED: {before} -> {after}"
assert (round(float(virt["x"])), round(float(virt["y"]))) == (500, 400), \
    f"virtual cursor did not move to (500,400): {virt}"

print(f"OK: real cursor unchanged {before}; virtual cursor {virt}")
PY
