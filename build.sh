#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
# Build Frodhes (see README.md).
#   ./build.sh [--release X.Y] [--board fm1|smk37] [--model pro|elite|mke]
#   BOARD            target board: fm1 (default) or smk37 (M-Vave SMK-37 -> build/frodhes-smk37.fwsc, SMK37.md)
#   MODEL            smk37: pro (default), elite or mke: the model name the keyboard reports
#   JIELI_TOOLCHAIN  JieLi Linux toolchain (default: .local/jieli/toolchain)
#   AC79_SDK         JieLi AC79 SDK checkout (default: .local/fw-AC79_AIoT_SDK)
set -e
cd "$(dirname "$0")"
export JIELI_TOOLCHAIN="${JIELI_TOOLCHAIN:-$PWD/.local/jieli/toolchain}"   # project-local (tools/get_toolchain.sh .local/jieli)
export AC79_SDK="${AC79_SDK:-$PWD/.local/fw-AC79_AIoT_SDK}"            # project-local: only 3 files are needed
PY="${PYTHON:-python3}"

"$PY" -c 'import PIL' 2>/dev/null || { echo "build.sh: $PY has no Pillow (pip3 install Pillow)"; exit 1; }
[ -x "$JIELI_TOOLCHAIN/pi32v2/bin/clang" ] || { echo "build.sh: no toolchain in $JIELI_TOOLCHAIN (run tools/get_toolchain.sh)"; exit 1; }
[ -f "$AC79_SDK/cpu/wl82/tools/uboot.boot" ] || { echo "build.sh: no JieLi AC79 SDK in $AC79_SDK"; exit 1; }
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    docker info >/dev/null 2>&1 || { echo "build.sh: Docker is not running"; exit 1; }
fi
export BOARD="${BOARD:-fm1}"     # read by tools/build.py (--board overrides it)
exec "$PY" tools/build.py "$@"
