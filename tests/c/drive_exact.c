/* SPDX-License-Identifier: GPL-3.0-only */
/* KHLON (firmware/src/drv_khlon.c) against its reference kernel, bit for bit (OHCD: tests/c/drv_kern_ref.c, through
 * tests/py/test_drive_kernels.py; its plain kernel, once here, is gone, 0.9.5). The reference below is the private 1x
 * integer kernel the TYPE was designed and measured with (their arithmetic as written
 * there: direct form I / transposed form II, 256-sample blocks, one loop per stage); the firmware runs them as the
 * framework calls them (a block, in place), with its own tables, its own loops and its own level fold. Same coefficients in, the same
 * integers out, or the TYPE is not the kernel that was measured (tests/py/test_drive_kernels_b.py).
 *
 *   drive_exact TYPE AMOUNT TONE IN OUT_FW OUT_REF      TYPE: khlon; IN raw int32 (the bus, Q15)
 *
 * Knobs held (the reference has no glide): both start at the knobs' rows. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DINGHY_HOST 1
#define FS 44100u
#include "fxp.h"
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
/* fx.c's SPREAD (S13) mixes rhodes.c's side signal: none here (rh_side_on 0) */
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"    /* (fx.c's reverb framework: fx_reverb, rev_cost_us) */

/* ---- KHLON reference (the private b110 kernel with its anti-aliased clipper, the KHLON AA prototype: O1 -> tmp, CL
 * in place; post: clipper -> tmp (plain below the AA rows, ADAA-1 from them), m += slot (of the droop-compensated
 * V_S on the AA rows); pre: slot, clipper, m += the compensated V_S; POST); input volts Q20 = the bus << 5 ---- */
typedef struct { int32_t b0, b1, b2, a1, a2; } rk_bq;
typedef struct { rk_bq o1, cl, post, o1b; int32_t kc4, rail; const int32_t *gf; int32_t ys_post, aa; } rk_coef;
typedef struct { int64_t o1[2], post[2], cl, o1b; int32_t sc, e1, F1, v1, aa_on; } rk_state;
#define M64(a, b) ((int64_t)(a) * (int64_t)(b))
#define RK_SF 12
#define RK_BETA 38
static void rk_bq_run(const rk_bq *c, int64_t *st, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t b0 = c->b0, b1 = c->b1, b2 = c->b2, a1 = c->a1, a2 = c->a2;
    int64_t s1 = st[0], s2 = st[1];
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x = in[i], y;
        y = (int32_t)((s1 + M64(b0, x)) >> 24);
        s1 = s2 + M64(b1, x) + M64(a1, y);
        s2 = M64(b2, x) + M64(a2, y);
        out[i] = y;
    }
    st[0] = s1; st[1] = s2;
}
static void rk_op_run(const rk_bq *c, int64_t *st, const int32_t *in, int32_t *out, uint32_t n, int add)
{
    int32_t b0 = c->b0, b1 = c->b1, a1 = c->a1;
    int64_t s = *st;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x = in[i], y;
        y = (int32_t)((s + M64(b0, x)) >> 24);
        s = M64(b1, x) + M64(a1, y);
        if (add) out[i] += y; else out[i] = y;
    }
    *st = s;
}
static void rk_opf_run(const rk_bq *c, int64_t *st, int32_t *v1p, const int32_t *in, int32_t *acc, uint32_t n)
{
    int32_t b0 = c->b0, b1 = c->b1, a1 = c->a1, v1 = *v1p;
    int64_t s = *st;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t v = in[i], x = v + (((v - v1) * RK_BETA) >> 8), y;
        v1 = v;
        y = (int32_t)((s + M64(b0, x)) >> 24);
        s = M64(b1, x) + M64(a1, y);
        acc[i] += y;
    }
    *st = s; *v1p = v1;
}
static void rk_fir_run(int32_t *v1p, const int32_t *in, int32_t *acc, uint32_t n)
{
    int32_t v1 = *v1p;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t v = in[i];
        acc[i] += v + (((v - v1) * RK_BETA) >> 8);
        v1 = v;
    }
    *v1p = v1;
}
static void rk_aa_run(const rk_coef *c, rk_state *s, const int32_t *in, int32_t *out, uint32_t n)
{
    const int32_t *gf = c->gf;
    const uint16_t *r0 = KH_RCP - (1 << 10);
    int32_t sc = s->sc, kc4 = c->kc4, e1 = s->e1, F1 = s->F1, L = c->rail, nL = -c->rail;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t e = in[i], a, fr, g0, dg, pr, F, d, v;
        uint32_t ad;
        const int32_t *p;
        e = e < nL ? nL : e; e = e > L ? L : e;
        e -= sc;
        a = e < 0 ? -e : e;
        p = gf + ((a >> 14) << 1);
        fr = a & ((1 << 14) - 1);
        g0 = p[0]; dg = p[2] - g0;
        pr = dg * fr;
        F = p[1] + (int32_t)(M64(fr, g0 + (pr >> 15)) >> 13);
        d = e - e1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (ad >= (1u << RK_SF)) {
            int nz = __builtin_clz(ad);
            uint32_t r = r0[(ad << nz) >> (32 - 11)];
            int32_t q = (int32_t)(M64(F - F1, r) >> (47 - RK_SF - nz));
            v = d < 0 ? -q : q;
        } else {
            int32_t gm = g0 + (pr >> 14);
            v = ((e < 0 ? -gm : gm) - ((dg * d) >> 15)) >> 1;
        }
        e1 = e; F1 = F;
        sc += (int32_t)(M64(e - v, kc4) >> 32);
        out[i] = v;
    }
    s->sc = sc; s->e1 = e1; s->F1 = F1;
}
static void rk_plain_run(const rk_coef *c, rk_state *s, const int32_t *in, int32_t *out, uint32_t n)
{
    const int32_t *gf = c->gf;
    int32_t sc = s->sc, kc4 = c->kc4, e = s->e1, L = c->rail, nL = -c->rail;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t a, g, v;
        const int32_t *p;
        e = in[i];
        e = e < nL ? nL : e; e = e > L ? L : e;
        e -= sc;
        a = e < 0 ? -e : e;
        p = gf + ((a >> 14) << 1);
        g = (p[0] + (((p[2] - p[0]) * (a & ((1 << 14) - 1)) + ((e >> 31) & 0x7ffe)) >> 14)) >> 1;
        v = e < 0 ? -g : g;
        sc += (int32_t)(M64(e - v, kc4) >> 32);
        out[i] = v;
    }
    s->sc = sc; s->e1 = e;
}
static int32_t rk_F(const int32_t *gf, int32_t e)
{
    int32_t a = e < 0 ? -e : e, fr = a & ((1 << 14) - 1), g0, dg;
    const int32_t *p = gf + ((a >> 14) << 1);
    g0 = p[0]; dg = p[2] - g0;
    return p[1] + (int32_t)(M64(fr, g0 + ((dg * fr) >> 15)) >> 13);
}
static void rk_run(const rk_coef *c, rk_state *s, int32_t *m, int32_t *tmp, uint32_t n)
{
    if (c->aa && !s->aa_on) s->F1 = rk_F(c->gf, s->e1);
    s->aa_on = c->aa;
    if (c->ys_post) {
        rk_bq_run(&c->o1, s->o1, m, tmp, n);
        rk_op_run(&c->cl, &s->cl, m, m, n, 0);
        if (c->aa) {
            rk_aa_run(c, s, tmp, tmp, n);
            rk_opf_run(&c->o1b, &s->o1b, &s->v1, tmp, m, n);
        } else {
            rk_plain_run(c, s, tmp, tmp, n);
            rk_op_run(&c->o1b, &s->o1b, tmp, m, n, 1);
            s->v1 = tmp[n - 1];
        }
    } else {
        rk_bq_run(&c->o1, s->o1, m, tmp, n);
        rk_op_run(&c->o1b, &s->o1b, tmp, tmp, n, 0);
        rk_op_run(&c->cl, &s->cl, m, m, n, 0);
        if (c->aa) { rk_aa_run(c, s, tmp, tmp, n); rk_fir_run(&s->v1, tmp, m, n); }
        else { uint32_t i; rk_plain_run(c, s, tmp, tmp, n); for (i = 0; i < n; i++) m[i] += tmp[i]; }
    }
    rk_bq_run(&c->post, s->post, m, m, n);
}
static int32_t unflip(uint32_t v) { return (int32_t)(v ^ 0x80000000u); }
static int32_t mean_or(const uint32_t *r, uint32_t i, uint32_t stride, uint32_t h)
{
    return h ? (unflip(r[i]) + unflip(r[stride + i])) >> 1 : unflip(r[i]);
}
/* the coefficients of AMOUNT a, TONE t as stored (an AMOUNT / TONE between two stored rows: their mean); POST's b x
 * (the AMOUNT scale x the TONE level); DRIVE_EXACT_UNITY: the scale is the design's own (k for the pre rows, POST in
 * volts Q20; for lg_db 0); DRIVE_EXACT_POST="b0,b1,b2": POST's b given */
static void rk_coef_for(rk_coef *c, int a, int t)
{
    uint32_t ga = KH_AMAP[a - 1], gr = ga >> 1, gh = ga & 1u, j = (uint32_t)(t + ((t + 1) >> 7)), tr = j >> 1, th = j & 1u;
    const uint32_t *g = KH_G[gr], *p = KH_T[tr];
    int32_t gs = gh ? (KH_GS[gr] + KH_GS[gr + 1]) >> 1 : KH_GS[gr], lt = th ? (KH_LT[tr] + KH_LT[tr + 1]) >> 1 : KH_LT[tr];
    int32_t s = (int32_t)(((int64_t)gs * lt + (1 << 13)) >> 14);
    if (getenv("DRIVE_EXACT_UNITY")) s = gr >= KH_PRE_ROW ? KH_GS[gr] * 32 : 1 << 24;
    c->o1.b0 = mean_or(g, 0, 11, gh); c->o1.b1 = mean_or(g, 1, 11, gh); c->o1.b2 = mean_or(g, 2, 11, gh);
    c->o1.a1 = mean_or(g, 3, 11, gh); c->o1.a2 = mean_or(g, 4, 11, gh);
    c->cl.b0 = mean_or(g, 5, 11, gh); c->cl.b1 = mean_or(g, 6, 11, gh); c->cl.a1 = mean_or(g, 7, 11, gh);
    c->o1b.b0 = mean_or(g, 8, 11, gh); c->o1b.b1 = mean_or(g, 9, 11, gh); c->o1b.a1 = mean_or(g, 10, 11, gh);
    c->post.b0 = (int32_t)((M64(mean_or(p, 0, 5, th), s) + (1 << 23)) >> 24);
    c->post.b1 = (int32_t)((M64(mean_or(p, 1, 5, th), s) + (1 << 23)) >> 24);
    c->post.b2 = (int32_t)((M64(mean_or(p, 2, 5, th), s) + (1 << 23)) >> 24);
    c->post.a1 = mean_or(p, 3, 5, th); c->post.a2 = mean_or(p, 4, 5, th);
    if (getenv("DRIVE_EXACT_POST"))                    /* (POST's b given: the design's own k fold, for a check) */
        sscanf(getenv("DRIVE_EXACT_POST"), "%d,%d,%d", &c->post.b0, &c->post.b1, &c->post.b2);
    c->kc4 = KH_KC4; c->rail = KH_RAIL; c->gf = KH_GF; c->ys_post = gr < KH_PRE_ROW;
    c->aa = a - 1 >= (int)KH_AA_FROM;
    if (getenv("DRIVE_EXACT_AA")) c->aa = atoi(getenv("DRIVE_EXACT_AA"));   /* (the design's own AA choice: g >= 0.6) */
}
static void ref_khlon(int32_t *x, size_t n, int a, int t)
{
    static rk_coef c;
    static rk_state s;
    static int32_t tmp[256];
    size_t k;
    rk_coef_for(&c, a, t);
    memset(&s, 0, sizeof s);
    for (k = 0; k < n; k++) x[k] <<= 5;
    for (k = 0; k < n; k += 256) rk_run(&c, &s, x + k, tmp, (uint32_t)(n - k < 256 ? n - k : 256));
}

static void fw_run(int typ, int32_t *x, size_t n, int a, int t)   /* as fx_drive_types all wet: in place, a block */
{
    drv_ctl_t c;
    size_t k;
    c.amount = a; c.tone = t; c.row = 0;
    (void)typ;
    drv_khlon_reset();
    for (k = 0; k < n; k += DRV_BLK) {
        uint32_t len = (uint32_t)(n - k < DRV_BLK ? n - k : DRV_BLK);
        drv_khlon_run(x + k, x + k, len, &c);
    }
}

int main(int argc, char **argv)
{
    FILE *f;
    int32_t *x, *y;
    long sz;
    size_t n;
    int typ, a, t;
    if (argc < 7) { fprintf(stderr, "usage: drive_exact TYPE AMOUNT TONE IN OUT_FW OUT_REF\n"); return 2; }
    if (strcmp(argv[1], "khlon")) { fprintf(stderr, "drive_exact: khlon only\n"); return 2; }
    typ = 2;
    a = atoi(argv[2]); t = atoi(argv[3]);
    f = fopen(argv[4], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); sz = ftell(f); fseek(f, 0, SEEK_SET);
    n = (size_t)sz / 4;
    x = malloc(n * 4 + 1024); y = malloc(n * 4 + 1024);
    if (fread(x, 4, n, f) != n) return 2;
    fclose(f);
    memcpy(y, x, n * 4);
    fw_run(typ, x, n, a, t);
    ref_khlon(y, n, a, t);
    f = fopen(argv[5], "wb"); fwrite(x, 4, n, f); fclose(f);
    f = fopen(argv[6], "wb"); fwrite(y, 4, n, f); fclose(f);
    return 0;
}
