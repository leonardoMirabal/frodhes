/* SPDX-License-Identifier: GPL-3.0-only */
/* The bridge's host tests (M1-03 .. M1-07), on build/move/frodhes/dsp.so through a fake host:
 *   test_bridge eq DSP SCRIPT HOST.wav   M1-03/04: the script's MIDI lines replayed through the plugin (set_param
 *                                        dev_midi, due at their ms) give the host build's samples exactly
 *   test_bridge api DSP                  M1-04..07: the singleton, a 200-message burst in one block, touch edges
 *                                        not played, the parameter keys, presets, the state round trip (1000
 *                                        random records), the clock fallback at 60..200 BPM
 *   test_bridge keys DSP < LINES         "key value" lines: set, render, print "key<TAB>value<TAB>read back"
 *   test_bridge script DSP < LINES       a timed session: "render N" (N blocks of 128), "set KEY VALUE", "midi SRC ST D1
 *                                        D2" (on_midi from source SRC), "get KEY" -> "KEY<TAB>value", "rms N" (N blocks
 *                                        rendered) -> "rms<TAB>mean square (LSB^2)" (M1-13b's sustain tests)
 *   test_bridge presets DSP              every preset: "preset <i> <preset> <name>"; every TYPE: "type <t> <ui_engine>"
 *   test_bridge gatep DSP [N]            Gate P (M1-11): N (10^4) random blocks of MIDI, key writes, state reads and
 *                                        loads; no syscall (seccomp) and no allocation inside any callback (unless
 *                                        built -DNO_GUARDS, for ASan / UBSan), no sample stuck at full scale
 *   test_bridge run DSP SCRIPT OUT.wav   M1-12 (the plugin monkey, tests/py/test_move_monkey.py): a timed script
 *                                        through the plugin's own entry points, under the Gate P guards: "T midi
 *                                        ST D1 D2" (on_midi, source EXTERNAL), "T key NAME VALUE" (set_param),
 *                                        "T preset N", "T getstate" / "T setstate" (the state read back in later),
 *                                        "T end"; each line when the rendered time reaches T; OUT.wav as host.c's
 *   test_bridge persist DSP DIR save|check   M1-08: "save" sets keys, records a pattern and saves a user preset, waits
 *                                        for the worker's files; "check" (a new process) finds them all back
 *   test_bridge play DSP SCRIPT HOST.wav the dev player and tap: the script's MIDI as a dev_play schedule (pieces
 *                                        under 256 B, as Schwung's WebSocket set ring takes them), started on the
 *                                        2560 ms grid; the tap file (<dir>/tap/<id>.wav) equals HOST.wav (the host
 *                                        build on the same script shifted by 2560 ms) from 2560 ms on, sample for
 *                                        sample; <id>.json, stop, the refusals
 * Every mode but persist runs in a fresh empty data folder (build/move/t/run.XXXXXX), so no file of an earlier run
 * changes the sound; create waits for the worker's boot (dev_ready) as the host's audio would play silence. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"
#ifndef NO_GUARDS
#include "seccomp_guard.h"
/* the allocation guard: any malloc family call while in_cb is set is counted */
extern void *__libc_malloc(size_t), *__libc_calloc(size_t, size_t), *__libc_realloc(void *, size_t);
extern void __libc_free(void *);
static _Thread_local int in_cb;   /* (per thread: the persistence worker's own calls are not callbacks) */
static int bad_alloc;
void *malloc(size_t n) { if (in_cb) bad_alloc++; return __libc_malloc(n); }
void *calloc(size_t a, size_t b) { if (in_cb) bad_alloc++; return __libc_calloc(a, b); }
void *realloc(void *p, size_t n) { if (in_cb) bad_alloc++; return __libc_realloc(p, n); }
void free(void *p) { if (in_cb && p) bad_alloc++; __libc_free(p); }
#else
static int in_cb, bad_alloc;
#endif

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* the fake host's transport */
static int clk_status = MOVE_CLOCK_STATUS_STOPPED;
static double beat = -1.0, bpm_v = 120.0;
static int beat_null;
static int h_clock(void) { return clk_status; }
static float h_bpm(void) { return (float)bpm_v; }
static double h_beat(void) { return beat; }
static void h_log(const char *m) { (void)m; }
static host_api_v1_t host = { .api_version = 1, .sample_rate = 44100, .frames_per_block = 128, .log = h_log,
                              .get_clock_status = h_clock, .get_bpm = h_bpm, .get_beat_position = h_beat };

static plugin_api_v2_t *api;
static move_plugin_init_v2_fn init_fn;
static plugin_api_v2_t *move_plugin_init_v2_again(const host_api_v1_t *h) { return init_fn(h); }
static void *load(const char *so)
{
    void *h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("FAIL dlopen %s: %s\n", so, dlerror()); exit(1); }
    move_plugin_init_v2_fn init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!init) { printf("FAIL no entry\n"); exit(1); }
    init_fn = init;
    api = init(&host);
    return h;
}
static char gbuf[70000];
static const char *get(void *in, const char *k) { int n = api->get_param(in, k, gbuf, sizeof gbuf); if (n < 0) strcpy(gbuf, "(none)"); return gbuf; }
static unsigned stat_of(void *in, const char *name)
{
    const char *s = get(in, "dev_stats"), *p; char pat[32]; unsigned v = 0;
    snprintf(pat, sizeof pat, "%s=", name);
    p = strstr(s, pat);
    if (p) sscanf(p + strlen(pat), "%u", &v);
    return v;
}
static int16_t blk[256];
/* create in DIR (NULL: a fresh empty folder), then wait for the worker's boot (silence until then) */
static void *create(const char *dir)
{
    static char tmpl[64];
    void *in;
    if (!dir) {
        mkdir("build", 0755); mkdir("build/move", 0755); mkdir("build/move/t", 0755);
        snprintf(tmpl, sizeof tmpl, "build/move/t/run.XXXXXX");
        dir = mkdtemp(tmpl);
        if (!dir) { printf("FAIL mkdtemp\n"); exit(1); }
    }
    in = api->create_instance(dir, NULL);
    for (int i = 0; in && i < 5000; i++) {
        char r[8] = "";
        api->get_param(in, "dev_ready", r, sizeof r);
        if (r[0] == '1') return in;
        { int16_t z[256]; api->render_block(in, z, 128); for (int k = 0; k < 256; k++) if (z[k]) { printf("FAIL sound before the boot\n"); exit(1); } }
        usleep(1000);
    }
    if (in) { printf("FAIL the worker did not boot\n"); exit(1); }
    return in;
}
static void render(void *in, int n) { while (n-- > 0) api->render_block(in, blk, 128); }

/* ---- eq */
static int eq(const char *so, const char *script, const char *wav)
{
    FILE *f = fopen(script, "r"), *w;
    char line[256], ev[96];
    unsigned end = 0, nev = 0;
    long hn, pn = 0, i, first_diff = -1, ndiff = 0;
    int16_t *hs, *ps;
    void *in;
    if (!f) { printf("FAIL no script %s\n", script); return 1; }
    load(so);
    in = create(NULL);
    CHECK(in != NULL, "create");
    while (fgets(line, sizeof line, f)) {
        unsigned t, st, d1, d2; char what[16];
        if (line[0] == '#' || sscanf(line, "%u %15s", &t, what) != 2) continue;
        if (!strcmp(what, "end")) { end = t; continue; }
        if (strcmp(what, "midi") || sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) { printf("FAIL eq: line not MIDI: %s", line); fails++; continue; }
        snprintf(ev, sizeof ev, "%u %u %u %u", t, st, d1, d2);
        api->set_param(in, "dev_midi", ev);
        nev++;
    }
    fclose(f);
    w = fopen(wav, "rb");
    if (!w) { printf("FAIL no host wav %s\n", wav); return 1; }
    fseek(w, 0, SEEK_END); hn = (ftell(w) - 44) / 4; fseek(w, 44, SEEK_SET);
    hs = malloc((size_t)hn * 4); ps = malloc((size_t)(hn + 512) * 4);
    if (fread(hs, 4, (size_t)hn, w) != (size_t)hn) { printf("FAIL short wav\n"); return 1; }
    fclose(w);
    while (pn < hn) { api->render_block(in, ps + 2 * pn, 128); pn += 128; }
    for (i = 0; i < 2 * hn; i++) if (hs[i] != ps[i]) { if (first_diff < 0) first_diff = i / 2; ndiff++; }
    {
        long peak = 0; for (i = 0; i < 2 * hn; i++) { long a = labs((long)hs[i]); if (a > peak) peak = a; }
        CHECK(peak > 1000, "the host render is not silent (peak %ld)", peak);
        CHECK(ndiff == 0, "%ld of %ld samples differ, the first at frame %ld (%.1f ms)", ndiff, 2 * hn, first_diff, first_diff / 44.1);
        CHECK(stat_of(in, "midi_out") == nev, "all %u messages delivered (%u)", nev, stat_of(in, "midi_out"));
        printf("%s    eq: %ld frames (%u ms script, %u messages, peak %ld) %s the host build\n", fails ? "FAIL" : "ok", hn, end,
               nev, peak, ndiff ? "differ from" : "equal to");
    }
    api->destroy_instance(in);
    return fails != 0;
}

/* ---- play: the dev player and tap */
static int16_t *read_wav(const char *path, long *frames)
{
    FILE *w = fopen(path, "rb");
    int16_t *x;
    long n;
    if (!w) return 0;
    fseek(w, 0, SEEK_END); n = (ftell(w) - 44) / 4; fseek(w, 44, SEEK_SET);
    x = malloc((size_t)(n > 0 ? n : 1) * 4);
    if (n < 0 || fread(x, 4, (size_t)n, w) != (size_t)n) { fclose(w); free(x); return 0; }
    fclose(w);
    *frames = n;
    return x;
}
static int wait_state(void *in, const char *want, int blocks)
{
    while (blocks-- > 0) {
        in_cb = 1; api->render_block(in, blk, 128); in_cb = 0;
        if (strstr(get(in, "dev_play"), want)) return 1;
        if (blocks % 8 == 0) usleep(2000);   /* (a pace the worker keeps up with: ~10x real time) */
    }
    return 0;
}
static void cb_set(void *in, const char *k, const char *v) { in_cb = 1; api->set_param(in, k, v); in_cb = 0; }
static unsigned load_take(void *in, const char *script, const char *begin, unsigned *pieces)   /* the messages */
{
    FILE *f = fopen(script, "r");
    char line[256], chunk[256] = "", ev[48];
    unsigned nev = 0;
    *pieces = 0;
    cb_set(in, "dev_play", begin);
    while (f && fgets(line, sizeof line, f)) {
        unsigned t, st, d1, d2; char what[16];
        if (line[0] == '#' || sscanf(line, "%u %15s", &t, what) != 2 || strcmp(what, "midi")) continue;
        if (sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) continue;
        snprintf(ev, sizeof ev, nev & 1 ? "%u 0x%02x %u %u;" : "%u %u %u %u;", t, st, d1, d2);   /* (both notations) */
        if (strlen(chunk) + strlen(ev) >= 255) {        /* (every other piece numbered: both forms) */
            cb_set(in, "dev_play_add", chunk); (*pieces)++;
            if (*pieces & 1u) snprintf(chunk, sizeof chunk, "#%u ", *pieces); else chunk[0] = 0;
        }
        strcat(chunk, ev);
        nev++;
    }
    if (f) fclose(f);
    if (chunk[0]) { cb_set(in, "dev_play_add", chunk); (*pieces)++; }
    return nev;
}
static int play(const char *so, const char *script, const char *wav)
{
    char path[700], begin[64], *dir;
    unsigned nev, pieces;
    long hn = 0, tn = 0, i, n, ndiff = 0, first = -1, f0 = 2560L * 441L / 10L;
    int16_t *hs, *ts;
    void *in;
    load(so);
    in = create(NULL);
    dir = strdup(get(in, "dev_store") + 4);
    if (strchr(dir, ' ')) *strchr(dir, ' ') = 0;
    CHECK(strstr(get(in, "dev_build"), "dev_play=1") && strstr(gbuf, "dsp_crc32="), "dev_build: %s", gbuf);
    snprintf(path, sizeof path, "%s/boot.txt", dir);
    { FILE *j = fopen(path, "r"); char bt[200] = ""; if (j) { if (!fgets(bt, sizeof bt, j)) bt[0] = 0; fclose(j); }
      CHECK(strstr(bt, "dev_play=1"), "boot.txt: %s", bt); }
    hs = read_wav(wav, &hn);
    if (!hs || hn <= f0) { printf("FAIL no host wav %s (or shorter than 2560 ms)\n", wav); return 1; }
    cb_set(in, "dev_play_add", "0 0x90 60 100");                       /* refused: no take begun */
    cb_set(in, "dev_play", "begin bad! 100");                          /* refused: the id */
    CHECK(strstr(get(in, "dev_play"), "state=idle") && strstr(gbuf, "bad=2"), "the refusals: %s", gbuf);
    snprintf(begin, sizeof begin, "begin bridge-eq %ld", (hn - f0) * 10 / 441);
    load_take(in, script, begin, &pieces);
    cb_set(in, "dev_play", "stop");                                    /* a loaded take dropped */
    CHECK(strstr(get(in, "dev_play"), "state=idle"), "stop while loading: %s", gbuf);
    nev = load_take(in, script, begin, &pieces);
    CHECK(strstr(get(in, "dev_play"), "state=load") && strstr(gbuf, "bad=0"), "loaded: %s", gbuf);
    cb_set(in, "dev_play", "go");
    CHECK(strstr(get(in, "dev_play"), "state=armed") && strstr(gbuf, "base=2560"), "go: %s", gbuf);
    CHECK(wait_state(in, "state=done", 40000), "the take did not finish: %s", get(in, "dev_play"));
    CHECK(strstr(get(in, "dev_play"), "late=0 bad=0 dropped=0"), "late / bad / dropped: %s", gbuf);
    snprintf(path, sizeof path, "%s/tap/bridge-eq.wav", dir);
    ts = read_wav(path, &tn);
    n = (hn - f0) * 10 / 441 * 441 / 10;
    CHECK(ts != 0 && tn == n, "the tap file %s: %ld frames, want %ld", path, tn, n);
    if (ts) for (i = 0; i < 2 * (tn < n ? tn : n); i++) if (ts[i] != hs[2 * f0 + i]) { if (first < 0) first = i / 2; ndiff++; }
    CHECK(ndiff == 0, "%ld samples differ from the host render shifted by 2560 ms, the first at frame %ld", ndiff, first);
    snprintf(path, sizeof path, "%s/tap/bridge-eq.json", dir);
    { FILE *j = fopen(path, "r"); char js[512] = ""; if (j) { if (!fgets(js, sizeof js, j)) js[0] = 0; fclose(j); }
      CHECK(strstr(js, "\"base_ms\":2560") && strstr(js, "\"dropped\":0") && strstr(js, "\"aborted\":0"), "json: %s", js); }
    /* stop in the middle: the file keeps its length (zeros after the stop), aborted */
    cb_set(in, "dev_play", "begin cut 3000");
    cb_set(in, "dev_play_add", "0 0x90 48 110;2000 0x80 48 0");
    cb_set(in, "dev_play", "go 0");
    CHECK(wait_state(in, "frames=4", 5000), "the second take does not capture: %s", get(in, "dev_play"));
    cb_set(in, "dev_play", "stop");
    CHECK(wait_state(in, "state=done", 20000), "stop: %s", get(in, "dev_play"));
    snprintf(path, sizeof path, "%s/tap/cut.wav", dir);
    { long cn = 0; int16_t *c = read_wav(path, &cn); CHECK(c && cn == 132300, "the stopped take: %ld frames", cn); free(c); }
    snprintf(path, sizeof path, "%s/tap/cut.json", dir);
    { FILE *j = fopen(path, "r"); char js[512] = ""; if (j) { if (!fgets(js, sizeof js, j)) js[0] = 0; fclose(j); }
      CHECK(strstr(js, "\"aborted\":1"), "json: %s", js); }
    /* a take plays once: its id again is refused; a piece again (its number out of turn) is refused whole */
    cb_set(in, "dev_play", "begin cut 3000");
    CHECK(strstr(get(in, "dev_play"), "state=done") && strstr(gbuf, "bad=1"), "the same id again: %s", gbuf);
    cb_set(in, "dev_play", "begin dup 1000");
    cb_set(in, "dev_play_add", "#0 0 0x90 50 100");
    cb_set(in, "dev_play_add", "#0 0 0x90 50 100");
    cb_set(in, "dev_play_add", "#1 10 0x80 50 0");
    CHECK(strstr(get(in, "dev_play"), "state=load") && strstr(gbuf, "events=0/2") && strstr(gbuf, "bad=1") &&
          strstr(gbuf, "pieces=2"), "a piece twice: %s", gbuf);
    cb_set(in, "dev_play", "stop");
    CHECK(bad_alloc == 0, "%d allocations inside callbacks", bad_alloc);
    printf("%s    play: %u messages in %u pieces under 256 B; the tap %s the host build shifted by 2560 ms (%ld frames); "
           "stop keeps the length\n", fails ? "FAIL" : "ok", nev, pieces, ndiff ? "DIFFERS from" : "equals", tn);
    api->destroy_instance(in);
    free(hs); free(ts); free(dir);
    return fails != 0;
}

/* ---- api */
static int api_tests(const char *so)
{
    void *in, *in2;
    unsigned n0, i;
    load(so);
    in = create(NULL);
    CHECK(in != NULL, "create");
    render(in, 400);                                          /* boot, the splash */

    /* plan 9.2 Q7 (Schwung's re-pick creates the new instance before it destroys the old; two slots share one
     * copy): the newest instance owns the app, the older renders silence and is not played; when the newest goes,
     * the older owns it again */
    {
        static host_api_v1_t host2;
        unsigned pk;
        host2 = host;
        if (api != move_plugin_init_v2_again(&host2)) { fails++; printf("FAIL init again\n"); }
        in2 = api->create_instance("build/move/t", NULL);
        CHECK(in2 != NULL && in2 != in, "a second instance");
        {   uint8_t on[3] = { 0x90, 60, 110 }; api->on_midi(in, on, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        pk = 0; for (i = 0; i < 60; i++) { api->render_block(in, blk, 128); for (unsigned k = 0; k < 256; k++) pk |= (unsigned)abs(blk[k]); }
        CHECK(pk == 0, "the older instance renders silence and is not played (%u)", pk);
        {   uint8_t on[3] = { 0x90, 64, 110 }; api->on_midi(in2, on, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        pk = 0; for (i = 0; i < 60; i++) { api->render_block(in2, blk, 128); for (unsigned k = 0; k < 256; k++) if ((unsigned)abs(blk[k]) > pk) pk = (unsigned)abs(blk[k]); }
        CHECK(pk > 1000, "the newest instance plays (peak %u)", pk);
        api->destroy_instance(in2);
        {   uint8_t off[3] = { 0x80, 64, 0 }; api->on_midi(in, off, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        pk = 0; for (i = 0; i < 60; i++) { api->render_block(in, blk, 128); for (unsigned k = 0; k < 256; k++) if ((unsigned)abs(blk[k]) > pk) pk = (unsigned)abs(blk[k]); }
        CHECK(pk > 1000, "after the newest goes, the older plays again (its release tail: peak %u)", pk);
        CHECK(stat_of(in, "midi_out") > 0, "stats from the owner");
        render(in, 400);
    }

    /* M1-04: a 200-message burst in one block plays complete; touch edges and the FX broadcast are not played */
    n0 = stat_of(in, "midi_out");
    for (i = 0; i < 100; i++) {
        uint8_t on[3] = { 0x90, (uint8_t)(30 + i % 60), 90 }, off[3] = { 0x80, (uint8_t)(30 + i % 60), 0 };
        api->on_midi(in, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
        api->on_midi(in, off, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    }
    { uint8_t t[3] = { 0x90, 3, 127 }; api->on_midi(in, t, 3, MOVE_MIDI_SOURCE_TOUCH); api->on_midi(in, t, 3, MOVE_MIDI_SOURCE_FX_BROADCAST); }
    { uint8_t sx[4] = { 0xF0, 1, 2, 0xF7 }; api->on_midi(in, sx, 4, MOVE_MIDI_SOURCE_EXTERNAL); }
    render(in, 20);
    CHECK(stat_of(in, "midi_out") - n0 == 200, "200 of a 200-message burst delivered (%u)", stat_of(in, "midi_out") - n0);
    CHECK(stat_of(in, "q_full") == 0, "no pre-queue overflow");
    {   /* pressure and bend pass untouched: they reach the app's ring (counted), the app ignores pressure */
        uint8_t a[3] = { 0xA0, 60, 50 }, d[2] = { 0xD0, 70 }, e[3] = { 0xE0, 0, 80 };
        n0 = stat_of(in, "midi_out");
        api->on_midi(in, a, 3, MOVE_MIDI_SOURCE_INTERNAL); api->on_midi(in, d, 2, MOVE_MIDI_SOURCE_INTERNAL);
        api->on_midi(in, e, 3, MOVE_MIDI_SOURCE_EXTERNAL);
        render(in, 4);
        CHECK(stat_of(in, "midi_out") - n0 == 3, "pressure and bend passed on (%u)", stat_of(in, "midi_out") - n0);
    }

    /* M1-06: keys */
    CHECK(strstr(get(in, "ui_hierarchy"), "\"levels\"") != NULL, "ui_hierarchy served");
    CHECK(strstr(get(in, "chain_params"), "p_type") != NULL, "chain_params served");
    CHECK(!strcmp(get(in, "preset_count"), "35"), "35 presets (%s)", gbuf);
    CHECK(!strncmp(get(in, "preset_name"), "F01", 3), "preset name (%s)", gbuf);
    api->set_param(in, "p_tone", "-20"); render(in, 3);
    CHECK(!strcmp(get(in, "p_tone"), "-20"), "p_tone -20 (%s)", gbuf);
    CHECK(!strcmp(get(in, "dev:pv20"), "44"), "TONE stored 44 (%s)", gbuf);
    api->set_param(in, "p_type", "4"); render(in, 3);
    CHECK(!strcmp(get(in, "p_type"), "4") && !strcmp(get(in, "ui_engine"), "1"), "TYPE VIBES, engine page 1 (%s)", gbuf);
    CHECK(!strcmp(get(in, "p_mot_on"), "1"), "VIBES turns its motor on, as the panel (%s)", gbuf);
    api->set_param(in, "p_type", "5"); render(in, 3);
    CHECK(!strcmp(get(in, "ui_engine"), "2"), "MARIMBA page (%s)", gbuf);
    api->set_param(in, "p_type", "1"); render(in, 3);
    CHECK(!strcmp(get(in, "p_type"), "1"), "back to SUITCASE (%s)", gbuf);
    api->set_param(in, "p_ph_sw", "1"); api->set_param(in, "p_cp_depth", "33"); render(in, 3);
    CHECK(!strcmp(get(in, "p_cp_depth"), "33"), "CHORUS DEPTH set while the phaser is on (%s)", gbuf);
    CHECK(!strcmp(get(in, "p_ph_sw"), "1"), "the phaser stays on (%s)", gbuf);
    bpm_v = 97.5; render(in, 3);                             /* (no clock running: the Move's tempo is the app's) */
    CHECK(!strcmp(get(in, "dev:bpm10"), "975"), "the host's 97.5 BPM (%s)", gbuf);
    CHECK(strstr(get(in, "dev_inert"), "clock=1 bpm10=975") != NULL, "CLOCK EXT, the host's tempo: %s", gbuf);
    bpm_v = 0.0; render(in, 3);
    CHECK(!strcmp(get(in, "dev:bpm10"), "1200"), "no host tempo: 120 BPM (%s)", gbuf);
    bpm_v = 500.0; render(in, 3);
    CHECK(!strcmp(get(in, "dev:bpm10"), "1200"), "a host tempo past 400: 120 BPM (%s)", gbuf);
    bpm_v = 300.0; render(in, 3);
    CHECK(!strcmp(get(in, "dev:bpm10"), "2400"), "300 BPM: the app's 240 at most (%s)", gbuf);
    bpm_v = 97.5; render(in, 3);
    api->set_param(in, "s_tune", "-12"); render(in, 3);
    CHECK(!strcmp(get(in, "s_tune"), "-12"), "TUNE -12 (%s)", gbuf);
    {   /* what the Move owns has no key: the sequencer, the arpeggiator, the chords, the scales, BPM, CLOCK */
        const char *gone[] = { "s_arpgate", "s_sqbars", "s_chtype", "s_chstrum", "s_scmode", "s_scale", "s_root", "s_trans",
                               "s_bpm", "s_clock" };
        for (i = 0; i < sizeof gone / sizeof gone[0]; i++)
            CHECK(!strcmp(get(in, gone[i]), "(none)"), "no %s on the Move (%s)", gone[i], gbuf);
    }
    api->set_param(in, "s_mch", "3"); render(in, 3);
    CHECK(!strcmp(get(in, "s_mch"), "3"), "CHANNEL 3 (%s)", gbuf);
    api->set_param(in, "s_mch", "0"); render(in, 3);
    api->set_param(in, "s_sustain", "1"); render(in, 3);
    CHECK(!strcmp(get(in, "s_sustain"), "1"), "SUSTAIN latched (%s)", gbuf);
    api->set_param(in, "s_sustain", "0"); render(in, 3);
    CHECK(!strcmp(get(in, "s_sustain"), "0"), "SUSTAIN off (%s)", gbuf);
    api->set_param(in, "p_tone", "banana"); render(in, 2);
    CHECK(stat_of(in, "keys_bad") == 1, "a non-number refused (%u)", stat_of(in, "keys_bad"));
    api->set_param(in, "p_drive", "500"); render(in, 3);
    CHECK(!strcmp(get(in, "p_drive"), "127"), "clamped to 127 (%s)", gbuf);
    {   /* every MK_PARAM / MK_SLOT key: set its value back and forth, read it back */
        const char *ks[] = { "p_spread", "p_decay", "p_cmp_amt", "p_cmp_ratio", "p_drv_sw", "p_fl_type", "p_fl_freq",
                             "p_rv_type", "p_rv_size", "p_dl_sw", "p_dl_time", "p_lfo_shape", "p_lfo_rate", "s_tune", "s_twkind" };
        const char *vs[] = { "90", "10", "60", "6", "3", "4", "70", "2", "33", "2", "40", "3", "50", "7", "3" };
        for (i = 0; i < sizeof ks / sizeof ks[0]; i++) {
            api->set_param(in, ks[i], vs[i]); render(in, 3);
            CHECK(!strcmp(get(in, ks[i]), vs[i]), "%s = %s (is %s)", ks[i], vs[i], gbuf);
        }
    }
    api->set_param(in, "p_mot_rate", "127"); render(in, 3);
    CHECK(!strcmp(get(in, "p_mot_rate"), "127"), "MOTOR RATE past the panel's 120 (%s)", gbuf);
    api->set_param(in, "p_mot_rate", "78"); render(in, 3);
    {   /* the timer ring: "<index> <us> <frames>" lines, the indices running on */
        unsigned a, us, fr, prev = 0, lines = 0; const char *p = get(in, "dev_cpu_ring");
        while (sscanf(p, "%u %u %u", &a, &us, &fr) == 3) { CHECK(fr == 128 && (lines == 0 || a == prev + 1), "ring line %u", a); prev = a; lines++; p = strchr(p, '\n'); if (!p) break; p++; }
        CHECK(lines > 100, "the timer ring has the callbacks (%u lines)", lines);
        CHECK(strstr(get(in, "dev_cpu"), "max_us=") != NULL, "dev_cpu");
    }
    api->set_param(in, "preset", "2"); render(in, 3);
    CHECK(!strcmp(get(in, "preset"), "2") && !strncmp(get(in, "preset_name"), "F03", 3), "preset F03 (%s)", gbuf);

    /* M1-07: state round trip */
    {
        static char s1[4096], s2[4096];
        int n1 = api->get_param(in, "state", s1, sizeof s1), n2;
        char tone[16], spread[16];
        snprintf(tone, sizeof tone, "%s", get(in, "p_tone")); snprintf(spread, sizeof spread, "%s", get(in, "p_spread"));
        CHECK(n1 > 600 && n1 < 1200, "state size %d", n1);
        CHECK(api->get_param(in, "state", s2, 100) == -1, "a small buffer is refused");
        api->set_param(in, "p_tone", "40"); api->set_param(in, "s_tune", "20"); render(in, 3);
        api->set_param(in, "state", s1); render(in, 3);
        n2 = api->get_param(in, "state", s2, sizeof s2);
        { char *r1 = strstr(s1, "\",\"rev\""), *r2 = strstr(s2, "\",\"rev\""); if (r1) *r1 = 0; if (r2) *r2 = 0; }
        CHECK(n2 > 0 && !strcmp(s1, s2), "state load gives the state back");
        CHECK(!strcmp(get(in, "dev:bpm10"), "975"), "the tempo stays the host's (%s)", gbuf);
        CHECK(!strcmp(get(in, "p_tone"), tone) && !strcmp(get(in, "p_spread"), spread), "the sound comes back: TONE %s (%s)", tone, gbuf);
        /* 1000 random records: load, read (= the record sanitized as the FM-1 does), load that, read: the same */
        srand(12345);
        for (i = 0; i < 1000 && fails < 20; i++) {
            char js[1200], *o = js, *p;
            unsigned k;
            int valid = i & 1;
            o += sprintf(o, "{\"v\":\"1\",\"set\":\"");
            for (k = 0; k < 256; k++) {
                unsigned b = (unsigned)rand() & 255u;
                if (valid && k == 0) b = 2;                   /* (half: version 2, so most fields survive) */
                if (valid && k == 1) b = 255;
                o += sprintf(o, "%02x", b);
            }
            o += sprintf(o, "\",\"base\":\"");
            for (k = 0; k < 64; k++) o += sprintf(o, "%02x", (unsigned)rand() & 127u);
            o += sprintf(o, "\",\"hand\":\"%u\"}", 400u + (unsigned)rand() % 2001u);
            api->set_param(in, "state", js); render(in, 2);
            api->get_param(in, "state", s1, sizeof s1);
            api->set_param(in, "state", s1); render(in, 2);
            api->get_param(in, "state", s2, sizeof s2);
            p = strstr(s1, "\",\"rev\""); if (p) *p = 0;
            p = strstr(s2, "\",\"rev\""); if (p) *p = 0;
            if (strcmp(s1, s2)) {
                unsigned d = 0; while (s1[d] && s1[d] == s2[d]) d++;
                CHECK(0, "record %u: the sanitized state is not stable: char %u (set byte %d): %.8s / %.8s", i, d,
                      ((int)d - 17) / 2, s1 + d, s2 + d);
            }
        }
        CHECK(stat_of(in, "state_bad") == 0, "no state refused (%u)", stat_of(in, "state_bad"));
        api->set_param(in, "state", "{\"v\":\"1\",\"set\":\"00\"}");
        api->set_param(in, "state", "garbage");
        CHECK(stat_of(in, "state_bad") == 2, "bad states refused (%u)", stat_of(in, "state_bad"));
        render(in, 3);
    }

    /* M1-05: the clock fallback at 60..200 BPM: F8 count = floor(beat * 24) + 1, the app follows the tempo */
    {
        double bpms[] = { 60.0, 97.0, 120.0, 200.0 };
        for (unsigned t = 0; t < 4; t++) {
            unsigned f0 = stat_of(in, "f8_synth"), blocks = (unsigned)(44100.0 * 60.0 / 128.0), b;   /* 60 s */
            bpm_v = bpms[t]; beat = 0.0; clk_status = MOVE_CLOCK_STATUS_RUNNING;
            for (b = 0; b < blocks; b++) {
                api->render_block(in, blk, 128);
                beat += bpm_v / 60.0 * 128.0 / 44100.0;
            }
            unsigned got = stat_of(in, "f8_synth") - f0, want = (unsigned)((beat - bpm_v / 60.0 * 128.0 / 44100.0) * 24.0) + 1u;
            CHECK(got == want, "%.0f BPM: %u pulses in 60 s, want %u (drift)", bpm_v, got, want);
            unsigned bpm10 = (unsigned)atoi(get(in, "dev:bpm10"));
            CHECK(bpm10 + 2 >= (unsigned)(bpm_v * 10.0) && bpm10 <= (unsigned)(bpm_v * 10.0) + 2, "%.0f BPM: the app shows %u",
                  bpm_v, bpm10);
            clk_status = MOVE_CLOCK_STATUS_STOPPED; render(in, 50);
        }
        /* no beat position (an older host): the tempo integrated */
        host.get_beat_position = NULL; beat_null = 1;
        {
            unsigned f0 = stat_of(in, "f8_synth"), b;
            bpm_v = 120.0; clk_status = MOVE_CLOCK_STATUS_RUNNING;
            for (b = 0; b < 3445; b++) api->render_block(in, blk, 128);   /* 10 s: 480 pulses */
            unsigned got = stat_of(in, "f8_synth") - f0;
            CHECK(got >= 478 && got <= 481, "no beat position: %u pulses in 10 s at 120 (want 480)", got);
            clk_status = MOVE_CLOCK_STATUS_STOPPED; render(in, 20);
        }
        host.get_beat_position = h_beat;
        /* a real MIDI clock wins: no pulses made while F8 arrive */
        {
            unsigned f0 = stat_of(in, "f8_synth"), b;
            uint8_t f8[1] = { 0xF8 };
            clk_status = MOVE_CLOCK_STATUS_RUNNING; beat = 0.0;
            for (b = 0; b < 1000; b++) {
                if (b % 7 == 0) api->on_midi(in, f8, 1, MOVE_MIDI_SOURCE_HOST);
                api->render_block(in, blk, 128);
                beat += 2.0 * 128.0 / 44100.0;
            }
            CHECK(stat_of(in, "f8_synth") - f0 <= 1, "a real clock: no pulses made (%u)", stat_of(in, "f8_synth") - f0);
            clk_status = MOVE_CLOCK_STATUS_STOPPED; render(in, 20);
        }
        /* the clock probe's counters (dev_stats): the realtime bytes that came in, the host's transport and tempo */
        {
            unsigned r8 = stat_of(in, "rt_f8"), ra = stat_of(in, "rt_fa"), rc = stat_of(in, "rt_fc"), b;
            uint8_t fa[1] = { 0xFA }, f8[1] = { 0xF8 }, fc[1] = { 0xFC };
            clk_status = MOVE_CLOCK_STATUS_RUNNING; bpm_v = 97.0; beat = 0.0;
            api->on_midi(in, fa, 1, MOVE_MIDI_SOURCE_EXTERNAL);
            for (b = 0; b < 30; b++) { api->on_midi(in, f8, 1, MOVE_MIDI_SOURCE_EXTERNAL); render(in, 1); }
            CHECK(stat_of(in, "host_run") == 1 && stat_of(in, "host_bpm10") == 970u, "host_run %u host_bpm10 %u (want 1, 970)",
                  stat_of(in, "host_run"), stat_of(in, "host_bpm10"));
            api->on_midi(in, fc, 1, MOVE_MIDI_SOURCE_EXTERNAL);
            clk_status = MOVE_CLOCK_STATUS_STOPPED; bpm_v = 120.0; render(in, 20);
            CHECK(stat_of(in, "rt_f8") - r8 == 30u && stat_of(in, "rt_fa") - ra == 1u && stat_of(in, "rt_fc") - rc == 1u &&
                  stat_of(in, "host_run") == 0, "rt_f8 +%u rt_fa +%u rt_fc +%u host_run %u (want 30 1 1 0)",
                  stat_of(in, "rt_f8") - r8, stat_of(in, "rt_fa") - ra, stat_of(in, "rt_fc") - rc, stat_of(in, "host_run"));
        }
    }

    api->destroy_instance(in);
    in = create(NULL);
    CHECK(in != NULL, "create again after destroy");
    render(in, 10);
    api->destroy_instance(in);
    printf("%s    bridge api (%d failures)\n", fails ? "FAIL" : "ok", fails);
    return fails != 0;
}

static int keys(const char *so)
{
    char line[256], k[64], v[64];
    void *in;
    load(so);
    in = create(NULL);
    if (!in) { printf("FAIL create\n"); return 1; }
    render(in, 400);
    while (fgets(line, sizeof line, stdin)) {
        if (sscanf(line, "%63s %63s", k, v) != 2) continue;
        api->set_param(in, k, v); render(in, 3);
        printf("%s\t%s\t%s\n", k, v, get(in, k));
    }
    api->destroy_instance(in);
    return 0;
}
static int script(const char *so)
{
    static char st[8192];                        /* state_save / state_load */
    char line[256], k[64], v[64];
    unsigned a, b, c, d;
    int n;
    void *in;
    load(so);
    in = create(NULL);
    if (!in) { printf("FAIL create\n"); return 1; }
    render(in, 400);
    while (fgets(line, sizeof line, stdin)) {
        if (sscanf(line, "render %d", &n) == 1) render(in, n);
        else if (sscanf(line, "set %63s %63s", k, v) == 2) api->set_param(in, k, v);
        else if (sscanf(line, "midi %u %i %i %i", &a, &b, &c, &d) == 4) {
            uint8_t m[3] = { (uint8_t)b, (uint8_t)c, (uint8_t)d };
            api->on_midi(in, m, 3, (int)a);
        } else if (!strncmp(line, "state_save", 10)) api->get_param(in, "state", st, sizeof st);
        else if (!strncmp(line, "state_load", 10)) {        /* "state_load nomap": the state without its "map" */
            char *m = strstr(st, ",\"map\":\"");
            if (strstr(line, "nomap") && m) { char *e = strchr(m + 8, '"'); if (e) memmove(m, e + 1, strlen(e + 1) + 1); }
            api->set_param(in, "state", st);
        } else if (sscanf(line, "get %63s", k) == 1) printf("%s\t%s\n", k, get(in, k));
        else if (sscanf(line, "rms %d", &n) == 1) {
            double e = 0.0; long cnt = 0;
            while (n-- > 0) { api->render_block(in, blk, 128); for (int i = 0; i < 256; i++) { e += (double)blk[i] * blk[i]; cnt++; } }
            printf("rms\t%.3f\n", cnt ? e / (double)cnt : 0.0);   /* (the mean square, LSB^2: dB in the caller) */
        }
        fflush(stdout);
    }
    api->destroy_instance(in);
    return 0;
}
static int presets(const char *so)
{
    char b[32];
    void *in;
    int i;
    load(so);
    in = create(NULL);
    if (!in) { printf("FAIL create\n"); return 1; }
    render(in, 400);
    printf("count %s\n", get(in, "preset_count"));
    for (i = 0; i < 35; i++) {
        snprintf(b, sizeof b, "%d", i); api->set_param(in, "preset", b); render(in, 3);
        printf("preset %d %s", i, get(in, "preset")); printf(" %s\n", get(in, "preset_name"));
    }
    for (i = 0; i < 6; i++) { snprintf(b, sizeof b, "%d", i); api->set_param(in, "p_type", b); render(in, 3); printf("type %d %s\n", i, get(in, "ui_engine")); }
    printf("hierarchy %d\n", (int)strlen(get(in, "ui_hierarchy")));
    api->destroy_instance(in);
    return 0;
}
static unsigned rnd_s = 1;
static unsigned rnd(void) { rnd_s = rnd_s * 1103515245u + 12345u; return rnd_s >> 8; }
static int gatep(const char *so, unsigned nblk)
{
    static const char *K[] = { "p_type", "p_tone", "p_drive", "p_drv_sw", "p_rv_type", "p_rv_depth", "p_dl_sw", "p_dl_depth",
                               "p_cp_sw", "p_ph_sw", "p_tw_depth", "p_fl_type", "p_fl_freq", "p_cmp_sw", "p_level", "s_tune",
                               "s_twkind", "p_lfo_rate", "s_sustain", "p_mot_on", "p_spread", "p_dl_sync" };
    static char st[4096], val[16];
    void *in;
    unsigned b, full = 0, peak = 0, i;
    load(so);
    in = create(NULL);
    if (!in) { printf("FAIL create\n"); return 1; }
    render(in, 400);
    api->get_param(in, "state", st, sizeof st);
    printf("gatep: %u random blocks\n", nblk);
    fflush(stdout);
#ifndef NO_GUARDS
    if (guard_no_syscalls() != 0) { printf("FAIL seccomp\n"); return 1; }
#endif
    for (b = 0; b < nblk; b++) {
        unsigned r = rnd() % 16u;
        in_cb = 1;
        if (r < 6) { uint8_t m[3] = { (uint8_t)(rnd() & 1 ? 0x90 : 0x80), (uint8_t)(24 + rnd() % 84), (uint8_t)(1 + rnd() % 127) }; api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        else if (r == 6) { uint8_t m[3] = { 0xB0, (uint8_t)(rnd() % 128), (uint8_t)(rnd() % 128) }; if (m[1] != 9 && m[1] != 10 && m[1] != 119) api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        else if (r == 7) { uint8_t m[3] = { 0xE0, 0, (uint8_t)(rnd() % 128) }; api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        else if (r == 8) { i = rnd() % (sizeof K / sizeof K[0]); snprintf(val, sizeof val, "%u", rnd() % 128); api->set_param(in, K[i], val); }
        else if (r == 9) { char g[64]; api->get_param(in, K[rnd() % (sizeof K / sizeof K[0])], g, sizeof g); api->get_param(in, "ui_engine", g, sizeof g); }
        else if (r == 10 && rnd() % 64 == 0) api->get_param(in, "state", st, sizeof st);
        else if (r == 11 && rnd() % 256 == 0) api->set_param(in, "state", st);
        else if (r == 12 && rnd() % 128 == 0) { uint8_t m[3] = { 0xB0, 123, 0 }; api->on_midi(in, m, 3, MOVE_MIDI_SOURCE_EXTERNAL); }
        else if (r == 13 && rnd() % 64 == 0) {   /* the dev player: a take begun, loaded, started, stopped, read */
            static const char *P[] = { "begin g 40", "go 0", "stop", "go", "begin x", "bad" };
            char g[160];
            api->set_param(in, "dev_play", P[rnd() % 6]);
            api->set_param(in, "dev_play_add", rnd() & 1 ? "0 0x90 60 100;10 128 60 0;5 0x90 1 1" : "x;0x7f 1 2 3;20 0xF8 0 0");
            api->get_param(in, "dev_play", g, sizeof g);
        }
        api->render_block(in, blk, 128);
        in_cb = 0;
        for (i = 0; i < 256; i++) { unsigned a = (unsigned)abs(blk[i]); if (a >= 32767u) full++; if (a > peak) peak = a; }
    }
    CHECK(bad_alloc == 0, "%d allocations inside callbacks", bad_alloc);
    CHECK(full * 1000u < nblk * 256u, "%u samples at full scale of %u", full, nblk * 256u);
#ifndef NO_GUARDS
    printf("%s    gate P: %u blocks, no syscall (seccomp), %d allocations in callbacks, peak %u, %u samples at full scale\n",
           fails ? "FAIL" : "ok", nblk, bad_alloc, peak, full);
#else
    printf("%s    gate P (sanitizers, no seccomp / allocation guard): %u blocks, peak %u, %u samples at full scale\n",
           fails ? "FAIL" : "ok", nblk, peak, full);
#endif
    return fails != 0;
}

/* ---- run (M1-12): a timed script through the plugin path; the guards from the first line on */
typedef struct { unsigned t; char kind; uint8_t m[3]; int len; char a[40], b[24]; } run_ev_t;
static int run_script(const char *so, const char *script, const char *wav)
{
    static run_ev_t ev[200000];
    static char st[8192], line[256];
    unsigned nev = 0, end = 0, i, peak = 0;
    uint32_t frames, done = 0;
    int16_t *pcm;
    FILE *f = fopen(script, "r"), *w;
    void *in;
    if (!f) { printf("FAIL no script %s\n", script); return 1; }
    while (fgets(line, sizeof line, f) && nev < sizeof ev / sizeof ev[0]) {
        run_ev_t *e = &ev[nev];
        char what[16];
        unsigned a, b, c;
        memset(e, 0, sizeof *e);
        if (line[0] == '#' || sscanf(line, "%u %15s", &e->t, what) != 2) continue;
        if (!strcmp(what, "end")) { end = e->t; continue; }
        if (!strcmp(what, "midi") && sscanf(line, "%*u %*s %i %u %u", &a, &b, &c) == 3) {
            e->kind = 'm'; e->m[0] = (uint8_t)a; e->m[1] = (uint8_t)b; e->m[2] = (uint8_t)c;
            e->len = a >= 0xF8u ? 1 : (a & 0xF0u) == 0xC0u || (a & 0xF0u) == 0xD0u || a == 0xF3u ? 2 : 3;
        } else if (!strcmp(what, "key") && sscanf(line, "%*u %*s %39s %23s", e->a, e->b) == 2) e->kind = 'k';
        else if (!strcmp(what, "preset") && sscanf(line, "%*u %*s %23s", e->b) == 1) e->kind = 'p';
        else if (!strcmp(what, "getstate")) e->kind = 'g';
        else if (!strcmp(what, "setstate")) e->kind = 's';
        else { printf("FAIL run: unknown line: %s", line); fclose(f); return 1; }
        nev++;
    }
    fclose(f);
    if (!end) { printf("FAIL run: no end line\n"); return 1; }
    frames = (uint32_t)((uint64_t)end * 44100u / 1000u) / 128u * 128u;
    pcm = malloc(((size_t)frames + 128u) * 4u);
    w = fopen(wav, "wb");
    if (!pcm || !w) { printf("FAIL run: buffers\n"); return 1; }
    load(so);
    in = create(NULL);
    if (!in) { printf("FAIL create\n"); return 1; }
    api->get_param(in, "state", st, sizeof st);              /* (a state to load before the first getstate) */
    printf("run %s: %u events, %u ms\n", script, nev, end);  /* (stdio's buffer set up before the guard) */
    fflush(stdout);
#ifndef NO_GUARDS
    if (guard_no_syscalls() != 0) { printf("FAIL seccomp\n"); return 1; }
#endif
    i = 0;
    while (done < frames) {
        unsigned now = (unsigned)((uint64_t)done * 1000u / 44100u);
        in_cb = 1;
        for (; i < nev && ev[i].t <= now; i++) {
            run_ev_t *e = &ev[i];
            if (e->kind == 'm') api->on_midi(in, e->m, e->len, MOVE_MIDI_SOURCE_EXTERNAL);
            else if (e->kind == 'k') api->set_param(in, e->a, e->b);
            else if (e->kind == 'p') api->set_param(in, "preset", e->b);
            else if (e->kind == 'g') api->get_param(in, "state", st, sizeof st);
            else if (e->kind == 's') api->set_param(in, "state", st);
        }
        api->render_block(in, pcm + 2u * done, 128);
        in_cb = 0;
        done += 128u;
    }
    for (i = 0; i < 2u * frames; i++) { unsigned a = (unsigned)abs(pcm[i]); if (a > peak) peak = a; }
    {   /* (write is the one syscall the guard lets through) */
        uint32_t hdr[11] = { 0x46464952u, frames * 4u + 36u, 0x45564157u, 0x20746d66u, 16u, 0x00020001u, 44100u,
                             44100u * 4u, 0x00100004u, 0x61746164u, frames * 4u };
        if (write(fileno(w), hdr, sizeof hdr) != (ssize_t)sizeof hdr ||
            write(fileno(w), pcm, (size_t)frames * 4u) != (ssize_t)((size_t)frames * 4u)) { printf("FAIL run: write\n"); return 1; }
    }
    CHECK(bad_alloc == 0, "%d allocations inside callbacks", bad_alloc);
    printf("%s    run %s: %u events, %u frames, peak %u, q_full %u\n", fails ? "FAIL" : "ok", script, nev, frames, peak,
           stat_of(in, "q_full"));
    fflush(stdout);
    _exit(fails != 0);                                       /* (no close: the guard allows write and exit only) */
}

/* ---- persist (M1-08): run as two processes on one folder: save, then check */
static int persist(const char *so, const char *dir, int save)
{
    static const char *K[][2] = { { "s_mch", "5" }, { "s_tune", "-7" } };   /* (settings: the sound itself comes back through the host's
                                                          * state, not store.bin) */
    char want[64], ms[64];
    void *in;
    unsigned i, t;
    load(so);
    in = create(dir);
    if (!in) { printf("FAIL create\n"); return 1; }
    render(in, 400);
    if (save) {
        char crc0[16];
        snprintf(crc0, sizeof crc0, "%s", get(in, "dev_pattern_crc"));
        {   /* no sequencer and no arpeggiator on the Move: their CCs, a transport Start and notes record nothing */
            static const uint8_t tries[][3] = { { 0xB0, 63, 127 }, { 0xB0, 60, 127 }, { 0xB0, 52, 3 }, { 0xB0, 45, 127 },
                                                { 0xB0, 56, 4 }, { 0xFA, 0, 0 } };
            for (i = 0; i < sizeof tries / sizeof tries[0]; i++) { api->on_midi(in, tries[i], tries[i][0] == 0xFA ? 1 : 3, MOVE_MIDI_SOURCE_EXTERNAL); render(in, 3); }
            for (t = 0; t < 16; t++) {
                uint8_t on[3] = { 0x90, (uint8_t)(48 + t * 3 % 24), 100 }, off[3] = { 0x80, on[1], 0 };
                api->on_midi(in, on, 3, MOVE_MIDI_SOURCE_EXTERNAL); render(in, 40);
                api->on_midi(in, off, 3, MOVE_MIDI_SOURCE_EXTERNAL); render(in, 40);
            }
            CHECK(strstr(get(in, "dev_inert"), "seq=0 rec=0 arp=0 hold=0 chord=0 scale=0 trans=0 clock=1") != NULL,
                  "the sequencer, the arpeggiator, the chords and the scales stay off: %s", gbuf);
            { unsigned cd = 0; sscanf(gbuf, "cc_drop=%u", &cd); CHECK(cd >= 5u, "their CCs dropped: %s", gbuf); }
            if (!fails) printf("ok    no sequencer, arpeggiator, chords or scales: %s\n", gbuf);
        }
        for (i = 0; i < sizeof K / sizeof K[0]; i++) { api->set_param(in, K[i][0], K[i][1]); render(in, 3); }   /* (after:
                                                               * s_mch moves the MIDI channel off the notes') */
        for (t = 0; t < 6000; t++) {                          /* (the worker writes within 100 ms of the next staging) */
            struct stat a; char f1[600];
            render(in, 30); usleep(2000);
            snprintf(f1, sizeof f1, "%s/store.bin", dir);
            if (!stat(f1, &a)) break;
        }
        CHECK(!strcmp(get(in, "dev_pattern_crc"), crc0), "no pattern recorded (crc %s, was %s)", gbuf, crc0);
        render(in, 700);                                      /* (the settings' own save wait, then the staging) */
        snprintf(want, sizeof want, "%s", get(in, "dev_pattern_crc"));
        {                                                     /* the timer ring to <module dir>/cpu-ring.log */
            char p[600], l[64]; FILE *f; unsigned lines = 0, idx, us, fr, last = 0; int ok = 1;
            api->set_param(in, "dev_cpu_log", "1"); render(in, 200); usleep(300000); render(in, 200); usleep(300000);
            api->set_param(in, "dev_cpu_log", "0"); usleep(300000);
            snprintf(p, sizeof p, "%s/cpu-ring.log", dir);
            f = fopen(p, "r");
            while (f && fgets(l, sizeof l, f)) { if (sscanf(l, "%u %u %u", &idx, &us, &fr) != 3 || fr != 128u || (lines && idx != last + 1u)) ok = 0; last = idx; lines++; }
            if (f) fclose(f);
            CHECK(ok && lines >= 400u, "cpu-ring.log: %u lines \"idx us frames\", in order (%d)", lines, ok);
            if (ok && lines >= 400u) printf("ok    cpu-ring.log: %u lines, consecutive indices\n", lines);
        }
        {                                                     /* dev_stats to <module dir>/stats.log (the clock probe) */
            char p[600], l[600]; FILE *f; unsigned lines = 0, good = 0;
            api->set_param(in, "dev_stats_log", "1"); render(in, 100); usleep(350000);
            api->set_param(in, "dev_stats_log", "0"); usleep(250000);
            snprintf(p, sizeof p, "%s/stats.log", dir);
            f = fopen(p, "r");
            while (f && fgets(l, sizeof l, f)) { lines++; good += strstr(l, "ms=") == l && strstr(l, " rt_f8=") && strstr(l, " host_run=") && strstr(l, " bpm10=") && l[strlen(l) - 1] == '\n'; }
            if (f) fclose(f);
            CHECK(lines >= 2u && good == lines, "stats.log: %u lines, %u of them dev_stats", lines, good);
            if (lines >= 2u && good == lines) printf("ok    stats.log: %u lines of dev_stats\n", lines);
        }
        snprintf(ms, sizeof ms, "%s", want);
        printf("persist save: %s, pattern %s\n", get(in, "dev_store"), want);
        api->destroy_instance(in);                            /* (the last staged write) */
        { FILE *f; char p[600]; snprintf(p, sizeof p, "%s/expect.txt", dir); f = fopen(p, "w"); if (f) { fprintf(f, "%s\n", ms); fclose(f); } }
        return fails != 0;
    }
    {
        FILE *f; char p[600]; snprintf(p, sizeof p, "%s/expect.txt", dir); f = fopen(p, "r");
        want[0] = 0; if (f) { if (!fgets(want, sizeof want, f)) want[0] = 0; fclose(f); }
        want[strcspn(want, "\n")] = 0;
    }
    CHECK(strstr(get(in, "dev_store"), "loaded=1") != NULL, "store.bin loaded (no pattern.bin): %s", gbuf);
    {   char f2[600]; struct stat b; snprintf(f2, sizeof f2, "%s/pattern.bin", dir);
        CHECK(stat(f2, &b) != 0, "no pattern.bin written"); }
    for (i = 0; i < sizeof K / sizeof K[0]; i++) { const char *v = get(in, K[i][0]); CHECK(!strcmp(v, K[i][1]), "%s restored: %s (want %s)", K[i][0], v, K[i][1]); }
    CHECK(want[0] && !strcmp(get(in, "dev_pattern_crc"), want), "the pattern still empty: %s (want %s)", gbuf, want);
    printf("%s    persist: the settings back in a new process, no pattern (%s)\n", fails ? "FAIL" : "ok", get(in, "dev_store"));
    api->destroy_instance(in);
    return fails != 0;
}

/* ---- boot: a new instance's settings are move/tests/boot_keys.txt's (what move_hil.py writes before usb-host) */
static int boot(const char *so, const char *file)
{
    char line[256], k[64], v[64];
    FILE *f = fopen(file, "r");
    int n = 0;
    void *in;
    if (!f) { printf("FAIL boot: no %s\n", file); return 1; }
    load(so);
    in = create(NULL);
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || sscanf(line, "%63s %63s", k, v) != 2) continue;
        CHECK(!strcmp(get(in, k), v), "boot: %s is %s, %s says %s", k, gbuf, file, v);
        n++;
    }
    fclose(f);
    api->destroy_instance(in);
    if (!fails) printf("ok    boot: %d settings as %s\n", n, file);
    return fails != 0;
}

/* ---- hq: the HQ switch (p_hq, FR_HQ builds). Off at boot; 1 plays new notes HQ (C7 at v100 is not the Classic
 * note: the exact pulse and the 2x pickup), 0 back to Classic; the reads follow the writes */
static void hq_note(void *in, int16_t *x, int blocks)
{
    uint8_t on[3] = { 0x90, 96, 100 }, off[3] = { 0x80, 96, 0 }, cut[3] = { 0xB0, 120, 0 };
    api->on_midi(in, on, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    for (int b = 0; b < blocks; b++) api->render_block(in, x + 256 * b, 128);
    api->on_midi(in, off, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    api->on_midi(in, cut, 3, MOVE_MIDI_SOURCE_EXTERNAL);
    render(in, 400);
}
static int hq(const char *so)
{
    static int16_t a[256 * 200], b[256 * 200], c[256 * 200];
    long da = 0, dc = 0, loud = 0;
    void *in;
    load(so);
    in = create(NULL);
    CHECK(!strcmp(get(in, "p_hq"), "0"), "p_hq at boot: %s", gbuf);
    render(in, 20);
    hq_note(in, a, 200);
    cb_set(in, "p_hq", "7");                             /* (any non-zero: on) */
    render(in, 2);
    CHECK(!strcmp(get(in, "p_hq"), "1"), "p_hq after 1: %s", gbuf);
    hq_note(in, b, 200);
    cb_set(in, "p_hq", "0");
    render(in, 2);
    CHECK(!strcmp(get(in, "p_hq"), "0"), "p_hq after 0: %s", gbuf);
    hq_note(in, c, 200);
    for (long i = 0; i < 256L * 200; i++) { da += a[i] != b[i]; dc += b[i] != c[i]; loud += abs(a[i]) > 1000; }
    CHECK(loud > 1000, "the Classic note is silent (%ld loud samples)", loud);
    CHECK(da > 10000 && dc > 10000, "the HQ note is the Classic note: %ld / %ld samples differ", da, dc);
    api->destroy_instance(in);
    if (!fails) printf("OK hq: p_hq 0 / 1 / 0, the HQ note differs from Classic in %ld of %ld samples\n", da, 256L * 200);
    return fails != 0;
}

int main(int argc, char **argv)
{
    if (argc >= 5 && !strcmp(argv[1], "persist")) return persist(argv[2], argv[3], !strcmp(argv[4], "save"));
    if (argc >= 3 && !strcmp(argv[1], "keys")) return keys(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "presets")) return presets(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "script")) return script(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "gatep")) return gatep(argv[2], argc > 3 ? (unsigned)atoi(argv[3]) : 10000u);
    if (argc >= 5 && !strcmp(argv[1], "eq")) return eq(argv[2], argv[3], argv[4]);
    if (argc >= 5 && !strcmp(argv[1], "run")) return run_script(argv[2], argv[3], argv[4]);
    if (argc >= 5 && !strcmp(argv[1], "play")) return play(argv[2], argv[3], argv[4]);
    if (argc >= 3 && !strcmp(argv[1], "api")) return api_tests(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "hq")) return hq(argv[2]);
    if (argc >= 4 && !strcmp(argv[1], "boot")) return boot(argv[2], argv[3]);
    printf("usage: test_bridge eq DSP SCRIPT HOST.wav | api DSP | hq DSP | boot DSP FILE | keys DSP | presets DSP | gatep DSP [N]\n");
    return 2;
}
