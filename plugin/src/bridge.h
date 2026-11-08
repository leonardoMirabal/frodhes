/* SPDX-License-Identifier: GPL-3.0-only */
/* The desktop plugin's bridge (private plan "next release" B2/B3).
 *
 * Two layers:
 *   the slot     one copy of the app (firmware/src/frodhes.c over web/web_hal.c) compiled into its own object
 *                (plugin/src/engine_unit.c with FRP_SLOT = 0 .. 2 x FRP_SLOTS - 1, plugin/cmake/slots.cmake). Everything
 *                in it is static except its table, frp_slot_<N> (frp_slot_api_t below): independent apps in one binary,
 *                no runtime file copies. Two pools: slots 0..15 HQ (the Move's build: 16 voices, the float paths),
 *                16..31 CLASSIC (the FM-1's build: 10 voices, the integer paths). The per-slot glue (time, queue,
 *                FIFO, state, presets, MIDI learn, settings) is slot_bridge.h.
 *   the instance a plugin instance (plugin/src/bridge.c): it holds one slot of each pool and plays the one its ENGINE
 *                names (a switch hands the state over and lets the other ring out), maps the host's frames and events
 *                onto the app's milliseconds, resamples for other rates and hands out float. The user presets (files)
 *                are bridge.c's too.
 *
 * Time (as the Move, move/move_bridge.h, and the host build, tests/host.c): the app runs on host milliseconds. Each
 * step: ms + 1, the events due by then into the app's MIDI ring / applied, every 256 frames owed app_audio(256) into
 * the slot's FIFO, then one pass of app_main() (a coroutine) up to its next platform_poll(). A 44.1 kHz render of a
 * MIDI script is therefore the host build's render of the same script, sample for sample (Gate D, tools/gate_d.py).
 *
 * Threads (CLAP's): boot / reset / state reads on the main thread; put / step / read on the audio thread; drain on
 * the main thread only while the instance is not processing (CLAP's flush outside processing). Never both at once
 * for one slot (the instance owns its slots). The main thread's own writes while the audio thread runs (the plugin
 * UI) go through the instance's UI queue, which the audio thread moves into the slot. */
#ifndef FRODHES_PLUGIN_BRIDGE_H
#define FRODHES_PLUGIN_BRIDGE_H
#include <stdint.h>
#include "../gen/params_map.h"
#include "../frp_ui_iface.h"           /* FRP_VEL_*, FRP_TEMPO_* */

#define FRP_SLOTS 16u                    /* a pool's slots: the instances that can play at once */
#define FRP_FS 44100u                    /* the app's rate: always */
#define FRP_SETTINGS_N 256u              /* params.c settings_t */
#define FRP_BASE_N 80u                   /* params.c P_SLOTS */
#define FRP_STATE_MAX 2048               /* the state's JSON text (slot_bridge.h SB_STATE_N) */
#define FRP_PNAME 32                     /* a preset name with its terminator */
#define FRP_NFACTORY 57u                 /* F01..F19 (presets.c) + F20..F57 (move/move_presets.h) */
#define FRP_POOL_HQ 0
#define FRP_POOL_CLASSIC 1

enum { FRP_EV_MIDI = 1, FRP_EV_PARAM, FRP_EV_RESERVED3, FRP_EV_RESET, FRP_EV_CFG, FRP_EV_PRESET, FRP_EV_UNDO,
       FRP_EV_LEARN, FRP_EV_RELEASE, FRP_EV_UINOTE };
/* FRP_EV_CFG: a = the setting, v = its value */
enum { FRP_CFG_MIDI_CH, FRP_CFG_VEL, FRP_CFG_FIXVEL, FRP_CFG_PGM, FRP_CFG_TEMPO, FRP_CFG_BPM, FRP_CFG_SYNC_ALL,
       FRP_CFG_SYNC_DIV };
/* FRP_EV_LEARN: a = the operation, v = the learn code (an app id, or 200 + k for the own parameter 0x10000 + k) */
enum { FRP_LEARN_START, FRP_LEARN_CANCEL, FRP_LEARN_FORGET, FRP_LEARN_FORGET_ALL };
#define FRP_LEARN_OWN 200u

/* what the main thread may read of a slot: published by the audio side after each step (a sequence lock) */
typedef struct {
    uint32_t rev;                        /* bumps on every change the app made or took (a parameter, a state) */
    uint8_t pv[FRP_NPARAM_APP];          /* the parameters' values (params.c PV) */
    uint8_t engine;                      /* FRP_ENGINE_CLASSIC / _HQ: the slot's pool */
    uint8_t voices;                      /* the slot's voice count (rhodes.c RH_NV) */
    uint8_t tune;                        /* TUNE + 50 (0..100) */
    uint8_t sustain, bypass;             /* the dampers held off; every effect bypassed */
    uint8_t edited, can_undo, quiet;     /* the sound differs from its preset; an undo copy exists; nothing sounds */
    int16_t pidx, last_fact;             /* the preset playing (0..56 factory, -1 a user file / none); the last factory */
    char pname[FRP_PNAME];
    uint8_t midi_ch, touch, key_vel;     /* the settings record's CHANNEL, TOUCH (keyvel.h order), KEY VEL */
    uint8_t pgm, tempo_src, sync_all, sync_div;
    uint16_t bpm10, tempo10, host10;     /* the internal tempo; the tempo now; the host's (0 none) */
    int16_t learning;                    /* the learn code waiting for a CC, -1 */
    uint8_t learn[128];                  /* each CC's learn code + 1 (0 none) */
    int32_t gr_q12;                      /* the compressor's gain reduction, log2 Q12 (fx.c fx_cmp_gr), 0 when off */
} frp_snap_t;

typedef struct {
    uint32_t ms, steps, blocks, midi_in, midi_out, params, q_full, cc_drop, state_ok, state_bad, ring_wait;
} frp_stats_t;

typedef struct frp_slot_api {
    int (*boot)(int engine);             /* main thread: the app's first pass (once a slot; later calls: 0 at once) */
    int (*booted)(void);
    void (*reuse)(int engine);           /* main thread, inactive (or the audio thread that owns it): a fresh start:
                                          * the boot's state, all notes off, the effects' state cleared */
    int (*put)(int kind, uint32_t at_ms, uint32_t a, int32_t v);   /* queue an event (0 ok, -1 full); at_ms 0 = at once.
                                          * MIDI / UINOTE: a = st | d1 << 8 | d2 << 16. PARAM: a = id, v = value.
                                          * CFG, LEARN: above. PRESET: v = factory index. UNDO. RESET: all notes off
                                          * and the effects' state cleared. RELEASE: every note let go (a tail) */
    void (*step)(void);                  /* audio thread: one host millisecond */
    void (*drain)(void);                 /* main thread, inactive: apply every queued event now (no time passes) */
    uint32_t (*avail)(void);             /* frames in the FIFO */
    void (*read)(int32_t *lr, uint32_t n);   /* audio thread: n <= avail() frames out of the FIFO (app_audio's own
                                          * int32 output, MASTER at unity, no clip) */
    uint32_t (*now)(void);               /* the host ms stepped */
    void (*snapshot)(frp_snap_t *out);   /* any thread */
    int (*state_get)(char *buf, int n);  /* any thread (from the last published record): the length, -1 too small */
    int (*state_set)(const char *json);  /* any thread: parsed into the mailbox, applied by the next step / drain */
    void (*set_tempo)(uint32_t bpm10);   /* the host's tempo x 10 (0: none, 120 BPM) */
    void (*stats)(frp_stats_t *out);
    int (*check_params)(void);           /* the generated map against the app's PARAMS[]: the mismatches (0 ok) */
    int (*user_put)(const uint8_t *vals, const char *name, int saved);   /* any thread: a user preset's values
                                          * (FRP_NPARAM_APP bytes) into the mailbox, loaded by the next step / drain;
                                          * saved = 1 (vals unused): the sound playing was saved as name. -1 busy */
    int (*fact_info)(uint32_t i, char *name, uint32_t n, uint8_t *vals);   /* any thread: factory preset i's name
                                          * ("SUITCASE") and values (FRP_NPARAM_APP bytes; either may be NULL) */
    uint32_t (*room)(void);              /* the thread that puts: how many more events fit the queue now */
} frp_slot_api_t;

#define FRP_SLOT_DECL(n) extern const frp_slot_api_t frp_slot_##n;
FRP_SLOT_DECL(0) FRP_SLOT_DECL(1) FRP_SLOT_DECL(2) FRP_SLOT_DECL(3) FRP_SLOT_DECL(4) FRP_SLOT_DECL(5)
FRP_SLOT_DECL(6) FRP_SLOT_DECL(7) FRP_SLOT_DECL(8) FRP_SLOT_DECL(9) FRP_SLOT_DECL(10) FRP_SLOT_DECL(11)
FRP_SLOT_DECL(12) FRP_SLOT_DECL(13) FRP_SLOT_DECL(14) FRP_SLOT_DECL(15)
FRP_SLOT_DECL(16) FRP_SLOT_DECL(17) FRP_SLOT_DECL(18) FRP_SLOT_DECL(19) FRP_SLOT_DECL(20) FRP_SLOT_DECL(21)
FRP_SLOT_DECL(22) FRP_SLOT_DECL(23) FRP_SLOT_DECL(24) FRP_SLOT_DECL(25) FRP_SLOT_DECL(26) FRP_SLOT_DECL(27)
FRP_SLOT_DECL(28) FRP_SLOT_DECL(29) FRP_SLOT_DECL(30) FRP_SLOT_DECL(31)

/* the user presets' cache for program changes 16..31 (bridge.c; the slots call it on the audio thread): the k-th
 * user preset's values and name, 0 ok, -1 none there. Lock-free (a sequence lock over a fixed table). */
int frp_user_get(uint32_t k, uint8_t *vals, char *name);

/* ---- the instance (bridge.c) */
#define FRP_LAT_MS 10u                   /* the events' schedule ahead of the app (and the output's pre-roll): 10 ms */
typedef struct frp_inst frp_inst_t;
frp_inst_t *frp_inst_new(void);          /* main thread: claims a slot of its ENGINE's pool (none: 16 in use, plays
                                          * silence); the other pool's slot at the first switch */
void frp_inst_free(frp_inst_t *in);
/* an ENGINE switch on the audio side waits for the other pool's slot: frp_inst_wants_main (any thread) says once that
 * the host should call the main thread (clap request_callback), where frp_inst_main takes the slot (0 nothing to do,
 * 1 taken: the switch at the next block, -1 none free: the ENGINE stays as it was) */
int frp_inst_wants_main(frp_inst_t *in);
int frp_inst_main(frp_inst_t *in);
int frp_inst_slot(const frp_inst_t *in); /* the slot playing, -1 none */
int frp_inst_activate(frp_inst_t *in, double rate);   /* main thread: 0 ok, -1 a rate outside 1 .. 768 kHz */
void frp_inst_deactivate(frp_inst_t *in);
uint32_t frp_inst_latency(const frp_inst_t *in);       /* output frames at the host's rate */
void frp_inst_reset(frp_inst_t *in);     /* audio thread: all notes off, effects cleared (CLAP reset) */
/* events of the current block (audio thread; time = the frame in the block, ascending), then render */
void frp_inst_midi(frp_inst_t *in, uint32_t time, uint8_t st, uint8_t d1, uint8_t d2);
void frp_inst_param(frp_inst_t *in, uint32_t time, uint32_t id, double value);
void frp_inst_render(frp_inst_t *in, float *l, float *r, uint32_t n, uint32_t tempo_bpm10);
/* an event for the slot playing, at once: the audio thread between blocks, or the main thread while not processing
 * (then followed by frp_inst_drain) */
void frp_inst_put_now(frp_inst_t *in, int kind, uint32_t a, int32_t v);
void frp_inst_drain(frp_inst_t *in);     /* not processing: apply what is queued now (and a pending ENGINE switch) */
int frp_inst_user_put(frp_inst_t *in, const uint8_t *vals, const char *name, int saved);
/* the main thread outside processing (CLAP flush / state while inactive) */
void frp_inst_param_now(frp_inst_t *in, uint32_t id, double value);
int frp_inst_get(const frp_inst_t *in, uint32_t id, double *value);   /* from the slot's published values */
int frp_inst_state_get(const frp_inst_t *in, char *buf, int n);
int frp_inst_state_set(frp_inst_t *in, const char *json);   /* applied now (inactive) or by the next block */
uint32_t frp_inst_rev(const frp_inst_t *in);
int frp_inst_snap(const frp_inst_t *in, frp_snap_t *out);   /* the slot's published values: 0, -1 no slot */
int frp_inst_engine(const frp_inst_t *in);                  /* the ENGINE asked (the slot follows at the next block) */
void frp_inst_stats(const frp_inst_t *in, frp_stats_t *st, uint32_t *late);
int frp_fact_info(uint32_t i, char *name, uint32_t n, uint8_t *vals);   /* any thread: a factory preset */
void frp_inst_meters(frp_inst_t *in, float peak[2]);        /* any thread: the output's peak since the last call */
void frp_inst_notes(const frp_inst_t *in, uint8_t notes[128]);   /* any thread: the keys held (velocity) */

/* ---- the user presets (bridge.c; main thread): one file a preset in the user's folder */
#define FRP_USER_MAX 1024
int frp_user_count(void);
int frp_user_info(int k, char *name, uint32_t n);            /* 0 ok */
int frp_user_values(int k, uint8_t *vals);                   /* from the file (read now): 0 ok */
int frp_user_find(const char *name);                         /* its index, -1 */
int frp_user_save(const char *name, const uint8_t *vals);    /* its index after the save, -1 not written */
int frp_user_delete(int k);
void frp_user_rescan(void);
uint32_t frp_user_gen(void);                                 /* bumps when the list changed */
const char *frp_user_dir(void);
#endif
