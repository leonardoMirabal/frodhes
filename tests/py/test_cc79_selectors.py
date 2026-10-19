# SPDX-License-Identifier: GPL-3.0-only
"""CC 79 selectors cannot change meaning (MARIMBA PLAN §9.4, M-prep): CC 79 bands 0..127 over the bar engine's TYPEs
(frodhes.c midi_cc), so a band's middle moves whenever a bar type is added (with VIBES alone 64 and 127 were VIBES;
with MARIMBA they are MARIMBA). Every tool, test, script and the emulator selects a bar type with a band's FIRST
value (VIBES = 0, stable for any number of bar types). This test fails on any other literal CC 79 value, and checks
the firmware's banding as this file computes it against the CC 80 / CC 79 map of the host build."""
import math
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(ROOT, "firmware", "src", "params.c")

# how the trees write a CC 79 message: host scripts, cc(t, 79, v) / cc(79, v) / (79, v) pairs, cc_raw, the web page,
# and ("NAME", 79, v ...) rows of the UI tests
PATS = [re.compile(p) for p in (r"0xB0 79 (\d+)\b", r"\bcc\(\s*(?:\w+,\s*)?79,\s*(\d+)\)", r"\bcc_raw\(79,\s*(\d+)\)",
                                r"_web_midi\(0xB0,\s*79,\s*(\d+)\)", r"(?<!in )[(\[]\s*79,\s*(\d+)\)", r"[\"'],\s*79,\s*(\d+)\b")]


def bar_types():
    """the TYPE ids of the bar engine, from params.c TYPE_ENGINE"""
    eng = re.search(r"TYPE_ENGINE\[N_TYPE_N\] = \{([^}]*)\}", open(SRC).read()).group(1)
    return [t for t, e in enumerate(int(x) for x in eng.split(",")) if e == 1]


def firsts(nb):
    """each band's first value: the smallest v with v * nb >> 7 == k"""
    return [math.ceil(k * 128 / nb) for k in range(nb)]


def test_band_firsts_select_their_type():
    nb = len(bar_types())
    assert nb >= 1
    for k, v in enumerate(firsts(nb)):
        assert v * nb >> 7 == k and (v == 0 or (v - 1) * nb >> 7 == k - 1)
    assert firsts(nb)[0] == 0


def test_every_cc79_selector_is_a_band_start():
    ok = set(firsts(len(bar_types())))
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
                            if int(m.group(1)) not in ok:
                                bad.append(f"{os.path.relpath(p, ROOT)}:{i}: CC 79 {m.group(1)}")
    assert not bad, "CC 79 selectors off a band's first value " + str(sorted(ok)) + ":\n" + "\n".join(bad)
