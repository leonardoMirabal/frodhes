/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes bar engine: struck metal bars over tuned tubes (VIBES; marimba / xylophone later as data only).
 *
 * Per voice: a mallet force pulse (the BARK family x velocity shapes blended once at note-on) drives up to 8 modes
 * of one bar: V1 TUBE V2 V3 V4 T1 T2 X. V1 and the tube's fundamental are a two-way coupled pair (stiffness
 * coupling, the tube's pressure acts on the bar and the bar drives the tube); the motor's discs scale the coupling
 * and the tube's radiation per sub-block, ramped per sample. The other modes are independent exact-pole coupled-form
 * resonators with first-order error feedback (as the Rhodes modes). Output = bar displacement + the tube's
 * radiation, times the key's gain: no pickup. Upper modes go to sleep when they fall 42 dB under the fundamental,
 * and a mode budget caps the live upper modes over all voices (the CPU).
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
    uint8_t band, flags;        /* flags bit0: outside F3..F6; bit1: T1/T2 present; bit2: sigma clamped */
} bar_key_t;
typedef struct {
    int16_t tube_cents;         /* the type's global tube offset (per-key values are already in m[1]) */
    int16_t mot_gc;             /* Q15 closed-disc residual g_c */
    int16_t tors_db;            /* Q15 torsion scale on b at the edge */
    uint8_t nbody, drive_row, sleep_rel, budget;
} bar_type_t;
#include "bars_tables.h"
_Static_assert(BAR_NM == 8, "bars.c: 8 mode slots");

#define BAR_NV 8u                                   /* voices: 8, as the Rhodes (the mode budget and the relative
                                                     * sleep are CPU protection for edge strikes with every effect on) */
#define BAR_SUB 32u
#define BAR_RAMP_SUB 7u
#define BAR_UP0 2u                                  /* first upper slot (V2) */
_Static_assert(BAR_NV <= 32u, "summaries are 32-bit masks");

typedef struct {
    int32_t x, y, ex, ey;                           /* Q25, MC3 states and error-feedback remainders */
    int32_t e, eps, e0, eps0, e1, eps1, es, epss;   /* live, held pair, felt pair, crossfade start (Q30) */
    int32_t b;                                      /* Q30 force -> y, strike-position weighted */
    int32_t peak;
    uint8_t live;
} bar_ms_t;

typedef struct {
    bar_ms_t m[BAR_NM];
    const bar_key_t *k;
    uint8_t note, vel, alive, held, ramp, pend, pend_note, pend_vel;
    int32_t w_tgt, fade;
    uint32_t age, life;
    int32_t gain, kbt, ktb, wt;                     /* BELL-scaled coupling and radiation, discs open */
    uint32_t ppos, prate;
    int32_t pamp;
    const int16_t *ptab;                            /* pbuf (the strike) or pthump (the damper's knock) */
    const int16_t *pthump;
    uint32_t prate_thump;
    int16_t pbuf[BAR_PLEN + 2];
    uint16_t felt_n, nz_n;
    int32_t felt_lp, nz_lvl;
} bar_voice_t;

static bar_voice_t bv[BAR_NV];
static int32_t bar_mono[256], bar_x[BAR_SUB], bar_F[BAR_SUB];
/* contact noise: every voice uses the same filter, so one filtered noise per block, weighted by the sum of the
 * voices' envelopes (10 multiplies a sample in all, not per voice) */
static int32_t bar_nzenv[256], bar_nz_s[4];
static uint8_t bar_nz_dirty;
static uint32_t bar_ages, bar_lfsr = 0x1D872u;
static int32_t bar_pedal_w = 32768;
static int32_t bar_bd_m[2][2], bar_bd_o[2][2], bar_bd_e[2];
static uint8_t bar_sub;                             /* main loop: the bar TYPE (index into BAR_TYPE) */
/* voicing written by the main loop (params.c), read at note-on */
static volatile int32_t bar_pos = 64;               /* TONE raw 0..127: strike position (64 = 0) */
static volatile int32_t bar_fam = 1 << 8;           /* KN_FAM[BARK]: family << 8 | Q8 fraction */
static volatile int32_t bar_tube = 32768;           /* KN_TUBE[BELL], Q15 */
static volatile int32_t bar_mass = 4096, bar_massi = 4096;   /* KN_MASS / KN_MASSI[HAMMER], Q12 */
static volatile int32_t bar_nz;                     /* KN_NZ[NOISE], Q15 */
static volatile int32_t bar_k_decay = 65536, bar_k_rel = 65536;   /* DECAY_K[RING], DECAY_K[DAMPER], Q16 */
static volatile int32_t bar_thump, bar_felt;        /* KN_THUMP / KN_FELT[NOISE]: the damper's knock and felt noise */
static volatile uint8_t bar_mot_on, bar_mot_depth, bar_mot_shape;   /* TREM/WAH = MOTOR, DEPTH, SHAPE (0..127) */
static volatile uint32_t bar_mot_inc;               /* Q32 phase step per sample (the AM rate, or TREM SYNC's) */
/* the motor: one global phase, gains per sub-block edge (ISR) */
static uint32_t bar_mot_ph;
static int32_t bar_mot_g[9] = {32768, 32768, 32768, 32768, 32768, 32768, 32768, 32768, 32768};
static const int32_t BAR_FAM_NZ[BAR_NFAM] = {8192, 14336, 22528, 32767};    /* Q15: harder mallets click more */
/* ISR-written summaries */
static volatile uint32_t bar_active_bits, bar_held_bits, bar_quiet_blocks;
static volatile uint8_t bar_notes[BAR_NV];
static volatile uint32_t bar_shed_count;
#ifdef DINGHY_HOST
static uint64_t bar_h_sat, bar_h_ms, bar_h_mul;     /* host counters: sat28 hits, mode-samples, int64 multiplies */
static uint32_t bar_h_blk_ms, bar_h_blk_mul, bar_h_blk_ns;   /* this block's; ns: the FM-1 cost proxy (P0 costs) */
static int bar_h_nobudget, bar_h_nosleep;           /* host tests (S12 transparency): FRODHES_NOBUDGET / NOSLEEP */
#define BAR_SLEEP_ON && !bar_h_nosleep
#else
#define BAR_SLEEP_ON
#endif

static int32_t bar_sat(int32_t v)
{
#ifdef DINGHY_HOST
    if (v > (1 << 28) - 1 || v < -((1 << 28) - 1)) bar_h_sat++;
#endif
    return sat28(v);
}

/* live upper slots over the alive voices, fading ones included (the mode budget counts these) */
static uint32_t bar_upper_live(void)
{
    uint32_t i, m, c = 0;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive)
            for (m = BAR_UP0; m < BAR_NM; m++) c += bv[i].m[m].live != 0;
    return c;
}

/* the mode budget: the most upper slots (V2 .. X) that run at once over the 8 voices. A hard cap by construction,
 * so the bar engine never needs the shedder. From the FM-1 costs (research/P0-cpu.md: 0.54 us per voice-sample
 * fixed, 0.17 per mode-sample, 0.33 the Rhodes pickup) as the host proxy counts them (engine.c; the proxy reads
 * 3130 us for the Rhodes' 8 bass v127 that measure 3500 us, so x 1.12): a block is 1388 us (idle, every effect and
 * the filter) + 1.12 x 256 x (8 x (0.54 + 0.06 gain) + 0.17 x (24 pair + U upper) + 0.68 noise) us. U = 11 gives
 * 4.69 ms at most: under the 4800 us gate with ~100 us to spare (and under the Rhodes' own 4.90 ms). The type's
 * table value can only lower it. */
#define BAR_BUDGET_MAX 11u
static uint32_t bar_budget_now(void)
{
    uint32_t b = BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u].budget;
#ifdef DINGHY_HOST
    if (bar_h_nobudget) return 255u;
#endif
    return b < BAR_BUDGET_MAX ? b : BAR_BUDGET_MAX;
}

static void bar_load_pulse(bar_voice_t *v, uint32_t vel)
{
    uint32_t key = (uint32_t)(v->k - BAR_KEYS), fam = (uint32_t)bar_fam, f = fam >> 8, f2, l, l2, i;
    int32_t a = (int32_t)(fam & 255u), w, pa, pa2, pr, pr2;
    uint32_t vi = RH_VPOS_I[vel], vf = RH_VPOS_F[vel];
    const int16_t *p11, *p12, *p21, *p22;
    if (f > BAR_NFAM - 1u) { f = BAR_NFAM - 1u; a = 0; }
    f2 = f + 1u < BAR_NFAM ? f + 1u : f;
    l = RH_VLAY_I[vel]; l2 = l + 1u < BAR_NLAYER ? l + 1u : l; w = RH_VLAY_W[vel];
    p11 = BAR_PULSE[v->k->band][f][l]; p12 = BAR_PULSE[v->k->band][f][l2];
    p21 = BAR_PULSE[v->k->band][f2][l]; p22 = BAR_PULSE[v->k->band][f2][l2];
    for (i = 0; i < BAR_PLEN + 2u; i++) {       /* hardness x velocity: one shape, once */
        int32_t A = p11[i] + (int32_t)(((int64_t)(p12[i] - p11[i]) * w) >> 15);
        int32_t B = p21[i] + (int32_t)(((int64_t)(p22[i] - p21[i]) * w) >> 15);
        v->pbuf[i] = (int16_t)(A + (((B - A) * a) >> 8));
    }
    pa = (int32_t)BAR_PAMP[key][f][vi] + ((((int32_t)BAR_PAMP[key][f][vi + 1] - (int32_t)BAR_PAMP[key][f][vi]) * (int32_t)vf) >> 8);
    pa2 = (int32_t)BAR_PAMP[key][f2][vi] + ((((int32_t)BAR_PAMP[key][f2][vi + 1] - (int32_t)BAR_PAMP[key][f2][vi]) * (int32_t)vf) >> 8);
    pr = (int32_t)BAR_PRATE[key][f][vi] + ((((int32_t)BAR_PRATE[key][f][vi + 1] - (int32_t)BAR_PRATE[key][f][vi]) * (int32_t)vf) >> 8);
    pr2 = (int32_t)BAR_PRATE[key][f2][vi] + ((((int32_t)BAR_PRATE[key][f2][vi + 1] - (int32_t)BAR_PRATE[key][f2][vi]) * (int32_t)vf) >> 8);
    pa += ((pa2 - pa) * a) >> 8;
    pr += (int32_t)(((int64_t)(pr2 - pr) * a) >> 8);
    v->prate = (uint32_t)(((uint64_t)((uint32_t)pr << 4) * (uint32_t)bar_mass) >> 12);   /* Q12 -> Q16, x mass */
    pa = (int32_t)(((int64_t)pa * bar_massi) >> 12);
    v->pamp = pa > 32767 ? 32767 : pa < 0 ? 0 : pa;
    v->ptab = v->pbuf;
    v->ppos = 0;
}

static void bar_init(bar_voice_t *v, uint32_t note, uint32_t vel)
{
    const bar_type_t *ty = &BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u];
    uint32_t m, n = note < BAR_KEY_LO ? BAR_KEY_LO : note > BAR_KEY_LO + BAR_NKEY - 1u ? BAR_KEY_LO + BAR_NKEY - 1u : note;
    const bar_key_t *k = &BAR_KEYS[n - BAR_KEY_LO];
    int32_t tone = bar_pos, s = bar_tube;
    uint32_t q = (uint32_t)tone * (16u * 256u) / 127u, pi = q >> 8, pf = q & 255u;
    const int16_t (*pos)[BAR_NM] = BAR_POS[k->band];
    v->k = k;
    for (m = 0; m < BAR_NM; m++) {
        bar_ms_t *ms = &v->m[m];
        const rh_mode_t *km = &k->m[m];
        int64_t b;
        int32_t w;
        ms->x = ms->y = ms->ex = ms->ey = 0;
        ms->peak = 0;
        ms->live = 0;
        ms->b = 0;
        if (!km->th) { ms->e = ms->eps = 0; continue; }
        rh_coef(km, km->d, rh_tune_t, m == 1u ? 65536 : bar_k_decay, &ms->e0, &ms->eps0);
        rh_coef(km, km->drel, rh_tune_t, m == 1u ? 65536 : bar_k_rel, &ms->e1, &ms->eps1);
        ms->e = ms->es = ms->e0; ms->eps = ms->epss = ms->eps0;
        w = pi < BAR_NPOS - 1u ? pos[pi][m] + (((pos[pi + 1u][m] - pos[pi][m]) * (int32_t)pf) >> 8) : pos[BAR_NPOS - 1u][m];
        b = ((int64_t)km->b * w) >> 14;
        if (m == 5u || m == 6u) b = tone > 64 ? (b * ty->tors_db) >> 15 : 0;   /* torsion: off the centreline only */
        ms->b = b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
        if (m == 0u) ms->live = 1;
        else if (m == 1u) ms->live = s != 0;                       /* BELL 0: no tube at all */
        else ms->live = ms->b != 0;                 /* bar_budget() trims, before the slot ever sounds */
    }
    v->note = (uint8_t)note; v->vel = (uint8_t)vel;
    v->gain = k->gain;
    v->kbt = (int32_t)(((int64_t)k->k_bt * s) >> 15);
    v->ktb = (int32_t)(((int64_t)k->k_tb * s) >> 15);
    v->wt = (int32_t)(((int64_t)k->wt * s) >> 15);
    v->alive = 1; v->held = 1; v->ramp = 0; v->w_tgt = 0;
    v->fade = 32768; v->pend = 0; v->felt_n = 0; v->felt_lp = 0;
    v->age = ++bar_ages;
    v->life = k->life;
    v->pthump = BAR_PULSE[k->band][BAR_NFAM - 1u][0];             /* the felt's knock: RUBBER, softest layer */
    v->prate_thump = BAR_PRATE[n - BAR_KEY_LO][BAR_NFAM - 1u][1] << 4;
    bar_load_pulse(v, vel);
    {   /* contact noise: 100 ms of filtered noise, louder for harder mallets and faster strikes */
        uint32_t f = (uint32_t)bar_fam >> 8;
        int64_t l = ((int64_t)bar_nz * BAR_FAM_NZ[f < BAR_NFAM ? f : BAR_NFAM - 1u] * vel) >> 22;
        v->nz_lvl = (int32_t)l;
        v->nz_n = l ? 4410u : 0u;
    }
}

static void bar_set_damper(bar_voice_t *v, int32_t w)
{
    uint32_t m;
    if (w == v->w_tgt) return;
    for (m = 0; m < BAR_NM; m++) { v->m[m].es = v->m[m].e; v->m[m].epss = v->m[m].eps; }
    v->w_tgt = w;
    v->ramp = BAR_RAMP_SUB;
}

/* ---- events (engine.c, ISR, before bar_render) */
static void bar_strike(uint32_t note, uint32_t vel)
{
    uint32_t i, pick = BAR_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < BAR_NV; i++)                    /* the same note still ringing: strike it again, superposed */
        if (bv[i].alive && !bv[i].pend && bv[i].note == note && bv[i].fade == 32768) {
            bar_voice_t *v = &bv[i];
            uint32_t m;
            for (m = BAR_UP0; m < BAR_NM; m++)       /* relight (a fading slot keeps its state); bar_budget() trims */
                if (v->m[m].b) v->m[m].live = 1;
            v->vel = (uint8_t)vel; v->held = 1; v->age = ++bar_ages; v->life = v->k->life;
            bar_set_damper(v, 0);
            bar_load_pulse(v, vel);
            return;
        }
    for (i = 0; i < BAR_NV; i++)
        if (!bv[i].alive) { bar_init(&bv[i], note, vel); return; }
    for (i = 0; i < BAR_NV; i++) {                  /* steal: the quietest released voice, else the oldest */
        uint32_t m, sum = 0, score;
        if (bv[i].pend) continue;
        for (m = 0; m < BAR_NM; m++) sum += (uint32_t)bv[i].m[m].peak >> 4;
        score = bv[i].held ? 0x80000000u + (bv[i].age & 0x7FFFFFFFu) : (sum > 0x7FFFFFFFu ? 0x7FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
    if (pick == BAR_NV) pick = 0;
    bv[pick].pend = 1; bv[pick].pend_note = (uint8_t)note; bv[pick].pend_vel = (uint8_t)vel;
    if (bv[pick].fade == 32768) bv[pick].fade = 32767;
}

static void bar_note_off(uint32_t note)
{
    uint32_t i;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive && bv[i].note == note && bv[i].held) {
            bar_voice_t *v = &bv[i];
            v->held = 0;
            bar_set_damper(v, bar_pedal_w);
            if (bar_pedal_w && bar_thump && !v->ptab) {      /* the felt lands on the bar: a soft knock */
                v->ptab = v->pthump;
                v->pamp = bar_thump > 32767 ? 32767 : bar_thump;
                v->prate = v->prate_thump;
                v->ppos = 0;
            }
            if (bar_pedal_w && bar_felt) v->felt_n = 353;    /* 8 ms of felt noise */
        }
}

static void bar_pedal(uint32_t cc)
{
    uint32_t i;
    bar_pedal_w = PEDAL_W[cc > 127u ? 127u : cc];
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive && !bv[i].held) bar_set_damper(&bv[i], bar_pedal_w);
}

static void bar_all_off(void)
{
    uint32_t i;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive) { bv[i].held = 0; bar_set_damper(&bv[i], 32768); }
}

static void bar_shed(void)                          /* overload: fade the quietest voice, released first */
{
    uint32_t i, m, pick = BAR_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < BAR_NV; i++) {
        uint32_t sum = 0, score;
        if (!bv[i].alive || bv[i].fade != 32768) continue;
        for (m = 0; m < BAR_NM; m++) sum += (uint32_t)bv[i].m[m].peak >> 4;
        score = (bv[i].held ? 0x40000000u : 0u) + (sum > 0x3FFFFFFFu ? 0x3FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
    if (pick < BAR_NV) { bv[pick].fade = 32767; bv[pick].pend = 0; bar_shed_count++; }
}

/* the engine stops at once (engine.c, after its output fade at a TYPE change) */
static void bar_kill_all(void)
{
    uint32_t i, sb;
    for (i = 0; i < BAR_NV; i++) { bv[i].alive = 0; bv[i].pend = 0; }
    for (sb = 0; sb < 2u; sb++) bar_bd_m[sb][0] = bar_bd_m[sb][1] = bar_bd_o[sb][0] = bar_bd_o[sb][1] = bar_bd_e[sb] = 0;
    bar_active_bits = bar_held_bits = 0;
    bar_nz_s[0] = bar_nz_s[1] = bar_nz_s[2] = bar_nz_s[3] = 0;
    bar_nz_dirty = 1;                               /* the next block clears the envelopes */
}

static int bar_quiet(void) { return bar_active_bits == 0 && bar_quiet_blocks >= 2u; }

/* ---- the motor: the discs' opening per sub-block edge, once per block (the TREM/WAH page through eng_bar_knob) */
static void bar_motor_block(void)
{
    uint32_t i, inc = bar_mot_inc;
    int32_t depth = bar_mot_depth > 127u ? 127 : bar_mot_depth, shape = bar_mot_shape > 127u ? 127 : bar_mot_shape;
    int32_t gc = BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u].mot_gc;
    int32_t gmin = 32768 - (depth * (32768 - gc)) / 127;
    if (!bar_mot_on) {                              /* not MOTOR: the discs parked open */
        for (i = 0; i < 9u; i++) bar_mot_g[i] = 32768;
        bar_mot_ph += 256u * inc;
        return;
    }
    for (i = 0; i < 9u; i++) {
        /* |cos phi|, phi = pi t / 512, t = ph >> 23 folded on the quarter-turn table: one AM period per 2^32 phase
         * cycle (bit-exact to tools/bartube.py mot_block) */
        uint32_t ph = bar_mot_ph + i * 32u * inc, t = ph >> 23, fr = (ph >> 15) & 255u, t1 = (t + 1u) & 511u;
        int32_t c0 = t < 256u ? BAR_MOT_COS[t] : BAR_MOT_COS[512u - t];
        int32_t c1 = t1 < 256u ? BAR_MOT_COS[t1] : BAR_MOT_COS[512u - t1];
        int32_t c = c0 + (((c1 - c0) * (int32_t)fr) >> 8);
        int32_t o = 32768 - c, op = o;
        if (shape) op = o + (((((o * o) >> 15) - o) * shape) / 127);   /* p = 1 + SHAPE/127: a narrower open window */
        bar_mot_g[i] = gmin + (((32768 - gmin) * op) >> 15);
    }
    bar_mot_ph += 256u * inc;
}

/* ---- the mode budget, at block start (after this block's strikes): while more upper slots are live than the cap,
 * drop the least audible one. Order: (1) ringing slots under -30 dB re their voice's fundamental (faded out over a
 * sub-block), (2) slots of a new strike that have not sounded yet, the least important partial first (T2, X, V4,
 * T1, V3, V2): dropping those is silent, (3) louder ringing slots, quietest relative to their voice first (faded). */
static const uint8_t BAR_KEEP[BAR_NM] = {0, 0, 5, 4, 2, 3, 0, 1};      /* V2 > V3 > T1 > V4 > X > T2 */
static void bar_budget(void)
{
    uint32_t used = bar_upper_live(), budget = bar_budget_now();
    while (used > budget) {
        uint32_t i, m, bi = BAR_NV, bm = 0, best = 0xFFFFFFFFu;
        for (i = 0; i < BAR_NV; i++) {
            const bar_voice_t *v = &bv[i];
            uint32_t ref;
            if (!v->alive) continue;
            ref = (uint32_t)v->m[0].peak + (uint32_t)(((int64_t)v->m[1].peak * iabs(v->wt)) >> 28);
            ref = (ref >> 10) + 1u;                                     /* the fundamental / 1024 */
            for (m = BAR_UP0; m < BAR_NM; m++) {
                const bar_ms_t *s = &v->m[m];
                uint32_t sc, r;
                if (s->live != 1u) continue;
                if (!s->peak) sc = 32u + BAR_KEEP[m];                   /* not sounded yet */
                else {
                    r = (uint32_t)s->peak / ref;                        /* Q10 level re the fundamental */
                    sc = r < 32u ? r : 64u + (r > 0x3FFFFFu ? 0x3FFFFFu : r);   /* 32/1024 = -30 dB */
                }
                if (sc < best) { best = sc; bi = i; bm = m; }
            }
        }
        if (bi == BAR_NV) break;
        {
            bar_ms_t *s = &bv[bi].m[bm];
            if (s->peak) s->live = 2;               /* faded out over its next sub-block, never cut */
            else { s->live = 0; s->x = s->y = s->ex = s->ey = 0; }      /* never sounded: nothing to fade */
        }
        used--;
    }
}

/* one MC3 step with first-order error feedback (Q30 coefficients, the Rhodes kernel copied) */
#define BAR_MC3(x, y, ex, ey, e, eps) do { \
        int64_t t_ = (int64_t)(e) * (y) + (ex); \
        (x) += (int32_t)(t_ >> 30); (ex) = (int32_t)(t_ & 0x3FFFFFFF); \
        t_ = -(int64_t)(eps) * (y) - (int64_t)(e) * (x) + (ey); \
        (y) += (int32_t)(t_ >> 30); (ey) = (int32_t)(t_ & 0x3FFFFFFF); \
    } while (0)

/* one voice, one 32-frame sub-block (number sb of the block), added into mix[0..BAR_SUB) */
static void bar_voice_sub(bar_voice_t *v, int32_t *mix, uint32_t sb)
{
    const bar_key_t *k = v->k;
    const bar_type_t *ty = &BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u];
    uint32_t j, m, base = sb * BAR_SUB;
    int32_t sum = 0, gain = v->gain, frc, tube, slow;
#ifdef DINGHY_HOST
    uint32_t nlive = 0;
#endif
    if (v->ramp) {                                  /* damper crossfade: E and eps together, 7 sub-blocks */
        int32_t f;
        v->ramp--;
        f = (int32_t)(BAR_RAMP_SUB - v->ramp) * 4681;
        if (f > 32768) f = 32768;
        if (!v->ramp) f = 32768;
        for (m = 0; m < BAR_NM; m++) {
            bar_ms_t *s = &v->m[m];
            int32_t et, pt;
            if (!s->live) continue;
            et = s->e0 + (int32_t)(((int64_t)(s->e1 - s->e0) * v->w_tgt) >> 15);
            pt = s->eps0 + (int32_t)(((int64_t)(s->eps1 - s->eps0) * v->w_tgt) >> 15);
            s->e = s->es + (int32_t)(((int64_t)(et - s->es) * f) >> 15);
            s->eps = s->epss + (int32_t)(((int64_t)(pt - s->epss) * f) >> 15);
        }
    }
    frc = v->ptab != 0;
    if (frc)                                        /* mallet (or felt) force for the sub-block, while it lasts */
        for (j = 0; j < BAR_SUB; j++) {
            if (v->ptab) {
                uint32_t i = v->ppos >> 16;
                int32_t f = (int32_t)((v->ppos >> 1) & 0x7FFFu);
                int32_t a = v->ptab[i], b = v->ptab[i + 1];
                bar_F[j] = mul15s(a + (int32_t)(((int64_t)(b - a) * f) >> 15), v->pamp);
                v->ppos += v->prate;
                if ((v->ppos >> 16) >= BAR_PLEN) v->ptab = 0;
            } else
                bar_F[j] = 0;
        }
    for (j = 0; j < BAR_SUB; j++) bar_x[j] = 0;
    tube = v->m[1].live != 0;
    for (m = tube ? BAR_UP0 : 0u; m < BAR_NM; m++) {    /* the upper modes (and V1 without a tube): MC3 + error
                                                         * feedback, mode-outer */
        bar_ms_t *s = &v->m[m];
        int32_t x, y, ex, ey, e, eps, b, pk = 0;
        if (!s->live || m == 1u) continue;
        x = s->x; y = s->y; ex = s->ex; ey = s->ey; e = s->e; eps = s->eps; b = s->b;
        if (s->live == 2u) {                        /* going to sleep: faded out over this sub-block, never cut */
            for (j = 0; j < BAR_SUB; j++) {
                if (frc) y += (int32_t)(((int64_t)bar_F[j] * b) >> 20);
                BAR_MC3(x, y, ex, ey, e, eps);
                bar_x[j] += (int32_t)(((int64_t)x * (int32_t)(1024u * (BAR_SUB - j))) >> 15);
            }
            s->live = 0; s->x = s->y = s->ex = s->ey = 0; s->peak = 0;
#ifdef DINGHY_HOST
            nlive++;
#endif
            continue;
        }
        if (frc)
            for (j = 0; j < BAR_SUB; j++) {
                y += (int32_t)(((int64_t)bar_F[j] * b) >> 20);
                BAR_MC3(x, y, ex, ey, e, eps);
                bar_x[j] += x;
                if (iabs(x) > pk) pk = iabs(x);
                if (iabs(y) > pk) pk = iabs(y);
            }
        else
            for (j = 0; j < BAR_SUB; j++) {
                BAR_MC3(x, y, ex, ey, e, eps);
                bar_x[j] += x;
                if (iabs(x) > pk) pk = iabs(x);
                if (iabs(y) > pk) pk = iabs(y);
            }
        s->x = bar_sat(x); s->y = bar_sat(y); s->ex = ex; s->ey = ey; s->peak = pk;
        sum += pk >> 3;
#ifdef DINGHY_HOST
        nlive++;
#endif
    }
    slow = v->fade != 32768 || v->nz_n || v->felt_n || !tube;
    if (tube) {                                     /* V1 <-> TUBE, sample-inner (the motor ramps per sample), then
                                                     * the gain straight into the mix */
        bar_ms_t *s0 = &v->m[0], *s1 = &v->m[1];
        int32_t x0 = s0->x, y0 = s0->y, ex0 = s0->ex, ey0 = s0->ey, e0 = s0->e, p0 = s0->eps, b0 = s0->b;
        int32_t x1 = s1->x, y1 = s1->y, ex1 = s1->ex, ey1 = s1->ey, e1 = s1->e, p1 = s1->eps;
        int32_t g0 = bar_mot_g[sb], g1 = bar_mot_g[sb + 1u], pk0 = 0, pk1 = 0;
        int32_t kbt = (int32_t)(((int64_t)v->kbt * g0) >> 15), ktb = (int32_t)(((int64_t)v->ktb * g0) >> 15);
        int32_t wt = (int32_t)(((int64_t)v->wt * g0) >> 15);
        int32_t dkbt = ((int32_t)(((int64_t)v->kbt * g1) >> 15) - kbt) >> 5;
        int32_t dktb = ((int32_t)(((int64_t)v->ktb * g1) >> 15) - ktb) >> 5;
        int32_t dwt = ((int32_t)(((int64_t)v->wt * g1) >> 15) - wt) >> 5;
        for (j = 0; j < BAR_SUB; j++) {
            int32_t u;
            if (frc) y0 += (int32_t)(((int64_t)bar_F[j] * b0) >> 20);
            y0 += (int32_t)(((int64_t)ktb * x1) >> 30);              /* the tube's pressure on the bar */
            BAR_MC3(x0, y0, ex0, ey0, e0, p0);
            y1 += (int32_t)(((int64_t)kbt * x0) >> 30);              /* the bar drives the tube */
            BAR_MC3(x1, y1, ex1, ey1, e1, p1);
            u = bar_x[j] + x0 + (int32_t)(((int64_t)wt * x1) >> 28); /* the bar's own sound + the tube's */
            if (slow) bar_x[j] = u;
            else mix[j] += (int32_t)(((int64_t)u * gain) >> 24);
            if (iabs(x0) > pk0) pk0 = iabs(x0);
            if (iabs(y0) > pk0) pk0 = iabs(y0);
            if (iabs(x1) > pk1) pk1 = iabs(x1);
            if (iabs(y1) > pk1) pk1 = iabs(y1);
            kbt += dkbt; ktb += dktb; wt += dwt;
        }
        s0->x = bar_sat(x0); s0->y = bar_sat(y0); s0->ex = ex0; s0->ey = ey0; s0->peak = pk0;
        s1->x = bar_sat(x1); s1->y = bar_sat(y1); s1->ex = ex1; s1->ey = ey1; s1->peak = pk1;
        sum += (pk0 >> 3) + (pk1 >> 3);
#ifdef DINGHY_HOST
        nlive += 3;                                 /* the coupling costs about one more mode (P0) */
#endif
    }
    if (slow)
        for (j = 0; j < BAR_SUB; j++) {             /* gain, steal fade, contact noise, felt noise */
            int32_t o = (int32_t)(((int64_t)bar_x[j] * gain) >> 24);
            if (v->fade != 32768) {
                o = (int32_t)(((int64_t)o * v->fade) >> 15);
                v->fade -= BAR_FADE_STEP;
                if (v->fade < 0) v->fade = 0;
            }
            if (v->nz_n) {                          /* contact noise: this voice's envelope into the shared noise */
                bar_nzenv[base + j] += v->nz_lvl;
                v->nz_lvl -= v->nz_lvl >> 10;       /* tau ~ 23 ms */
                v->nz_n--;
                bar_nz_dirty = 1;
            }
            if (v->felt_n) {                        /* felt noise: LFSR, one-pole ~2 kHz (as the Rhodes) */
                int32_t nz;
                bar_lfsr = (bar_lfsr >> 1) ^ (-(bar_lfsr & 1u) & 0xB400u);
                nz = (int32_t)(bar_lfsr & 0xFFFFu) - 32768;
                v->felt_lp += (nz - v->felt_lp) >> 3;
                o += (int32_t)(((int64_t)v->felt_lp * bar_felt) >> 10);
                v->felt_n--;
            }
            mix[j] += o;
        }
#ifdef DINGHY_HOST
    bar_h_blk_ms += nlive * BAR_SUB; bar_h_blk_mul += (3u * nlive + 1u) * BAR_SUB; bar_h_blk_ns += BAR_SUB * (540u + 57u + 170u * nlive);
#endif
    if (v->fade == 0) {                             /* a steal finished fading: start the waiting note */
        v->alive = 0;
        if (v->pend) bar_init(v, v->pend_note, v->pend_vel);
        return;
    }
    if (!v->ptab && !v->felt_n && !v->nz_n && sum < (k->kill >> 3)) { v->alive = 0; return; }
    if (!v->ptab BAR_SLEEP_ON) {                    /* upper modes: absolute and relative sleep, faded out over
                                                     * the next sub-block (live = 2); they never wake on their own */
        int32_t ref = v->m[0].peak + (int32_t)(((int64_t)v->m[1].peak * iabs(v->wt)) >> 28);
        int32_t rel = ref >> ty->sleep_rel, ab = k->kill >> 3;
        for (m = BAR_UP0; m < BAR_NM; m++) {
            bar_ms_t *s = &v->m[m];
            if (s->live == 1u && (s->peak < ab || s->peak < rel)) s->live = 2;
        }
    }
}

static void bar_render(int32_t *mono_out, uint32_t n)
{
    uint32_t i, sb, j, act = 0, held = 0, ty = bar_sub < BAR_NTYPE ? bar_sub : 0u, nb = BAR_TYPE[ty].nbody;
    int32_t mx = 0;
    bar_motor_block();
    bar_budget();
    for (i = 0; i < n; i++) bar_mono[i] = 0;
    if (bar_nz_dirty) { for (i = 0; i < n; i++) bar_nzenv[i] = 0; bar_nz_dirty = 0; }
#ifdef DINGHY_HOST
    bar_h_blk_ms = bar_h_blk_mul = bar_h_blk_ns = 0;
#endif
    for (i = 0; i < BAR_NV; i++) {
        bar_voice_t *v = &bv[i];
        for (sb = 0; sb < n && v->alive; sb += BAR_SUB)
            bar_voice_sub(v, bar_mono + sb, sb / BAR_SUB);
        if (v->alive && v->life && --v->life == 0) v->alive = 0;
    }
    if (bar_nz_dirty) {                             /* LFSR -> two biquads (Q14, transposed DF2) x the envelopes */
        for (j = 0; j < n; j++) {
            int32_t nz, y;
            bar_lfsr = (bar_lfsr >> 1) ^ (-(bar_lfsr & 1u) & 0xB400u);
            nz = ((int32_t)(bar_lfsr & 0xFFFFu) - 32768) >> 2;
            y = (int32_t)(((int64_t)NZ_BQ[0][0] * nz) >> 14) + bar_nz_s[0];
            bar_nz_s[0] = (int32_t)(((int64_t)NZ_BQ[0][1] * nz - (int64_t)NZ_BQ[0][3] * y) >> 14) + bar_nz_s[1];
            bar_nz_s[1] = (int32_t)(((int64_t)NZ_BQ[0][2] * nz - (int64_t)NZ_BQ[0][4] * y) >> 14);
            nz = y;
            y = (int32_t)(((int64_t)NZ_BQ[1][0] * nz) >> 14) + bar_nz_s[2];
            bar_nz_s[2] = (int32_t)(((int64_t)NZ_BQ[1][1] * nz - (int64_t)NZ_BQ[1][3] * y) >> 14) + bar_nz_s[3];
            bar_nz_s[3] = (int32_t)(((int64_t)NZ_BQ[1][2] * nz - (int64_t)NZ_BQ[1][4] * y) >> 14);
            bar_mono[j] += (int32_t)(((int64_t)y * bar_nzenv[j]) >> 13);
        }
#ifdef DINGHY_HOST
        bar_h_blk_mul += 11u * n; bar_h_blk_ns += 680u * n;
#endif
    } else
        bar_nz_s[0] = bar_nz_s[1] = bar_nz_s[2] = bar_nz_s[3] = 0;
    for (j = 0; j < n; j++) {                       /* the type's room / mic EQ (Q28 DF1, 0..2 biquads); Q20 -> Q15 */
        int32_t o = bar_mono[j];
        for (sb = 0; sb < nb; sb++) {
            const int32_t *b = BAR_BODY[ty][sb];
            int64_t a2 = (int64_t)b[0] * o + (int64_t)b[1] * bar_bd_m[sb][0] + (int64_t)b[2] * bar_bd_m[sb][1]
                       - (int64_t)b[3] * bar_bd_o[sb][0] - (int64_t)b[4] * bar_bd_o[sb][1] + bar_bd_e[sb];
            int32_t y = (int32_t)(a2 >> 28);
            bar_bd_e[sb] = (int32_t)(a2 & 0xFFFFFFF);
            bar_bd_m[sb][1] = bar_bd_m[sb][0]; bar_bd_m[sb][0] = o; bar_bd_o[sb][1] = bar_bd_o[sb][0]; bar_bd_o[sb][0] = y;
            o = y;
        }
        o = (o + 16) >> 5;
        if (iabs(o) > mx) mx = iabs(o);
        mono_out[j] = o;
    }
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive) { act |= 1u << i; bar_notes[i] = bv[i].note; if (bv[i].held) held |= 1u << i; }
    if (!act && mx < 2) {
        for (sb = 0; sb < 2u; sb++) bar_bd_m[sb][0] = bar_bd_m[sb][1] = bar_bd_o[sb][0] = bar_bd_o[sb][1] = bar_bd_e[sb] = 0;
        if (bar_quiet_blocks < 0xFFFFu) bar_quiet_blocks++;
    } else
        bar_quiet_blocks = 0;
    bar_held_bits = held;
    bar_active_bits = act;
#ifdef DINGHY_HOST
    bar_h_ms += bar_h_blk_ms; bar_h_mul += bar_h_blk_mul;
#endif
}
