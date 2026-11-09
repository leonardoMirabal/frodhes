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



def test_records_are_presets_c():
    """every A record is presets.c's factory record (over the ids it has; params.c's later ids at their defaults):
    gen_move_presets.py --sync has nothing to move (tools/tail_build.sh runs it on each candidate)"""
    recs, rows = gp.presets_c()
    rec = gp.records()
    assert len(recs) == 19
    for k, r in enumerate(recs):
        a = {n: v for n, _, v in rec[("F%02d" % (k + 1), "A")][1]}
        assert {n: r.get(n, rows[n][0]) for n in a} == a, "F%02d" % (k + 1)


def test_sync_moves_the_variants_with_their_a(tmp_path, monkeypatch):
    """presets.c's F01 LEVEL +3 and NOISE 48 -> 40: A takes both, B / C LEVEL by the same step and NOISE the new
    value (neither variant's "set" names it); one comment line says so; the others stay"""
    import shutil
    src = tmp_path / "firmware" / "src"
    src.mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "firmware", "src", "params.c"), src)
    pc = open(os.path.join(ROOT, "firmware", "src", "presets.c")).read()
    old = "P_RV_LOCUT, 70, P_LEVEL, 52, 0xFF,"
    assert pc.count(old) == 1
    (src / "presets.c").write_text(pc.replace(old, "P_RV_LOCUT, 70, P_LEVEL, 55, P_NOISE, 40, 0xFF,"))
    rec_path = tmp_path / "proposed_records.txt"
    shutil.copy(gp.RECORDS, rec_path)
    monkeypatch.setattr(gp, "ROOT", str(tmp_path))
    monkeypatch.setattr(gp, "RECORDS", str(rec_path))
    before = gp.records()
    assert gp.sync("2026-11-10") == ["F01 LEVEL +3 NOISE -8"]
    after = gp.records()
    for key, (_, pairs) in before.items():
        b = {n: v for n, _, v in pairs}
        a = {n: v for n, _, v in after[key][1]}
        if key[0] != "F01":
            assert a == b, key
            continue
        assert a["P_LEVEL"] == b["P_LEVEL"] + 3 and a["P_NOISE"] == 40, key
        assert {n: v for n, v in a.items() if n not in ("P_LEVEL", "P_NOISE")} == \
               {n: v for n, v in b.items() if n not in ("P_LEVEL", "P_NOISE")}, key
    text = rec_path.read_text()
    assert text.count("--sync") == 1 and "F01 LEVEL +3 NOISE -8" in text
    assert gp.sync("2026-11-10") == []                                          # (idempotent)
