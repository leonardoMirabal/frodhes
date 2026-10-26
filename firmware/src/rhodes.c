/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes engine: the physically modelled Rhodes voice (IMPLEMENTATION-v3 §4.3, §5).
 *
 * Per voice: a hammer force pulse (shapes from a host contact simulation) drives the tine's normal modes (V1 the
 * tine-bar sustain mode exactly on f0, V2 the other tine-bar mode, O1/O2 the tine's 2nd/3rd modes, H1 the
 * horizontal polarisation in Phase 3). Each mode is an exact-pole coupled-form resonator ("MC3") with first-order
 * error feedback. The tip displacement goes through the electromagnetic pickup: a radial flux table Phi~(s),
 * s = (x + off)^2 + h^2, evaluated by cubic Hermite, output = the exact flux difference. The voices sum into a
 * mono mix that passes the pickup load (RLC biquad). All the physics is precomputed per key on the host
 * (tools/gen_rhodes.py -> firmware/gen/rhodes_tables.h).
 *
 * Main loop interface (unchanged for frodhes.c):
 *   rh_note_on(note, vel), rh_note_off(note, rvel), rh_pedal(on) / rh_pedal_level(cc)   post events
 *   rh_quiet(), rh_lit(base, n), rh_hold                                            read summaries
 * ISR: rh_render(mono, n) writes n mono samples (Q15) for the effects chain.
 * The ISR owns every voice; the main loop only posts 32-bit events into a single-producer ring (P1). */
#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#include "fxp.h"
#ifndef RING_PUBLISH
#define RING_PUBLISH() do { } while (0)     /* host mode: one thread, no barrier needed */
#endif

/* ---- generated-table types (must match tools/gen_rhodes.py) */
typedef struct { int32_t sh, ch, th, d, drel, cr, b; } rh_mode_t;
typedef struct {
    rh_mode_t m[5];
    int32_t off, gain, kill, hc, pk;
    uint16_t prate, life;
    uint8_t band, fam, res, pad;
} rh_key_t;
typedef struct {              /* physical voicing per TYPE (params.json "types"); STAGE 77 = all unity */
    int32_t off_delta;        /* Q25 gap: pickup offset change (+ = toward the pole axis) */
    int32_t bark, bark_g;     /* Q16: tip / gap scale (pickup distance) and its gain compensation */
    int32_t bell, ham;        /* Q16: overtone force scale, hammer contact-rate scale */
    uint8_t nbody, drive_row; /* body biquads in use (RH_BODY), the drive's row */
} rh_type_t;
#include "rhodes_tables.h"
#ifndef RH_NO_TONE_G                           /* (-DRH_NO_TONE_G: without it, as tools/tone_level.py measures) */
#include "rh_tone_g.h"                         /* TONE's level compensation (tools/tone_level.py --write) */
#endif
#include "rhodes_phim.h"                       /* the pickup table interleaved (tools/gen_phim.py) */

#define RH_V1 0u
#define RH_V2 1u
/* the inaudible-mode gate (0.9.5, the engine CPU study): the tone-bar mode V2 of keys 29..48 (a 28-104 Hz partial;
 * through the pickup 3 LSB at most at v127, its loudest 50 ms -97 dBFS(A) or less, -81 at the voicing knobs' ends;
 * tests/py/test_rh_gate.py, tools/engine_ab.py) never runs: a mode loop fewer per voice (29 instructions a sample
 * while the hammer pushes, 22 after) where the worst block is (bass notes struck at v127). Decided per mode at the
 * strike, outside the sample loops. -DRH_NO_GATE: every mode runs (the A/B reference, byte-identical to before).
 * Since the O1 tables (params.json v2_drop) keys 21-39 have no V2 at all, so the gate stops nothing; it ends at 39
 * because keys 40-84 hold the beat partner ("whirl", params.json whirl) in the V2 slot */
#define RH_V2_GATE_LO 29u
#define RH_V2_GATE_HI 39u
#define RH_O1 2u
#define RH_O2 3u
/* voices per engine: the Rhodes 10 (RH_VOICES; 8 for a while after 0.9.4's crackles with the pedal and every effect,
 * 9 again once 0.9.5's CPU work made room, the user 2026-10-24; 10 with 0.9.5's exact engine savings), the bar
 * engine 10 too (BAR_VOICES, bars.h BAR_NV: bars-10's loops and cost model, no upper mode trimmed in
 * tests/py/test_bars_engine.py test_no_trim_in_the_hil_cpu_worst). A build may set another count: -DRH_VOICES=N or
 * -DBAR_VOICES=N for one engine, -DFR_VOICES=N for both (APP_DEFS / HOST_DEFS; make testv V=N) */
#ifdef FR_VOICES
#ifndef RH_VOICES
#define RH_VOICES FR_VOICES
#endif
#ifndef BAR_VOICES
#define BAR_VOICES FR_VOICES
#endif
#endif
#ifndef RH_VOICES
#define RH_VOICES 10u
#endif
#ifndef BAR_VOICES
#define BAR_VOICES 10u
#endif
#define RH_NV RH_VOICES
_Static_assert(RH_NV >= 1u && RH_NV <= 32u, "summaries are 32-bit masks");
#define SUB 32u
#define RAMP_SUB 7u
#define FADE_SAMPLES 64u
#define RH_OFF_LIFE 1378u                      /* blocks (8 s) a voice may still ring after an all-off */
#define Q30 (1 << 30)
#define RH_S_MAX_HI 2048u                      /* s >> 44 at 32 gap^2 */
/* sat28 (fxp.h) for an int32: the same bounds, without the 64-bit compares */
static inline int32_t sat28i(int32_t v) { return v > ((1 << 28) - 1) ? ((1 << 28) - 1) : v < -((1 << 28) - 1) ? -((1 << 28) - 1) : v; }

typedef struct {
    int32_t x, y, ex, ey;                      /* Q25 gap, MC3 states and error-feedback remainders */
    int32_t e, eps;                            /* live Q30 */
    int32_t e0, eps0, e1, eps1;                /* sustain pair, release pair */
    int32_t es, epss;                          /* values at the start of the current crossfade */
    int32_t b;                                 /* Q30 force (Q15) -> y (Q25), with the TYPE bell */
    int32_t peak;
    int32_t kap;                               /* S4, V1 only: amplitude -> pitch (Q16 per gap^2; 0: off) */
    uint8_t live;
} rh_ms_t;

typedef struct {
    rh_ms_t m[RH_NM];
    const rh_key_t *k;
    uint8_t note, vel, alive, held;
    uint8_t ramp;                              /* damper crossfade sub-blocks left */
    uint8_t rlen;                              /* its length (RAMP_SUB, or the release velocity's: rh_rel_len) */
    uint16_t rgain;                            /* Q15: the release velocity's damper weight scale (key up only) */
    int32_t w_tgt;                             /* damper weight Q15: 0 = undamped, 32768 = full felt */
    uint8_t pend, pend_note, pend_vel;         /* a start waiting for the steal fade */
    int32_t fade;                              /* Q15 output gain while fading out (32768 = not fading) */
    uint32_t age, life;
    int32_t Hprev, off, gain;
    uint32_t ppos, prate;
    int32_t pamp;
    const int16_t *ptab;                       /* the hammer pulse shape: a table's layer, or pbuf */
    uint16_t felt_n;                           /* felt-noise samples left (key-up) */
    int32_t felt_lp;
    int16_t pbuf[RH_PLEN + 2];                 /* the two velocity layers around vel pre-mixed at the strike (no step at
                                                * a layer change: the crossfade), the force loop reads one table */
    int32_t pan;                               /* Q8 stereo position (SPREAD, at the strike): -256 left .. 256 right */
    int32_t lvl, lvl0;                         /* the modes' peak sum: the last sub-block's, the highest since the
                                                * strike (the key-up thump's cap: rh_left) */
    uint16_t felt_g;                           /* Q15: the key-up felt noise's gain (the same cap: RH_FELT_K) */
    int32_t kd, kr;                            /* DECAY's and RELEASE's k at the note-on: eps0 / eps1's (rh_retune) */
} rh_voice_t;

static rh_voice_t rv[RH_NV];
#ifdef DINGHY_HOST
static int32_t rh_mono[256], rh_x[SUB], rh_F[SUB];
#else                                          /* the mode / pickup loops' buffers in one section of their own (firmware
                                                * only), out of the compiler's global merge: with 8 voices the merge
                                                * moved them and the mode loop lost a register (a stack load) */
static int32_t rh_mono[256] __attribute__((section(".bss.rh_buf"))), rh_x[SUB] __attribute__((section(".bss.rh_buf"))),
    rh_F[SUB] __attribute__((section(".bss.rh_buf")));
#endif
static uint32_t rh_ages;
#ifdef DINGHY_HOST
static uint32_t rh_h_steals;                   /* host: the steals (engine.c eng_host_report, tools/engine_ab.py) */
#endif
static int32_t rh_pedal_w = 32768;             /* ISR copy: damper weight for keys that are up */
static int32_t rh_st[3][5];                    /* the pickup RLC's and the TYPE's body biquads' states (m1 m2 o1 o2 e) */
/* SPREAD (S13): each voice's stereo position, pan in [-1, 1], as a side signal S = sum pan_k v_k next to the mono
 * mix M = sum v_k (one multiply a sample a voice that has a position); fx.c makes L = M + S, R = M - S, so the
 * centre (and SPREAD 0, no side at all) is the mono sum exactly. S goes through the same pickup load and body
 * (its own filter state), Q20 -> Q15 into rh_side; rh_side_on says this block's rh_side is valid */
#ifdef DINGHY_HOST                                    /* each in a section of its own on the FM-1: in the merged
                                                        * globals they moved app_audio's registers (the effects' loops) */
#define RH_SP_SEC(s)
#else
#define RH_SP_SEC(s) __attribute__((section(s)))
#endif
static volatile int32_t rh_spread RH_SP_SEC(".bss.rh_spread");   /* SPREAD 0..127 (params.c), taken at each strike */
static int32_t rh_side[256] RH_SP_SEC(".bss.rh_side");
static int32_t sd_st[3][5] RH_SP_SEC(".bss.rh_sd_st");      /* the side's RLC and body biquad states (m1 m2 o1 o2 e) */
static uint8_t rh_side_on RH_SP_SEC(".bss.rh_side_on"), rh_side_tail RH_SP_SEC(".bss.rh_side_tail");
static uint8_t rh_type;
/* voicing, written by the main loop (params.c), applied at each note-on (IMPLEMENTATION-v3 §4.4, §10.1) */
static volatile int32_t rh_tone_off;           /* Q25: added to the pickup offset (TONE) */
static volatile int32_t rh_bark_s = 65536;     /* Q16: tip scale (BARK) */
static volatile int32_t rh_bark_g = 65536;     /* Q16: its gain compensation (1/scale) */
static volatile int32_t rh_bell = 65536;       /* Q16: O1/O2 scale (BELL) */
static volatile int32_t rh_ham = 65536;        /* Q16: contact-rate multiplier (HAMMER) */
static volatile int32_t rh_k_decay = 65536;    /* Q16: 1/S of the sustain T60 (DECAY) */
static volatile int32_t rh_k_rel = 65536;      /* Q16: 1/S of the release T60 (RELEASE) */
static volatile int32_t rh_tune_t;             /* Q30: 2^(c/1200) - 1 (TUNE) */
static volatile int32_t rh_thump, rh_felt;     /* Q15: damper thump force, felt-noise level (NOISE) */
/* the key-up thump never louder than the note it ends (2026-10-25: a fixed thump was up to 12 dB over a soft note
 * with the FM-1's real key velocity, 30 dB at NOISE 127: "I press a key and get 2 sounds"): its force is capped at
 * RH_THUMP_CAP x the strike's force (the note's own velocity law, v->pamp) x the level left (lvl / lvl0), Q15. A loud
 * note let go early keeps NOISE's thump; a soft or a decayed one gets a thump in proportion. Release velocity (S5)
 * scales it after the cap (x0.6 .. 1.4). tests/py/test_release_never_louder.py */
/* the felt noise at the key-up the same way: in full down to a note left at RH_FELT_FULL of the full force, in
 * proportion below */
#ifndef RH_FELT_FULL
#define RH_FELT_FULL 16384
#endif
#ifndef RH_THUMP_CAP
#define RH_THUMP_CAP 1638                      /* 0.05 */
#endif
static uint32_t rh_lfsr = 0xACE1u;
/* the key-down knock (S11, IMPLEMENTATION-v3 §5.7): the hammer's force pulse itself, heard through the mechanism (the
 * thunk a miked or amplified instrument has), added to the mix at the NOISE level - 10 dB. Not on STAGE 77 (a DI
 * harp barely has it: the default stays exactly as before). One shared player, not one per voice: the strikes of one
 * block (a chord) sum into one knock with the lowest key's pulse; a strike while a knock still sounds adds none (no
 * restart, no step). Started in rh_events (rh_strike), played by rh_noise after the voices */
static volatile int32_t rh_knock;              /* Q15: the knock level (KN_KNOCK[NOISE]), 0 = none */
static const uint8_t RH_KNOCK_ON[4] = {0, 1, 1, 1};   /* per TYPE: STAGE 77 none; SUITCASE, EARLY FELT, BELL DYNO */
static const int16_t *rh_kn_tab;               /* the pulse playing (0 = none) */
static uint32_t rh_kn_pos, rh_kn_rate;         /* Q16 position and step in it */
static int32_t rh_kn_amp;                      /* force x level (<= 65535), Q20 out per Q15 pulse */
static uint8_t rh_kn_note;
/* the pedal noise (S12): one shared thud when the pedal goes down (the damper rail lifts) or up (the felts land): a
 * 10 ms noise burst (an LCG, its own: the felt noise's LFSR keeps its sequence) through a one-pole (~900 Hz), its
 * envelope falling 2.5 dB every 32 samples (-34 dB at the end), at the NOISE level (KN_PEDAL). Played by rh_noise */
#define RH_PN_LEN 440u                         /* 10 ms (even: the samples go in pairs) */
static volatile int32_t rh_pnz;                /* Q15: the thud level (KN_PEDAL[NOISE]), 0 = none */
static uint32_t rh_pn_n, rh_pn_x = 0x2545F491u;  /* samples left; the noise state */
static int32_t rh_pn_lp, rh_pn_env;
static uint8_t rh_ped_dn;                      /* the pedal as the thud last saw it: down (the dampers off the tines) */
/* the pitch bend (engine.c eng_bend_set): own sections (firmware only) so the compiler's global merge leaves them out
 * and the hot loops keep their registers (as engine.c's ring) */
#ifdef DINGHY_HOST
#define RH_OWN_SECTION(s)
#else
#define RH_OWN_SECTION(s) __attribute__((section(s)))
#endif
static volatile int32_t rh_bend_t RH_OWN_SECTION(".bss.rh_bend_t");   /* main loop: Q30 2^(c/1200) - 1 of the bend */
static int32_t rh_bend_used RH_OWN_SECTION(".bss.rh_bend_u");         /* ISR: the bend the voices are tuned to */
/* the retune spread (rh_retune): the voices still to retune to rh_bend_used, and the round-robin cursor */
static uint32_t rh_rt_pend RH_OWN_SECTION(".bss.rh_rt_pend");
static uint32_t rh_rt_cur RH_OWN_SECTION(".bss.rh_rt_cur");
#define RH_RT_PER ((RH_NV + 3u) / 4u)                                     /* voices retuned per block: a quarter */

/* ISR-written summaries (one writer each) */
static volatile uint32_t rh_active_bits, rh_held_bits, rh_quiet_blocks;
static volatile uint8_t rh_notes[RH_NV];

/* ---- the event ring (main loop writes ev_w, ISR writes ev_r) */
#define EV_NOTE_ON 1u
#define EV_NOTE_OFF 2u
#define EV_PEDAL 3u
#define EV_ALL_OFF 4u
static volatile uint32_t ev_q[32];
static volatile uint32_t ev_w, ev_r, ev_drop;
static volatile uint8_t rh_hold;               /* main loop: the sustain pedal is down (for the screen/LED) */
static uint8_t rh_shed_req;                    /* ISR: the audio ran late: fade out the quietest released voice */
static volatile uint32_t rh_shed_count;

static void rh_post(uint32_t type, uint32_t note, uint32_t val)
{
    uint32_t w = ev_w;
    if (w - ev_r >= 32u) { ev_drop++; return; }
    ev_q[w & 31u] = type << 28 | (note & 127u) << 16 | (val & 0xFFFFu);
    RING_PUBLISH();
    ev_w = w + 1u;
}

static void rh_note_on(uint32_t note, uint32_t vel) { if (note < 128u && vel && vel < 128u) rh_post(EV_NOTE_ON, note, vel); }
static void rh_note_off(uint32_t note, uint32_t rv) { if (note < 128u) rh_post(EV_NOTE_OFF, note, rv < 128u ? rv : 0u); }
static void rh_pedal_level(uint32_t cc) { rh_hold = (uint8_t)(cc >= 64u); rh_post(EV_PEDAL, 0, cc > 127u ? 127u : cc); }
static void rh_pedal(int on) { rh_pedal_level(on ? 127u : 0u); }
static void rh_all_off(void) { rh_post(EV_ALL_OFF, 0, 0); }

static int rh_quiet(void) { return rh_active_bits == 0 && rh_quiet_blocks >= 2u && ev_w == ev_r; }

static uint32_t rh_lit(uint32_t base, uint32_t n)
{
    uint32_t v, lit = 0, act = rh_active_bits & rh_held_bits;
    for (v = 0; v < RH_NV; v++)
        if (act >> v & 1u && rh_notes[v] >= base && rh_notes[v] < base + n)
            lit |= 1u << (rh_notes[v] - base);
    return lit;
}

/* the mode runs: it exists for the key (th != 0) and the gate does not stop it */
static inline int rh_mode_runs(const rh_key_t *k, uint32_t m)
{
#ifndef RH_NO_GATE
    uint32_t n = (uint32_t)(k - RH_KEYS) + RH_KEY_LO;
    if (m == RH_V2 && n >= RH_V2_GATE_LO && n <= RH_V2_GATE_HI) return 0;
#endif
    return k->m[m].th != 0;
}

/* ---- run-time coefficient path (§5.6): (E, eps) from (sin, cos of theta/2, theta, d, cr), cents t, decay k.
 * In three parts (0.9.5 cpu-trim, the same integers): the decay's eps (d, k only), the tuning's sin and cr (the mode
 * and t only), E from both. rh_retune (the bend) computes the tuning part once a mode for its two decays and takes
 * eps from the voice (DECAY / RELEASE unchanged since its note-on). tests/py/test_rh_coef.py builds these four */
static __attribute__((noinline)) int32_t rh_coef_eps(int32_t d30, int32_t k16)
{
    int32_t a = (int32_t)(((int64_t)k16 * (k16 - 65536)) >> 16);          /* k(k-1), Q16 */
    int32_t c = (int32_t)(((int64_t)a * (k16 - 131072)) >> 16);           /* k(k-1)(k-2), Q16 */
    int32_t d2 = mul30(d30, d30), d3 = mul30(d2, d30);
    int64_t dp = (((int64_t)k16 * d30) >> 16) - ((((int64_t)a * d2) >> 16) >> 1)
               + (int32_t)(((int64_t)c * d3) >> 16) / 6;
    return dp < 1 ? 1 : (int32_t)dp;
}
static __attribute__((noinline)) int64_t rh_coef_tune(const rh_mode_t *m, int32_t t30, int32_t *spo)   /* cr; *spo */
{
    int32_t dl = mul30(m->th, t30);
    int32_t dl2 = mul30(dl, dl), dl3 = mul30(dl2, dl), dl4 = mul30(dl2, dl2), dl5 = mul30(dl4, dl);
    int32_t cdl = Q30 - (dl2 >> 1) + dl4 / 24, sdl = dl - dl3 / 6 + dl5 / 120;
    *spo = mul30(m->sh, cdl) + mul30(m->ch, sdl);
    return (int64_t)m->cr - 2 * (int64_t)mul30(m->cr, t30) + 3 * (int64_t)mul30(m->cr, mul30(t30, t30));
}
static __attribute__((noinline)) int32_t rh_coef_e(int32_t dpi, int32_t sp, int64_t cr)
{
    int32_t dp2 = mul30(dpi, dpi), dp3 = mul30(dp2, dpi);
    int32_t q = Q30 - (dpi >> 2) - 3 * (dp2 >> 5);
    int32_t om = (dpi >> 1) + (dp2 >> 3) + (dp3 >> 4);
    int64_t corr = Q30 + (((int64_t)mul30(om, om) * cr) >> 16);   /* cr, corr in int64: tuned down (TUNE < 0, a bend
                                                * down) the lowest modes' cr (A0 V1 ...) exceeds int32, and corr can
                                                * pass 2 (only off the firmware's settings: a bent, fast-muted bar);
                                                * where they fit, the same integers as int32 (tests/py/test_rh_coef.py) */
    int32_t t1 = (int32_t)(((int64_t)q * sp) >> 29);                      /* 2 q sin(theta'/2), Q30 */
    return (int32_t)(((int64_t)t1 * corr) >> 30);
}
static void rh_coef(const rh_mode_t *m, int32_t d30, int32_t t30, int32_t k16, int32_t *E, int32_t *eps)
{
    int32_t dpi = rh_coef_eps(d30, k16), sp;
    int64_t cr = rh_coef_tune(m, t30, &sp);
    *E = rh_coef_e(dpi, sp, cr);
    *eps = dpi;
}

/* ---- the note-on coefficient cache (0.9.5): every key's live modes' (E, eps) at the sustain and the release decay,
 * computed in the main loop (rh_cc_poll, each pass; the work only when TUNE / DECAY / RELEASE moved) by rh_coef itself,
 * so a note-on copies 4 words a mode instead of two rh_coef calls. Two buffers: the main loop fills the one the ISR
 * does not read, then publishes it (the ISR preempts the main loop, never the reverse). rh_init uses the published
 * one only when its stamp is the tuning and the decays of this note-on (no bend: the bend changes the tuning), else
 * rh_coef as before: the same integers either way (Gate R; tests/py/test_rh_cc.py). 2 x 88 x 5 x 16 B in the pool */
typedef struct { int32_t e0, eps0, e1, eps1; } rh_cc_t;
static rh_cc_t rh_cc[2][RH_NKEY][RH_NM] RH_OWN_SECTION(".pool.rh_cc");
static volatile int32_t rh_cc_st[2][3] RH_OWN_SECTION(".bss.rh_cc_st");   /* each buffer's TUNE t, DECAY k, RELEASE k */
static volatile uint32_t rh_cc_pub RH_OWN_SECTION(".bss.rh_cc_pub");      /* the published buffer + 1 (0 = none yet) */

static void rh_cc_poll(void)                    /* main loop */
{
    uint32_t p = rh_cc_pub, b = p == 1u ? 1u : 0u, n, m;
    int32_t t = rh_tune_t, kd = rh_k_decay, kr = rh_k_rel;
    if (p && rh_cc_st[p - 1u][0] == t && rh_cc_st[p - 1u][1] == kd && rh_cc_st[p - 1u][2] == kr) return;
    for (n = 0; n < RH_NKEY; n++)
        for (m = 0; m < RH_NM; m++) {
            const rh_mode_t *km = &RH_KEYS[n].m[m];
            rh_cc_t *c = &rh_cc[b][n][m];
            if (!rh_mode_runs(&RH_KEYS[n], m)) continue;
            rh_coef(km, km->d, t, kd, &c->e0, &c->eps0);
            rh_coef(km, km->drel, t, kr, &c->e1, &c->eps1);
        }
    rh_cc_st[b][0] = t; rh_cc_st[b][1] = kd; rh_cc_st[b][2] = kr;
    RING_PUBLISH();
    rh_cc_pub = b + 1u;
}

/* ---- the pickup table in RAM (the O10 probe, -DRH_PU_RAM; default off: the table is read from flash and the image
 * is the same as without this code). The voices index the interleaved Phi~ / slope table (rhodes_phim.h) at
 * data-dependent points, so on the FM-1 a read may miss the flash cache; with the flag rh_pu_boot (app_main, before
 * any note) copies each pickup family's table (S8: two, 16,392 B each) to the pool and the voices read the copies:
 * the same values, so the same sound */
#ifdef RH_PU_RAM
static int32_t rh_pu_phim[2][2 * (RH_PU_N + 1)] RH_OWN_SECTION(".pool.rh_pu_phim");
static void rh_pu_boot(void)
{
    uint32_t f, i;
    for (f = 0; f < 2u; f++) for (i = 0; i < 2u * (RH_PU_N + 1); i++) rh_pu_phim[f][i] = RH_PHIM[f][i];
}
#define RH_PU_PHIM(f) ((const int32_t *)rh_pu_phim[f])
#else
#define RH_PU_PHIM(f) RH_PHIM[f]
#endif

/* ---- voices (ISR) */
static void rh_load_pulse(rh_voice_t *v, uint32_t vel)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS), i = RH_VPOS_I[vel], f = RH_VPOS_F[vel];
    const uint16_t *pa = RH_PAMP[key];
    const uint16_t *pr = RH_PRATE[key];               /* (+ RH_PRATE_B[key]: the key's base) */
    const int16_t *p1 = RH_PULSE[key][RH_VLAY_I[vel]];
    int32_t w = RH_VLAY_W[vel];
    if (w) {        /* the layer crossfade, once per point (O2): the same integers the force loop computed per sample
                     * (a + ((a2 - a) w >> 15), between a and a2: an int16), so the force is byte-identical */
        const int16_t *p2 = RH_PULSE[key][RH_VLAY_I[vel] + 1u < RH_NLAYER ? RH_VLAY_I[vel] + 1u : RH_NLAYER - 1u];
        uint32_t j;
        for (j = 0; j < RH_PLEN + 2u; j++) {
            int32_t a = p1[j];
            v->pbuf[j] = (int16_t)(a + (((p2[j] - a) * w) >> 15));
        }
        v->ptab = v->pbuf;
    } else
        v->ptab = p1;
    v->pamp = (int32_t)pa[i] + ((((int32_t)pa[i + 1] - (int32_t)pa[i]) * (int32_t)f) >> 8);
    v->prate = (uint32_t)((int32_t)RH_PRATE_B[key] + pr[i] + ((((int32_t)pr[i + 1] - (int32_t)pr[i]) * (int32_t)f) >> 8)) << 4;  /* Q12 -> Q16 */
    {   /* HAMMER knob x TYPE tips scale the contact rate; the force scales with it so the impulse (the hammer's
         * momentum) is kept: a softer tip pushes longer and less hard, it does not give more energy */
        uint32_t h = (uint32_t)(((uint64_t)(uint32_t)rh_ham * (uint32_t)RH_TYPE[rh_type & 3u].ham) >> 16);
        v->prate = (uint32_t)(((uint64_t)v->prate * h) >> 16);
        v->pamp = (int32_t)(((int64_t)v->pamp * h) >> 16);
        if (v->pamp > 32767) v->pamp = 32767;                                 /* mul15s range */
    }
    v->ppos = 0;
}

static int32_t rh_hermite(const int32_t *T, int64_t s)
{
    uint32_t i, t;
    int32_t p0, p1, m0, m1, c2, c3, a;
    const int32_t *q;
    if (s < 0) s = 0;
    if ((uint64_t)s >= ((uint64_t)RH_S_MAX_HI << 44)) s = ((int64_t)RH_S_MAX_HI << 44) - 1;
    i = (uint32_t)(s >> 44);
    t = (uint32_t)(s >> 22) & 0x3FFFFFu;
    q = T + 2u * i;
    p0 = q[0]; m0 = q[1]; p1 = q[2]; m1 = q[3];
    c3 = 2 * (p0 - p1) + m0 + m1;
    c2 = 3 * (p1 - p0) - 2 * m0 - m1;
    a = c2 + (int32_t)(((int64_t)t * c3) >> 22);
    a = m0 + (int32_t)(((int64_t)t * a) >> 22);
    return p0 + (int32_t)(((int64_t)t * a) >> 22);
}

/* keeps x a plain 32-bit register value (no code): the compiler otherwise carries sign-extended 64-bit copies of a
 * multiplier (t, the gain, eps, b), which in the hot loops cost a register pair and stack traffic */
#ifdef DINGHY_HOST
#define RH_REG32(x) ((void)(x))                         /* the host / the browser: no hint (the same bits) */
#else
#include "fm1_keep.h"                                   /* hal/: the empty asm (tools/build.py: no asm in src/) */
#define RH_REG32(x) HAL_KEEP_REG(x)
#endif

/* rh_hermite of s = u * u, inlined into the pickup loops: the same arithmetic. s >= 0 always (|u| < 2^31: the square
 * fits), so the low clamp could never fire; for s >= 0, s >= RH_S_MAX_HI << 44 exactly when hi32(s) >= RH_S_MAX_HI << 12.
 * On the halves (hi, lo) of s: i = s >> 44 = hi >> 12, t = (s >> 22) & 0x3FFFFF = (hi & 0xFFF) << 10 | lo >> 22 (sh: as
 * two shifts, else as masks; each loop takes the form that compiles shorter in it), q = T + 2 i as a byte offset; the
 * clamp out of line (expected never): the same i and t, no clamp constants loaded every sample (rh_pickup_s 48 -> 43
 * instructions a sample, rh_pickup 81 -> 77 a two-sample turn) */
static inline __attribute__((always_inline)) int32_t rh_hermite_u(const int32_t *T, int32_t u, int sh)
{
    int64_t s = (int64_t)u * u;
    uint32_t hi = (uint32_t)(s >> 32), lo = (uint32_t)s, i;
    int32_t t, p0, p1, m0, m1, c2, c3, a, d;  /* t < 2^22: 32-bit, so the products below are 32 x 32 */
    const int32_t *q;
    RH_REG32(hi); RH_REG32(lo);
    if (__builtin_expect(hi >= (RH_S_MAX_HI << 12), 0)) { i = RH_S_MAX_HI - 1u; t = 0x3FFFFF; }  /* s >> 44, s >> 22 of the clamp */
    else if (sh) { uint32_t h = hi << 20; RH_REG32(h); i = hi >> 12; t = (int32_t)((h >> 10) | (lo >> 22)); }
    else { i = hi >> 12; t = (int32_t)(((lo >> 22) & 0x3FFu) | ((hi << 10) & 0x3FFC00u)); }
    RH_REG32(t); RH_REG32(i);
    q = (const int32_t *)((const char *)T + (i << 3));   /* Phi~, slope interleaved: p0, m0, p1, m1 adjacent */
    p0 = q[0]; m0 = q[1]; p1 = q[2]; m1 = q[3];
    d = p1 - p0;                              /* the same c3, c2 (|p| < 2^29, |m| < 2^23: no overflow) */
    c3 = m0 + m1 - 2 * d;                     /* = 2 (p0 - p1) + m0 + m1 */
    c2 = d - m0 - c3;                         /* = 3 (p1 - p0) - 2 m0 - m1 */
    a = c2 + (int32_t)(((int64_t)t * c3) >> 22);
    a = m0 + (int32_t)(((int64_t)t * a) >> 22);
    return p0 + (int32_t)(((int64_t)t * a) >> 22);
}

/* the plain pickup loop (no steal fade, no felt noise): rh_x through the flux table, the difference times the
 * per-key gain added into mix[0..SUB). Its own function: inlined into app_audio it kept the offset, the table
 * pointers, Hp, the gain and the mix address on the stack (58 instructions a sample, 1 store and 6 loads; now 43,
 * none). Returns the last H (the voice's Hprev) */
static FR_RAM __attribute__((noinline)) int32_t rh_pickup(const int32_t *T, int32_t *mix, int32_t off, int32_t Hp, int32_t gain)
{
    const int32_t *xp = rh_x, *xe = rh_x + SUB;
    RH_REG32(xe);
    do {                                                  /* (two samples a turn, as the mode loops) */
        int32_t H = rh_hermite_u(T, xp[0] + off, 1);
        RH_REG32(gain);
        mix[0] += (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
        Hp = rh_hermite_u(T, xp[1] + off, 1);
        RH_REG32(gain);
        mix[1] += (int32_t)(((int64_t)(Hp - H) * gain) >> 24);
        xp += 2; mix += 2;
    } while (xp < xe);
    return Hp;
}

/* rh_pickup with a stereo position (SPREAD): the same into mix, and pan (Q8) times it into side */
static FR_RAM __attribute__((noinline)) int32_t rh_pickup_s(const int32_t *T, int32_t *mix, int32_t *side, int32_t off,
                                                     int32_t Hp, int32_t gain, int32_t pan)
{
    const int32_t *xp = rh_x, *xe = rh_x + SUB;
    RH_REG32(xe);
    do {
        int32_t H = rh_hermite_u(T, *xp++ + off, 0), o;
        RH_REG32(gain);
        o = (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
        *mix++ += o;
        *side++ += (o * pan) >> 8;
        Hp = H;
    } while (xp < xe);
    return Hp;
}

/* TUNE and the bend: (1 + t)(1 + b) - 1 (exactly rh_tune_t at no bend: the product is 0) */
static int32_t rh_tune_now(void)
{
    int32_t t = rh_tune_t, b = rh_bend_t;
    return t + b + (int32_t)(((int64_t)t * b) >> 30);
}

/* TONE above its middle moves the pickup offset toward the pole axis: the fundamental's slope falls and the note
 * gets quieter (up to ~10 dB at 127). The voice gain gives that back at the note-on, from the loudness against the
 * offset itself (RH_TONE_A, measured on the host: tools/tone_level.py), so every key gets its own (the per-key offset
 * jitter, the cap at RH_OFF_MAX): the level at the offset TONE 64 gives the key over the level at the one it plays.
 * Two curves, soft (v40) and hard (v127), blended by the square of the velocity (the loss eases off near the top).
 * TONE <= 64: not called (byte-identical); per pickup family (S8: its own flux table and offsets) */
#ifndef RH_NO_TONE_G
static int32_t rh_tone_a(const uint16_t *A, int32_t o)            /* the curve at offset o (Q25 gap), Q15 */
{
    uint32_t u, i;
    int32_t f;
    if (o < RH_TG_O_LO) o = RH_TG_O_LO;
    u = (uint32_t)(o - RH_TG_O_LO);
    i = u >> RH_TG_SH; f = (int32_t)((u >> (RH_TG_SH - 15)) & 0x7FFFu);
    if (i >= RH_TG_N - 1u) return A[RH_TG_N - 1u];
    return A[i] + ((((int32_t)A[i + 1] - (int32_t)A[i]) * f) >> 15);
}
static int32_t __attribute__((noinline)) rh_tone_gain(int32_t gain, int32_t o64, int32_t o, uint32_t vel, uint32_t fam)
{   /* (o64, o: capped by the caller, at the family's own cap) */
    const uint16_t *As = RH_TONE_A[fam & 1u][rh_type & 3u][0], *Ah = RH_TONE_A[fam & 1u][rh_type & 3u][1];
    int32_t w = vel <= 40u ? 0 : vel >= 127u ? 87 : ((int32_t)vel - 40) * ((int32_t)vel - 40) / 87, a64, a, s, h;
    uint32_t g;
    s = rh_tone_a(As, o64); h = rh_tone_a(Ah, o64); a64 = s + (h - s) * w / 87;
    s = rh_tone_a(As, o); h = rh_tone_a(Ah, o); a = s + (h - s) * w / 87;
    if (a < 1) a = 1;
    g = ((uint32_t)a64 << 14) / (uint32_t)a;                       /* Q14; a64 < 2^15: no overflow */
    return (int32_t)(((int64_t)gain * g) >> 14);
}
#endif

/* S4 (0.9.5): amplitude -> pitch in the bass. A struck tine's stiffness grows with its swing, so a hard-struck low note
 * starts a little sharp and settles as it rings down (spec S11). Per 32-sample sub-block V1's E is scaled by
 * (1 + kap a^2), a^2 = x^2 + y^2 its MC3 state (the coupled form's quadrature: a^2 almost constant over a cycle), so
 * the pitch offset follows the squared amplitude: ~6 cents at v127 on keys 21..36, down to 0 at key 48 (none
 * above), ~1.6 c at v100, ~0.2 c at v40. RH_KAP: (2^(C/1200) - 1) / a127^2 in Q16, C = 6 cents x min(1, (48 - key) / 12),
 * a127 = V1's peak amplitude (gap) at v127 on STAGE with BARK 64 (host, 8d913af + rh-noteon: 0.084 gap at key 21 ..
 * 0.193 at 47). BARK and the TYPE's bark scale the strike's force and so the swing: kap is divided by their square
 * (the offset stays the velocity's, BARK does not detune). The tuning a guess to set by ear. */
#define RH_KAP_HI 48u
static const uint16_t RH_KAP[RH_KAP_HI - RH_KEY_LO] = {
    32477, 31715, 31865, 29582, 29786, 29854, 28789, 26940, 25860, 24895, 23983, 24132, 23168, 21530,
    20598, 19799, 16925, 14897, 12589, 10651, 8082, 6021, 4365, 3046, 1996, 1156, 508};
static void rh_init(rh_voice_t *v, uint32_t note, uint32_t vel)
{
    const rh_type_t *ty = &RH_TYPE[rh_type];
    uint32_t m, n = note < RH_KEY_LO ? RH_KEY_LO : note > RH_KEY_LO + RH_NKEY - 1u ? RH_KEY_LO + RH_NKEY - 1u : note;
    const rh_key_t *k = &RH_KEYS[n - RH_KEY_LO];
    int32_t u, t = rh_tune_now();
    uint32_t cp = rh_cc_pub;                    /* the cache, if it holds this note-on's coefficients */
    const rh_cc_t *cc = cp && rh_cc_st[cp - 1u][0] == t && rh_cc_st[cp - 1u][1] == rh_k_decay
                        && rh_cc_st[cp - 1u][2] == rh_k_rel ? rh_cc[cp - 1u][n - RH_KEY_LO] : 0;
    v->k = k; v->kd = rh_k_decay; v->kr = rh_k_rel;
    for (m = 0; m < RH_NM; m++) {
        rh_ms_t *s = &v->m[m];
        const rh_mode_t *km = &k->m[m];
        s->x = s->y = s->ex = s->ey = 0;
        s->peak = 0;
        s->live = (uint8_t)rh_mode_runs(k, m);
        if (!s->live) { s->e = s->eps = 0; continue; }
        if (cc) { s->e0 = cc[m].e0; s->eps0 = cc[m].eps0; s->e1 = cc[m].e1; s->eps1 = cc[m].eps1; }
        else {
            rh_coef(km, km->d, t, rh_k_decay, &s->e0, &s->eps0);
            rh_coef(km, km->drel, t, rh_k_rel, &s->e1, &s->eps1);
        }
        s->e = s->es = s->e0; s->eps = s->epss = s->eps0;
        {   /* force gain: BARK scales the tip (the pickup then barks), BELL the overtones; saturating */
            int64_t b = ((((int64_t)km->b * rh_bark_s) >> 16) * ty->bark) >> 16;
            if (m == RH_O1 || m == RH_O2) b = (((b * ty->bell) >> 16) * rh_bell) >> 16;
            s->b = b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
        }
    }
    v->note = (uint8_t)note; v->vel = (uint8_t)vel; v->lvl = v->lvl0 = 0;
    {   /* SPREAD: the key's place on the 88-key harp, A0 hard left .. C8 hard right at 127 (0: none, the mono sum) */
        int32_t p = rh_spread ? ((2 * (int32_t)note - 129) * rh_spread * 256) / (87 * 127) : 0;
        v->pan = p > 256 ? 256 : p < -256 ? -256 : p;
    }
    {   /* TYPE, TONE and the cap are distances: in the 1/16" family's own gap (twice the 1/32") half as many (S8) */
        uint32_t sh = RH_FAM_SH[k->fam];
        int32_t mx = RH_OFF_MAX >> sh;
        v->off = k->off + ((ty->off_delta + rh_tone_off) >> sh);
        if (v->off > mx) v->off = mx;                  /* never onto the pole axis (the fundamental cancels there) */
        v->gain = mul16(mul16(mul16(k->gain, rh_bark_g), ty->bark_g), RH_TYPE_FG[rh_type][k->fam]);
#ifndef RH_NO_TONE_G
        if (rh_tone_off > 0) {                         /* TONE above 64: its loudness back (the offsets in the family's
                                                        * own gap, the family's own curve: S8) */
            int32_t o64 = k->off + (ty->off_delta >> sh);
            v->gain = rh_tone_gain(v->gain, o64 > mx ? mx : o64, v->off, vel, k->fam);
        }
#endif
    }
    u = v->off;
    v->Hprev = rh_hermite(RH_PU_PHIM(k->fam), (int64_t)u * u);
    v->alive = 1; v->held = 1; v->ramp = 0; v->w_tgt = 0;
    v->fade = 32768; v->pend = 0; v->felt_n = 0; v->felt_lp = 0;
    v->age = ++rh_ages;
    v->life = k->life;
    v->m[RH_V1].kap = 0;
#ifndef RH_NO_S4                                          /* -DRH_NO_S4: without it (the A/B reference) */
    if (n < RH_KAP_HI) {                                  /* S4: kap / (BARK x the TYPE's bark)^2 */
        uint32_t g = (uint32_t)(((uint64_t)(uint32_t)rh_bark_s * (uint32_t)ty->bark) >> 16), ig;
        if (g < 4096u) g = 4096u;
        ig = 0x80000000u / g;                             /* 1 / g, Q15 (g Q16) */
        v->m[RH_V1].kap = (int32_t)(((uint64_t)RH_KAP[n - RH_KEY_LO] * ig * ig) >> 30);
        v->m[RH_V1].kap <<= 2u * RH_FAM_SH[k->fam];      /* S8: the 1/16" family's x is in its own (double) gap, so
                                                           * the same swing is half the x: kap x 4 keeps the offset */
    }
#endif
    rh_load_pulse(v, vel);
}

/* the damper crossfade over len sub-blocks (3..16): step i of len is i * RH_RSTEP[len] (Q15; len 7 = 4681, as before) */
#define RH_RLEN_MIN 3u
#define RH_RLEN_MAX 16u
static const uint16_t RH_RSTEP[RH_RLEN_MAX + 1u] = {0, 0, 16384, 10923, 8192, 6554, 5461, 4681, 4096, 3641, 3277, 2979,
                                                     2731, 2521, 2341, 2185, 2048};
static void rh_set_damper_n(rh_voice_t *v, int32_t w, uint32_t len)
{
    uint32_t m;
    if (w == v->w_tgt) return;
    for (m = 0; m < RH_NM; m++) { v->m[m].es = v->m[m].e; v->m[m].epss = v->m[m].eps; }
    v->w_tgt = w;
    v->ramp = (uint8_t)len; v->rlen = (uint8_t)len;
}
/* the damper settle per register (S6): the felt takes longer to seat on a heavy bass tine than on a short treble one.
 * RAMP_SUB (7 sub-blocks, 5.1 ms) on C3..B4 (48..71), one more every 4 keys below C3 to 10 (7.3 ms) from D2 (38)
 * down, 6 on C5..B5, 5 (3.6 ms) from C6 (84) up */
static uint32_t rh_settle(uint32_t note)
{
    if (note < 48u) { uint32_t d = (50u - note) >> 2; return RAMP_SUB + (d > 3u ? 3u : d); }
    return note < 72u ? RAMP_SUB : note < 84u ? RAMP_SUB - 1u : RAMP_SUB - 2u;
}
static void rh_set_damper(rh_voice_t *v, int32_t w) { rh_set_damper_n(v, w, rh_settle(v->note)); }
/* the release velocity (S5): rv 1..127 (0 = none = 64, the neutral: exactly the release without it). A quick lift
 * lands the felt faster (the crossfade x0.6 at 127: 7 -> 4 sub-blocks), harder (the damper weight x1.4: the (E, eps)
 * target extrapolated past the release pair, about RELEASE / 1.4) and with a louder knock (the thump x1.4); a slow one
 * lets it settle (x1.4 at 1: 7 -> 10 sub-blocks, the weight and the knock x0.6). Linear in rv on both sides of 64. */
static uint32_t rh_rel_len(uint32_t base, uint32_t rv)
{
    int32_t m = 256 + (64 - (int32_t)(rv ? rv : 64u)) * 102 / 63;          /* Q8: 1.4 (rv 1) .. 1 (64) .. 0.6 (127) */
    uint32_t len = ((uint32_t)base * (uint32_t)m + 128u) >> 8;
    return len < RH_RLEN_MIN ? RH_RLEN_MIN : len > RH_RLEN_MAX ? RH_RLEN_MAX : len;
}
static int32_t rh_rel_gain(uint32_t rv)                                      /* Q15: 0.6 (rv 1) .. 1 (64) .. 1.4 (127) */
{
    return 32768 + ((int32_t)(rv ? rv : 64u) - 64) * 13107 / 63;
}
static int32_t rh_left(const rh_voice_t *v)     /* the note's level now in strike-force units (Q15): the strike's
                                                * force (its velocity law) x the level left. 32-bit, no 64-bit divide */
{
    int32_t f = v->pamp, l = v->lvl, l0 = v->lvl0;
    if (l0 <= 0 || l >= l0) return f;
    if (l < 0) l = 0;
    while (l0 > 0x7FFF) { l0 >>= 1; l >>= 1; }
    return f * l / l0;                          /* f, l < 2^15 */
}
static int32_t rh_rel_w(const rh_voice_t *v) { return (int32_t)(((uint32_t)rh_pedal_w * v->rgain) >> 15); }   /* key up */

/* a strike's knock (S11): the pulse, its rate and force as rh_load_pulse gives the voice (the nearer of the two
 * velocity layers: the knock is far under the note, the layer crossfade is not worth a per-sample cost) */
static void __attribute__((noinline)) rh_knock_strike(uint32_t note, uint32_t vel)
{
    uint32_t n, key, i, f, l, h, prate;
    int32_t pamp, a;
    if (!rh_knock || !RH_KNOCK_ON[rh_type & 3u]) return;
    if (rh_kn_tab && rh_kn_pos) return;                 /* a knock from an earlier block still sounds */
    n = note < RH_KEY_LO ? RH_KEY_LO : note > RH_KEY_LO + RH_NKEY - 1u ? RH_KEY_LO + RH_NKEY - 1u : note;
    key = n - RH_KEY_LO; i = RH_VPOS_I[vel]; f = RH_VPOS_F[vel];
    pamp = (int32_t)RH_PAMP[key][i] + ((((int32_t)RH_PAMP[key][i + 1] - (int32_t)RH_PAMP[key][i]) * (int32_t)f) >> 8);
    prate = (uint32_t)((int32_t)RH_PRATE_B[key] + RH_PRATE[key][i] + ((((int32_t)RH_PRATE[key][i + 1] - (int32_t)RH_PRATE[key][i])
                                                       * (int32_t)f) >> 8)) << 4;
    h = (uint32_t)(((uint64_t)(uint32_t)rh_ham * (uint32_t)RH_TYPE[rh_type & 3u].ham) >> 16);
    prate = (uint32_t)(((uint64_t)prate * h) >> 16);
    pamp = (int32_t)(((int64_t)pamp * h) >> 16);
    if (pamp > 32767) pamp = 32767;
    a = (int32_t)(((int64_t)pamp * rh_knock) >> 10);   /* <= 32767 x 653 >> 10 = 20896 */
    l = RH_VLAY_I[vel];
    if (RH_VLAY_W[vel] >= 16384 && l + 1u < RH_NLAYER) l++;
    if (rh_kn_tab) {                                    /* another strike of this block: one knock, the forces add */
        a += rh_kn_amp;
        if (a > 65535) a = 65535;                       /* x the pulse (<= 32767): still under 2^31 */
        rh_kn_amp = a;
        if (n >= rh_kn_note) return;                    /* the lowest key's pulse (the longest, the heaviest hammer) */
    }
    rh_kn_amp = a;
    rh_kn_tab = RH_PULSE[key][l]; rh_kn_rate = prate; rh_kn_pos = 0; rh_kn_note = (uint8_t)n;
}

static void rh_start_pend(rh_voice_t *v)                /* a stolen voice is gone: start the note waiting for it */
{
    uint32_t rel = v->pend;                             /* 1 held, 2 let go, 3 let go by an all-off */
    rh_init(v, v->pend_note, v->pend_vel);
    if (rel >= 2u) { v->held = 0; v->rgain = 32768; rh_set_damper(v, rel == 3u ? 32768 : rh_pedal_w); }
    if (rel == 3u && v->life > RH_OFF_LIFE) v->life = RH_OFF_LIFE;
}

static int rh_strike(uint32_t note, uint32_t vel)      /* 0: every voice already waits for a note; try next block */
{
    uint32_t i, pick = RH_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < RH_NV && rv[i].pend; i++) ;
    if (i == RH_NV) return 0;                           /* (more than RH_NV steals in one block: BUGHUNT #21) */
    rh_knock_strike(note, vel);
    for (i = 0; i < RH_NV; i++)                         /* the same note still sounding: re-strike, superpose */
        if (rv[i].alive && !rv[i].pend && rv[i].note == note && rv[i].fade == 32768) {
            rh_voice_t *v = &rv[i];
            uint32_t m;
            for (m = 0; m < RH_NM; m++) if (rh_mode_runs(v->k, m)) v->m[m].live = 1;
            v->vel = (uint8_t)vel; v->held = 1; v->age = ++rh_ages; v->life = v->k->life; v->lvl0 = v->lvl;
            rh_set_damper(v, 0);
            rh_load_pulse(v, vel);
            return 1;
        }
    for (i = 0; i < RH_NV; i++)
        if (!rv[i].alive) { rh_init(&rv[i], note, vel); return 1; }
    for (i = 0; i < RH_NV; i++) {                       /* steal: the quietest released voice, else the oldest */
        uint32_t m, sum = 0, score;
        if (rv[i].pend) continue;
        for (m = 0; m < RH_NM; m++) sum += (uint32_t)rv[i].m[m].peak >> 4;
        score = rv[i].held ? 0x80000000u + (rv[i].age & 0x7FFFFFFFu) : (sum > 0x7FFFFFFFu ? 0x7FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
#ifdef DINGHY_HOST
    rh_h_steals++;
#endif
    rv[pick].pend = 1; rv[pick].pend_note = (uint8_t)note; rv[pick].pend_vel = (uint8_t)vel;
    if (rv[pick].fade == 32768) rv[pick].fade = 32767;
    return 1;
}

static void rh_events(void)
{
    while (ev_r != ev_w) {
        uint32_t e = ev_q[ev_r & 31u], type = e >> 28, note = e >> 16 & 127u, val = e & 0xFFFFu, i;
        if (type == EV_NOTE_ON && !rh_strike(note, val)) break;   /* (kept: it starts next block, in order) */
        ev_r = ev_r + 1u;
        if (type == EV_NOTE_ON) continue;
        if (type == EV_NOTE_OFF) {
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive && rv[i].note == note && rv[i].held) {
                    rh_voice_t *v = &rv[i];
                    v->held = 0;
                    v->rgain = (uint16_t)rh_rel_gain(val);    /* val: the release velocity */
                    rh_set_damper_n(v, rh_rel_w(v), rh_rel_len(rh_settle(note), val));
                    if (rh_pedal_w && rh_thump && !v->ptab) {  /* the felt lands on the tine: a soft knock */
                        uint32_t key = (uint32_t)(v->k - RH_KEYS);
                        int32_t th = rh_thump > 32767 ? 32767 : rh_thump;
                        int32_t cap = (int32_t)(((uint32_t)rh_left(v) * RH_THUMP_CAP) >> 15);
                        th = mul15(th > cap ? cap : th, v->rgain);   /* the cap, then the release velocity's x0.6..1.4 */
                        v->ptab = RH_PULSE[key][0];
                        v->pamp = th > 32767 ? 32767 : th;
                        v->prate = (uint32_t)(RH_PRATE_B[key] + RH_PRATE[key][1]) << 4;
                        v->ppos = 0;
                    }
                    if (rh_pedal_w && rh_felt) {             /* 8 ms of felt noise */
                        int32_t g = rh_left(v) * (32768 / RH_FELT_FULL);
                        v->felt_n = 353; v->felt_g = (uint16_t)(g > 32768 ? 32768 : g);
                    }
                }
            for (i = 0; i < RH_NV; i++)                 /* a note let go while it waits for a steal's fade: it
                                                         * starts let go (it was held for good: fuzz seed 160, a fast
                                                         * pattern over 8 voices; the same fix as bars.c) */
                if (rv[i].alive && rv[i].pend == 1u && rv[i].pend_note == note) rv[i].pend = 2;
        } else if (type == EV_PEDAL) {
            rh_pedal_w = PEDAL_W[val];
            if ((uint8_t)(rh_pedal_w == 0) != rh_ped_dn) {   /* down or up: the pedal's thud (S12) */
                rh_ped_dn = (uint8_t)(rh_pedal_w == 0);
                if (rh_pnz) {                           /* (over sounding notes: no louder than the loudest, as
                                                         * the felt noise; into silence: NOISE's own) */
                    int32_t lm = -1, g;
                    for (i = 0; i < RH_NV; i++)
                        if (rv[i].alive) { g = rh_left(&rv[i]); if (g > lm) lm = g; }
                    g = lm < 0 ? 32768 : lm >> 2;
                    rh_pn_n = RH_PN_LEN; rh_pn_env = g >= 32768 ? rh_pnz : (int32_t)(((uint32_t)rh_pnz * (uint32_t)g) >> 15);
                }
            }
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive && !rv[i].held) rh_set_damper(&rv[i], rh_rel_w(&rv[i]));
        } else if (type == EV_ALL_OFF) {
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive) {
                    rv[i].held = 0; rv[i].rgain = 32768; rh_set_damper(&rv[i], 32768);
                    if (rv[i].life > RH_OFF_LIFE) rv[i].life = RH_OFF_LIFE;   /* the tone-bar mode (V2) of a low
                                                         * key barely feels the felt (T60 ~50 s, x3.9 at RELEASE 127)
                                                         * and kept a 1-4 LSB hum and the voice for ~40 s (fuzz 628) */
                    if (rv[i].pend) rv[i].pend = 3;     /* a note waiting for a steal's fade starts let go and
                                                         * damped, as the struck ones (it was held for good: fuzz
                                                         * seed 513, a strummed chord on 9 busy voices, then CC 120) */
                }
        }
    }
}

/* one live mode, one sub-block (MC3 + error feedback, mode-outer) into rh_x: written by the first live mode, added
 * by the others (O9: no zero pass); with the hammer force rh_F when the sub-block started with a pulse. Four
 * noinline copies (force / none x first / add): each loop has the whole register file, whatever the rest of
 * rh_voice_sub needs. A state and its error-feedback remainder (0 <= ex < 2^30) as one int64 X = x 2^30 + ex: the
 * MC3 step x += (e y + ex) >> 30, ex = (e y + ex) & (2^30 - 1) is then X += e y, x = X >> 30 (the same integers; |X|
 * < 2^61, no overflow; the int32 x the low word of X >> 30, as the 32-bit adds wrapped), no mask or sign rebuild in
 * the loop; the remainders stored back at the end. The force loop keeps y and ey apart (y moves by the force too) and
 * adds a zero force without a test (0 >> 20 = 0); RH_REG32 keeps b, e, eps and the loop end 32-bit registers.
 * 24 / 26 -> 20 / 21 instructions a sample (force), 19 / 20 -> 13 / 14 (none). Returns the peak >> 3 */
static inline __attribute__((always_inline)) int32_t rh_mode_f_t(rh_ms_t *s, int first)
{
    int32_t x = s->x, y = s->y, e = s->e, eps = s->eps, b = s->b, pk = 0;
    int64_t X = ((int64_t)x << 30) + (uint32_t)s->ex;
    uint32_t ey = (uint32_t)s->ey;
    int32_t *xp = rh_x, *xe = rh_x + SUB; const int32_t *fp = rh_F;
    RH_REG32(xe);
    do {
        int64_t t;
        RH_REG32(b); y += (int32_t)(((int64_t)*fp * b) >> 20);
        RH_REG32(e);
        X += (int64_t)e * y;
        x = (int32_t)(X >> 30);
        RH_REG32(eps);
        t = ey - (int64_t)eps * y - (int64_t)e * x;
        y += (int32_t)(t >> 30); ey = (uint32_t)t & 0x3FFFFFFFu;
        if (first) *xp = x; else *xp += x;
        if (iabs(x) > pk) pk = iabs(x);
        if (iabs(y) > pk) pk = iabs(y);
        xp++; fp++;
    } while (xp < xe);
    s->x = sat28i(x); s->y = sat28i(y); s->ex = (int32_t)((uint32_t)X & 0x3FFFFFFFu); s->ey = (int32_t)ey; s->peak = pk;
    return pk >> 3;
}

/* the same without the force (no pulse at the sub-block's start: every rh_F[j] would be 0, the add never ran); x and
 * y both as X, Y (Y -= eps y + e x); the peak as the max of the two magnitudes, then of the peak (the same value, one
 * temporary fewer: no spill) */
static inline __attribute__((always_inline)) int32_t rh_mode_p_t(rh_ms_t *s, int first)
{
    int32_t *xp = rh_x, *xe = rh_x + SUB;
    int32_t x = s->x, y = s->y, e = s->e, eps = s->eps, pk = 0;
    int64_t X = ((int64_t)x << 30) + (uint32_t)s->ex, Y = ((int64_t)y << 30) + (uint32_t)s->ey;
    RH_REG32(xe);
    do {
        RH_REG32(e);
        X += (int64_t)e * y;
        x = (int32_t)(X >> 30);
        RH_REG32(eps);
        Y -= (int64_t)eps * y + (int64_t)e * x;
        y = (int32_t)(Y >> 30);
        if (first) *xp = x; else *xp += x;
        xp++;
        { int32_t ax = iabs(x), ay = iabs(y); ax = ax > ay ? ax : ay; pk = ax > pk ? ax : pk; }
    } while (xp < xe);
    s->x = sat28i(x); s->y = sat28i(y); s->ex = (int32_t)((uint32_t)X & 0x3FFFFFFFu); s->ey = (int32_t)((uint32_t)Y & 0x3FFFFFFFu); s->peak = pk;
    return pk >> 3;
}

/* S4: this sub-block's E for V1, (1 + kap a^2) E, a^2 = x^2 + y^2 in Q30 gap^2 (held at 1 gap^2: the products fit
 * int64); s->e itself stays (the damper ramp, the retune and rh_set_damper own it). Out of line: the mode loops'
 * registers stay as they were */
static inline int32_t rh_s4_e(const rh_ms_t *s)
{
    int64_t a2 = ((int64_t)s->x * s->x + (int64_t)s->y * s->y) >> 20, f;
    if (a2 > Q30) a2 = Q30;
    f = (a2 * s->kap) >> 16;                              /* the fraction, Q30: at most 1/16 (a guard: ~6 c here) */
    if (f > (Q30 >> 4)) f = Q30 >> 4;
    return s->e + (int32_t)(((int64_t)s->e * f) >> 30);
}
static FR_RAM int32_t __attribute__((noinline)) rh_mode_f1_l(rh_ms_t *s) { return rh_mode_f_t(s, 1); }
static FR_RAM int32_t __attribute__((noinline)) rh_mode_f(rh_ms_t *s) { return rh_mode_f_t(s, 0); }
static FR_RAM int32_t __attribute__((noinline)) rh_mode_p1_l(rh_ms_t *s) { return rh_mode_p_t(s, 1); }
/* the first live mode (V1, the only one with kap): its loop runs on the S4 E of this sub-block, s->e put back after
 * it; the loops stay their own functions, reading s->e as without S4 (their registers unchanged) */
static int32_t __attribute__((noinline)) rh_mode_f1(rh_ms_t *s)
{
    int32_t e = s->e, pk;
    if (!s->kap) return FR_FAR(rh_mode_f1_l)(s);
    s->e = rh_s4_e(s); pk = FR_FAR(rh_mode_f1_l)(s); s->e = e;
    return pk;
}
static int32_t __attribute__((noinline)) rh_mode_p1(rh_ms_t *s)
{
    int32_t e = s->e, pk;
    if (!s->kap) return FR_FAR(rh_mode_p1_l)(s);
    s->e = rh_s4_e(s); pk = FR_FAR(rh_mode_p1_l)(s); s->e = e;
    return pk;
}
static FR_RAM int32_t __attribute__((noinline)) rh_mode_p(rh_ms_t *s) { return rh_mode_p_t(s, 0); }

/* the pickup loop with the steal fade (the plain one's arithmetic, then the fade per sample); its own function like
 * rh_pickup, so neither moves rh_voice_sub's mode-loop registers (the felt noise is a pass of its own, O6) */
static FR_RAM __attribute__((noinline)) int32_t rh_pickup_fx(rh_voice_t *v, const int32_t *T, int32_t *mix, int32_t off,
                                                      int32_t Hp, int32_t gain)
{
    uint32_t j;
    for (j = 0; j < SUB; j++) {                           /* the same, with the fade */
        int32_t H = rh_hermite_u(T, rh_x[j] + off, 1);
        int32_t o = (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
        Hp = H;
        if (v->fade != 32768) {
            o = (int32_t)(((int64_t)o * v->fade) >> 15);
            v->fade -= (int32_t)(32768 / FADE_SAMPLES);
            if (v->fade < 0) v->fade = 0;
        }
        mix[j] += o;
    }
    return Hp;
}

/* one voice, one 32-frame sub-block, added into mix[0..SUB) */
static __attribute__((noinline)) void rh_voice_sub(rh_voice_t *v, int32_t *mix, int32_t *side)
{
    const rh_key_t *k = v->k;
    uint32_t j, m;
    int32_t sum = 0, Hp = v->Hprev, off = v->off, gain = v->gain, fnz = 0;
    if (v->ramp) {                                        /* damper crossfade: E and eps together */
        int32_t f;
        v->ramp--;
        f = (int32_t)(v->rlen - v->ramp) * RH_RSTEP[v->rlen];   /* Q15 of i/len */
        if (f > 32768) f = 32768;
        if (!v->ramp) f = 32768;
        for (m = 0; m < RH_NM; m++) {
            rh_ms_t *s = &v->m[m];
            int32_t et, pt;
            if (!s->live) continue;
            et = s->e0 + (int32_t)(((int64_t)(s->e1 - s->e0) * v->w_tgt) >> 15);
            pt = s->eps0 + (int32_t)(((int64_t)(s->eps1 - s->eps0) * v->w_tgt) >> 15);
            s->e = s->es + (int32_t)(((int64_t)(et - s->es) * f) >> 15);
            s->eps = s->epss + (int32_t)(((int64_t)(pt - s->epss) * f) >> 15);
        }
    }
    if (v->ptab) {                                        /* hammer force for the sub-block: the fields in locals
                                                           * (only this loop touches them); 32-bit products, exact:
                                                           * pulses 0..32767, f < 2^15, so every |product| < 2^31 */
        const int16_t *pt = v->ptab;
        int32_t pamp = v->pamp;
        uint32_t ppos = v->ppos, prate = v->prate;
        fnz = 1;
        j = 0;                                            /* the pulse until it ends or the sub-block does, then
                                                           * zeros: the same samples as one loop testing pt per
                                                           * sample. The velocity layers are pre-mixed at the strike
                                                           * (rh_load_pulse, O2): one table, no per-sample blend */
        for (;;) {
            uint32_t i = ppos >> 16;
            int32_t f = (int32_t)((ppos >> 1) & 0x7FFFu);
            int32_t a = pt[i], b = pt[i + 1];
            rh_F[j++] = mul15s(a + (((b - a) * f) >> 15), pamp);
            ppos += prate;
            if ((ppos >> 16) >= RH_PLEN) { pt = 0; break; }
            if (j == SUB) break;
        }
        for (; j < SUB; j++) rh_F[j] = 0;
        v->ppos = ppos; v->ptab = pt;
    }                                                     /* (no force: rh_F is not read, fnz = 0) */
    for (m = 0; m < RH_NM && !v->m[m].live; m++) { }      /* the first live mode writes rh_x, the others add (O9: no
                                                           * zero pass); no live mode at all: zeros */
    if (m == RH_NM)
        for (j = 0; j < SUB; j++) rh_x[j] = 0;
    else {
        sum += fnz ? rh_mode_f1(&v->m[m]) : rh_mode_p1(&v->m[m]);
        for (m++; m < RH_NM; m++)
            if (v->m[m].live) sum += fnz ? FR_FAR(rh_mode_f)(&v->m[m]) : FR_FAR(rh_mode_p)(&v->m[m]);
    }
    if (v->fade == 32768) {                               /* pickup, then the per-key gain into the mix: no steal fade
                                                           * (only this loop changes it in a render: the test below would
                                                           * never fire); with a stereo position (SPREAD) also into side */
        if (!v->pan) Hp = FR_FAR(rh_pickup)(RH_PU_PHIM(k->fam), mix, off, Hp, gain);
        else Hp = FR_FAR(rh_pickup_s)(RH_PU_PHIM(k->fam), mix, side, off, Hp, gain, v->pan);
    } else if (!v->pan)
        Hp = FR_FAR(rh_pickup_fx)(v, RH_PU_PHIM(k->fam), mix, off, Hp, gain);
    else {                                                /* (a steal fade with a position: rare) */
        int32_t tmp[SUB], p = v->pan;
        for (j = 0; j < SUB; j++) tmp[j] = 0;
        Hp = FR_FAR(rh_pickup_fx)(v, RH_PU_PHIM(k->fam), tmp, off, Hp, gain);
        for (j = 0; j < SUB; j++) { mix[j] += tmp[j]; side[j] += (tmp[j] * p) >> 8; }
    }
    if (v->felt_n) {                                      /* felt noise after a key-up: LFSR, one-pole ~2 kHz, added in
                                                           * a pass of its own (O6; it was a branch in the fade loop):
                                                           * the same integers (the sums reassociate exactly) and the
                                                           * LFSR consumed in the same voice / sample order */
        uint32_t nf = v->felt_n < SUB ? v->felt_n : SUB, lfsr = rh_lfsr;
        int32_t lp = v->felt_lp, fl = (int32_t)(((uint32_t)rh_felt * v->felt_g) >> 15);           /* |lp| <= 2^15, fl < 2^16: the product fits an int32 */
        if (!v->pan)
            for (j = 0; j < nf; j++) {
                int32_t nz;
                lfsr = (lfsr >> 1) ^ (-(lfsr & 1u) & 0xB400u);
                nz = (int32_t)(lfsr & 0xFFFFu) - 32768;
                lp += (nz - lp) >> 3;
                mix[j] += (lp * fl) >> 10;                /* Q15 * Q15 >> 10 = Q20 */
            }
        else {                                            /* (SPREAD: the felt where its key is) */
            int32_t p = v->pan;
            for (j = 0; j < nf; j++) {
                int32_t nz;
                lfsr = (lfsr >> 1) ^ (-(lfsr & 1u) & 0xB400u);
                nz = (int32_t)(lfsr & 0xFFFFu) - 32768;
                lp += (nz - lp) >> 3;
                nz = (lp * fl) >> 10;
                mix[j] += nz; side[j] += (nz * p) >> 8;
            }
        }
        rh_lfsr = lfsr; v->felt_lp = lp; v->felt_n = (uint16_t)(v->felt_n - nf);
    }
    v->Hprev = Hp;
    if (v->fade == 0) {                                   /* a steal finished fading: start the waiting note */
        v->alive = 0;
        if (v->pend) rh_start_pend(v);
        return;
    }
    v->lvl = sum; if (sum > v->lvl0) v->lvl0 = sum;
    if (!v->ptab && !v->felt_n && sum < (k->kill >> 3)) {   /* died away; a stolen voice dying in its fade still
                                                           * starts the note waiting for it (it ended, and the note was
                                                           * lost: BUGHUNT #21) */
        v->alive = 0;
        if (v->pend) rh_start_pend(v);
        return;
    }
    for (m = 1; m < RH_NM; m++)                           /* sleep overtone modes that have died away */
        if (v->m[m].live && !v->ptab && v->m[m].peak < (k->kill >> 3)) {
            v->m[m].live = 0; v->m[m].x = v->m[m].y = v->m[m].ex = v->m[m].ey = 0;
        }
}

static void rh_shed(void)                                 /* overload: fade the quietest voice, released first */
{
    uint32_t i, m, pick = RH_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < RH_NV; i++) {
        uint32_t sum = 0, score;
        if (!rv[i].alive || rv[i].fade != 32768) continue;
        for (m = 0; m < RH_NM; m++) sum += (uint32_t)rv[i].m[m].peak >> 4;
        score = (rv[i].held ? 0x40000000u : 0u) + (sum > 0x3FFFFFFFu ? 0x3FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
    if (pick < RH_NV) { rv[pick].fade = 32767; rv[pick].pend = 0; rh_shed_count++; }
}

/* the bend moved: new resonator frequencies for the sounding voices (genkigenki's rh_retune, PR #3, for the SMK-37's
 * pitch wheel; here for MIDI pitch bend, engine.c eng_bend_set). The decay is kept (eps depends on the decay only); a
 * damper crossfade under way goes on from the new frequency. Spread: a block retunes at most RH_RT_PER sounding voices
 * (a quarter), round-robin, so every voice reaches the bend within 4 blocks of its last change and the worst block
 * pays a quarter of the whole retune (a voice born meanwhile starts at the bend in rh_init). Runs only in a block
 * where the bend changed or voices are still pending. */
static void __attribute__((noinline)) rh_retune(void)
{
    uint32_t i, m, k, done = 0, pend;
    int32_t t, b = rh_bend_t, kd = rh_k_decay, kr = rh_k_rel;
    if (b != rh_bend_used) { rh_bend_used = b; rh_rt_pend = (1u << RH_NV) - 1u; }
    t = rh_tune_t;
    t = t + b + (int32_t)(((int64_t)t * b) >> 30);                 /* rh_tune_now at the latched bend */
    pend = rh_rt_pend; i = rh_rt_cur;
    for (k = 0; k < RH_NV && pend && done < RH_RT_PER; k++, i = i + 1u == RH_NV ? 0u : i + 1u) {
        rh_voice_t *v = &rv[i];
        if (!(pend >> i & 1u)) continue;
        pend &= ~(1u << i);
        if (!v->alive) continue;
        done++;
        for (m = 0; m < RH_NM; m++) {
            rh_ms_t *s = &v->m[m];
            const rh_mode_t *km = &v->k->m[m];
            int32_t sp;
            int64_t cr;
            if (!rh_mode_runs(v->k, m)) continue;
            cr = rh_coef_tune(km, t, &sp);                 /* rh_coef's integers: the tuning part once for both decays,
                                                            * eps the note-on's while DECAY / RELEASE stay put */
            s->e0 = rh_coef_e(v->kd == kd ? s->eps0 : rh_coef_eps(km->d, kd), sp, cr);
            s->e1 = rh_coef_e(v->kr == kr ? s->eps1 : rh_coef_eps(km->drel, kr), sp, cr);
            s->e = s->e0 + (int32_t)(((int64_t)(s->e1 - s->e0) * v->w_tgt) >> 15);
            s->es = s->e;
        }
    }
    rh_rt_pend = pend; rh_rt_cur = i;
}

/* the mechanical noises after the voices, into the mix (Q20): the key-down knock (S11), the pedal's thud (S12) */
static FR_RAM void __attribute__((noinline)) rh_noise(int32_t *mix, uint32_t n)
{
    if (rh_pn_n) {                                        /* 32 samples a step of the envelope; a noise value held for
                                                           * two samples (half the cost: the one-pole and the RLC take
                                                           * its image away) */
        uint32_t x = rh_pn_x, m = (rh_pn_n < n ? rh_pn_n : n) & ~1u, sb;
        int32_t lp = rh_pn_lp, g = rh_pn_env, y;
        for (sb = 0; sb < m; sb += SUB) {
            int32_t *o = mix + sb, *e = mix + (sb + SUB < m ? sb + SUB : m);
            for (; o < e; o += 2) {
                x = x * 1664525u + 1013904223u;
                lp += (((int32_t)x >> 16) - lp) >> 3;
                y = (lp * g) >> 10;
                o[0] += y; o[1] += y;
            }
            g -= g >> 2;
        }
        rh_pn_n = m ? rh_pn_n - m : 0; rh_pn_x = x; rh_pn_lp = lp; rh_pn_env = g;
    }
    if (rh_kn_tab) {                                      /* the pulse to its end (the samples left, counted first:
                                                           * a tight loop), as the voices play it, each value held
                                                           * for two samples (half the cost; the pulse is smooth, its
                                                           * image at fs/2 is far under the knock, and the RLC's) */
        const int16_t *pt = rh_kn_tab;
        uint32_t pos = rh_kn_pos, rate = rh_kn_rate, r2 = rate << 1;
        uint32_t left = (((uint32_t)RH_PLEN << 16) - pos + rate - 1u) / rate;
        int32_t amp = rh_kn_amp, *o = mix, *e = mix + (left < n ? left : n), x;
#define RH_KN_F(p) (x = pt[(p) >> 16], x = ((x + (((pt[((p) >> 16) + 1u] - x) * (int32_t)(((p) >> 1) & 0x7FFFu)) >> 15)) * amp) >> 15)
        for (; o + 1 < e; o += 2, pos += r2) { RH_KN_F(pos); o[0] += x; o[1] += x; }
        if (o < e) { RH_KN_F(pos); o[0] += x; pos += rate; }
#undef RH_KN_F
        rh_kn_pos = pos; rh_kn_tab = left <= n ? 0 : pt;
    }
}

/* one Q28 DF1 biquad with error feedback over a block, in -> out (may be the same buffer); st = m1, m2, o1, o2, e.
 * The pickup load (RLC) and the TYPE's amp / speaker biquads are this same form; a cascade run stage by stage gives
 * the same integers as sample by sample. Its own function: the state and coefficients stay in registers */
static inline __attribute__((always_inline)) void rh_df1_t(const int32_t *in, int32_t *out, uint32_t n,
                                                           const int32_t *c, int32_t *st, int q15)
{
    int32_t c0 = c[0], c1 = c[1], c2 = c[2], c3 = -c[3], c4 = -c[4];   /* - c3 o1 = (-c3) o1: 5 MACs */
    int32_t m1 = st[0], m2 = st[1], o1 = st[2], o2 = st[3], e = st[4];
    const int32_t *ie = in + n;
    while (in < ie) {
        int32_t m = *in++, o;
        int64_t acc;
        RH_REG32(c0); RH_REG32(c1); RH_REG32(c2); RH_REG32(c3); RH_REG32(c4);
        acc = (int64_t)c0 * m + (int64_t)c1 * m1 + (int64_t)c2 * m2 + (int64_t)c3 * o1 + (int64_t)c4 * o2 + e;
        o = (int32_t)(acc >> 28); e = (int32_t)(acc & 0xFFFFFFF);
        m2 = m1; m1 = m; o2 = o1; o1 = o;
        *out++ = q15 ? (o + 16) >> 5 : o;               /* (q15: the last stage rounds to Q15, as rh_q15) */
    }
    st[0] = m1; st[1] = m2; st[2] = o1; st[3] = o2; st[4] = e;
}
static __attribute__((noinline)) void rh_df1(const int32_t *in, int32_t *out, uint32_t n, const int32_t *c, int32_t *st)
{
    rh_df1_t(in, out, n, c, st, 0);
}
/* SPREAD's side: its own copies (the mid's rh_df1 keeps its specialised registers), the last stage rounding to Q15
 * in the same pass (no rh_q15 pass: the same integers) */
static FR_RAM __attribute__((noinline)) void rh_df1_s(int32_t *p, uint32_t n, const int32_t *c, int32_t *st)
{
    rh_df1_t(p, p, n, c, st, 0);
}
static FR_RAM __attribute__((noinline)) void rh_df1_sq(int32_t *p, uint32_t n, const int32_t *c, int32_t *st)
{
    rh_df1_t(p, p, n, c, st, 1);
}

/* Q20 -> Q15 in place (round, no DC bias); returns max |out| */
static FR_RAM __attribute__((noinline)) int32_t rh_q15(int32_t *p, uint32_t n)
{
    int32_t mx = 0, *pe = p + n;
    while (p < pe) {
        int32_t o = (*p + 16) >> 5;
        if (iabs(o) > mx) mx = iabs(o);
        *p++ = o;
    }
    return mx;
}

/* a TYPE change under sound (0.9.6, BUGHUNT #12: the pickup RLC and the body biquads switched under the ringing
 * notes, their states left from the old coefficients: FELT -> DYNO 153x a held chord's d2). The filters keep the TYPE
 * they run (run); a new TYPE with the filters ringing starts the new chain from silence next to the old one, which
 * goes on, and the output crossfades old -> new over RH_TXF_BLK blocks (23 ms); then the new chain's state is the
 * filters'. With every filter state at 0 (nothing ringing) the TYPE is taken at once, as before (the same integers).
 * The side (SPREAD) the same. Only while it fades: ~3 more biquad passes and a blend a block */
#define RH_TXF_BLK 4u
typedef struct { int32_t st[3][5], ss[3][5], buf[256], sbuf[256]; uint8_t n, ty, run; } rh_txf_t;
static rh_txf_t rh_txf RH_SP_SEC(".bss.rh_txf");
static int rh_chain_zero(void)
{
    uint32_t i, z = 0;
    for (i = 0; i < 15u; i++) z |= (uint32_t)(rh_st[0][i] | sd_st[0][i]);
    return z == 0;
}
static uint32_t rh_txf_type(void)                         /* the TYPE the filters run this block */
{
    uint32_t want = rh_type & 3u;
    if (want != rh_txf.run && !rh_txf.n) {
        if (rh_chain_zero()) rh_txf.run = (uint8_t)want;
        else { memset(rh_txf.st, 0, sizeof rh_txf.st); memset(rh_txf.ss, 0, sizeof rh_txf.ss); rh_txf.ty = (uint8_t)want; rh_txf.n = RH_TXF_BLK; }
    }
    return rh_txf.run;
}
/* TYPE t's chain: in -> x (the side: x in place, its last stage rounding to Q15), from st (RLC, the body biquads) */
static __attribute__((noinline)) void rh_chain(int32_t *x, const int32_t *in, uint32_t n, int32_t (*st)[5], int side, uint32_t t)
{
    uint32_t nb = RH_TYPE[t].nbody, sb;
    if (!side) rh_df1(in, x, n, RH_RLC[t], st[0]);
    else if (nb) FR_FAR(rh_df1_s)(x, n, RH_RLC[t], st[0]); else FR_FAR(rh_df1_sq)(x, n, RH_RLC[t], st[0]);
    for (sb = 0; sb < nb; sb++)
        if (!side) rh_df1(x, x, n, RH_BODY[t][sb], st[1u + sb]);
        else if (sb + 1u < nb) FR_FAR(rh_df1_s)(x, n, RH_BODY[t][sb], st[1u + sb]);
        else FR_FAR(rh_df1_sq)(x, n, RH_BODY[t][sb], st[1u + sb]);
}
static void rh_txf_blend(int32_t *x, const int32_t *nw, uint32_t n)   /* x = old + (new - old) w, w rising */
{
    int32_t w = (int32_t)(RH_TXF_BLK - rh_txf.n) * (32768 / (int32_t)RH_TXF_BLK), dw = 32768 / (int32_t)RH_TXF_BLK / (int32_t)n;
    uint32_t j;
    for (j = 0; j < n; j++) { w += dw; x[j] += (int32_t)(((int64_t)(nw[j] - x[j]) * w) >> 15); }
}
static void rh_txf_end(void)                              /* the crossfade done: the new chain is the filters' */
{
    memcpy(rh_st, rh_txf.st, sizeof rh_st);
    memcpy(sd_st, rh_txf.ss, sizeof sd_st);
    rh_txf.run = rh_txf.ty; rh_txf.n = 0;
}

static __attribute__((noinline)) void rh_render(int32_t *mono_out, uint32_t n)   /* kept out of app_audio */
{
    uint32_t i, sb, j, act = 0, held = 0, idle, sd;
    int32_t mx = 0;
    uint32_t ty;
    rh_events();
    ty = rh_txf_type();
    if (rh_bend_t != rh_bend_used || rh_rt_pend) rh_retune();
    if (rh_shed_req) { rh_shed_req = 0; rh_shed(); }
    for (i = 0; i < RH_NV && !rv[i].alive; i++) ;
    idle = i == RH_NV && rh_quiet_blocks && !rh_pn_n;    /* (the pedal's thud plays into a silent engine) */
    for (i = 0; i < n; i++) rh_mono[i] = 0;
    for (i = 0, sd = 0; i < RH_NV; i++) sd |= rv[i].alive && rv[i].pan;
    if (sd) rh_side_tail = 4;                             /* the side's filters ring out 3 blocks after its last voice */
    if (rh_side_tail) for (i = 0; i < n; i++) rh_side[i] = 0;
    for (i = 0; i < RH_NV; i++) {
        rh_voice_t *v = &rv[i];
        for (sb = 0; sb < n && v->alive; sb += SUB)
            rh_voice_sub(v, rh_mono + sb, rh_side + sb);
        if (v->alive && v->life && --v->life == 0 && v->fade == 32768) v->fade = 32767;   /* its end: the 1.5 ms fade */
    }
    if (rh_kn_tab || rh_pn_n) FR_FAR(rh_noise)(rh_mono, n);
    if (idle) {                                           /* silent: no voice in this block and the filters' state
                                                           * zeroed at the end of the last one: the output is exactly 0 */
        for (j = 0; j < n; j++) mono_out[j] = 0;
    } else {   /* pickup load: RLC low-pass on the mix, then the instrument's amp / speaker (nb biquads); Q20 -> Q15 */
        rh_chain(mono_out, rh_mono, n, rh_st, 0, ty);
        if (rh_txf.n) { rh_chain(rh_txf.buf, rh_mono, n, rh_txf.st, 0, rh_txf.ty); rh_txf_blend(mono_out, rh_txf.buf, n); }
        mx = FR_FAR(rh_q15)(mono_out, n);
    }
    rh_side_on = rh_side_tail != 0;
    if (rh_side_tail) {                                   /* the side: the same RLC and body, its own state */
        if (rh_txf.n) memcpy(rh_txf.sbuf, rh_side, n * sizeof *rh_side);
        rh_chain(rh_side, rh_side, n, sd_st, 1, ty);
        if (rh_txf.n) { rh_chain(rh_txf.sbuf, rh_txf.sbuf, n, rh_txf.ss, 1, rh_txf.ty); rh_txf_blend(rh_side, rh_txf.sbuf, n); }
        if (--rh_side_tail == 0) { memset(sd_st, 0, sizeof sd_st); memset(rh_txf.ss, 0, sizeof rh_txf.ss); }
    }
    for (i = 0; i < RH_NV; i++)
        if (rv[i].alive) { act |= 1u << i; rh_notes[i] = rv[i].note; if (rv[i].held) held |= 1u << i; }
    if (!act && !rh_pn_n && mx < 2) {                     /* exact silence once everything has ended */
        memset(rh_st, 0, sizeof rh_st);
        if (rh_txf.n) { rh_txf.run = rh_txf.ty; rh_txf.n = 0; }   /* (silent: nothing left to fade) */
        if (rh_quiet_blocks < 0xFFFFu) rh_quiet_blocks++;
    } else
        rh_quiet_blocks = 0;
    if (rh_txf.n && !--rh_txf.n) rh_txf_end();           /* (the blend's last block was this one) */
    rh_held_bits = held;
    rh_active_bits = act;
}
