# SPDX-License-Identifier: GPL-3.0-only
"""The TYPE scroll order (STAGE 77, SUITCASE, EARLY FELT, BELL DYNO, WHURLI, VIBES, MARIMBA) is one table on every
surface: the firmware's TYPE_ORDER / TYPE_POS, the plugin's FRN_TYPE_ORDER / FRN_TYPE_POS and the Move screen's
`order`. They are inverse permutations and agree; the stored ids (0 ST77 .. 5 MARIMBA, 6 WHURLI) do not change."""
import json
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
WANT = [0, 1, 2, 3, 6, 4, 5]


def arr(path, name):
    s = open(os.path.join(ROOT, path)).read()
    m = re.search(name + r"\[[^\]]*\]\s*=\s*\{([^}]*)\}", s)
    assert m, (path, name)
    return [int(x) for x in m.group(1).replace("\n", " ").split(",") if x.strip()]


def test_firmware_table():
    order = arr("firmware/src/engine.c", "TYPE_ORDER")
    pos = arr("firmware/src/engine.c", "TYPE_POS")
    assert order == WANT
    assert [pos[i] for i in order] == list(range(7))
    assert [order[p] for p in pos] == list(range(7))


def test_plugin_table_equals_firmware():
    assert arr("plugin/ui/frui_params.h", "FRN_TYPE_ORDER") == arr("firmware/src/engine.c", "TYPE_ORDER")
    assert arr("plugin/ui/frui_params.h", "FRN_TYPE_POS") == arr("firmware/src/engine.c", "TYPE_POS")


def test_move_screen_order():
    s = open(os.path.join(ROOT, "move/module/screen.js")).read()
    m = re.search(r'"order"\s*:\s*(\[[0-9, ]*\])|order\s*:\s*(\[[0-9, ]*\])', s)
    assert m, "the p_type knob carries its order"
    assert json.loads(m.group(1) or m.group(2)) == WANT


def test_ids_unchanged():
    s = open(os.path.join(ROOT, "plugin/ui/frui_params.h")).read()
    names = re.search(r"FRN_TYPE\[\]\s*=\s*\{([^}]*)\}", s).group(1)
    assert [x.strip().strip('"') for x in names.split(",")] == [
        "STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO", "VIBES", "MARIMBA", "WHURLI"]


def test_plugin_step_walks_the_order(tmp_path):
    """the plugin's PREV / NEXT / wheel step (frn_type_step), compiled and run: the ids come out in scroll order, wrap at
    the ends for PREV / NEXT, stop at the ends for the wheel"""
    import shutil
    import subprocess
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        import pytest
        pytest.skip("no C compiler")
    src = tmp_path / "t.c"
    src.write_text('#include <stdio.h>\n#include "frui_params.h"\nint main(void){int t=0,i;\n'
                   'for(i=0;i<7;i++){printf("%d ",t);t=frn_type_step(t,1,1);}printf("| %d | ",t);\n'
                   't=0;for(i=0;i<9;i++)t=frn_type_step(t,1,0);printf("%d | ",t);\n'
                   't=0;t=frn_type_step(t,-1,1);printf("%d | ",t);t=0;t=frn_type_step(t,-1,0);printf("%d\\n",t);return 0;}\n')
    exe = tmp_path / "t"
    subprocess.run([cc, "-I", os.path.join(ROOT, "plugin/ui"), "-o", str(exe), str(src)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "0 1 2 3 6 4 5 | 0 | 5 | 5 | 0", out
