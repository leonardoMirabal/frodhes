# SPDX-License-Identifier: GPL-3.0-only
"""CMake option FRP_UPDATE_CHECK (plugin/CMakeLists.txt): OFF builds frodhes_clap.c and gui_clap.c against the update
module with nothing behind it (plugin/src/frp_update_off.c). Configure OFF and build the plugin's entry library:
it compiles, and the network module is not part of it."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPS = os.path.join(ROOT, ".local", "plugin-deps", "clap")

pytestmark = pytest.mark.skipif(not shutil.which("cmake") or not os.path.isdir(DEPS),
                                reason="needs cmake and the plugin's dependencies (make plugin-deps)")


def run(args, **kw):
    r = subprocess.run(["nice", "-n", "10", *args], capture_output=True, text=True, timeout=900, **kw)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return r.stdout


def test_configure_off_compiles(tmp_path):
    b = str(tmp_path / "b")
    run(["cmake", "-S", os.path.join(ROOT, "plugin"), "-B", b, "-DFRP_UPDATE_CHECK=OFF", "-DFRP_VERSION=0.0.0-dev"])
    run(["cmake", "--build", b, "--target", "frodhes_clap_impl", "-j2"])
    objs = [f for _, _, fs in os.walk(b) for f in fs if f.endswith((".o", ".obj"))]
    assert any(f.startswith("frp_update_off.c") for f in objs), objs
    assert not any(f.startswith(("frp_update_rt.c", "frp_update_net.c", "frp_update_mac.m", "frp_update.c")) for f in objs)
    cache = open(os.path.join(b, "CMakeCache.txt")).read()
    assert "FRP_UPDATE_CHECK:BOOL=OFF" in cache
