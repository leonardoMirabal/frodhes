#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Hardware-in-the-loop scan of a connected FM-1 running Frodhes: MIDI in, USB audio back.

The FM-1's USB audio is exactly what its output plays (after MASTER), so this checks the real thing:
silence, latency, every key (level, tuning), velocity, the TYPEs, every effect (level against dry,
clipping, what it is for), the bypass, sustain, the held-note tail, polyphony stress (dropouts) and
every factory preset. Captures and a report go to build/hil/.

  tools/hil_scan.py [--only NAME[,NAME]] [--list]
  tools/hil_scan.py --tier must [--fwsc PKG] [--dry-run]    T1: the release's must-pass scenarios (tools/hil_must.py)
  tools/hil_scan.py --tier full [options]                  T2: every classic, VIBES and MARIMBA suite
  tools/hil_scan.py --only stress_max [--rate 44100|48000] [--source both|usb] [--host | --dry-run]
                                        the cracks test (tools/hil_crackle.py: every engine, minutes of the worst case,
                                        USB + the jack; in T1; not in the default run)

The VIBES suites (not in the default run; every VIBES control at min / default / max with an audible-effect limit,
the effects' loudness jumps, recorded on USB and on the headphone jack through an interface at once, side by side):

  tools/hil_scan.py --vibes [--source usb|jack|both] [--jack-dev M6] [--jack-ch 5] [--wav]
  tools/hil_scan.py --only vibes_jump,vibes_fx ...      (vibes_jump vibes_voice vibes_decay vibes_motor vibes_vel
                                                         vibes_fx; report build/hil/vibes-report.md + .json)
  tools/hil_scan.py --vibes --dry-run                   the planned events and the time estimate; no MIDI / audio

Before: MASTER fully up and left alone. The scan changes the current panel settings (not the user
presets); it ends on F01. Needs mido + python-rtmidi, sounddevice, numpy.

Determinism (2026-10-18): the same firmware scanned twice gives the same verdicts. Before, free-running parts started
each take at another phase (the tremolo / phaser / chorus / filter LFOs, the tape's wow, the reverb's modulation, the
VIBES motor, the engines' noise) and a delay / reverb line could still hold the last take: 'types' read a minimum
spectral distance of 0.9 dB in one run and 1.8 in the next, 'presets' spanned 7.5 / 6.7 / 5.3 LU, fx_all's extremes
flipped. Now:
  - the developer reset, MIDI CC 10 >= 64 (fx.c fx_reset_state: every LFO, the delay / chorus / pre-delay / reverb
    lines, the phaser, the noise generators, the motor back to their boot state at the next audio block; every voice
    ends and the pickup RLC / body states go to 0, so no tail under the gate meets the next take; the delay line is
    cleared over the next ~16 blocks, with no heavy block), goes out
    RESET_LEAD (50 ms) before the first note of every take (with_reset(); classic Rig.take, VRig.take, fx_all's jack
    takes), after the settings, and once more at the end of every r.dry() / V.reset() (which also empties the delay
    line: the VIBES reset's 1.6 s DEPTH-0 flush is gone). What is left is the note's arrival within an audio block:
    the reset-to-note gap is a whole number of 256-frame blocks, +-1 block (5.8 ms) of MIDI / scheduling jitter, so an
    LFO's phase at the note moves by at most 5.8 ms of its period (10 degrees of the 4.75 Hz tremolo);
  - every window is anchored on the measured onset (Rig.lag(), align()), never on the recording's start (the USB
    capture starts 10-60 ms off the MIDI clock from take to take: 'types' cut its windows into the attack at another
    place each time; sustain, held_tail, tremolo_rate, fx_toggle, stress did the same);
  - a level over a modulated sound is taken over whole cycles of its one LFO where they fit (cyc_win(): effects,
    fx_all; fx_toggle's 0.4 s windows become whole cycles in 0.38-0.6 s, as vibes_jump);
  - the noisy ones take the median of REPEATS takes (types 3, presets 3, fx_all 2; --repeats N overrides) and report
    the takes' own spread next to the verdict;
  - the classic chords hold no octave (CHORD A3 C4 E4, was A3 E4 A4; fx_all / fx_toggle C3 G3 E4, was C3 G3 C4 E4):
    an octave's coinciding partials sum at the phase the notes' onsets set, and one note landing a block late moved
    a preset's loudness by up to 2.0 LU (the main cause of 'presets' 7.5 / 6.7 / 5.3); now <= 0.2 LU;
  - 'types' sums the two notes' band powers instead of joining the two windows end to end (the junction's step
    splashed broadband into the gaps between the partials: up to 0.8 dB of the distance from take to take).
One recording stream for the whole run (2026-10-18): the FM-1 held its automatic flash saves (the settings 2 s after a
change, the sequencer's pattern) back while a computer read its USB audio, because a save held the interrupts off
~24 ms, no USB audio packet went out and macOS then handed ~250 ms of stale input (since the split erase, 0.9.5,
fm1_flash.h fl_split_op: slices of ~0.5 ms, and the saves go while the stream runs). The scan
used to open a new input stream per take and per silence poll, so between them nobody read the audio and a save could
land at the start of the next take. Now Rig opens one stream per device (Capture: the FM-1's USB, and the jack's
interface input where a suite records it) at the first take and keeps it running; each take is cut out of it by the
streams' ADC times (Ring.cut, tests/py/test_hil_capture.py). Rig.persistent = False brings back a stream per take
(tools/hil_usb_onset.py, which measures exactly that case).
Expected run-to-run spread (host, block jitter alone, tests/py/test_hil_determinism.py: types' distances <= 0.05 dB,
presets <= 0.04 LU, effects / fx_all / fx_toggle <= 0.09 LU; without CC 10 presets moved 4.6 LU). On the device add
the USB capture (44.1 -> 48 kHz, stream slips): expect types +-0.1 dB, presets +-0.2 LU, the others +-0.3 LU; a
verdict closer than that to its limit is a real borderline, not noise. The same checks run on the host build: tools/hil_scan.py --host [SEED] --only types,presets (no FM-1;
report build/hil/host-report.txt; a VIBES suite, e.g. --only vibes_fx, too: USB only, the FM-1's default 120 BPM,
report build/hil/vibes-host.md): each take a fresh host run that replays the panel's CCs, idles a random 0.3-1.3 s
(the LFOs run on, as on the device between takes) and plays the take 3-25 ms late (MIDI + USB latency).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil")
PORT_KEYS = ("frodhes", "felucca", "fm-1")   # the FM-1's MIDI port / USB audio names: Frodhes, Felucca before
FS = 48000
A4 = 440.0

# the CC map (firmware/src/params.c)
CC = dict(TYPE=80, DRIVE=81, TW_SW=17, TW_DEPTH=18, TW_RATE=19, TW_MODE=21, CP_SW=85, CP_DEPTH=86, CP_SPEED=87,
          DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26, REV=91, PH_SW=12, PH_DEPTH=13, PH_SPEED=14, PH_FDBK=16,
          CMP_SW=106, CMP_AMT=107, CMP_ATK=108, CMP_REL=109, NOISE=104, SUS=64, RAW=119, CMP_RAT=112, CMP_GAIN=113,
          PH_SPEED2=14, CP_SPEED2=87, TW_SHAPE=22, DL_TONE=27, LEVEL=110, BYPASS=111, ARP=52, CHORD=56, SEQ=60, SQREC=63,
          FL_TYPE=70, FL_FREQ=74, FL_RESO=71, FL_LFO=72)
DRY = dict(DRIVE=0, REV=0, TW_SW=0, CP_SW=0, PH_SW=0, DL_SW=0, CMP_SW=0, RAW=0, SUS=0, ARP=0, CHORD=0, SQREC=0, SEQ=0,
           FL_TYPE=0)
# (ARP, CHORD, SEQ, SQREC: the arpeggiator, chord mode, the sequencer and its recording off)


RHODES_TYPES = 4           # TYPE 0..3 (ST77 SUIT FELT DYNO); 4.. are bar types (VIBES), scanned by their own tests


def n_types():
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    return int(re.search(r'\[P_TYPE\] = \{"[^"]*", (\d+),', src).group(1)) + 1


def type_cc(t):
    """the CC value selecting TYPE t on the CC that type_ccnum(t) names: CC 80 (by 32) for the Rhodes types 0..3 as
    before, CC 79 for the bar types (frodhes.c midi_cc, params.c TYPE_CC79): VIBES 0..95, MARIMBA 96..127; VIBES is
    sent as 64 (its 0.9.3 value), MARIMBA as 127"""
    if t < 4:
        return t * 32 + 8
    return {4: 64, 5: 127}[t]


def type_ccnum(t):
    return 80 if t < 4 else 79


def drv_cc(x):
    """the CC 84 value that selects drive TYPE x (params.c DRV_CC84, frodhes.c midi_cc: a band table, the CC 79 way):
    OFF 0, FDRV 127 (as off / on before), any other TYPE the middle of its band"""
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    lo = [int(v) for v in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", src).group(1).split(",")]
    if x <= 1:
        return 127 * int(x)
    ends = sorted(lo) + [128]
    return (lo[x] + ends[ends.index(lo[x]) + 1] - 1) // 2


def param_defaults():
    """every parameter's CC and the CC value that sets its default (parsed from firmware/src/params.c), so each test
    starts from the same state whatever the FM-1 had saved"""
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    out = []
    for name, hi, de, cc in re.findall(r'\[P_(\w+)\] = \{"[^"]*", (\d+), (\d+), F_\w+, (\d+)', src):
        hi, de, cc = int(hi), int(de), int(cc)
        if not cc:                                   # (records v3: a parameter without a CC yet)
            continue
        if name == "TYPE":
            v = type_cc(de)
        elif name == "DRV_SW":
            v = drv_cc(de)
        elif hi == 2:
            v = (0, 64, 127)[de]
        elif hi < 127:
            v = min(127, (de * 128 + 64) // (hi + 1))
        else:
            v = de
        out.append((cc, v))
    return out


def db(x):
    x = np.asarray(x, float)
    return 20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12) if x.size else -240.0


RESET_CC, RESET_LEAD = 10, 0.05   # the developer reset (fx.c fx_reset_state) and how long before a take's first note
REPEATS = dict(types=3, presets=3, fx_all=2)   # takes per measure, their median judged (--repeats N: all of them)


def with_reset(events, lead=RESET_LEAD):
    """the take's events with CC 10 = 127 lead s before its first note-on (at 0 when the note comes sooner): every LFO,
    line and noise generator starts the note from its boot state, +-1 audio block"""
    on = [e[0] for e in events if e[1] == "on"]
    if not on:
        return list(events)
    return sorted(list(events) + [(max(0.0, min(on) - lead), "cc", RESET_CC, 127)], key=lambda e: e[0])


def reps(name):
    return max(1, int(REPEATS.get(name, 1)))


class _Msg:
    """a stand-in for mido.Message (HostRig: no mido needed)"""
    def __init__(self, type, **kw):
        self.type = type
        self.note = self.velocity = self.control = self.value = self.program = 0
        self.__dict__.update(kw)


class _Mido:
    Message = _Msg


class RigBase:
    """what the classic suites call: the panel (cc / dry / setup / pc ...) on _send(), takes on _rec(); the device
    (Rig) or the host build (HostRig)"""
    reset = True                                         # CC 10 before every take's first note

    def _send(self, kind, a, b=0):
        raise NotImplementedError

    def send(self, msg):                                 # r.out.send(r.mido.Message(...)) as before
        k = msg.type
        if k == "note_on":
            self._send("on", msg.note, msg.velocity)
        elif k == "note_off":
            self._send("off", msg.note, 0)
        elif k == "control_change":
            self._send("cc", msg.control, msg.value)
        elif k == "program_change":
            self._send("pc", msg.program, 0)

    def cc(self, name, v):
        self._send("cc", CC[name], int(v))

    def cc_raw(self, n, v):
        self._send("cc", int(n), int(v))

    def pc(self, p):
        self._send("pc", int(p), 0)

    def note_off(self, n):
        self._send("off", int(n), 0)

    def fx_reset(self):
        """CC 10: the effects' and engines' free-running state to boot (at the next audio block)"""
        self._send("cc", RESET_CC, 127)

    def setup(self, **kv):
        for k, v in kv.items():
            self.cc(k, v)
        time.sleep(0.05)

    def settle(self):
        """the reset, then the gate's exact silence (the reset empties the delay / reverb lines: no tail to wait out)"""
        self.fx_reset()
        time.sleep(0.02)
        return self.wait_silence()

    def dry(self, **kv):
        self._send("cc", 120, 0)
        if not hasattr(self, "defaults"):
            self.defaults = param_defaults()
        for cc, v in self.defaults:                    # a known state: every parameter at its default
            self._send("cc", cc, v)
        d = dict(DRY)
        d.update(kv)
        self.setup(**d)
        self.settle()

    def take(self, dur, events):
        """record dur s; events: [(t_s, kind, a, b)], kind on/off/cc/pc; CC 10 goes out RESET_LEAD before the first
        note (self.reset)"""
        ev = with_reset(events) if self.reset else sorted(events, key=lambda e: e[0])
        return self._rec(dur, ev)

    @staticmethod
    def lag(x, events, thr=200):
        """samples from the take's event clock to its recording: the first sound's onset minus the first note-on's
        time (the USB capture starts 10-60 ms off the MIDI clock, differently each take; 0 when nothing sounds)"""
        on = [e[0] for e in events if e[1] == "on"]
        i = onset(x, thr, min(on)) if on else -1
        return i - int(round(min(on) * FS)) if i >= 0 else 0


class Ring:
    """one input stream's recent history (secs at the device's own rate, float32; only the channels kept): every
    callback block pushed with its ADC time (PortAudio's stream clock), so a take is cut by TIME, not by stream start.
    The persistent recorder (Capture) keeps the FM-1's USB stream open for a whole suite: the FM-1 held its automatic
    flash saves back while a computer read its USB audio (before the split erase, fm1_flash.h), and a
    stream opened per take let 'flowing' drop between takes, so a save (interrupts off ~24 ms: no USB packet, macOS
    then hands ~250 ms of stale input) landed at the start of the next take"""

    def __init__(self, sr, ch, secs=120.0):
        self.sr, self.ch = int(sr), int(ch)
        self.N = int(secs * sr)
        self.buf = np.zeros((self.N, ch), np.float32)
        self.w = 0                                   # frames pushed so far (absolute)
        import threading
        from collections import deque
        self.lock = threading.Lock()
        # the block log: (absolute frame, ADC time of that frame) in two preallocated rings, nlog entries so far. Not
        # a deque turned into an array per cut: np.array() of the full deque (180,000 tuples at 48 kHz, 120 s) took
        # ~18 ms with the lock and the GIL held, so the PortAudio callback stalled and CoreAudio dropped USB input
        # (the 13-27 ms "gaps" right after a take's last event, while the take was polled every 10 ms; 2026-10-19).
        # A cut copies only the entries from its start (_snap): microseconds under the lock
        self.M = max(64, self.N // 32)
        self.lf = np.zeros(self.M, np.int64)
        self.la = np.zeros(self.M, np.float64)
        self.nlog = 0
        self.status = deque(maxlen=1000)             # (ADC time, PortAudio status flags)
        self.gaps = []

    def push(self, block, adc, status=None):
        """a callback's frames (n, ch) whose first was captured at adc (s, the stream clock)"""
        n = len(block)
        if not n:
            return
        if n > self.N:
            block, adc = block[-self.N:], adc + (n - self.N) / self.sr
            self.w += n - self.N
            n = self.N
        i = self.w % self.N
        k = min(n, self.N - i)
        self.buf[i:i + k] = block[:k]
        if k < n:
            self.buf[:n - k] = block[k:]
        with self.lock:
            j = self.nlog % self.M
            self.lf[j], self.la[j] = self.w, float(adc)
            self.nlog += 1
            if status:
                self.status.append((float(adc), str(status)))
            self.w += n

    def _snap(self, t0, back=64):
        """(L, w): the log from the last entry at or before stream time t0 (less `back` entries of margin: ADC times
        may jitter out of order) to the newest, as an (n, 2) float array [frame, ADC time], and the frames pushed;
        (None, w) while empty. O(log n) under the lock plus the copy of the entries after t0"""
        with self.lock:
            c, w = self.nlog, self.w
            lo = max(0, c - self.M)
            if c == lo:
                return None, w
            a, b = lo, c                             # the first logical index whose ADC time is > t0
            while a < b:
                m = (a + b) // 2
                if self.la[m % self.M] <= t0:
                    a = m + 1
                else:
                    b = m
            idx = np.arange(max(lo, a - 1 - back), c) % self.M
            L = np.empty((len(idx), 2), float)
            L[:, 0], L[:, 1] = self.lf[idx], self.la[idx]
        return L, w

    def frame_at(self, t):
        """the absolute frame captured at stream time t: the blocks' ADC times interpolated, extrapolated past the ends
        at the nominal rate"""
        L, _ = self._snap(t)
        if L is None:
            return None
        if t <= L[0, 1]:
            return L[0, 0] + (t - L[0, 1]) * self.sr
        if t >= L[-1, 1]:
            return L[-1, 0] + (t - L[-1, 1]) * self.sr
        return float(np.interp(t, L[:, 1], L[:, 0]))

    def rate(self, t0, dur):
        """the stream's true frames per second of the stream clock (the computer's) over [t0, t0 + dur], from the
        blocks' ADC times: the median slope across half the span (the ADC times jitter by ms; thousands of blocks), or
        None with fewer than 32 blocks. cut() lays the frames end to end at the nominal rate, so a take's audio runs
        rate / sr - 1 off the event clock (the FM-1's USB ~+540..760 ppm on the MOTU rig, 2026-10-24:
        tools/hil_crackle.py maps its guards with it)"""
        L, _ = self._snap(t0)
        if L is None:
            return None
        L = L[(L[:, 1] >= t0) & (L[:, 1] <= t0 + dur)]
        if len(L) < 32:
            return None
        h = len(L) // 2
        dt = L[h:2 * h, 1] - L[:h, 1]
        ok = dt > 0
        return float(np.median((L[h:2 * h, 0] - L[:h, 0])[ok] / dt[ok])) if ok.any() else None

    def end_time(self):
        """the stream time of the last frame held (None before the first block)"""
        with self.lock:
            if not self.nlog:
                return None
            j, w = (self.nlog - 1) % self.M, self.w
            f, a = int(self.lf[j]), float(self.la[j])
        return a + (w - 1 - f) / self.sr

    def cut(self, t0, dur, tol=0.006):
        """the frames from stream time t0 for dur s ((n, ch), n = dur * sr), or None while they are not all in yet.
        Placed by the blocks' ADC times: the first block anchors the take, the rest follow on contiguously; where a
        block's ADC time steps more than tol s off its predecessor's length (input the stream never delivered, or a
        block delivered twice) the take re-anchors there: the missing time stays zeros, a repeat is overwritten, so
        what comes after is still on the event clock (tol 6 ms: on the FM-1 the ADC times jitter up to ~3 ms from block
        to block, 2026-10-18; a lost USB block is >= 11.6 ms). self.gaps: [(stream time, s)] of the last cut. Frames
        that already left the ring (or came before the first block) read as zeros"""
        L, w = self._snap(t0)
        n = int(round(dur * self.sr))
        if L is None:
            return None
        if L[-1, 1] + (w - 1 - L[-1, 0]) / self.sr < t0 + (n - 1) / self.sr:
            return None
        ends = np.append(L[1:, 0], w)
        k0 = max(0, int(np.searchsorted(L[:, 1], t0, "right")) - 1)
        out = np.zeros((n, self.ch), np.float32)
        self.gaps = []
        pos = None
        for k in range(k0, len(L)):
            a, f0, f1 = L[k, 1], int(L[k, 0]), int(ends[k])
            if pos is None:
                pos = int(round((a - t0) * self.sr))
            else:
                step = a - L[k - 1, 1] - (f0 - L[k - 1, 0]) / self.sr
                if abs(step) > tol:
                    self.gaps.append((float(a), float(step)))
                    pos += int(round(step * self.sr))
            if pos >= n:
                break
            lo, hi = max(f0, w - self.N, f0 - pos), min(f1, f0 + n - pos)   # (frames still held, inside the take)
            if hi > lo:
                out[pos + lo - f0:pos + hi - f0] = self.buf[np.arange(lo, hi) % self.N]
            pos += f1 - f0
        return out

    def flags_between(self, t0, t1):
        with self.lock:
            st = list(self.status)
        return [s for a, s in st if t0 <= a <= t1]


class Capture:
    """the persistent recorder: one InputStream per device (the FM-1's USB at its own rate, stereo; the jack interface
    at its own, one channel kept), opened once and left running for the whole run; each take is a slice of it by the
    streams' ADC times (Ring.cut). The callbacks only copy; everything else runs in the caller's thread"""

    def __init__(self, sd):
        self.sd, self.rings, self.streams, self.keep = sd, {}, {}, {}

    def add(self, key, dev, ch_keep, n_in, secs=120.0, sr=None, blocksize=0, switch_rate=False):
        """sr: open the stream at this rate (tools/hil_crackle.py: the FM-1 at 44.1 kHz for exact samples), else the
        device's default; blocksize: the callback's frames (0: PortAudio's choice); switch_rate: the device itself
        switched to sr (Core Audio, as hil_cpu --usb): without it Core Audio keeps the device at its nominal rate and
        converts, and the FM-1 runs the other rate's path"""
        sd = self.sd
        sr = int(sr or sd.query_devices(dev)["default_samplerate"])
        ring = Ring(sr, len(ch_keep), secs)
        sel = list(ch_keep)

        def cb(indata, frames, ti, status):
            adc = ti.inputBufferAdcTime or (ti.currentTime - frames / sr)
            ring.push(indata[:, sel], adc, status)
        kw = {"blocksize": int(blocksize)} if blocksize else {}
        if switch_rate:
            kw["extra_settings"] = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True)
        s = sd.InputStream(device=dev, samplerate=sr, channels=n_in, dtype="float32", callback=cb, **kw)
        s.start()
        self.rings[key], self.streams[key] = ring, s
        t0 = time.monotonic()
        while ring.end_time() is None and time.monotonic() - t0 < 5:
            time.sleep(0.01)
        return ring

    def now(self):
        """every stream's clock now (s), {key: stream.time}"""
        return {k: s.time for k, s in self.streams.items()}

    def cut(self, key, t0, dur, timeout=5.0):
        """the take's frames from stream time t0 (a now() stamp), waiting until they are in"""
        r = self.rings[key]
        lim = time.monotonic() + dur + timeout
        while True:
            x = r.cut(t0, dur)
            if x is not None:
                return x
            if time.monotonic() > lim:
                raise RuntimeError(f"{key}: the stream stopped delivering ({r.w} frames)")
            time.sleep(0.01)

    def close(self):
        for s in self.streams.values():
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.streams = {}


class Rig(RigBase):
    """the FM-1 over USB-MIDI + its USB audio (and an interface input recording the headphone jack, add_jack()).
    persistent (default): one input stream per device for the whole run (Capture), every take and every silence poll
    a slice of it by the ADC time, so the FM-1 sees a computer reading its audio the whole time and keeps its
    automatic flash saves back (no save inside a take). persistent = False: a stream per take / poll, as before
    2026-10-18 (tools/hil_usb_onset.py measures exactly that case; rh_devcmp.py switches the device's rate)"""
    persistent = True

    def __init__(self):
        import mido
        import sounddevice as sd
        self.sd = sd
        names = [n for n in mido.get_output_names() if any(k in n.lower() for k in PORT_KEYS)]
        if not names:
            sys.exit("no FM-1 MIDI port")
        self.out = mido.open_output(names[0])
        self.mido = mido
        devs = [i for i, d in enumerate(sd.query_devices())
                if any(k in d["name"].lower() for k in PORT_KEYS[:2]) and d["max_input_channels"] >= 2]
        if not devs:
            sys.exit("no FM-1 audio input")
        self.dev = devs[0]
        self.cap, self.xruns = None, []

    def capture(self):
        """the persistent recorder, opened at the first use (the FM-1's USB, stereo)"""
        if self.cap is None:
            import atexit
            self.cap = Capture(self.sd)
            self.cap.add("usb", self.dev, (0, 1), 2)
            atexit.register(self.cap.close)
        return self.cap

    def add_jack(self, dev, ch0):
        """the jack's interface input ch0 (0-based) into the persistent recorder too"""
        cap = self.capture()
        if "jack" not in cap.rings:
            cap.add("jack", dev, (ch0,), int(self.sd.query_devices(dev)["max_input_channels"]))
        return cap

    def jack(self, name="M6", ch=5):
        """the headphone jack on the interface 'name' input ch (1-based; hil_jack's defaults) into the persistent
        recorder when it is connected: True if it is there"""
        if not hasattr(self, "_jack"):
            self._jack = False
            if self.persistent:
                devs = self.sd.query_devices()
                js = [i for i, d in enumerate(devs) if d["name"] == name and d["max_input_channels"] >= ch]
                if js:
                    self.add_jack(js[0], ch - 1)
                    self._jack = True
        return self._jack

    def _send(self, kind, a, b=0):
        M = self.mido.Message
        self.out.send(M("note_on", note=a, velocity=b) if kind == "on" else M("note_off", note=a) if kind == "off" else
                      M("program_change", program=a) if kind == "pc" else M("control_change", control=a, value=b))

    def wait_silence(self, limit=10.0):
        """until the output is exact zero (the gate), so no delay / reverb tail of the last test leaks in"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit:
            if self.persistent:
                cap = self.capture()
                x = cap.cut("usb", cap.now()["usb"], 0.3)
            else:
                x = self.sd.rec(int(0.3 * FS), samplerate=FS, channels=2, device=self.dev, dtype="int16")
                self.sd.wait()
            if not np.any(x):
                return True
        return False

    def both(self, dur, events, jack=False):
        """dur s from now while the events go out on time: {"usb": (n, 2), "jack": (n, 1) with jack} at FS, full scale
        1.0 (the persistent recorder; each source cut from ITS stream's clock at the moment the event clock starts).
        self.xruns: PortAudio's status flags inside the take"""
        cap = self.capture()
        keys = ["usb"] + (["jack"] if jack else [])
        st = cap.now()
        t0 = time.monotonic()
        for t, kind, a, b in sorted(events, key=lambda e: e[0]):
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            self._send(kind, a, b)
        out = {k: to_fs(cap.cut(k, st[k], dur).astype(float), cap.rings[k].sr, dur) for k in keys}
        self.xruns = [(k, f) for k in keys for f in cap.rings[k].flags_between(st[k], st[k] + dur)] + \
                     [(k, f"gap {1000 * g:+.0f} ms at {a - st[k]:.2f} s") for k in keys for a, g in cap.rings[k].gaps]
        return out

    def _rec(self, dur, events):
        if self.persistent:
            return self.both(dur, events)["usb"] * 32768
        rec = self.sd.rec(int(dur * FS), samplerate=FS, channels=2, device=self.dev, dtype="int16")
        t0 = time.monotonic()
        for t, kind, a, b in events:
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            self._send(kind, a, b)
        self.sd.wait()
        return rec.astype(float)


class HostRig(RigBase):
    """the classic suites on the host build (tests/host.c through tests/py/hostrun.py), as the device would play them:
    each take is a fresh host run that replays the panel's state (every CC / program change sent so far, the last of
    each since the last program change), idles a random preroll (the LFOs run on, as on the FM-1 between takes; a
    take without the reset starts them at another phase) and plays the take lat ms late (MIDI + USB latency; +-1 ms
    of jitter per event), then resamples 44.1 -> 48 kHz. seed: the prerolls and latencies; reset=False: no CC 10"""

    def __init__(self, seed=0, reset=True, preroll=(300, 1300), lat=(3, 25), tag="hil"):
        self.rng = np.random.default_rng(seed)
        self.reset, self.preroll, self.latr, self.tag = reset, preroll, lat, f"{tag}-{seed}-{int(reset)}"
        self.hist, self.n, self.mido, self.out, self.dev = [], 0, _Mido, self, None

    def _send(self, kind, a, b=0):
        if kind == "pc":
            self.hist.append((0xC0, a, 0))
        elif kind == "cc" and a not in (RESET_CC, 120):     # (no note sounds outside a take)
            last_pc = max([k for k, h in enumerate(self.hist) if h[0] == 0xC0], default=-1)
            self.hist = [h for k, h in enumerate(self.hist) if k <= last_pc or h[:2] != (0xB0, a)] + [(0xB0, a, b)]

    def wait_silence(self, limit=10.0):
        return True

    def _rec(self, dur, events):
        from scipy.signal import resample_poly
        H = hr()
        lines = [f"{5 + k // 16} midi 0x{s:02X} {a} {b}" if s != 0xC0 else f"{5 + k // 16} midi 0xC0 {a}"
                 for k, (s, a, b) in enumerate(self.hist)]
        R = max(5 + len(self.hist) // 16 + 100, int(self.rng.integers(*self.preroll)))
        L = float(self.rng.uniform(*self.latr))
        for t, kind, a, b in events:
            ms = int(round(R + L + 1000 * t + self.rng.uniform(-1, 1)))
            if kind == "on":
                lines.append(f"{ms} midi 0x90 {a} {b}")
            elif kind == "off":
                lines.append(f"{ms} midi 0x80 {a} 0")
            elif kind == "cc":
                lines.append(f"{ms} midi 0xB0 {a} {b}")
                if a != RESET_CC:                          # (the take's CCs stay set, as on the device)
                    self._send("cc", a, b)
            elif kind == "pc":
                lines.append(f"{ms} midi 0xC0 {a}")
                self._send("pc", a)
        self.n += 1
        run = H.run(f"{self.tag}-{self.n}", lines, R + int(np.ceil(1000 * dur)) + 20)
        i0 = int(R * H.FS / 1000)
        x = np.stack([run.L[i0:], run.R[i0:]], 1)
        x = resample_poly(x, 160, 147, axis=0)             # 44.1 -> 48 kHz
        n = int(dur * FS)
        return np.round(np.pad(x, ((0, max(0, n - len(x))), (0, 0)))[:n])


def onset(x, thr=200, t=0.0):
    """the first sample over thr (LSB), searched from 5 ms before event time t (s): a release tail of the last take
    before its first note is not its onset"""
    s = max(0, int((t - 0.005) * FS))
    a = np.abs(x[s:]).max(axis=1) if x.ndim == 2 else np.abs(x[s:])
    if not len(a):
        return -1
    i = np.argmax(a > thr)
    return int(i) + s if a[i] > thr else -1


def f0_of(x, guess):
    """fundamental near guess (Hz): FFT peak, parabolic refinement"""
    n = len(x)
    w = np.hanning(n)
    sp = np.abs(np.fft.rfft(x * w, 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / FS)
    m = (fr > guess * 0.94) & (fr < guess * 1.06)
    if not m.any():
        return 0.0
    i = np.flatnonzero(m)[np.argmax(sp[m])]
    if 0 < i < len(sp) - 1:
        a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
        p = 0.5 * (a - c) / (a - 2 * b + c)
        return (i + p) * FS / (8 * n)
    return fr[i]


def centroid(x):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return float((sp * fr).sum() / (sp.sum() + 1e-12))


def bands_db(x):
    """third-octave band levels (dB) 100 Hz .. 10 kHz of a mono signal"""
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / FS)
    c = 100 * 2 ** (np.arange(0, 21) / 3)
    return np.array([10 * np.log10(sp[(f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))].sum() + 1e-12) for fc in c])


def clicks(x):
    """dropout / click detector: second difference spikes far above the local level"""
    m = x.mean(axis=1) if x.ndim == 2 else x
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def zero_runs(x, lo=0.05, hi=None):
    """runs of exact zeros (>= 64 samples) inside a stretch that should be sounding"""
    m = np.abs(x).max(axis=1)
    z = (m == 0).astype(int)
    runs, n = 0, 0
    for v in z:
        n = n + 1 if v else 0
        if n == 64:
            runs += 1
    return runs


TESTS = {}


def test(fn):
    TESTS[fn.__name__] = fn
    return fn


@test
def silence(r, rep):
    r.dry()
    x = r.take(2.0, [])
    rep["silence"] = dict(peak=float(np.abs(x).max()), rms_db=db(x), nonzero=int((x != 0).sum()))
    ok = rep["silence"]["peak"] == 0
    return ok, f"peak {rep['silence']['peak']:.0f} LSB, {rep['silence']['nonzero']} non-zero samples"


LATENCY_BEFORE_MS = 12.5      # MANUAL's measured MIDI -> USB latency before the USB ring kept one block more (+5.8 ms)


@test
def latency(r, rep):
    r.dry()
    lat = []
    for k in range(5):
        x = r.take(0.8, [(0.2, "on", 72, 110), (0.5, "off", 72, 0)])
        i = onset(x, t=0.2)
        if i > 0:
            lat.append((i / FS - 0.2) * 1000)
    rep["latency_ms"] = lat
    if not lat:
        return False, "no onset"
    return True, (f"MIDI -> USB audio {np.median(lat):.1f} ms (min {min(lat):.1f}, max {max(lat):.1f}); includes USB + host "
                  f"buffers (the USB ring holds a block more since the click fix: ~{LATENCY_BEFORE_MS + 5.8:.0f} ms expected, "
                  f"{LATENCY_BEFORE_MS} before)")


@test
def keyboard(r, rep):
    r.dry()
    rows = []
    for n in range(28, 104):
        x = r.take(0.75, [(0.05, "on", n, 100), (0.6, "off", n, 0)])
        i = onset(x, 100, 0.05)
        if i < 0:
            rows.append(dict(note=n, level=-240.0, cents=None, peak=0.0))
            continue
        seg = x[i + int(0.05 * FS):i + int(0.45 * FS), 0]
        f = A4 * 2 ** ((n - 69) / 12)
        fm = f0_of(seg, f)
        rows.append(dict(note=n, level=db(x[i:i + int(0.3 * FS)]), cents=1200 * np.log2(fm / f) if fm else None,
                         peak=float(np.abs(x).max()), centroid=centroid(seg)))
    rep["keyboard"] = rows
    lv = np.array([q["level"] for q in rows])
    jumps = [(rows[k]["note"], round(lv[k] - (lv[k - 1] + lv[k + 1]) / 2, 1)) for k in range(1, len(rows) - 1)
             if abs(lv[k] - (lv[k - 1] + lv[k + 1]) / 2) > 3]
    cents = [abs(q["cents"]) for q in rows if q["cents"] is not None]
    dead = [q["note"] for q in rows if q["level"] < -70]
    rep["keyboard_summary"] = dict(jumps=jumps, worst_cents=max(cents) if cents else None, dead=dead,
                                   level_span=float(lv.max() - lv[lv > -100].min()))
    ok = not dead and not jumps and (max(cents) < 15 if cents else False)
    return ok, (f"76 keys: level span {rep['keyboard_summary']['level_span']:.1f} dB, "
                f"uneven keys {jumps or 'none'}, worst tuning {max(cents):.1f} cents, silent {dead or 'none'}")


@test
def velocity(r, rep):
    r.dry()
    out = {}
    for n in (45, 57, 72, 84):
        lv, ce = [], []
        for v in range(8, 128, 8):
            x = r.take(0.6, [(0.05, "on", n, v), (0.45, "off", n, 0)])
            i = onset(x, 20, 0.05)
            seg = x[max(i, 0):max(i, 0) + int(0.3 * FS), 0]
            lv.append(db(seg))
            ce.append(centroid(seg) if len(seg) else 0)
        out[n] = dict(level=lv, centroid=ce)
    rep["velocity"] = out
    bad = [n for n, d in out.items() if np.any(np.diff(d["level"]) < -0.5)]
    rng = {n: round(max(d["level"]) - min(d["level"]), 1) for n, d in out.items()}
    return not bad, f"level range per key {rng} dB; non-monotonic {bad or 'none'}"


@test
def types(r, rep):
    out = {}
    ev = [(0.05, "on", 57, 100), (0.7, "off", 57, 0), (0.8, "on", 69, 100), (1.4, "off", 69, 0)]

    def bdist(p, q, fl=None):
        if fl is not None:                            # (information: only the bands within fl dB of the loudest)
            p, q = np.maximum(p, p.max() - fl), np.maximum(q, q.max() - fl)
        return float(np.sqrt(np.mean(((p - p.mean()) - (q - q.mean())) ** 2)))
    for t in range(RHODES_TYPES):                   # the Rhodes TYPEs (the bar types have their own tests)
        r.dry(TYPE=type_cc(t))
        ks = []
        for k in range(reps("types")):
            if k:
                r.settle()
            x = r.take(1.6, ev)
            # each window from its note's own onset (align: the A4's too), never from the recording's start (it
            # starts 10-60 ms off the MIDI clock: the windows cut into the attack at another place each take)
            on = align(x[:, 0], [0.05, 0.8])[0]
            a = x[on[0] + int(0.05 * FS):on[0] + int(0.55 * FS), 0]
            b = x[on[1] + int(0.05 * FS):on[1] + int(0.5 * FS), 0]
            # the two notes' band powers summed (before: the bands of the two windows joined end to end, whose
            # junction - a step at a sample the onset sets - splashed 100-117 dB of broadband into the gaps between
            # the partials: it alone moved the distances by up to 0.8 dB from take to take)
            ks.append(dict(level=(db(a) + db(b)) / 2, centroid_a3=centroid(a), centroid_a4=centroid(b),
                           bands=10 * np.log10(10 ** (bands_db(a) / 10) + 10 ** (bands_db(b) / 10))))
        med = np.median([q["bands"] for q in ks], 0)          # per band, over the takes
        out[t] = dict(level=float(np.median([q["level"] for q in ks])),
                      centroid_a3=float(np.median([q["centroid_a3"] for q in ks])),
                      centroid_a4=float(np.median([q["centroid_a4"] for q in ks])), bands=med,
                      repeat_db=max([bdist(q["bands"], med) for q in ks] + [0.0]))   # the takes' own spread
    rep["types"] = {t: {k: v for k, v in d.items() if k != "bands"} for t, d in out.items()}
    lv = [out[t]["level"] for t in out]
    # distinct: every pair of types differs in its spectral envelope (third-octave bands 100 Hz - 10 kHz, levels
    # aligned), by more than 1.5 dB RMS. One centroid ratio (> 1.3, the old limit) never held: the types differ in
    # their bell, bark and decay more than in their mean brightness.
    dist = [bdist(out[i]["bands"], out[j]["bands"]) for i in range(RHODES_TYPES) for j in range(i + 1, RHODES_TYPES)]
    rep["types_distance_db"] = dist
    rep["types_distance_60db"] = [bdist(out[i]["bands"], out[j]["bands"], 60.0) for i in range(RHODES_TYPES)
                                  for j in range(i + 1, RHODES_TYPES)]   # (information: the bands 60 dB down floored)
    spread = max(out[t]["repeat_db"] for t in out)
    rep["types_repeat_db"] = spread
    return min(dist) > 1.5 and max(lv) - min(lv) < 3, ("levels " + ", ".join(f"{lv[t]:.1f}" for t in range(RHODES_TYPES)) +
                                                     " dB; spectral distance between types min " + f"{min(dist):.2f} dB (" +
                                                     ", ".join(f"{d:.2f}" for d in dist) + f"); median of {reps('types')}"
                                                     f" takes, their own spread <= {spread:.2f} dB")


# The classic chords hold no octave: two notes an octave apart sum their coinciding partials (A3's 2nd and A4's 1st,
# exactly harmonic in the pickup) at the phase their onsets set, and a note landing one audio block (5.8 ms) after
# the others moved the chord's loudness by up to 2.0 LU (host: F06 A3-E4-A4; C3-G3-C4-E4 1.7 LU); without an octave
# the coincidences are tempered fifths and thirds, which drift through every phase within the window: <= 0.2 LU
# (tests/py/test_hil_determinism.py). (Before 2026-10-18: A3 E4 A4 and C3 G3 C4 E4.)
CH_NOTES = (57, 60, 64)                   # A3 C4 E4 (v100)
FX_NOTES = (48, 55, 64)                   # C3 G3 E4 (v108: the FM-1's own keys)
CHORD = [(0.05, "on", n, 100) for n in CH_NOTES] + [(1.25, "off", n, 0) for n in CH_NOTES]
FX = [
    ("drive 64", dict(DRIVE=64)), ("drive 127", dict(DRIVE=127)),
    ("comp 64", dict(CMP_SW=127, CMP_AMT=64)), ("comp 127", dict(CMP_SW=127, CMP_AMT=127)),
    ("trem pan", dict(TW_SW=64, TW_DEPTH=100, TW_MODE=0)), ("trem am", dict(TW_SW=64, TW_DEPTH=100, TW_MODE=127)),
    ("wah", dict(TW_SW=127, TW_DEPTH=100)),
    ("phaser", dict(PH_SW=127, PH_DEPTH=100, PH_FDBK=64)), ("chorus", dict(CP_SW=64, CP_DEPTH=100)),
    ("delay dig", dict(DL_SW=48, DL_DEPTH=80, DL_TIME=64)), ("delay ana", dict(DL_SW=80, DL_DEPTH=80, DL_TIME=64)),
    ("reverb 64", dict(REV=64)), ("reverb 127", dict(REV=127)),
    ("everything", dict(DRIVE=40, CMP_SW=127, CMP_AMT=64, TW_SW=64, TW_DEPTH=64, PH_SW=127, PH_DEPTH=64,
                        CP_SW=64, CP_DEPTH=64, DL_SW=48, DL_DEPTH=64, REV=64)),
]


def lfo_rates(kv):
    """the free-running LFOs (Hz) a classic setting (CC values by CC name) turns on: the tremolo (TREM/WAH = TREM),
    the phaser (CC 12), the chorus (CC 85 43..85); rates at their defaults unless kv sets them"""
    P, out = vp(), []
    if (kv.get("TW_SW", 0) * 3) >> 7 == 1:
        out.append(lfo_hz(kv.get("TW_RATE", P["TW_RATE"]["de"])))
    if kv.get("PH_SW", 0) >= 64:
        out.append(lfo_hz(kv.get("PH_SPEED", P["PH_SPEED"]["de"]), True))
    if 43 <= kv.get("CP_SW", 0) <= 85:
        out.append(lfo_hz(kv.get("CP_SPEED", P["CP_SPEED"]["de"]), True))
    return out


def cyc_win(base, rates, min_frac=0.6):
    """a window (s) of whole cycles of the one LFO running, the most that fit in base s (and >= min_frac * base);
    else base (no LFO, several, or a cycle longer than base: the reset holds its phase, +-1 block)"""
    if len(rates) != 1:
        return base
    n = int(base * rates[0])
    return n / rates[0] if n >= 1 and n / rates[0] >= min_frac * base else base


@test
def effects(r, rep):
    r.dry()
    ref = r.take(1.5, CHORD)
    i0 = max(onset(ref, t=0.05), 0)
    base_direct = db(ref[i0:i0 + int(0.25 * FS)])
    rows, bad = [], []
    for name, kv in FX:
        r.dry(**kv)
        x = r.take(2.2, CHORD)
        i = max(onset(x, t=0.05), 0)
        timed = name.startswith(("delay", "reverb"))     # echoes / a tail add level by design: check the direct
        w = cyc_win(1.1, lfo_rates(kv))                  # (a modulated level: whole cycles of its LFO)
        base = db(ref[i0:i0 + int(w * FS)])
        lv = db(x[i:i + int(0.25 * FS)]) - base_direct + base if timed else db(x[i:i + int(w * FS)])
        lu = (lufs(x[i:i + int(0.25 * FS)] / 32768.0) - lufs(ref[i0:i0 + int(0.25 * FS)] / 32768.0) if timed else
              lufs(x[i:i + int(w * FS)] / 32768.0) - lufs(ref[i0:i0 + int(w * FS)] / 32768.0))
        clip = int((np.abs(x) >= 32700).sum())
        row = dict(name=name, delta_db=lv - base, delta_lu=lu, peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12), clipped=clip,
                   lr_corr=float(np.corrcoef(x[i:i + FS, 0], x[i:i + FS, 1])[0, 1]) if i + FS < len(x) else 1.0,
                   tail_db=db(x[i + int(1.3 * FS):i + int(1.9 * FS)]), clicks=clicks(x), window_s=w)
        rows.append(row)
        row["with_tail_db"] = db(x[i:i + int(w * FS)]) - base
        lim = 99 if name.startswith(("comp", "everything")) else 3   # a compressor's level depends on the playing
        # loudness, not RMS: the wah's resonance adds +4.5 dB RMS in the mids but +0.2 LU (fx_all) to the ear
        wide = row["lr_corr"] < 0.8
        target = -2.5 if wide and name.startswith("chorus") else 0.0       # the chorus' width compensation (0.8;
        # the phaser has none: FXALL_EXPECT's note)
        if abs(row["delta_lu"] - target) > lim or clip or row["clicks"]:
            bad.append(name)
    rep["effects"] = dict(dry_db=db(ref[i0:i0 + int(1.1 * FS)]), rows=rows)
    summary = "; ".join(f"{q['name']} {q['delta_lu']:+.1f}" for q in rows)
    return not bad, (f"loudness vs dry (LU; delay/reverb: the direct sound; compressor: information): {summary}; "
                     f"problems: {bad or 'none'}")


@test
def bypass(r, rep):
    """the FX button (CC 111): with typical effects on vs bypassed, and bypassed vs no effects at all"""
    r.dry()
    a = r.take(1.5, CHORD)
    r.dry(PH_SW=127, PH_DEPTH=80, CP_SW=64, CP_DEPTH=64, REV=50, DRIVE=30, CMP_SW=127, CMP_AMT=50, CMP_GAIN=15)
    b = r.take(2.2, CHORD)
    r.cc_raw(111, 127)
    r.settle()
    c = r.take(1.5, CHORD)
    r.cc_raw(111, 0)
    lv = []
    for x in (a, b, c):
        i = max(onset(x, t=0.05), 0)
        lv.append(lufs(x[i:i + int(1.1 * FS)] / 32768.0))
    rep["bypass"] = dict(no_fx=lv[0], fx_on=lv[1], bypassed=lv[2])
    # the compressor as the panel switches it on (THRESH up: +2.8 dB makeup); before, it ran with no makeup and the
    # effects-on level sat 5 dB under the bypass by construction
    ok = abs(lv[2] - lv[0]) < 0.5 and abs(lv[1] - lv[2]) < 3
    return ok, f"no effects {lv[0]:.1f}, effects on {lv[1]:.1f}, bypassed {lv[2]:.1f} LUFS"


FXALL = [  # (name, settings, expected: LU re clean within +-tol)
    ("drive 20", dict(DRIVE=20)), ("drive 64", dict(DRIVE=64)), ("drive 127", dict(DRIVE=127)),
    ("comp on (-20 dB 4:1 +4.3)", dict(CMP_SW=127, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=23)),
    ("comp -40 dB 20:1 +4.3", dict(CMP_SW=127, CMP_AMT=127, CMP_RAT=127, CMP_GAIN=23)),
    ("trem pan 32", dict(TW_SW=64, TW_DEPTH=32)), ("trem pan 127", dict(TW_SW=64, TW_DEPTH=127)),
    ("trem am 64", dict(TW_SW=64, TW_DEPTH=64, TW_MODE=127)), ("trem am 127 square", dict(TW_SW=64, TW_DEPTH=127, TW_MODE=127, TW_SHAPE=64)),
    ("trem am 127 amp law", dict(TW_SW=64, TW_DEPTH=127, TW_MODE=127, TW_SHAPE=127)),
    ("wah 32", dict(TW_SW=127, TW_DEPTH=32)), ("wah 127", dict(TW_SW=127, TW_DEPTH=127)),
    ("phaser 32", dict(PH_SW=127, PH_DEPTH=32)), ("phaser 80", dict(PH_SW=127, PH_DEPTH=80)),
    ("phaser 127 fb127", dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127)),
    ("chorus 32", dict(CP_SW=64, CP_DEPTH=32)), ("chorus 64", dict(CP_SW=64, CP_DEPTH=64)), ("chorus 127", dict(CP_SW=64, CP_DEPTH=127)),
    ("delay dig 50", dict(DL_SW=48, DL_DEPTH=50)), ("delay ana 127", dict(DL_SW=80, DL_DEPTH=127)),
    ("reverb 32", dict(REV=32)), ("reverb 127", dict(REV=127)),
    ("all on", dict(DRIVE=30, CMP_SW=127, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=23, TW_SW=64, TW_DEPTH=50, PH_SW=127, PH_DEPTH=60,
                    CP_SW=64, CP_DEPTH=50, DL_SW=48, DL_DEPTH=40, REV=40)),
]
# fx_all's judgement (LU re clean): 0 +- 2 LU; the chorus 2.5 LU under when wide (fx.c: its width compensation, 0.8);
# the delays +1.5 +- 3. The phaser has no width compensation (its sides are 30 degrees apart since 2026-10-17, they
# were 90): 'phaser 127 fb127' (L/R 0.71) was judged against the chorus' -2.5 and failed at +1.6 LU, which is the
# feedback's resonance (fx.c fx_phaser; +1.5 .. +1.6 on the FM-1 USB and jack in every report since 2026-10-18).
# The settings below sit off 0 by what the effect does, measured the same on the FM-1 (USB and jack) in every saved
# report since 2026-10-18 (build/hil/full/20261018-134700, -191741, build/marimba/p5, build/pr4test,
# build/release-0.9.4, build/final: their spread 0.2 LU) and on the host (tools/hil_scan.py --host --only fx_all);
# each is judged against that value +- 1 LU (the device's run-to-run +-0.3 LU and a margin), so a change shows:
#  - drive 127: -2.2 .. -2.4 LU. The drive's level trim (tools/gen_fx_tables.py DRV_RISE, d868d8a) is the mean of
#    single notes and chords: at AMOUNT 127 a single note +2.4, a chord -4.3 LU (an amp's compression on big chords);
#    this 3-note chord sits between them;
#  - (phaser 32 was -2.4 .. -2.5 LU until 0.9.6: 0.70 x (dry + allpasses) cut where the notches sat. The phaser's
#    level is now held at every DEPTH (mix-fix 0c0be11): the host reads +0.7 LU, judged at 0 +- 2 as every setting.)
# Not here: 'wah 32' (-2.1) and 'wah 127' (+2.1) failed until 2026-10-24: fx.c's depth law (wg, x0.80 .. x1.26,
# fitted on the FM-1 in dba3831 to cancel the wah's level swing) was still applied after the 0.9.3 make-up gain
# (ce5ccfb0) that already holds the level: 20 log10(0.80) = -1.9, 20 log10(1.26) = +2.0. The law is gone; the host
# reads -0.1 / +0.0 LU, so they are judged at 0 +- 2 as every other setting.
# (Agent E's reading of the same reports, 2026-10-24, proposed all five measured values as targets +- 1 LU; taken for
# drive 127 and phaser 32, not for the wah rows (the defect above) nor phaser 127 fb127 (its fault was the width
# target: at 0 +- 2 it passes without a target of its own).)
FXALL_EXPECT = {"drive 127": (-2.3, 1.0)}


def lufs(x):
    """ITU-R BS.1770 K-weighted loudness of a (n, 2) or (n,) float array at 48 kHz"""
    from scipy.signal import lfilter
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621]
    x = np.atleast_2d(np.asarray(x, float).T).T
    return -0.691 + 10 * np.log10(sum(np.mean(lfilter(b2, a2, lfilter(b1, a1, x[:, c])) ** 2) for c in range(x.shape[1])) + 1e-15)


@test
def fx_all(r, rep):
    """every effect at several settings: loudness (LUFS) re clean on USB (and the jack when an interface is there),
    stereo width, peak, clipping, clicks; the chord C3 G3 E4 at v108 (the FM-1's own keys' velocity). Each setting: the
    median of REPEATS['fx_all'] takes; a modulated one over whole cycles of its LFO (cyc_win of 1.8 s)"""
    jack = None
    try:
        if isinstance(r, Rig):                       # (the device only: HostRig opens no MIDI / audio)
            import hil_jack as hj
            jack = hj.Jack("M6", 5)
            jack.r = r
    except Exception:
        pass
    ch = [(0.1, "on", n, 108) for n in FX_NOTES] + [(2.1, "off", n, 0) for n in FX_NOTES]
    nrep = reps("fx_all")

    def seg(x, w=1.8):
        a = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
        i = int(np.argmax(a > 0.3 * a.max()))
        return x[i:i + int(w * FS)]

    def takes(jk=True):
        """nrep takes: [(USB (n, 2) / 32768, the jack or None)], each after the reset"""
        out = []
        for k in range(nrep):
            if k:
                r.settle()
            u = r.take(2.6, ch) / 32768.0
            j = jack.both(2.6, with_reset(ch))[1] if jack and jk else None
            out.append((u, j))
        return out

    def med(f, ts):
        return float(np.median([f(u, j) for u, j in ts]))
    r.dry()
    t0 = takes()
    rows, bad = [], []
    for name, kv in FXALL:
        r.dry(**kv)
        ts = takes()
        w = cyc_win(1.8, lfo_rates(kv))
        base_u = med(lambda u, j: lufs(seg(u, w)), t0)
        lus = [lufs(seg(u, w)) - base_u for u, j in ts]
        row = dict(name=name, usb_lu=float(np.median(lus)), usb_lu_spread=float(max(lus) - min(lus)), window_s=w,
                   peak_db=float(max(20 * np.log10(np.abs(u).max() + 1e-12) for u, j in ts)),
                   clipped=int(max((np.abs(u) >= 0.9995).sum() for u, j in ts)),
                   clicks=int(max(clicks(u * 32768) for u, j in ts)),
                   corr=med(lambda u, j: float(np.corrcoef(seg(u, w)[:, 0], seg(u, w)[:, 1])[0, 1]), ts))
        if jack:
            base_j = med(lambda u, j: lufs(seg(j, w)), t0)
            base_l = med(lambda u, j: lufs(seg(u, w)[:, 0]), t0)
            row["jack_lu"] = med(lambda u, j: lufs(seg(j, w)), ts) - base_j
            row["usbL_lu"] = med(lambda u, j: lufs(seg(u, w)[:, 0]), ts) - base_l   # the jack cable carries the left channel
        wide = row["corr"] < 0.8
        tol = 99.0 if name.startswith(("comp -40", "all on")) else 2.0     # extreme comp: MAKEUP is the player's
        target = -2.5 if wide and name.startswith("chorus") else 0.0       # the chorus' width compensation (0.8)
        if name.startswith("delay"):                  # the echoes add loudness by design (full mix: about +3 LU)
            target, tol = 1.5, 3.0
        if name in FXALL_EXPECT:                      # a setting whose level the effect sets by design (FXALL_EXPECT)
            target, tol = FXALL_EXPECT[name]
        if abs(row["usb_lu"] - target) > tol or row["clipped"] or row["clicks"] or \
                ("jack_lu" in row and abs(row["jack_lu"] - row["usbL_lu"]) > 1.0):
            bad.append(name)
        rows.append(row)
        print(f"    {name:28s} USB {row['usb_lu']:+5.1f} LU" + (f"  jack {row['jack_lu']:+5.1f}" if "jack_lu" in row else "") +
              f"  L/R {row['corr']:.2f}  peak {row['peak_db']:6.1f}  clip {row['clipped']}  clicks {row['clicks']}"
              f"  (window {w:.2f} s, {nrep} takes within {row['usb_lu_spread']:.2f} LU)", flush=True)
    # the FX bypass (CC 111) with everything on: back to the clean level (clean measured again right before)
    r.dry()
    base_u = med(lambda u, j: lufs(seg(u)), takes(False))
    r.dry(**dict(FXALL[-1][1]))
    r.cc_raw(111, 127)
    r.settle()
    byp = med(lambda u, j: lufs(seg(u)), takes(False)) - base_u
    r.cc_raw(111, 0)
    print(f"    {'FX bypass (all on)':28s} USB {byp:+5.1f} LU", flush=True)
    if abs(byp) > 0.5:
        bad.append("bypass")
    rep["fx_all"] = dict(rows=rows, bypass_lu=byp, base_usb_lufs=base_u, repeats=nrep)
    spread = max(q["usb_lu_spread"] for q in rows)
    return not bad, (f"{len(FXALL)} settings + bypass, median of {nrep} takes (their own spread <= {spread:.2f} LU); "
                     f"out of tolerance: {bad or 'none'}")


TOGGLES = [("drive 64", "DRIVE", 64, 0), ("comp", "CMP_SW", 127, 0), ("trem pan", "TW_SW", 64, 0), ("wah", "TW_SW", 127, 0),
           ("phaser", "PH_SW", 127, 0), ("chorus", "CP_SW", 64, 0), ("delay", "DL_SW", 48, 0), ("reverb 50", "REV", 50, 0)]


@test
def fx_toggle(r, rep):
    """each effect switched on and off while a chord sounds (as the panel does, at its starting depth): the loudness
    step at each switch (before vs after, the chord's own decay taken out) and clicks around it. The windows: from
    the chord's measured onset; whole cycles of the effect's LFO in 0.38-0.6 s when they fit (vibes_jump), else 0.4 s"""
    starts = dict(TW_DEPTH=64, PH_DEPTH=80, CP_DEPTH=64, DL_DEPTH=50, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=15)
    rows, bad = [], []
    ch = [(0.1, "on", n, 108) for n in FX_NOTES] + [(3.4, "off", n, 0) for n in FX_NOTES]

    def st(x, g, a, b):
        return lufs(x[g + int(a * FS):g + int(b * FS)] / 32768.0)
    r.dry(**starts)
    ref = r.take(3.6, ch)
    gr = r.lag(ref, ch)
    for name, key, on, off in TOGGLES:
        r.dry(**starts)
        x = r.take(3.6, ch + [(1.2, "cc", CC[key], on), (2.4, "cc", CC[key], off)])
        g = r.lag(x, ch)
        rates = lfo_rates({key: on})
        w = next((k / rates[0] for k in range(1, 9) if rates and 0.38 <= k / rates[0] <= 0.6), 0.4)
        # the step at a switch, the chord's natural decay (ref) taken out
        steps = []
        for sw in (1.2, 2.4):
            aa, bb = (sw + 0.05, sw + 0.05 + w), (sw - 0.05 - w, sw - 0.05)
            steps.append((st(x, g, *aa) - st(x, g, *bb)) - (st(ref, gr, *aa) - st(ref, gr, *bb)))
        up, dn = steps
        ck = clicks(x[g + int(1.1 * FS):g + int(1.4 * FS)]) + clicks(x[g + int(2.3 * FS):g + int(2.6 * FS)])
        rows.append(dict(name=name, on_step=up, off_step=dn, clicks=ck, window_s=w))
        lim = 3.5 if name in ("phaser", "chorus", "wah") else 4.0 if name == "comp" else 2.0   # wah: a moving
        # resonance (0.4 s windows catch its sweep); comp: the +2.8 dB makeup plus its attack / release
        if abs(up) > lim or abs(dn) > lim or ck:
            bad.append(name)
        print(f"    {name:10s} on {up:+5.1f} LU   off {dn:+5.1f} LU   clicks {ck}   (windows {w:.2f} s)", flush=True)
    rep["fx_toggle"] = rows
    return not bad, f"{len(TOGGLES)} effects switched on/off under a chord; out of tolerance: {bad or 'none'}"


@test
def delay_time(r, rep):
    r.dry(DL_SW=48, DL_DEPTH=100, DL_TIME=64, DL_FDBK=60)
    x = r.take(1.6, [(0.05, "on", 72, 110), (0.1, "off", 72, 0)])
    from scipy.signal import find_peaks
    env = np.sqrt(np.convolve(x[:, 0] ** 2, np.ones(480) / 480, "same"))
    i = max(onset(x, t=0.05), 0)
    # echoes: envelope peaks at least 100 ms apart, standing out of the decay between them
    pk, _ = find_peaks(env[i:], distance=int(0.1 * FS), prominence=max(10.0, 0.05 * env[i:].max()))
    pk = [i + k for k in pk if k > int(0.05 * FS)]
    exp_ms = 20 * 74 ** (64 / 127)
    gaps = np.diff([i] + pk) / FS * 1000 if pk else []
    rep["delay"] = dict(expected_ms=exp_ms, echoes_ms=list(map(float, gaps)))
    ok = len(gaps) >= 2 and abs(gaps[0] - exp_ms) < 15
    return ok, f"expected {exp_ms:.0f} ms, echoes at {[round(g) for g in gaps[:4]]} ms"


@test
def tremolo_rate(r, rep):
    r.dry(TW_SW=64, TW_DEPTH=127, TW_MODE=127, TW_RATE=64)
    ev = [(0.05, "on", 60, 100), (2.9, "off", 60, 0)]
    x = r.take(3.0, ev)
    g = r.lag(x, ev)                                  # 0.3 .. 2.8 s on the event clock, from the note's onset
    env = np.sqrt(np.convolve(x[:, 0] ** 2, np.ones(480) / 480, "same"))[g + int(0.3 * FS):g + int(2.8 * FS)]
    env = env / (np.convolve(env, np.ones(9600) / 9600, "same") + 1e-9) - 1
    sp = np.abs(np.fft.rfft(env * np.hanning(len(env)), 1 << 18))
    fr = np.fft.rfftfreq(1 << 18, 1 / FS)
    m = (fr > 0.3) & (fr < 15)
    f = float(fr[m][np.argmax(sp[m])])
    exp = 0.5 * 24 ** (64 / 127)
    rep["tremolo"] = dict(expected_hz=exp, measured_hz=f)
    return abs(f - exp) / exp < 0.15, f"expected {exp:.2f} Hz, measured {f:.2f} Hz"


@test
def sustain(r, rep):
    r.dry()
    ea = [(0.05, "on", 60, 100), (0.5, "off", 60, 0)]
    a = r.take(2.0, ea)
    r.dry()
    eb = [(0.02, "cc", 64, 127), (0.05, "on", 60, 100), (0.5, "off", 60, 0)]
    b = r.take(2.0, eb)
    r.setup(SUS=0)
    ga, gb = r.lag(a, ea), r.lag(b, eb)               # 1.0 .. 1.5 s on the event clock
    la, lb = db(a[ga + int(1.0 * FS):ga + int(1.5 * FS)]), db(b[gb + int(1.0 * FS):gb + int(1.5 * FS)])
    rep["sustain"] = dict(released_db=la, pedal_db=lb)
    return lb - la > 15, f"0.5 s after the key: damped {la:.1f} dB, with the pedal {lb:.1f} dB"


@test
def held_tail(r, rep):
    r.dry(REV=40)
    ev = [(0.05, "on", 57, 100)]
    x = r.take(16.0, ev)
    r.note_off(57)
    g = r.lag(x, ev)                                  # times on the event clock (the note-on at 0.05 s)
    env = [db(x[g + int(t * FS):g + int((t + 0.5) * FS)]) for t in np.arange(0, 15.5, 0.5)]
    nz = np.flatnonzero(np.abs(x).max(axis=1) > 0)
    rep["held_tail"] = dict(env_db=env, last_nonzero_s=float((nz[-1] - g) / FS) if len(nz) else 0.0,
                            hiss_8k_db=None)
    last = rep["held_tail"]["last_nonzero_s"]
    return last < 15.0, f"held A3 with reverb: output reaches exact zero at {last:.1f} s (gate)"


@test
def stress(r, rep):
    """8+ voices, fast repeats, pedal, all effects: dropouts show as clicks / zero runs"""
    r.dry(**dict(FX[-1][1]))
    ev = [(0.02, "cc", 64, 127)]
    t = 0.1
    chords = [(36, 48, 55, 60, 64, 67, 72, 76, 79, 84), (38, 50, 57, 62, 65, 69, 74, 77, 81, 86)]
    for k in range(16):
        for n in chords[k % 2]:
            ev.append((t, "on", n, 127))
        t += 0.25
        for n in chords[k % 2]:
            ev.append((t - 0.01, "off", n, 0))
    ev.append((t + 0.1, "cc", 64, 0))
    x = r.take(t + 1.5, ev)
    g = r.lag(x, ev)
    c, z = clicks(x[g + int(0.1 * FS):g + int(t * FS)]), zero_runs(x[g + int(0.2 * FS):g + int(t * FS)])
    rep["stress"] = dict(clicks=c, zero_runs=z, peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12),
                         clipped=int((np.abs(x) >= 32700).sum()))
    return c == 0 and z == 0, f"clicks {c}, zero runs {z}, peak {rep['stress']['peak_db']:.1f} dBFS, clipped {rep['stress']['clipped']}"


PR_COUNT = 19          # F01..F19 (presets.c PR_NF): F13 / F14 VIBES, F15 / F16 MARIMBA, F17..F19 Rhodes (0.9.5)
# each factory preset's loudness re F01 (LU) and its tolerance, the chord A3 C4 E4 v100 (CHORD): the Rhodes presets
# 0 +- 1.5 (AGENTS.md); VIBES 2.5 dB above the Rhodes types (MANUAL), so F13 / F14 +2.5 +- 1.0; MARIMBA is set 2.5 dB
# above too, but its strikes are held under -0.5 dBFS (the user's level decision "A", 2026-10-20): F16 WOOD ECHO (echo,
# chorus, reverb) reads +2.0, F15 -0.2 (on this chord; the MANUAL's A3-E4-A4 reads -1.5 on the FM-1), judged at +-1.0.
# The 0.9.5 presets (host): F02..F12 -0.7..+0.8 (F11 -1.6 since its LEVEL 10: HOST_SHIFT), F13 +2.6, F14 +2.0
# F12 AUTO WAH (B, the user's pick): its deep 1/4 band-pass sweep reads +1.6 LU on the chord and -2.3 on a phrase at
# LEVEL 127 (the maximum): a documented exception, 0 +- 2.0
# F17..F19 (TAPE ECHO, DREAM, RADIO: the starters U10..U12 until 0.9.5's RC) keep the starters' 1 LU
PRESET_TARGET = {12: (0.0, 2.0), 13: (2.5, 1.0), 14: (2.5, 1.0), 15: (-0.2, 1.0), 16: (2.5, 1.0), 17: (0.0, 1.0), 18: (0.0, 1.0),
                 19: (0.0, 1.0)}
# the user bank's starters (presets.c PR_NS: U01..U09 in a bank never saved): judged on the host only (a device's bank
# may hold the player's own presets), each within 1 LU of F01 on the chord; presets 20.. in the rows = U01..
PR_STARTERS = 9


def preset_pc(p):
    """the program change of row p (0-based: F01..F19, then U01..): presets.c preset_of_pc, PC 0..15 F01..F16,
    16..31 U01..U16, 32..34 F17..F19"""
    return p if p < 16 else p + 16 if p < PR_COUNT else p - PR_COUNT + 16


# a preset the host reads at another level than the FM-1 (re F01, LU), its host target shifted by that much: F11 RESO
# SWEEP's OHCD drive into the resonant LP12 reads 1.9 LU louder on the FM-1 (2026-10-25, LEVEL 18: the FM-1 +1.8 on
# USB and the jack, the host -0.1); at LEVEL 10 the FM-1 ~+0.3, the host -1.6
HOST_SHIFT = {11: -1.9}


def preset_target(p, host=False):
    t, tol = PRESET_TARGET.get(p, (0.0, 1.0) if p > PR_COUNT else (0.0, 1.5))
    return (t + HOST_SHIFT.get(p, 0.0) if host else t), tol


def preset_label(p):
    return f"F{p:02d}" if p <= PR_COUNT else f"U{p - PR_COUNT:02d}"


@test
def presets(r, rep):
    """F01..F19 (and on the host the user bank's starters U01..U09: PR_STARTERS): the chord's loudness, the median of REPEATS['presets'] takes each (every take after the reset), on USB
    and on the headphone jack when the interface is there (Rig.jack(): the M6 input 5, recorded at once; the jack is
    the reference). The takes go to build/hil/presets/ (F01-1.npz ...: usb, jack)"""
    jk = isinstance(r, Rig) and r.jack()
    d = os.path.join(OUT, "presets")
    os.makedirs(d, exist_ok=True)
    rows = []

    def level(x, thr, t=0.05):
        i = max(onset(x, thr, t), 0)
        return lufs(x[i:i + int(1.1 * FS)]), i
    for p in range(PR_COUNT + (PR_STARTERS if isinstance(r, HostRig) else 0)):
        r.cc_raw(120, 0)
        r.pc(preset_pc(p))
        ks = []
        for k in range(reps("presets")):
            quiet = r.settle()                         # (False: no exact silence in 10 s, a note still sounds)
            if jk:
                dd = r.both(2.0, with_reset(CHORD), jack=True)
                x, j = dd["usb"] * 32768, dd["jack"][:, 0]
            else:
                x, j = r.take(2.0, CHORD), None
            lv, i = level(x / 32768.0, 200 / 32768)
            q = dict(level=lv, onset_ms=1000 * i / FS, rms_db=db(x[i:i + int(1.1 * FS)]),
                     peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12),
                     clipped=int((np.abs(x) >= 32700).sum()), clicks=clicks(x), quiet=bool(quiet),
                     gaps=[str(g) for g in getattr(r, "xruns", []) if "gap" in str(g)])
            if j is not None:
                floor = float(np.abs(j[:int(0.04 * FS)]).max()) if len(j) else 0.0
                q["jack"], ij = level(j, max(4 * floor, 0.01 * float(np.abs(j).max())))
                q["jack_onset_ms"] = 1000 * ij / FS
            ks.append(q)
            np.savez_compressed(os.path.join(d, f"{preset_label(p + 1)}-{k + 1}.npz"), usb=(x / 32768).astype(np.float32),
                                jack=(j if j is not None else np.zeros(0)).astype(np.float32))
        lv = [q["level"] for q in ks]
        row = dict(preset=p + 1, level=float(np.median(lv)), spread_lu=float(max(lv) - min(lv)),
                   rms_db=float(np.median([q["rms_db"] for q in ks])), peak_db=max(q["peak_db"] for q in ks),
                   clipped=max(q["clipped"] for q in ks), clicks=max(q["clicks"] for q in ks),
                   onset_ms=[round(q["onset_ms"], 1) for q in ks], not_quiet=sum(not q["quiet"] for q in ks),
                   gaps=sum(len(q["gaps"]) for q in ks))
        if jk:
            jl = [q["jack"] for q in ks]
            row.update(jack=float(np.median(jl)), jack_spread_lu=float(max(jl) - min(jl)),
                       jack_onset_ms=[round(q["jack_onset_ms"], 1) for q in ks])
        rows.append(row)
    rep["presets"] = rows
    lv = [q["level"] for q in rows]
    bad = [q["preset"] for q in rows if q["clipped"] or q["clicks"]]
    loud = [q["preset"] for q in rows if q["not_quiet"]]      # a take started over a sound that never ended
    gapped = [q["preset"] for q in rows if q["gaps"]]
    r.pc(0)
    spread = max(q["spread_lu"] for q in rows)
    ok = max(lv) - min(lv) < 8 and not bad and not loud and not gapped
    msg = (f"F01..{preset_label(len(rows))} loudness " + ", ".join(f"{v:.1f}" for v in lv) + f" LUFS (span {max(lv) - min(lv):.2f}; median of"
           f" {reps('presets')} takes, their own spread <= {spread:.2f} LU); clipping/clicks {bad or 'none'}")
    if loud:
        msg += f"; no exact silence before a take at {[preset_label(q) for q in loud]} (a note still sounding: those levels are not the preset)"
    if gapped:
        msg += f"; a recording gap at F{gapped}"
    # each preset re F01 against its target (PRESET_TARGET): the Rhodes F01..F12 within 1.5 LU (AGENTS.md's rule; F07
    # -1.9, F08 -1.7, F10 -4.0 LU until their LEVEL was set, 2026-10-24), VIBES and MARIMBA by their own level
    host = isinstance(r, HostRig)
    off = [(q["preset"], v - lv[0]) for q, v in zip(rows, lv)
           if abs(v - lv[0] - preset_target(q["preset"], host)[0]) > preset_target(q["preset"], host)[1]]
    rep["presets_off_target"] = [dict(preset=p, lu=float(d)) for p, d in off]
    ok = ok and not off
    msg += ("; re F01 " + ", ".join(f"{preset_label(q['preset'])} {v - lv[0]:+.1f}" for q, v in zip(rows, lv) if q["preset"] > 1) +
            " LU; off target: " + (", ".join(f"{preset_label(p)} {d:+.1f}" for p, d in off) or "none"))
    if jk:                                             # the jack: the reference (what the player hears)
        jl = [q["jack"] for q in rows]
        rel = [(a - lv[0]) - (b - jl[0]) for a, b in zip(lv, jl)]      # USB vs jack, each re its own F01
        mis = [q["preset"] for q, m in zip(rows, rel) if abs(m) > MISMATCH_DB]
        ok = ok and max(jl) - min(jl) < 8
        msg += (f"; jack (re its own scale) {', '.join(f'{v - jl[0]:+.1f}' for v in jl)} LU re F01 (span"
                f" {max(jl) - min(jl):.2f}, spread <= {max(q['jack_spread_lu'] for q in rows):.2f}); USB-jack"
                f" disagreement <= {max(abs(m) for m in rel):.2f} LU{' at F' + str(mis) if mis else ''}")
    return ok, msg


# ==================================================================================================================
# VIBES: every control on the FM-1, each heard (device suites). Not in the default run: --vibes, or --only vibes_*.
#
# Every take records the USB audio and / or the headphone jack through an interface input (--source usb|jack|both,
# default both; the jack is --jack-dev "M6" input --jack-ch 5, as tools/hil_jack.py). The levels and spectra are
# judged on the USB's LEFT channel (what the jack cable carries) and on the jack, side by side; a level difference
# that the two disagree on by more than MISMATCH_DB is flagged. Stereo-only measures (pan, width, the reverb's
# pre-delay through its side signal) are USB only. Every take is also checked for full-scale samples on USB and for
# clicks (hil_scan's clicks(), outside 3 ms before / 17 ms after each note event as tools/bar_scan.py S1).
# The thresholds: tests/py/test_bars_cards_audible.py (VOICE), tests/py/test_vibes_controls_audible.py (DECAY,
# MOTOR, velocity), tools/bar_scan.py S9 (key evenness), hil_scan fx_all / fx_toggle (the effects), the task's
# 1.5 LU loudness-jump flag. The expected rates and times come from firmware/gen/fx_tables.h, the CCs from params.c.
# ==================================================================================================================
MISMATCH_DB = 1.0              # USB (left) vs jack: a level change both must agree on
JUMP_LU = 1.5                  # an effect switched on at its default settings: loudness step flagged above this
VIBES_CC79 = 0                 # CC 79 = 0: TYPE VIBES (the first bar type's band starts at 0)
MARIMBA_CC79 = 127             # CC 79 96..127: TYPE MARIMBA (frodhes.c midi_cc; tools/marimba_scan.py CC79)
ENGINE = "vibes"               # the bar TYPE the device suites play: set per suite by vibes_main (marimba_* suites)
RESET_S, TAKE_S = 1.2, 0.3     # the time estimate: a reset (CCs, settle, wait_silence) and a take's stream overhead
VTESTS = {}
VDEBUG = False                 # --dry-run: measurement exceptions are printed
_VP, _FXT, _SPEC = None, {}, None


def vtest(fn):
    VTESTS[fn.__name__] = fn
    return fn


def vp():
    """every parameter in params.c PARAMS[]: name -> dict(cc, hi, de, fmt); the CCs are read here, never typed in"""
    global _VP
    if _VP is None:
        import re
        src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
        _VP = {n: dict(cc=int(c), hi=int(h), de=int(d), fmt=f)
               for n, h, d, f, c in re.findall(r'\[P_(\w+)\] = \{"[^"]*", (\d+), (\d+), (F_\w+), (\d+)', src)}
        names = re.search(r'N_SYNC\[\] = \{([^}]*)\}', src).group(1)
        _VP["_SYNC"] = re.findall(r'"([^"]*)"', names)
    return _VP


def sync_idx(name):
    return vp()["_SYNC"].index(name)


def fx_table(name):
    if name not in _FXT:
        import re
        src = open(os.path.join(ROOT, "firmware", "gen", "fx_tables.h")).read()
        m = re.search(r'%s\[\d+\] = \{([^}]*)\}' % name, src)
        _FXT[name] = [int(v) for v in re.findall(r'-?\d+', m.group(1))]
    return _FXT[name]


def lfo_hz(v, half=False):
    """F_HZ (tremolo, filter LFO, motor) and F_HZH (chorus, phaser: half): FX_LFO_INC[v] * 44100 / 2^32"""
    hz = fx_table("FX_LFO_INC")[int(v)] * 44100 / 2 ** 32
    return hz / 2 if half else hz


def mot_hz(v):
    return lfo_hz(min(int(v), 120))            # params.c MOT_RATE_V: 120 and up = 10.1 Hz


def dly_ms(v):
    return fx_table("FX_DLY_T")[int(v)] * 1000 / 44100


def pdly_ms(v):
    return (int(v) * 3528 // 127) * 1000 / 44100   # fx.c FX_PDLY_SAMPLES


def lvl_db(v):
    t = fx_table("FX_LVL")
    return 20 * np.log10(t[int(v)] / t[64])


def bar_cc79():
    return MARIMBA_CC79 if ENGINE == "marimba" else VIBES_CC79


def bspec():
    """the bar spec (docs/model/data/bars_spec.json, as tools/bar_cards.py): midi -> {mode name: Hz}; on the marimba
    suites docs/model/data/marimba_spec.json once it exists (until then the stub plays VIBES's tables)"""
    global _SPEC
    d = os.path.join(ROOT, "docs", "model", "data")
    p = os.path.join(d, "marimba_spec.json") if ENGINE == "marimba" else None
    p = p if p and os.path.exists(p) else os.path.join(d, "bars_spec.json")
    if _SPEC is None or _SPEC[0] != p:
        _SPEC = (p, {k["midi"]: {m["name"]: m["f"] for m in k["modes"]} for k in json.load(open(p))["keys"]})
    return _SPEC[1]


def f1(n):
    return bspec().get(n, {}).get("V1") or A4 * 2 ** ((n - 69) / 12)


def ccv(name, x):
    """the CC value that sets parameter name to x (its index for an enum / switch, else 0..127), as midi_cc() reads it"""
    p = vp()[name]
    if name == "CP_SW":                        # CC 85, the combined CHO/PHA switch: 43..85 chorus (86..: the phaser)
        return 64 if x else 0
    if name == "DRV_SW":                       # CC 84, the drive TYPE's band table
        return drv_cc(int(x))
    if p["hi"] >= 127:
        return int(x)
    if p["hi"] == 2:
        return (0, 64, 127)[int(x)]
    return min(127, (int(x) * 128 + 64) // (p["hi"] + 1))


def ccs(settings):
    return [(vp()[n]["cc"], ccv(n, x)) for n, x in settings]


def with_(base, *kv):
    """base settings with some replaced in place (order kept: CC 85 before the chorus' 86 / 87), others appended"""
    out = list(base)
    for k, v in kv:
        for i, (n, _) in enumerate(out):
            if n == k:
                out[i] = (k, v)
                break
        else:
            out.append((k, v))
    return out


# ---------------------------------------------------------------- the rig: USB and the jack at once
class Take:
    def __init__(self, tag, dur, ev, usb, jack):
        self.tag, self.dur, self.ev, self.usb, self.jack = tag, dur, ev, usb, jack
        self.sched = [t for t, k, a, b in ev if k == "on"]
        self._on = {}

    def srcs(self):
        return [s for s, x in (("usb", self.usb), ("jack", self.jack)) if x is not None]

    def m(self, src):
        return self.usb[:, 0] if src == "usb" else self.jack

    def on(self, src):
        """the onsets (samples) of the note-ons, in event order"""
        if src not in self._on:
            self._on[src] = align(self.m(src), self.sched)
            if src == "jack" and self.usb is not None and ENGINE == "marimba":
                o = jack_by_usb(self.m("usb"), self.on("usb"), self.jack, self._on["jack"][0])
                self._on["jack"] = (o, (o[0] / FS - self.sched[0]) if o else 0.0,
                                    [i / FS - s for i, s in zip(o, self.sched)])
        return self._on[src][0]

    def lat(self, src):
        self.on(src)
        return self._on[src][1]

    def lat_at(self, src, t):
        """the latency (s) in force at event time t: the last note-on's at or before it (the USB stream can slip)"""
        self.on(src)
        ls = [l for s, l in zip(self.sched, self._on[src][2]) if s <= t + 1e-9]
        return ls[-1] if ls else self._on[src][1]

    def slip_ms(self, src):
        """the largest change of the latency between the note-ons (ms): a stream that slipped"""
        self.on(src)
        ls = self._on[src][2]
        return float(1000 * (max(ls) - min(ls))) if ls else 0.0

    def at(self, src, t):
        """a sample index for event time t (s) in this source"""
        return int(round((t + self.lat_at(src, t)) * FS))

    def pre(self, src):
        """the pre-roll: the source before the first note-on reaches it (5 ms margin). USB: exact zero (the gate);
        the jack: the interface's and the FM-1's analogue hiss, the floor its measures stand on"""
        on = [t for t, k, a, b in self.ev if k == "on"]
        if not on:
            return self.m(src)[:0]
        return self.m(src)[:max(0, self.at(src, min(on)) - FS // 200)]


def jack_by_usb(u, ou, j, oj, pre=0.005, post=0.05, search=0.02, min_r=0.5):
    """the jack's onsets from USB's, recorded at once (the MARIMBA suites): each USB strike (pre .. post s around its
    onset; USB is exact zero before an isolated strike, so its onset is exact) found in the jack by cross-correlation
    within +-search s of align()'s jack onset (normalised |r| >= min_r, else align's stays). align() alone put the
    jack's onsets 2-15 ms late (the hiss under the 6 dB / 1 ms rise; the low notes' slow attack): the FM-1's keys
    C2..C7 take of 2026-10-19 read on the jack a level span 12.1 dB, a neighbour step 4.34 dB and C7 -46 c, where the
    jack is USB x -12.65 dB +-0.05 on every key (span 8.6 / step 0.48 dB, as USB). The interface's polarity (the M6
    reads the jack inverted) does not matter: |r|"""
    a0, a1, w = int(pre * FS), int(post * FS), int(search * FS)
    out = []
    for a, b in zip(ou, oj):
        x = np.asarray(u[max(0, a - a0):a + a1], float)
        lo = max(0, b - a0 - w)
        seg = np.asarray(j[lo:b - a0 + len(x) + w], float)
        if len(x) < a1 or not np.any(x) or len(seg) <= len(x):
            out.append(b)
            continue
        c = np.correlate(seg, x, "valid")
        e = np.sqrt(np.convolve(seg * seg, np.ones(len(x)), "valid") * np.dot(x, x)) + 1e-30
        k = int(np.argmax(np.abs(c) / e))
        out.append(lo + k + (a - max(0, a - a0)) if abs(c[k]) / e[k] >= min_r else b)
    return out


ALIGN_DENSE_S = 0.25  # align(): a note-on this soon after the previous one (a roll) is not searched for a slip: a
                       # re-strike of a ringing bar need not rise 3 dB in 1 ms frames, and the search then took a
                       # later strike for it (the 12 Hz rolls read 'the USB stream slipped' 217-338 ms)


def align(m, sched, lat_max=0.3, slip_max=0.35):
    """onsets of the scheduled note-ons: the first sound (5 % of the take's peak, never before the first event: a
    residue in the pre-roll is not a note) belongs to the last strike due before it (within lat_max): that gives the
    latency; each onset is then refined to a 6 dB rise within +-8 ms of its prediction. The USB stream can slip
    (round 1: +232 / -25 ms between strikes in the silent gaps of the VOICE takes; the jack, recorded at once, shows
    no slip): when the prediction holds no rise, the onset is searched +-slip_max around it (after the previous one)
    for the first rise of 10 dB, and the next prediction follows the latency found. Returns the onsets, the first
    latency (s) and the latency at each onset (s)."""
    if not sched:
        return [], 0.0, []
    a = np.abs(m)
    i0 = max(0, int((sched[0] - 0.005) * FS))
    mx = float(a[i0:].max()) if len(a) > i0 else 0.0
    if mx <= 0:
        return [int(s * FS) for s in sched], 0.0, [0.0] * len(sched)
    i = i0 + int(np.argmax(a[i0:] > 0.05 * mx))
    t = i / FS
    ks = [k for k, s in enumerate(sched) if s - 0.01 <= t <= s + lat_max]
    k = ks[-1] if ks else int(np.argmin([abs(t - s) for s in sched]))
    lat = t - sched[k]
    w = FS // 1000
    nf = len(m) // w
    e = 10 * np.log10(np.mean(np.asarray(m[:nf * w], float).reshape(nf, w) ** 2, axis=1) + 1e-20)   # 1 ms frames

    def rise(f, before=12):
        if f < 3 or f + 3 > nf:
            return 0.0
        return float(e[f:f + 3].max() - e[max(0, f - before):f - 1].max())
    out, lats, prev, prev_s = [], [], -1, None
    for s in sched:
        dense = prev_s is not None and 0 < s - prev_s < ALIGN_DENSE_S
        prev_s = s
        j = int(round((s + lat) * FS))
        a0, a1 = max(0, j - 10 * w), min(len(m), j + 8 * w)
        x = m[a0:a1]
        n = len(x) // w
        if n > 3:
            en = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1)) + 1e-9
            r = np.flatnonzero(en[2:] > 2 * en[:-2])
            if len(r):
                j = a0 + (int(r[0]) + 1) * w
        f = j // w
        r0 = max(rise(g) for g in range(max(1, f - 8), f + 9))
        if r0 < 3.0 and not dense:                       # nothing starts where it was due: the stream slipped?
            lo = max(prev // w + 30 if prev >= 0 else 0, f - int(slip_max * 1000))
            hi = min(nf - 3, f + int(slip_max * 1000))
            cand = [g for g in range(lo, hi) if rise(g) >= 15.0 and e[g:g + 3].max() > e[lo:hi + 3].max() - 30]
            if cand:
                j = min(cand, key=lambda c: abs(c - f)) * w
                lat = j / FS - s
        out.append(j)
        lats.append(j / FS - s)
        prev = j
    return out, lats[0], lats


def synth(dur, ev):
    """--dry-run: a stand-in for the FM-1 (bar-like partials 1 / 4 / 10, damped at the key-up unless the pedal is
    down), so every measurement runs; its numbers mean nothing"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    x = np.zeros(n)
    ped = [(e[0], e[3]) for e in ev if e[1] == "cc" and e[2] == 64]
    for k, (t0, kind, note, vel) in enumerate(ev):
        if kind != "on":
            continue
        offs = [e[0] for e in ev[k + 1:] if e[1] == "off" and e[2] == note]
        toff = offs[0] if offs else dur
        held = any(v >= 64 for tp, v in ped if tp <= toff)
        f = A4 * 2 ** ((note - 69) / 12)
        s = (t >= t0 + 0.004)
        tt = t[s] - t0
        env = 0.15 * (vel / 127) ** 2 * np.exp(-tt / 2.5)
        if not held:
            env = env * np.where(t[s] > toff, np.exp(-np.clip(t[s] - toff, 0, None) / 0.05), 1.0)
        x[s] += env * sum(a * np.sin(2 * np.pi * f * h * tt) for h, a in ((1, 1.0), (4, 0.3), (10, 0.05)))
    rng = np.random.default_rng(len(ev) + n)
    L = np.round(np.clip(x + 1e-5 * rng.standard_normal(n), -1, 32767 / 32768) * 32768) / 32768
    R = np.round(np.clip(0.97 * x + 1e-5 * rng.standard_normal(n), -1, 32767 / 32768) * 32768) / 32768
    return np.stack([L, R], 1), 0.35 * x + 2e-5 * rng.standard_normal(n)


class VRig:
    def __init__(self, source="both", jack_dev="M6", jack_ch=5, dry_run=False, wav=False, offline=None, host=None):
        self.source, self.dry_run, self.wav, self.offline = source, dry_run, wav and not offline, offline
        self.want_usb, self.want_jack = source in ("usb", "both"), source in ("jack", "both")
        self.plan_s, self.takes, self.r, self.jack_name, self.host = 0.0, [], None, None, host is not None
        if self.host:                                  # --host: the host build plays the takes; USB only (no jack)
            self.want_usb, self.want_jack, self.r = True, False, HostRig(host, tag="vhost")
            return
        if offline:
            self._jack_peak = offline_jack_peaks(offline)
            self.jack_name = "the saved takes"
            return
        if dry_run:
            return
        self.r = Rig()
        self.sd = self.r.sd
        if self.want_jack:
            devs = self.sd.query_devices()
            js = [i for i, d in enumerate(devs) if d["name"] == jack_dev and d["max_input_channels"] > 0] or \
                 [i for i, d in enumerate(devs) if jack_dev.lower() in d["name"].lower() and d["max_input_channels"] > 0]
            if not js:
                sys.exit(f"no '{jack_dev}' audio input for the headphone jack: connect it, or --source usb")
            self.jdev, self.jn, self.jch = js[0], devs[js[0]]["max_input_channels"], jack_ch - 1
            self.jack_name = f"{devs[js[0]]['name']} input {jack_ch}"
            if not 0 <= self.jch < self.jn:
                sys.exit(f"--jack-ch {jack_ch}: '{jack_dev}' has {self.jn} inputs")
        self.r.capture()                               # the persistent recorder: open for the whole suite
        if self.want_jack:
            self.r.add_jack(self.jdev, self.jch)

    def sources(self):
        return [s for s, w in (("usb", self.want_usb), ("jack", self.want_jack)) if w]

    def send(self, cc, v):
        if self.r:
            self.r.out.send(self.r.mido.Message("control_change", control=int(cc), value=int(v)))

    def reset(self, settings=(), motor=False, label=""):
        """CC 120, every parameter at its default (TYPE not touched), DRY, VIBES (CC 79), the motor (CC 47), then the
        settings in order, then the reset (CC 10: every LFO, line, noise and the motor to boot; it also empties the
        delay line, which round 1 heard replaying the previous take's echoes); waits for the gate's exact silence.
        take() sends CC 10 again RESET_LEAD before its first note"""
        self.plan_s += RESET_S
        sets = ccs(settings)
        if self.offline:
            return
        if self.dry_run:
            print(f"  reset{' [' + label + ']' if label else ''}: defaults + DRY, CC 79={bar_cc79()}, CC 47="
                  f"{127 if motor else 0}" + "".join(f", {n}={x} (CC {c}={v})" for (n, x), (c, v) in zip(settings, sets))
                  + f", CC {RESET_CC}=127")
            return
        self.send(120, 0)
        if not hasattr(self, "defaults"):
            self.defaults = [(c, v) for c, v in param_defaults() if c not in (79, 80)]
        for c, v in self.defaults:
            self.send(c, v)
        for k, v in DRY.items():
            self.r.cc(k, v)
        self.send(79, bar_cc79())
        self.send(vp()["MOT_ON"]["cc"], 127 if motor else 0)
        for c, v in sets:
            self.send(c, v)
        self.send(RESET_CC, 127)                       # (was: 1.6 s of DEPTH 0 to empty the delay line)
        if self.host:
            return
        time.sleep(0.3)
        if not self.r.wait_silence(15.0):
            print(f"   (reset {label}: no exact silence in 15 s; the take may start on a residue)", flush=True)

    def take(self, tag, dur, events):
        """the events go out with the reset (with_reset: CC 10 RESET_LEAD before the first note); the Take keeps the
        stimulus' own events (the analysis, --offline's sidecars)"""
        ev = sorted(events, key=lambda e: e[0])
        self.plan_s += dur + TAKE_S
        if self.offline:
            u, j = load_take(self.offline, tag, self._jack_peak, self.want_usb, self.want_jack)
        elif self.host:                                # (HostRig: int16 scale; the device's USB reads / 32768)
            u, j = self.r._rec(dur, with_reset(ev)) / 32768, None
        elif self.dry_run:
            print(f"    take {tag} ({dur:.1f} s): " + "; ".join(
                f"{t:.2f} {k} {a}" + (f" v{b}" if k == "on" else f"={b}" if k == "cc" else "") for t, k, a, b in with_reset(ev)))
            u, j = synth(dur, ev)
            u, j = (u if self.want_usb else None), (j if self.want_jack else None)
        else:
            u, j = self._rec(dur, with_reset(ev))
            if self.xruns:
                print(f"   xrun {tag}: {self.xruns[:4]}", flush=True)
        T = Take(tag, dur, ev, u, j)
        T.gaps = [f"{k} {x}" for k, _, x in getattr(self, "xruns", []) if str(x).startswith("gap")] \
            if not (self.offline or self.dry_run or self.host) else []
        self.takes.append(take_stats(T))
        if self.wav:
            save_wav(T)
        return T

    def _rec(self, dur, ev):
        """both inputs while the events go out on time: slices of the rig's persistent recorder (Rig.both: one stream
        per device for the whole suite, each at ITS OWN rate, the FM-1 44.1 kHz, the M6 48 kHz, resampled to FS here;
        letting PortAudio convert a device's rate while the other one also streamed made the USB capture skip ~200 ms
        in some takes, 2026-10-18). The USB stream stays open between the takes, so the FM-1 keeps its automatic
        flash saves back for the whole suite: a save (interrupts off ~24 ms) inside a take dropped ~250 ms of the
        USB recording (stale input), which a stream opened per take let in at every take's start"""
        d = self.r.both(dur, ev, jack=self.want_jack)
        self.xruns = [(k, 0.0, f) for k, f in self.r.xruns]
        u = d["usb"] if self.want_usb else None           # (stereo, as the suites expect)
        j = d["jack"][:, 0] if self.want_jack else None
        return u, j

    def finish(self):
        if self.r and not self.host:
            self.reset()
            self.r.out.send(self.r.mido.Message("program_change", program=0))



def to_fs(x, sr, dur):
    """a capture at a device's own rate -> FS (polyphase), dur s long; (n,) or (n, ch)"""
    if sr != FS:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(FS, sr)
        x = resample_poly(x, FS // g, sr // g, axis=0)
    n = int(dur * FS)
    return np.pad(x, [(0, max(0, n - len(x)))] + [(0, 0)] * (np.ndim(x) - 1))[:n]


def save_wav(T):
    """every take to build/hil/vibes/: USB as recorded (int16), the jack at its peak 0.5 (a 16-bit file) with the
    scale in the take's sidecar {tag}.json (jack_scale: the recorded value = the file's sample / 32768 * jack_scale),
    with the events, so --offline re-reads it exactly"""
    import wave
    d = os.path.join(OUT, "vibes")
    os.makedirs(d, exist_ok=True)
    side = dict(tag=T.tag, dur=T.dur, ev=[list(e) for e in T.ev], fs=FS)
    for s, x in (("usb", T.usb), ("jack", T.jack)):
        if x is None:
            continue
        x = np.atleast_2d(np.asarray(x).T).T
        if s == "jack":
            pk = float(np.abs(x).max()) + 1e-12
            side["jack_scale"] = pk / 0.5
            side["jack_peak_db"] = float(20 * np.log10(pk))
            x = x / pk * 0.5
        w = wave.open(os.path.join(d, f"{T.tag}-{s}.wav"), "wb")
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(np.clip(np.round(x * 32768), -32768, 32767).astype("<i2").tobytes())
        w.close()
    json.dump(side, open(os.path.join(d, f"{T.tag}.json"), "w"))


def offline_jack_peaks(d):
    """--offline: each take's jack peak (dBFS), to undo save_wav's normalisation (the jack's level changes between
    takes are measured): from the takes' sidecars, else from the scan's report json (round 1 saved no sidecar; its
    take_stats() jack_peak_db is that peak, to the float)"""
    out = {}
    for rp in (os.path.join(d, "..", "vibes-report.json"), os.path.join(d, "vibes-report.json")):
        if os.path.exists(rp):
            for t in json.load(open(rp)).get("takes", []):
                if "jack_peak_db" in t:
                    out[t["tag"]] = float(t["jack_peak_db"])
            break
    for f in os.listdir(d):
        if f.endswith(".json") and not f.startswith("vibes-"):
            j = json.load(open(os.path.join(d, f)))
            if "jack_peak_db" in j:
                out[j["tag"]] = float(j["jack_peak_db"])
    return out


def read_wav(path):
    import wave
    w = wave.open(path, "rb")
    n, ch = w.getnframes(), w.getnchannels()
    x = np.frombuffer(w.readframes(n), "<i2").astype(float).reshape(n, ch) / 32768
    w.close()
    return x


def load_take(d, tag, peaks, want_usb, want_jack):
    """--offline: a saved take (USB stereo as recorded; the jack restored to its recorded scale)"""
    u = j = None
    pu, pj = os.path.join(d, f"{tag}-usb.wav"), os.path.join(d, f"{tag}-jack.wav")
    if want_usb:
        if not os.path.exists(pu):
            raise FileNotFoundError(pu)
        u = read_wav(pu)
    if want_jack and os.path.exists(pj):
        j = read_wav(pj)[:, 0]
        if tag in peaks:
            j = j / (np.abs(j).max() + 1e-12) * 10 ** (peaks[tag] / 20)
        else:
            print(f"   (offline: no recorded jack peak for {tag}: its jack levels are relative to the file)")
    return u, j


def clicks_guarded(m16, guards, before_ms=3, after_ms=17):
    """hil_scan's clicks() detector (the same formula) with bar_scan S1's guards around the note events"""
    d2 = np.abs(np.diff(m16, 2))
    lvl = np.convolve(np.abs(m16), np.ones(480) / 480, "same")[1:-1] + 50
    hit = np.flatnonzero(d2 > 6 * lvl + 2000) + 1
    ok = np.ones(len(m16), bool)
    for i in guards:
        ok[max(0, i - before_ms * FS // 1000):max(0, i + after_ms * FS // 1000)] = False
    return int(ok[hit].sum())


def take_stats(T):
    """every take: full-scale samples on USB (-32768 / +32767), the peak, clicks on each channel / the jack"""
    st = dict(tag=T.tag, dur=T.dur)
    if getattr(T, "gaps", None):                      # input a stream never delivered / delivered twice (Ring.cut)
        st["gaps"] = list(T.gaps)
    notes = [t for t, k, a, b in T.ev if k in ("on", "off")]
    if T.usb is not None:
        raw = np.round(T.usb * 32768)
        st["usb_fs"] = int(((raw >= 32767) | (raw <= -32768)).sum())
        st["usb_near"] = int((np.abs(raw) >= 32700).sum())
        st["usb_peak_db"] = float(20 * np.log10(np.abs(T.usb).max() + 1e-12))
        g = [T.at("usb", t) for t in notes]
        st["usb_clicks"] = clicks_guarded(raw[:, 0], g) + clicks_guarded(raw[:, 1], g)
        st["usb_clicks_raw"] = clicks(raw[:, 0]) + clicks(raw[:, 1])
        st["usb_slip_ms"] = _safe(lambda: max_jump_ms(T, "usb", "jack" if T.jack is not None else None)) or 0.0
        p_ = T.usb[:max(0, int(min([t for t, k, a, b in T.ev if k in ("on", "cc")] or [0]) * FS) - FS // 200), 0]
        st["usb_pre_db"] = float(20 * np.log10(np.abs(p_).max() + 1e-12)) if len(p_) else -240.0
    if T.jack is not None:
        st["jack_peak_db"] = float(20 * np.log10(np.abs(T.jack).max() + 1e-12))
        st["jack_over"] = int((np.abs(T.jack) >= 0.999).sum())
        ref = T.usb[:, 0] if T.usb is not None else None
        gain = (np.sqrt(np.mean(ref ** 2)) / (np.sqrt(np.mean(T.jack ** 2)) + 1e-12)) if ref is not None else 1.0
        st["jack_clicks"] = clicks_guarded(T.jack * gain * 32768, [T.at("jack", t) for t in notes])
        st["jack_slip_ms"] = _safe(lambda: max_jump_ms(T, "jack")) or 0.0
    return st


SLIP_MS = 15.0                 # a latency step between two note-ons over this: the stream slipped (onsets jitter ~5 ms)


def max_jump_ms(T, src, ref=None):
    """the largest latency step between consecutive note-ons (ms; align()'s per-onset latency); with ref (the other
    source, recorded at once) the step of the difference: a note sent late moves both alike"""
    T.on(src)
    ls = np.array(T._on[src][2])
    if ref is not None:
        T.on(ref)
        ls = ls - np.array(T._on[ref][2])
    s = T.sched
    keep = [k for k in range(len(ls)) if k == 0 or not 0 < s[k] - s[k - 1] < ALIGN_DENSE_S]   # (a roll: align's
    ls = ls[keep]                                      # latencies there are the prediction's, not measured)
    return float(max((abs(b - a) * 1000 for a, b in zip(ls, ls[1:])), default=0.0))


# ---------------------------------------------------------------- measures (FS 48 kHz; mono unless named)
def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        if VDEBUG:
            import traceback
            traceback.print_exc()
        return None


def rdb(x):
    x = np.asarray(x, float)
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12)) if x.size else float("nan")


def cut(m, i, a, b):
    return m[max(0, i + int(a * FS)):max(0, i + int(b * FS))]


def lu(x):
    return float(lufs(x))


def cen(x):
    return centroid(np.asarray(x, float))


def third(x):
    """1/3-octave band levels (dB), 50 Hz .. 15.9 kHz (tools/bar_cards.py BANDS)"""
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return np.array([10 * np.log10(S[(fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))].sum() + 1e-30)
                     for c in [50 * 2 ** (i / 3) for i in range(26)]])


def sdist_bands(b1, b2):
    """the bands sdist averages over: those within 60 dB of the louder take's strongest band"""
    b1, b2 = np.asarray(b1), np.asarray(b2)
    r1, r2 = b1 - 10 * np.log10(np.sum(10 ** (b1 / 10))), b2 - 10 * np.log10(np.sum(10 ** (b2 / 10)))
    fl = max(r1.max(), r2.max()) - 60.0
    return (r1 > fl) | (r2 > fl)


def sdist(b1, b2, use=None):
    """tools/bar_cards.py dist(): level-aligned spectral distance, dB RMS (3 dB = a clear timbre change). use: the
    bands to average over (default sdist_bands of these two; the jack: USB's, vibes_voice)"""
    b1, b2 = np.asarray(b1), np.asarray(b2)
    t1, t2 = 10 * np.log10(np.sum(10 ** (b1 / 10))), 10 * np.log10(np.sum(10 ** (b2 / 10)))
    r1, r2 = b1 - t1, b2 - t2
    fl = max(r1.max(), r2.max()) - 60.0
    use = (r1 > fl) | (r2 > fl) if use is None else np.asarray(use, bool)
    if not use.any():
        return 0.0
    d = np.maximum(r1, fl) - np.maximum(r2, fl)
    return float(np.sqrt(np.mean(d[use] ** 2)))


def frame_dist(x1, x2, n=2048):
    """the spectral distance (sdist) of two takes frame by frame (n samples, 43 ms), the median over the frames: for
    two settings of a swept effect whose sweeps run in step (CC 10 restarts the LFO at the take, +-1 block), where
    the whole window's spectrum averages the sweep away. A PHASER FDBK resonance moves with the notches; over the
    5 s window the FM-1's jack read FDBK 0 vs 127 1.8 dB apart (the old measure), frame by frame 3.9 dB (2026-10-18)"""
    k = min(len(x1), len(x2)) // n
    if k < 1:
        return float("nan")
    return float(np.median([sdist(third(x1[i * n:(i + 1) * n]), third(x2[i * n:(i + 1) * n])) for i in range(k)]))


def crest_db(m, i):
    """the strike's crest: the peak in 0..30 ms from onset i re the rms of 0.1..0.4 s (dB); information since
    2026-10-24 (strike_db)"""
    return 20 * np.log10(np.abs(cut(m, i, 0, 0.03)).max() + 1e-12) - rdb(cut(m, i, 0.1, 0.4))


def strike_db(m, i):
    """the strike's level: rms 0..20 ms from onset i re 0.1..0.4 s (dB), COMP ATTACK's measure since 2026-10-24. The
    crest (crest_db) read the vibraphone's first peak, 2 ms after the onset, which no ATTACK holds (the compressor's
    gain moves once per 16 samples), so VIBES read crest 18.6 / 20.8 / 20.5 dB at ATTACK 0 / 40 / 127 (+2.0, at the
    limit, on the FM-1 in every report since 2026-10-18 and on the host alike); the strike's energy shows the attack:
    the VIBES host 1.3 / 4.2 / 10.9 dB (+9.7), the FM-1's VIBES takes of 2026-10-17 0.2 / 3.1 / 10.7, MARIMBA (FM-1,
    2026-10-19) 0.3 / 5.5 / 14.3 (tests/py/test_hil_limits.py). Agent E proposed max(crest 40, 127) - crest 0 >= +1.5
    dB (2026-10-24): the same blind measure with a lower limit; the strike's level keeps the 2 dB with a 7 dB margin"""
    return rdb(cut(m, i, 0, 0.02)) - rdb(cut(m, i, 0.1, 0.4))


def apart_of_spread(d, spread, least=2.0):
    """two settings apart by frame_dist d against the spread of two takes of one setting: the takes' own differences
    (the jack's hiss and harmonics in the frames) add to d in quadrature (sdist is an RMS over the bands), so the
    settings' own distance is sqrt(d^2 - spread^2); it must reach `least` and the spread itself. The rule before
    2026-10-24 (d >= 2 x spread) counted the noise twice: PHASER FDBK on VIBES, the FM-1 jack 4.2 .. 4.3 dB apart
    against a spread of 2.3 .. 2.4 (USB, the same takes: 4.1 against 0.0 .. 0.6) failed in every report since
    2026-10-18; now 3.4 .. 3.6 >= 2.4. A source whose two takes of one setting differ as much as the settings fails.
    (Agent E proposed a 1.5x spread guard on the jack, 2026-10-24: it passes the same takes; the quadrature is why)"""
    d2 = d * d - spread * spread
    return d2 > 0 and np.sqrt(d2) >= max(least, spread)


def pk_db(x, f, rel=0.03):
    nf = 1 << max(15, int(np.ceil(np.log2(len(x)))) + 2)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    return float(10 * np.log10(S[(fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)].sum() + 1e-30))


def strike_feats(m, i, n):
    """tools/bar_cards.py features() at 48 kHz: LUFS 0..1 s, attack centroid 0..0.3 s, V2 / V3 re V1 (5..300 ms),
    bands 0..0.5 s"""
    sp = bspec().get(n, {})
    att = cut(m, i, 0.005, 0.3)
    l1 = pk_db(att, sp.get("V1", f1(n)))
    w = cut(m, i, 0, 0.5)
    return dict(lufs=lu(cut(m, i, 0, 1.0)), cen_a=cen(cut(m, i, 0, 0.3)),
                v2=pk_db(att, sp["V2"]) - l1 if "V2" in sp else float("nan"),
                v3=pk_db(att, sp["V3"]) - l1 if "V3" in sp else float("nan"), bands=third(w))


def partial_trace(x, f, n=4096, hop=480):
    """a partial's level (dB) in frames: the peak within +-6 % of f"""
    w = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = np.abs(fr - f) <= max(0.06 * f, fr[1])          # (at least the nearest bins: short frames, low keys)
    idx = np.arange(0, len(x) - n, hop)
    out = np.array([20 * np.log10(np.abs(np.fft.rfft(x[i:i + n] * w))[sel].max() / (n / 4) + 1e-12) for i in idx])
    return out, (idx + n / 2) / FS


def t60(x, f, hi=6.0, lo=36.0):
    """tests/py/test_vibes_controls_audible.py t60(): the partial's T60 (s) fitted from 6 to 36 dB under its peak"""
    e, t = partial_trace(x, f)
    ip = int(np.argmax(e))
    a = np.where((np.arange(len(e)) > ip) & (e < e[ip] - hi))[0]
    b = np.where((np.arange(len(e)) > ip) & (e < e[ip] - lo))[0]
    i0, i1 = a[0], (b[0] if len(b) else len(e) - 1)
    return float(-60.0 / np.polyfit(t[i0:i1], e[i0:i1], 1)[0])


def release_rate(x, f, t_off):
    """test_vibes_controls_audible after_release(): the partial's decay (dB/s) 30..300 ms after the key-up"""
    e, t = partial_trace(x, f, hop=240)
    k = (t >= t_off + 0.03) & (t <= t_off + 0.3) & (e > e.max() - 100)
    return float(-np.polyfit(t[k], e[k], 1)[0])


def excess_db(x, ref):
    """what x adds to ref (two takes of one stimulus, the same window): the per-bin power above ref's (+1 dB of
    take-to-take slack), as a mean-square level (dB). The engine is deterministic apart from NOISE, so the tonal
    bins cancel without a sample-exact alignment (the device's takes are not aligned to the sample)."""
    nn = min(len(x), len(ref))
    w = np.hanning(nn)
    P = np.abs(np.fft.rfft(x[:nn] * w)) ** 2
    Q = np.abs(np.fft.rfft(ref[:nn] * w)) ** 2
    ex = np.maximum(P - 1.26 * Q, 0).sum() * 2 / (nn * (w * w).sum())
    return float(10 * np.log10(ex + 1e-24))


def knock_db(mx, ox, mref, oref, t0=1.5, t1=1.65, slack=0.012):
    """NOISE's knock in take mx (onset ox): what t0..t1 after the onset (the key-up) adds to the NOISE 0 take mref
    (onset oref), as excess_db but over the reference's windows shifted by up to +-slack (1 ms steps, the per-bin
    maximum). The engine takes a MIDI event at its next 256-frame block (5.8 ms), so the key-up moves +-1 block
    against the note-on from take to take: a take let go a block later than the reference held ~6 ms more of the
    undamped note in the window, an excess ~25 dB over its knock at NOISE 48 and 127 alike, and A4's row read +0 dB
    at random (the FM-1 scan of 2026-10-18 19:35: USB -0.15, jack +0.23; host seeds 2 5 6 7;
    tests/py/test_vibes_knock_measure.py)"""
    x = cut(mx, ox, t0, t1)
    nn = len(x)
    w = np.hanning(nn)
    P = np.abs(np.fft.rfft(x * w)) ** 2
    Q = np.zeros_like(P)
    for d in range(-int(slack * 1000), int(slack * 1000) + 1):
        r = cut(mref, oref + d * FS // 1000, t0, t1)
        if len(r) == nn:
            Q = np.maximum(Q, np.abs(np.fft.rfft(r * w)) ** 2)
    ex = np.maximum(P - 1.26 * Q, 0).sum() * 2 / (nn * (w * w).sum())
    return float(10 * np.log10(ex + 1e-24))


def ms_db(x):
    w = np.hanning(len(x))
    return float(10 * np.log10((np.abs(np.fft.rfft(x * w)) ** 2).sum() * 2 / (len(x) * (w * w).sum()) + 1e-24))


def hr():
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    return hostrun


def env_db(x, hop=240):
    n = len(x) // hop
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x[:n * hop], float).reshape(n, hop) ** 2, axis=1)) + 1e-9)


def until_floor(e, drop=50.0):
    """an envelope (dB) up to its first frame drop dB under its peak: a note that ends inside the window (the voice's
    1 LSB end, the gate's fade to digital zero on USB) leaves a -180 dB step the rate search would read"""
    e = np.asarray(e, float)
    lo = np.nonzero(e < e.max() - drop)[0]
    return e[:lo[0]] if len(lo) else e


def pfit(tr, fps, rate, H=3):
    """a periodic wave (H harmonics of rate) on a quadratic trend: peak-to-peak of the periodic part, the harmonics'
    amplitudes, and the share of the detrended variance the fundamental explains (r2)"""
    tr = np.asarray(tr, float)
    t = np.arange(len(tr)) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    base = [np.ones_like(t), tn, tn * tn]
    A = np.column_stack(base + [g(2 * np.pi * h * rate * t) for h in range(1, H + 1) for g in (np.cos, np.sin)])
    c = np.linalg.lstsq(A, tr, rcond=None)[0]
    per = A[:, 3:] @ c[3:]
    det = tr - np.column_stack(base) @ np.linalg.lstsq(np.column_stack(base), tr, rcond=None)[0]
    h1 = A[:, 3:5] @ c[3:5]
    return dict(pp=float(per.max() - per.min()), amps=[float(np.hypot(c[3 + 2 * k], c[4 + 2 * k])) for k in range(H)],
                r2=float(np.var(h1) / (np.var(det) + 1e-12)))


def rate_of(tr, fps, guess, lo=0.4, hi=2.6):
    return float(hr().rate_of(np.asarray(tr, float), fps, guess, lo=lo, hi=hi))


def lfo_rate(tr, fps, lo_hz, hi_hz, H=6, pearson=False, octave=False):
    """the repetition rate (Hz) of a periodic trace whose harmonics may outweigh its fundamental (a band's level as a
    triangle LFO sweeps notches up and down: the jack's 1/4 at 69 BPM had harmonics 3 and 4 at 2.5x the fundamental's
    amplitude, and rate_of's harmonic sum picked the 3rd: PHASER SYNC read 1.72 Hz for 1.15). The period is the
    shortest lag at which the trace repeats itself: the autocorrelation's (over the whole trace's energy, so a long
    lag's few overlapping frames do not win) first peak of at least 0.85 of its best one between 1 / hi_hz and
    1 / lo_hz (at most 80 % of the trace), refined by a least-squares fit of H harmonics on a quadratic trend
    (+-8 %). A trace shorter than a period returns about the longest lag (tests/py/test_phaser_measures.py).
    pearson: each lag's correlation over its own overlap (lags up to 0.6 of the trace), not over the whole trace's
    energy, which shrinks a long lag's peak by its overlap: MARIMBA's PHASER SPEED 0 (a 4 s period, 3 dips a cycle
    on the roll) correlates 0.63 at 2 s and 0.99 at 4 s, but 0.50 / 0.58 by energy, and the 2 s peak passed the 0.85
    rule: 0.50 Hz on the host and the FM-1.
    octave: the octave check: a rate f whose fit (2H harmonics of f) holds under 10 % of its even harmonics' power in
    its odd ones is a wave at 2f read at two periods; then 2f (refined the same way) when 2f <= hi_hz"""
    d = detrend2(np.asarray(tr, float))
    n = len(d)
    lmin = max(2, int(fps / hi_hz))
    lmax = max(lmin + 2, min(int((0.6 if pearson else 0.8) * n), int(np.ceil(fps / lo_hz))))
    r = np.full(lmax + 2, -1.0)
    e = np.dot(d, d) + 1e-30
    with np.errstate(invalid="ignore", divide="ignore"):  # (pearson on a flat stretch: nan, never a peak)
        for lag in range(lmin, min(lmax + 2, n - 1)):
            r[lag] = np.corrcoef(d[:-lag], d[lag:])[0, 1] if pearson else np.dot(d[:-lag], d[lag:]) / e
    pk = [lag for lag in range(lmin + 1, lmax + 1) if r[lag] >= r[lag - 1] and r[lag] >= r[lag + 1] and r[lag] > 0]
    best = max((r[lag] for lag in pk), default=None)
    l0 = min(lag for lag in pk if r[lag] >= 0.85 * best) if pk else lmax
    t = np.arange(n) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    trend = [np.ones_like(t), tn, tn * tn]

    def resid(f):
        A = np.column_stack(trend + [g(2 * np.pi * h * f * t) for h in range(1, H + 1) for g in (np.cos, np.sin)])
        return np.sum((d - A @ np.linalg.lstsq(A, d, rcond=None)[0]) ** 2)
    fine = fps / l0 * np.linspace(0.92, 1.08, 161)
    f0 = float(fine[int(np.argmin([resid(f) for f in fine]))])
    if octave and 2 * f0 <= hi_hz:
        A = np.column_stack(trend + [g(2 * np.pi * h * f0 * t) for h in range(1, 2 * H + 1) for g in (np.cos, np.sin)])
        c = np.linalg.lstsq(A, d, rcond=None)[0][3:]
        p = c[0::2] ** 2 + c[1::2] ** 2                 # (the power of harmonics 1 .. 2H)
        if p[0::2].sum() < 0.1 * p[1::2].sum():
            fine = 2 * f0 * np.linspace(0.96, 1.04, 81)
            f0 = float(fine[int(np.argmin([resid(f) for f in fine]))])
    return f0


def stft(x, n=1024, hop=240):
    w = np.hanning(n)
    idx = np.arange(0, len(x) - n, hop)
    F = np.abs(np.fft.rfft(np.stack([x[i:i + n] * w for i in idx]), axis=1)) ** 2
    return F, np.fft.rfftfreq(n, 1 / FS), FS / hop


def cen_trace(x):
    F, fr, fps = stft(x)
    s = (fr > 100) & (fr < 12000)
    return (F[:, s] * fr[s]).sum(1) / (F[:, s].sum(1) + 1e-30), fps


def motion_db(x):
    """spectral motion: 1/3-octave bands (250 Hz .. 10 kHz) in 5 ms frames, each band's level with its quadratic
    trend (the notes' decay) taken out: the mean standard deviation (dB) over the bands within 40 dB of the
    loudest. A swept filter / phaser / chorus moves it, a static sound leaves it near 0."""
    F, fr, fps = stft(x)
    rows = []
    for c in [250 * 2 ** (i / 3) for i in range(17)]:
        s = (fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))
        if s.any():
            rows.append(10 * np.log10(F[:, s].sum(1) + 1e-30))
    B = np.array(rows)
    t = np.arange(B.shape[1]) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    A = np.column_stack([np.ones_like(t), tn, tn * tn])
    R = B - (A @ np.linalg.lstsq(A, B.T, rcond=None)[0]).T
    keep = B.mean(1) > B.mean(1).max() - 40
    return float(R[keep].std(1).mean())


def hd_db(x, f):
    """harmonic distortion: H2 + H3 + H5 + H6 re H1 (dB); a bar's own partials sit at 4x and 10x, not there"""
    nf = 1 << (int(np.ceil(np.log2(len(x)))) + 1)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)

    def p(g):
        return S[(fr > g * 0.98) & (fr < g * 1.02)].sum()
    return float(10 * np.log10(sum(p(k * f) for k in (2, 3, 5, 6)) / (p(f) + 1e-30) + 1e-30))


def env_lin(x, w=96, hop=48):
    return np.sqrt(np.convolve(x * x, np.ones(w) / w, "same"))[::hop]       # 1 ms frames


def echo_levels(x, T_ms, kmax=10):
    """the echoes' levels (dB re the direct sound's peak) at k * T, k = 1 .. kmax"""
    e = env_lin(x)
    p0 = int(np.argmax(e[:50]))
    d = e[p0] + 1e-12
    out = []
    for k in range(1, kmax + 1):
        c = p0 + int(k * T_ms)
        a, b = c - max(10, int(0.1 * T_ms)), c + max(15, int(0.1 * T_ms))
        if b >= len(e):
            break
        out.append(float(20 * np.log10(e[max(0, a):b].max() / d + 1e-12)))
    return out


def n_echoes(lv, thr=-40.0):
    n = 0
    for v in lv:
        if v < thr:
            break
        n += 1
    return n


def tail_t60(x):
    """x starts 0.2 s after the key-up: the tail's T60 (s) from its envelope (20 ms), fitted down 30 dB"""
    e = env_db(x, 960)
    e0 = e[0]
    k = np.flatnonzero(e < e0 - 30)
    n = int(k[0]) if len(k) else len(e)
    n = max(n, 4)
    t = np.arange(n) * 0.02
    return float(-60.0 / np.polyfit(t, e[:n], 1)[0])


def corr_lr(st):
    return float(np.corrcoef(st[:, 0], st[:, 1])[0, 1])


# ---- round 2: the measures that stand above the floor (the jack's hiss: -92 dBFS; USB's own rounding) and the ones
# taken against a reference take of the same stimulus (the effect at 0): what the control adds, not the notes' own
# decay, beating or attack (round 1's failures, re-analysed offline: tools/hil_scan.py --offline)
def welch(x, n=4096):
    """the mean power spectrum of hann windows (50 % overlap): per-bin power of one n-window"""
    x = np.asarray(x, float)
    w = np.hanning(n)
    idx = range(0, max(1, len(x) - n + 1), n // 2)
    if len(x) < n:
        return None, np.fft.rfftfreq(n, 1 / FS)
    return np.mean([np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2 for i in idx], 0), np.fft.rfftfreq(n, 1 / FS)


def motion_f(x, fl=None, margin=10.0):
    """motion_db() over the bands that stand margin dB over the floor (fl: a pre-roll) 90 % of the time: the jack's
    hiss bands move at random in every take alike and diluted the difference (PHASER DEPTH, round 1: +3.1 dB USB,
    +1.8 jack)"""
    F, fr, fps = stft(x)
    Ff = stft(fl)[0].mean(0) if fl is not None and len(fl) > 2048 and np.any(fl) else None
    rows, fls = [], []
    for c in [250 * 2 ** (i / 3) for i in range(17)]:
        s = (fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))
        if s.any():
            rows.append(10 * np.log10(F[:, s].sum(1) + 1e-30))
            fls.append(10 * np.log10(Ff[s].sum() + 1e-30) if Ff is not None else -1e9)
    B, FL = np.array(rows), np.array(fls)
    t = np.arange(B.shape[1]) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    A = np.column_stack([np.ones_like(t), tn, tn * tn])
    R = B - (A @ np.linalg.lstsq(A, B.T, rcond=None)[0]).T
    keep = (B.mean(1) > B.mean(1).max() - 40) & (np.percentile(B, 10, axis=1) > FL + margin)
    return float(R[keep].std(1).mean()) if keep.any() else float("nan")


def band_trace(x, lo, hi, n=2048, hop=240):
    """a band's power per frame (5 ms hop)"""
    fr = np.fft.rfftfreq(n, 1 / FS)
    m = (fr >= lo) & (fr < hi)
    w = np.hanning(n)
    idx = np.arange(0, len(x) - n, hop)
    return np.array([np.sum(np.abs(np.fft.rfft(x[i:i + n] * w))[m] ** 2) for i in idx]), FS / hop


def rel_trace(t, r, q, a, b, lo, hi):
    """a band's level (dB, 5 ms frames) of take t re the reference take r (the same stimulus, the effect at 0), from
    each one's onset: what the control moves, not the chord's decay and beating; and its distance over t's floor"""
    x, y = cut(t.m(q), t.on(q)[0], a, b), cut(r.m(q), r.on(q)[0], a, b)
    n = min(len(x), len(y))
    A, fps = band_trace(x[:n], lo, hi)
    B, _ = band_trace(y[:n], lo, hi)
    fl = t.pre(q)
    F = band_trace(fl, lo, hi)[0].mean() if len(fl) > 2400 and np.any(fl) else 0.0
    return 10 * np.log10((A + 1e-30) / (B + 1e-30)), 10 * np.log10(A / (F + 1e-30) + 1e-30), fps


def detrend2(d):
    k = np.arange(len(d))
    return d - np.polyval(np.polyfit(k, d, 2), k)


def p2p(d, lo=2, hi=98):
    return float(np.percentile(d, hi) - np.percentile(d, lo))


def gain_trace(t, r, q, a, b, hop=240):
    """the linear gain of take t re the reference r per 5 ms frame (a tremolo's own wave: in dB a sine AM of 40+ dB
    is all cusps, round 1's TREM SHAPE read the sine as the squarer one)"""
    x, y = cut(t.m(q), t.on(q)[0], a, b), cut(r.m(q), r.on(q)[0], a, b)
    n = min(len(x), len(y)) // hop * hop
    ex = np.sqrt(np.mean(x[:n].reshape(-1, hop) ** 2, 1))
    ey = np.sqrt(np.mean(y[:n].reshape(-1, hop) ** 2, 1))
    return ex / (ey + 1e-12)


def edb1(x, w=96, hop=48):
    """the power envelope in dB, 1 ms frames (a 2 ms window)"""
    return 10 * np.log10(np.convolve(np.asarray(x, float) ** 2, np.ones(w) / w, "same")[::hop] + 1e-20)


def echo_cep_ms(x, y, b_s, lo, hi):
    """the delay as the peak of the cepstrum of x minus y's (y: the same stimulus without the echo): an echo is a
    ripple of the log spectrum, the notes' own pitch cancels; (ms, the peak's z-score)"""
    a, b = int(0.005 * FS), int(b_s * FS)
    n = 1 << int(np.ceil(np.log2(2 * b)))

    def cep(s):
        s = s[a:b]
        return np.fft.irfft(np.log(np.abs(np.fft.rfft(s * np.hanning(len(s)), n)) + 1e-9))
    m = min(len(x), len(y))
    c = cep(x[:m]) - cep(y[:m])
    qs = np.arange(int(lo * FS / 1000), int(hi * FS / 1000))
    k = qs[int(np.argmax(c[qs]))]
    return k * 1000.0 / FS, float(c[k] / (np.std(c[qs]) + 1e-12))


def echo_ms(x, y, exp_ms=None, flr_db=-120.0, t_min=30, rel=-20.0):
    """the first echo's arrival (ms; x and y from their onsets; y: the delay at DEPTH 0, the same stimulus). Round 1
    took the first envelope peak after the direct sound's: a vibraphone note keeps rising 60-90 ms (the tube), so
    it read 33-42 ms for every TIME. Here: a short delay (exp_ms <= 60: a comb on the note itself) by the cepstrum
    against y; else the first frame from t_min where x stands 6 dB over y (the direct sound with the delay's dry
    share) and over its floor, for 3 ms, within rel dB of the direct's peak (a residue of an earlier take is not an
    echo)"""
    if exp_ms is not None and exp_ms <= 60:
        k, z = echo_cep_ms(x, y, 0.3, 5, 60)
        return k if z >= 6 else float("nan")
    n = min(len(x), len(y))
    ex, ey = edb1(x[:n]), edb1(y[:n])

    def mis(k):
        d = (ex - np.roll(ey, k))[10:80]
        return np.sum(np.abs(d - np.median(d)))
    ey = np.roll(ey, min(range(-6, 7), key=mis))           # the two takes' onsets to the ms
    base = np.median((ex - ey)[5:15])
    top = ex[:150].max()
    exc = np.where(ex >= top + rel, ex - np.maximum(ey + base, flr_db + 6), -99.0)
    ok = np.flatnonzero((exc[t_min:-3] >= 6) & (exc[t_min + 1:-2] >= 6) & (exc[t_min + 2:-1] >= 6))
    if not len(ok):
        return float("nan")
    k = int(ok[0]) + t_min
    while k > t_min and ex[k - 1] - max(ey[k - 1] + base, flr_db + 6) >= 1.0:   # back to where it began to stand out
        k -= 1
    return float(k)


def floor_db(t, q):
    """the source's floor (dBFS rms, the pre-roll); -120 when there is none (USB: exact zeros)"""
    fl = t.pre(q)
    return rdb(fl) if len(fl) > 2000 and np.any(fl) else -120.0


def pk_rel(x, sp, keys, ref="V1"):
    """bar partials (bspec names) re V1, dB"""
    l1 = pk_db(x, sp[ref])
    return [pk_db(x, sp[k]) - l1 for k in keys]


def finite(v):
    try:
        return all(np.isfinite(np.asarray(v, float)).ravel())
    except Exception:
        return v is not None


# ---------------------------------------------------------------- checks and the report
class VSuite:
    def __init__(self, name, V):
        self.name, self.V, self.rows, self.t0 = name, V, [], time.time()

    def per(self, fn, srcs=None):
        """fn(src) on every recorded source (USB: its left channel, as the jack's cable)"""
        return {s: _safe(fn, s) for s in (srcs or self.V.sources())}

    def usb(self, fn):
        return {"usb": _safe(fn, "usb")} if self.V.want_usb else {}

    def check(self, check, setting, vals, ok, crit, level=False, fmt=None, info=False, floor=None):
        """one row: vals {src: value}; ok(value) -> bool on each source; level: a level change in dB / LU, compared
        between USB and the jack; floor {src: True}: that source's value stands on its floor (the jack's hiss), so the
        two are not compared (no MISMATCH) and its cell says so"""
        fmt = fmt or (lambda v: f"{v:+.2f}")
        res, txt = {}, {}
        for s in ("usb", "jack"):
            if s not in vals:
                txt[s] = "n/a" if s in self.V.sources() else "-"
                continue
            v = vals[s]
            if v is None or not finite(v):
                res[s], txt[s] = False, "no value"
                continue
            try:
                res[s] = bool(ok(v))
            except Exception:
                res[s] = False
            txt[s] = (_safe(fmt, v) or str(v)) + (" (floor)" if floor and floor.get(s) else "")
        mm = None
        if level and all(s in vals and vals[s] is not None and finite(vals[s]) for s in ("usb", "jack")) and \
                not (floor and any(floor.values())):
            mm = float(vals["jack"] - vals["usb"])
        if not res and not info:                       # (a USB-only measure with --source jack)
            self.rows.append(dict(check=check, setting=setting, usb=txt["usb"], jack=txt["jack"], crit=crit,
                                  mismatch=None, status="skip", ok=True))
            return
        passed = info or all(res.values())
        # (a large change: 5 % of it, the analogue path's own spread; round 1: REVERB 0 -> 127, +36.8 / +35.7)
        bad_mm = mm is not None and abs(mm) > max(MISMATCH_DB, 0.05 * abs(vals["usb"]))
        why = m_known(check, setting) if not passed and self.name.startswith("marimba_") else None
        status = ("info" if info else "ok" if passed else "KNOWN" if why else "FAIL") + (" MISMATCH" if bad_mm else "")
        row = dict(check=check, setting=setting, usb=txt["usb"], jack=txt["jack"], crit=crit,
                   mismatch=mm, status=status, ok=bool((passed or why) and not bad_mm),
                   src_ok=None if info else res)            # per source (tools/full_scan.py: the jack decides)
        if why:
            row["known"] = why                              # (a MARIMBA user decision: m_known)
        self.rows.append(row)

    def known(self):
        return [r for r in self.rows if r.get("known")]

    def fails(self):
        return [r for r in self.rows if not r["ok"]]


def v_report(suites, V, path, dry):
    tk = V.takes
    eng = "MARIMBA" if all(S.name.startswith("marimba_") for S in suites) else "VIBES"
    L = [f"# {eng} device scan (tools/hil_scan.py){' - DRY RUN (synthetic audio, the numbers mean nothing)' if dry else ''}",
         "", f"- date: {time.strftime('%Y-%m-%d %H:%M')}; sources: {', '.join(V.sources())}"
         + (f" (jack: {V.jack_name})" if V.jack_name else ""),
         "- USB = the FM-1's USB audio, LEFT channel (what the jack cable carries) unless the row says stereo; jack = "
         "the headphone jack through the interface. Levels are changes within one source (the interface's gain "
         f"cancels); 'USB-jack' is their difference, flagged (MISMATCH) above {MISMATCH_DB:.1f} dB.",
         f"- VIBES by CC 79 = {VIBES_CC79}, MARIMBA by CC 79 = {MARIMBA_CC79} (the marimba_* suites); every reset: CC 120, every parameter at its default, the DRY dict, the "
         "motor off unless the row is about it. CCs read from firmware/src/params.c PARAMS[].",
         "- Not testable over MIDI: KEY VEL (the FM-1's own keys only), TUNE's other values (DECAY page K4 has no CC; "
         "only the current value is measured), SPREAD (not in the engine), the physical keys and screen.", "",
         "## Summary", "", "| suite | checks | failed / flagged | takes | time |", "|---|---|---|---|---|"]
    for S in suites:
        L.append(f"| {S.name} | {len(S.rows)} | {len(S.fails())} | {S.ntakes} | {S.secs:.0f} s |")
    for S in suites:
        L += ["", f"## {S.name}", "", "| check | setting | USB | jack | criterion | USB-jack | result |",
              "|---|---|---|---|---|---|---|"]
        for r in S.rows:
            mm = f"{r['mismatch']:+.2f}" if r["mismatch"] is not None else ""
            L.append(f"| {r['check']} | {r['setting']} | {r['usb']} | {r['jack']} | {r['crit']} | {mm} | "
                     f"{'**' + r['status'] + '**' if not r['ok'] else r['status']} |")
        kn = S.known()
        if kn:
            L += ["", "KNOWN (the plan's threshold, not met as written; user decisions, tools/marimba_scan.py KNOWN):", ""]
            for r in kn:
                L.append(f"- {r['check']} [{r['setting']}]: {r['known']}")
    bad = [t for t in tk if t.get("usb_fs") or t.get("usb_clicks") or t.get("jack_clicks") or t.get("jack_over")]
    L += ["", "## Every take: clipping and clicks", "",
          "Criteria: no USB sample at full scale (-32768 / +32767); 0 clicks (hil_scan clicks(), outside 3 ms before / "
          "17 ms after each note event, a switch is not guarded); the jack's interface input not overloaded "
          "(|x| >= 0.999: its numbers are then not valid). 'raw' = clicks() with no guard (information). 'slip': the "
          f"largest latency step between two note-ons (USB: of its latency re the jack's, recorded at once; over "
          f"{SLIP_MS:.0f} ms its stream slipped and that strike's USB numbers are not the sound). 'pre-roll': USB before the "
          "first event (above -240: a residue of the last take, e.g. a delay line still sounding).", "",
          "| take | s | USB peak dBFS | full-scale | >= 32700 | clicks L+R (raw) | jack peak dBFS | jack over | jack clicks |"
          " slip USB re jack / jack ms | pre-roll USB dBFS |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in tk:
        L.append(f"| {t['tag']} | {t['dur']:.1f} | {t.get('usb_peak_db', float('nan')):.1f} | {t.get('usb_fs', '-')} | "
                 f"{t.get('usb_near', '-')} | {t.get('usb_clicks', '-')} ({t.get('usb_clicks_raw', '-')}) | "
                 f"{t.get('jack_peak_db', float('nan')):.1f} | {t.get('jack_over', '-')} | {t.get('jack_clicks', '-')} | "
                 f"{t.get('usb_slip_ms', 0):.0f} / {t.get('jack_slip_ms', 0):.0f} | {t.get('usb_pre_db', -240):.0f} |")
    L += ["", f"Takes with a problem: {', '.join(t['tag'] for t in bad) or 'none'}", ""]
    open(path, "w").write("\n".join(L))
    json.dump(dict(suites={S.name: S.rows for S in suites}, takes=tk), open(path[:-3] + ".json", "w"), indent=1,
              default=float)
    return bad


# ---------------------------------------------------------------- stimuli
def strikes(keys, vels, t0=0.2, gap=2.5, hold=1.2):
    """one strike per (key, velocity), gap apart, each held hold s: events and the take's length"""
    ev, t = [], t0
    for n in keys:
        for v in vels:
            ev += [(t, "on", n, v), (t + hold, "off", n, 0)]
            t += gap
    return ev, t - gap + hold + 0.8


def chord(notes, vel, on, off):
    return [(on, "on", n, vel) for n in notes] + [(off, "off", n, 0) for n in notes]


VKEYS = (53, 69, 89)                      # F3 A4 F6, the host tests' keys
FXCH = (48, 55, 60, 64)                   # hil_scan fx_all's chord, v108 (the FM-1 keys' velocity)
ST_CH = (3.6, chord(FXCH, 108, 0.1, 2.1))         # the chord, 2 s, then 1.5 s of tail
ST_LONG = (6.0, chord(FXCH, 108, 0.1, 5.6))       # held 5.5 s: slow rates
ST_NOTE = (2.0, chord((69,), 108, 0.1, 1.6))      # A4 alone: harmonics (the drive)
ST_STAC = (3.4, chord((69,), 108, 0.1, 0.25))     # A4 staccato: echoes, the reverb's tail
ST_QUIET = (2.0, chord((69,), 40, 0.1, 1.6))      # A4 v40: LEVEL's +12 dB stays under the limiter
ST_REL = (2.0, chord(FXCH, 127, 0.1, 0.6) + chord((69,), 40, 0.9, 1.6))   # loud, then soft: the release
# PHASER and CHORUS SPEED 0 (0.25 Hz: a 4 s period) on VIBES: the chord struck 3 times, 3.3 s apart (held, it is
# under the gate's -66 dBFS by ~6.5 s with the motor off): a 9.6 s trace, 2.4 periods (the takes and their DEPTH 0
# references share the strikes; host seeds 1-5: 0.25 Hz, both effects)
ST_PH0 = (10.5, sorted(chord(FXCH, 108, 0.1, 3.3) + chord(FXCH, 108, 3.4, 6.6) + chord(FXCH, 108, 6.7, 10.1),
                       key=lambda e: e[0]))


def fmt_s(v):
    return " / ".join(f"{x:.2f}" for x in v)


# ---------------------------------------------------------------- VOICE
@vtest
def vibes_voice(V, S):
    """VOICE: STRIKE (TONE), MALLET (BARK), TUBE (BELL), WEIGHT (HAMMER) at 0 / 64 / 127; F3 A4 F6 at v100"""
    ev, dur = strikes(VKEYS, (100,))
    V.reset(label="VOICE all 64")
    T = {64: V.take("voice-default", dur, ev)}
    for card in ("TONE", "BARK", "BELL", "HAMMER"):
        for x in (0, 127):
            V.reset([(card, x)], label=f"VOICE {card} {x}")
            T[(card, x)] = V.take(f"voice-{card}-{x}", dur, ev)
    cache = {}

    def F(key, src, k):
        if (key, src, k) not in cache:
            t = T[key]
            cache[(key, src, k)] = strike_feats(t.m(src), t.on(src)[k], VKEYS[k])
        return cache[(key, src, k)]

    def key(c, x):
        return 64 if x == 64 else (c, x)
    for k, n in enumerate(VKEYS):
        def d(c, a, b, src):
            """the jack over the bands the sound has on USB (when USB is recorded): the jack adds its own harmonics
            (F3 on the FM-1: H2 -56, H3 -50, H5 -57 dB re the strike, absent on USB), the same in both takes, and
            sdist's mean over them diluted every jack distance (MALLET 64 vs 127 on F3: USB 3.6, the jack 2.5 .. 2.7
            dB in every report since 2026-10-18; on USB's bands the jack reads 3.55, tools/hil_scan.py --offline of
            build/hil/vibes; tests/py/test_hil_limits.py). Not the jack's hiss: its floor sits at -77 dB, 20 dB under
            those harmonics, so a lower jack limit (agent E proposed 2.0 dB, 2026-10-24) would only hide them)"""
            use = None
            if src == "jack" and V.want_usb:
                use = _safe(lambda: sdist_bands(F(key(c, a), "usb", k)["bands"], F(key(c, b), "usb", k)["bands"]))
            return sdist(F(key(c, a), src, k)["bands"], F(key(c, b), src, k)["bands"], use)

        def lv(c, a, b, src):
            return F(key(c, b), src, k)["lufs"] - F(key(c, a), src, k)["lufs"]
        s = f"key {n} v100"
        lab = dict(TONE="STRIKE (CC %d)" % vp()["TONE"]["cc"], BARK="MALLET (CC %d)" % vp()["BARK"]["cc"],
                   BELL="TUBE (CC %d)" % vp()["BELL"]["cc"], HAMMER="WEIGHT (CC %d)" % vp()["HAMMER"]["cc"])
        S.check(f"{lab['TONE']} 0 vs 127", s, S.per(lambda q: d("TONE", 0, 127, q)), lambda v: v >= 3,
                ">= 3 dB spectral distance", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['TONE']} 64 vs 127", s, S.per(lambda q: d("TONE", 64, 127, q)), lambda v: v >= 3,
                ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['TONE']} 0 vs 64", s, S.per(lambda q: (d("TONE", 0, 64, q), lv("TONE", 64, 0, q))),
                lambda v: v[0] >= 3 or v[1] >= 2, ">= 3 dB, or 0 louder by >= 2 LU",
                fmt=lambda v: f"{v[0]:.1f} dB / {v[1]:+.1f} LU")
        S.check(f"{lab['BARK']} 0 vs 127", s, S.per(lambda q: d("BARK", 0, 127, q)), lambda v: v >= 3, ">= 3 dB",
                fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['BARK']} 64 vs 127", s, S.per(lambda q: d("BARK", 64, 127, q)), lambda v: v >= 3, ">= 3 dB",
                fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['BARK']} 0 vs 64", s, S.per(lambda q: d("BARK", 0, 64, q)), lambda v: v >= 3,
                ">= 3 dB" + (" (F6: information, the default is already a pure fundamental)" if n == 89 else ""),
                fmt=lambda v: f"{v:.1f} dB", info=n == 89)
        S.check(f"{lab['BELL']} level 0 -> 127", s, S.per(lambda q: lv("BELL", 0, 127, q)),
                lambda v: 6.0 <= v <= 12.5, "+6 .. +12.5 LU", level=True)
        S.check(f"{lab['BELL']} level 0 -> 64", s, S.per(lambda q: lv("BELL", 0, 64, q)), lambda v: v >= 4.0,
                ">= +4 LU", level=True)
        S.check(f"{lab['BELL']} level 64 -> 127", s, S.per(lambda q: lv("BELL", 64, 127, q)), lambda v: v >= 0.5,
                ">= +0.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 0 -> 127", s, S.per(lambda q: lv("HAMMER", 0, 127, q)), lambda v: v >= 2.5,
                ">= +2.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 0 -> 64", s, S.per(lambda q: lv("HAMMER", 0, 64, q)), lambda v: v >= 1.5,
                ">= +1.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 64 -> 127", s, S.per(lambda q: lv("HAMMER", 64, 127, q)), lambda v: v >= 0.5,
                ">= +0.5 LU", level=True)
        S.check(f"{lab['HAMMER']} 0 vs 127 timbre, attack centroid", s,
                S.per(lambda q: (d("HAMMER", 0, 127, q), F(("HAMMER", 0), q, k)["cen_a"] / F(("HAMMER", 127), q, k)["cen_a"])),
                lambda v: v[0] >= 3 and v[1] > 1.05,
                ">= 3 dB and light brighter (centroid ratio > 1.05)" + (" (F6: information)" if n == 89 else ""),
                fmt=lambda v: f"{v[0]:.1f} dB / x{v[1]:.2f}", info=n == 89)
        S.check("V2 / V3 re V1 at the defaults", s, S.per(lambda q: (F(64, q, k)["v2"], F(64, q, k)["v3"])),
                lambda v: True, "information (bar_cards V2 V3)", fmt=lambda v: f"{v[0]:.1f} / {v[1]:.1f} dB", info=True)


# ---------------------------------------------------------------- DECAY page, the pedal, TUNE
@vtest
def vibes_decay(V, S):
    """DECAY: RING (DECAY), DAMPER (RELEASE), NOISE at 0 / default / max, the sustain pedal (CC 64, half pedal 50),
    TUNE (its current value)"""
    P = vp()
    # RING: F3 + A4 + F6 held 10.5 s (no partial of one near another's fundamental)
    ev = chord(VKEYS, 100, 0.2, 10.7)
    T = {}
    for x in (0, P["DECAY"]["de"], 127):
        V.reset([("DECAY", x)], label=f"RING {x}")
        T[x] = V.take(f"ring-{x}", 11.2, ev)
    for k, n in enumerate(VKEYS):
        def t3(q):
            return [t60(cut(T[x].m(q), T[x].on(q)[k], 0, 10.4), f1(n)) for x in sorted(T)]
        S.check(f"RING (CC {P['DECAY']['cc']}) T60 at 0 / {P['DECAY']['de']} / 127", f"key {n} v100, held",
                S.per(t3), lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 2.0, "in order, 127 / 0 >= 2x",
                fmt=lambda v: fmt_s(v) + " s")
    # DAMPER and the pedal: the decay 30..300 ms after the key-up (NOISE 0: no knock)
    ev = chord(VKEYS, 100, 0.2, 1.7)
    for what, name, xs in (("DAMPER", "RELEASE", (0, P["RELEASE"]["de"], 127)), ("pedal", None, (0, 50, 127))):
        T = {}
        for x in xs:
            if name:
                V.reset([(name, x), ("NOISE", 0)], label=f"{what} {x}")
                T[x] = V.take(f"{what.lower()}-{x}", 2.6, ev)
            else:
                V.reset([("NOISE", 0)], label=f"pedal {x}")
                T[x] = V.take(f"pedal-{x}", 2.6, ev + [(0.05, "cc", 64, x)])
        for k, n in enumerate(VKEYS):
            def r3(q):
                return [release_rate(cut(T[x].m(q), T[x].on(q)[k], 0, 2.3), f1(n), 1.5) for x in xs]
            if name:
                S.check(f"DAMPER (CC {P['RELEASE']['cc']}) stop 0 vs 127", f"key {n}, decay after the key-up",
                        S.per(r3), lambda v: v[0] / v[2] >= 3.0, "firm / soft >= 3x (dB/s)",
                        fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s (x{v[0] / v[2]:.1f})")
                S.check("DAMPER default in between", f"key {n}", S.per(r3), lambda v: v[0] > v[1] > v[2],
                        "0 > 64 > 127", fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f}")
            else:
                S.check("sustain pedal CC 64: up vs down", f"key {n}, decay after the key-up", S.per(r3),
                        lambda v: v[0] / v[2] >= 3.0, "up / down >= 3x",
                        fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s (x{v[0] / v[2]:.1f})")
                S.check("half pedal (CC 64 = 50) in between", f"key {n}", S.per(r3), lambda v: v[2] < v[1] < v[0],
                        "down < half < up", fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f}")
    # NOISE: what it adds to the strike (0..60 ms) and after the key-up (0..150 ms: the damper's knock)
    ev, dur = strikes(VKEYS, (100,), gap=3.0, hold=1.5)
    T = {}
    for x in (0, P["NOISE"]["de"], 127):
        V.reset([("NOISE", x)], label=f"NOISE {x}")
        T[x] = V.take(f"noise-{x}", dur, ev)
    de = P["NOISE"]["de"]
    for k, n in enumerate(VKEYS):
        def nz(q):
            o = {x: T[x].on(q)[k] for x in T}
            m = {x: T[x].m(q) for x in T}
            ring = ms_db(cut(m[0], o[0], 0, 0.06))
            c = {x: excess_db(cut(m[x], o[x], 0, 0.06), cut(m[0], o[0], 0, 0.06)) - ring for x in (de, 127)}
            kn = {x: knock_db(m[x], o[x], m[0], o[0]) for x in (de, 127)}
            # the floor: two takes of hiss alone leave an excess 3.5 dB under the hiss (E[max(P - 1.26 Q, 0)] =
            # P / 2.26 for exponential bins); a value within 6 dB of it is the jack's hiss, not NOISE (round 1:
            # MISMATCH on F6's knock, +54.5 USB / +40.9 jack)
            pr = [T[x].pre(q) for x in T]
            fx = max((10 * np.log10(np.mean(np.asarray(p_, float) ** 2) + 1e-24) for p_ in pr if len(p_) > 2000 and np.any(p_)),
                     default=-240.0) - 3.5 + 6.0
            return dict(c=c, k=kn, strike=rdb(cut(m[0], o[0], 0.05, 0.3)), note=rdb(cut(m[0], o[0], 1.4, 1.5)),
                        cfl=c[de] + ring < fx, kfl=kn[de] < fx)
        vals = S.per(nz)
        CC = P["NOISE"]["cc"]
        S.check(f"NOISE (CC {CC}) contact {de} -> 127", f"key {n}, 0..60 ms", {s: v and v["c"][127] - v["c"][de] for s, v in vals.items()},
                lambda v: v >= 6, ">= +6 dB", level=True, floor={s: bool(v and v["cfl"]) for s, v in vals.items()})
        S.check("NOISE 127 contact re the note", f"key {n}", {s: v and v["c"][127] for s, v in vals.items()},
                lambda v: v > -35, "> -35 dB (a clear tick)", fmt=lambda v: f"{v:.1f} dB")
        # (2026-10-25: the knock capped by the note's level left, never louder than the note; it rings at the bar's
        # pitch, in the note's own bins, so 48 -> 127 no longer grows 6 dB after a 1.5 s hold: the host test
        # test_vibes_knock_measure.py checks it is there; here, at 127 too, it stays under the note it damps)
        S.check("NOISE 127 knock under the note", f"key {n}, 0..150 ms after the key-up",
                {s: v and v["k"][127] - v["note"] for s, v in vals.items()}, lambda v: v < 0,
                "< the note it damps", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"NOISE {de} knock under the strike / the note", f"key {n}",
                {s: v and (v["k"][de] - v["strike"], v["k"][de] - v["note"]) for s, v in vals.items()},
                lambda v: v[0] < -18 and v[1] < 0, "< -18 dB re the strike, < the note it damps",
                fmt=lambda v: f"{v[0]:.1f} / {v[1]:.1f} dB")
    # TUNE: no CC (DECAY page K4 on the panel): the current value, which should be 0
    V.reset(label="TUNE (current)")
    t = V.take("tune-a4", 2.6, chord((69,), 100, 0.2, 2.2))
    S.check("TUNE (panel only, no MIDI CC): A4 re the spec", "the FM-1's current TUNE",
            S.per(lambda q: 1200 * np.log2(f0_of(cut(t.m(q), t.on(q)[0], 0.3, 1.8), f1(69)) / f1(69))),
            lambda v: abs(v) <= 2.0, "|cents| <= 2 (TUNE 0; other values cannot be set over MIDI)",
            fmt=lambda v: f"{v:+.2f} c")


# ---------------------------------------------------------------- MOTOR page
@vtest
def vibes_motor(V, S):
    """MOTOR (CC 47-51): ON, DEPTH, SPEED, SHAPE, SYNC; switching without a click"""
    P = vp()
    dflt = P["MOT_RATE"]["de"]
    ev = chord(VKEYS, 100, 0.3, 4.8)
    T = {}
    for lab, kv, mot in (("off", [("MOT_DEPTH", 127)], False), ("d0", [("MOT_DEPTH", 0)], True),
                         ("d110", [("MOT_DEPTH", P["MOT_DEPTH"]["de"])], True), ("d127", [("MOT_DEPTH", 127)], True),
                         ("s127", [("MOT_DEPTH", 127), ("MOT_SHAPE", 127)], True)):
        V.reset(kv, motor=mot, label=f"MOTOR {lab}")
        T[lab] = V.take(f"motor-{lab}", 5.2, ev)
    hz = mot_hz(dflt)
    fps = FS / 240

    def am(lab, q, k, rate=None):
        n = VKEYS[k]
        x = cut(T[lab].m(q), T[lab].on(q)[k], 0.5, 3.0 if n < 80 else 2.0)
        tr, _ = partial_trace(x, f1(n), n=2048, hop=240)
        return pfit(tr, fps, rate or hz)
    for k, n in enumerate(VKEYS):
        s = f"key {n}, {hz:.2f} Hz"
        S.check(f"MOTOR DEPTH (CC {P['MOT_DEPTH']['cc']}) 127", s, S.per(lambda q: am("d127", q, k)["pp"]),
                lambda v: v >= 6, ">= 6 dB AM", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"MOTOR DEPTH default {P['MOT_DEPTH']['de']}", s, S.per(lambda q: am("d110", q, k)["pp"]),
                lambda v: v >= 6, ">= 6 dB AM (params.c: ~8 dB)", fmt=lambda v: f"{v:.1f} dB")
        S.check("MOTOR DEPTH 0", s, S.per(lambda q: am("d0", q, k)["pp"]), lambda v: v < 1, "< 1 dB",
                fmt=lambda v: f"{v:.2f} dB")
        S.check(f"MOTOR OFF (CC {P['MOT_ON']['cc']} = 0), DEPTH 127", s, S.per(lambda q: am("off", q, k)["pp"]),
                lambda v: v < 1, "< 1 dB", fmt=lambda v: f"{v:.2f} dB")
        if n < 80:
            def h21(lab, q):
                a = am(lab, q, k)["amps"]
                return a[1] / (a[0] + 1e-12)
            S.check(f"MOTOR SHAPE (CC {P['MOT_SHAPE']['cc']}) 0 vs 127", s,
                    S.per(lambda q: (h21("d127", q), h21("s127", q))), lambda v: v[1] >= 1.5 * v[0],
                    "the pulse's H2/H1 x1.5 or more", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    # SPEED: A4 alone, the whole signal's envelope
    one = chord((69,), 100, 0.3, 6.3)

    def rate(t, q, guess, lo=0.4, hi=2.6):
        # until the note falls 50 dB: with the motor A4 rings as long as dry (round 2: the coupling no longer
        # lengthens it) and its end, inside 5.8 s, would be read as part of the AM
        return rate_of(until_floor(env_db(cut(t.m(q), t.on(q)[0], 0.5, 5.8))), fps, guess, lo, hi)
    for x in (0, dflt, 127):
        V.reset([("MOT_DEPTH", 127), ("MOT_RATE", x)], motor=True, label=f"MOTOR SPEED {x}")
        t = V.take(f"motor-rate-{x}", 6.6, one)
        e = mot_hz(x)
        tol = 0.10 if x == 0 else 0.05
        S.check(f"MOTOR SPEED (CC {P['MOT_RATE']['cc']}) {x}", f"A4, expected {e:.2f} Hz",
                S.per(lambda q: rate(t, q, e)), lambda v: abs(v / e - 1) <= tol, f"within {tol:.0%}",
                fmt=lambda v: f"{v:.2f} Hz")
    R = {}
    for nm in ("1/4", "1/8"):
        V.reset([("MOT_DEPTH", 127), ("MOT_SYNC", sync_idx(nm))], motor=True, label=f"MOTOR SYNC {nm}")
        R[nm] = V.take(f"motor-sync-{nm.replace('/', '')}", 6.6, one)
    S.check(f"MOTOR SYNC (CC {P['MOT_SYNC']['cc']}) 1/8 vs 1/4", "A4; BPM = the FM-1's tempo",
            S.per(lambda q: (rate(R["1/8"], q, 2.0, 0.2, 5.0), rate(R["1/4"], q, 2.0, 0.2, 5.0))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "rate ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz (x{v[0] / v[1]:.2f}, {60 * v[1]:.0f} BPM)")
    # switching on / off while a note rings (the pedal down): no click, the AM comes and goes
    sw = P["MOT_ON"]["cc"]
    V.reset([("MOT_DEPTH", 127), ("MOT_RATE", dflt)], label="MOTOR switching")
    ev = [(0.05, "cc", 64, 127)] + chord((57,), 100, 0.3, 5.3) + [(0.3 + t, "cc", sw, v) for t, v in
                                                                   ((0.7, 127), (1.7, 0), (2.7, 127), (3.2, 0))]
    t = V.take("motor-switch", 4.5, ev)

    def sw_meas(q):
        m, i = t.m(q), t.on(q)[0]
        d2 = np.abs(np.diff(m, 2))
        ref = d2[i + int(0.3 * FS):i + int(0.65 * FS)].max()
        worst = max(d2[i + int((s - 0.02) * FS):i + int((s + 0.1) * FS)].max() for s in (0.7, 1.7, 2.7, 3.2)) / ref
        on = pfit(env_db(cut(m, i, 0.9, 1.65)), fps, hz)["pp"]
        off = pfit(env_db(cut(m, i, 1.9, 2.65)), fps, hz)["pp"]
        gain = 32768 if q == "usb" else 32768 * np.sqrt(np.mean(t.usb[:, 0] ** 2) / (np.mean(m ** 2) + 1e-20)) \
            if t.usb is not None else 32768 / (np.abs(m).max() + 1e-12) * 0.3
        ck = sum(clicks(m[i + int((s - 0.05) * FS):i + int((s + 0.15) * FS)] * gain) for s in (0.7, 1.7, 2.7, 3.2))
        return dict(worst=worst, on=on, off=off, ck=ck)
    v = S.per(sw_meas)
    S.check("MOTOR switched on / off under a held note: clicks", "4 switches", {s: x and x["ck"] for s, x in v.items()},
            lambda c: c == 0, "0 (clicks() around each switch)", fmt=lambda c: str(c))
    S.check("MOTOR switch: largest sample step", "re the note's own", {s: x and x["worst"] for s, x in v.items()},
            lambda c: c <= 1.2, "<= 1.2x (test_motor_switches_without_a_click)", fmt=lambda c: f"x{c:.2f}")
    S.check("MOTOR switch: AM on / off", "", {s: x and (x["on"], x["off"]) for s, x in v.items()},
            lambda c: c[0] >= 4 and c[1] < 1, "on >= 4 dB, off < 1 dB", fmt=lambda c: f"{c[0]:.1f} / {c[1]:.2f} dB")


# ---------------------------------------------------------------- velocity, every key
VELS = (1, 8, 24, 40, 64, 88, 104, 117, 120, 127)      # bar_scan VELS + 117 (the user's KEY VEL)


@vtest
def vibes_vel(V, S):
    """MIDI velocity on F3 A4 F6 (KEY VEL itself only plays the FM-1's own keys: not over MIDI); every key's level
    and tuning (bar_scan S9 / S6)"""
    for n in VKEYS:
        ev, dur = strikes((n,), VELS, gap=1.2, hold=0.7)
        V.reset(label=f"velocity {n}")
        t = V.take(f"vel-{n}", dur, ev)

        def lv(q):
            return [lu(cut(t.m(q), i, 0, 0.9)) for i in t.on(q)]
        vals = S.per(lv)
        S.check("velocity: no level drop as it rises", f"key {n}, v{VELS[0]}..v{VELS[-1]}",
                {s: v and float(min(np.diff(v[2:]))) for s, v in vals.items()}, lambda d: d >= -0.5,
                "every step >= -0.5 dB (from v24; v1 / v8 may be under the gate)", fmt=lambda d: f"{d:+.2f}")
        S.check("velocity: v40 -> v117", f"key {n}", {s: v and v[VELS.index(117)] - v[VELS.index(40)] for s, v in vals.items()},
                lambda d: d >= 10, ">= +10 LU", level=True)
        S.check("velocity: v1 .. v127 levels", f"key {n}", vals, lambda v: True, "information",
                fmt=lambda v: " ".join(f"{x:.0f}" for x in v), info=True)

        def bright(q):
            o = t.on(q)
            r = {}
            for v in (40, 117):
                x = cut(t.m(q), o[VELS.index(v)], 0, 0.05)
                sp = np.fft.rfft(x)
                sp[np.fft.rfftfreq(len(x), 1 / FS) < 3000] = 0
                r[v] = rdb(np.fft.irfft(sp, len(x))) - rdb(x)
            return r[117] - r[40]
        S.check("velocity: harder = brighter (> 3 kHz re level, 0..50 ms)", f"key {n}", S.per(bright),
                lambda d: d >= 3, ">= +3 dB (host: an expected failure, the VOICE work)", fmt=lambda d: f"{d:+.1f} dB",
                info=True)
    keys = list(range(21, 53, 3)) + list(range(53, 90)) + list(range(92, 109, 3))
    ev, dur = strikes(keys, (100,), gap=1.0, hold=0.6)
    V.reset(label="every key")
    t = V.take("keys", dur, ev)

    def kl(q):
        o = t.on(q)
        lv = np.array([lu(cut(t.m(q), i, 0, 0.9)) for i in o])
        c = np.array([1200 * np.log2(max(f0_of(cut(t.m(q), i, 0.05, 0.55), f1(n)), 1e-9) / f1(n)) for i, n in zip(o, keys)])
        return lv, c
    vals = S.per(kl)
    ins = [i for i, n in enumerate(keys) if 53 <= n <= 89]
    out_ = [i for i, n in enumerate(keys) if not 53 <= n <= 89]

    def med_dev(lv, idx):
        return [abs(lv[i] - np.median(lv[max(0, i - 3):i + 4])) for i in idx]
    S.check("keys F3..F6: neighbour step", "every key, v100", {s: v and float(np.abs(np.diff(v[0][ins])).max()) for s, v in vals.items()},
            lambda d: d <= 1.5, "<= 1.5 dB (bar_scan S9)", fmt=lambda d: f"{d:.2f} dB")
    S.check("keys F3..F6: off the +-3-key median", "", {s: v and float(max(med_dev(v[0], ins))) for s, v in vals.items()},
            lambda d: d <= 1.5, "<= 1.5 dB", fmt=lambda d: f"{d:.2f} dB")
    S.check("keys outside F3..F6 (every 3rd): off the median", "21..50, 92..107",
            {s: v and float(max(med_dev(v[0], out_))) for s, v in vals.items()}, lambda d: d <= 3, "<= 3 dB",
            fmt=lambda d: f"{d:.2f} dB")
    S.check("keys F3..F6: tuning re the spec", "", {s: v and float(np.abs(v[1][ins]).max()) for s, v in vals.items()},
            lambda d: d <= 3, "|cents| <= 3 (host S6: 1)", fmt=lambda d: f"{d:.2f} c")
    S.check("keys outside: tuning", "", {s: v and float(np.abs(v[1][out_]).max()) for s, v in vals.items()},
            lambda d: d <= 10, "|cents| <= 10 (a 0.5 s window on the lowest keys)", fmt=lambda d: f"{d:.1f} c")
    S.check("keys: level span", "", {s: v and float(v[0].max() - v[0].min()) for s, v in vals.items()}, lambda d: True,
            "information", fmt=lambda d: f"{d:.1f} dB", info=True)


# ---------------------------------------------------------------- every shared effect page
@vtest
def vibes_fx(V, S, stim=None, extra=()):
    """each effect page's every control at min / default / max (or the values that show it) on VIBES
    stim: stimuli in place of the ST_* ones (CH LONG NOTE; ATK: COMP ATTACK's), BW the spectra's window, FDN the
    frame of frame_dist, CS_PERIOD CHORUS SYNC by the period, SPREAD FILTER OFF re a 2nd dry take; extra: settings
    added to every take. The MARIMBA suite (marimba_fx) passes a sustained roll and MUTE 0; VIBES runs with neither
    (its takes and checks unchanged)"""
    P = vp()
    fps = FS / 240
    st = stim or {}
    ST_CH, ST_LONG, ST_NOTE = (st.get(k, globals()["ST_" + k]) for k in ("CH", "LONG", "NOTE"))
    ST_ATK = st.get("ATK", ST_CH)                                      # COMP ATTACK's strike
    FDN = st.get("FDN", 2048)                                          # frame_dist's frame (PHASER FDBK)
    BW = st.get("BW", (0.05, 1.05))                                    # the spectra's window (B, SD)
    cs_period = st.get("CS_PERIOD", False)                             # CHORUS SYNC by its period (lfo_rate)
    cs_band = st.get("CS_BAND", False)     # CHORUS SPEED 50 / 127 as SPEED 0: the band re DEPTH 0 (lfo_rate pearson)
    PH0 = st.get("PH0", ST_PH0)            # PHASER / CHORUS SPEED 0's takes (and DEPTH 0 refs): ST_LONG is too short
    jfloor = st.get("JFLOOR", False)       # the DRIVE rows: the jack judged against its own distortion floor
    v3line = st.get("V3LINE", False)       # DRIVE TONE's V3 as a line (m_line_db), clear of the resampler's image
    dl_wow = st.get("DL_WOW", False)       # DELAY DIG vs ANA judged by the tape's wow / flutter (the spectrum: info)

    def run(label, settings, stim, motor=False):
        V.reset(list(settings) + list(extra), motor=motor, label=label)
        return V.take(label.replace(" ", "-").replace("/", ""), *stim)

    def L(t, q, a=0.0, b=1.8, k=0):
        return lu(cut(t.m(q), t.on(q)[k], a, b))

    def B(t, q, a=BW[0], b=BW[1]):
        return third(cut(t.m(q), t.on(q)[0], a, b))

    def SD(t1, t2, q, a=BW[0], b=BW[1]):
        """the spectral distance of two takes (sdist), the same window from each onset"""
        return sdist(B(t1, q, a, b), B(t2, q, a, b))

    def FD(t1, t2, q, a=0.2, b=5.3):
        """frame_dist of two takes, the same window from each onset"""
        return frame_dist(cut(t1.m(q), t1.on(q)[0], a, b), cut(t2.m(q), t2.on(q)[0], a, b), FDN)

    def band_re(t, r, q, lo, hi, a=0.05, b=1.05):
        """a band's level (dB) of take t re the reference take r, the same window from each onset; and whether t's
        band is within 6 dB of its floor (then it reads the floor: an upper bound)"""
        x, y = cut(t.m(q), t.on(q)[0], a, b), cut(r.m(q), r.on(q)[0], a, b)
        n = min(len(x), len(y))
        X, fr = welch(x[:n])
        Y, _ = welch(y[:n])
        m_ = (fr >= lo) & (fr < hi)
        F, _ = welch(t.pre(q)) if np.any(t.pre(q)) else (None, None)
        lim = F is not None and X[m_].sum() < 4 * F[m_].sum()
        return float(10 * np.log10(X[m_].sum() / (Y[m_].sum() + 1e-30) + 1e-30)), bool(lim)

    def nm(n):
        return f"{n} (CC {P[n]['cc']})"
    dry = {k: run(f"fx-dry-{k}", [], st) for k, st in (("ch", ST_CH), ("long", ST_LONG))}

    # COMPRESSOR
    cb = [("CMP_SW", 1), ("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 0), ("CMP_ATK", 40), ("CMP_REL", 50)]
    T = {x: run(f"comp-thr-{x}", with_(cb, ("CMP_AMT", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"COMP THRESH {nm('CMP_AMT')} 0 -> 127", "4:1, MKUP 0, chord v108", S.per(lambda q: L(T[127], q) - L(T[0], q)),
            lambda v: v <= -3, "<= -3 LU", level=True)
    S.check("COMP THRESH 0 / 64 / 127 in order", "", S.per(lambda q: [L(T[x], q) for x in (0, 64, 127)]),
            lambda v: v[0] > v[1] > v[2], "louder > ... > quieter", fmt=fmt_s)
    T = {x: run(f"comp-ratio-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", x)), ST_CH) for x in (0, 3, 7)}
    S.check(f"COMP RATIO {nm('CMP_RATIO')} 1.5:1 -> 20:1", "THRESH 100 (-31 dB)", S.per(lambda q: L(T[7], q) - L(T[0], q)),
            lambda v: v <= -2, "<= -2 LU", level=True)
    S.check("COMP RATIO 1.5:1 / 4:1 / 20:1 in order", "", S.per(lambda q: [L(T[x], q) for x in (0, 3, 7)]),
            lambda v: v[0] > v[1] > v[2], "in order", fmt=fmt_s)

    def crest(t, q):
        return crest_db(t.m(q), t.on(q)[0])

    def strike(t, q):
        return strike_db(t.m(q), t.on(q)[0])
    T = {x: run(f"comp-atk-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", 7), ("CMP_ATK", x)), ST_ATK) for x in (0, 40, 127)}
    S.check(f"COMP ATTACK {nm('CMP_ATK')} 0 -> 127", "20:1, THRESH 100: the strike's level (0..20 ms re 0.1..0.4 s)",
            S.per(lambda q: strike(T[127], q) - strike(T[0], q)), lambda v: v >= 2, "slow attack: the strike >= +2 dB",
            fmt=lambda v: f"{v:+.1f} dB")
    S.check("COMP ATTACK strike 0 / 40 / 127", "", S.per(lambda q: [strike(T[x], q) for x in (0, 40, 127)]),
            lambda v: True, "information", fmt=fmt_s, info=True)
    S.check("COMP ATTACK crest 0 / 40 / 127", "the peak in 0..30 ms (the old measure)",
            S.per(lambda q: [crest(T[x], q) for x in (0, 40, 127)]), lambda v: True, "information", fmt=fmt_s, info=True)

    def soft(t, q):
        return rdb(cut(t.m(q), t.on(q)[-1], 0.02, 0.3))
    T = {x: run(f"comp-rel-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", 7), ("CMP_REL", x)), ST_REL) for x in (0, 50, 127)}
    S.check(f"COMP RELEASE {nm('CMP_REL')} 0 -> 127", "a soft A4 0.3 s after a loud chord",
            S.per(lambda q: soft(T[0], q) - soft(T[127], q)), lambda v: v >= 2, "slow release holds it down >= 2 dB",
            level=True)
    T = {x: run(f"comp-mkup-{x}", with_(cb, ("CMP_GAIN", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"COMP MAKEUP {nm('CMP_GAIN')} 0 -> 127", "THRESH 64, 4:1", S.per(lambda q: L(T[127], q) - L(T[0], q)),
            lambda v: v >= 6, ">= +6 LU (the limiter caps +24 dB)", level=True)
    S.check("COMP MAKEUP 0 -> 64", "", S.per(lambda q: L(T[64], q) - L(T[0], q)), lambda v: v >= 2, ">= +2 LU", level=True)

    # FILTER
    fb = [("FL_TYPE", 1), ("FL_FREQ", 64), ("FL_RESO", 0), ("FL_LFO", 0), ("LFO_SHAPE", 0), ("LFO_RATE", P["LFO_RATE"]["de"]),
          ("LFO_SYNC", 0), ("LFO_RETRIG", 0)]
    names = ("OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH")
    T = {x: run(f"filter-type-{names[x]}", with_(fb, ("FL_TYPE", x)), ST_CH) for x in range(7)}
    if not st.get("SPREAD"):
        S.check(f"FILTER TYPE {nm('FL_TYPE')} OFF passes", "FREQ 64", S.per(lambda q: SD(T[0], dry['ch'], q)),
                lambda v: v <= 0.5, "<= 0.5 dB re no filter", fmt=lambda v: f"{v:.2f} dB")
    else:   # (a roll: two takes of it are 0.2-1.2 dB apart, the strikes' phase on the ring: judged against that)
        d2 = run("fx-dry-ch-again", [], ST_CH)
        S.check(f"FILTER TYPE {nm('FL_TYPE')} OFF passes", "FREQ 64; two dry takes' spread",
                S.per(lambda q: (SD(T[0], dry['ch'], q), SD(d2, dry['ch'], q))), lambda v: v[0] <= max(0.5, 1.5 * v[1]),
                "<= 0.5 dB re no filter, or <= 1.5x the spread of two takes of the roll",
                fmt=lambda v: f"{v[0]:.2f} dB (spread {v[1]:.2f})")
    for x in range(1, 7):
        lim = 2.0 if names[x] == "NOTCH" else 3.0
        S.check(f"FILTER TYPE {names[x]}", "FREQ 64, RESO 0, vs no filter",
                S.per(lambda q: SD(T[x], dry['ch'], q)), lambda v: v >= lim, f">= {lim:.0f} dB",
                fmt=lambda v: f"{v:.1f} dB")
    for a, b in ((1, 2), (3, 4)):
        S.check(f"FILTER {names[a]} vs {names[b]}", "the slope", S.per(lambda q: SD(T[a], T[b], q)),
                lambda v: v >= 1, ">= 1 dB", fmt=lambda v: f"{v:.1f} dB")
    T = {x: run(f"filter-freq-{x}", with_(fb, ("FL_FREQ", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"FILTER FREQ {nm('FL_FREQ')} 0 vs 127", "LP12", S.per(lambda q: SD(T[0], T[127], q)),
            lambda v: v >= 3, ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
    # round 1 took the centroid: with the low-pass shut (FREQ 0) the take is 28 dB down and its flat floor (USB's
    # rounding, the jack's hiss) made it the brightest (2583 / 343 / 425 Hz). The band over the cutoff instead, re
    # the chord without the filter (tests/py/test_every_control.py FILTER-FREQ: 2 kHz -60 dB from 127 to 10)
    fq = S.per(lambda q: [band_re(T[x], dry["ch"], q, 1000, 4000) for x in (0, 64, 127)])
    S.check("FILTER FREQ 0 / 64 / 127: 1-4 kHz re no filter", "LP12", {s_: v and [c for c, _ in v] for s_, v in fq.items()},
            lambda v: v[0] <= v[1] - 3 and v[1] <= v[2] - 3, "rising, >= 3 dB a step (0: under the floor reads the floor)",
            fmt=lambda v: " / ".join(f"{c:+.1f}" for c in v) + " dB", floor={s_: bool(v and v[0][1]) for s_, v in fq.items()})
    T = {x: run(f"filter-reso-{x}", with_(fb, ("FL_RESO", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"FILTER RESO {nm('FL_RESO')} 0 vs 127", "LP12, FREQ 64", S.per(lambda q: SD(T[0], T[127], q)),
            lambda v: v >= 2, ">= 2 dB (the peak at the cutoff)", fmt=lambda v: f"{v:.1f} dB")
    S.check("FILTER RESO 0 vs 64", "", S.per(lambda q: SD(T[0], T[64], q)), lambda v: True, "information",
            fmt=lambda v: f"{v:.1f} dB", info=True)

    def mot(t, q, a=0.2, b=5.3):
        return motion_f(cut(t.m(q), t.on(q)[0], a, b), t.pre(q))
    T = {x: run(f"filter-lfo-{x}", with_(fb, ("FL_LFO", x)), ST_LONG) for x in (0, 64, 127)}
    S.check(f"FILTER LFO depth {nm('FL_LFO')} 0 -> 127", "LP12 FREQ 64, LFO SINE 1.4 Hz",
            S.per(lambda q: mot(T[127], q) - mot(T[0], q)), lambda v: v >= 3, "spectral motion >= +3 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("FILTER LFO 0 -> 64", "", S.per(lambda q: mot(T[64], q) - mot(T[0], q)), lambda v: v >= 1, ">= +1 dB",
            fmt=lambda v: f"{v:+.1f} dB")
    lref = T[0]                                       # LP12 FREQ 64, the LFO at 0: the sweeps' reference
    lb = with_(fb, ("FL_LFO", 127))

    def sweep(t, q):
        """the filter's sweep: the 250-2000 Hz band (dB) re the LFO-0 take. Round 1 followed the centroid, which
        saturates as the cutoff passes the chord's partials (a SINE sweep read H3/H1 0.95) and on the jack fell into
        the hiss whenever the filter closed (SYNC 1/4 0.53 Hz, RATE 0 0.63 Hz)"""
        d, _, fps_ = rel_trace(t, lref, q, 0.2, 5.3, 250, 2000)
        return detrend2(d), fps_

    def ctr(t, q, a=0.2, b=5.3):
        return cen_trace(cut(t.m(q), t.on(q)[0], a, b))[0]
    e = lfo_hz(P["LFO_RATE"]["de"])
    T = {x: run(f"filter-shape-{x}", with_(lb, ("LFO_SHAPE", x)), ST_LONG) for x in (0, 4, 5)}

    def shp(t, q):
        """the sweep's steepest slope re a sine of its p-p and rate (SINE ~6 with the band's bends, SQR its steps:
        20+), and the spread of its cycle means at the card's rate (S&H holds a new level every cycle)"""
        d, f_ = sweep(t, q)
        sm = np.convolve(d, np.ones(5) / 5, "same")
        slope = float(np.percentile(np.abs(np.diff(sm)) * f_, 99) / (p2p(d) * e + 1e-9))
        tt = np.arange(len(d)) / f_
        cyc = [d[(tt >= a0) & (tt < a0 + 1 / e)].mean() for a0 in np.arange(0, tt[-1] - 1 / e, 1 / e)]
        return slope, float(np.std(cyc))
    S.check(f"LFO SHAPE {nm('LFO_SHAPE')} SINE vs SQR", "the sweep (250-2000 Hz re LFO 0): steepest slope re a sine's",
            S.per(lambda q: (shp(T[0], q)[0], shp(T[4], q)[0])), lambda v: v[1] >= 2 * v[0],
            "SQR >= 2x SINE (its steps)", fmt=lambda v: f"{v[0]:.1f} -> {v[1]:.1f}")
    S.check("LFO SHAPE SINE vs S&H", "the sweep's cycle means at the card's rate: spread (dB)",
            S.per(lambda q: (shp(T[0], q)[1], shp(T[5], q)[1])), lambda v: v[1] >= 2 * v[0],
            "S&H >= 2x SINE's (a square's cycle means move with the cycles' cut: not compared)",
            fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f} dB")
    for x in (0, P["LFO_RATE"]["de"], 127):
        t = run(f"filter-rate-{x}", with_(lb, ("LFO_RATE", x)), ST_LONG)
        ex = lfo_hz(x)
        tol = 0.15 if x == 0 else 0.10
        S.check(f"LFO RATE {nm('LFO_RATE')} {x}", f"expected {ex:.2f} Hz (the sweep, 250-2000 Hz re LFO 0)",
                S.per(lambda q: (rate_of(sweep(t, q)[0], fps, ex), p2p(sweep(t, q)[0]))),
                lambda v: abs(v[0] / ex - 1) <= tol and v[1] >= 6, f"within {tol:.0%}, the sweep >= 6 dB p-p",
                fmt=lambda v: f"{v[0]:.2f} Hz ({v[1]:.0f} dB p-p)")
    T = {k: run(f"filter-sync-{k.replace('/', '')}", with_(lb, ("LFO_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    S.check(f"LFO SYNC {nm('LFO_SYNC')} 1/8 vs 1/4", "the sweep, 250-2000 Hz re LFO 0",
            S.per(lambda q: (rate_of(sweep(T['1/8'], q)[0], fps, 2, 0.2, 5), rate_of(sweep(T['1/4'], q)[0], fps, 2, 0.2, 5))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")
    rt = [0.1, 1.47, 2.93, 4.31]
    st_rt = (5.7, sum((chord((69,), 108, s, s + 1.2) for s in rt), []))
    T = {x: run(f"filter-retrig-{x}", with_(lb, ("LFO_RETRIG", x)), st_rt) for x in (0, 1)}

    def same_phase(t, q):
        o = t.on(q)
        tr = [cen_trace(cut(t.m(q), i, 0.05, 0.75))[0] for i in o]
        n = min(len(x) for x in tr)
        tr = [x[:n] - np.polyval(np.polyfit(np.arange(n), x[:n], 1), np.arange(n)) for x in tr]
        return float(np.mean([np.corrcoef(tr[a], tr[b])[0, 1] for a in range(len(tr)) for b in range(a + 1, len(tr))]))
    S.check(f"LFO TRIG {nm('LFO_RETRIG')} KEY", "4 strikes off the LFO's period: the sweep restarts",
            S.per(lambda q: (same_phase(T[1], q), same_phase(T[0], q))), lambda v: v[0] >= 0.8,
            "KEY: mean correlation of the sweeps >= 0.8 (FREE: information)", fmt=lambda v: f"KEY {v[0]:.2f} / FREE {v[1]:.2f}")

    # DRIVE
    db_ = [("DRV_SW", 1), ("DRIVE", 40), ("DRV_TONE", 64), ("DRV_MIX", 127)]

    def hd(t, q):
        return hd_db(cut(t.m(q), t.on(q)[0], 0.1, 1.1), f1(69))
    def hdrow(check, setting, hi, lo, lim, crit):
        """a distortion row: hd(hi) - hd(lo) >= lim. JFLOOR (MARIMBA): where the jack's drive-free take (lo) holds
        3 dB more of H2+H3+H5+H6 than USB's, the jack reads its own analogue distortion there (scan 3, DRIVE 0:
        H2 / H3 -48 / -49 dB re H1 on the jack, -54 / -57 on USB), its rise is a lower bound: '(floor)', not judged
        (USB judges)"""
        fl = {}
        if jfloor and V.want_usb and "jack" in V.sources():
            u, j = _safe(hd, lo, "usb"), _safe(hd, lo, "jack")
            fl = {"jack": bool(u is not None and j is not None and j >= u + 3)}
        if not fl:
            S.check(check, setting, S.per(lambda q: hd(hi, q) - hd(lo, q)), lambda v: v >= lim, crit,
                    fmt=lambda v: f"{v:+.1f} dB")
            return
        S.check(check, setting, S.per(lambda q: (hd(hi, q) - hd(lo, q), fl.get(q, False))),
                lambda v: v[0] >= lim or v[1], crit + " (the jack: unless its own distortion floors it; USB judges)",
                fmt=lambda v: f"{v[0]:+.1f} dB", floor=fl)
    T = {x: run(f"drive-{x}", with_(db_, ("DRIVE", x)), ST_NOTE) for x in (0, 40, 127)}
    hdrow(f"DRIVE {nm('DRIVE')} 0 -> 127", "A4 v108: H2+H3+H5+H6 re H1", T[127], T[0], 10, ">= +10 dB")
    hdrow("DRIVE 0 -> 40 (the panel's start)", "", T[40], T[0], 3, ">= +3 dB")
    S.check("DRIVE 0 -> 127 loudness", "", S.per(lambda q: L(T[127], q, 0, 1.4) - L(T[0], q, 0, 1.4)), lambda v: abs(v) <= 3,
            "within +-3 LU (fx_all: +-2 on the Rhodes)", level=True)
    T = {x: run(f"drive-tone-{x}", with_(db_, ("DRIVE", 64), ("DRV_TONE", x)), ST_NOTE) for x in (0, 64, 127)}
    # round 1: a spectral distance / centroid of the whole note (3.1 dB / x1.29 USB, 2.5 / x1.09 jack: the bands
    # over the hiss, the centroid on the fundamental). TONE is a shelf at 1.8 kHz (fx.c DR_TA: highs x0 .. x2): the
    # A4 bar's V2 (1.77 kHz) and V3 (4.41 kHz) re V1 move by it
    sp69 = bspec()[69]

    def dtone(t, q):
        v2, v3 = pk_rel(cut(t.m(q), t.on(q)[0], 0.005, 0.3), sp69, ("V2", "V3"))
        if v3line:                         # V3LINE (MARIMBA): V3 +-12 Hz over the roll, the resampler's image left out
            v3 = m_line_db(cut(t.m(q), t.on(q)[0], 0.005, 1.3), sp69, "V3")
        return [v2, v3]
    dts = "DRIVE 64: A4's V2 / V3 re V1 (5..300 ms" + ("; V3 +-12 Hz, 5..1300 ms)" if v3line else ")")
    if not (jfloor and "jack" in V.sources()):
        S.check(f"DRIVE TONE {nm('DRV_TONE')} 0 -> 127", dts,
                S.per(lambda q: [b_ - a_ for a_, b_ in zip(dtone(T[0], q), dtone(T[127], q))]),
                lambda v: v[0] >= 2 and v[1] >= 6, "V2 >= +2 dB and V3 >= +6 dB (the shelf: -3 / -9 dB at 0, +6 at 127)",
                fmt=lambda v: f"{v[0]:+.1f} / {v[1]:+.1f} dB")
    else:
        # JFLOOR (MARIMBA): the jack's own distortion of V1 puts a line at 10 x V1 (4.41 kHz, 23 Hz off A4's V3)
        # ~70 dB under V1 in every take, DRIVE 0 too (scan 3: -70.2 / -70.4 dB at DRIVE 0 / TONE 0; USB under -89),
        # where MARIMBA's TONE-0 V3 is -79 dB on USB: the jack's V3 band read its own line (-66 dB at every TONE,
        # +0.2 dB 0 -> 127). Its V3 is judged only where TONE 0's V3 band stands 10 dB over that line (m_line_floored)
        fl = {"jack": bool(_safe(m_line_floored, cut(T[0].m("jack"), T[0].on("jack")[0], 0.005, 1.5), sp69, "V3"))}
        S.check(f"DRIVE TONE {nm('DRV_TONE')} 0 -> 127", dts,
                S.per(lambda q: [b_ - a_ for a_, b_ in zip(dtone(T[0], q), dtone(T[127], q))] + [fl.get(q, False)]),
                lambda v: v[0] >= 2 and (v[1] >= 6 or v[2]),
                "V2 >= +2 dB and V3 >= +6 dB (the shelf: -3 / -9 dB at 0, +6 at 127; the jack's V3: unless TONE 0's "
                "band stands within 10 dB of the jack's own 10 x V1 line, USB judges)",
                fmt=lambda v: f"{v[0]:+.1f} / {v[1]:+.1f} dB", floor=fl)
    T = {x: run(f"drive-mix-{x}", with_(db_, ("DRIVE", 64), ("DRV_MIX", x)), ST_NOTE) for x in (0, 64, 127)}
    hdrow(f"DRIVE MIX {nm('DRV_MIX')} 0 -> 127", "DRIVE 64", T[127], T[0], 6, ">= +6 dB distortion")
    T = {x: run(f"drive-sw-{x}", with_(db_, ("DRIVE", 64), ("DRV_SW", x)), ST_NOTE) for x in (0, 1)}
    hdrow(f"DRIVE ON {nm('DRV_SW')} off -> on", "DRIVE 64", T[1], T[0], 6, ">= +6 dB distortion")

    # TREMOLO / WAH
    tb = [("TW_SW", 1), ("TW_DEPTH", 64), ("TW_MODE", 1), ("TW_RATE", P["TW_RATE"]["de"]), ("TW_SYNC", 0), ("TW_SHAPE", 0)]
    er = lfo_hz(P["TW_RATE"]["de"])

    def amq(t, q, rate, sig=None):
        m = sig if sig is not None else t.m(q)
        return pfit(env_db(cut(m, t.on(q)[0], 0.3, 1.9)), fps, rate)
    T = {x: run(f"trem-depth-{x}", with_(tb, ("TW_DEPTH", x)), ST_CH) for x in (0, 64, 127)}
    tref = T[0]                                       # the tremolo at DEPTH 0: SHAPE's reference
    S.check(f"TREM DEPTH {nm('TW_DEPTH')} 127", f"AM, {er:.1f} Hz", S.per(lambda q: amq(T[127], q, er)["pp"]),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM DEPTH 64", "", S.per(lambda q: amq(T[64], q, er)["pp"]), lambda v: v >= 2, ">= 2 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM DEPTH 0", "", S.per(lambda q: amq(T[0], q, er)["pp"]), lambda v: v < 1, "< 1 dB", fmt=lambda v: f"{v:.2f} dB")
    T = {x: run(f"trem-mode-{x}", with_(tb, ("TW_DEPTH", 127), ("TW_MODE", x)), ST_CH) for x in (0, 1)}

    def pan(t):
        st, i = t.usb, t.on("usb")[0]
        d = env_db(cut(st[:, 0], i, 0.3, 1.9)) - env_db(cut(st[:, 1], i, 0.3, 1.9))
        return pfit(d, fps, er)["pp"]
    S.check(f"TREM MODE {nm('TW_MODE')} PAN: L-R swing", "USB stereo, DEPTH 127", S.usb(lambda q: pan(T[0])),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE AM: L-R swing", "USB stereo", S.usb(lambda q: pan(T[1])), lambda v: v < 2, "< 2 dB",
            fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE AM: the sum's AM", "USB L+R", S.usb(lambda q: amq(T[1], q, er, T[1].usb.sum(1))["pp"]),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE PAN: one channel's AM", "USB L / the jack", S.per(lambda q: amq(T[0], q, er)["pp"]), lambda v: True,
            "information", fmt=lambda v: f"{v:.1f} dB", info=True)
    tb127 = with_(tb, ("TW_DEPTH", 127))
    for x in (0, P["TW_RATE"]["de"], 127):
        t = run(f"trem-rate-{x}", with_(tb127, ("TW_RATE", x)), ST_LONG)
        ex = lfo_hz(x)
        tol = 0.15 if x == 0 else 0.10
        S.check(f"TREM RATE {nm('TW_RATE')} {x}", f"AM, expected {ex:.2f} Hz",
                S.per(lambda q: rate_of(env_db(cut(t.m(q), t.on(q)[0], 0.3, 5.3)), fps, ex)),
                lambda v: abs(v / ex - 1) <= tol, f"within {tol:.0%}", fmt=lambda v: f"{v:.2f} Hz")
    T = {k: run(f"trem-sync-{k.replace('/', '')}", with_(tb127, ("TW_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}

    def trate(t, q):
        return rate_of(env_db(cut(t.m(q), t.on(q)[0], 0.3, 5.3)), fps, 2, 0.2, 5)
    S.check(f"TREM SYNC {nm('TW_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (trate(T['1/8'], q), trate(T['1/4'], q))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")
    T = {x: run(f"trem-shape-{x}", with_(tb127, ("TW_SHAPE", x)), ST_CH) for x in (0, 64)}   # (65..127: the amp law)

    def hh(t, q):
        """the AM's own wave, linear (the gain re DEPTH 0): a sine has no H2 / H3, a square H3 = H1 / 3. Round 1
        took the dB envelope, where a 40 dB sine AM is all cusps (0.80 -> 0.45: the sine read squarer)"""
        a = pfit(gain_trace(t, tref, q, 0.3, 1.9), fps, er)["amps"]
        return (a[1] + a[2]) / (a[0] + 1e-12)
    S.check(f"TREM SHAPE {nm('TW_SHAPE')} 0 vs 64", "AM, DEPTH 127: (H2+H3)/H1 of the gain re DEPTH 0",
            S.per(lambda q: (hh(T[0], q), hh(T[64], q))),
            lambda v: v[1] >= max(0.15, 1.5 * v[0]), ">= 0.15 and >= 1.5x", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    T = {x: run(f"wah-{x}", [("TW_SW", 2), ("TW_DEPTH", x)], ST_CH) for x in (0, 64, 127)}
    S.check(f"WAH (TREM/WAH {nm('TW_SW')} = WAH) DEPTH 127", "vs no effect", S.per(lambda q: sdist(B(T[127], q), B(dry['ch'], q))),
            lambda v: v >= 3, ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
    # a touch wah: DEPTH is how far the strike opens it (host TREM-WAH). Round 1 compared the spectral distances to
    # dry, which the wah's static filter dominates at any DEPTH (14.1 / 14.2 dB at 127 / 64)
    wo = S.per(lambda q: [band_re(T[x], dry["ch"], q, 1000, 4000, 0.02, 0.3)[0] for x in (0, 64, 127)])
    S.check("WAH DEPTH 0 / 64 / 127: the strike opens it", "1-4 kHz re no effect, 20..300 ms", wo,
            lambda v: v[0] < v[1] < v[2] and v[2] - v[0] >= 6, "in order, 127 >= 6 dB over 0",
            fmt=lambda v: " / ".join(f"{c:+.1f}" for c in v) + " dB")
    S.check("WAH DEPTH 64", "vs no effect", S.per(lambda q: sdist(B(T[64], q), B(dry['ch'], q))), lambda v: v >= 1.5,
            ">= 1.5 dB", fmt=lambda v: f"{v:.1f} dB")

    # PHASER
    pb = [("PH_SW", 1), ("PH_DEPTH", 80), ("PH_SPEED", P["PH_SPEED"]["de"]), ("PH_FDBK", P["PH_FDBK"]["de"]), ("PH_SYNC", 0)]
    T = {x: run(f"phaser-depth-{x}", with_(pb, ("PH_DEPTH", x)), ST_LONG) for x in (0, 80, 127)}
    p80 = T[80]                                       # = phaser-fdbk at FDBK's default: the take-to-take spread
    S.check(f"PHASER DEPTH {nm('PH_DEPTH')} 0 -> 127", "spectral motion", S.per(lambda q: mot(T[127], q) - mot(T[0], q)),
            lambda v: v >= 2, ">= +2 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("PHASER DEPTH 0 -> 80 (the panel's start)", "", S.per(lambda q: mot(T[80], q) - mot(T[0], q)), lambda v: v >= 1,
            ">= +1 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("PHASER DEPTH 80 loudness re no effect", "information (vibes_jump judges it)",
            S.per(lambda q: L(T[80], q, 0, 5.0) - L(dry["long"], q, 0, 5.0)), lambda v: True, "information", level=True, info=True)
    pb127 = with_(pb, ("PH_DEPTH", 127))
    pref = T[0]                                       # DEPTH 0: the notches stand still

    def phrate(t, q, ex, lo=0.4, hi=2.6, ref=None, b=5.3, pearson=False, octave=False):
        """the LFO's rate from the 300-3000 Hz band re DEPTH 0 (round 2's new phaser: the centroid read 0.50 Hz for
        SPEED 0's 0.25 and 2.06 for SYNC 1/4's 0.69), searched in ex * lo .. ex * hi. The band's level repeats once
        an LFO cycle (the sweep up and the sweep down are mirror images, not repeats), with strong harmonics. It
        assumed two dips a cycle (rate_of at 2x, halved) until 2026-10-18, which on the jack picked the 3rd harmonic
        for SYNC 1/4 at the FM-1's 69 BPM (1.72 Hz for 1.15: a firmware 'fault' that was the measure; the host shows
        it at 69 BPM, tests/py/test_phaser_measures.py). lfo_rate() takes the period instead"""
        d, _, f_ = rel_trace(t, pref if ref is None else ref, q, 0.2, b, 300, 3000)
        return lfo_rate(d, f_, ex * lo, ex * hi, pearson=pearson, octave=octave)
    psp, ph0_at = {}, None
    pd0 = P["PH_SPEED"]["de"]

    def slower():
        S.check(f"PHASER SPEED 0 slower than {pd0}", f"expected {lfo_hz(0, True):.2f} Hz",
                {s: (psp[0][s], psp[pd0][s]) if psp[0].get(s) is not None and psp[pd0].get(s) is not None else None
                 for s in psp[0]},
                lambda v: v[0] <= 0.7 * v[1], "<= 0.7x (a period longer than the take)",
                fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz")
    for x in (0, pd0, 127):
        ex = lfo_hz(x, half=True)
        if x == 0 and not (V.offline and not os.path.exists(os.path.join(V.offline, "phaser-speed-0-long.json"))):
            # (--offline on takes saved before PH0: the 6 s take as before)
            # PH0: SPEED 0's 4 s period in ST_LONG's 5.1 s trace is 1.3 periods (its lag at the search's very end):
            # VIBES read 0.33 Hz on the FM-1 and the host; MARIMBA's roll dips 3 times a cycle (at 0, 2 and 3 s), so
            # half the period correlates too: 0.50 Hz on USB, the jack and the host. A take of 2.4 periods against a
            # DEPTH 0 reference as long, and each lag's correlation over its own overlap (lfo_rate pearson: 0.63 at
            # 2 s, 0.99 at 4 s on the MARIMBA host; by the whole trace's energy 0.50 / 0.58, and 2 s passed): 0.25 Hz.
            # The two takes run last in the suite, their rows put back here: every other take is the same events
            # after the same takes (the host's seeded jitter and its CC history too), so every other row is unchanged
            ph0_at = len(S.rows)
            continue
        t = run(f"phaser-speed-{x}", with_(pb127, ("PH_SPEED", x)), ST_LONG)
        psp[x] = S.per(lambda q: phrate(t, q, ex))
        if x:
            S.check(f"PHASER SPEED {nm('PH_SPEED')} {x}", f"expected {ex:.2f} Hz (300-3000 Hz re DEPTH 0, its period)",
                    psp[x], lambda v: abs(v / ex - 1) <= 0.10, "within 10 %", fmt=lambda v: f"{v:.2f} Hz")
    if ph0_at is None:
        slower()
    sl_at = len(S.rows)                               # (where the SPEED 0 row's "slower than" goes when PH0 runs last)
    T = {x: run(f"phaser-fdbk-{x}", with_(pb, ("PH_FDBK", x)), ST_LONG) for x in (0, P["PH_FDBK"]["de"], 127)}
    # the feedback fills the notches as it raises the resonance between them (fx.c: x + y with the allpasses' output
    # fed back, as Felucca's; host response at DEPTH 80: the peak +2.9 -> +9.3 dB, the notches -62 .. -79 -> -11 ..
    # -17 dB), so the motion stays (vibes: -0.5 dB) and the whole window's spectrum averages the moving peak away
    # (the FM-1's jack: 1.8 dB). Frame by frame, the sweeps in step, the timbre is 3.9 dB apart (2026-10-18)
    S.check(f"PHASER FDBK {nm('PH_FDBK')} 0 vs 127", "DEPTH 80; frame by frame",
            S.per(lambda q: (mot(T[127], q) - mot(T[0], q), FD(T[127], T[0], q), FD(p80, T[P["PH_FDBK"]["de"]], q))),
            lambda v: v[0] >= 1.5 or apart_of_spread(v[1], v[2]),
            "motion >= +1.5 dB or, frame by frame, sqrt(d^2 - spread^2) >= 2 dB and >= the spread (two takes of one "
            "setting)", fmt=lambda v: f"{v[0]:+.1f} / {v[1]:.1f} dB (spread {v[2]:.1f})")
    T = {k: run(f"phaser-sync-{k.replace('/', '')}", with_(pb127, ("PH_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    S.check(f"PHASER SYNC {nm('PH_SYNC')} 1/8 vs 1/4", "300-3000 Hz re DEPTH 0",
            S.per(lambda q: (phrate(T['1/8'], q, 2, 0.125, 5), phrate(T['1/4'], q, 2, 0.125, 5))),   # 0.25-10 Hz: any BPM
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")

    # CHORUS
    chb = [("CP_SW", 1), ("CP_DEPTH", 64), ("CP_SPEED", P["CP_SPEED"]["de"]), ("CP_FDBK", P["CP_FDBK"]["de"]), ("CP_SYNC", 0)]
    T = {x: run(f"chorus-depth-{x}", with_(chb, ("CP_DEPTH", x)), ST_LONG) for x in (0, 64, 127)}
    cref = T[0]                                       # (CS_BAND: the SPEED rows' DEPTH 0 reference)
    c64 = T[64]                                       # = chorus-fdbk at FDBK's default: the take-to-take spread
    S.check(f"CHORUS DEPTH {nm('CP_DEPTH')} 0 -> 127", "spectral motion", S.per(lambda q: mot(T[127], q) - mot(T[0], q)),
            lambda v: v >= 1, ">= +1 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("CHORUS DEPTH 0 -> 127: width", "USB stereo L/R correlation",
            S.usb(lambda q: (corr_lr(cut(T[0].usb, T[0].on(q)[0], 0.1, 5.0)), corr_lr(cut(T[127].usb, T[127].on(q)[0], 0.1, 5.0)))),
            lambda v: v[0] - v[1] >= 0.1, "drops >= 0.1", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    cb127 = with_(chb, ("CP_DEPTH", 127))
    sp, cs0_at = {}, None
    d0 = P["CP_SPEED"]["de"]

    def cslower():
        S.check(f"CHORUS SPEED 0 slower than {d0}", f"expected {lfo_hz(0, True):.2f} Hz",
                {s: (sp[0][s], sp[d0][s]) if sp[0].get(s) is not None and sp[d0].get(s) is not None else None
                 for s in sp[0]},
                lambda v: v[0] <= 0.7 * v[1], "<= 0.7x", fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz")
    for x in (0, d0, 127):
        if x == 0 and not (V.offline and not os.path.exists(os.path.join(V.offline, "chorus-speed-0-long.json"))):
            # (--offline on takes saved before CS0: the 6 s take as before)
            # CS0, as PH0: SPEED 0's 4 s period in ST_LONG's 5.1 s centroid trace is 1.3 periods, and the harmonic
            # sum read VIBES 0.36 / 0.42 Hz on the FM-1 (0.36 on the host), MARIMBA's host 0.39 or 0.25 by the jitter
            # alone. PH0's 10.5 s take against a CHORUS DEPTH 0 take of the same strikes, read as the phaser's SPEED
            # 0 (phrate: 300-3000 Hz re DEPTH 0, lfo_rate pearson): host seeds 1-10 0.249-0.250 Hz on both engines.
            # (The centroid without the reference read VIBES' 3 strikes, 3.3 s apart: 0.30 Hz; the centroids'
            # difference 0.28 on VIBES seed 4, the strikes' own centroid steps differing with the chorus on.) Its
            # two takes run last (after PH0's), its rows put back here
            cs0_at = len(S.rows)
            continue
        t = run(f"chorus-speed-{x}", with_(cb127, ("CP_SPEED", x)), ST_LONG)
        ex = lfo_hz(x, half=True)
        if cs_band:
            # CS_BAND (MARIMBA): the roll's centroid by the harmonic sum (rate_of) read SPEED 50 1.31 Hz for 0.87 on
            # host seeds 4 and 13 (1.5x: the jitter alone). The 300-3000 Hz band re the CHORUS DEPTH 0 take of the
            # same roll, by its period (lfo_rate pearson), as SPEED 0 and the phaser: no take added
            # (octave: 0.9.6's chorus, its wet high-passed at ~220 Hz, moves the band less at the LFO's own rate than
            # at twice it; seed 4's SPEED 127 then correlated better at two periods than at one, 0.62 / 0.50, and read
            # 3.00 Hz for 6.00: the 3 Hz fit's odd harmonics held 2 % of its even ones, i.e. a 6 Hz wave)
            sp[x] = S.per(lambda q: phrate(t, q, ex, ref=cref, pearson=True, octave=True))
        else:
            sp[x] = S.per(lambda q: rate_of(ctr(t, q), fps, ex))
        if x:
            S.check(f"CHORUS SPEED {nm('CP_SPEED')} {x}",
                    f"expected {ex:.2f} Hz " + ("(300-3000 Hz re DEPTH 0, its period)" if cs_band else "(centroid)"), sp[x],
                    lambda v: abs(v / ex - 1) <= 0.10, "within 10 %", fmt=lambda v: f"{v:.2f} Hz")
    if cs0_at is None:
        cslower()
    csl_at = len(S.rows)                              # (where the SPEED 0 row's "slower than" goes when CS0 runs last)
    T = {x: run(f"chorus-fdbk-{x}", with_(chb, ("CP_FDBK", x)), ST_LONG) for x in (0, P["CP_FDBK"]["de"], 127)}
    S.check(f"CHORUS FDBK {nm('CP_FDBK')} 0 vs 127 (reserved: no card, no effect; MANUAL CC 24)", "DEPTH 64",
            S.per(lambda q: (mot(T[127], q) - mot(T[0], q), SD(T[127], T[0], q, 0.2, 5.3),
                              SD(c64, T[P["CP_FDBK"]["de"]], q, 0.2, 5.3))),
            lambda v: v[0] >= 1 or v[1] >= max(2, 2 * v[2]),
            "motion >= +1 dB or >= 2 dB apart (and 2x the spread of two takes of one setting)",
            fmt=lambda v: f"{v[0]:+.1f} / {v[1]:.1f} dB (spread {v[2]:.1f})", info=True)
    T = {k: run(f"chorus-sync-{k.replace('/', '')}", with_(cb127, ("CP_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    def csr(t, q):
        """CS_PERIOD: the period (lfo_rate, 0.25-10 Hz: any BPM), as the phaser's: on the MARIMBA roll the harmonic
        sum (rate_of) took the 1/4's 3rd harmonic (6.00 Hz for 2.00 at the host's 120 BPM)"""
        return lfo_rate(ctr(t, q), fps, 0.25, 10) if cs_period else rate_of(ctr(t, q), fps, 2, 0.2, 5)
    S.check(f"CHORUS SYNC {nm('CP_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (csr(T['1/8'], q), csr(T['1/4'], q))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")

    # DELAY (an A4 staccato)
    dlb = [("DL_SW", 1), ("DL_DEPTH", 50), ("DL_TIME", P["DL_TIME"]["de"]), ("DL_FDBK", P["DL_FDBK"]["de"]),
           ("DL_TONE", P["DL_TONE"]["de"]), ("DL_SYNC", 0)]

    def xs(t, q, a=0.0, b=3.2):
        return cut(t.m(q), t.on(q)[0], a, b)
    T0 = dly_ms(P["DL_TIME"]["de"])
    T = {x: run(f"delay-depth-{x}", with_(dlb, ("DL_DEPTH", x)), ST_STAC) for x in (0, 50, 127)}
    dref = T[0]                                       # DEPTH 0: the direct sound alone, the echoes' reference

    def gap(t, q, ex=None):
        return echo_ms(xs(t, q), xs(dref, q), ex, floor_db(t, q))
    S.check(f"DELAY DEPTH {nm('DL_DEPTH')} 0 -> 50", f"the 1st echo ({T0:.0f} ms) re the direct",
            S.per(lambda q: (echo_levels(xs(T[0], q), T0, 1)[0], echo_levels(xs(T[50], q), T0, 1)[0])),
            lambda v: v[1] - v[0] >= 6, "50 stands >= 6 dB over 0 (0: the note's own residue)",
            fmt=lambda v: f"{v[0]:.1f} -> {v[1]:.1f} dB")
    S.check("DELAY DEPTH 50 -> 127", "the 1st echo", S.per(lambda q: echo_levels(xs(T[127], q), T0, 1)[0] - echo_levels(xs(T[50], q), T0, 1)[0]),
            lambda v: v >= 3, ">= +3 dB", level=True)
    dl100 = with_(dlb, ("DL_DEPTH", 100))
    dtk = {}
    for x in (0, P["DL_TIME"]["de"], 127):
        t = dtk[x] = run(f"delay-time-{x}", with_(dl100, ("DL_TIME", x)), ST_STAC)
        ex = dly_ms(x)
        tol = max(8.0, 0.10 * ex) if x == 0 else max(5.0, 0.05 * ex)
        S.check(f"DELAY TIME {nm('DL_TIME')} {x}", f"expected {ex:.0f} ms (the 1st echo re DEPTH 0)",
                S.per(lambda q: gap(t, q, ex)), lambda v: abs(v - ex) <= tol, f"within {tol:.0f} ms", fmt=lambda v: f"{v:.0f} ms")
    T1 = dly_ms(64)
    T = {x: run(f"delay-fdbk-{x}", with_(dl100, ("DL_TIME", 64), ("DL_FDBK", x)), ST_STAC) for x in (0, P["DL_FDBK"]["de"], 127)}
    S.check(f"DELAY FDBK {nm('DL_FDBK')} 0 vs 127", f"TIME 64 ({T1:.0f} ms): echoes above -40 dB",
            S.per(lambda q: (n_echoes(echo_levels(xs(T[0], q), T1, 15)), n_echoes(echo_levels(xs(T[127], q), T1, 15)))),
            lambda v: v[1] >= v[0] + 3, "127: >= 3 more", fmt=lambda v: f"{v[0]} -> {v[1]}")
    T = {x: run(f"delay-tone-{x}", with_(dl100, ("DL_TIME", 64), ("DL_TONE", x)), ST_STAC) for x in (0, P["DL_TONE"]["de"], 127)}
    # round 1: the centroid of 1.5..5.5 echo times (x1.06 / x0.89), mostly the direct sound's tail and the floor.
    # TONE darkens each pass (a one-pole in the loop): A4's V2 re V1 in the 2nd and 3rd echoes, re the direct's
    def etone(t, q):
        i, m_ = t.on(q)[0], t.m(q)
        d0 = pk_rel(cut(m_, i, 0, 0.12), sp69, ("V2",))[0]
        return [pk_rel(cut(m_, i, k * T1 / 1000, k * T1 / 1000 + 0.12), sp69, ("V2",))[0] - d0 for k in (2, 3)]
    S.check(f"DELAY TONE {nm('DL_TONE')} 0 -> 127", "echoes 2 and 3: A4's V2 re V1, re the direct's",
            S.per(lambda q: (etone(T[0], q), etone(T[127], q))), lambda v: np.mean(v[1]) - np.mean(v[0]) >= 6,
            "127 >= 6 dB brighter", fmt=lambda v: f"{v[0][0]:.1f} / {v[0][1]:.1f} -> {v[1][0]:.1f} / {v[1][1]:.1f} dB")
    T = {x: run(f"delay-type-{x}", with_(dl100, ("DL_SW", x)), ST_STAC) for x in (1, 2)}

    def eb(t, q):
        return third(xs(t, q, T0 / 1000 - 0.02, 3 * T0 / 1000))
    S.check(f"DELAY {nm('DL_SW')} DIG vs ANA", "the echoes' spectrum",
            S.per(lambda q: (sdist(eb(T[1], q), eb(T[2], q)), cen(xs(T[2], q, T0 / 1000 - 0.02, 3 * T0 / 1000)) /
                             cen(xs(T[1], q, T0 / 1000 - 0.02, 3 * T0 / 1000)))),
            lambda v: v[0] >= 2 or abs(np.log(v[1])) >= np.log(1.15),
            ">= 2 dB or centroid x1.15 either way" + (" (information: the tape's darker loop has ~nothing to act "
                                                       "on, see the wow / flutter row)" if dl_wow else ""),
            fmt=lambda v: f"{v[0]:.1f} dB / x{v[1]:.2f}", info=dl_wow)
    if dl_wow:
        # DL_WOW (MARIMBA): A4's marimba note is V1 with V2 37 dB and V3 80 dB under it, and gone (-26 dB) by the 1st
        # echo, so the echoes are V1 alone. ANA's loop (fx.c: the TONE one-pole x0.6) darkens a pass by 0.07 dB at
        # V1, 1.0 at V2, 3.2 at V3: the spectrum and centroid can not move (scan 3: 1.6 dB / x1.00 USB, 1.5 / x1.00
        # jack, host 1.2), what a listener hears is the tape's wow and flutter: the echoes waver in pitch (+-8 cents
        # at 0.7 Hz, +-13 at 7 Hz). Measured as the echoes' timing against DIG's: scan 3 0.63 ms USB and jack, host
        # 0.60; two DIG takes (DELAY TIME's default take, the same settings) 0.03 ms USB, 0.002 jack, 0.000 host
        dg = dtk[P["DL_TIME"]["de"]]
        S.check(f"DELAY {nm('DL_SW')} DIG vs ANA: the tape's wow / flutter",
                f"A4's V1 in echoes 1-3 ({T0:.0f} ms): ANA's timing re DIG's, p2p (a line off); DIG re a 2nd DIG take",
                S.per(lambda q: (m_echo_wobble_ms(xs(T[2], q), xs(T[1], q), sp69["V1"], T0),
                                 m_echo_wobble_ms(xs(dg, q), xs(T[1], q), sp69["V1"], T0))),
                lambda v: v[0] >= 0.15 and v[0] >= 4 * v[1], ">= 0.15 ms and >= 4x DIG's",
                fmt=lambda v: f"{v[0]:.2f} / {v[1]:.3f} ms")
    T = {k: run(f"delay-sync-{k.replace('/', '')}", with_(dl100, ("DL_SYNC", sync_idx(k))), ST_STAC) for k in ("1/4", "1/8")}
    S.check(f"DELAY SYNC {nm('DL_SYNC')} 1/4 vs 1/8", "the 1st echo re DEPTH 0", S.per(lambda q: (gap(T['1/4'], q), gap(T['1/8'], q))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} ms ({60000 / v[0]:.0f} BPM)")

    # REVERB (the staccato: its tail after the key-up at 0.15 s)
    rb = [("RV_DEPTH", 64), ("RV_DECAY", P["RV_DECAY"]["de"]), ("RV_TONE", P["RV_TONE"]["de"]), ("RV_PREDLY", 0)]

    def tail(t, q):
        return max(rdb(xs(t, q, 0.45, 0.95)), -120) - rdb(xs(t, q, 0, 0.15))
    T = {x: run(f"reverb-{x}", with_(rb, ("RV_DEPTH", x)), ST_STAC) for x in (0, 64, 127)}
    S.check(f"REVERB {nm('RV_DEPTH')} 0 -> 127", "the tail 0.3..0.8 s after the key-up re the direct",
            S.per(lambda q: tail(T[127], q) - tail(T[0], q)), lambda v: v >= 10, ">= +10 dB", level=True,
            floor={q: rdb(xs(T[0], q, 0.45, 0.95)) < floor_db(T[0], q) + 6 for q in V.sources()})
    S.check("REVERB 0 / 64 / 127 in order", "", S.per(lambda q: [tail(T[x], q) for x in (0, 64, 127)]),
            lambda v: v[1] >= v[0] + 3 and v[1] <= v[2] + 0.5, "64: >= 3 dB over 0, not over 127", fmt=fmt_s)
    r100 = with_(rb, ("RV_DEPTH", 100))
    T = {x: run(f"reverb-decay-{x}", with_(r100, ("RV_DECAY", x)), ST_STAC) for x in (0, P["RV_DECAY"]["de"], 127)}
    S.check(f"REVERB DECAY {nm('RV_DECAY')} 0 / {P['RV_DECAY']['de']} / 127", "the tail's T60",
            S.per(lambda q: [tail_t60(xs(T[x], q, 0.35, 3.2)) for x in sorted(T)]),
            lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 2, "in order, 127 / 0 >= 2x", fmt=lambda v: fmt_s(v) + " s")
    T = {x: run(f"reverb-tone-{x}", with_(r100, ("RV_TONE", x)), ST_STAC) for x in (0, P["RV_TONE"]["de"], 127)}
    # round 1: the tail's centroid (3353 / 1097 / 1400 Hz): the dark tail at 0 is quiet and its flat floor won.
    # TONE damps the hall's highs: A4's V2 (1.77 kHz) re V1 in the tail, re the direct sound's own
    def rtilt(t, q):
        d = pk_rel(xs(t, q, 0.0, 0.15), sp69, ("V2",))[0]
        return pk_rel(xs(t, q, 0.35, 1.15), sp69, ("V2",))[0] - d

    def rfloor(t, q):
        """the tail's V2 within 12 dB of the floor's (the pre-roll, per sample; the floor while the FM-1 plays stands
        over its idle hiss): it reads the floor, an upper bound (round 2, jack: TONE 0 -11.9 dB, USB -19.4)"""
        fl, tl = t.pre(q), xs(t, q, 0.35, 1.15)
        if len(fl) < 4800 or not np.any(fl):
            return False
        return pk_db(fl, sp69["V2"]) - 10 * np.log10(len(fl)) > pk_db(tl, sp69["V2"]) - 10 * np.log10(len(tl)) - 12
    rf = {q: _safe(rfloor, T[0], q) for q in V.sources()}
    S.check(f"REVERB TONE {nm('RV_TONE')} 0 / {P['RV_TONE']['de']} / 127", "the tail's V2 re V1, re the direct's",
            S.per(lambda q: [rtilt(T[x], q) for x in sorted(T)] + [bool(rf.get(q)) or q == "jack"]),
            lambda v: (v[0] < v[1] or v[3]) and v[1] < v[2] and v[2] - v[0] >= 6,
            "rising, 127 >= 6 dB over 0 (the jack: 0's dark tail is near its floor, 0 vs 64 not judged)",
            fmt=lambda v: " / ".join(f"{c:+.1f}" for c in v[:3]) + " dB", floor=rf)
    T = {x: run(f"reverb-pdly-{x}", with_(r100, ("RV_PREDLY", x)), ST_STAC) for x in (0, 64, 127)}
    def side_lag(t, t0, q):
        """the hall's side signal (L - R; the dry VIBES is mono) of t against PDLY 0's: the lag (ms) of best
        correlation of their dB envelopes (floor at -60 dB), 0..120 ms. Round 1 took a 10 % threshold crossing, which
        the hall's own build-up moved (23 ms for 40)"""
        def env(tk):
            i = tk.on(q)[0]
            e_ = edb1(((tk.usb[:, 0] - tk.usb[:, 1]) / 2)[i:i + int(0.6 * FS)])
            return np.maximum(e_, e_.max() - 60)
        a_, b_ = env(t), env(t0)
        c_ = [np.corrcoef(a_[k:k + 400], b_[:400])[0, 1] for k in range(0, 121)]
        return float(np.argmax(c_))
    for x in (64, 127):
        ex = pdly_ms(x)
        S.check(f"REVERB PRE-DELAY {nm('RV_PREDLY')} {x}", f"the side signal re PDLY 0's (envelope lag), expected {ex:.0f} ms",
                S.usb(lambda q: side_lag(T[x], T[0], q)), lambda v: abs(v - ex) <= 15, "within 15 ms (USB stereo)",
                fmt=lambda v: f"{v:.0f} ms")

    # LEVEL (CC 110): the preset's trim, A4 v40
    T = {x: run(f"level-{x}", [("LEVEL", x)], ST_QUIET) for x in (0, 64, 127)}
    for x in (0, 127):
        ex = lvl_db(x)
        S.check(f"LEVEL {nm('LEVEL')} {x} re 64", f"A4 v40, expected {ex:+.1f} dB",
                S.per(lambda q: L(T[x], q, 0, 1.4) - L(T[64], q, 0, 1.4)), lambda v: abs(v - ex) <= 1.0, "within 1 dB",
                level=True)

    # PHASER SPEED 0 (PH0, see the PHASER section): its long takes last, its rows where they belong
    if ph0_at is not None:
        n0, ex = len(S.rows), lfo_hz(0, half=True)
        t = run("phaser-speed-0-long", with_(pb127, ("PH_SPEED", 0)), PH0)
        r0 = run("phaser-depth-0-long", with_(pb, ("PH_DEPTH", 0)), PH0)
        psp[0] = S.per(lambda q: phrate(t, q, ex, ref=r0, b=PH0[0] - 0.7, pearson=True))
        S.check(f"PHASER SPEED {nm('PH_SPEED')} 0", f"expected {ex:.2f} Hz (300-3000 Hz re DEPTH 0, its period; "
                f"a {PH0[0] - 0.9:.1f} s trace)", psp[0], lambda v: abs(v / ex - 1) <= 0.10, "within 10 %",
                fmt=lambda v: f"{v:.2f} Hz")
        slower()
        r_sp, r_sl = S.rows[n0:]
        del S.rows[n0:]
    # CHORUS SPEED 0 (CS0, see the CHORUS section): the same way, after PH0's takes (they keep their place)
    if cs0_at is not None:
        n0, ex = len(S.rows), lfo_hz(0, half=True)
        t = run("chorus-speed-0-long", with_(cb127, ("CP_SPEED", 0)), PH0)
        r0 = run("chorus-depth-0-long", with_(chb, ("CP_DEPTH", 0)), PH0)
        sp[0] = S.per(lambda q: phrate(t, q, ex, ref=r0, b=PH0[0] - 0.7, pearson=True))
        S.check(f"CHORUS SPEED {nm('CP_SPEED')} 0", f"expected {ex:.2f} Hz (300-3000 Hz re DEPTH 0, its period; "
                f"a {PH0[0] - 0.9:.1f} s trace)", sp[0], lambda v: abs(v / ex - 1) <= 0.10, "within 10 %",
                fmt=lambda v: f"{v:.2f} Hz")
        cslower()
        r_cs, r_csl = S.rows[n0:]
        del S.rows[n0:]
        S.rows.insert(csl_at, r_csl)                  # (the later places first: the earlier ones are still right)
        S.rows.insert(cs0_at, r_cs)
    if ph0_at is not None:
        S.rows.insert(sl_at, r_sl)
        S.rows.insert(ph0_at, r_sp)


# ---------------------------------------------------------------- loudness jumps
JUMPS = [  # (name, settings, the switch (parameter, on value)): each at the panel's starting settings (ui.c fx_wake)
    ("drive (DRV ON, DRIVE 40)", [("DRIVE", 40)], ("DRV_SW", 1)),
    ("comp (-20 dB 4:1, MKUP 15)", [("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 15)], ("CMP_SW", 1)),
    ("filter LP12 (FREQ 127)", [], ("FL_TYPE", 1)),
    ("tremolo PAN 64", [("TW_DEPTH", 64)], ("TW_SW", 1)),
    ("wah 64", [("TW_DEPTH", 64)], ("TW_SW", 2)),
    ("phaser 80", [("PH_DEPTH", 80)], ("PH_SW", 1)),
    ("chorus 64", [("CP_DEPTH", 64)], ("CP_SW", 1)),
    ("delay DIG 50", [("DL_DEPTH", 50)], ("DL_SW", 1)),
    ("delay ANA 50", [("DL_DEPTH", 50)], ("DL_SW", 2)),
    ("reverb 50 (no switch: REV 0 -> 50)", [], ("RV_DEPTH", 50)),
    ("motor (DEPTH 110, 3.5 Hz)", [], ("MOT_ON", 1)),
]
ALL_ON = [("DRIVE", 30), ("CMP_SW", 1), ("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 23), ("TW_SW", 1), ("TW_DEPTH", 50),
          ("CP_SW", 1), ("CP_DEPTH", 50), ("PH_SW", 1), ("PH_DEPTH", 60), ("DL_SW", 1), ("DL_DEPTH", 40), ("RV_DEPTH", 40)]
# (CC 85 first: it switches the phaser off; fx_all's "all on" sends it after CC 12, so its phaser is not on)


@vtest
def vibes_jump(V, S):
    """every effect switched on at its default settings: the loudness step (LUFS) on vs off, held on vs never on, and
    switched while a chord sounds (fx_toggle); clicks at the switch; the FX bypass"""
    return _jump(V, S, JUMPS)


def _jump(V, S, jumps, ch=None, extra=()):
    """ch: the stimulus (default the FXCH chord v108 held 3.3 s), extra: settings added to every reset (marimba_jump:
    a sustained roll, MUTE 0)"""
    P = vp()
    ch = ch or chord(FXCH, 108, 0.1, 3.4)
    st = (3.6, ch)
    refs = []
    V.reset(list(extra), label="jump ref")
    refs.append(V.take("jump-ref-a", *st))

    def lq(t, q, a, b, stereo=False):
        x = t.usb if stereo else t.m(q)
        i = t.on(q)[0]
        return lu(x[max(0, i + int(a * FS)):i + int(b * FS)])
    rows = []
    for name, base, (sw, on) in jumps:
        mot = sw == "MOT_ON"
        V.reset(base + ([] if mot else [(sw, on)]) + list(extra), motor=mot, label=f"jump {name} held on")
        held = V.take(f"jump-{sw}-{on}-held", *st)
        V.reset(base + [(sw, 0)] + list(extra), label=f"jump {name} switched")   # (DRV ON defaults to on)
        c, v = P[sw]["cc"], ccv(sw, on)
        tog = V.take(f"jump-{sw}-{on}-switched", 3.6, ch + [(1.2, "cc", c, v), (2.4, "cc", c, 0)])
        rows.append((name, held, tog))
    V.reset(list(extra), label="jump ref (end)")
    refs.append(V.take("jump-ref-b", *st))
    lfo = {"TW_SW": lfo_hz(P["TW_RATE"]["de"]), "PH_SW": lfo_hz(P["PH_SPEED"]["de"], True),
           "CP_SW": lfo_hz(P["CP_SPEED"]["de"], True), "MOT_ON": mot_hz(P["MOT_RATE"]["de"])}
    for name, held, tog in rows:
        sw = [j for n_, b_, j in jumps if n_ == name][0]
        rate = lfo.get(sw[0]) if not (sw[0] == "TW_SW" and sw[1] == 2) else None     # (the wah: no LFO)
        # a modulating effect is judged over whole cycles of its LFO (round 1: 0.4 s of a 3.5 Hz motor or a
        # 0.68 Hz phaser sweep read the LFO's phase, -6.2 / +7.1 LU); the held window: the most cycles in 0..3.2 s
        hw = (int(3.2 * rate) / rate if rate and int(3.2 * rate) >= 1 else 1.8)
        sww = None if not rate else next((k / rate for k in range(1, 9) if 0.38 <= k / rate <= 0.6), None)
        comp, wide, ph = sw[0] == "CMP_SW", sw[0] == "CP_SW", sw[0] == "PH_SW"
        # the targets: the chorus' loudness is set 2.5 LU under dry when it is wide (L/R correlation < 0.8: fx_all,
        # 0.8's width compensation; the host 2026-10-17: -2.2 LU at DEPTH 64, correlation 0.51; the phaser has none,
        # FXALL_EXPECT's note); a compressor's level follows the playing (fx_all: 99, fx_toggle: 4 LU; host: -2.5 LU
        # held, +2.3 / -2.8 switched); the phaser held on: fx_all's 2 LU for the same effect. 0.70 x (dry +
        # allpasses) cuts where its notches sit: VIBES at DEPTH 80 -1.45 LU stereo, -1.53 .. -1.55 on the left / the
        # jack in every FM-1 report since 2026-10-18 (build/hil, build/marimba/p5, build/release-0.9.4, build/final;
        # the host the same), MARIMBA -1.2, the Rhodes (fx_all phaser 80) -1.1; it failed the 1.5 LU flag by 0.05
        cor = corr_lr(cut(held.usb, held.on("usb")[0], 0.2, 1.8)) if held.usb is not None else 1.0
        tgt = -2.5 if wide and cor < 0.8 else 0.0
        lim = 3.0 if comp else 2.0 if ph else JUMP_LU
        tlim = 4.0 if comp else JUMP_LU

        def static(q, stereo=False):
            return lq(held, q, 0, hw, stereo) - np.mean([lq(r, q, 0, hw, stereo) for r in refs])

        def direct(q):
            return lq(held, q, 0, 0.25) - np.mean([lq(r, q, 0, 0.25) for r in refs])
        win = f"0..{hw:.2f} s" + (f" ({int(round(hw * rate))} cycles of {rate:.2f} Hz)" if rate else "")
        crit = f"|step{f' - ({tgt:+.1f})' if tgt else ''}| <= {lim} LU" + (" (a compressor)" if comp else
                                                                             " (a phaser's notches: fx_all)" if ph else
                                                                             " (wide: 0.8's width target)" if tgt else "")
        S.check(f"{name}: on vs off", f"held on, {win}, USB stereo (as heard)", S.usb(lambda q: static(q, True)),
                lambda d: abs(d - tgt) <= lim, crit, fmt=lambda d: f"{d:+.2f} LU")
        S.check(f"{name}: on vs off, USB L vs jack", win, S.per(static), lambda d: abs(d - tgt) <= lim, crit, level=True)
        S.check(f"{name}: the direct sound", "0..0.25 s (echoes / the hall not yet in)", S.per(direct), lambda d: True,
                "information", level=True, info=True)
        # the level re dry along the hold: an effect that changes the decay (round 1: the motor, +4.4 dB/s; the
        # compressor rises as the chord falls under its threshold, information)
        def slope(q):
            w_ = 1 / rate if rate else 0.3
            while w_ < 0.25:
                w_ *= 2
            r = refs[0]
            ks = np.arange(0.2, 3.2 - w_, w_ / 2)
            d = [lq(held, q, k, k + w_) - lq(r, q, k, k + w_) for k in ks]
            return float(np.polyfit(ks, d, 1)[0])
        timed = sw[0] in ("DL_SW", "RV_DEPTH")
        S.check(f"{name}: level re dry along the hold", "0.2..3.2 s, the slope", S.per(slope),
                lambda d: abs(d) <= 1.0, "|slope| <= 1 dB/s" + (" (information: a compressor / echoes / a hall)"
                                                                if comp or timed else ""),
                fmt=lambda d: f"{d:+.2f} dB/s", info=comp or timed)

        def steps(q):
            r = refs[0]
            w_ = sww or 0.4

            def sl(t, a, b):
                return lq(t, q, a - 0.1, b - 0.1)
            out = []
            for s_ in (1.2, 2.4):
                aa, bb = (s_ + 0.05, s_ + 0.05 + w_), (s_ - 0.05 - w_, s_ - 0.05)
                out.append((sl(tog, *aa) - sl(tog, *bb)) - (sl(r, *aa) - sl(r, *bb)))
            m = tog.m(q)
            g = 32768 if q == "usb" else 32768 * np.sqrt(np.mean(tog.usb[:, 0] ** 2) / (np.mean(m ** 2) + 1e-20)) \
                if tog.usb is not None else 32768 * 0.3 / (np.abs(m).max() + 1e-12)
            ck = sum(clicks(m[tog.at(q, s) - int(0.1 * FS):tog.at(q, s) + int(0.2 * FS)] * g) for s in (1.2, 2.4))
            return out[0], out[1], ck
        v = S.per(steps)
        cyc = rate and not sww
        S.check(f"{name}: switched under a chord", "the step at on / at off (fx_toggle)" +
                (f", windows of {int(round((sww or 0) * rate))} cycles" if sww else
                 f": its {1 / rate:.2f} s cycle does not fit the 0.4 s windows, they read its phase (information)" if cyc
                 else ""),
                {s: x and (x[0], x[1]) for s, x in v.items()},
                lambda d: abs(d[0] - tgt) <= tlim and abs(d[1] + tgt) <= tlim,
                (f"|step -+ ({tgt:+.1f})| <= {tlim} LU" if tgt else f"|step| <= {tlim} LU") + (" (fx_toggle: comp 4)" if comp else ""),
                fmt=lambda d: f"{d[0]:+.2f} / {d[1]:+.2f} LU", info=bool(cyc))
        S.check(f"{name}: clicks at the switches", "", {s: x and x[2] for s, x in v.items()}, lambda c: c == 0, "0",
                fmt=lambda c: str(c))
    S.check("reference drift", "the dry chord, start vs end", S.per(lambda q: lq(refs[1], q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: abs(d) <= 0.3, "within 0.3 LU (else the scan was disturbed)", level=True)
    V.reset(ALL_ON + list(extra), label="all on")
    a = V.take("jump-all-on", *st)
    V.reset(ALL_ON + list(extra), label="all on, FX bypass")
    V.send(111, 127)
    b = V.take("jump-bypass", *st)
    V.send(111, 0)
    S.check("FX bypass (CC 111) with everything on", "re no effects", S.per(lambda q: lq(b, q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: abs(d) <= 0.5, "within 0.5 LU (fx_all)", level=True)
    S.check("everything on re no effects", "fx_all 'all on'", S.per(lambda q: lq(a, q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: True, "information", level=True, info=True)

# ==================================================================================================================
# MARIMBA (TYPE 5, CC 79 = 127): the same rig and report as the VIBES suites (USB and the jack side by side, the jack
# decides), the thresholds of MARIMBA PLAN §10.2 / §10.3 (the D rows) as tests/py/test_marimba_cards_audible.py,
# test_marimba_controls_audible.py and tools/marimba_scan.py judge them on the host. Not in the default run:
# --marimba, or --only marimba_*; report build/hil/marimba-report.md (+ .json). Written for P5 (the coordinator's FM-1
# session); --dry-run and --host run them without a device. No MOTOR suite: MARIMBA has no motor (marimba_decay checks
# that CC 47-51 change nothing). MUTE (CC 103) is the hand at key-up: 0 rings out.
# ==================================================================================================================
MKEYS = (48, 60, 84)                      # C3 C4 C6 (PLAN §12 step 2)
MFAMS = (("SOFT", 0), ("MED", 42), ("HARD", 85), ("RUBBER", 127))
MVELS = (8, 24, 40, 64, 88, 100, 117, 127)
# MARIMBA's chord re ST77 (the user's level decision "A", 2026-10-20: marimba.json calib.st77_offset_db stays +2.5 dB,
# VIBES's; every MARIMBA strike is held under -0.5 dBFS by the top roll-off and the RING ceiling, so the A3-E4-A4 v100
# chord reads -1.5 LU re ST77: FM-1 scans 3 and 4, -1.50 USB / -1.48 jack)
MLEVEL_DB = -1.5
M_CAPPED = {(84, "SOFT", "MED")}
M_FX_RATE, M_FX_JIT, M_FX_SEED, M_FX_VEL = 8.0, 0.0, 1, 80   # marimba_fx's roll: per note, the jitter, the seed (m_fx_roll)          # both contacts at T7f's cap at v100 (marimba_va.capped, VA-M9's exceptions)


def m_known(check, setting):
    """the KNOWN reason of a MARIMBA row (tools/marimba_scan.py KNOWN / KNOWN_CARDS: the plan's thresholds a wooden bar
    cannot meet as written, user decisions; the host scan and the tests report the same items KNOWN / xfail), or None.
    A failing marimba_* row that matches is reported KNOWN with its reason, not FAIL; anything else fails as before."""
    import re
    import marimba_scan as ms
    rules = ((r"^C4 MED: v40 -> v117 brighter$", r"", ms.KNOWN_CARDS["VELOCITY centroid"]),
             (r"^pumping$", r"", ms.known("M5", " pumping")),
             (r"^two-hand balance$", r"", ms.known("M5", " balance")),
             (r"^STRIKE ", r"^key 84 ", ms.KNOWN_CARDS["STRIKE C6"]),
             (r"^WEIGHT light brighter$", r"^key 84 ", ms.KNOWN_CARDS["WEIGHT C6 centroid"]))
    for rc, rs, why in rules:
        if re.search(rc, check) and re.search(rs, setting):
            return why
    return None


def mt60(x, f):
    """the V1 T60 by a 30 dB fit (5..35 dB under the peak; PLAN §10.2 B1)"""
    return t60(x, f, 5.0, 35.0)


# The MARIMBA measures are Gate VA-M's (tools/marimba_va.py) and the host scan's (tools/marimba_scan.py feats, P4):
# the attack centroid over 0..50 ms, power-weighted, and every spectrum with the flat-start window (full weight from
# the strike, a raised-cosine fade over the last quarter). The VIBES measures (a Hann window over 0..0.3 / 0..0.5 s)
# weigh the middle of the segment, where a marimba note is its fundamental alone: on the FM-1 they read SOFT-MED
# 0.0 dB apart on C4 and the velocity 'darkening' the attack (x0.46): the window, not the sound.
M_ATT = 0.05


def m_flat(n):
    w = np.ones(n)
    q = n // 4
    w[n - q:] = 0.5 * (1 + np.cos(np.pi * np.arange(q) / q))
    return w


def m_cen(x):
    """marimba_va.centroid at this rig's rate: power-weighted, flat-start window"""
    x = np.asarray(x, float)
    S = np.abs(np.fft.rfft(x * m_flat(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return float(np.sum(fr * S) / (np.sum(S) + 1e-30))


def m_third(x):
    x = np.asarray(x, float)
    S = np.abs(np.fft.rfft(x * m_flat(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return np.array([10 * np.log10(S[(fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))].sum() + 1e-30)
                     for c in [50 * 2 ** (i / 3) for i in range(26)]])


def m_pk_db(x, f, rel=0.03):
    x = np.asarray(x, float)
    nf = 1 << max(15, int(np.ceil(np.log2(len(x)))) + 2)
    S = np.abs(np.fft.rfft(x * m_flat(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    return float(10 * np.log10(S[(fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)].sum() + 1e-30))


def m_strike_feats(m, i, n):
    """strike_feats with the marimba measures (above): LUFS 0..1 s, cen_a 0..50 ms, V2 / V3 re V1 5..300 ms, bands
    0..0.5 s"""
    sp = bspec().get(n, {})
    att = cut(m, i, 0.005, 0.3)
    l1 = m_pk_db(att, sp.get("V1", f1(n)))
    return dict(lufs=lu(cut(m, i, 0, 1.0)), cen_a=m_cen(cut(m, i, 0, M_ATT)),
                v2=m_pk_db(att, sp["V2"]) - l1 if "V2" in sp else float("nan"),
                v3=m_pk_db(att, sp["V3"]) - l1 if "V3" in sp else float("nan"), bands=m_third(cut(m, i, 0, 0.5)))


def m_excess_db(x, ref):
    """excess_db with the flat-start window: the contact noise (tau ~23 ms) sits at the window's start, where a Hann
    window over 0..60 ms is ~0 (it read C6's tick 37 dB under the note, the host's sample-exact difference -33)"""
    nn = min(len(x), len(ref))
    w = m_flat(nn)
    P = np.abs(np.fft.rfft(np.asarray(x[:nn], float) * w)) ** 2
    Q = np.abs(np.fft.rfft(np.asarray(ref[:nn], float) * w)) ** 2
    ex = np.maximum(P - 1.26 * Q, 0).sum() * 2 / (nn * (w * w).sum())
    return float(10 * np.log10(ex + 1e-24))


def m_ms_db(x):
    w = m_flat(len(x))
    return float(10 * np.log10((np.abs(np.fft.rfft(np.asarray(x, float) * w)) ** 2).sum() * 2 / (len(x) * (w * w).sum()) + 1e-24))


def frac_delay(x, d):
    """x delayed by d samples (fractional: a linear phase on its zero-padded spectrum)"""
    x = np.asarray(x, float)
    n = len(x)
    N = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, N)
    return np.fft.irfft(X * np.exp(-2j * np.pi * np.arange(len(X)) * d / N), N)[:n]


def m_contact_db(mx, ox, mr, orf, a=0.0, b=0.06, fit=(0.1, 0.3), lag_ms=6.0):
    """NOISE's contact as the gate measures it (tests/py/test_marimba_controls_audible.py, marimba_scan's P4 host
    numbers): the RMS of take x minus the NOISE 0 take r over a..b s from the strike, re r's own RMS there, both
    rectangular windows. The engine is deterministic apart from NOISE, so once x is aligned on r to a fraction of a
    sample the note cancels: the whole lag in samples where x - r is least over -5..300 ms (the strike: no period
    ambiguity, which a lone C6 partial has), then the fraction (+-1 sample, 0.01 steps) where it is least over fit
    (after the contact, the ring alone). The spectral excess before (m_excess_db: a flat-start window, the bins under
    the ring's partials dropped, 1.26 x the ring subtracted) read C6 1.2-1.6 dB low (-35.4 dB on the FM-1, USB and
    jack, and on the host, where the gate's sample-exact difference is -33.8; C4 -29.7 for -31.9). Returns (dB, lag)"""
    pad = int(0.02 * FS)
    x = np.asarray(cut(mx, ox, -0.02, 0.4), float)
    y = np.asarray(cut(mr, orf, -0.02, 0.4), float)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    L = int(lag_ms * FS / 1000)
    a0, a1 = max(L, pad - int(0.005 * FS)), min(n - L, pad + int(0.3 * FS))
    rr = [np.sum((x[a0 + k:a1 + k] - y[a0:a1]) ** 2) for k in range(-L, L + 1)]
    d0 = -(int(np.argmin(rr)) - L)
    f0, f1_ = pad + int(fit[0] * FS), min(n, pad + int(fit[1] * FS))
    ds = d0 + np.linspace(-1, 1, 201)
    d = ds[int(np.argmin([np.sum((frac_delay(x, s)[f0:f1_] - y[f0:f1_]) ** 2) for s in ds]))]
    xs = frac_delay(x, d)
    w0, w1 = pad + int(a * FS), pad + int(b * FS)
    e = np.mean((xs[w0:w1] - y[w0:w1]) ** 2)
    return float(10 * np.log10(e + 1e-30) - 10 * np.log10(np.mean(y[w0:w1] ** 2) + 1e-30)), float(d)


def m_line_floored(x, sp, band="V3", margin=10.0, hw=3.0):
    """whether bar partial `band` (pk_db's +-3 % band) in x stands within margin dB of the harmonic of V1 nearest it
    (k = round(band / V1), the line +-hw Hz around k x V1 as measured in x), i.e. a tenth or more of what the band
    reads is the source's own harmonic distortion. The FM-1's jack (not USB) has 10 x V1, 23 Hz off A4's V3, ~70 dB
    under V1 whatever the drive (DRIVE 0 too), and V3 itself 10-15 dB under that line at TONE 0: DRIVE TONE's jack V3
    band read the jack's own floor, +0.2 dB from TONE 0 to 127 (USB +6.8). Scan 3: the band 4.8-5.6 dB over the line
    on the jack, 17-22 dB on USB. x: a long stretch (the A4 roll's 1.5 s: lines 1.3 Hz wide, V1 to 0.1 Hz; 0.3 s
    took V1 2 Hz off and its 10th onto V3)"""
    x = np.asarray(x, float)
    nf = 1 << max(15, int(np.ceil(np.log2(len(x)))) + 2)           # (pk_db's: the two sums on one grid)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    m1 = np.flatnonzero((fr >= 0.992 * sp["V1"]) & (fr <= 1.008 * sp["V1"]))   # (not the tube's, 1 % over)
    f1m = fr[m1[int(np.argmax(S[m1]))]]                   # V1 as measured (the interface's clock is not the FM-1's)
    k = round(sp[band] / sp["V1"])
    line = 10 * np.log10(S[(fr >= k * f1m - hw) & (fr <= k * f1m + hw)].sum() + 1e-30)
    return bool(pk_db(x, sp[band]) < line + margin)


def m_line_db(x, sp, band="V3", hw=12.0):
    """bar partial `band` re V1 (dB) as lines: the power within +-hw Hz of each, V1 where x has it (the interface's
    clock is not the FM-1's) and `band` scaled by the same. MARIMBA's DRIVE TONE: the 44.1 -> 48 kHz resampling
    (resample_poly 160 / 147: to_fs on the FM-1's USB, HostRig on the host) leaves V1's image at 3900 + 440 = 4340 Hz,
    ~80 dB under V1 (a pure 440 Hz: -80.6 dB in pk_db's +-3 % band), whatever TONE does; MARIMBA A4's V3 at TONE 0
    stands ~98 dB under V1, so the +-3 % band (4253-4516 Hz) read the image: +6.1 .. +6.7 dB for TONE 0 -> 127 (the
    shelf: +12.1 at V3 re V1; VIBES' V3 reads it). +-12 Hz round V3 is 44 Hz clear of the image. x: a long stretch
    (the A4 roll's 1.3 s: lines 1 Hz wide)"""
    x = np.asarray(x, float)
    nf = 1 << max(15, int(np.ceil(np.log2(len(x)))) + 2)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    m1 = np.flatnonzero((fr >= 0.992 * sp["V1"]) & (fr <= 1.008 * sp["V1"]))
    f1m = fr[m1[int(np.argmax(S[m1]))]]

    def lv(f):
        return 10 * np.log10(S[(fr >= f - hw) & (fr <= f + hw)].sum() + 1e-30)
    return float(lv(sp[band] * f1m / sp["V1"]) - lv(f1m))


def m_echo_wobble_ms(x, y, f, t_ms, ks=(1, 2, 3), bw=40.0):
    """how far the echoes of take x move in time against take y's (the same note, the same delay settings): V1's
    phase in x re y (both +-bw Hz round f, analytic) as a delay (ms), in each echo k's window (k T + 30 ms ..
    (k + 1) T - 10 ms), a straight line taken off; the largest peak-to-peak over ks. A.DELAY's tape (fx.c dly_loop:
    wow ~0.7 Hz, flutter ~7 Hz, +-0.29 % / +-0.05 % of the time: +-8 / +-13 cents) moves its echoes, D.DELAY's stand
    still: two DIG takes 0.03 ms apart on the FM-1 (0.000 on the host), ANA re DIG 0.6 ms"""
    n = min(len(x), len(y))

    def an(z):
        Z = np.fft.fft(np.asarray(z[:n], float))
        fr = np.fft.fftfreq(n, 1 / FS)
        Z[(fr < f - bw) | (fr > f + bw)] = 0
        return np.fft.ifft(Z)
    d = np.unwrap(np.angle(an(x) * np.conj(an(y)))) / (2 * np.pi * f) * 1000
    out = []
    for k in ks:
        a, b = int((k * t_ms / 1000 + 0.03) * FS), int(((k + 1) * t_ms / 1000 - 0.01) * FS)
        s = d[a:min(b, n)]
        if len(s) < FS // 20:
            continue
        i = np.arange(len(s))
        out.append(float(np.ptp(s - np.polyval(np.polyfit(i, s, 1), i))))
    return max(out)


def m_drop_rate(x, t_off, db=30.0, win=0.005, hop=0.001, fit=False):
    """MARIMBA's hand mute as Gate VA-M9 measures it (marimba_va.mute_drop_s): the time from the key-up t_off (s) until
    the note (a 5 ms RMS envelope, 1 ms hop) stays db under its level just before the key-up, as a rate db / time
    (dB/s). The VIBES fit of the f0 band over 30..300 ms after the key-up runs into the floor on a marimba: a MUTE 127
    stop is 60 dB down in ~0.06 s, so the fit read MUTE 64 faster than 127 (the host: 585 / 476 dB/s)"""
    x = np.asarray(x, float)
    w, h = int(win * FS), int(hop * FS)
    idx = np.arange(0, max(len(x) - w, 1), h)
    e = np.array([10 * np.log10(np.mean(x[i:i + w] ** 2) + 1e-30) for i in idx])
    t = (idx + w / 2) / FS
    ref = e[(t >= t_off - 0.02) & (t <= t_off)].max()
    above = np.flatnonzero((t > t_off) & (e > ref - db))
    if not len(above):
        return float("nan")
    if fit:
        # the slope of the envelope (dB/s, least squares) from the key-up to its last frame db under the level before
        # it: the time to the crossing, read on the 1 ms grid from the key-up, moved C5's MUTE 0 x1.011 / x0.983 off
        # its held decay (the host, seeds 2 / 3; the jack) where the decays were the same
        k = np.arange(np.flatnonzero(t > t_off)[0], above[-1] + 1)
        if len(k) >= 5:
            return float(-np.polyfit(t[k], e[k], 1)[0])
    return float(db / max(t[above[-1]] - t_off, hop))


def m_audible_s(m, i, fl, t0=0.05, t1=0.6, over=20.0, lo=0.15):
    """the end (s after onset i) of a pitch window t0..t1 that stays over the source's floor (fl: its pre-roll): the
    first 10 ms frame from t0 under the floor + over dB, at least lo. USB has no floor (exact zero): t1. On the jack
    C6..C7 are under its hiss after ~0.3 s and the FFT peak of 0.05..0.6 s took C7 20 c flat (-46 c for C6# with
    align's late onset)"""
    if len(fl) < FS // 10 or not np.any(fl):
        return t1
    f = rdb(fl) + over
    w = FS // 100
    for k in range(int(t0 * FS), int(t1 * FS), w):
        if rdb(m[i + k:i + k + w]) < f:
            return max(lo, k / FS)
    return t1


def m_drop_db(takes, db=30.0, over=20.0, lo=10.0):
    """m_drop_rate's depth for takes compared with each other [(x, t_off, the source's pre-roll)]: db, or less where a
    take's level before its key-up stands less than db + over dB over its floor (the jack's hiss), at least lo. A
    30 dB drop of C5 ended 14 dB over the jack's 5 ms hiss: MUTE 0 vs held read x1.015 (USB x1.000; at 20 dB x1.000)"""
    d = db
    for x, t_off, fl in takes:
        if len(fl) < FS // 10 or not np.any(fl):
            continue
        i = int(t_off * FS)
        d = min(d, rdb(x[max(0, i - FS // 50):i]) - rdb(fl) - over)
    return max(lo, d)


def m_second_diff(vels, lv, step=16):
    """tools/marimba_scan.second_diff: the level curve's second difference on an even 16-velocity grid (the anchors
    8 24 40 64 88 100 117 127 are unevenly spaced: np.diff(lv, 2) read a curve straight in velocity as 1.8 / 2.7 dB)"""
    s = np.diff(lv) / np.diff(vels)
    return float(np.abs(np.diff(s)).max() * step)


@vtest
def marimba_voice(V, S):
    """MARIMBA VOICE: STRIKE, MALLET (the four families), TUBE (level and the low notes' length), WEIGHT; C3 C4 C6 v100"""
    ev, dur = strikes(MKEYS, (100,))
    V.reset([("RELEASE", 0)], label="MARIMBA VOICE all 64")
    T = {64: V.take("m-voice-default", dur, ev)}
    sets = [("TONE", 0), ("TONE", 127), ("BELL", 0), ("BELL", 127), ("HAMMER", 0), ("HAMMER", 127)] + \
           [("BARK", v) for _, v in MFAMS]
    for card, x in sets:
        V.reset([(card, x), ("RELEASE", 0)], label=f"MARIMBA {card} {x}")
        T[(card, x)] = V.take(f"m-voice-{card}-{x}", dur, ev)
    cache = {}

    def F(key, src, k):
        if (key, src, k) not in cache:
            t = T[key]
            cache[(key, src, k)] = m_strike_feats(t.m(src), t.on(src)[k], MKEYS[k])
        return cache[(key, src, k)]

    def key(c, x):
        return 64 if x == 64 else (c, x)
    P = vp()
    for k, n in enumerate(MKEYS):
        def d(c, a, b, src):
            return sdist(F(key(c, a), src, k)["bands"], F(key(c, b), src, k)["bands"])

        def lv(c, a, b, src):
            return F(key(c, b), src, k)["lufs"] - F(key(c, a), src, k)["lufs"]
        s = f"key {n} v100"
        S.check(f"STRIKE (CC {P['TONE']['cc']}) 0 vs 64", s, S.per(lambda q: d("TONE", 0, 64, q)), lambda v: v >= 3,
                ">= 3 dB spectral distance", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"STRIKE 64 vs 127", s, S.per(lambda q: d("TONE", 64, 127, q)), lambda v: v >= 3, ">= 3 dB",
                fmt=lambda v: f"{v:.1f} dB")

        def fam(q):
            ds = [(sdist(F(("BARK", va), q, k)["bands"], F(("BARK", vb), q, k)["bands"]), f"{na}-{nb}")
                  for i, (na, va) in enumerate(MFAMS) for nb, vb in MFAMS[i + 1:]
                  if not (n, na, nb) in M_CAPPED]
            return min(ds)
        S.check(f"MALLET (CC {P['BARK']['cc']}) families pairwise", s, S.per(fam), lambda v: v[0] >= 3,
                "every pair of SOFT MED HARD RUBBER >= 3 dB", fmt=lambda v: f"closest {v[1]} {v[0]:.1f} dB")
        S.check(f"TUBE (CC {P['BELL']['cc']}) level 0 -> 64", s, S.per(lambda q: lv("BELL", 0, 64, q)), lambda v: v >= 4.0,
                ">= +4 LU", level=True)
        S.check("TUBE level 64 -> 127", s, S.per(lambda q: lv("BELL", 64, 127, q)), lambda v: v >= 0.5, ">= +0.5 LU",
                level=True)
        S.check(f"WEIGHT (CC {P['HAMMER']['cc']}) level 0 -> 127", s, S.per(lambda q: lv("HAMMER", 0, 127, q)),
                lambda v: v >= 2.5, ">= +2.5 LU", level=True)
        S.check("WEIGHT light brighter", s,
                S.per(lambda q: F(("HAMMER", 0), q, k)["cen_a"] / F(("HAMMER", 127), q, k)["cen_a"]),
                lambda v: v >= 1.05, "attack centroid 0 / 127 >= 1.05", fmt=lambda v: f"x{v:.2f}")
        S.check("V2 / V3 re V1 at the defaults", s, S.per(lambda q: (F(64, q, k)["v2"], F(64, q, k)["v3"])),
                lambda v: True, "information", fmt=lambda v: f"{v[0]:.1f} / {v[1]:.1f} dB", info=True)
    # TUBE lengthens the low notes: C2 and C3 held 4 s, MUTE 0, TUBE 0 vs 64
    ev, dur = strikes((36, 48), (100,), gap=5.0, hold=4.5)
    R = {}
    for b in (0, 64):
        V.reset([("BELL", b), ("RELEASE", 0)], label=f"MARIMBA TUBE {b} ring")
        R[b] = V.take(f"m-tube-ring-{b}", dur, ev)
    for k, n in enumerate((36, 48)):
        S.check("TUBE 0 -> 64: the V1 T60", f"key {n} v100, held",
                S.per(lambda q: [mt60(cut(R[b].m(q), R[b].on(q)[k], 0, 4.4), f1(n)) for b in (0, 64)]),
                lambda v: v[1] >= 1.3 * v[0], "x1.3 or more (the tube lengthens the low notes)",
                fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f} s")


@vtest
def marimba_decay(V, S):
    """MARIMBA VOICE 2/2: RING, MUTE (the hand), the pedal, NOISE (no knock), TUNE; the MOTOR CCs inert"""
    P = vp()
    ev, dur = strikes((36, 60, 84), (100,), gap=8.0, hold=7.5)
    T = {}
    for x in (0, P["DECAY"]["de"], 127):
        V.reset([("DECAY", x), ("RELEASE", 0), ("NOISE", 0)], label=f"RING {x}")
        T[x] = V.take(f"m-ring-{x}", dur, ev)
    for k, n in enumerate((36, 60, 84)):
        S.check(f"RING (CC {P['DECAY']['cc']}) T60 at 0 / {P['DECAY']['de']} / 127", f"key {n} v100, held",
                S.per(lambda q: [mt60(cut(T[x].m(q), T[x].on(q)[k], 0, 7.4), f1(n)) for x in sorted(T)]),
                lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 2.0, "in order, 127 / 0 >= 2x",
                fmt=lambda v: fmt_s(v) + " s")
    # MUTE and the pedal: the decay 30..300 ms after the key-up (1.5 s), against the same note held
    # (the key-up while the short note still rings well over the jack's hiss: C3 at 0.4 s, C5 at 0.2 s; let go at
    # 1.5 s, as before P4, C3 was ~60 dB down, under the jack's floor, and C5 silent: 'no value')
    keys, holds = (48, 72), (0.4, 0.2)
    ev = [e for k, (n, h) in enumerate(zip(keys, holds)) for e in ((0.2 + 3.0 * k, "on", n, 100), (0.2 + 3.0 * k + h, "off", n, 0))]
    dur = 0.2 + 3.0 * (len(keys) - 1) + 2.8 + 0.8
    evh, _ = strikes(keys, (100,), gap=3.0, hold=2.8)
    T = {}
    for x in (0, 64, 127):
        V.reset([("RELEASE", x), ("NOISE", 0)], label=f"MUTE {x}")
        T[x] = V.take(f"m-mute-{x}", dur, ev)
    V.reset([("NOISE", 0)], label="held")
    T["held"] = V.take("m-held", dur, evh)
    for p_ in (0, 50, 127):
        V.reset([("RELEASE", 127), ("NOISE", 0)], label=f"pedal {p_} (MUTE 127)")
        T[("ped", p_)] = V.take(f"m-pedal-{p_}", dur, ev + [(0.05, "cc", 64, p_)])
    for k, n in enumerate(keys):
        dbs = {}

        def r(key, q, fit=False):
            if q not in dbs:                             # one depth for every take of this key and source
                dbs[q] = m_drop_db([(cut(T[x].m(q), T[x].on(q)[k], 0, 2.3), holds[k], T[x].pre(q)) for x in T])
            return m_drop_rate(cut(T[key].m(q), T[key].on(q)[k], 0, 2.3), holds[k], dbs[q], fit=fit)
        S.check(f"MUTE (CC {P['RELEASE']['cc']}) 0 = rings out", f"key {n}, after the key-up vs held",
                S.per(lambda q: r(0, q, True) / r("held", q, True)), lambda v: abs(v - 1) <= 0.01,
                "within 1 % of the held decay (the envelope's slope)",
                fmt=lambda v: f"x{v:.3f}")
        S.check("MUTE 0 / 64 / 127", f"key {n}, after the key-up", S.per(lambda q: [r(x, q) for x in (0, 64, 127)]),
                lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 3.0, "in order, 127 / 0 >= 3x",
                fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s")
        S.check("pedal down holds at MUTE 127", f"key {n}", S.per(lambda q: r(("ped", 127), q, True) / r("held", q, True)),
                lambda v: abs(v - 1) <= 0.01, "within 1 % of the held decay (the envelope's slope)", fmt=lambda v: f"x{v:.3f}")
        S.check("pedal up / half / down (MUTE 127)", f"key {n}",
                S.per(lambda q: [r(("ped", p_), q) for p_ in (0, 50, 127)]),
                lambda v: v[2] < v[1] < v[0] and v[0] / v[2] >= 3.0, "down < half < up, up / down >= 3x",
                fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s")
    # NOISE: the contact (0..60 ms) and, with no damper, nothing at the key-up
    ev, dur = strikes((48, 60, 84), (100,), gap=3.0, hold=1.5)
    T = {}
    de = P["NOISE"]["de"]
    for x in (0, de, 127):
        V.reset([("NOISE", x), ("RELEASE", 64)], label=f"NOISE {x}")
        T[x] = V.take(f"m-noise-{x}", dur, ev)
    for k, n in enumerate((48, 60, 84)):
        def nz(q):
            o = {x: T[x].on(q)[k] for x in T}
            m = {x: T[x].m(q) for x in T}
            ring = m_ms_db(cut(m[0], o[0], 0, 0.06))
            c = {x: m_excess_db(cut(m[x], o[x], 0, 0.06), cut(m[0], o[0], 0, 0.06)) - ring for x in (de, 127)}
            # the knock re the note at its strike (0..100 ms): just before the key-up (1.4..1.5 s, as VIBES) a short
            # marimba note is already gone (USB -240 dBFS, the jack its hiss), and the ratio read 0 / -2 dB
            nl = ms_db(cut(m[0], o[0], 0, 0.1))
            # the same excess of two hiss-only stretches (the takes' pre-rolls): what knock_db reads on a source with a
            # floor and nothing at the key-up (the jack: C3 -58.5 dB re the note, the hiss's own excess)
            p0, p1 = T[0].pre(q), T[127].pre(q)
            nh = min(len(p0), len(p1), int(0.15 * FS))
            hf = excess_db(p1[-nh:], p0[-nh:]) - nl if nh >= FS // 20 and np.any(p0) else -240.0
            pd = T[de].pre(q)
            nc = min(len(p0), len(pd), int(0.06 * FS))      # the contact's window on hiss alone: NOISE de's floor
            cf = m_excess_db(pd[-nc:], p0[-nc:]) - ring if nc >= int(0.06 * FS) and np.any(p0) else -240.0
            # the contact re the note as the gate reads it (m_contact_db: the aligned difference, rectangular 0..60
            # ms); NOISE de's own reading the same way bounds what the alignment leaves (its tick ~30 dB under 127's)
            ca = {x: m_contact_db(m[x], o[x], m[0], o[0])[0] for x in (de, 127)}
            return dict(c=c, k=knock_db(m[127], o[127], m[0], o[0]) - nl, hf=hf, cf=c[de] < cf + 6, ca=ca)
        vals = S.per(nz)
        S.check(f"NOISE (CC {P['NOISE']['cc']}) contact {de} -> 127", f"key {n}, 0..60 ms",
                {s_: v and v["c"][127] - v["c"][de] for s_, v in vals.items()}, lambda v: v >= 6, ">= +6 dB", level=True,
                floor={s_: bool(v and v["cf"]) for s_, v in vals.items()})   # (the jack: NOISE de's tick in its hiss)
        S.check("NOISE 127 contact re the note", f"key {n}, 0..60 ms, the aligned difference re NOISE 0 (the gate's)",
                {s_: v and (v["ca"][127], v["ca"][de]) for s_, v in vals.items()},
                lambda v: v[0] > -35 and v[0] >= v[1] + 10,
                f"> -35 dB (a clear tick; NOISE {de} read the same way >= 10 dB under it: the alignment's residue)",
                fmt=lambda v: f"{v[0]:.1f} dB ({de}: {v[1]:.1f})")
        S.check("no knock at the key-up (NOISE 127)", f"key {n}, 0..150 ms after the key-up",
                {s_: v and (v["k"], v["hf"]) for s_, v in vals.items()}, lambda v: v[0] <= max(-60, v[1] + 3),
                "<= -60 dB re the note, or within 3 dB of the source's own hiss read the same way (no damper on this "
                "type)", fmt=lambda v: f"{v[0]:.1f} dB" + (f" (hiss {v[1]:.1f})" if v[1] > -200 else ""))
    # the MOTOR CCs: inert
    ev, dur = strikes((57,), (100,), gap=3.0, hold=2.5)
    V.reset([("RELEASE", 0)], label="motor CCs off")
    a = V.take("m-mot-off", dur, ev)
    V.reset([("RELEASE", 0), ("MOT_DEPTH", 127)], motor=True, label="motor CCs on (inert)")
    b = V.take("m-mot-on", dur, ev)
    S.check("MOTOR CCs 47 / 48 on MARIMBA", "A3 held 2.5 s, re without",
            S.per(lambda q: lu(cut(b.m(q), b.on(q)[0], 0.3, 2.3)) - lu(cut(a.m(q), a.on(q)[0], 0.3, 2.3))),
            lambda v: abs(v) <= 0.3, "within 0.3 LU (no motor on this type)", level=True)
    V.reset(label="TUNE (current)")
    t = V.take("m-tune-a4", 2.6, chord((69,), 100, 0.2, 2.2))
    S.check("TUNE (panel only): A4 re the spec", "the FM-1's current TUNE",
            S.per(lambda q: 1200 * np.log2(f0_of(cut(t.m(q), t.on(q)[0], 0.3, 1.0), f1(69)) / f1(69))),
            lambda v: abs(v) <= 2.0, "|cents| <= 2 (TUNE 0)", fmt=lambda v: f"{v:+.2f} c")


@vtest
def marimba_vel(V, S):
    """MARIMBA velocity on C2 C3 C4 C6 (MED on C4 for the brightness); every key C2..C7 (level, tuning)"""
    for n in (36, 48, 60, 84):
        ev, dur = strikes((n,), MVELS, gap=1.6, hold=1.0)
        V.reset([("BARK", 42)] if n == 60 else [], label=f"velocity {n}")
        t = V.take(f"m-vel-{n}", dur, ev)

        def lv(q):
            return [lu(cut(t.m(q), i, 0, 1.0)) for i in t.on(q)]
        vals = S.per(lv)
        S.check("velocity monotone", f"key {n}, v{MVELS[1]}..v127",
                {s_: v and float(min(np.diff(v[1:]))) for s_, v in vals.items()}, lambda d: d >= -0.3,
                "no drop > 0.3 dB (v8 may sit under the gate)", fmt=lambda d: f"{d:+.2f}")
        S.check("velocity smooth", f"key {n}", {s_: v and m_second_diff(MVELS[1:], v[1:]) for s_, v in vals.items()},
                lambda d: d <= 1.5, "second difference <= 1.5 dB per 16 velocities", fmt=lambda d: f"{d:.2f} dB")
        S.check("velocity range v8 -> v127", f"key {n}", {s_: v and v[-1] - v[0] for s_, v in vals.items()},
                lambda d: 26 <= d <= 32, "26..32 dB", level=True)
        if n == 60:
            S.check("C4 MED: v40 -> v117 brighter", "attack centroid 0..50 ms (flat start, VA-M5)",
                    S.per(lambda q: m_cen(cut(t.m(q), t.on(q)[MVELS.index(117)], 0, M_ATT)) /
                          m_cen(cut(t.m(q), t.on(q)[MVELS.index(40)], 0, M_ATT))),
                    lambda v: v >= 1.4, ">= 1.4x (KNOWN, user decision: golden x1.04)", fmt=lambda v: f"x{v:.2f}")
    keys = list(range(36, 97))
    ev, dur = strikes(keys, (100,), gap=1.4, hold=0.9)
    V.reset([("RELEASE", 64)], label="every key C2..C7")
    t = V.take("m-keys", dur, ev)

    def kl(q):
        o = t.on(q)
        fl = t.pre(q)
        lv = np.array([lu(cut(t.m(q), i, 0, 1.0)) for i in o])
        c = np.array([1200 * np.log2(max(f0_of(cut(t.m(q), i, 0.05, m_audible_s(t.m(q), i, fl)), f1(n)), 1e-9) / f1(n))
                      for i, n in zip(o, keys)])
        return lv, c
    vals = S.per(kl)
    S.check("keys C2..C7: neighbour step", "every key, v100",
            {s_: v and float(np.abs(np.diff(v[0])).max()) for s_, v in vals.items()}, lambda d: d <= 1.5,
            "<= 1.5 dB (PLAN M1, D)", fmt=lambda d: f"{d:.2f} dB")
    S.check("keys C2..C7: level span", "", {s_: v and float(v[0].max() - v[0].min()) for s_, v in vals.items()},
            lambda d: d <= 3.0, "<= 3 dB (PLAN M1, D)", fmt=lambda d: f"{d:.1f} dB")
    S.check("keys C2..C7: tuning re the spec", "", {s_: v and float(np.abs(v[1]).max()) for s_, v in vals.items()},
            lambda d: d <= 3, "|cents| <= 3", fmt=lambda d: f"{d:.2f} c")


def m_roll(keys, rate, vel, t0=0.2, dur=4.0):
    ev, k, dt = [], 0, 1.0 / rate
    while k * dt < dur:
        n = keys[k % len(keys)]
        ev += [(t0 + k * dt, "on", n, vel), (t0 + k * dt + dt / 2, "off", n, 0)]
        k += 1
    return ev, t0 + dur + 1.5


def pumping(x, i0, dur=4.0):
    """the LUFS span over sliding 0.5 s windows (hop 0.1 s) from 0.5 s into the roll to its end (PLAN M5)"""
    v = [lu(cut(x, i0, a, a + 0.5)) for a in np.arange(0.5, dur - 0.5 + 1e-9, 0.1)]
    return float(max(v) - min(v))


@vtest
def marimba_roll(V, S):
    """MARIMBA rolls at 8 / 12 Hz (MUTE 0): no clicks, no pumping, the two hands even; MUTE 127 on a roll"""
    for name, keys, rate, vel, sets in (("roll 8 Hz C4 v100", (60,), 8, 100, []), ("roll 12 Hz C4 v117", (60,), 12, 117, []),
                                        ("roll 12 Hz C2 v127 RUBBER", (36,), 12, 127, [("BARK", 127)]),
                                        ("two-hand roll C2+G2 12 Hz v117", (36, 43), 12, 117, [])):
        ev, dur = m_roll(keys, rate, vel)
        V.reset(sets + [("RELEASE", 0)], label=name)
        t = V.take("m-" + name.replace(" ", "-"), dur, ev)
        S.check("pumping", name, S.per(lambda q: pumping(t.m(q), t.on(q)[0])), lambda v: v <= 1.5,
                "sliding 0.5 s LUFS span <= 1.5 LU", fmt=lambda v: f"{v:.2f} LU")
        S.check("peak", name, S.usb(lambda q: 20 * np.log10(np.abs(t.usb).max() + 1e-12)), lambda v: True,
                "information (the limiter's ceiling -2.7 dBFS)", fmt=lambda v: f"{v:.1f} dBFS", info=True)
        if len(keys) == 2:
            lvs = {}
            for n in keys:
                e1 = [e for e in ev if e[2] == n]
                V.reset(sets + [("RELEASE", 0)], label=f"{name}: {n} alone")
                tt = V.take(f"m-roll-alone-{n}", dur, e1)
                lvs[n] = tt
            S.check("two-hand balance", name,
                    S.per(lambda q: abs(lu(cut(lvs[keys[0]].m(q), lvs[keys[0]].on(q)[0], 0.5, 4.0)) -
                                        lu(cut(lvs[keys[1]].m(q), lvs[keys[1]].on(q)[0], 0.5, 4.0)))),
                    lambda v: v <= 2.0, "the two notes within 2 LU", fmt=lambda v: f"{v:.2f} LU")
    ev, dur = m_roll((60,), 12, 100, dur=3.0)
    off = max(t_ for t_, k, a, b in ev if k == "off")
    R = {}
    for mu in (0, 127):
        V.reset([("RELEASE", mu), ("NOISE", 0)], label=f"roll MUTE {mu}")
        R[mu] = V.take(f"m-roll-mute-{mu}", dur, ev)
    # the stop as marimba_decay's MUTE (m_drop_rate: the time to fall 30 dB, the depth kept 20 dB over the jack's
    # hiss): VIBES' fit of the f0 band 30..300 ms after the key-up ran into the floor (round 2 on the FM-1: x4.3 USB,
    # x2.2 jack; this measure on the same takes: x10.4 / x10.3)
    def stop(q):
        xs = {mu: cut(R[mu].m(q), R[mu].on(q)[0], 0, off + 1.0) for mu in R}
        d = m_drop_db([(xs[mu], off - ev[0][0], R[mu].pre(q)) for mu in R])
        return m_drop_rate(xs[127], off - ev[0][0], d) / m_drop_rate(xs[0], off - ev[0][0], d)
    S.check("MUTE 127 on a roll: the stop", "after the last key-up re MUTE 0 (the time to fall 30 dB)", S.per(stop),
            lambda v: v >= 3.0, ">= 3x faster", fmt=lambda v: f"x{v:.1f}")


def m_fx_roll(notes, vel, t0, t1, rate=M_FX_RATE, jit=M_FX_JIT, seed=M_FX_SEED):
    """a sustained stimulus for the effects on MARIMBA: each note of the chord re-struck at about rate Hz (staggered
    over the notes, every interval jittered +-jit, a fixed seed: the same events in every take) from t0 to t1, each
    strike let go half an interval later (the takes run MUTE 0: the key-up does not damp). A held marimba note is
    60 dB down in ~1 s (C4), so the VIBES stimuli (a chord held 2 / 5.5 s, A4 held 1.6 s) left the swept effects
    nothing to sweep after the first second: the round-2 FM-1 scan's 35 marimba_fx failures, 25 of them on the host
    too. The jitter spreads the roll's own amplitude modulation over a band instead of a line at the strike rate,
    where the LFO rate searches (0.25 .. 12 Hz) would find it"""
    rng = np.random.default_rng(seed)
    ev, n = [], len(notes)
    for k, note in enumerate(notes):
        t = t0 + k / (rate * n)
        while t < t1 - 0.5 / rate:
            dt = (1 + jit * (2 * rng.random() - 1)) / rate
            ev += [(round(t, 4), "on", note, vel), (round(t + 0.5 * dt, 4), "off", note, 0)]
            t += dt
    return sorted(ev, key=lambda e: e[0])


@vtest
def marimba_fx(V, S):
    """every effect page's every control at min / default / max on MARIMBA (vibes_fx's checks) on a sustained roll"""
    v = M_FX_VEL
    stim = dict(CH=(ST_CH[0], m_fx_roll(FXCH, v, 0.1, 2.1)), LONG=(ST_LONG[0], m_fx_roll(FXCH, v, 0.1, 5.6)),
                NOTE=(ST_NOTE[0], m_fx_roll((69,), v, 0.1, 1.6)),
                ATK=ST_CH,               # COMP ATTACK: a strike out of silence (on a roll the compressor never lets go)
                BW=(0.05, 2.05),         # the spectra over the whole roll: two takes of one roll 0.2-0.8 dB apart at 1 s
                CS_PERIOD=True,          # CHORUS SYNC by the period: the harmonic sum took the 1/4's 3rd harmonic
                CS_BAND=True,            # CHORUS SPEED 50 / 127 by the band re DEPTH 0: the centroid read 1.31 for 0.87
                SPREAD=True,             # FILTER OFF judged against two dry takes of the roll (one more take)
                FDN=16384,               # PHASER FDBK frame by frame in 0.34 s frames: two takes of one roll are
                #                          3.7 dB apart in 43 ms frames (the strikes' phase), 1.6 dB in 0.34 s
                PH0=(10.5, m_fx_roll(FXCH, v, 0.1, 10.1)),   # PHASER / CHORUS SPEED 0 (4 s): 9.6 s trace, 2.4 periods
                JFLOOR=True,             # the DRIVE rows: the jack's own distortion at MARIMBA's quiet level
                V3LINE=True,             # DRIVE TONE's V3 as a line: the +-3 % band read the resampler's image of V1
                DL_WOW=True)             # DELAY DIG vs ANA: the echoes (V1 alone) by the tape's wow / flutter
    return vibes_fx(V, S, stim=stim, extra=[("RELEASE", 0)])


@vtest
def marimba_jump(V, S):
    """MARIMBA: every effect switched on (vibes_jump without the motor); the level re ST77 and VIBES on A3-E4-A4"""
    # the chord re-struck (m_fx_roll, as marimba_fx): a held marimba chord is 60 dB down in ~1 s, so the switches at
    # 1.2 / 2.4 s and the 0.2..3.2 s slope read a dying tail (round 2 on the FM-1: wah 64 switched on +9.9 LU, the
    # phaser's and the chorus' slopes +1.4 / +1.3 dB/s)
    _jump(V, S, [j for j in JUMPS if j[2][0] != "MOT_ON"], ch=m_fx_roll(FXCH, M_FX_VEL, 0.1, 3.4), extra=[("RELEASE", 0)])
    ch = chord((57, 64, 69), 100, 0.4, 2.4)
    T = {}
    for name, sel in (("MARIMBA", []), ("VIBES", [(0.0, "cc", 79, VIBES_CC79)]), ("ST77", [(0.0, "cc", 80, 8)])):
        V.reset([("RELEASE", 64)], label=f"level {name}")
        T[name] = V.take(f"m-level-{name}", 3.4, sel + ch)

    def L(name, q):
        t = T[name]
        return lu(cut(t.m(q), t.on(q)[0], 0, 1.0))
    S.check("MARIMBA re ST77, A3-E4-A4 v100", "LUFS 0..1 s", S.per(lambda q: L("MARIMBA", q) - L("ST77", q)),
            lambda v: abs(v - MLEVEL_DB) <= 1.0, f"{MLEVEL_DB:+.1f} +-1 LU (level A: the +2.5 dB offset under the "
            "-0.5 dBFS ceiling)", level=True)
    S.check("MARIMBA re VIBES", "LUFS 0..1 s", S.per(lambda q: L("MARIMBA", q) - L("VIBES", q)), lambda v: True,
            "information", level=True, info=True)


MORDER = ("marimba_jump", "marimba_voice", "marimba_decay", "marimba_vel", "marimba_roll", "marimba_fx")


VORDER = ("vibes_jump", "vibes_voice", "vibes_decay", "vibes_motor", "vibes_vel", "vibes_fx")


def vibes_main(names, a):
    global VDEBUG
    VDEBUG = a.dry_run
    V = VRig(a.source, a.jack_dev, a.jack_ch, a.dry_run, a.wav, a.offline, getattr(a, "host", None))
    os.makedirs(OUT, exist_ok=True)
    suites, fails = [], 0
    for n in names:
        S = VSuite(n, V)
        t0, k0, p0 = time.time(), len(V.takes), V.plan_s
        global ENGINE
        ENGINE = "marimba" if n.startswith("marimba_") else "vibes"   # (reset's CC 79, the spec)
        print(f"== {n}: {VTESTS[n].__doc__.strip().splitlines()[0]}", flush=True)
        try:
            VTESTS[n](V, S)
        except Exception as e:  # keep scanning
            import traceback
            traceback.print_exc()
            S.rows.append(dict(check="suite error", setting="", usb=repr(e), jack="", crit="", mismatch=None,
                               status="FAIL", ok=False))
        S.ntakes = len(V.takes) - k0
        S.secs = (V.plan_s - p0) if a.dry_run else time.time() - t0
        S.ttakes = V.takes[k0:]
        bad_t = [t["tag"] for t in S.ttakes if t.get("usb_fs") or t.get("usb_clicks") or t.get("jack_clicks")]
        if bad_t:
            S.rows.append(dict(check="takes: full scale / clicks", setting="", usb=", ".join(bad_t), jack="", crit="none",
                               mismatch=None, status="FAIL", ok=False))
        gap = [f"{t['tag']} ({'; '.join(t['gaps'][:3])})" for t in S.ttakes if t.get("gaps")]
        if gap:
            S.rows.append(dict(check="takes: a recording stream had a gap", setting="ADC time vs frames",
                               usb=", ".join(gap), jack="", crit="none (the numbers of those takes are not the sound)",
                               mismatch=None, status="FAIL", ok=False))
        slip = [f"{t['tag']} ({t['usb_slip_ms']:.0f} ms)" for t in S.ttakes if t.get("usb_slip_ms", 0) > SLIP_MS]
        if slip:
            S.rows.append(dict(check="takes: the USB stream slipped (the jack did not)", setting="latency step between note-ons",
                               usb=", ".join(slip), jack="", crit=f"<= {SLIP_MS:.0f} ms (the USB numbers of those strikes are "
                               "not the sound: USB capture / the FM-1's USB audio, to look at)", mismatch=None,
                               status="FAIL", ok=False))
        fails += bool(S.fails())
        for r in S.rows:
            if not r["ok"] or a.dry_run or r.get("known"):
                mm = f"  USB-jack {r['mismatch']:+.2f}" if r["mismatch"] is not None else ""
                print(f"   {r['status']:13s} {r['check']} [{r['setting']}]  USB {r['usb']}  jack {r['jack']}  ({r['crit']}){mm}")
                if r.get("known"):
                    print(f"                 KNOWN: {r['known']}")
        print(f"{'ok  ' if not S.fails() else 'FAIL'}  {n}: {len(S.rows)} checks, {len(S.fails())} failed / flagged, "
              f"{S.ntakes} takes, {S.secs:.0f} s{' (estimated)' if a.dry_run else ''}", flush=True)
        suites.append(S)
    V.finish()
    pre = "marimba" if all(n.startswith("marimba_") for n in names) else "vibes"
    path = os.path.join(OUT, f"{pre}-dryrun.md" if a.dry_run else f"{pre}-offline.md" if a.offline else
                        f"{pre}-host.md" if getattr(a, "host", None) is not None else f"{pre}-report.md")
    v_report(suites, V, path, a.dry_run)
    if a.dry_run:
        print(f"estimated device time: {V.plan_s / 60:.1f} min ({len(V.takes)} takes); "
              f"{', '.join(f'{S.name} {S.secs / 60:.1f}' for S in suites)} min")
    print(f"report: {os.path.relpath(path, ROOT)} (+ .json)")
    return fails


def run_classic(names, r=None, report="report"):
    """the Rhodes-era tests: build/hil/report.txt / report.json (r: a HostRig -> host-report.*), ends on F01"""
    r = r or Rig()
    rep, lines, fails = {}, [], 0
    for n in names:
        t0 = time.time()
        try:
            ok, msg = TESTS[n](r, rep)
        except Exception as e:  # keep scanning
            ok, msg = False, f"error: {e!r}"
        fails += not ok
        line = f"{'ok  ' if ok else 'FAIL'}  {n:13s} {msg}  ({time.time() - t0:.0f} s)"
        print(line, flush=True)
        lines.append(line)
    r.dry()
    r.pc(0)
    json.dump(rep, open(os.path.join(OUT, report + ".json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, report + ".txt"), "w").write("\n".join(lines) + "\n")
    print(f"{fails} failed; report: build/hil/{report}.txt, {report}.json")
    return fails


def stress_max(r, rep, rate=48000, seconds=None, quick=False, tier=None, engines=None):
    """the cracks test (tools/hil_crackle.py t1): T1's S1 calls it by name (tools/hil_must.py), per rate"""
    import hil_crackle
    return hil_crackle.t1(r, rep, rate=rate, seconds=seconds, quick=quick, tier=tier,
                          engines=tuple(engines) if engines else hil_crackle.ENGINES)


TESTS["stress_max"] = stress_max                       # (not in the default run: main leaves it out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--tier" in argv or any(x.startswith("--tier=") for x in argv):   # the release tiers (tools/hil_must.py):
        import hil_must                                                # must = T1's scenarios, full = T2
        return hil_must.tier_main(argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--vibes", action="store_true", help="every VIBES suite (not part of the default run)")
    ap.add_argument("--marimba", action="store_true", help="every MARIMBA suite (not part of the default run; P5)")
    ap.add_argument("--source", default="both", choices=("usb", "jack", "both"), help="VIBES suites: what to record")
    ap.add_argument("--jack-dev", default="M6", help="VIBES suites: the interface recording the headphone jack")
    ap.add_argument("--jack-ch", type=int, default=5, help="VIBES suites: its input (1-based)")
    ap.add_argument("--dry-run", action="store_true", help="VIBES suites: print the planned events, open no MIDI / audio")
    ap.add_argument("--wav", action="store_true", help="VIBES suites: save every take to build/hil/vibes/")
    ap.add_argument("--repeats", type=int, help="takes per measure for every repeated one (default: REPEATS)")
    ap.add_argument("--host", nargs="?", type=int, const=1, metavar="SEED",
                    help="the classic suites on the host build (tests/host.c), no FM-1: prerolls / latencies from SEED;"
                         " report build/hil/host-report.txt; VIBES suites (--only vibes_fx ...): USB only,"
                         " build/hil/vibes-host.md")
    ap.add_argument("--no-reset", action="store_true", help="no CC 10 before the takes (to see what it changes)")
    ap.add_argument("--offline", nargs="?", const=os.path.join(OUT, "vibes"), metavar="DIR",
                    help="VIBES suites: re-analyse the takes saved by --wav (default build/hil/vibes/), no MIDI / audio;"
                         " report build/hil/vibes-offline.md (+ .json)")
    ap.add_argument("--rate", type=int, choices=(44100, 48000), help="stress_max: the USB rate (default 44100)")
    a = ap.parse_args(argv)
    if a.only and "stress_max" in a.only.split(","):   # the cracks test: tools/hil_crackle.py (its own recorder, rates)
        import hil_crackle
        rest = [n for n in a.only.split(",") if n != "stress_max"]
        argv = (["--host"] if a.host is not None else []) + (["--dry-run"] if a.dry_run else []) + \
            ["--source", "usb" if a.source == "usb" else "both", "--jack-dev", a.jack_dev, "--jack-ch", str(a.jack_ch)] + \
            (["--rate", str(a.rate)] if a.rate else [])
        code = hil_crackle.main(argv)
        if not rest:
            return code
        a.only = ",".join(rest)
        if code:
            print("stress_max: FAIL (the other tests run on)")
    if a.list:
        print(" ".join(TESTS))
        print("VIBES (--vibes or --only):", " ".join(VORDER))
        print("MARIMBA (--marimba or --only):", " ".join(MORDER))
        return 0
    if a.offline and not a.only:
        a.vibes = True
    names = a.only.split(",") if a.only else ([] if a.vibes or a.marimba else [n for n in TESTS if n != "stress_max"])
    if a.vibes:
        names += [n for n in VORDER if n not in names]
    if a.marimba:
        names += [n for n in MORDER if n not in names]
    bad = [n for n in names if n not in TESTS and n not in VTESTS]
    if bad:
        sys.exit(f"unknown test(s) {bad}; --list")
    classic, vib = [n for n in names if n in TESTS], [n for n in names if n in VTESTS]
    os.makedirs(OUT, exist_ok=True)
    fails = 0
    if a.repeats:
        for k in REPEATS:
            REPEATS[k] = a.repeats
    if a.no_reset:
        RigBase.reset = False
        globals()["with_reset"] = lambda events, lead=RESET_LEAD: sorted(events, key=lambda e: e[0])
    if a.host is not None:                          # the VIBES suites too: USB only, report build/hil/vibes-host.md
        if classic:
            fails += run_classic(classic, HostRig(a.host, reset=not a.no_reset), "host-report")
        if vib:
            fails += vibes_main(vib, a)
        return 1 if fails else 0
    if a.dry_run or a.offline:
        if a.offline:
            a.dry_run = False
        if classic:
            print(f"--dry-run / --offline cover the VIBES suites only: not running {', '.join(classic)}")
        return 1 if vibes_main(vib, a) else 0
    if classic:
        fails += run_classic(classic)
    if vib:
        fails += vibes_main(vib, a)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
