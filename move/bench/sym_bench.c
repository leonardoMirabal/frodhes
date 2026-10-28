/* SPDX-License-Identifier: GPL-3.0-only */
/* The cost gate of the HQ Rhodes' sympathetic resonance (private plan 06 item 6), before any voice code: a bank of
 * undamped tine modes (88 keys x M modes, the coupled-form resonator of the Classic voice in float), driven by one
 * signal (the harp's motion: the sounding tines' sum) and summed into one output, per 128-frame callback.
 *
 *   serial      hq_bench.c's first kernel: resonator-outer, sample-inner (one dependent chain at a time: latency)
 *   vec G       sample-inner over groups of G 4-lane vectors (G independent chains in flight: throughput), the
 *               output summed per sample in a 4-lane accumulator, reduced once at the end
 *
 * Build (aarch64): gcc -O2 -ffp-contract=off -o sym_bench sym_bench.c; on the Move: taskset -c 1 ./sym_bench
 * Prints us per callback: the min and the median over the runs, per kernel and bank size. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define NF 128
#define NK 88
#define MMAX 3
#define NR (NK * MMAX)                         /* resonators at most */
#define NVEC 72                               /* NR / 4 = 66, padded to whole groups of 8 */

typedef float v4 __attribute__((vector_size(16)));

static float rx[NR], ry[NR], rE[NR], rD[NR], rg[NR];
static v4 vx[NVEC], vy[NVEC], vE[NVEC], vD[NVEC], vg[NVEC];
static float drive[NF], out[NF];
static volatile float sink;

static double now_us(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e6 + t.tv_nsec * 1e-3;
}

static void serial(int nr)
{
    memset(out, 0, sizeof out);
    for (int s = 0; s < nr; s++) {
        float x = rx[s], y = ry[s], E = rE[s], D = rD[s], g = rg[s];
        for (int n = 0; n < NF; n++) { y += g * drive[n]; x += E * y; y = D * y - E * x; out[n] += x; }
        rx[s] = x; ry[s] = y;
    }
}

#define VEC_KERNEL(G)                                                                         \
static void vec##G(int nv)                                                                    \
{                                                                                             \
    static v4 acc[NF];                                                                        \
    memset(acc, 0, sizeof acc);                                                               \
    for (int b = 0; b < nv; b += G) {                                                         \
        v4 x[G], y[G], E[G], D[G], g[G];                                                      \
        int k, n;                                                                             \
        for (k = 0; k < G; k++) { x[k] = vx[b + k]; y[k] = vy[b + k]; E[k] = vE[b + k];         \
                                  D[k] = vD[b + k]; g[k] = vg[b + k]; }                       \
        for (n = 0; n < NF; n++) {                                                            \
            v4 d = {drive[n], drive[n], drive[n], drive[n]}, s = acc[n];                      \
            for (k = 0; k < G; k++) {                                                         \
                y[k] += g[k] * d; x[k] += E[k] * y[k]; y[k] = D[k] * y[k] - E[k] * x[k];       \
                s += x[k];                                                                    \
            }                                                                                 \
            acc[n] = s;                                                                       \
        }                                                                                     \
        for (k = 0; k < G; k++) { vx[b + k] = x[k]; vy[b + k] = y[k]; }                       \
    }                                                                                         \
    for (int n = 0; n < NF; n++) out[n] = (acc[n][0] + acc[n][1]) + (acc[n][2] + acc[n][3]);  \
}
VEC_KERNEL(2)
VEC_KERNEL(4)
VEC_KERNEL(8)

static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

static void bench(const char *name, void (*k)(int), int arg)
{
    enum { RUNS = 41, REPS = 200 };
    double t[RUNS];
    for (int r = 0; r < RUNS; r++) {
        double t0 = now_us();
        for (int i = 0; i < REPS; i++) k(arg);
        t[r] = (now_us() - t0) / REPS;
        sink += out[NF - 1];
    }
    qsort(t, RUNS, sizeof t[0], cmpd);
    printf("%-10s %4d resonators  min %7.2f  median %7.2f us per %d-frame callback\n", name,
           arg * (name[0] == 'v' ? 4 : 1), t[0], t[RUNS / 2], NF);
}

int main(void)
{
    for (int i = 0; i < NR; i++) {             /* tines from 27.5 Hz up, each mode a few octaves above */
        float f = 27.5f * (float)(1u << (i % NK / 12)) * (1.0f + 0.06f * (float)(i % 12)) * (1.0f + 6.0f * (float)(i / NK));
        float E = 2.0f * 3.14159265f * f / 44100.0f;
        if (E > 1.5f) E = 1.5f;
        rx[i] = 1e-3f * (float)(i % 7); ry[i] = 0.0f; rE[i] = E; rD[i] = 0.99999f; rg[i] = 1e-3f;
    }
    for (int i = 0; i < NVEC; i++)
        for (int l = 0; l < 4; l++) {
            vx[i][l] = rx[4 * i + l]; vy[i][l] = ry[4 * i + l]; vE[i][l] = rE[4 * i + l];
            vD[i][l] = rD[4 * i + l]; vg[i][l] = rg[4 * i + l];
        }
    for (int n = 0; n < NF; n++) drive[n] = (float)((n * 7919) % 200 - 100) * 1e-2f;
    for (int m = 1; m <= MMAX; m++) {
        bench("serial", serial, NK * m);
        int nv = (NK * m / 4 + 7) / 8 * 8;     /* (whole groups: 96, 192, 288 resonators, the bank padded) */
        bench("vec2", vec2, nv);
        bench("vec4", vec4, nv);
        bench("vec8", vec8, nv);
    }
    return 0;
}
