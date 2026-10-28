/* SPDX-License-Identifier: GPL-3.0-only */
/* M2-01 microbenchmarks (private plan 06, the HQ Rhodes cost gate): the candidate HQ kernels in float, timed per
 * 256-frame render (the unit the Move bridge renders every other 128-frame callback) at 16 voices. No Frodhes code:
 * the kernels have the HQ design's shape and operation counts, with made-up but stable coefficients.
 *   hq_bench [reps]      prints one line per kernel: "name  us_per_render  (min of 7 runs of reps renders)"
 * Build (the bookworm image, aarch64): cc -O2 -fPIC -ffp-contract=off (the Move build's flags) or -O3 -mcpu=cortex-a72;
 * run on the Move pinned off the audio core: taskset -c 1 ./hq_bench */
#define _GNU_SOURCE
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define NV 16          /* voices */
#define NF 256         /* frames a render */
#define NM_MAX 12

static double now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec * 1e-3; }
static volatile float sink;

/* ---- the tine modes: NM coupled-form resonators a voice (x += E y; y = D y - E x), the force into y */
static float mx[NV][NM_MAX], my[NV][NM_MAX], mE[NV][NM_MAX], mD[NV][NM_MAX], mB[NV][NM_MAX];
static float tip[NV][NF], force[NV][NF];
static void modes(int nm)
{
    for (int v = 0; v < NV; v++) {
        float x[NM_MAX], y[NM_MAX];
        for (int m = 0; m < nm; m++) { x[m] = mx[v][m]; y[m] = my[v][m]; }
        for (int n = 0; n < NF; n++) {
            float f = force[v][n], s = 0;
            for (int m = 0; m < nm; m++) {
                y[m] += f * mB[v][m];
                x[m] += mE[v][m] * y[m];
                y[m] = mD[v][m] * y[m] - mE[v][m] * x[m];
                s += x[m];
            }
            tip[v][n] = s;
        }
        for (int m = 0; m < nm; m++) { mx[v][m] = x[m]; my[v][m] = y[m]; }
    }
}

/* ---- the pickup: the exact flux Phi(u) = (u+R)/((u+R)^2+1) - (u-R)/((u-R)^2+1) of u = bark x + off, differenced */
static float pk_prev[NV], pk_out[NV][NF];
static inline float phi(float u, float R) { float a = u + R, b = u - R; return a / (a * a + 1.0f) - b / (b * b + 1.0f); }
static void pickup1(void)
{
    for (int v = 0; v < NV; v++) {
        float p0 = pk_prev[v];
        for (int n = 0; n < NF; n++) { float p = phi(1.1f * tip[v][n] - 0.3f, 1.0f); pk_out[v][n] = p - p0; p0 = p; }
        pk_prev[v] = p0;
    }
}
/* the same, the table way (Classic's form in float: a cubic Hermite on s = u^2 over 2048 cells) */
static float TAB[2050][2];
static inline float herm(float s)
{
    float q = s * (2048.0f / 32.0f);
    int i = (int)q;
    if (i > 2047) i = 2047;
    float t = q - (float)i, p0 = TAB[i][0], m0 = TAB[i][1], p1 = TAB[i + 1][0], m1 = TAB[i + 1][1];
    float c3 = 2 * (p0 - p1) + m0 + m1, c2 = 3 * (p1 - p0) - 2 * m0 - m1;
    return ((c3 * t + c2) * t + m0) * t + p0;
}
static void pickup1_table(void)
{
    for (int v = 0; v < NV; v++) {
        float p0 = pk_prev[v];
        for (int n = 0; n < NF; n++) { float u = 1.1f * tip[v][n] - 0.3f; float p = herm(u * u); pk_out[v][n] = p - p0; p0 = p; }
        pk_prev[v] = p0;
    }
}

/* ---- the oversampled pickup: half-band FIRs (L taps, odd; every other tap zero but the centre) as polyphase pairs on
 * mirrored ring buffers (each sample written twice, so a window is always contiguous: no wrap test, no memmove) */
#define HB1 23                              /* first stage (1x <-> 2x): 6 non-zero pairs */
#define HB2 11                              /* second stage (2x <-> 4x): 3 pairs */
#define RING 32
typedef struct { float b[2 * RING]; int i; } ring_t;
static float hb1[(HB1 + 1) / 4], hb2[(HB2 + 1) / 4];
static ring_t up1[NV], dn1a[NV], dn1b[NV], up2[NV][2], dn2a[NV][2], dn2b[NV][2];
static inline const float *ring_put(ring_t *r, float x)
{
    r->i = (r->i + 1) & (RING - 1);
    r->b[r->i] = r->b[r->i + RING] = x;
    return r->b + r->i + RING;              /* w[0] = newest, w[-k] = k samples ago */
}
/* one input sample -> two output samples (the zero-stuffed half-band: the even phase is the delayed centre tap) */
static inline void hb_up(ring_t *r, int L, const float *h, float x, float *o0, float *o1)
{
    int np = (L + 1) / 4;
    const float *w = ring_put(r, x);
    float a = 0;
    for (int k = 0; k < np; k++) a += h[k] * (w[-k] + w[-(2 * np - 1 - k)]);
    *o0 = w[-(np - 1)];
    *o1 = 2.0f * a;
}
/* two input samples -> one output sample: the odd phase through the taps, the even phase the centre */
static inline float hb_dn(ring_t *ra, ring_t *rb, int L, const float *h, float x0, float x1)
{
    int np = (L + 1) / 4;
    const float *wa = ring_put(ra, x0), *wb = ring_put(rb, x1);
    float a = 0;
    for (int k = 0; k < np; k++) a += h[k] * (wb[-k] + wb[-(2 * np - 1 - k)]);
    return 0.5f * (a + wa[-(np - 1)]);
}
static void pickup2(void)
{
    for (int v = 0; v < NV; v++) {
        float p0 = pk_prev[v];
        for (int n = 0; n < NF; n++) {
            float a, b;
            hb_up(&up1[v], HB1, hb1, tip[v][n], &a, &b);
            float pa = phi(1.1f * a - 0.3f, 1.0f), pb = phi(1.1f * b - 0.3f, 1.0f);
            float p = hb_dn(&dn1a[v], &dn1b[v], HB1, hb1, pa, pb);
            pk_out[v][n] = p - p0; p0 = p;
        }
        pk_prev[v] = p0;
    }
}
static void pickup4(void)
{
    for (int v = 0; v < NV; v++) {
        float p0 = pk_prev[v];
        for (int n = 0; n < NF; n++) {
            float a, b, q[4];
            hb_up(&up1[v], HB1, hb1, tip[v][n], &a, &b);
            hb_up(&up2[v][0], HB2, hb2, a, &q[0], &q[1]);
            hb_up(&up2[v][1], HB2, hb2, b, &q[2], &q[3]);
            for (int k = 0; k < 4; k++) q[k] = phi(1.1f * q[k] - 0.3f, 1.0f);
            float d0 = hb_dn(&dn2a[v][0], &dn2b[v][0], HB2, hb2, q[0], q[1]);
            float d1 = hb_dn(&dn2a[v][1], &dn2b[v][1], HB2, hb2, q[2], q[3]);
            float p = hb_dn(&dn1a[v], &dn1b[v], HB1, hb1, d0, d1);
            pk_out[v][n] = p - p0; p0 = p;
        }
        pk_prev[v] = p0;
    }
}

/* the same, vectorised across the voices (the HQ voice's layout: [time][voice], NEON 4 voices a lane group): one
 * shared ring index for all voices; each window read is NV contiguous floats */
typedef struct { float b[2 * RING][NV]; int i; } vring_t;
static vring_t vu1, vd1a, vd1b, vu2[2], vd2a[2], vd2b[2];
static float tipT[NF][NV], outT[NF][NV], prevV[NV];
static inline void vput(vring_t *r, const float *x) { r->i = (r->i + 1) & (RING - 1); memcpy(r->b[r->i], x, sizeof r->b[0]); memcpy(r->b[r->i + RING], x, sizeof r->b[0]); }
static inline void vhb_up(vring_t *r, int L, const float *h, const float *x, float *o0, float *o1)
{
    int np = (L + 1) / 4, c = r->i + RING;
    vput(r, x); c = r->i + RING;
    for (int v = 0; v < NV; v++) o1[v] = 0;
    for (int k = 0; k < np; k++) { const float *p = r->b[c - k], *q = r->b[c - (2 * np - 1 - k)]; for (int v = 0; v < NV; v++) o1[v] += h[k] * (p[v] + q[v]); }
    for (int v = 0; v < NV; v++) { o1[v] *= 2.0f; o0[v] = r->b[c - (np - 1)][v]; }
}
static inline void vhb_dn(vring_t *ra, vring_t *rb, int L, const float *h, const float *x0, const float *x1, float *o)
{
    int np = (L + 1) / 4, c;
    vput(ra, x0); vput(rb, x1); c = rb->i + RING;
    for (int v = 0; v < NV; v++) o[v] = 0;
    for (int k = 0; k < np; k++) { const float *p = rb->b[c - k], *q = rb->b[c - (2 * np - 1 - k)]; for (int v = 0; v < NV; v++) o[v] += h[k] * (p[v] + q[v]); }
    for (int v = 0; v < NV; v++) o[v] = 0.5f * (o[v] + ra->b[ra->i + RING - (np - 1)][v]);
}
static void vpickup1(void)
{
    for (int n = 0; n < NF; n++)
        for (int v = 0; v < NV; v++) { float p = phi(1.1f * tipT[n][v] - 0.3f, 1.0f); outT[n][v] = p - prevV[v]; prevV[v] = p; }
}
static void vpickup2(void)
{
    float a[NV], b[NV], p[NV];
    for (int n = 0; n < NF; n++) {
        vhb_up(&vu1, HB1, hb1, tipT[n], a, b);
        for (int v = 0; v < NV; v++) { a[v] = phi(1.1f * a[v] - 0.3f, 1.0f); b[v] = phi(1.1f * b[v] - 0.3f, 1.0f); }
        vhb_dn(&vd1a, &vd1b, HB1, hb1, a, b, p);
        for (int v = 0; v < NV; v++) { outT[n][v] = p[v] - prevV[v]; prevV[v] = p[v]; }
    }
}
static void vpickup4(void)
{
    float a[NV], b[NV], q[4][NV], d0[NV], d1[NV], p[NV];
    for (int n = 0; n < NF; n++) {
        vhb_up(&vu1, HB1, hb1, tipT[n], a, b);
        vhb_up(&vu2[0], HB2, hb2, a, q[0], q[1]);
        vhb_up(&vu2[1], HB2, hb2, b, q[2], q[3]);
        for (int k = 0; k < 4; k++) for (int v = 0; v < NV; v++) q[k][v] = phi(1.1f * q[k][v] - 0.3f, 1.0f);
        vhb_dn(&vd2a[0], &vd2b[0], HB2, hb2, q[0], q[1], d0);
        vhb_dn(&vd2a[1], &vd2b[1], HB2, hb2, q[2], q[3], d1);
        vhb_dn(&vd1a, &vd1b, HB1, hb1, d0, d1, p);
        for (int v = 0; v < NV; v++) { outT[n][v] = p[v] - prevV[v]; prevV[v] = p[v]; }
    }
}
/* the modes vectorised across voices too ([mode][voice] state) */
static float vmx[NM_MAX][NV], vmy[NM_MAX][NV], vmE[NM_MAX][NV], vmD[NM_MAX][NV], vmB[NM_MAX][NV], forceT[NF][NV];
static void vmodes(int nm)
{
    for (int n = 0; n < NF; n++) {
        float s[NV] = {0};
        for (int m = 0; m < nm; m++)
            for (int v = 0; v < NV; v++) {
                float y = vmy[m][v] + forceT[n][v] * vmB[m][v], x = vmx[m][v] + vmE[m][v] * y;
                vmy[m][v] = vmD[m][v] * y - vmE[m][v] * x; vmx[m][v] = x; s[v] += x;
            }
        memcpy(tipT[n], s, sizeof s);
    }
}
static void k_vmodes5(void) { vmodes(5); }
static void k_vmodes8(void) { vmodes(8); }
static void k_vmodes12(void) { vmodes(12); }

/* ---- the live hammer: a Hertz contact F = K e^1.5 (1 + alpha de/dt) against NM tine modes, semi-implicit Euler at
 * OSH x fs, until the hammer leaves the tine (here a fixed contact length: the worst, the bass's ~3 ms) */
static float contact(int nm, int osh, float ms)
{
    float q[NM_MAX] = {0}, qd[NM_MAX] = {0}, w2[NM_MAX], sg[NM_MAX], ph[NM_MAX];
    float xh = 0, vh = 2.0f, M = 0.011f, K = 2e6f, al = 0.3f, dt = 1.0f / (44100.0f * (float)osh), Fsum = 0;
    for (int m = 0; m < nm; m++) { float w = 6.2831853f * (110.0f * (float)(m + 1) * (m + 1)); w2[m] = w * w; sg[m] = 3.0f; ph[m] = 1.0f / (float)(m + 1); }
    int steps = (int)(ms * 1e-3f * 44100.0f * (float)osh);
    for (int s = 0; s < steps; s++) {
        float e = xh, de = vh;
        for (int m = 0; m < nm; m++) { e -= ph[m] * q[m]; de -= ph[m] * qd[m]; }
        float F = e > 0 ? K * e * sqrtf(e) * (1.0f + al * de) : 0.0f;
        if (F < 0) F = 0;
        vh -= F / M * dt; xh += vh * dt;
        for (int m = 0; m < nm; m++) { qd[m] += (ph[m] * F - 2 * sg[m] * qd[m] - w2[m] * q[m]) * dt; q[m] += qd[m] * dt; }
        Fsum += F;
    }
    return Fsum;
}

/* ---- sympathetic resonance: NS undamped strings, 2 modes each, driven by the mono mix (pedal down) */
#define NS 88
static float sx[NS][2], sy[NS][2], sE[NS][2], sD[NS][2], mix[NF], sym_out[NF];
static void sympathetic(void)
{
    memset(sym_out, 0, sizeof sym_out);
    for (int s = 0; s < NS; s++)
        for (int m = 0; m < 2; m++) {
            float x = sx[s][m], y = sy[s][m], E = sE[s][m], D = sD[s][m];
            for (int n = 0; n < NF; n++) { y += 1e-3f * mix[n]; x += E * y; y = D * y - E * x; sym_out[n] += x; }
            sx[s][m] = x; sy[s][m] = y;
        }
}

typedef void (*kern_t)(void);
static int g_nm;
static void k_modes5(void) { modes(5); }
static void k_modes8(void) { modes(8); }
static void k_modes12(void) { modes(12); }
static double timeit(kern_t k, int reps)
{
    double best = 1e30;
    for (int r = 0; r < 7; r++) {
        double t0 = now_us();
        for (int i = 0; i < reps; i++) k();
        double t = (now_us() - t0) / reps;
        if (t < best) best = t;
    }
    for (int v = 0; v < NV; v++) sink += pk_out[v][NF - 1] + tip[v][NF - 1] + outT[NF - 1][v] + tipT[NF - 1][v];
    sink += sym_out[3];
    return best;
}

int main(int argc, char **argv)
{
    int reps = argc > 1 ? atoi(argv[1]) : 400;
    (void)g_nm;
    srand(1);
    for (int v = 0; v < NV; v++)
        for (int m = 0; m < NM_MAX; m++) {
            float w = 6.2831853f * (100.0f + 37.0f * v) * (float)(m + 1) / 44100.0f;
            mE[v][m] = 2.0f * sinf(w / 2); mD[v][m] = 0.99995f; mB[v][m] = 0.01f / (float)(m + 1);
        }
    for (int v = 0; v < NV; v++) for (int n = 0; n < NF; n++) force[v][n] = n < 40 ? 0.5f * sinf((float)n * 0.08f) : 0.0f;
    for (int i = 0; i < 2050; i++) { float s = (float)i * 32.0f / 2048.0f, u = sqrtf(s); TAB[i][0] = phi(u, 1.0f); TAB[i][1] = 1e-3f; }
    { const float h1[6] = {0.0060f, -0.0150f, 0.0320f, -0.0610f, 0.1150f, -0.3140f}, h2[3] = {0.025f, -0.090f, 0.310f};
      memcpy(hb1, h1, sizeof h1); memcpy(hb2, h2, sizeof h2); }
    for (int s = 0; s < NS; s++) for (int m = 0; m < 2; m++) { float w = 6.2831853f * 27.5f * powf(2.0f, s / 12.0f) * (float)(m + 1) / 44100.0f; sE[s][m] = 2 * sinf(w / 2); sD[s][m] = 0.99999f; }
    for (int n = 0; n < NF; n++) mix[n] = sinf((float)n * 0.1f);
    modes(5);
    printf("hq_bench: %d voices, %d frames a render, us per render (min of 7 x %d)\n", NV, NF, reps);
    printf("modes5          %8.2f\n", timeit(k_modes5, reps));
    printf("modes8          %8.2f\n", timeit(k_modes8, reps));
    printf("modes12         %8.2f\n", timeit(k_modes12, reps));
    printf("pickup1_exact   %8.2f\n", timeit(pickup1, reps));
    printf("pickup1_table   %8.2f\n", timeit(pickup1_table, reps));
    printf("pickup2         %8.2f\n", timeit(pickup2, reps));
    printf("pickup4         %8.2f\n", timeit(pickup4, reps));
    printf("sympathetic88x2 %8.2f\n", timeit(sympathetic, reps));
    for (int m = 0; m < NM_MAX; m++) for (int v = 0; v < NV; v++) { vmE[m][v] = mE[v][m]; vmD[m][v] = mD[v][m]; vmB[m][v] = mB[v][m]; }
    for (int n = 0; n < NF; n++) for (int v = 0; v < NV; v++) forceT[n][v] = force[v][n];
    printf("(vectorised across the 16 voices)\n");
    printf("v_modes5        %8.2f\n", timeit(k_vmodes5, reps));
    printf("v_modes8        %8.2f\n", timeit(k_vmodes8, reps));
    printf("v_modes12       %8.2f\n", timeit(k_vmodes12, reps));
    printf("v_pickup1_exact %8.2f\n", timeit(vpickup1, reps));
    printf("v_pickup2       %8.2f\n", timeit(vpickup2, reps));
    printf("v_pickup4       %8.2f\n", timeit(vpickup4, reps));
    {   /* the live hammer: us per strike */
        double best = 1e30; float acc = 0;
        for (int r = 0; r < 7; r++) {
            double t0 = now_us();
            for (int i = 0; i < 50; i++) acc += contact(5, 8, 3.0f);
            double t = (now_us() - t0) / 50; if (t < best) best = t;
        }
        sink += acc;
        printf("hammer5_8x_3ms  %8.2f  (us per strike)\n", best);
        best = 1e30;
        for (int r = 0; r < 7; r++) {
            double t0 = now_us();
            for (int i = 0; i < 50; i++) acc += contact(5, 4, 1.0f);
            double t = (now_us() - t0) / 50; if (t < best) best = t;
        }
        sink += acc;
        printf("hammer5_4x_1ms  %8.2f  (us per strike)\n", best);
    }
    return 0;
}
