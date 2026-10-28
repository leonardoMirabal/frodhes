/* SPDX-License-Identifier: GPL-3.0-only */
/* PROTOTYPE test (move-multi): the loader's dsp.so as Schwung uses it in two slots: one dlopen, the init call with
 * each slot's host table just before its create. Checks: two live instances play independently (a note into A
 * sounds in A only, then one into B in B only, both at once), each its own copy (ml_inst), a re-pick (create before
 * destroy) takes a third copy, and a later create reuses a freed one. */
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "../vendor/plugin_api_v1.h"

static void lg(const char *m) { (void)m; }
static plugin_api_v2_t *api;
static double energy(void *in, int blocks)
{
    int16_t b[256];
    double e = 0;
    for (int i = 0; i < blocks; i++) {
        api->render_block(in, b, 128);
        for (int j = 0; j < 256; j++) e += (double)b[j] * b[j];
    }
    return e;
}
static void warm(void *in)
{
    struct timespec t = { 0, 2000000 };
    for (int i = 0; i < 300; i++) { (void)energy(in, 1); nanosleep(&t, 0); }
}
static int inst_k(void *in) { char b[16] = ""; api->get_param(in, "ml_inst", b, sizeof b); return b[0] ? b[0] - '0' : -1; }
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL " __VA_ARGS__); printf("\n"); return 1; } } while (0)

int main(int argc, char **argv)
{
    static host_api_v1_t ha = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = lg };
    static host_api_v1_t hb = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = lg };
    const char *dir = argc > 1 ? argv[1] : "build/move-multi/frodhes";
    char so[512];
    const uint8_t on[3] = { 0x90, 60, 110 };
    snprintf(so, sizeof so, "%s/dsp.so", dir);
    void *h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    CHECK(h, "dlopen %s", dlerror());
    move_plugin_init_v2_fn init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    api = init(&ha);
    void *a = api->create_instance(dir, NULL);
    (void)init(&hb);
    void *b = api->create_instance(dir, NULL);
    CHECK(a && b, "create");
    CHECK(inst_k(a) == 0 && inst_k(b) == 1, "copies %d %d", inst_k(a), inst_k(b));
    warm(a); warm(b);
    double ea0 = energy(a, 50), eb0 = energy(b, 50);
    CHECK(ea0 == 0 && eb0 == 0, "silent at the start %g %g", ea0, eb0);
    api->on_midi(a, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    double ea = energy(a, 200), eb = energy(b, 200);
    CHECK(ea > 1e6 && eb == 0, "note into A: A %g B %g", ea, eb);
    api->on_midi(b, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    ea = energy(a, 200); eb = energy(b, 200);
    CHECK(ea > 1e6 && eb > 1e6, "note into B: both sound: A %g B %g", ea, eb);
    printf("ok    two instances play on their own (A %.3g, B %.3g)\n", ea, eb);
    (void)init(&ha);
    void *c = api->create_instance(dir, NULL);      /* the re-pick of slot A: create first */
    CHECK(c && inst_k(c) == 2, "re-pick copy %d", c ? inst_k(c) : -1);
    api->destroy_instance(a);
    warm(c);
    ea = energy(c, 50);
    eb = energy(b, 50);
    CHECK(eb > 1e6, "B goes on playing after A's destroy: %g", eb);
    void *d = api->create_instance(dir, NULL);
    CHECK(d && inst_k(d) == 0, "a freed copy reused: %d", d ? inst_k(d) : -1);
    printf("ok    re-pick: copy 2, then the freed copy 0 reused; B still plays\n");
    api->destroy_instance(b); api->destroy_instance(c); api->destroy_instance(d);
    printf("ok    move-multi loader\n");
    return 0;
}
