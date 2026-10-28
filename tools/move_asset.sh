#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# move_asset.sh X.Y.Z TARBALL OUTDIR: release.yml's Move package (private plan 05 M7-02). The release commit (the one
# that added docs/releases/vX.Y.Z.md, tools/release.py step 5) records "Move package sha256 <64 hex>" only when the
# Move's device tiers passed on that exact tarball. Then:
#   recorded, and TARBALL (built here from the tag, reproducible: make move) has that sha256: OUTDIR gets
#     frodhes-X.Y.Z-move.tar.gz and frodhes-module.tar.gz (the stable name), exit 0
#   recorded, a different sha256: not the tarball that was tested: exit 1 (the release stops)
#   nothing recorded (the Move not tested for this release): OUTDIR stays empty, exit 0 (no Move asset)
# Needs the history (actions/checkout fetch-depth: 0).
set -eu
V=$1 TAR=$2 OUT=$3
NOTES="docs/releases/v$V.md"
mkdir -p "$OUT"
RC=$(git log -1 --format=%H --diff-filter=A -- "$NOTES" 2>/dev/null || true)
WANT=""
if [ -n "$RC" ]; then
    WANT=$(git log -1 --format=%B "$RC" | sed -n 's/.*Move package sha256 \([0-9a-f]\{64\}\).*/\1/p' | head -n 1)
fi
if [ -z "$WANT" ]; then
    echo "move_asset: the release commit (${RC:-none}) records no Move package: no Move asset (the Move was not tested)"
    exit 0
fi
[ -f "$TAR" ] || { echo "move_asset: $TAR missing (the release commit records the Move package $WANT)" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then GOT=$(sha256sum "$TAR" | cut -d' ' -f1)
else GOT=$(shasum -a 256 "$TAR" | cut -d' ' -f1); fi
if [ "$GOT" != "$WANT" ]; then
    echo "move_asset: $TAR sha256 $GOT is not the tested Move package's $WANT (release commit ${RC}): stop" >&2
    exit 1
fi
cp "$TAR" "$OUT/frodhes-$V-move.tar.gz"
cp "$TAR" "$OUT/frodhes-module.tar.gz"
echo "move_asset: $GOT = the tested Move package: frodhes-$V-move.tar.gz and frodhes-module.tar.gz"
