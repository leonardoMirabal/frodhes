/* SPDX-License-Identifier: GPL-3.0-only */
/* The desktop plugin's bridge (private plan "next release" B2/B3, milestone B-M0).
 *
 * Two layers:
 *   the slot     one copy of the app (firmware/src/frodhes.c over web/web_hal.c) compiled into its own object
 *                (plugin/src/engine_unit.c with FRP_SLOT = 0 .. FRP_SLOTS - 1, plugin/cmake/slots.cmake). Everything in
 *                it is static except its table, frp_slot_<N> (frp_slot_api_t below): FRP_SLOTS independent apps in one
 *                binary, no runtime file copies. The per-slot glue (time, queue, FIFO, state) is slot_bridge.h.
 *   the instance a plugin instance (plugin/src/bridge.c): it claims a free slot, maps the host's frames and events
 *                onto the app's milliseconds, resamples for 48 kHz and hands out float.
 *
 * Time (as the Move, move/move_bridge.h, and the host build, tests/host.c): the app runs on host milliseconds. Each
 * step: ms + 1, the events due by then into the app's MIDI ring / applied, every 256 frames owed app_audio(256) into
 * the slot's FIFO, then one pass of app_main() (a coroutine) up to its next platform_poll(). A 44.1 kHz render of a
 * MIDI script is therefore the host build's render of the same script, sample for sample (Gate D, tools/gate_d.py).
 *
 * Threads (CLAP's): boot / reset / state reads on the main thread; put / step / read on the audio thread; drain on
 * the main thread only while the instance is not active (CLAP's flush outside processing). Never both at once for
 * one slot (the instance owns its slot). */
#ifndef FRODHES_PLUGIN_BRIDGE_H
#define FRODHES_PLUGIN_BRIDGE_H
#include <stdint.h>
#include "../gen/params_map.h"

#define FRP_SLOTS 16u
#define FRP_FS 44100u                    /* the app's rate: always */
#define FRP_SETTINGS_N 256u              /* params.c settings_t */
#define FRP_BASE_N 80u                   /* params.c P_SLOTS */
#define FRP_STATE_MAX 1024               /* the state's JSON text (slot_bridge.h FRP_STATE_N) */

enum { FRP_EV_MIDI = 1, FRP_EV_PARAM, FRP_EV_ENGINE, FRP_EV_RESET };

/* what the main thread may read of a slot: published by the audio side after each step (a sequence lock) */
typedef struct {
    uint32_t rev;                        /* bumps on every change the app made or took (a parameter, a state) */
    uint8_t pv[FRP_NPARAM_APP];          /* the parameters' values (params.c PV) */
    uint8_t engine;                      /* FRP_ENGINE_CLASSIC / _HQ (the app's hq_mode) */
} frp_snap_t;

typedef struct {
    uint32_t ms, steps, blocks, midi_in, midi_out, params, q_full, cc_drop, state_ok, state_bad, ring_wait;
} frp_stats_t;

typedef struct frp_slot_api {
    int (*boot)(int engine);             /* main thread: the app's first pass (once a slot; later calls: 0 at once) */
    int (*booted)(void);
    void (*reuse)(int engine);           /* main thread, inactive: a new instance takes a used slot: the boot's
                                          * state, all notes off, the effects' state cleared */
    int (*put)(int kind, uint32_t at_ms, uint32_t a, int32_t v);   /* queue an event (0 ok, -1 full); at_ms 0 = at once.
                                          * MIDI: a = st | d1 << 8 | d2 << 16. PARAM: a = id, v = value.
                                          * ENGINE: v. RESET: all notes off and the effects' state cleared */
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
} frp_slot_api_t;

#define FRP_SLOT_DECL(n) extern const frp_slot_api_t frp_slot_##n;
FRP_SLOT_DECL(0) FRP_SLOT_DECL(1) FRP_SLOT_DECL(2) FRP_SLOT_DECL(3) FRP_SLOT_DECL(4) FRP_SLOT_DECL(5)
FRP_SLOT_DECL(6) FRP_SLOT_DECL(7) FRP_SLOT_DECL(8) FRP_SLOT_DECL(9) FRP_SLOT_DECL(10) FRP_SLOT_DECL(11)
FRP_SLOT_DECL(12) FRP_SLOT_DECL(13) FRP_SLOT_DECL(14) FRP_SLOT_DECL(15)

/* ---- the instance (bridge.c) */
#define FRP_LAT_MS 10u                   /* the events' schedule ahead of the app (and the output's pre-roll): 10 ms */
typedef struct frp_inst frp_inst_t;
frp_inst_t *frp_inst_new(void);          /* main thread: claims a slot (NULL slot: FRP_SLOTS in use, plays silence) */
void frp_inst_free(frp_inst_t *in);
int frp_inst_slot(const frp_inst_t *in); /* the slot, -1 none */
int frp_inst_activate(frp_inst_t *in, double rate);   /* main thread: 0 ok, -1 a rate outside 1 .. 768 kHz */
void frp_inst_deactivate(frp_inst_t *in);
uint32_t frp_inst_latency(const frp_inst_t *in);       /* output frames at the host's rate */
void frp_inst_reset(frp_inst_t *in);     /* audio thread: all notes off, effects cleared (CLAP reset) */
/* events of the current block (audio thread; time = the frame in the block, ascending), then render */
void frp_inst_midi(frp_inst_t *in, uint32_t time, uint8_t st, uint8_t d1, uint8_t d2);
void frp_inst_param(frp_inst_t *in, uint32_t time, uint32_t id, double value);
void frp_inst_render(frp_inst_t *in, float *l, float *r, uint32_t n, uint32_t tempo_bpm10);
/* the main thread outside processing (CLAP flush / state while inactive) */
void frp_inst_param_now(frp_inst_t *in, uint32_t id, double value);
int frp_inst_get(const frp_inst_t *in, uint32_t id, double *value);   /* from the slot's published values */
int frp_inst_state_get(const frp_inst_t *in, char *buf, int n);
int frp_inst_state_set(frp_inst_t *in, const char *json);   /* applied now (inactive) or by the next step */
uint32_t frp_inst_rev(const frp_inst_t *in);
int frp_inst_snap(const frp_inst_t *in, frp_snap_t *out);   /* the slot's published values: 0, -1 no slot */
void frp_inst_stats(const frp_inst_t *in, frp_stats_t *st, uint32_t *late);
#endif
