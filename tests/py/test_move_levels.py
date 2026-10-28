# SPDX-License-Identifier: GPL-3.0-only
"""M5-02 (private plan 05): the Move's gain staging on the host builds (tools/move_levels.py). Every factory preset
F01..F19, its effects as it sets them, on the Move's Classic and HQ builds (16 voices, FR_HQ):

- the worst case (the pedal down, two 16-note v127 chords, a 40-note v127 glissando: 24 steals) never reaches full
  scale (the limiter holds it under -2 dBFS);
- each preset's level (integrated LUFS over single notes C2..C6 and a pedalled chord, v100) is inside the FM-1's
  preset spread, and within 0.1 LU of the same preset on the FM-1 build (the material plays at most 5 voices: the
  Move changes nothing there)."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import move_levels as ml  # noqa: E402


def test_move_levels_and_headroom():
    bs = ml.builds()
    with ThreadPoolExecutor(2) as ex:
        rows = list(ex.map(ml.one, [(n, b, pc) for n, b in bs.items() for pc in ml.PCS]))
    fm = {r["pc"]: r for r in rows if r["build"] == "FM-1"}
    lo, hi = min(r["lufs"] for r in fm.values()), max(r["lufs"] for r in fm.values())
    bad = []
    for r in rows:
        if r["build"] == "FM-1":
            continue
        if r["full_scale"] or r["worst_peak"] > -2.0:
            bad.append(f"{r['build']} PC {r['pc']}: worst peak {r['worst_peak']:.2f} dBFS, {r['full_scale']} at full scale")
        if not lo - 0.05 <= r["lufs"] <= hi + 0.05 or abs(r["lufs"] - fm[r["pc"]]["lufs"]) > 0.1:
            bad.append(f"{r['build']} PC {r['pc']}: {r['lufs']:.2f} LUFS (FM-1 {fm[r['pc']]['lufs']:.2f}, spread "
                       f"{lo:.1f} .. {hi:.1f})")
    assert not bad, "\n".join(bad)
