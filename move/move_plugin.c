/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes on the Move: a sound generator for the Schwung host (plugin API v2), one compilation unit.
 * The app is firmware/src/frodhes.c unchanged, through the browser HAL (web/web_hal.c: DINGHY_HOST + DINGHY_WEB);
 * this file adds the Move glue after it: move/move_bridge.h (the coroutine, the audio and clock bridge, MIDI,
 * parameters, state) and the Schwung v2 entry points. Persistence (the user presets, the pattern: M1-08) is still
 * to come; until then they live for the session. Exports one symbol (-fvisibility=hidden, -Wl,-z,defs). */
#define DINGHY_HOST 1
#define DINGHY_WEB 1
#define DINGHY_MOVE 1
#if !defined(DINGHY_HOST) || !defined(DINGHY_WEB)
#error "DINGHY_MOVE requires DINGHY_HOST and DINGHY_WEB"
#endif
#define main frodhes_web_main            /* web_hal.c's main(): the coroutine's entry, never the process's */
/* The host build's diagnostics stay off on the Move: no stdout / stderr writes, no environment switches (they
 * would open log files from the render path). Declared first, so the macros only touch the call sites. */
#include <stdio.h>
#include <stdlib.h>
#define printf(...) ((void)0)
#define fprintf(...) ((void)0)
#define getenv(name) ((char *)0)

#include "../firmware/src/frodhes.c"

#undef main
#undef printf
#undef fprintf
#undef getenv
#include "vendor/plugin_api_v1.h"

#define MOVE_EXPORT __attribute__((visibility("default")))

static const host_api_v1_t *g_host;
int frodhes_web_main(void);              /* web_hal.c main(), renamed (hidden: not exported) */

#include "module/move_ui.h"
#include "move_bridge.h"

typedef struct { int unused; } move_inst_t;

static void *mv_create(const char *module_dir, const char *json_defaults)
{
    move_inst_t *in;
    int expect = 0;
    (void)module_dir; (void)json_defaults;
    if (!atomic_compare_exchange_strong(&mb.live, &expect, 1)) return 0;   /* one instance a process (plan 9.2 Q7) */
    in = calloc(1, sizeof *in);
    if (!in) { atomic_store(&mb.live, 0); return 0; }
    if (mb.booted) (void)mb_put(MB_EV_MIDI, 0xB0u, 123u, 0u, 0u, 0u, 0);   /* the app ran on: all notes off */
    else if (mb_boot()) { free(in); atomic_store(&mb.live, 0); return 0; }
    return in;
}
static void mv_destroy(void *inst) { if (inst) { free(inst); atomic_store(&mb.live, 0); } }
static void mv_on_midi(void *inst, const uint8_t *msg, int len, int source) { if (inst) mb_midi_in(msg, len, source); }
static void mv_set_param(void *inst, const char *key, const char *val) { if (inst) mb_set(key, val); }
static int mv_get_param(void *inst, const char *key, char *buf, int buf_len) { return inst ? mb_get(key, buf, buf_len) : -1; }
static int mv_get_error(void *inst, char *buf, int buf_len) { (void)inst; (void)buf; (void)buf_len; return 0; }
static void mv_render(void *inst, int16_t *out, int frames)
{
    if (!inst) { memset(out, 0, (size_t)frames * 2u * sizeof *out); return; }
    mb_render(out, frames);
}

static plugin_api_v2_t g_api = {
    .api_version = MOVE_PLUGIN_API_VERSION_2,
    .create_instance = mv_create, .destroy_instance = mv_destroy, .on_midi = mv_on_midi,
    .set_param = mv_set_param, .get_param = mv_get_param, .get_error = mv_get_error, .render_block = mv_render,
};

MOVE_EXPORT plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host) { g_host = host; return &g_api; }
