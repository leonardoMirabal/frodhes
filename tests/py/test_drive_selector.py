# SPDX-License-Identifier: GPL-3.0-only
"""The drive's TYPE (P_DRV_SW: OFF / FDRV / KHLON / TSH8 / OHCD / BHLUE) on CC 84, the panel and the records.

CC 84 maps by a band table, the CC 79 way (params.c DRV_CC84, frodhes.c midi_cc): 0..63 OFF, 64..75 KHLON, 76..87 TSH8,
88..99 OHCD, 100..111 BHLUE, 112..127 FDRV. 0 and 127, the values sent while CC 84 was on / off (0.9.3-0.9.4), keep
their sound: 127 is still the classic drive, FDRV (tests/py/test_fx_drive_reverb.py sends it and expects the drive as
before). Only 64..111, "on" before, now mean the other TYPEs. Every tool, test and script selects a TYPE with a stable
value: OFF 0, FDRV 127, the others the middle of their band (tools/hil_scan.py drv_cc; tests/scripts/
drive_select.txt, which checks the band edges, excepted). PERFORM's wake picks the TYPE last on (D11)."""
import os
import re

from hostrun import ROOT, cc, run

SRC = os.path.join(ROOT, "firmware", "src", "params.c")
STABLE = {0: 0, 127: 1, 69: 2, 81: 3, 93: 4, 105: 5}   # selector value -> P_DRV_SW (hil_scan.drv_cc)
EDGES_OK = {"drive_select.txt", os.path.basename(__file__)}
P_DRV_SW = 50                                       # params.c (ids are append-only)


def table():
    s = open(SRC).read()
    lo = [int(x) for x in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", s).group(1).split(",")]
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", s).group(1))
    hi = int(re.search(r'\[P_DRV_SW\] = \{"[^"]*", (\d+), (\d+), F_ENUM, 84, N_DRV\}', s).group(1))
    return lo, names, hi


def cc84(v):
    """the value whose band starts highest at or below v"""
    lo, _, _ = table()
    return max((t for t in range(len(lo)) if lo[t] <= v), key=lambda t: lo[t])


def test_cc84_table():
    lo, names, hi = table()
    assert len(lo) == len(names) == hi + 1 == 6
    assert names[:2] == ["OFF", "FDRV"] and all(len(n) <= 5 for n in names)   # a FONT_L card value: 5 characters
    assert [cc84(v) for v in range(128)] == [0] * 64 + [2] * 12 + [3] * 12 + [4] * 12 + [5] * 12 + [1] * 16
    for v, t in STABLE.items():
        assert cc84(v) == t
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import hil_scan
    assert {hil_scan.drv_cc(t): t for t in range(6)} == STABLE


def test_cc84_on_the_host_every_value():
    """the host build's map, all 128 values, from either side (a band edge after another type)"""
    lines, t = [], 100
    for v in list(range(128)) + list(range(127, -1, -1)):
        lines += [cc(t, 84, v), f"{t + 1} expect state p.{P_DRV_SW} {cc84(v)}"]
        t += 2
    run("drv-cc84", lines, t + 10)


def test_old_meaning_kept_for_0_and_127():
    """CC 84 0 / 127 after any other value: OFF / CLASSIC, as off / on before"""
    run("drv-cc84-old", [cc(100, 84, 105), cc(110, 84, 127), f"120 expect state p.{P_DRV_SW} 1",
                         cc(130, 84, 80), cc(140, 84, 0), f"150 expect state p.{P_DRV_SW} 0"], 200)


PATS = [re.compile(p) for p in (r"0xB0 84 (\d+)\b", r"\bcc\(\s*(?:\w+,\s*)?84,\s*(\d+)\)", r"\bcc_raw\(84,\s*(\d+)\)",
                                r"_web_midi\(0xB0,\s*84,\s*(\d+)\)")]


def test_every_cc84_selector_is_stable():
    """host scripts, cc(t, 84, v) calls and the web page select a drive type with the stable values only (the
    (84, v) pairs of test_fx_drive_reverb are 0 and 127; not scanned: a note number 84 looks the same)"""
    bad = []
    for d in ("tests", "tools", "web"):
        for dp, _, fs in os.walk(os.path.join(ROOT, d)):
            if "node_modules" in dp or "dist" in dp.split(os.sep):
                continue
            for f in fs:
                if not f.endswith((".py", ".txt", ".js")) or f in EDGES_OK:
                    continue
                p = os.path.join(dp, f)
                for i, line in enumerate(open(p, errors="replace"), 1):
                    for pat in PATS:
                        for m in pat.finditer(line):
                            if int(m.group(1)) not in STABLE:
                                bad.append(f"{os.path.relpath(p, ROOT)}:{i}: CC 84 {m.group(1)}")
    assert not bad, "CC 84 selectors off the stable values " + str(sorted(STABLE)) + ":\n" + "\n".join(bad)
