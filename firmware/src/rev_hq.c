/* SPDX-License-Identifier: GPL-3.0-only */
/* The REVERB's TYPEs at the full rate (FR_REV_HQ 1, a build switch; off by default): the framework of rev_types.c with
 * every TYPE a kernel at 44.1 kHz, so the tails keep the band up to ~20 kHz (the half-rate TYPEs stop at ~11 kHz) and
 * no down / up sampling filter sits in the path. Included by rev_types.c in place of its own framework; the panel, the
 * ranges, the records and the behaviour are the same (MIX, DECAY, TONE, PRE-DELAY, SIZE, MOD, LOW CUT; INFINITE's
 * SUSTAIN / FREEZE; tails end in exact silence; a TYPE change fades over RV_FADE control blocks; CC 10 to rest):
 *   HALL     rev_hq_hall.c   16 lines, a Hadamard mix, 4 input diffusers (the half-rate HALL: 8 lines, 2 diffusers)
 *   PLATE    rev_hq_plate.c  the figure-8 plate tank at 44.1 kHz (the half-rate one ran the paper's design at 22 kHz)
 *   INFINITE rev_hq_inf.c    the bright tank as designed (4 diffusers, 4 line allpasses, 8 modulated lines) at 44.1 kHz
 * The kernel API (as rev_types.c's, at the full rate): one control block = CTL = 32 samples a call;
 *   in[]: CTL mono samples, sent, LOW CUT, pre-delayed, level-scaled (HL_IN) and DC-blocked (rq_in);
 *   mid[] / side[]: CTL samples in the half-rate kernels' scale; the framework adds L = M + S, R = M - S with MIX's
 *   return (no upsample: the output is not delayed).
 * HALL is a kernel here (in the union rev_u): fx.c's rev_hall and its lines are not used (fx.c makes them small). */

#define FR_REV_HQ_ON 1
#define RQ_NC CTL                                       /* a kernel's samples a call */
#define RQ_FLASH 0                                      /* (nothing) */
#include "rev_hq_hall.c"
#include "rev_hq_plate.c"
#include "rev_hq_inf.c"

static union { rev_hqh_t h; rev_plate_t p; rev_inf_t i; } rev_u POOL;

/* the cost, us per 256-frame block: REV_US_FRONT the framework (the send, the pre-delay at rest, the input's level and
 * DC blocker, the return at the full rate; the half-rate framework's 88 less its half-bands); the kernels' own at
 * their #defines (instruction counts of the compiled loops, rev_hq_*.c) */
#define REV_US_FRONT 70u
#define REV_US_LOCUT 16u
#define REV_US_HQ_MAX 1000u                            /* a full-rate TYPE's budget (the core-1 plan: the reverb before
                                                         * the voices; ~1000-2000 us a block freed on core 0) */
_Static_assert(REV_US_HQH <= REV_US_HQ_MAX && REV_US_PLATE <= REV_US_HQ_MAX && REV_US_INF <= REV_US_HQ_MAX,
               "a full-rate REVERB TYPE over its CPU budget");
static const uint16_t REV_US[RV_N] = {0, REV_US_HQH, REV_US_PLATE, REV_US_INF};

#define RV_INF_AUTO 64                                  /* (rev_types.c: INFINITE's AUTO SIZE, fixed) */
static REV_BSS int32_t rv_pz;
static void rev_relatch(void) { *(volatile int32_t *)&rv_pz = -1; }
#define RV_FADE 8u
static REV_DATA uint8_t rv_cur = RV_HALL;
static REV_BSS uint8_t rv_fade;
static REV_BSS int32_t rv_m[RQ_NC], rv_s[RQ_NC];        /* the TYPE's mid / side, this control block */
static REV_DATA int32_t rv_dk = 64;
static REV_BSS int32_t rv_hp, rv_he;                    /* the kernels' DC blocker (rq_in) */
static REV_BSS int32_t rv_lc, rv_lca, rv_lcv;
static REV_BSS int32_t rv_x[RQ_NC];

static int32_t rev_locut_a(int32_t v) { return (3734 * (int32_t)exp2_neg((uint32_t)(127 - v) * 2767u)) >> 15; }
static __attribute__((noinline)) void rev_locut(int32_t *x, int32_t v)
{
    uint32_t j;
    int32_t lc = rv_lc, a;
    if (v != rv_lcv) { rv_lcv = v; rv_lca = v ? rev_locut_a(v) : 0; }
    if (!v) { rv_lc = 0; return; }
    a = rv_lca;
    for (j = 0; j < CTL; j++) {
        int32_t u = clampi(x[j], -(1 << 21), 1 << 21);
        lc += (int32_t)(((int64_t)((u << 8) - lc) * a) >> 15);
        x[j] = u - (lc >> 8);
    }
    rv_lc = lc;
}
/* the kernels' input at the full rate: the clamp and HL_IN as rev_down, its DC blocker at the same corner (>> 6 at
 * twice the rate: ~110 Hz -> 0.9.5's ~110 Hz) */
static FR_RAM __attribute__((noinline)) void rq_in(const int32_t *in, int32_t *out)
{
    int32_t hp = rv_hp, he = rv_he;
    uint32_t i;
    for (i = 0; i < RQ_NC; i++) {
        int32_t x = mulq15(clampi(in[i], -131071, 131071), HL_IN), v;
        v = x - hp + he;
        he = v & 63;
        hp += v >> 6;
        out[i] = x - hp;
    }
    rv_hp = hp, rv_he = he;
}
static __attribute__((noinline)) void rev_begin(uint32_t t)
{
    memset(rv_m, 0, sizeof rv_m); memset(rv_s, 0, sizeof rv_s); rv_hp = rv_he = 0;
    rv_cur = (uint8_t)t;
    if (t == RV_HALL) rev_hqh_start(&rev_u.h);
    else if (t == RV_PLATE) rev_plate_start(&rev_u.p), rv_pz = -1;
    else if (t == RV_INF) rev_inf_start(&rev_u.i);
}
static int rev_active(uint32_t t)
{
    return t == RV_HALL ? rev_hqh_active(&rev_u.h) : t == RV_PLATE ? rev_plate_active(&rev_u.p)
           : t == RV_INF ? rev_inf_active(&rev_u.i) : 0;
}
static void rev_poll(void) {}                           /* (HALL is a kernel: nothing to clear in the main loop) */
static int rev_frozen(void)
{
    return !rv_running || *(volatile uint8_t *)&rv_cur != RV_INF || fx_p[FXP_RV_TYPE] != RV_INF || rv_dk < 120 ? 0
           : rv_dk < 127 ? 1 : 2;
}
static void rev_reset_state(void)
{
    memset(rv_m, 0, sizeof rv_m); memset(rv_s, 0, sizeof rv_s); memset(rv_inb, 0, sizeof rv_inb); rv_hp = rv_he = 0;
    rv_lc = 0; rv_fade = 0;
    if (!rv_cur) rv_cur = RV_HALL;
    rev_begin(rv_cur);
}

#define RV_RET(w) ((int32_t)(((int64_t)(w) * rk) >> 30) + (int32_t)((uint32_t)(w) >> 31))
static int32_t rv_vl RH_SP_SEC(".data.fx_rvvl") = -1, rv_sk RH_SP_SEC(".bss.fx_rvsk");
static int32_t rv_mg RH_SP_SEC(".bss.fx_rvmg");
__attribute__((noinline)) static void rv_send_ramp(const int32_t *L, const int32_t *R, int32_t s0, int32_t s1)
{
    int32_t q = s0 << 5, dq = s1 - s0;
    uint32_t j;
    for (j = 0; j < CTL; j++) {
        q += dq;
        rv_in[j] = clampi((int32_t)(((int64_t)((L[j] + R[j]) >> 1) * (q >> 5)) >> 15), -131071, 131071);
    }
}
/* the return of one control block, with MIX's law (rk) or (MIX 0, a tail ringing out) as it is; act: its OR */
static FR_RAM __attribute__((noinline)) int32_t rq_ret(int32_t *L, int32_t *R, int32_t rk)
{
    uint32_t j;
    int32_t act = 0;
    if (rk)
        for (j = 0; j < RQ_NC; j++) {
            int32_t m = RV_RET(rv_m[j]), s = RV_RET(rv_s[j]);
            L[j] += m + s;
            R[j] += m - s;
        }
    else
        for (j = 0; j < RQ_NC; j++) {
            int32_t m = rv_m[j], s = rv_s[j];
            L[j] += m + s;
            R[j] += m - s;
            act |= m | s;
        }
    return act;
}
static void fx_reverb(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t v = fx_p[FXP_RV_DEPTH], tgt = fx_p[FXP_RV_TYPE], zv = fx_p[FXP_RV_SIZE];
    int32_t send = tgt ? v * 258 : 0, size = 40 + (zv ? zv : v) * 70 / 127;
    int32_t dv = fx_p[FXP_RV_DECAY], tv = fx_p[FXP_RV_TONE], lcv = fx_p[FXP_RV_LOCUT];
    int32_t off = ((dv - 64) * 15) / 32;
    int32_t damp = tv >= 64 ? 60 - ((tv - 64) * 60) / 63 : 60 + ((64 - tv) * 90) / 64;
    uint32_t sb, i, j, pd = FX_PDLY_SAMPLES(fx_p[FXP_RV_PREDLY]);
    int32_t rk, vl, sk;
    int quiet = !rv_running;
    if (tgt >= (int32_t)RV_N) tgt = RV_HALL;
    if (!rv_running) rv_mg = fx_p[FXP_RV_MOD] << 4;
    if (!rv_running || rv_vl < 0 || fx_quiet_blocks >= 2u) { rv_vl = v; rv_sk = send; }
    vl = rv_vl;
    if (v != vl && send) send = (int32_t)(((int64_t)send * (254 - v)) / (254 - vl));
    rk = (254 - vl) * 4227330 + (4 * (254 - vl)) / 254 + 1;
    sk = rv_sk; rv_sk = send;
    if (!send && !rv_running && !rv_fade) return;
    rv_running = 1;
    for (sb = 0; sb < n; sb += CTL) {
        int32_t act = 0;
        uint32_t cur;
        if (tgt && (uint32_t)tgt != rv_cur && !rv_fade) {
            if (quiet || !rv_cur) rev_begin((uint32_t)tgt);
            else rv_fade = RV_FADE;
        }
        if (sk != send)
            rv_send_ramp(L + sb, R + sb, sk + (int32_t)(((int64_t)(send - sk) * (int32_t)sb) / (int32_t)n),
                         sk + (int32_t)(((int64_t)(send - sk) * (int32_t)(sb + CTL)) / (int32_t)n));
        else
        for (j = 0; j < CTL; j++)
            rv_in[j] = clampi((int32_t)(((int64_t)((L[sb + j] + R[sb + j]) >> 1) * send) >> 15), -131071, 131071);
        if (lcv || rv_lc) rev_locut(rv_in, lcv);
        if (pd || pd_on) fx_predelay(rv_in, pd);
        if (rv_off != off) rv_off += rv_off < off ? 1 : -1;
        if (rv_damp != damp) rv_damp += rv_damp < damp ? (damp - rv_damp > 2 ? 2 : damp - rv_damp)
                                                      : -(rv_damp - damp > 2 ? 2 : rv_damp - damp);
        if (rv_dk != dv) rv_dk += rv_dk < dv ? 1 : -1;
        if (rv_mg != fx_p[FXP_RV_MOD] << 4) rv_mg += rv_mg < fx_p[FXP_RV_MOD] << 4 ? 1 : -1;
        cur = rv_cur;
        if (cur) {
            rev_ctl_t c;
            if (cur == RV_PLATE && (rv_pz < 0 || !rev_plate_active(&rev_u.p))) rv_pz = v;
            c.decay = rv_dk; c.damp = rv_damp; c.mod = rv_mg;
            c.size = cur == RV_HALL ? size + rv_off
                     : zv ? zv : cur == RV_INF ? RV_INF_AUTO : rv_pz;   /* (HALL: its size from MIX / SIZE and
                                                        * DECAY, as rev_hall's; the others: SIZE or AUTO) */
            c.freeze = cur != RV_INF || tgt != RV_INF || rv_dk < 120 ? 0 : rv_dk < 127 ? 1 : 2;
            FR_FAR(rq_in)(rv_in, rv_x);
            if (cur == RV_HALL) FR_FAR(rev_hqh_run)(&rev_u.h, rv_x, rv_m, rv_s, &c);
            else if (cur == RV_PLATE) rev_plate_run(&rev_u.p, rv_x, rv_m, rv_s, &c);
            else FR_FAR(rev_inf_run)(&rev_u.i, rv_x, rv_m, rv_s, &c);
        }
        if (cur && (rv_fade || (vl && !send))) {
            int32_t g = (int32_t)rv_fade << 12;
            for (i = 0; i < RQ_NC; i++, g -= 4096 / (int32_t)RQ_NC) {
                act |= rv_m[i] | rv_s[i];
                if (rv_fade) { rv_m[i] = mul15(rv_m[i], g); rv_s[i] = mul15(rv_s[i], g); }
            }
            if (rv_fade) act = 1;
        }
        if (cur) act |= FR_FAR(rq_ret)(L + sb, R + sb, vl ? rk : 0);
        if (rv_fade && !--rv_fade) {
            rev_begin(tgt ? (uint32_t)tgt : cur ? cur : RV_HALL);
        }
        else if (!send && !act && !rev_active(cur)) {
            rv_running = 0; pd_on = 0; quiet = 1;
            memset(rv_inb, 0, sizeof rv_inb);
        }
    }
}

static uint32_t rev_cost_us(void)
{
    uint32_t t = (uint32_t)fx_p[FXP_RV_TYPE], a = rv_cur, us;
    if (t >= RV_N) t = RV_HALL;
    if (!rv_running && !(fx_p[FXP_RV_DEPTH] && t)) return 0;
    us = REV_US[a];
    if (t && REV_US[t] > us) us = REV_US[t];
    return REV_US_FRONT + us + (fx_p[FXP_RV_LOCUT] ? REV_US_LOCUT : 0u);
}
