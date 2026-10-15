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
#define APP_FL_RANGE(off, n) FL_IN(off, n, 0x97000u, 0x98FFFu)    /* the user preset bank (presets.c) */
#include "platform.c"
#include "rhodes.c"
#include "fx.c"
#include "params.c"
#include "tempo.c"
#include "arp.c"
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

/* a note from the keys or USB-MIDI: to the piano, or to the arpeggiator while it runs (each note remembers which,
 * so switching the arpeggiator under held keys never leaves one hanging); the keys' notes are echoed to USB-MIDI */
static uint8_t note_to_arp[128];
static void note_in_on(uint32_t n, uint32_t vel, int echo)
{
    if (arp_on()) { note_to_arp[n] = 1; arp_in_on(n, vel); return; }
    note_to_arp[n] = 0;
    rh_note_on(n, vel);
    if (echo) midi_send(0x90, n, vel);
}
static void note_in_off(uint32_t n, int echo)
{
    if (note_to_arp[n]) { note_to_arp[n] = 0; arp_in_off(n); return; }
    rh_note_off(n);
    if (echo) midi_send(0x80, n, 0);
}

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
    uint8_t held[NKEYS], cnt[128] = {0};          /* the note each key plays (255 none); per-note key counts */
    for (k = 0; k < NKEYS; k++) held[k] = 255;
    if (settings_load(&set, sizeof set) != 0) params_defaults();
    params_sanitize();
    arp_sanitize();
    if (set.usb_fixed > 1u) set.usb_fixed = 0;
    usb_pre_master = set.usb_fixed;
    palette_set(set.color);
    params_apply_all();
    tempo_set10(set.bpm10);
    set_dirty = 0;
    presets_load_bank();
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
        for (k = 0; k < NKEYS; k++) {           /* a key stops exactly the note it started */
            if (down >> k & 1u) {
                uint32_t n = scale_key_note(k);
                held[k] = (uint8_t)n;
                if (n < 128u && cnt[n]++ == 0u) note_in_on(n, set.key_vel, 1);
            }
            if (up >> k & 1u && held[k] < 128u) {
                uint32_t n = held[k];
                if (cnt[n] && --cnt[n] == 0u) note_in_off(n, 1);
                held[k] = 255;
            }
        }

        b = fm1_input_edges(&rel);              /* buttons pressed / released since the last call */
        sustain_button(b, rel);
        ui_input(b, rel);

        while (midi_get(&st, &d1, &d2)) {       /* USB-MIDI in, any channel */
            uint8_t ty = st & 0xF0u;
            if (ty == 0x90u && d2) note_in_on(d1 & 127u, d2, 0);
            else if (ty == 0x80u || ty == 0x90u) note_in_off(d1 & 127u, 0);
            else if (ty == 0xB0u) midi_cc(d1, d2);
            else if (st >= 0xF8u) tempo_clock(st);
            else if (ty == 0xC0u) preset_load(d1 < 12u ? d1 : d1 >= 16u && d1 < 32u ? PR_NF + d1 - 16u : 255u);
        }

        for (k = 0; k < NKEYS; k++)             /* LEDs per key: held, or its note still rings */
            if (held[k] < 128u ? 1 : 0) lit |= 1u << k;
            else if (rh_hold && note_sounding((uint32_t)scale_key_note(k))) lit |= 1u << k;
        arp_poll();
        ui_leds(lit, tempo_beat_led() || (arp_on() && arp_n && arp_playing < 128u));
        ui_draw();
        presets_save_poll();

        if (set_dirty && fm1_ms - set_t_change > 2000u && rh_quiet() && fx_quiet()) {
            settings_save(&set, sizeof set);    /* only when silent: the erase stalls the audio */
            set_dirty = 0;
        }
    }
}
