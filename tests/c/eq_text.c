/* SPDX-License-Identifier: GPL-3.0-only */
/* The EQ cards' texts at every value, as the firmware shows them (params.c param_text), for tests/py/test_move_eq.py:
 * the Move's screen shows the same texts from tools/gen_move_module.py's port (FR_CONF.fmt). The whole app is
 * included (one compilation unit, as on the FM-1) with tests/host.c's main renamed:
 *
 *   cc -DDINGHY_HOST -Dmain=host_main -Ifirmware/src -Ifirmware/gen tests/c/eq_text.c -lm
 *
 * Output: "eq ID V TEXT" a line each. */
#include "frodhes.c"
#undef main

int main(void)
{
    static const uint32_t ids[] = { P_EQ_LO, P_EQ_MID, P_EQ_FREQ, P_EQ_HI };
    char t[24];
    uint32_t i, v;
    params_defaults();
    params_sanitize();
    params_apply_all();
    for (i = 0; i < 4u; i++)
        for (v = 0; v < 128u; v++) {
            param_set(ids[i], (int32_t)v);
            param_text(ids[i], t);
            printf("eq %u %u %s\n", ids[i], v, t);
        }
    return 0;
}
