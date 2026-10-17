#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Pitch and decay of every key's partials, measured on the C engine (host build) against the generated spec: the
SCAN's S6 (PLAN §9.2) on its own, like tools/filter_scan.py for the filter.

Per key (F3..F6 by default) at v100, the default cards (MALLET 5.0 = the middle family), RAW, NOISE 0, the key held
for the whole render (the damper off, as with the pedal down): each designed mode's frequency (inst_f over
0.3..2.3 s, cents re the spec), its T60 (a line through its band envelope, 0.3..8 s), the level (LUFS 0..1 s),
t_90 (the fundamental's band reaching 90 % of its peak: the tube's build-up on a vibraphone) and, with --motor, the
full-band AM depth and rate 1.5..2.5 s into the note.

  tools/bar_freq_scan.py                      TYPE VIBES (4), keys 53..89
  tools/bar_freq_scan.py --type 0 --keys 40-90/5      a Rhodes TYPE (plumbing; the spec is rhodes_spec.json)
  tools/bar_freq_scan.py --motor --rate 78 --depth 127
  -> build/bar_freq_scan/report.txt, scan.csv, curves.png

Spec: docs/model/data/bars_spec.json for the bar types (tools/gen_bars.py), rhodes_spec.json for the Rhodes ones;
{"keys": [{"midi", "modes": [{"name", "f", "t60"}]}]}. A mode is judged only where it is measurable (its band
within 60 dB of the fundamental's). Pass (host): frequency within +-1 cent (TUNE 0) and T60 within +-10 %. Modes
whose spec T60 is longer than the render can show are reported, not judged. The upper modes' sleep is off
(FRODHES_NOSLEEP; --sleep keeps it): this is the resonators against their spec, the sleep is S12's. The TUBE is
judged by the fundamental's build-up (the f0-band peak time, the reference procedure, vs the design's within +-15 ms
or +-30 %), not as a line: it is a normal mode of the V1 pair within ~5 c of V1 that dies before 0.3 s.
--hil (the FM-1 over USB) is P7's.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402

OUT = os.path.join(bm.ROOT, "build", "bar_freq_scan")


def parse_keys(s):
    out = []
    for part in s.split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/")
            step = int(st)
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1, step))
        else:
            out.append(int(part))
    return out


def measure_key(n, a, spec, spec_design=None):
    ev = bm.setup_events(typ=a.type, raw=not a.motor, noise=0)
    if a.motor:                                     # the motor (records v3: CC 47 ON, 48 DEPTH, 49 RATE, 50 SHAPE)
        ev += [(21, "midi 0xB0 47 127"), (21, f"midi 0xB0 48 {a.depth}"), (21, f"midi 0xB0 49 {a.rate}"),
               (21, "midi 0xB0 50 0")]
        for k in ("DRIVE", "REV"):
            ev.append((20, f"midi 0xB0 {bm.CC[k]} 0"))
    ev += bm.note(100, n, a.vel, a.dur - 200)
    # the resonators against their spec: the upper modes' sleep off (FRODHES_NOSLEEP). With it a mode 46 dB under
    # V1 (V4 at key 77) sleeps 54 dB under within 30 ms and the fit read the floor (-42 c, or a T60 cut short); the
    # sleep's own transparency is S12's check (bar_scan.py)
    x, log = bm.render(f"k{n}", ev, a.dur, os.path.join(OUT, "wav"), env=None if getattr(a, "sleep", False) else {"FRODHES_NOSLEEP": "1"})
    x = x[int(0.1 * bm.FS):]                         # from the strike (the note-on lands in the next block)
    first = int(np.argmax(np.abs(bm.mono(x)) > 0))
    x = x[first:]
    row = dict(key=n, lufs=bm.lufs(x[:bm.FS]), peak_dbfs=20 * np.log10(np.abs(x).max() / 32767 + 1e-12))
    modes = (spec or {}).get(n, {}).get("modes") or [dict(name="V1", f=440 * 2 ** ((n - 69) / 12), t60=None)]
    pad = np.zeros((1024, x.shape[1]))              # t_90 at ~1.5 ms resolution (frame centres from t = 0)
    t, env1 = bm.band_env(np.vstack([pad, x[:2 * bm.FS]]), modes[0]["f"], rel=0.03, n=2048, hop=64)
    row["t90"] = bm.t_frac(t - 1024 / bm.FS, env1)
    lv1 = None
    row["modes"] = []
    design = {d["midi"]: d for d in (spec_design or [])}.get(n)
    for i, m in enumerate(modes):
        f = float(m["f"])
        if f <= 1 or f >= bm.FS / 2 * 0.95:
            continue
        if i > 1 and (not m.get("b") or (m.get("name") in ("T1", "T2") and not getattr(a, "edge", False))):
            # a mode this strike does not excite is not judged: pruned (b = 0, never lit), or the torsion T1 / T2,
            # which bars.c lights only off the centreline (TONE > 64; here the default 64): their bands then read a
            # neighbour (T2 at 10..12.5 f0 shares V3's band on the low keys: V3's T60, its cents)
            continue
        if m.get("name") == "TUBE":
            # The tube is not a line of its own: V1 and the tube are one coupled pair whose tube-like normal mode lies
            # within ~5 c of V1 and dies in its T60 (0.02..0.5 s, before the 0.3 s measuring window opens), so a
            # narrow band at f_tube reads V1 (its T60, -5 c). Its trace is the fundamental's build-up: the f0-band
            # peak time, read with the reference recordings' procedure (bartube.f0_peak_ms), against the design's
            # (Gate VA's tolerance: +-15 ms or +-30 %, the larger)
            if design is None:
                continue
            import bartube as btm
            pk = btm.f0_peak_ms(bm.mono(x), modes[0]["f"])
            row["modes"].append(dict(name="TUBE", f_spec=f, f=None, cents=None, t60_spec=None, t60=None, level_db=0.0,
                                     measurable=True, long=False, ctol=1.0, peak_ms=pk, peak_spec=design["peak_ms"]))
            continue
        st = m.get("t60")                           # a short mode is measured early, over about its own decay
        w0 = 0.3 if not st or st >= 3 else max(0.03, 0.05 * st)
        w1 = min(2.3, w0 + max(0.25, 0.7 * st)) if st and st < 3 else 2.3
        r = bm.inst_f(x, f, w0, w1)
        tt, env = bm.band_env(x, f, n=8192 if w0 >= 0.3 else 2048, hop=1024 if w0 >= 0.3 else 256)
        sel = (tt > w0) & (tt < max(w0 + 0.2, min(1.0, w1)))
        lvl = float(env[sel].max()) if sel.any() else -240
        if i == 0:
            lv1 = lvl
        meas = lvl > (lv1 if lv1 is not None else lvl) - 60 and lvl > -110 and r is not None
        t60 = bm.decay_fit(tt, env, t0=w0, t1=(a.dur - 300) / 1000) if meas else None
        st60 = m.get("t60")
        long_ = st60 is not None and t60 is not None and st60 > 3 * (a.dur / 1000)
        c = bm.cents(r[0], f) if meas else None
        row["modes"].append(dict(name=m.get("name", f"M{i}"), f_spec=f, f=r[0] if meas else None, cents=c,
                                 t60_spec=st60, t60=t60, level_db=lvl, measurable=bool(meas), long=bool(long_),
                                 ctol=max(1.0, float(bm.cents(f + 0.5 / (w1 - w0), f)))))   # +-1 c, or what the window resolves
    if a.motor:
        row["am_db"], row["am_hz"] = bm.am_depth(x, 1.5, 2.5)
    return row


def judge(row, has_spec):
    bad = []
    for m in row["modes"]:
        if not m["measurable"] or not has_spec:
            continue
        if "peak_ms" in m:                          # the tube: the fundamental's build-up time
            if abs(m["peak_ms"] - m["peak_spec"]) > max(15.0, 0.3 * m["peak_spec"]):
                bad.append(f"TUBE f0 peak {m['peak_ms']:.0f} ms vs {m['peak_spec']:.0f}")
            continue
        if abs(m["cents"]) > m["ctol"]:
            bad.append(f"{m['name']} {m['cents']:+.2f} c")
        if m["t60_spec"] and m["t60"] and not m["long"] and abs(m["t60"] / m["t60_spec"] - 1) > 0.10:
            bad.append(f"{m['name']} T60 {m['t60']:.2f} s vs {m['t60_spec']:.2f}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", type=int, default=4, help="TYPE (4 = VIBES; 0..3 the Rhodes ones)")
    ap.add_argument("--keys", default="53-89")
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--dur", type=int, default=8200, help="render length, ms")
    ap.add_argument("--motor", action="store_true")
    ap.add_argument("--rate", type=int, default=78)
    ap.add_argument("--depth", type=int, default=127)
    ap.add_argument("--spec", help="override the spec file")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--hil", action="store_true", help="the FM-1 instead of the host (P7; not yet)")
    ap.add_argument("--sleep", action="store_true", help="keep the upper modes' sleep on (default: off, the resonators "
                                                         "against their spec)")
    a = ap.parse_args()
    if a.hil:
        sys.exit("--hil: the device run comes with P7 (tools/hil_scan.py vibes_*)")
    os.makedirs(OUT, exist_ok=True)
    bm.build()
    spath, bars = bm.spec_for_type(a.type)
    spec = bm.load_spec(a.spec or spath)
    design = bm.load_design(a.spec or spath)
    keys = parse_keys(a.keys)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(lambda n: measure_key(n, a, spec, design), keys))
    name = bm.type_name(a.type)
    lines = [f"bar_freq_scan: TYPE {a.type} {name}, v{a.vel}, keys {keys[0]}..{keys[-1]} ({len(keys)}), "
             f"spec {os.path.relpath(a.spec or spath, bm.ROOT) if spec else 'MISSING: frequencies re equal temperament, no T60 judged'}"
             + (f", MOTOR rate {a.rate} depth {a.depth}" if a.motor else ""),
             "pass: |cents| <= 1.0 (or what a short mode's window resolves), T60 within 10 % (modes whose band is within 60 dB of V1; very long T60s reported only)",
             ""]
    lines.append(f"{'key':>4} {'LUFS':>6} {'t90 ms':>6}  modes: name cents / T60 meas (spec)" +
                 ("   AM dB / Hz" if a.motor else ""))
    csv = ["key,mode,f_spec,f,cents,t60_spec,t60,level_db,measurable,lufs,t90"]
    nbad = 0
    for r in rows:
        bad = judge(r, spec is not None and not a.motor)   # (the motor modulates the decays: reported only)
        nbad += bool(bad)
        ms = []
        for m in r["modes"]:
            csv.append(f"{r['key']},{m['name']},{m['f_spec']:.3f},{m['f'] or ''},{'' if m['cents'] is None else round(m['cents'], 3)},"
                       f"{m['t60_spec'] or ''},{'' if m['t60'] is None else round(m['t60'], 3)},{m['level_db']:.1f},"
                       f"{int(m['measurable'])},{r['lufs']:.2f},{r['t90'] or ''}")
            if "peak_ms" in m:
                ms.append(f"TUBE f0 peak {m['peak_ms']:.0f}ms({m['peak_spec']:.0f})")
            elif m["measurable"]:
                ms.append(f"{m['name']} {m['cents']:+.2f}c/{m['t60'] if m['t60'] is not None else float('nan'):.2f}s"
                          + (f"({m['t60_spec']:.2f})" if m["t60_spec"] else "") + ("L" if m["long"] else ""))
        am = f"   {r['am_db']:.1f} / {r['am_hz']:.2f}" if a.motor and r.get("am_db") is not None else ""
        lines.append(f"{r['key']:4} {r['lufs']:6.1f} {1000 * (r['t90'] or 0):6.0f}  " + "  ".join(ms) + am +
                     ("   <-- " + "; ".join(bad) if bad else ""))
    lv = [r["lufs"] for r in rows]
    steps = np.abs(np.diff(lv)) if len(lv) > 1 else np.zeros(1)
    lines.append("")
    lines.append(f"{len(rows)} keys, {nbad} out of tolerance; LUFS {min(lv):.1f}..{max(lv):.1f}, largest neighbour step "
                 f"{steps.max():.2f} dB")
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    open(os.path.join(OUT, "scan.csv"), "w").write("\n".join(csv) + "\n")
    names = []
    for r in rows:
        for m in r["modes"]:
            if m["name"] not in names:
                names.append(m["name"])
    cols = [(200, 110, 20), (40, 90, 200), (30, 150, 70), (170, 40, 120), (90, 90, 90), (20, 160, 170), (200, 60, 40), (120, 80, 200)]
    def series(field, f=lambda v: v):
        out = []
        for i, nm in enumerate(names):
            X, Y = [], []
            for r in rows:
                for m in r["modes"]:
                    if m["name"] == nm and m["measurable"] and m[field] is not None:
                        X.append(r["key"]); Y.append(f(m[field]))
            if X:
                out.append((nm, cols[i % len(cols)], X, Y))
        return out
    ratio = []
    for i, nm in enumerate(names):
        X, Y = [], []
        for r in rows:
            for m in r["modes"]:
                if m["name"] == nm and m["measurable"] and m["t60"] and m["t60_spec"] and np.isfinite(m["t60"]):
                    X.append(r["key"]); Y.append(m["t60"] / m["t60_spec"])
        if X:
            ratio.append((nm, cols[i % len(cols)], X, Y))
    bm.plot_panels(os.path.join(OUT, "curves.png"), [
        ("pitch re spec (cents; +-1 passes)", series("cents"), "key", "cents", (-3, 3)),
        ("T60 measured / spec (0.9..1.1 passes)", ratio, "key", "ratio", (0.5, 1.5)),
        ("level, LUFS 0..1 s", [("LUFS", (200, 110, 20), [r["key"] for r in rows], lv)], "key", "LUFS", None),
        ("t_90 of the fundamental (ms)", [("t90", (40, 90, 200), [r["key"] for r in rows],
                                           [1000 * (r["t90"] or 0) for r in rows])], "key", "ms", None),
    ])
    print(lines[-1])
    print("->", os.path.relpath(OUT, bm.ROOT) + "/report.txt, scan.csv, curves.png")
    return 1 if nbad else 0


if __name__ == "__main__":
    sys.exit(main())
