#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Null test of the Rhodes voice tables: the C engine on the host with an old firmware/gen/rhodes_tables.h (from a
git revision) against the working tree's, every key struck alone, the effects bypassed (CC 119 RAW, NOISE 0).

  tools/rh_nulltest.py --base 8d913af|FILE [--new FILE] [--vel 100,127] [--dur 3.0] [--keys 21-108]

Per key: the residual rms(new - old) over rms(old) in dB over the whole note (strike, sustain, release), and the
level change. Prints a table and the worst key per range; --json writes the numbers. The only thing that differs
between the two binaries is the header, so the null measures exactly the table change.
"""
import argparse
import json
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FS = 44100
CFLAGS = ["-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DAPP_VER=\"dev\""]


def build(tag, header):
    d = os.path.join(ROOT, "build", "nulltest", tag)
    os.makedirs(os.path.join(d, "gen"), exist_ok=True)
    hp = os.path.join(d, "gen", "rhodes_tables.h")
    open(hp, "w").write(header)
    gen = os.path.join(ROOT, "firmware", "gen")
    for f in os.listdir(gen):                    # the other generated headers as they are in the working tree
        if f != "rhodes_tables.h":
            src = os.path.join(gen, f)
            dst = os.path.join(d, "gen", f)
            if not os.path.exists(dst) or open(dst, "rb").read() != open(src, "rb").read():
                open(dst, "wb").write(open(src, "rb").read())
    b = os.path.join(d, "frodhes")
    subprocess.run([os.environ.get("CC", "cc")] + CFLAGS + ["-Ifirmware/src", "-I" + os.path.join(d, "gen"),
                    "-o", b, "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)
    return b


def render(binary, keys, vels, dur, hold):
    out = os.path.join(os.path.dirname(binary), "keys")
    lines = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0"]
    t, starts = 200, []
    for v in vels:
        for n in keys:
            starts.append((n, v, t))
            lines += [f"{t} midi 0x90 {n} {v}", f"{t + int(hold * 1000)} midi 0x80 {n} 0"]
            t += int(dur * 1000)
        t += 8000                                # silence between the velocity passes (the top key's tail)
    lines.append(f"{t + 200} end")
    open(out + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([binary, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
    return x, starts


def db(a, b):
    return 20 * np.log10(max(a, 1e-12) / max(b, 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="8d913af", help="git revision of the old rhodes_tables.h (or a header file)")
    ap.add_argument("--new", help="the new header (default: the working tree's)")
    ap.add_argument("--vel", default="100")
    ap.add_argument("--dur", type=float, default=3.0, help="seconds per key (the key is held --hold s)")
    ap.add_argument("--hold", type=float, default=2.0)
    ap.add_argument("--keys", default="21-108")
    ap.add_argument("--json")
    a = ap.parse_args()
    lo, hi = (int(q) for q in a.keys.split("-"))
    keys = list(range(lo, hi + 1))
    vels = [int(v) for v in a.vel.split(",")]
    if os.path.isfile(a.base):
        old = open(a.base).read()
    else:
        old = subprocess.run(["git", "show", a.base + ":firmware/gen/rhodes_tables.h"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    new = open(a.new or os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    xo, st = render(build("old", old), keys, vels, a.dur, a.hold)
    xn, _ = render(build("new", new), keys, vels, a.dur, a.hold)
    res = []
    for n, v, t in st:
        i0, i1 = int(t * FS / 1000), int((t + a.dur * 1000 - 100) * FS / 1000)   # (the next key may start a block early)
        o, w = xo[i0:i1], xn[i0:i1]
        ro, rn, rd = (float(np.sqrt(np.mean(q ** 2))) for q in (o, w, w - o))
        res.append(dict(key=n, vel=v, null_db=round(db(rd, ro), 1) if rd else -999.0,
                        level_db=round(db(rn, ro), 2) if ro else 0.0, exact=bool(rd == 0)))
    for r in res:
        print("key %3d v%3d  null %7.1f dB  level %+6.2f dB%s" % (r["key"], r["vel"], r["null_db"], r["level_db"],
                                                                  "  (exact)" if r["exact"] else ""))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
