#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE TYPEs on the FM-1: the audio CPU per type (tools/hil_cpu.py) and jack + USB recordings per type
(tools/hil_jack.py's recorder). Run it when the device is free, with the firmware under test installed, the sandbox
off (CoreAudio), MASTER fully up and no headphones in the FM-1.

    tools/hil_drive.py --cpu  [--engines rhodes,vibes,marimba] [--types FDRV,KHLON,...] [--variants base,mix,switch]
                              [--runs 3] [--usb 48000] [--wait-lock 3600] [--dry-run]
    tools/hil_drive.py --jack [--dev M6] [--ch 5] [--types ...] [--phrases riff,bass,vibes] [--wait-lock 3600]
                              [--dry-run]

--cpu: for each engine, type and variant, `hil_cpu.py --worst --usb RATE --engine E` RUNS times (every effect on, the
drive at AMOUNT 127, the engine's own voice count; hil_cpu's "slow paths" scenario has MIX 64 in every run). The
type is chosen by CC 84 (its band's middle, params.c DRV_CC84) at every hil_cpu reset, before the drive is turned up.
Variants:
  base    the type at MIX 127 (all driven), as hil_cpu sets the rest
  mix     MIX 100 (CC 83) in every scenario: the blend in the type's loop on every block
  switch  CC 84 toggles between the type and the next one (KHLON -> TSH8 -> OHCD -> BHLUE -> KHLON, FDRV <-> KHLON)
          every 0.12 s for the whole run: a fade out then in (46 + 46 ms) on most blocks, the larger type's cost
FDRV is the A/B reference. Each run's whole-ISR worst (hil_cpu "whole ISR worst", CC 107/108) and the app's worst
block are recorded against the 4500 us limit, with hil_cpu's verdict (a shed voice, a USB fault). Exit 1 when any
run fails. Results: build/hil/drive/cpu-<time>.json and .md, each run's log in build/hil/drive/cpu-<time>/.

--jack: each type at the p1x1 listening grid's settings (private plan: DRIVE low / noon / max at TONE noon, DRIVE
max at TONE min / max; KHLON also GAIN min and 9 o'clock), on three phrases (an ST77 riff, a chord over a walking
bass, a VIBES phrase with the pedal: the p0 renders' phrases), plus OFF and FDRV references; every effect but the
drive off. The headphone jack (the MOTU input --ch) and USB at once: build/hil/drive/jack-<time>/<type>_<phrase>_
<setting>_{jack,usb}.wav (48 kHz) and levels.json (LUFS re OFF per take, the USB peak). AMOUNT "low" is 1 (AMOUNT 0
is dry: the circuit's lowest setting is AMOUNT 1). The jack is the reference; the user listens.

Both take the device lock (tools/lock.py DEVICE, <main repo>/build/hil/DEVICE.lockdir; a caller holding it,
FRODHES_LOCK_DEVICE, passes at once; wait up to --wait-lock s, released at the end). --dry-run prints the plan and the time it takes: no device, no lock.
Needs release-0.9.5's tools/hil_cpu.py (run_cpu, --usb RATE, the whole ISR line).
"""
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lock  # noqa: E402

LIMIT_US = 4500
SWITCH_S = 0.12
VARIANTS = ("base", "mix", "switch")
ENGINES = ("rhodes", "vibes", "marimba")
MIX_LOW = 100                                          # CC 83 for the "mix" variant (10.0 = 127 is all driven)


# ---------------------------------------------------------------- the types (params.c)
def drive_types(src=None):
    """[(name, index, cc84)] for every drive TYPE but OFF, in params.c order: N_DRV's names, DRV_CC84's band starts;
    the CC value is the band's middle (FDRV 127, as hil_scan.drv_cc)"""
    src = src or open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", src).group(1))
    lo = [int(v) for v in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", src).group(1).split(",")]
    ends = sorted(lo) + [128]
    out = []
    for i, n in enumerate(names):
        if i == 0:
            continue                                   # OFF
        cc = 127 if i == 1 else (lo[i] + ends[ends.index(lo[i]) + 1] - 1) // 2
        out.append((n, i, cc))
    return out


def partner(name, types):
    """the type a switch run alternates with: the next circuit type (cyclic), FDRV with the first circuit type"""
    circ = [n for n, i, _ in types if i >= 2]
    if name not in circ:
        return circ[0]
    return circ[(circ.index(name) + 1) % len(circ)]


# ---------------------------------------------------------------- the device lock
def main_repo(start=ROOT):
    try:
        g = subprocess.run(["git", "-C", start, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        if g:
            return os.path.dirname(g)
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def DeviceLock(root, who, wait_s, poll_s=10.0):
    """<root>/build/hil/DEVICE.lockdir through tools/lock.py: waits while another session holds it, passes at once for
    the holder's own tools (FRODHES_LOCK_DEVICE), reaps a dead holder; removed at the end only when still ours"""
    return lock.Lock("DEVICE", root=root, who=who, wait_s=wait_s, poll_s=poll_s)


# ---------------------------------------------------------------- the CPU plan
def cpu_plan(engines, names, variants, runs, rate):
    """[dict(engine, type, cc84, variant, other, other_cc84, run, argv)] in the order they run: per engine, per type,
    per variant, RUNS runs"""
    types = drive_types()
    by = {n: (i, cc) for n, i, cc in types}
    plan = []
    for e in engines:
        for n in names:
            for v in variants:
                o = partner(n, types) if v == "switch" else None
                for r in range(runs):
                    plan.append(dict(engine=e, type=n, cc84=by[n][1], variant=v, other=o,
                                     other_cc84=by[o][1] if o else None, run=r + 1,
                                     argv=["hil_cpu.py", "--worst", "--usb", str(rate), "--engine", e]))
    return plan


class LockedOut:
    """the MIDI output shared by hil_cpu and the switch thread: one send at a time"""

    def __init__(self, out):
        self._out, self._lock = out, threading.Lock()

    def send(self, msg):
        with self._lock:
            self._out.send(msg)

    def __getattr__(self, k):
        return getattr(self._out, k)


class Tee(io.TextIOBase):
    def __init__(self, *outs):
        self.outs = outs

    def write(self, s):
        for o in self.outs:
            o.write(s)
        return len(s)

    def flush(self):
        for o in self.outs:
            o.flush()


def parse_run(text):
    """(whole ISR worst us or None, voices shed in total, hil_cpu's verdict line ok / FAIL) from a hil_cpu run's
    output"""
    m = re.search(r"whole ISR worst: (\d+) us", text)
    shed = sum(int(x) for x in re.findall(r"voices shed (\d+)", text))
    ok = "hil_cpu: ok" in text
    return (int(m.group(1)) if m else None), shed, ok


def run_cpu_plan(plan, outdir):
    import hil_cpu
    if not hasattr(hil_cpu, "run_cpu") or "--usb RATE" not in (hil_cpu.__doc__ or ""):
        sys.exit("tools/hil_cpu.py here has no run_cpu / --usb RATE: merge release-0.9.5 into this branch first")
    mido, out, inp = hil_cpu.ports()
    lo = LockedOut(out)
    hil_cpu._PORTS[1] = lo
    cc0, dry0 = dict(hil_cpu.CC), dict(hil_cpu.DRY)
    os.makedirs(outdir, exist_ok=True)
    rows = []
    try:
        for k, p in enumerate(plan):
            hil_cpu.CC = dict(cc0, DRV_SW=84, DRV_MIX=83)
            d = {"DRV_SW": p["cc84"]}                  # first: the type is chosen while AMOUNT is still 0
            d.update(dry0)
            if p["variant"] == "mix":
                d["DRV_MIX"] = MIX_LOW
            hil_cpu.DRY = d
            stop = threading.Event()
            th = None
            if p["variant"] == "switch":
                def toggle(a=p["cc84"], b=p["other_cc84"]):
                    i = 0
                    while not stop.wait(SWITCH_S):
                        i += 1
                        lo.send(mido.Message("control_change", control=84, value=b if i % 2 else a))
                th = threading.Thread(target=toggle, daemon=True)
            name = f"{p['engine']}_{p['type']}_{p['variant']}_{p['run']}"
            print(f"\n=== [{k + 1}/{len(plan)}] {name}: {' '.join(p['argv'][1:])}, CC 84 {p['cc84']}"
                  + (f" <-> {p['other']} ({p['other_cc84']}) every {SWITCH_S} s" if th else "")
                  + (f", MIX {MIX_LOW}" if p["variant"] == "mix" else ""), flush=True)
            buf = io.StringIO()
            argv0 = sys.argv
            res = {}
            try:
                if th:
                    th.start()
                with contextlib.redirect_stdout(Tee(sys.stdout, buf)):
                    res = hil_cpu.run_cpu(p["argv"])
            finally:
                stop.set()
                if th:
                    th.join()
                sys.argv = argv0
            text = buf.getvalue()
            with open(os.path.join(outdir, name + ".log"), "w") as f:
                f.write(text)
            whole, shed, ok = parse_run(text)
            worst = res.get("worst")
            bad = (not ok or res.get("code") or whole is None or whole > LIMIT_US or (worst or 0) > LIMIT_US
                   or shed > 0)
            rows.append(dict(p, argv=" ".join(p["argv"]), whole_us=whole, app_us=worst, shed=shed,
                             hil_cpu_ok=ok, verdict="FAIL" if bad else "ok"))
    finally:
        hil_cpu.CC, hil_cpu.DRY = cc0, dry0
        hil_cpu._PORTS[1] = out
        hil_cpu.close_usb()
    return rows


def cpu_summary(rows):
    """markdown: per engine / type / variant the whole ISR's runs, the worst, the margin to 4500 us, the change
    against FDRV's same variant, sheds, the verdict"""
    keys = []
    for r in rows:
        k = (r["engine"], r["type"], r["variant"])
        if k not in keys:
            keys.append(k)
    worst = {}
    for k in keys:
        ws = [r["whole_us"] for r in rows if (r["engine"], r["type"], r["variant"]) == k and r["whole_us"]]
        worst[k] = max(ws) if ws else None
    L = [f"Whole-ISR worst per run (us; limit {LIMIT_US}); app = hil_cpu's worst block (rh_render + fx_process)", "",
         "| engine | type | variant | whole ISR per run | worst | margin | vs FDRV | app worst | shed | verdict |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k in keys:
        rs = [r for r in rows if (r["engine"], r["type"], r["variant"]) == k]
        w = worst[k]
        ref = worst.get((k[0], "FDRV", k[2])) or worst.get((k[0], "FDRV", "base"))
        dv = f"{w - ref:+d}" if w is not None and ref is not None and k[1] != "FDRV" else "-"
        L.append(f"| {k[0]} | {k[1]} | {k[2]}" + (f" ({rs[0]['other']})" if rs[0]["other"] else "")
                 + f" | {', '.join(str(r['whole_us']) for r in rs)} | {w} | "
                 + (f"{LIMIT_US - w}" if w is not None else "-") + f" | {dv} | "
                 + f"{max((r['app_us'] or 0) for r in rs)} | {sum(r['shed'] for r in rs)} | "
                 + ("FAIL" if any(r["verdict"] != "ok" for r in rs) else "ok") + " |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- the jack plan (the p1x1 grid)
GRID = (("dlow_tnoon", 1, 64), ("dnoon_tnoon", 64, 64), ("dmax_tnoon", 127, 64), ("dmax_tmin", 127, 0),
        ("dmax_tmax", 127, 127))
GRID_KHLON = (("gmin_tnoon", 1, 64), ("glow_tnoon", 32, 64), ("gnoon_tnoon", 64, 64), ("gmax_tnoon", 127, 64),
              ("gmax_tmin", 127, 0), ("gmax_tmax", 127, 127))


def _note(ev, t, n, v, dur):
    ev.append((t / 1000, "on", n, v))
    ev.append(((t + dur) / 1000, "off", n, 0))


def _chord(ev, t, keys, v, dur):
    for k in keys:
        _note(ev, t, k, v, dur)


def phrase_riff():
    """an A7 / D9 shuffle with a right-hand lick (ST77), velocity 70..110"""
    ev, t = [], 300
    for _ in range(2):
        for root, keys in ((45, [57, 61, 64, 67]), (50, [54, 60, 64, 66])):
            _chord(ev, t, keys, 100, 420)
            _note(ev, t, root - 12, 95, 700)
            _chord(ev, t + 600, keys, 80, 180)
            _chord(ev, t + 900, keys, 105, 420)
            t += 1200
    for i, k in enumerate([69, 72, 74, 75, 76, 79, 76, 74, 72, 69]):
        _note(ev, t + i * 180, k, 90 + (i % 3) * 10, 170)
    t += 10 * 180 + 200
    _chord(ev, t, [45, 57, 61, 64, 67, 72], 110, 1800)
    return ev, (t + 3200) / 1000


def phrase_bass():
    """an E minor 9 chord over a walking bass E1 G1 A1 B1 .., then E1 alone"""
    ev, t = [], 300
    for i, b in enumerate([28, 31, 33, 35, 33, 31, 28, 33]):
        if i % 4 == 0:
            _chord(ev, t, [52, 55, 59, 62, 66], 95, 2300)
        _note(ev, t, b, 110, 560)
        t += 600
    _note(ev, t, 28, 120, 2500)
    _chord(ev, t, [52, 59, 62], 90, 2500)
    return ev, (t + 3500) / 1000


def phrase_vibes():
    """VIBES, motor off: a melody with two-mallet double stops, pedal down, then a chord"""
    ev, t = [(0.25, "cc", 64, 127)], 300
    for a, b in [(72, 76), (74, None), (76, 79), (79, None), (81, 84), (79, None), (76, 79), (74, None), (72, 76)]:
        _note(ev, t, a, 100, 300)
        if b:
            _note(ev, t, b, 90, 300)
        t += 330
    ev.append(((t + 200) / 1000, "cc", 64, 0))
    _chord(ev, t + 400, [60, 64, 67, 71, 74], 105, 1500)
    return ev, (t + 3600) / 1000


PHRASES = {"riff": phrase_riff, "bass": phrase_bass, "vibes": phrase_vibes}


def jack_plan(names, phrases):
    """[dict(type, cc84, setting, amount, tone, phrase, dur)]: per phrase the OFF reference, then every type's grid
    (MIX 127)"""
    by = {n: cc for n, i, cc in drive_types()}
    plan = []
    for ph in phrases:
        dur = PHRASES[ph]()[1]
        plan.append(dict(type="OFF", cc84=0, setting="ref", amount=0, tone=64, phrase=ph, dur=dur))
        for n in names:
            for tag, a, t in (GRID_KHLON if n == "KHLON" else GRID):
                plan.append(dict(type=n, cc84=by[n], setting=tag, amount=a, tone=t, phrase=ph, dur=dur))
    return plan


def run_jack_plan(plan, dev, ch, outdir):
    import numpy as np
    import hil_jack as hj
    import wave
    J = hj.Jack(dev, ch)
    M = J.r.mido.Message
    os.makedirs(outdir, exist_ok=True)

    def cc(c, v):
        J.r.out.send(M("control_change", control=c, value=int(v)))

    def wr(path, x):
        y = np.clip(np.round(np.asarray(x, float) * 32767), -32767, 32767).astype("<i2")
        w = wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hj.FS)
        w.writeframes(y.tobytes())
        w.close()

    rows, ref = [], {}
    for k, p in enumerate(plan):
        J.r.dry()
        if p["phrase"] == "vibes":
            cc(79, 64)                                 # VIBES, the motor off (it starts with the TYPE change)
            cc(47, 0)
        cc(84, p["cc84"])                              # the type first, AMOUNT still 0 (no switch fade)
        cc(82, p["tone"])
        cc(83, 127)
        cc(81, p["amount"])
        time.sleep(0.4)
        ev, dur = PHRASES[p["phrase"]]()
        u, j = J.both(dur + 0.5, ev)
        cc(64, 0)
        name = f"{p['type']}_{p['phrase']}_{p['setting']}"
        wr(os.path.join(outdir, name + "_usb.wav"), u)
        wr(os.path.join(outdir, name + "_jack.wav"), j)   # as the interface reads it: the takes compare by ear
        lu, lj = hj.lufs48(u), hj.lufs48(j)
        if p["type"] == "OFF":
            ref[p["phrase"]] = (lu, lj)
        r0 = ref.get(p["phrase"], (lu, lj))
        rows.append(dict(p, usb_lufs=lu, jack_lufs=lj, usb_re_off=lu - r0[0], jack_re_off=lj - r0[1],
                         usb_peak_db=float(20 * np.log10(np.abs(u).max() + 1e-12)),
                         jack_peak_db=float(20 * np.log10(np.abs(j).max() + 1e-12))))
        print(f"[{k + 1}/{len(plan)}] {name:34s} USB {lu:6.1f} LUFS ({lu - r0[0]:+5.1f} re OFF)  jack "
              f"{lj:6.1f} ({lj - r0[1]:+5.1f} re OFF)  USB peak {rows[-1]['usb_peak_db']:5.1f} dBFS", flush=True)
    J.r.dry()
    return rows


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--jack", action="store_true")
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--types", default=None, help="default: every type (FDRV first, the reference)")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--usb", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--phrases", default=",".join(PHRASES))
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--wait-lock", type=float, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and its time; no device, no lock")
    a = ap.parse_args(argv)
    if not (a.cpu or a.jack):
        ap.error("--cpu and / or --jack")
    known = [n for n, _, _ in drive_types()]
    names = a.types.split(",") if a.types else known
    bad = [n for n in names if n not in known]
    if bad:
        ap.error(f"--types: unknown {bad} (params.c N_DRV: {known})")
    engines, variants = a.engines.split(","), a.variants.split(",")
    if any(e not in ENGINES for e in engines) or any(v not in VARIANTS for v in variants):
        ap.error(f"--engines from {ENGINES}, --variants from {VARIANTS}")
    phrases = a.phrases.split(",")
    if any(p not in PHRASES for p in phrases):
        ap.error(f"--phrases from {tuple(PHRASES)}")
    cp = cpu_plan(engines, names, variants, a.runs, a.usb) if a.cpu else []
    jp = jack_plan(names, phrases) if a.jack else []
    t_cpu = len(cp) * 40                               # ~40 s a hil_cpu --worst run (VIBES / MARIMBA longer)
    t_jack = sum(p["dur"] + 2.5 for p in jp)
    lock = DeviceLock(main_repo(), "hil_drive", a.wait_lock)
    if a.dry_run:
        for p in cp:
            print(f"cpu  {p['engine']:8s} {p['type']:6s} CC84 {p['cc84']:3d} {p['variant']:6s} run {p['run']}"
                  + (f"  <-> {p['other']} CC84 {p['other_cc84']}" if p["other"] else "")
                  + f"  {' '.join(p['argv'])}")
        for p in jp:
            print(f"jack {p['type']:6s} CC84 {p['cc84']:3d} {p['phrase']:6s} {p['setting']:12s} AMOUNT {p['amount']:3d} "
                  f"TONE {p['tone']:3d} MIX 127  {p['dur']:.1f} s")
        print(f"{len(cp)} hil_cpu runs (~{t_cpu / 60:.0f} min), {len(jp)} jack takes (~{t_jack / 60:.0f} min); "
              f"the device lock: {lock.dir}")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    hd = os.path.join(ROOT, "build", "hil", "drive")
    code = 0
    with lock:
        if cp:
            rows = run_cpu_plan(cp, os.path.join(hd, f"cpu-{stamp}"))
            md = cpu_summary(rows)
            json.dump(rows, open(os.path.join(hd, f"cpu-{stamp}.json"), "w"), indent=1)
            open(os.path.join(hd, f"cpu-{stamp}.md"), "w").write(md)
            print("\n" + md)
            code |= int(any(r["verdict"] != "ok" for r in rows))
            print(f"hil_drive --cpu: {'FAIL' if code else 'ok'} ({hd}/cpu-{stamp}.md)")
        if jp:
            d = os.path.join(hd, f"jack-{stamp}")
            rows = run_jack_plan(jp, a.dev, a.ch, d)
            json.dump(rows, open(os.path.join(d, "levels.json"), "w"), indent=1)
            print(f"hil_drive --jack: {len(rows)} takes in {d} (levels.json); the user listens on the jack files")
    return code


if __name__ == "__main__":
    sys.exit(main())
