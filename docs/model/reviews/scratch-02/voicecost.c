/* Review 02 scratch: host instruction count of a prototype Frodhes voice block (macOS: proc_pid_rusage, as
 * Felucca's tests/drum_test.c:834), to convert with Felucca's device ratio (1.7 % of real time per
 * 100 host instructions per sample, tests/drum_test.c:28). Not firmware code.
 *   per voice: M modes DF2 Q30 with first-order error feedback, mode-outer loops over the block (state in
 *   registers), the hammer force added during contact, x summed into a block buffer; then the pickup
 *   P2 (int32 Phi table, lerp) as a difference, times the voice gain, added to the mono mix.
 * cc -O2 -o voicecost voicecost.c && ./voicecost ; cc -Os ... */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <libproc.h>
#include <sys/resource.h>
#include <unistd.h>

#define NB 256
#define NV 8
typedef struct { int32_t a1, a2, w; int32_t y1, y2, e; } mode_t_;
typedef struct { mode_t_ m[5]; uint32_t nm; int64_t pprev; int32_t gain; } voice_t_;
static int32_t PHI[1025];
static voice_t_ V[NV];
static int32_t xb[NB], mix[NB], hb[NB];

static uint64_t instr(void)
{
    struct rusage_info_v4 ri;
    return proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (rusage_info_t *)&ri) ? 0 : ri.ri_instructions;
}

static __attribute__((noinline)) void mode_run(mode_t_ *m, int32_t *x, const int32_t *h, uint32_t n)
{
    int32_t a1 = m->a1, a2 = m->a2, y1 = m->y1, y2 = m->y2, w = m->w;
    int64_t e = m->e;
    uint32_t i;
    if (h) {
        for (i = 0; i < n; i++) {
            int64_t acc = (int64_t)a1 * y1 + (int64_t)a2 * y2 + e + (int64_t)w * h[i];
            int32_t y = (int32_t)(acc >> 30);
            e = acc & 0x3FFFFFFF;
            y2 = y1; y1 = y;
            x[i] += y >> 2;
        }
    } else {
        for (i = 0; i < n; i++) {
            int64_t acc = (int64_t)a1 * y1 + (int64_t)a2 * y2 + e;
            int32_t y = (int32_t)(acc >> 30);
            e = acc & 0x3FFFFFFF;
            y2 = y1; y1 = y;
            x[i] += y >> 2;
        }
    }
    m->y1 = y1; m->y2 = y2; m->e = (int32_t)e;
}

static __attribute__((noinline)) void pickup_run(voice_t_ *v, const int32_t *x, int32_t *out, uint32_t n)
{
    int64_t pp = v->pprev;
    int32_t g = v->gain;
    uint32_t i;
    for (i = 0; i < n; i++) {
        uint32_t u = (uint32_t)x[i] + 0x40000000u, k = u >> 21, fr = (u >> 5) & 0xFFFFu;
        int32_t a = PHI[k], p = a + (int32_t)(((int64_t)(PHI[k + 1] - a) * fr) >> 16);
        out[i] += (int32_t)(((int64_t)(p - pp) * g) >> 16);
        pp = p;
    }
    v->pprev = pp;
}

static void render(uint32_t nm, int contact)
{
    uint32_t v, k;
    memset(mix, 0, sizeof mix);
    for (v = 0; v < NV; v++) {
        memset(xb, 0, sizeof xb);
        for (k = 0; k < nm; k++)
            mode_run(&V[v].m[k], xb, contact ? hb : 0, NB);
        pickup_run(&V[v], xb, mix, NB);
    }
}

int main(void)
{
    uint32_t i, nm;
    for (i = 0; i <= 1024; i++) PHI[i] = (int32_t)(i * 1000003u);
    for (i = 0; i < NV; i++) {
        uint32_t k;
        for (k = 0; k < 5; k++) { V[i].m[k].a1 = 2147000000 - (int32_t)k * 1000; V[i].m[k].a2 = -1073700000; V[i].m[k].y1 = 1000000; V[i].m[k].w = 1000; }
        V[i].gain = 30000;
    }
    for (nm = 1; nm <= 5; nm++) {
        int c;
        for (c = 0; c < 2; c++) {
            uint64_t t0, t1;
            uint32_t r;
            render(nm, c);
            t0 = instr();
            for (r = 0; r < 200; r++) render(nm, c);
            t1 = instr();
            {
                double per = (double)(t1 - t0) / (200.0 * NB * NV);
                printf("modes %u %-8s: %6.1f host instr per voice-sample -> device ESTIMATE %.2f %% RT per voice,"
                       " %4.0f us / 256-frame block for 8 voices\n", nm, c ? "contact" : "free", per, per * 0.017,
                       per * 0.017 / 100 * 5805 * 8);
            }
        }
    }
    return 0;
}
