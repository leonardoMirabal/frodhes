/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes: an electric piano (Fender Rhodes physical model) for the M-VAVE FM-1. Built on Dinghy.
 *
 *   keys       play the piano at KEY VEL (SELECT), through the scale mode; the notes go out over USB-MIDI too
 *   panel      reface CP-style: TYPE DRIVE TREM/WAH CHO/PHA DLY REVERB VOLUME (ui.c), voicing pages, tempo, scales
 *   REC        the sustain pedal: hold it, or tap it to latch (tap again to let go)
 *   MASTER     the volume (platform.c)
 *   USB-MIDI   notes in (with velocity), the reface CP CC map, CC 64 sustain with half-pedal
 *
 * This file is the app: panel, MIDI, screen, settings. The sound is rhodes.c. Below both is platform.c
 * (Dinghy / SLOOP / Felucca): updates, the USB rescue, USB-MIDI, USB audio, keys, knobs, LEDs, screen, settings.
 * Rules of the road (no FPU, no libgcc): integers only, no float, no 64-bit division. docs/RULES.md.
 * tests/host.c runs this file on a computer: the screen as a PNG, the sound as a WAV. */
#define APP_NAME "FRODHES"
#define APP_OUT_SHIFT 8u   /* the codec at full scale: +6 dB on the jack over Dinghy's -6 dBFS ceiling (the look-ahead
                            * limiter keeps every sample under -2.7 dBFS, so it never clips; 0.8) */
#define APP_FL_RANGE(off, n) FL_IN(off, n, 0x97000u, 0x9AFFFu)    /* the user preset bank (presets.c), the
                                                                       * sequencer's pattern (seq.c) */
#include "platform.c"
#if defined(RH_PROF_STAGES) && !defined(DINGHY_HOST)
/* developer build only (APP_DEFS=-DRH_PROF_STAGES ./build.sh; never shipped): where the audio block's time goes.
 * PROF_STAMP(i) charges the 24 MHz ticks since the previous stamp to stage i (its maximum since the last CC 116
 * query); app_audio stamps 1 after the engine and 15 at the end, fx_process may stamp 2..14 between its stages.
 * tools/hil_cpu.py --stages reads them (CC 116 replies also CC 97 = stage, CC 98 / 99 = us MSB / LSB). */
#define PROF_NST 16u
static uint32_t prof_st_t __attribute__((section(".bss.prof_t")));     /* own sections: out of the global merge */
static uint32_t prof_st_max[PROF_NST] __attribute__((section(".bss.prof_st")));     /* (engine.c eq) */
#define PROF_STAMP(i) do { uint32_t t_ = fm1_ticks(), d_ = t_ - prof_st_t; prof_st_t = t_; \
                           if (d_ > prof_st_max[i]) prof_st_max[i] = d_; } while (0)
#else
#define PROF_STAMP(i) ((void)0)
#endif
#include "engine.c"     /* the voice engines: rhodes.c (the Rhodes), bars.c (VIBES), and the dispatcher */
#include "fx.c"
#include "params.c"
#include "tempo.c"
#include "arp.c"
#include "notes.c"
/* Flash writes and the USB audio: an erase holds the interrupts off (the settings: 24 ms) and no USB audio packet goes
 * out meanwhile. macOS then hands the recording stale input for ~250 ms (2026-10-18, tools/hil_usb_onset.py: a note
 * 0.2 s after a settings save lost its first 180-260 ms on USB while the jack had it at once; interrupts off for 20 ms
 * did the same, 5 ms did not). So while a computer reads the USB audio, the automatic saves (the settings, the
 * sequencer's pattern) wait until it stops, or for FL_REC_QUIET blocks of silence (so changes made during a long
 * session are kept even if the FM-1 is switched off before the computer lets go). SAVE (a preset) goes at once. */
#define FL_REC_QUIET 10336u                    /* 60 s of 5.8 ms blocks under -66 dBFS (fx_quiet_blocks) */
#if !defined(DINGHY_HOST)
static int usb_recording(void) { return uac.alt && uac.flowing; }
#elif defined(DINGHY_WEB)
static int usb_recording(void) { return 0; }
#else
static int usb_recording(void) { return host_usb_rec; }
#endif
static int flash_auto_ok(void) { return !usb_recording() || fx_quiet_blocks >= FL_REC_QUIET; }
#include "seq.c"
#include "ui.c"
#include "presets.c"
#include "bars.c"       /* last: the bar engine's code (see bars.c) */

static int32_t voice_mono[256];
static void app_audio(int32_t *out, uint32_t n)
{
#ifndef DINGHY_HOST
    uint32_t t0 = fm1_ticks(), us;
#endif
#if defined(RH_PROF_STAGES) && !defined(DINGHY_HOST)
    prof_st_t = t0;
#endif
    eng_render(voice_mono, n);
    PROF_STAMP(1);
    fx_process(voice_mono, out, n);
    PROF_STAMP(15);
#ifndef DINGHY_HOST
    us = (fm1_ticks() - t0) / 24u;                 /* 24 MHz ticks -> us (a constant divide) */
    prof_us = us;
    if (us > prof_max_us) prof_max_us = us;
    prof_over = us > 5000u ? prof_over + 1u : 0u;  /* two blocks over 5.0 ms of the 5.8 ms: shed a voice (8 voices
                                                    * struck with every effect on peak at 4.8 ms: tools/hil_cpu.py) */
    if (prof_over >= 2u) { eng_shed_req = 1; prof_over = 0; }
#endif
}


/* ---- the sustain pedal: REC held, or latched by a tap; MIDI CC 64 too. Either holds the dampers off. */
#define BTN_SUSTAIN BTN_REC
#define SUS_TAP_MS 250u                         /* a press shorter than this is a tap: it latches */
#define NKEYS 27u
static uint8_t sus_held, sus_latched, sus_midi, sus_was_latched;
static uint32_t sus_t;

static void sustain_update(void) { eng_q(EV_PEDAL, 0, sus_held || sus_latched ? 127u : sus_midi); ui_mark(E_HEADER); }

static void sustain_button(uint32_t pressed, uint32_t released)
{
    if (pressed >> BTN_SUSTAIN & 1u) {
        sus_held = 1; sus_t = fm1_ms; sus_was_latched = sus_latched;
    } else if (sus_held && (released >> BTN_SUSTAIN & 1u || !(fm1_in.buttons >> BTN_SUSTAIN & 1u))) {
        sus_held = 0;
        sus_latched = fm1_ms - sus_t < SUS_TAP_MS ? !sus_was_latched : 0;   /* a tap flips the latch, a hold clears it */
    } else
        return;
    sustain_update();
}

/* MIDI CC: every parameter's CC from the PARAMS table (the reface CP map for the panel: switch CCs 0-42 off,
 * 43-85 A, 86-127 B; TYPE in bands of 32), CC 64 sustain (continuous: half-pedal), 120/123 all off,
 * 119 developer: bypass the effects (tests of the voice engine); the other developer CCs below (2 3 4 8 9 10 11 116) */
static void midi_cc(uint32_t cc, uint32_t v)
{
    uint32_t id;
    if (cc == 64u) { sus_midi = (uint8_t)v; sustain_update(); return; }
    if (cc == 119u) { fx_set(FXP_RAW, v >= 64u); return; }
    if (cc == 9u) { bar_budget_force = (uint8_t)v; return; }   /* developer: the bar engine's mode cap (0 = auto) */
    if (cc == 10u) { if (v >= 64u) fx_reset_req = 1; return; }   /* developer: every LFO / line / noise to its boot state (fx.c) */
    if (cc == 3u) { ui_testpat = v >= 64u; return; }            /* developer: the colour test pattern (ui.c) */
    if (cc == 2u) { ui_look = (uint8_t)(v < MT_NLOOKS ? v : MT_NLOOKS - 1u); return; }   /* developer: the METERS
                                                        * colour candidate, 0 A 1 B 2 C (ui.c MT_LOOKS; not saved) */
    if (cc == 11u) { ui_cards = (uint8_t)(v < UI_CARDS_N ? v : UI_CARDS_N - 1u); return; }   /* developer: the rows'
                                                        * design variant, 0 TODAY 1 FADER 2 LINE (ui.c; not saved) */
    if (cc == 4u) {                                    /* developer: the battery query (ui.c, for calibrating its
                                                        * thresholds): CC 4 / 5 = the smoothed 10-bit reading (MSB = raw
                                                        * >> 3, LSB = raw & 7), CC 6 = the segments shown (4 = USB power) */
        int32_t r = batt_raw();
        midi_send(0xB0, 4, (uint32_t)r >> 3 & 127u); midi_send(0xB0, 5, (uint32_t)r & 7u); midi_send(0xB0, 6, batt_shown());
        return;
    }
    if (cc == 8u) { batt_sim = (uint8_t)v; return; }           /* developer: a simulated battery reading (v x 8; 0 = the ADC) */
    if (cc == 111u) { fx_bypass = v >= 64u; ui_mark(E_HEADER); return; }    /* the FX button's bypass (0.8) */
    if (cc == 114u) { set.usb_fixed = v >= 64u; usb_pre_master = set.usb_fixed; ui_mark(E_CARDS); return; }   /* USB VOL */
    if (cc == 116u) {                                  /* profiler query: CC 116 -> CC 116/117 = max audio us (MSB, LSB
                                                        * of us / 4), CC 118 = this block's (us / 32), CC 115 = voices shed
                                                        * (mod 128); then the max resets */
        uint32_t mx = prof_max_us >> 2;
        if (mx > 16383u) mx = 16383u;
        midi_send(0xB0, 116, mx >> 7); midi_send(0xB0, 117, mx & 127u);
        midi_send(0xB0, 118, prof_us >> 5 > 127u ? 127u : prof_us >> 5);
        midi_send(0xB0, 115, eng_shed_count() & 127u);
        midi_send(0xB0, 112, bar_prof_cap > 127u ? 127u : bar_prof_cap);   /* the bar engine: the lowest mode cap */
        midi_send(0xB0, 113, bar_prof_used > 127u ? 127u : bar_prof_used); /* and the most live upper modes */
        bar_prof_cap = 0xFFFFu; bar_prof_used = 0;
#if defined(RH_PROF_STAGES) && !defined(DINGHY_HOST)
        for (id = 1; id < PROF_NST; id++) {           /* the stage maxima (us), then they reset */
            uint32_t su = prof_st_max[id] / 24u;
            if (!prof_st_max[id]) continue;           /* a stage without a stamp (the MIDI out ring holds 64) */
            if (su > 16383u) su = 16383u;
            midi_send(0xB0, 97, id); midi_send(0xB0, 98, su >> 7); midi_send(0xB0, 99, su & 127u);
            prof_st_max[id] = 0;
        }
#endif
        prof_max_us = 0;
        return;
    }
    if (cc >= 52u && cc <= 55u) {                      /* the arpeggiator (0.9): 52 MODE 0..5 (0 off), 53 RATE 0..5,
                                                        * 54 OCTAVES 0..3 (= 1..4), 55 GATE 10..100 % */
        if (cc == 52u) { ARP_MODE = (uint8_t)(v > 5u ? 5u : v); if (ARP_MODE) ARP_LAST = ARP_MODE; else arp_clear(); }
        else if (cc == 53u) ARP_RATE = (uint8_t)(v > 5u ? 5u : v);
        else if (cc == 54u) ARP_OCT = (uint8_t)(v > 3u ? 3u : v);
        else ARP_GATE = (uint8_t)(v < 10u ? 10u : v > 100u ? 100u : v);
        ui_mark(E_CARDS); ui_mark(E_FOOTER);             /* DELAY's hint: SEL is HOLD while the arp is on */
        return;
    }
    if (cc >= 56u && cc <= 63u) {                      /* chord mode and the sequencer (0.9.1): 56 TYPE 0..14, 57
                                                        * INVERSION 0..3, 58 VOICING 0..2, 59 STRUM 0..12 (x 5 ms); 60 play
                                                        * (>= 64) / stop, 61 BARS 1..16, 62 QUANT 0..5, 63 recording */
        if (cc == 56u) { CH_TYPE = (uint8_t)(v < CH_NTYPE ? v : CH_NTYPE - 1u); if (CH_TYPE) CH_LAST = CH_TYPE; }
        else if (cc == 57u) CH_INV = (uint8_t)(v > 3u ? 3u : v);
        else if (cc == 58u) CH_VOICE = (uint8_t)(v > 2u ? 2u : v);
        else if (cc == 59u) CH_STRUM = (uint8_t)(v > 12u ? 12u : v);
        else if (cc == 60u) { if ((v >= 64u) != (seq_play != 0)) seq_toggle_play(); }
        else if (cc == 61u) { SQ_BARS = (uint8_t)(v < 1u ? 1u : v > 16u ? 16u : v); sq_roll_dirty = 1; }
        else if (cc == 62u) { SQ_QUANT = (uint8_t)(v > 5u ? 5u : v); sq_roll_dirty = 1; }
        else { if ((v >= 64u) != (seq_rec != 0)) seq_toggle_rec(); }
        ui_mark(E_CARDS); ui_mark(E_FOOTER);
        return;
    }
    if (cc >= 43u && cc <= 46u) {                      /* the sequencer's edits: 43 CLEAR (>= 64), 44 UNDO (>= 64),
                                                        * 45 ERASE (>= 64 on), 46 SWING 50..75 */
        if (cc == 43u && v >= 64u) seq_clear();
        else if (cc == 44u && v >= 64u) seq_undo();
        else if (cc == 45u) sq_erase = v >= 64u;
        else if (cc == 46u) { SQ_SWING = (uint8_t)(v < 50u ? 50u : v > 75u ? 75u : v); sq_roll_dirty = 1; }
        ui_mark(E_CARDS); ui_mark(E_FOOTER);
        return;
    }
    if (cc == 79u) {                                   /* the bar types (VIBES ...): bands over 0..127, one per type;
                                                        * CC 80 keeps the four Rhodes types exactly as before */
        type_pick((int32_t)(4u + ((v * (N_TYPE_N - 4u)) >> 7)));   /* (a TYPE change: the motor starts) */
        ui_mark_param(P_TYPE);
        return;
    }
    if (cc == 120u || cc == 123u) {                    /* all notes off: every MIDI note is let go as if its note-off
                                                        * came (0.9.3: a lost note-off left that note dead, and the
                                                        * arpeggiator playing it), then the dampers on everything */
        uint32_t n;
        out_quiet_off = 1;                             /* (one EV_ALL_OFF below damps them all) */
        for (n = 0; n < 128u; n++) src_off(SRC_MIDI + n);
        out_quiet_off = 0;
        eng_q(EV_ALL_OFF, 0, 0);
        return;
    }
    if (cc == 85u) {                                   /* reface CHO/PHA switch: off / chorus / phaser (one of them) */
        param_set(P_CP_SW, v >= 43u && v < 86u); param_set(P_PH_SW, v >= 86u);
        ui_mark_param(P_CP_SW);
        return;
    }
    if ((cc == 86u || cc == 87u) && PV(P_PH_SW) && !PV(P_CP_SW)) cc += 13u - 86u;   /* reface: the phaser's */
    for (id = 0; id < P_COUNT; id++)
        if (PARAMS[id].cc && PARAMS[id].cc == cc) {        /* cc 0: a parameter without a CC */
            int32_t x = (int32_t)v;
            if (id == P_TW_MODE) x = v >= 64u;          /* PAN 0..63, AM 64..127 (records v3: MOTOR, 112..127 on a bar
                                                        * TYPE, is the motor's own CC 47 now) */
            else if (PARAMS[id].hi == 2u) x = v < 43u ? 0 : v < 86u ? 1 : 2;
            else if (id == P_TYPE) x = (int32_t)(v >> 5);   /* the Rhodes types, as before; bar types: CC 79 */
            else if (PARAMS[id].hi < 127u) x = (int32_t)(v * (PARAMS[id].hi + 1u) >> 7);
            param_set(id, x);
            if (id == P_DL_SYNC || id == P_TW_SYNC || id == P_CP_SYNC || id == P_PH_SYNC || id == P_MOT_SYNC ||
                id == P_LFO_SYNC) tempo_sync_publish();   /* (LFO SYNC by CC 77 kept its free rate: the audit) */
            ui_mark_param(id);
            return;
        }
}

/* a note a voice is still sounding (for the per-key LEDs under sustain) */
static int note_sounding(uint32_t n)
{
    return eng_note_sounding(n);
}

static void app_main(void)
{
    uint32_t prev = 0, k;
    int fresh = settings_load(&set, sizeof set) != 0;
    if (!fresh && set.ver != 1u && set.ver != SET_VER) fresh = 1;   /* a record this firmware cannot read (another
                                                                     * version): as a first boot, so it plays F01 too */
    if (fresh) params_defaults();
    params_sanitize();
    arp_sanitize();
    chord_sanitize();
    seq_sanitize();
    if (set.usb_fixed > 1u) set.usb_fixed = 0;
    usb_pre_master = set.usb_fixed;
    uart_midi_on = !set.trs_off;
    palette_set(set.color);
    params_apply_all();
    tempo_set10(set.bpm10);
    set_dirty = 0;
    presets_load_bank();
    if (fresh) {                                /* a first boot (or a lost record) plays F01, as the header says */
        preset_load(0);
        set.undo_valid = 0;
        set_dirty = 0;
    }
    seq_load();
    ui_goto(PG_PERF);
    ui_splash();

    for (;;) {
        uint32_t keys, down, up, rel, b, lit = 0;
        uint8_t st, d1, d2;
        platform_poll();

        keys = fm1_in.notes;                    /* bit k: key k (0 = F3 .. 26 = G5) */
        down = keys & ~prev;
        up = prev & ~keys;
        prev = keys;
        for (k = 0; k < NKEYS; k++) {           /* a key stops exactly the notes it started (notes.c sources) */
            if (down >> k & 1u) {
                uint32_t n = scale_key_note(k);
                if (n < 128u) src_on(SRC_KEYS + k, n, set.key_vel, 1);
            }
            if (up >> k & 1u) src_off(SRC_KEYS + k);
        }

        b = fm1_input_edges(&rel);              /* buttons pressed / released since the last call */
        sustain_button(b, rel);
        ui_input(b, rel);

        while (midi_get(&st, &d1, &d2)) {       /* USB-MIDI and the TRS jack; the MIDI page's CHANNEL (0 = all) */
            uint8_t ty = st & 0xF0u;
            if (st < 0xF0u && set.midi_ch && (st & 0x0Fu) != set.midi_ch - 1u) continue;
            if (ty == 0x90u && d2) src_on(SRC_MIDI + (d1 & 127u), d1 & 127u, d2, 0);
            else if (ty == 0x80u || ty == 0x90u) src_off(SRC_MIDI + (d1 & 127u));
            else if (ty == 0xB0u) midi_cc(d1, d2);
            else if (st >= 0xF8u) {             /* clock and transport, from USB or TRS (CLOCK EXT) */
                tempo_clock(st);
                if (set.clock && (st == 0xFAu || st == 0xFBu) && !seq_play) seq_start();   /* Start / Continue */
                else if (set.clock && st == 0xFCu && seq_play) seq_stop();               /* Stop */
            }
            else if (ty == 0xC0u) preset_load(d1 < PR_NF ? d1 : d1 >= 16u && d1 < 32u ? PR_NF + d1 - 16u : 255u);
        }

        for (k = 0; k < NKEYS; k++)             /* LEDs per key: held, or its note still rings */
            if (src_active[SRC_KEYS + k]) lit |= 1u << k;
            else if (rh_hold && note_sounding((uint32_t)scale_key_note(k))) lit |= 1u << k;
        src_poll();
        eng_pump();                             /* (notes.c: events that waited for room in the engine's ring) */
        seq_poll();
        arp_poll();
        ui_leds(lit, tempo_beat_led() || (arp_on() && arp_n && arp_playing < 128u));
        ui_draw();
        presets_save_poll();
        seq_save_poll();

        if (set_dirty && fm1_ms - set_t_change > 2000u && eng_idle() && fx_quiet() && flash_auto_ok()) {
            settings_save(&set, sizeof set);    /* only when silent: the erase stalls the audio */
            set_dirty = 0;
        }
    }
}

#ifdef DINGHY_WEB
/* the emulator (web/app.js): which page is shown, so the buttons' captions can say what they do there */
EMSCRIPTEN_KEEPALIVE const char *web_page(void) { return PAGE_TITLE[ui_page]; }
#endif

#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
/* tests/host.c "expect preset": the panel equals what the preset the header names sets (presets.c preset_load) */
static int host_preset_check(char *msg, unsigned n)
{
    uint8_t p[P_COUNT];
    char nm[20];
    uint32_t i, idx = set.preset;
    if (idx >= PR_NF + PR_NU) { snprintf(msg, n, "preset %u is no preset", idx); return 1; }
    preset_name(idx, nm);
    if (idx >= PR_NF && !pr_user[idx - PR_NF].used) { snprintf(msg, n, "%s: the header names an empty user preset", nm); return 1; }
    for (i = 0; i < P_COUNT; i++) p[i] = PARAMS[i].def;
    if (idx < PR_NF)
        for (i = 0; i < 32u && PR_FDATA[idx][i] != 0xFF; i += 2u) p[PR_FDATA[idx][i]] = PR_FDATA[idx][i + 1u];
    else
        for (i = 0; i < P_SLOTS && i < P_COUNT && i < pr_user[idx - PR_NF].np; i++)    /* as preset_load reads it */
            p[i] = pr_user[idx - PR_NF].p[i] <= PARAMS[i].hi ? pr_user[idx - PR_NF].p[i] : PARAMS[i].def;
    for (i = 0; i < P_COUNT; i++)
        if (PV(i) != p[i]) { snprintf(msg, n, "%s: %s (%u) is %u, the preset sets %u", nm, PARAMS[i].lab, i, PV(i), p[i]); return 1; }
    snprintf(msg, n, "%s", nm);
    return 0;
}

/* "expect sound": what the ISR runs with (the effects' fx_p, the Rhodes and bar volatiles, the engine, the synced
 * rates) is what the panel says: a copy, every parameter applied again (param_apply is idempotent), compared */
typedef struct {
    int32_t fx[FXP_N];
    uint32_t sync[5];
    int32_t rh[11];
    int32_t bar[14];
    uint8_t want;
} host_snd_t;
static void host_snd(host_snd_t *s)
{
    uint32_t i;
    memset(s, 0, sizeof *s);
    for (i = 0; i < FXP_N; i++) s->fx[i] = i == FXP_RAW ? 0 : fx_p[i];    /* RAW: CC 119, not the panel */
    s->sync[0] = fx_dly_sync_samples; s->sync[1] = fx_tw_sync_inc; s->sync[2] = fx_cp_sync_inc;
    s->sync[3] = fx_ph_sync_inc; s->sync[4] = fx_lfo_sync_inc;
    s->rh[0] = rh_tone_off; s->rh[1] = rh_bark_s; s->rh[2] = rh_bark_g; s->rh[3] = rh_bell; s->rh[4] = rh_ham;
    s->rh[5] = rh_k_decay; s->rh[6] = rh_k_rel; s->rh[7] = rh_thump; s->rh[8] = rh_felt; s->rh[9] = rh_type;
    s->rh[10] = rh_tune_t;
    s->bar[0] = bar_pos; s->bar[1] = bar_fam; s->bar[2] = bar_tube; s->bar[3] = bar_mass; s->bar[4] = bar_massi;
    s->bar[5] = bar_nz; s->bar[6] = bar_k_decay; s->bar[7] = bar_k_rel; s->bar[8] = bar_thump; s->bar[9] = bar_felt;
    s->bar[10] = bar_mot_on; s->bar[11] = bar_mot_depth; s->bar[12] = bar_mot_shape; s->bar[13] = (int32_t)bar_mot_inc;
    s->want = eng_want;
    (void)bar_sub;
}
static int host_sound_check(char *msg, unsigned n)
{
    static const char *const FXN[FXP_N] = {"DRIVE", "TW_SW", "TW_DEPTH", "TW_RATE", "TW_MODE", "TW_SHAPE", "CP_SW",
        "CP_DEPTH", "CP_SPEED", "CP_FDBK", "DL_SW", "DL_DEPTH", "DL_TIME", "DL_FDBK", "DL_SYNC", "RV_DEPTH",
        "DRIVE_ROW", "RAW", "DL_TONE", "PH_SW", "PH_DEPTH", "PH_SPEED", "PH_FDBK", "CMP_SW", "CMP_AMT", "CMP_ATK",
        "CMP_REL", "LEVEL", "CMP_RATIO", "CMP_GAIN", "FL_TYPE", "FL_FREQ", "FL_RESO", "FL_LFO", "LFO_SHAPE", "LFO_RATE"};
    host_snd_t a, b;
    uint32_t i;
    host_snd(&a);
    params_apply_all();
    tempo_sync_publish();
    host_snd(&b);
    for (i = 0; i < FXP_N; i++)
        if (a.fx[i] != b.fx[i]) { snprintf(msg, n, "fx_p[FXP_%s] is %d, the panel says %d", FXN[i], a.fx[i], b.fx[i]); return 1; }
    for (i = 0; i < 5u; i++)
        if (a.sync[i] != b.sync[i]) { snprintf(msg, n, "synced rate %u is %u, the panel says %u", i, a.sync[i], b.sync[i]); return 1; }
    for (i = 0; i < 11u; i++)
        if (a.rh[i] != b.rh[i]) { snprintf(msg, n, "Rhodes volatile %u is %d, the panel says %d", i, a.rh[i], b.rh[i]); return 1; }
    for (i = 0; i < 14u; i++)
        if (a.bar[i] != b.bar[i]) { snprintf(msg, n, "bar knob %u is %d, the panel says %d", i, a.bar[i], b.bar[i]); return 1; }
    if (a.want != b.want) { snprintf(msg, n, "the engine is %u, TYPE says %u", a.want, b.want); return 1; }
    return 0;
}

/* "expect state" / "dump": what the screen, the LEDs and the state say, by name */
static uint32_t host_bits(uint32_t x) { uint32_t c = 0; while (x) { c += x & 1u; x >>= 1; } return c; }
static int host_state(const char *key, char *v, unsigned n)
{
    unsigned a = 0, b = 0;
    uint32_t i, c;
    char t[24];
    v[0] = 0;
    if (!strcmp(key, "header")) { if (set.preset < PR_NALL_UI) preset_name(set.preset, t); else p_strcpy(t, "FRODHES", 8); t[14] = 0; snprintf(v, n, "%s", t); }
    else if (!strcmp(key, "preset")) snprintf(v, n, "%u", set.preset);
    else if (!strcmp(key, "page")) snprintf(v, n, "%s", PAGE_TITLE[ui_page]);
    else if (!strcmp(key, "hint")) snprintf(v, n, "%s", page_hint(ui_page));
    else if (!strcmp(key, "msg")) snprintf(v, n, "%s", ui_msg_t && fm1_ms - ui_msg_t < MSG_MS ? ui_msg : "");
    else if (sscanf(key, "card.%u.%u", &a, &b) == 2 && a < PG_N && b < 4u) { slot_text(slot_at_v(a, 0, b), t); snprintf(v, n, "%s", t); }
    else if (sscanf(key, "label.%u.%u", &a, &b) == 2 && a < PG_N && b < 4u) snprintf(v, n, "%s", slot_label_v(a, 0, b));
    else if (!strcmp(key, "vpage")) { if (ui_page == PG_VOICE) snprintf(v, n, "%u/%u", ui_vp_ok() + 1u, vp_count()); }   /* VOICE n/m */
    else if (sscanf(key, "vcard.%u.%u", &a, &b) == 2 && a < ENG_VP_MAX && b < 4u) { slot_text(slot_at_v(PG_VOICE, a, b), t); snprintf(v, n, "%s", t); }
    else if (sscanf(key, "vlabel.%u.%u", &a, &b) == 2 && a < ENG_VP_MAX && b < 4u) snprintf(v, n, "%s", slot_label_v(PG_VOICE, a, b));
    else if (sscanf(key, "vhint.%u", &a) == 1) snprintf(v, n, "%s", a < vp_count() ? eng_ui()->hint[a] : "");
    else if (!strcmp(key, "vpages")) snprintf(v, n, "%u", vp_count());
    else if (!strcmp(key, "cards")) snprintf(v, n, "%u", ui_cards);   /* the rows' design variant (CC 11) */
    else if (sscanf(key, "rowbox%u", &a) == 1 && a < 4u)   /* the variants' row a: FIT SHORT FW VX0 VX1 UX0 UX1 LX1 BASE
                                                        * (row x; UX 0 0: no unit) */
        snprintf(v, n, "%u %u %d %d %d %d %d %d %d", ui_row_drawn[a].fit, ui_row_drawn[a].shortf, ui_row_drawn[a].fw,
                 ui_row_drawn[a].vx0, ui_row_drawn[a].vx1, ui_row_drawn[a].ux0, ui_row_drawn[a].ux1, ui_row_drawn[a].lx1,
                 CD_BASE);
    else if (!strcmp(key, "fitscan")) snprintf(v, n, "%u", ui_fit_scan());   /* every value of every card: how many
                                                        * do not fit (printed: FITSCAN lines) */
    else if (sscanf(key, "row%u", &a) == 1 && a < 4u) {   /* what row a shows: KIND V HI FACE (ui_row_drawn; FACE L / S) */
        static const char *const RK[] = {"none", "amount", "detent", "choice"};
        snprintf(v, n, "%s %d %d %c", RK[ui_row_drawn[a].kind], ui_row_drawn[a].v, ui_row_drawn[a].hi,
                 ui_row_drawn[a].big ? 'L' : 'S');
    }
    else if (!strcmp(key, "look")) {                   /* the METERS colours (CC 2): letter, then RGB565 hex: surf track
                                                        * outline hair fill edge tag lab val dim */
        mt_update();
        snprintf(v, n, "%c %04X %04X %04X %04X %04X %04X %04X %04X %04X %04X", 'A' + ui_look, mt.surf, mt.track,
                 mt.outline, mt.hair, mt.fill, mt.edge, mt.tag, mt.lab, mt.val, mt.dim);
    }
    else if (!strcmp(key, "hot")) { if (ui_hot_card < 4u && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS) snprintf(v, n, "%u", ui_hot_card); else snprintf(v, n, "-"); }
    else if (!strcmp(key, "title")) {                  /* the title line's left text: VOICE 2/3 */
        if (ui_page == PG_VOICE) snprintf(v, n, "%s %u/%u", PAGE_TITLE[ui_page], ui_vp_ok() + 1u, vp_count());
        else snprintf(v, n, "%s", PAGE_TITLE[ui_page]);
    }
    else if (!strcmp(key, "seqpos")) { seq_pos_text(t); snprintf(v, n, "%s", t); }   /* the SEQUENCER title's right text */
    else if (!strcmp(key, "count")) {                  /* the title's right text: the page n/m */
        fmt_frac(t, pg_pos(ui_page, ui_vp_ok()) + 1u, pg_count());   /* (the SEQUENCER draws seqpos there) */
        snprintf(v, n, "%s", t);
    }
    else if (!strcmp(key, "pages")) snprintf(v, n, "%u", pg_count());
    else if (sscanf(key, "card%u", &a) == 1 && a < 4u) { slot_text(slot_at(ui_page, a), t); snprintf(v, n, "%s", t); }
    else if (sscanf(key, "label%u", &a) == 1 && a < 4u) snprintf(v, n, "%s", slot_label(ui_page, a));
    else if (sscanf(key, "p.%u", &a) == 1 && a < P_COUNT) snprintf(v, n, "%u", PV(a));
    else if (sscanf(key, "led.%u", &a) == 1 && a < 64u) snprintf(v, n, "%u", host_led[a]);
    else if (sscanf(key, "glow.%u", &a) == 1 && a < 64u) snprintf(v, n, "%u", host_glow[a]);
    else if (sscanf(key, "u.%u", &a) == 1 && a >= 1u && a <= PR_NU) { preset_name(PR_NF + a - 1u, t); snprintf(v, n, "%s", t); }
    else if (!strcmp(key, "oct")) snprintf(v, n, "%d", set.oct);
    else if (!strcmp(key, "bpm")) { slot_text(S_BPM, t); snprintf(v, n, "%s", t); }
    else if (!strcmp(key, "sus")) snprintf(v, n, "%u", rh_hold);
    else if (!strcmp(key, "byp")) snprintf(v, n, "%u", fx_bypass);
    else if (!strcmp(key, "arp")) snprintf(v, n, "%u", arp_on());
    else if (!strcmp(key, "hold")) snprintf(v, n, "%u", ARP_HOLD);
    else if (!strcmp(key, "arpn")) snprintf(v, n, "%u", arp_n);
    else if (!strcmp(key, "chord")) snprintf(v, n, "%u", chord_on());
    else if (!strcmp(key, "play")) snprintf(v, n, "%u", seq_play);
    else if (!strcmp(key, "rec")) snprintf(v, n, "%u", seq_rec);
    else if (!strcmp(key, "notes")) snprintf(v, n, "%u", sq_n);
    else if (!strcmp(key, "savemode")) snprintf(v, n, "%u", pr_save_mode);
    else if (!strcmp(key, "undo")) snprintf(v, n, "%u", set.undo_valid);
    else if (!strcmp(key, "dirty")) snprintf(v, n, "%u", set_dirty);
    else if (!strcmp(key, "bars")) snprintf(v, n, "%d", eng_is_bars());
    else if (!strcmp(key, "voices")) snprintf(v, n, "%u", host_bits(eng_cur == ENG_BARS ? bar_active_bits : rh_active_bits));
    else if (!strcmp(key, "held")) snprintf(v, n, "%u", host_bits(eng_cur == ENG_BARS ? bar_held_bits : rh_held_bits));
    else if (!strcmp(key, "pitches")) { for (i = c = 0; i < 128u; i++) c += out_ref[i] != 0; snprintf(v, n, "%u", c + (arp_playing < 128u)); }   /* held by a source or the arpeggiator */
    else if (!strcmp(key, "srcs")) { for (i = c = 0; i < SRC_N; i++) c += src_active[i]; snprintf(v, n, "%u", c); }
    else if (!strcmp(key, "refs")) { for (i = c = 0; i < 128u; i++) c += out_ref[i]; snprintf(v, n, "%u", c); }
    else if (!strcmp(key, "quiet")) snprintf(v, n, "%d", eng_quiet() && fx_quiet());
    else if (!strcmp(key, "batt")) snprintf(v, n, "%u", batt_shown());
    else if (!strcmp(key, "battraw")) snprintf(v, n, "%d", batt_raw());
    else if (!strcmp(key, "drops")) snprintf(v, n, "%u", (unsigned)(eq_drop + ev_drop + engq_drop));   /* events lost */
    else return 1;
    return 0;
}
static void host_dump(void)
{
    static const char *const K[] = {"header", "preset", "page", "vpage", "hint", "msg", "oct", "bpm", "sus", "byp", "arp", "hold",
                                    "arpn", "chord", "play", "rec", "notes", "savemode", "undo", "dirty", "bars", "voices", "held", "pitches",
                                    "srcs", "refs", "quiet", "drops", "label0", "card0", "label1", "card1", "label2", "card2",
                                    "label3", "card3"};
    char key[24], v[64];
    uint32_t i, k;
    for (i = 0; i < sizeof K / sizeof K[0]; i++) { host_state(K[i], v, sizeof v); printf("@ %u %s %s\n", fm1_ms, K[i], v); }
    for (i = 0; i < PG_N; i++)
        for (k = 0; k < 4u; k++) {
            snprintf(key, sizeof key, "label.%u.%u", i, k); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v);
            snprintf(key, sizeof key, "card.%u.%u", i, k); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v);
        }
    for (i = 0; i < vp_count(); i++) {                  /* the engine's VOICE pages */
        snprintf(key, sizeof key, "vhint.%u", i); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v);
        for (k = 0; k < 4u; k++) {
            snprintf(key, sizeof key, "vlabel.%u.%u", i, k); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v);
            snprintf(key, sizeof key, "vcard.%u.%u", i, k); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v);
        }
    }
    for (i = 0; i < P_COUNT; i++) printf("@ %u p.%u %u\n", fm1_ms, i, PV(i));
    for (i = 0; i < 41u; i++) printf("@ %u led.%u %u\n", fm1_ms, i, host_led[i]);
    for (i = 1; i <= PR_NU; i++) { snprintf(key, sizeof key, "u.%u", i); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v); }
}
#endif
