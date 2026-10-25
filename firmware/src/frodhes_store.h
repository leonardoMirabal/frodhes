/* SPDX-License-Identifier: GPL-3.0-only */
/* Where Frodhes keeps its own data in flash, per board, and the app's allow-list (APP_FL_RANGE, store.h). frodhes.c
 * includes this before platform.c; tests/smk_flash_test.c (-DFRODHES_STORE) checks the same constants. Macros and
 * compile-time checks only.
 *
 *                  FM-1                             SMK-37 (Pro, Elite, MKE-P37)
 *   settings A, B  0xFC000, 0xFD000 (set_store.c)   0x9D000 (store.h; one sector: no free one in FL_DATA)
 *   presets A, B   0x97000, 0x98000 (presets.c)     0x9E000, 0x9F000 (Dinghy's blobs 0, 1)
 *   pattern A, B   0x99000, 0x9A000 (seq.c)         0xA0000, 0xA1000 (Dinghy's blobs 2, 3)
 *   owner tag      -                                0xAF000 ("SMK37DATA" + "FRODHES", platform.c smk_owner_check)
 *
 * On the SMK-37 the presets and the pattern are PR #3's places (0.9.2-SMK's), so a unit that ran it keeps its data:
 * the same family FRODHES, the same sectors. NBLOB 4 makes them Dinghy's blobs 0..3, so a store with another owner
 * tag is cleared there too (smk_owner_check: the settings, blobs 0..3, the owner tag). NSLOT 0: no slots, so that
 * clear never reaches USRFLASH (0xC2000..: the stock firmware's own user data, its key / knob mapping, kept for the
 * way back to stock); Dinghy's default (8 slots) would erase 8 sectors there. Nothing else of the VM is written.
 *
 * The FM-1's settings B (0.9.5, set_store.c: a save writes the sector the load would not pick): 0xFD000, after A in
 * Felucca's globals area (FL_GLOB 0xFC000..0xFEFFF, FL_STORE_OK; Felucca's own settings A / B are the same pair),
 * outside the update loader's reach (the app [0x4000, 0x93000), its records [0x93000, 0xFC000)), the update
 * staging (0xE0000..0xE4FFF) and key_mac (0xFF000), apart from the presets and the pattern. */
#pragma once

#if BOARD_SMK37
#define NBLOB 4u                                /* (store.h: blobs 0..3 = the presets and the pattern below) */
#define NSLOT 0u                                /* (store.h: no slots, nothing in USRFLASH) */
#define PR_BANK_A 0x0009E000u                   /* presets.c */
#define PR_BANK_B 0x0009F000u
#define SQ_A 0x000A0000u                        /* seq.c */
#define SQ_B 0x000A1000u
#define FR_OWNER_AT 0x000AF000u                 /* (store.h OWNER_AT: the clear and the tag go through this list) */
#ifndef DATA_FAMILY
#define DATA_FAMILY "FRODHES"                   /* the owner tag "SMK37DATAFRODHES" (tools/build.py passes the same) */
#endif
#define APP_FL_RANGE(off, n) (FL_IN(off, n, PR_BANK_A, SQ_B + 0x1000u) || FL_IN(off, n, FR_OWNER_AT, FR_OWNER_AT + 0x1000u))
#else
#define PR_BANK_A 0x00097000u
#define PR_BANK_B 0x00098000u
#define SQ_A 0x00099000u
#define SQ_B 0x0009A000u
#define SETTINGS_B 0x000FD000u                  /* set_store.c (settings A: store.h SETTINGS_AT 0xFC000) */
#define APP_FL_RANGE(off, n) (FL_IN(off, n, PR_BANK_A, SQ_B + 0x1000u) || \
                              FL_IN(off, n, SETTINGS_B, SETTINGS_B + 0x1000u))   /* the user preset bank (presets.c),
                                                                         * the sequencer's pattern (seq.c), settings B */
#endif

#include "store.h"                              /* (FL_GUARD, FL_DATA_*, SETTINGS_AT, BLOBS_AT, OWNER_AT) */

/* every sector Frodhes writes: whole, sector aligned, through the app's allow-list and the RAM driver's own net
 * (FL_GUARD), and in the board's store (FL_STORE_OK; on the SMK in FL_DATA itself, the VM below the update area) */
#define FR_SECTOR_OK(at) ((at) % 0x1000u == 0u && FL_GUARD(at, 0x1000u) && FL_GUARD((at) + 0xF00u, 0x100u) && \
                          FL_STORE_OK(at, 0x1000u))
_Static_assert(FR_SECTOR_OK(SETTINGS_AT), "Frodhes store: the settings sector");
_Static_assert(FR_SECTOR_OK(PR_BANK_A) && FR_SECTOR_OK(PR_BANK_B), "Frodhes store: the preset bank A / B");
_Static_assert(FR_SECTOR_OK(SQ_A) && FR_SECTOR_OK(SQ_B), "Frodhes store: the pattern A / B");
_Static_assert(PR_BANK_B == PR_BANK_A + 0x1000u && SQ_A == PR_BANK_B + 0x1000u && SQ_B == SQ_A + 0x1000u,
               "Frodhes store: four sectors in a row, apart");
_Static_assert(!FL_GUARD(SQ_B + 0x1000u, 0x1000u) && !FL_GUARD(SQ_B + 0xF00u, 0x200u) && !FL_GUARD(SQ_B, 0x2000u),
               "Frodhes store: nothing after the four sectors");
#if BOARD_SMK37
_Static_assert(PR_BANK_A - 0x1000u == SETTINGS_AT, "SMK Frodhes store: the settings right before the presets");
#else
_Static_assert(!FL_GUARD(PR_BANK_A - 0x1000u, 0x1000u) && !FL_GUARD(PR_BANK_A - 0x100u, 0x200u),
               "FM-1 Frodhes store: nothing before the four sectors");
#endif
#if BOARD_SMK37
_Static_assert(SETTINGS_AT >= FL_DATA_LO && SETTINGS_AT + 0x1000u <= FL_DATA_HI &&
               PR_BANK_A >= FL_DATA_LO && SQ_B + 0x1000u <= FL_DATA_HI, "SMK Frodhes store: outside FL_DATA");
_Static_assert(PR_BANK_A == BLOBS_AT && SQ_B == BLOBS_AT + (NBLOB - 1u) * 0x1000u,
               "SMK Frodhes store: the presets and the pattern are Dinghy's blobs 0..3 (the owner clear's list)");
_Static_assert(FR_OWNER_AT == OWNER_AT && FR_SECTOR_OK(OWNER_AT) && SETTINGS_AT == 0x9D000u,
               "SMK Frodhes store: the owner tag 0xAF000 writable, the settings at 0x9D000");
_Static_assert(SQ_B + 0x1000u <= OWNER_AT && OWNER_AT + 0x1000u <= FL_OTA_LO && FL_OTA_HI <= FL_CAL_LO,
               "SMK Frodhes store: below the owner tag, the update area and BTIF");
_Static_assert(!FL_GUARD(FL_SMP_LO, 0x1000u) && !FL_GUARD(0xB0000u, 0x1000u) && !FL_GUARD(0xFC000u, 0x1000u) &&
               !FL_GUARD(0x97000u, 0x1000u) && !FL_GUARD(0xA2000u, 0x1000u) && !FL_GUARD(0xAE000u, 0x1000u),
               "SMK Frodhes store: USRFLASH, the stock VM data, the FM-1's places refused");
_Static_assert(NSLOT == 0u, "SMK Frodhes store: no slots (the owner clear never reaches USRFLASH)");
_Static_assert(!FL_GUARD(0xFD000u, 0x1000u), "SMK Frodhes store: no settings B (0xFD000: the stock USR resources)");
#else
_Static_assert(SETTINGS_B == SETTINGS_AT + 0x1000u && FR_SECTOR_OK(SETTINGS_B) && SETTINGS_B >= FL_GLOB_LO &&
               SETTINGS_B + 0x1000u <= FL_GLOB_HI && SETTINGS_B >= 0xFC000u && SETTINGS_B + 0x1000u <= 0xFF000u &&
               (SETTINGS_B >= FL_OTA_HI || SETTINGS_B + 0x1000u <= FL_OTA_LO) && SETTINGS_B >= SQ_B + 0x1000u,
               "FM-1 Frodhes store: settings B 0xFD000, after A in FL_GLOB, past the loader's reach (< 0xFC000), the "
               "update staging, the presets and the pattern, below key_mac");
_Static_assert(!FL_GUARD(SETTINGS_B + 0x1000u, 0x1000u) && !FL_GUARD(SETTINGS_B + 0xF00u, 0x200u) &&
               !FL_GUARD(SETTINGS_AT - 0x1000u, 0x1000u) && !FL_GUARD(SETTINGS_AT - 0x100u, 0x200u),
               "FM-1 Frodhes store: nothing around the settings A / B");
_Static_assert(SETTINGS_AT == 0xFC000u && PR_BANK_A == 0x97000u && SQ_B == 0x9A000u &&
               PR_BANK_A >= FL_DATA_LO && SQ_B + 0x1000u <= FL_DATA_HI, "FM-1 Frodhes store: its places");
#endif
