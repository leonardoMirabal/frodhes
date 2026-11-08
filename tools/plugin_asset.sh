#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# plugin_asset.sh X.Y.Z INDIR OUTDIR: release.yml's desktop plugin packages (as tools/move_asset.sh for the Move). The
# release commit (the one that added docs/releases/vX.Y.Z.md) records the platforms whose packages were tested, in a
# line "Plugin tested: macos windows linux-x86_64 linux-aarch64" (any of them). INDIR holds the packages the release
# workflow built from the tag (.github/workflows/plugin.yml). Then:
#   each platform listed: its packages are copied to OUTDIR under their fixed names (the version only in the GitHub
#     release): macos frodhes-plugin-macos.pkg + frodhes-plugin-macos.zip, windows frodhes-plugin-windows.zip,
#     linux-x86_64 frodhes-plugin-linux-x86_64.zip, linux-aarch64 frodhes-plugin-linux-aarch64.zip; a listed
#     package missing from INDIR, or an unknown name: exit 1 (no plugin asset is attached)
#   no such line: OUTDIR stays empty, exit 0 (the plugin was not tested for this release: no plugin asset)
# Needs the history (actions/checkout fetch-depth: 0).
set -eu
V=$1 IN=$2 OUT=$3
NOTES="docs/releases/v$V.md"
mkdir -p "$OUT"
RC=$(git log -1 --format=%H --diff-filter=A -- "$NOTES" 2>/dev/null || true)
LIST=""
if [ -n "$RC" ]; then
    LIST=$(git log -1 --format=%B "$RC" | sed -n 's/^Plugin tested:[[:space:]]*\(.*\)$/\1/p' | head -n 1)
fi
if [ -z "$LIST" ]; then
    echo "plugin_asset: the release commit (${RC:-none}) records no tested plugin: no plugin asset"
    exit 0
fi
FILES=""
for p in $LIST; do
    case $p in
    macos) FILES="$FILES frodhes-plugin-macos.pkg frodhes-plugin-macos.zip" ;;
    windows) FILES="$FILES frodhes-plugin-windows.zip" ;;
    linux-x86_64|linux-aarch64) FILES="$FILES frodhes-plugin-$p.zip" ;;
    *) echo "plugin_asset: unknown platform '$p' in the release commit's 'Plugin tested:' line" >&2; exit 1 ;;
    esac
done
for f in $FILES; do
    [ -f "$IN/$f" ] || { echo "plugin_asset: $f missing from $IN (the release commit lists it as tested)" >&2; exit 1; }
done
for f in $FILES; do cp "$IN/$f" "$OUT/$f"; echo "plugin_asset: $f"; done
