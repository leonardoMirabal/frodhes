/* SPDX-License-Identifier: GPL-3.0-only */
/* The Move bridge (M1-02 .. M1-07): included by move/move_plugin.c after frodhes.c and move/module/move_ui.h, so
 * the app's statics are in scope. Float is allowed here (move/ only); the app itself stays integer.
 *
 * Execution model (plan 04, 1): ONE thread. app_main() runs as a coroutine (move/coro) on the audio callback.
 * The callback drives the app's time in host milliseconds exactly as tests/host.c does, so the Classic renders
 * equal the host renders:
 *
 *   step(ms):  1. ms += 1
 *              2. the MIDI pre-queue: every message due by ms goes into web_hal.c's 64-entry ring (web_midi), as
 *                 host.c puts a script's messages in at their ms; a burst waits in the pre-queue, never dropped
 *              3. parameter writes and a pending state load (the Move's set_param), applied between two passes
 *              4. the audio owed: every 256 frames app_audio(256) into a FIFO (int16, the FM-1's clip, MASTER
 *                 fixed at unity: Q12 4096, so the FIFO holds app_audio's own output, which Gate M compares)
 *              5. resume app_main() for one main-loop pass, up to its next platform_poll() (web_hal.c)
 *   render_block(128): steps until the FIFO holds 128 frames, then hands them out (latency <= 256 frames).
 *
 * The app is one set of globals, so the plugin is a singleton: one live instance per process (a second
 * create_instance gets NULL until the device session shows what two slots need, plan 9.2 Q7). The app keeps running
 * after destroy_instance (a later create continues it, with all notes off), because its static data cannot be
 * initialised a second time.
 *
 * Things the device session may change (each one place here): the clock source (MB_CLOCK_*), the state layout
 * (mb_state_get / mb_state_set), enum values by index or by label (mb_parse_wire), which MIDI sources play. */
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <time.h>
#include "move_store.h"
#include "coro/coro.h"

#define MB_STACK (256u * 1024u)          /* the coroutine's stack (app_main and what it calls; ui_draw is the deepest) */
#define MB_FIFO 1024u                    /* frames (power of two) */
#define MB_Q 1024u                       /* the pre-queue: MIDI and parameter writes (power of two) */
#define MB_HOST_RING 64u                 /* web_hal.c's mq[] */
#define MB_PAT_LO (SQ_A < SQ_B ? SQ_A : SQ_B) /* the pattern's sectors in host_mem (frodhes_store.h) */
#define MB_PAT_N ((SQ_A < SQ_B ? SQ_B : SQ_A) + 0x1000u - MB_PAT_LO)
#define MB_STAGE_BLOCKS 345u             /* look for something to save about once a second */
#define MB_CPU_N 4096u                   /* the per-callback timer ring (power of two): ~12 s of callbacks */

enum { MB_EV_MIDI = 1, MB_EV_KEY, MB_EV_PRESET };
enum { MB_CLOCK_AUTO, MB_CLOCK_MIDI, MB_CLOCK_HOST };   /* AUTO: synthesise from the host only while no F8 arrives */
#define MB_CLOCK_REAL_BLOCKS 690u        /* ~2 s: an F8 seen this recently means the slot gets a real clock */

typedef struct {
    _Atomic uint32_t seq;
    uint8_t kind, st, d1, d2;
    uint32_t at;                         /* the host ms it is due (0: at once) */
    int32_t v;
    uint16_t key;
} mb_ev_t;

static struct {
    coro_t co;
    void *stack;
    int booted;
    _Atomic int live;
    uint32_t ms;                         /* host milliseconds stepped (tests/host.c fm1_ms) */
    uint32_t owed;
    int first_now;                       /* web_hal.c's first clock reading (mb_now) */
    int16_t fifo[2u * MB_FIFO];
    uint32_t fr, fw;
    mb_ev_t q[MB_Q];
    _Atomic uint32_t qw;
    uint32_t qr;
    uint32_t blocks;
    /* the clock (M1-05) */
    int clock_mode, clk_run;
    uint32_t real_f8_blk; int real_f8;
    uint32_t tick; double acc, beat_prev;
    /* counters (dev_stats) */
    uint32_t q_full, midi_in, midi_out, f8_synth, keys_set, keys_bad, state_ok, state_bad, steps;
    /* the state mailbox (M1-07): 0 free, 1 being written, 2 ready, 3 being applied */
    _Atomic int st_flag;
    settings_t st_set;
    uint8_t st_base[P_SLOTS];
    uint16_t st_hand;
    uint32_t rev;                        /* bumps on every applied change (state's "rev") */
    /* the per-callback timer (M1-18): render_block's own time, CLOCK_MONOTONIC (the vDSO: no syscall) */
    uint32_t cpu_us[MB_CPU_N]; uint16_t cpu_fr[MB_CPU_N];
    _Atomic uint32_t cpu_n;
    uint32_t cpu_rd, cpu_max;
    /* persistence (M1-08): the audio side stages what the app saved (the store record: settings + user presets;
     * the pattern's sectors) under a sequence counter (odd: being copied); the worker writes the files */
    _Atomic uint32_t stg_seq;
    uint8_t stg_store[sizeof web_store_buf];
    uint8_t stg_pat[MB_PAT_N];
    uint32_t stg_blk;
    int stg_have;
    pthread_t wk; int wk_on; _Atomic int wk_stop, booting;
    _Atomic int ready;                   /* the app has booted (the worker booted it): render plays */
    char dir[512], mod_dir[512];
    _Atomic int cpu_log;                 /* dev: the worker appends the timer ring to <module dir>/cpu-ring.log */
    uint32_t saves, save_fail, loaded;
    /* the sustain control (M1-13b): presses / releases seen; the touch note that is the control (-1: none) */
    uint32_t sus_down, sus_up;
    _Atomic int sus_touch;
} mb = { .first_now = 1, .clock_mode = MB_CLOCK_AUTO, .sus_touch = -1 };
static int mb_sus_key = -1;              /* MOVE_KEYS' s_susbtn row (looked up at the first touch) */

/* ---- the coroutine (M1-02) */
static double move_now_ms(void)          /* web_hal.c platform_poll: fm1_ms = now - (the first reading) */
{
    /* tests/host.c's first poll is ms 1 and web_hal.c's first reading is its zero: the first reading is 0.25, the
     * n-th (n >= 2) mb.ms + 1.5, so fm1_ms equals host.c's from the second poll on (the first: 0 against 1) */
    if (mb.first_now) { mb.first_now = 0; return 0.25; }
    return (double)mb.ms + 1.5;
}
static void move_yield(void) { coro_yield(&mb.co); }
static void mb_entry(void *arg)
{
    (void)arg;
    coro_entered(&mb.co);
    app_main();                          /* (not web_hal.c's main(): it erases host_mem, which holds the loaded pattern) */
    for (;;) coro_yield(&mb.co);
}
static int mb_boot(void)                 /* the worker: app_main up to its first poll (not on the callback) */
{
    if (mb.booted) return 0;
    mb.stack = aligned_alloc(16, MB_STACK);
    if (!mb.stack) return -1;
    memset(mb.stack, 0, MB_STACK);       /* (touch every page now, not on the callback) */
    coro_init(&mb.co, mb.stack, MB_STACK, mb_entry, 0);
    coro_resume(&mb.co);
    mb.booted = 1;
    return 0;
}

/* ---- persistence (M1-08), the worker: SCHED_OTHER on cores 0-2 (off the audio core 3) as its first action; loads
 * the files and boots the app, then writes what the audio side staged (store.bin, pattern.bin) and, when asked,
 * the timer ring (cpu-ring.log). The data folder: under Schwung's user data when there (it survives a module
 * update: to confirm in D1, plan 9.2 Q3), else the module's folder */
static void mb_pick_dir(const char *module_dir)
{
    struct stat st;
    snprintf(mb.mod_dir, sizeof mb.mod_dir, "%s", module_dir && *module_dir ? module_dir : ".");
    if (strstr(mb.mod_dir, "/modules/") && stat("/data/UserData/schwung", &st) == 0 && S_ISDIR(st.st_mode))
        snprintf(mb.dir, sizeof mb.dir, "/data/UserData/schwung/module_data/frodhes");
    else
        snprintf(mb.dir, sizeof mb.dir, "%s", mb.mod_dir);
}
static void mb_load_files(void)          /* before the boot: what settings_load and seq_load will read */
{
    static uint8_t pat[MB_PAT_N];
    (void)ms_mkdirs(mb.dir);
    memset(host_mem, 0xFF, sizeof host_mem);
    if (ms_load(mb.dir, "store.bin", web_store_buf, sizeof web_store_buf) > 0) mb.loaded |= 1u;
    else memset(web_store_buf, 0, sizeof web_store_buf);
    if (ms_load(mb.dir, "pattern.bin", pat, MB_PAT_N) > 0) { memcpy(host_mem + MB_PAT_LO, pat, MB_PAT_N); mb.loaded |= 2u; }
    memcpy(mb.stg_store, web_store_buf, sizeof web_store_buf);   /* (what is on disk: nothing to write yet) */
    memcpy(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N);
}
static void mb_stage(void);
static uint32_t mb_wr_seq, mb_cpu_wr;
static void mb_write_staged(void)
{
    static uint8_t st[sizeof web_store_buf], pat[MB_PAT_N], last_st[sizeof web_store_buf], last_pat[MB_PAT_N];
    static int have_last;
    uint32_t q1 = atomic_load_explicit(&mb.stg_seq, memory_order_acquire), q2;
    if (q1 == mb_wr_seq || (q1 & 1u)) return;
    memcpy(st, mb.stg_store, sizeof st);
    memcpy(pat, mb.stg_pat, sizeof pat);
    atomic_thread_fence(memory_order_acquire);
    q2 = atomic_load_explicit(&mb.stg_seq, memory_order_relaxed);
    if (q1 != q2) return;                                     /* (copied over meanwhile: next time) */
    if (st[0] == 1 && (!have_last || memcmp(st, last_st, sizeof st))) {
        if (ms_save(mb.dir, "store.bin", st, sizeof st)) mb.save_fail++; else { mb.saves++; memcpy(last_st, st, sizeof st); }
    }
    if (!have_last || memcmp(pat, last_pat, sizeof pat)) {
        int erased = 1;
        for (uint32_t i = 0; i < sizeof pat && erased; i++) erased = pat[i] == 0xFFu;
        if (!erased || have_last) {
            if (ms_save(mb.dir, "pattern.bin", pat, sizeof pat)) mb.save_fail++; else { mb.saves++; memcpy(last_pat, pat, sizeof pat); }
        } else memcpy(last_pat, pat, sizeof pat);
    }
    have_last = 1;
    mb_wr_seq = q1;
}
static void mb_write_cpu(void)
{
    static char line[64];
    char path[600];
    FILE *f;
    uint32_t cn = atomic_load_explicit(&mb.cpu_n, memory_order_acquire);
    if (!atomic_load(&mb.cpu_log)) { mb_cpu_wr = cn; return; }
    if (cn == mb_cpu_wr) return;
    snprintf(path, sizeof path, "%s/cpu-ring.log", mb.mod_dir);
    f = fopen(path, "a");
    if (!f) return;
    if (cn - mb_cpu_wr > MB_CPU_N) mb_cpu_wr = cn - MB_CPU_N;   /* (overran: the gap shows in the indices) */
    while (mb_cpu_wr != cn) {
        uint32_t i = mb_cpu_wr & (MB_CPU_N - 1u);
        int k = snprintf(line, sizeof line, "%u %u %u\n", mb_cpu_wr, mb.cpu_us[i], (unsigned)mb.cpu_fr[i]);
        fwrite(line, 1, (size_t)k, f);
        mb_cpu_wr++;
    }
    fclose(f);
}
static void *mb_worker(void *arg)
{
    struct sched_param sp = { .sched_priority = 0 };
    (void)arg;
    pthread_setschedparam(pthread_self(), SCHED_OTHER, &sp);
#ifdef __linux__
    { cpu_set_t c; CPU_ZERO(&c); CPU_SET(0, &c); CPU_SET(1, &c); CPU_SET(2, &c); sched_setaffinity(0, sizeof c, &c); }
#endif
    if (!mb.booted) {
        mb_load_files();
        if (mb_boot()) { atomic_store(&mb.booting, 0); return 0; }
    }
    mb_cpu_wr = atomic_load(&mb.cpu_n);
    atomic_store_explicit(&mb.ready, 1, memory_order_release);
    atomic_store(&mb.booting, 0);
    while (!atomic_load(&mb.wk_stop)) {
        struct timespec t = { 0, 100 * 1000000L };
        mb_write_staged();
        mb_write_cpu();
        nanosleep(&t, 0);
    }
    mb_write_staged();                                        /* (a last staged save before the module goes) */
    mb_write_cpu();
    return 0;
}
static int mb_start(const char *module_dir)  /* create_instance */
{
    if (!mb.booted) mb_pick_dir(module_dir);
    for (uint32_t i = 0; !mb.booted && i < MB_Q; i++) atomic_store_explicit(&mb.q[i].seq, i, memory_order_relaxed);
    atomic_store(&mb.wk_stop, 0);
    atomic_store(&mb.booting, 1);
    if (pthread_create(&mb.wk, 0, mb_worker, 0)) { atomic_store(&mb.booting, 0); return -1; }
    mb.wk_on = 1;
    return 0;
}
static void mb_stop(void)                /* destroy_instance: the worker writes what is staged and ends (the host
                                          * may dlclose next: none of our code may still run) */
{
    if (!mb.wk_on) return;
    if (atomic_load(&mb.ready)) mb_stage();                   /* (render is not running now: the callbacks are ours) */
    atomic_store(&mb.wk_stop, 1);
    pthread_join(mb.wk, 0);
    mb.wk_on = 0;
    atomic_store(&mb.ready, 0);
}

/* ---- the pre-queue: many producers (on_midi, set_param from the callback or the loader), one consumer (step) */
static int mb_put(int kind, uint8_t st, uint8_t d1, uint8_t d2, uint32_t at, uint16_t key, int32_t v)
{
    uint32_t pos = atomic_load_explicit(&mb.qw, memory_order_relaxed);
    for (;;) {
        mb_ev_t *c = &mb.q[pos & (MB_Q - 1u)];
        uint32_t seq = atomic_load_explicit(&c->seq, memory_order_acquire);
        int32_t dif = (int32_t)(seq - pos);
        if (dif == 0) {
            if (atomic_compare_exchange_weak_explicit(&mb.qw, &pos, pos + 1u, memory_order_relaxed, memory_order_relaxed)) {
                c->kind = (uint8_t)kind; c->st = st; c->d1 = d1; c->d2 = d2; c->at = at; c->key = key; c->v = v;
                atomic_store_explicit(&c->seq, pos + 1u, memory_order_release);
                return 0;
            }
        } else if (dif < 0) { mb.q_full++; return -1; }
        else pos = atomic_load_explicit(&mb.qw, memory_order_relaxed);
    }
}
static mb_ev_t *mb_peek(void)
{
    mb_ev_t *c = &mb.q[mb.qr & (MB_Q - 1u)];
    return atomic_load_explicit(&c->seq, memory_order_acquire) == mb.qr + 1u ? c : 0;
}
static void mb_pop(mb_ev_t *c)
{
    atomic_store_explicit(&c->seq, mb.qr + MB_Q, memory_order_release);
    mb.qr++;
}

/* ---- parameters (M1-06): MOVE_KEYS[] (move/module/move_ui.h, generated) maps a key to a parameter id or a ui.c
 * slot. Writes go the panel's way (ui.c slot_turn: its side effects, cmp_wake, the motor's auto-on memory, the synced
 * rates), set to an absolute value; not as CCs (CC 80 is the Rhodes types only, CC 85 and 86 / 87 are combined) */
static int32_t mb_slot_get(uint32_t s) { int32_t hi; return slot_val(s, &hi); }
static void mb_slot_abs(uint32_t s, int32_t x)
{
    int32_t hi, cur = slot_val(s, &hi), n;
    if (x < 0) x = 0;
    if (x > hi) x = hi;
    if (s < P_COUNT) {
        if (s == P_CMP_SW) { if (x && !PV(P_CMP_SW)) cmp_wake(1); else param_set(P_CMP_SW, x); ui_mark_param(s); return; }
        if (slot_synced(s)) { param_set(s, x); ui_mark_param(s); return; }   /* (the panel would step its SYNC) */
    } else if (s == S_BPM) { tempo_set10((uint32_t)(x + 400)); bpm_hand = set.bpm10; ui_mark(E_CARDS); return; }
    else if (s == S_ARPGATE) { ARP_GATE = (uint8_t)(x + 10); ui_mark(E_CARDS); return; }   /* (the panel steps 5 %; CC 55 any) */
    else if (s == S_SQEDIT) { if ((x != 0) != (sq_erase != 0)) slot_turn(s, 1); ui_mark(E_CARDS); return; }   /* (left: UNDO) */
    for (n = 0; n < 512 && cur != x; n++) {                   /* an enum steps one a turn; an amount moves by d */
        slot_turn(s, x - cur);
        { int32_t now = slot_val(s, &hi); if (now == cur) break; cur = now; }
    }
    if (s < P_COUNT && PV(s) != (uint8_t)x && x <= PARAMS[s].hi) param_set(s, x);   /* past a panel stop (MOTOR
                                                     * RATE: the panel stops at 120, the parameter goes to 127) */
    if (s < P_COUNT) ui_mark_param(s);
    ui_mark(E_CARDS); ui_mark(E_FOOTER);
}
static int mb_key_find(const char *key)
{
    for (uint32_t i = 0; i < MOVE_NKEYS; i++) if (!strcmp(MOVE_KEYS[i].key, key)) return (int)i;
    return -1;
}
/* the wire text -> the slot value: wire = (slot - off) / scale. Numbers only ("12", "-3", "120.5"); an enum by
 * its index (Schwung sends the index for an enum: to confirm on the device, then labels here if not) */
static int mb_parse_wire(const char *s, const move_key_t *k, int32_t *out)
{
    int neg = 0, any = 0; int64_t ip = 0, fr = 0, fd = 1;
    if (!s) return -1;
    while (*s == ' ') s++;
    if (*s == '-' || *s == '+') neg = *s++ == '-';
    while (*s >= '0' && *s <= '9') { ip = ip * 10 + (*s++ - '0'); any = 1; if (ip > 1000000) return -1; }
    if (*s == '.') { s++; while (*s >= '0' && *s <= '9') { if (fd < 1000000) { fr = fr * 10 + (*s - '0'); fd *= 10; } s++; any = 1; } }
    while (*s == ' ') s++;
    if (!any || *s) return -1;
    {
        int64_t num = (ip * fd + fr) * (k->scale ? k->scale : 1);   /* (wire * scale) * fd */
        int64_t v = (num + fd / 2) / fd;
        if (neg) v = -((num + fd / 2) / fd);
        *out = (int32_t)(v + k->off);
    }
    return 0;
}
static int mb_wire_text(const move_key_t *k, char *buf, int n)
{
    int32_t v;
    switch (k->kind) {
    case MK_PARAM: case MK_SLOT: v = mb_slot_get(k->slot) - k->off; break;
    case MK_LATCH:
        v = k->cc == 64u ? (sus_latched || sus_held) : k->cc == 60u ? seq_play != 0 : k->cc == 63u ? seq_rec != 0 : 0;
        break;
    case MK_BTN: v = sus_held; break;        /* the sustain control is down */
    default: v = 0; break;
    }
    if (k->scale > 1u) {
        int32_t a = v < 0 ? -v : v, sc = k->scale;
        int dec = sc >= 100 ? 2 : 1;
        return snprintf(buf, (size_t)n, "%s%d.%0*d", v < 0 ? "-" : "", a / sc, dec, (a % sc) * (dec == 2 ? 100 : 10) / sc);
    }
    return snprintf(buf, (size_t)n, "%d", v);
}
static void mb_key_apply(uint32_t i, int32_t x)
{
    const move_key_t *k = &MOVE_KEYS[i];
    if (k->kind == MK_PARAM || k->kind == MK_SLOT) mb_slot_abs(k->slot, x);
    else if (k->cc == 64u) {                   /* SUSTAIN: the FM-1's REC latch (a MIDI pedal let up also frees it) */
        sus_latched = x != 0; sustain_update();
    } else if (k->kind == MK_BTN) {           /* the sustain control (M1-13b): the FM-1's REC button itself, so the
                                                 * FM-1's own code times it (frodhes.c sustain_button: held = the
                                                 * pedal, a tap under SUS_TAP_MS flips the latch), as the web
                                                 * emulator's REC does */
        if (x) mb.sus_down++; else mb.sus_up++;
        web_btn(BTN_SUSTAIN, x != 0);
    } else if (k->kind == MK_LATCH) midi_cc(k->cc, x ? 127u : 0u);
    else if (k->kind == MK_TRIG) { if (x) midi_cc(k->cc, 127u); }
    mb.keys_set++; mb.rev++;
}

/* ---- the state (M1-07): a flat JSON object, about 900 B: the 256 B settings record as the app holds it now (the
 * live knobs in panel / panel2 / panel3), the sound it was loaded as (snd_base: what EDITED compares with), the
 * tempo set by hand. The user presets and the pattern are the device's (files, M1-08), not a slot's. */
static const char MB_HEX[] = "0123456789abcdef";
static int mb_hex_put(char *o, const void *p, uint32_t n)
{
    const uint8_t *b = p;
    for (uint32_t i = 0; i < n; i++) { o[2 * i] = MB_HEX[b[i] >> 4]; o[2 * i + 1] = MB_HEX[b[i] & 15u]; }
    return (int)(2u * n);
}
static int mb_hex_get(const char *s, void *p, uint32_t n)
{
    uint8_t *b = p;
    for (uint32_t i = 0; i < 2u * n; i++) {
        char c = s[i]; int v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (v < 0) return -1;
        if (i & 1u) b[i >> 1] = (uint8_t)(b[i >> 1] << 4 | v); else b[i >> 1] = (uint8_t)v;
    }
    return 0;
}
#define MB_STATE_N (40 + 2 * 256 + 12 + 2 * P_SLOTS + 24 + 24)
static int mb_state_get(char *buf, int n)
{
    char *o = buf;
    if (n < MB_STATE_N + 1) return -1;
    o += sprintf(o, "{\"v\":\"1\",\"set\":\"");
    o += mb_hex_put(o, &set, sizeof set);
    o += sprintf(o, "\",\"base\":\"");
    o += mb_hex_put(o, snd_base, P_SLOTS);
    o += sprintf(o, "\",\"hand\":\"%u\",\"rev\":\"%u\"}", (unsigned)bpm_hand, (unsigned)mb.rev);
    return (int)(o - buf);
}
static const char *mb_json_str(const char *j, const char *key, uint32_t *len)   /* "key":"value" in a flat object */
{
    char pat[24];
    const char *p, *q;
    snprintf(pat, sizeof pat, "\"%s\":\"", key);
    p = strstr(j, pat);
    if (!p) return 0;
    p += strlen(pat);
    q = strchr(p, '"');
    if (!q) return 0;
    *len = (uint32_t)(q - p);
    return p;
}
static int mb_state_set(const char *j)   /* parse into the mailbox; step() applies it between two passes */
{
    uint32_t ls = 0, lb = 0, lh = 0, lv = 0;
    const char *v = j ? mb_json_str(j, "v", &lv) : 0, *s = j ? mb_json_str(j, "set", &ls) : 0;
    const char *b = j ? mb_json_str(j, "base", &lb) : 0, *h = j ? mb_json_str(j, "hand", &lh) : 0;
    int expect = 0;
    if (!v || lv != 1 || v[0] != '1' || !s || ls != 2u * sizeof(settings_t)) { mb.state_bad++; return -1; }
    while (!atomic_compare_exchange_weak(&mb.st_flag, &expect, 1)) {
        if (expect == 3) { mb.state_bad++; return -1; }       /* (being applied right now: the host retries a load) */
        if (expect != 0 && expect != 2) expect = 0;
    }
    if (mb_hex_get(s, &mb.st_set, sizeof mb.st_set)) { atomic_store(&mb.st_flag, 0); mb.state_bad++; return -1; }
    if (!b || lb != 2u * P_SLOTS || mb_hex_get(b, mb.st_base, P_SLOTS))
        for (uint32_t i = 0; i < P_SLOTS; i++) mb.st_base[i] = i < 32u ? mb.st_set.panel[i] : i < 48u ? mb.st_set.panel2[i - 32u] : mb.st_set.panel3[i - 48u];
    mb.st_hand = mb.st_set.bpm10;
    if (h && lh > 0 && lh < 6) { uint32_t x = 0; for (uint32_t i = 0; i < lh; i++) x = x * 10u + (uint32_t)(h[i] - '0'); mb.st_hand = (uint16_t)x; }
    atomic_store(&mb.st_flag, 2);
    return 0;
}
static void mb_state_apply(void)         /* the boot's sanitize and apply steps (frodhes.c app_main), at run time */
{
    int expect = 2;
    if (!atomic_compare_exchange_strong(&mb.st_flag, &expect, 3)) return;
    set = mb.st_set;
    if (set.ver != 1u && set.ver != SET_VER) params_defaults();   /* (as the boot: a record it cannot read) */
    params_sanitize();
    kv_apply();
    arp_sanitize();
    chord_sanitize();
    seq_sanitize();
    if (set.usb_fixed > 1u) set.usb_fixed = 0;
    if (set.palette >= COLOR_N) set.palette = 0;
    if (set.dim >= DIM_N) set.dim = 0;
    if (set.leds > 4u) set.leds = 0;
    usb_pre_master = set.usb_fixed;
    uart_midi_on = !set.trs_off;
    dim_apply();
    params_apply_all();
    tempo_set10(set.bpm10);
    bpm_hand = mb.st_hand >= 400u && mb.st_hand <= 2400u ? mb.st_hand : set.bpm10;
    for (uint32_t i = 0; i < P_SLOTS; i++) snd_base[i] = i < P_COUNT && mb.st_base[i] <= PARAMS[i].hi ? mb.st_base[i] : PV(i);
    rev_relatch();
    tempo_sync_publish();
    set_dirty = 1;
    ui_draw_page_change();
    mb.state_ok++; mb.rev++;
    atomic_store(&mb.st_flag, 0);
}

/* ---- the clock (M1-05): a real F8 stream wins; without one (AUTO) or by choice (HOST) the host's transport and
 * beat position become 24 PPQN with Start / Continue / Stop, each pulse at its own ms inside the block */
static void mb_midi_due(uint8_t st, uint32_t at) { if (!mb_put(MB_EV_MIDI, st, 0, 0, at, 0, 0)) mb.f8_synth += st == 0xF8u; }
static void mb_clock_block(int frames)
{
    const host_api_v1_t *h = g_host;
    double b = -1.0, ms_per = (double)frames * 1000.0 / 44100.0;
    int running;
    uint32_t n = 0;
    if (!h || !h->get_clock_status || mb.clock_mode == MB_CLOCK_MIDI) return;
    if (mb.clock_mode == MB_CLOCK_AUTO && mb.real_f8 && mb.blocks - mb.real_f8_blk < MB_CLOCK_REAL_BLOCKS) { mb.clk_run = 0; return; }
    running = h->get_clock_status() == MOVE_CLOCK_STATUS_RUNNING;
    if (running && h->get_beat_position) b = h->get_beat_position();
    if (running && !mb.clk_run) {                          /* the transport starts: Start at bar 1, else Continue */
        int from_top = !h->get_beat_position || (b >= 0.0 && b < 1.0 / 24.0);
        mb_midi_due(from_top ? 0xFAu : 0xFBu, mb.ms + 1u);
        mb.tick = from_top ? 0u : (uint32_t)(b * 24.0 + 0.999999);
        mb.acc = 0.0; mb.beat_prev = b;
    } else if (!running && mb.clk_run) mb_midi_due(0xFCu, mb.ms + 1u);
    mb.clk_run = running;
    if (!running) return;
    if (h->get_beat_position && b >= 0.0) {
        double t = b * 24.0, tp = mb.beat_prev * 24.0;
        if (t + 1.0 < (double)mb.tick || t > (double)mb.tick + 48.0) mb.tick = (uint32_t)(t + 0.999999);   /* a jump */
        while ((double)mb.tick <= t && n++ < 16u) {
            double fr = t > tp ? ((double)mb.tick - tp) / (t - tp) : 1.0;
            if (fr < 0.0) fr = 0.0;
            if (fr > 1.0) fr = 1.0;
            mb_midi_due(0xF8u, mb.ms + 1u + (uint32_t)(fr * ms_per));
            mb.tick++;
        }
        mb.beat_prev = b;
    } else if (h->get_bpm) {                               /* no beat position (an older host): integrate the tempo */
        double bpm = h->get_bpm();
        if (bpm < 20.0 || bpm > 400.0) bpm = 120.0;
        mb.acc += bpm * 24.0 / 60.0 * (double)frames / 44100.0;
        while (mb.acc >= 1.0 && n++ < 16u) { mb.acc -= 1.0; mb_midi_due(0xF8u, mb.ms + 1u); }
    }
}

/* ---- one host millisecond (see the top) */
static void mb_step(void)
{
    mb_ev_t *e;
    mb.ms++;
    mb.steps++;
    while ((e = mb_peek()) != 0 && (e->at == 0 || (int32_t)(e->at - mb.ms) <= 0)) {
        if (e->kind == MB_EV_MIDI) {
            if (mq_w - mq_r >= MB_HOST_RING) break;          /* the app's ring is full: the rest wait a millisecond */
            web_midi(e->st, e->d1, e->d2);
            mb.midi_out++;
        } else if (e->kind == MB_EV_KEY && e->key < MOVE_NKEYS) mb_key_apply(e->key, e->v);
        else if (e->kind == MB_EV_PRESET) { uint8_t p[P_COUNT]; if (preset_values((uint32_t)e->v, p) == 0) { preset_load((uint32_t)e->v); mb.rev++; } }
        mb_pop(e);
    }
    mb_state_apply();
    mb.owed += FS;
    while (mb.owed >= 1000u * HALF_FRAMES) {
        static int32_t blk[2u * HALF_FRAMES];
        uint32_t i;
        mb.owed -= 1000u * HALF_FRAMES;
        memset(blk, 0, sizeof blk);
        app_audio(blk, HALF_FRAMES);
        for (i = 0; i < HALF_FRAMES; i++) {                  /* MASTER at unity, then the FM-1's clip */
            int32_t l = blk[2u * i], r = blk[2u * i + 1u];
            uint32_t w = (mb.fw + i) & (MB_FIFO - 1u);
            mb.fifo[2u * w] = (int16_t)(l > 32767 ? 32767 : l < -32767 ? -32767 : l);
            mb.fifo[2u * w + 1u] = (int16_t)(r > 32767 ? 32767 : r < -32767 ? -32767 : r);
        }
        mb.fw += HALF_FRAMES;
    }
    coro_resume(&mb.co);
}

/* ---- persistence (M1-08), the audio side: once a second, between two main-loop passes, when the app saved (its
 * store record dirty, or the pattern's sectors changed) and the sound is quiet (the app saves only then anyway),
 * copy both into the staging buffers: ~6 KB of memcpy, no syscall */
static void mb_stage(void)
{
    uint32_t q;
    int dirty = web_store_dirty != 0, pat = memcmp(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N) != 0;
    if (!(dirty || pat || !mb.stg_have) || !eng_quiet() || !fx_quiet()) return;
    q = atomic_load_explicit(&mb.stg_seq, memory_order_relaxed);
    atomic_store_explicit(&mb.stg_seq, q + 1u, memory_order_relaxed);   /* odd: copying */
    atomic_thread_fence(memory_order_release);
    memcpy(mb.stg_store, web_store_buf, sizeof web_store_buf);
    memcpy(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N);
    atomic_store_explicit(&mb.stg_seq, q + 2u, memory_order_release);
    web_store_dirty = 0;
    mb.stg_have = 1;
}

static uint64_t mb_now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec; }
static void mb_render_in(int16_t *out, int frames);
static void mb_render(int16_t *out, int frames)
{
    uint64_t t0 = mb_now_ns(), d;
    uint32_t us;
    mb_render_in(out, frames);
    d = (mb_now_ns() - t0 + 500u) / 1000u;
    us = d > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)d;
    {
        uint32_t c = atomic_load_explicit(&mb.cpu_n, memory_order_relaxed);
        mb.cpu_us[c & (MB_CPU_N - 1u)] = us;
        mb.cpu_fr[c & (MB_CPU_N - 1u)] = (uint16_t)(frames < 0 ? 0 : frames);
        atomic_store_explicit(&mb.cpu_n, c + 1u, memory_order_release);
    }
    if (us > mb.cpu_max) mb.cpu_max = us;
}
/* dev_cpu_ring: the callbacks since the last read, one line each: "<callback index> <us> <frames>" (index from 0
 * at create; a gap in the indices = the ring overran between two reads). tools/move_hil.py RING_LINE */
static int mb_cpu_ring(char *buf, int n)
{
    int o = 0;
    uint32_t cn = atomic_load_explicit(&mb.cpu_n, memory_order_acquire);
    if (cn - mb.cpu_rd > MB_CPU_N) mb.cpu_rd = cn - MB_CPU_N;
    while (mb.cpu_rd != cn && n - o > 32) {
        uint32_t i = mb.cpu_rd & (MB_CPU_N - 1u);
        o += snprintf(buf + o, (size_t)(n - o), "%u %u %u\n", mb.cpu_rd, mb.cpu_us[i], (unsigned)mb.cpu_fr[i]);
        mb.cpu_rd++;
    }
    if (o < n) buf[o] = 0;
    return o;
}
static void mb_render_in(int16_t *out, int frames)
{
    uint32_t i, n = frames < 0 ? 0u : (uint32_t)frames;
    if (n > MB_FIFO - HALF_FRAMES) n = MB_FIFO - HALF_FRAMES;
    if (!atomic_load_explicit(&mb.ready, memory_order_acquire)) { memset(out, 0, (size_t)frames * 4u); return; }
    mb_clock_block(frames);
    while (mb.fw - mb.fr < n) mb_step();
    for (i = 0; i < n; i++) {
        uint32_t r = (mb.fr + i) & (MB_FIFO - 1u);
        out[2u * i] = mb.fifo[2u * r]; out[2u * i + 1u] = mb.fifo[2u * r + 1u];
    }
    for (; i < (uint32_t)frames; i++) out[2u * i] = out[2u * i + 1u] = 0;
    mb.fr += n;
    mb.blocks++;
    if (++mb.stg_blk >= MB_STAGE_BLOCKS) { mb.stg_blk = 0; mb_stage(); }
}

/* ---- MIDI in (M1-04): everything a sound generator gets goes to the app as it came (notes, release velocity, CC,
 * pressure, bend, program change, clock), except the knob / jog touch edges (source 5, notes 0-9: never played) and
 * the audio-FX-only broadcast. The app's CHANNEL setting filters, as on the FM-1. SysEx is dropped */
static void mb_midi_in(const uint8_t *m, int len, int source)
{
    uint8_t st;
    if (!m || len < 1) return;
    if (source == MOVE_MIDI_SOURCE_TOUCH) {   /* the direct route (M1-13b): a touch edge on the chosen control
                                               * (dev_sus_touch: 0-7 a knob, 9 the jog wheel; -1 off, the default)
                                               * is the sustain control; every other touch is dropped */
        int n = len > 1 ? m[1] & 127 : -1, on = (m[0] & 0xF0u) == 0x90u && len > 2 && m[2] != 0;
        if (n == atomic_load(&mb.sus_touch) && n >= 0 && (mb_sus_key >= 0 || (mb_sus_key = mb_key_find("s_susbtn")) >= 0)) (void)mb_put(MB_EV_KEY, 0, 0, 0, 0, (uint16_t)mb_sus_key, on);
        return;
    }
    if (source == MOVE_MIDI_SOURCE_FX_BROADCAST) return;
    st = m[0];
    if (st < 0x80u || st == 0xF0u || st == 0xF7u) return;
    if (st == 0xF8u) { mb.real_f8 = 1; mb.real_f8_blk = mb.blocks; }
    if (mb.clock_mode == MB_CLOCK_HOST && (st == 0xF8u || st == 0xFAu || st == 0xFBu || st == 0xFCu)) return;
    mb.midi_in++;
    (void)mb_put(MB_EV_MIDI, st, len > 1 ? m[1] & 127u : 0u, len > 2 ? m[2] & 127u : 0u, 0, 0, 0);
}

/* ---- get_param / set_param */
static int mb_get(const char *key, char *buf, int n)
{
    int i;
    if (!key || !buf || n <= 0) return -1;
    if (!strcmp(key, "dev_ready")) return snprintf(buf, (size_t)n, "%d", atomic_load(&mb.ready));
    if (!atomic_load_explicit(&mb.ready, memory_order_acquire) && strcmp(key, "chain_params") && strcmp(key, "ui_hierarchy"))
        return -1;                       /* (the worker still boots the app: nothing of it to read yet) */
    if (!strcmp(key, "state")) return mb_state_get(buf, n);
    if ((i = mb_key_find(key)) >= 0) return mb_wire_text(&MOVE_KEYS[i], buf, n);
    if (!strcmp(key, MOVE_GATE_KEY)) return snprintf(buf, (size_t)n, "%u", (unsigned)TYPE_UI[PV(P_TYPE) < N_TYPE_N ? PV(P_TYPE) : 0u]);
    if (!strcmp(key, "preset")) return snprintf(buf, (size_t)n, "%u", (unsigned)set.preset);
    if (!strcmp(key, "preset_count")) return snprintf(buf, (size_t)n, "%u", (unsigned)(PR_NF + PR_NU));
    if (!strcmp(key, "preset_name")) { char s[20]; preset_name(set.preset < PR_NF + PR_NU ? set.preset : 0u, s); return snprintf(buf, (size_t)n, "%s", s); }
    if (!strcmp(key, "ui_hierarchy")) { if ((int)sizeof MOVE_UI_HIERARCHY > n) return -1; memcpy(buf, MOVE_UI_HIERARCHY, sizeof MOVE_UI_HIERARCHY); return (int)sizeof MOVE_UI_HIERARCHY - 1; }
    if (!strcmp(key, "chain_params")) { if ((int)sizeof MOVE_CHAIN_PARAMS > n) return -1; memcpy(buf, MOVE_CHAIN_PARAMS, sizeof MOVE_CHAIN_PARAMS); return (int)sizeof MOVE_CHAIN_PARAMS - 1; }
    if (!strcmp(key, "rev")) return snprintf(buf, (size_t)n, "%u", mb.rev);
    if (!strcmp(key, "dev_cpu_ring")) return mb_cpu_ring(buf, n);
    if (!strcmp(key, "dev_store")) return snprintf(buf, (size_t)n, "dir=%s loaded=%u saves=%u fail=%u", mb.dir, mb.loaded, mb.saves, mb.save_fail);
    if (!strcmp(key, "dev_pattern_crc")) return snprintf(buf, (size_t)n, "%08x", ms_crc32(host_mem + MB_PAT_LO, MB_PAT_N));
    if (!strcmp(key, "dev_sus")) return snprintf(buf, (size_t)n, "held=%u latched=%u midi=%u down=%u up=%u touch=%d",
                                                 (unsigned)sus_held, (unsigned)sus_latched, (unsigned)sus_midi,
                                                 mb.sus_down, mb.sus_up, atomic_load(&mb.sus_touch));
    if (!strcmp(key, "dev_cpu")) return snprintf(buf, (size_t)n, "callbacks=%u max_us=%u", atomic_load(&mb.cpu_n), mb.cpu_max);
    /* development reads (tests, the device session) */
    if (!strcmp(key, "dev_stats"))
        return snprintf(buf, (size_t)n, "ms=%u steps=%u blocks=%u midi_in=%u midi_out=%u q_full=%u f8_synth=%u keys=%u keys_bad=%u "
                        "state_ok=%u state_bad=%u clock_mode=%d", mb.ms, mb.steps, mb.blocks, mb.midi_in, mb.midi_out,
                        mb.q_full, mb.f8_synth, mb.keys_set, mb.keys_bad, mb.state_ok, mb.state_bad, mb.clock_mode);
    if (!strncmp(key, "dev:", 4)) {
        const char *k = key + 4;
        if (!strcmp(k, "bpm10")) return snprintf(buf, (size_t)n, "%u", tempo_bpm10());
        if (!strcmp(k, "clklock")) return snprintf(buf, (size_t)n, "%u", (unsigned)clk_follows());
        if (!strcmp(k, "seqplay")) return snprintf(buf, (size_t)n, "%u", (unsigned)seq_play);
        if (!strcmp(k, "seq")) return snprintf(buf, (size_t)n, "rec=%u n=%u dirty=%u step=%u quiet=%d/%d/%d",
                                               (unsigned)seq_rec, (unsigned)sq_n, (unsigned)sq_dirty, (unsigned)sq_sv_step,
                                               eng_idle(), fx_quiet(), tempo_flash_ok());
        if (!strcmp(k, "fm1_ms")) return snprintf(buf, (size_t)n, "%u", fm1_ms);
        if (!strncmp(k, "pv", 2)) { int id = atoi(k + 2); return id >= 0 && id < (int)P_COUNT ? snprintf(buf, (size_t)n, "%u", PV(id)) : -1; }
    }
    return -1;
}
static void mb_set(const char *key, const char *val)
{
    int i;
    size_t kl;
    if (!key) return;
    kl = strlen(key);
    if (!strcmp(key, "state") || (kl > 6 && !strcmp(key + kl - 6, ":state"))) { (void)mb_state_set(val); return; }
    if (!val) return;
    if ((i = mb_key_find(key)) >= 0) {
        int32_t x;
        if (mb_parse_wire(val, &MOVE_KEYS[i], &x)) { mb.keys_bad++; return; }
        (void)mb_put(MB_EV_KEY, 0, 0, 0, 0, (uint16_t)i, x);
        return;
    }
    if (!strcmp(key, "preset")) { int p = atoi(val); if (p >= 0 && p < (int)(PR_NF + PR_NU)) (void)mb_put(MB_EV_PRESET, 0, 0, 0, 0, 0, p); return; }
    if (!strcmp(key, "dev_midi")) {                       /* "T ST D1 D2": a message due at host ms T (the harness) */
        unsigned t, st, d1, d2;
        if (sscanf(val, "%u %i %i %i", &t, &st, &d1, &d2) == 4 && st >= 0x80u && st <= 0xFFu)
            (void)mb_put(MB_EV_MIDI, (uint8_t)st, (uint8_t)(d1 & 127u), (uint8_t)(d2 & 127u), t ? t : 1u, 0, 0);
        return;
    }
    if (!strcmp(key, "dev_cpu_log")) { atomic_store(&mb.cpu_log, atoi(val) != 0); return; }
    if (!strcmp(key, "dev_sus_touch")) { int t = atoi(val); atomic_store(&mb.sus_touch, t >= 0 && t <= 9 && t != 8 ? t : -1); return; }
    if (!strcmp(key, "dev_clock")) { int m = atoi(val); if (m >= MB_CLOCK_AUTO && m <= MB_CLOCK_HOST) mb.clock_mode = m; return; }
}
