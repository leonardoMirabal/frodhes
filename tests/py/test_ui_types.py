# SPDX-License-Identifier: GPL-3.0-only
"""Every TYPE name is drawn alike on the main page's K1 card, whatever the engine: a short name (VIBES) must not
jump to the large font while the long Rhodes names use the small one (a bug found 2026-10-17). The METERS look
(host_state "row0" known): K1 is a row with the whole name (STAGE 77 .. EARLY FELT, VIBES) at its right, in the large
face (2026-10-18: names, not segments); before it: the classic K1 card."""
import os
import re
import subprocess

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
OUT = os.path.join(ROOT, "build", "host", "ui_types")


def ppm(path):
    d = open(path, "rb").read()
    head = d.split(b"\n", 1)
    w, h = 240, 240
    return np.frombuffer(head[1][-w * h * 3:], np.uint8).reshape(h, w, 3)


def test_type_names_same_size():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    sel = [(80, 8), (80, 40), (80, 72), (80, 104), (79, 0)]           # ST77 SUIT FELT DYNO, then VIBES (CC 79)
    lines = ["50 btn 8", "60 expect state row0 ~"]
    for i, (cc, v) in enumerate(sel):
        lines += [f"{100 + 300 * i} midi 0xB0 {cc} {v}", f"{300 + 300 * i} shot", f"{300 + 300 * i} expect state row0 ~",
                  f"{300 + 300 * i} expect state card.2.0 ~"]
    lines.append(f"{300 * len(sel) + 200} end")
    open(OUT + ".txt", "w").write("\n".join(lines) + "\n")
    out = subprocess.run([BIN, OUT, OUT + ".txt"], capture_output=True, text=True).stdout
    meters = "expect state row0: no such state" not in out
    heights = []
    for i in range(len(sel)):
        im = ppm(f"{OUT}-{300 + 300 * i}.ppm").astype(int)
        if meters:                                                    # the K1 row's name, at the right
            card = im[46 + 2:46 + 36, 172:232].sum(axis=2) * (np.ptp(im[46 + 2:46 + 36, 172:232], axis=2) <= 40)
        else:
            card = im[63 + 18:63 + 66, 2:118].sum(axis=2)             # K1 card, below its label row
        rows = np.nonzero((card > 0.6 * card.max()).any(axis=1))[0]
        heights.append(rows[-1] - rows[0] + 1)
    # one face: the same height within 1 px (round letters, S O D, overshoot by a row of antialiasing, which crosses
    # the threshold on a black row surface: the colour candidates of developer CC 2); the large face is ~20 px
    assert max(heights) - min(heights) <= 1, heights
    if meters:                                                        # every TYPE's whole name, in the large face

        rows = re.findall(r'expect state row0 = "~" \(is "([^"]*)"\)', out)[1:]
        names = re.findall(r'expect state card.2.0 = "~" \(is "([^"]*)"\)', out)
        assert names == ["STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO", "VIBES"], names
        assert len(rows) == len(sel) and all(r.split()[0] == "choice" and r.split()[-1] == "L" for r in rows), rows
        assert heights[0] >= 16, heights                                # the large face (24 px: ~18), not the small
