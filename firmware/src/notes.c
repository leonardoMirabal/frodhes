/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes notes (0.9.1), main loop only: where a played note goes, and chord mode.
 *
 *   sources   each FM-1 key (0..26) and each USB-MIDI note (27..154) is a source (155: spare): src_on()
 *             plays a chord or a single note for it (chord mode), src_off() stops exactly those notes. A strum
 *             starts the notes one after another (src_poll).
 *   out       out_on / out_off count each pitch, so two sources sharing a note never cut each other; the first
 *             one sends it to the piano, or to the arpeggiator while it runs (arp.c)
 *   chords    CHORD page: TYPE (OFF, DIATONIC and DIA 7TH from the SCALE page's key and scale, or a fixed shape),
 *             INVERSION, VOICING (close / open / +bass), STRUM; chord_name() names a set of notes for the screen
 *
 * The keys' notes are echoed to USB-MIDI, notes from USB-MIDI are not; every note started is told to the
 * sequencer's recorder (seq.c live_note_on / off). */

#define SRC_KEYS 0u
#define SRC_MIDI 27u
#define SRC_SEQ 155u
#if BOARD_SMK37
#define SRC_EXT 156u                             /* (SMK-37: its 10 outer keys, C3-E3 and G#5-C6: frodhes_smk.h) */
#define SRC_N 166u
#else
#define SRC_N 156u
#endif
#define CH_MAX 6u

/* ---- out: the piano or the arpeggiator, per pitch counted */
static uint8_t note_to_arp[128], out_ref[128], out_echo[128];
static void note_in_on(uint32_t n, uint32_t vel, int echo)
{
    if (arp_on()) { note_to_arp[n] = 1; arp_in_on(n, vel); return; }
    note_to_arp[n] = 0;
    rh_note_on(n, vel);
    if (echo) midi_send(0x90 | MIDI_OUT_CH, n, vel);
}
static void note_in_off(uint32_t n, int echo)
{
    if (note_to_arp[n]) { note_to_arp[n] = 0; arp_in_off(n); return; }
    rh_note_off(n);
    if (echo) midi_send(0x80 | MIDI_OUT_CH, n, 0);
}
static void out_on(uint32_t n, uint32_t vel, int echo)
{
    if (n > 127u) return;
    if (out_ref[n]++ == 0u) { out_echo[n] = (uint8_t)echo; note_in_on(n, vel, echo); }
}
static void out_off(uint32_t n)
{
    if (n > 127u || !out_ref[n]) return;
    if (--out_ref[n] == 0u) note_in_off(n, out_echo[n]);
}

/* ---- chord mode */
enum { CH_OFF, CH_DIA, CH_DIA7, CH_FIXED };                                    /* then the fixed shapes */
#define CH_NTYPE 15u
static const char *const CH_TYPE_NAME[CH_NTYPE] = {"OFF", "DIATONIC", "DIA 7TH", "MAJ", "MIN", "7", "MAJ7", "MIN7",
                                                    "SUS2", "SUS4", "ADD9", "DIM", "AUG", "6", "MIN6"};
static const uint8_t CH_SHAPE[CH_NTYPE - CH_FIXED][5] = {   /* intervals, 0xFF ends */
    {0, 4, 7, 0xFF}, {0, 3, 7, 0xFF}, {0, 4, 7, 10, 0xFF}, {0, 4, 7, 11, 0xFF}, {0, 3, 7, 10, 0xFF},
    {0, 2, 7, 0xFF}, {0, 5, 7, 0xFF}, {0, 4, 7, 14, 0xFF}, {0, 3, 6, 0xFF}, {0, 4, 8, 0xFF}, {0, 4, 7, 9, 0xFF},
    {0, 3, 7, 9, 0xFF},
};
static const char *const CH_INV_NAME[4] = {"ROOT", "1ST", "2ND", "3RD"};
static const char *const CH_VOICE_NAME[3] = {"CLOSE", "OPEN", "+BASS"};
#define CH_TYPE (set.chord[0])
#define CH_INV (set.chord[1])
#define CH_VOICE (set.chord[2])
#define CH_STRUM (set.chord[3])                  /* 0..12: x 5 ms between the notes */
static void chord_sanitize(void)
{
    if (CH_TYPE >= CH_NTYPE) CH_TYPE = 0;
    if (CH_INV > 3u) CH_INV = 0;
    if (CH_VOICE > 2u) CH_VOICE = 0;
    if (CH_STRUM > 12u) CH_STRUM = 0;
}
static int chord_on(void) { return CH_TYPE != CH_OFF; }

/* the chord on root n (already on the scale if SNAP / DEGREE did it): its notes in out[], their count */
static uint32_t chord_build(uint32_t n, uint8_t *out)
{
    int32_t v[CH_MAX], t;
    uint32_t c = 0, i, j;
    if (CH_TYPE == CH_DIA || CH_TYPE == CH_DIA7) {             /* stacked thirds of the SCALE page's scale */
        uint32_t m = SCALE_MASK[set.sc_scale], cnt = 0, deg = 0, pc;
        int32_t offs[12], r = (int32_t)n;
        r = scale_snap(r);
        pc = (uint32_t)(((r - (int32_t)set.sc_root) % 12 + 12) % 12);
        for (i = 0; i < 12u; i++) if (m >> i & 1u) { if (i == pc) deg = cnt; offs[cnt++] = (int32_t)i; }
        for (j = 0; j < (CH_TYPE == CH_DIA7 ? 4u : 3u); j++) {
            uint32_t k = deg + 2u * j;
            v[c++] = r + offs[k % cnt] - offs[deg] + 12 * (int32_t)(k / cnt);
        }
    } else {
        const uint8_t *s = CH_SHAPE[CH_TYPE - CH_FIXED];
        for (i = 0; i < 5u && s[i] != 0xFF; i++) v[c++] = (int32_t)n + s[i];
    }
    for (i = 0; i < CH_INV && i + 1u < c; i++) {               /* inversions: the lowest note up an octave */
        t = v[0] + 12;
        for (j = 0; j + 1u < c; j++) v[j] = v[j + 1u];
        v[c - 1u] = t;
    }
    if (CH_VOICE == 1u && c >= 3u) {                           /* open: the second note up an octave */
        t = v[1] + 12;
        for (j = 1; j + 1u < c; j++) v[j] = v[j + 1u];
        v[c - 1u] = t;
    } else if (CH_VOICE == 2u && c < CH_MAX) {                 /* +bass: the root an octave under the chord */
        for (j = c; j > 0u; j--) v[j] = v[j - 1u];
        v[0] = (int32_t)n - 12;
        c++;
    }
    for (i = j = 0; i < c; i++) if (v[i] >= 0 && v[i] <= 127) out[j++] = (uint8_t)v[i];
    return j;
}

static const char *const NOTE_NAME_S[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
static void ch_cat(char *d, const char *s) { while (*d) d++; while (*s) *d++ = *s++; *d = 0; }

/* a name for a set of notes (the sequencer's screen, capitals only): C, C MIN, C7, C MAJ7, C MIN7, C SUS4, C DIM ... */
static void chord_name(const uint8_t *notes, uint32_t c, char *s)
{
    static const struct { uint16_t mask; const char *suf; } T[] = {
        {0x891, " MAJ7"}, {0x489, " MIN7"}, {0x491, "7"}, {0x449, " M7B5"}, {0x249, " DIM7"}, {0x291, "6"},
        {0x289, " MIN6"}, {0x095, " ADD9"}, {0x091, ""}, {0x089, " MIN"}, {0x085, " SUS2"}, {0x0A1, " SUS4"},
        {0x049, " DIM"}, {0x111, " AUG"}, {0x081, "5"}, {0x001, ""},
    };
    uint32_t pcs = 0, i, r, lo = 127, k;
    s[0] = 0;
    if (!c) { s[0] = '-'; s[1] = 0; return; }
    for (i = 0; i < c; i++) { pcs |= 1u << (notes[i] % 12u); if (notes[i] < lo) lo = notes[i]; }
    for (k = 0; k < 12u; k++) {                                /* the bass note first as the root, then the others */
        r = (lo + k) % 12u;
        if (!(pcs >> r & 1u)) continue;
        {
            uint32_t rel = ((pcs >> r) | (pcs << (12u - r))) & 0xFFFu;
            for (i = 0; i < sizeof T / sizeof T[0]; i++)
                if (rel == T[i].mask) { ch_cat(s, NOTE_NAME_S[r]); ch_cat(s, T[i].suf); return; }
        }
    }
    ch_cat(s, NOTE_NAME_S[lo % 12u]);                          /* nothing known: the bass and a mark */
    ch_cat(s, "*");
}

/* ---- sources */
typedef struct { uint8_t n[CH_MAX], c, started, vel, echo; uint32_t t0; } src_t;
static src_t src[SRC_N];
static uint8_t src_active[SRC_N];
static void live_note_on(uint32_t n, uint32_t vel);   /* seq.c: what is played, for the recorder */
static void live_note_off(uint32_t n);
static void src_note_on(uint32_t slot, uint32_t n, uint32_t vel, int echo)
{
    out_on(n, vel, echo);
    if (slot != SRC_SEQ) live_note_on(n, vel);
}

static void src_start(uint32_t slot, const uint8_t *notes, uint32_t c, uint32_t vel, int echo)
{
    src_t *s = &src[slot];
    uint32_t i;
    s->c = (uint8_t)(c > CH_MAX ? CH_MAX : c);
    for (i = 0; i < s->c; i++) s->n[i] = notes[i];
    s->vel = (uint8_t)vel; s->echo = (uint8_t)echo; s->t0 = fm1_ms; s->started = 0;
    src_active[slot] = 1;
    if (s->c) { src_note_on(slot, s->n[0], vel, echo); s->started = 1; }
    for (i = 1; i < s->c && !CH_STRUM; i++) { src_note_on(slot, s->n[i], vel, echo); s->started = (uint8_t)(i + 1u); }
}
/* a key or a MIDI note pressed: root n (after the scale) */
static void src_on(uint32_t slot, uint32_t n, uint32_t vel, int echo)
{
    uint8_t notes[CH_MAX];
    uint32_t c;
    if (src_active[slot]) return;
    if (chord_on()) c = chord_build(n, notes); else { notes[0] = (uint8_t)n; c = 1; }
    src_start(slot, notes, c, vel, echo);
}
static void src_off(uint32_t slot)
{
    src_t *s = &src[slot];
    uint32_t i;
    if (!src_active[slot]) return;
    for (i = 0; i < s->started; i++) {                        /* a strum cut short: the rest never starts */
        out_off(s->n[i]);
        if (slot != SRC_SEQ) live_note_off(s->n[i]);
    }
    src_active[slot] = 0;
    s->c = s->started = 0;
}
/* the strum: the next notes when their time comes */
static void src_poll(void)
{
    uint32_t i;
    if (!CH_STRUM) {
        for (i = 0; i < SRC_N; i++)                           /* (a strum set to 0 mid-chord: the rest at once) */
            while (src_active[i] && src[i].started < src[i].c) { src_note_on(i, src[i].n[src[i].started], src[i].vel, src[i].echo); src[i].started++; }
        return;
    }
    for (i = 0; i < SRC_N; i++) {
        src_t *s = &src[i];
        while (src_active[i] && s->started < s->c && fm1_ms - s->t0 >= (uint32_t)s->started * CH_STRUM * 5u) {
            src_note_on(i, s->n[s->started], s->vel, s->echo);
            s->started++;
        }
    }
}
