/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes bar engine, its types, constants and state (bars.h); the code is bars.c. The engine: struck metal bars over tuned tubes (VIBES; marimba / xylophone later as data only).
 *
 * Per voice: a mallet force pulse (the BARK family x velocity shapes blended once at note-on) drives up to 8 modes
 * of one bar: V1 TUBE V2 V3 V4 T1 T2 X. V1 and the tube's fundamental are a two-way coupled pair (stiffness
 * coupling, the tube's pressure acts on the bar and the bar drives the tube); the motor's discs scale the coupling
 * and the tube's radiation per sub-block, ramped per sample. Every mode is the exact-pole MC3 resonator of the Rhodes
 * modes, run in its equivalent two-multiply form: x[n+1] = a1 x[n] - a2 x[n-1] + e u[n] with a1 = 2 - eps - e^2,
 * a2 = 1 - eps (the same poles and the same force-to-x transfer as MC3's x += e y, y += -eps y - e x, y += u), with
 * first-order error feedback (noise floor < -100 dBFS on every key and mode). Output = bar displacement + the tube's
 * radiation, times the key's gain: no pickup.
 *
 * Levels: each mode's amplitude comes once per block from its last two samples (x1^2 - a1 x1 x2 + a2 x2^2 =
 * A^2 sin^2 theta), in log2. A voice ends when its output falls under 1 LSB; upper modes sleep 54 dB under the
 * fundamental (faded over a sub-block).
 *
 * CPU, by construction: every block, a cost model of this block's work (voices, pulses, noise, the effects that
 * are on) gives the number of upper modes that fit under the 4800 us gate; when more are live, the least audible
 * go (their level re the loudest fundamental in the mix, less where a louder partial nearby masks them; a strike's
 * modes before they sound are ranked by their predicted level), faded over a sub-block, never cut.
 *
 * Its own voices, constants, summaries and render: nothing here changes the Rhodes engine (rhodes.c). It reuses the
 * Rhodes run-time coefficient path rh_coef() unchanged, the velocity layer tables and the main-loop voicing values
 * shared by both engines (DECAY, RELEASE, TUNE, the damper knock and felt noise levels). engine.c calls it.
 * Tables: firmware/gen/bars_tables.h (tools/gen_bars.py; the format of the plan's section 4.4). */

typedef struct {
    rh_mode_t m[8];             /* V1 TUBE V2 V3 V4 T1 T2 X; m[1] (TUBE): b = 0, drel = d */
    int32_t k_bt, k_tb;         /* Q30 coupling at BELL 64, discs open (same sign: a symmetric stiffness coupling) */
    int32_t wt;                 /* Q28 tube radiation weight (|wt| <= 4.0) */
    int32_t gain;               /* Q16 per-key output gain */
    int32_t kill;               /* Q25 */
    uint16_t life;              /* blocks */
    uint8_t band, flags;        /* flags bit0: outside the authentic range; bit1: T1/T2 present; bit2: sigma clamped;
                                 * bit3 (MARIMBA below C4): RING (bar_k_decay) scales the tube slot too */
} bar_key_t;
typedef struct {
    int16_t tube_cents;         /* the type's global tube offset (per-key values are already in m[1]) */
    int16_t mot_gc;             /* Q15 closed-disc residual g_c */
    int16_t tors_db;            /* Q15 torsion scale on b at the edge */
    uint8_t nbody, drive_row, sleep_rel, budget;
    uint8_t flags;              /* bit0 has_motor, bit1 has_damper (VIBES 3; params.c TYPE_FLAGS says the same) */
    uint8_t kn_tube_top;        /* KN_TUBE[t][127] in 1/100 (informational) */
} bar_type_t;
#include "bars_tables.h"
_Static_assert(BAR_NM == 8, "bars.c: 8 mode slots");

#define BAR_NV FR_VOICES                            /* voices: as the Rhodes (rhodes.c FR_VOICES, 9 by default) */
#define BAR_N 256u                                  /* the block */
#define BAR_SUB 32u
#define BAR_NSUB (BAR_N / BAR_SUB)
#define BAR_RAMP_SUB 7u
#define BAR_UP0 2u                                  /* first upper slot (V2) */
#define BAR_NUP (BAR_NM - BAR_UP0)
#define BAR_LZERO (-32768)                          /* log2 of nothing */
#define BAR_EFM 0x3FFFFFFF                          /* the error-feedback remainder (30 bits) */
_Static_assert(BAR_NV <= 32u, "summaries are 32-bit masks");

typedef struct {
    int32_t x1, x2, err;                            /* x[n], x[n-1] (Q25); the error-feedback remainder */
    int32_t k1, k2;                                 /* lo: c1 = eps + e^2, c2 = eps (Q32); hi: a1 = 2 - c1, a2 = 1 - eps (Q30) */
    int32_t e, eps, e0, eps0, e1, eps1, es, epss;   /* live, held pair, felt pair, crossfade start (Q30) */
    int32_t b, be;                                  /* Q30 force -> y, strike-position weighted; be = b e >> 30 */
    int32_t amp;                                    /* Q25 amplitude at the last block's end */
    int16_t lsin;                                   /* log2 sin(theta), Q8 (<= 0) */
    int16_t lpred;                                  /* the strike's predicted log2 amplitude (Q8), BAR_LZERO = none */
    uint8_t live, bin, fresh, lo;                   /* live 1, 2 = fades out over the next sub-block; 1/3-octave band;
                                                     * fresh: struck this block, its level not measured yet; lo: the
                                                     * Q32 form (c1 < 1/2: every mode under ~4.9 kHz, V1 and the tube
                                                     * always), else the Q30 form */
} bar_ms_t;

typedef struct {
    bar_ms_t m[BAR_NM];
    const bar_key_t *k;
    uint8_t note, vel, alive, held, ramp, pend, pend_note, pend_vel, struck, pred;
    int32_t w_tgt, fade;
    uint32_t age, life;
    int32_t gain, kbt, ktb, wt, kill1;              /* BELL-scaled coupling and radiation, discs open; kill1: 1 LSB */
    uint32_t ppos, prate;
    int32_t pamp;
    const int16_t *ptab;                            /* pbuf (the strike) or pthump (the damper's knock) */
    const int16_t *pthump;
    uint32_t prate_thump;
    int16_t pbuf[BAR_PLEN + 2];
    uint16_t felt_n;
    int32_t felt_lp;
} bar_voice_t;

static bar_voice_t bv[BAR_NV];
static int32_t bar_mono[BAR_N], bar_x[BAR_N], bar_tb[BAR_N], bar_F[BAR_N + 2];   /* F: the force << 12 */
static int32_t bar_nz_e, bar_nz_s[4];               /* contact noise: one envelope for every voice (one decay) */
static uint32_t bar_ages, bar_lfsr = 0x1D872u;
static int32_t bar_pedal_w = 32768;
static int32_t bar_bd_m[2][2], bar_bd_o[2][2], bar_bd_e[2];
static uint8_t bar_sub;                             /* the bar TYPE (index into BAR_TYPE): written only by engine.c
                                                     * eng_switch (ISR), so it never changes while a bar voice lives */
/* voicing written by the main loop (params.c), read at note-on */
static volatile int32_t bar_pos = 64;               /* TONE raw 0..127: strike position (64 = 0) */
static volatile int32_t bar_fam = 1 << 8;           /* KN_FAM[BARK]: family << 8 | Q8 fraction */
static volatile int32_t bar_tube = 32768;           /* KN_TUBE[BELL], Q15 */
static volatile int32_t bar_bell = 64;              /* BELL raw 0..127 (its level compensation, BAR_TUBEG) */
static volatile int32_t bar_mass = 4096, bar_massi = 4096;   /* KN_MASS / KN_MASSI[HAMMER], Q12 */
static volatile int32_t bar_nz;                     /* KN_NZ[NOISE], Q15 */
static volatile int32_t bar_k_decay = 65536, bar_k_rel = 65536;   /* DECAY_K[RING], DECAY_K[DAMPER], Q16 */
static volatile int32_t bar_thump, bar_felt;        /* KN_THUMP / KN_FELT[NOISE]: the damper's knock and felt noise */
static volatile int32_t bar_mute = 0;               /* MUTE (P_RELEASE, RELEASE * 258), Q15: on a type without a damper
                                                     * the key-up damper weight's scale (0 = rings out); not read yet */
static volatile uint8_t bar_mot_on, bar_mot_depth, bar_mot_shape;   /* TREM/WAH = MOTOR, DEPTH, SHAPE (0..127) */
static volatile uint32_t bar_mot_inc;               /* Q32 phase step per sample (the AM rate, or TREM SYNC's) */
static volatile uint8_t bar_budget_force;           /* developer (MIDI CC 9): 0 = the cost model, 1..49 = 0..48 upper
                                                     * modes, 50.. = no cap (CPU calibration on the FM-1) */
/* the motor: one global phase, gains per sub-block edge (ISR) */
static uint32_t bar_mot_ph;
static int32_t bar_mot_g[9] = {32768, 32768, 32768, 32768, 32768, 32768, 32768, 32768, 32768};
static const int32_t BAR_FAM_NZ[BAR_NFAM] = {2048, 8192, 14336, 22528, 32767};   /* Q15 per family (SOFT, YARN, the default,
                                                     * CORD, RUBBER), blended as the pulse: harder mallets click more,
                                                     * soft yarn hardly at all (the default's 14336 as before) */
/* ISR-written summaries */
static volatile uint32_t bar_active_bits, bar_held_bits, bar_quiet_blocks;
static volatile uint8_t bar_notes[BAR_NV];
static volatile uint32_t bar_shed_count;
static uint32_t bar_plan_us;                        /* the cost model's time for this block (bar_budget) */
static volatile uint32_t bar_prof_cap = 0xFFFFu, bar_prof_used;   /* since the last profiler query (CC 116): the lowest
                                                     * cap and the most live upper modes, in modes (x 8 = units) */
#ifdef DINGHY_HOST
static uint64_t bar_h_sat, bar_h_ms, bar_h_mul;     /* host counters: sat28 hits, mode-samples, int64 multiplies */
static uint32_t bar_h_blk_ms, bar_h_blk_mul, bar_h_blk_ns;   /* this block's; ns: the cost model's FM-1 time */
static int bar_h_nobudget, bar_h_nosleep;           /* host tests (S12 transparency): FRODHES_NOBUDGET / NOSLEEP */
static int bar_h_fxus = -1;                         /* FRODHES_FXUS: the effects' cost the budget plans with (us; RAW
                                                     * renders that budget as if the effects ran) */
static FILE *bar_h_pred;                            /* FRODHES_PREDLOG: predicted vs measured strike levels */
#define BAR_SLEEP_ON && !bar_h_nosleep
#else
#define BAR_SLEEP_ON
#endif
static uint32_t fx_cost_us(void);                   /* fx.c: the effects that run now, us per block */

/* the engine's functions (bars.c, compiled after every other file so the Rhodes and the effects keep their code
 * layout and their inlining: the FM-1 runs code from flash through a cache) */
static void bar_strike(uint32_t note, uint32_t vel);
static void bar_note_off(uint32_t note);
static void bar_pedal(uint32_t cc);
static void bar_all_off(void);
static void bar_shed(void);
static void bar_kill_all(void);
static int bar_quiet(void);
static uint32_t bar_cost_fixed(uint32_t *unit);
static void bar_render(int32_t *mono_out, uint32_t n);
