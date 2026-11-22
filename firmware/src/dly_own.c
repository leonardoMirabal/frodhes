/* SPDX-License-Identifier: GPL-3.0-only */
/* The delay's own sample loops, written from the specification (the plugin's own-code build). One file, included
 * from five places, each with DLY_OWN_PART set to its number (tests/c/dly_equiv.c lists the exact signatures):
 *   1  fx.c, where the old main loops were:         dly_loop, dly_loop_kb
 *   2  dly_tape.c, where dly_rd and dly_fade were:  dly_rd, dly_fade
 *   3  dly_tape.c (FR_CHUNK), the chunk group:      dly_wr_c, dly_loop_c, dly_loop_cd, dly_loop_ca, dly_fade_c
 *   4  dly_tape.c (FR_CHUNK), where tp_loop_c was:  tp_loop_c
 *   5  dly_tape.c, where the tape pass and loop were: tp_tape_k, tp_tape_kb, tp_tape, tp_loop
 * Each part sees everything defined above its include site. fx.c includes part 1; dly_tape.c sets the macro, includes
 * the file and undefines it for parts 2 to 5. */
#if DLY_OWN_PART == 1
/* the Q8 per-sample step of a ramp from a to b over n samples (0 when equal or n is 0) */
static inline __attribute__((always_inline)) int32_t dlo_step(int32_t a, int32_t b, uint32_t n)
{
    return a == b || !n ? 0 : ((b - a) * 256) / (int32_t)n;
}
/* the wow and flutter phases one step on; returns the modulation M (3 W + S / 2 towards zero) */
static inline __attribute__((always_inline)) int32_t dlo_mod(uint32_t *wow, uint32_t *flt)
{
    int32_t s, wv;
    *wow += 69000u;
    *flt += 690000u;
    s = osc_sine(*flt);
    s = (s + (s < 0)) >> 1;
    wv = osc_sine(*wow);
    return 3 * wv + s;
}
/* a time dd with the wow excursion */
static inline __attribute__((always_inline)) int32_t dlo_wowd(int32_t dd, int32_t m)
{
    return dd + (int32_t)(((int64_t)dd * m) >> 25);
}
/* the non-tape two-tap linear read, doubled; clean: rows behind the clean span count as 0 */
static inline __attribute__((always_inline)) int32_t dlo_lin(uint32_t i, uint32_t lb, uint32_t c, uint32_t fr,
                                                             const int clean)
{
    int32_t a0, a1;
    if (clean) {
        a0 = (((lb - 1u) & (DLY_LEN - 1u)) < c) ? dly_buf[i] : 0;
        a1 = ((lb & (DLY_LEN - 1u)) < c) ? DLY_AT(i, -1) : 0;
    } else {
        a0 = dly_buf[i];
        a1 = DLY_AT(i, -1);
    }
    return 2 * (a0 + (((a1 - a0) * (int32_t)fr) >> 8));
}
/* the dry's glide towards its target */
static inline __attribute__((always_inline)) int32_t dlo_dry(int32_t dg, int32_t dgt)
{
    int32_t e = dgt - dg;
    return dg + (e >> 9) + (e > 0 && e < 512);
}
static inline __attribute__((always_inline)) int32_t dlo_mix(int32_t x, int32_t dg, int32_t e)
{
    return (int32_t)(((int64_t)x * dg) >> 15) + e;
}
static inline __attribute__((always_inline)) void dly_loop(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                           const int32_t msk, int32_t sg16, int32_t target, int32_t col,
                                                           int32_t fb, int32_t wet, int32_t dgt, const int rst,
                                                           const int kb)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, c = dly_rst ? w - dly_rb : 0x7fffffffu, j;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    int32_t ek = 32768, eg = 32768;
    int32_t wq = dly_wk * 256, ws = dlo_step(dly_wk, wet, n);
    int32_t f0 = dly_fbk < 0 ? fb : dly_fbk;
    int32_t fq = f0 * 256, fs = dlo_step(f0, fb, n);
    if (kb) { ek = FX_ENG_K; eg = FX_ENG_G; }
    if (tape) {
        for (j = 0; j < n; j++) {
            int32_t m;
            dd += (target - dd) >> 12;
            m = dlo_mod(&wow, &flt);
            dly_dt[j] = dlo_wowd(dd, m);
        }
    }
    for (j = 0; j < n; j++) {
        int32_t s = dly_m[j] & msk, d, r, y, gain, v, e;
        uint32_t i, fr, lb;
        if (tape) d = dly_dt[j];
        else { dd += clampi(target - dd, -256, 256); d = dd; }
        i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        fr = (uint32_t)d & 255u;
        lb = (w - i) & (DLY_LEN - 1u);
        if (rst && c <= DLY_LEN) {
            if (tape) r = DLY_H4C(i, lb, c, (int32_t)fr);
            else r = dlo_lin(i, lb, c, fr, 1);
            c++;
        } else {
            if (tape) r = DLY_H4(i, (int32_t)fr);
            else r = dlo_lin(i, lb, c, fr, 0);
        }
        lp += (int32_t)(((int64_t)(r - lp) * col) >> 15);
        if (!tape) y = lp;
        else if (kb) y = fx_sck_k(lp, ek, eg);
        else y = softclip(lp);
        hp += (y - hp + 64) >> 7;
        y -= hp;
        fq += fs;
        gain = fq >> 8;
        v = (s >> 1) + (int32_t)(((int64_t)y * gain) >> 15);
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi(FX_HC(0, v, -32768, 32767), -32768, 32767);
        w++;
        wq += ws;
        e = (int32_t)(((int64_t)r * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, e);
        R[j] = dlo_mix(R[j], dg, e);
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg;
    dly_sgk = msk ? sg16 : 0;
}
__attribute__((noinline)) static void dly_loop_kb(int32_t *L, int32_t *R, uint32_t n, int32_t sg16, int32_t target,
                                                  int32_t col, int32_t fb, int32_t wet, int32_t dgt, int rst)
{
    if (rst) dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, dgt, 1, 1);
    else dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, dgt, 0, 1);
}
#elif DLY_OWN_PART == 2
static inline __attribute__((always_inline)) int32_t dly_rd(uint32_t w, int32_t d, uint32_t c)
{
    uint32_t i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
    if (c <= DLY_LEN) return DLY_H4C(i, (w - i) & (DLY_LEN - 1u), c, d & 255);
    return DLY_H4(i, d & 255);
}
__attribute__((noinline)) static void dly_fade(int32_t *L, int32_t *R, uint32_t n, int tape, int32_t msk,
                                               int32_t sg16, int32_t target, int32_t col, int32_t fb, int32_t wet,
                                               int32_t dgt, int32_t gs)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, c = dly_rst ? w - dly_rb : 0x7fffffffu, j;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    int32_t ek = FX_ENG_K, eg = FX_ENG_G;
    int32_t kq = dly_sgk * 256, dk = n ? ((sg16 - dly_sgk) * 256) / (int32_t)n : 0;
    int32_t a = tape == 0 ? 0 : tape == 1 ? 32768 : dly_xm;
    int32_t as = tape == 2 ? 16 : tape == 3 ? -16 : 0;
    int32_t cs = (tape >= 2) ? (((col * 19661) >> 15) - col) : 0;
    int32_t wq = dly_wk * 256, ws = dlo_step(dly_wk, wet, n);
    int32_t g = dly_xg, tn = dly_tn, tx = dly_tx;
    for (j = 0; j < n; j++) {
        int32_t sv, tone, d, r, y, v, e;
        sv = ((L[j] + R[j]) >> 1) & msk;
        kq += dk;
        sv = (int32_t)(((int64_t)sv * (kq >> 8)) >> 16);
        a = clampi(a + as, 0, 32768);
        tone = col + (int32_t)(((int64_t)cs * a) >> 15);
        if (a) {
            int32_t m, fl;
            if (a >= 16384) dd += clampi((target - dd) >> 12, -16, 16);
            else dd += clampi(target - dd, -256, 256);
            tx = 0;
            m = dlo_mod(&wow, &flt);
            fl = (int32_t)(((int64_t)dd * m) >> 25);
            d = dd + (int32_t)(((int64_t)fl * a) >> 15);
        } else {
            if (tx == 0 && dd != target) { tn = target; tx = 1; }
            d = dd;
        }
        r = dly_rd(w, d, c);
        if (tx) {
            int32_t r1 = dly_rd(w, tn, c);
            tx = tx + 32 < 32768 ? tx + 32 : 32768;
            r += (int32_t)(((int64_t)(r1 - r) * tx) >> 15);
            if (tx == 32768) { dd = tn; tx = 0; }
        }
        if (c <= DLY_LEN) c++;
        g = clampi(g + gs, 0, 32768);
        r = (int32_t)(((int64_t)r * g) >> 15);
        lp += (int32_t)(((int64_t)(r - lp) * tone) >> 15);
        y = lp;
        if (a) y += (int32_t)(((int64_t)(FX_SCK(lp, ek, eg) - lp) * a) >> 15);
        hp += (y - hp + 64) >> 7;
        y -= hp;
        v = (sv >> 1) + (int32_t)(((int64_t)y * fb) >> 15);
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi(FX_HC(0, v, -32768, 32767), -32768, 32767);
        w++;
        wq += ws;
        e = (int32_t)(((int64_t)r * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, e);
        R[j] = dlo_mix(R[j], dg, e);
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg;
    dly_sgk = msk ? sg16 : 0;
    dly_xg = g; dly_xm = a; dly_tn = tn; dly_tx = tx;
}
#elif DLY_OWN_PART == 3
__attribute__((noinline)) static void dly_wr_c(uint32_t w0, uint32_t n, int32_t sg16)
{
    int32_t mask = dly_wk_msk, kq = dly_sgk * 256, step;
    uint32_t j;
    if (dly_wp) step = ((sg16 - dly_sgk) * 256) / 8;
    else step = n ? ((sg16 - dly_sgk) * 256) / (int32_t)n : 0;
    for (j = 0; j < n; j++) {
        int32_t in, sv, sum, val;
        if (dly_wp && j == 8) { kq = sg16 * 256; step = 0; }
        in = dly_m[j] & mask;
        kq += step;
        sv = (int32_t)(((int64_t)in * (kq >> 8)) >> 16);
        sum = (sv >> 1) + dly_fbv[j];
        if (dly_wm == 1) val = clampi(FX_HC(0, sum, -32768, 32767), -32768, 32767);
        else if (FX_ENG_K != 32768) val = tp_sat_k(sum, FX_ENG_K, FX_ENG_G);
        else {
            int32_t u = 65536 + clampi(FX_HC(0, sum, -65536, 65535), -65536, 65535);
            uint32_t k = (uint32_t)u >> 9;
            int32_t q = u & 511;
            val = FX_TAPE_SAT[k] + (((FX_TAPE_SAT[k + 1u] - FX_TAPE_SAT[k]) * q) >> 9);
        }
        dly_buf[(w0 + j) & (DLY_LEN - 1u)] = (int16_t)val;
    }
    dly_sgk = mask ? sg16 : 0;
}
static inline __attribute__((always_inline)) void dly_loop_c(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                             int32_t target, int32_t col, int32_t fb, int32_t wet,
                                                             int32_t dgt)
{
    uint32_t o = fxc_o, e = fxc_e, w = dly_w, wow = dly_wow, flt = dly_flt, j;
    uint32_t c = dly_rst ? w - dly_rb : 0x7fffffffu;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    int32_t ek = FX_ENG_K, eg = FX_ENG_G;
    int32_t ws = dlo_step(dly_wk, wet, n), wq = dly_wk * 256 + ws * (int32_t)o;
    int32_t f0 = dly_fbk < 0 ? fb : dly_fbk;
    int32_t fs = dlo_step(f0, fb, n), fq = f0 * 256 + fs * (int32_t)o;
    if (tape) {
        for (j = o; j < e; j++) {
            int32_t m;
            dd += (target - dd) >> 12;
            m = dlo_mod(&wow, &flt);
            dly_dt[j] = dlo_wowd(dd, m);
        }
    }
    for (j = o; j < e; j++) {
        int32_t d, r, y, ev;
        uint32_t i, lb;
        if (tape) d = dly_dt[j];
        else { dd += clampi(target - dd, -256, 256); d = dd; }
        if (tape) r = dly_rd(w, d, c);
        else {
            i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
            lb = (w - i) & (DLY_LEN - 1u);
            r = dlo_lin(i, lb, c, (uint32_t)d & 255u, 1);
        }
        if (c <= DLY_LEN) c++;
        lp += (int32_t)(((int64_t)(r - lp) * col) >> 15);
        y = tape ? FX_SCK(lp, ek, eg) : lp;
        hp += (y - hp + 64) >> 7;
        y -= hp;
        fq += fs;
        dly_fbv[j] = (int32_t)(((int64_t)y * (fq >> 8)) >> 15);
        w++;
        wq += ws;
        ev = (int32_t)(((int64_t)r * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, ev);
        R[j] = dlo_mix(R[j], dg, ev);
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg;
    dly_wm = 1; dly_wp = 0; dly_wk_msk = -1;
}
__attribute__((noinline)) static void dly_loop_cd(int32_t *L, int32_t *R, uint32_t n, int32_t target, int32_t col,
                                                  int32_t fb, int32_t wet, int32_t dgt)
{
    dly_loop_c(L, R, n, 0, target, col, fb, wet, dgt);
}
__attribute__((noinline)) static void dly_loop_ca(int32_t *L, int32_t *R, uint32_t n, int32_t target, int32_t col,
                                                  int32_t fb, int32_t wet, int32_t dgt)
{
    dly_loop_c(L, R, n, 1, target, col, fb, wet, dgt);
}
__attribute__((noinline)) static void dly_fade_c(int32_t *L, int32_t *R, uint32_t n, int tape, int32_t msk,
                                                 int32_t target, int32_t col, int32_t fb, int32_t wet, int32_t dgt,
                                                 int32_t gs)
{
    uint32_t o = fxc_o, e = fxc_e, w = dly_w, wow = dly_wow, flt = dly_flt, j;
    uint32_t c = dly_rst ? w - dly_rb : 0x7fffffffu;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    int32_t ek = FX_ENG_K, eg = FX_ENG_G;
    int32_t a = tape == 0 ? 0 : tape == 1 ? 32768 : dly_xm;
    int32_t as = tape == 2 ? 16 : tape == 3 ? -16 : 0;
    int32_t cs = (tape >= 2) ? (((col * 19661) >> 15) - col) : 0;
    int32_t ws = dlo_step(dly_wk, wet, n), wq = dly_wk * 256 + ws * (int32_t)o;
    int32_t g = dly_xg, tn = dly_tn, tx = dly_tx;
    for (j = o; j < e; j++) {
        int32_t tone, d, r, y, ev;
        a = clampi(a + as, 0, 32768);
        tone = col + (int32_t)(((int64_t)cs * a) >> 15);
        if (a) {
            int32_t m, fl;
            if (a >= 16384) dd += clampi((target - dd) >> 12, -16, 16);
            else dd += clampi(target - dd, -256, 256);
            tx = 0;
            m = dlo_mod(&wow, &flt);
            fl = (int32_t)(((int64_t)dd * m) >> 25);
            d = dd + (int32_t)(((int64_t)fl * a) >> 15);
        } else {
            if (tx == 0 && dd != target) { tn = target; tx = 1; }
            d = dd;
        }
        r = dly_rd(w, d, c);
        if (tx) {
            int32_t r1 = dly_rd(w, tn, c);
            tx = tx + 32 < 32768 ? tx + 32 : 32768;
            r += (int32_t)(((int64_t)(r1 - r) * tx) >> 15);
            if (tx == 32768) { dd = tn; tx = 0; }
        }
        if (c <= DLY_LEN) c++;
        g = clampi(g + gs, 0, 32768);
        r = (int32_t)(((int64_t)r * g) >> 15);
        lp += (int32_t)(((int64_t)(r - lp) * tone) >> 15);
        y = lp;
        if (a) y += (int32_t)(((int64_t)(FX_SCK(lp, ek, eg) - lp) * a) >> 15);
        hp += (y - hp + 64) >> 7;
        y -= hp;
        dly_fbv[j] = (int32_t)(((int64_t)y * fb) >> 15);
        w++;
        wq += ws;
        ev = (int32_t)(((int64_t)r * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, ev);
        R[j] = dlo_mix(R[j], dg, ev);
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg;
    dly_xg = g; dly_xm = a; dly_tn = tn; dly_tx = tx;
    dly_wm = 1; dly_wp = 0; dly_wk_msk = msk;
}
#elif DLY_OWN_PART == 4
FX_RAM __attribute__((noinline)) static void tp_loop_c(int32_t *L, int32_t *R, uint32_t j0, uint32_t j1, uint32_t p0,
                                                     uint32_t np, int32_t fb, int32_t wet, int32_t dgt, int32_t g0)
{
    uint32_t w = dly_w, c = dly_rst ? w - dly_rb : 0x7fffffffu, j;
    int32_t dg = dly_dg, b1 = tp_b1, b2 = tp_b2, h1 = tp_h1, h2 = tp_h2;
    int32_t kf = tp_kf, kq = tp_kq, kh = tp_kh;
    int32_t ws = dlo_step(g0, wet, np), wq = g0 * 256 + ws * (int32_t)(j0 - p0);
    for (j = j0; j < j1; j++) {
        int32_t tap, x, y, ev;
        tap = dly_rd(w, dly_dt[j], c);
        if (c <= DLY_LEN) c++;
        x = tap * 16 - b2 - (int32_t)(((int64_t)b1 * kq) >> 14);
        b1 += (int32_t)(((int64_t)x * kf) >> 24);
        b2 += (int32_t)(((int64_t)b1 * kf) >> 24);
        y = x >> 4;
        h1 += (int32_t)(((int64_t)(y - h1) * kh) >> 15);
        h2 += (int32_t)(((int64_t)(h1 - h2) * kh) >> 15);
        y = h2;
        dly_fbv[j] = (int32_t)(((int64_t)y * fb) >> 15);
        w++;
        wq += ws;
        ev = (int32_t)(((int64_t)y * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, ev);
        R[j] = dlo_mix(R[j], dg, ev);
    }
    dly_w = w; dly_dg = dg; tp_b1 = b1; tp_b2 = b2; tp_h1 = h1; tp_h2 = h2;
}
#elif DLY_OWN_PART == 5
static inline __attribute__((always_inline)) void tp_tape_k(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                            int32_t fb, const int rst, const int kb)
{
    uint32_t w = dly_w, c = rst ? w - dly_rb : 0x7fffffffu, j;
    int32_t b1 = tp_b1, b2 = tp_b2, h1 = tp_h1, h2 = tp_h2;
    int32_t kf = tp_kf, kq = tp_kq, kh = tp_kh;
    int32_t ek = 32768, eg = 32768;
    if (kb) { ek = FX_ENG_K; eg = FX_ENG_G; }
    for (j = 0; j < n; j++) {
        int32_t tap, x, y, v, val;
        {   /* (the reader inline: dly_rd is defined further down the unit than this part) */
            int32_t d = dt[j];
            uint32_t i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
            if (rst && c <= DLY_LEN) {
                tap = DLY_H4C(i, (w - i) & (DLY_LEN - 1u), c, d & 255);
                c++;
            } else tap = DLY_H4(i, d & 255);
        }
        x = tap * 16 - b2 - (int32_t)(((int64_t)b1 * kq) >> 14);
        b1 += (int32_t)(((int64_t)x * kf) >> 24);
        b2 += (int32_t)(((int64_t)b1 * kf) >> 24);
        y = x >> 4;
        h1 += (int32_t)(((int64_t)(y - h1) * kh) >> 15);
        h2 += (int32_t)(((int64_t)(h1 - h2) * kh) >> 15);
        y = h2;
        v = ((m[j] & msk) >> 1) + (int32_t)(((int64_t)y * fb) >> 15);
        if (kb) val = tp_sat_k(v, ek, eg);
        else {
            int32_t u = 65536 + clampi(FX_HC(0, v, -65536, 65535), -65536, 65535);
            uint32_t k = (uint32_t)u >> 9;
            int32_t q = u & 511;
            val = FX_TAPE_SAT[k] + (((FX_TAPE_SAT[k + 1u] - FX_TAPE_SAT[k]) * q) >> 9);
        }
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)val;
        w++;
        m[j] = y;
    }
    dly_w = w; tp_b1 = b1; tp_b2 = b2; tp_h1 = h1; tp_h2 = h2;
}
__attribute__((noinline)) static void tp_tape_kb(int32_t *m, const int32_t *dt, uint32_t n, int32_t msk, int32_t fb)
{
    if (dly_rst) tp_tape_k(m, dt, n, msk, fb, 1, 1);
    else tp_tape_k(m, dt, n, msk, fb, 0, 1);
}
FX_RAM __attribute__((noinline)) static void tp_tape(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                     int32_t fb)
{
    if (FX_ENG_K != 32768) tp_tape_kb(m, dt, n, msk, fb);
    else if (dly_rst) tp_tape_k(m, dt, n, msk, fb, 1, 0);
    else tp_tape_k(m, dt, n, msk, fb, 0, 0);
}
FX_RAM __attribute__((noinline)) static void tp_loop(int32_t *L, int32_t *R, int32_t *m, const int32_t *dt, uint32_t n,
                                                     const int32_t msk, int32_t sg16, int32_t fb, int32_t wet,
                                                     int32_t dgt, int32_t w0)
{
    int32_t dg = dly_dg, ws = dlo_step(w0, wet, n), wq = w0 * 256;
    uint32_t j;
    if (msk) dly_ramp(m, n, sg16);
    tp_tape(m, dt, n, msk, fb);
    for (j = 0; j < n; j++) {
        int32_t ev;
        wq += ws;
        ev = (int32_t)(((int64_t)m[j] * (wq >> 8)) >> 15);
        dg = dlo_dry(dg, dgt);
        L[j] = dlo_mix(L[j], dg, ev);
        R[j] = dlo_mix(R[j], dg, ev);
    }
    dly_dg = dg;
    dly_sgk = msk ? sg16 : 0;
}
#endif
