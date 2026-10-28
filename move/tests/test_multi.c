/* SPDX-License-Identifier: GPL-3.0-only */
/* make test-move: several Frodhes through the loader (move/move_loader.c), as Schwung uses it: one dlopen of dsp.so,
 * the init call with each slot's host table just before its create.
 *
 *   test_multi DSP DIR     DSP the loader (engine.so beside it); DIR an empty scratch folder
 *
 * The module (dsp.so + engine.so) is copied into DIR/mod, so the update check can change engine.so. Checks:
 *   - two live instances, each its own copy (dev_inst 0 and 1), play on their own: a note into A sounds in A only,
 *     one into B in B too; A's knob leaves B's alone;
 *   - the shared store: the user presets another instance wrote to DIR/mod/store.bin reach a running instance (its
 *     worker reads them, the app takes them when quiet), and a settings save of that instance keeps them;
 *   - a re-pick (create before destroy) takes a third copy; the destroyed copy is unloaded (dlclose: no longer in
 *     /proc/self/maps) and its index taken again by the next create; a new instance boots with the shared presets;
 *   - an update: engine.so changes (size and CRC), and the next create copies it again;
 *   - a copy that cannot load (engine.so missing) still gives an instance: silent, named NO ENGINE. */
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"
#include "../move_store.h"

static void lg(const char *m) { (void)m; }
static plugin_api_v2_t *api;
static move_plugin_init_v2_fn init;
static host_api_v1_t hosts[8];
static int nh;
static void *live[8];
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL " __VA_ARGS__); printf("\n"); } } while (0)

static void *create(const char *dir)          /* a slot picks Frodhes: its init, its create, the boot */
{
    host_api_v1_t *h = &hosts[nh++ & 7];
    *h = (host_api_v1_t){ .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = lg };
    if (init(h) != api) { printf("FAIL init\n"); exit(1); }
    void *in = api->create_instance(dir, NULL);
    for (int i = 0; in && i < 5000; i++) {
        char r[8] = "";
        api->get_param(in, "dev_ready", r, sizeof r);
        if (r[0] == '1') break;
        int16_t z[256];
        api->render_block(in, z, 128);
        usleep(1000);
    }
    for (int i = 0; i < 8; i++) if (!live[i]) { live[i] = in; break; }
    return in;
}
static void destroy(void *in)
{
    for (int i = 0; i < 8; i++) if (live[i] == in) live[i] = 0;
    api->destroy_instance(in);
}
static double energy_all(void *of, int blocks)   /* the host renders every slot every block: of's energy */
{
    int16_t b[256];
    double e = 0;
    for (int i = 0; i < blocks; i++)
        for (int k = 0; k < 8; k++) {
            if (!live[k]) continue;
            api->render_block(live[k], b, 128);
            if (live[k] == of) for (int j = 0; j < 256; j++) e += (double)b[j] * b[j];
        }
    return e;
}
static void run_ms(int ms) { for (int i = 0; i < ms / 3; i++) { (void)energy_all(0, 1); usleep(2900); } }
static char gb[512];
static const char *get(void *in, const char *k) { if (api->get_param(in, k, gb, sizeof gb) < 0) strcpy(gb, "(none)"); return gb; }
static int inst_k(void *in) { return atoi(get(in, "dev_inst")); }
static int mapped(const char *what)             /* a path fragment in /proc/self/maps */
{
    char l[1024];
    int n = 0;
    FILE *f = fopen("/proc/self/maps", "r");
    while (f && fgets(l, sizeof l, f)) n += strstr(l, what) != 0;
    if (f) fclose(f);
    return n;
}
static int copy_file(const char *a, const char *b)
{
    char buf[65536];
    size_t n;
    FILE *i = fopen(a, "rb"), *o = fopen(b, "wb");
    if (!i || !o) { if (i) fclose(i); if (o) fclose(o); return -1; }
    while ((n = fread(buf, 1, sizeof buf, i)) > 0) fwrite(buf, 1, n, o);
    fclose(i);
    return fclose(o);
}
static long fsize(const char *p) { struct stat s; return stat(p, &s) ? -1 : (long)s.st_size; }
static int imports(void *in) { const char *s = strstr(get(in, "dev_store"), "imports="); return s ? atoi(s + 8) : -1; }
static int wait_import(void *in, int was, int ms)   /* the instance took another import */
{
    for (int t = 0; t < ms; t += 50) { if (imports(in) > was) return 1; run_ms(50); }
    return 0;
}

#define REC_N (4u + 256u + 16u * 96u)            /* store.bin's payload: [1 2 . .], the settings, 16 user presets */
#define TAIL (4u + 256u)

int main(int argc, char **argv)
{
    char mod[400], so[700], eng[700], p[700], src[600];
    static uint8_t rec[REC_N];
    int ia, ib;
    const uint8_t on[3] = { 0x90, 60, 110 }, on2[3] = { 0x90, 64, 110 };
    if (argc < 3) { printf("usage: test_multi DSP DIR\n"); return 2; }
    snprintf(src, sizeof src, "%s", argv[1]);
    *strrchr(src, '/') = 0;
    snprintf(mod, sizeof mod, "%s/mod", argv[2]);
    if (ms_mkdirs(mod)) { printf("FAIL mkdir %s\n", mod); return 1; }
    snprintf(so, sizeof so, "%s/dsp.so", mod);
    snprintf(eng, sizeof eng, "%s/engine.so", mod);
    snprintf(p, sizeof p, "%s/engine.so", src);
    if (copy_file(argv[1], so) || copy_file(p, eng)) { printf("FAIL copying the module into %s\n", mod); return 1; }
    void *h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("FAIL dlopen %s\n", dlerror()); return 1; }
    init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!init) { printf("FAIL no entry\n"); return 1; }
    api = init(&hosts[0]);

    /* two instances */
    void *a = create(mod), *b = create(mod);
    if (!a || !b) { printf("FAIL create\n"); return 1; }
    CHECK(inst_k(a) == 0 && inst_k(b) == 1, "copies %d %d", inst_k(a), inst_k(b));
    run_ms(600);
    double ea = energy_all(a, 50), eb = energy_all(b, 50);
    CHECK(ea == 0 && eb == 0, "silent at the start %g %g", ea, eb);
    api->on_midi(a, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    ea = energy_all(a, 100); eb = energy_all(b, 100);
    CHECK(ea > 1e6 && eb == 0, "note into A: A %g B %g", ea, eb);
    api->on_midi(b, on2, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    eb = energy_all(b, 100);
    CHECK(eb > 1e6, "note into B: B plays %g", eb);
    api->set_param(a, "p_level", "0");
    run_ms(100);
    CHECK(!strcmp(get(a, "p_level"), "0"), "A's LEVEL 0: %s", gb);
    CHECK(strcmp(get(b, "p_level"), "0") != 0, "B's LEVEL is its own: %s", gb);
    if (!fails) printf("ok    two instances, two copies (0, 1), each plays and takes its knobs on its own\n");
    api->set_param(a, "p_level", "64");
    { const uint8_t off[3] = { 0x80, 60, 0 }, off2[3] = { 0x80, 64, 0 };
      api->on_midi(a, off, 3, MOVE_MIDI_SOURCE_EXTERNAL); api->on_midi(b, off2, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
    run_ms(4000);                                /* (the tails die out: the store is written when quiet) */

    /* the shared store: A saves its settings; another instance's presets appear in store.bin; B takes them */
    snprintf(p, sizeof p, "%s/store.bin", mod);
    api->set_param(a, "s_tune", "-3");
    for (int t = 0; t < 8000 && fsize(p) < 0; t += 50) run_ms(50);
    CHECK(fsize(p) > 0, "store.bin in the data folder (%s)", p);
    snprintf(p, sizeof p, "%s/inst/0/store.bin", mod);
    CHECK(fsize(p) < 0, "no store.bin in a copy's own folder");
    CHECK(ms_load(mod, "store.bin", rec, REC_N) == (int)REC_N && rec[0] == 1 && rec[1] == 2, "store.bin's record");
    {
        uint8_t *u = rec + TAIL + 15u * 96u;
        ia = imports(a); ib = imports(b);    /* U16: used, no parameters (the defaults), named SHARED */
        memset(u, 0, 96);
        u[0] = 3; u[1] = 0; u[2] = 1;
        memcpy(u + 4, "SHARED      ", 12);
        struct timespec ts = { 0, 20000000 };
        nanosleep(&ts, 0);                       /* (a later mtime than A's write) */
        CHECK(ms_save(mod, "store.bin", rec, REC_N) == 0, "writing the other instance's store.bin");
    }
    CHECK(wait_import(b, ib, 5000), "B read the shared presets: %s", gb);
    { char u16[16]; snprintf(u16, sizeof u16, "%d", atoi(get(b, "preset_count")) - 1); api->set_param(b, "preset", u16); }
    run_ms(200);
    CHECK(!strcmp(get(b, "preset_name"), "U16 SHARED"), "B loads U16 from the shared presets: %s", gb);
    CHECK(wait_import(a, ia, 5000), "A read them too: %s", gb);
    api->set_param(b, "s_tune", "5");            /* B's settings save keeps the shared presets */
    run_ms(4000);
    memset(rec, 0, sizeof rec);
    CHECK(ms_load(mod, "store.bin", rec, REC_N) == (int)REC_N && !memcmp(rec + TAIL + 15u * 96u + 4u, "SHARED", 6),
          "B's save kept U16 in store.bin");
    CHECK(strstr(get(b, "dev_store"), "saves=0") == 0, "B wrote store.bin: %s", gb);
    if (!fails) printf("ok    one store.bin for every instance: the shared presets reach the others and survive their saves\n");

    /* the re-pick: create before destroy; dlclose; the index taken again; the new instance boots with the presets */
    void *c = create(mod);
    CHECK(c && inst_k(c) == 2, "re-pick copy %d", c ? inst_k(c) : -1);
    CHECK(mapped("/inst/0/dsp.so") > 0, "copy 0 loaded");
    destroy(a);
    CHECK(mapped("/inst/0/dsp.so") == 0, "copy 0 unloaded after its destroy (dlclose)");
    eb = energy_all(b, 10);
    api->on_midi(c, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    CHECK(energy_all(c, 100) > 1e6, "the re-picked slot plays");
    { char u16[16]; snprintf(u16, sizeof u16, "%d", atoi(get(c, "preset_count")) - 1); api->set_param(c, "preset", u16); }
    run_ms(200);
    CHECK(!strcmp(get(c, "preset_name"), "U16 SHARED"), "a new instance boots with the shared presets: %s", gb);
    void *d = create(mod);
    CHECK(d && inst_k(d) == 0, "the freed index 0 taken again: %d", d ? inst_k(d) : -1);
    if (!fails) printf("ok    re-pick: copy 2, copy 0 unloaded, then index 0 loaded again\n");
    destroy(b); destroy(c); destroy(d);
    CHECK(mapped("/inst/") == 0, "every copy unloaded (%d mapped)", mapped("/inst/"));

    /* an update: engine.so changes, the next create copies it again */
    {
        FILE *f = fopen(eng, "ab");
        static const uint8_t pad[4096];
        if (f) { fwrite(pad, 1, sizeof pad, f); fclose(f); }
        snprintf(p, sizeof p, "%s/inst/0/dsp.so", mod);
        long was = fsize(p);
        void *e = create(mod);
        CHECK(e && inst_k(e) == 0 && fsize(p) == fsize(eng) && fsize(p) == was + 4096, "the changed engine.so copied again: %ld, engine %ld (was %ld)", fsize(p), fsize(eng), was);
        api->on_midi(e, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
        CHECK(energy_all(e, 100) > 1e6, "the updated copy plays");
        destroy(e);
        if (!fails) printf("ok    update: a changed engine.so (size, CRC) is copied again at the next create\n");
    }

    /* no engine: a silent instance, NO ENGINE */
    {
        char gone[720];
        snprintf(gone, sizeof gone, "%s.gone", eng);
        rename(eng, gone);
        void *x = create(mod);
        CHECK(x != 0, "an instance without an engine");
        if (x) {
            api->on_midi(x, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
            CHECK(energy_all(x, 20) == 0, "it is silent");
            CHECK(!strcmp(get(x, "preset_name"), "NO ENGINE"), "it says NO ENGINE: %s", gb);
            destroy(x);
        }
        rename(gone, eng);
        if (!fails) printf("ok    no engine: an instance that renders silence and says NO ENGINE\n");
    }
    if (fails) { printf("FAIL  move loader (%d)\n", fails); return 1; }
    printf("ok    move loader\n");
    return 0;
}
