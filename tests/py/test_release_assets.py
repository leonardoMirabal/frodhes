# SPDX-License-Identifier: GPL-3.0-only
"""tools/release_assets.sh, release.yml's last check before the draft goes public: every required asset is on it
(v0.9.11 went public without its plugin packages). Host only, on asset-name lists (no GitHub)."""
import os
import subprocess

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
V = "9.9.9"
FULL = ["frodhes-%s.fwsc" % V, "frodhes.fwsc", "frodhes-%s-c1.fwsc" % V, "frodhes-c1.fwsc",
        "frodhes-%s-smk37-pro.fwsc" % V, "frodhes-smk37-pro.fwsc", "frodhes-%s-smk37-elite.fwsc" % V,
        "frodhes-smk37-elite.fwsc", "frodhes-%s-mke-p37.fwsc" % V, "frodhes-mke-p37.fwsc",
        "frodhes-%s-move.tar.gz" % V, "frodhes-module.tar.gz", "frodhes-plugin-macos.pkg",
        "frodhes-plugin-macos.zip", "frodhes-plugin-windows.zip", "frodhes-plugin-linux-x86_64.zip",
        "frodhes-plugin-linux-aarch64.zip", "frodhes-%s-mpc.zip" % V, "frodhes-mpc.zip",
        "Frodhes-%s-mpc-armv7.zip" % V, "Frodhes-%s-mpc-aarch64.zip" % V,
        "Apache-2.0.txt"]


def check(tmp_path, names):
    f = tmp_path / "names"
    f.write_text("".join(n + "\n" for n in names))
    return subprocess.run(["sh", "tools/release_assets.sh", V, str(f)], cwd=ROOT, capture_output=True, text=True)


def test_a_full_release_passes(tmp_path):
    r = check(tmp_path, FULL)
    assert r.returncode == 0, r.stderr


def test_any_missing_asset_fails(tmp_path):
    for n in FULL:                                  # the v0.9.11 case: each plugin package, and every other one
        r = check(tmp_path, [m for m in FULL if m != n])
        assert r.returncode == 1 and n in r.stderr, n


def test_release_yml_ships_the_hub_c1_asset():
    """The Firmware Hub follows frodhes-c1.fwsc: 0.9.12-0.9.13 lacked it and the hub stayed on 0.9.11"""
    run = "\n".join(s.get("run", "") for s in yaml.safe_load(open(os.path.join(
        ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"])
    assert 'cp "build/frodhes-$V.fwsc" build/publish/frodhes-c1.fwsc' in run
    assert 'cp "build/frodhes-$V.fwsc" "build/publish/frodhes-$V-c1.fwsc"' in run
    assert 'cp "build/frodhes-$V-c1.fwsc" "build/frodhes-$V.fwsc"' in run     # the two-core build as frodhes.fwsc


def test_release_yml_checks_before_publishing():
    st = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    names = [s.get("name", "") for s in st]
    chk = next(i for i, s in enumerate(st) if "tools/release_assets.sh" in s.get("run", ""))
    assert chk == names.index("publish the release") - 1
    att = next(s for s in st if "tools/plugin_asset.sh" in s.get("run", ""))
    assert "exit 0" not in att["run"]               # no plugin asset is a failure, never a silent skip


NAMES = os.path.join(ROOT, "tools", "release_asset_names.txt")


def names(text):
    return [n for n in text.splitlines() if n and not n.startswith("#")]


def test_the_asset_names_are_append_only():
    """every name the last release's tools/release_asset_names.txt listed is still listed: an asset name, once
    published, is never dropped or renamed (0.9.12 dropped frodhes-X.Y.Z-c1.fwsc, the name The Firmware Hub follows,
    and the hub stayed on 0.9.11)"""
    last = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    old = subprocess.run(["git", "show", f"{last}:tools/release_asset_names.txt"], cwd=ROOT, capture_output=True,
                         text=True)
    base = names(old.stdout) if old.returncode == 0 else [n.replace(V, "{v}") for n in FULL]   # before the list
    gone = sorted(set(base) - set(names(open(NAMES).read())))
    assert not gone, f"asset names dropped since {last or 'the list began'}: {gone} (append only)"


def test_release_yml_publishes_every_listed_fwsc():
    run = "\n".join(s.get("run", "") for s in yaml.safe_load(open(os.path.join(
        ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"])
    for n in names(open(NAMES).read()):
        if n.endswith(".fwsc"):
            assert n.replace("{v}", "$V") in run or n.split("{v}")[-1] in run, n
