/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes as a CLAP instrument (milestone B-M0): the entry, the factory and the plugin. clap-wrapper builds the VST3
 * and the Audio Unit (v2) from the same code (plugin/CMakeLists.txt, make_clapfirst_plugins).
 *
 *   audio-ports   one stereo output, no input
 *   note-ports    one input, CLAP and MIDI dialects: notes with velocity, CC, pitch bend, sustain (CC 64), program
 *                 change, as the FM-1 takes them over USB MIDI (its CHANNEL setting filters; ALL by default)
 *   params        the app's PARAMS[] (plugin/gen/params_map.h: stable ids, 0..hi, stepped, the FM-1's texts) and
 *                 ENGINE (CLASSIC / HQ, HQ by default); a change the app makes itself (a MIDI CC, a program change)
 *                 goes back to the host as a parameter output event
 *   state         the Move's flat JSON (a sound moves between a Move track and a desktop session)
 *   latency       the bridge's pre-roll (plugin/src/bridge.c), constant
 *   gui           the plugin's window (plugin/gui_*.c drawing plugin/ui/frui.c; macOS Cocoa, Windows HWND): its changes
 *                 reach the audio thread through a queue and go to the host as parameter events with gestures
 * Sample rates: 44.1 kHz (the app's own), 48 kHz (the FM-1's USB resampler), any other from 1 to 768 kHz
 * (a windowed-sinc resampler: plugin/src/bridge.c); outside that the instance refuses to activate and logs why. More
 * than FRP_SLOTS (16) instances in one process: the extra ones play silence and log why. */
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <clap/clap.h>
#include "bridge.h"
#include "../gui_clap.h"
#include "../gui_shot.h"

#ifndef FRP_VERSION
#define FRP_VERSION "0.0.0-dev"
#endif
#define FRP_PLUGIN_ID "io.github.leonardomirabal.frodhes"

static const char *const FEATURES[] = { CLAP_PLUGIN_FEATURE_INSTRUMENT, CLAP_PLUGIN_FEATURE_SYNTHESIZER,
                                        CLAP_PLUGIN_FEATURE_STEREO, 0 };
static const clap_plugin_descriptor_t DESC = {
    .clap_version = CLAP_VERSION_INIT,
    .id = FRP_PLUGIN_ID,
    .name = "Frodhes",
    .vendor = "Frodhes",
    .url = "https://github.com/leonardoMirabal/frodhes",
    .manual_url = "https://github.com/leonardoMirabal/frodhes/blob/main/docs/MANUAL.md",
    .support_url = "https://github.com/leonardoMirabal/frodhes/issues",
    .version = FRP_VERSION,
    .description = "A physical-model electric piano, vibraphone and marimba: the FM-1 firmware's engine",
    .features = FEATURES,
};

#define FRP_UIQ_N 1024u                  /* the window's queue (a power of two) */
enum { UIQ_BEGIN, UIQ_END, UIQ_VALUE, UIQ_MIDI };
typedef struct {
    clap_plugin_t plugin;
    const clap_host_t *host;
    const clap_host_log_t *log;
    const clap_host_params_t *hparams;
    frp_inst_t *in;
    int active;
    /* the audio thread's view of what the host believes (parameter output events: only the app's own changes) */
    double host_view[FRP_NPARAM];
    /* the defaults: a fresh instance's values (the app's boot, factory preset included; PARAMS[].def is the bare
     * parameter default, which the boot preset overrides) */
    double def[FRP_NPARAM];
    frp_snap_t last;
    int have_last;
    /* a state loaded while active: the host is told to re-read the values once the app has taken it */
    _Atomic uint32_t rescan_rev;
    _Atomic int rescan_tries;
    /* the window (plugin/gui_clap.c), its queue to the audio thread (one writer: the main thread; one reader: process
     * or flush) and what the audio thread shows it (the output's peak, the held notes, the sustain) */
    frp_gui_clap_t *gui;
    struct { uint8_t kind, st, d1, d2; uint32_t id; double v; } uiq[FRP_UIQ_N];
    _Atomic uint32_t uiq_w, uiq_r;
    _Atomic int processing;
    _Atomic uint32_t mon_peak[2];        /* float bits */
    _Atomic uint8_t mon_notes[128];
    _Atomic int mon_sustain;
} frp_plugin_t;

static void say(frp_plugin_t *p, clap_log_severity sev, const char *msg)
{
    if (p->log && p->log->log) p->log->log(p->host, sev, msg);
}

/* ---- parameters */
static int pidx(clap_id id)
{
    if (id < FRP_NPARAM_APP) return (int)id;
    for (uint32_t i = FRP_NPARAM_APP; i < FRP_NPARAM; i++) if (FRP_PARAMS[i].id == id) return (int)i;
    return -1;
}
static uint32_t p_count(const clap_plugin_t *plugin) { (void)plugin; return FRP_NPARAM; }
static bool p_info(const clap_plugin_t *plugin, uint32_t index, clap_param_info_t *info)
{
    const frp_plugin_t *p = plugin->plugin_data;
    const frp_param_t *d;
    if (index >= FRP_NPARAM) return false;
    d = &FRP_PARAMS[index];
    memset(info, 0, sizeof *info);
    info->id = d->id;
    info->flags = CLAP_PARAM_IS_AUTOMATABLE | CLAP_PARAM_IS_STEPPED | (d->stepped ? CLAP_PARAM_IS_ENUM : 0);
    snprintf(info->name, sizeof info->name, "%s", d->name);
    snprintf(info->module, sizeof info->module, "%s", d->module);
    info->min_value = 0.0;
    info->max_value = d->hi;
    info->default_value = p->def[index];
    return true;
}
static bool p_value(const clap_plugin_t *plugin, clap_id id, double *out)
{
    frp_plugin_t *p = plugin->plugin_data;
    return frp_inst_get(p->in, id, out) == 0;
}
static bool p_to_text(const clap_plugin_t *plugin, clap_id id, double value, char *out, uint32_t size)
{
    int i = pidx(id);
    int32_t v;
    (void)plugin;
    if (i < 0 || !size) return false;
    v = (int32_t)floor(value + 0.5);
    if (v < 0) v = 0;
    if (v > FRP_PARAMS[i].hi) v = FRP_PARAMS[i].hi;
    snprintf(out, size, "%s", FRP_TEXT[FRP_PARAMS[i].text0 + (uint32_t)v]);
    return true;
}
static bool p_from_text(const clap_plugin_t *plugin, clap_id id, const char *text, double *out)
{
    int i = pidx(id);
    char *end;
    double x;
    (void)plugin;
    if (i < 0 || !text) return false;
    for (uint32_t v = 0; v <= FRP_PARAMS[i].hi; v++) {   /* a value's own text ("4.2 kHz", "MARIMBA") */
        if (!strcasecmp(text, FRP_TEXT[FRP_PARAMS[i].text0 + v])) { *out = v; return true; }
    }
    x = strtod(text, &end);                              /* else the raw value 0..hi */
    if (end == text) return false;
    *out = x < 0.0 ? 0.0 : x > FRP_PARAMS[i].hi ? FRP_PARAMS[i].hi : floor(x + 0.5);
    return true;
}
/* ---- the window's queue (main thread -> audio thread) and the monitor (audio thread -> the window) */
static void mon_midi(frp_plugin_t *p, uint8_t st, uint8_t d1, uint8_t d2)
{
    uint8_t t = st & 0xF0u;
    if (d1 > 127) return;
    if (t == 0x90u && d2) atomic_store_explicit(&p->mon_notes[d1], d2, memory_order_relaxed);
    else if (t == 0x80u || t == 0x90u) atomic_store_explicit(&p->mon_notes[d1], 0, memory_order_relaxed);
    else if (t == 0xB0u && d1 == 64) atomic_store_explicit(&p->mon_sustain, d2 >= 64, memory_order_relaxed);
    else if (t == 0xB0u && (d1 == 120 || d1 == 123)) for (int k = 0; k < 128; k++) atomic_store(&p->mon_notes[k], 0);
}
static void mon_peak(frp_plugin_t *p, const float *l, const float *r, uint32_t n)
{
    float pk[2] = {0.f, 0.f};
    for (uint32_t i = 0; i < n; i++) {
        float a = fabsf(l[i]), b = r ? fabsf(r[i]) : a;
        if (a > pk[0]) pk[0] = a;
        if (b > pk[1]) pk[1] = b;
    }
    for (int c = 0; c < 2; c++) {                /* max into the window's accumulator (the window takes and clears it) */
        uint32_t old = atomic_load_explicit(&p->mon_peak[c], memory_order_relaxed), nw;
        float f;
        do {
            memcpy(&f, &old, 4);
            if (!(pk[c] > f)) break;
            memcpy(&nw, &pk[c], 4);
        } while (!atomic_compare_exchange_weak(&p->mon_peak[c], &old, nw));
    }
}
static void out_param(const clap_output_events_t *out, uint16_t type, clap_id id, double v)
{
    if (!out) return;
    if (type == CLAP_EVENT_PARAM_VALUE) {
        clap_event_param_value_t o;
        memset(&o, 0, sizeof o);
        o.header.size = sizeof o; o.header.space_id = CLAP_CORE_EVENT_SPACE_ID; o.header.type = type;
        o.param_id = id; o.note_id = -1; o.port_index = -1; o.channel = -1; o.key = -1; o.value = v;
        out->try_push(out, &o.header);
    } else {
        clap_event_param_gesture_t o;
        memset(&o, 0, sizeof o);
        o.header.size = sizeof o; o.header.space_id = CLAP_CORE_EVENT_SPACE_ID; o.header.type = type;
        o.param_id = id;
        out->try_push(out, &o.header);
    }
}
/* the window's changes into the app (at the block's start) and to the host; timed: active (process, or flush on the
 * audio thread), else the main thread while inactive (notes are dropped then: nothing plays) */
static void uiq_drain(frp_plugin_t *p, const clap_output_events_t *out, int timed)
{
    uint32_t r = atomic_load_explicit(&p->uiq_r, memory_order_relaxed);
    uint32_t w = atomic_load_explicit(&p->uiq_w, memory_order_acquire);
    for (; r != w; r++) {
        const __typeof__(p->uiq[0]) *e = &p->uiq[r & (FRP_UIQ_N - 1u)];
        int i;
        switch (e->kind) {
        case UIQ_BEGIN: out_param(out, CLAP_EVENT_PARAM_GESTURE_BEGIN, e->id, 0.0); break;
        case UIQ_END: out_param(out, CLAP_EVENT_PARAM_GESTURE_END, e->id, 0.0); break;
        case UIQ_VALUE:
            if ((i = pidx(e->id)) < 0) break;
            p->host_view[i] = e->v;
            if (timed) frp_inst_param(p->in, 0, e->id, e->v);
            else frp_inst_param_now(p->in, e->id, e->v);
            out_param(out, CLAP_EVENT_PARAM_VALUE, e->id, e->v);
            break;
        case UIQ_MIDI:
            if (timed) { frp_inst_midi(p->in, 0, e->st, e->d1, e->d2); mon_midi(p, e->st, e->d1, e->d2); }
            break;
        }
    }
    atomic_store_explicit(&p->uiq_r, r, memory_order_release);
}
static void uiq_push(frp_plugin_t *p, uint8_t kind, uint32_t id, double v, uint8_t st, uint8_t d1, uint8_t d2)
{
    uint32_t w = atomic_load_explicit(&p->uiq_w, memory_order_relaxed);
    if (w - atomic_load_explicit(&p->uiq_r, memory_order_acquire) >= FRP_UIQ_N) return;   /* (full: the host stalled) */
    p->uiq[w & (FRP_UIQ_N - 1u)].kind = kind;
    p->uiq[w & (FRP_UIQ_N - 1u)].id = id;
    p->uiq[w & (FRP_UIQ_N - 1u)].v = v;
    p->uiq[w & (FRP_UIQ_N - 1u)].st = st;
    p->uiq[w & (FRP_UIQ_N - 1u)].d1 = d1;
    p->uiq[w & (FRP_UIQ_N - 1u)].d2 = d2;
    atomic_store_explicit(&p->uiq_w, w + 1u, memory_order_release);
    if (!atomic_load(&p->processing) && p->hparams && p->hparams->request_flush) p->hparams->request_flush(p->host);
}
/* frp_ui_iface.h's core calls (main thread) */
static int ui_param_get(void *ctx, uint32_t id, double *v) { return frp_inst_get(((frp_plugin_t *)ctx)->in, id, v); }
static double ui_param_default(void *ctx, uint32_t id)
{
    int i = pidx(id);
    return i < 0 ? 0.0 : ((frp_plugin_t *)ctx)->def[i];
}
static void ui_gesture(void *ctx, uint32_t id, int begin) { uiq_push(ctx, begin ? UIQ_BEGIN : UIQ_END, id, 0.0, 0, 0, 0); }
static void ui_param_set(void *ctx, uint32_t id, double v) { uiq_push(ctx, UIQ_VALUE, id, v, 0, 0, 0); }
static void ui_midi(void *ctx, uint8_t st, uint8_t d1, uint8_t d2) { uiq_push(ctx, UIQ_MIDI, 0, 0.0, st, d1, d2); }
static void ui_monitor(void *ctx, frp_ui_monitor_t *o)
{
    frp_plugin_t *p = ctx;
    for (int c = 0; c < 2; c++) {
        uint32_t b = atomic_exchange(&p->mon_peak[c], 0u);
        memcpy(&o->peak[c], &b, 4);
    }
    for (int k = 0; k < 128; k++) o->notes[k] = atomic_load_explicit(&p->mon_notes[k], memory_order_relaxed);
    o->sustain = atomic_load_explicit(&p->mon_sustain, memory_order_relaxed);
    o->gr_db = 0.f;                      /* (the compressor's reduction is not published by the app yet) */
}
/* the window's table: the core calls here; the backends (presets, settings, MIDI learn, tempo, the value texts) are
 * B-M1's and stay NULL until they exist (frp_ui_iface.h) */
static void frp_ui_host_bind(frp_plugin_t *p, frp_ui_host_t *h)
{
    memset(h, 0, sizeof *h);
    h->ctx = p;
    h->param_get = ui_param_get;
    h->param_default = ui_param_default;
    h->param_gesture = ui_gesture;
    h->param_set = ui_param_set;
    h->midi = ui_midi;
    h->monitor = ui_monitor;
}
frp_gui_clap_t *frp_plugin_gui(const clap_plugin_t *plugin) { return ((frp_plugin_t *)plugin->plugin_data)->gui; }

static void p_events_in(frp_plugin_t *p, const clap_input_events_t *ev, int timed)
{
    uint32_t n = ev ? ev->size(ev) : 0, k;
    for (k = 0; k < n; k++) {
        const clap_event_header_t *h = ev->get(ev, k);
        const clap_event_param_value_t *pv;
        int i;
        if (h->space_id != CLAP_CORE_EVENT_SPACE_ID || h->type != CLAP_EVENT_PARAM_VALUE) continue;
        pv = (const clap_event_param_value_t *)h;
        if ((i = pidx(pv->param_id)) < 0) continue;
        p->host_view[i] = floor(pv->value + 0.5);
        if (timed) frp_inst_param(p->in, h->time, pv->param_id, pv->value);
        else frp_inst_param_now(p->in, pv->param_id, pv->value);
    }
}
static void p_flush(const clap_plugin_t *plugin, const clap_input_events_t *in, const clap_output_events_t *out)
{
    frp_plugin_t *p = plugin->plugin_data;
    uiq_drain(p, out, p->active);        /* the window's changes first (they reach the host as output events) */
    p_events_in(p, in, p->active);       /* active: the audio thread, between two blocks (the next block applies them) */
}
static const clap_plugin_params_t EXT_PARAMS = { p_count, p_info, p_value, p_to_text, p_from_text, p_flush };

/* ---- audio and note ports */
static uint32_t ap_count(const clap_plugin_t *plugin, bool is_input) { (void)plugin; return is_input ? 0 : 1; }
static bool ap_get(const clap_plugin_t *plugin, uint32_t index, bool is_input, clap_audio_port_info_t *info)
{
    (void)plugin;
    if (is_input || index) return false;
    memset(info, 0, sizeof *info);
    info->id = 0;
    snprintf(info->name, sizeof info->name, "Main");
    info->flags = CLAP_AUDIO_PORT_IS_MAIN;
    info->channel_count = 2;
    info->port_type = CLAP_PORT_STEREO;
    info->in_place_pair = CLAP_INVALID_ID;
    return true;
}
static const clap_plugin_audio_ports_t EXT_AUDIO = { ap_count, ap_get };
static uint32_t np_count(const clap_plugin_t *plugin, bool is_input) { (void)plugin; return is_input ? 1 : 0; }
static bool np_get(const clap_plugin_t *plugin, uint32_t index, bool is_input, clap_note_port_info_t *info)
{
    (void)plugin;
    if (!is_input || index) return false;
    memset(info, 0, sizeof *info);
    info->id = 0;
    info->supported_dialects = CLAP_NOTE_DIALECT_CLAP | CLAP_NOTE_DIALECT_MIDI;
    info->preferred_dialect = CLAP_NOTE_DIALECT_CLAP;
    snprintf(info->name, sizeof info->name, "Notes");
    return true;
}
static const clap_plugin_note_ports_t EXT_NOTES = { np_count, np_get };

/* ---- latency, state */
static uint32_t lat_get(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    return frp_inst_latency(p->in);
}
static const clap_plugin_latency_t EXT_LATENCY = { lat_get };

static bool st_save(const clap_plugin_t *plugin, const clap_ostream_t *s)
{
    frp_plugin_t *p = plugin->plugin_data;
    char buf[FRP_STATE_MAX];
    int n = frp_inst_state_get(p->in, buf, (int)sizeof buf), done = 0;
    if (n < 0) return false;
    while (done < n) {
        int64_t w = s->write(s, buf + done, (uint64_t)(n - done));
        if (w <= 0) return false;
        done += (int)w;
    }
    return true;
}
static bool st_load(const clap_plugin_t *plugin, const clap_istream_t *s)
{
    frp_plugin_t *p = plugin->plugin_data;
    char buf[FRP_STATE_MAX * 2];
    int64_t got = 0, r;
    while (got < (int64_t)sizeof buf - 1 && (r = s->read(s, buf + got, (uint64_t)(sizeof buf - 1 - (size_t)got))) > 0) got += r;
    buf[got] = 0;
    if (frp_inst_state_set(p->in, buf)) { say(p, CLAP_LOG_WARNING, "Frodhes: the saved state could not be read"); return false; }
    if (!p->active) { if (p->hparams) p->hparams->rescan(p->host, CLAP_PARAM_RESCAN_VALUES); }
    else {                               /* applied by the audio thread's next step: re-read the values after it */
        atomic_store(&p->rescan_rev, frp_inst_rev(p->in));
        atomic_store(&p->rescan_tries, 200);
        p->host->request_callback(p->host);
    }
    return true;
}
static const clap_plugin_state_t EXT_STATE = { st_save, st_load };

/* ---- the plugin */
static bool pl_init(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    p->log = p->host->get_extension(p->host, CLAP_EXT_LOG);
    p->hparams = p->host->get_extension(p->host, CLAP_EXT_PARAMS);
    p->in = frp_inst_new();
    if (!p->in) return false;
    if (frp_inst_slot(p->in) < 0)
        say(p, CLAP_LOG_WARNING, "Frodhes: 16 instances already play in this process: this one stays silent");
    for (uint32_t i = 0; i < FRP_NPARAM; i++) {
        double v = FRP_PARAMS[i].def;
        (void)frp_inst_get(p->in, FRP_PARAMS[i].id, &v);
        p->host_view[i] = v;
        p->def[i] = v;
    }
    {
        frp_ui_host_t uh;
        frp_ui_host_bind(p, &uh);
        p->gui = frp_gui_clap_new(p->host, &uh);
    }
    return true;
}
static void pl_destroy(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    frp_gui_clap_free(p->gui);
    frp_inst_free(p->in);
    free(p);
}
static bool pl_activate(const clap_plugin_t *plugin, double rate, uint32_t min_frames, uint32_t max_frames)
{
    frp_plugin_t *p = plugin->plugin_data;
    (void)min_frames; (void)max_frames;
    if (frp_inst_activate(p->in, rate)) {
        char m[160];
        snprintf(m, sizeof m, "Frodhes: plays at 1 to 768 kHz, not at %.0f Hz", rate);
        say(p, CLAP_LOG_ERROR, m);
        return false;
    }
    p->have_last = 0;
    p->active = 1;
    return true;
}
static void pl_deactivate(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    frp_stats_t st;
    uint32_t late;
    frp_inst_deactivate(p->in);
    p->active = 0;
    frp_inst_stats(p->in, &st, &late);
    if (late || st.q_full) {             /* (never expected: bridge.c's schedule keeps every event ahead) */
        char m[160];
        snprintf(m, sizeof m, "Frodhes: %u events were late, %u did not fit the queue", late, st.q_full);
        say(p, CLAP_LOG_WARNING, m);
    }
}
static bool pl_start(const clap_plugin_t *plugin)
{
    atomic_store(&((frp_plugin_t *)plugin->plugin_data)->processing, 1);
    return true;
}
static void pl_stop(const clap_plugin_t *plugin) { atomic_store(&((frp_plugin_t *)plugin->plugin_data)->processing, 0); }
static void pl_reset(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    frp_inst_reset(p->in);
    for (int k = 0; k < 128; k++) atomic_store(&p->mon_notes[k], 0);
}

static uint8_t vel7(double v)
{
    long x = lround(v * 127.0);
    return (uint8_t)(x < 0 ? 0 : x > 127 ? 127 : x);
}
static clap_process_status pl_process(const clap_plugin_t *plugin, const clap_process_t *pr)
{
    frp_plugin_t *p = plugin->plugin_data;
    const clap_input_events_t *ev = pr->in_events;
    uint32_t n = ev ? ev->size(ev) : 0, k, tempo10 = 0;
    float *l, *r;
    frp_snap_t sn;
    if (pr->transport && (pr->transport->flags & CLAP_TRANSPORT_HAS_TEMPO) && pr->transport->tempo > 0.0)
        tempo10 = (uint32_t)lround(pr->transport->tempo * 10.0);
    uiq_drain(p, pr->out_events, 1);     /* the window's changes, at the block's start */
    for (k = 0; k < n; k++) {
        const clap_event_header_t *h = ev->get(ev, k);
        if (h->space_id != CLAP_CORE_EVENT_SPACE_ID) continue;
        switch (h->type) {
        case CLAP_EVENT_NOTE_ON: case CLAP_EVENT_NOTE_OFF: case CLAP_EVENT_NOTE_CHOKE: {
            const clap_event_note_t *e = (const clap_event_note_t *)h;
            uint8_t ch = (uint8_t)(e->channel >= 0 && e->channel < 16 ? e->channel : 0), v;
            if (e->key < 0 || e->key > 127) break;          /* (a wildcard key: no MIDI note for it) */
            if (h->type == CLAP_EVENT_NOTE_ON) {
                v = vel7(e->velocity);
                frp_inst_midi(p->in, h->time, (uint8_t)(0x90u | ch), (uint8_t)e->key, v ? v : 1u);
                mon_midi(p, 0x90u, (uint8_t)e->key, v ? v : 1u);
            } else {
                frp_inst_midi(p->in, h->time, (uint8_t)(0x80u | ch), (uint8_t)e->key, h->type == CLAP_EVENT_NOTE_OFF ? vel7(e->velocity) : 0u);
                mon_midi(p, 0x80u, (uint8_t)e->key, 0);
            }
            break;
        }
        case CLAP_EVENT_MIDI: {
            const clap_event_midi_t *e = (const clap_event_midi_t *)h;
            frp_inst_midi(p->in, h->time, e->data[0], e->data[1], e->data[2]);
            mon_midi(p, e->data[0], e->data[1], e->data[2]);
            break;
        }
        case CLAP_EVENT_PARAM_VALUE: {
            const clap_event_param_value_t *e = (const clap_event_param_value_t *)h;
            int i = pidx(e->param_id);
            if (i < 0) break;
            p->host_view[i] = floor(e->value + 0.5);
            frp_inst_param(p->in, h->time, e->param_id, e->value);
            break;
        }
        default: break;
        }
    }
    if (pr->audio_outputs_count < 1 || pr->audio_outputs[0].channel_count < 1 || !pr->audio_outputs[0].data32)
        return CLAP_PROCESS_ERROR;
    l = pr->audio_outputs[0].data32[0];
    r = pr->audio_outputs[0].channel_count > 1 ? pr->audio_outputs[0].data32[1] : 0;
    if (r) frp_inst_render(p->in, l, r, pr->frames_count, tempo10);
    else {                                                 /* (a mono bus: L only) */
        static float scratch[4096];
        uint32_t done = 0;
        while (done < pr->frames_count) {
            uint32_t c = pr->frames_count - done < 4096u ? pr->frames_count - done : 4096u;
            frp_inst_render(p->in, l + done, scratch, c, tempo10);
            done += c;
        }
    }
    pr->audio_outputs[0].constant_mask = 0;
    mon_peak(p, l, r, pr->frames_count);
    /* the app's own changes (a MIDI CC, a program change, a state) back to the host: a value that moved since the
     * last block and is not what the host last sent (a host write reaches the app FRP_LAT_MS later: until then
     * the app's value has not moved, so nothing is echoed) */
    if (pr->out_events && frp_inst_snap(p->in, &sn) == 0) {
        uint32_t i;
        for (i = 0; i < FRP_NPARAM; i++) {
            double v = i < FRP_NPARAM_APP ? sn.pv[i] : sn.engine;
            double was = i < FRP_NPARAM_APP ? p->last.pv[i] : p->last.engine;
            clap_event_param_value_t o;
            if (!p->have_last || v == was || v == p->host_view[i]) continue;
            memset(&o, 0, sizeof o);
            o.header.size = sizeof o;
            o.header.time = 0;
            o.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
            o.header.type = CLAP_EVENT_PARAM_VALUE;
            o.param_id = FRP_PARAMS[i].id;
            o.note_id = -1; o.port_index = -1; o.channel = -1; o.key = -1;
            o.value = v;
            if (pr->out_events->try_push(pr->out_events, &o.header)) p->host_view[i] = v;
        }
        p->last = sn;
        p->have_last = 1;
    }
    return CLAP_PROCESS_CONTINUE;
}
static const void *pl_ext(const clap_plugin_t *plugin, const char *id)
{
    (void)plugin;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &EXT_PARAMS;
    if (!strcmp(id, CLAP_EXT_AUDIO_PORTS)) return &EXT_AUDIO;
    if (!strcmp(id, CLAP_EXT_NOTE_PORTS)) return &EXT_NOTES;
    if (!strcmp(id, CLAP_EXT_LATENCY)) return &EXT_LATENCY;
    if (!strcmp(id, CLAP_EXT_STATE)) return &EXT_STATE;
    if (!strcmp(id, CLAP_EXT_GUI)) return frp_gui_clap_ext();
    if (!strcmp(id, FRP_EXT_GUI_SHOT)) return frp_gui_clap_shot_ext();
    if (!strcmp(id, CLAP_EXT_POSIX_FD_SUPPORT)) return frp_gui_clap_fd_ext();     /* (Linux: the X11 view) */
    if (!strcmp(id, CLAP_EXT_TIMER_SUPPORT)) return frp_gui_clap_timer_ext();
    return 0;
}
static void pl_main(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    int t = atomic_load(&p->rescan_tries);
    if (t <= 0) return;
    if (frp_inst_rev(p->in) != atomic_load(&p->rescan_rev) || t == 1) {
        atomic_store(&p->rescan_tries, 0);
        if (p->hparams) p->hparams->rescan(p->host, CLAP_PARAM_RESCAN_VALUES);
    } else {
        atomic_store(&p->rescan_tries, t - 1);
        p->host->request_callback(p->host);
    }
}

static const clap_plugin_t *f_create(const clap_plugin_factory_t *f, const clap_host_t *host, const char *id)
{
    frp_plugin_t *p;
    (void)f;
    if (!host || !id || strcmp(id, DESC.id) || !clap_version_is_compatible(host->clap_version)) return 0;
    p = calloc(1, sizeof *p);
    if (!p) return 0;
    p->host = host;
    p->plugin.desc = &DESC;
    p->plugin.plugin_data = p;
    p->plugin.init = pl_init;
    p->plugin.destroy = pl_destroy;
    p->plugin.activate = pl_activate;
    p->plugin.deactivate = pl_deactivate;
    p->plugin.start_processing = pl_start;
    p->plugin.stop_processing = pl_stop;
    p->plugin.reset = pl_reset;
    p->plugin.process = pl_process;
    p->plugin.get_extension = pl_ext;
    p->plugin.on_main_thread = pl_main;
    return &p->plugin;
}
static uint32_t f_count(const clap_plugin_factory_t *f) { (void)f; return 1; }
static const clap_plugin_descriptor_t *f_desc(const clap_plugin_factory_t *f, uint32_t i) { (void)f; return i ? 0 : &DESC; }
static const clap_plugin_factory_t FACTORY = { f_count, f_desc, f_create };

/* the entry's three functions (plugin/src/clap_entry.cpp exports clap_entry from them in every format) */
bool frp_entry_init(const char *path) { (void)path; return true; }
void frp_entry_deinit(void) {}
const void *frp_entry_get_factory(const char *id) { return id && !strcmp(id, CLAP_PLUGIN_FACTORY_ID) ? &FACTORY : 0; }
