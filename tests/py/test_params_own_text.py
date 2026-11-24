# SPDX-License-Identifier: GPL-3.0-only
"""plugin/gen/params_map.h is shared by the plugin (FR_OWN=1: its drive's TYPE 1 is WARM) and the MPC build (no FR_OWN,
the classic drive: FDRV): the one text that differs by build is a macro that picks one, so the MPC's parameter texts
(and its binary) are what they were."""
import os
import re
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HDR = os.path.join(ROOT, "plugin", "gen", "params_map.h")


def texts(*flags):
    r = subprocess.run(["cc", "-E", "-P", "-x", "c", *flags, HDR], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def test_only_the_drive_type_text_depends_on_the_build():
    src = open(HDR).read()
    assert re.findall(r'FRP_OWN_TXT\(("[^"]*"), ("[^"]*")\)', src) == [('"WARM"', '"FDRV"')]
    own, classic = texts("-DFR_OWN=1"), texts()
    assert texts("-DFR_OWN=0") == classic
    assert '"WARM"' in own and '"FDRV"' not in own
    assert '"FDRV"' in classic and '"WARM"' not in classic
    assert own.replace('"WARM"', '"FDRV"') == classic
