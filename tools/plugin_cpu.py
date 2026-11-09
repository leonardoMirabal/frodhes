#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's CPU per instance (macOS): the worst case, 24 voices held with every effect on, rendered
offline through each format, timed, against the audio's own length (the real-time ratio: 0.05 = the instance takes
5 % of one core while it plays).

  CLAP          plugin/tests/clap_render --bench 1 (the time inside process() only), 512-frame blocks
  VST3, AU      a JUCE-based host (pedalboard: the plugin-hosting code of many DAWs), 512-frame blocks; the time of
                its whole render call (the wrapper's own work included); the Audio Unit must be installed and
                registered (~/Library/Audio/Plug-Ins/Components) and the VST3 is loaded from --bundles

The scenarios (hil_cpu.py --worst's, at 24 voices): every effect on at its heaviest (hil_cpu's "heavy": DRIVE, the
EQ, the compressor, the wah, the phaser, the chorus, the delay at its longest with feedback, the reverb at full mix,
the filter LP18 with its LFO) and the strike at its edge (TONE, BARK, BELL, HAMMER 127); 24 keys at velocity 127 with
the sustain pedal down, struck again every 2 s (the strike is the costliest block); each REVERB TYPE; STAGE 77 (the
Rhodes), VIBES (its motor on) and MARIMBA (the bar engine); 44.1 and 48 kHz (48: the plugin's resampler too).

  tools/plugin_cpu.py [--bundles DIR] [--render EXE] [--secs 20] [--rates 44100,48000] [--formats clap,vst3,au]
                      [--out build/plugin_cpu]
Needs pedalboard + mido for the VST3 / AU (pip install pedalboard mido; else those formats are skipped). Writes
OUT/report.md. Exit 0 (it measures; it does not judge).
"""
import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from hil_scan import CC  # noqa: E402

CC = dict(CC, TONE=28, BARK=29, BELL=30, HAMMER=31, RV_TYPE=95, EQ_LO=34, EQ_MID=35, EQ_HI=37)
HEAVY = dict(DRIVE=127, EQ_LO=127, EQ_MID=0, EQ_HI=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127,
             PH_DEPTH=127, PH_FDBK=127, CP_SW=64, CP_DEPTH=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127,
             REV=127, FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64, TONE=127, BARK=127, BELL=127, HAMMER=127)
ENGINES = {                                   # (the TYPE's CCs, the 24 keys)
    "STAGE 77": ([(80, 0)], list(range(40, 62)) + [105, 108]),   # (hil_cpu's mid + top voicing at 24)
    "VIBES": ([(79, 0)], [36 + 2 * k for k in range(24)]),
    "MARIMBA": ([(79, 127)], [36 + 2 * k for k in range(24)]),
}
RV_TYPES = [0, 43, 85, 127]                   # the four REVERB TYPEs (CC 95 across its range)


def script_events(engine, rv, secs):
    """(ms, status, d1, d2) events and the end ms"""
    tcc, keys = ENGINES[engine]
    ev = [(20, 0xB0, c, v) for c, v in tcc]
    ev += [(30, 0xB0, CC[k], v) for k, v in HEAVY.items()]
    ev += [(30, 0xB0, CC["RV_TYPE"], rv), (40, 0xB0, 64, 127)]
    t = 200
    while t + 2000 <= secs * 1000 - 100:   # (every event before the end)
        ev += [(t, 0x90, k, 127) for k in keys]
        ev += [(t + 1900, 0x80, k, 0) for k in keys]   # (the pedal holds them: struck again at t + 2000)
        t += 2000
    return ev, secs * 1000


def write_script(path, ev, end):
    with open(path, "w") as f:
        for ms, st, d1, d2 in ev:
            f.write(f"{ms} midi {st:#x} {d1} {d2}\n")
        f.write(f"{end} end\n")


def bench_clap(render, bundles, script, out, rate):
    r = subprocess.run([render, os.path.join(bundles, "Frodhes.clap"), script, out, "--rate", str(rate),
                        "--engine", "hq", "--bench", "1"], capture_output=True, text=True)
    m = re.search(r"process ([\d.]+) s for ([\d.]+) s .*?the worst call (\d+) us for (\d+) frames", r.stdout)
    if r.returncode or not m:
        return None
    return float(m.group(1)) / float(m.group(2)), int(m.group(3)), int(m.group(4))


def bench_pedalboard(path, ev, end, rate):
    import mido
    import pedalboard
    p = pedalboard.load_plugin(path)
    msgs = [mido.Message.from_bytes([st, d1, d2], time=ms / 1000.0) for ms, st, d1, d2 in ev]
    t0 = time.perf_counter()
    p(msgs, duration=end / 1000.0, sample_rate=rate, num_channels=2, buffer_size=512)
    dt = time.perf_counter() - t0
    return dt / (end / 1000.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundles", default=os.path.join(ROOT, "build", "plugin", "bundles"))
    ap.add_argument("--render", default=os.path.join(ROOT, "build", "plugin", "frp_clap_render"))
    ap.add_argument("--secs", type=int, default=20)
    ap.add_argument("--rates", default="44100,48000")
    ap.add_argument("--formats", default="clap,vst3,au")
    ap.add_argument("--au", default=os.path.expanduser("~/Library/Audio/Plug-Ins/Components/Frodhes.component"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "plugin_cpu"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rates = [int(x) for x in a.rates.split(",")]
    fmts = a.formats.split(",")
    try:
        import mido  # noqa: F401
        import pedalboard  # noqa: F401
    except ImportError:
        print("plugin_cpu: no pedalboard / mido: the VST3 and the AU are skipped")
        fmts = [f for f in fmts if f == "clap"]
    rows = []
    for eng in ENGINES:
        for rv in RV_TYPES:
            ev, end = script_events(eng, rv, a.secs)
            scr = os.path.join(a.out, f"{eng.replace(' ', '_')}_rv{rv}.txt")
            write_script(scr, ev, end)
            for rate in rates:
                row = dict(engine=eng, rv=rv, rate=rate)
                if "clap" in fmts:
                    b = bench_clap(a.render, a.bundles, scr, os.path.join(a.out, "x.wav"), rate)
                    row["clap"], row["clap_worst"] = (b[0], b[1] / (b[2] * 1e6 / rate)) if b else (None, None)
                if "vst3" in fmts:
                    row["vst3"] = bench_pedalboard(os.path.join(a.bundles, "Frodhes.vst3"), ev, end, rate)
                if "au" in fmts and os.path.exists(a.au):
                    row["au"] = bench_pedalboard(a.au, ev, end, rate)
                rows.append(row)
                print(f"{eng:9s} RV TYPE {rv:3d} {rate} Hz: " + "  ".join(
                    f"{f.upper()} {row[f]:.4f}" for f in ("clap", "vst3", "au") if row.get(f) is not None) +
                    (f"  (CLAP worst 512-frame call {row['clap_worst']:.3f} of its time)" if row.get("clap_worst") else ""),
                    flush=True)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write("| engine | REVERB TYPE | rate | CLAP | CLAP worst call | VST3 | AU |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['engine']} | {r['rv']} | {r['rate']} | " + " | ".join(
                f"{r[k]:.4f}" if r.get(k) is not None else "-" for k in ("clap", "clap_worst", "vst3", "au")) + " |\n")
        for fmt in ("clap", "vst3", "au"):
            v = [(r[fmt], r) for r in rows if r.get(fmt) is not None]
            if v:
                w, r = max(v, key=lambda x: x[0])
                line = (f"{fmt.upper()} worst: {w:.4f} of real time ({1 / w:.0f} x faster than real time), {r['engine']}, "
                        f"REVERB TYPE {r['rv']}, {r['rate']} Hz")
                print(line)
                f.write(f"\n{line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
