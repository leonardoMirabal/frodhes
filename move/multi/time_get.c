/* SPDX-License-Identifier: GPL-3.0-only */
/* PROTOTYPE probe (move-multi): the time of one get_param per key (the keys on argv), as the host's re-read does
 * after a write, on one instance of the plugin (build/move/frodhes/dsp.so) */
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "../vendor/plugin_api_v1.h"
static void lg(const char *m) { (void)m; }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec / 1e3; }
int main(int argc, char **argv)
{
    static host_api_v1_t host = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = lg };
    void *h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    plugin_api_v2_t *api = ((move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL))(&host);
    void *in = api->create_instance("build/move/frodhes", NULL);
    static char buf[131072];
    int16_t b[256];
    struct timespec ts = { 0, 2000000 };
    for (int i = 0; i < 300; i++) { api->render_block(in, b, 128); nanosleep(&ts, 0); }
    double tot = 0, worst = 0; const char *wk = "";
    for (int i = 2; i < argc; i++) {
        double t0 = now();
        int r = api->get_param(in, argv[i], buf, sizeof buf);
        double d = now() - t0;
        tot += d;
        if (d > worst) { worst = d; wk = argv[i]; }
        if (d > 50) printf("  %s %.0f us (%d B)\n", argv[i], d, r);
    }
    {
        const char *kv[][2] = { { "p_hq", "1" }, { "dev_cpu_log", "0" }, { "dev_play", "begin zz 1000" }, { "dev_play", "stop" },
                                { "p_drive", "64" }, { "preset", "3" } };
        for (unsigned i = 0; i < sizeof kv / sizeof kv[0]; i++) {
            double t0 = now();
            api->set_param(in, kv[i][0], kv[i][1]);
            printf("  set %s %s: %.0f us\n", kv[i][0], kv[i][1], now() - t0);
        }
    }
    printf("%d keys: %.0f us in all, worst %s %.0f us\n", argc - 2, tot, wk, worst);
    return 0;
}
