/* SPDX-License-Identifier: GPL-3.0-only */
/* A headless CLAP host for the plugin's tests (Gate D, make test-plugin). Only the CLAP ABI: no Frodhes code here.
 *
 *   clap_render PLUGIN SCRIPT OUT.wav [options]
 *       a host script's MIDI (tests/host.c format: "T midi ST D1 D2", "T end"; expect / shot / dump lines skipped)
 *       played through the plugin, written as the host build writes its WAV (16-bit stereo, the clip at +-32767). The
 *       events go in at the host frame whose millisecond is the script's (the plugin's clock, plugin/src/bridge.c:
 *       44.1 kHz: frame ceil(T x 44.1); 48 kHz: 48 T), as "due" in move/tests/move_replay.c: an event listed after a
 *       later one plays at the later time; another rate: frame ceil(T x rate / 1000). The output's first frames are
 *       the pre-roll (FRP_LAT_MS, 10 ms: 441 / 480 frames; another rate: the latency the plugin reports)
 *       and are not written: the rest is the host build's render of the script at 44.1 kHz (Gate D compares the
 *       sha256), and at 48 kHz the FM-1's USB resampler over it.
 *     --rate R                (44100; 48000 the FM-1's resampler; another rate from 1000 to 768000 the plugin's
 *                             windowed-sinc resampler)
 *     --engine classic|hq     ENGINE, set before activation (the plugin's default: hq)
 *     --block N               frames a process call (512); --block random:SEED for random sizes 0..2048
 *     --dialect midi|clap     notes as raw MIDI events (midi) or CLAP note events (clap); CC and the rest stay MIDI
 *   clap_render PLUGIN --selftest
 *       the parameters (every value's text back to the same value), the state (saved, loaded into a second instance,
 *       saved again: the same), the rates (500 Hz refused, 96 kHz taken), FRP_SLOTS + 1 instances (the last
 *       one silent, with its warning), the latency, the descriptor and the absence of a GUI extension.
 * Exit: 0 ok, 1 failure, 3 the script is not MIDI-driven (nothing written). */
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <clap/clap.h>

#define LAT_MS 10u                       /* plugin/src/bridge.h FRP_LAT_MS: the output's pre-roll */
#define HOST_BLOCK 256u                  /* tests/host.c HALF_FRAMES */
#define END_DEFAULT 6500u                /* tests/host.c: no "end" line */
#define MAXEV 200000
#define ENGINE_ID 0x10000u               /* plugin/gen/params_map.h FRP_ID_ENGINE */

/* ---- the host side */
static int warnings, late_warnings, slot_warnings;
static void h_log(const clap_host_t *h, clap_log_severity s, const char *msg)
{
    (void)h;
    if (s >= CLAP_LOG_WARNING) { warnings++; fprintf(stderr, "plugin log: %s\n", msg); }
    if (strstr(msg, "late")) late_warnings++;
    if (strstr(msg, "16 instances")) slot_warnings++;
}
static const clap_host_log_t H_LOG = { h_log };
static int rescans;
static void h_rescan(const clap_host_t *h, clap_param_rescan_flags f) { (void)h; (void)f; rescans++; }
static void h_clear(const clap_host_t *h, clap_id id, clap_param_clear_flags f) { (void)h; (void)id; (void)f; }
static void h_flush(const clap_host_t *h) { (void)h; }
static const clap_host_params_t H_PARAMS = { h_rescan, h_clear, h_flush };
static int callbacks;
static const void *h_ext(const clap_host_t *h, const char *id)
{
    (void)h;
    if (!strcmp(id, CLAP_EXT_LOG)) return &H_LOG;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &H_PARAMS;
    return 0;
}
static void h_req(const clap_host_t *h) { (void)h; }
static void h_cb(const clap_host_t *h) { (void)h; callbacks++; }
static const clap_host_t HOST = { CLAP_VERSION_INIT, 0, "frodhes clap_render", "Frodhes tests", "", "1",
                                  h_ext, h_req, h_req, h_cb };

/* the input events of one process call */
typedef struct { union { clap_event_header_t h; clap_event_midi_t m; clap_event_note_t n; clap_event_param_value_t p; } u; } ev_t;
static ev_t blk_ev[4096];
static uint32_t blk_n;
static uint32_t in_size(const clap_input_events_t *l) { (void)l; return blk_n; }
static const clap_event_header_t *in_get(const clap_input_events_t *l, uint32_t i) { (void)l; return &blk_ev[i].u.h; }
static const clap_input_events_t IN_EVENTS = { 0, in_size, in_get };
static uint32_t out_pushed;
static bool out_push(const clap_output_events_t *l, const clap_event_header_t *e) { (void)l; (void)e; out_pushed++; return true; }
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
static int process(const clap_plugin_t *p, float *l, float *r, uint32_t n, int64_t steady)
{
    float *ch[2] = { l, r };
    clap_audio_buffer_t out = { 0 };
    clap_process_t pr = { 0 };
    out.data32 = ch; out.channel_count = 2;
    pr.steady_time = steady; pr.frames_count = n;
    pr.audio_outputs = &out; pr.audio_outputs_count = 1;
    pr.in_events = &IN_EVENTS; pr.out_events = &OUT_EVENTS;
    return p->process(p, &pr) == CLAP_PROCESS_ERROR ? -1 : 0;
}

/* ---- the script */
typedef struct { uint64_t h; uint8_t st, d1, d2; } sev_t;
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
        if (strcmp(what, "midi")) { printf("host-only %s: \"%s\" lines\n", path, what); fclose(f); return 3; }
        if (sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) { printf("FAIL bad MIDI line: %s", line); fclose(f); return 1; }
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
    if (clap_notes && (t == 0x90u || t == 0x80u)) {
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
static int render(const clap_plugin_t *p, int16_t *pcm, uint32_t pre, uint32_t frames, int block, int clap_notes,
                  uint32_t *clips)
{
    static float l[4096], r[4096];
    uint64_t pos = 0, total = (uint64_t)pre + frames;
    uint32_t k = 0;
    while (pos < total) {
        uint32_t n = block > 0 ? (uint32_t)block : rnd() % 2049u, i;
        if (n > 4096u) n = 4096u;
        if (pos + n > total) n = (uint32_t)(total - pos);
        blk_n = 0;
        while (k < nsev && sev[k].h < pos + n && blk_n < 4096u) { put_event(&sev[k], (uint32_t)(sev[k].h - pos), clap_notes); k++; }
        if (process(p, l, r, n, (int64_t)pos)) { printf("FAIL process\n"); return -1; }
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

int main(int argc, char **argv)
{
    double rate = 44100.0;
    int engine = -1, block = 512, clap_notes = 0, rc, i;
    unsigned end;
    uint32_t frames, pre, clips = 0;
    int16_t *pcm;
    const clap_plugin_t *p;
    FILE *w;
    if (argc >= 3 && !strcmp(argv[2], "--selftest")) { if (load(argv[1])) return 1; return selftest(); }
    if (argc < 4) { fprintf(stderr, "usage: clap_render PLUGIN SCRIPT OUT.wav [--rate R] [--engine classic|hq] "
                                    "[--block N|random:SEED] [--dialect midi|clap]\n       clap_render PLUGIN --selftest\n"); return 1; }
    for (i = 4; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--rate")) rate = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--engine")) engine = !strcmp(argv[i + 1], "hq");
        else if (!strcmp(argv[i], "--block")) {
            if (!strncmp(argv[i + 1], "random:", 7)) { block = 0; rnd_state = (uint32_t)strtoul(argv[i + 1] + 7, 0, 0) | 1u; }
            else block = atoi(argv[i + 1]);
        } else if (!strcmp(argv[i], "--dialect")) clap_notes = !strcmp(argv[i + 1], "clap");
    }
    if ((rc = read_script(argv[2], rate, &end))) return rc;
    if (load(argv[1]) || !(p = create())) return 1;
    if (engine >= 0) set_param_inactive(p, ENGINE_ID, engine);
    if (!p->activate(p, rate, 1, 4096)) { printf("FAIL activate at %.0f\n", rate); return 1; }
    if (!p->start_processing(p)) { printf("FAIL start_processing\n"); return 1; }
    frames = (uint32_t)((uint64_t)end * 44100u / (1000u * HOST_BLOCK)) * HOST_BLOCK;   /* the host build's length */
    if (rate == 48000.0) frames = (uint32_t)((uint64_t)frames * 160u / 147u);          /* (the resampler's outputs) */
    else if (rate != 44100.0) frames = (uint32_t)floor(frames * rate / 44100.0);
    pre = rate == 48000.0 ? 48u * LAT_MS : rate == 44100.0 ? 441u * LAT_MS / 10u : latency_of(p);
    if (!(pcm = calloc((size_t)frames + 1u, 4u))) return 1;
    if (render(p, pcm, pre, frames, block, clap_notes, &clips)) { printf("FAIL render\n"); return 1; }
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
#if defined(__APPLE__) || defined(_WIN32)
    CHECK(a->get_extension(a, CLAP_EXT_GUI) != 0, "the GUI extension (the plugin's window: tests/py/test_plugin_gui.py)");
#else
    CHECK(!a->get_extension(a, CLAP_EXT_GUI), "no GUI extension on this OS yet (the host's generic view)");
#endif
    CHECK((pp = a->get_extension(a, CLAP_EXT_PARAMS)) != 0, "params extension");
    CHECK((ps = a->get_extension(a, CLAP_EXT_STATE)) != 0, "state extension");
    CHECK((pl = a->get_extension(a, CLAP_EXT_LATENCY)) != 0, "latency extension");
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
    set_param_inactive(a, ENGINE_ID, 0); /* ENGINE: CLASSIC */
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
              pb->get_value(b, ENGINE_ID, &x) && x == 0, "the loaded instance's values");
    }
    printf("ok    state: %zu bytes, saved -> loaded in a second instance -> saved: the same (rescans %d)\n", m1.n, rescans);
    CHECK(!a->activate(a, 500.0, 1, 4096), "500 Hz refused");
    CHECK(a->activate(a, 96000.0, 1, 4096) && pl->get(a) == 1056u, "96 kHz: latency %u", pl->get(a));
    a->deactivate(a);
    CHECK(a->activate(a, 48000.0, 1, 4096) && pl->get(a) == 487u, "48 kHz: latency %u", pl->get(a));
    a->deactivate(a);
    CHECK(a->activate(a, 44100.0, 1, 4096) && pl->get(a) == 441u, "44.1 kHz: latency %u", pl->get(a));
    a->deactivate(a);
    printf("ok    rates: 44100 (latency 441), 48000 (487), 96000 (1056) taken, 500 refused\n");
    b->destroy(b);
    /* FRP_SLOTS + 1 instances: 16 play (with a ready-made a), the 17th is silent and says why */
    for (k = 0; k < 16; k++) CHECK((many[k] = create()) != 0, "instance %d", k + 2);
    CHECK(slot_warnings == 1, "the 17th instance's warning (%d)", slot_warnings);
    for (k = 15; k <= 15; k++) {
        const clap_plugin_t *s = many[k], *f = many[0];
        sev_t on = { 0, 0x90, 60, 100 };
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
