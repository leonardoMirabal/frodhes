# SPDX-License-Identifier: GPL-3.0-only
"""dry_dist's attack cells (aH2..aH6, the strike's 0..150 ms) are read at the nominal f0, never retuned (2026-11-10).
In 150 ms the window's main lobe is +-27 Hz wide: at the low keys the H1 band (+-3 %) holds no peak of its own and
tuned() took its edge for one (the FM-1: A1 locked at 45.7 .. 47.5 Hz, and a 4 ms shift of the strike moved VIBE-B 33/64
aH6 from -54.8 to -26.8; the host: VIBES A1 at 53.5 Hz, E1 at 39.7, the band's lower edge). The analysis window
(150..650 ms) keeps its retune (the FM-1's clock on USB, a few hundred ppm). Re-measured on v0.9.9 and stage 1: only
aH cells move (80 / 88 of 6032), by at most 2.9 dB."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import dry_dist as dd  # noqa: E402
from hostrun import BIN, FS, build, run  # noqa: E402


def test_vibes_low_keys_lock_on_the_band_edge_in_the_strike_window():
    """why: the retune in the strike's window reads the band's edge at VIBES A1 / E1 (2.7 .. 3.6 % low)"""
    for key in (33, 28):
        ev = dd.type_base_lines(40) + ["50 midi 0xB0 10 127", "100 midi 0xB0 111 127", "110 midi 0xB0 106 0", "120 midi 0xB0 79 0",
              f"400 midi 0x90 {key} 64", f"1400 midi 0x80 {key} 0"]
        r = run(f"ddat-{key}", ev, 1600)
        x = (r.L.astype(float) + r.R.astype(float)) / 2
        a = int(dd.strike(x, 0.4, FS) * FS)
        p, fr = dd.spectrum(x[a:a + int(0.15 * FS)], FS, 8)
        assert dd.tuned(p, fr, [dd.f0(key)])[0] / dd.f0(key) < 0.98


def test_attack_cells_never_retuned(tmp_path, monkeypatch):
    """every tuned() call of a measured script is on the analysis window (2 Hz bins), none on the strike's (0.83 Hz)"""
    build(BIN)
    seen = []
    real = dd.tuned

    def spy(p, fr, funds):
        seen.append(fr[1] - fr[0])
        return real(p, fr, funds)
    monkeypatch.setattr(dd, "tuned", spy)
    cells = dd.measure_tag(BIN, "FXOFF-ST77-A", str(tmp_path))
    assert seen and min(seen) > 1.5, f"tuned() on a {min(seen):.2f} Hz grid (the strike's window)"
    assert any(k.startswith("aH") for c in cells.values() for k in c)
