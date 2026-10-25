/* SPDX-License-Identifier: GPL-3.0-only */
/* The DRIVE framework's per-sample kernels, each in a loop of its own, for an instruction count with the firmware's
 * compiler (tools/drive_insns.py: pi32v2 clang -Os, the paths through each loop). fx.c is included whole, as the
 * firmware has it; nothing here runs on the FM-1. A TYPE's own loop can be measured the same way: add a kernel. */
#include <stdint.h>
#include <stddef.h>
void *memset(void *d, int c, size_t n);
void *memcpy(void *d, const void *s, size_t n);
#ifndef DINGHY_HOST
#define DINGHY_HOST 1                                  /* (fx.c: no .pool section here) */
#endif
#include "fxp.h"
#define FS 44100u                                      /* (platform.c's) */
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
/* fx.c's SPREAD (S13) mixes rhodes.c's side signal: none here (rh_side_on 0) */
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"    /* (fx.c's reverb framework: fx_reverb, rev_cost_us) */

/* the classic drive's curve, plain (= softclip without its clamp) */
void k_curve(const int32_t *in, int32_t *out, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) out[i] = drv_curve(FX_TANH, 128u, 10u, in[i]);
}
/* the same curve with ADAA-1, eps 0 (a divide whenever the input moves) */
void k_adaa(drv_adaa_t *s, const int32_t *in, int32_t *out, uint32_t n)
{
    drv_adaa_t st = *s;
    uint32_t i;
    for (i = 0; i < n; i++) out[i] = drv_adaa1(&st, FX_TANH, DRV_TANH_H, 128u, 10u, 0, in[i]);
    *s = st;
}
/* ADAA-1, eps 64 (1/16 of a segment: the midpoint below it) */
void k_adaa_eps(drv_adaa_t *s, const int32_t *in, int32_t *out, uint32_t n)
{
    drv_adaa_t st = *s;
    uint32_t i;
    for (i = 0; i < n; i++) out[i] = drv_adaa1(&st, FX_TANH, DRV_TANH_H, 128u, 10u, 64, in[i]);
    *s = st;
}
/* for comparison only (not in drive.c): the quotient by the chip's 64 / 32 divide instead of the 32-bit one */
void k_adaa_div64(drv_adaa_t *s, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t xp = s->x;
    int64_t pp = s->p;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x = in[i], dx = x - xp;
        int64_t p = drv_curve_p(FX_TANH, DRV_TANH_H, 128u, 10u, x);
        out[i] = dx ? (int32_t)(((p - pp) >> 11) / dx) : drv_curve(FX_TANH, 128u, 10u, x);
        xp = x; pp = p;
    }
    s->x = xp; s->p = pp;
}
void k_bq(const drv_bq_t *c, drv_bqs_t *s, int32_t *m, uint32_t n)
{
    drv_bqs_t st = *s;
    uint32_t i;
    for (i = 0; i < n; i++) m[i] = drv_bq(c, &st, m[i]);
    *s = st;
}
void k_tpt(int32_t *s, int32_t G, int32_t *m, uint32_t n)
{
    int32_t st = *s;
    uint32_t i;
    for (i = 0; i < n; i++) m[i] = drv_tpt1(&st, G, m[i]);
    *s = st;
}
/* the framework itself: fx_drive_types (the glide, the call), drv_switch and fx_drive_classic (FDRV)
 * and drv_blend (the dry / wet blend) are functions of their own in fx.c: tools/drive_insns.py counts them by name */
void k_entry(int32_t *m, uint32_t n) { fx_drive(m, n); }
/* each TYPE's own loop (drv_<type>.c; a stub has none). Another file in a TYPE's place: -D DRV_FILE_KHLON=... */
void k_khlon(const int32_t *x, int32_t *m, uint32_t n, const drv_ctl_t *c) { drv_khlon_run(x, m, n, c); }
void k_tsh8(const int32_t *x, int32_t *m, uint32_t n, const drv_ctl_t *c) { drv_tsh8_run(x, m, n, c); }
void k_ohcd(const int32_t *x, int32_t *m, uint32_t n, const drv_ctl_t *c) { drv_ohcd_run(x, m, n, c); }
void k_bhlue(const int32_t *x, int32_t *m, uint32_t n, const drv_ctl_t *c) { drv_bhlue_run(x, m, n, c); }
