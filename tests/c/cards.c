/* SPDX-License-Identifier: GPL-3.0-only */
/* Every card with a unit, at every value: the text the firmware shows (slot_text, as the card draws it) and the
 * number the audio code really runs with (the same table entry fx.c / bars.c read), for tests/py/test_display_sound.py.
 * The whole app is included (one compilation unit, as on the FM-1) with tests/host.c's main renamed:
 *
 *   cc -DDINGHY_HOST -Dmain=host_main -Ifirmware/src -Ifirmware/gen tests/c/cards.c -lm
 *
 * Output: "card NAME V TEXT RAW" a line each (TEXT with '_' for a space; RAW: the table value, see the test). */
#include "frodhes.c"
#undef main

static void put(const char *name, uint32_t slot, uint32_t v, int64_t raw)
{
    char t[24];
    uint32_t i;
    slot_text(slot, t);
    for (i = 0; t[i]; i++) if (t[i] == ' ') t[i] = '_';
    printf("card %s %u %s %lld\n", name, v, t[0] ? t : "-", (long long)raw);
}

int main(void)
{
    uint32_t v;
    params_defaults();
    params_sanitize();
    params_apply_all();
    param_set(P_TW_SW, 1); param_set(P_TW_MODE, 1);                 /* the tremolo (not WAH: its RATE is a resonance) */
    for (v = 0; v < 128u; v++) {
        param_set(P_TW_RATE, (int32_t)v); put("TW_RATE", P_TW_RATE, v, FX_LFO_INC[v]);
        param_set(P_LFO_RATE, (int32_t)v); put("LFO_RATE", P_LFO_RATE, v, FX_LFO_INC[v]);
        param_set(P_CP_SPEED, (int32_t)v); put("CP_SPEED", P_CP_SPEED, v, FX_LFO_INC[v] >> 1);   /* fx.c: >> 1 */
        param_set(P_PH_SPEED, (int32_t)v); put("PH_SPEED", P_PH_SPEED, v, FX_LFO_INC[v] >> 1);
        param_set(P_DL_TIME, (int32_t)v); put("DL_TIME", P_DL_TIME, v, FX_DLY_T[v]);
        param_set(P_FL_FREQ, (int32_t)v);
        {                                                       /* fx.c fx_filter: base = v * 516, g interpolated */
            int32_t pos = (int32_t)v * 516, i = pos >> 8, f = pos & 255;
            put("FL_FREQ", P_FL_FREQ, v, (int32_t)FX_FL_G[i] + ((((int32_t)FX_FL_G[i + 1] - (int32_t)FX_FL_G[i]) * f) >> 8));
        }
        param_set(P_LEVEL, (int32_t)v); put("LEVEL", P_LEVEL, v, FX_LVL[v]);
        param_set(P_CMP_GAIN, (int32_t)v); put("CMP_GAIN", P_CMP_GAIN, v, ((int32_t)v * 16461) >> 7);    /* log2 Q12 */
        param_set(P_CMP_SW, 1); param_set(P_CMP_AMT, (int32_t)v); put("CMP_AMT", P_CMP_AMT, v, ((int32_t)v * 27434) >> 7);
    }
    param_set(P_TYPE, 4); param_set(P_TW_MODE, 2);                  /* VIBES, MOTOR: its own rate text */
    for (v = 0; v < 128u; v++) { param_set(P_TW_RATE, (int32_t)v); put("MOTOR", P_TW_RATE, v, bar_mot_inc); }
    return 0;
}
