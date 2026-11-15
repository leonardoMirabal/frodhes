# SPDX-License-Identifier: GPL-3.0-only
"""The full-rate REVERB TYPEs (FR_REV_HQ, a build switch, off by default: firmware/src/rev_hq.c, rev_hq_hall.c,
rev_hq_plate.c, rev_hq_inf.c) on the firmware's own fx_reverb (tests/c/rev_scan.c built with -DFR_REV_HQ=1):

- every test of the TYPE framework (test_rev_types.py) that runs on fx_reverb, on the HQ build: the T60 per DECAY,
  FREEZE / SUSTAIN held for ten minutes and full-scale bursts while held, a TYPE switch without a click and the old
  tail gone after the fade, stale lines read as empty, OFF rings out, the tails end in exact zero, SIZE / MOD / LOW CUT
  / TONE / PRE-DELAY move what they should, MIX does not bend a tail, the levels against HALL;
- the HQ's own: the band above 11 kHz (the half rate's limit) is there; each TYPE's wet within 1.5 dB of the half-rate
  one's (critical-band loudness) and its T60 within 25 % per octave 500 Hz .. 4 kHz (HALL, PLATE); no overflow at the
  worst input (full scale at MIX 127, DECAY 127, TONE bright); CC 10 (fx_reset_state) brings every TYPE to rest (what
  follows renders as from a fresh start); the cost model's rows.
The switch off (the default) builds the half-rate TYPEs byte-identical: Gate R (tools/gate_r.py) and
test_rev_types.py on the default build."""
import inspect
import os
import sys

import numpy as np
import pytest

import test_rev_types as base
from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import rev_scan as rs  # noqa: E402
import rev_hq_ab as ab  # noqa: E402

T = rs.TYPES
BLK = rs.BLK
FS = base.FS
HQ = ["-DFR_REV_HQ=1"]
# the framework's tests on fx_reverb (they take the exe fixture), less the half rate's cost gate (HALL + 200 us: the
# full rate costs more by design; its own rows below)
_SKIP = {"test_cost_per_type", "test_cpu_model_rows_follow_the_firmware", "test_infinite_bright_cost"}
for _n, _f in list(vars(base).items()):
    if _n.startswith("test_") and "exe" in inspect.signature(_f).parameters and _n not in _SKIP:
        globals()[_n] = _f
silent = base.silent
needs_types = base.needs_types


@pytest.fixture(scope="module")
def exe():
    if not base.HAS:
        pytest.skip("no reverb TYPEs in this tree")
    e = rs.build(cflags=HQ, tag="pytest-hq")
    yield e
    os.remove(e)


@pytest.fixture(scope="module")
def old():
    e = rs.build(tag="pytest-hq-old")
    yield e
    os.remove(e)


def _mid(w):
    return np.asarray(w, float)


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_band_above_11k(exe, old, typ):
    """the full rate's point: the tail keeps the band above 11 kHz (the half rate's top), >= 6 dB more than the half
    rate's there (its images and its down / up filters' residue only)"""
    x = rs.burst(rs.FS)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_TONE=100)
    tn = ab.top_band(rs.render(exe, x, p, blocks=200)["wet"], 0.1, 0.5)
    to = ab.top_band(rs.render(old, x, p, blocks=200)["wet"], 0.1, 0.5)
    assert tn >= to + 6, f"{typ}: >11 kHz re 1-4 kHz: HQ {tn:.1f} dB, half rate {to:.1f} dB"


def _inband_db(w):
    """the wet's level in band: K-weighted (BS.1770's two stages), 100 Hz .. 8 kHz, both channels, dB"""
    from scipy.signal import butter, sosfilt, lfilter
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621]
    y = sosfilt(butter(4, [100, 8000], "band", fs=rs.FS, output="sos"), w, axis=0)
    y = lfilter(b2, a2, lfilter(b1, a1, y, axis=0), axis=0)
    return 10 * np.log10((y ** 2).sum() + 1e-9)


def _chord_k(k, amp=8000):
    """rev_scan.chord (5 notes, 5 harmonics, decaying) moved k semitones"""
    t = np.arange(FS) / FS
    x = np.zeros(FS)
    for i, f in enumerate((130.81, 196.0, 261.63, 329.63, 392.0)):
        for h in range(1, 6):
            x += (amp / 5) / h * np.sin(2 * np.pi * f * 2 ** (k / 12) * h * t + i + h) * np.exp(-t * (1.5 + h))
    return np.round(x).astype(np.int64)


@needs_types
@pytest.mark.parametrize("typ", base.NEW)
def test_levels_match_hall(exe, silent, typ):
    """the full-rate build's version of test_rev_types' row: the TYPE's wet within 2 dB of HALL's, each octave band
    250 Hz .. 4 kHz within 6 dB, at MIX 100, DECAY 64, over the first 2 s, on the mean of the chord moved -6 .. +5
    semitones, in band (K-weighted, 100 Hz .. 8 kHz).
    THRESHOLD (0.9.13, A3): one chord and the broadband RMS -> twelve chords' mean in band. One chord samples a few of
    the tank's modes: the TYPE re HALL swings -2.6 .. +2.4 dB over the twelve on the half rate (PLATE), -3.0 .. +1.6 on
    the full rate, and rev_scan.chord itself sat at the full rate's end (PLATE -3.14 broadband, -2.99 in band, its
    354 .. 707 Hz octave -6.6) once HALL came up 1.5 dB to the half rate's level in band (the broadband RMS counts the
    full rate's band above 11 kHz, test_hq_level_matches_half_rate). The mean: PLATE re HALL -0.23 dB (half rate +0.30;
    0.9.12's level +1.27), INFINITE -1.04 (half rate -0.55), every octave within 4.3 dB. HALL 3 dB down reads PLATE
    +2.8: it still fails"""
    sounding = base.sounding
    sounding(silent, typ)
    lv, oc = [], []
    for k in range(-6, 6):
        x = _chord_k(k)
        w = {t: rs.render(exe, x, dict(RV_TYPE=base.T[t], RV_DEPTH=100), blocks=int(2 * FS / BLK))["wet"]
             for t in ("HALL", typ)}
        lv.append(_inband_db(w[typ]) - _inband_db(w["HALL"]))
        oc.append([base.band_db(w[typ], a, b) - base.band_db(w["HALL"], a, b) for a, b in base.OCT])
    assert abs(np.mean(lv)) <= 2.0, (typ, np.mean(lv), lv)
    for (a, b), d in zip(base.OCT, np.mean(oc, 0)):
        assert abs(d) <= 6.0, (typ, a, b, d)


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_level_matches_half_rate(exe, old, typ):
    """the A/B is fair: the wet's level in band (K-weighted, 100 Hz .. 8 kHz) within 1.5 dB of the half-rate TYPE's
    (a noise burst, MIX 100, the first 2 s); HALL and PLATE: the T60 per octave 500 Hz .. 4 kHz within 25 % of the
    half rate's. The band above 11 kHz (the full rate's own, test_hq_band_above_11k) and the Bark loudness, which
    counts it, are printed beside it.
    THRESHOLD (0.9.13): the Bark loudness (critical bands up to 20 kHz) -> K-weighted in band, the same 1.5 dB. The
    Bark match let the HQ HALL sit 1.1 dB under the half rate in band (noise, every MIX / DECAY; 1.4 .. 2 dB on Rhodes
    chords) with +0.35 dB on the Bark measure: its band above 11 kHz, which the half rate does not have, filled the
    difference, and the two-core bypass row read 3.8 LU against one core's 3.1. Now in band: HALL +0.3 .. +0.5,
    INFINITE -0.2 .. +0.4 dB (the Bark measure HALL ~+1.9, INFINITE ~-0.5, printed)"""
    x = rs.burst(rs.FS)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100)
    secs = 25 if typ == "INFINITE" else 8
    wn = rs.render(exe, x, p, blocks=int(secs * rs.FS / BLK))["wet"].astype(float)
    wo = rs.render(old, x, p, blocks=int(secs * rs.FS / BLK))["wet"].astype(float)
    g = _inband_db(wn[:2 * rs.FS]) - _inband_db(wo[:2 * rs.FS])
    gb = -20 * np.log10(ab.match_gain(ab.bark_loud(wo[:2 * rs.FS])[0], ab.bark_loud(wn[:2 * rs.FS])[0]))
    top = ab.top_band(wn, 0.1, 0.5) - ab.top_band(wo, 0.1, 0.5)
    print(f"{typ}: HQ re the half rate: in band {g:+.2f} dB, Bark {gb:+.2f} dB, > 11 kHz re 1-4 kHz {top:+.1f} dB")
    assert abs(g) <= 1.5, f"{typ}: the HQ's wet in band {g:+.2f} dB against the half rate's (Bark {gb:+.2f})"
    if typ != "INFINITE":
        for f in (500, 1000, 2000, 4000):
            a, b = ab.oct_t60(wn, f), ab.oct_t60(wo, f)
            assert a and b and abs(a / b - 1) <= 0.25, f"{typ} {f} Hz: T60 HQ {a} s, half rate {b} s"


@needs_types
def test_hq_hall_tone_darker_in_order(exe, old):
    """HALL's TONE darker than its middle (TONE 64 .. 0, damp 60 .. 150: rev_hq_hall.c rqh_pole): the 1 kHz T60 falls
    at every step 64 / 48 / 32 / 16 / 0, the 500 Hz one within 25 % of the half rate's. The linear pole (damp x 340,
    its 31500 cap at damp 93) made TONE 0 .. 40 one pole (T60 1.79 s at 1 kHz for 0, 16 and 32) and killed the tail's
    fundamental at TONE 0 (vibes_fx REVERB TONE on the FR_C1 host: 0 brighter than 64). Now 1 kHz: 1.01 / 1.29 / 1.79 /
    2.34 / 2.83 s (TONE 0 .. 64; the half rate 0.70 .. 2.81)"""
    x = rs.burst(rs.FS)
    t1 = []
    for tone in (0, 16, 32, 48, 64):
        p = dict(RV_TYPE=T["HALL"], RV_DEPTH=100, RV_TONE=tone)
        wn = rs.render(exe, x, p, blocks=int(8 * rs.FS / BLK))["wet"].astype(float)
        wo = rs.render(old, x, p, blocks=int(8 * rs.FS / BLK))["wet"].astype(float)
        a, b = ab.oct_t60(wn, 500), ab.oct_t60(wo, 500)
        assert a and b and abs(a / b - 1) <= 0.25, f"TONE {tone} 500 Hz: T60 HQ {a} s, half rate {b} s"
        t1.append(ab.oct_t60(wn, 1000))
    assert all(t1) and all(u < v for u, v in zip(t1, t1[1:])), t1


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_worst_input_no_overflow(exe, typ):
    """full-scale square bursts (+-32767 both sides, over the clamp of the send) at MIX 127, DECAY 127, TONE 127, MOD
    127, SIZE 127: the wet stays bounded (the lines clamp; |wet| <= 2^19: the framework's exact return) and never
    wraps (no step of 2^16 or more between samples)"""
    n = BLK * 400
    t = np.arange(n)
    x = np.where((t // 2000) % 2 == 0, 32767, -32767)
    x[n // 2:] = 0
    p = dict(RV_TYPE=T[typ], RV_DEPTH=127, RV_DECAY=127, RV_TONE=127, RV_MOD=127, RV_SIZE=127)
    r = rs.render(exe, x, p)
    w = r["wet"]
    assert np.abs(w).max() <= 1 << 19, f"{typ}: |wet| {np.abs(w).max()}"
    assert np.abs(np.diff(w, axis=0)).max() < 1 << 16, f"{typ}: a step of {np.abs(np.diff(w, axis=0)).max()}"


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_cc10_brings_it_to_rest(exe, typ):
    """CC 10 (fx_reset_state, before block B): from there on the output is exactly a fresh start's on what follows
    (the lines, the diffusers, the one-poles, the framework: nothing of the tail before is left)"""
    B = 150
    a = base.held_chord(BLK * B)
    b = rs.chord(BLK * 200, seed=3)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100)
    r = rs.render(exe, np.concatenate([a, b]), p, blocks=B + 400, reset=B)
    f = rs.render(exe, np.concatenate([np.zeros(BLK, np.int64), b]), p, blocks=401, reset=1)
    assert np.array_equal(r["y"][B * BLK:], f["y"][BLK:]), f"{typ}: the state before CC 10 still sounds"


@needs_types
def test_hq_cost_rows(exe):
    """fx_cost_us while each TYPE runs: the framework's front + the kernel's row (rev_hq.c REV_US_FRONT, REV_US_HQH,
    rev_hq_plate.c / rev_hq_inf.c REV_US_PLATE / REV_US_INF); every TYPE within REV_US_HQ_MAX"""
    src = "".join(open(os.path.join(ROOT, "firmware", "src", f)).read()
                  for f in ("rev_hq.c", "rev_hq_hall.c", "rev_hq_plate.c", "rev_hq_inf.c"))
    import re
    d = {k: int(re.search(rf"#define {k} (\d+)u", src).group(1))
         for k in ("REV_US_FRONT", "REV_US_HQH", "REV_US_PLATE", "REV_US_INF", "REV_US_HQ_MAX")}
    row = dict(HALL=d["REV_US_HQH"], PLATE=d["REV_US_PLATE"], INFINITE=d["REV_US_INF"])
    x = rs.chord(BLK * 40)
    for typ, us in row.items():
        r = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100), blocks=40)
        assert r["cost"] == d["REV_US_FRONT"] + us, (typ, r["cost"])
        assert us <= d["REV_US_HQ_MAX"], typ
