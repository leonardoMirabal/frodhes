# SPDX-License-Identifier: GPL-3.0-only
"""The Move-only factory presets F20-F57 (move/move_presets.h, tools/gen_move_presets.py; the user, 2026-11-01): the
header is what the review's records give, every variant of the review is there once with a generic name of at most
12 characters, and the table is the Move's only (nothing of it in firmware/). The plugin side (each one loads and
reads back its full list, F01-F19 read back as the review's A records, i.e. presets.c's, program change 35-72) is
tests/py/test_move_params.py test_plugin_presets_and_hierarchy_per_type and move/tests/test_bridge.c api: through
presets.c's own preset_values, so a change of PR_FDATA's format (a flat table) changes nothing here."""
import os
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


def test_records_follow_params(monkeypatch):
    """the 0.9.10 hook: a new parameter, a renumbered one or a smaller range in params.c stops the generator"""
    import pytest
    import gen_move_module as gm
    real = gm.model()
    rec = gp.records()
    gp.check_params(rec)                                   # as committed: every record is params.c's

    def with_params(edit):
        p = {n: dict(v) for n, v in real["params"].items()}
        edit(p)
        monkeypatch.setattr(gm, "model", lambda: dict(real, params=p))

    with_params(lambda p: p.__setitem__("P_NEWKNOB", {"id": max(v["id"] for v in p.values()) + 1, "hi": 127}))
    with pytest.raises(SystemExit, match="P_NEWKNOB"):
        gp.check_params(rec)
    with_params(lambda p: p["P_DECAY"].__setitem__("hi", 10))
    with pytest.raises(SystemExit, match="P_DECAY is .* over its range"):
        gp.check_params(rec)
    with_params(lambda p: p["P_DECAY"].__setitem__("id", 99))
    with pytest.raises(SystemExit, match="P_DECAY"):
        gp.check_params(rec)

