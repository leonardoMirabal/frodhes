/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes bar engine: the code (its types, tables and state are in bars.h, included by engine.c). frodhes.c
 * includes this file last, after every other part of the app, so adding or growing an engine never moves or
 * re-inlines the Rhodes and effects code (measured: +150 us on the Rhodes worst case when bars.c sat before it). */

static int32_t bar_sat(int32_t v)
{
#ifdef DINGHY_HOST
    if (v > (1 << 28) - 1 || v < -((1 << 28) - 1)) bar_h_sat++;
#endif
    return sat28(v);
}

/* ---- log2 in Q8 (a linear mantissa: within 0.09, 0.5 dB) and back; 32-bit shifts only */
static int32_t bar_log2_32(uint32_t v)
{
    int32_t p = 0;
    uint32_t t = v;
    if (!v) return BAR_LZERO;
    if (t >> 16) { t >>= 16; p += 16; }
    if (t >> 8) { t >>= 8; p += 8; }
    if (t >> 4) { t >>= 4; p += 4; }
    if (t >> 2) { t >>= 2; p += 2; }
    if (t >> 1) p += 1;
    return p * 256 + (int32_t)((p >= 8 ? v >> (p - 8) : v << (8 - p)) & 255u);
}
static int32_t bar_log2_64(uint64_t q)
{
    uint32_t hi = (uint32_t)(q >> 32), lo = (uint32_t)q;
    int32_t p, s;
    if (!hi) return bar_log2_32(lo);
    p = (bar_log2_32(hi) >> 8) + 32;
    s = p - 8;                                      /* >= 24 */
    return p * 256 + (int32_t)((s >= 32 ? hi >> (s - 32) : (hi << (32 - s)) | (lo >> s)) & 255u);
}
static int32_t bar_exp2(int32_t l)                  /* 2^(l / 256); 0 under 1 */
{
    int32_t i = l >> 8;
    uint32_t m = 256u + (uint32_t)(l & 255);
    if (l < 0) return 0;
    if (i >= 30) return 0x7FFFFFFF;
    return (int32_t)(i >= 8 ? m << (i - 8) : m >> (8 - i));
}

/* k p / 2^32 for a Q32 coefficient 0 <= k < 2^31 and |p| < 2^57, without losing p's low bits when p is small (the
 * products stay under 2^63) */
static int64_t bar_q32mul(int32_t k, int64_t p)
{
    int64_t a = p < 0 ? -p : p;
    if (a < (1ll << 31)) return ((int64_t)k * p) >> 32;
    if (a < (1ll << 47)) return ((int64_t)k * (p >> 16)) >> 16;
    return ((int64_t)k * (p >> 24)) >> 8;
}

/* a slot's log2 amplitude (Q8, Q25 units) from x[n], x[n-1]: x1^2 - a1 x1 x2 + a2 x2^2 (= (x1 - x2)^2 + c1 x1 x2 -
 * c2 x2^2) = A^2 sin^2(theta) for a damped sinusoid. Q32 form: the c terms on the products x1 x2 and x2^2 (rounding
 * c1 x1 first lost them on the low modes near the end: c1 x1 >> 32 is 0 for a 65 Hz mode under ~8 LSB out, and the
 * level read near 0 cut the voice at up to 7 LSB) */
static int32_t bar_lamp(const bar_ms_t *s)
{
    int64_t q;
    if (s->lo) {
        int32_t d = s->x1 - s->x2;
        q = (int64_t)d * d + bar_q32mul(s->k1, (int64_t)s->x1 * s->x2) - bar_q32mul(s->k2, (int64_t)s->x2 * s->x2);
    } else {
        int32_t c = (int32_t)(((int64_t)s->k1 * s->x2) >> 30), d = (int32_t)(((int64_t)s->k2 * s->x2) >> 30);
        q = (int64_t)s->x1 * (s->x1 - c) + (int64_t)s->x2 * d;
    }
    if (q <= 0) return BAR_LZERO;
    return (bar_log2_64((uint64_t)q) >> 1) - s->lsin;
}

static int32_t bar_sat31(int64_t v)
{
#ifdef DINGHY_HOST
    if (v > 0x7FFFFFFF || v < -0x7FFFFFFF) bar_h_sat++;
#endif
    return v > 0x7FFFFFFF ? 0x7FFFFFFF : v < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)v;
}

/* (e, eps) -> the two-multiply form's coefficients and the force gain. A mode under ~4.9 kHz (c1 < 1/2) runs in Q32:
 * x' = 2x - xp + hi(err - c1 x + c2 xp), err = lo (the pole 4x finer than Q30, no shifts); above, in Q30:
 * x' = (err + a1 x - a2 xp) >> 30. The force: F << 12 times be = b e >> 30 (Q32) or >> 32 (Q30). */
static void bar_coefs(bar_ms_t *s)
{
    int64_t c1 = ((int64_t)s->eps << 2) + ((((int64_t)s->e * s->e) + (1 << 27)) >> 28);
    uint8_t lo = c1 < 0x7FFFFFFF;
    if (lo != s->lo) s->err = lo ? (int32_t)((uint32_t)s->err << 2) : (int32_t)((uint32_t)s->err >> 2);   /* rescale */
    s->lo = lo;
    if (lo) {
        s->k1 = (int32_t)c1;
        s->k2 = s->eps << 2;
        s->be = bar_sat31(((int64_t)s->b * s->e) >> 30);
    } else {
        s->k1 = (int32_t)(((int64_t)2 << 30) - s->eps - ((((int64_t)s->e * s->e) + (1 << 29)) >> 30));
        s->k2 = (1 << 30) - s->eps;
        s->be = bar_sat31(((int64_t)s->b * s->e) >> 32);
    }
}

/* log2 sin(theta), Q8, from the coefficients: sin^2 = 1 - a1^2 / (4 a2) */
static int32_t bar_lsin(const bar_ms_t *s)
{
    int64_t num;
    if (s->lo) {                                    /* (c1 - c2 - c1^2 / 4) / (1 - c2), in Q32 */
        num = (int64_t)s->k1 - s->k2 - (((int64_t)s->k1 * s->k1) >> 34);
        return num > 0 ? (bar_log2_64((uint64_t)num) - bar_log2_64((1ull << 32) - (uint32_t)s->k2)) >> 1 : 0;
    }
    num = ((int64_t)s->k2 << 32) - (int64_t)s->k1 * s->k1;
    return num > 0 ? (bar_log2_64((uint64_t)num) - bar_log2_32((uint32_t)s->k2) - 32 * 256) >> 1 : 0;
}

static void bar_load_pulse(bar_voice_t *v, uint32_t vel)
{
    uint32_t ty = bar_sub < BAR_NTYPE ? bar_sub : 0u;   /* constant while the voice lives (engine.c C1) */
    uint32_t key = (uint32_t)(v->k - BAR_KEYS[ty]), fam = (uint32_t)bar_fam, f = fam >> 8, f2, l, l2, i;
    uint32_t pb = BAR_PBAND[v->k->band];               /* the key's pulse band (shared bank) */
    int32_t a = (int32_t)(fam & 255u), w, pa, pa2, pr, pr2;
    uint32_t vi = RH_VPOS_I[vel], vf = RH_VPOS_F[vel];
    const int16_t *p11, *p12, *p21, *p22;
    if (f > BAR_NFAM - 1u) { f = BAR_NFAM - 1u; a = 0; }
    f2 = f + 1u < BAR_NFAM ? f + 1u : f;
    l = RH_VLAY_I[vel]; l2 = l + 1u < BAR_NLAYER ? l + 1u : l; w = RH_VLAY_W[vel];
    p11 = BAR_PULSE[pb][f][l]; p12 = BAR_PULSE[pb][f][l2];
    p21 = BAR_PULSE[pb][f2][l]; p22 = BAR_PULSE[pb][f2][l2];
    for (i = 0; i < BAR_PLEN + 2u; i++) {       /* hardness x velocity: one shape, once */
        int32_t A = p11[i] + (((p12[i] - p11[i]) * w) >> 15);   /* |d| < 2^16, w <= 2^15: 32 bits are exact */
        int32_t B = p21[i] + (((p22[i] - p21[i]) * w) >> 15);
        v->pbuf[i] = (int16_t)(A + (((B - A) * a) >> 8));
    }
    {
        const uint16_t (*PA)[RH_NANCH] = BAR_PAMP[ty][key];
        const uint32_t (*PR)[RH_NANCH] = BAR_PRATE[ty][key];
        pa = (int32_t)PA[f][vi] + ((((int32_t)PA[f][vi + 1] - (int32_t)PA[f][vi]) * (int32_t)vf) >> 8);
        pa2 = (int32_t)PA[f2][vi] + ((((int32_t)PA[f2][vi + 1] - (int32_t)PA[f2][vi]) * (int32_t)vf) >> 8);
        pr = (int32_t)PR[f][vi] + ((((int32_t)PR[f][vi + 1] - (int32_t)PR[f][vi]) * (int32_t)vf) >> 8);
        pr2 = (int32_t)PR[f2][vi] + ((((int32_t)PR[f2][vi + 1] - (int32_t)PR[f2][vi]) * (int32_t)vf) >> 8);
    }
    pa += ((pa2 - pa) * a) >> 8;
    pr += (int32_t)(((int64_t)(pr2 - pr) * a) >> 8);
    v->prate = (uint32_t)(((uint64_t)((uint32_t)pr << 4) * (uint32_t)bar_mass) >> 12);   /* Q12 -> Q16, x mass */
    if (!v->prate) v->prate = 1u;
    pa = (int32_t)(((int64_t)pa * bar_massi) >> 12);
    v->pamp = pa > 32767 ? 32767 : pa < 0 ? 0 : pa;
    v->ptab = v->pbuf;
    v->ppos = 0;
    v->pred = 0;                                    /* the slots' strike levels: predicted only if the budget asks */
}

/* the strike's level in each slot, before it sounds (for the mode budget): the mode's amplitude after a short force
 * pulse is |u(theta)| / cos(theta / 2), u the pulse's spectrum at the mode (u = F b >> 20 a sample). The table's 66
 * points are played Delta = 2^16 / prate samples apart, so |u| = |b| / 2^20 x pamp / 2^15 x Delta x |P(theta Delta)|,
 * P the table's spectrum (Goertzel, 66 MACs). A mode over half the table's point rate gets nothing. */
static void bar_predict(bar_voice_t *v)
{
    uint32_t m, dq = 0xFFFFFFFFu / v->prate;        /* Delta, Q16 */
    int32_t lp = bar_log2_32((uint32_t)v->pamp) + bar_log2_32(dq) - (16 + 35) * 256;
    v->pred = 1;
    for (m = 0; m < BAR_NM; m++) {
        bar_ms_t *s = &v->m[m];
        const rh_mode_t *km = &v->k->m[m];
        uint64_t tpe;
        uint32_t ph, i, k;
        int32_t c, s1 = 0, s2 = 0, l;
        int64_t q;
        s->lpred = BAR_LZERO;
        if (!s->live || !s->b || m == 1u) continue;
        /* turns per sample (Q32) = theta / 2 pi = th 4 / pi (th = theta / 2, Q30), then per table point */
        tpe = ((((uint64_t)(uint32_t)km->th * 1367130551u) >> 30) * dq) >> 16;
        if (tpe >= 0x80000000u) continue;           /* over half the point rate: nothing to speak of */
        ph = (uint32_t)tpe;
        i = ph >> 22;                               /* 2 pi ph in 512 steps over [0, pi) */
        c = i < 256u ? BAR_MOT_COS[i] : -BAR_MOT_COS[512u - i];   /* cos, Q15 = 2 cos in Q14 */
        for (k = 0; k < BAR_PLEN + 2u; k++) {
            int32_t s0 = v->pbuf[k] + (int32_t)(((int64_t)c * s1) >> 14) - s2;
            s2 = s1; s1 = s0;
        }
        q = (int64_t)s1 * s1 + (int64_t)s2 * s2 - (((int64_t)c * s1) >> 14) * s2;
        if (q <= 0) continue;
        l = (bar_log2_64((uint64_t)q) >> 1) + bar_log2_32((uint32_t)(s->b < 0 ? -s->b : s->b)) + lp
          - (bar_log2_32((uint32_t)km->ch) - 30 * 256);
        s->lpred = (int16_t)(l < -32767 ? -32767 : l > 32767 ? 32767 : l);
    }
#ifdef DINGHY_HOST
    if (bar_h_pred) for (m = 0; m < BAR_NM; m++) v->m[m].fresh |= 2u;   /* report it after the first block */
#endif
}

/* a card's level compensation (BAR_POSG / BAR_TUBEG of one band, Q14): the 17 points at q = raw 4096 / 127 for
 * the family fm and the next, blended by the MALLET fraction fa (Q8), as the pulse */
static int32_t bar_card_gain(const uint16_t (*t)[BAR_NPOS], uint32_t fm, int32_t fa, uint32_t q)
{
    uint32_t i = q >> 8, f2 = fm + 1u < BAR_NFAM ? fm + 1u : fm;
    int32_t fr = (int32_t)(q & 255u), g0, g1;
    if (i >= BAR_NPOS - 1u) { g0 = t[fm][BAR_NPOS - 1u]; g1 = t[f2][BAR_NPOS - 1u]; }
    else {
        g0 = t[fm][i] + ((((int32_t)t[fm][i + 1u] - (int32_t)t[fm][i]) * fr) >> 8);
        g1 = t[f2][i] + ((((int32_t)t[f2][i + 1u] - (int32_t)t[f2][i]) * fr) >> 8);
    }
    return g0 + (((g1 - g0) * fa) >> 8);
}

/* the contact noise's level for this mallet (Q15): BAR_FAM_NZ blended by the MALLET fraction, as the pulse */
static int32_t bar_fam_nz(void)
{
    uint32_t f = (uint32_t)bar_fam >> 8, f2;
    int32_t a = (int32_t)((uint32_t)bar_fam & 255u);
    if (f > BAR_NFAM - 1u) { f = BAR_NFAM - 1u; a = 0; }
    f2 = f + 1u < BAR_NFAM ? f + 1u : f;
    return BAR_FAM_NZ[f] + (((BAR_FAM_NZ[f2] - BAR_FAM_NZ[f]) * a) >> 8);
}

static void bar_init(bar_voice_t *v, uint32_t note, uint32_t vel)
{
    uint32_t tyi = bar_sub < BAR_NTYPE ? bar_sub : 0u;
    const bar_type_t *ty = &BAR_TYPE[tyi];
    uint32_t m, n = note < BAR_KEY_LO ? BAR_KEY_LO : note > BAR_KEY_LO + BAR_NKEY - 1u ? BAR_KEY_LO + BAR_NKEY - 1u : note;
    const bar_key_t *k = &BAR_KEYS[tyi][n - BAR_KEY_LO];
    int32_t tone = bar_pos, s = bar_tube;
    uint32_t q = (uint32_t)tone * (16u * 256u) / 127u, pi = q >> 8, pf = q & 255u;
    const int16_t (*pos)[BAR_NM] = BAR_POS[k->band];
    v->k = k;
    for (m = 0; m < BAR_NM; m++) {
        bar_ms_t *ms = &v->m[m];
        const rh_mode_t *km = &k->m[m];
        int64_t b;
        int32_t w, lf;
        ms->x1 = ms->x2 = ms->err = 0;
        ms->amp = 0;
        ms->live = 0;
        ms->fresh = 1;
        ms->b = 0;
        ms->lpred = BAR_LZERO;
        ms->lo = 1;
        if (!km->th) { ms->e = ms->eps = 0; ms->k1 = ms->k2 = ms->be = 0; ms->lsin = 0; ms->bin = 0; continue; }
        rh_coef(km, km->d, rh_tune_t, m == 1u && !(k->flags & 8u) ? 65536 : bar_k_decay, &ms->e0, &ms->eps0);   /* key
                                                     * flag bit3 (MARIMBA below C4, never VIBES): RING scales the
                                                     * strongly coupled tube too (tools/gen_bars.py, the pins' grid) */
        rh_coef(km, km->drel, rh_tune_t, m == 1u || !(ty->flags & 2u) ? 65536 : bar_k_rel, &ms->e1, &ms->eps1);   /* no
                                                     * damper: drel is the hand mute, never scaled by RELEASE (C5) */
        ms->e = ms->es = ms->e0; ms->eps = ms->epss = ms->eps0;
        w = pi < BAR_NPOS - 1u ? pos[pi][m] + (((pos[pi + 1u][m] - pos[pi][m]) * (int32_t)pf) >> 8) : pos[BAR_NPOS - 1u][m];
        b = ((int64_t)km->b * w) >> 14;
        if (m == 5u || m == 6u) b = tone > 64 ? (b * ty->tors_db) >> 15 : 0;   /* torsion: off the centreline only */
        ms->b = b > 0x7FFFFFFF ? 0x7FFFFFFF : b < -0x7FFFFFFF ? -0x7FFFFFFF : (int32_t)b;
        bar_coefs(ms);
        ms->lsin = (int16_t)bar_lsin(ms);
        lf = bar_log2_32((uint32_t)km->th) - 20 * 256;              /* 1/3-octave bands from about 14 Hz */
        ms->bin = (uint8_t)(lf <= 0 ? 0 : (lf * 3) >> 8 > 35 ? 35 : (lf * 3) >> 8);
        if (m == 0u) ms->live = 1;
        else if (m == 1u) ms->live = s != 0;                       /* BELL 0: no tube at all */
        else ms->live = ms->b != 0;                 /* bar_budget() trims, before the slot ever sounds */
    }
    v->note = (uint8_t)note; v->vel = (uint8_t)vel;
    {   /* the key's gain x the band's level compensation at this TONE and BELL for this mallet (Q14, vibes.json
         * card_level: the strike position holds the level, the tube's own gain is scaled to its allowed span) */
        uint32_t qb = (uint32_t)bar_bell * (16u * 256u) / 127u, fm = (uint32_t)bar_fam >> 8;
        int32_t fa = (int32_t)((uint32_t)bar_fam & 255u), gt, gb;
        int64_t g;
        if (fm > BAR_NFAM - 1u) { fm = BAR_NFAM - 1u; fa = 0; }
        gt = bar_card_gain(BAR_POSG[k->band], fm, fa, q);
        gb = bar_card_gain(BAR_TUBEG[k->band], fm, fa, qb);
        g = ((((int64_t)k->gain * gt) >> 14) * gb) >> 14;
        v->gain = g > 0x7FFFFFFF ? 0x7FFFFFFF : (int32_t)g;
    }
    v->kill1 = (int32_t)((1u << 29) / (uint32_t)(k->gain > 0 ? k->gain : 1));   /* 1 LSB out, in Q25 units */
    if (v->kill1 < 1) v->kill1 = 1;
    v->kbt = (int32_t)(((int64_t)k->k_bt * s) >> 15);
    v->ktb = (int32_t)(((int64_t)k->k_tb * s) >> 15);
    v->wt = (int32_t)(((int64_t)k->wt * s) >> 15);
    v->alive = 1; v->held = 1; v->ramp = 0; v->w_tgt = 0; v->struck = 1;
    v->fade = 32768; v->pend = 0; v->felt_n = 0; v->felt_lp = 0;
    v->age = ++bar_ages;
    v->life = k->life;
    v->pthump = BAR_PULSE[BAR_PBAND[k->band]][0][0];  /* the felt's knock: SOFT, the softest layer */
    v->prate_thump = BAR_PRATE[tyi][n - BAR_KEY_LO][0][1] << 4;
    bar_load_pulse(v, vel);
    /* contact noise: filtered noise, louder for harder mallets and faster strikes, decaying (tau ~ 23 ms) */
    bar_nz_e += (int32_t)(((int64_t)bar_nz * bar_fam_nz() * vel) >> 22);
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
static uint32_t bar_level_sum(const bar_voice_t *v)
{
    uint32_t m, sum = 0;
    for (m = 0; m < BAR_NM; m++) sum += (uint32_t)v->m[m].amp >> 4;
    return sum;
}

static void bar_strike(uint32_t note, uint32_t vel)
{
    uint32_t i, pick = BAR_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < BAR_NV; i++)                    /* the same note still ringing: strike it again, superposed */
        if (bv[i].alive && !bv[i].pend && bv[i].note == note && bv[i].fade == 32768) {
            bar_voice_t *v = &bv[i];
            uint32_t m;
            for (m = BAR_UP0; m < BAR_NM; m++)       /* relight (a fading slot keeps its state); bar_budget() trims */
                if (v->m[m].b) { if (!v->m[m].live) { v->m[m].amp = 0; v->m[m].x1 = v->m[m].x2 = v->m[m].err = 0; }
                                 v->m[m].live = 1; v->m[m].fresh = 1; }
            v->m[0].fresh = 1;
            v->vel = (uint8_t)vel; v->held = 1; v->age = ++bar_ages; v->life = v->k->life; v->struck = 1;
            bar_set_damper(v, 0);
            bar_load_pulse(v, vel);
            bar_nz_e += (int32_t)(((int64_t)bar_nz * bar_fam_nz() * vel) >> 22);
            return;
        }
    for (i = 0; i < BAR_NV; i++)
        if (!bv[i].alive) { bar_init(&bv[i], note, vel); return; }
    for (i = 0; i < BAR_NV; i++) {                  /* steal: the quietest released voice, else the oldest */
        uint32_t sum, score;
        if (bv[i].pend) continue;
        sum = bar_level_sum(&bv[i]);
        score = bv[i].held ? 0x80000000u + (bv[i].age & 0x7FFFFFFFu) : (sum > 0x7FFFFFFFu ? 0x7FFFFFFFu : sum);
        if (score < best) { best = score; pick = i; }
    }
    if (pick == BAR_NV) pick = 0;
    bv[pick].pend = 1; bv[pick].pend_note = (uint8_t)note; bv[pick].pend_vel = (uint8_t)vel;
    if (bv[pick].fade == 32768) bv[pick].fade = 32767;
}

/* the damper weight a key-up gives at this pedal weight w: a type with a damper (VIBES) w itself; a type without one
 * (MARIMBA) w x MUTE (Q15: 0 = rings out, 127 = the full hand mute in drel; the pedal still holds: w 0) (C6, C7) */
static int bar_has_damper(void) { return (BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u].flags & 2u) != 0u; }
static int32_t bar_mute_w(int32_t w)
{
    return bar_has_damper() ? w : (int32_t)(((int64_t)w * bar_mute) >> 15);
}

static void bar_note_off(uint32_t note)
{
    uint32_t i;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive && bv[i].note == note && bv[i].held) {
            bar_voice_t *v = &bv[i];
            v->held = 0;
            bar_set_damper(v, bar_mute_w(bar_pedal_w));
            if (!bar_has_damper()) continue;                 /* no felt: no knock, no felt noise (C6) */
            if (bar_pedal_w && bar_thump && !v->ptab) {      /* the felt lands on the bar: a soft knock */
                v->ptab = v->pthump;
                v->pamp = bar_thump > 32767 ? 32767 : bar_thump;
                v->prate = v->prate_thump;
                v->ppos = 0;
            }
            if (bar_pedal_w && bar_felt) v->felt_n = 353;    /* 8 ms of felt noise */
        }
    for (i = 0; i < BAR_NV; i++)                    /* a note let go while it waits for a steal's fade: it starts let go
                                                     * (it was held for good: fuzz, a fast arpeggio over 8 voices) */
        if (bv[i].alive && bv[i].pend == 1u && bv[i].pend_note == note) bv[i].pend = 2;
}

static void bar_pedal(uint32_t cc)
{
    uint32_t i;
    bar_pedal_w = PEDAL_W[cc > 127u ? 127u : cc];
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive && !bv[i].held) bar_set_damper(&bv[i], bar_mute_w(bar_pedal_w));   /* (C7) */
}

static void bar_all_off(void)
{
    uint32_t i;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive) {
            bv[i].held = 0; bar_set_damper(&bv[i], 32768);
            if (bv[i].pend) bv[i].pend = 3;         /* a note waiting for a steal's fade starts let go and damped, as
                                                     * the struck ones (it was held for good: fuzz seed 780, a note-on
                                                     * on 9 busy voices, then CC 123 within the 1.5 ms fade) */
        }
}

static void bar_shed(void)                          /* overload: fade the quietest voice, released first */
{
    uint32_t i, pick = BAR_NV, best = 0xFFFFFFFFu;
    for (i = 0; i < BAR_NV; i++) {
        uint32_t sum, score;
        if (!bv[i].alive || bv[i].fade != 32768) continue;
        sum = bar_level_sum(&bv[i]);
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
    bar_nz_e = 0;
    bar_nz_s[0] = bar_nz_s[1] = bar_nz_s[2] = bar_nz_s[3] = 0;
}

static int bar_quiet(void) { return bar_active_bits == 0 && bar_quiet_blocks >= 2u; }

/* ---- the motor: the discs' opening per sub-block edge, once per block (the MOTOR page through eng_bar_knob). The
 * discs gate the tube's radiation only (bar_voice_block), x the make-up that keeps its mean power; the bar <-> tube
 * coupling stays the discs-open one. Until 2026-10-18 they scaled the coupling too: with the motor on the tube drained
 * the bar less, the bar rang about twice as long (C3 G3 C4 E4: -9.5 dB/s open, -5.0 with the motor; real discs
 * closed do sustain longer), so the motor drifted +4.4 dB/s above the dry level along a held chord and switching it
 * off jumped +4.7 LU (FM-1 vibes_jump, USB and the jack alike). The user's rule wins: the motor is a tremolo at the
 * same level and decay as the discs open (tests/py/test_vibes_motor_level.py). */
#define BAR_MOT_SLEW 512                            /* the depth's slew, Q15 a sub-block: off <-> full in 64 sub-blocks
                                                     * (46 ms), so switching the motor or turning DEPTH never clicks */
static int32_t bar_mot_d;                           /* the discs' depth as it plays, Q15 (DEPTH x ON, slewed) */
static uint8_t bar_mot_park = 1;                    /* this block: the discs parked open (bar_mot_g all x 1) */
static uint32_t bar_isqrt(uint32_t x)               /* floor(sqrt(x)), 32-bit */
{
    uint32_t r = 0, b = 1u << 30;
    while (b > x) b >>= 2;
    while (b) {
        if (x >= r + b) { x -= r + b; r = (r >> 1) + b; } else r >>= 1;
        b >>= 2;
    }
    return r;
}
/* the make-up of the tube's radiation for a disc law g = gmin + (1 - gmin) op (Q15), op = (1 - a) o + a o^2 (SHAPE a),
 * o = 1 - |cos|: 1 / sqrt(mean g^2) over a turn, so the tube's mean power stays what it is with the discs open (the
 * loudness matched at any DEPTH and SHAPE, and along the whole decay). The moments of o over a turn, Q15: E[o] = 1 -
 * 2/pi, E[o^2], E[o^3], E[o^4]. Q15 out (x 1 .. x 2.4) */
#define BAR_MOT_E1 11907
#define BAR_MOT_E2 7430
#define BAR_MOT_E3 5431
#define BAR_MOT_E4 4288
static int32_t bar_mot_mk(int32_t gmin, int32_t shape)
{
    int32_t a = (shape << 15) / 127, b = 32768 - a, h = 32768 - gmin;
    int32_t e1 = (b * BAR_MOT_E1 + a * BAR_MOT_E2) >> 15;                       /* E[op] */
    int32_t e2 = (int32_t)(((int64_t)b * b * BAR_MOT_E2 + 2 * (int64_t)a * b * BAR_MOT_E3
                            + (int64_t)a * a * BAR_MOT_E4) >> 30);              /* E[op^2] */
    int32_t m2 = ((gmin * gmin) >> 15) + (int32_t)((2 * (int64_t)gmin * h * e1) >> 30)
               + (int32_t)(((int64_t)h * h * e2) >> 30);                        /* E[g^2], Q15 */
    uint32_t r = bar_isqrt((uint32_t)m2 << 15);                                 /* sqrt, Q15 */
    return r ? (int32_t)((1u << 30) / r) : 32768;
}
static void bar_motor_block(void)
{
    uint32_t i, inc = bar_mot_inc;
    int32_t depth = bar_mot_depth > 127u ? 127 : bar_mot_depth, shape = bar_mot_shape > 127u ? 127 : bar_mot_shape;
    const bar_type_t *ty = &BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u];
    int32_t gc = ty->mot_gc;
    int32_t tgt = bar_mot_on && (ty->flags & 1u) ? (depth << 15) / 127 : 0, d0 = bar_mot_d;   /* no motor: parked (C8) */
    bar_mot_g[0] = bar_mot_g[8];                    /* the last block's end: continuous */
    bar_mot_park = !tgt && !d0;
    if (bar_mot_park) {                             /* off: the discs parked open */
        for (i = 0; i < 9u; i++) bar_mot_g[i] = 32768;
        bar_mot_ph += 256u * inc;
        return;
    }
    for (i = 1; i < 9u; i++) {                      /* each sub-block's end (its start is the one before) */
        /* |cos phi|, phi = pi t / 512, t = ph >> 23 folded on the quarter-turn table: one AM period per 2^32 phase
         * cycle (bit-exact to tools/bartube.py mot_rad at a steady depth: mot_block x mot_mk) */
        uint32_t ph = bar_mot_ph + i * 32u * inc, t = ph >> 23, fr = (ph >> 15) & 255u, t1 = (t + 1u) & 511u;
        int32_t c0 = t < 256u ? BAR_MOT_COS[t] : BAR_MOT_COS[512u - t];
        int32_t c1 = t1 < 256u ? BAR_MOT_COS[t1] : BAR_MOT_COS[512u - t1];
        int32_t c = c0 + (((c1 - c0) * (int32_t)fr) >> 8);
        int32_t o = 32768 - c, op = o, gmin, dd = tgt - bar_mot_d;
        bar_mot_d += dd > BAR_MOT_SLEW ? BAR_MOT_SLEW : dd < -BAR_MOT_SLEW ? -BAR_MOT_SLEW : dd;   /* DEPTH x ON, slewed */
        gmin = bar_mot_d == tgt ? 32768 - (depth * (32768 - gc)) / 127 * (tgt != 0)   /* steady: DEPTH exactly */
                                : 32768 - (int32_t)(((int64_t)bar_mot_d * (32768 - gc)) >> 15);
        if (shape) op = o + (((((o * o) >> 15) - o) * shape) / 127);   /* p = 1 + SHAPE/127: a narrower open window */
        bar_mot_g[i] = (int32_t)(((int64_t)(gmin + (((32768 - gmin) * op) >> 15)) * bar_mot_mk(gmin, shape)) >> 15);
    }
    bar_mot_ph += 256u * inc;
}

/* ---- the CPU cost model: ns on the FM-1 per 256-frame block. Derived from the measured costs of the previous kernels
 * (tools/hil_cpu.py 2026-10-17: an upper slot 40 us a block for 44 instructions a sample = 3.6 ns an instruction in
 * register-bound loops; the pair + pulse + output 1.46 us a voice-sample for ~248 = 5.9 ns where the loop spills)
 * applied to the instruction counts of these loops in build/frodhes.dis, then x 1.25 (x 1.3 for the slots) so each
 * is an upper bound. tools/hil_cpu.py --calib and --fx-each measure the real ones: replace these with them. The cap
 * is the gate minus a margin minus everything that is not an upper mode, in units of one upper mode for one
 * sub-block (a fading slot is one unit). Loop sizes (instructions a sample): upper slot 8 (Q32) / 9 (Q30), with the
 * force 13 / 20; the pair 34 (the motor no longer ramps it: 38.5 before 2026-10-18), with the force 38 / 44.5; the
 * tube's radiation 6; the gain 9; the slow output (steal fade, felt) 46; the pulse 25; the contact noise 48. */
#define BAR_NS_GATE 4800000u      /* the worst block allowed; the shedder is at 5000 us */
#define BAR_NS_MARGIN 150000u     /* the model's own error */
#define BAR_NS_BASE 312000u       /* idle, no effects: the platform, the master, an empty render (measured) */
#define BAR_NS_VOICE 100000u      /* a sounding voice: the pair, the radiation (motor), the gain, the levels, setup */
#define BAR_NS_FORCE 50000u       /* a voice whose pulse runs this block (the pulse, the pair's force), whole block */
#define BAR_NS_STRIKE 40000u      /* a note-on: 16 coefficient sets, the pulse blend, the slots' set-up */
#define BAR_NS_SLOW 45000u        /* a voice fading (steal) or with felt noise: the slow output loop */
#define BAR_NS_NOISE 65000u       /* the contact noise (one for all voices) */
#define BAR_NS_RANK 70000u        /* the budget's ranking (only when over) */
#define BAR_NS_PRED 3000u         /* one slot's strike prediction (only when over) */
#define BAR_NS_UNIT 1400u         /* one upper mode for one sub-block */
#define BAR_NS_UNITF 1500u        /* ... more while any voice's pulse runs (the force MAC; counted for every slot) */

static uint32_t bar_upper_units(void)               /* live upper slots over the alive voices, in sub-block units */
{
    uint32_t i, m, c = 0;
    for (i = 0; i < BAR_NV; i++)
        if (bv[i].alive)
            for (m = BAR_UP0; m < BAR_NM; m++) c += bv[i].m[m].live == 1u ? BAR_NSUB : bv[i].m[m].live ? 1u : 0u;
    return c;
}

/* everything this block will do except the upper modes (ns); *unit = one unit's ns */
static uint32_t bar_cost_fixed(uint32_t *unit)
{
    uint32_t i, ns = BAR_NS_BASE + 1000u * fx_cost_us(), f = 0;
#ifdef DINGHY_HOST
    if (bar_h_fxus >= 0) ns = BAR_NS_BASE + 1000u * (uint32_t)bar_h_fxus;
#endif
    for (i = 0; i < BAR_NV; i++) {
        const bar_voice_t *v = &bv[i];
        if (!v->alive) continue;
        ns += BAR_NS_VOICE;
        if (v->ptab) { ns += BAR_NS_FORCE; f = 1; }
        if (v->struck) ns += BAR_NS_STRIKE;
        if (v->fade != 32768 || v->felt_n) ns += BAR_NS_SLOW;
        if (v->pend && v->fade != 32768) ns += BAR_NS_STRIKE;       /* the waiting note starts at this block's end */
    }
    if (bar_nz_e) ns += BAR_NS_NOISE;
    *unit = BAR_NS_UNIT + (f ? BAR_NS_UNITF : 0u);
    return ns;
}

/* ---- the mode budget, at block start (after this block's strikes): while the live upper modes cost more than the
 * block can take, the least audible go. Audibility: a partial's output level re the loudest fundamental (V1) in the
 * mix, and under the masking of the loudest other partials in its own and the three neighbouring third-octave bands
 * (12 dB under a masker in the same band, 8 dB more per band above it, 15 dB more per band below it). A strike's
 * slots that have not sounded are ranked by their predicted level (bar_predict). Ringing slots fade out over a
 * sub-block (live 2); slots that never sounded just stay silent. */
#define BAR_DB(x) ((int32_t)(x) * 5442 / 128)       /* dB -> log2 Q8 (256 / 6.0206 = 42.52) */
#define BAR_NBIN 36u
#define BAR_FLOOR_DB 70                             /* the audibility floor under the loudest V1 */
#define BAR_MOFF_DB 12                              /* masking: 12 dB under a partial in the same band */
#define BAR_MUP_DB 8                                /* + 8 dB per band above the masker */
#define BAR_MDN_DB 15                               /* + 15 dB per band below it */
#define BAR_MCAP_DB 6                               /* the most masking can take off (it breaks near ties: S12
                                                     * measured an uncapped credit dropping louder partials first) */
typedef struct { int16_t l; uint8_t v, m; } bar_lv_t;
static bar_lv_t bar_lv[BAR_NV * BAR_NM];
static int16_t bar_top[BAR_NBIN][2];
static uint8_t bar_topi[BAR_NBIN][2];

static uint32_t bar_budget_run(void);
static void bar_budget(void)
{
    uint32_t unit, ns = bar_cost_fixed(&unit) + bar_budget_run();
    bar_plan_us = (ns + unit * bar_upper_units() + 999u) / 1000u;
}
static uint32_t bar_budget_run(void)                /* returns the ranking's own cost (ns) */
{
    uint32_t i, m, n = 0, nc = 0, used = bar_upper_units(), unit, cap, rank = 0;
    uint32_t fixed = bar_cost_fixed(&unit);
    int32_t lref = BAR_LZERO;
    uint8_t cand[BAR_NV * BAR_NUP];
    int16_t aud[BAR_NV * BAR_NUP];
    for (;;) {                                      /* the cap, in units: (gate - margin - the rest) / unit */
        uint32_t lim = BAR_NS_GATE - BAR_NS_MARGIN, cost = fixed + rank;
        cap = cost < lim ? (lim - cost) / unit : 0u;
        if (used <= cap || rank) break;
        rank = BAR_NS_RANK;                         /* over: the ranking itself costs, take it from the cap */
        for (i = 0; i < BAR_NV; i++)
            if (bv[i].alive && !bv[i].pred && bv[i].ptab == bv[i].pbuf)
                for (m = 0; m < BAR_NM; m++) rank += bv[i].m[m].fresh & 1u ? BAR_NS_PRED : 0u;
    }
#ifdef DINGHY_HOST
    if (bar_h_pred)
        for (i = 0; i < BAR_NV; i++) if (bv[i].alive && bv[i].ptab == bv[i].pbuf && !bv[i].pred) bar_predict(&bv[i]);
#endif
    {   /* the type's own cap (BAR_TYPE.budget, upper modes; 48 = all) can only lower it */
        uint32_t tcap = (uint32_t)BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u].budget * BAR_NSUB;
        if (tcap < cap) cap = tcap;
    }
    if (bar_budget_force) cap = bar_budget_force >= 50u ? 0xFFFFu : (uint32_t)(bar_budget_force - 1u) * BAR_NSUB;
#ifdef DINGHY_HOST
    if (bar_h_nobudget) cap = 0xFFFFu;
#endif
    if (cap / BAR_NSUB < bar_prof_cap) bar_prof_cap = cap / BAR_NSUB;
    if (used <= cap) { if ((used + 7u) / 8u > bar_prof_used) bar_prof_used = (used + 7u) / 8u; return rank; }
    /* every partial's output level, log2 Q8 in Q15 output units (gain Q16: amp gain / 2^29) */
    for (i = 0; i < BAR_NV; i++) {
        bar_voice_t *v = &bv[i];
        int32_t lg;
        if (!v->alive) continue;
        if (v->ptab == v->pbuf && !v->pred) bar_predict(v);
        lg = bar_log2_32((uint32_t)v->gain) - 29 * 256;
        for (m = 0; m < BAR_NM; m++) {
            const bar_ms_t *s = &v->m[m];
            int32_t l;
            if (s->live != 1u) continue;
            l = s->amp ? bar_log2_32((uint32_t)s->amp) : BAR_LZERO;
            if ((s->fresh & 1u) && v->ptab == v->pbuf && s->lpred > l) l = s->lpred;   /* struck now: predicted */
            if (l == BAR_LZERO) { if (m < BAR_UP0) continue; l = -32767 - lg; }   /* nothing to go on: goes first */
            l += lg;
            if (m == 1u) l += bar_log2_32((uint32_t)(v->wt < 0 ? -v->wt : v->wt)) - 28 * 256;
            if (m == 0u && l > lref) lref = l;
            bar_lv[n].l = (int16_t)(l < -32767 ? -32767 : l > 32767 ? 32767 : l);
            bar_lv[n].v = (uint8_t)i; bar_lv[n].m = (uint8_t)m;
            n++;
        }
    }
    for (i = 0; i < BAR_NBIN; i++) { bar_top[i][0] = bar_top[i][1] = -32767; bar_topi[i][0] = bar_topi[i][1] = 0xFF; }
    for (i = 0; i < n; i++) {                       /* the two loudest partials in every band */
        uint32_t b = bv[bar_lv[i].v].m[bar_lv[i].m].bin;
        int16_t l = bar_lv[i].l;
        if (l > bar_top[b][0]) { bar_top[b][1] = bar_top[b][0]; bar_topi[b][1] = bar_topi[b][0]; bar_top[b][0] = l; bar_topi[b][0] = (uint8_t)i; }
        else if (l > bar_top[b][1]) { bar_top[b][1] = l; bar_topi[b][1] = (uint8_t)i; }
    }
    for (i = 0; i < n; i++) {                       /* the candidates' audibility: level - max(floor, masking) */
        const bar_voice_t *v = &bv[bar_lv[i].v];
        int32_t b = v->m[bar_lv[i].m].bin, t = lref - BAR_DB(BAR_FLOOR_DB), d, a;
        uint32_t j;
        if (bar_lv[i].m < BAR_UP0) continue;
        for (d = -3; d <= 3; d++) {
            int32_t bb = b + d, mk;
            if (bb < 0 || bb >= (int32_t)BAR_NBIN) continue;
            j = bar_topi[bb][0] == i ? 1u : 0u;     /* not masked by itself */
            if (bar_topi[bb][j] == 0xFF) continue;
            mk = bar_top[bb][j] - BAR_DB(BAR_MOFF_DB) - (d < 0 ? BAR_DB(BAR_MUP_DB) * -d : BAR_DB(BAR_MDN_DB) * d);
            if (mk > t) t = mk;
        }
        {   /* masking only breaks near ties: its credit is capped */
            int32_t fl = lref - BAR_DB(BAR_FLOOR_DB), cr = t - fl;
            if (cr > BAR_DB(BAR_MCAP_DB)) cr = BAR_DB(BAR_MCAP_DB);
            a = bar_lv[i].l - fl - cr;
        }
        a -= (bar_log2_32((uint32_t)v->m[bar_lv[i].m].eps) - 16 * 256) >> 1;   /* short-lived partials count less:
                                                     * energy ~ A^2 tau, tau ~ 1 / eps (half a log2 per octave of eps) */
        if (v->fade != 32768) a -= BAR_DB(200);     /* a stolen voice's partials go first: it fades out anyway */
        aud[nc] = (int16_t)(a < -32767 ? -32767 : a > 32767 ? 32767 : a);
        cand[nc++] = (uint8_t)i;
    }
    for (i = 1; i < nc; i++) {                      /* least audible first (insertion sort, <= 48) */
        int16_t a = aud[i];
        uint8_t c = cand[i];
        uint32_t j = i;
        while (j && aud[j - 1u] > a) { aud[j] = aud[j - 1u]; cand[j] = cand[j - 1u]; j--; }
        aud[j] = a; cand[j] = c;
    }
    for (i = 0; i < nc && used > cap; i++) {
        bar_ms_t *s = &bv[bar_lv[cand[i]].v].m[bar_lv[cand[i]].m];
        if (s->amp) { s->live = 2; used -= BAR_NSUB - 1u; }                       /* faded over its next sub-block */
        else { s->live = 0; s->x1 = s->x2 = s->err = 0; used -= BAR_NSUB; }      /* never sounded: nothing to fade */
    }
    if (used > cap) {                               /* still over (should not happen): every unranked upper slot goes */
        for (i = 0; i < BAR_NV; i++)
            for (m = BAR_UP0; m < BAR_NM && used > cap; m++)
                if (bv[i].alive && bv[i].m[m].live == 1u) { bv[i].m[m].live = 2; used -= BAR_NSUB - 1u; }
    }
    if ((used + 7u) / 8u > bar_prof_used) bar_prof_used = (used + 7u) / 8u;
    return rank;
}

/* ---- the kernels: two samples a turn (x[n] and x[n-1] swap roles), first-order error feedback. 2 multiplies a
 * sample (3 while the force runs): Q32 for the modes under ~4.9 kHz, Q30 above (bar_coefs). */
#define BAR_LO(XN, XO, F) do { int64_t a_ = (int64_t)(uint32_t)err - (int64_t)k1 * (XN) + (int64_t)k2 * (XO) F; \
        (XO) = 2 * (XN) - (XO) + (int32_t)(a_ >> 32); err = (int32_t)a_; } while (0)
#define BAR_HI(XN, XO, F) do { int64_t a_ = (int64_t)(uint32_t)err + (int64_t)k1 * (XN) - (int64_t)k2 * (XO) F; \
        (XO) = (int32_t)(a_ >> 30); err = (int32_t)a_ & BAR_EFM; } while (0)
#define BAR_KERNEL(NAME, STEP, FRC) \
static void NAME(bar_ms_t *s, int32_t *bx, const int32_t *F, uint32_t n) \
{ \
    int32_t x1 = s->x1, x2 = s->x2, err = s->err, k1 = s->k1, k2 = s->k2, be = s->be; \
    int32_t *e = bx + n; \
    (void)F; (void)be; \
    while (bx < e) { \
        STEP(x1, x2, FRC); *bx++ += x2; \
        STEP(x2, x1, FRC); *bx++ += x1; \
    } \
    s->x1 = x1; s->x2 = x2; s->err = err; \
}
#define BAR_NOF
#define BAR_F_ + (int64_t)*F++ * be
BAR_KERNEL(bar_run_lo, BAR_LO, BAR_NOF)
BAR_KERNEL(bar_run_lo_f, BAR_LO, BAR_F_)
BAR_KERNEL(bar_run_hi, BAR_HI, BAR_NOF)
BAR_KERNEL(bar_run_hi_f, BAR_HI, BAR_F_)

static void bar_run(bar_ms_t *s, int32_t *bx, const int32_t *F, uint32_t n)   /* an upper slot, n even; F 0: none */
{
    if (s->lo) { if (F) bar_run_lo_f(s, bx, F, n); else bar_run_lo(s, bx, 0, n); }
    else { if (F) bar_run_hi_f(s, bx, F, n); else bar_run_hi(s, bx, 0, n); }
}

static void bar_run_fade(bar_ms_t *s, int32_t *bx, const int32_t *F)  /* one sub-block, faded out, then asleep */
{
    int32_t t[BAR_SUB], j;
    for (j = 0; j < (int32_t)BAR_SUB; j++) t[j] = 0;
    bar_run(s, t, F, BAR_SUB);
    for (j = 0; j < (int32_t)BAR_SUB; j++) bx[j] += (int32_t)(((int64_t)t[j] * (1024 * ((int32_t)BAR_SUB - j))) >> 15);
    s->live = 0; s->x1 = s->x2 = s->err = 0; s->amp = 0;
}

/* V1 <-> TUBE for one sub-block, sample-inner, Q32 form (both modes under ~4.9 kHz: every key but MARIMBA's 108, whose
 * tube, damped 15 % at 4.3 kHz, takes the Q30 form: bar_pair_*h below): bx = x0, tb = the
 * tube's x1 (its radiation weight is the output loop's). The tube's pressure on the bar (ktb x1 into y0) enters x0 as
 * e0 ktb x1, likewise e1 kbt x0 into x1: ktbe = ktb e0 >> 16 then >> 12 (the product keeps 12 more bits: ktb e0 is
 * 2^33 .. 2^46 over the keys), kbte = kbt e1 >> 28. The motor does not touch the coupling (bar_motor_block): the pair,
 * and so the bar's decay, is the same with the motor on or off. */
#define BAR_PSTEP(X0N, X0O, X1N, X1O, FRC) do { \
        int64_t a_ = (int64_t)(uint32_t)e0r - (int64_t)c10 * (X0N) + (int64_t)c20 * (X0O) \
                   + (((int64_t)ktbe * (X1N)) >> 12); \
        if (FRC) a_ += (int64_t)*F++ * be0; \
        (X0O) = 2 * (X0N) - (X0O) + (int32_t)(a_ >> 32); e0r = (int32_t)a_; \
        a_ = (int64_t)(uint32_t)e1r - (int64_t)c11 * (X1N) + (int64_t)c21 * (X1O) + (int64_t)kbte * (X0O); \
        (X1O) = 2 * (X1N) - (X1O) + (int32_t)(a_ >> 32); e1r = (int32_t)a_; \
        *bx++ = (X0O); *tb++ = (X1O); \
    } while (0)
/* the same with the tube in the Q30 form (its c1 >= 1/2: a heavily damped tube near the top, MARIMBA's key 108 only;
 * VIBES never: every VIBES tube is in Q32): x1' = (err + a1 x1 - a2 x1p + kbte x0) >> 30, kbte = kbt e1 >> 30 */
#define BAR_PSTEP_H(X0N, X0O, X1N, X1O, FRC) do { \
        int64_t a_ = (int64_t)(uint32_t)e0r - (int64_t)c10 * (X0N) + (int64_t)c20 * (X0O) \
                   + (((int64_t)ktbe * (X1N)) >> 12); \
        if (FRC) a_ += (int64_t)*F++ * be0; \
        (X0O) = 2 * (X0N) - (X0O) + (int32_t)(a_ >> 32); e0r = (int32_t)a_; \
        a_ = (int64_t)(uint32_t)e1r + (int64_t)c11 * (X1N) - (int64_t)c21 * (X1O) + (int64_t)kbte * (X0O); \
        (X1O) = (int32_t)(a_ >> 30); e1r = (int32_t)a_ & BAR_EFM; \
        *bx++ = (X0O); *tb++ = (X1O); \
    } while (0)
#define BAR_PAIR(NAME, STEP, FRC) \
static void NAME(bar_ms_t *s0, bar_ms_t *s1, int32_t *bx, int32_t *tb, const int32_t *F, int32_t ktbe, int32_t kbte) \
{ \
    int32_t x0 = s0->x1, x0p = s0->x2, e0r = s0->err, c10 = s0->k1, c20 = s0->k2, be0 = s0->be; \
    int32_t x1 = s1->x1, x1p = s1->x2, e1r = s1->err, c11 = s1->k1, c21 = s1->k2; \
    int32_t *e = bx + BAR_SUB; \
    (void)F; (void)be0; \
    while (bx < e) { STEP(x0, x0p, x1, x1p, FRC); STEP(x0p, x0, x1p, x1, FRC); } \
    s0->x1 = x0; s0->x2 = x0p; s0->err = e0r; s1->x1 = x1; s1->x2 = x1p; s1->err = e1r; \
}
BAR_PAIR(bar_pair_0, BAR_PSTEP, 0)
BAR_PAIR(bar_pair_1, BAR_PSTEP, 1)
BAR_PAIR(bar_pair_0h, BAR_PSTEP_H, 0)
BAR_PAIR(bar_pair_1h, BAR_PSTEP_H, 1)

static bar_ms_t bar_notube;                         /* BELL 0: a silent tube (the same work, so the same cost) */
static void bar_pair(bar_voice_t *v, int32_t *bx, int32_t *tb, const int32_t *F)
{
    bar_ms_t *s0 = &v->m[0], *s1 = v->m[1].live ? &v->m[1] : &bar_notube;
    int32_t ktbe = 0, kbte = 0;
    if (v->m[1].live) {
        ktbe = bar_sat31((int64_t)v->ktb * s0->e >> 16);
        if (!s1->lo) {                              /* the tube in Q30 (MARIMBA key 108; never VIBES): its own path */
            kbte = bar_sat31((int64_t)v->kbt * s1->e >> 30);
            if (F) bar_pair_1h(s0, s1, bx, tb, F, ktbe, kbte); else bar_pair_0h(s0, s1, bx, tb, 0, ktbe, kbte);
            return;
        }
        kbte = bar_sat31((int64_t)v->kbt * s1->e >> 28);
    }
    if (F) bar_pair_1(s0, s1, bx, tb, F, ktbe, kbte); else bar_pair_0(s0, s1, bx, tb, 0, ktbe, kbte);
}

/* the pulse into bar_F[0 .. nF) as F << 12, F exactly the Q15 force of before (the kernels' be scales it) */
static uint32_t bar_force(bar_voice_t *v)
{
    const int16_t *t = v->ptab;
    uint32_t pos = v->ppos, rate = v->prate, j = 0;
    int32_t amp = v->pamp;
    if (!t) return 0;
    while (j < BAR_N) {
        uint32_t i = pos >> 16;
        int32_t f = (int32_t)((pos >> 1) & 0x7FFFu), a = t[i], d = t[i + 1u] - a;
        bar_F[j++] = (((a + ((d * f) >> 15)) * amp) >> 15) << 12;
        pos += rate;
        if ((pos >> 16) >= BAR_PLEN) { t = 0; break; }
    }
    v->ppos = pos; v->ptab = t;
    {   /* zeros to the end of the sub-block (the kernels run in pairs, the pair per sub-block) */
        uint32_t z, e = (j + BAR_SUB) & ~(BAR_SUB - 1u);
        for (z = j; z < e && z < BAR_N + 2u; z++) bar_F[z] = 0;
    }
    return j;
}

/* damper crossfade: E and eps together, 7 sub-blocks */
static void bar_damper_step(bar_voice_t *v)
{
    uint32_t m;
    int32_t f;
    v->ramp--;
    f = (int32_t)(BAR_RAMP_SUB - v->ramp) * 4681;
    if (f > 32768 || !v->ramp) f = 32768;
    for (m = 0; m < BAR_NM; m++) {
        bar_ms_t *s = &v->m[m];
        int32_t et, pt;
        if (!s->live) continue;
        et = s->e0 + (int32_t)(((int64_t)(s->e1 - s->e0) * v->w_tgt) >> 15);
        pt = s->eps0 + (int32_t)(((int64_t)(s->eps1 - s->eps0) * v->w_tgt) >> 15);
        s->e = s->es + (int32_t)(((int64_t)(et - s->es) * f) >> 15);
        s->eps = s->epss + (int32_t)(((int64_t)(pt - s->epss) * f) >> 15);
        bar_coefs(s);
    }
}

/* one voice, one block, added into mix[0 .. 256) */
static void bar_voice_block(bar_voice_t *v, int32_t *mix)
{
    const bar_type_t *ty = &BAR_TYPE[bar_sub < BAR_NTYPE ? bar_sub : 0u];
    uint32_t j, m, sb, nf = bar_force(v), nf2 = (nf + 1u) & ~1u, nlive = 0, fade0 = 0;
    int32_t gain = v->gain, ref, sum, la[BAR_NM];
    if (v->ramp) {                                  /* the damper moves: every slot's coefficients per sub-block */
        for (sb = 0; sb < BAR_NSUB; sb++) {
            uint32_t o = sb * BAR_SUB;
            const int32_t *F = o < nf ? bar_F + o : 0;
            if (v->ramp) bar_damper_step(v);
            bar_pair(v, bar_x + o, bar_tb + o, F);
            for (m = BAR_UP0; m < BAR_NM; m++) {
                bar_ms_t *s = &v->m[m];
                if (!s->live) continue;
                if (s->live == 2u) { bar_run_fade(s, bar_x + o, F); fade0++; continue; }
                bar_run(s, bar_x + o, F, BAR_SUB);
            }
        }
    } else {
        for (sb = 0; sb < BAR_NSUB; sb++)
            bar_pair(v, bar_x + sb * BAR_SUB, bar_tb + sb * BAR_SUB, sb * BAR_SUB < nf ? bar_F + sb * BAR_SUB : 0);
        for (m = BAR_UP0; m < BAR_NM; m++) {        /* the upper modes: the whole block at once */
            bar_ms_t *s = &v->m[m];
            uint32_t o = 0;
            if (!s->live) continue;
            if (s->live == 2u) { bar_run_fade(s, bar_x, nf ? bar_F : 0); fade0++; continue; }
            if (nf2) { bar_run(s, bar_x, bar_F, nf2); o = nf2; }
            if (o < BAR_N) bar_run(s, bar_x + o, 0, BAR_N - o);
        }
    }
    if (v->m[1].live && bar_mot_park) {             /* + the tube's radiation (the discs parked open) */
        int32_t wt = v->wt;
        for (j = 0; j < BAR_N; j++) bar_x[j] += (int32_t)(((int64_t)wt * bar_tb[j]) >> 28);
    } else if (v->m[1].live)
        for (sb = 0; sb < BAR_NSUB; sb++) {         /* ... its weight ramped by the motor (x its make-up: up to x 2.4,
                                                     * so Q27: |wt| <= 5.8 x 2.4 / 2 < 2^31 / 2^28) */
            int32_t wt = bar_sat31(((int64_t)v->wt * bar_mot_g[sb]) >> 16);
            int32_t dw = (bar_sat31(((int64_t)v->wt * bar_mot_g[sb + 1u]) >> 16) - wt) >> 5;
            int32_t *x = bar_x + sb * BAR_SUB, *t = bar_tb + sb * BAR_SUB;
            for (j = 0; j < BAR_SUB; j++) { x[j] += (int32_t)(((int64_t)wt * t[j]) >> 27); wt += dw; }
        }
    if (v->fade == 32768 && !v->felt_n)             /* the gain into the mix */
        for (j = 0; j < BAR_N; j++) mix[j] += (int32_t)(((int64_t)bar_x[j] * gain) >> 24);
    else
        for (j = 0; j < BAR_N; j++) {               /* gain, steal fade, felt noise */
            int32_t o = (int32_t)(((int64_t)bar_x[j] * gain) >> 24);
            if (v->fade != 32768) {
                o = (int32_t)(((int64_t)o * v->fade) >> 15);
                v->fade -= BAR_FADE_STEP;
                if (v->fade < 0) v->fade = 0;
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
    /* the levels, once a block: each live slot's amplitude, the voice's output, then the end and the sleep */
    sum = 0;
    for (m = 0; m < BAR_NM; m++) {
        bar_ms_t *s = &v->m[m];
        la[m] = BAR_LZERO;
        if (!s->live) continue;
        s->x1 = bar_sat(s->x1); s->x2 = bar_sat(s->x2);
        la[m] = bar_lamp(s);
        s->amp = bar_exp2(la[m]);
#ifdef DINGHY_HOST
        if (getenv("BAR_DBG")) fprintf(stderr, "m%u la %d amp %d x1 %d x2 %d lsin %d live %u\n", (unsigned)m, (int)la[m], (int)s->amp, (int)s->x1, (int)s->x2, (int)s->lsin, (unsigned)s->live);
#endif
#ifdef DINGHY_HOST
        if ((s->fresh & 2u) && bar_h_pred && m != 1u)
            fprintf(bar_h_pred, "%u %u %d %d\n", (unsigned)v->note, (unsigned)m, (int)s->lpred, (int)la[m]);
        s->fresh &= 1u;
#endif
        if (!v->ptab) s->fresh = 0;                 /* measured from here on */
        sum += m == 1u ? (int32_t)(((int64_t)s->amp * iabs(v->wt)) >> 28) : s->amp;
        nlive += m == 1u ? 0u : m == 0u ? 3u : 1u;  /* the pair: about three modes' work (P0) */
    }
#ifdef DINGHY_HOST
    bar_h_blk_ms += nlive * BAR_N + fade0 * BAR_SUB;
    bar_h_blk_mul += (2u * nlive + 1u) * BAR_N;
#endif
    v->struck = 0;
    if (v->fade == 0) {                             /* a steal finished fading: start the waiting note */
        v->alive = 0;
        if (v->pend) {
            uint32_t rel = v->pend;                 /* 1 held, 2 let go, 3 let go by an all-off (bar_all_off) */
            bar_init(v, v->pend_note, v->pend_vel);
            if (rel >= 2u) { v->held = 0; bar_set_damper(v, rel == 3u ? 32768 : bar_mute_w(bar_pedal_w)); }
        }
        return;
    }
    if (!v->ptab && !v->felt_n && sum < v->kill1) { v->alive = 0; return; }   /* under 1 LSB out: the voice ends */
    if (!v->ptab BAR_SLEEP_ON) {                    /* upper modes: absolute and relative sleep, faded out over the
                                                     * next sub-block (live = 2); they never wake on their own */
        ref = v->m[0].amp + (int32_t)(((int64_t)v->m[1].amp * iabs(v->wt)) >> 28);
        ref >>= ty->sleep_rel;
        if (ref < (v->kill1 >> 4)) ref = v->kill1 >> 4;
        for (m = BAR_UP0; m < BAR_NM; m++) {
            bar_ms_t *s = &v->m[m];
            if (s->live == 1u && s->amp < ref) s->live = 2;
        }
    }
}

__attribute__((noinline)) static void bar_render(int32_t *mono_out, uint32_t n)   /* kept out of app_audio */
{
    uint32_t i, sb, j, act = 0, held = 0, ty = bar_sub < BAR_NTYPE ? bar_sub : 0u, nb = BAR_TYPE[ty].nbody;
    int32_t mx = 0;
    bar_motor_block();
    bar_budget();
    for (i = 0; i < n; i++) bar_mono[i] = 0;
#ifdef DINGHY_HOST
    bar_h_blk_ns = 1000u * bar_plan_us;             /* the cost model's time for this block, effects included */
#endif
    for (i = 0; i < BAR_NV; i++) {
        bar_voice_t *v = &bv[i];
        if (!v->alive) continue;
        bar_voice_block(v, bar_mono);
        if (v->alive && v->life && --v->life == 0) v->alive = 0;
    }
    if (bar_nz_e) {                                 /* contact noise: LFSR -> two biquads (Q14, transposed DF2) x the
                                                     * envelope (tau ~ 23 ms); off, and exactly silent, under 1/2 LSB */
        int32_t e = bar_nz_e, s0 = bar_nz_s[0], s1 = bar_nz_s[1], s2 = bar_nz_s[2], s3 = bar_nz_s[3];
        uint32_t r = bar_lfsr;
        for (j = 0; j < n; j++) {
            int32_t nz, y;
            r = (r >> 1) ^ (-(r & 1u) & 0xB400u);
            nz = ((int32_t)(r & 0xFFFFu) - 32768) >> 2;
            y = ((NZ_BQ[0][0] * nz) >> 14) + s0;
            s0 = (int32_t)(((int64_t)NZ_BQ[0][1] * nz - (int64_t)NZ_BQ[0][3] * y) >> 14) + s1;
            s1 = (int32_t)(((int64_t)NZ_BQ[0][2] * nz - (int64_t)NZ_BQ[0][4] * y) >> 14);
            nz = y;
            y = (int32_t)(((int64_t)NZ_BQ[1][0] * nz) >> 14) + s2;
            s2 = (int32_t)(((int64_t)NZ_BQ[1][1] * nz - (int64_t)NZ_BQ[1][3] * y) >> 14) + s3;
            s3 = (int32_t)(((int64_t)NZ_BQ[1][2] * nz - (int64_t)NZ_BQ[1][4] * y) >> 14);
            bar_mono[j] += (int32_t)(((int64_t)y * e) >> 13);
            e -= (e + 1023) >> 10;                  /* reaches 0: no floor at 1023 */
        }
        bar_lfsr = r;
        if (e < 16) e = s0 = s1 = s2 = s3 = 0;
        bar_nz_e = e; bar_nz_s[0] = s0; bar_nz_s[1] = s1; bar_nz_s[2] = s2; bar_nz_s[3] = s3;
#ifdef DINGHY_HOST
        bar_h_blk_mul += 11u * n;
#endif
    }
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
