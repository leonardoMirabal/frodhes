#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate the Frodhes voice tables from the physics (IMPLEMENTATION-v3 §6).

    tools/gen_rhodes.py            -> firmware/gen/rhodes_tables.h  +  docs/model/data/rhodes_spec.json

Per key 21..108: FE tine + tuning spring (tine_fe), 2-DOF tine-bar normal modes (tinebar), overtone modes, strike
weights, coefficients for the MC3 kernel, pickup family and offset, gain and kill. Per hammer band: the coupled
contact simulation (hammer) gives the force-pulse shapes, the velocity -> amplitude and contact-rate laws. Pickup
tables per gap family from the closed-form flux (pickup). The float spec (rhodes_spec.json) feeds the golden model
(tools/rhodes_model.py); the header feeds the C engine. Output is deterministic (byte-identical every run).

Inputs: docs/model/data/params.json (priors until the reference fit), docs/model/data/tine_lengths.csv.
"""
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import hammer as hm        # noqa: E402
import pickup as pu        # noqa: E402
import tine_fe as fe       # noqa: E402
import tinebar as tbm      # noqa: E402

GEN_VERSION = 1
FS = 44100.0
KEY_LO, KEY_HI = 21, 108
NM = 5                      # V1 V2 O1 O2 H1
NBAND, NLAYER, PLEN = 5, 8, 64
PU_N = 2048
S_MAX_GAP2 = 32.0
LAYER_V = [8, 24, 40, 56, 72, 88, 104, 120]
ROM_LO, ROM_HI = 0xFFC00000, 0xFFCFFFFF
Q30, Q29, Q28, Q25, Q16, Q15, Q12 = 1 << 30, 1 << 29, 1 << 28, 1 << 25, 1 << 16, 32767, 1 << 12


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def load_params():
    return json.load(open(os.path.join(ROOT, "docs/model/data/params.json")))


# ---------------------------------------------------------------- per-key physics
def tine_lengths():
    L = fe.load_lengths(os.path.join(ROOT, "docs/model/data/tine_lengths.csv"))
    keys = sorted(L)
    out = {}
    for n in range(KEY_LO, KEY_HI + 1):
        if n in L:
            out[n] = L[n]
        else:                                         # log-log extrapolation over the nearest 12 keys (§6.1)
            near = keys[:12] if n < keys[0] else keys[-12:]
            x = np.log([midi_hz(k) for k in near]); y = np.log([L[k] for k in near])
            a, b = np.polyfit(x, y, 1)
            out[n] = float(np.exp(a * np.log(midi_hz(n)) + b))
    return out


def t60_v1(p, n):
    c = p["t60_v1"]
    pts = c["points"]
    if n <= pts[0][0]:
        return pts[0][1]
    if n >= pts[-1][0]:
        return max(pts[-1][1] * 2 ** (-(n - pts[-1][0]) / c["halve_keys"]), c["floor_s"])
    xs = [q[0] for q in pts]
    return float(np.exp(np.interp(n, xs, [math.log(q[1]) for q in pts])))


def bar_cents(p, n):
    pts = p["tinebar"]["bar_cents_points"]
    return float(np.interp(n, [q[0] for q in pts], [q[1] for q in pts]))


def release_t60(p, n):
    r = p["release_t60"]
    for k in sorted(int(x) for x in r if x.isdigit()):
        if n <= k:
            return float(r[str(k)])
    return 0.2


def band_of(p, n):
    fk = p["bands"]["first_key"]
    b = 0
    for i, k in enumerate(fk):
        if n >= k:
            b = i
    return b


def family_of(p, n):
    for lo, hi in p["pickup"]["family0_keys"]:
        if lo <= n <= hi:
            return 0
    return 1


def strike_m(p, n):
    s = p["strike"]
    return s["lo_m"] + (s["hi_m"] - s["lo_m"]) * (n - KEY_LO) / (KEY_HI - KEY_LO)


def key_physics(p, n, L):
    """Float description of one key: modes (f, T60 hold, T60 release, amplitude per unit impulse at the tip)."""
    f0 = midi_hz(n)
    tb = p["tinebar"]
    # the tine's own target: the bar sits bar_cents above it; the 2-DOF retune then puts V1 exactly on f0
    try:
        ms = fe.tune_spring(L, f0, p["spring_pos"])
    except ValueError:
        ms = 0.0
    tine = fe.Tine(L, p["spring_pos"], ms)
    f, sh = tine.modes(6)
    xc = min(strike_m(p, n), 0.9 * L)
    psi_c = [sh(xc, m) for m in range(6)]
    psi_t = [sh(L, m) for m in range(6)]
    T1 = t60_v1(p, n)
    sig_t = 6.907755 / (T1 * tb["tine_t60_frac"])
    sig_b = 6.907755 / (T1 * tb["bar_t60_mult"])
    sys_ = tbm.TineBar(f[0], f[0] * 2 ** (bar_cents(p, n) / 1200), tb["k_rel"], tb["mu"], sig_t, sig_b)
    sys_ = tbm.retune(sys_, f0)
    held = sys_.modes()
    iv1 = tbm.sustain_mode(held)
    order = [iv1, 1 - iv1]
    Trel = release_t60(p, n)
    sig_d = 6.907755 / Trel - sig_t
    rel = sys_.with_damper(max(sig_d, 0.0)).modes()
    # match release modes to sustain modes by eigenvalue continuity (§6.3): follow each eigenvalue as the felt
    # damping grows in small steps (matching by weight would swap them: with the felt on, the bar mode rings longest)
    track = [complex(-s, 2 * math.pi * fq) for fq, s, _ in held]
    idx = [0, 1]
    for frac in np.linspace(0.05, 1.0, 20):
        cur = sys_.with_damper(max(sig_d, 0.0) * frac).modes()
        lc = [complex(-s, 2 * math.pi * fq) for fq, s, _ in cur]
        for i in range(2):
            idx[i] = int(np.argmin([abs(track[i] - c) for c in lc]))
            track[i] = lc[idx[i]]
    rel_by_held = {i: rel[idx[i]] for i in range(2)}
    gtip = psi_c[0] * psi_t[0]
    modes = []
    for slot, i in enumerate(order):
        fq, s, r = held[i]
        fr, sr, _ = rel_by_held[i]
        modes.append(dict(name=["V1", "V2"][slot], f=fq, t60=6.907755 / s, t60_rel=6.907755 / sr,
                          amp=gtip * 2 * abs(r)))
    ex = p["overtone_damping_exp"]
    for m, name in ((1, "O1"), (2, "O2")):
        fq = f0 * f[m] / f[0]
        t60 = T1 * tb["tine_t60_frac"] / (f[m] / f[0]) ** ex
        modes.append(dict(name=name, f=fq, t60=t60, t60_rel=min(t60, Trel),
                          amp=psi_c[m] * psi_t[m] / (2 * math.pi * f[m])))
    modes.append(dict(name="H1", f=0.0, t60=1.0, t60_rel=1.0, amp=0.0))      # Phase 3
    return dict(midi=n, f0=f0, L=L, spring_g=ms * 1e3, ratios=[float(f[1] / f[0]), float(f[2] / f[0])],
                band=band_of(p, n), fam=family_of(p, n), off=p["pickup"]["off_gap"], modes=modes,
                fe_modes=[(float(f0 * f[m] / f[0]), float(sig_t * (f[m] / f[0]) ** ex), float(psi_c[m]))
                          for m in range(6)])


# ---------------------------------------------------------------- hammer pulses
def hammer_speed(p, v):
    h = p["hammer"]
    return h["v_max"] * 2 ** (-h["range_db"] / 6.0206 * (1 - (v - 1) / 126))


def contact_run(p, K, v, modes):
    h = p["hammer"]
    Rd = h["damping_rel"] * K * 1e-4
    return hm.contact(hammer_speed(p, v), h["mass_kg"], K, h["p"], Rd, modes=modes, t_max=0.03)


def band_pulses(p, phys_by_key):
    out = []
    for b in range(NBAND):
        rep = p["bands"]["rep_key"][b]
        modes = phys_by_key[rep]["fe_modes"]
        K = p["bands"]["K"][b]
        shapes = []
        for v in LAYER_V:
            r = contact_run(p, K, v, modes)
            n = int(round(r["Tc"] / r["dt"]))
            Fv = r["F"][:max(n, 2)]
            x = np.linspace(0, len(Fv) - 1, PLEN)
            sh = np.interp(x, np.arange(len(Fv)), Fv)
            shapes.append(sh / max(sh.max(), 1e-30))
        vs = [1, 8, 16, 32, 48, 64, 80, 96, 100, 112, 127]
        tcs, fpk = [], []
        for v in vs:
            r = contact_run(p, K, v, modes)
            tcs.append(r["Tc"]); fpk.append(r["F"].max())
        allv = np.arange(1, 128)
        tc_all = np.exp(np.interp(allv, vs, np.log(tcs)))
        fp_all = np.exp(np.interp(allv, vs, np.log(fpk)))
        tc100 = float(np.exp(np.interp(100, vs, np.log(tcs))))
        out.append(dict(K=K, tc100=tc100, shapes=shapes, tc=tc_all, fpk=fp_all,
                        vamp=fp_all / fp_all[-1], vrate=tc100 / tc_all))
    return out


# ---------------------------------------------------------------- coefficients
def mode_coefs(f, t60, t60_rel):
    if f <= 0 or f > 18000:
        return dict(sh=0, ch=0, th=0, d=0, drel=0, cr=0)
    th = 2 * math.pi * f / FS
    r = math.exp(-6.907755279 / (t60 * FS))
    rr = math.exp(-6.907755279 / (t60_rel * FS))
    return dict(sh=int(round(math.sin(th / 2) * Q30)), ch=int(round(math.cos(th / 2) * Q30)),
                th=max(1, int(round(th * Q29))), d=int(math.ceil((1 - r * r) * Q30)),
                drel=int(math.ceil((1 - rr * rr) * Q30)), cr=int(round(Q16 / (8 * math.sin(th / 2) ** 2))))


def rom_safe(v):
    """Nudge a 32-bit word out of the chip-ROM window (§3.6)."""
    u = v & 0xFFFFFFFF
    while ROM_LO <= u <= ROM_HI:
        v += 1 if v < 0 else -1
        u = v & 0xFFFFFFFF
    return v


# ---------------------------------------------------------------- pickup tables
def pickup_tables(R):
    s = np.arange(PU_N + 1) * (S_MAX_GAP2 / PU_N)
    phi = pu.phi_s(s, R)
    dds = pu.dphi_ds(s, R) * (S_MAX_GAP2 / PU_N)      # slope per cell
    A = float(max(np.max(np.abs(phi)), np.max(np.abs(dds))))
    P = [rom_safe(int(round(x * Q28 / A))) for x in phi]
    M = [rom_safe(int(round(x * Q28 / A))) for x in dds]
    return P, M, A


# ---------------------------------------------------------------- main
def build():
    p = load_params()
    Ls = tine_lengths()
    phys = {n: key_physics(p, n, Ls[n]) for n in range(KEY_LO, KEY_HI + 1)}
    bands = band_pulses(p, phys)
    fam_tabs = [pickup_tables(R) for R in p["pickup"]["R"]]
    fams = [dict(A=t[2], R=R) for t, R in zip(fam_tabs, p["pickup"]["R"])]
    import rhodes_model as gm                          # the golden model calibrates b, gain, kill
    tp = p["tip_peak_gap_v127"]
    spec_keys = []
    for n in range(KEY_LO, KEY_HI + 1):
        k = phys[n]
        b = k["band"]
        frac = (n - KEY_LO) / (KEY_HI - KEY_LO)
        target_peak = tp["lo"] + (tp["hi"] - tp["lo"]) * frac
        prate = 64.0 / (bands[b]["tc100"] * FS)      # pulse samples per output sample at v = 100
        spec = dict(midi=n, f0=k["f0"], band=b, fam=k["fam"], off=k["off"], prate=prate,
                    modes=[dict(name=m["name"], f=m["f"], t60=m["t60"], t60_rel=m["t60_rel"], w=m["amp"])
                           for m in k["modes"]],
                    ratios=k["ratios"], spring_g=k["spring_g"], L=k["L"])
        # unit b = mode amplitude per unit force-sample; scale so V1 peaks at target_peak (gap) at v = 127
        w1 = spec["modes"][0]["w"]
        for m in spec["modes"]:
            m["b"] = m["w"] / abs(w1) if w1 else 0.0
        pk = gm.v1_peak(spec, bands, 127)
        scale = target_peak / pk
        for m in spec["modes"]:
            m["b"] *= scale
        spec_keys.append(spec)
    # gain: v = 100, 1 s RMS -> target dBFS, smoothed over +-3 keys; kill: output below 1 LSB, x2 margin
    y = gm.render_batch(spec_keys, bands, fams, [100] * len(spec_keys), 1.0)
    rms = [float(np.sqrt(np.mean(r[2205:] ** 2))) + 1e-12 for r in y]
    target = 10 ** (p["gain_target_dbfs"] / 20) * 32767
    g_raw = np.array([target / r for r in rms])
    g_s = np.array([np.exp(np.mean(np.log(g_raw[max(0, i - 3):i + 4]))) for i in range(len(g_raw))])
    for spec, g in zip(spec_keys, g_s):
        spec["gain"] = float(g)
        spec["kill"] = gm.kill_level(spec, fams, g)
        spec["life_blocks"] = int(min(6891, math.ceil((2 * spec["modes"][0]["t60"] + 1.0) * FS / 256)))
    spec = dict(gen_version=GEN_VERSION, fs=FS, params=p, keys=spec_keys,
                bands=[dict(K=b["K"], tc100=b["tc100"], shapes=[s.tolist() for s in b["shapes"]],
                            vamp=b["vamp"].tolist(), vrate=b["vrate"].tolist()) for b in bands],
                pickup=fams)
    return spec, fam_tabs


def emit(spec, fam_tabs):
    p = spec["params"]
    L = []
    w = L.append
    w("/* SPDX-License-Identifier: GPL-3.0-only */")
    w("/* GENERATED by tools/gen_rhodes.py from docs/model/data/params.json - do not edit. */")
    w(f"#define RH_GEN_VERSION {GEN_VERSION}")
    w(f"#define RH_KEY_LO {KEY_LO}\n#define RH_NKEY {KEY_HI - KEY_LO + 1}\n#define RH_NM {NM}")
    w(f"#define RH_NBAND {NBAND}\n#define RH_NLAYER {NLAYER}\n#define RH_PLEN {PLEN}\n#define RH_PU_N {PU_N}")
    w("")
    rows = []
    for k in spec["keys"]:
        ms = []
        for m in k["modes"]:
            c = mode_coefs(m["f"], m["t60"], m["t60_rel"])
            # y (Q25) += (F (Q15) * b) >> 20  =>  b = w_gap * 2^25 * 2^20 / 2^15 = w * 2^30
            b = rom_safe(int(round(m["b"] * Q30))) if c["th"] else 0
            ms.append("{%d,%d,%d,%d,%d,%d,%d}" % (rom_safe(c["sh"]), rom_safe(c["ch"]), rom_safe(c["th"]),
                                                  rom_safe(c["d"]), rom_safe(c["drel"]), rom_safe(c["cr"]), b))
        prate = int(round(k["prate"] * Q12))
        rows.append("    {{%s}, %d, %d, %d, 0, 0, %d, %d, %d, %d, 0xFF, 0}," % (
            ",".join(ms), rom_safe(int(round(k["off"] * Q25))), int(round(k["gain"] * Q16)),
            int(round(k["kill"] * Q25)), prate, k["life_blocks"], k["band"], k["fam"]))
    w("static const rh_key_t RH_KEYS[RH_NKEY] = {")
    L.extend(rows)
    w("};")
    w("static const int16_t RH_PULSE[RH_NBAND][RH_NLAYER][RH_PLEN + 2] = {")
    for b in spec["bands"]:
        w("  {")
        for sh in b["shapes"]:
            w("    {" + ",".join(str(int(round(x * 32767))) for x in sh) + ",0,0},")
        w("  },")
    w("};")
    for name, key in (("RH_VAMP", "vamp"), ("RH_VRATE", "vrate")):
        w(f"static const uint16_t {name}[RH_NBAND][128] = {{")
        for b in spec["bands"]:
            vals = [0] + [int(round(x * (32767 if key == "vamp" else 4096))) for x in b[key]]
            w("  {" + ",".join(str(min(v, 65535)) for v in vals) + "},")
        w("};")
    vl = [int(np.argmin([abs(v - c) for c in LAYER_V])) for v in range(128)]
    w("static const uint8_t RH_VLAYER[128] = {" + ",".join(map(str, vl)) + "};")
    w("static const int32_t RH_PHI[2][RH_PU_N + 1] = {")
    for P, M, A in fam_tabs:
        w("  {" + ",".join(map(str, P)) + "},")
    w("};")
    w("static const int32_t RH_SLOPE[2][RH_PU_N + 1] = {")
    for P, M, A in fam_tabs:
        w("  {" + ",".join(map(str, M)) + "},")
    w("};")
    # post-mix RLC low-pass per TYPE (prewarped bilinear), Q28
    w("static const int32_t RH_RLC[4][5] = {")
    for q in p["rlc"]["q"]:
        f0 = p["rlc"]["f_hz"]
        K = math.tan(math.pi * f0 / FS)
        nrm = 1 / (1 + K / q + K * K)
        b0 = K * K * nrm; b1 = 2 * b0; b2 = b0; a1 = 2 * (K * K - 1) * nrm; a2 = (1 - K / q + K * K) * nrm
        w("  {" + ",".join(str(rom_safe(int(round(x * Q28)))) for x in (b0, b1, b2, a1, a2)) + "},")
    w("};")
    ty = p["types"]
    w("static const rh_type_t RH_TYPE[4] = {")
    for i, name in enumerate(("STAGE77", "SUITCASE", "FELT", "DYNO")):
        bs, gs, od, bell, lo, hi = ty[name]
        w("  {%d, %d, %d, %d, %d, %d, %d, %d}," % (bs, int(round(gs * Q16)), int(round(od * Q25)),
                                                 int(round(bell * Q16)), i, i,
                                                 int(round(4096 * 10 ** (lo / 20))), int(round(4096 * 10 ** (hi / 20)))))
    w("};")
    w("static const int32_t TUNE_T[101] = {" + ",".join(
        str(rom_safe(int(round((2 ** (c / 1200) - 1) * Q30)))) for c in range(-50, 51)) + "};")
    w("static const uint16_t DECAY_K[128] = {" + ",".join(
        str(min(65535 * 4, int(round(Q16 / 2 ** ((v - 64) / 32))))) for v in range(128)) + "};")
    w("static const uint16_t PEDAL_W[128] = {" + ",".join(str(x) for x in pedal_w()) + "};")
    names = ["30 duro", "50 duro", "70 duro", "90 duro", "wood"]
    w("/* hammer bands: " + ", ".join("%s K=%.3g Tc100=%.2f ms" % (nm, b["K"], b["tc100"] * 1e3)
                                    for nm, b in zip(names, spec["bands"])) + " */")
    body = "\n".join(L) + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return body + f"/* sha256(body) = {digest} */\n"


def pedal_w():
    """Damper weight (Q15) vs CC64: the jRhodes half-pedal curve inverted (§5.7)."""
    pts = [(0, 32768), (41, 32768), (42, 29491), (54, 22938), (62, 19661), (63, 19661), (64, 0), (127, 0)]
    xs, ys = zip(*pts)
    return [min(32767, int(round(np.interp(v, xs, ys)))) for v in range(128)]


def main():
    spec, fam_tabs = build()
    hdr = emit(spec, fam_tabs)
    os.makedirs(os.path.join(ROOT, "firmware/gen"), exist_ok=True)
    open(os.path.join(ROOT, "firmware/gen/rhodes_tables.h"), "w").write(hdr)
    js = json.dumps(spec, indent=1, sort_keys=True, default=float)
    open(os.path.join(ROOT, "docs/model/data/rhodes_spec.json"), "w").write(js + "\n")
    k = {s["midi"]: s for s in spec["keys"]}
    for n in (28, 45, 60, 69, 84, 96):
        s = k[n]
        print("key %3d f0 %7.1f band %d fam %d ratio %.2f  V1 %.1fHz T60 %.1fs | V2 %.1fHz T60 %.1fs w %.3f | O1 T60 %.2fs  gain %.0f" % (
            n, s["f0"], s["band"], s["fam"], s["ratios"][0], s["modes"][0]["f"], s["modes"][0]["t60"], s["modes"][1]["f"],
            s["modes"][1]["t60"], abs(s["modes"][1]["w"] / s["modes"][0]["w"]), s["modes"][2]["t60"], s["gain"]))
    for i, b in enumerate(spec["bands"]):
        print("band %d K %.3g Tc(100) %.2f ms  Tc(127)/Tc(8) %.2f" % (i, b["K"], b["tc100"] * 1e3,
                                                                        b["vrate"][7] / b["vrate"][126]))


if __name__ == "__main__":
    main()
