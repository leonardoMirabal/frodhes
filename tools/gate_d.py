#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate D (desktop plugin, B-M0): the CLAP plugin renders what the host build renders, bit for bit.

The plugin (build/plugin/bundles/Frodhes.clap, make plugin) is driven by plugin/tests/clap_render.c, a headless CLAP
host that plays a host script's MIDI and writes the WAV the host build writes (the 10 ms pre-roll skipped).

  classic   every MIDI-driven Gate R entry (tools/gate_m.py entries(): the Move's list, the same reasons for the
            host-only ones), ENGINE CLASSIC, 44.1 kHz, 512-frame blocks: the sha256 equals
            tests/baselines/move16.sha256 where the 16 voices change the render, else tests/baselines/renders.sha256
  blocks    a subset again with random block sizes (0..2048 frames a process call) and with CLAP note events in
            place of raw MIDI: the same sha256
  hq        ENGINE HQ (the plugin's default): tests/hq/*.txt and a subset, against the host build with the plugin's
            switches and HQ on (-DHQ_DEFAULT=1), byte for byte
  rate48    48 kHz: a subset (CLASSIC and HQ) against the host build's 44.1 kHz render through the FM-1's own USB
            resampler, compiled unchanged (tests/uac_fir_test.c, stdin mode), sample for sample
  rates     the other rates (8, 22.05, 88.2, 96, 192 kHz: the plugin's windowed-sinc resampler) against the host
            build's 44.1 kHz render resampled by FFT, both band-limited below the resampler's cutoff: the error's RMS
            <= 0.6 LSB and its peak <= 8 LSB (both WAVs are 16-bit: their rounding alone is ~0.4 LSB RMS); needs
            numpy (make gate-d runs the venv)
  x86_64    (macOS, universal build) classic and hq subsets with the plugin's x86_64 slice under Rosetta, the HQ
            against an x86_64 host build

  tools/gate_d.py [--jobs 2] [--only classic,hq,...] [--quick]
Light by default: --jobs 2, every process under nice -n 15.
"""
import argparse
import hashlib
import os
import platform
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate_m  # noqa: E402

OUT = ROOT / "build" / "gate_d"
PLUG_BUILD = Path(os.environ.get("FRP_BUILD", ROOT / "build" / "plugin"))
PLUGIN = PLUG_BUILD / "bundles" / "Frodhes.clap"
RENDER = PLUG_BUILD / "frp_clap_render"
# the plugin's engine switches (plugin/cmake/slots.cmake FRP_ENGINE_DEFS = the Makefile's MOVE_DEFS)
DEFS = ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]
NICE = ["nice", "-n", "15"]
MODES = ["classic", "blocks", "hq", "rate48", "rates", "x86_64"]
RATES = [8000, 22050, 88200, 96000, 192000]


def run(cmd, **kw):
    return subprocess.run(NICE + [str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True, **kw)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else "MISSING"


def host_build(tag, extra, arch=None):
    """the host build (tests/host.c through frodhes.c) with the plugin's switches + extra; built once per run"""
    out = OUT / "bin" / f"host-{tag}"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["cc", "-O2", "-w", "-DDINGHY_HOST", *DEFS, *extra, "-DAPP_VER=\"dev\"", "-Ifirmware/src", "-Ifirmware/gen",
           "-o", out, "firmware/src/frodhes.c", "-lm"]
    if arch:
        cmd[1:1] = ["-arch", arch]
    r = run(cmd)
    if r.returncode:
        raise SystemExit(f"Gate D: the host build {tag} failed:\n{r.stderr[-2000:]}")
    return out


def host_render(binary, name, script, arch=None):
    out = OUT / "host" / binary.name / name
    out.parent.mkdir(parents=True, exist_ok=True)
    pre = ["arch", f"-{arch}"] if arch else []
    run(pre + [binary, out, script])                    # (expects may fail: the sound is what counts, as Gate R)
    return out.with_suffix(".wav")


def plug_render(name, script, tag, args, arch=None):
    out = OUT / tag / (name + ".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    pre = ["arch", f"-{arch}"] if arch else []
    r = run(pre + [RENDER, PLUGIN, script, out, *args])
    if r.returncode:
        return None, (r.stdout + r.stderr).strip().splitlines()[-1:] or [f"exit {r.returncode}"]
    return out, None


def frames(wav):
    with wave.open(str(wav)) as w:
        raw = w.readframes(w.getnframes())
    import array
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder != "little":
        a.byteswap()
    return a


def subset(midi, k):
    """k entries spread over the list (the scripts, the effect cases, the calib renders), plus every steal script"""
    step = max(1, len(midi) // k)
    pick = midi[::step][:k]
    pick += [e for e in midi if "steal" in e[0] and e not in pick]
    return pick


class Gate:
    def __init__(self, jobs):
        self.jobs = jobs
        self.fails = []
        self.counts = {}

    def check(self, mode, name, ok, why=""):
        n, k = self.counts.get(mode, (0, 0))
        self.counts[mode] = (n + 1, k + (1 if ok else 0))
        if not ok:
            self.fails.append(f"{mode} {name}: {why}")
            print(f"DIFF  {mode} {name}: {why}", flush=True)

    def pmap(self, fn, items):
        with ThreadPoolExecutor(self.jobs) as ex:
            return list(ex.map(fn, items))


def want_of(base, want16, n):
    return want16.get(n, base.get(n)), ("move16" if n in want16 else "baseline")


def mode_classic(g, midi, base, want16, tag="classic", extra=(), arch=None):
    def one(e):
        n, s = e
        w, err = plug_render(n.replace("/", "_"), s, tag, ["--engine", "classic", *extra], arch)
        want, src = want_of(base, want16, n)
        got = sha(w) if w else "FAILED"
        g.check(tag, n, got == want, f"{src} {str(want)[:12]}, plugin {got[:12]} {' '.join(err or [])}")
        if w and got == want:
            w.unlink()
    g.pmap(one, midi)


def mode_hq(g, entries, hostbin, tag="hq", arch=None):
    def one(e):
        n, s = e
        ref = host_render(hostbin, n.replace("/", "_"), s, arch)
        w, err = plug_render(n.replace("/", "_"), s, tag, ["--engine", "hq"], arch)
        ok = w is not None and ref.exists() and ref.read_bytes() == w.read_bytes()
        g.check(tag, n, ok, f"host {sha(ref)[:12]}, plugin {sha(w)[:12] if w else 'FAILED'} {' '.join(err or [])}")
        if ok:
            w.unlink()
    g.pmap(one, entries)


def mode_rate48(g, entries, fir):
    def one(e):
        (n, s), hostbin, eng = e
        ref = host_render(hostbin, n.replace("/", "_"), s)
        w, err = plug_render(n.replace("/", "_") + "-" + eng, s, "rate48", ["--engine", eng, "--rate", "48000"])
        if not w or not ref.exists():
            g.check("rate48", f"{n} {eng}", False, f"no render {' '.join(err or [])}")
            return
        a = frames(ref)
        txt = "".join(f"{a[i]} {a[i + 1]}\n" for i in range(0, len(a), 2))
        r = subprocess.run(NICE + [str(fir), "-"], input=txt, capture_output=True, text=True)
        want = [int(x) for x in r.stdout.split()]
        got = frames(w)
        m = min(len(want), len(got))
        # the host WAV clips at +-32767, while the resampler (on the FM-1 and in the plugin) takes the app's unclipped
        # samples (an s16 clamp): from the first clipped reference sample on, the reference is not the FM-1's input,
        # so the comparison stops 12 input frames (the FIR's span) before it
        clip = next((i // 2 for i in range(len(a)) if a[i] in (32767, -32767)), None)
        if clip is not None:
            m = min(m, 2 * max(0, (clip - 12) * 48000 // 44100))
        bad = next((i for i in range(m) if want[i] != got[i]), None)
        ok = bad is None and m > 0 and abs(len(want) - len(got)) <= 2 * 2
        g.check("rate48", f"{n} {eng}", ok,
                f"first difference at frame {bad // 2 if bad is not None else '-'} of {m // 2} "
                f"(the resampler {len(want) // 2} frames, the plugin {len(got) // 2})")
        if ok and clip is not None:
            print(f"note  rate48 {n} {eng}: compared {m // 2} of {len(got) // 2} frames (the reference clips at "
                  f"44.1 kHz frame {clip})", flush=True)
        if ok:
            w.unlink()
    g.pmap(one, entries)


def band_snr(ref_wav, wav):
    """SNR (dB), RMS and peak error (LSB) of wav (rate R) against ref_wav (44.1 kHz) resampled by FFT, both band-limited"""
    import numpy as np

    def rd(p):
        with wave.open(str(p)) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float64)
            return a, w.getframerate()
    ref, _ = rd(ref_wav)
    x, rate = rd(wav)
    n = len(ref) // 882 * 882                   # (n x rate / 44100 whole for every rate a multiple of 50 Hz)
    m = n * rate // 44100
    band = min(20000.0, 0.96 * min(rate, 44100) / 2 * 0.95)
    f = np.fft.rfft(ref[:n], axis=0)
    f[np.fft.rfftfreq(n, 1 / 44100) > band] = 0
    g = np.zeros((m // 2 + 1, 2), complex)
    k = min(len(g), len(f))
    g[:k] = f[:k]
    y = np.fft.irfft(g, m, axis=0) * (m / n)
    L = min(len(y), len(x))
    X = np.fft.rfft(x[:L], axis=0)
    X[np.fft.rfftfreq(L, 1 / rate) > band] = 0
    xb = np.fft.irfft(X, L, axis=0)
    s = slice(int(0.05 * L), int(0.95 * L))     # (the FFT's wrap-around at the ends)
    e = xb[s] - y[:L][s]
    return (10 * np.log10(np.sum(y[:L][s] ** 2) / max(np.sum(e ** 2), 1e-30)), float(np.sqrt(np.mean(e ** 2))),
            float(np.max(np.abs(e))), band)


def mode_rates(g, entries):
    def one(e):
        (n, s), hostbin, eng, rate = e
        ref = host_render(hostbin, n.replace("/", "_"), s)
        w, err = plug_render(f"{n.replace('/', '_')}-{eng}-{rate}", s, "rates", ["--engine", eng, "--rate", str(rate)])
        if not w or not ref.exists():
            g.check("rates", f"{n} {eng} {rate}", False, f"no render {' '.join(err or [])}")
            return
        snr, rms, peak, band = band_snr(ref, w)
        ok = rms <= 0.6 and peak <= 8.0
        if ok:
            print(f"ok    rates {n} {eng} {rate} Hz: error RMS {rms:.2f} LSB, peak {peak:.2f} LSB, SNR {snr:.1f} dB "
                  f"(0..{band:.0f} Hz)", flush=True)
        g.check("rates", f"{n} {eng} {rate}", ok, f"error RMS {rms:.2f} LSB, peak {peak:.2f} LSB, SNR {snr:.1f} dB")
        if ok:
            w.unlink()
    g.pmap(one, entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--only", default=",".join(MODES), help="the modes, comma separated: " + ", ".join(MODES))
    ap.add_argument("--quick", action="store_true", help="classic on a subset of 12 too")
    a = ap.parse_args()
    modes = [m for m in a.only.split(",") if m]
    if not PLUGIN.exists() or not RENDER.exists():
        raise SystemExit("Gate D: build the plugin first (make plugin)")
    midi, host = gate_m.entries()
    base, want16 = gate_m.read_sums(gate_m.BASE), gate_m.read_sums(gate_m.BASE16)
    g = Gate(a.jobs)
    sub = subset(midi, 12)
    hq_list = [(f"hq/{p.stem}", p.relative_to(ROOT)) for p in sorted((ROOT / "tests" / "hq").glob("*.txt"))]

    if "classic" in modes:
        mode_classic(g, sub if a.quick else midi, base, want16)
    if "blocks" in modes:
        bsub = subset(midi, 6)
        mode_classic(g, bsub, base, want16, "blocks-random", ["--block", "random:7"])
        mode_classic(g, bsub, base, want16, "blocks-1", ["--block", "1"])
        mode_classic(g, bsub, base, want16, "notes-clap", ["--dialect", "clap", "--block", "random:11"])
    if "hq" in modes or "rate48" in modes:
        hq_bin = host_build("hq", ["-DHQ_DEFAULT=1"])
    if "hq" in modes:
        mode_hq(g, hq_list + subset(midi, 8), hq_bin)
    if "rate48" in modes:
        fir = OUT / "bin" / "uac_fir_test"
        r = run(["cc", "-O1", "-w", "-Ifirmware/src", "-o", fir, "tests/uac_fir_test.c"])
        if r.returncode:
            raise SystemExit(f"Gate D: tests/uac_fir_test.c did not build:\n{r.stderr[-1500:]}")
        c16 = host_build("c16", [])
        mode_rate48(g, [(e, c16, "classic") for e in subset(midi, 4)] + [(e, hq_bin, "hq") for e in hq_list[:2]], fir)
    if "rates" in modes:
        try:
            import numpy  # noqa: F401
        except ImportError:
            numpy = None
            print("Gate D: rates skipped (no numpy: run it with the venv, make gate-d)")
        if numpy:
            hq_bin = hq_bin if "hq_bin" in locals() else host_build("hq", ["-DHQ_DEFAULT=1"])
            c16 = c16 if "c16" in locals() else host_build("c16", [])
            two = subset(midi, 2)[:2]
            mode_rates(g, [(e, c16, "classic", r) for e in two for r in RATES] +
                       [(hq_list[3], hq_bin, "hq", r) for r in (22050, 96000)])
    if "x86_64" in modes and platform.system() == "Darwin":
        lipo = subprocess.run(["lipo", "-archs", str(PLUGIN / "Contents" / "MacOS" / "Frodhes")],
                              capture_output=True, text=True).stdout.split()
        rlipo = subprocess.run(["lipo", "-archs", str(RENDER)], capture_output=True, text=True).stdout.split()
        if "x86_64" in lipo and "x86_64" in rlipo and platform.machine() == "arm64":
            mode_classic(g, subset(midi, 8), base, want16, "x86_64-classic", arch="x86_64")
            mode_hq(g, hq_list[:3] + subset(midi, 3), host_build("hq-x86_64", ["-DHQ_DEFAULT=1"], "x86_64"),
                    "x86_64-hq", "x86_64")
        else:
            print(f"Gate D: x86_64 skipped (the plugin {lipo}, clap_render {rlipo}, this machine "
                  f"{platform.machine()})")
    for m, (n, k) in g.counts.items():
        print(f"Gate D {m}: {k} of {n} pass")
    print(f"Gate D: {'PASS' if not g.fails else 'FAIL'} ({sum(k for _, k in g.counts.values())} checks pass, "
          f"{len(g.fails)} fail; {len(host)} Gate R entries host-only, as Gate M)")
    return 1 if g.fails else 0


if __name__ == "__main__":
    sys.exit(main())
