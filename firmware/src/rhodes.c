/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes engine: the electric piano voices.
 *
 * PLACEHOLDER. This is a decaying sine with a touch of second harmonic, only so that the voice handling,
 * the damper and the sustain pedal can be built and heard now. The physical model (hammer, tine, tonebar,
 * pickup) replaces the inside of rh_render() and rh_strike(); the interface below stays.
 *
 * The interface (frodhes.c uses only this):
 *   rh_note_on(note, vel)   main loop: strike a tine
 *   rh_note_off(note)       main loop: key up; the damper falls unless the pedal holds it
 *   rh_pedal(on)            main loop: the sustain pedal (MIDI CC 64)
 *   rh_render(out, n)       audio ISR: add n stereo frames (L R L R, Q15) to out
 *   rh_quiet()              main loop: nothing sounds (safe to write the flash)
 *   rh_lit(base, n)         main loop: bit k = note base + k is held (for the LEDs and the screen)
 *
 * Main loop / ISR handover: the main loop writes a voice's parameters first and bumps `strike` last; the ISR
 * starts the voice when it sees `strike` change. `damped` is a single byte, written by the main loop only.
 * Integers only (docs/RULES.md): Q15 levels, 32-bit phases, envelopes once per RH_BLOCK frames. */

#define RH_NV 8u                                /* voices */
#define RH_BLOCK 16u                            /* frames per envelope step */
#define RH_ENV_ONE (1 << 24)                    /* envelope full scale: Q24 */

static struct {
    /* written by the main loop */
    volatile uint32_t inc;                      /* phase step per frame: f * 2^32 / FS */
    volatile int32_t level;                     /* strike level, Q15 */
    volatile int32_t decay;                     /* envelope factor per block while the key is held, Q24 */
    volatile uint32_t strike;                   /* bumped last: the ISR starts the voice */
    volatile uint8_t damped;                    /* the damper is on the tine */
    uint8_t note, key;                          /* the note; key: still held */
    uint32_t age;
    /* written by the ISR (the main loop only reads `seen` and `env`) */
    volatile uint32_t seen;
    volatile int32_t env;                       /* Q24; 0 = silent */
    uint32_t ph;
} rv[RH_NV];

static volatile uint8_t rh_hold;                /* sustain pedal down */
static uint32_t rh_ages;

static int32_t rh_sine(uint32_t ph)             /* a full cycle over 2^32 -> -32767..32767 (refined parabola, ~0.1 %) */
{
    int32_t x = (int32_t)ph >> 16, ax = x < 0 ? -x : x, y, ay;
    y = (x * (32768 - ax)) >> 13;
    ay = y < 0 ? -y : y;
    y += ((((y * ay) >> 15) - y) * 7373) >> 15;
    return y > 32767 ? 32767 : y < -32767 ? -32767 : y;
}

/* ---- audio ISR */
#define RH_RELEASE ((int32_t)(RH_ENV_ONE - 101460)) /* damped: ~60 ms time constant at 44.1 kHz, 16-frame blocks */

static void rh_render(int32_t *out, uint32_t n)
{
    uint32_t v, i, j;
    for (v = 0; v < RH_NV; v++) {
        uint32_t ph, inc;
        int32_t env, lvl, k;
        if (rv[v].strike != rv[v].seen) {       /* a new strike: from the top */
            rv[v].seen = rv[v].strike;
            rv[v].ph = 0;
            rv[v].env = RH_ENV_ONE;
        }
        env = rv[v].env;
        if (!env)
            continue;                           /* silent: costs nothing */
        ph = rv[v].ph;
        inc = rv[v].inc;
        lvl = rv[v].level;
        k = rv[v].damped ? RH_RELEASE : rv[v].decay;
        for (i = 0; i < n; i += RH_BLOCK) {
            int32_t a = (int32_t)(((int64_t)lvl * env) >> 24);   /* Q15 amplitude for this block */
            for (j = i; j < i + RH_BLOCK && j < n; j++) {
                int32_t s;
                ph += inc;
                s = rh_sine(ph) + (rh_sine(ph << 1) >> 3);          /* PLACEHOLDER timbre */
                s = (s * a) >> 15;
                out[2u * j] += s;
                out[2u * j + 1u] += s;
            }
            env = (int32_t)(((int64_t)env * k) >> 24);
            if (env < 256) { env = 0; break; }  /* -96 dB: off */
        }
        rv[v].ph = ph;
        rv[v].env = env;
    }
}

/* ---- main loop */
static const uint32_t RH_INC_C9[12] = {         /* MIDI 120..131 (C9..B9) at 44.1 kHz; lower octaves shift right */
    815363807u, 863847862u, 915214929u, 969636441u, 1027294024u, 1088380105u,
    1153098554u, 1221665363u, 1294309365u, 1371273005u, 1452813141u, 1539201906u};

static void rh_note_on(uint32_t note, uint32_t vel)
{
    uint32_t v, pick = 0, best = 0xFFFFFFFFu;
    if (note > 127u || !vel || vel > 127u) return;
    for (v = 0; v < RH_NV; v++) {               /* the same note, else a silent voice, else a damped one, else the oldest */
        uint32_t score = rv[v].note == note ? 0u
                       : !rv[v].env && rv[v].strike == rv[v].seen ? 1u   /* (a strike not yet started is busy) */
                       : rv[v].damped ? 0x40000000u + rv[v].age
                       : 0x80000000u + rv[v].age;
        if (score < best) { best = score; pick = v; }
    }
    rv[pick].note = (uint8_t)note;
    rv[pick].key = 1;
    rv[pick].age = ++rh_ages;
    rv[pick].inc = RH_INC_C9[note % 12u] >> (10u - note / 12u);
    rv[pick].level = (int32_t)(vel * vel * 2u);             /* 127 -> 32258 Q15; the square: a softer touch */
    rv[pick].level >>= 2;                                   /* headroom for RH_NV voices */
    rv[pick].decay = RH_ENV_ONE - (int32_t)(1200u + note * 90u);   /* PLACEHOLDER: high notes die sooner */
    rv[pick].damped = 0;
    rv[pick].strike++;                          /* last: the ISR picks it up */
}

static void rh_note_off(uint32_t note)
{
    uint32_t v;
    for (v = 0; v < RH_NV; v++)
        if (rv[v].note == note && rv[v].key) {
            rv[v].key = 0;
            if (!rh_hold) rv[v].damped = 1;
        }
}

static void rh_pedal(int on)
{
    uint32_t v;
    rh_hold = (uint8_t)(on != 0);
    if (!on)
        for (v = 0; v < RH_NV; v++)
            if (!rv[v].key) rv[v].damped = 1;
}

static int rh_quiet(void)
{
    uint32_t v;
    for (v = 0; v < RH_NV; v++)
        if (rv[v].env || rv[v].strike != rv[v].seen) return 0;
    return 1;
}

static uint32_t rh_lit(uint32_t base, uint32_t n)
{
    uint32_t v, lit = 0;
    for (v = 0; v < RH_NV; v++)
        if (rv[v].key && rv[v].note >= base && rv[v].note < base + n)
            lit |= 1u << (rv[v].note - base);
    return lit;
}
