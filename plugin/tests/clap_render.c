/* SPDX-License-Identifier: GPL-3.0-only */
/* A headless CLAP host for the plugin's tests (Gate D, make test-plugin). Only the CLAP ABI: no Frodhes code here.
 *
 *   clap_render PLUGIN SCRIPT OUT.wav [options]
 *       a host script's MIDI (tests/host.c format: "T midi ST D1 D2", "T end"; expect / shot / dump lines skipped)
 *       played through the plugin, written as the host build writes its WAV (16-bit stereo, the clip at +-32767). The
 *       events go in at the host frame whose millisecond is the script's (44.1 kHz: frame ceil(T x 44.1); 48 kHz:
 *       48 T; another rate: frame ceil(T x rate / 1000)), as "due" in move/tests/move_replay.c: an event listed after
 *       a later one plays at the later time. The output's first frames (the latency the plugin reports) are not
 *       written. With FRODHES_PLUGIN_TEST=ms in the environment (plugin/src/bridge.c: the host build's time) the rest
 *       is the host build's render of the script at 44.1 kHz (Gate D compares the sha256), at other rates through
 *       the plugin's oversampler; with FRODHES_PLUGIN_TEST=fm1 at 48 kHz the FM-1's USB resampler over it (the
 *       480-frame pre-roll skipped: the resampler's own delay is in the reference too).
 *     --rate R                (44100; another rate from 1000 to 1536000)
 *     --offset N              every event N host frames later (tools/plugin_timing.py)
 *     --engine hq             accepted (the plugin is HQ only: 24 voices on every engine); any other value refused
 *     --block N               frames a process call (512); --block random:SEED for random sizes 0..2048
 *     --dialect midi|clap     notes as raw MIDI events (midi) or CLAP note events (clap); CC and the rest stay MIDI
 *     --tempo BPM             the host transport's tempo (none by default: the plugin plays 120)
 *     --tail-check finite|infinite
 *                             a host that stops at the tail the plugin reports: after the script's last event the
 *                             tail is read (and must be finite / infinite); a finite one is checked by rendering 20 s
 *                             past it: nothing may sound after it (16-bit). No WAV is written (OUT.wav is ignored)
 *     --bench 1               print the time process() took (in all and the worst call) against the audio's length
 *     --host-vendor wrapper   the host says it is clap-wrapper (the VST3 / Audio Unit): the tail's change is told from
 *                             the main thread, not in process
 *   Plugin-only script lines (plugin/tests/scripts): "T param ID VALUE", a CLAP parameter event at T (ID decimal or
 *   0x..., the value 0..hi).
 *   clap_render PLUGIN --selftest
 *       the parameters (every value's text back to the same value), the state (saved, loaded into a second instance,
 *       saved again: the same), the rates (500 Hz refused, 96 kHz taken), FRP_SLOTS + 1 instances (the last
 *       one silent, with its warning), the latency, the descriptor and the GUI extension (Linux: with
 *       the posix-fd and timer support its X11 view runs on).
 *   clap_render PLUGIN --coretest
 *       the plugin core through its UI extension (plugin/frp_ui_iface.h FRP_UI_EXT_ID): 24 voices on every engine
 *       (24 keys held all sound, the 25th steals one), the state (every setting, MIDI learn, the preset, robust to older and broken
 *       states), presets (factory, user files in $FRODHES_PRESET_DIR, browse, UNDO, program changes), automation
 *       (host writes not echoed, the app's and the UI's reported, gestures), MIDI learn, the tempo sources and SYNC
 *       ALL, SUSTAIN and FX BYPASS, the velocity curves, MIDI CHANNEL, the on-screen keyboard, the meters.
 * Exit: 0 ok, 1 failure, 3 the script is not MIDI-driven (nothing written). */
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <clap/clap.h>
#include "../frp_ui_iface.h"

#define LAT_MS 10u                       /* plugin/src/bridge.h FRP_LAT_MS: the fm1 test mode's pre-roll */
#define HOST_BLOCK 256u                  /* tests/host.c HALF_FRAMES */
#define END_DEFAULT 6500u                /* tests/host.c: no "end" line */
#define MAXEV 200000

/* ---- the host side */
static int warnings, late_warnings, slot_warnings, full_warnings;
static void h_log(const clap_host_t *h, clap_log_severity s, const char *msg)
{
    (void)h;
    if (s >= CLAP_LOG_WARNING) { warnings++; fprintf(stderr, "plugin log: %s\n", msg); }
    if (strstr(msg, "late")) late_warnings++;
    if (strstr(msg, "16 instances")) slot_warnings++;
    if (strstr(msg, "did not fit") && !strstr(msg, " 0 did not fit")) full_warnings++;
}
static const clap_host_log_t H_LOG = { h_log };
static int rescans;
static void h_rescan(const clap_host_t *h, clap_param_rescan_flags f) { (void)h; (void)f; rescans++; }
static void h_clear(const clap_host_t *h, clap_id id, clap_param_clear_flags f) { (void)h; (void)id; (void)f; }
static void h_flush(const clap_host_t *h) { (void)h; }
static const clap_host_params_t H_PARAMS = { h_rescan, h_clear, h_flush };
static int callbacks;
/* the tail's changed() is an [audio-thread] call (in process); as clap-wrapper (--host-vendor wrapper) the plugin
   calls it from the main thread instead, for the VST3's and the Audio Unit's main-thread calls behind it */
static int tail_changes, tail_off_thread, in_process, as_wrapper;
static void h_tail_changed(const clap_host_t *h) { (void)h; tail_changes++; if (in_process == as_wrapper) tail_off_thread++; }
static const clap_host_tail_t H_TAIL = { h_tail_changed };
static int lat_changes;                  /* the plugin's latency.changed() calls (in activate) */
static void h_lat_changed(const clap_host_t *h) { (void)h; lat_changes++; }
static const clap_host_latency_t H_LAT = { h_lat_changed };
static const void *h_ext(const clap_host_t *h, const char *id)
{
    (void)h;
    if (!strcmp(id, CLAP_EXT_TAIL)) return &H_TAIL;
    if (!strcmp(id, CLAP_EXT_LATENCY)) return &H_LAT;
    if (!strcmp(id, CLAP_EXT_LOG)) return &H_LOG;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &H_PARAMS;
    return 0;
}
static void h_req(const clap_host_t *h) { (void)h; }
static void h_cb(const clap_host_t *h) { (void)h; callbacks++; }
static clap_host_t HOST = { CLAP_VERSION_INIT, 0, "frodhes clap_render", "Frodhes tests", "", "1",
                                  h_ext, h_req, h_req, h_cb };

/* the input events of one process call */
typedef struct { union { clap_event_header_t h; clap_event_midi_t m; clap_event_note_t n; clap_event_param_value_t p; } u; } ev_t;
static ev_t blk_ev[4096];
static uint32_t blk_n;
static uint32_t in_size(const clap_input_events_t *l) { (void)l; return blk_n; }
static const clap_event_header_t *in_get(const clap_input_events_t *l, uint32_t i) { (void)l; return &blk_ev[i].u.h; }
static const clap_input_events_t IN_EVENTS = { 0, in_size, in_get };
static uint32_t out_pushed;
typedef struct { uint16_t type; uint32_t id; double v; } outev_t;
static outev_t outs[4096];
static uint32_t n_outs;
static bool out_push(const clap_output_events_t *l, const clap_event_header_t *e)
{
    (void)l;
    out_pushed++;
    if (n_outs < 4096u && e->space_id == CLAP_CORE_EVENT_SPACE_ID) {
        outs[n_outs].type = e->type;
        if (e->type == CLAP_EVENT_PARAM_VALUE) { outs[n_outs].id = ((const clap_event_param_value_t *)e)->param_id; outs[n_outs].v = ((const clap_event_param_value_t *)e)->value; }
        else if (e->type == CLAP_EVENT_PARAM_GESTURE_BEGIN || e->type == CLAP_EVENT_PARAM_GESTURE_END) outs[n_outs].id = ((const clap_event_param_gesture_t *)e)->param_id;
        n_outs++;
    }
    return true;
}
static const clap_output_events_t OUT_EVENTS = { 0, out_push };

static const clap_plugin_entry_t *entry;
static const clap_plugin_factory_t *factory;

static int load(const char *path)
{
    char bin[1024];
    struct stat st;
    void *h;
    const char *base = strrchr(path, '/');
    snprintf(bin, sizeof bin, "%s", path);
    if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {   /* a macOS bundle: Contents/MacOS/<name without .clap> */
        char name[256];
        snprintf(name, sizeof name, "%s", base ? base + 1 : path);
        if (strrchr(name, '.')) *strrchr(name, '.') = 0;
        snprintf(bin, sizeof bin, "%s/Contents/MacOS/%s", path, name);
    }
    if (!(h = dlopen(bin, RTLD_NOW | RTLD_LOCAL))) { printf("FAIL dlopen %s: %s\n", bin, dlerror()); return -1; }
    if (!(entry = dlsym(h, "clap_entry"))) { printf("FAIL no clap_entry\n"); return -1; }
    if (!entry->init(path)) { printf("FAIL clap_entry.init\n"); return -1; }
    if (!(factory = entry->get_factory(CLAP_PLUGIN_FACTORY_ID))) { printf("FAIL no plugin factory\n"); return -1; }
    return 0;
}
static const clap_plugin_t *create(void)
{
    const clap_plugin_descriptor_t *d = factory->get_plugin_descriptor(factory, 0);
    const clap_plugin_t *p = d ? factory->create_plugin(factory, &HOST, d->id) : 0;
    if (!p || !p->init(p)) { printf("FAIL create / init\n"); return 0; }
    return p;
}
static void set_param_inactive(const clap_plugin_t *p, clap_id id, double v)
{
    const clap_plugin_params_t *pp = p->get_extension(p, CLAP_EXT_PARAMS);
    memset(&blk_ev[0], 0, sizeof blk_ev[0]);
    blk_ev[0].u.p.header.size = sizeof(clap_event_param_value_t);
    blk_ev[0].u.p.header.type = CLAP_EVENT_PARAM_VALUE;
    blk_ev[0].u.p.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
    blk_ev[0].u.p.param_id = id;
    blk_ev[0].u.p.value = v;
    blk_ev[0].u.p.note_id = -1; blk_ev[0].u.p.port_index = -1; blk_ev[0].u.p.channel = -1; blk_ev[0].u.p.key = -1;
    blk_n = 1;
    pp->flush(p, &IN_EVENTS, &OUT_EVENTS);
    blk_n = 0;
}

/* process: n frames into l / r, with the events blk_ev[0..blk_n) already set */
static double transport_bpm;             /* 0: no transport */
static int process(const clap_plugin_t *p, float *l, float *r, uint32_t n, int64_t steady)
{
    float *ch[2] = { l, r };
    clap_audio_buffer_t out = { 0 };
    clap_process_t pr = { 0 };
    clap_event_transport_t tr = { 0 };
    clap_process_status st;
    if (transport_bpm > 0.0) {
        tr.header.size = sizeof tr; tr.header.type = CLAP_EVENT_TRANSPORT; tr.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        tr.flags = CLAP_TRANSPORT_HAS_TEMPO | CLAP_TRANSPORT_IS_PLAYING;
        tr.tempo = transport_bpm;
        pr.transport = &tr;
    }
    out.data32 = ch; out.channel_count = 2;
    pr.steady_time = steady; pr.frames_count = n;
    pr.audio_outputs = &out; pr.audio_outputs_count = 1;
    pr.in_events = &IN_EVENTS; pr.out_events = &OUT_EVENTS;
    in_process = 1;
    st = p->process(p, &pr);
    in_process = 0;
    return st == CLAP_PROCESS_ERROR ? -1 : 0;
}

/* ---- the script */
typedef struct { uint64_t h; uint8_t st, d1, d2; uint32_t pid; double pv; } sev_t;   /* st 0: a parameter event */
static sev_t sev[MAXEV];
static uint32_t nsev;
static uint32_t latency_of(const clap_plugin_t *p)
{
    const clap_plugin_latency_t *pl = p->get_extension(p, CLAP_EXT_LATENCY);
    return pl ? pl->get(p) : 0u;
}
static int read_script(const char *path, double rate, unsigned *end_ms)
{
    char line[256], what[16];
    unsigned due = 0;
    FILE *f = fopen(path, "r");
    *end_ms = END_DEFAULT;
    if (!f) { printf("FAIL no script %s\n", path); return 1; }
    while (fgets(line, sizeof line, f)) {
        unsigned t, st, d1, d2;
        char sub[16] = "";
        if (line[0] == '#' || sscanf(line, "%u %15s", &t, what) != 2) continue;
        if (!strcmp(what, "end")) { *end_ms = t; continue; }
        if (!strcmp(what, "shot") || !strcmp(what, "dump")) continue;
        if (!strcmp(what, "expect")) {   /* host.c holds the next events back behind an expect it knows */
            if (sscanf(line, "%*u %*s %15s", sub) == 1 && (!strcmp(sub, "preset") || !strcmp(sub, "sound") ||
                !strcmp(sub, "state") || !strcmp(sub, "led") || !strcmp(sub, "rms")) && t > due) due = t;
            continue;
        }
        if (!strcmp(what, "param")) {    /* (plugin-only scripts) */
            unsigned id;
            double v;
            if (sscanf(line, "%*u %*s %i %lf", &id, &v) != 2) { printf("FAIL bad param line: %s", line); fclose(f); return 1; }
            st = 0; d1 = d2 = 0;
            sev[nsev].pid = id; sev[nsev].pv = v;
        } else if (strcmp(what, "midi")) { printf("host-only %s: \"%s\" lines\n", path, what); fclose(f); return 3; }
        else if (sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) { printf("FAIL bad MIDI line: %s", line); fclose(f); return 1; }
        if (t > due) due = t;
        if (nsev >= MAXEV) { printf("FAIL too many events\n"); fclose(f); return 1; }
        sev[nsev].h = rate == 48000.0 ? 48ull * due : rate == 44100.0 ? (441ull * due + 9u) / 10u   /* ceil(44.1 T) */
                                                      : (uint64_t)ceil(due * rate / 1000.0 - 1e-9);
        sev[nsev].st = (uint8_t)st; sev[nsev].d1 = (uint8_t)d1; sev[nsev].d2 = (uint8_t)d2;
        nsev++;
    }
    fclose(f);
    return 0;
}
static void put_event(const sev_t *e, uint32_t time, int clap_notes)
{
    ev_t *b = &blk_ev[blk_n++];
    uint8_t t = e->st & 0xF0u;
    memset(b, 0, sizeof *b);
    if (e->st == 0) {                    /* a parameter event */
        b->u.p.header.size = sizeof(clap_event_param_value_t);
        b->u.p.header.type = CLAP_EVENT_PARAM_VALUE;
        b->u.p.param_id = e->pid; b->u.p.value = e->pv;
        b->u.p.note_id = -1; b->u.p.port_index = -1; b->u.p.channel = -1; b->u.p.key = -1;
    } else if (clap_notes && (t == 0x90u || t == 0x80u)) {
        int off = t == 0x80u || e->d2 == 0;
        b->u.n.header.size = sizeof(clap_event_note_t);
        b->u.n.header.type = off ? CLAP_EVENT_NOTE_OFF : CLAP_EVENT_NOTE_ON;
        b->u.n.note_id = -1; b->u.n.port_index = 0; b->u.n.channel = e->st & 15; b->u.n.key = e->d1;
        b->u.n.velocity = (t == 0x80u ? e->d2 : off ? 0 : e->d2) / 127.0;
    } else {
        b->u.m.header.size = sizeof(clap_event_midi_t);
        b->u.m.header.type = CLAP_EVENT_MIDI;
        b->u.m.port_index = 0;
        b->u.m.data[0] = e->st; b->u.m.data[1] = e->d1; b->u.m.data[2] = e->d2;
    }
    b->u.h.space_id = CLAP_CORE_EVENT_SPACE_ID;
    b->u.h.time = time;
}
static uint32_t rnd_state = 1;
static uint32_t rnd(void) { rnd_state ^= rnd_state << 13; rnd_state ^= rnd_state >> 17; rnd_state ^= rnd_state << 5; return rnd_state; }
static void put32(FILE *f, uint32_t v) { fwrite(&v, 4, 1, f); }

/* render the loaded script: pre + frames output frames; the frames after the pre-roll as int16 into pcm */
/* --bench: the time spent in process() (the plugin's own work), in all and the worst call */
static int bench;
static double bench_s, bench_worst_s;
static uint32_t bench_worst_n;
/* C11's timespec_get: macOS, glibc and the Windows C runtime alike (clock_gettime is not in the last) */
static double now_s(void) { struct timespec t; timespec_get(&t, TIME_UTC); return t.tv_sec + t.tv_nsec * 1e-9; }
static int render(const clap_plugin_t *p, int16_t *pcm, uint32_t pre, uint32_t frames, int block, int clap_notes,
                  uint32_t *clips)
{
    static float l[4096], r[4096];
    double t0;
    uint64_t pos = 0, total = (uint64_t)pre + frames;
    uint32_t k = 0;
    while (pos < total) {
        uint32_t n = block > 0 ? (uint32_t)block : rnd() % 2049u, i;
        if (n > 4096u) n = 4096u;
        if (pos + n > total) n = (uint32_t)(total - pos);
        blk_n = 0;
        while (k < nsev && sev[k].h < pos + n && blk_n < 4096u) { put_event(&sev[k], (uint32_t)(sev[k].h - pos), clap_notes); k++; }
        t0 = bench ? now_s() : 0.0;
        if (process(p, l, r, n, (int64_t)pos)) { printf("FAIL process\n"); return -1; }
        if (bench) {
            double dt = now_s() - t0;
            bench_s += dt;
            if (dt > bench_worst_s) { bench_worst_s = dt; bench_worst_n = n; }
        }
        for (i = 0; i < n; i++) {
            uint64_t g = pos + i;
            if (g >= pre) {
                double a = l[i] * 32768.0, b = r[i] * 32768.0;
                long x = lrint(a), y = lrint(b);
                if (x > 32767 || x < -32767 || y > 32767 || y < -32767) (*clips)++;
                pcm[2u * (g - pre)] = (int16_t)(x > 32767 ? 32767 : x < -32767 ? -32767 : x);
                pcm[2u * (g - pre) + 1u] = (int16_t)(y > 32767 ? 32767 : y < -32767 ? -32767 : y);
            }
        }
        pos += n;
    }
    blk_n = 0;
    return k == nsev ? 0 : -1;
}

static int selftest(void);
static int coretest(void);

/* --tail-check: render past the last event, read the tail, render 20 s past it; the last sound must lie inside it */
static int tail_check(const clap_plugin_t *p, double rate, int block, int clap_notes, int want_inf)
{
    static float l[4096], r[4096];
    const clap_plugin_tail_t *pt = p->get_extension(p, CLAP_EXT_TAIL);
    uint64_t last_ev = 0, pos = 0, stop, last_snd = 0, tail;
    uint32_t k = 0;
    int got_tail = 0;
    if (!pt) { printf("FAIL no tail extension\n"); return 1; }
    for (uint32_t i = 0; i < nsev; i++) if (sev[i].h > last_ev) last_ev = sev[i].h;
    stop = last_ev + 8192u;              /* (first: every event in, a few blocks run) */
    while (pos < stop) {
        uint32_t n = block > 0 ? (uint32_t)block : rnd() % 2049u, i;
        if (n > 4096u) n = 4096u;
        if (!n) continue;
        if (pos + n > stop) n = (uint32_t)(stop - pos);
        blk_n = 0;
        while (k < nsev && sev[k].h < pos + n && blk_n < 4096u) { put_event(&sev[k], (uint32_t)(sev[k].h - pos), clap_notes); k++; }
        if (process(p, l, r, n, (int64_t)pos)) { printf("FAIL process\n"); return 1; }
        blk_n = 0;
        for (i = 0; i < n; i++)
            if (lrint(l[i] * 32768.0) || lrint(r[i] * 32768.0)) last_snd = pos + i;
        pos += n;
        if (callbacks) { callbacks = 0; p->on_main_thread(p); }
        if (!got_tail && pos >= last_ev + 4096u) {
            uint32_t t = pt->get(p);
            got_tail = 1;
            if (t >= 0x7FFFFFFFu) {
                printf("%s tail infinite (expected %s)\n", want_inf ? "ok   " : "FAIL ", want_inf ? "infinite" : "finite");
                return want_inf ? 0 : 1;
            }
            if (want_inf) { printf("FAIL tail %u frames (expected infinite)\n", t); return 1; }
            tail = t;
            stop = last_ev + tail + (uint64_t)(20.0 * rate);
        }
    }
    if (tail_off_thread) {
        printf("FAIL the tail's changed() called %s process %d times\n", as_wrapper ? "in" : "outside", tail_off_thread);
        return 1;
    }
    printf("%s tail %.0f ms after the last event; the last sound %.0f ms after it (%.0f ms to spare), %d tail changes told\n",
           last_snd <= last_ev + tail ? "ok   " : "FAIL ", tail * 1000.0 / rate, ((double)last_snd - (double)last_ev) * 1000.0 / rate,
           ((double)(last_ev + tail) - (double)last_snd) * 1000.0 / rate, tail_changes);
    return last_snd <= last_ev + tail ? 0 : 1;
}

int main(int argc, char **argv)
{
    double rate = 44100.0;
    const char *tmode = getenv("FRODHES_PLUGIN_TEST");
    int block = 512, clap_notes = 0, rc, i, tailck = -1;
    uint64_t offset = 0;
    unsigned end;
    uint32_t frames, pre, clips = 0;
    int16_t *pcm;
    const clap_plugin_t *p;
    FILE *w;
    if (argc >= 3 && !strcmp(argv[2], "--selftest")) { if (load(argv[1])) return 1; return selftest(); }
    if (argc >= 3 && !strcmp(argv[2], "--coretest")) { if (load(argv[1])) return 1; return coretest(); }
    if (argc < 4) { fprintf(stderr, "usage: clap_render PLUGIN SCRIPT OUT.wav [--rate R] [--engine hq] "
                                    "[--block N|random:SEED] [--dialect midi|clap]\n       clap_render PLUGIN --selftest\n"); return 1; }
    for (i = 4; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--rate")) rate = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--engine")) {   /* (hq, the only sound: kept for the scripts that name it) */
            if (strcmp(argv[i + 1], "hq")) { fprintf(stderr, "clap_render: --engine %s: the plugin is HQ only\n", argv[i + 1]); return 1; }
        }
        else if (!strcmp(argv[i], "--block")) {
            if (!strncmp(argv[i + 1], "random:", 7)) { block = 0; rnd_state = (uint32_t)strtoul(argv[i + 1] + 7, 0, 0) | 1u; }
            else block = atoi(argv[i + 1]);
        } else if (!strcmp(argv[i], "--dialect")) clap_notes = !strcmp(argv[i + 1], "clap");
        else if (!strcmp(argv[i], "--tempo")) transport_bpm = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--tail-check")) tailck = !strcmp(argv[i + 1], "infinite");
        else if (!strcmp(argv[i], "--bench")) bench = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--offset")) offset = strtoull(argv[i + 1], 0, 10);
        else if (!strcmp(argv[i], "--host-vendor") && !strcmp(argv[i + 1], "wrapper")) {
            HOST.vendor = "The CLAP Wrapper Team";
            as_wrapper = 1;
        }
    }
    if ((rc = read_script(argv[2], rate, &end))) return rc;
    for (uint32_t k = 0; k < nsev; k++) sev[k].h += offset;
    if (load(argv[1]) || !(p = create())) return 1;
    if (!p->activate(p, rate, 1, 4096)) { printf("FAIL activate at %.0f\n", rate); return 1; }
    if (!p->start_processing(p)) { printf("FAIL start_processing\n"); return 1; }
    if (tailck >= 0) return tail_check(p, rate, block, clap_notes, tailck);
    frames = (uint32_t)((uint64_t)end * 44100u / (1000u * HOST_BLOCK)) * HOST_BLOCK;   /* the host build's length */
    if (rate == 48000.0) frames = (uint32_t)((uint64_t)frames * 160u / 147u);          /* (the resampler's outputs) */
    else if (rate != 44100.0) frames = (uint32_t)floor(frames * rate / 44100.0);
    pre = tmode && !strcmp(tmode, "fm1") && rate == 48000.0 ? 48u * LAT_MS : latency_of(p);
    if (!(pcm = calloc((size_t)frames + 1u, 4u))) return 1;
    if (render(p, pcm, pre, frames, block, clap_notes, &clips)) { printf("FAIL render\n"); return 1; }
    if (bench) {
        double audio = (double)(pre + frames) / rate;
        printf("bench: process %.3f s for %.3f s of audio at %.0f Hz: %.4f of real time (%.1f x faster); the worst "
               "call %.0f us for %u frames (%.0f us of audio: %.3f)\n", bench_s, audio, rate, bench_s / audio,
               audio / bench_s, bench_worst_s * 1e6, bench_worst_n, bench_worst_n * 1e6 / rate,
               bench_worst_s * rate / (bench_worst_n ? bench_worst_n : 1));
    }
    p->stop_processing(p);
    p->deactivate(p);
    if (late_warnings) { printf("FAIL %d late events\n", late_warnings); return 1; }
    if (!(w = fopen(argv[3], "wb"))) { perror(argv[3]); return 1; }
    fwrite("RIFF", 1, 4, w); put32(w, frames * 4u + 36u);
    fwrite("WAVEfmt ", 1, 8, w);
    put32(w, 16); put32(w, 0x00020001u); put32(w, (uint32_t)(rate + 0.5)); put32(w, (uint32_t)(rate + 0.5) * 4u);
    put32(w, 0x00100004u);
    fwrite("data", 1, 4, w); put32(w, frames * 4u);
    fwrite(pcm, 4, frames, w);
    if (fclose(w)) { perror(argv[3]); return 1; }
    printf("ok    render %s: %u frames at %.0f Hz, %u events, %u clipped, %u param outputs -> %s\n", argv[2], frames,
           rate, nsev, clips, out_pushed, argv[3]);
    p->destroy(p);
    entry->deinit();
    return 0;
}

/* ---- the self-test */
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); return 1; } } while (0)
typedef struct { char *b; size_t n, cap, rd; } mem_t;
static int64_t m_write(const clap_ostream_t *s, const void *buf, uint64_t n)
{
    mem_t *m = s->ctx;
    if (m->n + n > m->cap) { m->cap = (m->n + n) * 2; m->b = realloc(m->b, m->cap); }
    memcpy(m->b + m->n, buf, n); m->n += n;
    return (int64_t)n;
}
static int64_t m_read(const clap_istream_t *s, void *buf, uint64_t n)
{
    mem_t *m = s->ctx;
    uint64_t k = m->n - m->rd < n ? m->n - m->rd : n;
    if (k > 7) k = 7;                    /* (small reads: the plugin must loop) */
    memcpy(buf, m->b + m->rd, k); m->rd += k;
    return (int64_t)k;
}
static int selftest(void)
{
    const clap_plugin_t *a, *b, *many[17];
    const clap_plugin_params_t *pp;
    const clap_plugin_state_t *ps;
    const clap_plugin_latency_t *pl;
    const clap_plugin_descriptor_t *d = factory->get_plugin_descriptor(factory, 0);
    uint32_t np, i, texts = 0;
    mem_t m1 = { 0 }, m2 = { 0 };
    clap_ostream_t os = { &m1, m_write };
    clap_istream_t is = { &m1, m_read };
    static float l[512], r[512];
    double v;
    int k, nonzero_last = 0, nonzero_first = 0;
    CHECK(d && d->id && d->name && !strcmp(d->name, "Frodhes"), "descriptor");
    CHECK(factory->get_plugin_count(factory) == 1, "one plugin in the factory");
    CHECK((a = create()) != 0, "create");
    CHECK(a->get_extension(a, CLAP_EXT_GUI) != 0, "the GUI extension (the plugin's window: tests/py/test_plugin_gui.py)");
#if !defined(__APPLE__) && !defined(_WIN32)
    CHECK(a->get_extension(a, CLAP_EXT_POSIX_FD_SUPPORT) && a->get_extension(a, CLAP_EXT_TIMER_SUPPORT),
          "posix-fd and timer support (the X11 view runs on the host's event loop)");
#endif
    CHECK((pp = a->get_extension(a, CLAP_EXT_PARAMS)) != 0, "params extension");
    CHECK((ps = a->get_extension(a, CLAP_EXT_STATE)) != 0, "state extension");
    CHECK((pl = a->get_extension(a, CLAP_EXT_LATENCY)) != 0, "latency extension");
    CHECK(pl->get(a) == 325u, "the latency before the first activation: 48 kHz's 325 (%u)", pl->get(a));
    CHECK(a->get_extension(a, CLAP_EXT_AUDIO_PORTS) && a->get_extension(a, CLAP_EXT_NOTE_PORTS), "ports extensions");
    np = pp->count(a);
    for (i = 0; i < np; i++) {           /* every value: its text, and the text back to the same value */
        clap_param_info_t info;
        uint32_t x;
        CHECK(pp->get_info(a, i, &info), "param info %u", i);
        CHECK(info.flags & CLAP_PARAM_IS_STEPPED, "param %u stepped", i);
        CHECK(pp->get_value(a, info.id, &v) && v == info.default_value, "param %u (%s) default %g, value %g", i, info.name, info.default_value, v);
        for (x = 0; x <= (uint32_t)info.max_value; x++) {
            char t[64], t2[64];
            double back;
            CHECK(pp->value_to_text(a, info.id, x, t, sizeof t) && t[0], "text of %s = %u", info.name, x);
            CHECK(pp->text_to_value(a, info.id, t, &back), "value of %s text '%s'", info.name, t);
            CHECK(pp->value_to_text(a, info.id, back, t2, sizeof t2) && !strcmp(t, t2), "%s: '%s' back as '%s'", info.name, t, t2);
            texts++;
        }
    }
    printf("ok    params: %u parameters, %u values: each text reads back as itself\n", np, texts);
    /* the state: a changed sound saved, loaded into a second instance, saved again */
    set_param_inactive(a, 0, 3);         /* TYPE: BELL DYNO */
    set_param_inactive(a, 19, 90);       /* REVERB mix */
    CHECK(pp->get_value(a, 0, &v) && v == 3, "TYPE written while inactive (flush): %g", v);
    CHECK(ps->save(a, &os), "state save");
    CHECK((b = create()) != 0, "second instance");
    CHECK(ps->load(b, &is), "state load");
    os.ctx = &m2;
    CHECK(ps->save(b, &os), "state save (second)");
    m1.b[m1.n] = 0; m2.b = realloc(m2.b, m2.n + 1); m2.b[m2.n] = 0;
    m1.b = realloc(m1.b, m1.n + 1); m1.b[m1.n] = 0;
    CHECK(!strcmp(m1.b, m2.b), "state round trip:\n%s\n%s", m1.b, m2.b);
    {
        const clap_plugin_params_t *pb = b->get_extension(b, CLAP_EXT_PARAMS);
        double x;
        CHECK(pb->get_value(b, 0, &x) && x == 3 && pb->get_value(b, 19, &x) && x == 90 &&
              !pb->get_value(b, 0x10000u, &x), "the loaded instance's values (no ENGINE)");
    }
    printf("ok    state: %zu bytes, saved -> loaded in a second instance -> saved: the same (rescans %d)\n", m1.n, rescans);
    CHECK(!a->activate(a, 500.0, 1, 4096), "500 Hz refused");
    /* the latency (plugin/src/bridge.c): one app block (256 frames at 44.1 kHz) + 2 frames + the oversampler's delay
     * (plugin/src/frp_os.h), placed exactly; 22.05 kHz: the windowed sinc's reach instead */
    {
        static const struct { double r; uint32_t lat; } L[] = { {44100.0, 258u}, {48000.0, 325u}, {88200.0, 602u},
            {96000.0, 698u}, {176400.0, 1222u}, {192000.0, 1416u}, {22050.0, 155u} };
        for (unsigned q = 0; q < sizeof L / sizeof L[0]; q++) {
            uint32_t before = pl->get(a);
            int ch = lat_changes;
            CHECK(a->activate(a, L[q].r, 1, 4096) && pl->get(a) == L[q].lat, "%.0f Hz: latency %u, want %u", L[q].r,
                  pl->get(a), L[q].lat);
            CHECK(lat_changes - ch == (before != L[q].lat), "%.0f Hz: the host told of a change in activate only when it "
                  "changed (%u -> %u, %d calls)", L[q].r, before, L[q].lat, lat_changes - ch);
            a->deactivate(a);
            CHECK(pl->get(a) == L[q].lat, "%.0f Hz: deactivated, the latency still answers %u (%u)", L[q].r, L[q].lat,
                  pl->get(a));
        }
    }
    printf("ok    rates: 44100 (latency 258), 48000 (325), 88200 (602), 96000 (698), 176400 (1222), 192000 (1416), "
           "22050 (155) taken, 500 refused; the latency answers before, during and after an activation (the host told "
           "of each change in activate: %d)\n", lat_changes);
    /* a flood of automation (pluginval's parameter thread test: 2000+ changes a 32-frame block, each due ~10 ms
     * ahead, ~15 blocks in the queue at once): no change dropped, every parameter ends on the last value sent */
    {
        enum { FB = 30, FN = 4000, FIDS = 40 };
        int last[FIDS + 1], hi[FIDS + 1], sent = 0;
        clap_param_info_t inf;
        for (k = 1; k <= FIDS; k++) { last[k] = -1; hi[k] = pp->get_info(a, (uint32_t)k, &inf) ? (int)inf.max_value : 0; }
        full_warnings = 0;
        CHECK(a->activate(a, 48000.0, 1, 4096) && a->start_processing(a), "activate (flood)");
        rnd_state = 0x27129cdu;
        for (i = 0; i < FB + 120; i++) {
            blk_n = 0;
            for (k = 0; i < FB && k < FN; k++) {
                int id = 1 + (int)(rnd() % FIDS), val = hi[id] ? (int)(rnd() % (uint32_t)(hi[id] + 1)) : 0;
                clap_event_param_value_t *e = &blk_ev[blk_n++].u.p;
                memset(e, 0, sizeof *e);
                e->header.size = sizeof *e; e->header.space_id = CLAP_CORE_EVENT_SPACE_ID;
                e->header.type = CLAP_EVENT_PARAM_VALUE; e->header.time = (uint32_t)k * 32u / FN;
                e->param_id = (clap_id)id; e->note_id = -1; e->port_index = -1; e->channel = -1; e->key = -1;
                e->value = val;
                last[id] = val;
                sent++;
            }
            process(a, l, r, 32, (int64_t)i * 32);
        }
        blk_n = 0;
        a->stop_processing(a);
        for (k = 1; k <= FIDS; k++)
            CHECK(last[k] < 0 || (pp->get_value(a, (clap_id)k, &v) && (int)v == last[k]),
                  "flood: parameter %d ends on %d, the last value sent (%g)", k, last[k], v);
        a->deactivate(a);
        CHECK(!full_warnings, "flood: no change dropped (%d warnings)", full_warnings);
        printf("ok    flood: %d changes in %d blocks of 32 frames, none dropped, every parameter on its last value\n", sent, FB);
        /* the same through MIDI (the VST3 wrapper's program and controller parameters): program changes 0..15 and a
         * controller, none dropped, the last program's preset loaded */
        {
            int lastpc = -1;
            full_warnings = 0;
            sent = 0;
            CHECK(a->activate(a, 48000.0, 1, 4096) && a->start_processing(a), "activate (MIDI flood)");
            for (i = 0; i < FB + 400; i++) {
                blk_n = 0;
                for (k = 0; i < FB && k < FN; k++) {
                    clap_event_midi_t *e = &blk_ev[blk_n++].u.m;
                    int pc = (int)(rnd() % 16u);
                    memset(e, 0, sizeof *e);
                    e->header.size = sizeof *e; e->header.space_id = CLAP_CORE_EVENT_SPACE_ID;
                    e->header.type = CLAP_EVENT_MIDI; e->header.time = (uint32_t)k * 32u / FN;
                    if (k & 1) { e->data[0] = 0xB0; e->data[1] = 74; e->data[2] = (uint8_t)(rnd() % 128u); }
                    else { e->data[0] = 0xC0; e->data[1] = (uint8_t)pc; lastpc = pc; }
                    sent++;
                }
                process(a, l, r, 32, (int64_t)i * 32);
            }
            blk_n = 0;
            a->stop_processing(a);
            CHECK(pp->get_value(a, 0x10002u, &v) && (int)v == lastpc, "MIDI flood: the last program's preset (F%02d, PRESET %g)",
                  lastpc + 1, v);
            a->deactivate(a);
            CHECK(!full_warnings, "MIDI flood: no change dropped (%d warnings)", full_warnings);
            printf("ok    MIDI flood: %d program and controller changes in %d blocks, none dropped, F%02d loaded last\n",
                   sent, FB, lastpc + 1);
        }
    }
    b->destroy(b);
    /* FRP_SLOTS + 1 instances: 16 play (with a ready-made a), the 17th is silent and says why */
    for (k = 0; k < 16; k++) CHECK((many[k] = create()) != 0, "instance %d", k + 2);
    CHECK(slot_warnings == 1, "the 17th instance's warning (%d)", slot_warnings);
    for (k = 15; k <= 15; k++) {
        const clap_plugin_t *s = many[k], *f = many[0];
        sev_t on = { 0, 0x90, 60, 100, 0, 0 };
        CHECK(s->activate(s, 44100.0, 1, 4096) && f->activate(f, 44100.0, 1, 4096), "activate");
        for (i = 0; i < 40; i++) {
            uint32_t j;
            blk_n = 0;
            if (i == 0) put_event(&on, 0, 0);
            process(s, l, r, 512, i * 512);
            for (j = 0; j < 512; j++) nonzero_last |= l[j] != 0.0f || r[j] != 0.0f;
            blk_n = 0;
            if (i == 0) put_event(&on, 0, 0);
            process(f, l, r, 512, i * 512);
            for (j = 0; j < 512; j++) nonzero_first |= l[j] != 0.0f || r[j] != 0.0f;
        }
        blk_n = 0;
    }
    CHECK(nonzero_first && !nonzero_last, "instance 2 plays (%d), instance 17 is silent (%d)", nonzero_first, nonzero_last);
    for (k = 0; k < 16; k++) { many[k]->deactivate(many[k]); many[k]->destroy(many[k]); }
    printf("ok    instances: 16 play, the 17th is silent with a warning; slots freed\n");
    CHECK((b = create()) != 0 && slot_warnings == 1, "a freed slot is taken again");
    b->destroy(b);
    a->destroy(a);
    entry->deinit();
    printf("ok    selftest\n");
    return 0;
}

/* ---- the core test (the plugin's UI extension) */
static const frp_ui_vtable *VT;
static int64_t steady_pos;
static void ev_midi(uint32_t t, uint8_t st, uint8_t d1, uint8_t d2) { sev_t e = { 0, st, d1, d2, 0, 0 }; put_event(&e, t, 0); }
static void ev_param(uint32_t t, uint32_t id, double v) { sev_t e = { 0, 0, 0, 0, id, v }; put_event(&e, t, 0); }
/* blocks of 512 frames (the events already in blk_ev go with the first); the output's peak */
static float run(const clap_plugin_t *p, int blocks)
{
    static float l[512], r[512];
    float pk = 0.0f;
    for (int b = 0; b < blocks; b++) {
        process(p, l, r, 512, steady_pos);
        steady_pos += 512;
        blk_n = 0;
        if (callbacks) { callbacks = 0; p->on_main_thread(p); }   /* (the host calls the main thread back) */
        for (int i = 0; i < 512; i++) { float a = fabsf(l[i]), c = fabsf(r[i]); if (a > pk) pk = a; if (c > pk) pk = c; if (l[i] != l[i]) return -1.0f; }
    }
    return pk;
}
/* as run, the largest step between two samples (across the blocks too): a click shows as a step far above the
 * music's own */
static float run_step(const clap_plugin_t *p, int blocks)
{
    static float l[512], r[512], pl, pr;
    float st = 0.0f;
    for (int b = 0; b < blocks; b++) {
        process(p, l, r, 512, steady_pos);
        steady_pos += 512;
        blk_n = 0;
        if (callbacks) { callbacks = 0; p->on_main_thread(p); }
        for (int i = 0; i < 512; i++) {
            float a = fabsf(l[i] - pl), c = fabsf(r[i] - pr);
            if (a > st) st = a;
            if (c > st) st = c;
            pl = l[i]; pr = r[i];
        }
    }
    return st;
}
static int out_count(uint16_t type, uint32_t id, double *last)
{
    int n = 0;
    for (uint32_t i = 0; i < n_outs; i++) if (outs[i].type == type && outs[i].id == id) { n++; if (last) *last = outs[i].v; }
    return n;
}
static int state_of(const clap_plugin_t *p, mem_t *m)
{
    const clap_plugin_state_t *ps = p->get_extension(p, CLAP_EXT_STATE);
    clap_ostream_t os = { m, m_write };
    m->n = m->rd = 0;
    if (!ps->save(p, &os)) return -1;
    m_write(&os, "", 1);                 /* (a terminator for the tests' string compares) */
    m->n--;
    return 0;
}
static int state_load(const clap_plugin_t *p, const char *txt)
{
    const clap_plugin_state_t *ps = p->get_extension(p, CLAP_EXT_STATE);
    mem_t m = { (char *)txt, strlen(txt), strlen(txt), 0 };
    clap_istream_t is = { &m, m_read };
    return ps->load(p, &is) ? 0 : -1;
}
#define P_TYPE_ 0u
#define P_TONE_ 20u
#define P_RV_DEPTH_ 19u
#define P_DL_DEPTH_ 14u
#define P_FL_FREQ_ 41u
#define P_TW_SW_ 2u
#define ID_TUNE 0x10001u
#define ID_PRESET 0x10002u
#define ID_SUSTAIN 0x10003u
#define ID_BYPASS 0x10004u
#define ID_VRESP 0x10005u
static const uint32_t SYNC_IDS[6] = { 5u, 11u, 16u, 31u, 46u, 58u };   /* TW CP DL PH LFO MOT (params.c) */
static const int SYNC_HI[6] = { 13, 14, 13, 14, 14, 13 };

static float note_peak(const clap_plugin_t *p, uint8_t st, uint8_t key, uint8_t vel)
{
    float pk;
    blk_n = 0; ev_midi(0, st, key, vel);
    pk = run(p, 30);
    blk_n = 0; ev_midi(0, (uint8_t)(0x80u | (st & 15u)), key, 0); ev_midi(0, 0xB0u, 123, 0);
    run(p, 200);                         /* (rings out) */
    return pk;
}

static int coretest(void)
{
    const clap_plugin_t *a, *b;
    frp_core *ca, *cb;
    frp_settings s, s2;
    frp_preset_info pi;
    mem_t m1 = { 0 }, m2 = { 0 };
    int ed, idx, n0, k, l;
    float pk;
    char val[64], unit[16];
    const char *dir = getenv("FRODHES_PRESET_DIR");
    CHECK(dir && *dir, "FRODHES_PRESET_DIR names the test's user preset folder");
    CHECK((a = create()) != 0, "create");
    CHECK((VT = a->get_extension(a, FRP_UI_EXT_ID)) != 0 && VT->version >= 2u, "the UI extension %s", FRP_UI_EXT_ID);
    ca = VT->core_of(a);
    CHECK(VT->has_slot(ca), "a slot");

    /* ---- 24 voices on every engine (the HQ sound, the only one): 24 keys held all sound, none stolen; the 25th
     * steals one and the voices stay 24 */
    CHECK(VT->voices(ca) == 24, "24 voices, not %d", VT->voices(ca));
    CHECK(VT->version >= 3u && VT->voices_on, "the UI extension has voices_on (version %u)", VT->version);
    {   /* VERSION (K28): a release's X.Y.Z, a dev build DEV and 4 hex digits of its commit (or DEV alone without git) */
        const char *vt = VT->version >= 5u && VT->version_text ? VT->version_text() : "";
        int okv = (vt[0] >= '1' && vt[0] <= '9') || (vt[0] == '0' && vt[1] == '.' && strncmp(vt, "0.0.0", 5)) ||
                  !strcmp(vt, "DEV") || (!strncmp(vt, "DEV ", 4) && strlen(vt) == 8 && strspn(vt + 4, "0123456789abcdef") == 4);
        CHECK(okv, "the VERSION text: \"%s\"", vt);
        printf("ok    VERSION %s\n", vt);
    }
    {
        const clap_plugin_params_t *pa = a->get_extension(a, CLAP_EXT_PARAMS);
        clap_param_info_t info;
        for (uint32_t i = 0; i < pa->count(a); i++)
            CHECK(pa->get_info(a, i, &info) && info.id != 0x10000u, "no ENGINE parameter (0x10000) any more");
    }
    CHECK(a->activate(a, 44100.0, 1, 4096) && a->start_processing(a), "activate");
    {
        static const int TYPES[3] = { 0, 4, 5 };   /* STAGE 77 (the Rhodes), VIBES, MARIMBA (the bar engine) */
        static const char *const TNAME[3] = { "STAGE 77", "VIBES", "MARIMBA" };
        for (int t = 0; t < 3; t++) {
            int on = 0, held = 0, on_max = 0;
            blk_n = 0; ev_param(0, P_TYPE_, TYPES[t]); ev_midi(0, 0xB0, 47, 0);   /* (VIBES: its motor off) */
            run(a, 40);
            CHECK(VT->voices_on(ca, &held) == 0, "%s: nothing sounds before the chord (%d)", TNAME[t], VT->voices_on(ca, 0));
            for (k = 0; k < 24; k++) {   /* keys 36, 38 .. 82: every engine's range, one new key a block */
                blk_n = 0; ev_midi(0, 0x90, (uint8_t)(36 + 2 * k), 100);
                run(a, 1);
                on = VT->voices_on(ca, &held);
                CHECK(on == k + 1 && held == k + 1, "%s: key %d of 24 struck: %d voices sound (%d held), want %d: none "
                      "stolen", TNAME[t], k + 1, on, held, k + 1);
            }
            blk_n = 0; ev_midi(0, 0x90, 84, 100); ev_midi(0, 0x80, 84, 0);   /* the 25th, let go at once */
            for (l = 0; l < 4; l++) { run(a, 1); on = VT->voices_on(ca, &held); if (on > on_max) on_max = on; }
            CHECK(on_max == 24 && held == 23, "%s: the 25th key steals a voice: %d voices (want 24), %d held (want 23)",
                  TNAME[t], on_max, held);
            blk_n = 0; ev_midi(0, 0xB0, 123, 0);
            for (k = 0; k < 24; k++) ev_midi(0, 0x80, (uint8_t)(36 + 2 * k), 0);
            run(a, 300);                 /* (rings out) */
            printf("ok    voices: %s: 24 keys held, 24 voices sound, none stolen; the 25th steals one\n", TNAME[t]);
        }
        blk_n = 0; ev_param(0, P_TYPE_, 0); run(a, 40);
    }
    a->reset(a);                         /* a note at once after the host's reset is heard (it was killed: the
                                          * reset's voice kill ran at the next block, after the note) */
    pk = note_peak(a, 0x90, 64, 100);
    CHECK(pk > 0.01f, "a note right after reset() sounds (%.4f)", pk);
    CHECK(late_warnings == 0, "no late events");

    /* ---- automation: host writes not echoed, the app's own changes and the UI's reported (gestures in order) */
    n_outs = 0;
    blk_n = 0; ev_param(100, P_FL_FREQ_, 30); ev_param(300, P_RV_DEPTH_, 55);
    run(a, 4);
    CHECK(VT->param_get(ca, P_FL_FREQ_) == 30 && VT->param_get(ca, P_RV_DEPTH_) == 55, "host writes taken (%d %d)",
          VT->param_get(ca, P_FL_FREQ_), VT->param_get(ca, P_RV_DEPTH_));
    CHECK(out_count(CLAP_EVENT_PARAM_VALUE, P_FL_FREQ_, 0) == 0 && out_count(CLAP_EVENT_PARAM_VALUE, P_RV_DEPTH_, 0) == 0,
          "a host write is not echoed back");
    blk_n = 0; ev_midi(0, 0xB0, 89, 77);     /* CC 89: DELAY mix (the device's map) */
    run(a, 4);
    {
        double v = -1;
        CHECK(VT->param_get(ca, P_DL_DEPTH_) == 77 && out_count(CLAP_EVENT_PARAM_VALUE, P_DL_DEPTH_, &v) == 1 && v == 77,
              "a CC's change reported to the host (%d, %g)", VT->param_get(ca, P_DL_DEPTH_), v);
    }
    n_outs = 0;
    VT->param_begin(ca, P_TONE_); VT->param_set(ca, P_TONE_, 70); VT->param_set(ca, P_TONE_, 90); VT->param_end(ca, P_TONE_);
    CHECK(VT->param_get(ca, P_TONE_) == 90, "the UI sees its own write at once (%d)", VT->param_get(ca, P_TONE_));
    run(a, 3);
    CHECK(n_outs >= 4 && outs[0].type == CLAP_EVENT_PARAM_GESTURE_BEGIN && outs[1].type == CLAP_EVENT_PARAM_VALUE &&
          outs[1].v == 70 && outs[2].v == 90 && outs[3].type == CLAP_EVENT_PARAM_GESTURE_END && outs[3].id == P_TONE_,
          "the UI's drag to the host: begin, 70, 90, end (%u events)", n_outs);
    CHECK(out_count(CLAP_EVENT_PARAM_VALUE, P_TONE_, 0) == 2, "and not again as the app's change");
    {
        const clap_plugin_params_t *pp = a->get_extension(a, CLAP_EXT_PARAMS);
        double v;
        CHECK(pp->get_value(a, P_TONE_, &v) && v == 90, "the host reads the UI's value (%g)", v);
    }
    /* the GUI's next frame reads the app's 90: the UI's own value is no longer held (else, for PEND_S, a later change
     * by a CC would read as the UI's 90) */
    CHECK(VT->param_get(ca, P_TONE_) == 90, "the app shows the UI's write (%d)", VT->param_get(ca, P_TONE_));
    VT->param_text(ca, 41u, 127, val, sizeof val, unit, sizeof unit);
    CHECK(val[0] && unit[0], "a value's text and unit: '%s' '%s'", val, unit);
    VT->param_text(ca, ID_TUNE, 55, val, sizeof val, unit, sizeof unit);
    CHECK(!strcmp(val, "+5") && !strcmp(unit, "c"), "TUNE 55: '%s' '%s'", val, unit);
    printf("ok    automation: host writes taken and not echoed; a CC's change and the UI's drag (with its gesture) "
           "reported; FL FREQ 127 = '%s %s'\n", val, unit);

    /* ---- MIDI learn */
    VT->learn_start(ca, P_FL_FREQ_);
    run(a, 2);
    CHECK(VT->learning(ca) == (int)P_FL_FREQ_, "learning FL FREQ (%d)", VT->learning(ca));
    blk_n = 0; ev_midi(0, 0xB0, 3, 0);       /* (CC 3: a developer CC on the device; learned, it is the user's) */
    run(a, 3);
    CHECK(VT->learning(ca) == -1 && VT->learned_cc(ca, P_FL_FREQ_) == 3, "CC 3 learned for FL FREQ (%d)", VT->learned_cc(ca, P_FL_FREQ_));
    blk_n = 0; ev_midi(0, 0xB0, 3, 127); run(a, 3);
    k = VT->param_get(ca, P_FL_FREQ_);
    blk_n = 0; ev_midi(0, 0xB0, 3, 64); run(a, 3);
    CHECK(k == 127 && VT->param_get(ca, P_FL_FREQ_) == 64, "CC 3 drives FL FREQ (127 -> %d, 64 -> %d)", k, VT->param_get(ca, P_FL_FREQ_));
    VT->learn_start(ca, P_TW_SW_); run(a, 1);
    blk_n = 0; ev_midi(0, 0xB0, 5, 1); run(a, 2);
    blk_n = 0; ev_midi(0, 0xB0, 5, 127); run(a, 2);
    CHECK(VT->param_get(ca, P_TW_SW_) == 2, "a learned CC scaled to the range (TREM 0..2: %d)", VT->param_get(ca, P_TW_SW_));
    VT->learn_start(ca, P_TONE_); run(a, 1);
    blk_n = 0; ev_midi(0, 0xB0, 91, 5); run(a, 2);   /* CC 91: REVERB mix on the device: learned for TONE */
    k = VT->param_get(ca, P_RV_DEPTH_);
    blk_n = 0; ev_midi(0, 0xB0, 91, 33); run(a, 2);
    CHECK(VT->param_get(ca, P_TONE_) == 33 && VT->param_get(ca, P_RV_DEPTH_) == k, "learned first: CC 91 drives TONE "
          "(%d), not REVERB (%d)", VT->param_get(ca, P_TONE_), VT->param_get(ca, P_RV_DEPTH_));
    CHECK(VT->cc_target(ca, 91, &l) == (int)P_TONE_ && l == 1, "CC 91's target: TONE, learned");
    VT->learn_forget(ca, P_TONE_); run(a, 1);
    CHECK(VT->cc_target(ca, 91, &l) == (int)P_RV_DEPTH_ && l == 0, "forgotten: CC 91 is REVERB's again");
    blk_n = 0; ev_midi(0, 0xB0, 91, 44); run(a, 2);
    CHECK(VT->param_get(ca, P_RV_DEPTH_) == 44, "the default map again (%d)", VT->param_get(ca, P_RV_DEPTH_));
    VT->learn_start(ca, ID_SUSTAIN); run(a, 1);
    blk_n = 0; ev_midi(0, 0xB0, 70, 0); run(a, 2);
    blk_n = 0; ev_midi(0, 0xB0, 70, 127); run(a, 2);
    CHECK(VT->sustain(ca) == 1, "SUSTAIN learned on CC 70");
    blk_n = 0; ev_midi(0, 0xB0, 70, 0); run(a, 2);
    CHECK(VT->sustain(ca) == 0, "and let go");
    VT->learn_start(ca, 0x10000u); run(a, 1);
    CHECK(VT->learning(ca) == -1, "the retired ENGINE id (0x10000) cannot be learned");
    printf("ok    MIDI learn: learned, scaled, before the default map, forgotten; own parameters too\n");

    /* ---- tempo sources, SYNC ALL */
    transport_bpm = 100.0;
    run(a, 4);
    CHECK(VT->tempo_bpm10(ca) == 1000 && VT->host_bpm10(ca) == 1000, "HOST: the transport's 100 BPM (%d, %d)", VT->tempo_bpm10(ca), VT->host_bpm10(ca));
    VT->settings_get(ca, &s);
    CHECK(s.tempo_src == FRP_TEMPO_HOST && s.pgm_change == 1 && s.midi_ch == 0 && s.vel_curve == FRP_VEL_MEDIUM,
          "the defaults: HOST, program changes on, ALL channels, MEDIUM");
    s.tempo_src = FRP_TEMPO_INTERNAL; s.bpm10 = 1375;
    VT->settings_set(ca, &s); run(a, 4);
    CHECK(VT->tempo_bpm10(ca) == 1375, "INTERNAL: 137.5 BPM over the transport's 100 (%d)", VT->tempo_bpm10(ca));
    s.tempo_src = FRP_TEMPO_MIDI; VT->settings_set(ca, &s); run(a, 4);
    CHECK(VT->tempo_bpm10(ca) == 1375, "MIDI CLOCK without a clock: the BPM setting (%d)", VT->tempo_bpm10(ca));
    for (k = 0; k < 2 * 24 * 4; k++) {   /* a clock at 90 BPM: 24 a beat, 27.8 ms apart */
        blk_n = 0;
        ev_midi(0, 0xF8, 0, 0);
        { static float l2[1225], r2[1225]; process(a, l2, r2, 1225, steady_pos); steady_pos += 1225; blk_n = 0; }
    }
    CHECK(abs(VT->tempo_bpm10(ca) - 900) <= 5, "MIDI CLOCK follows a 90 BPM clock (%d)", VT->tempo_bpm10(ca));
    s.tempo_src = FRP_TEMPO_HOST; VT->settings_set(ca, &s); run(a, 30);
    CHECK(VT->tempo_bpm10(ca) == 1000, "HOST again (%d)", VT->tempo_bpm10(ca));
    s.sync_all = 1; s.sync_div = 9; VT->settings_set(ca, &s); run(a, 3);
    for (k = 0; k < 6; k++) CHECK(VT->param_get(ca, SYNC_IDS[k]) == 9, "SYNC ALL 1/4: sync id %u = %d", SYNC_IDS[k], VT->param_get(ca, SYNC_IDS[k]));
    VT->preset_load(ca, 8); run(a, 3);
    for (k = 0; k < 6; k++) CHECK(VT->param_get(ca, SYNC_IDS[k]) == 9, "SYNC ALL on a preset load: sync id %u = %d", SYNC_IDS[k], VT->param_get(ca, SYNC_IDS[k]));
    s.sync_div = 14 - 1; VT->settings_set(ca, &s); run(a, 3);
    for (k = 0; k < 6; k++) CHECK(VT->param_get(ca, SYNC_IDS[k]) == (13 < SYNC_HI[k] ? 13 : SYNC_HI[k]), "SYNC ALL 2BAR: %d", VT->param_get(ca, SYNC_IDS[k]));
    s.sync_all = 0; VT->settings_set(ca, &s); run(a, 3);
    for (k = 0; k < 6; k++) CHECK(VT->param_get(ca, SYNC_IDS[k]) == 0, "SYNC ALL off: FREE (%d)", VT->param_get(ca, SYNC_IDS[k]));
    transport_bpm = 0.0;
    printf("ok    tempo: HOST 100, INTERNAL 137.5, MIDI CLOCK (none: the setting; a 90 BPM clock followed), SYNC ALL "
           "on / on a preset load / off\n");

    /* ---- presets: factory, browse, UNDO, user files, program changes */
    n0 = VT->preset_count(ca);
    CHECK(n0 >= 57, "57 factory presets at least (%d)", n0);
    CHECK(!VT->preset_info(ca, 0, &pi) && !strcmp(pi.tag, "F01") && pi.name[0] && !pi.user, "F01 '%s'", pi.name);
    CHECK(!VT->preset_info(ca, 56, &pi) && !strcmp(pi.tag, "F57"), "F57 '%s'", pi.name);
    VT->preset_load(ca, 4); run(a, 2);
    idx = VT->preset_current(ca, &pi, &ed);
    CHECK(idx == 4 && !ed && !strcmp(pi.tag, "F05"), "F05 loaded (%d %s %s edited %d)", idx, pi.tag, pi.name, ed);
    k = VT->param_get(ca, P_TYPE_);
    VT->param_set(ca, P_TONE_, 3); run(a, 2);
    CHECK(VT->preset_current(ca, &pi, &ed) == 4 && ed, "edited after a change");
    VT->preset_load(ca, 30); run(a, 2);           /* F31: a Move variant */
    CHECK(VT->preset_current(ca, &pi, &ed) == 30 && !strcmp(pi.tag, "F31"), "F31 (%s %s)", pi.tag, pi.name);
    CHECK(VT->can_undo(ca) && !VT->undo(ca), "UNDO");
    run(a, 2);
    /* (presets.c preset_undo: the sound back, as it was, and taken as the base: not marked edited, as the device) */
    CHECK(VT->preset_current(ca, &pi, &ed) == 4 && !ed && VT->param_get(ca, P_TONE_) == 3, "UNDO: F05 with its edit (%d %d)", ed, VT->param_get(ca, P_TONE_));
    VT->undo(ca); run(a, 2);
    CHECK(VT->preset_current(ca, &pi, &ed) == 30, "UNDO again: F31");
    VT->preset_load(ca, 0); run(a, 2);
    CHECK(VT->preset_step(ca, -1) == n0 - 1, "PREV from F01 wraps to the last");
    run(a, 2);
    CHECK(VT->preset_step(ca, 1) == 0, "NEXT wraps to F01");
    run(a, 2);
    CHECK(VT->preset_step(ca, 1) == 1, "NEXT: F02");
    run(a, 2);
    /* a user preset */
    VT->param_set(ca, P_TONE_, 111); VT->param_set(ca, P_RV_DEPTH_, 99); run(a, 2);
    idx = VT->preset_save(ca, "Test \"One\"");
    CHECK(idx >= 57 && VT->preset_count(ca) == n0 + 1, "saved as a user preset (%d)", idx);
    run(a, 2);
    CHECK(VT->preset_current(ca, &pi, &ed) == idx && pi.user && !ed && !strcmp(pi.name, "Test _One_"),
          "the sound is that preset now (%s '%s' edited %d)", pi.tag, pi.name, ed);
    {
        char path[1200];
        struct stat st;
        snprintf(path, sizeof path, "%s/Test _One_.frpreset", dir);
        CHECK(stat(path, &st) == 0 && st.st_size > 100, "the file %s", path);
    }
    VT->preset_load(ca, 2); run(a, 2);
    CHECK(VT->param_get(ca, P_TONE_) != 111, "another preset");
    VT->preset_load(ca, idx); run(a, 2);
    CHECK(VT->param_get(ca, P_TONE_) == 111 && VT->param_get(ca, P_RV_DEPTH_) == 99 && VT->preset_current(ca, &pi, &ed) == idx,
          "the user preset loads back (TONE %d)", VT->param_get(ca, P_TONE_));
    /* program changes: 4 -> F05, 40 -> F25, 16 -> U01; off: ignored */
    blk_n = 0; ev_midi(0, 0xC0, 4, 0); run(a, 3);
    CHECK(VT->preset_current(ca, &pi, &ed) == 4, "program change 4: F05 (%d)", VT->preset_current(ca, &pi, &ed));
    blk_n = 0; ev_midi(0, 0xC0, 40, 0); run(a, 3);
    CHECK(VT->preset_current(ca, &pi, &ed) == 24, "program change 40: F25 (%d)", VT->preset_current(ca, &pi, &ed));
    {   /* the FM-1's map, on to F57: 0..15 F01..F16, 32 + k F17 + k (32..34 F17..F19 as the device, 35..72 F20..F57) */
        static const int PC[][2] = { { 0, 0 }, { 15, 15 }, { 32, 16 }, { 34, 18 }, { 35, 19 }, { 50, 34 }, { 72, 56 } };
        for (k = 0; k < (int)(sizeof PC / sizeof PC[0]); k++) {
            blk_n = 0; ev_midi(0, 0xC0, (uint8_t)PC[k][0], 0); run(a, 3);
            CHECK(VT->preset_current(ca, &pi, &ed) == PC[k][1], "program change %d: F%02d (%d)", PC[k][0], PC[k][1] + 1,
                  VT->preset_current(ca, &pi, &ed));
        }
        blk_n = 0; ev_midi(0, 0xC0, 73, 0); run(a, 3);
        CHECK(VT->preset_current(ca, &pi, &ed) == 56, "program change 73: no preset there, F57 stays");
    }
    blk_n = 0; ev_midi(0, 0xC0, 16, 0); run(a, 3);
    CHECK(VT->preset_current(ca, &pi, &ed) == 57 && VT->param_get(ca, P_TONE_) == 111, "program change 16: U01 (%d)",
          VT->preset_current(ca, &pi, &ed));
    VT->settings_get(ca, &s); s.pgm_change = 0; VT->settings_set(ca, &s); run(a, 2);
    blk_n = 0; ev_midi(0, 0xC0, 4, 0); run(a, 3);
    CHECK(VT->preset_current(ca, &pi, &ed) == 57, "program changes off: ignored");
    s.pgm_change = 1; VT->settings_set(ca, &s); run(a, 2);
    printf("ok    presets: %d (57 factory + user), browse with wrap, UNDO both ways, a user file saved and loaded, "
           "program changes (0..15 F01..F16, 32..72 F17..F57, U01; off)\n", VT->preset_count(ca));

    /* ---- SUSTAIN, FX BYPASS, the on-screen keyboard, the meters, velocity, MIDI CHANNEL */
    n_outs = 0;
    VT->sustain_set(ca, 1); run(a, 2);
    CHECK(VT->sustain(ca) == 1 && VT->param_get(ca, ID_SUSTAIN) == 1, "SUSTAIN latched");
    blk_n = 0; ev_midi(0, 0xB0, 64, 0); run(a, 2);   /* a pedal let up: the latch goes, as on the device */
    {
        double v = -1;
        CHECK(VT->sustain(ca) == 0 && out_count(CLAP_EVENT_PARAM_VALUE, ID_SUSTAIN, &v) >= 2 && v == 0,
              "a pedal's CC 64 0 lets the latch go (and the host hears it: %g)", v);
    }
    VT->fx_bypass_set(ca, 1); run(a, 2);
    CHECK(VT->fx_bypass(ca) == 1, "FX BYPASS on");
    VT->fx_bypass_set(ca, 0); run(a, 2);
    CHECK(VT->fx_bypass(ca) == 0, "FX BYPASS off");
    VT->note(ca, 67, 100);
    pk = run(a, 20);
    {
        uint8_t nt[128];
        float db[2], pkd[2], gr;
        VT->notes(ca, nt);
        VT->meters(ca, db, pkd, &gr);
        CHECK(nt[67] == 100 && pk > 0.01f, "the on-screen key plays (%u, %.3f)", nt[67], pk);
        VT->meters(ca, db, pkd, &gr);
        CHECK(pkd[0] > -40.0f && gr >= 0.0f, "the meters (%.1f dB peak, GR %.1f)", pkd[0], gr);
    }
    VT->note(ca, 67, 0); run(a, 100);
    VT->preset_load(ca, 2); run(a, 2);           /* (F03: no compressor, no tremolo) */
    VT->settings_get(ca, &s);
    {
        float pm, ph, plt, pf;
        pm = note_peak(a, 0x90, 60, 40);
        s.vel_curve = FRP_VEL_HEAVY; VT->settings_set(ca, &s); run(a, 2);
        ph = note_peak(a, 0x90, 60, 40);
        s.vel_curve = FRP_VEL_LIGHT; VT->settings_set(ca, &s); run(a, 2);
        plt = note_peak(a, 0x90, 60, 40);
        s.vel_curve = FRP_VEL_FIXED; s.fixed_vel = 127; VT->settings_set(ca, &s); run(a, 2);
        pf = note_peak(a, 0x90, 60, 40);
        CHECK(ph < pm && pm < plt && plt < pf, "velocity 40: HEAVY %.4f < MEDIUM %.4f < LIGHT %.4f < FIXED 127 %.4f", ph, pm, plt, pf);
        printf("ok    velocity 40: HEAVY %.4f < MEDIUM %.4f < LIGHT %.4f < FIXED 127 %.4f\n", ph, pm, plt, pf);
        s.vel_curve = FRP_VEL_MEDIUM; s.midi_ch = 2; VT->settings_set(ca, &s); run(a, 2);
        pm = note_peak(a, 0x90, 60, 100);
        ph = note_peak(a, 0x91, 60, 100);
        CHECK(ph > 0.01f && pm < 0.05f * ph, "MIDI CHANNEL 2: channel 1 silent (%.4f), channel 2 plays (%.4f)", pm, ph);
    }
    printf("ok    SUSTAIN latch and a pedal, FX BYPASS, the on-screen keyboard, the meters, MIDI CHANNEL\n");

    /* ---- the state: every setting, MIDI learn, the preset; older (CLASSIC too) and broken states */
    s.midi_ch = 3; s.vel_curve = FRP_VEL_HEAVY; s.fixed_vel = 100; s.pgm_change = 0; s.tempo_src = FRP_TEMPO_INTERNAL;
    s.bpm10 = 975; s.sync_all = 1; s.sync_div = 6;
    VT->settings_set(ca, &s); run(a, 2);
    VT->learn_start(ca, 37u); run(a, 1);         /* LEVEL on CC 9 of channel 3 */
    blk_n = 0; ev_midi(0, 0xB2, 9, 20); run(a, 2);
    VT->preset_load(ca, 6); run(a, 2);
    VT->param_set(ca, P_TONE_, 100); VT->param_set(ca, ID_TUNE, 57); run(a, 2);
    /* the velocity response (the settings' RESPONSE, 0.9.10): A by default, a host's automation (clamped to the methods
     * built), saved with the settings record */
    CHECK(VT->param_get(ca, ID_VRESP) == 0, "RESPONSE: A by default (%d)", VT->param_get(ca, ID_VRESP));
    blk_n = 0; ev_param(0, ID_VRESP, 9); run(a, 2);
    CHECK(VT->param_get(ca, ID_VRESP) == 1, "RESPONSE automated past its end: B (%d)", VT->param_get(ca, ID_VRESP));
    blk_n = 0; ev_param(0, ID_VRESP, 0); run(a, 2);
    CHECK(VT->param_get(ca, ID_VRESP) == 0, "RESPONSE automated back to A (%d)", VT->param_get(ca, ID_VRESP));
    blk_n = 0; ev_midi(0, 0xB2, 40, 127); run(a, 2);      /* (CC 40 on CHANNEL 3: B; the host is told) */
    CHECK(VT->param_get(ca, ID_VRESP) == 1, "RESPONSE by CC 40: B (%d)", VT->param_get(ca, ID_VRESP));
    blk_n = 0; ev_param(0, ID_SUSTAIN, 1); run(a, 2);   /* (SUSTAIN: a parameter, so in the state too) */
    blk_n = 0; ev_param(0, ID_BYPASS, 1); run(a, 2);    /* (EFFECTS BYPASS too) */
    blk_n = 0; ev_param(0, P_RV_DEPTH_, 41); ev_param(10, P_RV_DEPTH_, 42);
    run(a, 2);
    CHECK(VT->param_get(ca, P_RV_DEPTH_) == 42, "a block's changes in order (%d)", VT->param_get(ca, P_RV_DEPTH_));
    a->stop_processing(a);
    a->deactivate(a);
    CHECK(state_of(a, &m1) == 0, "state save");
    CHECK((b = create()) != 0, "a second instance");
    cb = VT->core_of(b);
    CHECK(state_load(b, m1.b) == 0, "state load");
    CHECK(state_of(b, &m2) == 0 && !strcmp(m1.b, m2.b), "state round trip:\n%s\n%s", m1.b, m2.b);
    VT->settings_get(cb, &s2);
    CHECK(!memcmp(&s, &s2, sizeof s), "the settings come back (ch %d vel %d fix %d pgm %d tsrc %d bpm %d sall %d sdiv %d)",
          s2.midi_ch, s2.vel_curve, s2.fixed_vel, s2.pgm_change, s2.tempo_src, s2.bpm10, s2.sync_all, s2.sync_div);
    CHECK(VT->learned_cc(cb, 37u) == 9 && VT->learned_cc(cb, P_FL_FREQ_) == 3, "the learned CCs come back");
    CHECK(VT->preset_current(cb, &pi, &ed) == 6 && ed && VT->param_get(cb, P_TONE_) == 100 && VT->param_get(cb, ID_TUNE) == 57,
          "the preset (F07, edited), TONE and TUNE come back");
    CHECK(VT->param_get(cb, ID_VRESP) == 1, "RESPONSE comes back (%d)", VT->param_get(cb, ID_VRESP));
    CHECK(VT->voices(cb) == 24 && strstr(m1.b, "\"engine\":\"1\""), "24 voices; the state says HQ (\"engine\" 1) for "
          "older builds: %d voices", VT->voices(cb));
    CHECK(VT->can_undo(cb), "the undo copy comes back");
    CHECK(VT->param_get(cb, ID_SUSTAIN) == 1 && VT->param_get(cb, P_RV_DEPTH_) == 42, "SUSTAIN and REVERB come back (%d %d)",
          VT->param_get(cb, ID_SUSTAIN), VT->param_get(cb, P_RV_DEPTH_));
    CHECK(VT->fx_bypass(cb) == 1 && VT->param_get(cb, ID_BYPASS) == 1, "EFFECTS BYPASS comes back (%d)", VT->fx_bypass(cb));
    printf("ok    state: %zu bytes, every setting, the learned CCs, the preset and its edit, TUNE, SUSTAIN, EFFECTS BYPASS, the undo copy\n", m1.n);
    {
        /* the scaffold's state (version 1: the Move's keys only): the sound loads, the plugin's settings default */
        char old[4096], *pv;
        snprintf(old, sizeof old, "%s", m1.b);
        pv = strstr(old, ",\"pv\":");
        CHECK(pv != 0, "the plugin's keys");
        strcpy(pv, "}");
        CHECK(state_load(b, old) == 0, "a version 1 state loads");
        VT->settings_get(cb, &s2);
        CHECK(s2.pgm_change == 1 && s2.tempo_src == FRP_TEMPO_HOST && s2.sync_all == 0 && s2.midi_ch == 3 &&
              s2.vel_curve == FRP_VEL_HEAVY && VT->learned_cc(cb, 37u) == -1 && VT->param_get(cb, P_TONE_) == 100,
              "version 1: the record's settings and sound, the plugin's defaults");
        CHECK(state_load(b, "hello") != 0 && VT->param_get(cb, P_TONE_) == 100, "a broken state is refused, nothing changes");
        snprintf(old, sizeof old, "%.300s", m1.b);
        CHECK(state_load(b, old) != 0, "a cut state is refused");
        snprintf(old, sizeof old, "%s", m1.b);
        pv = strstr(old, "\"learn\":\"");
        pv[9] = 'Z';
        CHECK(state_load(b, old) == 0 && VT->learned_cc(cb, 37u) == -1 && VT->learned_cc(cb, P_FL_FREQ_) == -1,
              "a bad learn map loads empty, the rest loads");
        /* a state saved with ENGINE CLASSIC (the builds before the plugin became HQ only wrote "engine":"0"; the rest
         * of their state is this one's format): it loads, on HQ with 24 voices, the sound, settings and learned CCs
         * whole, and saves again as this build's own state */
        snprintf(old, sizeof old, "%s", m1.b);
        pv = strstr(old, "\"engine\":\"1\"");
        CHECK(pv != 0, "the engine key");
        pv[10] = '0';
        CHECK(state_load(b, "hello") != 0, "(a broken state first)");
        CHECK(state_load(b, old) == 0 && VT->voices(cb) == 24 && VT->param_get(cb, P_TONE_) == 100 &&
              VT->param_get(cb, ID_TUNE) == 57 && VT->learned_cc(cb, 37u) == 9, "a CLASSIC state loads on HQ: 24 voices "
              "(%d), the same sound and learned CCs", VT->voices(cb));
        CHECK(state_of(b, &m2) == 0 && !strcmp(m1.b, m2.b), "the CLASSIC state saved again is the HQ state:\n%s\n%s",
              m1.b, m2.b);
        pv = strstr(old, ",\"engine\":\"0\"");
        memmove(pv, pv + 13, strlen(pv + 13) + 1);
        CHECK(state_load(b, old) == 0 && VT->voices(cb) == 24 && state_of(b, &m2) == 0 && !strcmp(m1.b, m2.b),
              "a state without the engine key: the same:\n%s\n%s", m1.b, m2.b);
        printf("ok    older and broken states: version 1 loads (defaults for the new keys), garbage and a cut state "
               "refused, a bad learn map dropped, a CLASSIC state loads on HQ (24 voices) and saves as HQ\n");
    }
    b->destroy(b);
    /* the user preset seen by a new instance, deleted */
    CHECK((b = create()) != 0, "a third instance");
    cb = VT->core_of(b);
    CHECK(VT->preset_count(cb) == n0 + 1 && !VT->preset_info(cb, 57, &pi) && pi.user, "the user preset in a new instance");
    CHECK(VT->preset_delete(cb, 57) == 0 && VT->preset_count(cb) == n0 && VT->preset_delete(cb, 3) != 0, "deleted (a factory one cannot be)");
    b->destroy(b);
    a->destroy(a);
    entry->deinit();
    printf("ok    coretest\n");
    return 0;
}
