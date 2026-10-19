# SPDX-License-Identifier: GPL-3.0-only
"""Nothing does nothing (the control audit of 2026-10-17): every user-facing control of Frodhes moves what its name
and the manual say it moves, by at least a justified amount. Added after the VIBES VOICE cards passed the settings
scan (which only looks for damage: clicks, clipping, level jumps, CPU) while barely changing the sound.

One case per control, driven through the panel (btn / turn) or its MIDI CC, measured on the host build's sound,
MIDI out, LEDs and cards, at the control's ends:

  the Rhodes VOICE / DECAY cards on each of the four types, TYPE, KEY VEL, the COMPRESSOR, the FILTER and its LFO,
  TREM/WAH (PAN, TREM, WAH), PHASER, CHORUS, DELAY (DIG / ANA), DRIVE, REVERB, LEVEL, TUNE, TRANSP, OCT, TEMPO (knob,
  tap, clock), every sync, SCALE, CHORD, the SEQUENCER, the ARPEGGIO, all 31 PERFORM assignments, the MIDI page, USB
  VOL, presets (browse, load, save, undo), the sustain (REC and CC 64), FX bypass, the page buttons, the developer CCs.

The minimum each case asserts is about a third to a half of what the control does today (the numbers are in the
docstrings), so a re-voicing passes and a control that stops working fails. Covered by their own tests and left out
here: the VIBES VOICE / DECAY cards, the motor, the VIBES pedal and velocity (test_bars_*). Exact card-vs-sound
accuracy (rates, times, cutoffs, dB) is test_display_sound.py; this file only asks that each control acts."""
import math

import numpy as np
import pytest

from hostrun import FS, cc, env_rms, note, rate_of, rms_db, run

CLEAN = [cc(20, 81, 0), cc(21, 91, 0), cc(21, 70, 0)]   # F01's drive, reverb and filter (on by default since 0.9.3:
                                                        # LP18 at 16K) off: each control alone
PEDAL = [cc(22, 64, 127)]
SHORT = [cc(23, 103, 0), cc(24, 104, 0)]            # RELEASE 0, NOISE 0: a note let go stops at once
# the SELECT order (ui.c PG_ORDER) and the page ids the dump's card.<page>.<k> use (ui.c PG_*)
ORDER = ["PERF", "PIANO", "VOICE", "DECAY", "COMP", "FILTER", "DRIVE", "TREM", "PHA", "CHO", "DELAY", "REVERB", "TEMPO",
         "SCALE", "CHORD", "ARP", "LFO", "SEQ", "ASSIGN", "SETUP", "MIDI", "DISPLAY"]   # (a Rhodes TYPE: no MOTOR page)
PG = {n: i for i, n in enumerate(["PERF", "ASSIGN", "PIANO", "VOICE", "DECAY", "COMP", "FILTER", "TREM", "PHA", "CHO",
                                  "DELAY", "TEMPO", "SCALE", "CHORD", "SEQ", "ARP", "LFO", "SETUP", "MIDI",
                                  "DRIVE", "REVERB", "DISPLAY"])}
BTN = {"OCT-": 0, "OCT+": 1, "FX": 2, "SEL": 3, "ENV": 4, "LFO": 5, "EDIT": 6, "GLO": 7, "HOME": 8, "SAVE": 9,
       "ARP": 10, "SEQ": 11, "PLAY": 12, "REC": 13}
C4 = 261.63
_n = [0]


def R(lines, end):
    _n[0] += 1
    return run(f"ec{_n[0]}", lines, end)


def goto(name, t=40):
    """SELECT from PERFORM (where the FM-1 starts) to the page: one detent per pass"""
    return [f"{t + 3 * i} turn 0 1" for i in range(ORDER.index(name))]


def detents(enc, d, n, t):
    """n single detents (an enum card steps once per pass)"""
    return [f"{t + 3 * i} turn {enc} {d}" for i in range(n)]


def sync_cc(i, hi):
    return -(-i * 128 // (hi + 1))


# ---- measures

def spec(x):
    x = np.asarray(x, float)
    return np.fft.rfftfreq(len(x), 1 / FS), np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2


def band_db(x, lo, hi):
    """the share of the energy between lo and hi, dB"""
    f, X = spec(x)
    return 10 * np.log10(X[(f >= lo) & (f < hi)].sum() / (X.sum() + 1e-9) + 1e-12)


def harm(x, f0, nh=6):
    """each harmonic's level over the fundamental's, dB"""
    f, X = spec(x)
    h = [10 * np.log10(X[(f > f0 * k * 0.97) & (f < f0 * k * 1.03)].max() + 1e-9) for k in range(1, nh + 1)]
    return np.array(h) - h[0]


def bright(x, f0, nh=12):
    """harmonics 3..nh over 1..2, dB"""
    f, X = spec(x)

    def e(a, b):
        return sum(X[(f > f0 * k * 0.97) & (f < f0 * k * 1.03)].sum() for k in range(a, b + 1))
    return 10 * np.log10(e(3, nh) / (e(1, 2) + 1e-9) + 1e-12)


def bands3(x, lo=60, hi=16000):
    f, X = spec(x)
    e = lo * 2 ** (np.arange(0, np.log2(hi / lo) * 3 + 1) / 3)
    return np.array([10 * np.log10(X[(f >= a) & (f < b)].sum() + 1e-3) for a, b in zip(e[:-1], e[1:])])


def tdist(a, b):
    """timbre distance: the 1/3-octave band levels, each normalised to its total, RMS dB difference (bands within
    40 dB of the top)"""
    A, B = bands3(a), bands3(b)
    m = (A > A.max() - 40) | (B > B.max() - 40)
    la, lb = 10 * np.log10((10 ** (A / 10)).sum()), 10 * np.log10((10 ** (B / 10)).sum())
    return float(np.sqrt(np.mean(((A - la) - (B - lb))[m] ** 2)))


def env_db(x, hop=441):
    return 20 * np.log10(env_rms(x, hop) / 32767 + 1e-9)


def decay_db_s(x, t0, t1, hop=441):
    e = env_db(x, hop)
    t = np.arange(len(e)) * hop / FS
    m = (t >= t0) & (t < t1)
    return -float(np.polyfit(t[m], e[m], 1)[0])


def t_down(x, t_from, db, hop=220):
    """seconds after t_from until the level is db under its level there"""
    e = env_db(x, hop)
    i0 = int(t_from * FS / hop)
    idx = np.nonzero(e[i0:] < e[i0] - db)[0]
    return idx[0] * hop / FS if len(idx) else 99.0


def pitch(x, f_guess):
    n = len(x)
    sp = np.abs(np.fft.rfft(x * np.hanning(n), 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / FS)
    m = (fr > f_guess * 2 ** (-1.5 / 12)) & (fr < f_guess * 2 ** (1.5 / 12))
    i = np.nonzero(m)[0][np.argmax(sp[m])]
    a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
    return fr[i] + 0.5 * (a - c) / (a - 2 * b + c) * (fr[1] - fr[0])


def cents(f, f0):
    return 1200 * math.log2(f / f0)


def hf_track(w, a=0.5, b=6.0, lo=1000, hop=441, n=2048):
    """the energy above lo per frame (log): the filter's cutoff over time"""
    x = w.seg(a, b)
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = fr >= lo
    wd = np.hanning(n)
    return np.array([np.log(np.sum(np.abs(np.fft.rfft(x[i:i + n] * wd))[m] ** 2) + 1) for i in range(0, len(x) - n, hop)])


def band_share(x, hop=256, n=1024, lo=300, hi=3000):
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    w = np.hanning(n)
    out = []
    for i in range(0, len(x) - n, hop):
        s = np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2
        out.append(s[m].sum() / (s.sum() + 1))
    return np.array(out), FS / hop


def lr_band(L, R, hop=256, n=1024, lo=300, hi=4000):
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    w = np.hanning(n)
    return np.array([np.log(np.sum(np.abs(np.fft.rfft(L[i:i + n] * w))[m] ** 2) + 1)
                     - np.log(np.sum(np.abs(np.fft.rfft(R[i:i + n] * w))[m] ** 2) + 1)
                     for i in range(0, len(L) - n, hop)]), FS / hop


def gain_db(w, ref, t0, t1, hop=64):
    return 20 * np.log10(env_rms(w.seg(t0, t1), hop) / env_rms(ref.seg(t0, t1), hop)), FS / hop


def p2p(x, lo=2, hi=98):
    return float(np.percentile(x, hi) - np.percentile(x, lo))


def ons(w, t0=0, st=0x90):
    return [(t, a) for t, s, a, b in w.midi_out if (s & 0xF0) == st and t >= t0]


CASES = {}


def case(name):
    def deco(f):
        CASES[name] = f
        return f
    return deco


XFAIL = {}                                           # cases of a known bug, until its fix (strict: they must fail)


# ---- the Rhodes VOICE and DECAY cards, on each of the four types (CC 80: 0 32 64 96)

TYPES = ["ST77", "SUIT", "FELT", "DYNO"]


def rh_note(ty, ccs, n=60, vel=100, dur=1300, end=1700, extra=()):
    return R(CLEAN + [cc(10, 80, ty * 32)] + [cc(30 + i, c, v) for i, (c, v) in enumerate(ccs)] + list(extra)
             + note(200, n, vel, dur), end)


def _voice_cases():
    for ty, tn in enumerate(TYPES):
        def tone(ty=ty):
            """TONE (CC 28): the tine against the pickup. H2 over H1 of a C4 moves 8 (DYNO) .. 73 (FELT) dB from -5.0
            to +5.0; at least 5 dB"""
            h = [harm(rh_note(ty, [(28, v)]).seg(0.25, 1.0), C4)[1] for v in (0, 127)]
            return abs(h[1] - h[0]), 5.0, f"H2/H1 {h[0]:+.1f} -> {h[1]:+.1f} dB"

        def bark(ty=ty):
            """BARK (CC 29): the growl of hard notes. H2 over H1 of a C4 at velocity 127 rises 11 .. 12 dB from 0 to
            10 (H3: 9 .. 20); at least 6"""
            h = [harm(rh_note(ty, [(29, v)], vel=127).seg(0.21, 0.5), C4)[1] for v in (0, 127)]
            return h[1] - h[0], 6.0, f"H2/H1 {h[0]:+.1f} -> {h[1]:+.1f} dB"

        def bell(ty=ty):
            """BELL (CC 30): the bright overtone at the strike. The energy above 3 kHz in the first 100 ms of a C5
            rises 12 .. 24 dB from 0 to 10; at least 6"""
            h = [band_db(rh_note(ty, [(30, v)], n=72).seg(0.2, 0.3), 3000, 16000) for v in (0, 127)]
            return h[1] - h[0], 6.0, f"attack > 3 kHz {h[0]:.1f} -> {h[1]:.1f} dB"

        def hammer(ty=ty):
            """HAMMER (CC 31): softer is darker, harder brighter. Harmonics 3-12 over 1-2 in the first 100 ms of a C4
            at velocity 100 rise 9.5 .. 14 dB; at least 4"""
            b = [bright(rh_note(ty, [(31, v)]).seg(0.21, 0.31), C4) for v in (0, 127)]
            return b[1] - b[0], 4.0, f"brightness {b[0]:.1f} -> {b[1]:.1f} dB"

        def decay(ty=ty):
            """DECAY (CC 102): how long a held note rings. A held C4 decays 33 dB/s at 0 and 1.5 dB/s at 10 (x22);
            at least x4"""
            r = [decay_db_s(rh_note(ty, [(102, v)], dur=6000, end=6500).L, 1.0, 5.5) for v in (0, 127)]
            return r[0] / r[1], 4.0, f"{r[0]:.1f} -> {r[1]:.2f} dB/s"

        def release(ty=ty):
            """RELEASE (CC 103): how fast the damper stops a note. -40 dB after the note-off in 75 ms at 0, 1050 ms
            at 10 (x14); at least x4"""
            t = [t_down(rh_note(ty, [(103, v), (104, 0)], dur=800, end=3000).L, 1.0, 40) for v in (0, 127)]
            return t[1] / t[0], 4.0, f"{t[0] * 1000:.0f} -> {t[1] * 1000:.0f} ms"

        def noise(ty=ty):
            """NOISE (CC 104): the damper's knock and the felt at the note-off. The 100 ms after the note-off are
            14-16 dB louder at 10 than at 0; at least 6"""
            lv = [rms_db(rh_note(ty, [(104, v)], dur=800, end=2000).seg(0.99, 1.1)) for v in (0, 127)]
            return lv[1] - lv[0], 6.0, f"note-off {lv[0]:.1f} -> {lv[1]:.1f} dB"

        for k, f in (("TONE", tone), ("BARK", bark), ("BELL", bell), ("HAMMER", hammer), ("DECAY", decay),
                     ("RELEASE", release), ("NOISE", noise)):
            CASES[f"rhodes-{tn}-{k}"] = f


_voice_cases()


@case("voice-panel")
def _voice_panel():
    """the VOICE page's knobs (EDIT, K1-K4) reach the sound as the CCs do: K3 BELL to 10 brightens the attack
    (a C5's attack above 3 kHz); at least 6"""
    a = R(CLEAN + ["30 turn 0 1", "33 turn 0 1", "40 turn 4 -64", "50 dump"] + note(200, 72, 100, 1300), 1700)
    b = R(CLEAN + ["30 turn 0 1", "33 turn 0 1", "40 turn 4 63", "50 dump"] + note(200, 72, 100, 1300), 1700)
    assert a.state(50)["page"] == "VOICE" and a.state(50)["label2"] == "BELL" and b.state(50)["card2"] == "10.0"
    h = [band_db(w.seg(0.2, 0.3), 3000, 16000) for w in (a, b)]
    return h[1] - h[0], 6.0, f"{h[0]:.1f} -> {h[1]:.1f} dB"


@case("decay-panel-TUNE")
def _tune():
    """TUNE (DECAY K4): +50 on the card moves a key's pitch +50 cents (test_display_sound checks +-1 cent); here at
    least 40"""
    pre = CLEAN + PEDAL + ["30 turn 0 1", "31 turn 0 1", "33 turn 0 1"]   # SELECT: FRODHES, VOICE, DECAY
    f = [pitch(R(pre + [f"40 turn 5 {d}"] + ["100 keys 0x80", "1800 keys 0"], 2000).seg(0.3, 1.5), C4) for d in (-50, 50)]
    return cents(f[1], f[0]), 80.0, f"-50 -> +50: {cents(f[1], f[0]):.1f} cents"


# ---- TYPE

PROBE3 = sum((note(300, n, 100, 1300) for n in (48, 60, 72)), [])


@case("TYPE")
def _type():
    """TYPE (ALGORITHM, from any page): five different instruments. The closest two (STAGE 77 / SUITCASE) are 0.84 dB
    apart in 1/3-octave timbre, VIBES 22+ dB from every Rhodes; each pair at least 0.4, VIBES at least 8"""
    ws = []
    for t in range(5):
        w = R(CLEAN + detents(1, 1, t, 40) + ["100 dump"] + PROBE3, 1800)
        assert w.state(100)["p.0"] == str(t) and w.state(100)["bars"] == str(int(t == 4))
        ws.append(w.seg(0.3, 1.5))
    d = min(tdist(ws[a], ws[b]) for a in range(4) for b in range(a + 1, 4))
    v = min(tdist(ws[4], ws[b]) for b in range(4))
    assert v >= 8.0, f"VIBES only {v:.2f} dB from a Rhodes type"
    return d, 0.4, f"closest pair {d:.2f} dB, VIBES {v:.1f} dB"


@case("TYPE-CC80-79")
def _type_cc():
    """CC 80 bands of 32 pick the Rhodes types, CC 79 any value VIBES"""
    w = R(CLEAN + [cc(30, 80, 70), "40 dump", cc(50, 79, 3), "60 dump", cc(70, 80, 127), "80 dump"], 100)
    got = [w.state(t)["p.0"] for t in (40, 60, 80)]
    assert got == ["2", "4", "3"], got
    return 1.0, 1.0, "ok"


@case("KEY-VEL")
def _kvel():
    """KEY VEL (main page K4): the FM-1's keys strike at it. A key at 1 vs 127: 31 dB apart; at least 15"""
    lv = []
    for d in (-40, 40):
        w = R(CLEAN + goto("PIANO") + [f"60 turn 5 {d}", "200 keys 0x80", "1400 keys 0"], 1700)
        lv.append(rms_db(w.seg(0.21, 0.5)))
    return lv[1] - lv[0], 15.0, f"{lv[0]:.1f} -> {lv[1]:.1f} dB"


# ---- COMPRESSOR

CHORD5 = sum((note(200, n, 120, 1500) for n in (48, 55, 60, 64, 67)), [])


@pytest.fixture(scope="module")
def chord_ref():
    return R(CLEAN + CHORD5, 2000)


def comp(ccs, probe=CHORD5, end=2000):
    return R(CLEAN + [cc(30, 106, 127)] + [cc(31 + i, c, v) for i, (c, v) in enumerate(ccs)] + probe, end)


@case("COMP-THRESH")
def _thr():
    """THRESH (CC 107; COMP on): a loud chord is reduced 0 dB at 0 and 22 dB at -40 (4:1, no makeup); at least 10"""
    ref = R(CLEAN + CHORD5, 2000)
    g = [rms_db(ref.seg(0.25, 1.6)) - rms_db(comp([(107, v), (113, 0)]).seg(0.25, 1.6)) for v in (1, 127)]
    return g[1] - g[0], 10.0, f"gain reduction {g[0]:.1f} -> {g[1]:.1f} dB"


@case("COMP-THRESH-panel")
def _thr_panel():
    """THRESH is the compressor's switch on its page: OFF, turned up it comes on (with MAKEUP +2.8 dB); 4.6 dB net
    reduction at -20; at least 2"""
    ref = R(CLEAN + CHORD5, 2000)
    w = R(CLEAN + goto("COMP") + ["100 turn 2 64", "110 dump"] + CHORD5, 2000)
    st = w.state(110)
    assert st["p.33"] == "1" and st["card0"] == "-20" and st["card3"] == "+2.8", (st["p.33"], st["card0"], st["card3"])
    g = rms_db(ref.seg(0.25, 1.6)) - rms_db(w.seg(0.25, 1.6))
    return g, 2.0, f"{g:.1f} dB"


@case("COMP-THRESH-card")
def _thr_card():
    """the THRESH card shows the threshold rounded to whole dB: at value 1 (-0.3 dB) it reads 0, not -0"""
    w = R(CLEAN + [cc(30, 106, 127), cc(31, 107, 1), "40 dump", cc(50, 107, 2), "60 dump"], 100)
    got = (w.state(40)[f"card.{PG['COMP']}.0"], w.state(60)[f"card.{PG['COMP']}.0"])
    return float(got == ("0", "-1")), 1.0, f"{got}"




@case("COMP-RATIO")
def _ratio():
    """RATIO (CC 112) at -30 dB: 1.5:1 reduces 5 dB, 20:1 14 dB; at least 5 apart"""
    ref = R(CLEAN + CHORD5, 2000)
    g = [rms_db(ref.seg(0.25, 1.6)) - rms_db(comp([(107, 80), (113, 0), (112, v)]).seg(0.25, 1.6)) for v in (0, 127)]
    return g[1] - g[0], 5.0, f"{g[0]:.1f} -> {g[1]:.1f} dB"


@case("COMP-ATTACK")
def _attack():
    """ATTACK (CC 108): 0.1 ms catches the hammer's peak, 50 ms lets it through: the first 15 ms' peak over the body
    9.5 dB at 0, 19.8 dB at 10; at least 5 apart (the code: high = slow)"""
    c = []
    for v in (0, 127):
        w = comp([(107, 80), (113, 0), (108, v)])
        c.append(20 * np.log10(np.abs(w.seg(0.2, 0.215)).max() / 32767) - rms_db(w.seg(0.4, 0.6)))
    return c[1] - c[0], 5.0, f"crest {c[0]:.1f} -> {c[1]:.1f} dB"


@case("COMP-RELEASE")
def _release():
    """RELEASE (CC 109): after a loud chord a soft note comes back at once at 0 (0 dB 200 ms on) and stays held
    down at 127 (-17 dB); at least 6 apart"""
    pre = SHORT + sum((note(200, n, 127, 600) for n in (48, 55, 60, 64, 67)), []) + note(850, 72, 50, 1500)
    r0 = R(CLEAN + SHORT + note(850, 72, 50, 1500), 2500)
    g = []
    for v in (0, 127):
        w = R(CLEAN + [cc(30, 106, 127), cc(31, 107, 90), cc(32, 113, 0), cc(33, 109, v)] + pre, 2500)
        g.append(rms_db(w.seg(1.0, 1.1)) - rms_db(r0.seg(1.0, 1.1)))
    return g[0] - g[1], 6.0, f"gain 150 ms after: {g[0]:.1f} / {g[1]:.1f} dB"


@case("COMP-MAKEUP")
def _makeup():
    """MAKEUP (CC 113): +24 dB on a quiet note below the threshold; at least +20"""
    ref = R(CLEAN + note(200, 60, 40, 1500), 2000)
    w = comp([(107, 1), (113, 127)], probe=note(200, 60, 40, 1500))
    g = rms_db(w.seg(0.25, 1.6)) - rms_db(ref.seg(0.25, 1.6))
    return g, 20.0, f"{g:+.1f} dB"


# ---- FILTER and its LFO

CHORD9 = sum((note(200, n, 90, 1700) for n in (36, 48, 55, 60, 64, 67, 72, 79, 84)), [])


def band_gain(w, ref, fc, a=0.4, b=1.9):
    x, y = w.seg(a, b), ref.seg(a, b)
    f, X = spec(x)
    _, Y = spec(y)
    m = (f > fc / 1.3) & (f < fc * 1.3) & (Y > Y.max() * 1e-6)
    return 10 * np.log10(X[m].sum() / (Y[m].sum() + 1e-9) + 1e-12)


@case("FILTER-TYPE")
def _ftype():
    """FILTER TYPE (K1 on its page, every one of the six): at FREQ 710 Hz the LPs cut 2 kHz (LP12 -4.7, LP18 -8.3 dB),
    the HPs cut 130 Hz (-29, -44), BP cuts both ends (-12 / -4.5), NOTCH the cutoff (-11 at 1 kHz); each at least
    half, the 18s steeper than the 12s"""
    ref = R(CLEAN + PEDAL + CHORD9, 2000)
    g = {}
    for t in range(7):
        w = R(CLEAN + PEDAL + goto("FILTER") + detents(2, 1, t, 100) + [cc(130, 74, 64), "150 dump"] + CHORD9, 2000)
        name = w.state(150)["card0"]
        g[name] = [band_gain(w, ref, f) for f in (130, 1000, 2000)]
    assert list(g) == ["OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH"], list(g)
    ok = (g["LP12"][2] < -2.3 and g["LP18"][2] < g["LP12"][2] - 1.5 and g["HP12"][0] < -14 and
          g["HP18"][0] < g["HP12"][0] - 5 and g["BP"][0] < -6 and g["BP"][2] < -2 and g["NOTCH"][1] < -5 and
          max(abs(x) for x in g["OFF"]) < 0.1)
    return float(ok), 1.0, " ".join(f"{k} {v[0]:.1f}/{v[1]:.1f}/{v[2]:.1f}" for k, v in g.items())


@case("FILTER-FREQ")
def _ffreq():
    """FREQ (CC 74) on LP12: from 16 kHz down to 30 Hz the band 1-3 kHz loses 60+ dB; at least 30"""
    ref = R(CLEAN + PEDAL + CHORD9, 2000)
    g = [band_gain(R(CLEAN + PEDAL + [cc(30, 70, 20), cc(31, 74, v)] + CHORD9, 2000), ref, 2000) for v in (127, 10)]
    return g[0] - g[1], 30.0, f"2 kHz {g[0]:.1f} -> {g[1]:.1f} dB"


@case("FILTER-RESO")
def _freso():
    """RESO (CC 71) on LP12 at 710 Hz: the cutoff band goes from -4.7 to +11.6 dB; at least 8"""
    ref = R(CLEAN + PEDAL + CHORD9, 2000)
    g = [band_gain(R(CLEAN + PEDAL + [cc(30, 70, 20), cc(31, 74, 64), cc(32, 71, v)] + CHORD9, 2000), ref, 1000)
         for v in (0, 127)]
    return g[1] - g[0], 8.0, f"{g[0]:.1f} -> {g[1]:.1f} dB"


LFO_CHORD = sum((note(200, n, 80, 6000) for n in (36, 43, 48, 55, 60, 64, 67, 72, 79, 84)), [])
LFO_PRE = CLEAN + PEDAL + [cc(23, 102, 127), cc(30, 70, 20), cc(31, 74, 60)]


def lfo_run(ccs, extra=()):
    return R(LFO_PRE + [cc(32 + i, c, v) for i, (c, v) in enumerate(ccs)] + list(extra) + ["400 dump"] + LFO_CHORD, 6500)


@pytest.fixture(scope="module")
def lfo_ref():
    return hf_track(lfo_run([(72, 0)]))


@case("FILTER-LFO")
def _flfo():
    """the FILTER page's LFO amount (CC 72): the brightness above 1 kHz swings 0 dB at 0, 57 dB p-p at 10; at least
    20"""
    f0 = hf_track(lfo_run([(72, 0)]))
    s = [p2p((hf_track(lfo_run([(72, v), (76, 40)])) - f0) * 10 / math.log(10)) for v in (0, 127)]
    return s[1] - s[0], 20.0, f"{s[0]:.1f} -> {s[1]:.1f} dB p-p"


def lfo_profile(d, fps, r, nb=16):
    t = np.arange(len(d)) / fps
    ph = ((t * r) % 1.0 * nb).astype(int)
    p = np.array([d[ph == k].mean() for k in range(nb)])
    return (p - p.mean()) / (p.std() + 1e-9)


@case("LFO-SHAPE")
def _lshape():
    """LFO SHAPE (CC 75, all six): each moves the cutoff in its own wave. Folded on its cycle the six profiles differ
    pairwise by 0.5+ (normalised RMS; the closest, SINE / TRI, 0.6); S&H steps differently every cycle"""
    f0 = hf_track(lfo_run([(72, 0)]))
    prof, sd = {}, {}
    for s in range(6):
        w = lfo_run([(72, 100), (75, int(s * 128 / 6) + 2), (76, 20)])
        d = (hf_track(w) - f0) * 10 / math.log(10)
        name = w.state(400)[f"card.{PG['LFO']}.0"]
        prof[name] = lfo_profile(d, FS / 441, 0.825)
        t = np.arange(len(d)) / (FS / 441)
        sd[name] = np.std([d[(t >= a) & (t < a + 1 / 0.825)].mean() for a in np.arange(0, t[-1] - 1.3, 1 / 0.825)])
    assert list(prof) == ["SINE", "TRI", "SAW", "RAMP", "SQR", "S&H"], list(prof)
    names = list(prof)[:5]
    dmin = min(np.sqrt(np.mean((prof[a] - prof[b]) ** 2)) for i, a in enumerate(names) for b in names[i + 1:])
    assert sd["S&H"] > 3 * max(sd[n] for n in names), sd
    return dmin, 0.25, f"closest pair {dmin:.2f}; S&H cycle sd {sd['S&H']:.2f}"


@case("LFO-RATE")
def _lrate():
    """LFO RATE (CC 76): 0.5 Hz at 0, 12 Hz at 127 (the card's accuracy: test_display_sound); at least x8"""
    f0 = hf_track(lfo_run([(72, 0)]))
    r = []
    for v, g in ((10, 0.6), (100, 5.0)):
        d = hf_track(lfo_run([(72, 100), (76, v)])) - f0
        assert p2p(d) * 10 / math.log(10) > 10, f"RATE {v}: the cutoff does not move"
        r.append(rate_of(d, FS / 441, g))
    return r[1] / r[0], 6.0, f"{r[0]:.2f} -> {r[1]:.2f} Hz"


def _lfo_sync(how):
    f0 = hf_track(lfo_run([(72, 0)]))
    extra = ([cc(37, 77, sync_cc(9, 14))] if how == "cc" else goto("LFO", 50) + detents(4, 1, 9, 120))
    w = lfo_run([(72, 100), (76, 40)], extra)
    assert w.state(400)[f"card.{PG['LFO']}.2"] == "1/4"
    assert p2p(hf_track(w) - f0) * 10 / math.log(10) > 10, "the cutoff does not move"
    r = rate_of(hf_track(w) - f0, FS / 441, 2.0, lo=0.3, hi=2.5)
    return -abs(r - 2.0), -0.04, f"SYNC 1/4 at 120 BPM: {r:.3f} Hz (2.000 wanted; free RATE is 1.36)"


CASES["LFO-SYNC-panel"] = lambda: _lfo_sync("panel")
CASES["LFO-SYNC-CC77"] = lambda: _lfo_sync("cc")


@case("LFO-RETRIG")
def _retrig():
    """RETRIG (CC 78): KEY starts the LFO at every note, FREE runs on. The brightness 30-80 ms into nine notes struck
    at odd times: spread 0.7 dB with KEY, 35 dB with FREE; KEY under 3, FREE over 6"""
    pre = CLEAN + [cc(30, 70, 20), cc(31, 74, 50), cc(32, 72, 110), cc(33, 76, 20), cc(36, 75, 0), cc(37, 103, 0),
                   cc(38, 104, 0)]
    times = [300 + int(t) for t in np.cumsum([0, 523, 611, 777, 431, 689, 555, 901, 333])]
    sd = []
    for v in (0, 127):
        w = R(pre + [cc(39, 78, v)] + sum((note(t, 72, 100, 250) for t in times), []), times[-1] + 600)
        sd.append(np.std([band_db(w.seg(t / 1000 + 0.03, t / 1000 + 0.08), 1500, 16000) for t in times]))
    assert sd[1] < 3.0, f"KEY: the strikes' brightness still spreads {sd[1]:.2f} dB"
    return sd[0], 6.0, f"FREE {sd[0]:.1f} dB, KEY {sd[1]:.2f} dB"


# ---- TREM/WAH

TCHORD = sum((note(200 + 2000 * k, m, 100, 1900) for m in (48, 55, 60, 64, 67, 72) for k in range(3)), [])


def trem(ccs, extra=()):
    return R(CLEAN + PEDAL + [cc(30 + i, c, v) for i, (c, v) in enumerate(ccs)] + list(extra) + ["400 dump"] + TCHORD, 6200)


def pan_track(w, hop=441):
    l, r = env_rms(w.seg(0.5, 6.0), hop), env_rms(w.seg(0.5, 6.0, 1), hop)
    return (l - r) / (l + r + 1e-9)


@case("TREM-PAN-DEPTH")
def _pan():
    """EFFECT PAN, DEPTH (CC 18): the image moves (L-R)/(L+R) 0 at 0, 2.0 p-p at 10; at least 1"""
    s = [p2p(pan_track(trem([(17, 64), (21, 10), (18, v), (19, 80)]))) for v in (0, 127)]
    return s[1] - s[0], 1.0, f"{s[0]:.2f} -> {s[1]:.2f}"


@case("TREM-AM-DEPTH")
def _am():
    """EFFECT TREM, DEPTH (CC 18): the gain swings 2.5 dB at 2.5, 60 dB at 10; at depth 10 at least 20"""
    ref = trem([(17, 0)])
    s = [p2p(gain_db(trem([(17, 64), (21, 80), (18, v), (19, 80)]), ref, 0.5, 6.0)[0]) for v in (0, 127)]
    return s[1] - s[0], 20.0, f"{s[0]:.1f} -> {s[1]:.1f} dB p-p"


@case("TREM-RATE")
def _trate():
    """RATE (CC 19): 0.5 Hz at 0, 12 Hz at 127; at least x8"""
    ref = trem([(17, 0)])
    r = []
    for v, g in ((20, 0.8), (120, 10.0)):
        x, fps = gain_db(trem([(17, 64), (21, 80), (18, 100), (19, v)]), ref, 0.5, 6.0)
        assert p2p(x) > 6, f"RATE {v}: no tremolo"
        r.append(rate_of(x, fps, g))
    return r[1] / r[0], 8.0, f"{r[0]:.2f} -> {r[1]:.2f} Hz"


@case("TREM-SHAPE")
def _tshape():
    """SHAPE (CC 22): sine to square. The time the gain spends within 10 % of its extremes: 41 % at 0, 93 % at 10;
    at least 25 points more"""
    ref = trem([(17, 0)])
    e = []
    for v in (0, 127):
        g = 10 ** (gain_db(trem([(17, 64), (21, 80), (18, 127), (19, 60), (22, v)]), ref, 0.5, 6.0)[0] / 20)
        lo, hi = np.percentile(g, 1), np.percentile(g, 99)
        e.append(np.mean((g < lo + 0.1 * (hi - lo)) | (g > hi - 0.1 * (hi - lo))) * 100)
    return e[1] - e[0], 25.0, f"{e[0]:.0f} % -> {e[1]:.0f} %"


@case("TREM-SYNC")
def _tsync():
    """TREM SYNC (TEMPO K2): the tremolo locks to the beat: 1/4 at 120 BPM = 2.000 Hz (+-1 %)"""
    ref = trem([(17, 0)])
    w = trem([(17, 64), (21, 80), (18, 100), (19, 20)], goto("TEMPO", 50) + detents(3, 1, 9, 120))
    assert w.state(400)[f"card.{PG['TEMPO']}.1"] == "1/4"
    x, fps = gain_db(w, ref, 0.5, 6.0)
    assert p2p(x) > 6, "no tremolo"
    r = rate_of(x, fps, 2.0)
    return -abs(r - 2.0), -0.02, f"{r:.3f} Hz"


@case("TREM-WAH")
def _wah():
    """EFFECT WAH: a touch wah, harder playing opens it. A soft and a hard chord's centroid apart: 41 Hz dry, 117 Hz
    with the wah at DEPTH 64; the wah at least 40 Hz more than dry"""
    def spread(extra):
        c = []
        for vel in (30, 127):
            w = R(CLEAN + extra + sum((note(200, n, vel, 1200) for n in (48, 55, 60, 64)), []), 1600)
            f, X = spec(w.seg(0.21, 0.4))
            c.append((f * X).sum() / X.sum())
        return c[1] - c[0]
    d, wv = spread([]), spread([cc(30, 17, 127), cc(32, 18, 64), cc(33, 19, 64)])
    return wv - d, 40.0, f"dry {d:.0f} Hz, wah {wv:.0f} Hz"


@case("TREM-KINDS-panel")
def _tkinds():
    """EFFECT (TREM/WAH K1, the LFO button's page): OFF PAN TREM WAH set the switch and mode; the LFO button switches
    it off and back on"""
    w = R(CLEAN + ["30 btn 5"] + [x for i in range(4) for x in (f"{50 + 20 * i} dump", f"{55 + 20 * i} turn 2 1")]
          + ["150 btn 5", "160 dump", "170 btn 5", "180 dump"], 200)
    got = [(w.state(t)["card0"], w.state(t)["p.2"], w.state(t)["p.6"], w.state(t)["led.5"]) for t in (50, 70, 90, 110, 160, 180)]
    want = [("OFF", "0", "0", "0"), ("PAN", "1", "0", "1"), ("TREM", "1", "1", "1"), ("WAH", "2", "1", "1"),
            ("OFF", "0", "1", "0"), ("WAH", "2", "1", "1")]
    assert got == want, got
    return 1.0, 1.0, "ok"


# ---- PHASER, CHORUS

PCHORD = sum((note(200, n, 100, 6000) for n in (48, 55, 60, 64, 67, 72, 76, 79)), [])
PPRE = CLEAN + PEDAL + [cc(23, 102, 127)]


def pha(ccs, extra=()):
    return R(PPRE + [cc(30 + i, c, v) for i, (c, v) in enumerate(ccs)] + list(extra) + ["400 dump"] + PCHORD, 6500)


@pytest.fixture(scope="module")
def pha_ref():
    return pha([])


def sweep(w, ref):
    f, _ = band_share(w.seg(0.5, 6.2))
    f0, _ = band_share(ref.seg(0.5, 6.2))
    return p2p(10 * np.log10(f / f0))


@case("PHASER-ON")
def _pon():
    """PHASER on (ENV button on its page, CC 12): 9.4 dB of timbre change at DEPTH 8.0, 0 when off; at least 3"""
    ref = pha([])
    w = pha([(13, 100)], ["40 btn 4", "50 btn 4"])
    off = pha([(13, 100)], ["40 btn 4"])
    assert w.state(400)["p.28"] == "1" and w.state(400)["led.4"] == "1" and off.state(400)["p.28"] == "0"
    d = tdist(w.seg(0.5, 6.2), ref.seg(0.5, 6.2))
    assert tdist(off.seg(0.5, 6.2), ref.seg(0.5, 6.2)) < 0.01
    return d, 3.0, f"{d:.1f} dB"


@case("PHASER-DEPTH")
def _pdepth():
    """DEPTH (CC 13): the notches sweep the 300-3000 Hz band's share 1.7 dB p-p at 0 (they stand still), 8.6 at 10;
    at least 3 more"""
    ref = pha([])
    s = [sweep(pha([(85, 127), (13, v), (14, 40), (16, 50)]), ref) for v in (0, 127)]
    return s[1] - s[0], 3.0, f"{s[0]:.1f} -> {s[1]:.1f} dB p-p"


@case("PHASER-SPEED")
def _pspeed():
    """SPEED (CC 14): 0.25 Hz at 0 to 6 Hz at 127; at least x6"""
    r = []
    for v, g in ((20, 0.4), (120, 5.0)):
        f, fps = band_share(pha([(85, 127), (13, 100), (16, 0), (14, v)]).seg(0.5, 6.2))
        assert p2p(10 * np.log10(f)) > 2, f"SPEED {v}: no sweep"
        r.append(rate_of(f, fps, g))
    return r[1] / r[0], 6.0, f"{r[0]:.2f} -> {r[1]:.2f} Hz"


@case("PHASER-FEEDBK")
def _pfb():
    """FEEDBK (CC 16): the resonance. Wet over dry in 1/3 octaves, 93 ms frames, at DEPTH 8.0: the deepest band of
    each frame (median) -17.8 dB at 0, -11.0 dB at 10, the strongest +18.9 / +19.5 (the notches fill in more than
    the peaks grow: see the audit); at least 3 dB of change"""
    ref = pha([])

    def notch(w, win=4096):
        a, b = w.seg(0.5, 6.2), ref.seg(0.5, 6.2)
        f = np.fft.rfftfreq(win, 1 / FS)
        e = 150 * 2 ** (np.arange(0, 16) / 3)
        out = []
        for i in range(0, len(a) - win, win):
            A = np.abs(np.fft.rfft(a[i:i + win] * np.hanning(win))) ** 2
            B = np.abs(np.fft.rfft(b[i:i + win] * np.hanning(win))) ** 2
            out.append(min(10 * np.log10(A[(f >= x) & (f < y)].sum() / (B[(f >= x) & (f < y)].sum() + 1e-9))
                           for x, y in zip(e[:-1], e[1:])))
        return float(np.median(out))
    n = [notch(pha([(85, 127), (13, 80), (14, 40), (16, v)])) for v in (0, 127)]
    return abs(n[1] - n[0]), 3.0, f"deepest band {n[0]:.1f} -> {n[1]:.1f} dB"


@case("PHASER-SYNC")
def _psync():
    """PHA SYNC (TEMPO K3): 1/4 at 120 BPM = 2.000 Hz"""
    w = pha([(85, 127), (13, 100), (16, 0)], goto("TEMPO", 50) + detents(4, 1, 9, 120))
    f, fps = band_share(w.seg(0.5, 6.2))
    assert p2p(10 * np.log10(f)) > 2, "no sweep"
    r = rate_of(f, fps, 2.0)
    return -abs(r - 2.0), -0.02, f"{r:.3f} Hz"


@case("CHORUS-ON-DEPTH")
def _cdepth():
    """CHORUS EFFECT (K1) and DEPTH (CC 86): the left / right band levels swing 0 dB at 0, 10.5 dB p-p at 5.0; at
    least 4; off: nothing"""
    s = []
    for v in (0, 64):
        w = pha([(85, 64), (86, v), (87, 50)])
        f, _ = lr_band(w.seg(0.5, 6.2), w.seg(0.5, 6.2, 1))
        s.append(p2p(f) * 10 / math.log(10))
    off = pha([(85, 0), (86, 64)])
    assert np.array_equal(off.L, off.R)
    return s[1] - s[0], 4.0, f"{s[0]:.1f} -> {s[1]:.1f} dB p-p"


@case("CHORUS-SPEED")
def _cspeed():
    """SPEED (CC 87): 0.96 Hz at 20, 5.0 Hz at 120 (x5.2); at least x4"""
    r = []
    for v, g in ((20, 0.4), (120, 5.0)):
        w = pha([(85, 64), (86, 100), (87, v)])
        f, fps = lr_band(w.seg(0.5, 6.2), w.seg(0.5, 6.2, 1))
        assert p2p(f) * 10 / math.log(10) > 3, f"SPEED {v}: no sweep"
        r.append(rate_of(f, fps, g))
    return r[1] / r[0], 4.0, f"{r[0]:.2f} -> {r[1]:.2f} Hz"


@case("CHORUS-SYNC")
def _csync():
    """CHORUS SYNC (its K4, CC 23): 1/4 at 120 BPM = 2.000 Hz, by the panel and by the CC"""
    out = []
    for extra in (goto("CHO", 50) + detents(5, 1, 9, 120), [cc(50, 23, sync_cc(9, 14))]):
        w = pha([(85, 64), (86, 100)], extra)
        assert w.state(400)[f"card.{PG['CHO']}.3"] == "1/4"
        f, fps = lr_band(w.seg(0.5, 6.2), w.seg(0.5, 6.2, 1))
        assert p2p(f) * 10 / math.log(10) > 3, "no sweep"
        out.append(rate_of(f, fps, 2.0))
    return -max(abs(r - 2.0) for r in out), -0.02, " ".join(f"{r:.3f}" for r in out)


@case("CC85-86-87")
def _reface():
    """CC 85 (reface CHO/PHA: off / chorus / phaser) sets both switches; CC 86 / 87 move the phaser's DEPTH / SPEED
    when only the phaser is on"""
    w = R(CLEAN + [cc(30, 85, 64), "35 dump", cc(40, 85, 127), cc(45, 86, 90), cc(46, 87, 11), "50 dump", cc(60, 85, 0),
                   "70 dump"], 100)
    a, b, c = (w.state(t) for t in (35, 50, 70))
    assert (a["p.8"], a["p.28"]) == ("1", "0") and (b["p.8"], b["p.28"], b["p.29"], b["p.30"]) == ("0", "1", "90", "11")
    assert (c["p.8"], c["p.28"]) == ("0", "0")
    return 1.0, 1.0, "ok"


# ---- DELAY

def dly(ccs, n=72, dur=40, end=4000, extra=()):
    return R(CLEAN + SHORT + [cc(30 + i, c, v) for i, (c, v) in enumerate(ccs)] + list(extra) + ["150 dump"]
             + note(200, n, 110, dur), end)


def echoes(w, d_ms, k=11):
    return [rms_db(w.seg(0.2 + i * d_ms / 1000, 0.2 + i * d_ms / 1000 + 0.06)) for i in range(1, k + 1)
            if 0.26 + i * d_ms / 1000 < len(w.L) / FS]


@case("DELAY-SW-panel")
def _dsw():
    """EFFECT (SEL button on DELAY: on / off; K1 OFF DIG ANA): echoes 175 ms after a short note only while on"""
    on = dly([(89, 100), (90, 64)], extra=["30 btn 3", "40 btn 3"])
    off = dly([(89, 100), (90, 64)], extra=["30 btn 3", "40 btn 3", "50 btn 3"])
    assert on.state(150)["p.13"] == "1" and on.state(150)["led.3"] == "1" and off.state(150)["p.13"] == "0"
    e_on, e_off = echoes(on, 175)[0], echoes(off, 175)[0]
    return e_on - e_off, 30.0, f"first echo {e_off:.0f} -> {e_on:.0f} dB"


@case("DELAY-MIX")
def _dmix():
    """MIX (CC 89): the first echo -35 dB at 2.5, -23.5 dB at 10; at least 6 louder (and none at 0)"""
    e = [echoes(dly([(88, 64), (89, v), (90, 64), (26, 64)]), 175)[0] for v in (0, 32, 127)]
    assert e[0] < -200
    return e[2] - e[1], 6.0, f"{e[1]:.1f} -> {e[2]:.1f} dB"


@case("DELAY-TIME")
def _dtime():
    """TIME (CC 90): 20 ms .. 1.48 s (the card's accuracy: test_display_sound). The echo of a short note at TIME 10
    vs 127: at least x10 later"""
    def first(w, lo, hi):
        e = env_db(w.seg(0.25, 2.0), 44)
        t = np.arange(len(e)) * 44 / FS + 0.25 - 0.2
        m = (t >= lo) & (t < hi)
        return t[m][np.argmax(e[m])]
    a = first(dly([(88, 64), (89, 127), (90, 10), (26, 0)], end=2500), 0.04, 0.2)
    b = first(dly([(88, 64), (89, 127), (90, 127), (26, 0)], end=2500), 0.5, 1.8)
    return b / a, 10.0, f"{a * 1000:.0f} -> {b * 1000:.0f} ms"


@case("DELAY-FEEDBK")
def _dfb():
    """FEEDBK (CC 26) at MIX 7.9: 1 echo above -60 dB at 0, 7 at 5.0; at least 4 more (the runaway at high MIX and
    FEEDBK: see the audit)"""
    n = [sum(x > -60 for x in echoes(dly([(88, 64), (89, 100), (90, 64), (26, v)]), 175)) for v in (0, 64)]
    return n[1] - n[0], 4.0, f"{n[0]} -> {n[1]} echoes"


@case("DELAY-TONE")
def _dtone():
    """DLY TONE (SETUP K1, CC 27): the echoes' energy above 2 kHz -27 dB at 0, -20 dB at 10; at least 4"""
    h = []
    for v in (0, 127):
        w = dly([(88, 64), (89, 100), (90, 64), (26, 64), (27, v)])
        h.append(band_db(np.concatenate([w.seg(0.2 + k * 0.1753, 0.26 + k * 0.1753) for k in (1, 2, 3)]), 2000, 16000))
    return h[1] - h[0], 4.0, f"{h[0]:.1f} -> {h[1]:.1f} dB"


def wobble(x, f0):
    """the pitch's spread (cents, 2..98 %) of a steady tone around f0: the analytic signal's frequency"""
    n = len(x)
    X = np.fft.fft(x)
    f = np.fft.fftfreq(n, 1 / FS)
    a = np.fft.ifft(X * ((f > f0 * 0.8) & (f < f0 * 1.25)) * 2)
    fi = np.convolve(np.diff(np.unwrap(np.angle(a))) * FS / (2 * np.pi), np.ones(441) / 441, "valid")
    return p2p(1200 * np.log2(fi / f0))


@case("DELAY-ANA")
def _dana():
    """EFFECT DIG / ANA: the tape's wow and flutter. A held A5 under its echoes at TIME 593 ms wobbles 0.2 cents p-p
    on DIG, 83 on ANA (too much: see the audit); at least 5 more on ANA"""
    w = []
    for t in (64, 127):
        x = R(CLEAN + PEDAL + [cc(23, 102, 127), cc(30, 88, t), cc(31, 89, 127), cc(32, 90, 100), cc(33, 26, 0)]
              + note(200, 81, 100, 5000), 5500)
        w.append(wobble(x.seg(2.5, 5.0), 880.0))
    return w[1] - w[0], 5.0, f"DIG {w[0]:.1f}, ANA {w[1]:.1f} cents p-p"


@case("DELAY-SYNC")
def _dsync():
    """DLY SYNC (TEMPO K4): 1/8 at 120 BPM = 250 ms between the note and its echo (+-3 %; the note comes 0.5 s after
    the change: the line's time slews 1 sample a sample, and an echo written while it moves comes late)"""
    w = R(CLEAN + SHORT + [cc(30, 88, 64), cc(31, 89, 127), cc(32, 26, 0)] + goto("TEMPO", 40) + detents(5, 1, 6, 100)
          + ["150 dump"] + note(700, 72, 110, 40), 1700)
    assert w.state(150)[f"card.{PG['TEMPO']}.3"] == "1/8" and w.state(150)[f"card.{PG['DELAY']}.2"] == "1/8"
    x = w.seg(0.6, 1.6)                                                 # the onsets' strength, correlated
    o = np.maximum(np.diff(np.convolve(np.abs(np.diff(x)), np.ones(32) / 32, "same")), 0)
    i0 = int(0.1 * FS)
    lags = range(int(0.1 * FS), int(0.5 * FS))
    got = lags[int(np.argmax([np.dot(o[i0 - 200:i0 + 1500], o[i0 - 200 + k:i0 + 1500 + k]) for k in lags]))] / FS
    return -abs(got - 0.25) / 0.25, -0.03, f"{got * 1000:.1f} ms"


# ---- DRIVE, REVERB, LEVEL

@case("DRIVE")
def _drive():
    """DRIVE (main page K2, CC 81): clean to growl. H3 over H1 of a C3 rises 16-24 dB from 0 to 10; at least 8"""
    out = []
    for ty in (0, 3):
        h = []
        for v in (0, 127):
            w = R([cc(20, 81, v), cc(21, 91, 0), cc(22, 80, ty * 32)] + note(200, 48, 100, 1300), 1700)
            h.append(harm(w.seg(0.25, 1.2), 130.81)[2])
        out.append(h[1] - h[0])
    return min(out), 8.0, " ".join(f"{x:.1f}" for x in out)


@case("REVERB")
def _reverb():
    """REVERB (main page K3, CC 91): a tail after the dampers. 0.1 s after the note-off: -96 dB at 0, -44 dB at 10;
    at least 30 louder"""
    lv = [rms_db(R([cc(20, 81, 0), cc(21, 91, v)] + SHORT + note(200, 60, 100, 400), 1200).seg(0.7, 0.8)) for v in (0, 127)]
    return lv[1] - lv[0], 30.0, f"{lv[0]:.0f} -> {lv[1]:.0f} dB"


@case("LEVEL")
def _level():
    """LEVEL (SETUP K2, CC 110): -12 .. +11.8 dB; at least 20 apart"""
    lv = [rms_db(R(CLEAN + [cc(30, 110, v)] + note(200, 60, 40, 1300), 1600).seg(0.25, 1.4)) for v in (0, 127)]
    return lv[1] - lv[0], 20.0, f"{lv[0]:.1f} -> {lv[1]:.1f} dB"


# ---- pitch: TRANSP, OCT

@case("TRANSP")
def _transp():
    """TRANSP (SCALE K4): +7 semitones moves a key +700 cents (+-5)"""
    f0 = pitch(R(CLEAN + PEDAL + ["100 keys 0x80", "1800 keys 0"], 2000).seg(0.3, 1.5), C4)
    w = R(CLEAN + PEDAL + goto("SCALE") + ["90 turn 5 7", "100 keys 0x80", "1800 keys 0"], 2000)
    c = cents(pitch(w.seg(0.3, 1.5), C4 * 2 ** (7 / 12)), f0)
    return -abs(c - 700), -5.0, f"{c:.1f} cents"


@case("OCT")
def _oct():
    """OCT- / OCT+: an octave each (+-2), the LEDs lit while shifted"""
    f0 = pitch(R(CLEAN + PEDAL + ["100 keys 0x80", "1800 keys 0"], 2000).seg(0.3, 1.5), C4)
    w = R(CLEAN + PEDAL + ["30 btn 1", "40 btn 1", "50 btn 1", "60 dump", "100 keys 0x80", "1800 keys 0"], 2000)
    st = w.state(60)
    assert st["oct"] == "2" and st["led.1"] == "1" and st["led.0"] == "0"
    c = cents(pitch(w.seg(0.3, 1.5), C4 * 4), f0)
    return -abs(c - 2400), -5.0, f"{c:.1f} cents"


# ---- TEMPO

def arp_steps(lines, end=4000):
    w = R(CLEAN + SHORT + [cc(40, 52, 1), cc(41, 53, 1)] + lines + ["2000 dump"] + note(1000, 60, 100, end - 1300), end)
    t = [x for x, a in ons(w, 1300)]
    return w, float(np.median(np.diff(t)))


@case("TEMPO-BPM")
def _bpm():
    """BPM (TEMPO K1): the beat. The arpeggiator's 1/8 steps: 250 ms at 120 BPM, 375 ms at 80; within 2 %"""
    w, s = arp_steps(goto("TEMPO", 50) + ["150 turn 2 -40"])
    assert w.state(2000)["bpm"] == "80.0", w.state(2000)["bpm"]
    return -abs(s - 375) / 375, -0.02, f"{s:.0f} ms a step"


@case("TEMPO-TAP")
def _tap():
    """PLAY taps the tempo (from the third tap): four taps 600 ms apart = 100 BPM, the arp's 1/8 = 300 ms"""
    w, s = arp_steps(["100 btn 12", "700 btn 12", "1300 btn 12", "1900 btn 12"], end=5000)
    assert w.state(2000)["bpm"] == "100.0", w.state(2000)["bpm"]
    return -abs(s - 300) / 300, -0.02, f"{s:.0f} ms a step"


@case("TEMPO-CLOCK")
def _clock():
    """MIDI clock (CLOCK EXT, the default) sets the tempo; CLOCK INT (MIDI page K3) ignores it"""
    clock = [f"{300 + i * 25} midi 0xF8 0 0" for i in range(24 * 6)]
    ext = R(CLEAN + clock + ["4000 dump"], 4100).state(4000)["bpm"]
    intl = R(CLEAN + goto("MIDI") + ["150 turn 4 -1"] + clock + ["4000 dump"], 4100).state(4000)["bpm"]
    assert intl == "120.0", intl
    return -abs(float(ext) - 100), -1.0, f"EXT {ext}, INT {intl}"


# ---- SCALE, CHORD

NM = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def key_notes(setup, keys=range(27), page="SCALE"):
    lines = CLEAN + goto(page) + setup
    t = 300
    for k in keys:
        lines += [f"{t} keys {1 << k:#x}", f"{t + 30} keys 0"]
        t += 40
    w = R(lines + ["290 dump"], t + 100)
    return w.state(290), [a for _, a in ons(w, 300)]


@case("SCALE-MODE")
def _scmode():
    """SCALE MODE (K1): OFF plays 27 notes, SNAP C major the 16 of the white keys (the black keys move), DEGREE plays only
    the 16 white keys as the scale's steps"""
    _, off = key_notes([])
    st1, snap = key_notes(["150 turn 2 1"])
    st2, deg = key_notes(["150 turn 2 1", "153 turn 2 1"])
    assert (st1["card0"], st2["card0"]) == ("SNAP", "DEGREE")
    assert all(n % 12 in (0, 2, 4, 5, 7, 9, 11) for n in snap + deg)
    ok = len(set(off)) == 27 and len(set(snap)) == 16 and len(deg) == 16
    return float(ok), 1.0, f"OFF {len(set(off))}, SNAP {len(set(snap))}, DEGREE {len(deg)} notes"


@case("SCALE-SCALE-ROOT")
def _scale():
    """SCALE (K2) and ROOT (K3) change the notes SNAP plays: C MINOR has E flat, D MAJOR F sharp and C sharp"""
    _, mi = key_notes(["150 turn 2 1", "153 turn 3 1"])
    _, d = key_notes(["150 turn 2 1", "153 turn 4 2", "156 turn 4 1"])
    pcs = lambda ns: {n % 12 for n in ns}
    ok = pcs(mi) == {0, 2, 3, 5, 7, 8, 10} and pcs(d) == {2, 4, 6, 7, 9, 11, 1}
    return float(ok), 1.0, f"C MINOR {sorted(pcs(mi))}, D MAJOR {sorted(pcs(d))}"


def chord_notes(setup):
    w = R(CLEAN + ["40 btn 6"] + setup + ["195 dump", "200 keys 0x80", "600 keys 0"], 800)
    return w.state(195), ons(w, 200)


@case("CHORD-TYPE")
def _chtype():
    """CHORD TYPE (K1, all 15): one key plays the chord, each type its own set of notes"""
    sets = []
    for k in range(15):
        st, on = chord_notes(detents(2, 1, k, 100))
        sets.append(tuple(sorted(a - 60 for _, a in on)))
    want = [(0,), (0, 4, 7), (0, 4, 7, 11), (0, 4, 7), (0, 3, 7), (0, 4, 7, 10), (0, 4, 7, 11), (0, 3, 7, 10),
            (0, 2, 7), (0, 5, 7), (0, 4, 7, 14), (0, 3, 6), (0, 4, 8), (0, 4, 7, 9), (0, 3, 7, 9)]
    assert sets == want, sets
    return 1.0, 1.0, "ok"


@case("CHORD-INVERSION")
def _chinv():
    """INVERSION (K2): MIN7 ROOT / 1ST / 2ND / 3RD put the lowest notes an octave up (a triad's 3RD: the root
    position an octave up)"""
    sets = []
    for i in range(4):
        st, on = chord_notes(detents(2, 1, 7, 100) + detents(3, 1, i, 130))
        sets.append([a - 60 for _, a in on])
    assert sets == [[0, 3, 7, 10], [3, 7, 10, 12], [7, 10, 12, 15], [10, 12, 15, 19]], sets
    tri = []                                         # a triad (MIN): 3RD is the root position an octave up (it was
    for i in range(4):                               # the 2ND again: the audit)
        st, on = chord_notes(detents(2, 1, 4, 100) + detents(3, 1, i, 130))
        tri.append([a - 60 for _, a in on])
    assert tri == [[0, 3, 7], [3, 7, 12], [7, 12, 15], [12, 15, 19]], tri
    return 1.0, 1.0, "ok"


@case("CHORD-VOICING")
def _chvoice():
    """VOICING (K3): CLOSE, OPEN (the second note an octave up), +BASS (the root an octave below)"""
    sets = []
    for v in range(3):
        st, on = chord_notes(detents(2, 1, 4, 100) + detents(4, 1, v, 130))
        sets.append([a - 60 for _, a in on])
    assert sets == [[0, 3, 7], [0, 7, 15], [-12, 0, 3, 7]], sets
    return 1.0, 1.0, "ok"


@case("CHORD-STRUM")
def _chstrum():
    """STRUM (K4): 0 to 60 ms between the notes"""
    st, on = chord_notes(detents(2, 1, 4, 100) + detents(5, 1, 12, 130))
    assert st["card3"] == "60 MS"
    return float(on[-1][0] - on[0][0]), 110.0, f"{[t for t, _ in on]}"


# ---- SEQUENCER

SEQ_BASE = CLEAN + ["40 btn 11", cc(50, 61, 1), cc(51, 62, 0), "60 btn 11", "100 btn 12"]
SEQ_LIVE = ["150 keys 0x80", "260 keys 0", "680 keys 0x800", "790 keys 0", "1227 keys 0x4000", "1330 keys 0", "2050 btn 11"]


def seq_phases(w, t0=2100):
    return sorted(set((t - 100) % 2000 for t, _ in ons(w, t0)))


@case("SEQ-REC-PLAY")
def _seqrec():
    """REC (SEQ on SEQUENCER) and PLAY: three notes played live loop every 2 s (1 bar at 120 BPM) where they were
    played (QUANT OFF); PLAY again stops them"""
    w = R(SEQ_BASE + SEQ_LIVE + ["4200 btn 12", "4210 dump"], 6500)
    ph = seq_phases(w)
    assert w.state(4210)["play"] == "0" and not ons(w, 4250)
    ok = len(ph) == 3 and all(abs(a - b) <= 3 for a, b in zip(ph, (50, 580, 1127)))
    return float(ok), 1.0, f"{ph}"


@case("SEQ-QUANT")
def _seqq():
    """QUANT (K2): the notes snap as they play: 1/4 puts them on 0 / 500 / 1000 ms, 1/16 on 0 / 625 / 1125"""
    a = seq_phases(R(SEQ_BASE + SEQ_LIVE + [cc(2060, 62, 1)], 6200))
    b = seq_phases(R(SEQ_BASE + SEQ_LIVE + [cc(2060, 62, 3)], 6200))
    ok = all(abs(x - y) <= 3 for x, y in zip(a, (0, 500, 1000))) and all(abs(x - y) <= 3 for x, y in zip(b, (0, 625, 1125)))
    return float(ok), 1.0, f"1/4 {a}, 1/16 {b}"


@case("SEQ-BARS")
def _seqbars():
    """BARS (K1): 2 bars = a 4 s loop"""
    w = R(SEQ_BASE + SEQ_LIVE + [cc(2060, 61, 2)], 12200)
    d = np.diff([t for t, a in ons(w, 2100) if a == 60])
    return -abs(np.median(d) - 4000), -3.0, f"{d}"


@case("SEQ-EDIT")
def _seqedit():
    """EDIT (K4) left: UNDO the last pass (4 notes -> 3); right: ERASE, held keys' notes go as the loop passes;
    SEQ held 1 s clears"""
    u = R(SEQ_BASE + SEQ_LIVE + ["2100 btn 11", "2200 keys 0x1", "2300 keys 0", "4000 btn 11", "4100 dump",
                                 "4200 turn 5 -1", "4300 dump"], 4400)
    e = R(SEQ_BASE + SEQ_LIVE + ["2100 turn 5 1", "2110 dump", "2200 keys 0x800", "4300 keys 0", "4400 dump"], 4500)
    c = R(SEQ_BASE + SEQ_LIVE + ["3000 press 11", "4200 release 11", "4300 dump"], 4400)
    got = (u.state(4100)["notes"], u.state(4300)["notes"], e.state(2110)["card3"], e.state(4400)["notes"], c.state(4300)["notes"])
    assert got == ("4", "3", "ERASE", "2", "0"), got
    return 1.0, 1.0, "ok"


@case("SEQ-STEP")
def _seqstep():
    """step recording (REC while stopped): notes on the cursor's steps, QUANT 1/16, then played back 125 ms apart"""
    lines = CLEAN + ["40 btn 11", cc(50, 61, 1), cc(51, 62, 3), "60 btn 11"]
    for i, k in enumerate((7, 11, 14, 18)):
        lines += [f"{100 + 50 * i} keys {1 << k:#x}", f"{120 + 50 * i} keys 0"]
    lines += ["400 btn 11", "500 btn 12"]
    w = R(lines, 2400)
    t = [x for x, _ in ons(w, 500)][:4]
    return -float(np.max(np.abs(np.diff(t) - 125))), -3.0, f"{t}"


# ---- ARPEGGIO

def arp_run(setup, hold_ms=2400, end=3000):
    w = R(CLEAN + ["40 btn 10"] + setup + ["195 dump", "200 keys 0x4880", f"{200 + hold_ms} keys 0"], end)
    return w, ons(w, 200), ons(w, 200, 0x80)


@case("ARP-MODE")
def _arpmode():
    """MODE (K1): UP, DOWN, UP-DN, ORDER, RANDOM play C E G in their own orders; OFF plays the chord"""
    seqs = {}
    for m in range(6):
        w, on, _ = arp_run(detents(2, 1, m, 100))
        seqs[w.state(195)["card0"]] = [a - 60 for _, a in on][:8]
    assert seqs["OFF"] == [0, 4, 7] and seqs["UP"] == [0, 4, 7] * 2 + [0, 4] and seqs["DOWN"] == [7, 4, 0] * 2 + [7, 4]
    assert seqs["UP-DN"] == [0, 4, 7, 4, 0, 4, 7, 4] and seqs["ORDER"] == seqs["UP"]
    assert seqs["RANDOM"] not in (seqs["UP"], seqs["DOWN"]) and set(seqs["RANDOM"]) <= {0, 4, 7}
    return 1.0, 1.0, "ok"


@case("ARP-RATE")
def _arprate():
    """RATE (K2): 1/4 = 500 ms, 1/32 = 62.5 ms a step at 120 BPM"""
    s = []
    for r in (0, 5):
        w, on, _ = arp_run([cc(100, 52, 1), cc(101, 53, r)])
        s.append(np.median(np.diff([t for t, _ in on])))
    ok = abs(s[0] - 500) <= 2 and abs(s[1] - 62.5) <= 2
    return float(ok), 1.0, f"{s}"


@case("ARP-OCTAVES")
def _arpoct():
    """OCTAVES (K3): 4 = the pattern up to three octaves higher"""
    w, on, _ = arp_run(["100 turn 2 1"] + detents(4, 1, 3, 110))
    return float(max(a for _, a in on) - 67), 36.0, f"top {max(a for _, a in on)}"


@case("ARP-GATE")
def _arpgate():
    """GATE (K4): a 1/16 note sounds 12 ms at 10 %, 123 ms at 100 %"""
    d = []
    for g in (-1, 1):
        w, on, off = arp_run(["100 turn 2 1"] + detents(5, g, 10, 110))
        d.append(np.median([b[0] - a[0] for a, b in zip(on, off)][1:6]))
    return d[1] / d[0], 5.0, f"{d}"


@case("ARP-HOLD")
def _arphold():
    """HOLD (SEL on ARPEGGIO, 0.9.3): the notes go on after the keys are let go; without it they stop"""
    w, on, _ = arp_run(["100 turn 2 1", "120 btn 3"], hold_ms=600)
    w2, on2, _ = arp_run(["100 turn 2 1"], hold_ms=600)
    assert w.state(195)["hold"] == "1" and not [t for t, _ in on2 if t > 900]
    return float(len([t for t, _ in on if t > 900])), 10.0, f"{len(on)} notes"


# ---- PERFORM: every ASSIGN choice moves its target

PERF_TARGET = ["2 3", "28 29", "8 9", "13 14", "19", "1", "33 34 39", "39", "4", "7", "30", "32", "10", "15", "17",
               "18", "20", "21", "22", "23", "24", "25", "26", "KVEL", "37", "ARPRATE", "ARPGATE", "40 41", "42", "43", "45"]


def _perf_cases():
    for i in range(31):
        def f(i=i):
            """ASSIGN K1 picks what PERFORM K1 plays; turning it on PERFORM moves that parameter (and switches the
            effect on, for an amount)"""
            w = R(CLEAN + goto("ASSIGN") + detents(2, 1, i, 100) + ["200 dump", "210 btn 8", "220 dump", "230 turn 2 -30",
                                                                    "240 dump", "250 turn 2 60", "260 dump"], 300)
            a, b, c, d = (w.state(t) for t in (200, 220, 240, 260))
            assert b["page"] == "PERFORM" and b["label0"] == a["card0"], (b["label0"], a["card0"])
            moved = sorted(k[2:] for k in a if k.startswith("p.") and (b[k] != c[k] or c[k] != d[k]))
            want = PERF_TARGET[i]
            if want in ("KVEL", "ARPRATE", "ARPGATE"):
                ok = not moved and (b["card0"] != c["card0"] or c["card0"] != d["card0"])
            else:
                ok = moved == sorted(want.split())
            return float(ok), 1.0, f"{a['card0']}: {moved}, card {b['card0']} -> {c['card0']} -> {d['card0']}"
        CASES[f"perform-{i:02d}"] = f


_perf_cases()


# ---- SETUP, MIDI, presets, sustain, bypass, buttons

@case("MIDI-CHANNEL")
def _mch():
    """CHANNEL (MIDI K1) 2: channel 1's notes are not heard, channel 2's are; the keys go out on channel 2"""
    w = R(CLEAN + goto("MIDI") + detents(2, 1, 2, 120) + note(300, 60, 100, 400) + note(1000, 64, 100, 400, ch=1)
          + ["1700 keys 0x80", "1900 keys 0"], 2500)
    a, b = rms_db(w.seg(0.35, 0.6)), rms_db(w.seg(1.05, 1.3))
    assert [s for _, s, _, _ in w.midi_out] == [0x91, 0x81]
    return b - a, 100.0, f"ch 1 {a:.0f} dB, ch 2 {b:.0f} dB"


@case("MIDI-TRS-USBVOL")
def _trs():
    """TRS IN (MIDI K2) and USB VOL (SETUP K3, CC 114) switch (the host has neither a TRS jack nor a MASTER knob: the
    cards and the settings only; the FM-1 has to be checked)"""
    w = R(CLEAN + goto("MIDI") + ["120 turn 3 -1", "130 dump", "140 turn 3 1", "150 dump"], 200)
    v = R(CLEAN + goto("SETUP") + ["120 turn 4 1", "130 dump", cc(140, 114, 0), "150 dump"], 200)
    got = (w.state(130)["card1"], w.state(150)["card1"], v.state(130)["card2"], v.state(150)["card2"])
    assert got == ("OFF", "ON", "FIXED", "KNOB"), got
    return 1.0, 1.0, "ok"


@case("PRESET-LOAD-UNDO")
def _preset():
    """PRESETS loads a sound 0.4 s after the last detent (F11 RADIO: drive 115 and TONE: its centroid 465 Hz against
    F01's 273); SAVE held 0.7 s goes back"""
    w = R(CLEAN + [f"{50 + 3 * i} turn 6 1" for i in range(10)] + ["600 dump", "700 press 9", "1500 release 9",
                                                                     "1600 dump"] + note(620, 60, 100, 60), 1700)
    assert w.state(600)["header"] == "F11 RADIO" and w.state(1600)["header"] == "F01 STAGE 77", (w.state(600)["header"], w.state(1600)["header"])
    a = R(note(200, 60, 100, 1000), 1500)
    b = R(["50 turn 6 1"] + [f"{60 + 3 * i} turn 6 1" for i in range(9)] + note(700, 60, 100, 1000), 2000)
    ca = (lambda f, X: (f * X).sum() / X.sum())(*spec(a.seg(0.25, 1.1)))
    cb = (lambda f, X: (f * X).sum() / X.sum())(*spec(b.seg(0.75, 1.6)))
    return cb - ca, 80.0, f"centroid {ca:.0f} -> {cb:.0f} Hz"


@case("PRESET-SAVE")
def _save():
    """SAVE, SAVE stores the sound to U01 (named from it), and it loads back"""
    w = R(CLEAN + [cc(30, 91, 99), "100 btn 9", "200 btn 9", "3000 dump", cc(3010, 91, 0), "3050 turn 6 -1",
                   "3060 turn 6 1", "3600 dump"], 3700)
    a, b = w.state(3000), w.state(3600)
    assert a["header"] == "U01 ST77" and a["u.1"] == "U01 ST77" and b["header"] == "U01 ST77" and b["p.19"] == "99", (a["header"], b["header"], b["p.19"])
    return 1.0, 1.0, "ok"


@case("PRESET-BROWSE-END")
def _browse_end():
    """PRESETS turned on past the last user preset stays there (it does not jump back to F14)"""
    w = R(CLEAN + ["100 btn 9", "200 btn 9", "3000 dump"] + [f"{3100 + 500 * i} turn 6 1" for i in range(2)]
          + ["3560 dump", "4060 dump"], 4200)
    got = [w.state(t)["header"] for t in (3000, 3560, 4060)]
    return float(got == ["U01 ST77"] * 3), 1.0, f"{got}"




@case("SUSTAIN")
def _sustain():
    """REC held, REC tapped (latched; tapped again: off) and CC 64 hold the dampers off: a note let go after 300 ms
    still rings at -31 dB 1 s later, -240 without"""
    base = CLEAN + [cc(23, 103, 40)] + note(200, 60, 100, 300)
    lv = {k: rms_db(R(base + x, 2000).seg(1.0, 1.5)) for k, x in
          (("none", []), ("held", ["100 press 13", "1800 release 13"]), ("tap", ["100 btn 13"]),
           ("tap2", ["100 btn 13", "700 btn 13"]), ("cc64", [cc(100, 64, 127)]))}
    assert lv["tap2"] < -90 and lv["none"] < -90, lv
    return min(lv["held"], lv["tap"], lv["cc64"]) - lv["none"], 100.0, f"{lv}"


@case("SUSTAIN-HALF")
def _half():
    """CC 64 half-pedal: between 42 and 63 the dampers press less (the note 0.4-0.6 s after the note-off -47.9 dB at
    40, -41.8 dB at 56; the range is narrow: see the audit); at least 3 louder"""
    base = CLEAN + [cc(23, 103, 64)] + note(200, 60, 100, 300)
    lv = [rms_db(R(base + [cc(100, 64, v)], 1000).seg(0.6, 0.8)) for v in (40, 56)]
    return lv[1] - lv[0], 3.0, f"{lv[0]:.1f} -> {lv[1]:.1f} dB"


HEAVY = [cc(30, 81, 127), cc(31, 17, 64), cc(32, 18, 127), cc(33, 85, 127), cc(34, 13, 127), cc(35, 88, 64),
         cc(36, 89, 127), cc(37, 91, 127), cc(38, 70, 20), cc(39, 74, 30)]


@case("FX-BYPASS")
def _bypass():
    """FX (all effects off / on, not a setting), CC 111 and the developer's CC 119 (RAW): the dry piano, exactly"""
    probe = note(300, 60, 100, 800)
    dry = R(CLEAN + probe, 2000)
    wet = R(CLEAN + HEAVY + probe, 2000)
    for extra in (["100 btn 2"], [cc(100, 111, 127)]):
        w = R(CLEAN + HEAVY + extra + probe, 2000)
        assert np.abs(w.L - dry.L).max() <= 1, extra
    raw = R(CLEAN + HEAVY + [cc(100, 119, 127)] + probe, 2000)        # (RAW skips the master and the limiter too)
    assert tdist(raw.seg(0.3, 1.0), dry.seg(0.3, 1.0)) < 0.1 and abs(rms_db(raw.seg(0.3, 1.0)) - rms_db(dry.seg(0.3, 1.0))) < 0.3
    d = tdist(wet.seg(0.3, 1.0), dry.seg(0.3, 1.0))
    return d, 10.0, f"the effects: {d:.1f} dB of timbre; bypassed: none"


@case("BUTTONS")
def _buttons():
    """the page buttons and their LEDs: HOME (PERFORM <-> main), EDIT (CHORD), ENV (PHASER), LFO (TREM/WAH), SEL
    (DELAY; on ARPEGGIO: HOLD), SEQ (SEQUENCER, straight), ARP, GLO (TEMPO -> SETUP -> MIDI -> DISPLAY) (0.9.4)"""
    seq = [("HOME", "FRODHES"), ("HOME", "PERFORM"), ("EDIT", "CHORD"), ("ENV", "PHASER"), ("LFO", "TREM/WAH"),
           ("SEL", "DELAY"), ("SEQ", "SEQUENCER"), ("ARP", "ARPEGGIO"), ("SEL", "ARPEGGIO"), ("GLO", "TEMPO"),
           ("GLO", "SETUP"), ("GLO", "MIDI"), ("GLO", "DISPLAY"), ("GLO", "TEMPO")]
    w = R(CLEAN + [f"{100 + 100 * i} btn {BTN[b]}" for i, (b, _) in enumerate(seq)]
          + [f"{150 + 100 * i} dump" for i in range(len(seq))], 100 + 100 * len(seq) + 100)
    got = [w.state(150 + 100 * i)["page"] for i in range(len(seq))]
    assert got == [p for _, p in seq], got
    led = {"FRODHES": 8, "PERFORM": 8, "TEMPO": 7, "SETUP": 7, "MIDI": 7, "DISPLAY": 7}
    for i, (_, p) in enumerate(seq):
        if p in led:
            assert w.state(150 + 100 * i)[f"led.{led[p]}"] == "1", p
    i = seq.index(("SEL", "ARPEGGIO"))                  # SEL on ARPEGGIO: HOLD on, SEL lit
    assert w.state(150 + 100 * i)["hold"] == "1" and w.state(150 + 100 * i)["led.3"] == "1"
    return 1.0, 1.0, "ok"


@case("DEV-CC3")
def _dev_ui():
    """developer CC 3 (the colour test pattern) changes the screen, < 64 gives the page back (CC 2, the design
    study's looks, went with the METERS look: test_ui_meters.py checks it changes nothing)"""
    import os
    from hostrun import OUT
    _n[0] += 1
    tag = f"ec{_n[0]}"
    run(tag, ["2500 shot", cc(2600, 3, 127), "3000 shot", cc(3100, 3, 0), "3600 shot"], 3700)

    def ppm(t):
        d = open(os.path.join(OUT, f"{tag}-{t}.ppm"), "rb").read()
        return np.frombuffer(d[-240 * 240 * 3:], np.uint8).reshape(240, 240, 3).astype(int)
    a, b, c = ppm(2500), ppm(3000), ppm(3600)
    n1 = int((np.abs(b - a).sum(axis=2) > 30).sum())
    a[0:19] = c[0:19] = 0                               # (the header's live BPM / battery)
    n2 = int((np.abs(c - a).sum(axis=2) > 30).sum())
    assert n2 == 0, f"CC 3 < 64: {n2} pixels differ from the page before the pattern"
    return n1, 2000.0, f"pixels changed: CC 3 {n1}, back {n2}"


@case("DEV-CC9-CC116")
def _dev_eng():
    """developer CC 9 caps VIBES' upper modes (1 = none: the attack above 3 kHz drops 50+ dB); CC 116 replies on CC
    115-118 and 112-113"""
    pv = [cc(10, 79, 64), cc(20, 81, 0), cc(21, 91, 0)]
    probe = sum((note(300, n, 110, 1000) for n in (60, 72)), [])
    a = band_db(R(pv + probe, 1500).seg(0.3, 0.5), 3000, 16000)
    b = band_db(R(pv + [cc(30, 9, 1)] + probe, 1500).seg(0.3, 0.5), 3000, 16000)
    w = R([cc(50, 116, 0)], 200)
    assert sorted(c for _, s, c, _ in w.midi_out if s == 0xB0) == [112, 113, 115, 116, 117, 118]
    return a - b, 20.0, f"{a:.1f} -> {b:.1f} dB"


@case("RESERVED-CC24-105")
def _reserved():
    """CC 24 (CHORUS FEEDBACK) and 105 (SPREAD) are reserved, as the manual says: stored, no effect yet"""
    probe = note(300, 60, 100, 800)
    d = R(CLEAN + [cc(30, 85, 64), cc(31, 86, 90)] + probe, 1500)
    for c in (24, 105):
        w = R(CLEAN + [cc(30, 85, 64), cc(31, 86, 90), cc(32, c, 127)] + probe, 1500)
        assert np.array_equal(w.L, d.L), c
    return 1.0, 1.0, "no effect, as documented"


@pytest.mark.parametrize("name", sorted(CASES))
def test_control(name):
    got, need, what = CASES[name]()
    print(f"{name}: {what}")
    ok = got >= need
    if name in XFAIL:
        assert not ok, f"{name} passes now ({what}): remove it from XFAIL"
        pytest.xfail(f"{XFAIL[name]}: {what}")
    assert ok, f"{name}: {what} (needs {need})\n{CASES[name].__doc__ or ''}"
