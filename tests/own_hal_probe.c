/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* Probe of the plugin's hardware layer: the app as the plugin builds it (DINGHY_HOST + DINGHY_WEB + DINGHY_PLUGIN,
 * the engine_unit.c switches), compiled once with -DFR_OWN=0 (the browser layer, web/web_hal.c) and once with
 * -DFR_OWN=1 (plugin/src/own_hal.c, own_store.h). It prints every shared value and the behaviour of every call the
 * app and plugin/src/slot_bridge.h make; tests/py/test_own_hal.py runs both and compares the text, which must be
 * the same line for line. (A record id, st_id, is a free hash: only "never 0" and "deterministic" are compared.) */
#define FRP_SLOT 0
#define DINGHY_HOST 1
#define DINGHY_WEB 1
#define DINGHY_PLUGIN 1
#define main(...) static __attribute__((unused)) probe_web_main(__VA_ARGS__)
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define printf(...) ((void)0)
#define fprintf(...) ((void)0)
#define getenv(name) ((char *)0)
#include "../firmware/src/frodhes.c"
#undef main
#undef printf
#undef fprintf
#undef getenv

static int poll_n, yields;
static double move_now_ms(void) { return poll_n++ == 0 ? 0.25 : (double)poll_n + 0.5; }
static void move_yield(void) { yields++; }

#define V(x) printf_("%s=%u\n", #x, (unsigned)(x))
static int printf_(const char *f, ...) __attribute__((format(printf, 1, 2)));
#include <stdarg.h>
static int printf_(const char *f, ...) { va_list a; int r; va_start(a, f); r = vprintf(f, a); va_end(a); return r; }

int main(void)
{
    uint32_t i, n, rel, h = 0, pass = 0;
    uint8_t st, d1, d2, buf[ST_TAIL_N], set_in[256], set_out[256];
    /* the names and values the app takes from its platform */
    V(BOARD_SMK37); V(FS); V(HALF_FRAMES); V(HOST_SAVE_MS); V(ST_TAIL_N);
    V(BTN_OCT_DOWN); V(BTN_OCT_UP); V(BTN_FX); V(BTN_SEL); V(BTN_ENV); V(BTN_LFO); V(BTN_EDIT); V(BTN_GLO); V(BTN_HOME);
    V(BTN_SAVE); V(BTN_ARP); V(BTN_SEQ); V(BTN_PLAY); V(BTN_REC);
    V(ENC_SELECT); V(ENC_ALGORITHM); V(ENC_KNOB1); V(ENC_KNOB2); V(ENC_KNOB3); V(ENC_KNOB4); V(ENC_PRESETS);
    V(KEY_LED(0)); V(KEY_LED(13)); V(KEY_LED(26));
    V(SETTINGS_AT); V(SETTINGS_B); V(SQ_A); V(SQ_B); V(OLD_SET_A); V(OLD_PR_A); V(OLD_PR_B); V(OLD_SQ_A); V(OLD_SQ_B);
    V(FL_GLOB_LO); V(FL_GLOB_HI); V(NBLOB); V(NSLOT);
    V(sizeof host_mem); V(sizeof fm1_led_duty); V(fm1_led_duty[0]); V(fm1_led_duty[1]); V(uart_midi_on);
    V(usb_pre_master); V(flash_ok); V(fm1_ms); V(fm1_in.notes); V(fm1_in.buttons); V(host_mem[0]);
    /* FL_IN and FL_GUARD over a grid of ranges (a sum of the answers, and the first few exactly) */
    {
        static const uint32_t offs[] = {0u, 0x97000u, 0x99000u, 0xA8FFFu, 0xA9000u, 0xADFFFu, 0xAE000u, 0xE0000u, 0xE4FFFu,
                                        0xE5000u, 0xFB000u, 0xFBFFFu, 0xFC000u, 0xFC001u, 0xFCFFFu, 0xFD000u, 0xFE000u,
                                        0xFEFFFu, 0xFF000u, 0xFF001u, 0x100000u, 0xFFFFFFFFu};
        static const uint32_t lens[] = {0u, 1u, 0x100u, 0x1000u, 0x1001u, 0x3000u, 0x5000u, 0x5001u, 0xFFFFFFFFu};
        uint32_t a, b, g = 0, in = 0;
        for (a = 0; a < sizeof offs / sizeof *offs; a++)
            for (b = 0; b < sizeof lens / sizeof *lens; b++) {
                g = g * 3u + (FL_GUARD(offs[a], lens[b]) ? 1u : 0u);
                in = in * 3u + (FL_IN(offs[a], lens[b], FL_GLOB_LO, FL_GLOB_HI) ? 1u : 0u);
                printf_("guard %x %x %d %d\n", offs[a], lens[b], !!FL_GUARD(offs[a], lens[b]),
                        !!FL_IN(offs[a], lens[b], FL_GLOB_LO, FL_GLOB_HI));
            }
        V(g); V(in);
    }
    /* the panel: idle */
    rel = 77u;
    V(fm1_input_edges(&rel)); V(rel); V(fm1_input_note_edges());
    for (i = 0; i < 9u; i++) printf_("enc %u %d\n", i, (int)fm1_enc_take(i));
    fm1_led_key(3, 1); fm1_led_key_glow(3, 1); fm1_led_group_ids(1, 2); fm1_led_build();
    /* the clock: the first poll reads 0, then ms + 1 */
    for (i = 0; i < 6u; i++) { platform_poll(); printf_("poll %u ms %u yields %d\n", i, (unsigned)fm1_ms, yields); }
    /* MIDI: an empty ring, then 70 messages (the 64 first stay, the rest are dropped), clock bytes stamped */
    V(midi_get(&st, &d1, &d2)); V(fr_ev_ofs);
    for (i = 0; i < 70u; i++) {
        uint32_t s = i % 5u == 0u ? 0xF8u : i % 7u == 0u ? 0xFAu : 0x90u + (i & 3u);
        fm1_ms = 1000u + i * 37u;
        web_ofs_next = (uint16_t)(i * 997u);
        web_midi(s, (i * 11u) & 127u, (i * 5u + 1u) & 127u);
    }
    V(mq_w); V(mq_r);
    for (n = 0; midi_get(&st, &d1, &d2); n++) {
        printf_("midi %u %02x %02x %02x ofs %u\n", n, st, d1, d2, (unsigned)fr_ev_ofs);
        if (n == 10u) { web_ofs_next = 9; web_midi(0xB0u, 1u, 2u); web_ofs_next = 0; }   /* room made: one more goes in */
    }
    V(n); V(fr_ev_ofs); V(mq_w - mq_r);
    web_ofs_next = 5; web_midi(0xC0u, 3u, 0u); web_ofs_next = 0;
    V(midi_get(&st, &d1, &d2)); V(st); V(fr_ev_ofs);
    midi_send(0x90u, 60u, 100u);
    /* flash image reads */
    memset(host_mem, 0xFF, sizeof host_mem);
    host_mem[0x10] = 0x12; host_mem[OWN_PROBE_LAST] = 0x34;
    V(st_read(0x10, buf, 1)); V(buf[0]);
    V(st_read(OWN_PROBE_LAST, buf, 1)); V(buf[0]);
    V(st_read(OWN_PROBE_LAST, buf, 2)); V(st_read(sizeof host_mem, buf, 0)); V(st_read(sizeof host_mem - 4u, buf, 4));
    V(st_read(0xFFFFFFF0u, buf, 32));
    /* the settings record: nothing before a save, then the settings and the tail come back */
    st_tail = buf;
    for (i = 0; i < sizeof set_in; i++) set_in[i] = (uint8_t)(i * 7u + 3u);
    for (i = 0; i < ST_TAIL_N; i++) buf[i] = (uint8_t)(i * 13u + 1u);
    memset(set_out, 0, sizeof set_out);
#ifdef OWN_PROBE_RESET
    OWN_PROBE_RESET;
#endif
    V(settings_load(set_out, 256u)); V(settings_load_old(set_out, 256u)); V(settings_old_id()); V(smk_old_ours());
    V(settings_check()); V(settings_save_poll());
    V(settings_save(set_in, 255u)); V(settings_save(set_in, 257u)); V(settings_load(set_out, 255u));
    V(settings_save_start(set_in, 255u)); V(settings_load(set_out, 256u));
    V(settings_save_start(set_in, 256u)); V(settings_save_poll()); V(settings_check());
    memset(buf, 0, ST_TAIL_N);
    V(settings_load(set_out, 255u)); V(settings_load(set_out, 256u));
    V(memcmp(set_in, set_out, 256u)); for (i = 0; i < ST_TAIL_N; i++) h = h * 31u + buf[i]; V(h);
    V(settings_load_old(set_out, 256u));
    set_in[0] ^= 0xFF; V(settings_save(set_in, 256u)); V(settings_load(set_out, 256u)); V(set_out[0]);
    V(st_id(0u, 0u) != 0u); V(st_id(0x12345678u, 9u) != 0u); V(st_id(0x12345678u, 9u) == st_id(0x12345678u, 9u));
    for (i = 0; i < 100000u; i++) pass += st_id(i * 2654435761u, i) != 0u;
    V(pass); V(set_at); V(set_seq);
    return 0;
}
