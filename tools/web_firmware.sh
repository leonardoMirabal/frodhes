#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# web_firmware.sh FWSC DIST X.Y.Z: the release's FM-1 package next to the emulator, for the page's installer
# (web/install.js): DIST/frodhes.fwsc and DIST/firmware.json ({version, identity, size, sha256}). GitHub's release
# downloads send no CORS headers, so the page fetches its own copy from Pages and checks it against this sha256.
# Used by release.yml (job release, before tools/deploy_pages.sh) and tools/release.py step 8's local fallback.
set -e
FWSC="$1"; DIST="$2"; VER="$3"
[ -s "$FWSC" ] && [ -d "$DIST" ] && [ -n "$VER" ] || { echo "usage: web_firmware.sh FWSC DIST X.Y.Z" >&2; exit 2; }
cd "$(dirname "$0")/.."
python3 - "$FWSC" "$DIST" "$VER" <<'PY'
import hashlib, json, shutil, sys
sys.path.insert(0, "tools")
import fm1_install as I
fwsc, dist, ver = sys.argv[1:4]
raw = open(fwsc, "rb").read()
product, _image = I.load_package(fwsc, False)
if product != "FM-1_958":
    sys.exit(f"web_firmware.sh: {fwsc} is {product}, not FM-1_958 (the page installs the FM-1 package only)")
shutil.copyfile(fwsc, f"{dist}/frodhes.fwsc")
meta = {"version": ver, "identity": product, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "file": "frodhes.fwsc"}
open(f"{dist}/firmware.json", "w").write(json.dumps(meta, indent=1) + "\n")
print(f"{dist}/frodhes.fwsc: {product} {ver}, {len(raw)} B, sha256 {meta['sha256']}")
PY
