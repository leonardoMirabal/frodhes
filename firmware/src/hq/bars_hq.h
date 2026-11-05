/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ bars (VIBES and MARIMBA on the Move only: FR_HQ; private plan 07): their state, included at the end of
 * bars.h. The code is hq/bars_hq.c, included by bars.c. Never in the FM-1 / SMK builds (tools/build.py
 * float_check). The HQ bar voice is the Classic bar voice (bars.c: V1 + TUBE as the integer pair, the motor, the
 * damper, the levels, the sleep, the calibration, the same integers) with three parts changed, each latched per
 * voice at its note-on (hq_mode, hq/rhodes_hq.h: one switch for every engine; a sounding note keeps its path):
 *   the upper slots   V2 V3 V4 T1 T2 in float, 4 slots a vector (the coupled form x += e y, the same poles and the
 *                     same force-to-x transfer as the integer two-multiply form), and no trimming (M3-03 already)
 *   more modes        V5 V6 V7 (bending) and T3 T4 (torsion: off the centreline only, as T1 T2), m[8 .. 12], from
 *                     the FE of each key (firmware/gen/hq/bars_hq_tables.h, tools/gen_bars_hq.py)
 *   the contact       a re-strike of a ringing bar (and every strike on MARIMBA's top keys, where the velocity
 *                     anchors cannot follow V1's place on the pulse spectrum): the mallet contact computed from the
 *                     bar's state (hq/bars_contact.c), at most HQB_CK_MAX contacts a block; the rest the tables
 * hq_mode 0: every bar note is Classic, the same integers as without FR_HQ (Gate M). */

#ifndef HQB_EXTRA
#define HQB_EXTRA 1                            /* the extra modes (0: the 5 Classic upper slots only, the parity test) */
#endif
#ifndef HQB_CONTACT
#define HQB_CONTACT HQB_EXTRA                  /* the live contact (0: the tables' pulse always) */
#endif
#ifndef HQB_CK_MAX
#define HQB_CK_MAX 2u                          /* contacts a block at most (a law each; plan 07 B3's cost cap) */
#endif
#define HQB_NL 12u                             /* float lanes: m 2..6, m 8..12, two spare */
#define HQB_NVEC (HQB_NL / 4u)
typedef float hqb_f4 __attribute__((vector_size(16)));
typedef struct {
    hqb_f4 x[HQB_NVEC], y[HQB_NVEC];           /* each lane's x (Q25 units, as bar_ms_t.x1) and y (x[n] - x[n-1] = e y) */
} hqb_st_t;
static hqb_st_t hqb_st[BAR_NV];
static const uint8_t HQB_SLOT[HQB_NL] = {2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 0xFF, 0xFF};
#define BAR_NMV(v) ((v)->hq ? BAR_NM + HQB_NX : BAR_NM)   /* the slots a voice runs */

static void hqb_init(bar_voice_t *v, uint32_t key, uint32_t tyi, int32_t tone, uint32_t pi, uint32_t pf);
static void hqb_upper(bar_voice_t *v, uint32_t o, uint32_t n, uint32_t nf);
static void hqb_sync(bar_voice_t *v);
#if HQB_CONTACT
static void hqb_pulse(bar_voice_t *v, uint32_t vel, int restrike);
#endif
