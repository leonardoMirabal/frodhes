/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* Host-only prototype of the pulse-bank decoders (private/PULSE-COMPRESSION-PLAN.md), not firmware code.
 * Built and run by `python3 tools/pulse_codec_study.py --emit-c build/pz`, which writes build/pz/pz_streams.h (the
 * streams of the real RH_PULSE / BAR_PULSE and the reference banks). Integers only, no 64-bit divide, no libcalls:
 * the shape the firmware decoder would take. Exit 0 = every value of both banks decoded bit-exactly, both formats.
 *
 *   PZ-B (whole bank, at the engine change): per pulse a 3-bit predictor, a 4-bit Rice k, 64 Rice codes.
 *   PZ-A (one pulse, random access, a note-on candidate): 1-bit predictor (own3 / own4), four 4-bit k (one per
 *        16 points), 64 Rice codes; a 16-bit word offset per pulse.
 * A Rice code: q ones and a 0, then k bits; q >= 24 (the escape): the value follows raw in 22 bits. Zigzag values. */
#include <stdint.h>
#ifndef PZ_DEC_ONLY             /* -DPZ_DEC_ONLY: the decoders alone (to read their pi32v2 code: -target pi32v2 -S) */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "pz_streams.h"
#endif

#define PZ_ESC 24u
#define PZ_RAWB 22u
#define PLEN 64u
#define ROW 66u

typedef struct { const uint8_t *p; uint32_t w, n; } br_t;   /* w: the next n bits, left-aligned */

static inline void br_fill(br_t *b)
{
    while (b->n <= 24u) { b->w |= (uint32_t)*b->p++ << (24u - b->n); b->n += 8u; }
}
static inline uint32_t br_get(br_t *b, uint32_t k)          /* k <= 24 */
{
    uint32_t v;
    if (!k) return 0;
    br_fill(b);
    v = b->w >> (32u - k); b->w <<= k; b->n -= k;
    return v;
}
static inline uint32_t br_rice(br_t *b, uint32_t k)
{
    uint32_t q = 0;
    br_fill(b);                                             /* >= 25 bits: the longest unary (24 ones + 0) fits */
    while (b->w & 0x80000000u) { q++; b->w <<= 1; }         /* (the firmware may use a count-leading-zeros) */
    b->w <<= 1; b->n -= q + 1u;
    if (q >= PZ_ESC) return br_get(b, PZ_RAWB);
    return q << k | br_get(b, k);
}
static inline int32_t unzz(uint32_t u) { return (int32_t)(u >> 1) ^ -(int32_t)(u & 1u); }

static const int16_t ZROW[ROW];

/* predictors 0..7: own3 own4 key2 key3 lay2 lay3 2d2 avg2 (tools/pulse_codec_study.py PREDS) */
static const uint8_t P_ORD[8] = {3, 4, 2, 3, 2, 3, 2, 2};

/* one pulse of PZ-B into dst[66] (dst[64] = dst[65] = 0), kp / lp / dg the neighbours (ZROW when absent) */
static void pzb_pulse(br_t *b, int16_t *dst, const int16_t *kp, const int16_t *lp, const int16_t *dg)
{
    uint32_t pr = br_get(b, 3), k = br_get(b, 4), ord = P_ORD[pr], i;
    int32_t c1 = 0, c2 = 0, c3 = 0, c4 = 0;
    for (i = 0; i < PLEN; i++) {
        int32_t v = unzz(br_rice(b, k)), ref;
        c1 += v; c2 += c1;                                  /* the order's running sums (order >= 2 always) */
        if (ord >= 3u) { c3 += c2; if (ord == 4u) { c4 += c3; v = c4; } else v = c3; } else v = c2;
        switch (pr) {
        case 0: case 1: ref = 0; break;
        case 2: case 3: ref = kp[i]; break;
        case 4: case 5: ref = lp[i]; break;
        case 6: ref = kp[i] + lp[i] - dg[i]; break;
        default: ref = (kp[i] + lp[i]) >> 1; break;
        }
        dst[i] = (int16_t)(v + ref);
    }
    dst[64] = dst[65] = 0;
}

/* the whole bank: ngroup groups of nkey keys of 8 layers; row(g, k, l) -> the pulse's row in the bank */
static void pzb_bank(const uint8_t *s, int16_t *bank, uint32_t ngroup, uint32_t nkey, uint32_t gstride, uint32_t kstride)
{
    br_t b = {s, 0, 0};
    uint32_t g, k, l;
    for (g = 0; g < ngroup; g++)
        for (k = 0; k < nkey; k++)
            for (l = 0; l < 8u; l++) {
                int16_t *row = bank + (g * gstride + k * kstride + l) * ROW;
                pzb_pulse(&b, row, k ? row - kstride * ROW : ZROW, l ? row - ROW : ZROW,
                          k && l ? row - kstride * ROW - ROW : ZROW);
            }
}

/* PZ-A: one pulse from its word offset into dst[66] */
static void pza_pulse(const uint8_t *s, uint32_t woff, int16_t *dst)
{
    br_t b = {s + 2u * woff, 0, 0};
    uint32_t ord = 3u + br_get(&b, 1), k[4], i;
    int32_t c1 = 0, c2 = 0, c3 = 0, c4 = 0;
    for (i = 0; i < 4u; i++) k[i] = br_get(&b, 4);
    for (i = 0; i < PLEN; i++) {
        int32_t v = unzz(br_rice(&b, k[i >> 4]));
        c1 += v; c2 += c1; c3 += c2;
        if (ord == 4u) { c4 += c3; v = c4; } else v = c3;
        dst[i] = (int16_t)v;
    }
    dst[64] = dst[65] = 0;
}

#ifdef PZ_DEC_ONLY
void pz_test_b(const uint8_t *s, int16_t *bank) { pzb_bank(s, bank, 1, 88, 0, 8); }
void pz_test_a(const uint8_t *s, uint32_t o, int16_t *d) { pza_pulse(s, o, d); }
#else
static int16_t bank[88 * 8 * ROW > 17 * 5 * 8 * ROW ? 88 * 8 * ROW : 17 * 5 * 8 * ROW];

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

int main(void)
{
    int fails = 0;
    uint32_t i, n;
    double t0;
    int16_t one[ROW];
    /* PZ-B, Rhodes: one group, 88 keys, key stride 8 rows */
    memset(bank, 0x55, sizeof bank);
    t0 = now();
    pzb_bank(PZB_RH, bank, 1, 88, 0, 8);
    printf("PZ-B RH : %u B stream, %s (%.0f us on the host)\n", (unsigned)sizeof PZB_RH,
           memcmp(bank, REF_RH, sizeof REF_RH) ? (fails++, "MISMATCH") : "bit-exact", (now() - t0) * 1e6);
    /* PZ-B, bars: 5 groups (families), 17 bands; row = (band * 5 + fam) * 8 + layer */
    memset(bank, 0x55, sizeof bank);
    t0 = now();
    pzb_bank(PZB_BAR, bank, 5, 17, 8, 40);
    printf("PZ-B BAR: %u B stream, %s (%.0f us on the host)\n", (unsigned)sizeof PZB_BAR,
           memcmp(bank, REF_BAR, sizeof REF_BAR) ? (fails++, "MISMATCH") : "bit-exact", (now() - t0) * 1e6);
    /* PZ-A: every pulse by its offset (the encoder's order: Rhodes (key, layer); bars (fam, band, layer)) */
    n = sizeof PZA_RH_OFF / 2u;
    for (i = 0; i < n; i++) {
        pza_pulse(PZA_RH, PZA_RH_OFF[i], one);
        if (memcmp(one, REF_RH + i * ROW, sizeof one)) { fails++; printf("PZ-A RH pulse %u MISMATCH\n", i); break; }
    }
    printf("PZ-A RH : %u B stream + %u B offsets, %u pulses %s\n", (unsigned)sizeof PZA_RH, 2u * n, n, i == n ? "bit-exact" : "");
    n = sizeof PZA_BAR_OFF / 2u;
    for (i = 0; i < n; i++) {
        uint32_t f = i / (17u * 8u), band = i / 8u % 17u, l = i % 8u;
        pza_pulse(PZA_BAR, PZA_BAR_OFF[i], one);
        if (memcmp(one, REF_BAR + ((band * 5u + f) * 8u + l) * ROW, sizeof one)) { fails++; printf("PZ-A BAR pulse %u MISMATCH\n", i); break; }
    }
    printf("PZ-A BAR: %u B stream + %u B offsets, %u pulses %s\n", (unsigned)sizeof PZA_BAR, 2u * n, n, i == n ? "bit-exact" : "");
    return fails ? 1 : 0;
}
#endif
