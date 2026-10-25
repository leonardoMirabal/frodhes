#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Run firmware/src/frodhes.c on this computer (tests/host.c): build/host/frodhes.wav and the screens as PNG.
#   tests/run_host.sh [script]               your own script (tests/host.c says what a line can do)
#   BOARD=smk37 tests/run_host.sh [script]   the SMK-37 build (7 voices; velocity, pads, faders, wheels, the outer
#                                            keys): build/host-smk/frodhes.wav and its screens
set -e
cd "$(dirname "$0")/.."
D=build/host; DEFS=
if [ "${BOARD:-fm1}" = smk37 ]; then D=build/host-smk; DEFS="-DBOARD_SMK37=1 -DFR_VOICES=7u"; fi
mkdir -p "$D"
cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST $DEFS -DAPP_VER="\"${APP_VER:-dev}\"" -Ifirmware/src -Ifirmware/gen -o "$D/frodhes" firmware/src/frodhes.c -lm
"$D/frodhes" "$D/frodhes" "$@"
python3 - "$D" <<'PY'
import sys
from pathlib import Path
from PIL import Image
for p in sorted(Path(sys.argv[1]).glob("frodhes-*.ppm")):
    Image.open(p).resize((480, 480), Image.NEAREST).save(p.with_suffix(".png"))
    p.unlink()
    print("screen ->", p.with_suffix(".png"))
PY
