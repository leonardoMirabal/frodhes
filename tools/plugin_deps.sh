#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin's dependencies (plugin/deps.lock), fetched into the project: .local/plugin-deps/<name>
# (or $FRP_DEPS_DIR). Each git dependency is a shallow checkout of the pinned commit, checked after the fetch; each
# zip is checked by its sha256 before it is unpacked (into its own empty folder). Run again: what is there and right
# is kept, anything else is fetched again. No network access happens in the CMake build itself.
#   tools/plugin_deps.sh            everything (the libraries and the test tools)
#   tools/plugin_deps.sh --libs     the libraries only (clap, clap-wrapper, the VST3 SDK, the AudioUnit SDK)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOCK="$ROOT/plugin/deps.lock"
DEPS=${FRP_DEPS_DIR:-"$ROOT/.local/plugin-deps"}
LIBS_ONLY=0
[ "${1:-}" = "--libs" ] && LIBS_ONLY=1
mkdir -p "$DEPS"

git_dep() {   # name url commit
    d="$DEPS/$1"
    if [ -d "$d/.git" ] && [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$3" ]; then
        echo "ok    $1 $3"; return 0
    fi
    rm -rf "$d"
    git init -q "$d"
    git -C "$d" remote add origin "$2"
    git -C "$d" fetch -q --depth 1 origin "$3"
    git -C "$d" -c advice.detachedHead=false checkout -q FETCH_HEAD
    got=$(git -C "$d" rev-parse HEAD)
    [ "$got" = "$3" ] || { echo "FAIL $1: got $got, pinned $3"; exit 1; }
    if [ "$1" = vst3sdk ]; then   # (the four parts clap-wrapper builds; the commits the tag records)
        git -C "$d" submodule update -q --init --depth 1 base pluginterfaces public.sdk cmake
    fi
    echo "ok    $1 $3 (fetched)"
}

zip_dep() {   # name url sha256
    d="$DEPS/$1"
    if [ -f "$d/.sha256" ] && [ "$(cat "$d/.sha256")" = "$3" ]; then echo "ok    $1 $3"; return 0; fi
    rm -rf "$d" "$DEPS/_dl/$1"
    mkdir -p "$DEPS/_dl/$1" "$d"
    curl -sSLf -o "$DEPS/_dl/$1/a.zip" "$2"
    got=$(shasum -a 256 "$DEPS/_dl/$1/a.zip" | cut -d' ' -f1)
    [ "$got" = "$3" ] || { echo "FAIL $1: sha256 $got, pinned $3"; exit 1; }
    (cd "$d" && unzip -q "$DEPS/_dl/$1/a.zip")
    for t in "$d"/*.tar.gz; do [ -f "$t" ] && (cd "$d" && tar -xzf "$t" && rm -f "$t"); done
    echo "$3" > "$d/.sha256"
    rm -rf "$DEPS/_dl/$1"
    echo "ok    $1 $3 (fetched)"
}

grep -v '^#' "$LOCK" | while read -r kind name url ref pin lic; do
    [ -n "${kind:-}" ] || continue
    case $kind in
    git) git_dep "$name" "$url" "$pin" ;;
    zip) [ $LIBS_ONLY = 1 ] || zip_dep "$name" "$url" "$pin" ;;
    esac
done
