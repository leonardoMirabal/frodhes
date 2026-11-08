# SPDX-License-Identifier: GPL-3.0-only
"""Two cores in chunks (FR_C1, frodhes.c app_audio, fx.c fxc_o / fxc_e, engine.c eng_chunk): a block runs as C1_SUB
chunks, each effect's per-block logic at the first chunk, its loops on the chunk, its epilogue at the last. The
chunks must render what the one-core 14-voice HQ build renders, sample for sample, with no delay:

- every effect page's controls, swept 0 -> 127 -> 0 under a ringing chord (tools/control_sweep.py's cases: one or
  more per effect, TYPE and engine), at C1_SUB 8;
- the HEAVY take (every effect on, 14 voices) at C1_SUB 1, 2, 4 and 8, and with the job run late in the block
  (order 1) and refused;
- the two places that read the block's future, where a chunk cannot (the block's later samples are not rendered
  yet), stay within their measured size and add no click (the differential click check against the one-core build):
  COMP switched on from rest (the detector from the peak of the block before and the first chunk; one core: the
  block's) and KHLON's 17 ms dip at an AMOUNT mode change (the dip's dry gain from the block before and the first
  chunk; one core: the dip's first block)."""
import os
import sys

import numpy as np
import pytest

from hostrun import ROOT, build, run, rms_db
from test_c1_pipeline import ref_bin, take

sys.path.insert(0, os.path.join(ROOT, "tools"))
import control_sweep as cs  # noqa: E402


def c1_bin(sub=8):
    return build(os.path.join(ROOT, "build", "host", f"c1s{sub}", "frodhes"), ["-DFR_C1=1", f"-DC1_SUB={sub}u"])


def render(tag, text, binary, env=None):
    r = run(tag, [l for l in text.splitlines() if l], binary=binary, env=env)
    return np.stack([r.L, r.R]).T.astype(int)


def first_diff(a, b):
    if a.shape != b.shape:
        return 0
    d = np.flatnonzero(np.any(a != b, axis=1))
    return int(d[0]) if d.size else None


# one or more cases per effect (tools/control_sweep.py), the ones that switch TYPEs and fades included
SWEEP = ["rhodes TONE", "rhodes TYPE SPREAD", "vibes MOTOR DEPTH", "vibes MOTOR on/off", "marimba STRIKE",
         "comp THRESH", "comp RATIO", "comp RELEASE", "drive FDRV AMOUNT", "drive TSH8 TONE", "drive OHCD MIX",
         "drive BHLUE AMOUNT", "drive KHLON TONE", "drive TYPE", "filter LP18 FREQ", "filter BP FREQ",
         "filter TYPE", "lfo SHAPE", "trem AM DEPTH", "trem PAN RATE", "wah DEPTH", "trem/wah EFFECT",
         "phaser FDBK", "chorus DEPTH", "cho/pha EFFECT", "delay DIG TIME", "delay ANA FDBK", "delay TAPE TIME",
         "delay TAPE MIX", "delay EFFECT", "rev HALL auto MIX", "rev PLATE MOD", "rev INF SIZE", "rev TYPE",
         "SPREAD", "LEVEL"]


@pytest.mark.parametrize("name", SWEEP)
def test_sweep_as_one_core(name):
    c = cs.BY_NAME[name]
    t = cs.script(c, sweep=True)
    tag = "c1s_" + name.replace(" ", "_").replace("/", "-")
    a = render(tag, t, c1_bin())
    r = render(tag + "_ref", t, ref_bin())
    d = first_diff(a, r)
    assert d is None, f"{name}: two cores differ from one from {d / 44100:.3f} s"


@pytest.mark.parametrize("sub,env", [(1, None), (2, None), (4, None), (8, "order=1"), (4, "order=1,refuse=3")])
def test_heavy_any_chunking(sub, env):
    ls = take(0)
    a = run(f"c1s_heavy_{sub}_{env}", ls, end_ms=6000, binary=c1_bin(sub), env=env and {"FRODHES_C1": env})
    r = run("c1s_heavy_ref", ls, end_ms=6000, binary=ref_bin())
    assert rms_db(r.L) > -40
    assert np.array_equal(a.L, r.L) and np.array_equal(a.R, r.R), f"C1_SUB {sub} {env}: not the one-core build"


def _flip(case, v0, v1, b, tag, onset=None):
    """case's control held at v0, moved to v1 at T_FLIP; onset: the chord struck at T_FLIP instead of T_NOTE"""
    t = cs.script(case, v0, flip=v1)
    if onset:
        ls = [l for l in t.splitlines() if not (len(l.split()) > 2 and l.split()[2] == "0x90")]
        ls += [f"{cs.T_FLIP} midi 0x90 {n} 100" for n in cs.CHORD]
        t = "\n".join(sorted(ls, key=lambda s: int(s.split()[0])))
    return render(tag, t, b)


def _dev(case, v0, v1, tag, onset=None):
    """(the two builds' moved renders, their click ratios (control_sweep's), the first differing frame)"""
    out = []
    for b, nm in ((ref_bin(), "r"), (c1_bin(), "c")):
        mv = _flip(case, v0, v1, b, f"{tag}_{nm}m", onset)
        if onset:
            out.append((mv, 0.0))
            continue
        st = _flip(case, v0, v0, b, f"{tag}_{nm}s")
        en = _flip(case, v1, v1, b, f"{tag}_{nm}e")
        out.append((mv, cs.click_ratio(mv, st, en)))
    (rm, rc), (cm, cc) = out
    return rm, cm, rc, cc, first_diff(cm, rm)


@pytest.mark.parametrize("eng,chord", [("rhodes", [48, 64, 67, 69, 72]), ("vibes", [36, 43, 52]),
                                       ("marimba", [48, 64, 67, 69, 72])])
def test_khlon_dip_estimate(eng, chord):
    """KHLON AMOUNT 127 -> 0 crosses its mode: the 17 ms dip through the dry at the wet's level. Two cores take the
    dry's gain from the block before and the first chunk: within 4 dB of one core's (24 dips, build/c1sub/khdip.py:
    1.3 dB mean, 3.5 at most), no click (control_sweep's click ratio as one core's within 1)"""
    keep = list(cs.CHORD)
    cs.CHORD[:] = chord
    try:
        c = cs.Case(f"kh {eng}", 81, [(84, 70), (82, 64), (83, 127)], engine=eng)
        rm, cm, rc, cc, d = _dev(c, 127, 0, f"c1s_kh_{eng}")
    finally:
        cs.CHORD[:] = keep
    assert d is not None and abs(d - cs.ms(cs.T_FLIP)) < 512, "no dip where the AMOUNT moved"
    b = d // 256 + 1                                    # the dip's middle block: the dry x kh_dg in both
    s = slice(b * 256, b * 256 + 256)
    g = 20 * np.log10(np.sqrt(np.mean(cm[s].astype(float) ** 2)) / np.sqrt(np.mean(rm[s].astype(float) ** 2)))
    assert abs(g) < 4.0, f"{eng}: the dip's dry {g:+.2f} dB off one core's"
    assert cc < rc + 1.0 and cc < 5.0, f"{eng}: click ratio {cc:.2f} on two cores, {rc:.2f} on one"
    e = cm[d:] - rm[d:]                                 # a few blocks on, as one core (but the last bit)
    assert np.abs(e[4 * 256:]).max() <= 16, "the dip's difference does not end with the dip"


@pytest.mark.parametrize("eng,chord,onset", [("rhodes", [48, 64, 67, 69, 72], False),
                                             ("rhodes", [36, 43, 52], False),
                                             ("rhodes", [36, 43, 52], True), ("marimba", [36, 43, 52], True)])
def test_comp_switch_on_estimate(eng, chord, onset):
    """COMP switched on from rest at the slowest ATTACK (the detector's start lasts longest): the level over the
    first 100 ms within 3 dB of one core's (ringing: +1.3 dB at most; the switch and a chord in the same block:
    +2.5 dB, build/c1sub/cmpdev.py), no click while the chord rings (click ratio as one core's within 1)"""
    keep = list(cs.CHORD)
    cs.CHORD[:] = chord
    try:
        c = cs.Case(f"cmp {eng}", 106, [(107, 120), (108, 127), (113, 40)], engine=eng)
        rm, cm, rc, cc, d = _dev(c, 0, 127, f"c1s_cmp_{eng}_{chord[0]}_{int(onset)}", onset)
    finally:
        cs.CHORD[:] = keep
    if d is None:
        return
    s = slice(d, d + 4410)
    g = 20 * np.log10(np.sqrt(np.mean(cm[s].astype(float) ** 2)) / np.sqrt(np.mean(rm[s].astype(float) ** 2)))
    assert abs(g) < 3.0, f"{eng}: {g:+.2f} dB off one core over the first 100 ms"
    if not onset:
        assert cc < rc + 1.0, f"{eng}: click ratio {cc:.2f} on two cores, {rc:.2f} on one"
