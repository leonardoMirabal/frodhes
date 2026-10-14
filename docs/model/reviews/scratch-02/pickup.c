/* Review 02 scratch: precision of the pickup stage y = Phi(x[n]) - Phi(x[n-1]) (PLAN D2) in integers.
 * Phi: a field-like bump 1 / (1 + ((x - off) / w)^2) over x in [-1, 1] (two spikes per period in y, an offset rest).
 *   P1  x Q15, Phi Q15 table (1024 + 1 entries, lerp on the 6 low bits), y = difference   (the plan as written)
 *   P2  x Q30 (int32 state), Phi Q30 int32 table (1024 + 1, lerp on 20 bits), y = difference
 *   P3  x Q30, g = Phi' as a Q15 table (lerp), y = g(x) * (x[n] - x[n-1])  (chain rule; dx at full precision)
 * SNR of y against the double reference, at ff and pp (-30 dB), low and mid pitch.
 * cc -O2 -o pickup pickup.c -lm && ./pickup */
#include <stdint.h>
#include <stdio.h>
#include <math.h>

#define FS 44100.0
#define N 1024
static double W = 0.35, OFF = 0.15;
static double phi(double x) { double u = (x - OFF) / W; return 1.0 / (1.0 + u * u); }
static double dphi(double x) { double u = (x - OFF) / W; return -2.0 * u / W / ((1.0 + u * u) * (1.0 + u * u)); }

static int16_t T15[N + 1], G15[N + 1];
static int32_t T30[N + 1];
static double gscale;

static void tables(void)
{
    int i;
    double gmax = 0;
    for (i = 0; i <= N; i++) { double d = fabs(dphi(-1 + 2.0 * i / N)); if (d > gmax) gmax = d; }
    gscale = 32767.0 / gmax;
    for (i = 0; i <= N; i++) {
        double x = -1 + 2.0 * i / N;
        T15[i] = (int16_t)lrint(phi(x) * 32767);
        T30[i] = (int32_t)lrint(phi(x) * 1073741823.0);
        G15[i] = (int16_t)lrint(dphi(x) * gscale);
    }
}

static void run(double f, double amp)
{
    long n = (long)(2 * FS), i;
    double se = 0, s1 = 0, s2 = 0, s3 = 0, e1 = 0, e2 = 0, e3 = 0, k1 = 0, k2 = 0, k3 = 0;
    int32_t px15 = 0, px30 = 0, pp1 = 0;
    int64_t pp2 = 0;
    /* least-squares scale per variant, then the residual */
    double sy[3] = {0}, syy[3] = {0}, srr = 0, sry[3] = {0};
    static double ref[88200], out[3][88200];
    (void)se; (void)s1; (void)s2; (void)s3; (void)e1; (void)e2; (void)e3; (void)k1; (void)k2; (void)k3; (void)sy;
    for (i = 0; i < n; i++) {
        double x = amp * sin(2 * M_PI * f * i / FS), xp = amp * sin(2 * M_PI * f * (i - 1) / FS);
        int32_t x15 = (int32_t)lrint(x * 32767), x30 = (int32_t)lrint(x * 1073741823.0);
        ref[i] = phi(x) - phi(xp);
        {   /* P1 */
            uint32_t u = (uint32_t)(x15 + 32768), k = u >> 6, fr = u & 63u;
            int32_t p = T15[k] + (((T15[k + 1] - T15[k]) * (int32_t)fr) >> 6);
            out[0][i] = i ? p - pp1 : 0;
            pp1 = p;
        }
        {   /* P2 */
            uint32_t u = (uint32_t)x30 + 0x40000000u, k = u >> 21, fr = (u >> 1) & 0xFFFFFu;
            int64_t p = T30[k] + (((int64_t)(T30[k + 1] - T30[k]) * fr) >> 20);
            out[1][i] = i ? (double)(p - pp2) : 0;
            pp2 = p;
        }
        {   /* P3 */
            uint32_t u = (uint32_t)x30 + 0x40000000u, k = u >> 21, fr = (u >> 6) & 0x7FFFu;
            int32_t g = G15[k] + (((G15[k + 1] - G15[k]) * (int32_t)fr) >> 15), dx = x30 - px30;
            out[2][i] = i ? (double)(((int64_t)g * dx) >> 15) : 0;
            px30 = x30;
        }
        (void)px15;
    }
    for (i = 1; i < n; i++) {
        int v;
        srr += ref[i] * ref[i];
        for (v = 0; v < 3; v++) { syy[v] += out[v][i] * out[v][i]; sry[v] += ref[i] * out[v][i]; }
    }
    printf("f %6.1f Hz, amp %.4f:", f, amp);
    for (int v = 0; v < 3; v++) {
        double a = sry[v] / syy[v], err = 0;
        for (i = 1; i < n; i++) { double d = ref[i] - a * out[v][i]; err += d * d; }
        printf("  P%d SNR %5.1f dB", v + 1, 10 * log10(srr / err));
    }
    printf("\n");
}

int main(void)
{
    tables();
    run(41.2, 0.8); run(41.2, 0.8 / 31.6);
    run(261.6, 0.8); run(261.6, 0.8 / 31.6);
    run(2093, 0.8); run(2093, 0.8 / 31.6);
    return 0;
}
