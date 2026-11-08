#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Gate M's renders (tools/gate_m.py runs this in the Move image on macOS, natively on Linux arm64):
#   move/tests/gate_m.sh replay LIST JOBS [DSP]   each "NAME SCRIPT" line of LIST through DSP (default
#                                           build/move/frodhes/dsp.so; tools/gate_m.py passes its snapshot,
#                                           build/gate_m/so/dsp.so) (move/tests/move_replay.c) -> build/gate_m/replay/NAME.wav;
#                                           the instances' folders in build/gate_m/t (MOVE_REPLAY_TMP), not build/move/t
#   move/tests/gate_m.sh host16 LIST JOBS   the same scripts through the host build at the Move's voice count
#                                           (MOVE_DEFS) -> build/gate_m/host16/NAME.wav (--save, --update)
#   move/tests/gate_m.sh host16 LIST JOBS SRC TAG
#                                           the same from another source tree (SRC: a firmware/ folder, e.g. a ref
#                                           commit's, tools/gate_m.py --update) -> build/gate_m/TAG/NAME.wav
set -eu
MODE=$1 LIST=$2 JOBS=${3:-2}
if [ "$MODE" = replay ]; then DSP=${4:-build/move/frodhes/dsp.so} SRC=firmware TAG=$1
else DSP=build/move/frodhes/dsp.so SRC=${4:-firmware} TAG=${5:-$1}; fi
DEFS=${MOVE_DEFS:--DFR_VOICES=16u -DFR_BYP_KEEP_CMP=1 -DFR_REV_HQ=1}
mkdir -p build/gate_m build/gate_m/t
export MOVE_REPLAY_TMP=build/gate_m/t DSP
case $MODE in
replay)
    [ -f "$DSP" ] || { echo "no $DSP: make move first"; exit 1; }
    cc -O1 -std=gnu11 -Wall -Wextra -o build/gate_m/move_replay.tmp move/tests/move_replay.c -ldl
    mv -f build/gate_m/move_replay.tmp build/gate_m/move_replay
    RUN='mkdir -p "$(dirname build/gate_m/replay/$0)" && build/gate_m/move_replay "$DSP" "$1" "build/gate_m/replay/$0.wav" > "build/gate_m/replay/$0.log" 2>&1 || { echo "FAIL $0 (exit $?)"; cat "build/gate_m/replay/$0.log"; }' ;;
host16)
    cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST $DEFS -DAPP_VER='"dev"' -I"$SRC/src" -I"$SRC/gen" \
      -o "build/gate_m/$TAG.bin" "$SRC/src/frodhes.c" -lm 2> "build/gate_m/$TAG.build.log"
    RUN='mkdir -p "$(dirname build/gate_m/'"$TAG"'/$0)" && build/gate_m/'"$TAG"'.bin "build/gate_m/'"$TAG"'/$0" "$1" > "build/gate_m/'"$TAG"'/$0.log" 2>&1 || true' ;;
*) echo "usage: gate_m.sh replay|host16 LIST JOBS"; exit 2 ;;
esac
xargs -P "$JOBS" -L 1 sh -c "$RUN" < "$LIST"
rm -rf build/gate_m/t/run.*
