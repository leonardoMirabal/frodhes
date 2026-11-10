# SPDX-License-Identifier: GPL-3.0-only
"""The firmware's sources compile without an uninitialised-value warning in the host build at -O2 -Wall (gcc's
-Wmaybe-uninitialized needs the optimiser, so the object is built, into tmp_path), for each build the gates render: the FM-1's, the HQ one
(tools/release_diff.py HQ_DEFS) and the Move's (move/tests/gate_m.sh host16, the Makefile's MOVE_DEFS). K8
(2026-11-09): Debian 12's gcc 12 warned 'q0[6] may be used uninitialized' (drv_khlon.c:429, the Move defines) until
83fa7520 copied the states every block; valgrind found no read of it, and 20 replays of fx/v_filt14 through the Move
plugin gave one hash (build/t3/k8). This keeps that class of warning from coming back (Debian 12's gcc 12 finds it in
e1a25984; the other host warnings on Linux gcc, an unused variable, a clang pragma, a format width, are not this
test's business)."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_diff  # noqa: E402

BUILDS = {"fm1": [], "hq": release_diff.HQ_DEFS,
          "move16": ["-DFR_VOICES=16u", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"],
          "move": ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]}


@pytest.mark.parametrize("name", list(BUILDS))
def test_host_build_has_no_warning(name, tmp_path):
    cc = os.environ.get("CC", "cc")
    r = subprocess.run([cc, "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', *BUILDS[name],
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-c", "-o", str(tmp_path / "frodhes.o"), os.path.join(ROOT, "firmware", "src", "frodhes.c")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    bad = [ln for ln in r.stderr.splitlines() if "uninitialized" in ln and "warning:" in ln]
    assert not bad, "\n".join(bad)
