#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Run firmware/src/frodhes.c on this computer (tests/host.c): build/host/frodhes.wav and the screens as PNG.
set -e
cd "$(dirname "$0")/.."
mkdir -p build/host
cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST -DAPP_VER="\"${APP_VER:-dev}\"" -Ifirmware/src -Ifirmware/gen -o build/host/frodhes firmware/src/frodhes.c -lm
build/host/frodhes build/host/frodhes "$@"
python3 - <<'PY'
from pathlib import Path
from PIL import Image
for p in sorted(Path("build/host").glob("frodhes-*.ppm")):
    Image.open(p).resize((480, 480), Image.NEAREST).save(p.with_suffix(".png"))
    p.unlink()
    print("screen ->", p.with_suffix(".png"))
PY
