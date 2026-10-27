/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes on the Move: a sound generator for the Schwung host (plugin API v2), one compilation unit.
 * The app is firmware/src/frodhes.c unchanged, through the browser HAL (web/web_hal.c: DINGHY_HOST + DINGHY_WEB);
 * this file adds the Move glue after it. M1-01 scaffolding: the entry points exist and the unit compiles and
 * links with -Wl,-z,defs exporting one symbol. The coroutine (M1-02), the audio and clock bridge (M1-03), MIDI
 * (M1-04), params and state (M1-06/07) and persistence (M1-08) fill these stubs in; until then the module
 * renders silence and app_main() is never entered. */
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
static uint64_t g_frames;                 /* the sample clock (M1-03 advances it per 128-frame block) */

static double move_now_ms(void) { return (double)g_frames * 1000.0 / 44100.0; }
static void move_yield(void) { }          /* M1-02: switch back to the callback */

typedef struct { int unused; } move_inst_t;

static void *mv_create(const char *module_dir, const char *json_defaults)
{
    (void)module_dir; (void)json_defaults;
    return calloc(1, sizeof(move_inst_t));
}
static void mv_destroy(void *inst) { free(inst); }
static void mv_on_midi(void *inst, const uint8_t *msg, int len, int source) { (void)inst; (void)msg; (void)len; (void)source; }
static void mv_set_param(void *inst, const char *key, const char *val) { (void)inst; (void)key; (void)val; }
static int mv_get_param(void *inst, const char *key, char *buf, int buf_len)
{
    (void)inst; (void)key; (void)buf; (void)buf_len;
    return -1;
}
static int mv_get_error(void *inst, char *buf, int buf_len) { (void)inst; (void)buf; (void)buf_len; return 0; }
static void mv_render(void *inst, int16_t *out, int frames)
{
    (void)inst;
    memset(out, 0, (size_t)frames * 2u * sizeof *out);
    g_frames += (uint64_t)frames;
}

static plugin_api_v2_t g_api = {
    .api_version = MOVE_PLUGIN_API_VERSION_2,
    .create_instance = mv_create, .destroy_instance = mv_destroy, .on_midi = mv_on_midi,
    .set_param = mv_set_param, .get_param = mv_get_param, .get_error = mv_get_error, .render_block = mv_render,
};

MOVE_EXPORT plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host) { g_host = host; return &g_api; }
