/* SPDX-License-Identifier: GPL-3.0-only */
/* make test-move (M1-01 stub): dlopen build/move/frodhes/dsp.so, check the entry point and the API version,
 * create an instance, render and feed it a little, destroy it. Grows into the replay harness (M1-09). */
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include "../vendor/plugin_api_v1.h"

static void lg(const char *m) { (void)m; }
int main(int argc, char **argv)
{
    static host_api_v1_t host = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = lg };
    const char *so = argc > 1 ? argv[1] : "build/move/frodhes/dsp.so";
    void *h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("FAIL dlopen: %s\n", dlerror()); return 1; }
    move_plugin_init_v2_fn init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!init) { printf("FAIL no %s\n", MOVE_PLUGIN_INIT_V2_SYMBOL); return 1; }
    plugin_api_v2_t *api = init(&host);
    if (!api || api->api_version != MOVE_PLUGIN_API_VERSION_2) { printf("FAIL api version\n"); return 1; }
    void *in = api->create_instance("build/move/frodhes", NULL);
    if (!in) { printf("FAIL create_instance\n"); return 1; }
    int16_t blk[256];
    const uint8_t on[3] = { 0x90, 60, 100 }, off[3] = { 0x80, 60, 0 };
    char buf[64];
    for (int b = 0; b < 100; b++) {
        if (b == 10) api->on_midi(in, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
        if (b == 60) api->on_midi(in, off, 3, MOVE_MIDI_SOURCE_EXTERNAL);
        api->render_block(in, blk, 128);
    }
    api->set_param(in, "unknown", "1");
    api->get_param(in, "unknown", buf, sizeof buf);
    api->destroy_instance(in);
    dlclose(h);
    printf("ok    move smoke (%s)\n", so);
    return 0;
}
