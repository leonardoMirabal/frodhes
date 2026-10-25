/* SPDX-License-Identifier: GPL-3.0-only */
/* The PLATE reverb kernel (firmware/src/rev_plate.c) driven directly on the host (tests/py/test_rev_plate.py).
 * The input is made as the framework makes it for every type: 32 full-rate samples a control block, decimated
 * ((last + 2 a + b) / 4) and scaled to the lines' level (x HL_IN / 2^15), HL_NC = 16 half-rate samples a call.
 *   rev_plate_test ir DECAY DAMP SIZE MOD LEVEL BLOCKS OUT    a 20 ms noise burst at LEVEL (full-rate Q15), then
 *                                                          silence: mid, side (int32) of every half-rate sample
 *   rev_plate_test hall SIZE DAMP LEVEL BLOCKS OUT          the same burst through rev_hall: its mid, side
 *   rev_plate_test tail DECAY DAMP SIZE MOD                  burst, silence: the block the kernel went inactive,
 *                                                          the last nonzero output's block, any nonzero output after
 *   rev_plate_test stale SEED                               two kernels, one started over garbage, one over zeros,
 *                                                          the same input and controls: the first differing sample
 *   rev_plate_test fuzz SEED BLOCKS                          random controls, signals up to many times full scale:
 *                                                          the largest |mid| / |side|, a hash of every output */
#define main host_main
#include "frodhes.c"
#undef main
#ifndef REV_US_PLATE                    /* the kernel alone (before the framework includes it) */
typedef struct { int32_t decay, damp, size, mod, freeze; } rev_ctl_t;
#include "rev_plate.c"
#endif

static uint32_t rs = 1;
static uint32_t rnd(void) { rs = rs * 1664525u + 1013904223u; return rs >> 8; }
static int32_t xp;
/* one control block of the framework's front end: 32 full-rate samples -> 16 at the lines' level */
static void front(const int32_t *f, int32_t *x)
{
    uint32_t i;
    for (i = 0; i < HL_NC; i++) {
        int32_t v = (xp + 2 * f[2u * i] + f[2u * i + 1u]) >> 2;
        xp = f[2u * i + 1u];
        x[i] = mulq15(clampi(v, -131071, 131071), HL_IN);
    }
}
static void burst(int32_t *f, uint32_t blk, int32_t level)
{
    uint32_t j;
    for (j = 0; j < CTL; j++)       /* 20 ms = 27 control blocks */
        f[j] = blk < 27u ? (int32_t)(rnd() % (2u * (uint32_t)level + 1u)) - level : 0;
}
static rev_plate_t pa, pb;

int main(int argc, char **argv)
{
    static int32_t f[CTL], x[HL_NC], m[HL_NC], sd[HL_NC], m2[HL_NC], s2[HL_NC];
    rev_ctl_t c = {64, 60, 64, 64, 0};
    uint32_t blk, i;
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "ir") && argc >= 9) {
        FILE *o = fopen(argv[8], "wb");
        uint32_t nb = (uint32_t)atoi(argv[7]);
        int32_t lv = atoi(argv[6]);
        c.decay = atoi(argv[2]), c.damp = atoi(argv[3]), c.size = atoi(argv[4]), c.mod = atoi(argv[5]);
        rev_plate_start(&pa);
        for (blk = 0; blk < nb; blk++) {
            burst(f, blk, lv);
            front(f, x);
            rev_plate_run(&pa, x, m, sd, &c);
            for (i = 0; i < HL_NC; i++) { fwrite(&m[i], 4, 1, o); fwrite(&sd[i], 4, 1, o); }
        }
        fclose(o);
        return 0;
    }
    if (!strcmp(argv[1], "hall") && argc >= 7) {
        FILE *o = fopen(argv[6], "wb");
        int32_t size = atoi(argv[2]), damp = atoi(argv[3]), lv = atoi(argv[4]);
        uint32_t nb = (uint32_t)atoi(argv[5]);
        static int32_t fb[RV_DZ + CTL];            /* (rev_hall's input: its half-band's history first) */
        for (blk = 0; blk < nb; blk++) {
            memmove(fb, fb + CTL, sizeof fb[0] * RV_DZ);
            burst(fb + RV_DZ, blk, lv);
            rev_hall(fb + RV_DZ, size, damp, (64 + 4) >> 3, m, sd);   /* MOD 64: HALL as before */
            for (i = 0; i < HL_NC; i++) { fwrite(&m[i], 4, 1, o); fwrite(&sd[i], 4, 1, o); }
        }
        fclose(o);
        return 0;
    }
    if (!strcmp(argv[1], "tail") && argc >= 6) {
        uint32_t off = 0, last = 0, after = 0;
        c.decay = atoi(argv[2]), c.damp = atoi(argv[3]), c.size = atoi(argv[4]), c.mod = atoi(argv[5]);
        rev_plate_start(&pa);
        for (blk = 0; blk < 200000u; blk++) {
            int32_t a = 0;
            burst(f, blk, 30000);
            front(f, x);
            rev_plate_run(&pa, x, m, sd, &c);
            for (i = 0; i < HL_NC; i++) a |= m[i] | sd[i];
            if (a) { last = blk; if (off) after++; }
            if (!off && !rev_plate_active(&pa)) off = blk;
            if (off && blk > off + 2000u) break;
        }
        printf("%u %u %u\n", off, last, after);
        return 0;
    }
    if (!strcmp(argv[1], "stale") && argc >= 3) {
        rs = (uint32_t)atoi(argv[2]);
        for (i = 0; i < sizeof pa; i++) ((uint8_t *)&pa)[i] = (uint8_t)rnd();
        memset(&pb, 0, sizeof pb);
        pa.w = pb.w = rnd();                     /* the ring index is the framework's state: any value */
        rev_plate_start(&pa);
        rev_plate_start(&pb);
        for (blk = 0; blk < 4000u; blk++) {
            if (blk % 97u == 0) { c.decay = (int32_t)(rnd() % 128u); c.size = (int32_t)(rnd() % 128u);
                                  c.mod = (int32_t)(rnd() % 128u); c.damp = (int32_t)(rnd() % 151u); }
            burst(f, blk % 600u, 20000);
            front(f, x);
            rev_plate_run(&pa, x, m, sd, &c);
            rev_plate_run(&pb, x, m2, s2, &c);
            for (i = 0; i < HL_NC; i++)
                if (m[i] != m2[i] || sd[i] != s2[i]) { printf("differ %u %u\n", blk, i); return 1; }
        }
        printf("same %u\n", blk);
        return 0;
    }
    if (!strcmp(argv[1], "fuzz") && argc >= 4) {
        uint64_t h = 1469598103934665603ull;
        uint32_t nb = (uint32_t)atoi(argv[3]), span = 0, j;
        int32_t amp = 0, mx = 0;
        rs = (uint32_t)atoi(argv[2]);
        memset(&pa, 0, sizeof pa);
        rev_plate_start(&pa);
        for (blk = 0; blk < nb; blk++) {
            if (!span) {
                uint32_t r = rnd() % 6u;
                c.decay = (int32_t)(rnd() % 128u), c.size = (int32_t)(rnd() % 128u);
                c.mod = (int32_t)(rnd() % 128u), c.damp = (int32_t)(rnd() % 151u);
                if (rnd() & 1u) c.decay = 127;
                amp = r == 0 ? 0 : r == 1 ? 200 : r == 2 ? 8000 : r == 3 ? 32767 : r == 4 ? 200000 : 3000000;
                span = 8u + rnd() % 400u;
            }
            span--;
            for (j = 0; j < CTL; j++)
                f[j] = amp ? (int32_t)(rnd() % (2u * (uint32_t)amp + 1u)) - amp : 0;
            if (amp == 32767 && (blk & 4u)) for (j = 0; j < CTL; j++) f[j] = (j & 8u) ? amp : -amp;   /* square */
            front(f, x);
            rev_plate_run(&pa, x, m, sd, &c);
            for (i = 0; i < HL_NC; i++) {
                int32_t a = m[i] < 0 ? -m[i] : m[i], b = sd[i] < 0 ? -sd[i] : sd[i];
                mx = a > mx ? a : mx;
                mx = b > mx ? b : mx;
                h = (h ^ (uint32_t)m[i]) * 1099511628211ull;
                h = (h ^ (uint32_t)sd[i]) * 1099511628211ull;
            }
        }
        printf("%d %016llx\n", mx, (unsigned long long)h);
        return 0;
    }
    return 2;
}
