#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Install Emscripten (the emulator's compiler, web/build.sh) at the pinned version into DEST (default .local/emsdk,
# where web/build.sh looks first). Used by ci.yml (job web) and release.yml (job web); the same version as the
# development machine. A DEST that already holds this version (a CI cache hit) is only activated.
#   tools/get_emsdk.sh [DEST]
set -e
EMSDK_VERSION=3.1.74
DEST="${1:-.local/emsdk}"
if [ "$(cat "$DEST/upstream/emscripten/emscripten-version.txt" 2>/dev/null)" = "\"$EMSDK_VERSION\"" ]; then
  echo "emsdk $EMSDK_VERSION already in $DEST"
else
  [ -d "$DEST/.git" ] || git clone -q --depth 1 https://github.com/emscripten-core/emsdk.git "$DEST"
  "$DEST/emsdk" install "$EMSDK_VERSION"
fi
"$DEST/emsdk" activate "$EMSDK_VERSION" >/dev/null
. "$DEST/emsdk_env.sh" >/dev/null 2>&1
emcc --version | head -1
