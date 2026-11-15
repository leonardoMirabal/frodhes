# SPDX-License-Identifier: GPL-3.0-only
"""The MIX law (0.9.13, E2: as Textura's MIX) for DELAY MIX (CC 89), DRIVE MIX (CC 83) and REVERB MIX (CC 91), on the
FM-1 host build and the HQ build (the Move, plugin and MPC: FR_HQ, FR_REV_HQ):

- 0 .. 64 the dry whole and the wet rising to whole, 64 both whole, past 64 the wet whole and the dry falling, 127 the
  wet alone: the gains fx.c runs with (host_state "mixg": the delay's dry / echo, the reverb's dry / send, the drive's
  dry / wet, Q15) at 0 / 32 / 64 / 96 / 127, the same table on both builds;
- MIX 0 is each effect off, bit for bit, and the dry before the first echo at DELAY MIX 64 is the delay off (levels
  matched at 64: the dry passes whole);
- sweeping each MIX 0 -> 127 -> 0 does not zipper (test_mix_levels' measure: the sweep's largest second difference
  over the statics');
- the MIX card on every palette (27): the bar to 64 in the accent's fill, past 64 in the accent's complement's
  (ui.c cd_comp), a 1 px grey line at 64; the text DRY / 50/50 / WET at 0 / 64 / 127 only (tests/scripts/mixbar.txt
  checks the text and the gains on the default palette)."""
import os

import numpy as np
import pytest

import hostrun as H
import test_mix_levels as ML
from hostrun import cc

HQ_DEFS = ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1", "-DHQ_DEFAULT=1"]   # release_diff
SETUP = [(88, 40), (84, 127), (81, 60), (95, 40)]   # DIG delay, FDRV drive (AMOUNT 60), a reverb TYPE
# MIX: (delay dry, echo), (reverb dry, send), (drive dry, wet); Q15 (the reverb's send: RV_SEND's full 32766)
LAW = {0: (32768, 0, 32768, 0, 32768, 0), 32: (32768, 16384, 32768, 16384, 32768, 16384),
       64: (32768, 32768, 32768, 32766, 32768, 32768), 96: (16124, 32768, 16124, 32766, 16128, 32768),
       127: (0, 32768, 0, 32766, 0, 32768)}


def hq_bin():
    b = os.path.join(H.ROOT, "build", "host", "frodhes_mixlaw_hq")
    return H.build(b, HQ_DEFS)


@pytest.mark.parametrize("build", ["fm1", "hq"])
def test_law_table(build):
    ls, t = [cc(100, c, v) for c, v in SETUP], 600
    for v, g in LAW.items():
        ls += [cc(t, 83, v), cc(t, 89, v), cc(t, 91, v), f"{t + 300} expect state mixg {'/'.join(map(str, g))}"]
        t += 400
    H.run(f"mixlaw_table_{build}", ls, end_ms=t + 100, binary=H.BIN if build == "fm1" else hq_bin())


def chord(tag, setup, binary=H.BIN, sweep=None, end=4000):
    ls = [cc(100, c, v) for c, v in ML.OFF] + [cc(200, c, v) for c, v in setup] + [cc(400, 10, 127)]
    ls += [f"{ML.T_NOTE} midi 0x90 {n} 100" for n in ML.CHORD] + [cc(ML.T_NOTE, 64, 127)]
    if sweep is not None:
        ls += [cc(ML.T_SW0 + k * ML.STEP, sweep, v) for k, v in enumerate(list(range(1, 128)) + list(range(126, -1, -1)))]
        end = ML.T_SW1 + 500
    r = H.run(tag, ls, end_ms=end, binary=binary)
    return np.stack([r.L, r.R], 1)


@pytest.mark.parametrize("build", ["fm1", "hq"])
def test_mix_zero_is_the_effect_off(build):
    b = H.BIN if build == "fm1" else hq_bin()
    off = chord(f"mixlaw_off_{build}", [], b)
    assert np.array_equal(chord(f"mixlaw_dly0_{build}", [(88, 40), (89, 0)], b), off), "DELAY MIX 0 is the delay off"
    assert np.array_equal(chord(f"mixlaw_rv0_{build}", [(95, 40), (91, 0)], b), off), "REVERB MIX 0 is the reverb off"
    drv = chord(f"mixlaw_drvoff_{build}", [(84, 127), (81, 60), (84, 0)], b)
    assert np.array_equal(chord(f"mixlaw_drv0_{build}", [(84, 127), (81, 60), (83, 0)], b), drv), \
        "DRIVE MIX 0 is the drive off"


@pytest.mark.parametrize("build", ["fm1", "hq"])
def test_delay_mix_64_keeps_the_dry_whole(build):
    """the chord's first 150 ms (before the DIG delay's first echo) at DELAY MIX 64 and 32 against the delay off: the
    same samples (the old law eased the dry: -0.9 dB at 64)"""
    b = H.BIN if build == "fm1" else hq_bin()
    a, e = int(ML.T_NOTE * H.FS / 1000), int((ML.T_NOTE + 150) * H.FS / 1000)
    off = chord(f"mixlaw_d64off_{build}", [], b, end=1500)[a:e]
    for v in (32, 64):
        x = chord(f"mixlaw_d{v}_{build}", [(88, 40), (89, v)], b, end=1500)[a:e]
        assert np.abs(x - off).max() <= 2.0 / 32768, f"DELAY MIX {v}: the dry before the echo moved"


@pytest.mark.parametrize("what,setup,cc_n", [("reverb", [(95, 40)], 91), ("drive", [(84, 127), (81, 60)], 83)])
def test_mix_sweep_does_not_zipper(what, setup, cc_n):
    st = [chord(f"mixlaw_{what}_s{v}", setup + [(cc_n, v)], end=ML.T_SW1 + 500) for v in (0, 32, 64, 96, 127)]
    z = ML.zipper(chord(f"mixlaw_{what}_sweep", setup + [(cc_n, 0)], sweep=cc_n), st)
    assert z < 1.5, f"{what} MIX sweep: largest jump {z:.2f}x the statics'"


COLOR_ACC = [0xE3C1, 0xFD0E, 0xFC50, 0xFCB5, 0xFCB9, 0xFCDC, 0xFCFF, 0xED1F, 0xDD1F, 0xC53F, 0xACFF, 0x94FF, 0x957F,
             0x8E1F, 0x8EFF, 0x8FBE, 0x8F9B, 0x8777, 0x8774, 0x8790, 0x9F90, 0xB7AF, 0xD7AF, 0xF7CF, 0xFF0F, 0xFE2F,
             0xF8C3]                                   # ui.c COLOR_ACC


def rgb565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


def comp(c):
    """ui.c cd_comp"""
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    r8, g8, b8 = r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2
    s = max(r8, g8, b8) + min(r8, g8, b8)
    return rgb565(s - r8, s - g8, s - b8)


def shade(c, pct):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r * pct // 100) << 11 | (g * pct // 100) << 5 | (b * pct // 100)


def count(im, c):
    want = ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
    return int(np.sum((im == np.array(want)).all(axis=-1)))


def test_mix_card_on_every_palette():
    """per palette: DISPLAY's COLOR one step on, HOME, 7 SELECT detents to DRIVE, DRIVE MIX 32 then 96"""
    import test_ui_meters as M
    ls, t, shots = [cc(100, 84, 127), cc(100, 81, 60)], 300, []
    for i in range(len(COLOR_ACC)):
        ls += [f"{t + 30 * k} btn 7" for k in range(4)]
        t += 150
        if i:
            ls.append(f"{t} turn 2 1")
        ls.append(f"{t + 100} btn 8")
        t += 200
        ls += [f"{t + 20 * k} turn 0 1" for k in range(7)]
        t += 200
        ls += [f"{t} expect state page DRIVE", cc(t, 83, 32), f"{t + 400} shot", cc(t + 450, 83, 96), f"{t + 850} shot"]
        shots.append((i, t + 400, t + 850))
        ls.append(f"{t + 900} btn 8")
        t += 1000
    H.run("mixlaw_cards", ls, end_ms=t + 100)
    bad = []
    for i, t32, t96 in shots:
        a, b = M.ppm("mixlaw_cards", t32), M.ppm("mixlaw_cards", t96)
        acc = COLOR_ACC[i]
        wf, line = shade(comp(acc), 45), rgb565(150, 150, 156)
        got = (count(a, wf), count(b, wf), count(a, line), count(b, line))
        if not (got[0] == 0 and got[1] > 300 and got[2] >= 20 and got[3] >= 20):
            bad.append((i, hex(acc), got))
    assert not bad, f"(palette, accent, (complement fill at 32, at 96, line at 32, at 96)): {bad}"


@pytest.mark.parametrize("build", ["fm1", "hq"])
def test_freeze_keeps_the_dry(build):
    """INFINITE's FREEZE (DECAY 127) takes nothing in, so the dry stays whole at any MIX (what is played over the
    frozen tail stays heard); DECAY down from it, MIX 127's law again (the dry out)"""
    ls = [cc(100, 95, 110), cc(110, 91, 127), cc(120, 92, 127), "700 expect state mixg 32768/0/32768/32766/32768/0",
          cc(800, 92, 100), "1300 expect state mixg 32768/0/0/32766/32768/0"]
    H.run(f"mixlaw_freeze_{build}", ls, end_ms=1400, binary=H.BIN if build == "fm1" else hq_bin())
