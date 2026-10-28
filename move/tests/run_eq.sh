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
# the load's HQ Rhodes (move_bridge.h mb_start: the Move's one engine): the plugin as it loads, no p_hq written
# (MOVE_EQ_HQ=boot), equals the host build with HQ on, which is not the Classic render
case "$DEFS" in *FR_HQ*)
  cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST $DEFS -DHQ_DEFAULT=1 -DAPP_VER='"0.0.0-dev"' -Ifirmware/src -Ifirmware/gen \
    -o build/move/t/host-hq firmware/src/frodhes.c -lm 2> build/move/t/host-hq.build.log
  s=move/tests/scripts/bridge_eq.txt
  build/move/t/host build/move/t/eq-classic "$s" > /dev/null 2>&1 || { echo "FAIL host run eq-classic"; exit 1; }
  build/move/t/host-hq build/move/t/eq-hq "$s" > build/move/t/eq-hq.host.log 2>&1 || { echo "FAIL host-hq run"; exit 1; }
  if cmp -s build/move/t/eq-classic.wav build/move/t/eq-hq.wav; then echo "FAIL eq hq: the HQ host render is the Classic one"; exit 1; fi
  MOVE_EQ_HQ=boot build/move/t/test_bridge eq build/move/frodhes/dsp.so "$s" build/move/t/eq-hq.wav
  echo "ok    eq hq: the plugin as it loads plays the HQ Rhodes (the HQ host build's render, not Classic's)"
  rm -f build/move/t/eq-classic.wav build/move/t/eq-hq.wav build/move/t/eq-classic-*.ppm build/move/t/eq-hq-*.ppm ;;
esac
# the dev player and tap: the same script started on the 2560 ms grid equals the host build on the script shifted
s=move/tests/scripts/bridge_eq.txt
awk '/^[0-9]/ { $1 = $1 + 2560 } { print }' "$s" > build/move/t/bridge_eq_2560.txt
build/move/t/host build/move/t/play "build/move/t/bridge_eq_2560.txt" > build/move/t/play.host.log 2>&1 || { echo "FAIL host run play"; exit 1; }
build/move/t/test_bridge play build/move/frodhes/dsp.so "$s" build/move/t/play.wav
rm -f build/move/t/play.wav build/move/t/play-*.ppm build/move/t/bridge_eq_2560.txt
