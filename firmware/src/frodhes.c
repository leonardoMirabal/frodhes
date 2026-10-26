/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes: an electric piano (a Rhodes-style tine piano physical model) for the M-VAVE FM-1. Built on Dinghy.
 *
 *   keys       play the piano at KEY VEL (SELECT), through the scale mode; the notes go out over USB-MIDI too
 *   panel      vintage-stage-keyboard style: TYPE DRIVE TREM/WAH CHO/PHA DLY REVERB VOLUME (ui.c), voicing pages, tempo, scales
 *   REC        the sustain pedal: hold it, or tap it to latch (tap again to let go)
 *   MASTER     the volume (platform.c)
 *   USB-MIDI   notes in (with velocity), the panel CC map, CC 64 sustain with half-pedal
 *
 * This file is the app: panel, MIDI, screen, settings. The sound is rhodes.c. Below both is platform.c
 * (Dinghy / SLOOP / Felucca): updates, the USB rescue, USB-MIDI, USB audio, keys, knobs, LEDs, screen, settings.
 * Rules of the road (no FPU, no libgcc): integers only, no float, no 64-bit division. docs/RULES.md.
 * tests/host.c runs this file on a computer: the screen as a PNG, the sound as a WAV. */
#define APP_NAME "FRODHES"
#define APP_OUT_SHIFT 8u   /* the codec at full scale: +6 dB on the jack over Dinghy's -6 dBFS ceiling (the look-ahead
                            * limiter keeps every sample under -2.7 dBFS, so it never clips; 0.8) */
#define APP_OUT_WRITTEN 1   /* app_audio writes every sample of out (fx_process's limiter, with "="): platform.c does not
                             * clear it first; the host build fills it with garbage instead (tests/host.c) */
/* FR_RAM: an audio hot loop that runs from RAM (.ram_text, copied there at boot as the flash driver is), not from XIP
 * flash through its cache (0.9.5 cpu-trim). Leaf functions only (build.py: no calls in .ram_text); the same code */
#if defined(DINGHY_HOST) || defined(DINGHY_WEB)
#define FR_RAM
#define FR_FAR(fn) fn
#else
#define FR_RAM __attribute__((section(".ram_text.fr")))
#include "fm1_keep.h"   /* hal/: the empty asm */
/* XIP (0x02xxxxxx) -> RAM (0x01C0xxxx) is beyond the 23-bit call range (as fm1_flash.h FL_FAR): an FR_RAM function is
 * called through its address in a register the compiler cannot fold back (one instruction more a call) */
#define FR_FAR(fn) (__extension__({ __typeof__(&fn) p_ = &fn; HAL_KEEP_REG(p_); p_; }))
#endif
#include "frodhes_store.h"   /* the flash Frodhes writes, per board, and its allow-list APP_FL_RANGE (store.h) */
#include "keyvel.h"     /* key velocity's interfaces: first, so the scan tick (platform.c) can reach its hooks */
#include "platform.c"
#if defined(DINGHY_HOST) && !defined(HOST_FL_CHECK)
/* (the web build: no flash log; a place the firmware's allow-list refuses fails as on the device) */
static int host_fl_check(const char *what, uint32_t at, uint32_t n) { (void)what; return FL_GUARD(at, n) ? 0 : -1; }
#endif
#if BOARD_SMK37
/* the SMK-37's versions of the FM-1 HAL's names ui.c uses (smk_input.h has none of them): no TRS input (the MIDI page
 * shows no TRS IN card); DISPLAY BRIGHTNESS as the FM-1's lit duties /16, [0] the keys' (the SMK: the pads' colours,
 * frodhes_smk.h smk_leds), [1] the buttons'; fm1_led_build sets the button LEDs' brightness in the HAL (smk_led_dark:
 * the multiplexed LEDs can be lit fully, half or a quarter of the time without a visible flicker) */
static volatile uint8_t uart_midi_on;           /* (stored by the MIDI page's code, nothing reads it) */
static uint8_t fm1_led_duty[2] = {16u, 16u};
static void fm1_led_group_ids(uint32_t id0, uint32_t id1) { (void)id0; (void)id1; }   /* (no key LEDs) */
static void fm1_led_build(void) { smk_led_dark = fm1_led_duty[1] >= 10u ? 0u : fm1_led_duty[1] >= 5u ? 2u : 6u; }
static uint32_t smk_btn_leds;                   /* the SMK's own buttons' LEDs (KNOB / FADER BANK, < >: smk_leds), as
                                                 * ui_leds' mask of button ids */
#endif
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
#include "engine.c"     /* the voice engines: rhodes.c (the Rhodes), bars.c (VIBES + MARIMBA), and the dispatcher */
#include "fx.c"
#include "rev_types.c"  /* the REVERB's TYPEs: the framework (fx.c's fx_reverb), rev_plate.c, rev_inf.c */
#include "params.c"
#include "keyvel.c"     /* the FM-1 keys' velocity (TOUCH, keyvel.h) */
#if KV_FM1KA
#include "fm1_keyadc.h" /* and its part of the scan tick (the hooks keyvel.h maps) */
#endif
#include "tempo.c"
#include "arp.c"
#include "notes.c"
/* Flash writes and the USB audio: an erase used to hold the interrupts off ~24 ms (no USB audio packet went out, and
 * macOS then handed a recording ~250 ms of stale input, 2026-10-18), so the automatic saves waited while a computer
 * read the USB audio. Now the HAL splits every erase and page program into slices of <= ~0.3 ms with the interrupts
 * off, the flash suspended between them while the ISRs run (fm1_flash.h fl_split_op), and the saves go as they do
 * without a stream: the settings 2 s after the last change once silent, the pattern 1.5 s after. */
#if !defined(DINGHY_HOST)
/* the CC 116 query's second half (main loop): the whole audio ISR and the USB audio stream's health since the last
 * query. CC 108 / 107 = the longest whole ISR (us / 4, MSB / LSB: entry .. the I2S ack, the USB tap and the 48 kHz
 * resampler included), CC 110 / 109 = the longest tail after app_audio (us / 4). Then one CC 100 = id, CC 101 / 102
 * = value (MSB / LSB, 14 bits, saturated) per item: 1 underrun packets (the ring ran dry: a repeated frame),
 * 2 missed (an IN token found no packet), 3 overruns (a block dropped: the ring full), 4 / 5 packets one frame
 * longer / shorter (the rate adaptation), 6 / 7 the lowest / highest fill at a render's start (frames), 8 the
 * lowest fill just before a block is tapped in (the true low point; 16383 = no stream), 9 / 10 the shortest /
 * longest spacing of the audio ISR's entries (us; 5805 nominal), 11 stalls, 12 the stream: 1 alt set + 2 flowing
 * + 4 at 48 kHz + 8 primed, 17 the longest stretch a flash write held the interrupts off (us, fm1_flash.h; 0 = no
 * write), and (not in an FM1_PROF_TICK build: the MIDI out ring holds 64) 18 the SR2 suspend bits the part set
 * (0x80 / 0x04; 0 = none), 19 the longest erase's own time (x 10 us, the part busy: the slices summed), 20 the longest
 * the main loop was held by a flash write (x 10 us: a preset's or the pattern's erase or page program from start to
 * end; the settings' auto save one slice, fm1_flash.h fl_op_poll, the ISRs meanwhile included). The counters are deltas since the last query; the ranges restart at each query. */
static void usb_health_send(uint32_t id, uint32_t v)
{
    if (v > 16383u) v = 16383u;
    midi_send(0xB0, 100, id); midi_send(0xB0, 101, v >> 7); midi_send(0xB0, 102, v & 127u);
}
static void usb_health_reply(void)
{
    static uint32_t seen[6] __attribute__((section(".bss.usb_seen")));   /* own section: out of the global merge */
    uint32_t now[6], i, v;
    now[0] = uac.underruns; now[1] = uac.missed; now[2] = uac.overruns;
    now[3] = uac.adj_up; now[4] = uac.adj_down; now[5] = uac.stalls;
    v = isr_st.max_us >> 2; if (v > 16383u) v = 16383u;
    midi_send(0xB0, 108, v >> 7); midi_send(0xB0, 107, v & 127u);
    v = isr_st.tail_us >> 2; if (v > 16383u) v = 16383u;
    midi_send(0xB0, 110, v >> 7); midi_send(0xB0, 109, v & 127u);
#ifndef RH_PROF_STAGES                                 /* (a stage build answers its stages instead: the MIDI
                                                        * out ring holds 64 messages) */
    for (i = 0; i < 5u; i++)
        usb_health_send(i + 1u, now[i] - seen[i]);
    usb_health_send(6, uac.fill_lo == 0xFFFFFFFFu ? 16383u : uac.fill_lo);
    usb_health_send(7, uac.fill_hi);
    usb_health_send(8, isr_st.fill_low ? isr_st.fill_low - 1u : 16383u);
    usb_health_send(9, isr_st.per_min);
    usb_health_send(10, isr_st.per_max);
    usb_health_send(11, now[5] - seen[5]);
    usb_health_send(12, (uint32_t)(uac.alt != 0) | (uint32_t)(uac.flowing != 0) << 1 | (uint32_t)(uac.r48 != 0) << 2 |
                        (uint32_t)(uac.go != 0) << 3);
    usb_health_send(17, fl_off_max_us);
#ifndef FM1_PROF_TICK
    usb_health_send(18, fl_sus_seen);
    usb_health_send(19, fl_erase_max_us / 10u);
    usb_health_send(20, fl_wall_max_us / 10u);
#endif
#endif
#ifdef FM1_PROF_TICK
    tick_prof_reply();                                 /* (developer build: the timer tick's time, platform.c) */
#endif
    for (i = 0; i < 6u; i++)
        seen[i] = now[i];
    /* the ranges restart (single stores: an ISR between the read and the store only loses that one sample) */
    uac.fill_lo = 0xFFFFFFFFu; uac.fill_hi = 0;
    isr_st.max_us = isr_st.tail_us = isr_st.per_min = isr_st.per_max = isr_st.fill_low = 0;
    fl_off_max_us = fl_erase_max_us = fl_wall_max_us = 0;
    fl_sus_seen = 0;
}
#endif
#if !defined(DINGHY_HOST)
static int usb_link(void) { return usb.config != 0; }          /* a computer has the FM-1 configured (USB-MIDI up) */
#elif defined(DINGHY_WEB)
static int usb_link(void) { return 1; }
#else
static int usb_link(void) { return host_usb_link; }
#endif
#include "seq.c"
#include "ui.c"
#include "presets.c"
#include "bars.c"       /* last: the bar engine's code (see bars.c) */

static int32_t voice_mono[256];
#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
/* host only (tests/py/test_race_fuzz.py): after each block, with nothing in flight (both rings empty, no TYPE change
 * pending), every voice held down must be a note the notes layer wants sounding (a source's pitch sent to the engine,
 * or the arpeggiator's note): "RACE STUCK" after 3 blocks without one; a voice that ended while a note waited for its
 * steal's fade lost that note: "RACE LOST" (the waiting note never starts) */
static void host_race_check(void)
{
    static uint8_t bad[16], lost[16];
    uint32_t i, nv = eng_cur == ENG_BARS ? BAR_NV : RH_NV;
    int calm = eq_r == eq_w && engq_r == engq_w && eng_want == eng_cur && !eng_swapping;
    for (i = 0; i < nv && i < 16u; i++) {
        uint32_t alive = eng_cur == ENG_BARS ? bv[i].alive : rv[i].alive, held = eng_cur == ENG_BARS ? bv[i].held : rv[i].held;
        uint32_t note = eng_cur == ENG_BARS ? bv[i].note : rv[i].note, pend = eng_cur == ENG_BARS ? bv[i].pend : rv[i].pend;
        uint32_t pn = eng_cur == ENG_BARS ? bv[i].pend_note : rv[i].pend_note;
        int want = (out_ref[note & 127u] && !note_to_arp[note & 127u]) || arp_playing == note;
        if (calm && alive && held && !want) { if (bad[i] < 255u && ++bad[i] == 3u) printf("%6u ms  RACE STUCK voice %u note %u\n", fm1_ms, i, note); }
        else bad[i] = 0;
        if (!alive && pend) { if (!lost[i]) printf("%6u ms  RACE LOST voice %u note %u (pend %u)\n", fm1_ms, i, pn, pend); lost[i] = 1; }
        else lost[i] = 0;
    }
}
#endif
/* noinline (0.9.5): with the filter and the tremolo out of it, app_audio became small enough for the compiler to
 * inline it into the audio interrupt (fm1_alnk0_irq): its own function, as before (tools/build.py counts its loops) */
static __attribute__((noinline)) void app_audio(int32_t *out, uint32_t n)
{
    uint32_t ms0 = fm1_ms;                         /* (the block's start: the clock's phase lock, tempo.c) */
#ifndef DINGHY_HOST
    uint32_t t0 = fm1_ticks(), us;
#endif
#if defined(RH_PROF_STAGES) && !defined(DINGHY_HOST)
    prof_st_t = t0;
#endif
    eng_render(voice_mono, n);
#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
    host_race_check();
#endif
    PROF_STAMP(1);
    fx_process(voice_mono, out, n);
    PROF_STAMP(15);
#if FELUCCA_UAC_TONE == 2
    {   /* bench (BUGHUNT #24, builds with APP_DEFS=-DFELUCCA_UAC_TONE=2 only): the app's output replaced by triangles of
         * period 147 frames (300 Hz, L and R in antiphase, -12 dBFS) after the engine and the effects ran (their CPU
         * load stays), so the platform's tail, the USB tap, the 44.1 -> 48 kHz resampler, the ring and the rate
         * adjustment carry them as they carry music: 147 frames in are exactly 160 out, so the recording repeats
         * itself every 160 frames bit for bit (USB VOL fixed: the knob does not scale it); tools/hil_usb_bench.py */
        static uint32_t tph;
        uint32_t i;
        for (i = 0; i < n; i++, tph = tph == 146u ? 0u : tph + 1u) {
            int32_t v = (int32_t)(tph < 74u ? tph : 147u - tph) * 222 - 8200;
            out[2u * i] = v;
            out[2u * i + 1u] = -v;
        }
    }
#endif
    blk_ph[0] = tw_ph; blk_ph[1] = cp_ph; blk_ph[2] = ph_ph; blk_ph[3] = lfo_ph; blk_ph[4] = bar_mot_ph;
    blk_ms = ms0;                                  /* the LFOs at this block's end, for the clock's phase lock (tempo.c) */
    blk_n = blk_n + 1u;
#ifndef DINGHY_HOST
    us = (fm1_ticks() - t0) / 24u;                 /* 24 MHz ticks -> us (a constant divide) */
    prof_us = us;
    if (us > prof_max_us) prof_max_us = us;
    prof_over = us > 5000u ? prof_over + 1u : 0u;  /* two blocks over 5.0 ms of the 5.8 ms: shed a voice (every voice
                                                    * struck with every effect on: ~3.9 ms the Rhodes' 9, ~3.6 ms the
                                                    * bars' 9; tools/hil_cpu.py) */
    if (prof_over >= 2u) { eng_shed_req = 1; prof_over = 0; }
#endif
}


/* ---- the sustain pedal: REC held, or latched by a tap; MIDI CC 64 too. Either holds the dampers off; a CC 64 below 64
 * (the MIDI pedal let up) also releases the latch. */
#define BTN_SUSTAIN BTN_REC
#define SUS_TAP_MS 250u                         /* a press shorter than this is a tap: it latches */
#define NKEYS 27u
static uint8_t sus_held, sus_latched, sus_midi, sus_was_latched;
static uint32_t sus_t;

#if BOARD_SMK37
static uint8_t smk_sus;                         /* the SMK-37's SUSTAIN pad or its pedal jack (frodhes_smk.h) */
static void sustain_update(void) { eng_q(EV_PEDAL, 0, sus_held || sus_latched || smk_sus ? 127u : sus_midi); ui_mark(E_HEADER); }
#else
static void sustain_update(void) { eng_q(EV_PEDAL, 0, sus_held || sus_latched ? 127u : sus_midi); ui_mark(E_HEADER); }
#endif

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

/* MIDI CC: every parameter's CC from the PARAMS table (the panel CC map, after a vintage stage keyboard's: switch CCs 0-42 off,
 * 43-85 A, 86-127 B; TYPE in bands of 32), CC 64 sustain (continuous: half-pedal), 120/123 all off,
 * 119 developer: bypass the effects (tests of the voice engine); the other developer CCs below (2 3 4 8 9 10 11 116) */
static void midi_cc(uint32_t cc, uint32_t v)
{
    uint32_t id;
    if (cc == 64u) {                            /* a MIDI pedal let up also releases the REC latch */
        sus_midi = (uint8_t)v; if (v < 64u) sus_latched = 0;
        sustain_update(); return;
    }
    if (cc == 121u) { eng_bend_reset(); return; }               /* reset all controllers: the pitch bend to 0 */
    if (cc == 119u) { fx_set(FXP_RAW, v >= 64u); return; }
    if (cc == 9u) { bar_budget_force = (uint8_t)v; return; }   /* developer: the bar engine's mode cap (0 = auto) */
    if (cc == 10u) { if (v >= 64u) { fx_reset_req = 1; drv_rst_req = 1; lk_reset = 1; } return; }   /* developer: every LFO / line /
                                                                * noise to its boot state (fx.c; the drive's TYPEs: drive.c), and
                                                                * the FM-1's own beat from here (a synced LFO's cycle starts with it) */
    if (cc == 3u) { ui_testpat = v >= 64u; return; }            /* developer: the colour test pattern (ui.c) */
    if (cc == 2u) { ui_look = (uint8_t)(v < MT_NLOOKS ? v : MT_NLOOKS - 1u); return; }   /* developer: the METERS
                                                        * colour candidate, 0 A 1 B 2 C (ui.c MT_LOOKS; not saved) */
    if (cc == 11u) { ui_cards = (uint8_t)(v < UI_CARDS_N ? v : UI_CARDS_N - 1u); return; }   /* developer: the rows'
                                                        * design variant, 0 TODAY 1 FADER 2 LINE 3 A 4 B 5 C (ui.c; not saved) */
    if (cc == 4u) {                                    /* developer: the battery query (ui.c, for calibrating its
                                                        * thresholds): CC 4 / 5 = the smoothed 10-bit reading (MSB = raw
                                                        * >> 3, LSB = raw & 7), CC 6 = the segments shown (4 = USB power) */
        int32_t r = batt_raw();
        midi_send(0xB0, 4, (uint32_t)r >> 3 & 127u); midi_send(0xB0, 5, (uint32_t)r & 7u); midi_send(0xB0, 6, batt_shown());
        return;
    }
    if (cc == 8u) { batt_sim = (uint8_t)v; return; }           /* developer: a simulated battery reading (v x 8; 0 = the ADC) */
    if (cc == KV_CC_MODE || cc == KV_CC_QUERY) { kv_dev_cc(cc, v); return; }   /* developer: key velocity (keyvel.h) */
    if (cc == 111u) { fx_bypass = v >= 64u; ui_mark(E_HEADER); return; }    /* the FX button's bypass (0.8) */
    if (cc == 114u) { set.usb_fixed = v >= 64u; usb_pre_master = set.usb_fixed; set_dirty = 1; ui_mark(E_CARDS); return; }   /* USB VOL */
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
#ifndef DINGHY_HOST
        usb_health_reply();
#endif
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
    if (cc == 79u) {                                   /* the bar types: each TYPE of the bar engine (TYPE_ENGINE 1) from
                                                        * its TYPE_CC79 value up to the next one's: VIBES 0..95 (every
                                                        * value 0.9.3 used for it up to 95, 0 and 64 included), MARIMBA
                                                        * 96..127 (0.9.4). CC 80 keeps the four Rhodes types as before */
        uint32_t t, pick = 4u;
        for (t = 0; t < N_TYPE_N; t++) if (TYPE_ENGINE[t] == 1u && v >= TYPE_CC79[t]) pick = t;
        type_pick((int32_t)pick);                      /* (a TYPE change: the motor starts) */
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
    if (cc == 85u) {                                   /* the combined CHO/PHA switch: off / chorus / phaser (one) */
        param_set(P_CP_SW, v >= 43u && v < 86u); param_set(P_PH_SW, v >= 86u);
        ui_mark_param(P_CP_SW);
        return;
    }
    if ((cc == 86u || cc == 87u) && PV(P_PH_SW) && !PV(P_CP_SW)) cc += 13u - 86u;   /* the panel map: the phaser's */
    for (id = 0; id < P_COUNT; id++)
        if (PARAMS[id].cc && PARAMS[id].cc == cc) {        /* cc 0: a parameter without a CC */
            int32_t x = (int32_t)v;
            if (id == P_TW_MODE) x = v >= 64u;          /* PAN 0..63, AM 64..127 (records v3: MOTOR, 112..127 on a bar
                                                        * TYPE, is the motor's own CC 47 now) */
            else if (id == P_DRV_SW) x = drv_cc84(v);   /* the drive TYPE by band (params.c DRV_CC84) */
            else if (PARAMS[id].hi == 2u) x = v < 43u ? 0 : v < 86u ? 1 : 2;
            else if (id == P_TYPE) x = (int32_t)(v >> 5);   /* the Rhodes types, as before; bar types: CC 79 */
            else if (PARAMS[id].hi < 127u) x = (int32_t)(v * (PARAMS[id].hi + 1u) >> 7);
            if (id == P_CMP_SW && x && !PV(P_CMP_SW)) cmp_wake(1);   /* CC 106 on: as the panel (ui.c cmp_wake) */
            else param_set(id, x);
            if (id == P_DL_SYNC || id == P_TW_SYNC || id == P_CP_SYNC || id == P_PH_SYNC || id == P_MOT_SYNC ||
                id == P_LFO_SYNC) tempo_sync_publish();   /* (LFO SYNC by CC 77 kept its free rate: the audit) */
            ui_mark_param(id);
            return;
        }
}

/* a note the pedal holds and a voice still sounds (the per-key LEDs under sustain; notes.c ped.sus) */
static int note_sustained(uint32_t n)
{
    return n < 128u && ped.sus[n >> 5] >> (n & 31u) & 1u && eng_note_sounding(n);
}

#if BOARD_SMK37
#include "frodhes_smk.h"   /* the SMK-37's app layer: velocity, the outer keys, pads, faders, wheels, the pedal, its
                            * buttons and lights, the panel check (hooks below) */
#else
#define FR_KEY_VEL(k) kv_vel(k)                  /* the FM-1's keys: their velocity (keyvel.c, TOUCH) */
#endif

/* the settings' auto save (params.c settings_record: the system setup, the sound as last loaded or saved, the tempo
 * set by hand): written 2 s after the record last changed and only when silent (the erase stalls the audio). A knob,
 * a CC or the MIDI clock leaves the record as it is, so they never write; nor restart the wait of a setup change.
 * The save runs beside the main loop, a slice of <= ~0.3 ms a pass (platform.c settings_save_start / _poll): one
 * call held the loop ~100 ms, and a MIDI note 2 s after a Program Change (the preset the record names) came ~100 ms
 * late over USB, later still over TRS (FM-1, hil_midi_parity, 2026-10-25; tests/scripts/pc_stall.txt) */
static settings_t set_pend, set_saved;          /* the record waiting to be written; the one written (or loaded) */
static uint32_t set_pend_t;
static uint8_t set_pend_new;                    /* set_pend is not what the flash holds */
static uint8_t set_saving KV_BSS(set_saving);                       /* a save runs (settings_save_poll) */
static int set_save_busy(void) { return set_saving; }   /* (seq.c seq_save_poll: not while it runs) */
static uint32_t set_chk_t;
static void settings_poll(void)
{
    if (set_saving && settings_save_poll() != 1) set_saving = 0;
    if (set_dirty || fm1_ms - set_chk_t >= 250u) {  /* (also every 250 ms: a setup change that marked nothing) */
        static settings_t r;
        set_dirty = 0;
        set_chk_t = fm1_ms;
        settings_record(&r);
        if (memcmp(&r, &set_pend, sizeof r)) { set_pend = r; set_pend_t = fm1_ms; }
        set_pend_new = memcmp(&set_pend, &set_saved, sizeof r) != 0;
    }
    if (!set_saving && set_pend_new && fm1_ms - set_pend_t > 2000u && eng_idle() && fx_quiet() && tempo_flash_ok() &&
        !seq_save_busy()) {                     /* (the pattern's save runs: next pass; neither busy-finishes the other) */
        set_saved = set_pend;                   /* (written from set_saved: set_pend may move on meanwhile; not while a clock runs) */
        set_saving = settings_save_start(&set_saved, sizeof set_saved) == 1;
        set_pend_new = 0;
    }
}

/* What of the places before 0.9.6 to take (st_mig). No record yet: all of it (the first boot after 0.9.5). A record:
 * only what 0.9.5 saved after the record took those places in (back to 0.9.5 and up again, BUGHUNT #40): each is
 * told by its id, which the record keeps (set.old_id); one that is gone is not newer. 0.9.5 ran last, so what it saved
 * is the newer (the FM-1: its settings share the record's two sectors, so a copy of the record left from before the
 * way back would else win, the 0.9.5 setup and pattern never read) */
static void st_old_scan(void)
{
    uint32_t i, was[3];
    st_mig = 0;
    if (!st_old_ok) return;
    st_oid[0] = settings_old_id(); st_oid[1] = pr_old_id(); st_oid[2] = sq_old_id();
    memcpy(was, set.old_id, sizeof was);
    for (i = 0; i < 3u; i++)
        if (!st_new || (st_oid[i] && st_oid[i] != was[i])) st_mig |= (uint8_t)(1u << i);
}

static void app_main(void)
{
    uint32_t prev = 0, k;
    int link = 0, fresh;
    st_tail = (uint8_t *)pr_user;                   /* the record: the settings, then the user presets (set_store.c) */
    st_new = settings_load(&set, sizeof set) == 0;
#ifdef OLD_OWNER_AT
    st_old_ok = smk_old_ours();                     /* (SMK: the places before 0.9.6 only if ours, frodhes_store.h) */
#else
    st_old_ok = 1;
#endif
    st_old_scan();
    fresh = !st_new && (!st_old_ok || settings_load_old(&set, sizeof set) != 0);
    if (st_new && (st_mig & 1u)) {                  /* 0.9.5's settings, saved after the record took them in */
        uint32_t at = set_at, q = set_seq;
        if (!settings_load_old(&set, sizeof set)) {
            if (set_at != SETTINGS_AT && set_at != SETTINGS_B) set_at = at;   /* (SMK: the save to the record's older
                                                     * copy; the FM-1: to the record's, 0.9.5's copy kept till then) */
            if ((int32_t)(q - set_seq) > 0) set_seq = q;   /* (the save newer than both) */
        }
    }
    if (!fresh && set.ver != 1u && set.ver != SET_VER) fresh = 1;   /* a record this firmware cannot read (another
                                                                     * version): as a first boot, so it plays F01 too */
    if (fresh) params_defaults();
    params_sanitize();
    kv_apply();
    arp_sanitize();
    chord_sanitize();
    seq_sanitize();
    if (set.usb_fixed > 1u) set.usb_fixed = 0;
    if (set.palette >= COLOR_N) set.palette = 0;   /* AMBER (ui.c S_COLOR) */
    if (set.dim >= DIM_N) set.dim = 0;
    if (set.leds > 4u) set.leds = 0;                /* (ui.c LEDS_N: 0 ROOTS 1 OFF 2 PLAYED 3 ALL 4 SCALE) */
    usb_pre_master = set.usb_fixed;
    uart_midi_on = !set.trs_off;
    fm1_led_group_ids(KEY_LED(0), KEY_LED(26));     /* BRIGHTNESS: the keys' LEDs are their own group */
    dim_apply();                                    /* the palette and the LEDs at BRIGHTNESS (set.color is Dinghy's, unused) */
    params_apply_all();
    drv_boot();                                     /* (the DRIVE TYPEs' RAM tables, whole: never in an audio block) */
#ifdef RH_PU_RAM
    rh_pu_boot();                                   /* (rhodes.c: the pickup table to RAM, the O10 probe) */
#endif
    tempo_set10(set.bpm10);
    bpm_hand = set.bpm10;                       /* (the record holds the tempo set by hand) */
    presets_load_bank();
    snd_base_take();                            /* the record's sound: what plays if its preset is gone */
    if (fresh) {                                /* a first boot (or a lost record) plays F01, as the header says */
        preset_load(0);
        set.undo_valid = 0;
    } else if (preset_boot()) {                 /* the preset last loaded or saved, as it is stored (params.c: the
                                                 * knobs turned after it are not kept, on purpose) */
        /* not there: the record's sound runs its synced rates and PLATE AUTO size as a load sets them
         * (0.9.6, BUGHUNT #32: they waited for the next change) */
        rev_relatch();
        tempo_sync_publish();
    }
    snd_base_take();
    set_dirty = 0;
    memcpy(set.old_id, st_oid, sizeof st_oid);  /* (the places before 0.9.6 as this record has them now) */
    settings_record(&set_saved);                /* nothing to write at boot (a converted record: at the next change) */
    set_pend = set_saved;
    seq_load();
    if ((st_old_ok && (st_new ? (st_mig & 1u) != 0u || pr_old : !fresh || pr_old)) || sq_old)   /* the first boot
                                                     * after 0.9.5 (no record yet, its data there; or newer there):
                                                     * the migration. The pattern to its place, then the record (the
                                                     * commit): a cut before it finds no record and migrates again from
                                                     * the old places, which nothing writes */
        if (!sq_old || !seq_save_now()) (void)settings_save(&set_saved, sizeof set_saved);
    ui_goto(PG_PERF);
#if BOARD_SMK37
    smk_init();
#endif
    ui_splash();

    for (;;) {
        uint32_t keys, down, up, go, rel, b, lit = 0;
        int src;
        uint8_t st, d1, d2;
#if FELUCCA_OTA && !defined(DINGHY_HOST)
        if (usb.ota_req) {                      /* an update comes: the auto saves to their end first (the settings'
                                                 * single sector half written would be lost) */
            while (seq_save_busy()) seq_save_poll();
            while (set_saving && settings_save_poll() == 1) ;
            set_saving = 0;
        }
#endif
        platform_poll();

        keys = fm1_in.notes;                    /* bit k: key k (0 = F3 .. 26 = G5) */
        down = keys & ~prev;
        up = prev & ~keys;
        prev = keys;
        go = kv_keys(down, up);                 /* the presses whose velocity is known (keyvel.h: held up to 6 ms; a
                                                 * key let go first plays here, then stops below) */
        for (k = 0; k < NKEYS; k++) {           /* a key stops exactly the notes it started (notes.c sources) */
            if (go >> k & 1u) {
                uint32_t n = scale_key_note(k);
                if (n < 128u) src_on(SRC_KEYS + k, n, FR_KEY_VEL(k), 1);
            }
            if (up >> k & 1u) src_off(SRC_KEYS + k);
        }
#if BOARD_SMK37
        smk_poll();                             /* the outer keys, pads, faders, wheels, the pedal (frodhes_smk.h) */
#endif

        b = fm1_input_edges(&rel);              /* buttons pressed / released since the last call */
        kv_dev_edges(b);
#if BOARD_SMK37
        smk_buttons(&b, &rel);                  /* the SMK's own buttons first (KNOB / FADER BANK, < >) */
#endif
        sustain_button(b, rel);
        ui_input(b, rel);

        if (link && !usb_link()) eng_bend_reset();   /* the USB cable out (or the computer let go): no bend left on */
        link = usb_link();
        while ((src = midi_get(&st, &d1, &d2)) != 0) {   /* USB-MIDI and the TRS jack; the MIDI page's CHANNEL (0 = all) */
            uint8_t ty = st & 0xF0u;
            if (st < 0xF0u && set.midi_ch && (st & 0x0Fu) != set.midi_ch - 1u) continue;
            if (ty == 0x90u && d2) src_on(SRC_MIDI + (d1 & 127u), d1 & 127u, d2, 0);
            else if (ty == 0x80u) src_off_v(SRC_MIDI + (d1 & 127u), d2 & 127u);   /* its release velocity */
            else if (ty == 0x90u) src_off(SRC_MIDI + (d1 & 127u));          /* a note-on at 0: none */
            else if (ty == 0xB0u) midi_cc(d1, d2);
            else if (ty == 0xE0u) eng_bend_set((int32_t)(d1 & 127u) | (int32_t)(d2 & 127u) << 7);   /* the Rhodes */
            else if (st >= 0xF8u) {             /* clock and transport, from USB or TRS (CLOCK EXT) */
                if (tempo_clock(st, (uint32_t)d1 | (uint32_t)d2 << 8, src)) {   /* (d1 d2: the arrival, usb.c /
                                                 * midi_uart.c; 0: CLOCK INT, or the other source of two) */
                    if (st == 0xFAu || (st == 0xFBu && !seq_play)) seq_start_ext();   /* Start (again, too) / Continue */
                    else if (st == 0xFCu && seq_play) seq_stop();                   /* Stop */
                }
            }
            else if (st == 0xF2u) tempo_spp((uint32_t)(d1 & 127u) | (uint32_t)(d2 & 127u) << 7, src);   /* Song Position */
            else if (ty == 0xC0u) preset_load(preset_of_pc(d1));
        }

        for (k = 0; k < NKEYS; k++)             /* LEDs per key: held (the FM-1's key, or its note over MIDI), or its
                                                 * note still rings under the sustain (0.9.4: MIDI notes too; only
                                                 * a note the pedal holds, not one let go before it went down) */
            if (src_active[SRC_KEYS + k]) lit |= 1u << k;
            else {
                uint32_t n = scale_key_note(k);
                if ((n < 128u && src_active[SRC_MIDI + n]) || note_sustained(n)) lit |= 1u << k;
            }
        tempo_lock_poll();                      /* (tempo.c: the synced LFOs locked to the clock's beats) */
        src_poll();
        eng_pump();                             /* (notes.c: events that waited for room in the engine's ring) */
        rev_poll();                             /* (rev_types.c: HALL's lines cleared after a TYPE change) */
        rh_cc_poll();                           /* (rhodes.c: the note-on coefficients, when TUNE / DECAY / RELEASE moved) */
        seq_poll();
        arp_poll();
        ui_leds(lit, tempo_beat_led() || (arp_on() && arp_n && arp_playing < 128u));
#if BOARD_SMK37
        smk_leds();
        if (!smk_panel())                       /* KNOB BANK held: the panel check owns the screen (frodhes_smk.h) */
#endif
        ui_draw();
        presets_save_poll();
        seq_save_poll();

        settings_poll();
    }
}

#ifdef DINGHY_WEB
/* the emulator (web/app.js): which page is shown, so the buttons' captions can say what they do there */
EMSCRIPTEN_KEEPALIVE const char *web_page(void) { return PAGE_TITLE[ui_page]; }
#endif

#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
static void host_shed(void) { eng_shed_req = 1; }   /* tests/host.c "T shed": as app_audio asks it on the FM-1 */

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
        for (i = 0; i < PR_FDN && PR_FDATA[idx][i] != 0xFF; i += 2u) p[PR_FDATA[idx][i]] = PR_FDATA[idx][i + 1u];
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
    int32_t rh[13];
    int32_t bar[16];
    uint8_t want;
} host_snd_t;
static void host_snd(host_snd_t *s)
{
    uint32_t i;
    memset(s, 0, sizeof *s);
    for (i = 0; i < FXP_N; i++) s->fx[i] = i == FXP_RAW ? 0 : fx_p[i];    /* RAW: CC 119, not the panel */
    s->sync[0] = fx_dly_sync_samples; s->sync[1] = lk_nom[LK_TW]; s->sync[2] = lk_nom[LK_CP];   /* (the nominal rates:
                                                    * the phase lock corrects the live ones, tempo.c) */
    s->sync[3] = lk_nom[LK_PH]; s->sync[4] = lk_nom[LK_LFO];
    s->rh[0] = rh_tone_off; s->rh[1] = rh_bark_s; s->rh[2] = rh_bark_g; s->rh[3] = rh_bell; s->rh[4] = rh_ham;
    s->rh[5] = rh_k_decay; s->rh[6] = rh_k_rel; s->rh[7] = rh_thump; s->rh[8] = rh_felt; s->rh[9] = rh_type;
    s->rh[10] = rh_tune_t; s->rh[11] = rh_knock; s->rh[12] = rh_pnz;
    s->bar[0] = bar_pos; s->bar[1] = bar_fam; s->bar[2] = bar_tube; s->bar[3] = bar_mass; s->bar[4] = bar_massi;
    s->bar[5] = bar_nz; s->bar[6] = bar_k_decay; s->bar[7] = bar_k_rel; s->bar[8] = bar_thump; s->bar[9] = bar_felt;
    s->bar[10] = bar_mot_on; s->bar[11] = bar_mot_depth; s->bar[12] = bar_mot_shape; s->bar[13] = (int32_t)bar_mot_inc;
    s->want = eng_want;
    s->bar[14] = bar_mute; s->bar[15] = eng_want_sub;          /* MUTE, the bar type asked for (MARIMBA) */
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
    for (i = 0; i < 13u; i++)
        if (a.rh[i] != b.rh[i]) { snprintf(msg, n, "Rhodes volatile %u is %d, the panel says %d", i, a.rh[i], b.rh[i]); return 1; }
    for (i = 0; i < 16u; i++)
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
    else if (!strcmp(key, "bpm10")) snprintf(v, n, "%u", tempo_bpm10());          /* the tempo shown (tempo.c) */
    else if (!strcmp(key, "setbpm")) snprintf(v, n, "%u", set.bpm10);             /* the tempo setting */
    else if (!strcmp(key, "pubs")) snprintf(v, n, "%u", tempo_pubs);              /* the clock's tempo publishes */
    else if (!strcmp(key, "sqtick")) snprintf(v, n, "%u %u %u %u", sq_tick(), clk_sp, clk_k, sq_k0);   /* (seq.c) */
    else if (!strcmp(key, "twinc")) snprintf(v, n, "%u %u", fx_tw_sync_inc, lk_nom[LK_TW]);   /* the tremolo's rate, nominal */
    else if (!strcmp(key, "lfoinc")) snprintf(v, n, "%u %u", fx_lfo_sync_inc, lk_nom[LK_LFO]);   /* the filter LFO's */
    else if (!strcmp(key, "sqreads")) snprintf(v, n, "%u", sq_host_reads);        /* (seq.c: the pattern's flash reads) */
    else if (!strcmp(key, "bpmmarks")) snprintf(v, n, "%u", tempo_marks);         /* its BPM redraws */
    else if (!strcmp(key, "clklock")) snprintf(v, n, "%u", clk_follows());        /* the MIDI clock is followed */
    else if (!strcmp(key, "beatsmp")) snprintf(v, n, "%u", beat_samples());       /* samples a beat */
    else if (!strcmp(key, "dlysync")) snprintf(v, n, "%u", fx_dly_sync_samples);  /* the synced delay time */
    else if (!strcmp(key, "twph")) snprintf(v, n, "%u", tw_ph);                   /* the tremolo's LFO phase (Q32) */
    else if (!strcmp(key, "cpph")) snprintf(v, n, "%u", cp_ph);                   /* the chorus' */
    else if (!strcmp(key, "phph")) snprintf(v, n, "%u", ph_ph);                   /* the phaser's */
    else if (!strcmp(key, "lfoph")) snprintf(v, n, "%u", lfo_ph);                 /* the filter LFO's */
    else if (!strcmp(key, "motph")) snprintf(v, n, "%u", bar_mot_ph);             /* the vibraphone motor's */
    else if (!strcmp(key, "wavn")) snprintf(v, n, "%u", wav_n);                   /* (host.c: the frames rendered) */
    else if (!strcmp(key, "bend")) snprintf(v, n, "%d", (int)eng_bend14 - 8192);   /* the pitch bend (-8192 .. 8191) */
    else if (!strcmp(key, "rtpend")) snprintf(v, n, "%u", (unsigned)rh_rt_pend);   /* voices still to retune (rhodes.c) */
    else if (!strcmp(key, "rtbusy")) snprintf(v, n, "%d", rh_rt_pend != 0u);        /* a retune still under way */
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
                                                        * CAP STYLE N LIT (row x; UX 0 0: no unit; STYLE ui.c
                                                        * CS_LETTER, N / LIT a selector's options and the one lit) */
        snprintf(v, n, "%u %u %d %d %d %d %d %d %d %d %c %d %d", ui_row_drawn[a].fit, ui_row_drawn[a].shortf,
                 ui_row_drawn[a].fw, ui_row_drawn[a].vx0, ui_row_drawn[a].vx1, ui_row_drawn[a].ux0, ui_row_drawn[a].ux1,
                 ui_row_drawn[a].lx1, (int)CD_BASE, AF_L_CAP_H, ui_row_drawn[a].style ? ui_row_drawn[a].style : '-',
                 ui_row_drawn[a].sn, ui_row_drawn[a].si);
    else if (!strcmp(key, "glyphmiss")) {              /* drawn characters without a glyph (aatext.c aa_h_note);
                                                        * printed: "GLYPHS S|L" and each character measured or drawn
                                                        * as hex code + 1 measured / 2 drawn / 3 both */
        for (a = 0; a < 2u; a++) {
            printf("GLYPHS %c", a ? 'L' : 'S');
            for (i = 32; i < 256u; i++) if (aa_h_used[a][i]) printf(" %02X%u", (unsigned)i, aa_h_used[a][i]);
            printf("\n");
        }
        snprintf(v, n, "%u", aa_h_miss);
    }
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
    else if (!strcmp(key, "fxdrv")) snprintf(v, n, "%d", (int)fx_p[FXP_DRV_SW]);   /* the drive TYPE fx.c runs */
    else if (!strcmp(key, "fxrv")) snprintf(v, n, "%u%u%u", rv_cur, rv_running, rv_fade);   /* the reverb TYPE that
                                                        * runs (0: waiting for HALL's lines), its tail, the fade */
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
    else if (!strcmp(key, "savepend")) snprintf(v, n, "%u", pr_save_pending);   /* a preset save waiting for silence */
    else if (!strcmp(key, "undo")) snprintf(v, n, "%u", set.undo_valid);
    else if (!strcmp(key, "dirty")) snprintf(v, n, "%u", set_pend_new);   /* a setup change waits to be saved */
    else if (!strcmp(key, "saving")) snprintf(v, n, "%u", set_saving);   /* the settings' save runs (settings_poll) */
    else if (!strcmp(key, "ledduty")) snprintf(v, n, "%u %u", fm1_led_duty[0], fm1_led_duty[1]);   /* DISPLAY BRIGHTNESS: the keys', the buttons' lit duty /16 */
    else if (!strcmp(key, "bars")) snprintf(v, n, "%d", eng_is_bars());
    else if (!strcmp(key, "voices")) snprintf(v, n, "%u", host_bits(eng_cur == ENG_BARS ? bar_active_bits : rh_active_bits));
    else if (!strcmp(key, "held")) snprintf(v, n, "%u", host_bits(eng_cur == ENG_BARS ? bar_held_bits : rh_held_bits));
    else if (key[0] == 'v' && key[1] == 'n' && key[2] >= '0' && key[2] <= '9') {   /* vnN: voices sounding note N
                                                           * (alive, not fading out for a steal; BUGHUNT #21) */
        uint32_t nn = (uint32_t)atoi(key + 2) & 127u;
        for (i = c = 0; i < (eng_cur == ENG_BARS ? BAR_NV : RH_NV); i++)
            c += eng_cur == ENG_BARS ? bv[i].alive && bv[i].note == nn && bv[i].fade == 32768
                                     : rv[i].alive && rv[i].note == nn && rv[i].fade == 32768;
        snprintf(v, n, "%u", c);
    }
    else if (!strcmp(key, "pitches")) { for (i = c = 0; i < 128u; i++) c += out_ref[i] != 0; snprintf(v, n, "%u", c + (arp_playing < 128u)); }   /* held by a source or the arpeggiator */
    else if (!strcmp(key, "srcs")) { for (i = c = 0; i < SRC_N; i++) c += src_active[i]; snprintf(v, n, "%u", c); }
    else if (!strcmp(key, "refs")) { for (i = c = 0; i < 128u; i++) c += out_ref[i]; snprintf(v, n, "%u", c); }
    else if (!strcmp(key, "quiet")) snprintf(v, n, "%d", eng_quiet() && fx_quiet());
    else if (!strcmp(key, "batt")) snprintf(v, n, "%u", batt_shown());
    else if (!strcmp(key, "battraw")) snprintf(v, n, "%d", batt_raw());
    else if (!strcmp(key, "lastvel")) snprintf(v, n, "%u", (unsigned)kv_last_vel());   /* the last FM-1 key's velocity */
    else if (!strcmp(key, "touch")) snprintf(v, n, "%s", KV_TOUCH_NAME[kv_touch()]);   /* SETUP TOUCH */
    else if (!strcmp(key, "drops")) snprintf(v, n, "%u", (unsigned)(eq_drop + ev_drop + engq_drop));   /* events lost */
#if BOARD_SMK37
    else if (!smk_host_state(key, v, n)) {}            /* the SMK-37's own (frodhes_smk.h) */
#endif
    else return 1;
    return 0;
}
static void host_dump(void)
{
    static const char *const K[] = {"header", "preset", "page", "vpage", "hint", "msg", "oct", "bpm", "sus", "byp", "arp", "hold",
                                    "arpn", "chord", "play", "rec", "notes", "savemode", "undo", "dirty", "ledduty", "bars", "voices", "held", "pitches",
                                    "srcs", "refs", "quiet", "drops", "fxdrv", "label0", "card0", "label1", "card1", "label2", "card2",
                                    "label3", "card3", "bpm10", "setbpm", "pubs", "bpmmarks", "sqtick", "sqreads", "twinc", "lfoinc", "clklock", "beatsmp", "dlysync", "twph", "cpph", "phph", "lfoph", "motph", "wavn"};
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
    for (i = 0; i < 41u; i++) printf("@ %u glow.%u %u\n", fm1_ms, i, host_glow[i]);
    for (i = 1; i <= PR_NU; i++) { snprintf(key, sizeof key, "u.%u", i); host_state(key, v, sizeof v); printf("@ %u %s %s\n", fm1_ms, key, v); }
#if BOARD_SMK37
    {                                                   /* the SMK-37's own (frodhes_smk.h smk_host_state) */
        static const char *const S[] = {"bend", "wbend", "modw", "fbank", "fader.0", "fader.1", "fader.2", "fader.3",
                                        "smksus", "pedal", "ext", "leddark", "panel", "btnleds", "mastq"};
        for (i = 0; i < sizeof S / sizeof S[0]; i++) { host_state(S[i], v, sizeof v); printf("@ %u %s %s\n", fm1_ms, S[i], v); }
    }
#endif
}
#endif
