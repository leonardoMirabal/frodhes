# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE and REVERB pages (0.9.3) on the host build: every new control has an audible, measured effect, the
defaults are the sound before the pages (sample for sample), and moving any of them does not click.

CCs: DRIVE 81 (AMOUNT), 82 TONE, 83 MIX, 84 ON / OFF; REVERB 91 (MIX), 92 DECAY, 93 TONE, 94 PRE-DELAY."""
import os
import subprocess
import wave

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
CHORD = [(100, n, 110, 900) for n in (48, 55, 60, 64, 67, 72)]
HIT = [(100, 60, 120, 150)]                          # a short note: the damper ends it, the reverb rings on


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def run(tag, ccs, notes, end_ms, extra=()):
    """ccs at 20 ms (before the notes); extra: (ms, cc, value) during the run"""
    out = os.path.join(ROOT, "build", "host", f"drv-{tag}")
    ev = [(20, f"midi 0xB0 {cc} {v}") for cc, v in ccs] + [(t, f"midi 0xB0 {cc} {v}") for t, cc, v in extra]
    for t, n, v, dur in notes:
        ev += [(t, f"midi 0x90 {n} {v}"), (t + dur, f"midi 0x80 {n} 0")]
    ev.sort(key=lambda e: e[0])                      # (a stable sort: the order given within one ms is kept)
    open(out + ".txt", "w").write("".join(f"{t} {e}\n" for t, e in ev) + f"{end_ms} end\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    return x[:, 0], x[:, 1]


def seg(x, t0, t1):
    return x[int(t0 * FS):int(t1 * FS)]


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12)


def band_db(x, f0, f1):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return 10 * np.log10(np.sum(sp[(fr >= f0) & (fr < f1)]) + 1e-12)


def tilt_db(x):
    """the treble around 8 kHz against the body (200 Hz .. 2 kHz), dB"""
    return band_db(x, 6000, 10000) - band_db(x, 200, 2000)


DRY = [(81, 0), (91, 0)]
DRV = [(81, 110), (91, 0)]
REV = [(81, 0), (91, 80)]


# ---- the defaults: the sound before the pages, exactly

def test_defaults_are_the_old_sound():
    """the new controls sent at their defaults change nothing, with the drive and the reverb both on"""
    on = [(81, 90), (91, 90)]
    a, ar = run("def0", on, CHORD, 2500)
    b, br = run("def1", on + [(82, 64), (83, 127), (84, 127), (92, 64), (93, 64), (94, 0)], CHORD, 2500)
    assert np.array_equal(a, b) and np.array_equal(ar, br)


# ---- DRIVE

def test_drive_mix_and_switch():
    dry, _ = run("dry", DRY, CHORD, 1300)
    full, _ = run("full", DRV, CHORD, 1300)
    m0, _ = run("mix0", DRV + [(83, 0)], CHORD, 1300)
    off, _ = run("off", DRV + [(84, 0)], CHORD, 1300)
    half, _ = run("mix64", DRV + [(83, 64)], CHORD, 1300)
    assert np.array_equal(m0, dry)                   # MIX 0 = dry, sample for sample
    assert np.array_equal(off, dry)                  # OFF = dry, whatever the AMOUNT
    a, b, h = (seg(x, 0.15, 0.95) for x in (dry, full, half))
    sig = rms_db(b)
    assert rms_db(b - a) > sig - 20                  # the drive is audible against the dry
    assert rms_db(h - a) > sig - 30 and rms_db(h - b) > sig - 30   # MIX 64 lies audibly between the two
    assert rms_db(h - a) < rms_db(b - a) - 3         # ... nearer the dry than the full drive is


def test_drive_tone_tilts():
    t0, _ = run("tone0", DRV + [(82, 0)], CHORD, 1300)
    t64, _ = run("full", DRV, CHORD, 1300)
    t127, _ = run("tone127", DRV + [(82, 127)], CHORD, 1300)
    d0, d64, d127 = (tilt_db(seg(x, 0.15, 0.95)) for x in (t0, t64, t127))
    assert d0 < d64 < d127, (d0, d64, d127)
    assert d127 - d0 >= 6, (d0, d127)                # dark .. bright: >= 6 dB at 8 kHz
    assert d127 - d64 >= 3 and d64 - d0 >= 3, (d0, d64, d127)   # both halves of the knob do something
    for x in (t0, t127):
        assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 32767


# ---- REVERB

def wet(tag, ccs, notes=HIT, end_ms=5000):
    """the reverb's own sound: the render with it minus the same render without it (the chain before it is the same,
    the return is added to the dry signal)"""
    w, _ = run(tag, ccs, notes, end_ms)
    d, _ = run("rv-dry", [c for c in ccs if c[0] != 91] + [(91, 0)], notes, end_ms)
    return w - d


def t60(x, t0=0.45):
    """T60 from the energy envelope's slope (50 ms frames), fitted over its first 25 dB below t0's level"""
    n = int(0.05 * FS)
    y = x[int(t0 * FS):]
    e = 10 * np.log10(np.array([np.mean(y[i:i + n] ** 2) for i in range(0, len(y) - n, n)]) + 1e-9)
    k = np.argmax(e < e[0] - 25)
    k = k if k > 3 else len(e)
    t = np.arange(k) * 0.05
    slope = np.polyfit(t, e[:k], 1)[0]
    return -60 / slope


def test_reverb_decay_moves_t60():
    lo = t60(wet("dcy0", REV + [(92, 0)]))
    mid = t60(wet("dcy64", REV))
    hi = t60(wet("dcy127", REV + [(92, 127)], end_ms=7000))
    assert lo < mid < hi, (lo, mid, hi)
    assert hi / lo >= 2, (lo, mid, hi)               # >= 2x over its range (designed: x0.5 .. x1.96 around 64)
    assert 0.4 < mid < 6.0, mid                      # the hall as before: a plausible length


def test_reverb_tone_tilts():
    """a bright source (high notes, driven: the plain piano's tail has next to nothing at 8 kHz), the tail's treble
    against its lows (100 .. 600 Hz: TONE 0 also takes some of the body away)"""
    notes = [(100, n, 127, 150) for n in (48, 60, 72, 84, 91, 96)]
    def tilt(v):
        x = seg(wet(f"rtn{v}", [(81, 127), (91, 80), (93, v)], notes, 3000), 0.3, 0.8)
        return band_db(x, 6000, 10000) - band_db(x, 100, 600)
    d0, d64, d127 = tilt(0), tilt(64), tilt(127)
    assert d0 < d64 < d127, (d0, d64, d127)
    assert d127 - d0 >= 6, (d0, d127)                # (measured: ~22 dB; the dark end sits on the 16-bit floor)


def test_reverb_mix0_is_dry():
    w, _ = run("rmix0", [(81, 0), (91, 0), (92, 127), (93, 0), (94, 127)], HIT, 2000)
    d, _ = run("rv-dry2", [(81, 0), (91, 0)], HIT, 2000)
    assert np.array_equal(w, d)                      # MIX 0: no reverb, whatever the other three say


@pytest.mark.parametrize("v", [32, 64, 127])
def test_reverb_predelay_moves_the_onset(v):
    ref = wet("pd0", REV)
    got = wet(f"pd{v}", REV + [(94, v)])
    ms = v * 3528 // 127 / FS * 1000                 # the stated pre-delay (fx.c FX_PDLY_SAMPLES; the card's ms)
    a, b = seg(ref, 0.05, 0.6), seg(got, 0.05, 0.6)
    c = np.correlate(b, a[:len(a) - 4096], "valid")  # lags 0 .. 4096 samples (93 ms)
    lag = int(np.argmax(c)) / FS * 1000
    assert abs(lag - ms) <= 1.0, (v, lag, ms)
    # and the onset itself: nothing of the reverb before the pre-delay has passed
    on_ref = np.argmax(np.abs(ref) > 30) / FS * 1000
    on_got = np.argmax(np.abs(got) > 30) / FS * 1000
    assert abs((on_got - on_ref) - ms) <= 1.0, (v, on_ref, on_got, ms)


# ---- no clicks, no zipper: every new knob swept while a chord rings

def jumps(x, t0, t1):
    """the largest second difference (a click or a step shows as a spike in it)"""
    y = seg(x, t0, t1)
    return float(np.max(np.abs(np.diff(y, 2))))


@pytest.mark.parametrize("name,base,cc", [("dtone", DRV, 82), ("dmix", DRV, 83), ("dsw", DRV, 84),
                                          ("rdecay", REV, 92), ("rtone", REV, 93), ("rpre", REV, 94)])
def test_sweeps_do_not_click(name, base, cc):
    chord = [(100, n, 100, 2600) for n in (48, 55, 60, 64, 67, 72)]
    still_lo, _ = run(f"sw-{name}-lo", base + [(cc, 0)], chord, 2800)
    still_hi, _ = run(f"sw-{name}-hi", base + [(cc, 127)], chord, 2800)
    sweep = [(300 + 8 * i, cc, int(round(127 * (0.5 - 0.5 * np.cos(np.pi * i / 64))))) for i in range(129)]   # up
    sweep += [(1400 + 4 * i, cc, 127 if i % 2 else 0) for i in range(40)] if cc == 84 else \
             [(1400 + 4 * i, cc, 127 - int(127 * i / 63)) for i in range(64)]                                 # down, fast
    L, R = run(f"sw-{name}", base + [(cc, 0)], chord, 2800, sweep)
    ref = max(jumps(still_lo, 0.3, 2.5), jumps(still_hi, 0.3, 2.5))
    got = max(jumps(L, 0.3, 2.5), jumps(R, 0.3, 2.5))
    assert got <= 1.5 * ref + 40, (name, got, ref)
    assert np.all(np.isfinite(L)) and np.max(np.abs(L)) < 32767 and np.max(np.abs(R)) < 32767


@pytest.mark.parametrize("mix,fdbk", [(127, 127), (100, 127), (127, 100)])
def test_delay_echoes_always_die(mix, fdbk):
    """the delay's loop gain stays under 1 at any MIX x FEEDBK (the audit, 2026-10-17: over MIX x FDBK ~ 11650 one short
    note's echoes grew to the limiter: -14.7, -7.3, -4.8, -3.6 dBFS at 1, 3, 6, 11 s). Reverb off, one short note."""
    L, R = run(f"dly{mix}-{fdbk}", [(91, 0), (88, 64), (89, mix), (26, fdbk)], HIT, 12000)
    x = np.maximum(np.abs(L), np.abs(R))
    early, late = rms_db(seg(x, 1.0, 2.0)), rms_db(seg(x, 10.5, 11.5))
    assert late < early - 20, (early, late)


@pytest.mark.parametrize("fx", [[(12, 127), (13, 80)], [(85, 64), (86, 127)]], ids=["phaser", "chorus"])
@pytest.mark.parametrize("eng", ["rhodes", "vibes"])
def test_delay_keeps_its_echoes_under_stereo_effects(fx, eng):
    """the delay's send is the mono sum (L + R) / 2: under the phaser or chorus (L and R differ) the echoes came out
    1-2.5 dB weaker in stereo power (3-6 dB on one side; the user: "when chorus or phaser enabled I lost the delay", 2026-10-18); the send keeps the stereo
    power now: the first echo within 1 dB of what it is with the delay alone, re its direct sound"""
    sel = [(79, 64), (47, 0)] if eng == "vibes" else []
    base = [(81, 0), (91, 0), (88, 64), (89, 90), (90, 85), (26, 40)]
    hit = [(300, 72, 110, 120)]

    def rel(ccs):
        L, R = run(f"dlst-{eng}-{len(ccs)}", base + sel + ccs, hit, 2500)
        p = lambda a, b: 10 * np.log10(np.mean(seg(L, a, b) ** 2) + np.mean(seg(R, a, b) ** 2) + 1e-9)
        return p(0.66, 0.76) - p(0.30, 0.40)
    assert abs(rel(fx) - rel([])) <= 1.0, (rel(fx), rel([]))


@pytest.mark.parametrize("eng", ["rhodes", "vibes"])
def test_drive_is_heard(eng):
    """DRIVE distorts a single note audibly at the top (TODO #4: "drive effect is bare noticiable ... does nothing"):
    A3 v110 at AMOUNT 127 vs 0 raises the 1-3 kHz band re the lows (Rhodes 15 dB, VIBES 5) or more and drops the crest factor; at 64
    the level stays within 2 LU (it changes the tone, not the volume)"""
    sel = [(79, 64), (47, 0)] if eng == "vibes" else []
    out = {}
    for d in (0, 64, 127):
        L, R = run(f"drh-{eng}-{d}", [(91, 0), (84, 127), (81, d)] + sel, [(300, 57, 110, 2000)], 2500)
        x = seg((L + R) / 2, 0.5, 1.5)
        X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / FS)
        hi = 10 * np.log10(X[(f >= 1000) & (f < 3000)].sum() + 1e-9) - 10 * np.log10(X[f < 300].sum() + 1e-9)
        out[d] = (hi, 20 * np.log10(np.abs(x).max() / np.sqrt(np.mean(x * x))), rms_db(x))
    assert out[127][0] - out[0][0] >= (15 if eng == "rhodes" else 5), out   # (VIBES starts brighter: +6 dB, the Rhodes +43)
    assert out[127][1] < out[0][1] - 2, out
    assert abs(out[64][2] - out[0][2]) <= 2.0, out
