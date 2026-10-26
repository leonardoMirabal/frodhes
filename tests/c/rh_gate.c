/* SPDX-License-Identifier: GPL-3.0-only */
/* rhodes.c alone on the host (tests/py/test_rh_gate.py, tools/rh_gate_scan.py): built twice, with the inaudible-mode
 * gate and with -DRH_NO_GATE (every mode runs), so the two can be compared.
 *   rh_gate masks                     per key: "key live-mask" (the modes that run at the strike)
 *   rh_gate render note vel type bark bell tone ham decay rel offblk pedal blocks [note ...]
 *       the engine's output (Q15, before the effects) as int32 on stdout; knobs 0..127 (64 = default) through the
 *       firmware's curves; offblk = the note-off block (-1: none); pedal 1 = down before the strike, up 100 blocks
 *       after offblk; more notes = a chord at the same velocity. stderr: "modes max N sum S" (live modes per block) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rhodes.c"
#include "fx_tables.h"

static void knobs(int type, int bark, int bell, int tone, int ham, int decay, int rel)
{
    rh_type = (uint8_t)type;
    rh_bark_s = (int32_t)KN_P64(bark); rh_bark_g = (int32_t)KN_P64I(bark);
    rh_bell = (int32_t)KN_P32(bell);
    rh_tone_off = (tone - 64) * (1 << 18);
    rh_ham = (int32_t)KN_HAM(ham);
    rh_k_decay = (int32_t)DECAY_K[decay]; rh_k_rel = (int32_t)DECAY_K[rel];
}

int main(int argc, char **argv)
{
    int i, b;
    if (argc >= 2 && !strcmp(argv[1], "masks")) {
        uint32_t n, m, g;
        knobs(0, 64, 64, 64, 64, 64, 64);
        for (n = RH_KEY_LO; n < RH_KEY_LO + RH_NKEY; n++) {
            rh_init(&rv[0], n, 127);
            for (g = 0, m = 0; m < RH_NM; m++) g |= (uint32_t)rv[0].m[m].live << m;
            printf("%u %u\n", n, (unsigned)g);
        }
        return 0;
    }
    if (argc < 14 || strcmp(argv[1], "render")) {
        fprintf(stderr, "usage: rh_gate masks | render note vel type bark bell tone ham decay rel offblk pedal blocks [note ...]\n");
        return 2;
    }
    {
        int note = atoi(argv[2]), vel = atoi(argv[3]), offblk = atoi(argv[11]), pedal = atoi(argv[12]);
        int blocks = atoi(argv[13]);
        long ms = 0, msmax = 0;
        static int32_t out[256];
        knobs(atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), atoi(argv[7]), atoi(argv[8]), atoi(argv[9]), atoi(argv[10]));
        if (pedal) rh_pedal(1);
        rh_note_on((uint32_t)note, (uint32_t)vel);
        for (i = 14; i < argc; i++) rh_note_on((uint32_t)atoi(argv[i]), (uint32_t)vel);
        for (b = 0; b < blocks; b++) {
            long n = 0;
            uint32_t v, m;
            if (b == offblk) { rh_note_off((uint32_t)note, 0); for (i = 14; i < argc; i++) rh_note_off((uint32_t)atoi(argv[i]), 0); }
            if (pedal && b == offblk + 100) rh_pedal(0);
            rh_render(out, 256);
            for (v = 0; v < RH_NV; v++) if (rv[v].alive) for (m = 0; m < RH_NM; m++) n += rv[v].m[m].live;
            ms += n; if (n > msmax) msmax = n;
            fwrite(out, 4, 256, stdout);
        }
        fprintf(stderr, "modes max %ld sum %ld\n", msmax, ms);
    }
    return 0;
}
