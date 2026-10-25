/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 zvenson (from OberVave's; after genkigenki/sloop-fm1 smk37 tests/smk_flash_test.c) */
/* Dinghy's SMK-37 flash map (build with -DBOARD_SMK37=1; firmware/src/store.h, hal/fm1_flash_map.h): every 4 KiB
 * sector of the 1 MiB part through the app's allow-list (FL_RANGE_OK / FL_GUARD) and the update loader's
 * (FL_LDR_RANGE_OK): none may ever reach the head 0x0-0x3FFF, BTIF / USRTRIM 0xC0000-0xC1FFF (the calibration),
 * USR 0xF4000-0xFDFFF or key_mac 0xFF000-. The settings, the blobs, the owner tag, the slots and the OTA staging lie
 * in the store windows, each in its own sectors; ranges that wrap or straddle a boundary are refused.
 * -DFRODHES_STORE: Frodhes' own layout and allow-list instead (firmware/src/frodhes_store.h: NBLOB 4, NSLOT 0,
 * APP_FL_RANGE): the settings, the preset bank A / B (presets.c), the pattern A / B (seq.c), the owner tag, the OTA
 * staging and nothing else; the owner change's clear (platform.c smk_owner_check) only inside that list, never in
 * USRFLASH (0xC2000..: the stock firmware's own user data). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fm1_cc.h"
#if !BOARD_SMK37
#error "build with -DBOARD_SMK37=1"
#endif
#ifdef FRODHES_STORE
#include "../firmware/src/frodhes_store.h"     /* (includes store.h, with Frodhes' APP_FL_RANGE) */
#else
#include "../firmware/src/store.h"
#endif
#define LDR_RANGE_OK(off, n) FL_LDR_RANGE_OK(off, n)       /* what loader.c uses on the SMK */

static int bad;
static void check(const char *what, int ok) { printf("smk flash: %-86s %s\n", what, ok ? "ok" : "FAIL"); bad += !ok; }

static int protected_sector(uint32_t s)
{
    return s < 0x4000u || (s >= 0xC0000u && s < 0xC2000u) || (s >= 0xF4000u && s < 0xFE000u) || s >= 0xFF000u;
}

int main(void)
{
    uint32_t s, i, leak_app = 0, leak_ldr = 0, leak_guard = 0, allowed = 0, objs[NBLOB + 2], distinct = 1, inside = 1;
    uint8_t tag[16];
    for (s = 0; s < 0x100000u; s += 0x1000u) {
        if (!protected_sector(s))
            continue;
        leak_app += FL_RANGE_OK(s, 0x1000u) || FL_GUARD(s, 0x1000u) || FL_RANGE_OK(s, 256u) || FL_RANGE_OK(s + 0xF00u, 256u);
        leak_ldr += LDR_RANGE_OK(s, 0x1000u) || LDR_RANGE_OK(s + 0xF00u, 256u);
        leak_guard += !FL_PROTECTED(s, 0x1000u) || !FL_PROTECTED(s + 0xF00u, 256u);
    }
    for (s = 0; s < 0x100000u; s += 0x1000u)
        allowed += FL_GUARD(s, 0x1000u);
    check("no protected sector passes the app's allow-list (erase, page)", leak_app == 0);
    check("no protected sector passes the loader's allow-list", leak_ldr == 0);
    check("the RAM driver's own net (FL_PROTECTED) covers every protected sector", leak_guard == 0);
#ifdef FRODHES_STORE
    check("Frodhes may write 11 sectors: settings, presets A / B, pattern A / B, the owner tag, 5 of OTA", allowed == 11u);
#else
    check("the app may write 47 sectors: settings, 8 blobs, the owner tag, 5 of OTA, 8 slots of 4", allowed == 7u + NBLOB + NSLOT * SLOT_SECT);
#endif
    check("straddling ranges refused: 0xBF000+0x2000, 0x9C000+0x2000, 0xA5000+0x2000, 0x3000+0x2000",
          !FL_GUARD(0xBF000u, 0x2000u) && !FL_GUARD(0x9C000u, 0x2000u) && !FL_GUARD(0xA5000u, 0x2000u) &&
          !LDR_RANGE_OK(0xBF000u, 0x2000u) && !LDR_RANGE_OK(0x3000u, 0x2000u) && FL_PROTECTED(0xBF000u, 0x2000u));
    check("wrapping ranges refused (off + n past 4 GiB)",
          !FL_GUARD(0xBB000u, 0xFFFFF000u) && !FL_GUARD(SETTINGS_AT, 0xFFFFFF00u) && !LDR_RANGE_OK(0x9D000u, 0xFFFFFF00u) &&
          FL_PROTECTED(0xBB000u, 0xFFFFF000u));
    objs[0] = SETTINGS_AT;
    for (i = 0; i < NBLOB; i++) objs[1 + i] = BLOBS_AT + i * 0x1000u;
    objs[NBLOB + 1] = OWNER_AT;
    for (i = 0; i < NBLOB + 2u; i++) {
        uint32_t j;
        inside &= FL_GUARD(objs[i], 0x1000u) && FL_GUARD(objs[i] + 0xF00u, 0x100u) && objs[i] >= FL_DATA_LO &&
                  objs[i] + 0x1000u <= FL_DATA_HI && objs[i] % 0x1000u == 0u;
        for (j = 0; j < NBLOB + 2u; j++) if (i != j && objs[i] == objs[j]) distinct = 0;
    }
    check("settings 0x9D000, blobs 0x9E000-0xA5FFF, owner tag 0xAF000: in FL_DATA, writable, apart",
          inside && distinct && SETTINGS_AT == 0x9D000u && BLOBS_AT == 0x9E000u && OWNER_AT == 0xAF000u);
    check("OTA staging 0xBB000-0xBFFFF writable, the window ends below BTIF",
          FL_GUARD(0xBB000u, 0x1000u) && FL_GUARD(0xBFF00u, 0x100u) && !FL_GUARD(0xC0000u, 0x100u) && FL_OTA_HI == 0xC0000u);
#ifdef FRODHES_STORE
    {
        uint32_t usr = 0, list[1 + NBLOB + 1 + NSLOT + 1u], nl = 0, ok = 1;
        for (s = FL_SMP_LO; s < FL_SMP_HI; s += 0x1000u) usr += FL_GUARD(s, 0x1000u) || FL_GUARD(s + 0xF00u, 0x100u);
        check("USRFLASH 0xC2000-0xE9FFF (the stock user data): no sector writable", usr == 0u);
        check("the FM-1's places refused on the SMK: 0x97000-0x9AFFF, 0xFC000, 0xF4000-0xFBFFF",
              !FL_GUARD(0x97000u, 0x1000u) && !FL_GUARD(0x9A000u, 0x1000u) && !FL_GUARD(0xFC000u, 0x1000u) &&
              !FL_GUARD(0xF4000u, 0x1000u) && !FL_GUARD(0xFB000u, 0x1000u));
        check("presets A 0x9E000, B 0x9F000, pattern A 0xA0000, B 0xA1000 (PR #3's): Dinghy's blobs 0..3, writable",
              PR_BANK_A == 0x9E000u && PR_BANK_B == 0x9F000u && SQ_A == 0xA0000u && SQ_B == 0xA1000u &&
              NBLOB == 4u && BLOBS_AT == PR_BANK_A && FL_GUARD(PR_BANK_A, 0x4000u) && !FL_GUARD(PR_BANK_A, 0x5000u));
        check("the stock VM data 0xA2000-0xAEFFF, 0xB0000-0xBAFFF untouched (the owner tag 0xAF000 only)",
              !FL_GUARD(0xA2000u, 0x1000u) && !FL_GUARD(0xAE000u, 0x1000u) && !FL_GUARD(0xB0000u, 0x1000u) &&
              !FL_GUARD(0xBA000u, 0x1000u) && FL_GUARD(OWNER_AT, 0x1000u) && !FL_GUARD(OWNER_AT, 0x2000u));
        /* smk_owner_check's erases, in its order: the settings, every blob, every slot's head, the owner tag */
        list[nl++] = SETTINGS_AT;
        for (i = 0; i < NBLOB; i++) list[nl++] = BLOBS_AT + i * 0x1000u;
        for (i = 0; i < NSLOT; i++) list[nl++] = SLOTS_AT + i * SLOT_SECT * 0x1000u;
        list[nl++] = OWNER_AT;
        for (i = 0; i < nl; i++) {
            ok &= FL_GUARD(list[i], 0x1000u) && list[i] >= FL_DATA_LO && list[i] + 0x1000u <= FL_DATA_HI;
            printf("smk flash:   owner change erases 0x%05X\n", list[i]);
        }
        smk_owner_tag(tag);
        check("the owner tag is Frodhes' (PR #3's family): SMK37DATAFRODHES, 16 bytes, no NUL",
              !memcmp(tag, "SMK37DATAFRODHES", 16));
        check("the owner change clears 6 sectors (settings, presets A / B, pattern A / B, the tag), all in FL_DATA; "
              "no slot head", ok && nl == 6u && NSLOT == 0u);
    }
#else
    check("slots 0xC2000-0xE1FFF in FL_SMP, writable; the FM-1's places refused on the SMK: 0xFC000, 0xF4000-0xFBFFF",
          FL_GUARD(SLOTS_AT, NSLOT * SLOT_SECT * 0x1000u) && SLOTS_AT == 0xC2000u &&
          !FL_GUARD(0xFC000u, 0x1000u) && !FL_GUARD(0xF4000u, 0x1000u) && !FL_GUARD(0xFB000u, 0x1000u));
#endif
    check("the calibration record 0xC1000 (trimdat at +0x224) never writable",
          !FL_GUARD(0xC1000u, 0x1000u) && !FL_GUARD(0xC1200u, 0x100u) && FL_PROTECTED(0xC1224u, 8u));
    check("loader: app area 0x4000-0x9CFFF, records 0x9D000-0xBFFFF only",
          LDR_RANGE_OK(0x4000u, 0x1000u) && LDR_RANGE_OK(0x9C000u, 0x1000u) && LDR_RANGE_OK(0xBF000u, 0x1000u) &&
          !LDR_RANGE_OK(0xC0000u, 0x1000u) && FL_REC_HI == 0xC0000u);
    check("FL_PLAIN_LO = the VM start 0x9D000", FL_PLAIN_LO == 0x9D000u);
    check("FL_JEDEC_OK: the SMK part 0xEB6014 and 0x856014 only", FL_JEDEC_OK(0xEB6014u) && FL_JEDEC_OK(0x856014u) &&
          !FL_JEDEC_OK(0u) && !FL_JEDEC_OK(0xFFFFFFu) && !FL_JEDEC_OK(0xEB6015u));
    smk_owner_tag(tag);
    check("owner tag: \"SMK37DATA\" + the family, 16 bytes (" DATA_FAMILY ")",
          !memcmp(tag, "SMK37DATA", 9) && !memcmp(tag + 9, DATA_FAMILY, sizeof DATA_FAMILY - 1u) &&
          (sizeof DATA_FAMILY - 1u == 7u || tag[9 + sizeof DATA_FAMILY - 1u] == 0u));
    printf("%s\n", bad ? "SMK FLASH TEST FAILED" : "smk flash test passed");
    return bad != 0;
}
