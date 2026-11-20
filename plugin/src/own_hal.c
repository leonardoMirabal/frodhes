/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin's own hardware layer (FR_OWN_HAL, the plugin's own-code build): what firmware/src/frodhes.c takes from
 * its platform, as the desktop plugin needs it. frodhes.c includes this file in place of platform.c (and the browser
 * layer behind it) when FR_OWN_HAL is set; every other build keeps its own platform file.
 *
 * Written from the contract, that is from what the app and plugin/src/slot_bridge.h call:
 *   names      the panel buttons and encoders, KEY_LED, BOARD_SMK37
 *   panel      fm1_in, fm1_input_edges, fm1_input_note_edges, fm1_enc_take, the LED calls, uart_midi_on,
 *              usb_pre_master: nothing is ever pressed or turned here, so the inputs read idle and the LEDs go nowhere
 *   clock      fm1_ms, platform_poll (the slot's own ms clock, then the coroutine yield)
 *   MIDI       a 64-message ring, each message with its place in the block (1/256 frame); midi_get, midi_send
 *   storage    the flash image kept in RAM (host_mem, st_read), the settings record (settings_*)
 *   screen     the canvas and text calls ui.c draws through, as no-ops (own_gfx.h)
 * The slot is silent about the rest: no USB, no audio hardware, no flash timing. */
#ifndef FRODHES_PLUGIN_OWN_HAL_C
#define FRODHES_PLUGIN_OWN_HAL_C

#include <stdint.h>
#include <string.h>

#ifndef BOARD_SMK37
#define BOARD_SMK37 0                           /* the FM-1's panel (the other board's has other ids) */
#endif
#if BOARD_SMK37
#error "own_hal.c is the FM-1 layer"
#endif
#ifndef APP_NAME
#define APP_NAME "DINGHY"
#endif
#ifndef APP_VER
#define APP_VER "web"
#endif
#define FS 44100u                               /* the sample rate */
#ifndef HALF_FRAMES                             /* frames per audio call; slot_bridge.h renders in blocks of it */
#define HALF_FRAMES 256u
#endif

/* ---- the panel's names: the ids the app uses for buttons (bit in a mask, LED id) and encoders */
enum { BTN_OCT_DOWN = 0, BTN_OCT_UP = 1, BTN_FX = 2, BTN_SEL = 3, BTN_ENV = 4, BTN_LFO = 5, BTN_EDIT = 6,
       BTN_GLO = 7, BTN_HOME = 8, BTN_SAVE = 9, BTN_ARP = 10, BTN_SEQ = 11, BTN_PLAY = 12, BTN_REC = 13 };
enum { ENC_SELECT = 0, ENC_ALGORITHM = 1, ENC_KNOB1 = 2, ENC_KNOB2 = 3, ENC_KNOB3 = 4, ENC_KNOB4 = 5, ENC_PRESETS = 6 };
#define KEY_LED(k) (14u + (k))                  /* the 27 note keys' LEDs follow the 14 buttons' */
_Static_assert(BTN_OCT_DOWN == 0 && BTN_OCT_UP == 1 && BTN_FX == 2 && BTN_SEL == 3 && BTN_ENV == 4 && BTN_LFO == 5 &&
               BTN_EDIT == 6 && BTN_GLO == 7 && BTN_HOME == 8 && BTN_SAVE == 9 && BTN_ARP == 10 && BTN_SEQ == 11 &&
               BTN_PLAY == 12 && BTN_REC == 13, "own_hal: the buttons' ids (bits of fm1_in.buttons, LED ids)");
_Static_assert(ENC_SELECT == 0 && ENC_ALGORITHM == 1 && ENC_KNOB1 == 2 && ENC_KNOB2 == 3 && ENC_KNOB3 == 4 &&
               ENC_KNOB4 == 5 && ENC_PRESETS == 6, "own_hal: the encoders' ids");
_Static_assert(BTN_REC < 32 && ENC_PRESETS < 8, "own_hal: buttons fit a 32-bit mask, encoders the 8 the panel has");
_Static_assert(KEY_LED(0) == BTN_REC + 1 && KEY_LED(26) == 40u, "own_hal: the key LEDs follow the buttons' (27 keys)");

/* ---- the panel: idle. The app polls these every pass; none of it ever changes in a plugin slot */
static volatile uint32_t fm1_ms;                /* milliseconds, set by platform_poll */
static struct { uint32_t notes, buttons; } fm1_in;
static uint32_t fm1_input_edges(uint32_t *released) { *released = 0u; return 0u; }
static uint32_t fm1_input_note_edges(void) { return 0u; }
static int32_t fm1_enc_take(uint32_t e) { (void)e; return 0; }
static void fm1_led_key(uint32_t id, int on) { (void)id; (void)on; }
static void fm1_led_key_glow(uint32_t id, int on) { (void)id; (void)on; }
static void fm1_led_group_ids(uint32_t id0, uint32_t id1) { (void)id0; (void)id1; }
static void fm1_led_build(void) {}
static uint8_t fm1_led_duty[2] = {16u, 16u};    /* the lit duty of the keys and the buttons, /16 (DISPLAY BRIGHTNESS) */
static volatile uint8_t uart_midi_on = 1;       /* the TRS input's switch: stored by the MIDI page, nothing reads it */
static volatile uint8_t usb_pre_master;         /* USB audio before MASTER: there is no USB audio */

/* ---- the screen: the canvas and text calls ui.c draws through (plugin/src/own_gfx.h), as no-ops. Nothing shows a frame,
 * so nothing is kept */
static void lcd_sync(void) {}
static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint16_t c) { (void)x; (void)y; (void)w; (void)h; (void)c; }
static void lcd_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *px) { (void)x; (void)y; (void)w; (void)h; (void)px; }
#include "own_gfx.h"

/* ---- the flash image and the settings record */
#define HOST_SAVE_MS 100u                       /* the pattern's modelled erase time (seq.c) */
#define OWN_FLASH_BYTES 0x100000u               /* 1 MiB: every address the app's layout uses (frodhes_store.h) */
static uint8_t host_mem[OWN_FLASH_BYTES];       /* erased = 0xFF; seq.c and presets.c erase and write it directly */
static uint8_t flash_ok = 1;
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off >= OWN_FLASH_BYTES || n > OWN_FLASH_BYTES - off) return -1;
    memcpy(dst, host_mem + off, n);
    return 0;
}

/* the settings record: the 256-byte settings, then the user presets (st_tail: ST_TAIL_N bytes, set by the app before
 * the first load). A "save" keeps it here for the session; the project keeps the real copy (slot_bridge.h state) */
static uint8_t *st_tail;
static struct { uint8_t saved; uint8_t settings[256]; uint8_t tail[ST_TAIL_N]; } own_rec;
static void own_rec_clear(void) { memset(&own_rec, 0, sizeof own_rec); }   /* (a slot's boot: nothing saved yet) */
static int settings_load(void *p, uint32_t n)
{
    if (!own_rec.saved || n != sizeof own_rec.settings) return -1;
    memcpy(p, own_rec.settings, n);
    memcpy(st_tail, own_rec.tail, ST_TAIL_N);
    return 0;
}
static int settings_save(const void *p, uint32_t n)
{
    if (n != sizeof own_rec.settings) return -1;
    memcpy(own_rec.settings, p, n);
    memcpy(own_rec.tail, st_tail, ST_TAIL_N);
    own_rec.saved = 1;
    return 0;
}
static int settings_save_start(const void *p, uint32_t n) { return settings_save(p, n); }   /* done at once: 0 */
static int settings_save_poll(void) { return 0; }
static __attribute__((unused)) int settings_check(void) { return 0; }
/* what the boot's migration asks about the places older versions used (frodhes.c st_old_scan): the image has no such
 * record, so there is nothing to take in and every id is 0 */
static __attribute__((unused)) int settings_load_old(void *p, uint32_t n) { (void)p; (void)n; return -1; }
static __attribute__((unused)) int smk_old_ours(void) { return 1; }
static __attribute__((unused)) uint32_t set_at, set_seq;   /* (read only for the re-take: never reached here) */
static __attribute__((unused)) uint32_t settings_old_id(void) { return 0u; }
static __attribute__((unused)) uint32_t st_id(uint32_t crc, uint32_t seq)   /* a record's id: never 0 */
{
    uint32_t v = crc ^ (seq << 13 | seq >> 19) ^ (seq * 0x2545F491u);
    return v ? v : 1u;
}
#define OWN_STORE_RESET() own_rec_clear()       /* what slot_bridge.h calls at a slot's boot */

/* ---- MIDI in: a ring of 64 messages (status, 2 data bytes), each with its place in the block it lands in, in 1/256
 * of a frame (slot_bridge.h sets web_ofs_next before web_midi; midi_get hands it on in fr_ev_ofs, 0 once the ring is
 * empty: the events the main loop posts itself, the arpeggiator's, the sequencer's, start at the block's start) */
#define OWN_RING 64u
static uint8_t mq[OWN_RING][3];
static uint16_t mq_ofs[OWN_RING];
static uint32_t mq_w, mq_r;                     /* counters, never wrapped back: the fill is mq_w - mq_r */
static uint16_t web_ofs_next, fr_ev_ofs;
static void web_midi(uint32_t st, uint32_t d1, uint32_t d2)
{
    uint8_t *m;
    if (mq_w - mq_r >= OWN_RING) return;        /* full: dropped (slot_bridge.h waits for room instead) */
    m = mq[mq_w & (OWN_RING - 1u)];
    m[0] = (uint8_t)st; m[1] = (uint8_t)d1; m[2] = (uint8_t)d2;
    mq_ofs[mq_w & (OWN_RING - 1u)] = web_ofs_next;
    if (st >= 0xF8u) {                          /* real time: tempo.c wants its arrival (ms mod 4096) and the pulse count
                                                 * (mod 16) in the data bytes, as the USB stack stamps them */
        static uint8_t pulses;
        uint32_t stamp;
        if (st == 0xF8u) pulses++;
        stamp = (fm1_ms & 0xFFFu) | (uint32_t)(pulses & 15u) << 12;
        m[1] = (uint8_t)stamp; m[2] = (uint8_t)(stamp >> 8);
    }
    mq_w++;
}
static int midi_get(uint8_t *st, uint8_t *d1, uint8_t *d2)
{
    const uint8_t *m;
    if (mq_r == mq_w) { fr_ev_ofs = 0u; return 0; }
    fr_ev_ofs = mq_ofs[mq_r & (OWN_RING - 1u)];
    m = mq[mq_r & (OWN_RING - 1u)];
    *st = m[0]; *d1 = m[1]; *d2 = m[2];
    mq_r++;
    return 1;                                   /* (the source: 1 = USB) */
}
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2) { (void)st; (void)d1; (void)d2; }   /* no MIDI out */
_Static_assert((OWN_RING & (OWN_RING - 1u)) == 0u && OWN_RING == 64u, "own_hal: the ring is 64 messages, a power of two");

/* ---- the app's two entry points, and the main loop's turn */
static void app_audio(int32_t *out, uint32_t n);
static void app_main(void);
static double move_now_ms(void);                /* slot_bridge.h: the slot's millisecond clock */
static void move_yield(void);                   /* slot_bridge.h: back to the audio thread until the next step */
static void platform_poll(void)
{
    static double origin;
    static int started;
    double now = move_now_ms();
    if (!started) { started = 1; origin = now; }
    fm1_ms = (uint32_t)(now - origin);          /* the first poll reads 0, then ms + 1 (as the host build's clock) */
    move_yield();
}

#endif
