# SPDX-License-Identifier: GPL-3.0-only
"""The Move-only factory presets F20-F57 (move/move_presets.h, tools/gen_move_presets.py; the user, 2026-11-01): the
header is what the review's records give, every variant of the review is there once with a generic name of at most
12 characters, the table is the Move's only (nothing of it in firmware/), and presets.c's F01-F19 are the review's A
records. The plugin side (each one loads and reads back its full list, F01-F19 equal the FM-1's, program change
35-72) is tests/py/test_move_params.py test_plugin_presets_and_hierarchy_per_type and move/tests/test_bridge.c api."""
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_move_presets as gp  # noqa: E402


def test_header_current():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_move_presets.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_variant_once_named():
    rows = gp.table()
    assert len(rows) == 38 and [r[0] for r in rows] == list(range(20, 58))
    assert [(r[3], r[4]) for r in rows] == [("F%02d" % f, v) for f in range(1, 20) for v in "BC"]
    names = [r[1] for r in rows]
    assert len(set(names)) == len(names)
    assert all(len(n) <= 12 and n == n.upper() for n in names)
    assert all(len("F%02d %s" % (r[0], r[1])) <= 16 for r in rows)      # (the screen's 16 characters)


def test_move_only():
    """nothing of the table reaches the FM-1: no firmware source names it, the bridge includes it"""
    for d, _, fs in os.walk(os.path.join(ROOT, "firmware")):
        for f in fs:
            if f.endswith((".c", ".h")):
                t = open(os.path.join(d, f), errors="replace").read()
                assert "move_presets.h" not in t and "MB_PX_" not in t, os.path.join(d, f)
    assert '#include "move_presets.h"' in open(os.path.join(ROOT, "move", "move_bridge.h")).read()


def test_a_records_are_presets_c():
    """the review's A records (what F01-F19 on the Move read back against) are presets.c's: each pair of PR_FDATA
    over params.c's defaults gives the A list"""
    pc = open(os.path.join(ROOT, "firmware", "src", "presets.c")).read()
    body = re.search(r"PR_FDATA\[PR_NF\]\[PR_FDN\] = \{(.*?)\n\};", pc, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    recs = re.findall(r"\{([^{}]*)\}", body)
    assert len(recs) == 19
    rec = gp.records()
    for i, r in enumerate(recs):
        a = {n: x for n, _, x in rec[("F%02d" % (i + 1), "A")][1]}
        toks = [t.strip() for t in r.split(",") if t.strip()]
        assert toks[-1] == "0xFF"
        pairs = dict(zip(toks[0:-1:2], (int(x) for x in toks[1:-1:2])))
        for n, x in pairs.items():
            assert a[n] == x, ("F%02d" % (i + 1), n, a[n], x)
