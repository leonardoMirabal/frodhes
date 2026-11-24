/* SPDX-License-Identifier: GPL-3.0-only */
/* dly_equiv's shared declarations: the delay state the two sides exchange, and the side interface (dly_side.c is
 * compiled once as `old` and once as `new`; dly_equiv.c drives both). */
#ifndef DLY_STATE_H
#define DLY_STATE_H
#include <stdint.h>
#define DLYQ_LEN 65536u
/* every scalar the loops, or their callers' contract, touch (the spec's section 1 and the delay's neighbours) */
#define DLY_SCALARS(X)                                                                                              \
    X(dly_w, uint32_t) X(dly_wow, uint32_t) X(dly_flt, uint32_t) X(dly_d, int32_t) X(dly_lp, int32_t)              \
    X(dly_hp, int32_t) X(dly_dg, int32_t) X(dly_sgk, int32_t) X(dly_wk, int32_t) X(dly_fbk, int32_t)               \
    X(dly_rst, uint8_t) X(dly_rb, uint32_t) X(dly_sg, int32_t) X(dly_son, uint8_t) X(dly_clrn, uint32_t)           \
    X(dly_qz, uint32_t) X(dly_lc, uint8_t) X(dly_xm, int32_t) X(dly_xo, uint8_t) X(dly_sil, uint32_t)              \
    X(dly_ana, uint8_t) X(dly_qlo, int32_t) X(dly_qhi, int32_t) X(dly_ring, uint8_t) X(dly_rf, uint8_t)              \
    X(dly_xg, int32_t) X(dly_tn, int32_t) X(dly_tx, int32_t) X(dly_wm, uint8_t) X(dly_wp, uint8_t)                 \
    X(dly_wk_msk, int32_t) X(tp_b1, int32_t) X(tp_b2, int32_t) X(tp_h1, int32_t) X(tp_h2, int32_t)                  \
    X(tp_kf, int32_t) X(tp_kq, int32_t) X(tp_kh, int32_t)
typedef struct {
#define F(n, t) t n;
    DLY_SCALARS(F)
#undef F
    uint32_t hc[5];                              /* the host's headroom clip counters (FX_HC) */
    int32_t m[256], dt[256], fbv[256];           /* dly_m, dly_dt, dly_fbv */
} dly_st_t;
typedef struct {
    int shape, rst;                              /* shape: see dly_shape_names (0..4 the main loop, 5.. dly_tape.c's) */
    uint32_t n;
    int32_t sg16, target, col, fb, wet, dgt, ek, eg;
    int mode, msk;                               /* the fade's `tape` 0..3, its / the mask */
    int32_t gs;                                  /* the fade's gain step */
    uint32_t o, e;                               /* fxc_o, fxc_e */
    uint32_t w0;                                 /* dly_wr_c's first row */
    uint32_t j0, j1, p0, np;                     /* tp_loop_c */
    int32_t g0;
    uint32_t c;                                  /* dly_rd's clean count */
    int32_t d;                                   /* dly_rd's time */
} dly_args_t;
/* 0..3 dly_loop (tape1/msk-1, tape1/msk0, tape0/msk-1, tape0/msk0, kb 0), 4 dly_loop_kb, 5 dly_fade, 6 dly_loop_cd,
 * 7 dly_loop_ca, 8 dly_fade_c, 9 dly_wr_c, 10 tp_loop_c, 11 dly_rd */
/* 12..19 tp_tape_k with literal (rst, msk, kb) = bit 0 kb, bit 1 msk (0: -1, 1: 0), bit 2 rst; 20 tp_tape_kb, 21 tp_tape,
 * 22 tp_loop */
#define DLY_SHAPES 23
#define DLY_DECL(P)                                                                                                 \
    void P##_put(const dly_st_t *s);             /* the state in */                                                 \
    void P##_get(dly_st_t *s);                   /* the state out */                                                \
    void P##_call(const dly_args_t *a, int32_t *L, int32_t *R);                                                    \
    int16_t *P##_line(void);                     /* the side's own line (dly_buf), read / write */
DLY_DECL(old)
DLY_DECL(new)
#endif
