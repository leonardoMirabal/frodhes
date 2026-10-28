/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ Rhodes' live contact (private plan 06 item 4; FR_HQ, the Move only): the hammer-tine contact of
 * tools/hammer.py (the one the pulse tables come from), run in the voice from the tine's actual state when a key
 * is struck again while its tine still rings. The tables' pulse is the contact against a tine at rest; on a moving
 * tine the hammer meets it faster or slower than its own speed, and the force's size and shape change with the
 * tine's phase (tools/hq_restrike_study.py --classic: up to +-1.5 dB at the same velocity on the treble, +5 dB for
 * a soft strike on a loud note; under 0.2 dB below about C4).
 *
 * Self-contained (included by hq/rhodes_hq.c; tests/hq/contact_test.c includes it alone): float, no libm call but
 * the square root (exact in IEEE, so the same results on every host; the callers keep fused multiply-adds off).
 *
 * The contact: the hammer (mass mh, speed vh) on a Hertz tip, F = K e^1.5 (1 + alpha de/dt) for compression
 * e > 0, against the tine's 6 mass-normalised modes at the strike line, q'' + 2 sigma q' + w^2 q = phi_c F. The
 * hammer starts touching the tine where it is now, so the contact starts at the strike's sample, at the tine's
 * phase now. Each step is exact for the modes and the hammer under a force linear across the step (the generated
 * step matrices), its end force solved for (implicit: hq_ck_step); a mode too fast for the step (omega dt > 2: the
 * treble's highest reach 355 kHz) follows the force statically. The step is 1 / (fs os), os the power of two that
 * gives 64 or more steps across the at-rest contact at the velocity (the explicit predictor-corrector this replaced
 * needed the highest mode's step: up to 770 steps, 99 us on the A72; this one takes up to 209). The pulse is then
 * made the tables' way (tools/gen_rhodes.py key_pulses_all): the window is the key's ratio times the end of the first contact (as tools/hammer.py's 16x
 * grid places it), 64 cells of the mean force over it (the force linear between steps), peak-normalised, and the
 * rate that plays the 64 cells across the window. Its force scale against the at-rest pulse's: the peak cells'
 * ratio (HQ_CK_FPK). tests/py/test_rhodes_hq.py checks the at-rest pulses against the tables. */
#include "hq/rh_hq_contact.h"

#ifndef HQ_CK_SPC
#define HQ_CK_SPC 64u                          /* steps at least across the at-rest contact */
#endif
#define HQ_CK_MAXSTEP 2048u                    /* (a contact that never ends: cut there) */
#define HQ_FS 44100.0f

/* the square root, the instruction alone on the Move: gcc's sqrtf keeps a call to libm's for errno (a negative
 * argument, which never comes here), an import the dsp.so may not have (make test-move). Exact in IEEE either way */
static inline float hq_ck_sqrt(float x)
{
#if defined(__aarch64__) && defined(__GNUC__)
    float r;
    __asm__("fsqrt %s0, %s1" : "=w"(r) : "w"(x));
    return r;
#else
    return sqrtf(x);
#endif
}

typedef struct {
    int16_t shape[RH_PLEN + 2];                /* the pulse, peak 32767 (the last two 0, as the tables) */
    uint32_t prate;                            /* Q12 cells per sample (as RH_HQ_PRATE) */
    float fpk;                                 /* N: its peak cell (HQ_CK_FPK's units) */
} hq_live_t;

static float hq_ck_F[HQ_CK_MAXSTEP + 1], hq_ck_e[HQ_CK_MAXSTEP + 1];   /* force and compression per step's start */

typedef float hq_ck_v __attribute__((vector_size(16)));      /* 4 lanes: the modes in two vectors */
typedef float hq_ck_vu __attribute__((vector_size(16), aligned(4), may_alias));
typedef struct { float xh, vh; hq_ck_v q[2], qd[2]; } hq_ck_st_t;
static inline hq_ck_v hq_ck_ld(const float *p) { return *(const hq_ck_vu *)p; }
static inline float hq_ck_hsum(hq_ck_v t) { HQ_FP_STRICT return (t[0] + t[1]) + (t[2] + t[3]); }   /* (fixed order) */

/* one step from s (its force F0) under a force linear from F0 to the step's end force, which is solved for: the end
 * state is linear in F1 (the modes' and the hammer's exact steps), so the compression and its rate at the end are
 * e1 = E - C F1, de1 = W - D F1 (C, D the same every step), and F1 = K e1^1.5 (1 + alpha de1). In r = sqrt(e1),
 * F1 = (E - r^2) / C, the equation is a polynomial, E - r^2 - K C r^3 (d0 + b r^2) = 0 with d0 = 1 + alpha (W - D E /
 * C) and b = alpha D / C: decreasing and concave in r on [0, sqrt(E)], so Newton from the right of the root never
 * overshoots; no square root or divide in its loop but the step's one. Implicit: no mode limits the step (the
 * treble's FE modes reach 355 kHz), the step is set by the contact's own length. k: {C, D, 1 / C, K C, alpha D / C}.
 * Returns F1; *r1 its sqrt(e1) (or -1 when e1 < 0: no contact), *e1 the compression */
static inline float hq_ck_step(const hq_ck_t *c, const float (*st)[HQ_CK_NV], float dt, float imh, const float *k,
                               hq_ck_st_t *s, float F0, float rg, float *r1, float *e1)
{
    HQ_FP_STRICT
    hq_ck_v u[2], w[2], f0 = {F0, F0, F0, F0}, p0 = hq_ck_ld(c->pc), p1 = hq_ck_ld(c->pc + 4);
    float Xh = s->xh + dt * s->vh - dt * dt * (2.0f * F0) * imh * (1.0f / 6.0f), Vh = s->vh - dt * F0 * imh * 0.5f;
    float Ch = dt * dt * imh * (1.0f / 6.0f), Dh = dt * imh * 0.5f, E, W, F = 0.0f, r = -1.0f;
    uint32_t h, it;
    for (h = 0; h < 2u; h++) {
        const uint32_t o = 4u * h;
        u[h] = (hq_ck_ld(st[0] + o) * s->q[h] + hq_ck_ld(st[1] + o) * s->qd[h]) + hq_ck_ld(st[2] + o) * f0;
        w[h] = (hq_ck_ld(st[4] + o) * s->q[h] + hq_ck_ld(st[5] + o) * s->qd[h]) + hq_ck_ld(st[6] + o) * f0;
    }
    E = Xh - hq_ck_hsum(p0 * u[0] + p1 * u[1]);
    W = Vh - hq_ck_hsum(p0 * w[0] + p1 * w[1]);
    *e1 = E;
    if (E > 0.0f) {
        float d0 = 1.0f + HQ_CK_ALPHA * (W - k[1] * E * k[2]), b = k[4], kc = k[3], rmax = hq_ck_sqrt(E);
        if (d0 + b * E > 0.0f) {                       /* (else the felt would pull even at no force: none) */
            r = rg > 0.0f && rg < rmax ? rg : rmax;
            for (it = 0; it < 8u; it++) {
                float r2 = r * r, dm = d0 + b * r2, H, dH, rn;
                if (dm < 0.0f) dm = 0.0f;
                H = (E - r2) - kc * (r2 * r) * dm;
                dH = 2.0f * r + kc * r2 * (3.0f * dm + 2.0f * b * r2);
                if (!(dH > 0.0f)) { r = rmax; continue; }
                rn = r + H / dH;
                if (rn > rmax) rn = rmax;
                if (rn < 0.0f) rn = 0.0f;
                if (rn - r <= 1e-5f * r && r - rn <= 1e-5f * r) { r = rn; break; }   /* (F to ~1e-5: the last
                                                         * iteration's change is the error's square) */
                r = rn;
            }
            F = (E - r * r) * k[2];
            if (F < 0.0f) F = 0.0f;
            *e1 = E - k[0] * F;
        }
    }
    {
        hq_ck_v f1 = {F, F, F, F};
        for (h = 0; h < 2u; h++) {
            const uint32_t o = 4u * h;
            s->q[h] = u[h] + hq_ck_ld(st[3] + o) * f1;
            s->qd[h] = w[h] + hq_ck_ld(st[7] + o) * f1;
        }
    }
    s->xh = Xh - Ch * F; s->vh = Vh - Dh * F;
    (void)Ch; (void)Dh;
    *r1 = r;
    return F;
}

/* a time in steps (where e crosses zero between two steps) in cells of hammer.py's 16x grid: g cells a step */
static inline float hq_ck_cross(uint32_t k, float ek, float ek1, float g)
{
    HQ_FP_STRICT
    float d = ek - ek1, f = d != 0.0f ? ek / d : 0.0f;
    if (f < 0.0f) f = 0.0f;
    if (f > 1.0f) f = 1.0f;
    return ((float)k + f) * g;
}

/* hammer.py records the force at each 32x step's start and averages pairs into the 16x cells: a contact from T0 to
 * T1 (16x cells) lights the cells from floor(ceil(2 T0) / 2) to floor(ceil(2 T1) / 2 - 1/2) */
static inline uint32_t hq_ck_c0(float T) { HQ_FP_STRICT float a = (float)(uint32_t)(2.0f * T); if (a < 2.0f * T) a += 1.0f; return (uint32_t)(a * 0.5f); }
static inline uint32_t hq_ck_c1(float T)                  /* exclusive */
{
    HQ_FP_STRICT
    float a = (float)(uint32_t)(2.0f * T);
    if (a < 2.0f * T) a += 1.0f;                           /* ceil(2 T) */
    return (uint32_t)(a * 0.5f - 0.5f) + 1u;
}

/* the pulse from the force: the window (the key's ratio x the first strike's end, 16x cells), gen_rhodes.area_resample's
 * 64 cells over it, peak-normalised, and the rate that plays them across the window */
static void hq_ck_cells(const hq_ck_t *c, uint32_t nst, float g, float end16, hq_live_t *out)
{
    HQ_FP_STRICT
    float win16 = c->ratio * end16, cells[RH_PLEN], acc = 0.0f, mx = 0.0f, st, hw, prev;
    uint32_t nn = (uint32_t)(win16 + 0.5f), i, j;
    if (nn < 2u) nn = 2u;
    prev = hq_ck_F[0];                         /* the integral at the steps (16x units), trapezoids */
    hq_ck_F[0] = 0.0f;
    for (i = 1; i < nst; i++) { float f = hq_ck_F[i]; acc += 0.5f * (prev + f) * g; prev = f; hq_ck_F[i] = acc; }
    acc += 0.5f * prev * g;                    /* (the force 0 one step after the last) */
    hq_ck_F[nst] = acc;
    st = (float)(nn - 1u) / (float)(RH_PLEN - 1u);
    hw = 0.5f * st;
    for (j = 0; j < RH_PLEN; j++) {
        float x = (float)j * st, lo = x - hw, hi = x + hw, I[2], t[2];
        uint32_t q;
        lo = (lo < -0.5f ? -0.5f : lo > (float)nn - 0.5f ? (float)nn - 0.5f : lo) + 0.5f;
        hi = (hi < -0.5f ? -0.5f : hi > (float)nn - 0.5f ? (float)nn - 0.5f : hi) + 0.5f;
        t[0] = (lo - 0.25f) / g; t[1] = (hi - 0.25f) / g;   /* in steps; hammer.py's cells hold the force at a
                                                            * quarter cell (the mean of its two 32x step starts):
                                                            * its integral is the true one a quarter cell late */
        for (q = 0; q < 2u; q++) {
            uint32_t k;
            if (t[q] <= 0.0f) { I[q] = 0.0f; continue; }
            k = (uint32_t)t[q];
            I[q] = k >= nst ? hq_ck_F[nst] : hq_ck_F[k] + (hq_ck_F[k + 1u] - hq_ck_F[k]) * (t[q] - (float)k);
        }
        cells[j] = (I[1] - I[0]) / (hi - lo > 1e-12f ? hi - lo : 1e-12f);
        if (cells[j] > mx) mx = cells[j];
    }
    for (j = 0; j < RH_PLEN; j++) out->shape[j] = (int16_t)(cells[j] * 32767.0f / (mx > 1e-30f ? mx : 1e-30f) + 0.5f);
    out->shape[RH_PLEN] = out->shape[RH_PLEN + 1u] = 0;
    out->fpk = mx;
    out->prate = (uint32_t)(64.0f / (win16 / 16.0f) * 4096.0f + 0.5f);
}

/* the contact from the tine's state (q0, qd0: the 6 modes, m and m/s) at velocity vel 1..127 of key (0..87), with
 * the at-rest pulse's rate prate_rest (Q12: RH_HQ_PRATE, its window sets the step), into out; returns the steps
 * run (0: no contact in the time allowed: out is not written) */
static uint32_t hq_contact_run(uint32_t key, uint32_t vel, uint32_t prate_rest, const float *q0, const float *qd0,
                               hq_live_t *out)
{
    HQ_FP_STRICT
    const hq_ck_t *c = &HQ_CK[key];
    float end_rest16 = 64.0f * 16.0f * 4096.0f / ((float)prate_rest * c->ratio), dt, g, imh = 1.0f / c->mh, tot = 0.0f;
    uint32_t oi = 0, n, m, touched = 0, nst = 0, on = 0;
    float fs = -1.0f, fe = -1.0f;              /* the first strike's start and end so far, in steps */
    hq_ck_st_t s;
    float kq[5], Fc = 0.0f, rc = -1.0f, rp = -1.0f, rn, ec = 0.0f, qa[HQ_CK_NV] = {0}, qda[HQ_CK_NV] = {0}, stl[8][HQ_CK_NV], apc[HQ_CK_NV], iw2[HQ_CK_NV];
    while (oi + 1u < HQ_CK_NOS && end_rest16 / 16.0f * HQ_CK_OS[oi] < (float)HQ_CK_SPC) oi++;
    dt = 1.0f / (HQ_FS * HQ_CK_OS[oi]);
    g = 16.0f / HQ_CK_OS[oi];                  /* 16x cells a step */
    s.xh = 0.0f; s.vh = HQ_CK_VH[vel - 1u];
    for (m = 0; m < HQ_CK_NFE; m++) { qa[m] = q0[m]; qda[m] = qd0[m]; }
    {   /* a mode too fast for the step (omega dt > 2) follows the force statically, q = phi F / omega^2 (its rate
         * from the force's slope): its exact step would ring at the step's rate against the contact's stiffness */
        hq_ck_v p0, p1;
        memcpy(stl, c->st[oi], sizeof stl);
        for (m = 0; m < HQ_CK_NFE; m++)
            if (c->w2[m] * dt * dt > 4.0f) {
                float sq = c->pc[m] / c->w2[m];
                uint32_t j;
                for (j = 0; j < 8u; j++) stl[j][m] = 0.0f;
                stl[3][m] = sq; stl[6][m] = -sq / dt; stl[7][m] = sq / dt;
                qa[m] = qda[m] = 0.0f;
            }
        s.q[0] = hq_ck_ld(qa); s.q[1] = hq_ck_ld(qa + 4); s.qd[0] = hq_ck_ld(qda); s.qd[1] = hq_ck_ld(qda + 4);
        for (m = 0; m < HQ_CK_NV; m++) {
            apc[m] = c->pc[m] < 0.0f ? -c->pc[m] : c->pc[m];
            iw2[m] = c->w2[m] > 0.0f && c->w2[m] * dt * dt <= 4.0f ? 1.0f / c->w2[m] : 0.0f;
            if (c->w2[m] * dt * dt > 4.0f) apc[m] = 0.0f;   /* (a static mode: 0 at no force) */
        }
        p0 = hq_ck_ld(c->pc); p1 = hq_ck_ld(c->pc + 4);
        s.xh = hq_ck_hsum(p0 * s.q[0] + p1 * s.q[1]);   /* touching */
        kq[0] = dt * dt * imh * (1.0f / 6.0f) + hq_ck_hsum(p0 * hq_ck_ld(stl[3]) + p1 * hq_ck_ld(stl[3] + 4));   /* C */
        kq[1] = dt * imh * 0.5f + hq_ck_hsum(p0 * hq_ck_ld(stl[7]) + p1 * hq_ck_ld(stl[7] + 4));            /* D */
        kq[2] = 1.0f / kq[0];
        kq[3] = c->K * kq[0];
        kq[4] = HQ_CK_ALPHA * kq[1] * kq[2];
    }
    for (n = 0; n < HQ_CK_MAXSTEP; n++) {
        float F0 = Fc, e0 = ec;
        Fc = hq_ck_step(c, (const float (*)[HQ_CK_NV])stl, dt, imh, kq, &s, F0, rp > 0.0f ? 2.0f * rc - rp : rc, &rn, &ec);
        rp = rc; rc = rn;
        hq_ck_F[n] = F0; hq_ck_e[n] = e0;
        tot += F0;
        nst = n + 1u;
        if (e0 > 0.0f && !on) {               /* a contact starts: the first strike again (a short gap), or a new */
            on = 1;                            /* one after a complete first strike: the window is known */
            if (fe > 0.0f && (float)n - fe >= 0.5f * (fe - fs)) break;
        } else if (!(e0 > 0.0f) && on) {
            on = 0;
            fe = (float)n;                     /* (a later contact got here only as part of the first strike;
                                                * step units: the cells redo it on hammer.py's grid) */
            if (fs < 0.0f) fs = 0.0f;
        }
        if (e0 > 0.0f && fs < 0.0f) fs = (float)n;
        if (fe > 0.0f && !on && (float)n - fe >= 0.5f * (fe - fs) && (float)n >= c->ratio * fe + 2.0f)
            break;                             /* the first strike is complete and its window run: the rest of the
                                                * contact is never read (the cost: a slow hammer separates slowly) */
        if (F0 > 0.0f) touched = 1;
        else if (touched && s.vh < 0.0f) {
            hq_ck_v a0, a1;
            if (ec < -2e-4f) break;            /* free, moving away, clearly clear of the tine */
            /* or never to touch again: a free mode's energy only falls, so |q| <= sqrt(q^2 + qd^2 / w^2) from now
             * on, and the tine at the strike line stays above -sum |phi| that; the hammer only falls (the pulse is
             * the same as running on: no step after this one could touch) */
            a0 = s.q[0] * s.q[0] + s.qd[0] * s.qd[0] * hq_ck_ld(iw2);
            a1 = s.q[1] * s.q[1] + s.qd[1] * s.qd[1] * hq_ck_ld(iw2 + 4);
            {
                float A = 0.0f;
                for (m = 0; m < 4u; m++) A += apc[m] * hq_ck_sqrt(a0[m]) + apc[m + 4u] * hq_ck_sqrt(a1[m]);
                if (s.xh + A < 0.0f) break;
            }
        }
    }
    if (!touched) return 0;
    hq_ck_e[nst] = -1.0f;
    {   /* the first strike's end (gen_rhodes.contact_segments on hammer.py's 16x cells: a gap shorter than half the
         * strike so far is the same strike, its face chattering on the high modes; a strike under 0.5 % of the
         * impulse does not count) */
        uint32_t i = 0, have = 0, s0 = 0, e0c = 0;
        float seg = 0.0f;
        while (i < nst) {
            uint32_t a, b, ca, cb;
            float part = 0.0f;
            while (i < nst && !(hq_ck_e[i] > 0.0f)) i++;
            if (i >= nst) break;
            a = i;
            while (i < nst && hq_ck_e[i] > 0.0f) { part += hq_ck_F[i]; i++; }
            b = i;                             /* e > 0 on steps a .. b - 1 */
            ca = hq_ck_c0(a ? hq_ck_cross(a - 1u, hq_ck_e[a - 1u], hq_ck_e[a], g) : 0.0f);
            cb = hq_ck_c1(hq_ck_cross(b - 1u, hq_ck_e[b - 1u], hq_ck_e[b], g));
            if (cb <= ca) cb = ca + 1u;
            if (have && (float)ca - (float)e0c < 0.5f * (float)(e0c - s0)) { e0c = cb; seg += part; continue; }
            if (have && seg >= 0.005f * tot) break;
            s0 = ca; e0c = cb; seg = part; have = 1;
        }
        hq_ck_cells(c, nst, g, (float)e0c, out);
    }
    return nst;
}
