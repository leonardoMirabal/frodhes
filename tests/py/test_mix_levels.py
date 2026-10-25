# SPDX-License-Identifier: GPL-3.0-only
"""The effects' MIX / DEPTH controls (the mix audit, 2026-10-25), on the host build:

- moving the control does not zipper: the largest second difference of a render with the control swept 0 -> 127 -> 0
  (a CC step every 20 ms) over the largest of five static renders (0, 32, 64, 96, 127), per 1024-sample window: the
  chorus DEPTH was 5.3x (its taps jumped and its mix stepped once per sub-block), the delay's echo gain 1.9x (DIG) /
  3.3x (ANA) / 2.8x (TAPE) (one step a block);
- the chorus no longer cancels the bass (its 7 ms tap's comb notch near 71 Hz: C2 was -10 dB at DEPTH 64);
- the phaser keeps the chord's loudness (LUFS) at every DEPTH (DEPTH 0, the default, was -4.3 LU, A4 -7.7 dB);
- DEPTH / MIX 0 is still the effect off, bit for bit (chorus, delays); pitch stays put while the chorus DEPTH moves.

Material: the chord C3 C4 E4 G4 C5 (or one note) at velocity 100 held by the pedal from 1.0 s, every other effect
off, the developer reset (CC 10) so each render is deterministic."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import bar_measure as bm  # noqa: E402
import hostrun as H  # noqa: E402

FS = H.FS
CHORD = (48, 60, 64, 67, 72)
T_NOTE, T_SW0, STEP = 1000, 2000, 20
T_SW1 = T_SW0 + 254 * STEP
OFF = [(106, 0), (107, 0), (84, 0), (81, 0), (70, 0), (72, 0), (17, 0), (85, 0), (12, 0), (88, 0), (89, 0),
       (91, 0), (95, 0), (73, 0), (105, 0), (110, 64), (80, 0)]
CHORUS = [(85, 60)]                    # CHO on (the combined switch: 43..85)
PHASER = [(85, 100)]                   # PHA on (86..127)
DELAY = {"DIG": [(88, 40)], "ANA": [(88, 70)], "TAPE": [(88, 110)]}


def render(tag, setup=(), cc=None, value=None, sweep=False, notes=CHORD):
    ls = [H.cc(100, c, v) for c, v in OFF] + [H.cc(200, c, v) for c, v in setup]
    if cc is not None and value is not None:
        ls.append(H.cc(250, cc, value))
    ls.append(H.cc(400, 10, 127))
    ls += [f"{T_NOTE} midi 0x90 {n} 100" for n in notes] + [H.cc(T_NOTE, 64, 127)]
    if sweep:
        ls += [H.cc(T_SW0 + k * STEP, cc, v) for k, v in enumerate(list(range(1, 128)) + list(range(126, -1, -1)))]
    r = H.run(tag, ls, end_ms=T_SW1 + 500)
    return np.stack([r.L, r.R], 1)


def seg(x):
    return x[int(T_SW0 * FS / 1000):int(T_SW1 * FS / 1000)]


def jumps(x, n=1024):
    d = np.abs(np.diff(x.mean(1), 2))
    k = len(d) // n
    return d[:k * n].reshape(k, n).max(1)


def zipper(sweep, statics):
    """the largest per-window ratio of the sweep's largest second difference to the statics' largest"""
    m = np.max([jumps(s) for s in statics], 0)
    a, b = int(T_SW0 * FS / 1000) // 1024, int(T_SW1 * FS / 1000) // 1024
    return float((jumps(sweep)[a:b] / (m[a:b] + 1)).max())


def lu_re_off(x, off):
    return bm.lufs(seg(x)) - bm.lufs(seg(off))


def test_chorus_depth_does_not_zipper():
    st = [render(f"mix_cho_s{v}", CHORUS, 86, v) for v in (0, 32, 64, 96, 127)]
    z = zipper(render("mix_cho_sweep", CHORUS, 86, 0, sweep=True), st)
    assert z < 2.0, f"chorus DEPTH sweep: largest jump {z:.2f}x the statics' (5.3x before the fix)"


@pytest.mark.parametrize("typ", sorted(DELAY))
def test_delay_mix_does_not_zipper(typ):
    """the echo alone (render minus the delay off: the dry passes through the same at MIX 0)"""
    off = render(f"mix_dly_{typ}_off")
    st = [render(f"mix_dly_{typ}_s{v}", DELAY[typ], 89, v) - off for v in (0, 32, 64, 96, 127)]
    assert np.array_equal(st[0], 0 * off), "MIX 0 is the delay off"
    z = zipper(render(f"mix_dly_{typ}_sweep", DELAY[typ], 89, 0, sweep=True) - off, st)
    assert z < 1.5, f"{typ} MIX sweep: the echo's largest jump {z:.2f}x the statics' (1.9 .. 3.3x before)"


def test_chorus_keeps_the_bass_and_the_level():
    off_c2 = render("mix_cho_c2_off", notes=(36,))
    for v in (32, 64, 127):
        d = lu_re_off(render(f"mix_cho_c2_{v}", CHORUS, 86, v, notes=(36,)), off_c2)
        assert -2.0 < d < 2.0, f"chorus DEPTH {v}: C2 {d:+.2f} dB re off (-10.1 at 64 before)"
    off = render("mix_cho_off")
    assert np.array_equal(render("mix_cho_0", CHORUS, 86, 0), off), "DEPTH 0 is the chorus off"
    for v in (16, 32, 64, 96, 127):
        d = lu_re_off(render(f"mix_cho_{v}", CHORUS, 86, v), off)
        assert -1.5 < d < 0.5, f"chorus DEPTH {v}: the chord {d:+.2f} LU re off (-2.6 at 96 before)"


def test_chorus_depth_sweep_keeps_the_pitch():
    """A4's 440 Hz partial, 8192-sample windows across the DEPTH sweep, against the static DEPTH 64 render's mean:
    a tap that jumps would bend it; the chorus' own vibrato stays within a few cents"""
    def f440(x):
        out = []
        for s0 in range(int(T_SW0 * FS / 1000), int(T_SW1 * FS / 1000) - 8192, 4096):
            w = x[s0:s0 + 8192].mean(1) * np.hanning(8192)
            X = np.abs(np.fft.rfft(w, 1 << 17))
            f = np.fft.rfftfreq(1 << 17, 1 / FS)
            m = (f > 420) & (f < 460)
            i = np.where(m)[0][np.argmax(X[m])]
            a, b, c = np.log(X[i - 1:i + 2] + 1e-12)
            out.append((i + 0.5 * (a - c) / (a - 2 * b + c)) * FS / (1 << 17))
        return np.array(out)
    ref = f440(render("mix_cho_a4_off", notes=(69,)))
    sw = f440(render("mix_cho_a4_sweep", CHORUS, 86, 0, sweep=True, notes=(69,)))
    c = 1200 * np.log2(sw / ref)
    assert abs(c.mean()) < 1.5 and np.percentile(np.abs(c), 90) < 6, (c.mean(), np.percentile(np.abs(c), 90))


def test_phaser_keeps_the_level_at_every_depth():
    off = render("mix_pha_off")
    for v in (0, 32, 64, 96, 127):
        d = lu_re_off(render(f"mix_pha_{v}", PHASER, 13, v), off)
        assert abs(d) < 0.5, f"phaser DEPTH {v}: the chord {d:+.2f} LU re off (-4.3 at DEPTH 0 before)"
    for n, lab in ((69, "A4"), (36, "C2"), (60, "C4")):
        d = lu_re_off(render(f"mix_pha_0_{lab}", PHASER, 13, 0, notes=(n,)), render(f"mix_pha_off_{lab}", notes=(n,)))
        assert abs(d) < 1.0, f"phaser DEPTH 0: {lab} {d:+.2f} dB re off (A4 -7.7, C4 -9.2 before)"


def test_phaser_depth_does_not_zipper():
    st = [render(f"mix_pha_s{v}", PHASER, 13, v) for v in (0, 32, 64, 96, 127)]
    z = zipper(render("mix_pha_sweep", PHASER, 13, 0, sweep=True), st)
    assert z < 1.5, f"phaser DEPTH sweep: largest jump {z:.2f}x the statics'"
