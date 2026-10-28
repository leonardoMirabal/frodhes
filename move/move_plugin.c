/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes on the Move: a sound generator for the Schwung host (plugin API v2), one compilation unit.
 * The app is firmware/src/frodhes.c unchanged, through the browser HAL (web/web_hal.c: DINGHY_HOST + DINGHY_WEB);
 * this file adds the Move glue after it: move/move_bridge.h (the coroutine, the audio and clock bridge, MIDI,
 * parameters, state) and the Schwung v2 entry points. Persistence (M1-08, move/move_store.h and the worker in
 * move_bridge.h): store.bin (settings + user presets) and pattern.bin, written off the audio core. Exports one symbol (-fvisibility=hidden, -Wl,-z,defs). */
#define _GNU_SOURCE 1                    /* (sched_setaffinity for the persistence worker) */
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

static const host_api_v1_t *_Atomic g_host;   /* the owner's host table (see the instances below) */
int frodhes_web_main(void);              /* web_hal.c main(), renamed (hidden: not exported) */

#include "module/move_ui.h"
#include "move_bridge.h"

/* Instances (Schwung 1.8.1, plan 9.2 Q7). On the Move this file is engine.so, and the module's dsp.so is the loader
 * (move/move_loader.c): every slot's instance gets its own copy of this file, so each copy holds one instance and the
 * rules below never come into play there. They hold for this file loaded on its own (the tests, an older package):
 * one app a process (its globals), but the host may hold several
 * instances of it. Re-picking Frodhes in a slot creates the new instance before it destroys the old one
 * (chain_synth_load.c: stage, commit, retire), and two slots with Frodhes share this one dlopen'd copy. The
 * newest live instance owns the app: only it renders and plays; the others render silence and ignore MIDI and
 * writes (their reads are answered, their preset_name says ONE SLOT ONLY). When the owner goes, the newest remaining one takes over. The app (its
 * worker) stops only with the last instance. Each slot's chain passes its own host table to the init call just
 * before its create, so an instance keeps that table and the owner's is the one the bridge reads (g_host).
 * create / destroy run on the host's loader thread, the callbacks on the audio thread: the list is under a
 * spin lock (no syscall), the owner and g_host are atomic pointers. */
#define MV_MAX 8
typedef struct { const host_api_v1_t *host; } move_inst_t;
static move_inst_t *mv_list[MV_MAX];     /* (in creation order) */
static atomic_flag mv_lock = ATOMIC_FLAG_INIT;
static move_inst_t *_Atomic mv_owner;
static const host_api_v1_t *_Atomic g_host_init;   /* the table of the latest init call */
static void mv_lock_take(void) { while (atomic_flag_test_and_set_explicit(&mv_lock, memory_order_acquire)) {} }
static void mv_lock_give(void) { atomic_flag_clear_explicit(&mv_lock, memory_order_release); }
static void mv_own(move_inst_t *in)      /* (under the lock) */
{
    if (in) atomic_store(&g_host, in->host);
    atomic_store(&mv_owner, in);
}

static void *mv_create(const char *module_dir, const char *json_defaults)
{
    move_inst_t *in;
    int n = 0, i;
    (void)json_defaults;
    in = calloc(1, sizeof *in);
    if (!in) return 0;
    in->host = atomic_load(&g_host_init);
    mv_lock_take();
    for (i = 0; i < MV_MAX && mv_list[i]; i++) n++;
    if (n >= MV_MAX) { mv_lock_give(); free(in); return 0; }
    if (n == 0) {                        /* the first: the worker loads the files and boots (or the app runs on) */
        if (mb.booted) (void)mb_put(MB_EV_MIDI, 0xB0u, 123u, 0u, 0u, 0u, 0);   /* (the app ran on: all notes off) */
        if (mb_start(module_dir)) { mv_lock_give(); free(in); return 0; }
    }
    mv_list[n] = in;
    mv_own(in);
    mv_lock_give();
    return in;
}
static void mv_destroy(void *inst)
{
    int i, n = 0, at = -1;
    if (!inst) return;
    mv_lock_take();
    for (i = 0; i < MV_MAX && mv_list[i]; i++) { if (mv_list[i] == inst) at = i; n++; }
    if (at < 0) { mv_lock_give(); return; }
    for (i = at; i < n - 1; i++) mv_list[i] = mv_list[i + 1];
    mv_list[--n] = 0;
    if (atomic_load(&mv_owner) == inst) mv_own(n ? mv_list[n - 1] : 0);
    if (n == 0) mb_stop();               /* (the last one: the worker writes what is staged and ends) */
    mv_lock_give();
    free(inst);
}
static int mv_owns(void *inst) { return inst && inst == (void *)atomic_load_explicit(&mv_owner, memory_order_acquire); }
static void mv_on_midi(void *inst, const uint8_t *msg, int len, int source) { if (mv_owns(inst)) mb_midi_in(msg, len, source); }
static void mv_set_param(void *inst, const char *key, const char *val) { if (mv_owns(inst)) mb_set(key, val); }
/* a slot that does not own the app says so where the Move shows a sound's name (the header, the slot list): Frodhes
 * plays in one slot at a time (the user, 2026-10-28: "i load 2 frodhes and lost sound") */
#define MV_NOT_OWNER_NAME "ONE SLOT ONLY"
static int mv_get_param(void *inst, const char *key, char *buf, int buf_len)
{
    if (!inst) return -1;
    if (!mv_owns(inst) && key && !strcmp(key, "preset_name") && buf && buf_len > 0)
        return snprintf(buf, (size_t)buf_len, "%s", MV_NOT_OWNER_NAME);
    return mb_get(key, buf, buf_len);
}
static int mv_get_error(void *inst, char *buf, int buf_len) { (void)inst; (void)buf; (void)buf_len; return 0; }
static void mv_render(void *inst, int16_t *out, int frames)
{
    if (!mv_owns(inst)) { memset(out, 0, (size_t)frames * 2u * sizeof *out); return; }
    mb_render(out, frames);
}

/* the loader (move/move_loader.c) dlclose()s a copy after its last destroy: the app's heap goes with it (the worker
 * has ended in mb_stop). At a process exit with an instance alive, nothing (the worker may still run) */
__attribute__((destructor)) static void mv_fini(void)
{
    if (mb.wk_on) return;
    free(mb.stack);
    mb.stack = 0;
}

static plugin_api_v2_t g_api = {
    .api_version = MOVE_PLUGIN_API_VERSION_2,
    .create_instance = mv_create, .destroy_instance = mv_destroy, .on_midi = mv_on_midi,
    .set_param = mv_set_param, .get_param = mv_get_param, .get_error = mv_get_error, .render_block = mv_render,
};

MOVE_EXPORT plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host)
{
    atomic_store(&g_host_init, host);
    if (!atomic_load(&g_host)) atomic_store(&g_host, host);
    return &g_api;
}
