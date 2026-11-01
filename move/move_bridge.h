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
 * The app is one set of globals: one app per process. Several instances may exist (a re-pick creates the new one
 * before it destroys the old; two slots share the dlopen'd copy): the newest owns the app (move_plugin.c). The
 * app keeps running after the last destroy_instance (a later create continues it, with all notes off), because
 * its static data cannot be initialised a second time.
 *
 * Things the device session may change (each one place here): the clock source (MB_CLOCK_*), the state layout
 * (mb_state_get / mb_state_set), enum values by index or by label (mb_parse_wire), which MIDI sources play. */
#include <pthread.h>
#include <sched.h>
#include <sys/file.h>
#include <dlfcn.h>
#include <stdatomic.h>
#include <time.h>
#include "move_store.h"
#include "move_presets.h"
#include "coro/coro.h"

#define MB_STACK (256u * 1024u)          /* the coroutine's stack (app_main and what it calls; ui_draw is the deepest) */
#define MB_FIFO 1024u                    /* frames (power of two) */
#define MB_Q 1024u                       /* the pre-queue: MIDI and parameter writes (power of two) */
#define MB_HOST_RING 64u                 /* web_hal.c's mq[] */
#define MB_PAT_LO (SQ_A < SQ_B ? SQ_A : SQ_B) /* the pattern's sectors in host_mem (frodhes_store.h) */
#define MB_PAT_N ((SQ_A < SQ_B ? SQ_B : SQ_A) + 0x1000u - MB_PAT_LO)
#define MB_STAGE_BLOCKS 345u             /* look for something to save about once a second */
#define MB_CPU_N 4096u                   /* the per-callback timer ring (power of two): ~12 s of callbacks */

enum { MB_EV_MIDI = 1, MB_EV_KEY, MB_EV_PRESET, MB_EV_HQ, MB_EV_USAVE };   /* (MB_EV_HQ: p_hq, applied in order with the
                                                          * rest; MB_EV_USAVE: u_save, the sound into a user slot) */
enum { MB_CLOCK_AUTO, MB_CLOCK_MIDI, MB_CLOCK_HOST };   /* AUTO: synthesise from the host only while no F8 arrives */
#define MB_CLOCK_REAL_BLOCKS 690u        /* ~2 s: an F8 seen this recently means the slot gets a real clock */

typedef struct {
    _Atomic uint32_t seq;
    uint8_t kind, st, d1, d2;
    uint32_t at;                         /* the host ms it is due (0: at once) */
    int32_t v;
    uint16_t key;
} mb_ev_t;

/* No FM-1 sequencer, chords or scales on the Move (the user, 2026-10-28: the Move has its own), and the Move's
 * tempo. The FM-1 code stays as it is (Gate S); here they are inert: their CCs never reach the app (MB_CC_DROP:
 * 43-46 the sequencer's edits, 56-59 the chords, 60-63 PLAY, BARS, QUANT, REC; the scales, BPM and CLOCK have no
 * CC), the pattern file is not loaded (an older pattern.bin stays on disk, unused), and mb_inert stops what a
 * transport Start or an older state would turn on (the sequencer playing or recording, a chord type, a scale mode,
 * a transpose, CLOCK INT). The arpeggiator plays (the user, 2026-10-30, reversing 2026-10-28): its CCs 52-55 reach
 * the app, its state is the slot's (the state's set.arp; a new instance starts with it OFF, not with what another
 * instance left in store.bin), and it steps on the Move's clock (CLOCK EXT: F8 / FA / FC, else the host's tempo).
 * Its HOLD (2026-11-01) is the s_arphold card (MK_HOLD): the FM-1 has no CC for it (SEL on its ARPEGGIO page), so no
 * CC is let through for it here; it is in set.arp, so it goes with the state as MODE does. */
#define MB_CC_DROP(cc) (((cc) >= 43u && (cc) <= 46u) || ((cc) >= 56u && (cc) <= 63u))
#define MB_NMAP 6                        /* the mappings' settings: 2 sources x (CC, LO, HI) */
#define MB_MAP_DEFAULT {0, 0, 127, 0, 0, 127}   /* every mapping off (CC 0), LO 0, HI 127 */
static struct {
    coro_t co;
    void *stack;
    int booted;
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
    uint32_t rt_f8, rt_fa, rt_fb, rt_fc;  /* realtime bytes that came in (on_midi, any clock mode): the real clock */
    int h_run, h_bpm10;                   /* the host's transport (get_clock_status RUNNING) and tempo, each block */
    /* the state mailbox (M1-07): 0 free, 1 being written, 2 ready, 3 being applied */
    _Atomic int st_flag;
    settings_t st_set;
    uint8_t st_base[P_SLOTS];
    uint16_t st_hand;
    uint32_t rev;                        /* bumps on every applied change (state's "rev") */
    unsigned eng_seen, eng_init;         /* is_loading: the engine the last read saw (mb_engine_moved) */
    uint32_t eng_moves;
    uint64_t eng_moved_ms;
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
    uint32_t stg_imp;                    /* the import (below) the staged record holds: imp_done at the staging */
    /* the shared store (several instances, move_loader.c): the user presets another instance wrote to store.bin, read
     * by the worker and applied by the audio side when quiet (mb_stage): imp_seq odd while the worker copies, even =
     * that import's id; imp_done the last id applied */
    uint8_t imp[ST_TAIL_N];
    _Atomic uint32_t imp_seq;
    uint32_t imp_done;
    pthread_t wk; int wk_on; _Atomic int wk_stop, booting;
    _Atomic int ready;                   /* the app has booted (the worker booted it): render plays */
    char dir[512], mod_dir[512], idir[512];   /* the data folder (shared), the module's, this instance's own */
    _Atomic int cpu_log;                 /* dev: the worker appends the timer ring to <module dir>/cpu-ring.log */
    _Atomic int stats_log;               /* dev: the worker appends dev_stats to <module dir>/stats.log, 10 a second */
    uint32_t saves, save_fail, loaded, usaves;
    /* the sustain control (M1-13b): presses / releases seen; the touch note that is the control (-1: none) */
    uint32_t sus_down, sus_up;
    _Atomic int sus_touch;
    /* the Move-only mappings (M6-03, U9; tools/gen_move_module.py MAP_KEYS): per source (0 channel pressure, 1 poly
     * pressure) its CC (0: off), LO, HI; the state's copy; poly pressure per note; the last value sent per source */
    uint8_t map[MB_NMAP], st_map[MB_NMAP];
    int st_map_have;
    uint8_t pa[128];
    int16_t map_last[MB_NMAP / 3];
    uint32_t map_out, map_drop;
    uint32_t cc_drop, inert_n, bpm_host;            /* MB_CC_DROP's messages dropped; mb_inert's stops; tempo takes */
    /* the compressor's gain-reduction meter (r_cmp_gr), as ui.c meter_poll shows it on the FM-1's COMP page: dB x 10
     * (rises at once, falls 0.5 dB a 33 ms tick = 15 dB/s), the peak's segment (GR_DB10) held a second; written by
     * the audio thread (mb_gr_tick), read by get_param without a lock: bits 0-9 dB x 10, 10-19 the peak, 20 on */
    int32_t gr_db10;
    uint32_t gr_t, gr_peak_t;
    uint8_t gr_peak;
    _Atomic uint32_t gr_word;
} mb = { .first_now = 1, .clock_mode = MB_CLOCK_AUTO, .sus_touch = -1, .map = MB_MAP_DEFAULT, .map_last = {-1, -1} };
static int mb_sus_key = -1;              /* MOVE_KEYS' s_susbtn row (looked up at the first touch) */
/* dev: what reached the plugin (dev_rx: the last MB_RX_N messages but the clock, with their source and the host ms)
 * and every change of FX (dev_fxlog: the ms, the new state, what made it: a key's row or a MIDI CC 111), for the pad
 * velocity and the FX arrow reports of 2026-10-30. Written on the callback, read by get_param without a lock (dev) */
#define MB_RX_N 1024u
static struct { uint32_t ms[MB_RX_N]; uint8_t st[MB_RX_N], d1[MB_RX_N], d2[MB_RX_N], src[MB_RX_N]; _Atomic uint32_t n; } mb_rx;
#define MB_FXLOG_N 32u
static struct { uint32_t ms[MB_FXLOG_N]; uint8_t on[MB_FXLOG_N]; char why[MB_FXLOG_N][12]; _Atomic uint32_t n; int last; } mb_fxlog = { .last = -1 };
static int mb_fxlog_cc;
static void mb_fx_note(const char *why)  /* after an event: FX changed? */
{
    int on = fx_bypass == 0;
    uint32_t i;
    if (on == mb_fxlog.last) return;
    mb_fxlog.last = on;
    i = atomic_load_explicit(&mb_fxlog.n, memory_order_relaxed) % MB_FXLOG_N;
    mb_fxlog.ms[i] = mb.ms; mb_fxlog.on[i] = (uint8_t)on;
    snprintf(mb_fxlog.why[i], sizeof mb_fxlog.why[i], "%s", why);
    atomic_fetch_add_explicit(&mb_fxlog.n, 1u, memory_order_release);
}

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
 * the timer ring (cpu-ring.log) and dev_stats (stats.log), both in the module's folder. The data folder: /data/UserData/schwung/module_data/frodhes when Schwung's user
 * data is there, else the module's folder; for a copy the loader made (move_loader.c: <data>/inst/<k>) the folder
 * above inst/, with this instance's own files (boot.txt, tap/, cpu-ring.log, stats.log) in <k>. From Schwung 1.8.1's source (plan 9.2 Q3): a module install or update
 * and a Schwung update both merge files by rename into /data/UserData/schwung (nothing there is deleted, except
 * modules/store), and a module uninstall deletes only the module's own folder. So this folder survives all three.
 * (presets/<id>/ would too, but the host's preset browser lists every .json file there.) */
static void mb_pick_dir(const char *module_dir)
{
    struct stat st;
    size_t n;
    const char *k;
    snprintf(mb.mod_dir, sizeof mb.mod_dir, "%s", module_dir && *module_dir ? module_dir : ".");
    n = strlen(mb.mod_dir);
    k = n ? mb.mod_dir + n : mb.mod_dir;
    while (k > mb.mod_dir && k[-1] >= '0' && k[-1] <= '9') k--;
    if (*k && k - mb.mod_dir >= 6 && !strncmp(k - 6, "/inst/", 6)) {   /* a copy of the loader (move_loader.c):
                                                                     * <data>/inst/<k>, the data folder above it */
        snprintf(mb.dir, sizeof mb.dir, "%.*s", (int)(k - 6 - mb.mod_dir), mb.mod_dir);
        if (!mb.dir[0]) snprintf(mb.dir, sizeof mb.dir, "/");
        snprintf(mb.idir, sizeof mb.idir, "%s", mb.mod_dir);
        return;
    }
    if (strstr(mb.mod_dir, "/modules/") && stat("/data/UserData/schwung", &st) == 0 && S_ISDIR(st.st_mode))
        snprintf(mb.dir, sizeof mb.dir, "/data/UserData/schwung/module_data/frodhes");
    else
        snprintf(mb.dir, sizeof mb.dir, "%s", mb.mod_dir);
    snprintf(mb.idir, sizeof mb.idir, "%s", mb.dir);
}
/* ---- the shared store: one store.bin in the data folder for every instance (move_loader.c gives each slot its own
 * copy of the plugin; they share the data folder). The settings are the last writer's; the user presets (the tail,
 * U01-U16) are one set: a write keeps the presets on disk unless this instance changed its own (one writer at a
 * time: flock on .store.lock, read, merge, write), and a store.bin another instance wrote is read here (the worker:
 * a stat every 100 ms) and its presets handed to the app (mb_stage applies them when quiet). Store.bin keeps its
 * place, name and format: nothing to migrate. Worker only. */
#define MB_TAIL_AT (4u + 256u)
static uint8_t mb_base_tail[ST_TAIL_N];  /* the presets this instance and the file last agreed on */
static struct { dev_t dev; ino_t ino; off_t size; long sec, nsec; int have; } mb_sig;
static void mb_store_base(const uint8_t *rec)
{
    if (rec[0] == 1 && rec[1] == 2) memcpy(mb_base_tail, rec + MB_TAIL_AT, ST_TAIL_N);
    else memset(mb_base_tail, 0, ST_TAIL_N);
}
static int mb_store_sig(int set)         /* 1: store.bin is not as last seen (set: remember it as it is now) */
{
    char path[600];
    struct stat a;
    int moved;
    snprintf(path, sizeof path, "%s/store.bin", mb.dir);
    if (stat(path, &a)) return 0;
    moved = !mb_sig.have || a.st_dev != mb_sig.dev || a.st_ino != mb_sig.ino || a.st_size != mb_sig.size ||
            (long)a.st_mtim.tv_sec != mb_sig.sec || (long)a.st_mtim.tv_nsec != mb_sig.nsec;
    if (set || moved) {
        mb_sig.dev = a.st_dev; mb_sig.ino = a.st_ino; mb_sig.size = a.st_size;
        mb_sig.sec = (long)a.st_mtim.tv_sec; mb_sig.nsec = (long)a.st_mtim.tv_nsec; mb_sig.have = 1;
    }
    return moved;
}
static void mb_import_post(const uint8_t *tail)   /* the presets for the app (mb_stage) */
{
    uint32_t q = atomic_load_explicit(&mb.imp_seq, memory_order_relaxed);
    atomic_store_explicit(&mb.imp_seq, q + 1u, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    memcpy(mb.imp, tail, ST_TAIL_N);
    atomic_store_explicit(&mb.imp_seq, q + 2u, memory_order_release);
}
static void mb_store_poll(void)          /* another instance wrote store.bin: its presets */
{
    static uint8_t cur[sizeof web_store_buf];
    if (!mb_store_sig(0)) return;
    if (ms_load(mb.dir, "store.bin", cur, sizeof cur) > 0 && cur[0] == 1 && cur[1] == 2 &&
        memcmp(cur + MB_TAIL_AT, mb_base_tail, ST_TAIL_N)) {
        memcpy(mb_base_tail, cur + MB_TAIL_AT, ST_TAIL_N);
        mb_import_post(mb_base_tail);
    }
}
/* store.bin = st's settings with the presets merged (st is changed to what was written): 0 written */
static int mb_store_write(uint8_t *st, int stale)
{
    static uint8_t cur[sizeof web_store_buf];
    char lp[600];
    int lk, rc;
    snprintf(lp, sizeof lp, "%s/.store.lock", mb.dir);
    lk = open(lp, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
    if (lk >= 0) (void)flock(lk, LOCK_EX);
    /* this instance's presets win only when it changed them itself (and the record has the last import) */
    if ((stale || !memcmp(st + MB_TAIL_AT, mb_base_tail, ST_TAIL_N)) &&
        ms_load(mb.dir, "store.bin", cur, sizeof cur) > 0 && cur[0] == 1 && cur[1] == 2)
        memcpy(st + MB_TAIL_AT, cur + MB_TAIL_AT, ST_TAIL_N);
    rc = ms_save(mb.dir, "store.bin", st, sizeof cur);
    if (!rc) { memcpy(mb_base_tail, st + MB_TAIL_AT, ST_TAIL_N); (void)mb_store_sig(1); }
    if (lk >= 0) { (void)flock(lk, LOCK_UN); close(lk); }
    return rc;
}

static void mb_load_files(void)          /* before the boot: what settings_load and seq_load will read */
{
    (void)ms_mkdirs(mb.dir);
    memset(host_mem, 0xFF, sizeof host_mem);
    if (ms_load(mb.dir, "store.bin", web_store_buf, sizeof web_store_buf) > 0) mb.loaded |= 1u;
    else memset(web_store_buf, 0, sizeof web_store_buf);
    /* (no sequencer on the Move: pattern.bin is not loaded, MB_CC_DROP) */
    memcpy(mb.stg_store, web_store_buf, sizeof web_store_buf);   /* (what is on disk: nothing to write yet) */
    memcpy(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N);
    mb_store_base(web_store_buf);
    (void)mb_store_sig(1);
}
static void mb_stage(void);
static uint32_t mb_wr_seq, mb_cpu_wr;
static void mb_write_staged(void)
{
    static uint8_t st[sizeof web_store_buf], pat[MB_PAT_N], last_st[sizeof web_store_buf], last_pat[MB_PAT_N];
    static uint8_t raw[sizeof web_store_buf];
    static int have_last;
    uint32_t q1 = atomic_load_explicit(&mb.stg_seq, memory_order_acquire), q2, imp;
    if (q1 == mb_wr_seq || (q1 & 1u)) return;
    memcpy(st, mb.stg_store, sizeof st);
    memcpy(pat, mb.stg_pat, sizeof pat);
    imp = mb.stg_imp;
    atomic_thread_fence(memory_order_acquire);
    q2 = atomic_load_explicit(&mb.stg_seq, memory_order_relaxed);
    if (q1 != q2) return;                                     /* (copied over meanwhile: next time) */
    if (st[0] == 1 && (!have_last || memcmp(st, last_st, sizeof st))) {   /* (last_st: the record as staged) */
        memcpy(raw, st, sizeof st);
        if (mb_store_write(st, imp != atomic_load_explicit(&mb.imp_seq, memory_order_relaxed))) mb.save_fail++;
        else {
            mb.saves++; memcpy(last_st, raw, sizeof st);
            if (memcmp(st + MB_TAIL_AT, raw + MB_TAIL_AT, ST_TAIL_N)) mb_import_post(st + MB_TAIL_AT);
        }
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
static int mb_stats(char *buf, int n);
static void mb_write_stats(void)          /* (dev_stats_log: the clock probe reads where the clock comes from) */
{
    static char line[512];
    char path[600];
    FILE *f;
    int k;
    if (!atomic_load(&mb.stats_log)) return;
    snprintf(path, sizeof path, "%s/stats.log", mb.mod_dir);
    f = fopen(path, "a");
    if (!f) return;
    k = mb_stats(line, (int)sizeof line - 1);
    if (k > 0 && k < (int)sizeof line - 1) { line[k] = '\n'; fwrite(line, 1, (size_t)k + 1u, f); }
    fclose(f);
}
/* dev_rx_log 1: the worker appends what dev_rx and dev_fxlog gained to <instance dir>/rx.log ("rx ms st d1 d2 src",
 * "fx ms on why"), so a session with the user playing the pads keeps every message (the rings hold the last few) */
static _Atomic int mb_rx_log;
static uint32_t mb_rx_wr, mb_fx_wr;
static void mb_write_rx(void)
{
    char path[600];
    FILE *f;
    uint32_t rn = atomic_load_explicit(&mb_rx.n, memory_order_acquire), fn = atomic_load_explicit(&mb_fxlog.n, memory_order_acquire);
    if (!atomic_load(&mb_rx_log)) { mb_rx_wr = rn; mb_fx_wr = fn; return; }
    if (rn == mb_rx_wr && fn == mb_fx_wr) return;
    snprintf(path, sizeof path, "%s/rx.log", mb.mod_dir);
    f = fopen(path, "a");
    if (!f) return;
    char ln[64];
    int k;
    if (rn - mb_rx_wr > MB_RX_N) {
        k = snprintf(ln, sizeof ln, "rx-lost %u\n", rn - mb_rx_wr - MB_RX_N); fwrite(ln, 1, (size_t)k, f);
        mb_rx_wr = rn - MB_RX_N;
    }
    for (; mb_rx_wr != rn; mb_rx_wr++) {
        uint32_t i = mb_rx_wr % MB_RX_N;
        k = snprintf(ln, sizeof ln, "rx %u %02x %u %u %u\n", mb_rx.ms[i], mb_rx.st[i], mb_rx.d1[i], mb_rx.d2[i], mb_rx.src[i]);
        fwrite(ln, 1, (size_t)k, f);
    }
    if (fn - mb_fx_wr > MB_FXLOG_N) mb_fx_wr = fn - MB_FXLOG_N;
    for (; mb_fx_wr != fn; mb_fx_wr++) {
        uint32_t i = mb_fx_wr % MB_FXLOG_N;
        k = snprintf(ln, sizeof ln, "fx %u %u %s\n", mb_fxlog.ms[i], mb_fxlog.on[i], mb_fxlog.why[i]);
        fwrite(ln, 1, (size_t)k, f);
    }
    fclose(f);
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
/* ---- the dev player and tap (tests over Schwung's WebSocket, without the Move's mix): set_param writes a
 * schedule of MIDI messages that the bridge plays itself (never through the Move: no cable 2, so no Move track
 * hears it), and the frames this plugin hands out from the schedule's start are written to
 * <data dir>/tap/<id>.wav by the worker. The WebSocket's set ring takes values under 256 B, so the schedule comes
 * in pieces:
 *
 *   dev_play      "begin ID DUR_MS"   a new take (only when none is playing): ID [A-Za-z0-9_-]{1,32}, DUR_MS the tap;
 *                                     the ID of the take that went last is refused (a take plays once)
 *   dev_play_add  "[#K ]T ST D1 D2;..."   messages at T ms after the start (decimal or 0x hex; T never decreasing);
 *                                     #K: the piece's number from 0, a piece out of turn (a repeat) is refused whole
 *   dev_play      "go [LEAD_MS]"      the start B: the first ms at least LEAD_MS (100) ahead with B % 2560 == 0,
 *                                     where the 256-frame blocks fall on the host build's ms grid as at its ms 0
 *                                     (44100 x 2560 / 1000 = 441 blocks): the take is the host build's render of the
 *                                     same messages shifted by B, sample for sample
 *   dev_play      "stop"              ends the take now (the rest of the schedule dropped, the file padded)
 *   get dev_play                      "state=idle|load|armed|done id= base= frames= events= late= dropped= ..."
 *
 * The worker writes the WAV (44.1 kHz stereo 16 bit; its length fixed at DUR_MS, a frame lost to an overrun is a
 * zero and counted) and <id>.json (base ms, frame0, frames, events, late, dropped, aborted), then the state is done.
 * The audio side only copies: one test of the state per 256 frames when no take runs; no syscall, no allocation */
#define MB_PLAY_N 16384u
static int mb_put(int kind, uint8_t st, uint8_t d1, uint8_t d2, uint32_t at, uint16_t key, int32_t v);
#define MB_TAP_N (1u << 17)              /* frames (3 s): the worker drains it every 100 ms */
#define MB_GRID_MS 2560u
enum { MP_IDLE, MP_LOAD, MP_ARMED, MP_DONE };
typedef struct { uint32_t t; uint8_t st, d1, d2, pad; } mb_pev_t;
static struct {
    _Atomic int state;                   /* IDLE / DONE: set_param may begin; LOAD: adds; ARMED: the audio side plays */
    _Atomic uint32_t seq;                /* bumps at each go: the worker starts a new file */
    char id[36], went[36];               /* went: the take that last went (its id is refused at begin) */
    uint32_t dur_ms, n, r, base, f0w, nfr, late, bad, last_t, pieces;
    _Atomic uint32_t tw;                 /* frames captured (the audio side) */
    _Atomic uint32_t dropped;
    _Atomic int file_done, abort;
    int16_t ring[2u * MB_TAP_N];
    mb_pev_t ev[MB_PLAY_N];
} mp;
static uint32_t mp_wseq, mp_tr;          /* the worker's: the take it writes, frames written */
static int mp_fd = -1, mp_open;         /* mp_open: a take is being written */
static char mp_tmp[680], mp_path[680];
static void mp_put32(uint8_t *b, uint32_t v) { b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24); }
static void mp_finish(void)
{
    char js[512], jp[680], jt[700];
    int k, fd;
    uint32_t dr = atomic_load(&mp.dropped);
    if (mp_fd >= 0) { (void)fsync(mp_fd); close(mp_fd); mp_fd = -1; (void)rename(mp_tmp, mp_path); }
    k = snprintf(js, sizeof js, "{\"id\":\"%s\",\"sr\":44100,\"base_ms\":%u,\"grid_ms\":%u,\"frame0\":%u,\"frames\":%u,"
                 "\"events\":%u,\"played\":%u,\"pieces\":%u,\"late\":%u,\"bad\":%u,\"dropped\":%u,\"aborted\":%d}\n", mp.id,
                 mp.base, MB_GRID_MS, mp.f0w, mp.nfr, mp.n, mp.r, mp.pieces, mp.late, mp.bad, dr, atomic_load(&mp.abort));
    snprintf(jp, sizeof jp, "%s/tap/%s.json", mb.idir, mp.id);
    snprintf(jt, sizeof jt, "%s/tap/.%s.json.tmp", mb.idir, mp.id);
    fd = open(jt, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) { if (ms_write_all(fd, js, (size_t)k) == 0) (void)fsync(fd); close(fd); (void)rename(jt, jp); }
    mp_open = 0;
    atomic_store_explicit(&mp.file_done, 1, memory_order_release);
}
static void mb_write_tap(void)           /* the worker */
{
    static int16_t buf[2u * 4096u];
    uint32_t q = atomic_load_explicit(&mp.seq, memory_order_acquire), tw;
    if (q != mp_wseq) {                  /* a new take: its file, the header for DUR_MS */
        char dir[600];
        uint8_t h[44];
        if (mp_fd >= 0) { close(mp_fd); mp_fd = -1; }
        mp_wseq = q; mp_tr = 0; mp_open = 1;
        snprintf(dir, sizeof dir, "%s/tap", mb.idir);
        (void)ms_mkdirs(dir);
        snprintf(mp_path, sizeof mp_path, "%s/%s.wav", dir, mp.id);
        snprintf(mp_tmp, sizeof mp_tmp, "%s/.%s.wav.tmp", dir, mp.id);
        mp_fd = open(mp_tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        memcpy(h, "RIFF\0\0\0\0WAVEfmt \x10\0\0\0\x01\0\x02\0\x44\xac\0\0\x10\xb1\x02\0\x04\0\x10\0data\0\0\0\0", 44);
        mp_put32(h + 4, 36u + 4u * mp.nfr);
        mp_put32(h + 40, 4u * mp.nfr);
        if (mp_fd >= 0 && ms_write_all(mp_fd, h, sizeof h)) { close(mp_fd); mp_fd = -1; }
    }
    if (!mp_open) return;
    tw = atomic_load_explicit(&mp.tw, memory_order_acquire);
    if (tw - mp_tr > MB_TAP_N - 4096u) {  /* (overrun: what was written over is lost; zeros keep the time) */
        uint32_t lost = tw - mp_tr - (MB_TAP_N - 4096u);
        memset(buf, 0, sizeof buf);
        atomic_fetch_add(&mp.dropped, lost);
        while (lost) { uint32_t c = lost < 4096u ? lost : 4096u; if (mp_fd >= 0) (void)ms_write_all(mp_fd, buf, 4u * c); lost -= c; mp_tr += c; }
    }
    while (mp_tr != tw) {
        uint32_t c = tw - mp_tr, i;
        if (c > 4096u) c = 4096u;
        for (i = 0; i < c; i++) { uint32_t k = (mp_tr + i) & (MB_TAP_N - 1u); buf[2u * i] = mp.ring[2u * k]; buf[2u * i + 1u] = mp.ring[2u * k + 1u]; }
        if (mp_fd >= 0) (void)ms_write_all(mp_fd, buf, 4u * c);
        mp_tr += c;
    }
    if (atomic_load_explicit(&mp.abort, memory_order_acquire) && mp_tr == atomic_load(&mp.tw)) {   /* padded to DUR_MS */
        memset(buf, 0, sizeof buf);
        while (mp_tr < mp.nfr) { uint32_t c = mp.nfr - mp_tr < 4096u ? mp.nfr - mp_tr : 4096u; if (mp_fd >= 0) (void)ms_write_all(mp_fd, buf, 4u * c); mp_tr += c; }
    }
    if (mp_tr >= mp.nfr) mp_finish();
}
/* the audio side: the block just written to the FIFO at index w0 */
static void mb_tap_take(uint32_t w0)
{
    uint32_t tw = atomic_load_explicit(&mp.tw, memory_order_relaxed), i;
    if (atomic_load_explicit(&mp.abort, memory_order_relaxed)) return;
    for (i = 0; i < HALF_FRAMES && tw < mp.nfr; i++) {
        uint32_t idx = w0 + i, k;
        if ((int32_t)(idx - mp.f0w - tw) != 0) continue;     /* (before the start) */
        k = tw & (MB_TAP_N - 1u);
        mp.ring[2u * k] = mb.fifo[2u * (idx & (MB_FIFO - 1u))];
        mp.ring[2u * k + 1u] = mb.fifo[2u * (idx & (MB_FIFO - 1u)) + 1u];
        tw++;
    }
    atomic_store_explicit(&mp.tw, tw, memory_order_release);
}
static void mb_play_due(void)            /* the audio side, each ms: the schedule's messages due by now */
{
    while (mp.r < mp.n && (int32_t)(mp.base + mp.ev[mp.r].t - mb.ms) <= 0) {
        const mb_pev_t *e = &mp.ev[mp.r];
        if (mq_w - mq_r >= MB_HOST_RING) return;              /* (the app's ring is full: the rest a ms later) */
        if (mp.base + e->t != mb.ms) mp.late++;
        web_midi(e->st, e->d1, e->d2);
        mp.r++;
    }
    if (mp.r == mp.n && (atomic_load_explicit(&mp.tw, memory_order_relaxed) >= mp.nfr || atomic_load(&mp.abort)) &&
        atomic_load_explicit(&mp.file_done, memory_order_acquire))
        atomic_store_explicit(&mp.state, MP_DONE, memory_order_release);
}
static int mp_num(const char **p, uint32_t *out)   /* a decimal or 0x hex number; spaces / commas before it */
{
    const char *s = *p;
    uint32_t v = 0; int any = 0;
    while (*s == ' ' || *s == ',' || *s == '\t') s++;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        for (;; s++) {
            int d = *s >= '0' && *s <= '9' ? *s - '0' : *s >= 'a' && *s <= 'f' ? *s - 'a' + 10 : *s >= 'A' && *s <= 'F' ? *s - 'A' + 10 : -1;
            if (d < 0) break;
            if (v > 0x0FFFFFFFu) return -1;
            v = v * 16u + (uint32_t)d; any = 1;
        }
    } else for (; *s >= '0' && *s <= '9'; s++) { if (v > 429496728u) return -1; v = v * 10u + (uint32_t)(*s - '0'); any = 1; }
    if (!any) return -1;
    *p = s; *out = v;
    return 0;
}
static void mp_set(const char *key, const char *val)
{
    int st = atomic_load_explicit(&mp.state, memory_order_acquire);
    const char *p = val;
    if (!strcmp(key, "dev_play_add")) {  /* "T ST D1 D2;T ST D1 D2;..." */
        if (st != MP_LOAD) { mp.bad++; return; }
        while (*p == ' ') p++;
        if (*p == '#') {                 /* the piece's number: out of turn (a repeat, a gap) refused whole */
            uint32_t k;
            p++;
            if (mp_num(&p, &k) || k != mp.pieces) { mp.bad++; return; }
        }
        mp.pieces++;
        while (*p) {
            uint32_t t, a, b, c;
            while (*p == ';' || *p == ' ' || *p == '\n') p++;
            if (!*p) break;
            if (mp_num(&p, &t) || mp_num(&p, &a) || mp_num(&p, &b) || mp_num(&p, &c) || a < 0x80u || a > 0xFFu ||
                a == 0xF0u || a == 0xF7u || t < mp.last_t || mp.n >= MB_PLAY_N) {
                mp.bad++;
                while (*p && *p != ';') p++;
                continue;
            }
            mp.ev[mp.n].t = t; mp.ev[mp.n].st = (uint8_t)a; mp.ev[mp.n].d1 = (uint8_t)(b & 127u); mp.ev[mp.n].d2 = (uint8_t)(c & 127u);
            mp.n++; mp.last_t = t;
        }
        return;
    }
    if (!strncmp(val, "begin ", 6)) {
        uint32_t i = 0, dur;
        if (st == MP_LOAD || st == MP_ARMED) { mp.bad++; return; }
        p = val + 6;
        while (*p == ' ') p++;
        while (i < 32u && ((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) mp.id[i++] = *p++;
        mp.id[i] = 0;
        if (!i || (*p != ' ' && *p != ',') || mp_num(&p, &dur) || !dur || dur > 3600000u || !strcmp(mp.id, mp.went)) {
            mp.bad++; mp.id[0] = 0; return;
        }
        mp.dur_ms = dur; mp.n = mp.r = mp.late = mp.bad = mp.last_t = mp.pieces = 0;
        atomic_store_explicit(&mp.state, MP_LOAD, memory_order_release);
        return;
    }
    if (!strncmp(val, "go", 2) && (val[2] == 0 || val[2] == ' ')) {
        uint32_t lead = 100u;
        if (st != MP_LOAD) { mp.bad++; return; }
        p = val + 2;
        if (*p) (void)mp_num(&p, &lead);
        if (lead > 60000u) lead = 60000u;
        mp.base = ((mb.ms + lead) / MB_GRID_MS + 1u) * MB_GRID_MS;
        mp.f0w = (uint32_t)((uint64_t)mp.base * 441u / 10u);  /* (the FIFO's frame index at the start: a block edge) */
        mp.nfr = (uint32_t)((uint64_t)mp.dur_ms * 441u / 10u);
        memcpy(mp.went, mp.id, sizeof mp.went);
        atomic_store(&mp.tw, 0); atomic_store(&mp.dropped, 0); atomic_store(&mp.file_done, 0); atomic_store(&mp.abort, 0);
        atomic_fetch_add_explicit(&mp.seq, 1u, memory_order_release);
        atomic_store_explicit(&mp.state, MP_ARMED, memory_order_release);
        return;
    }
    if (!strcmp(val, "stop")) {
        if (st == MP_ARMED) { mp.r = mp.n; atomic_store_explicit(&mp.abort, 1, memory_order_release); (void)mb_put(MB_EV_MIDI, 0xB0u, 123u, 0u, 0u, 0u, 0); }
        else if (st == MP_LOAD) atomic_store(&mp.state, MP_IDLE);
        return;
    }
    mp.bad++;
}
static int mp_get(char *buf, int n)
{
    static const char *const NM[] = { "idle", "load", "armed", "done" };
    int st = atomic_load_explicit(&mp.state, memory_order_acquire);
    return snprintf(buf, (size_t)n, "state=%s id=%s base=%u ms=%u frames=%u/%u events=%u/%u late=%u bad=%u dropped=%u "
                    "pieces=%u", NM[st & 3], mp.id, mp.base, mb.ms, atomic_load(&mp.tw), mp.nfr, mp.r, mp.n, mp.late,
                    mp.bad, atomic_load(&mp.dropped), mp.pieces);
}

/* which build runs (the device tools): the features of this bridge, the app's version, and the CRC-32 of the
 * dsp.so file in the module folder as the worker found it when this instance started (zlib.crc32 of the local
 * build compares: the file this code was loaded from, dladdr; under the loader this instance's copy of engine.so); written to this instance's
 * folder (<data dir>/inst/<k>/boot.txt under the loader) and answered by get dev_build */
#define MB_FEATURES "bridge=2 dev_play=1"
static char mb_build[160];
static uint32_t mb_starts;
static void mb_note_build(void)
{
    static uint8_t b[65536];
    char path[600], line[200];
    uint32_t c = 0xFFFFFFFFu, total = 0;
    int fd, k;
    ssize_t r;
    {   /* this file itself (dladdr): a copy the loader made, or the plugin loaded on its own */
        Dl_info di;
        if (dladdr((void *)mb_note_build, &di) && di.dli_fname && di.dli_fname[0])
            snprintf(path, sizeof path, "%s", di.dli_fname);
        else
            snprintf(path, sizeof path, "%s/dsp.so", mb.mod_dir);
    }
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd >= 0) {
        while ((r = read(fd, b, sizeof b)) > 0) {
            for (ssize_t i = 0; i < r; i++) { c ^= b[i]; for (int j = 0; j < 8; j++) c = c >> 1 ^ (0xEDB88320u & (0u - (c & 1u))); }
            total += (uint32_t)r;
        }
        close(fd);
    }
    mb_starts++;
    {   /* at: CLOCK_MONOTONIC ms of this start (a copy the loader loads again counts starts from 1: at tells) */
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        snprintf(mb_build, sizeof mb_build, "%s ver=%s dsp_crc32=%08x dsp_bytes=%u starts=%u at=%lld", MB_FEATURES,
                 APP_VER, fd >= 0 ? ~c : 0u, total, mb_starts, (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000);
    }
    k = snprintf(line, sizeof line, "%s\n", mb_build);
    snprintf(path, sizeof path, "%s/boot.txt", mb.idir);
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) { (void)ms_write_all(fd, line, (size_t)k); close(fd); }
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
        arp_defaults();                                       /* (the slot's own: OFF until its state or a key) */
    }
    mb_cpu_wr = atomic_load(&mb.cpu_n);
    mb_note_build();
    atomic_store_explicit(&mb.ready, 1, memory_order_release);
    atomic_store(&mb.booting, 0);
    while (!atomic_load(&mb.wk_stop)) {
        struct timespec t = { 0, 100 * 1000000L };
        mb_write_staged();
        mb_store_poll();
        mb_write_cpu();
        mb_write_stats();
        mb_write_rx();
        mb_write_tap();
        nanosleep(&t, 0);
    }
    mb_write_staged();                                        /* (a last staged save before the module goes) */
    mb_write_cpu();
    mb_write_tap();
    return 0;
}
static int mb_start(const char *module_dir)  /* create_instance */
{
#ifdef FR_HQ
    /* the Move ships one engine: the HQ Rhodes, on at every load (the user, 2026-10-28; the FM-1 plays Classic).
     * Not saved, not on a page: a preset or a state load leaves it on. p_hq 0 (a dev / test key only) plays Classic
     * until the next load, for Gate M (Classic = the host renders) and d2 --hq 0 */
    hq_mode = 1;
#endif
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
        v = k->cc == 64u ? (sus_latched || sus_held) : k->cc == 60u ? seq_play != 0 : k->cc == 63u ? seq_rec != 0 :
            k->cc == 111u ? fx_bypass == 0 : 0;   /* 111: FX (s_fx, M5-01) OFF / ON: 1 = the effects on, not bypassed */
        break;
    case MK_BTN: v = k->cc == 111u ? fx_bypass == 0 : sus_latched || sus_held; break;   /* a button's key reads what it
                                                       * flips: FX (the Left arrow) or SUS (the Right arrow) */
    case MK_MAP: v = k->slot < MB_NMAP ? mb.map[k->slot] : 0; break;
    case MK_HOLD: v = ARP_HOLD != 0; break;
    default: v = 0; break;
    }
    if (k->scale > 1u) {
        int32_t a = v < 0 ? -v : v, sc = k->scale;
        int dec = sc >= 100 ? 2 : 1;
        return snprintf(buf, (size_t)n, "%s%d.%0*d", v < 0 ? "-" : "", a / sc, dec, (a % sc) * (dec == 2 ? 100 : 10) / sc);
    }
    return snprintf(buf, (size_t)n, "%d", v);
}
/* SUS OFF: the latch, a hold of the FM-1's REC code and a MIDI pedal held down all let go (the pedal's next CC 64
 * counts again); the caller sends the pedal (sustain_update) */
static void mb_sus_off(void) { sus_latched = 0; sus_held = 0; sus_midi = 0; }
static void mb_key_apply(uint32_t i, int32_t x)
{
    const move_key_t *k = &MOVE_KEYS[i];
    if ((k->kind == MK_LATCH || k->kind == MK_BTN) && x < 0) x = 0;   /* a switch under its OFF is OFF (the sweep:
                                                                         * s_fx / s_sustain -1 read ON) */
    if (k->kind == MK_PARAM || k->kind == MK_SLOT) mb_slot_abs(k->slot, x);
    else if (k->kind == MK_HOLD) {            /* the arpeggiator's HOLD (2026-11-01): the FM-1's SEL on ARPEGGIO, set to a
                                                 * state (OFF lets the notes no longer held go: arp_hold_toggle) */
        if ((x > 0) != (ARP_HOLD != 0)) arp_hold_toggle();
        ui_mark(E_CARDS); ui_mark(E_FOOTER);
    } else if (k->cc == 64u) {                   /* SUS, the sustain toggle (the user, 2026-10-28): ON holds the notes as
                                                 * the FM-1's REC latch does; OFF lets everything go and always wins
                                                 * (0.9.8 candidate: OFF went back to ON while the PEDAL cell held the
                                                 * REC button). A MIDI pedal let up still frees the latch (midi_cc). */
        if (x) sus_latched = 1; else mb_sus_off();
        sustain_update();
    } else if (k->kind == MK_BTN && k->cc == 111u) {   /* the Left arrow (screen.js): a press flips FX */
        if (x) midi_cc(111u, fx_bypass ? 0u : 127u);
    } else if (k->kind == MK_BTN) {           /* the Right arrow (screen.js) or the chosen touch (dev_sus_touch): a
                                                 * press flips SUS, a release does nothing */
        if (x) { mb.sus_down++; if (sus_latched || sus_held) mb_sus_off(); else sus_latched = 1; sustain_update(); }
        else mb.sus_up++;
    } else if (k->cc == 111u) midi_cc(111u, x ? 0u : 127u);   /* FX: 1 = the effects on (CC 111 is the bypass) */
    else if (k->kind == MK_LATCH) midi_cc(k->cc, x ? 127u : 0u);
    else if (k->kind == MK_TRIG) { if (x) midi_cc(k->cc, 127u); }
    else if (k->kind == MK_MAP && k->slot < MB_NMAP) {   /* a mapping's setting: the CC 0..119, LO / HI 0..127 */
        int32_t hi = k->slot % 3u ? 127 : 119;
        mb.map[k->slot] = (uint8_t)(x < 0 ? 0 : x > hi ? hi : x);
        mb.map_last[k->slot / 3u] = -1;           /* (the next pressure is sent whatever it is) */
    }
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
#define MB_STATE_N (40 + 2 * 256 + 12 + 2 * P_SLOTS + 24 + 24 + 10 + 2 * MB_NMAP)
static int mb_state_get(char *buf, int n)
{
    char *o = buf;
    if (n < MB_STATE_N + 1) return -1;
    o += sprintf(o, "{\"v\":\"1\",\"set\":\"");
    o += mb_hex_put(o, &set, sizeof set);
    o += sprintf(o, "\",\"base\":\"");
    o += mb_hex_put(o, snd_base, P_SLOTS);
    o += sprintf(o, "\",\"map\":\"");
    o += mb_hex_put(o, mb.map, MB_NMAP);
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
    {   /* the mappings (M6-03): a state without them (before M6-03) loads them off */
        uint32_t lm = 0;
        const char *mp = mb_json_str(j, "map", &lm);
        mb.st_map_have = mp && lm == 2u * MB_NMAP && !mb_hex_get(mp, mb.st_map, MB_NMAP);
    }
    if (h && lh > 0 && lh < 6) { uint32_t x = 0; for (uint32_t i = 0; i < lh; i++) x = x * 10u + (uint32_t)(h[i] - '0'); mb.st_hand = (uint16_t)x; }
    atomic_store(&mb.st_flag, 2);
    return 0;
}
static void mb_state_apply(void)         /* the boot's sanitize and apply steps (frodhes.c app_main), at run time */
{
    int expect = 2, held;
    if (!atomic_compare_exchange_strong(&mb.st_flag, &expect, 3)) return;
    held = ARP_HOLD != 0;
    set = mb.st_set;
    if (set.ver != 1u && set.ver != SET_VER) params_defaults();   /* (as the boot: a record it cannot read) */
    params_sanitize();
    kv_apply();
    arp_sanitize();
    if (held && !ARP_HOLD) { ARP_HOLD = 1; arp_hold_toggle(); }   /* a state with HOLD off: the latched notes go, as
                                                                   * the card's OFF lets them go */
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
    {
        static const uint8_t def[MB_NMAP] = MB_MAP_DEFAULT;
        memcpy(mb.map, mb.st_map_have ? mb.st_map : def, MB_NMAP);
        for (uint32_t i = 0; i < MB_NMAP; i += 3u) if (mb.map[i] > 119u) mb.map[i] = 0;
        mb.map_last[0] = mb.map_last[1] = -1;
    }
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
    const host_api_v1_t *h = atomic_load_explicit(&g_host, memory_order_acquire);
    double b = -1.0, ms_per = (double)frames * 1000.0 / 44100.0;
    int running;
    uint32_t n = 0;
    if (h && h->get_clock_status) mb.h_run = h->get_clock_status() == MOVE_CLOCK_STATUS_RUNNING;
    if (h && h->get_bpm) mb.h_bpm10 = (int)(h->get_bpm() * 10.0f + 0.5f);
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

/* ---- the Move-only mappings (M6-03, U9): channel pressure (source 0) or the most pressed note's poly pressure
 * (source 1) -> its CC on the message's channel, LO + (HI - LO) x pressure / 127, sent only when it changes, after
 * the pressure itself (which the app passes over). A CC is a CC: the app takes it as from a controller (its CHANNEL
 * filter, the settings saved when silent). Off (CC 0) by default: nothing is sent (Gate M) */
static void mb_map_out(uint32_t src, uint8_t ch, int32_t p)
{
    uint8_t cc = mb.map[3u * src];
    int32_t lo = mb.map[3u * src + 1u], hi = mb.map[3u * src + 2u], v;
    if (!cc || MB_CC_DROP(cc)) return;
    v = lo + (hi - lo) * p / 127;
    if (v == mb.map_last[src]) return;
    if (mq_w - mq_r >= MB_HOST_RING) { mb.map_drop++; return; }   /* (the app's ring is full: the next one) */
    mb.map_last[src] = (int16_t)v;
    web_midi((uint8_t)(0xB0u | ch), cc, (uint8_t)v);
    mb.map_out++;
}
static void mb_map(uint8_t st, uint8_t d1, uint8_t d2)
{
    uint8_t t = st & 0xF0u, ch = st & 15u, m = 0;
    if (t == 0xD0u) { mb_map_out(0, ch, d1); return; }
    if (t == 0x90u && d2) return;                    /* (a note-on: its pressure comes later) */
    if (t == 0xA0u) mb.pa[d1] = d2;
    else if (mb.pa[d1]) mb.pa[d1] = 0;                /* the note let go: its pressure ends */
    else return;
    for (uint32_t i = 0; i < 128u; i++) if (mb.pa[i] > m) m = mb.pa[i];
    mb_map_out(1, ch, m);
}

/* ---- one host millisecond (see the top) */
static void mb_gr_tick(void);
/* the sequencer kept off (see MB_CC_DROP), after each main-loop pass: a Start / Continue from the Move's transport
 * starts the FM-1 sequencer (frodhes.c), which plays nothing (no pattern) and is stopped here (the arpeggiator is
 * left alone since 2026-10-30: it plays on the Move). The same for what
 * else the Move owns (the user, 2026-10-28 12:10): the chords (CC 56-59 dropped), the scales and the transpose (off
 * and 0), and the tempo: the clock source stays EXT (the Move's transport reaches every slot as F8 / FA / FB / FC,
 * or mb_clock_block makes it from the host's transport), and with no clock leading, the tempo is the Move's
 * (host get_bpm, 20-400 BPM, else 120; the FM-1's tempo_set10 keeps it in 40-240) */
#define MB_BPM_FALLBACK10 1200u
static uint32_t mb_host_bpm10(void)
{
    const host_api_v1_t *h = atomic_load_explicit(&g_host, memory_order_acquire);
    double b = h && h->get_bpm ? h->get_bpm() : 0.0;
    return b >= 20.0 && b <= 400.0 ? (uint32_t)(b * 10.0 + 0.5) : MB_BPM_FALLBACK10;
}
static void mb_inert(void)
{
    uint32_t b10 = mb_host_bpm10(), t10 = b10 < 400u ? 400u : b10 > 2400u ? 2400u : b10;
    if (!clk_follows() && (clk_owns || set.bpm10 != t10)) { tempo_set10(b10); bpm_hand = set.bpm10; mb.bpm_host++; }
    if (CH_TYPE || set.sc_mode || set.sc_trans || set.clock != 1u) {
        mb.inert_n++;
        if (CH_TYPE) CH_TYPE = 0;
        set.sc_mode = 0; set.sc_trans = 0; set.clock = 1;
        set_dirty = 1;
    }
    if (!(seq_play | seq_rec | sq_erase)) return;
    mb.inert_n++;
    if (seq_rec) seq_toggle_rec();
    if (seq_play) seq_stop();
    sq_erase = 0;
}
/* u_save (Frodhes' own screen, SAVE TO USER): the sound as it is into user slot u (0..15), as the FM-1's SAVE does
 * (presets.c presets_poll: the parameters, the autoname, the slot loaded), then saved when quiet (presets_save_poll in
 * the app's loop; on the Move into store.bin, merged with what other instances wrote) */
static void mb_user_save(uint32_t u)
{
    preset_t *pu;
    uint32_t i;
    if (u >= PR_NU) return;
    pu = &pr_user[u];
    pu->ver = 3; pu->np = P_COUNT; pu->used = 1; pu->rsv = 0;
    preset_autoname(pu->name);
    for (i = 0; i < P_SLOTS; i++) pu->p[i] = i < P_COUNT ? PV(i) : 0;
    for (i = 0; i < sizeof pu->rsv2; i++) pu->rsv2[i] = 0;
    set.preset = (uint8_t)(PR_NF + u);
    snd_base_take();
    set_dirty = 1; set_t_change = fm1_ms;
    pr_save_mode = 0;
    pr_save_pending = 1;
    mb.usaves++;
}

/* ---- the Move-only factory presets (move/move_presets.h, tools/gen_move_presets.py): F20-F57, the factory preset
 * review's B and C variants of F01-F19 (the user, 2026-11-01); the FM-1 keeps presets.c as it is (Gate S). Their
 * index (set.preset, the "preset" key, a program change) comes after presets.c's: 0-18 F01-F19 and 19-34 U01-U16 as
 * on the FM-1 (store.bin and the states keep their meaning), then 35-72 F20-F57. Program change 35-72 loads them
 * (presets.c preset_of_pc stops at 34); Frodhes' own screen browses F01-F57, then U01-U16 (u_first) */
#define MB_PX_BASE (PR_NF + PR_NU)
#define MB_PR_N (MB_PX_BASE + MB_PX_N)
_Static_assert(MB_PR_N <= 255u && MB_PX_FIRST == PR_NF + 1u, "the presets' index fits set.preset; F20 follows F19");
_Static_assert(MB_PX_BASE == 16u + PR_NF, "program change 16 + PR_NF.. is the Move-only presets (preset_of_pc: 255)");
static int mb_pr_values(uint32_t idx, uint8_t *p)    /* preset_values, with the Move-only presets: -1 none there */
{
    const uint8_t *d;
    uint32_t i;
    if (idx < MB_PX_BASE) return preset_values(idx, p);
    if (idx >= MB_PR_N) return -1;
    for (i = 0; i < P_COUNT; i++) p[i] = PARAMS[i].def;
    d = MB_PX_DATA[idx - MB_PX_BASE];
    for (i = 0; i + 1u < MB_PX_REC && d[i] != 0xFFu; i += 2u) if (d[i] < P_COUNT && d[i + 1u] <= PARAMS[d[i]].hi) p[d[i]] = d[i + 1u];
    return 0;
}
static void mb_pr_name(uint32_t idx, char *s)          /* preset_name: "F20 STAGE77 ECHO" */
{
    if (idx < MB_PX_BASE) { preset_name(idx, s); return; }
    if (idx >= MB_PR_N) { snprintf(s, 20, "FRODHES"); return; }
    snprintf(s, 20, "F%02u %s", (unsigned)(MB_PX_FIRST + idx - MB_PX_BASE), MB_PX_NAME[idx - MB_PX_BASE]);
}
static void mb_pr_load(uint32_t idx)                   /* presets.c preset_load, for every index */
{
    uint8_t p[P_COUNT];
    uint32_t i;
    if (idx < MB_PX_BASE) { preset_load(idx); return; }
    if (mb_pr_values(idx, p)) return;
    for (i = 0; i < P_COUNT; i++) UV(i) = PV(i);       /* (as preset_load: the sound before it is the undo copy) */
    set.undo_valid = 1;
    set.undo_preset = (uint8_t)(set.preset < PR_NF + PR_NU ? set.preset + 1u : 0u);
    for (i = 0; i < P_COUNT; i++) param_set(i, p[i]);
    rev_relatch();
    set.preset = (uint8_t)idx;
    snd_base_take();
    tempo_sync_publish();
    set_dirty = 1; set_t_change = fm1_ms;
    ui_draw_page_change();
}

static void mb_step(void)
{
    mb_ev_t *e;
    int play = atomic_load_explicit(&mp.state, memory_order_acquire) == MP_ARMED;
    mb.ms++;
    mb.steps++;
    if (play) mb_play_due();
    while ((e = mb_peek()) != 0 && (e->at == 0 || (int32_t)(e->at - mb.ms) <= 0)) {
        if (e->kind == MB_EV_MIDI) {
            if (mq_w - mq_r >= MB_HOST_RING) break;          /* the app's ring is full: the rest wait a millisecond */
            if ((e->st & 0xF0u) == 0xB0u && MB_CC_DROP(e->d1)) { mb.cc_drop++; mb_pop(e); continue; }
            if ((e->st & 0xF0u) == 0xC0u && e->d1 >= MB_PX_BASE && e->d1 < MB_PR_N &&
                (!set.midi_ch || (e->st & 0x0Fu) == set.midi_ch - 1u)) {   /* a program change to F20-F57 (the app's
                                                     * CHANNEL filter as frodhes.c's), after what the app's ring holds */
                if (mq_w != mq_r) break;
                mb_pr_load(e->d1); mb.rev++; mb.midi_out++;
                mb_fx_note("preset");
                mb_pop(e);
                continue;
            }
            web_midi(e->st, e->d1, e->d2);
            if ((e->st & 0xF0u) == 0xB0u && e->d1 == 111u) mb_fxlog_cc = 1;   /* (dev_fxlog: the app takes it next) */
            mb.midi_out++;
            if ((e->st & 0xF0u) == 0xD0u || (e->st & 0xF0u) == 0xA0u || (e->st & 0xE0u) == 0x80u) mb_map(e->st, e->d1, e->d2);
        } else if (e->kind == MB_EV_KEY && e->key < MOVE_NKEYS) mb_key_apply(e->key, e->v);
        else if (e->kind == MB_EV_PRESET) { uint8_t p[P_COUNT]; if (mb_pr_values((uint32_t)e->v, p) == 0) { mb_pr_load((uint32_t)e->v); mb.rev++; } }
        else if (e->kind == MB_EV_USAVE) { mb_user_save((uint32_t)e->v); mb.rev++; }
#ifdef FR_HQ
        else if (e->kind == MB_EV_HQ) hq_mode = (uint8_t)(e->v != 0);   /* (new notes; a sounding note keeps its path) */
#endif
        mb_fx_note(e->kind == MB_EV_KEY && e->key < MOVE_NKEYS ? MOVE_KEYS[e->key].key : e->kind == MB_EV_PRESET ? "preset"
                   : e->kind == MB_EV_MIDI ? "midi" : "other");
        mb_pop(e);
    }
    mb_state_apply();
    mb_fx_note("state");
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
        if (play) mb_tap_take(mb.fw);
        mb.fw += HALF_FRAMES;
    }
    mb_gr_tick();
    coro_resume(&mb.co);
    mb_fx_note(mb_fxlog_cc ? "midi cc111" : "app");   /* (the app's loop: the MIDI it took this pass) */
    mb_fxlog_cc = 0;
    mb_inert();
}

/* ---- the gain-reduction meter (r_cmp_gr): ui.c meter_poll's ballistics on the bridge's own millisecond clock, every
 * page (the FM-1 runs it on its COMP page only), from fx.c's fx_cmp_gr; arithmetic only (Gate P) */
static void mb_gr_tick(void)
{
    int32_t g;
    uint32_t segs = 0, on = PV(P_CMP_SW) != 0;
    if (mb.ms - mb.gr_t < 33u) return;
    mb.gr_t = mb.ms;
    g = on ? fx_cmp_gr * 301 / 20480 : 0;                        /* log2 Q12 -> dB x 10 (as meter_poll); FX off keeps
                                                                  * the compressor on the Move (FR_BYP_KEEP_CMP) */
    if (g > 300) g = 300;
    mb.gr_db10 = g > mb.gr_db10 ? g : mb.gr_db10 > g + 5 ? mb.gr_db10 - 5 : g;
    while (segs < GR_SEGS && mb.gr_db10 >= GR_DB10[segs]) segs++;
    if (segs >= mb.gr_peak || mb.ms - mb.gr_peak_t > 1000u) { mb.gr_peak = (uint8_t)segs; mb.gr_peak_t = mb.ms; }
    atomic_store_explicit(&mb.gr_word, (uint32_t)mb.gr_db10 | (uint32_t)(mb.gr_peak ? GR_DB10[mb.gr_peak - 1u] : 0) << 10
                          | on << 20, memory_order_relaxed);
}

/* ---- persistence (M1-08), the audio side: once a second, between two main-loop passes, when the app saved (its
 * store record dirty, or the pattern's sectors changed) and the sound is quiet (the app saves only then anyway),
 * copy both into the staging buffers: ~6 KB of memcpy, no syscall */
static void mb_stage(void)
{
    uint32_t q;
    static uint8_t tail[ST_TAIL_N];
    uint32_t iq = atomic_load_explicit(&mb.imp_seq, memory_order_acquire);
    int dirty = web_store_dirty != 0, pat = memcmp(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N) != 0;
    int imp = !(iq & 1u) && iq != mb.imp_done;
    if (!(dirty || pat || imp || !mb.stg_have) || !eng_quiet() || !fx_quiet()) return;
    if (imp) {                           /* another instance's presets (the worker read them): ~1.5 KB of memcpy */
        memcpy(tail, mb.imp, sizeof tail);
        atomic_thread_fence(memory_order_acquire);
        if (atomic_load_explicit(&mb.imp_seq, memory_order_relaxed) == iq) {
            memcpy(st_tail, tail, ST_TAIL_N);
            memcpy(web_store_buf + MB_TAIL_AT, tail, ST_TAIL_N);
            mb.imp_done = iq;
        }
    }
    q = atomic_load_explicit(&mb.stg_seq, memory_order_relaxed);
    atomic_store_explicit(&mb.stg_seq, q + 1u, memory_order_relaxed);   /* odd: copying */
    atomic_thread_fence(memory_order_release);
    memcpy(mb.stg_store, web_store_buf, sizeof web_store_buf);
    memcpy(mb.stg_pat, host_mem + MB_PAT_LO, MB_PAT_N);
    mb.stg_imp = mb.imp_done;
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
/* the Move's output trim (Move only): fx.c's look-ahead limiter keeps every sample at or under LIM_T 24000
 * (-2.7 dBFS), so +2.2 dB (Q12 5279) puts that ceiling at 30934 (-0.5 dBFS): louder, a plain linear gain, never a
 * clip. It acts on what Schwung gets, after the FIFO: the tap (dev_play) stays the app's own output. dev_trim 0 (a
 * dev / test key, no page) plays at unity, so the byte-identity tests (Gate M, test_bridge eq) see the app's samples */
#define MB_TRIM_Q12 5279
static uint16_t mb_trim = MB_TRIM_Q12;
static inline int16_t mb_trimmed(int16_t x)
{
    int32_t y = ((int32_t)x * mb_trim + 2048) >> 12;   /* rounded (0.9.8, the tail report): truncated, -1 became -2
                                                         * and 1 stayed 1, an asymmetry in the last bits of a tail */
    return (int16_t)(y > 32767 ? 32767 : y < -32767 ? -32767 : y);
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
        out[2u * i] = mb_trimmed(mb.fifo[2u * r]); out[2u * i + 1u] = mb_trimmed(mb.fifo[2u * r + 1u]);
    }
    for (; i < (uint32_t)frames; i++) out[2u * i] = out[2u * i + 1u] = 0;
    mb.fr += n;
    mb.blocks++;
    if (++mb.stg_blk >= MB_STAGE_BLOCKS) { mb.stg_blk = 0; mb_stage(); }
}

/* ---- MIDI in (M1-04): everything a sound generator gets goes to the app as it came (notes, release velocity, CC,
 * pressure, bend, program change, clock), except the knob / jog touch edges (source 5, notes 0-9: never played) and
 * the audio-FX-only broadcast. The app's CHANNEL setting filters, as on the FM-1. SysEx is dropped */
/* The Move's Full Velocity echo (2026-10-30, the Move's Settings.json "isFullVelocityOn": true): a note from the
 * Standalone port or a USB-A keyboard reaches the slot twice, as played and again as the Move's track echoes it at
 * 127; Schwung's echo filter (shadow_midi.c shadow_external_dispatch_was_recent) compares the velocity too, so with
 * Full Velocity on the echo passes. The app would strike the held note again at 127 and the played velocity is lost.
 * A note-on at 127 for a note whose note-on (not 127) came in this block or the one before, with no note-off between,
 * is that echo and is dropped (dev_stats echo_drop). The pads themselves are only the echo: with Full Velocity on
 * they arrive at 127, which is the Move's setting, not the plugin's. */
static uint32_t mb_on_blk[16][128];      /* 1 + the block of a held note's note-on (not 127), 0 none */
static uint32_t mb_echo_drops;
static int mb_echo_drop(uint8_t st, uint8_t n, uint8_t v)
{
    uint32_t *b = &mb_on_blk[st & 15u][n];
    if ((st & 0xF0u) == 0x80u || v == 0) { *b = 0; return 0; }
    if (v == 127u && *b && mb.blocks + 1u - *b <= 1u) { *b = 0; mb_echo_drops++; return 1; }
    *b = v == 127u ? 0 : mb.blocks + 1u;
    return 0;
}
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
    if (st == 0xF8u) { mb.real_f8 = 1; mb.real_f8_blk = mb.blocks; mb.rt_f8++; }
    else if (st == 0xFAu) mb.rt_fa++;
    else if (st == 0xFBu) mb.rt_fb++;
    else if (st == 0xFCu) mb.rt_fc++;
    if (st != 0xF8u && st != 0xFEu) {        /* (dev_rx) */
        uint32_t i = atomic_load_explicit(&mb_rx.n, memory_order_relaxed) % MB_RX_N;
        mb_rx.ms[i] = mb.ms; mb_rx.st[i] = st; mb_rx.d1[i] = len > 1 ? m[1] : 0; mb_rx.d2[i] = len > 2 ? m[2] : 0;
        mb_rx.src[i] = (uint8_t)source;
        atomic_fetch_add_explicit(&mb_rx.n, 1u, memory_order_release);
    }
    if (mb.clock_mode == MB_CLOCK_HOST && (st == 0xF8u || st == 0xFAu || st == 0xFBu || st == 0xFCu)) return;
    if ((st & 0xE0u) == 0x80u && len > 2 && mb_echo_drop(st, m[1] & 127u, m[2] & 127u)) return;
    mb.midi_in++;
    (void)mb_put(MB_EV_MIDI, st, len > 1 ? m[1] & 127u : 0u, len > 2 ? m[2] & 127u : 0u, 0, 0, 0);
}

/* ---- get_param / set_param */
static int mb_stats(char *buf, int n)
{
    return snprintf(buf, (size_t)n, "ms=%u steps=%u blocks=%u midi_in=%u midi_out=%u q_full=%u f8_synth=%u keys=%u keys_bad=%u "
                    "state_ok=%u state_bad=%u clock_mode=%d rt_f8=%u rt_fa=%u rt_fb=%u rt_fc=%u host_run=%d host_bpm10=%d "
                    "bpm10=%u clklock=%u echo_drop=%u", mb.ms, mb.steps, mb.blocks, mb.midi_in, mb.midi_out, mb.q_full, mb.f8_synth,
                    mb.keys_set, mb.keys_bad, mb.state_ok, mb.state_bad, mb.clock_mode, mb.rt_f8, mb.rt_fa, mb.rt_fb,
                    mb.rt_fc, mb.h_run, mb.h_bpm10, tempo_bpm10(), (unsigned)clk_follows(), mb_echo_drops);
}
/* the engine that sounds: TYPE_UI[TYPE] (eng_pages.h), 0 before the app runs (the pages of the default TYPE) */
static unsigned mb_engine(void)
{
    unsigned e = atomic_load_explicit(&mb.ready, memory_order_acquire) && PV(P_TYPE) < N_TYPE_N ? TYPE_UI[PV(P_TYPE)] : 0u;
    return e < MOVE_N_ENG ? e : 0u;
}
/* is_loading: 1 for MB_ENG_MOVED_MS after a read first sees the engine changed, else 0. Schwung re-reads ui_hierarchy
 * on the 1 -> 0 edge (its knob grid polls about every 190 ms, the list editor every frame), and only then re-plans
 * the VOICE pages for the new TYPE (a knob turn, a preset, a program change). Long enough for every poller to see a
 * 1, short enough not to hold anything. */
#define MB_ENG_MOVED_MS 600u
static int mb_engine_moved(void)
{
    unsigned e = mb_engine();
    uint64_t t = mb_now_ns() / 1000000u;
    if (!mb.eng_init) { mb.eng_init = 1; mb.eng_seen = e; }   /* the first look is not a change */
    if (e != mb.eng_seen) { mb.eng_seen = e; mb.eng_moved_ms = t; mb.eng_moves++; }
    return mb.eng_moves && t - mb.eng_moved_ms < MB_ENG_MOVED_MS;
}
/* Frodhes' own screen as the whole Move UI (MB_OWN_UI 1): the plugin answers no ui_hierarchy, so Schwung's component
 * editor loads the module's ui_chain.js (shadow_ui.js enterComponentEdit -> enterComponentEditFallback) in place of its
 * knob grid; the same pages go out as ui_pages, which external surfaces (E16, EC4) read when ui_hierarchy fails.
 * MB_OWN_UI 0: Schwung's knob grid (ui_hierarchy), as before. */
#ifndef MB_OWN_UI
#define MB_OWN_UI 1
#endif
#if MB_OWN_UI
#define MB_PAGES_KEY "ui_pages"
#else
#define MB_PAGES_KEY "ui_hierarchy"
#endif
static int mb_get(const char *key, char *buf, int n)
{
    int i;
    if (!key || !buf || n <= 0) return -1;
    if (!strcmp(key, "dev_ready")) return snprintf(buf, (size_t)n, "%d", atomic_load(&mb.ready));
    if (!strcmp(key, "dev_build")) return snprintf(buf, (size_t)n, "%s", mb_build[0] ? mb_build : MB_FEATURES);
    if (!atomic_load_explicit(&mb.ready, memory_order_acquire) && strcmp(key, "chain_params") && strcmp(key, MB_PAGES_KEY))
        return -1;                       /* (the worker still boots the app: nothing of it to read yet) */
    if (!strcmp(key, "state")) return mb_state_get(buf, n);
    if ((i = mb_key_find(key)) >= 0) return mb_wire_text(&MOVE_KEYS[i], buf, n);
    if (!strcmp(key, MOVE_SCREEN_KEY)) return snprintf(buf, (size_t)n, "%s", MOVE_SCREEN_VALUE);   /* PAGES' value line */
    if (!strcmp(key, MOVE_GATE_KEY)) return snprintf(buf, (size_t)n, "%u", mb_engine());
    if (!strcmp(key, "preset")) return snprintf(buf, (size_t)n, "%u", (unsigned)set.preset);
    if (!strcmp(key, "preset_count")) return snprintf(buf, (size_t)n, "%u", (unsigned)MB_PR_N);
    if (!strcmp(key, "preset_name")) { char s[20]; mb_pr_name(set.preset < MB_PR_N ? set.preset : 0u, s); return snprintf(buf, (size_t)n, "%s", s); }
    if (!strcmp(key, "u_first")) return snprintf(buf, (size_t)n, "%u", (unsigned)PR_NF);   /* U01's index (U01-U16 one run) */
    if (!strcmp(key, MB_PAGES_KEY)) {   /* the sounding engine's pages (gen_move_module.py hier_for_engine) */
        unsigned e = mb_engine();
        if ((int)MOVE_UI_HIERARCHY_N[e] >= n) return -1;
        memcpy(buf, MOVE_UI_HIERARCHY[e], (size_t)MOVE_UI_HIERARCHY_N[e] + 1u);
        return (int)MOVE_UI_HIERARCHY_N[e];
    }
    if (!strcmp(key, "is_loading")) return snprintf(buf, (size_t)n, "%d", mb_engine_moved());
    if (!strcmp(key, "chain_params")) { if ((int)sizeof MOVE_CHAIN_PARAMS > n) return -1; memcpy(buf, MOVE_CHAIN_PARAMS, sizeof MOVE_CHAIN_PARAMS); return (int)sizeof MOVE_CHAIN_PARAMS - 1; }
    if (!strcmp(key, "rev")) return snprintf(buf, (size_t)n, "%u", mb.rev);
    if (!strcmp(key, "u_names")) {      /* the 16 user slots, "|" between: "U05 ST77 TRM", empty for an unused slot */
        int w = 0;
        for (uint32_t u = 0; u < PR_NU; u++) {
            char s[20];
            s[0] = 0;
            if (pr_user[u].used) preset_name(PR_NF + u, s);
            w += snprintf(buf + w, (size_t)(n - w), "%s%s", u ? "|" : "", s);
            if (w >= n) return -1;
        }
        return w;
    }
    if (!strcmp(key, "u_savepend")) return snprintf(buf, (size_t)n, "%u", (unsigned)pr_save_pending);   /* 1: waits for quiet */
    if (!strcmp(key, "dev_cpu_ring")) return mb_cpu_ring(buf, n);
    if (!strcmp(key, "dev_store")) return snprintf(buf, (size_t)n, "dir=%s inst=%s loaded=%u saves=%u fail=%u imports=%u usave_n=%u", mb.dir, mb.idir, mb.loaded, mb.saves, mb.save_fail, mb.imp_done / 2u, mb.usaves);
    if (!strcmp(key, "dev_pattern_crc")) return snprintf(buf, (size_t)n, "%08x", ms_crc32(host_mem + MB_PAT_LO, MB_PAT_N));
    if (!strcmp(key, "dev_sus")) return snprintf(buf, (size_t)n, "held=%u latched=%u midi=%u down=%u up=%u touch=%d",
                                                 (unsigned)sus_held, (unsigned)sus_latched, (unsigned)sus_midi,
                                                 mb.sus_down, mb.sus_up, atomic_load(&mb.sus_touch));
    if (!strcmp(key, "dev_play")) return mp_get(buf, n);
    if (!strcmp(key, "r_cmp_gr") || !strcmp(key, "r_cmp_gr:effective")) {
        /* the meter: "OFF" (COMP off), else "<dB x 10> <the held peak, dB x 10>"; :effective the same (the knob grid's
         * GR cell is "live": Schwung reads <key>:effective every tick) */
        uint32_t w = atomic_load_explicit(&mb.gr_word, memory_order_relaxed);
        return w >> 20 & 1u ? snprintf(buf, (size_t)n, "%u %u", w & 1023u, w >> 10 & 1023u) : snprintf(buf, (size_t)n, "OFF");
    }
    if (!strcmp(key, "r_cmp_pk")) {   /* the GR cell's second half: "OFF", else the held peak (dB x 10) */
        uint32_t w = atomic_load_explicit(&mb.gr_word, memory_order_relaxed);
        return w >> 20 & 1u ? snprintf(buf, (size_t)n, "%u", w >> 10 & 1023u) : snprintf(buf, (size_t)n, "OFF");
    }
    if (!strcmp(key, "dev_inert")) return snprintf(buf, (size_t)n, "cc_drop=%u stops=%u seq=%u rec=%u arp=%u hold=%u chord=%u scale=%u trans=%d clock=%u bpm10=%u host_bpm=%u arpn=%u",
                                                   mb.cc_drop, mb.inert_n, (unsigned)seq_play, (unsigned)seq_rec,
                                                   (unsigned)ARP_MODE, (unsigned)ARP_HOLD, (unsigned)CH_TYPE,
                                                   (unsigned)set.sc_mode, (int)set.sc_trans, (unsigned)set.clock,
                                                   (unsigned)tempo_bpm10(), mb.bpm_host, (unsigned)arp_n);
    if (!strcmp(key, "dev_map")) return snprintf(buf, (size_t)n, "out=%u drop=%u last=%d,%d", mb.map_out, mb.map_drop,
                                                 mb.map_last[0], mb.map_last[1]);
#ifdef FR_HQ
    if (!strcmp(key, "p_hq")) return snprintf(buf, (size_t)n, "%u", (unsigned)hq_mode);
#endif
    if (!strcmp(key, "dev_cpu")) return snprintf(buf, (size_t)n, "callbacks=%u max_us=%u", atomic_load(&mb.cpu_n), mb.cpu_max);
    if (!strcmp(key, "dev_trim")) return snprintf(buf, (size_t)n, "%u", (unsigned)mb_trim);
    /* development reads (tests, the device session) */
    if (!strcmp(key, "dev_stats")) return mb_stats(buf, n);
    if (!strcmp(key, "dev_rx") || !strcmp(key, "dev_fxlog")) {   /* oldest first: "n=<total>;ms st d1 d2 src;..." */
        int rx = key[4] == 'r';
        uint32_t tot = rx ? atomic_load_explicit(&mb_rx.n, memory_order_acquire) : atomic_load_explicit(&mb_fxlog.n, memory_order_acquire);
        uint32_t cap = rx ? MB_RX_N : MB_FXLOG_N, k = tot > cap ? tot - cap : 0u;
        int o = snprintf(buf, (size_t)n, "n=%u fx=%d;", tot, fx_bypass == 0);
        for (; k < tot && o > 0 && o < n - 40; k++) {
            uint32_t i = k % cap;
            if (rx) o += snprintf(buf + o, (size_t)(n - o), "%u %02x %u %u %u;", mb_rx.ms[i], mb_rx.st[i], mb_rx.d1[i], mb_rx.d2[i], mb_rx.src[i]);
            else o += snprintf(buf + o, (size_t)(n - o), "%u %u %s;", mb_fxlog.ms[i], mb_fxlog.on[i], mb_fxlog.why[i]);
        }
        return o;
    }
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
    if (!strcmp(key, "preset")) { int p = atoi(val); if (p >= 0 && p < (int)MB_PR_N) (void)mb_put(MB_EV_PRESET, 0, 0, 0, 0, 0, p); return; }
    if (!strcmp(key, "u_save")) { int u = atoi(val); if (u >= 1 && u <= (int)PR_NU) (void)mb_put(MB_EV_USAVE, 0, 0, 0, 0, 0, u - 1); return; }
#ifdef FR_HQ
    if (!strcmp(key, "p_hq")) { (void)mb_put(MB_EV_HQ, 0, 0, 0, 0, 0, atoi(val) != 0); return; }   /* dev / test only (no page):
                                                          * 1 the HQ Rhodes (the load's), 0 Classic until the next load */
#endif
    if (!strcmp(key, "dev_trim")) { mb_trim = atoi(val) ? MB_TRIM_Q12 : 4096; return; }   /* dev / test only: 0 unity */
    if (!strcmp(key, "dev_midi")) {                       /* "T ST D1 D2": a message due at host ms T (the harness) */
        unsigned t, st, d1, d2;
        if (sscanf(val, "%u %i %i %i", &t, &st, &d1, &d2) == 4 && st >= 0x80u && st <= 0xFFu)
            (void)mb_put(MB_EV_MIDI, (uint8_t)st, (uint8_t)(d1 & 127u), (uint8_t)(d2 & 127u), t ? t : 1u, 0, 0);
        return;
    }
    if (!strcmp(key, "dev_play") || !strcmp(key, "dev_play_add")) { mp_set(key, val); return; }
    if (!strcmp(key, "dev_cpu_log")) { atomic_store(&mb.cpu_log, atoi(val) != 0); return; }
    if (!strcmp(key, "dev_stats_log")) { atomic_store(&mb.stats_log, atoi(val) != 0); return; }
    if (!strcmp(key, "dev_rx_log")) { atomic_store(&mb_rx_log, atoi(val) != 0); return; }
    if (!strcmp(key, "dev_sus_touch")) { int t = atoi(val); atomic_store(&mb.sus_touch, t >= 0 && t <= 9 && t != 8 ? t : -1); return; }
    if (!strcmp(key, "dev_clock")) { int m = atoi(val); if (m >= MB_CLOCK_AUTO && m <= MB_CLOCK_HOST) mb.clock_mode = m; return; }
}
