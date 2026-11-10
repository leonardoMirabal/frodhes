#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The note latency on the FM-1: a MIDI note-on over USB to its first sound in the FM-1's USB audio.

Single notes from silence, their send times spread over the audio block's phase (a 0..6.4 ms jitter on each gap),
the USB audio recorded as float32 at --rate (one input stream for the whole run, blocksize 64). Every callback's ADC
time is kept and each note-on is stamped on the same stream clock, so a note's latency is the time of its first USB
sample over THR LSB minus the send's time. The figure includes the computer's side (USB input, CoreAudio's input
buffer): compare builds on the same unit, in the same session (the difference is the firmware's). A note whose
previous 30 ms are not silent is not counted.

    python3 tools/hil_latency.py [--port NAME --location 0x...] [--notes 40] [--rate 48000|44100] [--raw]
                                 [--engine rhodes|vibes|marimba] [--note 72] [--json OUT]

Default sound: every effect but the compressor off (CC 111 127, as tools' earlier latency takes), the TYPE's
defaults (every parameter's, CC 120 first); --raw: the engine's own output (CC 119 127: no limiter look-ahead). Prints median, p10, p90, min, max in ms
and writes build/hil/latency.json (or --json). Needs the macOS sandbox off (CoreAudio)."""
import argparse
import json
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm1_ports  # noqa: E402

THR = 8 / 32768.0                 # the onset: the first sample over 8 LSB (USB is digital silence between notes)
QUIET_MS = 30.0                   # a note counts when its previous 30 ms are below THR
ENGINE_CC = {"rhodes": None, "vibes": 0, "marimba": 127}   # CC 79 (the bar engine) value; rhodes: the defaults


def stats(lat):
    a = np.asarray(lat, float)
    return dict(n=int(a.size), median=float(np.median(a)), p10=float(np.percentile(a, 10)),
                p90=float(np.percentile(a, 90)), min=float(a.min()), max=float(a.max()))


def onsets(x, sr, marks, sends, thr=THR):
    """x: the recording (mono float); marks: [(first frame, ADC time)] per callback; sends: note-on times on the
    stream clock. Returns the latencies in ms (None for a note not counted)"""
    f = np.array([m[0] for m in marks], float)
    t = np.array([m[1] for m in marks], float)
    a = np.abs(x)
    out = []
    for s in sends:
        k = int(np.searchsorted(t, s))
        k = min(max(k, 1), len(t) - 1)
        j = k - 1 if abs(t[k - 1] - s) <= abs(t[k] - s) else k
        i0 = int(round(f[j] + (s - t[j]) * sr))                    # the send's frame
        q = int(QUIET_MS * sr / 1000)
        if i0 - q < 0 or i0 >= len(a) or a[i0 - q:i0].max() > thr:
            out.append(None)
            continue
        hit = np.flatnonzero(a[i0:i0 + int(0.2 * sr)] > thr)
        out.append(None if not hit.size else 1000.0 * hit[0] / sr)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port")
    ap.add_argument("--location")
    ap.add_argument("--notes", type=int, default=40)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--engine", default="rhodes", choices=sorted(ENGINE_CC))
    ap.add_argument("--note", type=int, default=72)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--jack", type=int, metavar="CH", help="record the jack on this input of --dev (1-based)")
    ap.add_argument("--dev", default="M6", help="the jack's interface (with --jack)")
    ap.add_argument("--json", default=os.path.join(os.path.dirname(__file__), "..", "build", "hil", "latency.json"))
    a = ap.parse_args(argv)
    if a.location and not a.port:
        sys.exit("--location needs --port")
    if a.port:
        fm1_ports.force(a.port, fm1_ports.parse_loc(a.location) if a.location else None)
    import mido
    import sounddevice as sd
    import fm1_midi
    import midi_guard
    names = fm1_ports.pick(mido.get_output_names())
    if not names:
        sys.exit("no FM-1 MIDI port")
    out = fm1_midi.ChannelOut(midi_guard.open_output(mido, names[0]))
    print(fm1_midi.say(out.ch), flush=True)
    if a.jack:                                       # the jack: the interface's input, at its own rate
        devs = [i for i, d in enumerate(sd.query_devices()) if d["name"].startswith(a.dev) and d["max_input_channels"]]
        if not devs:
            sys.exit(f"no {a.dev} input")
        nch, col = sd.query_devices(devs[0])["max_input_channels"], a.jack - 1
        a.rate = int(sd.query_devices(devs[0])["default_samplerate"])
    else:
        devs = fm1_ports.devices(sd.query_devices(), 2)
        if not devs:
            sys.exit("no FM-1 audio input")
        nch, col = 2, 0
    cc = lambda n, v: out.send(mido.Message("control_change", control=n, value=v))  # noqa: E731
    import hil_scan
    cc(120, 0)
    for c, v in hil_scan.param_defaults():           # a known state: every parameter at its default (the Rhodes)
        cc(c, v)
    cc(64, 0)
    if ENGINE_CC[a.engine] is not None:
        cc(79, ENGINE_CC[a.engine])
    cc(119 if a.raw else 111, 127)
    rng = random.Random(a.seed)
    gap, hold = 0.55, 0.12
    dur = 1.0 + a.notes * (gap + 0.0064) + 1.0
    buf = np.zeros(int(dur * a.rate) + a.rate, np.float32)
    pos, marks = [0], []

    def cb(indata, frames, ti, status):
        p = pos[0]
        marks.append((p, ti.inputBufferAdcTime))
        m = min(frames, len(buf) - p)
        if m > 0:
            buf[p:p + m] = indata[:m, col]
        pos[0] = p + frames

    s = sd.InputStream(device=devs[0], samplerate=a.rate, channels=nch, dtype="float32", callback=cb, blocksize=64,
                       latency="low")
    sends = []
    try:
        s.start()
        time.sleep(1.0)
        for i in range(a.notes):
            out.send(mido.Message("note_on", note=a.note, velocity=100))
            sends.append(s.time)
            time.sleep(hold)
            out.send(mido.Message("note_off", note=a.note, velocity=0))
            time.sleep(gap - hold + rng.random() * 0.0064)
        time.sleep(0.5)
    finally:
        s.stop()
        s.close()
        cc(119 if a.raw else 111, 0)
    x = buf[:min(pos[0], len(buf))]
    thr = THR                                        # the jack has a noise floor: 4x the first second's peak
    if a.jack:
        thr = max(THR, 4.0 * float(np.abs(x[int(0.2 * a.rate):int(0.9 * a.rate)]).max()))
    lat = onsets(x, a.rate, marks, sends, thr)
    ok = [v for v in lat if v is not None]
    if len(ok) < max(5, a.notes // 2):
        sys.exit(f"hil_latency: only {len(ok)} of {a.notes} notes counted (the FM-1 not sounding, or not silent)")
    st = stats(ok)
    print(f"{a.label or 'latency'}: MIDI -> {'jack' if a.jack else 'USB audio'} ms over {st['n']} notes: median {st['median']:.2f} p10 "
          f"{st['p10']:.2f} p90 {st['p90']:.2f} (min {st['min']:.2f} max {st['max']:.2f})")
    os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
    with open(a.json, "w") as fh:
        json.dump(dict(st, label=a.label, rate=a.rate, raw=a.raw, engine=a.engine, lat=lat), fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
