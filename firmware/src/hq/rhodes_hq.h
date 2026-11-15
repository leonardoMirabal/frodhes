/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ Rhodes (the Move only: FR_HQ; private plan 06): its state and entry points, included by rhodes.c after the
 * voices (rv[]). The code is hq/rhodes_hq.c, included at the end of rhodes.c. Never in the FM-1 / SMK builds
 * (tools/build.py float_check). The HQ voice is the Classic voice (rhodes.c: its modes, damper, release, noises and
 * calibration, the same integers) with two parts replaced, each latched per voice at its note-on (a note keeps the
 * path it started with: a switch never changes a sounding note):
 *   the hammer pulse   the exact contact at the note's own velocity (firmware/gen/hq/rh_hq_pulse.h), not the blend of
 *                      the two velocity layers around it; equal to Classic at the 8 layer velocities
 *   the pickup         on keys >= HQ_OS_LO, the flux at 2x (polyphase half-band up and down) against its aliasing
 *   a re-strike        on keys >= HQ_LIVE_LO whose tine still moves: the hammer contact run from the tine's state
 *                      (hq/hq_contact.c), not the at-rest pulse superposed; at most HQ_LIVE_MAX a block
 *   the 1x pickup      on keys under HQ_OS_LO, the flux in closed form (hq_pickup1), not Classic's table; so the soft
 *                      notes' harmonic decay glide runs past the table's limit (hq_hd_step, HQ_OFF_MAX)
 *   above v120         the pulse's force at the fundamental matched to Classic's (hq_ir_tab): the level of Classic's
 *                      top layer, the exact contact's shape
 * hq_mode 0 (the default unless HQ_DEFAULT says otherwise): every note is Classic, the same integers as without
 * FR_HQ (Gate M). */

#ifndef HQ_DEFAULT
#define HQ_DEFAULT 0
#endif
#define HQ_OS_LO 85u                           /* the 2x pickup from C#6: below it the 1x pickup's aliasing is under
                                                * -140 dB (tools/hq_alias_study.py: every TYPE, v100 and v127) */
#ifndef HQ_HBP
#define HQ_HBP 16u                             /* the half-band's non-zero side taps (63 taps, Kaiser beta 8) */
#endif
#define HQ_HIS (2u * HQ_HBP)                   /* the history each filter keeps */
#define HQ_LIVE_LO 60u                         /* the live contact from C4: below it a re-strike's force differs by
                                                * under 0.2 dB from the at-rest pulse (tools/hq_restrike_study.py
                                                * --classic) */
#ifndef HQ_LIVE_MAX
#define HQ_LIVE_MAX 2u                         /* live contacts a block at most (the rest: the table; 0: none) */
#endif
#define HQ_LIVE_THR 0.01f                      /* the tine's speed at the strike line (its amplitude, re the hammer
                                                * speed) from which it runs: below, the table (a pulse within -40 dB) */

/* the exact pickup at 1x (0.9.12, the HQ uplift): every HQ note under HQ_OS_LO reads the flux in closed form (hq_dphi2)
 * instead of Classic's cubic Hermite table (2048 cells over s = u^2, int32, its words kept out of the FM-1 chip-ROM
 * window: rom_safe() moved the cells next to the pole axis by up to 25 % of their value, so Classic's offsets stop at
 * RH_OFF_MAX). The same flux to the table's error (tools/hq_uplift_study.py: -100 dB or less re the note on every key
 * and TYPE at the note-on's offset), so a note plays as Classic's at its strike. With the table's limit gone, the
 * harmonic decay glide (rhodes.c rh_hd_*, a soft strike's rest point drifting toward the pole axis as the note rings)
 * runs on to HQ_OFF_MAX (hq_hd_step), and its fundamental's gain follows the exact flux up to +24 dB (Classic's +12 dB
 * cap): a soft note's 2nd harmonic decays at ~0.8 of the fundamental's T60, as the reference instrument's (Classic: 0.54,
 * the cap reached in the first seconds) */
#ifndef HQ_OFF_MAX
#define HQ_OFF_MAX (-786432)                   /* Q25 gap (1/32" family; >> RH_FAM_SH as RH_OFF_MAX): the glide's nearest
                                                * point to the pole axis (Classic RH_OFF_MAX -3145728) */
#endif
#ifndef HQ_HD_EXT
#define HQ_HD_EXT 1                            /* 0: the glide stops at Classic's limit, gain capped as Classic (x4): the
                                                * exact pickup alone (tests/py/test_rhodes_hq.py parity, +-LSB) */
#endif
#if HQ_HD_EXT
#define HQ_HD_RCAP 16.0f                       /* the glide's fundamental gain at most x16 (+24 dB; Classic x4) */
#else
#define HQ_HD_RCAP 4.0f
#endif
#define HQ_HD_V0 40u                           /* the glide past Classic's limit in full up to this velocity, */
#define HQ_HD_V1 64u                           /* none from this one (a smoothstep between) */

static volatile uint8_t hq_mode = HQ_DEFAULT;  /* main loop: 1 = new notes play HQ */
typedef struct {
    uint8_t on, os;                            /* latched at the note-on: HQ, and the 2x pickup */
    uint8_t ex;                                /* the exact 1x pickup (HQ, keys under HQ_OS_LO): hq_pickup1 */
    int32_t eo;                                /* the offset (v->off) b1, b2, c1, c2 were made for */
    float xl;                                  /* the last sample's tip (gaps): the glide re-bases pprev on it */
    float ir;                                  /* the table pulse's force x this: its force at the fundamental Classic's
                                                * (hq_ir_tab, applied in hq_pulse_gain) */
    int32_t ys;                                /* the glide past Classic's limit: its own lag, at the soft strike's rate
                                                * (Q25 gap, re the strike's offset; hq_hd_step) */
    float b1, b2, c1, c2;                      /* the offset +- R and 1 / (b^2 + 1): hq_dphi's constants */
    float pprev;                               /* the last decimated flux (the difference's), re the rest flux */
    float xb[HQ_HIS + SUB], eb[HQ_HIS + SUB], ob[HQ_HIS + SUB];   /* tip in; the flux's even / odd phases */
    float lg;                                  /* the live pulse's force re the table's (0: none), for hq_pulse_gain */
    float hs;                                  /* the hammer's share of the live contact's closing speed (hq_live) */
#ifdef RH_DRIVE
    float dr;                                  /* the pickup drive (S1a): the tine's physical swing re the modes' state
                                                * (the drive's excursion law), the last strike's (hq_live) */
    int32_t pa;                                /* this strike's physical force (RH_PAMP at vel, before HAMMER) */
    float drl;                                 /* dr before this strike (the ringing tine's), for hq_pulse_gain */
    uint32_t dq;                               /* the dynamics stage (rhodes.c rh_dyn, Q15) in the voice's gain law gq:
                                                * the strike that set gq (rh_drv_level), for hq_pulse_gain */
#endif
} hq_voice_t;
static hq_voice_t hq_v[RH_NV];
static uint32_t hq_live_n;                     /* live contacts this block */

static void hq_voice_init(rh_voice_t *v);
static void hq_pulse(rh_voice_t *v, uint32_t vel);
static int32_t hq_pickup(rh_voice_t *v, int32_t *mix, int32_t *side, int32_t Hp, int32_t gain);
static void hq_pickup1(rh_voice_t *v, int32_t *mix, int32_t *side, int32_t gain);
static int32_t hq_hd_step(rh_voice_t *v, int32_t Hp);
static void hq_resync(rh_voice_t *v);
static void hq_pulse_gain(rh_voice_t *v);
static inline void hq_block(void) { hq_live_n = 0; }
