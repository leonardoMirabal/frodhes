/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes as a CLAP instrument (milestone B-M0): the entry, the factory and the plugin. clap-wrapper builds the VST3
 * and the Audio Unit (v2) from the same code (plugin/CMakeLists.txt, make_clapfirst_plugins).
 *
 *   audio-ports   one stereo output, no input
 *   note-ports    one input, CLAP and MIDI dialects: notes with velocity, CC, pitch bend, sustain (CC 64), program
 *                 change, as the FM-1 takes them over USB MIDI (its CHANNEL setting filters; ALL by default)
 *   params        the app's PARAMS[] (plugin/gen/params_map.h: stable ids, 0..hi, stepped, the FM-1's texts) and
 *                 the plugin's own: ENGINE (CLASSIC: the FM-1's build and its 10 voices / HQ: the Move's float paths,
 *                 16 voices; HQ by default), TUNE, PRESET (F01..F57), SUSTAIN (the latch), FX BYPASS; a change the
 *                 app makes itself (a MIDI CC, a program change, a preset) goes back to the host as a parameter output
 *                 event, and the plugin UI's writes as gestures and values
 *   state         the Move's flat JSON (a sound moves between a Move track and a desktop session) and the plugin's
 *                 own keys (slot_bridge.h: settings, MIDI learn, the preset's name)
 *   the UI core   plugin/frp_ui_iface.h: what the plugin's own UI calls (and the extension FRP_UI_EXT_ID for the
 *                 headless tests)
 *   latency       the bridge's pre-roll (plugin/src/bridge.c), constant
 *   tail          how long the sound can go on after the last event: the notes' release, the reverb and the delay
 *                 at their settings (tail_ms below; INFINITE reverb at SUSTAIN / FREEZE and self-oscillating echoes:
 *                 infinite); the host is told when it changes
 *   gui           the plugin's window (plugin/gui_clap.c over plugin/gui_*.c, drawing plugin/ui/frui.c; macOS
 *                 Cocoa, Windows HWND, Linux X11 with the host's posix-fd and timer support): it reaches the core
 *                 only through frp_ui_iface.h's calls
 * Sample rates: 44.1 kHz (the app's own), 48 kHz (the FM-1's USB resampler), any other from 1 to 1536 kHz
 * (a windowed-sinc resampler: plugin/src/bridge.c); outside that the instance refuses to activate and logs why. More
 * than FRP_SLOTS (16) instances in one process: the extra ones play silence and log why. */
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <clap/clap.h>
#include "bridge.h"
#include "../frp_ui_iface.h"
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
    .description = "Physical-model electric piano, vibraphone and marimba",
    .features = FEATURES,
};

#define OQ 1024u                         /* the UI's notes to the host (gestures, values): main thread -> processing */
enum { OQ_VALUE, OQ_BEGIN, OQ_END };
typedef struct { uint8_t type; uint32_t id; double v; } oq_ev_t;
typedef struct frp_core {
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
    _Atomic int processing;              /* between start_processing and stop_processing */
    const clap_host_tail_t *htail;
    double rate;                         /* the activation's (0: not active yet) */
    _Atomic uint32_t tail_said;          /* the tail (ms) the host was last told about; tail_new: a change to tell */
    _Atomic int tail_new;
    uint32_t tail_blocks;
    void *gui;                           /* frp_ui_gui_data */
    oq_ev_t oq[OQ];
    _Atomic uint32_t oqw, oqr;
    /* the UI's own view until the app has taken its writes (a drag must not jump back for a block) */
    int pend_v[FRP_NPARAM];
    double pend_t[FRP_NPARAM];           /* when (monotonic s; 0 none) */
    frp_settings pend_set;
    double pend_set_t;
    uint32_t ui_rev;
    float m_db[2], m_pk[2];              /* the meter's ballistics (frp_ui_meters) */
    double m_pk_t[2], m_t;
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
    info->flags = CLAP_PARAM_IS_STEPPED | (d->stepped ? CLAP_PARAM_IS_ENUM : 0) |
                  (d->id == FRP_ID_ENGINE || d->id == FRP_ID_PRESET ? 0 : CLAP_PARAM_IS_AUTOMATABLE);   /* (ENGINE and
                   * PRESET are choices a user makes, not curves: a host may still set them) */
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
static void p_out(frp_plugin_t *p, const clap_output_events_t *out);
static void p_flush(const clap_plugin_t *plugin, const clap_input_events_t *in, const clap_output_events_t *out)
{
    frp_plugin_t *p = plugin->plugin_data;
    int proc = atomic_load(&p->processing);
    p_events_in(p, in, p->active && proc);   /* processing: the audio thread, between two blocks (the next block
                                          * applies them); else the main thread owns the instance: at once */
    if (p->active && !proc) frp_inst_drain(p->in);
    p_out(p, out);
    if (frp_inst_wants_main(p->in)) p->host->request_callback(p->host);
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

/* ---- the tail: an upper bound for the time the output takes to fall under 1 LSB of 16 bits after the last event,
 * from the settings. Measured with plugin/tests/clap_render (a 6-note fortissimo chord, MIX 127, CLASSIC and HQ, the
 * longer of the two; tests/py/test_plugin_tail.py checks the bound against renders): the notes after their release
 * (RELEASE 127, VIBES) under 2 s; the reverb by TYPE and DECAY (HALL, PLATE; INFINITE below SUSTAIN: up to 59 s;
 * at SUSTAIN / FREEZE it holds: infinite); the delay by FEEDBACK at its longest time (1.49 s: the line; a synced
 * time is taken as the longest), scaled by the time set; ANA from FEEDBACK 115 and TAPE from 114 build up into
 * oscillation (fx.c fx_delay_t): infinite. The parts are added (a delay into the reverb), with a quarter more. */
#define TAIL_INF 0xFFFFFFFFu
#define TAIL_NOTES_MS 2500u
#define DLY_LINE_MS 1486u                /* fx.c DLY_LEN 65536 at 44.1 kHz */
typedef struct { uint8_t x; uint32_t ms; } tpt_t;
static const tpt_t T_HALL[] = { {0, 2780}, {32, 3732}, {64, 4811}, {96, 6364}, {127, 8019} };
static const tpt_t T_PLATE[] = { {0, 1017}, {32, 1678}, {64, 2611}, {96, 4212}, {127, 6924} };
static const tpt_t T_INF[] = { {0, 3299}, {16, 4601}, {32, 6430}, {48, 9489}, {64, 13604}, {80, 19490}, {96, 29736},
                               {105, 38963}, {110, 44092}, {115, 50657}, {119, 59129} };
static const tpt_t T_DLY[] = { {0, 2047}, {16, 6197}, {32, 10474}, {48, 14949}, {64, 26768}, {80, 34188}, {90, 38708},
                               {100, 47562}, {110, 62465}, {113, 89345} };   /* (at the line's length; 111..113 TAPE) */
static uint32_t t_lerp(const tpt_t *t, int n, int x)   /* linear between the points, the last point beyond */
{
    int i;
    if (x <= t[0].x) return t[0].ms;
    for (i = 1; i < n; i++)
        if (x <= t[i].x) return t[i - 1].ms + (uint32_t)((uint64_t)(t[i].ms - t[i - 1].ms) * (uint32_t)(x - t[i - 1].x) / (uint32_t)(t[i].x - t[i - 1].x));
    return t[n - 1].ms;
}
#define NPT(t) ((int)(sizeof t / sizeof t[0]))
static int pv_of(frp_plugin_t *p, clap_id id)
{
    double v = 0.0;
    (void)frp_inst_get(p->in, id, &v);
    return (int)floor(v + 0.5);
}
static uint32_t tail_ms(frp_plugin_t *p)   /* any thread (the app's published values) */
{
    uint32_t rv = 0, dl = 0;
    int t, d, fb;
    if (pv_of(p, FRP_ID_FX_BYPASS)) return TAIL_NOTES_MS * 5u / 4u;
    t = pv_of(p, 59); d = pv_of(p, 51);                      /* P_RV_TYPE (OFF HALL PLATE INFINITE), P_RV_DECAY */
    if (t && pv_of(p, 19)) {                                 /* P_RV_DEPTH: the MIX */
        if (t == 3 && d >= 120) return TAIL_INF;             /* (INFINITE's SUSTAIN and FREEZE: ui.c RV_SUSTAIN_AT) */
        rv = (t == 1 ? t_lerp(T_HALL, NPT(T_HALL), d) : t == 2 ? t_lerp(T_PLATE, NPT(T_PLATE), d)
                                                              : t_lerp(T_INF, NPT(T_INF), d)) + 100u;   /* (+ PRE-DELAY) */
    }
    t = pv_of(p, 13); fb = pv_of(p, 17);                     /* P_DL_SW (OFF DIG ANA TAPE), P_DL_FDBK */
    if (t && pv_of(p, 14)) {                                 /* P_DL_DEPTH: the MIX */
        uint32_t len = DLY_LINE_MS, n;
        if ((t == 2 && fb >= 115) || (t == 3 && fb >= 114)) return TAIL_INF;
        n = fb > 110 && t != 3 ? 62465u : t_lerp(T_DLY, NPT(T_DLY), fb);
        if (!pv_of(p, 16)) {                                 /* P_DL_SYNC FREE: the TIME's text ("357 ms", "1.5 s") */
            const frp_param_t *pp = &FRP_PARAMS[15];         /* P_DL_TIME */
            int v = pv_of(p, 15);
            const char *tx = FRP_TEXT[pp->text0 + (uint32_t)(v < 0 ? 0 : v > pp->hi ? pp->hi : v)];
            double ms = atof(tx) * (strstr(tx, " s") ? 1000.0 : 1.0);
            if (ms > 0.0 && ms < (double)DLY_LINE_MS) len = (uint32_t)ceil(ms);
        }
        dl = (uint32_t)((uint64_t)n * len / DLY_LINE_MS);
        if (dl < T_DLY[0].ms) dl = T_DLY[0].ms;
    }
    return (TAIL_NOTES_MS + rv + dl) * 5u / 4u;
}
static uint32_t tail_get(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    uint32_t ms = tail_ms(p);
    double rate = p->rate > 0.0 ? p->rate : 48000.0;
    if (ms == TAIL_INF) return TAIL_INF;                     /* (>= INT32_MAX: infinite) */
    atomic_store(&p->tail_said, ms);
    return (uint32_t)ceil(ms * rate / 1000.0) + frp_inst_latency(p->in);
}
static const clap_plugin_tail_t EXT_TAIL = { tail_get };
static void tail_watch(frp_plugin_t *p)  /* the audio thread, every 32nd block: a changed tail is told on the main thread */
{
    if (++p->tail_blocks & 31u) return;
    if (tail_ms(p) != atomic_load(&p->tail_said) && !atomic_exchange(&p->tail_new, 1)) p->host->request_callback(p->host);
}

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
static void pend_clear(frp_core *c);
static bool st_load(const clap_plugin_t *plugin, const clap_istream_t *s)
{
    frp_plugin_t *p = plugin->plugin_data;
    char buf[FRP_STATE_MAX * 2];
    int64_t got = 0, r;
    while (got < (int64_t)sizeof buf - 1 && (r = s->read(s, buf + got, (uint64_t)(sizeof buf - 1 - (size_t)got))) > 0) got += r;
    buf[got] = 0;
    if (frp_inst_state_set(p->in, buf)) { say(p, CLAP_LOG_WARNING, "Frodhes: the saved state could not be read"); return false; }
    pend_clear(p);
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
    p->htail = p->host->get_extension(p->host, CLAP_EXT_TAIL);
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
    p->gui = frp_gui_clap_new(p->host, p);   /* (the window's state; the window opens on the host's create) */
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
        snprintf(m, sizeof m, "Frodhes: plays at 1 to 1536 kHz, not at %.0f Hz", rate);
        say(p, CLAP_LOG_ERROR, m);
        return false;
    }
    p->have_last = 0;
    p->active = 1;
    p->rate = rate;
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
static bool pl_start(const clap_plugin_t *plugin) { frp_plugin_t *p = plugin->plugin_data; atomic_store(&p->processing, 1); return true; }
static void pl_stop(const clap_plugin_t *plugin) { frp_plugin_t *p = plugin->plugin_data; atomic_store(&p->processing, 0); }
static void pl_reset(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    frp_inst_reset(p->in);
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
    if (pr->transport && (pr->transport->flags & CLAP_TRANSPORT_HAS_TEMPO) && pr->transport->tempo > 0.0)
        tempo10 = (uint32_t)lround(pr->transport->tempo * 10.0);
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
            } else
                frp_inst_midi(p->in, h->time, (uint8_t)(0x80u | ch), (uint8_t)e->key, h->type == CLAP_EVENT_NOTE_OFF ? vel7(e->velocity) : 0u);
            break;
        }
        case CLAP_EVENT_MIDI: {
            const clap_event_midi_t *e = (const clap_event_midi_t *)h;
            frp_inst_midi(p->in, h->time, e->data[0], e->data[1], e->data[2]);
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
    if (pr->out_events) p_out(p, pr->out_events);
    if (frp_inst_wants_main(p->in)) p->host->request_callback(p->host);   /* (an ENGINE switch: its slot) */
    tail_watch(p);
    return CLAP_PROCESS_CONTINUE;
}
/* the host's view: first the UI's gestures and values (in their order), then the app's own changes (a MIDI CC, a
 * program change, a state, a preset): a value that moved since the last time and is not what the host last sent (a
 * host write reaches the app FRP_LAT_MS later: until then the app's value has not moved, so nothing is echoed) */
static int out_push(const clap_output_events_t *out, uint8_t type, uint32_t id, double v)
{
    if (type == OQ_VALUE) {
        clap_event_param_value_t o;
        memset(&o, 0, sizeof o);
        o.header.size = sizeof o;
        o.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        o.header.type = CLAP_EVENT_PARAM_VALUE;
        o.param_id = id;
        o.note_id = -1; o.port_index = -1; o.channel = -1; o.key = -1;
        o.value = v;
        return out->try_push(out, &o.header);
    } else {
        clap_event_param_gesture_t g;
        memset(&g, 0, sizeof g);
        g.header.size = sizeof g;
        g.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        g.header.type = type == OQ_BEGIN ? CLAP_EVENT_PARAM_GESTURE_BEGIN : CLAP_EVENT_PARAM_GESTURE_END;
        g.param_id = id;
        return out->try_push(out, &g.header);
    }
}
static void p_out(frp_plugin_t *p, const clap_output_events_t *out)
{
    frp_snap_t sn;
    uint32_t r = atomic_load_explicit(&p->oqr, memory_order_relaxed), i;
    if (!out) return;
    while (r != atomic_load_explicit(&p->oqw, memory_order_acquire)) {
        oq_ev_t *e = &p->oq[r & (OQ - 1u)];
        int k = pidx(e->id);
        if (!out_push(out, e->type, e->id, e->v)) break;   /* (the host's list is full: the rest next time) */
        if (e->type == OQ_VALUE && k >= 0) p->host_view[k] = e->v;
        atomic_store_explicit(&p->oqr, ++r, memory_order_release);
    }
    if (frp_inst_snap(p->in, &sn)) return;
    for (i = 0; i < FRP_NPARAM; i++) {
        double v, was;
        if (FRP_PARAMS[i].id == FRP_ID_ENGINE) continue;   /* (the host's own write, or the UI's: never the app's) */
        v = i < FRP_NPARAM_APP ? sn.pv[i] : FRP_PARAMS[i].id == FRP_ID_TUNE ? sn.tune : FRP_PARAMS[i].id == FRP_ID_PRESET ?
            sn.last_fact : FRP_PARAMS[i].id == FRP_ID_SUSTAIN ? sn.sustain : sn.bypass;
        was = i < FRP_NPARAM_APP ? p->last.pv[i] : FRP_PARAMS[i].id == FRP_ID_TUNE ? p->last.tune :
              FRP_PARAMS[i].id == FRP_ID_PRESET ? p->last.last_fact : FRP_PARAMS[i].id == FRP_ID_SUSTAIN ? p->last.sustain :
              p->last.bypass;
        if (!p->have_last || v == was || v == p->host_view[i]) continue;
        if (out_push(out, OQ_VALUE, FRP_PARAMS[i].id, v)) p->host_view[i] = v;
    }
    p->last = sn;
    p->have_last = 1;
}

static const frp_ui_vtable UI_VTABLE;
static const void *pl_ext(const clap_plugin_t *plugin, const char *id)
{
    (void)plugin;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &EXT_PARAMS;
    if (!strcmp(id, CLAP_EXT_AUDIO_PORTS)) return &EXT_AUDIO;
    if (!strcmp(id, CLAP_EXT_NOTE_PORTS)) return &EXT_NOTES;
    if (!strcmp(id, CLAP_EXT_LATENCY)) return &EXT_LATENCY;
    if (!strcmp(id, CLAP_EXT_STATE)) return &EXT_STATE;
    if (!strcmp(id, CLAP_EXT_TAIL)) return &EXT_TAIL;
    if (!strcmp(id, FRP_UI_EXT_ID)) return &UI_VTABLE;
    if (!strcmp(id, CLAP_EXT_GUI)) return frp_gui_clap_ext();
    if (!strcmp(id, FRP_EXT_GUI_SHOT)) return frp_gui_clap_shot_ext();
    if (!strcmp(id, CLAP_EXT_POSIX_FD_SUPPORT)) return frp_gui_clap_fd_ext();     /* (Linux: the X11 view) */
    if (!strcmp(id, CLAP_EXT_TIMER_SUPPORT)) return frp_gui_clap_timer_ext();
    return 0;
}
static void pl_main(const clap_plugin_t *plugin)
{
    frp_plugin_t *p = plugin->plugin_data;
    int t;
    if (frp_inst_main(p->in) < 0) {      /* an ENGINE switch with no slot of that engine free: it stays */
        say(p, CLAP_LOG_WARNING, "Frodhes: no engine slot free for that ENGINE (16 instances use it): it stays");
        if (p->hparams) p->hparams->rescan(p->host, CLAP_PARAM_RESCAN_VALUES);
    }
    if (atomic_exchange(&p->tail_new, 0)) {   /* told once; the host reads the new tail (tail_get) */
        atomic_store(&p->tail_said, tail_ms(p));
        if (p->htail) p->htail->changed(p->host);
    }
    t = atomic_load(&p->rescan_tries);
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

/* ---- the plugin UI's core (plugin/frp_ui_iface.h): the main thread */
#if defined(_WIN32)
#include <windows.h>
static double mono_s(void)               /* (Windows: the performance counter) */
{
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}
#else
static double mono_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec * 1e-9;
}
#endif
#define PEND_S 0.3                       /* the UI's own value wins this long after its write (then the app's) */
frp_core *frp_core_of(const clap_plugin_t *plugin) { return plugin ? plugin->plugin_data : 0; }
void **frp_ui_gui_data(frp_core *c) { return &c->gui; }
int frp_ui_has_slot(const frp_core *c) { return frp_inst_slot(c->in) >= 0; }
static int ui_snap(const frp_core *c, frp_snap_t *sn)
{
    if (frp_inst_snap(c->in, sn) == 0) return 0;
    memset(sn, 0, sizeof *sn);
    sn->pidx = -1; sn->learning = -1; sn->key_vel = 96; sn->touch = 1; sn->tune = 50; sn->bpm10 = 1200;
    sn->sync_div = 9; sn->pgm = 1;
    return -1;
}
uint32_t frp_ui_rev(const frp_core *c)
{
    return frp_inst_rev(c->in) + frp_user_gen() * 7919u + c->ui_rev * 104729u + (uint32_t)frp_inst_engine(c->in) * 31u;
}
static void oq_put(frp_core *c, uint8_t type, uint32_t id, double v)
{
    uint32_t w = atomic_load_explicit(&c->oqw, memory_order_relaxed);
    if (w - atomic_load_explicit(&c->oqr, memory_order_acquire) >= OQ) return;   /* (full: the host misses a note) */
    c->oq[w & (OQ - 1u)] = (oq_ev_t){ type, id, v };
    atomic_store_explicit(&c->oqw, w + 1u, memory_order_release);
}
static void host_flush(frp_core *c)      /* the host calls flush (or process) and gets the notes */
{
    if (c->hparams && c->hparams->request_flush) c->hparams->request_flush(c->host);
}
static void host_values_changed(frp_core *c)   /* the app changed values while not active: the host re-reads them */
{
    if (!c->active && c->hparams) c->hparams->rescan(c->host, CLAP_PARAM_RESCAN_VALUES);
}
int frp_ui_param_hi(uint32_t id) { int i = pidx(id); return i < 0 ? -1 : FRP_PARAMS[i].hi; }
const char *frp_ui_param_name(uint32_t id) { int i = pidx(id); return i < 0 ? "" : FRP_PARAMS[i].name; }
int frp_ui_param_default(const frp_core *c, uint32_t id) { int i = pidx(id); return i < 0 ? -1 : (int)c->def[i]; }
int frp_ui_param_get(const frp_core *c, uint32_t id)
{
    int i = pidx(id);
    double v;
    if (i < 0 || frp_inst_get(c->in, id, &v)) return -1;
    if (c->pend_t[i] > 0.0) {            /* the UI's write until the app shows it (or PEND_S passed) */
        if ((int)v == c->pend_v[i] || mono_s() - c->pend_t[i] >= PEND_S) ((frp_core *)c)->pend_t[i] = 0.0;
        else return c->pend_v[i];
    }
    return (int)v;
}
void frp_ui_param_text(const frp_core *c, uint32_t id, int v, char *val, uint32_t nval, char *unit, uint32_t nunit)
{
    static const char *const UNITS[] = { "Hz", "kHz", "ms", "s", "dB", "c" };
    int i = pidx(id);
    const char *t, *sp;
    (void)c;
    if (nval) val[0] = 0;
    if (nunit) unit[0] = 0;
    if (i < 0 || !nval) return;
    v = v < 0 ? 0 : v > FRP_PARAMS[i].hi ? FRP_PARAMS[i].hi : v;
    t = FRP_TEXT[FRP_PARAMS[i].text0 + (uint32_t)v];
    sp = strrchr(t, ' ');
    if (sp && !FRP_PARAMS[i].stepped)
        for (uint32_t k = 0; k < sizeof UNITS / sizeof UNITS[0]; k++)
            if (!strcmp(sp + 1, UNITS[k])) {
                snprintf(val, nval, "%.*s", (int)(sp - t), t);
                if (nunit) snprintf(unit, nunit, "%s", UNITS[k]);
                return;
            }
    snprintf(val, nval, "%s", t);
}
void frp_ui_param_begin(frp_core *c, uint32_t id) { if (pidx(id) >= 0) { oq_put(c, OQ_BEGIN, id, 0); host_flush(c); } }
void frp_ui_param_end(frp_core *c, uint32_t id) { if (pidx(id) >= 0) { oq_put(c, OQ_END, id, 0); host_flush(c); } }
void frp_ui_param_set(frp_core *c, uint32_t id, int v)
{
    int i = pidx(id);
    if (i < 0) return;
    v = v < 0 ? 0 : v > FRP_PARAMS[i].hi ? FRP_PARAMS[i].hi : v;
    if (frp_ui_param_get(c, id) == v) return;
    c->pend_v[i] = v;
    c->pend_t[i] = mono_s();
    frp_inst_put_now(c->in, FRP_EV_PARAM, id, v);
    oq_put(c, OQ_VALUE, id, v);
    c->ui_rev++;
    host_flush(c);
}

/* presets */
int frp_ui_preset_factory_count(void) { return (int)FRP_NFACTORY; }
int frp_ui_preset_count(const frp_core *c) { (void)c; return (int)FRP_NFACTORY + frp_user_count(); }
int frp_ui_preset_info(const frp_core *c, int idx, frp_preset_info *out)
{
    (void)c;
    memset(out, 0, sizeof *out);
    if (idx < 0) return -1;
    if (idx < (int)FRP_NFACTORY) {
        snprintf(out->tag, sizeof out->tag, "F%02d", idx + 1);
        return frp_fact_info((uint32_t)idx, out->name, sizeof out->name, 0);
    }
    snprintf(out->tag, sizeof out->tag, "U%02d", idx - (int)FRP_NFACTORY + 1);
    out->user = 1;
    return frp_user_info(idx - (int)FRP_NFACTORY, out->name, sizeof out->name);
}
int frp_ui_preset_current(const frp_core *c, frp_preset_info *out, int *edited)
{
    frp_snap_t sn;
    int idx = -1, k;
    ui_snap(c, &sn);
    if (edited) *edited = sn.edited;
    if (sn.pidx >= 0) idx = sn.pidx;
    else if (sn.pname[0] && (k = frp_user_find(sn.pname)) >= 0) idx = (int)FRP_NFACTORY + k;
    if (idx >= 0) return frp_ui_preset_info(c, idx, out) ? -1 : idx;
    memset(out, 0, sizeof *out);
    snprintf(out->name, sizeof out->name, "%s", sn.pname[0] ? sn.pname : "FRODHES");
    return -1;
}
/* a whole new sound asked (a preset, UNDO): the UI's own writes are no longer what it shows (they come after) */
static void pend_clear(frp_core *c) { for (uint32_t i = 0; i < FRP_NPARAM; i++) c->pend_t[i] = 0.0; }
int frp_ui_preset_load(frp_core *c, int idx)
{
    if (idx >= 0 && idx < (int)FRP_NFACTORY) frp_inst_put_now(c->in, FRP_EV_PRESET, 0, idx);
    else {
        uint8_t v[FRP_NPARAM_APP];
        char nm[FRP_PNAME];
        int k = idx - (int)FRP_NFACTORY;
        if (idx < 0 || frp_user_info(k, nm, sizeof nm) || frp_user_values(k, v)) return -1;
        if (frp_inst_user_put(c->in, v, nm, 0)) return -1;
    }
    pend_clear(c);
    c->ui_rev++;
    host_values_changed(c);
    return 0;
}
int frp_ui_preset_step(frp_core *c, int dir)
{
    frp_preset_info cur;
    frp_snap_t sn;
    int n = frp_ui_preset_count(c), idx = frp_ui_preset_current(c, &cur, 0);
    if (idx < 0) { ui_snap(c, &sn); idx = sn.last_fact; }
    idx = ((idx + (dir < 0 ? -1 : 1)) % n + n) % n;
    return frp_ui_preset_load(c, idx) ? -1 : idx;
}
int frp_ui_preset_save(frp_core *c, const char *name)
{
    frp_snap_t sn;
    char nm[FRP_PNAME];
    int k;
    if (ui_snap(c, &sn)) return -1;
    for (uint32_t i = 0; i < FRP_NPARAM_APP; i++)   /* (a write the app has not taken yet: the UI's value) */
        if (c->pend_t[i] > 0.0 && mono_s() - c->pend_t[i] < PEND_S) sn.pv[i] = (uint8_t)c->pend_v[i];
    if ((k = frp_user_save(name, sn.pv)) < 0) return -1;
    frp_user_info(k, nm, sizeof nm);
    (void)frp_inst_user_put(c->in, 0, nm, 1);
    c->ui_rev++;
    return (int)FRP_NFACTORY + k;
}
int frp_ui_preset_delete(frp_core *c, int idx)
{
    if (idx < (int)FRP_NFACTORY) return -1;
    c->ui_rev++;
    return frp_user_delete(idx - (int)FRP_NFACTORY);
}
void frp_ui_preset_rescan(frp_core *c) { frp_user_rescan(); c->ui_rev++; }
const char *frp_ui_preset_dir(void) { return frp_user_dir(); }
int frp_ui_can_undo(const frp_core *c) { frp_snap_t sn; return ui_snap(c, &sn) ? 0 : sn.can_undo; }
int frp_ui_undo(frp_core *c)
{
    if (!frp_ui_can_undo(c)) return -1;
    frp_inst_put_now(c->in, FRP_EV_UNDO, 0, 0);
    pend_clear(c);
    c->ui_rev++;
    host_values_changed(c);
    return 0;
}

/* settings */
void frp_ui_settings_get(const frp_core *c, frp_settings *out)
{
    static const uint8_t VEL_OF_TOUCH[4] = { FRP_VEL_LIGHT, FRP_VEL_MEDIUM, FRP_VEL_HEAVY, FRP_VEL_FIXED };
    frp_snap_t sn;
    ui_snap(c, &sn);
    out->midi_ch = sn.midi_ch;
    out->vel_curve = VEL_OF_TOUCH[sn.touch & 3u];
    out->fixed_vel = sn.key_vel;
    out->pgm_change = sn.pgm;
    out->tempo_src = sn.tempo_src;
    out->bpm10 = sn.bpm10;
    out->sync_all = sn.sync_all;
    out->sync_div = sn.sync_div;
    if (c->pend_set_t > 0.0) {           /* the UI's write until the app shows it (or PEND_S passed) */
        if (!memcmp(out, &c->pend_set, sizeof *out) || mono_s() - c->pend_set_t >= PEND_S) ((frp_core *)c)->pend_set_t = 0.0;
        else *out = c->pend_set;
    }
}
static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
void frp_ui_settings_set(frp_core *c, const frp_settings *s)
{
    frp_settings o, n = *s;
    frp_ui_settings_get(c, &o);
    n.midi_ch = clampi(n.midi_ch, 0, 16); n.vel_curve = clampi(n.vel_curve, 0, 3); n.fixed_vel = clampi(n.fixed_vel, 1, 127);
    n.pgm_change = n.pgm_change != 0; n.tempo_src = clampi(n.tempo_src, 0, 2); n.bpm10 = clampi(n.bpm10, 400, 2400);
    n.sync_all = n.sync_all != 0; n.sync_div = clampi(n.sync_div, 1, 13);
    if (n.midi_ch != o.midi_ch) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_MIDI_CH, n.midi_ch);
    if (n.vel_curve != o.vel_curve) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_VEL, n.vel_curve);
    if (n.fixed_vel != o.fixed_vel) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_FIXVEL, n.fixed_vel);
    if (n.pgm_change != o.pgm_change) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_PGM, n.pgm_change);
    if (n.tempo_src != o.tempo_src) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_TEMPO, n.tempo_src);
    if (n.bpm10 != o.bpm10) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_BPM, n.bpm10);
    if (n.sync_div != o.sync_div) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_SYNC_DIV, n.sync_div);
    if (n.sync_all != o.sync_all) frp_inst_put_now(c->in, FRP_EV_CFG, FRP_CFG_SYNC_ALL, n.sync_all);
    if (n.sync_all != o.sync_all || (n.sync_all && n.sync_div != o.sync_div)) host_values_changed(c);
    c->pend_set = n;
    c->pend_set_t = mono_s();
    c->ui_rev++;
    host_flush(c);
}
int frp_ui_host_bpm10(const frp_core *c) { frp_snap_t sn; ui_snap(c, &sn); return sn.host10; }
int frp_ui_tempo_bpm10(const frp_core *c) { frp_snap_t sn; ui_snap(c, &sn); return sn.tempo10; }

/* MIDI learn: the slot's codes (an app id, FRP_LEARN_OWN + k for 0x10000 + k) */
static int learn_code(uint32_t id)
{
    if (id < FRP_NPARAM_APP) return (int)id;
    if (id > FRP_ID_ENGINE && id < FRP_ID_ENGINE + FRP_NOWN) return (int)(FRP_LEARN_OWN + (id - FRP_ID_ENGINE));
    return -1;
}
static int code_id(int code)
{
    return code < 0 ? -1 : code < (int)FRP_NPARAM_APP ? code : code >= (int)FRP_LEARN_OWN ? (int)(FRP_ID_ENGINE + (uint32_t)(code - (int)FRP_LEARN_OWN)) : -1;
}
void frp_ui_learn_start(frp_core *c, uint32_t id)
{
    int k = learn_code(id);
    if (k < 0 || id == FRP_ID_ENGINE) return;
    frp_inst_put_now(c->in, FRP_EV_LEARN, FRP_LEARN_START, k);
    c->ui_rev++;
}
void frp_ui_learn_cancel(frp_core *c) { frp_inst_put_now(c->in, FRP_EV_LEARN, FRP_LEARN_CANCEL, 0); c->ui_rev++; }
int frp_ui_learning(const frp_core *c) { frp_snap_t sn; ui_snap(c, &sn); return code_id(sn.learning); }
int frp_ui_learned_cc(const frp_core *c, uint32_t id)
{
    frp_snap_t sn;
    int k = learn_code(id);
    if (k < 0 || ui_snap(c, &sn)) return -1;
    for (int cc = 0; cc < 128; cc++) if (sn.learn[cc] == k + 1) return cc;
    return -1;
}
void frp_ui_learn_forget(frp_core *c, uint32_t id)
{
    int k = learn_code(id);
    if (k >= 0) { frp_inst_put_now(c->in, FRP_EV_LEARN, FRP_LEARN_FORGET, k); c->ui_rev++; }
}
void frp_ui_learn_forget_all(frp_core *c) { frp_inst_put_now(c->in, FRP_EV_LEARN, FRP_LEARN_FORGET_ALL, 0); c->ui_rev++; }
int frp_ui_default_cc(uint32_t id) { int i = pidx(id); return i < 0 || !FRP_PARAMS[i].cc ? -1 : FRP_PARAMS[i].cc; }
int frp_ui_cc_target(const frp_core *c, int cc, int *learned)
{
    frp_snap_t sn;
    if (learned) *learned = 0;
    if (cc < 0 || cc > 127) return -1;
    if (!ui_snap(c, &sn) && sn.learn[cc]) { if (learned) *learned = 1; return code_id(sn.learn[cc] - 1); }
    for (uint32_t i = 0; i < FRP_NPARAM_APP; i++) if (FRP_PARAMS[i].cc == cc) return (int)FRP_PARAMS[i].id;
    return -1;
}

/* performance, live values */
void frp_ui_sustain_set(frp_core *c, int on) { frp_ui_param_set(c, FRP_ID_SUSTAIN, on != 0); }
int frp_ui_sustain(const frp_core *c) { frp_snap_t sn; ui_snap(c, &sn); return sn.sustain; }
void frp_ui_fx_bypass_set(frp_core *c, int on) { frp_ui_param_set(c, FRP_ID_FX_BYPASS, on != 0); }
int frp_ui_fx_bypass(const frp_core *c) { return frp_ui_param_get(c, FRP_ID_FX_BYPASS); }
void frp_ui_note(frp_core *c, int key, int vel)
{
    if (key < 0 || key > 127) return;
    vel = clampi(vel, 0, 127);
    frp_inst_put_now(c->in, FRP_EV_UINOTE, (uint32_t)(vel ? 0x90u : 0x80u) | (uint32_t)key << 8 | (uint32_t)vel << 16, 0);
}
void frp_ui_meters(const frp_core *c, float out_db[2], float peak_db[2], float *gr_db)
{
    frp_core *m = (frp_core *)c;         /* (the ballistics are the meter's own state) */
    frp_snap_t sn;
    float pk[2];
    double now = mono_s(), dt = m->m_t > 0.0 ? now - m->m_t : 0.0;
    m->m_t = now;
    frp_inst_meters(m->in, pk);
    for (int k = 0; k < 2; k++) {
        float db = pk[k] > 1e-6f ? 20.0f * log10f(pk[k]) : -120.0f, fall = m->m_db[k] - (float)(dt * 20.0);   /* 20 dB/s */
        m->m_db[k] = db > fall ? db : fall < -120.0f ? -120.0f : fall;
        if (db >= m->m_pk[k] || now - m->m_pk_t[k] > 1.5) { m->m_pk[k] = db > m->m_db[k] ? db : m->m_db[k]; m->m_pk_t[k] = now; }
        out_db[k] = m->m_db[k];
        peak_db[k] = m->m_pk[k];
    }
    ui_snap(c, &sn);
    if (gr_db) *gr_db = (float)sn.gr_q12 * 6.0206f / 4096.0f;
}
void frp_ui_notes(const frp_core *c, uint8_t notes[128]) { frp_inst_notes(c->in, notes); }
int frp_ui_voices(const frp_core *c) { frp_snap_t sn; return ui_snap(c, &sn) ? 0 : sn.voices; }

static const frp_ui_vtable UI_VTABLE = {
    FRP_UI_IFACE_VERSION, frp_core_of, frp_ui_has_slot, frp_ui_rev, frp_ui_param_get, frp_ui_param_default,
    frp_ui_param_text, frp_ui_param_begin, frp_ui_param_set, frp_ui_param_end, frp_ui_preset_count, frp_ui_preset_info,
    frp_ui_preset_current, frp_ui_preset_load, frp_ui_preset_step, frp_ui_preset_save, frp_ui_preset_delete,
    frp_ui_preset_rescan, frp_ui_can_undo, frp_ui_undo, frp_ui_settings_get, frp_ui_settings_set, frp_ui_host_bpm10,
    frp_ui_tempo_bpm10, frp_ui_learn_start, frp_ui_learn_cancel, frp_ui_learning, frp_ui_learned_cc, frp_ui_learn_forget,
    frp_ui_learn_forget_all, frp_ui_cc_target, frp_ui_sustain_set, frp_ui_sustain, frp_ui_fx_bypass_set, frp_ui_fx_bypass,
    frp_ui_note, frp_ui_meters, frp_ui_notes, frp_ui_voices,
};
