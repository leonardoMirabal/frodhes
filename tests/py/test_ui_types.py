# SPDX-License-Identifier: GPL-3.0-only
"""Every TYPE name is drawn alike on the main page's K1 card, whatever the engine: a short name (VIBES) must not
jump to the large font while the long Rhodes names use the small one (a bug found 2026-10-17). The METERS look
(host_state "row0" known): K1 is a row, its choice name right of the segments; before it: the classic K1 card."""
import os
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
    sel = [(80, 8), (80, 40), (80, 72), (80, 104), (79, 64)]           # ST77 SUIT FELT DYNO, then VIBES (CC 79)
    lines = ["50 btn 8", "60 expect state row0 ~"]
    for i, (cc, v) in enumerate(sel):
        lines += [f"{100 + 300 * i} midi 0xB0 {cc} {v}", f"{300 + 300 * i} shot"]
    lines.append(f"{300 * len(sel) + 200} end")
    open(OUT + ".txt", "w").write("\n".join(lines) + "\n")
    out = subprocess.run([BIN, OUT, OUT + ".txt"], capture_output=True, text=True).stdout
    meters = "expect state row0: no such state" not in out
    heights = []
    for i in range(len(sel)):
        im = ppm(f"{OUT}-{300 + 300 * i}.ppm").astype(int)
        if meters:                                                    # the K1 row's text right of the segments
            card = im[46 + 2:46 + 36, 172:232].sum(axis=2) * (np.ptp(im[46 + 2:46 + 36, 172:232], axis=2) <= 40)
        else:
            card = im[63 + 18:63 + 66, 2:118].sum(axis=2)             # K1 card, below its label row
        rows = np.nonzero((card > 0.6 * card.max()).any(axis=1))[0]
        heights.append(rows[-1] - rows[0] + 1)
    assert len(set(heights)) == 1, heights
