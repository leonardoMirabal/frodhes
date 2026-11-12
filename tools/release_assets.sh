#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# release_assets.sh X.Y.Z [NAMES]: release.yml's last check before the draft goes public. Every asset a Frodhes
# release must carry is on it, or exit 1 and the release stays a draft (v0.9.11, 2026-11-12, went public without
# its plugin packages). NAMES is a file with one asset name per line; without it, the assets of the draft vX.Y.Z
# (gh release view). Required: the firmware packages (versioned and fixed names), the Move package, the 5 plugin
# packages, the licence, and every releases/latest/download/<name> that README.md, docs/*.md or web/ links.
set -eu
V=$1
NAMES=${2:-}
if [ -z "$NAMES" ]; then
    NAMES=$(mktemp)
    gh release view "v$V" --json assets -q '.assets[].name' > "$NAMES"
fi
REQ="frodhes-$V.fwsc frodhes.fwsc frodhes-$V-c1.fwsc frodhes-c1.fwsc
frodhes-$V-smk37-pro.fwsc frodhes-smk37-pro.fwsc frodhes-$V-smk37-elite.fwsc frodhes-smk37-elite.fwsc
frodhes-$V-mke-p37.fwsc frodhes-mke-p37.fwsc frodhes-$V-move.tar.gz frodhes-module.tar.gz
frodhes-plugin-macos.pkg frodhes-plugin-macos.zip frodhes-plugin-windows.zip
frodhes-plugin-linux-x86_64.zip frodhes-plugin-linux-aarch64.zip Apache-2.0.txt"
LINKED=$(cat README.md docs/*.md web/*.html web/*.js 2>/dev/null \
    | grep -oE 'releases/latest/download/[A-Za-z0-9._-]+' | sed 's|.*/||' | sort -u)
miss=""
# shellcheck disable=SC2086  # split the name lists into words on purpose
for n in $(printf '%s\n' $REQ $LINKED | sort -u); do
    grep -qxF "$n" "$NAMES" || miss="$miss $n"
done
if [ -n "$miss" ]; then
    echo "release_assets: v$V is missing:$miss; not published" >&2
    exit 1
fi
echo "release_assets: v$V carries every required asset ($(wc -l < "$NAMES" | tr -d ' ') assets)"
