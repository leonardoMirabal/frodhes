#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The plugin in third-party hosts, scripted (macOS): a host script's MIDI (tests/host.c format) rendered through
  - the Audio Unit in Apple's own AU host (AVAudioEngine offline rendering, tools/plugin_au_render.swift, each event
    at its exact frame): must equal the CLAP render of the project's headless host (plugin/tests/clap_render.c,
    Gate D's reference) sample for sample, at 44.1 and 48 kHz;
  - the VST3 and the Audio Unit in a JUCE-based host (pedalboard, the plugin-hosting code of many DAWs): level,
    length, NaN / inf, silence and the loudness envelope against the CLAP render. Not sample for sample: that host
    plays a probe note (middle C, then all notes off) into every instrument when it loads it, so the voices and the
    effects' tails start from a different state;
  - the name / vendor / version / category / latency / parameter count each host reads, and a state round trip
    through the JUCE host (saved, loaded into a fresh instance, saved again: the same bytes).

  python3 tools/plugin_hosts.py [--bundles DIR] [--render EXE] [--script tests/scripts/demo.txt] [--out DIR]
                                [--rates 44100,48000] [--no-au] [--au PATH]

DIR holds Frodhes.clap / .vst3 / .component (default build/plugin/bundles; an installed ~/Library/Audio/Plug-Ins
layout works too). The Audio Unit must be installed and registered (~/Library/Audio/Plug-Ins/Components) for both AU
hosts; --no-au skips them. Needs pedalboard + numpy (pip install pedalboard numpy) and swiftc (Xcode's command line
tools). Writes DIR/report.md. Exit 0 pass, 1 fail.
"""
import argparse
import math
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def midi_events(script):
    ev, end = [], 6500
    for ln in open(script):
        f = ln.split("#")[0].split()
        if len(f) >= 2 and f[1] == "end":
            end = int(f[0])
        elif len(f) >= 5 and f[1] == "midi":
            ev.append((int(f[0]), [int(x, 0) for x in f[2:5]]))
    return ev, end


def read_wav(path):
    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
        return a.reshape(-1, w.getnchannels()).T / 32768.0


def q16(x):
    return np.clip(np.round(np.asarray(x, dtype=np.float64) * 32768.0), -32767, 32767) / 32768.0


def db(x):
    return 20 * math.log10(max(float(x), 1e-12))


def env50(x, rate):
    n = int(rate * 0.05)
    k = x.shape[1] // n
    return np.sqrt(np.mean(x[:, : k * n].reshape(2, k, n) ** 2, axis=(0, 2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundles", default=os.path.join(ROOT, "build/plugin/bundles"))
    ap.add_argument("--render", default=os.path.join(ROOT, "build/plugin/frp_clap_render"))
    ap.add_argument("--script", default=os.path.join(ROOT, "tests/scripts/demo.txt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build/plugin-hosts"))
    ap.add_argument("--rates", default="44100,48000")
    ap.add_argument("--no-au", action="store_true")
    ap.add_argument("--au", help="the registered Frodhes.component (default: DIR's)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    def bundle(name):
        if name == "Frodhes.component" and a.au:
            return a.au
        p = os.path.join(a.bundles, name)
        if os.path.exists(p):
            return p
        sub = {"Frodhes.clap": "CLAP", "Frodhes.vst3": "VST3", "Frodhes.component": "Components"}[name]
        return os.path.join(a.bundles, sub, name)

    ev, end = midi_events(a.script)
    rows = ["| host | format | rate | frames | RMS dBFS | peak dBFS | vs CLAP | result |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    info, ok = [], True

    def row(host, fmt, rate, x, cmp, good):
        nonlocal ok
        ok &= good
        rms = float(np.sqrt(np.mean(np.nan_to_num(x) ** 2)))
        rows.append(f"| {host} | {fmt} | {rate} | {x.shape[1]} | {db(rms):.2f} | {db(np.max(np.abs(np.nan_to_num(x)))):.2f} | "
                    f"{cmp} | {'PASS' if good else 'FAIL'} |")

    au_exe = None
    if not a.no_au:
        au_exe = os.path.join(a.out, "plugin_au_render")
        src = os.path.join(ROOT, "tools/plugin_au_render.swift")
        if not os.path.exists(au_exe) or os.path.getmtime(au_exe) < os.path.getmtime(src):
            subprocess.run(["swiftc", "-O", "-o", au_exe, src], check=True, capture_output=True)
    try:
        import pedalboard
    except ImportError:
        pedalboard = None
        info.append("- pedalboard not installed: the JUCE host was skipped (pip install pedalboard)")
        ok = False

    for rate in [int(x) for x in a.rates.split(",")]:
        wav = os.path.join(a.out, f"clap_{rate}.wav")
        r = subprocess.run([a.render, bundle("Frodhes.clap"), a.script, wav, "--rate", str(rate)], capture_output=True, text=True)
        if r.returncode:
            rows.append(f"| clap_render | CLAP | {rate} | - | - | - | - | FAIL: exit {r.returncode} |")
            ok = False
            continue
        ref = read_wav(wav)
        row("clap_render (reference)", "CLAP", rate, ref, "-", bool(np.all(np.isfinite(ref))) and np.max(np.abs(ref)) > 0.01)

        # Apple's AU host: sample for sample
        if au_exe:
            evf = os.path.join(a.out, f"au_events_{rate}.txt")
            with open(evf, "w") as f:
                for t, b in ev:
                    fr = t * 48 if rate == 48000 else math.ceil(t * rate / 1000.0 - 1e-9)
                    f.write(f"{fr} {b[0]} {b[1]} {b[2]}\n")
            frames = ref.shape[1] + 1000
            raw = os.path.join(a.out, f"au_apple_{rate}.f32")
            r = subprocess.run([au_exe, str(rate), str(frames), evf, raw], capture_output=True, text=True)
            if r.returncode:
                rows.append(f"| AVAudioEngine | AU | {rate} | - | - | - | - | FAIL: {r.stderr.strip()[:80]} |")
                ok = False
            else:
                x = np.fromfile(raw, dtype=np.float32).astype(np.float64).reshape(-1, 2).T
                x16, lag = q16(x), None
                n = ref.shape[1]
                for L in range(0, 1000):   # the pre-roll the CLAP render left out (441 / 480 frames)
                    if x16.shape[1] - L >= n and np.array_equal(x16[:, L:L + n], ref):
                        lag = L
                        break
                row("AVAudioEngine (Apple)", "AU", rate, x[:, : n + 487], "identical (after the %s-frame pre-roll)" % lag
                    if lag is not None else "DIFFERS", lag is not None and bool(np.all(np.isfinite(x))))

        # the JUCE host: level and envelope
        if pedalboard:
            msgs = [(bytes(b), t / 1000.0) for t, b in ev]
            for fmt, path in [("VST3", bundle("Frodhes.vst3"))] + ([] if a.no_au else [("AU", bundle("Frodhes.component"))]):
                p = pedalboard.load_plugin(path)
                lat = int(p.reported_latency_samples)
                x = np.asarray(p(msgs, duration=end / 1000.0 + lat / rate, sample_rate=float(rate), num_channels=2,
                                 buffer_size=512), dtype=np.float64)
                g = x[:, lat:]
                k = min(g.shape[1], ref.shape[1])
                nan = int(np.sum(~np.isfinite(g)))
                g = np.nan_to_num(g[:, :k])
                rr = ref[:, :k]
                lvl = db(np.sqrt(np.mean(g ** 2))) - db(np.sqrt(np.mean(rr ** 2)))
                eg, er = env50(g, rate), env50(rr, rate)
                skip = int(1.0 / 0.05)        # (the probe note's tail: the first second)
                m = er[skip:] > 10 ** (-50 / 20)
                ed = np.abs(20 * np.log10(np.maximum(eg[skip:][m], 1e-9)) - 20 * np.log10(er[skip:][m]))
                e95 = float(np.percentile(ed, 95)) if len(ed) else 99.0
                good = nan == 0 and abs(lvl) <= 0.25 and e95 <= 1.0 and abs(g.shape[1] - ref.shape[1]) <= 1 and np.max(np.abs(g)) > 0.01
                row("pedalboard (JUCE)", fmt, rate, g,
                    f"level {lvl:+.2f} dB, envelope within {e95:.2f} dB (95%), {nan} NaN", good)
                if rate == int(a.rates.split(",")[0]):
                    st = p.raw_state
                    q = pedalboard.load_plugin(path)
                    q([], duration=0.1, sample_rate=float(rate))
                    q.raw_state = st
                    q([], duration=0.2, sample_rate=float(rate), reset=False)
                    same = st == q.raw_state
                    ok &= same and len(st) > 0
                    info.append(f"- {fmt} in the JUCE host: name `{p.name}`, vendor `{p.manufacturer_name}`, version "
                                f"`{p.version}`, category `{p.category}`, instrument {p.is_instrument}, latency {lat} "
                                f"samples at {rate} Hz, {len(p.parameters)} parameters; state {len(st)} bytes, round trip "
                                f"{'the same bytes' if same else 'DIFFERS'}")
                del p
    out = "\n".join(rows + [""] + info)
    print(out)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write(f"# The plugin in hosts ({os.path.basename(a.script)})\n\n{out}\n")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
