#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Line and branch coverage of the firmware's own code on the host (llvm-cov): the committed tree (HEAD) exported to
# build/cov/tree, its build/host/frodhes built with clang's -fprofile-instr-generate -fcoverage-mapping (every tool
# and test that runs build/host/frodhes there runs the instrumented one), then make test, make pytest, the control
# sweep and the monkey's seeds run in it. Every host run adds to a pool of profiles (LLVM_PROFILE_FILE %4m).
#
#   tools/coverage.sh [--quick] [--seeds 0-63] [--no-pytest]
#
# Output: build/cov/report.txt (per file: lines, branches, functions; firmware/src without platform / hal / usb /
# loader), build/cov/functions.txt (the summary, every function never executed), build/cov/branches.txt, build/cov/html/ (llvm-cov show), build/cov/
# frodhes.profdata. --quick: make test and 8 monkey seeds only. Heavy (pytest -n PYTEST_N, default 2): run it under
# the HEAVY lock. Needs .local/venv (pytest, numpy).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
COV="$ROOT/build/cov"
SEEDS=0-63
QUICK=0
PYT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) QUICK=1; SEEDS=0-7 ;;
    --seeds) SEEDS="$2"; shift ;;
    --no-pytest) PYT=0 ;;
    *) echo "usage: tools/coverage.sh [--quick] [--seeds A-B] [--no-pytest]"; exit 2 ;;
  esac
  shift
done
PROFDATA=$(xcrun -f llvm-profdata 2>/dev/null || command -v llvm-profdata)
LLVMCOV=$(xcrun -f llvm-cov 2>/dev/null || command -v llvm-cov)
PY="$ROOT/.local/venv/bin/python"
[ -x "$PROFDATA" ] && [ -x "$LLVMCOV" ] || { echo "no llvm-profdata / llvm-cov"; exit 1; }

rm -rf "$COV"
mkdir -p "$COV/tree" "$COV/prof"
(cd "$ROOT" && git archive HEAD) | tar -x -C "$COV/tree"
ln -s "$(cd "$ROOT/.local" && pwd -P)" "$COV/tree/.local"
export LLVM_PROFILE_FILE="$COV/prof/cov-%4m.profraw"
cd "$COV/tree"
make -s build/host/frodhes CC=clang HOST_DEFS="-O1 -fprofile-instr-generate -fcoverage-mapping"
BIN="$COV/tree/build/host/frodhes"

step() { echo "== $1 ($(date +%H:%M:%S))"; shift; "$@" || echo "   (exit $?: coverage counts what ran)"; }
step "make test" make -s test PY="$PY"
if [ "$QUICK" = 0 ]; then
  [ "$PYT" = 1 ] && step "make pytest" make -s pytest PY="$PY" PYTEST_N="${PYTEST_N:-2}"
  step "control sweep" "$PY" tools/control_sweep.py -j 2 --json "$COV/sweep.json"
fi
step "monkey seeds $SEEDS" "$PY" tools/monkey.py --seeds "$SEEDS" -j 2 --no-shrink --clicks 3 --shed 3

"$PROFDATA" merge -sparse -o "$COV/frodhes.profdata" "$COV"/prof/*.profraw
IGN='(platform|usb|usb_desc|uac|midi_uart|ota|loader|hal/.*|tests/.*|firmware/gen/.*)\.[ch]$'
"$LLVMCOV" report "$BIN" -instr-profile="$COV/frodhes.profdata" -ignore-filename-regex="$IGN" \
  -show-region-summary=false > "$COV/report.txt"
"$LLVMCOV" show "$BIN" -instr-profile="$COV/frodhes.profdata" -ignore-filename-regex="$IGN" -format=html \
  -show-branches=count -output-dir="$COV/html" >/dev/null
"$LLVMCOV" export "$BIN" -instr-profile="$COV/frodhes.profdata" -ignore-filename-regex="$IGN" -format=text \
  -skip-expansions > "$COV/export.json"
"$PY" "$ROOT/tools/coverage_sum.py" "$COV/export.json" > "$COV/functions.txt"
"$PY" "$ROOT/tools/coverage_sum.py" "$COV/export.json" --branches | sed -n '/^# conditions/,$p' > "$COV/branches.txt"
rm -rf "$COV/prof" "$COV/export.json"
echo "coverage: $COV/report.txt (per file), $COV/functions.txt (never run), $COV/html/index.html"
cat "$COV/report.txt"
