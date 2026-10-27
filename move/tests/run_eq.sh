#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# make test-move: the bridge equals the host build on move/tests/scripts/*.txt (MIDI-only scripts). Runs in the
# Move image (arm64): the host build with the Move's voice count, then each script on both paths.
set -e
DEFS=${MOVE_DEFS:--DFR_VOICES=16u}
mkdir -p build/move/t
cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST $DEFS -DAPP_VER='"0.0.0-dev"' -Ifirmware/src -Ifirmware/gen \
  -o build/move/t/host firmware/src/frodhes.c -lm 2> build/move/t/host.build.log
for s in move/tests/scripts/*.txt; do
  n=$(basename "$s" .txt)
  build/move/t/host "build/move/t/$n" "$s" > "build/move/t/$n.host.log" 2>&1 || { echo "FAIL host run $n"; exit 1; }
  build/move/t/test_bridge eq build/move/frodhes/dsp.so "$s" "build/move/t/$n.wav"
  rm -f "build/move/t/$n.wav" build/move/t/$n-*.ppm
done
