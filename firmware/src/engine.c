/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes voice engines: the dispatcher. One engine runs at a time; the TYPE picks it.
 *
 *   TYPE 0..3  ST77 SUIT FELT DYNO   the Rhodes engine (rhodes.c, unchanged)
 *   TYPE 4     VIBES                 the bar engine (bars.c)
 *
 * The app calls only these entry points: eng_note_on / eng_note_off / eng_pedal_level / eng_all_off post events
 * into this file's ring (main loop -> ISR, single producer, single consumer); eng_set_type(TYPE); eng_quiet(),
 * eng_note_sounding(); the ISR calls eng_render(mono, n). eng_render drains the ring into the running engine: for
 * the Rhodes it re-posts each event into rhodes.c's own ring right before rh_render (in the same block, in order),
 * so the Rhodes engine sees exactly what it saw when the main loop posted to it directly: its output is unchanged.
 * rhodes.c only ever gets TYPE 0..3 in rh_type. params.c (param_apply) calls eng_set_type, eng_is_bars, eng_bar_knob.
 *
 * TYPE change across engines: the running engine keeps playing for one more block with its output faded to zero over
 * those 256 samples, then all its voices stop and the other engine takes over; events wait in the ring during that
 * block (they are played by the new engine one block later). With no voice sounding the change is immediate.
 * A change between two bar types (VIBES <-> MARIMBA) goes the same way (MARIMBA PLAN §3.12, C1): the main loop asks
 * for a bar type in eng_want_sub, the ISR latches it into bar_sub only in eng_switch, after the fade and bar_kill_all,
 * so a voice never re-strikes with another type's tables and bar_sub is constant while any bar voice lives. A session
 * that never leaves VIBES never takes this path. */
#include "rhodes.c"
#include "bars.h"     /* the bar engine's state; its code (bars.c) comes last, in frodhes.c */

#define ENG_HAS_BARS 1                         /* params.c: the engine hooks below replace its stubs */
#define ENG_RHODES 0u
#define ENG_BARS 1u
#define ENG_RH_NTYPE 4u                        /* TYPE 0..3 the Rhodes; 4.. the bar types (4 = VIBES), as params.c
                                                * TYPE_ENGINE / TYPE_SUB */

static volatile uint8_t eng_want;              /* main loop: the engine of the TYPE last set */
static volatile uint8_t eng_want_sub;          /* main loop: its bar type (bar_sub follows it in eng_switch) */
static volatile uint8_t eng_cur;               /* ISR: the engine running */
static uint8_t eng_swapping;                   /* ISR: this block fades the old engine out */
static uint8_t eng_shed_req;                   /* ISR (frodhes.c app_audio): shed a voice of the running engine */
static uint8_t eng_pedal_cc;                   /* ISR: the last pedal level (handed to the new engine at a change) */

/* the ring has a section of its own (firmware only) so the compiler's global merge leaves it out: merged in, it pushed
 * rhodes.c's rh_F and rh_x into two merged blocks, and the Rhodes mode loop needed a second base register and spilled
 * (about 35 us per block with 8 voices, 2026-10-17; tools/build.py checks that loop) */
#ifdef DINGHY_HOST
#define ENG_OWN_SECTION
#else
#define ENG_OWN_SECTION __attribute__((section(".bss.eng_eq")))
#endif
static volatile uint32_t eq[32] ENG_OWN_SECTION;
static volatile uint32_t eq_w, eq_r, eq_drop;

static void eng_post(uint32_t type, uint32_t note, uint32_t val)
{
    uint32_t w = eq_w;
    if (w - eq_r >= 32u) { eq_drop++; return; }
    eq[w & 31u] = type << 28 | (note & 127u) << 16 | (val & 0xFFFFu);
    RING_PUBLISH();
    eq_w = w + 1u;
}

/* ---- main loop */
static void eng_note_on(uint32_t note, uint32_t vel) { if (note < 128u && vel && vel < 128u) eng_post(EV_NOTE_ON, note, vel); }
static void eng_note_off(uint32_t note) { if (note < 128u) eng_post(EV_NOTE_OFF, note, 0); }
static void eng_pedal_level(uint32_t cc) { rh_hold = (uint8_t)(cc >= 64u); eng_post(EV_PEDAL, 0, cc > 127u ? 127u : cc); }
static void eng_all_off(void) { eng_post(EV_ALL_OFF, 0, 0); }

/* params.c param_apply(P_TYPE): rhodes.c only ever gets 0..3 in rh_type (a bar TYPE leaves it at the last Rhodes
 * one, so a Rhodes note still fading out at the change keeps its own voicing) */
static uint16_t eng_epoch;                     /* main loop: counts the TYPE changes that stop the sounding voices (to
                                                * another engine, or VIBES <-> MARIMBA); notes.c re-sounds a pitch
                                                * still held across one when it is pressed again (out_resound) */
static void eng_set_type(uint32_t t)
{
    uint8_t w = eng_want, s = eng_want_sub;
    if (t < ENG_RH_NTYPE) { rh_type = (uint8_t)t; eng_want = ENG_RHODES; }
    else { t -= ENG_RH_NTYPE; eng_want_sub = (uint8_t)(t < BAR_NTYPE ? t : 0u); eng_want = ENG_BARS; }
    if (eng_want != w || (eng_want == ENG_BARS && eng_want_sub != s)) eng_epoch++;
}
static int eng_is_bars(void) { return eng_want == ENG_BARS; }   /* the main loop's TYPE, not the ISR latch */

/* params.c: the VOICE / DECAY cards and the motor, for the bar engine (the Rhodes keeps its own volatiles) */
enum { BK_STRIKE, BK_MALLET, BK_TUBE, BK_WEIGHT, BK_RING, BK_DAMPER, BK_NOISE,      /* the VOICE / DECAY cards, 0..127 */
       BK_MOT_ON, BK_MOT_DEPTH, BK_MOT_SHAPE, BK_MOT_INC };                          /* the motor (INC: a Q32 step) */
static void eng_bar_knob(uint32_t k, uint32_t v)
{
    uint32_t c = v > 127u ? 127u : v;
    switch (k) {
    case BK_STRIKE: bar_pos = (int32_t)c; break;
    case BK_MALLET: bar_fam = KN_FAM[eng_want_sub][c]; break;   /* the type's map: shared detents, MARIMBA re-spaced */
    case BK_TUBE: bar_tube = KN_TUBE[eng_want_sub][c]; bar_bell = (int32_t)c; break;
    case BK_WEIGHT: bar_mass = KN_MASS[eng_want_sub][c]; bar_massi = KN_MASSI[eng_want_sub][c]; break;
    case BK_RING: bar_k_decay = (int32_t)DECAY_K[c]; bar_ring = (int32_t)c; break;
    case BK_DAMPER: bar_k_rel = (int32_t)DECAY_K[c]; bar_mute = (int32_t)c * 258; break;   /* MUTE: no-damper types */
    case BK_NOISE:                  /* params.c sets rh_thump / rh_felt first. The Rhodes levels did not fit the bars
                                     * (audit 2026-10-17): the contact noise came out 75 dB under the note (exactly 0
                                     * at the default), so it is 48 dB up (NOISE 127: about -25 dB re the note, the
                                     * default -50); the damper's knock struck the bar as hard as a v100 RUBBER mallet
                                     * (louder than the strike at 127, louder than the note it damps), so it is 30 dB
                                     * down and a felt (SOFT) pulse: the default -31 .. -40 dB re the strike */
        bar_nz = (int32_t)KN_NZ[eng_want_sub][c] << 8; bar_thump = rh_thump >> 5; bar_felt = rh_felt; break;
    case BK_MOT_ON: bar_mot_on = (uint8_t)(v != 0); break;
    case BK_MOT_DEPTH: bar_mot_depth = (uint8_t)c; break;
    case BK_MOT_SHAPE: bar_mot_shape = (uint8_t)c; break;
    case BK_MOT_INC: bar_mot_inc = v; break;
    default: break;
    }
}

static int eng_quiet(void)
{
    if (eq_w != eq_r || eng_swapping || eng_want != eng_cur || (eng_want == ENG_BARS && eng_want_sub != bar_sub)) return 0;
    return eng_cur == ENG_BARS ? bar_quiet() : rh_quiet();
}


static int eng_note_sounding(uint32_t n)
{
    uint32_t v;
    if (eng_cur == ENG_BARS) {
        uint32_t act = bar_active_bits;
        for (v = 0; v < BAR_NV; v++) if (act >> v & 1u && bar_notes[v] == n) return 1;
        return 0;
    }
    {
        uint32_t act = rh_active_bits;
        for (v = 0; v < RH_NV; v++) if (act >> v & 1u && rh_notes[v] == n) return 1;
    }
    return 0;
}

static uint32_t eng_shed_count(void) { return rh_shed_count + bar_shed_count; }

/* ---- ISR */
static void eng_rh_kill_all(void)               /* the Rhodes stops at once (after the fade block) */
{
    uint32_t i, sb;
    for (i = 0; i < RH_NV; i++) { rv[i].alive = 0; rv[i].pend = 0; }
    rlc_m1 = rlc_m2 = rlc_o1 = rlc_o2 = rlc_e = 0;
    for (sb = 0; sb < 2u; sb++) bd_m[sb][0] = bd_m[sb][1] = bd_o[sb][0] = bd_o[sb][1] = bd_e[sb] = 0;
    rh_active_bits = rh_held_bits = 0;
}

static void eng_switch(uint32_t want)
{
    if (eng_cur == ENG_BARS) bar_kill_all(); else eng_rh_kill_all();
    eng_cur = (uint8_t)want;
    bar_sub = eng_want_sub;                                         /* the bar type: latched only here (C1) */
    if (want == ENG_BARS) bar_pedal(eng_pedal_cc);                  /* the pedal is global */
    else rh_post(EV_PEDAL, 0, eng_pedal_cc);
    eng_swapping = 0;
}

#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
static uint64_t eng_h_ms, eng_h_mul, eng_h_blocks;
static uint32_t eng_h_max_ms, eng_h_max_mul, eng_h_max_us[2];   /* us: per engine (ENG_RHODES, ENG_BARS) */
static FILE *eng_h_log;
static int eng_h_log_open;
#endif

static void eng_render(int32_t *mono, uint32_t n)
{
    uint32_t want = eng_want == ENG_BARS ? ENG_BARS : ENG_RHODES, j;
    if ((want != eng_cur || (want == ENG_BARS && eng_want_sub != bar_sub)) && !eng_swapping) {   /* another engine,
                                                                    * or another bar type (C1) */
        uint32_t busy = eng_cur == ENG_BARS ? bar_active_bits : rh_active_bits;
        if (busy) eng_swapping = 1;                                 /* fade the old engine out this block */
        else eng_switch(want);                                      /* nothing sounding: change now */
    }
    if (!eng_swapping)                                              /* events wait while the old engine fades */
        while (eq_r != eq_w) {
            uint32_t e = eq[eq_r & 31u], type = e >> 28, note = e >> 16 & 127u, val = e & 0xFFFFu;
            eq_r = eq_r + 1u;
            if (type == EV_PEDAL) eng_pedal_cc = (uint8_t)val;
            if (eng_cur == ENG_RHODES) rh_post(type, note, val);    /* drained by rh_render below, in order */
            else if (type == EV_NOTE_ON) bar_strike(note, val);
            else if (type == EV_NOTE_OFF) bar_note_off(note);
            else if (type == EV_PEDAL) bar_pedal(val);
            else if (type == EV_ALL_OFF) bar_all_off();
        }
    if (eng_shed_req) {
        eng_shed_req = 0;
        if (eng_cur == ENG_RHODES) rh_shed_req = 1; else bar_shed();
    }
    if (eng_cur == ENG_BARS) bar_render(mono, n); else rh_render(mono, n);
#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
    {   /* host counters: mode-samples and int64 multiplies this block (Rhodes: estimated from the live modes) */
        uint32_t ms = 0, mul = 0, ns = 0, i, m;
        if (eng_cur == ENG_BARS) { ms = bar_h_blk_ms; mul = bar_h_blk_mul; ns = bar_h_blk_ns; bar_h_blk_ms = bar_h_blk_mul = bar_h_blk_ns = 0; }
        else {
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive) {
                    for (m = 0; m < RH_NM; m++) if (rv[i].m[m].live) { ms += n; mul += 3u * n; ns += 170u * n; }
                    mul += 5u * n; ns += 870u * n;                  /* pickup: square, 3 Hermite, gain; P0: 0.54 + 0.33 us */
                }
        }
        eng_h_ms += ms; eng_h_mul += mul; eng_h_blocks++;
        if (ms > eng_h_max_ms) eng_h_max_ms = ms;
        if (mul > eng_h_max_mul) eng_h_max_mul = mul;
        if (ns / 1000u > eng_h_max_us[eng_cur & 1u]) eng_h_max_us[eng_cur & 1u] = ns / 1000u;
        if (!eng_h_log_open) {
            const char *p = getenv("FRODHES_MSLOG");
            eng_h_log = p ? fopen(p, "w") : 0; eng_h_log_open = 1;
            bar_h_nobudget = getenv("FRODHES_NOBUDGET") != 0; bar_h_nosleep = getenv("FRODHES_NOSLEEP") != 0;
            p = getenv("FRODHES_FXUS");
            if (p) bar_h_fxus = atoi(p);
            p = getenv("FRODHES_PREDLOG");
            bar_h_pred = p ? fopen(p, "w") : 0;
        }
        if (eng_h_log) fprintf(eng_h_log, "%u %u %u %u\n", (unsigned)eng_cur, ms, mul, ns / 1000u);
    }
#endif
    if (eng_swapping) {                                             /* the fade: 32768 -> 0 over the block */
        for (j = 0; j < n; j++) {
            int32_t g = 32768 - (int32_t)(((j + 1u) << 15) / n);
            mono[j] = (int32_t)(((int64_t)mono[j] * g) >> 15);
        }
        eng_switch(want);
    }
}

#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
static void eng_host_report(void)                /* tests/host.c, at the end of a run */
{
    printf("sat28 hits %llu\n", (unsigned long long)bar_h_sat);
    printf("mode-samples %llu (max per block %u, blocks %llu)\n", (unsigned long long)eng_h_ms, eng_h_max_ms,
           (unsigned long long)eng_h_blocks);
    printf("mul64 %llu (max per block %u)\n", (unsigned long long)eng_h_mul, eng_h_max_mul);
    printf("cpu-proxy max %u us per block (%s)\n", eng_h_max_us[eng_cur & 1u], eng_cur == ENG_BARS ?
           "bars: the cost model's whole block, effects included" : "voices only, P0 costs; + 312 idle, + 1388 with every effect");
    printf("cpu-proxy-each rhodes %u bars %u\n", eng_h_max_us[ENG_RHODES], eng_h_max_us[ENG_BARS]);   /* a run on both */
    printf("ev_drop %u\n", (unsigned)(eq_drop + ev_drop));
    printf("type %u\n", eng_cur == ENG_BARS ? ENG_RH_NTYPE + bar_sub : (unsigned)rh_type);   /* the TYPE last rendered (C11) */
    if (eng_h_log) fclose(eng_h_log);
    if (bar_h_pred) fclose(bar_h_pred);
}
#endif
