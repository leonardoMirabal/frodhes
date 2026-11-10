/* SPDX-License-Identifier: GPL-3.0-only */
/* A long stress run in a headless CLAP host that behaves like a DAW (macOS): N instances of the built Frodhes.clap in
 * one process, an audio thread that processes them at real time while the main thread plays, automates and edits:
 *
 *   clap_stress <Frodhes.clap> [--minutes M (30)] [--instances N (3)] [--seed S] [--fast] [--no-gui | --gui-only]
 *
 *   - notes and chords on every instance (CLAP note events and raw MIDI), always released
 *   - full chords: 20 to 28 keys at once (every one of the 24 voices, and steals past them), PRESET changes through
 *     the PRESET parameter
 *   - automation floods: 2000 parameter changes in one block on random app parameters; a few blocks later every
 *     touched parameter must read back the last value sent (no event dropped)
 *   - the window opened in a real NSWindow, shown, painted for a while, hidden and destroyed, over and over
 *   - state round trips: instance A saved, loaded into instance B, B saved again: the same bytes
 *   - every output sample finite and inside +-4 (no NaN, no blow-up); every instance that played is not silent
 *   - the process's resident and physical footprint sampled every 30 s: after a 3-minute warm-up it must not grow
 *     by more than 8 MB (a leak in the window's open/close, the state or the presets shows up there)
 * --fast: the audio thread runs as fast as it can instead of at real time (a quick smoke run). --no-gui leaves the
 * window out, --gui-only does nothing but open and close it (to tell where memory goes).
 * Prints a log line per minute and a summary; exit 0 when every check passed, 1 otherwise. */
#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <clap/clap.h>

#define MAXI 8
#define BLOCK 512
#define RATE 44100.0
#define QCAP 8192
#define ID_PRESET 0x10002u

static int fails;
static void fail(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void fail(const char *fmt, ...)
{
    va_list a;
    va_start(a, fmt);
    printf("FAIL  ");
    vprintf(fmt, a);
    printf("\n");
    va_end(a);
    fflush(stdout);
    fails++;
}

/* ---- one instance: the plugin, its pending input events (main thread -> audio thread) */
typedef union { clap_event_header_t h; clap_event_note_t n; clap_event_param_value_t p; clap_event_midi_t m; } ev_t;
typedef struct {
    clap_host_t host;
    const clap_plugin_t *pl;
    const clap_plugin_params_t *params;
    const clap_plugin_gui_t *gui;
    const clap_plugin_state_t *state;
    pthread_mutex_t mu;
    ev_t q[QCAP], blk[QCAP];
    uint32_t qn, bn;
    atomic_int cb_req, flush_req, rescans;
    double peak, peak_win;      /* (audio thread) the whole run's peak, the peak since the main thread last read it */
    long nonfinite, huge;
    long blocks;
    int gui_open;
    NSWindow *win;
    int warn;
} inst_t;
static inst_t I[MAXI];
static int NI = 3;
static pthread_t main_tid, audio_tid;
static atomic_int running = 1, audio_ready;
static int fast, no_gui, gui_only;

static inst_t *of(const clap_host_t *h) { return (inst_t *)h->host_data; }
static void h_log(const clap_host_t *h, clap_log_severity s, const char *m)
{
    if (s >= CLAP_LOG_WARNING) { of(h)->warn++; printf("log   [%d] %s\n", s, m); }
}
static const clap_host_log_t HLOG = { h_log };
static void h_rescan(const clap_host_t *h, clap_param_rescan_flags f) { (void)f; atomic_fetch_add(&of(h)->rescans, 1); }
static void h_clear(const clap_host_t *h, clap_id id, clap_param_clear_flags f) { (void)h; (void)id; (void)f; }
static void h_rflush(const clap_host_t *h) { atomic_store(&of(h)->flush_req, 1); }
static const clap_host_params_t HPARAMS = { h_rescan, h_clear, h_rflush };
static void hg_hints(const clap_host_t *h) { (void)h; }
static bool hg_resize(const clap_host_t *h, uint32_t w, uint32_t hh) { (void)h; (void)w; (void)hh; return false; }
static bool hg_show(const clap_host_t *h) { (void)h; return true; }
static void hg_closed(const clap_host_t *h, bool d) { (void)h; (void)d; }
static const clap_host_gui_t HGUI = { hg_hints, hg_resize, hg_show, hg_show, hg_closed };
static bool ht_main(const clap_host_t *h) { (void)h; return pthread_equal(pthread_self(), main_tid); }
static bool ht_audio(const clap_host_t *h) { (void)h; return atomic_load(&audio_ready) && pthread_equal(pthread_self(), audio_tid); }
static const clap_host_thread_check_t HTHREAD = { ht_main, ht_audio };
static void hs_dirty(const clap_host_t *h) { (void)h; }
static const clap_host_state_t HSTATE = { hs_dirty };
static const void *h_ext(const clap_host_t *h, const char *id)
{
    (void)h;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &HPARAMS;
    if (!strcmp(id, CLAP_EXT_LOG)) return &HLOG;
    if (!strcmp(id, CLAP_EXT_GUI)) return &HGUI;
    if (!strcmp(id, CLAP_EXT_THREAD_CHECK)) return &HTHREAD;
    if (!strcmp(id, CLAP_EXT_STATE)) return &HSTATE;
    return 0;
}
static void h_req(const clap_host_t *h) { (void)h; }
static void h_cb(const clap_host_t *h) { atomic_store(&of(h)->cb_req, 1); }

/* ---- events, queued by the main thread for the next block */
static void push(inst_t *in, const ev_t *e)
{
    pthread_mutex_lock(&in->mu);
    if (in->qn < QCAP) in->q[in->qn++] = *e;
    else fail("host queue full (test bug)");
    pthread_mutex_unlock(&in->mu);
}
static void note(inst_t *in, int on, int key, double vel, int midi)
{
    ev_t e;
    memset(&e, 0, sizeof e);
    if (midi) {
        e.m.header.size = sizeof e.m; e.m.header.type = CLAP_EVENT_MIDI;
        e.m.port_index = 0;
        e.m.data[0] = (uint8_t)(on ? 0x90 : 0x80); e.m.data[1] = (uint8_t)key; e.m.data[2] = (uint8_t)(on ? (int)(vel * 127) : 0);
    } else {
        e.n.header.size = sizeof e.n; e.n.header.type = on ? CLAP_EVENT_NOTE_ON : CLAP_EVENT_NOTE_OFF;
        e.n.note_id = -1; e.n.port_index = 0; e.n.channel = 0; e.n.key = (int16_t)key; e.n.velocity = vel;
    }
    e.h.space_id = CLAP_CORE_EVENT_SPACE_ID;
    push(in, &e);
}
static void param(inst_t *in, clap_id id, double v)
{
    ev_t e;
    memset(&e, 0, sizeof e);
    e.p.header.size = sizeof e.p; e.p.header.space_id = CLAP_CORE_EVENT_SPACE_ID; e.p.header.type = CLAP_EVENT_PARAM_VALUE;
    e.p.param_id = id; e.p.note_id = -1; e.p.port_index = -1; e.p.channel = -1; e.p.key = -1; e.p.value = v;
    push(in, &e);
}
static uint32_t ie_size(const clap_input_events_t *l) { return ((inst_t *)l->ctx)->bn; }
static const clap_event_header_t *ie_get(const clap_input_events_t *l, uint32_t i) { return &((inst_t *)l->ctx)->blk[i].h; }
static bool oe_push(const clap_output_events_t *l, const clap_event_header_t *e) { (void)l; (void)e; return true; }

/* ---- the audio thread: every instance, one block each, at real time (or as fast as it can with --fast) */
static void *audio_main(void *arg)
{
    static float L[BLOCK], R[BLOCK];
    float *ch[2] = {L, R};
    struct timespec t0;
    long n = 0;
    (void)arg;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    atomic_store(&audio_ready, 1);
    while (atomic_load(&running)) {
        for (int k = 0; k < NI; k++) {
            inst_t *in = &I[k];
            clap_audio_buffer_t ob = {0};
            clap_process_t pr = {0};
            clap_input_events_t iev = { in, ie_size, ie_get };
            clap_output_events_t oev = { in, oe_push };
            pthread_mutex_lock(&in->mu);
            memcpy(in->blk, in->q, in->qn * sizeof(ev_t));
            in->bn = in->qn; in->qn = 0;
            pthread_mutex_unlock(&in->mu);
            for (uint32_t j = 0; j < in->bn; j++) in->blk[j].h.time = (uint32_t)(j * BLOCK / (in->bn + 1));   /* sorted */
            ob.data32 = ch; ob.channel_count = 2;
            pr.steady_time = n * BLOCK; pr.frames_count = BLOCK; pr.audio_outputs = &ob; pr.audio_outputs_count = 1;
            pr.in_events = &iev; pr.out_events = &oev;
            in->pl->process(in->pl, &pr);
            in->bn = 0;
            for (int i = 0; i < BLOCK; i++) {
                double a = fabs(L[i]), b = fabs(R[i]), m = a > b ? a : b;
                if (!isfinite(L[i]) || !isfinite(R[i])) in->nonfinite++;
                else {
                    if (m > 4.0) in->huge++;
                    if (m > in->peak) in->peak = m;
                    if (m > in->peak_win) in->peak_win = m;
                }
            }
            in->blocks++;
        }
        n++;
        if (!fast) {   /* the block's deadline: n x 512 / 44100 s after the start */
            struct timespec now, d;
            double due = (double)n * BLOCK / RATE, el;
            clock_gettime(CLOCK_MONOTONIC, &now);
            el = (double)(now.tv_sec - t0.tv_sec) + (now.tv_nsec - t0.tv_nsec) * 1e-9;
            if (due > el) { d.tv_sec = (time_t)(due - el); d.tv_nsec = (long)((due - el - (double)d.tv_sec) * 1e9); nanosleep(&d, 0); }
        }
    }
    return 0;
}

/* ---- the main thread's helpers */
static void service(void)
{
    for (int k = 0; k < NI; k++) {
        inst_t *in = &I[k];
        if (atomic_exchange(&in->cb_req, 0)) in->pl->on_main_thread(in->pl);
        atomic_store(&in->flush_req, 0);   /* (processing: the next block flushes) */
    }
}
static void pump(double t)
{
    NSDate *end = [NSDate dateWithTimeIntervalSinceNow:t];
    do {
        @autoreleasepool {
            service();
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
        }
    } while ([end timeIntervalSinceNow] > 0);
}
static double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec * 1e-9;
}
static void mem(double *rss_mb, double *foot_mb)
{
    task_vm_info_data_t vi;
    mach_msg_type_number_t n = TASK_VM_INFO_COUNT;
    *rss_mb = *foot_mb = -1;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vi, &n) == KERN_SUCCESS) {
        *rss_mb = (double)vi.resident_size / 1048576.0;
        *foot_mb = (double)vi.phys_footprint / 1048576.0;
    }
}
static double pval(inst_t *in, clap_id id)
{
    double v = -1e9;
    in->params->get_value(in->pl, id, &v);
    return v;
}

/* the app's parameters a flood may touch: stepped, not the module selectors that reload others */
typedef struct { clap_id id; double lo, hi; } pinfo_t;
static pinfo_t P[256];
static int NP;

/* parameters the app itself moves when another one changes, as the FM-1 does (flooded, not read back): an effect's
 * depth turned up while it is off switches it on (ui.c: the type last on), an effect switched on at depth 0 gets a
 * starting depth (fx_wake), and the compressor's THRESH is its switch (cmp_wake: COMP on, THRESH at least -16 dB, the
 * engine's make-up when none is set). The pairs: drive, tremolo / wah, chorus, delay, reverb, phaser, compressor */
static int coupled(clap_id id)
{
    static const clap_id C[] = { 1, 50, 2, 3, 8, 9, 13, 14, 19, 59, 28, 29, 33, 34, 39 };   /* params_map.h ids */
    for (unsigned i = 0; i < sizeof C / sizeof C[0]; i++) if (C[i] == id) return 1;
    return 0;
}

/* memory streams for the state */
typedef struct { char b[1 << 16]; int64_t n, r; } mstream;
static int64_t ms_write(const clap_ostream_t *s, const void *p, uint64_t n)
{
    mstream *m = s->ctx;
    if (m->n + (int64_t)n > (int64_t)sizeof m->b) return -1;
    memcpy(m->b + m->n, p, n); m->n += (int64_t)n;
    return (int64_t)n;
}
static int64_t ms_read(const clap_istream_t *s, void *p, uint64_t n)
{
    mstream *m = s->ctx;
    int64_t k = m->n - m->r;
    if (k > (int64_t)n) k = (int64_t)n;
    if (k > 1000) k = 1000;   /* short reads, as some hosts do */
    memcpy(p, m->b + m->r, (size_t)k); m->r += k;
    return k;
}
static int save(inst_t *in, mstream *m)
{
    clap_ostream_t o = { m, ms_write };
    m->n = m->r = 0;
    return in->state->save(in->pl, &o);
}
static int load(inst_t *in, mstream *m)
{
    clap_istream_t s = { m, ms_read };
    m->r = 0;
    return in->state->load(in->pl, &s);
}

static void gui_open(inst_t *in, double scale)
{
    uint32_t w = 0, h = 0;
    if (!in->gui->create(in->pl, CLAP_WINDOW_API_COCOA, false)) { fail("gui create"); return; }
    in->gui->set_scale(in->pl, scale);
    if (!in->gui->get_size(in->pl, &w, &h) || !w || !h) fail("gui get_size");
    in->win = [[NSWindow alloc] initWithContentRect:NSMakeRect(60 + 40 * (in - I), 60 + 30 * (in - I), w, h)
                                          styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskResizable
                                            backing:NSBackingStoreBuffered
                                              defer:NO];
    [in->win setReleasedWhenClosed:NO];
    [in->win setTitle:[NSString stringWithFormat:@"Frodhes stress %ld", (long)(in - I)]];
    clap_window_t cw = {CLAP_WINDOW_API_COCOA, {.cocoa = (__bridge void *)[in->win contentView]}};
    if (!in->gui->set_parent(in->pl, &cw)) fail("gui set_parent");
    if (!in->gui->show(in->pl)) fail("gui show");
    [in->win orderFrontRegardless];
    in->gui_open = 1;
}
static void gui_close(inst_t *in)
{
    if (!in->gui_open) return;
    in->gui->hide(in->pl);
    in->gui->destroy(in->pl);
    [in->win orderOut:nil];
    [in->win close];
    in->win = nil;
    in->gui_open = 0;
}

int main(int argc, char **argv)
{
    char exe[1024];
    void *lib;
    const clap_plugin_entry_t *entry;
    const clap_plugin_factory_t *fac;
    const char *pid;
    double minutes = 30;
    unsigned seed = 1;
    long n_notes = 0, n_full = 0, n_pre = 0, n_flood = 0, n_flood_ev = 0, n_drop = 0, n_gui = 0, n_state = 0;
    double rss0 = -1, foot0 = -1, rss_max = 0, foot_max = 0, t_start, t_end, t_next_log, t_warm;
    int held[MAXI][32] = {{0}}, nheld[MAXI] = {0};
    static mstream sa, sb;
    if (argc < 2) { fprintf(stderr, "usage: clap_stress <Frodhes.clap> [--minutes M] [--instances N] [--seed S] [--fast] [--no-gui | --gui-only]\n"); return 2; }
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--minutes") && i + 1 < argc) minutes = atof(argv[++i]);
        else if (!strcmp(argv[i], "--instances") && i + 1 < argc) NI = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--fast")) fast = 1;
        else if (!strcmp(argv[i], "--no-gui")) no_gui = 1;
        else if (!strcmp(argv[i], "--gui-only")) gui_only = 1;
    }
    if (NI < 1 || NI > MAXI) NI = 3;
    srandom(seed);
    main_tid = pthread_self();
    snprintf(exe, sizeof exe, "%s/Contents/MacOS/Frodhes", argv[1]);
    if (!(lib = dlopen(exe, RTLD_LOCAL | RTLD_NOW))) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    entry = dlsym(lib, "clap_entry");
    if (!entry || !entry->init(argv[1])) return 1;
    fac = entry->get_factory(CLAP_PLUGIN_FACTORY_ID);
    pid = fac->get_plugin_descriptor(fac, 0)->id;

    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [NSApp finishLaunching];

    for (int k = 0; k < NI; k++) {
        inst_t *in = &I[k];
        clap_host_t h = { CLAP_VERSION_INIT, in, "frodhes clap_stress", "Frodhes", "", "1", h_ext, h_req, h_req, h_cb };
        in->host = h;
        pthread_mutex_init(&in->mu, 0);
        in->pl = fac->create_plugin(fac, &in->host, pid);
        if (!in->pl || !in->pl->init(in->pl)) { fail("instance %d: create / init", k); return 1; }
        in->params = in->pl->get_extension(in->pl, CLAP_EXT_PARAMS);
        in->gui = in->pl->get_extension(in->pl, CLAP_EXT_GUI);
        in->state = in->pl->get_extension(in->pl, CLAP_EXT_STATE);
        if (!in->params || !in->gui || !in->state) { fail("instance %d: params / gui / state extensions", k); return 1; }
        if (!in->pl->activate(in->pl, RATE, 1, BLOCK)) { fail("instance %d: activate", k); return 1; }
    }
    /* the flood's parameters: the app's own (ids under 0x10000), skipping the module TYPE selectors (their names end in
     * TYPE / "SW": a type change sets the module's other knobs, which is not an event lost) */
    for (uint32_t i = 0, n = I[0].params->count(I[0].pl); i < n && NP < 256; i++) {
        clap_param_info_t inf;
        if (!I[0].params->get_info(I[0].pl, i, &inf) || inf.id >= 0x10000u) continue;
        if (strstr(inf.name, "TYPE") || strstr(inf.name, "Type") || (inf.flags & CLAP_PARAM_IS_READONLY)) continue;
        P[NP].id = inf.id; P[NP].lo = inf.min_value; P[NP].hi = inf.max_value; NP++;
    }
    printf("info  %d instances, %d flood parameters, %.1f minutes%s, seed %u\n", NI, NP, minutes, fast ? " (fast)" : "", seed);
    for (int k = 0; k < NI; k++) I[k].pl->start_processing(I[k].pl);   /* (CLAP: on the audio thread; here before it runs) */
    pthread_create(&audio_tid, 0, audio_main, 0);

    t_start = now_s(); t_end = t_start + minutes * 60.0; t_next_log = t_start + 60.0;
    t_warm = t_start + (minutes >= 10 ? 180.0 : minutes * 60.0 * 0.3);
    while (now_s() < t_end) @autoreleasepool {
        inst_t *in = &I[random() % NI];
        int k = (int)(in - I);
        int act = (int)(random() % 100);
        if (gui_only) act = 76 + (int)(random() % 12);
        else if (no_gui && act >= 76 && act < 88) act = (int)(random() % 76);
        /* release what this instance holds, then maybe a new chord */
        for (int j = 0; j < nheld[k]; j++) note(in, 0, held[k][j], 0.0, j & 1);
        nheld[k] = 0;
        if (act < 40) {
            int root = 36 + (int)(random() % 48), nn = 1 + (int)(random() % 6);
            for (int j = 0; j < nn; j++) { held[k][j] = root + 3 * j + (int)(random() % 2); note(in, 1, held[k][j], 0.2 + 0.8 * (random() % 100) / 100.0, j & 1); }
            nheld[k] = nn; n_notes += nn;
        } else if (act < 52) {
            int nn = 20 + (int)(random() % 9);   /* a full chord: 20..28 keys (24 voices: steals past them) */
            for (int j = 0; j < nn; j++) { held[k][j] = 33 + 2 * j + (int)(random() % 2); note(in, 1, held[k][j], 0.3 + 0.7 * (random() % 100) / 100.0, j & 1); }
            nheld[k] = nn; n_notes += nn; n_full++;
        } else if (act < 64) {
            param(in, ID_PRESET, (double)(random() % 57)); n_pre++;
        } else if (act < 76) {   /* the flood, then the read-back */
            static double last[256];
            static int touched[256];
            int drops = 0;
            memset(touched, 0, sizeof touched);
            for (int j = 0; j < 2000; j++) {
                int pi = (int)(random() % NP);
                double v = P[pi].lo + (double)(random() % (long)(P[pi].hi - P[pi].lo + 1));
                param(in, P[pi].id, v); last[pi] = v; touched[pi] = 1;
            }
            n_flood++; n_flood_ev += 2000;
            pump(0.4);
            for (int pi = 0; pi < NP; pi++)
                if (touched[pi] && !coupled(P[pi].id) && pval(in, P[pi].id) != last[pi]) {
                    if (drops++ < 5) fail("flood on instance %d: param %u reads %.0f, last sent %.0f", k, P[pi].id, pval(in, P[pi].id), last[pi]);
                }
            n_drop += drops;
        } else if (act < 88) {   /* the window: open, paint for a while, close */
            gui_open(in, (random() & 1) ? 2.0 : 1.0);
            note(in, 1, 64, 0.7, 0); held[k][0] = 64; nheld[k] = 1;
            pump(0.3 + (random() % 100) / 100.0);
            gui_close(in);
            n_gui++;
        } else {                 /* the state: A -> B -> B saved again */
            inst_t *b = &I[(k + 1) % NI];
            if (NI > 1) {
                pump(0.2);
                if (!save(in, &sa)) fail("state save, instance %d", k);
                else if (!load(b, &sa)) fail("state load into instance %d", (int)(b - I));
                else {
                    pump(0.3);   /* (the load applies on b's next audio step) */
                    if (!save(b, &sb)) fail("state save, instance %d", (int)(b - I));
                    else if (sa.n != sb.n || memcmp(sa.b, sb.b, (size_t)sa.n)) {
                        sa.b[sa.n < (int64_t)sizeof sa.b ? sa.n : 0] = 0;
                        fail("state round trip %d -> %d: %lld bytes, then %lld (differs)", k, (int)(b - I), (long long)sa.n, (long long)sb.n);
                    }
                    n_state++;
                }
            }
        }
        pump(0.15 + (random() % 30) / 100.0);
        if (now_s() >= t_next_log || now_s() >= t_end) {
            double rss, foot;
            mem(&rss, &foot);
            if (now_s() >= t_warm && rss0 < 0) { rss0 = rss; foot0 = foot; }
            if (rss > rss_max) rss_max = rss;
            if (foot > foot_max) foot_max = foot;
            printf("min %5.1f  rss %.1f MB  footprint %.1f MB  notes %ld full %ld preset %ld flood %ld gui %ld state %ld  peaks",
                   (now_s() - t_start) / 60.0, rss, foot, n_notes, n_full, n_pre, n_flood, n_gui, n_state);
            for (int j = 0; j < NI; j++) { printf(" %.3f", I[j].peak_win); I[j].peak_win = 0; }
            printf("\n");
            fflush(stdout);
            t_next_log += 60.0;
        }
    }
    for (int k = 0; k < NI; k++) { for (int j = 0; j < nheld[k]; j++) note(&I[k], 0, held[k][j], 0.0, 0); }
    pump(0.5);
    atomic_store(&running, 0);
    pthread_join(audio_tid, 0);
    {
        double rss, foot;
        mem(&rss, &foot);
        printf("info  memory: after the warm-up rss %.1f / footprint %.1f MB, at the end %.1f / %.1f MB, max %.1f / %.1f MB\n",
               rss0, foot0, rss, foot, rss_max, foot_max);
        if (rss0 > 0 && foot - foot0 > 8.0) fail("the footprint grew %.1f MB after the warm-up", foot - foot0);
        if (rss0 > 0 && rss - rss0 > 8.0) fail("the resident size grew %.1f MB after the warm-up", rss - rss0);
    }
    for (int k = 0; k < NI; k++) {
        inst_t *in = &I[k];
        printf("info  instance %d: %ld blocks, peak %.3f, non-finite %ld, over 4.0 %ld, warnings %d\n", k, in->blocks, in->peak, in->nonfinite, in->huge, in->warn);
        if (in->nonfinite) fail("instance %d: %ld non-finite samples", k, in->nonfinite);
        if (in->huge) fail("instance %d: %ld samples over 4.0", k, in->huge);
        if (in->peak < 0.01) fail("instance %d: silent (peak %.4f)", k, in->peak);
        if (in->warn) fail("instance %d: %d warnings in the log", k, in->warn);
        in->pl->stop_processing(in->pl);
        in->pl->deactivate(in->pl);
        in->pl->destroy(in->pl);
    }
    entry->deinit();
    printf("info  %ld notes, %ld full chords, %ld preset changes, %ld floods (%ld events, %ld dropped), %ld window open/close, %ld state round trips\n",
           n_notes, n_full, n_pre, n_flood, n_flood_ev, n_drop, n_gui, n_state);
    printf("%s  clap_stress: %d failure%s\n", fails ? "FAIL" : "ok   ", fails, fails == 1 ? "" : "s");
    return fails ? 1 : 0;
}
