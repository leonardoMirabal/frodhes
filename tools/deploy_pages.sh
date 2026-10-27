#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# deploy_pages.sh DIST PAGES X.Y.Z: the emulator (web/build.sh's DIST, e.g. web/dist) onto the gh-pages checkout
# PAGES. Every file of DIST is copied into the branch root; the branch's other files (.nojekyll ...) stay. A commit
# "Emulator X.Y.Z" only when something changed, then a push of gh-pages (a no-op when nothing did): a rerun is
# harmless. Used by release.yml (job release, on a v* tag, before the release leaves draft) and by tools/release.py step 8's local fallback.
# DEPLOY_PAGES_PUSH=0 commits without pushing (the tests).
set -e
DIST="$1"; PAGES="$2"; VER="$3"
[ -n "$DIST" ] && [ -n "$PAGES" ] && [ -n "$VER" ] || { echo "usage: deploy_pages.sh DIST PAGES X.Y.Z" >&2; exit 2; }
for f in index.html app.js fm1install.js install.js version.js frodhes.js frodhes.wasm; do
  [ -s "$DIST/$f" ] || { echo "deploy_pages.sh: $DIST/$f missing (run web/build.sh)" >&2; exit 1; }
done
# the page shows the version it was built with: it must be this one (web/build.sh --release X.Y.Z)
grep -qx "window.FRODHES_VERSION = \"$VER\";" "$DIST/version.js" ||
  { echo "deploy_pages.sh: $DIST/version.js is not $VER (web/build.sh --release $VER)" >&2; exit 1; }
[ "$(git -C "$PAGES" rev-parse --abbrev-ref HEAD)" = gh-pages ] || { echo "$PAGES is not on gh-pages" >&2; exit 1; }
cp "$DIST"/* "$PAGES"/
git -C "$PAGES" add -A
if [ -n "$(git -C "$PAGES" status --porcelain)" ]; then
  git -C "$PAGES" commit -q -m "Emulator $VER"
  echo "gh-pages: committed Emulator $VER ($(git -C "$PAGES" rev-parse --short HEAD))"
else
  echo "gh-pages: already up to date with $DIST (no commit)"
fi
[ "${DEPLOY_PAGES_PUSH:-1}" = 0 ] || git -C "$PAGES" push -q origin HEAD:gh-pages
