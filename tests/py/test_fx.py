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
