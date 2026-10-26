/* SPDX-License-Identifier: GPL-3.0-only */
/* Where Frodhes keeps its own data in flash, per board, and the app's allow-list (APP_FL_RANGE, store.h). frodhes.c
 * includes this before platform.c; the host tests (tests/set_ab_test.c, tests/smk_flash_test.c -DFRODHES_STORE)
 * check the same constants. Macros and compile-time checks only.
 *
 * 0.9.6: only in flash that no region of the stock firmware's own table covers (hal/fm1_flash_map.h has the stock
 * tables, decoded from M-VAVE's packages), so going back to stock finds the stock settings and presets as they were.
 *
 *                   FM-1 (V15 and V16 both: 0xFC000..)   SMK-37 (Pro, Elite, MKE-P37: FL_FREE 0xEB000..)
 *   record A, B     0xFC000, 0xFD000                     0xEB000, 0xEC000     the settings + the user presets
 *   pattern         0xFE000 (one sector)                 0xED000, 0xEE000     seq.c
 *   update staging  0xA9000 (V16) / 0xE0000 (V15), ota.c 0xEF000..0xF3FFF     5 sectors, its record at the end
 *
 * The record (set_store.c): the 256-byte settings, then the 16 user presets (ST_TAIL_N bytes, presets.c pr_user),
 * one CRC, in two sectors written in turn (a save never erases the copy the load would pick). The FM-1 has three free
 * sectors only: its pattern has one copy (a power cut in a pattern save loses the pattern, not the presets).
 *
 * Before 0.9.6 (read by the migration at the first boot, and again for what 0.9.5 saved there after a way back to it,
 * frodhes.c st_old_scan; never written again: the allow-list refuses them):
 *   FM-1  settings A / B 0xFC000 / 0xFD000 (the same sectors), presets 0x97000 / 0x98000, pattern 0x99000 / 0x9A000:
 *         in stock V15's VM (its settings store; in V16 the end of the firmware image)
 *   SMK   settings 0x9D000, presets 0x9E000 / 0x9F000, pattern 0xA0000 / 0xA1000, owner tag 0xAF000, staging
 *         0xBB000..: all in the stock VM. Read only when the owner tag is Frodhes' ("SMK37DATAFRODHES"). */
#pragma once

#define ST_TAIL_N 1536u                         /* the record's user presets (presets.c: 16 x 96 B, asserted there) */
#define NBLOB 0u                                /* (store.h: Dinghy's blobs and slots, unused) */
#define NSLOT 0u
#if BOARD_SMK37
#define SETTINGS_AT 0x000EB000u                 /* the record A, B (set_store.c) */
#define SETTINGS_B 0x000EC000u
#define SQ_A 0x000ED000u                        /* the pattern A, B (seq.c) */
#define SQ_B 0x000EE000u
#define OTA_AREA FL_STAGE_LO                    /* the update staging (ota.c), 0xEF000..0xF3FFF */
#define OLD_SET_A 0x0009D000u                   /* before 0.9.6 */
#define OLD_SET_B 0x0009D000u
#define OLD_PR_A 0x0009E000u
#define OLD_PR_B 0x0009F000u
#define OLD_SQ_A 0x000A0000u
#define OLD_SQ_B 0x000A1000u
#define OLD_OWNER_AT 0x000AF000u
#ifndef DATA_FAMILY
#define DATA_FAMILY "FRODHES"                   /* the owner tag "SMK37DATAFRODHES" before 0.9.6 (tools/build.py) */
#endif
#define APP_FL_RANGE(off, n) FL_IN(off, n, SETTINGS_AT, FL_STAGE_HI)   /* 0xEB000..0xF3FFF: the record, the pattern,
                                                                         * the staging */
#else
#define SETTINGS_AT 0x000FC000u
#define SETTINGS_B 0x000FD000u
#define SQ_A 0x000FE000u
#define SQ_B 0x000FE000u                        /* (one sector: seq.c's two copies are the same) */
#define FR_OTA_V15 0x000E0000u                  /* the staging on a unit that came from V15 (its VM's end, where */
#define FR_OTA_V16 0x000A9000u                  /* Felucca and 0.9.5 stage) or from V16 (in no region of V16's) */
#define OTA_AREA fr_ota_area                    /* (platform.c: picked at power-on) */
#define OLD_SET_A 0x000FC000u
#define OLD_SET_B 0x000FD000u
#define OLD_PR_A 0x00097000u
#define OLD_PR_B 0x00098000u
#define OLD_SQ_A 0x00099000u
#define OLD_SQ_B 0x0009A000u
#define APP_FL_RANGE(off, n) (FL_IN(off, n, SETTINGS_AT, 0xFF000u) || FL_IN(off, n, FR_OTA_V15, FR_OTA_V15 + 0x5000u) || \
                              FL_IN(off, n, FR_OTA_V16, FR_OTA_V16 + 0x5000u))
#endif

#include "store.h"                              /* (FL_GUARD, FL_IN, the stock map: hal/fm1_flash_map.h) */

/* every sector Frodhes writes: whole, sector aligned, through the app's allow-list and the RAM driver's own net */
#define FR_SECTOR_OK(at) ((at) % 0x1000u == 0u && FL_GUARD(at, 0x1000u) && FL_GUARD((at) + 0xF00u, 0x100u))
_Static_assert(FR_SECTOR_OK(SETTINGS_AT) && FR_SECTOR_OK(SETTINGS_B) && SETTINGS_B == SETTINGS_AT + 0x1000u,
               "Frodhes store: the record A / B");
_Static_assert(FR_SECTOR_OK(SQ_A) && FR_SECTOR_OK(SQ_B) && SQ_A >= SETTINGS_B + 0x1000u, "Frodhes store: the pattern");
_Static_assert(12u + 256u + ST_TAIL_N + 8u <= 4096u, "Frodhes store: the record fits a sector");
/* nothing of the old places (the migration reads them), nothing around the store */
_Static_assert(!FL_GUARD(OLD_PR_A, 0x1000u) && !FL_GUARD(OLD_PR_B, 0x1000u) && !FL_GUARD(OLD_SQ_A, 0x1000u) &&
               !FL_GUARD(OLD_SQ_B, 0x1000u) && !FL_GUARD(SETTINGS_AT - 0x1000u, 0x1000u) &&
               !FL_GUARD(SETTINGS_AT - 0x100u, 0x200u), "Frodhes store: the old places refused");
#if BOARD_SMK37
_Static_assert(SETTINGS_AT == FL_FREE_LO && FL_STAGE_HI == FL_FREE_HI && SQ_B + 0x1000u == FL_STAGE_LO &&
               !FL_GUARD(FL_FREE_HI, 0x1000u) && !FL_GUARD(FL_FREE_HI - 0x100u, 0x200u),
               "SMK Frodhes store: 0xEB000..0xF3FFF exactly (in no stock region)");
_Static_assert(!FL_GUARD(0x9D000u, 0x1000u) && !FL_GUARD(0xAF000u, 0x1000u) && !FL_GUARD(0xBB000u, 0x1000u) &&
               !FL_GUARD(0xBF000u, 0x1000u) && !FL_GUARD(0xEA000u, 0x1000u) && !FL_GUARD(FL_SMP_LO, 0x1000u) &&
               !FL_GUARD(0xFE000u, 0x1000u) && !FL_GUARD(0xFC000u, 0x1000u),
               "SMK Frodhes store: the stock VM, USRFLASH, the FM-1's places refused");
#else
_Static_assert(SETTINGS_AT == 0xFC000u && SQ_A + 0x1000u == 0xFF000u && SETTINGS_AT >= FL_GLOB_LO &&
               !FL_GUARD(0xFF000u, 0x100u) && !FL_GUARD(0xFB000u, 0x1000u),
               "FM-1 Frodhes store: 0xFC000..0xFEFFF exactly (after V15's USR, before key_mac)");
_Static_assert(FR_SECTOR_OK(FR_OTA_V16) && FR_SECTOR_OK(FR_OTA_V16 + 0x4000u) && FR_OTA_V16 + 0x5000u <= 0xBA000u &&
               FR_SECTOR_OK(FR_OTA_V15) && FR_OTA_V15 + 0x5000u <= 0xE9000u && !FL_GUARD(FR_OTA_V16 + 0x5000u, 0x100u) &&
               !FL_GUARD(FR_OTA_V16 - 0x100u, 0x100u) && !FL_GUARD(FR_OTA_V15 + 0x5000u, 0x100u),
               "FM-1 staging: V16's gap 0xA9000..0xADFFF, V15's VM end 0xE0000..0xE4FFF, nothing next to them");
#endif
