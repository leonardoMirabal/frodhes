#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# ci_move.sh [--dry-run]: the Move's checks (MT0, private plan 05 section 6.1) as one script, the same locally and in
# CI's `move` job (ubuntu-24.04-arm in debian:bookworm, the Move's glibc). Light by default: nice, PYTEST_N=2.
#   1 Gate U      move/module/ is what tools/gen_move_module.py makes (--check)
#   2 build       make move test-move (dsp.so, the exports / imports, the dlopen smoke): when the Makefile has them
#   3 Gate M / P  tools/gate_m.py, tests/py/test_move_safety.py: when they exist (M1-10, M1-11)
#   4 pytest      the Move's own tests (tests/py/test_move_*.py, the closure map, float_check)
# On macOS step 2 runs in the arm64 bookworm image (the Makefile's MOVE_RUN); in CI natively (MOVE_RUN=).
# Exit 0 only when every step that ran passed; a step that cannot run yet says so (SKIP) and does not fail.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 2
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
PY=${PY:-python3}
N=${PYTEST_N:-2}
NICE=${NICE:-nice -n 19}
fail=0

step() {  # $1 name, $2.. the command
    name=$1; shift
    echo "== $name: $*"
    [ $DRY = 1 ] && return 0
    if $NICE "$@"; then echo "ok    $name"; else echo "FAIL  $name"; fail=1; fi
}
skip() { echo "SKIP  $1 ($2)"; }

step "gate U" "$PY" tools/gen_move_module.py --check
if [ -f move/move_plugin.c ] && make -n move-native >/dev/null 2>&1; then
    step "build + test-move" make -s move test-move
else
    skip "build + test-move" "no move/move_plugin.c or Makefile move targets in this tree"
fi
if [ -f tools/gate_m.py ]; then step "gate M" "$PY" tools/gate_m.py; else skip "gate M" "tools/gate_m.py: M1-10"; fi
# (the named files reach tools/pytest_run.py as named: a missing one fails the step, never drops out of the list)
TESTS="$(ls tests/py/test_move_*.py) tests/py/test_target_closure.py tests/py/test_float_check.py tests/py/test_gate_s.py tests/py/test_test_map_targets.py"
if "$PY" -c "import xdist" 2>/dev/null && [ "$N" != 0 ]; then
    step "pytest (Move)" "$PY" tools/pytest_run.py -q -p no:cacheprovider -n "$N" -- $TESTS
else
    step "pytest (Move)" "$PY" tools/pytest_run.py -q -p no:cacheprovider -- $TESTS
fi
[ $DRY = 1 ] && { echo "(dry run)"; exit 0; }
[ $fail = 0 ] && echo "ci_move: PASS" || echo "ci_move: FAIL"
exit $fail
