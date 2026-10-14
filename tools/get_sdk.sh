#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Fetch the 3 files of the JieLi AC79 SDK (Apache-2.0) the build needs, into DEST (default .local/fw-AC79_AIoT_SDK).
# tools/build.py checks them against pinned sha256 values.
set -e
DEST="${1:-.local/fw-AC79_AIoT_SDK}"
B=https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK/raw/AC79NN_SDK_V1.2.1_2023-12-13/cpu/wl82/tools
D="$DEST/cpu/wl82/tools"
mkdir -p "$D/cfg"
for f in uboot.boot cfg_tool.bin cfg/eq_cfg_hw.bin; do
    [ -f "$D/$f" ] || curl -fsSL --retry 3 -o "$D/$f" "$B/$f"
done
echo "AC79_SDK=$DEST"
