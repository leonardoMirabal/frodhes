/* v3 review scratch: the S7 bake-off on the host, DF2 vs MC3, each with and without first-order error
 * feedback (EF1). Int32 state, Q30 coefficients, int64 products, floor shifts: exactly the firmware arithmetic.
 * The reference is a double simulation with the SAME quantised coefficients, so only round-off is measured.
 *
 *   DF2       acc = a1*y1 + a2*y2 (+ e) ; y = acc >> 30 ; (e = acc & 0x3FFFFFFF)         out = y
 *   MC3       t = E*y (+ex); x += t>>30 ; (ex = t&m) ; t = -eps*y - E*x (+ey) ; y += t>>30 ; (ey = t&m)   out = x
 *
 * Reported per case: SNR of the int output vs the double reference (after 0.1 s, over 1 s), the coefficient
 * quantisation error in cents and % T60, the measured T60 of the int output, the residue in the last 0.5 s of a
 * 2.5 x T60 run (limit cycle / dead band), and the RMS error of the per-block amplitude estimate the ISR would use
 * for amplitude -> pitch (DF2: energy form; MC3: x^2 + y^2).
 * cc -O2 -o reson_bakeoff reson_bakeoff.c -lm && ./reson_bakeoff */
#include <stdint.h>
#include <stdio.h>
#include <math.h>

#define FS 44100.0
#define Q30 1073741824.0
#define MASK 0x3FFFFFFFLL

typedef struct { const char *name; double f, t60; } mode_case_t;

static void run(const mode_case_t *c, double peak)
{
    double r = exp(-6.907755279 / (c->t60 * FS)), th = 2 * M_PI * c->f / FS;
    /* DF2 coefficients: a2 rounded toward MORE damping (ceil of -r^2), a1 nearest */
    int32_t a1 = (int32_t)llround(2 * r * cos(th) * Q30);
    int32_t a2 = (int32_t)ceil(-r * r * Q30);
    /* MC3 coefficients: E nearest, eps = 1 - r^2 rounded UP (more damping) */
    int32_t E = (int32_t)llround(sqrt(1 + r * r - 2 * r * cos(th)) * Q30);
    int32_t EPS = (int32_t)ceil((1 - r * r) * Q30);
    double A1 = a1 / Q30, A2 = a2 / Q30, Eq = E / Q30, EPSq = EPS / Q30;
    double rq = sqrt(-A2), thq = acos(A1 / (2 * rq));
    double Dq = 1 - EPSq, rq2 = sqrt(Dq), thq2 = acos((1 + Dq - Eq * Eq) / (2 * rq2));
    long n_snr = (long)((c->t60 < 1.0 ? c->t60 : 1.0) * FS), n_tail = (long)(2.5 * c->t60 * FS) + (long)FS, i, v, n0 = n_snr / 10;
    double sin2 = sin(thq) * sin(thq);
    printf("\n%s: f %.1f Hz, T60 %.2f s, peak 2^%.0f; 1-r = %.3e\n", c->name, c->f, c->t60, log2(peak), 1 - r);
    printf("  coef quantisation: DF2 %+.4f c, T60 %+.3f %% | MC3 %+.4f c, T60 %+.3f %%\n",
           1200 * log2(thq / th), 100 * (log(r) / log(rq) - 1), 1200 * log2(thq2 / th), 100 * (log(r) / log(rq2) - 1));
    for (v = 0; v < 4; v++) {
        static const char *const NM[] = {"DF2-floor", "DF2-EF1  ", "MC3-floor", "MC3-EF1  "};
        int32_t y1 = (int32_t)peak, y2 = (int32_t)llround(peak * cos(thq)), x = 0, y = (int32_t)peak;   /* DF2: a sinusoid of amplitude peak */
        int64_t e = 0, ex = 0, ey = 0;
        double d1 = peak, d2 = peak * cos(thq), dx = 0, dy = peak;
        double err2 = 0, sig2 = 0, aerr2 = 0, an = 0, tailmax = 0, win = 0, t60m = -1, nextdb = -60;
        long per = (long)(FS / c->f) + 1, cnt = 0;
        for (i = 0; i < n_tail; i++) {
            double ref, out;
            if (v < 2) {
                int64_t acc = (int64_t)a1 * y1 + (int64_t)a2 * y2 + (v == 1 ? e : 0);
                int32_t yn = (int32_t)(acc >> 30);
                if (v == 1) e = acc & MASK;
                ref = A1 * d1 + A2 * d2;
                d2 = d1; d1 = ref; y2 = y1; y1 = yn; out = yn;
                if (i < n_snr && (i & 31) == 0 && i > n0) {      /* amplitude estimate, energy form */
                    int64_t q = (int64_t)y1 * y1 + (int64_t)y2 * y2 - (int64_t)(((int64_t)a1 * y1) >> 30) * y2;
                    double a_est = sqrt((double)q / sin2), a_true = peak * pow(rq, (double)i) * sin(thq) / sin(thq);
                    aerr2 += (a_est / a_true - 1) * (a_est / a_true - 1); an++;
                }
            } else {
                int64_t t = (int64_t)E * y + (v == 3 ? ex : 0);
                x += (int32_t)(t >> 30); if (v == 3) ex = t & MASK;
                t = -(int64_t)EPS * y - (int64_t)E * x + (v == 3 ? ey : 0);
                y += (int32_t)(t >> 30); if (v == 3) ey = t & MASK;
                dx += Eq * dy; dy = dy - EPSq * dy - Eq * dx;
                ref = dx; out = x;
                if (i < n_snr && (i & 31) == 0 && i > n0) {
                    double a_est = sqrt((double)x * x + (double)y * y), a_true = peak * pow(rq2, (double)i);
                    aerr2 += (a_est / a_true - 1) * (a_est / a_true - 1); an++;
                }
            }
            if (i > n0 && i < n_snr) { err2 += (out - ref) * (out - ref); sig2 += ref * ref; cnt++; }
            if (fabs(out) > win) win = fabs(out);
            if (i % per == per - 1) { if (t60m < 0 && 20 * log10(win / peak) < nextdb) t60m = i / FS; win = 0; }
            if (i > n_tail - (long)FS / 2 && fabs(out) > tailmax) tailmax = fabs(out);
        }
        printf("  %s SNR %6.1f dB | T60 meas %7.2f s | last 0.5 s of 2.5xT60: |out| max %6.0f LSB | amp-est RMS err %.2f %%\n",
               NM[v], 10 * log10(sig2 / (err2 + 1e-30)), t60m, tailmax, 100 * sqrt(aerr2 / (an > 0 ? an : 1)));
    }
}

int main(void)
{
    static const mode_case_t C[] = {
        {"E1 V1", 41.2, 20.0}, {"E2 V1", 82.4, 18.0}, {"A4 V1", 440.0, 8.0}, {"C7 V1", 2093.0, 1.5},
        {"C8 V1", 4186.0, 1.0}, {"E1 O1 (7.1x)", 292.5, 0.4}, {"A4 O2 (20x)", 8800.0, 0.02}, {"E1 V2 (bar)", 41.6, 4.0}};
    unsigned k;
    for (k = 0; k < sizeof C / sizeof C[0]; k++) { run(&C[k], 268435456.0); run(&C[k], 268435456.0 / 1000.0); }
    return 0;
}
