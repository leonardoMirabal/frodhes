/* SPDX-License-Identifier: GPL-3.0-only */
/* The two Emscripten calls web/web_hal.c makes, for the desktop plugin (as move/compat/emscripten.h for the Move;
 * plugin/src/slot_bridge.h defines move_now_ms() and move_yield() after the app):
 *   emscripten_get_now()  the slot's host-millisecond clock (milliseconds stepped), not the wall clock, so the app's
 *                         timing follows the audio exactly as on the FM-1 and in the host build
 *   emscripten_sleep()    the coroutine yield: app_main() hands the audio thread back its turn
 * EMSCRIPTEN_KEEPALIVE is "static": web_hal.c's page-side exports stay inside their slot's object (engine_unit.c is
 * compiled once a slot; the only global of each is its table). */
#ifndef FRODHES_PLUGIN_EMSCRIPTEN_H
#define FRODHES_PLUGIN_EMSCRIPTEN_H
#define EMSCRIPTEN_KEEPALIVE static __attribute__((unused))
static double move_now_ms(void);
static void move_yield(void);
static inline double emscripten_get_now(void) { return move_now_ms(); }
static inline void emscripten_sleep(unsigned ms) { (void)ms; move_yield(); }
#endif
