/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes engine: the physically modelled Rhodes voice (IMPLEMENTATION-v3 §4.3, §5).
 *
 * Per voice: a hammer force pulse (shapes from a host contact simulation) drives the tine's normal modes (V1 the
 * tine-bar sustain mode exactly on f0, V2 the other tine-bar mode, O1/O2 the tine's 2nd/3rd modes, H1 the
 * horizontal polarisation in Phase 3). Each mode is an exact-pole coupled-form resonator ("MC3") with first-order
 * error feedback. The tip displacement goes through the electromagnetic pickup: a radial flux table Phi~(s),
 * s = (x + off)^2 + h^2, evaluated by cubic Hermite, output = the exact flux difference. The voices sum into a
 * mono mix that passes the pickup load (RLC biquad). All the physics is precomputed per key on the host
 * (tools/gen_rhodes.py -> firmware/gen/rhodes_tables.h) (the flux table is stored as a bias minus Phi~, every word exact and out of the
 * chip-ROM window: the pickup loops take the difference the other way round, the bias cancels).
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
#if defined(RH_DRIVE) && defined(RH_VM_ON)
#include "rh_tone_b.h"                         /* ... and the velocity method MAP's (rh_tone_gain, map 1) */
#endif
#endif
#include "rhodes_phim.h"                       /* the pickup table interleaved (tools/gen_phim.py) */
#define RH_MK_N 12                             /* the level make-ups' anchors per row (rh_mk_q12) */
#if defined(RH_DRIVE) && !defined(RH_NO_DRV_MK)  /* (-DRH_NO_DRV_MK: without it, as tools/drive_makeup.py measures) */
#include "rh_drive_mk.h"                       /* the pickup drive's level make-up (tools/drive_makeup.py --write) */
#endif
#if defined(RH_DRIVE) && !defined(RH_NO_TOP) && !defined(RH_VM_PROBE)   /* (-DRH_NO_TOP: without it, as tools/rh_top.py
                                                * measures) */
#include "rh_top.h"                            /* the clean top's swing cut per TYPE and key (tools/rh_top.py --write) */
#ifdef RH_NO_TOP                                /* (a test build without the clean top: tests/py/test_gate_b.py, the
#undef RH_TOP_Q                                 * golden model has no strike-time output gains) */
#endif
#endif
#if defined(RH_DRIVE) && defined(RH_VM_ON)       /* the velocity map MAP (0.9.10 S1b, RESPONSE B until the dry
                                                * distortion fix put the tine back on the physical force law, after
                                                * which it played the pickup drive's sound): retired, RESPONSE B is
                                                * the dynamics stage's light curve (rh_dyn); -DRH_VM_ON builds it */
#define RH_VMAP 1                              /* the second velocity method, MAP (rh_touch_mode 1): rh_velmap.h */
#endif

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
#define RAMP_SUB 5u                            /* the damper settle (rh_settle) on C3..B4, sub-blocks */
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
    int32_t hd_off0, hd_g0, hd_gc, hd_d, hd_y1, hd_y2; /* the harmonic decay glide (rh_hd_init): the strike's offset and
                                                * gain there, the gain it set last, the glide's end (Q25 gap), its two
                                                * lag states */
    int32_t hd_a1, hd_a2;                      /* the two lags' coefficients per step (Q30) */
    uint16_t hd_n, hd_w;                       /* glide steps left (0: none), steps still to wait before it */
    uint8_t hd_ph;                             /* sub-blocks to the next step (RH_HD_SUBS) */
#ifdef RH_DRIVE
    /* S1a, the pickup drive (gen_rhodes.pickup_drive): pamp above is the excursion law's force (RH_PDRV), the one the
     * modes get; the physical hammer force (RH_PAMP) stays the measure of the note for the key-up noises and the thump
     * cap (rh_left); the output gain law (RH_PGAIN) is in gain, its value kept for a re-strike; kill in the voice's own
     * gain (an output of half an LSB) */
    int32_t pphys;                             /* Q15: the physical force of the strike (rh_left) */
    int32_t kl;                                /* the kill level, sum units (k->kill >> 3 over the gain law) */
    uint16_t gq;                               /* Q16: the gain law's value at the note-on */
    uint8_t lt;                                /* RESPONSE at the strike (rh_touch_mode): 0 A, 1 B (rh_dyn) */
    uint32_t pq;                               /* Q12: the tine's physical swing over the modes' (the note-on's
                                                * strike: RH_PAMP / (RH_PDRV x the force gains' scale)) */
    uint32_t mk;                               /* Q12: the level make-up at the note-on (rh_drv_mkt, 4096 = none;
                                                * < 2^20 with the clean top's gain back) */
    int32_t gain0;                             /* the voice gain before the velocity's (MAP's G, the drive's gain law
                                                * and make-up): a re-strike's level from it */
#endif
#ifdef RH_VMAP
    uint8_t vm;                                /* the velocity method of the note-on (rh_touch_mode): 0 DRIVE, 1 MAP */
    int32_t vg;                                /* MAP: the map's gain scale G (Q11) */
    int32_t vn;                                /* MAP: Q11 <= 1, the map's X x G, the mechanical noises' scale */
#endif
#ifdef DINGHY_PLUGIN
    uint16_t pdly;                             /* the plugin: samples before the pulse starts (fr_ofs_apply) */
#endif
} rh_voice_t;
#ifdef RH_VMAP
#define RH_IS_MAP(v) ((v)->vm)
#else
#define RH_IS_MAP(v) 0
#endif

static rh_voice_t rv[RH_NV];
#ifdef DINGHY_HOST
static uint8_t rh_host_vel;                          /* (host: rh_strike's last velocity) */
#endif
#ifdef RH_DRIVE
#define RH_KILL(v, k) ((v)->kl)                /* the kill level in the voice's own gain (rh_init) */
#define RH_OUT_SUM(v, s) rh_phys_sum(v, s)      /* a steal / shed score: the mode peaks as the tine's physical
                                                * swing, the order the score had before the drive (the modes' units
                                                * carry each key's own force scale) */
#else
#define RH_KILL(v, k) ((k)->kill >> 3)
#define RH_OUT_SUM(v, s) (s)
#endif
#ifdef RH_DRIVE
static inline uint32_t rh_phys_sum(const rh_voice_t *v, uint32_t s)
{
    uint64_t o = ((uint64_t)s * v->pq) >> 12;
    return o > 0x7FFFFFFFu ? 0x7FFFFFFFu : (uint32_t)o;
}
#endif
#ifdef FR_HQ
#include "hq/rhodes_hq.h"                      /* the HQ Rhodes (the Move only; never in the FM-1 / SMK builds) */
#endif
#ifdef DINGHY_HOST
static int32_t rh_mono[256], rh_xF[2u * SUB];
#else                                          /* the mode / pickup loops' buffers in one section of their own (firmware
                                                * only), out of the compiler's global merge: with 8 voices the merge
                                                * moved them and the mode loop lost a register (a stack load) */
static int32_t rh_mono[256] __attribute__((section(".bss.rh_buf"))), rh_xF[2u * SUB] __attribute__((section(".bss.rh_buf")));
#endif
#define rh_x rh_xF                             /* the modes' sum (pickup input) and, right after it in one array, the */
#define rh_F (rh_xF + SUB)                     /* hammer force: the force loop reads xp[SUB], no pointer of its own */
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
#ifdef RH_DRIVE
/* the level make-ups' code (rh_drive_mk.h: the pickup drive's per TYPE, key, velocity; rh_velmap_mk.h: the map's per
 * key, velocity): 1/32 octave, 128 = none, RH_MK_N anchors per row (v40, 48 .. 120, 128; under v40 the v40 value),
 * interpolated in the code; the code to a Q12 gain: 4096 x 2^((c - 128) / 32), from the octave's 32 steps (Q15) */
static const uint16_t RH_MK_F[32] = {32768,33486,34219,34968,35734,36516,37316,38133,38968,39821,40693,41584,42495,43425,44376,45348,46341,47356,48393,49452,50535,51642,52773,53928,55109,56316,57549,58809,60097,61413,62757,64132};
static uint32_t rh_mk_l(uint32_t c) { return ((uint32_t)RH_MK_F[c & 31u] << (c >> 5)) >> 7; }   /* c <= 256 */
static uint32_t rh_mk_q12(const uint8_t *row, uint32_t vel)      /* Q12, < 2^16 */
{
    uint32_t u = vel < 40u ? 0u : vel - 40u;
    const uint8_t *m = row + (u >> 3);
    uint32_t c = ((uint32_t)m[0] << 3) + (uint32_t)(((int32_t)m[1] - (int32_t)m[0]) * (int32_t)(u & 7u));
    int32_t x0 = (int32_t)rh_mk_l(c >> 3), x1 = (int32_t)rh_mk_l((c >> 3) + 1u);
    return (uint32_t)(x0 + (((x1 - x0) * (int32_t)(c & 7u)) >> 3));
}
#ifdef RH_DRV_MK_Q
/* the pickup drive's level make-up per TYPE, key and velocity: the drive's loudness back to the level the notes had
 * before it at mf and above, never under it (tools/drive_makeup.py). A gain on the voice's output only: the timbre
 * (the swing through the pickup) is the drive's. Note-on / re-strike only */
#define rh_drv_mk(key, vel) rh_mk_q12(RH_DRV_MK[rh_type & 3u][key], vel)
/* the mechanical noises (knock, felt, pedal thud: absolute levels) follow the notes' level: the make-up brings the
 * notes back to the reference's level, the noises were set against, so they stay at their own (without it: RH_NZ_G,
 * the drive's gain target re the noises' reference, -6 dB; with it the pedal's thud played 6 dB under 0.9.9's) */
#define RH_NZ_S(a) ((uint32_t)(a))
#else
#define rh_drv_mk(key, vel) 4096u
#define RH_NZ_S(a) (((uint32_t)(a) * RH_NZ_G) >> 15)
#endif
/* the clean top (0.9.10, the user: "v127 itself must be cleaned up", external controllers send 127 easily): the
 * pickup's H2 grows with the tine's swing (H2 / H1 in proportion to it), to about -6 dB re H1 at v127 on the mid keys
 * (0.9.9 the same). Above a knee the swing is cut, linearly in dB to RH_TOP_R[TYPE][key] (1/32 octave) at v127, and
 * the voice's output gain takes the same factor back (the fundamental's level as it was) and RH_TOP_G more on the
 * same ramp: the loudness the bark took with it (K-weighted, at v127; never less), so the dynamic range is kept
 * (tools/rh_top.py: the pickup's H2 and the attack's H2 to -14 dB re H1 at v127). The cut runs over 1.25 R velocity
 * steps (0.15 dB a step: the swing still grows, ~0.2 dB a step before), so the knee is per key (127 - 1.25 R) and
 * every velocity under it, and every key with R 0, is the engine as it was. Both velocity methods: the drive's force
 * (RH_PDRV) and make-up, MAP's X and G. Note-on / re-strike only */
#ifdef RH_TOP_Q
static uint32_t rh_top_x(uint32_t key, uint32_t vel, uint32_t *g8)   /* the cut and the gain back, 1/256 octave */
{
    uint32_t r = RH_TOP_R[rh_type & 3u][key], span = r + (r >> 2), u;
    *g8 = 0;
    if (!r || vel + span <= 127u) return 0;
    u = vel + span - 127u;                                   /* 1 .. span */
    *g8 = ((r + RH_TOP_G[rh_type & 3u][key]) * 8u * u) / span;   /* (32-bit divides; r + g <= 128) */
    return (r * 8u * u) / span;
}
static uint32_t rh_q12_of(uint32_t c8)                       /* 4096 x 2^((c8 - 1024) / 256), c8 8 .. 2047 */
{
    uint32_t x0 = rh_mk_l(c8 >> 3), x1 = rh_mk_l((c8 >> 3) + 1u);
    return x0 + (((x1 - x0) * (c8 & 7u)) >> 3);
}
static uint32_t rh_top_q12(uint32_t key, uint32_t vel)      /* Q12 <= 4096: the swing's factor */
{
    uint32_t g8, c8 = rh_top_x(key, vel, &g8);
    return c8 ? rh_q12_of(1024u - c8) : 4096u;               /* (r <= 120: the code stays >= 64) */
}
static uint32_t rh_top_g12(uint32_t key, uint32_t vel)      /* Q12 >= 4096: the output's gain back (< 2^16) */
{
    uint32_t g8;
    return rh_top_x(key, vel, &g8) ? rh_q12_of(1024u + g8) : 4096u;
}
static uint32_t rh_drv_mkt(uint32_t key, uint32_t vel)      /* the make-up x the top's gain back (Q12, < 2^20) */
{
    uint32_t m = rh_drv_mk(key, vel), g = rh_top_g12(key, vel);
    return g == 4096u ? m : (uint32_t)(((uint64_t)m * g) >> 12);   /* (m < 2^16, g <= 2^16) */
}
#else
#define rh_top_q12(key, vel) 4096u
#define rh_top_g12(key, vel) 4096u
#define rh_drv_mkt(key, vel) rh_drv_mk(key, vel)
#endif
#endif
/* voicing, written by the main loop (params.c), applied at each note-on (IMPLEMENTATION-v3 §4.4, §10.1) */
static volatile int32_t rh_tone_off;           /* Q25: added to the pickup offset (TONE) */
static volatile uint8_t rh_touch_mode;         /* RESPONSE, taken at each note-on (params.c vresp_apply): 0 A (FULL,
                                                * dynamic), 1 B (LIGHT, the light touch): the dynamics stage's soft end
                                                * (rh_dyn); with -DRH_VM_ON, 1 is the retired velocity map MAP */
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
static const int16_t *rh_kn_tab;               /* the pulse playing (0 = none): rh_kn_buf */
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
static int16_t rh_kn_buf[RH_PLEN + 2] RH_OWN_SECTION(".bss.rh_kn_buf");   /* the knock's pulse (rh_knock_strike): out of the global merge */
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
#ifdef DINGHY_PLUGIN
/* the desktop plugin: a note starts at its own sample in the block (engine.c eq_ofs, 1/256 sample): rh_post keeps the
 * place rh_ofs_in gives, rh_events hands it to the strike in rh_ofs (0 for anything else: a note that waited for a
 * steal's fade starts at the block's start). The voice waits pdly samples, then plays its pulse from the fraction's
 * point (the pulse at its true time, read between the samples as it always is); the knock likewise (whole samples).
 * The FM-1 / SMK / Move builds have none of it */
static uint16_t ev_ofs[32], rh_ofs_in, rh_ofs, rh_kn_dly;
static void fr_ofs_apply(uint32_t *ppos, uint16_t *dly, uint32_t prate, uint32_t ofs)
{
    uint32_t d = ofs >> 8, f = ofs & 255u;
    if (f) { d++; *ppos = (uint32_t)(((uint64_t)prate * (256u - f)) >> 8); }   /* (the first sample after the start) */
    *dly = (uint16_t)d;
}
#endif
static volatile uint8_t rh_hold;               /* main loop: the sustain pedal is down (for the screen/LED) */
static uint8_t rh_shed_req;                    /* ISR: the audio ran late: fade out the quietest released voice */
static volatile uint32_t rh_shed_count;

static void rh_post(uint32_t type, uint32_t note, uint32_t val)
{
    uint32_t w = ev_w;
    if (w - ev_r >= 32u) { ev_drop++; return; }
#ifdef DINGHY_PLUGIN
    ev_ofs[w & 31u] = rh_ofs_in;
#endif
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
#ifndef RH_CC_SEC
#define RH_CC_SEC ".pool.rh_cc"                 /* (FR_C1 builds, frodhes.c: .ramx) */
#endif
static rh_cc_t rh_cc[2][RH_NKEY][RH_NM] RH_OWN_SECTION(RH_CC_SEC);
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

/* ---- the pulse banks, packed (tools/gen_pulse_pz.py, firmware/gen/pulse_pz.h; 2026-11-01: 79 KB of flash): a pulse
 * decodes into a voice's buffer at the strike, the same 66 int16 the table held (the pads 0), so the force is
 * byte-identical. PZ: per point the 3rd difference, the head int16, the tail int8; ~7 instructions a point */
#include "pulse_pz.h"
#define RH_PZI(key, l) ((key) * RH_NLAYER + (l))
static const int16_t *rh_pz(int16_t *o, const pz_bank_t *b, uint32_t p)
{
    const int16_t *h = b->d + b->off[p];
    uint32_t n = b->nh[p], i;
    const int8_t *t = (const int8_t *)(h + n);
    int32_t x = 0, d1 = 0, d2 = 0;
    for (i = 0; i < n; i++) { d2 += h[i]; d1 += d2; x += d1; o[i] = (int16_t)x; }
    for (; i < 64u; i++) { d2 += *t++; d1 += d2; x += d1; o[i] = (int16_t)x; }
    o[64] = o[65] = 0;
    return o;
}
static void rh_pz_mix(int16_t *o, const pz_bank_t *b, uint32_t p, int32_t w)   /* o + ((pulse - o) w >> 15): the layer crossfade */
{
    const int16_t *h = b->d + b->off[p];
    uint32_t n = b->nh[p], i;
    const int8_t *t = (const int8_t *)(h + n);
    int32_t x = 0, d1 = 0, d2 = 0;
    for (i = 0; i < n; i++) { d2 += h[i]; d1 += d2; x += d1; o[i] = (int16_t)(o[i] + (((x - o[i]) * w) >> 15)); }
    for (; i < 64u; i++) { d2 += *t++; d1 += d2; x += d1; o[i] = (int16_t)(o[i] + (((x - o[i]) * w) >> 15)); }
}

/* The dynamics stage (0.9.10): a key x velocity gain on the voice, set at the strike (no per-sample cost), relative
 * to 0.9.9's loudness. From v40 up both keep 0.9.9's level exactly (2026-11-09, the user: the soft end back toward
 * 0.9.9; it was 0 dB at v60 to -9 dB at v20: v40 -4.5, v50 -1.6 dB, the FM-1's keys' usual range heard weak); below,
 * RESPONSE (rh_touch_mode, taken at the strike) picks the soft end: A (FULL, dynamic) goes quieter on a smoothstep,
 * 0 dB at v40 to -9 dB at v10 and below (no step, no corner, at most 0.45 dB a velocity step: v30 -2.3, v20 -6.7 dB;
 * v1 -> v127 as before); B (LIGHT, the
 * light touch) keeps 0.9.9's velocity law (v20 -> v127 ~25.5 dB). The same sound at every velocity in both, only the
 * soft end's level differs (the same drive, swing and harmonics: B is A x a gain, to the integers' rounding; A = B
 * from v40). A's depth is held by the output gate (fx.c GATE_LO, 16 LSB peak, -66 dBFS, 406 ms): a soft note that
 * drops under it is faded 0.4 s later; at -9 dB no velocity has more notes under it within 1 s than 0.9.9 at its
 * 32 LSB (keys 21..96 x the TYPEs, LEVEL 64: v1 20, v10 17, v20 10, v30 4 of 104; 0.9.9 20 / 17 / 13 / 8; -12 dB
 * at v1 gave 24 / 19 / 10 / 4), and a v1 note on keys up to ~96 stays ~20 dB over the FM-1 jack's -92.5 dBFS floor.
 * The top keys taper in both, 0 dB at key 82 to -5 dB at key 98 and above (smoothstep: the upper octave thinner,
 * keys 90-100 at v64 ~-6 dB re key 60). Only ever a cut, so no chord gets louder than 0.9.9's. It multiplies the drive's output gain law (rh_drive_g: the note-on's
 * gain, the kill, a re-strike's force ratio) and the noises' scale (the knock, the felt). The tables:
 * round(32768 x 10^(-9 s((40 - v) / 30) / 20)), round(32768 x 10^(-5 s((k - 82) / 16) / 20)), s(t) = 3t^2 - 2t^3 on
 * [0, 1] */
#define RH_DYN_V_HI 40u
#define RH_DYN_K_LO 82u
static const uint16_t RH_DYN_V[RH_DYN_V_HI] = {             /* Q15, v 0..39 (40..127: 32768): RESPONSE A */
    11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11666, 11781, 11969, 12227, 12554,
    12949, 13413, 13944, 14543, 15210, 15943, 16744, 17608, 18535, 19519, 20555, 21636, 22754, 23896, 25049, 26197,
    27322, 28404, 29420, 30347, 31159, 31831, 32338, 32658};
/* the bars' LIGHT level map (bars.c bar_lt_g: 1 / this, up to +9 dB at v20 and below, 0 dB from v60): the electric
 * piano's FULL soft end until 2026-11-09, kept for the bars so their LIGHT stays byte-identical */
#define RH_DYN_VB_HI 60u
static const uint16_t RH_DYN_VB[RH_DYN_VB_HI] = {
    11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627, 11627,
    11627, 11627, 11627, 11627, 11627, 11649, 11714, 11821, 11969, 12156, 12382, 12646, 12949, 13291, 13670, 14087,
    14543, 15037, 15568, 16137, 16744, 17386, 18064, 18775, 19519, 20291, 21091, 21913, 22754, 23608, 24472, 25337,
    26197, 27044, 27870, 28665, 29420, 30125, 30769, 31341, 31831, 32228, 32523, 32705};
static const uint16_t RH_DYN_K[RH_KEY_LO + RH_NKEY - RH_DYN_K_LO] = {   /* Q15, keys 82..108 (below: 32768) */
    32768, 32557, 31967, 31073, 29949, 28673, 27312, 25928, 24573, 23288, 22108, 21059, 20161, 19432, 18888, 18546,
    18427, 18427, 18427, 18427, 18427, 18427, 18427, 18427, 18427, 18427, 18427};
static uint32_t rh_dyn(uint32_t n, uint32_t vel, uint32_t lt)   /* Q15 (<= 32768): the key n (clamped), velocity vel;
                                                * lt: RESPONSE B (light: 0.9.9's velocity law, the key taper only) */
{   /* (-DRH_NO_DYN: without it, as tests/py/test_rhodes_dyn.py compares; off in the velocity map's probe builds:
     * gen_velmap.py measures the map without it) */
#if defined(RH_NO_DYN) || defined(RH_VM_PROBE)
    (void)n; (void)vel; (void)lt;
    return 32768u;
#else
    uint32_t g = vel < RH_DYN_V_HI && !lt ? RH_DYN_V[vel] : 32768u;
    if (n > RH_KEY_LO + RH_NKEY - 1u) n = RH_KEY_LO + RH_NKEY - 1u;   /* (a MIDI note over 108 plays the top key's
                                                * values: the table ended there, read past its end: the sanitizer) */
    if (n >= RH_DYN_K_LO) g = (g * RH_DYN_K[n - RH_DYN_K_LO]) >> 15;
    return g;
#endif
}
static int32_t rh_dyn_gain(int32_t gain, uint32_t gd)       /* gain x gd (32768: gain itself, byte-identical) */
{
    return gd >= 32768u ? gain : (int32_t)(((int64_t)gain * gd) >> 15);
}
#ifdef RH_DRIVE
/* the pickup drive's output gain law at a velocity (Q16, 1 .. 65535): RH_PGF (gain x force) and RH_PDRV (the force)
 * interpolated between the anchors as rh_load_pulse does, then divided, so the output (their product while the swing
 * is small) is linear between anchors (tools/rhodes_model.py vgain: the same integers). Note-on / re-strike only */
static int32_t rh_drive_g(uint32_t key, uint32_t vel, uint32_t lt)
{
    uint32_t i = RH_VPOS_I[vel], f = RH_VPOS_F[vel];
    const uint16_t *pg = RH_PGF[key], *pd = RH_PDRV[key];
    int32_t q = (int32_t)pg[i] + ((((int32_t)pg[i + 1] - (int32_t)pg[i]) * (int32_t)f) >> 8);
    int32_t d = (int32_t)pd[i] + ((((int32_t)pd[i + 1] - (int32_t)pd[i]) * (int32_t)f) >> 8);
    uint32_t g = ((uint32_t)q << 15) / (uint32_t)(d < 1 ? 1 : d);   /* q < 2^16: 32-bit */
    if (g > 65535u) g = 65535u;
    g = (g * rh_dyn(key + RH_KEY_LO, vel, lt)) >> 15;                     /* x the dynamics stage (rh_dyn, <= 2^15) */
    return g < 1u ? 1 : (int32_t)g;
}
#endif

/* ---- voices (ISR) */
#ifdef RH_DRIVE
static int32_t rh_hermite(const int32_t *T, int64_t s);
static int32_t rh_pd(uint32_t ki, uint32_t vel)        /* the drive's force at vel (RH_PDRV, interpolated) x the top */
{
    uint32_t i = RH_VPOS_I[vel], f = RH_VPOS_F[vel], t = rh_top_q12(ki, vel);
    int32_t pd = (int32_t)RH_PDRV[ki][i] + ((((int32_t)RH_PDRV[ki][i + 1] - (int32_t)RH_PDRV[ki][i]) * (int32_t)f) >> 8);
    return t >= 4096u ? pd : (int32_t)(((uint32_t)pd * t) >> 12);   /* (pd < 2^16) */
}
static int32_t rh_pdrv_at(uint32_t ki, uint32_t vel)   /* ... at least 16 (a ratio's divisor) */
{
    int32_t pd = rh_pd(ki, vel);
    return pd < 16 ? 16 : pd;
}
/* the pickup drive's output gain law at this velocity (Q16, <= 1), and the kill level in that gain: the voice ends where
 * its output falls under half an LSB, as before (kill was set for the key's gain); then the level make-up. From the
 * voice gain before them (gain0): the note-on, and a re-strike louder than the ringing note (rh_load_pulse) */
static void rh_drv_level(rh_voice_t *v, uint32_t ki, uint32_t vel)
{
    const rh_key_t *k = v->k;
    uint32_t kk = (uint32_t)(k->kill >> 3), q;
    int32_t g = rh_drive_g(ki, vel, v->lt);
    v->gq = (uint16_t)g;
#ifdef FR_HQ
    hq_v[v - rv].dq = rh_dyn(ki + RH_KEY_LO, vel, v->lt);   /* (the live contact keeps the level map: hq_pulse_gain) */
#endif
    {
        uint32_t i = RH_VPOS_I[vel], f = RH_VPOS_F[vel];
        int32_t pa = (int32_t)RH_PAMP[ki][i] + ((((int32_t)RH_PAMP[ki][i + 1] - (int32_t)RH_PAMP[ki][i]) * (int32_t)f) >> 8);
        int32_t pd = rh_pd(ki, vel);
        uint64_t r = ((uint64_t)(((uint32_t)(pa < 1 ? 1 : pa) << 12) / (uint32_t)(pd < 1 ? 1 : pd)) * RH_DRV_IB[ki]) >> 12;
        v->pq = r > 0xFFFFFFu ? 0xFFFFFFu : (uint32_t)r;     /* (32-bit divide) */
    }
    v->gain = (int32_t)(((int64_t)v->gain0 * g) >> 16);
    q = kk < 32768u ? (kk << 16) / (uint32_t)g : kk / (uint32_t)g;     /* 32-bit divides */
    v->kl = kk < 32768u ? (int32_t)(q > 0x7FFFFFFFu ? 0x7FFFFFFFu : q) : q >= 32768u ? 0x7FFFFFFF : (int32_t)(q << 16);
    {   /* the level make-up: the output gain x mk, the kill level / mk (the same output level ends the voice) */
        uint32_t mk = rh_drv_mkt(ki, vel), kl = (uint32_t)v->kl;
        int64_t gm = ((int64_t)v->gain * mk) >> 12;
        v->mk = mk;
        v->gain = gm > 0x7FFFFFFF ? 0x7FFFFFFF : (int32_t)gm;
        v->kl = (int32_t)(kl < (1u << 19) ? (kl << 12) / mk : (kl / mk) << 12);
    }
}
static uint32_t rh_vel_old;                    /* a re-strike: the ringing strike's velocity (rh_note_on, for rh_load_pulse) */
/* a harder re-strike at another level than the ringing note's (the gain law x the make-up): the voice takes the new strike's
 * gain, the ringing note's mode states are scaled by the old gain over the new at once, so its output goes on with no
 * step (as MAP's rh_vm_restrike; the pickup's last flux value taken again from the scaled tip), and the new strike gets
 * its own full force: it plays as a fresh strike over the ringing note. Kept at the note-on's gain with the force
 * scaled by the ratio, the pickup swung at the old strike's scale: a hard re-strike of a soft note played 4-5 dB under
 * a fresh one (C4 v64 -> v127, the pedal down), and with the force capped at 1 up to 16 dB under (A5 v64 -> v127).
 * The scale is capped at the drive's force ratio (new over old) when it grows, as MAP's at X_new / X_old: never swung
 * harder than the new strike */
static void rh_drv_restrike(rh_voice_t *v, uint32_t ki, uint32_t vel)
{
#ifndef RH_NO_HD
    uint32_t go = (uint32_t)v->hd_g0;         /* (the glide's base: rh_hd_restrike puts the offset back to it) */
#else
    uint32_t go = (uint32_t)v->gain;
#endif
    uint32_t gn, mx, sh, r, m;
    int64_t u = v->off;
    rh_drv_level(v, ki, vel);
#ifdef FR_HQ
    hq_v[v - rv].drl = hq_v[v - rv].dr;       /* the modes now hold this strike's units: the live force goes to them at its
                                                * own physical-to-drive ratio (hq_pulse_gain: dr / drl = 1) */
#endif
    gn = (uint32_t)v->gain;
    if (go == gn || !gn) return;
    mx = go > gn ? go : gn;
    sh = mx >= 65536u ? 16u - (uint32_t)__builtin_clz(mx) : 0u;      /* both >> sh under 2^16 */
    r = ((go >> sh) << 15) / ((gn >> sh) ? (gn >> sh) : 1u);         /* old / new, Q15 (32-bit divide) */
    if (r > 32768u) {
        uint32_t rc = ((uint32_t)rh_pdrv_at(ki, vel) << 15) / (uint32_t)rh_pdrv_at(ki, rh_vel_old ? rh_vel_old : vel);
        if (r > rc) r = rc > 32768u ? rc : 32768u;
    }
    for (m = 0; m < RH_NM; m++) {
        rh_ms_t *s = &v->m[m];
        if (!s->live) continue;
        s->x = sat28i((int32_t)(((int64_t)s->x * r) >> 15)); s->y = sat28i((int32_t)(((int64_t)s->y * r) >> 15));
        s->ex = s->ey = 0;
        u += s->x;
    }
    v->lvl = (int32_t)(((int64_t)v->lvl * r) >> 15); v->lvl0 = (int32_t)(((int64_t)v->lvl0 * r) >> 15);
    if (u > 0x7FFFFFFF) u = 0x7FFFFFFF;
    if (u < -0x7FFFFFFF) u = -0x7FFFFFFF;
    v->Hprev = rh_hermite(RH_PU_PHIM(v->k->fam), u * u);
}
#endif
static void rh_load_pulse(rh_voice_t *v, uint32_t vel)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS), i = RH_VPOS_I[vel], f = RH_VPOS_F[vel], l = RH_VLAY_I[vel];
    const uint16_t *pa = RH_PAMP[key];
    const uint16_t *pr = RH_PRATE[key];               /* (+ RH_PRATE_B[key]: the key's base) */
    int32_t w = RH_VLAY_W[vel];
    rh_pz(v->pbuf, &RH_PZB, RH_PZI(key, l));
    if (w)          /* the layer crossfade, once per point (O2): the same integers the force loop computed per sample
                     * (a + ((a2 - a) w >> 15), between a and a2: an int16), so the force is byte-identical */
        rh_pz_mix(v->pbuf, &RH_PZB, RH_PZI(key, l + 1u < RH_NLAYER ? l + 1u : RH_NLAYER - 1u), w);
    v->ptab = v->pbuf;
    v->pamp = (int32_t)pa[i] + ((((int32_t)pa[i + 1] - (int32_t)pa[i]) * (int32_t)f) >> 8);
#ifdef RH_DRIVE
    if (RH_IS_MAP(v)) v->pphys = v->pamp;             /* MAP: the physical force is the modes' force */
    else
    {   /* the note's level law (rh_left's measure of the note: the physical force was it before the drive), and the
         * excursion law's force for the modes (the same interpolation) */
        const uint16_t *pl = RH_PLEV[key];
        v->pphys = (int32_t)pl[i] + ((((int32_t)pl[i + 1] - (int32_t)pl[i]) * (int32_t)f) >> 8);
        v->pamp = rh_pd(key, vel);                    /* (the clean top's cut in it: rh_top_q12) */
    }
#endif
    v->prate = (uint32_t)((int32_t)RH_PRATE_B[key] + pr[i] + ((((int32_t)pr[i + 1] - (int32_t)pr[i]) * (int32_t)f) >> 8)) << 4;  /* Q12 -> Q16 */
#ifdef FR_HQ
    hq_pulse(v, vel);
#endif
#ifdef RH_DRIVE
    if (!RH_IS_MAP(v))
    {   /* the drive. A harder re-strike takes its own level (rh_drv_restrike) and its full force. A softer one keeps the
         * note-on's output gain (the tine still rings through it: a step would click), so the force takes the ratio of
         * the gain law x the make-up, this velocity's over the note-on's, capped at 1 as a product (either alone may
         * pass 1); before the HAMMER scaling and the HQ live force, which cap it. Never above 1: a softer strike of a
         * note ringing from a harder one (E2 v110 -> v64) took the force up by the gain law's ratio and swung the tine
         * past a fresh strike's, a burst of bark (+25 dB > 5 kHz, the pedal down); capped, it plays at its own swing,
         * under the ringing note's gain. At the note-on the ratio is 1, exactly. num / den: Q16 x Q12, under 2^36
         * (the make-up < 2^20 with the clean top's gain back), scaled down to 15 bits for the 32-bit divide */
        uint64_t num = (uint64_t)rh_drive_g(key, vel, v->lt) * rh_drv_mkt(key, vel), den = (uint64_t)v->gq * v->mk;
        uint32_t sh;
        int32_t a = (int32_t)v->pamp;
        if (num != den && vel > rh_vel_old) rh_drv_restrike(v, key, vel);
        else if (num < den && den) {
            uint32_t hi = (uint32_t)(den >> 32);
            sh = hi ? 49u - (uint32_t)__builtin_clz(hi)
                    : den >= 32768u ? 17u - (uint32_t)__builtin_clz((uint32_t)den) : 0u;   /* den >> sh in [2^14, 2^15) */
            a = (int32_t)(((uint32_t)v->pamp * (uint32_t)(num >> sh)) / (uint32_t)(den >> sh));   /* < 2^16 x 2^15 */
        }
        v->pamp = a > 65535 ? 65535 : a < 1 ? 1 : a;   /* mul15s: 32767 x 65535 < 2^31 (gen_rhodes: the headroom) */
    }
#endif
    {   /* HAMMER knob x TYPE tips scale the contact rate; the force scales with it so the impulse (the hammer's
         * momentum) is kept: a softer tip pushes longer and less hard, it does not give more energy */
        uint32_t h = (uint32_t)(((uint64_t)(uint32_t)rh_ham * (uint32_t)RH_TYPE[rh_type & 3u].ham) >> 16);
        v->prate = (uint32_t)(((uint64_t)v->prate * h) >> 16);
        v->pamp = (int32_t)(((int64_t)v->pamp * h) >> 16);
        if (v->pamp > 32767) v->pamp = 32767;   /* mul15s range; the drive too (0.9.10, the user: clean at every
                                                * velocity): a TYPE's harder tip (DYNO x1.2) swings the tine no further
                                                * than 0.9.9's full scale (65535 let DYNO v127 fold: H3 +3 dB over
                                                * 0.9.9's, tests/py/test_dry_distortion.py) */
#ifdef RH_DRIVE
        v->pphys = (int32_t)(((int64_t)v->pphys * h) >> 16);
        if (v->pphys > 32767) v->pphys = 32767;
#endif
    }
#ifdef FR_HQ
    hq_pulse_gain(v);
#endif
    v->ppos = 0;
#ifdef DINGHY_PLUGIN
    fr_ofs_apply(&v->ppos, &v->pdly, v->prate, rh_ofs);
#endif
}

static inline __attribute__((always_inline)) int32_t rh_hermite_i(const int32_t *T, int64_t s)   /* (rh_hd_step: the
                                                * same integers through rh_hd_flux) */
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
static int32_t rh_hermite(const int32_t *T, int64_t s) { return rh_hermite_i(T, s); }
/* rh_hd_step's flux value at the gap q (Q25): rh_hermite_i(T, q * q), the same integer, with its clamp taken in 32 bits
 * (q * q >= RH_S_MAX_HI << 44 exactly when |q| > RH_HD_QMAX: index and fraction at the table's last point). The 64-bit
 * form kept its limit on the stack and compiled the clamp to a predicated 64-bit load of it: the FM-1's core 0 faulted
 * there in the audio interrupt (EMU_MSG bit 0, rh_hd_step+0x158, the 0.9.10 RC in soak rows 276 / 277). Here nothing
 * 64-bit is loaded from the stack (tools/build.py checks rh_hd_step; tests/py/test_rh_hd_flux.py the integers) */
#define RH_HD_QMAX 189812531u                   /* floor(sqrt(2^55 - 1)) */
_Static_assert(RH_S_MAX_HI == 2048u, "RH_HD_QMAX: sqrt(RH_S_MAX_HI << 44)");
static inline __attribute__((always_inline)) int32_t rh_hd_flux(const int32_t *T, int32_t q)
{
    uint32_t aq = q < 0 ? 0u - (uint32_t)q : (uint32_t)q, i, t;
    int32_t p0, p1, m0, m1, c2, c3, a;
    const int32_t *r;
    if (aq > RH_HD_QMAX) { i = RH_S_MAX_HI - 1u; t = 0x3FFFFFu; }
    else {
        uint64_t s = (uint64_t)aq * aq;
        i = (uint32_t)(s >> 44);
        t = (uint32_t)(s >> 22) & 0x3FFFFFu;
    }
    r = T + 2u * i;
    p0 = r[0]; m0 = r[1]; p1 = r[2]; m1 = r[3];
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
    /* the clamp (s >> 44, s >> 22 of the limit); its RH_REG32s keep the two constants in this rare branch: without
     * them the compiler built them every sample and selected (pickup loop 77 -> 71 instructions per 2 samples) */
    if (__builtin_expect(hi >= (RH_S_MAX_HI << 12), 0)) { i = RH_S_MAX_HI - 1u; t = 0x3FFFFF; RH_REG32(i); RH_REG32(t); }
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
        mix[0] += (int32_t)(((int64_t)(Hp - H) * gain) >> 24);   /* (the table is stored negated: gen_rhodes) */
        Hp = rh_hermite_u(T, xp[1] + off, 1);
        RH_REG32(gain);
        mix[1] += (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
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
        o = (int32_t)(((int64_t)(Hp - H) * gain) >> 24);
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
#ifdef RH_VMAP
/* the velocity method MAP's blend weight (x 87) per TYPE at the key anchors RH_TWB_K, linear between them, held beyond */
static int32_t rh_tone_wb1(uint32_t ka, uint32_t vel)
{
    const int16_t *W = RH_TONE_WB[rh_type & 3u][ka];
    return (int32_t)W[vel >> 3] + ((((int32_t)W[(vel >> 3) + 1u] - (int32_t)W[vel >> 3]) * (int32_t)(vel & 7u)) >> 3);
}
static int32_t rh_tone_wb(uint32_t note, uint32_t vel)
{
    uint32_t i;
    int32_t a, b;
    if (note <= RH_TWB_K[0]) return rh_tone_wb1(0u, vel);
    for (i = 1; i < RH_TWB_N; i++)
        if (note < RH_TWB_K[i]) {
            a = rh_tone_wb1(i - 1u, vel); b = rh_tone_wb1(i, vel);
            return a + (b - a) * (int32_t)(note - RH_TWB_K[i - 1u]) / (int32_t)(RH_TWB_K[i] - RH_TWB_K[i - 1u]);
        }
    return rh_tone_wb1(RH_TWB_N - 1u, vel);
}
#endif
static int32_t __attribute__((noinline)) rh_tone_gain(int32_t gain, int32_t o64, int32_t o, uint32_t vel, uint32_t fam,
                                                    uint32_t ki, uint32_t map)
{   /* (o64, o: capped by the caller, at the family's own cap) */
    const uint16_t *As = RH_TONE_A[fam & 1u][rh_type & 3u][0], *Ah = RH_TONE_A[fam & 1u][rh_type & 3u][1];
    int32_t a64, a, s, h;
    uint32_t g;
#ifdef RH_VMAP
    if (map) {                                     /* the velocity method MAP: its own curves and blend (rh_tone_b.h) */
        int32_t w = rh_tone_wb(ki + RH_KEY_LO, vel);
        As = RH_TONE_AB[fam & 1u][rh_type & 3u][0]; Ah = RH_TONE_AB[fam & 1u][rh_type & 3u][1];
        s = rh_tone_a(As, o64); h = rh_tone_a(Ah, o64); a64 = s + (h - s) * w / 87;
        s = rh_tone_a(As, o); h = rh_tone_a(Ah, o); a = s + (h - s) * w / 87;
        if (a < 1) a = 1;
        if (a64 < 1) a64 = 1;                      /* (w past 87: the curves extrapolated) */
        g = ((uint32_t)a64 << 14) / (uint32_t)a;   /* Q14; a64 < 2^18 (w <= 5 x 87): fits */
        return (int32_t)(((int64_t)gain * g) >> 14);
    }
#else
    (void)map;
#endif
#ifdef RH_TONE_W_Q
    /* S1a: with the pickup drive each key swings its own way at each velocity, so the blend is per TYPE, key and
     * velocity anchor (tone_level.py fit_w), x RH_TONE_W_Q, extrapolated past the two curves where the swing is past them */
    const uint8_t *wt = RH_TONE_W[rh_type & 3u][ki];
    uint32_t vi = RH_VPOS_I[vel], vf = RH_VPOS_F[vel] > 255u ? 255u : RH_VPOS_F[vel];
    int32_t w = (int32_t)wt[vi] + ((((int32_t)wt[vi + 1u] - (int32_t)wt[vi]) * (int32_t)vf) >> 8) - RH_TONE_W_Q;
    s = rh_tone_a(As, o64); h = rh_tone_a(Ah, o64); a64 = s + (h - s) * w / RH_TONE_W_Q;
    s = rh_tone_a(As, o); h = rh_tone_a(Ah, o); a = s + (h - s) * w / RH_TONE_W_Q;
    if (a64 < 1) a64 = 1;
    if (a64 > 0x3FFFF) a64 = 0x3FFFF;                              /* (a64 << 14) in 32 bits */
    if (a < 1) a = 1;
    g = ((uint32_t)a64 << 14) / (uint32_t)a;
    if (g > (16u << 14)) g = 16u << 14;                            /* +24 dB at most */
#else
    int32_t w = vel <= 40u ? 0 : vel >= 127u ? 87 : ((int32_t)vel - 40) * ((int32_t)vel - 40) / 87;
    (void)ki;
    s = rh_tone_a(As, o64); h = rh_tone_a(Ah, o64); a64 = s + (h - s) * w / 87;
    s = rh_tone_a(As, o); h = rh_tone_a(Ah, o); a = s + (h - s) * w / 87;
    if (a < 1) a = 1;
    g = ((uint32_t)a64 << 14) / (uint32_t)a;                       /* Q14; a64 < 2^15: no overflow */
#endif
    {
        int64_t r = ((int64_t)gain * g) >> 14;
        return r > 0x7FFFFFFF ? 0x7FFFFFFF : (int32_t)r;
    }
}
#endif

/* the harmonic decay against the strike (0.9.10; the target: the 2nd harmonic's T60 over the fundamental's
 * ~0.8 after a soft strike, 0.2-0.4 after a hard one in the bass and the middle, and a hard-struck note darkening over
 * its first 2 s). The 2nd harmonic is the pickup's curvature on V1's swing (H2 ~ A^2: T60 H2/H1 0.5 at every velocity);
 * the tine's rest point in the pickup field moves a little after the strike instead: a soft strike's toward the pole
 * axis (the curvature grows as the note rings: H2 decays slower), a hard strike's away from it (H2 falls faster). The
 * voice gain follows the pickup's slope at the offset (the fundamental's small-signal gain, from the flux table), so
 * the fundamental keeps the strike's level and decay.
 * Decided at the strike (end, lags, rates from the velocity and the key); after a 0.4 s wait a step every RH_HD_SUBS
 * sub-blocks:
 * two lags, the gain, and the last sample's flux at the new offset (Hprev: the output has no step). Keys 72 up: none
 * (tapered over 69..72). -DRH_NO_HD: without it (the A/B reference, byte-identical to before). The Move (FR_HQ) too:
 * the glide runs on keys under 72, where every HQ voice uses the Classic pickup (the 2x one from key 85) */
#ifndef RH_NO_HD
#define RH_HD 1
#ifndef RH_HD_SUBS                             /* a glide step every this many 32-sample sub-blocks */
#define RH_HD_SUBS 2
#endif
#ifndef RH_HD_DS                               /* soft: the glide's end toward the axis (Q25, 1/32" gap: 5/8 gap), far, */
#define RH_HD_DS 20971520                      /* so the glide is near linear until the cap (RH_OFF_MAX) stops it */
#endif
#ifndef RH_HD_RS                               /* soft: the glide's rate per unit of V1's decay (d), Q30 per step: H2 */
#define RH_HD_RS (3 * RH_HD_SUBS)              /* over H1 rises as V1 decays (d ~ 1 / T60) */
#endif
#ifndef RH_HD_DH                               /* hard: the glide's end away from the axis (5/8 gap) */
#define RH_HD_DH (-20971520)
#endif
#define RH_HD_AH (1731000 * RH_HD_SUBS)        /* hard: both lags' rate per step (Q30): tau 0.45 s */
#define RH_HD_WAIT (552u / RH_HD_SUBS)                       /* the glide waits 0.4 s (the strike's first 30-500 ms: its level and
                                                * colour stay the strike's) */
#define RH_HD_STEPS (16536u / RH_HD_SUBS)      /* 12 s of steps */
#define RH_HD_DU 524288                        /* the slope's half step (1/64 gap) */
static inline __attribute__((always_inline)) uint32_t rh_hd_isqrt(uint32_t n)          /* floor(sqrt(n)), bit by bit (32-bit, no divide); the first bit
                                                * from the leading zeros (the same result, no scan down to it) */
{
    uint32_t r = 0, b;
    if (!n) return 0;
    b = 1u << ((31u - (uint32_t)__builtin_clz(n)) & ~1u);
    while (b) {
        if (n >= r + b) { n -= r + b; r = (r >> 1) + b; } else r >>= 1;
        b >>= 2;
    }
    return r;
}
static void rh_hd_target(rh_voice_t *v, uint32_t n, uint32_t vel)   /* the glide's end and lags for a strike */
{
    int32_t w, r, a1;
    r = n <= 69u ? 32768 : n >= 72u ? 0 : (int32_t)(72u - n) * 32768 / 3;          /* the register, Q15 */
    w = vel <= 40u ? 0 : vel >= 110u ? 32768 : ((int32_t)vel - 40) * 32768 / 70;  /* soft 0 .. hard 1, Q15 */
    a1 = v->k->m[RH_V1].d * RH_HD_RS;           /* d < 2^17 at every key: no overflow */
    if (a1 > Q30 / 64) a1 = Q30 / 64;
    v->hd_d = (int32_t)(((((int64_t)RH_HD_DS * (32768 - w) + (int64_t)RH_HD_DH * w) >> 15) * r) >> 15)
              >> RH_FAM_SH[v->k->fam];
    v->hd_a1 = (int32_t)(((int64_t)a1 * (32768 - w) + (int64_t)RH_HD_AH * w) >> 15);
    v->hd_a2 = (int32_t)(((int64_t)Q30 * (32768 - w) + (int64_t)RH_HD_AH * w) >> 15);
    v->hd_w = RH_HD_WAIT;
    v->hd_n = v->hd_d ? RH_HD_STEPS : 0;
}
static void rh_hd_init(rh_voice_t *v, uint32_t n, uint32_t vel)
{
    v->hd_y1 = v->hd_y2 = 0; v->hd_ph = RH_HD_SUBS;
    v->hd_off0 = v->off; v->hd_g0 = v->hd_gc = v->gain;
    rh_hd_target(v, n, vel);
}
/* a step of the glide: a leaf in RAM (FR_RAM; 0.9.10 cpu: from XIP flash it waited on the cache, 10 voices x 4 steps
 * a block), no call: the five flux values (the slopes at the strike's offset and here, then the last sample's at the
 * new offset) through one inlined rh_hd_flux (rh_hermite's integers) in a loop (RAM is short), the root and the divide inline. The gain at the
 * offset now: the base x the pickup's fundamental gain at the strike's offset over the one here, both at V1's swing now
 * (the secant over +-a: the large swing's own, not the small-signal slope) */
static FR_RAM __attribute__((noinline)) int32_t rh_hd_step(rh_voice_t *v, int32_t Hp)
{
    const rh_key_t *k = v->k;
    const rh_ms_t *m1 = &v->m[RH_V1];
    const int32_t *T;
    int32_t o, mx = RH_OFF_MAX >> RH_FAM_SH[k->fam], a, s0, s1, q[5], h[5];
    uint64_t a2;
    uint32_t r, i;
    int64_t g;
    if (v->hd_w) { v->hd_w--; return Hp; }
    v->hd_n--;
    v->hd_y1 += (int32_t)(((int64_t)(v->hd_d - v->hd_y1) * v->hd_a1) >> 30);
    v->hd_y2 += (int32_t)(((int64_t)(v->hd_y1 - v->hd_y2) * v->hd_a2) >> 30);
    o = v->hd_off0 + v->hd_y2;
    if (o > mx) {                                         /* at the cap: the lags wait there (a re-strike's glide
                                                           * starts from the offset itself) */
        o = mx; v->hd_y2 = mx - v->hd_off0;
        if (v->hd_y1 > v->hd_y2) v->hd_y1 = v->hd_y2;
    }
    if (o == v->off) return Hp;
    v->off = o;
    T = RH_PU_PHIM(k->fam);
    a2 = (uint64_t)((int64_t)m1->x * m1->x + (int64_t)m1->y * m1->y) >> 22;   /* (|x|, |y| < 2^28: < 2^35) */
    a = (int32_t)(rh_hd_isqrt(a2 > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)a2) << 11);
    if (a < RH_HD_DU) a = RH_HD_DU;
    if (a > (1 << 26)) a = 1 << 26;                                /* (2 gap) */
    q[0] = v->hd_off0 + a; q[1] = v->hd_off0 - a; q[2] = o + a; q[3] = o - a; q[4] = o + rh_x[SUB - 1u];
#if defined(__clang__)                                             /* (gcc, the Move's and Linux hosts', has no such pragma) */
#pragma clang loop unroll(disable)
#endif
    for (i = 0; i < 5u; i++) h[i] = rh_hd_flux(T, q[i]);
    s0 = h[0] - h[1]; s0 = s0 < 0 ? -s0 : s0;                      /* |Phi~((o0 + a)^2) - Phi~((o0 - a)^2)| */
    s1 = h[2] - h[3]; s1 = s1 < 0 ? -s1 : s1;
    {   /* both under 2^17, the same shift (the leading zeros of the larger: no loop) */
        uint32_t mxs = (uint32_t)(s0 > s1 ? s0 : s1), sh = mxs >= (1u << 17) ? 15u - (uint32_t)__builtin_clz(mxs) : 0u;
        s0 >>= sh; s1 >>= sh;
    }
    r = s1 < 1 ? 4u << 14 : ((uint32_t)s0 << 14) / (uint32_t)s1;   /* Q14 (a 32-bit divide) */
    if (r > (4u << 14)) r = 4u << 14;                              /* +12 dB at most */
    g = ((int64_t)v->hd_g0 * r) >> 14;
    v->gain = v->hd_gc = g > 0x7FFFFFFF ? 0x7FFFFFFF : (int32_t)g;
    return h[4];
}
/* a re-strike of a sounding note: the offset goes back to the strike's, and the glide starts again for the new strike
 * (gliding on from a soft strike's offset, near the axis, the new hard strike barked +13 dB > 5 kHz in its first 6 ms
 * against a fresh one; a hard strike's, far off, left the old note brighter for 250 ms: tests/py/test_rh_velmap.py
 * test_restrike_no_click). The fundamental's gain goes back with it (the glide kept it level), the last flux value is
 * taken again from the tip at the strike's offset (the output has no step). A gain the re-strike set anew (MAP: its G,
 * at the strike's offset) becomes the glide's base */
static void rh_hd_restrike(rh_voice_t *v, uint32_t vel)
{
    uint32_t m;
    int64_t u = v->hd_off0;
    if (v->gain != v->hd_gc) v->hd_g0 = v->gain;
    v->hd_y1 = v->hd_y2 = 0; v->off = v->hd_off0; v->gain = v->hd_gc = v->hd_g0;
    for (m = 0; m < RH_NM; m++) if (v->m[m].live) u += v->m[m].x;
    if (u > 0x7FFFFFFF) u = 0x7FFFFFFF;
    if (u < -0x7FFFFFFF) u = -0x7FFFFFFF;
    v->Hprev = rh_hermite(RH_PU_PHIM(v->k->fam), u * u);
    rh_hd_target(v, (uint32_t)(v->k - RH_KEYS) + RH_KEY_LO, vel);
}
#else
static void rh_hd_init(rh_voice_t *v, uint32_t n, uint32_t vel) { (void)v; (void)n; (void)vel; }
static void rh_hd_restrike(rh_voice_t *v, uint32_t vel) { (void)v; (void)vel; }
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
#ifdef RH_VMAP
/* the velocity method MAP (0.9.10, the knee map): per key and velocity the tip's swing scale X (Q11, on every mode's
 * force gain b) and the voice's output gain scale G (Q11), RH_VM_X / RH_VM_G [key][v / 8] (points every 8 velocity
 * steps, 0 .. 128, linear in between; tools/gen_velmap.py measures them on the host). The bark is flat to a knee that
 * rises with the register, then steep; the fundamental saturates in the bass. The physical force (RH_PAMP) and the
 * force gains before the drive's scale (b x RH_DRV_IB / 4096) and the gain before its own (x RH_DRV_SG) are the
 * engine without the drive, which the map was fitted on. At the strike only: no per-sample cost */
#include "rh_velmap.h"
#define RH_VM_Q 11                                 /* the tables' fraction bits (X, G up to 32: +30 dB) */
#define RH_VM_ONE (1 << RH_VM_Q)
#if defined(DINGHY_HOST) && defined(RH_VM_PROBE)        /* host only (tools/gen_velmap.py): the tables from a file */
#include <stdio.h>
#include <stdlib.h>
static uint16_t rh_vm_tab[2][RH_NKEY][17];
static int rh_vm_loaded;
static const uint16_t *rh_vm_row(uint32_t t, uint32_t key)
{
    if (!rh_vm_loaded) {
        const char *p = getenv("RH_VM_TAB");
        FILE *f = p ? fopen(p, "rb") : 0;
        uint32_t i, j, q;
        for (q = 0; q < 2u; q++) for (i = 0; i < RH_NKEY; i++) for (j = 0; j < 17u; j++) rh_vm_tab[q][i][j] = RH_VM_ONE;
        if (f) { if (fread(rh_vm_tab, sizeof rh_vm_tab, 1, f) != 1) fprintf(stderr, "RH_VM_TAB short\n"); fclose(f); }
        rh_vm_loaded = 1;
    }
    return rh_vm_tab[t][key];
}
#define RH_VM_ROW(t, key) rh_vm_row(t, key)
#else
#define RH_VM_ROW(t, key) ((t) ? RH_VM_G[key] : RH_VM_X[key])
#endif
#if !defined(RH_VM_PROBE) && !defined(RH_NO_VM_MK)
#include "rh_velmap_mk.h"                      /* MAP's level make-up (tools/velmap_makeup.py --write) */
#endif
static int32_t rh_vm(uint32_t t, uint32_t key, uint32_t vel)     /* t 0: X, 1: G; Q11 */
{
    const uint16_t *r = RH_VM_ROW(t, key);
    uint32_t i = vel >> 3, f = vel & 7u;
    int32_t a = (int32_t)r[i] + ((((int32_t)r[i + 1] - (int32_t)r[i]) * (int32_t)f) >> 3);
    {   /* the clean top (rh_top_q12): X x the cut, G x the gain back (rh_top_g12) */
        uint32_t tq = t ? rh_top_g12(key, vel) : rh_top_q12(key, vel);
        if (tq != 4096u) {
            a = (int32_t)(((uint64_t)(uint32_t)a * tq) >> 12);      /* (a < 2^16, tq <= 2^16) */
            if (a > 65535) a = 65535;
            if (a < 1) a = 1;
        }
    }
#ifdef RH_VM_MK_Q
    if (t) {   /* G x the level make-up (Q12, the notes back to the reference release's level at mf and above, never
                * under it below: an output gain only, the colour is the map's) */
        a = (int32_t)(((uint32_t)a * rh_mk_q12(RH_VM_MK[key], vel)) >> 12);
        if (a > 65535) a = 65535;
        if (a < 1) a = 1;
    }
#endif
    return a;
}
static int32_t rh_vm_g(uint32_t key, uint32_t vel)          /* MAP: G x the dynamics stage (rh_dyn), Q11 */
{
    uint32_t d = rh_dyn(key + RH_KEY_LO, vel, 0u);
    int32_t g = rh_vm(1u, key, vel);
    return d >= 32768u ? g : (int32_t)(((uint32_t)g * d) >> 15);   /* (G < 2^16, d <= 2^15: fits) */
}
/* MAP: the voice gain x the gain before the drive's (RH_DRV_SG, Q16) x G, the kill level / both (the same output
 * level ends the voice), the noises' scale X x G (at most 1), the steal score's swing (the state / X) */
static void rh_vm_level(rh_voice_t *v, int32_t vx, int32_t vg)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS), sg = RH_DRV_SG[key], kk = (uint32_t)(v->k->kill >> 3), q;
    int64_t g = ((((int64_t)v->gain0 * sg) >> 16) * vg) >> RH_VM_Q;
    uint64_t d = ((uint64_t)sg * (uint32_t)(vg > 16 ? vg : 16)) >> RH_VM_Q;     /* sg x G, Q16 */
    int32_t n = (int32_t)(((uint32_t)vx * (uint32_t)vg) >> RH_VM_Q);
    v->vg = vg;
    v->gain = g > 0x7FFFFFFF ? 0x7FFFFFFF : (int32_t)g;
    if (d < 1u) d = 1u;
    if (d > 0xFFFFFFFFu) d = 0xFFFFFFFFu;
    {   /* kk / d, Q16 divisor: kk normalised under 2^15 so the 32-bit divide fits, the shift put back (saturating) */
        uint32_t sh = 0;
        uint64_t r;
        while (kk >= 32768u) { kk >>= 1; sh++; }
        q = (kk << 16) / (uint32_t)d;
        r = (uint64_t)q << sh;
        v->kl = (int32_t)(r > 0x7FFFFFFFu ? 0x7FFFFFFFu : r);
    }
    v->vn = n < RH_VM_ONE ? n : RH_VM_ONE;
    v->pq = (uint32_t)((4096u << RH_VM_Q) / (uint32_t)(vx > 16 ? vx : 16));
}
/* MAP: a mode's force gain, the drive's scale taken out (RH_DRV_IB / 4096: the engine the map was fitted on), then
 * BARK, BELL, the TYPE, and the map's swing X (Q11); saturating */
static int32_t rh_vm_b(const rh_mode_t *km, uint32_t m, const rh_type_t *ty, uint32_t key, int32_t x)
{
    int64_t b = ((int64_t)km->b * RH_DRV_IB[key]) >> 12;
    b = (((b * rh_bark_s) >> 16) * ty->bark) >> 16;
    if (m == RH_O1 || m == RH_O2) b = (((b * ty->bell) >> 16) * rh_bell) >> 16;
    b = (b * x) >> RH_VM_Q;
    return b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
}
static void rh_vm_kap(rh_voice_t *v, uint32_t n, const rh_type_t *ty, int32_t vx);   /* (after RH_KAP) */
/* MAP, a re-strike of a sounding note: the new strike gets the map's swing X and gain G of its own velocity. The
 * ringing note was swung for the old G: its mode states are scaled by G_old / G_new at once (before the new force),
 * so its fundamental (the near-linear part of the pickup) goes on at the same level with no step, and the pickup's
 * last flux value is taken again from the scaled tip position (the output is a flux difference). Only the old note's
 * colour moves, under the new strike. The scale is capped at X_new / X_old when it grows (G_old > G_new): a note
 * ringing from below the knee re-struck far above it (E3 v30 -> v127: G_old / G_new 7.5) was swung ~3x harder than a
 * fresh v127 strike, a burst of bark (+13 dB > 5 kHz with the pedal down); capped, its fundamental steps down under
 * the much louder new strike instead. G unchanged: the force gains only (X) */
static void __attribute__((noinline)) rh_vm_restrike(rh_voice_t *v, uint32_t vel, uint32_t vel_old)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS), m;
    int32_t x = rh_vm(0u, key, vel), g = rh_vm_g(key, vel);   /* (G x rh_dyn) */
    const rh_type_t *ty = &RH_TYPE[rh_type];
    if (g != v->vg) {
        uint32_t r = ((uint32_t)v->vg << 15) / (uint32_t)(g > 16 ? g : 16);    /* G_old / G_new, Q15 (vg < 2^16) */
        int64_t u = v->off;
        if (r > 32768u) {                       /* never swung harder than the new strike: r <= X_new / X_old */
            int32_t xo = rh_vm(0u, key, vel_old);
            uint32_t rc = ((uint32_t)x << 15) / (uint32_t)(xo > 16 ? xo : 16);   /* (X < 2^16: fits) */
            if (r > rc) r = rc > 32768u ? rc : 32768u;
        }
        for (m = 0; m < RH_NM; m++) {
            rh_ms_t *s = &v->m[m];
            if (!s->live) continue;
            s->x = sat28i((int32_t)(((int64_t)s->x * r) >> 15)); s->y = sat28i((int32_t)(((int64_t)s->y * r) >> 15));
            s->ex = s->ey = 0;
            u += s->x;
        }
        v->lvl = (int32_t)(((int64_t)v->lvl * r) >> 15); v->lvl0 = (int32_t)(((int64_t)v->lvl0 * r) >> 15);
        if (u > 0x7FFFFFFF) u = 0x7FFFFFFF;
        if (u < -0x7FFFFFFF) u = -0x7FFFFFFF;
        v->Hprev = rh_hermite(RH_PU_PHIM(v->k->fam), u * u);
        rh_vm_level(v, x, g);
        rh_vm_kap(v, key + RH_KEY_LO, ty, x);
    } else {
        int32_t n = (int32_t)(((uint32_t)x * (uint32_t)v->vg) >> RH_VM_Q);
        v->vn = n < RH_VM_ONE ? n : RH_VM_ONE;
        v->pq = (uint32_t)((4096u << RH_VM_Q) / (uint32_t)(x > 16 ? x : 16));
    }
    for (m = 0; m < RH_NM; m++) if (v->m[m].live) v->m[m].b = rh_vm_b(&v->k->m[m], m, ty, key, x);
}
#endif
#ifdef RH_VMAP
static void rh_vm_kap(rh_voice_t *v, uint32_t n, const rh_type_t *ty, int32_t vx)   /* MAP: S4's kap for its X */
{
    v->m[RH_V1].kap = 0;
#ifndef RH_NO_S4
    if (n < RH_KAP_HI) {
        uint32_t g = (uint32_t)(((uint64_t)(uint32_t)rh_bark_s * (uint32_t)ty->bark) >> 16), ig;
        g = (uint32_t)(((uint64_t)g * (uint32_t)vx) >> RH_VM_Q);
        if (g < 4096u) g = 4096u;
        ig = 0x80000000u / g;
        v->m[RH_V1].kap = (int32_t)(((uint64_t)RH_KAP[n - RH_KEY_LO] * ig * ig) >> 30);
        v->m[RH_V1].kap <<= 2u * RH_FAM_SH[v->k->fam];
    }
#else
    (void)n; (void)ty; (void)vx;
#endif
}
#endif
static void rh_init(rh_voice_t *v, uint32_t note, uint32_t vel)
{
    const rh_type_t *ty = &RH_TYPE[rh_type];
    uint32_t m, n = note < RH_KEY_LO ? RH_KEY_LO : note > RH_KEY_LO + RH_NKEY - 1u ? RH_KEY_LO + RH_NKEY - 1u : note;
    const rh_key_t *k = &RH_KEYS[n - RH_KEY_LO];
    int32_t u, t = rh_tune_now();
    uint32_t cp = rh_cc_pub;                    /* the cache, if it holds this note-on's coefficients */
    const rh_cc_t *cc = cp && rh_cc_st[cp - 1u][0] == t && rh_cc_st[cp - 1u][1] == rh_k_decay
                        && rh_cc_st[cp - 1u][2] == rh_k_rel ? rh_cc[cp - 1u][n - RH_KEY_LO] : 0;
#ifdef RH_VMAP
    int32_t vx = RH_VM_ONE, vg = RH_VM_ONE;
    v->vm = (uint8_t)(rh_touch_mode != 0);
    if (v->vm) { vx = rh_vm(0u, n - RH_KEY_LO, vel); vg = rh_vm_g(n - RH_KEY_LO, vel); }   /* MAP: X, G x rh_dyn (Q11) */
#endif
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
#ifdef RH_VMAP
        if (v->vm) { s->b = rh_vm_b(km, m, ty, n - RH_KEY_LO, vx); continue; }   /* MAP: the map's swing */
#endif
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
            v->gain = rh_tone_gain(v->gain, o64 > mx ? mx : o64, v->off, vel, k->fam, n - RH_KEY_LO, RH_IS_MAP(v));
        }
#endif
    }
#ifdef RH_VMAP
    if (v->vm) { v->gain0 = v->gain; v->mk = 4096u; rh_vm_level(v, vx, vg); }   /* MAP: the gain, the kill, the noises, the score */
    else
#endif
#ifdef RH_DRIVE
    { v->gain0 = v->gain; v->lt = (uint8_t)(rh_touch_mode != 0); rh_drv_level(v, n - RH_KEY_LO, vel); }   /* the drive: the gain law, the make-up, the kill */
#endif
    u = v->off;
    v->Hprev = rh_hermite(RH_PU_PHIM(k->fam), (int64_t)u * u);
    v->alive = 1; v->held = 1; v->ramp = 0; v->w_tgt = 0;
    v->fade = 32768; v->pend = 0; v->felt_n = 0; v->felt_lp = 0;
    v->age = ++rh_ages;
    v->life = k->life;
    v->m[RH_V1].kap = 0;
#ifdef FR_HQ
    hq_voice_init(v);
#endif
#ifndef RH_NO_S4                                          /* -DRH_NO_S4: without it (the A/B reference) */
    if (n < RH_KAP_HI) {                                  /* S4: kap / (BARK x the TYPE's bark)^2 */
        uint32_t g = (uint32_t)(((uint64_t)(uint32_t)rh_bark_s * (uint32_t)ty->bark) >> 16), ig;
#ifdef RH_VMAP
        if (v->vm) g = (uint32_t)(((uint64_t)g * (uint32_t)vx) >> RH_VM_Q);   /* MAP: / its swing X too */
#endif
        if (g < 4096u) g = 4096u;
        ig = 0x80000000u / g;                             /* 1 / g, Q15 (g Q16) */
        v->m[RH_V1].kap = (int32_t)(((uint64_t)RH_KAP[n - RH_KEY_LO] * ig * ig) >> 30);
        v->m[RH_V1].kap <<= 2u * RH_FAM_SH[k->fam];      /* S8: the 1/16" family's x is in its own (double) gap, so
                                                           * the same swing is half the x: kap x 4 keeps the offset */
#ifdef RH_DRIVE
        if (!RH_IS_MAP(v))
            v->m[RH_V1].kap = (int32_t)(((uint64_t)(uint32_t)v->m[RH_V1].kap * RH_KAP_X[n - RH_KEY_LO]) >> 16);   /* the
                                                           * drive's swing at v127 is m127 x the physical one */
#endif
    }
#endif
    rh_hd_init(v, n, vel);
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
 * RAMP_SUB (5 sub-blocks, 3.6 ms) on C3..B4 (48..71), one more every 4 keys below C3 to 7 (5.1 ms) from A#2 (42)
 * down, 4 on C5..B5, 3 (2.2 ms) from C6 (84) up. (Until 2026-11-08 7 / 10 / 6 / 5: the felt acted 10-20 ms late
 * against a real tine piano's, which damps within ~5 ms of the key-up) */
static uint32_t rh_settle(uint32_t note)
{
    if (note < 48u) { uint32_t d = (50u - note) >> 2; return RAMP_SUB + (d > 2u ? 2u : d); }
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
#ifdef RH_DRIVE
    int32_t f = v->pphys, l = v->lvl, l0 = v->lvl0;  /* (the physical force: pamp is the drive's excursion law) */
#else
    int32_t f = v->pamp, l = v->lvl, l0 = v->lvl0;
#endif
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
#ifdef RH_DRIVE
#ifdef RH_VMAP
    if (!rh_touch_mode)
#endif
    a = (int32_t)RH_NZ_S(a);                            /* (the notes' level; MAP played at the noises' own) */
    a = rh_dyn_gain(a, rh_dyn(n, vel, rh_touch_mode != 0));   /* the dynamics stage, as the note's own */
#endif
    l = RH_VLAY_I[vel];
    if (RH_VLAY_W[vel] >= 16384 && l + 1u < RH_NLAYER) l++;
    if (rh_kn_tab) {                                    /* another strike of this block: one knock, the forces add */
        a += rh_kn_amp;
        if (a > 65535) a = 65535;                       /* x the pulse (<= 32767): still under 2^31 */
        rh_kn_amp = a;
        if (n >= rh_kn_note) return;                    /* the lowest key's pulse (the longest, the heaviest hammer) */
    }
    rh_kn_amp = a;
    rh_kn_tab = rh_pz(rh_kn_buf, &RH_PZB, RH_PZI(key, l)); rh_kn_rate = prate; rh_kn_pos = 0; rh_kn_note = (uint8_t)n;
#ifdef DINGHY_PLUGIN
    rh_kn_dly = (uint16_t)(rh_ofs >> 8);
#endif
}

static void rh_start_pend(rh_voice_t *v)                /* a stolen voice is gone: start the note waiting for it */
{
    uint32_t rel = v->pend;                             /* 1 held, 2 let go, 3 let go by an all-off */
    rh_init(v, v->pend_note, v->pend_vel);
    if (rel >= 2u) { v->held = 0; v->rgain = 32768; rh_set_damper(v, rel == 3u ? 32768 : rh_pedal_w); }
    if (rel == 3u && v->life > RH_OFF_LIFE) v->life = RH_OFF_LIFE;
}

#ifndef RH_NA
#define RH_NA RH_NV                                     /* the voices a strike may take (FR_C1 builds, frodhes.c: fewer
                                                         * once core 1 is held: the one-core fallback) */
#endif
static int rh_strike(uint32_t note, uint32_t vel)      /* 0: every voice already waits for a note; try next block */
{
    uint32_t i, pick = RH_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < RH_NA && rv[i].pend; i++) ;
    if (i == RH_NA) return 0;                           /* (more than RH_NV steals in one block: BUGHUNT #21) */
#ifdef DINGHY_HOST
    rh_host_vel = (uint8_t)vel;                         /* (host: the velocity that reached the engine, frodhes.c
                                                         * "engvel"; RESPONSE never rewrites it, tests/py/test_vel_layers.py) */
#endif
    rh_knock_strike(note, vel);
    for (i = 0; i < RH_NV; i++)                         /* the same note still sounding: re-strike, superpose */
        if (rv[i].alive && !rv[i].pend && rv[i].note == note && rv[i].fade == 32768) {
            rh_voice_t *v = &rv[i];
            uint32_t m, vo = v->vel;                    /* (the old strike's velocity: rh_vm_restrike) */
            for (m = 0; m < RH_NM; m++) if (rh_mode_runs(v->k, m)) v->m[m].live = 1;
            v->vel = (uint8_t)vel; v->held = 1; v->age = ++rh_ages; v->life = v->k->life; v->lvl0 = v->lvl;
            rh_set_damper_n(v, 0, 1);                   /* the felt off at once: the damper lifts as the key goes
                                                         * down, before the hammer arrives (a ramp under the strike
                                                         * stepped the modes' coefficients each sub-block while the
                                                         * force swung them: a click, +16 dB > 5 kHz with the map) */
#ifdef RH_DRIVE
            rh_vel_old = vo;                            /* (unused at a note-on: the ratio is 1) */
            v->lt = (uint8_t)(rh_touch_mode != 0);      /* RESPONSE at this strike (a change: the re-strike's rescale
                                                         * of the ringing modes takes the level step, rh_drv_restrike) */
#endif
            rh_load_pulse(v, vel);                      /* (with the pickup drive: the level of its own, rh_drv_restrike) */
#ifdef RH_VMAP
            if (v->vm) rh_vm_restrike(v, vel, vo);      /* (after the pulse: the HQ live contact reads the tine as
                                                         * it rang, hq/rhodes_hq.c hq_live) */
#else
            (void)vo;
#endif
            rh_hd_restrike(v, vel);                     /* (after the map's gain: the glide's base) */
            return 1;
        }
    for (i = 0; i < RH_NA; i++)
        if (!rv[i].alive) { rh_init(&rv[i], note, vel); return 1; }
    for (i = 0; i < RH_NA; i++) {                       /* steal: the quietest released voice, else the oldest */
        uint32_t m, sum = 0, score;
        if (rv[i].pend) continue;
        for (m = 0; m < RH_NM; m++) sum += (uint32_t)rv[i].m[m].peak >> 4;
        sum = RH_OUT_SUM(&rv[i], sum);
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
#ifdef DINGHY_PLUGIN
        rh_ofs = ev_ofs[ev_r & 31u];
        ev_ofs[ev_r & 31u] = 0;                         /* (a note kept for the next block starts at its start) */
#endif
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
                        v->ptab = rh_pz(v->pbuf, &RH_PZB, RH_PZI(key, 0));
#ifdef RH_DRIVE
                        {   /* th is a physical force: the modes get it at the strike's drive ratio (pamp / pphys) */
                            int32_t pp = v->pphys < 1 ? 1 : v->pphys, a;
                            th = th > 32767 ? 32767 : th;
                            a = (int32_t)(((uint32_t)th * (uint32_t)v->pamp) / (uint32_t)pp);
                            v->pphys = th;
                            th = a;
                        }
#endif
                        v->pamp = th > 32767 ? 32767 : th;
                        v->prate = (uint32_t)(RH_PRATE_B[key] + RH_PRATE[key][1]) << 4;
                        v->ppos = 0;
#ifdef DINGHY_PLUGIN
                        v->pdly = 0;
#endif
                    }
                    if (rh_pedal_w && rh_felt) {             /* 8 ms of felt noise */
                        int32_t g = rh_left(v) * (32768 / RH_FELT_FULL);
#ifdef RH_VMAP
                        if (v->vm) g = (int32_t)(((uint32_t)(g > 32768 ? 32768 : g) * (uint32_t)v->vn) >> RH_VM_Q);   /* MAP:
                                                         * x X x G, the note as heard */
                        else
#endif
#ifdef RH_DRIVE
                        {
                            g = (int32_t)RH_NZ_S(g > 32768 ? 32768 : g);   /* (the notes' level) */
                            if (g > 32768) g = 32768;
                            g = rh_dyn_gain(g, rh_dyn(v->note, v->vel, v->lt));   /* x the dynamics stage (MAP: in vn) */
                        }
#endif
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
#ifdef RH_VMAP
                    int32_t lmb = -1, eb = 0;            /* MAP's notes: as heard (x X x G), at the noises' own level */
                    for (i = 0; i < RH_NV; i++)
                        if (rv[i].alive && rv[i].vm) {
                            g = (int32_t)(((uint32_t)rh_left(&rv[i]) * (uint32_t)rv[i].vn) >> RH_VM_Q);
                            if (g > lmb) lmb = g;
                        }
                    if (lmb >= 0) { g = lmb >> 2; eb = g >= 32768 ? rh_pnz : (int32_t)(((uint32_t)rh_pnz * (uint32_t)g) >> 15); }
#endif
                    for (i = 0; i < RH_NV; i++)
                        if (rv[i].alive && !RH_IS_MAP(&rv[i])) {
                            g = rh_left(&rv[i]);
#ifdef RH_DRIVE
                            g = rh_dyn_gain(g, rh_dyn(rv[i].note, rv[i].vel, rv[i].lt));   /* the note as heard, x the
                                                         * dynamics stage as its knock and felt (0.9.10: under FULL's
                                                         * -9 dB soft end a v1 note's pedal-up thud peaked +0.5 dB over
                                                         * it, test_release_never_louder) */
#endif
                            if (g > lm) lm = g;
                        }
                    g = lm < 0 ? 32768 : lm >> 2;
                    rh_pn_n = RH_PN_LEN; rh_pn_env = g >= 32768 ? rh_pnz : (int32_t)(((uint32_t)rh_pnz * (uint32_t)g) >> 15);
#ifdef RH_DRIVE
                    rh_pn_env = (int32_t)RH_NZ_S(rh_pn_env);   /* (the notes' level) */
#endif
#ifdef RH_VMAP
                    if (lmb >= 0 && lm < 0) rh_pn_env = eb;          /* only MAP's notes sound */
                    else if (lmb >= 0 && eb > rh_pn_env) rh_pn_env = eb;
                    else if (lmb < 0 && lm < 0 && rh_touch_mode) rh_pn_env = rh_pnz;   /* into silence: MAP's level */
#endif
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
#ifdef DINGHY_PLUGIN
    rh_ofs = 0;                                         /* (a note that waited for a steal's fade: the block's start) */
#endif
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
    int32_t *xp = rh_x, *xe = rh_x + SUB;
    RH_REG32(xe);
    do {
        int64_t t;
        RH_REG32(b); y += (int32_t)(((int64_t)xp[SUB] * b) >> 20);   /* (rh_F[j]) */
        RH_REG32(e);
        X += (int64_t)e * y;
        x = (int32_t)(X >> 30);
        RH_REG32(eps);
        t = ey - (int64_t)eps * y - (int64_t)e * x;
        y += (int32_t)(t >> 30); ey = (uint32_t)t & 0x3FFFFFFFu;
        if (first) *xp = x; else *xp += x;
        if (iabs(x) > pk) pk = iabs(x);
        if (iabs(y) > pk) pk = iabs(y);
        xp++;
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
#ifdef RH_DRIVE
    if (a2 > ((int64_t)Q30 << 6)) a2 = (int64_t)Q30 << 6;   /* the drive swings past 1 gap (kap is scaled down by
                                                           * RH_KAP_X): held at 64 gap^2, a2 kap < 2^52 */
#else
    if (a2 > Q30) a2 = Q30;
#endif
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
        int32_t o = (int32_t)(((int64_t)(Hp - H) * gain) >> 24);
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
#ifdef DINGHY_PLUGIN
        if (v->pdly) {                                    /* the plugin: zeros until the note's own sample */
            uint32_t z = v->pdly < SUB ? v->pdly : SUB;
            for (; j < z; j++) rh_F[j] = 0;
            v->pdly = (uint16_t)(v->pdly - z);
        }
        if (j < SUB)
#endif
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
#ifdef FR_HQ
    if (hq_v[v - rv].os) Hp = hq_pickup(v, mix, side, Hp); else
#endif
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
#ifdef RH_HD
    if (v->hd_n && !--v->hd_ph) { v->hd_ph = RH_HD_SUBS; Hp = FR_FAR(rh_hd_step)(v, Hp); }   /* the harmonic decay glide */
#endif
    v->Hprev = Hp;
    if (v->fade == 0) {                                   /* a steal finished fading: start the waiting note */
        v->alive = 0;
        if (v->pend) rh_start_pend(v);
        return;
    }
    v->lvl = sum; if (sum > v->lvl0) v->lvl0 = sum;
    if (!v->ptab && !v->felt_n && sum < RH_KILL(v, k)) {   /* died away; a stolen voice dying in its fade still
                                                           * starts the note waiting for it (it ended, and the note was
                                                           * lost: BUGHUNT #21) */
        v->alive = 0;
        if (v->pend) rh_start_pend(v);
        return;
    }
    for (m = 1; m < RH_NM; m++)                           /* sleep overtone modes that have died away */
        if (v->m[m].live && !v->ptab && v->m[m].peak < RH_KILL(v, k)) {
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
        sum = RH_OUT_SUM(&rv[i], sum);
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
#ifdef DINGHY_PLUGIN
    if (rh_kn_tab && rh_kn_dly) {                         /* the plugin: the knock from its note's own sample */
        uint32_t d = rh_kn_dly < n ? rh_kn_dly : n;
        rh_kn_dly = (uint16_t)(rh_kn_dly - d); mix += d; n -= d;
    }
    if (rh_kn_tab && n)
#else
    if (rh_kn_tab)
#endif
    {                                                     /* the pulse to its end (the samples left, counted first:
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

/* TPDF dither where the engine's Q20 mix is cut to Q15 (0.9.7, GitHub #16): plain rounding left a decaying held note's
 * cut error tied to the note itself, its odd harmonics (H3, H5) standing at a fixed ~-113 dBFS while H1 fell from -42
 * to -76 dBFS. A triangular dither of +-1 LSB (Q15) before the cut turns that error into a steady noise floor:
 * x + d - 15 >> 5 with d the sum of two uniform 0..31 (one LCG step, its top bits: 27..31 and 22..26), so d - 15 is
 * +16 on average, the rounding of before. The mid's (rh_q15) and the side's (SPREAD, its last stage) have their own
 * generator (uncorrelated); none while the engine is silent (rh_render's idle branch writes exact 0; the side only
 * with rh_dith_on), so the silence is still exact digital zero. Integers only, deterministic (the host renders) */
#define RH_DITH(r) ((r) = (r) * 1664525u + 1013904223u, (int32_t)((r) >> 27) + (int32_t)(((r) >> 22) & 31u) - 15)
#define RH_DITH_M0 0x2545F491u
#define RH_DITH_S0 0x9E3779B9u
static uint32_t rh_dith_m = RH_DITH_M0, rh_dith_s = RH_DITH_S0;   /* the mid's, the side's (seeded again whenever
                                                           * the engine is silent: rh_render's idle branch) */
static int32_t rh_dith_on;                                /* -1: the side dithers this block; 0: +16 (silent) */

/* one Q28 DF1 biquad with error feedback over a block, in -> out (may be the same buffer); st = m1, m2, o1, o2, e.
 * The pickup load (RLC) and the TYPE's amp / speaker biquads are this same form; a cascade run stage by stage gives
 * the same integers as sample by sample. Its own function: the state and coefficients stay in registers */
static inline __attribute__((always_inline)) void rh_df1_t(const int32_t *in, int32_t *out, uint32_t n,
                                                           const int32_t *c, int32_t *st, int q15)
{
    int32_t c0 = c[0], c1 = c[1], c2 = c[2], c3 = -c[3], c4 = -c[4];   /* - c3 o1 = (-c3) o1: 5 MACs */
    int32_t m1 = st[0], m2 = st[1], o1 = st[2], o2 = st[3], e = st[4];
    const int32_t *ie = in + n;
    uint32_t r = rh_dith_s;
    int32_t dm = rh_dith_on;
    while (in < ie) {
        int32_t m = *in++, o;
        int64_t acc;
        RH_REG32(c0); RH_REG32(c1); RH_REG32(c2); RH_REG32(c3); RH_REG32(c4);
        acc = (int64_t)c0 * m + (int64_t)c1 * m1 + (int64_t)c2 * m2 + (int64_t)c3 * o1 + (int64_t)c4 * o2 + e;
        o = (int32_t)(acc >> 28); e = (int32_t)(acc & 0xFFFFFFF);
        m2 = m1; m1 = m; o2 = o1; o1 = o;
        if (q15) { int32_t d = RH_DITH(r); *out++ = (o + 16 + ((d - 16) & dm)) >> 5; }   /* (the last stage: Q15,
                                                           * dithered as rh_q15) */
        else *out++ = o;
    }
    if (q15) rh_dith_s = r;
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

/* Q20 -> Q15 in place, TPDF dithered (RH_DITH); returns max |round(x)| of the undithered Q20 (as before the dither:
 * the silence test mx < 2 ends a tail at the same block) */
static FR_RAM __attribute__((noinline)) int32_t rh_q15(int32_t *p, uint32_t n)
{
    int32_t hi = 0, lo = 0, *pe = p + n, a, b;
    uint32_t r = rh_dith_m;
    while (p < pe) {
        int32_t x = *p;
        if (x > hi) hi = x;
        if (x < lo) lo = x;
        *p++ = (x + RH_DITH(r)) >> 5;
    }
    rh_dith_m = r;
    a = (hi + 16) >> 5; b = (15 - lo) >> 5;               /* (|(x + 16) >> 5| at the extremes) */
    return a > b ? a : b;
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
    const int32_t *a = &rh_st[0][0], *b = &sd_st[0][0];   /* (flat: 15 each) */
    uint32_t i, z = 0;
    for (i = 0; i < 15u; i++) z |= (uint32_t)(a[i] | b[i]);
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
#ifdef FR_HQ
    hq_block();
#endif
    rh_events();
    ty = rh_txf_type();
    if (rh_bend_t != rh_bend_used || rh_rt_pend) rh_retune();
    if (rh_shed_req) { rh_shed_req = 0; rh_shed(); }
    for (i = 0; i < RH_NV && !rv[i].alive; i++) ;
    idle = i == RH_NV && rh_quiet_blocks && !rh_pn_n;    /* (the pedal's thud plays into a silent engine) */
    if (i == RH_NV && !idle && !rh_pn_n && !rh_kn_tab && !rh_txf.n && rh_chain_zero())
        idle = 1;                                         /* (no voice, no noise, every filter at rest: its output is
                                                           * 0 already; the dither must not make it 1 LSB of noise:
                                                           * at boot and after a reset, before rh_quiet_blocks counts) */
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
        rh_dith_m = RH_DITH_M0; rh_dith_s = RH_DITH_S0;   /* (the dither starts over: a take from silence is the
                                                           * same integers every time) */
    } else {   /* pickup load: RLC low-pass on the mix, then the instrument's amp / speaker (nb biquads); Q20 -> Q15 */
        rh_chain(mono_out, rh_mono, n, rh_st, 0, ty);
        if (rh_txf.n) { rh_chain(rh_txf.buf, rh_mono, n, rh_txf.st, 0, rh_txf.ty); rh_txf_blend(mono_out, rh_txf.buf, n); }
        mx = FR_FAR(rh_q15)(mono_out, n);
    }
    rh_side_on = rh_side_tail != 0;
    rh_dith_on = idle ? 0 : -1;                           /* (the side dithers with the mid: not while silent) */
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
#if defined(FR_C1) && FR_C1
/* FR_C1 (engine.c eng_begin / eng_chunk / eng_end, frodhes.c app_audio): the block in chunks of frames [o, e), every
 * voice's frames of a chunk, then that chunk's post chain (the noises, RLC, body, Q15, the side), so core 1 runs one
 * chunk's effects while core 0 renders the next. The same integers as rh_render over the block: rh_voice_sub is per
 * voice and per 32-frame sub-block already (its state is the voice's), the filters and the dithers carry their state
 * from chunk to chunk, rh_begin decides what rh_render decides once a block (events, TYPE, retune, shed, idle, side) and
 * rh_end sums up what it sums up at the end. Two things cross voices within a block: the felt noise's LFSR (rh_lfsr,
 * consumed voice by voice, each felt voice starting where the one before it ends: rh_begin jumps it ahead by what each
 * will take, rh_lfsr_jump; a voice's felt ends early only at its steal fade's end, known from fade), and rh_ages (a
 * waiting note started at a steal's end takes the next age: rh_end gives those out again in voice order) */
static const uint16_t RH_LJ32[16] = {0x137B, 0x26F6, 0x4DEC, 0x9BD8, 0x5FB1, 0xBF62, 0x16C5, 0x2D8A, 0x5B14, 0xB628,
                                     0x0451, 0x08A2, 0x1144, 0x2288, 0x4510, 0x8A20};    /* bit b, 32 steps on */
static const uint16_t RH_LJ256[16] = {0x9AB8, 0x5D71, 0xBAE2, 0x1DC5, 0x3B8A, 0x7714, 0xEE28, 0xB451, 0x00A3, 0x0146,
                                      0x028C, 0x0518, 0x0A30, 0x1460, 0x28C0, 0x5180};   /* ... 256 steps on */
static uint32_t rh_lfsr_mat(const uint16_t *J, uint32_t r)    /* the 0xB400 LFSR is linear: the bits' images summed */
{
    uint32_t o = 0;
    for (; r; r >>= 1, J++) if (r & 1u) o ^= *J;
    return o;
}
static uint32_t rh_lfsr_jump(uint32_t r, uint32_t k)        /* k steps of r = (r >> 1) ^ (-(r & 1) & 0xB400) (also
                                                             * bars.c's bar_lfsr: its seed has bit 16, gone in 1 step) */
{
    for (; k && (r >> 16); k--) r = (r >> 1) ^ (-(r & 1u) & 0xB400u);
    for (; k >= 256u; k -= 256u) r = rh_lfsr_mat(RH_LJ256, r);
    for (; k >= 32u; k -= 32u) r = rh_lfsr_mat(RH_LJ32, r);
    for (; k; k--) r = (r >> 1) ^ (-(r & 1u) & 0xB400u);
    return r;
}
static uint32_t rhc_n, rhc_ty, rhc_felt, rhc_a0, rhc_lf_end;
static uint32_t rhc_lf[RH_NV];                  /* each felt voice's LFSR as it plays (rhc_felt: the voices with felt) */
static uint8_t rhc_idle;
static int32_t rhc_mx;
static uint32_t rhc_ds2;                        /* the side's crossfade chain's dither (rh_txf.n): rh_render runs it after
                                                 * the whole block's side, so it starts n steps of RH_DITH further on */
#ifdef DINGHY_HOST
static uint32_t rhc_lfe[RH_NV];                 /* (host: where each felt voice's LFSR must end, checked in rh_end) */
#endif
static void rh_begin(uint32_t n)
{
    uint32_t i, sd, r;
    rhc_n = n;
    rh_events();
    rhc_ty = rh_txf_type();
    if (rh_bend_t != rh_bend_used || rh_rt_pend) rh_retune();
    if (rh_shed_req) { rh_shed_req = 0; rh_shed(); }
    for (i = 0; i < RH_NV && !rv[i].alive; i++) ;
    rhc_idle = (uint8_t)(i == RH_NV && rh_quiet_blocks && !rh_pn_n);
    if (i == RH_NV && !rhc_idle && !rh_pn_n && !rh_kn_tab && !rh_txf.n && rh_chain_zero())
        rhc_idle = 1;
    for (i = 0, sd = 0; i < RH_NV; i++) sd |= rv[i].alive && rv[i].pan;
    if (sd) rh_side_tail = 4;
    rh_side_on = rh_side_tail != 0;             /* fixed for the block: the effects' first chunk reads it */
    rh_dith_on = rhc_idle ? 0 : -1;
    if (rhc_idle) { rh_dith_m = RH_DITH_M0; rh_dith_s = RH_DITH_S0; }   /* (rh_render: after the mid, before the side;
                                                 * the mid uses no dither in an idle block) */
    for (i = 0, r = rh_lfsr, rhc_felt = 0; i < RH_NV; i++) {
        const rh_voice_t *v = &rv[i];
        uint32_t ns = n, c;
        if (!v->alive || !v->felt_n) continue;
        if (v->fade != 32768) {                 /* the steal fade ends it (rh_pickup_fx) after the sub-block where the
                                                 * fade reaches 0: no felt after that */
            uint32_t s = ((uint32_t)(v->fade > 0 ? v->fade : 0) + (32768u / FADE_SAMPLES) - 1u) / (32768u / FADE_SAMPLES);
            uint32_t d = s ? ((s - 1u) / SUB + 1u) * SUB : SUB;
            if (d < ns) ns = d;
        }
        c = v->felt_n < ns ? v->felt_n : ns;
        rhc_felt |= 1u << i; rhc_lf[i] = r;
        r = rh_lfsr_jump(r, c);
#ifdef DINGHY_HOST
        rhc_lfe[i] = r;
#endif
    }
    rhc_lf_end = r;
    rhc_a0 = rh_ages; rhc_mx = 0;
    if (rh_txf.n && rh_side_tail) {             /* the LCG n steps on: x a^n + c (a^n - 1) / (a - 1) */
        uint32_t a = 1u, c = 0u;
        if (n == 256u) { a = 0x3E57C401u; c = 0x0292A500u; }
        else for (i = 0; i < n; i++) { a *= 1664525u; c = c * 1664525u + 1013904223u; }
        rhc_ds2 = rh_dith_s * a + c;
    }
}
static void rh_txf_blend_c(int32_t *x, const int32_t *nw, uint32_t o, uint32_t cnt)   /* rh_txf_blend from frame o */
{
    int32_t dw = 32768 / (int32_t)RH_TXF_BLK / (int32_t)rhc_n;
    int32_t w = (int32_t)(RH_TXF_BLK - rh_txf.n) * (32768 / (int32_t)RH_TXF_BLK) + (int32_t)o * dw;
    uint32_t j;
    for (j = 0; j < cnt; j++) { w += dw; x[j] += (int32_t)(((int64_t)(nw[j] - x[j]) * w) >> 15); }
}
static void rh_chunk(int32_t *mono_out, uint32_t o, uint32_t e)   /* frames [o, e) of the block: mono_out, rh_side */
{
    uint32_t i, sb, cnt = e - o;
    int32_t *mo = mono_out + o;
    for (i = o; i < e; i++) rh_mono[i] = 0;
    if (rh_side_tail) for (i = o; i < e; i++) rh_side[i] = 0;
    for (i = 0; i < RH_NV; i++) {
        rh_voice_t *v = &rv[i];
        if (!v->alive) continue;
        if (rhc_felt >> i & 1u) rh_lfsr = rhc_lf[i];
        for (sb = o; sb < e && v->alive; sb += SUB)
            rh_voice_sub(v, rh_mono + sb, rh_side + sb);
        if (rhc_felt >> i & 1u) rhc_lf[i] = rh_lfsr;
    }
    if (rh_kn_tab || rh_pn_n) FR_FAR(rh_noise)(rh_mono + o, cnt);   /* (from a sub-block's start: the same steps) */
    if (rhc_idle)
        for (i = 0; i < cnt; i++) mo[i] = 0;
    else {
        int32_t mx;
        rh_chain(mo, rh_mono + o, cnt, rh_st, 0, rhc_ty);
        if (rh_txf.n) { rh_chain(rh_txf.buf + o, rh_mono + o, cnt, rh_txf.st, 0, rh_txf.ty); rh_txf_blend_c(mo, rh_txf.buf + o, o, cnt); }
        mx = FR_FAR(rh_q15)(mo, cnt);
        if (mx > rhc_mx) rhc_mx = mx;
    }
    if (rh_side_tail) {
        int32_t *sp = rh_side + o;
        if (rh_txf.n) memcpy(rh_txf.sbuf + o, sp, cnt * sizeof *sp);
        rh_chain(sp, sp, cnt, sd_st, 1, rhc_ty);
        if (rh_txf.n) {
            uint32_t d = rh_dith_s;
            rh_dith_s = rhc_ds2;
            rh_chain(rh_txf.sbuf + o, rh_txf.sbuf + o, cnt, rh_txf.ss, 1, rh_txf.ty);
            rhc_ds2 = rh_dith_s; rh_dith_s = d;
            rh_txf_blend_c(sp, rh_txf.sbuf + o, o, cnt);
        }
    }
}
static void rh_end(void)                        /* after the block's last chunk: rh_render's end of block */
{
    uint32_t i, act = 0, held = 0;
    for (i = 0; i < RH_NV; i++) {
        rh_voice_t *v = &rv[i];
        if (v->alive && v->life && --v->life == 0 && v->fade == 32768) v->fade = 32767;
    }
#ifdef DINGHY_HOST
    for (i = 0; i < RH_NV; i++)
        if ((rhc_felt >> i & 1u) && rhc_lf[i] != rhc_lfe[i]) { fprintf(stderr, "C1 RH LFSR voice %u\n", (unsigned)i); abort(); }
#endif
    rh_lfsr = rhc_lf_end;
    if (rh_txf.n && rh_side_tail) rh_dith_s = rhc_ds2;   /* (where rh_render's second side chain leaves it) */
    if (rh_ages != rhc_a0) {                    /* waiting notes started this block: their ages in voice order */
        uint32_t a = rhc_a0, k = rh_ages - rhc_a0;
        for (i = 0; i < RH_NV; i++) if (rv[i].age - rhc_a0 - 1u < k) rv[i].age = ++a;
    }
    if (rh_side_tail && --rh_side_tail == 0) { memset(sd_st, 0, sizeof sd_st); memset(rh_txf.ss, 0, sizeof rh_txf.ss); }
    for (i = 0; i < RH_NV; i++)
        if (rv[i].alive) { act |= 1u << i; rh_notes[i] = rv[i].note; if (rv[i].held) held |= 1u << i; }
    if (!act && !rh_pn_n && rhc_mx < 2) {
        memset(rh_st, 0, sizeof rh_st);
        if (rh_txf.n) { rh_txf.run = rh_txf.ty; rh_txf.n = 0; }
        if (rh_quiet_blocks < 0xFFFFu) rh_quiet_blocks++;
    } else
        rh_quiet_blocks = 0;
    if (rh_txf.n && !--rh_txf.n) rh_txf_end();
    rh_held_bits = held;
    rh_active_bits = act;
}
#endif
#ifdef FR_HQ
#include "hq/rhodes_hq.c"
#endif
