/* SPDX-License-Identifier: GPL-3.0-only
 * SMK-37 Pro port: Copyright (C) 2026 M. Geisler (genkigenki/makkuro)
 * (from genkigenki/sloop-fm1 smk37, as OberVave carries it) */
/* The SMK-37 Pro's pitch wheel as a bend (dinghy.c: smk_pw_bend on fm1_in.wheel[0]):
 * raw ADC reading -> 1/4096 semitone, +-SMK_PW_ST semitones. Self-calibrating: the rest (centre) is the first
 * reading, the range grows to what the wheel has reached, a dead band of SMK_PW_DEAD around the centre.
 * While the wheel rests in the dead band the centre follows a slow drift, at most one step per SMK_PW_DRIFT_MS
 * (in time, not in calls: the main loop calls this thousands of times a second, and a centre that moves that
 * fast is dragged along by a slow touch of the wheel; the spring then brings the wheel back to its real rest,
 * outside the dragged dead band, and a bend stays on whatever track is selected). No hardware here: the host
 * tests (tests/smk_wheel_test.c) run it.
 * 0.9.6 (#29: "the pitch wheel stops after the pedal"): a reading only counts once it holds. The centre is taken when
 * the reading has stayed within SMK_PW_STILL counts for SMK_PW_HOLD_MS, and the range grows only to what the wheel
 * held beyond it for SMK_PW_HOLD_MS (the least of it over that time). A spike on the analog inputs (a jack going in,
 * contact bounce) no longer sets a wrong centre at power-on (a bend that stays on) or widens the range for good (a
 * full travel that bends only a little). Beyond the range the bend is full anyway, so the hold costs nothing. */
#pragma once
#include <stdint.h>

#define SMK_PW_ST 2               /* +- semitones at the end of the travel */
#define SMK_PW_DEAD 12            /* raw counts around the centre: no bend */
#define SMK_PW_SPAN0 80           /* the range assumed until the wheel goes further */
#define SMK_PW_DRIFT_MS 250u      /* the centre follows a resting wheel by one count per this */
#define SMK_PW_HOLD_MS 50u        /* a new centre or a wider range: held this long */
#define SMK_PW_STILL 2            /* the centre: the reading within +- this many counts for SMK_PW_HOLD_MS */
#define SMK_PW_WAIT_MS 1000u      /* ... or, still not, this long after the first reading */

typedef struct {
    int32_t pc, plo, phi;         /* centre, lowest and highest reading seen (pc 0: not calibrated yet) */
    uint32_t drift_ms;            /* when the centre last moved (or the wheel left the dead band) */
    int32_t c0, xlo, xhi;         /* the centre's candidate; the readings held below plo / above phi (-1: none) */
    uint32_t c0_ms, xlo_ms, xhi_ms;   /* since when */
    uint32_t start_ms;            /* the first reading */
} smk_pw_t;

static int32_t smk_pw_bend(smk_pw_t *w, int32_t p, uint32_t now_ms)
{
    int32_t d, b, up, dn;
    if (!w->pc) {                                        /* the centre: once the reading holds still (a noisy
                                                         * input that never does: after SMK_PW_WAIT_MS, as it is) */
        if (!w->c0) w->start_ms = now_ms;
        if (!w->c0 || p - w->c0 > SMK_PW_STILL || w->c0 - p > SMK_PW_STILL) {
            w->c0 = p ? p : 1;
            w->c0_ms = now_ms;
        }
        if (now_ms - w->c0_ms < SMK_PW_HOLD_MS && now_ms - w->start_ms < SMK_PW_WAIT_MS)
            return 0;
        w->pc = w->c0;
        w->plo = w->pc - SMK_PW_SPAN0;
        w->phi = w->pc + SMK_PW_SPAN0;
        w->drift_ms = now_ms;
        w->xlo = w->xhi = -1;
    }
    if (p < w->plo) {                                    /* beyond the range: wider once held */
        if (w->xlo < 0) w->xlo = p, w->xlo_ms = now_ms;
        else if (p > w->xlo) w->xlo = p;                 /* (the least of it) */
        if (now_ms - w->xlo_ms >= SMK_PW_HOLD_MS) w->plo = w->xlo, w->xlo = -1;
    } else
        w->xlo = -1;
    if (p > w->phi) {
        if (w->xhi < 0) w->xhi = p, w->xhi_ms = now_ms;
        else if (p < w->xhi) w->xhi = p;
        if (now_ms - w->xhi_ms >= SMK_PW_HOLD_MS) w->phi = w->xhi, w->xhi = -1;
    } else
        w->xhi = -1;
    d = p - w->pc;
    if (d > -SMK_PW_DEAD && d < SMK_PW_DEAD) {
        if (now_ms - w->drift_ms >= SMK_PW_DRIFT_MS) {   /* resting: the centre follows a slow drift */
            w->pc += d > 0 ? 1 : d < 0 ? -1 : 0;
            w->drift_ms = now_ms;
        }
        return 0;
    }
    w->drift_ms = now_ms;                                /* (bent: the drift waits until it rests again) */
    up = w->phi - w->pc - SMK_PW_DEAD;
    dn = w->pc - w->plo - SMK_PW_DEAD;
    b = d > 0 ? (d - SMK_PW_DEAD) * (SMK_PW_ST * 4096) / (up > 1 ? up : 1)
              : (d + SMK_PW_DEAD) * (SMK_PW_ST * 4096) / (dn > 1 ? dn : 1);
    return b < -SMK_PW_ST * 4096 ? -SMK_PW_ST * 4096 : b > SMK_PW_ST * 4096 ? SMK_PW_ST * 4096 : b;
}
