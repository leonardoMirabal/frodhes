# SPDX-License-Identifier: GPL-3.0-only
"""The dry-distortion gate through the plugin (tools/plugin_dist.py): each dry_dist script played through the CLAP,
the VST3 and the Audio Unit (HQ, the plugin's default engine), every format within 0.9.11's limits
(tests/baselines/dry_dist_0911.json) and the three sample-identical. macOS with a built plugin only (make plugin;
FRP_BUILD names another build); skipped elsewhere. The whole run with a report: make plugin-dist."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist  # noqa: E402

BUILD = os.path.join(ROOT, os.environ.get("FRP_BUILD", "build/plugin"))
HAVE = sys.platform == "darwin" and all(os.path.exists(os.path.join(BUILD, p)) for p in
                                        ("frp_clap_render", "bundles/Frodhes.clap", "bundles/Frodhes.vst3",
                                         "bundles/Frodhes.component"))


@pytest.mark.skipif(not HAVE, reason="macOS with a built plugin (make plugin)")
@pytest.mark.xdist_group("plugin_dist")          # (one worker: the hosts are built once, on first use)
@pytest.mark.parametrize("tag", [t[0] for t in dry_dist.tags()])
def test_plugin_dry_distortion(tag):
    out = os.path.join(ROOT, "build", "plugin-dist", "pytest")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "plugin_dist.py"), "--tags", tag, "--out", out,
                        "--bundles", os.path.join(BUILD, "bundles"), "--render", os.path.join(BUILD, "frp_clap_render")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert r.stdout.count("1 of 1 scripts sample-identical") == 2, r.stdout
