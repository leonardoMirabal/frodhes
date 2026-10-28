/* SPDX-License-Identifier: GPL-3.0-only */
/* M4-01 microbenchmarks (private plan 07, the HQ VIBES / MARIMBA cost gate), per 256-frame render (the unit the Move
 * bridge renders every other 128-frame callback) at 16 voices. No Frodhes code is linked: the upper-slot kernel is
 * bars.c's BAR_LO loop copied (the same operations, the same p[] stride and 32-frame sub-block calls), the rest are
 * the HQ candidates' shapes with made-up but stable coefficients.
 *
 *   slots U     bars.c's integer upper slots (Q32 form, error feedback), 16 voices x U slots, per sub-block calls:
 *               U = 6 (today: V2 V3 V4 T1 T2 X), 11 (+ V5 V6 V7 T3 T4), 13 (+ L1 L2)
 *   fvec U      the same resonators in float, 4 voices a vector (coupled form x += E y, y = D y - E x), all 16
 *               voices' slot k in 4 vectors, the outputs summed in a vector accumulator (the bar engine sums every
 *               voice into one mono buffer): U = 6, 11, 13, 16
 *   contact M   one mallet contact computed at a strike, float: the mallet against M modes (F = K e^p, p 1.5 / 2 /
 *               any), at 16 x 44.1 kHz, semi-implicit Euler (each mode a symplectic step) or RK4 (tools/hammer.py's
 *               method), for contact lengths 0.5 / 1.5 / 4 ms (hard .. soft mallets): us per contact
 *
 *   bars_bench [reps]   one line per case: "name  min us  median us"
 * Build (the bookworm image, aarch64): cc -O2 -ffp-contract=off -o bars_bench bars_bench.c -lm; on the Move:
 * taskset -c 1 ./bars_bench */
#define _GNU_SOURCE
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define NV 16
#define NF 256
#define SUB 32
#define UMAX 16

static double now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec * 1e-3; }
static volatile float sink;
static volatile int32_t isink;
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

/* ---- bars.c's upper slot, BAR_LO without the force (bars.c 722 ff.) */
typedef struct { int32_t x1, x2, err, k1, k2; } ms_t;
static ms_t sl[NV][UMAX];
static int32_t xt[3 * NF];
#define BAR_LO(XN, XO) do { int64_t a_ = (int64_t)(uint32_t)err - (int64_t)k1 * (XN) + (int64_t)k2 * (XO); \
        (XO) = 2 * (XN) - (XO) + (int32_t)(a_ >> 32); err = (int32_t)a_; } while (0)
__attribute__((noinline)) static void run_lo(ms_t *s, int32_t *p, uint32_t n)
{
    int32_t x1 = s->x1, x2 = s->x2, err = s->err, k1 = s->k1, k2 = s->k2;
    int32_t *e = p + 3u * n;
    for (; p < e; p += 12) {
        BAR_LO(x1, x2); p[0] += x2;
        BAR_LO(x2, x1); p[3] += x1;
        BAR_LO(x1, x2); p[6] += x2;
        BAR_LO(x2, x1); p[9] += x1;
    }
    s->x1 = x1; s->x2 = x2; s->err = err;
}
static int U;
static void slots(void)
{
    memset(xt, 0, sizeof xt);
    for (int b = 0; b < NF / SUB; b++)
        for (int v = 0; v < NV; v++)
            for (int u = 0; u < U; u++) run_lo(&sl[v][u], xt + 3 * SUB * b, SUB);
    isink += xt[3 * (NF - 1)];
}

/* ---- float, 4 voices a vector */
typedef float v4 __attribute__((vector_size(16)));
static v4 fx[UMAX][NV / 4], fy[UMAX][NV / 4], fE[UMAX][NV / 4], fD[UMAX][NV / 4];
static float fout[NF];
static void fvec(void)
{
    static v4 acc[NF];
    memset(acc, 0, sizeof acc);
    for (int u = 0; u < U; u++) {                    /* one slot of all 16 voices: 4 independent chains */
        v4 x0 = fx[u][0], x1 = fx[u][1], x2 = fx[u][2], x3 = fx[u][3];
        v4 y0 = fy[u][0], y1 = fy[u][1], y2 = fy[u][2], y3 = fy[u][3];
        v4 E0 = fE[u][0], E1 = fE[u][1], E2 = fE[u][2], E3 = fE[u][3];
        v4 D0 = fD[u][0], D1 = fD[u][1], D2 = fD[u][2], D3 = fD[u][3];
        for (int n = 0; n < NF; n++) {
            x0 += E0 * y0; y0 = D0 * y0 - E0 * x0;
            x1 += E1 * y1; y1 = D1 * y1 - E1 * x1;
            x2 += E2 * y2; y2 = D2 * y2 - E2 * x2;
            x3 += E3 * y3; y3 = D3 * y3 - E3 * x3;
            acc[n] += (x0 + x1) + (x2 + x3);
        }
        fx[u][0] = x0; fx[u][1] = x1; fx[u][2] = x2; fx[u][3] = x3;
        fy[u][0] = y0; fy[u][1] = y1; fy[u][2] = y2; fy[u][3] = y3;
    }
    for (int n = 0; n < NF; n++) fout[n] = (acc[n][0] + acc[n][1]) + (acc[n][2] + acc[n][3]);
    sink += fout[NF - 1];
}

/* ---- the mallet contact: M modes (w, sigma, phi), the mallet (mass, K, p) */
#define CM_MAX 16
static int CM, CP, CRK;                              /* modes, p (0: 1.5, 1: 2, 2: any via powf), RK4 */
static double CT;                                     /* contact length (s) */
static float cw[CM_MAX], cs[CM_MAX], cph[CM_MAX];
static inline float law(float e)
{
    if (e <= 0.0f) return 0.0f;
    return CP == 0 ? e * sqrtf(e) : CP == 1 ? e * e : powf(e, 2.3f);
}
static void contact(void)
{
    const float dt = 1.0f / (44100.0f * 16.0f), K = 2e8f, im = 1.0f / 0.03f;
    int n = (int)(CT * 44100.0 * 16.0);
    float q[CM_MAX] = {0}, qd[CM_MAX] = {0}, xh = 0.0f, vh = 2.0f;
    if (!CRK) {
        for (int i = 0; i < n; i++) {
            float e = xh, F;
            for (int m = 0; m < CM; m++) e -= cph[m] * q[m];
            F = K * law(e);
            vh -= F * im * dt; xh += vh * dt;
            for (int m = 0; m < CM; m++) {           /* symplectic: velocity first, then position */
                qd[m] += (cph[m] * F - 2.0f * cs[m] * qd[m] - cw[m] * cw[m] * q[m]) * dt;
                q[m] += qd[m] * dt;
            }
        }
    } else {
        for (int i = 0; i < n; i++) {
            float kq[4][CM_MAX], kd[4][CM_MAX], kx[4], kv[4], tq[CM_MAX], td[CM_MAX], tx = xh, tv = vh;
            for (int st = 0; st < 4; st++) {
                float e = tx, F;
                for (int m = 0; m < CM; m++) e -= cph[m] * (st ? tq[m] : q[m]);
                F = K * law(e);
                kx[st] = tv; kv[st] = -F * im;
                for (int m = 0; m < CM; m++) {
                    float a = st ? tq[m] : q[m], b = st ? td[m] : qd[m];
                    kq[st][m] = b; kd[st][m] = cph[m] * F - 2.0f * cs[m] * b - cw[m] * cw[m] * a;
                }
                if (st < 3) {
                    float h = st < 2 ? 0.5f * dt : dt;
                    tx = xh + h * kx[st]; tv = vh + h * kv[st];
                    for (int m = 0; m < CM; m++) { tq[m] = q[m] + h * kq[st][m]; td[m] = qd[m] + h * kd[st][m]; }
                }
            }
            xh += dt / 6 * (kx[0] + 2 * kx[1] + 2 * kx[2] + kx[3]);
            vh += dt / 6 * (kv[0] + 2 * kv[1] + 2 * kv[2] + kv[3]);
            for (int m = 0; m < CM; m++) {
                q[m] += dt / 6 * (kq[0][m] + 2 * kq[1][m] + 2 * kq[2][m] + kq[3][m]);
                qd[m] += dt / 6 * (kd[0][m] + 2 * kd[1][m] + 2 * kd[2][m] + kd[3][m]);
            }
        }
    }
    sink += q[0] + xh;
}

static void bench(const char *name, void (*k)(void), int reps)
{
    enum { RUNS = 15 };
    double t[RUNS];
    for (int r = 0; r < RUNS; r++) {
        double t0 = now_us();
        for (int i = 0; i < reps; i++) k();
        t[r] = (now_us() - t0) / reps;
    }
    qsort(t, RUNS, sizeof t[0], cmpd);
    printf("%-30s min %8.2f  median %8.2f us\n", name, t[0], t[RUNS / 2]);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    int reps = argc > 1 ? atoi(argv[1]) : 200;
    char nm[64];
    for (int v = 0; v < NV; v++)
        for (int u = 0; u < UMAX; u++) {
            double f = 300.0 * (1 + v) * (1 + 0.7 * u), E = 2 * 3.14159265 * f / 44100.0;
            if (E > 1.2) E = 1.2;
            sl[v][u].k1 = (int32_t)(E * E * 1073741824.0);       /* (made up; the loop's cost does not depend on them) */
            sl[v][u].k2 = (int32_t)(1e-5 * 2147483648.0);
            sl[v][u].x1 = 1000 * (v + 1); sl[v][u].x2 = 0;
            fE[u][v / 4][v % 4] = (float)E; fD[u][v / 4][v % 4] = 0.99999f; fx[u][v / 4][v % 4] = 1e-3f * (v + 1);
        }
    for (int m = 0; m < CM_MAX; m++) {
        cw[m] = 2 * 3.14159265f * 400.0f * (1 + 1.9f * m); cs[m] = 2.0f + m; cph[m] = 3.0f / (1 + m);
    }
    int us_[] = {6, 11, 13};
    for (int i = 0; i < 3; i++) { U = us_[i]; snprintf(nm, sizeof nm, "slots %d (int, bars.c)", U); bench(nm, slots, reps); }
    int uf[] = {6, 11, 13, 16};
    for (int i = 0; i < 4; i++) { U = uf[i]; snprintf(nm, sizeof nm, "fvec %d (float)", U); bench(nm, fvec, reps); }
    double ts[] = {0.5e-3, 1.5e-3, 4e-3};
    int ms[] = {6, 11};
    for (int a = 0; a < 2; a++)
        for (int p = 0; p < 3; p++)
            for (int j = 0; j < 3; j++) {
                CRK = 0; CM = ms[a]; CP = p; CT = ts[j];
                snprintf(nm, sizeof nm, "contact %d p%s %.1fms", CM, p == 0 ? "1.5" : p == 1 ? "2" : "any", CT * 1e3);
                bench(nm, contact, reps / 10 + 1);
            }
    CRK = 1; CM = 11; CP = 2; CT = 1.5e-3;
    bench("contact 11 pany 1.5ms RK4", contact, reps / 20 + 1);
    return 0;
}
