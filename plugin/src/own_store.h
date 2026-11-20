/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin's flash-layout constants, written for the plugin's own-code build (FR_OWN_HAL).
 *
 * The app (frodhes_store.h, seq.c, presets.c, frodhes.c host_fl_check) states where it keeps its sectors and asks
 * "may this range be written?" through three names only: FL_IN (a range lies inside a window), FL_RANGE_OK (the
 * app's allow-list, APP_FL_RANGE) and FL_GUARD (allow-list plus the protected areas), with the window bound FL_GLOB_LO
 * that frodhes_store.h checks the record against. The plugin has no flash; its "flash" is a RAM image
 * (own_hal.c host_mem) that keeps the same addresses, so the same checks hold there. This file gives those names, and
 * asserts every one of them against the layout frodhes_store.h defines just before including it.
 * FM-1 layout only: the plugin never builds the other board's. */
#pragma once
#include <stdint.h>

#if defined(BOARD_SMK37) && BOARD_SMK37
#error "own_store.h: the plugin's store is the FM-1 layout"
#endif
#ifndef APP_FL_RANGE
#error "own_store.h: include it from frodhes_store.h, after APP_FL_RANGE"
#endif

/* [off, off + n) lies inside [lo, hi]: the offset is in the window and the length fits what is left of it (the
 * subtraction cannot wrap once off <= hi) */
#define FL_IN(off, n, lo, hi) \
    ((uint32_t)(off) >= (uint32_t)(lo) && (uint32_t)(off) <= (uint32_t)(hi) && \
     (uint32_t)(n) <= (uint32_t)(hi) - (uint32_t)(off))

/* the window the settings record and the pattern sit in (the FM-1's globals area) */
#define FL_GLOB_LO 0x000FC000u
#define FL_GLOB_HI 0x000FF000u

/* the allow-list is the app's own; nothing else is protected on the plugin's RAM image */
#define FL_RANGE_OK(off, n) APP_FL_RANGE(off, n)
#define FL_GUARD(off, n) FL_RANGE_OK(off, n)

/* ---- every value above against the layout it serves (frodhes_store.h, FM-1 branch) */
_Static_assert(FL_GLOB_LO == 0xFC000u && FL_GLOB_HI == 0xFF000u && FL_GLOB_HI - FL_GLOB_LO == 3u * 0x1000u,
               "own_store: the globals window is three sectors, 0xFC000..0xFEFFF");
_Static_assert(SETTINGS_AT == FL_GLOB_LO && SETTINGS_B == SETTINGS_AT + 0x1000u && SQ_A == SETTINGS_B + 0x1000u &&
               SQ_A + 0x1000u == FL_GLOB_HI, "own_store: the record A, B and the pattern fill the window exactly");
_Static_assert(FL_IN(0xFC000u, 0x3000u, FL_GLOB_LO, FL_GLOB_HI) && !FL_IN(0xFC000u, 0x3001u, FL_GLOB_LO, FL_GLOB_HI) &&
               !FL_IN(0xFBFFFu, 1u, FL_GLOB_LO, FL_GLOB_HI) && FL_IN(0xFF000u, 0u, FL_GLOB_LO, FL_GLOB_HI) &&
               !FL_IN(0xFF001u, 0u, FL_GLOB_LO, FL_GLOB_HI) && !FL_IN(0xFD000u, 0xFFFFFFFFu, FL_GLOB_LO, FL_GLOB_HI),
               "own_store: FL_IN edges (first byte, last byte, one past, the length that would wrap)");
_Static_assert(FL_GUARD(SETTINGS_AT, 0x1000u) && FL_GUARD(SETTINGS_B, 0x1000u) && FL_GUARD(SQ_A, 0x1000u) &&
               FL_GUARD(FR_OTA_V16, 0x5000u) && FL_GUARD(FR_OTA_V15, 0x5000u),
               "own_store: the sectors Frodhes writes pass the guard");
_Static_assert(!FL_GUARD(0xFF000u, 0x100u) && !FL_GUARD(0xFBFFFu, 2u) && !FL_GUARD(OLD_PR_A, 0x1000u) &&
               !FL_GUARD(OLD_SQ_A, 0x1000u), "own_store: what is outside the store (the old places, key_mac) is refused");
