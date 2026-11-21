/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* The code of fx.c that derives from Felucca (GPL-3.0-only) and is compiled only when FR_OWN is 0: the FM-1, SMK-37,
 * Move, MPC and web builds. The plugin's own-code build (FR_OWN=1) has its own versions of these (dly_own.c, fx_own.h)
 * and never includes this file. One file, included from fx.c under !FR_OWN at four places, each with FEL_PART set to
 * its number; each part sees everything fx.c defines above its include site (moved here as whole blocks, unchanged):
 *   1  the delay's main sample loop (dly_loop)
 *   2  the delay's bars wrapper (dly_loop_kb)
 *   3  the master DC blocker (dc_block)
 *   4  the classic drive (FDRV: dr_classic, fx_drive_classic and their state) */
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
#elif FEL_PART == 4
/* the classic drive (FDRV, Felucca track_dist): its kernel and its silence */
/* ---- DRIVE (Felucca track_dist; per TYPE drive row: gain scale, bias, make-up). Its page (0.9.3): ON / OFF, AMOUNT,
 * TONE (after the drive: a shelf at ~1.8 kHz, 0 = the highs gone (a one-pole low-pass), 64 = flat, 127 = +6 dB) and MIX
 * (dry / driven). At TONE 64 and MIX 127 the loop is the one before the page, sample for sample. TONE's gain and the
 * wet share glide (TONE 0 .. 127 in ~23 ms, MIX in ~46 ms, ON / OFF a 46 ms crossfade): no zipper, no click. */
#define DR_TA 7700                                      /* the shelf's one-pole: 1 - e^(-2 pi 1.8 kHz / fs), Q15 */
static const int16_t DR_S[4] = {16, 20, 14, 24}, DR_BIAS[4] = {2400, 2400, 1800, 3200}, DR_MU[4] = {4096, 4096, 4300, 3700};
static int32_t dr_hp, dr_lp1, dr_lp2;
/* FDRV's shaper output (ys, drive.c dr_os2) and its two low-passes carry 4 more bits; the make-up's product (64-bit)
 * rounds once to the output (c1-int: the shaper's input, its curve, ys, both low-passes and the make-up each cut to
 * the integer, ~5 truncations of a quiet tail: steps of 3 LSB in 3.3 % of its samples at AMOUNT 20, the dry signal
 * 1.7 %; now 1.9 %, the residual over 2 kHz 0.62 -> 0.57 LSB, dry 0.55: tests/py/test_drive_tail.py) */
#define DR_YS_X 4u
/* 0.9.6's new state in sections of their own (RH_SP_SEC): in the compiler's merged globals they moved app_audio's
 * registers (the phaser loop 77 -> 80, the limiter's, +36 us on VIBES with every effect, cpu_check on 1415937) */
static int32_t dr_gl RH_SP_SEC(".data.fx_drgl") = -1, dr_kl RH_SP_SEC(".bss.fx_drkl"), dr_ml RH_SP_SEC(".bss.fx_drml"),
               dr_dl RH_SP_SEC(".bss.fx_drdl");        /* FDRV's gain, its low-pass and make-up the last block ran with
                                                        * (-1: none), and its AMOUNT: AMOUNT moved, it glides (at most
                                                        * DR_DSTEP a block) and they ramp over the block (0.9.6, #5:
                                                        * they stepped once a block, a zipper of 2.7x the held ones';
                                                        * the gain and the make-up ramped apart over a big step were
                                                        * +4.8 dB: the make-up matches the gain per AMOUNT only) */
#define DR_DSTEP 4
static uint8_t dr_os_ok RH_SP_SEC(".bss.dr_os_ok");   /* FDRV's 2x state is valid (0: to rest at the next block;
                                                        * drive.c dr_os2) */
static int32_t dr_sal RH_SP_SEC(".bss.fx_dr_sal");      /* FDRV's wet share at the block's end (fx_drive; fx_spread) */
static int32_t dr_w = 32768, dr_tg = 4096, dr_tlp;      /* the wet's gain (Q15), the shelf's high gain (Q12), its low-pass */
/* MIX (0.9.13, as Textura's MIX; it was a crossfade, the wet share 258 MIX): two gains, the wet's (dr_w) and the dry's
 * (dr_d), out = (dry x d + wet x w) >> 15. 0 .. 64 the dry whole, the wet rising to whole (DR_WET); 64 both whole;
 * 64 .. 127 the wet whole, the dry falling to nothing (DR_DRY). OFF, AMOUNT 0 and a TYPE switch go to w 0, d whole.
 * Each gain glides on its own, 16 a sample (46 ms whole <-> nothing, as the share did): an OFF from 127 is the old
 * crossfade (d + w stays whole), from 64 the wet alone fades; d + w never falls under whole on the way. Both are
 * multiples of 256 (the TYPEs' ramp of 64 every 4 samples lands on them, drive.c) */
#define DR_WET(v) ((v) >= 64 ? 32768 : (v) * 512)
#define DR_DRY(v) ((v) <= 64 ? 32768 : (((127 - (v)) * 32768 / 63 + 128) & ~255))
static int32_t dr_d RH_SP_SEC(".bss.fx_dr_d");          /* the dry's gain (Q15; 0: all wet, as dr_w's 32768 at boot) */
/* the wet's share of the two (fx_spread aligns the side to it): w / (w + d), Q15; 0 at rest (w 0) */
static int32_t dr_share(int32_t w, int32_t d) { return w ? (int32_t)(((uint32_t)w << 15) / (uint32_t)(w + d)) : 0; }
static uint8_t dr_ton;                                  /* the shelf runs (its gain is not 1, or on its way) */
/* TONE and MIX settled (0.9.5): the gliding loop below with its glides at rest, so each sample does what that loop
 * does then (the shelf at its gain tg when it runs, the wet share w), in registers (that loop: 125 instructions a
 * sample, its state in memory); shelf and full are constants at each use. full (0.9.13, MIX's two gains, settled on
 * the law where one of them is whole): 1 all wet, 2 the wet whole and the dry x d, 0 the dry whole and the wet x w,
 * one multiply each, the same integers as dr_general's (x0 d + y w) >> 15 there (both products in one loop moved the
 * make-up's multiply onto the stack: 22 -> 30 instructions a sample) */
/* clampi(v, -230000, 230000) as smax + smin (the same value) */
static inline int32_t dr_cl(int32_t v) { v = v > -230000 ? v : -230000; return v < 230000 ? v : 230000; }
/* FDRV's shaper at 2x (drive.c dr_os2): the high-pass, the gain, the curve between the half-bands -> ys[]; the
 * loops below run its low-passes, make-up, TONE shelf and MIX at 1x on ys (dr_hp is dr_os2's) */
static const int32_t *dr_os2(const int32_t *m, uint32_t n, int32_t gq, int32_t dgq, int32_t bias, int32_t b0);
static inline __attribute__((always_inline)) void dr_settled(int32_t *m, const int32_t *ys, uint32_t n, int32_t k,
                                                             int32_t mk, int32_t tg, int32_t w, int32_t d,
                                                             const int shelf, const int full)
{
    int32_t lp1 = dr_lp1, lp2 = dr_lp2, tlp = dr_tlp;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], y = ys[i];
        lp1 += (int32_t)(((int64_t)(y - lp1) * k) >> 15);
        lp2 += (int32_t)(((int64_t)(lp1 - lp2) * k) >> 15);
        y = (int32_t)(((int64_t)lp2 * mk + (1 << (14 + DR_YS_X))) >> (15 + DR_YS_X));
        if (shelf) {
            tlp += mulq15(y - tlp, DR_TA);
            y = tlp + (int32_t)(((int64_t)(y - tlp) * tg) >> 12);
        }
        m[i] = full == 1 ? y : full == 2 ? y + (int32_t)(((int64_t)x0 * d) >> 15)   /* (the wet whole, the dry x d) */
                                         : x0 + (int32_t)(((int64_t)y * w) >> 15);    /* (the dry whole, the wet x w) */
    }
    dr_lp1 = lp1; dr_lp2 = lp2; dr_tlp = tlp;
}
/* the gliding loop (TONE / MIX on their way, AMOUNT's ramp): out of app_audio (inlined, its registers moved the
 * defaults' and the settled loops: +1 instruction and a stack load each, 0.9.6); g, k, mk in Q8 from gq / kq / mq by
 * dgq / dkq / dmq a sample (0: constant) */
/* MIX's two gains ramp straight over the block (Q8: wq / gq by dwq / dgq a sample) to where their 16-a-sample glides end
 * it (dr_after); the caller sets dr_w / dr_d to those ends */
FR_RAM2 __attribute__((noinline)) static void dr_general(int32_t *m, const int32_t *ys, uint32_t n, int32_t kq, int32_t mq,
                                                 int32_t dkq, int32_t dmq, int32_t tt, int32_t wq, int32_t dwq,
                                                 int32_t gq, int32_t dgq)
{
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], y = ys[i], k, mk;
        kq += dkq; mq += dmq;
        k = kq >> 8; mk = mq >> 8;
        dr_lp1 += (int32_t)(((int64_t)(y - dr_lp1) * k) >> 15);
        dr_lp2 += (int32_t)(((int64_t)(dr_lp1 - dr_lp2) * k) >> 15);
        y = (int32_t)(((int64_t)dr_lp2 * mk + (1 << (14 + DR_YS_X))) >> (15 + DR_YS_X));
        if (dr_ton || tt != 4096) {                     /* TONE: lows + the highs x gain (lp + g (y - lp)) */
            if (!dr_ton) { dr_tlp = y; dr_ton = 1; }    /* (from the signal itself: no step when it starts) */
            dr_tg += dr_tg < tt ? (tt - dr_tg > 8 ? 8 : tt - dr_tg) : -(dr_tg - tt > 8 ? 8 : dr_tg - tt);
            dr_tlp += mulq15(y - dr_tlp, DR_TA);
            y = dr_tlp + (int32_t)(((int64_t)(y - dr_tlp) * dr_tg) >> 12);
            if (dr_tg == 4096 && tt == 4096) dr_ton = 0;   /* back to flat: the shelf rests (y is exact again) */
        }
        wq += dwq; gq += dgq;
        m[i] = (int32_t)(((int64_t)x0 * (gq >> 8) + (int64_t)y * (wq >> 8)) >> 15);   /* (all wet: y exactly) */
    }
}
/* a gain toward its target at 16 a sample, n samples on (dr_general's glide, at a block's end) */
static inline __attribute__((always_inline)) int32_t dr_after(int32_t g, int32_t t, int32_t n)
{
    int32_t st = 16 * n;
    return g < t ? (t - g > st ? g + st : t) : (g - t > st ? g - st : t);
}
/* FDRV on the bars (0.9.13): their SND3 -3 dB is a level, so the shaper's gain reads them 3 dB up (bars.h BAR_FX_K) and
 * the make-up gives the output back 3 dB down (BAR_MIX_G): driven as on 0.9.11. In flash (the RAM loops keep their size) */
__attribute__((noinline)) static void dr_eng_lvl(int32_t *g, int32_t *mk)
{
    *g = (int32_t)(((int64_t)*g * FX_ENG_K + 16384) >> 15);
    *mk = (int32_t)(((int64_t)*mk * FX_ENG_G + 16384) >> 15);
}
#if FR_CHUNK
/* dr_classic and fx_drive_classic for a chunk: the settings, the ramp and the loop's choice at the first chunk; dr_w's
 * value at the block's end (dr_sal: fx_spread's first chunk reads it) worked out there too */
static const int32_t *dr_os2_c(const int32_t *m, int32_t gq, int32_t dgq, int32_t bias, int32_t b0);
static struct { int32_t g, k, mk, bias, b0, tt, wt, dt, gq, kq, mq, dgq, dkq, dmq, w_end, d_end, dwq, ddq; uint8_t path; } dr_c;
FR_RAM2 __attribute__((noinline)) static void dr_classic(int32_t *m, uint32_t n, int32_t sw)
{
    uint32_t o = fxc_o, e = fxc_e;
    const int32_t *ys;
    if (FXC_1) {
        int32_t d = fx_p[FXP_DRIVE], row = fx_p[FXP_DRIVE_ROW] & 3, g, k, mk, tv = fx_p[FXP_DRV_TONE], mv = fx_p[FXP_DRV_MIX];
        int32_t wt = !sw ? 0 : DR_WET(mv), dt = !sw ? 32768 : DR_DRY(mv);
        int32_t tt = tv >= 64 ? 4096 + (((tv - 64) * 4161) >> 6) : tv * 64;
        int32_t gq = -1, kq = 0, mq = 0, dgq = 0, dkq = 0, dmq = 0;
        if (!d) { wt = 0; dt = 32768; d = 1; }
        dr_c.w_end = dr_w; dr_c.d_end = dr_d;
        if (!wt && !dr_w && dr_d == 32768) { dr_ton = 0; dr_tg = 4096; dr_gl = -1; dr_c.path = 0; return; }
        if (dr_gl >= 0 && d != dr_dl) d = dr_dl + clampi(d - dr_dl, -DR_DSTEP, DR_DSTEP);
        dr_dl = d;
        g = 4096 + ((d * d * 2 * DR_S[row]) >> 4);
        if (d > 16) g = 4096 + ((g - 4096) * (95 + d)) / 111;   /* (32 bits: g - 4096 <= 48387, x 222 < 2^24) */
        k = 32000 - d * 95;
        mk = ((30000 - d * 120) * DR_MU[row]) >> 12;
        mk = (mk * (int32_t)FX_DRV_LVL[d]) >> 15;
        mk = (mk * 39000) >> 15;
        if (FX_ENG_K != 32768) FR_FAR(dr_eng_lvl)(&g, &mk);   /* the bars driven as on 0.9.11, given back 3 dB down */
        dr_c.bias = DR_BIAS[row];
        dr_c.b0 = softclip(dr_c.bias);
        if (tt > 8192) tt = 8192;
        if (dr_gl >= 0 && (dr_gl != g || dr_kl != k || dr_ml != mk)) {
            gq = dr_gl << 8; kq = dr_kl << 8; mq = dr_ml << 8;
            dgq = ((g - dr_gl) << 8) / (int32_t)n; dkq = ((k - dr_kl) << 8) / (int32_t)n; dmq = ((mk - dr_ml) << 8) / (int32_t)n;
        }
        dr_gl = g; dr_kl = k; dr_ml = mk;
        if (gq < 0 && wt == 32768 && dr_w == 32768 && !dt && !dr_d && tt == 4096 && !dr_ton) dr_c.path = 1;
        else if (gq < 0 && dr_tg == tt && dr_w == wt && dr_d == dt && (wt == 32768 || dt == 32768) &&
                 (dr_ton ? tt != 4096 : tt == 4096))
            dr_c.path = !dr_ton ? (wt == 32768 ? 6 : 2) : wt == 32768 && !dt ? 3 : wt == 32768 ? 7 : 4;
        else {
            dr_c.path = 5;
            if (gq < 0) { kq = k << 8; mq = mk << 8; }
            dr_c.w_end = dr_after(dr_w, wt, (int32_t)n);   /* (dr_general's ramps: to the 16-a-sample glides' ends) */
            dr_c.d_end = dr_after(dr_d, dt, (int32_t)n);
            dr_c.dwq = ((dr_c.w_end - dr_w) << 8) / (int32_t)n; dr_c.ddq = ((dr_c.d_end - dr_d) << 8) / (int32_t)n;
        }
        dr_c.g = g; dr_c.k = k; dr_c.mk = mk; dr_c.tt = tt; dr_c.wt = wt; dr_c.dt = dt;
        dr_c.gq = gq; dr_c.kq = kq; dr_c.mq = mq; dr_c.dgq = dgq; dr_c.dkq = dkq; dr_c.dmq = dmq;
    }
    if (!dr_c.path) return;
    ys = dr_os2_c(m, dr_c.gq < 0 ? dr_c.g << 8 : dr_c.gq, dr_c.dgq, dr_c.bias, dr_c.b0);
    if (dr_c.path == 1) {
        int32_t lp1 = dr_lp1, lp2 = dr_lp2, *p = m + o, *pe = m + e, k = dr_c.k, mk = dr_c.mk;
        ys += o;
        for (; p < pe; p++) {
            int32_t y = *ys++;
            lp1 += (int32_t)(((int64_t)(y - lp1) * k) >> 15);
            lp2 += (int32_t)(((int64_t)(lp1 - lp2) * k) >> 15);
            *p = (int32_t)(((int64_t)lp2 * mk + (1 << (14 + DR_YS_X))) >> (15 + DR_YS_X));
        }
        dr_lp1 = lp1; dr_lp2 = lp2;
    }
    else if (dr_c.path == 2) dr_settled(m + o, ys + o, e - o, dr_c.k, dr_c.mk, dr_c.tt, dr_c.wt, dr_c.dt, 0, 0);
    else if (dr_c.path == 6) dr_settled(m + o, ys + o, e - o, dr_c.k, dr_c.mk, dr_c.tt, dr_c.wt, dr_c.dt, 0, 2);
    else if (dr_c.path == 3) dr_settled(m + o, ys + o, e - o, dr_c.k, dr_c.mk, dr_c.tt, dr_c.wt, dr_c.dt, 1, 1);
    else if (dr_c.path == 4) dr_settled(m + o, ys + o, e - o, dr_c.k, dr_c.mk, dr_c.tt, dr_c.wt, dr_c.dt, 1, 0);
    else if (dr_c.path == 7) dr_settled(m + o, ys + o, e - o, dr_c.k, dr_c.mk, dr_c.tt, dr_c.wt, dr_c.dt, 1, 2);
    else {
        FR_FAR2(dr_general)(m + o, ys + o, e - o, dr_c.kq + dr_c.dkq * (int32_t)o, dr_c.mq + dr_c.dmq * (int32_t)o, dr_c.dkq,
                   dr_c.dmq, dr_c.tt, (dr_w << 8) + dr_c.dwq * (int32_t)o, dr_c.dwq, (dr_d << 8) + dr_c.ddq * (int32_t)o,
                   dr_c.ddq);
        if (FXC_Z) { dr_w = dr_c.w_end; dr_d = dr_c.d_end; }   /* (the block's last chunk: the ramps' ends) */
    }
}
__attribute__((noinline)) static void fx_drive_classic(int32_t *m, uint32_t n, int32_t sw)
{
    static int32_t hp, l1, l2, tl, z;                   /* (the block's start) */
    if (FXC_1) { hp = dr_hp; l1 = dr_lp1; l2 = dr_lp2; tl = dr_tlp; z = m[0]; }
    if (fxc_o <= (n >> 1) && (n >> 1) < fxc_e) z |= m[n >> 1];
    if (FXC_Z) z |= m[n - 1];
    FR_FAR2(dr_classic)(m, n, sw);
    if (FXC_Z && !z && hp == dr_hp && l1 == dr_lp1 && l2 == dr_lp2 && tl == dr_tlp && (hp | l1 | l2 | tl))
        { dr_hp = dr_lp1 = dr_lp2 = dr_tlp = 0; dr_os_ok = 0; }
}
#else
FR_RAM2 __attribute__((noinline)) static void dr_classic(int32_t *m, uint32_t n, int32_t sw)   /* FDRV; sw: FXP_DRV_SW
                                                        * (0 while it fades out for another TYPE: drive.c fx_drive) */
{
    int32_t d = fx_p[FXP_DRIVE], row = fx_p[FXP_DRIVE_ROW] & 3, g, k, mk, bias, b0, tv = fx_p[FXP_DRV_TONE], mv = fx_p[FXP_DRV_MIX];
    int32_t wt = !sw ? 0 : DR_WET(mv), dt = !sw ? 32768 : DR_DRY(mv);   /* the gains it goes to */
    int32_t tt = tv >= 64 ? 4096 + (((tv - 64) * 4161) >> 6) : tv * 64;         /* the shelf's gain: 0 .. 1 .. 2 (Q12) */
    int32_t gq = -1, kq = 0, mq = 0, dgq = 0, dkq = 0, dmq = 0;   /* (gq >= 0: the ramp, Q8) */
    const int32_t *ys;
    if (!d) { wt = 0; dt = 32768; d = 1; }              /* AMOUNT 0: off, faded out at AMOUNT 1's settings (it cut
                                                        * to dry in one sample: AMOUNT 1 is not the dry signal, the
                                                        * high-pass, the bias and the make-up still act; the bug hunt) */
    if (!wt && !dr_w && dr_d == 32768) { dr_ton = 0; dr_tg = 4096; dr_gl = -1; return; }   /* off, or MIX 0: the dry
                                                        * signal, untouched */
    if (dr_gl >= 0 && d != dr_dl) d = dr_dl + clampi(d - dr_dl, -DR_DSTEP, DR_DSTEP);
    dr_dl = d;
    g = 4096 + ((d * d * 2 * DR_S[row]) >> 4);
    if (d > 16) g = 4096 + ((g - 4096) * (95 + d)) / 111;   /* above 16 the gain rises to ~18x (32 bits: g - 4096 <=
                                                       * 127^2 x 2 x 24 / 16 = 48387, x 222 < 2^24; 0.9.13, the review D7)
                                                       * at the top (was ~9x: a single note never reached the knee, TODO #4;
                                                       * ~36x was a distortion, the user wants an overdrive); <= 16 (the
                                                       * presets' low drive) exactly as before */
    k = 32000 - d * 95;
    mk = ((30000 - d * 120) * DR_MU[row]) >> 12;
    mk = (mk * (int32_t)FX_DRV_LVL[d]) >> 15;          /* level-neutral: the drive adds tone, not volume (0.6) */
    mk = (mk * 39000) >> 15;                           /* +1.5 dB: measured on the FM-1, LUFS (0.8) */
    if (FX_ENG_K != 32768) FR_FAR(dr_eng_lvl)(&g, &mk);   /* the bars driven as on 0.9.11, given back 3 dB down */
    bias = DR_BIAS[row];
    b0 = softclip(bias);
    /* the shaper's input x g >> 12 + bias needs a 64-bit product (|x| <= 230000, g <= 100870 at AMOUNT 127), but its
     * result fits 32 bits (<= 5.67e6 + 3200), so softclip's own 32-bit clamp to +-4 is the whole clamp (it was a
     * second, 64-bit one before it: the same value). 0.9.8: it was (x >> 2) g >> 10, the 2 bits dropped before the
     * gain: a held note's quiet tail (under ~40 LSB) came out as a staircase of ~3 LSB steps, a buzz on the Move's
     * clean output (tests/py/test_drive_tail.py) */
    if (tt > 8192) tt = 8192;
    if (dr_gl >= 0 && (dr_gl != g || dr_kl != k || dr_ml != mk)) {   /* AMOUNT moved: the ramp, below */
        gq = dr_gl << 8; kq = dr_kl << 8; mq = dr_ml << 8;
        dgq = ((g - dr_gl) << 8) / (int32_t)n; dkq = ((k - dr_kl) << 8) / (int32_t)n; dmq = ((mk - dr_ml) << 8) / (int32_t)n;
    }
    dr_gl = g; dr_kl = k; dr_ml = mk;
    ys = dr_os2(m, n, gq < 0 ? g << 8 : gq, dgq, bias, b0);   /* the shaper at 2x (the gain's ramp in it) */
    if (gq < 0 && wt == 32768 && dr_w == 32768 && !dt && !dr_d && tt == 4096 && !dr_ton) {   /* the defaults: the loop
                                                        * before the page */
        int32_t lp1 = dr_lp1, lp2 = dr_lp2, *e = m + n;   /* (0.9.5: the state in locals) */
        for (; m < e; m++) {
            int32_t y = *ys++;
            lp1 += (int32_t)(((int64_t)(y - lp1) * k) >> 15);
            lp2 += (int32_t)(((int64_t)(lp1 - lp2) * k) >> 15);
            *m = (int32_t)(((int64_t)lp2 * mk + (1 << (14 + DR_YS_X))) >> (15 + DR_YS_X));
        }
        dr_lp1 = lp1; dr_lp2 = lp2;
        return;
    }
    if (gq < 0 && dr_tg == tt && dr_w == wt && dr_d == dt && (wt == 32768 || dt == 32768) &&
        (dr_ton ? tt != 4096 : tt == 4096)) {           /* settled: the glides at rest (MIX on its law: one gain whole),
                                                        * the shelf running (and staying on) or resting (dr_tg 4096) */
        if (!dr_ton) {                                  /* (MIX alone: all wet is the defaults) */
            if (wt == 32768) dr_settled(m, ys, n, k, mk, tt, wt, dt, 0, 2);
            else dr_settled(m, ys, n, k, mk, tt, wt, dt, 0, 0);
        }
        else if (wt == 32768 && !dt) dr_settled(m, ys, n, k, mk, tt, wt, dt, 1, 1);
        else if (wt == 32768) dr_settled(m, ys, n, k, mk, tt, wt, dt, 1, 2);
        else dr_settled(m, ys, n, k, mk, tt, wt, dt, 1, 0);
        return;
    }
    if (gq < 0) { kq = k << 8; mq = mk << 8; }          /* (no ramp: the steps are 0) */
    {
        int32_t w1 = dr_after(dr_w, wt, (int32_t)n), g1 = dr_after(dr_d, dt, (int32_t)n);
        FR_FAR2(dr_general)(m, ys, n, kq, mq, dkq, dmq, tt, dr_w << 8, ((w1 - dr_w) << 8) / (int32_t)n, dr_d << 8,
                   ((g1 - dr_d) << 8) / (int32_t)n);
        dr_w = w1; dr_d = g1;
    }
}
/* FDRV, and its silence (0.9.8, the residual after a panic): on zero input the rounding leaves fixed points that are
 * not 0 (the high-pass stops anywhere in -63 .. 64, in Q7 from 0.9.10: -64 .. 63 / 128 LSB; the shaper turns -hp
 * into a constant and the low-passes hold it): a DC of ~30 LSB out, for ever, kept open by the silence gate. A block that starts and
 * ends on 0 in with every state where it started is that fixed point: to rest (exact 0 from the next block). Once a
 * block, no per-sample work (dr_classic's loops as they were); music never leaves the states unmoved */
__attribute__((noinline)) static void fx_drive_classic(int32_t *m, uint32_t n, int32_t sw)
{
    int32_t hp = dr_hp, l1 = dr_lp1, l2 = dr_lp2, tl = dr_tlp, z = !(m[0] | m[n >> 1] | m[n - 1]);
    FR_FAR2(dr_classic)(m, n, sw);
    if (z && hp == dr_hp && l1 == dr_lp1 && l2 == dr_lp2 && tl == dr_tlp && (hp | l1 | l2 | tl))
        { dr_hp = dr_lp1 = dr_lp2 = dr_tlp = 0; dr_os_ok = 0; }   /* (and the 2x state: drive.c dr_os2) */
}
#endif
#endif
