/* SPDX-License-Identifier: GPL-3.0-only */
/* The key-velocity names of the plugin's own-code build (FR_OWN_HAL): what firmware/src/frodhes.c, params.c, ui.c and
 * plugin/src/slot_bridge.h take from the FM-1's key path. A plugin slot has no keybed: the keys never read pressed
 * (own_hal.c), every note comes in as MIDI with its velocity, and TOUCH is only a setting the host can store and the
 * velocity curve of plugin/src/slot_bridge.h (sb_vel) reads. So the key path is the plain one:
 *   kv_keys   the presses it is given, at once
 *   kv_vel    KEY VEL (set.key_vel): the velocity of a press without a reading
 *   the rest  nothing to apply, nothing to count, no developer protocol (CC 41 / 42 are still taken by the app's CC
 *             handler, which then calls kv_dev_cc, a no-op)
 * Written from the names and contracts the app calls. The calls are macros because set (params.c) is declared after
 * this file is read; kv_touch / kv_touch_store are params.c's. */
#ifndef FRODHES_PLUGIN_OWN_TOUCH_H
#define FRODHES_PLUGIN_OWN_TOUCH_H
#include <stdint.h>

#define KV_BSS(name)                    /* (a plain global: the plugin has no scan tick to keep out of a section) */
#define KV_FM1KA 0                      /* no key rows, no converter */

/* TOUCH, in the order the settings record and the UI use (set.vel_curve = (touch + 3) & 3) */
enum { KV_LIGHT, KV_MEDIUM, KV_HEAVY, KV_FIXED, KV_NTOUCH };
_Static_assert(KV_LIGHT == 0 && KV_MEDIUM == 1 && KV_HEAVY == 2 && KV_FIXED == 3 && KV_NTOUCH == 4,
               "own_touch: TOUCH's order is the settings record's (params.c kv_touch)");
static uint32_t kv_touch(void);                 /* params.c: TOUCH, KV_LIGHT..KV_FIXED */
static void kv_touch_store(uint32_t touch);     /* params.c: set it */
static const char *const KV_TOUCH_NAME[KV_NTOUCH] = {"LIGHT", "MEDIUM", "HEAVY", "FIXED"};   /* (6 characters at most) */

#define kv_apply() ((void)0)                    /* TOUCH changed: nothing to arm */
#define kv_keys(down, up) ((void)(up), (uint32_t)(down))
#define kv_vel(k) ((void)(k), (uint32_t)set.key_vel)
#define kv_last_vel() 0u                        /* no FM-1 key has been pressed */
#define KV_CC_MODE 41u                          /* the developer CCs the app's CC handler hands to kv_dev_cc */
#define KV_CC_QUERY 42u
#define kv_dev_cc(cc, v) ((void)(cc), (void)(v))
#define kv_dev_edges(buttons) ((void)(buttons))

#endif
