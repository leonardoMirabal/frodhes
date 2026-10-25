/* SPDX-License-Identifier: GPL-3.0-only */
/* Reference: the 1x integer kernels of TSH8, BHLUE and OHCD as the private design work left them (their loops copied as
 * they were; the tables passed in instead of included), run on whole 256-sample blocks. The firmware's drv_tsh8.c /
 * drv_bhlue.c / drv_ohcd.c must give the same integers on their own blocks (tests/py/test_drive_kernels.py). Host
 * only. */
#include <stdint.h>

#define M(a, b) ((int64_t)(a) * (b))

/* ---- TSH8 ---- */
#define TS_FINE_SH 7
#define TS_FINE_END (2 << 14)
#define TS_COARSE_SH 12
#define TS_NFINE (TS_FINE_END >> TS_FINE_SH)
#define TS_UEND (64 << 14)
typedef struct { int32_t b0, b1, b2, a1, a2; } ts_bq;

static void ts_hp2q(const int32_t *hc, int32_t *st, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t b0 = hc[0], a1 = hc[1], x1 = st[0], y1 = st[1];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0], xb = in[1], ya;
        ya = (int32_t)((M(b0, xa - x1) + M(a1, y1)) >> 28);
        y1 = (int32_t)((M(b0, xb - xa) + M(a1, ya)) >> 28);
        x1 = xb;
        out[0] = ya; out[1] = y1;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = y1;
}

static void ts_br2q(const ts_bq *c, int32_t *st, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0], xb = in[1], ya, yb;
        int64_t pa, pb;
        int32_t k;
        k = c->b2; pa = M(k, x2); pb = M(k, x1);
        k = c->b1; pa += M(k, x1); pb += M(k, xa);
        k = c->b0; pa += M(k, xa); pb += M(k, xb);
        k = c->a2; pa += M(k, y2); pb += M(k, y1);
        k = c->a1; pa += M(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += M(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        out[0] = ya >> 4; out[1] = yb >> 4;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

static inline int32_t ts_g(const int32_t *g, int32_t x)
{
    int32_t a = x < 0 ? -x : x, k, fr, g0, v;
    a = a > TS_UEND - 1 ? TS_UEND - 1 : a;
    if (a < TS_FINE_END) {
        k = a >> TS_FINE_SH; fr = a & ((1 << TS_FINE_SH) - 1); g0 = g[k];
        v = g0 + (((g[k + 1] - g0) * fr) >> TS_FINE_SH);
    } else {
        k = TS_NFINE + ((a - TS_FINE_END) >> TS_COARSE_SH); fr = (a - TS_FINE_END) & ((1 << TS_COARSE_SH) - 1);
        g0 = g[k]; v = g0 + (((g[k + 1] - g0) * fr) >> TS_COARSE_SH);
    }
    return x < 0 ? -v : v;
}

static void ts_tab1(const int32_t *g, int32_t *u, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) u[i] = ts_g(g, u[i]);
}

static int ts_osh;                                        /* the port's one addition: the output shifted (TSH8_OSH) */
static void ts_tone2qi(const ts_bq *c, int32_t *st, const int32_t *vi, int32_t *v, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = vi[0] + (v[0] << 9), xb = vi[1] + (v[1] << 9), ya, yb, k;
        int64_t pa, pb;
        k = c->b2; pa = M(k, x2); pb = M(k, x1);
        k = c->b1; pa += M(k, x1); pb += M(k, xa);
        k = c->b0; pa += M(k, xa); pb += M(k, xb);
        k = c->a2; pa += M(k, y2); pb += M(k, y1);
        k = c->a1; pa += M(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += M(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        v[0] = ya >> ts_osh; v[1] = yb >> ts_osh;
        vi += 2; v += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

/* xi (volts Q24) -> out, len a multiple of 2, blocks of 256 from zero state */
void ref_tsh8(const int32_t *hc, const int32_t *cb, const int32_t *ct, const int32_t *g, const int32_t *xi, int32_t *out,
              uint32_t len, int osh)
{
    int32_t s1[2] = {0, 0}, s2[4] = {0, 0, 0, 0}, s3[4] = {0, 0, 0, 0}, vi[256];
    uint32_t k, i;
    ts_osh = osh;
    for (k = 0; k < len; k += 256) {
        uint32_t n = len - k < 256 ? len - k : 256;
        for (i = 0; i < n; i++) out[k + i] = 0;
        ts_hp2q(hc, s1, xi + k, vi, n);
        ts_br2q((const ts_bq *)cb, s2, vi, out + k, n);
        ts_tab1(g, out + k, n);
        ts_tone2qi((const ts_bq *)ct, s3, vi, out + k, n);
    }
}

/* ---- BHLUE ---- */
typedef struct {
    int32_t pa, ph;
    int32_t b0, b1, b2, a1, a2;
    int32_t c0, c1, c2, d1, d2;
} bh_coef_t;
typedef struct { int32_t xa1, xo, x1, h1, h2, u1, u2, w1, w2, z1, z2, up; } bh_state_t;

static const int32_t *BH_V, *BH_D;

static inline int32_t bh_clip(int32_t us)
{
    int32_t a = us < 0 ? -us : us, i = a >> 12, f = a & 4095, v;
    if (i > 1024) i = 1024;
    v = BH_V[i] + ((BH_D[i] * f) >> 12);
    return us < 0 ? -v : v;
}

static inline void bh_post2q(const bh_coef_t *k, bh_state_t *st, int32_t *m, unsigned n)
{
    int32_t w1 = st->w1, w2 = st->w2, z1 = st->z1, z2 = st->z2;
    unsigned j;
    for (j = n >> 1; j; j--) {
        int32_t va = m[0], vb = m[1], za, zb, c;
        int64_t pa, pb;
        c = k->c2; pa = M(c, w2); pb = M(c, w1);
        c = k->c1; pa += M(c, w1); pb += M(c, va);
        c = k->c0; pa += M(c, va); pb += M(c, vb);
        pa <<= 10; pb <<= 10;
        c = k->d2; pa -= M(c, z2); pb -= M(c, z1);
        c = k->d1; pa -= M(c, z1);
        za = (int32_t)(pa >> 28);
        pb -= M(c, za);
        zb = (int32_t)(pb >> 28);
        w2 = va; w1 = vb; z2 = za; z1 = zb;
        m[0] = za >> 8; m[1] = zb >> 8;
        m += 2;
    }
    st->w1 = w1; st->w2 = w2; st->z1 = z1; st->z2 = z2;
}

static inline void bh_hps2q(const bh_coef_t *k, bh_state_t *st, int32_t *m, unsigned n)
{
    int32_t xa1 = st->xa1, x1 = st->x1, h1 = st->h1;
    unsigned j;
    for (j = n >> 1; j; j--) {
        int32_t xa = m[0] << 12, xb = m[1] << 12, ga, gb, ha, hb, c;
        c = k->pa;
        ga = (xa - xa1) + (int32_t)(M(c, x1) >> 30);
        gb = (xb - xa) + (int32_t)(M(c, ga) >> 30);
        c = k->ph;
        ha = (ga - x1) + (int32_t)(M(c, h1) >> 30);
        hb = (gb - ga) + (int32_t)(M(c, ha) >> 30);
        xa1 = xb; x1 = gb; h1 = hb;
        m[0] = ha; m[1] = hb;
        m += 2;
    }
    st->xa1 = xa1; st->x1 = x1;
}

static inline void bh_pre2q(const bh_coef_t *k, bh_state_t *st, int32_t *m, unsigned n)
{
    int32_t h1 = st->h1, h2 = st->h2, u1 = st->u1, u2 = st->u2;
    unsigned j;
    for (j = n >> 1; j; j--) {
        int32_t ha = m[0], hb = m[1], ua, ub, c;
        int64_t pa, pb;
        c = k->b2; pa = M(c, h2); pb = M(c, h1);
        c = k->b1; pa += M(c, h1); pb += M(c, ha);
        c = k->b0; pa += M(c, ha); pb += M(c, hb);
        pa <<= 6; pb <<= 6;
        c = k->a2; pa -= M(c, u2); pb -= M(c, u1);
        c = k->a1; pa -= M(c, u1);
        ua = (int32_t)(pa >> 28);
        pb -= M(c, ua);
        ub = (int32_t)(pb >> 28);
        h2 = ha; h1 = hb; u2 = ua; u1 = ub;
        m[0] = ua >> 4; m[1] = ub >> 4;
        m += 2;
    }
    st->h1 = h1; st->h2 = h2; st->u1 = u1; st->u2 = u2;
}

static inline void bh_clip1(int32_t *m, unsigned n)
{
    unsigned j;
    for (j = 0; j < n; j++) m[j] = bh_clip(m[j]);
}

static void bh_run_g(const bh_coef_t *k, bh_state_t *st, int32_t *m, unsigned n)
{
    bh_hps2q(k, st, m, n);
    bh_pre2q(k, st, m, n);
    bh_clip1(m, n);
    bh_post2q(k, st, m, n);
}

/* x: the input already clipped to +-2^18 (as the private model converts it), in place, blocks of 256 */
void ref_bhlue(const int32_t *k12, const int32_t *v, const int32_t *d, int32_t *m, uint32_t len)
{
    bh_coef_t k;
    bh_state_t st = {0};
    uint32_t i;
    k.pa = k12[0]; k.ph = k12[1]; k.b0 = k12[2]; k.b1 = k12[3]; k.b2 = k12[4]; k.a1 = k12[5]; k.a2 = k12[6];
    k.c0 = k12[7]; k.c1 = k12[8]; k.c2 = k12[9]; k.d1 = k12[10]; k.d2 = k12[11];
    BH_V = v; BH_D = d;
    for (i = 0; i < len; i += 256) bh_run_g(&k, &st, m + i, len - i < 256 ? len - i : 256);
}

/* ---- OHCD: the kernel with anti-aliasing (the private OHCD AA prototype: S1 + shift-only input HP | the clipper with
 * divide-free ADAA-1 (AA_SF 2); then, full range (0.9.5): S2 x Q | the IC1B rail with ADAA-1 | the tone x 1 / cos),
 * its passes as written there (direct form I, one loop per stage); the tables (curve, its antiderivative, the
 * reciprocal) passed in ---- */
typedef struct {
    const int32_t *s1;
    int32_t ix1, iy1;
    int32_t s1x1, s1x2, s1y1, s1y2;
} ohr_t;
typedef struct { ohr_t b; int32_t x1, F1; } ohr_aa_t;
#define OHR_RAIL (1 << 17)
#define OHR_CSH 11
#define OHR_QS1 28
#define OHR_QS2AA 24
#define AA_SF 2
#define OA_TH (1 << AA_SF)
#define VSH 12                                   /* the port's change in the clipper: its output x 2^12 (the
                                                  * quotient's next 12 bits kept, for the joined section after it) */
static const int16_t *OHR_CF;
static const int32_t *OHR_CFF;
static const uint16_t *OA_RCP0;                  /* the reciprocal table less its first 2^(RB-1) (RB 11) */
#define BQPASS(c, st, qs, PRE, POST) do { \
        int32_t b0 = (c)[0], b1 = (c)[1], b2 = (c)[2], a1 = -(c)[3], a2 = -(c)[4]; \
        int32_t x1 = (st)[0], x2 = (st)[1], y1 = (st)[2], y2 = (st)[3]; \
        for (i = 0; i < n; i++) { \
            int32_t x = PRE(m[i]), y; \
            int64_t acc_ = (int64_t)b0 * x; acc_ += (int64_t)b1 * x1; acc_ += (int64_t)b2 * x2; \
            acc_ += (int64_t)a1 * y1; acc_ += (int64_t)a2 * y2;   /* one accumulator, a negated */ \
            y = (int32_t)(acc_ >> (qs)); x2 = x1; x1 = x; y2 = y1; y1 = y; m[i] = POST(y); } \
        (st)[0] = x1; (st)[1] = x2; (st)[2] = y1; (st)[3] = y2; } while (0)
#define ID(v) (v)
#define HPPRE(v) (lp += (((v) << 8) - lp) >> 9, ((v) << 8) - lp)
static void ohr_s1hp_pass(ohr_t *s, int32_t *m, unsigned n)
{
    int32_t lp = s->iy1;
    unsigned i;
    BQPASS(s->s1, &s->s1x1, OHR_QS1, HPPRE, ID);
    s->iy1 = lp;
}
static void ohr_clipaa_pass(ohr_aa_t *sa, int32_t *m, unsigned n)
{
    int32_t e1 = sa->x1, F1 = sa->F1;
    const uint16_t *rcp = OA_RCP0;
    unsigned i;
    for (i = 0; i < n; i++) {
        int32_t y = m[i], a, k, t, f0, df, pr, F, d, v;
        uint32_t ad;
        y = y < -OHR_RAIL ? -OHR_RAIL : y;
        y = y > OHR_RAIL ? OHR_RAIL : y;
        a = y < 0 ? -y : y;
        k = a >> OHR_CSH; t = a & ((1 << OHR_CSH) - 1);
        f0 = OHR_CF[k]; df = OHR_CF[k + 1] - f0;
        pr = df * t;
        F = OHR_CFF[k] + ((t * (2 * f0 + (pr >> OHR_CSH))) >> 3);
        d = y - e1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (__builtin_expect(ad >= OA_TH, 1)) {
            int nz = __builtin_clz(ad);
            uint32_t r = rcp[(ad << nz) >> (32 - 11)];
            int32_t q = (int32_t)(((int64_t)(F - F1) * (int64_t)r) >> (47 - AA_SF - VSH - nz));
            v = d < 0 ? -q : q;
        } else {
            int32_t gm = f0 + (pr >> OHR_CSH);
            v = ((y < 0 ? -gm : gm) - ((df * d) >> (OHR_CSH + 1))) << VSH;
        }
        e1 = y; F1 = F;
        m[i] = v;
    }
    sa->x1 = e1; sa->F1 = F1;
}
/* the full-range kernel's stages after the clipper (0.9.5): stage 2 x Q (direct form I, u x 2^12 in, u x 2^8 out),
 * the IC1B rail with ADAA-1, the tone x 1 / cos (direct form I, W = the bus x 2^4 inside, the bus out rounded), one
 * sample a loop. The rail as its formula reads (the mean inside, the rail past it on one side, the mean less
 * (E(v) - E(p)) / (v - p) at a crossing, E(x) = (|x| - R)^2 / 2, in u; the quotient by the reciprocal table) */
#define OHR_QS2R 28
#define OHR_RAIL2 (1 << 25)
static int32_t ohr_rail(int32_t v, int32_t p)
{
    int32_t R = OHR_RAIL2, ev, ep, d, q, mean = (v + p) >> 1;
    uint32_t ad;
    int nz;
    int64_t num;
    if ((v < 0 ? -v : v) <= R && (p < 0 ? -p : p) <= R) return mean;
    if (v > R && p > R) return R;
    if (v < -R && p < -R) return -R;
    ev = (v < 0 ? -v : v) - R; ev = ev > 0 ? ev >> 8 : 0;
    ep = (p < 0 ? -p : p) - R; ep = ep > 0 ? ep >> 8 : 0;
    d = v - p;
    ad = (uint32_t)(d < 0 ? -d : d) >> 8;
    q = 0;
    if (ad) {
        nz = __builtin_clz(ad);
        num = (int64_t)ev * ev - (int64_t)ep * ep;              /* 2 (E(v) - E(p)) in u^2 */
        q = (int32_t)((num * (int64_t)OA_RCP0[(ad << nz) >> (32 - 11)]) >> (48 - nz));   /* / (2 |d|), in u */
        q *= 256;
    }
    q = mean - (d < 0 ? -q : q);
    return q > R ? R : q < -R ? -R : q;
}
typedef struct { int32_t x1, x2, y1, y2, rp, t1, t2, w1, w2; } ohr_b_t;
static void ohr_after_pass(ohr_b_t *b, const int32_t *s2, const int32_t *o, int32_t *m, unsigned n)
{
    unsigned i;
    for (i = 0; i < n; i++) {
        int64_t acc = (int64_t)s2[0] * m[i] + (int64_t)s2[1] * b->x1 + (int64_t)s2[2] * b->x2 -
                      (int64_t)s2[3] * b->y1 - (int64_t)s2[4] * b->y2;
        int32_t v = (int32_t)(acc >> OHR_QS2R), t, w;
        b->x2 = b->x1; b->x1 = m[i]; b->y2 = b->y1; b->y1 = v;
        t = ohr_rail(v, b->rp);
        b->rp = v;
        acc = (int64_t)o[0] * t + (int64_t)o[1] * b->t1 + (int64_t)o[2] * b->t2 - (int64_t)o[3] * b->w1 -
              (int64_t)o[4] * b->w2;
        w = (int32_t)(acc >> 28);
        b->t2 = b->t1; b->t1 = t; b->w2 = b->w1; b->w1 = w;
        m[i] = (w + 8) >> 4;
    }
}
/* s1[5] (b0 b1 b2 a1 a2, as the private table: a not negated), s2[5] stage 2 x Q (the same), o[5] the tone x 1 / cos
 * (b x the level, a not negated), cf[66] the curve, cff[65] its antiderivative (u^2 / 4), rcp[1024] the reciprocal
 * table; x: the bus (Q15), in place, blocks of 256 from zero state. Through the clipper: the private prototype (the
 * OHCD AA prototype) as written */
void ref_ohcd(const int32_t *s1, const int32_t *s2, const int32_t *o, const int16_t *cf, const int32_t *cff,
              const uint16_t *rcp, int32_t *m, uint32_t len)
{
    static ohr_aa_t s;
    static ohr_b_t b;
    uint32_t k;
    for (k = 0; k < sizeof s; k++) ((char *)&s)[k] = 0;
    for (k = 0; k < sizeof b; k++) ((char *)&b)[k] = 0;
    OHR_CF = cf; OHR_CFF = cff; OA_RCP0 = rcp - 1024;
    s.b.s1 = s1;
    for (k = 0; k < len; k += 256) {
        unsigned n = len - k < 256 ? len - k : 256;
        ohr_s1hp_pass(&s.b, m + k, n);
        ohr_clipaa_pass(&s, m + k, n);
        ohr_after_pass(&b, s2, o, m + k, n);
    }
}
/* the rail alone: y[i] = rail(v[i], v[i - 1]) (v[-1] = 0) */
void ref_ohcd_rail(const int32_t *v, int32_t *y, uint32_t n, const uint16_t *rcp)
{
    uint32_t i;
    OA_RCP0 = rcp - 1024;
    for (i = 0; i < n; i++) y[i] = ohr_rail(v[i], i ? v[i - 1] : 0);
}

/* ---- TSH8 with anti-aliasing: the private design's loops (ts_in2qi, ts_br2qa, ts_aa1h, ts_tone2qi; AA_SF 5, the
 * uint16 reciprocal table), as they were; the port's additions: the input scale (the caller passes x 154), the
 * tone's output shift (osh) and the table guard at 128 V (never reached by these signals) */
static void ts_in2qi(const ts_bq *c, int32_t *st, int32_t *io, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = io[0], xb = io[1], ya, yb;
        int64_t pa, pb;
        int32_t k;
        k = c->b2; pa = M(k, x2); pb = M(k, x1);
        k = c->b1; pa += M(k, x1); pb += M(k, xa);
        k = c->b0; pa += M(k, xa); pb += M(k, xb);
        k = c->a2; pa += M(k, y2); pb += M(k, y1);
        k = c->a1; pa += M(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += M(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        io[0] = ya; io[1] = yb;
        io += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

static void ts_br2qa(const ts_bq *c, int32_t *st, int32_t *in, int32_t *out, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0], xb = in[1], ya, yb;
        int64_t pa, pb;
        int32_t k;
        k = c->b2; pa = M(k, x2); pb = M(k, x1);
        k = c->b1; pa += M(k, x1); pb += M(k, xa);
        k = c->b0; pa += M(k, xa); pb += M(k, xb);
        k = c->a2; pa += M(k, y2); pb += M(k, y1);
        k = c->a1; pa += M(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += M(k, ya);
        yb = (int32_t)(pb >> 28);
        in[0] = (xa + x1) >> 1; in[1] = (xb + xa) >> 1;
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        out[0] = ya >> 4; out[1] = yb >> 4;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

#define RAA_SF 5
#define RAA_T (1 << RAA_SF)
#define RAA_RB 11
static void ts_aa1h(const int32_t *gf, const uint16_t *rcp0, int32_t *st, int32_t *u, uint32_t n)
{
    const int32_t *gc = gf + 2 * (TS_NFINE - (TS_FINE_END >> TS_COARSE_SH));
    const uint16_t *rcp = rcp0 - (1 << (RAA_RB - 1));
    int32_t x1 = st[0], F1 = st[1];
    uint32_t i;
    for (i = n; i; i--) {
        int32_t x = u[0], a, fr, g0, dg, p, F, d, hd, v;
        uint32_t ad;
        const int32_t *e;
        a = x < 0 ? -x : x;
        a = a > (128 << 14) - 1 ? (128 << 14) - 1 : a;
        if (a < TS_FINE_END) {
            e = gf + 2 * (a >> TS_FINE_SH); fr = a & ((1 << TS_FINE_SH) - 1); hd = TS_FINE_SH + 1;
        } else {
            e = gc + 2 * (a >> TS_COARSE_SH); fr = a & ((1 << TS_COARSE_SH) - 1); hd = TS_COARSE_SH + 1;
        }
        g0 = e[0]; dg = e[2] - g0; p = dg * fr;
        F = e[1] + ((g0 * fr + (int32_t)(((uint32_t)p * (uint32_t)fr) >> hd)) >> RAA_SF);
        d = x - x1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (ad < RAA_T) {
            int32_t v_ = g0 + (p >> (hd - 1));
            v_ = x < 0 ? -v_ : v_;
            v = v_ - ((dg * d) >> hd);
        } else {
            int nz = __builtin_clz(ad);
            int32_t r = rcp[(ad << nz) >> (32 - RAA_RB)];
            int32_t df = F - F1;
            if (d < 0) df = -df;
            v = (int32_t)(M(df, r) >> (47 - RAA_SF - nz));
        }
        x1 = x; F1 = F;
        *u++ = v;
    }
    st[0] = x1; st[1] = F1;
}

void ref_tsh8_aa(const int32_t *ci, const int32_t *cb, const int32_t *ct, const int32_t *gf, const uint16_t *rcp,
                 const int32_t *xi, int32_t *out, uint32_t len, int osh)
{
    int32_t s1[4] = {0}, s2[4] = {0}, s3[2] = {0}, s4[4] = {0}, vi[256];
    uint32_t k, i;
    ts_osh = osh;
    for (k = 0; k < len; k += 256) {
        uint32_t n = len - k < 256 ? len - k : 256;
        for (i = 0; i < n; i++) vi[i] = xi[k + i];
        ts_in2qi((const ts_bq *)ci, s1, vi, n);
        ts_br2qa((const ts_bq *)cb, s2, vi, out + k, n);
        ts_aa1h(gf, rcp, s3, out + k, n);
        ts_tone2qi((const ts_bq *)ct, s4, vi, out + k, n);
    }
}

/* ---- BHLUE with anti-aliasing: the private design's loops (bh_hps2qs, bh_pre2qc, bh_aa1j, bh_post2q; AA_SF 9, the
 * uint16 reciprocal table), as they were; x already clipped to +-2^18 by the caller (as the design converts it) */
#define BH_XMAX ((64 << 16) - 1)
static void bh_hps2qs(const bh_coef_t *k, const int32_t *sh, bh_state_t *st, int32_t *m, unsigned n)
{
    int32_t xa1 = st->xa1, x1 = st->x1, h1 = st->xo, y1 = st->up;
    unsigned j;
    for (j = n >> 1; j; j--) {
        int32_t xa = m[0] << 12, xb = m[1] << 12, ga, gb, ha, hb, ya, c;
        c = k->pa;
        ga = (xa - xa1) + (int32_t)(M(c, x1) >> 30);
        gb = (xb - xa) + (int32_t)(M(c, ga) >> 30);
        c = k->ph;
        ha = (ga - x1) + (int32_t)(M(c, h1) >> 30);
        hb = (gb - ga) + (int32_t)(M(c, ha) >> 30);
        ya = (int32_t)((M(sh[0], ha) + M(sh[1], h1) + M(sh[2], y1)) >> 28);
        y1 = (int32_t)((M(sh[0], hb) + M(sh[1], ha) + M(sh[2], ya)) >> 28);
        xa1 = xb; x1 = gb; h1 = hb;
        m[0] = ya; m[1] = y1;
        m += 2;
    }
    st->xa1 = xa1; st->x1 = x1; st->xo = h1; st->up = y1;
}
static void bh_pre2qc(const bh_coef_t *k, bh_state_t *st, int32_t *m, unsigned n)
{
    int32_t h1 = st->h1, h2 = st->h2, u1 = st->u1, u2 = st->u2;
    unsigned j;
    for (j = n >> 1; j; j--) {
        int32_t ha = m[0], hb = m[1], ua, ub, c, sa, sb;
        int64_t pa, pb;
        c = k->b2; pa = M(c, h2); pb = M(c, h1);
        c = k->b1; pa += M(c, h1); pb += M(c, ha);
        c = k->b0; pa += M(c, ha); pb += M(c, hb);
        pa <<= 6; pb <<= 6;
        c = k->a2; pa -= M(c, u2); pb -= M(c, u1);
        c = k->a1; pa -= M(c, u1);
        ua = (int32_t)(pa >> 28);
        pb -= M(c, ua);
        ub = (int32_t)(pb >> 28);
        h2 = ha; h1 = hb; u2 = ua; u1 = ub;
        sa = ua >> 4; sb = ub >> 4;
        sa = sa > BH_XMAX ? BH_XMAX : sa; sa = sa < -BH_XMAX ? -BH_XMAX : sa;
        sb = sb > BH_XMAX ? BH_XMAX : sb; sb = sb < -BH_XMAX ? -BH_XMAX : sb;
        m[0] = sa; m[1] = sb;
        m += 2;
    }
    st->h1 = h1; st->h2 = h2; st->u1 = u1; st->u2 = u2;
}
static void bh_aa1j(const int64_t *gf, const uint16_t *rcp0, int32_t *st2, int32_t *m, unsigned n)
{
    const uint16_t *rcp = rcp0 - (1 << (RAA_RB - 1));
    int32_t x1 = st2[0], F1 = st2[1];
    unsigned j;
    for (j = n; j; j--) {
        int32_t x = m[0], a, fr, g0, dg, p, F, d, v;
        uint32_t ad;
        int64_t w_;
        a = x < 0 ? -x : x;
        w_ = gf[a >> 12]; fr = a & 4095;
        g0 = (int32_t)w_; dg = (int32_t)gf[(a >> 12) + 1] - g0; p = dg * fr;
        F = (int32_t)(w_ >> 32) + (int32_t)(M(fr, (g0 << 13) + p) >> (13 + 9));
        d = x - x1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (ad < (1 << 9)) {
            int32_t v_ = g0 + (p >> 12);
            v_ = x < 0 ? -v_ : v_;
            v = v_ - ((dg * d) >> 13);
        } else {
            int nz = __builtin_clz(ad);
            int32_t r = (int32_t)rcp[(ad << nz) >> (32 - RAA_RB)];
            int32_t df_ = F - F1;
            if (d < 0) df_ = -df_;
            v = (int32_t)(M(df_, r) >> (47 - 9 - nz));
        }
        x1 = x; F1 = F;
        *m++ = v;
    }
    st2[0] = x1; st2[1] = F1;
}
/* k12 as ref_bhlue, sh the shelf {s0, s1, pole}; in place, blocks of 256 */
void ref_bhlue_aa(const int32_t *k12, const int32_t *sh, const int64_t *gf, const uint16_t *rcp, int32_t *m,
                  uint32_t len)
{
    bh_coef_t k;
    bh_state_t st = {0};
    int32_t st2[2] = {0, 0};
    uint32_t i;
    k.pa = k12[0]; k.ph = k12[1]; k.b0 = k12[2]; k.b1 = k12[3]; k.b2 = k12[4]; k.a1 = k12[5]; k.a2 = k12[6];
    k.c0 = k12[7]; k.c1 = k12[8]; k.c2 = k12[9]; k.d1 = k12[10]; k.d2 = k12[11];
    for (i = 0; i < len; i += 256) {
        uint32_t n = len - i < 256 ? len - i : 256;
        bh_hps2qs(&k, sh, &st, m + i, n);
        bh_pre2qc(&k, &st, m + i, n);
        bh_aa1j(gf, rcp, st2, m + i, n);
        bh_post2q(&k, &st, m + i, n);
    }
}
