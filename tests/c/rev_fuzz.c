/* SPDX-License-Identifier: GPL-3.0-only */
/* fx_reverb (the HALL, its pre-delay, the send and the return) driven directly on the host: random MIX / DECAY /
 * TONE / PRE-DELAY settings held for random spans, signals from silence to several times full scale (the clamps),
 * MIX 0 spans long enough for a tail to end in exact silence, and fx_reset_state now and then. Prints a 64-bit FNV-1a
 * of every output sample, how many blocks the reverb ran and how many times its tail ended
 * (tests/py/test_rev_hall.py: the hash is pinned there).
 *   rev_fuzz SEED BLOCKS */
#define main host_main
#include "frodhes.c"
#undef main
static uint32_t rs;
static uint32_t rnd(void) { rs = rs * 1664525u + 1013904223u; return rs >> 8; }
int main(int argc, char **argv)
{
    static int32_t L[256], R[256];
    uint64_t h = 1469598103934665603ull;
    uint32_t blk, j, nb, span = 0, runs = 0, ends = 0, was = 0;
    int32_t amp = 0, mode = 0;
    if (argc < 3) return 2;
    rs = (uint32_t)atoi(argv[1]);
    nb = (uint32_t)atoi(argv[2]);
    for (blk = 0; blk < nb; blk++) {
        if (!span) {
            uint32_t r = rnd();
            fx_p[FXP_RV_DEPTH] = (r & 7u) == 0 ? 0 : (int32_t)(rnd() % 128u);
            if ((r & 0x30u) == 0) fx_p[FXP_RV_DEPTH] = 127;
            fx_p[FXP_RV_DECAY] = (int32_t)(rnd() % 128u);
            fx_p[FXP_RV_TONE] = (int32_t)(rnd() % 128u);
            fx_p[FXP_RV_PREDLY] = (rnd() & 3u) ? 0 : (int32_t)(rnd() % 128u);
            mode = (int32_t)(rnd() % 6u);
            amp = mode == 0 ? 0 : mode == 1 ? 200 : mode == 2 ? 8000 : mode == 3 ? 32767 : mode == 4 ? 200000 : 3000000;
            span = 8u + rnd() % 64u;
            if (!fx_p[FXP_RV_DEPTH] && !amp) span = 600u + rnd() % 3000u;   /* a tail rings out */
        }
        span--;
        if (blk % 4999u == 2500u) fx_reset_state();
        for (j = 0; j < 256u; j++) {
            int32_t a = amp ? (int32_t)(rnd() % (2u * (uint32_t)amp + 1u)) - amp : 0;
            int32_t b = amp ? (int32_t)(rnd() % (2u * (uint32_t)amp + 1u)) - amp : 0;
            if (mode == 2 && (j & 64u)) a = b = (j & 1u) ? amp : -amp;      /* a loud square */
            L[j] = a;
            R[j] = (rnd() & 1u) ? b : a;
        }
        fx_reverb(L, R, 256u);
        for (j = 0; j < 256u; j++) {
            h = (h ^ (uint32_t)L[j]) * 1099511628211ull;
            h = (h ^ (uint32_t)R[j]) * 1099511628211ull;
        }
        runs += rv_running;
        ends += was && !rv_running && blk % 4999u != 2500u;      /* (not the reset) */
        was = rv_running;
    }
    printf("%016llx %u %u\n", (unsigned long long)h, runs, ends);
    return 0;
}
