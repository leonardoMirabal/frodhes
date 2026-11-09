# SPDX-License-Identifier: GPL-3.0-only
"""The MANUAL's promises about moving and switching, one assertion each (docs/MANUAL.md; the quote is in each case).
Each is a pedalled chord (C2 C3 E3 G3 C4, v110) with one change at TE ms, judged by the DIFFERENTIAL click detector:
the largest |second difference| from 3 ms before to 30 ms after the change, against the larger of the same render
without the change and the render that had the new setting from the start; over 10x is a click. Level claims compare
RMS over 50 ms windows.

The promises 0.9.5 does not keep are expected failures with their finding (private/BUGHUNT-095.md); strict=False."""
import numpy as np
import pytest

import hostrun as hr

TE = 1500
CLEAN = [hr.cc(20, 81, 0), hr.cc(21, 91, 0), hr.cc(21, 70, 0), hr.cc(22, 106, 0), hr.cc(22, 107, 0), hr.cc(22, 113, 0)]
_n = [0]


def chord(notes=(36, 48, 52, 55, 60), v=110, t=300):
    ls = [hr.cc(t - 5, 64, 127)]
    for n in notes:
        ls += hr.note(t, n, v, 3000)
    return ls


def R(lines, end=2400):
    _n[0] += 1
    return hr.run(f"claim{_n[0]}", CLEAN + lines, end)


def d2(x):
    return np.abs(np.diff(np.column_stack(x), 2, axis=0)).max(1)


def click_x(setup, change, end_setup=None, notes=(36, 48, 52, 55, 60)):
    """the change's click ratio. end_setup: the setup lines that give the state after the change from the start
    (default: the change's lines moved to t 40)"""
    if end_setup is None:
        end_setup = [l.replace(f"{TE} ", "40 ", 1) for l in change]
    a = R(setup + chord(notes) + change)
    b = R(setup + chord(notes))
    c = R(setup + end_setup + chord(notes))
    i0, i1 = int((TE - 3) * 44.1), int((TE + 30) * 44.1)
    ref = max(d2((b.L, b.R))[i0:i1].max(), d2((c.L, c.R))[i0:i1].max())
    return d2((a.L, a.R))[i0:i1].max() / max(ref, 1.0), a, b


def lvl(r, t0, t1):
    return hr.rms_db(r.L[int(t0 * 44.1):int(t1 * 44.1)])


def cc(n, v, t=TE):
    return [hr.cc(t, n, v)]


CLICKS = [
    # (id, MANUAL quote, setup, change, expected failure)
    ("fx-bypass", "FX switches all effects off at once and back on ... with a short crossfade",
     cc(84, 76, 30) + cc(81, 60, 31) + cc(91, 70, 32) + cc(13, 80, 33) + cc(12, 127, 34), cc(111, 127), None),
    ("drive-type", "A new type takes over smoothly: the one playing fades out, then the new one fades in",
     cc(84, 69, 30) + cc(81, 70, 31), cc(84, 93), None),
    ("drive-amount-glides", "Moving any of the knobs glides, so you can turn them while you play (AMOUNT to 0)",
     cc(84, 127, 30) + cc(81, 20, 31), cc(81, 0), None),
    ("khlon-last-step", "Turning AMOUNT onto its last step (10.0), or back off it, passes ... without a click",
     cc(84, 64, 30) + cc(81, 126, 31), cc(81, 127), None),
    ("filter-glides-in", "turning FREQ down glides into the filter, without a click",
     cc(70, 46, 30) + cc(74, 127, 31), cc(74, 100), None),
    ("delay-off", "Switching the delay off or on while echoes ring does not click",
     cc(88, 40, 30) + cc(89, 90, 31) + cc(90, 50, 32), cc(88, 0), None),
    ("delay-to-tape", "Switching to or from TAPE while echoes ring fades the old type out, then the new one in",
     cc(88, 40, 30) + cc(89, 90, 31) + cc(90, 50, 32), cc(88, 110), None),
    ("reverb-type", "A new type takes over at once: the old tail fades out in a few milliseconds",
     cc(95, 40, 30) + cc(91, 80, 31), cc(95, 80), None),
    ("reverb-mix-glides", "MIX, DECAY and TONE glide when moved (MIX 90 -> 0)",
     cc(95, 40, 30) + cc(91, 90, 31), cc(91, 0), None),
    ("phaser-switch", "Every effect keeps its state while bypassed (switching on never clicks): the phaser off",
     cc(13, 80, 30) + cc(12, 127, 31), cc(12, 0), None),
    ("chorus-switch", "Every effect keeps its state while bypassed (switching on never clicks): the chorus off",
     cc(86, 64, 30) + cc(85, 64, 31), cc(85, 0), None),
    ("tremolo-switch", "Every effect keeps its state while bypassed: TREM off at a deep setting",
     cc(17, 64, 30) + cc(21, 127, 31) + cc(18, 127, 32), cc(17, 0), None),
]


@pytest.mark.parametrize("cid,quote,setup,change,known", CLICKS, ids=[c[0] for c in CLICKS])
def test_no_click(cid, quote, setup, change, known, request):
    if known:
        request.applymarker(pytest.mark.xfail(reason=known, strict=False))
    x, _, _ = click_x(setup, change)
    assert x <= 10, f"{quote}: a click {x:.1f}x the unchanged renders"


def test_synced_delay_follows_tempo():
    """DELAY: 'A synced delay's time glides to a new tempo without clicks' (DIG, 1/4, MIX 127, FDBK 100; one staccato
    chord, BPM 120 -> 90 on TEMPO): the echoes keep coming. The time crossfades to the new one (0.9.6, #3), so the
    echo due 500 ms after the last one comes 667 ms after it (the tap moved back): over one new delay time (750 ms)
    the echoes hold the energy of the quieter held tempo (within 3 dB), and the first 350 ms are no dropout (within
    8 dB; 0.9.5: -23 dB, the read head standing still on one sample)"""
    s = CLEAN + cc(88, 40, 30) + cc(89, 127, 31) + cc(25, 83, 32) + cc(26, 100, 33) + \
        [f"{40 + 3 * i} turn 0 1" for i in range(13)] + [l for n in (60, 64, 67) for l in hr.note(300, n, 110, 80)]
    a = hr.run("claim-sync-a", s + [f"{TE} turn 2 -30"], 2600)
    b = hr.run("claim-sync-b", s, 2600)
    c = hr.run("claim-sync-c", s + ["200 turn 2 -30"], 2600)
    la, lb, lc = (lvl(r, TE + 50, TE + 400) for r in (a, b, c))
    assert la >= min(lb, lc) - 8, f"echoes after the tempo change {la:.1f} dBFS (held tempos {lb:.1f} / {lc:.1f})"
    la, lb, lc = (lvl(r, TE + 50, TE + 800) for r in (a, b, c))
    assert la >= min(lb, lc) - 3, f"echoes over one new delay time {la:.1f} dBFS (held tempos {lb:.1f} / {lc:.1f})"


def test_vibes_motor_switch():
    """VOICE 3/3 MOTOR: 'it fades in and out over about 50 ms, so switching never clicks'; 'Switching the motor on or
    off under a held chord starts or stops the pulse without a jump in level'"""
    vib = cc(79, 0, 25) + cc(47, 127, 26)
    x, a, b = click_x(vib, cc(47, 0), notes=(53, 57, 60, 65, 69))
    assert x <= 10, f"motor off: a click {x:.1f}x"
    assert abs((lvl(a, TE + 100, TE + 400) - lvl(a, TE - 300, TE)) - (lvl(b, TE + 100, TE + 400) - lvl(b, TE - 300, TE))) < 3


@pytest.mark.parametrize("name,setup,tol,known", [
    ("chorus", cc(86, 64, 30) + cc(85, 64, TE), 1.5, None),
    ("wah", cc(18, 90, 30) + cc(17, 127, TE), 1.5, None),
])
def test_switch_keeps_level(name, setup, tol, known, request):
    """'Every effect is set to keep about the same volume when you switch it on' (section 11); the phaser 'keeps the
    level'; the wah 'keeps the sound's level'; THRESH from OFF: 'MAKEUP at +2.8 dB, which keeps the volume about the
    same'"""
    if known:
        request.applymarker(pytest.mark.xfail(reason=known, strict=False))
    a, b = R(setup + chord()), R([l for l in setup if not l.startswith(f"{TE} ")] + chord())
    step = (lvl(a, TE + 60, TE + 300) - lvl(a, TE - 300, TE - 10)) - (lvl(b, TE + 60, TE + 300) - lvl(b, TE - 300, TE - 10))
    assert abs(step) <= tol, f"{name} switched on: {step:+.2f} dB"


@pytest.mark.parametrize("notes", [(36, 48, 52, 55, 60), (60, 64, 67, 72, 76)], ids=["low", "high"])
@pytest.mark.parametrize("depth", [30, 80, 127])
def test_phaser_keeps_level_over_its_sweep(notes, depth):
    """PHASER: 'The phaser keeps the level at every DEPTH (a chord's loudness matches the phaser switched off; its
    notches sweep through the sound, so a single note still dips and swells as they pass it...)': judged over whole
    sweeps (4 s at SPEED 40, ~4 cycles), a low and a high chord, within 1.5 dB. (BUGHUNT #2's "2 dB quieter on low
    chords" was a 240 ms window: wherever the notches stood then, -3 .. +1.5 dB by the chord and the phase)"""
    setup = cc(13, depth, 30) + cc(14, 40, 30) + cc(12, 127, TE)
    ch = [hr.cc(295, 64, 127)] + [x for n in notes for x in hr.note(300, n, 110, 6000)]
    a, b = R(setup + ch, 6500), R([l for l in setup if not l.startswith(f"{TE} ")] + ch, 6500)
    step = (lvl(a, TE + 100, TE + 4100) - lvl(a, TE - 1000, TE - 10)) - (lvl(b, TE + 100, TE + 4100) - lvl(b, TE - 1000, TE - 10))
    assert abs(step) <= 1.5, f"phaser DEPTH {depth} switched on: {step:+.2f} dB over its sweep"


def test_comp_from_off_keeps_the_playing_level():
    """COMPRESSOR: THRESH turned up from OFF 'comes on with MAKEUP ... which keeps the volume about the same': a chord
    played after it within 1 dB of the same chord with the compressor off (BUGHUNT #6: 0.9.5 woke it at THRESH -0.3 dB
    with +2.8 dB of make-up, the chord +2.8 dB louder). A quiet, dying tail comes up, as under any compressor: the
    chord's first 300 ms are judged, where it is played (it was switched on there, under the tail, before)"""
    on = R([f"{40 + 3 * i} turn 0 1" for i in range(4)] + ["150 turn 2 1"] + chord())
    off = R([f"{40 + 3 * i} turn 0 1" for i in range(4)] + chord())
    step = lvl(on, 400, 700) - lvl(off, 400, 700)
    assert abs(step) <= 1.0, f"the chord with the compressor woken from OFF: {step:+.2f} dB"


@pytest.mark.parametrize("name,setup", [
    ("drive AMOUNT 0", cc(84, 127, 30) + cc(81, 0, 31)),
    ("drive MIX 0", cc(84, 64, 30) + cc(81, 80, 31) + cc(83, 0, 32)),
    ("filter fully open", cc(70, 46, 30) + cc(74, 127, 31)),
    ("delay MIX 0", cc(88, 40, 30) + cc(89, 0, 31)),
    ("reverb MIX 0", cc(95, 40, 30) + cc(91, 0, 31)),
])
def test_dry_at_zero(name, setup):
    """'at 0 there is no drive'; DRIVE MIX '0 is clean'; FILTER 'Fully open ... the sound passes untouched, exactly as
    with the filter off'; DELAY / REVERB MIX '0 = none': bit for bit the dry chain"""
    a, b = R(setup + chord()), R(chord())
    assert np.abs(a.L - b.L).max() <= 1 and np.abs(a.R - b.R).max() <= 1, name


@pytest.mark.parametrize("kind,v", [("ANA", 70), ("TAPE", 110), ("DIG", 40)])
def test_echoes_die_at_113(kind, v):
    """DELAY FEEDBK: 'up to about 113 the echoes always die away' (DIG at any FEEDBK)"""
    fb = 127 if kind == "DIG" else 113
    r = hr.run(f"claim-fb-{kind}", CLEAN + cc(88, v, 30) + cc(89, 100, 31) + cc(90, 60, 32) + cc(26, fb, 33)
               + hr.note(300, 60, 110, 100) + hr.note(300, 64, 110, 100), 16000)
    early, late = lvl(r, 1000, 3000), lvl(r, 14000, 16000)
    assert late < early - 20, f"{kind} FDBK {fb}: {early:.1f} -> {late:.1f} dBFS"


def test_limiter_ten_notes():
    """'The limiter at the end keeps even big chords of ten notes from ever clipping' (every effect on)"""
    s = cc(84, 76, 30) + cc(81, 100, 31) + cc(17, 64, 32) + cc(18, 90, 33) + cc(13, 90, 34) + cc(12, 127, 35) + \
        cc(88, 110, 36) + cc(89, 127, 37) + cc(95, 112, 38) + cc(91, 127, 39) + cc(106, 127, 40) + cc(107, 40, 41)
    r = R(s + chord(notes=(24, 31, 36, 43, 48, 55, 60, 67, 72, 79), v=127), 3000)
    assert max(np.abs(r.L).max(), np.abs(r.R).max()) <= 24000
