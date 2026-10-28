/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ Rhodes (FR_HQ, the Move only): the code, included at the end of rhodes.c. State and the overview:
 * hq/rhodes_hq.h. Float (the Move's A72 has an FPU); the same results on every host: no fused multiply-add, no
 * reassociation (the pragmas below, and the build's -ffp-contract=off), so Gate H's renders are the same on the
 * Mac, Linux and the Move. */
#include "hq/rh_hq_pulse.h"
#if defined(__clang__)
#define HQ_FP_STRICT _Pragma("clang fp contract(off)")   /* (first in each float function's body) */
#else
#define HQ_FP_STRICT
#if defined(__GNUC__)
#pragma GCC push_options
#pragma GCC optimize("fp-contract=off", "tree-vectorize", "no-predictive-commoning")
/* (vectorised lane-wise: the same sums, in the same order. Predictive commoning (gcc 12 at -O2 here) reuses the
 * FIR's overlapping loads across taps and spills 16 registers a tap: hq_fir 3x the loads) */
#endif
#endif

/* the half-band's side taps, doubled (2 g_k, k = 0..15: tools/hq_alias_study.py hb_taps(16, 8), as float32). With
 * them the 2x interpolator is a[n] = sum 2 g_k (x[n-P+1+k] + x[n-P-k]) between x[n-P] and x[n-P+1], and the
 * decimator p[n] = (sum 2 g_k (e[n-P+1+k] + e[n-P-k]) + o[n-P]) / 2 (o the odd phase); the pair's delay is 2P - 1 =
 * 31 samples (0.7 ms) on the keys that use it */
static const float HQ_G2[HQ_HBP] = {
    0.634145796f, -0.204885036f, 0.115448095f, -0.0749787688f, 0.0512753725f, -0.0356094018f, 0.0246311668f,
    -0.0167524219f, 0.0110872947f, -0.00706882915f, 0.00429181755f, -0.00244415225f, 0.00127621286f,
    -0.000587120187f, 0.00021807247f, -4.80305098e-05f};

/* the dense table (1.5 MB) copied into RAM of its own at load (a constructor: it runs when the module is loaded, in
 * the loader's thread, never the audio's). Read from the module's file mapping, its first reads came as page faults
 * in the audio callback: 16 notes of a chord, 16 faults, up to 1.5 ms in one 128-frame callback
 * (move/bench/classic_bench.c, 2026-10-28); and a clean file page can be dropped under memory pressure and fault
 * again. The copy is anonymous memory, written once: it leaves RAM only to a swap (none seen on the Move yet:
 * to check at D3); no import added (no mlock) */
static int16_t hq_pulse_ram[RH_NKEY][127][RH_PLEN + 2];
static uint32_t hq_prate_ram[RH_NKEY][127];
static __attribute__((constructor)) void hq_page_in(void)
{
    memcpy(hq_pulse_ram, RH_HQ_PULSE, sizeof hq_pulse_ram);
    memcpy(hq_prate_ram, RH_HQ_PRATE, sizeof hq_prate_ram);
}

/* the flux in closed form (tools/pickup.py: Phi(u) = T(u + R) - T(u - R), T(w) = w / (w^2 + 1), u in the family's
 * gaps; even in u, so Phi~(s) = Phi(sqrt s)), per family its R and the Classic table's scale Q28 / A
 * (tools/gen_rhodes.py pickup_tables; tests/py/test_rhodes_hq.py checks both). The HQ path evaluates it exactly,
 * not through the table: the same flux to the table's own error, and NEON-friendly (no table gather) */
static const float HQ_PU_R[2] = {0.5f, 1.0f};
static const float HQ_PU_K[2] = {335544320.0f, 268435456.0f};
#define HQ_GAP (1.0f / 33554432.0f)            /* Q25 gap -> gap */

/* Phi(o + d) - Phi(o) without the cancellation of two near values: T(a) - T(b) = (a - b)(1 - ab) / ((a^2 + 1)(b^2 + 1)),
 * a - b = d; b1 = o + R, b2 = o - R and c = 1 / (b^2 + 1) per voice (hq_voice_init). The value is d (n1 d2 - n2 d1)
 * / (d1 d2): hq_dphi2 gives it for two points d, e (the 2x path's two phases) with one division, 1 / (D_d D_e)
 * (a vector divide is the A72's slowest instruction: about 20 cycles for 4 lanes) */
static inline void hq_dphi2(const hq_voice_t *h, float d, float e, float *pd, float *pe)
{
    HQ_FP_STRICT
    float a1 = h->b1 + d, a2 = h->b2 + d, b1 = h->b1 + e, b2 = h->b2 + e;
    float n1 = (1.0f - a1 * h->b1) * h->c1, n2 = (1.0f - a2 * h->b2) * h->c2;
    float m1 = (1.0f - b1 * h->b1) * h->c1, m2 = (1.0f - b2 * h->b2) * h->c2;
    float d1 = a1 * a1 + 1.0f, d2 = a2 * a2 + 1.0f, e1 = b1 * b1 + 1.0f, e2 = b2 * b2 + 1.0f;
    float D = d1 * d2, E = e1 * e2, r = 1.0f / (D * E);
    *pd = d * (n1 * d2 - n2 * d1) * E * r;
    *pe = e * (m1 * e2 - m2 * e1) * D * r;
}

static void hq_voice_init(rh_voice_t *v)       /* rhodes.c rh_init, before rh_load_pulse */
{
    HQ_FP_STRICT
    hq_voice_t *h = &hq_v[v - rv];
    h->on = hq_mode != 0;
    h->os = (uint8_t)(h->on && v->note >= HQ_OS_LO);
    if (!h->os) return;
    {
        float o = (float)v->off * HQ_GAP, R = HQ_PU_R[v->k->fam & 1u];
        h->b1 = o + R; h->b2 = o - R;
        h->c1 = 1.0f / (h->b1 * h->b1 + 1.0f); h->c2 = 1.0f / (h->b2 * h->b2 + 1.0f);
    }
    h->pprev = 0.0f;
    memset(h->xb, 0, sizeof h->xb); memset(h->eb, 0, sizeof h->eb); memset(h->ob, 0, sizeof h->ob);
}

/* rhodes.c rh_load_pulse, before the HAMMER scaling: the exact contact's shape and rate at vel (the peak stays
 * Classic's, the velocity law the calibration set) */
static void hq_pulse(rh_voice_t *v, uint32_t vel)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS);
    if (!hq_v[v - rv].on || vel < 1u || vel > 127u) return;
    v->ptab = hq_pulse_ram[key][vel - 1u];
    v->prate = hq_prate_ram[key][vel - 1u] << 4;          /* Q12 -> Q16 */
}

/* the half-band's symmetric sum for the 32 outputs of a sub-block: y[n] = sum_k 2 g_k (x[n-P+1+k] + x[n-P-k]), x the
 * history buffer (HQ_HIS samples of history, then the sub-block). Each output sums in tap order, from 0 (the same
 * result as one output at a time, on every host: lane-wise IEEE, no fused multiply-add); the 32 sums stay in 8
 * 4-lane registers across the taps, so a tap costs 16 loads and no store (the taps-outer loop over an array cost
 * 3 loads and a store per 4 outputs, the A72's load port the limit) */
typedef float hq_f4 __attribute__((vector_size(16)));
typedef float hq_f4u __attribute__((vector_size(16), aligned(4), may_alias));     /* (an unaligned load / store) */
static inline hq_f4 hq_ld4(const float *p) { return *(const hq_f4u *)p; }
static inline void hq_fir(float *y, const float *xb)
{
    HQ_FP_STRICT
    hq_f4 s0 = {0}, s1 = {0}, s2 = {0}, s3 = {0}, s4 = {0}, s5 = {0}, s6 = {0}, s7 = {0};
    int k;
    for (k = 0; k < (int)HQ_HBP; k++) {
        const float *c1 = xb + HQ_HIS + k + 1 - (int)HQ_HBP, *c2 = xb + HQ_HIS - (int)HQ_HBP - k;
        const hq_f4 g = {HQ_G2[k], HQ_G2[k], HQ_G2[k], HQ_G2[k]};
        s0 += g * (hq_ld4(c1) + hq_ld4(c2));
        s1 += g * (hq_ld4(c1 + 4) + hq_ld4(c2 + 4));
        s2 += g * (hq_ld4(c1 + 8) + hq_ld4(c2 + 8));
        s3 += g * (hq_ld4(c1 + 12) + hq_ld4(c2 + 12));
        s4 += g * (hq_ld4(c1 + 16) + hq_ld4(c2 + 16));
        s5 += g * (hq_ld4(c1 + 20) + hq_ld4(c2 + 20));
        s6 += g * (hq_ld4(c1 + 24) + hq_ld4(c2 + 24));
        s7 += g * (hq_ld4(c1 + 28) + hq_ld4(c2 + 28));
    }
    *(hq_f4u *)y = s0; *(hq_f4u *)(y + 4) = s1; *(hq_f4u *)(y + 8) = s2; *(hq_f4u *)(y + 12) = s3;
    *(hq_f4u *)(y + 16) = s4; *(hq_f4u *)(y + 20) = s5; *(hq_f4u *)(y + 24) = s6; *(hq_f4u *)(y + 28) = s7;
}
_Static_assert(SUB == 32u, "hq_fir: 8 x 4 outputs");

static inline int32_t hq_round(float x) { HQ_FP_STRICT return (int32_t)(x >= 0.0f ? x + 0.5f : x - 0.5f); }

/* the pickup at 2x for one 32-sample sub-block: rh_x (the tip, in gaps) up, through the flux (hq_dphi), down, the
 * difference times the key's gain into mix; the steal fade and the stereo position as rhodes.c's pickup loops. Hp
 * (the Classic loop's last flux) is passed through: this path keeps its own */
static int32_t hq_pickup(rh_voice_t *v, int32_t *mix, int32_t *side, int32_t Hp)
{
    HQ_FP_STRICT
    hq_voice_t *h = &hq_v[v - rv];
    const float gs = (float)v->gain * (1.0f / 16777216.0f) * HQ_PU_K[v->k->fam & 1u];   /* (Classic: (dH gain) >> 24,
                                                * H in the table's Q28 / A) */
    int32_t pan = v->pan;
    float a[SUB], p[SUB], *xb = h->xb, *eb = h->eb, *ob = h->ob, pp = h->pprev;
    uint32_t n;
    for (n = 0; n < SUB; n++) xb[HQ_HIS + n] = (float)rh_x[n] * HQ_GAP;
    hq_fir(a, xb);                             /* up: the half-sample point between the two middle inputs */
    for (n = 0; n < SUB; n++)                  /* the flux at both phases (the odd phase: the input, delayed) */
        hq_dphi2(h, a[n], xb[HQ_HIS + n + 1u - HQ_HBP], &eb[HQ_HIS + n], &ob[HQ_HIS + n]);
    hq_fir(p, eb);                             /* down */
    for (n = 0; n < SUB; n++) p[n] = 0.5f * (p[n] + ob[HQ_HIS + n - HQ_HBP]);
    for (n = 0; n < SUB; n++) {                /* the difference into the mix */
        int32_t o = hq_round((p[n] - pp) * gs);
        pp = p[n];
        if (v->fade != 32768) {
            o = (int32_t)(((int64_t)o * v->fade) >> 15);
            v->fade -= (int32_t)(32768 / FADE_SAMPLES);
            if (v->fade < 0) v->fade = 0;
        }
        mix[n] += o;
        if (pan) side[n] += (o * pan) >> 8;
    }
    h->pprev = pp;
    memmove(xb, xb + SUB, HQ_HIS * sizeof *xb);
    memmove(eb, eb + SUB, HQ_HIS * sizeof *eb);
    memmove(ob, ob + SUB, HQ_HIS * sizeof *ob);
    return Hp;
}
#if !defined(__clang__) && defined(__GNUC__)
#pragma GCC pop_options
#endif
