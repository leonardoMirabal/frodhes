# SPDX-License-Identifier: GPL-3.0-only
"""MIDI clock in (CLOCK EXT, firmware/src/tempo.c tempo_clock / tempo_lock_poll) on the host build: synthetic clock
streams, as USB-MIDI and as the TRS jack, steady, bursty (a computer's USB packets: several pulses at once, late,
then caught up), jittered (the TRS polls held back by the audio ISR), with the main loop busy 0-30 ms at a time
("T stall": the messages wait in the ring, stamped with their arrival as usb.c / midi_uart.c do), tempo ramps and
jumps, Start / Stop / Continue. Checked: the tempo (bpm10, the samples a beat, the synced delay time) and, in the
sound, the tremolo SYNC 1/4: its period within 0.5 % and its loudest point within 5 % of a beat from the beats sent,
2 bars after Start. Before 0.9.5's fix the tempo never followed a clock read in bursts (any interval under 8 ms reset
the count) and nothing locked the LFOs' phase."""
import re

import numpy as np
import pytest

import hostrun

FS = 44100
CC_TW_SW, CC_TW_DEPTH, CC_TW_SYNC, CC_TW_MODE, CC_TW_SHAPE = 17, 18, 20, 21, 22
CC_DL_SW, CC_DL_DEPTH, CC_DL_SYNC = 88, 89, 25


def band(x, hi):
    """the CC value of enum x of a parameter whose range is 0..hi (x = v * (hi + 1) >> 7)"""
    return min(127, -(-x * 128 // (hi + 1)))


def setup(t=20, delay=False):
    ls = [hostrun.cc(t, CC_TW_SW, band(1, 2)), hostrun.cc(t, CC_TW_DEPTH, 64), hostrun.cc(t, CC_TW_MODE, 127),
          hostrun.cc(t, CC_TW_SHAPE, 0), hostrun.cc(t, CC_TW_SYNC, band(9, 13))]       # TRM, AM, SYNC 1/4
    if delay:
        ls += [hostrun.cc(t, CC_DL_SW, band(1, 3)), hostrun.cc(t, CC_DL_SYNC, band(9, 13))]   # DIG, SYNC 1/4
    return ls


def chord(t, dur):
    ls = [hostrun.cc(t - 5, 64, 127)]                                                 # the pedal holds it
    for n in (48, 55, 60, 64, 67):
        ls += hostrun.note(t, n, 100, dur)
    return ls


def pulses(t0, bpm, n, src="midi"):
    """n pulses from t0 (ms, float) at bpm: [(true time ms, line)]"""
    p = 60000.0 / bpm / 24
    return [(t0 + i * p, src) for i in range(n)]


def emit(ps, arrive=lambda t: t):
    """script lines for pulses [(true t, src)], each arriving at arrive(t) (ms, rounded down to the ms)"""
    return [f"{int(arrive(t))} {src} 0xF8 0 0" for t, src in ps]


def stalls(t0, t1, rng, lo=0, hi=30, every=(5, 60)):
    """the main loop busy lo..hi ms, every few ms (a screen draw, a page change)"""
    ls, t = [], t0
    while t < t1:
        t += rng.uniform(*every)
        ls.append(f"{int(t)} stall {int(rng.uniform(lo, hi))}")
    return ls


def trem_audio_phase(r, t0_ms, t1_ms, beat0_ms, beat_ms):
    """the tremolo's loudest point against the beats in the sound (fraction of a beat, -0.5..0.5): the envelope's
    component at the beat rate, fitted per 2 beats, the median"""
    x = (r.L + r.R)[int(t0_ms * FS / 1000):int(t1_ms * FS / 1000)]
    hop = 44
    env = hostrun.env_rms(x, hop)
    t = t0_ms + (np.arange(len(env)) + 0.5) * hop * 1000.0 / FS                     # ms
    out = []
    w = int(2 * beat_ms * FS / 1000 / hop)
    for i in range(0, len(env) - w, w // 2):
        e, tt = env[i:i + w], t[i:i + w]
        y = e / np.polyval(np.polyfit(tt, e, 1), tt) - 1
        a = np.sum(y * np.exp(-2j * np.pi * (tt - beat0_ms) / beat_ms))
        out.append(np.angle(a) / (2 * np.pi))
    return (np.median(out) + 0.5) % 1.0 - 0.5


def lfo_errors(r, times, beat0_ms, beat_ms):
    """the tremolo LFO's phase (its cycle's start, phase 0, due on each beat: SYNC 1/4) at each dump, against the beats
    sent: the error in beats (-0.5..0.5), at the time of the last rendered frame"""
    errs = []
    for t in times:
        s = r.state(t)
        ph = int(s["twph"]) / 2 ** 32
        tm = int(s["wavn"]) * 1000.0 / FS
        want = ((tm - beat0_ms) / beat_ms) % 1.0
        errs.append((ph - want + 0.5) % 1.0 - 0.5)
    return np.array(errs)


def run(tag, lines, end_ms, dumps=()):
    return hostrun.run(f"clock-{tag}", lines + [f"{int(t)} dump" for t in dumps], end_ms=end_ms)


def tempo_at(r, t):
    s = r.state(t)
    return int(s["bpm10"]), int(s["beatsmp"]), int(s["clklock"]), int(s["dlysync"])


def lock_dumps(t_from, t_end, beat):
    """dump times: every 0.37 beat from t_from to the end (not in step with the beat or the blocks)"""
    return [int(t) for t in np.arange(t_from, t_end - 30, 0.37 * beat)]


def check_lock(r, times, beat0, beat, audio=None):
    """the LFO's phase within 5 % of a beat from the beats at every dump, its period within 0.5 % (the drift of
    the error over the dumps); audio = (t0, t1): the loudest point in the sound within 5 % too"""
    e = lfo_errors(r, times, beat0, beat)
    assert np.max(np.abs(e)) < 0.05, f"{r.tag}: the LFO's phase is {np.max(np.abs(e)) * 100:.1f} % of a beat off"
    tt = np.array([int(r.state(t)["wavn"]) * 1000.0 / FS for t in times])
    slope = np.polyfit(tt, e, 1)[0] * beat                       # beats of error per beat: the period's error
    assert abs(slope) < 0.005, f"{r.tag}: the LFO's period is {slope * 100:+.2f} % off"
    if audio:
        ph = trem_audio_phase(r, audio[0], audio[1], beat0, beat) + 0.25   # (loudest a quarter beat after it)
        ph = (ph + 0.5) % 1.0 - 0.5
        assert abs(ph) < 0.05, f"{r.tag}: the tremolo's loudest point is {ph * 100:+.1f} % of a beat from the beat"
    return e


@pytest.mark.parametrize("bpm,src", [(90, "midi"), (120, "midi"), (137, "midi"), (120, "trs"), (137, "trs")])
def test_steady(bpm, src):
    """a steady clock with Start, the chord held from before Start (the lock slews: no jump in the sound)"""
    t_fa, beat = 600, 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, int(24 * 16), src)
    end = int(ps[-1][0]) + 20
    times = lock_dumps(t_fa + 8 * beat, end, beat)
    lines = setup(delay=True) + chord(300, end) + [f"{t_fa} {src} 0xFA 0 0"] + emit(ps)
    r = run(f"steady-{bpm}-{src}", lines, end, dumps=times)
    b10, smp, lock, dly = tempo_at(r, times[-1])
    exact = FS * 60.0 / bpm
    assert lock == 1 and abs(b10 - bpm * 10) <= 1, (b10, lock)
    assert abs(smp / exact - 1) < 0.001 and abs(dly / exact - 1) < 0.001, (smp, dly, exact)   # the delay: 1/4
    check_lock(r, times, ps[0][0], beat, audio=(t_fa + 8 * beat, end - 50))


@pytest.mark.parametrize("src", ["midi", "trs"])
def test_busy_main_loop(src):
    """0.9.5's bug: the main loop busy 0-30 ms at a time reads the pulses in bursts. The arrival stamps keep the
    tempo and the phase (before the fix the 24-pulse count reset on every burst: the tempo stayed at 120.0)"""
    rng = np.random.default_rng(7)
    bpm, t_fa = 137, 600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 16, src)
    end = int(ps[-1][0]) + 20
    times = lock_dumps(t_fa + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + [f"{t_fa} {src} 0xFA 0 0"] + emit(ps) + stalls(400, end - 50, rng)
    r = run(f"busy-{src}", lines, end, dumps=times)
    b10, smp, lock, _ = tempo_at(r, times[-1])
    assert lock == 1 and abs(b10 - 1370) <= 1, b10
    check_lock(r, times, ps[0][0], beat, audio=(t_fa + 8 * beat, end - 50))


def test_bursty_usb():
    """a computer's USB timing: the pulses leave in packets (every 4 ms, several together, 0-1 ms apart), sometimes
    held back up to 15 ms and then caught up; the main loop busy as well"""
    rng = np.random.default_rng(3)
    bpm, t_fa = 120, 600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 16)
    hold = {}

    def arrive(t):
        a = np.ceil(t / 4) * 4 + rng.uniform(0, 1)
        k = int(t // 200)
        if k not in hold:
            hold[k] = rng.uniform(0, 15) if rng.uniform() < 0.4 else 0
        return max(a, k * 200 + hold[k]) if t - k * 200 < hold[k] else a   # held, then all at once
    end = int(ps[-1][0]) + 40
    times = lock_dumps(t_fa + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps, arrive) + stalls(400, end - 50, rng, hi=12)
    r = run("bursty", lines, end, dumps=times)
    b10, _, lock, _ = tempo_at(r, times[-1])
    assert lock == 1 and abs(b10 - 1200) <= 2, b10
    check_lock(r, times, ps[0][0] + 2, beat)                      # (the packets: 2 ms late on average)


def test_jittered_trs():
    """the TRS jack: each pulse stamped 0-5 ms late (its poll held back while the audio ISR runs)"""
    rng = np.random.default_rng(11)
    bpm, t_fa = 90, 600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 16, "trs")
    end = int(ps[-1][0]) + 20
    times = lock_dumps(t_fa + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + ["600 trs 0xFA 0 0"] + emit(ps, lambda t: t + rng.uniform(0, 5))
    r = run("jitter-trs", lines, end, dumps=times)
    b10, _, lock, _ = tempo_at(r, times[-1])
    assert lock == 1 and abs(b10 - 900) <= 1, b10
    check_lock(r, times, ps[0][0] + 2.5, beat)                   # (the mean lateness: the phase follows the stamps)


def test_tempo_jump_and_ramp():
    """a jump 120 -> 90 is followed within 2 beats; a ramp 90 -> 140 over 8 s is tracked within 2 %; the LFO is
    locked again 2 bars after the ramp ends"""
    ps, t = [], 601.0
    for i in range(24 * 8):                                       # 8 beats at 120
        ps.append((t, "midi")); t += 60000 / 120 / 24
    t_jump = t
    for i in range(24 * 8):                                       # 8 beats at 90
        ps.append((t, "midi")); t += 60000 / 90 / 24
    t_ramp = t
    while t < t_ramp + 8000:                                      # the ramp: 90 -> 140
        bpm = 90 + 50 * (t - t_ramp) / 8000
        ps.append((t, "midi")); t += 60000 / bpm / 24
    k_steady, t_steady = len(ps), t
    for i in range(24 * 16):                                      # 16 beats at 140
        ps.append((t, "midi")); t += 60000 / 140 / 24
    end = int(t) + 20
    beat = 60000 / 140
    jump_ok = int(t_jump + 2 * 667)
    mid_ramp = int(t_ramp + 6000)
    times = lock_dumps(t_steady + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps)
    r = run("ramp", lines, end, dumps=[jump_ok, mid_ramp] + times)
    assert abs(tempo_at(r, jump_ok)[0] - 900) <= 2, tempo_at(r, jump_ok)
    want = 90 + 50 * (6000 - 750) / 8000                         # (the estimate spans about the last 1.5 beats)
    assert abs(tempo_at(r, mid_ramp)[0] / 10 / want - 1) < 0.02, (tempo_at(r, mid_ramp), want)
    assert abs(tempo_at(r, times[-1])[0] - 1400) <= 1
    # the beats: pulse 0 is beat 1; the steady part starts on pulse k_steady (its phase within the beat counts)
    beat0 = t_steady - (k_steady % 24) * beat / 24
    check_lock(r, times, beat0, beat)


def test_start_stop_continue():
    """Start, 4 bars, Stop (the clock stops too), 1 s, Continue: the count goes on from where it stopped (a 1/4 LFO
    on the resumed beats), the lock comes back; a second Start makes the next pulse beat 1 again"""
    bpm, p = 120, 60000 / 120 / 24
    beat = 24 * p
    ps = [(601 + i * p, "midi") for i in range(24 * 16 + 12)]    # 16 and a half beats: stopped mid-beat
    t_stop = ps[-1][0] + p / 2
    t_cont = t_stop + 1000
    ps2 = [(t_cont + 1 + i * p, "midi") for i in range(24 * 12)]
    t_fa2 = ps2[-1][0] + p / 2
    ps3 = [(t_fa2 + 7 + i * p, "midi") for i in range(24 * 12)]   # Start, then the pulses (a beat from here)
    end = int(ps3[-1][0]) + 20
    lines = (setup() + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps) + [f"{int(t_stop)} midi 0xFC 0 0",
             f"{int(t_cont)} midi 0xFB 0 0"] + emit(ps2) + [f"{int(t_fa2)} midi 0xFA 0 0"] + emit(ps3))
    times2 = lock_dumps(t_cont + 8 * beat, t_fa2, beat)
    times3 = lock_dumps(ps3[0][0] + 8 * beat, end, beat)
    r = run("startstop", lines, end, dumps=[int(t_stop) + 400] + times2 + times3)
    assert tempo_at(r, int(t_stop) + 400)[2] == 0                # no pulses: the lock is off, the tempo stays
    assert tempo_at(r, int(t_stop) + 400)[0] == 1200
    assert tempo_at(r, times2[-1])[2] == 1
    check_lock(r, times2, ps2[0][0] - 12 * p, beat)              # Continue: pulse 12 of a beat comes first
    check_lock(r, times3, ps3[0][0], beat)


def note_ons(r, t0):
    """the note-ons sent to MIDI out after t0: [(ms, note)]"""
    return [(t, a) for t, s, a, b in r.midi_out if s & 0xF0 == 0x90 and b and t >= t0]


@pytest.mark.parametrize("busy", [False, True])
def test_arp_on_the_pulses(busy):
    """the arpeggiator (UP, 1/16) under the clock: a step on every 6th pulse from Start's first, never before its
    pulse arrives, none missed or doubled (with the main loop busy too)"""
    rng = np.random.default_rng(5)
    bpm, t_fa = 137, 600
    ps = pulses(t_fa + 1, bpm, 24 * 8)
    end = int(ps[-1][0]) + 20
    lines = ["20 midi 0xB0 52 1", "20 midi 0xB0 53 3"]               # UP, 1/16
    for n in (60, 64, 67):
        lines += hostrun.note(300, n, 100, end - 300)
    lines += [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + (stalls(700, end - 50, rng, hi=25) if busy else [])
    r = run(f"arp-{busy}", lines, end)
    ons = note_ons(r, t_fa)
    want = [int(ps[i][0]) for i in range(0, len(ps), 6)]
    assert len(ons) == len(want), (len(ons), len(want))
    late = np.array([t for t, _ in ons]) - np.array(want)
    assert late.min() >= 0, late                                      # never ahead of the clock
    assert late.max() <= (27 if busy else 1), late
    assert [n for _, n in ons[:6]] == [60, 64, 67, 60, 64, 67]


@pytest.mark.parametrize("busy", [False, True])
def test_sequencer_on_the_clock(busy):
    """a 1-bar pattern recorded at the FM-1's own 120 BPM (notes on the 4 beats, QUANT 1/16) plays under a 137 BPM
    clock: Start plays bar 1 on the first pulse, each note on its beat's pulse (never before it, with the main loop
    busy too), Stop stops it, Continue goes on from the song position"""
    rng = np.random.default_rng(9)
    lines = ["20 midi 0xB0 61 1", "20 midi 0xB0 62 3", "100 midi 0xB0 63 127", "100 midi 0xB0 60 127"]
    for k, n in enumerate((60, 62, 64, 65)):                          # bar 1 at 120 BPM from 100 ms
        lines += hostrun.note(100 + 500 * k, n, 100, 200)
    lines += ["2100 midi 0xB0 63 0", "2150 midi 0xB0 60 0"]           # recorded: stop
    bpm, t_fa = 137, 2600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 10)                                # 2.5 bars, Stop mid-bar 3
    t_stop = ps[-1][0] + 5
    t_cont = t_stop + 800
    ps2 = pulses(t_cont + 1, bpm, 24 * 6)
    end = int(ps2[-1][0]) + 30
    lines += [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + [f"{int(t_stop)} midi 0xFC 0 0", f"{int(t_cont)} midi 0xFB 0 0"]
    lines += emit(ps2) + (stalls(t_fa + 100, end - 50, rng, hi=25) if busy else [])
    r = run(f"seq-{busy}", lines, end)
    ons = note_ons(r, t_fa)
    allp = [int(t) for t, _ in ps] + [int(t) for t, _ in ps2]          # the song position's pulses
    want = [(allp[24 * b], (60, 62, 64, 65)[b % 4]) for b in range(16) if 24 * b < len(allp)]
    got = [(t, n) for t, n in ons]
    assert [n for _, n in got] == [n for _, n in want], (got, want)
    late = np.array([t for t, _ in got]) - np.array([t for t, _ in want])
    assert late.min() >= 0 and late.max() <= (27 if busy else 2), late


SYNC_UNITS = [0, 12, 16, 24, 32, 36, 48, 64, 72, 96, 144, 192, 384, 768, 1536]   # tempo.c, 1/96 beat
LFOS = {   # name: (the dump key, the CCs that switch it on and set its SYNC to index s)
    "tremolo": ("twph", lambda s: [(17, band(1, 2)), (18, 64), (20, band(s, 13))]),
    "chorus": ("cpph", lambda s: [(85, 64), (86, 80), (23, band(s, 14))]),
    "phaser": ("phph", lambda s: [(85, 127), (13, 80), (15, band(s, 14))]),
    "filter LFO": ("lfoph", lambda s: [(70, band(1, 6)), (72, 60), (77, band(s, 14))]),
    "vibes motor": ("motph", lambda s: [(79, 0), (47, 127), (51, band(s, 13))]),
}


@pytest.mark.parametrize("name,s", [(n, 9) for n in LFOS] + [("chorus", 11), ("filter LFO", 12), ("vibes motor", 3)])
def test_every_synced_lfo(name, s):
    """each synced LFO (1/4; and 1/2, 1 BAR, 1/16) locked to the beats of a 120 BPM clock with the main loop busy:
    its cycle starting (phase 0) on the beat, a cycle of several beats on beat 1 counted from Start"""
    rng = np.random.default_rng(13)
    key, ccs = LFOS[name]
    bpm, t_fa = 120, 600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 24)
    end = int(ps[-1][0]) + 20
    cyc = SYNC_UNITS[s] / 96 * beat                                  # the cycle (ms)
    times = lock_dumps(t_fa + max(8, 4 * SYNC_UNITS[s] / 96) * beat, end, beat)   # (a cycle's speed moves by 1/8 at
                                                                     # most: half a 1-bar cycle takes 4 bars)
    lines = [hostrun.cc(20, c, v) for c, v in ccs(s)] + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps)
    lines += stalls(400, end - 50, rng, hi=20)
    r = run(f"lfo-{key}-{s}", lines, end, dumps=times)
    for t in times:
        st = r.state(t)
        ph = int(st[key]) / 2 ** 32
        tm = int(st["wavn"]) * 1000.0 / FS
        want = ((tm - ps[0][0]) / cyc) % 1.0
        err = ((ph - want + 0.5) % 1.0 - 0.5) * cyc / beat         # in beats
        assert abs(err) < 0.05, f"{name} SYNC {s} at {t} ms: {err * 100:+.1f} % of a beat off"


@pytest.mark.parametrize("kind", [1, 2, 3])                          # DIG ANA TAPE
def test_delay_follows(kind):
    """the delay SYNC 1/4 on every EFFECT under a 137 BPM clock: its time is the clock's beat (0.1 %), and an echo
    of a short note comes one beat after it in the sound"""
    bpm, t_fa = 137, 600
    beat = 60000.0 / bpm
    ps = pulses(t_fa + 1, bpm, 24 * 16)
    end = int(ps[-1][0]) + 20
    t_note = int(ps[24 * 12][0])                                     # (TAPE's speed takes some beats to settle, as a tape's)
    lines = [hostrun.cc(20, 88, band(kind, 3)), hostrun.cc(20, 89, 127), hostrun.cc(20, 25, band(9, 13)),
             hostrun.cc(20, 26, 40)] + hostrun.note(t_note, 72, 120, 60) + ["600 midi 0xFA 0 0"] + emit(ps)
    r = run(f"delay-{kind}", lines, end, dumps=[t_note - 5])
    assert abs(int(r.state(t_note - 5)["dlysync"]) / (FS * 60 / bpm) - 1) < 0.001
    env = hostrun.env_rms(r.L + r.R, 44)                             # 1 ms frames
    a = env[t_note:t_note + 40].max()
    seg = env[int(t_note + beat - 60):int(t_note + beat + 60)]
    x = env[t_note - 10:t_note + 120]                                # the note, against the sound a beat later
    lags = np.arange(int(beat) - 40, int(beat) + 40)
    c = [np.dot(x - x.mean(), env[t_note - 10 + k:t_note + 120 + k] - env[t_note - 10 + k:t_note + 120 + k].mean())
         for k in lags]
    lag = lags[int(np.argmax(c))]
    assert abs(lag / beat - 1) < 0.01, (lag, beat)
    assert seg.max() > 0.1 * a


@pytest.mark.parametrize("bpm,locks", [(40, True), (240, True), (36, False), (250, False)])
def test_range(bpm, locks):
    """40 and 240 BPM lock (the BPM card's range); outside, nothing changes (120.0 goes on)"""
    ps = pulses(601, bpm, 24 * 6)
    end = int(ps[-1][0]) + 5
    r = run(f"range-{bpm}", ["600 midi 0xFA 0 0"] + emit(ps), end + 10, dumps=[end])
    b10, smp, lock, _ = tempo_at(r, end)
    if locks:
        assert lock == 1 and abs(smp / (FS * 60 / bpm) - 1) < 0.002 and abs(b10 - bpm * 10) <= 1, (b10, smp)
    else:
        assert lock == 0 and b10 == 1200 and smp == 22050


def test_two_clocks():
    """USB at 120 and the TRS jack at 90 at once: the first one (USB) is followed, the jack's ignored (its Start
    too); USB stops, 500 ms later the jack's clock takes over"""
    a = pulses(601, 120, 24 * 8, "midi")
    b = pulses(700, 90, 24 * 16, "trs")
    t_usb_end = a[-1][0]
    end = int(b[-1][0]) + 5
    lines = ["600 midi 0xFA 0 0", "690 trs 0xFA 0 0"] + emit(a) + emit(b)
    r = run("two", lines, end + 10, dumps=[int(t_usb_end) - 5, end])
    assert tempo_at(r, int(t_usb_end) - 5)[0] == 1200
    assert tempo_at(r, end)[0] == 900 and tempo_at(r, end)[2] == 1


def test_int_lfo_on_the_taps():
    """CLOCK EXT without a clock (and so CLOCK INT): the synced LFOs lock to the FM-1's own beats, which a tap
    re-phases (PLAY three times at 100 BPM: the tremolo's peak on the taps' beats)"""
    taps = [3000, 3600, 4200]
    end = 12000
    beat = 600.0
    times = lock_dumps(4200 + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + [f"{t} btn 12" for t in taps]
    r = run("int-tap", lines, end, dumps=times)
    assert tempo_at(r, times[-1])[0] == 1000
    check_lock(r, times, 4200 + 1, beat)                         # (the tap's ms, mid-ms as the grid takes it)
def test_clock_tempo_not_saved_and_tap():
    """the clock's tempo marks nothing to save (no settings write, "dirty" 0) and redraws only when the BPM shown
    changes; PLAY taps while a clock runs change nothing (the clock leads: no jump, nothing to save)"""
    ps = pulses(601, 100, 24 * 20)
    end = int(ps[-1][0]) + 10
    taps = [f"{t} btn 12" for t in (5000, 5300, 5600)]                 # a tap tempo of 200 BPM
    r = run("tap", ["600 midi 0xFA 0 0"] + emit(ps) + taps, end + 3000, dumps=[4900, 5601, 5700, end])
    assert r.state(4900)["dirty"] == "0" and tempo_at(r, 4900)[0] == 1000
    assert tempo_at(r, 5601)[0] == 1000 and tempo_at(r, 5700)[0] == 1000 and tempo_at(r, end)[0] == 1000
    assert r.state(5700)["dirty"] == "0" and not r.saves


# ---- the review of the first version (each fails on a76ee88)

def seq_pattern():
    """a 1-bar pattern recorded at 120 BPM without a clock: C D E F on the 4 beats (QUANT 1/16)"""
    ls = ["20 midi 0xB0 61 1", "20 midi 0xB0 62 3", "100 midi 0xB0 63 127", "100 midi 0xB0 60 127"]
    for k, n in enumerate((60, 62, 64, 65)):
        ls += hostrun.note(100 + 500 * k, n, 100, 200)
    return ls + ["2100 midi 0xB0 63 0", "2150 midi 0xB0 60 0"]


def seq_check(r, t0, allp, beats, slack):
    """the pattern's notes on their song position's pulses: [(beat, note)] against [(ms, note)] sent"""
    got = note_ons(r, t0)
    want = [(allp[24 * b], (60, 62, 64, 65)[b % 4]) for b in beats]
    assert [n for _, n in got] == [n for _, n in want], (got, want)
    late = np.array([t for t, _ in got]) - np.array([t for t, _ in want])
    assert late.min() >= 0 and late.max() <= slack, late


def test_q8_wrap():
    """the uptime crossing 2^24 ms (4.66 hours: the Q8 times wrap) while the clock runs: the lock holds"""
    t0 = (1 << 24) - 4000
    bpm, beat = 120, 500.0
    ps = pulses(601, bpm, 24 * 20)
    end = int(ps[-1][0]) + 20
    times = lock_dumps(601 + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps) + [f"{t} dump" for t in times]
    r = hostrun.run("clock-wrap", lines, end_ms=end, env={"FRODHES_MS0": str(t0)})
    r.dumps = {k - t0: v for k, v in r.dumps.items()}
    assert tempo_at(r, times[-1])[2] == 1 and tempo_at(r, times[-1])[0] == 1200
    check_lock(r, times, ps[0][0], beat)


def test_render_load():
    """the FM-1's render holds the main loop 2-5 ms every block: the lock still follows (the blocks' own snapshot)"""
    bpm, beat = 120, 500.0
    ps = pulses(601, bpm, 24 * 16)
    end = int(ps[-1][0]) + 20
    times = lock_dumps(601 + 8 * beat, end, beat)
    lines = setup() + chord(300, end) + ["10 renderload 3", "600 midi 0xFA 0 0"] + emit(ps)
    r = run("rload", lines, end, dumps=times)
    check_lock(r, times, ps[0][0], beat)


def test_one_late_pulse():
    """one pulse 45 ms late (the interrupts off for a flash erase), the next ones with it: the tempo does not move"""
    bpm = 120
    ps = pulses(601, bpm, 24 * 16)
    late = 24 * 8 + 5
    arr = {i: t for i, (t, _) in enumerate(ps)}
    for i in range(late, late + 3):
        arr[i] = ps[late][0] + 45
    lines = ["600 midi 0xFA 0 0"] + [f"{int(arr[i])} midi 0xF8 0 0" for i in range(len(ps))]
    end = int(ps[-1][0]) + 10
    times = sorted(set([int(t) for t in np.arange(601 + 4 * 500, end - 10, 50)] +
                       [int(t) for t in np.arange(ps[late][0] - 20, ps[late][0] + 1000, 3)]))
    r = run("late", lines, end + 10, dumps=times)
    smp = np.array([tempo_at(r, t)[1] for t in times])
    assert np.all(np.abs(smp / 22050 - 1) < 0.003), (smp.min(), smp.max())
    assert all(tempo_at(r, t)[0] == 1200 for t in times)


def test_jitter_does_not_move_the_delay():
    """TRS pulses 0-5 ms late at random: the delay time is published at most once a beat and stays within 0.2 %"""
    rng = np.random.default_rng(21)
    bpm = 137
    beat = 60000 / bpm
    ps = pulses(601, bpm, 24 * 16, "trs")
    end = int(ps[-1][0]) + 10
    times = [int(t) for t in np.arange(601 + 4 * beat, end - 10, 50)]
    lines = [hostrun.cc(20, CC_DL_SW, band(1, 3)), hostrun.cc(20, CC_DL_SYNC, band(9, 13)), "600 trs 0xFA 0 0"]
    lines += emit(ps, lambda t: t + rng.uniform(0, 5))
    r = run("dlyjit", lines, end + 10, dumps=times)
    d = np.array([tempo_at(r, t)[3] for t in times])
    exact = FS * 60 / bpm
    assert np.all(np.abs(d / exact - 1) < 0.002), (d.min(), d.max(), exact)
    assert len(set(d)) <= 12, sorted(set(d))                       # (12 beats: once a beat at most)


def test_seq_main_loop_held_and_bars_changed():
    """the sequencer under the clock: the main loop held 900 ms (2 notes due meanwhile) and BARS 1 -> 2 while it
    plays: never a burst of notes at once (the held-up ones are left out, the others on their pulses)"""
    bpm, t_fa = 137, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 16)
    end = int(ps[-1][0]) + 30
    t_hold = int(ps[24 * 4 + 6][0])
    lines = seq_pattern() + [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + [f"{t_hold} stall 900",
             f"{int(ps[24 * 10 + 3][0])} midi 0xB0 61 2"]
    r = run("seqhold", lines, end)
    ts = [t for t, _ in note_ons(r, t_fa)]
    assert len(ts) == len(set(ts)), ts                             # no two notes in the same ms
    assert np.min(np.diff(ts)) > 200, np.diff(ts)
    beats = [int(ps[24 * b][0]) for b in range(16)]
    t_bars = ps[24 * 10 + 3][0]
    after = [t for t in ts if t_hold + 902 < t < ps[-1][0]]       # (the last pulse: then on its own)
    assert [t for t in after if t < t_bars] == [b for b in beats if t_hold + 902 < b < t_bars], after   # each beat
    assert all(min(abs(t - b) for b in beats) <= 2 for t in after), after   # (BARS 2: bar 2 is empty, on the beats)


def test_seq_through_a_clock_gap():
    """400 ms of the clock lost on the way (the line, the computer) and then on: the pattern holds through the gap and
    goes on with the song position (the gap's pulses counted from the tempo), not shifted for good"""
    bpm, t_fa = 137, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 12)
    t_gap = ps[24 * 6 + 3][0]
    sent = [(t, src) for t, src in ps if not (t_gap < t < t_gap + 400)]
    end = int(ps[-1][0]) + 30
    r = run("seqgap", seq_pattern() + [f"{t_fa} midi 0xFA 0 0"] + emit(sent), end)
    allp = [int(t) for t, _ in ps]
    got = note_ons(r, int(t_gap) + 401)
    want = [(allp[24 * b], (60, 62, 64, 65)[b % 4]) for b in range(12) if allp[24 * b] > t_gap + 401]
    assert [n for _, n in got] == [n for _, n in want], (got, want)
    late = np.array([t for t, _ in got]) - np.array([t for t, _ in want])
    assert late.min() >= 0 and late.max() <= 2, late
    assert not [t for t, _ in note_ons(r, int(t_gap)) if t < t_gap + 401 and t > t_gap + 5], "no notes in the gap"


def test_song_position():
    """Start, 4 beats, Stop, Song Position 8 (16ths: beat 2 of bar 1), Continue: the pattern goes on at beat 2"""
    bpm, t_fa = 137, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 4)
    t_stop = ps[-1][0] + 5
    ps2 = pulses(t_stop + 600, bpm, 24 * 3)
    end = int(ps2[-1][0]) + 30
    lines = seq_pattern() + [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + [f"{int(t_stop)} midi 0xFC 0 0",
             f"{int(t_stop) + 200} midi 0xF2 8 0", f"{int(ps2[0][0]) - 1} midi 0xFB 0 0"] + emit(ps2)
    r = run("spp", lines, end)
    got = [n for _, n in note_ons(r, int(t_stop) + 100)]
    assert got == [64, 65, 60], got
    t1 = note_ons(r, int(t_stop) + 100)[0][0]
    assert 0 <= t1 - int(ps2[0][0]) <= 2


def test_seq_ring_full():
    """the main loop held 1.5 s while the clock runs (72 pulses: the 64-entry ring drops some): the pulses are
    still counted, the pattern goes on with the song position"""
    bpm, t_fa = 120, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 12)
    end = int(ps[-1][0]) + 30
    t_hold = int(ps[24 * 2 + 3][0])
    r = run("seqring", seq_pattern() + [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + [f"{t_hold} stall 1500"], end)
    allp = [int(t) for t, _ in ps]
    seq_check(r, t_hold + 1600, allp, range(6, 12), 2)


def test_edge_tempo_redraws():
    """a clock at 240.5 BPM (inside the lock's +-0.4 %): the BPM shown is 240.0, drawn once, not on every pulse;
    the setting stays 120.0"""
    ps = pulses(601, 240.5, 24 * 16)
    end = int(ps[-1][0]) + 10
    r = run("edge", ["600 midi 0xFA 0 0"] + emit(ps), end + 10, dumps=[end])
    s = r.state(end)
    assert s["bpm10"] == "2400" and s["setbpm"] == "1200" and int(s["bpmmarks"]) <= 2, s["bpmmarks"]


def test_arp_clock_to_internal():
    """the arpeggiator (1/16) on a clock that stops: the steps go on at the clock's last tempo, without an extra
    note in between"""
    bpm = 120
    ps = pulses(601, bpm, 24 * 6)
    end = int(ps[-1][0]) + 1500
    lines = ["20 midi 0xB0 52 1", "20 midi 0xB0 53 3"] + [x for n in (60, 64, 67) for x in hostrun.note(300, n, 100, end - 400)]
    lines += ["600 midi 0xFA 0 0"] + emit(ps)
    r = run("arpstop", lines, end)
    ts = np.array([t for t, _ in note_ons(r, 601)])
    step = 125
    assert np.min(np.diff(ts)) >= 0.9 * step, np.diff(ts)


# ---- the second review (6ca0042) and the FM-1's findings on it

def test_seq_late_notes_at_240():
    """240 BPM, the render holding the main loop 2 ms a block and draws of 40 ms now and then: every note of the
    pattern plays (late at most by the hold), none is dropped as a jump"""
    rng = np.random.default_rng(4)
    bpm, t_fa = 240, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 16)
    end = int(ps[-1][0]) + 30
    lines = seq_pattern() + ["2300 renderload 2", f"{t_fa} midi 0xFA 0 0"] + emit(ps)
    lines += [f"{int(t)} stall 40" for t in np.arange(t_fa + 300, end - 100, 173 + rng.uniform(0, 50))]
    r = run("seq240", lines, end)
    got = note_ons(r, t_fa)
    want = [int(ps[24 * b][0]) for b in range(16)]
    assert len(got) == 16, (len(got), got)
    late = np.array([t for t, _ in got]) - np.array(want)
    assert late.min() >= 0 and late.max() <= 70, late          # (a 40 ms draw with the renders: ~60 ms)


def test_count_wrap_17_dropped():
    """the main loop held 1.7 s at 120 BPM: 64 pulses queued, 17 dropped (the 4-bit count wraps): the song position
    still comes out right"""
    bpm, t_fa = 120, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 14)
    end = int(ps[-1][0]) + 30
    t_hold = int(ps[24 * 2 + 3][0])
    r = run("seqwrap", seq_pattern() + [f"{t_fa} midi 0xFA 0 0"] + emit(ps) + [f"{t_hold} stall 1690"], end)
    seq_check(r, t_hold + 1800, [int(t) for t, _ in ps], [b for b in range(14) if ps[24 * b][0] > t_hold + 1800], 2)


def arp_intervals(r, t0, t1):
    ts = np.array([t for t, _ in note_ons(r, t0) if t < t1])
    return np.diff(ts)


def test_int_and_back_counts():
    """CLOCK EXT -> INT -> EXT within half a second while the clock runs: the pulses that came meanwhile do not come
    back as fake ones (the arpeggiator's 1/16 steps stay 6 pulses apart once the clock is followed again)"""
    ps = pulses(601, 120, 24 * 16)
    end = int(ps[-1][0]) + 10
    t = int(ps[24 * 4][0]) + 3
    lines = ["20 midi 0xB0 52 1", "20 midi 0xB0 53 3"] + [x for n in (60, 64, 67) for x in hostrun.note(300, n, 100, end - 400)]
    lines += ["600 midi 0xFA 0 0"] + emit(ps) + [f"{t} btn 7", f"{t + 20} btn 7", f"{t + 40} btn 7",
                                                 f"{t + 60} turn 4 -1", f"{t + 330} turn 4 1"]
    r = run("intback", lines, end)
    grid = np.array([ps[6 * m][0] for m in range(len(ps) // 6)])
    ts = [x for x, _ in note_ons(r, t + 930) if x < end - 20]
    assert len(ts) > 10 and all(np.min(np.abs(grid - x)) <= 2 for x in ts), ts   # (on Start's 1/16 grid still)


def test_spp_while_clocked_and_stopped():
    """Stop, the computer keeps clocking, a Song Position: the arpeggiator (on the clock) goes on without a stray
    note; Continue then plays the pattern from there"""
    ps = pulses(601, 120, 24 * 12)
    end = int(ps[-1][0]) + 10
    lines = ["20 midi 0xB0 52 1", "20 midi 0xB0 53 3"] + [x for n in (60, 64, 67) for x in hostrun.note(300, n, 100, end - 400)]
    lines += ["600 midi 0xFA 0 0"] + emit(ps) + [f"{int(ps[24 * 4][0]) + 3} midi 0xFC 0 0",
                                                 f"{int(ps[24 * 5][0]) + 7} midi 0xF2 13 0"]
    r = run("sppclk", lines, end)
    iv = arp_intervals(r, 700, end - 20)
    assert np.all(np.abs(iv - 125) <= 2), iv


def test_arp_first_note_after_clock_change():
    """the arpeggiator on with no chord while a clock starts: the next chord's first note plays at once"""
    ps = pulses(601, 120, 24 * 8)
    end = int(ps[-1][0]) + 10
    t_ch = int(ps[24 * 3 + 2][0]) + 3                               # (between two of its steps)
    lines = ["20 midi 0xB0 52 1", "20 midi 0xB0 53 3", "600 midi 0xFA 0 0"] + emit(ps)
    lines += [x for n in (60, 64, 67) for x in hostrun.note(t_ch, n, 100, 600)]
    r = run("arpfirst", lines, end)
    ons = note_ons(r, t_ch)
    assert ons and ons[0][0] - t_ch <= 2, ons[:2]


def test_int_bpm_knob_does_not_wobble():
    """CLOCK INT, the tremolo at 1/16: the BPM knob turned a detent at a time: the LFO's rate stays within 2 % of
    its nominal (the beat goes on from where it is at each new tempo)"""
    lines = setup() + [hostrun.cc(20, CC_TW_SYNC, band(3, 13))] + chord(300, 6000) + ["1000 btn 7"]
    lines += [f"{t} turn 2 1" for t in range(1500, 4000, 120)]
    times = list(range(1500, 4500, 20))
    r = run("bpmknob", lines, 5000, dumps=times)
    dev = []
    for t in times:
        inc, nom = map(int, r.state(t)["twinc"].split())
        if nom:
            dev.append(abs(inc / nom - 1))
    assert max(dev) < 0.02, max(dev)


def test_retrig_gives_back_the_rate():
    """the filter LFO synced under a clock, then RETRIG on (the keys lead it): its rate is the nominal one again"""
    ps = pulses(601, 120, 24 * 10)
    end = int(ps[-1][0]) + 10
    lines = [hostrun.cc(20, 70, band(1, 6)), hostrun.cc(20, 72, 60), hostrun.cc(20, 77, band(9, 14))] + chord(300, end)
    lines += ["600 midi 0xFA 0 0"] + emit(ps) + [hostrun.cc(int(ps[24 * 6][0]), 78, 127)]
    r = run("retrig", lines, end, dumps=[end - 5])
    inc, nom = map(int, r.state(end - 5)["lfoinc"].split())
    assert inc == nom, (inc, nom)


def test_publishes_after_a_change():
    """a tempo jump 120 -> 90: the new tempo is published a few times while it settles, not on every pulse"""
    ps, t = [], 601.0
    for i in range(24 * 8):
        ps.append((t, "midi")); t += 60000 / 120 / 24
    t_j = t
    for i in range(24 * 8):
        ps.append((t, "midi")); t += 60000 / 90 / 24
    end = int(t) + 10
    r = run("pubs", ["600 midi 0xFA 0 0"] + emit(ps), end, dumps=[int(t_j) - 5, int(t_j + 1500)])
    n = int(r.state(int(t_j + 1500))["pubs"]) - int(r.state(int(t_j) - 5)["pubs"])
    assert 1 <= n <= 5, n


def test_ext_to_int_gives_back_the_setting():
    """a clock at 100 BPM, then CLOCK INT on the MIDI page: the BPM shown and the tempo are the setting's 120.0"""
    ps = pulses(601, 100, 24 * 8)
    t = int(ps[-1][0]) + 300
    lines = ["600 midi 0xFA 0 0"] + emit(ps) + [f"{t} btn 7", f"{t + 50} btn 7", f"{t + 100} btn 7", f"{t + 200} turn 4 -1"]
    r = run("toint", lines, t + 600, dumps=[t - 10, t + 400])
    assert tempo_at(r, t - 10)[0] == 1000
    b10, smp, _, _ = tempo_at(r, t + 400)
    assert b10 == 1200 and smp == 22050, (b10, smp)


def test_expect_sound_under_the_lock():
    """the host's "expect sound" (the ISR runs what the panel says) holds while the phase lock corrects the rates"""
    ps = pulses(601, 120, 24 * 8)
    end = int(ps[-1][0]) + 10
    lines = setup() + chord(300, end) + ["600 midi 0xFA 0 0"] + emit(ps) + [f"{t} expect sound" for t in (3000, 4000)]
    run("expsound", lines, end)


def test_out_of_range_holds():
    """12 beats at 120, then 4 beats at 30 BPM and 8 at 400 BPM without a Start (hil_clock's range take): the delay
    stays at 500 ms all along (on 6ca0042 the FM-1 echoed at 750 and 552 ms)"""
    ps, t = [], 601.0
    for i in range(24 * 12):
        ps.append((t, "midi")); t += 60000 / 120 / 24
    t1 = t
    for i in range(24 * 4):
        ps.append((t, "midi")); t += 60000 / 30 / 24
    for i in range(24 * 8):
        ps.append((t, "midi")); t += 60000 / 400 / 24
    end = int(t) + 10
    times = [int(x) for x in np.arange(t1, end - 5, 25)]
    lines = [hostrun.cc(20, CC_DL_SW, band(1, 3)), hostrun.cc(20, CC_DL_SYNC, band(12, 13)), "600 midi 0xFA 0 0"] + emit(ps)
    r = run("oor", lines, end, dumps=times)
    d = [tempo_at(r, x)[3] for x in times]
    assert all(abs(v / 44100 - 2.0) < 0.002 for v in d), sorted(set(d))   # (1/1 = 4 beats at 120: 2 s)


def hil_take(t0, bpm, change=None, restrike=True, reset=False):
    """hil_clock's LFO take from t0 (ms): a note, the chord, a Start, 20 beats at bpm [, 12 at change[0], Stop, a
    pause of 1.37 beats, Start, 16 at change[0]], Stop; the chord struck again at beats 7.5 and 15.5 (12 ms gap).
    Returns (lines, [(segment's first pulse ms, its beat ms, its pulses)], end ms)"""
    ls = hostrun.note(t0 + 300, 48, 110, 50) + [f"{t0 + 600 + k} midi 0x90 {n} 100" for k, n in enumerate((48, 55, 60, 64))]
    if reset:
        ls.append(f"{t0 + 550} midi 0xB0 10 127")
    t = t0 + 1000.0
    segs, allp = [], []
    ls.append(f"{int(t - 1)} midi 0xFA 0 0")
    def run_(bpm_, beats):
        nonlocal t
        p = 60000.0 / bpm_ / 24
        ps_ = [t + k * p for k in range(24 * beats)]
        t += 24 * beats * p
        return ps_
    a = run_(bpm, 20)
    segs.append((a[0], 60000.0 / bpm, a))
    allp += a
    if change:
        b = run_(change, 12)
        allp += b
        ls.append(f"{int(t)} midi 0xFC 0 0")
        t += 1.37 * 60000.0 / change
        ls.append(f"{int(t - 1)} midi 0xFA 0 0")
        c = run_(change, 16)
        segs.append((c[0], 60000.0 / change, c))
        allp += c
    ls.append(f"{int(t)} midi 0xFC 0 0")
    ls += [f"{int(x)} midi 0xF8 0 0" for x in allp]
    if restrike:
        for b in (7.5, 15.5):
            x = segs[0][0] + b * segs[0][1]
            ls += [f"{int(x - 12)} midi 0x80 {n} 0" for n in (48, 55, 60, 64)]
            ls += [f"{int(x) + k} midi 0x90 {n} 100" for k, n in enumerate((48, 55, 60, 64))]
    ls += [f"{int(t) + 50} midi 0x80 {n} 0" for n in (48, 55, 60, 64)]
    return ls, segs, int(t) + 1000


@pytest.mark.parametrize("reset", [False, True])
def test_hil_takes_in_a_row(reset):
    """hil_clock's tremolo takes one after another (90, then 120 with a change to 100, a Stop, a pause and a Start),
    optionally with the developer reset (CC 10) before each: the LFO is on the clock's beats in every segment, 2
    bars after each Start (on 73cfda0 the FM-1 showed +0.16 and -0.42 beat after the Starts)"""
    lines = setup()
    l1, s1, e1 = hil_take(0, 90, reset=reset)
    l2, s2, e2 = hil_take(e1, 120, change=100, reset=reset)
    lines += l1 + l2
    times, chk = [], []
    for first, beat, ps_ in s1 + s2:
        ts = lock_dumps(first + 8 * beat, ps_[-1], beat)
        times += ts
        chk.append((ts, first, beat))
    r = run(f"hiltakes-{reset}", lines, e2, dumps=times)
    for ts, first, beat in chk:
        check_lock(r, ts, first, beat)


def test_hil_seq_take():
    """hil_clock's sequencer take exactly: a 1-bar pattern step-recorded (8 sixteenths, QUANT 1/16), Start, 20 beats
    at 120, 12 at 100, Stop, 1.37 beats without a clock, Continue, 16 at 100: every note on its pulse (the song
    position over the Stop's pause neither gains nor loses pulses), none extra"""
    notes = (60, 67, 72, 64, 60, 67, 72, 64)
    ls = ["10 midi 0xB0 60 0", "20 midi 0xB0 43 127", "30 midi 0xB0 61 1", "40 midi 0xB0 62 3", "50 midi 0xB0 46 50",
          "100 midi 0xB0 60 127", "130 midi 0xB0 60 0", "280 midi 0xB0 63 127"]
    t = 400
    for n in notes:
        ls += [f"{t} midi 0x90 {n} 100", f"{t + 80} midi 0x80 {n} 0"]
        t += 160
    ls.append(f"{t + 100} midi 0xB0 63 0")
    t0 = 3000.0
    ls.append(f"{int(t0 - 1)} midi 0xFA 0 0")
    allp, t = [], t0
    for bpm, beats in ((120, 20), (100, 12)):
        p = 60000.0 / bpm / 24
        allp += [t + k * p for k in range(24 * beats)]
        t += 24 * beats * p
    ls.append(f"{int(t)} midi 0xFC 0 0")
    t += 1.37 * 600.0
    ls.append(f"{int(t - 1)} midi 0xFB 0 0")
    p = 25.0
    allp += [t + k * p for k in range(24 * 16)]
    t_end = t + 24 * 16 * p
    ls += [f"{int(x)} midi 0xF8 0 0" for x in allp] + [f"{int(t_end)} midi 0xFC 0 0"]
    r = run("hilseq", ls, int(t_end) + 300)
    got = note_ons(r, int(t0) - 5)
    want = [(int(allp[6 * s]), notes[s % 16]) for s in range(len(allp) // 6) if s % 16 < 8]
    assert [n for _, n in got] == [n for _, n in want], (len(got), len(want), got[:4], want[:4])
    late = np.array([a for a, _ in got]) - np.array([a for a, _ in want])
    assert late.min() >= 0 and late.max() <= 2, late



def test_stop_pause_clock_again_adds_nothing():
    """Stop, 600 ms without a clock, then the clock again without a Start (hil_clock's restore take, a computer that
    clocks while stopped): no pulses are made up for the pause (the beat count goes on by the pulses sent)"""
    ps = pulses(601, 120, 24 * 4)
    t1 = int(ps[-1][0]) + 20
    ps2 = pulses(t1 + 600, 120, 24 * 2)
    end = int(ps2[-1][0]) + 10
    r = run("stoppause", ["600 midi 0xFA 0 0"] + emit(ps) + [f"{t1} midi 0xFC 0 0"] + emit(ps2), end + 10, dumps=[end])
    tick, sp, k, k0 = map(int, r.state(end)["sqtick"].split())
    assert k == 24 * 6 and sp == 24 * 4, (k, sp)

def test_seq_start_with_the_main_loop_held():
    """the main loop held 150 ms right after a Start (a flash write, a screen draw): bar 1's first note still plays
    (late), none of the pattern is lost (the release's hil_clock: bar 1 missing, 23 / 24 notes)"""
    bpm, t_fa = 120, 2600
    ps = pulses(t_fa + 1, bpm, 24 * 8)
    end = int(ps[-1][0]) + 30
    lines = seq_pattern() + [f"{t_fa} midi 0xFA 0 0", f"{t_fa} stall 150"] + emit(ps)
    r = run("seqstartheld", lines, end)
    got = note_ons(r, t_fa)
    want = [(int(ps[24 * b][0]), (60, 62, 64, 65)[b % 4]) for b in range(8)]
    assert [n for _, n in got] == [n for _, n in want], (got, want)
    assert got[0][0] - want[0][0] <= 160 and all(0 <= a - b <= 2 for (a, _), (b, _) in zip(got[1:], want[1:])), got


def test_pattern_save_never_holds_the_main_loop():
    """the pattern's auto save (1.5 s after the step recording, silent) runs beside the main loop: a clock's Start and
    a note sent during it are taken at once (the release's hil_clock sequencer take: the save held the loop ~100 ms,
    its reference note 52-98 ms late; the host's erase takes HOST_SAVE_MS, as the settings' in tests/host.c)"""
    lines = seq_pattern()                                           # recorded until 2100, stopped at 2150
    r0 = run("seqsave-probe", lines, 6000)
    t_save = [int(t) for t in re.findall(r"^\s*(\d+) ms  pattern . erased", r0.out, re.M)][0]
    t_n = t_save + 30
    lines2 = lines + hostrun.note(t_n, 72, 100, 100) + [f"{t_n + 2} expect state pitches 1"]
    run("seqsave", lines2, t_save + 400)


def test_auto_saves_one_at_a_time():
    """the setup's auto save (HOST_SAVE_MS beside the loop) runs when the pattern's becomes due: the pattern's waits
    for it (neither busy-finishes the other in the main loop), a note sent meanwhile is taken at once, both are
    written"""
    lines = seq_pattern()
    r0 = run("saves-probe", lines, 6000)
    t_pat = [int(t) for t in re.findall(r"^\s*(\d+) ms  pattern . erased", r0.out, re.M)][0]
    found = None
    for t_cc in range(t_pat - 2400, t_pat - 1900, 25):              # a setup change whose save overlaps the pattern's
        lines2 = lines + [hostrun.cc(t_cc, 46, 60)]                  # (SWING: the setup)
        r1 = run("saves-probe2", lines2, 6000)
        t_set = [int(t) for t in re.findall(r"^\s*(\d+) ms  settings saved", r1.out, re.M)]
        t_pat2 = [int(t) for t in re.findall(r"^\s*(\d+) ms  pattern . erased", r1.out, re.M)]
        if t_set and t_set[-1] - 100 < t_pat < t_set[-1]:
            found = (lines2, t_set[-1], t_pat2)
            break
    assert found, "no overlap found"
    lines2, t_set, t_pat2 = found
    assert t_pat2 and t_pat2[0] >= t_set, (t_set, t_pat2)          # the pattern's waited for the setup's
    t_n = t_set - 20
    run("saves", lines2 + hostrun.note(t_n, 72, 100, 100) + [f"{t_n + 2} expect state pitches 1"], 6000)


# ---- the validation review of 242dcf6

def test_late_pass_ends_its_short_notes():
    """the main loop held 60 ms over a 6-tick note (120 BPM: 31 ms): the note starts late and ends with the next pass,
    not a loop later (a hung note)"""
    ls = ["20 midi 0xB0 61 1", "20 midi 0xB0 62 0", "100 midi 0xB0 63 127", "100 midi 0xB0 60 127"]
    ls += [f"{600} midi 0x90 67 100", f"{605} midi 0x80 67 0"]           # one very short note: 6 ticks
    ls += ["2100 midi 0xB0 63 0"]
    r0 = run("short-probe", ls, 9000)
    ons = [t for t, s, a, b in r0.midi_out if s == 0x90 and a == 67 and b and t > 2200]
    t_on = ons[0]
    r = run("short", ls + [f"{t_on - 20} stall 60"], 9000)
    ons = [t for t, s, a, b in r.midi_out if s == 0x90 and a == 67 and b and t > t_on - 30]
    offs = [t for t, s, a, b in r.midi_out if (s == 0x80 or (s == 0x90 and b == 0)) and a == 67 and t >= ons[0]]
    assert offs and offs[0] - ons[0] <= 3, (ons[:2], offs[:2])


def test_pattern_save_reads_nothing():
    """the pattern's saves read no flash (their sector is known from the load): the reads stay those of the boot"""
    ls = seq_pattern() + ["4000 dump"] + [hostrun.cc(4500, 46, 60)] + [x for k in range(3) for x in
          ([f"{5000 + 2500 * k} midi 0xB0 43 127"] if k == 1 else [])] + ["12000 dump"]
    r = run("sqreads", ls, 12100)
    assert len(re.findall(r"pattern . erased", r.out)) >= 2, r.out[-400:]
    assert r.state(4000)["sqreads"] == r.state(12000)["sqreads"], (r.state(4000)["sqreads"], r.state(12000)["sqreads"])


def test_pattern_save_failures_retry_slowly():
    """a pattern save that fails is tried again 1.5 s later, 3 times at most until the next edit (not every pass)"""
    r = hostrun.run("clock-sqfail", hostrun.script(seq_pattern()) + ["20000 end"], env={"FRODHES_SQ_FAIL": "1"},
                    check=False)
    ts = [int(t) for t in re.findall(r"^\s*(\d+) ms  pattern erase failed", r.out, re.M)]
    assert 1 <= len(ts) <= 3 and all(b - a >= 1500 for a, b in zip(ts, ts[1:])), ts
