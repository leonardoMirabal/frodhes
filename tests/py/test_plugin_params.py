# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's parameter map (plugin/gen/params_map.h, tools/gen_plugin_params.py): the committed file is
what the app's PARAMS[] gives (a parameter added to params.c without its long name and group fails here), the ids are
the enum's (append-only: a host's automation and saved sessions keep pointing at the same parameter), and the
plugin's own ids stay out of the app's range."""
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GEN = os.path.join(ROOT, "plugin", "gen", "params_map.h")


def test_params_map_is_current():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_plugin_params.py"), "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_ids_are_the_enum():
    src = open(GEN).read()
    n_app = int(re.search(r"#define FRP_NPARAM_APP (\d+)u", src).group(1))
    rows = re.findall(r"^\s*\{\s*(0x[0-9a-fA-F]+|\d+)u?,", src.split("FRP_PARAMS[", 1)[1], re.M)
    ids = [int(x, 0) for x in rows]
    assert ids[:n_app] == list(range(n_app)), "the app's parameters: id = index = the P_ enum"
    assert all(i >= 0x10000 for i in ids[n_app:]), "the plugin's own ids start at 0x10000"
    assert len(set(ids)) == len(ids)
    params_c = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    assert params_c.count("[P_") >= n_app       # (every P_ id has its PARAMS[] row)


def test_plugin_preset_numbers_did_not_move():
    """every plugin factory preset keeps its number and name from 0.9.13 (a host's saved PRESET value, automation, a
    program change file): the Move's index i (35..72 the variants) is the plugin's i - 16; WHURLI is F58-F61, after them"""
    import re
    src = open(GEN).read()
    names = dict(re.findall(r'"(F\d\d) ([^"]*)"', src))
    old = [ln.split(" ", 1) for ln in open(os.path.join(ROOT, "tests", "baselines", "move_preset_index_0913.txt")).read().splitlines()
           if not ln.startswith("#")]
    for i, n in old:
        i = int(i)
        if 19 <= i < 35:
            continue                                   # (the user slots)
        tag, name = n.split(" ", 1)
        assert tag == "F%02d" % (i + 1 if i < 19 else i - 15) and names.get(tag) == name, (i, n, names.get(tag))
    assert [names["F%02d" % k] for k in range(58, 62)] == ["WHURLI", "REED GROWL", "REED TREM", "REED SOFT"]
