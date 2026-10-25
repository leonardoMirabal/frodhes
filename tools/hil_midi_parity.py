#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Do USB-MIDI and the TRS MIDI input behave the same? (the FM-1 over both, its USB audio recorded)

    tools/hil_midi_parity.py --port NAME     NAME: the interface output wired to the TRS IN (a substring; default
                                             $FRODHES_TRS_PORT); TRS IN ON on the MIDI page
    tools/hil_midi_parity.py --dry-run       the script and the checks; no MIDI, no audio, no lock
    tools/hil_midi_parity.py --selftest      the comparison on synthetic recordings (equal: PASS; a path that drops
                                             the pitch bend, plays 1 dB quieter or ignores the CHANNEL: FAIL)

The same script goes over each path (USB-MIDI, then TRS), from the same start (dry over USB: every parameter at
its default, hil_scan DRY), each event 50 ms after a CC 10 reset (the LFOs, lines and noise at their start):
  vel30 / vel80 / vel127   C4 at three velocities                       onset, level (300 ms RMS)
  rel10 / rel127           C4 v100, released with velocity 10 / 127     the release tail's level (0.05-0.45 s after)
  sustain                  CC 64 on, C4 released, CC 64 off            the held level, then the damped level
  drive                    CC 84 TSH8, CC 81 AMOUNT 100                 level and third-octave band shape
  delay1..3                CC 88 DIG (no modulation), MIX 100, FEEDBACK 30, three short C6
                                                                         the echoes' level against each probe's
                                                                         own dry note, averaged; their band shape
  program                  Program Change 3 (F04 BELL DYNO)             level and band shape
  bend_up / bend_down      A4 held, pitch bend +8191 / -8192            the pitch change (cents)
  channel                  C4 v127 on another channel                   silent (the CHANNEL filter, on both)
Pass, per event: the onset within 2 ms + one audio block (5.8 ms: a note starts at a block boundary) of the
paths' median offset (the TRS byte time aligned away), the level within 0.5 dB (a release tail, measured from
where the damper lands: 1 dB), the band shape within 1 dB (bands
within 40 dB of the loudest), the bend within 5 cents, a wrong-channel note silent (under 200 LSB) on both.

The channel the FM-1 listens on is found first (a note on each, hil_clock.find_channel); with CHANNEL ALL the
channel row is MANUAL (every channel is played). The player's sound: the FM-1 cannot be read over MIDI, so the tool
takes a fingerprint of the sound at the start (C4 v100 after a CC 10 reset: levels and band shape over 1.5 s),
and at the end loads the program (Program Change 0-34: F01-F16, U01-U16, F17-F19) whose fingerprint matches it;
when none does (the start was an unsaved edit), it says so loudly and leaves the closest one. Nothing is saved:
no user preset, pattern or setting is written by a MIDI message this tool sends (the settings' own autosave keeps
the last sound, which is the restored program).

Report build/hil/midi_parity/report.txt + report.json, the takes as npz. Exit 0 PASS, 1 FAIL, 2 setup error. Takes
the DEVICE lock (tools/lock.py). Needs the macOS sandbox off. ~3 min."""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hil_clock as HC  # noqa: E402

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "build", "hil", "midi_parity")
ONSET_TOL_S = 0.002 + 256 / 44100
LEVEL_TOL_DB = 0.5
TAIL_TOL_DB = 1.0                # a release tail's level: the damper's landing found within ~3 ms, ~0.5 dB on its slope
SHAPE_TOL_DB = 1.0
BEND_TOL_CENTS = 5.0
SILENT_LSB = 200
SLOT = 2.2
DLY_PROBES, DLY_GAP = 3, 3.2     # the delay: 3 probes (their echo level against each one's own dry note, averaged:
                                 # one probe's absolute echo level moved +-0.84 dB from run to run on one build)
N_PROGRAMS = 35


def script():
    """[(t, path|'wrong', msg tuple, label)] and the events' labels with their kind; msg: ('on', n, v), ('off', n, v),
    ('cc', c, v), ('pc', p), ('bend', value)"""
    ev, marks = [], []
    t = 0.3

    def at(dt, m, lab=None, dest="p"):
        ev.append((round(t + dt, 4), dest, m, lab))
    for v in (30, 80, 127):
        at(-0.05, ("cc", 10, 127)); at(0, ("on", 60, v), f"vel{v}"); at(0.6, ("off", 60, 64))
        marks.append((f"vel{v}", "note", t)); t += SLOT
    for rv in (10, 127):
        at(-0.05, ("cc", 10, 127)); at(0, ("on", 60, 100), f"rel{rv}"); at(0.6, ("off", 60, rv))
        marks.append((f"rel{rv}", "release", t)); t += SLOT
    at(-0.15, ("cc", 64, 127)); at(-0.05, ("cc", 10, 127)); at(0, ("on", 60, 100), "sustain"); at(0.3, ("off", 60, 64))
    at(1.2, ("cc", 64, 0))
    marks.append(("sustain", "sustain", t)); t += SLOT + 1.3
    at(-1.0, ("cc", 84, 80)); at(-0.95, ("cc", 81, 100)); at(-0.05, ("cc", 10, 127)); at(0, ("on", 60, 100), "drive")
    at(0.6, ("off", 60, 64)); at(1.6, ("cc", 84, 0))
    marks.append(("drive", "shape", t)); t += SLOT
    at(-0.3, ("cc", 88, 40)); at(-0.25, ("cc", 89, 100)); at(-0.2, ("cc", 25, 0)); at(-0.15, ("cc", 26, 30))
    for k in range(DLY_PROBES):                          # DIG (no modulation), a short feedback: the echoes end
        at(-0.05, ("cc", 10, 127)); at(0, ("on", 84, 110), f"delay{k + 1}"); at(0.05, ("off", 84, 64))
        marks.append((f"delay{k + 1}", "echo", t)); t += DLY_GAP   # inside the window, before the next probe
    at(-0.1, ("cc", 88, 0)); t += 0.4
    at(-0.5, ("pc", 3)); at(-0.05, ("cc", 10, 127)); at(0, ("on", 60, 100), "program"); at(0.6, ("off", 60, 64))
    marks.append(("program", "shape", t)); t += SLOT
    for nm, b in (("bend_up", 8191), ("bend_down", -8192)):
        at(-0.05, ("cc", 10, 127)); at(0, ("on", 69, 100), nm); at(0.45, ("bend", b)); at(1.0, ("bend", 0))
        at(1.1, ("off", 69, 64))
        marks.append((nm, "bend", t)); t += SLOT
    t += 0.8
    at(0, ("on", 60, 127), "channel", dest="wrong"); at(0.5, ("off", 60, 64), dest="wrong")
    marks.append(("channel", "silent", t)); t += 1.5
    return sorted(ev, key=lambda e: e[0]), marks, t


def _bands(x, fs):
    """third-octave band levels (dB) of x and its RMS level (dB)"""
    n = len(x)
    P = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1 / fs)
    edges = 100.0 * 2 ** (np.arange(0, 31) / 3.0)
    edges = edges[edges < min(12000.0, 0.45 * fs)]
    b = np.array([P[(f >= lo) & (f < hi)].sum() for lo, hi in zip(edges[:-1], edges[1:])])
    return 10 * np.log10(b + 1e-20), 10 * np.log10(np.mean(x.astype(float) ** 2) + 1e-20)


def _rms_db(x):
    return float(10 * np.log10(np.mean(x.astype(float) ** 2) + 1e-20))


def _f0(x, fs, guess):
    """the fundamental near guess (Hz): a zero-padded FFT peak within +-3 semitones, parabolic"""
    n = len(x)
    N = 1 << int(np.ceil(np.log2(n * 8)))
    S = np.abs(np.fft.rfft(x * np.hanning(n), N))
    f = np.fft.rfftfreq(N, 1 / fs)
    m = (f > guess * 2 ** (-3.5 / 12)) & (f < guess * 2 ** (3.5 / 12))
    i = int(np.argmax(np.where(m, S, 0)))
    y0, y1, y2 = np.log(S[i - 1:i + 2] + 1e-20)
    d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) else 0.0
    return float((i + d) * fs / N)


def _attack(x, fs, ts, ons):
    """the note attack after the send at ts (hil_clock.note_onsets: the hammer's rise; a ringing previous tail
    fooled a level threshold, 107 ms), within 150 ms"""
    a = max(0, int((ts - 0.05) * fs))
    loc = HC.note_onsets(x[a:a + int(0.35 * fs)], fs) + a / fs   # (local: the take-wide level filter dropped a
    c = loc[(loc >= ts - 0.002) & (loc < ts + 0.15)]              # soft note's attack)
    if len(c):
        return float(c[0])
    return HC.onset(x, fs, ts, span=0.15)


def _release_at(x, fs, t_from, t_to):
    """where the damper lands between t_from and t_to (the note-off's own time, which the sending path's jitter
    moves): the first 20 ms over which the 5 ms envelope falls 3 dB, then where it is 6 dB down"""
    t, e = HC.envelope_db(x, fs, win=0.005, hop=0.001)
    sel = np.nonzero((t >= t_from) & (t < t_to - 0.02))[0]
    if not len(sel):
        return t_from
    d = e[sel + 20] - e[sel]
    hit = np.nonzero(d < -3.0)[0]                       # the first 20 ms falling by 3 dB: a note's own decay is
    i = sel[hit[0]] if len(hit) else sel[int(np.argmin(d))]   # far slower than the damper
    pre = float(np.median(e[max(0, i - 40):max(1, i - 15)]))   # then where it is 6 dB under the level 15-40 ms
    i = max(0, i - 15)                                   # before, interpolated
    j = i + int(np.argmax(e[i:i + 200] < pre - 6.0))
    if j <= 0 or e[j] >= pre - 6.0:
        return float(t[i])
    a, b = e[j - 1], e[j]
    return float(t[j - 1] + (t[j] - t[j - 1]) * (a - (pre - 6.0)) / (a - b if a != b else 1.0))


def measure(x, fs, marks, sends):
    """per event: its features. sends {label: tau of its send (s, the recording's clock)}"""
    out = {}
    ons = None
    for lab, kind, _ in marks:
        ts = sends[lab]
        o = _attack(x, fs, ts, ons)
        r = dict(kind=kind, onset_ms=None if o is None else round(1000 * (o - ts), 3))
        seg = lambda a, b: x[int(max(0, (o if o is not None else ts) + a) * fs):int(((o if o is not None else ts) + b) * fs)]  # noqa
        if kind == "silent":
            r["peak_lsb"] = float(np.abs(x[int(ts * fs):int((ts + 0.6) * fs)]).max() * 32768)
            r.pop("onset_ms")
        elif o is None:
            r["error"] = "no sound"
        elif kind == "note":
            r["level_db"] = round(_rms_db(seg(0, 0.3)), 3)
        elif kind == "release":
            r["level_db"] = round(_rms_db(seg(0, 0.3)), 3)
            tr = _release_at(x, fs, o + 0.5, o + 0.9) - o
            r["tail_db"], r["release_s"] = round(_rms_db(seg(tr + 0.03, tr + 0.43)), 3), round(tr, 4)
        elif kind == "sustain":
            r["held_db"] = round(_rms_db(seg(0.6, 1.0)), 3)
            tr = _release_at(x, fs, o + 1.1, o + 1.5) - o
            r["damped_db"], r["release_s"] = round(_rms_db(seg(tr + 0.03, tr + 0.43)), 3), round(tr, 4)
        elif kind == "shape":
            bd, lv = _bands(seg(0.02, 0.5), fs)
            r["level_db"], r["bands"] = round(lv, 3), [round(float(v), 2) for v in bd]
        elif kind == "echo":                              # the echoes against the take's own dry note
            bd, lv = _bands(seg(0.15, DLY_GAP - 0.25), fs)
            r["echo_rel_db"] = round(lv - _rms_db(seg(0, 0.05)), 3)
            r["level_db_info"], r["bands"] = round(lv, 3), [round(float(v), 2) for v in bd]
            r["level_db"] = r["level_db_info"]
        elif kind == "bend":
            f1 = _f0(seg(0.1, 0.4), fs, 440.0)
            f2 = _f0(seg(0.65, 0.95), fs, f1)
            r["cents"] = round(1200 * np.log2(f2 / f1), 2)
        out[lab] = r
    return out


def compare(a, b, channel_all=False):
    """two paths' measures -> (verdict, rows, problems)"""
    rows, probs = [], []
    d = [b[k]["onset_ms"] - a[k]["onset_ms"] for k in a if a[k].get("onset_ms") is not None
         and b.get(k, {}).get("onset_ms") is not None]
    med = float(np.median(d)) if d else 0.0
    for k, ra in a.items():
        rb = b.get(k, {})
        row = dict(event=k, kind=ra["kind"], ok=True, notes=[])
        if ra.get("error") or rb.get("error"):
            row["ok"] = False
            row["notes"].append(f"USB {ra.get('error', 'ok')}, TRS {rb.get('error', 'ok')}")
        else:
            if ra.get("onset_ms") is not None:
                dev = rb["onset_ms"] - ra["onset_ms"] - med
                row["onset_dev_ms"] = round(dev, 2)
                if abs(dev) > 1000 * ONSET_TOL_S:
                    row["ok"] = False
                    row["notes"].append(f"onset {dev:+.1f} ms off the paths' median offset")
            for key in ("level_db", "tail_db", "held_db", "damped_db"):
                if key in ra and not (key == "level_db" and ra["kind"] == "echo"):
                    dl = rb[key] - ra[key]
                    row[key.replace("_db", "_diff_db")] = round(dl, 2)
                    if abs(dl) > (TAIL_TOL_DB if key in ("tail_db", "damped_db") else LEVEL_TOL_DB):
                        row["ok"] = False
                        row["notes"].append(f"{key[:-3]} {dl:+.2f} dB")
            if "bands" in ra:
                ba, bb = np.array(ra["bands"]), np.array(rb["bands"])
                use = ba > ba.max() - 40
                dd = (bb - rb["level_db"]) - (ba - ra["level_db"])
                row["shape_diff_db"] = round(float(np.abs(dd[use]).max()), 2)
                if row["shape_diff_db"] > SHAPE_TOL_DB:
                    row["ok"] = False
                    row["notes"].append(f"band shape up to {row['shape_diff_db']} dB apart")
            if "cents" in ra:
                dc = rb["cents"] - ra["cents"]
                row["cents"] = (ra["cents"], rb["cents"])
                if abs(dc) > BEND_TOL_CENTS or abs(abs(ra["cents"]) - 200) > 30:
                    row["ok"] = False
                    row["notes"].append(f"bend USB {ra['cents']:+.1f} / TRS {rb['cents']:+.1f} cents")
            if "peak_lsb" in ra:
                row["peak_lsb"] = (round(ra["peak_lsb"]), round(rb["peak_lsb"]))
                if channel_all:
                    row["ok"], row["manual"] = None, "CHANNEL ALL: every channel is played (set a CHANNEL to check)"
                elif max(ra["peak_lsb"], rb["peak_lsb"]) > SILENT_LSB:
                    row["ok"] = False
                    row["notes"].append(f"a note on another channel sounded (USB {ra['peak_lsb']:.0f}, TRS "
                                        f"{rb['peak_lsb']:.0f} LSB)")
        rows.append(row)
        if row["ok"] is False:
            probs.append(f"{k}: " + "; ".join(row["notes"]))
    ea = [r["echo_rel_db"] for r in a.values() if "echo_rel_db" in r]
    eb = [r["echo_rel_db"] for r in b.values() if "echo_rel_db" in r]
    if ea and len(ea) == len(eb):                         # the delay: the echo level against the dry, averaged
        dl = float(np.mean(eb) - np.mean(ea))
        row = dict(event="delay", kind="echo", ok=abs(dl) <= LEVEL_TOL_DB, echo_rel_diff_db=round(dl, 2),
                   per_probe_db=[round(y - x_, 2) for x_, y in zip(ea, eb)], notes=[])
        if not row["ok"]:
            row["notes"].append(f"echo level (to its dry note, mean of {len(ea)}) {dl:+.2f} dB")
            probs.append("delay: " + row["notes"][0])
        rows.append(row)
    return ("PASS" if not probs else "FAIL"), rows, probs, med


def spread(m):
    """a path's onset spread (ms, max - min over its notes): its own timing jitter (USB: about an audio block)"""
    v = [r["onset_ms"] for r in m.values() if r.get("onset_ms") is not None]
    return round(max(v) - min(v), 2) if v else None


def fingerprint(x, fs, t):
    o = HC.onset(x, fs, t)
    s = x[int((o or t) * fs):int(((o or t) + 1.5) * fs)]
    bd, lv = _bands(s, fs)
    return dict(level_db=round(lv, 2), bands=[round(float(v), 2) for v in bd])


def fp_distance(a, b):
    ba, bb = np.array(a["bands"]), np.array(b["bands"])
    use = ba > ba.max() - 40
    return max(abs(a["level_db"] - b["level_db"]), float(np.abs((bb - b["level_db"]) - (ba - a["level_db"]))[use].max()))


# ---- synthetic (--selftest, tests/py/test_hil_midi_parity.py) -----------------------------------------------------


def synth(fs=48000, lat=0.012, gain_db=0.0, no_bend=False, channel_heard=False, seed=0, echo_db=0.0):
    """a recording of the script as the FM-1 would play it, and the sends (tau)"""
    rng = np.random.default_rng(seed)
    ev, marks, dur = script()
    x = np.zeros(int(dur * fs))
    g = 10 ** (gain_db / 20)
    sends = {lab: t for t, _, _, lab in ev if lab}
    held, bend, sus = {}, 0.0, False

    def note(t0, t1, f0, v, tail=0.3, cents_at=None):
        i0 = int((t0 + lat + rng.uniform(0, 0.0058)) * fs)
        n = int((t1 - t0 + tail) * fs)
        tt = np.arange(n) / fs
        f = np.full(n, f0)
        if cents_at:
            for (ta, c) in cents_at:
                f[tt >= ta - t0] = f0 * 2 ** (c / 1200)
        ph = 2 * np.pi * np.cumsum(f) / fs
        env = np.exp(-tt / 2.0) * np.minimum(1, tt / 0.002) * np.where(tt > t1 - t0, np.exp(-(tt - (t1 - t0)) / 0.05), 1)
        y = (v / 127) * 0.3 * g * env * (np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.1 * np.sin(5 * ph))
        m = min(n, len(x) - i0)
        x[i0:i0 + m] += y[:m]
    offs = {}
    for t, dest, m, lab in ev:
        if m[0] == "on":
            offs[(m[1], dest)] = t
    for lab, kind, t in marks:
        if kind == "note":
            note(t, t + 0.6, 261.63, int(lab[3:]))
        elif kind == "release":
            note(t, t + 0.6, 261.63, 100, tail=0.3 + (0.2 if lab == "rel127" else 0.1))
        elif kind == "sustain":
            note(t, t + 1.2, 261.63, 100)
        elif kind == "shape":
            note(t, t + 0.6, 261.63 if lab == "drive" else 329.6, 110 if lab == "drive" else 90)
        elif kind == "echo":
            for k in range(4):
                note(t + k * 0.5, t + k * 0.5 + 0.05, 1046.5, 110 * 0.5 ** k * (10 ** (echo_db / 20) if k else 1))
        elif kind == "bend":
            note(t, t + 1.1, 440.0, 100, cents_at=None if no_bend else
                 [(t + 0.45, 200 if lab == "bend_up" else -200), (t + 1.0, 0)])
        elif kind == "silent" and channel_heard:
            note(t, t + 0.5, 261.63, 127)
    x += rng.normal(0, 1e-5, len(x))
    return x.astype(np.float32), marks, sends


def selftest(verbose=True, fs=48000):
    a = measure(*_s(synth(fs, seed=1), fs))
    cases = [("equal", dict(seed=2, lat=0.013), "PASS"), ("1 dB quieter", dict(seed=3, gain_db=-1.0), "FAIL"),
             ("no pitch bend", dict(seed=4, no_bend=True), "FAIL"),
             ("ignores the CHANNEL", dict(seed=5, channel_heard=True), "FAIL")]
    ok = True
    for name, kw, want in cases:
        b = measure(*_s(synth(fs, **kw), fs))
        v, rows, probs, med = compare(a, b)
        ok &= v == want
        if verbose:
            print(f"{'ok  ' if v == want else 'BAD '} selftest {name}: {v} (want {want})" +
                  (f"; {probs[0]}" if probs else ""))
    return ok


def _s(sy, fs):
    x, marks, sends = sy
    return x, fs, marks, sends


# ---- the device ---------------------------------------------------------------------------------------------------


def run_device(a):
    import hil_scan as H
    import lock
    import mido
    ev, marks, dur = script()
    with lock.Lock("DEVICE", who="hil_midi_parity", why="USB vs TRS MIDI", wait_s=a.lock_wait):
        rig = H.Rig()
        cap = rig.capture()
        ring, stream = cap.rings["usb"], cap.streams["usb"]
        fs = ring.sr
        ch = HC.find_channel(rig, cap, a.channel)
        if ch is None:
            print("no note sounded on any MIDI channel: is the FM-1's USB audio the input?")
            return 2
        raw_usb = rig.out
        pn = HC.find_port(mido.get_output_names(), a.port)
        if any(k in pn.lower() for k in H.PORT_KEYS):
            sys.exit(f"--port {a.port} is the FM-1's own port ({pn})")
        raw_trs = mido.open_output(pn)
        rig.out = HC.ChannelPort(raw_usb, ch)
        print(f"the FM-1 listens on MIDI channel {ch + 1}; TRS over '{pn}'", flush=True)

        def rec(fn, d):
            rig.wait_silence(15.0)
            off0 = HC.clock_offset(stream)
            t_start = time.perf_counter() + 0.1
            T0 = t_start + off0
            stamps = fn(t_start)
            x = cap.cut("usb", T0, d, timeout=10.0)
            rate = ring.rate(T0, d) or float(fs)
            gaps = list(ring.gaps)
            return x.mean(axis=1), {k: (v + off0 - T0) * rate / fs for k, v in stamps.items()}, gaps

        def play_ref(t_start):
            M = mido.Message
            while time.perf_counter() < t_start:
                time.sleep(0.0005)
            rig.out.send(M("control_change", control=10, value=127))
            time.sleep(0.05)
            st = {"ref": time.perf_counter()}
            rig.out.send(M("note_on", note=60, velocity=100))
            time.sleep(0.6)
            rig.out.send(M("note_off", note=60))
            return st
        x0, s0, _ = rec(play_ref, 2.0)                        # the player's sound, before anything
        fp0 = fingerprint(x0, fs, s0["ref"])
        print(f"the start sound: {fp0['level_db']:.1f} dB", flush=True)
        wrong = (ch + 1) % 16
        res, gaps_all = {}, {}
        for path, raw in (("usb", raw_usb), ("trs", raw_trs)):
            rig.dry()
            port = HC.ChannelPort(raw, ch)
            wport = HC.ChannelPort(raw, wrong)
            M = mido.Message
            msgs = []
            for t, dest, m, lab in ev:
                k = m[0]
                mm = (M("note_on", note=m[1], velocity=m[2]) if k == "on" else
                      M("note_off", note=m[1], velocity=m[2]) if k == "off" else
                      M("control_change", control=m[1], value=m[2]) if k == "cc" else
                      M("program_change", program=m[1]) if k == "pc" else M("pitchwheel", pitch=m[1]))
                msgs.append((t, wport if dest == "wrong" else port, mm, lab))

            def play(t_start, msgs=msgs):
                st = {}
                for t, p, mm, lab in msgs:
                    target = t_start + t
                    while True:
                        dd = target - time.perf_counter()
                        if dd <= 0:
                            break
                        time.sleep(dd - 0.002 if dd > 0.003 else 0)
                    p.send(mm)
                    if lab:
                        st[lab] = time.perf_counter()
                return st
            x, sends, gaps = rec(play, dur)
            res[path] = measure(x, fs, marks, sends)
            gaps_all[path] = gaps
            os.makedirs(OUT, exist_ok=True)
            np.savez_compressed(os.path.join(OUT, f"{path}.npz"), x=(x * 32767).astype(np.int16), fs=fs,
                                sends=json.dumps(sends))
            print(f"{path}: {len(sends)} events" + (f", USB gaps {gaps}" if gaps else ""), flush=True)
        v, rows, probs, med = compare(res["usb"], res["trs"], channel_all=(a.channel is None and ch == 0))
        # the player's sound back: the program whose fingerprint matches the start
        best = None
        for p in range(N_PROGRAMS):
            raw_usb_ch = HC.ChannelPort(raw_usb, ch)
            raw_usb_ch.send(mido.Message("program_change", program=p))
            time.sleep(0.4)
            x1, s1, _ = rec(play_ref, 2.0)
            dist = fp_distance(fp0, fingerprint(x1, fs, s1["ref"]))
            if best is None or dist < best[0]:
                best = (dist, p)
            if dist <= 0.5:
                break
        dist, p = best
        if dist > 0.5:
            rig.out.send(mido.Message("program_change", program=p))
        restored = dict(program=p, distance_db=round(dist, 2), matched=bool(dist <= 0.5))
        rig.cc_raw(120, 0)
    prog = (f"F{p + 1:02d}" if p < 16 else f"U{p - 15:02d}" if p < 32 else f"F{p - 15:02d}")
    restore_line = (f"the start sound restored: Program Change {p} ({prog}), {dist:.2f} dB from it" if dist <= 0.5 else
                    f"WARNING: the start sound matched no stored program (an unsaved edit, or changed earlier): left on "
                    f"{prog}, the closest ({dist:.1f} dB); the FM-1's panel cannot be read over MIDI")
    if any(gaps_all.values()):
        v = "INCOMPLETE" if v == "PASS" else v
    L = [f"hil_midi_parity: {v}", f"  MIDI channel {ch + 1}, TRS port {pn}, fs {fs}; TRS - USB latency {med:+.2f} ms "
         f"(median); onset spread USB {spread(res['usb'])} ms, TRS {spread(res['trs'])} ms (the sending interface's "
         f"MIDI out timing is in the TRS one)", f"  {restore_line}"]
    for r in rows:
        st = "MANUAL" if r["ok"] is None else "ok  " if r["ok"] else "FAIL"
        det = ", ".join(f"{k} {r[k]}" for k in ("onset_dev_ms", "level_diff_db", "tail_diff_db", "held_diff_db",
                                                   "damped_diff_db", "shape_diff_db", "cents", "peak_lsb") if k in r)
        L.append(f"  {st} {r['event']:10s} {det}" + (f"  {r.get('manual', '')}" if r["ok"] is None else ""))
    L += [f"problem: {q}" for q in probs]
    txt = "\n".join(L) + "\n"
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "report.txt"), "w").write(txt)
    json.dump(dict(verdict=v, channel=ch + 1, port=pn, latency_diff_ms=med, rows=rows, problems=probs, usb=res["usb"],
                   trs=res["trs"], restored=restored, start_fingerprint=fp0), open(os.path.join(OUT, "report.json"), "w"),
              indent=1, default=float)
    print(txt, end="")
    return 0 if v == "PASS" else 1 if v == "FAIL" else 2


def dry_run(a):
    ev, marks, dur = script()
    print(f"hil_midi_parity plan: the script ({dur:.1f} s) over USB-MIDI, then over '{a.port or '$FRODHES_TRS_PORT'}'")
    for lab, kind, t in marks:
        msgs = [m for tt, _, m, _ in ev if abs(tt - t) < 1.2 and tt >= t - 0.6]
        print(f"  {t:6.2f} s  {lab:10s} {kind:8s} {msgs[:6]}")
    print(f"  before: the start sound's fingerprint; after: Program Change 0..{N_PROGRAMS - 1} until one matches it")
    print(f"  pass: onset within {1000 * ONSET_TOL_S:.1f} ms of the median offset, level {LEVEL_TOL_DB} dB, band shape "
          f"{SHAPE_TOL_DB} dB, bend {BEND_TOL_CENTS} cents, another channel silent; ~3 min; {OUT}/report.txt")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default=os.environ.get("FRODHES_TRS_PORT", ""))
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--lock-wait", type=float, default=1800.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if a.dry_run:
        return dry_run(a)
    if not a.port:
        ap.error("--port NAME (the interface output wired to the TRS IN) or FRODHES_TRS_PORT")
    return run_device(a)


if __name__ == "__main__":
    sys.exit(main())
