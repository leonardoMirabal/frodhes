# SPDX-License-Identifier: GPL-3.0-only
"""PLACEHOLDER tables for the bar engine (firmware/src/bars.c) until tools/gen_bars.py lands.

Writes firmware/gen/bars_tables.h in the exact format of the plan's section 4.4 (the contract between the generator
and bars.c), with plausible but UNCALIBRATED values: 1:4:10 bar modes with a simple per-key detune, V4 at 13.76x,
torsion T1/T2 at 3.05x / 11.5x, simple decay laws, a tube a few cents above V1 with a fixed coupling, half-sine
mallet pulses whose width follows a Hertz-like law per family, analytic free-free bar shapes for the strike map.
Nothing here is measured or fitted; the real generator (vibes.json -> gen_bars.py) replaces this file and its
output entirely. Deterministic (two runs give the same header). No samples.

    python3 tools/gen_bars_stub.py            -> firmware/gen/bars_tables.h
"""
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rh_coefs import mode_coefs  # noqa: E402

FS = 44100.0
Q12, Q14, Q15, Q16, Q25, Q28, Q30 = 1 << 12, 1 << 14, 1 << 15, 1 << 16, 1 << 25, 1 << 28, 1 << 30
ROM_LO, ROM_HI = 0xFFC00000, 0xFFCFFFFF
KEY_LO, NKEY, NM, NFAM, NBAND, NLAYER, PLEN, NPOS, NTYPE = 21, 88, 8, 4, 16, 8, 64, 17, 1
LAYER_V = [8, 24, 40, 56, 72, 88, 104, 120]
V_ANCH = [1, 8, 24, 40, 56, 72, 88, 104, 120, 127]
FAM = [  # SOFT YARN, MED YARN, CORD, RUBBER: contact exponent p, contact time at 1 m/s (s), pulse shape power
    dict(p=2.5, tau=1.2e-3, q=1.6), dict(p=2.0, tau=0.7e-3, q=1.3), dict(p=1.5, tau=0.42e-3, q=1.0),
    dict(p=1.5, tau=0.17e-3, q=1.0)]


def rom_safe(v):
    v = int(v)
    u = v & 0xFFFFFFFF
    while ROM_LO <= u <= ROM_HI:
        v += 1 if v < 0 else -1
        u = v & 0xFFFFFFFF
    return v


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def speed(v):                       # mallet speed (m/s) vs MIDI velocity: 0.3 .. 3.5, log
    return 0.3 * 11.67 ** ((v - 1) / 126)


def width_s(fam, v):                # contact time: tau(1 m/s) * V^-((p-1)/(p+1))
    p = FAM[fam]["p"]
    return FAM[fam]["tau"] * speed(v) ** (-(p - 1) / (p + 1))


def hsh(n, salt):                   # deterministic per-key value in [0, 1)
    return int(hashlib.sha256(f"{n}:{salt}".encode()).hexdigest()[:8], 16) / 2 ** 32


def interp(n, pts):
    xs = sorted(pts)
    if n <= xs[0]:
        return pts[xs[0]]
    if n >= xs[-1]:
        return pts[xs[-1]]
    for a, b in zip(xs, xs[1:]):
        if a <= n <= b:
            return pts[a] + (pts[b] - pts[a]) * (n - a) / (b - a)


def ff_shape(k, x):                 # free-free Euler-Bernoulli bending mode k (1..4) at x in [0, 1], |phi| <= ~2
    bl = [4.7300408, 7.8532046, 10.9956078, 14.1371655][k - 1]
    s = (math.cosh(bl) - math.cos(bl)) / (math.sinh(bl) - math.sin(bl))
    z = bl * x
    return math.cosh(z) + math.cos(z) - s * (math.sinh(z) + math.sin(z))


def key_data(n):
    f0 = midi_hz(n)
    t60_free = min(40.0, 25.0 * (174.6 / f0) ** 0.77, 7330.0 / f0)
    felt = 0.5 * (1.7 if n <= 57 or n >= 86 else 1.0)
    zeta = min(0.04, max(0.005, interp(n, {53: 0.018, 89: 0.038})))
    cents = interp(n, {53: 5.0, 84: 20.0, 89: 100.0})
    sigma = 0.006
    # the tuned pair (stiffness coupling in mass-normalised coordinates): put the V1-like normal mode on f0
    r = 2 ** (cents / 1200)
    w1, w2 = 1.0, r
    kap = sigma * w1 * w2
    m, d = (w1 * w1 + w2 * w2) / 2, (w1 * w1 - w2 * w2) / 2
    lo = math.sqrt(m - math.sqrt(d * d + kap * kap))
    f1, ft = f0 / lo, f0 * r / lo
    t60_tube = 6.908 / (2 * math.pi * ft * zeta)
    # heard V1 decay (weak regime): extra damping sigma^2 / (4 (zeta_r - zeta_n))
    zn = 6.908 / (2 * math.pi * f0 * t60_free)
    t60_v1 = 6.908 / (2 * math.pi * f0 * (zn + sigma * sigma / (4 * max(zeta - zn, 1e-4))))
    G = 8.0
    th1, tht = 2 * math.pi * f1 / FS, 2 * math.pi * ft / FS
    k_bt, k_tb = sigma * math.sqrt(th1 * tht) * G, sigma * math.sqrt(th1 * tht) / G
    ratio = G * sigma / math.hypot(2 * (r - 1), 2 * zeta)          # |x_tube / x_bar| near resonance
    wt = min(3.9, 3.16 / ratio)                                     # tube part ~ +10 dB over the bar's f0
    d2, d3, d4 = hsh(n, "v2") * 7, -50 + hsh(n, "v3") * 160, -70 + hsh(n, "v4") * 140
    law = lambda f: min(t60_free * (f0 / f) ** 0.8, 7330.0 / f)   # noqa: E731
    modes = [  # (f, t60, t60_rel, weight) per slot V1 TUBE V2 V3 V4 T1 T2 X
        (f1, t60_free, felt, 1.0),
        (ft, t60_tube, t60_tube, 0.0),
        (4 * f0 * 2 ** (d2 / 1200), min(interp(n, {53: 5.0, 65: 1.5, 77: 0.9}), 7330 / (4 * f0)), felt * 0.7,
         0.35 if n <= 79 else 0.0),
        (10 * f0 * 2 ** (d3 / 1200), min(interp(n, {53: 1.2, 72: 0.4}), 7330 / (10 * f0)), felt * 0.7,
         0.12 if n <= 72 else 0.0),
        (13.76 * f0 * 2 ** (d4 / 1200), min(1.0, 7330 / (13.76 * f0)), felt * 0.7, 0.03 if n <= 72 else 0.0),
        (3.05 * f0, law(3.05 * f0) * 0.85, felt * 0.7, 0.10 if n <= 84 else 0.0),
        (11.5 * f0, law(11.5 * f0) * 0.85, felt * 0.7, 0.05 if n <= 84 else 0.0),
        (0.0, 1.0, 1.0, 0.0),
    ]
    return dict(n=n, f0=f0, modes=modes, k_bt=k_bt, k_tb=k_tb, wt=wt, t60_v1=t60_v1, ratio=ratio,
                flags=(1 if n < 53 or n > 89 else 0) | (2 if modes[5][3] else 0))


def pulse_shape(fam):
    q = FAM[fam]["q"]
    s = [int(round(32767 * math.sin(math.pi * i / (PLEN - 1)) ** q)) for i in range(PLEN)]
    return s + [0, 0]


def build():
    keys = [key_data(n) for n in range(KEY_LO, KEY_LO + NKEY)]
    # the strongest impulse (RUBBER, v127, HAMMER 64) gives V1 a peak of ~2^24 at the strike point weight 1
    amp_raw = [[speed(v) / width_s(f, v) for v in V_ANCH] for f in range(NFAM)]
    amax = max(max(r) for r in amp_raw)
    pamp = [[max(1, int(round(32767 * a / amax))) for a in r] for r in amp_raw]
    prate = [[int(round(64.0 / (width_s(f, v) * FS) * Q12)) for v in V_ANCH] for f in range(NFAM)]
    imp = 0.637 * 32767 * width_s(3, 127) * FS                       # sum of F over the strongest pulse
    b1 = (1 << 24) * (1 << 20) / imp / Q30                           # Q30 fraction
    lvl = speed(100) / speed(127)
    for k in keys:
        k["b"] = [b1 * w for (_, _, _, w) in k["modes"]]
        x0 = (1 << 24) * lvl * 0.6                                   # V1 peak at v100 (strike weight ~0.6)
        heard = x0 * (1 + k["wt"] * min(k["ratio"], 1.0))
        k["gain"] = 0.03 * (1 << 20) * (1 << 24) / heard / Q16       # -30 dBFS peak-ish at v100 (placeholder)
        k["kill"] = 0.5 * 32 * (1 << 24) / (k["gain"] * Q16) / 2     # output under 1/2 LSB, x2 margin (Q25 units)
        k["life"] = int(min(65535, math.ceil((2 * max(k["t60_v1"], 1.0) + 1.0) * FS / 256)))
        k["band"] = (k["n"] - KEY_LO) * NBAND // NKEY
    calibrate(keys, pamp, prate)
    return keys, pamp, prate


def calibrate(keys, pamp, prate, vel=100, target_dbfs=-26.0):
    """Per-key gain so a note at v100 (MED YARN, TONE 0, BELL 64) has an RMS of target over 50..300 ms: a float run
    of the C structure (pulse into V1 V2 V3 V4, the V1 <-> TUBE pair), every key at once."""
    import numpy as np
    from rh_coefs import coef
    n = len(keys)
    N = int(0.30 * FS)
    E = np.zeros((n, 5)); P = np.zeros((n, 5)); B = np.zeros((n, 5))
    wpos = [abs(ff_shape(k, 0.35)) / abs(ff_shape(k, 0.0)) for k in (1, 2, 3, 4)]
    wslot = [wpos[0], 0.0, wpos[1], wpos[2], wpos[3]]
    for i, k in enumerate(keys):
        for m in range(5):
            f, t60, trel, _ = k["modes"][m]
            c = mode_coefs(f, t60, trel)
            if not c["th"] or (m != 1 and not k["b"][m]):
                continue
            e, eps = coef(c, c["d"])
            E[i, m], P[i, m] = e / Q30, eps / Q30
            B[i, m] = round(k["b"][m] * Q30) * wslot[m] / (1 << 20)
    kbt = np.array([k["k_bt"] for k in keys]); ktb = np.array([k["k_tb"] for k in keys])
    wt = np.array([k["wt"] for k in keys])
    kbt0 = np.array([k["k_bt"] for k in keys]); ktb0 = np.array([k["k_tb"] for k in keys])
    wt0 = np.array([k["wt"] for k in keys])

    def run(fam, v, s, N, hard=1.0):
        ai = max(i for i in range(len(V_ANCH)) if V_ANCH[i] <= v)
        a = 0.0 if ai == len(V_ANCH) - 1 else (v - V_ANCH[ai]) / (V_ANCH[ai + 1] - V_ANCH[ai])
        amp = (pamp[fam][ai] + (pamp[fam][min(ai + 1, len(V_ANCH) - 1)] - pamp[fam][ai]) * a) * hard
        W = width_s(fam, v) * FS / hard
        shp = np.array(pulse_shape(fam)[:PLEN], float)
        kbt, ktb, wt = kbt0 * s, ktb0 * s, wt0 * s
        x = np.zeros((n, 5)); y = np.zeros((n, 5)); out = np.zeros((n, N))
        for j in range(N):
            F = min(amp, 32767) * np.interp(j / W * PLEN, np.arange(PLEN), shp, right=0.0) / 32768 if j < W else 0.0
            if F:
                y += F * B
            y[:, 0] += ktb * x[:, 1]
            x[:, 0] += E[:, 0] * y[:, 0]
            y[:, 0] += -P[:, 0] * y[:, 0] - E[:, 0] * x[:, 0]
            y[:, 1] += kbt * x[:, 0]
            x[:, 1:] += E[:, 1:] * y[:, 1:]
            y[:, 1:] += -P[:, 1:] * y[:, 1:] - E[:, 1:] * x[:, 1:]
            out[:, j] = x[:, 0] + wt * x[:, 1] + x[:, 2:].sum(axis=1)
        return out * Q16 / (1 << 24) / 32 / 32767                  # full scale 1.0, at gain 1.0 (Q16 65536)

    seg = run(1, vel, 1.0, int(0.30 * FS))[:, int(0.05 * FS):]
    rms = np.sqrt(np.mean(seg * seg, axis=1))
    # the loudest single note: RUBBER v127, BELL 127 (s = 1.2), the lightest mallet (contact 1.32x faster, harder)
    pk = np.max(np.abs(run(3, 127, 1.2, int(0.10 * FS), hard=1.32)), axis=1)
    for k, r, p in zip(keys, rms, pk):
        k["gain"] = min(10 ** (target_dbfs / 20) / max(r, 1e-12), 0.5 / max(p, 1e-12))   # peak <= -6 dBFS
        k["kill"] = 0.5 * 32 * (1 << 24) / (k["gain"] * Q16) / 2


def emit(keys, pamp, prate):
    out = []
    w = out.append
    w("/* SPDX-License-Identifier: GPL-3.0-only */")
    w("/* PLACEHOLDER bar tables: generated by tools/gen_bars_stub.py (uncalibrated stand-in values, NOT the physics")
    w(" * model). tools/gen_bars.py (vibes.json) replaces this file in the same format (plan section 4.4). */")
    w("#define BAR_GEN_VERSION 0          /* 0 = placeholder */")
    w(f"#define BAR_KEY_LO {KEY_LO}")
    w(f"#define BAR_NKEY {NKEY}")
    w(f"#define BAR_NM {NM}                 /* V1 TUBE V2 V3 V4 T1 T2 X */")
    w(f"#define BAR_NFAM {NFAM}               /* SOFT YARN, MED YARN, CORD, RUBBER */")
    w(f"#define BAR_NBAND {NBAND}")
    w(f"#define BAR_NLAYER {NLAYER}")
    w(f"#define BAR_PLEN {PLEN}")
    w(f"#define BAR_NPOS {NPOS}")
    w(f"#define BAR_NTYPE {NTYPE}")
    w("#define BAR_FADE_STEP 128        /* steal fade 256 samples: 32768 / 256 */")
    w("static const bar_key_t BAR_KEYS[BAR_NKEY] = {")
    for k in keys:
        ms = []
        for (f, t60, trel, _), b in zip(k["modes"], k["b"]):
            c = mode_coefs(f, t60, trel)
            bb = rom_safe(round(b * Q30)) if c["th"] and b else 0
            if not bb and f and _ == 0.0 and len(ms) != 1:       # silent upper slot: leave it out (th = 0)
                c = mode_coefs(0, 1, 1)
            ms.append("{%d,%d,%d,%d,%d,%d,%d}" % (rom_safe(c["sh"]), rom_safe(c["ch"]), rom_safe(c["th"]),
                                                  rom_safe(c["d"]), rom_safe(c["drel"]), rom_safe(c["cr"]), bb))
        w("  {{%s}, %d, %d, %d, %d, %d, %d, %d, 0x%02X}," % (
            ",".join(ms), rom_safe(round(k["k_bt"] * Q30)), rom_safe(round(k["k_tb"] * Q30)),
            rom_safe(round(k["wt"] * Q28)), rom_safe(round(k["gain"] * Q16)), rom_safe(round(k["kill"])),
            k["life"], k["band"], k["flags"]))
    w("};")
    w("static const int16_t BAR_PULSE[BAR_NBAND][BAR_NFAM][BAR_NLAYER][BAR_PLEN + 2] = {")
    for _ in range(NBAND):
        w("  {" + ",".join("{" + ",".join("{" + ",".join(map(str, pulse_shape(f))) + "}"
                                          for _ in range(NLAYER)) + "}" for f in range(NFAM)) + "},")
    w("};")
    w("static const uint32_t BAR_PRATE[BAR_NKEY][BAR_NFAM][RH_NANCH] = {")
    for _ in keys:
        w("  {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in prate) + "},")
    w("};")
    w("static const uint16_t BAR_PAMP[BAR_NKEY][BAR_NFAM][RH_NANCH] = {")
    for _ in keys:
        w("  {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in pamp) + "},")
    w("};")
    # strike map: TONE 0..64 -> x 0.5 .. 0.35 L on the centreline; 64..127 -> x 0.35 L, toward the edge (torsion)
    pos = []
    for i in range(NPOS):
        t = i * 127 / (NPOS - 1)
        x = 0.5 - 0.15 * min(t, 64) / 64
        y = max(0.0, (t - 64) / 63)
        wv = [abs(ff_shape(k, x)) / abs(ff_shape(k, 0.0)) for k in (1, 2, 3, 4)]
        row = [wv[0], 1.0, wv[1], wv[2], wv[3], y * wv[0], y * wv[2], 0.0]
        pos.append("{" + ",".join(str(int(round(min(1.0, v) * Q14))) for v in row) + "}")
    w("static const int16_t BAR_POS[BAR_NBAND][BAR_NPOS][BAR_NM] = {")
    for _ in range(NBAND):
        w("  {" + ",".join(pos) + "},")
    w("};")
    w("static const int32_t BAR_BODY[BAR_NTYPE][2][5] = {{{%d,0,0,0,0},{%d,0,0,0,0}}};" % (Q28, Q28))
    w("static const bar_type_t BAR_TYPE[BAR_NTYPE] = {   /* tube cents, g_c, torsion Q15, nbody, drive row, sleep_rel, budget */")
    w("  {0, %d, %d, 0, 0, 9, 11}," % (round(0.15 * Q15), round(10 ** (-24 / 20) * Q15)))
    w("};")

    def tab(name, typ, vals):
        w(f"static const {typ} {name}[128] = {{" + ",".join(str(int(v)) for v in vals) + "};")
    fam = []
    for v in range(128):
        p = v * 3 / 127
        i = min(int(p), 3)
        fam.append(i << 8 | (int((p - i) * 256) if i < 3 else 0))
    tab("KN_FAM", "uint16_t", fam)
    mass = [15 * 3 ** (v / 127) for v in range(128)]                  # 15 .. 45 g, log (30 g at ~64)
    m64 = mass[64]
    tab("KN_MASS", "uint16_t", [round(Q12 * (m / m64) ** -0.4) for m in mass])
    tab("KN_MASSI", "uint16_t", [round(Q12 * (m / m64) ** -0.4) for m in mass])
    tab("KN_TUBE", "uint16_t", [round(Q15 * (v / 64 if v <= 64 else 1.2 ** ((v - 64) / 63))) for v in range(128)])
    tab("KN_NZ", "uint16_t", [round(32767 * (v / 127) ** 2) for v in range(128)])

    def bq(fp, rp, fz, rz):
        tp, tz = 2 * math.pi * fp / FS, 2 * math.pi * fz / FS
        b = [1.0, -2 * rz * math.cos(tz), rz * rz]
        a = [-2 * rp * math.cos(tp), rp * rp]
        z = complex(math.cos(tp), math.sin(tp))
        h = abs((b[0] + b[1] / z + b[2] / z / z) / (1 + a[0] / z + a[1] / z / z))
        b = [x / h for x in b]
        return "{" + ",".join(str(int(round(x * Q14))) for x in b + a) + "}"
    w("static const int16_t NZ_BQ[2][5] = {%s,%s};   /* b0 b1 b2 a1 a2, Q14 */" % (
        bq(2000, 0.9, 5030, 0.98), bq(4000, 0.964, 8000, 0.8)))
    w("static const int16_t BAR_MOT_COS[257] = {" + ",".join(
        str(int(round(32767 * abs(math.cos(math.pi * i / 512))))) for i in range(257)) + "};   /* Q15 |cos(pi i / 512)| */")
    body = "\n".join(out) + "\n"
    return body + f"/* sha256(body) = {hashlib.sha256(body.encode()).hexdigest()} */\n"


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "firmware", "gen", "bars_tables.h")
    keys, pamp, prate = build()
    open(path, "w").write(emit(keys, pamp, prate))
    for n in (53, 60, 69, 77, 89):
        k = keys[n - KEY_LO]
        print(f"key {n}: f0 {k['f0']:.1f} Hz, V1 T60 heard {k['t60_v1']:.1f} s, wt {k['wt']:.2f}, "
              f"gain {k['gain']:.3f}, life {k['life']}")
    print("->", path)


if __name__ == "__main__":
    main()
