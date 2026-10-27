/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ Rhodes (the Move only: FR_HQ; private plan 06): its state and entry points, included by rhodes.c after the
 * voices (rv[]). The code is hq/rhodes_hq.c, included at the end of rhodes.c. Never in the FM-1 / SMK builds
 * (tools/build.py float_check). The HQ voice is the Classic voice (rhodes.c: its modes, damper, release, noises and
 * calibration, the same integers) with two parts replaced, each latched per voice at its note-on (a note keeps the
 * path it started with: a switch never changes a sounding note):
 *   the hammer pulse   the exact contact at the note's own velocity (firmware/gen/hq/rh_hq_pulse.h), not the blend of
 *                      the two velocity layers around it; equal to Classic at the 8 layer velocities
 *   the pickup         on keys >= HQ_OS_LO, the flux at 2x (polyphase half-band up and down) against its aliasing
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

static volatile uint8_t hq_mode = HQ_DEFAULT;  /* main loop: 1 = new notes play HQ */
typedef struct {
    uint8_t on, os;                            /* latched at the note-on: HQ, and the 2x pickup */
    int32_t h0;                                /* the rest flux (Phi~ at the offset): the filters run on H - h0 */
    float pprev;                               /* the last decimated flux (the difference's) */
    float xb[HQ_HIS + SUB], eb[HQ_HIS + SUB], ob[HQ_HIS + SUB];   /* tip in; the flux's even / odd phases */
} hq_voice_t;
static hq_voice_t hq_v[RH_NV];

static void hq_voice_init(rh_voice_t *v);
static void hq_pulse(rh_voice_t *v, uint32_t vel);
static int32_t hq_pickup(rh_voice_t *v, int32_t *mix, int32_t *side, int32_t Hp);
