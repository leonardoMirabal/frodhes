# SPDX-License-Identifier: GPL-3.0-only
"""The package identity stays FM-1_958 for every FM-1 build (dev and release), through every Dinghy merge.

The hub and the installers tell Frodhes from the other firmwares by it (950 and 953 are others'); Dinghy 0.4's
build.py derives FM-1_99N from the release instead, which must never reach an FM-1 build of Frodhes."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build_py():
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("frodhes_build", ROOT / "tools" / "build.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fm1_identity_every_build():
    b = build_py()
    assert b.PRODUCT == "FM-1_958"
    assert b.package() == ("FM-1_958", "frodhes.fwsc")
    for v in ("0.9.5", "0.9.10", "1.0", "0.9.5-rc1"):
        assert b.package("fm1", "pro", v) == ("FM-1_958", f"frodhes-{v}.fwsc")


def test_smk_builds_never_take_the_fm1_identity():
    b = build_py()
    for model in b.MODELS:
        ident, name = b.package("smk37", model)
        assert ident != "FM-1_958" and not ident.startswith("FM-1")
        assert name.startswith("frodhes-smk37")


def test_release_smk_assets_are_the_release_builds():
    """tools/release.py SMK_ASSETS (release.yml's experimental SMK-37 assets) are build.py's release packages of the
    three models, each under its own identity (SMK-37 Pro_470, SMK-37 Elite_470, MKE-P37_470), never the FM-1's"""
    b = build_py()
    import release
    want = {"pro": ("SMK-37 Pro_470", "frodhes-smk37-experimental.fwsc"),
            "elite": ("SMK-37 Elite_470", "frodhes-smk37-elite-experimental.fwsc"),
            "mke": ("MKE-P37_470", "frodhes-smk37-mke-experimental.fwsc")}
    assert list(release.SMK_ASSETS) == [want[m][1] for m in ("pro", "elite", "mke")]
    for model, (ident, pub) in want.items():
        assert b.package("smk37", model, "0.9.9") == (ident, release.SMK_ASSETS[pub].format(v="0.9.9"))
    assert b.package("fm1", "pro", "0.9.9") == ("FM-1_958", "frodhes-0.9.9.fwsc")    # the FM-1's unchanged


def test_main_sets_the_identity_only_through_package():
    """main() may not assign the identity any other way (Dinghy's `PRODUCT = BOARD_PREFIX[...] + m[2]`)"""
    src = (ROOT / "tools" / "build.py").read_text()
    main = src[src.index("\ndef main():"):]
    assert re.findall(r"^\s*PRODUCT\b[^=\n]*=", main, re.M) == ["    PRODUCT, name ="]


def test_built_package_carries_it():
    """the last firmware build, when there is one (./build.sh): the package's identity as the installer reads it, and
    the app's own answer to the hub's handshake (FELUCCA_ID in the app image), no Dinghy FM-1_99N anywhere"""
    pkg, app = ROOT / "build" / "frodhes.fwsc", ROOT / "build" / "frodhes.bin"
    if not pkg.exists() or not app.exists():
        import pytest
        pytest.skip("no build/frodhes.fwsc (./build.sh)")
    sys.path.insert(0, str(ROOT / "tools"))
    import fm1_install
    assert fm1_install.product_of(pkg.read_bytes()) == "FM-1_958"
    img = app.read_bytes()
    assert b"FM-1_958\0" in img
    assert not re.search(rb"FM-1_99\d", img)
