/* SPDX-License-Identifier: GPL-3.0-only */
/* A fake FM-1 on a computer: the app (firmware/src/dinghy.c) runs unchanged on it. platform.c includes
 * this file instead of the hardware when DINGHY_HOST is set. A script presses keys over time; out come
 * the screen (PPM, at the times SHOT asks) and the sound (WAV, as the FM-1 sends it over USB).
 *
 *   mkdir -p build/host
 *   cc -O2 -Wall -DDINGHY_HOST -Ifirmware/src -Ifirmware/gen -o build/host/dinghy firmware/src/dinghy.c
 *   build/host/dinghy build/host/dinghy        (-> build/host/dinghy.wav, build/host/dinghy-<ms>.ppm)
 *   build/host/dinghy OUT script.txt [FPS]    (your own script; with FPS: OUT-NNNNN.ppm, a frame every 1/FPS s:
 *                                             a film of your firmware, e.g. ffmpeg -r 30 -i OUT-%05d.ppm ...)
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
static struct { uint32_t notes, buttons; } fm1_in;
static uint32_t host_edges, host_released;
static int32_t host_enc[8];
static uint8_t host_led[64];                    /* the last state each LED id was set to */
static uint32_t fm1_input_edges(uint32_t *released) { uint32_t b = host_edges; host_edges = 0; *released = host_released; host_released = 0; return b; }
static uint32_t fm1_input_note_edges(void) { return 0; }   /* (the script sets fm1_in.notes) */
static int32_t fm1_enc_take(uint32_t e) { int32_t d = e < 8u ? host_enc[e] : 0; if (e < 8u) host_enc[e] = 0; return d; }
static void fm1_led_key(uint32_t id, int on) { if (id < 64u) host_led[id] = (uint8_t)(on != 0); }
static volatile uint8_t uart_midi_on = 1;        /* midi_uart.c: the TRS input (none here) */
static volatile uint8_t usb_pre_master;          /* platform.c: USB audio before MASTER (no USB here) */
static uint8_t fm1_led_duty[2] = {16u, 16u};   /* hal/fm1_input.h: the lit duty of the keys and the buttons, /16 (DISPLAY BRIGHTNESS) */
static void fm1_led_group_ids(uint32_t id0, uint32_t id1) { (void)id0; (void)id1; }   /* (which LEDs are the keys: the hardware's) */
static void fm1_led_build(void) {}               /* (the scan ISR's tables: the hardware's) */
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

static uint8_t host_usb_rec;                    /* "T usbrec 1": a computer reads the USB audio (frodhes.c usb_recording) */
static uint8_t host_store[256 + 4];             /* the settings record: [0] = 1 when saved, the bytes from [4] */
static uint32_t host_store_len = 256;           /* its length (platform.c: a record of another size is "nothing") */
static int settings_load(void *p, uint32_t n)
{
    if (host_store[0] != 1 || n > 256u || n != host_store_len) return -1;
    memcpy(p, host_store + 4, n);
    return 0;
}
static int settings_save(const void *p, uint32_t n)
{
    host_store[0] = 1;
    host_store_len = n;
    memcpy(host_store + 4, p, n);
    printf("%6u ms  settings saved (%u B)\n", fm1_ms, n);
    return 0;
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

static uint8_t mq[64][3];
static uint32_t mq_w, mq_r;
static int midi_get(uint8_t *st, uint8_t *d1, uint8_t *d2)
{
    if (mq_r == mq_w) return 0;
    *st = mq[mq_r % 64u][0]; *d1 = mq[mq_r % 64u][1]; *d2 = mq[mq_r % 64u][2];
    mq_r++;
    return 1;
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
 *   T shot               a screenshot (OUT-T.ppm)
 *   T expect led ID V    fails the run unless LED ID is V (0/1)
 *   T expect rms OP DB   fails unless the RMS of the last 50 ms is OP (< or >) DB dBFS
 *   T expect preset      fails unless every parameter equals what the preset the header shows sets (frodhes.c), a
 *                        factory or a user preset
 *   T expect sound       fails unless what the engines and effects run with is what the panel says (frodhes.c)
 *   T expect state K V   fails unless the state K (frodhes.c host_state: header, page, card0..3, oct, ...) reads V
 *                        (V: one word, '_' for a space)
 *   T usbrec V           a computer reads the USB audio (1) or not (0): the automatic flash saves wait (frodhes.c)
 *   T dump               prints the state, one "@ T K V" line each (every page's cards, the parameters, LEDs ...)
 *   T end                stop; the exit code is the number of failed expects
 * With FRODHES_FLASH=FILE the flash is kept in FILE across runs (host_flash_load / host_flash_save). */
enum { EV_KEYS, EV_BTN, EV_PRESS, EV_RELEASE, EV_TURN, EV_MIDI, EV_SHOT, EV_EXP_LED, EV_EXP_RMS, EV_EXP_PRESET,
       EV_EXP_SOUND, EV_EXP_STATE, EV_DUMP, EV_USBREC };
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

static void read_script(const char *path)
{
    static host_ev_t ev[4096];
    char line[256], what[16];
    FILE *f = fopen(path, "r");
    uint32_t n = 0;
    if (!f) { perror(path); exit(1); }
    while (fgets(line, sizeof line, f) && n < 4096u) {
        host_ev_t e = {0, 0, -1, 0, 0, 0, EV_KEYS, 0, 0, 0};
        unsigned a = 0, b = 0, c = 0;
        char sub[16] = "", op[4] = "";
        double db = 0;
        if (line[0] == '#' || sscanf(line, "%u %15s", &e.t, what) != 2) continue;
        if (!strcmp(what, "end")) { end_ms = e.t; continue; }
        if (!strcmp(what, "keys")) { e.kind = EV_KEYS; sscanf(line, "%*u %*s %i", (int *)&e.keys); }
        else if (!strcmp(what, "btn")) { e.kind = EV_BTN; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "press")) { e.kind = EV_PRESS; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "release")) { e.kind = EV_RELEASE; sscanf(line, "%*u %*s %d", &e.btn); }
        else if (!strcmp(what, "turn")) { e.kind = EV_TURN; sscanf(line, "%*u %*s %d %d", &e.a, &e.b); }
        else if (!strcmp(what, "midi")) { e.kind = EV_MIDI; sscanf(line, "%*u %*s %i %u %u", (int *)&a, &b, &c); e.st = (uint8_t)a; e.d1 = (uint8_t)b; e.d2 = (uint8_t)c; }
        else if (!strcmp(what, "shot")) e.kind = EV_SHOT;
        else if (!strcmp(what, "dump")) e.kind = EV_DUMP;
        else if (!strcmp(what, "usbrec")) { e.kind = EV_USBREC; sscanf(line, "%*u %*s %d", &e.a); }
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
        ev[n++] = e;
    }
    fclose(f);
    script = ev;
    script_n = n;
}

static const char *host_out = "build/host/dinghy";
static uint32_t host_fails;
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

static void platform_poll(void)                 /* one call = one millisecond */
{
    static uint32_t step, shot, owed, frame;
    static int32_t blk[2u * HALF_FRAMES];
    fm1_ms++;
    while (step < script_n && script[step].t <= fm1_ms) {
        const host_ev_t *e = &script[step];
        switch (e->kind) {
        case EV_MIDI:
            mq[mq_w % 64u][0] = e->st; mq[mq_w % 64u][1] = e->d1; mq[mq_w % 64u][2] = e->d2;
            mq_w++;
            break;
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
            if (host_state(e->key, v, sizeof v)) { printf("%6u ms  FAIL expect state %s: no such state\n", fm1_ms, e->key); host_fails++; }
            else if (strcmp(v, e->val)) { printf("%6u ms  FAIL expect state %s = \"%s\" (is \"%s\")\n", fm1_ms, e->key, e->val, v); host_fails++; }
            else printf("%6u ms  ok   expect state %s = \"%s\"\n", fm1_ms, e->key, v);
            break;
        }
        case EV_DUMP: host_dump(); break;
        case EV_USBREC: host_usb_rec = e->a != 0; break;
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
        memset(blk, 0, sizeof blk);
        app_audio(blk, HALF_FRAMES);
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
    if (fm1_ms >= end_ms) {
        write_wav();
        if (getenv("FRODHES_FLASH")) host_flash_save(getenv("FRODHES_FLASH"));
        eng_host_report();
        if (host_fails) printf("%u expect(s) FAILED\n", host_fails);
        exit(host_fails ? 1 : 0);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1) host_out = argv[1];
    if (argc > 2) read_script(argv[2]);
    if (argc > 3) frame_fps = (uint32_t)atoi(argv[3]);
    if (getenv("FRODHES_FLASH")) host_flash_load(getenv("FRODHES_FLASH"));
    app_main();
    return 0;
}
