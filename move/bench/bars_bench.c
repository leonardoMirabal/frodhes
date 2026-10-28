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
 *   fast M      the per-strike contact the Rhodes HQ's way (implicit, exact per mode under a linear force: tools/
 *               bars_hq_study.py fast_contact), 11 modes, p 2 / 1.5, 150 / 300 / 600 steps: us per contact
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


/* ---- the per-strike contact, the Rhodes HQ's method (tools/bars_hq_study.py fast_contact): each step exact for the
 * modes and the mallet under a force linear across it, the end force solved (p 2: the quadratic's root; p 1.5:
 * Newton in r = sqrt(e)), 12 mode lanes in 3 vectors; a fixed step count (the study: <= 566 steps at 32 steps a
 * contact and 4 a period of the top audible mode) */
#define FM 12
static float fa00[FM], fa01[FM], fa10[FM], fa11[FM], fg00[FM], fg01[FM], fg10[FM], fg11[FM], fph[FM];
static int FSTEPS, FP15, FON;                 /* FON: steps in contact, the last run (printed) */
static void fast_init(void)
{
    const double dt = 1.0 / (44100.0 * 4.0 * 4.0);
    for (int m = 0; m < FM; m++) {
        double w = 2 * 3.14159265 * 400.0 * (1 + 1.9 * m), sg = 2.0 + m, X[4][4];
        /* the augmented system's exponential by fine RK4 (init only): state q, qd, F, dF/dt from each unit vector */
        for (int c = 0; c < 4; c++) {
            double y[4] = {0, 0, 0, 0}, h = dt / 4096;
            y[c] = 1.0;
            for (int i = 0; i < 4096; i++) {
                double k[4][4], t[4];
                for (int st = 0; st < 4; st++) {
                    const double *z = st ? t : y;
                    k[st][0] = z[1]; k[st][1] = -w * w * z[0] - 2 * sg * z[1] + z[2]; k[st][2] = z[3]; k[st][3] = 0;
                    if (st < 3) for (int j = 0; j < 4; j++) t[j] = y[j] + (st < 2 ? 0.5 : 1.0) * h * k[st][j];
                }
                for (int j = 0; j < 4; j++) y[j] += h / 6 * (k[0][j] + 2 * k[1][j] + 2 * k[2][j] + k[3][j]);
            }
            for (int j = 0; j < 4; j++) X[j][c] = y[j];
        }
        fa00[m] = (float)X[0][0]; fa01[m] = (float)X[0][1]; fa10[m] = (float)X[1][0]; fa11[m] = (float)X[1][1];
        fg00[m] = (float)(X[0][2] - X[0][3] / dt); fg10[m] = (float)(X[1][2] - X[1][3] / dt);
        fg01[m] = (float)(X[0][3] / dt); fg11[m] = (float)(X[1][3] / dt);
        fph[m] = m < 11 ? 3.0f / (1 + m) : 0.0f;
    }
}
static inline v4 ld4(const float *p) { v4 r; memcpy(&r, p, 16); return r; }
static void fast_contact(void)
{
    const float dt = 1.0f / (44100.0f * 16.0f), imh = 1.0f / 0.03f, K = FP15 ? 2.5e8f : 4.5e7f;   /* (CORD, SOFT) */
    v4 q[3] = {{0}}, qd[3] = {{0}};
    float xh = 0.0f, vh = 2.0f, F0 = 0.0f, C = dt * dt * imh / 6.0f;
    for (int h = 0; h < 3; h++) { v4 p = ld4(fph + 4 * h), g = ld4(fg01 + 4 * h), t = p * p * g; C += (t[0] + t[1]) + (t[2] + t[3]); }
    const float kc = K * C, iC = 1.0f / C, k4 = 4.0f * kc;
    FON = 0;
    for (int i = 0; i < FSTEPS; i++) {
        v4 u[3], w[3], f0 = {F0, F0, F0, F0}, acc = {0};
        for (int h = 0; h < 3; h++) {
            const int o = 4 * h;
            u[h] = ld4(fa00 + o) * q[h] + ld4(fa01 + o) * qd[h] + ld4(fph + o) * ld4(fg00 + o) * f0;
            w[h] = ld4(fa10 + o) * q[h] + ld4(fa11 + o) * qd[h] + ld4(fph + o) * ld4(fg10 + o) * f0;
            acc += ld4(fph + o) * u[h];
        }
        float Xh = xh + dt * vh - dt * dt * 2.0f * F0 * imh / 6.0f, Vh = vh - dt * F0 * imh * 0.5f;
        float E = Xh - ((acc[0] + acc[1]) + (acc[2] + acc[3])), F1 = 0.0f;
        if (E > 0.0f) {
            float e1;
            FON++;
            if (!FP15) e1 = 2.0f * E / (1.0f + sqrtf(1.0f + k4 * E));   /* (the stable root: no cancellation) */
            else {
                float r = sqrtf(E);
                for (int it = 0; it < 6; it++) {
                    float r2 = r * r, H = E - r2 - kc * r2 * r, rn = r + H / (2.0f * r + 3.0f * kc * r2);
                    if (rn - r <= 1e-5f * r && r - rn <= 1e-5f * r) { r = rn; break; }
                    r = rn;
                }
                e1 = r * r;
            }
            F1 = (E - e1) * iC;
            if (F1 < 0.0f) F1 = 0.0f;
        }
        v4 f1 = {F1, F1, F1, F1};
        for (int h = 0; h < 3; h++) {
            const int o = 4 * h;
            q[h] = u[h] + ld4(fph + o) * ld4(fg01 + o) * f1;
            qd[h] = w[h] + ld4(fph + o) * ld4(fg11 + o) * f1;
        }
        xh = Xh - dt * dt * F1 * imh / 6.0f; vh = Vh - dt * F1 * imh * 0.5f;
        F0 = F1;
    }
    sink += q[0][0] + xh;
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
    fast_init();
    int fst[] = {150, 300, 600};
    for (int p = 0; p < 2; p++)
        for (int j = 0; j < 3; j++) {
            FP15 = p; FSTEPS = fst[j];
            snprintf(nm, sizeof nm, "fast 11 p%s %d steps", p ? "1.5" : "2", FSTEPS);
            bench(nm, fast_contact, reps);
            printf("    (%d of the %d steps in contact)\n", FON, FSTEPS);
        }
    CRK = 1; CM = 11; CP = 2; CT = 1.5e-3;
    bench("contact 11 pany 1.5ms RK4", contact, reps / 20 + 1);
    return 0;
}
