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
 * host is told is that pre-roll at its rate. */
#include <math.h>
#ifdef _WIN32                            /* (Windows: InitOnceExecuteOnce in pthread_once's place, no winpthreads) */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <pthread.h>
#endif
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "bridge.h"
#include "../../firmware/src/uac_fir.h"

static const frp_slot_api_t *const SLOT[FRP_SLOTS] = {
    &frp_slot_0, &frp_slot_1, &frp_slot_2, &frp_slot_3, &frp_slot_4, &frp_slot_5, &frp_slot_6, &frp_slot_7,
    &frp_slot_8, &frp_slot_9, &frp_slot_10, &frp_slot_11, &frp_slot_12, &frp_slot_13, &frp_slot_14, &frp_slot_15,
};
static atomic_int slot_used[FRP_SLOTS];

#define CHUNK 256u                       /* frames read from the FIFO at a time */
#define UAC_KNEE 24576                   /* usb.c UAC_KNEE: above it the excess is halved, before the clamp */
#define RS_HALF 24                       /* the other rates: zero crossings each side of the sinc */
#define RS_OS 512                        /* table points a zero crossing (linear between them) */
#define RS_BETA 9.0                      /* the Kaiser window (about -90 dB side lobes) */
#define RS_N 4096u                       /* the input ring (power of 2): 2 x the reach at 1 kHz + a chunk fit */
#define RS_MIN 1000.0
#define RS_MAX 768000.0
enum { M441, M48, MANY };

struct frp_inst {
    int slot;
    const frp_slot_api_t *s;
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
#ifdef _WIN32
static INIT_ONCE rs_once = INIT_ONCE_STATIC_INIT;
static void rs_init(void);
static BOOL CALLBACK rs_init_win(PINIT_ONCE o, PVOID p, PVOID *c) { (void)o; (void)p; (void)c; rs_init(); return TRUE; }
#define pthread_once(o, f) InitOnceExecuteOnce((o), rs_init_win, 0, 0)
#else
static pthread_once_t rs_once = PTHREAD_ONCE_INIT;
#endif
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

static int slot_claim(void)
{
    int i, pass;
    for (pass = 0; pass < 2; pass++)     /* a slot never used first (a clean app), then a used one */
        for (i = 0; i < (int)FRP_SLOTS; i++) {
            int z = 0;
            if (pass == 0 && SLOT[i]->booted()) continue;
            if (atomic_compare_exchange_strong(&slot_used[i], &z, 1)) return i;
        }
    return -1;
}

frp_inst_t *frp_inst_new(void)
{
    frp_inst_t *in = calloc(1, sizeof *in);
    if (!in) return 0;
    in->engine = FRP_ENGINE_HQ;          /* (the plugin's default; params_map.h FRP_ID_ENGINE) */
    in->slot = slot_claim();
    if (in->slot >= 0) {
        in->s = SLOT[in->slot];
        if (in->s->booted()) in->s->reuse(in->engine);
        else if (in->s->boot(in->engine)) { atomic_store(&slot_used[in->slot], 0); in->slot = -1; in->s = 0; }
    }
    return in;
}
void frp_inst_free(frp_inst_t *in)
{
    if (!in) return;
    if (in->slot >= 0) atomic_store(&slot_used[in->slot], 0);
    free(in);
}
int frp_inst_slot(const frp_inst_t *in) { return in ? in->slot : -1; }

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
    if (in->s) {
        uint32_t a;
        in->s->drain();                  /* (what the main thread queued while inactive) */
        while ((a = in->s->avail()) != 0) in->s->read(in->buf, a < CHUNK ? a : CHUNK);   /* (frames from before) */
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
void frp_inst_reset(frp_inst_t *in) { if (in->s) (void)in->s->put(FRP_EV_RESET, 0, 0, 0); }

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
void frp_inst_midi(frp_inst_t *in, uint32_t time, uint8_t st, uint8_t d1, uint8_t d2)
{
    if (!in->s || st < 0x80u || st == 0xF0u || st == 0xF7u) return;
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
    if (id == FRP_ID_ENGINE) {
        in->engine = param_round((uint32_t)i, value);
        if (in->s) (void)in->s->put(FRP_EV_ENGINE, at, 0, in->engine);
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
int frp_inst_get(const frp_inst_t *in, uint32_t id, double *value)
{
    frp_snap_t sn;
    int i = param_index(id);
    if (i < 0) return -1;
    if (!in->s) { *value = id == FRP_ID_ENGINE ? in->engine : FRP_PARAMS[i].def; return 0; }
    in->s->snapshot(&sn);
    *value = id == FRP_ID_ENGINE ? sn.engine : sn.pv[id];
    return 0;
}
int frp_inst_state_get(const frp_inst_t *in, char *buf, int n) { return in->s ? in->s->state_get(buf, n) : -1; }
int frp_inst_state_set(frp_inst_t *in, const char *json)
{
    if (!in->s) return -1;
    if (in->s->state_set(json)) return -1;
    if (!in->active) in->s->drain();
    return 0;
}
uint32_t frp_inst_rev(const frp_inst_t *in)
{
    frp_snap_t sn;
    if (!in->s) return 0;
    in->s->snapshot(&sn);
    return sn.rev;
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

/* n frames out of the slot (stepping it as needed), n <= CHUNK */
static void pull(frp_inst_t *in, uint32_t n)
{
    while (in->s->avail() < n) in->s->step();
    in->s->read(in->buf, n);
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

void frp_inst_render(frp_inst_t *in, float *l, float *r, uint32_t n, uint32_t tempo_bpm10)
{
    const float sc = 1.0f / 32768.0f;
    uint32_t i = 0;
    if (!in->s || !in->active) { memset(l, 0, n * sizeof *l); memset(r, 0, n * sizeof *r); return; }
    in->s->set_tempo(tempo_bpm10);
    if (in->mode == MANY) { rs_render(in, l, r, n); in->pos += n; return; }
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
}
