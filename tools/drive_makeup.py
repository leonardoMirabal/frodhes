#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The pickup drive's level make-up (rhodes.c rh_drv_mk, firmware/gen/rh_drive_mk.h RH_DRV_MK), on the host.

    tools/drive_makeup.py --write    render every key at every velocity anchor with the make-up off (a host build with
                                     -DRH_NO_DRV_MK) and on the reference release (--ref, default v0.9.9: the level
                                     before the drive), write firmware/gen/rh_drive_mk.h
    tools/drive_makeup.py            the residual: the engine with the make-up (a fresh host build of this tree) re the
                                     reference, per key 28 .. 100 x velocity, dB (RMS 50 .. 500 ms)

The drive (S1a) makes the swing through the pickup follow the velocity, which is its timbre; it also took the mf level
about 6 dB down and the top keys 6 dB down at v127. The make-up is a gain on the voice's output only (the swing, the
pickup's curve and every timbre term stay the drive's): per key and velocity anchor, ref RMS / drive RMS (one note in
its own run, effects bypassed by CC 119, NOISE 0, 50 .. 500 ms after the note-on), or the gain that matches the
critical-band loudness (tools/cb_loud.py) where that is larger: the darker treble at the same RMS sounds quieter. The drive's hard
low notes are peakier at the same RMS (a v127 bass chord peaks ~1-2 dB over the reference's): the headroom is the
output level's (set for the worst case), not this table's. Anchors at v48 and above match the
reference; below, the larger of the two levels (the make-up never under 1), so the curve has no step. The pickup output
is linear in the voice gain: one pass. Physics-only: nothing here reads recordings."""
import argparse
import os
import subprocess
import sys
import wave
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cb_loud  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "drive_makeup")
HDR = os.path.join(ROOT, "firmware", "gen", "rh_drive_mk.h")
FS = 44100
KEY_LO, NKEY = 21, 88
ANCH = tuple(range(40, 128, 8)) + (127,)                   # rendered: v40 (and under it), 48 .. 120, 127 (for 128, extended)
TYPES = (0, 1, 2, 3)                                       # CC 80 = TYPE x 32: each TYPE's drive row has its own law
MATCH_V = 48                                               # anchors >= this match the reference; below: never under 1
CFLAGS = ["-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-Ifirmware/src",
          "-Ifirmware/gen"]


def build(name, src, extra=()):
    b = os.path.join(OUT, name)
    os.makedirs(OUT, exist_ok=True)
    subprocess.run(["cc", *CFLAGS, *extra, "-o", b, "firmware/src/frodhes.c", "-lm"], cwd=src, check=True)
    return b


def build_ref(rev):
    src = os.path.join(OUT, "ref-src")
    subprocess.run("rm -rf %s && mkdir -p %s && git archive %s firmware tests/host.c | tar -x -C %s"
                   % (src, src, rev, src), shell=True, cwd=ROOT, check=True)
    return build("ref", src)


def run1(binary, ty, n, vel, raw):
    d = os.path.join(OUT, "r", os.path.basename(binary))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ty}-{n}-{vel}-{raw}")
    open(path + ".txt", "w").write("\n".join((["20 midi 0xB0 119 127"] if raw else []) +
                                             ["30 midi 0xB0 104 0", f"40 midi 0xB0 80 {ty * 32}",
                                              f"200 midi 0x90 {n} {vel}", f"800 midi 0x80 {n} 0", "1200 end"]) + "\n")
    subprocess.run(["nice", "-n", "10", binary, path, path + ".txt"], check=True, capture_output=True)
    w = wave.open(path + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    for f in os.listdir(d):
        if f.startswith(f"{ty}-{n}-{vel}-{raw}-") and f.endswith(".ppm"):
            os.remove(os.path.join(d, f))
    for e in (".wav", ".txt"):
        os.remove(path + e)
    return x


def render(job):
    """one note in its own run, the note 600 ms: RMS (left) 50 .. 500 ms after the note-on (the first sample), the
    peak (both channels, to 1 s), and the peak with the default effects (CC 119 off)"""
    binary, ty, n, vel = job
    x = run1(binary, ty, n, vel, 1)
    seg = x[int(0.2 * FS):int(0.8 * FS), 0]
    i = int(np.argmax(np.abs(seg) > 0))
    pk = float(np.abs(x[int(0.2 * FS):]).max() + 1e-9)
    seg = seg[i:][int(0.05 * FS):int(0.5 * FS)]
    pf = float(np.abs(run1(binary, ty, n, vel, 0)[int(0.2 * FS):]).max() + 1e-9)
    return (float(np.sqrt(np.mean(seg * seg)) + 1e-9), pk, pf, *cb_loud.bands(seg / 32768.0))


def grid(binary, keys, vels, jobs, types=TYPES):
    js = [(binary, t, n, v) for t in types for n in keys for v in vels]
    with ProcessPoolExecutor(jobs) as ex:
        r = list(ex.map(render, js, chunksize=8))
    return np.array(r).reshape(len(types), len(keys), len(vels), 3 + len(cb_loud.EDGES) - 1)


def write(code):
    tys = ",\n".join("  {\n" + ",\n".join("    {" + ",".join("%d" % v for v in row) + "}" for row in t) + "\n  }"
                     for t in code)
    open(HDR, "w").write(
        "/* SPDX-License-Identifier: GPL-3.0-only */\n"
        "/* generated by tools/drive_makeup.py --write: the pickup drive's level make-up (rhodes.c rh_drv_mk) per TYPE x\n"
        " * key x velocity anchor (rhodes.c RH_MK_N: v40, 48 .. 120, 128; rendered at v%s, 128 extended from 120 .. 127): the reference\n"
        " * release's RMS (50 .. 500 ms) over the drive's, matched at v%d and above, never under 1 below; coded in 1/32\n"
        " * octave (128 = 1; rhodes.c rh_mk_q12). A gain on the voice's output only */\n"
        "#define RH_DRV_MK_Q 4096\n"
        "static const uint8_t RH_DRV_MK[4][RH_NKEY][RH_MK_N] = {\n%s\n};\n"
        % (" ".join(str(a) for a in ANCH), MATCH_V, tys))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ref", default="v0.9.9")
    ap.add_argument("-j", type=int, default=4)
    a = ap.parse_args()
    keys = list(range(KEY_LO, KEY_LO + NKEY))
    ref = build_ref(a.ref)
    if a.write:
        nomk = build("nomk", ROOT, ["-DRH_NO_DRV_MK", "-DRH_NO_DYN"])   # (the dynamics stage after it)
        R, D = grid(ref, keys, ANCH, a.j), grid(nomk, keys, ANCH, a.j)
        c = 32 * np.log2(R[..., 0] / D[..., 0])                      # 1/32 octave: the RMS's ratio
        for idx in np.ndindex(c.shape):                              # ... or the critical-band loudness's, the
            if ANCH[idx[2]] >= MATCH_V:                              # larger (never quieter by either: the
                g = cb_loud.match_db(D[idx][3:], cb_loud.loud(R[idx][3:]))   # treble's brightness)
                c[idx] = max(c[idx], 32 * g / 6.0206)
        for j, v in enumerate(ANCH):
            if v < MATCH_V:
                c[:, :, j] = np.maximum(c[:, :, j], 0.0)
        c[:, :, -1] = c[:, :, -2] + (c[:, :, -1] - c[:, :, -2]) * 8 / 7     # v128: the line through 120, 127
        code = np.clip(np.round(c + 128), 0, 255).astype(int)
        write(code)
        print("wrote %s: make-up %.1f .. %.1f dB" % (HDR, (code.min() - 128) * 6.0206 / 32, (code.max() - 128) * 6.0206 / 32))
        return
    cur = build("cur", ROOT)
    ks, vs = (28, 40, 52, 64, 76, 88, 100), (32, 48, 64, 80, 96, 112, 127)
    R, C = grid(ref, ks, vs, a.j), grid(cur, ks, vs, a.j)
    for t in TYPES:
        print("TYPE %d: dB re %s   " % (t, a.ref) + " ".join("v%-5d" % v for v in vs))
        for i, k in enumerate(ks):
            print("key %3d       " % k + " ".join("%+5.1f " % (20 * np.log10(C[t, i, j, 0] / R[t, i, j, 0]))
                                                for j in range(len(vs))))


if __name__ == "__main__":
    sys.exit(main())
