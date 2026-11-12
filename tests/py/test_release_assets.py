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
        "frodhes-plugin-linux-aarch64.zip", "Apache-2.0.txt"]


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


def test_release_yml_checks_before_publishing():
    st = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    names = [s.get("name", "") for s in st]
    chk = next(i for i, s in enumerate(st) if "tools/release_assets.sh" in s.get("run", ""))
    assert chk == names.index("publish the release") - 1
    att = next(s for s in st if "tools/plugin_asset.sh" in s.get("run", ""))
    assert "exit 0" not in att["run"]               # no plugin asset is a failure, never a silent skip
