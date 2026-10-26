/* SPDX-License-Identifier: GPL-3.0-only
 * SMK-37 port: Copyright (C) 2026 M. Geisler (genkigenki/makkuro) */
/* Frodhes on the M-Vave SMK-37 (BOARD_SMK37; docs/SMK37.md): the app layer of the SMK build, after genkigenki's
 * frodhes_smk.h of PR #3 (the pads, the fader banks, the wheels, the pedal, the panel check are his design), rewritten
 * for today's Frodhes (six TYPEs and two engines, the engine-neutral sustain, the DRIVE switch, the filter, the
 * vibraphone's motor, DISPLAY). frodhes.c includes it before app_main, on the SMK only: the FM-1 build never sees this
 * file. Main loop only: nothing here runs in the audio ISR.
 *
 * The FM-1's panel keeps its jobs (platform.c maps the 14 buttons by printed name, ui.c the knobs: K1 MASTER, K2
 * SELECT, K3 ALGORITHM, K4 PRESETS, K5..K8 the cards). What the SMK has more:
 *   keys        all 37, C3..C6 at OCTAVE 0: keybed 5..31 are the FM-1's F3..G5 (fm1_in.notes), the outer ten (C3..E3,
 *               G#5..C6) are the sources SRC_EXT 156..165 (notes.c); through SCALE and chord mode as the FM-1's keys.
 *               Each key strikes at its own velocity (the unit's factory curve, smk_input.h) through the touch curve
 *               (smk_vel): KEY VEL is a medium press, as on the FM-1 with key velocity
 *   pads        top row 1..6 the TYPE (ST77 SUIT FELT DYNO VIBE MRMB), 7 / 8 the previous / next preset; bottom row
 *               9 DRIVE, 10 COMP, 11 FILTER, 12 TREM/WAH (VIBES: its MOTOR), 13 PHASER, 14 CHORUS, 15 DELAY on / off,
 *               16 SUSTAIN (held: down; a tap latches, as REC). Lights: off dark orange, on light blue, bright while
 *               sounding; at DISPLAY BRIGHTNESS
 *   faders      FADER BANK switches (its light: bank B). A: the engine's first VOICE page (Rhodes TONE BARK BELL
 *               HAMMER; VIBES / MARIMBA STRIKE MALLET TUBE WEIGHT). B: the PERFORM knobs' four (ASSIGN sets them).
 *               Soft takeover: a fader takes over once it reaches the value (after a bank change, a preset, a knob or
 *               MIDI moved it), the footer says where to go until then
 *   wheels      pitch: the bend, +-2 semitones (engine.c eng_bend_set, as MIDI pitch bend: the Rhodes); mod: the
 *               TREM/WAH depth (VIBES: the motor's depth), switching the effect on as a PERFORM knob does
 *   pedal jack  a sustain pedal, either polarity: its reading at power-on is "up" (plug it in before switching on)
 *   < >         the previous / next page (as SELECT); KNOB BANK held: the panel check (the readings, for bring-up)
 *   lights      KNOB BANK while held, FADER BANK on bank B, < > where there is a page that way; DISPLAY BRIGHTNESS
 *               dims the button LEDs (smk_led_dark) and the pads */
#pragma once
#if !BOARD_SMK37
#error "frodhes_smk.h is the SMK-37's (BOARD_SMK37)"
#endif
#include "smk_wheel.h"

/* ---- the touch curve. The SMK's keybed gives a velocity (1..127, the unit's factory curve); as the FM-1's key
 * velocity (keyvel.c kv_curve) it is a strength s = (v - 1) / 126, shaped u = s ^ gamma by TOUCH (SETUP K4: LIGHT
 * 0.45, MEDIUM 0.70, HEAVY 1.10; kv_upow) and mapped so that a medium press (u = KV_UMID) plays at KEY VEL: 1 at
 * u = 0, KEY VEL at the middle, 127 at u = 1, linear on each side (kv_vmap). FIXED: every key at KEY VEL. One curve
 * for both boards: keyvel.c's two halves (tests/py/test_smk_app.py checks it against the formula) */
static uint32_t smk_vel(uint32_t v)            /* the keybed's velocity -> the note's */
{
    uint32_t t = kv_touch(), kvel = set.key_vel;
    if (kvel < 1u) kvel = 1u;
    if (kvel > 127u) kvel = 127u;
    if (t >= KV_FIXED) return kvel;
    if (v < 1u) v = 1u;
    if (v > 127u) v = 127u;
    return kv_vmap(kv_upow(((v - 1u) * 32768u + 63u) / 126u, t), kvel);
}
#define FR_KEY_VEL(k) smk_vel(fm1_in.key_vel[SMK_KEY_F3 + (k)])   /* a middle key (keybed 5..31) */

/* ---- state */
enum { SP_PREV = 6, SP_NEXT, SP_DRIVE, SP_COMP, SP_FILTER, SP_TREM, SP_PHA, SP_CHO, SP_DLY, SP_SUS };   /* pads 1..6 TYPE */
static const uint8_t SMK_OFF[3] = {110, 38, 0}, SMK_ON[3] = {40, 120, 210}, SMK_ACT[3] = {190, 230, 255};   /* R G B */
static uint8_t smk_bank;                       /* FADER BANK: 0 = A (the voice), 1 = B (the PERFORM knobs) */
static struct {
    uint8_t slot, own, last, side, val;        /* the slot it drives; it has taken over; its last reading; the side of
                                                * the value it was on (1 below, 2 above, 0 not known); the value it set */
} smk_fad[4];
static uint32_t smk_pad_t[16];                 /* when each pad last acted (a press counts once) */
static uint8_t smk_sus_pad, smk_sus_was, smk_pedal;   /* (the latch: frodhes.c sus_latched, shared with REC) */
static uint32_t smk_sus_t;
static uint8_t smk_mem_cmp = 64, smk_mem_flt = 2;   /* COMP's THRESH, the FILTER's type, to come back on with */
static int32_t smk_bend16, smk_modw = -1;      /* the wheels: the bend (1/4096 semitone), the mod wheel 0..127 */
static uint8_t smk_panel_on;
#define SMK_SETTLE 300u                        /* scan frames (~0.5 s) before the analog readings count */

/* K1's MASTER across power cycles (GitHub #14): in the settings record (set.smk_mst, the value + 1; 0 = none, every
 * record before: smk_input.h's 724). Restored by smk_init, before the main loop's first pass reads K1; kept in `set`
 * once a main-loop pass when it moved, so the settings' own auto save writes it (2 s after the last change, in
 * silence: never from the ISR, never a write per detent) */
static int32_t smk_master_stored(void)
{
    uint32_t v = set.smk_mst[0] | (uint32_t)set.smk_mst[1] << 8;
    return v >= 1u && v <= 1024u ? (int32_t)v - 1 : 724;
}
static void smk_master_keep(void)
{
    if (smk_master != smk_master_stored()) {
        uint32_t v = (uint32_t)smk_master + 1u;
        set.smk_mst[0] = (uint8_t)v; set.smk_mst[1] = (uint8_t)(v >> 8);
    }
}
static void smk_init(void)
{
    uint32_t i;
    smk_pad_events = 1;                        /* the pads are Frodhes' own controls (events, no notes) */
    for (i = 0; i < 4u; i++) smk_fad[i].slot = S_NONE;
    smk_master = smk_master_stored();          /* (MASTER as it was left) */
}

static void smk_sus_update(void)
{
    uint8_t on = (uint8_t)(smk_sus_pad || smk_pedal);
    if (on != smk_sus) { smk_sus = on; sustain_update(); }
}

/* ---- the outer keys (C3..E3 = keybed 0..4, G#5..C6 = 32..36): the keys' path, at their velocity */
static void smk_outer(void)
{
    int32_t e;
    while ((e = smk_extkey_take()) >= 0) {
        uint32_t kk = ((uint32_t)e >> 8) % FM1_NKEY, vel = (uint32_t)e & 0x7Fu, slot;
        if (kk >= SMK_KEY_F3 && kk < SMK_KEY_F3 + 27u) continue;   /* (the middle keys are fm1_in.notes) */
        slot = SRC_EXT + (kk < SMK_KEY_F3 ? kk : kk - 27u);        /* 0..4, 32..36 -> 156..165 */
        if (vel) {
            uint32_t n = scale_key_note((uint32_t)((int32_t)kk - (int32_t)SMK_KEY_F3));   /* (k -5..-1, 27..31) */
            if (n < 128u) src_on(slot, n, smk_vel(vel), 1);
        } else
            src_off(slot);
    }
}

/* ---- the pads */
static void smk_say_onoff(const char *name, int on)
{
    char s[24];
    uint32_t n = 0;
    p_strcpy(s, name, 12);
    while (s[n]) n++;
    p_strcpy(s + n, on ? " ON" : " OFF", 4);
    ui_say(s);
}
static void smk_fx_switch(uint32_t k)          /* TREM/WAH, CHORUS, DELAY, PHASER as their button on their page does */
{
    static const uint8_t SW[4] = {P_TW_SW, P_CP_SW, P_DL_SW, P_PH_SW};
    static const char *const NAME[4] = {"TREM/WAH", "CHORUS", "DELAY", "PHASER"};
    uint32_t id = SW[k], sw = PV(id);
    if (sw) { if (k < 3u) set.last_on[k] = (uint8_t)sw; param_set(id, 0); }
    else param_set(id, k < 3u && set.last_on[k] && set.last_on[k] <= PARAMS[id].hi ? set.last_on[k] : 1);
    fx_wake(id);
    smk_say_onoff(NAME[k], PV(id) != 0);
}
static void smk_pad_hit(uint32_t p)
{
    if (p < N_TYPE_N) {                        /* the TYPE, as ALGORITHM sets it */
        type_pick((int32_t)p);
        ui_say_big(N_TYPE_LONG[PV(P_TYPE)]);
    } else if (p == SP_PREV || p == SP_NEXT)
        presets_poll(p == SP_NEXT ? 1 : -1, 0, 0, 0);   /* (as a PRESETS detent: it loads 400 ms later) */
    else if (p == SP_DRIVE) {
        param_set(P_DRV_SW, !PV(P_DRV_SW));
        fx_wake(P_DRV_SW);
        smk_say_onoff("DRIVE", PV(P_DRV_SW) && PV(P_DRIVE));
    } else if (p == SP_COMP) {                 /* THRESH is the compressor's switch (0 = off): kept for the way back */
        if (PV(P_CMP_SW)) { if (PV(P_CMP_AMT)) smk_mem_cmp = (uint8_t)PV(P_CMP_AMT); param_set(P_CMP_AMT, 0); param_set(P_CMP_SW, 0); }
        else {
            param_set(P_CMP_SW, 1);
            param_set(P_CMP_AMT, smk_mem_cmp ? smk_mem_cmp : 64);
            if (!PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, eng_ui()->cmp_mkup);   /* level-neutral, as THRESH */
        }
        smk_say_onoff("COMP", PV(P_CMP_SW) != 0);
    } else if (p == SP_FILTER) {
        if (PV(P_FL_TYPE)) { smk_mem_flt = (uint8_t)PV(P_FL_TYPE); param_set(P_FL_TYPE, 0); }
        else param_set(P_FL_TYPE, smk_mem_flt ? smk_mem_flt : 2);
        smk_say_onoff("FILTER", PV(P_FL_TYPE) != 0);
    } else if (p == SP_TREM && TYPE_HAS_MOTOR(PV(P_TYPE))) {   /* VIBES: its motor */
        param_set(P_MOT_ON, !PV(P_MOT_ON));
        set.mot_user_off = !PV(P_MOT_ON);
        smk_say_onoff("MOTOR", PV(P_MOT_ON) != 0);
    } else if (p >= SP_TREM && p <= SP_DLY)
        smk_fx_switch(p == SP_TREM ? 0u : p == SP_PHA ? 3u : p == SP_CHO ? 1u : 2u);
    ui_mark(E_CARDS);
    ui_mark(E_HEADER);
}
static void smk_pads(void)
{
    int32_t e;
    while ((e = smk_pad_take()) >= 0) {
        uint32_t p = ((uint32_t)e >> 8) & 15u, vel = (uint32_t)e & 0xFFu;
        if (p == SP_SUS) {                     /* SUSTAIN: held, or a tap flips the latch; the latch is REC's own
                                                * (frodhes.c sus_latched: either one's tap flips what sounds, a hold
                                                * on either is momentary and its release clears it, CC 64 up too) */
            if (vel && !smk_sus_pad) { smk_sus_pad = 1; smk_sus_t = fm1_ms; smk_sus_was = sus_latched; }
            else if (!vel && smk_sus_pad) {
                smk_sus_pad = 0;
                sus_latched = fm1_ms - smk_sus_t < SUS_TAP_MS ? !smk_sus_was : 0;
            }
            smk_sus = (uint8_t)(smk_sus_pad || smk_pedal);
            sustain_update();
            continue;
        }
        if (!vel || (smk_pad_t[p] && fm1_ms - smk_pad_t[p] < 180u)) continue;   /* a press counts once */
        smk_pad_t[p] = fm1_ms;
        if (pr_save_mode || smk_panel_on) continue;   /* (the save mode owns the panel) */
        smk_pad_hit(p);
    }
}

/* ---- the faders: two banks, soft takeover */
static uint32_t smk_fad_slot(uint32_t i)
{
    const ectl_t *c;
    if (smk_bank) return slot_at_v(PG_PERF, 0, i);
    c = voice_ctl(0, i);
    return c ? c->id : S_NONE;
}
static const char *smk_fad_name(uint32_t i) { const ectl_t *c = voice_ctl(0, i); return smk_bank ? slot_label_v(PG_PERF, 0, i) : c ? c->name : ""; }
static void smk_slot_set(uint32_t s, int32_t t)   /* a card's slot to t, as its knob would turn it there */
{
    uint32_t n;
    for (n = 0; n < 128u; n++) {
        int32_t hi, v = slot_val(s, &hi), d = t - v;
        if (!d) break;
        if (s >= P_COUNT || PARAMS[s].fmt == F_ENUM || PARAMS[s].fmt == F_DIV || slot_synced(s)) d = sgn(d);
        slot_turn(s, d);
        if (slot_val(s, &hi) == v) break;      /* (no further: a synced or clamped one) */
    }
    if (s < P_COUNT) perf_wake(s);             /* an effect's amount up from 0 switches it on (PERFORM) */
}
static void smk_fad_say(uint32_t i, uint32_t s, const char *pre)
{
    char m[40], t[16];
    uint32_t n = 0;
    p_strcpy(m, smk_fad_name(i), 12);
    while (m[n]) n++;
    m[n++] = ' ';
    if (pre) { p_strcpy(m + n, pre, 8); while (m[n]) n++; }
    slot_text(s, t);
    p_strcpy(m + n, t, 15);
    ui_say(m);
}
static void smk_faders(void)
{
    uint32_t i;
    for (i = 0; i < 4u; i++) {
        uint32_t f = fm1_in.fader[i], s = smk_fad_slot(i);
        int32_t hi, cur, t, tol;
        if (s != smk_fad[i].slot) { smk_fad[i].slot = (uint8_t)s; smk_fad[i].own = 0; smk_fad[i].side = 0; }
        if (fm1_in.frames < SMK_SETTLE || s == S_NONE) { smk_fad[i].last = (uint8_t)f; continue; }
        cur = slot_val(s, &hi);
        if (hi < 1) hi = 1;
        t = (int32_t)((f * (uint32_t)hi + 63u) / 127u);
        if (smk_fad[i].own && cur != smk_fad[i].val) { smk_fad[i].own = 0; smk_fad[i].side = 0; }   /* moved elsewhere
                                                         * (a knob, MIDI, a preset): the fader catches up first */
        if (f == smk_fad[i].last) continue;
        smk_fad[i].last = (uint8_t)f;
        if (!smk_fad[i].own) {
            uint32_t side = t < cur ? 1u : t > cur ? 2u : 0u;
            tol = hi / 32;
            if ((t >= cur - tol && t <= cur + tol) || (smk_fad[i].side && side && side != smk_fad[i].side)) smk_fad[i].own = 1;
            else { smk_fad[i].side = (uint8_t)side; smk_fad_say(i, s, side == 1u ? "UP TO " : "DOWN TO "); continue; }
        }
        smk_slot_set(s, t);
        smk_fad[i].val = (uint8_t)slot_val(s, &hi);
        smk_fad_say(i, s, 0);
        ui_mark(E_CARDS);
        ui_mark(E_HEADER);
    }
}

/* ---- the wheels */
static void smk_wheels(void)
{
    enum { DEAD = 12, MOD_LO = 485, MOD_HI = 615 };
    static smk_pw_t pw;
    static int32_t acc = -1, used, mlo, mhi;
    int32_t raw = fm1_in.wheel[0], r, b, m = fm1_in.wheel[1], mw;
    if (fm1_in.frames < SMK_SETTLE) return;    /* (the readings settle) */
    /* the pitch wheel: an IIR (x16, 1/4) over the HAL's own, a hysteresis of one count (a count ~2.5 cents: a reading
     * that wobbles by one moves nothing), then smk_pw_bend (centre, range, dead band: smk_wheel.h) */
    if (acc < 0) { acc = raw << 4; used = raw; }
    acc += ((raw << 4) - acc) / 4;
    r = (acc + 8) >> 4;
    if (r > used + 1) used = r - 1;
    else if (r < used - 1) used = r + 1;
    b = smk_pw_bend(&pw, used, fm1_ms);
    if (b != smk_bend16) {
        smk_bend16 = b;
        eng_bend_set(8192 + (b > 8191 ? 8191 : b));   /* (the MIDI bend's scale: 8192 = 2 semitones) */
    }
    /* the mod wheel (up = a lower reading): more TREM/WAH depth, on VIBES more motor depth, once it moves: on top of
     * the DEPTH as set (params.c MW_ADD; 0.9.6, #29: it wrote DEPTH, its rest was DEPTH 0); moved up it switches the
     * effect on, back at rest off again if it did that (and nothing else switched it since) */
    if (!mhi) { mlo = m < MOD_LO ? m : MOD_LO; mhi = m > MOD_HI ? m : MOD_HI; }
    if (m < mlo) mlo = m;
    if (m > mhi) mhi = m;
    mw = mhi - m <= DEAD ? 0 : (mhi - m - DEAD) * 127 / (mhi - mlo - DEAD > 1 ? mhi - mlo - DEAD : 1);
    mw = mw < 0 ? 0 : mw > 127 ? 127 : mw;
    if (mw != smk_modw) {
        if (smk_modw >= 0) {
            smk_mw = mw;
            param_apply(P_TW_DEPTH);
            param_apply(P_MOT_DEPTH);
            if (mw && !smk_mw_woke) {
                if (TYPE_HAS_MOTOR(PV(P_TYPE))) {
                    if (!PV(P_MOT_ON)) { smk_mw_uoff = set.mot_user_off; param_set(P_MOT_ON, 1); set.mot_user_off = 0; smk_mw_woke = P_MOT_ON; }
                } else if (!PV(P_TW_SW)) { param_set(P_TW_SW, set.last_on[0]); smk_mw_woke = P_TW_SW; }
                smk_mw_wv = smk_mw_woke ? PV(smk_mw_woke) : 0;
            } else if (!mw && smk_mw_woke) {
                if (PV(smk_mw_woke) == smk_mw_wv) {      /* (still as the wheel left it) */
                    param_set(smk_mw_woke, 0);
                    if (smk_mw_woke == P_MOT_ON) set.mot_user_off = smk_mw_uoff;
                }
                smk_mw_woke = 0;
            }
            ui_mark(E_CARDS);
        }
        smk_modw = mw;
    }
}

/* ---- the pedal jack: a sustain pedal of either polarity (no plug detect: the reading at power-on is "up") */
static void smk_pedal_poll(void)
{
    static int32_t rest = -1;
    int32_t d, p = fm1_in.pedal;
    if (fm1_in.frames < SMK_SETTLE) return;
    if (rest < 0) { rest = p; return; }
    d = p > rest ? p - rest : rest - p;
    if (!smk_pedal && d > 300) { smk_pedal = 1; smk_sus_update(); }
    else if (smk_pedal && d < 200) { smk_pedal = 0; smk_sus_update(); }
}

/* ---- the main loop's SMK part, after the middle keys */
static void smk_poll(void)
{
    smk_outer();
    smk_pads();
    smk_faders();
    smk_wheels();
    smk_pedal_poll();
    smk_master_keep();
}

/* ---- the SMK's own buttons, before the FM-1's reach sustain_button / ui_input; their bits are cleared here */
static void smk_buttons(uint32_t *pressed, uint32_t *released)
{
    uint32_t b = *pressed, mine = 1u << BTN_KNOB_BANK | 1u << BTN_FADER_BANK | 1u << BTN_LEFT | 1u << BTN_RIGHT;
    if (b & mine && ui_splash_on) ui_splash_t = fm1_ms - UI_SPLASH_MS;   /* (any touch ends the start screen) */
    if (b >> BTN_FADER_BANK & 1u) {
        smk_bank ^= 1u;                        /* a new bank: every fader takes over again (smk_faders) */
        ui_say(smk_bank ? "FADERS B: PERFORM" : "FADERS A: VOICE");
    }
    if ((b >> BTN_LEFT & 1u || b >> BTN_RIGHT & 1u) && !pr_save_mode) {   /* < >: SELECT's step */
        int32_t p = clampi((int32_t)pg_pos(ui_page, ui_vp_ok()) + (b >> BTN_RIGHT & 1u ? 1 : -1), 0, (int32_t)pg_count() - 1);
        uint32_t vp, pg = pg_at((uint32_t)p, &vp);
        if (pg != ui_page || vp != ui_vp) ui_goto_v(pg, vp);
    }
    *pressed &= ~mine;
    *released &= ~mine;
}

/* ---- the lights: the pads, the SMK's own buttons (ui_leds sets the LEDs from smk_btn_leds) */
static int smk_sounding(void) { return (eng_cur == ENG_BARS ? bar_active_bits : rh_active_bits) != 0u; }
static void smk_pad_rgb(uint32_t p, int on, int act)
{
    const uint8_t *c = !on ? SMK_OFF : act ? SMK_ACT : SMK_ON;
    uint32_t d = fm1_led_duty[0], k;            /* DISPLAY BRIGHTNESS (the FM-1's keys' duty, /16) */
    uint8_t o[3];
    for (k = 0; k < 3u; k++) o[k] = (uint8_t)(c[k] ? (c[k] * d + 8u) / 16u ? (c[k] * d + 8u) / 16u : 1u : 0u);
    padled_set(p, o[0], o[1], o[2]);
}
static void smk_leds(void)
{
    uint32_t p, snd = (uint32_t)smk_sounding(), fx = snd && !fx_bypass, pos = pg_pos(ui_page, ui_vp_ok());
    for (p = 0; p < N_TYPE_N; p++) smk_pad_rgb(p, PV(P_TYPE) == p, (int)snd);
    smk_pad_rgb(SP_PREV, smk_pad_t[SP_PREV] && fm1_ms - smk_pad_t[SP_PREV] < 200u, 1);   /* (a flash when pressed) */
    smk_pad_rgb(SP_NEXT, smk_pad_t[SP_NEXT] && fm1_ms - smk_pad_t[SP_NEXT] < 200u, 1);
    smk_pad_rgb(SP_DRIVE, PV(P_DRV_SW) && PV(P_DRIVE), (int)fx);
    smk_pad_rgb(SP_COMP, PV(P_CMP_SW) != 0, (int)fx);
    smk_pad_rgb(SP_FILTER, PV(P_FL_TYPE) != 0, (int)fx);
    smk_pad_rgb(SP_TREM, TYPE_HAS_MOTOR(PV(P_TYPE)) ? PV(P_MOT_ON) != 0 : PV(P_TW_SW) != 0, (int)fx);
    smk_pad_rgb(SP_PHA, PV(P_PH_SW) != 0, (int)fx);
    smk_pad_rgb(SP_CHO, PV(P_CP_SW) != 0, (int)fx);
    smk_pad_rgb(SP_DLY, PV(P_DL_SW) != 0, (int)fx);
    smk_pad_rgb(SP_SUS, sus_held || sus_latched || smk_sus, (int)snd);
    smk_btn_leds = (uint32_t)(smk_bank != 0u) << BTN_FADER_BANK | (fm1_in.buttons >> BTN_KNOB_BANK & 1u) << BTN_KNOB_BANK |
                   (uint32_t)(pos > 0u) << BTN_LEFT | (uint32_t)(pos + 1u < pg_count()) << BTN_RIGHT;
    padled_flush();                            /* (only when a colour changed; between LCD transfers) */
}

/* ---- KNOB BANK held: the panel check (the bring-up screen) instead of the pages; 1 = it owns the screen this pass */
static char *smk_put(char *p, const char *s) { while (*s) *p++ = *s++; *p = 0; return p; }
static char *smk_int(char *p, int32_t v) { p_fmt_int(p, v); while (*p) p++; return p; }
static void smk_hex(char *s, uint32_t v, uint32_t n)
{
    uint32_t k;
    for (k = 0; k < n; k++) s[k] = "0123456789ABCDEF"[(v >> (4u * (n - 1u - k))) & 15u];
    s[n] = 0;
}
static int smk_panel(void)
{
    static uint32_t t;
    static char ln[12][32];
    uint32_t i, k;
    char *p;
    if (!(fm1_in.buttons >> BTN_KNOB_BANK & 1u)) {
        if (smk_panel_on) { smk_panel_on = 0; lcd_fill(0, 0, 240, 240, C_BLACK); ui_draw_page_change(); }
        return 0;
    }
    if (smk_panel_on && fm1_ms - t < 100u) return 1;
    smk_panel_on = 1;
    t = fm1_ms;
    for (i = 0; i < 12u; i++) ln[i][0] = 0;
    smk_put(ln[0], "PANEL CHECK");
    p = smk_put(ln[1], "FLASH "); smk_hex(p, smk_jedec, 6); smk_put(p + 6, smk_trim_ok ? " TRIM OK" : " TRIM DEFAULT");
    p = smk_int(smk_put(ln[2], "ADC "), (int32_t)(fm1_in.adc_ok % 100000u)); smk_int(smk_put(p, " MISS "), (int32_t)(fm1_in.adc_miss % 10000u));
    p = smk_put(ln[3], "FADER");
    for (k = 0; k < 4u; k++) p = smk_int(smk_put(p, " "), fm1_in.fader[k]);
    p = smk_int(smk_put(ln[4], "WHEEL "), fm1_in.wheel[0]); p = smk_int(smk_put(p, " "), fm1_in.wheel[1]);
    p = smk_int(smk_put(p, " PED "), fm1_in.pedal); smk_int(smk_put(p, smk_pedal ? " ON B " : " B "), smk_bend16 / 41);
    p = smk_put(ln[5], "BTN "); smk_hex(p, fm1_in.buttons, 5); p += 5;
    for (k = 0; k < FM1_NKEY && !(fm1_in.keys >> k & 1u); k++) {}
    if (k < FM1_NKEY) { p = smk_int(smk_put(p, " KEY "), (int32_t)k); smk_int(smk_put(p, " V"), fm1_in.key_vel[k]); }
    p = smk_int(smk_put(ln[6], "BAT "), fm1_in.batt);
    if (smk_store_cleared) smk_put(p, "  STORE CLEARED");
    smk_put(ln[7], "PADS: READING - BASELINE");
    for (i = 0; i < 4u; i++) {                 /* pads 1-4, 5-8 (top row), 9-12, 13-16 */
        p = smk_put(smk_int(smk_put(smk_int(ln[8 + i], (int32_t)(4u * i + 1u)), "-"), (int32_t)(4u * i + 4u)), ":");
        for (k = 4u * i; k < 4u * i + 4u; k++)
            p = smk_int(smk_put(p, " "), (int32_t)fm1_in.pad_raw[k] - (int32_t)fm1_in.pad_base[k]);
    }
    for (k = 0; k < 4u; k++) {                 /* (4 strips of 60 rows: a canvas holds CV_MAX pixels) */
        cv_begin(240, 60, C_BLACK);
        for (i = 0; i < 3u; i++) cv_text(4, (int32_t)i * 19 + 4, &FONT_S, ln[3u * k + i], k == 0u && i == 0u ? UI_ACC : C_HI);
        cv_blit(0, 60u * k);
    }
    return 1;
}

#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
/* the host tests (tests/host.c): "expect state" on the SMK's own state; a hint the FM-1's scripts expect, in the
 * SMK's words (ui_text) */
static const char *host_xlat(const char *s) { return ui_text(s); }
static int smk_host_state(const char *key, char *v, unsigned n)
{
    unsigned a;
    if (!strcmp(key, "fbank")) snprintf(v, n, "%c", smk_bank ? 'B' : 'A');
    else if (!strcmp(key, "twdepth")) snprintf(v, n, "%d", (int)fx_p[FXP_TW_DEPTH]);   /* the depth TREM/WAH runs at */
    else if (!strcmp(key, "motdepth")) snprintf(v, n, "%u", (unsigned)bar_mot_depth);  /* the motor's (engine.c) */
    else if (sscanf(key, "fader.%u", &a) == 1 && a < 4u) {   /* fader a: the card it drives, OWN or WAIT */
        char t[16];
        slot_text(smk_fad_slot(a), t);
        snprintf(v, n, "%s:%s:%s", smk_fad_name(a), t, smk_fad[a].own ? "OWN" : "WAIT");
    }
    else if (sscanf(key, "pad.%u", &a) == 1 && a < 16u) {     /* pad a: 0 off, 1 on, 2 active (sounding) */
        uint32_t c = padled_rgb[a], d = fm1_led_duty[0], k, on[3], act[3];
        for (k = 0; k < 3u; k++) { on[k] = (SMK_ON[k] * d + 8u) / 16u; act[k] = (SMK_ACT[k] * d + 8u) / 16u; }
        snprintf(v, n, "%u", c == (act[1] << 16 | act[0] << 8 | act[2]) ? 2u : c == (on[1] << 16 | on[0] << 8 | on[2]) ? 1u : 0u);
    }
    else if (sscanf(key, "padrgb.%u", &a) == 1 && a < 16u) snprintf(v, n, "%06X", padled_rgb[a]);   /* G R B */
    else if (!strcmp(key, "wbend")) snprintf(v, n, "%d", smk_bend16);   /* the wheel's bend, 1/4096 semitone */
    else if (!strcmp(key, "modw")) snprintf(v, n, "%d", smk_modw);
    else if (!strcmp(key, "smksus")) snprintf(v, n, "%u", smk_sus || sus_latched);   /* (the pad, the jack, the latch) */
    else if (!strcmp(key, "suslatch")) snprintf(v, n, "%u", sus_latched);
    else if (!strcmp(key, "pedal")) snprintf(v, n, "%u", smk_pedal);
    else if (!strcmp(key, "ext")) { uint32_t i, c = 0; for (i = 0; i < 10u; i++) c += src_active[SRC_EXT + i]; snprintf(v, n, "%u", c); }
    else if (!strcmp(key, "leddark")) snprintf(v, n, "%u", smk_led_dark);
    else if (!strcmp(key, "panel")) snprintf(v, n, "%u", smk_panel_on);
    else if (!strcmp(key, "btnleds")) snprintf(v, n, "%X", smk_btn_leds);
    else if (!strcmp(key, "master")) snprintf(v, n, "%d", smk_master);
    else if (!strcmp(key, "mastq")) snprintf(v, n, "%d", host_enc[ENC_MASTER]);   /* K1's detents left for MASTER */
    else if (sscanf(key, "vel.%u", &a) == 1 && a < 128u) snprintf(v, n, "%u", smk_vel(a));   /* the curve at a */
    else return 1;
    return 0;
}
#endif
