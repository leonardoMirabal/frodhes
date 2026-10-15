/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes presets (IMPLEMENTATION-v3 §10.6, §12): 12 factory presets (const) and 16 user presets in a bank of two
 * flash sectors written alternately (A 0x97000, B 0x98000): erase the older sector, write the records, the header
 * last; load picks the valid copy with the higher sequence number, so a cut-off save never loses the bank.
 *
 *   PRESETS encoder   browse F01..F12, U01..U16; loads 400 ms after the last detent (the edit goes to undo)
 *   SAVE              open the save mode: PRESETS / K1 pick the slot, SAVE again saves (when quiet), HOME cancels
 *   SAVE held 700 ms  undo / redo the last load
 *   MIDI PC           0..11 -> F01..F12, 16..31 -> U01..U16 */

#define PR_NF 12u
#define PR_NU 16u
_Static_assert(PR_NF == PR_NF_UI && PR_NF + PR_NU == PR_NALL_UI, "ui.c preset counts");
#define PR_BANK_A 0x97000u
#define PR_BANK_B 0x98000u
#define PR_MAGIC 0x42505246u                    /* "FRPB" */

typedef struct { uint8_t ver, np, used, rsv; char name[12]; uint8_t p[P_SLOTS]; } preset_t;   /* 0.4 on */
_Static_assert(sizeof(preset_t) == 64, "preset record is 64 B");
typedef struct { uint8_t ver, np, used, rsv; char name[12]; uint8_t p[32]; } preset_v1_t;     /* 0.1 .. 0.3 */
_Static_assert(sizeof(preset_v1_t) == 48, "old preset record is 48 B");
typedef struct { uint32_t magic, seq, len, crc; } bank_hdr_t;

static preset_t pr_user[PR_NU];
static uint32_t pr_seq;

/* factory presets: (param id, value) pairs over the defaults, 0xFF ends (values ESTIMATE, tuned by ear) */
static const char *const PR_FNAME[PR_NF] = {"STAGE 77", "70S PHASER", "SUITCASE", "80S BALLAD", "SLAPBACK", "BRIGHT CHO",
                                            "FUNK WAH", "BARK", "TAPE ECHO", "DREAM", "RADIO", "AM TREMOLO"};
static const uint8_t PR_FDATA[PR_NF][32] = {
    {P_TYPE, 0, P_DRIVE, 13, P_RV_DEPTH, 25, 0xFF},
    {P_TYPE, 0, P_DRIVE, 25, P_PH_SW, 1, P_PH_DEPTH, 83, P_PH_SPEED, 38, P_PH_FDBK, 50, P_RV_DEPTH, 32, 0xFF},
    {P_TYPE, 1, P_DRIVE, 13, P_TW_SW, 1, P_TW_DEPTH, 64, P_TW_RATE, 64, P_TW_MODE, 0, P_RV_DEPTH, 25, 0xFF},
    {P_TYPE, 2, P_DRIVE, 0, P_CP_SW, 1, P_CP_DEPTH, 64, P_CP_SPEED, 50, P_DL_SW, 1, P_DL_DEPTH, 38, P_DL_TIME, 85,
     P_RV_DEPTH, 64, P_BELL, 80, 0xFF},
    {P_TYPE, 2, P_DRIVE, 13, P_DL_SW, 2, P_DL_DEPTH, 64, P_DL_TIME, 19, P_DL_FDBK, 60, P_RV_DEPTH, 13, 0xFF},
    {P_TYPE, 3, P_DRIVE, 32, P_CP_SW, 1, P_CP_DEPTH, 32, P_CP_SPEED, 38, P_DL_SW, 1, P_DL_DEPTH, 19, P_DL_TIME, 19,
     P_RV_DEPTH, 25, P_TONE, 80, 0xFF},
    {P_TYPE, 0, P_DRIVE, 51, P_TW_SW, 2, P_TW_DEPTH, 89, P_TW_RATE, 64, P_RV_DEPTH, 13, P_BARK, 80, P_CMP_SW, 1,
     P_CMP_AMT, 80, P_CMP_ATK, 30, 0xFF},
    {P_TYPE, 0, P_DRIVE, 76, P_RV_DEPTH, 13, P_BARK, 100, P_HAMMER, 90, P_CMP_SW, 1, P_CMP_AMT, 64, 0xFF},
    {P_TYPE, 1, P_DRIVE, 19, P_DL_SW, 2, P_DL_DEPTH, 57, P_DL_TIME, 80, P_DL_FDBK, 100, P_RV_DEPTH, 38, 0xFF},
    {P_TYPE, 2, P_DRIVE, 0, P_TW_SW, 1, P_TW_DEPTH, 38, P_PH_SW, 1, P_PH_DEPTH, 51, P_PH_SPEED, 25, P_DL_SW, 1,
     P_DL_DEPTH, 51, P_DL_TIME, 100, P_RV_DEPTH, 89, P_DECAY, 80, 0xFF},
    {P_TYPE, 3, P_DRIVE, 115, P_RV_DEPTH, 0, P_TONE, 94, 0xFF},
    {P_TYPE, 0, P_DRIVE, 13, P_TW_SW, 1, P_TW_DEPTH, 70, P_TW_RATE, 100, P_TW_MODE, 1, P_TW_SHAPE, 90, P_RV_DEPTH, 25, 0xFF},
};

/* ---- bank storage (host: two RAM sectors) */
#ifdef DINGHY_HOST
static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu, k;
    while (n--) { c ^= *p++; for (k = 0; k < 8u; k++) c = c & 1u ? (c >> 1) ^ 0xEDB88320u : c >> 1; }
    return ~c;
}
static uint8_t host_bank[2][4096];
static int bank_read(uint32_t at, void *p, uint32_t n) { memcpy(p, &host_bank[at == PR_BANK_B][0], n); return 0; }
static int bank_erase(uint32_t at) { memset(&host_bank[at == PR_BANK_B][0], 0xFF, 4096); return 0; }
static int bank_write(uint32_t at, uint32_t off, const void *p, uint32_t n) { memcpy(&host_bank[at == PR_BANK_B][off], p, n); return 0; }
#else
static int bank_read(uint32_t at, void *p, uint32_t n) { return flash_ok ? st_read(at, p, n) : -1; }
static int bank_erase(uint32_t at) { uint32_t took; return fl_erase4k(at, &took); }
static int bank_write(uint32_t at, uint32_t off, const void *p, uint32_t n) { return fl_write(at + off, p, n); }
#endif

static uint8_t bank_buf[sizeof(bank_hdr_t) + sizeof pr_user];
static int bank_valid(uint32_t at, bank_hdr_t *h)          /* the records: 0.4 (64 B) or 0.1 .. 0.3 (48 B) */
{
    if (bank_read(at, h, sizeof *h) || h->magic != PR_MAGIC) return 0;
    if (h->len != sizeof pr_user && h->len != PR_NU * sizeof(preset_v1_t)) return 0;
    if (bank_read(at, bank_buf, sizeof(bank_hdr_t) + h->len)) return 0;
    return crc32(bank_buf + sizeof(bank_hdr_t), h->len) == h->crc;
}

static void presets_load_bank(void)
{
    bank_hdr_t ha, hb;
    int va = bank_valid(PR_BANK_A, &ha), vb = bank_valid(PR_BANK_B, &hb);
    uint32_t i;
    for (i = 0; i < PR_NU; i++) pr_user[i].used = 0;
    if (!va && !vb) return;
    {
        uint32_t at = va && (!vb || (int32_t)(ha.seq - hb.seq) > 0) ? PR_BANK_A : PR_BANK_B;
        bank_hdr_t h;
        if (!bank_valid(at, &h)) return;                      /* (re-reads it into bank_buf) */
        if (h.len == sizeof pr_user) memcpy(pr_user, bank_buf + sizeof(bank_hdr_t), sizeof pr_user);
        else {
            const preset_v1_t *o = (const preset_v1_t *)(bank_buf + sizeof(bank_hdr_t));
            for (i = 0; i < PR_NU; i++) {
                preset_t *u = &pr_user[i];
                uint32_t j;
                u->ver = 2; u->used = o[i].used; u->rsv = 0;
                memcpy(u->name, o[i].name, sizeof u->name);
                for (j = 0; j < P_SLOTS; j++) u->p[j] = j < 32u && j < o[i].np ? o[i].p[j] : j < P_COUNT ? PARAMS[j].def : 0;
                params_split_phaser(u->p, P_SLOTS);
                u->np = P_COUNT;
            }
        }
        pr_seq = at == PR_BANK_A ? ha.seq : hb.seq;
    }
}

static int presets_save_bank(void)                 /* main loop, only when quiet (the erase stalls the audio) */
{
    static uint8_t buf[sizeof(bank_hdr_t) + sizeof pr_user];
    bank_hdr_t h, cur;
    uint32_t at = PR_BANK_A;
    if (bank_valid(PR_BANK_A, &cur) && (!bank_valid(PR_BANK_B, &h) || (int32_t)(cur.seq - h.seq) > 0)) at = PR_BANK_B;
    h.magic = PR_MAGIC; h.seq = ++pr_seq; h.len = sizeof pr_user;
    h.crc = crc32((const uint8_t *)pr_user, sizeof pr_user);
    if (bank_erase(at)) return -1;
    if (bank_write(at, sizeof h, pr_user, sizeof pr_user)) return -1;
    if (bank_write(at, 0, &h, sizeof h)) return -1;       /* the header last */
    if (bank_read(at, buf, sizeof buf) || memcmp(buf + sizeof h, pr_user, sizeof pr_user)) return -1;
    return 0;
}

/* ---- loading, browsing, saving */
static uint8_t pr_cand = 255, pr_save_mode, pr_save_slot, pr_save_pending;
static uint32_t pr_cand_t;

static void preset_name(uint32_t idx, char *s)          /* "F03 SUITCASE" / "U05 ST77 TRM" */
{
    uint32_t i = 0, n = idx < PR_NF ? idx + 1u : idx - PR_NF + 1u;
    const char *nm = idx < PR_NF ? PR_FNAME[idx] : pr_user[idx - PR_NF].used ? pr_user[idx - PR_NF].name : "----";
    s[i++] = idx < PR_NF ? 'F' : 'U';
    s[i++] = (char)('0' + n / 10u); s[i++] = (char)('0' + n % 10u); s[i++] = ' ';
    while (*nm && i < 16u) s[i++] = *nm++;
    while (i > 4u && s[i - 1u] == ' ') i--;
    s[i] = 0;
}

static void preset_load(uint32_t idx)
{
    uint8_t p[P_SLOTS];
    uint32_t i;
    if (idx >= PR_NF + PR_NU) return;
    if (idx >= PR_NF && !pr_user[idx - PR_NF].used) return;
    for (i = 0; i < P_COUNT; i++) p[i] = PARAMS[i].def;
    if (idx < PR_NF) {
        const uint8_t *d = PR_FDATA[idx];
        for (i = 0; i < 32u && d[i] != 0xFF; i += 2u) p[d[i]] = d[i + 1u];
    } else {
        const preset_t *u = &pr_user[idx - PR_NF];
        for (i = 0; i < P_COUNT && i < u->np; i++) p[i] = u->p[i] <= PARAMS[i].hi ? u->p[i] : PARAMS[i].def;
    }
    for (i = 0; i < P_COUNT; i++) UV(i) = PV(i);
    set.undo_valid = 1;
    for (i = 0; i < P_COUNT; i++) param_set(i, p[i]);
    set.preset = (uint8_t)idx;
    tempo_sync_publish();
    set_dirty = 1; set_t_change = fm1_ms;
    ui_draw_page_change();
    {
        char s[20];
        preset_name(idx, s);
        ui_say(s);
    }
}

static void preset_undo(void)
{
    uint8_t t[P_SLOTS];
    uint32_t i;
    if (!set.undo_valid) return;
    for (i = 0; i < P_COUNT; i++) t[i] = PV(i);
    for (i = 0; i < P_COUNT; i++) param_set(i, UV(i));
    for (i = 0; i < P_COUNT; i++) UV(i) = t[i];
    tempo_sync_publish();
    ui_draw_page_change();
    ui_say("UNDO");
}

static void preset_autoname(char *n)                     /* TYPE + the effects on: "ST77 TRM PHA DD" */
{
    const char *parts[5];
    uint32_t i = 0, k, np = 0;
    parts[np++] = N_TYPE[PV(P_TYPE)];
    if (PV(P_TW_SW)) parts[np++] = N_TW[PV(P_TW_SW)];
    if (PV(P_CP_SW)) parts[np++] = "CHO";
    if (PV(P_PH_SW) && np < 4u) parts[np++] = "PHA";
    if (PV(P_DL_SW) && np < 5u) parts[np++] = PV(P_DL_SW) == 1 ? "DD" : "AD";
    for (k = 0; k < np; k++) {
        const char *p = parts[k];
        if (k && i < 12u) n[i++] = ' ';
        while (*p && i < 12u) n[i++] = *p++;
    }
    while (i < 12u) n[i++] = ' ';
}

static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel)
{
    static uint32_t save_t;
    char s[20];
    uint32_t i;
    if (b >> BTN_SAVE & 1u) save_t = fm1_ms;
    if (!pr_save_mode && fm1_in.buttons >> BTN_SAVE & 1u && save_t && fm1_ms - save_t >= 700u) {
        save_t = 0; preset_undo(); return;
    }
    if (rel >> BTN_SAVE & 1u && save_t) {               /* a tap: open the save mode, or (in it) save */
        save_t = 0;
        if (!pr_save_mode) {
            pr_save_mode = 1;
            pr_save_slot = set.preset >= PR_NF && set.preset < PR_NF + PR_NU ? (uint8_t)(set.preset - PR_NF) : 0;
            return;
        }
        {
            preset_t *u = &pr_user[pr_save_slot];
            u->ver = 2; u->np = P_COUNT; u->used = 1; u->rsv = 0;
            preset_autoname(u->name);
            for (i = 0; i < P_SLOTS; i++) u->p[i] = i < P_COUNT ? PV(i) : 0;
            set.preset = (uint8_t)(PR_NF + pr_save_slot);
            set_dirty = 1; set_t_change = fm1_ms;
            pr_save_mode = 0;
            pr_save_pending = 1;
            ui_mark(E_HEADER);
            ui_say("SAVING...");
        }
        return;
    }
    if (pr_save_mode) {
        int32_t d = d_presets + d_k1;
        if (d) {
            int32_t v = pr_save_slot + (d > 0 ? 1 : -1);
            pr_save_slot = (uint8_t)(v < 0 ? 0 : v > (int32_t)PR_NU - 1 ? PR_NU - 1u : (uint32_t)v);
        }
        if ((b >> BTN_HOME | b >> BTN_OCT_DOWN) & 1u) { pr_save_mode = 0; ui_say("NOT SAVED"); }
        return;
    }
    if (d_presets) {
        int32_t v = (pr_cand == 255 ? (set.preset < PR_NF + PR_NU ? set.preset : 0) : pr_cand) + (d_presets > 0 ? 1 : -1);
        while (v >= (int32_t)PR_NF && v < (int32_t)(PR_NF + PR_NU) && !pr_user[v - PR_NF].used) v += d_presets > 0 ? 1 : -1;
        if (v < 0) v = 0;
        if (v >= (int32_t)(PR_NF + PR_NU)) v = (int32_t)PR_NF - 1;
        pr_cand = (uint8_t)v; pr_cand_t = fm1_ms;
        preset_name((uint32_t)v, s);
        ui_say(s);
    }
    if (pr_cand != 255 && fm1_ms - pr_cand_t >= 400u) { preset_load(pr_cand); pr_cand = 255; }
}

static void presets_save_poll(void)
{
    if (pr_save_pending && rh_quiet() && fx_quiet()) {
        pr_save_pending = 0;
        ui_say(presets_save_bank() ? "SAVE FAILED" : "SAVED");
    }
}
