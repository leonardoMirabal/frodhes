/* SPDX-License-Identifier: GPL-3.0-only */
/* One side of the delay equivalence harness (tests/c/dly_equiv.c): fx.c whole, and its delay loops reached through
 * a small interface (dly_state.h). Compiled twice, -DSIDE_OLD (the loops as fx.c has them: old_*) or -DSIDE_NEW
 * (the loops of firmware/src/dly_own.c: new_*). SIDE_NEW has two ways to get them:
 *   default           fx.c's own loops are renamed out of the way, then firmware/src/dly_own.c (or DLY_OWN_FILE)
 *                     is included after fx.c, its two routines named dly_loop / dly_loop_kb, as they will be in fx.c
 *   -DDLY_FROM_FX     fx.c itself includes dly_own.c in place of the loops (the sparse tree): nothing is renamed
 * Host only. */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef DINGHY_HOST
#define DINGHY_HOST 1
#endif
#include "fxp.h"
#define FS 44100u
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
static int32_t h_ek = 32768, h_eg = 32768;     /* the bars' level, read through FX_ENG_K / FX_ENG_G */
static uint8_t engc_fx __attribute__((unused));
#define FX_ENG_K h_ek
#define FX_ENG_G h_eg
#define FX_ENG_DT 0
#if defined(SIDE_NEW) && !defined(DLY_FROM_FX)
#define dly_loop unused_dly_loop
#define dly_loop_kb unused_dly_loop_kb
#endif
#include "fx.c"
#include "rev_types.c"
#if defined(SIDE_NEW) && !defined(DLY_FROM_FX)
#undef dly_loop
#undef dly_loop_kb
#ifdef DLY_OWN_FILE
#include DLY_OWN_FILE
#else
#include "dly_own.c"
#endif
#endif
#include "dly_state.h"
#ifdef SIDE_NEW
#define P(x) new_##x
#else
#define P(x) old_##x
#endif
#define ARGS L, R, n, sg16, target, col, fb, wet, dgt, rst
#define PARS int32_t *L, int32_t *R, uint32_t n, int32_t sg16, int32_t target, int32_t col, int32_t fb, int32_t wet, \
             int32_t dgt, int rst
static __attribute__((noinline)) void s0(PARS) { dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, dgt, rst, 0); }
static __attribute__((noinline)) void s1(PARS) { dly_loop(L, R, n, 1, 0, sg16, target, col, fb, wet, dgt, rst, 0); }
static __attribute__((noinline)) void s2(PARS) { dly_loop(L, R, n, 0, -1, sg16, target, col, fb, wet, dgt, rst, 0); }
static __attribute__((noinline)) void s3(PARS) { dly_loop(L, R, n, 0, 0, sg16, target, col, fb, wet, dgt, rst, 0); }
static __attribute__((noinline)) void s4(PARS) { dly_loop_kb(L, R, n, sg16, target, col, fb, wet, dgt, rst); }
void P(put)(const dly_st_t *s)
{
#define F(n, t) n = s->n;
    DLY_SCALARS(F)
#undef F
    memcpy(fx_h_clip, s->hc, sizeof s->hc);
    memcpy(dly_m, s->m, sizeof s->m);
    memcpy(dly_dt, s->dt, sizeof s->dt);
}
void P(get)(dly_st_t *s)
{
#define F(n, t) s->n = n;
    DLY_SCALARS(F)
#undef F
    memcpy(s->hc, fx_h_clip, sizeof s->hc);
    memcpy(s->m, dly_m, sizeof s->m);
    memcpy(s->dt, dly_dt, sizeof s->dt);
}
void P(call)(const dly_args_t *a, int32_t *L, int32_t *R)
{
    static void (*const fn[DLY_SHAPES])(PARS) = {s0, s1, s2, s3, s4};
    h_ek = a->ek; h_eg = a->eg;
    fn[a->shape](L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst);
}
int16_t *P(line)(void) { return dly_buf; }
