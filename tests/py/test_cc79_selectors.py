# SPDX-License-Identifier: GPL-3.0-only
"""CC 79 selects the bar types (frodhes.c midi_cc, params.c TYPE_CC79): VIBES 0..95, MARIMBA 96..127 (the user,
2026-10-19: 0 and 64, the values 0.9.3 used and documented for VIBES, keep selecting it; 96..127 changed meaning in
0.9.4). Every tool, test, script and the emulator selects a bar type with a stable value: VIBES 0 or 64, MARIMBA 96 or
127. This test checks the table and fails on any other literal CC 79 value; the host build's map is checked in
test_bars_compat (every value 0..127)."""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(ROOT, "firmware", "src", "params.c")
STABLE = {0: 4, 64: 4, 96: 5, 127: 5}          # selector value -> TYPE

# how the trees write a CC 79 message: host scripts, cc(t, 79, v) / cc(79, v) / (79, v) pairs, cc_raw, the web page,
# and ("NAME", 79, v ...) rows of the UI tests
PATS = [re.compile(p) for p in (r"0xB0 79 (\d+)\b", r"\bcc\(\s*(?:\w+,\s*)?79,\s*(\d+)\)", r"\bcc_raw\(79,\s*(\d+)\)",
                                r"_web_midi\(0xB0,\s*79,\s*(\d+)\)", r"(?<!in )[(\[]\s*79,\s*(\d+)\)", r"[\"'],\s*79,\s*(\d+)\b")]


def table(name):
    return [int(x) for x in re.search(name + r"\[N_TYPE_N\] = \{([^}]*)\}", open(SRC).read()).group(1).split(",")]


def cc79(v):
    """frodhes.c midi_cc: the last bar TYPE whose band start <= v"""
    eng, lo = table("TYPE_ENGINE"), table("TYPE_CC79")
    pick = 4
    for t in range(len(eng)):
        if eng[t] == 1 and v >= lo[t]:
            pick = t
    return pick


def test_cc79_table():
    assert [cc79(v) for v in range(128)] == [4] * 96 + [5] * 32
    for v in (0, 64, 95):
        assert cc79(v) == 4                     # VIBES, as in 0.9.3
    for v in (96, 127):
        assert cc79(v) == 5                     # MARIMBA
    for v, t in STABLE.items():
        assert cc79(v) == t


def test_every_cc79_selector_is_stable():
    bad = []
    for d in ("tests", "tools", "web"):
        for dp, _, fs in os.walk(os.path.join(ROOT, d)):
            if "node_modules" in dp or "dist" in dp.split(os.sep):
                continue
            for f in fs:
                if not f.endswith((".py", ".txt", ".js")) or f == os.path.basename(__file__):
                    continue
                p = os.path.join(dp, f)
                for i, line in enumerate(open(p, errors="replace"), 1):
                    for pat in PATS:
                        for m in pat.finditer(line):
                            if int(m.group(1)) not in STABLE:
                                bad.append(f"{os.path.relpath(p, ROOT)}:{i}: CC 79 {m.group(1)}")
    assert not bad, "CC 79 selectors off the stable values " + str(sorted(STABLE)) + ":\n" + "\n".join(bad)
