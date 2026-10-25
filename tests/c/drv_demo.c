/* SPDX-License-Identifier: GPL-3.0-only */
/* A demonstration TYPE for test builds only (drive_scan with -DDRV_FILE_KHLON="<this file>"; never in the firmware):
 * the smallest real TYPE on the shared helpers, so the framework's tests run a TYPE that is not a stub, and the
 * instruction count shows what a simple one costs. Gain (AMOUNT, gliding per block) -> tanh with ADAA-1 (the
 * classic drive's own curve, FX_TANH / DRV_TANH_H) -> a one-pole low-pass at 2 .. 6 kHz (TONE) -> level. */
#define DRV_US_KHLON 104u      /* pi32v2 -Os, the longest path: 67 instructions per sample (107 us) with the gain gliding
                                * per sample; the glide per block now (-3..4, not recounted): <= DRV_US_KERNEL_MAX */
static drv_adaa_t dm_aa;
static int32_t dm_g, dm_lp, dm_G;
static void drv_khlon_reset(void)
{
    drv_adaa_reset(&dm_aa, FX_TANH, DRV_TANH_H, 128u, 10u);
    dm_g = 4096; dm_lp = 0; dm_G = 134703052;
}
static void drv_khlon_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    int32_t lp = dm_lp, G = 134703052 + (201331064 / 127) * c->tone, g;          /* 2 kHz .. ~6 kHz (roughly) */
    drv_adaa_t aa = dm_aa;
    uint32_t i;
    g = dm_g = drv_glide(dm_g, 4096 + c->amount * 256, 2048);                     /* x1 .. x9 (Q12), per block */
    for (i = 0; i < n; i++) {
        int32_t x = (int32_t)(((int64_t)in[i] * g) >> 12), y;
        y = drv_adaa1(&aa, FX_TANH, DRV_TANH_H, 128u, 10u, 0, x);
        y = drv_tpt1(&lp, G, y);
        out[i] = (y * 3) >> 2;
    }
    dm_aa = aa; dm_lp = lp; dm_G = G;
}
