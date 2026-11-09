/* SPDX-License-Identifier: GPL-3.0-only */
/* The oversampler alone (plugin/src/frp_os.h), for tools/plugin_os.py:
 *   os_test tone RATE FREQ FRAMES OUT.f32   a 44.1 kHz sine of FREQ Hz (amplitude 0.5) through the chain to RATE:
 *                                           FRAMES stereo float32 frames into OUT (L only written twice: L R), and on
 *                                           stdout "lat N" (the latency the chain placed, output frames)
 *   os_test cpu RATE SECONDS                the chain's time for SECONDS of output (stereo): "cpu RATE us_per_s"
 *   os_test lat RATE LATMIN                 the latency placed for LATMIN */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "../src/frp_os.h"

typedef struct { double w; uint64_t n; } tone_t;
static void tone_src(void *ctx, float *l, float *r)
{
    tone_t *t = ctx;
    float v = (float)(0.5 * sin(t->w * (double)t->n++));
    *l = v; *r = -v;
}
int main(int argc, char **argv)
{
    static frp_os_t os;
    tone_t t = {0, 0};
    if (argc >= 6 && !strcmp(argv[1], "tone")) {
        double rate = atof(argv[2]), f = atof(argv[3]);
        uint32_t n = (uint32_t)atol(argv[4]), lat;
        float *l = malloc(n * sizeof *l), *r = malloc(n * sizeof *r);
        FILE *o = fopen(argv[5], "wb");
        if (!l || !r || !o) return 1;
        t.w = 2.0 * M_PI * f / 44100.0;
        lat = frp_os_init(&os, rate, 0, tone_src, &t);
        frp_os_render(&os, l, r, n);
        for (uint32_t i = 0; i < n; i++) { fwrite(&l[i], 4, 1, o); fwrite(&r[i], 4, 1, o); }
        fclose(o);
        printf("lat %u\n", lat);
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "cpu")) {
        double rate = atof(argv[2]), secs = atof(argv[3]);
        uint32_t n = 512, total = (uint32_t)(rate * secs), done = 0;
        float l[512], r[512];
        struct timespec a, b;
        t.w = 2.0 * M_PI * 1000.0 / 44100.0;
        frp_os_init(&os, rate, 0, tone_src, &t);
        clock_gettime(CLOCK_MONOTONIC, &a);
        while (done < total) { frp_os_render(&os, l, r, n); done += n; }
        clock_gettime(CLOCK_MONOTONIC, &b);
        printf("cpu %.0f %.1f\n", rate, ((b.tv_sec - a.tv_sec) * 1e6 + (b.tv_nsec - a.tv_nsec) / 1e3) / secs);
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "lat")) {
        printf("lat %u\n", frp_os_init(&os, atof(argv[2]), (uint32_t)atol(argv[3]), tone_src, &t));
        return 0;
    }
    fprintf(stderr, "usage: os_test tone RATE FREQ FRAMES OUT | cpu RATE SECONDS | lat RATE LATMIN\n");
    return 2;
}
