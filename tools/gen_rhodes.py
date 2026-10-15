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


def shore_E(S):
    """Young's modulus (Pa) of a rubber of Shore A hardness S (Gent 1958: E = 0.0981 (56 + 7.62336 S) /
    (0.137505 (254 - 2.54 S)) MPa)."""
    return 0.0981 * (56 + 7.62336 * S) / (0.137505 * (254 - 2.54 * S)) * 1e6


def band_K(p, b):
    """Hertz contact stiffness K (N/m^1.5) of the band's hammer tip on the tine: F = K e^1.5, K = 4/3 E* sqrt(Re),
    E* = E / (1 - nu^2) (rubber nu = 0.5; the steel's compliance is negligible), Re = sqrt(R_tip R_tine) for the
    crossed tip / tine cylinders. Tip hardness and size per band from the service manual (R05 W1/W2): 30/50/70/90
    Shore A neoprene, 1/4" .. 7/16"; the top band is maple wrapped in shrink tube (E_wrap, ESTIMATE)."""
    h = p["hammer"]
    bd = p["bands"]
    S = bd["shore"][b]
    E = shore_E(S) if S else h["E_wrap"]
    R_tip = 0.5 * bd["tip_in"][b] * 0.0254
    Re = math.sqrt(R_tip * 0.5 * fe.D_TINE)
    return 4.0 / 3.0 * E / (1 - 0.25) * math.sqrt(Re)


def hammer_mass(p, k):
    """Effective hammer mass at the tip (kg): the whole hammer (R02, 11 g) where the tine is heavy at the strike line,
    but never more than the tine's own effective mass there (mode 1, 1 / phi^2) / m_eff_ratio. A flexible hammer
    shank lends the tip only the mass within reach of the contact time (the short treble contacts see little
    more than the tip), and a regulated instrument gives one clean strike: with a heavier tip the hammer follows
    the tine and strikes it 2..12 times (the FM-1 scan's holes). m_eff_ratio = 3 is the single-contact bound of a
    two-mass collision (the tine returns faster than the hammer recoils when m_tine < 3 m_hammer)."""
    h = p["hammer"]
    m1 = 1.0 / k["fe_modes"][0][2] ** 2
    return min(h["mass_kg"], m1 / h["m_eff_ratio"])


def contact_cases(p, k, vels):
    h = p["hammer"]
    K = band_K(p, k["band"])
    return [dict(v=hammer_speed(p, v), m_h=hammer_mass(p, k), K=K, p=h["p"], alpha=h["alpha"], modes=k["fe_modes"])
            for v in vels]


V_ANCH = [1, 8, 24, 40, 56, 72, 88, 104, 120, 127]   # velocity anchors of the per-key amplitude / rate laws


def area_resample(F, n=PLEN):
    """n samples spanning the contact, each the mean of F over its cell (the impulse is kept, spikes are not
    point-sampled away), peak-normalised to 32767."""
    m = len(F)
    if m < 2:
        F = np.array([F[0] if m else 0.0, 0.0]); m = 2
    C = np.concatenate([[0.0], np.cumsum(F)])            # integral at cell edges 0..m
    x = np.linspace(0, m - 1, n)                          # sample centres in F's index space
    lo = np.clip(x - 0.5 * (m - 1) / (n - 1), -0.5, m - 0.5) + 0.5
    hi = np.clip(x + 0.5 * (m - 1) / (n - 1), -0.5, m - 0.5) + 0.5
    I = lambda t: np.interp(t, np.arange(m + 1), C)
    sh = (I(hi) - I(lo)) / np.maximum(hi - lo, 1e-12)
    return [int(round(q * 32767 / max(sh.max(), 1e-30))) for q in sh]


def key_pulses_all(p, phys):
    """Every key's own coupled hammer-tine contact (§6.6; one batch over keys x velocity anchors): the force shape
    at the 8 layer velocities (64 points over the contact, peak 32767), and at every anchor the pulse rate (Q12
    samples of the shape per output sample) and the peak force (Q15 re the key's strongest anchor). Ints, exactly
    what the C tables hold. The hammer is its effective mass at the tip on a Hertz rubber tip of the key's band."""
    keys = sorted(phys)
    cases = []
    for n in keys:
        cases += contact_cases(p, phys[n], V_ANCH)
    res = hm.contact_batch(cases, fs=FS, t_max=0.03)
    out = {}
    for i, n in enumerate(keys):
        sims = dict(zip(V_ANCH, res[i * len(V_ANCH):(i + 1) * len(V_ANCH)]))
        # the pulse window: a light hammer can be caught again by the returning tine half a period later (a second,
        # weaker contact that comes and goes with velocity). The window is a fixed multiple of the first contact,
        # the same multiple at every velocity of the key, so the shape's time base stays continuous in velocity
        seg = {v: contact_segments(sims[v]["F"]) for v in V_ANCH}
        ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg.values()))
        win = {v: ratio * seg[v][0][1] for v in V_ANCH}            # samples of the 16x grid
        shapes = []
        for v in LAYER_V:
            F = sims[v]["F"]
            nn = max(2, int(round(win[v])))
            F = np.concatenate([F, np.zeros(max(0, nn - len(F)))])[:nn]
            shapes.append(area_resample(F))
        dt = sims[V_ANCH[0]]["dt"]
        fmax = max(sims[v]["F"].max() for v in V_ANCH)
        prate = [int(round(64.0 / (win[v] * dt * FS) * Q12)) for v in V_ANCH]
        pamp = [max(1, int(round(sims[v]["F"].max() / fmax * 32767))) for v in V_ANCH]
        assert all(0 < q < 2 ** 31 for q in prate), prate
        out[n] = dict(shapes=shapes, prate=prate, pamp=pamp, pamp_phys=list(pamp), K=band_K(p, phys[n]["band"]),
                      tc_ms=[seg[v][0][1] * dt * 1e3 for v in V_ANCH], win_ms=[win[v] * dt * 1e3 for v in V_ANCH],
                      ncont=[len(seg[v]) for v in V_ANCH])
    return out


def contact_segments(F, min_frac=0.005):
    """[(start, end)] sample ranges (16x grid) of the separate strikes carrying >= min_frac of the impulse."""
    on = F > 0
    e = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    segs = [(int(e[j]), int(e[j + 1])) for j in range(0, len(e), 2)]
    merged = []                                   # a gap shorter than half the contact so far is one strike whose
    for sg in segs:                               # face chatters on the tine's high modes, not a second strike
        if merged and sg[0] - merged[-1][1] < 0.5 * (merged[-1][1] - merged[-1][0]):
            merged[-1] = (merged[-1][0], sg[1])
        else:
            merged.append(sg)
    tot = float(F.sum())
    keep = [sg for sg in merged if F[sg[0]:sg[1]].sum() >= min_frac * tot]
    return keep or merged[:1]


def h2_over_h1(y, f0, t0=0.03, t1=0.30):
    """H2/H1 (dB) of a rendered note over [t0, t1] (zero-padded Hann FFT, +-3 % bands)."""
    seg = y[int(t0 * FS):int(t1 * FS)]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18)) ** 2
    fr = np.fft.rfftfreq(1 << 18, 1 / FS)
    pw = lambda f: float(np.sum(S[(fr >= 0.97 * f) & (fr <= 1.03 * f)]))
    return 10 * math.log10(max(pw(2 * f0), 1e-30) / max(pw(f0), 1e-30))


def voicing_h2_target(p, n):
    pts = p["voicing"]["h2_v100_points"]
    return float(np.interp(n, [q[0] for q in pts], [q[1] for q in pts]))


def voice_pickups(p, spec_keys, fams):
    """Voicing (what a technician does with the pickup distance, R05 W3 / W6): the tip's swing against the gap sets
    the bark, so each key's tip scale (all its b) is set so that a v = 100 strike has the H2/H1 of
    params voicing.h2_v100_points (mostly fundamental at mf; the bark then grows with the swing toward v = 127).
    H2/H1 grows ~1 dB per dB of tip swing at these levels, so a few multiplicative steps converge."""
    import rhodes_model as gm
    for it in range(5):
        y = gm.render_batch(spec_keys, fams, [100] * len(spec_keys), 0.35)
        err = []
        for spec, r in zip(spec_keys, y):
            e = voicing_h2_target(p, spec["midi"]) - h2_over_h1(r, spec["f0"])
            err.append(e)
            s = 10 ** (float(np.clip(e, -12, 12)) / 20)
            bmax = max(abs(m["b"]) for m in spec["modes"]) * s
            if bmax * Q30 >= 2 ** 31 - 1:
                s *= (2 ** 31 - 2) / (bmax * Q30)
            for m in spec["modes"]:
                m["b"] *= s
        print("voicing %d: H2/H1 at v100, worst error %.2f dB" % (it, float(np.max(np.abs(err)))), file=sys.stderr)


def vlayers():
    """Per MIDI velocity: the pulse layer below it and the Q15 weight of the next layer (the engine crossfades the
    two shapes: a nearest-layer switch made steps of up to 1.4 dB in the level against velocity)."""
    li, lw = [0] * 128, [0] * 128
    for v in range(128):
        if v >= LAYER_V[-1]:
            li[v], lw[v] = len(LAYER_V) - 1, 0
        elif v > LAYER_V[0]:
            j = max(a for a in range(len(LAYER_V)) if LAYER_V[a] <= v)
            li[v] = j
            lw[v] = int(round(32768 * (v - LAYER_V[j]) / (LAYER_V[j + 1] - LAYER_V[j])))
    return li, lw


def vpos():
    """Per MIDI velocity: the anchor index below it and the Q8 fraction toward the next (shared by C and model)."""
    idx, frac = [0] * 128, [0] * 128
    for v in range(1, 128):
        i = max(a for a in range(len(V_ANCH) - 1) if V_ANCH[a] <= v)
        idx[v] = i
        frac[v] = int(round(256 * (v - V_ANCH[i]) / (V_ANCH[i + 1] - V_ANCH[i])))
    return idx, frac


# ---------------------------------------------------------------- coefficients
from rh_coefs import mode_coefs  # noqa: E402  (shared with the golden model)


def biquad(kind, f0, q, gain_db):
    """RBJ cookbook biquad, normalised (b0, b1, b2, a1, a2)."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / FS
    c, al = math.cos(w0), math.sin(w0) / (2 * q)
    if kind == "lp2":       # prewarped bilinear, as before (bit-identical for the STAGE 77 load)
        K = math.tan(math.pi * f0 / FS)
        nrm = 1 / (1 + K / q + K * K)
        b0 = K * K * nrm
        return (b0, 2 * b0, b0, 2 * (K * K - 1) * nrm, (1 - K / q + K * K) * nrm)
    if kind == "hp2":
        b, a = [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al]
    elif kind == "peak":
        b, a = [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
    elif kind in ("lshelf", "hshelf"):
        sq = 2 * math.sqrt(A) * al
        if kind == "lshelf":
            b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
            a = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
        else:
            b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
            a = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
    else:
        raise ValueError(kind)
    return (b[0] / a[0], b[1] / a[0], b[2] / a[0], a[1] / a[0], a[2] / a[0])


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
    pulses = key_pulses_all(p, phys)
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
        pz = pulses[n]
        spec = dict(midi=n, f0=k["f0"], band=b, fam=k["fam"], off=k["off"], pulse=pz,
                    tc_ms=float(np.interp(100, V_ANCH, pz["tc_ms"])),
                    modes=[dict(name=m["name"], f=m["f"], t60=m["t60"], t60_rel=m["t60_rel"], w=m["amp"])
                           for m in k["modes"]],
                    ratios=k["ratios"], spring_g=k["spring_g"], L=k["L"])
        # unit b = mode amplitude per unit force-sample; scale so V1 peaks at target_peak (gap) at v = 127
        w1 = spec["modes"][0]["w"]
        for m in spec["modes"]:
            m["b"] = m["w"] / abs(w1) if w1 else 0.0
        pk = gm.v1_peak(spec, 127)
        scale = target_peak / pk
        bmax = max(abs(m["b"]) for m in spec["modes"]) * scale
        if bmax * Q30 >= 2 ** 31 - 1:                  # extremely short treble contacts: keep b in int32
            scale *= (2 ** 31 - 2) / (bmax * Q30)
        for m in spec["modes"]:
            m["b"] *= scale
        spec_keys.append(spec)
    voice_pickups(p, spec_keys, fams)
    # velocity calibration (FM-1 scan, 0.6): on some keys the hammer's contact at middle velocities partly cancels
    # the tine's fundamental, so their level against velocity had holes of up to 20 dB next to normal keys. Each
    # key's force amplitude per velocity anchor is fitted so its level curve (re the v = 104 anchor) follows the
    # median curve of all keys (made monotonic); the physics still shapes the timbre, the level is made even.
    def anchor_levels():
        out = np.zeros((len(spec_keys), len(V_ANCH)))
        for a, v in enumerate(V_ANCH):
            yv = gm.render_batch(spec_keys, fams, [v] * len(spec_keys), 0.5)
            out[:, a] = [20 * np.log10(np.sqrt(np.mean(r[2205:13230] ** 2)) + 1e-9) for r in yv]
        return out
    ref = V_ANCH.index(104)
    vt = p.get("velocity_trim", {})
    for it in range(int(vt.get("iterations", 4))):
        Lv = anchor_levels()
        rel = Lv - Lv[:, ref:ref + 1]
        tgt = np.median(rel, axis=0)
        tgt = np.maximum.accumulate(tgt)                    # louder with velocity, never less
        corr = np.clip(tgt[None, :] - rel, -vt.get("max_db", 24.0), vt.get("max_db", 24.0))
        corr[:, ref] = 0
        print("velocity fit %d: worst %.1f dB, median |err| %.2f dB" % (it, np.abs(corr).max(), np.median(np.abs(corr))),
              file=sys.stderr)
        for k, spec in zip(range(len(spec_keys)), spec_keys):
            pa = spec["pulse"]["pamp"]
            for a in range(len(V_ANCH)):
                pa[a] = int(min(32767, max(8, round(pa[a] * 10 ** (vt.get("gain", 0.85) * corr[k, a] / 20)))))
    # gain: v = 100, RMS of 50..300 ms (what is heard as the note's level) -> target dBFS, per key exactly: the
    # contact time against the tine's period makes the raw output of neighbouring keys differ by up to 18 dB, and
    # a +-3-key smoothing kept those holes (FM-1 scan, 0.6); kill: output below 1 LSB, x2 margin
    y = gm.render_batch(spec_keys, fams, [100] * len(spec_keys), 1.0)
    rms = [float(np.sqrt(np.mean(r[2205:13230] ** 2))) + 1e-12 for r in y]
    target = 10 ** (p["gain_target_dbfs"] / 20) * 32767
    g_raw = np.array([target / r for r in rms])
    # headroom: one voice struck at v = 127 must peak at or below peak_dbfs (the bark spike of the attack)
    yp = gm.render_batch(spec_keys, fams, [127] * len(spec_keys), 0.25)
    pk = np.array([float(np.max(np.abs(r))) + 1e-12 for r in yp])
    g_cap = 10 ** (p.get("peak_dbfs", -9.0) / 20) * 32767 / pk
    # a global cap (never per key: that undid the level calibration, FM-1 scan 0.6): 90 % of the keys' v = 127
    # peaks stay under peak_dbfs + 3 dB; it only binds if gain_target_dbfs is set too hot for the headroom
    cap = min(1.0, float(np.percentile(g_cap * 10 ** (3 / 20) / g_raw, 10)))
    print("gain: target %.1f dBFS, global peak cap %.2f dB" % (p["gain_target_dbfs"], 20 * math.log10(cap)), file=sys.stderr)
    g_s = g_raw * cap
    g_s = np.minimum(g_s, (2 ** 31 - 1) / Q16)         # Q16 in int32
    for spec, g in zip(spec_keys, g_s):
        spec["gain"] = float(g)
        spec["kill"] = gm.kill_level(spec, fams, g)
        spec["life_blocks"] = int(min(6891, math.ceil((2 * spec["modes"][0]["t60"] + 1.0) * FS / 256)))
    idx, frac = vpos()
    spec = dict(gen_version=GEN_VERSION, fs=FS, params=p, keys=spec_keys, v_anchors=V_ANCH,
                vpos_idx=idx, vpos_frac=frac, layer_v=LAYER_V, pickup=fams)
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
        prate = min(65535, k["pulse"]["prate"][V_ANCH.index(104)])   # informational only (per-key tables rule)
        rows.append("    {{%s}, %d, %d, %d, 0, 0, %d, %d, %d, %d, 0xFF, 0}," % (
            ",".join(ms), rom_safe(int(round(k["off"] * Q25))), int(round(k["gain"] * Q16)),
            int(round(k["kill"] * Q25)), prate, k["life_blocks"], k["band"], k["fam"]))
    w("static const rh_key_t RH_KEYS[RH_NKEY] = {")
    L.extend(rows)
    w("};")
    w("static const int16_t RH_PULSE[RH_NKEY][RH_NLAYER][RH_PLEN + 2] = {")
    for k in spec["keys"]:
        w("  {" + ",".join("{" + ",".join(map(str, sh)) + ",0,0}" for sh in k["pulse"]["shapes"]) + "},")
    w("};")
    w(f"#define RH_NANCH {len(V_ANCH)}")
    w("static const uint32_t RH_PRATE[RH_NKEY][RH_NANCH] = {")
    for k in spec["keys"]:
        w("  {" + ",".join(map(str, k["pulse"]["prate"])) + "},")
    w("};")
    w("static const uint16_t RH_PAMP[RH_NKEY][RH_NANCH] = {")
    for k in spec["keys"]:
        w("  {" + ",".join(map(str, k["pulse"]["pamp"])) + "},")
    w("};")
    w("static const uint8_t RH_VPOS_I[128] = {" + ",".join(map(str, spec["vpos_idx"])) + "};")
    w("static const uint8_t RH_VPOS_F[128] = {" + ",".join(str(min(255, f)) for f in spec["vpos_frac"]) + "};")
    li, lw = vlayers()
    w("static const uint8_t RH_VLAY_I[128] = {" + ",".join(map(str, li)) + "};")
    w("static const uint16_t RH_VLAY_W[128] = {" + ",".join(map(str, lw)) + "};")
    w("static const int32_t RH_PHI[2][RH_PU_N + 1] = {")
    for P, M, A in fam_tabs:
        w("  {" + ",".join(map(str, P)) + "},")
    w("};")
    w("static const int32_t RH_SLOPE[2][RH_PU_N + 1] = {")
    for P, M, A in fam_tabs:
        w("  {" + ",".join(map(str, M)) + "},")
    w("};")
    # per TYPE (physical voicing, docs/model/data/params.json "types"): the pickup load (RLC low-pass), then the
    # instrument's amp / speaker "body" (up to 2 biquads, identity when unused); all Q28 DF1 (b0 b1 b2 a1 a2)
    ty = p["types"]
    names = ("STAGE77", "SUITCASE", "FELT", "DYNO")
    q28 = lambda c: "{" + ",".join(str(rom_safe(int(round(x * Q28)))) for x in c) + "}"
    w("static const int32_t RH_RLC[4][5] = {")
    for name in names:
        f0, q = ty[name]["rlc"]
        w("  " + q28(biquad("lp2", f0, q, 0.0)) + ",")
    w("};")
    w("static const int32_t RH_BODY[4][2][5] = {")
    for name in names:
        bq = [biquad(*b) for b in ty[name]["body"]] + [(1.0, 0.0, 0.0, 0.0, 0.0)] * 2
        w("  {" + ",".join(q28(c) for c in bq[:2]) + "},")
    w("};")
    w("static const rh_type_t RH_TYPE[4] = {   /* off (Q25 gap), bark, level/bark, bell, ham (Q16), body biquads, drive row */")
    for i, name in enumerate(names):
        t = ty[name]
        w("  {%d, %d, %d, %d, %d, %d, %d}," % (int(round(t["off"] * Q25)), int(round(t["bark"] * Q16)),
                                               int(round(Q16 * 10 ** (t.get("level_db", 0.0) / 20) / t["bark"])), int(round(t["bell"] * Q16)),
                                               int(round(t["ham"] * Q16)), len(t["body"]), t["drive_row"]))
    w("};")
    w("static const int32_t TUNE_T[101] = {" + ",".join(
        str(rom_safe(int(round((2 ** (c / 1200) - 1) * Q30)))) for c in range(-50, 51)) + "};")
    w("static const uint32_t DECAY_K[128] = {" + ",".join(
        str(int(round(Q16 / 2 ** ((v - 64) / 32)))) for v in range(128)) + "};")
    w("static const uint16_t PEDAL_W[128] = {" + ",".join(str(x) for x in pedal_w()) + "};")
    w("/* hammer bands (Hertz tip K, N/m^1.5): " + ", ".join("%s %.3g" % (nm, band_K(p, b)) for b, nm in enumerate(p["bands"]["names"])) + " */")
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
    for n in (28, 60, 84, 100):
        print("key %d contact ms at v=%s: %s" % (n, V_ANCH, " ".join("%.2f" % t for t in k[n]["pulse"]["tc_ms"])))


if __name__ == "__main__":
    main()
