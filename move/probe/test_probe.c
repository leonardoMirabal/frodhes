// SPDX-License-Identifier: GPL-3.0-only
// Host test for the probe dsp.so: dlopen it (two module ids), drive it through a fake host without the Move, and
// check the sound, the MIDI log, the state sizes, the data-dir markers, the timing summary and that no callback
// allocates. Usage: test_probe <frodhes-probe/dsp.so> <frodhes-probe-raw/dsp.so>
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "../vendor/plugin_api_v1.h"

// ---- allocation guard: any malloc family call while in_cb is set is a failure ----
extern void *__libc_malloc(size_t), *__libc_calloc(size_t, size_t), *__libc_realloc(void *, size_t);
extern void __libc_free(void *);
static __thread int in_cb;
static int bad_alloc;
void *malloc(size_t n) { if (in_cb) bad_alloc++; return __libc_malloc(n); }
void *calloc(size_t a, size_t b) { if (in_cb) bad_alloc++; return __libc_calloc(a, b); }
void *realloc(void *p, size_t n) { if (in_cb) bad_alloc++; return __libc_realloc(p, n); }
void free(void *p) { if (in_cb && p) bad_alloc++; __libc_free(p); }

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static int clock_st = MOVE_CLOCK_STATUS_STOPPED;
static int f_clock(void) { return clock_st; }
static float f_bpm(void) { return 123.5f; }
static double f_beat(void) { return 4.25; }
static int f_chan(void *i) { (void)i; return 3; }
static void f_log(const char *m) { (void)m; }

static host_api_v1_t host = {
    .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = f_log,
    .get_clock_status = f_clock, .get_bpm = f_bpm, .slot_recv_channel = f_chan, .get_beat_position = f_beat,
};

static plugin_api_v2_t *load(const char *path) {
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("FAIL dlopen %s: %s\n", path, dlerror()); exit(1); }
    move_plugin_init_v2_fn init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    CHECK(init != NULL, "no %s in %s", MOVE_PLUGIN_INIT_V2_SYMBOL, path);
    CHECK(dlsym(h, "move_plugin_init_v1") == NULL, "v1 entry exported");
    CHECK(dlsym(h, "p_render") == NULL, "internal symbol exported (visibility)");
    plugin_api_v2_t *api = init(&host);
    CHECK(api && api->api_version == MOVE_PLUGIN_API_VERSION_2, "api version");
    return api;
}

static int16_t blk[256];
static int peak;
static void render(plugin_api_v2_t *a, void *in, int n) {
    peak = 0;
    for (int b = 0; b < n; b++) {
        in_cb = 1; a->render_block(in, blk, 128); in_cb = 0;
        for (int i = 0; i < 256; i++) { int v = abs(blk[i]); if (v > peak) peak = v; }
    }
}
static void midi(plugin_api_v2_t *a, void *in, int src, int len, int b0, int b1, int b2) {
    uint8_t m[3] = { (uint8_t)b0, (uint8_t)b1, (uint8_t)b2 };
    in_cb = 1; a->on_midi(in, m, len, src); in_cb = 0;
}

static char *slurp(const char *p) {
    FILE *f = fopen(p, "r"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc((size_t)n + 1); size_t r = fread(b, 1, (size_t)n, f); b[r] = 0; fclose(f); return b;
}
static int count(const char *hay, const char *needle) {
    int n = 0; for (const char *p = hay; (p = strstr(p, needle)); p++) n++; return n;
}

int main(int argc, char **argv) {
    if (argc < 3) { printf("usage: %s probe.so probe-raw.so\n", argv[0]); return 2; }
    char dir[] = "/tmp/frodhes-probe-test-XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); return 1; }
    char d1[300], d2[300];
    snprintf(d1, sizeof d1, "%s/frodhes-probe", dir); snprintf(d2, sizeof d2, "%s/frodhes-probe-raw", dir);
    mkdir(d1, 0755); mkdir(d2, 0755);

    plugin_api_v2_t *A = load(argv[1]), *B = load(argv[2]);
    void *a = A->create_instance(d1, "{\"x\":1}");
    void *b = B->create_instance(d2, NULL);
    CHECK(a && b, "create_instance");

    // sound: a note sounds, and is silent after its release
    render(A, a, 10); CHECK(peak == 0, "silence before a note (peak %d)", peak);
    midi(A, a, 0, 3, 0x90, 60, 100);
    render(A, a, 50); CHECK(peak > 3000, "a note sounds (peak %d)", peak);
    midi(A, a, 0, 3, 0x80, 60, 0);
    render(A, a, 400); render(A, a, 10); CHECK(peak < 50, "silent after release (peak %d)", peak);

    // every MIDI kind lands in the log
    for (int i = 0; i < 96; i++) midi(A, a, MOVE_MIDI_SOURCE_HOST, 1, 0xF8, 0, 0);
    midi(A, a, MOVE_MIDI_SOURCE_HOST, 1, 0xFA, 0, 0);
    midi(A, a, MOVE_MIDI_SOURCE_HOST, 1, 0xFB, 0, 0);
    midi(A, a, MOVE_MIDI_SOURCE_HOST, 1, 0xFC, 0, 0);
    midi(A, a, MOVE_MIDI_SOURCE_EXTERNAL, 2, 0xD0, 64, 0);
    midi(A, a, MOVE_MIDI_SOURCE_INTERNAL, 3, 0xA0, 70, 33);
    midi(A, a, MOVE_MIDI_SOURCE_EXTERNAL, 3, 0xE1, 0, 96);
    midi(A, a, MOVE_MIDI_SOURCE_EXTERNAL, 3, 0xB0, 64, 127);
    midi(A, a, MOVE_MIDI_SOURCE_TOUCH, 3, 0x90, 3, 127);    // knob 4 touch: logged, not played
    render(A, a, 20); CHECK(peak == 0, "a touch edge plays nothing (peak %d)", peak);
    uint8_t sx[6] = { 0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7 };
    in_cb = 1; A->on_midi(a, sx, 6, MOVE_MIDI_SOURCE_EXTERNAL); in_cb = 0;
    clock_st = MOVE_CLOCK_STATUS_RUNNING;
    render(A, a, 400);

    // state: Auto mode grows 1K .. 64K per get; a small buffer is refused; set_param validates the blob
    static char buf[70000];
    const int steps[7] = { 1024, 2048, 4096, 8192, 16384, 32768, 65536 };
    for (int i = 0; i < 7; i++) {
        in_cb = 1; int n = A->get_param(a, "state", buf, sizeof buf); in_cb = 0;
        CHECK(n == steps[i], "state get %d returned %d", steps[i], n);
        CHECK(n > 0 && (int)strlen(buf) == n, "state length");
        in_cb = 1; A->set_param(a, "state", buf); in_cb = 0;
    }
    buf[100] = 'Z'; buf[101] = 0; A->set_param(a, "synth:state", buf);       // a torn blob is reported invalid
    A->set_param(a, "state_kb", "4K");
    in_cb = 1; int n = A->get_param(a, "state", buf, 2048); in_cb = 0;
    CHECK(n == -1, "a 4 KB state into a 2 KB buffer is refused (%d)", n);
    in_cb = 1; n = A->get_param(a, "state", buf, sizeof buf); in_cb = 0;
    CHECK(n == 4096, "state_kb 4K gives 4096 (%d)", n);
    A->set_param(a, "state_kb", "3"); n = A->get_param(a, "state", buf, sizeof buf);
    CHECK(n == 4096, "state_kb index 3 gives 4096 (%d)", n);
    for (int i = 0; i < 10; i++) { in_cb = 1; A->get_param(a, "ui_hierarchy", buf, 512); in_cb = 0; }
    in_cb = 1; n = A->get_param(a, "level", buf, 64); in_cb = 0;
    CHECK(n > 0 && !strcmp(buf, "0.25"), "level get (%s)", buf);

    // burn: the timing histogram sees it
    A->set_param(a, "burn_us", "300");
    render(A, a, 20);
    A->set_param(a, "burn_us", "0");
    A->set_param(a, "mark", "end-of-test");
    render(A, a, 5);
    CHECK(bad_alloc == 0, "%d allocations inside callbacks", bad_alloc);

    usleep(2300 * 1000);                                   // the worker writes the summary every ~2 s
    A->destroy_instance(a);
    B->destroy_instance(b);

    char p[400];
    snprintf(p, sizeof p, "%s/probe-0.log", d1); char *log = slurp(p);
    snprintf(p, sizeof p, "%s/probe-0-summary.txt", d1); char *sum = slurp(p);
    snprintf(p, sizeof p, "%s/probe-0-summary.txt", d2); char *sum2 = slurp(p);
    CHECK(log && sum && sum2, "log / summaries written");
    if (log && sum && sum2) {
        CHECK(count(log, "f8 Clock") == 96, "96 clock lines (%d)", count(log, "f8 Clock"));
        CHECK(count(log, "fa Start") == 1 && count(log, "fb Continue") == 1 && count(log, "fc Stop") == 1, "FA FB FC");
        CHECK(strstr(log, "d0 40 ChanPressure ch1") != NULL, "channel pressure");
        CHECK(strstr(log, "a0 46 21 PolyPressure ch1") != NULL, "poly pressure");
        CHECK(strstr(log, "e1 00 60 PitchBend ch2") != NULL, "pitch bend");
        CHECK(strstr(log, "src=5 len=3 90 03 7f") != NULL, "touch source");
        CHECK(strstr(log, "len=6 f0 7e 7f 06 01 f7 SysEx") != NULL, "sysex bytes");
        CHECK(count(log, "state=VALID") == 7, "7 valid state loads (%d)", count(log, "state=VALID"));
        CHECK(strstr(log, "state=INVALID") != NULL, "invalid state reported");
        CHECK(strstr(log, "state_want=4096 DOES-NOT-FIT") != NULL, "does-not-fit logged");
        CHECK(count(log, "get key=ui_hierarchy") == 3, "a polled key is logged 3 times");
        CHECK(strstr(log, "set key+val=state_kb 4K") != NULL, "set value logged");
        CHECK(strstr(log, "host get_clock_status=2") && strstr(log, "get_bpm=123.500") &&
              strstr(log, "slot_recv_channel=3") && strstr(log, "get_beat_position=4.2500"), "host queries");
        CHECK(strstr(log, "audio thread: policy=") != NULL, "thread info");
        CHECK(strstr(log, "note end-of-test") != NULL, "mark");
        char want[400]; snprintf(want, sizeof want, "datadir %s", d1);
        CHECK(strstr(log, want) && strstr(log, "writable=1 readback=1"), "module dir marker");
        CHECK(strstr(sum, "ui_hierarchy=10") != NULL, "key counts in the summary");
        unsigned mx = 0; const char *r = strstr(sum, "render_us  max ");
        if (r) sscanf(r, "render_us  max %u", &mx);
        CHECK(mx >= 300 && mx < 2000, "render max sees burn_us 300 (%u)", mx);
        CHECK(strstr(sum, "src3=") && strstr(sum, "src5=1") && strstr(sum, "Clock=96"), "summary counts");
        const char *m1 = strstr(sum, "&g_marker="), *m2 = strstr(sum2, "&g_marker=");
        CHECK(m1 && m2 && strncmp(m1, m2, 26) != 0, "two module ids have separate globals");
    }
    // a second run finds the first run's marker (persistence check)
    void *a2 = A->create_instance(d1, NULL);
    usleep(200 * 1000);
    A->destroy_instance(a2);
    snprintf(p, sizeof p, "%s/probe-0.log", d1); free(log); log = slurp(p);
    CHECK(log && strstr(log, "previous_marker=[pid="), "the earlier marker is found");
    CHECK(log && strstr(log, "final summary:") && strstr(log, "Clock=96"), "the final summary is in the log");
    free(log); free(sum); free(sum2);

    if (!fails && !getenv("PROBE_KEEP")) {               // clean the temp dir only on success
        char cmd[400]; snprintf(cmd, sizeof cmd, "rm -rf %s", dir); if (system(cmd)) {}
    } else printf("logs kept in %s\n", dir);
    printf("%s (%d failures)\n", fails ? "FAIL" : "ok    probe", fails);
    return fails != 0;
}
