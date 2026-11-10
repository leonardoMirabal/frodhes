#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move's headphone / line jack against its USB-C audio, while MIDI plays Frodhes: the Move's hil_jack.py.

    tools/move_jack.py [--engines rhodes,vibes,marimba] [--secs 60] [--nv 16] [--dev M6 --ch 5]
                       [--midi NAME --audio NAME --midi-ch N] [--ws URL|none] [--host USER@HOST]
                       [--prove] [--no-preflight] [--out DIR] [--dry-run [--dry-inject KIND@S ...]]

MIDI goes to the Move's "Ableton Move Standalone Port" and the Move's USB audio ("Ableton Move Audio", Main Out) is
recorded (tools/move_hil.py UsbRig, its persistent recorder) while the jack is recorded at the same time on the audio
interface's input --ch (hil_jack.Jack's: default the interface "M6", input 5, mono), each stream at its own rate in
the same recorder (the jack at 48 kHz). The MIDI port is opened before any audio stream (python-rtmidi deadlocks on
this Mac otherwise). Never the MIDI ports "Frodhes", "M6", MIDISPORT, IAC or AF16Rig (move_hil.NEVER_PORTS); audio
input: the interface's input --ch and the Move's USB audio only. The MOVE lock is taken (move_install.move_lock).

Per engine (CC 79, as move_hil.ENGINE_CCS: VIBES with its motor off for the level takes, MARIMBA):
  (a) silence   the jack's floor with nothing playing (the Move's digital Main Out at rest) and the jack's hiss
                (8-20 kHz under a quiet low note) against USB's: hiss_added_db = the jack's 8-20 kHz minus USB's
                times the jack's gain (from the loud chord)
  (b) loud      a loud chord (7 notes v127): peak and RMS on the jack and on USB, the jack re USB in dB
  (c) worst     move_hil usb-stress's take recorded on the jack too (hil_crackle's worst stimulus: 16 voices, every
                effect heavy, the pedal; two takes of --secs / 2, LEVEL -12 dB and unity): hil_crackle's detectors
                on the JACK (zero, clip, click, step, track: hil_crackle.GATED) and det_hf_click (bursts over 12
                kHz: a one-sample click of the Move's reaches the 48 kHz jack band-limited, which det_click's
                impulse-shape test turns down); USB's are information. 0 jack events is the pass
  (d) compare   jack against USB, time-aligned (hil_crackle.track: the clocks' drift fitted by cross-correlation,
                USB warped onto the jack's clock), on the worst takes and the loud chord: the lag, the drift, the gain
                (least squares) and so the level difference, the residual (the jack minus USB through a least-squares
                FIR of the analog path, path_fir) overall and per octave band re the jack; clicks in the residual
                outside the attack / pedal guards (det_hf_click) with no USB event near: a click on the jack that USB
                does not have. 0 is the pass
The proof (always first; --prove: the proof alone): the jack detector must show it works on this rig before any
verdict counts. On a held pedalled chord (Rhodes), one clean take and one take with a real fault injected on the
device: the plugin's dev key dev_fault (move/move_bridge.h mb_fault, set over Schwung's WebSocket), PROVE_N
one-sample clicks of PROVE_LSB LSB in what the plugin hands Schwung (after its trim: on Main Out, USB-C and the jack,
as a real glitch of the device's would be), one every PROVE_EVERY_MS. Proven when the clean take gives 0 jack
events and in the fault take USB shows at least PROVE_N - 1 of the clicks (the fault reached the output) and the
jack's click detectors (det_hf_click, hil_crackle's click) find every one USB shows, and neither take has an
event flagged as on the jack only (the clicks are on USB too: (d) must say so). A failed proof stops the run (exit 2): no PASS is given. A build
without dev_fault shows no click on USB: the proof says so (install a build with it, boot.txt "dev_fault=1").

Writes build/move/hil/<time>-jack/summary.md + summary.json (each take's npz: USB LSB at its rate, the jack LSB at 48
kHz, in <engine>/ and proof/). Exit 0 PASS, 1 FAIL, 2 stopped (no device, the preflight, the proof).
--dry-run: no device, no lock (the host build plays the MIDI, as hil_jack's dry run; the jack is simulated from its USB by
hil_crackle.sim_jack: 48 kHz on a drifting clock, AC coupling, the codec's low-pass, noise, DRY_GAIN; dev_fault
simulated): checks the procedure and the measures, never the Move; results to build/move/hil/dry/<time>-jack/.
--dry-inject KIND@S (dry only, the worst takes, each at the first moment at or after S outside the guards): jclick
(a click on the jack only: (d) must flag it), click (on USB and the jack: a device glitch).

Jack figures are re the interface's full scale, so they depend on its input gain and the Move's volume; the
differences are what matter. Never run on the device from a test or a gate without the user's OK to use the Move."""
import argparse
import json
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_crackle as hc  # noqa: E402
import hil_jack as hj  # noqa: E402
import hil_scan as hs  # noqa: E402
import move_hil as mh  # noqa: E402
import move_install as mi  # noqa: E402

FS_J = hc.FS_JACK                              # the jack's rate (48 kHz; resampled if the interface runs another)
ENGINES = ("rhodes", "vibes", "marimba")
WIDEN = hc.DEV_JITTER                          # the guards widened: a MIDI event lands in the Move's next 128-frame
                                               # block (2.9 ms) after the Standalone port's path (cable 2 -> Schwung)
PROVE_N, PROVE_EVERY_MS, PROVE_LSB = 6, 1000, 1500   # the proof's fault: 6 clicks of 1500 LSB (-27 dBFS), 1 a second
PROVE_SECS = 8.0
PROVE_CHORD = (48, 55, 60, 64)
MATCH_S = 0.012                                # a jack event this close to a USB one (on the event clock) is the same
USB_KINDS = ("click", "zero", "repeat", "stale", "clip", "step")   # USB's glitches. Not click-lim (the limiter's
# own steps, information): the unity takes had 910 of them in 30 s, one every 33 ms, so MATCH_S around them excused
# most of a take. A jack burst at one of them is excused only by USB's own burst there (HF_MATCH_S, split_hf)
JACK_CLICKS = ("click", "click-lim", "hfclick")
HF_LO, HF_K, HF_FLOOR = 12000.0, 12.0, 20.0     # det_hf_click: over 12 kHz, 12 x the local floor and >= 20 LSB
OCT_EDGES = (22, 44, 88, 177, 355, 710, 1420, 2840, 5680, 11360, 20000)
DRY_GAIN = 0.5                                 # the dry run's jack: 6 dB under USB (the Move's line out on the M6:
                                               # unmeasured; the jack's figures of a dry run are SIMULATED)
DRY_JCLICK = 1000.0                            # (LSB on the jack) --dry-inject jclick: -30 dBFS (with the
                                               # Move's jack floor at -65.7 dBFS RMS, 300 LSB is under det_hf_click's
                                               # 12 x the floor over 12 kHz, and under det_click's 20 x)
DRY_CLICK = 1500.0                             # (LSB on USB) --dry-inject click
DRY_FLOOR_DBFS = -65.7                         # the dry jack's own noise floor (RMS): the Move's jack on the M6's
                                               # input 5 before the proof's chord, 2026-11-04 (flat 200 Hz-8 kHz,
                                               # USB exact zero there; the FM-1's jack: about -91)
DRY_DROP_S, DRY_STEP_S = 0.003, 0.3            # --dry-inject jdrop: 3 ms of zeros; jstep: 300 ms 20 dB down (the jack)
NCC_GATE = 0.97                                # hil_crackle.track's ncc gate, as it is with no noise
SNR_MIN_DB = 10.0                              # a track window is judged only this far over the jack's own floor
TRACK_WIN = 2400                               # (hil_crackle.track's window: 50 ms at 48 kHz)
NEVER_MIDI = ("frodhes", "m6") + mh.NEVER_PORTS


def dbfs(x):
    x = np.asarray(x, float)
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) / 32768 + 1e-15)) if x.size else -300.0


def peak_dbfs(x):
    return float(20 * np.log10(np.abs(np.asarray(x, float)).max() / 32768 + 1e-15))


def band_db(x, fs, f0, f1):
    """the power in [f0, f1) as an RMS level, dBFS (hil_jack.band_db at any rate)"""
    x = np.asarray(x, float) / 32768
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / fs)
    m = (fr >= f0) & (fr < f1)
    return float(10 * np.log10(sp[m].sum() * 2 / (len(x) ** 2 * 0.375) + 1e-30))


# ----------------------------------------------------------------------------------------------------------- the rigs
class MoveJack(hj.Jack):
    """hil_jack's Jack on the Move: the jack on the interface devname's input ch (1-based), USB = the Move's
    (move_hil.UsbRig rig, opened first: its MIDI port before any audio stream). both(dur, events) -> {"usb": (n, 2)
    LSB at "usb_sr", "jack": (m,) LSB at 48 kHz, "clock": each stream's rate off the event clock (the recorder's ADC
    times), "xruns": the recorder's flags and gaps inside the take}: slices of the rig's persistent recorder, the jack
    added to it as its second stream"""
    fake = False

    def __init__(self, rig, devname="M6", ch=5):
        import sounddevice as sd
        devs = [d for d in sd.query_devices() if d["name"] == devname]
        if not devs or devs[0]["max_input_channels"] < ch:
            raise SystemExit("no audio input %s with an input %d (have: %s)" % (devname, ch, ", ".join(
                d["name"] for d in sd.query_devices() if d["max_input_channels"])))
        super().__init__(devname, ch, rig=rig)
        self.ws = None

    def fault_open(self, url, slot=0):
        """a WebSocket kept open over the proof: dev_fault is armed with no wait before the take starts (the clicks
        count from the arming), the manager's re-read after the writes is read at the end (fault_close)"""
        import move_ws
        self.ws = move_ws.Remote(url).__enter__()
        self.ws.subscribe(slot)
        self.ws.pump(0.5)

    def fault(self, spec, url, slot=0):
        """the plugin's dev_fault key over Schwung's WebSocket (move/move_bridge.h): "click N EVERY_MS LSB", or "off" """
        if self.ws is not None:
            self.ws.set_param(slot, "synth:dev_fault", spec)
            return
        import move_ws
        with move_ws.Remote(url) as w:
            w.subscribe(slot)
            w.pump(0.5)
            w.set_param(slot, "synth:dev_fault", spec)
            w.pump(1.5)

    def fault_close(self):
        if self.ws is not None:
            try:
                self.ws.pump(1.5)
            finally:
                self.ws.__exit__(None, None, None)
                self.ws = None

    def both(self, dur, events):
        cap = self.r.capture()
        if "jack" not in cap.rings:
            cap.add("jack", self.jdev, (self.jch,), self.jn)
        st = cap.now()
        t0 = time.monotonic()
        for t, kind, a, b in sorted(events, key=lambda e: e[0]):
            while time.monotonic() - t0 < t:
                time.sleep(0.0003)
            self.r._send(kind, a, b)
        got, clock, xr = {}, {}, []
        for k in ("usb", "jack"):
            x = cap.cut(k, st[k], dur)
            ring = cap.rings[k]
            fr = ring.rate(st[k], dur)
            clock[k] = None if fr is None else fr / ring.sr - 1.0
            xr += ["%s: %s" % (k, f) for f in ring.flags_between(st[k], st[k] + dur)]
            xr += ["%s: recorder gap %+.0f ms at %.2f s" % (k, 1000 * g, a - st[k]) for a, g in ring.gaps
                   if st[k] <= a <= st[k] + dur]
            got[k] = (np.asarray(x, float) * 32768.0, ring.sr)
        u, usr = got["usb"]
        j, jsr = got["jack"]
        j = j[:, 0]
        if jsr != FS_J:
            j = hs.to_fs(j, jsr, dur)
            clock["jack"] = None
        return {"usb": u, "usb_sr": usr, "jack": j, "clock": clock, "xruns": xr}


class DryHost(hs.RigBase):
    """the dry run's "Move": the host build (tests/host.c, hil_crackle.host_take) playing each take with the panel as
    sent so far (every CC, the last of each), USB at 44.1 kHz, the events on time"""
    fake = True
    port_name, audio_name, ch = "the host build (dry run)", "the host build's output", 1

    def __init__(self):
        self.mido, self.state, self.n = hs._Mido, {}, 0

    def _send(self, kind, a, b=0):
        if kind == "cc" and a not in (hs.RESET_CC, 120):
            self.state.pop(a, None)
            self.state[a] = b

    def wait_silence(self, limit=10.0, floor_db=-80.0):
        return True

    def rec(self, dur, events):
        self.n += 1
        x, _ = hc.host_take("mjack-%d" % self.n, list(self.state.items()), events, dur, tag="move_jack")
        for t, kind, a, b in events:
            if kind == "cc":
                self._send("cc", a, b)
        n = int(round(dur * hc.FS_HOST))
        x = np.pad(np.asarray(x, float), ((0, max(0, n - len(x))), (0, 0)))[:n]
        return {"x": x, "sr": hc.FS_HOST, "xruns": []}


class DryJack:
    """--dry-run: the same calls without a device, as hil_jack's dry run: USB = the host build playing the MIDI
    (DryHost); the jack = hil_crackle.sim_jack of USB at DRY_GAIN (48 kHz on a clock JACK_DRIFT off, JACK_DELAY late,
    AC coupling, the codec's low-pass, noise). fault(spec): dev_fault simulated (its clicks at EVERY_MS k from the
    next take's start, at frame 37 of a 128-frame callback, on USB and so on the jack). jinject [(kind, t)]: jclick
    (the jack only) / click (USB and the jack) in the worst takes (worst=True), each at the first moment at or after
    t outside the take's guards. Every jack figure of a dry run is SIMULATED"""
    fake = True

    def __init__(self, seed=1, jinject=()):
        self.r = DryHost()
        self.jinject, self.armed, self.seed = list(jinject), None, seed

    def fault_open(self, url=None, slot=0):
        pass

    def fault_close(self):
        pass

    def fault(self, spec, url=None, slot=0):
        p = spec.split()
        self.armed = None if not p or p[0] != "click" or len(p) < 3 else (int(p[1]), float(p[2]) / 1000,
                                                                          float(p[3]) if len(p) > 3 else 1000.0)

    def both(self, dur, events, worst=False, ccs=()):
        tk = self.r.rec(dur, events)
        u, sr = np.array(tk["x"], float), tk["sr"]
        if self.armed:
            n, every, lsb = self.armed
            for k in range(1, n + 1):
                i = (int(k * every * sr) // mh.BLOCK) * mh.BLOCK + 37
                if i < len(u):
                    u[i] = np.clip(u[i] + lsb, -32768, 32767)
            self.armed = None
        jinj = []
        if worst and self.jinject:
            g = hc.guards(events, ccs)
            for kind, t in self.jinject:
                while any(-hc.GUARD[0] - 2 * WIDEN <= t - x <= hc.GUARD[1] + 2 * WIDEN for x in g):
                    t += 0.005
                if kind in ("jdrop", "jstep"):              # (the drop, the step's first 30 ms, where the sound is
                    w = int((DRY_DROP_S if kind == "jdrop" else 0.03) * sr)   # loudest within 300 ms, outside the
                                                            # guards: a 3 ms gap in material near the jack's floor
                    ok = [t + 0.005 * d for d in range(-60, 61)   # is no fault anyone hears or any detector can see,
                          if not any(-hc.GUARD[0] - 2 * WIDEN <= t + 0.005 * d - x <= hc.GUARD[1] + 2 * WIDEN
                                     for x in g) and 0 <= int((t + 0.005 * d) * sr) < len(u) - w]
                    if ok:                                  # and the mark followed the sound; the time is recorded)
                        t = max(ok, key=lambda c: float(np.mean(u[int(c * sr):int(c * sr) + w] ** 2)))
                if kind == "click":
                    i = int(t * sr)
                    if i < len(u):
                        u[i] = np.clip(u[i] + DRY_CLICK, -32768, 32767)
                jinj.append((kind, t))
        self.seed += 1
        j = hc.sim_jack(u, fs_usb=sr, gain=DRY_GAIN, seed=self.seed)
        j += np.random.default_rng(self.seed + 7).normal(0, 32768 * 10 ** (DRY_FLOOR_DBFS / 20), len(j))
        for kind, t in jinj:                                # (sim_jack: the jack JACK_DELAY late, its clock slow)
            i = int((t + hc.JACK_DELAY) / (1 + hc.JACK_DRIFT) * FS_J)
            if i >= len(j):
                continue
            if kind == "jclick":
                j[i] += DRY_JCLICK
            elif kind == "jdrop":
                j[i:i + int(DRY_DROP_S * FS_J)] = 0.0
            elif kind == "jstep":
                j[i:i + int(DRY_STEP_S * FS_J)] *= 0.1
        return {"usb": u, "usb_sr": sr, "jack": j, "clock": {"usb": 0.0, "jack": 1.0 / (1.0 + hc.JACK_DRIFT) - 1.0},
                "xruns": [],                                 # (the rates as a device run's recorder measures them)
                "injected": [(k, round(t, 4)) for k, t in jinj]}


def rec(J, dur, events, worst=False, ccs=()):
    if J.fake:
        return J.both(dur, events, worst=worst, ccs=ccs)
    return J.both(dur, events)


# ------------------------------------------------------------------------------------------------------ the analysis
def det_hf_click(x, fs, gmask, lo=HF_LO, k=HF_K, floor=HF_FLOOR):
    """[(frame, detail)]: clicks on a band-limited recording (the jack): bursts over lo Hz (a 6th-order high-pass,
    zero phase) over k x their local floor (the median of |x| over +-50 ms, x 1.4826, + 1 LSB) and >= floor LSB,
    outside the guards (gmask), one per 5 ms. Why: the Move renders at 44.1 kHz and the jack is recorded at 48 kHz
    through its DAC and the interface's ADC, so a one-sample click of the Move's arrives on the jack as a band-limited
    pulse between samples, which hil_crackle.det_click's impulse-shape test (ncc >= 0.98 against the interpolator's
    response to one sample) turns down: on the dry run's jack it found 0 of 6 clicks of 1500 LSB (shape 0.2-0.4).
    Music has little over 12 kHz; a click's spectrum is flat up there. On the host build's worst takes (every engine,
    HEAVY, 16 voices, sim_jack) the clicks stood 115-185 x their floor, the clean audio outside the guards under 3.3 x"""
    from scipy.signal import butter, sosfiltfilt
    x = np.asarray(x, float)
    if len(x) < int(0.2 * fs):
        return []
    a = np.abs(sosfiltfilt(butter(6, lo, "high", fs=fs, output="sos"), x))
    fl = hc._local_median(a, half=int(0.05 * fs), hop=int(0.005 * fs)) * 1.4826 + 1.0
    bad = (a > k * fl) & (a > floor) & ~gmask[:len(a)]
    e = int(0.05 * fs)
    bad[:e] = bad[-e:] = False                          # (the filter's and the take's edges)
    out, last = [], -10 ** 9
    for i in np.flatnonzero(bad):
        if i - last > int(0.005 * fs):
            w = a[i:i + int(0.001 * fs)]
            p = i + int(np.argmax(w))
            out.append((int(p), "burst over %d kHz %.0f LSB = %.0f x local" % (lo / 1000, a[p], a[p] / fl[p])))
        last = i
    return out


LEAD_QUIET_DBFS = -70.0                        # a take's lead (before its first note) must be this quiet on USB


def lead_dbfs(u, sr, events, cu):
    """USB's level (dBFS RMS) in the take's lead: from 10 ms to 10 ms before the first note-on (None: no lead)"""
    on = [e[0] for e in events if e[1] == "on"]
    if not on:
        return None
    a, b = int(0.01 * sr), int((float(hc.map_clock(min(on), cu)) - 0.01) * sr)
    return dbfs(np.asarray(u, float)[a:b]) if b - a >= int(0.04 * sr) else None


def jack_floor(j, events, cj, usb_lead_db=None):
    """the jack's own noise power (LSB^2): its lead, from 10 ms to 10 ms before the first note-on on the jack's clock;
    None under 40 ms of lead, or when USB is not quiet there (usb_lead_db over LEAD_QUIET_DBFS: the lead holds
    music, not the floor; the first device run's unity takes began on the low take's tail at -5 dBFS, and the
    'floor' read -12.8 dBFS)"""
    on = [e[0] for e in events if e[1] == "on"]
    if not on or (usb_lead_db is not None and usb_lead_db > LEAD_QUIET_DBFS):
        return None
    a, b = int(0.01 * FS_J), int((float(hc.map_clock(min(on), cj)) - 0.01) * FS_J)
    return float(np.mean(np.asarray(j[a:b], float) ** 2)) if b - a >= int(0.04 * FS_J) else None


HF_MATCH_S = 0.003                             # a jack burst this close to one of USB's (on the clocks) is USB's too


def usb_hf_times(q, sr, cu, cj):
    """the times (on the jack's clock, sorted) of every USB frame over HF_LO at half det_hf_click's gates, no guards:
    the music's high-frequency transients (the drive, the delay's and the reverb's edges) as USB has them. Every
    frame, not one burst per 5 ms: two jack bursts 6.4 ms apart (marimba, 14.967 / 14.974 s) had USB's under both"""
    from scipy.signal import butter, sosfiltfilt
    x = np.asarray(q, float)
    x = x[:, 0] if x.ndim == 2 else x
    if len(x) < int(0.2 * sr):
        return np.zeros(0)
    a = np.abs(sosfiltfilt(butter(6, HF_LO, "high", fs=sr, output="sos"), x))
    fl = hc._local_median(a, half=int(0.05 * sr), hop=int(0.005 * sr)) * 1.4826 + 1.0
    i = np.flatnonzero((a > HF_K / 2 * fl) & (a > HF_FLOOR / 2))
    return np.sort(hc.map_clock((i / sr - cu[0]) / (1.0 + cu[1]), cj))       # (usb_to_jack, every frame at once)


def near(ts, t, tol):
    """any of the sorted times ts within tol of t"""
    k = int(np.searchsorted(ts, t - tol))
    return k < len(ts) and ts[k] <= t + tol


def split_hf(events, usb_ev, usb_hf):
    """the jack's hfclick events: (kept, music): kept where USB's detectors have an event there too (a glitch of the
    device: on both, the proof's clicks) or where USB has no burst there (the jack's own); music where USB has the
    same burst and no event (the music's own transient, which USB's bit-exact detectors pass). The first device run's
    low takes: 3-5 jack-only hfclicks an engine, 22-72 LSB at 13-39 x local, each with USB's burst of the same size
    (x the jack's gain: 30-99 LSB) at the same moment"""
    kept, music = [], []
    for e in events:
        if e[1] == "hfclick" and not any(abs(e[0] - x) <= MATCH_S for x in usb_ev) and \
                near(usb_hf, e[0], HF_MATCH_S):
            music.append(e)
        else:
            kept.append(e)
    return kept, music


NCC_RE = re.compile(r"ncc ([0-9.]+) <")


def track_noise_aware(events, j, noise):
    """hil_crackle.track's "jack vs USB ncc < 0.97" events judged against the jack's own floor: (kept, dropped). A
    floor of power N under a window of power P caps the jack-vs-USB correlation at sqrt(1 - N / P) (the floor is in
    the jack, not in USB), so the gate is ncc^2 < NCC_GATE^2 - N / P (the plain 0.97 with no noise), and a window
    under SNR_MIN_DB over the floor is not judged. Why: the Move's jack on the M6 has a floor of -65.7 dBFS (the
    proof's lead, 2026-11-04), and the proof's clean chord, decaying from -23 to -62 dBFS, fell from ncc 0.996 to
    0.84 as it neared the floor: the residual after the path stayed at the floor's -65 dBFS the whole take (a drift
    would have grown with the level; the clocks' 61 ppm is fitted and warped out by track). Slips stay as they are"""
    if noise is None:
        return list(events), []
    kept, dropped = [], []
    for e in events:
        m = NCC_RE.search(e[2]) if e[1] == "track" else None
        if not m:
            kept.append(e)
            continue
        a = int(round(e[0] * FS_J))
        p = float(np.mean(np.asarray(j[a:a + TRACK_WIN], float) ** 2)) + 1e-9
        ncc, snr = float(m.group(1)), 10 * np.log10(p / max(noise, 1e-9))
        if snr >= SNR_MIN_DB and ncc ** 2 < NCC_GATE ** 2 - noise / p:
            kept.append((e[0], e[1], e[2] + " (SNR %.1f dB: gate %.3f)" % (snr, np.sqrt(NCC_GATE ** 2 - noise / p))))
        else:
            dropped.append((e[0], e[1], e[2] + " (SNR %.1f dB over the jack's floor)" % snr))
    return kept, dropped


def judge(tk, events, ccs=()):
    """hil_crackle's detectors on USB and the jack of one take (each recording's clock fitted to the events) and the
    jack-vs-USB comparison: {"usb": [...], "jack": [...], "gated_jack": [...], "compare": {...}, "clock": {...}}"""
    u, sr, j = tk["usb"], tk["usb_sr"], np.asarray(tk["jack"], float)
    q = np.round(u).astype(np.int64)
    fu = hc.clock_fit(q, events, sr, rate=tk["clock"].get("usb"))
    fj = hc.clock_fit(j, events, FS_J, rate=tk["clock"].get("jack"))
    cu, cj = (fu["lag"], fu["rate"]), (fj["lag"], fj["rate"])
    res = hc.analyse(q, j, sr, events, widen=WIDEN, ccs=ccs, clock_usb=cu, clock_jack=cj)
    gj = hc._guard_mask(len(j), FS_J, hc.guards(events, ccs, clock=cj, echo_rate=cj[1] - cu[1]), 0.0, WIDEN)
    res["jack"] = sorted(res["jack"] + [(i / FS_J, "hfclick", d) for i, d in det_hf_click(j, FS_J, gj)])
    lead = lead_dbfs(q, sr, events, cu)
    noise = jack_floor(j, events, cj, lead)
    res["jack"], dropped = track_noise_aware(res["jack"], j, noise)
    usb_ev = [usb_to_jack(t, cu, cj) for t, k, _ in res["usb"] if k in USB_KINDS]
    usb_hf = usb_hf_times(q, sr, cu, cj)
    res["jack"], music = split_hf(res["jack"], usb_ev, usb_hf)
    res["info"]["hfclick_usb_too"] = music
    res["info"]["usb_lead_dbfs"] = None if lead is None else round(lead, 1)
    res["info"]["jack_floor_dbfs"] = (None if noise is None else
                                      round(10 * np.log10(noise + 1e-9) - 20 * np.log10(32768), 1))
    res["info"]["track_under_floor"] = dropped
    out = {"usb": res["usb"], "jack": res["jack"], "info": res["info"],
           "gated_jack": [e for e in res["jack"] if e[1] in hc.GATED + ("hfclick",)],
           "gated_usb": [e for e in res["usb"] if e[1] in hc.GATED],
           "clock": {"usb": fu, "jack": fj}}
    out["compare"] = compare(q, j, sr, events, ccs, cu, cj, res, noise, usb_hf)
    return out


def usb_to_jack(tu, cu, cj):
    """a USB time (s) on the jack's clock (both through the event clock)"""
    return float(hc.map_clock((tu - cu[0]) / (1.0 + cu[1]), cj))


FIR_K = 32                                     # path_fir: taps -32 .. +32 (+-0.67 ms at 48 kHz)


def path_fir(y, x, K=FIR_K):
    """the least-squares FIR h (2K + 1 taps, centred) with y ~ x * h: the jack's analog path (AC coupling, the DAC's
    and the ADC's filters, a fraction of a sample left by the warp) from USB warped onto the jack's clock, so the
    residual holds what the jack adds (noise, distortion, a click), not the path's phase. Normal equations from the
    FFT correlations (solve_toeplitz)"""
    from scipy.linalg import solve_toeplitz
    from scipy.signal import correlate
    n = len(x)
    rxx = correlate(x, x, "full", method="fft")[n - 1:n + 2 * K]
    rxy = correlate(y, x, "full", method="fft")[n - 1 - K:n + K]
    if rxx[0] <= 0:
        return np.zeros(2 * K + 1)
    rxx = rxx.copy()
    rxx[0] *= 1.0 + 1e-6
    return solve_toeplitz(rxx, rxy)


ALIGN_TOL = 0.0015                             # (s) align: the lag searched this close to the clocks' line
ALIGN_WIDE = 0.03                              # (s) ... or this close, when too few windows agree within ALIGN_TOL
ALIGN_WIN, ALIGN_HOP = 4800, 0.25              # align: 100 ms windows every 250 ms


def align(q, j, sr, cu, cj, noise=None):
    """USB warped onto the jack's clock: (uw at 48 kHz, rows [(t_jack, lag frames, ncc)], L0, r, how) or None. The
    jack's frame a is USB's (at 48 kHz) a + L0 + r a. The line is the two recordings' clocks (clock_fit: each one's
    first sound and rate, mapped through the event clock), refined by the cross-correlation of 100 ms windows (the
    jack 10 dB over its own floor) searched only ALIGN_TOL around it, then a least-squares line through the windows
    that agree (2 rounds, within 2 frames). Why not hil_crackle.track's fit alone: it searches +-0.3 s, and on the
    proof's held chord (C3 G3 C4 E4: a common period of 15.3 ms) every window's best peak was one period off, so the
    whole take was aligned 14.7 ms late: the residual held each click twice (the jack's and USB's, a period apart)
    and flagged all six as on the jack only (the Move, 2026-11-04). The clocks' line is good to about a millisecond,
    under any period that matters"""
    from math import gcd
    from scipy.signal import resample_poly
    u = np.asarray(q, float)
    u = u[:, 0] if u.ndim == 2 else u
    g = gcd(4 * FS_J, int(sr))
    u4 = resample_poly(u, 4 * FS_J // g, int(sr) // g)
    u48 = u4[::4]
    j = np.asarray(j, float)
    n = min(len(u48), len(j))
    k = (1.0 + cu[1]) / (1.0 + cj[1])

    def prior(a):                                   # (USB's 48 kHz frame minus the jack's, on the clocks' line)
        return (cu[0] + (a / FS_J - cj[0]) * k) * FS_J - a
    thr = max(1.0, 10.0 * noise) if noise else 1.0
    for how, tol_s in (("clocks", ALIGN_TOL), ("wide", ALIGN_WIDE)):
        tol, pts = int(round(tol_s * FS_J)), []
        for a in range(int(0.05 * FS_J), n - ALIGN_WIN, int(ALIGN_HOP * FS_J)):
            if np.mean(j[a:a + ALIGN_WIN] ** 2) < thr:
                continue
            lp = int(round(prior(a)))
            lo, hi = lp - tol, lp + tol
            if a + lo < 0 or a + hi + ALIGN_WIN > len(u48):
                continue
            L, c = hc._lag(u48, j, a, ALIGN_WIN, lo, hi)
            if c >= 0.8 and lo < L < hi:
                pts.append((a, L, c))
        if len(pts) >= 3:
            break
    if len(pts) < 3:
        return None
    P = np.array(pts, float)
    keep = np.ones(len(P), bool)
    for _ in range(2):
        r, L0 = np.polyfit(P[keep, 0], P[keep, 1], 1) if keep.sum() >= 2 else (0.0, float(np.median(P[:, 1])))
        keep = np.abs(P[:, 1] - (L0 + r * P[:, 0])) <= 2.0
        if keep.sum() < 2:
            keep = np.ones(len(P), bool)
    pos = 4.0 * (np.arange(n) * (1.0 + r) + L0)
    uw = np.interp(pos, np.arange(len(u4)), u4, left=0.0, right=0.0)
    rows = [(a / FS_J, L, c) for a, L, c in pts]
    return uw, rows, float(L0), float(r), how


LEVEL_WIN, LEVEL_DB, LEVEL_SNR_DB = 960, 3.0, 20.0   # level_steps: 20 ms windows, 3 dB off, 20 dB over the floor


def level_steps(jj, yh, m, noise):
    """[(t s, detail)]: the jack's level against USB's through the path (yh) per 20 ms window (hop 10 ms): the local
    gain sum(j yh) / sum(yh^2) (unbiased by the jack's own floor, which USB does not share) over LEVEL_DB off 1, where
    USB's window is LEVEL_SNR_DB over the jack's floor; one event per run of such windows. A level step on the jack
    alone (its codec, the interface): the music's own steps are on USB too, and the ncc of hil_crackle.track does not
    see a gain"""
    out, run = [], None
    hop = LEVEL_WIN // 2
    fl = noise or 0.0
    for a in range(0, len(jj) - LEVEL_WIN, hop):
        if not m[a:a + LEVEL_WIN].all():
            continue
        y, x = yh[a:a + LEVEL_WIN], jj[a:a + LEVEL_WIN]
        py = float(np.mean(y * y))
        if py <= 0 or (fl and py < fl * 10 ** (LEVEL_SNR_DB / 10)) or py < 1.0:
            if run:
                out.append(run)
                run = None
            continue
        gdb = 20 * np.log10(max(abs(float(np.dot(x, y)) / (py * LEVEL_WIN)), 1e-6))
        if abs(gdb) > LEVEL_DB:
            run = [a, a + LEVEL_WIN, gdb] if run is None else [run[0], a + LEVEL_WIN, max(run[2], gdb, key=abs)]
        elif run:
            out.append(run)
            run = None
    if run:
        out.append(run)
    return [(round(a / FS_J, 4), "level %+.1f dB against USB for %.0f ms" % (g, 1000 * (b - a) / FS_J))
            for a, b, g in out]


def compare(q, j, sr, events, ccs, cu, cj, res, noise=None, usb_hf=np.zeros(0)):
    """(d): the jack against USB time-aligned (align: the clocks' line refined by the cross-correlation), the level
    difference (one gain, least squares), the residual after the analog path's own response (path_fir): overall and
    per octave re the jack (resid_gain_only_re_jack_db: after the gain alone), and the residual's clicks
    (det_hf_click) with no USB event near: on the jack only (jack_only)"""
    er = cj[1] - cu[1]
    gjt = hc.guards(events, ccs, clock=cj, echo_rate=er)
    al = align(q, j, sr, cu, cj, noise)
    if al is None:
        return {"ok": False, "why": "no alignment (too little sound in the take for the cross-correlation)",
                "jack_only": []}
    uw, rows, L0, r_line, how = al
    n = min(len(j), len(uw))
    jj, uw = j[:n], uw[:n]
    m = uw != 0
    den = float(np.dot(uw[m], uw[m]))
    g = float(np.dot(jj[m], uw[m]) / den) if den > 0 else 0.0
    r0 = np.where(m, jj - g * uw, 0.0)
    h = path_fir(jj, np.where(m, uw, 0.0))           # (the analog path's own response: AC coupling, the filters)
    yh = np.convolve(np.where(m, uw, 0.0), h)[FIR_K:FIR_K + n]   # (USB as the jack should have it)
    r = np.where(m, jj - yh, 0.0)
    m[:FIR_K] = m[n - FIR_K:] = False
    r[~m] = 0.0
    R = np.array([(t, lag) for t, lag, _ in rows])
    drift = r_line
    pj = float(np.sum(jj[m] ** 2)) + 1e-9
    w = np.hanning(n)
    sj, sr_ = np.abs(np.fft.rfft(np.where(m, jj, 0) * w)) ** 2, np.abs(np.fft.rfft(r * w)) ** 2
    fr = np.fft.rfftfreq(n, 1 / FS_J)
    bands = []
    for f0, f1 in zip(OCT_EDGES, OCT_EDGES[1:]):
        k = (fr >= f0) & (fr < f1)
        bands.append({"band_hz": [f0, f1], "jack_db": round(float(10 * np.log10(sj[k].sum() * 2 / (n ** 2 * 0.375)
                                                                                  / 32768 ** 2 + 1e-30)), 1),
                      "resid_re_jack_db": round(float(10 * np.log10((sr_[k].sum() + 1e-9) / (sj[k].sum() + 1e-9))), 1)})
    from scipy.ndimage import minimum_filter1d
    cover = minimum_filter1d(m.astype(np.uint8), 2 * int(0.05 * FS_J) + 1) > 0   # (det_hf_click's +-50 ms floor
    gm = hc._guard_mask(n, FS_J, gjt, 0.0, WIDEN) | ~cover     # inside the covered part only: the zeroed rest of
    #                                                            the residual put the floor at 1 LSB at its edge)
    cand = det_hf_click(r, FS_J, gm)
    tusb = [usb_to_jack(t, cu, cj) for t, k, _ in res["usb"] if k in USB_KINDS]
    jack_only, both = [], []
    for i, d in cand:
        t = i / FS_J
        (both if any(abs(t - x) <= MATCH_S for x in tusb) or near(usb_hf, t, HF_MATCH_S)
         else jack_only).append((round(t, 4), d))
    steps = level_steps(jj, yh, m, noise)
    jack_only = sorted(jack_only + steps)
    jcl = [t for t, k, _ in res["jack"] if k in JACK_CLICKS]
    return {"ok": True, "align": how, "lag_ms": round(float(np.median(R[:, 1])) / FS_J * 1000, 3),
            "drift_ppm": round(1e6 * drift, 1), "gain": round(g, 5),
            "jack_re_usb_db": round(float(20 * np.log10(abs(g) + 1e-12)), 2),
            "ncc_min": round(float(min(x for _, _, x in rows)), 4), "windows": len(rows),
            "resid_re_jack_db": round(float(10 * np.log10(float(np.sum(r ** 2)) / pj + 1e-30)), 1),
            "resid_gain_only_re_jack_db": round(float(10 * np.log10(float(np.sum(r0 ** 2)) / pj + 1e-30)), 1),
            "resid_dbfs": dbfs(r[m]) if m.any() else -300.0, "bands": bands,
            "resid_impulses": len(cand), "resid_impulses_usb_too": both, "jack_only": jack_only,
            "jack_clicks_in_resid": sum(1 for t in jcl if any(abs(t - x) <= 0.005 for x, _ in jack_only))}


# -------------------------------------------------------------------------------------------------------- the takes
def prep(J, engine):
    mh._prep(J.r, engine)


def proof(J, a, d, out=print):
    """the jack detector proven on this rig: a clean take, then PROVE_N real clicks (dev_fault) on a held pedalled
    chord. (ok, report)"""
    spec = "click %d %d %d" % (PROVE_N, PROVE_EVERY_MS, PROVE_LSB)
    ev = [(0.0, "cc", 64, 127)] + [(0.3, "on", n, 90) for n in PROVE_CHORD]   # (0.3 s of lead: the jack's floor)
    rep = {"spec": spec, "secs": PROVE_SECS, "chord": PROVE_CHORD}
    takes = {}
    J.fault_open(a.ws)
    try:
        _proof_takes(J, a, d, spec, ev, takes, out)
    finally:
        J.fault_close()
    return proof_verdict(rep, takes, out)


def _proof_takes(J, a, d, spec, ev, takes, out):
    for name in ("clean", "fault"):
        prep(J, "rhodes")
        if name == "fault":
            J.fault(spec, a.ws)
        try:
            tk = rec(J, PROVE_SECS, ev)
        finally:
            if name == "fault":
                J.fault("off", a.ws)
            for n in PROVE_CHORD:
                J.r._send("off", n, 0)
            J.r._send("cc", 64, 0)
        save(d, "proof-" + name, tk)
        res = judge(tk, ev)
        usb_cl = [t for t, k, _ in res["usb"] if k in ("click", "click-lim")]
        jack_cl = [t for t, k, _ in res["jack"] if k in JACK_CLICKS]
        cu = (res["clock"]["usb"]["lag"], res["clock"]["usb"]["rate"])
        cj = (res["clock"]["jack"]["lag"], res["clock"]["jack"]["rate"])
        matched = [t for t in usb_cl if any(abs(usb_to_jack(t, cu, cj) - x) <= MATCH_S for x in jack_cl)]
        takes[name] = {"usb_clicks": [round(t, 4) for t in usb_cl], "jack_clicks": [round(t, 4) for t in jack_cl],
                       "matched": len(matched), "gated_jack": res["gated_jack"], "gated_usb": res["gated_usb"],
                       "jack_only": res["compare"].get("jack_only", []), "compare_ok": res["compare"].get("ok"),
                       "jack_floor_dbfs": res["info"].get("jack_floor_dbfs"),
                       "track_under_floor": len(res["info"].get("track_under_floor", [])),
                       "xruns": tk["xruns"], "jack_peak_dbfs": round(peak_dbfs(tk["jack"]), 1),
                       "usb_peak_dbfs": round(peak_dbfs(tk["usb"]), 1)}
        out("proof %-5s USB clicks %d, jack clicks %d (%d matched), jack gated events %d" % (
            name, len(usb_cl), len(jack_cl), len(matched), len(res["gated_jack"])))


def proof_verdict(rep, takes, out=print):
    rep.update(takes)
    c, f = takes["clean"], takes["fault"]
    why = []
    if c["gated_jack"] or c["jack_clicks"]:
        why.append("the clean take gave %d jack event(s): the detector fires on clean audio" % len(c["gated_jack"]))
    for k, q in (("clean", c), ("fault", f)):
        if q["jack_only"]:
            why.append("the %s take: %d event(s) flagged on the jack only (its clicks are on USB too)" % (
                k, len(q["jack_only"])))
        if not q["compare_ok"]:
            why.append("the %s take: the jack could not be aligned on USB" % k)
    if len(f["usb_clicks"]) == 0:
        why.append("no fault reached the output (USB shows none of the %d clicks): a build without dev_fault "
                   "(boot.txt has no dev_fault=1) or the WebSocket key did not arrive" % PROVE_N)
    elif len(f["usb_clicks"]) < PROVE_N - 1:
        why.append("USB shows %d of the %d clicks" % (len(f["usb_clicks"]), PROVE_N))
    if f["usb_clicks"] and f["matched"] < len(f["usb_clicks"]):
        why.append("the jack's detector found %d of the %d clicks USB shows" % (f["matched"], len(f["usb_clicks"])))
    if c["xruns"] or f["xruns"]:
        why.append("recorder xruns: %s" % (c["xruns"] + f["xruns"])[:4])
    rep["verdict"] = "PROVEN" if not why else "NOT PROVEN: " + "; ".join(why)
    out("proof: " + rep["verdict"])
    return not why, rep


def save(d, name, tk):
    os.makedirs(d, exist_ok=True)
    np.savez_compressed(os.path.join(d, name + ".npz"), usb=np.clip(np.round(tk["usb"]), -32768, 32767).astype(np.int16),
                        usb_sr=tk["usb_sr"], jack=np.asarray(tk["jack"], np.float32), jack_sr=FS_J)


def engine_run(J, e, a, d, out=print):
    """(a) - (d) for one engine: (ok, report)"""
    rep = {"engine": e}
    # (a) silence: nothing playing
    prep(J, e)
    tk = rec(J, 1.5, [])
    rep["silence"] = {"jack_db": round(dbfs(tk["jack"]), 1), "jack_hf_db": round(band_db(tk["jack"], FS_J, 8000, 20000), 1),
                      "usb_db": round(dbfs(tk["usb"]), 1), "usb_nonzero": int(np.count_nonzero(np.round(tk["usb"])))}
    # (a) hiss: under a quiet low note held (MARIMBA: the pedal and RING 127 keep it sounding)
    ev = [(0.1, "on", 33, 30)]
    if e == "marimba":
        ev = [(0.0, "cc", 102, 127), (0.0, "cc", 64, 127)] + ev
    tk = rec(J, 2.5, ev)
    J.r._send("off", 33, 0)
    J.r._send("cc", 64, 0)
    su, sj = slice(int(0.6 * tk["usb_sr"]), int(2.4 * tk["usb_sr"])), slice(int(0.6 * FS_J), int(2.4 * FS_J))
    rep["hiss"] = {"usb_hf_db": round(band_db(tk["usb"][su, 0], tk["usb_sr"], 8000, 20000), 1),
                   "jack_hf_db": round(band_db(tk["jack"][sj], FS_J, 8000, 20000), 1),
                   "usb_db": round(dbfs(tk["usb"][su]), 1), "jack_db": round(dbfs(tk["jack"][sj]), 1)}
    # (b) a loud chord
    prep(J, e)
    ch = (36, 43, 48, 52, 55, 60, 64)
    ev = [(0.1, "on", n, 127) for n in ch] + [(1.6, "off", n, 0) for n in ch]
    tk = rec(J, 2.0, ev)
    save(os.path.join(d, e), "loud", tk)
    su, sj = slice(int(0.15 * tk["usb_sr"]), int(1.5 * tk["usb_sr"])), slice(int(0.15 * FS_J), int(1.5 * FS_J))
    rep["loud"] = {"usb_peak_db": round(peak_dbfs(tk["usb"]), 1), "usb_db": round(dbfs(tk["usb"][su]), 1),
                   "jack_peak_db": round(peak_dbfs(tk["jack"]), 1), "jack_db": round(dbfs(tk["jack"][sj]), 1)}
    gain = rep["loud"]["jack_db"] - rep["loud"]["usb_db"]
    rep["jack_re_usb_db"] = round(gain, 2)
    rep["snr_db"] = round(rep["loud"]["jack_db"] - rep["hiss"]["jack_hf_db"], 1)
    rep["hiss_added_db"] = round(rep["hiss"]["jack_hf_db"] - (rep["hiss"]["usb_hf_db"] + gain), 1)
    lj = judge(tk, ev)
    rep["loud"]["compare"] = lj["compare"]
    bad = []
    if rep["loud"]["jack_peak_db"] >= -0.1:
        bad.append("the jack's loud chord reaches the interface's full scale (lower input %d's gain)" % a.ch)
    # (c) + (d): usb-stress's worst takes, on the jack too
    rng = np.random.default_rng(a.seed)
    rep["worst"] = []
    for name, fx in (("low", dict(hc.HEAVY, LEVEL=0)), ("unity", hc.HEAVY)):
        ccs = hc.engine_ccs(e, fx)
        ev, dur = hc.worst_events(a.nv, a.secs / 2, rng)
        ev = sorted(ev, key=lambda x: x[0])
        for attempt in (1, 2):                    # (from silence: the last take's tail, the reverb's and the
            J.r._send("cc", 64, 0)                # delay's, ran into the next one at -5 dBFS, 2026-11-04)
            J.r._send("cc", 123, 0)
            J.r._send("cc", 120, 0)
            for c, v in ccs:
                J.r._send("cc", c, v)
            time.sleep(0.3)
            J.r._send("cc", hs.RESET_CC, 127)     # (the effects' lines emptied: fx_reset_state)
            J.r.wait_silence(10.0, LEAD_QUIET_DBFS - 10)
            tk = rec(J, dur, ev, worst=True, ccs=ccs)
            ld = lead_dbfs(tk["usb"], tk["usb_sr"], ev, (0.0, 0.0))
            if ld is None or ld <= LEAD_QUIET_DBFS:
                break
            out("  %s %s: USB %.1f dBFS before the first note (not silent): again" % (e, name, ld))
        J.r._send("cc", 64, 0)
        save(os.path.join(d, e), "worst-" + name, tk)
        res = judge(tk, ev, ccs)
        cmpr = res["compare"]
        t = {"take": name, "secs": round(dur, 1), "notes": sum(1 for x in ev if x[1] == "on"),
             "usb_peak_dbfs": round(peak_dbfs(tk["usb"]), 1), "jack_peak_dbfs": round(peak_dbfs(tk["jack"]), 1),
             "gated_jack": res["gated_jack"], "gated_usb": res["gated_usb"], "jack_events": res["jack"],
             "usb_events": res["usb"], "info": res["info"], "clock": res["clock"], "compare": cmpr,
             "xruns": tk["xruns"]}
        if "injected" in tk:
            t["injected"] = tk["injected"]
        rep["worst"].append(t)
        for tt, k, det in res["gated_jack"][:20]:
            out("  %-5s jack %8.3f s  %-9s %s" % (name, tt, k, det))
        for tt, det in cmpr.get("jack_only", [])[:20]:
            out("  %-5s jack only %8.3f s  %s" % (name, tt, det))
        out("%s %s: %d notes, %.0f s; jack %d gated event(s), USB %d (information); jack only %d; residual %s dB re "
            "the jack; lag %s ms, drift %s ppm, jack re USB %s dB" % (
                e, name, t["notes"], dur, len(res["gated_jack"]), len(res["gated_usb"]), len(cmpr.get("jack_only", [])),
                cmpr.get("resid_re_jack_db"), cmpr.get("lag_ms"), cmpr.get("drift_ppm"), cmpr.get("jack_re_usb_db")))
        if res["gated_jack"]:
            bad.append("%s: %d jack event(s)" % (name, len(res["gated_jack"])))
        if cmpr.get("jack_only"):
            bad.append("%s: %d event(s) on the jack only" % (name, len(cmpr["jack_only"])))
        if not cmpr.get("ok"):
            bad.append("%s: the jack against USB: %s" % (name, cmpr.get("why")))
        if tk["xruns"]:
            bad.append("%s: recorder xruns (RIG) %s" % (name, tk["xruns"][:3]))
        if ld is not None and ld > LEAD_QUIET_DBFS:
            bad.append("%s: the take did not start in silence (USB %.1f dBFS before its first note; RIG)" % (name, ld))
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("%s: (a) jack floor %.1f dB, hiss 8-20k jack %.1f / USB %.1f dB (added %+.1f); (b) loud USB peak %.1f rms %.1f, "
        "jack peak %.1f rms %.1f (jack re USB %+.1f dB); %s" % (
            e, rep["silence"]["jack_db"], rep["hiss"]["jack_hf_db"], rep["hiss"]["usb_hf_db"], rep["hiss_added_db"],
            rep["loud"]["usb_peak_db"], rep["loud"]["usb_db"], rep["loud"]["jack_peak_db"], rep["loud"]["jack_db"],
            gain, rep["verdict"]))
    return not bad, rep


def estimate_s(engines, secs):
    """the run's length (s): the preflight, the state read and the proof once (~60 s), then per engine the level takes
    with their resets (~30 s), the two worst takes and their analysis (~1.2 x secs)"""
    return 60 + len(engines) * (30 + 1.2 * secs)


# ------------------------------------------------------------------------------------------------------------ report
def summary_md(R):
    L = ["# Move jack against USB: %s" % R["verdict"], "",
         "%s; %s; jack: %s input %d; MIDI %s (channel %s), audio %s; Schwung WebSocket %s" % (
             R["time"], "DRY RUN (the host build; the jack SIMULATED)" if R["dry_run"] else "the Move", R["rig"]["jack_dev"],
             R["rig"]["jack_ch"], R["rig"]["midi"], R["rig"]["ch"], R["rig"]["audio"], R["ws"]), ""]
    if R.get("stopped"):
        L += ["**Stopped:** " + R["stopped"], ""]
    P = R.get("proof")
    L += ["## The proof", "",
          "Before any verdict the jack detector proves itself on this rig: a held pedalled Rhodes chord (%s, v90), "
          "%.0f s, recorded twice on USB and the jack. The first take is clean. Before the second, the plugin's dev key "
          "`dev_fault` is set over Schwung's WebSocket to `%s`: %d one-sample clicks of %d LSB, one every %d ms, put "
          "into what the plugin hands Schwung (after its trim), so they pass the Move's mix, its USB-C audio, its DAC "
          "and its jack as a real glitch of the device's would; the key is set to `off` after the take. Proven when "
          "the clean take gives 0 jack events, USB shows at least %d of the clicks (the fault reached the output) and "
          "the jack's click detectors (det_hf_click: bursts over 12 kHz, as a click arrives band-limited through the DAC; and hil_crackle's) find every click USB shows (matched within %d ms on the event "
          "clock), and neither take has an event flagged as on the jack only (the clicks are on USB too). The jack's "
          "own floor is measured in each take's lead (before the first note): hil_crackle's jack-vs-USB correlation "
          "check is judged against it (a window's ncc is capped at sqrt(1 - floor / level), so the gate is ncc^2 < "
          "0.97^2 - floor / level, and windows under 10 dB over the floor are not judged), and the jack is aligned "
          "on USB along the two recordings' clocks (within 1.5 ms), not on a held chord's period. A run whose proof "
          "fails stops there and gives no PASS. Each worst take starts from silence (pedal up, notes and sound off, "
          "the effects' lines emptied, USB under %d dBFS before its first note, or the take is a rig failure). A jack "
          "burst over 12 kHz with USB's own burst within %d ms and no USB glitch there is the music's (information: "
          "hfclick_usb_too); the limiter's click-lim on USB excuses nothing by itself." % (
              ", ".join(map(str, PROVE_CHORD)), PROVE_SECS, "click %d %d %d" % (PROVE_N, PROVE_EVERY_MS, PROVE_LSB),
              PROVE_N, PROVE_LSB, PROVE_EVERY_MS, PROVE_N - 1, int(MATCH_S * 1000), LEAD_QUIET_DBFS,
              int(HF_MATCH_S * 1000)), ""]
    if P:
        L += ["| Take | USB clicks | Jack clicks | Matched | Jack gated events | Jack only | Jack floor dBFS | "
              "Track windows under the floor |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for k in ("clean", "fault"):
            q = P.get(k) or {}
            L.append("| %s | %d | %d | %d | %d | %d | %s | %s |" % (
                k, len(q.get("usb_clicks", [])), len(q.get("jack_clicks", [])), q.get("matched", 0),
                len(q.get("gated_jack", [])), len(q.get("jack_only", [])), q.get("jack_floor_dbfs"),
                q.get("track_under_floor", 0)))
        L += ["", "**%s**" % P["verdict"], ""]
    if R["engines"]:
        L += ["## Per engine", "",
              "| Engine | Jack floor dB | Hiss 8-20k jack / USB dB (added) | Loud USB peak / RMS dBFS | Loud jack peak / "
              "RMS dB | Jack re USB dB | Worst: jack events | Jack only | Residual re jack dB | Result |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for E in R["engines"]:
            w = E.get("worst", [])
            L.append("| %s | %s | %s / %s (%+.1f) | %s / %s | %s / %s | %+.2f | %s | %s | %s | %s |" % (
                E["engine"], E["silence"]["jack_db"], E["hiss"]["jack_hf_db"], E["hiss"]["usb_hf_db"],
                E["hiss_added_db"], E["loud"]["usb_peak_db"], E["loud"]["usb_db"], E["loud"]["jack_peak_db"],
                E["loud"]["jack_db"], E["jack_re_usb_db"], " + ".join(str(len(t["gated_jack"])) for t in w),
                " + ".join(str(len(t["compare"].get("jack_only", []))) for t in w),
                " / ".join(str(t["compare"].get("resid_re_jack_db")) for t in w),
                E["verdict"].replace("|", "/")[:200]))
        L += ["", "Worst takes: low (LEVEL -12 dB) + unity. The jack's figures are re the interface's full scale "
                  "(its input gain, the Move's volume); the differences matter.", ""]
        for E in R["engines"]:
            for t in E.get("worst", []):
                c = t["compare"]
                if not c.get("ok"):
                    continue
                L.append("- %s %s: lag %s ms, drift %s ppm, jack re USB %s dB, ncc min %s; residual per octave re the "
                         "jack: %s" % (E["engine"], t["take"], c["lag_ms"], c["drift_ppm"], c["jack_re_usb_db"],
                                       c["ncc_min"], ", ".join("%d-%d Hz %s" % (b["band_hz"][0], b["band_hz"][1],
                                                                                 b["resid_re_jack_db"])
                                                               for b in c["bands"])))
        L.append("")
    for E in R.get("crashed", []):
        L.append("- %s: %s" % (E["engine"], E["verdict"]))
    L += ["Takes (npz: USB LSB at its rate, the jack LSB at 48 kHz) and summary.json: %s" % R["dir"]]
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------------------------------------------------- main
def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--secs", type=float, default=60.0, help="the worst stimulus per engine (two takes of secs / 2)")
    ap.add_argument("--nv", type=int, default=mh.MOVE_VOICES, help="the worst takes' voices per chord")
    ap.add_argument("--dev", default="M6", help="the jack's audio interface (hil_jack's)")
    ap.add_argument("--ch", type=int, default=5, help="the interface's input the jack is on (1-based)")
    ap.add_argument("--midi", help="the MIDI output (a substring; default the Move's Standalone port)")
    ap.add_argument("--audio", help="the Move's audio input (a substring; default the Move's)")
    ap.add_argument("--midi-ch", type=int, default=None, help="the MIDI channel (default: slot 1's, read over Schwung)")
    ap.add_argument("--ws", default=os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui"))
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--prove", action="store_true", help="the proof only")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dry-inject", action="append", default=[], help="--dry-run: jclick@S or click@S (the worst takes)")
    a = ap.parse_args(argv)
    a.engine_list = tuple(x for x in a.engines.split(",") if x)
    bad = [x for x in a.engine_list if x not in ENGINES]
    if bad:
        ap.error("--engines: %s" % bad)
    if a.dry_inject and not a.dry_run:
        ap.error("--dry-inject needs --dry-run")
    if a.midi and any(b in a.midi.lower() for b in NEVER_MIDI):
        ap.error("--midi %s: never that port (the Move's Standalone port only)" % a.midi)
    return a


def mh_args(a):
    """the namespace move_hil's channel_for / preflight_cmd read"""
    return argparse.Namespace(fake=a.dry_run, inject=None, midi=a.midi, audio=a.audio, ws=a.ws, host=a.host,
                              ch=a.midi_ch)


def main(argv=None):
    a = parse(argv)
    import contextlib
    with (contextlib.nullcontext() if a.dry_run else mi.move_lock("move_jack")):
        return run(a)


def run(a, out=print):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = a.out or os.path.join(mh.OUT, *(["dry"] if a.dry_run else []), stamp + "-jack")
    os.makedirs(d, exist_ok=True)
    engines = () if a.prove else a.engine_list
    R = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": bool(a.dry_run), "dir": d, "ws": a.ws,
         "engines": [], "proof": None, "stopped": None, "estimate_s": estimate_s(engines, a.secs)}
    ma = mh_args(a)
    chan, srep = mh.channel_for(ma, out=out)
    if chan.get("stop"):
        R["stopped"] = chan["stop"]
    J, ks = None, None
    try:
        if not R["stopped"]:
            if a.dry_run:
                inj = [(k, float(t or 0)) for k, _, t in (s.partition("@") for s in a.dry_inject)]
                bad = [k for k, _ in inj if k not in ("jclick", "click", "jdrop", "jstep")]
                if bad:
                    raise SystemExit("--dry-inject: %s (jclick, click, jdrop, jstep)" % bad)
                J = DryJack(seed=a.seed + 1, jinject=inj)
            else:
                if a.ws == "none":
                    raise SystemExit("the proof sets dev_fault over Schwung's WebSocket: --ws none cannot be proven")
                rig = mh.UsbRig(a.midi, a.audio, chan["send"])      # (its MIDI port first, then the audio streams)
                if "standalone" not in rig.port_name.lower() or any(b in rig.port_name.lower() for b in NEVER_MIDI):
                    raise SystemExit("MIDI %s: not the Move's Standalone port" % rig.port_name)
                J = MoveJack(rig, a.dev, a.ch)
                ks = mh.KeptState(a.ws, 0).__enter__()
            R["rig"] = {"midi": J.r.port_name, "audio": J.r.audio_name, "ch": J.r.ch, "jack_dev": a.dev,
                        "jack_ch": a.ch, "fake": J.fake}
            out("MIDI %s (channel %d), audio %s, jack %s input %d; about %.0f min; -> %s" % (
                J.r.port_name, J.r.ch, J.r.audio_name, a.dev, a.ch, R["estimate_s"] / 60, d), flush=True)
            if not a.no_preflight:
                ok, pre = mh.preflight_cmd(ma, os.path.join(d, "preflight"), chan, srep, u=None if J.fake else J.r)
                R["preflight"] = mh._plain(pre)
                if not ok:
                    R["stopped"] = "the preflight failed: %s" % pre.get("verdict", "")
        if not R["stopped"]:
            ok, P = proof(J, a, os.path.join(d, "proof"), out=out)
            R["proof"] = P
            if not ok:
                R["stopped"] = "the proof failed (%s): no verdict" % P["verdict"]
        if not R["stopped"]:
            for e in engines:
                out("== %s" % e)
                try:
                    ok, E = engine_run(J, e, a, d, out=out)
                except Exception as x:                    # (one engine's crash is its FAIL)
                    E = {"engine": e, "verdict": "FAIL: %s: %s" % (type(x).__name__, x), "crash": True}
                R["engines"].append(E)
    finally:
        if J is not None and not J.fake:
            try:
                J.fault("off", a.ws)
            except Exception as x:
                out("dev_fault off failed: %s" % x)
        if ks is not None:
            ks.__exit__(None, None, None)
            R["state"] = ks.report
    if J is None:
        R.setdefault("rig", {"midi": "-", "audio": "-", "ch": "-", "jack_dev": a.dev, "jack_ch": a.ch, "fake": a.dry_run})
    crashed = [E for E in R["engines"] if E.get("crash")]
    ok = all(E["verdict"] == "PASS" for E in R["engines"])
    if R["stopped"]:
        R["verdict"], code = "STOPPED", 2
    elif R.get("state") and R["state"]["verdict"] != "PASS":
        R["verdict"], code = "FAIL (the plugin's state not restored: %s)" % R["state"]["verdict"], 1
    elif a.prove:
        R["verdict"], code = "PROVEN", 0
    else:
        R["verdict"], code = ("PASS", 0) if ok and not crashed else ("FAIL", 1)
    if a.dry_run:
        R["verdict"] += " (DRY RUN)"
    R = mh._plain(R)
    json.dump(R, open(os.path.join(d, "summary.json"), "w"), indent=1, default=str)
    md = summary_md({**R, "engines": [E for E in R["engines"] if not E.get("crash")],
                     "crashed": [E for E in R["engines"] if E.get("crash")]})
    open(os.path.join(d, "summary.md"), "w").write(md)
    out(md)
    return code


if __name__ == "__main__":
    sys.exit(main())
