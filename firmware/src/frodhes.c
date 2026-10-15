/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes: an electric piano (Fender Rhodes physical model) for the M-VAVE FM-1. Built on Dinghy.
 *
 *   keys       play the piano; the notes go out over USB-MIDI too
 *   OCT- OCT+  the octave (kept in flash)
 *   REC        the sustain pedal: hold it, or tap it to latch (tap again to let go)
 *   MASTER     the volume (platform.c)
 *   USB-MIDI   notes in (with velocity), CC 64 the sustain pedal
 *
 * This file is the app: panel, MIDI, screen, settings. The sound is rhodes.c. Below both is platform.c
 * (Dinghy / SLOOP / Felucca): updates, the USB rescue, USB-MIDI, USB audio, keys, knobs, LEDs, screen, settings.
 * Rules of the road (no FPU, no libgcc): integers only, no float, no 64-bit division. docs/RULES.md.
 * tests/host.c runs this file on a computer: the screen as a PNG, the sound as a WAV. */
#define APP_NAME "FRODHES"
#include "platform.c"
#include "rhodes.c"
#include "fx.c"

static int32_t voice_mono[256];
static void app_audio(int32_t *out, uint32_t n) { rh_render(voice_mono, n); fx_process(voice_mono, out, n); }


/* ---- the screen: the name, the last note big, the keyboard with what is held, the octave */
#define C_ACC RGB(255, 170, 60)                 /* warm amber */
#define C_KEY RGB(210, 210, 210)
#define C_OFF RGB(90, 90, 90)
#define KEY0 53u                                /* key 0 = F3 */
#define NKEYS 27u
static uint32_t last_note = 255;
static int black(uint32_t note) { return (0x54Au >> (note % 12u)) & 1u; }

static void draw_note(void)
{
    static const char *const NAME[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
    char s[8] = "-";
    if (last_note < 128u) {
        const char *p = NAME[last_note % 12u];
        uint32_t i = 0;
        while (*p) s[i++] = *p++;
        if (last_note >= 12u) s[i++] = (char)('0' + last_note / 12u - 1u);
        s[i] = 0;
    }
    draw_text_box(0, 82, 240, &FONT_L, s, C_WHITE, 1);
}

static void draw_keys(uint32_t lit)             /* bit k: key k is held */
{
    uint32_t k, w = 0;
    cv_begin(240, 56, C_BLACK);
    for (k = 0; k < NKEYS; k++)                 /* 16 white keys of 14 px from x = 8 */
        if (!black(KEY0 + k)) {
            cv_rect(8 + (int32_t)w * 14, 0, 13, 56, lit >> k & 1u ? C_ACC : C_KEY);
            w++;
        }
    for (k = 0, w = 0; k < NKEYS; k++)          /* the black ones on the line between two white */
        if (black(KEY0 + k))
            cv_rect(8 + (int32_t)w * 14 - 5, 0, 9, 34, lit >> k & 1u ? C_ACC : C_BLACK);
        else
            w++;
    cv_blit(0, 140);
}

static void draw_status(int32_t oct, int pedal)
{
    char s[20] = "OCTAVE +0";
    s[7] = oct < 0 ? '-' : '+';
    s[8] = (char)('0' + (oct < 0 ? -oct : oct));
    if (pedal) { const char *p = "  PEDAL"; uint32_t i = 9; while (*p) s[i++] = *p++; s[i] = 0; }
    draw_text_box(0, 212, 240, &FONT_S, s, C_OFF, 1);
}

/* ---- the sustain pedal: REC held, or latched by a tap; MIDI CC 64 too. Either holds the dampers off. */
#define BTN_SUSTAIN BTN_REC
#define SUS_TAP_MS 250u                         /* a press shorter than this is a tap: it latches */
static uint8_t sus_held, sus_latched, sus_midi, sus_was_latched;
static uint32_t sus_t;

static void sustain_update(void) { rh_pedal_level(sus_held || sus_latched ? 127u : sus_midi); }   /* sus_midi: CC64 level */

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

/* MIDI CC: the reface CP map (switch CCs: 0-42 off, 43-85 A, 86-127 B), plus Frodhes-only and developer CCs */
static void midi_cc(uint32_t cc, uint32_t v)
{
    uint32_t sw = v < 43u ? 0u : v < 86u ? 1u : 2u;
    switch (cc) {
    case 64: sus_midi = (uint8_t)v; sustain_update(); break;            /* continuous: half-pedal */
    case 80: rh_type = (uint8_t)(v * 4u / 128u); fx_set(FXP_DRIVE_ROW, (int32_t)rh_type); break;   /* TYPE */
    case 81: fx_set(FXP_DRIVE, (int32_t)v); break;
    case 17: fx_set(FXP_TW_SW, (int32_t)sw); break;
    case 18: fx_set(FXP_TW_DEPTH, (int32_t)v); break;
    case 19: fx_set(FXP_TW_RATE, (int32_t)v); break;
    case 85: fx_set(FXP_CP_SW, (int32_t)sw); break;
    case 86: fx_set(FXP_CP_DEPTH, (int32_t)v); break;
    case 87: fx_set(FXP_CP_SPEED, (int32_t)v); break;
    case 88: fx_set(FXP_DL_SW, (int32_t)sw); break;
    case 89: fx_set(FXP_DL_DEPTH, (int32_t)v); break;
    case 90: fx_set(FXP_DL_TIME, (int32_t)v); break;
    case 91: fx_set(FXP_RV_DEPTH, (int32_t)v); break;
    case 102: fx_set(FXP_RAW, v >= 64u); break;                         /* developer: bypass the effects */
    case 103: fx_set(FXP_TW_MODE, v >= 64u); break;                     /* tremolo: 0 autopan, 1 amplitude */
    case 104: fx_set(FXP_DL_FDBK, (int32_t)v); break;
    case 105: fx_set(FXP_CP_FDBK, (int32_t)v); break;
    case 20: fx_set(FXP_TW_SHAPE, (int32_t)v); break;
    default: break;
    }
}

static void app_main(void)
{
    struct { int8_t oct; } set = {0};           /* what survives a power-off (add yours, up to 256 bytes) */
    uint32_t prev = 0, k, t_change = 0, dirty = 0, lit_drawn = ~0u, note_drawn = ~0u;
    int32_t oct_drawn = 99, pedal_drawn = -1;
    uint8_t held[NKEYS] = {0};                  /* the note each key plays */
    settings_load(&set, sizeof set);
    if (set.oct < -2 || set.oct > 2) set.oct = 0;

    lcd_fill(0, 0, 240, 240, C_BLACK);
    draw_text_box(0, 18, 240, &FONT_L, APP_NAME, C_ACC, 1);
    draw_text_box(0, 52, 240, &FONT_S, "electric piano  v" APP_VER, C_OFF, 1);

    for (;;) {
        uint32_t keys, down, up, rel, b, lit, base = (uint32_t)(KEY0 + 12 * set.oct);
        uint8_t st, d1, d2;
        platform_poll();

        keys = fm1_in.notes;                    /* bit k: key k (0 = F3 .. 26 = G5) */
        down = keys & ~prev;
        up = prev & ~keys;
        prev = keys;
        for (k = 0; k < NKEYS; k++) {           /* (a key stops the note it started: the octave may change meanwhile) */
            if (down >> k & 1u) {
                held[k] = (uint8_t)(base + k);
                rh_note_on(held[k], 100); last_note = held[k];
                midi_send(0x90, held[k], 100);
            }
            if (up >> k & 1u) { rh_note_off(held[k]); midi_send(0x80, held[k], 0); }
        }

        b = fm1_input_edges(&rel);              /* buttons pressed since the last call (bit = button id) */
        if (b & (1u << BTN_OCT_DOWN) && set.oct > -2) { set.oct--; dirty = 1; t_change = fm1_ms; }
        if (b & (1u << BTN_OCT_UP) && set.oct < 2) { set.oct++; dirty = 1; t_change = fm1_ms; }
        sustain_button(b, rel);

        while (midi_get(&st, &d1, &d2)) {       /* USB-MIDI in, any channel */
            uint8_t ty = st & 0xF0u;
            if (ty == 0x90u && d2) { rh_note_on(d1, d2); last_note = d1; }
            else if (ty == 0x80u || ty == 0x90u) rh_note_off(d1);
            else if (ty == 0xB0u) midi_cc(d1, d2);
        }

        base = (uint32_t)(KEY0 + 12 * set.oct);
        lit = rh_lit(base, NKEYS);              /* what is held, on the keys of this octave */
        for (k = 0; k < NKEYS; k++)
            fm1_led_key(KEY_LED(k), lit >> k & 1u);
        fm1_led_key(BTN_OCT_DOWN, set.oct < 0);
        fm1_led_key(BTN_OCT_UP, set.oct > 0);
        fm1_led_key(BTN_SUSTAIN, rh_hold);

        if (lit != lit_drawn) { lit_drawn = lit; draw_keys(lit); }   /* redraw only what changed */
        if (last_note != note_drawn) { note_drawn = last_note; draw_note(); }
        if (set.oct != oct_drawn || rh_hold != pedal_drawn) {
            oct_drawn = set.oct; pedal_drawn = rh_hold; draw_status(set.oct, rh_hold);
        }

        if (dirty && rh_quiet() && fm1_ms - t_change > 2000u) {   /* 2 s after the change, when silent (an erase stalls the sound) */
            settings_save(&set, sizeof set);
            dirty = 0;
        }
    }
}
