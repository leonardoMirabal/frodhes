#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The HQ Rhodes' live contact data (the Move only, FR_HQ; private plan 06 item 4) -> firmware/gen/hq/rh_hq_contact.h

A re-strike of a key whose tine still rings runs the hammer-tine contact again, from the tine's actual state
(firmware/src/hq/hq_contact.c), instead of superposing the at-rest pulse. Per key this writes the contact's own
description, the same as tools/gen_rhodes.py's tables use (tools/hammer.py: the hammer's effective mass, the Hertz
tip's K, Hunt-Crossley alpha, the tine's 6 FE modes at the strike line), and what the voice needs around it:

- step[11][8][8]: per time step 1 / (fs os), os = 1, 1.5, 2, 3, .. 24, 32 (the voice takes the one that gives 64 or more steps
  across the at-rest contact at its velocity), per mode, the exact step of the damped mode under a force linear
  across the step (a first-order hold: from the matrix exponential); the voice solves the step's end force
  implicitly and lets a mode too fast for the step follow the force statically, so no mode limits the step (the
  treble's FE modes reach 355 kHz);
- ratio: the pulse window's multiple of the first contact (gen_rhodes.key_pulses_all's rule; 1 on every key now);
- HQ_CK_GAP: metres per gap unit per pickup family (1/16" and 1/32"): Classic's tip (calibrated in gaps,
  params.json tip_peak_gap_v127) in metres is the tine's state the contact starts from. (Not the contact
  simulation's own tip after a strike: that is 2.5x Classic's in the treble but 400-750x in the bass, where its
  absolute scale was never fitted; tools/hq_restrike_study.py, private plan 06 item 4.)
- psi_t[3]: the tip's mode shapes of the modes Classic runs (V1 and V2 the 1st FE mode, O1 the 2nd, O2 the 3rd);
- fpk[127]: the at-rest contact's peak force in the pulse's 64 cells (N) at every velocity: the live pulse's force
  scale against the table's (whose shapes are peak-normalised).
Plus the hammer speed per velocity (gen_rhodes.hammer_speed). Floats only (the Move; no libm calls in C).

    python3 tools/gen_rhodes_hq_contact.py [--check]
"""
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_rhodes as gr  # noqa: E402
import gen_rhodes_hq as gh  # noqa: E402
import hammer as hm  # noqa: E402

ROOT = gh.ROOT
OUT = os.path.join(ROOT, "firmware/gen/hq/rh_hq_contact.h")
VS = gh.VS
NFE = 6


def area_cells(F, n=gr.PLEN):
    """gen_rhodes.area_resample without the peak normalisation: the mean of F over each of n cells (F's units)"""
    m = len(F)
    if m < 2:
        F = np.array([F[0] if m else 0.0, 0.0]); m = 2
    C = np.concatenate([[0.0], np.cumsum(F)])
    x = np.linspace(0, m - 1, n)
    lo = np.clip(x - 0.5 * (m - 1) / (n - 1), -0.5, m - 0.5) + 0.5
    hi = np.clip(x + 0.5 * (m - 1) / (n - 1), -0.5, m - 0.5) + 0.5
    I = lambda t: np.interp(t, np.arange(m + 1), C)
    return (I(hi) - I(lo)) / np.maximum(hi - lo, 1e-12)


OSS = (1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32)   # (half octaves: 64 to 96 steps across a contact)


def mode_step(f, s, pc, dt):
    """q1 = a q + b qd + g0 F0 + g1 F1, qd1 = c q + d qd + h0 F0 + h1 F1 for q'' + 2 s q' + w^2 q = pc F, F linear
    from F0 to F1 over dt: the exact step (expm of the system with the force and its slope as states)"""
    from scipy.linalg import expm
    w2 = (2 * np.pi * f) ** 2
    A = np.array([[0, 1, 0, 0], [-w2, -2 * s, pc, 0], [0, 0, 0, 1], [0, 0, 0, 0]], float)
    E = expm(A * dt)
    # state (q, qd, F, Fd): F0 = F, slope Fd = (F1 - F0) / dt
    a, b, cF, cS = E[0]
    c, d, dF, dS = E[1]
    return [a, b, cF - cS / dt, cS / dt, c, d, dF - dS / dt, dS / dt]


def build():
    p, phys = gh.physics()
    h = p["hammer"]
    assert h["p"] == 1.5, "hq_contact.c: e^p as e sqrt(e)"
    keys = []
    for n in range(gr.KEY_LO, gr.KEY_HI + 1):
        k = phys[n]
        cases = gr.contact_cases(p, k, gr.V_ANCH + VS)
        res = hm.contact_batch(cases, fs=gr.FS, t_max=0.03)
        anch = dict(zip(gr.V_ANCH, res[:len(gr.V_ANCH)]))
        seg = {v: gr.contact_segments(anch[v]["F"]) for v in gr.V_ANCH}
        ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg.values()))
        fpk = []
        for v, r in zip(VS, res[len(gr.V_ANCH):]):
            win = ratio * gr.contact_segments(r["F"])[0][1]
            nn = max(2, int(round(win)))
            F = np.concatenate([r["F"], np.zeros(max(0, nn - len(r["F"])))])[:nn]
            c = area_cells(F)
            assert [int(round(q * 32767 / max(c.max(), 1e-30))) for q in c] == gr.area_resample(F), (n, v)
            fpk.append(float(c.max()))
        fr = gr.PINS[n]
        step = [[mode_step(f, sg, pc, 1.0 / (gr.FS * o)) for f, sg, pc in k["fe_modes"]] for o in OSS]
        keys.append(dict(n=n, step=step, ratio=float(ratio), mh=float(cases[0]["m_h"]),
                         K=float(cases[0]["K"]), modes=k["fe_modes"], psi_t=[float(x) for x in fr["psi_t"][:3]],
                         fpk=fpk))
        print(f"key {n}", file=sys.stderr, end="\r")
    vh = [float(gr.hammer_speed(p, v)) for v in VS]
    gap = [0.0254 / 32 * g for g in p["pickup"]["gap_rel"]]
    return keys, vh, float(h["alpha"]), gap


def f32(x):
    return repr(float(np.float32(x))) + "f"


def emit(keys, vh, alpha, gap):
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* GENERATED by tools/gen_rhodes_hq_contact.py from docs/model/data/params.json - do not edit. The HQ Rhodes'",
         " * live contact per key (FR_HQ, the Move): the hammer-tine contact of tools/hammer.py (the tables' own) and",
         " * the voice's maps around it (firmware/src/hq/hq_contact.c) */",
         f"#define HQ_CK_NFE {NFE}u",
         f"#define HQ_CK_NOS {len(OSS)}u",
         "static const float HQ_CK_OS[HQ_CK_NOS] = {" + ",".join(f32(o) for o in OSS) + "};   /* the steps' os: dt = 1 / (fs os) */",
         "#define HQ_CK_NV 8u                    /* the modes padded to two 4-lane vectors (the last two all 0) */",
         f"#define HQ_CK_ALPHA {f32(alpha)}",
         "static const float HQ_CK_GAP[2] = {" + ",".join(f32(g) for g in gap) + "};   /* m per gap, per family */",
         "typedef struct {",
         "    float st[HQ_CK_NOS][8][HQ_CK_NV];   /* per os (HQ_CK_OS), per coefficient, per mode (two 4-lane vectors): q1 = a q + b qd",
         "                                 * + g0 F0 + g1 F1, qd1 = c q + d qd + h0 F0 + h1 F1 (a b g0 g1 c d h0 h1) */",
         "    float w2[HQ_CK_NV], pc[HQ_CK_NV];   /* the FE modes: omega^2, phi at the strike (modes 7, 8: none) */",
         "    float pt[3];                          /* phi at the tip: modes 1..3 */",
         "    float mh, K, ratio;                   /* hammer mass (kg), Hertz K (N/m^1.5), the window's multiple */",
         "} hq_ck_t;",
         "static const hq_ck_t HQ_CK[RH_NKEY] = {"]
    for k in keys:
        md = k["modes"]
        pad = [0.0] * (8 - NFE)
        L.append("  {{" + ",".join("{" + ",".join("{" + ",".join(f32(x) for x in [ms[j] for ms in so] + pad) + "}"
                                              for j in range(8)) + "}" for so in k["step"]) + "},")
        L.append("   {" + ",".join(f32(x) for x in [(2 * np.pi * f) ** 2 for f, _, _ in md] + pad) + "},{"
                 + ",".join(f32(x) for x in [c for _, _, c in md] + pad) + "},{" + ",".join(f32(x) for x in k["psi_t"]) + "},"
                 + f"{f32(k['mh'])},{f32(k['K'])},{f32(k['ratio'])}}},   /* {k['n']} */")
    L.append("};")
    L.append("static const float HQ_CK_FPK[RH_NKEY][127] = {   /* N: the at-rest pulse's peak cell, v = row + 1 */")
    for k in keys:
        L.append("  {" + ",".join(f32(x) for x in k["fpk"]) + "},")
    L.append("};")
    L.append("static const float HQ_CK_VH[127] = {   /* m/s: the hammer speed, v = index + 1 */")
    L.append("  " + ",".join(f32(x) for x in vh))
    L.append("};")
    body = "\n".join(L) + "\n"
    return body + f"/* sha256(body) = {hashlib.sha256(body.encode()).hexdigest()} */\n"


def main():
    hdr = emit(*build())
    if "--check" in sys.argv[1:]:
        old = open(OUT).read() if os.path.exists(OUT) else ""
        print("equal" if old == hdr else "DIFFERS")
        return 0 if old == hdr else 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(hdr)
    print(f"wrote {OUT} ({len(hdr)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
