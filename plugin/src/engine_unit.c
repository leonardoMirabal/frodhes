/* SPDX-License-Identifier: GPL-3.0-only */
/* One slot of the desktop plugin (plugin/src/bridge.h): the app, firmware/src/frodhes.c unchanged, through the
 * browser HAL (web/web_hal.c: DINGHY_HOST + DINGHY_WEB, as move/move_plugin.c), then this slot's glue
 * (slot_bridge.h). Compiled FRP_SLOTS times with -DFRP_SLOT=0..15 (plugin/cmake/slots.cmake): every symbol of the
 * unit is static except the slot's table frp_slot_<FRP_SLOT>, which the build checks with nm (no other global), so
 * the copies never meet. web_hal.c's exports become static here (plugin/compat/emscripten.h makes
 * EMSCRIPTEN_KEEPALIVE "static") and its main() is renamed; the host build's diagnostics (printf, getenv) are
 * compiled out, as on the Move. DINGHY_PLUGIN is defined for any future hook in shared code (none so far).
 * Slots 0..15 are built with the Move's switches (HQ: 16 voices, the float paths), 16..31 with none (CLASSIC: the
 * FM-1's build, 10 voices); plugin/cmake/slots.cmake. */
#ifndef FRP_SLOT
#error "plugin/src/engine_unit.c: build with -DFRP_SLOT=<0..31> (plugin/cmake/slots.cmake)"
#endif
#define DINGHY_HOST 1
#define DINGHY_WEB 1
#define DINGHY_PLUGIN 1
#define main(...) static __attribute__((unused)) frp_web_main(__VA_ARGS__)   /* web_hal.c main(): never called */
#include <math.h>                       /* (the HQ paths call sqrtf etc.: gcc declares them itself) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define printf(...) ((void)0)
#define fprintf(...) ((void)0)
#define getenv(name) ((char *)0)

#include "../../firmware/src/frodhes.c"

#undef main
#undef printf
#undef fprintf
#undef getenv
#include "bridge.h"
#include "slot_bridge.h"

#define FRP_CAT2(a, b) a##b
#define FRP_CAT(a, b) FRP_CAT2(a, b)
const frp_slot_api_t FRP_CAT(frp_slot_, FRP_SLOT) = {
    .boot = sb_boot, .booted = sb_booted, .reuse = sb_reuse, .put = sb_put, .step = sb_step, .drain = sb_drain,
    .avail = sb_avail, .read = sb_read, .now = sb_now, .snapshot = sb_snapshot, .state_get = sb_state_get,
    .state_set = sb_state_set, .set_tempo = sb_set_tempo, .stats = sb_stats, .check_params = sb_check_params,
    .user_put = sb_user_put, .fact_info = sb_fact_info,
};
