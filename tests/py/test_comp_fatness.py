# SPDX-License-Identifier: GPL-3.0-only
"""The compressor's stage-2 behaviour (2026-11-10): (1) the program-dependent release keeps the beating of a loud chord
out of the gain: COMP 4:1 ATK 40 REL 48 (the default ATTACK, RELEASE near its default) put the v127 mid chord's
intermodulation at -43 dB (tools/dry_dist.py's chords, FX off keeps the compressor; the dry piano -79); (2) the auto
make-up: with THRESH -30 / -40 dB at 4:1 / 20:1 and MAKEUP 0 the critical-band loudness (tools/cb_loud.py, binaural)
averaged over C2 / C4 / C6 x v40 / 80 / 127, C5 v100 and chords equals the compressor off; (3) the fatness, the sustain
(1.0 .. 1.5 s) over the attack (0 .. 60 ms) brought up by the compressor, at least 0.9.9's on the v127 chord (0.9.9:
THRESH -30 20:1 slow +1.00, -40 20:1 slow -1.75, -40 20:1 fast +6.86 dB), its peak never over the bypass'"""
import os
import sys

import numpy as np

import hostrun

sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
import dry_dist  # noqa: E402
import fx_level_audit as A  # noqa: E402

S = A.SIGNALS
SIG = {k: S[k] for k in ("C2v40", "C2v80", "C2v127", "C4v40", "C4v80", "C4v127", "C6v40", "C6v80", "C6v127",
                         "chd80", "chd127")}
SIG["C5v100"] = [(72, 100)]
SIG["lowchd127"] = [(n, 127) for n in (33, 40, 45, 49)]
SETS = {"t96r4": [(107, 96), (112, 56)], "t96r20": [(107, 96), (112, 120)], "t127r4": [(107, 127), (112, 56)],
        "t127r20": [(107, 127), (112, 120)]}
FAT = {("t96r20", 100): 1.00, ("t127r20", 100): -1.75, ("t127r20", 40): 6.86}   # 0.9.9's chd127 (build/s2 audit)


def test_chord_beating_clean():
    ls = dry_dist.script(111, 0, 0, keys=False)
    ls += ["150 midi 0xB0 106 127", "152 midi 0xB0 107 50", "154 midi 0xB0 112 48", "156 midi 0xB0 108 40",
           "158 midi 0xB0 109 48"]
    x = dry_dist.render(hostrun.BIN, "cfat-beat", ls, os.path.join(hostrun.OUT, "comp_fat"))
    im = {k: v["imd"] for k, v in dry_dist.measure_wave(x).items() if k.startswith("chord/")}
    for k, v in im.items():
        assert v <= -60.0, f"{k}: imd {v:.1f} dB at COMP 4:1 ATK 40 REL 48 (limit -60, dry_dist's floor)"


def run(sets, atk):
    A.OUT = os.path.join(hostrun.OUT, "comp_fat")
    jobs = []
    for s, nn in SIG.items():
        jobs.append((hostrun.BIN, f"off|{s}", 0, A.CLEAN, 0, nn))
        for k, c in sets.items():
            jobs.append((hostrun.BIN, f"{k}|{s}", 0, A.CLEAN + [(106, 127), (113, 0), (108, atk)] + c, 0, nn))
    return A.run_jobs(jobs, 2)


def test_auto_makeup_and_fatness():
    for atk in (40, 100):
        r = run(SETS, atk)
        for k in SETS:
            d = [r[f"{k}|{s}"]["cb"] - r[f"off|{s}"]["cb"] for s in SIG]
            # THRESHOLD: |mean| <= 0.3 -> every signal <= +0.05 cb (stage 2, 2026-11-10: the auto make-up only up to the
            # reduction, so nothing is louder than the bypass; the average is below it by design, -1.2 .. -2 cb)
            assert max(d) <= 0.05, f"{k} ATK {atk}: a signal louder than the bypass, {max(d):+.2f} cb"
            on, off = r[f"{k}|chd127"], r[f"off|chd127"]
            assert max(on["pkL"], on["pkR"]) <= max(off["pkL"], off["pkR"]) + 0.05, f"{k} ATK {atk}: the chord's peak"
            if (k, atk) in FAT:
                fat = (on["sus"] - on["att"]) - (off["sus"] - off["att"])
                assert fat >= FAT[(k, atk)], f"{k} ATK {atk}: fatness {fat:+.2f} dB, 0.9.9 {FAT[(k, atk)]:+.2f}"
