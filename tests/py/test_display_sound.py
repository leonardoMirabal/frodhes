# SPDX-License-Identifier: GPL-3.0-only
"""What the screen says is what the sound does (PLAN P11.1; added 2026-10-17 after the CHORUS / PHASER SPEED cards
showed twice the real rate). Every card with a unit is read through the firmware's own slot_text ("T dump" on the
host build, or tests/c/cards.c for every value) and the quantity is measured from the rendered audio at three
settings, on the Rhodes and on VIBES (TYPE by CC 79 0):

  tremolo rate and depth, MOTOR rate, chorus and phaser speed, delay time (free and synced), the filter's LFO rate,
  the filter's cutoff, TUNE, TRANSP and OCT, BPM by knob / tap / MIDI clock with the tremolo synced, arpeggiator
  RATE, sequencer SWING, compressor THRESH and MAKEUP, LEVEL.

Tolerances: rates and times +-5 % (or half a step of the card's last digit where that is coarser: "0.6" Hz cannot
say better than +-0.05), pitch +-1 cent, levels +-0.5 dB. The measurements themselves are good to ~0.3 %."""
import math
import os
import re
import subprocess

import numpy as np
import pytest

from hostrun import FS, ROOT, card_hz, cc, env_rms, note, once, rate_of, rms_db, run, tap_lag

import sys  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist  # noqa: E402  (the voicing at its defaults, not F01's)

ENGINES = {"rhodes": [], "vibes": [cc(10, 79, 0), cc(11, 47, 0)]}   # VIBES: its motor (on with it) off
CLEAN = dry_dist.type_base_lines(19) + [cc(20, 81, 0), cc(21, 91, 0), cc(23, 106, 0), cc(24, 110, 64)]   # F01's drive, reverb, comp off; 0 dB
PEDAL = [cc(22, 64, 127)]
PG = {"TREM": 7, "PHA": 8, "CHO": 9, "DELAY": 10, "TEMPO": 11, "FILTER": 6, "LFO": 16, "COMP": 5, "SETUP": 17,
      "ARP": 15, "SEQ": 14, "DRIVE": 19, "REVERB": 20, "MOTOR": 21,
      "REVERB2": 22}
UNITS = {"1/32": 12, "16T": 16, "1/16": 24, "8T": 32, "16D": 36, "1/8": 48, "4T": 64, "8D": 72, "1/4": 96, "4D": 144,
         "1/2": 192, "1/1": 384, "2BAR": 768, "4BAR": 1536}
ARP_UNITS = {"1/4": 96, "1/8": 48, "1/8T": 32, "1/16": 24, "1/16T": 16, "1/32": 12}


def restruck(notes, vel=100, t0=200, every=2000, n=5):
    """a chord struck again every 2 s (the notes' decay never leaves the measurement without sound)"""
    return sum((note(t0 + every * k, m, vel, every - 100) for m in notes for k in range(n)), [])


CHORD = (48, 55, 60, 64, 67, 72)


# ---- the card's number and how fine its last digit is

def card_value(text):
    """(value, step) of a card's text in its own unit: "4.7" -> (4.7, 0.1), "12" -> (12, 1), "1.4S" -> (1400 ms,
    100), "4.2K" -> (4200 Hz, 100), "+6.0" -> (6.0, 0.1), "3.5HZ" -> (3.5, 0.1)"""
    t = text.upper().replace("HZ", "")
    scale = 1.0
    if t.endswith("S"):
        t, scale = t[:-1], 1000.0
    elif t.endswith("K"):
        t, scale = t[:-1], 1000.0
    v = float(t)
    step = 10 ** -len(t.split(".")[1]) if "." in t else 1.0
    return v * scale, step * scale


def assert_rate(meas, text, what, rel=0.05):
    shown, step = card_value(text)
    tol = max(rel * shown, step / 2) + 0.004 * shown        # (+0.4 %: the measurement)
    assert abs(meas - shown) <= tol, f"{what}: the card says {text}, the sound does {meas:.4g} (tolerance {tol:.3g})"


def assert_db(meas, text, what, tol=0.5):
    shown, _ = card_value(text)
    assert abs(meas - shown) <= tol, f"{what}: the card says {text} dB, the sound does {meas:+.2f} dB"


def gain_ratio(wet, ref, t0, t1, hop=64):
    """the gain an effect applies over time: the wet run's envelope over the same run without it"""
    a = env_rms(wet.L[int(t0 * FS):int(t1 * FS)], hop)
    b = env_rms(ref.L[int(t0 * FS):int(t1 * FS)], hop)
    return a / b, FS / hop


# ---- every value of every unit card: the text against the table the audio code reads (no audio: exhaustive)

@pytest.fixture(scope="module")
def cards():
    exe = once(os.path.join(ROOT, "build", "host", "cards"), lambda exe: subprocess.run(
        ["cc", "-O1", "-w", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"", "-Dmain=host_main",
         "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
         "-o", exe, os.path.join(ROOT, "tests", "c", "cards.c"), "-lm"], check=True))
    out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    d = {}
    for name, v, text, raw in re.findall(r"^card (\S+) (\d+) (\S+) (-?\d+)$", out, re.M):
        d.setdefault(name, []).append((int(v), text.replace("_", " "), int(raw)))
    return d


DB_PER_LOG2 = 20 * math.log10(2)
TRUE = {                                                # the quantity the audio runs with, from the table value
    "TW_RATE": lambda r: r * FS / 2 ** 32, "LFO_RATE": lambda r: r * FS / 2 ** 32, "MOTOR": lambda r: r * FS / 2 ** 32,
    "CP_SPEED": lambda r: r * FS / 2 ** 32, "PH_SPEED": lambda r: r * FS / 2 ** 32,
    "DL_TIME": lambda r: r * 1000 / FS,                                        # ms
    "FL_FREQ": lambda r: math.atan(r / 2 ** 24) * FS / math.pi,               # g = tan(pi fc / fs)
}
TRUE_DB = {"LEVEL": lambda r: 20 * math.log10(r / 4096), "CMP_GAIN": lambda r: r / 4096 * DB_PER_LOG2,
           "CMP_AMT": lambda r: -r / 4096 * DB_PER_LOG2}


@pytest.mark.parametrize("name", sorted(TRUE) + sorted(TRUE_DB))
def test_every_card_value_is_what_plays(cards, name):
    """all 128 values: rates, times and cutoffs within 5 % (or half the last digit), levels within 0.5 dB"""
    assert len(cards[name]) == 128
    bad = []
    for v, text, raw in cards[name]:
        if name in TRUE:
            true = TRUE[name](raw)
            shown, step = card_value(text)
            if abs(true - shown) > max(0.05 * shown, step / 2) + 1e-9:
                bad.append(f"{v}: '{text}' for {true:.4g}")
        else:
            true = TRUE_DB[name](raw)
            if abs(true - card_value(text)[0]) > 0.505:          # (+0.005: the Q12 tables round the gain)
                bad.append(f"{v}: '{text}' for {true:+.2f} dB")
    assert not bad, f"{name}: {len(bad)} values show another number than plays: {bad[:8]}"


# ---- measured from the sound, three settings each, both engines

@pytest.mark.parametrize("eng", ENGINES)
def test_tremolo_rate_and_depth(eng):
    """TREM (AM): the gain swings at the RATE card's rate, by DEPTH / 10 of full scale (DEPTH 5.0 = a 50 % dip)"""
    pre = ENGINES[eng] + CLEAN + PEDAL
    ref = run(f"ds-trem-ref-{eng}", pre + restruck(CHORD), 10500)
    for v, depth in ((30, 127), (80, 64), (120, 100)):
        w = run(f"ds-trem{v}-{eng}", pre + [cc(30, 17, 64), cc(31, 21, 80), cc(32, 18, depth), cc(33, 19, v), "100 dump"]
                + restruck(CHORD), 10500)
        st = w.state(100)
        assert st[f"card.{PG['TREM']}.0"] == "TREM"
        g, fps = gain_ratio(w, ref, 0.5, 10.2)
        assert_rate(rate_of(g, fps, card_hz(st[f"card.{PG['TREM']}.2"])), st[f"card.{PG['TREM']}.2"], f"TREM RATE {v}")
        dip = np.median([(s.max() - s.min()) / s.max() for s in np.array_split(g, 10)])
        shown = float(st[f"card.{PG['TREM']}.1"]) / 10
        assert abs(dip - shown) <= 0.03, f"TREM DEPTH {depth}: the card says {shown * 10:.1f}, the dip is {dip:.3f}"


def test_motor_rate():
    """VIBES MOTOR (its own page): the tubes' discs modulate the sound at the SPEED card's rate ("3.5HZ"), 0.5-10 Hz"""
    pre = ENGINES["vibes"] + CLEAN + PEDAL
    ref = run("ds-mot-ref", pre + restruck(CHORD), 10500)
    for v in (20, 78, 127):
        w = run(f"ds-mot{v}", pre + [cc(30, 47, 127), cc(32, 48, 127), cc(33, 49, v), "100 dump"]
                + restruck(CHORD), 10500)
        st = w.state(100)
        assert st[f"vcard.2.0"] == "ON" and st[f"card.{PG['TREM']}.0"] == "OFF"
        g, fps = gain_ratio(w, ref, 0.5, 10.2, hop=256)
        txt = st[f"vcard.2.2"]
        assert_rate(rate_of(np.log(g), fps, card_hz(txt)), txt, f"MOTOR RATE {v}")


def lr_band(L, R, hop=256, n=1024, lo=300, hi=4000):
    """left minus right energy (log) in a band, frame by frame: the chorus' two taps move 90 degrees apart"""
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    w = np.hanning(n)
    out = [np.log(np.sum(np.abs(np.fft.rfft(L[i:i + n] * w))[m] ** 2) + 1)
           - np.log(np.sum(np.abs(np.fft.rfft(R[i:i + n] * w))[m] ** 2) + 1) for i in range(0, len(L) - n, hop)]
    return np.array(out), FS / hop


def band_share(x, hop=256, n=1024, lo=300, hi=3000):
    """the share of the energy in a band, frame by frame: the phaser's notches sweep through it"""
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    w = np.hanning(n)
    out = []
    for i in range(0, len(x) - n, hop):
        s = np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2
        out.append(s[m].sum() / (s.sum() + 1))
    return np.array(out), FS / hop


@pytest.mark.parametrize("eng,fx", [("rhodes", "chorus"), ("rhodes", "phaser"), ("vibes", "phaser")])
def test_chorus_phaser_speed(eng, fx):
    """CHORUS / PHASER SPEED: the sweep runs at the card's rate (0.25-6 Hz; the bug of 0.9.2 showed twice that). A
    chord held under the pedal with DECAY / RING at 10 (a steady sound). The chorus is measured on the Rhodes only:
    VIBES' few pure partials make the left / right level difference a mix of the rate and its double (the chorus
    code is the same for both engines; its card text is checked at every value above). The chorus: its left tap's
    delay against the chorus off (hostrun.tap_lag; 0.9.10's Rhodes read twice the rate by the level difference)"""
    chord = (48, 55, 60, 64, 67, 72, 76, 79)
    pre = ENGINES[eng] + CLEAN + PEDAL + [cc(23, 102, 127)]
    if fx == "chorus":
        off = run(f"ds-{fx}off-{eng}", pre + [cc(30, 85, 0), "100 dump"] + sum((note(200, m, 100, 10000) for m in chord), []), 10500)
    for v in (20, 64, 127):
        if fx == "chorus":
            p, card = [cc(30, 85, 64), cc(31, 86, 100), cc(32, 87, v)], f"card.{PG['CHO']}.2"
        else:
            p, card = [cc(30, 85, 127), cc(31, 13, 100), cc(32, 16, 0), cc(33, 14, v)], f"card.{PG['PHA']}.2"
        w = run(f"ds-{fx}{v}-{eng}", ENGINES[eng] + CLEAN + PEDAL + [cc(23, 102, 127)] + p + ["100 dump"]
                + sum((note(200, m, 100, 10000) for m in chord), []), 10500)
        txt = w.state(100)[card]
        L, R = w.seg(0.5, 10.2), w.seg(0.5, 10.2, 1)
        feat, fps = tap_lag(L, off.seg(0.5, 10.2)) if fx == "chorus" else band_share(L)
        assert_rate(rate_of(feat, fps, card_hz(txt)), txt, f"{fx} SPEED {v}")


def onset_strength(x):
    e = np.convolve(np.abs(np.diff(x)), np.ones(32) / 32, "same")
    return np.maximum(np.diff(e), 0)


def echo_spacing(x, d_guess, dry, t0=0.2):
    """the time from the note to its first echo (samples): the dry note's onset strength (dry: the same note with the
    delay off) against the echoes alone (x minus dry at its least-squares gain). 2026-11-08: the render's own onset
    strength against itself read TIME 10 (28 ms) as 31.9 ms on the clean Rhodes (61baedac; 28.1 on 0.9.9): its
    softer strike spreads the attack's onset over ~11 ms, and the template, which held the first echo too, matched
    the attack's later peaks against the echo's. The echoes alone against the dry onset: 28.1 ms on 0.9.9, 568fe9d6
    and 61baedac, and VIBES TIME 10 / 20 at 28.1 / 39.4 (the old way: 29.3 / 44.4)"""
    a = float(np.dot(x, dry) / np.dot(dry, dry))
    od, ow = onset_strength(dry), onset_strength(x - a * dry)
    i0 = int(t0 * FS)
    w = od[i0 - 200:i0 + 1500]
    lags = range(int(0.4 * d_guess), int(2.6 * d_guess))
    c = [np.dot(w, ow[i0 - 200 + l:i0 + 1500 + l]) for l in lags]
    return lags[int(np.argmax(c))]


def test_echo_spacing_finds_a_wrong_time():
    """the detector against a delay at the wrong time: the dry note plus its copy 4 ms late (TIME 10's card 28 ms, the
    echo at 32) reads 32 (out of the 1.5 ms tolerance), at 28 reads 28, and a 175 ms card with the echo at 190 reads 190"""
    dry = run("ds-dly-dry-rhodes", ENGINES["rhodes"] + CLEAN + SHORT + [cc(30, 88, 0)] + note(200, 72, 110, 30), 2500).L
    for card, true in ((28, 32), (28, 28), (175, 190)):
        L = int(round(true * FS / 1000))
        x = 0.8 * dry + 0.5 * np.concatenate([np.zeros(L), dry[:-L]])
        meas = echo_spacing(x, card * FS / 1000, dry) * 1000 / FS
        assert abs(meas - true) < 0.3, (card, true, meas)


@pytest.mark.parametrize("eng", ENGINES)
def test_delay_time(eng):
    """DELAY TIME: the echoes come at the card's time; synced (TEMPO page DLY SYNC, BPM 100 by the knob) at the note
    value, and an echo longer than the line's 1.486 s at half of it (the manual says so)"""
    cases = [(10, None), (64, None), (127, None), (64, 3), (64, 9), (64, 12)]       # 1/16, 1/4, 1/1 (2.4 s -> 1.2)
    dry = run(f"ds-dly-dry-{eng}", ENGINES[eng] + CLEAN + SHORT + [cc(30, 88, 0)] + note(200, 72, 110, 30), 5500).L
    for v, sync in cases:
        p = [cc(30, 88, 48), cc(31, 89, 100), cc(32, 26, 50), cc(33, 90, v)]
        if sync is not None:
            p += [cc(34, 25, -(-sync * 128 // 14)), "40 btn 7", "60 turn 2 -20"]   # TEMPO page, BPM 120 -> 100
        w = run(f"ds-dly{v}-{sync}-{eng}", ENGINES[eng] + CLEAN + SHORT + p + ["150 dump"] + note(200, 72, 110, 30), 5500)
        st = w.state(150)
        txt = st[f"card.{PG['DELAY']}.2"]
        if sync is None:
            meas = echo_spacing(w.L, card_value(txt)[0] * FS / 1000, dry) * 1000 / FS
            assert_rate(meas, txt, f"DELAY TIME {v}")
        else:
            assert st[f"card.{PG['TEMPO']}.3"] == txt and st["bpm"] == "100.0", (st[f"card.{PG['TEMPO']}.3"], txt)
            ms = 60000 / 100 * UNITS[txt] / 96
            while ms * FS / 1000 >= 65534:                  # fx.c: the line is 65536 samples
                ms /= 2
            meas = echo_spacing(w.L, ms * FS / 1000, dry) * 1000 / FS
            assert abs(meas - ms) <= 0.05 * ms, f"DELAY SYNC {txt} at 100 BPM: {ms:.1f} ms expected, the echo at {meas:.1f}"


def band_over(x, ref, lo=1000, hop=512, n=2048):
    """the energy above lo, the filtered run over the same run without the LFO, frame by frame (log)"""
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = fr >= lo
    w = np.hanning(n)
    out = [np.log(np.sum(np.abs(np.fft.rfft(x[i:i + n] * w))[m] ** 2) + 1)
           - np.log(np.sum(np.abs(np.fft.rfft(ref[i:i + n] * w))[m] ** 2) + 1) for i in range(0, len(x) - n, hop)]
    return np.array(out), FS / hop


@pytest.mark.parametrize("eng", ENGINES)
def test_lfo_rate(eng):
    """LFO RATE (the filter's LFO, on LP12): the brightness moves at the card's rate"""
    chord = (36, 43, 48, 55, 60, 64, 67, 72)
    base = ENGINES[eng] + CLEAN + PEDAL + [cc(30, 70, 20), cc(31, 74, 60), cc(33, 75, 0)]
    ref = run(f"ds-lfo-ref-{eng}", base + [cc(32, 72, 0)] + restruck(chord, vel=70), 10500)
    for v in (10, 60, 127):
        w = run(f"ds-lfo{v}-{eng}", base + [cc(32, 72, 100), cc(34, 76, v), "100 dump"] + restruck(chord, vel=70), 10500)
        txt = w.state(100)[f"card.{PG['LFO']}.1"]
        feat, fps = band_over(w.seg(0.5, 10.2), ref.seg(0.5, 10.2))
        assert_rate(rate_of(feat, fps, card_hz(txt)), txt, f"LFO RATE {v}")


@pytest.mark.parametrize("eng", ENGINES)
def test_filter_cutoff(eng):
    """FILTER FREQ: the response (filtered over unfiltered, the same notes) is a 12 dB Butterworth at the card's
    cutoff, low-pass and high-pass (RESO 0 = Q 0.707: -3 dB at the cutoff)"""
    chord = (36, 43, 48, 55, 60, 64, 67, 72)
    pre = ENGINES[eng] + CLEAN + PEDAL
    ref = run(f"ds-flt-ref-{eng}", pre + sum((note(200, m, 70, 1700) for m in chord), []), 2000)
    for kind, tv in (("LP12", 20), ("HP12", 64)):
        for v in (30, 50, 70):
            w = run(f"ds-flt{kind}{v}-{eng}", pre + [cc(30, 70, tv), cc(31, 74, v), cc(32, 71, 0), cc(33, 72, 0), "100 dump"]
                    + sum((note(200, m, 70, 1700) for m in chord), []), 2000)
            st = w.state(100)
            assert st[f"card.{PG['FILTER']}.0"] == kind
            txt = st[f"card.{PG['FILTER']}.1"]
            shown = card_value(txt)[0]
            a, b = w.seg(0.4, 1.9), ref.seg(0.4, 1.9)
            Y, X = np.fft.rfft(a * np.hanning(len(a))), np.fft.rfft(b * np.hanning(len(b)))
            fr = np.fft.rfftfreq(len(a), 1 / FS)
            m = (np.abs(X) > np.abs(X).max() * 1e-3) & (fr > shown / 4) & (fr < shown * 4)
            H = 20 * np.log10(np.abs(Y[m]) / np.abs(X[m]))

            def err(fc):
                r = np.tan(np.pi * fr[m] / FS) / np.tan(np.pi * fc / FS)
                return np.sqrt(np.mean((H + 10 * np.log10(1 + (r if kind == "LP12" else 1 / r) ** 4)) ** 2))

            fcs = shown * np.exp(np.linspace(np.log(0.4), np.log(2.5), 1500))
            es = [err(f) for f in fcs]
            assert min(es) < 1.0 and m.sum() > 10, (kind, v, min(es), m.sum())
            assert_rate(fcs[int(np.argmin(es))], txt, f"FILTER {kind} FREQ {v}")


def pitch(x, f_guess):
    """the strongest peak within 1.5 semitones of the guess (Hz), zero-padded FFT and a parabola"""
    n = len(x)
    sp = np.abs(np.fft.rfft(x * np.hanning(n), 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / FS)
    m = (fr > f_guess * 2 ** (-1.5 / 12)) & (fr < f_guess * 2 ** (1.5 / 12))
    i = np.nonzero(m)[0][np.argmax(sp[m])]
    a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
    return fr[i] + 0.5 * (a - c) / (a - 2 * b + c) * (fr[1] - fr[0])


C4_KEY = ["30 keys 0x80", "1800 keys 0"]            # key 7 = C4


@pytest.mark.parametrize("eng", ENGINES)
def test_tune_transpose_octave(eng):
    """TUNE (DECAY K4) in cents, TRANSP (SCALE K4) in semitones, OCT- / OCT+ (the header, the LEDs): the key's pitch
    moves by exactly that (+-1 cent)"""
    pre = ENGINES[eng] + CLEAN + PEDAL
    f0 = pitch(run(f"ds-pitch-ref-{eng}", pre + C4_KEY, 2000).seg(0.3, 1.5), 261.63)
    for d in (-37, 12, 50):                            # SELECT -> FRODHES, VOICE, DECAY, K4
        w = run(f"ds-tune{d}-{eng}", pre + ["10 turn 0 1", "11 turn 0 1", "12 turn 0 1", f"14 turn 5 {d}", "20 dump"] + C4_KEY, 2000)
        st = w.state(20)
        assert st["page"] == "VOICE" and st["vpage"].startswith("2/") and st["label3"] == "TUNE"
        c = 1200 * math.log2(pitch(w.seg(0.3, 1.5), 261.63 * 2 ** (d / 1200)) / f0)
        assert abs(c - int(st["card3"])) <= 1.0, f"TUNE: the card says {st['card3']} cents, the pitch moved {c:+.2f}"
    for d in (-5, 3, 24):                              # GLO -> TEMPO, SELECT -> SCALE, K4
        w = run(f"ds-transp{d}-{eng}", pre + ["10 btn 7", "12 turn 0 1", f"14 turn 5 {d}", "20 dump"] + C4_KEY, 2000)
        st = w.state(20)
        assert st["page"] == "SCALE" and st["label3"] == "TRANSP"
        c = 1200 * math.log2(pitch(w.seg(0.3, 1.5), 261.63 * 2 ** (d / 12)) / f0)
        assert abs(c - 100 * int(st["card3"])) <= 1.0, f"TRANSP: the card says {st['card3']}, the pitch moved {c:+.1f} c"
    for btns, o in (("0", -1), ("1 1", 2)):            # OCT- once, OCT+ twice
        lines = [f"{10 + 3 * i} btn {b}" for i, b in enumerate(btns.split())]
        w = run(f"ds-oct{o}-{eng}", pre + lines + ["20 dump"] + C4_KEY, 2000)
        st = w.state(20)
        assert int(st["oct"]) == o and st["led.0"] == str(int(o < 0)) and st["led.1"] == str(int(o > 0)), st["oct"]
        c = 1200 * math.log2(pitch(w.seg(0.3, 1.5), 261.63 * 2 ** o) / f0)
        assert abs(c - 1200 * o) <= 1.0, f"OCT {o:+d}: the pitch moved {c:+.1f} c"


def sync_v(i, hi=13):
    return -(-i * 128 // (hi + 1))


@pytest.mark.parametrize("eng", ENGINES)
def test_tempo_and_sync(eng):
    """BPM set by the TEMPO page's knob, by tapping PLAY and by MIDI clock: the BPM card, and the tremolo synced
    (TREM SYNC 1/16, 1/8, 1/4) runs at that BPM's note value; on VIBES the MOTOR's own SYNC (CC 51) too"""
    pre = ENGINES[eng] + CLEAN + PEDAL
    ref = run(f"ds-sync-ref-{eng}", pre + restruck(CHORD, t0=2600, n=4), 10500)
    clock = [f"{300 + i * 25} midi 0xF8 0 0" for i in range(24 * 8)]   # 25 ms a pulse: 100 BPM (smoothed in)
    cases = [("knob", ["40 btn 7", "50 turn 2 -20"], 3), ("tap", ["40 btn 7"] + [f"{60 + 600 * i} btn 12" for i in range(4)], 6),
             ("clock", ["40 btn 7"] + clock, 9)]
    for how, lines, si in cases:
        mode = 120 if eng == "vibes" and how == "clock" else 80            # VIBES: the MOTOR once, synced
        fx = ([cc(30, 47, 127), cc(32, 48, 100), cc(33, 51, sync_v(si))] if mode == 120 else
              [cc(30, 17, 64), cc(31, 21, mode), cc(32, 18, 100), cc(33, 20, sync_v(si))])
        w = run(f"ds-sync-{how}-{eng}", pre + fx + ["2550 dump"] + lines + restruck(CHORD, t0=2600, n=4), 10500)
        st = w.state(2550)
        assert st["page"] == "TEMPO" and st["card0"] == st["bpm"]
        bpm = float(st["card0"])
        assert abs(bpm - 100) <= 2.0, f"{how}: BPM {bpm}"
        div = st[f"vcard.2.2"] if mode == 120 else st["card1"]   # the SPEED / TREM SYNC card: the note
        assert st["vcard.2.2" if mode == 120 else f"card.{PG['TREM']}.2"] == div, (st[f"card.{PG['TREM']}.2"], div)
        hz = bpm / 60 * 96 / UNITS[div]
        g, fps = gain_ratio(w, ref, 2.7, 10.2, hop=256 if mode == 120 else 64)
        meas = rate_of(np.log(g) if mode == 120 else g, fps, hz)
        assert abs(meas - hz) <= 0.01 * hz, f"{how}: BPM {bpm}, {st['card1']} = {hz:.3f} Hz, the tremolo runs {meas:.3f}"


SHORT = [cc(23, 103, 0), cc(24, 104, 0)]            # RELEASE / DAMPER 0, NOISE 0: a note let go is gone at once


def beat_profile(x, beat, bins=240, hop=64):
    """the sound's log level folded on the beat (the loop starts at x's start): the average level at each phase,
    and its rise (a strike's attack) per bin"""
    e = np.log(env_rms(x, hop) + 1)
    ph = (np.arange(len(e)) * hop / FS / beat) % 1.0
    k = np.minimum((ph * bins).astype(int), bins - 1)
    prof = np.bincount(k, e, bins) / np.maximum(np.bincount(k, None, bins), 1)
    return prof, np.diff(np.concatenate([prof[-1:], prof]))


@pytest.mark.parametrize("eng", ENGINES)
def test_arp_rate(eng):
    """ARPEGGIO RATE: at BPM 100 the notes come every note value (1/8 = 300 ms ...): the sound repeats at that rate,
    and MIDI out sends a note each step"""
    for r in (1, 3, 4):
        w = run(f"ds-arp{r}-{eng}", ENGINES[eng] + CLEAN + SHORT + ["40 btn 7", "50 turn 2 -20", cc(60, 52, 1), cc(61, 53, r),
                                                                  cc(62, 55, 50), "70 btn 10", "80 dump"]
                + note(300, 60, 100, 6000), 6500)
        st = w.state(80)
        assert st["page"] == "ARPEGGIO" and st["label1"] == "RATE" and st["bpm"] == "100.0"
        step = 60 / 100 * ARP_UNITS[st["card1"]] / 96
        e = np.log(env_rms(w.seg(0.5, 6.2), 64) + 1)
        meas = 1 / rate_of(e, FS / 64, 1 / step)
        assert abs(meas - step) <= 0.05 * step, f"ARP RATE {st['card1']}: {step * 1000:.0f} ms a note, heard {meas * 1000:.1f}"
        sent = np.diff([t for t, s, a, b in w.midi_out if s == 0x90])
        assert abs(np.median(sent) - step * 1000) <= 2, sent[:8]


@pytest.mark.parametrize("eng", ENGINES)
def test_swing(eng):
    """SEQUENCER SWING: eight notes step-recorded on the 1/8s of one bar (as 1/16s, one step skipped after each: short
    notes), played with QUANT 1/8 at BPM 100: the off-beat notes come at SWING % of the beat (50 % = straight), in
    the sound (its level folded on the beat: the off-beat attack's phase after the on-beat's) and on MIDI out"""
    for s in (50, 62, 70):
        lines = ["10 btn 7", "20 turn 2 -20", "30 btn 11",                          # BPM 100; SEQ = SEQUENCER
                 cc(40, 61, 1), cc(41, 62, 3), cc(42, 63, 127)]                     # BARS 1, QUANT 1/16, step REC
        for i in range(8):
            lines += note(100 + 40 * i, 72, 100, 15) + [f"{100 + 40 * i + 25} turn 1 1"]   # ALGORITHM: skip a 16th
        lines += [cc(500, 63, 0), cc(505, 62, 2), cc(510, 46, s), "560 dump", cc(600, 60, 127)]   # QUANT 1/8, SWING
        w = run(f"ds-swing{s}-{eng}", ENGINES[eng] + CLEAN + SHORT + lines, 5500)
        st = w.state(560)
        assert st["page"] == "SEQUENCER" and st["label2"] == "SWING" and st["notes"] == "8", (st["page"], st["notes"])
        shown = int(st["card2"].rstrip("%")) / 100
        beat, bins = 0.6, 240
        _, rise = beat_profile(w.seg(0.6, 5.4), beat, bins)
        on = int(np.argmax(np.concatenate([rise[-12:], rise[:24]]))) - 12          # the on-beat: within 50 ms of 0
        off = int(bins * 0.3) + int(np.argmax(rise[int(bins * 0.3):int(bins * 0.9)]))
        heard = (off - on) / bins
        assert abs(heard - shown) <= 0.02, f"SWING {st['card2']}: the off-beats heard at {heard * 100:.1f} % of the beat"
        t = np.array([t for t, x, a, b in w.midi_out if x == 0x90 and t >= 600]) - 600
        sent = (t / 600.0) % 1.0
        sent = sent[(sent > 0.3) & (sent < 0.9)]
        assert len(t) >= 12 and abs(np.median(sent) - shown) <= 0.01, (st["card2"], t[:8])


def frames(x, hop):
    n = len(x) // hop
    return np.asarray(x[:n * hop], float).reshape(n, hop)


def body_slope(thr, slope=0.95):
    """(stage 2: the body lift, fx.c cmp_beta) the slope a held note follows: the reduction relaxed by CMP_BETA 90 / 256,
    faded in over THRESH 56 .. 80"""
    beta = 0 if thr <= 56 else 90 if thr >= 80 else 90 * (thr - 56) // 24
    return slope * (1 - beta / 256)


def cmp_auto_db(thr, slope):
    """fx.c cmp_auto: THRESH's own make-up (dB) at this slope (1 - 1 / ratio)"""
    q = np.interp(thr, (56, 64, 80, 96, 112, 127), (0, 266, 993, 2026, 3500, 5017)) if thr > 56 else 0.0
    return q * slope / 4096 * 6.0206


@pytest.mark.parametrize("eng", ENGINES)
def test_compressor_threshold_and_makeup(eng):
    """COMPRESSOR THRESH: a held note decays through the threshold; the gain reduction (20:1, fast attack and
    release; a held note's slope relaxed by the body lift, less THRESH's auto make-up) follows the 6 dB soft knee
    around the card's level (the detector is a peak follower: the input's peak
    per 10 ms); MAKEUP: below the threshold the gain is the card's"""
    pre = ENGINES[eng] + CLEAN + PEDAL
    hi = note(100, 84, 127, 7000)
    ref = run(f"ds-cmp-ref-{eng}", pre + hi, 7500)
    for thr in (64, 96, 120):                          # -20, -30, -38 dB
        w = run(f"ds-cmp{thr}-{eng}", pre + [cc(30, 112, 127), cc(31, 108, 0), cc(32, 109, 0), cc(33, 113, 0),
                                            cc(34, 107, thr), cc(35, 106, 127), "70 dump"] + hi, 7500)
        txt = w.state(70)[f"card.{PG['COMP']}.0"]
        a, b = frames(w.mid(0.15, 7.4), 441), frames(ref.mid(0.15, 7.4), 441)   # (mono sums: the dry width cancels)
        lvl = 20 * np.log10(np.abs(b).max(axis=1) / 32768 + 1e-9)
        gr = 20 * np.log10(np.sqrt((b ** 2).mean(axis=1) + 1e-9) / np.sqrt((a ** 2).mean(axis=1) + 1e-9))

        auto, SL = cmp_auto_db(thr, 0.95), body_slope(thr)

        def err(T):
            o = lvl - T
            sel = (o > -9) & (o < 12)
            model = np.maximum(np.where(o <= -3, 0, np.where(o < 3, SL * (o + 3) ** 2 / 12, SL * o)) - auto, 0)
            # (the auto make-up gives back at most the reduction: fx.c cmp_amx)
            return np.sqrt(np.mean((gr[sel] - model[sel]) ** 2)) if sel.sum() > 20 else 99

        Ts = np.linspace(-45, 0, 901)
        es = [err(T) for T in Ts]
        assert min(es) < 0.3, (thr, min(es))
        assert_db(Ts[int(np.argmin(es))], txt, f"THRESH {thr}", tol=0.8)   # THRESHOLD 0.5 -> 0.8 dB (stage 2: -20.75 at
                                                                            # THRESH 64 with the body lift, -30.35, -37.85)
    ref = run(f"ds-mk-ref-{eng}", pre + note(100, 60, 30, 1500), 1700)
    for mk in (32, 64, 120):
        w = run(f"ds-mk{mk}-{eng}", pre + [cc(33, 113, mk), cc(34, 107, 1), cc(35, 106, 127), "70 dump"]
                + note(100, 60, 30, 1500), 1700)
        txt = w.state(70)[f"card.{PG['COMP']}.3"]
        assert_db(rms_db(w.seg(0.2, 1.5)) - rms_db(ref.seg(0.2, 1.5)), txt, f"MAKEUP {mk}")


@pytest.mark.parametrize("eng", ENGINES)
def test_level(eng):
    """LEVEL (SETUP K2): the output is the engine's own (CC 119 RAW: the effects and the master skipped) moved by the
    card's dB, "+0.0" included (a quiet note: the limiter stays out of it)"""
    pre = ENGINES[eng] + CLEAN + PEDAL
    ref = run(f"ds-lvl-ref-{eng}", pre + [cc(30, 119, 127)] + note(100, 60, 40, 1500), 1700)
    for v in (0, 30, 64, 127):
        w = run(f"ds-lvl{v}-{eng}", pre + [cc(30, 110, v), "50 dump"] + note(100, 60, 40, 1500), 1700)
        txt = w.state(50)[f"card.{PG['SETUP']}.1"]
        assert_db(rms_db(w.mid(0.2, 1.5)) - rms_db(ref.mid(0.2, 1.5)), txt, f"LEVEL {v}")   # (mono sums: RAW has no width)


def test_drive_type_card_is_what_runs():
    """the DRIVE page's K1 names the drive TYPE fx.c runs (fx_p[FXP_DRV_SW], "fxdrv"): every CC 84 value and every
    panel step, on the Rhodes and on VIBES. The sounds themselves: test_every_control DRIVE TYPE, test_drive_types"""
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}",
                                               open(os.path.join(ROOT, "firmware", "src", "params.c")).read()).group(1))
    for eng in ENGINES:
        lines, t = list(ENGINES[eng]), 100
        for v in range(128):
            lines += [cc(t, 84, v), f"{t + 1} dump"]
            t += 2
        # SELECT from PERFORM to DRIVE (ui.c PG_ORDER: PERF PIANO VOICE 1/3 2/3 3/3 COMP EQ DRIVE; VIBES: VOICE 4/4 too)
        lines += [cc(t, 84, 0)] + [f"{t + 10 + 3 * i} turn 0 1" for i in range(7 + (eng == "vibes"))]
        t += 40
        t_panel = t
        for d in (1, 1, 1, -1, -1, -1):
            lines += [f"{t} turn 2 {d}", f"{t + 1} dump"]
            t += 2
        r = run(f"ds-drvtype-{eng}", lines, t + 10)
        seen, bad = set(), []
        for ms, s in sorted(r.dumps.items()):
            txt, val = s[f"card.{PG['DRIVE']}.0"], int(s["fxdrv"])
            seen.add(txt)
            if txt != names[val] or (ms > t_panel and s["card0"] != txt):   # (card0: the DRIVE page on screen)
                bad.append(f"{ms} ms: card {txt} / {s['card0']}, fx runs {val}")
        assert not bad and seen == set(names), (eng, bad[:8], seen)
