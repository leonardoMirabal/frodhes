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
#include "rhodes.c"
#include "fx.c"
#include "params.c"
#include "tempo.c"
#include "arp.c"
#include "notes.c"
#include "seq.c"
#include "ui.c"
#include "presets.c"

static int32_t voice_mono[256];
static void app_audio(int32_t *out, uint32_t n)
{
#ifndef DINGHY_HOST
    uint32_t t0 = fm1_ticks(), us;
#endif
    rh_render(voice_mono, n);
    fx_process(voice_mono, out, n);
#ifndef DINGHY_HOST
    us = (fm1_ticks() - t0) / 24u;                 /* 24 MHz ticks -> us (a constant divide) */
    prof_us = us;
    if (us > prof_max_us) prof_max_us = us;
    prof_over = us > 5000u ? prof_over + 1u : 0u;  /* two blocks over 5.0 ms of the 5.8 ms: shed a voice (8 voices
                                                    * struck with every effect on peak at 4.8 ms: tools/hil_cpu.py) */
    if (prof_over >= 2u) { rh_shed_req = 1; prof_over = 0; }
#endif
}


/* ---- the sustain pedal: REC held, or latched by a tap; MIDI CC 64 too. Either holds the dampers off. */
#define BTN_SUSTAIN BTN_REC
#define SUS_TAP_MS 250u                         /* a press shorter than this is a tap: it latches */
#define NKEYS 27u
static uint8_t sus_held, sus_latched, sus_midi, sus_was_latched;
static uint32_t sus_t;

static void sustain_update(void) { rh_pedal_level(sus_held || sus_latched ? 127u : sus_midi); ui_mark(E_HEADER); }

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
 * 119 developer: bypass the effects (tests of the voice engine) */
static void midi_cc(uint32_t cc, uint32_t v)
{
    uint32_t id;
    if (cc == 64u) { sus_midi = (uint8_t)v; sustain_update(); return; }
    if (cc == 119u) { fx_set(FXP_RAW, v >= 64u); return; }
    if (cc == 111u) { fx_bypass = v >= 64u; ui_mark(E_HEADER); return; }    /* the FX button's bypass (0.8) */
    if (cc == 114u) { set.usb_fixed = v >= 64u; usb_pre_master = set.usb_fixed; ui_mark(E_CARDS); return; }   /* USB VOL */
    if (cc == 116u) {                                  /* profiler query: CC 116 -> CC 116/117 = max audio us (MSB, LSB
                                                        * of us / 4), CC 118 = this block's (us / 32), CC 115 = voices shed
                                                        * (mod 128); then the max resets */
        uint32_t mx = prof_max_us >> 2;
        if (mx > 16383u) mx = 16383u;
        midi_send(0xB0, 116, mx >> 7); midi_send(0xB0, 117, mx & 127u);
        midi_send(0xB0, 118, prof_us >> 5 > 127u ? 127u : prof_us >> 5);
        midi_send(0xB0, 115, rh_shed_count & 127u);
        prof_max_us = 0;
        return;
    }
    if (cc >= 52u && cc <= 55u) {                      /* the arpeggiator (0.9): 52 MODE 0..5 (0 off), 53 RATE 0..5,
                                                        * 54 OCTAVES 0..3 (= 1..4), 55 GATE 10..100 % */
        if (cc == 52u) { ARP_MODE = (uint8_t)(v > 5u ? 5u : v); if (ARP_MODE) ARP_LAST = ARP_MODE; else arp_clear(); }
        else if (cc == 53u) ARP_RATE = (uint8_t)(v > 5u ? 5u : v);
        else if (cc == 54u) ARP_OCT = (uint8_t)(v > 3u ? 3u : v);
        else ARP_GATE = (uint8_t)(v < 10u ? 10u : v > 100u ? 100u : v);
        ui_mark(E_CARDS);
        return;
    }
    if (cc >= 56u && cc <= 63u) {                      /* chord mode and the sequencer (0.9.1): 56 TYPE 0..14, 57
                                                        * INVERSION 0..3, 58 VOICING 0..2, 59 STRUM 0..12 (x 5 ms); 60 play
                                                        * (>= 64) / stop, 61 BARS 1..16, 62 QUANT 0..5, 63 recording */
        if (cc == 56u) CH_TYPE = (uint8_t)(v < CH_NTYPE ? v : CH_NTYPE - 1u);
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
    if (cc == 120u || cc == 123u) { rh_all_off(); return; }
    if (cc == 85u) {                                   /* reface CHO/PHA switch: off / chorus / phaser (one of them) */
        param_set(P_CP_SW, v >= 43u && v < 86u); param_set(P_PH_SW, v >= 86u);
        ui_mark_param(P_CP_SW);
        return;
    }
    if ((cc == 86u || cc == 87u) && PV(P_PH_SW) && !PV(P_CP_SW)) cc += 13u - 86u;   /* reface: the phaser's */
    for (id = 0; id < P_COUNT; id++)
        if (PARAMS[id].cc == cc) {
            int32_t x = (int32_t)v;
            if (PARAMS[id].hi == 2u) x = v < 43u ? 0 : v < 86u ? 1 : 2;
            else if (id == P_TYPE) x = (int32_t)(v >> 5);
            else if (PARAMS[id].hi < 127u) x = (int32_t)(v * (PARAMS[id].hi + 1u) >> 7);
            param_set(id, x);
            if (id == P_DL_SYNC || id == P_TW_SYNC || id == P_CP_SYNC || id == P_PH_SYNC) tempo_sync_publish();
            ui_mark_param(id);
            return;
        }
}

/* a note a voice is still sounding (for the per-key LEDs under sustain) */
static int note_sounding(uint32_t n)
{
    uint32_t v, act = rh_active_bits;
    for (v = 0; v < RH_NV; v++) if (act >> v & 1u && rh_notes[v] == n) return 1;
    return 0;
}

static void app_main(void)
{
    uint32_t prev = 0, k;
    if (settings_load(&set, sizeof set) != 0) params_defaults();
    params_sanitize();
    arp_sanitize();
    chord_sanitize();
    seq_sanitize();
    if (set.usb_fixed > 1u) set.usb_fixed = 0;
    usb_pre_master = set.usb_fixed;
    palette_set(set.color);
    params_apply_all();
    tempo_set10(set.bpm10);
    set_dirty = 0;
    presets_load_bank();
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

        while (midi_get(&st, &d1, &d2)) {       /* USB-MIDI in, any channel */
            uint8_t ty = st & 0xF0u;
            if (ty == 0x90u && d2) src_on(SRC_MIDI + (d1 & 127u), d1 & 127u, d2, 0);
            else if (ty == 0x80u || ty == 0x90u) src_off(SRC_MIDI + (d1 & 127u));
            else if (ty == 0xB0u) midi_cc(d1, d2);
            else if (st >= 0xF8u) tempo_clock(st);
            else if (ty == 0xC0u) preset_load(d1 < 12u ? d1 : d1 >= 16u && d1 < 32u ? PR_NF + d1 - 16u : 255u);
        }

        for (k = 0; k < NKEYS; k++)             /* LEDs per key: held, or its note still rings */
            if (src_active[SRC_KEYS + k]) lit |= 1u << k;
            else if (rh_hold && note_sounding((uint32_t)scale_key_note(k))) lit |= 1u << k;
        src_poll();
        seq_poll();
        arp_poll();
        ui_leds(lit, tempo_beat_led() || (arp_on() && arp_n && arp_playing < 128u));
        ui_draw();
        presets_save_poll();
        seq_save_poll();

        if (set_dirty && fm1_ms - set_t_change > 2000u && rh_quiet() && fx_quiet()) {
            settings_save(&set, sizeof set);    /* only when silent: the erase stalls the audio */
            set_dirty = 0;
        }
    }
}
