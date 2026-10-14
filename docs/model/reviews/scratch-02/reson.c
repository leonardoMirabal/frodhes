/* Review 02 scratch: fixed-point 2-pole resonator round-off on the host (no firmware code).
 * Compares, for a lightly damped low mode (E1, T60 18 s) and a mid mode, the int32-state / Q30-coefficient
 * forms the plan could use, against a double simulation with the SAME quantised coefficients
 * (so only the arithmetic round-off is measured):
 *   DF-floor   y = (a1 y1 + a2 y2) >> 30                     (arithmetic shift = floor)
 *   DF-round   y = (a1 y1 + a2 y2 + 2^29) >> 30
 *   DF-zero    y = trunc toward zero
 *   DF-ef2     floor + 2nd-order error feedback (acc += 2 e1 - e2): the round-off is shaped by (1 - z^-1)^2
 *   CPL-floor  coupled form u' = rc u - rs v, v' = rs u + rc v (Q30), floor
 * Report: residual round-off error RMS (dB re the start peak), the DC offset, and the level the free
 * ringing settles at once the ideal signal is below 1 LSB (limit cycle / dead band).
 * cc -O2 -o reson reson.c -lm && ./reson */
#include <stdint.h>
#include <stdio.h>
#include <math.h>

#define FS 44100.0
static int64_t fl30(int64_t a) { return a >> 30; }                       /* floor */
static int64_t rn30(int64_t a) { return (a + (1 << 29)) >> 30; }         /* round half up */
static int64_t tz30(int64_t a) { return a < 0 ? -((-a) >> 30) : a >> 30; } /* toward zero */

static void run(const char *name, double f, double t60, double peak, double secs)
{
    double r = exp(-6.907755 / (t60 * FS)), th = 2 * M_PI * f / FS;
    int32_t a1 = (int32_t)llround(2 * r * cos(th) * 1073741824.0);
    int32_t a2 = (int32_t)llround(-r * r * 1073741824.0);
    int32_t rc = (int32_t)llround(r * cos(th) * 1073741824.0), rs = (int32_t)llround(r * sin(th) * 1073741824.0);
    double A1 = a1 / 1073741824.0, A2 = a2 / 1073741824.0, RC = rc / 1073741824.0, RS = rs / 1073741824.0;
    long n = (long)(secs * FS), i;
    int v;
    printf("\n%s: f %.1f Hz, T60 %.1f s, peak %.0f LSB (2^%.1f), %.0f s; 1-r = %.2e\n", name, f, t60, peak,
           log2(peak), secs, 1 - r);
    {   /* what the quantised coefficients do to the pitch and the decay */
        double rq = sqrt(-A2), thq = acos(A1 / (2 * rq));
        double rq2 = sqrt(RC * RC + RS * RS), thq2 = atan2(RS, RC);
        printf("  coef quantisation: DF  %+.4f cents, T60 %+.3f %%;  coupled %+.4f cents, T60 %+.3f %%\n",
               1200 * log2(thq / th), 100 * (log(r) / log(rq) - 1), 1200 * log2(thq2 / th), 100 * (log(r) / log(rq2) - 1));
    }
    for (v = 0; v < 6; v++) {
        static const char *const NM[] = {"DF-floor", "DF-round", "DF-zero", "DF-ef2", "CPL-floor", "DF-ef1"};
        double d1 = peak * sin(th), d2 = 0, du = 0, dv = peak;   /* double reference, same quantised coefs */
        int32_t y1 = (int32_t)llround(d1), y2 = 0, u = 0, w = (int32_t)peak;
        int64_t e1 = 0, e2 = 0;
        double err2 = 0, sum = 0, tailmax = 0, ideal_end = 0, t60m = -1, win = 0; long per = (long)(FS / f) + 1;
        long cnt = 0;
        if (v == 4) { du = 0; dv = peak; }
        for (i = 0; i < n; i++) {
            double ref, out;
            if (v < 4 || v == 5) {
                int64_t acc = (int64_t)a1 * y1 + (int64_t)a2 * y2;
                int32_t y;
                if (v == 3) acc += 2 * e1 - e2;
                if (v == 5) acc += e1;
                y = (int32_t)(v == 0 || v == 3 || v == 5 ? fl30(acc) : v == 1 ? rn30(acc) : tz30(acc));
                if (v == 3 || v == 5) { e2 = e1; e1 = acc - ((int64_t)y << 30); }
                ref = A1 * d1 + A2 * d2;
                d2 = d1; d1 = ref;
                y2 = y1; y1 = y;
                out = y;
            } else {
                int32_t nu = (int32_t)fl30((int64_t)rc * u - (int64_t)rs * w);
                int32_t nw = (int32_t)fl30((int64_t)rs * u + (int64_t)rc * w);
                double ru = RC * du - RS * dv, rw = RS * du + RC * dv;
                u = nu; w = nw; du = ru; dv = rw;
                ref = du; out = u;
            }
            if (fabs(out) > win) win = fabs(out);
            if (i % per == per - 1) { if (t60m < 0 && win < peak / 1000) t60m = i / FS; win = 0; }
            if (i > n / 4 && i < n / 2) { err2 += (out - ref) * (out - ref); sum += out - ref; cnt++; }
            if (i > n - (long)FS) { if (fabs(out) > tailmax) tailmax = fabs(out); ideal_end = fabs(ref) > ideal_end ? fabs(ref) : ideal_end; }
        }
        printf("  %-9s round-off RMS %7.1f LSB = %6.1f dB re peak, DC %+8.1f LSB; last second: |y| max %8.0f LSB"
               " (ideal %.2g); T60 %.2f s\n", NM[v], sqrt(err2 / cnt), 20 * log10(sqrt(err2 / cnt) / peak + 1e-30), sum / cnt, tailmax, ideal_end, t60m);
    }
}

int main(void)
{
    double ff = 268435456.0;      /* the state's peak for a hard hit: 2^28 (headroom x4 below int32) */
    run("E1 tonebar, ff", 41.2, 18, ff, 60);
    run("E1 tonebar, pp (-30 dB)", 41.2, 18, ff / 31.6, 60);
    run("C4 fundamental, ff", 261.6, 10, ff, 30);
    run("C6, ff", 1046.5, 3, ff, 10);
    run("O1 of C4 (7.1 f0), ff", 1857, 0.4, ff, 2);
    return 0;
}
