// SPDX-License-Identifier: GPL-3.0-only
// Frodhes probe: a standalone sound generator (plugin API v2) that answers the first device session's questions.
// No Frodhes code. A sine per note (16 voices), and a log of everything the host gives a sound generator:
//   - every on_midi call (bytes, length, source, time, block number): notes, CC, clock F8/FA/FB/FC, pressure, bend,
//     knob / jog touches (touch_observe), whatever buttons or pads arrive;
//   - every render_block: its own duration and the interval since the previous call (histograms, max, p99.9);
//   - get_param / set_param keys and buffer sizes, the `state` sizes the host accepts and returns (1 KB .. 64 KB);
//   - the host clock calls (get_clock_status, get_bpm, get_beat_position), slot_recv_channel;
//   - which candidate data directories exist, are writable, and still hold the marker of an earlier run.
// Real-time rules: the callbacks only write fixed-size records into a lock-free ring and bump counters. A worker
// thread (demoted to SCHED_OTHER on cores 0-2 as its first action) does every file write. The one exception is the
// first render_block, which reads its own thread's scheduling policy and CPU once (two syscalls, logged as such).

#define _GNU_SOURCE
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "../vendor/plugin_api_v1.h"

#ifndef PROBE_ID
#define PROBE_ID "frodhes-probe"
#endif
#define EXPORT __attribute__((visibility("default")))

#define NVOICE 16
#define RING_N 8192u            // records (power of two)
#define REC_DATA 48
#define DUR_BINS 6000           // render duration, 1 us bins (0 .. 5999 us, last bin = overflow)
#define IVL_BINS 12000          // interval between render calls, 1 us bins
#define STATE_MAX (64 * 1024)
#define NKEYS 64
#define LOG_CAP (32u << 20)     // stop writing event lines after 32 MB (counters go on)

enum { R_MIDI = 1, R_GET, R_SET, R_CLOCK, R_BPM, R_BEAT, R_CHAN, R_THREAD, R_NOTE };

typedef struct {
    _Atomic uint32_t seq;       // = write index + 1 once the record is complete
    uint8_t kind, src, len, trunc;
    uint32_t blk;
    uint64_t t_ns;
    int32_t a, b;               // kind-specific numbers (buf_len, value length, status ...)
    double d;
    char data[REC_DATA];
} rec_t;

typedef struct { float ph, inc, amp, env; int note, on; } voice_t;

typedef struct {
    const host_api_v1_t *host;
    char module_dir[512];
    char defaults[256];
    int id;                     // instance number in this process
    // ring
    rec_t *ring;
    _Atomic uint32_t w, r;
    _Atomic uint32_t dropped;
    // synth
    voice_t v[NVOICE];
    float bend;                 // semitones
    float level;
    // timing (written on the callback only)
    uint64_t t_prev, t_first;
    uint32_t blocks, *dur_h, *ivl_h;
    uint32_t dur_max, ivl_max, ivl_min, late;   // late = interval > 2 blocks
    uint32_t burn_us;
    // counters (callback only)
    uint32_t midi_src[8], midi_status[16], rt[16], midi_total, sysex;
    uint32_t key_n[NKEYS]; char keys[NKEYS][40]; int nkeys;
    int32_t clock_last, chan_last; float bpm_last; double beat_last;
    int state_mode;             // 0 = auto (grow per get), 1.. = 1 KB << (mode-1)
    uint32_t state_auto_i;
    int32_t state_get_max_buflen, state_set_max_len, state_set_ok, state_set_bad;
    char *state_pad;            // built at create
    // worker
    pthread_t th; _Atomic int stop; int th_ok;
    FILE *log; uint32_t log_bytes; char log_path[600], sum_path[600];
} inst_t;

static const host_api_v1_t *g_host;
static _Atomic int g_instances;
static int g_marker = 0x46524f44; // a global whose address tells whether two module ids share one copy

static uint64_t now_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);   // the vDSO on aarch64: no syscall
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

// ---- the ring (multi-producer safe, one consumer) ----
static rec_t *rec_begin(inst_t *s, int kind) {
    uint32_t w = atomic_load_explicit(&s->w, memory_order_relaxed);
    for (;;) {
        uint32_t r = atomic_load_explicit(&s->r, memory_order_acquire);
        if (w - r >= RING_N) { atomic_fetch_add_explicit(&s->dropped, 1, memory_order_relaxed); return NULL; }
        if (atomic_compare_exchange_weak_explicit(&s->w, &w, w + 1, memory_order_acq_rel, memory_order_relaxed)) break;
    }
    rec_t *e = &s->ring[w & (RING_N - 1)];
    e->kind = (uint8_t)kind; e->src = 0; e->len = 0; e->trunc = 0; e->a = e->b = 0; e->d = 0; e->data[0] = 0;
    e->blk = s->blocks; e->t_ns = now_ns();
    e->a = (int32_t)w;          // stash the index; rec_end publishes it
    return e;
}
static void rec_end(rec_t *e, int32_t a) {
    uint32_t idx = (uint32_t)e->a; e->a = a;
    atomic_store_explicit(&e->seq, idx + 1, memory_order_release);
}
static void rec_str(rec_t *e, const char *p, size_t n) {
    if (!p) { e->data[0] = 0; return; }
    if (n >= REC_DATA) { n = REC_DATA - 1; e->trunc = 1; }
    memcpy(e->data, p, n); e->data[n] = 0;
}

// ---- synth ----
static void note_on(inst_t *s, int n, int vel) {
    int k = 0; float best = 2.0f;
    for (int i = 0; i < NVOICE; i++) { if (!s->v[i].on && s->v[i].env < best) { best = s->v[i].env; k = i; } }
    voice_t *v = &s->v[k];
    v->note = n; v->on = 1; v->ph = 0; v->env = 0; v->amp = (float)vel / 127.0f;
}
static void note_off(inst_t *s, int n) {
    for (int i = 0; i < NVOICE; i++) if (s->v[i].on && s->v[i].note == n) s->v[i].on = 0;
}

static void midi_in(inst_t *s, const uint8_t *m, int len, int source) {
    rec_t *e = rec_begin(s, R_MIDI);
    if (e) { e->src = (uint8_t)source; e->len = (uint8_t)(len > 255 ? 255 : len);
             size_t n = len > 0 ? (size_t)len : 0; if (n > REC_DATA) { n = REC_DATA; e->trunc = 1; }
             if (m && n) memcpy(e->data, m, n);
             rec_end(e, len); }
    s->midi_total++;
    s->midi_src[(unsigned)source & 7]++;
    if (len < 1 || !m) return;
    uint8_t st = m[0];
    if (st >= 0xF8) { s->rt[st & 15]++; return; }
    if (st == 0xF0) { s->sysex++; return; }
    s->midi_status[st >> 4]++;
    if (source == MOVE_MIDI_SOURCE_TOUCH) return;           // knob / jog touches: logged, never played
    int hi = st & 0xF0;
    if (hi == 0x90 && len >= 3 && m[2]) note_on(s, m[1] & 127, m[2]);
    else if ((hi == 0x80 || hi == 0x90) && len >= 2) note_off(s, m[1] & 127);
    else if (hi == 0xE0 && len >= 3) s->bend = (float)(((m[2] << 7) | m[1]) - 8192) / 8192.0f * 2.0f;
    else if (hi == 0xB0 && len >= 3 && (m[1] == 120 || m[1] == 123)) for (int i = 0; i < NVOICE; i++) s->v[i].on = 0;
}

// ---- the state blob: a flat JSON object, its size chosen by the `state_kb` param ----
static const int STATE_STEPS[] = { 1024, 2048, 4096, 8192, 16384, 32768, 65536 };
static int state_target(inst_t *s) {
    if (s->state_mode >= 1 && s->state_mode <= 7) return STATE_STEPS[s->state_mode - 1];
    return STATE_STEPS[s->state_auto_i++ % 7];
}
static uint32_t fnv(const char *p, size_t n) { uint32_t h = 2166136261u; while (n--) { h ^= (uint8_t)*p++; h *= 16777619u; } return h; }

// {"v":"1","n":"<len>","h":"<fnv of pad>","pad":"abc..."}  total length == target (when it fits)
static int state_get(inst_t *s, char *buf, int buf_len) {
    int want = state_target(s);
    char head[96];
    int hl = snprintf(head, sizeof head, "{\"v\":\"1\",\"n\":\"%06d\",\"h\":\"%08x\",\"pad\":\"", want, 0u);
    int padn = want - hl - 2;
    if (padn < 0) padn = 0;
    uint32_t h = fnv(s->state_pad, (size_t)padn);
    hl = snprintf(head, sizeof head, "{\"v\":\"1\",\"n\":\"%06d\",\"h\":\"%08x\",\"pad\":\"", want, h);
    rec_t *e = rec_begin(s, R_GET);
    if (e) { rec_str(e, "state", 5); e->b = want; rec_end(e, buf_len); }
    if (buf_len > s->state_get_max_buflen) s->state_get_max_buflen = buf_len;
    if (hl + padn + 2 + 1 > buf_len) return -1;             // does not fit: the log shows want vs buf_len
    memcpy(buf, head, (size_t)hl);
    memcpy(buf + hl, s->state_pad, (size_t)padn);
    memcpy(buf + hl + padn, "\"}", 3);
    return hl + padn + 2;
}
static void state_set(inst_t *s, const char *val) {
    size_t n = val ? strlen(val) : 0;
    int ok = 0, want = -1; unsigned h = 0;
    const char *p = val ? strstr(val, "\"pad\":\"") : NULL;
    if (val && sscanf(val, "{\"v\":\"1\",\"n\":\"%d\",\"h\":\"%x\"", &want, &h) == 2 && p) {
        p += 7; const char *q = strchr(p, '"');
        if (q && fnv(p, (size_t)(q - p)) == h && (int)n == want) ok = 1;
    }
    if ((int)n > s->state_set_max_len) s->state_set_max_len = (int)n;
    if (ok) s->state_set_ok++; else s->state_set_bad++;
    rec_t *e = rec_begin(s, R_SET);
    if (e) { rec_str(e, "state", 5); e->b = ok ? want : -1; rec_end(e, (int32_t)n); }
}

// ---- keys seen by get_param (the shadow UI polls: count, log only the first three of each) ----
static int key_seen(inst_t *s, const char *k) {
    for (int i = 0; i < s->nkeys; i++) if (!strncmp(s->keys[i], k, 39)) return ++s->key_n[i];
    if (s->nkeys < NKEYS) { snprintf(s->keys[s->nkeys], 40, "%s", k); s->key_n[s->nkeys] = 1; s->nkeys++; return 1; }
    return 1000000;
}

// ---- the worker: every file write happens here ----
static void lprintf(inst_t *s, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void lprintf(inst_t *s, const char *fmt, ...) {
    if (!s->log || s->log_bytes > LOG_CAP) return;
    va_list ap; va_start(ap, fmt); int n = vfprintf(s->log, fmt, ap); va_end(ap);
    if (n > 0) s->log_bytes += (uint32_t)n;
}

static const char *status_name(uint8_t st) {
    static const char *ch[8] = { "NoteOff", "NoteOn", "PolyPressure", "CC", "Program", "ChanPressure", "PitchBend", "Sys" };
    if (st >= 0x80 && st < 0xF0) return ch[(st >> 4) - 8];
    switch (st) { case 0xF8: return "Clock"; case 0xFA: return "Start"; case 0xFB: return "Continue";
                  case 0xFC: return "Stop"; case 0xFE: return "ActiveSense"; case 0xFF: return "Reset";
                  case 0xF0: return "SysEx"; case 0xF2: return "SongPos"; default: return "?"; }
}

static void write_rec(inst_t *s, const rec_t *e) {
    double t = (double)(e->t_ns - s->t_first) / 1e9;
    switch (e->kind) {
    case R_MIDI: {
        lprintf(s, "%.6f blk=%u midi src=%u len=%d", t, e->blk, e->src, e->a);
        int n = e->a < REC_DATA ? e->a : REC_DATA;
        for (int i = 0; i < n; i++) lprintf(s, " %02x", (uint8_t)e->data[i]);
        if (n > 0) { uint8_t st = (uint8_t)e->data[0];
            lprintf(s, " %s", status_name(st));
            if (st >= 0x80 && st < 0xF0) lprintf(s, " ch%d", (st & 15) + 1); }
        lprintf(s, "%s\n", e->trunc ? " (truncated)" : "");
        break; }
    case R_GET: lprintf(s, "%.6f blk=%u get key=%s%s buf_len=%d%s", t, e->blk, e->data, e->trunc ? "~" : "", e->a,
                        strcmp(e->data, "state") ? "" : " state_want=");
                if (!strcmp(e->data, "state")) lprintf(s, "%d%s", e->b, e->b + 1 > e->a ? " DOES-NOT-FIT" : "");
                lprintf(s, "\n"); break;
    case R_SET: lprintf(s, "%.6f blk=%u set key+val=%s%s val_len=%d", t, e->blk, e->data, e->trunc ? "~" : "", e->a);
                if (e->b) lprintf(s, " %s", e->b > 0 ? "state=VALID" : "state=INVALID-or-foreign");
                lprintf(s, "\n"); break;
    case R_CLOCK: lprintf(s, "%.6f blk=%u host get_clock_status=%d\n", t, e->blk, e->a); break;
    case R_BPM: lprintf(s, "%.6f blk=%u host get_bpm=%.3f\n", t, e->blk, e->d); break;
    case R_BEAT: lprintf(s, "%.6f blk=%u host get_beat_position=%.4f\n", t, e->blk, e->d); break;
    case R_CHAN: lprintf(s, "%.6f blk=%u host slot_recv_channel=%d\n", t, e->blk, e->a); break;
    case R_THREAD: lprintf(s, "%.6f blk=%u audio thread: policy=%d prio=%d cpu=%d (2 syscalls, first block only)\n",
                           t, e->blk, e->a, e->b, (int)e->d); break;
    case R_NOTE: lprintf(s, "%.6f blk=%u note %s\n", t, e->blk, e->data); break;
    }
}

static void drain(inst_t *s) {
    for (;;) {
        uint32_t r = atomic_load_explicit(&s->r, memory_order_relaxed);
        rec_t *e = &s->ring[r & (RING_N - 1)];
        if (atomic_load_explicit(&e->seq, memory_order_acquire) != r + 1) break;
        write_rec(s, e);
        atomic_store_explicit(&s->r, r + 1, memory_order_release);
    }
    if (s->log) fflush(s->log);
}

static uint32_t hist_pct(const uint32_t *h, int nb, uint64_t total, double pct) {
    if (!total) return 0;
    uint64_t want = (uint64_t)((double)total * pct), acc = 0;
    for (int i = 0; i < nb; i++) { acc += h[i]; if (acc >= want) return (uint32_t)i; }
    return (uint32_t)(nb - 1);
}

static void summary_to(inst_t *s, FILE *f) {
    uint64_t nd = 0, ni = 0;
    for (int i = 0; i < DUR_BINS; i++) nd += s->dur_h[i];
    for (int i = 0; i < IVL_BINS; i++) ni += s->ivl_h[i];
    fprintf(f, "probe %s instance %d pid %d  &g_marker=%p instances_in_process=%d\n", PROBE_ID, s->id, (int)getpid(),
            (void *)&g_marker, atomic_load(&g_instances));
    fprintf(f, "module_dir %s\ndefaults %s\n", s->module_dir, s->defaults);
    fprintf(f, "host api_version=%u sample_rate=%d frames=%d get_bpm=%s get_beat_position=%s slot_recv_channel=%s"
               " get_clock_status=%s\n", s->host ? s->host->api_version : 0, s->host ? s->host->sample_rate : 0,
            s->host ? s->host->frames_per_block : 0, s->host && s->host->get_bpm ? "yes" : "NULL",
            s->host && s->host->get_beat_position ? "yes" : "NULL", s->host && s->host->slot_recv_channel ? "yes" : "NULL",
            s->host && s->host->get_clock_status ? "yes" : "NULL");
    fprintf(f, "blocks %u  burn_us %u  ring_dropped %u  log_bytes %u%s\n", s->blocks, s->burn_us,
            atomic_load(&s->dropped), s->log_bytes, s->log_bytes > LOG_CAP ? " (CAPPED)" : "");
    fprintf(f, "render_us  max %u  p50 %u  p99 %u  p99.9 %u  p99.99 %u\n", s->dur_max,
            hist_pct(s->dur_h, DUR_BINS, nd, .5), hist_pct(s->dur_h, DUR_BINS, nd, .99),
            hist_pct(s->dur_h, DUR_BINS, nd, .999), hist_pct(s->dur_h, DUR_BINS, nd, .9999));
    fprintf(f, "interval_us  min %u  max %u  p0.1 %u  p50 %u  p99.9 %u  late(>5804us) %u\n", s->ivl_min, s->ivl_max,
            hist_pct(s->ivl_h, IVL_BINS, ni, .001), hist_pct(s->ivl_h, IVL_BINS, ni, .5),
            hist_pct(s->ivl_h, IVL_BINS, ni, .999), s->late);
    fprintf(f, "render_hist_us (bin:count, nonzero)");
    for (int i = 0; i < DUR_BINS; i++) if (s->dur_h[i]) fprintf(f, " %d:%u", i, s->dur_h[i]);
    fprintf(f, "\ninterval_hist_100us (bin*100:count)");
    for (int b = 0; b < IVL_BINS; b += 100) { uint64_t c = 0; for (int i = b; i < b + 100; i++) c += s->ivl_h[i];
                                              if (c) fprintf(f, " %d:%llu", b, (unsigned long long)c); }
    fprintf(f, "\nmidi total %u sysex %u  by source:", s->midi_total, s->sysex);
    for (int i = 0; i < 8; i++) if (s->midi_src[i]) fprintf(f, " src%d=%u", i, s->midi_src[i]);
    fprintf(f, "\nmidi by status:");
    for (int i = 8; i < 15; i++) if (s->midi_status[i]) fprintf(f, " %s=%u", status_name((uint8_t)(i << 4)), s->midi_status[i]);
    for (int i = 8; i < 16; i++) if (s->rt[i]) fprintf(f, " %s=%u", status_name((uint8_t)(0xF0 | i)), s->rt[i]);
    fprintf(f, "\nclock_status %d  bpm %.3f  beat %.3f  recv_channel %d\n", s->clock_last, s->bpm_last, s->beat_last,
            s->chan_last);
    fprintf(f, "state  get_max_buf_len %d  set_max_len %d  set_valid %d  set_invalid %d  mode %d\n",
            s->state_get_max_buflen, s->state_set_max_len, s->state_set_ok, s->state_set_bad, s->state_mode);
    fprintf(f, "get_param keys:");
    for (int i = 0; i < s->nkeys; i++) fprintf(f, " %s=%u", s->keys[i], s->key_n[i]);
    fprintf(f, "\n");
}
static void write_summary(inst_t *s) {
    char tmp[620]; snprintf(tmp, sizeof tmp, "%s.tmp", s->sum_path);
    FILE *f = fopen(tmp, "w"); if (!f) return;
    summary_to(s, f);
    if (fclose(f) == 0) rename(tmp, s->sum_path);
}

// Candidate data directories: exists? writable? does the marker of an earlier run survive (module update, reboot)?
static void probe_dirs(inst_t *s) {
    char cand[12][600]; int n = 0;
    const char *home = getenv("HOME");
    snprintf(cand[n++], 600, "%s", s->module_dir);
    snprintf(cand[n++], 600, "%s/..", s->module_dir);                         // sound_generators/
    snprintf(cand[n++], 600, "/data/UserData/schwung");
    snprintf(cand[n++], 600, "/data/UserData/schwung/config");
    snprintf(cand[n++], 600, "/data/UserData/schwung/module_data");
    snprintf(cand[n++], 600, "/data/UserData/schwung/patches");
    snprintf(cand[n++], 600, "/data/UserData");
    snprintf(cand[n++], 600, "%s", home ? home : "/nonexistent-HOME");
    snprintf(cand[n++], 600, "/var/tmp");
    snprintf(cand[n++], 600, "/tmp");
    lprintf(s, "datadir probe (marker file .%s-marker; write = tmp + rename + read back)\n", PROBE_ID);
    for (int i = 0; i < n; i++) {
        struct stat st; int ex = stat(cand[i], &st) == 0 && S_ISDIR(st.st_mode);
        char path[700], tmp[720], prev[128] = "none";
        snprintf(path, sizeof path, "%.590s/.%s-marker", cand[i], PROBE_ID);
        snprintf(tmp, sizeof tmp, "%s.tmp", path);
        FILE *f = ex ? fopen(path, "r") : NULL;
        if (f) { if (!fgets(prev, sizeof prev, f)) snprintf(prev, sizeof prev, "empty"); fclose(f);
                 prev[strcspn(prev, "\n")] = 0; }
        int wr = 0, rb = 0, err = 0;
        if (ex) {
            f = fopen(tmp, "w");
            if (f) { fprintf(f, "pid=%d t=%ld instance=%d\n", (int)getpid(), (long)time(NULL), s->id);
                     wr = fclose(f) == 0 && rename(tmp, path) == 0; }
            if (!wr) err = errno;
            f = wr ? fopen(path, "r") : NULL;
            if (f) { char b[128]; rb = fgets(b, sizeof b, f) != NULL; fclose(f); }
        }
        lprintf(s, "datadir %-48s exists=%d writable=%d readback=%d err=%d previous_marker=[%s]\n", cand[i], ex, wr, rb,
                err, prev);
    }
}

static void *worker(void *arg) {
    inst_t *s = arg;
    struct sched_param sp = { .sched_priority = 0 };
    int rs = sched_setscheduler(0, SCHED_OTHER, &sp);
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(0, &set); CPU_SET(1, &set); CPU_SET(2, &set);
    int ra = sched_setaffinity(0, sizeof set, &set);
    s->log = fopen(s->log_path, "a");
    if (!s->log) { snprintf(s->log_path, sizeof s->log_path, "/tmp/%s-%d.log", PROBE_ID, s->id); s->log = fopen(s->log_path, "a"); }
    lprintf(s, "==== %s instance %d start pid=%d time=%ld worker sched_setscheduler=%d sched_setaffinity=%d\n", PROBE_ID,
            s->id, (int)getpid(), (long)time(NULL), rs, ra);
    lprintf(s, "module_dir=%s defaults=%s &g_marker=%p\n", s->module_dir, s->defaults, (void *)&g_marker);
    probe_dirs(s);
    int tick = 0;
    while (!atomic_load(&s->stop)) {
        drain(s);
        if (++tick % 40 == 0) write_summary(s);            // every ~2 s
        struct timespec ts = { 0, 50 * 1000000L }; nanosleep(&ts, NULL);
    }
    drain(s); write_summary(s);
    lprintf(s, "==== instance %d stop, final summary:\n", s->id);
    if (s->log && s->log_bytes <= LOG_CAP) summary_to(s, s->log);
    if (s->log) fclose(s->log);
    return NULL;
}

// ---- the plugin API ----
static void *p_create(const char *module_dir, const char *json_defaults) {
    inst_t *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->ring = calloc(RING_N, sizeof(rec_t));
    s->dur_h = calloc(DUR_BINS, sizeof(uint32_t));
    s->ivl_h = calloc(IVL_BINS, sizeof(uint32_t));
    s->state_pad = malloc(STATE_MAX);
    if (!s->ring || !s->dur_h || !s->ivl_h || !s->state_pad) {
        free(s->ring); free(s->dur_h); free(s->ivl_h); free(s->state_pad); free(s); return NULL; }
    for (int i = 0; i < STATE_MAX; i++) s->state_pad[i] = (char)('a' + (i * 7 + i / 26) % 26);
    s->host = g_host;
    s->id = atomic_fetch_add(&g_instances, 1);
    snprintf(s->module_dir, sizeof s->module_dir, "%s", module_dir ? module_dir : ".");
    snprintf(s->defaults, sizeof s->defaults, "%s", json_defaults ? json_defaults : "(null)");
    snprintf(s->log_path, sizeof s->log_path, "%s/probe-%d.log", s->module_dir, s->id);
    snprintf(s->sum_path, sizeof s->sum_path, "%s/probe-%d-summary.txt", s->module_dir, s->id);
    s->level = 0.25f; s->ivl_min = UINT32_MAX; s->clock_last = -99; s->chan_last = -99; s->bpm_last = -1;
    s->beat_last = -1e9;
    s->t_first = now_ns();
    s->th_ok = pthread_create(&s->th, NULL, worker, s) == 0;
    return s;
}

static void p_destroy(void *inst) {
    inst_t *s = inst; if (!s) return;
    atomic_store(&s->stop, 1);
    if (s->th_ok) pthread_join(s->th, NULL);
    free(s->ring); free(s->dur_h); free(s->ivl_h); free(s->state_pad); free(s);
    atomic_fetch_sub(&g_instances, 1);
}

static void p_on_midi(void *inst, const uint8_t *msg, int len, int source) { if (inst) midi_in(inst, msg, len, source); }

static void p_set_param(void *inst, const char *key, const char *val) {
    inst_t *s = inst; if (!s || !key) return;
    if (!strcmp(key, "state") || (strlen(key) > 6 && !strcmp(key + strlen(key) - 6, ":state"))) { state_set(s, val); return; }
    rec_t *e = rec_begin(s, R_SET);
    if (e) { char kv[REC_DATA]; int n = snprintf(kv, sizeof kv, "%s %s", key, val ? val : "(null)");
             rec_str(e, kv, n < 0 ? 0 : (size_t)n); rec_end(e, val ? (int32_t)strlen(val) : -1); }
    if (!val) return;
    if (!strcmp(key, "state_kb")) {                     // the enum's index or its label ("Auto", "1K" .. "64K")
        int m = atoi(val);
        if (val[0] == 'A') m = 0;
        else if (strchr(val, 'K')) { int kb = atoi(val); m = 1; while (kb > 1 && m < 7) { kb >>= 1; m++; } }
        s->state_mode = m < 0 ? 0 : m > 7 ? 7 : m; }
    else if (!strcmp(key, "burn_us")) { int b = atoi(val); s->burn_us = b < 0 ? 0 : b > 2500 ? 2500 : (uint32_t)b; }
    else if (!strcmp(key, "level")) { float l = (float)atof(val); s->level = l < 0 ? 0 : l > 1 ? 1 : l; }
    else if (!strcmp(key, "mark")) { rec_t *m = rec_begin(s, R_NOTE); if (m) { rec_str(m, val, strlen(val)); rec_end(m, 0); } }
}

static int p_get_param(void *inst, const char *key, char *buf, int buf_len) {
    inst_t *s = inst; if (!s || !key || !buf || buf_len <= 0) return -1;
    if (!strcmp(key, "state")) { key_seen(s, key); return state_get(s, buf, buf_len); }
    if (key_seen(s, key) <= 3) { rec_t *e = rec_begin(s, R_GET); if (e) { rec_str(e, key, strlen(key)); rec_end(e, buf_len); } }
    if (!strcmp(key, "state_kb")) return snprintf(buf, (size_t)buf_len, "%d", s->state_mode);
    if (!strcmp(key, "burn_us")) return snprintf(buf, (size_t)buf_len, "%u", s->burn_us);
    if (!strcmp(key, "level")) return snprintf(buf, (size_t)buf_len, "%.2f", s->level);
    return -1;
}

static int p_get_error(void *inst, char *buf, int buf_len) { (void)inst; (void)buf; (void)buf_len; return 0; }

static void host_queries(inst_t *s) {
    const host_api_v1_t *h = s->host; if (!h) return;
    if (h->get_clock_status) { int c = h->get_clock_status();
        if (c != s->clock_last) { s->clock_last = c; rec_t *e = rec_begin(s, R_CLOCK); if (e) rec_end(e, c); } }
    if (h->get_bpm) { float b = h->get_bpm();
        if (b != s->bpm_last) { s->bpm_last = b; rec_t *e = rec_begin(s, R_BPM); if (e) { e->d = b; rec_end(e, 0); } } }
    if (h->get_beat_position && s->blocks % 345 == 0) {      // ~1 s
        double p = h->get_beat_position(); s->beat_last = p;
        rec_t *e = rec_begin(s, R_BEAT); if (e) { e->d = p; rec_end(e, 0); } }
    if (h->slot_recv_channel && s->blocks % 345 == 0) { int c = h->slot_recv_channel(s);
        if (c != s->chan_last) { s->chan_last = c; rec_t *e = rec_begin(s, R_CHAN); if (e) rec_end(e, c); } }
}

static void p_render(void *inst, int16_t *out, int frames) {
    inst_t *s = inst;
    uint64_t t0 = now_ns();
    if (!s) { if (out) memset(out, 0, (size_t)frames * 4); return; }
    if (s->blocks == 0) {
        struct sched_param sp = { .sched_priority = -1 }; int pol = -1;
        pthread_getschedparam(pthread_self(), &pol, &sp);
        rec_t *e = rec_begin(s, R_THREAD); if (e) { e->b = sp.sched_priority; e->d = sched_getcpu(); rec_end(e, pol); }
    } else {
        uint64_t d = (t0 - s->t_prev) / 1000;
        uint32_t iv = d > UINT32_MAX ? UINT32_MAX : (uint32_t)d;
        s->ivl_h[iv < IVL_BINS ? iv : IVL_BINS - 1]++;
        if (iv > s->ivl_max) s->ivl_max = iv;
        if (iv < s->ivl_min) s->ivl_min = iv;
        if (iv > 5804) s->late++;
    }
    s->t_prev = t0;
    host_queries(s);
    const float sr = 44100.0f;
    for (int i = 0; i < frames; i++) { out[2 * i] = 0; out[2 * i + 1] = 0; }
    for (int k = 0; k < NVOICE; k++) {
        voice_t *v = &s->v[k];
        if (!v->on && v->env < 1e-4f) continue;
        float f = 440.0f * powf(2.0f, ((float)v->note - 69.0f + s->bend) / 12.0f);
        v->inc = 6.2831853f * f / sr;
        for (int i = 0; i < frames; i++) {
            v->env += ((v->on ? 1.0f : 0.0f) - v->env) * (v->on ? 0.01f : 0.0005f);
            float y = sinf(v->ph) * v->env * v->amp * s->level * 32767.0f;
            v->ph += v->inc; if (v->ph > 6.2831853f) v->ph -= 6.2831853f;
            int a = out[2 * i] + (int)y;
            a = a > 32767 ? 32767 : a < -32768 ? -32768 : a;
            out[2 * i] = out[2 * i + 1] = (int16_t)a;
        }
    }
    if (s->burn_us) { uint64_t until = t0 + (uint64_t)s->burn_us * 1000; while (now_ns() < until) { } }
    uint64_t du = (now_ns() - t0) / 1000;
    uint32_t dd = du > UINT32_MAX ? UINT32_MAX : (uint32_t)du;
    s->dur_h[dd < DUR_BINS ? dd : DUR_BINS - 1]++;
    if (dd > s->dur_max) s->dur_max = dd;
    s->blocks++;
}

static plugin_api_v2_t g_api = {
    .api_version = MOVE_PLUGIN_API_VERSION_2,
    .create_instance = p_create, .destroy_instance = p_destroy, .on_midi = p_on_midi,
    .set_param = p_set_param, .get_param = p_get_param, .get_error = p_get_error, .render_block = p_render,
};

EXPORT plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host) { g_host = host; return &g_api; }
