#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# web_firmware.sh FWSC DIST X.Y.Z [SMK.fwsc ...]: the release's packages next to the emulator, for the page's
# installer (web/install.js): FWSC is the FM-1's (DIST/frodhes.fwsc); each further one an SMK-37 build, under its
# release name (SMK-37 Pro_470 -> frodhes-smk37-experimental.fwsc, ...). DIST/firmware.json: {version, identity, size,
# sha256, file} of the FM-1's (as before) and packages: [{identity, file, size, sha256}, ...], the FM-1's first.
# GitHub's release downloads send no CORS headers, so the page fetches its own copy from Pages and checks it
# against this sha256. Used by release.yml (job release, before tools/deploy_pages.sh) and tools/release.py step 8's
# local fallback.
set -e
FWSC="$1"; DIST="$2"; VER="$3"
[ -s "$FWSC" ] && [ -d "$DIST" ] && [ -n "$VER" ] || { echo "usage: web_firmware.sh FWSC DIST X.Y.Z [SMK.fwsc ...]" >&2; exit 2; }
shift 3
cd "$(dirname "$0")/.."
python3 - "$DIST" "$VER" "$FWSC" "$@" <<'PY'
import hashlib, json, shutil, sys
sys.path.insert(0, "tools")
import fm1_install as I
dist, ver, fm1, smk = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
NAMES = {"SMK-37 Pro_470": "frodhes-smk37-experimental.fwsc", "SMK-37 Elite_470": "frodhes-smk37-elite-experimental.fwsc",
         "MKE-P37_470": "frodhes-smk37-mke-experimental.fwsc"}   # web/fm1install.js PRODUCTS, the release's names
packages = []
for i, fwsc in enumerate([fm1] + smk):
    raw = open(fwsc, "rb").read()
    product, _image = I.load_package(fwsc, False)
    want = ["FM-1_958"] if i == 0 else list(NAMES)
    if product not in want or any(p["identity"] == product for p in packages):
        sys.exit(f"web_firmware.sh: {fwsc} is {product}, not {' / '.join(want)} (or a second package for it)")
    packages.append({"identity": product, "file": "frodhes.fwsc" if i == 0 else NAMES[product], "size": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest(), "src": fwsc})
for p in packages:
    shutil.copyfile(p.pop("src"), f"{dist}/{p['file']}")
    print(f"{dist}/{p['file']}: {p['identity']} {ver}, {p['size']} B, sha256 {p['sha256']}")
meta = {"version": ver, **packages[0], "packages": packages}
open(f"{dist}/firmware.json", "w").write(json.dumps(meta, indent=1) + "\n")
PY
