/* SPDX-License-Identifier: GPL-3.0-only */
/* One slot's glue (plugin/src/bridge.h): included by plugin/src/engine_unit.c after the app, so the app's statics are
 * in scope; everything here is static too. Float is allowed in plugin/ (the app itself stays integer).
 *
 * The time model is the Move's (move/move_bridge.h mb_step), which equals the host build (tests/host.c):
 *   step:  1. ms += 1
 *          2. the queued events due by ms: MIDI into web_hal.c's 64-entry ring (a burst waits here, never dropped),
 *             parameter writes the panel's way (ui.c slot_turn: its side effects), settings, presets, MIDI learn
 *          3. a pending state load / user preset (the mailboxes)
 *          4. the audio owed: every 256 frames app_audio(256) into the FIFO (int32, MASTER at unity, no clip)
 *          5. one main-loop pass of app_main() (the coroutine) up to its next platform_poll()
 *          6. the inert parts kept off (below), the tempo source, the values published for the main thread
 *
 * The plugin's own parts live here too, so one published record and one state carry everything:
 *   MIDI       the velocity curve (TOUCH: MEDIUM = as played), the learned CCs before the default map (the app's own
 *              CC table), program changes on / off and the plugin's preset list for them
 *   presets    F01..F19 (presets.c), F20..F57 (move/move_presets.h, the Move's variants), the user's files (their
 *              values come in through the mailbox, or for a program change from bridge.c's cache: frp_user_get);
 *              UNDO is the app's (presets.c preset_undo: the undo copy in the settings record), the names follow
 *   tempo      HOST (the DAW's tempo, 120 without one; a MIDI clock still leads, as CLOCK EXT), INTERNAL (the BPM
 *              setting, no clock), MIDI CLOCK (the clock, else the BPM setting); SYNC ALL (every tempo effect's SYNC
 *              at one division, now and on every preset load)
 *
 * Inert in v1 (as on the Move, plan B2): the FM-1's sequencer, arpeggiator, chords and scales. Their CCs never reach
 * the app (SB_CC_DROP: 43-46 the sequencer's edits, 52-55 the arpeggiator, 56-59 the chords, 60-63 PLAY, BARS, QUANT,
 * REC; the scales, BPM and CLOCK have no CC), the pattern is not loaded, and sb_inert stops what a state from a Move
 * track or a MIDI Start would turn on. Nothing is saved to disk here: the settings live in the host's project (the
 * CLAP state); the user presets are bridge.c's files. */
#include <fenv.h>
#include <stdatomic.h>
#include "coro/coro.h"
#include "../../move/move_presets.h"     /* F20..F57 (read only: the Move's generated table) */

#define SB_STACK (256u * 1024u)          /* the coroutine's stack (app_main and what it calls; ui_draw is the deepest) */
#define SB_FIFO 2048u                    /* frames (power of two); the instance never asks more than 1024 ahead */
#define SB_Q 4096u                       /* the event queue (power of two) */
#define SB_HOST_RING 64u                 /* web_hal.c's mq[] */
#define SB_CC_DROP(cc) (((cc) >= 43u && (cc) <= 46u) || ((cc) >= 52u && (cc) <= 63u))
#define SB_BPM_FALLBACK10 1200u
#define SB_MAP_DEFAULT_HEX "00007f00007f"   /* the Move state's "map" (move_bridge.h MB_MAP_DEFAULT: every mapping off) */
#define SB_PX_BASE (PR_NF + PR_NU)       /* the app's index of F20 (move_bridge.h MB_PX_BASE) */
#define SB_NFACT (PR_NF + MB_PX_N)       /* the factory presets: 57 */
#define SB_NOWN 5u                       /* the plugin's own parameters (params_map.h, ids 0x10000 + k) */
#define SB_LEARN_OWN 200u                /* a learned own parameter's code: 200 + k (the app's ids are < P_COUNT) */
_Static_assert(SB_NFACT == FRP_NFACTORY, "bridge.h FRP_NFACTORY: F01..F19 + F20..F57");
_Static_assert(MB_PX_FIRST == PR_NF + 1u && SB_PX_BASE == 35u, "move_presets.h follows presets.c (F20 = index 35)");
_Static_assert(P_COUNT <= FRP_NPARAM_APP && P_COUNT < SB_LEARN_OWN, "the learn codes");

typedef struct { uint8_t kind; uint32_t at, a; int32_t v; } sb_ev_t;
typedef struct {                         /* the plugin's settings that are not in the app's record */
    uint8_t pgm, tempo_src, sync_all, sync_div;
    uint16_t bpm10;                      /* INTERNAL / MIDI CLOCK without a clock */
} sb_cfg_t;
#define SB_CFG_DEFAULT { 1, FRP_TEMPO_HOST, 0, 9, 1200 }

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
    sb_cfg_t st_cfg;
    uint8_t st_sus;
    uint8_t st_learn[128];
    int16_t st_pidx, st_upidx;           /* -2: the state had none (derived from set.preset) */
    char st_pname[FRP_PNAME], st_upname[FRP_PNAME];
    _Atomic int up_flag;                 /* the user preset mailbox: as st_flag */
    uint8_t up_vals[P_COUNT];
    char up_name[FRP_PNAME];
    int up_saved;                        /* 1: the sound was saved under up_name (no load: the name, the base) */
    _Atomic uint32_t pub_seq;            /* the published copy (odd: being written) */
    frp_snap_t pub;
    settings_t pub_set;
    uint8_t pub_base[P_SLOTS];
    uint16_t pub_hand;
    uint32_t rev;
    _Atomic uint32_t tempo10;
    frp_stats_t st;
    char boot_state[FRP_STATE_MAX];
    /* the plugin's own state */
    sb_cfg_t cfg;
    uint8_t learn[128];                  /* a CC's parameter: its code + 1 (0: none; codes as SB_LEARN_OWN) */
    int16_t learning;                    /* the code waiting for a CC, -1 none */
    int16_t pidx, upidx;                 /* the plugin's preset index playing / of the undo copy (-1: not in the list) */
    char pname[FRP_PNAME], upname[FRP_PNAME];
    int16_t last_fact;                   /* the last factory preset (the PRESET parameter's value) */
    uint8_t seen_preset;                 /* set.preset as last seen (the app's own loads: a program change) */
} sb = { .first_now = 1, .st_pidx = -2, .st_upidx = -2, .cfg = SB_CFG_DEFAULT, .learning = -1, .pidx = 0,
         .upidx = -1 };

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

/* ---- the presets: the plugin's index 0..56 = F01..F57; the app's: 0..18 F01..F19, 35..72 F20..F57 */
static uint32_t sb_app_idx(uint32_t i) { return i < PR_NF ? i : SB_PX_BASE + (i - PR_NF); }
static int sb_plug_idx(uint32_t a)       /* the app's index -> the plugin's, -1 (a user slot: not in the list) */
{
    return a < PR_NF ? (int)a : a >= SB_PX_BASE && a < SB_PX_BASE + MB_PX_N ? (int)(PR_NF + a - SB_PX_BASE) : -1;
}
static void sb_fact_name(uint32_t i, char *s, uint32_t n)   /* "SUITCASE" (no tag) */
{
    const char *nm = i < PR_NF ? PR_FNAME[i] : i < SB_NFACT ? MB_PX_NAME[i - PR_NF] : "";
    uint32_t k = 0;
    while (nm[k] && k + 1u < n) { s[k] = nm[k]; k++; }
    while (k > 0 && s[k - 1u] == ' ') k--;
    s[k] = 0;
}
static int sb_fact_values(uint32_t i, uint8_t *p)
{
    uint32_t a = sb_app_idx(i), k;
    const uint8_t *d;
    if (i >= SB_NFACT) return -1;
    if (a < PR_NF) return preset_values(a, p);
    for (k = 0; k < P_COUNT; k++) p[k] = PARAMS[k].def;
    d = MB_PX_DATA[a - SB_PX_BASE];
    for (k = 0; k + 1u < MB_PX_REC && d[k] != 0xFFu; k += 2u) if (d[k] < P_COUNT && d[k + 1u] <= PARAMS[d[k]].hi) p[d[k]] = d[k + 1u];
    return 0;
}
static void sb_name_copy(char *d, const char *s)
{
    uint32_t k = 0;
    for (; s && s[k] && k + 1u < FRP_PNAME; k++) d[k] = s[k] == '"' || s[k] == '\\' || (uint8_t)s[k] < 32u ? '_' : s[k];
    d[k] = 0;
}

/* ---- the published values (a sequence lock: the audio side writes, any thread reads) */
static void sb_publish(void)
{
    uint32_t q = atomic_load_explicit(&sb.pub_seq, memory_order_relaxed), i, ed = 0;
    atomic_store_explicit(&sb.pub_seq, q + 1u, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    for (i = 0; i < P_COUNT; i++) { sb.pub.pv[i] = PV(i); ed |= PV(i) != snd_base[i]; }
#ifdef FR_HQ
    sb.pub.engine = FRP_ENGINE_HQ;
#else
    sb.pub.engine = FRP_ENGINE_CLASSIC;
#endif
    sb.pub.voices = (uint8_t)RH_NV;
    sb.pub.tune = (uint8_t)(set.tune + 50);
    sb.pub.sustain = (uint8_t)(sus_held || sus_latched || sus_midi >= 64u);
    sb.pub.bypass = fx_bypass;
    sb.pub.edited = (uint8_t)ed;
    sb.pub.can_undo = set.undo_valid;
    sb.pub.quiet = (uint8_t)(eng_quiet() && fx_quiet());
    sb.pub.pidx = sb.pidx;
    sb.pub.last_fact = sb.last_fact;
    memcpy(sb.pub.pname, sb.pname, FRP_PNAME);
    sb.pub.midi_ch = set.midi_ch;
    sb.pub.touch = (uint8_t)kv_touch();
    sb.pub.key_vel = set.key_vel;
    sb.pub.pgm = sb.cfg.pgm;
    sb.pub.tempo_src = sb.cfg.tempo_src;
    sb.pub.sync_all = sb.cfg.sync_all;
    sb.pub.sync_div = sb.cfg.sync_div;
    sb.pub.bpm10 = sb.cfg.bpm10;
    sb.pub.tempo10 = (uint16_t)tempo_bpm10();
    sb.pub.host10 = (uint16_t)atomic_load_explicit(&sb.tempo10, memory_order_relaxed);
    sb.pub.learning = sb.learning;
    memcpy(sb.pub.learn, sb.learn, sizeof sb.learn);
    sb.pub.gr_q12 = PV(P_CMP_SW) && !fx_bypass ? fx_cmp_gr : 0;
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
 * track and a desktop session: the 256 B settings record as the app holds it (the sound, its undo copy, MIDI CHANNEL,
 * TOUCH, KEY VEL, TUNE ...), the sound it was loaded as (snd_base), the Move's mappings (written off; ignored here),
 * the tempo set by hand; "engine" (the plugin's ENGINE: bridge.c picks the slot by it; the Move ignores it). Then the
 * plugin's own keys (version "pv" 2; the Move ignores them, a state without them loads their defaults): "pgm" program
 * changes, "tsrc" the tempo source, "ibpm" its BPM, "sall" / "sdiv" SYNC ALL, "sus" the SUSTAIN parameter, "learn"
 * the learned CCs (128 bytes: code + 1 per CC), "pidx" / "pname" the preset playing, "upidx" / "upname" the undo
 * copy's. The Move's "rev" (a change counter its state_set never reads) is left out: a saved state depends on the
 * sound only, so saving, loading and saving again gives the same bytes. Version history: "v" 1 the Move's keys (the scaffold); "pv" 2 the plugin's. */
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
#define SB_STATE_N (40 + 2 * 256 + 12 + 2 * P_SLOTS + 12 + 12 + 24 + 24 + 16 + 120 + 2 * 128 + 4 * FRP_PNAME)
_Static_assert(SB_STATE_N < FRP_STATE_MAX, "the state fits bridge.h FRP_STATE_MAX");
static int sb_state_get(char *buf, int n)
{
    frp_snap_t snap;
    settings_t s;
    uint8_t base[P_SLOTS];
    uint16_t hand;
    char *o = buf, upn[FRP_PNAME];
    int16_t upidx;
    if (n < SB_STATE_N + 1) return -1;
    sb_read_pub(&snap, &s, base, &hand);
    /* (the undo copy's name is not in the published record: it changes only with the main thread's events, and the
     * state is read while those wait or between steps; a torn read here costs a name, never the sound) */
    upidx = sb.upidx;
    memcpy(upn, sb.upname, FRP_PNAME);
    upn[FRP_PNAME - 1] = 0;
    o += sprintf(o, "{\"v\":\"1\",\"set\":\"");
    o += sb_hex_put(o, &s, sizeof s);
    o += sprintf(o, "\",\"base\":\"");
    o += sb_hex_put(o, base, P_SLOTS);
    o += sprintf(o, "\",\"map\":\"%s\",\"hand\":\"%u\",\"engine\":\"%u\",\"pv\":\"2\",\"pgm\":\"%u\",\"tsrc\":\"%u\","
                 "\"ibpm\":\"%u\",\"sall\":\"%u\",\"sdiv\":\"%u\",\"sus\":\"%u\",\"learn\":\"", SB_MAP_DEFAULT_HEX,
                 (unsigned)hand, (unsigned)snap.engine, snap.pgm, snap.tempo_src, snap.bpm10, snap.sync_all,
                 snap.sync_div, snap.sustain);
    o += sb_hex_put(o, snap.learn, sizeof snap.learn);
    o += sprintf(o, "\",\"pidx\":\"%d\",\"pname\":\"%s\",\"upidx\":\"%d\",\"upname\":\"%s\"}", snap.pidx, snap.pname,
                 upidx, upn);
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
static int sb_json_int(const char *j, const char *key, int lo, int hi, int def)   /* a decimal value in range, else def */
{
    uint32_t l = 0, i = 0;
    const char *p = sb_json_str(j, key, &l);
    int neg = 0, x = 0;
    if (!p || !l || l > 7u) return def;
    if (p[0] == '-') { neg = 1; i = 1; }
    if (i == l) return def;
    for (; i < l; i++) { if (p[i] < '0' || p[i] > '9') return def; x = x * 10 + (p[i] - '0'); }
    x = neg ? -x : x;
    return x < lo || x > hi ? def : x;
}
static void sb_json_name(const char *j, const char *key, char *out)
{
    uint32_t l = 0;
    const char *p = sb_json_str(j, key, &l);
    char t[FRP_PNAME];
    if (l >= FRP_PNAME) l = FRP_PNAME - 1u;
    if (p) memcpy(t, p, l);
    t[p ? l : 0] = 0;
    sb_name_copy(out, t);
}
static int sb_state_set(const char *j)   /* into the mailbox; sb_state_apply applies it between two passes */
{
    uint32_t ls = 0, lb = 0, lh = 0, lv = 0, ll = 0;
    const char *v = j ? sb_json_str(j, "v", &lv) : 0, *s = j ? sb_json_str(j, "set", &ls) : 0;
    const char *b = j ? sb_json_str(j, "base", &lb) : 0, *h = j ? sb_json_str(j, "hand", &lh) : 0;
    const char *lr = j ? sb_json_str(j, "learn", &ll) : 0;
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
    /* the plugin's keys ("pv" 2): each one missing or out of range loads its default */
    sb.st_cfg.pgm = (uint8_t)sb_json_int(j, "pgm", 0, 1, 1);
    sb.st_cfg.tempo_src = (uint8_t)sb_json_int(j, "tsrc", 0, 2, FRP_TEMPO_HOST);
    sb.st_cfg.bpm10 = (uint16_t)sb_json_int(j, "ibpm", 400, 2400, sb.st_hand >= 400u && sb.st_hand <= 2400u ? sb.st_hand : 1200);
    sb.st_cfg.sync_all = (uint8_t)sb_json_int(j, "sall", 0, 1, 0);
    sb.st_cfg.sync_div = (uint8_t)sb_json_int(j, "sdiv", 1, 13, 9);
    sb.st_sus = (uint8_t)sb_json_int(j, "sus", 0, 1, 0);
    if (!lr || ll != 256u || sb_hex_get(lr, sb.st_learn, 128u)) memset(sb.st_learn, 0, sizeof sb.st_learn);
    for (uint32_t i = 0; i < 128u; i++) {                /* (a code this build does not have: dropped) */
        uint32_t c = sb.st_learn[i];
        if (c && !(c - 1u < P_COUNT || (c - 1u >= SB_LEARN_OWN && c - 1u < SB_LEARN_OWN + SB_NOWN))) sb.st_learn[i] = 0;
    }
    sb.st_pidx = (int16_t)sb_json_int(j, "pidx", -1, (int)SB_NFACT - 1, -2);
    sb.st_upidx = (int16_t)sb_json_int(j, "upidx", -1, (int)SB_NFACT - 1, -2);
    sb_json_name(j, "pname", sb.st_pname);
    sb_json_name(j, "upname", sb.st_upname);
    atomic_store(&sb.st_flag, 2);
    return 0;
}
static void sb_name_of_app(uint32_t a, int16_t *pidx, char *name)   /* the plugin's identity of the app's preset a */
{
    int i = sb_plug_idx(a);
    *pidx = (int16_t)i;
    if (i >= 0) sb_fact_name((uint32_t)i, name, FRP_PNAME);
    else if (a >= PR_NF && a < PR_NF + PR_NU && pr_user[a - PR_NF].used) sb_name_copy(name, pr_user[a - PR_NF].name);
    else name[0] = 0;
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
    sb.cfg = sb.st_cfg;
    if ((uint8_t)(sus_held || sus_latched || sus_midi >= 64u) != sb.st_sus)   /* (SUSTAIN, a parameter: as saved) */
        midi_cc(64u, sb.st_sus ? 127u : 0u);
    memcpy(sb.learn, sb.st_learn, sizeof sb.learn);
    sb.learning = -1;
    if (sb.st_pidx == -2) sb_name_of_app(set.preset, &sb.pidx, sb.pname);   /* (a state without the keys: the record's) */
    else { sb.pidx = sb.st_pidx; memcpy(sb.pname, sb.st_pname, FRP_PNAME); if (sb.pidx >= 0) sb_fact_name((uint32_t)sb.pidx, sb.pname, FRP_PNAME); }
    if (sb.st_upidx == -2) {
        if (set.undo_valid && set.undo_preset) sb_name_of_app(set.undo_preset - 1u, &sb.upidx, sb.upname);
        else { sb.upidx = -1; sb.upname[0] = 0; }
    } else { sb.upidx = sb.st_upidx; memcpy(sb.upname, sb.st_upname, FRP_PNAME); if (sb.upidx >= 0) sb_fact_name((uint32_t)sb.upidx, sb.upname, FRP_PNAME); }
    if (sb.pidx >= 0) sb.last_fact = sb.pidx;
    sb.seen_preset = set.preset;
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

/* ---- SYNC ALL: every tempo effect's SYNC at the division (on), or FREE (off) */
static const uint8_t SB_SYNC_IDS[6] = { P_TW_SYNC, P_CP_SYNC, P_DL_SYNC, P_PH_SYNC, P_LFO_SYNC, P_MOT_SYNC };
static void sb_sync_all(void)
{
    for (uint32_t i = 0; i < 6u; i++) {
        uint32_t id = SB_SYNC_IDS[i], hi = PARAMS[id].hi;
        sb_param_abs(id, sb.cfg.sync_all ? (int32_t)(sb.cfg.sync_div < hi ? sb.cfg.sync_div : hi) : 0);
    }
}

/* ---- presets: every load as presets.c preset_load (the undo copy, the base, the tempo sync), and the names */
static void sb_after_load(void)
{
    if (sb.cfg.sync_all) { sb_sync_all(); snd_base_take(); }   /* (SYNC ALL is part of the sound as loaded) */
    if (sb.pidx >= 0) sb.last_fact = sb.pidx;
    sb.seen_preset = set.preset;
    sb.rev++;
}
static void sb_load_fact(uint32_t i)
{
    uint8_t p[P_COUNT];
    uint32_t a = sb_app_idx(i), k;
    if (sb_fact_values(i, p)) return;
    sb.upidx = sb.pidx; memcpy(sb.upname, sb.pname, FRP_PNAME);
    if (a < PR_NF) preset_load(a);       /* (the app's own, as the panel's and a program change's) */
    else {                               /* (move_bridge.h mb_pr_load) */
        for (k = 0; k < P_COUNT; k++) UV(k) = PV(k);
        set.undo_valid = 1;
        set.undo_preset = (uint8_t)(set.preset < PR_NF + PR_NU ? set.preset + 1u : 0u);
        for (k = 0; k < P_COUNT; k++) param_set(k, p[k]);
        rev_relatch();
        set.preset = (uint8_t)a;
        snd_base_take();
        tempo_sync_publish();
        set_dirty = 1; set_t_change = fm1_ms;
        ui_draw_page_change();
    }
    sb.pidx = (int16_t)i;
    sb_fact_name(i, sb.pname, FRP_PNAME);
    sb_after_load();
}
static void sb_load_values(const uint8_t *p, const char *name)   /* a user preset (a file's values) */
{
    uint32_t k;
    sb.upidx = sb.pidx; memcpy(sb.upname, sb.pname, FRP_PNAME);
    for (k = 0; k < P_COUNT; k++) UV(k) = PV(k);
    set.undo_valid = 1;
    set.undo_preset = (uint8_t)(set.preset < PR_NF + PR_NU ? set.preset + 1u : 0u);   /* (set.preset stays: the record's
                                          * index means nothing for a file; preset_undo swaps it back to the same) */
    for (k = 0; k < P_COUNT; k++) param_set(k, p[k] <= PARAMS[k].hi ? p[k] : PARAMS[k].def);
    rev_relatch();
    snd_base_take();
    tempo_sync_publish();
    set_dirty = 1; set_t_change = fm1_ms;
    ui_draw_page_change();
    sb.pidx = -1;
    sb_name_copy(sb.pname, name);
    sb_after_load();
}
static void sb_undo(void)
{
    int16_t t;
    char n[FRP_PNAME];
    if (!set.undo_valid) return;
    preset_undo();
    t = sb.pidx; sb.pidx = sb.upidx; sb.upidx = t;
    memcpy(n, sb.pname, FRP_PNAME); memcpy(sb.pname, sb.upname, FRP_PNAME); memcpy(sb.upname, n, FRP_PNAME);
    if (sb.pidx >= 0) sb.last_fact = sb.pidx;
    sb.seen_preset = set.preset;
    sb.rev++;
}
static void sb_user_apply(void)          /* the user preset mailbox */
{
    int expect = 2;
    if (!atomic_compare_exchange_strong(&sb.up_flag, &expect, 3)) return;
    if (sb.up_saved) {                   /* saved: this sound is that preset now (no load, no undo step) */
        snd_base_take();
        sb.pidx = -1;
        sb_name_copy(sb.pname, sb.up_name);
        sb.rev++;
    } else sb_load_values(sb.up_vals, sb.up_name);
    atomic_store(&sb.up_flag, 0);
}
static int sb_user_put(const uint8_t *vals, const char *name, int saved)   /* any thread: into the mailbox */
{
    int expect = 0;
    if (!atomic_compare_exchange_strong(&sb.up_flag, &expect, 1)) return -1;
    if (vals) memcpy(sb.up_vals, vals, P_COUNT);
    sb_name_copy(sb.up_name, name);
    sb.up_saved = saved;
    atomic_store(&sb.up_flag, 2);
    return 0;
}
/* the app loaded a preset itself (a program change through its ring: presets.c preset_load): the names follow */
static void sb_preset_watch(void)
{
    if (set.preset == sb.seen_preset) return;
    sb.upidx = sb.pidx; memcpy(sb.upname, sb.pname, FRP_PNAME);
    sb_name_of_app(set.preset, &sb.pidx, sb.pname);
    sb_after_load();
}

/* ---- the plugin's own parameters (params_map.h ids 0x10000 + k; ENGINE is bridge.c's) */
static void sb_tune_abs(int32_t x)
{
    x = x < 0 ? 0 : x > 100 ? 100 : x;
    set.tune = (int8_t)(x - 50);
    rh_tune_t = TUNE_T[set.tune + 50];   /* (ui.c S_TUNE) */
    set_dirty = 1;
}
static void sb_own_abs(uint32_t k, int32_t x)
{
    if (k == FRP_ID_TUNE - FRP_ID_ENGINE) sb_tune_abs(x);
    else if (k == FRP_ID_PRESET - FRP_ID_ENGINE) { if (x >= 0 && x < (int32_t)SB_NFACT) sb_load_fact((uint32_t)x); }
    else if (k == FRP_ID_SUSTAIN - FRP_ID_ENGINE) midi_cc(64u, x ? 127u : 0u);   /* the SUSTAIN latch: as a CC 64 */
    else if (k == FRP_ID_FX_BYPASS - FRP_ID_ENGINE) midi_cc(111u, x ? 127u : 0u);  /* the FX button: as its CC 111 */
}
static int32_t sb_code_hi(uint32_t c)    /* a learn code's range */
{
    static const uint8_t OWN_HI[SB_NOWN] = { 0, 100, SB_NFACT - 1u, 1, 1 };
    return c < P_COUNT ? PARAMS[c].hi : c >= SB_LEARN_OWN && c < SB_LEARN_OWN + SB_NOWN ? OWN_HI[c - SB_LEARN_OWN] : 0;
}
static void sb_code_abs(uint32_t c, int32_t x)
{
    if (c < P_COUNT) sb_param_abs(c, x);
    else if (c >= SB_LEARN_OWN + 1u && c < SB_LEARN_OWN + SB_NOWN) sb_own_abs(c - SB_LEARN_OWN, x);
}

/* ---- settings (bridge.h FRP_CFG_*) */
static void sb_cfg(uint32_t f, int32_t v)
{
    static const uint8_t TOUCH_OF[4] = { KV_MEDIUM, KV_LIGHT, KV_HEAVY, KV_FIXED };   /* FRP_VEL_* -> keyvel.h */
    switch (f) {
    case FRP_CFG_MIDI_CH: set.midi_ch = (uint8_t)(v < 0 ? 0 : v > 16 ? 16 : v); set_dirty = 1; break;
    case FRP_CFG_VEL: kv_touch_store(TOUCH_OF[v & 3]); kv_apply(); set_dirty = 1; break;
    case FRP_CFG_FIXVEL: set.key_vel = (uint8_t)(v < 1 ? 1 : v > 127 ? 127 : v); set_dirty = 1; break;
    case FRP_CFG_PGM: sb.cfg.pgm = (uint8_t)(v != 0); break;
    case FRP_CFG_TEMPO: sb.cfg.tempo_src = (uint8_t)(v < 0 || v > 2 ? 0 : v); break;
    case FRP_CFG_BPM: sb.cfg.bpm10 = (uint16_t)(v < 400 ? 400 : v > 2400 ? 2400 : v); break;
    case FRP_CFG_SYNC_DIV: sb.cfg.sync_div = (uint8_t)(v < 1 ? 1 : v > 13 ? 13 : v); if (sb.cfg.sync_all) sb_sync_all(); break;
    case FRP_CFG_SYNC_ALL: sb.cfg.sync_all = (uint8_t)(v != 0); sb_sync_all(); break;
    default: return;
    }
    sb.rev++;
}

/* ---- MIDI learn: codes (an app id, or SB_LEARN_OWN + k) */
static int sb_learnable(uint32_t cc) { return cc && cc != 32u && cc != 64u && cc < 120u; }
static void sb_learn_op(uint32_t op, int32_t v)
{
    uint32_t i;
    if (op == FRP_LEARN_START) sb.learning = (int16_t)(v >= 0 && sb_code_hi((uint32_t)v) > 0 ? v : -1);
    else if (op == FRP_LEARN_CANCEL) sb.learning = -1;
    else if (op == FRP_LEARN_FORGET) { for (i = 0; i < 128u; i++) if (sb.learn[i] == (uint32_t)v + 1u) sb.learn[i] = 0; }
    else if (op == FRP_LEARN_FORGET_ALL) { memset(sb.learn, 0, sizeof sb.learn); sb.learning = -1; }
    sb.rev++;
}
static int sb_learn_cc(uint32_t ch, uint32_t cc, uint32_t val)   /* 1: the CC was the plugin's (learned / learning) */
{
    uint32_t c, i;
    if (set.midi_ch && ch != set.midi_ch - 1u) return 0;   /* (another channel: the app's filter drops it) */
    if (!sb_learnable(cc)) return 0;
    if (sb.learning >= 0) {              /* the next CC: the learning parameter's (one CC a parameter, one a CC) */
        for (i = 0; i < 128u; i++) if (sb.learn[i] == (uint32_t)sb.learning + 1u) sb.learn[i] = 0;
        sb.learn[cc] = (uint8_t)(sb.learning + 1);
        sb.learning = -1;
        sb.rev++;
        return 1;
    }
    if (!sb.learn[cc]) return 0;
    c = sb.learn[cc] - 1u;
    sb_code_abs(c, (int32_t)((val * (uint32_t)sb_code_hi(c) + 63u) / 127u));
    sb.rev++;
    return 1;
}

/* ---- the MIDI velocity curve (TOUCH): MEDIUM as played; LIGHT / HEAVY the device's gammas relative to MEDIUM's
 * (keyvel.c KV_GAMMA 0.45, 0.70, 1.10: v' = 127 (v / 127)^(g / 0.70)); FIXED: KEY VEL */
static uint8_t sb_vel(uint32_t v)
{
    static uint8_t tab[2][128];
    static int made;
    uint32_t t = kv_touch();
    if (t == KV_MEDIUM || !v) return (uint8_t)v;
    if (t == KV_FIXED) return set.key_vel;
    if (!made) {
        for (int k = 0; k < 128; k++) {
            double x = k / 127.0;
            int a = (int)lround(127.0 * pow(x, 0.45 / 0.70)), b = (int)lround(127.0 * pow(x, 1.10 / 0.70));
            tab[0][k] = (uint8_t)(k && a < 1 ? 1 : a); tab[1][k] = (uint8_t)(k && b < 1 ? 1 : b);
        }
        made = 1;
    }
    return tab[t == KV_LIGHT ? 0 : 1][v & 127u];
}

/* ---- program changes: 0..15 F01..F16, 32..34 F17..F19 through the app (presets.c preset_of_pc: as the FM-1),
 * 16..31 the first 16 user presets (bridge.c's files: frp_user_get; where there is no file, the app's own user bank
 * as on the FM-1: its starter sounds), 35..72 F20..F57: the FM-1's rule (32 + k loads F17 + k, presets.c) carried
 * on past its last preset, so every factory preset has a program change; 73..127 load nothing */
static int sb_pc_mine(uint32_t ch, uint32_t pc)   /* 1: the plugin takes it (or drops it), 0: the app's */
{
    uint8_t p[P_COUNT];
    char name[FRP_PNAME];
    if (!sb.cfg.pgm) return 1;
    if (set.midi_ch && ch != set.midi_ch - 1u) return 0;   /* (the app's filter drops it) */
    if (pc >= 16u && pc < 32u) return frp_user_get(pc - 16u, p, name) == 0;   /* (no file there: the app's own user
                                          * bank, its starter sounds, as on a new FM-1) */
    return !(pc < 16u || (pc >= 32u && pc < 16u + PR_NF));
}
static void sb_pc(uint32_t pc)
{
    uint8_t p[P_COUNT];
    char name[FRP_PNAME];
    if (!sb.cfg.pgm) { sb.st.cc_drop++; return; }
    if (pc >= 16u && pc < 32u) { if (!frp_user_get(pc - 16u, p, name)) sb_load_values(p, name); }
    else if (pc >= SB_PX_BASE && pc < SB_PX_BASE + MB_PX_N) sb_load_fact(PR_NF + pc - SB_PX_BASE);
}                                        /* (else no preset there) */

/* ---- the inert parts (above; move_bridge.h mb_inert, with the arpeggiator off too) and the tempo source */
static void sb_inert(void)
{
    uint32_t h = atomic_load_explicit(&sb.tempo10, memory_order_relaxed), clk = sb.cfg.tempo_src == FRP_TEMPO_INTERNAL ? 0u : 1u;
    uint32_t b10 = h >= 200u && h <= 4000u ? h : SB_BPM_FALLBACK10, t10 = b10 < 400u ? 400u : b10 > 2400u ? 2400u : b10;
    if (sb.cfg.tempo_src != FRP_TEMPO_HOST) b10 = t10 = sb.cfg.bpm10;
    if (CH_TYPE || set.sc_mode || set.sc_trans || set.clock != clk || ARP_MODE != ARP_OFF) {
        if (CH_TYPE) CH_TYPE = 0;
        set.sc_mode = 0; set.sc_trans = 0;
        if (set.clock != clk) { set.clock = (uint8_t)clk; if (!clk) tempo_clock_int(); }
        ARP_MODE = ARP_OFF;
        set_dirty = 1;
    }
    if (!clk_follows() && (clk_owns || set.bpm10 != t10)) { tempo_set10(b10); bpm_hand = set.bpm10; }
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
static uint32_t sb_room(void)            /* the producer's side: events that still fit */
{
    return SB_Q - (atomic_load_explicit(&sb.qw, memory_order_relaxed) - atomic_load_explicit(&sb.qr, memory_order_acquire));
}
static void sb_reset_now(void)           /* all notes off, the effects' state cleared (frodhes.c midi_cc 123, 10) */
{
    uint32_t t;
    midi_cc(123u, 0u);
    midi_cc(10u, 127u);
    /* CC 10's requests done now (fx.c fx_process, drive.c fx_drive_types), not at the next block: there
     * eng_rh_kill_all() also ended the notes that came after the reset, so a note played at once after a host's
     * reset was never heard. Here no block runs meanwhile (the slot's own thread); lk_reset stays for tempo.c. */
    fx_reset_state();
    fx_reset_req = 0;
    rh_side_on = 0; sp_g = 32768; sp_aw = 0;
    drv_rst_req = 0;
    for (t = 2u; t < DRV_N; t++) drv_reset(t);
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
        if (e->kind == FRP_EV_MIDI || e->kind == FRP_EV_UINOTE) {
            uint8_t st = (uint8_t)e->a, d1 = (uint8_t)(e->a >> 8) & 127u, d2 = (uint8_t)(e->a >> 16) & 127u;
            uint8_t t = st & 0xF0u, ch = st & 0x0Fu;
            if (e->kind == FRP_EV_UINOTE) { ch = set.midi_ch ? (uint8_t)(set.midi_ch - 1u) : 0u; st = (uint8_t)(t | ch); }
            if (t == 0xB0u && sb_learn_cc(ch, d1, d2)) sb.st.midi_out++;
            else if (t == 0xB0u && SB_CC_DROP(d1)) sb.st.cc_drop++;
            else if (t == 0xC0u && sb_pc_mine(ch, d1)) {
                if (mq_w != mq_r) return;      /* (it acts at once: never ahead of what the ring still holds) */
                sb_pc(d1);
                sb.st.midi_out++;
            } else {
                if (mq_w - mq_r >= SB_HOST_RING) { sb.st.ring_wait++; return; }   /* the rest wait a millisecond */
                if (t == 0x90u && d2) d2 = sb_vel(d2);
                web_midi(st, d1, d2);
                sb.st.midi_out++;
            }
        } else if (e->kind == FRP_EV_PARAM) {
            if (e->a < P_COUNT) sb_param_abs(e->a, e->v);
            else if (e->a > FRP_ID_ENGINE && e->a < FRP_ID_ENGINE + SB_NOWN) sb_own_abs(e->a - FRP_ID_ENGINE, e->v);
            sb.st.params++; sb.rev++;
        } else if (e->kind == FRP_EV_CFG) sb_cfg(e->a, e->v);
        else if (e->kind == FRP_EV_PRESET) { if (e->v >= 0 && e->v < (int32_t)SB_NFACT) sb_load_fact((uint32_t)e->v); }
        else if (e->kind == FRP_EV_UNDO) sb_undo();
        else if (e->kind == FRP_EV_LEARN) sb_learn_op(e->a, e->v);
        else if (e->kind == FRP_EV_RESET) sb_reset_now();
        else if (e->kind == FRP_EV_RELEASE) { midi_cc(64u, 0u); midi_cc(123u, 0u); }   /* (a tail: every note let go) */
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
    sb_user_apply();
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
    sb_preset_watch();
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
    sb_name_of_app(set.preset, &sb.pidx, sb.pname);
    sb.last_fact = sb.pidx >= 0 ? sb.pidx : 0;
    sb.seen_preset = set.preset;
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
    sb_user_apply();
    sb_preset_watch();
    sb_publish();
}
static void sb_reuse(int engine)
{
    if (!sb.booted) return;
    atomic_store(&sb.qr, atomic_load(&sb.qw));   /* (the last instance's events: dropped) */
    if (sb.boot_state[0]) (void)sb_state_set(sb.boot_state);
    sb_state_apply();
    if (sb.st.steps) sb_reset_now();     /* (a slot that never ran is as it booted: nothing to clear, its bits kept) */
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
static int sb_fact_info(uint32_t i, char *name, uint32_t n, uint8_t *vals)   /* any thread: constant tables only */
{
    if (i >= SB_NFACT) return -1;
    if (name) sb_fact_name(i, name, n);
    return vals ? sb_fact_values(i, vals) : 0;
}
