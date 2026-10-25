/* SPDX-License-Identifier: GPL-3.0-only */
/* FM-1 key velocity: the interfaces between its four parts (one compilation unit: frodhes.c includes this file
 * before platform.c, so the scan tick in hal/fm1_input.h can call into the parts defined later).
 *
 * After charlesvestal/fm1-velocity (https://github.com/charlesvestal/fm1-velocity, commit 8f62ce0, GPL-3.0-only,
 * by Charles Vestal): the method (a key's driven reading once its rubber dome has landed), the drive, the deferred
 * conversion between two ticks and the log curve are his. The scheduler (variant B) and everything below are
 * Frodhes' own.
 *
 *   part            file                  runs in                  owns (each global in its own .bss section)
 *   converter       hal/fm1_adc.h         tick (ISR) + main loop   fm1_adc_sched  (.bss.fm1_adc_sched)
 *   key rows        hal/fm1_keyadc.h      tick (ISR)               fm1_ka         (.bss.fm1_ka)
 *   key velocity    src/keyvel.c          tick (kv_isr_*) + main   kv (ISR <-> main, .bss.kv), kvm (main, .bss.kvm)
 *   TOUCH setting   src/params.c, ui.c    main loop                set.vel_curve (the settings record)
 *
 * Own sections: a new global that the compiler merges into the others' block moves the Rhodes' hot loops
 * (src/engine.c eq, hal/fm1_input.h fm1_led_tab); every new ISR global gets __attribute__((section(".bss.<name>")))
 * on the FM-1 (KV_BSS(name) below), ./build.sh's hot-loop checks (23/0/0, 30/0/2, 100/1/8, 59/1/6) must not move.
 *
 * The tick (TIMER5, 10 kHz, priority 4: above the audio ISR) runs fm1_input_tick() once per column; a frame is 11
 * ticks (1.1 ms). Per tick, in this order (hal/fm1_input.h's two hooks):
 *   1. FM1_INPUT_ADC_TAKE(p) = fm1_ka_take(p), before column p's rows are read, only while a conversion runs
 *        (fm1_adc_sched_busy(): the idle tick's one load and branch): the row driven last tick is restored (an input with its pull-up: RESTORE BEFORE READ, else held keys never
 *        debounce and a row driven low reads OCT- / OCT+ pressed), then fm1_adc_sched_take() hands over last tick's
 *        conversion: MASTER's / the battery's go to their cache inside fm1_adc.h, a key's comes back here and goes to
 *        kv_isr_sample() (or to the self-test's counters).
 *   2. the rows of column p are read (fm1_in.raw[p]), the 595 shifts to column n.
 *   3. FM1_INPUT_ADC_START(p, n, lit) = fm1_ka_start(p, n, lit), column n just latched, lit = its LED lines.
 *        (After it, on column 1's tick only, the 595 takes 15 bits of column 2's word unless FM1_INPUT_ADC_BUSY() =
 *        fm1_ka_busy(): a conversion runs, and the pins stay still until it is taken.)
 *        Idle (kv_isr_busy(p, n, raw) 0 and NORMAL): only columns 0 / 1 call fm1_adc_sched_slot (MASTER, the
 *        battery); return lit. Else, out of line (fm1__ka_start):
 *        kv_isr_want(p, raw) ? kv_isr_col(p, raw, notes) (arm a key whose contact just closed); then
 *        fm1_adc_sched_slot(n) ? (MASTER's / the battery's tick: no key conversion) :
 *        kv_isr_pick(n, odd) >= 0 (one row of column n, chosen in O(1): no loop over the keys) ? drive that row,
 *        fm1_adc_key_start(row), and return 0 (the column's LEDs dark for this one tick) : return lit.
 *   At most one conversion runs per tick; it is taken at the next tick (~60 us at FM1_ADC_CFG_KEY).
 * The main loop (app_main) then sees the debounced press in fm1_in.notes as today, and kv_keys() holds it until its
 * velocity is known (at most KV_WAIT_MS), or plays it at once on the fallback.
 *
 * Velocity off (TOUCH FIXED, the host, these stubs): kv_keys() returns the presses unchanged and kv_vel() is
 * set.key_vel, exactly as before; the renders stay byte-identical. */
#pragma once
#include <stdint.h>

#ifdef DINGHY_HOST
#define KV_BSS(name)
#else
#define KV_BSS(name) __attribute__((section(".bss." #name)))
#endif
/* KV_FM1KA 1: the FM-1's key rows and converter (hal/fm1_keyadc.h) are there. 0 on the host and on the SMK-37, whose
 * keybed gives each key its velocity (frodhes_smk.h smk_vel: the same curve, kv_upow / kv_vmap below); kv_keys then
 * passes every press at once and the TOUCH card works as on the FM-1 */
#if !defined(DINGHY_HOST) && !(defined(BOARD_SMK37) && BOARD_SMK37)
#define KV_FM1KA 1
#else
#define KV_FM1KA 0
#endif

/* ---- the keys (fm1_in.notes bit k = note key k, 0 = F3 .. 26 = G5) */
#define KV_NK 27u                       /* note keys */
#define KV_ANALOG 0x000FFFFFu           /* keys 0..19 (F3..C5): rows PA7 / PA8, which are converter pins too */
#define KV_TOP 0x07F00000u              /* keys 20..26 (C#5..G5): row PA6, no converter: CONTEXT velocity */
#define KV_NCOL 11u                     /* scan columns (FM1_NCOL) */
#define KV_ROW_PA7 0u                   /* "row" below: 0 = PA7 (converter ch 0), 1 = PA8 (ch 1) */
#define KV_ROW_PA8 1u
#define KV_WAIT_MS 6u                   /* the longest a pressed analog key waits for its reading (then CONTEXT) */

/* ---- TOUCH (SETUP K4). UI order; set.vel_curve stores it so that 0 (every record before key velocity) is
 * MEDIUM, the default after the update: vel_curve = (touch + 3) & 3, touch = (vel_curve + 1) & 3; params_sanitize
 * clamps vel_curve > 3 to 0. Presets are unchanged. */
enum { KV_LIGHT, KV_MEDIUM, KV_HEAVY, KV_FIXED, KV_NTOUCH };
static uint32_t kv_touch(void);                 /* params.c: TOUCH, KV_LIGHT..KV_FIXED */
static void kv_touch_store(uint32_t touch);     /* params.c: set it (the settings save as for any other setting);
                                                 * the caller then calls kv_apply() */
static const char *const KV_TOUCH_NAME[KV_NTOUCH];   /* keyvel.c: "LIGHT" "MEDIUM" "HEAVY" "FIXED" (<= 6 chars) */

/* ---- key velocity, main loop side (keyvel.c; called from frodhes.c app_main and ui.c; never from an ISR) */
static void kv_apply(void);                     /* TOUCH or the hardware changed: after params_sanitize at boot and
                                                 * after every kv_touch_store. Sets kv.on (arm keys in the ISR) */
static uint32_t kv_keys(uint32_t down, uint32_t up);   /* once per main-loop pass with the debounced note-key edges
                                                 * (bit k = key k, as fm1_in.notes): returns the presses to start now.
                                                 * A press is held until its reading came or KV_WAIT_MS passed; a key
                                                 * let go first still plays (in this pass's return, before its up is
                                                 * handled), never dropped, never left hanging. TOUCH FIXED: = down */
static uint32_t kv_vel(uint32_t k);             /* 1..127: the velocity of key k's press that kv_keys just returned
                                                 * (FIXED: set.key_vel; KEY VEL is a medium press's velocity) */
static uint32_t kv_last_vel(void);              /* the last FM-1 key's velocity (host state "lastvel"; 0 none yet) */

/* ---- key velocity, ISR side (keyvel.c; called only from fm1_keyadc.h in the tick: short, no loops over the
 * keys, no calls out, no flash / screen / MIDI). p / n are scan columns 0..10, row KV_ROW_*, raw = fm1_in.raw[p]
 * (bit 3 PA7, bit 4 PA8: 1 = closed), notes = fm1_in.notes (the debounced keys). */
static inline int kv_isr_want(uint32_t p, uint32_t raw);    /* column p has anything for kv_isr_col (one load +
                                                 * branch when idle: an analog row closed with TOUCH on, or a key busy) */
static void kv_isr_col(uint32_t p, uint32_t raw, uint32_t notes);   /* column p just read: arm a key that closed
                                                 * while its note is off; let go an armed key that opened */
static inline uint32_t kv_isr_pend(uint32_t n); /* column n has a key that wants a sample (bit r = row r) */
static inline uint32_t kv_isr_busy(uint32_t p, uint32_t n, uint32_t raw);   /* 0: kv_isr_want(p, raw) and
                                                 * kv_isr_pend(n) are both 0 (the tick's idle test, three loads) */
static int32_t kv_isr_pick(uint32_t n, uint32_t odd);   /* the row of column n to sample this tick, -1 none (odd:
                                                 * the frame's parity, which row when both want one) */
static void kv_isr_sample(uint32_t n, uint32_t row, uint32_t v);   /* a driven reading 0..1023 of (column n, row) */
static int kv_isr_defer(uint32_t n, uint32_t row);   /* 1: skip this frame once (blanking would swallow a sparse
                                                 * LED frame, BRIGHTNESS 1); 0: sample now */
static void kv_isr_storm(void);                 /* developer STORM: fake landings on 8 keys (their results dropped) */

/* ---- developer protocol (MIDI CC in, frodhes.c midi_cc -> kv_dev_cc; tools/hil_cpu.py --keyadc, hil_keyvel.py;
 * nothing saved, every mode is lost at power-off) */
#define KV_CC_MODE 41u                  /* CC 41 v: a command, KV_DEV_* below */
#define KV_CC_QUERY 42u                 /* CC 42 g: reply items g*8 .. g*8+7, each as CC 38 item, CC 39 value >> 7,
                                         * CC 40 value & 127 (14 bits, saturated) */
#define KV_CC_ITEM 38u
#define KV_CC_HI 39u
#define KV_CC_LO 40u
enum {                                  /* CC 41 values (0..4: the scan's mode; each one clears the counters) */
    KV_DEV_NORMAL = 0,                  /* variant B: only keys whose contact just closed */
    KV_DEV_OFF = 1,                     /* no key conversions (MASTER and the battery slots only): --keyadc off */
    KV_DEV_STRESS = 2,                  /* a key conversion, drive and dark LEDs on every free tick: --keyadc stress */
    KV_DEV_STORM = 3,                   /* 8 fake landings every 32 frames */
    KV_DEV_SELFTEST = 4,                /* every sampled row driven low: each must read 0 (items 9, 10) */
    KV_DEV_NMODE = 5,
    KV_DEV_REPORT_ON = 8,               /* every press's reading out: CC 33 key, 34 raw >> 3, 35 (raw & 7) | src << 3
                                         * (src 0 reading, 1 timed out, 2 let go first, 3 a top key, 4 FIXED / none),
                                         * 36 its wait in ms; then the note-on with its velocity */
    KV_DEV_REPORT_OFF = 9,
    KV_DEV_DRIVE1 = 16,                 /* 16..19: DRIVE 1..4 (1 the weakest driver, the default) */
    KV_DEV_PULL = 20,                   /* no drive: the 10k pull-up's reading */
    KV_DEV_WAIT1 = 24,                  /* 24 / 25: samples per landing 1 / 2 (the lowest is kept) */
    KV_DEV_FLOORS = 32,                 /* every key's learned floor back to its default */
};
enum {                                  /* CC 42 items (who answers) */
    KV_IT_MODE = 0, KV_IT_DRIVE = 1,    /* fm1_keyadc.h (DRIVE 0 = pull-up) */
    KV_IT_WAIT = 2, KV_IT_TOUCH = 3,    /* keyvel.c */
    KV_IT_CONVS = 4, KV_IT_TIMEOUTS = 5, KV_IT_BATT_TO = 6, KV_IT_MASTER = 7, KV_IT_BATT = 8,   /* fm1_adc.h */
    KV_IT_ST_PA7 = 9, KV_IT_ST_PA8 = 10, KV_IT_ST_N = 11,  /* fm1_keyadc.h: the self-test's highest reading, count */
    KV_IT_NOTES = 12, KV_IT_BUTTONS = 13, KV_IT_ANALOG = 14, KV_IT_FALLBACK = 15,   /* keyvel.c, since the mode */
    KV_IT_TICK_MAX = 16, KV_IT_FRAMES = 17,  /* fm1_keyadc.h: the tick's longest, 0.1 us (an FM1_KA_PROF build;
                                         * it restarts on the read); scan frames */
    KV_IT_FLOOR0 = 32,                  /* 32..51: keyvel.c, the learned floors of keys 0..19 */
    KV_IT_N = 64,
};
static void kv_dev_cc(uint32_t cc, uint32_t v);      /* keyvel.c: CC 41 / 42 (main loop) */
static void kv_dev_edges(uint32_t buttons);          /* keyvel.c: the button presses this pass (item 13: a soak sees
                                                      * a spurious OCT- / OCT+ edge) */

/* ---- the key rows' scan part (hal/fm1_keyadc.h; FM-1 only) */
#if KV_FM1KA
static inline void fm1_ka_take(uint32_t p);
static inline uint32_t fm1_ka_start(uint32_t p, uint32_t n, uint32_t lit);   /* returns the LED lines for column n */
static uint32_t fm1_ka_dev_item(uint32_t item);      /* KV_IT_* it or fm1_adc.h answers (main loop) */
static void fm1_ka_dev_mode(uint32_t v);             /* KV_DEV_ modes 0..4 and DRIVE 16..20 (main loop) */
#define FM1_INPUT_ADC_TAKE(p) fm1_ka_take(p)
#define FM1_INPUT_ADC_START(p, n, lit) fm1_ka_start(p, n, lit)
static inline uint32_t fm1_ka_busy(void);            /* a conversion runs (the tick keeps the 595's pins still) */
#define FM1_INPUT_ADC_BUSY() fm1_ka_busy()
#endif
#ifdef DINGHY_HOST
/* tests/host.c "T vkey K RAW": key K's next press lands with driven reading RAW (as the ISR would hand it over;
 * the host has no converter, so a key without one plays at once at CONTEXT) */
static void kv_host_vkey(uint32_t k, uint32_t raw);
#endif
