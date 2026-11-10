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
    recs, rows, names = gp.presets_c()
    rec = gp.records()
    assert len(recs) == 19
    for k, r in enumerate(recs):
        assert rec[("F%02d" % (k + 1), "A")][0] == names[k], (k, names[k])
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
    import re as _re
    a = pc.index("PR_FDATA[] = {")
    b = pc.index("0xFF,", a)                                                    # (F01's record: up to its 0xFF)
    rec01 = _re.sub(r"P_LEVEL, (\d+),", lambda m: "P_LEVEL, %d," % (int(m.group(1)) + 3), pc[a:b], count=1)
    (src / "presets.c").write_text(pc[:a] + rec01 + "P_NOISE, 40, " + pc[b:])
    rec_path = tmp_path / "proposed_records.txt"
    shutil.copy(gp.RECORDS, rec_path)
    monkeypatch.setattr(gp, "ROOT", str(tmp_path))
    monkeypatch.setattr(gp, "RECORDS", str(rec_path))
    var_path = tmp_path / "variants.json"
    shutil.copy(gp.VARIANTS, var_path)
    monkeypatch.setattr(gp, "VARIANTS", str(var_path))
    before = gp.records()
    assert gp.sync("2026-11-10") == ["F01 STAGE 77 LEVEL +3 NOISE -8"]
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
    assert text.count("--sync") == open(os.path.join(ROOT, "move", "presets", "proposed_records.txt")).read().count("--sync") + 1
    assert "F01 STAGE 77 LEVEL +3 NOISE -8" in text
    assert gp.sync("2026-11-10") == []                                          # (idempotent)


def test_sync_by_name(tmp_path, monkeypatch):
    """a reordered presets.c (the proposed 19, 2026-11-10): F05 and F08 swap places, F12 is replaced by a new preset
    with B / C in new_variants.json and F19 by one without: the moved presets take their records, variants.json
    entries and names along by name, the new ones get their A (and B / C when named), the dropped ones' go"""
    import json
    import re as _re
    import shutil
    src = tmp_path / "firmware" / "src"
    src.mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "firmware", "src", "params.c"), src)
    recs, rows, names = gp.presets_c()
    pc = open(os.path.join(ROOT, "firmware", "src", "presets.c")).read()
    order = list(range(19))
    order[4], order[7] = 7, 4
    nn = [names[i] for i in order]
    nr = [recs[i] for i in order]
    nn[11], nr[11] = "NEW ONE", dict(recs[0], P_LEVEL=40)
    nn[18], nr[18] = "NEW TWO", dict(recs[1])
    body = "".join("\n    %s, 0xFF," % ", ".join("%s, %d" % kv for kv in r.items()) for r in nr)
    pc = _re.sub(r"(PR_FNAME\[PR_NF\] = \{).*?(\};)", lambda m: m.group(1) + ", ".join('"%s"' % n for n in nn) + m.group(2),
                 pc, flags=_re.S)
    pc = _re.sub(r"(PR_FDATA\[\] = \{).*?(\n\};)", lambda m: m.group(1) + body + m.group(2), pc, flags=_re.S)
    (src / "presets.c").write_text(pc)
    for f in ("RECORDS", "VARIANTS"):
        shutil.copy(getattr(gp, f), tmp_path / os.path.basename(getattr(gp, f)))
        monkeypatch.setattr(gp, f, str(tmp_path / os.path.basename(getattr(gp, f))))
    (tmp_path / "new_variants.json").write_text(json.dumps({"NEW ONE": {
        "B": {"name": "NEW ONE ECHO", "line": "an echo", "set": {"P_DL_SW": 2, "P_DL_DEPTH": 10}, "level_delta": -2},
        "C": {"name": "NEW ONE HALL", "line": "a hall", "set": {"P_RV_DEPTH": 30}}}}))
    monkeypatch.setattr(gp, "NEW_VARIANTS", str(tmp_path / "new_variants.json"))
    monkeypatch.setattr(gp, "ROOT", str(tmp_path))
    old_rec, old_var = gp.records(), json.load(open(gp.VARIANTS))
    moved = gp.sync("2026-11-10")
    assert moved == ["F05 %s from F08" % names[7], "F08 %s from F05" % names[4], "F12 NEW ONE new (with B / C)",
                     "F19 NEW TWO new (A only)", "F12 %s dropped (its B / C with it)" % names[11],
                     "F19 %s dropped (its B / C with it)" % names[18]], moved
    rec, var = gp.records(), json.load(open(gp.VARIANTS))
    for new, old in (("F05", "F08"), ("F08", "F05")):
        for v in "ABC":
            assert rec[(new, v)] == old_rec[(old, v)], (new, v)
        assert var[new] == old_var[old]
    a = {n: x for n, _, x in rec[("F12", "A")][1]}
    b = {n: x for n, _, x in rec[("F12", "B")][1]}
    assert rec[("F12", "A")][0] == "NEW ONE" and a["P_LEVEL"] == 40 and b["P_LEVEL"] == 38 and b["P_DL_SW"] == 2
    assert {n for n in a if a[n] != b[n]} == {"P_LEVEL", "P_DL_SW", "P_DL_DEPTH"}
    assert ("F19", "B") not in rec and [k for k in var["F19"] if len(k) == 1] == []
    rows_ = gp.table()
    assert len(rows_) == 36 and [r[1] for r in rows_ if r[3] == "F12"] == ["NEW ONE ECHO", "NEW ONE HALL"]
    assert gp.sync("2026-11-10") == []                                          # (idempotent)


def test_preset_lists_expected_and_compare():
    """tools/preset_lists.py (the Move's and the plugin's lists against presets.c, tools/tail_build.sh): the expected
    list is presets.c's 19 then the 38 variants, the records agree with presets.c, and compare names a value or a name
    that differs"""
    import preset_lists as pl
    ids, want, bad = pl.expected()
    assert not bad and len(want) == 57 and want[0][0].startswith("F01 ") and want[19][0].startswith("F20 ")
    assert all(len(v) == len(ids) for _, v in want)
    assert pl.compare("x", want, want, ids) == []
    got = [(l, list(v)) for l, v in want]
    got[3][1][ids.index("P_LEVEL")] += 1
    got[30] = ("F31 OTHER", got[30][1])
    b = pl.compare("x", got, want, ids)
    assert len(b) == 2 and "P_LEVEL" in b[0] and "F31 OTHER" in b[1], b
    assert pl.compare("x", got[:-1], want, ids)[0].startswith("x: 56 factory presets")


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

