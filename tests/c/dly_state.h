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
    X(dly_ana, uint8_t) X(dly_qlo, int32_t) X(dly_qhi, int32_t) X(dly_ring, uint8_t) X(dly_rf, uint8_t)
typedef struct {
#define F(n, t) t n;
    DLY_SCALARS(F)
#undef F
    uint32_t hc[5];                              /* the host's headroom clip counters (FX_HC) */
    int32_t m[256], dt[256];                     /* dly_m, dly_dt */
} dly_st_t;
typedef struct {
    int shape, rst;                              /* shape 0..4: tape1/msk-1, tape1/msk0, tape0/msk-1, tape0/msk0, bars wrapper */
    uint32_t n;
    int32_t sg16, target, col, fb, wet, dgt, ek, eg;
} dly_args_t;
#define DLY_SHAPES 5
#define DLY_DECL(P)                                                                                                 \
    void P##_put(const dly_st_t *s);             /* the state in */                                                 \
    void P##_get(dly_st_t *s);                   /* the state out */                                                \
    void P##_call(const dly_args_t *a, int32_t *L, int32_t *R);                                                    \
    int16_t *P##_line(void);                     /* the side's own line (dly_buf), read / write */
DLY_DECL(old)
DLY_DECL(new)
#endif
