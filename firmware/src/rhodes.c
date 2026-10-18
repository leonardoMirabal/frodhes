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
 *   rh_note_on(note, vel), rh_note_off(note), rh_pedal(on) / rh_pedal_level(cc)   post events
 *   rh_quiet(), rh_lit(base, n), rh_hold                                            read summaries
 * ISR: rh_render(mono, n) writes n mono samples (Q15) for the effects chain.
 * The ISR owns every voice; the main loop only posts 32-bit events into a single-producer ring (P1). */
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

#define RH_V1 0u
#define RH_O1 2u
#define RH_O2 3u
#ifndef FR_VOICES
#define FR_VOICES 9u                           /* voices per engine (bars.h BAR_NV too); a build may set another
                                                * count with -DFR_VOICES=N (APP_DEFS / HOST_DEFS) */
#endif
#define RH_NV FR_VOICES
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
    uint8_t live;
} rh_ms_t;

typedef struct {
    rh_ms_t m[RH_NM];
    const rh_key_t *k;
    uint8_t note, vel, alive, held;
    uint8_t ramp;                              /* damper crossfade sub-blocks left */
    int32_t w_tgt;                             /* damper weight Q15: 0 = undamped, 32768 = full felt */
    uint8_t pend, pend_note, pend_vel;         /* a start waiting for the steal fade */
    int32_t fade;                              /* Q15 output gain while fading out (32768 = not fading) */
    uint32_t age, life;
    int32_t Hprev, off, gain;
    uint32_t ppos, prate;
    int32_t pamp;
    const int16_t *ptab, *ptab2;               /* pulse shapes of the two velocity layers around vel */
    int32_t pmix;                              /* Q15 weight of ptab2 (layer crossfade: no step at a layer change) */
    uint16_t felt_n;                           /* felt-noise samples left (key-up) */
    int32_t felt_lp;
} rh_voice_t;

static rh_voice_t rv[RH_NV];
static int32_t rh_mono[256], rh_x[SUB], rh_F[SUB];
static uint32_t rh_ages;
static int32_t rh_pedal_w = 32768;             /* ISR copy: damper weight for keys that are up */
static int32_t rlc_m1, rlc_m2, rlc_o1, rlc_o2, rlc_e;
static int32_t bd_m[2][2], bd_o[2][2], bd_e[2];          /* the TYPE's body biquads */
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
static uint32_t rh_lfsr = 0xACE1u;

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
static void rh_note_off(uint32_t note) { if (note < 128u) rh_post(EV_NOTE_OFF, note, 0); }
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

/* ---- run-time coefficient path (§5.6): (E, eps) from (sin, cos of theta/2, theta, d, cr), cents t, decay k */
static void rh_coef(const rh_mode_t *m, int32_t d30, int32_t t30, int32_t k16, int32_t *E, int32_t *eps)
{
    int32_t a = (int32_t)(((int64_t)k16 * (k16 - 65536)) >> 16);          /* k(k-1), Q16 */
    int32_t c = (int32_t)(((int64_t)a * (k16 - 131072)) >> 16);           /* k(k-1)(k-2), Q16 */
    int32_t d2 = mul30(d30, d30), d3 = mul30(d2, d30);
    int64_t dp = (((int64_t)k16 * d30) >> 16) - ((((int64_t)a * d2) >> 16) >> 1)
               + (int32_t)(((int64_t)c * d3) >> 16) / 6;
    int32_t dpi = dp < 1 ? 1 : (int32_t)dp;
    int32_t dp2 = mul30(dpi, dpi), dp3 = mul30(dp2, dpi);
    int32_t q = Q30 - (dpi >> 2) - 3 * (dp2 >> 5);
    int32_t om = (dpi >> 1) + (dp2 >> 3) + (dp3 >> 4);
    int32_t dl = mul30(m->th, t30);
    int32_t dl2 = mul30(dl, dl), dl3 = mul30(dl2, dl), dl4 = mul30(dl2, dl2), dl5 = mul30(dl4, dl);
    int32_t cdl = Q30 - (dl2 >> 1) + dl4 / 24, sdl = dl - dl3 / 6 + dl5 / 120;
    int32_t sp = mul30(m->sh, cdl) + mul30(m->ch, sdl);
    int32_t cr = m->cr - 2 * mul30(m->cr, t30) + 3 * mul30(m->cr, mul30(t30, t30));
    int32_t corr = Q30 + mul16(mul30(om, om), cr);
    int32_t t1 = (int32_t)(((int64_t)q * sp) >> 29);                      /* 2 q sin(theta'/2), Q30 */
    *E = mul30(t1, corr);
    *eps = dpi;
}

/* ---- voices (ISR) */
static void rh_load_pulse(rh_voice_t *v, uint32_t vel)
{
    uint32_t key = (uint32_t)(v->k - RH_KEYS), i = RH_VPOS_I[vel], f = RH_VPOS_F[vel];
    const uint16_t *pa = RH_PAMP[key];
    const uint32_t *pr = RH_PRATE[key];
    v->ptab = RH_PULSE[key][RH_VLAY_I[vel]];
    v->ptab2 = RH_PULSE[key][RH_VLAY_I[vel] + 1u < RH_NLAYER ? RH_VLAY_I[vel] + 1u : RH_NLAYER - 1u];
    v->pmix = RH_VLAY_W[vel];
    v->pamp = (int32_t)pa[i] + ((((int32_t)pa[i + 1] - (int32_t)pa[i]) * (int32_t)f) >> 8);
    v->prate = (uint32_t)((int32_t)pr[i] + ((((int32_t)pr[i + 1] - (int32_t)pr[i]) * (int32_t)f) >> 8)) << 4;  /* Q12 -> Q16 */
    {   /* HAMMER knob x TYPE tips scale the contact rate; the force scales with it so the impulse (the hammer's
         * momentum) is kept: a softer tip pushes longer and less hard, it does not give more energy */
        uint32_t h = (uint32_t)(((uint64_t)(uint32_t)rh_ham * (uint32_t)RH_TYPE[rh_type & 3u].ham) >> 16);
        v->prate = (uint32_t)(((uint64_t)v->prate * h) >> 16);
        v->pamp = (int32_t)(((int64_t)v->pamp * h) >> 16);
        if (v->pamp > 32767) v->pamp = 32767;                                 /* mul15s range */
    }
    v->ppos = 0;
}

static int32_t rh_hermite(const int32_t *P, const int32_t *M, int64_t s)
{
    uint32_t i, t;
    int32_t p0, p1, m0, m1, c2, c3, a;
    if (s < 0) s = 0;
    if ((uint64_t)s >= ((uint64_t)RH_S_MAX_HI << 44)) s = ((int64_t)RH_S_MAX_HI << 44) - 1;
    i = (uint32_t)(s >> 44);
    t = (uint32_t)(s >> 22) & 0x3FFFFFu;
    p0 = P[i]; p1 = P[i + 1]; m0 = M[i]; m1 = M[i + 1];
    c3 = 2 * (p0 - p1) + m0 + m1;
    c2 = 3 * (p1 - p0) - 2 * m0 - m1;
    a = c2 + (int32_t)(((int64_t)t * c3) >> 22);
    a = m0 + (int32_t)(((int64_t)t * a) >> 22);
    return p0 + (int32_t)(((int64_t)t * a) >> 22);
}

/* rh_hermite of s = u * u, inlined into the pickup loops: the same arithmetic. s >= 0 always (|u| < 2^31: the square
 * fits), so the low clamp could never fire; for s >= 0, s >= RH_S_MAX_HI << 44 exactly when hi32(s) >= RH_S_MAX_HI << 12 */
static inline __attribute__((always_inline)) int32_t rh_hermite_u(const int32_t *P, const int32_t *M, int32_t u)
{
    int64_t s = (int64_t)u * u;
    uint32_t i, t;
    int32_t p0, p1, m0, m1, c2, c3, a;
    if ((uint32_t)(s >> 32) >= (RH_S_MAX_HI << 12)) s = ((int64_t)RH_S_MAX_HI << 44) - 1;
    i = (uint32_t)(s >> 44);
    t = (uint32_t)(s >> 22) & 0x3FFFFFu;
    p0 = P[i]; p1 = P[i + 1]; m0 = M[i]; m1 = M[i + 1];
    c3 = 2 * (p0 - p1) + m0 + m1;
    c2 = 3 * (p1 - p0) - 2 * m0 - m1;
    a = c2 + (int32_t)(((int64_t)t * c3) >> 22);
    a = m0 + (int32_t)(((int64_t)t * a) >> 22);
    return p0 + (int32_t)(((int64_t)t * a) >> 22);
}

static void rh_init(rh_voice_t *v, uint32_t note, uint32_t vel)
{
    const rh_type_t *ty = &RH_TYPE[rh_type];
    uint32_t m, n = note < RH_KEY_LO ? RH_KEY_LO : note > RH_KEY_LO + RH_NKEY - 1u ? RH_KEY_LO + RH_NKEY - 1u : note;
    const rh_key_t *k = &RH_KEYS[n - RH_KEY_LO];
    int32_t u;
    v->k = k;
    for (m = 0; m < RH_NM; m++) {
        rh_ms_t *s = &v->m[m];
        const rh_mode_t *km = &k->m[m];
        s->x = s->y = s->ex = s->ey = 0;
        s->peak = 0;
        s->live = km->th != 0;
        if (!s->live) { s->e = s->eps = 0; continue; }
        rh_coef(km, km->d, rh_tune_t, rh_k_decay, &s->e0, &s->eps0);
        rh_coef(km, km->drel, rh_tune_t, rh_k_rel, &s->e1, &s->eps1);
        s->e = s->es = s->e0; s->eps = s->epss = s->eps0;
        {   /* force gain: BARK scales the tip (the pickup then barks), BELL the overtones; saturating */
            int64_t b = ((((int64_t)km->b * rh_bark_s) >> 16) * ty->bark) >> 16;
            if (m == RH_O1 || m == RH_O2) b = (((b * ty->bell) >> 16) * rh_bell) >> 16;
            s->b = b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
        }
    }
    v->note = (uint8_t)note; v->vel = (uint8_t)vel;
    v->off = k->off + ty->off_delta + rh_tone_off;
    v->gain = mul16(mul16(k->gain, rh_bark_g), ty->bark_g);
    u = v->off;
    v->Hprev = rh_hermite(RH_PHI[k->fam], RH_SLOPE[k->fam], (int64_t)u * u);
    v->alive = 1; v->held = 1; v->ramp = 0; v->w_tgt = 0;
    v->fade = 32768; v->pend = 0; v->felt_n = 0; v->felt_lp = 0;
    v->age = ++rh_ages;
    v->life = k->life;
    rh_load_pulse(v, vel);
}

static void rh_set_damper(rh_voice_t *v, int32_t w)
{
    uint32_t m;
    if (w == v->w_tgt) return;
    for (m = 0; m < RH_NM; m++) { v->m[m].es = v->m[m].e; v->m[m].epss = v->m[m].eps; }
    v->w_tgt = w;
    v->ramp = RAMP_SUB;
}

static void rh_strike(uint32_t note, uint32_t vel)
{
    uint32_t i, pick = RH_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < RH_NV; i++)                         /* the same note still sounding: re-strike, superpose */
        if (rv[i].alive && !rv[i].pend && rv[i].note == note && rv[i].fade == 32768) {
            rh_voice_t *v = &rv[i];
            uint32_t m;
            for (m = 0; m < RH_NM; m++) if (v->k->m[m].th) v->m[m].live = 1;
            v->vel = (uint8_t)vel; v->held = 1; v->age = ++rh_ages; v->life = v->k->life;
            rh_set_damper(v, 0);
            rh_load_pulse(v, vel);
            return;
        }
    for (i = 0; i < RH_NV; i++)
        if (!rv[i].alive) { rh_init(&rv[i], note, vel); return; }
    for (i = 0; i < RH_NV; i++) {                       /* steal: the quietest released voice, else the oldest */
        uint32_t m, sum = 0, score;
        if (rv[i].pend) continue;
        for (m = 0; m < RH_NM; m++) sum += (uint32_t)rv[i].m[m].peak >> 4;
        score = rv[i].held ? 0x80000000u + (rv[i].age & 0x7FFFFFFFu) : (sum > 0x7FFFFFFFu ? 0x7FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
    if (pick == RH_NV) pick = 0;
    rv[pick].pend = 1; rv[pick].pend_note = (uint8_t)note; rv[pick].pend_vel = (uint8_t)vel;
    if (rv[pick].fade == 32768) rv[pick].fade = 32767;
}

static void rh_events(void)
{
    while (ev_r != ev_w) {
        uint32_t e = ev_q[ev_r & 31u], type = e >> 28, note = e >> 16 & 127u, val = e & 0xFFFFu, i;
        ev_r = ev_r + 1u;
        if (type == EV_NOTE_ON) rh_strike(note, val);
        else if (type == EV_NOTE_OFF) {
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive && rv[i].note == note && rv[i].held) {
                    rh_voice_t *v = &rv[i];
                    v->held = 0;
                    rh_set_damper(v, rh_pedal_w);
                    if (rh_pedal_w && rh_thump && !v->ptab) {  /* the felt lands on the tine: a soft knock */
                        uint32_t key = (uint32_t)(v->k - RH_KEYS);
                        v->ptab = RH_PULSE[key][0];
                        v->pmix = 0;
                        v->pamp = rh_thump > 32767 ? 32767 : rh_thump;
                        v->prate = (uint32_t)RH_PRATE[key][1] << 4;
                        v->ppos = 0;
                    }
                    if (rh_pedal_w && rh_felt) v->felt_n = 353;  /* 8 ms of felt noise */
                }
            for (i = 0; i < RH_NV; i++)                 /* a note let go while it waits for a steal's fade: it
                                                         * starts let go (it was held for good: fuzz seed 160, a fast
                                                         * pattern over 8 voices; the same fix as bars.c) */
                if (rv[i].alive && rv[i].pend == 1u && rv[i].pend_note == note) rv[i].pend = 2;
        } else if (type == EV_PEDAL) {
            rh_pedal_w = PEDAL_W[val];
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive && !rv[i].held) rh_set_damper(&rv[i], rh_pedal_w);
        } else if (type == EV_ALL_OFF) {
            for (i = 0; i < RH_NV; i++)
                if (rv[i].alive) {
                    rv[i].held = 0; rh_set_damper(&rv[i], 32768);
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

/* one voice, one 32-frame sub-block, added into mix[0..SUB) */
static void rh_voice_sub(rh_voice_t *v, int32_t *mix)
{
    const rh_key_t *k = v->k;
    const int32_t *P = RH_PHI[k->fam], *M = RH_SLOPE[k->fam];
    uint32_t j, m;
    int32_t sum = 0, Hp = v->Hprev, off = v->off, gain = v->gain, fnz = 0;
    if (v->ramp) {                                        /* damper crossfade: E and eps together */
        int32_t f;
        v->ramp--;
        f = (int32_t)(RAMP_SUB - v->ramp) * 4681;         /* Q15 of i/7 */
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
                                                           * pulses 0..32767, pmix <= 30720, so every |product| < 2^31 */
        const int16_t *pt = v->ptab, *pt2 = v->ptab2;
        int32_t pmix = v->pmix, pamp = v->pamp;
        uint32_t ppos = v->ppos, prate = v->prate;
        fnz = 1;
        for (j = 0; j < SUB; j++) {
            if (pt) {
                uint32_t i = ppos >> 16;
                int32_t f = (int32_t)((ppos >> 1) & 0x7FFFu);
                int32_t a = pt[i], b = pt[i + 1];
                if (pmix) {
                    a += ((pt2[i] - a) * pmix) >> 15;
                    b += ((pt2[i + 1] - b) * pmix) >> 15;
                }
                rh_F[j] = mul15s(a + (((b - a) * f) >> 15), pamp);
                ppos += prate;
                if ((ppos >> 16) >= RH_PLEN) pt = 0;
            } else
                rh_F[j] = 0;
            rh_x[j] = 0;
        }
        v->ppos = ppos; v->ptab = pt;
    } else
        for (j = 0; j < SUB; j++) rh_x[j] = 0;            /* no force: rh_F is not read (fnz = 0) */
    for (m = 0; m < RH_NM; m++) {                         /* modes: MC3 + error feedback, mode-outer */
        rh_ms_t *s = &v->m[m];
        int32_t x, y, ex, ey, e, eps, b, pk = 0;
        if (!s->live) continue;
        x = s->x; y = s->y; ex = s->ex; ey = s->ey; e = s->e; eps = s->eps; b = s->b;
        if (fnz)                                          /* the hammer pushes in this sub-block */
            for (j = 0; j < SUB; j++) {
                int64_t t;
                if (rh_F[j]) y += (int32_t)(((int64_t)rh_F[j] * b) >> 20);
                t = (int64_t)e * y + ex;
                x += (int32_t)(t >> 30); ex = (int32_t)(t & 0x3FFFFFFF);
                t = ey - (int64_t)eps * y - (int64_t)e * x;   /* = -eps y - e x + ey: every term exact in int64
                                                               * (< 2^62), the same integer; two 32x32 MACs */
                y += (int32_t)(t >> 30); ey = (int32_t)(t & 0x3FFFFFFF);
                rh_x[j] += x;
                if (iabs(x) > pk) pk = iabs(x);
                if (iabs(y) > pk) pk = iabs(y);
            }
        else                                              /* no force (no pulse at the sub-block's start: every
                                                           * rh_F[j] would be 0, the add never ran) */
            for (j = 0; j < SUB; j++) {
                int64_t t;
                t = (int64_t)e * y + ex;
                x += (int32_t)(t >> 30); ex = (int32_t)(t & 0x3FFFFFFF);
                t = ey - (int64_t)eps * y - (int64_t)e * x;
                y += (int32_t)(t >> 30); ey = (int32_t)(t & 0x3FFFFFFF);
                rh_x[j] += x;
                if (iabs(x) > pk) pk = iabs(x);
                if (iabs(y) > pk) pk = iabs(y);
            }
        s->x = sat28i(x); s->y = sat28i(y); s->ex = ex; s->ey = ey; s->peak = pk;
        sum += pk >> 3;
    }
    if (v->fade == 32768 && !v->felt_n)                   /* pickup, then the per-key gain into the mix: no steal fade
                                                           * and no felt noise (only this loop changes them in a render:
                                                           * the tests below would never fire) */
        for (j = 0; j < SUB; j++) {
            int32_t H = rh_hermite_u(P, M, rh_x[j] + off);
            mix[j] += (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
            Hp = H;
        }
    else
    for (j = 0; j < SUB; j++) {                           /* the same, with the fade and the felt noise */
        int32_t H = rh_hermite_u(P, M, rh_x[j] + off);
        int32_t o = (int32_t)(((int64_t)(H - Hp) * gain) >> 24);
        Hp = H;
        if (v->fade != 32768) {
            o = (int32_t)(((int64_t)o * v->fade) >> 15);
            v->fade -= (int32_t)(32768 / FADE_SAMPLES);
            if (v->fade < 0) v->fade = 0;
        }
        if (v->felt_n) {                                  /* felt noise: LFSR, one-pole ~2 kHz */
            int32_t nz;
            rh_lfsr = (rh_lfsr >> 1) ^ (-(rh_lfsr & 1u) & 0xB400u);
            nz = (int32_t)(rh_lfsr & 0xFFFFu) - 32768;
            v->felt_lp += (nz - v->felt_lp) >> 3;
            o += (int32_t)(((int64_t)v->felt_lp * rh_felt) >> 10);   /* Q15 * Q15 >> 10 = Q20 */
            v->felt_n--;
        }
        mix[j] += o;
    }
    v->Hprev = Hp;
    if (v->fade == 0) {                                   /* a steal finished fading: start the waiting note */
        v->alive = 0;
        if (v->pend) {
            uint32_t rel = v->pend;                     /* 1 held, 2 let go, 3 let go by an all-off */
            rh_init(v, v->pend_note, v->pend_vel);
            if (rel >= 2u) { v->held = 0; rh_set_damper(v, rel == 3u ? 32768 : rh_pedal_w); }
            if (rel == 3u && v->life > RH_OFF_LIFE) v->life = RH_OFF_LIFE;
        }
        return;
    }
    if (!v->ptab && !v->felt_n && sum < (k->kill >> 3)) { v->alive = 0; return; }
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

static void rh_render(int32_t *mono_out, uint32_t n)
{
    uint32_t i, sb, j, act = 0, held = 0;
    int32_t mx = 0;
    uint32_t ty = rh_type & 3u, nb = RH_TYPE[ty].nbody;
    const int32_t *c = RH_RLC[ty];
    rh_events();
    if (rh_shed_req) { rh_shed_req = 0; rh_shed(); }
    for (i = 0; i < n; i++) rh_mono[i] = 0;
    for (i = 0; i < RH_NV; i++) {
        rh_voice_t *v = &rv[i];
        for (sb = 0; sb < n && v->alive; sb += SUB)
            rh_voice_sub(v, rh_mono + sb);
        if (v->alive && v->life && --v->life == 0 && v->fade == 32768) v->fade = 32767;   /* its end: the 1.5 ms fade */
    }
    for (j = 0; j < n; j++) {     /* pickup load: RLC low-pass on the mix (Q28 DF1, error feedback); Q20 -> Q15 */
        int32_t m = rh_mono[j], o;
        int64_t acc = (int64_t)c[0] * m + (int64_t)c[1] * rlc_m1 + (int64_t)c[2] * rlc_m2
                    - (int64_t)c[3] * rlc_o1 - (int64_t)c[4] * rlc_o2 + rlc_e;
        o = (int32_t)(acc >> 28); rlc_e = (int32_t)(acc & 0xFFFFFFF);
        rlc_m2 = rlc_m1; rlc_m1 = m; rlc_o2 = rlc_o1; rlc_o1 = o;
        for (sb = 0; sb < nb; sb++) {                        /* the instrument's amp / speaker (Q28 DF1) */
            const int32_t *b = RH_BODY[ty][sb];
            int64_t a2 = (int64_t)b[0] * o + (int64_t)b[1] * bd_m[sb][0] + (int64_t)b[2] * bd_m[sb][1]
                       - (int64_t)b[3] * bd_o[sb][0] - (int64_t)b[4] * bd_o[sb][1] + bd_e[sb];
            int32_t y = (int32_t)(a2 >> 28);
            bd_e[sb] = (int32_t)(a2 & 0xFFFFFFF);
            bd_m[sb][1] = bd_m[sb][0]; bd_m[sb][0] = o; bd_o[sb][1] = bd_o[sb][0]; bd_o[sb][0] = y;
            o = y;
        }
        o = (o + 16) >> 5;                                   /* round, no DC bias */
        if (iabs(o) > mx) mx = iabs(o);
        mono_out[j] = o;
    }
    for (i = 0; i < RH_NV; i++)
        if (rv[i].alive) { act |= 1u << i; rh_notes[i] = rv[i].note; if (rv[i].held) held |= 1u << i; }
    if (!act && mx < 2) {                                 /* exact silence once everything has ended */
        rlc_m1 = rlc_m2 = rlc_o1 = rlc_o2 = rlc_e = 0;
        for (sb = 0; sb < 2u; sb++) bd_m[sb][0] = bd_m[sb][1] = bd_o[sb][0] = bd_o[sb][1] = bd_e[sb] = 0;
        if (rh_quiet_blocks < 0xFFFFu) rh_quiet_blocks++;
    } else
        rh_quiet_blocks = 0;
    rh_held_bits = held;
    rh_active_bits = act;
}
