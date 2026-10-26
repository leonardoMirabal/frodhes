/* SPDX-License-Identifier: GPL-3.0-only */
/* The delay's TAPE type (EFFECT = TAPE, 0.9.5): a single-head tape echo on the delay's own line (dly_buf, dly_dt).
 * Included by fx.c after the delay core; DIG and ANA keep their loop (dly_loop) untouched.
 *
 *   send -> record amp -> [record head + tape: the transfer curve, FX_TAPE_SAT] -> tape (dly_buf)
 *        -> [playback head: the low-frequency head bump (a resonant 2nd-order high-pass) and the gap / spacing loss
 *            (two one-poles), both moving with the tape speed] -> echo (heard) and regeneration (back to the record amp)
 *
 * TIME is the tape speed: one record head and one playback head a fixed distance apart, so a longer TIME is a slower
 * tape (inside +-2 octaves of the reference speed; beyond it the head distance changes instead). The speed moves the
 * playback head's bump and its high-frequency loss with it, and the transport's flutter rates. Wow and flutter: a slow
 * random drift of the speed (noise through two low-passes) and the capstan's rotation (its fundamental and second
 * harmonic) plus the pinch roller's, their depth drifting slowly; computed every 16 samples into dly_dt (the delay per
 * sample), interpolated in between. With the two heads on one tape the delay is the transit time, so a speed error
 * delta(t) moves it by -(integral of delta over the transit): for a component at a rate proportional to the speed
 * that is a sine of fixed depth relative to the delay (the same pitch deviation at every TIME), and for the drift its
 * mean over the transit (a one-pole of time constant T / 2).
 * A switch to or from TAPE fades the running type out, then the new one in (TAPE per sample, DIG / ANA per block). */

#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#define TP_CTL 16u                                      /* the wow / flutter control step, samples */
#ifndef TP_WOWX
#define TP_WOWX 256                                     /* wow and flutter depth, Q8 (listening tests: other depths) */
#endif
static int32_t tp_b1, tp_b2, tp_h1, tp_h2;              /* the bump's SVF (band, low; Q4), the HF loss one-poles */
static int32_t tp_n1, tp_n2, tp_n3, tp_avg, tp_dc = -1; /* drift low-passes (Q6), depth drift, transit mean, delay Q12 */
static uint32_t tp_nz = 0x2545F491u, tp_ph1;           /* noise; the capstan's phase (Q32) */
static int32_t tp_dq;                                   /* the gliding delay (Q12): the motor's speed */
static int32_t tp_g;                                    /* TAPE's fade gain (Q15, 32768 = in) */
static int32_t dly_xg = 32768;                          /* DIG / ANA's fade gain around TAPE (Q15) */
static uint8_t dly_cur, dly_last;                       /* the line's owner: 3 TAPE, else the old loops; their last sw */
static int32_t tp_kf, tp_kq = TP_KQ, tp_kh;             /* this block's coefficients (Q24, Q14, Q15) */

/* the wow / flutter pre-pass: dly_dt[] = this block's delay per sample (Q8); s = log2 speed re the reference (Q12),
 * lv = log2 T (Q12) */
__attribute__((noinline)) static void tp_mod(uint32_t n, int32_t target, int32_t s, int32_t lv)
{
    int32_t dq = tp_dq, d0 = tp_dc, v = exp2_q16(s), iv = exp2_q16(-s), t12 = target << 4;
    int32_t kw = exp2_q16((5 << 12) - lv);              /* 32 / T in Q16: the transit mean's one-pole per step */
    uint32_t i1 = (uint32_t)(((uint64_t)TP_INC1 * (uint32_t)v) >> 16);
    int32_t a1 = (int32_t)(((int64_t)(TP_A1 * TP_WOWX >> 8) * iv) >> 16), a2 = (int32_t)(((int64_t)(TP_A2 * TP_WOWX >> 8) * iv) >> 16);
    int32_t a3 = (int32_t)(((int64_t)(TP_A3 * TP_WOWX >> 8) * iv) >> 16);   /* the flutter depths in samples (Q12): ~ T */
    uint32_t j, k;
    if (d0 < 0) d0 = dq = t12;                         /* TAPE starts (faded out): the motor at speed */
    for (j = 0; j < n; j += TP_CTL) {
        int32_t wn, dc, fl, st, fr;
        dq += (int32_t)(((int64_t)(t12 - dq) * (t12 > dq ? TP_KUP : TP_KDN)) >> 16);   /* TIME: the motor's speed
                                                         * follows (time constants TP_KUP / TP_KDN) */
        tp_nz = tp_nz * 1664525u + 1013904223u;
        wn = (int32_t)tp_nz >> 16;                      /* +-32768 */
        tp_n1 += ((wn << 5) - tp_n1) * TP_KN >> 16;     /* the drift: noise through two low-passes */
        tp_n2 += (tp_n1 - tp_n2) * TP_KN >> 16;
        tp_n3 += ((wn << 5) - tp_n3) * TP_KM >> 16;     /* the flutter's depth drift (slower) */
        tp_avg += (int32_t)(((int64_t)(tp_n2 - tp_avg) * kw) >> 16);   /* the drift's mean over the transit */
        tp_ph1 += i1;                                   /* the capstan: its rotation and 2nd / 3rd harmonics */
        fl = (int32_t)(((int64_t)a1 * osc_sine(tp_ph1) + (int64_t)a2 * osc_sine(tp_ph1 * 2u + 0x40000000u)
                        + (int64_t)a3 * osc_sine(tp_ph1 * 3u + 0x9000000u)) >> 15);
        fl += (int32_t)(((int64_t)fl * tp_n3 * TP_DM) >> 30);   /* the depth drifts */
        fr = (int32_t)(((int64_t)tp_avg * (TP_DRIFT * TP_WOWX >> 8)) >> 16);   /* the mean speed error, Q24 */
        dc = dq - (int32_t)(((int64_t)dq * fr) >> 24) + fl;   /* Q12 */
        st = (dc - d0) >> 4;
        for (k = 0; k < TP_CTL; k++) { d0 += st; dly_dt[j + k] = d0 >> 4; }
        d0 = dc;
    }
    tp_dq = dq; dly_d = dq >> 4; tp_dc = d0;
}

/* the tape pass: the send (m[], ramped: dly_ramp) through the tape's read, the playback head and the record head
 * (dt: the delay per sample, from tp_mod); m[j] becomes sample j's echo (heard x wet: tp_loop). The dry's ease and the sum
 * into L / R in a pass of their own (tp_loop; 0.9.5 cpu-trim: in one loop the 20 values spilled, 128 instructions a
 * sample with 8 stack stores and 17 loads; each sample's arithmetic is the same, in the same order) */
static inline __attribute__((always_inline)) void tp_tape_k(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                            int32_t fb, const int rst)
{
    uint32_t w = dly_w, j, c = rst ? w - dly_rb : 0x7FFFFFFFu;
    int32_t b1 = tp_b1, b2 = tp_b2, h1 = tp_h1, h2 = tp_h2;
    FX_K32 kf = tp_kf, kr = tp_kq, kh = tp_kh, kfb = fb;
    for (j = 0; j < n; j++) {
        int32_t d, r, x, y, in;
        uint32_t i;
        d = dt[j];
        i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        if (rst && c <= DLY_LEN) {                      /* (as dly_loop: behind the clean span reads 0) */
            uint32_t lb = (w - i) & (DLY_LEN - 1u);
            int32_t a0 = ((lb - 1u) & (DLY_LEN - 1u)) < c ? dly_buf[i] : 0;
            int32_t a1 = (lb & (DLY_LEN - 1u)) < c ? dly_buf[(i - 1u) & (DLY_LEN - 1u)] : 0;
            r = (a0 + (((a1 - a0) * (d & 255)) >> 8)) << 1;
            c++;
        } else
            r = (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
        /* the playback head: the bump (2nd-order high-pass, resonant; Q4 inside) */
        x = (r << 4) - b2 - (int32_t)(((int64_t)b1 * kr) >> 14);
        {   /* (kf, kh read once a sample each, used twice: one stack load, not two) */
            int32_t f = kf;
            b1 += (int32_t)(((int64_t)x * f) >> 24);
            b2 += (int32_t)(((int64_t)b1 * f) >> 24);
        }
        y = x >> 4;
        /* the gap / spacing loss */
        {
            int32_t h = kh;
            h1 += (int32_t)(((int64_t)(y - h1) * h) >> 15);
            h2 += (int32_t)(((int64_t)(h1 - h2) * h) >> 15);
        }
        y = h2;
        /* the record head and the tape: send + regeneration through the transfer curve */
        in = m[j] & msk;                                /* (the send, ramped: dly_ramp; read here, where it is used) */
        x = PH_CL((in >> 1) + (int32_t)(((int64_t)y * kfb) >> 15), -65536, 65535) + 65536;
        i = (uint32_t)x >> 9;
        x &= 511;
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)(FX_TAPE_SAT[i] + (((FX_TAPE_SAT[i + 1u] - FX_TAPE_SAT[i]) * x) >> 9));
        w++;
        m[j] = y;                                       /* (heard x wet in tp_loop: ramped there) */
    }
    dly_w = w; tp_b1 = b1; tp_b2 = b2; tp_h1 = h1; tp_h2 = h2;
}
/* (three copies, 0.9.5 fx-trim: the developer reset's clean span only where it is (elsewhere c stays 0x7FFFFFFF, its
 * test never true), and the send on (-1) or off (0) as constants: 78 -> 71 instructions a sample with the send on,
 * 68 off, ~600 B more code, in .ram_text with tp_tape) */
FR_RAM __attribute__((noinline)) static void tp_tape(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                     int32_t fb)
{
    if (dly_rst) tp_tape_k(m, dt, n, msk, fb, 1);
    else if (msk) tp_tape_k(m, dt, n, -1, fb, 0);
    else tp_tape_k(m, dt, n, 0, fb, 0);
}

/* the sample loop: the tape pass (tp_tape: m[] in place, the echoes), then the dry easing to dgt plus the echo, x
 * the echo's gain ramped from w0 (the last block's) to wet */
__attribute__((noinline)) static void tp_loop(int32_t *L, int32_t *R, int32_t *m, const int32_t *dt, uint32_t n,
                                                          const int32_t msk, int32_t sg16, int32_t fb, int32_t wet,
                                                          int32_t dgt, int32_t w0)
{
    int32_t dg = dly_dg, *e = L + n, wq = w0 << 8, ws = DLY_WSTEP(w0, wet, n);
    if (msk) FR_FAR(dly_ramp)(m, n, sg16);                      /* (each fade piece its own ramp, as it was) */
    FR_FAR(tp_tape)(m, dt, n, msk, fb);
    for (; L < e; L++, R++) {
        int32_t y;
        wq += ws;
        y = (int32_t)(((int64_t)*m++ * (wq >> 8)) >> 15);
        dg += (dgt - dg) >> 9;
        *L = (int32_t)(((int64_t)*L * dg) >> 15) + y; *R = (int32_t)(((int64_t)*R * dg) >> 15) + y;
    }
    dly_dg = dg; dly_sgk = msk ? sg16 : 0;              /* (fx.c dly_son) */
}

/* one block of TAPE (or its fade-out / tail): the coefficients for this TIME and TONE, the pre-pass, the loop.
 * gt: the fade's target (32768 in, 0 out); msk: the send on / off */
__attribute__((noinline)) static void tp_block(int32_t *L, int32_t *R, uint32_t n, int32_t target, int32_t msk,
                                               int32_t sg16, int32_t fb, int32_t wet, int32_t dgt, int32_t gt)
{
    int32_t lv = log2_q12((uint32_t)target >> 8), s = PH_CL(TP_LT_REF - lv, -TP_S_MAX, TP_S_MAX), v = exp2_q16(s), h;
    tp_kf = (int32_t)(((int64_t)TP_KF_REF * v) >> 16);
    h = PH_CL(TP_LH_REF + s + (fx_p[FXP_DL_TONE] - 70) * TP_TONE_K, 0, (128 << 8) - 1);   /* log2 HF corner, Q8 re 250 Hz */
    tp_kh = FX_TAPE_HF[h >> 8] + (((FX_TAPE_HF[(h >> 8) + 1] - FX_TAPE_HF[h >> 8]) * (h & 255)) >> 8);
    fb = FX_TAPE_FB[fx_p[FXP_DL_FDBK] & 127];           /* TAPE's regeneration: FDBK alone (DIG / ANA scale it by MIX
                                                         * too); the loop's gain at the bump < 1 up to FDBK 119, then a
                                                         * bounded build-up held by the tape's saturation */
    wet = (wet * TP_WET) >> 12;                         /* level-matched to ANA */
    tp_mod(n, target, s, lv);
    if (tp_g == gt) tp_loop(L, R, dly_m, dly_dt, n, msk, sg16, fb, wet, dgt, (dly_wk * TP_WET) >> 12);
    else {                                              /* fading: the echo's gain (feedback and heard) steps every 8
                                                         * samples (0.4 % a step), the same loop (no second one: flash) */
        uint32_t j;
        for (j = 0; j < n; j += 8u) {
            tp_g = PH_CL(tp_g + (gt ? 8 * TP_FADE : -8 * TP_FADE), 0, 32768);
            int32_t wg = (wet * tp_g) >> 15;           /* (a fade: the echo's gain steps with it, as it did) */
            tp_loop(L + j, R + j, dly_m + j, dly_dt + j, 8u, msk, sg16, (fb * tp_g) >> 15, wg, dgt, wg);
        }
    }
}

static void tp_zero(void) { tp_b1 = tp_b2 = tp_h1 = tp_h2 = 0; tp_g = 0; tp_dc = -1; }
static void tp_reset(void)                              /* developer CC 10: the transport and filters to boot state */
{
    tp_b1 = tp_b2 = tp_h1 = tp_h2 = 0; tp_dc = -1;
    tp_n1 = tp_n2 = tp_n3 = tp_avg = 0; tp_nz = 0x2545F491u; tp_ph1 = 0;
}

/* DIG / ANA (tape = 0 / 1, runtime) fading: dly_loop's arithmetic sample for sample (fx.c), the echo read times g
 * (dly_xg, to gt by gs per sample): heard and fed back. Runs only in the blocks around a switch to / from TAPE, so the
 * old loops keep their code (a block-wise step of the mix was a click: 10x the second difference, host).
 * tape = 2 / 3: DIG -> ANA / ANA -> DIG under ringing echoes (0.9.6, BUGHUNT #17): ANA's share a (dly_xm, Q15) moves
 * TP_FADE a sample and blends what ANA adds: the wow and flutter's depth, the darker tone (col: DIG's), the softclip;
 * the delay time slews as the type with the larger share. At a = 0 / 32768 the integers are DIG's / ANA's (dly_xm:
 * fx.c) */
/* DIG's TIME changed (0.9.6, BUGHUNT #3: the time glided one sample a sample, so while it rose the read head stood
 * still, one held sample, and the echoes were lost; falling, they played an octave up): the old tap and the new one
 * (dly_tn) crossfade over 1024 samples (23 ms, dly_tx the new one's share), then the new is the delay time; a time
 * that moves on meanwhile is the next crossfade's. ANA's slew is capped at 1/16 sample a sample (it scrubbed at up to
 * ~5x: a bend stays a bend). fx_delay_t routes those blocks here (tp_route); at rest the old loops */
#define DLY_TXS 32
static int32_t dly_tn RH_SP_SEC(".bss.fx_dlytn"), dly_tx RH_SP_SEC(".bss.fx_dlytx");
static inline __attribute__((always_inline)) int32_t dly_rd(uint32_t w, int32_t d, uint32_t c)   /* the tap at d (Q8) */
{
    uint32_t i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
    if (c <= DLY_LEN) {
        uint32_t lb = (w - i) & (DLY_LEN - 1u);
        int32_t a0 = ((lb - 1u) & (DLY_LEN - 1u)) < c ? dly_buf[i] : 0;
        int32_t a1 = (lb & (DLY_LEN - 1u)) < c ? dly_buf[(i - 1u) & (DLY_LEN - 1u)] : 0;
        return (a0 + (((a1 - a0) * (d & 255)) >> 8)) << 1;
    }
    return (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
}
__attribute__((noinline)) static void dly_fade(int32_t *L, int32_t *R, uint32_t n, int tape, int32_t msk,
                                               int32_t sg16, int32_t target, int32_t col, int32_t fb, int32_t wet,
                                               int32_t dgt, int32_t gs)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, j, c = dly_rst ? w - dly_rb : 0x7FFFFFFFu;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg, g = dly_xg;
    int32_t kq = dly_sgk << 8, dk = n ? ((sg16 - dly_sgk) << 8) / (int32_t)n : 0;
    int32_t a = tape == 0 ? 0 : tape == 1 ? 32768 : dly_xm, da = tape == 2 ? TP_FADE : tape == 3 ? -TP_FADE : 0;
    int32_t cd = tape >= 2 ? ((col * 19661) >> 15) - col : 0, c0 = col;
    int32_t wq = dly_wk << 8, ws = DLY_WSTEP(dly_wk, wet, n);   /* the echo's gain: a ramp from the last block's */
    int32_t tn = dly_tn, tx = dly_tx;
    for (j = 0; j < n; j++) {
        int32_t d, r, in = ((L[j] + R[j]) >> 1) & msk, f;
        kq += dk;
        in = (int32_t)(((int64_t)in * (kq >> 8)) >> 16);
        a = PH_CL(a + da, 0, 32768);
        col = c0 + (int32_t)(((int64_t)cd * a) >> 15);
        if (a) {
            int32_t sf;
            if (a >= 16384) dd += PH_CL((target - dd) >> 12, -16, 16); else dd += PH_CL(target - dd, -256, 256);
            tx = 0;
            wow += 69000u; flt += 690000u;
            sf = osc_sine(flt);
            sf = (sf + (int32_t)((uint32_t)sf >> 31)) >> 1;
            d = dd + (int32_t)(((((int64_t)dd * (osc_sine(wow) * 3 + sf)) >> 25) * a) >> 15);
        } else {
            if (!tx && dd != target) { tn = target; tx = 1; }   /* (a new time: the crossfade starts) */
            d = dd;
        }
        r = dly_rd(w, d, c);
        if (tx) {                                       /* DIG's two taps */
            int32_t r1 = dly_rd(w, tn, c);
            tx = tx + DLY_TXS > 32768 ? 32768 : tx + DLY_TXS;
            r += (int32_t)(((int64_t)(r1 - r) * tx) >> 15);
            if (tx == 32768) { dd = tn; tx = 0; }
        }
        if (c <= DLY_LEN) c++;
        g = PH_CL(g + gs, 0, 32768);
        r = (int32_t)(((int64_t)r * g) >> 15);
        lp += (int32_t)(((int64_t)(r - lp) * col) >> 15);
        f = lp;
        if (a) f += (int32_t)(((int64_t)(softclip(lp) - lp) * a) >> 15);
        hp += (f - hp + 64) >> 7;
        f -= hp;
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi((in >> 1) + (int32_t)(((int64_t)f * fb) >> 15), -32768, 32767);
        w++;
        wq += ws;
        r = mulq15(r, wq >> 8);
        dg += (dgt - dg) >> 9;
        L[j] = (int32_t)(((int64_t)L[j] * dg) >> 15) + r; R[j] = (int32_t)(((int64_t)R[j] * dg) >> 15) + r;
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg; dly_sgk = msk ? sg16 : 0;
    dly_xg = g; dly_xm = a; dly_tn = tn; dly_tx = tx;
}

/* the blocks fx_delay_t hands to tp_route: TAPE, a switch to / from it, DIG <-> ANA (#17), a DIG time change, ANA's
 * big time change with sound in the line (#3) */
__attribute__((noinline)) static int dly_routed(int32_t sw, int32_t target)
{
    return dly_xo || sw == 3 || dly_cur == 3 || dly_xg != 32768 || dly_tx || (sw == 1 && dly_d != target) ||
           (sw == 2 && (dly_lp || dly_hp || dly_sil < DLY_LEN) && iabs(target - dly_d) > 65536);
}
/* fx_delay_t's router: 1 = this block is done here, 0 = the old loops run it as always. dly_cur: 3 while TAPE owns
 * the line (TAPE, its fade-out, its tail after OFF), else the old loops (DIG / ANA / their tail; dly_last the EFFECT
 * they last ran with). A switch to or from TAPE: the running type fades out, then the new one fades in. */
__attribute__((noinline)) static int tp_route(int32_t *L, int32_t *R, uint32_t n, int32_t sw, int32_t target,
                                              int32_t sg16, int32_t fb, int32_t wet, int32_t depth)
{
    int32_t dgt = 32768 - depth * depth, osw = dly_cur == 3 ? sw : dly_last;
    int32_t col = 2000 + fx_p[FXP_DL_TONE] * 240;
    if (dly_xo) {                                       /* DIG <-> ANA (fx.c dly_xo): the crossfade, tape 2 / 3 */
        dly_fade(L, R, n, dly_ana ? 2 : 3, sw ? -1 : 0, sg16, target, col, fb, wet, sw ? dgt : 32768, 0);
        if (dly_xm == (dly_ana ? 32768 : 0)) dly_xo = 0;
        return 1;
    }
    if (osw == 2) col = (col * 19661) >> 15;
    if (dly_cur == 3) {
        if (sw == 3 || (sw == 0 && tp_g)) {             /* TAPE (fading in), or its tail after OFF (send off) */
            tp_block(L, R, n, target, sw ? -1 : 0, sg16, fb, wet, sw ? dgt : 32768, sw ? 32768 : tp_g);
            if (!sw && dly_tail_over(n, target) && iabs(tp_h2) < 2 && iabs(tp_b1) < 32 && iabs(tp_b2) < 32) {
                tp_zero(); dly_cur = 0; dly_last = 0;   /* (fx.c dly_tail_over: the line ~0 over the delay first) */
            }
            return 1;
        }
        if (tp_g) {                                     /* DIG / ANA asked for: TAPE fades out first */
            tp_block(L, R, n, target, -1, sg16, fb, wet, dgt, 0);
            return 1;
        }
        tp_zero(); dly_cur = 0; dly_xg = 0; dly_lp = dly_hp = 0;   /* then they fade in (at the time: no bend) */
        dly_d = target; dly_tx = 0;
        dly_last = (uint8_t)sw;
    }
    if (sw == 3) {
        if ((dly_lp || dly_hp) && dly_xg) {             /* a DIG / ANA line rings: it fades out first */
            dly_fade(L, R, n, dly_last == 2, dly_last ? -1 : 0, sg16, target, col, fb, wet,
                     dly_last ? dgt : 32768, -TP_FADE);
            return 1;
        }
        dly_xg = 32768; dly_lp = dly_hp = 0; dly_cur = 3; tp_g = 0;
        tp_block(L, R, n, target, -1, sg16, fb, wet, dgt, 32768);
        return 1;
    }
    dly_last = (uint8_t)sw;
    if (dly_xg == 32768) {
        if (!sw) return 0;
        dly_fade(L, R, n, sw == 2, -1, sg16, target, col, fb, wet, dgt, 0);   /* (a time change: DIG's crossfade, ANA's
                                                         * capped slew) */
        return 1;
    }
    dly_fade(L, R, n, sw == 2, sw ? -1 : 0, sg16, target, col, fb, wet, sw ? dgt : 32768, TP_FADE);   /* fading in */
    return 1;
}
