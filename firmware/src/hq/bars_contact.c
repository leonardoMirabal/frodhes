/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ bars' mallet contact (private plan 07 items B3, B4; FR_HQ, the Move only): the mallet-bar contact of
 * tools/hammer.py (the one the bar pulse tables come from, tools/gen_bars.py contacts), run in the voice from the
 * bar's actual state when a key is struck while its bar still rings (and on MARIMBA's top keys at every strike: there
 * the contact is longer than V1's period and the velocity anchors cannot follow V1's place on its spectrum, plan 07
 * section 2.2). The tables' pulse is the contact against a bar at rest; on a ringing bar the mallet meets it moving,
 * and the force's size and shape change with the bar's phase (-9 to -17 dB of the note's energy on VIBES re-strikes,
 * tools/bars_hq_study.py contact).
 *
 * Self-contained (included by hq/bars_hq.c; tests/hq/bars_contact_gen.c includes it alone): float, no libm call but
 * the square root (exact in IEEE, so the same results on every host; the callers keep fused multiply-adds off).
 *
 * The contact: the mallet (mass mh at HAMMER 64, speed vh) on the felt, F = K e^p (p 2 the yarns, 1.5 the cords and
 * rubber; no damping, as the tables), against the bar's bending modes V1 .. V7 at the strike line (TONE 64's point,
 * the tables' own), mass-normalised: q'' + 2 sigma q' + w^2 q = phi F. The mallet starts touching the bar where it is
 * now. Each step is exact for the modes and the mallet under a force linear across it (the generated step matrices),
 * its end force solved for: p 2 the stable root of the quadratic, p 1.5 Newton in r = sqrt(e) (as hq_contact.c). The
 * step is 1 / (fs os), os the smallest of HQB_CK_OS with 24 steps or more across the at-rest contact and 3 or more a
 * period of the key's top mode (plan 07: <= -33 dB against RK4). The pulse is then made the tables' way
 * (gen_bars.contact_tables): the window the law's ratio times the end of the first contact, 64 cells of the mean force
 * over it, peak-normalised, played at the rate of the played window (T7g's velocity brightness, T7f's cap); its force
 * scale against the at-rest one's (HQB_CK_FPK, from this same code: equal at rest by construction). */
#include "hq/bars_hq_contact.h"

#define HQB_CK_SPC 24.0f                       /* steps at least across the at-rest contact */
#define HQB_CK_PPF 3.0f                        /* steps at least a period of the key's top mode */
#define HQB_CK_MAXSTEP 2048u                   /* (a contact that never ends: cut there) */
#define HQB_FS 44100.0f

static inline float hqb_ck_sqrt(float x)       /* the instruction alone on the Move (no libm import: hq_contact.c) */
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
    int16_t shape[BAR_PLEN + 2];               /* the pulse, peak 32767 (the last two 0, as the tables) */
    uint32_t prate;                            /* Q12 cells a sample (as BAR_PRATE) */
    float fpk;                                 /* N: the peak force x sqrt(the cap's share), contact_tables' fpk */
} hqb_live_t;

static float hqb_ck_F[HQB_CK_MAXSTEP + 1], hqb_ck_e[HQB_CK_MAXSTEP + 1];   /* force and compression per step's start */
static float hqb_ck_qe[2][HQB_CK_NV];          /* the modes' q, q' at the contact's end (the tests) */
typedef float hqb_ck_v __attribute__((vector_size(16)));
typedef float hqb_ck_vu __attribute__((vector_size(16), aligned(4), may_alias));
static inline hqb_ck_v hqb_ck_ld(const float *p) { return *(const hqb_ck_vu *)p; }
static inline float hqb_ck_hsum(hqb_ck_v t) { HQ_FP_STRICT return (t[0] + t[1]) + (t[2] + t[3]); }

/* hammer.py's 16x grid: cells from a time in steps (hq_contact.c's rules, the same grid) */
static inline float hqb_ck_cross(uint32_t k, float ek, float ek1, float g)
{
    HQ_FP_STRICT
    float d = ek - ek1, f = d != 0.0f ? ek / d : 0.0f;
    if (f < 0.0f) f = 0.0f;
    if (f > 1.0f) f = 1.0f;
    return ((float)k + f) * g;
}
static inline uint32_t hqb_ck_c0(float T) { HQ_FP_STRICT float a = (float)(uint32_t)(2.0f * T); if (a < 2.0f * T) a += 1.0f; return (uint32_t)(a * 0.5f); }
static inline uint32_t hqb_ck_c1(float T)
{
    HQ_FP_STRICT
    float a = (float)(uint32_t)(2.0f * T);
    if (a < 2.0f * T) a += 1.0f;
    return (uint32_t)(a * 0.5f - 0.5f) + 1u;
}

/* the pulse from the force (hq_contact.c hq_ck_cells, the bars' window and rate): win16 the window in 16x cells */
static void hqb_ck_cells(uint32_t nst, float g, float win16, hqb_live_t *out)
{
    HQ_FP_STRICT
    float cells[BAR_PLEN], acc = 0.0f, mx = 0.0f, st, hw, prev;
    uint32_t nn = (uint32_t)(win16 + 0.5f), i, j;
    if (nn < 2u) nn = 2u;
    prev = hqb_ck_F[0];                        /* the integral at the steps (16x units), trapezoids */
    hqb_ck_F[0] = 0.0f;
    for (i = 1; i < nst; i++) { float f = hqb_ck_F[i]; acc += 0.5f * (prev + f) * g; prev = f; hqb_ck_F[i] = acc; }
    acc += 0.5f * prev * g;
    hqb_ck_F[nst] = acc;
    st = (float)(nn - 1u) / (float)(BAR_PLEN - 1u);
    hw = 0.5f * st;
    for (j = 0; j < BAR_PLEN; j++) {
        float x = (float)j * st, lo = x - hw, hi = x + hw, I[2], t[2];
        uint32_t q;
        lo = (lo < -0.5f ? -0.5f : lo > (float)nn - 0.5f ? (float)nn - 0.5f : lo) + 0.5f;
        hi = (hi < -0.5f ? -0.5f : hi > (float)nn - 0.5f ? (float)nn - 0.5f : hi) + 0.5f;
        t[0] = (lo - 0.25f) / g; t[1] = (hi - 0.25f) / g;
        for (q = 0; q < 2u; q++) {
            uint32_t k;
            if (t[q] <= 0.0f) { I[q] = 0.0f; continue; }
            k = (uint32_t)t[q];
            I[q] = k >= nst ? hqb_ck_F[nst] : hqb_ck_F[k] + (hqb_ck_F[k + 1u] - hqb_ck_F[k]) * (t[q] - (float)k);
        }
        cells[j] = (I[1] - I[0]) / (hi - lo > 1e-12f ? hi - lo : 1e-12f);
        if (cells[j] > mx) mx = cells[j];
    }
    for (j = 0; j < BAR_PLEN; j++) out->shape[j] = (int16_t)(cells[j] * 32767.0f / (mx > 1e-30f ? mx : 1e-30f) + 0.5f);
    out->shape[BAR_PLEN] = out->shape[BAR_PLEN + 1u] = 0;
}

/* the contact of law (HQB_CK_LAW) at velocity vel 1..127 on key (0..87) of type ty, from the bar's state (q0, qd0: V1 ..
 * V7, mass-normalised m and m/s; 0 at rest), into out; returns the steps run (0: no contact: out is not written) */
static uint32_t hqb_contact_run(uint32_t ty, uint32_t key, uint32_t law, uint32_t vel, const float *q0, const float *qd0,
                                hqb_live_t *out)
{
    HQ_FP_STRICT
    const hqb_ck_t *c = &HQB_CK[ty][key];
    const float K = HQB_CK_LK[ty][law], imh = 1.0f / c->mh, tc = HQB_CK_TC[ty][law][vel - 1u];
    const int p2 = HQB_CK_LP2[ty][law];
    float dtm = tc / HQB_CK_SPC, dt, g, Ch, Dh, C, D, iC, kc, Fc = 0.0f, rc = -1.0f, ec = 0.0f, tot = 0.0f, mxF = 0.0f;
    float fs_ = -1.0f, fe = -1.0f, apc[HQB_CK_NV], iw2[HQB_CK_NV];
    uint32_t oi = 0, n, m, touched = 0, nst = 0, on = 0;
    hqb_ck_v q[2], qd[2], p0 = hqb_ck_ld(c->pc), p1 = hqb_ck_ld(c->pc + 4);
    float xh, vh = HQB_CK_VH[ty][vel - 1u];
    const float (*st)[HQB_CK_NV];
    if (c->ftop > 0.0f && 1.0f / (HQB_CK_PPF * c->ftop) < dtm) dtm = 1.0f / (HQB_CK_PPF * c->ftop);
    while (oi + 1u < HQB_CK_NOS && 1.0f / (HQB_FS * HQB_CK_OS[oi]) > dtm) oi++;
    dt = 1.0f / (HQB_FS * HQB_CK_OS[oi]);
    g = 16.0f / HQB_CK_OS[oi];                 /* 16x cells a step */
    st = (const float (*)[HQB_CK_NV])c->st[oi];
    q[0] = hqb_ck_ld(q0); q[1] = hqb_ck_ld(q0 + 4); qd[0] = hqb_ck_ld(qd0); qd[1] = hqb_ck_ld(qd0 + 4);
    for (m = 0; m < HQB_CK_NV; m++) {
        apc[m] = c->pc[m] < 0.0f ? -c->pc[m] : c->pc[m];
        iw2[m] = c->w2[m] > 0.0f ? 1.0f / c->w2[m] : 0.0f;
    }
    xh = hqb_ck_hsum(p0 * q[0] + p1 * q[1]);  /* touching */
    Ch = dt * dt * imh * (1.0f / 6.0f); Dh = dt * imh * 0.5f;
    C = Ch + hqb_ck_hsum(p0 * hqb_ck_ld(st[3]) + p1 * hqb_ck_ld(st[3] + 4));
    D = Dh + hqb_ck_hsum(p0 * hqb_ck_ld(st[7]) + p1 * hqb_ck_ld(st[7] + 4));
    iC = 1.0f / C; kc = K * C;
    (void)D;
    for (n = 0; n < HQB_CK_MAXSTEP; n++) {
        float F0 = Fc, e0 = ec, E, Xh, Vh, F = 0.0f;
        hqb_ck_v u[2], w[2], f0 = {F0, F0, F0, F0}, f1;
        uint32_t h;
        for (h = 0; h < 2u; h++) {
            const uint32_t o = 4u * h;
            u[h] = (hqb_ck_ld(st[0] + o) * q[h] + hqb_ck_ld(st[1] + o) * qd[h]) + hqb_ck_ld(st[2] + o) * f0;
            w[h] = (hqb_ck_ld(st[4] + o) * q[h] + hqb_ck_ld(st[5] + o) * qd[h]) + hqb_ck_ld(st[6] + o) * f0;
        }
        Xh = xh + dt * vh - dt * dt * (2.0f * F0) * imh * (1.0f / 6.0f);
        Vh = vh - dt * F0 * imh * 0.5f;
        E = Xh - hqb_ck_hsum(p0 * u[0] + p1 * u[1]);
        ec = E;
        if (E > 0.0f) {
            float e1;
            if (p2) e1 = 2.0f * E / (1.0f + hqb_ck_sqrt(1.0f + 4.0f * kc * E));   /* (the stable root) */
            else {                             /* E - r^2 - kc r^3 = 0: decreasing and concave in r, Newton from the
                                                * right (the last root, then sqrt(E)) never overshoots */
                float rmax = hqb_ck_sqrt(E), r = rc > 0.0f && rc < rmax ? rc : rmax;
                uint32_t it;
                for (it = 0; it < 8u; it++) {
                    float r2 = r * r, H = (E - r2) - kc * r2 * r, dH = 2.0f * r + 3.0f * kc * r2, rn;
                    if (!(dH > 0.0f)) { r = rmax; continue; }
                    rn = r + H / dH;
                    if (rn > rmax) rn = rmax;
                    if (rn < 0.0f) rn = 0.0f;
                    if (rn - r <= 1e-5f * r && r - rn <= 1e-5f * r) { r = rn; break; }
                    r = rn;
                }
                rc = r;
                e1 = r * r;
            }
            F = (E - e1) * iC;
            if (F < 0.0f) F = 0.0f;
            ec = E - C * F;
        }
        f1 = (hqb_ck_v){F, F, F, F};
        for (h = 0; h < 2u; h++) {
            const uint32_t o = 4u * h;
            q[h] = u[h] + hqb_ck_ld(st[3] + o) * f1;
            qd[h] = w[h] + hqb_ck_ld(st[7] + o) * f1;
        }
        xh = Xh - Ch * F; vh = Vh - Dh * F;
        Fc = F;
        hqb_ck_F[n] = F0; hqb_ck_e[n] = e0;
        tot += F0;
        if (F0 > mxF) mxF = F0;
        nst = n + 1u;
        if (e0 > 0.0f && !on) {               /* a contact starts: the first strike again (a short gap), or a new one */
            on = 1;
            if (fe > 0.0f && (float)n - fe >= 0.5f * (fe - fs_)) break;
        } else if (!(e0 > 0.0f) && on) {
            on = 0;
            fe = (float)n;
            if (fs_ < 0.0f) fs_ = 0.0f;
        }
        if (e0 > 0.0f && fs_ < 0.0f) fs_ = (float)n;
        if (fe > 0.0f && !on && (float)n - fe >= 0.5f * (fe - fs_) && (float)n >= c->ratio[law] * fe + 2.0f)
            break;                             /* the first strike is complete and its window run */
        if (F0 > 0.0f) touched = 1;
        else if (touched && vh < 0.0f) {
            hqb_ck_v a0, a1;
            float A = 0.0f;
            if (ec < -2e-4f) break;            /* free, moving away, clearly clear of the bar */
            a0 = q[0] * q[0] + qd[0] * qd[0] * hqb_ck_ld(iw2);   /* never to touch again (hq_contact.c) */
            a1 = q[1] * q[1] + qd[1] * qd[1] * hqb_ck_ld(iw2 + 4);
            for (m = 0; m < 4u; m++) A += apc[m] * hqb_ck_sqrt(a0[m]) + apc[m + 4u] * hqb_ck_sqrt(a1[m]);
            if (xh + A < 0.0f) break;
        }
    }
    if (!touched) return 0;
    for (m = 0; m < 4u; m++) {
        hqb_ck_qe[0][m] = q[0][m]; hqb_ck_qe[0][m + 4u] = q[1][m];
        hqb_ck_qe[1][m] = qd[0][m]; hqb_ck_qe[1][m + 4u] = qd[1][m];
    }
    hqb_ck_e[nst] = -1.0f;
    {   /* the first strike's end (gen_rhodes.contact_segments on the 16x cells, as hq_contact.c) */
        uint32_t i = 0, have = 0, s0 = 0, e0c = 0;
        float seg = 0.0f, win16, play0, play, cap = HQB_CK_FTAU[ty] * 16.0f * HQB_FS / c->f0;
        while (i < nst) {
            uint32_t a, b, ca, cb;
            float part = 0.0f;
            while (i < nst && !(hqb_ck_e[i] > 0.0f)) i++;
            if (i >= nst) break;
            a = i;
            while (i < nst && hqb_ck_e[i] > 0.0f) { part += hqb_ck_F[i]; i++; }
            b = i;
            ca = hqb_ck_c0(a ? hqb_ck_cross(a - 1u, hqb_ck_e[a - 1u], hqb_ck_e[a], g) : 0.0f);
            cb = hqb_ck_c1(hqb_ck_cross(b - 1u, hqb_ck_e[b - 1u], hqb_ck_e[b], g));
            if (cb <= ca) cb = ca + 1u;
            if (have && (float)ca - (float)e0c < 0.5f * (float)(e0c - s0)) { e0c = cb; seg += part; continue; }
            if (have && seg >= 0.005f * tot) break;
            s0 = ca; e0c = cb; seg = part; have = 1;
        }
        win16 = c->ratio[law] * (float)e0c;
        hqb_ck_cells(nst, g, win16, out);
        play0 = win16 < cap ? win16 : cap;      /* the played window: T7g's velocity scale, T7f's cap (16x cells) */
        play = win16 * HQB_CK_VX[ty][vel - 1u];
        if (play > cap) play = cap;
        if (play < 1e-3f) play = 1e-3f;
        out->prate = (uint32_t)(64.0f / (play / 16.0f) * 4096.0f + 0.5f);
        out->fpk = mxF * hqb_ck_sqrt(play0 / play);
    }
    return nst;
}
