# SPDX-License-Identifier: GPL-3.0-only
"""M1-14, the host side of the Move's parameter and state round trips, against move/module/move_ui.h (the table the
plugin's bridge uses): every parameter key, set through its CC with the banding inverted (as plan 04 4.2.6 planned the
bridge), reads back as the value set (PV); the CC map's traps the bridge must not walk into are pinned (so a change
there is seen); the settings cards with a CC show the option the key's options list names at that index; every
preset (program change 0-34) loads; a setup saved by the app comes back after a power cycle.

The same checks through the plugin (set_param / get_param / state on dsp.so) need agent A's glue (move/move_plugin.c
and the replay harness): they are skipped until it is there."""
import os
import re
import sys

import pytest

from hostrun import cc, run

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_move_module as gen  # noqa: E402

import test_persistence as tp  # noqa: E402

M = gen.model()
ROWS = re.findall(r'\{"(\w+)", (\w+), (\d+), (MK_\w+), (\d+), (-?\d+)\}',
                  open(os.path.join(gen.OUT, "move_ui.h")).read())
PARAM_ROWS = [(k, s, int(c)) for k, s, c, kind, _, _ in ROWS if kind == "MK_PARAM"]
PLUGIN = os.path.join(ROOT, "move", "move_plugin.c")
PARAMS_C = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
TYPE_CC79 = [int(x) for x in re.search(r"TYPE_CC79\[N_TYPE_N\] = \{([^}]*)\}", PARAMS_C).group(1).split(",")]
DRV_CC84 = [int(x) for x in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", PARAMS_C).group(1).split(",")]


def cc_for(slot, x):
    """the (CC, value) that sets parameter slot to x through frodhes.c midi_cc: the inverse of its banding"""
    p = M["params"][slot]
    if slot == "P_TYPE":
        return (80, x * 32) if x < 4 else (79, TYPE_CC79[x])      # CC 80: the piano types only
    if slot == "P_TW_MODE":
        return 21, 64 if x else 0
    if slot == "P_DRV_SW":
        return 84, DRV_CC84[x]
    if slot == "P_CP_SW":
        return 85, 64 if x else 0                                  # (the combined CHO / PHA switch)
    if p["hi"] == 2:
        return p["cc"], (0, 43, 86)[x]
    if p["hi"] < 127:
        return p["cc"], -(-x * 128 // (p["hi"] + 1))               # ceil(x * 128 / (hi + 1))
    return p["cc"], x


def values(hi):
    return list(range(hi + 1)) if hi < 127 else [0, 1, 63, 64, 126, 127]


def test_every_parameter_key_round_trips():
    """each key's every value (all of an enum's; 0, 1, 63, 64, 126, 127 of an amount) set by its CC reads back as
    PV; the key's CC in move_ui.h is PARAMS[].cc (or the TYPE / CHO-PHA selectors' own); each parameter goes back
    to its default before the next (so the phaser-only redirect of CC 86 / 87 never applies)"""
    lines, want, t = [], [], 100
    seen = set()
    for key, slot, kcc in PARAM_ROWS:
        if slot in seen:                                           # (p_strike etc.: the same parameter again)
            continue
        seen.add(slot)
        p = M["params"][slot]
        assert kcc == p["cc"], (key, kcc)
        for x in values(p["hi"]):
            n, v = cc_for(slot, x)
            lines += [cc(t, n, v), f"{t + 3} dump"]
            want.append((t + 3, slot, x))
            t += 6
        n, v = cc_for(slot, p["def"])
        lines.append(cc(t, n, v))
        t += 6
    r = run("move-params", lines, t + 50)
    bad = [(slot, x, r.state(tt)[f"p.{M['params'][slot]['id']}"]) for tt, slot, x in want
           if int(r.state(tt)[f"p.{M['params'][slot]['id']}"]) != x]
    assert not bad, bad[:20]
    assert {s for _, s, _ in PARAM_ROWS} | set(gen.VIA) | set(gen.MIDI_ONLY) == set(M["pids"])


def test_cc_path_traps_are_as_documented():
    """the reasons the bridge should set parameters directly (param_set), not as CCs (05a-rebase-notes D9): with
    only the phaser on, CC 86 moves the phaser's DEPTH, not the chorus's; CC 85 to CHO turns the phaser off; CC 80
    never reaches a bar TYPE"""
    pid = {s: M["params"][s]["id"] for s in ("P_CP_DEPTH", "P_PH_DEPTH", "P_PH_SW", "P_CP_SW", "P_TYPE")}
    r = run("move-traps", [cc(100, 12, 127), "110 dump", cc(120, 86, 100), "130 dump", cc(140, 85, 64), "150 dump",
                           cc(160, 80, 127), "170 dump"], 250)
    s1, s2, s3, s4 = (r.state(t) for t in (110, 130, 150, 170))
    assert s1[f"p.{pid['P_PH_SW']}"] == "1" and s1[f"p.{pid['P_CP_SW']}"] == "0"
    assert s2[f"p.{pid['P_PH_DEPTH']}"] == "100" and s2[f"p.{pid['P_CP_DEPTH']}"] == s1[f"p.{pid['P_CP_DEPTH']}"]
    assert s3[f"p.{pid['P_CP_SW']}"] == "1" and s3[f"p.{pid['P_PH_SW']}"] == "0"
    assert s4[f"p.{pid['P_TYPE']}"] == "3"


def card_of(slot):
    order = list(M["pages"])                                       # (the PG_ enum's order: host_state card.<pg>.<k>)
    for i, pg in enumerate(order):
        if slot in M["pages"][pg]["cards"]:
            return i, M["pages"][pg]["cards"].index(slot)
    raise KeyError(slot)


def test_settings_options_match_the_cards():
    """the settings cards that have a CC (arpeggiator, chord, sequencer; none on the Move today): the CC's index shows on the FM-1's card as
    the key's option of that index (so the options lists are in the firmware's order)"""
    chain = {c["key"]: c for c in __import__("json").load(open(os.path.join(gen.OUT, "chain_params.json")))}
    lines, want, t = [], [], 100
    for key, slot, kcc, kind, _, _ in ROWS:
        if kind != "MK_SLOT" or not int(kcc) or chain[key]["type"] != "enum":
            continue
        opts = chain[key]["options"]
        for x in range(len(opts)):
            v = 64 if slot == "S_SQEDIT" and x else x
            lines += [cc(t, int(kcc), v), f"{t + 3} dump"]
            want.append((t + 3, slot, opts[x]))
            t += 6
        lines.append(cc(t, int(kcc), 0))
        t += 6
    if not want:
        # (the user, 2026-10-28: the arpeggiator, the chords and the sequencer, the settings cards with a CC, are not
        # on the Move; their CCs are dropped by the bridge, so no key is left to check)
        assert not any(int(kcc) for key, slot, kcc, kind, _, _ in ROWS if kind == "MK_SLOT")
        return
    r = run("move-slots", lines, t + 50)
    bad = []
    for tt, slot, o in want:
        pg, k = card_of(slot)
        got = r.state(tt)[f"card.{pg}.{k}"]
        if gen.short_value(got) != o:                   # (the FM-1's name, the Move's abbreviation of it)
            bad.append((slot, o, got))
    assert not bad, bad


def test_every_preset_loads():
    """program change 0-15 = F01-F16, 32-34 = F17-F19 (a factory preset by its number on the header), 16-31 the user
    slots (U01.. when the slot holds one, else nothing changes); every parameter within its range after each"""
    lines, t, pcs = ["50 dump"], 100, list(range(35))
    for pc in pcs:
        lines += [f"{t} midi 0xC0 {pc} 0", f"{t + 40} dump"]
        t += 60
    r = run("move-presets", lines, t + 50)
    prev = r.state(50)["header"]
    for i, pc in enumerate(pcs):
        s = r.state(100 + 60 * i + 40)
        hd = s["header"]
        if pc < 16 or pc >= 32:
            num = pc + 1 if pc < 16 else pc - 15
            assert hd.startswith("F%02d " % num), (pc, hd)
        else:
            assert hd.startswith("U%02d " % (pc - 15)) or hd == prev, (pc, hd, prev)
        for pname, p in M["params"].items():
            assert 0 <= int(s[f"p.{p['id']}"]) <= p["hi"], (pc, pname)
        prev = hd


def test_saved_setup_survives_a_power_cycle():
    """the app's own save (the setup, 2 s after a change, silent) comes back on the next boot: the host's flash file
    stands in for the Move's store file (the plugin path: test_plugin_state_round_trip)"""
    fl = tp.flash_file("move-state")
    r1 = run("move-state-1", ["50 btn 7", "60 btn 7", "70 btn 7", "500 turn 2 1", "600 turn 2 1", "4000 dump"], 4500,
             flash=fl)
    assert r1.saves, "the setup change was not saved"
    r2 = tp.boot("move-state-2", fl, end=400)
    pg, k = card_of("S_MCH")
    assert r2.state(100)[f"card.{pg}.{k}"] == r1.state(4000)[f"card.{pg}.{k}"] == "2"


def _bridge(*args, stdin=None):
    """run build/move/t/test_bridge (agent A's bridge tests on dsp.so): natively on Linux arm64, else in the Move
    image (Docker, 2 CPUs); None when neither is there"""
    import platform
    import shutil
    import subprocess
    inner = "make -s move-tools-native >/dev/null 2>&1 && build/move/t/test_bridge " + " ".join(args)
    if platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64"):
        cmd = ["sh", "-c", inner]
    elif shutil.which("docker") and subprocess.run(["docker", "image", "ls", "-q", "frodhes-move:bookworm"],
                                                    capture_output=True, text=True).stdout.strip():
        cmd = ["docker", "run", "--rm", "-i", "--cpus=2", "--platform", "linux/arm64", "-v", ROOT + ":/src", "-w", "/src",
               "frodhes-move:bookworm", "sh", "-c", inner]
    else:
        return None
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=ROOT, timeout=1800)


NO_GLUE = pytest.mark.skipif(not os.path.exists(PLUGIN), reason="needs move/move_plugin.c")
CHAIN = __import__("json").load(open(os.path.join(gen.OUT, "chain_params.json")))
KINDS = {k: kind for k, _, _, kind, _, _ in ROWS}


def _wire_values(p):
    if p["type"] == "enum":
        return [str(i) for i in range(len(p["options"]))]
    lo, hi = p["min"], p["max"]
    if p["type"] == "float":
        return ["%.1f" % v for v in (lo, hi, (lo + hi) / 2, 97.5)]
    return [str(v) for v in sorted({lo, hi, (lo + hi) // 2, lo + 1, hi - 1})]


@NO_GLUE
def test_plugin_every_key_set_get():
    """set_param(key, wire) then get_param(key) == wire for every MOVE_KEYS row (MK_TRIG: one shot, read 0) and every
    value of its range (enums: each index; amounts: the ends, the middle, one in from each end)"""
    lines = []
    for p in CHAIN:
        if KINDS.get(p["key"]) in (None, "MK_TRIG", "MK_BTN"):     # (MK_BTN, a momentary control: test_move_sustain)
            continue
        lines += ["%s %s" % (p["key"], v) for v in _wire_values(p)]
        lines.append("%s %s" % (p["key"], _wire_values(p)[0]))      # (back to the low end: the next key starts clean)
    r = _bridge("keys", "build/move/frodhes/dsp.so", stdin="\n".join(lines) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    rows = [ln.split("\t") for ln in r.stdout.splitlines() if ln.count("\t") == 2]
    assert len(rows) == len(lines), r.stdout[-2000:] + r.stderr[-2000:]
    bad = [(k, v, got) for k, v, got in rows if v != got]
    assert not bad, "keys that do not read back: %s" % bad[:20]


@NO_GLUE
def test_plugin_state_round_trip():
    """test_bridge api: 1000 random settings records through state -> set_param("state") -> state stable (sanitized
    as the FM-1 sanitizes); the current sound (TONE, SPREAD) and the tempo come back with a state load"""
    r = _bridge("api", "build/move/frodhes/engine.so")    # (the plugin on its own: api also checks its one-app rule)
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    assert "ok    bridge api (0 failures)" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


@NO_GLUE
def test_plugin_presets_and_hierarchy_per_type():
    """preset / preset_count (73) / preset_name for every preset: F01-F19 (0-18), U01-U16 (19-34, empty in a new data
    folder: nothing loads), the Move-only F20-F57 (35-72, move/move_presets.h); after each load the panel holds the
    preset's full parameter list exactly as the review read it back (move/presets/proposed_records.txt: A for F01-F19,
    so the Move's F01-F19 are the FM-1's presets.c records; B / C for F20-F57); get_param("ui_hierarchy") is
    move_ui.h's text and ui_engine (the VOICE levels' gate) follows each TYPE (eng_pages.h TYPE_UI)"""
    import gen_move_presets as gp
    r = _bridge("presets", "build/move/frodhes/dsp.so")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    out = r.stdout.splitlines()
    assert "count 73" in out, r.stdout[-2000:]
    pr = [ln.split(" ", 3) for ln in out if ln.startswith("preset ")]
    pv = {int(ln.split()[1]): [int(x) for x in ln.split()[2:]] for ln in out if ln.startswith("pv ")}
    assert len(pr) == 73 and len(pv) == 73
    rec = gp.records()
    want = {i: rec[("F%02d" % (i + 1), "A")][1] for i in range(19)}
    for num, name, _, base, v, pairs in gp.table():
        want[35 + num - gp.FIRST] = pairs
    prev = None
    for i, (_, idx, cur, name) in enumerate(pr):
        if cur == str(i):                                          # loaded: its own name
            n = i + 1 if i < 19 else i - 19 + 1 if i < 35 else i - 35 + gp.FIRST
            assert re.match(r"[FU]%02d " % n, name) and name[0] == ("U" if 19 <= i < 35 else "F"), (i, name)
        else:                                                      # an empty user slot: nothing loads
            assert 19 <= i < 35 and cur == prev, (i, cur, prev)
        prev = cur
        if i in want:
            assert cur == str(i), (i, cur)
            got = pv[i]
            bad = [(n, x, got[pid] if pid < len(got) else None) for n, pid, x in want[i] if pid >= len(got) or got[pid] != x]
            assert not bad, (i, name, bad)                         # (parameter, want, got)
    names = {i: name for i, (_, _, _, name) in enumerate(pr)}
    assert names[35] == "F20 STAGE77 ECHO" and names[72] == "F57 RADIO LOW", (names[35], names[72])
    ty = {int(ln.split()[1]): ln.split()[2] for ln in out if ln.startswith("type ")}
    assert [ty[t] for t in range(6)] == ["0", "0", "0", "0", "1", "2"]
    hl = [int(ln.split()[1]) for ln in out if ln.startswith("hierarchy ")]
    lit = open(os.path.join(gen.OUT, "move_ui.h")).read()
    assert hl and hl[0] > 1000 and 'levels' in lit
