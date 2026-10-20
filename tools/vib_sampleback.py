#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""VIBES sample-back (PLAN P7.5): play the FM-1's own VIBES over MIDI, record its USB audio, measure every note with
the SAME procedure as the reference recordings (tools/vib_ref_measure.py), and compare the device with the model's
targets (docs/model/data/bars_spec.json) and with the reference set (measured in place, numbers only). The same
loop the Rhodes was tuned with: a difference is fixed in the data / generator, never by hand in the tables.

    python3 tools/vib_sampleback.py [--round N] [--keys 53-89] [--step 1] [--vel 100] [--ref DIR] [--dry-run]
    python3 tools/vib_sampleback.py --engine marimba [--round N] [--keys 36-96] [--hold 2.5] [--dry-run]

--engine marimba (MARIMBA PLAN §12 step 4, Gate VD-M): CC 79 127 (no motor), every key 36..96 at v100, 2.5 s, the
pedal down; each take measured by tools/mrb_ref_measure.measure (f0, t60_v1_30db, lufs, f0_peak_ms, v2_db, v3_db) and
compared with the host build playing the same events (hil_scan.HostRig) and with the golden model; the table goes to
private/marimba/research/P5-sampleback.md, the takes to build/mrb_sampleback/rN/.
--dry-run (either engine): the host build plays in the device's place (no FM-1, no CoreAudio); everything goes under
build/ (the private tables are not touched).

Writes build/vib_sampleback/rN/*.wav (the FM-1's own output) and appends a table to
private/vibraphone/research/P7-sampleback.md. Gate VD (PLAN §10): pitch +-1 c, V1 T60 +-10 % (pedal down), level
per key +-1 dB re its neighbours' trend, all **device vs the host render** of the same events measured the same way
(PLAN §10; the spec's modal T60 is not what a band fit of the fundamental reads, since the tube shares that
frequency: about 5 % apart on the host too). Needs the FM-1 on USB (sandbox off for CoreAudio).
"""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_scan as hs           # noqa: E402  (the HIL rig: MIDI out, USB audio in, a known panel state)
import vib_ref_measure as vm    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.expanduser("~/Desktop/grbaciones de campo/SAMPLEST37/Vibraphone Samples 4")
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FS = hs.FS


def name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def spec_targets():
    d = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
    out = {}
    for k in d["keys"]:
        modes = {m["name"]: m for m in k["modes"]}
        out[k["midi"]] = dict(f0=modes["V1"]["f"], t60=modes["V1"]["t60"],
                              v2=modes.get("V2", {}).get("f"), v2_t60=modes.get("V2", {}).get("t60"))
    return out


def reference(ref_dir):
    """the reference set, measured in place (numbers only), by MIDI note"""
    got = {}
    if not os.path.isdir(ref_dir):
        return got
    for f in sorted(os.listdir(ref_dir)):
        if f.lower().endswith(".wav"):
            r = vm.measure(os.path.join(ref_dir, f))
            if r:
                got[r["midi"]] = r
    return got


def fine_f0(x, f_guess):
    """the fundamental to ~0.1 c: 1.5 s from 0.3 s after the onset, Hann, 2^19-point FFT, parabolic interpolation on
    the log magnitude (the reference procedure's FFT bin is ~5 c at 220 Hz: too coarse for Gate VD's +-1 c)"""
    m = x.mean(axis=1) if x.ndim > 1 else x
    i0 = int(np.argmax(np.abs(m) > 0.1 * np.abs(m).max())) + int(0.3 * FS)
    seg = m[i0:i0 + int(1.5 * FS)]
    N = 1 << 19
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    k0 = int(round(f_guess * N / FS))
    w = max(3, int(0.03 * k0))
    k = k0 - w + int(np.argmax(sp[k0 - w:k0 + w + 1]))
    a, b, c = np.log(sp[k - 1:k + 2] + 1e-12)
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * FS / N


def measure_like_ref(path):
    """vib_ref_measure.measure on a copy with a recording-like -80 dBFS white floor: the engine ends in exact digital
    zero, and the reference procedure's fit (down to 10 dB over the floor) otherwise runs into that cliff and reads the
    decay ~6 % short (found by the P6 scan lane; Gate VA uses the same floor, bars_va.ref_band_t60)"""
    w = wave.open(path)
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float).reshape(-1, w.getnchannels())
    fs = w.getframerate()
    rng = np.random.default_rng(1)
    x = x + rng.standard_normal(x.shape) * 32768 * 10 ** (-80 / 20)
    tmp = path[:-4] + ".floor.wav"
    o = wave.open(tmp, "wb")
    o.setnchannels(x.shape[1]); o.setsampwidth(2); o.setframerate(fs)
    o.writeframes(np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes())
    o.close()
    r = vm.measure(tmp)
    os.unlink(tmp)
    return r


def v1_t60(r):
    rows = [p for p in r["rows"] if abs(p["ratio"] - 1) < 0.03 and p["t60"]]
    return max(rows, key=lambda p: p["lvl"])["t60"] if rows else None   # the fundamental itself, not a weak side peak


def host_render(m, vel, hold, out):
    """the same note on the host build (tests/host.c), measured like the device"""
    import subprocess
    base = os.path.join(out, f"VIBES {name(m)} v{vel} host")
    open(base + ".txt", "w").write(f"20 midi 0xB0 79 0\n21 midi 0xB0 47 0\n30 midi 0xB0 64 127\n100 midi 0x90 {m} {vel}\n"
                                   f"{int((hold - 0.6) * 1000)} midi 0x80 {m} 0\n{int(hold * 1000)} end\n")
    subprocess.run([os.path.join(ROOT, "build", "host", "frodhes"), base, base + ".txt"], check=True, capture_output=True)
    return measure_like_ref(base + ".wav")


def write_wav(path, x):
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(FS)
    w.writeframes(np.clip(x, -32768, 32767).astype("<i2").tobytes())
    w.close()


def dry_rig():
    """--dry-run: the host build in the device's place (hil_scan.HostRig: the panel state replayed each take, the take
    played 3-25 ms late, 44.1 -> 48 kHz), so the whole procedure runs without the FM-1"""
    return hs.HostRig(seed=7, tag="sbdry")


# ---------------------------------------------------------------------------------------------------- MARIMBA
MRB_GATE = dict(cents=1.0, t60_rel=0.10, pk_ms=10.0, pk_rel=0.15, lufs_db=1.0, v_db=2.0)   # Gate VD-M (PLAN §12.4)


def mrb_take(rig, m, vel, hold):
    """one MARIMBA note on rig (the FM-1 or HostRig): a known panel (hil_scan dry: every parameter at its default, the
    effects off), MARIMBA (CC 79 127; no motor), the pedal down (MUTE does not stop it at key-up), strike at 0.1 s,
    key-up 0.3 s before the end; returns the stereo take (int16 scale, hs.FS)"""
    rig.dry()
    rig.out.send(rig.mido.Message("control_change", control=79, value=127))     # MARIMBA
    rig.out.send(rig.mido.Message("control_change", control=64, value=127))     # pedal down: the free ring
    time.sleep(0.3)
    x = rig.take(hold, [(0.1, "on", m, vel), (hold - 0.3, "off", m, 0)])
    rig.out.send(rig.mido.Message("control_change", control=64, value=0))
    return x


def mrb_measures(x, m):
    """the sample-back's numbers, by mrb_ref_measure.measure (the reference procedure): f0 (cents vs ET),
    t60_v1_30db, lufs (0-0.4 s), f0_peak_ms, v2_db / v3_db (5-300 ms re V1)"""
    import mrb_ref_measure as mr
    mono = x.mean(axis=1) if x.ndim > 1 else x
    r = mr.measure(mono, m, FS)
    return None if r is None else {k: r[k] for k in ("f0", "cents", "t60_v1_30db", "lufs", "f0_peak_ms", "v2_db", "v3_db")}


def mrb_gate(d, h, m):
    """device d vs host h (both measured the same way): [(measure, ok)]"""
    G = MRB_GATE
    out = []

    def rel(a, b):
        return abs(a / b - 1) if a and b else None
    out.append(("pitch", abs(1200 * np.log2(d["f0"] / h["f0"])) <= G["cents"]))
    r = rel(d["t60_v1_30db"], h["t60_v1_30db"])
    out.append(("t60", r is None or r <= G["t60_rel"]))
    if m <= 60:
        dp = abs(d["f0_peak_ms"] - h["f0_peak_ms"])
        out.append(("bloom", dp <= G["pk_ms"] or dp <= G["pk_rel"] * max(h["f0_peak_ms"], 1.0)))
    out.append(("lufs", abs(d["lufs"] - h["lufs"]) <= G["lufs_db"]))
    for k in ("v2_db", "v3_db"):
        if d[k] is not None and h[k] is not None:
            out.append((k, abs(d[k] - h[k]) <= G["v_db"]))
    return out


def main_marimba(a):
    """PLAN §12 step 4 (Gate VD-M): every key at v100, a.hold s each, the device vs the host render of the same events
    (HostRig: the same panel state, the same MIDI), both measured by mrb_ref_measure.measure; the golden model's numbers
    (tools/bars_model.py, the DEFAULT cards) beside them for information. Table appended to
    private/marimba/research/P5-sampleback.md (a dry run: build/mrb_sampleback/dry-P5-sampleback.md)"""
    import mrb_ref_measure as mr
    lo, hi = (int(v) for v in a.keys.split("-"))
    keys = list(range(lo, hi + 1, a.step))
    tag = ("dry-" if a.dry_run else "") + f"r{a.round}"
    out = os.path.join(ROOT, "build", "mrb_sampleback", tag)
    os.makedirs(out, exist_ok=True)
    rig = dry_rig() if a.dry_run else hs.Rig()
    host = hs.HostRig(seed=11, tag="sbhost")
    gold = {r["midi"]: r for r in mr.golden(keys, a.vel, a.hold - 0.1) if r}
    rows = []
    for m in keys:
        x = mrb_take(rig, m, a.vel, a.hold)
        write_wav(os.path.join(out, f"MARIMBA {name(m)} v{a.vel}.wav"), x)
        d = mrb_measures(x, m)
        h = mrb_measures(mrb_take(host, m, a.vel, a.hold), m)
        g = gold.get(m)
        if not d or not h:
            rows.append(dict(midi=m, d=d, h=h, g=g, gate=[("measured", False)]))
            continue
        rows.append(dict(midi=m, d=d, h=h, g=g, gate=mrb_gate(d, h, m)))
        print(f"{name(m):4s} " + " ".join(f"{k}={'ok' if ok else 'FAIL'}" for k, ok in rows[-1]["gate"]), flush=True)
    rig.dry()
    f = lambda v, fmt: "-" if v is None else format(v, fmt)     # noqa: E731
    head = (f"\n## Round {a.round}{' DRY RUN (the host build in the device place)' if a.dry_run else ''} "
            f"({time.strftime('%Y-%m-%d %H:%M')}): MARIMBA (CC 79 127), v{a.vel}, pedal down, {a.hold} s, keys {a.keys} "
            f"step {a.step}\n")
    lines = [head, "Each cell: device / host / golden. Gate VD-M (device vs host): pitch +-1 c, V1 T60 (30 dB fit) +-10 %, "
             "bloom +-10 ms or +-15 % (keys <= 60), LUFS +-1 dB, V2 / V3 (5-300 ms re V1) +-2 dB.", "",
             "| key | f0 vs ET (c) | V1 T60 30 dB (s) | f0 peak (ms) | LUFS 0-0.4 s | V2 dB | V3 dB | VD-M |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    bad = 0

    def cell(q, k, fmt):
        return " / ".join(f(None if s is None else s.get(k), fmt) for s in (q["d"], q["h"], q["g"]))
    for q in rows:
        fails = [k for k, ok in q["gate"] if not ok]
        bad += bool(fails)
        lines.append(f"| {name(q['midi'])} | {cell(q, 'cents', '+.1f')} | {cell(q, 't60_v1_30db', '.2f')} | "
                     f"{cell(q, 'f0_peak_ms', '.0f')} | {cell(q, 'lufs', '.1f')} | {cell(q, 'v2_db', '.1f')} | "
                     f"{cell(q, 'v3_db', '.1f')} | {'ok' if not fails else 'FAIL ' + ', '.join(fails)} |")
    lines.append(f"\n{len(rows)} notes, {bad} outside Gate VD-M. Recordings: build/mrb_sampleback/{tag}/ "
                 f"({'the host build' if a.dry_run else 'the FM-1'}'s own output).")
    md = (os.path.join(ROOT, "build", "mrb_sampleback", "dry-P5-sampleback.md") if a.dry_run else
          os.path.join(ROOT, "private", "marimba", "research", "P5-sampleback.md"))
    os.makedirs(os.path.dirname(md), exist_ok=True)
    if not os.path.exists(md):
        open(md, "w").write("# P5 sample-back rounds (the FM-1's MARIMBA measured like the reference recordings)\n")
    open(md, "a").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="vibes", choices=("vibes", "marimba"))
    ap.add_argument("--dry-run", action="store_true", help="no device: the host build plays the takes (HostRig); "
                    "output under build/ only")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--keys", default=None, help="lo-hi (default 53-89 VIBES, 36-96 MARIMBA)")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--hold", type=float, default=None, help="s per note (default 8.0 VIBES, 2.5 MARIMBA)")
    ap.add_argument("--ref", default=REF)
    a = ap.parse_args()
    if a.engine == "marimba":
        a.keys, a.hold = a.keys or "36-96", a.hold or 2.5
        return main_marimba(a)
    a.keys, a.hold = a.keys or "53-89", a.hold or 8.0
    lo, hi = (int(v) for v in a.keys.split("-"))
    out = os.path.join(ROOT, "build", "vib_sampleback", ("dry-" if a.dry_run else "") + f"r{a.round}")
    os.makedirs(out, exist_ok=True)
    import subprocess
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    rig = dry_rig() if a.dry_run else hs.Rig()
    spec, ref = spec_targets(), reference(a.ref)
    rows = []
    for m in range(lo, hi + 1, a.step):
        rig.dry()
        rig.out.send(rig.mido.Message("control_change", control=79, value=64))      # VIBES
        rig.out.send(rig.mido.Message("control_change", control=47, value=0))       # the motor off (no motor refs)
        rig.out.send(rig.mido.Message("control_change", control=64, value=127))     # pedal down: the free ring
        time.sleep(0.3)
        x = rig.take(a.hold, [(0.1, "on", m, a.vel), (a.hold - 0.6, "off", m, 0)])
        rig.out.send(rig.mido.Message("control_change", control=64, value=0))
        p = os.path.join(out, f"VIBES {name(m)} v{a.vel}.wav")
        write_wav(p, x)
        r = measure_like_ref(p)
        if not r:
            continue
        s, q, h = spec.get(m), ref.get(m), host_render(m, a.vel, a.hold, out)
        rows.append(dict(midi=m, cents=1200 * np.log2(fine_f0(x, s["f0"]) / s["f0"]) if s else None, t60=v1_t60(r),
                         t60_host=v1_t60(h) if h else None,
                         t60_spec=s["t60"] if s else None, t60_ref=v1_t60(q) if q else None,
                         pk=r["f0pk_ms"], pk_ref=q["f0pk_ms"] if q else None,
                         level=float(20 * np.log10(np.sqrt(np.mean((x[int(0.1 * FS):int(1.1 * FS)] / 32768) ** 2)) + 1e-12))))
        print(f"{name(m):4s} {rows[-1]}", flush=True)
    rig.dry()
    lv = np.array([q["level"] for q in rows])
    trend = np.convolve(np.pad(lv, 2, mode="edge"), np.ones(5) / 5, "valid") if len(lv) >= 3 else lv
    lines = [f"\n## Round {a.round} ({time.strftime('%Y-%m-%d %H:%M')}): v{a.vel}, pedal down, keys {a.keys} step {a.step}\n",
             "| key | pitch vs spec (c) | V1 T60 device / host / spec / reference (s) | f0 peak device / ref (ms) | level dB (vs trend) | VD |",
             "| --- | --- | --- | --- | --- | --- |"]
    bad = 0
    for q, tr in zip(rows, trend):
        ok = (q["cents"] is None or abs(q["cents"]) <= 1) and \
             (q["t60"] is None or q["t60_host"] is None or abs(q["t60"] / q["t60_host"] - 1) <= 0.10) and \
             abs(q["level"] - tr) <= 1.0
        bad += not ok
        f = lambda v, fmt: "-" if v is None else format(v, fmt)
        lines.append(f"| {name(q['midi'])} | {f(q['cents'], '+.1f')} | {f(q['t60'], '.2f')} / {f(q['t60_host'], '.2f')} / {f(q['t60_spec'], '.2f')} / "
                     f"{f(q['t60_ref'], '.2f')} | {q['pk']:.0f} / {f(q['pk_ref'], '.0f')} | {q['level']:.1f} ({q['level'] - tr:+.1f}) | "
                     f"{'ok' if ok else 'FAIL'} |")
    if a.dry_run:
        lines[0] = lines[0].replace(f"## Round {a.round} ", f"## Round {a.round} DRY RUN (the host build in the device place) ")
    lines.append(f"\n{len(rows)} notes, {bad} outside Gate VD. Recordings: build/vib_sampleback/"
                 f"{'dry-' if a.dry_run else ''}r{a.round}/ ({'the host build' if a.dry_run else 'the FM-1'}'s own output).")
    md = (os.path.join(ROOT, "build", "vib_sampleback", "dry-P7-sampleback.md") if a.dry_run else
          os.path.join(ROOT, "private", "vibraphone", "research", "P7-sampleback.md"))
    if not os.path.exists(md):
        open(md, "w").write("# P7 sample-back rounds (the FM-1's VIBES measured like the reference recordings)\n")
    open(md, "a").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
