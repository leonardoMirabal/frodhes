#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move's hardware-in-the-loop driver (M1-18), the offline skeleton: every subcommand plans its device commands
and prints them (DRY RUN by default; --go runs the ones that are implemented). The detectors are the FM-1's
(tools/hil_crackle.py) with the Move's timing; the null test is tools/device_null.py.

    tools/move_hil.py [--go] facts           the device facts (kernel, CPU, RAM, glibc, the Schwung version)
    tools/move_hil.py [--go] install|rollback|info [--src DIR]   tools/move_install.py
    tools/move_hil.py [--go] logs            the module's logs (its folder, /tmp) -> build/move/hil/<time>/
    tools/move_hil.py [--go] cpu [--file F]  the dev build's per-callback timer ring: pulled, or F read; the worst
                                             callback against U12 (working limit 725 us, design 600 us)
    tools/move_hil.py crackle [--engine E] [--analyze WAV]   the takes (hil_crackle's stimulus) and their minutes;
                                             --analyze: the stale / zero / clip detectors on a recording
    tools/move_hil.py must                   MT1, the Move's must-pass (smoke, CPU, crackle, null): the plan; running
                                             it needs the glue and D1's answers (the MIDI path, the ring's location)
    tools/move_hil.py null [--self-test]     tools/device_null.py's takes / proof

Over USB-C, as the FM-1 is tested (hil_scan / hil_cpu / hil_jack): MIDI out to the Move's "Standalone" port (the
only one of its four USB-C MIDI ports that reaches a Schwung slot: cable 2, Schwung docs/SYSEX.md), the Move's USB
audio in (Core Audio), the FM-1's analysis (onsets, levels, FFT, the crackle detectors). Each run writes its takes
(WAV, at the device's own rate) and report.json to build/move/hil/<time>-<cmd>/. Exit 0 PASS, 1 FAIL, 2 no device.

    tools/move_hil.py usb-devices            the MIDI outputs and audio inputs; the ones picked are marked
    tools/move_hil.py usb-note [--note 60 --vel 100]   one note: level, pitch (cents), onset, release, glitches
    tools/move_hil.py usb-vel [--note 60]    velocity sweep 1..127: levels rise, brightness, the range in dB
    tools/move_hil.py usb-sus [--note 60]    CC 64: a damped note dies, a pedalled one rings, the pedal let up damps
                                             it, never louder after key-up (the FM-1's sustain preflight)
    tools/move_hil.py usb-clock [--bpm 120]  MIDI clock (Start + F8) with the arpeggiator at 1/8: the steps' period
    tools/move_hil.py usb-stress [--engine rhodes --secs 60 --nv 16 --bpm 0]   the worst case: every voice, every
                                             effect heavy, the pedal (hil_crackle's worst take); stale / repeat /
                                             zero / clip / click; then read the CPU ring with `--go cpu`
    tools/move_hil.py d2 [--engines rhodes,vibes,marimba --secs 60 --ws URL|none]   the whole run, per engine: slot
                                             1's module checked and its preset set over Schwung's WebSocket
                                             (tools/move_ws.py; F01 / F13 / F15), preflight (a failure stops the
                                             run), usb-host, usb-note on 3 keys, usb-vel, usb-sus, usb-clock,
                                             usb-stress; the table in build/move/hil/<time>-d2/summary.md
  --path tap (usb-*, d2): no USB-C at all: each take goes to the plugin's own player over Schwung's WebSocket
  (dev_play, move/move_bridge.h: never on cable 2, so no Move track plays along) and comes back as the plugin's own
  output (the tap file, fetched over ssh): free of the Move's mix, volumes and USB-C setting; usb-host then compares
  sample for sample (the take starts on the host build's block grid).
  --midi NAME, --audio NAME (substrings, else the Move's), --ch N (default 1: the slot on track 1), --fake: an
  offline model of the device in place of the Move (tests/py/test_move_hil.py), --inject KIND@S (fake only).
  On the Move first: Settings > USB-C audio out = Main Out (the Move resets it to Mic at every boot; Main Out mutes
  its speakers), Frodhes in slot 1, the Move's volume where it stays for the run.

Never run from a test or a gate without the user's OK to use the Move (the device rules); --host USER@HOST (default
$MOVE_HOST, else ableton@move.local)."""
import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_install as mi  # noqa: E402
import hil_scan as hs  # noqa: E402
import numpy as np  # noqa: E402

OUT = os.path.join(ROOT, "build", "move", "hil")
FS = 44100
BLOCK = 128                                  # the Move's callback: 128 frames (2902 us)
BLOCK_US = BLOCK * 1e6 / FS
U12_LIMIT_US = 725                           # plan 05 U12: the working limit (a quarter of the block); D1 sets it
U12_DESIGN_US = 600
FACTS = ("uname -a; echo; cat /etc/os-release; echo; ldd --version | head -1; echo; free -m; echo; nproc; "
         "grep -E 'model name|Hardware|CPU part' /proc/cpuinfo | sort | uniq -c; echo; "
         "cat /data/UserData/schwung/VERSION 2>/dev/null; df -h /data")
# the dev ring as the worker logs it (to agree with agent A's move_bridge.h when it lands): one line per callback,
# "<callback index> <us in render_block> [<frames rendered this call>]"; lines starting with # are comments
RING_LINE = re.compile(r"^\s*(\d+)\s+(\d+)(?:\s+(\d+))?\s*$")
MT1 = [("smoke: install, load, a note per engine, the TYPE pages", 5),
       ("cpu: 10 and 16 voices per engine, FX off / on, the ring", 10),
       ("crackle: the worst and the recipe takes per engine (line out + the ring)", 15),
       ("null: the device null set (device_null.py) on the ring and the line out", 10)]


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def facts(t, st=None):
    d = os.path.join(OUT, st or stamp())
    t.out("+ mkdir -p " + d)
    if t.go:
        os.makedirs(d, exist_ok=True)
    code, out = t.run(FACTS)
    if t.go and not code:
        open(os.path.join(d, "facts.txt"), "w").write(out)
    return d


def logs(t, st=None):
    d = os.path.join(OUT, st or stamp())
    if t.go:
        os.makedirs(d, exist_ok=True)
    src = t.mods + "/" + mi.MODULE_ID
    if t.local:
        t._exec(["sh", "-c", "cp %s/*.log %s/ 2>/dev/null || true" % (mi.q(src), mi.q(d))])
    else:
        t._exec(["scp", *mi.SSH_OPTS, "-q", "%s:%s/*.log" % (t.host, src), d + "/"])
        t._exec(["scp", *mi.SSH_OPTS, "-q", "%s:/tmp/frodhes*.log" % t.host, d + "/"])
    return d


def parse_ring(text):
    """[(callback, us, frames)] from the ring's log"""
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("#") or not ln.strip():
            continue
        m = RING_LINE.match(ln)
        if not m:
            raise ValueError("ring log: %r" % ln)
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
    return out


def cpu_summary(rows, limit=U12_LIMIT_US):
    """{n, worst, p99, over, missing, verdict}: over = callbacks above the limit; missing = gaps in the callback
    index (the ring overran: the worker fell behind, the data is incomplete)"""
    if not rows:
        return {"n": 0, "verdict": "no data"}
    us = sorted(r[1] for r in rows)
    idx = [r[0] for r in rows]
    missing = sum(max(0, b - a - 1) for a, b in zip(idx, idx[1:]))
    over = sum(1 for u in us if u > limit)
    s = {"n": len(us), "worst": us[-1], "p99": us[min(len(us) - 1, int(0.99 * len(us)))], "over": over,
         "missing": missing, "limit": limit, "block_us": round(BLOCK_US)}
    s["verdict"] = "FAIL" if over or missing else ("PASS (over the design target %d us)" % U12_DESIGN_US
                                                   if s["worst"] > U12_DESIGN_US else "PASS")
    return s


def cpu(t, path=None):
    if path:
        s = cpu_summary(parse_ring(open(path).read()))
    else:
        code, out = t.run("cat %s/%s/cpu-ring.log" % (t.mods, mi.MODULE_ID))
        if not t.go:
            return None
        s = cpu_summary(parse_ring(out))
    t.out(" ".join("%s=%s" % kv for kv in s.items()))
    return s


def crackle_plan(engine="all", out=print):
    import hil_crackle as hc
    total = 0.0
    for e in (hc.ENGINES if engine == "all" else (engine,)):
        for name, _, events, dur in hc.takes(e, nv=16):
            out("%-24s %5.1f s  %5d events  (16 voices; the line out + the ring)" % (name, dur, len(events)))
            total += dur
    out("%.1f min of takes; blocks of %d frames (%.0f us)" % (total / 60, BLOCK, BLOCK_US))
    return total


def crackle_analyze(path, out=print):
    """the detectors that need no USB reference: stale repeats, dropouts to zero, clipping (hil_crackle's)"""
    import numpy as np
    import wave
    import hil_crackle as hc
    w = wave.open(path)
    fs, ch = w.getframerate(), w.getnchannels()
    q = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.int64).reshape(-1, ch)
    if ch == 1:
        q = np.repeat(q, 2, axis=1)
    ev = [("stale", f, d) for f, d in hc.det_stale(q)]
    ev += [("zero", f, d) for f, d in hc.det_zero(q.astype(float), fs, exact=True)]
    ev += [("clip", f, d) for f, d in hc.det_clip(q.astype(float), 32767)]
    for kind, f, d in sorted(ev, key=lambda e: e[1]):
        out("%-6s %8.3f s  %s" % (kind, f / fs, d))
    out("%d event(s)" % len(ev))
    return ev


def must(out=print):
    for name, minutes in MT1:
        out("%3d min  %s" % (minutes, name))
    out("%d min in all. Not runnable yet: needs the glue (move/move_plugin.c) and D1's answers" % sum(m for _, m in MT1))
    return 2


# --------------------------------------------------------------------------- over USB-C (the FM-1's way of testing)
MIDI_KEYS = ("move", "standalone")          # "Ableton Move Standalone Port": the one that reaches the slots (cable 2)
AUDIO_KEYS = ("move",)
USB_CH = 1                                  # the slot on track 1 (Schwung's slot receive channel 1-4, default)
MOVE_VOICES = 16                            # Makefile MOVE_DEFS -DFR_VOICES=16u
FAKE_SR = 44100
FAKE_LAT = 0.012                            # (s) the fake device's MIDI + USB latency


NEVER_PORTS = ("midisport", "af16rig", "iac")   # the rig's other MIDI ports: never driven by the device tools


def _pick(names, keys, what, want=None):
    """the first name holding every key (case-insensitive), or the one holding want; None if there is none; never a
    port of NEVER_PORTS. MIDI: only the Standalone port (the Move's Live / User / External ports never reach a slot)"""
    low = [(n, n.lower()) for n in names if not any(b in n.lower() for b in NEVER_PORTS)]
    if want:
        hit = [n for n, l in low if want.lower() in l]
    else:
        hit = [n for n, l in low if all(k in l for k in keys)]
    return hit[0] if hit else None


def note_hz(n, a4=440.0):
    return a4 * 2 ** ((n - 69) / 12)


class UsbRig(hs.RigBase):
    """the Move over USB-C: a mido output on ch (1-based) and the Move's USB audio input (hil_scan's persistent
    recorder, Capture). rec(dur, events) plays the events on time while recording: {"x": (n, 2) LSB float at "sr"
    (the device's own rate), "sr", "xruns"}. Kinds: on / off / cc / pc, rt (a realtime byte: 0xF8, 0xFA, 0xFC)"""
    fake = False

    def __init__(self, midi=None, audio=None, ch=USB_CH):
        import mido
        import sounddevice as sd
        self.mido, self.sd, self.ch = mido, sd, int(ch)
        outs = mido.get_output_names()
        name = _pick(outs, MIDI_KEYS, "MIDI", midi)
        if not name:
            raise SystemExit("no Move MIDI output (have: %s)" % ", ".join(outs))
        self.port_name, self.port = name, mido.open_output(name)
        devs = sd.query_devices()
        ins = [d["name"] for d in devs if d["max_input_channels"] >= 2]
        an = _pick(ins, AUDIO_KEYS, "audio", audio)
        if not an:
            raise SystemExit("no Move audio input (have: %s)" % ", ".join(ins))
        self.audio_name = an
        self.dev = [i for i, d in enumerate(devs) if d["name"] == an and d["max_input_channels"] >= 2][0]
        self.sr = int(devs[self.dev]["default_samplerate"])
        self.cap = None

    def _send(self, kind, a, b=0):
        M, c = self.mido.Message, self.ch - 1
        if kind == "rt":
            self.port.send(M.from_bytes([int(a)]))
            return
        m = (M("note_on", channel=c, note=a, velocity=b) if kind == "on" else
             M("note_off", channel=c, note=a, velocity=b) if kind == "off" else
             M("program_change", channel=c, program=a) if kind == "pc" else
             M("control_change", channel=c, control=a, value=b))
        self.port.send(m)
        if kind == "cc":
            time.sleep(0.001)                  # (a CC burst paced: the Move forwards cable 2 to the track)

    def capture(self):
        if self.cap is None:
            import atexit
            self.cap = hs.Capture(self.sd)
            self.cap.add("usb", self.dev, (0, 1), 2, sr=self.sr)
            atexit.register(self.cap.close)
        return self.cap

    def level_now(self, secs=0.3):
        cap = self.capture()
        return hs.db(cap.cut("usb", cap.now()["usb"], secs) * 32768)

    def wait_silence(self, limit=10.0, floor_db=-80.0):
        """the Move's mix is not the FM-1's gated zero: quiet = under floor_db"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit:
            if self.level_now() < floor_db:
                return True
        return False

    def rec(self, dur, events):
        cap = self.capture()
        st = cap.now()["usb"]
        t0 = time.monotonic()
        for t, kind, a, b in sorted(events, key=lambda e: e[0]):
            while time.monotonic() - t0 < t:
                time.sleep(0.0003)
            self._send(kind, a, b)
        r = cap.rings["usb"]
        x = cap.cut("usb", st, dur).astype(float) * 32768
        xr = [f for f in r.flags_between(st, st + dur)] + ["gap %+.0f ms" % (1000 * g) for _, g in r.gaps]
        return {"x": x, "sr": r.sr, "xruns": xr}

    def info(self):
        return {"midi": self.port_name, "audio": self.audio_name, "sr": self.sr, "ch": self.ch}


class FakeRig(UsbRig):
    """an offline model of the Move for the tests: every message logged; rec() renders a decaying two-partial tone per
    note (level by velocity, the dampers when neither key nor pedal (CC 64 >= 64) holds it), FAKE_LAT late; the
    arpeggiator (CC 52 > 0) retriggers the held notes every CC 53 step of MIDI clock while Start ran. inject:
    [(kind, t s)], kind zero (2 ms of zeros), click (one sample), stale (one 128-frame callback twice), loud (the
    note gets louder after key-up), nosus (the pedal ignored), playing (a tone at idle: the preflight fails)"""
    fake = True

    def __init__(self, inject=(), sr=FAKE_SR, seed=0):
        self.ch, self.sr, self.inject, self.log = USB_CH, sr, list(inject), []
        self.rng = np.random.default_rng(seed)
        self.mido, self.port_name, self.audio_name = hs._Mido, "Fake Move Standalone Port", "Fake Move"
        self.state = {}                          # CC -> value (the panel, kept across takes)

    def _send(self, kind, a, b=0):
        self.log.append((kind, a, b))
        if kind == "cc":
            self.state[a] = b

    def level_now(self, secs=0.3):
        return -240.0

    def rec(self, dur, events):
        sr, n = self.sr, int(dur * self.sr)
        ev = sorted(events, key=lambda e: e[0])
        for _, kind, a, b in ev:
            self.log.append((kind, a, b))
        nosus = any(k == "nosus" for k, _ in self.inject)
        if not events and any(k == "playing" for k, _ in self.inject):     # (the preflight's idle: something plays)
            t = np.arange(n) / sr
            x = 3000 * np.sin(2 * np.pi * 262 * t)
            return {"x": np.round(np.stack([x, 0.7 * x], 1)), "sr": sr, "xruns": []}
        ped = [(0.0, self.state.get(64, 0) >= 64)] + [(t, b >= 64) for t, k, a, b in ev if k == "cc" and a == 64]
        if nosus:
            ped = [(0.0, False)]
        arp = self.state.get(52, 0) or next((b for t, k, a, b in ev if k == "cc" and a == 52), 0)
        rate = self.state.get(53, 3)
        units = (96, 48, 32, 24, 16, 12)[min(5, rate)] // 4          # F8 per arp step (24 PPQN)
        for t, k, a, b in ev:
            if k == "cc":
                self.state[a] = b
        # each note: (t_on, t_off, note, vel); the arp splits a held note into steps on the clock
        notes, held = [], {}
        for t, k, a, b in ev:
            if k == "on" and b:
                held[a] = (t, b)
            elif k in ("on", "off") and a in held:
                t1, v = held.pop(a)
                notes.append((t1, t, a, v))
        notes += [(t1, dur, a, v) for a, (t1, v) in held.items()]
        if arp:
            f8, run, c = [], False, 0
            for t, k, a, b in ev:
                if k == "rt" and a == 0xFA:
                    run, c = True, 0
                elif k == "rt" and a == 0xFC:
                    run = False
                elif k == "rt" and a == 0xF8 and run:
                    if c % units == 0:
                        f8.append(t)
                    c += 1
            split = []
            for t1, t2, a, v in notes:
                st = [s for s in f8 if t1 <= s < t2]
                split += [(s, min(s + 0.6 * units / 24 * 0.5, t2), a, v) for s in st]
            notes = split

        def pedal_at(t):
            return [p for s, p in ped if s <= t][-1]
        tt = np.arange(n) / sr
        x = np.zeros(n)
        loud = [t for k, t in self.inject if k == "loud"]
        for t1, t2, a, v in notes:
            i0 = int((t1 + FAKE_LAT) * sr)
            if i0 >= n:
                continue
            amp = 300 + 9000 * (v / 127) ** 2
            # the envelope piecewise: tau 2 s while the key or the pedal holds it, 0.05 s when damped (MARIMBA, CC 79
            # over 63: 0.01 s, the hand's mute)
            cuts = sorted({t2} | {s for s, _ in ped if s > t2})
            env = np.ones(n - i0)
            ts = tt[i0:] - FAKE_LAT
            lvl, last = 1.0, t1
            seg_ts = [t1] + [c for c in cuts if c < dur] + [dur + 1]
            for s0, s1 in zip(seg_ts, seg_ts[1:]):
                tau = 2.0 if (s0 < t2 or pedal_at(s0)) else (0.01 if self.state.get(79, 0) >= 64 else 0.05)
                m = (ts >= s0) & (ts < s1)
                env[m] = lvl * np.exp(-(ts[m] - s0) / tau)
                lvl = lvl * np.exp(-(s1 - s0) / tau)
                last = s1
            if any(t1 < lt for lt in loud) and loud:
                env[ts >= t2] *= 2.0
            att = np.minimum(1.0, (ts - t1) / 0.002)
            f = note_hz(a)
            x[i0:] += amp * env * att * (np.sin(2 * np.pi * f * (ts - t1)) + 0.3 * np.sin(4 * np.pi * f * (ts - t1)))
        x *= 0.25 if self.state.get(110, 64) == 0 else 1.0                # LEVEL (CC 110 0: -12 dB)
        x = 24000 * np.tanh(x / 24000) + self.rng.normal(0, 2.0, n)   # (the app's limiter, softly)
        q = np.round(np.stack([x, 0.9 * x], 1))
        for k, t in self.inject:
            i = int(t * sr)
            if k == "zero":
                q[i:i + int(0.002 * sr)] = 0
            elif k == "click":
                q[i, :] += 6000
            elif k == "stale":
                q[i:i + BLOCK] = q[i - BLOCK:i]
        return {"x": np.clip(q, -32768, 32767), "sr": sr, "xruns": []}


TAP_PRE_MS = 500                            # the tap path: the messages sent before a take (the panel) at 1 ms and a
                                            # program change at 5 ms, the take's own from 500 ms (host_phrase's layout)
TAP_DIR = "/data/UserData/schwung/module_data/" + mi.MODULE_ID + "/tap"
TAP_GRID_S = 2.56                           # the plugin starts a take on its 2560 ms grid (move_bridge.h MB_GRID_MS)


def to_status(kind, a, b, ch):
    c = (ch - 1) & 15
    if kind == "rt":
        return (int(a), 0, 0)
    return {"on": (0x90 | c, a, b), "off": (0x80 | c, a, b), "cc": (0xB0 | c, a, b), "pc": (0xC0 | c, a, 0)}[kind]


def scp_fetch(host, local_dir):
    """the tap's files from the Move (ssh / scp; the main session's): fetch(take) -> (wav path, json dict) or None"""
    import json
    import subprocess

    def fetch(take):
        os.makedirs(local_dir, exist_ok=True)
        js = os.path.join(local_dir, take + ".json")
        r = subprocess.run(["scp", *mi.SSH_OPTS, "-q", "%s:%s/%s.json" % (host, TAP_DIR, take), js], capture_output=True)
        if r.returncode:
            return None
        wav = os.path.join(local_dir, take + ".wav")
        r = subprocess.run(["scp", *mi.SSH_OPTS, "-q", "%s:%s/%s.wav" % (host, TAP_DIR, take), wav], capture_output=True)
        if r.returncode:
            return None
        subprocess.run(["ssh", *mi.SSH_OPTS, host, "rm -f %s/%s.wav %s/%s.json" % (TAP_DIR, take, TAP_DIR, take)],
                       capture_output=True)
        return wav, json.load(open(js))
    return fetch


def scp_cpu(host):
    """the plugin's timer ring (cpu-ring.log in its module folder, written while dev_cpu_log is 1) from the Move:
    cpu() -> its text ("" when there is none), the file deleted there"""
    import subprocess
    path = "%s/%s/cpu-ring.log" % (mi.MODS, mi.MODULE_ID)

    def cpu():
        r = subprocess.run(["ssh", *mi.SSH_OPTS, host, "cat %s 2>/dev/null; rm -f %s" % (path, path)],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    return cpu


class TapRig(UsbRig):
    """the Move without its mix: every take goes to the plugin's own player over Schwung's WebSocket (dev_play: no
    MIDI on cable 2, so no Move track plays along) and comes back as the plugin's own output (the tap file: before
    the slot's volume, the Move's mix, its master and its USB-C setting). What the tests send before a take (the
    panel's CCs, a program change) leads the take, as host_phrase's do; rec() returns the take from TAP_PRE_MS on.
    fetch(take) -> (wav, json) | None (scp_fetch; a test passes its own). cpu() -> the timer ring's text, the
    file then deleted (scp_cpu; None: no CPU read)"""
    fake = False

    def __init__(self, url, fetch, ch=USB_CH, slot=0, wait=10.0, cpu=None):
        import move_ws
        self.remote, self.fetch, self.ch, self.slot, self.wait = move_ws.Remote(url), fetch, int(ch), slot, wait
        self.cpu_fetch = cpu
        self.port_name, self.audio_name, self.sr = "Schwung %s, slot %d, dev_play" % (url, slot + 1), "the plugin's tap", FS
        self.pending, self.n, self.mido = [], 0, hs._Mido
        self.stamp = time.strftime("%H%M%S")
        self.stop()                              # (a take left armed by an earlier run would refuse the next)

    def stop(self):
        self.remote.set_param(self.slot, "synth:dev_play", "stop")
        self.remote.pump(0.5)

    def _send(self, kind, a, b=0):
        self.pending.append((kind, int(a), int(b)))

    def set_hq(self, on):
        """the HQ Rhodes (the bridge's p_hq): new notes play HQ (1) or Classic (0)"""
        self.remote.set_param(self.slot, "synth:p_hq", 1 if on else 0)
        self.remote.pump(0.2)

    def cpu_start(self):
        """the plugin logs every callback's time from now (dev_cpu_log 1); an older log is dropped first"""
        self.cpu_fetch()
        self.remote.set_param(self.slot, "synth:dev_cpu_log", 1)
        self.remote.pump(0.2)

    def cpu_stop(self):
        """dev_cpu_log 0, then the ring since cpu_start: [(callback, us, frames)]"""
        self.remote.set_param(self.slot, "synth:dev_cpu_log", 0)
        self.remote.pump(0.5)                    # (the worker writes the log every 100 ms)
        return parse_ring(self.cpu_fetch() or "")

    def level_now(self, secs=0.3):
        return -240.0

    def wait_silence(self, limit=10.0, floor_db=-80.0):
        return True

    def rec(self, dur, events):
        import wave
        self.n += 1
        take = "d%s-%03d" % (self.stamp, self.n)
        ev, i = [], 0                            # (48 a ms: the app's MIDI ring holds 64; a program change at 5 ms)
        for k, a, b in self.pending:
            if k == "pc":
                ev.append((max(5, 2 + i // 48), *to_status(k, a, b, self.ch)))
            else:
                ev.append((1 + i // 48, *to_status(k, a, b, self.ch)))
                i += 1
        ev += [(TAP_PRE_MS + int(round(1000 * t)), *to_status(k, a, b, self.ch)) for t, k, a, b in events]
        self.pending = []
        dur_ms = TAP_PRE_MS + int(round(1000 * dur)) + 1
        self.remote.play(self.slot, take, dur_ms, ev)
        time.sleep(dur_ms / 1000 + TAP_GRID_S + 0.3)
        got, t0 = None, time.monotonic()
        while got is None and time.monotonic() - t0 < self.wait:
            got = self.fetch(take)
            if got is None:
                time.sleep(0.5)
        if got is None:
            self.stop()
            raise RuntimeError("the tap %s did not arrive: is the installed dsp.so a build with dev_play (its "
                               "module_data/frodhes/boot.txt says dev_play=1), Frodhes in slot %d, and does the host "
                               "keep calling render_block (module.json requires_continuous_processing)?" %
                               (take, self.slot + 1))
        path, js = got
        w = wave.open(path)
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float)
        i0 = int(TAP_PRE_MS * FS / 1000)
        bad = ["%s %s" % (k, js[k]) for k in ("dropped", "late", "bad", "aborted") if js.get(k)]
        return {"x": x[i0:i0 + int(round(dur * FS))], "sr": FS, "xruns": bad}


# ---- the analysis (hil_scan's and hil_crackle's functions; the recording at its own rate, LSB)
def _mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def _onset(x, sr, t, thr=200):
    s = max(0, int((t - 0.005) * sr))
    a = np.abs(x[s:]).max(axis=1)
    i = int(np.argmax(a > thr)) if len(a) else 0
    return (s + i) / sr if len(a) and a[i] > thr else None


def _seg(x, sr, t0, t1):
    return x[max(0, int(t0 * sr)):max(0, int(t1 * sr))]


def _f0(x, sr, guess):
    """hil_scan.f0_of at the recording's rate"""
    n = len(x)
    sp = np.abs(np.fft.rfft(x * np.hanning(n), 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / sr)
    m = (fr > guess * 0.94) & (fr < guess * 1.06)
    if not m.any():
        return 0.0
    i = np.flatnonzero(m)[np.argmax(sp[m])]
    if 0 < i < len(sp) - 1:
        a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
        return (i + 0.5 * (a - c) / (a - 2 * b + c)) * sr / (8 * n)
    return float(fr[i])


def _centroid(x, sr):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / sr)
    return float((sp * fr).sum() / (sp.sum() + 1e-12))


def glitches(x, sr, events, ccs=()):
    """hil_crackle's detectors on one recording (its clock fitted to the events, clock_fit): repeat / stale (an
    underrun or a callback played twice), zero (a dropout inside sound), clip, click (one-sample impulses outside the
    attack and pedal guards): [(t s, kind, detail)]"""
    import hil_crackle as hc
    q = np.round(np.asarray(x, float)).astype(np.int64)
    kinds = ("repeat", "stale", "zero", "clip", "click", "click-lim")   # (click-lim: under the limiter, information)
    try:
        fit = hc.clock_fit(q, events, sr)
        res = hc.analyse(q, None, sr, events, ccs=ccs, only=kinds, clock_usb=(fit["lag"], fit["rate"]))
        return [(t, k, "%s [frame %d: mod 128 = %d, mod 256 = %d]" % (d, int(round(t * sr)), int(round(t * sr)) % 128,
                                                                       int(round(t * sr)) % 256))
                for t, k, d in res["usb"] if k in kinds]   # (the phase: the recording's frames, not the Move's blocks;
                                                           # a constant phase over many events still points at a block)
    except Exception as e:                   # (nothing to fit the clock on: the detectors that need no guards)
        out = [(f / sr, "stale", d) for f, d in hc.det_stale(q)]
        out += [(f / sr, "zero", d) for f, d in hc.det_zero(q.astype(float), sr, exact=True)]
        out += [(f / sr, "clip", d) for f, d in hc.det_clip(q.astype(float), 32767)]
        return sorted(out) + [(0.0, "note", "click / repeat not run: %s" % e)]


# ---- the tests: each returns (ok, report); takes go to d
def _plain(o):
    """numpy scalars to Python (the report's JSON and its print)"""
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    return o.item() if isinstance(o, np.generic) else o


def _save(d, name, take):
    import wave
    os.makedirs(d, exist_ok=True)
    w = wave.open(os.path.join(d, name + ".wav"), "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(int(take["sr"]))
    w.writeframes(np.clip(np.round(take["x"]), -32768, 32767).astype("<i2").tobytes())
    w.close()


ENGINE_CCS = {"rhodes": (), "vibes": ((79, 0), (47, 0)), "marimba": ((79, 127),)}   # (hil_crackle.engine_ccs: CC 79
# the bar TYPE; VIBES with its motor off, CC 47: the TYPE's turn switches it on, and its pulse would move every level
# the tests read; usb-host plays the presets, F13 with its motor)


def _prep(r, engine=None, **cc):
    """hil_scan's dry state (every parameter at its default, no effects), the engine, then cc (name or number: value)"""
    r.dry()
    for c, v in ENGINE_CCS[engine or "rhodes"]:
        r._send("cc", c, v)
    for k, v in cc.items():
        r._send("cc", hs.CC[k] if k in hs.CC else int(k.lstrip("c")), int(v))
    time.sleep(0.05)


def usb_note(r, d, note=60, vel=100, engine=None, name="note", out=print):
    _prep(r, engine)
    ev = [(0.2, "on", note, vel), (1.6, "off", note, 0)]
    tk = r.rec(3.2, ev)
    _save(d, name, tk)
    x, sr = tk["x"], tk["sr"]
    on = _onset(x, sr, 0.2)
    rep = {"note": note, "vel": vel, "xruns": tk["xruns"]}
    if on is None:
        rep["verdict"] = "FAIL: no sound (Settings > USB-C audio out = Main Out? Frodhes in the slot? channel %d)" % r.ch
        out(rep["verdict"])
        return False, rep
    body = _mono(_seg(x, sr, on + 0.05, on + 0.45))
    f = _f0(_mono(_seg(x, sr, on + 0.2, on + 0.7)), sr, note_hz(note))   # (the steady part: the attack's pitch moves)
    cents = 1200 * np.log2(f / note_hz(note)) if f else None
    pre_off = hs.db(_seg(x, sr, 1.5 + (on - 0.2), 1.6 + (on - 0.2)))
    tail = hs.db(_seg(x, sr, 2.6 + (on - 0.2), 3.0 + (on - 0.2)))
    g = glitches(x, sr, ev)
    rep.update(onset_ms=round(1000 * (on - 0.2), 1), peak_db=round(hs.db(np.abs(x).max() * np.ones(1)), 1),
               rms_db=round(hs.db(body), 1), f0=round(f, 2), cents=None if cents is None else round(cents, 1),
               centroid_hz=round(_centroid(body, sr)), before_off_db=round(pre_off, 1),
               tail_db=round(tail, 1), glitches=g)
    bad = []
    if cents is None or abs(cents) > 10:
        bad.append("pitch %s cents" % rep["cents"])
    if tail > pre_off - 30 and tail > -90:      # (a bar note that has died before key-up: silent at both)
        bad.append("the note does not die after key-up (%.1f dB under)" % (pre_off - tail))
    if g:
        bad.append("%d glitch(es)" % len(g))
    if tk["xruns"]:
        bad.append("recorder xruns %s" % tk["xruns"])
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("note %d v%d: onset %.1f ms (MIDI + USB + capture), RMS %.1f dBFS, f0 %.2f Hz (%+.1f c), centroid %d Hz, "
        "after key-up %.1f dB down, %d glitch(es): %s" % (note, vel, rep["onset_ms"], rep["rms_db"], f,
                                                         cents or 0, rep["centroid_hz"], pre_off - tail, len(g),
                                                         rep["verdict"]))
    return not bad, rep


VELS = (1, 8, 16, 24, 32, 48, 64, 80, 96, 112, 127)


# usb-vel per engine, as the FM-1's scans judge it (tools/hil_scan.py vibes_vel / marimba_vel): the velocities, the
# first one from which no step may fall more than 0.5 dB, and the rise (dB) from lo to hi. VIBES with the motor off
# (its pulse moves a 100 ms level by a few dB, by where the strike falls on it)
VEL = {"rhodes": dict(vels=VELS, mono_from=1, rise=(1, 127, 20.0, None)),
       "vibes": dict(vels=(1, 8, 24, 40, 64, 80, 96, 117, 127), mono_from=24, rise=(40, 117, 10.0, None)),
       "marimba": dict(vels=VELS, mono_from=8, rise=(8, 127, 26.0, 32.0))}


def usb_vel(r, d, note=60, vels=None, engine=None, out=print):
    c = VEL[engine or "rhodes"]
    vels = vels or c["vels"]
    _prep(r, engine)
    step, ev = 1.2, []
    for i, v in enumerate(vels):
        ev += [(0.2 + i * step, "on", note, v), (0.2 + i * step + 0.5, "off", note, 0)]
    tk = r.rec(0.2 + len(vels) * step + 0.4, ev)
    _save(d, "vel", tk)
    x, sr = tk["x"], tk["sr"]
    rows = []
    for i, v in enumerate(vels):
        t = 0.2 + i * step
        on = _onset(x, sr, t, thr=20)
        if on is None or on > t + 0.2:
            rows.append({"vel": v, "db": None})
            continue
        a = _mono(_seg(x, sr, on, on + 0.1))
        rows.append({"vel": v, "db": round(hs.db(a), 2), "centroid_hz": round(_centroid(a, sr)),
                     "onset_ms": round(1000 * (on - t), 1)})
    lv = [w["db"] for w in rows]
    bad = []
    if any(v is None for v in lv):
        bad.append("silent at v%s" % [w["vel"] for w in rows if w["db"] is None])
    else:
        drops = [(rows[i]["vel"], rows[i + 1]["vel"]) for i in range(len(lv) - 1)
                 if rows[i]["vel"] >= c["mono_from"] and lv[i + 1] < lv[i] - 0.5]
        if drops:
            bad.append("level falls between %s" % drops)
        lo, hi, need, most = c["rise"]
        if lo in vels and hi in vels:
            rise = lv[vels.index(hi)] - lv[vels.index(lo)]
            if rise < need or (most is not None and rise > most):
                bad.append("v%d -> v%d: %.1f dB (want %s)" % (lo, hi, rise, ">= %g" % need if most is None else
                                                             "%g..%g" % (need, most)))
    g = glitches(x, sr, ev)
    if g:
        bad.append("%d glitch(es)" % len(g))
    for w in rows:
        out("v%-3d %s" % (w["vel"], "silent" if w["db"] is None else
                          "%7.2f dBFS  centroid %5d Hz  onset %5.1f ms" % (w["db"], w["centroid_hz"], w["onset_ms"])))
    rep = {"note": note, "rows": rows, "range_db": None if None in lv else round(lv[-1] - lv[0], 2), "glitches": g,
           "verdict": "FAIL: " + "; ".join(bad) if bad else "PASS"}
    out("range %s dB: %s" % (rep["range_db"], rep["verdict"]))
    return not bad, rep


def _after_keyup(x, sr, t_off, span, win=0.05):
    """the loudest 50 ms window after key-up against the 50 ms before it (dB): > 1 = louder after key-up"""
    ref = hs.db(_seg(x, sr, t_off - win, t_off))
    w = [hs.db(_seg(x, sr, t_off + k * win, t_off + (k + 1) * win)) for k in range(int(span / win))]
    return max(w) - ref if w else -240.0


# usb-sus per engine (times in s from the take's start): the key-up, the windows the levels are read in, the pedal's
# let-up and the catch's press. A Rhodes note and a vibraphone bar ring for seconds: the FM-1's sustain preflight
# (1 s after key-up). A marimba bar's own ring is short (C4 is ~35 dB down 0.4 s in, MANUAL section 5: "MUTE 0 rings
# out"), so the pedal is judged while the bar still rings: MUTE at 10.0 (CC 103 127: the hand stops it 60 dB down
# within 0.1 s), the key let go at 0.35 s, the levels 0.1-0.2 s after; held, the hand never lands (MANUAL: "the
# pedal keeps its meaning")
SUS = {"rhodes": dict(off=0.8, win=(0.9, 1.15), up=2.0, gone=(0.9, 1.3), dur=(2.2, 3.4), catch=0.5, hold_db=15.0,
                      cc=()),
       "marimba": dict(off=0.35, win=(0.1, 0.2), up=0.55, gone=(0.1, 0.2), dur=(1.2, 1.4), catch=0.25, hold_db=None,
                       cc=((103, 127),))}
SUS["vibes"] = SUS["rhodes"]


def usb_sus(r, d, note=60, vel=100, engine=None, out=print):
    """three takes: (a) no pedal: the note dies after key-up; (b) the pedal down before the note, key-up: it rings,
    then the pedal up damps it; (c) the pedal pressed after the note (it catches the held note). The pedal holds when
    (b) at the window after key-up is >= 20 dB over (a) there (and, where notes ring for seconds, within hold_db of
    its level before key-up); SUS[engine] gives the times"""
    c = SUS[engine or "rhodes"]
    off, (w0, w1), up, (g0, g1) = c["off"], c["win"], c["up"], c["gone"]
    rep, bad = {"engine": engine or "rhodes"}, []
    _prep(r, engine, **{"c%d" % k: v for k, v in c["cc"]})
    ev = [(0.2, "on", note, vel), (off, "off", note, 0)]
    a = r.rec(c["dur"][0], ev)
    _save(d, "sus-a-damped", a)
    lat = (_onset(a["x"], a["sr"], 0.2) or 0.2) - 0.2
    x, sr = a["x"], a["sr"]
    ref = hs.db(_seg(x, sr, off - 0.1 + lat, off + lat))
    dead = hs.db(_seg(x, sr, off + w0 + lat, off + w1 + lat))
    rep["damped"] = {"before_off_db": round(ref, 1), "after_1s_db": round(dead, 1),
                     "louder_after_keyup_db": round(_after_keyup(x, sr, off + lat, 1.0), 2)}
    if dead > ref - 40:
        bad.append("(a) a damped note does not die: %.1f dB down %.2f s after key-up" % (ref - dead, w0))
    ev = [(0.1, "cc", 64, 127), (0.2, "on", note, vel), (off, "off", note, 0), (up, "cc", 64, 0)]
    b = r.rec(c["dur"][1], ev)
    _save(d, "sus-b-pedal", b)
    x, sr = b["x"], b["sr"]
    ref = hs.db(_seg(x, sr, off - 0.1 + lat, off + lat))
    ring = hs.db(_seg(x, sr, off + w0 + lat, off + w1 + lat))
    gone = hs.db(_seg(x, sr, up + g0 + lat, up + g1 + lat))
    rep["pedal"] = {"before_off_db": round(ref, 1), "held_1s_db": round(ring, 1), "after_up_db": round(gone, 1),
                    "over_damped_db": round(ring - dead, 1),
                    "louder_after_keyup_db": round(_after_keyup(x, sr, off + lat, 1.1), 2)}
    if ring < dead + 20 or (c["hold_db"] is not None and ring < ref - c["hold_db"]):
        bad.append("(b) the pedal does not hold: %.1f dB down %.2f s after key-up, %.1f dB over the damped note (CC 64 "
                   "reached the slot?)" % (ref - ring, w0, ring - dead))
    if gone > ring - 30:
        bad.append("(b) the pedal let up does not damp: %.1f dB down" % (ring - gone))
    ev = [(0.2, "on", note, vel), (c["catch"], "cc", 64, 127), (off, "off", note, 0), (up, "cc", 64, 0)]
    cc_ = r.rec(c["dur"][1], ev)
    _save(d, "sus-c-catch", cc_)
    x, sr = cc_["x"], cc_["sr"]
    ref = hs.db(_seg(x, sr, off - 0.1 + lat, off + lat))
    ring = hs.db(_seg(x, sr, off + w0 + lat, off + w1 + lat))
    rep["catch"] = {"held_1s_db": round(ring, 1), "before_off_db": round(ref, 1)}
    if ring < dead + 20 or (c["hold_db"] is not None and ring < ref - c["hold_db"]):
        bad.append("(c) the pedal pressed under a held key does not catch it")
    for k in ("damped", "pedal"):
        if rep[k]["louder_after_keyup_db"] > 1.0:
            bad.append("(%s) louder after key-up by %.1f dB" % (k, rep[k]["louder_after_keyup_db"]))
    g = glitches(a["x"], a["sr"], [(0.2, "on", note, vel), (off, "off", note, 0)])
    rep["glitches"] = g
    if g:
        bad.append("%d glitch(es) in (a)" % len(g))
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    rep = _plain(rep)
    out("damped %s | pedal %s | catch %s: %s" % (rep["damped"], rep["pedal"], rep["catch"], rep["verdict"]))
    return not bad, rep


def clock_events(bpm, t0, t1):
    """MIDI Start at t0, 24 PPQN F8 from t0 to t1, Stop at t1"""
    p = 60.0 / bpm / 24
    ev = [(t0 - 0.001, "rt", 0xFA, 0)]
    k = 0
    while t0 + k * p < t1:
        ev.append((t0 + k * p, "rt", 0xF8, 0))
        k += 1
    return ev + [(t1, "rt", 0xFC, 0)]


def usb_clock(r, d, bpm=120.0, secs=8.0, note=60, engine=None, out=print):
    """the arpeggiator (CC 52 1: UP, CC 53 1: 1/8) on one held note, MIDI clock at bpm: a step every half beat"""
    _prep(r, engine, c52=1, c53=1)
    ev = clock_events(bpm, 0.3, secs - 0.5) + [(0.25, "on", note, 100), (secs - 0.6, "off", note, 0)]
    tk = r.rec(secs, ev)
    _save(d, "clock", tk)
    x, sr = tk["x"], tk["sr"]
    env = np.abs(_mono(x))
    hop = int(0.002 * sr)
    e = np.array([env[i:i + hop].max() for i in range(0, len(env) - hop, hop)])
    ons, last = [], -1.0
    for i in range(5, len(e)):                   # (an attack: the 2 ms envelope 6 dB over its value 10 ms before)
        t = i * hop / sr
        if e[i] > 200 and e[i] > 2 * e[i - 5] + 50 and t - last > 0.06:
            ons.append(t)
            last = t
    want = 30.0 / bpm
    o = np.array(ons[1:])
    rep = {"bpm": bpm, "steps": len(ons), "want_ms": round(1000 * want, 2)}
    bad = []
    if len(o) < 5:
        bad.append("%d steps: the clock or the arpeggiator did not run (does the slot get F8 / Start?)" % len(ons))
    else:
        # (each attack against a straight line through them: an attack the detector catches 5 ms or more late, under
        # the ring of the step before (the bars), is counted, not taken as timing; jitter is the spread of the others)
        k = np.round((o - o[0]) / float(np.median(np.diff(o))))
        inl = np.ones(len(o), bool)
        for _ in range(2):
            a, b = np.polyfit(k[inl], o[inl], 1)
            res = o - (a * k + b)
            inl = np.abs(res) < 0.005
        med, sd, late = float(a), float(np.std(res[inl])), int((~inl).sum())
        rep.update(median_ms=round(1000 * med, 2), jitter_ms=round(1000 * sd, 2), bpm_seen=round(30.0 / med, 2),
                   late=late)
        if abs(med / want - 1) > 0.01:
            bad.append("step %.1f ms against %.1f ms (%.2f BPM seen)" % (1000 * med, 1000 * want, 30.0 / med))
        if sd > 0.004:
            bad.append("jitter %.1f ms" % (1000 * sd))
        if late > max(2, len(o) // 10):
            bad.append("%d of %d attacks off the beat by 5 ms or more" % (late, len(o)))
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("clock %.1f BPM: %s: %s" % (bpm, {k: v for k, v in rep.items() if k != "verdict"}, rep["verdict"]))
    return not bad, rep


def usb_stress(r, d, engine="rhodes", secs=60.0, nv=MOVE_VOICES, bpm=0.0, seed=0, out=print):
    """hil_crackle's worst takes on the Move, two of secs / 2: every effect heavy, the pedal down, chords every
    0.15-0.4 s across the registers at v100 / v127 (up to 12 notes each; under the pedal every voice sounds), the pedal
    lifted and pressed again; the first take at LEVEL -12 dB (CC 110 0: the limiter stays out, so the click check
    sees everything), the second at unity (a click under the limiter is information, click-lim); MIDI clock with bpm"""
    import hil_crackle as hc
    rng = np.random.default_rng(seed)
    rep = {"engine": engine, "voices": nv, "takes": []}
    bad = []
    for name, fx in (("low", dict(hc.HEAVY, LEVEL=0)), ("unity", hc.HEAVY)):
        ccs = hc.engine_ccs(engine, fx)
        r._send("cc", 120, 0)
        for c, v in ccs:
            r._send("cc", c, v)
        time.sleep(0.3)
        ev, dur = hc.worst_events(nv, secs / 2, rng)
        ev = sorted(ev, key=lambda e: e[0])
        tk = r.rec(dur, ev + (clock_events(bpm, 0.1, dur - 0.2) if bpm else []))
        _save(d, "stress-%s-%s" % (engine, name), tk)
        x, sr = tk["x"], tk["sr"]
        g = glitches(x, sr, ev, ccs=ccs)
        gated = [e for e in g if e[1] in hc.GATED]
        peak = float(np.abs(x).max())
        t = {"take": name, "secs": round(dur, 1), "notes": sum(1 for e in ev if e[1] == "on"),
             "peak_dbfs": round(20 * np.log10(peak / 32767 + 1e-12), 1), "events": g, "gated": len(gated),
             "xruns": tk["xruns"]}
        rep["takes"].append(t)
        for tt, k, det in g[:20]:
            out("  %-5s %8.3f s  %-9s %s" % (name, tt, k, det))
        out("stress %s %s: %d voices, %.0f s, %d notes, peak %.1f dBFS, %d gated event(s) (%d in all)%s" % (
            engine, name, nv, dur, t["notes"], t["peak_dbfs"], len(gated), len(g),
            ", recorder xruns %s" % tk["xruns"] if tk["xruns"] else ""))
        if gated:
            bad.append("%s: %d glitch(es)" % (name, len(gated)))
        if tk["xruns"]:
            bad.append("%s: recorder xruns" % name)
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("%s (then the CPU ring: tools/move_hil.py --go cpu)" % rep["verdict"])
    return not bad, rep


def usb_devices(midi=None, audio=None, out=print):
    import mido
    import sounddevice as sd
    outs = mido.get_output_names()
    pm = _pick(outs, MIDI_KEYS, "MIDI", midi)
    ins = [d for d in sd.query_devices() if d["max_input_channels"] >= 2]
    pa = _pick([d["name"] for d in ins], AUDIO_KEYS, "audio", audio)
    out("MIDI outputs:")
    for n in outs:
        out("  %s %s" % ("*" if n == pm else " ", n))
    out("audio inputs (>= 2 channels):")
    for dv in ins:
        out("  %s %s  (%d in, %d Hz)" % ("*" if dv["name"] == pa else " ", dv["name"], dv["max_input_channels"],
                                         dv["default_samplerate"]))
    out("picked: MIDI %s, audio %s" % (pm, pa))
    return 0 if pm and pa else 2


PHRASE = [(36, 40), (36, 127), (60, 40), (60, 100), (60, 127), (84, 40), (84, 127)]   # (note, velocity), one by one
CHORD = [(48, 90), (60, 100), (64, 100), (67, 100), (72, 110)]
NOTE_S, GAP_S = 1.0, 1.0                    # held, then the release's silence before the next


def phrase_events(t0=0.4):
    ev, marks, t = [(0.05, "cc", 10, 127)], [], t0    # (CC 10: the effects' and LFOs' state to boot, both sides)
    for n, v in PHRASE:
        ev += [(t, "on", n, v), (t + NOTE_S, "off", n, 0)]
        marks.append(("%d v%d" % (n, v), t, n))
        t += NOTE_S + GAP_S
    ev += [(t, "on", n, v) for n, v in CHORD] + [(t + 1.5, "off", n, 0) for n, _ in CHORD]
    marks.append(("chord", t, CHORD[1][0]))
    return ev, marks, t + 1.5 + GAP_S + 0.5


def host_phrase(ev, dur, program=0):
    """the host build (tests/host.c) playing the events after program change program (F01 = 0): (n, 2) LSB at 44.1"""
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    pre = 500
    lines = ["5 midi 0xC0 %d" % program]
    for t, k, a, b in ev:
        ms = pre + int(round(1000 * t))
        lines.append("%d midi 0x%02X %d %d" % (ms, {"on": 0x90, "off": 0x80, "cc": 0xB0}[k], a, b))
    run = hostrun.run("move-same-as-host", lines, pre + int(1000 * dur) + 20)
    i0 = int(pre * hostrun.FS / 1000)
    return np.stack([np.asarray(run.L[i0:], float), np.asarray(run.R[i0:], float)], 1)


def _lag(a, b, maxlag):
    """the lag (frames) of a against b (a[i + lag] ~ b[i]) by cross-correlation (FFT)"""
    n = len(a) + len(b)
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.r_[np.arange(0, maxlag + 1), np.arange(-maxlag, 0)]
    vals = np.r_[c[:maxlag + 1], c[-maxlag:]]
    return int(lags[int(np.argmax(vals))])


def _decay_s(x, sr, db=20.0):
    """time from the 10 ms envelope's peak to db under it"""
    hop = int(0.01 * sr)
    e = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) for i in range(0, len(x) - hop, hop)])
    if not len(e) or e.max() <= 0:
        return None
    k = int(np.argmax(e))
    below = np.flatnonzero(e[k:] < e[k] * 10 ** (-db / 20))
    return round(float(below[0] * hop / sr), 3) if len(below) else None


def _bands(x, sr):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    c = 50 * 2 ** (np.arange(0, 25) / 3)                   # third octaves 50 Hz .. 12.8 kHz
    return np.array([10 * np.log10(sp[(f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))].sum() + 1e-12) for fc in c])


TAIL_JUDGED_DBFS = -60.0   # usb-host: a note whose tail (0.5-1.0 s, the host's, after the gain) is quieter is not
                           # judged on its residual: a short bar note (MARIMBA 84 v40) has died there, the floor
                           # and the rounding are all that is left


def usb_host(r, d, program=0, out=print):
    """the same phrase on the Move and on the host build (program change program, CC 10): one gain for the whole
    take, each note aligned on its own (MIDI over USB lands up to a ms or two apart), then per note: the residual
    after the gain (dB under the note), f0 (cents, the steady part), the time to -20 dB, the centroid, the third-
    octave bands' largest difference. A note whose tail is under TAIL_JUDGED_DBFS is not judged on its residual. Classic is the host's code: the residual should be at the floor, the gain
    the Move's volume"""
    ev, marks, dur = phrase_events()
    r._send("cc", 123, 0)
    r._send("pc", program, 0)
    time.sleep(0.5)
    tk = r.rec(dur, ev)
    _save(d, "move-phrase", tk)
    mv, sr = tk["x"], tk["sr"]
    h = host_phrase(ev, dur, program)
    _save(d, "host-phrase", {"x": h, "sr": 44100})
    rows, segs = [], []
    maxlag = int(0.08 * sr)
    for name, t, n in marks:
        # the alignment, the gain and the residual on the held note's second half (0.5-1.0 s: a short foreign sound
        # on the same keys, a Move instrument on the track, has died there; the first device run, 2026-10-28); the
        # "extra" is what the Move has on top of the host's note over the whole held second
        a, b = int(t * sr), int((t + NOTE_S) * sr)
        ta, tb = a + int(0.5 * sr), b
        hm_t = _mono(h[ta:tb])
        if np.sqrt(np.mean(hm_t ** 2)) < 32768 * 10 ** (TAIL_JUDGED_DBFS / 20):   # (died by then: the whole note)
            ta = a
            hm_t = _mono(h[ta:tb])
        best = (-2.0, 0)
        for lag in range(-int(0.01 * sr), maxlag):
            if ta + lag < 0 or tb + lag > len(mv):
                continue
            mt = _mono(mv[ta + lag:tb + lag])
            c = float(np.dot(mt, hm_t) / (np.linalg.norm(mt) * np.linalg.norm(hm_t) + 1e-9))
            if c > best[0]:
                best = (c, lag)
        corr, lag = best
        mm = _mono(mv[a + lag:b + lag])
        hm = _mono(h[a:b])
        segs.append((name, n, mm, hm, lag, corr))
    nt = int(0.5 * sr)
    gs = [float(np.dot(m[-nt:], hh[-nt:]) / (np.dot(hh[-nt:], hh[-nt:]) + 1e-9)) for _, _, m, hh, _, _ in segs]
    g = float(np.median(gs)) if gs else 0.0          # (one gain: the notes' median, so one spoilt note cannot move it)
    for name, n, mm, hm, lag, corr in segs:
        res = mm - g * hm
        rd = 20 * np.log10((np.sqrt(np.mean(res[-nt:] ** 2)) + 1e-9) / (np.sqrt(np.mean(mm[-nt:] ** 2)) + 1e-9))
        held = slice(0, int(NOTE_S * sr))
        ex = res[held]
        ex_db = 20 * np.log10((np.sqrt(np.mean(ex ** 2)) + 1e-9) / (np.sqrt(np.mean((g * hm[held]) ** 2)) + 1e-9))
        sp = np.abs(np.fft.rfft(ex * np.hanning(len(ex))))
        fr = np.fft.rfftfreq(len(ex), 1 / sr)
        peaks = []
        for k in np.argsort(sp)[::-1]:
            if fr[k] > 20 and all(abs(fr[k] - q) > 15 for q in peaks):
                peaks.append(round(float(fr[k])))
            if len(peaks) == 4:
                break
        st = slice(int(0.25 * sr), int(0.75 * sr))
        fm, fh = _f0(mm[st], sr, note_hz(n)), _f0(hm[st], sr, note_hz(n))
        tail = 20 * np.log10((np.sqrt(np.mean((g * hm[-nt:]) ** 2)) + 1e-9) / 32768)
        rows.append({"note": name, "lag_ms": round(1000 * lag / sr, 2), "tail_corr": round(corr, 4),
                     "residual_db": round(float(rd), 1), "tail_dbfs": round(float(tail), 1),
                     "judged": bool(tail >= TAIL_JUDGED_DBFS), "extra_db": round(float(ex_db), 1), "extra_hz": peaks,
                     "f0_move_c": round(1200 * np.log2(fm / note_hz(n)), 2) if fm else None,
                     "f0_host_c": round(1200 * np.log2(fh / note_hz(n)), 2) if fh else None,
                     "decay_move_s": _decay_s(mm, sr), "decay_host_s": _decay_s(hm, sr),
                     "centroid_move": round(_centroid(mm[st], sr)), "centroid_host": round(_centroid(hm[st], sr))})
    rep = {"program": program, "gain": round(g, 5), "gain_db": round(20 * np.log10(abs(g) + 1e-12), 2),
           "rows": rows, "peak_move": float(np.abs(mv).max()), "peak_host": float(np.abs(h).max())}
    bad = [w["note"] for w in rows if w["residual_db"] > -40 and w["judged"]]
    extra = [w["note"] for w in rows if w["extra_db"] > -30]
    rep["verdict"] = ("PASS: the host's sound (one gain %.2f dB)" % rep["gain_db"] if not bad and not extra else
                      "DIFFERS at %s (residual over -40 dB after one gain, 0.5-1.0 s into the note)" % bad if bad else
                      "FRODHES = the host (tails), but ANOTHER SOUND plays with the keys at %s (a Move instrument "
                      "on the track, or another slot): silence it and rerun" % extra)
    out("gain Move/host %.2f dB (peak Move %d, host %d LSB)" % (rep["gain_db"], rep["peak_move"], rep["peak_host"]))
    for w in rows:
        out("%-8s lag %6.2f ms (tail corr %.4f)  tail residual %6.1f dB  extra %6.1f dB %s  f0 %+6.2f / %+6.2f c  "
            "decay %s / %s s  centroid %d / %d Hz" % (
                w["note"], w["lag_ms"], w["tail_corr"], w["residual_db"] if w["judged"] else float("nan"), w["extra_db"], w["extra_hz"],
                w["f0_move_c"] or 0, w["f0_host_c"] or 0, w["decay_move_s"], w["decay_host_s"],
                w["centroid_move"], w["centroid_host"]))
    out(rep["verdict"])
    return not bad and not extra, rep


def usb_host_file(d, out=print):
    """usb-host's analysis again on a saved run (move-phrase.wav, host-phrase.wav in d)"""
    import wave

    def rd(p):
        w = wave.open(p)
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float), w.getframerate()

    m, sr = rd(os.path.join(d, "move-phrase.wav"))
    h, _ = rd(os.path.join(d, "host-phrase.wav"))

    class Saved(FakeRig):
        def rec(self, dur, events):
            return {"x": m, "sr": sr, "xruns": []}
    global host_phrase
    keep = host_phrase
    host_phrase = lambda ev, dur, program=0: h            # noqa: E731
    try:
        return usb_host(Saved(), os.path.join(d, "reanalysis"), out=out)
    finally:
        host_phrase = keep


IDLE_FLOOR_DB = -80.0                       # the digital Main Out with nothing playing: under this (silence, or dither)


def idle_verdict(x, sr):
    """what an idle USB-C stream is: {"kind": "digital" (under IDLE_FLOOR_DB) | "mic" (the room: broadband noise
    with rumble under 20 Hz, the two channels one microphone) | "playing" (sound, not the room's signature),
    floor_db, peak, lf_db (under 20 Hz against 200 Hz-4 kHz, per Hz), same_lr (share of frames with L == R)}"""
    x = np.asarray(x, float)
    floor = hs.db(x)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    m = x.mean(axis=1) if x.ndim == 2 else x
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2 if len(m) else np.zeros(1)
    f = np.fft.rfftfreq(len(m), 1 / sr) if len(m) else np.zeros(1)

    def per_hz(lo, hi):
        k = (f >= lo) & (f < hi)
        return 10 * np.log10(sp[k].mean() + 1e-12) if k.any() else -240.0
    lf = per_hz(1, 20) - per_hz(200, 4000)
    same = float(np.mean(x[:, 0] == x[:, 1])) if x.ndim == 2 and len(x) else 0.0
    if floor < IDLE_FLOOR_DB:
        kind = "digital"
    elif lf > 10 and same > 0.3:
        kind = "mic"
    else:
        kind = "playing"
    return {"kind": kind, "floor_db": round(floor, 1), "peak": peak, "lf_db": round(float(lf), 1),
            "same_lr": round(same, 2)}


SAY = {"digital": "the digital Main Out",
       "mic": "the MICROPHONE (the room: rumble under 20 Hz, one channel on both sides). On the Move: Settings > "
              "USB-C audio out, pick Mic and then Main Out again (the Move resets it at boot, and its sampling page "
              "puts it back on the mic while the screen still says Main Out); then rerun",
       "playing": "NOT silent, and not the room's signature: something plays (a clip, another slot); stop it, rerun"}


def preflight(r, out=print, secs=1.0):
    """the Move's USB-C stream must be its digital Main Out: with nothing playing it is (near) silent (idle_verdict).
    Nothing reads the setting itself: Schwung's master_fx:usbc_out_source sees only the 37 14 half, not the
    monitoring bit whose loss is the silent revert to the mic (docs/SPI_PROTOCOL.md). (ok, verdict)"""
    r._send("cc", 123, 0)
    r._send("cc", 64, 0)
    time.sleep(0.5)
    tk = r.rec(secs, [])
    v = idle_verdict(tk["x"], tk["sr"])
    out("preflight: idle %.1f dBFS, peak %d LSB, rumble %+.1f dB, L == R %.0f %%: %s" % (
        v["floor_db"], v["peak"], v["lf_db"], 100 * v["same_lr"], SAY[v["kind"]]))
    return v["kind"] == "digital", v


USB_CMDS = ("usb-devices", "usb-host", "usb-note", "usb-vel", "usb-sus", "usb-clock", "usb-stress")


def usb_main(a):
    import json
    if a.cmd == "usb-host" and a.analyze:            # (a saved run, offline: no device)
        ok, _ = usb_host_file(a.analyze)
        return 0 if ok else 1
    if a.cmd == "usb-devices" and not a.fake:
        return usb_devices(a.midi, a.audio)
    d = a.out or os.path.join(OUT, stamp() + "-" + a.cmd)
    if a.fake:
        inj = []
        for s in a.inject or ():
            k, _, t = s.partition("@")
            inj.append((k, float(t or 0)))
        r = FakeRig(inject=inj)
    elif a.path == "tap":
        r = TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), a.ch)
    else:
        r = UsbRig(a.midi, a.audio, a.ch)
    if a.cmd == "usb-devices":
        print("picked: MIDI %s, audio %s" % (r.port_name, r.audio_name))
        return 0
    if not r.fake and not a.no_preflight and a.path == "usb":
        ok, pre = preflight(r)
        if not ok:
            return 2
    print("MIDI %s (channel %d), audio %s; takes -> %s" % (r.port_name, r.ch, r.audio_name, d), flush=True)
    e = a.engine if a.engine in ENGINE_CCS else None
    fn = {"usb-host": lambda: usb_host(r, d, a.program), "usb-note": lambda: usb_note(r, d, a.note, a.vel, e),
          "usb-vel": lambda: usb_vel(r, d, a.note, engine=e),
          "usb-sus": lambda: usb_sus(r, d, a.note, engine=e), "usb-clock": lambda: usb_clock(r, d, a.bpm or 120.0, engine=e),
          "usb-stress": lambda: usb_stress(r, d, a.engine if a.engine != "all" else "rhodes", a.secs, a.nv, a.bpm)}
    ok, rep = fn[a.cmd]()
    rep["rig"] = {"midi": r.port_name, "audio": r.audio_name, "ch": r.ch, "fake": r.fake}
    os.makedirs(d, exist_ok=True)
    json.dump(_plain(rep), open(os.path.join(d, "report.json"), "w"), indent=1, default=str)
    return 0 if ok else 1


# --------------------------------------------------------------------------- d2: the whole USB-C run, three engines
D2_ENGINES = ("rhodes", "vibes", "marimba")
D2_PRESET = {"rhodes": 0, "vibes": 12, "marimba": 14}         # F01, F13 VIBES, F15 MARIMBA (the dry presets)
D2_NOTES = {"rhodes": (36, 60, 84), "vibes": (53, 65, 84), "marimba": (36, 60, 84)}   # (VIBES' range is F3-F6)


class FakeEchoRig(FakeRig):
    """the fake Move for d2: usb-host's phrase comes back as the host's own render (6 dB down, 300 frames late), so
    a fake d2 can pass; everything else is FakeRig"""

    def rec(self, dur, events):
        ev, _, _ = phrase_events()
        if sorted(events) == sorted(ev):
            for _, kind, a, b in events:
                self.log.append((kind, a, b))
            prog = next((a for k, a, b in reversed(self.log) if k == "pc"), 0)
            h = host_phrase(events, dur, prog)
            x = np.zeros_like(h)
            x[300:] = 0.5 * h[:-300]
            return {"x": np.round(x), "sr": self.sr, "xruns": []}
        return FakeRig.rec(self, dur, events)


def _num(rep, test):
    """the key numbers of one test's report, one short line"""
    try:
        if test == "preflight":
            return "idle %s dBFS (%s)" % (rep["floor_db"], rep["kind"])
        if test == "ws":
            return rep.get("note", "")
        if test == "usb-host":
            w = rep["rows"]
            j = [x["residual_db"] for x in w if x.get("judged", True)]
            return "gain %.2f dB, residual max %s dB (%d of %d notes judged), extra max %.1f dB" % (
                rep["gain_db"], "%.1f" % max(j) if j else "-", len(j), len(w), max(x["extra_db"] for x in w))
        if test.startswith("usb-note"):
            return "%d v%d: %s c, RMS %s dBFS, onset %s ms, %d glitch(es)" % (
                rep["note"], rep["vel"], rep.get("cents"), rep.get("rms_db"), rep.get("onset_ms"),
                len(rep.get("glitches") or []))
        if test == "usb-vel":
            return "range %s dB, %d glitch(es)" % (rep["range_db"], len(rep["glitches"]))
        if test == "usb-sus":
            return "damped %s dB after key-up, pedal held %s dB / up %s dB, louder after key-up %s / %s dB" % (
                rep["damped"]["after_1s_db"], rep["pedal"]["held_1s_db"], rep["pedal"]["after_up_db"],
                rep["damped"]["louder_after_keyup_db"], rep["pedal"]["louder_after_keyup_db"])
        if test == "cpu":
            if not rep.get("n"):
                return "no data"
            return "%d callbacks while usb-stress played, worst %d us, p99 %d us, %d over %d us, %d missing%s" % (
                rep["n"], rep["worst"], rep["p99"], rep["over"], rep["limit"], rep["missing"],
                "" if rep.get("hq") is None else ", HQ %s" % ("on" if rep["hq"] else "off"))
        if test == "usb-clock":
            return "%s steps, %s BPM seen, jitter %s ms, %s late" % (rep["steps"], rep.get("bpm_seen"), rep.get("jitter_ms"),
                                                                      rep.get("late"))
        if test == "usb-stress":
            return "; ".join("%s: %s notes, peak %s dBFS, %d gated" % (t["take"], t["notes"], t["peak_dbfs"], t["gated"])
                             for t in rep["takes"])
    except (KeyError, TypeError, ValueError) as e:
        return "(%s)" % e
    return ""


def _ws_prepare(url, engine, slot=0, out=print):
    """slot 1 must hold Frodhes; its preset is set to the engine's (D2_PRESET). (ok, report)"""
    import move_ws
    want = D2_PRESET[engine]
    try:
        with move_ws.Remote(url) as w:
            st = w.subscribe(slot)
            if st["module"] != mi.MODULE_ID:
                return False, dict(st, verdict="FAIL: slot %d holds %r, not %s" % (slot + 1, st["module"],
                                                                                    mi.MODULE_ID))
            before = st["preset"]
            st = w.set_preset(slot, want)
    except (OSError, TimeoutError, move_ws.SchwungError) as e:
        return False, {"verdict": "FAIL: Schwung's WebSocket %s: %s" % (url, e)}
    st.update(before=before, verdict="PASS",
              note="slot %d %s, preset %s -> %s %s" % (slot + 1, st["module"], before, st["preset"],
                                                       st["preset_name"] or ""))
    out("ws: " + st["note"])
    return True, st


def d2(r, d, engines=D2_ENGINES, ws=None, stress_secs=60.0, bpm=120.0, hq=None, out=print):
    """per engine: the slot's module and preset over Schwung's WebSocket (ws: its URL, None: skipped), the preflight
    (a failure stops the whole run: the stream is not the Move's Main Out), usb-host on the engine's preset, usb-note
    on three keys, usb-vel, usb-sus, usb-clock, usb-stress; each test's takes and report.json in d/<engine>/<test>/,
    the table in d/summary.md, everything in d/report.json. Over the tap with a CPU read (TapRig cpu): the cpu row,
    every callback's time while usb-stress plays (the worst case), against U12. hq 0 / 1 (the tap): the HQ switch
    set first (p_hq; None: as the plugin has it). Returns (exit code: 0 all PASS, 1 a FAIL, 2 stopped)"""
    import json
    rows, code = [], 0
    os.makedirs(d, exist_ok=True)

    def note(engine, test, ok, rep):
        rep = _plain(rep)
        rows.append({"engine": engine, "test": test, "ok": bool(ok), "verdict": str(rep.get("verdict", "")),
                     "numbers": _num(rep, test), "report": rep})
        sub = os.path.join(d, engine, test)
        os.makedirs(sub, exist_ok=True)
        json.dump(rep, open(os.path.join(sub, "report.json"), "w"), indent=1, default=str)

    def quiet(s):
        out("  " + s)
    stopped = None
    tap_cpu = isinstance(r, TapRig) and r.cpu_fetch is not None
    if hq is not None and isinstance(r, TapRig):
        r.set_hq(hq)
    for e in engines:
        out("== %s" % e)
        if ws:
            ok, rep = _ws_prepare(ws, e, out=quiet)
            note(e, "ws", ok, rep)
            if not ok:
                stopped = "%s: %s" % (e, rep["verdict"])
                break
        if not isinstance(r, TapRig):           # (the tap is the plugin's own output: no stream to check)
            ok, pre = preflight(r, out=quiet)
            pre["verdict"] = "PASS" if ok else "FAIL: " + SAY[pre["kind"]]
            note(e, "preflight", ok, pre)
            if not ok:
                stopped = "%s: the preflight failed (%s)" % (e, pre["kind"])
                break
        sub = lambda t: os.path.join(d, e, t)          # noqa: E731
        r._send("cc", 120, 0)                          # (the host's state: every parameter at its default, the
        if not hasattr(r, "defaults"):                 # preset then loads over it, as on the host build)
            r.defaults = hs.param_defaults()
        for c, v in r.defaults:
            r._send("cc", c, v)
        tests = [("usb-host", lambda: usb_host(r, sub("usb-host"), D2_PRESET[e], out=quiet))]
        for n in D2_NOTES[e]:
            tests.append(("usb-note-%d" % n, lambda n=n: usb_note(r, sub("usb-note-%d" % n), n, 100, e, out=quiet)))
        tests += [("usb-vel", lambda: usb_vel(r, sub("usb-vel"), engine=e, out=quiet)),
                  ("usb-sus", lambda: usb_sus(r, sub("usb-sus"), engine=e, out=quiet)),
                  ("usb-clock", lambda: usb_clock(r, sub("usb-clock"), bpm, engine=e, out=quiet)),
                  ("usb-stress", lambda: usb_stress(r, sub("usb-stress"), e, stress_secs, out=quiet))]
        cpu_rows = []
        for name, fn in tests:
            out("-- %s %s" % (e, name))
            try:
                if tap_cpu and name == "usb-stress":
                    r.cpu_start()
                    try:
                        ok, rep = fn()
                    finally:
                        cpu_rows = r.cpu_stop()
                else:
                    ok, rep = fn()
            except Exception as x:                     # (one test's crash is its FAIL; the run goes on)
                ok, rep = False, {"verdict": "FAIL: %s: %s" % (type(x).__name__, x)}
            note(e, name, ok, rep)
        if tap_cpu:
            s = cpu_summary(cpu_rows)
            s["engine"], s["hq"] = e, hq
            s["verdict"] = s["verdict"] if s["n"] else "FAIL: no callback times (dev_cpu_log reached the plugin?)"
            note(e, "cpu", s["verdict"].startswith("PASS"), s)
    if stopped:
        code = 2
    elif not all(w["ok"] for w in rows):
        code = 1
    rig = {"midi": r.port_name, "audio": r.audio_name, "ch": r.ch, "fake": r.fake}
    md = ["# Move D2 over %s: %s" % ("the tap" if isinstance(r, TapRig) else "USB-C",
                                     "PASS" if code == 0 else "FAIL" if code == 1 else "STOPPED"), "",
          "%s; MIDI %s (channel %d), audio %s%s; Schwung WebSocket %s" % (
              time.strftime("%Y-%m-%d %H:%M:%S"), rig["midi"], rig["ch"], rig["audio"], " (FAKE)" if r.fake else "",
              ws or "not used"), ""]
    if stopped:
        md += ["**Stopped:** " + stopped, ""]
    md += ["| Engine | Test | Result | Key numbers |", "| --- | --- | --- | --- |"]
    for w in rows:
        v = w["verdict"].replace("|", "/").replace("\n", " ")
        res = "PASS" if w["ok"] else ("FAIL" + (": " + v[6:][:160] if v.startswith("FAIL: ") else ": " + v[:160]))
        md.append("| %s | %s | %s | %s |" % (w["engine"], w["test"], res, w["numbers"].replace("|", "/")))
    failed = sum(1 for w in rows if not w["ok"])
    md += ["", "%d test(s), %d FAIL. Takes and each report.json: %s/<engine>/<test>/" % (len(rows), failed, d)]
    open(os.path.join(d, "summary.md"), "w").write("\n".join(md) + "\n")
    json.dump({"code": code, "stopped": stopped, "rig": rig, "ws": ws, "rows": rows}, open(os.path.join(d, "report.json"), "w"),
              indent=1, default=str)
    out("\n".join(md))
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("facts", "install", "rollback", "info", "logs", "cpu", "crackle", "must", "null", "d2")
                    + USB_CMDS)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--target-dir", help="a local folder in place of the Move's module folder (tests)")
    ap.add_argument("--src", default=os.path.join(ROOT, "build", "move", mi.MODULE_ID))
    ap.add_argument("--file", help="cpu: read this ring log instead of pulling it")
    ap.add_argument("--engine", default="all")
    ap.add_argument("--analyze", help="crackle: a recording (WAV, 16 bit) to run the detectors on")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--midi", help="usb-*: the MIDI output (a substring of its name; default the Move's Standalone port)")
    ap.add_argument("--audio", help="usb-*: the audio input (a substring; default the Move's)")
    ap.add_argument("--ch", type=int, default=USB_CH, help="usb-*: the MIDI channel, 1-based (default 1)")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--program", type=int, default=0, help="usb-host: the program change first (0 = F01)")
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--bpm", type=float, default=0.0, help="usb-clock (default 120), usb-stress (0: no clock)")
    ap.add_argument("--secs", type=float, default=60.0, help="usb-stress: the take's length")
    ap.add_argument("--nv", type=int, default=MOVE_VOICES, help="usb-stress: voices per chord")
    ap.add_argument("--out", help="usb-*: the folder for the takes and report.json")
    ap.add_argument("--no-preflight", action="store_true", help="usb-*: skip the idle check of the USB-C stream")
    ap.add_argument("--fake", action="store_true", help="usb-*: the offline model of the Move (no device)")
    ap.add_argument("--inject", action="append", help="usb-* --fake: KIND@SECONDS (zero, click, stale, loud, nosus)")
    ap.add_argument("--engines", default=",".join(D2_ENGINES), help="d2: the engines, comma-separated")
    ap.add_argument("--hq", type=int, choices=(0, 1), default=None,
                    help="d2 --path tap: the HQ Rhodes off (0) or on (1) for the run (default: as the plugin has it)")
    ap.add_argument("--path", choices=("usb", "tap"), default="usb",
                    help="usb-*, d2: usb = MIDI to the Standalone port, the USB-C audio recorded (the Move's mix); "
                         "tap = the plugin's own player and output over Schwung's WebSocket (--ws), the takes "
                         "fetched over ssh (--host): no Move track, volume or USB-C setting in the way")
    ap.add_argument("--ws", default=os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui"),
                    help="d2: Schwung's WebSocket (slot 1's module and preset); 'none' to skip")
    a = ap.parse_args(argv)
    if a.cmd == "d2":
        engines = tuple(x for x in a.engines.split(",") if x)
        bad = [x for x in engines if x not in D2_ENGINES]
        if bad:
            ap.error("--engines: %s" % bad)
        d = a.out or os.path.join(OUT, stamp() + "-d2")
        if a.fake:
            inj = [(k, float(t or 0)) for k, _, t in (s.partition("@") for s in a.inject or ())]
            r = FakeEchoRig(inject=inj)
        elif a.path == "tap":
            r = TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), a.ch, cpu=scp_cpu(a.host))
        else:
            r = UsbRig(a.midi, a.audio, a.ch)
        return d2(r, d, engines, None if a.ws == "none" else a.ws, a.secs, a.bpm or 120.0, a.hq)
    if a.cmd in USB_CMDS:
        return usb_main(a)
    t = mi.Target(host=None if a.target_dir else a.host, local_dir=a.target_dir, go=a.go or a.cmd == "info")
    if a.cmd in ("facts", "install", "rollback", "logs", "cpu") and not a.go and not a.file:
        print("(dry run: add --go to execute; %s)" % (a.target_dir or a.host))
    if a.cmd == "facts":
        facts(t)
    elif a.cmd == "install":
        mi.install(t, a.src)
    elif a.cmd == "rollback":
        mi.rollback(t)
    elif a.cmd == "info":
        mi.info(t)
    elif a.cmd == "logs":
        logs(t)
    elif a.cmd == "cpu":
        s = cpu(t, a.file)
        return 1 if s and s["verdict"].startswith("FAIL") else 0
    elif a.cmd == "crackle":
        if a.analyze:
            return 1 if crackle_analyze(a.analyze) else 0
        crackle_plan(a.engine)
    elif a.cmd == "must":
        return must()
    elif a.cmd == "null":
        import device_null
        if a.self_test:
            return 0 if all(w == g for _, w, g, _ in device_null.self_test()) else 1
        device_null.dry_run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
