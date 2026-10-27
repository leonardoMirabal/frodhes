/* SPDX-License-Identifier: GPL-3.0-only */
/* M2-01: the Classic plugin's own cost per 128-frame callback, the base the HQ budget sits on (private plan 06).
 *   classic_bench DSP [seconds] [top]
 * dlopens the shipping dsp.so through its plugin entry points, and per engine (STAGE 77, VIBES, MARIMBA) plays the
 * heavy case: every effect on (Gate R's fx/heavy CCs), the pedal down, a 16-note chord at v127 struck again every
 * 400 ms (each strike takes 16 voices: steals, the attack's cost), for N seconds (default 10). Prints per engine the
 * callback time: max, p99.9, p99, the mean, and the mean of the render callbacks (every other one renders 256
 * frames). top: the chord on the 16 keys 85..108 instead (the HQ Rhodes' 2x pickup on every voice: its worst case;
 * a dsp.so built with -DHQ_DEFAULT=1). Run it on the Move off the audio core: taskset -c 1 ./classic_bench dsp.so */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"

static double now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec * 1e-3; }
static void h_log(const char *m) { (void)m; }
static int h_clock(void) { return MOVE_CLOCK_STATUS_STOPPED; }
static float h_bpm(void) { return 120.0f; }
static double h_beat(void) { return -1.0; }
static host_api_v1_t host = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = h_log,
                              .get_clock_status = h_clock, .get_bpm = h_bpm, .get_beat_position = h_beat };
static int cmp(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return x < y ? -1 : x > y; }
static plugin_api_v2_t *api;
static void *in;
static void cc(int n, int v) { uint8_t m[3] = { 0xB0, (uint8_t)n, (uint8_t)v }; api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }

int main(int argc, char **argv)
{
    static const int heavy[][2] = { {81, 127}, {106, 127}, {107, 100}, {17, 127}, {18, 127}, {12, 127}, {13, 127},
                                    {16, 127}, {88, 80}, {89, 127}, {90, 127}, {26, 127}, {91, 127}, {70, 45},
                                    {74, 60}, {71, 100}, {72, 64} };
    static const char *eng[] = { "STAGE77", "VIBES", "MARIMBA" };
    static int16_t buf[256];
    static double t[20000];
    char tmpl[] = "/tmp/classic_bench.XXXXXX", r[8];
    double secs = argc > 2 ? atof(argv[2]) : 10.0;
    int top = argc > 3 && !strcmp(argv[3], "top");
    void *h;
    move_plugin_init_v2_fn init;
    if (argc < 2) { fprintf(stderr, "usage: classic_bench DSP [seconds]\n"); return 2; }
    if (!(h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) || !(init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL))) {
        fprintf(stderr, "dlopen %s: %s\n", argv[1], dlerror()); return 1; }
    api = init(&host);
    if (!mkdtemp(tmpl) || !(in = api->create_instance(tmpl, NULL))) { fprintf(stderr, "create\n"); return 1; }
    for (int i = 0; i < 5000; i++) { r[0] = 0; api->get_param(in, "dev_ready", r, sizeof r); if (r[0] == '1') break; api->render_block(in, buf, 128); usleep(1000); }
    for (int e = 0; e < 3; e++) {
        int nb = (int)(secs * 44100.0 / 128.0), k = 0;
        if (nb > 20000) nb = 20000;
        if (e == 0) cc(80, 0); else cc(79, e == 1 ? 0 : 127);
        cc(85, 0);
        for (unsigned i = 0; i < sizeof heavy / sizeof heavy[0]; i++) cc(heavy[i][0], heavy[i][1]);
        cc(64, 127);
        for (int i = 0; i < 400; i++) api->render_block(in, buf, 128);
        for (int b = 0; b < nb; b++) {
            if (b % 138 == 0) {                 /* every ~400 ms: 16 notes at v127 (28..103, spread) */
                for (int n = 0; n < 16; n++) {
                    uint8_t m[3] = { 0x90, (uint8_t)(top ? 85 + (n * 3 + (b / 138)) % 24 : 28 + n * 5 + (b / 138) % 5), 127 };
                    api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL);
                }
            }
            double t0 = now_us();
            api->render_block(in, buf, 128);
            t[k++] = now_us() - t0;
        }
        {
            double mean = 0, rmean = 0, rn = 0;
            for (int i = 0; i < k; i++) { mean += t[i]; if (t[i] > 0) { } }
            mean /= k;
            double *s = malloc(sizeof(double) * (size_t)k);
            memcpy(s, t, sizeof(double) * (size_t)k);
            qsort(s, (size_t)k, sizeof *s, cmp);
            for (int i = k / 2; i < k; i++) { rmean += s[i]; rn++; }   /* (the upper half: the render callbacks) */
            printf("%-8s callbacks %d  max %7.1f us  p99.9 %7.1f  p99 %7.1f  mean %6.1f  render-callback mean %6.1f\n",
                   eng[e], k, s[k - 1], s[(int)(k * 0.999)], s[(int)(k * 0.99)], mean, rmean / rn);
            free(s);
        }
        cc(64, 0); cc(123, 0);
        for (int i = 0; i < 2000; i++) api->render_block(in, buf, 128);
    }
    api->destroy_instance(in);
    return 0;
}
