# SPDX-License-Identifier: GPL-3.0-only
"""The float golden model of the VIBES bar voice (VIBES PLAN §4-§7, P1 step 5; Gate VA on it, Gate VB = bars.c vs it).

It has exactly the structure of bars.c: 32-frame sub-blocks; the mallet pulse blended once at note-on from the
tables (two families x two velocity layers, integer arithmetic as the C), played with linear interpolation; slot 0
(V1) and slot 1 (TUBE) stepped sample by sample as a two-way coupled pair in the bars.c operation order, the
couplings and the tube's radiation weight ramped per sample from the motor's per-sub-block gains; slots 2..7 (V2 V3
V4 T1 T2 X) as the existing mode-outer MC3 loop; the damper as the 7-sub-block crossfade of (E, eps); the absolute
and relative sleep rules and the kill; the per-key output gain. Arithmetic is double precision on the states, with
every coefficient taken from the SAME integer words the C uses (rh_coefs.coef for (E, eps), the Q30 couplings, the
Q28 wt, the Q30 b after the strike-position and torsion scaling, the Q15 pulse samples and amplitude), so the C engine
matches it to its own rounding (residual <= -60 dB, Gate VB).

    spec = load_spec()                                            # docs/model/data/bars_spec.json (gen_bars.py)
    y = render_batch(spec, keys, vels, dur, knobs=None, off_at=None, motor=None, parts=False, slots=False)
        keys: MIDI notes; vels: 1..127; knobs: dict (or one per note) with tone bark bell hammer decay release tune
        (raw 0..127 as the panel sends them, tune in cents -50..50; kf: a KN_FAM word in place of bark's, for the
        generator's calibration family); off_at: key-up time (s) or None (pedal down);
        motor: dict(rate=0..127, depth=0..127, shape=0..127) or None. Output: Q15-unit float samples per note
        ([n_notes, n]); parts=True returns dict(out, bar, tube); slots=True also the per-slot x traces (output units).

Per-sample pair (bars.c, §4.5 with the coupling sign of tools/bartube.py: a symmetric spring):
    y0 += F b0 >> 20;  y0 += k_tb x1 >> 30;  MC3 slot 0;  y1 += k_bt x0 >> 30;  MC3 slot 1;
    rh_x = x0 + (wt x1 >> 28) + sum of the other live slots;  out = rh_x gain >> 24 (Q20), then (o + 16) >> 5 (Q15).
NOISE (contact noise, the damper knock and the felt noise) is not modelled: the golden comparisons run with NOISE 0.
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bartube as btm  # noqa: E402
import rh_coefs as rc  # noqa: E402

FS = 44100.0
SUB = 32
BLOCK = 256
RAMP_SUB = 7
PLEN = 64
NM = 8
S_V1, S_TUBE, S_V2, S_V3, S_V4, S_T1, S_T2, S_X = range(8)
SPEC_PATH = os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")
DEFAULT_KNOBS = dict(tone=64, bark=64, bell=64, hammer=64, decay=64, release=64, tune=0)
LAYER_V = [8, 24, 40, 56, 72, 88, 104, 120]
V_ANCH = [1, 8, 24, 40, 56, 72, 88, 104, 120, 127]


def load_spec(path=SPEC_PATH):
    return json.load(open(path))


# ---------------------------------------------------------------- the note-on arithmetic, as bars.c (integers)
def vlayer(v):
    """(layer below v, Q15 weight of the next) = RH_VLAY_I / RH_VLAY_W (gen_rhodes.vlayers)."""
    if v >= LAYER_V[-1]:
        return len(LAYER_V) - 1, 0
    if v <= LAYER_V[0]:
        return 0, 0
    j = max(a for a in range(len(LAYER_V)) if LAYER_V[a] <= v)
    return j, int(round(32768 * (v - LAYER_V[j]) / (LAYER_V[j + 1] - LAYER_V[j])))


def vpos(v):
    """(anchor below v, Q8 fraction) = RH_VPOS_I / RH_VPOS_F (gen_rhodes.vpos, the fraction clamped to 255)."""
    if v < 1:
        return 0, 0
    i = max(a for a in range(len(V_ANCH) - 1) if V_ANCH[a] <= v)
    return i, min(255, int(round(256 * (v - V_ANCH[i]) / (V_ANCH[i + 1] - V_ANCH[i]))))


def pos_point(i, npos=17):
    """TONE raw of BAR_POS point i (bars.c: q = raw * 16 * 256 / 127, point q >> 8, Q8 fraction q & 255)."""
    return 127.0 * i / (npos - 1)


def pos_weight(P, tone):
    """BAR_POS row interpolation exactly as bar_init: q = tone 4096 / 127, i = q >> 8, f = q & 255,
    w = P[i] + ((P[i+1] - P[i]) f >> 8) (the last point as is)."""
    q = tone * 16 * 256 // 127
    i, fr = q >> 8, q & 255
    if i >= len(P) - 1:
        return list(P[-1])
    return [a + (((b - a) * fr) >> 8) for a, b in zip(P[i], P[i + 1])]


def blend_pulse(spec, band, bark, vel, kf=None):
    """pbuf[66]: the four shapes BAR_PULSE[band][f..f+1][l..l+1] blended as bars.c does: per family the two layers
    (Q15 weight w), then the two families (Q8 weight a)."""
    kf = spec["kn_fam"][bark] if kf is None else kf
    f, a = kf >> 8, kf & 255
    f1 = min(f + 1, len(spec["fam_names"]) - 1)
    l, w = vlayer(vel)
    l1 = min(l + 1, len(LAYER_V) - 1)
    P = spec["pulse"][band]

    def lay(q):
        A, B = P[q][l], P[q][l1]
        return [x + (((y - x) * w) >> 15) for x, y in zip(A, B)] if w else list(A)
    s0 = lay(f)
    if not a:
        return s0
    s1 = lay(f1)
    return [x + (((y - x) * a) >> 8) for x, y in zip(s0, s1)]


def pulse_rate_amp(spec, kidx, bark, hammer, vel, kf=None):
    """(prate Q16 per output sample, pamp Q15) as bar_load_pulse: anchors (RH_VPOS), families (Q8), HAMMER."""
    kf = spec["kn_fam"][bark] if kf is None else kf
    f, a = kf >> 8, kf & 255
    f1 = min(f + 1, len(spec["fam_names"]) - 1)
    i, vf = vpos(vel)

    def anch(tab, q):
        t = tab[kidx][q]
        return t[i] + (((t[i + 1] - t[i]) * vf) >> 8)

    def fam(tab):
        x0 = anch(tab, f)
        return x0 + (((anch(tab, f1) - x0) * a) >> 8) if a else x0
    pr = (fam(spec["prate"]) << 4)
    pr = (pr * spec["kn_mass"][hammer]) >> 12
    pa = (fam(spec["pamp"]) * spec["kn_massi"][hammer]) >> 12
    return pr, min(32767, pa)


def sat32(v):
    return max(-0x7FFFFFFF, min(0x7FFFFFFF, v))


def note_init(spec, n, vel, kn):
    """Everything bar_init computes for one note: per slot (E, eps) held and felt (ints), b (int Q30), live; the
    BELL-scaled couplings; the blended pulse, prate, pamp; gain, kill, life."""
    k = spec["keys"][min(max(n, spec["key_lo"]), spec["key_lo"] + len(spec["keys"]) - 1) - spec["key_lo"]]
    kidx = k["midi"] - spec["key_lo"]
    dk, dr = spec["decay_k"][kn["decay"]], spec["decay_k"][kn["release"]]
    t30 = spec["tune_t"][int(kn["tune"]) + 50]
    pw = pos_weight(spec["bar_pos"][k["band"]], kn["tone"])
    tors = spec["type"]["tors_db"]
    out = dict(E0=[0] * NM, eps0=[0] * NM, E1=[0] * NM, eps1=[0] * NM, b=[0] * NM, live=[False] * NM)
    for m in range(NM):
        w = k["modes"][m]["w"]
        if not w["th"]:
            continue
        k16 = 65536 if m == S_TUBE else dk
        k16r = 65536 if m == S_TUBE else dr
        out["E0"][m], out["eps0"][m] = rc.coef(w, w["d"], t30, k16)
        out["E1"][m], out["eps1"][m] = rc.coef(w, w["drel"], t30, k16r)
        b = (k["modes"][m]["b"] * pw[m]) >> 14
        if m in (S_T1, S_T2):
            b = (b * tors) >> 15
            if kn["tone"] <= 64:
                b = 0
        out["b"][m] = sat32(b)
        out["live"][m] = True if m == S_V1 else (out["b"][m] != 0)
    s = spec["kn_tube"][kn["bell"]]
    if k["modes"][S_TUBE]["w"]["th"]:
        out["live"][S_TUBE] = s != 0                 # BELL 0: no tube at all
    out["kbt"], out["ktb"], out["wt"] = (k["k_bt"] * s) >> 15, (k["k_tb"] * s) >> 15, (k["wt"] * s) >> 15
    out["pbuf"] = blend_pulse(spec, k["band"], kn["bark"], vel, kn.get("kf")) + [0, 0]
    out["prate"], out["pamp"] = pulse_rate_amp(spec, kidx, kn["bark"], kn["hammer"], vel, kn.get("kf"))
    out.update(gain=card_gain(spec, k, kn), kill=k["kill"], life=k["life"], key=k)
    return out


def point_gain(row, raw):
    """BAR_POSG / BAR_TUBEG row (Q14, 17 points over raw 0..127) at a card value, as bar_init: q = raw 4096 / 127,
    i = q >> 8, g = R[i] + ((R[i+1] - R[i]) (q & 255) >> 8) (the last point as is)."""
    q = int(raw) * 16 * 256 // 127
    i, fr = q >> 8, q & 255
    if i >= len(row) - 1:
        return row[-1]
    return row[i] + (((row[i + 1] - row[i]) * fr) >> 8)


def fam_gain(tab, kf, raw):
    """BAR_POSG / BAR_TUBEG [family][17] of one band at a card value: per family over the points (point_gain), then
    the MALLET blend (KN_FAM: family << 8 | Q8 fraction), as bar_init"""
    f, a = kf >> 8, kf & 255
    if f > len(tab) - 1:
        f, a = len(tab) - 1, 0
    f1 = min(f + 1, len(tab) - 1)
    g0 = point_gain(tab[f], raw)
    return g0 + (((point_gain(tab[f1], raw) - g0) * a) >> 8)


def card_gain(spec, k, kn):
    """bars.c bar_init's output gain: the key's gain x the strike position's (TONE) and the tube's (BELL) level
    compensation of its band and mallet (Q14 tables; none in an older spec)"""
    g = int(k["gain"])
    if "pos_gain" in spec:
        kf = spec["kn_fam"][kn["bark"]] if kn.get("kf") is None else kn["kf"]
        g = (g * fam_gain(spec["pos_gain"][k["band"]], kf, kn["tone"])) >> 14
        g = (g * fam_gain(spec["tube_gain"][k["band"]], kf, kn["bell"])) >> 14
    return min(g, 0x7FFFFFFF)


# ---------------------------------------------------------------- the levels, exactly as bars.c (integers, log2 Q8)
LZERO = -32768


def log2q8(v):
    """bars.c bar_log2_32 / bar_log2_64: msb * 256 + the next 8 bits (a linear mantissa)."""
    v = int(v)
    if v <= 0:
        return LZERO
    p = v.bit_length() - 1
    return p * 256 + (((v >> (p - 8)) if p >= 8 else (v << (8 - p))) & 255)


def exp2q8(l):
    """bars.c bar_exp2: 2^(l / 256), 0 under 1."""
    if l < 0:
        return 0
    i, m = l >> 8, 256 + (l & 255)
    if i >= 30:
        return 0x7FFFFFFF
    return m << (i - 8) if i >= 8 else m >> (8 - i)


def df2_coefs(E, P):
    """(E, eps) Q30 ints -> the two-multiply form's (a1, a2): a1 = 2 - eps - e^2, a2 = 1 - eps."""
    return (2 << 30) - P - ((E * E) >> 30), (1 << 30) - P


def log2_sin(E, P):
    """bars.c bar_init lsin: log2 sin(theta) Q8 from a1, a2 (sin^2 = (a2 2^32 - a1^2) / (a2 2^32))."""
    a1, a2 = df2_coefs(E, P)
    num = (a2 << 32) - a1 * a1
    return (log2q8(num) - log2q8(a2) - 32 * 256) >> 1 if num > 0 else 0


def amp_q25(x1, x2, E, P, lsin):
    """bars.c bar_lamp + bar_exp2: a slot's amplitude (Q25 int) from x[n], x[n-1] (ints)."""
    a1, a2 = df2_coefs(E, P)
    c = (a1 * x2) >> 30
    d = (a2 * x2) >> 30
    q = x1 * (x1 - c) + x2 * d
    if q <= 0:
        return 0
    return exp2q8((log2q8(q) >> 1) - lsin)


# ---------------------------------------------------------------- the render
def _knobs_list(knobs, n):
    if knobs is None:
        return [dict(DEFAULT_KNOBS)] * n
    if isinstance(knobs, dict):
        kn = dict(DEFAULT_KNOBS)
        kn.update(knobs)
        return [kn] * n
    out = []
    for k in knobs:
        kn = dict(DEFAULT_KNOBS)
        kn.update(k)
        out.append(kn)
    return out


def render_batch(spec, keys, vels, dur, knobs=None, off_at=None, motor=None, parts=False, slots=False,
                 sleep_rel=None, sleep=True, pedal_w=32768, peaks=False):
    """See the module docstring. sleep_rel: the relative-sleep shift (None = the type's, 0 = off); sleep: at every
    block's end, the voice ends under 1 LSB out and an upper slot sleeps under max(1/16 LSB, ref >> sleep_rel),
    faded out over the next sub-block, levels from bars.c's estimator (amp_q25); off = the pure linear model, for VA
    measurements; peaks=True returns
    (out, the per-note per-slot max |x|, |y| over the render in real state units, 1.0 = 2^25)."""
    N = len(keys)
    n = int(round(dur * FS))
    kns = _knobs_list(knobs, N)
    ini = [note_init(spec, k, v, kn) for k, v, kn in zip(keys, vels, kns)]
    q30 = float(1 << 30)
    E = np.array([[x / q30 for x in i["E0"]] for i in ini])
    D = np.array([[1.0 - x / q30 for x in i["eps0"]] for i in ini])
    Ei0 = np.array([i["E0"] for i in ini], np.int64)
    Pi0 = np.array([i["eps0"] for i in ini], np.int64)
    Ei1 = np.array([i["E1"] for i in ini], np.int64)
    Pi1 = np.array([i["eps1"] for i in ini], np.int64)
    Ecur, Pcur = Ei0.copy(), Pi0.copy()            # the live (E, eps) ints (the damper crossfade works on these)
    Es, Ps = Ecur.copy(), Pcur.copy()
    B = np.array([[b / float(1 << 45) for b in i["b"]] for i in ini])     # y(real) += F(int Q15) * B
    live = np.array([i["live"] for i in ini]) & (Ei0 != 0)
    kbt = np.array([i["kbt"] for i in ini], np.int64)
    ktb = np.array([i["ktb"] for i in ini], np.int64)
    wtw = np.array([i["wt"] for i in ini], np.int64)
    gain = np.array([i["gain"] for i in ini], float)
    kill1 = [max(1, (1 << 29) // max(1, int(i["gain"]))) for i in ini]     # 1 LSB out, Q25 (bars.c kill1)
    lsin = [[log2_sin(int(a), int(b)) if a else 0 for a, b in zip(i["E0"], i["eps0"])] for i in ini]
    life = np.array([i["life"] for i in ini], np.int64)
    pbuf = np.array([i["pbuf"] for i in ini], np.int64)
    prate = np.array([i["prate"] for i in ini], np.int64)
    pamp = np.array([i["pamp"] for i in ini], np.int64)
    ppos = np.zeros(N, np.int64)
    pon = np.ones(N, bool)
    alive = np.ones(N, bool)
    going = np.zeros((N, NM), bool)
    sr = spec["type"]["sleep_rel"] if sleep_rel is None else sleep_rel
    x = np.zeros((N, NM))
    y = np.zeros((N, NM))
    out = np.zeros((N, n))
    bar = np.zeros((N, n)) if parts or slots else None
    tub = np.zeros((N, n)) if parts or slots else None
    sl = np.zeros((N, NM, n)) if slots else None
    rows = np.arange(N)
    ramp = 0
    wtg = 0
    off_sub = None if off_at is None else int(off_at * FS) // SUB
    if motor:
        inc = btm.mot_inc(motor.get("rate", 64))
        ph = motor.get("ph0", 0)
        gc = spec["type"]["mot_gc"]
    for sb in range(0, n, SUB):
        k_sub = sb // SUB
        if sb % BLOCK == 0:
            if motor:
                gedge, ph = btm.mot_rad(ph, inc, motor.get("depth", 127), motor.get("shape", 0), gc, True)
            else:
                gedge = [32768] * 9
        if off_sub is not None and k_sub == off_sub:
            wtg = pedal_w
            Es, Ps = Ecur.copy(), Pcur.copy()
            ramp = RAMP_SUB
        if ramp:                                    # the damper crossfade, integer as rh_voice_sub
            ramp -= 1
            f = (RAMP_SUB - ramp) * 4681
            f = 32768 if (f > 32768 or not ramp) else f
            et = Ei0 + (((Ei1 - Ei0) * wtg) >> 15)
            pt = Pi0 + (((Pi1 - Pi0) * wtg) >> 15)
            Ecur = np.where(live, Es + (((et - Es) * f) >> 15), Ecur)
            Pcur = np.where(live, Ps + (((pt - Ps) * f) >> 15), Pcur)
            E = Ecur / q30
            D = 1.0 - Pcur / q30
        # the motor over this sub-block: the tube's radiation weight only (x its make-up, Q27 as bars.c); the coupling
        # is the discs-open one whether the motor runs or not (the bar's decay is the same)
        bi = (sb % BLOCK) // SUB
        g0, g1 = gedge[bi], gedge[bi + 1]
        kb0, kt0, dkb, dkt = kbt, ktb, 0, 0
        if motor:                                   # (Q27 in C; doubled here to the Q28 the loop reads)
            w0, w1 = ((wtw * g0) >> 16) * 2, ((wtw * g1) >> 16) * 2
            dw = ((w1 - w0) >> 6) * 2
        else:
            w0, dw = wtw, 0
        pk = np.zeros((N, NM))
        if peaks and sb == 0:
            pkall = np.zeros((N, NM))
        upper = live.copy()
        upper[:, :2] = False
        fading = going.copy()                       # slots put to sleep last sub-block: faded out over this one
        going[:] = False
        for j in range(sb, min(sb + SUB, n)):
            jj = j - sb
            # force (the pulse reader, integers)
            if pon.any():
                i = ppos >> 16
                fr = (ppos >> 1) & 0x7FFF
                a = pbuf[rows, np.minimum(i, PLEN)]
                b = pbuf[rows, np.minimum(i + 1, PLEN + 1)]
                F = np.where(pon & alive, ((a + (((b - a) * fr) >> 15)) * pamp) >> 15, 0)
                ppos = np.where(pon, ppos + prate, ppos)
                pon = pon & ((ppos >> 16) < PLEN)
                y += F[:, None] * B
            kt = (kt0 + jj * dkt) / q30 * live[:, 1]
            kb = (kb0 + jj * dkb) / q30 * live[:, 1]
            wj = (w0 + jj * dw) / float(1 << 28)
            y[:, 0] += btm.COUPLE_SIGN * kt * x[:, 1]
            x[:, 0] += E[:, 0] * y[:, 0]
            y[:, 1] += kb * x[:, 0]
            x[:, 1:] += E[:, 1:] * y[:, 1:]
            y[:] = D * y - E * x
            run = upper | fading
            x[:, 2:] *= run[:, 2:]
            y[:, 2:] *= run[:, 2:]
            np.maximum(pk, np.maximum(np.abs(x), np.abs(y)), out=pk)
            fw = np.where(fading, (1024 * (SUB - jj)) / 32768.0, 1.0)
            xo = x * fw
            up = np.sum(xo[:, 2:], axis=1)
            o = (x[:, 0] + wj * x[:, 1] + up) * alive
            if (j + 2) % BLOCK == 0:
                xprev = x.copy()
            out[:, j] = o
            if bar is not None:
                bar[:, j] = (x[:, 0] + up) * alive
                tub[:, j] = wj * x[:, 1] * alive
            if sl is not None:
                sl[:, :, j] = xo * alive[:, None]
                sl[:, 1, j] = wj * x[:, 1] * alive
        x[fading] = 0.0
        y[fading] = 0.0
        pk[fading] = 0.0
        if peaks:
            np.maximum(pkall, pk, out=pkall)
        # end of the block: the levels, the kill, then the sleep (bars.c bar_voice_block), Q25 integers
        if sleep and (sb + SUB) % BLOCK == 0:
            xi = np.round(x * (1 << 25)).astype(np.int64)
            xp = np.round(xprev * (1 << 25)).astype(np.int64)
            for r in range(N):
                if not alive[r]:
                    continue
                am = [amp_q25(int(xi[r, m]), int(xp[r, m]), int(Ecur[r, m]), int(Pcur[r, m]), lsin[r][m])
                      if live[r, m] else 0 for m in range(NM)]
                wa = abs(int(wtw[r]))
                tot = sum(am[m] for m in range(NM) if m != 1) + ((am[1] * wa) >> 28)
                if not pon[r] and tot < kill1[r]:
                    alive[r] = False
                    continue
                if pon[r]:
                    continue
                ref = (am[0] + ((am[1] * wa) >> 28)) >> sr if sr else 0
                ref = max(ref, kill1[r] >> 4)
                for m in range(2, NM):
                    if live[r, m] and am[m] < ref:
                        live[r, m] = False
                        going[r, m] = True
        if (sb + SUB) % BLOCK == 0:
            life -= 1
            alive &= life > 0
    g = gain[:, None] / 16.0                        # x(real) 2^25 gain / 2^24 / 2^5
    out *= g
    if peaks:
        return out, pkall
    if not (parts or slots):
        return out
    res = dict(out=out, bar=bar * g, tube=tub * g)
    if slots:
        res["slots"] = sl * g[:, :, None]
    return res


# ---------------------------------------------------------------- analysis helpers shared by the generator and the tests
def band_level(yv, f, t0, t1, rel=0.03):
    """Energy (dB) within +-rel of f over [t0, t1] (zero-padded Hann FFT)."""
    seg = yv[int(t0 * FS):int(t1 * FS)]
    nf = 1 << max(14, int(math.ceil(math.log2(len(seg)))) + 2)
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    m = (fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)
    return 10 * math.log10(max(float(np.sum(S[m])), 1e-30))


def env_t60(sig, t0, t1, hop=SUB):
    """T60 (s) of one mode's trace from the slope of its log peak envelope (per hop) over [t0, t1]."""
    a, b = int(t0 * FS), int(t1 * FS)
    seg = np.abs(sig[a:b])
    m = len(seg) // hop
    pk = seg[:m * hop].reshape(m, hop).max(axis=1)
    ok = pk > 0
    t = (np.arange(m) * hop + hop / 2) / FS
    lv = 20 * np.log10(pk[ok])
    if ok.sum() < 4:
        return float("nan")
    sl = np.polyfit(t[ok], lv, 1)[0]
    return -60.0 / sl if sl < 0 else float("inf")


def rms_db(yv, t0=0.05, t1=0.30):
    return 20 * math.log10(float(np.sqrt(np.mean(yv[int(t0 * FS):int(t1 * FS)] ** 2))) + 1e-12)


def lufs_like(yv, t0=0.0, t1=1.0):
    """A simple loudness proxy: RMS (dB re Q15 full scale) over [t0, t1] (VA level checks are relative)."""
    return 20 * math.log10(float(np.sqrt(np.mean(yv[int(t0 * FS):int(t1 * FS)] ** 2))) / 32768 + 1e-12)
