/* SPDX-License-Identifier: GPL-3.0-only */
/* The engines' controls on the panel (ui.c): one table per engine. The UI builds the VOICE pages from it (four
 * controls a page, in the table's order: VOICE 1/n .. n/n, SELECT walks them), the FRODHES page's K2 / K3 (the two
 * headline controls) and the PERFORM / ASSIGN names of the engine's parameters. A new engine adds its table here and
 * its row in ENG_UI (by params.c TYPE_ENGINE); the page code is not touched.
 *
 * A control: the slot (a parameter id < P_COUNT, or a ui.c slot such as S_TUNE), its name on the row, its kind, its
 * unit, the headline flag and, for named detents, the names and where they sit. The range and the number's format are
 * the parameter's own (PARAMS[id].hi / .fmt, or the slot's in ui.c slot_val / slot_text), so the table cannot disagree
 * with the CC map, the records or the presets. Included by ui.c after its S_* slot ids. */

enum { EK_AMT, EK_CHOICE, EK_DETENT };      /* an amount (a fill and a number), a choice (segments), named detents (a
                                             * fill and the detent's name, the number between two of them) */
enum { EU_NONE, EU_HZ, EU_MS, EU_DB, EU_C };   /* the unit, small next to the number: Hz, ms, dB, c (cents) */
typedef struct {
    uint8_t id;                              /* the slot */
    const char *name;                        /* the row's label (it ends before the segments, x 88) */
    uint8_t kind, unit;                      /* EK_*, EU_* */
    uint8_t head;                            /* 1 = the FRODHES page's K2, 2 = its K3 (0: not a headline) */
    uint8_t nn, tol;                         /* EK_DETENT: the names; how far from a detent its name still shows */
    const char *const *names;                /* EK_DETENT: one name per detent */
    const uint8_t *at;                       /* EK_DETENT: each name's value */
} ectl_t;
typedef struct {
    const ectl_t *c;
    uint8_t n;                               /* controls: VOICE pages = (n + 3) / 4 */
    uint8_t cmp_mkup;                        /* the compressor's make-up when the panel switches it on with none set
                                             * (-20 dB 4:1): level-neutral for this engine on the FM-1 (Rhodes 15 =
                                             * +4.3 dB; VIBES is peakier, the same threshold takes more: 28 = +6.8 dB) */
    const char *const *hint;                 /* the footer's hint, one per VOICE page */
} engui_t;

/* the Rhodes (TYPE 0..3): the tine's voicing, then how notes die away */
static const ectl_t ENG_RHODES_C[] = {
    {P_TONE, "TONE", EK_AMT, EU_NONE, 1},    /* FRODHES K2 (PLAN rev. 12: TONE + BELL; DRIVE / REVERB have pages) */
    {P_BARK, "BARK", EK_AMT, EU_NONE},
    {P_BELL, "BELL", EK_AMT, EU_NONE, 2},    /* FRODHES K3 */
    {P_HAMMER, "HAMMER", EK_AMT, EU_NONE},
    {P_DECAY, "DECAY", EK_AMT, EU_NONE},
    {P_RELEASE, "RELEASE", EK_AMT, EU_NONE},
    {P_NOISE, "NOISE", EK_AMT, EU_NONE},
    {S_TUNE, "TUNE", EK_AMT, EU_C},
};
static const char *const ENG_RHODES_HINT[] = {"HOW THE TINE IS STRUCK", "HOW NOTES DIE AWAY"};

/* the bar engine (VIBES): the same parameters as the Rhodes, what they do on a vibraphone, then its motor */
static const char *const BAR_STRIKE_NAME[3] = {"CENTRE", "L/3", "EDGE"};       /* STRIKE: where the mallet hits */
static const uint8_t BAR_STRIKE_V[3] = {0, 64, 127};
static const char *const BAR_FAM_NAME[4] = {"SOFT", "YARN", "CORD", "RUBBER"};  /* MALLET: the pulse families */
static const uint8_t BAR_FAM_V[4] = {0, 42, 85, 127};   /* at each family's centre (bars.c's KN_FAM puts them there) */
static const ectl_t ENG_BARS_C[] = {
    {P_TONE, "STRIKE", EK_DETENT, EU_NONE, 0, 3, 0, BAR_STRIKE_NAME, BAR_STRIKE_V},   /* the name exactly there */
    {P_BARK, "MALLET", EK_DETENT, EU_NONE, 1, 4, 4, BAR_FAM_NAME, BAR_FAM_V},         /* the name within +-4; K2 */
    {P_BELL, "TUBE", EK_AMT, EU_NONE},
    {P_HAMMER, "WEIGHT", EK_AMT, EU_NONE},
    {P_DECAY, "RING", EK_AMT, EU_NONE},
    {P_RELEASE, "DAMPER", EK_AMT, EU_NONE},
    {P_NOISE, "NOISE", EK_AMT, EU_NONE},
    {S_TUNE, "TUNE", EK_AMT, EU_C},
    {P_MOT_ON, "MOTOR", EK_CHOICE, EU_NONE, 2},                                       /* FRODHES K3 */
    {P_MOT_DEPTH, "DEPTH", EK_AMT, EU_NONE},
    {P_MOT_RATE, "SPEED", EK_AMT, EU_HZ},
    {P_MOT_SHAPE, "SHAPE", EK_AMT, EU_NONE},
};
static const char *const ENG_BARS_HINT[] = {"HOW THE MALLET STRIKES", "HOW BARS RING AND STOP", "THE VIBRAPHONE'S MOTOR"};

#define ENG_UI_ROW(c, mk, h) {c, sizeof c / sizeof c[0], mk, h}
static const engui_t ENG_UI[] = {           /* by params.c TYPE_ENGINE */
    ENG_UI_ROW(ENG_RHODES_C, 15, ENG_RHODES_HINT),
    ENG_UI_ROW(ENG_BARS_C, 28, ENG_BARS_HINT),
};
_Static_assert(sizeof ENG_RHODES_HINT / sizeof ENG_RHODES_HINT[0] == (sizeof ENG_RHODES_C / sizeof ENG_RHODES_C[0] + 3u) / 4u &&
               sizeof ENG_BARS_HINT / sizeof ENG_BARS_HINT[0] == (sizeof ENG_BARS_C / sizeof ENG_BARS_C[0] + 3u) / 4u,
               "one hint per VOICE page");
#define ENG_VP_MAX 4u                        /* VOICE pages an engine may have (ui.c's page walk) */
