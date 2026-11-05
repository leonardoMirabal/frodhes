/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ bars (FR_HQ, the Move only): the code, included by bars.c. State and the overview: hq/bars_hq.h. Float (the
 * Move's A72 has an FPU); the same results on every host: no fused multiply-add, no reassociation (HQ_FP_STRICT
 * from hq/rhodes_hq.c, the pragmas below and the Move build's -ffp-contract=off), so Gate H's renders are the same on
 * the Mac, Linux and the Move. */
#if HQB_EXTRA
#include "hq/bars_hq_tables.h"
#endif
#if !defined(__clang__) && defined(__GNUC__)
#pragma GCC push_options
#pragma GCC optimize("fp-contract=off", "tree-vectorize", "no-predictive-commoning")
#endif
#if HQB_CONTACT
#include "hq/bars_contact.c"
#endif

/* the extra slots at the note-on (bars.c bar_init, after the 8 Classic slots): V5 V6 V7 T3 T4 from the key's HQ row
 * (their words as rh_mode_t, their b at the 17 TONE points: the key's own FE weights, so no band shape), with RING and
 * DAMPER as the other upper slots, the torsion only off the centreline (as T1 T2). key: the key's row (0..87) */
static void hqb_init(bar_voice_t *v, uint32_t key, uint32_t tyi, int32_t tone, uint32_t pi, uint32_t pf)
{
    uint32_t x;
    memset(&hqb_st[v - bv], 0, sizeof hqb_st[0]);
    v->hq = hq_mode != 0;
    for (x = 0; x < HQB_NX; x++) {
        bar_ms_t *ms = &v->m[BAR_NM + x];
        memset(ms, 0, sizeof *ms);
        ms->lpred = BAR_LZERO;
        ms->lo = 1;
        ms->fresh = 1;
#if HQB_EXTRA
        {
            const bar_type_t *ty = &BAR_TYPE[tyi];
            const rh_mode_t *km = &HQB_KEYS[tyi][key].m[x];
            const int32_t *bt = HQB_KEYS[tyi][key].b[x];
            int64_t b;
            int32_t lf;
            if (!v->hq || !km->th) continue;
            rh_coef(km, km->d, rh_tune_t, bar_k_decay, &ms->e0, &ms->eps0);
            rh_coef(km, km->drel, rh_tune_t, !(ty->flags & 2u) ? 65536 : bar_k_rel, &ms->e1, &ms->eps1);
            ms->e = ms->es = ms->e0; ms->eps = ms->epss = ms->eps0;
            b = pi < BAR_NPOS - 1u ? bt[pi] + ((((int64_t)bt[pi + 1u] - bt[pi]) * (int32_t)pf) >> 8) : bt[BAR_NPOS - 1u];
            if (x >= 3u) b = tone > 64 ? (b * ty->tors_db) >> 15 : 0;   /* torsion: off the centreline only */
            ms->b = b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
            bar_coefs(ms);
            ms->lsin = (int16_t)bar_lsin(ms);
            lf = bar_log2_32((uint32_t)km->th) - 20 * 256;
            ms->bin = (uint8_t)(lf <= 0 ? 0 : (lf * 3) >> 8 > 35 ? 35 : (lf * 3) >> 8);
            ms->live = ms->b != 0;
        }
#else
        (void)key; (void)tyi; (void)tone; (void)pi; (void)pf;
#endif
    }
}

/* the upper slots of an HQ voice over samples o .. o + n of the block (a multiple of 4; the force in those before
 * nf), into BAR_XT(j)[0], the coefficients fixed over them (bars.c calls it per sub-block while the damper moves).
 * Each lane is the coupled form y = y - (eps y + e x) + bq F, x = x + e y: x[n] = (2 - eps - e^2) x[n-1] - (1 - eps)
 * x[n-2] + e bq F[n], the integer form's poles and force gain (bq = b / 2^32: F << 12 times be = b e >> 30 >> 32).
 * A slot asleep or never lit is a lane at zero (its state cleared, so a relight starts from rest, as bar_strike's);
 * a slot fading (live 2) is weighted (32 - i) / 32 over the call's first sub-block, then cleared (bar_run_fade).
 * The extra slots (V5 .. T4) radiate their ringing and not their forced motion under the mallet. Their x less its
 * static part, x - bq F / e (= -(the second difference) / e^2: the mode's acceleration over omega^2), is the same x
 * once the force is gone; under it, a mode far above the mallet's band follows the force (x = bq F / e), and with
 * R1's (f / f1)^2 in b each would radiate the force pulse once more, scaled by its b (+8 dB on MARIMBA C4 at v127
 * with the three extra bending modes). What is left, the sub-resonant motion, is weighted r^3 over the contact (r
 * its progress, 0 .. 1): R1's weight is the mode's radiation at its own frequency, and a high mode moving at the
 * pulse's lower frequencies radiates far less (it spread the attack's click over the 1-2 kHz bands: STRIKE's L/3 to
 * EDGE distance on MARIMBA C4 2.9 dB with it, 3.0-3.1 without; test_marimba_cards_audible). The Classic slots keep
 * their x: their sound is Classic's, calibrated with it.
 * HQB_EXTRA 0 (the parity build): no extra slots, the integers' output within 2 LSB */
static hqb_f4 hqb_acc[BAR_N];                  /* hqb_upper's per-sample lane sums */

/* the recurrence over samples o .. end on nv packed vectors (1 .. 3; the compiler makes one loop per nv): the force
 * and the fade only where they are (the first loop), the rest the bare recurrence; each sample's lane vector into
 * hqb_acc. M: 1 on the extra slots' lanes (their weight under the force, r0 + dr j the pulse's progress). The
 * vectors are named (an array of them went through the stack every sample with gcc: store-to-load in
 * the recurrence, 3x the cost on the A72) */
static inline __attribute__((always_inline)) void hqb_run(const uint32_t nv, hqb_f4 *X, hqb_f4 *Y, const hqb_f4 *E,
    const hqb_f4 *P, const hqb_f4 *B, const hqb_f4 *G, const hqb_f4 *D, uint32_t o, uint32_t end, uint32_t fz,
    uint32_t fe, uint32_t nfade, const hqb_f4 *M, float r0, float dr)
{
    HQ_FP_STRICT
    const hqb_f4 one = {1.0f, 1.0f, 1.0f, 1.0f}, z = {0.0f, 0.0f, 0.0f, 0.0f};
    hqb_f4 x0 = X[0], y0 = Y[0], x1 = nv > 1u ? X[1] : z, y1 = nv > 1u ? Y[1] : z, x2 = nv > 2u ? X[2] : z,
           y2 = nv > 2u ? Y[2] : z;
    const hqb_f4 E0 = E[0], P0 = P[0], B0 = B[0], G0 = G[0], E1 = nv > 1u ? E[1] : z, P1 = nv > 1u ? P[1] : z,
                 B1 = nv > 1u ? B[1] : z, G1 = nv > 1u ? G[1] : z, E2 = nv > 2u ? E[2] : z, P2 = nv > 2u ? P[2] : z,
                 B2 = nv > 2u ? B[2] : z, G2 = nv > 2u ? G[2] : z;
    uint32_t j, e1 = nfade && fe > fz ? fe : fz;  /* the first loop's end */
    if (e1 > end) e1 = end;
    for (j = o; j < e1; j++) {
        hqb_f4 f, s;
        float F = j < fz ? (float)BAR_XT(j)[2] : 0.0f;
        f = (hqb_f4){F, F, F, F};
        y0 = y0 - (P0 * y0 + E0 * x0) + B0 * f; x0 = x0 + E0 * y0;
        if (nv > 1u) { y1 = y1 - (P1 * y1 + E1 * x1) + B1 * f; x1 = x1 + E1 * y1; }
        if (nv > 2u) { y2 = y2 - (P2 * y2 + E2 * x2) + B2 * f; x2 = x2 + E2 * y2; }
        if (nfade && j < fe) {
            const float t = (float)(j - o) * (1.0f / 32.0f);
            hqb_f4 tt = {t, t, t, t};
            s = (x0 - G0 * f) * (one - D[0] * tt);
            if (nv > 1u) s = s + (x1 - G1 * f) * (one - D[1] * tt);
            if (nv > 2u) s = s + (x2 - G2 * f) * (one - D[2] * tt);
        } else if (j < fz) {
            float rr = r0 + dr * (float)(j - o);
            hqb_f4 q;
            rr = rr > 1.0f ? 1.0f : rr < 0.0f ? 0.0f : rr;
            rr = 1.0f - rr * rr * rr;
            q = (hqb_f4){rr, rr, rr, rr};
            s = (x0 - G0 * f) * (one - M[0] * q);
            if (nv > 1u) s = s + (x1 - G1 * f) * (one - M[1] * q);
            if (nv > 2u) s = s + (x2 - G2 * f) * (one - M[2] * q);
        } else {
            s = x0;
            if (nv > 1u) s = s + x1;
            if (nv > 2u) s = s + x2;
        }
        hqb_acc[j] = s;
    }
    for (; j < end; j++) {                         /* (a zero force: + 0 is exact, as F 0 was) */
        hqb_f4 s;
        y0 = y0 - (P0 * y0 + E0 * x0); x0 = x0 + E0 * y0;
        s = x0;
        if (nv > 1u) { y1 = y1 - (P1 * y1 + E1 * x1); x1 = x1 + E1 * y1; s = s + x1; }
        if (nv > 2u) { y2 = y2 - (P2 * y2 + E2 * x2); x2 = x2 + E2 * y2; s = s + x2; }
        hqb_acc[j] = s;
    }
    X[0] = x0; Y[0] = y0;
    if (nv > 1u) { X[1] = x1; Y[1] = y1; }
    if (nv > 2u) { X[2] = x2; Y[2] = y2; }
}

static void hqb_upper(bar_voice_t *v, uint32_t o, uint32_t n, uint32_t nf)
{
    HQ_FP_STRICT
    hqb_st_t *h = &hqb_st[v - bv];
    hqb_f4 E[HQB_NVEC], P[HQB_NVEC], B[HQB_NVEC], D[HQB_NVEC], G[HQB_NVEC], X[HQB_NVEC], Y[HQB_NVEC];
    float *e = (float *)E, *p = (float *)P, *b = (float *)B, *d = (float *)D, *g = (float *)G, *x = (float *)X,
          *y = (float *)Y;
    uint8_t map[HQB_NL];
    uint32_t l, j, np = 0, nv, nfade = 0, end = o + n, fe = o + BAR_SUB, fz = nf < o + n ? nf : o + n;
    const float *acc = (const float *)hqb_acc;
    int32_t *xt = &BAR_XT(0)[0];
    for (l = 0; l < HQB_NL; l++) {                 /* the live slots, packed into the first lanes */
        uint32_t m = HQB_SLOT[l], q = l >> 2, r = l & 3u;
        const bar_ms_t *s = m < BAR_NM + HQB_NX ? &v->m[m] : 0;
        if (!s || !s->live) { h->x[q][r] = h->y[q][r] = 0.0f; continue; }
        e[np] = (float)s->e * (1.0f / 1073741824.0f);
        p[np] = (float)s->eps * (1.0f / 1073741824.0f);
        b[np] = (float)s->b * (1.0f / 4294967296.0f);
        d[np] = s->live == 2u ? 1.0f : 0.0f;
        g[np] = m >= BAR_NM && e[np] > 0.0f ? b[np] / e[np] : 0.0f;   /* the static part's gain (the extra slots) */
        x[np] = h->x[q][r]; y[np] = h->y[q][r];
        nfade += s->live == 2u;
        map[np++] = (uint8_t)l;
    }
    if (!np) return;
    nv = (np + 3u) >> 2;
    for (l = np; l < 4u * nv; l++) e[l] = p[l] = b[l] = d[l] = g[l] = x[l] = y[l] = 0.0f;
    {
        hqb_f4 M[HQB_NVEC];
        float *mm = (float *)M, r0, dr;
        uint32_t L = (uint32_t)BAR_PLEN << 16;
        for (l = 0; l < 4u * HQB_NVEC; l++) mm[l] = l < 4u * nv && g[l] != 0.0f ? 1.0f : 0.0f;
        dr = (float)v->prate / (float)L;
        r0 = ((float)v->ppos - (float)v->prate * (float)(nf - o)) / (float)L;   /* the pulse's progress at o (the
                                                    * force ran nf samples to ppos: bar_force) */
        if (nf <= o) r0 = 1.0f;
        if (nv == 1u) hqb_run(1u, X, Y, E, P, B, G, D, o, end, fz, fe, nfade, M, r0, dr);
        else if (nv == 2u) hqb_run(2u, X, Y, E, P, B, G, D, o, end, fz, fe, nfade, M, r0, dr);
        else hqb_run(3u, X, Y, E, P, B, G, D, o, end, fz, fe, nfade, M, r0, dr);
    }
    for (j = o; j < end; j++) {                    /* the sums and the rounding (vectorised: the same order) */
        float a = (acc[4u * j] + acc[4u * j + 1u]) + (acc[4u * j + 2u] + acc[4u * j + 3u]);
        xt[3u * j] += (int32_t)(a + __builtin_copysignf(0.5f, a));   /* (rounded half away: no branch) */
    }
    for (l = 0; l < np; l++) {                     /* the state back; a slot faded out is asleep (bar_run_fade) */
        uint32_t q = map[l] >> 2, r = map[l] & 3u;
        if (d[l] != 0.0f) {
            bar_ms_t *s = &v->m[HQB_SLOT[map[l]]];
            s->live = 0; s->x1 = s->x2 = s->err = 0; s->amp = 0;
            h->x[q][r] = h->y[q][r] = 0.0f;
        } else { h->x[q][r] = x[l]; h->y[q][r] = y[l]; }
    }
}

/* the lanes' state into the slots' x[n], x[n-1] (Q25, rounded) for bars.c's levels, sleep and kill (bar_lamp), at
 * the block's end; bar_sat's clamp applied to the lane too, as the integer slots are */
static void hqb_sync(bar_voice_t *v)
{
    HQ_FP_STRICT
    hqb_st_t *h = &hqb_st[v - bv];
    uint32_t l;
    const float lim = 268435455.0f;
    for (l = 0; l < HQB_NL; l++) {
        uint32_t m = HQB_SLOT[l], q = l >> 2, r = l & 3u;
        bar_ms_t *s;
        float e, xn, xp;
        if (m >= BAR_NM + HQB_NX || !v->m[m].live) continue;
        s = &v->m[m];
        e = (float)s->e * (1.0f / 1073741824.0f);
        xn = h->x[q][r];
        xp = xn - e * h->y[q][r];
        if (xn > lim || xn < -lim || xp > lim || xp < -lim) {
            xn = xn > lim ? lim : xn < -lim ? -lim : xn;
            xp = xp > lim ? lim : xp < -lim ? -lim : xp;
            h->x[q][r] = xn;
            h->y[q][r] = e > 0.0f ? (xn - xp) / e : 0.0f;
        }
        s->x1 = (int32_t)(xn >= 0.0f ? xn + 0.5f : xn - 0.5f);
        s->x2 = (int32_t)(xp >= 0.0f ? xp + 0.5f : xp - 0.5f);
        s->err = 0;
    }
}
#if HQB_CONTACT
/* the strike's pulse from the live contact (bars.c, after bar_load_pulse has made the table's: v->pbuf, prate, pamp):
 * restrike 1 a key struck again while its bar rings, 0 a note's first strike (MARIMBA's top keys only, from
 * HQB_CK_REST_LO). The laws of MALLET's family and the next (a blend family's two) each run from the bar's state
 * (V1's integer pair, the float lanes: their x and x[n-1] -> the mode's q and q'); their pulses are blended as the
 * tables are (the family's own blend, then MALLET's fraction), the rate likewise, and the force is the table's times
 * the live force scale over the at-rest one (HQB_CK_FPK): at rest the table's level. The table's pulse stays where
 * the bar hardly moves under the mallet (|v_bar| and w1 |x_bar| under 1 % of its speed), where the block already ran
 * HQB_CK_MAX contacts, or where a law found no contact. HAMMER as the tables: bar_mass on the rate, bar_massi (already
 * in v->pamp) on the force */
static uint32_t hqb_ck_n;                      /* contacts run this block (bar_render clears it) */
static void hqb_pulse(bar_voice_t *v, uint32_t vel, int restrike)
{
    HQ_FP_STRICT
    const uint32_t ty = bar_sub < BAR_NTYPE ? bar_sub : 0u, key = (uint32_t)(v->k - BAR_KEYS[ty]);
    const hqb_ck_t *c = &HQB_CK[ty][key];
    hqb_st_t *h = &hqb_st[v - bv];
    static hqb_live_t lv[4];
    float q[HQB_CK_NV], qd[HQB_CK_NV], wl[4], acc[BAR_PLEN + 2u], fl = 0.0f, fr = 0.0f, pr = 0.0f, r;
    uint32_t lw[4], nl = 0, fam = (uint32_t)bar_fam, f = fam >> 8, i, j, vi, m;
    int32_t a = (int32_t)(fam & 255u);
    float fv;
    if (!v->hq || vel < 1u || vel > 127u) return;
    if (!restrike && key + BAR_KEY_LO < HQB_CK_REST_LO[ty]) return;
    if (f > BAR_NFAM - 1u) { f = BAR_NFAM - 1u; a = 0; }
    for (j = 0; j < 2u; j++) {                 /* the laws and their weights: MALLET's family (256 - a), the next (a) */
        uint32_t ff = j ? (f + 1u < BAR_NFAM ? f + 1u : f) : f;
        float wf = (j ? (float)a : (float)(256 - a)) * (1.0f / 256.0f);
        const uint8_t *L = HQB_CK_FLAW[ty][ff];
        if (wf <= 0.0f) continue;
        if (L[1] == 255u) { lw[nl] = L[0]; wl[nl++] = wf; continue; }
        {
            float b = (float)L[2] * (1.0f / 256.0f);   /* a blend family: its two laws at its own a */
            if (b < 1.0f) { lw[nl] = L[0]; wl[nl++] = wf * (1.0f - b); }
            if (b > 0.0f) { lw[nl] = L[1]; wl[nl++] = wf * b; }
        }
    }
    if (!nl || hqb_ck_n + nl > HQB_CK_MAX) return;
    for (m = 0; m < HQB_CK_NV; m++) q[m] = qd[m] = 0.0f;
    if (restrike) {                            /* the bar's state: x, x[n-1] (Q25) -> q, q' at the strike line */
        static const int8_t LANE[HQB_CK_NV] = {-1, 0, 1, 2, 5, 6, 7, -2};   /* V1 (m[0]), V2..V4, V5..V7; -2 none */
        float xc = 0.0f, vc = 0.0f, vh = HQB_CK_VH[ty][vel - 1u];
        for (m = 0; m < HQB_CK_NV; m++) {
            float x1, x2;
            int l = LANE[m];
            if (l == -2 || c->gq[m] == 0.0f || c->sth[m] <= 0.0f) continue;
            if (l == -1) { x1 = (float)v->m[0].x1; x2 = (float)v->m[0].x2; }
            else {
                const bar_ms_t *s = &v->m[HQB_SLOT[l]];
                if (HQB_SLOT[l] >= BAR_NM + HQB_NX || !s->live) continue;
                x1 = h->x[l >> 2][l & 3];
                x2 = x1 - (float)s->e * (1.0f / 1073741824.0f) * h->y[l >> 2][l & 3];
            }
            q[m] = c->gq[m] * x1;
            qd[m] = c->gq[m] * (c->th[m] * HQB_FS) * (x1 * c->cth[m] - x2) / c->sth[m];
            xc += c->pc[m] * q[m];
            vc += c->pc[m] * qd[m];
        }
        if (vc * vc + c->w2[0] * (xc * xc) < (0.01f * vh) * (0.01f * vh)) return;   /* still: the table's own */
    }
    for (i = 0; i < nl; i++)
        if (!hqb_contact_run(ty, key, lw[i], vel, q, qd, &lv[i])) return;
    hqb_ck_n += nl;
    vi = (vel - 1u) >> 3;                       /* HQB_CK_FPK's points: 1, 9 .. 121, 127 */
    if (vi > HQB_CK_NFV - 2u) vi = HQB_CK_NFV - 2u;
    fv = (float)(vel - HQB_CK_FV[vi]) / (float)(HQB_CK_FV[vi + 1u] - HQB_CK_FV[vi]);
    for (j = 0; j < BAR_PLEN + 2u; j++) acc[j] = 0.0f;
    for (i = 0; i < nl; i++) {
        const float *P = HQB_CK_FPK[ty][key][lw[i]];
        for (j = 0; j < BAR_PLEN + 2u; j++) acc[j] += wl[i] * (float)lv[i].shape[j];
        fl += wl[i] * lv[i].fpk;
        fr += wl[i] * (P[vi] + (P[vi + 1u] - P[vi]) * fv);
        pr += wl[i] * (float)lv[i].prate;
    }
    for (j = 0; j < BAR_PLEN + 2u; j++) v->pbuf[j] = (int16_t)(acc[j] + 0.5f);
    {
        uint32_t p = (uint32_t)(pr + 0.5f);
        v->prate = (uint32_t)(((uint64_t)(p << 4) * (uint32_t)bar_mass) >> 12);
        if (!v->prate) v->prate = 1u;
    }
    r = fr > 0.0f ? (float)v->pamp * (fl / fr) : (float)v->pamp;
    v->pamp = r > 65535.0f ? 65535 : (int32_t)(r + 0.5f);
}
#endif
#if !defined(__clang__) && defined(__GNUC__)
#pragma GCC pop_options
#endif
