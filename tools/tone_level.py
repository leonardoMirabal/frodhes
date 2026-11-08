#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes TONE knob's loudness above its middle, on the host (the C engine, effects bypassed by CC 119, NOISE 0):
the curve rhodes.c rh_tone_gain takes it back by (TONE changes the colour, not the loudness), and the check.

    tools/tone_level.py --write     measure the loudness against the pickup offset per TYPE (soft v40, hard v127) with
                                    the compensation off (a host build with -DRH_NO_TONE_G, build/tone_level/), write
                                    firmware/gen/rh_tone_g.h (RH_TONE_A)
    tools/tone_level.py --write --map   the same with RESPONSE B (CC 40 127, the velocity map): its curves and its blend
                                    per TYPE, key anchor and velocity (fit_wb), firmware/gen/rh_tone_b.h
    tools/tone_level.py             the residual with the engine as built (build/host/frodhes): the loudness re TONE
                                    64 per TYPE at TONE 64, 68 .. 124, 127, the mean over C2 C3 A3 C5 C6 and the
                                    worst key, at v40 / v100 / v127

Above 64 the pickup offset moves toward the pole axis (params.c P_TONE): the fundamental's slope falls, the second
harmonic grows, and the note gets quieter (up to ~10 dB at 127). Every key has its own offset (the per-key jitter,
the TYPE's voicing, the cap RH_OFF_MAX), so the curve is the loudness against the offset itself: per key, TONE
32 .. 127 gives its loudness at the offsets it reaches (rhodes_tables.h: the key's offset, the TYPE's, RH_TONE_UP,
RH_OFF_MAX); one curve for the keys (C2 C3 A3 C5 C6) and a level per key, fitted by least squares, on a grid of
1/128 gap from -0.5625 to the cap. The pickup output is linear in the voice gain: the gain A(TONE 64's offset) /
A(the offset played) gives the loudness back. Physics-only: nothing here reads recordings."""
import argparse
import os
import re
import subprocess
import sys
import wave

import numpy as np
from scipy.signal import sosfilt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from bar_measure import k_weight_sos  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
NOG = os.path.join(ROOT, "build", "tone_level", "frodhes-nog")
HDR = os.path.join(ROOT, "firmware", "gen", "rh_tone_g.h")
FS = 44100
Q25 = 1 << 25
TYPES = ["STAGE77", "SUITCASE", "FELT", "DYNO"]          # CC 80 = index * 32
KEYS = (36, 48, 57, 72, 84)                                # the 1/32" pickup family (S8: family 1)
FKEYS = {1: KEYS, 0: (24, 28, 32, 96, 100)}               # per pickup family: its own curve (the 1/16" one: family 0,
                                                           # keys <= 32 and >= 96, its own flux table and half the
                                                           # offsets)
ANCHORS = tuple(range(64, 128, 4)) + (127,)
VELS = (40, 127)                                           # the two curves: soft, hard (rh_tone_gain blends them)
SH = 18                                                    # the grid step: 2^18 Q25 = 1/128 gap
O_LO = -72 << SH                                           # -0.5625 gap
SOS = k_weight_sos(FS)


def lufs(x):
    y = sosfilt(SOS, np.asarray(x, float) / 32768.0)
    return float(-0.691 + 10 * np.log10(np.mean(y * y) + 1e-20))


RESP = 0                                                   # RESPONSE (CC 40): 0 = A, the pickup drive (rh_tone_g.h);
                                                           # 127 = B, the velocity map (--map: rh_tone_b.h)


def render(out, ty, tone, n, vel=100, binary=BIN):
    """one note in its own run: RESPONSE, TYPE, TONE, NOISE 0 (no knock), the note 600 ms -> its 50..600 ms (left)"""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"t{ty}-{tone}-{n}-{vel}" + ("-b" if RESP else ""))
    script = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0", f"35 midi 0xB0 40 {RESP}", f"40 midi 0xB0 80 {ty * 32}",
              f"50 midi 0xB0 28 {tone}",
              f"200 midi 0x90 {n} {vel}", f"800 midi 0x80 {n} 0", "900 end"]
    open(path + ".txt", "w").write("\n".join(script) + "\n")
    if not (os.environ.get("TONE_LEVEL_REUSE") and os.path.exists(path + ".wav")):   # (a re-fit from the last renders)
        subprocess.run([binary, path, path + ".txt"], check=True, capture_output=True)
    w = wave.open(path + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
    seg = x[int(0.2 * FS):int(0.8 * FS)]
    seg = seg[int(np.argmax(np.abs(seg) > 0)):]
    return seg[int(0.05 * FS):int(0.6 * FS)]


def tables():
    """the key offsets, the TYPE offsets, RH_TONE_UP, RH_OFF_MAX, RH_KEY_LO, each key's pickup family and the
    families' offset shifts (RH_FAM_SH) from rhodes_tables.h"""
    t = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    i = t.index("RH_KEYS[RH_NKEY]")
    body = t[i:t.index("};", i)]
    offs = [int(m.group(1)) for m in re.finditer(r"\}\}, (-?\d+),", body)]
    fams = [int(m.group(1)) for m in
            re.finditer(r"\}\}, -?\d+, -?\d+, -?\d+, -?\d+, -?\d+, -?\d+, -?\d+, \d+, (\d+),", body)]
    assert len(fams) == len(offs)
    i = t.index("RH_TYPE[4]")
    tys = [int(m.group(1)) for m in re.finditer(r"\{(-?\d+),", t[i:t.index("};", i)])]
    num = lambda k: int(re.search(r"#define %s \(?(-?\d+)" % k, t).group(1))
    sh = [int(v) for v in re.search(r"RH_FAM_SH\[2\] = \{([^}]*)\}", t).group(1).split(",")]
    return offs, tys, num("RH_TONE_UP"), num("RH_OFF_MAX"), num("RH_KEY_LO"), fams, sh


def offset(key_off, ty_off, tone, up, omax, sh=0):
    """rhodes.c rh_init: the TYPE's and TONE's distances in the family's own gap (>> sh), the cap too"""
    o = key_off + ((ty_off + ((tone - 64) * (1 << 18) if tone <= 64 else (tone - 64) * up)) >> sh)
    return min(o, omax >> sh)


def build_nog():
    os.makedirs(os.path.dirname(NOG), exist_ok=True)
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-DRH_NO_TONE_G",
                    "-Ifirmware/src", "-Ifirmware/gen", "-o", NOG, "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)


def measure(out):
    """[family][type][vel] -> the Q15 curve on the grid (the loudest point 32767)"""
    build_nog()
    offs, tys, up, omax, lo, fams, shs = tables()
    n_grid = (max(omax, omax >> min(shs), omax >> max(shs)) - O_LO >> SH) + 1   # to the highest family cap (S8: the
                                                           # 1/16" one's, omax >> 1, nearer the axis: omax < 0)
    return [measure_fam(out, f, offs, tys, up, omax, lo, fams, shs, n_grid) for f in (0, 1)], n_grid, omax


def measure_fam(out, fam, offs, tys, up, omax, lo, fams, shs, n_grid):
    keys = FKEYS[fam]
    assert all(fams[n - lo] == fam for n in keys), (fam, keys)
    tones = list(range(32, 64, 2)) + list(range(64, 128))
    R = []
    for ty in range(4):
        row = []
        for vel in VELS:
            rows, rhs = [], []                                     # least squares: L(o) on the grid + a level per key
            for ki, n in enumerate(keys):
                for t in tones:
                    o = offset(offs[n - lo], tys[ty], t, up, omax, shs[fam])
                    x = min(max((o - O_LO) / (1 << SH), 0.0), n_grid - 1.0)
                    i = min(int(x), n_grid - 2)
                    r = np.zeros(n_grid + len(keys))
                    r[i], r[i + 1] = 1 - (x - i), x - i
                    if ki:
                        r[n_grid + ki] = 1.0                       # (key 0's level is the reference)
                    rows.append(r)
                    rhs.append(lufs(render(out, ty, t, n, vel, NOG)))
            for g in range(1, n_grid - 1):                         # a light smoothness (the grid points no key reaches)
                r = np.zeros(n_grid + len(keys))
                r[g - 1], r[g], r[g + 1] = 0.01, -0.02, 0.01
                rows.append(r)
                rhs.append(0.0)
            Lm = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0][:n_grid]
            A = 10 ** ((Lm - Lm.max()) / 20)
            row.append([int(round(32767 * a)) for a in A])
        R.append(row)
    return R


WKEY_STEP = 3                                              # the blend weight's fit: every 3rd key (interpolated)
WTONES = (64, 80, 96, 112, 127)
WQ = 32                                                    # RH_TONE_W: Q5, stored + WQ (-1 .. +6.97)
WGAIN_MAX_DB = 12.0                                        # the compensation's most gain at any TONE (the fit's bound)
WREG = 0.01                                                # LU^2 per unit of w^2 a TONE: the pull to the old law


def tone_a(A, o):
    """rhodes.c rh_tone_a: the curve at offset o (Q25), Q15"""
    o = max(o, O_LO)
    u = o - O_LO
    i, f = u >> SH, (u >> (SH - 15)) & 0x7FFF
    if i >= len(A) - 1:
        return A[-1]
    return A[i] + (((A[i + 1] - A[i]) * f) >> 15)


def fit_w(out, R, n_grid):
    """S1a, with the pickup drive (RH_DRIVE): each key's swing at each velocity is its own (the drive holds the soft
    colour to the register's knee, then folds), so one blend of the soft and the hard curve by velocity no longer
    fits every key (TONE 127: the bass +4 LU, the treble -5 LU). Per TYPE, key and velocity anchor the blend weight
    w (a = soft + (hard - soft) w, extrapolated past 0 .. 1) that takes the loudness of TONE 80 .. 127 back to TONE
    64's best (least squares, the host engine without the compensation); every WKEY_STEP-th key, interpolated."""
    import concurrent.futures as cf
    import rhodes_model as gm
    offs, tys, up, omax, lo, fams, shs = tables()
    nk = len(offs)
    keys = sorted(set(range(lo, lo + nk, WKEY_STEP)) | {lo + nk - 1})
    jobs = [(ty, n, v, t) for ty in range(4) for n in keys for v in gm.V_ANCH for t in WTONES]
    with cf.ThreadPoolExecutor(int(os.environ.get("TONE_LEVEL_J", "2"))) as ex:
        L = list(ex.map(lambda j: lufs(render(out, j[0], j[3], j[1], j[2], NOG)), jobs))
    L = dict(zip(jobs, L))
    ws = np.arange(-WQ, 255 - WQ + 1) / WQ
    gmax = 10 ** (WGAIN_MAX_DB / 20)

    def safe(ty, n):
        """the weights whose gain stays within WGAIN_MAX_DB at every TONE 65 .. 127 (a and a64 linear in w: an
        interval, so the anchors' and the keys' interpolation stays inside it)"""
        fam, sh = fams[n - lo], shs[fams[n - lo]]
        S, H = R[fam][ty]
        o64 = offset(offs[n - lo], tys[ty], 64, up, omax, sh)
        a64 = tone_a(S, o64) + (tone_a(H, o64) - tone_a(S, o64)) * ws
        ok = a64 >= 1
        for t in range(65, 128):
            o = offset(offs[n - lo], tys[ty], t, up, omax, sh)
            ok &= tone_a(S, o) + (tone_a(H, o) - tone_a(S, o)) * ws >= a64 / gmax
        return ok

    W = np.zeros((4, nk, len(gm.V_ANCH)))
    worst = 0.0
    for ty in range(4):
        ok = {n: safe(ty, n) for n in range(lo, lo + nk)}
        for a, v in enumerate(gm.V_ANCH):
            w0 = min(max(v - 40, 0), 87) ** 2 / 87 / 87          # the velocity law it replaces (rh_tone_gain)
            col = []
            for n in keys:
                fam, sh = fams[n - lo], shs[fams[n - lo]]
                S, H = R[fam][ty]
                o64 = offset(offs[n - lo], tys[ty], 64, up, omax, sh)
                e = WREG * (len(WTONES) - 1) * (ws - w0) ** 2       # a light pull to the old law (flat minima)
                for t in WTONES[1:]:
                    o = offset(offs[n - lo], tys[ty], t, up, omax, sh)
                    s64, h64, so, ho = tone_a(S, o64), tone_a(H, o64), tone_a(S, o), tone_a(H, o)
                    a64 = np.maximum(1, s64 + (h64 - s64) * ws)
                    ao = np.maximum(1, so + (ho - so) * ws)
                    e += (L[ty, n, v, t] - L[ty, n, v, 64] + 20 * np.log10(a64 / ao)) ** 2
                e = np.where(ok[n], e, np.inf)
                col.append(ws[int(np.argmin(e))])
                worst = max(worst, float(np.sqrt(e.min() / (len(WTONES) - 1))))
            wi = np.interp(np.arange(lo, lo + nk), keys, col)
            for i, n in enumerate(range(lo, lo + nk)):              # each key inside its own safe interval
                sw = ws[ok[n]]
                W[ty, i, a] = min(max(wi[i], sw.min()), sw.max())
    print("tone blend: rms residual worst key %.2f LU" % worst, file=sys.stderr)
    return np.clip(np.round(W * WQ) + WQ, 0, 255).astype(int)


def write(R, n_grid, omax, W=None):
    o = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* Generated by tools/tone_level.py --write: do not edit. The Rhodes loudness against the pickup offset (Q15,",
         " * the loudest 32767), per TYPE, soft (v40) and hard (v127): the host engine without the compensation",
         " * (-DRH_NO_TONE_G), NOISE 0, CC 119 RAW, per pickup family (S8; its offsets in its own gap) the LUFS of its keys",
         " * (the 1/32\": C2 C3 A3 C5 C6; the 1/16\": C1 E1 G#1 C7 E7; one curve + a level per key, least squares) on a",
         " * grid of 2^RH_TG_SH Q25 (1/128 gap) from RH_TG_O_LO to the highest family cap. rhodes.c rh_tone_gain */",
         "#define RH_TG_SH %d" % SH, "#define RH_TG_O_LO (%d)" % O_LO, "#define RH_TG_N %du" % n_grid,
         "static const uint16_t RH_TONE_A[2][4][2][RH_TG_N] = {"]
    for fam in (0, 1):
        o.append(" {  /* family %d (%s) */" % (fam, "1/16\"" if fam == 0 else "1/32\""))
        for ty in range(4):
            o.append("  {   /* %s */" % TYPES[ty])
            for v in R[fam][ty]:
                o.append("    {%s}," % ",".join(str(x) for x in v))
            o.append("  },")
        o.append(" },")
    o.append("};")
    if W is not None:
        o += ["/* S1a (the pickup drive): the blend of the soft and the hard curve per TYPE, key and velocity anchor",
              " * (RH_VPOS_I / RH_VPOS_F), stored x %d + %d: a = soft + (hard - soft) w (tone_level.py fit_w) */" % (WQ, WQ),
              "#define RH_TONE_W_Q %d" % WQ,
              "static const uint8_t RH_TONE_W[4][RH_NKEY][RH_NANCH] = {"]
        for ty in range(4):
            o.append(" {  /* %s */" % TYPES[ty])
            for row in W[ty]:
                o.append("  {%s}," % ",".join(str(int(x)) for x in row))
            o.append(" },")
        o.append("};")
    open(HDR, "w").write("\n".join(o) + "\n")


HDR_B = os.path.join(ROOT, "firmware", "gen", "rh_tone_b.h")
TWB_K = (32, 33, 52, 64, 72, 84, 95, 96)                   # rhodes.c rh_tone_wb: the key anchors (both families' edges)
TWB_V0 = 4                                                 # the velocity grid v = 0, 8 .. 128: fitted from v32 up;
                                                           # below it the soft curve (w 0), as measured at v40


def fit_wb(out, R):
    """RESPONSE B (the velocity map): the blend w (x 87: a = soft + (hard - soft) w / 87) per TYPE, key anchor and
    velocity (v = 0, 8 .. 128; v128 measured at 127) that takes TONE 80 .. 127's loudness back to TONE 64's (least
    squares over WTONES, the host engine without the compensation, RESPONSE B), within WGAIN_MAX_DB of gain"""
    import concurrent.futures as cf
    offs, tys, up, omax, lo, fams, shs = tables()
    vels = [min(8 * i, 127) for i in range(TWB_V0, 17)]
    jobs = [(ty, n, v, t) for ty in range(4) for n in TWB_K for v in vels for t in WTONES]
    with cf.ThreadPoolExecutor(int(os.environ.get("TONE_LEVEL_J", "2"))) as ex:
        L = list(ex.map(lambda j: lufs(render(out, j[0], j[3], j[1], j[2], NOG)), jobs))
    L = dict(zip(jobs, L))
    ws = np.arange(-87, 5 * 87 + 1)
    gmax = 10 ** (WGAIN_MAX_DB / 20)
    W = np.zeros((4, len(TWB_K), 17), dtype=int)
    worst = 0.0
    for ty in range(4):
        for ki, n in enumerate(TWB_K):
            fam, sh = fams[n - lo], shs[fams[n - lo]]
            S, H = R[fam][ty]
            ao = {t: (lambda o: (tone_a(S, o), tone_a(H, o)))(offset(offs[n - lo], tys[ty], t, up, omax, sh))
                  for t in range(64, 128)}
            s64, h64 = ao[64]
            a64 = np.maximum(1, s64 + (h64 - s64) * ws / 87)
            ok = a64 >= 1
            for t in range(65, 128):                               # the gain bound at every TONE
                ok &= np.maximum(1, ao[t][0] + (ao[t][1] - ao[t][0]) * ws / 87) >= a64 / gmax
            for vi, v in enumerate(vels):
                e = np.zeros(len(ws))
                for t in WTONES[1:]:
                    a = np.maximum(1, ao[t][0] + (ao[t][1] - ao[t][0]) * ws / 87)
                    e += (L[ty, n, v, t] - L[ty, n, v, 64] + 20 * np.log10(a64 / a)) ** 2
                e = np.where(ok, e, np.inf)
                W[ty, ki, TWB_V0 + vi] = int(ws[int(np.argmin(e))])
                worst = max(worst, float(np.sqrt(e.min() / (len(WTONES) - 1))))
    print("tone blend (B): rms residual worst anchor %.2f LU" % worst, file=sys.stderr)
    return W


def write_b(R, W):
    o = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* Generated by tools/tone_level.py --write --map: do not edit. TONE's level compensation for the velocity",
         " * method MAP (RESPONSE B; rhodes.c rh_tone_gain, rh_velm 1): the loudness against the pickup offset (Q15, the",
         " * loudest 32767) per pickup family, TYPE, soft (v40) and hard (v127), as rh_tone_g.h's but measured with",
         " * RESPONSE B (CC 40 127), the host engine without the compensation (-DRH_NO_TONE_G), NOISE 0, CC 119 RAW.",
         " * Physics only (the host engine), no recordings. */",
         "static const uint16_t RH_TONE_AB[2][4][2][RH_TG_N] = {"]
    for fam in (0, 1):
        o.append(" {  /* family %d (%s) */" % (fam, "1/16\"" if fam == 0 else "1/32\""))
        for ty in range(4):
            o.append("  {   /* %s */" % TYPES[ty])
            for v in R[fam][ty]:
                o.append("    {%s}," % ",".join(str(x) for x in v))
            o.append("  },")
        o.append(" },")
    o.append("};")
    o += ["/* the soft / hard blend per TYPE and key anchor (RH_TWB_K, linear between, held beyond) at v = 0, 8 .. 128",
          " * (0 soft, 87 hard, extrapolated beyond either), fitted at v%d .. v127 to give TONE 64's loudness back with" % (8 * TWB_V0),
          " * the velocity map (tone_level.py fit_wb; below: the soft curve); -DRH_NO_VM: rh_tone_gain's (vel - 40)^2 / 87 */",
          "#define RH_TWB_N %du" % len(TWB_K),
          "static const uint8_t RH_TWB_K[RH_TWB_N] = {%s};" % ", ".join(str(k) for k in TWB_K),
          "static const int16_t RH_TONE_WB[4][RH_TWB_N][17] = {"]
    for ty in range(4):
        o.append("  {   /* %s */" % TYPES[ty])
        for row in W[ty]:
            o.append("    {%s}," % ",".join(str(int(x)) for x in row))
        o.append("  },")
    o.append("};")
    open(HDR_B, "w").write("\n".join(o) + "\n")


def level(out, ty, tone, keys, vel):
    return [lufs(render(out, ty, tone, n, vel)) for n in keys]


def residual(out, vels=(40, 100, 127), keys=KEYS):
    R = {}
    for vel in vels:
        for ty in range(4):
            ref = np.array(level(out, ty, 64, keys, vel))
            R[vel, ty] = [np.array(level(out, ty, a, keys, vel)) - ref for a in ANCHORS]
    return R


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "tone_level"))
    ap.add_argument("--map", action="store_true", help="RESPONSE B (the velocity map): measure / write rh_tone_b.h")
    a = ap.parse_args()
    global RESP
    RESP = 127 if a.map else 0
    if a.write and a.map:
        R, n_grid, omax = measure(a.out)
        write_b(R, fit_wb(a.out, R))
        print("wrote", os.path.relpath(HDR_B, ROOT))
        return
    if a.write:
        R, n_grid, omax = measure(a.out)
        drive = "#define RH_DRIVE" in open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
        write(R, n_grid, omax, fit_w(a.out, R, n_grid) if drive else None)
        print("wrote", os.path.relpath(HDR, ROOT))
        return
    for fam in (1, 0):
        R = residual(a.out, keys=FKEYS[fam])
        print("family %d %s TONE re 64, LU: the key mean (the worst key)   " % (fam, FKEYS[fam])
              + " ".join("%11d" % v for v in ANCHORS))
        for (vel, ty), rows in R.items():
            print("v%-3d %-9s" % (vel, TYPES[ty]) + " ".join("%+5.2f(%+4.1f)" % (r.mean(), r[np.argmax(np.abs(r))])
                                                          for r in rows))


if __name__ == "__main__":
    main()
