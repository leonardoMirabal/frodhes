# SPDX-License-Identifier: GPL-3.0-only
"""The delay's TAPE type (firmware/src/dly_tape.c; EFFECT = TAPE, CC 88 96..127): it echoes, it differs from ANA,
it is darker at a longer TIME (slower tape) and with TONE down, its echoes wobble a little (a transport's wow and
flutter: well under ANA's), FDBK alone sets their regeneration (a tail that ends up to 113, a bounded build-up from
about 115), switching to and from it and moving its knobs does not
click, and after OFF its tail ends in exact digital zero."""
import os

import numpy as np
import pytest

import hostrun as hr

SW = dict(OFF=0, DIG=48, ANA=80, TAPE=112)
CLEAN = [hr.cc(5, 120, 0), hr.cc(6, 91, 0), hr.cc(7, 81, 0), hr.cc(8, 106, 0)]   # reverb, drive, compressor off


def d2(x):
    return np.abs(np.diff(x, 2))


def clicks(x):
    """bar_measure.clicks / hil_scan: second-difference spikes far above the local level"""
    lv = np.convolve(np.abs(x), np.ones(441) / 441, "same")[1:-1] + 50
    return int((d2(x) > 6 * lv + 2000).sum())


def dly(kind, mix=100, time=70, fb=60, tone=70, t=20):
    return [hr.cc(t, 88, SW[kind]), hr.cc(t + 1, 89, mix), hr.cc(t + 2, 90, time), hr.cc(t + 3, 26, fb),
            hr.cc(t + 4, 27, tone)]


def chords(t0=300, n=6, step=700, dur=400):
    ls = []
    for k in range(n):
        for p in ((48, 60, 64, 67), (50, 62, 65, 69))[k % 2]:
            ls += hr.note(t0 + k * step, p, 105, dur)
    return ls


def first_echo(kind, time=100, tone=70):
    """one staccato chord, MIX 127, FDBK 0: the echo's window and the chord's window (one delay apart)"""
    r = hr.run(f"tape-echo-{kind}-{time}-{tone}", CLEAN + dly(kind, 127, time, 0, tone) +
               [l for k in (36, 48, 60, 64, 67, 72, 79, 84, 91) for l in hr.note(300, k, 115, 60)], 3000)
    T = int(round(0.020 * 74 ** (time / 127) * hr.FS))
    a = r.L[int(0.3 * hr.FS):int(0.3 * hr.FS) + 8192]
    b = r.L[int(0.3 * hr.FS) + T:int(0.3 * hr.FS) + T + 8192]
    return a, b


def hf_ratio(b):
    """the echo's energy above 6 kHz re 300 Hz .. 2 kHz (dB)"""
    B = np.abs(np.fft.rfft(b * np.hanning(len(b)))) ** 2
    f = np.fft.rfftfreq(len(b), 1 / hr.FS)
    return 10 * np.log10(B[f > 6000].sum() / B[(f > 300) & (f < 2000)].sum())


def test_tape_echoes_and_differs_from_ana():
    ra, ta = first_echo("ANA")
    rt, tt = first_echo("TAPE")
    assert hr.rms_db(tt) > hr.rms_db(rt) - 12, "TAPE: an echo one delay after the chord"
    assert abs(hr.rms_db(tt) - hr.rms_db(ta)) < 3, (hr.rms_db(tt), hr.rms_db(ta))   # level near ANA's
    assert hf_ratio(tt) < hf_ratio(ta) - 3, "the playback head's high-frequency loss (ANA's first echo is flat)"


def test_tape_darker_when_slower_and_with_tone():
    fast = hf_ratio(first_echo("TAPE", 70)[1])        # ~260 ms: a faster tape
    slow = hf_ratio(first_echo("TAPE", 100)[1])       # ~600 ms
    dark = hf_ratio(first_echo("TAPE", 100, 20)[1])
    bright = hf_ratio(first_echo("TAPE", 100, 120)[1])
    assert slow < fast - 3, (fast, slow)
    assert dark < slow - 3 < bright - 6, (dark, slow, bright)


def wobble(kind):
    """a held A5 under its echoes (MIX 127, FDBK 0, TIME 100): cents p-p of the 880 Hz band (as test_every_control)"""
    r = hr.run(f"tape-wob-{kind}", CLEAN + dly(kind, 127, 100, 0) + [hr.cc(23, 102, 127), hr.cc(24, 64, 127)]
               + hr.note(200, 81, 100, 5000), 5500)
    x = r.seg(2.5, 5.0)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / hr.FS)
    X[(f < 820) | (f > 940)] = 0
    a = np.fft.irfft(X * 2, len(x))
    h = np.fft.fft(a)
    h[len(a) // 2 + 1:] = 0
    h[1:len(a) // 2] *= 2
    an = np.fft.ifft(h)
    fi = np.convolve(np.diff(np.unwrap(np.angle(an))) * hr.FS / (2 * np.pi), np.ones(441) / 441, "valid")[2000:-2000]
    c = 1200 * np.log2(fi / np.median(fi))
    return np.ptp(c)


def test_tape_wobble_subtle():
    """a transport's wow and flutter (rms ~0.1 %: a few cents), clearly more than DIG, well under ANA's 0.7 Hz sine"""
    d, a, t = wobble("DIG"), wobble("ANA"), wobble("TAPE")
    assert d + 2 < t < a, (d, t, a)


def test_tape_feedback_range():
    """FDBK alone sets TAPE's regeneration (0.9.5: DIG / ANA's too): up to FDBK 113 the tail ends in exact digital zero;
    at 127 the loop builds up, held by the tape's saturation: bounded, no DC (the units self-oscillate at the top of
    their range; 0.9.5: from about 115, as ANA)"""
    r = hr.run("tape-fb113", CLEAN + dly("TAPE", 64, 64, 113) + chords(n=1), 30000)
    assert hr.rms_db(r.seg(1.0, 2.0)) > -40
    assert np.all(r.seg(28.0, 30.0) == 0) and np.all(r.seg(28.0, 30.0, 1) == 0)
    r = hr.run("tape-fb127", CLEAN + dly("TAPE", 64, 64, 127) + chords(n=1), 60000)
    late = r.seg(50.0, 60.0)
    assert -30 < hr.rms_db(late) < -4, hr.rms_db(late)
    assert np.abs(late).max() < 0.8 * 32767 and abs(late.mean()) < 6


def echo_rel(kind, mix, fb=0):
    """the first echo re the chord (dB), first_echo's windows, at this MIX"""
    r = hr.run(f"dly-rel-{kind}-{mix}-{fb}", CLEAN + dly(kind, mix, 100, fb) +
               [l for k in (36, 48, 60, 64, 67, 72, 79, 84, 91) for l in hr.note(300, k, 115, 60)], 3000)
    T = int(round(0.020 * 74 ** (100 / 127) * hr.FS))
    a = r.L[int(0.3 * hr.FS):int(0.3 * hr.FS) + 8192]
    b = r.L[int(0.3 * hr.FS) + T:int(0.3 * hr.FS) + T + 8192]
    return hr.rms_db(b) - hr.rms_db(a)


@pytest.mark.parametrize("kind", ["DIG", "ANA", "TAPE"])
def test_mix_first_echo_as_loud_as_the_dry_at_64(kind):
    """MIX (0.9.5, the user): the first echo as loud as the dry at 64, in these windows (8192 samples of the chord and
    of its echo, so the chord's own tail is in both; 8d913af: -2.8 / -3.0 / -4.4 dB, the echo's gain -4.8 dB re the
    eased dry), the same at 127 as before (+3.0 here: the gain +5.9 dB), and MIX 32 still clearly under the dry"""
    e32, e64, e127 = echo_rel(kind, 32), echo_rel(kind, 64), echo_rel(kind, 127)
    assert abs(e64) < 1.0, e64
    assert 2.0 < e127 < 4.0, e127
    assert e32 < e64 - 3, (e32, e64)


def repeat_loss(kind, mix, fb):
    """dB lost per repeat (TIME 64: the 2nd vs the 4th echo of one staccato chord)"""
    r = hr.run(f"dly-loss-{kind}-{mix}-{fb}", CLEAN + dly(kind, mix, 64, fb) +
               [l for k in (48, 60, 64, 67) for l in hr.note(300, k, 115, 40)], 4000)
    T = 0.020 * 74 ** (64 / 127)
    e = lambda k: hr.rms_db(r.seg(0.3 + k * T, 0.3 + k * T + 0.06))
    return (e(2) - e(4)) / 2


@pytest.mark.parametrize("kind", ["DIG", "ANA", "TAPE"])
def test_fdbk_independent_of_mix(kind):
    """FDBK alone sets the regeneration (0.9.5; DIG / ANA scaled it by MIX: at MIX 64 the repeats died fast): the loss
    per repeat at FDBK 90 is the same at MIX 40 and 127"""
    a, b = repeat_loss(kind, 40, 90), repeat_loss(kind, 127, 90)
    assert 0 < a < 6 and abs(a - b) < 0.7, (a, b)


@pytest.mark.parametrize("kind", ["ANA", "TAPE"])
def test_self_oscillation_bounded_and_it_decays_again(kind):
    """ANA and TAPE at FDBK 127: one chord's echoes build up into oscillation (as the analog and tape units do), held by
    the softclip / the tape's saturation (TAPE builds up more slowly: its head's losses): bounded, no DC, finite; FDBK
    back to 60 and it dies away to digital zero"""
    r = hr.run(f"dly-osc-{kind}", CLEAN + dly(kind, 100, 70, 127) + chords(n=1) + [hr.cc(45000, 26, 60)], 66000)
    x = np.concatenate([r.seg(35.0, 45.0), r.seg(35.0, 45.0, 1)])
    assert -30 < hr.rms_db(x) < -4, hr.rms_db(x)
    assert np.abs(x).max() < 0.8 * 32767 and abs(x.mean()) < 6
    assert hr.rms_db(r.seg(40.0, 45.0)) > hr.rms_db(r.seg(2.0, 2.4)) - 6, "it does not die at 127"
    assert np.all(r.seg(62.0, 66.0) == 0) and np.all(r.seg(62.0, 66.0, 1) == 0)


@pytest.mark.parametrize("kind", ["DIG", "ANA", "TAPE"])
def test_fdbk_sweep_no_click(kind):
    """FDBK swept up into the oscillation zone and back down (and jumped) while the echoes ring: no click"""
    sweep = [hr.cc(1200 + 40 * i, 26, v) for i, v in enumerate(list(range(0, 128, 4)) + list(range(127, -1, -4)))]
    sweep += [hr.cc(4000, 26, 127), hr.cc(4400, 26, 0), hr.cc(4800, 26, 120)]
    r = hr.run(f"dly-fbsweep-{kind}", CLEAN + dly(kind, 100, 60, 40) + [hr.cc(250, 64, 127)]
               + chords(n=8, step=650, dur=600) + sweep, 7000)
    assert clicks(r.L) == 0 and clicks(r.R) == 0


def test_delay_fuzz_no_overflow():
    """the delay's knobs at random (FDBK mostly in the oscillation zone, every EFFECT, loud chords and the pedal) under
    the sanitizers: no signed overflow, division by zero or oversized shift, the output finite and below full scale"""
    b = hr.build(os.path.join(hr.OUT, "frodhes-ubsan"), ["-fsanitize=signed-integer-overflow,integer-divide-by-zero,shift-exponent", "-fno-sanitize-recover=all"])
    rng = np.random.default_rng(958)
    ls = CLEAN + [hr.cc(250, 64, 127)] + chords(n=14, step=700, dur=500)
    for t in range(400, 10000, 90):
        k = rng.integers(0, 5)
        if k == 0:
            ls.append(hr.cc(t, 88, int(rng.choice([0, 48, 80, 112]))))
        elif k == 1:
            ls.append(hr.cc(t, 26, int(rng.choice([int(rng.integers(0, 128)), 127, 125, 118]))))
        else:
            ls.append(hr.cc(t, (89, 90, 27)[k - 2], int(rng.integers(0, 128))))
    r = hr.run("dly-fuzz", ls + [hr.cc(10000, 26, 127), hr.cc(10000, 88, 80)], 25000, binary=b)
    assert "runtime error" not in r.out, r.out[-2000:]
    assert np.all(np.isfinite(r.L)) and np.abs(r.L).max() < 32767 and np.abs(r.R).max() < 32767


@pytest.mark.parametrize("first, seq", [
    ("ANA", ["TAPE", "DIG", "TAPE", "ANA", "TAPE"]),
    ("DIG", ["TAPE", "ANA", "TAPE", "OFF", "TAPE"]),
])
def test_type_switch_no_click(first, seq):
    """switches while the echoes ring under held chords: the worst second difference near a switch stays within 2x the
    same moment of the steady renders (a block-wise step of the old type's mix was ~10x)"""
    ts = [1800 + 1300 * k for k in range(len(seq))]
    sw = [hr.cc(t, 88, SW[v]) for t, v in zip(ts, seq)]
    pedal = [hr.cc(250, 64, 127)]
    body = CLEAN + dly(first, 100, 60, 90) + pedal + chords(n=12, step=650, dur=600)
    r = hr.run(f"tape-sw-{first}", body + sw, 9500)
    refs = [hr.run(f"tape-sw-ref-{k}", CLEAN + dly(k, 100, 60, 90) + pedal + chords(n=12, step=650, dur=600), 9500)
            for k in ("ANA", "DIG", "TAPE")]
    assert clicks(r.L) == 0 and clicks(r.R) == 0
    for t in ts:
        a, b = int((t / 1000 - 0.005) * hr.FS), int((t / 1000 + 0.15) * hr.FS)
        got = d2(r.L[a:b]).max()
        ref = max(d2(q.L[a:b]).max() for q in refs)
        assert got < 2 * ref + 50, (t, got, ref)


@pytest.mark.parametrize("kind", ["DIG", "ANA", "TAPE"])
def test_send_switch_no_kink_in_the_echo(kind):
    """EFFECT switched OFF under held chords, then back on: the send ramps over a block (fx.c dly_son: the old EFFECT
    once more with the make-up ramped to 0, and from 0 after a block with the send off), so the echo of each switch,
    one delay time later (TIME 60: 153 ms), has no kink: the second difference there within 1.5x the steady renders'
    (the EFFECT on all along, OFF all along) at the same moment, and so is the switch's own block. Before (57e0025):
    the send cut at a block edge (the echo of an OFF here 1646 against 73 on DIG; TAPE -> OFF 245 against 89 in
    test_type_switch_no_click's DIG-seq1 on whirl-40's Rhodes); ANA's tail run by the DIG loop (1207 against 50); and
    OFF -> DIG / ANA reading a line the tail had left a ring (1.49 s) old: a step of 9741 / 10814 against 150 at the
    switch (dly_tail_over: |lp| < 2 at a block's end cut a ringing tail)"""
    D = 6739 / hr.FS                                    # (fx_tables.h FX_DLY_T[60]: 6739 samples)
    ts = (2003, 3507, 5011, 6519)                        # off, on, off, on: at four offsets in a block
    body = CLEAN + dly(kind, 100, 60, 50) + [hr.cc(250, 64, 127)] + chords(n=10, step=700, dur=650)
    sw = [hr.cc(t, 88, SW["OFF"] if k % 2 == 0 else SW[kind]) for k, t in enumerate(ts)]
    r = hr.run(f"dly-sendsw-{kind}", body + sw, 8000)
    refs = [hr.run(f"dly-sendsw-ref-{k}", CLEAN + dly(k, 100, 60, 50) + [hr.cc(250, 64, 127)]
                   + chords(n=10, step=700, dur=650), 8000) for k in (kind, "OFF")]
    assert clicks(r.L) == 0 and clicks(r.R) == 0
    for t in ts:
        for w0, w1 in ((-0.008, 0.012), (D - 0.012, D + 0.020)):       # the switch's block, and its echo
            a, b = int((t / 1000 + w0) * hr.FS), int((t / 1000 + w1) * hr.FS)
            for ch in ("L", "R"):
                got = d2(getattr(r, ch)[a:b]).max()
                ref = max(d2(getattr(q, ch)[a:b]).max() for q in refs)
                assert got < 1.5 * ref + 20, (kind, t, w0, ch, got, ref)


def test_tape_knob_moves_no_click():
    moves = [hr.cc(1500, 90, 40), hr.cc(2500, 90, 110), hr.cc(3200, 26, 120), hr.cc(3600, 27, 10),
             hr.cc(4000, 27, 127), hr.cc(4400, 89, 30), hr.cc(4800, 89, 127), hr.cc(5200, 90, 64)]
    r = hr.run("tape-knobs", CLEAN + dly("TAPE", 100, 70, 80) + [hr.cc(250, 64, 127)] + chords(n=9, step=650, dur=600)
               + moves, 7000)
    assert clicks(r.L) == 0 and clicks(r.R) == 0


def test_tape_tail_after_off_ends_in_zero():
    r = hr.run("tape-off", CLEAN + dly("TAPE", 100, 60, 90) + chords(n=2) + [hr.cc(1800, 88, 0)], 16000)
    assert hr.rms_db(r.seg(1.9, 2.4)) > -60, "the tail rings on after OFF"
    tail = np.concatenate([r.seg(15.0, 16.0), r.seg(15.0, 16.0, 1)])
    assert np.all(tail == 0), np.abs(tail).max()
