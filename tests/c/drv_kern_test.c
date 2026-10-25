/* SPDX-License-Identifier: GPL-3.0-only */
/* The TSH8, BHLUE and OHCD kernels as the firmware has them (fx.c whole, drive.c, drv_tsh8.c, drv_bhlue.c,
 * drv_ohcd.c), callable from Python (tests/py/test_drive_kernels.py, ctypes): the kernels with rows given by the caller (to compare them, bit for
 * bit, with the reference loops in tests/c/drv_kern_ref.c), and the control side (rows, levels, the diode table
 * build) as the firmware computes it. Host only. */
#include <stdint.h>
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

void t_tsh8_reset(void) { drv_tsh8_reset(); }
/* power-on as the firmware has it: the RAM tables never built (no harness reset fallback), then drv_boot at AMOUNT a */
void t_power_on(int32_t a) { ts_ok = 0; ts_trow = -1; ts_bpos = -1; fx_p[FXP_DRIVE] = a; drv_boot(); }
int t_tsh8_ok(void) { return ts_ok; }
/* one block of drv_tsh8_run at (a, t) on m (n samples, in place): the nodes built inside the call */
uint32_t t_tsh8_run(int32_t a, int32_t t, int32_t *m, uint32_t n)
{
    drv_ctl_t c;
    c.amount = a; c.tone = t; c.row = 0;
    drv_tsh8_run(m, m, n, &c);
    return ts_nb;
}
/* the four passes with the caller's rows (no level: the tone row as given), n even, in place */
void t_tsh8_kernel(const int32_t *br, const int32_t *tone, const int32_t *g, int32_t *m, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i += DRV_BLK) ts_kernel(br, tone, 0, g, m + i, m + i, n - i < DRV_BLK ? n - i : DRV_BLK);
}
/* the diode table of AMOUNT step a as the firmware builds it */
void t_tsh8_build(int32_t a, int32_t *out) { ts_build(out, a, 0u, TSH8_NODES); }
/* the anti-aliased variant's {g, F} table of step a, built in slices of 32 nodes as the firmware would */
void t_tsh8_build_gf(int32_t a, int32_t *out)
{
    uint32_t i;
    for (i = 0; i < TSH8_NODES; i += 32u) ts_build_gf(out, a, i, i + 32u < TSH8_NODES ? i + 32u : TSH8_NODES);
}
/* the rows the firmware runs at steps (a, t): branch[5], tone[5] (x the level) */
void t_tsh8_rows(int32_t a, int32_t t, int32_t *br, int32_t *tone)
{
    ts_set(a, t);
    memcpy(br, ts_br, sizeof ts_br); memcpy(tone, ts_tone, sizeof ts_tone);
}
#if TSH8_AA
/* the anti-aliased kernel with the caller's rows and {g, F} table (no swap), n a multiple of 2, in place */
void t_tsh8_kernel_aa(const int32_t *cin, const int32_t *br, const int32_t *tone, const int32_t *gf, int32_t *m,
                      uint32_t n)
{
    uint32_t i;
    memcpy(ts_in, cin, sizeof ts_in);
    for (i = 0; i < n; i += DRV_BLK) ts_kernel_aa(br, tone, 0, gf, m + i, m + i, n - i < DRV_BLK ? n - i : DRV_BLK);
}
#endif
int t_aa_on(void) { return TSH8_AA * 2 + BHLUE_AA; }
const uint16_t *t_aa_rcp(void) { return DRV_AA_RCP; }
#if BHLUE_AA
const int32_t *t_bhlue_sh(void) { return BHLUE_SH; }
const int64_t *t_bhlue_gf(void) { if (!bh_gf_ok) bh_gf_build(); return bh_gf; }
#endif
void t_bhlue_reset(void) { drv_bhlue_reset(); }
/* k: pa ph b0 b1 b2 a1 a2 c0 c1 c2 d1 d2 */
void t_bhlue_kernel(const int32_t *k, int32_t *m, uint32_t n)
{
    bh_coef_t c;
    uint32_t i;
    memcpy(&c, k, 12 * sizeof *k);
    c.lo = -BH_CLAMP; c.hi = BH_CLAMP;
#if BHLUE_AA
    c.s0 = BHLUE_SH[0]; c.s1 = BHLUE_SH[1]; c.sp = BHLUE_SH[2];
#endif
    for (i = 0; i < n; i += DRV_BLK) bh_kernel(&c, &bh_s, m + i, m + i, n - i < DRV_BLK ? n - i : DRV_BLK);
}
void t_bhlue_rows(int32_t a, int32_t t, int32_t *k)
{
    bh_set(a, t);
    memcpy(k, &bh_k, 12 * sizeof *k);
}
void t_ohcd_reset(void) { drv_ohcd_reset(); }
/* k: the 15 words of oh_coef_t (S1 b0 b1 b2 -a1 -a2, S2 x Q the same, the tone x 1 / cos the same, its b x the
 * level); n even, in place */
void t_ohcd_kernel(const int32_t *k, int32_t *m, uint32_t n)
{
    oh_coef_t c;
    uint32_t i;
    memcpy(&c, k, sizeof c);
    for (i = 0; i < n; i += DRV_BLK) oh_kernel(&c, &oh_s, m + i, m + i, n - i < DRV_BLK ? n - i : DRV_BLK);
}
/* the rows the firmware runs at steps (a, t): the 15 words */
void t_ohcd_rows(int32_t a, int32_t t, int32_t *k)
{
    oh_set(a, t);
    memcpy(k, &oh_k, sizeof oh_k);
}
/* the shared reciprocal table the anti-aliased clipper uses */
const uint16_t *t_ohcd_rcp(void) { return DRV_AA_RCP; }
int t_ohcd_aa(void) { return OHCD_AA; }
/* the firmware's IC1B rail on every v given (y[i] from v[i] and v[i - 1], v[-1] = 0) */
void t_ohcd_rail(const int32_t *v, int32_t *y, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) y[i] = oh_rail(v[i], i ? v[i - 1] : 0, DRV_AA_RCP - (1u << (DRV_AA_RB - 1)));
}
