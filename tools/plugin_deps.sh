#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin's dependencies (plugin/deps.lock), fetched into the project: .local/plugin-deps/<name>
# (or $FRP_DEPS_DIR). Each git dependency is a shallow checkout of the pinned commit, checked after the fetch; each
# zip is checked by its sha256 before it is unpacked (into its own empty folder). Run again: what is there and right
# is kept, anything else is fetched again. No network access happens in the CMake build itself.
#   tools/plugin_deps.sh            everything (the libraries and the test tools)
#   tools/plugin_deps.sh --libs     the libraries only (clap, clap-wrapper, the VST3 SDK, the AudioUnit SDK)
#   tools/plugin_deps.sh --mingw    the libraries and llvm-mingw (the Windows cross toolchain, tools/plugin_pack.sh)
# The test tools and the toolchain come in this system's build (a name@os line in deps.lock); FRP_DEPS_OS=macos,
# linux or windows picks another system's.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOCK="$ROOT/plugin/deps.lock"
DEPS=${FRP_DEPS_DIR:-"$ROOT/.local/plugin-deps"}
LIBS_ONLY=0 MINGW=0
[ "${1:-}" = "--libs" ] && LIBS_ONLY=1
[ "${1:-}" = "--mingw" ] && LIBS_ONLY=1 && MINGW=1
case "${FRP_DEPS_OS:-$(uname -s)}" in
    Darwin|macos) OS=macos ;;
    Linux|linux) OS=linux ;;
    MINGW*|MSYS*|CYGWIN*|windows) OS=windows ;;
    *) OS=unknown ;;
esac
sha256() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1; }
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

arc_dep() {   # kind name url sha256 (zip: unpacked as it is; tc: a .zip or .tar.xz, its one top folder stripped)
    d="$DEPS/$2"
    if [ -f "$d/.sha256" ] && [ "$(cat "$d/.sha256")" = "$4" ]; then echo "ok    $2 $4"; return 0; fi
    rm -rf "$d" "$DEPS/_dl/$2"
    mkdir -p "$DEPS/_dl/$2" "$d"
    curl -sSLf -o "$DEPS/_dl/$2/a.$1" "$3"
    got=$(sha256 "$DEPS/_dl/$2/a.$1")
    [ "$got" = "$4" ] || { echo "FAIL $2: sha256 $got, pinned $4"; exit 1; }
    if [ "$1" = tc ]; then
        case $3 in
        *.tar.xz) tar -xJf "$DEPS/_dl/$2/a.tc" -C "$d" --strip-components 1 ;;
        *) (cd "$DEPS/_dl/$2" && unzip -q a.tc -d x) && mv "$DEPS/_dl/$2"/x/*/* "$d"/ ;;
        esac
    else
        (cd "$d" && unzip -q "$DEPS/_dl/$2/a.zip")
        for t in "$d"/*.tar.gz; do if [ -f "$t" ]; then (cd "$d" && tar -xzf "$t" && rm -f "$t"); fi; done
    fi
    if [ "$OS" = macos ]; then xattr -dr com.apple.quarantine "$d" 2>/dev/null || true; fi
    echo "$4" > "$d/.sha256"
    rm -rf "$DEPS/_dl/$2"
    echo "ok    $2 $4 (fetched)"
}

grep -v '^#' "$LOCK" | while read -r kind name url ref pin lic; do
    [ -n "${kind:-}" ] || continue
    case $name in *@*) [ "${name#*@}" = "$OS" ] || continue; name=${name%@*} ;; esac
    case $kind in
    git) git_dep "$name" "$url" "$pin" ;;
    zip) [ $LIBS_ONLY = 1 ] || arc_dep zip "$name" "$url" "$pin" ;;
    tc) if [ $MINGW = 1 ]; then arc_dep tc "$name" "$url" "$pin"; fi ;;
    esac
done
