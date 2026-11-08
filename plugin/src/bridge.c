/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin instance (plugin/src/bridge.h): the slot table, the host's frames and events onto the app's
 * milliseconds, the 48 kHz resampler, float out.
 *
 * Time. The app always runs at 44.1 kHz on host milliseconds (slot_bridge.h). The output starts with a pre-roll of
 * FRP_LAT_MS (441 frames at 44.1 kHz, 480 at 48 kHz) and then plays the slot's FIFO from the activation on; an event
 * at host frame h (from the activation) is due at the app's millisecond ms0 + h / (frames a ms), the host build's
 * clock for that frame. So the output after the pre-roll is the host build's render of the same events, sample for
 * sample (Gate D), and the latency the host is told is the pre-roll (+ the resampler's delay at 48 kHz). 10 ms
 * keeps every event ahead of the app: the FIFO runs at most 256 frames + a millisecond ahead of the output (the bound
 * needs 8.8 ms at 44.1 kHz, 8.9 ms at 48); an event that is not ahead anyway (a slot reused mid-stream) plays a
 * millisecond later and is counted (late). As on the FM-1, an event acts from the next 256-frame block: up to 5.8 ms
 * after its time (the plan's TIMING study, B-M1).
 *
 * 44.1 kHz: app_audio's own int32 samples, exact as float (x / 32768, no clip: the host's mixer has the headroom).
 * 48 kHz: the FM-1's own USB resampler, firmware/src/uac_fir.h (160 / 147, 12 taps a phase, the knee and the 16-bit
 * clamp of usb.c uac_tap48), so the stream is the FM-1's USB stream at 48 kHz bit for bit.
 * Any other rate (1 to 768 kHz): a windowed-sinc resampler in float (RS_HALF zero crossings, a Kaiser window, the
 * cutoff 0.96 x the lower Nyquist), read at the 44.1 kHz time of each output frame, so it adds no delay of its own:
 * the pre-roll grows by the filter's reach instead (FRP_LAT_MS + RS reach, rounded up to a ms), and the latency the
 * host is told is that pre-roll at its rate.
 *
 * ENGINE. An instance holds a slot of each pool (bridge.h): HQ (16 voices, the float paths) and CLASSIC (the FM-1's
 * build: 10 voices). Only the one ENGINE names is stepped. A switch hands the sound over (the state, the slot's own
 * JSON, into a fresh start of the other slot) at the next block; the old slot gets every note let go and is mixed in
 * until it is quiet (its tails ring out), then rests. The new slot's clock is re-based so that events keep their
 * FRP_LAT_MS lead and the latency stays the same.
 *
 * The UI queue. The plugin UI writes from the main thread while the audio thread owns the slot: its events wait in
 * the instance's own queue (one producer, one consumer) and the audio thread moves them into the slot at the start
 * of its next block (frp_inst_render, or frp_inst_drain from a flush outside processing).
 *
 * The user presets: one file a preset (FRP_USER_EXT) in the user's folder (frp_user_dir), written atomically (a
 * temporary file, then rename) on the main thread; the list is read at the first use and after every change. Program
 * changes 16..31 load the first 16 of them on the audio thread from a copy kept for it (a sequence lock). */
#include <dirent.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include "bridge.h"
#include "../../firmware/src/uac_fir.h"

static const frp_slot_api_t *const SLOT[2u * FRP_SLOTS] = {
    &frp_slot_0, &frp_slot_1, &frp_slot_2, &frp_slot_3, &frp_slot_4, &frp_slot_5, &frp_slot_6, &frp_slot_7,
    &frp_slot_8, &frp_slot_9, &frp_slot_10, &frp_slot_11, &frp_slot_12, &frp_slot_13, &frp_slot_14, &frp_slot_15,
    &frp_slot_16, &frp_slot_17, &frp_slot_18, &frp_slot_19, &frp_slot_20, &frp_slot_21, &frp_slot_22, &frp_slot_23,
    &frp_slot_24, &frp_slot_25, &frp_slot_26, &frp_slot_27, &frp_slot_28, &frp_slot_29, &frp_slot_30, &frp_slot_31,
};
static atomic_int slot_used[2u * FRP_SLOTS];
#define POOL_OF(engine) ((engine) == FRP_ENGINE_HQ ? FRP_POOL_HQ : FRP_POOL_CLASSIC)
#define ENGINE_OF(pool) ((pool) == FRP_POOL_HQ ? FRP_ENGINE_HQ : FRP_ENGINE_CLASSIC)
#define UQ 512u                          /* the UI queue (power of two) */
#define TAIL_MAX_MS 30000u               /* a tail rests after this long even if it is not quiet (an INFINITE reverb) */

#define CHUNK 256u                       /* frames read from the FIFO at a time */
#define UAC_KNEE 24576                   /* usb.c UAC_KNEE: above it the excess is halved, before the clamp */
#define RS_HALF 24                       /* the other rates: zero crossings each side of the sinc */
#define RS_OS 512                        /* table points a zero crossing (linear between them) */
#define RS_BETA 9.0                      /* the Kaiser window (about -90 dB side lobes) */
#define RS_N 4096u                       /* the input ring (power of 2): 2 x the reach at 1 kHz + a chunk fit */
#define RS_MIN 1000.0
#define RS_MAX 768000.0
enum { M441, M48, MANY };

typedef struct { uint8_t kind; uint32_t a; int32_t v; } uq_ev_t;
struct frp_inst {
    int slot;                            /* the slot playing (-1 none) */
    const frp_slot_api_t *s;             /* its table */
    int slots[2];                        /* the instance's slot of each pool (-1 none) */
    const frp_slot_api_t *sp[2];
    int cur;                             /* the pool playing */
    int tail;                            /* the pool ringing out after a switch, -1 none */
    uint32_t tail_ms0;                   /* its ms when it started */
    _Atomic int want;                    /* the ENGINE asked (FRP_ENGINE_*): the audio side follows at its next block */
    _Atomic int sj_flag;                 /* a state for the switch (0 none, 1 being written, 2 ready) */
    char sj[FRP_STATE_MAX];
    char xbuf[FRP_STATE_MAX];            /* the audio side's hand-over buffer */
    int32_t tbuf[2u * 256u];
    uq_ev_t uq[UQ];
    _Atomic uint32_t uqw, uqr;
    _Atomic uint32_t peak[2];            /* the output's peak since the last read (float bits) */
    _Atomic uint8_t notes[128];          /* the keys held (velocity) */
    int active, r48, mode;
    uint64_t pos;                        /* host frames since the activation */
    uint32_t ms0;                        /* the slot's ms at the activation */
    uint32_t pre;                        /* pre-roll frames still to play */
    uint32_t late;
    int engine;                          /* ENGINE as last set (the slot's published value wins once it runs) */
    /* 48 kHz: the resampler's history (12 frames, oldest first), its phase, the outputs not yet handed out */
    int16_t hl[UAC_FIR_N], hr[UAC_FIR_N];
    uint32_t ph;
    int16_t ol[2], or_[2];
    uint32_t on, oi;
    int32_t buf[2u * CHUNK];
    /* the other rates: the rate, the cutoff (x the input rate's Nyquist), the reach in input frames, the pre-roll in
     * ms, the input frames read so far and the ring holding them */
    double rate, fc, reach;
    uint32_t lat_ms;
    int64_t kin;
    float rl[RS_N], rr[RS_N];
};

static float rs_tab[RS_HALF * RS_OS + 2];
static pthread_once_t rs_once = PTHREAD_ONCE_INIT;
static double bessel_i0(double x)
{
    double s = 1.0, t = 1.0;
    for (int k = 1; k < 40; k++) { t *= (x / (2.0 * k)) * (x / (2.0 * k)); s += t; }
    return s;
}
static void rs_init(void)                /* sinc(u) x Kaiser(u / RS_HALF), u = 0 .. RS_HALF */
{
    double i0b = bessel_i0(RS_BETA);
    for (int i = 0; i <= RS_HALF * RS_OS + 1; i++) {
        double u = (double)i / RS_OS, w = u / RS_HALF, si = u == 0.0 ? 1.0 : sin(M_PI * u) / (M_PI * u);
        rs_tab[i] = w >= 1.0 ? 0.0f : (float)(si * bessel_i0(RS_BETA * sqrt(1.0 - w * w)) / i0b);
    }
}

static int slot_claim(int pool)
{
    int i, pass, i0 = pool == FRP_POOL_HQ ? 0 : (int)FRP_SLOTS;
    for (pass = 0; pass < 2; pass++)     /* a slot never used first (a clean app), then a used one */
        for (i = i0; i < i0 + (int)FRP_SLOTS; i++) {
            int z = 0;
            if (pass == 0 && SLOT[i]->booted()) continue;
            if (atomic_compare_exchange_strong(&slot_used[i], &z, 1)) return i;
        }
    return -1;
}

frp_inst_t *frp_inst_new(void)
{
    frp_inst_t *in = calloc(1, sizeof *in);
    int k;
    if (!in) return 0;
    in->engine = FRP_ENGINE_HQ;          /* (the plugin's default; params_map.h FRP_ID_ENGINE) */
    atomic_store(&in->want, in->engine);
    in->tail = -1;
    for (k = 0; k < 2; k++) {            /* one slot of each pool, both booted now (the main thread) */
        int sl = slot_claim(k);
        in->slots[k] = sl;
        in->sp[k] = 0;
        if (sl < 0) continue;
        if (SLOT[sl]->booted()) SLOT[sl]->reuse(ENGINE_OF(k));
        else if (SLOT[sl]->boot(ENGINE_OF(k))) { atomic_store(&slot_used[sl], 0); in->slots[k] = -1; continue; }
        in->sp[k] = SLOT[sl];
    }
    if (!in->sp[0] || !in->sp[1]) {      /* (both or none: an instance never plays only one path) */
        for (k = 0; k < 2; k++) if (in->slots[k] >= 0) atomic_store(&slot_used[in->slots[k]], 0);
        in->slots[0] = in->slots[1] = -1;
        in->sp[0] = in->sp[1] = 0;
    }
    in->cur = POOL_OF(in->engine);
    in->s = in->sp[in->cur];
    in->slot = in->s ? in->slots[in->cur] : -1;
    return in;
}
void frp_inst_free(frp_inst_t *in)
{
    if (!in) return;
    for (int k = 0; k < 2; k++) if (in->slots[k] >= 0) atomic_store(&slot_used[in->slots[k]], 0);
    free(in);
}
int frp_inst_slot(const frp_inst_t *in) { return in ? in->slot : -1; }
int frp_inst_engine(const frp_inst_t *in) { return in->engine; }

/* the host frames since the activation as the app's milliseconds (due_ms's clock, without ms0) */
static uint32_t pos_ms(const frp_inst_t *in, uint64_t h)
{
    return (uint32_t)(in->mode == M48 ? h / 48u : in->mode == M441 ? h * 10u / 441u : (uint64_t)floor((double)h * 1000.0 / in->rate));
}
/* the ENGINE switch to pool np: the sound (json, or the playing slot's own state) into a fresh start of the other
 * slot. Inactive (main thread): at once, the old slot cleared. Active (the thread that processes): the new slot's
 * clock re-based, the old one rings out as the tail. */
static void do_swap(frp_inst_t *in, int np, const char *json)
{
    const frp_slot_api_t *o = in->s, *n = in->sp[np];
    if (!o || !n || np == in->cur) return;
    if (!json) {                         /* what the old slot was given and has not applied yet (the same block's
                                          * parameter changes, before or after the switch) goes with the sound */
        o->drain();
        json = o->state_get(in->xbuf, (int)sizeof in->xbuf) >= 0 ? in->xbuf : 0;
    }
    n->reuse(ENGINE_OF(np));
    if (json) (void)n->state_set(json);
    if (!in->active) {
        n->drain();
        (void)o->put(FRP_EV_RESET, 0, 0, 0);
        o->drain();
        in->tail = -1;
    } else {
        n->drain();                      /* (the hand-over applied before the first step: no time passes) */
        in->ms0 = n->now() + FRP_LAT_MS - pos_ms(in, in->pos);
        (void)o->put(FRP_EV_RELEASE, o->now() + FRP_LAT_MS + 1u, 0, 0);   /* (after what it was already given) */
        in->tail = in->cur;
        in->tail_ms0 = o->now();
    }
    in->cur = np;
    in->s = n;
    in->slot = in->slots[np];
}
static void apply_ev(frp_inst_t *in, uint8_t kind, uint32_t a, int32_t v);
/* the processing side, at a block's start: the UI's events, a state's switch, a switch asked */
static void block_start(frp_inst_t *in)
{
    uint32_t r = atomic_load_explicit(&in->uqr, memory_order_relaxed);
    int np;
    while (r != atomic_load_explicit(&in->uqw, memory_order_acquire)) {
        uq_ev_t *e = &in->uq[r & (UQ - 1u)];
        apply_ev(in, e->kind, e->a, e->v);
        atomic_store_explicit(&in->uqr, ++r, memory_order_release);
    }
    np = POOL_OF(atomic_load_explicit(&in->want, memory_order_acquire));
    if (np != in->cur) {
        int two = 2;
        int have = atomic_compare_exchange_strong(&in->sj_flag, &two, 3);
        do_swap(in, np, have ? in->sj : 0);
        if (have) atomic_store(&in->sj_flag, 0);
    } else {                             /* (a state of the same pool that waited for a switch: the slot takes it) */
        int two = 2;
        if (atomic_compare_exchange_strong(&in->sj_flag, &two, 3)) {
            if (in->s) (void)in->s->state_set(in->sj);
            atomic_store(&in->sj_flag, 0);
        }
    }
}

int frp_inst_activate(frp_inst_t *in, double rate)
{
    if (!(rate >= RS_MIN && rate <= RS_MAX)) return -1;
    in->mode = rate == 44100.0 ? M441 : rate == 48000.0 ? M48 : MANY;
    in->r48 = in->mode == M48;
    in->rate = rate;
    in->pos = 0;
    in->pre = in->r48 ? 48u * FRP_LAT_MS : 441u * FRP_LAT_MS / 10u;
    if (in->mode == MANY) {
        pthread_once(&rs_once, rs_init);
        in->fc = 0.96 * (rate < FRP_FS ? rate / FRP_FS : 1.0);
        in->reach = RS_HALF / in->fc;
        in->lat_ms = FRP_LAT_MS + (uint32_t)ceil(in->reach * 1000.0 / FRP_FS);
        in->pre = 0;                     /* (the pre-roll is in the read position: frp_inst_render) */
        in->kin = 0;
        memset(in->rl, 0, sizeof in->rl); memset(in->rr, 0, sizeof in->rr);
    }
    in->late = 0;
    memset(in->hl, 0, sizeof in->hl); memset(in->hr, 0, sizeof in->hr);
    in->ph = 0; in->on = in->oi = 0;
    in->tail = -1;                       /* (a tail from before: it rests) */
    if (in->s) {
        uint32_t a;
        block_start(in);                 /* (what the UI queued, a switch asked: the instance is not active yet) */
        for (int k = 0; k < 2; k++) {
            in->sp[k]->drain();          /* (what the main thread queued while inactive) */
            while ((a = in->sp[k]->avail()) != 0) in->sp[k]->read(in->buf, a < CHUNK ? a : CHUNK);   /* (frames from before) */
        }
        in->ms0 = in->s->now();
    }
    in->active = 1;
    return 0;
}
void frp_inst_deactivate(frp_inst_t *in) { in->active = 0; }
uint32_t frp_inst_latency(const frp_inst_t *in)
{
    /* the resampler: a symmetric prototype of 160 x 12 taps, its centre 959.5 / 160 = 6.0 input frames = 6.5 out */
    if (in->mode == MANY) return (uint32_t)ceil(in->lat_ms * in->rate / 1000.0);
    return in->r48 ? 48u * FRP_LAT_MS + 7u : 441u * FRP_LAT_MS / 10u;
}
void frp_inst_reset(frp_inst_t *in)
{
    if (!in->s) return;
    (void)in->s->put(FRP_EV_RESET, 0, 0, 0);
    if (in->tail >= 0) { (void)in->sp[in->tail]->put(FRP_EV_RESET, 0, 0, 0); in->tail = -1; }
    for (int k = 0; k < 128; k++) atomic_store_explicit(&in->notes[k], 0, memory_order_relaxed);
}

static uint32_t due_ms(frp_inst_t *in, uint32_t time)
{
    uint64_t h = in->pos + time;
    uint32_t t = in->ms0 + (uint32_t)(in->mode == M48 ? h / 48u : in->mode == M441 ? h * 10u / 441u
                                                                     : (uint64_t)floor((double)h * 1000.0 / in->rate)),
             now = in->s->now();
    if ((int32_t)(t - now) <= 0) {
        if (!(t == now && now == in->ms0)) in->late++;   /* (an event at the activation's own ms: the first step's) */
        t = now + 1u;
    }
    return t;
}
static void note_track(frp_inst_t *in, uint8_t st, uint8_t d1, uint8_t d2)
{
    uint8_t t = st & 0xF0u;
    if (t == 0x90u && d2) atomic_store_explicit(&in->notes[d1 & 127u], d2, memory_order_relaxed);
    else if (t == 0x80u || t == 0x90u) atomic_store_explicit(&in->notes[d1 & 127u], 0, memory_order_relaxed);
    else if (t == 0xB0u && (d1 == 120u || d1 == 123u))
        for (int k = 0; k < 128; k++) atomic_store_explicit(&in->notes[k], 0, memory_order_relaxed);
}
void frp_inst_midi(frp_inst_t *in, uint32_t time, uint8_t st, uint8_t d1, uint8_t d2)
{
    if (!in->s || st < 0x80u || st == 0xF0u || st == 0xF7u) return;
    note_track(in, st, d1, d2);
    (void)in->s->put(FRP_EV_MIDI, due_ms(in, time), (uint32_t)st | (uint32_t)(d1 & 127u) << 8 | (uint32_t)(d2 & 127u) << 16, 0);
}
static int32_t param_round(uint32_t idx, double v)
{
    double hi = FRP_PARAMS[idx].hi;
    if (!(v >= 0.0)) v = 0.0;            /* (NaN too) */
    if (v > hi) v = hi;
    return (int32_t)floor(v + 0.5);
}
static int param_index(uint32_t id)
{
    if (id < FRP_NPARAM_APP) return (int)id;
    for (uint32_t i = FRP_NPARAM_APP; i < FRP_NPARAM; i++) if (FRP_PARAMS[i].id == id) return (int)i;
    return -1;
}
static void param_put(frp_inst_t *in, uint32_t at, uint32_t id, double value)
{
    int i = param_index(id);
    if (i < 0) return;
    if (id == FRP_ID_ENGINE) {           /* the slot of that pool plays from the next block (inactive: at once) */
        in->engine = param_round((uint32_t)i, value) ? FRP_ENGINE_HQ : FRP_ENGINE_CLASSIC;
        atomic_store(&in->want, in->engine);
        if (!in->active) block_start(in);
        return;
    }
    if (in->s) (void)in->s->put(FRP_EV_PARAM, at, id, param_round((uint32_t)i, value));
}
void frp_inst_param(frp_inst_t *in, uint32_t time, uint32_t id, double value)
{
    param_put(in, in->s ? due_ms(in, time) : 0u, id, value);
}
void frp_inst_param_now(frp_inst_t *in, uint32_t id, double value)
{
    param_put(in, 0u, id, value);
    if (in->s && !in->active) in->s->drain();
}
/* an event for the slot playing, at once (the processing side), or the UI's (the main thread: below) */
static void apply_ev(frp_inst_t *in, uint8_t kind, uint32_t a, int32_t v)
{
    if (kind == FRP_EV_PARAM) { param_put(in, 0u, a, v); return; }
    if (!in->s) return;
    if (kind == FRP_EV_UINOTE) note_track(in, (uint8_t)a, (uint8_t)(a >> 8), (uint8_t)(a >> 16));
    (void)in->s->put(kind, 0u, a, v);
}
void frp_inst_put_now(frp_inst_t *in, int kind, uint32_t a, int32_t v)
{
    if (!in->active) {                   /* (the main thread owns the slots: applied now) */
        apply_ev(in, (uint8_t)kind, a, v);
        if (in->s) in->s->drain();
        return;
    }
    {
        uint32_t w = atomic_load_explicit(&in->uqw, memory_order_relaxed);
        if (w - atomic_load_explicit(&in->uqr, memory_order_acquire) >= UQ) return;   /* (full: dropped) */
        in->uq[w & (UQ - 1u)] = (uq_ev_t){ (uint8_t)kind, a, v };
        atomic_store_explicit(&in->uqw, w + 1u, memory_order_release);
    }
}
void frp_inst_drain(frp_inst_t *in)
{
    block_start(in);
    if (in->s) in->s->drain();
}
int frp_inst_user_put(frp_inst_t *in, const uint8_t *vals, const char *name, int saved)
{
    int rc;
    if (!in->s) return -1;
    rc = in->s->user_put(vals, name, saved);
    if (!rc && !in->active) in->s->drain();
    return rc;
}
int frp_inst_get(const frp_inst_t *in, uint32_t id, double *value)
{
    frp_snap_t sn;
    int i = param_index(id);
    if (i < 0) return -1;
    if (id == FRP_ID_ENGINE) { *value = in->engine; return 0; }
    if (!in->s) { *value = FRP_PARAMS[i].def; return 0; }
    in->s->snapshot(&sn);
    *value = id < FRP_NPARAM_APP ? sn.pv[id] : id == FRP_ID_TUNE ? sn.tune : id == FRP_ID_PRESET ? sn.last_fact :
             id == FRP_ID_SUSTAIN ? sn.sustain : sn.bypass;
    return 0;
}
int frp_inst_state_get(const frp_inst_t *in, char *buf, int n) { return in->s ? in->s->state_get(buf, n) : -1; }
static int json_engine(const char *j)    /* the state's "engine" ("0" / "1"), -1 none */
{
    const char *p = j ? strstr(j, "\"engine\":\"") : 0;
    if (!p) return -1;
    p += 10;
    return (p[0] == '0' || p[0] == '1') && p[1] == '"' ? p[0] - '0' : -1;
}
int frp_inst_state_set(frp_inst_t *in, const char *json)
{
    int e = json_engine(json), np, zero = 0;
    if (!in->s || !json) return -1;
    np = e < 0 ? in->cur : POOL_OF(e);
    if (e >= 0) in->engine = e;
    if (!in->active) {                   /* (the main thread owns the slots) */
        atomic_store(&in->want, in->engine);
        if (np != in->cur) {
            if (in->sp[np]->state_set(json)) return -1;   /* (parsed first: a state it cannot read changes nothing) */
            do_swap(in, np, json);
            return 0;
        }
        if (in->s->state_set(json)) return -1;
        in->s->drain();
        return 0;
    }
    if (np == in->cur && atomic_load(&in->want) == in->engine && atomic_load(&in->sj_flag) == 0)
        return in->s->state_set(json);   /* (the slot's own mailbox) */
    /* a switch with it: the processing side takes the state at its next block (block_start) */
    if (strlen(json) >= sizeof in->sj || !atomic_compare_exchange_strong(&in->sj_flag, &zero, 1)) return -1;
    strcpy(in->sj, json);
    atomic_store(&in->sj_flag, 2);
    atomic_store(&in->want, in->engine);
    return 0;
}
uint32_t frp_inst_rev(const frp_inst_t *in)
{
    frp_snap_t sn;
    if (!in->s) return 0;
    in->s->snapshot(&sn);
    return sn.rev + (uint32_t)in->slot * 0x10000u;   /* (a switch: another slot's counter) */
}
int frp_inst_snap(const frp_inst_t *in, frp_snap_t *out)
{
    if (!in->s) return -1;
    in->s->snapshot(out);
    return 0;
}
void frp_inst_stats(const frp_inst_t *in, frp_stats_t *st, uint32_t *late)
{
    if (in->s) in->s->stats(st); else memset(st, 0, sizeof *st);
    *late = in->late;
}
int frp_fact_info(uint32_t i, char *name, uint32_t n, uint8_t *vals) { return SLOT[0]->fact_info(i, name, n, vals); }
static float bits_f(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }
static uint32_t f_bits(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }
void frp_inst_meters(frp_inst_t *in, float peak[2])
{
    for (int c = 0; c < 2; c++) peak[c] = bits_f(atomic_exchange_explicit(&in->peak[c], 0u, memory_order_relaxed));
}
void frp_inst_notes(const frp_inst_t *in, uint8_t notes[128])
{
    for (int k = 0; k < 128; k++) notes[k] = atomic_load_explicit(&((frp_inst_t *)in)->notes[k], memory_order_relaxed);
}

/* n frames out of the slot (stepping it as needed), n <= CHUNK */
static void pull(frp_inst_t *in, uint32_t n)
{
    while (in->s->avail() < n) in->s->step();
    in->s->read(in->buf, n);
    if (in->tail >= 0) {                 /* the other slot rings out after a switch: mixed in until it is quiet */
        const frp_slot_api_t *t = in->sp[in->tail];
        uint32_t k, ran;
        while (t->avail() < n) t->step();
        t->read(in->tbuf, n);
        for (k = 0; k < 2u * n; k++) in->buf[k] += in->tbuf[k];
        ran = t->now() - in->tail_ms0;
        if (ran > FRP_LAT_MS + 50u && (ran & 31u) == 0u) {
            frp_snap_t sn;
            t->snapshot(&sn);
            if (sn.quiet || ran > TAIL_MAX_MS) in->tail = -1;
        }
    }
}
static int16_t s16(int32_t v) { return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
static int32_t knee_clamp(int32_t v)     /* usb.c uac_tap48: the knee above UAC_KNEE, then the 16-bit clamp */
{
    if ((uint32_t)(v + UAC_KNEE) > 2u * UAC_KNEE)
        v = v > 0 ? UAC_KNEE + ((v - UAC_KNEE) >> 1) : -UAC_KNEE - ((-v - UAC_KNEE) >> 1);
    return v < -32768 ? -32768 : v > 32767 ? 32767 : v;
}
/* one 44.1 kHz frame into the resampler: its 48 kHz outputs (1 or 2) into ol / or_ */
static void rs_push(frp_inst_t *in, int32_t l, int32_t r)
{
    memmove(in->hl, in->hl + 1, (UAC_FIR_N - 1u) * sizeof in->hl[0]);
    memmove(in->hr, in->hr + 1, (UAC_FIR_N - 1u) * sizeof in->hr[0]);
    in->hl[UAC_FIR_N - 1u] = s16(l);
    in->hr[UAC_FIR_N - 1u] = s16(r);
    in->on = in->oi = 0;
    while (in->ph < UAC_FIR_L) {
        int64_t al = 1 << (UAC_FIR_Q - 1), ar = 1 << (UAC_FIR_Q - 1);
        uint32_t k;
        for (k = 0; k < UAC_FIR_N; k++) {
            int32_t c = in->ph < UAC_FIR_L / 2u ? UAC_FIR[in->ph][k] : UAC_FIR[UAC_FIR_L - 1u - in->ph][UAC_FIR_N - 1u - k];
            al += (int64_t)c * in->hl[k];
            ar += (int64_t)c * in->hr[k];
        }
        in->ol[in->on] = (int16_t)knee_clamp((int32_t)(al >> UAC_FIR_Q));
        in->or_[in->on] = (int16_t)knee_clamp((int32_t)(ar >> UAC_FIR_Q));
        in->on++;
        in->ph += UAC_FIR_M;
    }
    in->ph -= UAC_FIR_L;
}

/* the other rates: output frame j (from the activation) is the 44.1 kHz signal at input frame
 * t = (j - latency) x 44100 / rate, the latency the host is told (whole output frames, at least the pre-roll in ms);
 * the input frames before the first one read are silence */
static void rs_render(frp_inst_t *in, float *l, float *r, uint32_t n)
{
    const double step = FRP_FS / in->rate, sc = 1.0 / 32768.0, os = in->fc * RS_OS;
    const double pre = (double)frp_inst_latency(in) * step;
    for (uint32_t i = 0; i < n; i++) {
        double t = (double)(in->pos + i) * step - pre, al = 0.0, ar = 0.0, ws = 0.0;
        int64_t k0 = (int64_t)ceil(t - in->reach), k1 = (int64_t)floor(t + in->reach), k;
        while (in->kin <= k1) {          /* the input frames up to k1 */
            int64_t want = k1 - in->kin + 1;
            uint32_t c = want < (int64_t)CHUNK ? (uint32_t)want : CHUNK, q;
            pull(in, c);
            for (q = 0; q < c; q++) {
                in->rl[(uint64_t)(in->kin + q) & (RS_N - 1u)] = (float)in->buf[2u * q];
                in->rr[(uint64_t)(in->kin + q) & (RS_N - 1u)] = (float)in->buf[2u * q + 1u];
            }
            in->kin += c;
        }
        for (k = k0; k <= k1; k++) {
            double x = fabs((double)k - t) * os, w;
            int32_t xi = (int32_t)x;
            if (xi >= RS_HALF * RS_OS) continue;
            w = rs_tab[xi] + (x - xi) * (rs_tab[xi + 1] - rs_tab[xi]);
            ws += w;
            if (k < 0) continue;         /* (before the first frame: silence) */
            al += w * in->rl[(uint64_t)k & (RS_N - 1u)];
            ar += w * in->rr[(uint64_t)k & (RS_N - 1u)];
        }
        if (ws > 0.0) { al /= ws; ar /= ws; }   /* (unity gain at DC whatever the phase) */
        l[i] = (float)(al * sc); r[i] = (float)(ar * sc);
    }
}

static void meter(frp_inst_t *in, const float *l, const float *r, uint32_t n)   /* the block's peak into the max */
{
    float pl = 0.0f, pr = 0.0f;
    for (uint32_t i = 0; i < n; i++) { float a = fabsf(l[i]), b = fabsf(r[i]); if (a > pl) pl = a; if (b > pr) pr = b; }
    if (pl > bits_f(atomic_load_explicit(&in->peak[0], memory_order_relaxed))) atomic_store_explicit(&in->peak[0], f_bits(pl), memory_order_relaxed);
    if (pr > bits_f(atomic_load_explicit(&in->peak[1], memory_order_relaxed))) atomic_store_explicit(&in->peak[1], f_bits(pr), memory_order_relaxed);
}
void frp_inst_render(frp_inst_t *in, float *l, float *r, uint32_t n, uint32_t tempo_bpm10)
{
    const float sc = 1.0f / 32768.0f;
    uint32_t i = 0;
    if (!in->s || !in->active) { memset(l, 0, n * sizeof *l); memset(r, 0, n * sizeof *r); return; }
    block_start(in);
    in->s->set_tempo(tempo_bpm10);
    if (in->tail >= 0) in->sp[in->tail]->set_tempo(tempo_bpm10);
    if (in->mode == MANY) { rs_render(in, l, r, n); in->pos += n; meter(in, l, r, n); return; }
    for (; i < n && in->pre; i++, in->pre--) l[i] = r[i] = 0.0f;
    if (!in->r48) {
        while (i < n) {
            uint32_t c = n - i < CHUNK ? n - i : CHUNK, k;
            pull(in, c);
            for (k = 0; k < c; k++) { l[i + k] = (float)in->buf[2u * k] * sc; r[i + k] = (float)in->buf[2u * k + 1u] * sc; }
            i += c;
        }
    } else {
        while (i < n) {
            if (in->oi == in->on) {      /* the next 44.1 kHz frame in: 1 or 2 outputs (13 of 147 frames give 2) */
                pull(in, 1u);
                rs_push(in, in->buf[0], in->buf[1]);
                continue;
            }
            l[i] = (float)in->ol[in->oi] * sc; r[i] = (float)in->or_[in->oi] * sc;
            in->oi++; i++;
        }
    }
    in->pos += n;
    meter(in, l, r, n);
}

/* ---- the user presets: one file a preset, FRP_USER_EXT, in frp_user_dir():
 *   {"frodhes_preset":"1","name":"MY BALLAD","n":"67","p":"<n values, hex>"}
 * n is the app's parameter count when it was saved: a later build's new parameters load their defaults, values out of
 * range load the default. The list is sorted by name (case-blind); a name is 1..24 printable characters. */
#define FRP_USER_EXT ".frpreset"
typedef struct { char name[FRP_PNAME]; char file[96]; } user_t;
static pthread_mutex_t user_mu = PTHREAD_MUTEX_INITIALIZER;
static user_t *users;
static int n_users, users_read;
static _Atomic uint32_t user_gen;
static char user_dir_s[1024];
static struct {                          /* the first 16 for program changes 16..31 (the audio thread: frp_user_get) */
    _Atomic uint32_t seq;
    int n;
    uint8_t v[16][FRP_NPARAM_APP];
    char name[16][FRP_PNAME];
} pc_cache;

const char *frp_user_dir(void)
{
    if (!user_dir_s[0]) {
        const char *e = getenv("FRODHES_PRESET_DIR"), *h = getenv("HOME");
#if defined(_WIN32)
        const char *ad = getenv("APPDATA");
        if (e && *e) snprintf(user_dir_s, sizeof user_dir_s, "%s", e);
        else snprintf(user_dir_s, sizeof user_dir_s, "%s\\Frodhes\\Presets", ad ? ad : ".");
#elif defined(__APPLE__)
        if (e && *e) snprintf(user_dir_s, sizeof user_dir_s, "%s", e);
        else snprintf(user_dir_s, sizeof user_dir_s, "%s/Library/Application Support/Frodhes/Presets", h ? h : ".");
#else
        const char *x = getenv("XDG_DATA_HOME");
        if (e && *e) snprintf(user_dir_s, sizeof user_dir_s, "%s", e);
        else if (x && *x) snprintf(user_dir_s, sizeof user_dir_s, "%s/frodhes/presets", x);
        else snprintf(user_dir_s, sizeof user_dir_s, "%s/.local/share/frodhes/presets", h ? h : ".");
#endif
    }
    return user_dir_s;
}
static int mkdirs(const char *path)
{
    char t[1024];
    size_t n = strlen(path);
    if (n >= sizeof t) return -1;
    memcpy(t, path, n + 1);
    for (size_t i = 1; i <= n; i++)
        if (t[i] == '/' || t[i] == 0) {
            char c = t[i];
            t[i] = 0;
            if (mkdir(t, 0755) && errno != EEXIST) return -1;
            t[i] = c;
        }
    return 0;
}
static void name_clean(char *out, const char *in)   /* printable, no quote / backslash, trimmed, 1..24 */
{
    size_t k = 0;
    while (in && (*in == ' ' || *in == '\t')) in++;
    for (; in && *in && k < 24u; in++) out[k++] = (*in == '"' || *in == '\\' || (unsigned char)*in < 32u || (unsigned char)*in > 126u) ? '_' : *in;
    while (k && out[k - 1] == ' ') k--;
    out[k] = 0;
}
static void file_of(char *out, size_t n, const char *name)   /* a file name for a preset name */
{
    size_t k = 0;
    char t[64];
    for (; name[k] && k < 40u; k++) {
        char c = name[k];
        t[k] = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == ' ' || c == '-' || c == '_' ? c : '_';
    }
    t[k] = 0;
    snprintf(out, n, "%s%s", t, FRP_USER_EXT);
}
static const char *jstr(const char *j, const char *key, size_t *len)
{
    char pat[40];
    const char *p, *q;
    snprintf(pat, sizeof pat, "\"%s\":\"", key);
    if (!(p = strstr(j, pat))) return 0;
    p += strlen(pat);
    if (!(q = strchr(p, '"'))) return 0;
    *len = (size_t)(q - p);
    return p;
}
static int hexv(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; }
/* a preset file into its name and values: 0 ok */
static int user_read_file(const char *path, char *name, uint8_t *vals)
{
    char buf[1024], nm[FRP_PNAME];
    size_t got, l = 0, ln = 0;
    const char *p, *v;
    unsigned n = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    got = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[got] = 0;
    if (!(v = jstr(buf, "frodhes_preset", &l)) || l < 1 || !(p = jstr(buf, "p", &l))) return -1;
    if (jstr(buf, "n", &ln) && ln && ln < 4) n = (unsigned)atoi(jstr(buf, "n", &ln)); else n = (unsigned)(l / 2u);
    if (l != 2u * n || n == 0 || n > 255u) return -1;
    if (name) {
        const char *q = jstr(buf, "name", &ln);
        char t[FRP_PNAME];
        if (!q) return -1;
        if (ln >= sizeof t) ln = sizeof t - 1;
        memcpy(t, q, ln); t[ln] = 0;
        name_clean(nm, t);
        if (!nm[0]) return -1;
        memcpy(name, nm, FRP_PNAME);
    }
    if (vals)
        for (unsigned i = 0; i < FRP_NPARAM_APP; i++) {
            int a = i < n ? hexv(p[2 * i]) : -1, b = i < n ? hexv(p[2 * i + 1]) : -1, x = a < 0 || b < 0 ? -1 : a * 16 + b;
            vals[i] = x >= 0 && x <= FRP_PARAMS[i].hi ? (uint8_t)x : FRP_PARAMS[i].def;
        }
    return 0;
}
static int user_cmp(const void *a, const void *b) { return strcasecmp(((const user_t *)a)->name, ((const user_t *)b)->name); }
static void pc_cache_fill(void)          /* (user_mu held) */
{
    uint32_t q = atomic_load_explicit(&pc_cache.seq, memory_order_relaxed);
    char path[1200];
    atomic_store_explicit(&pc_cache.seq, q + 1u, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    pc_cache.n = 0;
    for (int k = 0; k < n_users && pc_cache.n < 16; k++) {
        snprintf(path, sizeof path, "%s/%s", frp_user_dir(), users[k].file);
        if (user_read_file(path, pc_cache.name[pc_cache.n], pc_cache.v[pc_cache.n])) continue;
        pc_cache.n++;
    }
    atomic_store_explicit(&pc_cache.seq, q + 2u, memory_order_release);
}
static void user_scan_locked(void)
{
    DIR *d = opendir(frp_user_dir());
    struct dirent *e;
    int cap = 0;
    n_users = 0;
    users_read = 1;
    while (d && (e = readdir(d)) != 0) {
        size_t l = strlen(e->d_name), lx = strlen(FRP_USER_EXT);
        char path[1200], nm[FRP_PNAME];
        if (l <= lx || l >= sizeof users[0].file || strcmp(e->d_name + l - lx, FRP_USER_EXT)) continue;
        snprintf(path, sizeof path, "%s/%s", frp_user_dir(), e->d_name);
        if (user_read_file(path, nm, 0)) continue;
        if (n_users >= FRP_USER_MAX) break;
        if (n_users == cap) {
            user_t *u = realloc(users, (size_t)(cap ? 2 * cap : 64) * sizeof *u);
            if (!u) break;
            users = u; cap = cap ? 2 * cap : 64;
        }
        memcpy(users[n_users].name, nm, FRP_PNAME);
        snprintf(users[n_users].file, sizeof users[0].file, "%s", e->d_name);
        n_users++;
    }
    if (d) closedir(d);
    if (n_users) qsort(users, (size_t)n_users, sizeof *users, user_cmp);
    pc_cache_fill();
    atomic_fetch_add(&user_gen, 1u);
}
static void user_ensure(void) { if (!users_read) user_scan_locked(); }
void frp_user_rescan(void) { pthread_mutex_lock(&user_mu); user_scan_locked(); pthread_mutex_unlock(&user_mu); }
uint32_t frp_user_gen(void) { return atomic_load(&user_gen); }
int frp_user_count(void)
{
    int n;
    pthread_mutex_lock(&user_mu); user_ensure(); n = n_users; pthread_mutex_unlock(&user_mu);
    return n;
}
int frp_user_info(int k, char *name, uint32_t n)
{
    int rc = -1;
    pthread_mutex_lock(&user_mu);
    user_ensure();
    if (k >= 0 && k < n_users) { snprintf(name, n, "%s", users[k].name); rc = 0; }
    pthread_mutex_unlock(&user_mu);
    return rc;
}
int frp_user_values(int k, uint8_t *vals)
{
    int rc = -1;
    char path[1200];
    pthread_mutex_lock(&user_mu);
    user_ensure();
    if (k >= 0 && k < n_users) { snprintf(path, sizeof path, "%s/%s", frp_user_dir(), users[k].file); rc = user_read_file(path, 0, vals); }
    pthread_mutex_unlock(&user_mu);
    return rc;
}
int frp_user_find(const char *name)
{
    int rc = -1;
    char nm[FRP_PNAME];
    name_clean(nm, name);
    pthread_mutex_lock(&user_mu);
    user_ensure();
    for (int k = 0; k < n_users && rc < 0; k++) if (!strcasecmp(users[k].name, nm)) rc = k;
    pthread_mutex_unlock(&user_mu);
    return rc;
}
int frp_user_save(const char *name, const uint8_t *vals)
{
    char nm[FRP_PNAME], file[96], path[1200], tmp[1300];
    int k = -1;
    FILE *f;
    name_clean(nm, name);
    if (!nm[0] || mkdirs(frp_user_dir())) return -1;
    pthread_mutex_lock(&user_mu);
    user_ensure();
    for (int i = 0; i < n_users; i++) if (!strcasecmp(users[i].name, nm)) k = i;   /* (the same name: overwritten) */
    if (k >= 0) snprintf(file, sizeof file, "%s", users[k].file);
    else {
        struct stat st;
        file_of(file, sizeof file, nm);
        for (int u = 2; u < 1000; u++) {   /* (another name with the same file name: a number) */
            snprintf(path, sizeof path, "%s/%s", frp_user_dir(), file);
            if (stat(path, &st)) break;
            char base[64];
            file_of(base, sizeof base, nm);
            base[strlen(base) - strlen(FRP_USER_EXT)] = 0;
            snprintf(file, sizeof file, "%s %d%s", base, u, FRP_USER_EXT);
        }
    }
    snprintf(path, sizeof path, "%s/%s", frp_user_dir(), file);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!(f = fopen(tmp, "wb"))) { pthread_mutex_unlock(&user_mu); return -1; }
    fprintf(f, "{\"frodhes_preset\":\"1\",\"name\":\"%s\",\"n\":\"%u\",\"p\":\"", nm, (unsigned)FRP_NPARAM_APP);
    for (unsigned i = 0; i < FRP_NPARAM_APP; i++) fprintf(f, "%02x", vals[i]);
    fprintf(f, "\"}\n");
    if (fclose(f) || rename(tmp, path)) { remove(tmp); pthread_mutex_unlock(&user_mu); return -1; }
    user_scan_locked();
    k = -1;
    for (int i = 0; i < n_users && k < 0; i++) if (!strcasecmp(users[i].name, nm)) k = i;
    pthread_mutex_unlock(&user_mu);
    return k;
}
int frp_user_delete(int k)
{
    int rc = -1;
    char path[1200];
    pthread_mutex_lock(&user_mu);
    user_ensure();
    if (k >= 0 && k < n_users) {
        snprintf(path, sizeof path, "%s/%s", frp_user_dir(), users[k].file);
        rc = remove(path) ? -1 : 0;
        user_scan_locked();
    }
    pthread_mutex_unlock(&user_mu);
    return rc;
}
int frp_user_get(uint32_t k, uint8_t *vals, char *name)   /* the audio thread: never blocks */
{
    for (int tries = 0; tries < 1000; tries++) {
        uint32_t q1 = atomic_load_explicit(&pc_cache.seq, memory_order_acquire), q2;
        int ok;
        if (q1 & 1u) continue;
        ok = (int)k < pc_cache.n;
        if (ok) { memcpy(vals, pc_cache.v[k], FRP_NPARAM_APP); memcpy(name, pc_cache.name[k], FRP_PNAME); }
        atomic_thread_fence(memory_order_acquire);
        q2 = atomic_load_explicit(&pc_cache.seq, memory_order_relaxed);
        if (q1 == q2) return ok ? 0 : -1;
    }
    return -1;
}
