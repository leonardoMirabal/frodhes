# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's core (plugin/frp_ui_iface.h, plugin/src/slot_bridge.h): Gate D's core mode on the built
plugin (make plugin): 24 voices on every engine, the state round trip with every setting and older (CLASSIC too) or
broken states, presets (factory, user files, UNDO, program changes), automation (= the device's CC path, byte for byte), MIDI learn,
the tempo sources and a synced delay at the transport's tempo. Skipped when the plugin is not built."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD = os.environ.get("FRP_BUILD", os.path.join(ROOT, "build", "plugin"))


@pytest.mark.skipif(not os.path.exists(os.path.join(BUILD, "frp_clap_render")) or
                    not os.path.exists(os.path.join(BUILD, "bundles", "Frodhes.clap")),
                    reason="the plugin is not built (make plugin)")
def test_plugin_core():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gate_d.py"), "--only", "core"], cwd=ROOT,
                       capture_output=True, text=True, env={**os.environ, "FRP_BUILD": BUILD})
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "Gate D: PASS" in r.stdout


def test_iface_header_compiles():
    """plugin/frp_ui_iface.h stands alone (the GUI branch builds against it)"""
    r = subprocess.run(["cc", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", "-x", "c",
                        os.path.join(ROOT, "plugin", "frp_ui_iface.h")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
