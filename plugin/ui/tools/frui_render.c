/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: renders the window to PNG files, headless (B-M2a). The pictures are the plugin's own drawing
 * code (frui.c) on the app's real presets (presets.c's factory records) and value texts (params.c param_text, the
 * same arithmetic over the same tables, firmware/gen/fx_tables.h); in the plugin the text comes from the engine itself.
 *
 *   frui_render <font.ttf> <out dir>      writes every state listed in SCENES at its sizes */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <zlib.h>
#include "../frui.h"
#include "../frui_text.h"
#define DEF frui_param_def
#define text_cb frui_text_default

/* ---- presets.c's factory records used in the pictures (id / value pairs, 0xFF ends), and their names */
static const uint8_t PR_F05[] = {FRP_TYPE, 1, FRP_NOISE, 44, FRP_DRIVE, 20, FRP_TW_SW, 1, FRP_TW_MODE, 0, FRP_TW_DEPTH, 72,
    FRP_TW_RATE, 88, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8,
    FRP_RV_DEPTH, 24, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 70, FRP_LEVEL, 46, 0xFF};
static const uint8_t PR_F06[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 40, FRP_CMP_RATIO, 2, FRP_CMP_ATK, 70,
    FRP_CMP_GAIN, 16, FRP_DRIVE, 64, FRP_DRV_SW, 2, FRP_DRV_TONE, 72, FRP_BARK, 96, FRP_HAMMER, 80, FRP_DL_SW, 3,
    FRP_DL_DEPTH, 12, FRP_DL_SYNC, 8, FRP_DL_FDBK, 40, FRP_DL_TONE, 56, FRP_RV_DEPTH, 18, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 80,
    FRP_LEVEL, 45, 0xFF};
static const uint8_t PR_F07[] = {FRP_TYPE, 0, FRP_TONE, 56, FRP_BELL, 76, FRP_NOISE, 40, FRP_CMP_SW, 1, FRP_CMP_AMT, 50,
    FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_CP_SW, 1, FRP_CP_DEPTH, 56, FRP_CP_SPEED, 40,
    FRP_DL_SW, 1, FRP_DL_DEPTH, 12, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30, FRP_DL_TONE, 50, FRP_RV_DEPTH, 48, FRP_RV_TYPE, 2,
    FRP_RV_DECAY, 84, FRP_RV_PREDLY, 40, FRP_RV_LOCUT, 70, FRP_LEVEL, 53, 0xFF};
static const uint8_t PR_F08[] = {FRP_TYPE, 3, FRP_NOISE, 44, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80,
    FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_CP_SW, 1, FRP_CP_DEPTH, 70, FRP_CP_SPEED, 56, FRP_DL_SW, 1, FRP_DL_DEPTH, 18,
    FRP_DL_SYNC, 8, FRP_DL_FDBK, 24, FRP_DL_TONE, 76, FRP_RV_DEPTH, 30, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 80, FRP_LEVEL, 68, 0xFF};
static const uint8_t PR_F09[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_DRIVE, 34, FRP_DRV_SW, 5, FRP_BARK, 76, FRP_CMP_SW, 1,
    FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_PH_SW, 1, FRP_PH_DEPTH, 85,
    FRP_PH_SPEED, 30, FRP_PH_FDBK, 64, FRP_DL_SW, 2, FRP_DL_DEPTH, 12, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30, FRP_DL_TONE, 50,
    FRP_RV_DEPTH, 22, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 70, FRP_LEVEL, 60, 0xFF};
static const uint8_t PR_F10[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 56, FRP_CMP_RATIO, 3, FRP_CMP_ATK, 20,
    FRP_CMP_REL, 40, FRP_CMP_GAIN, 26, FRP_DRIVE, 44, FRP_DRV_SW, 3, FRP_TW_SW, 2, FRP_TW_DEPTH, 92, FRP_TW_RATE, 72,
    FRP_BARK, 84, FRP_RV_DEPTH, 14, FRP_RV_TYPE, 1, FRP_RV_LOCUT, 80, FRP_LEVEL, 42, 0xFF};
static const uint8_t PR_F11[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80,
    FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_DRIVE, 53, FRP_DRV_SW, 4, FRP_DRV_TONE, 60, FRP_FL_TYPE, 1, FRP_FL_FREQ, 76,
    FRP_FL_RESO, 88, FRP_FL_LFO, 64, FRP_LFO_SHAPE, 1, FRP_LFO_SYNC, 13, FRP_DL_SW, 1, FRP_DL_DEPTH, 14, FRP_DL_SYNC, 9,
    FRP_DL_FDBK, 40, FRP_DL_TONE, 60, FRP_RV_DEPTH, 22, FRP_RV_TYPE, 1, FRP_RV_LOCUT, 80, FRP_LEVEL, 18, 0xFF};
static const uint8_t PR_F13[] = {FRP_TYPE, 4, FRP_TONE, 64, FRP_BARK, 52, FRP_BELL, 70, FRP_HAMMER, 64, FRP_DECAY, 64,
    FRP_RELEASE, 64, FRP_NOISE, 40, FRP_MOT_ON, 1, FRP_MOT_DEPTH, 90, FRP_MOT_RATE, 66, FRP_MOT_SHAPE, 0, FRP_CMP_SW, 1,
    FRP_CMP_AMT, 46, FRP_CMP_RATIO, 0, FRP_CMP_ATK, 92, FRP_CMP_REL, 64, FRP_CMP_GAIN, 6, FRP_RV_DEPTH, 20, FRP_RV_TYPE, 1,
    FRP_RV_PREDLY, 20, FRP_RV_LOCUT, 70, FRP_LEVEL, 55, 0xFF};
static const uint8_t PR_F16[] = {FRP_TYPE, 5, FRP_TONE, 60, FRP_BARK, 80, FRP_BELL, 64, FRP_HAMMER, 110, FRP_DECAY, 64,
    FRP_RELEASE, 0, FRP_NOISE, 40, FRP_CMP_SW, 1, FRP_CMP_AMT, 46, FRP_CMP_RATIO, 0, FRP_CMP_ATK, 92, FRP_CMP_REL, 64,
    FRP_CMP_GAIN, 6, FRP_CP_SW, 1, FRP_CP_DEPTH, 40, FRP_CP_SPEED, 44, FRP_DL_SW, 1, FRP_DL_DEPTH, 22, FRP_DL_SYNC, 8,
    FRP_DL_FDBK, 26, FRP_DL_TONE, 50, FRP_RV_DEPTH, 38, FRP_RV_TYPE, 1, FRP_RV_DECAY, 64, FRP_RV_PREDLY, 24,
    FRP_RV_LOCUT, 80, FRP_LEVEL, 67, 0xFF};
static const uint8_t PR_F18[] = {FRP_TYPE, 2, FRP_DECAY, 84, FRP_NOISE, 30, FRP_CMP_SW, 1, FRP_CMP_AMT, 46, FRP_CMP_RATIO, 1,
    FRP_CMP_ATK, 90, FRP_CMP_REL, 80, FRP_CMP_GAIN, 8, FRP_TW_SW, 1, FRP_TW_MODE, 0, FRP_TW_DEPTH, 36, FRP_TW_RATE, 30,
    FRP_CP_SW, 1, FRP_CP_DEPTH, 60, FRP_CP_SPEED, 30, FRP_DL_SW, 2, FRP_DL_DEPTH, 21, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30,
    FRP_RV_DEPTH, 76, FRP_RV_TYPE, 1, FRP_RV_DECAY, 104, FRP_RV_PREDLY, 60, FRP_RV_TONE, 56, FRP_RV_MOD, 84,
    FRP_RV_LOCUT, 80, FRP_LEVEL, 55, 0xFF};

/* the factory presets' names and instruments (F01..F57: presets.c, move/move_presets.h), for the PRESETS sheet */
static const struct { const char *name; int type; } FACT[57] = {
    {"STAGE 77", 0}, {"SUITCASE", 1}, {"EARLY FELT", 2}, {"BELL DYNO", 3}, {"SUIT TREM", 1}, {"DRIVEN 77", 0},
    {"BALLAD", 0}, {"DYNO 80S", 3}, {"70S PHASER", 0}, {"FUNK WAH", 0}, {"RESO SWEEP", 0}, {"AUTO WAH", 0},
    {"VIBES", 4}, {"VIBES ECHO", 4}, {"MARIMBA", 5}, {"WOOD ECHO", 5}, {"TAPE ECHO", 0}, {"DREAM", 2}, {"RADIO", 0},
    {"STAGE77 ECHO", 0}, {"STAGE77 TAPE", 0}, {"SUITCASE DRY", 1}, {"SUITCASE AMP", 1}, {"FELT HALL", 2},
    {"FELT ECHO", 2}, {"BELL SOFT", 3}, {"BELL ECHO", 3}, {"TREM ECHO", 1}, {"TREM DEEP", 1}, {"DRIVEN77 LVL", 0},
    {"DRIVEN BODY", 0}, {"BALLAD TIGHT", 0}, {"BALLAD DARK", 0}, {"DYNO80S LVL", 3}, {"DYNO80S SOFT", 3},
    {"PHASER LVL", 0}, {"PHASER SLOW", 0}, {"FUNK WAH LVL", 0}, {"FUNK PLUCK", 0}, {"RESO SPACE", 0},
    {"RESO SMOOTH", 0}, {"AUTOWAH ECHO", 0}, {"AUTOWAH TAPE", 0}, {"VIBES HALL", 4}, {"VIBES MOTOR", 4},
    {"VIBES CLEAN", 4}, {"VIBES DRIFT", 4}, {"MARIMBA HALL", 5}, {"MARIMBA ECHO", 5}, {"WOOD PLAIN", 5},
    {"WOOD LIGHT", 5}, {"TAPE WIDE", 0}, {"TAPE DARK", 0}, {"DREAM CLEAR", 2}, {"DREAM DEEP", 2}, {"RADIO LVL", 0},
    {"RADIO LOW", 0},
};
/* the list: the factory presets, then nu user presets (named from users[], instruments from utype[]) */
static void plist(frui_model *m, int nu, const char *const *users, const int *utype)
{
    int i;
    for (i = 0; i < 57 + nu && i < FRUI_PL_MAX; i++) {
        int u = i >= 57;
        snprintf(m->pl[i].tag, sizeof m->pl[i].tag, "%c%02d", u ? 'U' : 'F', u ? i - 56 : i + 1);
        snprintf(m->pl[i].name, sizeof m->pl[i].name, "%s", u ? users[(i - 57) % 4] : FACT[i].name);
        m->pl[i].type = (uint8_t)(u ? utype[(i - 57) % 4] : FACT[i].type);
        m->pl[i].user = (uint8_t)u;
    }
    m->pl_n = i;
}
static void load(frui_model *m, const uint8_t *rec, const char *tag, const char *name)
{
    int i;
    memset(m, 0, sizeof *m);
    for (i = 0; i < FRP_COUNT; i++) m->p[i] = DEF[i];
    for (; *rec != 0xFF; rec += 2) m->p[rec[0]] = rec[1];
    snprintf(m->preset_tag, sizeof m->preset_tag, "%s", tag);
    snprintf(m->preset_name, sizeof m->preset_name, "%s", name);
    m->focus = m->hover = m->menu = m->ctx_knob = m->learning = -1;
    m->fixed_vel = 100; m->bpm10 = 1200; m->host_bpm10 = 1200; m->sync_div = 6;
    m->out_db[0] = -15.5f; m->out_db[1] = -16.8f; m->out_peak_db[0] = -7.2f; m->out_peak_db[1] = -8.1f;
    m->gr_db = m->p[FRP_CMP_SW] ? 2.4f : 0.f;
}
static void chord(frui_model *m, const int *n, int k) { while (k--) m->notes[n[k]] = 96; }

/* ---- PNG (8-bit RGB, zlib) */
static uint32_t crc_tab[256];
static uint32_t crc(uint32_t c, const uint8_t *p, size_t n)
{
    size_t i;
    if (!crc_tab[1]) {
        uint32_t k, j, v;
        for (k = 0; k < 256; k++) { v = k; for (j = 0; j < 8; j++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1; crc_tab[k] = v; }
    }
    for (i = 0; i < n; i++) c = crc_tab[(c ^ p[i]) & 255u] ^ (c >> 8);
    return c;
}
static void chunk(FILE *f, const char *t, const uint8_t *d, uint32_t n)
{
    uint8_t b[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n};
    uint32_t c;
    fwrite(b, 1, 4, f); fwrite(t, 1, 4, f); if (n) fwrite(d, 1, n, f);
    c = crc(crc(0xFFFFFFFFu, (const uint8_t *)t, 4), d, n) ^ 0xFFFFFFFFu;
    b[0] = (uint8_t)(c >> 24); b[1] = (uint8_t)(c >> 16); b[2] = (uint8_t)(c >> 8); b[3] = (uint8_t)c;
    fwrite(b, 1, 4, f);
}
static int write_png(const char *path, const uint32_t *px, int w, int h)
{
    size_t rl = (size_t)w * 3 + 1, raw_n = rl * (size_t)h;
    uint8_t *raw = malloc(raw_n), hdr[13];
    uLongf zn = compressBound((uLong)raw_n);
    uint8_t *z = malloc(zn);
    FILE *f;
    int x, y;
    for (y = 0; y < h; y++) {
        uint8_t *r = raw + rl * (size_t)y;
        r[0] = 0;
        for (x = 0; x < w; x++) {
            uint32_t c = px[(size_t)y * (size_t)w + (size_t)x];
            r[1 + 3 * x] = (uint8_t)(c >> 16); r[2 + 3 * x] = (uint8_t)(c >> 8); r[3 + 3 * x] = (uint8_t)c;
        }
    }
    compress2(z, &zn, raw, (uLong)raw_n, 9);
    f = fopen(path, "wb");
    if (!f) { free(raw); free(z); return -1; }
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    hdr[0] = (uint8_t)(w >> 24); hdr[1] = (uint8_t)(w >> 16); hdr[2] = (uint8_t)(w >> 8); hdr[3] = (uint8_t)w;
    hdr[4] = (uint8_t)(h >> 24); hdr[5] = (uint8_t)(h >> 16); hdr[6] = (uint8_t)(h >> 8); hdr[7] = (uint8_t)h;
    hdr[8] = 8; hdr[9] = 2; hdr[10] = hdr[11] = hdr[12] = 0;
    chunk(f, "IHDR", hdr, 13);
    chunk(f, "IDAT", z, (uint32_t)zn);
    chunk(f, "IEND", 0, 0);
    fclose(f);
    free(raw); free(z);
    return 0;
}

static frui_font *font, *dfont;
static const char *outdir;
static int look = FRUI_LOOK_A3;

/* the layout check: every widget's edges on the 4 px grid, every knob on a column centre (or the half-column of a
 * row of three) and on its tier's row, every control row one height; prints what is off by more than 1 px at 1x */
static int check_layout(const frui *u, const char *name)
{
    int i, n = frui_widget_count(u), bad = 0;
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        float e[4] = {g->x, g->y, g->x + g->w, g->y + g->h};
        static const char *const en[4] = {"left", "top", "right", "bottom"};
        int k;
        if (g->kind == FRW_TAB_L || g->kind == FRW_TAB_R || g->kind == FRW_CTX || g->kind == FRW_ITEM) continue;   /* (the knob menu: a pop-up) */      /* text-width tabs: checked by their baseline */
        if (g->kind == FRW_KNOB && g->id != FRP_LEVEL) goto knob;          /* a knob: its centre, below */
        for (k = 0; k < 4; k++) {
            float d = e[k] - 4.f * roundf(e[k] / 4.f);
            if (fabsf(d) > 1.f) { printf("  %s: widget kind %d id %d %s edge %.2f off the 4 px grid by %.2f\n", name, g->kind, g->id, en[k], e[k], d); bad++; }
        }
    knob:
        if (g->kind == FRW_KNOB && g->id != FRP_LEVEL) {
            float cx = g->x + g->w * 0.5f, cy = g->y + 52.f, best = 1e9f;
            int j;
            for (j = 0; j < 2 * 8 - 1; j++) {                 /* column centres and the half-columns between them */
                float b2 = fabsf(cx - ((j < 7 ? 28.f : 500.f) + 54.f * (float)(j % 7 + 1)));
                if (b2 < best) best = b2;
            }
            if (best > 1.f) { printf("  %s: knob %d centre x %.2f off its column by %.2f\n", name, g->id, cx, best); bad++; }
            if (fabsf(cy - 196.f) > 1.f && fabsf(cy - 424.f) > 1.f) { printf("  %s: knob %d centre y %.2f off its row\n", name, g->id, cy); bad++; }
        }
        if ((g->kind == FRW_SEG || g->kind == FRW_MENU || g->kind == FRW_PAGER) && g->id != FRP_TYPE && g->id < FRS_CHANNEL &&
            g->id != FRUI_PL_PAGER &&
            (fabsf(g->y - 336.f) > 1.f || fabsf(g->h - 28.f) > 1.f)) {
            printf("  %s: control %d row y %.2f h %.2f (want 336, 28)\n", name, g->id, g->y, g->h); bad++;
        }
    }
    /* the text: each knob's label and value centred on the knob, one baseline per row; the tabs on one baseline per
     * panel; the titles on the padding's left edge; the halves' captions on their centres */
    {
        int na = frui_anchor_count(u), j;
        float tab_y[2] = {-1.f, -1.f};
        for (i = 0; i < na; i++) {
            const frui_anchor *a = frui_anchor_get(u, i);
            if (a->what == FRA_LABEL || a->what == FRA_VALUE) {
                for (j = 0; j < n; j++) {
                    const frui_widget *g = frui_widget_get(u, j);
                    if (g->kind == FRW_KNOB && g->id == a->id) {
                        float cx = g->x + g->w * 0.5f, cy = g->y + 52.f;
                        float want = a->what == FRA_LABEL ? cy - 39.f : cy + 48.f;
                        if (fabsf(a->x - cx) > 1.f) { printf("  %s: knob %d %s centred at %.2f, knob at %.2f\n", name, a->id, a->what ? "value" : "label", a->x, cx); bad++; }
                        if (fabsf(a->y - want) > 1.f) { printf("  %s: knob %d %s baseline %.2f, want %.2f\n", name, a->id, a->what ? "value" : "label", a->y, want); bad++; }
                    }
                }
            } else if (a->what == FRA_TAB) {
                int side = a->x > 480.f;
                if (tab_y[side] < 0.f) tab_y[side] = a->y;
                else if (fabsf(a->y - tab_y[side]) > 0.01f) { printf("  %s: tab %d baseline %.2f, its row %.2f\n", name, a->id, a->y, tab_y[side]); bad++; }
            } else if (a->what == FRA_TITLE) {
                if (fabsf(a->x - 28.f) > 1.f) { printf("  %s: title %d at x %.2f, want 28\n", name, a->id, a->x); bad++; }
            } else if (a->what == FRA_ROWLABEL) {
                if (fabsf(a->x - 28.f) > 1.f && fabsf(a->x - 500.f) > 1.f) { printf("  %s: row label at x %.2f, want 28 or 500\n", name, a->x); bad++; }
            } else if (a->what == FRA_CAPTION) {
                if (fabsf(a->x - (a->id ? 716.f : 244.f)) > 1.f) { printf("  %s: caption %d at %.2f\n", name, a->id, a->x); bad++; }
            }
        }
        if (tab_y[0] >= 0.f && tab_y[1] >= 0.f && fabsf(tab_y[0] - tab_y[1]) > 0.01f) { printf("  %s: the panels' tabs on different baselines\n", name); bad++; }
    }
    /* SETTINGS: every control on a row (148 + 44 k, h 28) or in the CC map's header row */
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        if (g->id >= FRS_CHANNEL && (g->kind == FRW_SEG || g->kind == FRW_MENU || g->kind == FRW_FADER)) {
            float k = (g->y - 148.f) / 44.f;
            if (fabsf(k - roundf(k)) * 44.f > 1.f || fabsf(g->h - 28.f) > 0.5f) { printf("  %s: setting %d off its row (y %.2f h %.2f)\n", name, g->id, g->y, g->h); bad++; }
            if (g->id != FRS_BPM && fabsf(g->x - 136.f) > 1.f && fabsf(g->x - 608.f) > 1.f && fabsf(g->x - 832.f) > 1.f) {
                printf("  %s: setting %d at x %.2f, not on a column\n", name, g->id, g->x); bad++;
            }
        }
    }
    /* the rounded shapes: corners that nest (both gaps under the outer radius) concentric, the same gap on both sides,
     * the inner radius the outer less the gap; each shape's edges on the 4 px grid */
    {
        int ns = frui_shell_count(u), a, b, q;
        for (a = 0; a < ns; a++) {
            const frui_shell *o = frui_shell_get(u, a);
            float e[4] = {o->x, o->y, o->x + o->w, o->y + o->h};
            for (q = 0; q < 4; q++)
                if (fabsf(e[q] - 4.f * roundf(e[q] / 4.f)) > 0.01f) { printf("  %s: shape %d edge %.2f off the 4 px grid\n", name, a, e[q]); bad++; }
            for (b = 0; b < ns; b++) {
                const frui_shell *in = frui_shell_get(u, b);
                if (a == b || in->w * in->h >= o->w * o->h || in->x < o->x - 0.01f || in->y < o->y - 0.01f ||
                    in->x + in->w > o->x + o->w + 0.01f || in->y + in->h > o->y + o->h + 0.01f) continue;
                for (q = 0; q < 4; q++) {
                    float dx = q == 0 || q == 3 ? in->x - o->x : o->x + o->w - in->x - in->w;
                    float dy = q < 2 ? in->y - o->y : o->y + o->h - in->y - in->h;
                    if (dx >= o->r || dy >= o->r) continue;            /* not nested in this corner */
                    if (fabsf(dx - dy) > 0.01f || fabsf(in->r - (o->r - dx)) > 0.01f) {
                        printf("  %s: shape %d (r %.1f) in shape %d (r %.1f), corner %d gaps %.1f/%.1f: not concentric\n", name, b,
                               in->r, a, o->r, q, dx, dy);
                        bad++;
                    }
                }
            }
        }
    }
    /* the rail: every control centred on the rail's centre line, one height */
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        float rc = look == FRUI_LOOK_A3 ? 32.f : 28.f;    /* A3: the centre of the metal above the body (y 0 .. 64) */
        if (g->y < 56.f && (fabsf(g->y + g->h * 0.5f - rc) > 0.5f || fabsf(g->h - 32.f) > 0.5f)) {
            printf("  %s: rail widget %d/%d centre %.2f h %.2f (want %.0f, 32)\n", name, g->kind, g->id, g->y + g->h * 0.5f, g->h, rc); bad++;
        }
    }
    return bad;
}

static uint32_t *render_px(const frui_model *m, int pw, int ph, int chk, const char *name)
{
    uint32_t *px = malloc(sizeof(uint32_t) * (size_t)pw * (size_t)ph);
    frui_canvas c;
    frui *u = frui_new(font, text_cb, 0);
    frui_set_look(u, look, dfont);
    frui_canvas_init(&c, px, pw, ph);
    frui_draw(u, m, &c);
    if (chk) {
        int bad = check_layout(u, name);
        printf("layout check %s: %s\n", name, bad ? "OFF-GRID ITEMS ABOVE" : "all on the grid");
    }
    frui_free(u);
    return px;
}
static void render(const frui_model *m, const char *name, int pw, int ph)
{
    char path[1024];
    uint32_t *px = render_px(m, pw, ph, pw == 960 && ph == 600, name);
    snprintf(path, sizeof path, "%s/%s.png", outdir, name);
    if (write_png(path, px, pw, ph)) { fprintf(stderr, "cannot write %s\n", path); exit(1); }
    printf("%s  %dx%d\n", path, pw, ph);
    free(px);
}

/* the looks side by side (A1 rail, A2 plate, A3 framed): five screens each at 1x and 2x, and contact sheets of all of
 * them, one look per column, at 1x and 2x (outdir/directions-a3) */
static void blit(uint32_t *dst, int dw, const uint32_t *src, int sw, int sh, int ox, int oy)
{
    int y;
    for (y = 0; y < sh; y++) memcpy(dst + (size_t)(oy + y) * (size_t)dw + (size_t)ox, src + (size_t)y * (size_t)sw, sizeof(uint32_t) * (size_t)sw);
}
static int directions(const int *maj7, const int *min9, const int *vib)
{
    static const char *const vn[3] = {"A1-rail", "A2-plate", "A3-framed"};
    static const int vl[3] = {FRUI_LOOK_RAIL, FRUI_LOOK_PLATE, FRUI_LOOK_A3};
    static const char *const SN[5] = {"main", "menu-engine", "settings-midi", "knob-learn", "tab-comp-delay"};
    char path[1024], dir[1024];
    int v, k, bad = 0;
    uint32_t *sheet[2];
    frui_model m;
    snprintf(dir, sizeof dir, "%s/directions-a3", outdir);
    for (k = 0; k < 2; k++) {                          /* the contact sheets: three columns (the looks), five rows */
        int sw = (960 * 3 + 64) << k, sh = (600 * 5 + 96) << k, i;
        sheet[k] = malloc(sizeof(uint32_t) * (size_t)sw * (size_t)sh);
        for (i = 0; i < sw * sh; i++) sheet[k][i] = 0xFF2A2A2Cu;
    }
    for (v = 0; v < 3; v++) {
        int s;
        look = vl[v];
        for (s = 0; s < 5; s++) {
            uint32_t *px;
            const char *sn = SN[s];
            if (s == 0) { load(&m, PR_F09, "F09", "70S PHASER"); m.tab_l = FRT_DRIVE; m.tab_r = FRT_PHASER; chord(&m, maj7, 5);
                          m.sustain = 1; m.learn_cc[FRP_DRIVE] = 2; }
            else if (s == 2) { load(&m, PR_F06, "F06", "DRIVEN 77"); m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY;
                               m.settings = 1; m.vel_curve = 0; m.pgm_change = 1; m.tempo_src = 0; m.host_bpm10 = 965;
                               m.sync_all = 0; m.learn_cc[FRP_DRIVE] = 2; m.learn_cc[FRP_FL_FREQ] = 75; m.cc_scroll = 0; }
            else if (s == 3) { load(&m, PR_F11, "F11", "SWEEP"); m.tab_l = FRT_DRIVE; m.tab_r = FRT_FILTER; chord(&m, min9, 3);
                               m.learn_cc[FRP_DRIVE] = 2; m.ctx_knob = FRP_FL_FREQ; m.learning = FRP_FL_FREQ;
                               m.hover = FRUI_WID(FRW_CTX, FRC_LEARN); }
            else if (s == 4) { load(&m, PR_F06, "F06", "DRIVEN 77"); m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f; chord(&m, min9, 5); }
            else { load(&m, PR_F13, "F13", "VIBES"); m.tab_l = FRT_MOTOR; m.tab_r = FRT_REVERB; chord(&m, vib, 5);
                   m.menu = FRUI_WID(FRW_MENU, FRP_TYPE); m.fx_bypass = 1; }
            for (k = 0; k < 2; k++) {
                int pw = 960 << k, ph = 600 << k;
                snprintf(path, sizeof path, "%s-%s@%dx", vn[v], sn, k + 1);
                px = render_px(&m, pw, ph, k == 0, path);
                blit(sheet[k], (960 * 3 + 64) << k, px, pw, ph, (16 + v * (960 + 16)) << k, (16 + s * (600 + 16)) << k);
                snprintf(path, sizeof path, "%s/%s-%s@%dx.png", dir, vn[v], sn, k + 1);
                if (write_png(path, px, pw, ph)) { fprintf(stderr, "cannot write %s\n", path); return 1; }
                printf("%s\n", path);
                free(px);
            }
        }
    }
    for (k = 0; k < 2; k++) {
        snprintf(path, sizeof path, k ? "%s/compare@2x.png" : "%s/compare.png", dir);
        if (write_png(path, sheet[k], (960 * 3 + 64) << k, (600 * 5 + 96) << k)) return 1;
        printf("%s\n", path);
        free(sheet[k]);
    }
    return bad;
}

/* FRUI_MODEL=<file> FRUI_SIZE=<w>x<h>: draws the frui_model saved in the file (the plugin window's own, plugin/tests/
 * gui_shot.m) at that pixel size into <out dir>/model.png: the reference the window's picture is compared with */
static int render_model(const char *path, const char *size)
{
    frui_model m;
    FILE *f = fopen(path, "rb");
    int w = 960, h = 600;
    if (!f || fread(&m, 1, sizeof m, f) != sizeof m || fgetc(f) != EOF) {
        fprintf(stderr, "%s: not a frui_model of %zu bytes\n", path, sizeof m);
        if (f) fclose(f);
        return 1;
    }
    fclose(f);
    if (size && sscanf(size, "%dx%d", &w, &h) != 2) return 2;
    render(&m, "model", w, h);
    return 0;
}

int main(int argc, char **argv)
{
    FILE *f;
    long n;
    uint8_t *d;
    frui_model m;
    static const int maj7[] = {36, 48, 52, 55, 59}, min9[] = {41, 56, 60, 63, 67}, vib[] = {60, 64, 67, 71, 74};
    if (argc < 3) { fprintf(stderr, "usage: frui_render <font.ttf> <out dir> [display.ttf]\n"); return 2; }
    f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot read %s\n", argv[1]); return 1; }
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    d = malloc((size_t)n);
    if (fread(d, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);
    font = frui_font_load(d, (size_t)n);
    if (!font) { fprintf(stderr, "not a TrueType font: %s\n", argv[1]); return 1; }
    outdir = argv[2];
    dfont = 0;
    if (argc > 3) {
        FILE *g = fopen(argv[3], "rb");
        long n2;
        uint8_t *d2;
        if (!g) { fprintf(stderr, "cannot read %s\n", argv[3]); return 1; }
        fseek(g, 0, SEEK_END); n2 = ftell(g); fseek(g, 0, SEEK_SET);
        d2 = malloc((size_t)n2);
        if (fread(d2, 1, (size_t)n2, g) != (size_t)n2) return 1;
        fclose(g);
        dfont = frui_font_load(d2, (size_t)n2);
        if (!dfont) { fprintf(stderr, "not a TrueType font: %s\n", argv[3]); return 1; }
    }
    if (getenv("FRUI_MODEL")) return render_model(getenv("FRUI_MODEL"), getenv("FRUI_SIZE"));
    if (getenv("FRUI_DIRECTIONS")) return directions(maj7, min9, vib);

    /* the main view: F09 70S PHASER, DRIVE and PHASER open, a chord held; the default size at 1x and 2x, the smallest
     * and the largest window */
    load(&m, PR_F09, "F09", "70S PHASER");
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_PHASER;
    chord(&m, maj7, 5);
    render(&m, "main-960x600@1x", 960, 600);
    render(&m, "main-960x600@2x", 1920, 1200);
    render(&m, "size-min-720x450@1x", 720, 450);
    render(&m, "size-min-720x450@2x", 1440, 900);
    render(&m, "size-max-1920x1200@1x", 1920, 1200);
    render(&m, "size-max-1920x1200@2x", 3840, 2400);

    /* every tab, on the factory presets that use it */
    load(&m, PR_F06, "F06", "DRIVEN 77");                   /* COMP + DELAY (TAPE, synced 8D) */
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f;
    chord(&m, min9, 5);
    render(&m, "tab-comp-delay", 960, 600);
    load(&m, PR_F11, "U01", "SWEEP WARM");                   /* a user preset from F11: the EQ set, FILTER swept */
    m.p[FRP_EQ_LO] = 74; m.p[FRP_EQ_MID] = 54; m.p[FRP_EQ_FREQ] = 50; m.p[FRP_EQ_HI] = 60; m.edited = 1;
    m.tab_l = FRT_EQ; m.tab_r = FRT_FILTER;
    render(&m, "tab-eq-filter", 960, 600);
    load(&m, PR_F05, "F05", "SUIT TREM");                    /* DRIVE (FDRV) + TREM/WAH on PAN */
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_TREM;
    chord(&m, maj7, 5);
    render(&m, "tab-drive-tremwah", 960, 600);
    load(&m, PR_F08, "F08", "DYNO 80S");                     /* COMP + CHORUS */
    m.tab_l = FRT_COMP; m.tab_r = FRT_CHORUS; m.gr_db = 1.8f;
    render(&m, "tab-comp-chorus", 960, 600);
    load(&m, PR_F07, "F07", "BALLAD");                       /* EQ (flat) + REVERB page 1 */
    m.tab_l = FRT_EQ; m.tab_r = FRT_REVERB; m.page_rv = 0;
    chord(&m, min9, 5);
    render(&m, "tab-eq-reverb", 960, 600);
    load(&m, PR_F18, "F18", "DREAM");                        /* REVERB 2 (page 2) */
    m.tab_l = FRT_COMP; m.tab_r = FRT_REVERB; m.page_rv = 1; m.gr_db = 1.2f;
    render(&m, "tab-comp-reverb2", 960, 600);
    load(&m, PR_F13, "F13", "VIBES");                        /* the vibraphone: its cards, the MOTOR tab */
    m.tab_l = FRT_MOTOR; m.tab_r = FRT_REVERB;
    chord(&m, vib, 5);
    render(&m, "engine-vibes-motor", 960, 600);
    load(&m, PR_F16, "F16", "WOOD ECHO");                    /* the marimba: MUTE in place of DAMPER */
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 2.0f;
    chord(&m, vib, 3);
    render(&m, "engine-marimba", 960, 600);

    /* interaction states: an open menu (DELAY's SYNC), the keyboard focus ring and a hovered control */
    load(&m, PR_F06, "F06", "DRIVEN 77");
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f;
    m.menu = FRUI_WID(FRW_MENU, FRP_DL_SYNC);
    render(&m, "state-menu-open", 960, 600);
    render(&m, "state-menu-open@2x", 1920, 1200);
    load(&m, PR_F10, "F10", "FUNK WAH");
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_TREM;
    m.focus = FRUI_WID(FRW_KNOB, FRP_TW_DEPTH); m.hover = FRUI_WID(FRW_KNOB, FRP_DRIVE);
    chord(&m, min9, 3);
    render(&m, "state-focus-hover", 960, 600);

    /* PRESETS (a click on the preset's name): every preset by instrument, the one playing marked, a row hovered; with
     * user presets (two of them on VIBES and STAGE); a long list over two pages */
    {
        static const char *const un[4] = {"MY BALLAD", "LATE SET", "MY VIBES", "GOSPEL ROOM"};
        static const int ut[4] = {0, 0, 4, 1};
        load(&m, PR_F09, "F09", "70S PHASER");
        m.tab_l = FRT_DRIVE; m.tab_r = FRT_PHASER;
        plist(&m, 4, un, ut);
        m.presets = 1; m.pl_cur = 8; m.hover = FRUI_WID(FRW_PITEM, 44);
        chord(&m, maj7, 5);
        render(&m, "presets-open", 960, 600);
        render(&m, "presets-open@2x", 1920, 1200);
        plist(&m, 60, un, ut);
        m.pl_page = 1; m.pl_cur = 70; m.hover = -1;
        render(&m, "presets-page2", 960, 600);
    }
    /* the effects bypassed (EFFECTS: BYPASS): every effect knob drawn inactive, the voice's knobs as they are */
    load(&m, PR_F06, "F06", "DRIVEN 77");
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 0.f; m.fx_bypass = 1;
    render(&m, "fx-bypass", 960, 600);
    render(&m, "fx-bypass@2x", 1920, 1200);
    frui_font_free(font);
    free(d);
    return 0;
}
