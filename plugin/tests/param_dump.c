/* SPDX-License-Identifier: GPL-3.0-only */
/* The parameter table as the app itself has it, for tools/gen_plugin_params.py: the app (firmware/src/frodhes.c over
 * the host harness tests/host.c, its main renamed) prints every parameter of PARAMS[] and the text the FM-1 shows for
 * every value (params.c param_text; the enums' full names), so the plugin's value texts are the panel's, never typed in
 * twice.
 *   param_dump                    one "P id lab hi def fmt cc" line per parameter, then "T id v text" per value
 * Built by the generator with the plugin's engine flags (plugin/CMakeLists.txt FRP_ENGINE_DEFS). */
#define main host_main_unused
#include "../../firmware/src/frodhes.c"
#undef main

int main(void)
{
    uint32_t id;
    int32_t v;
    char s[16];
    printf("N %u\n", (unsigned)P_COUNT);
    for (id = 0; id < P_COUNT; id++) {
        const pdesc_t *d = &PARAMS[id];
        printf("P %u %s %u %u %u %u\n", (unsigned)id, d->lab, d->hi, d->def, d->fmt, d->cc);
    }
    for (id = 0; id < P_COUNT; id++) {
        const pdesc_t *d = &PARAMS[id];
        uint8_t keep = PV(id);
        for (v = 0; v <= d->hi; v++) {
            const char *t = s;
            PV(id) = (uint8_t)v;
            if (id == P_TYPE) t = N_TYPE_LONG[v];
            else if (id == P_RV_TYPE) t = N_RV_LONG[v];
            else if (d->fmt == F_ENUM || d->fmt == F_DIV) t = d->names[v];
            else param_text(id, s);
            printf("T %u %d %s\n", (unsigned)id, (int)v, t);
        }
        PV(id) = keep;
    }
    return 0;
}
