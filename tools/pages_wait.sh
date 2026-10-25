#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# pages_wait.sh SHA DIST URL: wait until GitHub Pages reports "built" for the gh-pages commit SHA (asking for a build
# once if none started for it), then until URL serves every file of DIST byte for byte (sha256). Used by release.yml
# (job release) between the gh-pages deploy and the release leaving draft: the emulator is live when the release is.
# Exit 1 on an errored build or a timeout (PAGES_WAIT_S, default 900 s; PAGES_POLL_S, default 15 s).
SHA="$1"; DIST="$2"; URL="$3"
REPO="${GH_REPO:-$GITHUB_REPOSITORY}"
WAIT="${PAGES_WAIT_S:-900}"
POLL="${PAGES_POLL_S:-15}"
t=0; asked=0
h() { if command -v sha256sum >/dev/null; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }
while :; do
  b=$(gh api "repos/$REPO/pages/builds/latest" --jq '.status + " " + .commit' 2>/dev/null)
  st="${b%% *}"; c="${b#* }"
  if [ "$c" = "$SHA" ]; then
    [ "$st" = built ] && { echo "Pages: built $SHA"; break; }
    [ "$st" = errored ] && { echo "Pages: the build of $SHA errored" >&2; exit 1; }
  elif [ "$asked" = 0 ] && [ "$t" -ge 120 ]; then
    echo "Pages: no build of $SHA after $t s (latest: ${b:-none}): requesting one"
    gh api -X POST "repos/$REPO/pages/builds" >/dev/null || true
    asked=1
  fi
  [ "$t" -ge "$WAIT" ] && { echo "Pages: $SHA not built after $WAIT s (latest: ${b:-none})" >&2; exit 1; }
  sleep "$POLL"; t=$((t + POLL))
done
while :; do
  bad=""
  for f in "$DIST"/*; do
    n=$(basename "$f")
    want=$(h < "$f")
    got=$(curl -fsSL "$URL$n?nocache=$(date +%s)" | h)
    [ "$want" = "$got" ] || bad="$bad $n"
  done
  [ -z "$bad" ] && { echo "live $URL: every file of $DIST byte for byte ($(ls "$DIST" | tr '\n' ' '))"; exit 0; }
  [ "$t" -ge "$WAIT" ] && { echo "live $URL still differs after $WAIT s:$bad" >&2; exit 1; }
  echo "live differs:$bad (retry in $POLL s)"
  sleep "$POLL"; t=$((t + POLL))
done
