/* SPDX-License-Identifier: GPL-3.0-only
 * SMK-37 port: Copyright (C) 2026 M. Geisler (genkigenki/makkuro) */
/* Frodhes on the M-Vave SMK-37 (BOARD_SMK37; SMK37.md). The FM-1's panel keeps its jobs (the middle 27 keys, the 14
 * buttons by printed name, the knobs: platform.c, ui.c); this file adds what the SMK has more. Included by frodhes.c;
 * main loop only.
 *
 *   keys        all 37, C3..C6 at OCTAVE 0 (keybed 5..31 are the FM-1's F3..G5; C3-E3 and G#5-C6 go on beyond
 *               them), through the scale mode and chord mode as the FM-1's keys. The hammer is struck at the key's
 *               own velocity (the unit's factory curve), through VELOCITY (the main page's K4): NORMAL, SOFT,
 *               HARD or FIXED (at KEY VEL, 96)
 *   pads        top row: 1-4 the TYPE (STAGE 77, SUITCASE, EARLY FELT, BELL DYNO), 5 / 6 the previous / next preset,
 *               7 DRIVE on / off, 8 CHORD mode on / off. Bottom row: 9 COMP, 10 TREM/WAH, 11 PHASER, 12 CHORUS,
 *               13 DELAY, 14 REVERB on / off (DRIVE, REVERB: the amount, remembered), 15 ARP on / off, 16 SUSTAIN
 *               (held: down; a tap latches, as REC). Lights: off dark orange, on light blue, active (sounding) bright
 *   faders      two banks, FADER BANK switches (lit on B). A: TONE, BARK, PHASER, COMP (its THRESH: 0 = off). B: DRIVE,
 *               CHORUS, DELAY, REVERB (the amounts; up from 0 switches the effect on, as PERFORM). A fader acts once
 *               it moves (also after a bank change), so nothing jumps; the footer says the value
 *   wheels      pitch: +-2 semitones (every sounding tine retuned); mod: the TREM/WAH depth (switched on as TREM)
 *   pedal jack  a sustain pedal, either polarity: its reading at power-on is "up" (plug it in before switching on)
 *   < >         the previous / next page;  KNOB BANK held: the panel check */
#include "smk_wheel.h"

static const uint8_t SMK_OFF[3] = {110, 38, 0}, SMK_ON[3] = {40, 120, 210}, SMK_ACT[3] = {190, 230, 255};
enum { SP_PREV = 4, SP_NEXT, SP_DRIVE, SP_CHORD, SP_COMP, SP_TREM, SP_PHA, SP_CHO, SP_DLY, SP_REV, SP_ARP, SP_SUS };
static const uint8_t SMK_FP[2][4] = {{P_TONE, P_BARK, P_PH_DEPTH, P_CMP_AMT}, {P_DRIVE, P_CP_DEPTH, P_DL_DEPTH, P_RV_DEPTH}};
static const char *const SMK_FNAME[2][4] = {{"TONE", "BARK", "PHASER", "COMP THRESH"}, {"DRIVE", "CHORUS", "DELAY", "REVERB"}};

static uint32_t smk_bank;                       /* FADER BANK: 0 = A (the piano), 1 = B (the effects) */
static uint8_t smk_fad_last[4] = {0xFF, 0xFF, 0xFF, 0xFF};
static uint8_t smk_mem_drive = 32, smk_mem_rev = 32, smk_mem_chord = 1;   /* what DRIVE, REVERB, CHORD come back as */
static uint32_t smk_pad_t[16];                  /* when each pad last switched (a press counts once) */
static uint8_t smk_sus_pad, smk_sus_latch, smk_sus_was, smk_pedal;
static uint32_t smk_sus_t;
static int32_t smk_bend16, smk_modw = -1;       /* (the panel check and the host tests show them) */

static void smk_init(void)
{
    smk_pad_events = 1;                         /* the pads are Frodhes' own (events, no notes) */
    if (set.vel_curve >= SMK_VC_N) set.vel_curve = 0;
}

/* the keybed's velocity (1..127, the unit's factory curve) -> the hammer's, through VELOCITY */
static uint32_t smk_vel(uint32_t v)
{
    v = v < 1u ? 1u : v > 127u ? 127u : v;
    switch (set.vel_curve) {
    case 1: v = v * (254u - v) / 127u; break;   /* SOFT: a light touch plays louder (32 -> 56, 64 -> 96) */
    case 2: v = v * v / 127u; break;            /* HARD: it takes more (64 -> 32, 100 -> 78) */
    case 3: v = set.key_vel; break;             /* FIXED: KEY VEL, as the FM-1 */
    default: break;
    }
    return v < 1u ? 1u : v > 127u ? 127u : v;
}

static void smk_sus_update(void)
{
    uint8_t on = (uint8_t)(smk_sus_pad || smk_sus_latch || smk_pedal);
    if (on != smk_sus) { smk_sus = on; sustain_update(); }
}

/* a parameter from a fader: as a knob would set it (an amount up from 0 switches its effect on, COMP's THRESH is its
 * switch), and the footer says it */
static void smk_param(uint32_t i, uint32_t v)
{
    uint32_t id = SMK_FP[smk_bank][i], n = 0;
    char s[32], t[8];
    int32_t hi = PARAMS[id].hi;
    param_set(id, (int32_t)((v * (uint32_t)hi + 63u) / 127u));
    if (id == P_CMP_AMT) {
        if (PV(P_CMP_AMT) && !PV(P_CMP_SW) && !PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, 15);   /* level-neutral (ui.c) */
        param_set(P_CMP_SW, PV(P_CMP_AMT) != 0);
    } else
        perf_wake(id);
    p_strcpy(s, SMK_FNAME[smk_bank][i], 12);
    while (s[n]) n++;
    s[n++] = ' ';
    if (id == P_CMP_AMT && !PV(P_CMP_SW)) p_strcpy(t, "OFF", 3); else param_text(id, t);
    p_strcpy(s + n, t, 8);
    ui_say(s);
    ui_mark(E_CARDS);
    ui_mark(E_HEADER);
}

/* ---- the outer keys (C3-E3, G#5-C6): the keys' path, with velocity */
static void smk_outer(void)
{
    int32_t e;
    while ((e = smk_extkey_take()) >= 0) {
        uint32_t kk = ((uint32_t)e >> 8) % FM1_NKEY, vel = (uint32_t)e & 0x7Fu, slot;
        if (kk >= SMK_KEY_F3 && kk < SMK_KEY_F3 + 27u) continue;
        slot = SRC_EXT + (kk < SMK_KEY_F3 ? kk : kk - 27u);    /* keybed 0..4, 32..36 -> 0..9 */
        if (vel) {
            uint32_t n = scale_key_note((uint32_t)((int32_t)kk - (int32_t)SMK_KEY_F3));
            if (n < 128u) src_on(slot, n, smk_vel(vel), 1);
        } else
            src_off(slot);
    }
}

/* an effect's switch from its pad: as its button does on its page (ui_fx_button), without going there */
static void smk_fx_switch(uint32_t k)
{
    static const uint8_t SW[5] = {P_TW_SW, P_CP_SW, P_DL_SW, P_PH_SW, P_CMP_SW};
    static const char *const NAME[5] = {"TREM/WAH", "CHORUS", "DELAY", "PHASER", "COMP"};
    uint32_t id = SW[k], sw = PV(id), n = 0;
    char s[24];
    if (sw) { if (k < 3u) set.last_on[k] = (uint8_t)sw; param_set(id, 0); }
    else param_set(id, k < 3u && set.last_on[k] <= PARAMS[id].hi ? set.last_on[k] : 1);
    fx_wake(id);
    p_strcpy(s, NAME[k], 12);
    while (s[n]) n++;
    p_strcpy(s + n, PV(id) ? " ON" : " OFF", 4);
    ui_say(s);
}

/* DRIVE, REVERB: the amount on / off (remembered while off) */
static void smk_amount_switch(uint32_t id, uint8_t *mem, const char *name)
{
    char s[24];
    uint32_t n = 0;
    if (PV(id)) { *mem = PV(id); param_set(id, 0); }
    else param_set(id, *mem ? *mem : 32);
    p_strcpy(s, name, 12);
    while (s[n]) n++;
    p_strcpy(s + n, PV(id) ? " ON" : " OFF", 4);
    ui_say(s);
}

/* ---- the pads */
static void smk_pads(void)
{
    int32_t e;
    while ((e = smk_pad_take()) >= 0) {
        uint32_t p = ((uint32_t)e >> 8) & 15u, vel = (uint32_t)e & 0xFFu;
        if (p == SP_SUS) {                      /* SUSTAIN: held, or a tap latches (as REC) */
            if (vel && !smk_sus_pad) { smk_sus_pad = 1; smk_sus_t = fm1_ms; smk_sus_was = smk_sus_latch; }
            else if (!vel && smk_sus_pad) {
                smk_sus_pad = 0;
                smk_sus_latch = fm1_ms - smk_sus_t < SUS_TAP_MS ? !smk_sus_was : 0;
            }
            smk_sus_update();
            continue;
        }
        if (!vel || fm1_ms - smk_pad_t[p] < 180u) continue;   /* (a press counts once; let go does nothing) */
        smk_pad_t[p] = fm1_ms;
        if (pr_save_mode) continue;             /* (the save mode owns the panel) */
        if (p < 4u) {                           /* the TYPE */
            param_set(P_TYPE, (int32_t)p);
            ui_say(N_TYPE_LONG[p]);
        } else if (p == SP_PREV || p == SP_NEXT) {
            presets_poll(p == SP_NEXT ? 1 : -1, 0, 0, 0);   /* (as a PRESETS detent: it loads 400 ms later) */
        } else if (p == SP_DRIVE) smk_amount_switch(P_DRIVE, &smk_mem_drive, "DRIVE");
        else if (p == SP_REV) smk_amount_switch(P_RV_DEPTH, &smk_mem_rev, "REVERB");
        else if (p == SP_CHORD) {
            if (CH_TYPE) { smk_mem_chord = CH_TYPE; CH_TYPE = 0; }
            else CH_TYPE = smk_mem_chord && smk_mem_chord < CH_NTYPE ? smk_mem_chord : 1;
            ui_say(CH_TYPE ? "CHORD ON" : "CHORD OFF");
            ui_changed();
        } else if (p == SP_ARP) {
            arp_toggle();
            ui_say(arp_on() ? "ARP ON" : "ARP OFF");
            ui_changed();
        } else
            smk_fx_switch(p == SP_COMP ? 4u : p == SP_TREM ? 0u : p == SP_PHA ? 3u : p == SP_CHO ? 1u : 2u);
        ui_mark(E_CARDS);
        ui_mark(E_HEADER);
    }
}

/* ---- FADER BANK, < > (with the other buttons) */
static void smk_buttons(uint32_t b)
{
    if (b & (1u << BTN_FADER_BANK)) {
        uint32_t i;
        smk_bank ^= 1u;
        for (i = 0; i < 4u; i++) smk_fad_last[i] = 0xFF;   /* a new bank: each fader waits until it moves */
        ui_say(smk_bank ? "FADERS B: EFFECTS" : "FADERS A: PIANO");
    }
    if (b & (1u << BTN_LEFT) && ui_page > 0u) ui_goto(ui_page - 1u);
    if (b & (1u << BTN_RIGHT) && ui_page + 1u < PG_N) ui_goto(ui_page + 1u);
}

/* ---- the faders: two banks, a fader acts once it moves */
static void smk_faders(void)
{
    uint32_t i;
    for (i = 0; i < 4u; i++) {
        uint32_t v = fm1_in.fader[i];
        if (smk_fad_last[i] == 0xFF || fm1_in.frames < 200u) {   /* (the first readings settle) */
            smk_fad_last[i] = (uint8_t)v;
            continue;
        }
        if (v == smk_fad_last[i]) continue;
        smk_fad_last[i] = (uint8_t)v;
        smk_param(i, v);
    }
}

/* ---- the wheels: pitch bend (the tines retuned, rhodes.c rh_retune), the TREM/WAH depth */
static const int32_t SMK_BEND_T[17] = {   /* Q30: 2^(k/48) - 1, k = -8..8 (quarter semitones, +-2 semitones) */
    -117146609, -103232612, -89116230, -74794521, -60264498, -45523131, -30567345, -15394023, 0,
    15617934, 31463037, 47538612, 63848011, 80394637, 97181938, 114213416, 131492623};
static int32_t smk_bend_q30(int32_t b16)        /* 1/4096 semitone (+-8192) -> Q30 ratio - 1 */
{
    int32_t q = b16 + 8192, i = q >> 10, f = q & 1023;
    if (q <= 0) return SMK_BEND_T[0];
    if (i >= 16) return SMK_BEND_T[16];
    return SMK_BEND_T[i] + (int32_t)(((int64_t)(SMK_BEND_T[i + 1] - SMK_BEND_T[i]) * f) >> 10);
}
static void smk_wheels(void)
{
    enum { DEAD = 12, MOD_LO = 485, MOD_HI = 615 };
    static smk_pw_t pw;
    static int32_t mlo, mhi;
    int32_t m = fm1_in.wheel[1], mw, t, b;
    if (fm1_in.frames < 300u)                   /* (the readings settle) */
        return;
    if (!mhi) {
        mlo = m < MOD_LO ? m : MOD_LO;
        mhi = m > MOD_HI ? m : MOD_HI;
    }
    if (m < mlo) mlo = m;
    if (m > mhi) mhi = m;
    mw = mhi - m <= DEAD ? 0 : (mhi - m - DEAD) * 127 / (mhi - mlo - DEAD > 1 ? mhi - mlo - DEAD : 1);   /* up = lower raw */
    mw = mw < 0 ? 0 : mw > 127 ? 127 : mw;
    if (mw != smk_modw) {                       /* the mod wheel: TREM/WAH depth, once it moves */
        if (smk_modw >= 0) {
            param_set(P_TW_DEPTH, mw);
            perf_wake(P_TW_DEPTH);
            ui_mark(E_CARDS);
        }
        smk_modw = mw;
    }
    smk_bend16 = smk_pw_bend(&pw, fm1_in.wheel[0], fm1_ms);
    b = smk_bend_q30(smk_bend16);               /* TUNE and the bend: (1 + t)(1 + b) - 1 */
    t = TUNE_T[set.tune + 50];
    t = t + b + (int32_t)(((int64_t)t * b) >> 30);
    if (t != rh_tune_t) rh_tune_t = t;
}

/* ---- the pedal jack: a sustain pedal of either polarity (no plug detect: the reading at power-on is "up") */
static void smk_pedal_poll(void)
{
    static int32_t rest = -1;
    int32_t d, p = fm1_in.pedal;
    if (fm1_in.frames < 300u) return;
    if (rest < 0) { rest = p; return; }
    d = p > rest ? p - rest : rest - p;
    if (!smk_pedal && d > 300) { smk_pedal = 1; smk_sus_update(); }
    else if (smk_pedal && d < 200) { smk_pedal = 0; smk_sus_update(); }
}

/* ---- the pads' and the extra buttons' lights */
static void smk_pad_rgb(uint32_t p, const uint8_t *c) { padled_set(p, c[0], c[1], c[2]); }
static void smk_lit(uint32_t p, int on, int act) { smk_pad_rgb(p, !on ? SMK_OFF : act ? SMK_ACT : SMK_ON); }
static void smk_leds(void)
{
    uint32_t p, snd = rh_held_bits != 0u || (rh_hold && rh_active_bits), fx = snd && !fx_bypass;   /* (playing: keys
                                                * held, or notes the sustain holds) */
    for (p = 0; p < 4u; p++) smk_lit(p, PV(P_TYPE) == p, (int)snd);
    smk_lit(SP_PREV, smk_pad_t[SP_PREV] && fm1_ms - smk_pad_t[SP_PREV] < 200u, 1);   /* (flash when pressed) */
    smk_lit(SP_NEXT, smk_pad_t[SP_NEXT] && fm1_ms - smk_pad_t[SP_NEXT] < 200u, 1);
    smk_lit(SP_DRIVE, PV(P_DRIVE) != 0, (int)fx);
    smk_lit(SP_CHORD, chord_on(), (int)snd);
    smk_lit(SP_COMP, PV(P_CMP_SW) != 0, (int)fx);
    smk_lit(SP_TREM, PV(P_TW_SW) != 0, (int)fx);
    smk_lit(SP_PHA, PV(P_PH_SW) != 0, (int)fx);
    smk_lit(SP_CHO, PV(P_CP_SW) != 0, (int)fx);
    smk_lit(SP_DLY, PV(P_DL_SW) != 0, (int)fx);
    smk_lit(SP_REV, PV(P_RV_DEPTH) != 0, (int)fx);
    smk_lit(SP_ARP, arp_on(), arp_playing < 128u);
    smk_lit(SP_SUS, rh_hold, (int)snd);
    fm1_led_key(BTN_FADER_BANK, smk_bank != 0u);
    fm1_led_key(BTN_KNOB_BANK, fm1_in.buttons >> BTN_KNOB_BANK & 1u);   /* (lit while the panel check is up) */
    fm1_led_key(BTN_LEFT, ui_page > 0u);
    fm1_led_key(BTN_RIGHT, ui_page + 1u < PG_N);
    padled_flush();                             /* (only when a colour changed; between LCD transfers) */
}

/* ---- the main loop's SMK part, after the middle keys */
static void smk_poll(void)
{
    smk_outer();
    smk_pads();
    smk_faders();
    smk_wheels();
    smk_pedal_poll();
}

/* ---- KNOB BANK held: the panel check (the bring-up screen) instead of the pages; 1 = it owns the screen */
static char *smk_put(char *p, const char *s) { while (*s) *p++ = *s++; *p = 0; return p; }
static char *smk_int(char *p, int32_t v) { p_fmt_int(p, v); while (*p) p++; return p; }
static void smk_hex(char *s, uint32_t v, uint32_t n)
{
    uint32_t k;
    for (k = 0; k < n; k++) s[k] = "0123456789ABCDEF"[(v >> (4u * (n - 1u - k))) & 15u];
    s[n] = 0;
}
static int smk_check(void)
{
    static uint32_t on, t;
    static char ln[12][32];
    uint32_t i, k;
    char *p;
    if (!(fm1_in.buttons >> BTN_KNOB_BANK & 1u)) {
        if (on) { on = 0; lcd_fill(0, 0, 240, 240, C_BLACK); ui_draw_page_change(); }
        return 0;
    }
    if (on && fm1_ms - t < 100u) return 1;
    on = 1;
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
    for (k = 0; k < FM1_NKEY && !(fm1_in.keys >> k & 1u); k++) ;
    if (k < FM1_NKEY) { p = smk_int(smk_put(p, " KEY "), (int32_t)k); smk_int(smk_put(p, " V"), fm1_in.key_vel[k]); }
    p = smk_int(smk_put(ln[6], "BAT "), fm1_in.batt);
    if (smk_store_cleared) smk_put(p, "  STORE CLEARED");
    smk_put(ln[7], "PADS: READING - BASELINE");
    for (i = 0; i < 4u; i++) {                  /* pads 1-4, 5-8 (top row), 9-12, 13-16 */
        p = smk_put(smk_int(smk_put(smk_int(ln[8 + i], (int32_t)(4u * i + 1u)), "-"), (int32_t)(4u * i + 4u)), ":");
        for (k = 4u * i; k < 4u * i + 4u; k++)
            p = smk_int(smk_put(p, " "), (int32_t)fm1_in.pad_raw[k] - (int32_t)fm1_in.pad_base[k]);
    }
    for (k = 0; k < 4u; k++) {                  /* (4 strips of 60 rows: a canvas holds CV_MAX pixels) */
        cv_begin(240, 60, C_BLACK);
        for (i = 0; i < 3u; i++) cv_text(4, (int32_t)i * 19 + 4, &FONT_S, ln[3u * k + i], k == 0u && i == 0u ? UI_ACC : C_HI);
        cv_blit(0, 60u * k);
    }
    return 1;
}

#ifdef DINGHY_HOST
/* the host tests (tests/host.c): "dump" prints the pads and the state; "expect val NAME V" reads one of these */
static int host_val(const char *name, int32_t *v)
{
    static const struct { const char *n; uint8_t id; } P[] = {
        {"type", P_TYPE}, {"tone", P_TONE}, {"bark", P_BARK}, {"drive", P_DRIVE}, {"tw_sw", P_TW_SW},
        {"tw_depth", P_TW_DEPTH}, {"ph_sw", P_PH_SW}, {"ph_depth", P_PH_DEPTH}, {"cp_sw", P_CP_SW},
        {"cp_depth", P_CP_DEPTH}, {"dl_sw", P_DL_SW}, {"dl_depth", P_DL_DEPTH}, {"rv_depth", P_RV_DEPTH},
        {"cmp_sw", P_CMP_SW}, {"cmp_amt", P_CMP_AMT},
    };
    uint32_t i;
    for (i = 0; i < sizeof P / sizeof P[0]; i++)
        if (!strcmp(name, P[i].n)) { *v = PV(P[i].id); return 1; }
    if (!strcmp(name, "bank")) *v = (int32_t)smk_bank;
    else if (!strcmp(name, "page")) *v = ui_page;
    else if (!strcmp(name, "preset")) *v = set.preset;
    else if (!strcmp(name, "sustain")) *v = rh_hold;
    else if (!strcmp(name, "chord")) *v = CH_TYPE;
    else if (!strcmp(name, "arp")) *v = arp_on();
    else if (!strcmp(name, "vcurve")) *v = set.vel_curve;
    else if (!strcmp(name, "bend")) *v = smk_bend16;
    else if (!strcmp(name, "tune_t")) *v = rh_tune_t;
    else if (!strcmp(name, "voices")) *v = (int32_t)rh_active_bits;
    else if (!strcmp(name, "master")) *v = smk_master;
    else if (!strcmp(name, "note_vel")) {       /* the velocity the last voice was struck with */
        uint32_t k, best = 0;
        *v = -1;
        for (k = 0; k < RH_NV; k++) if (rv[k].alive && rv[k].age >= best) { best = rv[k].age; *v = rv[k].vel; }
    } else if (!strncmp(name, "pad", 3)) {      /* pad1..pad16: 0 off, 1 on, 2 active */
        uint32_t p = (uint32_t)atoi(name + 3) - 1u, c = p < 16u ? padled_rgb[p] : 0u;
        *v = c == ((uint32_t)SMK_ACT[1] << 16 | (uint32_t)SMK_ACT[0] << 8 | SMK_ACT[2]) ? 2 :
             c == ((uint32_t)SMK_ON[1] << 16 | (uint32_t)SMK_ON[0] << 8 | SMK_ON[2]) ? 1 : 0;
    } else return 0;
    return 1;
}
static void host_dump(void)
{
    uint32_t p;
    printf("%6u ms  PADS", fm1_ms);
    for (p = 0; p < 16u; p++) printf(" %06X", padled_rgb[p]);
    printf("\n%6u ms  STATE bank=%u page=%u type=%d preset=%u tone=%d bark=%d drive=%d tw=%d/%d ph=%d/%d cp=%d/%d"
           " dl=%d/%d rv=%d cmp=%d/%d chord=%u arp=%d sus=%u vcurve=%u bend=%d master=%d\n", fm1_ms, smk_bank, ui_page,
           PV(P_TYPE), set.preset, PV(P_TONE), PV(P_BARK), PV(P_DRIVE), PV(P_TW_SW), PV(P_TW_DEPTH), PV(P_PH_SW),
           PV(P_PH_DEPTH), PV(P_CP_SW), PV(P_CP_DEPTH), PV(P_DL_SW), PV(P_DL_DEPTH), PV(P_RV_DEPTH), PV(P_CMP_SW),
           PV(P_CMP_AMT), CH_TYPE, arp_on(), rh_hold, set.vel_curve, smk_bend16, smk_master);
}
#endif
