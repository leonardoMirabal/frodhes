/* SPDX-License-Identifier: GPL-3.0-only */
/* One slot's glue (plugin/src/bridge.h): included by plugin/src/engine_unit.c after the app, so the app's statics are
 * in scope; everything here is static too. Float is allowed in plugin/ (the app itself stays integer).
 *
 * The time model is the Move's (move/move_bridge.h mb_step), which equals the host build (tests/host.c):
 *   step:  1. ms += 1
 *          2. the queued events due by ms: MIDI into web_hal.c's 64-entry ring (a burst waits here, never dropped),
 *             parameter writes the panel's way (ui.c slot_turn: its side effects), the engine path, a reset
 *          3. a pending state load (the mailbox)
 *          4. the audio owed: every 256 frames app_audio(256) into the FIFO (int32, MASTER at unity, no clip)
 *          5. one main-loop pass of app_main() (the coroutine) up to its next platform_poll()
 *          6. the inert parts kept off (below), the values published for the main thread
 *
 * Inert in v1 (as on the Move, plan B2): the FM-1's sequencer, arpeggiator, chords and scales. Their CCs never reach
 * the app (SB_CC_DROP: 43-46 the sequencer's edits, 52-55 the arpeggiator, 56-59 the chords, 60-63 PLAY, BARS, QUANT,
 * REC; the scales, BPM and CLOCK have no CC), the pattern is not loaded, and sb_inert stops what a state from a Move
 * track or a MIDI Start would turn on. The tempo is the host's (its transport tempo, else 120 BPM) unless a MIDI clock
 * leads. Nothing is saved to disk: the settings live in the host's project (the CLAP state). */
#include <fenv.h>
#include <stdatomic.h>
#include "coro/coro.h"

#define SB_STACK (256u * 1024u)          /* the coroutine's stack (app_main and what it calls; ui_draw is the deepest) */
#define SB_FIFO 2048u                    /* frames (power of two); the instance never asks more than 1024 ahead */
#define SB_Q 4096u                       /* the event queue (power of two) */
#define SB_HOST_RING 64u                 /* web_hal.c's mq[] */
#define SB_CC_DROP(cc) (((cc) >= 43u && (cc) <= 46u) || ((cc) >= 52u && (cc) <= 63u))
#define SB_BPM_FALLBACK10 1200u
#define SB_MAP_DEFAULT_HEX "00007f00007f"   /* the Move state's "map" (move_bridge.h MB_MAP_DEFAULT: every mapping off) */

typedef struct { uint8_t kind; uint32_t at, a; int32_t v; } sb_ev_t;

static struct {
    coro_t co;
    void *stack;
    int booted, first_now;
    uint32_t ms, owed;
    int32_t fifo[2u * SB_FIFO];
    uint32_t fr, fw;
    sb_ev_t q[SB_Q];
    _Atomic uint32_t qw, qr;             /* one producer and one consumer, never at once (bridge.h: threads) */
    _Atomic int st_flag;                 /* the state mailbox: 0 free, 1 being written, 2 ready, 3 being applied */
    settings_t st_set;
    uint8_t st_base[P_SLOTS];
    uint16_t st_hand;
    int st_engine;                       /* -1: the state had none */
    _Atomic uint32_t pub_seq;            /* the published copy (odd: being written) */
    frp_snap_t pub;
    settings_t pub_set;
    uint8_t pub_base[P_SLOTS];
    uint16_t pub_hand;
    uint32_t rev;
    _Atomic uint32_t tempo10;
    frp_stats_t st;
    char boot_state[FRP_STATE_MAX];
} sb = { .first_now = 1, .st_engine = -1 };

/* ---- the coroutine (web_hal.c platform_poll asks the clock, then yields) */
static double move_now_ms(void)          /* (plugin/compat/emscripten.h: the name web_hal.c's shim calls) */
{
    /* tests/host.c's first poll is ms 1 and web_hal.c's first reading is its zero: the first reading is 0.25, the
     * n-th (n >= 2) ms + 1.5, so fm1_ms equals host.c's from the second poll on (as move_bridge.h move_now_ms) */
    if (sb.first_now) { sb.first_now = 0; return 0.25; }
    return (double)sb.ms + 1.5;
}
static void move_yield(void) { coro_yield(&sb.co); }
static void sb_entry(void *arg)
{
    (void)arg;
    coro_entered(&sb.co);
    app_main();                          /* (not web_hal.c's main(): no erase after the boot's setup) */
    for (;;) coro_yield(&sb.co);
}

/* ---- the published values (a sequence lock: the audio side writes, any thread reads) */
static void sb_publish(void)
{
    uint32_t q = atomic_load_explicit(&sb.pub_seq, memory_order_relaxed), i;
    atomic_store_explicit(&sb.pub_seq, q + 1u, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    for (i = 0; i < P_COUNT; i++) sb.pub.pv[i] = PV(i);
#ifdef FR_HQ
    sb.pub.engine = hq_mode ? FRP_ENGINE_HQ : FRP_ENGINE_CLASSIC;
#else
    sb.pub.engine = FRP_ENGINE_CLASSIC;
#endif
    sb.pub.rev = sb.rev;
    sb.pub_set = set;
    memcpy(sb.pub_base, snd_base, P_SLOTS);
    sb.pub_hand = (uint16_t)bpm_hand;
    atomic_store_explicit(&sb.pub_seq, q + 2u, memory_order_release);
}
static void sb_read_pub(frp_snap_t *snap, settings_t *s, uint8_t *base, uint16_t *hand)
{
    for (;;) {
        uint32_t q1 = atomic_load_explicit(&sb.pub_seq, memory_order_acquire), q2;
        if (q1 & 1u) continue;
        if (snap) *snap = sb.pub;
        if (s) *s = sb.pub_set;
        if (base) memcpy(base, sb.pub_base, P_SLOTS);
        if (hand) *hand = sb.pub_hand;
        atomic_thread_fence(memory_order_acquire);
        q2 = atomic_load_explicit(&sb.pub_seq, memory_order_relaxed);
        if (q1 == q2) return;
    }
}

/* ---- the state: the Move's flat JSON (move_bridge.h mb_state_get / mb_state_set), so a sound moves between a Move
 * track and a desktop session: the 256 B settings record as the app holds it, the sound it was loaded as (snd_base),
 * the Move's mappings (written off; ignored here), the tempo set by hand; and "engine" (the plugin's ENGINE: the Move
 * ignores it, a state without it keeps the instance's). The Move's "rev" (a change counter, which its state_set never
 * reads) is left out: a saved state depends on the sound only, so saving, loading and saving again gives the same bytes */
static const char SB_HEX[] = "0123456789abcdef";
static int sb_hex_put(char *o, const void *p, uint32_t n)
{
    const uint8_t *b = p;
    for (uint32_t i = 0; i < n; i++) { o[2 * i] = SB_HEX[b[i] >> 4]; o[2 * i + 1] = SB_HEX[b[i] & 15u]; }
    return (int)(2u * n);
}
static int sb_hex_get(const char *s, void *p, uint32_t n)
{
    uint8_t *b = p;
    for (uint32_t i = 0; i < 2u * n; i++) {
        char c = s[i]; int v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (v < 0) return -1;
        if (i & 1u) b[i >> 1] = (uint8_t)(b[i >> 1] << 4 | v); else b[i >> 1] = (uint8_t)v;
    }
    return 0;
}
#define SB_STATE_N (40 + 2 * 256 + 12 + 2 * P_SLOTS + 12 + 12 + 24 + 24 + 16)
_Static_assert(SB_STATE_N < FRP_STATE_MAX, "the state fits bridge.h FRP_STATE_MAX");
static int sb_state_get(char *buf, int n)
{
    frp_snap_t snap;
    settings_t s;
    uint8_t base[P_SLOTS];
    uint16_t hand;
    char *o = buf;
    if (n < SB_STATE_N + 1) return -1;
    sb_read_pub(&snap, &s, base, &hand);
    o += sprintf(o, "{\"v\":\"1\",\"set\":\"");
    o += sb_hex_put(o, &s, sizeof s);
    o += sprintf(o, "\",\"base\":\"");
    o += sb_hex_put(o, base, P_SLOTS);
    o += sprintf(o, "\",\"map\":\"%s\",\"hand\":\"%u\",\"engine\":\"%u\"}", SB_MAP_DEFAULT_HEX, (unsigned)hand,
                 (unsigned)snap.engine);
    return (int)(o - buf);
}
static const char *sb_json_str(const char *j, const char *key, uint32_t *len)   /* "key":"value" in a flat object */
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
static int sb_state_set(const char *j)   /* into the mailbox; sb_state_apply applies it between two passes */
{
    uint32_t ls = 0, lb = 0, lh = 0, lv = 0, le = 0;
    const char *v = j ? sb_json_str(j, "v", &lv) : 0, *s = j ? sb_json_str(j, "set", &ls) : 0;
    const char *b = j ? sb_json_str(j, "base", &lb) : 0, *h = j ? sb_json_str(j, "hand", &lh) : 0;
    const char *e = j ? sb_json_str(j, "engine", &le) : 0;
    int expect = 0;
    if (!v || lv != 1 || v[0] != '1' || !s || ls != 2u * sizeof(settings_t)) { sb.st.state_bad++; return -1; }
    while (!atomic_compare_exchange_weak(&sb.st_flag, &expect, 1)) {
        if (expect == 3) { sb.st.state_bad++; return -1; }   /* (being applied right now: the host may retry) */
        if (expect != 0 && expect != 2) expect = 0;
    }
    if (sb_hex_get(s, &sb.st_set, sizeof sb.st_set)) { atomic_store(&sb.st_flag, 0); sb.st.state_bad++; return -1; }
    if (!b || (lb != 2u * P_SLOTS && lb != 2u * 64u) || sb_hex_get(b, sb.st_base, lb / 2u))   /* (64: before the EQ) */
        for (uint32_t i = 0; i < 64u; i++) sb.st_base[i] = i < 32u ? sb.st_set.panel[i] : i < 48u ? sb.st_set.panel2[i - 32u] : sb.st_set.panel3[i - 48u];
    for (uint32_t i = P_EQ_LO; i < P_SLOTS; i++)        /* the EQ's ids: a state from before it (64 ids) flat */
        if (!b || lb != 2u * P_SLOTS)
            sb.st_base[i] = sb.st_set.flags2 & 2u ? (i < 64u ? sb.st_set.panel3[15] : sb.st_set.panel4[i - 64u]) :
                            i < P_COUNT ? PARAMS[i].def : 0u;
    sb.st_hand = sb.st_set.bpm10;
    if (h && lh > 0 && lh < 6) { uint32_t x = 0; for (uint32_t i = 0; i < lh; i++) x = x * 10u + (uint32_t)(h[i] - '0'); sb.st_hand = (uint16_t)x; }
    sb.st_engine = e && le == 1 && (e[0] == '0' || e[0] == '1') ? e[0] - '0' : -1;
    atomic_store(&sb.st_flag, 2);
    return 0;
}
static void sb_state_apply(void)         /* the boot's sanitize and apply steps (frodhes.c app_main), at run time */
{
    int expect = 2;
    if (!atomic_compare_exchange_strong(&sb.st_flag, &expect, 3)) return;
    set = sb.st_set;
    if (set.ver != 1u && set.ver != SET_VER) params_defaults();   /* (as the boot: a record it cannot read) */
    params_sanitize();
    kv_apply();
    arp_sanitize();
    ARP_MODE = ARP_OFF; ARP_HOLD = 0;    /* (inert in v1: a Move track's state may have it on) */
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
    bpm_hand = sb.st_hand >= 400u && sb.st_hand <= 2400u ? sb.st_hand : set.bpm10;
    for (uint32_t i = 0; i < P_SLOTS; i++) snd_base[i] = i < P_COUNT && sb.st_base[i] <= PARAMS[i].hi ? sb.st_base[i] : PV(i);
#ifdef FR_HQ
    if (sb.st_engine >= 0) hq_mode = (uint8_t)sb.st_engine;   /* (new notes; a sounding note keeps its path) */
#endif
    rev_relatch();
    tempo_sync_publish();
    set_dirty = 1;
    ui_draw_page_change();
    sb.st.state_ok++; sb.rev++;
    atomic_store(&sb.st_flag, 0);
}

/* ---- a parameter written the panel's way (move_bridge.h mb_slot_abs for an id < P_COUNT): its side effects
 * (cmp_wake, the motor's auto-on memory, the synced rates), set to an absolute value */
static void sb_param_abs(uint32_t s, int32_t x)
{
    int32_t hi, cur = slot_val(s, &hi), n;
    if (s >= P_COUNT) return;
    if (x < 0) x = 0;
    if (x > hi) x = hi;
    if (s == P_CMP_SW) { if (x && !PV(P_CMP_SW)) cmp_wake(1); else param_set(P_CMP_SW, x); ui_mark_param(s); return; }
    if (slot_synced(s)) { param_set(s, x); ui_mark_param(s); return; }   /* (the panel would step its SYNC) */
    for (n = 0; n < 512 && cur != x; n++) {                   /* an enum steps one a turn; an amount moves by d */
        slot_turn(s, x - cur);
        { int32_t now = slot_val(s, &hi); if (now == cur) break; cur = now; }
    }
    if (PV(s) != (uint8_t)x && x <= PARAMS[s].hi) param_set(s, x);   /* past a panel stop (MOTOR RATE: 120) */
    ui_mark_param(s);
    ui_mark(E_CARDS); ui_mark(E_FOOTER);
}

/* ---- the inert parts (above; move_bridge.h mb_inert, with the arpeggiator off too) */
static void sb_inert(void)
{
    uint32_t h = atomic_load_explicit(&sb.tempo10, memory_order_relaxed);
    uint32_t b10 = h >= 200u && h <= 4000u ? h : SB_BPM_FALLBACK10, t10 = b10 < 400u ? 400u : b10 > 2400u ? 2400u : b10;
    if (!clk_follows() && (clk_owns || set.bpm10 != t10)) { tempo_set10(b10); bpm_hand = set.bpm10; }
    if (CH_TYPE || set.sc_mode || set.sc_trans || set.clock != 1u || ARP_MODE != ARP_OFF) {
        if (CH_TYPE) CH_TYPE = 0;
        set.sc_mode = 0; set.sc_trans = 0; set.clock = 1;
        ARP_MODE = ARP_OFF;
        set_dirty = 1;
    }
    if (!(seq_play | seq_rec | sq_erase)) return;
    if (seq_rec) seq_toggle_rec();
    if (seq_play) seq_stop();
    sq_erase = 0;
}

/* ---- the queue */
static int sb_put(int kind, uint32_t at, uint32_t a, int32_t v)
{
    uint32_t w = atomic_load_explicit(&sb.qw, memory_order_relaxed), r = atomic_load_explicit(&sb.qr, memory_order_acquire);
    sb_ev_t *e;
    if (w - r >= SB_Q) { sb.st.q_full++; return -1; }
    e = &sb.q[w & (SB_Q - 1u)];
    e->kind = (uint8_t)kind; e->at = at; e->a = a; e->v = v;
    atomic_store_explicit(&sb.qw, w + 1u, memory_order_release);
    return 0;
}
static void sb_reset_now(void)           /* all notes off, the effects' state cleared (frodhes.c midi_cc 123, 10) */
{
    midi_cc(123u, 0u);
    midi_cc(10u, 127u);
}
/* apply the queued events; timed: only those due by sb.ms (and a full MIDI ring makes the rest wait) */
static void sb_events(int timed)
{
    for (;;) {
        uint32_t r = atomic_load_explicit(&sb.qr, memory_order_relaxed);
        sb_ev_t *e;
        if (r == atomic_load_explicit(&sb.qw, memory_order_acquire)) return;
        e = &sb.q[r & (SB_Q - 1u)];
        if (timed && e->at && (int32_t)(e->at - sb.ms) > 0) return;
        if (e->kind == FRP_EV_MIDI) {
            uint8_t st = (uint8_t)e->a, d1 = (uint8_t)(e->a >> 8) & 127u, d2 = (uint8_t)(e->a >> 16) & 127u;
            if ((st & 0xF0u) == 0xB0u && SB_CC_DROP(d1)) sb.st.cc_drop++;
            else {
                if (mq_w - mq_r >= SB_HOST_RING) { sb.st.ring_wait++; return; }   /* the rest wait a millisecond */
                web_midi(st, d1, d2);
                sb.st.midi_out++;
            }
        } else if (e->kind == FRP_EV_PARAM) { sb_param_abs(e->a, e->v); sb.st.params++; sb.rev++; }
#ifdef FR_HQ
        else if (e->kind == FRP_EV_ENGINE) { hq_mode = (uint8_t)(e->v != 0); sb.rev++; }
#endif
        else if (e->kind == FRP_EV_RESET) sb_reset_now();
        atomic_store_explicit(&sb.qr, r + 1u, memory_order_release);
    }
}

static void sb_step(void)
{
    fenv_t env;
    fegetenv(&env);
    fesetenv(FE_DFL_ENV);                /* the host build's floating-point state (no flush-to-zero): the HQ paths'
                                          * bits do not depend on what the host set on its audio thread */
    sb.ms++;
    sb.st.steps++;
    sb_events(1);
    sb_state_apply();
    sb.owed += FRP_FS;
    while (sb.owed >= 1000u * HALF_FRAMES) {
        static int32_t blk[2u * HALF_FRAMES];
        uint32_t i;
        sb.owed -= 1000u * HALF_FRAMES;
        memset(blk, 0, sizeof blk);
        app_audio(blk, HALF_FRAMES);
        for (i = 0; i < HALF_FRAMES; i++) {
            uint32_t w = (sb.fw + i) & (SB_FIFO - 1u);
            sb.fifo[2u * w] = blk[2u * i];
            sb.fifo[2u * w + 1u] = blk[2u * i + 1u];
        }
        sb.fw += HALF_FRAMES;
        sb.st.blocks++;
    }
    coro_resume(&sb.co);
    sb_inert();
    sb_publish();
    fesetenv(&env);
}

static int sb_boot(int engine)
{
    if (sb.booted) return 0;
    sb.stack = aligned_alloc(16, SB_STACK);
    if (!sb.stack) return -1;
    memset(sb.stack, 0, SB_STACK);       /* (every page touched now, not on the audio thread) */
    memset(host_mem, 0xFF, sizeof host_mem);   /* (the flash, erased: nothing saved, the defaults load) */
    memset(web_store_buf, 0, sizeof web_store_buf);
#ifdef FR_HQ
    hq_mode = (uint8_t)(engine != 0);
#else
    (void)engine;
#endif
    coro_init(&sb.co, sb.stack, SB_STACK, sb_entry, 0);
    coro_resume(&sb.co);                 /* app_main up to its first poll */
    arp_defaults();
    sb.booted = 1;
    sb_publish();
    if (sb_state_get(sb.boot_state, (int)sizeof sb.boot_state) < 0) sb.boot_state[0] = 0;
    return 0;
}
static int sb_booted(void) { return sb.booted; }
static void sb_drain(void)
{
    if (!sb.booted) return;
    sb_events(0);
    sb_state_apply();
    sb_publish();
}
static void sb_reuse(int engine)
{
    if (!sb.booted) return;
    atomic_store(&sb.qr, atomic_load(&sb.qw));   /* (the last instance's events: dropped) */
    if (sb.boot_state[0]) (void)sb_state_set(sb.boot_state);
    sb_state_apply();
    sb_reset_now();
#ifdef FR_HQ
    hq_mode = (uint8_t)(engine != 0);
#else
    (void)engine;
#endif
    sb.fr = sb.fw;                       /* (what the last instance left in the FIFO: dropped) */
    sb.rev++;
    sb_publish();
}
static uint32_t sb_avail(void) { return sb.fw - sb.fr; }
static void sb_read(int32_t *lr, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        uint32_t r = (sb.fr + i) & (SB_FIFO - 1u);
        lr[2u * i] = sb.fifo[2u * r];
        lr[2u * i + 1u] = sb.fifo[2u * r + 1u];
    }
    sb.fr += n;
}
static uint32_t sb_now(void) { return sb.ms; }
static void sb_snapshot(frp_snap_t *out) { sb_read_pub(out, 0, 0, 0); }
static void sb_set_tempo(uint32_t bpm10) { atomic_store_explicit(&sb.tempo10, bpm10, memory_order_relaxed); }
static void sb_stats(frp_stats_t *out) { *out = sb.st; out->ms = sb.ms; }
static int sb_check_params(void)
{
    int bad = FRP_NPARAM_APP == P_COUNT ? 0 : 1000;
    for (uint32_t i = 0; i < P_COUNT && i < FRP_NPARAM_APP; i++)
        bad += FRP_PARAMS[i].id != i || FRP_PARAMS[i].hi != PARAMS[i].hi || FRP_PARAMS[i].def != PARAMS[i].def ||
               FRP_PARAMS[i].cc != PARAMS[i].cc;
    return bad;
}
