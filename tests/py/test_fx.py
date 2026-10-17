# SPDX-License-Identifier: GPL-3.0-only
"""Effects chain sanity on the host build (IMPLEMENTATION-v3 §9): every effect is driven by its reface CP MIDI CC,
stays finite and below full scale, and does what it is for (echo at the delay time, a reverb tail that ends in
exact silence, the tremolo moving the image between left and right, ...)."""
import os
import subprocess
import wave

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def run(tag, ccs, notes, end_ms):
    out = os.path.join(ROOT, "build", "host", f"fx-{tag}")
    lines = [f"20 midi 0xB0 {cc} {v}" for cc, v in ccs]
    for t, n, v, dur in notes:
        lines += [f"{t} midi 0x90 {n} {v}", f"{t + dur} midi 0x80 {n} 0"]
    lines.sort(key=lambda l: int(l.split()[0]))         # the host runs the script in time order (chords: all on first)
    lines.append(f"{end_ms} end")
    open(out + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    return x[:, 0], x[:, 1]


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12)


def seg(x, t0, t1):
    return x[int(t0 * FS):int(t1 * FS)]


def test_dry_chain_is_clean():
    L, R = run("dry", [(81, 0), (91, 0)], [(100, 60, 100, 400)], 3000)
    assert np.max(np.abs(L)) < 32000 and np.array_equal(L, R)
    assert rms_db(seg(L, 2.5, 3.0)) < -90          # the damped note ends


def test_delay_echoes():
    # TIME 64 -> 20 ms * 74^(64/127) ~ 175 ms; a short note, then echoes after it
    L, R = run("dly", [(88, 64), (89, 100), (90, 64)], [(100, 72, 110, 60)], 2500)
    assert rms_db(seg(L, 0.6, 1.2)) > -60          # echoes ring after the damped note
    L0, _ = run("dly0", [(81, 0), (91, 0)], [(100, 72, 110, 60)], 2500)
    assert rms_db(seg(L0, 0.6, 1.2)) < rms_db(seg(L, 0.6, 1.2)) - 20


def test_reverb_tail_and_silence():
    L, R = run("rev", [(91, 100)], [(100, 60, 110, 200)], 9000)
    assert rms_db(seg(L, 0.5, 1.0)) > -60          # a tail after the damper
    assert not np.array_equal(L, R)                # stereo
    assert np.max(np.abs(seg(L, 8.5, 9.0))) <= 2   # and it ends (near) silent


def test_tremolo_autopan_moves():
    L, R = run("trem", [(17, 64), (18, 127), (19, 80)], [(100, 64, 100, 2500)], 2700)
    l = np.sqrt(np.convolve(seg(L, 0.3, 2.3) ** 2, np.ones(2205) / 2205, "valid"))
    r = np.sqrt(np.convolve(seg(R, 0.3, 2.3) ** 2, np.ones(2205) / 2205, "valid"))
    pan = (l - r) / (l + r + 1e-9)                   # the image position, independent of the note's decay
    assert np.std(pan) > 0.3 and np.min(pan) < -0.3 < 0.3 < np.max(pan)


@pytest.mark.parametrize("name,ccs", [("chorus", [(85, 64), (86, 100), (87, 40)]),
                                      ("phaser", [(85, 127), (86, 120), (87, 60)]),
                                      ("drive", [(81, 110)]),
                                      ("wah", [(17, 127), (18, 100), (19, 80)]),
                                      ("tape", [(88, 127), (89, 90), (90, 70)]),
                                      ("all", [(81, 60), (17, 64), (18, 80), (85, 127), (86, 80), (88, 64),
                                               (89, 70), (91, 80)])])
def test_effect_finite_and_bounded(name, ccs):
    L, R = run(name, ccs, [(100, 48, 127, 800), (150, 64, 127, 800), (200, 71, 127, 800)], 2500)
    assert np.all(np.isfinite(L)) and np.max(np.abs(L)) < 32767 and np.max(np.abs(R)) < 32767
    assert rms_db(seg(L, 0.2, 0.8)) > -50


def test_noise_knock_at_key_up():
    """NOISE: the damper's knock and felt noise follow the key-up, and only then."""
    base = [(81, 0), (91, 0), (119, 127)]
    Lq, _ = run("noise0", base + [(104, 0)], [(100, 64, 100, 500)], 1200)
    Ln, _ = run("noise1", base + [(104, 127)], [(100, 64, 100, 500)], 1200)
    pre = slice(int(0.3 * FS), int(0.55 * FS))
    assert np.array_equal(Lq[pre], Ln[pre])                     # identical before the key-up
    post = slice(int(0.6 * FS), int(0.7 * FS))
    assert np.sum((Ln[post] - Lq[post]) ** 2) > 0               # something added after it


def test_compressor_narrows_dynamics():
    # soft (v 40) and hard (v 120) A3, dry vs COMP on at AMOUNT 127: the level gap must shrink by >= 4 dB
    notes = [(100, 57, 40, 700), (1000, 57, 120, 700)]
    base = [(81, 0), (91, 0)]
    def gap(ccs):
        L, _ = run("cmp" + str(len(ccs)), base + ccs, notes, 1900)
        return rms_db(seg(L, 1.02, 1.4)) - rms_db(seg(L, 0.12, 0.5))
    g_off, g_on = gap([]), gap([(106, 127), (107, 127), (108, 0), (109, 40)])
    assert g_off > 10 and g_on < g_off - 4, (g_off, g_on)


def test_phaser_and_chorus_together():
    # the split (0.4): both on at once, finite, stereo, and different from either alone
    both, _ = run("phcho", [(12, 127), (13, 100), (85, 64), (86, 100)], [(100, 60, 100, 1500)], 1800)
    pha, _ = run("phonly", [(12, 127), (13, 100)], [(100, 60, 100, 1500)], 1800)
    assert np.max(np.abs(both)) < 32767 and np.isfinite(both).all()
    assert rms_db(seg(both - pha, 0.2, 1.5)) > rms_db(seg(both, 0.2, 1.5)) - 20


def test_level_trim():
    # LEVEL (CC 110, 0.8): 64 = 0 dB, 0 = -12 dB, 0.1875 dB a step; it scales everything after the effects
    notes = [(100, 57, 100, 600)]
    ref, _ = run("lvl64", [(81, 0), (91, 0), (110, 64)], notes, 800)
    lo, _ = run("lvl0", [(81, 0), (91, 0), (110, 0)], notes, 800)
    d = rms_db(seg(lo, 0.12, 0.6)) - rms_db(seg(ref, 0.12, 0.6))
    assert abs(d + 12.0) < 0.5, d


def centroid(x):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return float(np.sum(sp * fr) / np.sum(sp))


def band_db(x, f0, f1):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return 10 * np.log10(np.sum(sp[(fr >= f0) & (fr < f1)]) + 1e-12)


# the FILTER (0.9.3): CC 70 TYPE in 7 bands of 18.3 (OFF 0-18, LP12 19-36, LP18 37-54, HP12 55-73, HP18 74-91,
# BP 92-109, NOTCH 110-127), 74 cutoff,
# 71 resonance, 72 LFO amount; the LFO: 75 shape in 6 bands (square: 86-106), 76 rate
CHORD = [(100, n, 110, 1600) for n in (48, 55, 60, 64, 67, 72, 76)]


def test_filter_off_is_transparent():
    L0, _ = run("flt0", [(81, 0), (91, 0), (70, 0)], CHORD, 1900)
    L1, _ = run("flt1", [(81, 0), (91, 0), (70, 20), (74, 127), (72, 0), (71, 0)], CHORD, 1900)   # LP wide open: 16 kHz
    a, b = seg(L0, 0.3, 1.5), seg(L1, 0.3, 1.5)
    assert abs(rms_db(a) - rms_db(b)) < 0.5          # the open low-pass changes nothing audible
    assert np.max(np.abs(L1)) < 32000


def test_filter_types_do_their_job():
    dry, _ = run("fltd", [(81, 0), (91, 0), (70, 0), (72, 0)], CHORD, 1900)   # (OFF: settings persist between runs)
    lp, _ = run("fltlp", [(81, 0), (91, 0), (70, 20), (74, 40), (71, 0), (72, 0)], CHORD, 1900)   # LP ~200 Hz
    hp, _ = run("flthp", [(81, 0), (91, 0), (70, 64), (74, 90), (71, 0), (72, 0)], CHORD, 1900)   # HP ~2 kHz
    d, l, h = (seg(x, 0.3, 1.5) for x in (dry, lp, hp))
    assert band_db(l, 1000, 8000) < band_db(d, 1000, 8000) - 20      # the low-pass takes the highs away
    assert band_db(h, 50, 400) < band_db(d, 50, 400) - 20            # the high-pass the lows
    assert centroid(l) < centroid(d) < centroid(h)
    for x in (l, h):
        assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 32000


def test_filter_resonance_and_lfo():
    q0, _ = run("fltq0", [(81, 0), (91, 0), (70, 20), (74, 60), (71, 0), (72, 0)], CHORD, 1900)
    q1, _ = run("fltq1", [(81, 0), (91, 0), (70, 20), (74, 60), (71, 127), (72, 0)], CHORD, 1900)
    fc = 30 * (16000 / 30) ** (60 * 2 / 254)                         # the card's cutoff for 60
    a, b = seg(q0, 0.3, 1.5), seg(q1, 0.3, 1.5)
    assert band_db(b, fc / 1.3, fc * 1.3) > band_db(a, fc / 1.3, fc * 1.3) + 6   # resonance: a peak at the cutoff
    assert np.all(np.isfinite(b)) and np.max(np.abs(b)) < 32700
    # the LFO (square, ~2 Hz, full depth) moves the cutoff: the brightness changes over time
    m, _ = run("fltlfo", [(81, 0), (91, 0), (70, 20), (74, 70), (72, 127), (75, 96), (76, 40)], CHORD, 1900)
    cs = [centroid(seg(m, t, t + 0.1)) for t in np.arange(0.3, 1.4, 0.1)]
    assert max(cs) > 1.3 * min(cs)                   # (LP12: the centroid swings ~1.5x)


def lfo_rate_shown(v, half):
    """the rate the SPEED / RATE card shows (params.c F_HZ / F_HZH): 0.5 * 24^(v/127) Hz, halved for F_HZH"""
    hz = 0.5 * 24 ** (v / 127)
    return hz / 2 if half else hz



def band_share(x, lo, hi, n=441):
    """the share of energy in the lo..hi Hz band, in 10 ms frames, with the notes' decay removed"""
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    sh = []
    for i in range(0, len(x) - n, n):
        s = np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n))) ** 2
        sh.append(s[m].sum() / (s.sum() + 1e-9))
    sh = np.array(sh)
    t = np.arange(len(sh))
    return sh - np.polyval(np.polyfit(t, sh, 3), t)

def test_phaser_speed_card_matches_the_sound():
    """the PHASER SPEED card shows the rate the sweep really runs at: fx.c runs the phaser (and the chorus, the same
    code path: FX_LFO_INC >> 1) at half the LFO table's rate, so the card (F_HZH) shows half; one triangle sweep,
    up and down, per cycle moves the notches once"""
    for v in (40, 64, 90):
        chord = [(100, n, 100, 5800) for n in (36, 43, 48, 55, 60, 64, 67, 72)]
        L, _ = run(f"phrate{v}", [(81, 0), (91, 0), (64, 127), (85, 127), (13, 127), (16, 0), (14, v)], chord, 6000)
        sh, fps = band_share(seg(L, 0.5, 5.9), 300, 3000), FS / 441
        corr = lambda hz: np.corrcoef(sh[:-int(round(fps / hz))], sh[int(round(fps / hz)):])[0, 1]
        shown, table = lfo_rate_shown(v, half=True), lfo_rate_shown(v, half=False)
        # the sweep repeats at the rate the card shows; at the table's rate (half its period) it is a time-reversed
        # half sweep, so it does not correlate
        assert corr(shown) > 0.15 and corr(shown) > corr(table) + 0.1, (v, corr(shown), corr(table))
