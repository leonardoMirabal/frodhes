/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* The code of fx.c that derives from Felucca (GPL-3.0-only) and is compiled only when FR_OWN is 0: the FM-1, SMK-37,
 * Move, MPC and web builds. The plugin's own-code build (FR_OWN=1) has its own versions of these (dly_own.c, fx_own.h)
 * and never includes this file. One file, included from fx.c under !FR_OWN at three places, each with FEL_PART set to
 * its number; each part sees everything fx.c defines above its include site (moved here as whole blocks, unchanged):
 *   1  the delay's main sample loop (dly_loop)
 *   2  the delay's bars wrapper (dly_loop_kb)
 *   3  the master DC blocker (dc_block) */
#if FEL_PART == 1
/* the delay's sample loop, one version for TAPE (tape = 1) and one for DIGITAL / off (tape = 0: a constant after
 * inlining), the state in locals: the same arithmetic per sample as one loop with the tests inside. msk: the send on
 * (-1) or off (0, the tail rings); sg16: the send's make-up (Q16, exactly 65536 when dly_sg is 0: x 1), ramped sample
 * by sample from the last block's dly_sgk (a step once per block put a one-sample step into the echoes, one delay time
 * after each block boundary, whenever L != R: up to ~700 LSB on VIBES with the chorus; equal ends: the old send, bit
 * for bit); dgt: the dry's gain it eases to. */
static inline __attribute__((always_inline)) void dly_loop(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                           const int32_t msk, int32_t sg16, int32_t target, int32_t col,
                                                           int32_t fb, int32_t wet, int32_t dgt, const int rst,
                                                           const int kb)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, j, c = dly_rst ? w - dly_rb : 0x7FFFFFFFu;   /* c: the clean span behind w */
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg, ek = kb ? FX_ENG_K : 32768, eg = kb ? FX_ENG_G : 32768;
    int32_t wq = dly_wk << 8, ws = DLY_WSTEP(dly_wk, wet, n);   /* the echo's gain: a ramp from the last block's */
    int32_t fq = (dly_fbk < 0 ? fb : dly_fbk) << 8, fs = DLY_WSTEP(fq >> 8, fb, n);   /* the feedback's, the same */
    FX_K32 kcol = col;
    int32_t kfb;                         /* the tone filter's and the feedback's (r - lp) * col, f * fb:
                                                          * 32 x 32 -> 64, one instruction each (they were split into
                                                          * two 32-bit products, mulq15w: (a * b) >> 15, the same bits
                                                          * for |a| < 2^18; a * b alone overflows int32 from |a| 65536,
                                                          * fuzz vibes 4300-san) */
    if (tape)                                            /* tape: slewed time, wow and flutter; they depend on nothing
                                                          * else, so first for the whole block (fewer registers below) */
        for (j = 0; j < n; j++) {
            int32_t sf;
            dd += (target - dd) >> 12;
            wow += 69000u; flt += 690000u;              /* ~0.7 Hz, ~7 Hz */
            sf = osc_sine(flt);
            sf = (sf + (int32_t)((uint32_t)sf >> 31)) >> 1;   /* sf / 2 (towards 0) without the divide */
            dly_dt[j] = dd + (int32_t)(((int64_t)dd * (osc_sine(wow) * 3 + sf)) >> 25);
        }
    for (j = 0; j < n; j++) {
        int32_t d, r, in = dly_m[j] & msk, f;          /* (the send, ramped: dly_ramp) */
        uint32_t i;
        if (tape)
            d = dly_dt[j];
        else {
            dd += PH_CL(target - dd, -256, 256);
            d = dd;
        }
        i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        if (rst && c <= DLY_LEN) {                       /* a tap behind the clean span reads 0 (lookback L clean: L <= c,
                                                          * 0 counts as DLY_LEN; from c > DLY_LEN every lookback is) */
            uint32_t lb = (w - i) & (DLY_LEN - 1u);
            if (tape) r = DLY_H4C(i, lb, c, d & 255);    /* (ANA: the 4-point read) */
            else {
                int32_t a0 = ((lb - 1u) & (DLY_LEN - 1u)) < c ? dly_buf[i] : 0;
                int32_t a1 = (lb & (DLY_LEN - 1u)) < c ? dly_buf[(i - 1u) & (DLY_LEN - 1u)] : 0;
                r = (a0 + (((a1 - a0) * (d & 255)) >> 8)) << 1;
            }
            c++;
        } else if (tape)                                 /* (c stays > DLY_LEN: no need to count on) */
            r = DLY_H4(i, d & 255);
        else
            r = (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
        lp += (int32_t)(((int64_t)(r - lp) * kcol) >> 15);   /* (r - lp reaches 2^17 on a full-scale swing) */
        f = !tape ? lp : kb ? fx_sck_k(lp, ek, eg) : softclip(lp);   /* (kb: the bars, dly_loop_kb) */
        hp += (f - hp + 64) >> 7;
        f -= hp;
        fq += fs; kfb = fq >> 8;
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi(FX_HC(0, (in >> 1) + (int32_t)(((int64_t)f * kfb) >> 15), -32768, 32767), -32768, 32767);
        w++;
        wq += ws;
        r = (int32_t)(((int64_t)r * (wq >> 8)) >> 15);   /* (64 bits: the 4-point read overshoots 2^16, c1-int) */
        DLY_DG_STEP(dg, dgt);
        L[j] = (int32_t)(((int64_t)L[j] * dg) >> 15) + r; R[j] = (int32_t)(((int64_t)R[j] * dg) >> 15) + r;
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg; dly_sgk = msk ? sg16 : 0;
}
#elif FEL_PART == 2
/* ANA on the bars (0.9.13): dly_loop with its soft clip at the engine's level (fx_sck_k: the bars' SND3 -3 dB a level,
 * the tape's clip as on 0.9.11); in flash, the RAM copies keep their size */
__attribute__((noinline)) static void dly_loop_kb(int32_t *L, int32_t *R, uint32_t n, int32_t sg16, int32_t target,
                                                  int32_t col, int32_t fb, int32_t wet, int32_t dgt, int rst)
{
    dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, dgt, rst, 1);
}
#elif FEL_PART == 3
static inline __attribute__((always_inline)) int32_t dc_block(int32_t x, int32_t *dc, int32_t *err)
{
    int32_t e = (x << 6) - *dc + *err, d = e >> 12;
    *err = e - (d << 12);
    *dc += d;
    return x - ((*dc + 32) >> 6);
}
#endif
