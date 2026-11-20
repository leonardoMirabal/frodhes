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
#ifndef FR_CHUNK
#define FR_CHUNK 1                               /* the chunk routines of dly_tape.c exist */
#endif
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
#define dly_rd unused_dly_rd
#define dly_fade unused_dly_fade
#define dly_loop_c unused_dly_loop_c
#define dly_loop_cd unused_dly_loop_cd
#define dly_loop_ca unused_dly_loop_ca
#define dly_fade_c unused_dly_fade_c
#define dly_wr_c unused_dly_wr_c
#define tp_loop_c unused_tp_loop_c
#endif
#include "fx.c"
#if !FR_CHUNK
#include "rev_types.c"
#endif
#if defined(SIDE_NEW) && !defined(DLY_FROM_FX)
#undef dly_loop
#undef dly_loop_kb
#undef dly_rd
#undef dly_fade
#undef dly_loop_c
#undef dly_loop_cd
#undef dly_loop_ca
#undef dly_fade_c
#undef dly_wr_c
#undef tp_loop_c
#ifdef DLY_OWN_FILE
#define DLY_OWN_INC DLY_OWN_FILE
#else
#define DLY_OWN_INC "dly_own.c"
#endif
#define DLY_OWN_PART 1                           /* the four places fx.c / dly_tape.c include it from */
#include DLY_OWN_INC
#undef DLY_OWN_PART
#define DLY_OWN_PART 2
#include DLY_OWN_INC
#undef DLY_OWN_PART
#define DLY_OWN_PART 3
#include DLY_OWN_INC
#undef DLY_OWN_PART
#define DLY_OWN_PART 4
#include DLY_OWN_INC
#undef DLY_OWN_PART
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
    memcpy(dly_fbv, s->fbv, sizeof s->fbv);
}
void P(get)(dly_st_t *s)
{
#define F(n, t) s->n = n;
    DLY_SCALARS(F)
#undef F
    memcpy(s->hc, fx_h_clip, sizeof s->hc);
    memcpy(s->m, dly_m, sizeof s->m);
    memcpy(s->dt, dly_dt, sizeof s->dt);
    memcpy(s->fbv, dly_fbv, sizeof s->fbv);
}
static __attribute__((noinline)) void s5(const dly_args_t *a, int32_t *L, int32_t *R)
{
    dly_fade(L, R, a->n, a->mode, a->msk, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->gs);
}
static __attribute__((noinline)) void s6(const dly_args_t *a, int32_t *L, int32_t *R)
{
    fxc_o = a->o; fxc_e = a->e;
    dly_loop_cd(L, R, a->n, a->target, a->col, a->fb, a->wet, a->dgt);
}
static __attribute__((noinline)) void s7(const dly_args_t *a, int32_t *L, int32_t *R)
{
    fxc_o = a->o; fxc_e = a->e;
    dly_loop_ca(L, R, a->n, a->target, a->col, a->fb, a->wet, a->dgt);
}
static __attribute__((noinline)) void s8(const dly_args_t *a, int32_t *L, int32_t *R)
{
    fxc_o = a->o; fxc_e = a->e;
    dly_fade_c(L, R, a->n, a->mode, a->msk, a->target, a->col, a->fb, a->wet, a->dgt, a->gs);
}
static __attribute__((noinline)) void s9(const dly_args_t *a, int32_t *L, int32_t *R)
{
    (void)L; (void)R;
    dly_wr_c(a->w0, a->n, a->sg16);
}
static __attribute__((noinline)) void s10(const dly_args_t *a, int32_t *L, int32_t *R)
{
    tp_loop_c(L, R, a->j0, a->j1, a->p0, a->np, a->fb, a->wet, a->dgt, a->g0);
}
static __attribute__((noinline)) void s11(const dly_args_t *a, int32_t *L, int32_t *R)
{
    (void)R;
    L[0] = dly_rd(dly_w, a->d, a->c);          /* the result comes back in L[0] */
}
void P(call)(const dly_args_t *a, int32_t *L, int32_t *R)
{
    h_ek = a->ek; h_eg = a->eg;
    switch (a->shape) {
    case 0: s0(L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst); break;
    case 1: s1(L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst); break;
    case 2: s2(L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst); break;
    case 3: s3(L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst); break;
    case 4: s4(L, R, a->n, a->sg16, a->target, a->col, a->fb, a->wet, a->dgt, a->rst); break;
    case 5: s5(a, L, R); break;
    case 6: s6(a, L, R); break;
    case 7: s7(a, L, R); break;
    case 8: s8(a, L, R); break;
    case 9: s9(a, L, R); break;
    case 10: s10(a, L, R); break;
    default: s11(a, L, R); break;
    }
}
int16_t *P(line)(void) { return dly_buf; }
