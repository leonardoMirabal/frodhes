/* SPDX-License-Identifier: GPL-3.0-only */
/* A fake FM-1 (or SMK-37) on a computer: the app (firmware/src/frodhes.c) runs unchanged on it. platform.c includes
 * this file instead of the hardware when DINGHY_HOST is set. A script presses keys over time; out come
 * the screen (PPM, at the times SHOT asks) and the sound (WAV, as the FM-1 sends it over USB).
 *
 *   mkdir -p build/host
 *   cc -O2 -Wall -DDINGHY_HOST -Ifirmware/src -Ifirmware/gen -o build/host/frodhes firmware/src/frodhes.c -lm
 *   build/host/frodhes build/host/frodhes      (-> build/host/frodhes.wav, build/host/frodhes-<ms>.ppm)
 *   build/host/frodhes OUT script.txt [FPS]   (your own script; with FPS: OUT-NNNNN.ppm, a frame every 1/FPS s:
 *                                             a film of your firmware, e.g. ffmpeg -r 30 -i OUT-%05d.ppm ...)
 *   -DBOARD_SMK37=1 -DFR_VOICES=7u (BOARD=smk37 tests/run_host.sh, make test-smk): the SMK-37 build: its keys with
 *                                             velocity, the outer keys, pads, faders, wheels, the pedal
 *
 * Change SCRIPT below to try your own app. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef APP_NAME
#define APP_NAME "DINGHY"
#endif
#ifndef APP_VER
#define APP_VER "0.0"
#endif
#define FS 44100u
#define HALF_FRAMES 256u

static volatile uint32_t fm1_ms;
#if BOARD_SMK37
/* the SMK-37 (hal/smk_input.h's API, the part the app may use): the 37 keys with velocity (keys 5..31 are the FM-1's
 * 27 note keys: fm1_in.notes), the outer keys (0..4, 32..36) and the pads as events, 4 faders, 2 wheels, the pedal;
 * 18 buttons, 8 encoders (K1 = MASTER, smk_master); the pad LEDs (lcd.c padled_*). What smk_input.h does not have
 * (the FM-1's TRS flag, its LED duty and groups) the app gives itself (frodhes_smk.h), so it is not here */
#define FM1_NKEY 37u
#define FM1_NBTN 18u
#define FM1_NENC 8u
#define SMK_NPAD 16u
#define SMK_KEY_F3 5u
static struct {
    uint32_t notes, buttons, frames, adc_ok, adc_miss;
    uint64_t keys;
    uint8_t key_vel[FM1_NKEY], fader[4], pad_press[SMK_NPAD];
    uint16_t wheel[2], pedal, batt, pad_raw[SMK_NPAD], pad_base[SMK_NPAD];
} fm1_in = {.wheel = {535, 620}, .pedal = 985};   /* (the wheels at rest, no pedal: as a unit reads them) */
static uint8_t smk_pad_events, smk_pad_piano, smk_pad_grid;   /* (smk_input.h: the pads as events / layouts) */
static uint16_t smk_pad_pick;
static int32_t smk_master = 724;               /* (smk_input.h: K1 as the MASTER pot) */
static int32_t host_q[2][64];                   /* 0: pad events, 1: outer keys (key << 8 | velocity, 0 = release) */
static uint32_t host_qw[2], host_qr[2];
static void host_qput(int i, int32_t v) { host_q[i][host_qw[i]++ % 64u] = v; }
static int32_t host_qtake(int i) { return host_qr[i] == host_qw[i] ? -1 : host_q[i][host_qr[i]++ % 64u]; }
static int32_t smk_pad_take(void) { return host_qtake(0); }
static int32_t smk_extkey_take(void) { return host_qtake(1); }
static uint32_t host_pick;
static uint32_t smk_pick_take(void) { uint32_t h = host_pick; host_pick = 0; return h; }
/* a pad hit (v) or let go (0), as smk_input.h smk__padq: an event when the app takes the pads (smk_pad_events), a pick
 * (smk_pad_pick), else a note key: the white keys F3..G5 (low row 8..15 = F3..E4), or the piano layout */
static const uint8_t HOST_WHITE[SMK_NPAD] = {0, 2, 4, 6, 7, 9, 11, 12, 14, 16, 18, 19, 21, 23, 24, 26};
static const int8_t HOST_PIANO[SMK_NPAD] = {7, 9, 11, 12, 14, 16, 18, 19, 8, 10, -1, 13, 15, 17, -1, 20};
static uint8_t host_pad_note[SMK_NPAD];
static void host_pad(uint32_t p, uint32_t v)
{
    int32_t n;
    p &= 15u;
    if (smk_pad_events) { host_qput(0, (int32_t)(p << 8 | v)); return; }
    if (v && (smk_pad_pick >> p & 1u)) { host_pick |= 1u << p; return; }
    if (v) {
        n = smk_pad_piano ? HOST_PIANO[p] : (int32_t)HOST_WHITE[smk_pad_grid ? p : p < 8u ? p + 8u : p - 8u];
        if (n < 0) return;
        host_pad_note[p] = (uint8_t)(n + 1);
        fm1_in.key_vel[SMK_KEY_F3 + (uint32_t)n] = (uint8_t)v;
        fm1_in.notes |= 1u << n;
    } else if (host_pad_note[p]) {
        fm1_in.notes &= ~(1u << (host_pad_note[p] - 1u));
        host_pad_note[p] = 0;
    }
}
static uint32_t padled_rgb[SMK_NPAD];           /* as lcd.c: g << 16 | r << 8 | b */
static void padled_set(uint32_t pad, uint8_t r, uint8_t g, uint8_t b) { if (pad < SMK_NPAD) padled_rgb[pad] = (uint32_t)g << 16 | (uint32_t)r << 8 | b; }
static void padled_flush(void) {}
static volatile uint8_t smk_led_dark;           /* (smk_input.h: the button LEDs' brightness) */
static uint8_t smk_store_cleared, smk_trim_ok = 1;   /* (platform.c: what the panel check shows) */
static uint32_t smk_jedec = 0x204016u;
static const char *host_xlat(const char *s);    /* frodhes_smk.h: an FM-1 text in the SMK's words (ui.c ui_text) */
#else
static struct { uint32_t notes, buttons; } fm1_in;
#endif
static uint32_t host_edges, host_released;
static int32_t host_enc[8];
static uint8_t host_led[64];                    /* the last state each LED id was set to */
static uint32_t fm1_input_edges(uint32_t *released) { uint32_t b = host_edges; host_edges = 0; *released = host_released; host_released = 0; return b; }
static uint32_t fm1_input_note_edges(void) { return 0; }   /* (the script sets fm1_in.notes) */
static int32_t fm1_enc_take(uint32_t e) { int32_t d = e < 8u ? host_enc[e] : 0; if (e < 8u) host_enc[e] = 0; return d; }
static void fm1_led_key(uint32_t id, int on) { if (id < 64u) host_led[id] = (uint8_t)(on != 0); }
static volatile uint8_t usb_pre_master;          /* platform.c: USB audio before MASTER (no USB here) */
#if !BOARD_SMK37
static volatile uint8_t uart_midi_on = 1;        /* midi_uart.c: the TRS input ("T trs ...": dropped while 0) */
static uint8_t fm1_led_duty[2] = {16u, 16u};   /* hal/fm1_input.h: the lit duty of the keys and the buttons, /16 (DISPLAY BRIGHTNESS) */
static void fm1_led_group_ids(uint32_t id0, uint32_t id1) { (void)id0; (void)id1; }   /* (which LEDs are the keys: the hardware's) */
static void fm1_led_build(void) {}               /* (the scan ISR's tables: the hardware's) */
#endif
static uint8_t host_glow[64];                   /* the dim layer (fm1_led_key_glow) */
static void fm1_led_key_glow(uint32_t id, int on) { if (id < 64u) host_glow[id] = (uint8_t)(on != 0); }

/* the screen: 240 x 240 RGB565, as the ST7789 gets it (gfx.c sends the bytes swapped) */
static uint16_t fb[240 * 240];
static void lcd_sync(void) {}
static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint16_t c)
{
    uint32_t i, j;
    for (j = y; j < y + h && j < 240u; j++)
        for (i = x; i < x + w && i < 240u; i++)
            fb[j * 240u + i] = c;
}
static void lcd_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *px)
{
    uint32_t i, j;
    for (j = 0; j < h; j++)
        for (i = 0; i < w; i++)
            if (x + i < 240u && y + j < 240u) {
                uint16_t c = px[j * w + i];
                fb[(y + j) * 240u + x + i] = (uint16_t)(c >> 8 | c << 8);
            }
}
#define CV_MAX (240u * 64u)
#include "gfx.c"

/* Every erase and write the firmware makes (the settings here, the preset bank in presets.c, the pattern in seq.c)
 * goes through host_fl_check first: the place must pass the firmware's own allow-list (FL_GUARD: store.h with
 * frodhes_store.h's APP_FL_RANGE, hal/fm1_flash_map.h) and lie in the board's store (the SMK-37: FL_DATA, the VM;
 * the FM-1: FL_DATA or the globals sector), else a FAIL line, the call fails as on the device, and the run fails.
 * So every script checks the flash safety of a board we cannot try (BOARD=smk37); FRODHES_FLASHLOG=1 prints every
 * call. */
#define HOST_FL_CHECK 1
#include "store.h"
static uint32_t host_fails, host_fl_calls;
static int host_fl_check(const char *what, uint32_t at, uint32_t n)
{
#if BOARD_SMK37
    int in_store = FL_IN(at, n, FL_DATA_LO, FL_DATA_HI);
#else
    int in_store = FL_IN(at, n, FL_DATA_LO, FL_DATA_HI) || FL_IN(at, n, FL_GLOB_LO, FL_GLOB_HI);
#endif
    host_fl_calls++;
    if (!FL_GUARD(at, n) || !in_store) {
        printf("%6u ms  FAIL flash %s 0x%05X + %u: %s\n", fm1_ms, what, at, n,
               !FL_GUARD(at, n) ? "refused by the allow-list (FL_GUARD)" : "outside the store");
        host_fails++;
        return -1;
    }
    if (getenv("FRODHES_FLASHLOG")) printf("%6u ms  flash %s 0x%05X + %u ok\n", fm1_ms, what, at, n);
    return 0;
}

static uint8_t host_usb_link = 1;                /* "T usblink 0": the USB cable out (frodhes.c usb_link) */
static uint8_t host_usb_rec;                    /* "T usbrec 1": a computer reads the USB audio (nothing reads it since the split flash erase) */
static uint8_t host_store[256 + 4];             /* the settings record: [0] = 1 when saved, the bytes from [4] */
static uint32_t host_store_len = 256;           /* its length (platform.c: a record of another size is "nothing") */
static int settings_load(void *p, uint32_t n)
{
    if (host_store[0] != 1 || n > 256u || n != host_store_len) return -1;
    memcpy(p, host_store + 4, n);
    return 0;
}
static void platform_poll(void);
/* The time a save takes (platform.c, fm1_flash.h): on the FM-1 a settings save is a split erase (~85 ms from start to
 * end for a 20 ms erase) and three page programs, ~100 ms. settings_save (platform.c area_save, fl_split_op) holds
 * the main loop all that time: here the milliseconds go by (the audio ISR, the script's MIDI into the queue) without
 * the app's loop, so a note sent meanwhile is taken late, as on the device. settings_save_start / settings_save_poll
 * (the app's auto save) run while the loop goes on: written HOST_SAVE_MS after the start. */
#define HOST_SAVE_MS 100u
static void host_store_put(const void *p, uint32_t n)
{
    host_store[0] = 1;
    host_store_len = n;
    memcpy(host_store + 4, p, n);
    printf("%6u ms  settings saved (%u B)\n", fm1_ms, n);
}
static __attribute__((unused)) int settings_save(const void *p, uint32_t n)
{
    uint32_t i;
    if (host_fl_check("erase settings", SETTINGS_AT, 0x1000u) || host_fl_check("write settings", SETTINGS_AT, 12u + n))
        return -1;
    for (i = 0; i < HOST_SAVE_MS; i++) platform_poll();   /* (the main loop held) */
    host_store_put(p, n);
    return 0;
}
static uint8_t host_save_buf[256];
static uint32_t host_save_n, host_save_t;
static uint8_t host_save_on;
static int settings_save_poll(void)
{
    if (!host_save_on) return 0;
    if (fm1_ms - host_save_t < HOST_SAVE_MS) return 1;
    host_save_on = 0;
    host_store_put(host_save_buf, host_save_n);
    return 0;
}
static int settings_save_start(const void *p, uint32_t n)
{
    if (host_save_on) { host_save_on = 0; host_store_put(host_save_buf, host_save_n); }   /* (finished first) */
    if (n > sizeof host_save_buf || host_fl_check("erase settings", SETTINGS_AT, 0x1000u) ||
        host_fl_check("write settings", SETTINGS_AT, 12u + n))
        return -1;
    memcpy(host_save_buf, p, n);
    host_save_n = n;
    host_save_t = fm1_ms;
    host_save_on = 1;
    return 1;
}

/* The flash across power cycles: with FRODHES_FLASH=FILE the run starts from FILE's flash (if it exists) and writes
 * the flash back at its end, so a second run is the FM-1 switched on again. Only what the firmware saved is there.
 * FILE is five 4 KiB sectors as on the FM-1: the settings ([magic DNGY][len][crc32] + the record, platform.c's
 * format and checks), the preset bank A / B (presets.c), the pattern A / B (seq.c). */
static uint8_t host_bank[2][4096];              /* presets.c's and seq.c's sectors (their definitions: there) */
static uint8_t sq_host_fl[2][4096];
static uint32_t crc32(const uint8_t *p, uint32_t n);   /* presets.c (host) */
#define HOST_FLASH_MAGIC 0x59474E44u            /* "DNGY", platform.c SETTINGS_MAGIC */
static void host_flash_load(const char *path)
{
    static uint8_t img[5][4096];
    uint32_t h[3];
    FILE *f = fopen(path, "rb");
    if (!f) return;                             /* no file: a new FM-1 (nothing saved) */
    if (fread(img, 1, sizeof img, f) != sizeof img) { fprintf(stderr, "%s: short flash image\n", path); exit(2); }
    fclose(f);
    memcpy(h, img[0], sizeof h);
    if (h[0] == HOST_FLASH_MAGIC && h[1] <= 256u && crc32(img[0] + sizeof h, h[1]) == h[2]) {
        host_store[0] = 1; host_store_len = h[1];
        memcpy(host_store + 4, img[0] + sizeof h, h[1]);
    }                                           /* else: a bad magic, length or CRC reads as nothing saved */
    memcpy(host_bank, img[1], sizeof host_bank);
    memcpy(sq_host_fl, img[3], sizeof sq_host_fl);
}
static void host_flash_save(const char *path)
{
    static uint8_t img[5][4096];
    uint32_t h[3] = {HOST_FLASH_MAGIC, host_store_len, 0};
    FILE *f;
    memset(img, 0xFF, sizeof img);
    if (host_store[0] == 1) {
        h[2] = crc32(host_store + 4, host_store_len);
        memcpy(img[0], h, sizeof h);
        memcpy(img[0] + sizeof h, host_store + 4, host_store_len);
    }
    memcpy(img[1], host_bank, sizeof host_bank);
    memcpy(img[3], sq_host_fl, sizeof sq_host_fl);
    if (!(f = fopen(path, "wb")) || fwrite(img, 1, sizeof img, f) != sizeof img) { perror(path); exit(2); }
    fclose(f);
}

static uint8_t mq[64][4];                       /* status, data 1, data 2, source (1 USB, 2 TRS) */
static uint32_t mq_w, mq_r;
static int midi_get(uint8_t *st, uint8_t *d1, uint8_t *d2)   /* 1 (USB) or 2 (TRS), as platform.c's; 0: none */
{
    int src;
    if (mq_r == mq_w) return 0;
    *st = mq[mq_r % 64u][0]; *d1 = mq[mq_r % 64u][1]; *d2 = mq[mq_r % 64u][2]; src = mq[mq_r % 64u][3];
    mq_r++;
    return src;
}
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2) { printf("%6u ms  MIDI out %02X %u %u\n", fm1_ms, st, d1, d2); }

static void app_audio(int32_t *out, uint32_t n);
static void app_main(void);
static void eng_host_report(void);              /* engine.c: the engines' host counters */

/* the script: at t ms, keys down (bit k = key k, 0 = F3), a button edge, a MIDI message. A script file (the second
 * argument) replaces it, a line each:
 *   T keys MASK          the keys held from now on (hex or decimal)
 *   T btn ID             a tap: pressed and released at once (an edge only)
 *   T press ID           button ID goes down (held until "release")
 *   T release ID         button ID goes up
 *   T turn ENC DELTA     encoder ENC moves DELTA detents
 *   T midi ST D1 D2      a USB-MIDI message in
 *   T trs ST D1 D2       a message on the TRS jack (dropped while TRS IN is off, as midi_uart.c does)
 *   T stall MS           the main loop is busy for MS ms from now (one platform_poll call lasts MS + 1 ms: the
 *                        messages wait in the ring, 64 at most, stamped with their arrival as usb.c does; the audio
 *                        blocks go on)
 *   T renderload MS      from now on each audio block keeps the main loop from running for MS ms more (the
 *                        FM-1's render preempts it for 2-5 ms a block)
 *   T shed               the audio ran late: the next block sheds a voice, as the FM-1's ISR asks when two blocks run
 *                        over 5 ms (frodhes.c app_audio; the host has no CPU clock)
 *   T shot               a screenshot (OUT-T.ppm)
 *   T vkey K RAW         key K's next press lands with the driven reading RAW (firmware/src/keyvel.h kv_host_vkey:
 *                        as the FM-1's converter would measure it; without one a key plays at once at CONTEXT)
 *   T expect led ID V    fails the run unless LED ID is V (0/1)
 *   T expect rms OP DB   fails unless the RMS of the last 50 ms is OP (< or >) DB dBFS
 *   T expect preset      fails unless every parameter equals what the preset the header shows sets (frodhes.c), a
 *                        factory or a user preset
 *   T expect sound       fails unless what the engines and effects run with is what the panel says (frodhes.c)
 *   T expect state K V   fails unless the state K (frodhes.c host_state: header, page, card0..3, oct, ...) reads V
 *                        (V: one word, '_' for a space)
 *   T usblink V          the USB link up (1, the default) or the cable out (0): the pitch bend resets (frodhes.c)
 *   T usbrec V           a computer reads the USB audio (1) or not (0): the automatic saves go as without (fm1_flash.h fl_split_op)
 *   T dump               prints the state, one "@ T K V" line each (every page's cards, the parameters, LEDs ...)
 *   T end                stop; the exit code is the number of failed expects
 * SMK-37 builds (BOARD_SMK37) also (an FM-1 build ignores these lines, with a warning):
 *   T vel V              the velocity of the keys pressed from now on (1..127; "keys" lines take it)
 *   T xkey K V           keybed key K (0..36) at velocity V, 0 = let go (the outer keys 0..4, 32..36 as events;
 *                        5..31 are the FM-1's 27 note keys: set as "keys" does, at V)
 *   T pad P V            hardware pad P (0..15; 0..7 the top row) hit at V, 0 = let go (as the HAL: a white key F3..G5, or an event with smk_pad_events)
 *   T fader F V          fader F (0..3) to V (0..127)
 *   T wheel W RAW        wheel W (0 pitch, 1 mod) at the raw ADC reading RAW (0..1023; at rest 535 / 620)
 *   T pedal RAW          the pedal jack's raw reading (0..1023; 985 = nothing plugged in)
 * With FRODHES_FLASH=FILE the flash is kept in FILE across runs (host_flash_load / host_flash_save). */
enum { EV_KEYS, EV_BTN, EV_PRESS, EV_RELEASE, EV_TURN, EV_MIDI, EV_SHOT, EV_EXP_LED, EV_EXP_RMS, EV_EXP_PRESET,
       EV_EXP_SOUND, EV_EXP_STATE, EV_DUMP, EV_USBREC, EV_USBLINK, EV_VKEY, EV_TRS,
       EV_VEL, EV_XKEY, EV_PAD, EV_FADER, EV_WHEEL, EV_PEDAL, EV_STALL, EV_RLOAD, EV_SHED };
static void host_shed(void);                        /* frodhes.c: the overload's voice shed ("T shed") */
static int host_preset_check(char *msg, unsigned n);   /* frodhes.c: 0 if the panel is the shown preset */
static int host_sound_check(char *msg, unsigned n);    /* frodhes.c: 0 if the engines run what the panel says */
static int host_state(const char *key, char *val, unsigned n);   /* frodhes.c: 0 if K is known */
static void host_dump(void);                           /* frodhes.c */
typedef struct { uint32_t t, keys; int btn; uint8_t st, d1, d2; uint8_t kind; int32_t a, b; char op; char key[24], val[40]; } host_ev_t;
static const host_ev_t SCRIPT0[] = {
    {200, 1u << 7, -1, 0, 0, 0, EV_KEYS, 0, 0, 0},                                /* C4 */
    {700, 1u << 7 | 1u << 11 | 1u << 14, -1, 0, 0, 0, EV_KEYS, 0, 0, 0},          /* + E4 G4: a chord */
    {1500, 0, -1, 0, 0, 0, EV_KEYS, 0, 0, 0},
    {1700, 0, -1, 0x90, 81, 120, EV_MIDI, 0, 0, 0},                               /* MIDI in: A5 */
    {2300, 0, -1, 0x80, 81, 0, EV_MIDI, 0, 0, 0},
    {2500, 0, BTN_OCT_UP, 0, 0, 0, EV_BTN, 0, 0, 0},
    {2700, 1u << 2 | 1u << 9, -1, 0, 0, 0, EV_KEYS, 0, 0, 0},                     /* G4 + D5, an octave up */
    {3400, 0, -1, 0, 0, 0, EV_KEYS, 0, 0, 0},
};
static const host_ev_t *script = SCRIPT0;
static uint32_t script_n = sizeof SCRIPT0 / sizeof SCRIPT0[0], end_ms = 6500, frame_fps;
static const uint32_t SHOT[] = {1000, 3000};

#if BOARD_SMK37
/* The scripts name the FM-1's buttons, encoders and LEDs by its ids (docs/API.md); the SMK-37 build runs them on the SMK
 * buttons that mean the same (platform.c's printed-name map), so every script also tests the SMK's wiring of the
 * same app. A line "T ids smk" switches the rest of a script to the SMK's own ids (KNOB BANK 0, FADER BANK 1, < 2,
 * > 3 ...; K1 = MASTER 0 .. K8 7; button LED = its button, key LEDs 32 + key). */
static const int8_t HOST_FM1_BTN[14] = {BTN_OCT_DOWN, BTN_OCT_UP, BTN_FX, BTN_SEL, BTN_ENV, BTN_LFO, BTN_EDIT,
                                        BTN_GLO, BTN_HOME, BTN_SAVE, BTN_ARP, BTN_SEQ, BTN_PLAY, BTN_REC};
static const int8_t HOST_FM1_ENC[7] = {ENC_SELECT, ENC_ALGORITHM, ENC_KNOB1, ENC_KNOB2, ENC_KNOB3, ENC_KNOB4, ENC_PRESETS};
static int host_btn_id(int id, int raw) { return raw || id < 0 || id >= 14 ? id : HOST_FM1_BTN[id]; }
static int host_enc_id(int id, int raw) { return raw || id < 0 || id >= 7 ? id : HOST_FM1_ENC[id]; }
static int host_led_id(int id, int raw) { return raw || id < 0 ? id : id < 14 ? HOST_FM1_BTN[id] : id < 41 ? (int)KEY_LED(id - 14) : id; }
#else
static int host_btn_id(int id, int raw) { (void)raw; return id; }
static int host_enc_id(int id, int raw) { (void)raw; return id; }
static int host_led_id(int id, int raw) { (void)raw; return id; }
#endif

static void read_script(const char *path)
{
    static host_ev_t ev[16384];
    char line[256], what[16];
    FILE *f = fopen(path, "r");
    uint32_t n = 0;
    int raw = 0;                                /* "T ids smk": the SMK's own ids from here on */
    if (!f) { perror(path); exit(1); }
    while (fgets(line, sizeof line, f) && n < 16384u) {
        host_ev_t e = {0, 0, -1, 0, 0, 0, EV_KEYS, 0, 0, 0};
        unsigned a = 0, b = 0, c = 0;
        char sub[16] = "", op[4] = "";
        double db = 0;
        if (line[0] == '#' || sscanf(line, "%u %15s", &e.t, what) != 2) continue;
        if (!strcmp(what, "end")) { end_ms = e.t; continue; }
        if (!strcmp(what, "ids")) { raw = sscanf(line, "%*u %*s %15s", sub) == 1 && !strcmp(sub, "smk"); continue; }
        if (!strcmp(what, "keys")) { e.kind = EV_KEYS; sscanf(line, "%*u %*s %i", (int *)&e.keys); }
        else if (!strcmp(what, "btn")) { e.kind = EV_BTN; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "press")) { e.kind = EV_PRESS; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "release")) { e.kind = EV_RELEASE; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "turn")) { e.kind = EV_TURN; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "trs")) { e.kind = EV_TRS; sscanf(line, "%*u %*s %i %u %u", (int *)&a, &b, &c); e.st = (uint8_t)a; e.d1 = (uint8_t)b; e.d2 = (uint8_t)c; }
        else if (!strcmp(what, "vel")) { e.kind = EV_VEL; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "xkey")) { e.kind = EV_XKEY; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "pad")) { e.kind = EV_PAD; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "fader")) { e.kind = EV_FADER; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "wheel")) { e.kind = EV_WHEEL; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "pedal")) { e.kind = EV_PEDAL; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "midi")) { e.kind = EV_MIDI; sscanf(line, "%*u %*s %i %u %u", (int *)&a, &b, &c); e.st = (uint8_t)a; e.d1 = (uint8_t)b; e.d2 = (uint8_t)c; }
        else if (!strcmp(what, "stall")) { e.kind = EV_STALL; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "renderload")) { e.kind = EV_RLOAD; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "shed")) e.kind = EV_SHED;
        else if (!strcmp(what, "shot")) e.kind = EV_SHOT;
        else if (!strcmp(what, "dump")) e.kind = EV_DUMP;
        else if (!strcmp(what, "usblink")) { e.kind = EV_USBLINK; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "usbrec")) { e.kind = EV_USBREC; sscanf(line, "%*u %*s %d", &e.a); }
        else if (!strcmp(what, "vkey")) { e.kind = EV_VKEY; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "expect") && sscanf(line, "%*u %*s %15s", sub) == 1) {
            if (!strcmp(sub, "preset")) e.kind = EV_EXP_PRESET;
            else if (!strcmp(sub, "sound")) e.kind = EV_EXP_SOUND;
            else if (!strcmp(sub, "state")) {
                char *u;
                if (sscanf(line, "%*u %*s %*s %23s %39s", e.key, e.val) != 2) continue;
                for (u = e.val; *u; u++) if (*u == '_') *u = ' ';
                e.kind = EV_EXP_STATE;
            }
            else if (!strcmp(sub, "led")) { e.kind = EV_EXP_LED; sscanf(line, "%*u %*s %*s %d %d", &e.a, &e.b); }
            else if (!strcmp(sub, "rms") && sscanf(line, "%*u %*s %*s %3s %lf", op, &db) == 2) { e.kind = EV_EXP_RMS; e.op = op[0]; e.a = (int32_t)(db * 100.0); }
            else continue;
        }
        else continue;
        if (e.kind == EV_BTN || e.kind == EV_PRESS || e.kind == EV_RELEASE) e.btn = host_btn_id(e.btn, raw);
        else if (e.kind == EV_TURN) e.a = host_enc_id(e.a, raw);
        else if (e.kind == EV_EXP_LED) e.a = host_led_id(e.a, raw);
        ev[n++] = e;
    }
    fclose(f);
    script = ev;
    script_n = n;
}

static const char *host_out = "build/host/frodhes";
static int16_t *wav;
static uint32_t wav_n;

static void write_ppm_as(const char *name)
{
    FILE *f;
    uint32_t i;
    if (!(f = fopen(name, "wb"))) { perror(name); exit(1); }
    fprintf(f, "P6 240 240 255\n");
    for (i = 0; i < 240u * 240u; i++) {
        uint16_t c = fb[i];
        fputc((c >> 11) * 255 / 31, f);
        fputc((c >> 5 & 63) * 255 / 63, f);
        fputc((c & 31) * 255 / 31, f);
    }
    fclose(f);
}
static void write_ppm(uint32_t t)
{
    char name[512];
    snprintf(name, sizeof name, "%s-%u.ppm", host_out, t);
    write_ppm_as(name);
    printf("%6u ms  screen -> %s\n", t, name);
}

static void write_wav(void)
{
    char name[512];
    FILE *f;
    uint32_t bytes = wav_n * 4u, v[] = {16, 0x00020001u, FS, FS * 4u, 0x00100004u};
    snprintf(name, sizeof name, "%s.wav", host_out);
    if (!(f = fopen(name, "wb"))) { perror(name); exit(1); }
    fwrite("RIFF", 1, 4, f); bytes += 36; fwrite(&bytes, 4, 1, f); bytes -= 36;
    fwrite("WAVEfmt ", 1, 8, f); fwrite(v, 4, 5, f);
    fwrite("data", 1, 4, f); fwrite(&bytes, 4, 1, f);
    fwrite(wav, 4, wav_n, f);
    fclose(f);
    printf("sound  -> %s (%u frames)\n", name, wav_n);
}

static uint32_t host_t0;                        /* FRODHES_MS0: fm1_ms at the start (the script's times after it;
                                                 * the printed times are fm1_ms itself) */
static uint32_t host_stall, host_rload;         /* "T stall": milliseconds the next platform_poll call lasts longer;
                                                 * "T renderload": the ms each audio block adds */
static void host_ms(void);
static void platform_poll(void)                 /* one call = one millisecond (or a stall's) */
{
    host_ms();
    while (host_stall) { host_stall--; host_ms(); }
}
static void host_ms(void)
{
    static uint32_t step, shot, owed, frame;
    static int32_t blk[2u * HALF_FRAMES];
    fm1_ms++;
#if BOARD_SMK37
    fm1_in.frames++;                            /* (the SMK scans ~625 frames a second; enough for "settled") */
#endif
    while (step < script_n && script[step].t + host_t0 <= fm1_ms) {
        const host_ev_t *e = &script[step];
        switch (e->kind) {
        case EV_MIDI:
        case EV_TRS:
#if BOARD_SMK37
            if (e->kind == EV_TRS) { fprintf(stderr, "script: no TRS MIDI IN on the SMK-37, ignored\n"); break; }
#else
            if (e->kind == EV_TRS && !uart_midi_on) break;
#endif
            {
                static uint8_t clk_n[2];        /* (usb.c / midi_uart.c: the pulses counted, a dropped one too) */
                uint32_t sv = e->kind == EV_TRS, stamp;
                if (e->st == 0xF8u) clk_n[sv] = (uint8_t)(clk_n[sv] + 1u);   /* (wraps on purpose: 4 bits are stamped) */
                stamp = (fm1_ms & 0xFFFu) | (clk_n[sv] & 15u) << 12;
                if (mq_w - mq_r >= 64u) break;  /* (the ring is full: dropped, as usb.c / midi_uart.c do) */
                mq[mq_w % 64u][0] = e->st; mq[mq_w % 64u][1] = e->d1; mq[mq_w % 64u][2] = e->d2;
                if (e->st >= 0xF8u) { mq[mq_w % 64u][1] = (uint8_t)stamp; mq[mq_w % 64u][2] = (uint8_t)(stamp >> 8); }   /* (the arrival) */
            }
            mq[mq_w % 64u][3] = e->kind == EV_TRS ? 2u : 1u;
            mq_w++;
            break;
        case EV_STALL: host_stall += (uint32_t)(e->a > 0 ? e->a : 0); break;
        case EV_RLOAD: host_rload = (uint32_t)(e->a > 0 ? e->a : 0); break;
        case EV_SHED: host_shed(); break;
#if BOARD_SMK37
        case EV_VEL: { uint32_t k; for (k = 0; k < FM1_NKEY; k++) fm1_in.key_vel[k] = (uint8_t)(e->a < 1 ? 1 : e->a > 127 ? 127 : e->a); break; }
        case EV_XKEY: {
            uint32_t k = (uint32_t)e->a % FM1_NKEY, v = (uint32_t)e->b & 0x7Fu;
            if (v) { fm1_in.key_vel[k] = (uint8_t)v; fm1_in.keys |= (uint64_t)1u << k; }
            else fm1_in.keys &= ~((uint64_t)1u << k);
            if (k >= SMK_KEY_F3 && k < SMK_KEY_F3 + 27u) {     /* a middle key: a note key, as smk_input.h */
                if (v) fm1_in.notes |= 1u << (k - SMK_KEY_F3);
                else fm1_in.notes &= ~(1u << (k - SMK_KEY_F3));
            } else
                host_qput(1, (int32_t)(k << 8 | v));
            break;
        }
        case EV_PAD: host_pad((uint32_t)e->a, (uint32_t)e->b & 0x7Fu); break;
        case EV_FADER: fm1_in.fader[e->a & 3] = (uint8_t)(e->b < 0 ? 0 : e->b > 127 ? 127 : e->b); break;
        case EV_WHEEL: fm1_in.wheel[e->a & 1] = (uint16_t)(e->b < 0 ? 0 : e->b > 1023 ? 1023 : e->b); break;
        case EV_PEDAL: fm1_in.pedal = (uint16_t)(e->a < 0 ? 0 : e->a > 1023 ? 1023 : e->a); break;
#else
        case EV_VEL: case EV_XKEY: case EV_PAD: case EV_FADER: case EV_WHEEL: case EV_PEDAL:
            fprintf(stderr, "script: an SMK-37 line in an FM-1 build (BOARD_SMK37=0), ignored\n");
            break;
#endif
        case EV_BTN: host_edges |= 1u << e->btn; host_released |= 1u << e->btn; break;
        case EV_PRESS: host_edges |= 1u << e->btn; fm1_in.buttons |= 1u << e->btn; break;
        case EV_RELEASE: host_released |= 1u << e->btn; fm1_in.buttons &= ~(1u << e->btn); break;
        case EV_TURN: if ((uint32_t)e->a < 8u) host_enc[e->a] += e->b; break;
        case EV_SHOT: write_ppm(fm1_ms); break;
        case EV_EXP_PRESET: {
            char msg[96];
            if (host_preset_check(msg, sizeof msg)) { printf("%6u ms  FAIL expect preset: %s\n", fm1_ms, msg); host_fails++; }
            else printf("%6u ms  ok   expect preset: %s\n", fm1_ms, msg);
            break;
        }
        case EV_EXP_SOUND: {
            char msg[128];
            if (host_sound_check(msg, sizeof msg)) { printf("%6u ms  FAIL expect sound: %s\n", fm1_ms, msg); host_fails++; }
            else printf("%6u ms  ok   expect sound\n", fm1_ms);
            break;
        }
        case EV_EXP_STATE: {
            char v[64];
            const char *want = e->val;
#if BOARD_SMK37
            if (!strcmp(e->key, "hint")) want = host_xlat(want);   /* (the hints name the SMK's buttons) */
#endif
            if (host_state(e->key, v, sizeof v)) { printf("%6u ms  FAIL expect state %s: no such state\n", fm1_ms, e->key); host_fails++; }
            else if (strcmp(v, want)) { printf("%6u ms  FAIL expect state %s = \"%s\" (is \"%s\")\n", fm1_ms, e->key, want, v); host_fails++; }
            else printf("%6u ms  ok   expect state %s = \"%s\"\n", fm1_ms, e->key, v);
            break;
        }
        case EV_DUMP: host_dump(); break;
        case EV_USBREC: host_usb_rec = e->a != 0; break;
        case EV_USBLINK: host_usb_link = e->a != 0; break;
        case EV_VKEY: kv_host_vkey((uint32_t)e->a, (uint32_t)e->b); break;
        case EV_EXP_LED:
            if ((uint32_t)e->a >= 64u || host_led[e->a] != (uint8_t)e->b) {
                printf("%6u ms  FAIL expect led %d = %d (is %d)\n", fm1_ms, e->a, e->b, (uint32_t)e->a < 64u ? host_led[e->a] : -1);
                host_fails++;
            } else
                printf("%6u ms  ok   expect led %d = %d\n", fm1_ms, e->a, e->b);
            break;
        case EV_EXP_RMS: {
            uint32_t n = FS / 20u, i0 = wav_n > n ? wav_n - n : 0, i;
            double acc = 0, db;
            for (i = i0; i < wav_n; i++) acc += (double)wav[2u * i] * wav[2u * i];
            db = 10.0 * log10(acc / (double)(wav_n - i0 ? wav_n - i0 : 1) / (32767.0 * 32767.0) + 1e-20);
            if ((e->op == '<' && !(db < e->a / 100.0)) || (e->op == '>' && !(db > e->a / 100.0))) {
                printf("%6u ms  FAIL expect rms %c %.1f dB (is %.1f)\n", fm1_ms, e->op, e->a / 100.0, db);
                host_fails++;
            } else
                printf("%6u ms  ok   expect rms %c %.1f dB (%.1f)\n", fm1_ms, e->op, e->a / 100.0, db);
            break;
        }
        default: fm1_in.notes = e->keys; break;
        }
        step++;
    }
    owed += FS;                                 /* the frames this millisecond is worth, in blocks as the ISR renders */
    while (owed >= 1000u * HALF_FRAMES) {
        uint32_t i;
        owed -= 1000u * HALF_FRAMES;
#if APP_OUT_WRITTEN
        for (i = 0; i < 2u * HALF_FRAMES; i++)  /* garbage: the app must write every sample (as on the FM-1) */
            blk[i] = (int32_t)(0x5A5A5A5Au ^ (i * 2654435761u));
#else
        memset(blk, 0, sizeof blk);
#endif
        app_audio(blk, HALF_FRAMES);
        host_stall += host_rload;
        wav = realloc(wav, (wav_n + HALF_FRAMES) * 4u);
        for (i = 0; i < 2u * HALF_FRAMES; i++) {
            int32_t v = blk[i];                 /* (USB audio at the MASTER knob all the way up) */
            wav[2u * wav_n + i] = (int16_t)(v > 32767 ? 32767 : v < -32767 ? -32767 : v);
        }
        wav_n += HALF_FRAMES;
    }
    if (frame_fps) {                            /* a film: a frame every 1/fps s, as the screen is then */
        if ((uint64_t)frame * 1000u <= (uint64_t)fm1_ms * frame_fps) {
            char name[512];
            snprintf(name, sizeof name, "%s-%05u.ppm", host_out, frame++);
            write_ppm_as(name);
        }
    } else if (shot < sizeof SHOT / sizeof SHOT[0] && fm1_ms == SHOT[shot])
        write_ppm(SHOT[shot++]);
    if (fm1_ms - host_t0 >= end_ms) {
        write_wav();
        if (getenv("FRODHES_FLASH")) host_flash_save(getenv("FRODHES_FLASH"));
        eng_host_report();
        if (host_fl_calls) printf("flash  %u erase / write calls, each checked against the allow-list and the store\n", host_fl_calls);
        if (host_fails) printf("%u expect(s) FAILED\n", host_fails);
        exit(host_fails ? 1 : 0);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1) host_out = argv[1];
    if (argc > 2) read_script(argv[2]);
    if (argc > 3) frame_fps = (uint32_t)atoi(argv[3]);
#if BOARD_SMK37
    memset(fm1_in.key_vel, 43, sizeof fm1_in.key_vel);    /* (a medium press until a "vel" line: KEY VEL at TOUCH
                                                           * MEDIUM, frodhes_smk.h smk_vel; the FM-1 scripts play as there) */
#endif
    if (getenv("FRODHES_FLASH")) host_flash_load(getenv("FRODHES_FLASH"));
    if (getenv("FRODHES_MS0")) fm1_ms = host_t0 = (uint32_t)strtoul(getenv("FRODHES_MS0"), 0, 0);   /* (e.g. near the
                                                           * Q8 wrap at 2^24 ms: 4.66 hours up) */
    app_main();
    return 0;
}
