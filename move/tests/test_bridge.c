/* SPDX-License-Identifier: GPL-3.0-only */
/* The bridge's host tests (M1-03 .. M1-07), on build/move/frodhes/dsp.so through a fake host:
 *   test_bridge eq DSP SCRIPT HOST.wav   M1-03/04: the script's MIDI lines replayed through the plugin (set_param
 *                                        dev_midi, due at their ms) give the host build's samples exactly
 *   test_bridge api DSP                  M1-04..07: the singleton, a 200-message burst in one block, touch edges
 *                                        not played, the parameter keys, presets, the state round trip (1000
 *                                        random records), the clock fallback at 60..200 BPM
 *   test_bridge keys DSP < LINES         "key value" lines: set, render, print "key<TAB>value<TAB>read back"
 *   test_bridge presets DSP              every preset: "preset <i> <preset> <name>"; every TYPE: "type <t> <ui_engine>"
 *   test_bridge gatep DSP [N]            Gate P (M1-11): N (10^4) random blocks of MIDI, key writes, state reads and
 *                                        loads; no syscall (seccomp) and no allocation inside any callback (unless
 *                                        built -DNO_GUARDS, for ASan / UBSan), no sample stuck at full scale */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../vendor/plugin_api_v1.h"
#ifndef NO_GUARDS
#include "seccomp_guard.h"
/* the allocation guard: any malloc family call while in_cb is set is counted */
extern void *__libc_malloc(size_t), *__libc_calloc(size_t, size_t), *__libc_realloc(void *, size_t);
extern void __libc_free(void *);
static int in_cb, bad_alloc;
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
static void *load(const char *so)
{
    void *h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("FAIL dlopen %s: %s\n", so, dlerror()); exit(1); }
    move_plugin_init_v2_fn init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!init) { printf("FAIL no entry\n"); exit(1); }
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
    in = api->create_instance("build/move/frodhes", NULL);
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

/* ---- api */
static int api_tests(const char *so)
{
    void *in, *in2;
    unsigned n0, i;
    load(so);
    in = api->create_instance("build/move/frodhes", NULL);
    CHECK(in != NULL, "create");
    in2 = api->create_instance("build/move/frodhes", NULL);
    CHECK(in2 == NULL, "a second live instance is refused (one app a process)");
    render(in, 400);                                          /* boot, the splash */

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
    api->set_param(in, "s_bpm", "97.5"); render(in, 3);
    CHECK(!strcmp(get(in, "s_bpm"), "97.5"), "BPM 97.5 (%s)", gbuf);
    api->set_param(in, "s_tune", "-12"); render(in, 3);
    CHECK(!strcmp(get(in, "s_tune"), "-12"), "TUNE -12 (%s)", gbuf);
    api->set_param(in, "s_root", "9"); render(in, 3);
    CHECK(!strcmp(get(in, "s_root"), "9"), "ROOT 9 (%s)", gbuf);
    api->set_param(in, "s_arpgate", "55"); render(in, 3);
    CHECK(!strcmp(get(in, "s_arpgate"), "55"), "ARP GATE 55 (%s)", gbuf);
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
                             "p_rv_type", "p_rv_size", "p_dl_sw", "p_dl_time", "p_lfo_shape", "s_scale", "s_trans", "s_chtype",
                             "s_arpmode", "s_sqbars", "s_sqswing", "s_clock", "s_twkind" };
        const char *vs[] = { "90", "10", "60", "6", "3", "4", "70", "2", "33", "2", "40", "3", "5", "-7", "4", "2", "8", "61", "0", "3" };
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
        CHECK(n1 > 600 && n1 < 1200, "state size %d", n1);
        CHECK(api->get_param(in, "state", s2, 100) == -1, "a small buffer is refused");
        api->set_param(in, "p_tone", "40"); api->set_param(in, "s_bpm", "150"); render(in, 3);
        api->set_param(in, "state", s1); render(in, 3);
        n2 = api->get_param(in, "state", s2, sizeof s2);
        { char *r1 = strstr(s1, "\",\"rev\""), *r2 = strstr(s2, "\",\"rev\""); if (r1) *r1 = 0; if (r2) *r2 = 0; }
        CHECK(n2 > 0 && !strcmp(s1, s2), "state load gives the state back");
        CHECK(!strcmp(get(in, "s_bpm"), "97.5"), "the tempo comes back (%s)", gbuf);
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
    api->set_param(in, "s_clock", "1"); render(in, 3);
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
    }

    api->destroy_instance(in);
    in = api->create_instance("build/move/frodhes", NULL);
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
    in = api->create_instance("build/move/frodhes", NULL);
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
static int presets(const char *so)
{
    char b[32];
    void *in;
    int i;
    load(so);
    in = api->create_instance("build/move/frodhes", NULL);
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
                               "p_cp_sw", "p_ph_sw", "p_tw_depth", "p_fl_type", "p_fl_freq", "p_cmp_sw", "p_level", "s_bpm",
                               "s_arpmode", "s_chtype", "s_sustain", "p_mot_on", "p_spread", "s_trans" };
    static char st[4096], val[16];
    void *in;
    unsigned b, full = 0, peak = 0, i;
    load(so);
    in = api->create_instance("build/move/frodhes", NULL);
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

int main(int argc, char **argv)
{
    if (argc >= 3 && !strcmp(argv[1], "keys")) return keys(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "presets")) return presets(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "gatep")) return gatep(argv[2], argc > 3 ? (unsigned)atoi(argv[3]) : 10000u);
    if (argc >= 5 && !strcmp(argv[1], "eq")) return eq(argv[2], argv[3], argv[4]);
    if (argc >= 3 && !strcmp(argv[1], "api")) return api_tests(argv[2]);
    printf("usage: test_bridge eq DSP SCRIPT HOST.wav | api DSP | keys DSP | presets DSP | gatep DSP [N]\n");
    return 2;
}
