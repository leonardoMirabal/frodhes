#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""stress_max, the cracks test: the worst case played for minutes on the FM-1, recorded on USB and the headphone
jack at once, every glitch counted (private/plans/final/B-cracks-test-8-voices.md). A must-pass of the release
(tools/release.py step 4: every engine, USB at 44.1 and 48 kHz); also `tools/hil_scan.py --only stress_max`.

    tools/hil_crackle.py [--engine rhodes|vibes|marimba|all] [--rate 44100|48000] [--blocksize N] [--source both|usb]
                         [--worst-s 180] [--recipe-s 120] [--quick] [--no-usb-s 60] [--leds] [--jack-dev M6]
                         [--jack-ch 5] [--seed N]
    tools/hil_crackle.py --dry-run      the planned takes, events and time; no MIDI, no audio, no host run
    tools/hil_crackle.py --host         the host build plays in the FM-1's place (USB = its WAV at 44.1 kHz; the jack
                                        simulated from it: 48 kHz on a clock 536 ppm fast, AC coupling, the codec's
                                        low-pass, noise): every detector and gate except the CPU and the USB counters
    tools/hil_crackle.py --self-test    the proof (also in make pytest: tests/py/test_hil_crackle.py): host renders of
                                        the same stimulus, clean, then with synthetic glitches injected (10 of each
                                        kind, KINDS); each must be found by its own detector, and the clean renders give
                                        0 events. A device run does this first and stops if it fails.

Why (ISSUES.md 1): 0.9.4 (and 0.9.3) crack on USB with every voice under the pedal, with or without effects, and no
test saw it: hil_cpu never opened the USB stream nor looked at the audio, hil_scan's stress take is 4 s with moderate
effects, its click detector (the second difference over 6 x the local level + 2000) goes blind on loud chords, and
nobody read uac.underruns. The cause found on the FM-1 (private/plans/final/A-usb-cracks-fix.md): the USB IN ring runs
dry late in a long render and uac_packet() repeats the last frame, at most about once per ~29 ms beat (5 render blocks
against 29 packets), exact at both rates (after the resampler).

The stimulus, per engine (every take starts from every parameter's default; USB VOL FIXED, so USB is the output
before MASTER; the host and the FM-1 get the same events):
  worst      every effect heavy (hil_cpu's HEAVY + reverb pre-delay 127, drive mix 64, reverb decay 127; VIBES its
             motor), the pedal down, chords of every voice (the engine's own count: rhodes.c RH_VOICES / BAR_VOICES)
             every 0.15-0.4 s across bass / middle / high / spread at v100 / v127, the pedal lifted and pressed again
             every 20 s. The odd takes at LEVEL -12 dB (CC 110 0): the limiter stays out, so the click check sees
             everything; the even ones at unity, where the limiter works (its activity is information, see click)
  recipe     the user's: F01-like, no effects, the pedal, notes added one by one across the registers up to every
             voice and three more (steals), let go, again; then the same with chorus, phaser, delay, reverb and drive
             moderate
  no-usb     (FM-1, both sources) the recipe with the USB stream closed: the jack alone (separates the USB load)
  leds       (FM-1, --leds) the recipe under the pedal with DISPLAY LEDS at OFF, PLAYED and ALL (no MIDI path: the
             tool asks for each setting on the FM-1's DISPLAY page)
Takes of at most 60 s (the recorder keeps 120 s). The CPU is asked every 0.5 s (CC 116) and once more after each
take's last note-off and pedal-up, before anything resets (the release blocks).

The detectors (each event: time, source, kind, detail):
  repeat     (USB) the underrun's mark: a run of equal stereo frames between two steps the same way (>= 8 LSB). 4 frames
             or more are an event; 2 or 3 when the wave without the copies is smoother (the AR interpolation residual
             around it 3 dB lower): an underrun inserts its copies and the wave runs on after them, while a flat spot
             of the music's own (a bright partial's turn) leaves a jump when taken out
  stale      (USB) 16 frames exactly equal to 16 frames 16..4096 frames earlier, with something in them (>= 64 LSB peak
             to peak): a block, half a block or a packet played twice; not where the match is seamless: the 8
             frames either side of the exact run repeat those as far back too (within 3 % of the wave: the wave's own
             near-period, a lone note of a whole number of frames or two dying partials meeting their past)
  zero       (both) exact zeros on USB for >= 0.5 ms, the jack under 4 LSB for >= 1 ms, with sound on both sides; on
             the jack also cut in (>= 40 LSB within 0.25 ms of an end: a quiet bass wave can pass 0 that slowly),
             and USB, on the jack's clock, with the sound the jack lost (zero_vs_usb: a spell USB runs through too is
             the music's own, information; no USB there or no fit around it: the event stands)
  clip       (both) USB at full scale (|x| >= 32767), the jack >= 0.999 of full scale. Over the limiter's ceiling
             (24100) is counted as information (the limiter's activity)
  click      (both) one-sample impulses: the AR interpolation residual (order 24, per 4096 frames: each sample from 24
             on either side) over 20 x its local median and >= 40 LSB, whose neighbourhood has the shape an impulse
             leaves in it (the block's interpolator, ncc >= 0.98: steps, corners and bright attacks leave other
             shapes), outside the guards (8 ms before to 20 ms after a note-on or a pedal change and their delay
             echoes: the hammer, the steal fade, the damper are real transients; the plan's 3 / 17 ms plus the host's
             audio running a block ahead of its clock; +5.8 ms on the FM-1 for the event-to-block jitter). Where the
             limiter works (a USB peak >= 22000 in the 50 ms before) the event is 'click-lim': information
  step       (both) a sudden level step: a 20 ms window 12 dB off the one before and staying there (the next two
             within 6 dB), with no attack or pedal change near it and, down, no damper starting in the 300 ms before;
             with a free-running level modulation in the take (the vibraphone's motor, the tremolo, the filter's LFO:
             am_periods) also 12 dB off the level one period before (a trough of it is as low as the last one)
  track      (jack) the jack against USB: the clocks' drift fitted over the take (loud windows voted on a line, the
             first differences too) and taken out (USB warped onto the jack's clock), then per 50 ms window a slip (a
             lag > 3 samples off, ncc >= 0.97 and 0.1 better than the old one, in two windows running: a tone's
             period alone is no slip; judged down to -60 dBFS on USB's scale) or a window whose jack is above -60
             dBFS with an ncc < 0.97 (not where an attack starts); only windows USB covers along the line (the
             take's last ~30 ms of jack have no USB on a 60 s take: the clocks differ ~550 ppm)
  periodic   (information) the USB repeat / stale events' spacing against the ~29.0 ms beat: the underrun's signature
Every guard (the attacks, the dampers, the delay's echoes) is placed on each recording's own clock (clock_fit): the
FM-1's USB stream runs ~540-760 ppm off the computer's clock that sends the events, the jack ~0 (2026-10-24: USB 42 ms
late at 52.7 s), so a single lag left the guards up to 30 ms off by a take's end; on the FM-1 each stream's rate is
measured by the recorder (its ADC times, hil_scan Ring.rate) and the lag is the first sound's (every take starts in
silence; onset_lag's threshold follows the recording's noise: the old fixed 300 LSB missed the first note on the jack,
10-15 dB under USB, and took one 0.2-0.45 s later, which put every jack guard there). Without a measured rate (the
host) the strong onsets fit it.
Proven on the host (the self-test; build/crk evidence in the commit): 0 events on 25 minutes of clean renders (the full
stimulus, 2 seeds, 3 engines); the injected glitches found 10/10 per kind and engine. Found by it on the host on the
way (information, for the planners): the delay's stereo-power send gain steps once per block (fx.c, dly_sg), so its
echoes carry one-sample steps DL_TIME after a block boundary when L and R differ (chorus, phaser, pan): up to ~700 LSB
on VIBES with the moderate effects; they are under this click gate's shape test, so it does not fail on them.
The rig's own health is separate: the recorder's gaps (Ring.cut) and PortAudio's status flags mark a take RIG (it is
re-taken once; twice is a FAIL of the rig, not a firmware verdict).

The gates (all must hold): the firmware's own counters first, read every 0.5 s with tools/hil_must.py's reader (CC 116;
the CC 100 id / 101-102 value items of frodhes.c usb_health) and judged by hil_must.gate_counters: they must answer
(no reply, or a reply without the USB items, is a FAIL: a firmware before the USB ring fix), the stream in the state
the take set up (the recorder switches the FM-1 itself to --rate, Core Audio's change_device_parameters as hil_cpu
--usb, and the FM-1 must report it flowing at that rate before the first take, or the run FAILs there), underruns = missed = overruns = 0, the ring's true low point >= one packet, the whole ISR (CC 108/107)
and the engine's longest block (CC 116/117) <= 4500 us, 0 voices shed; then 0 events of every gated kind on USB and
on the jack; the stimulus took every voice (host: the build's "voices max" line). In the release it is T1's S1
(hil_scan.py --tier must, tools/hil_must.py: TESTS["stress_max"] there, at 48 and 44.1 kHz), where hil_must holds
the USB stream and polls the counters around the whole run and this tool judges the audio. Report:
build/hil/stress_max/<rate>[-bs<N>]/report.txt + report.json, a 1 s WAV
around each of the first 20 events; the host's in build/hil/stress_max/host/.
"""
import argparse
import json
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_scan  # noqa: E402
from hil_scan import CC, DRY, param_defaults  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil", "stress_max")
FS_HOST = 44100
FS_JACK = 48000
ENGINES = ("rhodes", "vibes", "marimba")
CPU_TARGET_US, SHEDDER_US = 4500, 5000
CEIL = 24100                                  # fx.c LIM_T 24000 (+ rounding): information
LIM_ON = 22000                                # a USB peak this high in the last 50 ms: the limiter is working
GUARD = (0.008, 0.020)                        # s before / after a note-on or a pedal change (click): 3 / 17 ms
                                              # (the plan) + the host's audio running up to a block ahead of its clock
DEV_JITTER = 0.0058                           # the FM-1: a MIDI event lands in the next audio block
CLICK_K, CLICK_FLOOR, CLICK_SHAPE = 20.0, 40.0, 0.98
CLICK_LSB = 328                               # the injected click: -40 dBFS, one sample
TAKE_MAX = 60.0
JACK_DRIFT = 536e-6                           # the FM-1's clock against the MOTU (plan 01 section 1)
PRIOR_TOL = 0.03                              # (s) track's drift line within this of the clocks' (clock_fit: <= 4 ms
                                              # off each on the self-test's device timing)
BEAT_S = 0.02901                              # 5 render blocks (5 x 256 / 44100) against 29 packets
# The firmware's own counters (workstream A, frodhes.c usb_health): read with tools/hil_must.py's reader (Dev.query,
# parse_reply, Acc, Poller, gate_counters: CC 116 asks; the answer carries CC 100 id / 101-102 value triples, ids 1
# underruns .. 12 the stream state, the whole ISR on CC 108/107, CC 115 the voices shed), the one copy of that protocol.
# No reply, or a reply without the USB items, is a FAIL (a firmware before the USB ring fix), never a pass.
GATED = ("repeat", "stale", "zero", "clip", "click", "step", "track")

CC = dict(CC, TONE=28, BARK=29, BELL=30, HAMMER=31, DRV_MIX=83, RV_DECAY=92, RV_PREDLY=94, USB_VOL=114)
HEAVY = dict(DRIVE=127, DRV_MIX=64, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127,
             PH_FDBK=127, CP_SW=64, CP_DEPTH=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127,
             RV_DECAY=127, RV_PREDLY=127, FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)   # hil_cpu's + the reverb's
MODERATE = dict(DRIVE=60, CP_SW=64, CP_DEPTH=64, PH_SW=127, PH_DEPTH=64, DL_SW=48, DL_DEPTH=64, DL_TIME=60, REV=80)
REG = dict(bass=[28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48],
           mid=[48, 52, 55, 59, 62, 64, 67, 71, 74, 76, 79, 83],
           high=[72, 76, 79, 83, 86, 88, 91, 95, 98, 100, 103, 107],
           spread=[28, 40, 52, 59, 64, 71, 76, 83, 88, 95, 100, 105])
RECIPE = [36, 60, 84, 43, 67, 91, 48, 72, 96, 55, 79, 41, 65, 89, 52]   # low, middle, high in turn
LEDS = (("OFF", 1), ("PLAYED", 2), ("ALL", 3))                         # ui.c LEDS_N: 0 ROOTS 1 OFF 2 PLAYED 3 ALL


def voices(eng):
    """the firmware's voice count for an engine (rhodes.c RH_VOICES / BAR_VOICES)"""
    src = open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()
    key = "RH_VOICES" if eng == "rhodes" else "BAR_VOICES"
    return int(re.search(r"#define " + key + r" (\d+)u", src).group(1))


# ---------------------------------------------------------------------------------------------------- the stimulus
def engine_ccs(eng, fx):
    """(cc, v) after the defaults: the engine, the effects, USB VOL FIXED; CC 85 before the phaser's CCs"""
    out = [(c, v) for c, v in param_defaults()]
    d = dict(DRY)
    d.update(fx)
    if "CP_SW" in d:
        out.append((CC["CP_SW"], d.pop("CP_SW")))
    out += [(CC[k], v) for k, v in d.items()]
    if eng == "vibes":
        out += [(79, 0), (47, 127 if fx else 0)]          # VIBES, the motor with the effects
    elif eng == "marimba":
        out.append((79, 127))
    out.append((CC["USB_VOL"], 127))
    return out


def worst_events(nv, dur, rng):
    ev = [(0.2, "cc", 64, 127)]
    every = min(20.0, dur / 3)                             # (a short take, the self-test's: three times too; each
    t, k, held, ped = 0.4, 0, [], every                    # chord after it starts from the dampers' quiet: clock_fit's
    while t < dur - 1.6:                                   # marks along the take)
        if t >= ped:                                       # the pedal lifted and pressed again every 20 s
            ev += [(t, "cc", 64, 0), (t + 0.12, "cc", 64, 127)]
            ped += every
            t += 0.2
        notes = REG[("bass", "mid", "high", "spread")[k % 4]][:nv]
        vel = (100, 127)[(k // 4) % 2]
        ev += [(t - 0.005, "off", n, 0) for n in held]    # let go (the pedal holds them)
        ev += [(t, "on", n, vel) for n in notes]
        held, k = notes, k + 1
        t += float(rng.uniform(0.15, 0.4))
    ev += [(t, "off", n, 0) for n in held] + [(t + 0.05, "cc", 64, 0)]
    return ev, t + 1.5


def recipe_events(nv, dur, rng):
    ev, t, r = [], 0.2, 0
    while t < dur - 5.0:
        ev.append((t, "cc", 64, 127))
        notes = [RECIPE[(r * 2 + i) % len(RECIPE)] for i in range(nv + 3)]
        for i, n in enumerate(notes):
            ev.append((t + 0.1 + 0.22 * i, "on", n, (100, 112, 127)[i % 3]))
        t += 0.1 + 0.22 * len(notes) + 1.0
        ev += [(t, "off", n, 0) for n in notes] + [(t + 0.2, "cc", 64, 0)]
        t += 1.0
        r += 1
    return ev, t + 1.5


def takes(eng, worst_s=180.0, recipe_s=120.0, seed=0, nv=None):
    """[(name, ccs, events, dur)] for one engine"""
    nv = nv or voices(eng)
    rng = np.random.default_rng(seed + ENGINES.index(eng))
    out = []

    def split(total, name, fxs, gen):
        n = max(1, int(np.ceil(total / TAKE_MAX)))
        for i in range(n):
            ev, dur = gen(nv, total / n, rng)
            out.append((f"{eng}-{name}{i + 1}", engine_ccs(eng, fxs[i % len(fxs)]), sorted(ev, key=lambda e: e[0]),
                        dur))
    split(worst_s, "worst", [dict(HEAVY, LEVEL=0), HEAVY], worst_events)
    split(recipe_s / 2, "recipe-dry", [{}], recipe_events)
    split(recipe_s / 2, "recipe-fx", [MODERATE], recipe_events)
    return out


def guards(events, ccs=(), releases=False, clock=(0.0, 0.0), echo_rate=0.0):
    """the times (s) that may carry a real transient: every note-on and pedal change (releases: every note-off and
    pedal-up instead, where a damper starts), and with the delay on their echoes (DL_TIME's time, until the feedback
    has them 20 dB down: a hammer's edge repeated by the delay is a real transient too). clock: the events mapped onto
    a recording's clock (map_clock); the echoes then follow DL_TIME on the FM-1's own clock, which USB carries sample
    for sample (echo_rate: the recording's rate against USB's, the jack's; 24 echoes of a long delay x 1500 ppm put
    them 10 ms off when the whole was mapped, 2026-10-24)"""
    if releases:
        t0 = sorted({e[0] for e in events if e[1] == "off" or (e[1] == "cc" and e[2] == 64 and e[3] < 64)})
    else:
        t0 = sorted({e[0] for e in events if e[1] == "on" or (e[1] == "cc" and e[2] == 64)})
    t0 = [float(t) for t in map_clock(t0, clock)] if tuple(clock) != (0.0, 0.0) else t0
    d = dict(param_defaults())
    d.update(dict(ccs))
    if d.get(CC["DL_SW"], 0) and d.get(CC["DL_DEPTH"], 0):
        dt = hil_scan.dly_ms(d.get(CC["DL_TIME"], 85)) / 1000
        fb = min(0.85, d[CC["DL_DEPTH"]] * 180 * d.get(CC["DL_FDBK"], 90) / 128 / 32768)
        reps = 1
        while reps < 24 and fb ** reps >= 0.1:
            reps += 1
        dt *= 1.0 + echo_rate
        t0 = sorted(set(t0 + [t + k * dt for t in t0 for k in range(1, reps + 1)]))
    return np.array(t0)


# ------------------------------------------------------------------------------------------------------ detectors
def _runs(mask):
    """(starts, ends) of the True runs of a bool array (end exclusive)"""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


def _peak(x):
    a = np.abs(np.asarray(x, float))
    return a.max(axis=1) if a.ndim == 2 else a


def det_repeat(q, slope=8, gain=2.0, ctx=256, half=4):
    """frames that repeat the frame before where the wave runs on: [(frame, detail)]; q (n, 2) int. A candidate is a
    run of equal stereo frames between two steps the same way (>= slope LSB, either channel); 4 frames or more are an
    event; 2 or 3 when taking the copies out makes the wave smoother: the AR interpolation residual at it falls >= gain
    x (3 dB) where the steps either side are alike (0.4..1/0.4: a copy keeps the wave's slope), >= 20 dB where they are
    less so (down to 0.15). An underrun
    inserts its copies (the stream comes later by as many frames), so the wave without them runs on; a flat spot of the
    music's own leaves a jump when taken out"""
    q = np.asarray(q, np.int64)
    eq = np.all(q[1:] == q[:-1], axis=1)                  # eq[i]: frame i + 1 == frame i
    s, e = _runs(eq)                                       # run i..j-1 of eq: plateau frames i..j
    out = []
    for p0, p1 in zip(s, e):
        if p0 < ctx or p1 + ctx >= len(q):
            continue
        d0, d1 = q[p0] - q[p0 - 1], q[p1 + 1] - q[p1]
        ok = (np.sign(d0) == np.sign(d1)) & (np.abs(d0) >= slope) & (np.abs(d1) >= slope)
        if not ok.any():
            continue
        ch = int(np.argmax(np.abs(d0) + np.abs(d1)))
        if p1 - p0 >= 3:                                   # 4 equal frames or more between two steps the same way: no
            out.append((int(p0), f"{p1 - p0 + 1} equal frames on a slope ({int(d0[ch]):+d} / {int(d1[ch]):+d})"))
            continue                                       # wave of the music's own holds still that long on a slope
        like = min(abs(d0[ch]), abs(d1[ch])) / max(abs(d0[ch]), abs(d1[ch]))
        if like < 0.15:
            continue                                       # (a copy keeps the slope: the frames either side of it are
        need = gain if like >= 0.4 else 100.0              # the wave's own consecutive steps; -48 / -8 is a turn: steps
        seg = q[p0 - ctx:p1 + ctx + 1, ch].astype(float)   # less alike need 20 dB, a copy in a hammer's turn gives 58)
        cut = np.delete(seg, np.arange(ctx + 1, ctx + 1 + (p1 - p0)))
        ea = interp_residual(seg, order=16, block=len(seg))
        eb = interp_residual(cut, order=16, block=len(cut))
        Ea = float((ea[ctx - half:ctx + (p1 - p0) + half] ** 2).sum())   # (the whole run and its edges)
        Eb = float((eb[ctx - half:ctx + half] ** 2).sum())
        if Ea > need * Eb and Ea > 4 * slope ** 2:
            out.append((int(p0), f"{p1 - p0 + 1} equal frames on a slope ({int(d0[ch]):+d} / {int(d1[ch]):+d}); "
                                 f"the wave without the copies {10 * np.log10(Ea / max(Eb, 1e-9)):.0f} dB smoother"))
    return out


def _exact_run(q, b, lag, w, step=256):
    """[s, e): the whole run of frames around q[b:b + w] exactly equal to those lag earlier"""
    s, e = b, b + w
    while s - lag > 0:
        a = max(lag, s - step)
        ne = np.flatnonzero(np.any(q[a:s] != q[a - lag:s - lag], axis=1))
        if len(ne):
            return a + int(ne[-1]) + 1, _run_end(q, e, lag, step)
        s = a
    return s, _run_end(q, e, lag, step)


def _run_end(q, e, lag, step):
    while e < len(q):
        z = min(len(q), e + step)
        ne = np.flatnonzero(np.any(q[e:z] != q[e - lag:z - lag], axis=1))
        if len(ne):
            return e + int(ne[0])
        e = z
    return e


def _own_period(q, b, lag, w, ctx=8, rel=0.03, run=None):
    """the match at b is seamless: the whole run of frames exactly equal to those lag earlier around it, and the ctx
    frames either side of that run, repeat those lag earlier within 3 % of the wave's peak there (and >= 4 LSB). The
    difference to the wave lag earlier then runs into the match and out of it smoothly: the wave's own near-period
    (a steady tone of a whole number of frames, or two decaying partials whose sum comes back at that lag for a
    moment), not a copy (a copied packet or block starts and ends with a jump in that difference, as big as the wave
    is unlike itself lag earlier; and a seamless copy, were there one, would be inaudible). A lone G2 held (98.0 Hz:
    450 frames a period, -23 dBFS, the period before 1-6 LSB off) matched it exactly for 19 frames on the host,
    2026-10-24; the FM-1's USB at 22.180 s of rhodes-recipe-dry1 (44.1 kHz, build m2, 2026-10-24: C2 and G2 dying
    under the dampers, peak 400 LSB) for 16 frames at lag 517: the difference 4, 3, 3, 1, 1, 1, 1 into the run, -1
    after it, and 21 exact frames at lag 847 in the same 70 ms; the host render of the take has the same partials
    and its own runs (12 frames at lag 1436)"""
    s, e = run or _exact_run(q, b, lag, w)
    if s - ctx - lag < 0 or e + ctx > len(q):
        return False
    tol = max(4.0, rel * float(np.abs(q[s - ctx:e + ctx]).max()))
    d0 = np.abs(q[s - ctx:s] - q[s - ctx - lag:s - lag]).max()
    d1 = np.abs(q[e:e + ctx] - q[e - lag:e + ctx - lag]).max()
    return bool(d0 <= tol and d1 <= tol)


def det_stale(q, w=16, maxlag=4096, p2p=64):
    """16 frames exactly equal to 16 frames 16..maxlag frames earlier, with sound in them: [(frame, detail)]"""
    q = np.asarray(q, np.int64)
    n = len(q) - w
    if n <= 0:
        return []
    v = ((q[:, 0] & 0xFFFF) << 16 | (q[:, 1] & 0xFFFF)).astype(np.uint64)
    rnd = np.random.default_rng(12345).integers(1, 2 ** 62, w, dtype=np.uint64) | np.uint64(1)
    h = np.zeros(n, np.uint64)
    with np.errstate(over="ignore"):
        for k in range(w):
            h += v[k:k + n] * rnd[k]
    from scipy.ndimage import maximum_filter1d, minimum_filter1d
    rng_ = np.zeros(n)
    for c in range(q.shape[1]):
        col = q[:, c].astype(float)
        hi = maximum_filter1d(col, w, origin=-(w // 2))[:n]
        lo = minimum_filter1d(col, w, origin=-(w // 2))[:n]
        rng_ = np.maximum(rng_, hi - lo)
    idx = np.flatnonzero(rng_ >= p2p)
    if not len(idx):
        return []
    o = idx[np.lexsort((idx, h[idx]))]
    hs = h[o]
    same = np.flatnonzero(hs[1:] == hs[:-1])
    hits, runs = {}, {}
    for i in same:
        a, b = int(o[i]), int(o[i + 1])
        lag = b - a
        if not (w <= lag <= maxlag and np.array_equal(q[a:a + w], q[b:b + w])):
            continue
        r = runs.get(lag)                                  # (a run's windows share its verdict)
        if r is None or not r[0] <= b < r[1]:
            s0, e0 = _exact_run(q, b, lag, w)
            r = runs[lag] = (s0, e0, _own_period(q, b, lag, w, run=(s0, e0)))
        if not r[2]:
            hits[b] = min(lag, hits.get(b, lag))
    out, last = [], -10 ** 9
    for b in sorted(hits):
        if b - last > 1024:
            out.append((b, f"16 frames equal to those {hits[b]} frames earlier"))
        last = b
    return out


ZERO_EDGE = 40                                # (the jack) a dropout cuts the wave: >= 40 LSB within 0.25 ms of the gap


def det_zero(x, fs, exact=True, around=200, edge=ZERO_EDGE, smooth=None):
    """runs of exact zeros (USB, >= 0.5 ms) / under 4 LSB (the jack, >= 1 ms) with sound on both sides; on the jack
    also cut in: the wave >= edge LSB within 0.25 ms of one end of the run. A gap cuts the wave where it is (the
    codec's low-pass leaves it 2-3 samples of edge); a quiet low note's wave can pass 0 slowly enough to stay under
    4 LSB for a millisecond (two partials cancelling: the FM-1's jack at 47.03 s of vibes-recipe-dry1, 2026-10-24: 15
    LSB 0.25 ms before the run, 5 after, every second difference <= 3 LSB), and that is no dropout. smooth (a list):
    such runs go there instead (analyse judges them against USB: a gap can fall between two of the wave's own zero
    crossings, rhodes-worst1 at 17.748 s with INFINITE SUSTAIN, 2026-10-25: 4.6 ms gone, 440 LSB of wave in it, under
    40 LSB at both ends)"""
    a = _peak(x)
    zm = a == 0 if exact else a < 4
    min_n = int((0.5 if exact else 1.0) * fs / 1000)
    s, e = _runs(zm)
    out = []
    w, k = int(0.01 * fs), max(1, int(0.00025 * fs))
    for a0, b0 in zip(s, e):
        if b0 - a0 < min_n or a0 < 1 or b0 >= len(a):
            continue
        if a[max(0, a0 - w):a0].max() > around and a[b0:b0 + w].max() > around:
            if not exact and max(a[max(0, a0 - k):a0].max(), a[b0:b0 + k].max()) < edge:
                if smooth is not None:                     # (the wave runs into the quiet spell and out of it)
                    smooth.append((int(a0), f"{1000 * (b0 - a0) / fs:.2f} ms of silence, smooth ends"))
                continue
            out.append((int(a0), f"{1000 * (b0 - a0) / fs:.2f} ms of {'zeros' if exact else 'silence'}"))
    return out


ZERO_FIT = 0.9                                 # zero_vs_usb: the jack and USB this alike around a spell to judge it
ZERO_MISS = 12.0                               # (LSB) a dropout: the jack this far off USB's wave inside the spell, and
ZERO_MISS_X = 3.0                              # this many times the neighbourhood's own difference


def zero_vs_usb(jack, uw, a0, win=240, lags=4):
    """a quiet spell of the jack from frame a0 (det_zero's, under 4 LSB) against USB on the jack's clock (uw: track's
    warp of USB along the drift line): (verdict, detail). True: USB has the sound the jack lost there (a dropout);
    False: USB runs through the same quiet (the music's own; the jack's AC coupling takes USB's offset there off);
    None: no judgement (USB and the jack not alike around it, or no USB there: the event stands). Fitted over the 5 ms
    either side (jack ~ g USB + c + a trend, the AC coupling's slow drift, at the best lag within +-4 samples). The
    FM-1's jack at 34.873 s of marimba-worst1 (44.1 kHz, 2026-10-24): the wave fell into the spell at 6 LSB a sample
    (past the 40 LSB cut-in test); USB, on the jack's scale, passes through the same spell at <= 16 LSB, the jack 15
    LSB off it inside and 12 outside: the music's own quiet, no dropout"""
    j = np.asarray(jack, float)
    b0 = a0
    while b0 < len(j) and abs(j[b0]) < 4:
        b0 += 1
    if a0 - win - lags < 0 or b0 + win + lags > min(len(j), len(uw)):
        return None, "no USB around it"
    nb = np.r_[a0 - win:a0, b0:b0 + win]
    tt = (nb - a0) / float(win)
    best = None
    for L in range(-lags, lags + 1):
        A = np.stack([uw[nb + L], np.ones(len(nb)), tt], 1)
        coef, *_ = np.linalg.lstsq(A, j[nb], rcond=None)
        res = j[nb] - A @ coef
        ncc = 1.0 - (res ** 2).sum() / max(((j[nb] - j[nb].mean()) ** 2).sum(), 1e-9)
        if best is None or ncc > best[0]:
            best = (ncc, L, coef, float(np.abs(res).max()))
    ncc, L, (g, c, dc), around = best
    if ncc < ZERO_FIT ** 2:
        return None, f"USB and the jack not alike around it (r2 {ncc:.2f})"
    pred = g * uw[a0 + L:b0 + L] + c + dc * (np.arange(a0, b0) - a0) / float(win)
    miss = float(np.abs(j[a0:b0] - pred).max())
    real = miss >= max(ZERO_MISS, ZERO_MISS_X * around)
    return real, (f"USB {'has' if real else 'runs through the same quiet:'} {np.abs(pred).max():.0f} LSB there, "
                  f"the jack {miss:.0f} LSB off it (around: {around:.0f})")


def det_clip(x, ceil):
    a = _peak(x)
    s, e = _runs(a >= ceil)
    out, last = [], -10 ** 9
    for a0, b0 in zip(s, e):
        if a0 - last > 256:
            out.append((int(a0), f"{b0 - a0} frame(s) at or over {ceil:.0f} (peak {a[a0:b0].max():.0f})"))
        last = b0
    return out


def _guard_mask(n, fs, gtimes, lag, widen=0.0):
    m = np.zeros(n, bool)
    for t in gtimes:
        a, b = int((t + lag - GUARD[0] - widen) * fs), int((t + lag + GUARD[1] + widen) * fs)
        m[max(0, a):max(0, min(n, b))] = True
    return m


def interp_residual(x, order=24, block=4096, with_c=False):
    """each sample minus its AR interpolation from `order` samples on either side (the AR model fitted per block):
    a single-sample click stays whole in it, a sharp but smooth feature (a hammer's edge, a clipper's corner) mostly
    does not. with_c: also the per-block interpolators ({block start: c / c0}, length 2 order + 1)"""
    from scipy.linalg import solve_toeplitz
    x = np.asarray(x, float)
    e = np.zeros_like(x)
    cs = {}
    for b0 in range(0, len(x), block):
        a0, a1 = max(0, b0 - 512), min(len(x), b0 + block + 512)
        seg = x[a0:a1] * np.hanning(a1 - a0)
        r = np.correlate(seg, seg, "full")[len(seg) - 1:len(seg) + order]
        if r[0] < 1e-3 * len(seg):
            continue
        r[0] *= 1.0 + 1e-6
        try:
            a = np.concatenate([[1.0], solve_toeplitz(r[:order], -r[1:order + 1])])
        except np.linalg.LinAlgError:
            continue
        c = np.correlate(a, a, "full")                    # the interpolator: symmetric, c[order] = c0
        c = c / c[order]
        cs[b0] = c
        c0, c1 = max(0, b0 - order), min(len(x), b0 + block + order)
        y = np.convolve(x[c0:c1], c, "same")
        k = min(block, len(x) - b0)
        e[b0:b0 + k] = y[b0 - c0:b0 - c0 + k]
    return (e, cs) if with_c else e


def _local_median(a, half=512, hop=128):
    n = len(a)
    c = np.arange(0, n, hop)
    m = np.array([np.median(a[max(0, i - half):i + half]) for i in c])
    return np.interp(np.arange(n), c, m)


def limiting(usb, fs):
    """per USB frame: the limiter is working (a peak >= LIM_ON within the 50 ms before, or the next 0.7 ms)"""
    from scipy.ndimage import maximum_filter1d
    a = _peak(usb)
    w = int(0.05 * fs)
    m = maximum_filter1d(a, w + 33)                         # (centred: frame n sees n - (w + 32) / 2 .. n + (w + 32) / 2)
    sh = (w - 32) // 2                                      # (moved so frame n sees n - w .. n + 32)
    out = np.empty_like(m)
    out[sh:] = m[:len(m) - sh]
    out[:sh] = m[0]
    return out >= LIM_ON


def det_click(x, fs, gmask, k=CLICK_K, floor=CLICK_FLOOR, shape=CLICK_SHAPE, half=8):
    """[(frame, detail)]: one-sample impulses: the interpolation residual over k x its local median (and >= floor LSB)
    whose neighbourhood has the shape an impulse leaves in it (the block's interpolator, ncc >= shape), outside the
    guards, per channel. Steps, corners and bright attacks leave other shapes"""
    x = np.asarray(x, float)
    chans = [x[:, 0], x[:, 1]] if x.ndim == 2 else [x]
    hits = []
    for c in chans:
        es, cs = interp_residual(c, with_c=True)
        e = np.abs(es)
        s = _local_median(e) * 1.4826 + 2.0
        bad = (e > k * s) & (e > floor) & ~gmask
        bad[:512] = bad[-512:] = False                      # (the take's edges: the model has no context there)
        for i in np.flatnonzero(bad):
            b0 = (i // 4096) * 4096
            if b0 not in cs or i < half or i + half + 1 > len(es):
                continue
            h = cs[b0][24 - half:24 + half + 1]
            seg = es[i - half:i + half + 1]
            m = float(np.dot(seg, h) / (np.linalg.norm(seg) * np.linalg.norm(h) + 1e-12))
            if abs(m) >= shape:
                hits.append((int(i), f"impulse {e[i]:.0f} LSB = {e[i] / s[i]:.0f} x local (shape {abs(m):.2f})"))
    hits.sort()
    out, last = [], -10 ** 9
    for i, d in hits:
        if i - last > int(0.005 * fs):
            out.append((i, d))
        last = i
    return out


STEP_WIN = 0.020                              # det_step's window (s; a low note dips at each zero crossing in less)
STEP_ON = (0.025, 0.045)                      # no note-on / pedal change this long before / after the windows it judges
STEP_OFF = 0.300                              # (a step down) no note-off / pedal-up this long before them


def step_blind(win=STEP_WIN):
    """where det_step cannot see a step at frame p by design, relative to p (s): (on_before, on_after, off_before,
    off_after): a note-on or pedal change from on_before before p to on_after after it, or (a step down) a release from
    off_before before to off_after after. The windows it judges start up to two windows before p (p inside a window:
    the one before it, then the half-way rule's one before that) and end up to two after; the self-test places its
    steps from these, so a placement can never land where the detector, as designed, does not look"""
    return 2 * win + STEP_ON[0], 2 * win + STEP_ON[1], 2 * win + STEP_OFF, 2 * win


def lfo_hz(v):
    """fx_tables.h FX_LFO_INC's law (tools/bartube.py): 0.5 x 24^(v / 127) Hz"""
    return 0.5 * 24 ** (min(int(v), 127) / 127)


def am_periods(ccs):
    """(s) the periods of the free-running level modulations a take's settings start: the vibraphone's motor (CC 79 a
    VIBES value, CC 47 on: its 3.5 Hz AM at RATE 78, the discs open for a third of each period), the TREM / WAH
    tremolo (CC 17 on with DEPTH: PAN moves the jack's left channel as much as AM does) and the filter's LFO (CC 72)
    sweeping a resonant filter. A level that falls 12 dB in a trough of them is the music's own (vibes-worst1..3 on
    the FM-1, 2026-10-24: the first chord met the motor open for 110 ms, then -15 dB; the host render with the same
    motor phase gives the same step)"""
    d = dict(param_defaults())
    d.update(dict(ccs))
    out = []
    if d.get(79, 127) < 96 and d.get(47, 0) >= 64 and d.get(48, 110) > 0:
        out.append(1.0 / lfo_hz(min(d.get(49, 78), 120)))
    if d.get(CC["TW_SW"], 0) >= 43 and d.get(CC["TW_DEPTH"], 0) > 0:
        out.append(1.0 / lfo_hz(d.get(19, 90)))
    if d.get(72, 0) > 0:
        out.append(1.0 / lfo_hz(d.get(76, 40)))
    return out


def _holds(r5, t, hop_s, level, d, step_db):
    """the level after a step (dB) still step_db past the 20 ms level at t (s; r5: one every hop_s), the closest of
    t and +-hop_s: a step of the same sign. Before the take: holds (nothing to compare)"""
    k = int(round(t / hop_s))
    ref = [r5[j] for j in (k - 1, k, k + 1) if 0 <= j < len(r5)]
    if not ref:
        return True
    ref = min(ref, key=lambda v: abs(v - level))
    return (level - ref <= -step_db) if d < 0 else (level - ref >= step_db)


STEP_AGREE = 4.0                              # (dB) a USB step in a modulation's first period the jack has too: same
STEP_JACK_FLOOR = -80.0                       # sign, within this, the jack's louder window over this (dBFS)


def det_step(x, fs, ons, offs, lag=0.0, widen=0.0, step_db=12.0, win_ms=1000 * STEP_WIN, min_db=-45.0, periods=(),
             ref=None, own=None):
    """a sudden level step: the 20 ms window's level >= step_db off the window before it and staying there (the next
    two within 6 dB of it), above min_db, with no note-on or pedal change from 25 ms before to 45 ms after (an attack,
    a damper's thump) and, for a step down, no note-off or pedal-up in the 300 ms before it (a damper's fall); windows
    of 20 ms because a low note's wave dips at each zero crossing in shorter ones; [(frame, detail)]. (Judging the
    step against the level's own trend, the slope of the two windows before, was tried on 2026-10-24 and is worse:
    the vibes' tremolo makes that slope a poor guess, 8 of 10 steps found; the plain difference finds them all.)
    periods (s, am_periods): with a level modulation running, the step must also hold against the level one period
    before (the same phase of it, +-5 ms): a modulation's trough is as low as the one before it, or the sound was not
    there yet; a step is not. In a modulation's first period (one period back is before the first note-on: no level
    to compare, the sound was not there) the modulation's crest after its first trough looks like a step
    (marimba-worst2 on the FM-1, 48 kHz, 2026-10-24: +12 dB at 0.480 s, 80 ms after the first chord, under the
    filter LFO's 0.735 s and TREM's 0.210 s periods; the jack the same wave, ncc 0.996, +9.5 dB at the same moment):
    there, with ref (a function (t_before, t_after, win) -> the jack's two levels in dB at those USB times, or None),
    a step the jack has too (same sign, within STEP_AGREE dB, over STEP_JACK_FLOOR) is the music's own and goes to
    own [(frame, detail)]; a USB-path step (a lost or repeated block, the ring) is on USB alone and stays an event.
    ons / offs on the recording's clock (map_clock), or the event clock and lag"""
    x = np.asarray(x, float)
    p = (x ** 2).mean(axis=1) if x.ndim == 2 else x ** 2
    w = int(win_ms * fs / 1000)
    n = len(p) // w
    if n < 8:
        return []
    r = 10 * np.log10(p[:n * w].reshape(n, w).mean(axis=1) + 1e-3) - 20 * np.log10(32767)
    if periods:                                            # (the same 20 ms level every 5 ms: one period back)
        h = max(1, int(0.005 * fs))
        cs = np.concatenate([[0.0], np.cumsum(p)])
        k5 = np.arange(0, len(p) - w + 1, h)
        r5 = 10 * np.log10((cs[k5 + w] - cs[k5]) / w + 1e-3) - 20 * np.log10(32767)
    ons, offs = np.asarray(ons, float) + lag, np.asarray(offs, float) + lag
    out = []
    for i in range(2, n - 3):
        b = i - 1                                          # the window before the step; a step inside a window
        if (min(r[i], r[i - 2]) <= r[i - 1] <= max(r[i], r[i - 2]) and abs(r[i - 1] - r[i - 2]) >= 3
                and abs(r[i] - r[i - 1]) >= 3):
            b = i - 2                                      # leaves it half way: then the one before that
        d = r[i] - r[b]
        if abs(d) < step_db or max(r[i], r[b]) < min_db or np.ptp(r[i:i + 3]) > 6:
            continue
        t0, t1 = b * w / fs, (i + 1) * w / fs
        if len(ons) and np.any((ons >= t0 - STEP_ON[0] - widen) & (ons <= t1 + STEP_ON[1] + widen)):
            continue
        if d < 0 and len(offs) and np.any((offs >= t0 - STEP_OFF - widen) & (offs <= t1 + widen)):
            continue
        if periods and not all(_holds(r5, i * w / fs - P, h / fs, r[i], d, step_db) for P in periods):
            continue                                       # (a modulation's trough or crest: the music's own)
        if periods and ref is not None and len(ons) and any(i * w / fs - P < ons.min() for P in periods):
            lv = ref(b * w / fs, i * w / fs, w / fs)       # (a modulation's first period: the jack decides)
            if lv is not None:
                dj = lv[1] - lv[0]
                if dj * d > 0 and abs(dj - d) <= STEP_AGREE and max(lv) > STEP_JACK_FLOOR:
                    if own is not None:
                        own.append(((b + 1) * w, f"level {d:+.0f} dB, the jack {dj:+.0f} dB (a modulation's first "
                                                 f"period)"))
                    continue
        if out and (b + 1) * w - out[-1][0] < 3 * w:
            continue                                       # (the same step, seen from the next window)
        out.append(((b + 1) * w, f"level {d:+.0f} dB in {20 * (i - b)} ms and staying"))
    return out


def _lag(u, j, a, n, lo, hi):
    js = j[a:a + n]
    us = u[a + lo:a + hi + n]
    c = np.correlate(us, js, "valid")
    k = int(np.argmax(np.abs(c)))
    den = np.sqrt((js ** 2).sum() * (us[k:k + n] ** 2).sum()) + 1e-20
    return lo + k, float(abs(c[k]) / den)


def _drift_fit(u, j, n, win, floor, npts=96, prior=None):
    """(L0, r, inliers): the jack's frame a matches USB frame a + L0 + r * a (48 kHz frames). Up to npts loud 100 ms
    windows spread over the take, each searched +-0.3 s (FFT correlation of the first differences: a strike's edge,
    not a tone's period, makes the peak); a line through the pair that most others agree with (within 2 samples, |r|
    <= 1200 ppm: USB audio clocks are far closer), refitted on those: a tone's wrong peak or a slip's later part
    cannot pull it. prior (L0, r): the line the recordings' clocks give; then only points within PRIOR_TOL of it vote
    (a held tone's decay put its raw peak at the search's loud edge, 0.3 s early, in every window: a false line)"""
    from scipy.signal import correlate
    w2, big = 2 * win, int(0.3 * FS_JACK)
    e = np.sqrt(np.convolve(j[:n] ** 2, np.ones(w2) / w2, "valid")[::win])
    ks = [k for k in np.flatnonzero(e > 10 * floor) if big <= k * win < n - big - w2]
    if not ks:
        return None
    ks = [ks[int(i)] for i in np.linspace(0, len(ks) - 1, min(npts, len(ks)))]
    pts = []
    for k in ks:
        a = k * win
        for js, us in ((j[a:a + w2], u[a - big:a + big + w2]),                       # the wave, and its first
                       (np.diff(j[a:a + w2 + 1]), np.diff(u[a - big:a + big + w2 + 1]))):   # difference: both
            c = correlate(us, js, "valid", method="fft")                           # candidates go to the vote
            i = int(np.argmax(np.abs(c)))
            den = np.sqrt((js ** 2).sum() * (us[i:i + w2] ** 2).sum()) + 1e-20
            if abs(c[i]) / den > 0.9:
                pts.append((a, i - big))
    if prior is not None:                                    # (only the points near the clocks' own line: see track)
        pts = [(a, L) for a, L in pts if abs(L - (prior[0] + prior[1] * a)) <= PRIOR_TOL * FS_JACK]
    if not pts:
        return None
    P = np.array(pts, float)
    best, bi = None, -1
    for x in range(len(P)):
        for y in range(x + 1, len(P)):
            da = P[y, 0] - P[x, 0]
            if da <= 0:
                continue
            r = (P[y, 1] - P[x, 1]) / da
            if abs(r) > 0.0012:
                continue
            L0 = P[x, 1] - r * P[x, 0]
            inl = np.abs(P[:, 1] - (L0 + r * P[:, 0])) <= 2
            if inl.sum() > bi:
                best, bi = inl, int(inl.sum())
    if best is None or bi < 2:
        return float(np.median(P[:, 1])), 0.0, 0
    r, L0 = np.polyfit(P[best, 0], P[best, 1], 1)
    return float(L0), float(r), bi


def track(usb, jack48, fs_usb, win=2400, hop=2400, min_db=-60.0, gtimes=(), lag=0.0, stimes=None, keep=None,
          prior=None):
    """the jack against USB (left), the clocks' drift taken out: (rows [(t_jack, lag, ncc)], events [(jack frame,
    detail)]). The drift line is fitted over the take (_drift_fit), USB is warped onto the jack's clock along it (so a
    window holds no drift of its own: 1.3 samples across 50 ms took a bright attack's ncc down to 0.75), then per
    window the best lag within +-2 samples of the last one ('near') and within +-320 ('far'); a slip = far more than 3
    samples off near, matching (ncc >= 0.97) clearly better than near (+0.1), in three steady windows running (no
    attack in them; a tone's period alone is no slip); an ncc < 0.97 in a window above -60 dBFS is an event too, unless the window holds a
    note-on or a pedal change or their echo (gtimes, on the jack's clock less lag: an attack out of silence through
    the jack's AC coupling and filters is not the same wave sample for sample); slips are judged in windows without a
    strike (stimes, default gtimes). prior (L0, r): the line the two recordings' clocks give (analyse: the guards'
    clocks): the drift line's points within PRIOR_TOL of it; without 8 that agree there, every point as before"""
    from math import gcd
    from scipy.signal import resample_poly
    u = np.asarray(usb, float)
    u = u[:, 0] if u.ndim == 2 else u
    g = gcd(4 * FS_JACK, fs_usb)
    u4 = resample_poly(u, 4 * FS_JACK // g, fs_usb // g)    # 192 kHz: the warp interpolates on it
    u48 = u4[::4]
    j = np.asarray(jack48, float)
    n = min(len(u48), len(j))
    floor = 32767 * 10 ** (min_db / 20)                    # (slips: on USB's scale, the jack's that x its gain,
    if n < 4 * win + int(0.8 * FS_JACK):                   # 10-15 dB under USB on the FM-1; a fixed -60 dBFS on it
        return [], []                                      # left a LEVEL -12 dB take's slips mostly unjudged)
    gj = float(np.clip(np.sqrt((j[:n] ** 2).mean() / max((u48[:n] ** 2).mean(), 1e-9)), 0.01, 10.0))
    floor_j = floor * gj

    def pick(pr):
        fa, fb = _drift_fit(u48, j, n, win, floor, prior=pr), _drift_fit(u48, j, n, win, floor_j, prior=pr)   # (the
        return (fa if fa is not None and fa[2] >= 8 else                   # loud windows, unless too few agree:
                (fb if fb is not None and (fa is None or fb[2] > fa[2]) else fa))   # then the quieter ones too)
    fit = pick(prior)
    if prior is not None and (fit is None or fit[2] < 8):   # (the clocks' line not borne out: the whole search)
        fit = pick(None)
    if fit is None:                                          # (the louder fit the bright attacks' ncc better,
        return [], []                                        # 2026-10-24)
    L0, r, _ = fit
    pos = 4.0 * (np.arange(n) * (1.0 + r) + L0)
    uw = np.interp(pos, np.arange(len(u4)), u4, left=0.0, right=0.0)
    if keep is not None:                                     # (keep: the warp for zero_vs_usb, outside USB's frames 0)
        keep["uw"] = uw.copy()
    # the jack frames USB covers along the line: the clocks differ by ~550 ppm on the FM-1, so at a 60 s take's end the
    # jack's last 50 ms window wanted USB 33 ms past its last frame, the warp padded it with zeros and the ncc fell to
    # 0.77 / 0.80 at 59.900 s (marimba-worst3, both rates, 2026-10-24): windows outside it are not judged
    lo_ok = int(np.ceil(max(0.0, -L0) / (1.0 + r)))
    hi_ok = min(n, int(np.floor(((len(u4) - 1) / 4.0 - L0) / (1.0 + r))) + 1)
    if keep is not None:
        keep["uw"][:lo_ok] = 0.0
        keep["uw"][hi_ok:] = 0.0
    rows, ev, prev, pend = [], [], 0, None
    wide = 320                                               # (a lost / repeated packet or block: <= ~1 block at 48 kHz)

    def far_lag(a):
        """the best lag within +-320 samples of the line (FFT correlation)"""
        from scipy.signal import correlate
        lo, hi = max(-wide, -a), min(wide, n - a - win)
        js, us = j[a:a + win], uw[a + lo:a + hi + win]
        c = correlate(us, js, "valid", method="fft")
        k = int(np.argmax(np.abs(c)))
        den = np.sqrt((js ** 2).sum() * (us[k:k + win] ** 2).sum()) + 1e-20
        return lo + k, float(abs(c[k]) / den)
    for a in range(int(0.3 * FS_JACK), n - win - 100, hop):
        if a + prev - 84 < lo_ok or a + prev + 84 + win > hi_ok:
            continue
        rj = np.sqrt((j[a:a + win] ** 2).mean())
        if rj < floor_j or np.sqrt((uw[a:a + win] ** 2).mean()) < floor:
            continue
        near, cn = _lag(uw, j, a, win, prev - 2, prev + 2)
        t0, t1 = a / FS_JACK - lag, (a + win) / FS_JACK - lag
        steady = not any(t0 - GUARD[1] <= t <= t1 + GUARD[0] for t in (gtimes if stimes is None else stimes))
        calm = not any(t0 - GUARD[1] <= t <= t1 + GUARD[0] for t in gtimes)   # (no attack, nor its echo)
        far, cf = far_lag(a) if cn < 0.97 and steady else (near, cn)   # (a wide look only where near fails)
        if pend is not None and steady:                      # a slip seen before: does it hold here too?
            pa, pd, pcn, pcf, nk = pend
            new, cnew = _lag(uw, j, a, win, prev + pd - 3, prev + pd + 3)
            if cnew >= 0.97 and cnew > cn + 0.1:
                if nk >= 1:                                  # (three steady windows: a tone's period alone is no slip)
                    ev.append((pa, f"slip {pd:+d} samples (jack vs USB; ncc {pcn:.3f} -> {pcf:.3f})"))
                    near, cn = new, cnew
                    prev = prev + pd
                    pend = None
                else:
                    pend = (pa, pd, pcn, pcf, nk + 1)
                    rows.append((a / FS_JACK, new + L0 + r * a, cnew))
                    continue
            else:
                pend = None
        elif steady and abs(far - near) > 3 and cf >= 0.97 and cf > cn + 0.1:
            pend = (a, far - near, cn, cf, 0)
            rows.append((a / FS_JACK, far + L0 + r * a, cf))
            continue
        if cn < 0.97 and calm and pend is None and rj >= floor:   # (the ncc test where the jack itself is over
                                                                   # -60 dBFS, as before: quieter windows of a hard
                                                                   # mallet's tail fell to 0.96 on the host's jack)
            ev.append((a, f"jack vs USB ncc {cn:.3f} < 0.97"))
        rows.append((a / FS_JACK, near + L0 + r * a, cn))
        # (prev moves only with a confirmed slip: the drift is out, so the right lag stays put; letting it follow the
        # best peak within +-3 walked a tone's broad peak away, +16 samples in a second, 2026-10-24)
    return rows, ev


def periodicity(times):
    """(n, the share of the spacings within 1.5 ms of a multiple of BEAT_S) of USB repeat / stale event times"""
    t = np.sort(np.asarray(times, float))
    if len(t) < 3:
        return len(t), None
    d = np.diff(t)
    k = np.maximum(1, np.round(d / BEAT_S))
    return len(t), float(np.mean(np.abs(d - k * BEAT_S) < 0.0015))


def analyse(usb, jack, fs_usb, events, lag_usb=0.0, lag_jack=None, widen=0.0, ccs=(), only=None, clock_usb=None,
            clock_jack=None):
    """every detector on one take: {"usb": [(t, kind, detail)], "jack": [...], "track": rows, "info": {}}; usb (n, 2)
    int LSB at fs_usb, jack (m,) LSB at 48 kHz or None; the take's event clock -> each recording: clock_* (lag s,
    rate: clock_fit's), or lag_* alone (rate 0); every guard is mapped onto each recording's own clock; only: run just
    these detectors (the self-test's injected takes)"""
    cu = tuple(clock_usb) if clock_usb is not None else (lag_usb, 0.0)
    cj = (tuple(clock_jack) if clock_jack is not None else
          (cu if lag_jack is None else (lag_jack, 0.0)))
    g, offs = guards(events, ccs, clock=cu), guards(events, ccs, releases=True, clock=cu)   # (on USB's clock)
    per = am_periods(ccs)
    q = np.round(np.asarray(usb, float)).astype(np.int64)
    res = dict(usb=[], jack=[], track=[], info=dict(clock_usb=[round(cu[0], 4), round(1e6 * cu[1], 1)]))
    lim = limiting(q, fs_usb)
    want = (lambda k: True) if only is None else (lambda k: k in only)

    def add(src, fs, kind, fn):
        if want(kind):
            res[src] += [(i / fs, kind, d) for i, d in fn()]
    add("usb", fs_usb, "repeat", lambda: det_repeat(q))
    add("usb", fs_usb, "stale", lambda: det_stale(q))
    add("usb", fs_usb, "zero", lambda: det_zero(q, fs_usb))
    add("usb", fs_usb, "clip", lambda: det_clip(q, 32767))
    gm = _guard_mask(len(q), fs_usb, g, 0.0, widen)
    if want("click"):
        for i, d in det_click(q, fs_usb, gm):
            res["usb"].append((i / fs_usb, "click-lim" if lim[i] else "click", d))
    jref = None
    if jack is not None:
        ja = np.asarray(jack, float)

        def jref(tb, ti, ws):                                # (USB times -> the jack's clock -> its 20 ms levels)
            out = []
            for tu in (tb, ti):
                a = int(round(float(map_clock((tu - cu[0]) / (1.0 + cu[1]), cj)) * FS_JACK))
                n = int(round(ws * FS_JACK))
                if a < 0 or a + n > len(ja):
                    return None
                out.append(10 * np.log10((ja[a:a + n] ** 2).mean() + 1e-3) - 20 * np.log10(32767))
            return out
    own_steps = res["info"]["step_jack_too"] = []
    add("usb", fs_usb, "step", lambda: det_step(q, fs_usb, g, offs, 0.0, widen, periods=per, ref=jref,
                                                 own=own_steps))
    res["info"]["step_jack_too"] = [f"{i / fs_usb:.3f} s {d}" for i, d in own_steps]
    res["info"]["over_ceiling"] = len(det_clip(q, CEIL))
    res["info"]["limiting_s"] = round(float(lim.mean() * len(q) / fs_usb), 2)
    if jack is not None:
        res["info"]["clock_jack"] = [round(cj[0], 4), round(1e6 * cj[1], 1)]
        er = cj[1] - cu[1]                                     # (on the jack's clock)
        gjt, offj = guards(events, ccs, clock=cj, echo_rate=er), guards(events, ccs, True, cj, er)
        gj = _guard_mask(len(jack), FS_JACK, gjt, 0.0, widen)
        add("jack", FS_JACK, "clip", lambda: det_clip(jack, 0.999 * 32768))
        warp = {}
        pr = (FS_JACK * (cu[0] - cj[0] * (1.0 + cu[1]) / (1.0 + cj[1])), (1.0 + cu[1]) / (1.0 + cj[1]) - 1.0)
        rows, tev = (track(q, jack, fs_usb, gtimes=gjt, lag=0.0, stimes=guards(events, clock=cj), keep=warp,   # (slips:
                           prior=pr)                         # (the jack's frame a is USB's a + L0 + r a on the clocks)
                     if want("track") or want("click") or want("zero") else ([], []))   # the strikes only, not their
                                                                        # echoes: no echo starts from silence)
        if want("zero"):                                     # (a quiet spell USB has too is the music's own: info)
            own, sm = [], []
            res["info"]["jack_zero_own"] = own
            for i, d in det_zero(jack, FS_JACK, exact=False, smooth=sm):
                v, why = zero_vs_usb(jack, warp["uw"], i) if "uw" in warp else (None, "no USB alignment")
                if v is False:
                    own.append(f"{i / FS_JACK:.3f} s {d}: {why}")
                else:
                    res["jack"].append((i / FS_JACK, "zero", d + ("" if v is None else f" ({why})")))
            for i, d in sm:                                  # (smooth ends: an event only where USB has the sound
                v, why = zero_vs_usb(jack, warp["uw"], i) if "uw" in warp else (None, "no USB alignment")
                if v is True:                                # the jack lost; else the music's own, information)
                    res["jack"].append((i / FS_JACK, "zero", f"{d} ({why})"))
                else:
                    own.append(f"{i / FS_JACK:.3f} s {d}: {why}")
        res["track"] = rows
        lagj = np.array([[r[0], r[1]] for r in rows]) if rows else None
        if want("click"):
            for i, d in det_click(jack, FS_JACK, gj):
                tu = i / FS_JACK + (float(np.interp(i / FS_JACK, lagj[:, 0], lagj[:, 1])) / FS_JACK
                                    if lagj is not None else 0.0)
                k = int(tu * fs_usb)
                res["jack"].append((i / FS_JACK, "click-lim" if 0 <= k < len(lim) and lim[k] else "click", d))
        add("jack", FS_JACK, "step", lambda: det_step(jack, FS_JACK, gjt, offj, 0.0, widen, periods=per))
        add("jack", FS_JACK, "track", lambda: tev)
    for k in ("usb", "jack"):
        res[k].sort()
    n, share = periodicity([t for t, kd, _ in res["usb"] if kd in ("repeat", "stale")])
    res["info"]["periodic"] = None if share is None else round(share, 2)
    res["info"]["click_lim"] = sum(1 for s in ("usb", "jack") for _, kd, _ in res[s] if kd == "click-lim")
    return res


def gated(res):
    return [(s, t, k, d) for s in ("usb", "jack") for t, k, d in res[s] if k in GATED]


USB_REPEAT_KINDS = ("repeat", "stale")


def usb_judge(usb, fs, events, ccs=(), rate=None):
    """the USB stream's own glitches (an underrun's repeated frames, a stale packet or block) in one recording, judged
    by this tool's detectors and nothing else: T1's other scenarios (tools/hil_must.py S3, S4, S1's fallback) call
    this, so the release judges USB audio with one definition. usb (n, 2) LSB at fs; events on the event clock (the
    take's own: t 0 = its first message); the guards on the recording's own clock (clock_fit; rate: measured, if the
    recorder has it). Returns ([(t s, kind, detail)], the clock fit)"""
    fit = clock_fit(usb, events, fs, rate=rate)
    res = analyse(usb, None, fs, events, ccs=ccs, only=USB_REPEAT_KINDS, clock_usb=(fit["lag"], fit["rate"]))
    return [e for e in res["usb"] if e[1] in USB_REPEAT_KINDS], fit


# ----------------------------------------------------------------------------------------------- the host build
def host_take(name, ccs, events, dur, tag="stress_max", clock=(0.0, 0.0)):
    """(usb (n, 2) LSB at 44.1 kHz, the build's output text): the host build plays the take. clock (lag s, rate): the
    events played at lag + t (1 + rate) of the recording, as the FM-1's USB stream has them (the self-test's device
    timing)"""
    H = hil_scan.hr()
    lines = [f"{5 + k // 16} midi 0xB0 {c} {v}" for k, (c, v) in enumerate(ccs)]
    R = 5 + len(ccs) // 16 + 300                          # (the settings, then the reset and 300 ms)
    lines.append(f"{R - 60} midi 0xB0 {hil_scan.RESET_CC} 127")
    dur = dur * (1.0 + clock[1]) + max(0.0, clock[0])
    for t, kind, a, b in events:
        ms = int(round(R + 1000 * float(map_clock(t, clock))))
        lines.append(f"{ms} midi 0x90 {a} {b}" if kind == "on" else f"{ms} midi 0x80 {a} 0" if kind == "off"
                     else f"{ms} midi 0xB0 {a} {b}")
    r = H.run(f"{tag}-{name}", lines, R + int(np.ceil(1000 * dur)) + 20, check=False)
    i0 = int(R * FS_HOST / 1000)
    return np.stack([r.L[i0:], r.R[i0:]], 1), r.out


def host_voices(txt, eng):
    m = re.search(r"voices max rhodes (\d+) of (\d+) bars (\d+) of (\d+)", txt)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2))) if eng == "rhodes" else (int(m.group(3)), int(m.group(4)))


JACK_GAIN = 0.25                              # the FM-1's jack on the MOTU: 10-15 dB under USB (2026-10-24 snippets)
JACK_DELAY = 0.003


def sim_jack(usb, fs_usb=FS_HOST, drift=JACK_DRIFT, delay_s=JACK_DELAY, gain=JACK_GAIN, seed=0):
    """the headphone jack as the MOTU records it, from the USB stream: left channel, 48 kHz on a clock drift off,
    AC coupling, the codec's low-pass, noise at -95 dBFS, a few ms later; 12 dB under USB as on the rig (0.45 until
    2026-10-24: the first note then always crossed onset_lag's old 300 LSB, the FM-1's did not)"""
    from scipy.signal import butter, resample_poly, sosfilt
    u = np.asarray(usb, float)[:, 0]
    up = resample_poly(u, 640, 147)                       # 44.1 -> 192 kHz
    n = int(len(u) * FS_JACK / fs_usb * (1 - drift)) - 64
    pos = (np.arange(n) * (1 + drift) * 4.0) - delay_s * 192000
    y = np.interp(pos, np.arange(len(up)), up, left=0.0, right=0.0)
    y = sosfilt(butter(1, 10 / (FS_JACK / 2), "high", output="sos"), y)
    y = sosfilt(butter(2, 20000 / (FS_JACK / 2), "low", output="sos"), y)
    return gain * y + np.random.default_rng(seed).normal(0, 32768 * 10 ** (-95 / 20), n)


# ------------------------------------------------------------------------------------------------- the self-test
KINDS = {                     # kind: (the path it is put on, the detector that must find it)
    "click": ("usb", "click"),            # one sample +-328 LSB (-40 dBFS)
    "repeat1": ("usb", "repeat"),         # an underrun's one frame: the last frame once more (on a slope)
    "hold": ("usb", "repeat"),            # an underrun: the last frame 2..44 times, the stream later by as much
    "chunk32": ("usb", "stale"),          # 32 frames played twice
    "stale128": ("usb", "stale"),         # a half block from three blocks earlier
    "block256": ("usb", "stale"),         # a block played twice
    "dropout": ("usb", "zero"),           # 1-5 ms of zeros
    "clip": ("usb", "clip"),              # 1 ms overdriven into full scale
    "step": ("usb", "step"),              # 300 ms at -20 dB (a level step down, and back up)
    "drop1ms": ("usb", ("track", "click")),   # a packet lost: 44 frames gone (a slip against the jack, the other
                                              # path, or the jump itself)
    "jack_click": ("jack", "click"),
    "jack_dropout": ("jack", "zero"),     # 1-5 ms of nothing on the jack
}


STEP_S = 0.3                                  # the injected level step: 300 ms at -20 dB


def _span_mask(n, fs, gtimes, before, after):
    """frames p with an event time t from `before` s before p to `after` s after it (p in [t - after, t + before])"""
    m = np.zeros(n, bool)
    for t in gtimes:
        a, b = int((t - after) * fs), int((t + before) * fs) + 1
        m[max(0, a):max(0, min(n, b))] = True
    return m


def _step_keep(dur, releases=False, spare=STEP_WIN):
    """(before, after): how far from a step at p an onset (releases: a note-off / pedal-up) must be for the self-test
    to place it there: det_step's blind zone (step_blind) and a window to spare, after it also the step's own length
    (the level comes back after dur: nothing of the music's own inside it)"""
    ob, oa, rb, ra = step_blind()
    return (rb + spare, ra + dur + spare) if releases else (ob + spare, oa + dur + spare)


def _places(x, fs, events, rng, k, kind, avoid_lim=None, ccs=(), widen=0.0, clock=(0.0, 0.0), echo_rate=0.0,
            inside=False):
    """k frames that sound (>= -40 dBFS around), 0.3 s apart, outside the guards (every kind: the clicks must be
    there to be judged, the others for a fair test), the in-place repeats on a slope (where an underrun's repeat is a
    click; on a turning point it is no click at all), never where the limiter works (avoid_lim)"""
    lv = _peak(x)
    from scipy.ndimage import maximum_filter1d
    lv = maximum_filter1d(lv, int(0.01 * fs) + 1)
    g = _guard_mask(len(lv), fs, guards(events, ccs, clock=clock, echo_rate=echo_rate), 0.0, 0.003 + widen)
                                                            # (widen: the device's guards are wider)
    if kind == "step":                                      # (a level to step from, and nothing of the music's own:
        on = guards(events, ccs, clock=clock, echo_rate=echo_rate)   # no attack, no damper near it: outside
        off = guards(events, ccs, True, clock, echo_rate)
        g = _span_mask(len(lv), fs, on, *_step_keep(STEP_S, spare=STEP_WIN + widen))   # det_step's blind zone, a
        g |= _span_mask(len(lv), fs, off, *_step_keep(STEP_S, releases=True, spare=STEP_WIN + widen))   # window to
                                                            # spare, and no attack or damper in its 300 ms)
        p2 = np.asarray(x, float)[:, 0] ** 2
        w = int(STEP_WIN * fs)
        rms = np.sqrt(np.convolve(p2, np.ones(w) / w, "full")[:len(p2)])
        g |= rms < 32767 * 10 ** (-35 / 20)
        db = 20 * np.log10(rms + 1.0)                       # (the 20 ms level ending at each frame)
        lvl = np.stack([np.concatenate([db[k * w:], np.full(k * w, db[-1])]) for k in range(4)])
        g |= np.ptp(lvl, axis=0) > 3.0                      # a level that holds still: the window before p and
                                                            # the three after it within 3 dB, half det_step's 6 (a
                                                            # fast-decaying bar would leave too little margin)
        for P in am_periods(ccs):                           # (a modulation running: det_step judges the step against
            sh = int(P * fs)                                # the level one period before too: there as here)
            ref = np.concatenate([np.full(sh, -200.0), db[:-sh]])
            g |= np.abs(db - ref) > 3.0
    ok = (lv > 32767 * 0.01) & ~g
    ok[:int(0.5 * fs)] = False
    ok[-int((2.0 if kind == "drop1ms" else 0.5) * fs):] = False   # (a slip is judged over the next second)
    if avoid_lim is not None:
        ok &= ~avoid_lim[:len(ok)]
    if kind in ("dropout", "jack_dropout"):                 # (sound on both sides, as the detector wants it)
        from scipy.ndimage import maximum_filter1d as mx
        a = _peak(x)
        w = int(0.01 * fs)
        before = mx(a, w, origin=w // 2 - 1)                # (the 10 ms before each frame)
        after = np.empty_like(before)
        sk = int(0.006 * fs)                                # (the 10 ms after the longest dropout, 5 ms)
        after[:-sk] = mx(a, w, origin=-(w // 2))[sk:]
        after[-sk:] = 0
        ok &= (before > 400) & (after > 400)
        if inside:                                          # (and in its first ms, the shortest: 1 ms of a low
            s1 = int(0.001 * fs)                            # tone's zero crossing is the tone's own quiet; the
            ok &= mx(a, s1, origin=-(s1 // 2)) > 400        # folded jack check)
    if kind in ("chunk32", "stale128", "block256"):         # (a copy of near silence is no glitch: something to copy)
        from scipy.ndimage import maximum_filter1d as mx, minimum_filter1d as mn
        c0 = np.asarray(x, float)[:, 0]
        ok &= (mx(c0, 33) - mn(c0, 33)) >= 256
    if kind in ("repeat1", "hold"):
        q = np.asarray(x, float)
        d0, d1 = q[1:-1, 0] - q[:-2, 0], q[2:, 0] - q[1:-1, 0]
        sl = np.zeros(len(ok), bool)
        sl[1:-1] = (np.sign(d0) == np.sign(d1)) & (np.abs(d0) >= 40) & (np.abs(d1) >= 40)
        ok &= np.roll(sl, 1)                                # (the copy goes in at p: frame p - 1 on the slope)
    cand = np.flatnonzero(ok)
    out = []
    for i in rng.permutation(cand):
        if all(abs(int(i) - p) > (0.8 if kind == "step" else 0.3) * fs for p in out):
            out.append(int(i))
        if len(out) == k:
            break
    return sorted(out)


def inject(kind, usb, jack, events, rng, k=10, ccs=(), widen=0.0, clock_usb=(0.0, 0.0), clock_jack=(0.0, 0.0),
           inside=False):
    """(usb', jack', [(t_usb s or None, t_jack s or None)]): k glitches of one kind; placed by the guards on each
    recording's true clock (clock_*: the self-test's device timing) widened by widen"""
    u = np.array(usb, float)
    j = None if jack is None else np.array(jack, float)
    if KINDS[kind][0] == "jack":
        lim = limiting(usb, FS_HOST)
        lim48 = np.interp(np.arange(len(j)) * FS_HOST / FS_JACK, np.arange(len(lim)), lim) > 0
        pl = _places(j, FS_JACK, events, rng, k, kind, lim48, ccs, widen, clock_jack, clock_jack[1] - clock_usb[1],
                     inside)
        for p in pl:
            if kind == "jack_click":                  # (the USB click as the jack has it: JACK_GAIN)
                j[p] += CLICK_LSB * JACK_GAIN * (1 if rng.random() < 0.5 else -1)
            else:
                j[p:p + int(rng.uniform(1, 5) * FS_JACK / 1000)] = 0.0
        return u, j, [(None, p / FS_JACK) for p in pl]
    pl = _places(u, FS_HOST, events, rng, k, kind, limiting(usb, FS_HOST) if kind == "click" else None, ccs, widen,
                 clock_usb)
    shift, marks = 0, []
    for p0 in pl:
        p = p0 + shift
        if kind == "click":
            u[p, :] += CLICK_LSB * (1 if rng.random() < 0.5 else -1)
        elif kind == "repeat1":                       # an underrun: the last frame once more, the rest one later
            u = np.concatenate([u[:p], u[p - 1:p], u[p:]])[:len(usb)]
            shift += 1
        elif kind == "hold":
            n = int(rng.integers(2, 45))
            u = np.concatenate([u[:p], np.repeat(u[p - 1:p], n, 0), u[p:]])[:len(usb)]
            shift += n
        elif kind == "chunk32":
            u[p:p + 32] = u[p - 32:p]
        elif kind == "stale128":
            u[p:p + 128] = u[p - 768:p - 640]
        elif kind == "block256":
            u[p:p + 256] = u[p - 256:p]
        elif kind == "dropout":
            u[p:p + int(rng.uniform(1, 5) * FS_HOST / 1000)] = 0.0
        elif kind == "clip":                          # overdriven into full scale (twice what it needs)
            s = slice(p, p + int(0.001 * FS_HOST))
            u[s] = np.clip(u[s] * (2 * 32767 / max(1.0, np.abs(u[s]).max())), -32767, 32767)
        elif kind == "step":
            u[p:p + int(STEP_S * FS_HOST)] *= 0.1
        elif kind == "drop1ms":
            u = np.concatenate([u[:p], u[p + 44:], np.zeros((44, 2))])[:len(usb)]
            shift -= 44
        marks.append((p / FS_HOST, None))
    return u, j, marks


def found(res, kind, marks, tol=0.012, each=False):
    """how many of the marks the kind's own detector(s) found (each: a list of True / False per mark)"""
    src, det = KINDS[kind]
    dets = det if isinstance(det, tuple) else (det,)
    n, per = 0, []
    for tu, tj in marks:
        hit = False
        for d in dets:
            side = "jack" if src == "jack" or d == "track" else "usb"
            t, slack = (tj, tol) if src == "jack" else (tu, tol)
            if d == "step":
                slack = 0.045                              # (20 ms windows)
            if d == "track":                               # (judged in steady windows, three running: up to a second
                t = tu + 0.003                             # later in the busiest take; the simulated jack's delay)
                hit |= any(t - 0.06 <= e <= t + 1.5 for e, kd, _ in res[side] if kd == d)
                continue
            hit |= any(abs(e - t) <= slack for e, kd, _ in res[side] if kd == d)
        n += hit
        per.append(bool(hit))
    return per if each else n


DEV_CLOCK = (0.004, 1500e-6)                 # the self-test's device timing: USB 4 ms late, its clock 1500 ppm off
RATE_ERR = 20e-6                              # (the rate as the recorder measures it, Ring.rate: off by this much)
CLOCK_TOL = DEV_JITTER                        # clock_fit's mapping this close to the truth over the whole take (the
                                              # guards widen by DEV_JITTER on the FM-1: the block the event lands in)


def _jack_clock(cu, drift=JACK_DRIFT, delay=JACK_DELAY):
    """sim_jack's jack clock from USB's: a USB time tu is jack time (tu + delay) / (1 + drift)"""
    return (cu[0] + delay) / (1 + drift), (1 + cu[1]) / (1 + drift) - 1


def self_test(engines=ENGINES, worst_s=20.0, recipe_s=24.0, seed=0, k=10, log=print, timings=("exact", "device")):
    """the proof on host renders: ({engine: {"clean": [...], "kinds": {kind: (found, placed)}, "kinds_device": {...},
    "voices": (n, of), "clocks": [...]}}, ok). Every take clean: 0 gated events; k injections of every kind into the
    first worst take (LEVEL -12 dB: the limiter out) and the first two recipe takes (the user's), a few to a copy (a
    lost packet one to a copy: it moves the rest of the take); a level step only in the recipe takes (the busy one has
    no room for 300 ms without an attack). Twice: 'exact', the host's own timing (the events where the take's clock
    has them: the --host run), and 'device', the FM-1's: the events played DEV_CLOCK late and slow, the jack another
    JACK_DRIFT off and 12 dB under USB, and the analysis as a device run does it, with no knowledge of that: each
    recording's clock_fit, DEV_JITTER. clock_fit must find both clocks (CLOCK_TOL), the clean renders give 0 events
    there too, and every injection is found there too"""
    allok, out = True, {}
    for eng in engines:
        r = dict(clean=[], kinds={}, kinds_device={}, voices=None, click_lim=0, missed=[], clocks=[])
        for timing in timings:
            dev = timing == "device"
            cu = DEV_CLOCK if dev else (0.0, 0.0)
            cj = _jack_clock(cu) if dev else (0.0, 0.0)
            clips = []
            for name, ccs, ev, dur in takes(eng, worst_s, recipe_s, seed):
                u, txt = host_take(name, ccs, ev, dur, tag="stress_max-st" + ("-dev" if dev else ""), clock=cu)
                v = host_voices(txt, eng)
                if v:
                    r["voices"] = max(r["voices"] or (0, v[1]), v)
                jk = sim_jack(u)
                if dev:
                    fu = clock_fit(u, ev, FS_HOST, rate=cu[1] + RATE_ERR)
                    fj = clock_fit(jk, ev, FS_JACK, rate=cj[1] + RATE_ERR)
                    tt = np.linspace(0.0, dur, 64)
                    for src, f_, c_ in (("usb", fu, cu), ("jack", fj, cj)):
                        err = float(np.abs(map_clock(tt, (f_["lag"], f_["rate"])) - map_clock(tt, c_)).max())
                        good = err <= CLOCK_TOL
                        r["clocks"].append(f"{name} {src}: lag {1000 * f_['lag']:.1f} ms (true {1000 * c_[0]:.1f}), "
                                           f"{f_['n']} onsets, {f_['how']}; {1000 * err:.1f} ms off at most"
                                           + ("" if good else " FAIL"))
                        if not good:
                            r["clean"].append(f"{name} {src} clock_fit off: {r['clocks'][-1]}")
                    fit = dict(clock_usb=(fu["lag"], fu["rate"]), clock_jack=(fj["lag"], fj["rate"]),
                               widen=DEV_JITTER)
                else:
                    fit = {}
                clips.append((name, u, jk, ev, ccs, fit))
                res = analyse(u, jk, FS_HOST, ev, ccs=ccs, **fit)
                r["clean"] += [f"{timing} {name} {s} {t:.3f} s {kd}: {d}" for s, t, kd, d in gated(res)]
                r["click_lim"] += res["info"]["click_lim"]
            rng = np.random.default_rng(seed + 99)
            base = [c for c in clips if c[0].endswith(("-worst1", "-recipe-dry1", "-recipe-fx1"))]
            kinds = r["kinds_device" if dev else "kinds"]
            for kind in KINDS:
                f = m = tries = 0
                per = 1 if kind == "drop1ms" else 5         # (a lost packet moves the rest of the take: one a copy)
                cand = [c for c in base if kind != "step" or "-recipe" in c[0]]   # (no room for a step in the busy
                                                                                  # take)
                while m < k and tries < 12:
                    name, u, jk, ev, ccs, fit = cand[tries % len(cand)]
                    tries += 1
                    u2, j2, marks = inject(kind, u, jk, ev, rng, min(per, k - m), ccs,   # (placed by the truth,
                                           widen=(DEV_JITTER + CLOCK_TOL) if dev else 0.0,   # found without it;
                                           clock_usb=cu, clock_jack=cj)                # outside the wider guards)
                    d = KINDS[kind][1]
                    res = analyse(u2, j2, FS_HOST, ev, ccs=ccs, only=set(d if isinstance(d, tuple) else (d,)), **fit)
                    hits = found(res, kind, marks, each=True)
                    f, m = f + sum(hits), m + len(marks)
                    r["missed"] += [f"{timing}: {kind} in {name} at {tu if tu is not None else tj:.3f} s"
                                    for (tu, tj), h in zip(marks, hits) if not h]
                kinds[kind] = (f, m)
            for name, u, jk, ev, ccs, fit in [c for c in clips if c[0].endswith("-jackcheck")]:
                for kind in ("jack_click", "jack_dropout"):   # (rev_hil's folded jack check: the jack-path
                    f = m = tries = 0                         # detectors on its held quiet low tone too, 2026-10-25;
                    per = 5                                   # a lost USB packet stays proven on the takes above)
                    while m < k and tries < 12:
                        tries += 1
                        u2, j2, marks = inject(kind, u, jk, ev, rng, min(per, k - m), ccs,
                                               widen=(DEV_JITTER + CLOCK_TOL) if dev else 0.0,
                                               clock_usb=cu, clock_jack=cj, inside=True)
                        d = KINDS[kind][1]
                        res = analyse(u2, j2, FS_HOST, ev, ccs=ccs, only=set(d if isinstance(d, tuple) else (d,)),
                                      **fit)
                        hits = found(res, kind, marks, each=True)
                        f, m = f + sum(hits), m + len(marks)
                        r["missed"] += [f"{timing}: {kind} in {name} at {tu if tu is not None else tj:.3f} s"
                                        for (tu, tj), h in zip(marks, hits) if not h]
                    kinds[f"{kind}@jackcheck"] = (f, m)
        okv = r["voices"] is not None and r["voices"][0] == r["voices"][1]
        ok = (not r["clean"] and all(f == m == k for kk in ("kinds", "kinds_device") for f, m in r[kk].values())
              and okv)
        allok &= ok
        out[eng] = r
        for kk, lab in (("kinds", "exact timing"), ("kinds_device", "device timing")):
            if r[kk]:
                log(f"self-test {eng} ({lab}): " + ", ".join(f"{kd} {f}/{m}" for kd, (f, m) in r[kk].items()))
        log(f"self-test {eng}: clean renders {len(r['clean'])} event(s) ({r['click_lim']} click-lim, information); "
            f"voices {r['voices']}" + ("  ok" if ok else "  FAIL"))
        for line in r["clocks"]:
            log("    clock_fit: " + line)
        for line in r["clean"][:10]:
            log("    false alarm: " + line)
        for line in r["missed"][:10]:
            log("    missed: " + line)
    return out, allok


# ------------------------------------------------------------------------------------------------------ the FM-1
class RateError(RuntimeError):
    """the FM-1's USB stream is not at the asked rate (or not flowing) after the recorder opened it"""


def stream_ok(state, rate):
    """the firmware's stream state (CC 100 id 12: bit 1 flowing, bit 2 48 kHz) against the asked rate: as
    hil_must.gate_counters' "USB stream state" row"""
    return bool(state & 2) and bool(state & 4) == (rate == 48000)


def stream_text(state):
    if state is None:
        return "state unknown"
    return ("flowing" if state & 2 else "not flowing") + (", 48 kHz" if state & 4 else ", 44.1 kHz")


class Device:
    """the FM-1: hil_must.Dev (MIDI out under a lock, MIDI in for the CC 116 replies), the persistent recorder with USB
    at the asked rate (and blocksize) and the jack on the interface. poll: the counters read every 0.5 s through
    hil_must.Poller around each take (False inside T1, where hil_must polls around the whole run)"""

    def __init__(self, rate, jack, jack_dev, jack_ch, blocksize=0, poll=True):
        import sounddevice as sd
        import hil_must
        self.hm, self.sd, self.poll = hil_must, sd, poll
        self.mdev = hil_must.Dev()
        devs = [i for i, d in enumerate(sd.query_devices())
                if any(k in d["name"].lower() for k in hil_scan.PORT_KEYS) and d["max_input_channels"] >= 2]
        if not devs:
            sys.exit("no FM-1 audio input")
        self.udev, self.rate, self.bs = devs[0], rate, blocksize
        self.clock = {}
        self.cap = None
        self.jack = (jack_dev, jack_ch) if jack else None
        if jack:
            js = [i for i, d in enumerate(sd.query_devices()) if d["name"] == jack_dev and
                  d["max_input_channels"] >= jack_ch]
            if not js:
                sys.exit(f"no {jack_dev} input {jack_ch} for the jack (--source usb records USB only)")
            self.jdev = js[0]
        self.open(usb=True)

    def open(self, usb=True):
        """(re)open the recorder: USB (unless usb=False: the jack alone) and the jack"""
        if self.cap:
            self.cap.close()
        sd = self.sd
        self.cap = hil_scan.Capture(sd)
        if usb:
            self.cap.add("usb", self.udev, (0, 1), 2, secs=120.0, sr=self.rate, blocksize=self.bs, switch_rate=True)
        if self.jack:
            self.cap.add("jack", self.jdev, (self.jack[1] - 1,), int(sd.query_devices(self.jdev)["max_input_channels"]),
                         secs=120.0, sr=FS_JACK)
        time.sleep(0.5)
        if usb:
            self.check_rate()

    def check_rate(self, wait=3.0):
        """the FM-1's own view of the USB stream (CC 116, CC 100 id 12) before any take: flowing at self.rate, or
        RateError (2026-10-24: "--rate 44100" left the device at 48 kHz, every take's counter gate said so)"""
        t0, st = time.monotonic(), None
        while time.monotonic() - t0 < wait:
            st = self.hm.parse_reply(self.mdev.query())["usb"].get("state")
            if st is not None and stream_ok(st, self.rate):
                return st
            time.sleep(0.25)
        raise RateError(f"the FM-1 reports the USB stream {stream_text(st)}, not flowing at {self.rate / 1000:g} kHz"
                        + (" (no reply with the USB items: a firmware before the USB ring fix)" if st is None else ""))

    def send(self, kind, a, b=0):
        self.mdev.send(kind, a, b)

    def take(self, ccs, events, dur, usb=True):
        """(usb (n, 2) LSB or None, jack (m,) LSB at 48 kHz or None, the counters' rows and summary (None when not
        polled), rig problems)"""
        self.send("cc", 120, 0)
        for c, v in ccs:
            self.send("cc", c, v)
        time.sleep(0.3)
        self.send("cc", hil_scan.RESET_CC, 127)
        time.sleep(0.4)
        keys = list(self.cap.rings)
        poller = self.hm.Poller(self.mdev) if self.poll else None
        acc = poller.__enter__() if poller else None         # (the maxima restart; every 0.5 s; the release blocks
        st = self.cap.now()                                  # are in the last reply, before any reset)
        t0 = time.monotonic()
        for t, kind, a, b in events + [(dur, "end", 0, 0)]:
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            if kind != "end":
                self.send(kind, a, b)
        if poller:
            poller.__exit__(None, None, None)
        got, rig = {}, []
        self.clock = {}                                      # (each stream's rate against the event clock: its ADC
        for k in keys:                                       # times, Ring.rate; clock_fit maps the guards with it)
            x = self.cap.cut(k, st[k], dur)
            ring = self.cap.rings[k]
            fr = ring.rate(st[k], dur)
            self.clock[k] = None if fr is None else fr / ring.sr - 1.0
            rig += [f"{k}: {f}" for f in ring.flags_between(st[k], st[k] + dur)]
            rig += [f"{k}: recorder gap {1000 * g:+.0f} ms at {a - st[k]:.2f} s" for a, g in ring.gaps]
            got[k] = np.asarray(x, float) * 32768.0
        self.send("cc", 64, 0)
        self.send("cc", 120, 0)
        counters = None
        if acc is not None:
            rows = self.hm.gate_counters(acc, self.rate if usb else None)
            sm = acc.summary()
            if sm["replies"]:                                # the engine's own longest block (CC 116/117): <= 4500
                rows.append(("engine block max (us)", sm["engine_max_us"], f"<= {CPU_TARGET_US}",
                             "ok" if sm["engine_max_us"] <= CPU_TARGET_US else "FAIL"))
            counters = dict(rows=rows, summary=sm)
        return got.get("usb"), (got["jack"][:, 0] if "jack" in got else None), counters, rig


def onset_lag(x, events, fs, thr=None):
    """s from the event clock to the recording: the first sound against the first note-on. thr (LSB): by default 10 x
    the recording's peak before it (and >= 24): the FM-1's jack sits 10-15 dB under USB, and a fixed 300 LSB there
    missed the first note and took the second or third, 0.2-0.45 s late (2026-10-24). clock_fit's fallback"""
    on = [e[0] for e in events if e[1] == "on"]
    if not on or x is None:
        return 0.0
    a = _peak(x)
    s = int(max(0.0, min(on) - 0.005) * fs)
    if thr is None:                                    # (and at most a tenth of the first note's peak: a hum)
        thr = max(24.0, 10 * float(a[int(0.05 * fs):s].max())) if s > int(0.06 * fs) else 24.0
        thr = min(thr, max(4.0, 0.1 * float(a[s:s + int(0.25 * fs)].max(initial=0.0))))
    i = np.flatnonzero(a[s:] > thr)
    return (s + int(i[0])) / fs - min(on) if len(i) else 0.0


CLOCK_PPM = 3000e-6                           # clock_fit's search: the recording's clock against the event clock


def map_clock(times, clock):
    """event-clock times (s) onto a recording's clock: lag + t (1 + rate)"""
    lag, rate = clock
    return np.asarray(times, float) * (1.0 + rate) + lag


ONSET_DB = 15.0                               # a strong onset: the attacks' energy 15 dB over the 20 ms before
ONSET_TOL = 0.006                             # an onset matches a note-on within +-6 ms (the FM-1: the next block)
LAG0_TOL = 0.012                              # (s) clock_fit: the lag within this of the first sound's


def onset_times(x, fs, thr=None, hold=0.03):
    """(s) the strong onsets of a recording: where the energy of the first difference (attacks, not a bass note's
    swing nor the jack's AC coupling), per 1 ms frame, first rises thr dB over its mean in the 20 ms ending 3 ms
    before (60 dB under the take's loudest at least), once per hold: a strike out of quiet (a take's first, the
    chords after a pedal lift, a recipe's notes), not every chord of a busy take (the vibraphone's soft attacks under
    HEAVY's reverb and delay: no onset)"""
    thr = ONSET_DB if thr is None else thr
    x = np.asarray(x, float)
    x = x.mean(axis=1) if x.ndim == 2 else x
    d = np.diff(x, prepend=x[:1])
    nf = int(len(x) / (fs * 0.001))
    if nf < 64:
        return np.zeros(0)
    edges = np.round(np.arange(nf + 1) * fs * 0.001).astype(int)
    cs = np.concatenate([[0.0], np.cumsum(d ** 2)])
    e = (cs[edges[1:]] - cs[edges[:-1]]) / np.maximum(1, np.diff(edges))
    L = 10 * np.log10(e + e.max() * 1e-6 + 1e-9)
    c = np.concatenate([[0.0], np.cumsum(L)])
    k = np.arange(nf)
    a, b = np.maximum(0, k - 23), np.maximum(1, k - 3)
    base = np.where(k >= 4, (c[b] - c[a]) / np.maximum(1, b - a), L[0])
    S = L - base
    up = np.flatnonzero((S[1:] >= thr) & (S[:-1] < thr)) + 1
    out, last = [], -1e9
    for i in up:
        if i - last >= hold * 1000:
            out.append((i + 0.5) / 1000)
        last = i
    return np.array(out)


def _match(o, on, lag, rate):
    """(the matched note-on times, their onsets): each onset to the nearest predicted note-on, within ONSET_TOL"""
    pred = lag + on * (1.0 + rate)
    k = np.abs(o[:, None] - pred[None, :]).argmin(axis=1)
    m = np.abs(o - pred[k]) <= ONSET_TOL
    return on[k[m]], o[m]


def clock_fit(x, events, fs, rate=None):
    """the recording's clock against the event clock (the stimulus' note-on times): {"lag", "rate", "n", "span",
    "how"}: a recording time = lag + t (1 + rate). The FM-1's USB stream runs ~540-760 ppm off the computer's clock
    that sends the events (2.6 ms late at the start of a take, 42 ms at 52.7 s; 2026-10-24), the jack ~10 ms late and
    steady: one lag from the first onset (onset_lag, the old way) left the guards (the attacks, the dampers, the
    delay's echoes) up to 30 ms off the real ones by a take's end, and on the jack, 10-15 dB under USB, the old fixed
    300 LSB took the second or third note for the first, 0.2-0.45 s late. rate: measured (the recorder's ADC times,
    Ring.rate: a device run): the lag is then the first sound's (onset_lag: every take starts in silence, its first
    note-on is the surest onset there is; a busy take's later strikes come 5-9 ms late out of the sound already
    there, and a median of a few of them was worse, 2026-10-24); n counts the strong onsets (onset_times) it puts
    within ONSET_TOL of a note-on (information). Without a rate the onsets fit both: the lag within LAG0_TOL of the
    first sound (so a busy take's strikes cannot line up on the wrong chords), the (rate, lag) on a grid (+-CLOCK_PPM
    by 50 ppm, 1 ms) that matches the most of them, then a least-squares line through the pairs (twice), when 3 or
    more span a third of the take; else rate 0 (how says which)"""
    on = np.unique(np.round([e[0] for e in events if e[1] == "on"], 3))
    lag0 = onset_lag(x, events, fs)
    fb = dict(lag=lag0, rate=float(rate or 0.0), n=0, span=0.0, how="first sound")
    if x is None or len(on) < 2:
        return fb
    o = onset_times(x, fs)
    if rate is not None:
        n = len(_match(o, on, lag0, float(rate))[0]) if len(o) else 0
        return dict(fb, n=n, how="measured rate, the first sound's lag")
    if not len(o):
        return fb
    dur = len(x) / fs
    rates = np.arange(-CLOCK_PPM, CLOCK_PPM + 1e-9, 50e-6)
    best = (-1.0, rates[0], lag0)
    for r in rates:
        for lg in np.arange(lag0 - LAG0_TOL, lag0 + LAG0_TOL + 1e-9, 0.001):
            pred = lg + on * (1.0 + r)
            i = np.clip(np.searchsorted(pred, o), 1, len(pred) - 1)
            dist = np.minimum(np.abs(o - pred[i - 1]), np.abs(o - pred[i]))
            m = dist <= ONSET_TOL
            sc = m.sum() - 0.1 * (dist[m].mean() / ONSET_TOL if m.any() else 0.0)
            if sc > best[0]:
                best = (float(sc), float(r), float(lg))
    _, r, lag = best
    how = "rate 0"
    for _ in range(2):                                      # (the pairs, and the lag / the line through them)
        te, to = _match(o, on, lag, r)
        if not len(te):
            return fb
        span = float(te.max() - te.min())
        if rate is None and len(te) >= 3 and span >= dur / 3:
            sl, lag = np.polyfit(te, to, 1)
            r, how = float(sl - 1.0), "fit"
        else:
            r = 0.0 if how != "fit" else r
            lag = float(np.median(to - te * (1.0 + r)))
    return dict(lag=float(lag), rate=float(r), n=int(len(te)), span=round(span, 2), how=how)


def save_snip(path, x, fs, t):
    import wave
    a = max(0, int((t - 0.5) * fs))
    y = np.clip(np.round(np.asarray(x, float)[a:a + int(fs)]), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1 if y.ndim == 1 else y.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(y.tobytes())


# ------------------------------------------------------------------------------------------------------ the runs
def device_takes(eng, a):
    """the takes of a device run: the stimulus, then (both sources) the no-USB recipe, then (--leds) the LEDS ones"""
    T = [(n, c, e, d, dict(usb=True)) for n, c, e, d in takes(eng, a.worst_s, a.recipe_s, a.seed)]
    rng = np.random.default_rng(a.seed + 7)
    if a.source == "both" and a.no_usb_s > 0:
        ev, dur = recipe_events(voices(eng), a.no_usb_s, rng)
        T.append((f"{eng}-no-usb", engine_ccs(eng, {}), sorted(ev), dur, dict(usb=False)))
    if a.leds:
        for nm, v in LEDS:
            ev, dur = recipe_events(voices(eng), 20.0, rng)
            T.append((f"{eng}-leds-{nm.lower()}", engine_ccs(eng, {}), sorted(ev), dur, dict(usb=True, leds=nm)))
    return T


def plan_text(engines, a):
    lines, total = [], 0.0
    for eng in engines:
        for name, ccs, ev, dur, opt in device_takes(eng, a):
            non = sum(1 for e in ev if e[1] == "on")
            lines.append(f"  {name:22s} {dur:6.1f} s  {len(ccs):3d} setup CCs  {non:4d} note-ons  "
                         f"{sum(1 for e in ev if e[1] == 'cc' and e[2] == 64):3d} pedal moves  voices {voices(eng)}"
                         + ("  (USB stream closed: the jack alone)" if not opt["usb"] else "")
                         + (f"  (LEDS {opt['leds']}: set on the DISPLAY page when asked)" if opt.get("leds") else ""))
            total += dur + 1.5
    head = (f"stress_max (tools/hil_crackle.py): {', '.join(engines)}, USB at {a.rate} Hz"
            + (f" blocksize {a.blocksize}" if a.blocksize else "") + (" + the jack (48 kHz)" if a.source == "both"
                                                                       else "")
            + f"; the self-test on host renders first; ~{total / 60:.1f} min on the FM-1")
    gates = (f"gates: the firmware's counters (hil_must.gate_counters: they must answer; underruns, missed, overruns 0, "
             f"the ring's low point >= a packet, the whole ISR and the engine block <= {CPU_TARGET_US} us, 0 voices "
             f"shed); 0 events ({', '.join(GATED)}) on USB and the jack")
    return "\n".join([head] + lines + [gates])


def run_takes(engines, a, dev=None, log=print):
    """every take on the FM-1 (dev) or the host build: (the report dict, its directory)"""
    rate = dev.rate if dev else FS_HOST
    rep = dict(rate=rate, blocksize=getattr(a, "blocksize", 0) if dev else None,
               source=a.source if dev else "host (the jack simulated)", engines={}, ok=True)
    od = os.path.join(OUT, (f"{rate}" + (f"-bs{a.blocksize}" if a.blocksize else "")) if dev else "host")
    os.makedirs(od, exist_ok=True)
    nsnip = 0
    for eng in engines:
        E = dict(takes=[], events=0, cpu_worst=None, whole_worst=None, shed=0, usb_counters={}, voices=None, rig=[],
                 click_lim=0, over_ceiling=0, zero_own=0, counter_fails=[], polled=bool(dev and dev.poll))
        T = device_takes(eng, a) if dev else [(n, c, e, d, dict(usb=True)) for n, c, e, d in
                                              takes(eng, a.worst_s, a.recipe_s, a.seed)]
        for name, ccs, ev, dur, opt in T:
            counters = None
            if dev:
                if opt.get("leds"):
                    input(f"  set DISPLAY > LEDS to {opt['leds']} on the FM-1, then Enter: ")
                if not opt["usb"]:
                    dev.open(usb=False)
                for attempt in (1, 2):
                    u, j, counters, rig = dev.take(ccs, ev, dur, usb=opt["usb"])
                    if not rig:
                        break
                    log(f"  {name}: the recorder slipped ({'; '.join(rig[:3])}): "
                        + ("again" if attempt == 1 else "RIG"))
                if not opt["usb"]:
                    dev.open(usb=True)
                E["rig"] += [f"{name}: {r}" for r in rig]
                fs, widen = dev.rate, DEV_JITTER
                fu = clock_fit(u, ev, fs, rate=dev.clock.get("usb"))   # (each recording's own clock: its rate
                fj = clock_fit(j, ev, FS_JACK, rate=dev.clock.get("jack"))   # from the recorder, the lag fitted)
                cu, cj = (fu["lag"], fu["rate"]), (fj["lag"], fj["rate"])
                fits = dict(usb=fu if u is not None else None, jack=fj if j is not None else None)
                if counters:
                    sm = counters["summary"]
                    E["cpu_worst"] = max(E["cpu_worst"] or 0, sm["engine_max_us"])
                    E["whole_worst"] = max(E["whole_worst"] or 0, sm["isr_max_us"])
                    E["shed"] += sm["shed"]
                    for k in ("underruns", "missed", "overruns"):
                        E["usb_counters"][k] = E["usb_counters"].get(k, 0) + sm["usb"].get(k, 0)
                    E["counter_fails"] += [f"{name}: {c} = {v} ({crit})" for c, v, crit, stt in counters["rows"]
                                           if stt == "FAIL"]
            else:
                u, txt = host_take(name, ccs, ev, dur)
                fs, cu, cj, widen, fits = FS_HOST, (0.0, 0.0), (0.0, 0.0), 0.0, None
                j = sim_jack(u)
                v = host_voices(txt, eng)
                if v:
                    E["voices"] = list(max(tuple(E["voices"] or (0, v[1])), v))
            if u is None:                                    # the jack alone: USB detectors have nothing to read
                res = dict(usb=[], jack=[], track=[], info=dict(clock_jack=[round(cj[0], 4), round(1e6 * cj[1], 1)]))
                gjt, offj = guards(ev, ccs, clock=cj), guards(ev, ccs, releases=True, clock=cj)
                gj = _guard_mask(len(j), FS_JACK, gjt, 0.0, widen)
                res["jack"] += [(i / FS_JACK, "zero", d) for i, d in det_zero(j, FS_JACK, exact=False)]
                res["jack"] += [(i / FS_JACK, "clip", d) for i, d in det_clip(j, 0.999 * 32768)]
                res["jack"] += [(i / FS_JACK, "click", d) for i, d in det_click(j, FS_JACK, gj)]
                res["jack"] += [(i / FS_JACK, "step", d) for i, d in
                                det_step(j, FS_JACK, gjt, offj, 0.0, widen, periods=am_periods(ccs))]
                res["jack"].sort()
            else:
                res = analyse(u, j, fs, ev, widen=widen, ccs=ccs, clock_usb=cu, clock_jack=cj)
            if fits:
                res["info"]["clock_fit"] = fits
            evs = gated(res)
            E["events"] += len(evs)
            E["click_lim"] += res["info"].get("click_lim", 0)
            E["over_ceiling"] += res["info"].get("over_ceiling", 0)
            E["zero_own"] += len(res["info"].get("jack_zero_own", ()))
            rec = dict(name=name, dur=round(dur, 2), usb_stream=opt["usb"], leds=opt.get("leds"),
                       events=[f"{s} {t:.3f} s {k}: {d}" for s, t, k, d in evs], info=res["info"],
                       peak_usb=None if u is None else float(np.abs(u).max()),
                       counters=counters, track_windows=len(res["track"]),
                       track_ncc_min=round(min([r[2] for r in res["track"]], default=1.0), 4))
            E["takes"].append(rec)
            for s, t, k, d in evs[:max(0, 20 - nsnip)]:
                save_snip(os.path.join(od, f"{name}-{s}-{t:.3f}-{k}.wav"), u[:, 0] if s == "usb" else j,
                          fs if s == "usb" else FS_JACK, t)
                nsnip += 1
            if dev and evs:                                  # (the whole take, to judge its events again later)
                np.savez_compressed(os.path.join(od, f"{name}-take.npz"), fs=fs, events=json.dumps(ev),
                                    ccs=json.dumps(ccs),
                                    usb=np.zeros((0, 2), np.int16) if u is None else
                                    np.clip(np.round(u), -32768, 32767).astype(np.int16),
                                    jack=np.zeros(0, np.int16) if j is None else
                                    np.clip(np.round(j), -32768, 32767).astype(np.int16))
            cw = counters["summary"]["engine_max_us"] if counters else None
            log(f"  {name:22s} {len(evs):4d} event(s)" + (f", CPU {cw} us" if cw is not None else "")
                + (f", periodic {res['info']['periodic']}" if res["info"].get("periodic") is not None else "")
                + "".join(f"\n      {x}" for x in rec["events"][:8]))
        E["ok"] = bool(E["events"] == 0 and not E["rig"] and not E["counter_fails"]
                       and (dev or (E["voices"] is not None and E["voices"][0] == E["voices"][1])))
        rep["engines"][eng] = E
        rep["ok"] &= E["ok"]
    return rep, od


def report_text(rep):
    host = rep["source"].startswith("host")
    L = [f"stress_max: USB {rep['rate']} Hz" + (f" blocksize {rep['blocksize']}" if rep.get("blocksize") else "")
         + f", {rep['source']}: {'PASS' if rep['ok'] else 'FAIL'}"]
    for eng, E in rep["engines"].items():
        if host:
            cpu, cnt = "CPU not measured (host)", "USB counters not measured (host)"
        elif not E["polled"]:
            cpu, cnt = "CPU and USB counters: gated by the caller (T1, hil_must)", ""
        else:
            cpu = (f"engine block {E['cpu_worst']} us, whole ISR {E['whole_worst']} us (target {CPU_TARGET_US}, "
                   f"shedder {SHEDDER_US}), voices shed {E['shed']}")
            cnt = (", ".join(f"{k} {v}" for k, v in E["usb_counters"].items()) +
                   (f"; counter gates FAILED: {'; '.join(E['counter_fails'][:4])}" if E["counter_fails"] else ""))
        L.append(f"{eng:8s} {'ok  ' if E['ok'] else 'FAIL'} {E['events']} event(s) in {len(E['takes'])} takes; {cpu}; "
                 f"{cnt}" + (f"; voices {E['voices'][0]} of {E['voices'][1]}" if E.get("voices") else "")
                 + f"; information: {E['click_lim']} click-lim, {E['over_ceiling']} over {CEIL}, "
                 f"{E.get('zero_own', 0)} jack quiet spell(s) USB has too"
                 + (f"; RIG: {len(E['rig'])} recorder problem(s)" if E["rig"] else ""))
        for t in E["takes"]:
            for x in t["events"][:5]:
                L.append(f"    {t['name']}: {x}")
    return "\n".join(L)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="all", choices=ENGINES + ("all",))
    ap.add_argument("--rate", type=int, default=44100, choices=(44100, 48000))
    ap.add_argument("--blocksize", type=int, default=0, help="the computer's input blocksize (0: PortAudio's)")
    ap.add_argument("--source", default="both", choices=("both", "usb"))
    ap.add_argument("--worst-s", type=float, default=180.0)
    ap.add_argument("--recipe-s", type=float, default=120.0)
    ap.add_argument("--no-usb-s", type=float, default=60.0, help="the jack-alone recipe (0: none)")
    ap.add_argument("--leds", action="store_true", help="the LEDS OFF / PLAYED / ALL takes (asks for each)")
    ap.add_argument("--quick", action="store_true", help="20 s worst + 24 s recipe per engine")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-self-test", action="store_true", help="(device) skip the self-test before the takes")
    a = ap.parse_args(argv)
    if a.quick:
        a.worst_s, a.recipe_s, a.no_usb_s = 20.0, 24.0, min(a.no_usb_s, 20.0)
    return a


def main(argv=None):
    a = parse_args(argv)
    engines = ENGINES if a.engine == "all" else (a.engine,)
    if a.dry_run:
        print(plan_text(engines, a))
        return 0
    if a.self_test:
        _, ok = self_test(engines)
        print("self-test: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    os.makedirs(OUT, exist_ok=True)
    if a.host:
        rep, od = run_takes(engines, a)
    else:
        if not a.no_self_test:
            _, ok = self_test(engines)
            if not ok:
                print("self-test FAILED: the detectors are not proven; no device run")
                return 1
        print(plan_text(engines, a))
        try:
            dev = Device(a.rate, a.source == "both", a.jack_dev, a.jack_ch, a.blocksize)
        except RateError as e:
            print(f"stress_max --rate {a.rate}: FAIL before any take: {e}")
            return 1
        rep, od = run_takes(engines, a, dev)
        dev.cap.close()
    txt = report_text(rep)
    print(txt)
    open(os.path.join(od, "report.txt"), "w").write(txt + "\n")
    json.dump(rep, open(os.path.join(od, "report.json"), "w"), indent=1, default=str)
    return 0 if rep["ok"] else 1


def t1(r=None, rep=None, rate=48000, seconds=None, quick=False, tier="must", engines=ENGINES, log=print):
    """hil_scan TESTS["stress_max"], called by hil_must's S1 per rate (keywords it accepts: rate, seconds, quick,
    tier): every engine on the FM-1 at that rate with the jack when the interface is there, the counters left to
    hil_must (it polls around the whole call; a second poller would split the replies). seconds: the worst-case and
    recipe time per engine (0.6 / 0.4 of it); without it the --quick lengths. Returns (ok, message)"""
    a = parse_args(["--rate", str(rate), "--no-usb-s", "0"] + ([] if seconds else ["--quick"]))
    if seconds:
        a.worst_s, a.recipe_s = 0.6 * seconds, 0.4 * seconds
    import sounddevice as sd
    jack = any(d["name"] == a.jack_dev and d["max_input_channels"] >= a.jack_ch for d in sd.query_devices())
    a.source = "both" if jack else "usb"
    try:
        dev = Device(a.rate, jack, a.jack_dev, a.jack_ch, poll=False)
    except RateError as e:                           # (the device not at rate: no take means anything)
        return False, f"stress_max {rate} Hz: FAIL before any take: {e}"
    try:
        res, od = run_takes(engines, a, dev, log=log)
    finally:
        dev.cap.close()
    txt = report_text(res)
    open(os.path.join(od, "report.txt"), "w").write(txt + "\n")
    json.dump(res, open(os.path.join(od, "report.json"), "w"), indent=1, default=str)
    if rep is not None:
        rep["stress_max"] = res
    n = sum(E["events"] for E in res["engines"].values())
    return res["ok"], (f"stress_max {rate} Hz ({'USB + jack' if jack else 'USB only: no jack interface'}): "
                       f"{n} glitch event(s); " + txt.split("\n")[0] + f" ({os.path.relpath(od, ROOT)})")


if __name__ == "__main__":
    sys.exit(main())
