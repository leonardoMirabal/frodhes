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
#pragma GCC optimize("fp-contract=off", "tree-vectorize")   /* (lane-wise: the same sums, in the same order) */
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

static void hq_voice_init(rh_voice_t *v)       /* rhodes.c rh_init, before rh_load_pulse */
{
    hq_voice_t *h = &hq_v[v - rv];
    h->on = hq_mode != 0;
    h->os = (uint8_t)(h->on && v->note >= HQ_OS_LO);
    if (!h->os) return;
    h->h0 = v->Hprev;                          /* (rh_init: Phi~ at the offset, the voice at rest) */
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

static inline int32_t hq_round(float x) { HQ_FP_STRICT return (int32_t)(x >= 0.0f ? x + 0.5f : x - 0.5f); }

/* the pickup at 2x for one 32-sample sub-block: rh_x (the tip) up, through the flux table (rh_hermite_u, the
 * Classic flux exactly), down, the difference times the key's gain into mix; the steal fade and the stereo position
 * as rhodes.c's pickup loops. Hp (the Classic loop's last flux) is passed through: this path keeps its own */
static int32_t hq_pickup(rh_voice_t *v, int32_t *mix, int32_t *side, int32_t Hp)
{
    HQ_FP_STRICT
    hq_voice_t *h = &hq_v[v - rv];
    const int32_t *T = RH_PU_PHIM(v->k->fam);
    const float gs = (float)v->gain * (1.0f / 16777216.0f);   /* (Classic: (dH gain) >> 24) */
    int32_t off = v->off, h0 = h->h0, pan = v->pan;
    float a[SUB], p[SUB], *xb = h->xb, *eb = h->eb, *ob = h->ob, pp = h->pprev;
    uint32_t n;
    int k;
    for (n = 0; n < SUB; n++) xb[HQ_HIS + n] = (float)rh_x[n];
    for (n = 0; n < SUB; n++) a[n] = 0.0f;      /* up: the half-sample point between the two middle inputs, the
                                                * taps outer (each a[n] sums in tap order: the same result as one
                                                * output at a time; the inner loop runs across the outputs: SIMD) */
    for (k = 0; k < (int)HQ_HBP; k++) {
        const float g = HQ_G2[k], *c1 = xb + HQ_HIS + k + 1 - (int)HQ_HBP, *c2 = xb + HQ_HIS - (int)HQ_HBP - k;
        for (n = 0; n < SUB; n++) a[n] += g * (c1[n] + c2[n]);
    }
    for (n = 0; n < SUB; n++) {                /* the flux at both phases (the odd phase: the input, delayed) */
        eb[HQ_HIS + n] = (float)(rh_hermite_u(T, hq_round(a[n]) + off, 1) - h0);
        ob[HQ_HIS + n] = (float)(rh_hermite_u(T, hq_round(xb[HQ_HIS + n + 1u - HQ_HBP]) + off, 1) - h0);
    }
    for (n = 0; n < SUB; n++) p[n] = 0.0f;      /* down (the same order) */
    for (k = 0; k < (int)HQ_HBP; k++) {
        const float g = HQ_G2[k], *c1 = eb + HQ_HIS + k + 1 - (int)HQ_HBP, *c2 = eb + HQ_HIS - (int)HQ_HBP - k;
        for (n = 0; n < SUB; n++) p[n] += g * (c1[n] + c2[n]);
    }
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
