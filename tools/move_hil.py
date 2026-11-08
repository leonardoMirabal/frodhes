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
    tools/move_hil.py usb-clock [--bpm 100]  MIDI clock (Start + F8) with the tremolo synced at 1/8 on a held Rhodes
                                             note: its period against the clock's, the jitter. Always over the tap
                                             (dev_play): Schwung gives a slot no MIDI clock from USB-C, only the
                                             Move's own transport (UsbRig.CLOCK_IN)
    tools/move_hil.py clock-probe [--auto]   the user presses Play on the Move, then Stop (the tool says when; --auto:
                                             the tool presses them, Schwung's test bus): do the
                                             Move's own F8 / FA / FC reach the slot, or only the bridge's fallback
                                             (mb_clock_block)? The plugin's dev_stats, logged while dev_stats_log is 1
    tools/move_hil.py usb-stress [--engine rhodes --secs 60 --nv 16 --bpm 0]   the worst case: every voice, every
                                             effect heavy, the pedal (hil_crackle's worst take); stale / repeat /
                                             zero / clip / click; then read the CPU ring with `--go cpu`
    tools/move_hil.py d2 [--engines rhodes,vibes,marimba --secs 60 --ws URL|none]   the whole run, per engine: slot
                                             1's module checked and its preset set over Schwung's WebSocket
                                             (tools/move_ws.py; F01 / F13 / F15), preflight (a failure stops the
                                             run), usb-host, usb-note on 3 keys, usb-vel, usb-sus, usb-clock (once),
                                             usb-stress; the table in build/move/hil/<time>-d2/summary.md
  --path tap (usb-*, d2): no USB-C at all: each take goes to the plugin's own player over Schwung's WebSocket
  (dev_play, move/move_bridge.h: never on cable 2, so no Move track plays along) and comes back as the plugin's own
  output (the tap file, fetched over ssh): free of the Move's mix, volumes and USB-C setting; usb-host then compares
  sample for sample (the take starts on the host build's block grid).
    tools/move_hil.py preflight              before every sound or level verdict (usb-host, usb-note, usb-vel, usb-sus,
                                             usb-clock, usb-stress, d2 on either path, usb-levels; --no-preflight skips
                                             it): (1) Main Out idle under IDLE_FLOOR_DB RMS and IDLE_PEAK_LSB peak (the
                                             microphone named apart), (2) one note over the Move's MIDI path, Main Out
                                             against Frodhes' tap of the same moment (a passive dev_play take): one gain
                                             a channel by least squares, the residual under PF_RESID_DB re the note and
                                             PF_RESID_DBFS, else "sound on Main Out that is not Frodhes", (3) what
                                             Schwung shows of slot 1 (chain, channels, CHAN) and the Move's saved set of
                                             track 1 (MIDI in, instruments). A failure stops the command (exit 2)
  --midi NAME, --audio NAME (substrings, else the Move's), --ch N (default: slot 1's receive channel, read over
  Schwung's WebSocket or shadow_chain_config.json; unread, the tools stop and name the channel 1 they assumed), --fake: an
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
        # each instance's own (move_loader.c: inst/<k>/*.log, boot.txt, load.txt), one file: d/inst-logs.txt
        code, out = t._exec(["ssh", *mi.SSH_OPTS, t.host, 'cd %s 2>/dev/null && for f in */*.log */*.txt; do '
                             '[ -f "$f" ] && echo "== $f" && cat "$f"; done; true' % mi.INST])
        if t.go and not code and out:
            open(os.path.join(d, "inst-logs.txt"), "w").write(out)
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


def newest_cmd(words):
    """a shell command: the newest of the files (a word list, globs allowed) to stdout. Each instance writes its own
    files in its folder (move_loader.c: inst/<k>); the tools turn the timer ring on in one slot"""
    return 'f=$(ls -t %s 2>/dev/null | head -n 1); [ -n "$f" ] && cat "$f"' % words


def cpu(t, path=None):
    if path:
        s = cpu_summary(parse_ring(open(path).read()))
    else:
        code, out = t.run(newest_cmd("%s %s/%s/cpu-ring.log" % (mi.inst_glob("cpu-ring.log"), t.mods, mi.MODULE_ID)))
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
FAKE_TAP_LEAD = 2.46                        # (s) the fake tap's start ahead of the take (the device's, 2026-10-29)
FAKE_MIX = 5279 / 4096.0 * 0.9              # the fake Main Out over its tap: the trim and a mix of -0.9 dB
FAKE_HISS_DBFS = -54.6                      # the fake's steady floor (inject hiss): the bad case of 2026-10-29


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
    (the device's own rate), "sr", "xruns"}. Kinds: on / off / cc / pc, rt (a realtime byte: 0xF8, 0xFA, 0xFC).
    CLOCK_IN False: Schwung (1.8) hands a slot only the channel messages of USB-C's cable 2 (host/shadow_midi.c
    shadow_dispatch_*: CIN 0x08-0x0E); the realtime bytes a slot gets are the Move's own transport, cable 0
    (schwung_shim.c "Broadcast Move's internal transport clock"). The F8 / FA / FC sent here reach the Move, never
    Frodhes (the plugin's rt_f8 stays 0, 2026-10-30): the clock is tested over the tap (TapRig) or by the Move's
    own transport (clock-probe)"""
    fake = False
    CLOCK_IN = False

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
    tremolo (CC 17 43-85, CC 21 >= 64: AM, CC 18 its depth) synced (CC 20, FAKE_SYNC) to the MIDI clock's F8 while
    Start ran, else free at 120 BPM. inject: [(kind, t s)], kind zero (2 ms of zeros), click (one sample), stale (one
    128-frame callback twice), loud (the note gets louder after key-up), nosus (the pedal ignored), playing (a tone at
    idle: the preflight fails), noclock (the clock ignored), hiss (a steady -54.6 dBFS floor on Main Out, idle too),
    foreign (a noise burst with every note-on on Main Out, not in the tap: a sampler on the track), mic (the room at
    idle). The tap (begin / collect, the preflight's): the take before the trim and the mix (FAKE_MIX), FAKE_TAP_LEAD
    s ahead. As Schwung does, the realtime bytes sent over USB-C never reach the slot (UsbRig.CLOCK_IN); tap=True
    models the tap path instead (TapRig: they do)"""
    fake = True

    def __init__(self, inject=(), sr=FAKE_SR, seed=0, tap=False):
        self.CLOCK_IN = bool(tap)
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

    def begin(self, secs):                       # (the preflight's passive tap: the next rec's)
        self.tap = None

    def collect(self):
        return self.tap

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
        if not events and any(k == "mic" for k, _ in self.inject):         # (USB-C audio out left on the mic: the room)
            t = np.arange(n) / sr
            room = np.round(self.rng.normal(0, 60, n) + 2000 * np.sin(2 * np.pi * 4 * t) + 800 * np.sin(2 * np.pi * 11 * t))
            return {"x": np.stack([room, room], 1), "sr": sr, "xruns": []}
        if not events and any(k == "hiss" for k, _ in self.inject):        # (the preflight's idle: a steady floor)
            return {"x": np.round(self.rng.normal(0, 32768 * 10 ** (FAKE_HISS_DBFS / 20), (n, 2))), "sr": sr,
                    "xruns": []}
        ped = [(0.0, self.state.get(64, 0) >= 64)] + [(t, b >= 64) for t, k, a, b in ev if k == "cc" and a == 64]
        if nosus:
            ped = [(0.0, False)]
        for t, k, a, b in ev:
            if k == "cc":
                self.state[a] = b
        notes, held = [], {}                     # each note: (t_on, t_off, note, vel)
        for t, k, a, b in ev:
            if k == "on" and b:
                held[a] = (t, b)
            elif k in ("on", "off") and a in held:
                t1, v = held.pop(a)
                notes.append((t1, t, a, v))
        notes += [(t1, dur, a, v) for a, (t1, v) in held.items()]

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
        st = self.state
        if 43 <= st.get(17, 0) <= 85 and st.get(21, 0) >= 64:           # the tremolo, AM
            f8 = [] if any(k == "noclock" for k, _ in self.inject) or not self.CLOCK_IN else [
                t for t, k, a, b in ev if k == "rt" and a == 0xF8]          # (over USB-C, as Schwung: none arrive)
            per = FAKE_SYNC.get(st.get(20, 0) * 14 // 127)                 # (F8 per cycle; None: FREE)
            if per and len(f8) > 1:                                        # (the phase: the F8 count, between them
                ph = np.interp(tt, f8, np.arange(len(f8)) / per)           # at the clock's rate; before the first
                ph[tt < f8[0]] = (tt[tt < f8[0]] - f8[0]) / 0.25          # and after the last, free at 120 BPM)
                ph[tt > f8[-1]] = ph[tt <= f8[-1]][-1] + (tt[tt > f8[-1]] - f8[-1]) / 0.25
            else:
                ph = tt / 0.25
            x *= 1 - st.get(18, 0) / 127 * (0.5 + 0.5 * np.cos(2 * np.pi * ph))
        x *= 0.25 if self.state.get(110, 64) == 0 else 1.0                # LEVEL (CC 110 0: -12 dB)
        x = 29204 * np.tanh(x / 29204)                                    # (the app's limiter, softly: fx.c LIM_T;
        #                                                                     2026-11-08: 24000 before 0.9.10)
        clean = np.stack([x, 0.9 * x], 1)
        # (the tap: the plugin's own output, before the Move's trim and mix, FAKE_TAP_LEAD s ahead of the take)
        self.tap = np.concatenate([np.zeros((int(FAKE_TAP_LEAD * FS), 2)), np.round(clean / FAKE_MIX),
                                   np.zeros((int(0.5 * FS), 2))])
        x = x + self.rng.normal(0, 2.0, n)
        q = np.round(np.stack([x, 0.9 * x], 1))
        for k, _ in self.inject:
            if k == "hiss":                                 # (a steady floor on Main Out, not in the tap: -54.6 dBFS)
                q += np.round(self.rng.normal(0, 32768 * 10 ** (FAKE_HISS_DBFS / 20), (n, 2)))
            elif k == "foreign":                            # (a sampler on the track: noise with every note-on)
                for t1, _, _, v in notes:
                    i0 = int((t1 + FAKE_LAT) * sr)
                    m = n - i0
                    if m > 0:
                        env = np.exp(-np.arange(m) / (0.3 * sr))
                        q[i0:] += np.round(self.rng.normal(0, 600, (m, 2)) * env[:, None])
        for k, t in self.inject:
            i = int(t * sr)
            if k == "zero":
                q[i:i + int(0.002 * sr)] = 0
            elif k == "click":
                q[i, :] += 6000
            elif k == "stale":
                q[i:i + BLOCK] = q[i - BLOCK:i]
        return {"x": np.clip(q, -32768, 32767), "sr": sr, "xruns": []}


FAKE_SYNC = {1: 3, 2: 4, 3: 6, 4: 8, 5: 9, 6: 12, 7: 16, 8: 18, 9: 24, 10: 36, 11: 48, 12: 96}   # (the fake
# tremolo's SYNC: CC 20 * 14 // 127, F8 a cycle at 24 PPQN: 1/32 .. 1/1; 0 FREE)
TAP_PRE_MS = 500                            # the tap path: the messages sent before a take (the panel) at 1 ms and a
                                            # program change at 5 ms, the take's own from 500 ms (host_phrase's layout)
TAP_DIR = mi.DATA + "/tap"                  # (a build before the loader; now each instance's inst/<k>/tap)
TAP_DIRS = "%s/*/tap %s" % (mi.INST, TAP_DIR)
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
        r = subprocess.run(["ssh", *mi.SSH_OPTS, host, 'for d in %s; do [ -f "$d/%s.json" ] && { echo "$d"; break; }; '
                            'done; true' % (TAP_DIRS, take)], capture_output=True, text=True)
        d = r.stdout.strip()
        if r.returncode or not d:
            return None
        js = os.path.join(local_dir, take + ".json")
        r = subprocess.run(["scp", *mi.SSH_OPTS, "-q", "%s:%s/%s.json" % (host, d, take), js], capture_output=True)
        if r.returncode:
            return None
        wav = os.path.join(local_dir, take + ".wav")
        r = subprocess.run(["scp", *mi.SSH_OPTS, "-q", "%s:%s/%s.wav" % (host, d, take), wav], capture_output=True)
        if r.returncode:
            return None
        subprocess.run(["ssh", *mi.SSH_OPTS, host, "rm -f %s/%s.wav %s/%s.json" % (d, take, d, take)],
                       capture_output=True)
        return wav, json.load(open(js))
    return fetch


def scp_cpu(host):
    """the plugin's timer ring (cpu-ring.log in its module folder, written while dev_cpu_log is 1) from the Move:
    cpu() -> its text ("" when there is none), the file deleted there"""
    import subprocess
    words = "%s %s/%s/cpu-ring.log" % (mi.inst_glob("cpu-ring.log"), mi.MODS, mi.MODULE_ID)

    def cpu():
        r = subprocess.run(["ssh", *mi.SSH_OPTS, host, "%s; rm -f %s" % (newest_cmd(words), words)],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    return cpu


class TapRig(UsbRig):
    """the Move without its mix: every take goes to the plugin's own player over Schwung's WebSocket (dev_play: no
    MIDI on cable 2, so no Move track plays along) and comes back as the plugin's own output (the tap file: before
    the slot's volume, the Move's mix, its master and its USB-C setting). What the tests send before a take (the
    panel's CCs, a program change) leads the take, as host_phrase's do; rec() returns the take from TAP_PRE_MS on.
    fetch(take) -> (wav, json) | None (scp_fetch; a test passes its own). cpu() -> the timer ring's text, the
    file then deleted (scp_cpu; None: no CPU read). CLOCK_IN: the schedule's F8 / FA / FC reach the app (the
    bridge plays them itself)"""
    fake = False
    CLOCK_IN = True

    def __init__(self, url, fetch, ch=USB_CH, slot=0, wait=10.0, cpu=None):
        import move_ws
        self.remote, self.fetch, self.ch, self.slot, self.wait = move_ws.Remote(url), fetch, int(ch), slot, wait
        self.cpu_fetch, self.hq = cpu, None
        self.port_name, self.audio_name, self.sr = "Schwung %s, slot %d, dev_play" % (url, slot + 1), "the plugin's tap", FS
        self.pending, self.n, self.mido = [], 0, hs._Mido
        self.stamp = time.strftime("%H%M%S")
        self.stop()                              # (a take left armed by an earlier run would refuse the next)

    def stop(self):
        self._set("synth:dev_play", "stop")
        self._pump(0.5)

    def reconnect(self):
        """a new connection after the manager dropped this one (the plugin keeps playing: nothing lost but the
        socket)"""
        import move_ws
        try:
            self.remote.close()
        except Exception:
            pass
        self.remote = move_ws.Remote(self.remote.url)

    def _pump(self, secs):
        import move_ws
        try:
            self.remote.pump(secs)
        except move_ws.ConnectionClosed:
            self.reconnect()

    def _set(self, key, value):
        import move_ws
        try:
            self.remote.set_param(self.slot, key, value)
        except move_ws.ConnectionClosed:
            self.reconnect()
            self.remote.set_param(self.slot, key, value)

    def _send(self, kind, a, b=0):
        self.pending.append((kind, int(a), int(b)))

    def set_hq(self, on):
        """the HQ Rhodes (the bridge's p_hq): new notes play HQ (1) or Classic (0)"""
        self._set("synth:p_hq", 1 if on else 0)
        self.hq = 1 if on else 0
        self._pump(0.2)

    def boot_settings(self):
        """the app's settings as the host build boots (boot_keys): a setup the Move saved cannot play along. Only the
        keys whose value Schwung reports otherwise (or not at all) are written: a write is not always nothing (the
        pedal's, see host_phrase), and the host build gets none. The read and the writes go over a connection of
        their own, kept open until the manager's re-read has gone out: the rig's own never subscribes (a
        subscriber gets every write's re-read, 94 params, and the manager then reads its socket late). Returns the
        keys written"""
        import move_ws
        w = move_ws.Remote(self.remote.url)
        try:
            try:
                w.subscribe(self.slot)
            except (OSError, TimeoutError):
                pass
            sent = self._boot_write(w)
            w.pump(1.5)
        finally:
            w.close()
        return sent

    def _boot_write(self, w):
        have, sent = w.params.get(self.slot, {}), []

        def same(a, b):
            try:
                return a is not None and float(a) == float(b)
            except ValueError:
                return a == b
        for k, v in boot_keys():
            if not same(have.get("synth:" + k), v):
                w.set_param(self.slot, "synth:" + k, v)
                sent.append(k)
                time.sleep(0.003)
        return sent

    def cpu_start(self):
        """the plugin logs every callback's time from now (dev_cpu_log 1); an older log is dropped first"""
        self.cpu_fetch()
        self._set("synth:dev_cpu_log", 1)
        self._pump(0.2)

    def cpu_stop(self):
        """dev_cpu_log 0, then the ring since cpu_start: [(callback, us, frames)]"""
        self._set("synth:dev_cpu_log", 0)
        self._pump(0.5)                    # (the worker writes the log every 100 ms)
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
        import move_ws
        try:
            self.remote.play(self.slot, take, dur_ms, ev)
        except move_ws.ConnectionClosed:
            # (the connection dropped while the take went up: stop it, and play it again under a new id only when
            # the plugin shows it never started: no tap file, or one that played nothing. A take that started is
            # never sent again: its notes would sound twice)
            self.reconnect()
            self.stop()
            time.sleep(1.0)
            got = self.fetch(take)
            if got is not None and got[1].get("played", 1):
                raise RuntimeError("the WebSocket dropped while take %s went up, and the plugin had started it "
                                   "(played %s of %s): not sent again" % (take, got[1].get("played"),
                                                                         got[1].get("events")))
            take += "r"
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
                               "module_data/frodhes/inst/<k>/boot.txt says dev_play=1), Frodhes in slot %d, and does the host "
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


GLOBAL_CCS = ((64, 0), (111, 0), (119, 0))
# (the modes param_defaults does not reach, off as the host boots: the pedal, the FX bypass, RAW. The Move has no
# arpeggiator, sequencer or chords: the bridge drops their CCs, MB_CC_DROP)
BOOT_KEYS = os.path.join(ROOT, "move", "tests", "boot_keys.txt")


def boot_keys():
    """[(key, value)]: the app's settings as a new instance has them (move/tests/boot_keys.txt)"""
    out = []
    for ln in open(BOOT_KEYS):
        if ln.strip() and not ln.startswith("#"):
            k, v = ln.split()
            out.append((k, v))
    return out


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


CLOCK_CCS = ((17, 64), (21, 127), (18, 127), (20, 59))
# (usb-clock's tremolo: TREM (CC 17 43-85), MODE AM (CC 21 64-127), DEPTH full, SYNC 1/8 (CC 20 59: index 6 of FREE
# 1/32 16T 1/16 8T 16D 1/8 ...); the rest at the defaults)
CLOCK_T0 = 3.0          # (the synced LFO catches up with the clock gradually, at most 1/8 faster or slower a step:
                        # from the internal 120 BPM to 100 it takes ~2 s; the troughs are judged from here)


def usb_clock(r, d, bpm=100.0, secs=12.0, note=60, engine=None, out=print):
    """MIDI clock at bpm from the take's start; the tremolo (AM, full depth) synced at 1/8 on one Rhodes note held
    from 2.8 s: a trough of its level every half beat. The troughs from CLOCK_T0 against a straight line: the period
    (within 1 % of the clock's) and the jitter (4 ms at most). The tremolo's LFO is the effects' code, the same for
    every engine, so the take is the Rhodes' (it holds a note longest; engine is not used). The default 100 BPM is
    not the internal 120: a tremolo that ignores the clock fails on the period and the jitter. A rig whose clock
    never reaches the slot (UsbRig.CLOCK_IN: Schwung's USB-C input) is refused before any take; d2 and the command
    then run the test over the tap (clock_rig)"""
    if not getattr(r, "CLOCK_IN", True):
        rep = {"bpm": bpm, "verdict": "FAIL: not over USB-C: Schwung gives a slot no MIDI clock from USB-C (only the "
                                      "Move's own transport); run usb-clock over the tap (--path tap)"}
        out("clock %.1f BPM: %s" % (bpm, rep["verdict"]))
        return False, rep
    _prep(r, None, **{"c%d" % c: v for c, v in CLOCK_CCS})
    ev = clock_events(bpm, 0.1, secs - 0.4) + [(2.8, "on", note, 100), (secs - 0.6, "off", note, 0)]
    tk = r.rec(secs, ev)
    _save(d, "clock", tk)
    x, sr = tk["x"], tk["sr"]
    from scipy.signal import find_peaks
    m = _mono(x)
    hop, w = max(1, int(round(0.001 * sr))), int(round(0.010 * sr))
    e = np.sqrt(np.convolve(m * m, np.ones(w) / w, "same"))[::hop]          # (the level: 10 ms RMS, a value a ms)
    ed = 20 * np.log10(e + 1)
    want = 30.0 / bpm
    pk, _ = find_peaks(-ed, distance=max(1, int(0.6 * want * sr / hop)), prominence=3)
    t = pk * hop / sr
    t = t[(t > CLOCK_T0) & (t < secs - 0.7)]
    rep = {"bpm": bpm, "troughs": int(len(t)), "want_ms": round(1000 * want, 2)}
    bad = []
    if len(t) < 8:
        bad.append("%d troughs: no tremolo on the held note (does the slot get the CCs?)" % len(t))
    else:
        k = np.round((t - t[0]) / want)
        a, b = np.polyfit(k, t, 1)
        res = t - (a * k + b)
        med, sd = float(a), float(np.std(res))
        rep.update(period_ms=round(1000 * med, 2), jitter_ms=round(1000 * sd, 2), bpm_seen=round(30.0 / med, 2),
                   worst_ms=round(1000 * float(np.abs(res).max()), 1))
        if abs(med / want - 1) > 0.01:
            bad.append("period %.1f ms against %.1f ms (%.2f BPM seen)" % (1000 * med, 1000 * want, 30.0 / med))
        if sd > 0.004:
            bad.append("jitter %.1f ms (the tremolo does not follow the clock: does the slot get F8 / Start?)"
                       % (1000 * sd))
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("clock %.1f BPM: %s: %s" % (bpm, {k: v for k, v in rep.items() if k != "verdict"}, rep["verdict"]))
    return not bad, rep


# ---- clock-probe: where the slot's clock comes from when the Move's own transport plays (the user presses Play,
# then Stop). Schwung broadcasts the Move's cable-0 realtime bytes (F8, FA, FC) to every slot (schwung_shim.c, "Broadcast
# Move's internal transport clock (cable 0)"), as MOVE_MIDI_SOURCE_EXTERNAL; without them the bridge makes the pulses
# from the host's transport and beat position (mb_clock_block, AUTO). The plugin's dev_stats, logged 10 a second to
# stats.log while dev_stats_log is 1, count both: rt_f8 / rt_fa / rt_fc (the bytes that came in), f8_synth (the
# pulses made), host_run / host_bpm10 (get_clock_status, get_bpm), bpm10 / clklock (the app's tempo, the clock followed)
STATS_LOG = "stats.log"


def parse_stats(text):
    """stats.log's lines -> [{name: int}] (a line that does not parse is skipped)"""
    rows = []
    for ln in text.splitlines():
        try:
            rows.append({k: int(v) for k, v in (w.split("=", 1) for w in ln.split())})
        except ValueError:
            continue
    return [r for r in rows if "ms" in r and "rt_f8" in r]


STATS_PATHS = "%s/*/%s %s/%s/%s" % (mi.INST, STATS_LOG, mi.MODS, mi.MODULE_ID, STATS_LOG)
# (the plugin writes stats.log in its own folder: under the loader each instance's inst/<k>/, before it the module
# folder; the probe read only the module folder's, so it found nothing under the loader, 2026-10-30)


def pick_stats(text):
    """the read of every stats.log ("== PATH" before each file's lines) -> the rows of the longest (the slot's
    instance: the one logging, dev_stats_log is set on its slot only)"""
    best, cur = [], []
    for ln in text.splitlines() + ["== end"]:
        if ln.startswith("== "):
            rows = parse_stats("\n".join(cur))
            best, cur = (rows if len(rows) > len(best) else best), []
        else:
            cur.append(ln)
    return best


class StatsLink:
    """the plugin's dev_stats over Schwung's WebSocket (dev_stats_log) and ssh (stats.log in the instance's folder,
    STATS_PATHS)"""
    path = STATS_PATHS

    def __init__(self, url, host, slot=0):
        import move_ws
        self.remote, self.host, self.slot = move_ws.Remote(url), host, slot

    def _ssh(self, cmd):
        import subprocess
        r = subprocess.run(["ssh", *mi.SSH_OPTS, self.host, cmd], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""

    def start(self):
        self._ssh("rm -f %s" % self.path)
        self.remote.set_param(self.slot, "synth:dev_stats_log", 1)
        self.remote.pump(0.3)

    def read(self):
        return pick_stats(self._ssh('for f in %s; do [ -f "$f" ] && { echo "== $f"; cat "$f"; }; done; true'
                                    % self.path))

    def stop(self):
        try:
            self.remote.set_param(self.slot, "synth:dev_stats_log", 0)
            self.remote.pump(0.3)
        finally:
            self._ssh("rm -f %s" % self.path)
            self.remote.close()


class FakeStatsLink:
    """the probe's tests: a plugin whose transport starts after `play_at` reads and stops `play_for` reads later (a
    read is 1 s: 10 lines); source "real" (the F8 come in, none made), "fallback" (none come in: the bridge makes
    them), "none" (the transport never runs), "late" (the real F8 start 2 s after Play: the fallback first)"""

    def __init__(self, source="real", bpm=120.0, play_at=3, play_for=12):
        self.source, self.bpm, self.play_at, self.play_for = source, bpm, play_at, play_for
        self.rows, self.reads, self.started, self.stopped = [], 0, False, False
        self.c = dict(ms=0, rt_f8=0, rt_fa=0, rt_fb=0, rt_fc=0, f8_synth=0, host_run=0, host_bpm10=int(bpm * 10),
                      bpm10=1200, clklock=0)
        self.acc = 0.0
        self.pressed = []

    def start(self):
        self.started = True

    def press(self, what):                       # (--auto: the tool's own Play / Stop)
        self.pressed.append(what)

    def read(self):
        self.reads += 1
        c = self.c
        for _ in range(10):
            c["ms"] += 100
            sec = c["ms"] / 1000.0
            run = self.source != "none" and self.play_at < sec <= self.play_at + self.play_for
            if run and not c["host_run"]:
                c["rt_fa"] += self.source in ("real", "late")
            if not run and c["host_run"]:
                c["rt_fc"] += self.source in ("real", "late")
            c["host_run"] = int(run)
            if run:
                self.acc += self.bpm * 24 / 60 / 10
                k, self.acc = int(self.acc), self.acc - int(self.acc)
                real = self.source == "real" or (self.source == "late" and sec > self.play_at + 2)
                c["rt_f8" if real else "f8_synth"] += k
                c["bpm10"], c["clklock"] = int(self.bpm * 10), 1
            self.rows.append(dict(c))
        return [dict(r) for r in self.rows]

    def stop(self):
        self.stopped = True


def clock_verdict(rows):
    """from stats.log's rows: the playing span (host_run 1, or pulses counted), the real F8 a second there (from 3 s
    into it) against the host's tempo x 24 / 60, the pulses made, FA / FC seen. (ok, report): PASS when the real clock arrives (the
    bridge then makes none, after the first ~2 s of AUTO's wait at most)"""
    rep = {"rows": len(rows)}
    if len(rows) < 2:
        rep["verdict"] = "FAIL: no dev_stats read (stats.log empty: an older build without dev_stats_log?)"
        return False, rep
    play = [i for i in range(1, len(rows)) if rows[i].get("host_run") or rows[i]["rt_f8"] > rows[i - 1]["rt_f8"]
            or rows[i]["f8_synth"] > rows[i - 1]["f8_synth"]]
    d = lambda k: rows[-1].get(k, 0) - rows[0].get(k, 0)   # noqa: E731
    rep.update(rt_fa=d("rt_fa"), rt_fb=d("rt_fb"), rt_fc=d("rt_fc"), rt_f8=d("rt_f8"), f8_synth=d("f8_synth"))
    if not play:
        rep["verdict"] = "FAIL: the transport never ran (host_run stayed 0, no F8 came or was made: Play not pressed?)"
        return False, rep
    i0, i1 = play[0], play[-1]
    secs = max(0.1, (rows[i1]["ms"] - rows[i0 - 1]["ms"]) / 1000.0)
    bpm10 = max(r.get("host_bpm10", 0) for r in rows[i0:i1 + 1])
    want = bpm10 / 10.0 * 24 / 60
    tail = [r for r in rows[i0:i1 + 1] if r["ms"] - rows[i0]["ms"] >= 3000]   # (after AUTO's 2 s wait: the rates)
    if len(tail) < 2:
        tail = rows[i0 - 1:i1 + 1]
    ts = max(0.1, (tail[-1]["ms"] - tail[0]["ms"]) / 1000.0)
    real = (tail[-1]["rt_f8"] - tail[0]["rt_f8"]) / ts
    made = (tail[-1]["f8_synth"] - tail[0]["f8_synth"]) / ts
    made_late = tail[-1]["f8_synth"] - tail[0]["f8_synth"]
    rep.update(play_secs=round(secs, 1), host_bpm=bpm10 / 10.0, want_per_s=round(want, 2),
               real_per_s=round(real, 2), made_per_s=round(made, 2), made_after_3s=made_late,
               app_bpm=max(r.get("bpm10", 0) for r in rows[i0:i1 + 1]) / 10.0,
               clklock=max(r.get("clklock", 0) for r in rows[i0:i1 + 1]),
               host_run_seen=any(r.get("host_run") for r in rows))
    if real > 0.9 * want and not made_late:
        rep["source"] = "real"
        rep["verdict"] = "PASS: the Move's own F8 arrive (%.1f a second, want %.1f; FA %d, FC %d); the bridge made " \
                         "%d pulses (its fallback, before the first F8 at most)" % (real, want, rep["rt_fa"],
                                                                                    rep["rt_fc"], rep["f8_synth"])
        return True, rep
    if real < 0.05 * want and made > 0.9 * want:
        rep["source"] = "fallback"
        rep["verdict"] = "FAIL: no F8 from the Move's transport; only the bridge's fallback (mb_clock_block) runs " \
                         "(%.1f pulses made a second, want %.1f; FA %d, FC %d came in)" % (made, want, rep["rt_fa"],
                                                                                          rep["rt_fc"])
        return False, rep
    rep["source"] = "mixed"
    rep["verdict"] = "FAIL: %.1f real F8 and %.1f made a second, want %.1f (%d made after 3 s)" % (
        real, made, want, made_late or 0)
    return False, rep


class PlayButton:
    """--auto: the Move's Play button pressed for the user, through Schwung's test bus (tools/move_ui_drive.py: arm
    sets inject_as_hardware and starts schwung-testd, the previous values kept; disarm puts them back). Play (CC 85)
    toggles the transport: press("play") and press("stop") are the same tap"""

    def _drive(self, *words):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "move_ui_drive.py"), *words],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("move_ui_drive %s: %s" % (" ".join(words), (r.stderr or r.stdout).strip()))
        return r.stdout

    def __enter__(self):
        self._drive("arm")
        return self

    def press(self, what):
        self._drive("seq", "tap:85")

    def __exit__(self, *exc):
        self._drive("disarm")
        return False


def clock_probe(link, d, play_wait=180.0, play_secs=10.0, stop_wait=180.0, poll=1.0, sleep=time.sleep, out=print,
                press=None):
    """dev_stats_log on; "PRESS PLAY NOW" until the transport runs (play_wait s at most), play_secs of it, "PRESS STOP
    NOW" until it stops (stop_wait s); the log off and read; clock_verdict. (ok, report); stats.log kept in d.
    press(what): the tool presses Play / Stop itself (--auto: PlayButton), once each"""
    import json

    def say(s):
        out(s)
        sys.stdout.flush()
        if press and s.startswith(">>> PRESS PLAY"):
            press("play")
        elif press and s.startswith(">>> PRESS STOP"):
            press("stop")

    def running(rows):
        return bool(rows) and len(rows) > 1 and (rows[-1].get("host_run") or rows[-1]["rt_f8"] > rows[-2]["rt_f8"]
                                                 or rows[-1]["f8_synth"] > rows[-2]["f8_synth"])
    os.makedirs(d, exist_ok=True)
    link.start()
    rows, phase = [], "play"
    try:
        sleep(2 * poll)
        rows = link.read()
        say(">>> PRESS PLAY NOW on the Move (the transport; let it play, the tool says when to stop)")
        waited = 0.0
        while not running(rows) and waited < play_wait:
            sleep(poll)
            waited += poll
            rows = link.read()
        if running(rows):
            say("    playing (seen after %.0f s); recording %.0f s" % (waited, play_secs))
            t = 0.0
            while t < play_secs:
                sleep(poll)
                t += poll
                rows = link.read()
            phase = "stop"
            say(">>> PRESS STOP NOW on the Move")
            waited = 0.0
            while running(rows) and waited < stop_wait:
                sleep(poll)
                waited += poll
                rows = link.read()
            sleep(poll)
            rows = link.read()
            say("    stopped" if not running(rows) else "    still running after %.0f s" % stop_wait)
            phase = "done"
        else:
            say("    the transport did not start within %.0f s" % play_wait)
    finally:
        link.stop()
    ok, rep = clock_verdict(rows)
    if phase == "stop":
        ok, rep["verdict"] = False, "FAIL: the transport did not stop; " + rep["verdict"]
    with open(os.path.join(d, STATS_LOG), "w") as f:
        for r in rows:
            f.write(" ".join("%s=%d" % kv for kv in r.items()) + "\n")
    json.dump(rep, open(os.path.join(d, "report.json"), "w"), indent=1)
    say("clock-probe: %s" % {k: v for k, v in rep.items() if k != "verdict"})
    say(rep["verdict"])
    return ok, rep


RESTRIKE_KEYS = (60, 67, 74, 81, 88, 95, 102, 108)    # (spread over the HQ live contact's range, keys >= 60)
RESTRIKE_CHORD = (28, 33, 38, 43, 47, 52, 55, 59)      # (8 held under the pedal: with the 8 above, 16 voices)
RESTRIKE_VELS = (127, 64, 30)
RESTRIKE_LIMIT_US = 600                                # (A's spec: the worst callback with the live contacts)


def restrike_events(secs=30.0, seed=0):
    """the HQ re-strike stress (A's spec, HQ item 4): the pedal down, a chord of 8 held on keys 28-59; the 8 keys
    of RESTRIKE_KEYS struck once, then every 30-50 ms one of them (in turn) struck again while it rings: key up
    20 ms before (a note-on of a held key is no re-strike), velocities 127 / 64 / 30 in turn; one time in six 3 or
    4 keys at once (more than the 2 live contacts a block: the rest take the table). Then every key up, the pedal
    up. (events, the pedal's let-up time, duration)"""
    rng = np.random.default_rng(seed)
    ev = [(0.1, "cc", 64, 127)] + [(0.2, "on", n, 100) for n in RESTRIKE_CHORD]
    ev += [(0.3 + 0.03 * i, "on", n, 100) for i, n in enumerate(RESTRIKE_KEYS)]
    t, k, v, end = 0.8, 0, 0, 0.8 + secs
    last = {n: 0.3 + 0.03 * i for i, n in enumerate(RESTRIKE_KEYS)}
    while t < end:
        m = int(rng.choice((3, 4))) if rng.random() < 1 / 6 else 1
        for _ in range(m):
            n = RESTRIKE_KEYS[k % len(RESTRIKE_KEYS)]
            k += 1
            if t - last[n] < 0.045:              # (its own last strike too close: the next key)
                continue
            ev += [(t - 0.020, "off", n, 0), (t, "on", n, RESTRIKE_VELS[v % 3])]
            v += 1
            last[n] = t
        t += float(rng.uniform(0.030, 0.050))
    ev += [(end + 0.2, "off", n, 0) for n in RESTRIKE_CHORD + RESTRIKE_KEYS]
    ev += [(end + 0.5, "cc", 64, 0)]
    return sorted(ev, key=lambda e: e[0]), end + 0.5, end + 2.5


def usb_restrike(r, d, secs=30.0, listen_secs=8.0, seed=0, out=print):
    """the HQ re-strike stress on the Rhodes (F01, HQ on; over the tap with the CPU read: every callback's time,
    against RESTRIKE_LIMIT_US): no glitch, never louder after the pedal's let-up. Then the listening pair: the
    phrase's first listen_secs with HQ on and with HQ off (restrike-listen-hq1.wav / -hq0.wav, for an A/B); the HQ
    switch is left on"""
    rep, bad = {"secs": secs}, []
    ev, up, dur = restrike_events(secs, seed)
    r._send("cc", 123, 0)
    for c, v in GLOBAL_CCS:
        r._send("cc", c, v)
    r._send("pc", 0, 0)
    if hasattr(r, "set_hq"):
        r.set_hq(1)
    cpu = hasattr(r, "cpu_start") and getattr(r, "cpu_fetch", None) is not None
    if cpu:
        r.cpu_start()
    try:
        tk = r.rec(dur, ev)
    finally:
        rows = r.cpu_stop() if cpu else []
    _save(d, "restrike-hq1", tk)
    x, sr = tk["x"], tk["sr"]
    g = glitches(x, sr, ev)
    louder = _after_keyup(x, sr, up, 1.0)
    rep.update(notes=sum(1 for e in ev if e[1] == "on"), restrikes=sum(1 for e in ev if e[1] == "on") - 16,
               peak_dbfs=round(20 * np.log10(np.abs(x).max() / 32767 + 1e-12), 1), glitches=g,
               louder_after_up_db=round(float(louder), 2), xruns=tk["xruns"])
    if cpu:
        c = cpu_summary(rows, RESTRIKE_LIMIT_US)
        rep["cpu"] = c
        if not c["n"]:
            bad.append("no callback times (dev_cpu_log reached the plugin?)")
        elif c["over"] or c["missing"]:
            bad.append("worst callback %d us (%d over %d us, %d missing)" % (c["worst"], c["over"], RESTRIKE_LIMIT_US,
                                                                            c["missing"]))
    if g:
        bad.append("%d glitch(es): %s" % (len(g), g[:3]))
    if louder > 1.0:
        bad.append("louder after the pedal's let-up by %.1f dB" % louder)
    if tk["xruns"]:
        bad.append("the take: %s" % tk["xruns"])
    lev = [e for e in ev if e[0] < listen_secs] + [(listen_secs, "off", n, 0) for n in RESTRIKE_CHORD + RESTRIKE_KEYS]
    lev += [(listen_secs + 0.3, "cc", 64, 0)]
    for hq in ((0, 1) if hasattr(r, "set_hq") else ()):
        r.set_hq(hq)
        r._send("cc", 123, 0)
        _save(d, "restrike-listen-hq%d" % hq, r.rec(listen_secs + 2.0, lev))
    if hasattr(r, "set_hq"):
        r.set_hq(1)
    rep["verdict"] = "FAIL: " + "; ".join(bad) if bad else "PASS"
    out("restrike: %d re-strikes in %.0f s, peak %s dBFS, %s, %d glitch(es), after the let-up %+.1f dB: %s" % (
        rep["restrikes"], secs, rep["peak_dbfs"], "worst %s us" % rep["cpu"].get("worst") if cpu else "no CPU read",
        len(g), louder, rep["verdict"]))
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


PHRASE = [(36, 40), (36, 127), (60, 40), (60, 100), (60, 127), (84, 40), (84, 127), (96, 90)]   # (note, velocity)
# (one by one; 96 v90: a key over HQ_OS_LO at a velocity between the Classic layers, so HQ on and off differ there)
HQ_PROBE = "96 v90"
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


MOVE_HOST = os.path.join(ROOT, "build", "host", "frodhes-move-hq%d")
MOVE_HOST_DEFS = ["-O2", "-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]   # (the Makefile's MOVE_DEFS: the Move's build)


def host_binary(hq=None):
    """the host build to compare with: the Move's (16 voices, FR_HQ: the bars without the FM-1's budget, d6cd6bfe),
    new notes HQ or Classic: hq 0 Classic (p_hq 0, the dev key), else HQ (1, or None: the plugin as it loads, whose
    one engine is the HQ Rhodes)"""
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    h = 0 if hq == 0 else 1
    return hostrun.build(MOVE_HOST % h, extra=MOVE_HOST_DEFS + ["-DHQ_DEFAULT=%d" % h])


def host_phrase(ev, dur, program=0, hq=None, lead=(), after=()):
    """the host build (tests/host.c) playing the events after program change program (F01 = 0): (n, 2) LSB at 44.1;
    hq: 0 Classic, else (1, None: as the plugin loads) the HQ Rhodes' build (host_binary); lead: [(cc, value)] at 1 ms, before the program change, as the take
    has them (a pedal let up at boot is not nothing: CC 64 0 moves a later note by a few LSB); after: [(cc, value)] at
    6 ms, after the program change (the values Schwung's scene locks hold on the Move: held_reader)"""
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    pre = 500
    lines = (["1 midi 0xB0 %d %d" % cv for cv in lead] + ["5 midi 0xC0 %d" % program] +
             ["6 midi 0xB0 %d %d" % cv for cv in after])
    for t, k, a, b in ev:
        ms = pre + int(round(1000 * t))
        lines.append("%d midi 0x%02X %d %d" % (ms, {"on": 0x90, "off": 0x80, "cc": 0xB0}[k], a, b))
    run = hostrun.run("move-same-as-host" + ("" if hq == 0 else "-hq"), lines, pre + int(1000 * dur) + 20,
                      binary=host_binary(hq))
    i0 = int(pre * hostrun.FS / 1000)
    return np.stack([np.asarray(run.L[i0:], float), np.asarray(run.R[i0:], float)], 1)


def _lag(a, b, maxlag):
    """the lag (frames) of a against b (a[i + lag] ~ b[i]) by cross-correlation (FFT)"""
    n = len(a) + len(b)
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.r_[np.arange(0, maxlag + 1), np.arange(-maxlag, 0)]
    vals = np.r_[c[:maxlag + 1], c[-maxlag:]]
    return int(lags[int(np.argmax(vals))])


def _strikes(x, sr, thr=0.2, k=2.5):
    """the times (ms from the note's start) of the strikes after the first in a held note x: a sharp step in the
    sample-to-sample difference (2 ms peaks) over thr of the note's own attack and k times the 10 ms before it, with
    the level rising (the next 10 ms 3 dB over the 20 ms before: a sound that stops is no strike)"""
    x = np.asarray(x, float)
    d = np.abs(np.diff(x))
    hop = int(0.002 * sr)
    e = np.array([d[i:i + hop].max() for i in range(0, len(d) - hop, hop)])
    if len(e) < 11:
        return []
    p0, out, last = e[:10].max(), [], 0.0
    for i in range(10, len(e)):
        t = i * hop / sr
        if e[i] > thr * p0 and e[i] > k * e[max(0, i - 5):i].max() and t - last > 0.05:
            j, w = i * hop, int(0.01 * sr)
            after, before = np.sqrt(np.mean(x[j:j + w] ** 2)), np.sqrt(np.mean(x[max(0, j - 2 * w):j] ** 2))
            if after > 1.41 * before:
                out.append(round(1000 * t))
                last = t
    return out


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


ALIGN_TIE = (1e-7, 4.0, 0.002)   # usb-host: tail correlations within 4 x (1 - the best), at most 0.002, of the best
                                 # are one tie (the 2026-10-30 take: the best 0.9998 one period off, the true lag
                                 # 0.9995). An exact tail (the tap, a take with a foreign sound on the attack only)
                                 # has no tie: its best is the lag
ALIGN_ENV_S = (0.001, 0.3)       # the tie broken on the note's level envelope (1 ms RMS) over its first 0.3 s


def _env(x, sr):
    w = max(1, int(ALIGN_ENV_S[0] * sr))
    return np.sqrt(np.convolve(np.asarray(x, float) ** 2, np.ones(w) / w, "same"))


def _align(mv, ref, a, b, ta, tb, lo, hi):
    """the lag of the take mv against the host's ref for one note (a..b, its held second; ta..tb, the window judged:
    the tail): (tail correlation, lag). The lag is picked on the tail, where a short foreign sound at the attack
    has died. A tail that is a steady tone correlates almost as well one period off as on time (the 2026-10-30
    USB-C run: 96 v90 at 2093 Hz came 14 periods late, 6.7 ms, and its attack left -29.7 dB of "another sound"; 36
    v127 one period late). So every lag within the tie (ALIGN_TIE) of the best tail correlation is a candidate,
    and the one whose level envelope matches the host's onset best wins: the onset is where the note starts, and
    a foreign sound struck with the key starts there too (its waveform, at the note's own pitch, would pull a
    waveform match off by whole periods)"""
    rt = _mono(ref[ta:tb])
    ne = min(b - a, int(ALIGN_ENV_S[1] * FS))
    re_ = _env(_mono(ref[a:a + ne]), FS)
    cs = []
    for lag in range(lo, hi):
        if ta + lag < 0 or tb + lag > len(mv) or a + lag < 0 or b + lag > len(mv):
            continue
        mt = _mono(mv[ta + lag:tb + lag])
        cs.append((float(np.dot(mt, rt) / (np.linalg.norm(mt) * np.linalg.norm(rt) + 1e-9)), lag))
    if not cs:
        return -2.0, 0
    top = max(c for c, _ in cs)
    tie = min(ALIGN_TIE[2], max(ALIGN_TIE[0], ALIGN_TIE[1] * (1.0 - top)))
    tied = [(c, lag) for c, lag in cs if c >= top - tie]
    if len(tied) == 1:
        return tied[0]
    best = (-3.0, 0)
    for c, lag in tied:
        me = _env(_mono(mv[a + lag:a + lag + ne]), FS)
        w = float(np.dot(me, re_) / (np.linalg.norm(me) * np.linalg.norm(re_) + 1e-9))
        if w > best[0]:
            best = (w, lag)
    # (the envelope finds the onset to within its 1 ms, not a high note's period: the tail decides within that)
    near = int(ALIGN_ENV_S[0] * FS)
    return max((c, lag) for c, lag in tied if abs(lag - best[1]) <= near)


HQ_RESOLVE_DB = 3.0        # usb-host: the HQ probe judges Classic only when the take's floor is this far under the
                           # HQ / Classic difference at the probe
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
    for c, v in GLOBAL_CCS:                     # (the host boots with these off; the Move keeps what was saved)
        r._send("cc", c, v)
    if hasattr(r, "boot_settings"):
        r.boot_settings()
    r._send("pc", program, 0)
    time.sleep(0.5)
    # (Schwung's scene locks (the set's scenes.json) put their parameters back after the program change: the take
    # plays the held values, so the host gets them too. F13's BARK 52 played as the held 64: DIFFERS, 2026-10-30)
    locked = r.held() if callable(getattr(r, "held", None)) else {}
    after = [(cc, v) for _, (cc, v) in sorted(locked.items()) if cc]
    tk = r.rec(dur, ev)
    _save(d, "move-phrase", tk)
    if locked:
        import json
        json.dump({k: list(v) for k, v in locked.items()}, open(os.path.join(d, "held.json"), "w"))
    mv, sr = tk["x"], tk["sr"]
    hq = getattr(r, "hq", None)
    h = host_phrase(ev, dur, program, hq, GLOBAL_CCS, after)
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
        corr, lag = _align(mv, h, a, b, ta, tb, -int(0.01 * sr), maxlag)
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
        hs_ = _strikes(hm[held], sr)
        again = [t for t in _strikes(mm[held], sr) if all(abs(t - u) > 15 for u in hs_)]
        rows.append({"note": name, "lag_ms": round(1000 * lag / sr, 2), "tail_corr": round(corr, 4), "again_ms": again,
                     "residual_db": round(float(rd), 1), "tail_dbfs": round(float(tail), 1),
                     "judged": bool(tail >= TAIL_JUDGED_DBFS), "extra_db": round(float(ex_db), 1), "extra_hz": peaks,
                     "f0_move_c": round(1200 * np.log2(fm / note_hz(n)), 2) if fm else None,
                     "f0_host_c": round(1200 * np.log2(fh / note_hz(n)), 2) if fh else None,
                     "decay_move_s": _decay_s(mm, sr), "decay_host_s": _decay_s(hm, sr),
                     "centroid_move": round(_centroid(mm[st], sr)), "centroid_host": round(_centroid(hm[st], sr))})
    classic_db, classic_res_db = None, None
    if hq != 0:                                  # (HQ, the load's or asked: the take must not be Classic where only HQ
        c = host_phrase(ev, dur, program, 0, GLOBAL_CCS, after)    # differs)
        for name, t, n in marks:
            if name == HQ_PROBE:
                a, b = int(t * sr), int((t + NOTE_S) * sr)
                ta = a + int(0.5 * sr)

                def fit(ref):                        # (the take against one host: its own best lag, as the rows')
                    _, lag_ = _align(mv, ref, a, b, ta, b, -int(0.01 * sr), maxlag)
                    m_ = _mono(mv[a + lag_:b + lag_])
                    return m_, m_ - g * _mono(ref[a:b])
                m_, dm = fit(h)
                mc, dc = fit(c)
                vs = 20 * np.log10((np.sqrt(np.mean(dc ** 2)) + 1e-9) / (np.sqrt(np.mean(dm ** 2)) + 1e-9))
                rc = 20 * np.log10((np.sqrt(np.mean(dc ** 2)) + 1e-9) / (np.sqrt(np.mean(mc ** 2)) + 1e-9))
                hc = _mono(h[a:b]) - _mono(c[a:b])
                # (only where HQ changes the sound at the probe: the Rhodes and the HQ bars; else both hosts the same)
                classic_db = round(float(vs), 1) if np.abs(hc).max() > 0 else None
                classic_res_db = round(float(rc), 1)
                # (what the take can tell apart: its own floor, the nearer of its two residuals under the note, must
                # be HQ_RESOLVE_DB under the two hosts' difference there, each aligned on its own as fit() aligns
                # the take: Classic's 96 v90 is HQ's 31 frames later within -50 dB. A take whose floor is -39 dB
                # (the 2026-10-30 one: Main Out 17 dB down, the note on another block phase than the host's)
                # cannot: it read 1.1 dB nearer Classic)
                hh_ = _mono(h[a:b])
                hc_db = min(20 * np.log10((np.sqrt(np.mean((hh_ - _mono(c[a + k:b + k])) ** 2)) + 1e-9) /
                                          (np.sqrt(np.mean(hh_ ** 2)) + 1e-9))
                            for k in range(-64, 65) if a + k >= 0 and b + k <= len(c))
                fl_db = 20 * np.log10((min(np.sqrt(np.mean(dm ** 2)) / (np.sqrt(np.mean(m_ ** 2)) + 1e-9),
                                           np.sqrt(np.mean(dc ** 2)) / (np.sqrt(np.mean(mc ** 2)) + 1e-9))) + 1e-12)
                probe = {"hq_classic_db": round(float(hc_db), 1), "floor_db": round(float(fl_db), 1),
                         "resolved": bool(fl_db < hc_db - HQ_RESOLVE_DB)}
    rep = {"program": program, "hq": hq, "gain": round(g, 5), "gain_db": round(20 * np.log10(abs(g) + 1e-12), 2),
           "rows": rows, "peak_move": float(np.abs(mv).max()), "peak_host": float(np.abs(h).max())}
    if locked:
        rep["held"] = {k: v for k, (_, v) in locked.items()}
    bad = [w["note"] for w in rows if w["residual_db"] > -40 and w["judged"]]
    extra = [w["note"] for w in rows if w["extra_db"] > -30]
    again = ["%s +%s ms" % (w["note"], "/".join(str(t) for t in w["again_ms"])) for w in rows if w["again_ms"]]
    classic = False
    if hq != 0:
        rep["hq_vs_classic_db"] = classic_db     # (the take's residual against Classic over the one against HQ)
        # (Classic: nearer the Classic host than the HQ one, and the Classic host's sound itself (under -30 dB). The
        # two hosts differ by ~-35 dB at the probe, so an exact HQ take is ~+100 dB nearer HQ and a take through the
        # Move's volume and latency ~+13 dB; a take that is neither, another sound or a note struck again, gets its
        # own verdict below)
        classic = classic_db is not None and classic_db < 0.0 and classic_res_db < -30.0
        if classic_db is not None:
            rep["hq_probe"] = probe
            classic = classic and probe["resolved"]
    rep["verdict"] = ("CLASSIC: the HQ Rhodes was due (the load's, or p_hq 1) but %s plays as Classic (the Classic host %.1f dB "
                      "nearer than the HQ one: an old dsp.so, or p_hq 0 left on?)"
                      % (HQ_PROBE, -rep["hq_vs_classic_db"])
                      if classic else
                      "STRUCK AGAIN: %s (a note the host strikes once comes again while it is held: the "
                      "arpeggiator or chord mode on (a build without MB_CC_DROP), or the schedule played twice)" % ", ".join(again)
                      if again else
                      "PASS: the host's sound (one gain %.2f dB)" % rep["gain_db"] if not bad and not extra else
                      "DIFFERS at %s (residual over -40 dB after one gain, 0.5-1.0 s into the note)" % bad if bad else
                      "FRODHES = the host (tails), but ANOTHER SOUND plays with the keys at %s (a Move instrument "
                      "on the track, or another slot): silence it and rerun" % extra)
    if "hq_probe" in rep and not rep["hq_probe"]["resolved"] and rep["verdict"].startswith("PASS"):
        rep["verdict"] += ("; HQ against Classic not resolved at this level (the take's floor %.1f dB, HQ and Classic "
                           "%.1f dB apart at %s): the tap path checks it" % (rep["hq_probe"]["floor_db"],
                                                                         rep["hq_probe"]["hq_classic_db"], HQ_PROBE))
    if locked:
        rep["verdict"] += ("; Schwung's scene locks hold %s over the program change: the host plays the held values"
                           % ", ".join("%s (CC %d %d)" % (k, cc, v) if cc else "%s (no CC: not in the host)" % k
                                       for k, (cc, v) in sorted(locked.items())))
    out("gain Move/host %.2f dB (peak Move %d, host %d LSB)" % (rep["gain_db"], rep["peak_move"], rep["peak_host"]))
    for w in rows:
        out("%-8s lag %6.2f ms (tail corr %.4f)  tail residual %6.1f dB  extra %6.1f dB %s  f0 %+6.2f / %+6.2f c  "
            "decay %s / %s s  centroid %d / %d Hz" % (
                w["note"], w["lag_ms"], w["tail_corr"], w["residual_db"] if w["judged"] else float("nan"), w["extra_db"], w["extra_hz"],
                w["f0_move_c"] or 0, w["f0_host_c"] or 0, w["decay_move_s"], w["decay_host_s"],
                w["centroid_move"], w["centroid_host"]))
    out(rep["verdict"])
    return not bad and not extra and not again and not classic, rep


def usb_host_file(d, out=print):
    """usb-host's analysis again on a saved run (move-phrase.wav, host-phrase.wav in d)"""
    import wave

    def rd(p):
        w = wave.open(p)
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float), w.getframerate()

    m, sr = rd(os.path.join(d, "move-phrase.wav"))
    h, _ = rd(os.path.join(d, "host-phrase.wav"))

    import json
    try:
        kept = {k: tuple(v) for k, v in json.load(open(os.path.join(d, "held.json"))).items()}
    except OSError:
        kept = {}

    class Saved(FakeRig):
        def rec(self, dur, events):
            return {"x": m, "sr": sr, "xruns": []}

        def held(self):
            return kept
    global host_phrase
    keep = host_phrase
    # (the saved host is the one the take was compared with; the HQ probe's Classic host is rendered again)
    host_phrase = lambda ev, dur, program=0, hq=None, lead=(), after=(): (   # noqa: E731
        keep(ev, dur, program, 0, lead, after) if hq == 0 else h)
    try:
        return usb_host(Saved(), os.path.join(d, "reanalysis"), out=out)
    finally:
        host_phrase = keep


IDLE_FLOOR_DB = -80.0                       # the digital Main Out with nothing playing: RMS under this (silence, dither)
IDLE_PEAK_LSB = 128                         # ... and its peak under this (the clean floor: 0-30 LSB)


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
    if floor < IDLE_FLOOR_DB and peak < IDLE_PEAK_LSB:
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


def idle_check(r, out=print, secs=1.0):
    """the Move's USB-C stream must be its digital Main Out: with nothing playing it is (near) silent (idle_verdict:
    RMS under IDLE_FLOOR_DB and the peak under IDLE_PEAK_LSB). Nothing reads the setting itself: Schwung's
    master_fx:usbc_out_source sees only the 37 14 half, not the monitoring bit whose loss is the silent revert to the
    mic (docs/SPI_PROTOCOL.md). Waits for silence first (up to 5 s). (ok, verdict)"""
    r._send("cc", 123, 0)
    r._send("cc", 64, 0)
    time.sleep(0.5)
    r.wait_silence(5.0, IDLE_FLOOR_DB)
    tk = r.rec(secs, [])
    v = idle_verdict(tk["x"], tk["sr"])
    out("idle: %.1f dBFS RMS, peak %d LSB, rumble %+.1f dB, L == R %.0f %%: %s" % (
        v["floor_db"], v["peak"], v["lf_db"], 100 * v["same_lr"], SAY[v["kind"]]))
    return v["kind"] == "digital", v


# ---- the preflight (2026-10-29): a Move sampler on Frodhes' track, its sample recorded noise, played with every note
# that came over the Move's MIDI path; the tap (Frodhes' own output) was exact zero and the tools passed. So before a
# sound or level verdict: (1) Main Out idle, (2) one note over the Move's MIDI path, Main Out against the tap of the
# same moment (a passive take: dev_play with no events, which writes whatever the plugin plays), (3) what the slot
# and the Move's track hold. The slot's receive channel is read first: the notes go out on it
PF_NOTE, PF_VEL = 60, 100
PF_ON, PF_OFF, PF_DUR = 0.2, 1.2, 2.5         # (s, from the Main Out take's start)
PF_RESID_DB = -40.0                           # the residual after the gain, re the note's level: over it, another sound
PF_RESID_DBFS = -66.0                         # the residual's own level (dBFS RMS): over it, another sound
PF_CORR_MIC = 0.9                             # Main Out against the tap under this: not the digital Main Out
PF_GAIN_DB_MIN = -40.0                        # Main Out's gain on the tap under this: Frodhes is not on Main Out
MIC_SAY = ("USB-C audio is the microphone: set Settings > USB-C audio out > Main Out (the Move resets it at boot)")
PLAY_SAY = ("Main Out not silent with no note: something else is playing on the Move (another instrument on the "
            "track, a clip, input monitoring)")
FOREIGN_SAY = "sound on Main Out that is not Frodhes"
TRIM = 5279 / 4096.0                          # move/move_bridge.h MB_TRIM_Q12: Main Out = the tap x TRIM x the mix


def parse_chain_config(text, slot=0):
    """Schwung's shadow_chain_config.json: the slot's receive channel (0 All, 1-16), its forward channel (-2 THRU,
    -1 auto, 0-15) and volume, or {} when the file does not say"""
    import json
    try:
        j = json.loads(text)
    except ValueError:
        return {}
    out = {}
    chs, fw, vol = j.get("slot_channels"), j.get("slot_forward_channels"), j.get("slot_volumes")
    if isinstance(chs, list) and slot < len(chs):
        c = int(chs[slot])                    # (internal: -1 All, 0-15)
        out["receive_channel"] = 0 if c < 0 else c + 1
    if isinstance(fw, list) and slot < len(fw):
        out["forward_channel"] = int(fw[slot])
    if isinstance(vol, list) and slot < len(vol):
        out["volume"] = float(vol[slot])
    return out


def parse_song(text, track=0):
    """the Move's saved set (Song.abl, JSON): track's MIDI input ("auto", or the channels 1-16), its instruments
    (kind, name, the sample loaded or none) and its mixer volume; {} when it does not parse. The file is the set as
    last saved: the live state can differ"""
    import json
    try:
        t = json.loads(text)["tracks"][track]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}
    devs = []

    def walk(o):
        if isinstance(o, dict):
            if "kind" in o and ("parameters" in o or "devices" in o or "chains" in o):
                dd = o.get("deviceData") or {}
                devs.append({"kind": o["kind"], "name": o.get("name") or "",
                             **({"sample": dd.get("sampleUri")} if "sampleUri" in dd else {})})
            for k in ("devices", "chains"):
                walk(o.get(k))
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(t.get("devices"))
    mode = t.get("midiInputMode")
    return {"midi_in": mode if isinstance(mode, str) else [int(c) + 1 for c in (mode or [])],
            "devices": devs, "volume_db": (t.get("mixer") or {}).get("volume")}


def scene_locks(text, slot=0):
    """Schwung's scene locks (set_state/<set>/scenes.json) on slot's Frodhes: the keys, sorted. Schwung puts a locked
    parameter back after any change, a program change's too (2026-10-30: F13's BARK 52 read 64 a second later, the
    locks the user's own); [] when the file does not parse or locks nothing there"""
    import json
    try:
        j = json.loads(text)
    except ValueError:
        return []
    keys = set()
    for h in (j.get("halves") or []) if isinstance(j, dict) else []:
        for k in (h.get("locks") or []) if isinstance(h, dict) else []:
            if (isinstance(k, dict) and k.get("scope") == "slot%d" % slot and k.get("target") == "synth"
                    and k.get("module") in (None, "frodhes") and isinstance(k.get("param"), str)):
                keys.add(k["param"])
    return sorted(keys)


def move_keys():
    """the plugin's keys (move/module/move_ui.h MOVE_KEYS, generated): {key: (cc, kind, scale, off)}; a parameter's
    CC value is wire * scale + off"""
    txt = open(os.path.join(ROOT, "move", "module", "move_ui.h")).read()
    return {m.group(1): (int(m.group(2)), m.group(3), int(m.group(4)), int(m.group(5)))
            for m in re.finditer(r'\{"(\w+)", \w+, (\d+), MK_(\w+), (\d+), (-?\d+)\}', txt)}


def held_cc(key, wire, keys=None):
    """a held key's value as its CC (cc, value), (0, wire) when it has none the host takes (a latch, a slot)"""
    k = (keys or move_keys()).get(key)
    try:
        w = int(float(wire))
    except (TypeError, ValueError):
        return 0, wire
    if not k or k[1] != "PARAM" or not k[0]:
        return 0, w
    return k[0], max(0, min(127, w * k[2] + k[3]))


def held_reader(url, slot, locks):
    """usb_host's r.held: the locked keys' live values over Schwung's WebSocket, {key: (cc, value)}"""
    def held():
        import move_ws
        with move_ws.Remote(url) as w:
            w.subscribe(slot)
            w.pump(0.5)
            p = w.params.get(slot, {})
        keys = move_keys()
        return {k: held_cc(k, p.get(COMP_KEY + k), keys) for k in locks if COMP_KEY + k in p}
    return held


COMP_KEY = "synth:"


def slot_report(url, host, slot=0, ssh=None, out=print):
    """what Schwung and the Move show of the slot and its track: over the WebSocket the chain (synth, audio FX, MIDI
    FX: module ids), the slot's receive / forward channel, volume, mute and Frodhes' CHAN (s_mch); over ssh Schwung's
    shadow_chain_config.json (the channels again, when the WebSocket gave none) and the Move's saved set (the
    track's MIDI input and instruments). Each source that cannot be read says so in "errors". {"ws", "config",
    "track", "errors"}"""
    import move_ws
    rep = {"ws": {}, "config": {}, "track": {}, "errors": []}
    if url and url != "none":
        try:
            with move_ws.Remote(url) as w:
                w.subscribe(slot)
                try:                              # (the slot's own keys can come in a later update than the preset)
                    w.until(lambda r: "slot:receive_channel" in r.params.get(slot, {}), 2.0)
                except TimeoutError:
                    pass
                w.pump(0.5)
                i, p = w.info.get(slot, {}), w.params.get(slot, {})
            rep["ws"] = {"synth": i.get("synth", ""), "fx": i.get("fx") or [x for x in (i.get("fx1"), i.get("fx2")) if x],
                         "midi_fx": i.get("midi_fx") or [x for x in (i.get("midi_fx1"),) if x],
                         **{k[5:]: p[k] for k in ("slot:receive_channel", "slot:forward_channel", "slot:volume",
                                                  "slot:muted", "slot:soloed") if k in p}}
            if "synth:s_mch" in p:
                rep["ws"]["s_mch"] = p["synth:s_mch"]
        except (OSError, TimeoutError, move_ws.SchwungError, move_ws.ConnectionClosed) as e:
            rep["errors"].append("Schwung's WebSocket %s: %s" % (url, e))
    else:
        rep["errors"].append("Schwung's WebSocket: not used (--ws none)")
    if ssh is None and host:
        import subprocess

        def ssh(cmd):
            r = subprocess.run(["ssh", *mi.SSH_OPTS, host, cmd], capture_output=True, text=True, timeout=20)
            return r.stdout if r.returncode == 0 else None
    if ssh is None:
        rep["errors"].append("ssh: no host")
        return rep
    txt = ssh("cat /data/UserData/schwung/shadow_chain_config.json")
    rep["config"] = parse_chain_config(txt or "", slot)
    if not rep["config"]:
        rep["errors"].append("shadow_chain_config.json: not read")
    uid = (ssh("head -n 1 /data/UserData/schwung/active_set.txt") or "").strip()
    song = ssh('cat /data/UserData/UserLibrary/Sets/%s/*/Song.abl' % uid) if re.match(r"^[0-9a-f-]{8,}$", uid) else None
    rep["track"] = parse_song(song or "", slot)
    if not rep["track"]:
        rep["errors"].append("the Move's set (active_set.txt -> Song.abl): not read")
    sc = ssh("cat /data/UserData/schwung/set_state/%s/scenes.json" % uid) if re.match(r"^[0-9a-f-]{8,}$", uid) else None
    rep["locks"] = scene_locks(sc or "", slot)
    return rep


def resolve_channel(rep, ch=None, slot=0):
    """from slot_report: the channel to send the Move's MIDI path on, and the one Frodhes sees. ch: --ch (None: the
    slot's own receive channel). {"send", "plugin", "recv", "notes", "stop"}: stop is a message when the notes would
    not reach Frodhes, or nothing says where they go"""
    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
    w, c = rep.get("ws", {}), rep.get("config", {})
    recv = num(w.get("receive_channel"))
    src = "Schwung's WebSocket"
    if recv is None:
        recv, src = num(c.get("receive_channel")), "shadow_chain_config.json"
    fwd = num(w.get("forward_channel"))
    if fwd is None:
        fwd = num(c.get("forward_channel"))
    mch = num(w.get("s_mch"))
    res = {"recv": recv, "recv_from": src if recv is not None else None, "forward": fwd, "s_mch": mch, "notes": [],
           "stop": None}
    if recv is None:
        if ch is None:
            res["stop"] = ("slot %d's receive channel could not be read (%s); the tools would assume channel %d: "
                           "pass --ch N to send on channel N" % (slot + 1, "; ".join(rep.get("errors") or ["no source"]),
                                                                 USB_CH))
            return res
        res["notes"].append("slot %d's receive channel not read: --ch %d as given" % (slot + 1, ch))
        send = ch
    elif ch is not None and recv not in (0, ch):
        res["stop"] = ("--ch %d, but slot %d receives on channel %d (%s): its notes would not reach Frodhes; pass "
                       "--ch %d or none" % (ch, slot + 1, recv, src, recv))
        return res
    else:
        send = ch if ch is not None else (recv or USB_CH)
        if recv == 0:
            res["notes"].append("slot %d receives every channel (All): sending on %d" % (slot + 1, send))
    # (Schwung's shadow_chain_remap_channel: -2 THRU keeps the channel, -1 auto the receive channel (Frodhes declares no
    # default_forward_channel; All + auto keeps it), 0-15 that one)
    plugin = send if fwd in (None, -2) or (fwd == -1 and not recv) else (recv if fwd == -1 else fwd + 1)
    res.update(send=send, plugin=plugin)
    if mch and mch != plugin:
        res["stop"] = ("Frodhes' CHAN is %d, but slot %d hands it channel %d (receive %s, forward %s): its notes would "
                       "not play" % (mch, slot + 1, plugin, recv, fwd))
    mi_in = rep.get("track", {}).get("midi_in")
    if isinstance(mi_in, list) and mi_in and send not in mi_in:
        res["notes"].append("the Move's track %d takes MIDI in on channel(s) %s, the slot on %s: they differ (the "
                            "saved set)" % (slot + 1, mi_in, recv))
    elif mi_in is not None:
        res["notes"].append("the Move's track %d MIDI in: %s (the saved set); the slot: %s" % (
            slot + 1, mi_in, "All" if recv == 0 else recv))
    return res


def report_lines(rep, chan):
    """slot_report and resolve_channel as lines for the user"""
    w, t, L = rep.get("ws", {}), rep.get("track", {}), []
    if w:
        L.append("slot: synth %r, audio FX %s, MIDI FX %s, receive %s, forward %s, volume %s, muted %s, Frodhes CHAN %s"
                 % (w.get("synth"), w.get("fx") or "none", w.get("midi_fx") or "none", w.get("receive_channel", "?"),
                    w.get("forward_channel", "?"), w.get("volume", "?"), w.get("muted", "?"), w.get("s_mch", "?")))
    elif rep.get("config"):
        L.append("slot (shadow_chain_config.json): %s" % rep["config"])
    if t:
        devs = ", ".join("%s%s%s" % (d["kind"], " %r" % d["name"] if d["name"] else "",
                                     " (sample %s)" % (d["sample"] or "none") if "sample" in d else "")
                         for d in t.get("devices", [])) or "none"
        L.append("the Move's track (the saved set; the live one can differ): MIDI in %s, volume %s dB, devices: %s"
                 % (t.get("midi_in"), t.get("volume_db"), devs))
    if rep.get("locks"):
        L.append("Schwung's scene locks hold %s on this slot (the set's scenes.json): a preset or a CC does not "
                 "move them" % ", ".join(rep["locks"]))
    for e in rep.get("errors", []):
        L.append("cannot read: " + e)
    if chan.get("send"):
        L.append("channel: send %s, Frodhes sees %s (receive %s from %s, forward %s)" % (
            chan["send"], chan.get("plugin"), chan.get("recv"), chan.get("recv_from"), chan.get("forward")))
    L += chan.get("notes", [])
    return L


def _bands3(x, sr):
    """third-octave band powers (dB), 50 Hz .. 12.8 kHz, of x (mono)"""
    return _bands(x, sr)


def tap_vs_main(main, sr, tap, t_on=PF_ON, t_off=PF_OFF):
    """Main Out (n, 2, at sr) against the tap (m, 2, at 44.1 kHz) that covers it: the lag by cross-correlation, then
    the gain per channel by least squares (the Move's trim and its mix, the slot's and the track's volume), the
    residual Main Out - gain x tap from just before the note to the take's end: its level re the note and in dBFS,
    its spectrum's peaks and the third-octave bands where it is within 20 dB of the note. {...}"""
    from scipy.signal import resample_poly
    tap = np.asarray(tap, float)
    main = np.asarray(main, float)
    if sr != FS:
        from math import gcd
        g = gcd(int(sr), FS)
        tap = resample_poly(tap, int(sr) // g, FS // g, axis=0)
    rep = {"sr": sr}
    if not len(tap) or np.abs(tap).max() == 0:
        rep["kind"] = "tap-silent"
        return rep
    a, b = int(max(0.0, t_on - 0.05) * sr), len(main)
    m = main[a:b]
    mm, tm = _mono(m), _mono(tap)
    if len(tm) < len(mm):
        rep["kind"] = "tap-short"
        return rep
    n = len(mm) + len(tm)
    c = np.fft.irfft(np.fft.rfft(tm, n) * np.conj(np.fft.rfft(mm, n)), n)[:len(tm) - len(mm) + 1]
    best = None
    k0 = int(np.argmax(c))
    for k in range(max(0, k0 - 2), min(len(tm) - len(mm), k0 + 2) + 1):   # (the residual's own best near the peak)
        seg = tap[k:k + len(m)]
        gains = [float(np.dot(m[:, j], seg[:, j]) / (np.dot(seg[:, j], seg[:, j]) + 1e-9)) for j in (0, 1)]
        res = m - seg * np.array(gains)
        e = float(np.sum(res ** 2))
        if best is None or e < best[0]:
            best = (e, k, gains, res, seg)
    _, k, gains, res, seg = best
    corr = float(np.dot(mm, _mono(seg)) / (np.linalg.norm(mm) * np.linalg.norm(_mono(seg)) + 1e-9))
    note = m - res                               # (Frodhes on Main Out: the tap through the gain)
    rel = 10 * np.log10((np.sum(res ** 2) + 1e-9) / (np.sum(note ** 2) + 1e-9))
    rm, nm = _mono(res), _mono(note)
    sp = np.abs(np.fft.rfft(rm * np.hanning(len(rm))))
    fr = np.fft.rfftfreq(len(rm), 1 / sr)
    peaks = []
    for i in np.argsort(sp)[::-1]:
        if fr[i] > 20 and all(abs(fr[i] - q) > 15 for q in peaks):
            peaks.append(round(float(fr[i])))
        if len(peaks) == 4:
            break
    br, bn = _bands3(rm, sr), _bands3(nm, sr)
    fc = 50 * 2 ** (np.arange(0, 25) / 3)
    near = [int(round(f)) for f, x, y in zip(fc, br, bn) if x > y - 20 and x > bn.max() - 60]   # (judged: the level)
    g = float(np.mean(np.abs(gains)))
    rep.update(kind="ok", tap_offset_s=round((k - a) / sr, 4), corr=round(corr, 5), gain=[round(x, 4) for x in gains],
               gain_db=round(20 * np.log10(g + 1e-12), 2), mix_db=round(20 * np.log10(g / TRIM + 1e-12), 2),
               resid_db=round(float(rel), 1), resid_dbfs=round(float(hs.db(res)), 1),
               note_dbfs=round(float(hs.db(note)), 1), main_peak=float(np.abs(m).max()),
               resid_peak=round(float(np.abs(res).max()), 1), resid_hz=peaks, resid_bands_hz=near)
    return rep


def tap_verdict(v, idle=None):
    """(ok, text) of tap_vs_main"""
    if v["kind"] == "tap-silent":
        return False, ("the note did not reach Frodhes: its tap stayed silent (the channel? the slot muted? another "
                       "module in the slot?)")
    if v["kind"] != "ok":
        return False, "the tap did not cover the take (%s)" % v["kind"]
    if v["gain_db"] < PF_GAIN_DB_MIN:
        return False, "Frodhes is not on Main Out (gain %.1f dB: the slot or the track muted, its volume at 0?)" % (
            v["gain_db"])
    if v["corr"] < PF_CORR_MIC and (idle or {}).get("kind") in ("mic", None):
        return False, MIC_SAY + " (Main Out against the tap: correlation %.3f)" % v["corr"]
    if v["resid_db"] > PF_RESID_DB or v["resid_dbfs"] > PF_RESID_DBFS:
        return False, ("%s: %.1f dB under the note (%.1f dBFS RMS; limits %g dB, %g dBFS), its peaks at %s Hz%s" % (
            FOREIGN_SAY, -v["resid_db"], v["resid_dbfs"], -PF_RESID_DB, PF_RESID_DBFS, v["resid_hz"],
            ", within 20 dB of the note in the bands at %s Hz" % v["resid_bands_hz"] if v["resid_bands_hz"] else ""))
    return True, "Main Out is Frodhes: residual %.1f dB under the note (%.1f dBFS), gain %.2f dB (trim %+.2f, the mix " \
                 "%+.2f dB)" % (-v["resid_db"], v["resid_dbfs"], v["gain_db"], 20 * np.log10(TRIM), v["mix_db"])


class PassiveTap:
    """the tap of whatever the plugin plays (dev_play with no events): begin(secs) arms a take that covers the next
    secs from now on (it starts on the plugin's 2560 ms grid: begin waits for it), collect() -> (m, 2) at 44.1 kHz or
    None. The same as usb_levels' stock take"""

    def __init__(self, t):
        self.t, self.take = t, None

    def begin(self, secs):
        t = self.t
        t.n += 1
        self.take = "pf%s-%03d" % (t.stamp, t.n)
        t.remote.play(t.slot, self.take, int((TAP_GRID_S + 0.3 + secs + 0.5) * 1000), [])
        time.sleep(TAP_GRID_S + 0.3)

    def collect(self):
        import wave
        time.sleep(1.0)
        got, t0 = None, time.monotonic()
        while got is None and time.monotonic() - t0 < 10:
            got = self.t.fetch(self.take)
            if got is None:
                time.sleep(0.5)
        if not got:
            return None
        w = wave.open(got[0])
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float)


def preflight_run(u, tap, rep, chan, d=None, out=print):
    """the preflight: rep / chan from slot_report / resolve_channel (printed first), (1) idle_check, (2) one note
    over the Move's MIDI path (u: UsbRig on chan["send"]) recorded on Main Out, with the tap of the same moment
    (tap: PassiveTap, or the fake's), tap_vs_main. Stops at the first failure. (ok, report)"""
    import json
    res = {"slot": rep, "channel": chan}
    for ln in report_lines(rep, chan):
        out("preflight: " + ln)
    if chan.get("stop"):
        res["verdict"] = "FAIL: " + chan["stop"]
        out("preflight: " + res["verdict"])
        return False, res
    ok, idle = idle_check(u, out=lambda s: out("preflight: " + s))
    res["idle"] = idle
    if not ok:
        res["verdict"] = "FAIL: " + (MIC_SAY if idle["kind"] == "mic" else PLAY_SAY) + \
            " (%.1f dBFS RMS, peak %d LSB; limits %g dBFS, %d LSB)" % (idle["floor_db"], idle["peak"], IDLE_FLOOR_DB,
                                                                       IDLE_PEAK_LSB)
        out("preflight: " + res["verdict"])
        return False, res
    tap.begin(PF_DUR)
    tk = u.rec(PF_DUR, [(PF_ON, "on", PF_NOTE, PF_VEL), (PF_OFF, "off", PF_NOTE, 0)])
    tx = tap.collect()
    if d:
        _save(d, "preflight-main", tk)
        if tx is not None:
            _save(d, "preflight-tap", {"x": tx, "sr": FS})
    if tx is None:
        res["verdict"] = "FAIL: the tap did not arrive (dev_play: an installed build with it, Frodhes in the slot?)"
        out("preflight: " + res["verdict"])
        return False, res
    v = tap_vs_main(tk["x"], tk["sr"], tx)
    res["tap"] = v
    ok, txt = tap_verdict(v, idle)
    if v.get("kind") == "ok":
        out("preflight: note %d v%d over the Move's MIDI path (channel %s): the tap %.3f s ahead, correlation %.5f, "
            "gain %s (%.2f dB), residual %.1f dB re the note, %.1f dBFS RMS, peak %.0f LSB" % (
                PF_NOTE, PF_VEL, chan.get("send"), v["tap_offset_s"], v["corr"], v["gain"], v["gain_db"],
                v["resid_db"], v["resid_dbfs"], v["resid_peak"]))
    res["verdict"] = ("PASS: " if ok else "FAIL: ") + txt
    out("preflight: " + res["verdict"])
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(_plain(res), open(os.path.join(d, "preflight.json"), "w"), indent=1, default=str)
    return ok, res


def fake_slot_report(recv=1, fwd=-1, mch=0, midi_in="auto"):
    """slot_report as a fake Move answers it (the tests)"""
    return {"ws": {"synth": mi.MODULE_ID, "fx": [], "midi_fx": [], "receive_channel": str(recv),
                   "forward_channel": str(fwd), "volume": "1.0000", "muted": "0", "s_mch": str(mch)},
            "config": {"receive_channel": recv, "forward_channel": fwd, "volume": 1.0},
            "track": {"midi_in": midi_in, "devices": [{"kind": "instrumentRack", "name": "Sampler"},
                                                      {"kind": "melodicSampler", "name": "", "sample": None}],
                      "volume_db": 0.0}, "errors": []}


def channel_for(a, out=print):
    """the device commands' channel: the slot's receive channel read over Schwung (slot_report), --ch when given.
    (chan, rep); a chan with "stop" set ends the command (exit 2)"""
    if a.fake:
        rep = fake_slot_report()
    else:
        rep = slot_report(a.ws, a.host, 0, out=out)
    return resolve_channel(rep, a.ch), rep


def preflight_cmd(a, d, chan, rep, out=print, u=None):
    """the preflight before a device command (and the preflight subcommand): (ok, report). The fake: FakeRig, its own
    tap"""
    if a.fake:
        inj = [(k, float(t or 0)) for k, _, t in (s.partition("@") for s in a.inject or ())]
        u = u if isinstance(u, FakeRig) else FakeRig(inject=inj)
        tap = u
    else:
        u = u or UsbRig(a.midi, a.audio, chan["send"])
        if a.ws == "none":
            return False, {"verdict": "FAIL: the preflight needs Schwung's WebSocket for the tap (--ws), or "
                                      "--no-preflight"}
        tap = PassiveTap(TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), chan["plugin"]))
    try:
        return preflight_run(u, tap, rep, chan, d, out=out)
    finally:
        if isinstance(tap, PassiveTap):
            try:
                tap.t.remote.close()
            except Exception:
                pass



SAY["mic"], SAY["playing"] = MIC_SAY, PLAY_SAY
USB_CMDS = ("usb-devices", "usb-host", "usb-note", "usb-vel", "usb-sus", "usb-clock", "usb-stress")


def clock_rig(a, r, d, chan):
    """the rig usb-clock plays on: r itself when its clock reaches the slot (the tap), else the tap over Schwung's
    WebSocket (a fake: FakeRig(tap=True)); None when there is none (--ws none): usb_clock then refuses r"""
    if getattr(r, "CLOCK_IN", True):
        return r
    if r.fake:
        return FakeRig(inject=getattr(r, "inject", ()), tap=True)
    if a.ws == "none":
        return None
    return TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), chan["plugin"])


def usb_main(a):
    import json
    if a.cmd == "usb-host" and a.analyze:            # (a saved run, offline: no device)
        ok, _ = usb_host_file(a.analyze)
        return 0 if ok else 1
    if a.cmd == "usb-devices" and not a.fake:
        return usb_devices(a.midi, a.audio)
    if a.cmd == "usb-devices":
        r = FakeRig()
        print("picked: MIDI %s, audio %s" % (r.port_name, r.audio_name))
        return 0
    d = a.out or os.path.join(OUT, stamp() + "-" + a.cmd)
    chan, srep = channel_for(a)
    if chan.get("stop"):
        print("STOP: " + chan["stop"])
        return 2
    if a.fake:
        inj = []
        for s in a.inject or ():
            k, _, t = s.partition("@")
            inj.append((k, float(t or 0)))
        r = FakeRig(inject=inj)
    elif a.path == "tap":
        r = TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), chan["plugin"])
    else:
        r = UsbRig(a.midi, a.audio, chan["send"])
    if not r.fake and a.ws != "none" and srep.get("locks"):
        r.held = held_reader(a.ws, 0, srep["locks"])
    print("MIDI %s (channel %d), audio %s; takes -> %s" % (r.port_name, r.ch, r.audio_name, d), flush=True)
    e = a.engine if a.engine in ENGINE_CCS else None
    cr = clock_rig(a, r, d, chan) if a.cmd == "usb-clock" else r
    if cr is not r and cr is not None:
        print("usb-clock over %s: Schwung gives a slot no MIDI clock from USB-C" % cr.port_name, flush=True)
    fn = {"usb-host": lambda: usb_host(r, d, a.program), "usb-note": lambda: usb_note(r, d, a.note, a.vel, e),
          "usb-vel": lambda: usb_vel(r, d, a.note, engine=e),
          "usb-sus": lambda: usb_sus(r, d, a.note, engine=e), "usb-clock": lambda: usb_clock(cr or r, d, a.bpm or 100.0, engine=e),
          "usb-stress": lambda: usb_stress(r, d, a.engine if a.engine != "all" else "rhodes", a.secs, a.nv, a.bpm)}

    def run():
        if not a.no_preflight:                 # (a sound verdict: the preflight first; a failure stops the command)
            ok, pre = preflight_cmd(a, os.path.join(d, "preflight"), chan, srep,
                                    u=r if not isinstance(r, TapRig) else None)
            if not ok:
                return None, {"verdict": "STOP: the preflight failed", "preflight": pre}
        return fn[a.cmd]()
    ks = KeptState(a.ws, 0) if not r.fake and a.ws != "none" else None
    if ks is None:
        ok, rep = run()
    else:
        with ks:                               # (the plugin's state as it was, also on a failure or ctrl-C)
            ok, rep = run()
        rep["state"] = ks.report
        ok = ok if ok is None else ok and ks.report["verdict"] == "PASS"
    rep["rig"] = {"midi": r.port_name, "audio": r.audio_name, "ch": r.ch, "fake": r.fake, "channel": chan}
    os.makedirs(d, exist_ok=True)
    json.dump(_plain(rep), open(os.path.join(d, "report.json"), "w"), indent=1, default=str)
    if ok is None:
        return 2
    return 0 if ok else 1


# ---- the plugin's state, kept: every test that plays into the plugin (its CCs, the preset, boot_settings, CHANNEL,
# p_hq) leaves it as it found it. The plugin saves its settings and the panel, so a test's leftovers came back at the
# next load: the arpeggiator on (2026-10-28 01:42), every effect on with the feedbacks at 127 (usb-stress's HEAVY,
# the user's report of 2026-10-28)
STATE_SKIP = ("preset_count", "preset_name", "s_susbtn", "r_cmp_gr", "r_cmp_pk")   # (meta; momentary: a write is a
#   press; the GR meter's read-only cells move with the sound)


def _same(a, b):
    try:
        return a is not None and b is not None and float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def state_read(url, slot=0, secs=0.8):
    """the slot's params as Schwung reports them (its own connection): {key: value} without the synth: prefix, the
    meta and momentary keys left out"""
    import move_ws
    with move_ws.Remote(url) as w:
        w.subscribe(slot)
        w.pump(secs)
        p = dict(w.params.get(slot, {}))
    return {k[6:]: v for k, v in p.items() if k.startswith("synth:") and k[6:] not in STATE_SKIP
            and not k.startswith("synth:dev_")}


def state_restore(url, slot, before, hq_default=1):
    """writes back every key that differs from before (the preset first: it loads its own values), HQ to the boot
    value (HQ: p_hq, the dev key, is not saved and Schwung does not report it), the dev player stopped, the CPU and stats logs off; then reads
    the state back. (keys written, {key: (before, after)} still different)"""
    import move_ws
    with move_ws.Remote(url) as w:
        w.subscribe(slot)
        w.pump(0.8)
        for k, v in (("dev_play", "stop"), ("dev_cpu_log", 0), ("dev_stats_log", 0), ("p_hq", hq_default)):
            w.set_param(slot, "synth:" + k, v)
        w.pump(1.5)              # (the manager's re-read after these writes goes out under this connection: closed in
        #                          the middle of it, the next connection is cut, "no close frame", 2026-10-29)
        now = lambda: {k[6:]: v for k, v in w.params.get(slot, {}).items() if k.startswith("synth:")}  # noqa: E731
        written = []
        if "preset" in before and not _same(now().get("preset"), before["preset"]):
            w.set_param(slot, "synth:preset", before["preset"])
            written.append("preset")
            w.pump(1.5)                                  # (the re-read of every param, the preset's values in it)
        # (the switches and types first: switching an effect gives its MIX a starting amount, so a MIX written
        # before its switch came back changed, p_dl_depth 0 -> 50 on the Move, 2026-10-28; then every key that still
        # differs is written again, up to 3 passes, each after the manager's re-read)
        first = lambda k: (0 if k.endswith(("_sw", "_type", "_on")) or k == "p_type" else 1, k)   # noqa: E731
        for _ in range(3):
            cur = now()
            todo = sorted((k for k, v in before.items() if k != "preset" and not _same(cur.get(k), v)), key=first)
            if not todo:
                break
            for k in todo:
                w.set_param(slot, "synth:" + k, before[k])
                written.append(k)
                time.sleep(0.003)
            w.pump(1.5)                                  # (the manager's re-read goes out under this connection)
    after = state_read(url, slot)
    bad = {k: (v, after.get(k)) for k, v in before.items() if not _same(after.get(k), v)}
    return written, bad


RESTORE_RETRY_S = 2.0                           # KeptState: the wait before the restore's one new connection


class KeptState:
    """with KeptState(url, slot, out) as ks: ...: the state read first, written back at the end, also on an
    exception, ctrl-C (KeyboardInterrupt) or SIGTERM (raised as SystemExit), and at interpreter exit (atexit) if the
    block never ended. ks.report: {'verdict', 'params', 'written', 'bad'} once restored"""

    def __init__(self, url, slot=0, out=print):
        self.url, self.slot, self.out, self.report, self.before = url, slot, out, None, None

    def __enter__(self):
        import atexit
        import signal
        self.before = state_read(self.url, self.slot)
        atexit.register(self.restore)
        self._sig = None
        try:
            self._sig = signal.signal(signal.SIGTERM, self._term)
        except ValueError:                               # (not the main thread: atexit and finally still hold)
            pass
        return self

    @staticmethod
    def _term(signum, frame):
        raise SystemExit(128 + signum)

    def restore(self):
        if self.report is not None or self.before is None:
            return self.report
        import move_ws
        dropped = []
        try:
            for attempt in (0, 1):
                # (the manager once dropped the restore's own socket, "no close frame", 2026-10-28, after
                # usb-restrike: the write-back is idempotent and the read after it decides, so a dropped socket gets
                # one new connection, named in the report; any other error, or a second drop, fails)
                try:
                    written, bad = state_restore(self.url, self.slot, self.before)
                    break
                except (move_ws.ConnectionClosed, ConnectionError) as e:
                    if attempt:
                        raise
                    dropped.append("%s: %s" % (type(e).__name__, e))
                    time.sleep(RESTORE_RETRY_S)
            self.report = {"verdict": "PASS" if not bad else "FAIL: after the run %d param(s) differ from before: %s"
                           % (len(bad), ", ".join("%s %s -> %s" % (k, a, b) for k, (a, b) in sorted(bad.items())[:8])),
                           "params": len(self.before), "written": written, "bad": bad, "dropped": dropped}
        except Exception as e:                           # (the WebSocket gone: say so loudly, never pass)
            self.report = {"verdict": "FAIL: the state was not restored: %s: %s" % (type(e).__name__, e),
                           "params": len(self.before), "written": [], "bad": {"(restore)": (None, str(e))}}
        self.out("state: " + self.report["verdict"] + (" (%d param(s) as before, %d written back%s)" % (
            self.report["params"], len(self.report["written"]),
            ", after a dropped socket: %s" % self.report["dropped"][0] if self.report.get("dropped") else "")
            if self.report["verdict"] == "PASS" else ""))
        return self.report

    def __exit__(self, *exc):
        import atexit
        import signal
        try:
            self.restore()
        finally:
            atexit.unregister(self.restore)
            if self._sig is not None:
                try:
                    signal.signal(signal.SIGTERM, self._sig)
                except ValueError:
                    pass
        return False


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
        if test == "idle":
            return "idle %s dBFS (%s)" % (rep["floor_db"], rep["kind"])
        if test == "preflight":
            i, t = rep.get("idle") or {}, rep.get("tap") or {}
            return "idle %s dBFS RMS, peak %s LSB; residual %s dB re the note, %s dBFS; gain %s dB; channel %s" % (
                i.get("floor_db", "-"), i.get("peak", "-"), t.get("resid_db", "-"), t.get("resid_dbfs", "-"),
                t.get("gain_db", "-"), (rep.get("channel") or {}).get("send", "-"))
        if test == "ws":
            return rep.get("note", "")
        if test == "state":
            return "%s param(s) read before, %d written back, %d differ after" % (
                rep.get("params", "-"), len(rep.get("written", [])), len(rep.get("bad", {})))
        if test == "usb-host":
            w = rep["rows"]
            j = [x["residual_db"] for x in w if x.get("judged", True)]
            return "gain %.2f dB, residual max %s dB (%d of %d notes judged), extra max %.1f dB, %d struck again%s" % (
                rep["gain_db"], "%.1f" % max(j) if j else "-", len(j), len(w), max(x["extra_db"] for x in w),
                sum(1 for x in w if x.get("again_ms")), {None: "", 0: ", HQ off", 1: ", HQ on"}[rep.get("hq")])
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
        if test == "usb-restrike":
            c = rep.get("cpu") or {}
            return "%d re-strikes, worst callback %s us, p99 %s us, %d glitch(es), after the let-up %+.1f dB" % (
                rep["restrikes"], c.get("worst", "-"), c.get("p99", "-"), len(rep["glitches"]), rep["louder_after_up_db"])
        if test == "cpu":
            if not rep.get("n"):
                return "no data"
            return "%d callbacks while usb-stress played, worst %d us, p99 %d us, %d over %d us, %d missing%s" % (
                rep["n"], rep["worst"], rep["p99"], rep["over"], rep["limit"], rep["missing"],
                "" if rep.get("hq") is None else ", HQ %s" % ("on" if rep["hq"] else "off"))
        if test == "usb-clock":
            return "%s troughs, %s BPM seen, jitter %s ms" % (rep["troughs"], rep.get("bpm_seen"), rep.get("jitter_ms"))
        if test == "usb-stress":
            return "; ".join("%s: %s notes, peak %s dBFS, %d gated" % (t["take"], t["notes"], t["peak_dbfs"], t["gated"])
                             for t in rep["takes"])
    except (KeyError, TypeError, ValueError) as e:
        return "(%s)" % e
    return ""


def _ws_prepare(url, engine, slot=0, out=print, remote=None):
    """slot 1 must hold Frodhes; its preset is set to the engine's (D2_PRESET). (ok, report). remote: an open
    connection to use (the tap rig's), else one of its own, kept open until the manager's re-read has gone out: the
    manager writes that re-read to every subscriber under the connection of the client that switched the preset,
    and a connection closed in the middle of it cuts the others off (no close frame; the tap rig's, 2026-10-28)"""
    import contextlib
    import move_ws
    want = D2_PRESET[engine]
    try:
        with (contextlib.nullcontext(remote) if remote else move_ws.Remote(url)) as w:
            st = w.subscribe(slot)
            if st["module"] != mi.MODULE_ID:
                return False, dict(st, verdict="FAIL: slot %d holds %r, not %s" % (slot + 1, st["module"],
                                                                                    mi.MODULE_ID))
            before = st["preset"]
            st = w.set_preset(slot, want)
            w.pump(1.5)                          # (the re-read: 94 params in ~0.6 s)
    except (OSError, TimeoutError, move_ws.SchwungError, move_ws.ConnectionClosed) as e:
        return False, {"verdict": "FAIL: Schwung's WebSocket %s: %s" % (url, e)}
    st.update(before=before, verdict="PASS",
              note="slot %d %s, preset %s -> %s %s" % (slot + 1, st["module"], before, st["preset"],
                                                       st["preset_name"] or ""))
    out("ws: " + st["note"])
    return True, st


class _Stop(Exception):
    pass


def d2(r, d, engines=D2_ENGINES, ws=None, stress_secs=60.0, bpm=100.0, hq=None, only=None, out=print, pre=None,
       clock=None):
    """per engine: the slot's module and preset over Schwung's WebSocket (ws: its URL, None: skipped), the preflight
    (a failure stops the whole run: the stream is not the Move's Main Out), usb-host on the engine's preset, usb-note
    on three keys, usb-vel, usb-sus, usb-clock (once a run: with the Rhodes engine, else the first; on the rig clock,
    default r: over USB-C it is the tap, clock_rig, as no MIDI clock reaches a slot from USB-C), usb-stress; each
    test's takes and report.json in d/<engine>/<test>/, the table in d/summary.md, everything in d/report.json. Over the tap with a CPU read (TapRig cpu): the cpu row,
    every callback's time while usb-stress plays (the worst case), against U12. hq 0 / 1 (the tap): the HQ switch
    set first (p_hq; None: as the plugin has it). only: the test names to run (a name or its prefix, "usb-note" for all
    three; None: all; the cpu row comes with usb-stress). pre: the preflight (preflight_cmd, called once before the
    engines; its failure stops the run). Returns (exit code: 0 all PASS, 1 a FAIL, 2 stopped)"""
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
    ks = None
    if ws:                                             # (the plugin's state read first, written back at the end)
        try:
            ks = KeptState(ws, 0, out=quiet).__enter__()
        except Exception as x:                         # (OSError, a timeout, the manager's error, a cut socket)
            note("all", "state", False, {"verdict": "FAIL: the state could not be read first: %s: %s" % (
                type(x).__name__, x)})
            stopped = "the plugin's state could not be read over %s" % ws
    try:
        if stopped:
            raise _Stop()
        if pre is not None:
            ok, rep = pre()
            note("all", "preflight", ok, rep)
            if not ok:
                stopped = "the preflight failed: %s" % rep.get("verdict", "")
                raise _Stop()
        for e in engines:
            out("== %s" % e)
            if ws:
                ok, rep = _ws_prepare(ws, e, out=quiet)    # (its own connection: the rig's never subscribes)
                note(e, "ws", ok, rep)
                if not ok:
                    stopped = "%s: %s" % (e, rep["verdict"])
                    break
            if not isinstance(r, TapRig):           # (the tap is the plugin's own output: no stream to check)
                ok, idl = idle_check(r, out=quiet)
                idl["verdict"] = "PASS" if ok else "FAIL: " + SAY[idl["kind"]]
                note(e, "idle", ok, idl)
                if not ok:
                    stopped = "%s: the idle check failed (%s)" % (e, idl["kind"])
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
                      ("usb-stress", lambda: usb_stress(r, sub("usb-stress"), e, stress_secs, out=quiet))]
            if e == (engines[0] if "rhodes" not in engines else "rhodes"):    # (the effects' clock: once a run)
                tests.insert(-1, ("usb-clock", lambda: usb_clock(clock or r, sub("usb-clock"), bpm, out=quiet)))
            if e == "rhodes" and hq == 1 and tap_cpu:
                tests.append(("usb-restrike", lambda: usb_restrike(r, sub("usb-restrike"), stress_secs / 2,
                                                                   out=quiet)))
            if only:
                tests = [(n, f) for n, f in tests if any(n == o or n.startswith(o + "-") for o in only)]
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
            if tap_cpu and any(n == "usb-stress" for n, _ in tests):
                s = cpu_summary(cpu_rows)
                s["engine"], s["hq"] = e, hq
                s["verdict"] = s["verdict"] if s["n"] else "FAIL: no callback times (dev_cpu_log reached the plugin?)"
                note(e, "cpu", s["verdict"].startswith("PASS"), s)
    except _Stop:
        pass
    finally:
        if ks is not None:
            ks.__exit__(None, None, None)
            note("all", "state", ks.report["verdict"] == "PASS",
                 dict(ks.report, verdict=ks.report["verdict"] + (" (%d param(s) as before, %d written back)" % (
                     ks.report["params"], len(ks.report["written"])) if ks.report["verdict"] == "PASS" else "")))
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


# --------------------------------------------------------------------------- usb-levels: Frodhes against the Move's own
LEVEL_NOTES = tuple(range(36, 85, 4))     # C2 .. C6 a major third apart, one at a time (tools/move_levels.py's)
LEVEL_CHORD = (48, 52, 55, 60, 64)        # C3 E3 G3 C4 E4, held by the keys (no pedal: a stock instrument may not
                                          # take CC 64 as Frodhes does)
LEVEL_VELS = (100, 64, 127)               # v100 sets the gain; v64 and v127 show the two velocity curves' ends
LEVEL_OFF_CH = 16                         # Frodhes's CHANNEL while the stock instrument plays: off the notes' channel
BARK_EDGES = (20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700,
              4400, 5300, 6400, 7700, 9500, 12000, 15500, 20000)
N_EXP = 0.23                              # specific loudness ~ band power ^ 0.23 (Zwicker's exponent, above threshold)


def level_events(vel=100, t0=0.3):
    """the fixed pattern: (events [(t s, kind, a, b)], dur s)"""
    ev, t = [], t0
    for k in LEVEL_NOTES:
        ev += [(t, "on", k, vel), (t + 0.45, "off", k, 0)]
        t += 0.5
    t += 0.2
    ev += [(t, "on", k, vel) for k in LEVEL_CHORD] + [(t + 2.0, "off", k, 0) for k in LEVEL_CHORD]
    return ev, t + 3.5


def lufs_gated(x, sr):
    """BS.1770-4 integrated loudness: K-weighted, 400 ms blocks (75 % overlap), the -70 LUFS and the -10 LU gates;
    x (n, 2) in LSB. None when no block passes"""
    import bar_measure as bm
    from scipy.signal import sosfilt
    y = sosfilt(bm.k_weight_sos(sr), np.asarray(x, float) / 32768.0, axis=0)
    n, hop = int(0.4 * sr), int(0.1 * sr)
    if len(y) < n:
        return None
    z = np.array([np.sum(np.mean(y[i:i + n] ** 2, axis=0)) for i in range(0, len(y) - n + 1, hop)])
    l = -0.691 + 10 * np.log10(z + 1e-30)
    z = z[l > -70]
    if not len(z):
        return None
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z.mean()))


def bark_frames(x, sr, ms=100):
    """the power (re full scale) per critical band (BARK_EDGES) and frame of ms: (frames, 25)"""
    m = (x.mean(axis=1) if x.ndim == 2 else x) / 32768.0
    n = int(sr * ms / 1000)
    k = len(m) // n
    if not k:
        return np.zeros((0, len(BARK_EDGES) - 1))
    w = np.hanning(n)
    sp = np.abs(np.fft.rfft(m[:k * n].reshape(k, n) * w, axis=1)) ** 2 * 4 / (w.sum() ** 2)
    f = np.fft.rfftfreq(n, 1 / sr)
    return np.stack([sp[:, (f >= lo) & (f < hi)].sum(axis=1) for lo, hi in zip(BARK_EDGES[:-1], BARK_EDGES[1:])], 1)


def level_of(x, sr):
    """one take's level: LUFS (gated), the peak (dBFS), the critical-band levels over the whole take (dBFS per band),
    and the critical-band loudness (the mean over the frames of the sum over the bands of power ^ N_EXP: a gain g dB
    scales it by 10 ^ (N_EXP g / 10))"""
    x = np.asarray(x, float)
    E = bark_frames(x, sr)
    act = E.sum(axis=1) > 1e-7                      # (frames over -70 dBFS: the silences between the notes out)
    return {"lufs": lufs_gated(x, sr), "peak_dbfs": float(20 * np.log10(np.abs(x).max() / 32768.0 + 1e-12)),
            "bands_db": [float(10 * np.log10(v + 1e-20)) for v in E.mean(axis=0)] if len(E) else [],
            "cb_loudness": float((E[act] ** N_EXP).sum(axis=1).mean()) if act.any() else 0.0}


def level_match(ref, frod):
    """the gain (dB) on Frodhes that matches the reference: by LUFS, and by the critical-band loudness"""
    g_lufs = ref["lufs"] - frod["lufs"] if ref["lufs"] is not None and frod["lufs"] is not None else None
    g_cb = (10 / N_EXP * np.log10(ref["cb_loudness"] / frod["cb_loudness"])
            if ref["cb_loudness"] > 0 and frod["cb_loudness"] > 0 else None)
    return {"gain_lufs_db": None if g_lufs is None else round(float(g_lufs), 2),
            "gain_cb_db": None if g_cb is None else round(float(g_cb), 2)}


def _ws_params(url, slot=0):
    """the slot's params as Schwung reports them now (a connection of its own, kept open past the re-read)"""
    import move_ws
    with move_ws.Remote(url) as w:
        w.subscribe(slot)
        w.pump(0.5)
        return dict(w.params.get(slot, {}))


def usb_levels(u, t, d, url, vels=LEVEL_VELS, out=print):
    """how loud Frodhes F01 is against the Move's own instrument on the selected track, both through the Move's mix
    (the track's volume, the master, USB-C Main Out). u: the UsbRig (MIDI to the Standalone port, USB-C recorded);
    t: the TapRig (Schwung's WebSocket, slot 1). Per velocity:
      stock    the pattern over USB MIDI, Frodhes's CHANNEL moved off the notes (LEVEL_OFF_CH), and a tap take that
               runs the whole time: Frodhes's own output must be silent there, so the recording is the stock
               instrument alone
      frodhes  the same pattern on Frodhes's dev player (no Move track hears it, CHANNEL back as it was), recorded
               on USB-C: Frodhes alone, through the same track volume and master; the tap is its output before them
    The gain that matches them, by LUFS and by critical-band loudness, is reported only; nothing is set. (ok, rep)"""
    import json
    os.makedirs(d, exist_ok=True)
    ok, pre = idle_check(u, out)
    if not ok:
        return False, {"verdict": "STOP: the USB-C stream is not the Move's digital Main Out", "preflight": pre}
    ok, ws = _ws_prepare(url, "rhodes", t.slot, out)          # (F01)
    if not ok:
        return False, {"verdict": "STOP: " + ws["verdict"], "ws": ws}
    t.boot_settings()
    p0 = _ws_params(url, t.slot)
    ch0 = p0.get("synth:s_mch", "0")
    rep = {"preset": ws.get("preset_name"), "ch_before": ch0, "p_hq": p0.get("synth:p_hq"), "rows": [], "ws": ws, "preflight": pre}
    good = True
    try:
        for vel in vels:
            ev, dur = level_events(vel)
            # stock: Frodhes off the channel, its tap running across the whole take
            t._set("synth:s_mch", str(LEVEL_OFF_CH))
            t._pump(0.5)
            u.wait_silence(5.0)
            t.n += 1
            take = "lv%s-%03d" % (t.stamp, t.n)
            t.remote.play(t.slot, take, int((TAP_GRID_S + 0.3 + dur + 0.5) * 1000), [])
            time.sleep(TAP_GRID_S + 0.3)                     # (the tap starts on the plugin's 2560 ms grid)
            u._send("cc", 123, 0)
            sk = u.rec(dur, ev)
            time.sleep(1.0)
            got, t0 = None, time.monotonic()
            while got is None and time.monotonic() - t0 < 10:
                got = t.fetch(take)
                time.sleep(0.5) if got is None else None
            import wave
            fx = None
            if got:
                w = wave.open(got[0])
                fx = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float)
            frod_quiet = fx is not None and np.abs(fx).max() == 0
            # frodhes: the dev player, recorded on USB-C as it plays
            t._set("synth:s_mch", ch0)
            t._pump(0.5)
            u.wait_silence(5.0)
            cap = u.capture()
            st = cap.now()["usb"]
            t.pending = [("cc", c, v) for c, v in GLOBAL_CCS] + [("cc", 123, 0)]
            ft = t.rec(dur, ev)
            span = cap.now()["usb"] - st
            ux = cap.cut("usb", st, span).astype(float) * 32768
            ref_m, got_m = _mono(ft["x"]), _mono(ux)
            lag = _lag(got_m, ref_m, len(got_m) - 1) if len(got_m) > len(ref_m) else 0
            lag = max(0, min(lag, len(ux) - len(ft["x"])))
            fu = ux[lag:lag + len(ft["x"])]
            path_db = float(hs.db(fu) - hs.db(ft["x"])) if np.abs(ft["x"]).max() > 0 else None
            for name, x in (("stock", sk["x"]), ("frodhes", fu), ("frodhes-tap", ft["x"])):
                _save(d, "v%d-%s" % (vel, name), {"x": x, "sr": u.sr})
            ls, lf = level_of(sk["x"], u.sr), level_of(fu, u.sr)
            m = level_match(ls, lf)
            row = {"vel": vel, "stock": ls, "frodhes": lf, "tap": level_of(ft["x"], FS), "match": m,
                   "frodhes_silent_in_stock": bool(frod_quiet), "path_db": path_db, "lag": lag,
                   "xruns": sk["xruns"] + ft["xruns"]}
            if not frod_quiet:
                row["note"] = ("Frodhes's tap was not silent during the stock take (peak %s LSB): the stock take is "
                               "not the stock instrument alone" % (None if fx is None else int(np.abs(fx).max())))
                good = False
            if ls["lufs"] is None:
                row["note"] = "the stock take is silent: the selected track's instrument did not play"
                good = False
            rep["rows"].append(row)
            out("v%-3d stock %s LUFS (peak %.1f dBFS), Frodhes %s LUFS (peak %.1f); the gain to match: %s dB by "
                "LUFS, %s dB by critical-band loudness; Frodhes silent in the stock take: %s; track+master %s dB" % (
                    vel, "%.1f" % ls["lufs"] if ls["lufs"] is not None else "-", ls["peak_dbfs"],
                    "%.1f" % lf["lufs"] if lf["lufs"] is not None else "-", lf["peak_dbfs"], m["gain_lufs_db"],
                    m["gain_cb_db"], frod_quiet, "%.1f" % path_db if path_db is not None else "-"))
    finally:
        t._set("synth:s_mch", ch0)
        t._pump(0.5)
    rep["ch_after"] = _ws_params(url, t.slot).get("synth:s_mch")
    rep["verdict"] = "PASS" if good else "FAIL: see the rows' notes"
    json.dump(_plain(rep), open(os.path.join(d, "report.json"), "w"), indent=1, default=str)
    if rep["rows"]:
        r0 = rep["rows"][0]
        out("critical bands (Hz: stock / Frodhes dBFS, v%d):" % r0["vel"])
        out("  " + "  ".join("%d:%.0f/%.0f" % (lo, a, b) for lo, a, b in zip(BARK_EDGES, r0["stock"]["bands_db"],
                                                                            r0["frodhes"]["bands_db"])))
    return good, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("facts", "install", "rollback", "info", "logs", "cpu", "crackle", "must", "null", "d2", "usb-levels",
                                    "clock-probe", "preflight")
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
    ap.add_argument("--ch", type=int, default=None, help="usb-*, d2, usb-levels, preflight: the MIDI channel, "
                    "1-16 (default: slot 1's receive channel, read over Schwung)")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--program", type=int, default=0, help="usb-host: the program change first (0 = F01)")
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--bpm", type=float, default=0.0, help="usb-clock (default 100), usb-stress (0: no clock)")
    ap.add_argument("--secs", type=float, default=60.0, help="usb-stress: the take's length")
    ap.add_argument("--nv", type=int, default=MOVE_VOICES, help="usb-stress: voices per chord")
    ap.add_argument("--out", help="usb-*: the folder for the takes and report.json")
    ap.add_argument("--auto", action="store_true",
                    help="clock-probe: press Play and Stop on the Move itself (Schwung's test bus, move_ui_drive.py)")
    ap.add_argument("--no-preflight", action="store_true",
                    help="usb-*, d2, usb-levels: skip the preflight (Main Out idle, Main Out against the tap)")
    ap.add_argument("--fake", action="store_true", help="usb-*: the offline model of the Move (no device)")
    ap.add_argument("--inject", action="append", help="usb-* --fake: KIND@SECONDS (zero, click, stale, loud, nosus, playing, hiss, foreign, mic); clock-probe --fake: real, fallback, none, late")
    ap.add_argument("--engines", default=",".join(D2_ENGINES), help="d2: the engines, comma-separated")
    ap.add_argument("--tests", help="d2: only these tests, comma-separated (a name or its prefix: usb-host,usb-stress; "
                                    "the cpu row comes with usb-stress)")
    ap.add_argument("--hq", type=int, choices=(0, 1), default=None,
                    help="d2 --path tap: the HQ Rhodes off (0, Classic: a test only, through the dev key p_hq) or "
                         "on (1) for the run (default: as the plugin loads, HQ)")
    ap.add_argument("--path", choices=("usb", "tap"), default="usb",
                    help="usb-*, d2: usb = MIDI to the Standalone port, the USB-C audio recorded (the Move's mix); "
                         "tap = the plugin's own player and output over Schwung's WebSocket (--ws), the takes "
                         "fetched over ssh (--host): no Move track, volume or USB-C setting in the way")
    ap.add_argument("--ws", default=os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui"),
                    help="d2: Schwung's WebSocket (slot 1's module and preset); 'none' to skip")
    a = ap.parse_args(argv)
    import contextlib
    with (mi.move_lock("move_hil " + a.cmd) if drives_the_move(a) else contextlib.nullcontext()):
        return _main(a)


def drives_the_move(a):
    """does this command send to the Move or read it (the MOVE lock), or is it offline (a fake, a saved file, a dry
    run, a local folder)?"""
    if a.cmd in ("usb-levels", "clock-probe", "preflight"):
        return not a.fake
    if a.cmd == "d2":
        return not a.fake
    if a.cmd in USB_CMDS:
        return not a.fake and a.cmd != "usb-devices" and not (a.cmd == "usb-host" and a.analyze)
    if a.cmd in ("facts", "install", "rollback", "info", "logs", "cpu"):
        return not a.target_dir and not a.file and (a.go or a.cmd == "info")
    return False


def _main(a):
    if a.cmd == "d2":
        engines = tuple(x for x in a.engines.split(",") if x)
        bad = [x for x in engines if x not in D2_ENGINES]
        if bad:
            raise SystemExit("--engines: %s" % bad)
        d = a.out or os.path.join(OUT, stamp() + "-d2")
        chan, srep = channel_for(a)
        if chan.get("stop"):
            print("STOP: " + chan["stop"])
            return 2
        if a.fake:
            inj = [(k, float(t or 0)) for k, _, t in (s.partition("@") for s in a.inject or ())]
            r = FakeEchoRig(inject=inj)
        elif a.path == "tap":
            r = TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), chan["plugin"], cpu=scp_cpu(a.host))
        else:
            r = UsbRig(a.midi, a.audio, chan["send"])
        only = [x for x in (a.tests or "").split(",") if x] or None
        pre = None if a.no_preflight else (lambda: preflight_cmd(a, os.path.join(d, "preflight"), chan, srep,
                                                                 u=r if not isinstance(r, TapRig) else None))
        return d2(r, d, engines, None if a.ws == "none" else a.ws, a.secs, a.bpm or 100.0, a.hq, only, pre=pre,
                  clock=clock_rig(a, r, d, chan))
    if a.cmd == "clock-probe":
        d = a.out or os.path.join(OUT, stamp() + "-clock-probe")
        if a.fake:
            src = (a.inject or ["real"])[0]
            link = FakeStatsLink(src)
            ok, _ = clock_probe(link, d, sleep=lambda s: None, press=link.press if a.auto else None)
        elif a.auto:
            with PlayButton() as pb:
                ok, _ = clock_probe(StatsLink(a.ws, a.host), d, press=pb.press)
        else:
            ok, _ = clock_probe(StatsLink(a.ws, a.host), d)
        return 0 if ok else 1
    if a.cmd == "preflight":
        d = a.out or os.path.join(OUT, stamp() + "-preflight")
        chan, srep = channel_for(a)
        if a.fake or a.ws == "none":
            ok, rep = preflight_cmd(a, d, chan, srep)
        else:
            with KeptState(a.ws, 0) as ks:      # (CC 123 / CC 64 0 and the note: the state as it was)
                ok, rep = preflight_cmd(a, d, chan, srep)
            ok = ok and ks.report["verdict"] == "PASS"
        return 0 if ok else 1
    if a.cmd == "usb-levels":
        d = a.out or os.path.join(OUT, stamp() + "-usb-levels")
        chan, srep = channel_for(a)
        if chan.get("stop"):
            print("STOP: " + chan["stop"])
            return 2
        t = TapRig(a.ws, scp_fetch(a.host, os.path.join(d, "tap")), chan["plugin"])
        u = UsbRig(a.midi, a.audio, chan["send"])
        with KeptState(a.ws, 0) as ks:          # (the preset, CHANNEL, boot_settings: as they were)
            if not a.no_preflight:
                ok, pre = preflight_cmd(a, os.path.join(d, "preflight"), chan, srep, u=u)
                if not ok:
                    print("STOP: the preflight failed")
                    return 2
            ok, rep = usb_levels(u, t, d, a.ws)
        rep["state"] = ks.report
        ok = ok and ks.report["verdict"] == "PASS"
        print(rep["verdict"])
        return 0 if ok else 1
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
