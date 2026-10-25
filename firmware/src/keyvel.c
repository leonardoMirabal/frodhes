/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes key velocity: how hard an FM-1 key landed, as a note-on velocity (the contract: keyvel.h).
 *
 * After charlesvestal/fm1-velocity by Charles Vestal (https://github.com/charlesvestal/fm1-velocity, commit 8f62ce0,
 * GPL-3.0-only; its sense.c has no copyright line): the method (a key's driven reading once its rubber dome has
 * landed), the drive, the log scale (reading - the diode's base is the pill's resistance, whose log is linear in the
 * log of the force), the curve's exponent 0.7, the driven floor 450 at DRIVE 1, the ceiling 1000 and the base 185 are
 * his. His float maths is rewritten here in integers; the state machine is Frodhes' own, for Frodhes' scan (variant B:
 * a conversion only for a key whose contact just closed), and so is the calibration: a per-key floor learned
 * downward only plus one shared ceiling (his two-ended per-key calibration made things worse: 11 % of notes at 127).
 *
 * The keys are rubber domes with a carbon pill; the dome snaps, so a key lands within a sample or two whatever its
 * speed: what tells a hard press from a soft one is the pill's resistance once landed. 20 of the 27 keys (F3..C5,
 * notes 0..19) sit on the matrix rows PA7 and PA8, which are converter pins; the top 7 (C#5..G5, row PA6) have none.
 * Note on and note off stay on the digital scan (hal/fm1_input.h: a press after 2 closed frames, a release after 8
 * open ones); this file only supplies the velocity.
 *
 * ISR side (kv_isr_*, from hal/fm1_keyadc.h in the 10 kHz tick: short, O(1), no calls out, no loop over the keys).
 * One state per analog key:
 *   IDLE   -> ARMED  its contact reads closed while its note is off (and kv.on): its column gets a pending sample
 *   ARMED  -> DONE   kv.wait driven samples of it have come (the lowest kept): kv.raw[k], its bit in kv.ready
 *   ARMED  (open)    note still off: back to IDLE (a graze, or let go before its note); note on: it waits for the
 *                    contact to close again (a bounce), no sample of an open contact (one reading KV_OPEN or
 *                    higher: it opened since its column was read, not counted)
 *   DONE   -> HELD   the note is on (the press the main loop plays)
 *   DONE   -> IDLE   open with the note off (a graze that was sampled: its reading is never used, the next arm
 *                    clears it)
 *   HELD   -> IDLE / ARMED   the note is off (a closed contact then arms at once: a fast repeat)
 * Main-loop side (kv_keys): every debounced press of an analog key waits for its reading, at most KV_WAIT_MS; the
 * reading goes through the curve below. No reading after KV_WAIT_MS: CONTEXT (a fallback). A key released before its
 * reading came (its release reached the main loop first: the host, or a main loop that stalled) plays at once at
 * CONTEXT in the same pass, before its release is handled, so it is never dropped nor left hanging; its reading, if
 * it still comes, is dropped (the next landing re-arms and clears it). The top 7 keys play CONTEXT: the recent average
 * of the analog keys' velocities (KEY VEL after KV_CTX_MS without one). TOUCH FIXED: every key at KEY VEL at once, as
 * before key velocity, and nothing is armed.
 *
 * The curve (kv_curve, integers only), driven reading d, key floor fl (its firmest landing), the shared ceiling C
 * (the lightest press that seats):
 *   t = log2((d - B) / (fl - B)) / log2((C - B) / (fl - B)), 0..1      (B = KV_DBASE)
 *   s = 1 - t (the strength), u = s ^ gamma (TOUCH: LIGHT 0.45, MEDIUM 0.70 = fm1-velocity's, HEAVY 1.10)
 *   velocity: 1 at u = 0, KEY VEL at u = KV_UMID, 127 at u = 1, linear on each side.
 * KV_UMID is the median of fm1-velocity's free playing at 0.7 (quartiles 40 / 59 / 74 on 1 + 126 u): so KEY VEL is
 * the velocity of a medium press, as it was every press's before. The floor starts at KV_FLOOR0 and only learns
 * downward, at most KV_FLOOR_STEP a landing, never under KV_FLOOR_MIN (lost at power-off; CC 41 32 resets it).
 * Every constant is a starting point from fm1-velocity's unit, to be re-measured on ours (tools/hil_keyvel.py). */

static const char *const KV_TOUCH_NAME[KV_NTOUCH] = {"LIGHT", "MEDIUM", "HEAVY", "FIXED"};

#define KV_NA 20u                       /* the analog keys (KV_ANALOG: notes 0..19) */
#define KV_DBASE 185u                   /* the driven reading with no pill resistance (the diode and the 595) */
#define KV_FLOOR0 450u                  /* a key's floor before it has landed lower (DRIVE 1) */
#define KV_FLOOR_MIN 217u               /* the floor never below this (KV_DBASE + 32) */
#define KV_FLOOR_STEP 48u               /* the most one landing moves its key's floor down */
#define KV_CEIL 1000u                   /* the lightest press that seats (velocity 1), every key */
#define KV_UMID 15073u                  /* 0.46 in Q15: a medium press (KEY VEL) */
#define KV_OPEN 1016u                   /* a driven sample this high: the contact had opened (a bounce), not counted */
#define KV_CTX_MS 5000u                 /* CONTEXT forgets the analog keys after this long */
static const uint16_t KV_GAMMA[3] = {115, 179, 282};   /* Q8: 0.45, 0.70, 1.10 (LIGHT MEDIUM HEAVY) */

/* the analog key at (column, row 0 = PA7 / 1 = PA8), 0xFF none: hal/fm1_input.h FM1_KEYMAP's rows 3 and 4 as note
 * numbers (key id - 14; PA8's columns 0 and 1 are OCT- / OCT+). tests/c/keyvel_test.c and test_keyvel.py check it */
static const uint8_t KV_COLKEY[KV_NCOL][2] = {
    {9, 0xFF}, {10, 0xFF}, {11, 1}, {12, 0}, {13, 3}, {14, 2}, {15, 5}, {16, 4}, {17, 6}, {18, 7}, {19, 8},
};
enum { KS_IDLE, KS_ARMED, KS_DONE, KS_HELD };
#define KV_ON_ROWS 0x18u                /* kv.on when arming: raw's PA7 / PA8 bits (bit 3 row 0, bit 4 row 1) */

/* shared ISR <-> main loop. The ISR writes st / n / min / raw / pend / act / defer / fake and sets ready bits; the
 * main loop sets on / wait and takes ready bits and raw[] under fm1__lock */
static volatile struct {
    uint8_t st[KV_NA], n[KV_NA];        /* the state (KS_*); the samples taken since armed */
    uint16_t min[KV_NA], raw[KV_NA];    /* the lowest sample so far; the reading handed to the main loop */
    uint32_t ready;                     /* bit k: kv.raw[k] is new (ISR sets, the main loop clears) */
    uint32_t defer, fake;               /* bit k: its sample waited once for the LEDs; a developer storm's key */
    uint8_t pend[KV_NCOL];              /* per column: bit r = its row r key wants a sample */
    uint8_t act[KV_NCOL];               /* per column: bit r = its row r key is not IDLE */
    uint8_t on;                         /* main loop (kv_apply): arm keys; KV_ON_ROWS (the analog rows' raw bits) or 0 */
    uint8_t wait;                       /* samples per landing (1..2), the lowest kept */
} kv KV_BSS(kv);

/* ---- ISR side (hal/fm1_keyadc.h). Inlined into the tick: no calls (the hooks may have to go to .ram_text) */
#define KV_ISR static inline __attribute__((always_inline))
KV_ISR void kv__arm(uint32_t col, uint32_t r, uint32_t k)
{
    kv.st[k] = KS_ARMED; kv.n[k] = 0; kv.min[k] = 0xFFFFu;
    kv.ready &= ~(1u << k);             /* a reading left from a press that never played is not this one's */
    kv.defer &= ~(1u << k);
    kv.pend[col] |= (uint8_t)(1u << r);
    kv.act[col] |= (uint8_t)(1u << r);
}
KV_ISR void kv__idle(uint32_t col, uint32_t r, uint32_t k)
{
    kv.st[k] = KS_IDLE;
    kv.pend[col] &= (uint8_t)~(1u << r);
    kv.act[col] &= (uint8_t)~(1u << r);
    kv.fake &= ~(1u << k);
}
KV_ISR void kv__key(uint32_t p, uint32_t r, uint32_t closed, uint32_t notes)   /* one analog key of column p */
{
    uint32_t k = KV_COLKEY[p][r], bit, st;
    if (k >= KV_NA) return;
    bit = 1u << k;
    st = kv.st[k];
    if (st == KS_IDLE) {
        if (closed && kv.on && !(notes & bit)) kv__arm(p, r, k);
    } else if (st == KS_ARMED) {
        if (kv.fake & bit) return;      /* a storm's landing: sampled whatever the contact */
        if (closed) kv.pend[p] |= (uint8_t)(1u << r);
        else if (notes & bit) kv.pend[p] &= (uint8_t)~(1u << r);   /* a bounce of a played key: wait, no open sample */
        else kv__idle(p, r, k);         /* a graze, or let go before its note */
    } else if (st == KS_DONE) {
        if (notes & bit) { if (!(kv.fake & bit)) kv.st[k] = KS_HELD; }
        else if (!closed) kv__idle(p, r, k);
    } else if (!(notes & bit)) {        /* HELD, its note off */
        if (closed && kv.on) kv__arm(p, r, k);
        else kv__idle(p, r, k);
    }
}
/* column p has anything to look at: an analog row reads closed (TOUCH on: kv.on is the rows' mask), or one of its
 * keys is busy */
KV_ISR int kv_isr_want(uint32_t p, uint32_t raw) { return (raw & kv.on) | kv.act[p]; }
/* the tick's idle test: 0 = kv_isr_want(p, raw) is 0 and kv_isr_pend(n) is 0 (a pending key is never IDLE: pend is a
 * subset of act), so the tick skips both (two byte loads off one base, one with raw) */
KV_ISR uint32_t kv_isr_busy(uint32_t p, uint32_t n, uint32_t raw) { return (raw & kv.on) | kv.act[p] | kv.pend[n]; }
KV_ISR void kv_isr_col(uint32_t p, uint32_t raw, uint32_t notes)   /* column p was just read (raw: 1 = closed) */
{
    kv__key(p, 0, raw >> 3 & 1u, notes);
    kv__key(p, 1, raw >> 4 & 1u, notes);
}
KV_ISR uint32_t kv_isr_pend(uint32_t n) { return kv.pend[n]; }
KV_ISR int32_t kv_isr_pick(uint32_t n, uint32_t odd)   /* the row of column n to sample, -1 none */
{
    uint32_t m = kv.pend[n];
    return !m ? -1 : m == 3u ? (int32_t)(odd & 1u) : (int32_t)(m >> 1);
}
KV_ISR void kv_isr_sample(uint32_t n, uint32_t r, uint32_t v)   /* a driven reading of (column n, row r) */
{
    uint32_t k;
    r &= 1u;
    k = KV_COLKEY[n][r];
    if (k >= KV_NA || kv.st[k] != KS_ARMED || !(kv.pend[n] >> r & 1u)) return;   /* (STRESS: any row) */
    if (v >= KV_OPEN && !(kv.fake & (1u << k))) return;   /* opened since its column was read: the next frame's */
    if (v < kv.min[k]) kv.min[k] = (uint16_t)v;
    if (++kv.n[k] < (kv.wait ? kv.wait : 1u)) return;
    kv.st[k] = KS_DONE;
    kv.pend[n] &= (uint8_t)~(1u << r);
    if (kv.fake & (1u << k)) return;    /* a storm's: dropped */
    kv.raw[k] = kv.min[k];
    kv.ready |= 1u << k;
}
KV_ISR int kv_isr_defer(uint32_t n, uint32_t r)    /* wait a frame for a sparse LED frame? once a landing */
{
    uint32_t k = KV_COLKEY[n][r & 1u];
    if (k >= KV_NA || kv.defer & 1u << k) return 0;
    kv.defer |= 1u << k;
    return 1;
}
static void kv_isr_storm(void)          /* developer: 8 landings at once, columns 2..9, both rows (results dropped) */
{
    uint32_t c;
    for (c = 2; c < 10u; c++) {
        uint32_t r = c & 1u, k = KV_COLKEY[c][r];
        if (kv.st[k] != KS_IDLE) continue;
        kv.fake |= 1u << k;
        kv__arm(c, r, k);
    }
}

/* ---- main loop */
#ifndef KV_HW
#define KV_HW KV_FM1KA                  /* 0, no converter (the host, the SMK-37): a key plays at once (CONTEXT) unless
                                         * "T vkey" gave it a reading */
#endif
#ifdef DINGHY_HOST
static uint32_t kv_lock(void) { return 0; }
static void kv_unlock(uint32_t k) { (void)k; }
#else
static uint32_t kv_lock(void) { return fm1__lock(); }
static void kv_unlock(uint32_t k) { fm1__unlock(k); }
#endif

static struct {
    uint32_t wait, t0[KV_NK];           /* keys pressed, waiting for their reading; since when (fm1_ms) */
    uint16_t floor_[KV_NA];             /* per analog key: its firmest landing so far (0: KV_FLOOR0) */
    uint8_t vel[KV_NK];                 /* the velocity of each key kv_keys starts */
    uint32_t ctx_q8, ctx_t;             /* CONTEXT: the analog keys' recent velocity x 256, and when last */
    uint8_t ctx_have, last_vel, report, mode;   /* report: CC 41 8 / 9; mode: KV_DEV_* 0..4 */
    uint16_t n_notes, n_buttons;        /* developer, since the mode was set: key presses, button presses */
    uint16_t n_analog, n_fallback;      /* presses that got their reading, and the analog ones that did not */
} kvm KV_BSS(kvm);

static const uint16_t KV_LOG2T[128] = {   /* log2(1 + i / 128) in Q16 (the 129th, 65536: kv_log2) */
    0, 736, 1466, 2190, 2909, 3623, 4331, 5034, 5732, 6425, 7112, 7795, 8473, 9146, 9814, 10477, 11136, 11791, 12440,
    13086, 13727, 14363, 14996, 15624, 16248, 16868, 17484, 18096, 18704, 19308, 19909, 20505, 21098, 21687, 22272,
    22854, 23433, 24007, 24579, 25146, 25711, 26272, 26830, 27384, 27936, 28484, 29029, 29571, 30109, 30645, 31178,
    31707, 32234, 32758, 33279, 33797, 34312, 34825, 35334, 35841, 36346, 36847, 37346, 37842, 38336, 38827, 39316,
    39802, 40286, 40767, 41246, 41722, 42196, 42667, 43137, 43603, 44068, 44530, 44990, 45448, 45904, 46357, 46809,
    47258, 47705, 48150, 48593, 49034, 49472, 49909, 50344, 50776, 51207, 51636, 52063, 52488, 52911, 53332, 53751,
    54169, 54584, 54998, 55410, 55820, 56229, 56635, 57040, 57443, 57845, 58245, 58643, 59039, 59434, 59827, 60219,
    60609, 60997, 61384, 61769, 62152, 62534, 62915, 63294, 63671, 64047, 64421, 64794, 65166};
static const uint16_t KV_EXP2T[33] = {32768, 32066, 31379, 30706, 30048, 29405, 28774, 28158, 27554, 26964, 26386, 25821,
    25268, 24726, 24196, 23678, 23170, 22674, 22188, 21713, 21247, 20792, 20347, 19911, 19484, 19066, 18658, 18258,
    17867, 17484, 17109, 16743, 16384};   /* 2^(-i / 32) in Q15 */
static uint32_t kv_log2(uint32_t x)     /* log2(x) in Q16, 1 <= x < 2^16 (0 reads as 1) */
{
    uint32_t e = 0, m, i, f, a, b;
    if (!x) x = 1;
    while (x >> (e + 1u)) e++;
    m = (e >= 15u ? x >> (e - 15u) : x << (15u - e)) - 32768u;   /* the mantissa's fraction, Q15 */
    i = (m >> 8) & 127u; f = m & 255u;          /* (& 127: i <= 127 already; gcc cannot see it, -Warray-bounds) */
    a = KV_LOG2T[i]; b = i < 127u ? KV_LOG2T[i + 1u] : 65536u;
    return (e << 16) + a + (((b - a) * f + 128u) >> 8);
}
static uint32_t kv_exp2n(uint32_t a)    /* 2^(-a / 65536) in Q15 */
{
    uint32_t k = a >> 16, f = a & 65535u, i = f >> 11, r = f & 2047u, v;
    if (k >= 16u) return 0;
    v = KV_EXP2T[i] - (((uint32_t)(KV_EXP2T[i] - KV_EXP2T[i + 1u]) * r + 1024u) >> 11);
    return (v + (k ? 1u << (k - 1u) : 0u)) >> k;
}
/* the curve's two halves, shared with the SMK-37 (frodhes_smk.h smk_vel: its keybed's velocity is the strength):
 * u = s ^ gamma at TOUCH touch (LIGHT..HEAVY), s and u in Q15 */
static uint32_t kv_upow(uint32_t s, uint32_t touch)
{
    if (!s) return 0;
    if (s >= 32768u) return 32768u;
    return kv_exp2n(((15u << 16) - kv_log2(s)) * KV_GAMMA[touch] >> 8);   /* s ^ gamma (< 2^31: 15 x 2^16 x 282) */
}
/* u (Q15) -> the velocity: 1 at u = 0, KEY VEL kvel (1..127) at u = KV_UMID, 127 at u = 1, linear on each side */
static uint32_t kv_vmap(uint32_t u, uint32_t kvel)
{
    uint32_t v;
    if (u <= KV_UMID) v = 1u + ((kvel - 1u) * u + KV_UMID / 2u) / KV_UMID;
    else v = kvel + ((127u - kvel) * (u - KV_UMID) + (32768u - KV_UMID) / 2u) / (32768u - KV_UMID);
    return v < 1u ? 1u : v > 127u ? 127u : v;
}
/* the velocity of driven reading d on a key whose floor is fl, at TOUCH touch, KEY VEL kvel (the top) */
static uint32_t kv_curve(uint32_t d, uint32_t fl, uint32_t touch, uint32_t kvel)
{
    uint32_t s;
    if (kvel < 1u) kvel = 1u;
    if (kvel > 127u) kvel = 127u;
    if (touch >= KV_FIXED) return kvel;
    if (fl < KV_FLOOR_MIN) fl = KV_FLOOR_MIN;
    if (fl >= KV_CEIL) fl = KV_CEIL - 1u;
    if (d <= fl) s = 32768u;
    else if (d >= KV_CEIL) s = 0;
    else {                              /* t = (l - l0) / (l1 - l0) in Q15, 32-bit, in two steps (the logs are Q16) */
        uint32_t l0 = kv_log2(fl - KV_DBASE), l1 = kv_log2(KV_CEIL - KV_DBASE), dl, den, q, t;
        if (l1 <= l0) return kvel;      /* (the divisor: never 0) */
        den = l1 - l0;
        dl = kv_log2(d - KV_DBASE) - l0;                /* < 2^20 */
        q = (dl << 11) / den;
        t = (q << 4) + (((dl << 11) - q * den) << 4) / den;
        s = t >= 32768u ? 0u : 32768u - t;
    }
    return kv_vmap(kv_upow(s, touch), kvel);
}
static uint32_t kv_ctx(void)            /* CONTEXT: the recent average of the analog keys, else KEY VEL */
{
    if (!kvm.ctx_have || fm1_ms - kvm.ctx_t > KV_CTX_MS) return set.key_vel;
    return (kvm.ctx_q8 + 128u) >> 8;
}
static void kv_apply(void)              /* TOUCH, the hardware or the developer mode changed (main loop) */
{
    kv.on = (uint8_t)(KV_HW && kv_touch() != KV_FIXED && kvm.mode != KV_DEV_OFF ? KV_ON_ROWS : 0u);
    if (!kv.wait) kv.wait = 1;
}
static void kv_report(uint32_t k, uint32_t raw, uint32_t src, uint32_t ms)
{
    if (!kvm.report) return;            /* developer (tools/hil_keyvel.py, keyvel.h KV_DEV_REPORT_ON) */
    midi_send(0xB0, 33, k);
    midi_send(0xB0, 34, raw >> 3 & 127u);
    midi_send(0xB0, 35, (raw & 7u) | (src & 15u) << 3);
    midi_send(0xB0, 36, ms > 127u ? 127u : ms);
}
static void kv_learn(uint32_t k, uint32_t d, uint32_t v)   /* a landing with its reading: the floor, CONTEXT */
{
    uint32_t fl = kvm.floor_[k] ? kvm.floor_[k] : KV_FLOOR0;
    if (d < fl) {                       /* learned downward only, at most a step a landing */
        uint32_t nf = fl - d > KV_FLOOR_STEP ? fl - KV_FLOOR_STEP : d;
        kvm.floor_[k] = (uint16_t)(nf < KV_FLOOR_MIN ? KV_FLOOR_MIN : nf);
    }
    if (kvm.ctx_have && fm1_ms - kvm.ctx_t <= KV_CTX_MS)   /* CONTEXT: a quarter of the way to it */
        kvm.ctx_q8 = (uint32_t)((int32_t)kvm.ctx_q8 + ((int32_t)(v << 8) - (int32_t)kvm.ctx_q8) / 4);
    else kvm.ctx_q8 = v << 8;
    kvm.ctx_have = 1; kvm.ctx_t = fm1_ms;
}
/* the keys pressed (down) and let go (up) since the last pass: the keys to start now, their velocity in kvm.vel[] */
static uint32_t kv_keys(uint32_t down, uint32_t up)
{
    uint32_t go = 0, rdy, k, lk, touch = kv_touch();
    uint16_t raw[KV_NA];
    down &= (1u << KV_NK) - 1u;
    for (k = 0; k < KV_NK; k++)
        if (down >> k & 1u) { kvm.t0[k] = fm1_ms; kvm.n_notes++; }
    kvm.wait |= down;
    if (!kvm.wait) return 0;
    lk = kv_lock();
    rdy = kv.ready & kvm.wait;
    kv.ready &= ~rdy;
    for (k = 0; k < KV_NA; k++) if (rdy >> k & 1u) raw[k] = kv.raw[k];
    kv_unlock(lk);
    for (k = 0; k < KV_NK; k++) {
        uint32_t bit = 1u << k, v, src, d = 0, ms = fm1_ms - kvm.t0[k];
        if (!(kvm.wait & bit)) continue;
        if (touch == KV_FIXED) { v = set.key_vel; src = 4; }
        else if (rdy & bit) {
            d = raw[k];
            v = kv_curve(d, kvm.floor_[k] ? kvm.floor_[k] : KV_FLOOR0, touch, set.key_vel);
            kv_learn(k, d, v);
            kvm.n_analog++;
            src = 0;
        }
        else if (!(KV_ANALOG & bit)) { v = kv_ctx(); src = 3; }
        else if (!kv.on) { v = kv_ctx(); src = 4; }   /* no converter (the host, CC 41 1) */
        else if (up & bit) { v = kv_ctx(); src = 2; kvm.n_fallback++; }   /* let go first: plays at once */
        else if (ms >= KV_WAIT_MS) { v = kv_ctx(); src = 1; kvm.n_fallback++; }
        else continue;                  /* its reading may still come */
        if (v < 1u) v = 1u;
        if (v > 127u) v = 127u;
        kv_report(k, d, src, ms);
        kvm.vel[k] = (uint8_t)v;
        kvm.last_vel = (uint8_t)v;
        go |= bit;
        kvm.wait &= ~bit;
    }
    return go;
}
static uint32_t kv_vel(uint32_t k)      /* the velocity of a key kv_keys just returned */
{
    if (kv_touch() == KV_FIXED || k >= KV_NK || !kvm.vel[k]) return set.key_vel;
    return kvm.vel[k];
}
static uint32_t kv_last_vel(void) { return kvm.last_vel; }

#ifdef DINGHY_HOST
/* tests/host.c "T vkey K RAW": key K's next press lands with driven reading RAW (as the ISR would hand it over) */
static void kv_host_vkey(uint32_t k, uint32_t raw)
{
    if (k >= KV_NA) return;             /* the top 7 have no converter */
    kv.raw[k] = (uint16_t)(raw > 1023u ? 1023u : raw);
    kv.ready |= 1u << k;
}
#endif

/* ---- developer (keyvel.h KV_CC_*, KV_DEV_*, KV_IT_*; nothing saved) */
static void kv_dev_edges(uint32_t buttons) { while (buttons) { kvm.n_buttons += (uint16_t)(buttons & 1u); buttons >>= 1; } }
static uint32_t kv_dev_item(uint32_t i)
{
    if (i == KV_IT_WAIT) return kv.wait;
    if (i == KV_IT_TOUCH) return kv_touch();
    if (i == KV_IT_NOTES) return kvm.n_notes;
    if (i == KV_IT_BUTTONS) return kvm.n_buttons;
    if (i == KV_IT_ANALOG) return kvm.n_analog;
    if (i == KV_IT_FALLBACK) return kvm.n_fallback;
    if (i >= KV_IT_FLOOR0 && i < KV_IT_FLOOR0 + KV_NA)
        return kvm.floor_[i - KV_IT_FLOOR0] ? kvm.floor_[i - KV_IT_FLOOR0] : KV_FLOOR0;
#if KV_FM1KA
    return fm1_ka_dev_item(i);
#else
    return i == KV_IT_MODE ? kvm.mode : 0;
#endif
}
static void kv_dev_cc(uint32_t cc, uint32_t v)
{
    uint32_t i;
    if (cc == KV_CC_QUERY) {
        for (i = v * 8u; i < v * 8u + 8u && i < KV_IT_N; i++) {
            uint32_t x = kv_dev_item(i);
            if (x > 16383u) x = 16383u;
            midi_send(0xB0, KV_CC_ITEM, i); midi_send(0xB0, KV_CC_HI, x >> 7); midi_send(0xB0, KV_CC_LO, x & 127u);
        }
        return;
    }
    if (cc != KV_CC_MODE) return;
    if (v == KV_DEV_REPORT_ON || v == KV_DEV_REPORT_OFF) { kvm.report = v == KV_DEV_REPORT_ON; return; }
    if (v == KV_DEV_WAIT1 || v == KV_DEV_WAIT1 + 1u) { kv.wait = (uint8_t)(v - KV_DEV_WAIT1 + 1u); return; }
    if (v == KV_DEV_FLOORS) { for (i = 0; i < KV_NA; i++) kvm.floor_[i] = 0; return; }
    if (v < KV_DEV_NMODE) {             /* a mode: the counters start again */
        kvm.n_notes = kvm.n_buttons = kvm.n_analog = kvm.n_fallback = 0;
        kvm.mode = (uint8_t)v;
        kv_apply();                     /* OFF: nothing armed, every key at once (CONTEXT) */
    } else if (v < KV_DEV_DRIVE1 || v > KV_DEV_PULL) return;
#if KV_FM1KA
    fm1_ka_dev_mode(v);                 /* the modes 0..4, DRIVE 16..20 */
#endif
}
