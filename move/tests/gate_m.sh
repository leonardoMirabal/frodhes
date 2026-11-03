#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Gate M's renders (tools/gate_m.py runs this in the Move image on macOS, natively on Linux arm64):
#   move/tests/gate_m.sh replay LIST JOBS   each "NAME SCRIPT" line of LIST through build/move/frodhes/dsp.so
#                                           (move/tests/move_replay.c) -> build/gate_m/replay/NAME.wav
#   move/tests/gate_m.sh host16 LIST JOBS   the same scripts through the host build at the Move's voice count
#                                           (MOVE_DEFS) -> build/gate_m/host16/NAME.wav (only for --save)
set -eu
MODE=$1 LIST=$2 JOBS=${3:-2}
DEFS=${MOVE_DEFS:--DFR_VOICES=16u -DFR_BYP_KEEP_CMP=1 -DFR_REV_HQ=1}
mkdir -p build/gate_m build/move/t
case $MODE in
replay)
    [ -f build/move/frodhes/dsp.so ] || { echo "no build/move/frodhes/dsp.so: make move first"; exit 1; }
    cc -O1 -std=gnu11 -Wall -Wextra -o build/gate_m/move_replay move/tests/move_replay.c -ldl
    RUN='mkdir -p "$(dirname build/gate_m/replay/$0)" && build/gate_m/move_replay build/move/frodhes/dsp.so "$1" "build/gate_m/replay/$0.wav" > "build/gate_m/replay/$0.log" 2>&1 || { echo "FAIL $0 (exit $?)"; cat "build/gate_m/replay/$0.log"; }' ;;
host16)
    cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST $DEFS -DAPP_VER='"dev"' -Ifirmware/src -Ifirmware/gen \
      -o build/gate_m/host16.bin firmware/src/frodhes.c -lm 2> build/gate_m/host16.build.log
    RUN='mkdir -p "$(dirname build/gate_m/host16/$0)" && build/gate_m/host16.bin "build/gate_m/host16/$0" "$1" > "build/gate_m/host16/$0.log" 2>&1 || true' ;;
*) echo "usage: gate_m.sh replay|host16 LIST JOBS"; exit 2 ;;
esac
xargs -P "$JOBS" -L 1 sh -c "$RUN" < "$LIST"
rm -rf build/move/t/run.*
