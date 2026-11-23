#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate the WHURLI voice tables (struck steel reeds, an electrostatic pickup) from the physics.

    tools/gen_reed.py              write firmware/gen/reed_tables.h
    tools/gen_reed.py --check      exit 1 if the header is not what the physics gives (tests/py/test_gen_reed.py)
    tools/gen_reed.py --repin      recompute the pinned solver results (docs/model/data/reed_pins.json)
    tools/gen_reed.py --report     also print the per-register physics (modes, decays, contact, swing, levels)

The model, per key 33..96 (64 reeds, A1..C7):
- the reed (tools/reed_bar.py): a clamped steel bar, flat in the bass and ground thinner toward the tongue above,
  tuned by the solder mass at its tip; finite elements give modes 1..3 and the mode shapes at the hammer line and at
  the tip. Decay: thermoelastic and viscous-air losses from the reed's dimensions, plus the loss into the reed bar
  (the support), which sets the mode-1 sustain targets.
- the hammer: felt over maple, F = K e^p (1 + alpha de/dt), against the reed's first three modes (tools/hammer.py,
  the contact simulation the tine piano uses): force shapes per pulse group and anchor, rates and amplitudes per key.
- the pickup (tools/reed_pickup.py): the capacitance between the reed's tip and its slotted plate against the reed's
  vertical position, from a 2-D Laplace solve. The plate is symmetric about its mid-plane, so C depends on u^2, u the
  tip's height over the mid-plane in units of the edge half-height (plate + reed thickness) / 2: one table in
  s = u^2 serves every key, each key's x and rest offset in its own unit. The engine's flux difference per sample is
  the capacitance difference: the current the polarizing voltage drives into the input load. Soft strikes stay on the
  smooth top of C(u) next to the rest point (fundamental and a little 2nd harmonic: warm); hard strikes swing the tip
  past the plate's edges, where C falls steeply and then flattens, so each pass makes a short, lopsided current pulse
  (even and odd harmonics together: the bark), strongest where the swing is large against the plate (bass, middle).
- the load and the amplifier: the pickup capacitance against the input load is a first-order corner (current mode
  below it, charge mode above), the small speakers a band-pass with a midrange lift (RD_RLC, RD_BODY).

Format: private/plans/whurli-interface.md (rh_key_t rows with 4 mode slots, 5 velocity anchors at v = 0, 32, 64, 96,
128, raw pulse shapes per group, the interleaved Hermite pickup row, Q28 biquads). Every word is checked out of the
chip-ROM window. The solver results (finite elements, capacitance curves) are pinned in reed_pins.json, rounded, so
every machine builds the same header.
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
import gen_rhodes as gr        # noqa: E402  (shared helpers: biquad, rom_safe, area_resample, contact_segments)
import hammer as hm            # noqa: E402
import reed_bar as rb          # noqa: E402
import rh_coefs as rc          # noqa: E402

OUT = os.path.join(ROOT, "firmware", "gen", "reed_tables.h")
PINS = os.path.join(ROOT, "docs", "model", "data", "reed_pins.json")
GEN_VERSION = 1
FS = 44100.0
KEY_LO, KEY_HI = rb.KEY_LO, rb.KEY_HI
NKEY = KEY_HI - KEY_LO + 1
NSLOT = 4                       # rh_key_t mode slots (RH_NM in the engine)
NMODE = 3                       # reed modes 1..3 present; slot 4 absent (mode 4 is ~45 x f1 and barely struck)
PLEN = 64
PU_N = 256                      # pickup knots - 1 over s = 0 .. S_MAX
PU_SH = 55 - 8                  # 55 - log2(PU_N)
S_MAX = 32.0                    # unit^2 (2^55 in Q50)
VA = [0, 32, 64, 96, 128]       # the engine's velocity anchors (v >> 5)
ROM_LO, ROM_HI = gr.ROM_LO, gr.ROM_HI
PHI_BIAS = 1 << 28
Q30, Q28, Q25, Q16, Q12 = 1 << 30, 1 << 28, 1 << 25, 1 << 16, 1 << 12
M_LIM = (1 << 23) - 1           # |m| bound of the pickup loop
X_MAX = 7.0                     # units: the tip's peak at the BARK cap (the mode states saturate at 8)
OFF_MAX = 1.0                   # units: TONE's cap on the rest height (the plate's edge)
LEVELMAP = os.path.join(ROOT, "docs", "model", "data", "reed_levelmap.json")   # per key: the soft anchors' gain (tools/reed_levelmap.py)
BUDGET = 17 * 1024              # bytes: the interface's cap for all WHURLI data

# Every number the model uses; ESTIMATE marks the ones no source fixes (private/plans/whurli-physics.md, to verify).
P = {
    "slot_gap_mm": 0.21,        # the reed's lateral clearance in its slot: published slot spacings - reed widths
    "plate_t_mm": 1.0,          # ESTIMATE: pickup plate (sheet) thickness the reed's edge sees
    "ears_t_mm": 2.0,           # ESTIMATE: the bass slots' upturned ears (reeds 1..20) as a taller plate edge
    "table_geom": "ground",     # the cross-section the one pickup table is solved for (44 of the 64 reeds)
    "off_u": 0.42,              # ESTIMATE (voicing): the rest height over the plate's mid-plane, edge half-heights
    "hammer": {"m_g": [[33, 2.6], [96, 1.4]],        # ESTIMATE: effective hammer mass at the reed (g)
               "p": 2.5, "alpha": 0.3,               # felt law exponent (piano felt 2.2..3.5), Hunt-Crossley loss s/m
               "tc_ms": [[33, 1.3], [96, 0.55]],     # ESTIMATE: rigid-wall contact time at 1 m/s (ms)
               "v_max": 2.5, "range_db": 30.0},      # ESTIMATE: hammer speed at v = 127 (m/s) and its range
    # mode-1 T60 targets (s): the reed-bar loss is what is left after the thermoelastic and air losses
    "t60": [[33, 13.0], [45, 9.0], [57, 6.0], [69, 3.6], [81, 1.9], [93, 0.85], [96, 0.7]],
    "t60_rel": [[33, 0.40], [45, 0.25], [57, 0.15], [69, 0.10], [81, 0.08], [96, 0.06]],   # felt damper (s)
    "load_fc": 2500.0,          # pickup ~240 pF against ~200 kOhm (bias feed || input bias): the corner (Hz)
    "spk_hp": [140.0, 0.8],     # small oval speakers in an open lid: the bass roll-off (Hz, Q)
    "spk_peak": [1200.0, 1.0, 4.0],   # the cones' midrange lift (Hz, Q, dB)
    "spk_lp": 6000.0,           # the cones' top roll-off (Hz), one real pole in RD_RLC with the load's
    "gain_target_dbfs": -32.0,  # v = 96 anchor + 4, RMS 50..300 ms: the tine piano's calibration level
    "peak_dbfs": -6.0,          # one voice at v = 127: 90 % of the keys peak under this + 3 dB
    "groups": [33, 47, 53, 75],  # pulse groups: the reed groups' first keys
    "bark_max": 2.0,            # BARK's cap on the swing scale (x the table)
}

STEEL = dict(E=200e9, rho=7850.0, alpha=12e-6, k=50.0, cp=460.0, T0=295.0)
AIR = dict(rho=1.2, mu=1.8e-5)


def interp_anchor(tab, key):
    xs, ys = zip(*tab)
    return float(np.interp(key, xs, ys))


def interp_log(tab, key):
    xs, ys = zip(*tab)
    return float(np.exp(np.interp(key, xs, np.log(ys))))


def group_of(key):
    return max(i for i, k in enumerate(P["groups"]) if key >= k)


def unit_mm(key):
    """the key's length unit: the edge half-height (plate edge + reed tip thickness) / 2 (mm); the bass slots' ears
    make their edge taller (the one table's shape is the plain plate's: the bass keys use it scaled)"""
    t = P["ears_t_mm"] if key - KEY_LO + 1 <= 20 else P["plate_t_mm"]
    return 0.5 * (t + rb.geometry(key)[3] * 1e3)


# ---------------------------------------------------------------- pins (solver results, portable)
def _r(x, d=10):
    return float("%.*g" % (d, x))


GEOMS = {"flat": 33, "ground": 69}     # the two cross-sections: a flat bass reed, a ground reed's tongue


def pin_inputs():
    return dict(clamp=rb.CLAMP, strike=rb.STRIKE, taper=rb.TAPER, nel=rb.NEL, gap=P["slot_gap_mm"],
                plate=P["plate_t_mm"], E=rb.E_STEEL, rho=rb.RHO_STEEL, zmax=8.0)


def compute_pins():
    import reed_pickup as rp
    reeds = {}
    for k in range(KEY_LO, KEY_HI + 1):
        r = rb.reed(k)
        reeds[str(k)] = {q: (_r(v) if isinstance(v, float) else [_r(x) for x in v] if isinstance(v, list) else v)
                         for q, v in r.items()}
    zs = [round(float(z), 4) for z in list(np.arange(0, 2.0001, 0.05)) + list(np.arange(2.25, 8.0001, 0.25))]
    caps = {}
    for name, k in GEOMS.items():
        _, w, _, ht = rb.geometry(k)
        g = dict(w=w * 1e3, h=ht * 1e3, S=w * 1e3 + 2 * P["slot_gap_mm"], t=P["plate_t_mm"])
        c = rp.cap_curve(g["w"], g["h"], g["S"], g["t"], zs, box=(16.0, 24.0), dx=0.025)
        caps[name] = dict(geom={q: _r(v) for q, v in g.items()}, z_mm=zs, c_pF_per_m=[_r(x * 1e12, 8) for x in c])
    return dict(reeds=reeds, caps=caps, inputs=pin_inputs())


def load_pins():
    if not os.path.exists(PINS):
        return None
    d = json.load(open(PINS))
    return d if d.get("inputs") == json.loads(json.dumps(pin_inputs())) else None


# ---------------------------------------------------------------- losses
def q_ted(h, f):
    """Zener thermoelastic damping of a steel bar of thickness h (m) at f (Hz): 1/Q."""
    s = STEEL
    delta = s["E"] * s["alpha"] ** 2 * s["T0"] / (s["rho"] * s["cp"])
    tau = h * h / (math.pi ** 2 * s["k"] / (s["rho"] * s["cp"]))
    wt = 2 * math.pi * f * tau
    return delta * wt / (1 + wt * wt)


def q_air(w, h, f, mass_ratio):
    """viscous air damping of a ribbon of width w moving flatwise (1/Q): the oscillating-cylinder drag at the
    frequency Reynolds number Re = rho w^2 omega / (4 mu), Gamma_i ~ 2 sqrt(2 / Re) (large Re), over the bar's mass
    per length; mass_ratio = the mode's modal mass over the bar's share (the tip mass lowers the loss)."""
    om = 2 * math.pi * f
    re = AIR["rho"] * w * w * om / (4 * AIR["mu"])
    gi = 2 * math.sqrt(2) / math.sqrt(re)
    return (math.pi * AIR["rho"] * w * w / 4 * gi) / (STEEL["rho"] * w * h) / mass_ratio


def mode_losses(r, key):
    """per mode: f, t60, t60_rel and the Q parts. The support Q comes from the mode-1 target and is the same on
    the higher modes; their damped T60 shrinks with the mode's period."""
    t1 = interp_log(P["t60"], key)
    trel = interp_log(P["t60_rel"], key)
    h = 0.5 * (r["hb"] + r["ht"])
    mr = 1.0 + r["m_tip"] / (0.25 * r["m_bar"])
    out, q_sup = [], None
    for m in range(NMODE):
        f = r["f"][m]
        qi_t, qi_a = q_ted(h, f), q_air(r["w"], h, f, mr if m == 0 else 1.0)
        if m == 0:
            qi_s = 6.907755 / (math.pi * f * t1) - qi_t - qi_a
            assert qi_s > 0, (key, "air + thermoelastic alone decay faster than the target")
            q_sup = 1 / qi_s
        qi = qi_t + qi_a + 1 / q_sup
        t60 = 6.907755 / (math.pi * f * qi)
        out.append(dict(f=f, t60=t60, t60_rel=min(t60, trel * (r["f"][0] / f) ** 0.5),
                        Q=1 / qi, Q_ted=1 / qi_t, Q_air=1 / qi_a, Q_sup=q_sup))
    return out


# ---------------------------------------------------------------- hammer
def hammer_speed(v):
    h = P["hammer"]
    return h["v_max"] * 2 ** (-h["range_db"] / 6.0206 * (1 - (v - 1) / 126))


def felt_K(key):
    """K (N/m^p): a hammer of the key's mass meets a rigid wall for tc_ms at 1 m/s (Tc ~ K^(-1 / (p + 1)))."""
    h = P["hammer"]
    m = interp_anchor(h["m_g"], key) * 1e-3
    tc = interp_anchor(h["tc_ms"], key) * 1e-3
    return (hm.hertz_tc(1.0, m, 1.0, h["p"]) / tc) ** (h["p"] + 1)


def contact_cases(r, key):
    h = P["hammer"]
    modes = [(r["f"][m], math.pi * r["f"][m] / 400.0, r["phi_strike"][m]) for m in range(3)]
    m_h = interp_anchor(h["m_g"], key) * 1e-3
    K = felt_K(key)
    return [dict(v=hammer_speed(v), m_h=m_h, K=K, p=h["p"], alpha=h["alpha"], modes=modes) for v in VA]


def pulses(reeds):
    keys = sorted(reeds)
    cases = []
    for k in keys:
        cases += contact_cases(reeds[k], k)
    res = hm.contact_batch(cases, fs=FS, t_max=0.03)
    out = {}
    na = len(VA)
    for i, k in enumerate(keys):
        sims = res[i * na:(i + 1) * na]
        seg = [gr.contact_segments(s["F"]) for s in sims]
        ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg))
        win = [ratio * sg[0][1] for sg in seg]             # samples of the 16x grid, the same multiple per key
        shapes = []
        for s, wn in zip(sims, win):
            nn = max(2, int(round(wn)))
            F = np.concatenate([s["F"], np.zeros(max(0, nn - len(s["F"])))])[:nn]
            shapes.append(gr.area_resample(F))
        dt = sims[0]["dt"]
        fmax = max(s["F"].max() for s in sims)
        prate = [int(round(64.0 / (wn * dt * FS) * Q12)) for wn in win]
        pamp = [max(1, int(round(s["F"].max() / fmax * 32767))) for s in sims]
        r = reeds[k]
        amp = [[abs(r["phi_tip"][m]) * math.hypot(s["q"][m], s["qd"][m] / (2 * math.pi * r["f"][m])) * 1e3
                for m in range(NMODE)] for s in sims]      # tip amplitude per mode after the contact (mm)
        out[k] = dict(shapes=shapes, prate=prate, pamp=pamp, fmax=float(fmax), tip_mm=amp,
                      tc_ms=[sg[0][1] * dt * 1e3 for sg in seg], ncont=[len(sg) for sg in seg],
                      K=cases[i * na]["K"])
    return out


# ---------------------------------------------------------------- pickup table
def cap_fn(cap):
    """C(s) - C(far) as a smooth monotone function of s = u^2, u in the cross-section's edge half-heights (PCHIP)."""
    from scipy.interpolate import PchipInterpolator
    e = 0.5 * (cap["geom"]["t"] + cap["geom"]["h"])
    z = np.array(cap["z_mm"]) / e
    c = np.array(cap["c_pF_per_m"])
    return PchipInterpolator(z * z, c - c[-1], extrapolate=False), float((z[-1]) ** 2)


def pickup_table(cap):
    """the knots p_i = bias - C~(s_i), m_i = -dC~/di, scaled so max |p - bias| <= 2^28 and max |m| <= 2^23 - 1 (the
    pickup loop's bounds), flat after the last solve (the far tail is ~1/u and nearly flat there)."""
    fn, smax = cap_fn(cap)
    s = np.arange(PU_N + 1) * (S_MAX / PU_N)
    se = np.minimum(s, smax)
    c = fn(se)
    d = np.where(s < smax, fn.derivative()(se), 0.0) * (S_MAX / PU_N)
    sc = min(Q28 / float(np.max(np.abs(c))), M_LIM / float(np.max(np.abs(d))))
    Pk = [PHI_BIAS - int(round(x * sc)) for x in c]
    Mk = [-int(round(x * sc)) for x in d]
    assert not any(ROM_LO <= (v & 0xFFFFFFFF) <= ROM_HI for v in Pk + Mk), "pickup table word in the ROM window"
    assert all(0 <= v < (1 << 29) for v in Pk) and all(abs(v) <= M_LIM for v in Mk)
    return Pk, Mk


def hermite(Pk, Mk, s):
    """C~(s) in table units (sign restored), the cubic Hermite per cell as the engine, clamped at the last knot"""
    s = np.asarray(s, float)
    x = np.clip(s / (S_MAX / PU_N), 0, PU_N - 1e-9)
    i = np.floor(x).astype(int)
    t = x - i
    Pa, Ma = np.asarray(Pk, float), np.asarray(Mk, float)
    p0, p1, m0, m1 = PHI_BIAS - Pa[i], PHI_BIAS - Pa[i + 1], -Ma[i], -Ma[i + 1]
    t2, t3 = t * t, t * t * t
    return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * m1


# ---------------------------------------------------------------- filters
def lp1_pair(f1, f2):
    """two real poles (the load's corner, the speakers' top) as one biquad (bilinear, prewarped, unity at DC)"""
    def lp1(f):
        K = math.tan(math.pi * f / FS)
        return np.array([K, K]) / (1 + K), np.array([1, (K - 1) / (1 + K)])
    (b1, a1), (b2, a2) = lp1(f1), lp1(f2)
    b, a = np.convolve(b1, b2), np.convolve(a1, a2)
    return (b[0], b[1], b[2], a[1], a[2])


def filters():
    rlc = lp1_pair(P["load_fc"], P["spk_lp"])
    body = [gr.biquad("hp2", P["spk_hp"][0], P["spk_hp"][1], 0.0),
            gr.biquad("peak", P["spk_peak"][0], P["spk_peak"][1], P["spk_peak"][2])]
    return rlc, body


def q28_words(c):
    return [gr.rom_safe(int(round(x * Q28))) for x in c]


# ---------------------------------------------------------------- build
def build(repin=False):
    pins = None if repin else load_pins()
    if pins is None:
        if not repin:
            print("reed_pins.json is missing or stale: recomputing (tools/gen_reed.py --repin saves it)",
                  file=sys.stderr)
        pins = compute_pins()
        if repin:
            with open(PINS, "w") as fh:
                json.dump(pins, fh, indent=1, sort_keys=True)
                fh.write("\n")
    reeds = {int(k): v for k, v in pins["reeds"].items()}
    pz = pulses(reeds)
    Pk, Mk = pickup_table(pins["caps"][P["table_geom"]])
    pick = dict(P=Pk, M=Mk)
    import reed_model as rm
    rlc, body = filters()
    gshapes = []
    for g, k0 in enumerate(P["groups"]):
        ks = [k for k in range(KEY_LO, KEY_HI + 1) if group_of(k) == g]
        gshapes.append(pz[ks[len(ks) // 2]]["shapes"])      # the group's middle key's shapes
    keys = []
    for k in range(KEY_LO, KEY_HI + 1):
        r = reeds[k]
        U = unit_mm(k)
        ls = mode_losses(r, k)
        w = [r["phi_strike"][m] * r["phi_tip"][m] / (2 * math.pi * r["f"][m]) for m in range(NMODE)]
        modes = [dict(ls[m], w=w[m] / w[0]) for m in range(NMODE)]
        spec = dict(midi=k, f0=rb.midi_hz(k), grp=group_of(k), off=P["off_u"], unit_mm=U,
                    pulse=dict(shapes=gshapes[group_of(k)], own=pz[k]["shapes"], prate=pz[k]["prate"],
                               pamp=pz[k]["pamp"], pgain=[65535] * len(VA)),
                    tc_ms=pz[k]["tc_ms"], ncont=pz[k]["ncont"], tip_mm=pz[k]["tip_mm"], K=pz[k]["K"],
                    reed=dict(L_mm=r["L"] * 1e3, m_tip_g=r["m_tip"] * 1e3, m_bar_g=r["m_bar"] * 1e3,
                              f2_f1=r["f"][1] / r["f"][0], f3_f1=r["f"][2] / r["f"][0], strike=r["strike"]),
                    modes=modes)
        for m in spec["modes"]:
            m["b"] = m["w"]
        # b: the engine's mode-1 tip swing at the top anchor equals the contact simulation's (in the key's unit)
        pk = rm.mode1_peak(spec, 128)
        sc = pz[k]["tip_mm"][-1][0] / U / pk
        for m in spec["modes"]:
            m["b"] *= sc
        keys.append(spec)
    rom_b(keys)
    # gains: v = 100, RMS 50..300 ms (after the load) -> the calibration level per key; a global cap on v = 127 peaks
    y = rm.render_batch(keys, pick, [100] * NKEY, 0.35, rlc=rlc, gains=[1.0] * NKEY)
    rms = np.array([float(np.sqrt(np.mean(r[2205:13230] ** 2))) + 1e-12 for r in y])
    g = 10 ** (P["gain_target_dbfs"] / 20) * 32767 / rms
    yp = rm.render_batch(keys, pick, [127] * NKEY, 0.25, rlc=rlc, gains=g)
    pk = np.array([float(np.max(np.abs(r))) + 1e-12 for r in yp])
    cap = min(1.0, float(np.percentile(10 ** ((P["peak_dbfs"] + 3) / 20) * 32767 / pk, 10)))
    g = np.minimum(g * cap, (2 ** 31 - 1) / Q16)
    lmap = json.load(open(LEVELMAP))["g32"] if os.path.exists(LEVELMAP) else {}
    for s, gk in zip(keys, g):
        # the soft end's level evenness (docs/model/data/reed_levelmap.json, tools/reed_levelmap.py): the output gain at the anchors
        # v0 / v32 is g32 times the table's (>1: the key's own gain takes the boost, RD_PGAIN the cut at the louder anchors, so
        # v64 and up stay exactly as they were); the pulses, so the bark and the growl, are untouched
        g32 = float(lmap.get(str(s["midi"]), 1.0))
        G = max(1.0, g32)
        gk = float(gk) * G
        s["pulse"]["pgain"] = [max(1, min(65535, int(round(65535 * a / G)))) for a in (g32, g32, 1.0, 1.0, 1.0)]
        s["gain"] = float(gk)
        s["kill"] = rm.kill_level(s, pick, float(gk))
        s["life_blocks"] = int(min(6891, math.ceil((2 * s["modes"][0]["t60"] + 1.0) * FS / 256)))
    # BARK's cap: the swing scale at which the loudest key's tip (all modes, v = 128) stays under X_MAX units, the
    # mode states' Q28 saturation (8 units) with a margin, and never past P["bark_max"]
    tip = rm.modes_batch(keys, [128] * NKEY, int(0.1 * FS))
    xpk = float(np.max(np.abs(tip)))
    bmax = min(P["bark_max"], math.floor(X_MAX / xpk * 64) / 64)
    lim = dict(bark_max=int(round(bmax * Q16)), off_max=gr.rom_safe(int(round(OFF_MAX * Q25))), pamp_max=32767,
               x_peak=xpk)
    return dict(gen_version=GEN_VERSION, params=P, keys=keys, gshapes=gshapes, pick=pick, rlc=rlc, body=body,
                gain_cap_db=20 * math.log10(cap), lim=lim, caps=pins["caps"])


def rom_b(keys):
    """no force gain b (Q30) in the ROM window: the key's b scaled up by the smallest step that clears it, its pulse
    amplitudes down by the same (the products stay), as gen_rhodes.rom_window_b"""
    lo, hi = -(1 << 22), -(3 << 20) - 1
    for s in keys:
        inw = lambda sc: any(lo <= int(round(m["b"] * sc * Q30)) <= hi for m in s["modes"])
        if not inw(1.0):
            continue
        sc = next(1 + i / 4096 for i in range(1, 1366) if not inw(1 + i / 4096))
        for m in s["modes"]:
            m["b"] *= sc
        s["pulse"]["pamp"] = [max(1, int(round(a / sc))) for a in s["pulse"]["pamp"]]


# ---------------------------------------------------------------- emit
def key_row(k):
    ms = []
    for j in range(NSLOT):
        if j < len(k["modes"]):
            m = k["modes"][j]
            c = rc.mode_coefs(m["f"], m["t60"], m["t60_rel"])
            b = int(round(m["b"] * Q30)) if c["th"] else 0
        else:
            c, b = dict(sh=0, ch=0, th=0, d=0, drel=0, cr=0), 0
        assert gr.rom_safe(b) == b, (k["midi"], j, b)
        ms.append("{%d,%d,%d,%d,%d,%d,%d}" % (gr.rom_safe(c["sh"]), gr.rom_safe(c["ch"]), gr.rom_safe(c["th"]),
                                              gr.rom_safe(c["d"]), gr.rom_safe(c["drel"]), gr.rom_safe(c["cr"]), b))
    return "  {{%s}, %d, %d, %d, 0, 0, 0, %d, 0, 0, 0, 0}," % (
        ",".join(ms), gr.rom_safe(int(round(k["off"] * Q25))), int(round(k["gain"] * Q16)),
        gr.rom_safe(int(round(k["kill"] * Q25))), k["life_blocks"])


def emit(spec):
    L = []
    w = L.append
    w("/* SPDX-License-Identifier: GPL-3.0-only */")
    w("/* GENERATED by tools/gen_reed.py - do not edit. WHURLI: struck steel reeds, an electrostatic comb pickup. */")
    w("#ifndef REED_TABLES_H")
    w("#define REED_TABLES_H")
    w("#define RD_GEN_VERSION %d" % GEN_VERSION)
    w("#define RD_KEY_LO %d\n#define RD_NKEY %d\n#define RD_NANCH %d" % (KEY_LO, NKEY, len(VA)))
    w("#define RD_NGRP %d\n#define RD_PU_N %d\n#define RD_PU_SH %d" % (len(P["groups"]), PU_N, PU_SH))
    w("/* per key: reed modes 1..3 (slot 4 absent), off (Q25, edge half-heights), gain (Q16), kill (Q25), life */")
    w("static const rh_key_t RD_KEYS[RD_NKEY] = {")
    for k in spec["keys"]:
        w(key_row(k))
    w("};")
    for name, fld in (("RD_PRATE", "prate"), ("RD_PAMP", "pamp"), ("RD_PGAIN", "pgain")):
        w("static const uint16_t %s[RD_NKEY][RD_NANCH] = {" % name)
        for k in spec["keys"]:
            v = k["pulse"][fld]
            assert all(1 <= x <= 65535 for x in v), (name, k["midi"], v)
            w("  {" + ",".join(map(str, v)) + "},")
        w("};")
    w("static const uint8_t RD_KGRP[RD_NKEY] = {%s};" % ",".join(str(k["grp"]) for k in spec["keys"]))
    w("static const int16_t RD_PULSE[RD_NGRP][RD_NANCH][RH_PLEN + 2] = {")
    for g in spec["gshapes"]:
        w("  {" + ",".join("{" + ",".join(map(str, sh)) + ",0,0}" for sh in g) + "},")
    w("};")
    pk = spec["pick"]
    w("/* the pickup: [2 i] = bias - C~(s_i), [2 i + 1] = -dC~/di; s = u^2, u = (x + off) in edge half-heights */")
    w("static const int32_t RD_PHIM[2 * (RD_PU_N + 1)] = {%s};" % ",".join(
        str(x) for pq in zip(pk["P"], pk["M"]) for x in pq))
    w("/* the input load's corner x the speakers' top; the speakers' band-pass and midrange lift (Q28 DF1) */")
    w("static const int32_t RD_RLC[5] = {%s};" % ",".join(map(str, q28_words(spec["rlc"]))))
    w("static const int32_t RD_BODY[2][5] = {%s};" % ",".join(
        "{" + ",".join(map(str, q28_words(c))) + "}" for c in spec["body"]))
    w("static const rh_type_t RD_TYPE = {0, %d, %d, %d, %d, %d, 0};" % (Q16, Q16, Q16, Q16, len(spec["body"])))
    lm = spec["lim"]
    w("static const rd_lim_t RD_LIM = {%d, %d, %d, 0};" % (lm["bark_max"], lm["off_max"], lm["pamp_max"]))
    w("#endif")
    body = "\n".join(L) + "\n"
    return body + "/* sha256(body) = %s */\n" % hashlib.sha256(body.encode()).hexdigest()


def sizes():
    """flash bytes per table as the compiler lays them out (rh_key_t with 4 mode slots: 140 B)"""
    na, ng = len(VA), len(P["groups"])
    return {"RD_KEYS": NKEY * (NSLOT * 7 * 4 + 5 * 4 + 2 * 2 + 4), "RD_PRATE+PAMP+PGAIN": 3 * NKEY * na * 2,
            "RD_KGRP": NKEY, "RD_PULSE": ng * na * (PLEN + 2) * 2, "RD_PHIM": 2 * (PU_N + 1) * 4,
            "RD_RLC+BODY+TYPE+LIM": 15 * 4 + 28 + 12}


def words(hdr):
    """every integer literal in the header's arrays (for the ROM-window rule)"""
    import re
    body = hdr.split("*/", 2)[-1]
    return [int(x) for x in re.findall(r"(?<![\w.])-?\d+(?![\w.])", re.sub(r"/\*.*?\*/", "", body, flags=re.S))]


def report(spec):
    print("key   f1     f2/f1 f3/f1  tip g  T60 s  T60_2  rel s  Q_sup  tc ms(v64)  swing mm(v32/64/128)  ncont")
    for k in spec["keys"]:
        if k["midi"] in (33, 40, 45, 52, 53, 57, 60, 64, 69, 74, 75, 81, 86, 93, 96):
            print("%3d %7.1f  %5.2f %5.2f  %5.3f  %5.2f  %5.2f  %5.2f  %6.0f  %5.2f   %5.2f %5.2f %5.2f  %s" % (
                k["midi"], k["modes"][0]["f"], k["reed"]["f2_f1"], k["reed"]["f3_f1"], k["reed"]["m_tip_g"],
                k["modes"][0]["t60"], k["modes"][1]["t60"], k["modes"][0]["t60_rel"], k["modes"][0]["Q_sup"],
                k["tc_ms"][2], k["tip_mm"][1][0], k["tip_mm"][2][0], k["tip_mm"][4][0], k["ncont"]))
    print("gain cap %.2f dB; tip peak at v 128 %.2f units; BARK cap %.3f" % (spec["gain_cap_db"], spec["lim"]["x_peak"],
                                                                      spec["lim"]["bark_max"] / Q16))


def main():
    a = sys.argv[1:]
    spec = build(repin="--repin" in a)
    hdr = emit(spec)
    sz = sizes()
    print("WHURLI tables: %d B (%s)" % (sum(sz.values()), ", ".join("%s %d" % kv for kv in sz.items())),
          file=sys.stderr)
    if "--report" in a:
        report(spec)
    if "--check" in a:
        ok = os.path.exists(OUT) and open(OUT).read() == hdr
        print("reed_tables.h", "up to date" if ok else "STALE: run tools/gen_reed.py")
        return 0 if ok else 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write(hdr)
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
