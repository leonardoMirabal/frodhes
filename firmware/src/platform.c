/* SPDX-License-Identifier: GPL-3.0-only */
/* Dinghy's platform: everything an FM-1 (or SMK-37) firmware needs before it can be fun, taken unchanged from
 * SLOOP / Felucca / sloopDX / OberVave and wired up here. You should not need to touch this file.
 *
 *   boot + boot guard   a firmware that crashes at boot falls back to the USB rescue, then to the ROM
 *   USB rescue          OCT- held at power-on: no audio, only the updater (the web installer still works)
 *   updater             the M-UPGRADE SysEx path: the web installer / the hub can always reach the FM-1
 *   MIDI                USB in and out, the FM-1's TRS jack in (midi_get: 1 USB, 2 TRS; midi_send); clock and
 *                       transport (F8 FA FB FC) reach the app too
 *   USB audio           the output as a class-compliant 16-bit stereo input, 44.1 or 48 kHz
 *   keys, knobs, LEDs   fm1_in.notes / buttons, fm1_input_edges, fm1_enc_take, fm1_led_key, fm1_led_key_glow
 *                       (hal/fm1_input.h; SMK-37: hal/smk_input.h, the same calls and velocity, pads, faders, wheels)
 *   screen              240 x 240, lcd_fill, draw_text_box with FONT_S (16 px) / FONT_L (32 px) (gfx.c); LCD_IDLE
 *   storage             settings, NBLOB blobs of one sector, NSLOT slots of SLOT_SECT sectors, each with a CRC (store.h)
 *   crash screen        the PC of a fault, then a reboot
 *
 * The app (dinghy.c) includes this file first and supplies:
 *   static void app_audio(int32_t *out, uint32_t n)   audio ISR: n stereo frames, L R L R, Q15 (+-32767), out is zero
 *   static void app_main(void)                        never returns; calls platform_poll() often (every few ms)
 * and may define, before the #include:
 *   LCD_IDLE()        called while the screen's DMA sends a strip (the keys, MIDI: nothing waits for a redraw)
 *   FELUCCA_UAC 0     no USB audio (MIDI only)
 */
/* Frodhes: and APP_OUT_WRITTEN 1: app_audio writes every sample of out (then out is not cleared before it) */
/* the panel by name: buttons (fm1_in.buttons, fm1_input_edges, fm1_led_key: bit / id), the encoders
 * (fm1_enc_take); the 27 note keys are fm1_in.notes bit 0 (F3) .. 26 (G5), LED id 14 + key */
#ifndef BOARD_SMK37
#define BOARD_SMK37 0                           /* 1: the M-Vave SMK-37 (tools/build.py --board smk37; hal/fm1_cc.h) */
#endif
#if BOARD_SMK37
/* the M-Vave SMK-37 (docs/SMK37.md): the FM-1's buttons on the SMK buttons that mean the same (the fm1-to-smk37 kit's
 * printed-name map: PATCH = HOME, PARA = EDIT, CHORD = ENV, NOTE REPEAT = LFO, SCALE = SEL, GLOBE = GLO, BT = SAVE),
 * the four extras; K1 is the MASTER pot (hal/smk_input.h), K2 SELECT, K3 ALGORITHM, K4 PRESETS, K5..K8 KNOB 1..4.
 * The note keys have no LEDs here (the pads have colours: padled_set): KEY_LED ids are past the button LEDs and do nothing */
enum { BTN_KNOB_BANK = 0, BTN_FADER_BANK = 1, BTN_LEFT = 2, BTN_RIGHT = 3, BTN_OCT_DOWN = 4, BTN_OCT_UP = 5,
       BTN_ARP = 6, BTN_LFO = 7, BTN_SEL = 8, BTN_ENV = 9, BTN_GLO = 10, BTN_SAVE = 11, BTN_HOME = 12, BTN_EDIT = 13,
       BTN_FX = 14, BTN_SEQ = 15, BTN_PLAY = 16, BTN_REC = 17 };
enum { ENC_MASTER = 0, ENC_SELECT = 1, ENC_ALGORITHM = 2, ENC_PRESETS = 3, ENC_KNOB1 = 4, ENC_KNOB2 = 5, ENC_KNOB3 = 6,
       ENC_KNOB4 = 7 };
#define KEY_LED(k) (32u + (k))
#else
enum { BTN_OCT_DOWN = 0, BTN_OCT_UP = 1, BTN_FX = 2, BTN_SEL = 3, BTN_ENV = 4, BTN_LFO = 5, BTN_EDIT = 6,
       BTN_GLO = 7, BTN_HOME = 8, BTN_SAVE = 9, BTN_ARP = 10, BTN_SEQ = 11, BTN_PLAY = 12, BTN_REC = 13 };
enum { ENC_SELECT = 0, ENC_ALGORITHM = 1, ENC_KNOB1 = 2, ENC_KNOB2 = 3, ENC_KNOB3 = 4, ENC_KNOB4 = 5, ENC_PRESETS = 6 };
#define KEY_LED(k) (14u + (k))
#endif

#ifdef DINGHY_HOST
#ifdef DINGHY_WEB
#include "../../web/web_hal.c"                 /* in a web browser (Emscripten): web/web_hal.c */
#else
#include "../../tests/host.c"                  /* on a computer: a fake FM-1 (tests/host.c) */
#endif
#else
#include <stdint.h>
#include "fm1_time.h"
#include "fm1_sys.h"
#include "fm1_irq.h"
#include "fm1_guard.h"
#if BOARD_SMK37
#include "smk_input.h"                          /* keys with velocity, buttons, 8 encoders, LEDs, pads, faders, wheels */
static void fm1_led_key_glow(uint32_t id, int on) { (void)id; (void)on; }   /* (no key LEDs: the pads glow) */
#else
#define FM1_SETTLE_US 100u                      /* the boot scan (OCT- for the rescue): 10 us read no key on an FM-1, the tick waits 100 too */
#include "fm1_input.h"
static void fm1_led_key_glow(uint32_t id, int on)   /* as fm1_led_key, the dim layer (~1/24): "there is something here" */
{
    uint32_t p, r;
    for (p = 0; p < FM1_NCOL; p++)
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][p] == (int8_t)id) {
                if (on) fm1_led_dim[p] |= (uint8_t)(1u << r);
                else fm1_led_dim[p] &= (uint8_t)~(1u << r);
            }
}
#endif
#include "fm1_timer.h"
#include "fm1_lcd_hw.h"
#include "fm1_audio.h"
#include "fm1_keep.h"                           /* (Frodhes: HAL_KEEP_REG, the audio ISR's tail) */
#if !BOARD_SMK37
#include "fm1_adc.h"                            /* (SMK: smk_input.h stands in, MASTER = K1) */
#endif
#include "bootguard.h"

#ifndef APP_NAME
#define APP_NAME "DINGHY"
#endif
#ifndef FELUCCA_ID
#if BOARD_SMK37
#define FELUCCA_ID SMK_MODEL "_990"             /* SMK-37 Pro_99N (Elite, MKE-P37: SMK_MODEL), tools/build.py */
#else
#define FELUCCA_ID "FM-1_990"                   /* package identity: 99N = Dinghy build N (tools/build.py) */
#endif
#endif
#ifndef APP_VER
#define APP_VER "0.0"
#endif

bootguard_t bootguard __attribute__((section(".noinit")));
static volatile uint32_t fm1_ms;                /* milliseconds since power-on (TIMER5) */

#define RING_PUBLISH() __asm__ volatile("" ::: "memory")
#include "libc.c"
#include "lcd.c"
#ifndef CV_MAX
#define CV_MAX (240u * 64u)                     /* the biggest text box drawn at once (canvas in RAM: 2 B a pixel) */
#endif
#include "gfx.c"

#define FS 44100u                               /* the sample rate */
#define HALF_FRAMES 256u                        /* I2S half buffer: 5.8 ms (as sloopDX: the USB audio band is set for it) */
#define HALF_WORDS (HALF_FRAMES * 2u)
#define FELUCCA_OTA 1
#define FELUCCA_CDC 0
#ifndef FELUCCA_UAC
#define FELUCCA_UAC 1                           /* USB audio to the computer (0: MIDI only, as zp12) */
#endif
#define FELUCCA_UART (!BOARD_SMK37)             /* the FM-1's TRS MIDI IN (SMK-37: none, PH8 is a key-matrix row) */
#if BOARD_SMK37 && FELUCCA_UART
#error "BOARD_SMK37: no UART MIDI IN (RX on PH8, a key-matrix row on the SMK)"
#endif
#define MIDI_RX_STAMP() fm1_ms                  /* (Frodhes: usb.c / midi_uart.c stamp the MIDI clock's arrival) */
#include "usb.c"
#if FELUCCA_UART
#include "midi_uart.c"                          /* (sloopDX's, after Felucca Salt: the ring read by content; cable 1) */
#endif
#if FELUCCA_UAC
#define UAC_ONLY(x) x
#else
#define UAC_ONLY(x) ((void)0)
#endif
#ifndef APP_OUT_SHIFT                          /* the app may drive the codec to full scale (8) */
#define APP_OUT_SHIFT 7u
#endif

/* ---- flash: read anywhere; erase / program only the update's staging area and the app's store (store.h; the app
 * may give its own, tighter allow-list: APP_FL_RANGE) */
#include "store.h"
#include "fm1_flash.h"
static uint8_t flash_ok;
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    uint8_t *d = dst;
    while (n) {
        uint32_t k = n > 256u ? 256u : n, f = irq_save();
        int rc = FL_FAR(fl_read_ram)(off, d, k);
        irq_restore(f);
        if (rc)
            return rc;
        off += k;
        d += k;
        n -= k;
    }
    return 0;
}

static uint8_t recovery_active;
#if BOARD_SMK37
#define OTA_IDENTITY (recovery_active ? SMK_MODEL "_000" : FELUCCA_ID)
#else
#define OTA_IDENTITY (recovery_active ? "FM-1_000" : FELUCCA_ID)
#endif
#include "ota.c"
static void recovery_poll(void);
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void ota_idle(void)
{
    fm1_wdt_feed();
    if (recovery_active) recovery_poll();
}
static int ota_in_area(uint32_t off, uint32_t n) { return FL_IN(off, n, OTA_AREA, OTA_AREA + OTA_AREA_LEN); }
static int ota_erase(uint32_t off)
{
    uint32_t took;
    if (!ota_in_area(off, 0x1000u) || (off & 0xFFFu))
        return -8;
    return fl_erase4k(off, &took);
}
static int ota_prog(uint32_t off, const void *p, uint32_t n)
{
    if (!ota_in_area(off, n))
        return -8;
    return fl_write(off, p, n);
}
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code)
{
#if BOARD_SMK37   /* step 5 stays on screen while the loader writes the flash: say so */
    static const char *const STEP[] = {"", "PACKAGE", "CHECK HEAD", "LOADER", "CONFIRM", "UPDATING..."};
#else
    static const char *const STEP[] = {"", "PACKAGE", "CHECK HEAD", "LOADER", "CONFIRM", "RESTART"};
#endif
    if (recovery_active) return;
    lcd_fill(0, 0, 240, 240, C_BLACK);
    draw_text_box(0, 92, 240, &FONT_S, "UPDATE", C_WHITE, 1);
    if (step < 9u)
        draw_text_box(0, 124, 240, &FONT_S, STEP[step < 6u ? step : 0], C_WHITE, 1);
    else
        draw_text_box(0, 124, 240, &FONT_S, code == 1 ? "DRY RUN OK" : "FAILED", C_WHITE, 1);
}
static void ota_commit(const uint8_t *parm)
{
    bootguard_clear(&bootguard);
    usb_detach();
    fm1_delay_ms(30);
    fm1_enter_update(parm);
}
#include "recovery.c"

/* ---- storage: [magic][length][crc32] then the bytes. A save erases (~50 ms a sector with the interrupts off:
 * the sound drops out), so save when nothing plays, not on every knob step. Each sector wears out after ~100,000 */
static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu, k;
    while (n--) {
        c ^= *p++;
        for (k = 0; k < 8u; k++)
            c = c & 1u ? (c >> 1) ^ 0xEDB88320u : c >> 1;
    }
    return ~c;
}
static int area_load(uint32_t at, uint32_t max, uint32_t magic, void *p, uint32_t n)   /* reads straight into p */
{
    uint32_t h[3];
    if (!flash_ok || n > max || st_read(at, h, sizeof h) || h[0] != magic || h[1] != n || st_read(at + sizeof h, p, n))
        return -1;
    return crc32(p, n) == h[2] ? 0 : -1;
}
static int area_save(uint32_t at, uint32_t sectors, uint32_t magic, const void *p, uint32_t n)   /* p in RAM */
{
    uint32_t h[3] = {magic, n, 0}, k, took;
    int rc;
    if (!flash_ok || n > sectors * 0x1000u - sizeof h)
        return -1;
    h[2] = crc32(p, n);
    for (k = 0; k < sectors; k++) {
        fm1_wdt_feed();                         /* (~50 ms an erase) */
        if ((rc = fl_erase4k(at + k * 0x1000u, &took)) != 0)
            return rc;
    }
    if ((rc = fl_write(at + sizeof h, p, n)) != 0)
        return rc;
    return fl_write(at, (const uint8_t *)h, sizeof h);   /* the header last: a cut save stays invalid */
}
/* one sector each, up to SECTOR_MAX bytes; 0: done. A load that fails leaves p as it was (a copy first) */
static int sector_load(uint32_t at, void *p, uint32_t n)
{
    static uint8_t buf[SECTOR_MAX];
    if (n > sizeof buf || area_load(at, SECTOR_MAX, STORE_MAGIC, buf, n))
        return -1;
    memcpy(p, buf, n);
    return 0;
}
#include "set_store.c"                          /* (Frodhes: the settings in two sectors, A / B: settings_load,
                                                 * settings_save_start / _poll, settings_save) */
static int blob_load(uint32_t i, void *p, uint32_t n) { return i < NBLOB ? sector_load(BLOBS_AT + i * 0x1000u, p, n) : -1; }
static int blob_save(uint32_t i, const void *p, uint32_t n) { return i < NBLOB ? area_save(BLOBS_AT + i * 0x1000u, 1u, STORE_MAGIC, p, n) : -1; }
/* slot i: SLOT_SECT sectors, up to SLOT_MAX bytes. slot_load reads straight into p (no buffer that big): on -1 p holds
 * rubbish, start afresh */
#define SLOT_MAGIC 0x544F4C53u                  /* "SLOT" */
static int slot_load(uint32_t i, void *p, uint32_t n) { return i < NSLOT ? area_load(SLOTS_AT + i * SLOT_SECT * 0x1000u, SLOT_MAX, SLOT_MAGIC ^ STORE_MAGIC, p, n) : -1; }
static int slot_save(uint32_t i, const void *p, uint32_t n) { return i < NSLOT ? area_save(SLOTS_AT + i * SLOT_SECT * 0x1000u, SLOT_SECT, SLOT_MAGIC ^ STORE_MAGIC, p, n) : -1; }
#if BOARD_SMK37
/* the store's owner (the kit's convention, store.h): our tag, load as ever; anything else (the stock settings,
 * another port's data) is cleared once and the tag written, before the audio starts */
static uint8_t smk_store_cleared, smk_trim_ok;   /* (the panel check shows them) */
static uint32_t smk_jedec;
static void smk_owner_check(void)
{
    uint8_t tag[16], want[16];
    uint32_t i, ours = 1, took;
    if (!flash_ok)
        return;
    smk_owner_tag(want);
    if (st_read(OWNER_AT, tag, 16))
        return;
    for (i = 0; i < 16u; i++)
        ours &= tag[i] == want[i];
    if (ours)
        return;
    fl_erase4k(SETTINGS_AT, &took);
    for (i = 0; i < NBLOB; i++) {
        fm1_wdt_feed();                         /* (~50 ms an erase) */
        fl_erase4k(BLOBS_AT + i * 0x1000u, &took);
    }
    for (i = 0; i < NSLOT; i++) {               /* (each slot's head; another firmware's samples go) */
        fm1_wdt_feed();
        fl_erase4k(SLOTS_AT + i * SLOT_SECT * 0x1000u, &took);
    }
    fl_erase4k(OWNER_AT, &took);
    fl_write(OWNER_AT, want, 16);
    smk_store_cleared = 1;
}
/* the factory key / pad velocity curves (USRTRIM 0xC1000, read only; smk_input.h checks them, else its defaults) */
static void smk_trim_read(void)
{
    static uint8_t rec[0x22Cu];
    if (flash_ok && !st_read(SMK_TRIM_FLASH, rec, sizeof rec))
        smk_trim_ok = (uint8_t)smk_trim_load(rec);
}
#endif

/* ---- MIDI: USB and the TRS jack share one ring of 4-byte USB-MIDI packets (USB cable 0, the jack cable 1) */
static int midi_get(uint8_t *st, uint8_t *d1, uint8_t *d2)   /* 1 (USB) or 2 (TRS): a message; 0: none */
{
    uint32_t pk;
    if (mi_r == mi_w)
        return 0;
    pk = midi_in_q[mi_r % MQ];
    mi_r++;
    *st = (uint8_t)(pk >> 8);
    *d1 = (uint8_t)(pk >> 16);
    *d2 = (uint8_t)(pk >> 24);
    return pk & 0xF0u ? 2 : 1;
}
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2)   /* USB out, from the main loop only; F8 FA FB FC too */
{
    midi_out_event((st >> 4) | st << 8 | (d1 & 0x7Fu) << 16 | (d2 & 0x7Fu) << 24);
}

/* ---- the timer: 10 kHz key scan, milliseconds, USB at 2 kHz. It outranks the audio ISR; nested in a
 * render it only scans, counts and feeds the USB audio stream (as sloopDX main.c) */
static volatile uint8_t in_audio;
#ifdef FM1_PROF_TICK
/* Frodhes, developer build only (APP_DEFS=-DFM1_PROF_TICK ./build.sh; never shipped): the timer tick's own time (its C
 * body, entry .. return: the scan, the ms count, the USB / TRS polls; not the register save of hal/fm1_isr.S) and how
 * much of it lands in each audio block (the ticks nested in the audio ISR: the render's real cost includes them).
 * In 24 MHz ticks; the CC 116 query answers them (tick_prof_reply, items 13..16) and they restart. */
static struct { uint32_t max, sum, n, blk, blk_max, blk_sum, blk_n; } tick_st __attribute__((section(".bss.tick_st")));
static void tick_prof(uint32_t t0)
{
    uint32_t d = fm1_ticks() - t0;
    if (d > tick_st.max) tick_st.max = d;
    tick_st.sum += d;
    tick_st.n++;
    if (in_audio) tick_st.blk += d;
}
static void tick_prof_block(void)              /* the audio ISR's end: this block's tick time */
{
    uint32_t b = tick_st.blk;
    tick_st.blk = 0;
    if (b > tick_st.blk_max) tick_st.blk_max = b;
    tick_st.blk_sum += b;
    tick_st.blk_n++;
}
static void tick_prof_item(uint32_t id, uint32_t v)
{
    if (v > 16383u) v = 16383u;
    midi_send(0xB0, 100, id); midi_send(0xB0, 101, v >> 7); midi_send(0xB0, 102, v & 127u);
}
/* main loop (the CC 116 reply): CC 100 id / 101-102 value, 13 the longest tick (1/24 us), 14 the most tick time in one
 * audio block (us), 15 the mean tick time per audio block (1/4 us), 16 the mean tick (1/24 us); then they restart */
static void tick_prof_reply(void)
{
    uint32_t mx = tick_st.max, sum = tick_st.sum, n = tick_st.n, bmx = tick_st.blk_max, bs = tick_st.blk_sum;
    uint32_t bn = tick_st.blk_n;
    tick_st.max = tick_st.sum = tick_st.n = tick_st.blk_max = tick_st.blk_sum = tick_st.blk_n = 0;
    tick_prof_item(13, mx);
    tick_prof_item(14, bmx / 24u);
    tick_prof_item(15, bn ? bs / (6u * bn) : 0u);
    tick_prof_item(16, n ? sum / n : 0u);
}
void timer5_run(void);
#define fm1_timer5_irq timer5_run              /* (the body below; the wrapper after it times it) */
#endif
void fm1_timer5_irq(void)
{
    static uint32_t sub, owed, last, acc;       /* (Frodhes: sub counts 0..4, the phase of the 2 kHz polls; no divide) */
    uint32_t t0 = fm1_ticks(), usb_due = sub == 0u;
    fm1_timer5_ack();
    fm1_input_tick();
    acc += t0 - last;
    last = t0;
    while (acc >= 1000u * FM1_TICKS_PER_US) {
        acc -= 1000u * FM1_TICKS_PER_US;
        fm1_ms++;
    }
    if (usb_due)
        owed |= 1u;
#if FELUCCA_UART
    if (sub == 2u)
        owed |= 2u;                             /* 2 kHz: <= ~7 bytes per call at 31250 baud */
#endif
    if (++sub == 5u)
        sub = 0;
#if !FELUCCA_UAC                                /* MIDI only: the USB poll keeps its 2 kHz, nested in a render too (as zp12,
                                                   whose MIDI out works on the device) */
    if (usb_due)
        usb_poll();
#if FELUCCA_UART
    if (owed & 2u)
        uart_midi_tick();
#endif
    owed = 0;
    return;
#endif
    if (in_audio) {
        if (usb_due)
            UAC_ONLY(uac_service());
        return;
    }
    if (owed & 1u)
        usb_poll();
#if FELUCCA_UART
    if (owed & 2u)
        uart_midi_tick();
#endif
    owed = 0;
}
#ifdef FM1_PROF_TICK
#undef fm1_timer5_irq
void fm1_timer5_irq(void)
{
    uint32_t t0 = fm1_ticks();
    timer5_run();
    tick_prof(t0);
}
#endif
extern void isr_timer5(void);

/* ---- the sound: ALNK0 I2S, two halves; the app renders, the MASTER knob scales, Q15 -> 24 bit (-6 dBFS ceiling) */
static int32_t abuf[2u * HALF_WORDS] __attribute__((aligned(4)));
static volatile int32_t master_q12 = 2048;
static int32_t tap_q[64] __attribute__((section(".bss.tap_q")));   /* Frodhes: 32 frames for the USB tap (Q15) */
static volatile uint8_t usb_pre_master;        /* the app: 1 = the USB audio does not follow the MASTER knob */
static void app_audio(int32_t *out, uint32_t n);
static void app_main(void);

/* Frodhes: the whole audio ISR's time (entry .. fm1_audio_ack_half: the render, the MASTER pass, the USB tap and the
 * 48 kHz resampler), the tail after app_audio, the spacing of the ISR entries (a late entry shows as one long and
 * one short period: TIMER5 outranks the audio ISR) and the USB ring's true low point (its fill just before the
 * block is tapped in). Maxima / minima since the app's last read (frodhes.c CC 116); own sections. */
static struct { uint32_t t_prev, max_us, tail_us, per_min, per_max, fill_low; } isr_st
    __attribute__((section(".bss.isr_st")));
static __attribute__((noinline)) void isr_stats(uint32_t t0, uint32_t t1, uint32_t fill)
{
    uint32_t t2 = fm1_ticks(), us = (t2 - t0) / FM1_TICKS_PER_US, tail = (t2 - t1) / FM1_TICKS_PER_US;
    uint32_t per = (t0 - isr_st.t_prev) / FM1_TICKS_PER_US;
    if (isr_st.t_prev) {
        if (!isr_st.per_min || per < isr_st.per_min) isr_st.per_min = per;
        if (per > isr_st.per_max) isr_st.per_max = per;
    }
    isr_st.t_prev = t0;
    if (us > isr_st.max_us) isr_st.max_us = us;
    if (tail > isr_st.tail_us) isr_st.tail_us = tail;
    if (fill && (!isr_st.fill_low || fill < isr_st.fill_low)) isr_st.fill_low = fill;   /* fill + 1; 0 = no stream */
}

/* Frodhes: the block's MASTER pass with the gain ramping from master_prev to m (the loops below, a gain a frame) */
static int32_t master_prev = -1;
#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))
static __attribute__((noinline)) void master_ramp(int32_t *o, int32_t m)
{
    int32_t mq = (master_prev < 0 ? m : master_prev) << 8, dm = master_prev < 0 ? 0 : m - master_prev, b, i;
    for (b = 0; b < (int32_t)HALF_WORDS; b += 64) {
        int32_t *c = o + b, *q = tap_q;
        HAL_KEEP_REG(q);
        for (i = 0; i < 32; i++, c += 2, q += 2) {
            int32_t g, l, r;
            mq += dm; g = mq >> 8;
            if (usb_pre_master) {
                l = TAIL_PRE(c[0]); r = TAIL_PRE(c[1]);
                q[0] = l; q[1] = r;
                c[0] = ((l * g) >> 12) << APP_OUT_SHIFT;
                c[1] = ((r * g) >> 12) << APP_OUT_SHIFT;
            } else {
                l = (c[0] * g) >> 12; r = (c[1] * g) >> 12;
                l = TAIL_PRE(l); r = TAIL_PRE(r);
                q[0] = l; q[1] = r;
                c[0] = l << APP_OUT_SHIFT;
                c[1] = r << APP_OUT_SHIFT;
            }
        }
        UAC_ONLY(uac_tap(q - 64, 32u));
    }
    master_prev = m;
}
#undef TAIL_PRE
void fm1_alnk0_irq(void)                       /* via isr_alnk0 (hal/fm1_isr.S) */
{
    uint8_t p;
    uint32_t t0 = fm1_ticks();
    in_audio = 1;
    p = fm1_audio_pending();
    fm1_audio_ack_aux(p);
    if (p & FM1_AUDIO_HALF) {
        int32_t *o = &abuf[fm1_audio_free_half() * HALF_WORDS];
        uint32_t i, b;
        int32_t m;
#if !APP_OUT_WRITTEN
        for (i = 0; i < HALF_WORDS; i++)
            o[i] = 0;
#endif
        uint32_t t1, fill;
        UAC_ONLY(uac_render_start());
        app_audio(o, HALF_FRAMES);
        t1 = fm1_ticks();
        fill = uac.feed && uac.go ? ua_w - ua_r + 1u : 0u;   /* the ring's low point: the render ran it down */
        /* Frodhes: one pass per 32 frames (MASTER, the clip, the codec's shift; the USB tap's samples into tap_q) instead
         * of three over the block; the same samples. MASTER read once: only the main loop writes it */
        m = master_q12;
#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))
        if (m != master_prev) {                 /* Frodhes (0.9.6, BUGHUNT #20: a new MASTER stepped once a block, a
                                                 * 2.4x zipper while the knob turns): the gain ramps from the last
                                                 * block's, frame by frame (Q8: (m - prev) a frame reaches m exactly) */
            master_ramp(o, m);
            b = HALF_WORDS;
        } else b = 0;
        for (; b < HALF_WORDS; b += 64u) {
            int32_t *c = o + b, *q = tap_q;
            HAL_KEEP_REG(q);                    /* (an opaque pointer: else the compiler specialises uac_tap48 on
                                                 * tap_q and its per-frame loop gets 7 instructions longer) */
            if (usb_pre_master) {               /* the computer records the app at full level, the jack follows MASTER */
                for (i = 0; i < 32u; i++, c += 2, q += 2) {
                    int32_t l = TAIL_PRE(c[0]), r = TAIL_PRE(c[1]);
                    q[0] = l; q[1] = r;
                    c[0] = ((l * m) >> 12) << APP_OUT_SHIFT;
                    c[1] = ((r * m) >> 12) << APP_OUT_SHIFT;
                }
            } else {                            /* the computer records what the speaker plays */
                for (i = 0; i < 32u; i++, c += 2, q += 2) {
                    int32_t l = (c[0] * m) >> 12, r = (c[1] * m) >> 12;
                    l = TAIL_PRE(l); r = TAIL_PRE(r);
                    q[0] = l; q[1] = r;
                    c[0] = l << APP_OUT_SHIFT;  /* Q15 -> 24 bit: 7 = a -6 dBFS ceiling (Dinghy), 8 = full scale */
                    c[1] = r << APP_OUT_SHIFT;
                }
            }
            UAC_ONLY(uac_tap(q - 64, 32u));
        }
#undef TAIL_PRE
        fm1_audio_ack_half();
        isr_stats(t0, t1, fill);
#ifdef FM1_PROF_TICK
        tick_prof_block();
#endif
    }
    in_audio = 0;
}
extern void isr_alnk0(void);

/* ---- the main loop's duties: watchdog, USB, the installer's update request, the MASTER knob */
static void platform_poll(void)
{
    static int32_t knob_avg = 512 * 16;
    int32_t a;
    fm1_wdt_feed();
    usb_retry(fm1_ms);
    ota_service();
    if (usb.ota_req) {
        usb.ota_req = 0;
        if (flash_ok)
            ota_session();                      /* returns only if nothing was committed */
    }
    if (usb.uboot_req) {
        fm1_audio_stop();
        lcd_fill(0, 0, 240, 240, C_BLACK);
        draw_text_box(0, 110, 240, &FONT_S, "UBOOT (USB)", C_WHITE, 1);
        usb_detach();
        fm1_delay_ms(30);
        bootguard.pending = 0;
        fm1_enter_uboot();
    }
    if (fm1_ms >= 30000u && bootguard.pending)
        bootguard_clear(&bootguard);            /* 30 s up: this firmware boots */
    if ((a = fm1_adc_read(FM1_ADC_MASTER)) >= 0) {
        /* heavily smoothed (a headphone plug makes the pot's reading jump about) and with a dead band of 6 / 1023,
         * so the volume moves only when the knob does */
        static uint32_t k_used = 512;
        uint32_t k10;
        knob_avg += (a * 16 - knob_avg) / 64;
        k10 = (uint32_t)(knob_avg / 16);
        if (k10 + 6u < k_used || k10 > k_used + 6u || (k10 == 0u) != (k_used == 0u) || (k10 >= 1020u) != (k_used >= 1020u)) {
            k_used = k10;
            master_q12 = (int32_t)((k10 * k10) >> 8);
        }
    }
}

static void fm1_fault(const fm1_crash_t *c)
{
    char b[12];
    uint32_t i, t0, v[3] = {c->vec, c->pc, c->rets};
    fm1_audio_stop();
    lcd_fill(0, 0, 240, 240, RGB(160, 0, 0));
    draw_text_box(0, 20, 240, &FONT_S, APP_NAME " CRASH", C_WHITE, 1);
    for (i = 0; i < 3u; i++) {
        uint32_t k;
        for (k = 0; k < 8u; k++) b[k] = "0123456789ABCDEF"[(v[i] >> (28u - 4u * k)) & 15u];
        b[8] = 0;
        draw_text_box(10, 60 + i * 20, 220, &FONT_S, b, C_WHITE, 0);
    }
    t0 = fm1_ticks();
    while ((uint32_t)(fm1_ticks() - t0) < 8000u * 1000u * FM1_TICKS_PER_US)
        fm1_wdt_feed();
    fm1_reboot();
}

void fm1_cstart(void)
{
    extern uint32_t _data_start[], _data_end[], _data_load[], _bss_start[], _bss_end[];
    extern uint32_t _pool_start[], _pool_end[], _rt_start[], _rt_end[], _rt_load[];
    uint32_t *s, *d, p3, boot_mode;
    fm1_time_init();
    fm1_reset_reason();
    p3 = fm1_boot.p3_rst;
    fm1_wdt_arm(0x0D);
    if ((p3 & 1u) && !(p3 & (4u | 0x40u)))
        bootguard_clear(&bootguard);
    boot_mode = bootguard_begin(&bootguard);
    if (boot_mode == BOOT_ROM)
        fm1_enter_uboot();
    fm1_irq_init();
    for (d = _bss_start; d < _bss_end; d++) *d = 0;
    for (d = _pool_start; d < _pool_end; d++) *d = 0;
    for (s = _data_load, d = _data_start; d < _data_end; s++, d++) *d = *s;
    for (s = _rt_load, d = _rt_start; d < _rt_end; s++, d++) *d = *s;
    fm1_mailbox_clear();
    fm1_guard_enable(FM1_GUARD_STACK | FM1_GUARD_WRITE | FM1_GUARD_BUS | FM1_GUARD_PC);
    fm1_boot.p3_rst = (uint8_t)p3;
    if (boot_mode == BOOT_RECOVERY || recovery_key())
        recovery_main();                        /* OCT- at power-on: the USB rescue */
#if BOARD_SMK37
    smk_jedec = FL_FAR(fl_jedec_ram)();         /* (the panel check shows it: units may carry another part) */
    flash_ok = FL_JEDEC_OK(smk_jedec);
#else
    flash_ok = FL_JEDEC_OK(FL_FAR(fl_jedec_ram)());
#endif
    ota_boot_cleanup();
    lcd_init();
    lcd_fill(0, 0, 240, 240, C_BLACK);
    fm1_input_init();
    fm1_adc_init();
#if BOARD_SMK37
    smk_trim_read();                            /* (after fm1_input_init: it sets the defaults first) */
    smk_owner_check();
#endif
    fm1_audio_init(abuf, HALF_WORDS, isr_alnk0, 3);   /* (abuf is zero: .bss) */
    usb_start();
#if FELUCCA_UART
    uart_midi_init();                           /* before TIMER5: PORTH read-modify-write */
#endif
    fm1_timer5_start(isr_timer5, 4);
    fm1_guard_lock_top();
    fm1_irq_enable_all();
    app_main();
    for (;;)
        platform_poll();
}
#endif
