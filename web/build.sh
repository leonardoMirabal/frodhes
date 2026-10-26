#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The Frodhes emulator: firmware/src/frodhes.c compiled to WebAssembly with Emscripten (the browser shim is
# web/web_hal.c). Output: web/dist/ (index.html, app.js, frodhes.js, frodhes.wasm), a static site.
#
#   web/build.sh            (Emscripten from .local/emsdk, or emcc on the PATH)
#   python3 -m http.server -d web/dist 8000     then open http://localhost:8000
set -e
cd "$(dirname "$0")/.."
if [ -f .local/emsdk/emsdk_env.sh ]; then . .local/emsdk/emsdk_env.sh >/dev/null 2>&1; fi
mkdir -p web/dist
emcc -O2 -DDINGHY_HOST -DDINGHY_WEB -DAPP_VER="\"web\"" -Ifirmware/src -Ifirmware/gen \
  -Wno-unused-function -Wno-unused-variable \
  firmware/src/frodhes.c -o web/dist/frodhes.js \
  -sASYNCIFY -sASYNCIFY_IMPORTS='["emscripten_sleep"]' -sALLOW_MEMORY_GROWTH=0 -sINITIAL_MEMORY=33554432 \
  -sEXPORTED_FUNCTIONS='["_main","_web_audio","_web_btn","_web_keys","_web_enc","_web_midi","_web_master","_web_fb","_web_leds","_web_store","_web_store_n","_web_store_take_dirty","_web_page"]' \
  -sEXPORTED_RUNTIME_METHODS='["HEAPU8","HEAPF32"]' -sENVIRONMENT=web
cp web/index.html web/app.js web/dist/
echo "web/dist: $(ls web/dist | tr '\n' ' ')"
