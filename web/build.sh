#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The Frodhes emulator: firmware/src/frodhes.c compiled to WebAssembly with Emscripten (the browser shim is
# web/web_hal.c). Output: web/dist/ (index.html, app.js, version.js, frodhes.js, frodhes.wasm), a static site.
#
#   web/build.sh [--release X.Y.Z]   (Emscripten from .local/emsdk, or emcc on the PATH)
# The version is stamped at build time, never written in the repo: --release X.Y.Z (release.yml passes the tag's)
# shows "Frodhes X.Y.Z" in the page's title, heading and footer (web/dist/version.js) and on the emulated screen
# (APP_VER); without it the build is "dev" (the screen shows DEV).
#   python3 -m http.server -d web/dist 8000     then open http://localhost:8000
set -e
cd "$(dirname "$0")/.."
VER=dev
if [ "$1" = "--release" ]; then
  VER="$2"
  echo "$VER" | grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?(-[A-Za-z0-9.]+)?$' || { echo "--release $VER: use X.Y.Z" >&2; exit 2; }
fi
if [ -f .local/emsdk/emsdk_env.sh ]; then . .local/emsdk/emsdk_env.sh >/dev/null 2>&1; fi
mkdir -p web/dist
emcc -O2 -DDINGHY_HOST -DDINGHY_WEB -DHOST_SAVE_MS=1u -DAPP_VER="\"$VER\"" -Ifirmware/src -Ifirmware/gen \
  -Wno-unused-function -Wno-unused-variable \
  firmware/src/frodhes.c -o web/dist/frodhes.js \
  -sASYNCIFY -sASYNCIFY_IMPORTS='["emscripten_sleep"]' -sALLOW_MEMORY_GROWTH=0 -sINITIAL_MEMORY=33554432 \
  -sEXPORTED_FUNCTIONS='["_main","_web_audio","_web_btn","_web_keys","_web_enc","_web_midi","_web_master","_web_fb","_web_leds","_web_store","_web_store_take_dirty","_web_page"]' \
  -sEXPORTED_RUNTIME_METHODS='["HEAPU8","HEAPF32"]' -sENVIRONMENT=web
cp web/index.html web/app.js web/dist/
printf 'window.FRODHES_VERSION = "%s";\n' "$VER" > web/dist/version.js
echo "web/dist: $(ls web/dist | tr '\n' ' ')"
