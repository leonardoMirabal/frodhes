/* SPDX-License-Identifier: GPL-3.0-only */
/* M2-01: the plugin's own cost per 128-frame callback, the base the HQ budget sits on (private plan 06).
 *   classic_bench [-r ROUNDS] [-s SECONDS] [-e ENGINES] [-x SEED] CASE...      CASE = DSP or DSP:top
 * dlopens each case's dsp.so through its plugin entry points (each case its own file, its own instance) and, per
 * engine (ENGINES: a subset of the digits 012 = STAGE 77, VIBES, MARIMBA; default all), plays the heavy case: every
 * effect on (Gate R's fx/heavy CCs), the pedal down, a 16-note chord at v127 struck again every 400 ms (each strike
 * takes 16 voices: steals, the attack's cost), for SECONDS (default 5). top: the chord on the 16 keys 85..108
 * instead (the HQ Rhodes' 2x pickup on every voice: its worst case; a dsp.so built with -DHQ_DEFAULT=1).
 * Every round runs each case x engine once, in an order shuffled per round (SEED, default 1), so the device's drift
 * and the other cores' load fall on every case alike: the Move has system-wide stretches of +300-400 us that last a
 * few runs. Prints one line per run, then per case and engine the summary: the median of p99.9, the min of the max,
 * the median of p99 and of the mean (us per callback), and the median of p99.9 over the quiet rounds only. A round's
 * noise index is the median, over its runs, of each run's p99.9 / the median p99.9 of the same case and engine;
 * a round is quiet when its index is <= 1.10. Many short rounds are better than few long ones (-r 15 -s 2).
 * Each case's copy of its file and its state directory are made in the current directory and removed at the end
 * (never /tmp: on the Move it is the small root file system). Run it on the Move off the audio core, from
 * /data/UserData/...:   taskset -c 1 ./classic_bench -r 15 -s 2 c_o2.so c_o3.so c_o2.so:top c_o3.so:top */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <ftw.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"

#define MAXC 16
#define MAXR 64
#define MAXB 20000

static double now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec * 1e-3; }
static void h_log(const char *m) { (void)m; }
static int h_clock(void) { return MOVE_CLOCK_STATUS_STOPPED; }
static float h_bpm(void) { return 120.0f; }
static double h_beat(void) { return -1.0; }
static host_api_v1_t host = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = h_log,
                              .get_clock_status = h_clock, .get_bpm = h_bpm, .get_beat_position = h_beat };
static int cmp(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return x < y ? -1 : x > y; }
static int rm1(const char *p, const struct stat *s, int f, struct FTW *w) { (void)s; (void)f; (void)w; return remove(p); }

typedef struct { const char *name; int top; plugin_api_v2_t *api; void *in; char dir[64]; } bcase_t;
static bcase_t cs[MAXC];
static double st[MAXC][3][MAXR][4];     /* per case, engine, round: max, p99.9, p99, mean */

static void cc(bcase_t *c, int n, int v) { uint8_t m[3] = { 0xB0, (uint8_t)n, (uint8_t)v }; c->api->on_midi(c->in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
static int copy(const char *from, const char *to)
{
    char b[65536];
    size_t n;
    FILE *f = fopen(from, "rb"), *g = f ? fopen(to, "wb") : NULL;
    int bad = !f || !g;
    while (!bad && (n = fread(b, 1, sizeof b, f)) > 0) bad = fwrite(b, 1, n, g) != n;
    if (f) fclose(f);
    if (g && fclose(g)) bad = 1;
    return bad;
}
static double medn(double *x, int n) { double s[MAXC * 3]; memcpy(s, x, sizeof(double) * (size_t)n); qsort(s, (size_t)n, sizeof *s, cmp); return n & 1 ? s[n / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]); }
static double med(double *x, int n) { double s[MAXR]; memcpy(s, x, sizeof(double) * (size_t)n); qsort(s, (size_t)n, sizeof *s, cmp); return n & 1 ? s[n / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]); }

static void run(bcase_t *c, int e, double secs, double *out)
{
    static const int heavy[][2] = { {81, 127}, {106, 127}, {107, 100}, {17, 127}, {18, 127}, {12, 127}, {13, 127},
                                    {16, 127}, {88, 80}, {89, 127}, {90, 127}, {26, 127}, {91, 127}, {70, 45},
                                    {74, 60}, {71, 100}, {72, 64} };
    static int16_t buf[256];
    static double t[MAXB];
    int nb = (int)(secs * 44100.0 / 128.0), k = 0;
    double mean = 0;
    if (nb > MAXB) nb = MAXB;
    if (e == 0) cc(c, 80, 0); else cc(c, 79, e == 1 ? 0 : 127);
    cc(c, 85, 0);
    for (unsigned i = 0; i < sizeof heavy / sizeof heavy[0]; i++) cc(c, heavy[i][0], heavy[i][1]);
    cc(c, 64, 127);
    for (int i = 0; i < 400; i++) c->api->render_block(c->in, buf, 128);
    for (int b = 0; b < nb; b++) {
        if (b % 138 == 0) {                 /* every ~400 ms: 16 notes at v127 (28..103 spread, or 85..108) */
            for (int n = 0; n < 16; n++) {
                uint8_t m[3] = { 0x90, (uint8_t)(c->top ? 85 + (n * 3 + (b / 138)) % 24 : 28 + n * 5 + (b / 138) % 5), 127 };
                c->api->on_midi(c->in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL);
            }
        }
        double t0 = now_us();
        c->api->render_block(c->in, buf, 128);
        t[k++] = now_us() - t0;
    }
    for (int i = 0; i < k; i++) mean += t[i];
    qsort(t, (size_t)k, sizeof *t, cmp);
    out[0] = t[k - 1]; out[1] = t[(int)(k * 0.999)]; out[2] = t[(int)(k * 0.99)]; out[3] = mean / k;
    cc(c, 64, 0); cc(c, 123, 0);
    for (int i = 0; i < 2000; i++) c->api->render_block(c->in, buf, 128);
}

int main(int argc, char **argv)
{
    static const char *eng[] = { "STAGE77", "VIBES", "MARIMBA" };
    static int16_t buf[256];
    const char *engs = "012";
    double secs = 5.0;
    int rounds = 5, nc = 0, o, ne = 0, ord[MAXC * 3], en_of[3];
    uint32_t seed = 1;
    double noise[MAXR];
    int quiet[MAXR];
    while ((o = getopt(argc, argv, "r:s:e:x:")) != -1) {
        if (o == 'r') rounds = atoi(optarg);
        else if (o == 's') secs = atof(optarg);
        else if (o == 'e') engs = optarg;
        else if (o == 'x') seed = (uint32_t)strtoul(optarg, NULL, 0);
        else { fprintf(stderr, "usage: classic_bench [-r ROUNDS] [-s SECONDS] [-e 012] [-x SEED] DSP[:top]...\n"); return 2; }
    }
    if (rounds < 1 || rounds > MAXR || optind >= argc || argc - optind > MAXC) {
        fprintf(stderr, "usage: classic_bench [-r ROUNDS(1-%d)] [-s SECONDS] [-e 012] DSP[:top]... (at most %d)\n", MAXR, MAXC);
        return 2;
    }
    for (int a = optind; a < argc; a++, nc++) {
        bcase_t *c = &cs[nc];
        char *p = strdup(argv[a]), *colon = strrchr(p, ':'), r[8], so[96];
        void *h;
        move_plugin_init_v2_fn init;
        c->name = argv[a];
        if (colon && !strcmp(colon, ":top")) { *colon = 0; c->top = 1; }
        /* each case loads its own copy of the file, so the same dsp.so can be two cases (normal and top) with
         * separate module state */
        snprintf(c->dir, sizeof c->dir, "./classic_bench.XXXXXX");
        if (!mkdtemp(c->dir)) { perror("mkdtemp"); return 1; }
        snprintf(so, sizeof so, "%s/dsp.so", c->dir);
        if (copy(p, so)) { fprintf(stderr, "copy %s: failed\n", p); return 1; }
        if (!(h = dlopen(so, RTLD_NOW | RTLD_LOCAL)) || !(init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL))) {
            fprintf(stderr, "dlopen %s: %s\n", p, dlerror()); return 1; }
        c->api = init(&host);
        if (!(c->in = c->api->create_instance(c->dir, NULL))) { fprintf(stderr, "create %s\n", p); return 1; }
        for (int i = 0; i < 5000; i++) { r[0] = 0; c->api->get_param(c->in, "dev_ready", r, sizeof r); if (r[0] == '1') break; c->api->render_block(c->in, buf, 128); usleep(1000); }
    }
    for (const char *e = engs; *e; e++) if (*e >= '0' && *e <= '2' && ne < 3) en_of[ne++] = *e - '0';
    if (!ne) { fprintf(stderr, "no engine in -e %s\n", engs); return 2; }
    for (int r = 0; r < rounds; r++) {
        int n = nc * ne;
        for (int j = 0; j < n; j++) ord[j] = j;
        for (int j = n - 1; j > 0; j--) {                       /* Fisher-Yates, a fixed LCG: the same order per seed */
            int q, t;
            seed = seed * 1664525u + 1013904223u;
            q = (int)((seed >> 8) % (uint32_t)(j + 1));
            t = ord[j]; ord[j] = ord[q]; ord[q] = t;
        }
        for (int j = 0; j < n; j++) {
            int ci = ord[j] / ne, en = en_of[ord[j] % ne];
            run(&cs[ci], en, secs, st[ci][en][r]);
            printf("round %d  %-24s %-8s max %7.1f  p99.9 %7.1f  p99 %7.1f  mean %6.1f\n", r + 1, cs[ci].name, eng[en],
                   st[ci][en][r][0], st[ci][en][r][1], st[ci][en][r][2], st[ci][en][r][3]);
            fflush(stdout);
        }
    }
    {   /* the noise index per round */
        double base[MAXC][3], rel[MAXC * 3];
        for (int i = 0; i < nc; i++)
            for (int j = 0; j < ne; j++) {
                double col[MAXR];
                for (int r = 0; r < rounds; r++) col[r] = st[i][en_of[j]][r][1];
                base[i][j] = med(col, rounds);
            }
        printf("\nnoise index per round (1.00 = typical; quiet <= 1.10):");
        for (int r = 0; r < rounds; r++) {
            int m = 0;
            for (int i = 0; i < nc; i++)
                for (int j = 0; j < ne; j++) rel[m++] = base[i][j] > 0 ? st[i][en_of[j]][r][1] / base[i][j] : 1.0;
            noise[r] = medn(rel, m);
            quiet[r] = noise[r] <= 1.10;
            printf(" %d:%.2f%s", r + 1, noise[r], quiet[r] ? "" : "*");
        }
        printf("\n");
    }
    printf("\nsummary over %d rounds of %.1f s (us per 128-frame callback)\n", rounds, secs);
    printf("%-24s %-8s %10s %10s %10s %10s %12s\n", "case", "engine", "med p99.9", "min max", "med p99", "med mean",
           "quiet p99.9");
    for (int i = 0; i < nc; i++)
        for (int j = 0; j < ne; j++) {
            int en = en_of[j], nq = 0;
            double col[4][MAXR], qc[MAXR], mn = 1e18;
            for (int r = 0; r < rounds; r++) {
                for (int q = 0; q < 4; q++) col[q][r] = st[i][en][r][q];
                if (col[0][r] < mn) mn = col[0][r];
                if (quiet[r]) qc[nq++] = st[i][en][r][1];
            }
            printf("%-24s %-8s %10.1f %10.1f %10.1f %10.1f", cs[i].name, eng[en], med(col[1], rounds), mn,
                   med(col[2], rounds), med(col[3], rounds));
            if (nq) printf(" %8.1f (%d)\n", med(qc, nq), nq); else printf("        - (0)\n");
        }
    for (int i = 0; i < nc; i++) { cs[i].api->destroy_instance(cs[i].in); nftw(cs[i].dir, rm1, 8, FTW_DEPTH | FTW_PHYS); }
    return 0;
}
