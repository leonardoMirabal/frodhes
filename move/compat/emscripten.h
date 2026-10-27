/* SPDX-License-Identifier: GPL-3.0-only */
/* The two Emscripten calls web/web_hal.c makes, for the Move build (move/move_plugin.c is the one compilation
 * unit; it defines move_now_ms() and move_yield() after including the app):
 *   emscripten_get_now()  the plugin's sample clock in ms (frames rendered / 44.1), not the wall clock, so the
 *                         app's timing follows the audio exactly as on the FM-1 and in the host build
 *   emscripten_sleep()    the coroutine yield: app_main() hands the callback back its turn (M1-02)
 * EMSCRIPTEN_KEEPALIVE is empty: everything is built with -fvisibility=hidden, and the only export is the
 * plugin's entry point. */
#ifndef FRODHES_MOVE_EMSCRIPTEN_H
#define FRODHES_MOVE_EMSCRIPTEN_H
#define EMSCRIPTEN_KEEPALIVE
static double move_now_ms(void);
static void move_yield(void);
static inline double emscripten_get_now(void) { return move_now_ms(); }
static inline void emscripten_sleep(unsigned ms) { (void)ms; move_yield(); }
#endif
