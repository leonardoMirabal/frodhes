/* SPDX-License-Identifier: GPL-3.0-only */
/* The REVERB's TYPEs: the framework (included by frodhes.c right after fx.c; fx.c declares fx_reverb, rev_cost_us and
 * rev_reset_state). P_RV_TYPE / FXP_RV_TYPE, on / off and the type in one field, as the drive's P_DRV_SW:
 *   0 OFF (the send stops, the tail rings out), 1 HALL (fx.c rev_hall, today's, sample for sample), 2 PLATE
 *   (rev_plate.c), 3 INFINITE (rev_inf.c). Append-only: the records store the number; old records load as HALL.
 * REVERB 2's knobs (params.c P_RV_SIZE / P_RV_MOD / P_RV_LOCUT, fx side FXP_RV_*):
 *   SIZE   0 = AUTO (HALL: from MIX, as HALL always did; PLATE: MIX, latched while a tail rings, rv_pz; INFINITE:
 *          RV_INF_AUTO, fixed), 1..127 fixed;  MOD 64 = HALL's modulation as before (0 none);
 *   LOW CUT 0 = off (as before), 1..127 a one-pole high-pass on the send, 20 .. 800 Hz.
 * Only one TYPE runs at a time. HALL keeps its own lines (fx.c, bit-exact); PLATE and INFINITE share one pool union.
 * A TYPE change while a tail rings fades the old TYPE's return out over RV_FADE control blocks (5.8 ms), then the new
 * TYPE starts with its lines reading as empty (O(1): no memset in the audio interrupt; HALL's lines, left full, are
 * cleared by the main loop, rev_poll, before HALL may run again). From silence the new TYPE starts at once.
 *
 * ---- The kernel API (one file per TYPE: rev_plate.c, rev_inf.c; private/reverb/CONTRACT.md) ----
 * Half rate (22.05 kHz), one control block = HL_NC = 16 samples a call.
 *   typedef struct { int32_t decay, damp, size, mod, freeze; } rev_ctl_t;      (below; filled once a control block)
 *   static void rev_<t>_start(rev_<t>_t *s);  O(1): no memset > 2 KB; stale buffer content must read as 0
 *   static void rev_<t>_run(rev_<t>_t *s, const int32_t *in, int32_t *mid, int32_t *side, const rev_ctl_t *c);
 *   static int rev_<t>_active(const rev_<t>_t *s);   0 once the tail is exact silence (lines and output)
 *   #define REV_US_<T> n   the kernel's cost, us per 256-frame block (dis loop counts x 0.8 us per instruction per
 *                          half-rate sample); REV_US_DEC + it must stay <= REV_US_MAX (HALL + 200 us) or it does not build
 * in[]: HL_NC mono samples, sent, LOW CUT, pre-delayed, decimated (HALL's half-band), level-scaled
 *       (mulq15(clamp(x, +-131071), HL_IN)) and DC-blocked exactly as rev_hall's network gets them (hl.bx): int16
 *       lines hold them without clipping at a piano's levels;
 * mid[] / side[]: HL_NC samples in hl.l / hl.r's scale (HALL: the sum of 4 line ends each); the framework upsamples
 *       them and adds L = M + S, R = M - S with MIX's return (as HALL).
 * A kernel: noinline, integers only, no 64-bit / or %, no float, no ROM-range constants; its mutable statics in its
 * own sections (REV_BSS / REV_DATA below), every buffer in its state type (the union rev_u, pool). c->freeze
 * (INFINITE only): 1 SUSTAIN (DECAY 120..126: gain 1, input open), 2 FREEZE (DECAY 127: gain 1, the kernel closes its
 * own input with its fade). */

#ifdef DINGHY_HOST
#define REV_BSS
#define REV_DATA
#else
#define REV_BSS __attribute__((section(".bss.rev")))
#define REV_DATA __attribute__((section(".data.rev")))
#endif

enum { RV_OFF, RV_HALL, RV_PLATE, RV_INF, RV_N };

typedef struct {            /* filled by the framework once per control block */
    int32_t decay;          /* 0..127 the DECAY knob, after the framework's slew (one step a control block) */
    int32_t damp;           /* 0..150, as HALL's rv_damp (TONE: 150 dark, 60 at 64, 0 bright) */
    int32_t size;           /* 1..127: SIZE (AUTO resolved by the framework: MIX, as HALL's size follows it; on
                             * PLATE rv_pz, on INFINITE RV_INF_AUTO) */
    int32_t mod;            /* 0..2032: MOD x 16 (64 the default: 1024; 0.9.6: the framework glides it 1/16 step a
                             * control block) */
    int32_t freeze;         /* INFINITE only (the user, 2026-10-24): 0 normal; 1 INFINITE SUSTAIN, DECAY 120..126: the
                             * loop gain exactly 1, the input open (it piles up, bounded); 2 FREEZE, DECAY 127: the
                             * loop gain exactly 1, the input closed (it holds the captured tail). Leaving either
                             * releases the tail (it decays again) */
} rev_ctl_t;

#ifndef REV_FILE_PLATE      /* (a test build may put another file in a TYPE's place) */
#define REV_FILE_PLATE "rev_plate.c"
#endif
#ifndef REV_FILE_INF
#define REV_FILE_INF "rev_inf.c"
#endif
#include REV_FILE_PLATE
#include REV_FILE_INF

static union { rev_plate_t p; rev_inf_t i; } rev_u POOL;

/* The cost, us per 256-frame block (fx_cost_us; the voice shedder and the bar engine's budget see it):
 *   REV_US_FRONT  the framework around a TYPE: the send, the pre-delay at rest, the 2x upsample and the return;
 *   REV_US_HALL   HALL itself: FX_US_REVERB - FRONT (HALL with its rev_down);
 *   REV_US_DEC    a kernel's rev_down (the decimator + DC blocker; HALL calls its own inside rev_hall);
 *   (0.9.5's half-bands, build/frodhes.dis: the return loop 45 -> 70 instructions a half-rate sample (+20 us), the
 *   send's clamp +5 a sample (+8), the two histories (+5), the pre-delay's loop +3 a sample (+5); rev_down 24 -> 37
 *   a half-rate sample with its call (+11), HALL's matrix +2 (+1.6): FRONT 50 -> 88, HALL 210 -> 223, DEC 10 -> 23,
 *   every TYPE +51 us, estimated: the FM-1's measured 260 for HALL as a whole + 51 = FX_US_REVERB 311)
 *   REV_US_LOCUT  LOW CUT's one-pole on the send, only while it is on;
 *   REV_US_MAX    a TYPE's budget: HALL + 200 us (the coordinator's cost gate, every engine). */
#define REV_US_FRONT 88u
#define REV_US_HALL (FX_US_REVERB - REV_US_FRONT)
#define REV_US_DEC 23u
#define REV_US_LOCUT 16u
#define REV_US_MAX (REV_US_HALL + 200u)
_Static_assert(REV_US_DEC + REV_US_PLATE <= REV_US_MAX && REV_US_DEC + REV_US_INF <= REV_US_MAX,
               "a REVERB TYPE over its CPU budget (REV_US_MAX: HALL + 200 us)");
static const uint16_t REV_US[RV_N] = {0, REV_US_HALL, REV_US_DEC + REV_US_PLATE, REV_US_DEC + REV_US_INF};

/* INFINITE's AUTO SIZE: fixed, not MIX (the user, 2026-10-25: MIX bent the pitch). A SIZE change glides its 8 lines'
 * lengths, a Doppler bend of the whole tail (-50 .. -250 cents while MIX moved 64 -> 120 under a held tail), and its
 * tails ring 4 s .. for ever: MIX is the level only. 64: AUTO's size at MIX 64 */
#define RV_INF_AUTO 64
/* PLATE's AUTO SIZE: MIX, latched while the plate rings (a size step moves its delays: MIX turned under a tail
 * scrambled its pitch, up to -266 cents); MIX's size applies from silence (rev_plate_active 0, or a start: -1). The
 * presets render as before (a preset's load re-takes the size, rev_relatch) */
static REV_BSS int32_t rv_pz;
/* the main loop: a preset or an undo (a whole sound) takes MIX's size at once, ringing or not, as before */
static void rev_relatch(void) { *(volatile int32_t *)&rv_pz = -1; }
#define RV_FADE 8u                                  /* control blocks of a TYPE change's fade: 256 frames, 5.8 ms */
static REV_DATA uint8_t rv_cur = RV_HALL;           /* ISR: the TYPE that runs (0: none yet, HALL waits for rev_poll) */
static REV_BSS uint8_t rv_fade;                     /* ISR: the fade's control blocks left, 0 = none */
static REV_BSS volatile uint8_t rv_hall_dirty;      /* ISR sets it when HALL stops with its lines full; rev_poll clears */
/* the 1:2 up of the mid / side (the TYPE's half rate to 44.1 kHz): the odd phase of rev_down's half-band
 * ((40, -152, 624, 624, -152, 40) / 1024; the even samples are the TYPE's own, 2 half-rate samples late): the images
 * of the tank's band (a tone at f: 22.05 kHz - f), which the old (last + this) / 2 left only 14 dB down at 6 kHz, are
 * 41 dB and more down for every tone to 6.9 kHz. rv_m / rv_s: the last RV_QZ half-rate samples, then the block's */
#define RV_QZ 5u
#define RV_UPI(u) ((RV_HB((u) + 0, 1) + 512) >> 10)
static REV_BSS int32_t rv_m[RV_QZ + HL_NC], rv_s[RV_QZ + HL_NC];   /* the TYPE's mid / side, at RV_QZ */
static REV_DATA int32_t rv_dk = 64;                 /* DECAY for the kernels, as it glides */
static REV_BSS int32_t rv_hp, rv_he;                /* the kernels' DC blocker (as rev_hall's) */
static REV_BSS int32_t rv_lc, rv_lca, rv_lcv;       /* LOW CUT: the low-pass (x 256), its coefficient, its knob */
static REV_BSS int32_t rv_x[HL_NC];

/* LOW CUT: x - lowpass(x), one pole, a = 2 pi fc / fs in Q15: 3734 at 800 Hz (127) .. 1/40 of it, 20 Hz (1) */
static int32_t rev_locut_a(int32_t v) { return (3734 * (int32_t)exp2_neg((uint32_t)(127 - v) * 2767u)) >> 15; }
static __attribute__((noinline)) void rev_locut(int32_t *x, int32_t v)
{
    uint32_t j;
    int32_t lc = rv_lc, a;
    if (v != rv_lcv) { rv_lcv = v; rv_lca = v ? rev_locut_a(v) : 0; }
    if (!v) { rv_lc = 0; return; }
    a = rv_lca;
    for (j = 0; j < CTL; j++) {
        int32_t u = clampi(x[j], -(1 << 21), 1 << 21);   /* (2^21: 16x full scale; not 2^22, a ROM-range constant) */
        lc += (int32_t)(((int64_t)((u << 8) - lc) * a) >> 15);
        x[j] = u - (lc >> 8);
    }
    rv_lc = lc;
}
/* a kernel's input: rev_hall's (rev_down: the half-band decimation, HL_IN, the DC blocker), into rv_x */
static void rev_dec(const int32_t *in) { FR_FAR(rev_down)(in, &rv_hp, &rv_he, rv_x); }
/* TYPE t starts, its lines reading as empty; HALL left full is cleared by the main loop first (rv_cur 0 meanwhile) */
static __attribute__((noinline)) void rev_begin(uint32_t t)
{
    if (rv_cur == RV_HALL) rv_hall_dirty = 1;
    memset(rv_m, 0, sizeof rv_m); memset(rv_s, 0, sizeof rv_s); rv_hp = rv_he = 0;
    if (t == RV_HALL && rv_hall_dirty) { rv_cur = 0; return; }
    rv_cur = (uint8_t)t;
    if (t == RV_PLATE) rev_plate_start(&rev_u.p), rv_pz = -1;
    else if (t == RV_INF) rev_inf_start(&rev_u.i);
}
static int rev_active(uint32_t t)
{
    return t == RV_PLATE ? rev_plate_active(&rev_u.p) : t == RV_INF ? rev_inf_active(&rev_u.i) : 0;
}
/* main loop: HALL's lines to 0 after another TYPE took over (14 KB: ~0.4 ms, never in the audio interrupt) */
static void rev_poll(void)
{
    if (rv_hall_dirty && *(volatile uint8_t *)&rv_cur != RV_HALL) {
        memset(rev_comb, 0, sizeof rev_comb); memset(rev_ap, 0, sizeof rev_ap); memset(&hl, 0, sizeof hl);
        rv_hall_dirty = 0;
    }
}
/* the main loop: INFINITE holds its tail (SUSTAIN or FREEZE, DECAY >= 120), so the sound never falls silent and no flash write may
 * start (presets.c shows a waiting save; leaving the freeze lets the tail end and the save go through) */
static int rev_frozen(void)       /* 0, or 1 SUSTAIN (DECAY 120..126), 2 FREEZE (127) */
{
    return !rv_running || *(volatile uint8_t *)&rv_cur != RV_INF || fx_p[FXP_RV_TYPE] != RV_INF || rv_dk < 120 ? 0
           : rv_dk < 127 ? 1 : 2;
}
/* CC 10 (fx_reset_state, which has just cleared HALL's lines): every TYPE's state to rest */
static void rev_reset_state(void)
{
    memset(rv_m, 0, sizeof rv_m); memset(rv_s, 0, sizeof rv_s); memset(rv_inb, 0, sizeof rv_inb); rv_hp = rv_he = 0;
    rv_lc = 0; rv_fade = 0; rv_hall_dirty = 0;
    if (!rv_cur) rv_cur = RV_HALL;
    if (rv_cur == RV_PLATE) rev_plate_start(&rev_u.p), rv_pz = -1;
    else if (rv_cur == RV_INF) rev_inf_start(&rev_u.i);
}

#define RV_RET(w) ((int32_t)(((int64_t)(w) * rk) >> 30) + (int32_t)((uint32_t)(w) >> 31))
/* MIX moved while a tail rings (0.9.6, BUGHUNT #18: the return eases off as MIX rises, x1 .. x0.5, so MIX 127 -> 0
 * doubled the ringing tail, +5..6.6 dB, and the send stepped to 0 in one block: 41-72x a held chord's d2). The return
 * keeps the MIX the tail started with (rv_vl: latched while the reverb runs; at rest, or with the out under -66 dBFS
 * for two blocks, it follows MIX, so a MIX set in silence renders exactly as before), and the send carries the rest of the law:
 * v 258 (254 - v) / (254 - rv_vl), the same wet level for what goes in from now on; the send ramps sample by sample
 * to its new value over the block (rv_sk: the last block's), only while it moves */
static int32_t rv_vl RH_SP_SEC(".data.fx_rvvl") = -1, rv_sk RH_SP_SEC(".bss.fx_rvsk");
/* MOD, gliding (0.9.6, BUGHUNT #10: HALL's read offset (md + 2) << 8 moved a whole sample every 8 MOD steps, INFINITE's
 * mean read depth and PLATE's swing followed MOD at once: MOD 0 -> 127 in one CC 32x a HALL tail's d2): HALL's md in Q8
 * (rv_md8) moves 1/8 sample a sub-block (0 -> 127 in 93 ms); the others' MOD x 16 (rv_mg) 1 a sub-block (127 in
 * 1.5 s: INFINITE reads a line every 11.6 ms; a whole MOD step there moved its read 0.375 samples at once, 3x its
 * slowest LFO's step); at rest (the reverb stopped) both are MOD's own: a MOD that has not moved renders as before */
static int32_t rv_md8 RH_SP_SEC(".bss.fx_rvmd8"), rv_mg RH_SP_SEC(".bss.fx_rvmg");
__attribute__((noinline)) static void rv_send_ramp(const int32_t *L, const int32_t *R, int32_t s0, int32_t s1)
{
    int32_t q = s0 << 5, dq = s1 - s0;                  /* (Q5: 32 steps of dq / 32 add up to s1 exactly) */
    uint32_t j;
    for (j = 0; j < CTL; j++) {
        q += dq;
        rv_in[j] = clampi((int32_t)(((int64_t)((L[j] + R[j]) >> 1) * (q >> 5)) >> 15), -131071, 131071);
    }
}
static void fx_reverb(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t v = fx_p[FXP_RV_DEPTH], tgt = fx_p[FXP_RV_TYPE], zv = fx_p[FXP_RV_SIZE];
    int32_t send = tgt ? v * 258 : 0, size = 40 + (zv ? zv : v) * 70 / 127;
    int32_t dv = fx_p[FXP_RV_DECAY], tv = fx_p[FXP_RV_TONE], md = (fx_p[FXP_RV_MOD] + 4) >> 3, lcv = fx_p[FXP_RV_LOCUT];
    int32_t off = ((dv - 64) * 15) / 32;                /* -30 .. +29 size steps: x0.5 .. x1.96 in T60 */
    int32_t damp = tv >= 64 ? 60 - ((tv - 64) * 60) / 63 : 60 + ((64 - tv) * 90) / 64;   /* 150 .. 60 .. 0 */
    uint32_t sb, i, j, pd = FX_PDLY_SAMPLES(fx_p[FXP_RV_PREDLY]);
    int32_t rk, vl, sk;
    int quiet = !rv_running;
    if (tgt >= (int32_t)RV_N) tgt = RV_HALL;
    if (!rv_running) { rv_md8 = md << 8; rv_mg = fx_p[FXP_RV_MOD] << 4; }
    if (!rv_running || rv_vl < 0 || fx_quiet_blocks >= 2u) { rv_vl = v; rv_sk = send; }   /* (at rest, or the out
                                                    * under -66 dBFS for 2 blocks, the boot's silence too: the return
                                                    * follows MIX) */
    vl = rv_vl;
    if (v != vl && send) send = (int32_t)(((int64_t)send * (254 - v)) / (254 - vl));
    rk = (254 - vl) * 4227330 + (4 * (254 - vl)) / 254 + 1;   /* floor((254 - vl) 2^30 / 254) + 1 */
    sk = rv_sk; rv_sk = send;
    if (!send && !rv_running && !rv_fade) return;
    rv_running = 1;
    for (sb = 0; sb < n; sb += CTL) {
        int32_t act = 0, *om = rv_m + RV_QZ, *os = rv_s + RV_QZ;
        uint32_t cur;
        if (tgt && (uint32_t)tgt != rv_cur && !rv_fade) {
            if (quiet || !rv_cur) rev_begin((uint32_t)tgt);
            else rv_fade = RV_FADE;
        }
        for (j = 0; j < RV_DZ; j++) rv_inb[j] = rv_inb[CTL + j];   /* (rev_down's history) */
        if (sk != send)                                /* (MIX moved: the send's ramp over the block) */
            rv_send_ramp(L + sb, R + sb, sk + (int32_t)(((int64_t)(send - sk) * (int32_t)sb) / (int32_t)n),
                         sk + (int32_t)(((int64_t)(send - sk) * (int32_t)(sb + CTL)) / (int32_t)n));
        else
        for (j = 0; j < CTL; j++)                      /* 64-bit: the bus can be several times full scale here
                                                        * (feedback effects, many v127 notes: fuzz seed 95) */
            rv_in[j] = clampi((int32_t)(((int64_t)((L[sb + j] + R[sb + j]) >> 1) * send) >> 15), -131071, 131071);
        if (lcv || rv_lc) rev_locut(rv_in, lcv);
        if (pd || pd_on) fx_predelay(rv_in, pd);
        if (rv_off != off) rv_off += rv_off < off ? 1 : -1;
        if (rv_damp != damp) rv_damp += rv_damp < damp ? (damp - rv_damp > 2 ? 2 : damp - rv_damp)
                                                      : -(rv_damp - damp > 2 ? 2 : rv_damp - damp);
        if (rv_dk != dv) rv_dk += rv_dk < dv ? 1 : -1;
        if (rv_md8 != md << 8) rv_md8 += PH_CL((md << 8) - rv_md8, -32, 32);
        if (rv_mg != fx_p[FXP_RV_MOD] << 4) rv_mg += rv_mg < fx_p[FXP_RV_MOD] << 4 ? 1 : -1;
        cur = rv_cur;
        if (cur) for (i = 0; i < RV_QZ; i++) rv_m[i] = om[HL_NC - RV_QZ + i], rv_s[i] = os[HL_NC - RV_QZ + i];
        if (cur == RV_HALL) rev_hall(rv_in, size + rv_off, rv_damp, rv_md8, om, os);
        else if (cur) {
            rev_ctl_t c;
            if (cur == RV_PLATE && (rv_pz < 0 || !rev_plate_active(&rev_u.p))) rv_pz = v;   /* (AUTO: from silence) */
            c.decay = rv_dk; c.damp = rv_damp; c.mod = rv_mg;
            c.size = zv ? zv : cur == RV_INF ? RV_INF_AUTO : cur == RV_PLATE ? rv_pz : v;
            c.freeze = cur != RV_INF || tgt != RV_INF || rv_dk < 120 ? 0 : rv_dk < 127 ? 1 : 2;   /* (OFF or a TYPE
                                                        * change releases the hold: the tail decays, silence comes) */
            rev_dec(rv_in);
            if (cur == RV_PLATE) rev_plate_run(&rev_u.p, rv_x, om, os, &c);
            else FR_FAR(rev_inf_run)(&rev_u.i, rv_x, om, os, &c);
        }
        /* the 2x upsample to mid / side (RV_UPI between u[-1] and u[0], then u[0]: 2 half-rate samples late) and the
         * return, which eases off as the send rises: x1 .. x0.5 (0.6): (w * (254 - v)) / 254 exactly, as a multiply
         * and a shift (|w| <= 2^19 here, the half-band's overshoot included; every v and w: tests/py/test_rev_hall.py).
         * At MIX 0 or OFF (a tail ringing out) only the tail's end matters (act: the last RV_QZ samples, which the
         * next block's output still reads, all 0) */
        if (cur && (rv_fade || (vl && !send))) {        /* a TYPE change: the TYPE's out x (fade / 8) .. ((fade - 1) / 8),
                                                        * in place; OFF with MIX up: the tail's end watched (act; at
                                                        * MIX 0 the loop below watches it, as it always did) */
            int32_t g = (int32_t)rv_fade << 12;
            for (i = 0; i < HL_NC; i++, g -= 256) {
                act |= om[i] | os[i];
                if (rv_fade) { om[i] = mul15(om[i], g); os[i] = mul15(os[i], g); }
            }
            if (rv_fade) act = 1;
        }
        if (!cur) {}
        else if (vl)
            for (i = 0, j = sb; i < HL_NC; i++, j += 2u) {
                const int32_t *u = rv_m + RV_QZ - 2u + i, *q = rv_s + RV_QZ - 2u + i;
                int32_t wm0 = RV_RET(RV_UPI(u)), ws0 = RV_RET(RV_UPI(q)), wm1 = RV_RET(u[0]), ws1 = RV_RET(q[0]);
                L[j] += wm0 + ws0;
                R[j] += wm0 - ws0;
                L[j + 1u] += wm1 + ws1;
                R[j + 1u] += wm1 - ws1;
            }
        else
            for (i = 0, j = sb; i < HL_NC; i++, j += 2u) {
                const int32_t *u = rv_m + RV_QZ - 2u + i, *q = rv_s + RV_QZ - 2u + i;
                int32_t m0 = RV_UPI(u), s0 = RV_UPI(q), m1 = u[0], s1 = q[0];
                L[j] += m0 + s0;
                R[j] += m0 - s0;
                L[j + 1u] += m1 + s1;
                R[j + 1u] += m1 - s1;
                act |= u[2] | q[2];
            }
        if (rv_fade && !--rv_fade) {                   /* faded out: the new TYPE (OFF meanwhile: the old one afresh) */
            rev_begin(tgt ? (uint32_t)tgt : cur ? cur : RV_HALL);
        }
        else if (!send && !act && !rev_active(cur)) {  /* the tail has ended in exact silence (the pre-delay line:
                                                        * cleared at its next use) */
            rv_running = 0; pd_on = 0; quiet = 1;
            memset(rv_inb, 0, sizeof rv_inb);          /* (LOW CUT's last drops: the next start from silence) */
        }
    }
}

/* the reverb's cost while it runs (as before: while MIX is up or a tail rings): the TYPE that runs, or the one a
 * pending change brings if it costs more; LOW CUT on top */
static uint32_t rev_cost_us(void)
{
    uint32_t t = (uint32_t)fx_p[FXP_RV_TYPE], a = rv_cur, us;
    if (t >= RV_N) t = RV_HALL;
    if (!rv_running && !(fx_p[FXP_RV_DEPTH] && t)) return 0;
    us = REV_US[a];
    if (t && REV_US[t] > us) us = REV_US[t];
    return REV_US_FRONT + us + (fx_p[FXP_RV_LOCUT] ? REV_US_LOCUT : 0u);
}
