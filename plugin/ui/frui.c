/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the window (frui.h). Layout, widgets and the look, all in logical pixels scaled at draw time.
 *
 *   rail        FRODHES (lamp) | < F09 70S PHASER > SAVE UNDO |                    OUTPUT meter | LEVEL
 *   VOICE       the instrument (a compact selector with its drawing) and its eight knobs in two groups of four, as
 *               the device's VOICE 1/2 and 2/2
 *   two panels  left: what comes before the drive (MOTOR on VIBES, COMP, EQ, DRIVE); right: the effects after it
 *               (FILTER, TREM/WAH, PHASER, CHORUS, DELAY, REVERB); one tab at a time: its switch / type, its knobs
 *   keyboard    C1 .. C6, the held notes lit
 *
 * The grid: 4 px base unit; 16 px margins and gutters; two halves (x 16..472 and 488..944), each with 12 px padding
 * and four 108 px columns, so every knob in the window sits on one of eight column centres (82 190 298 406 | 554
 * 662 770 878) or, for a row of three, on the half-column between them. Rows: rail 0..56, VOICE 72..264, the effect
 * panels 280..488, the keyboard 504..584.
 *
 * The look: brushed aluminium and deep black, white legends, one amber accent (the pilot lamp, the effects' lamps,
 * the held keys). Knobs: black skirts with spun-aluminium caps over an engraved scale whose ticks light up to the
 * value. Hairlines are one device pixel.
 *
 * Three looks share the layout: A1 (an aluminium rail over a black body), A2 (one aluminium plate, black sections set
 * into it) and A3, the default: a rounded plate (R_OUT) carries the rail and an 8 px frame round one rounded black body
 * (R_BODY); the sections, the keys' bed and the corner switch sit 8 px in, on R_PANEL, so every nested corner is
 * concentric (the layout check proves it). Bevels are hairlines; the metal is brushed lightly and only frames. */
#include "frui.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXW 160

struct frui {
    frui_font *f, *fd;             /* text; display (the wordmark, the section titles) */
    frui_text_fn text;
    void *ctx;
    frui_widget wg[MAXW];
    frui_anchor an[MAXW];
    frui_shell sh[16];
    int nw, na, nsh, look;
    int layer, sb[4];              /* FRUI_LAYER_*; the canvas's base clip (the static layer's sections draw in it) */
    float s;                       /* pixels per logical pixel */
    float hp;                      /* one device pixel, in logical pixels */
    frui_canvas *c;
    const frui_model *m;
    float rg[FRR_N][4];            /* frui_region */
};

/* ---- the grid */
#define G_M 16.f                   /* margin, gutter */
#define G_PAD 12.f                 /* panel padding */
#define G_COL 108.f                /* column */
#define G_H0 28.f                  /* the halves' first column edges */
#define G_H1 500.f
#define RAIL_H 56.f
#define VO_Y 72.f
#define VO_H 192.f
#define FX_Y 280.f
#define FX_H 208.f
#define KB_Y 504.f
#define KB_H 80.f
#define R_PANEL 6.f                /* corner radii: panels, controls, small parts */
#define R_CTRL 4.f
#define R_SMALL 2.f
#define R_OUT 22.f                 /* A3: the plate's outer corners; the body's (R_OUT - A3_FRAME); the sections' R_PANEL
                                    * (R_BODY - the 8 px gap): one scale, concentric where corners nest */
#define A3_FRAME 8.f               /* A3: the metal frame round the body; the body from y A3_BODY_Y */
#define R_BODY (R_OUT - A3_FRAME)
#define A3_BODY_Y 64.f
#define KB_X 136.f                 /* the keys from column 2, after the SUSTAIN and FX switches (16..120) */
#define KNOB_R 24.f                /* knob tiers */
#define KNOB_R2 14.f
static float colc(int half, int k) { return (half ? G_H1 : G_H0) + G_COL * (float)k + G_COL * 0.5f; }

/* ---- the palette */
#define C_BG FRUI_RGB(12, 12, 13)
#define C_PANEL FRUI_RGB(20, 20, 21)
#define C_INSET FRUI_RGB(9, 9, 10)
#define C_EDGE FRUI_RGB(38, 38, 40)
#define C_TEXT FRUI_RGB(238, 237, 232)
#define C_TEXT2 FRUI_RGB(150, 150, 154)
#define C_TEXT3 FRUI_RGB(104, 104, 109)
#define C_DIM FRUI_RGB(58, 58, 62)
#define C_ACC FRUI_RGB(255, 152, 38)
#define C_ACC_D FRUI_RGB(150, 78, 8)
#define C_AL_T FRUI_RGB(204, 206, 209)  /* aluminium, top .. bottom of a lit plate */
#define C_AL_B FRUI_RGB(170, 172, 176)
#define C_ENGR FRUI_RGB(34, 35, 38)       /* engraved legends on the metal */
#define C_ENGR2 FRUI_RGB(78, 79, 83)
#define C_HL FRUI_RGBA(255, 255, 255, 150)   /* the light edge of an engraving / bevel on the metal */
#define C_SHADOW FRUI_RGBA(0, 0, 0, 170)

/* type: size (logical px), weight, tracking */
#define T_LABEL 9.5f, 620.f, 1.1f
#define T_CAPTION 8.5f, 600.f, 1.6f
#define T_VALUE 15.f, 560.f, 0.f
#define T_UNIT 10.f, 520.f, 0.3f
#define T_TAB 10.5f, 640.f, 1.0f
#define T_SEG 9.5f, 640.f, 0.8f
#define T_BTN 9.5f, 680.f, 1.2f
#define T_MENU 10.5f, 600.f, 0.5f

#define X(v) ((v) * u->s)

/* ---- text in logical coordinates (font fd for the display face) */
static float twf(frui *u, frui_font *f, float size, float weight, float track, int flags, const char *s)
{
    frui_style st = {size * u->s, weight, track * u->s, flags};
    return frui_text_width(f, &st, s) / u->s;
}
static float txf(frui *u, frui_font *f, float x, float y, float size, float weight, float track, int flags, const char *s,
                 uint32_t col)
{
    frui_style st = {size * u->s, weight, track * u->s, flags};
    /* tracking adds space after every glyph, the last too: remove it from the advance so centring stays optical */
    return frui_text(u->c, f, &st, X(x), X(y), s, col) / u->s;
}
static float tw(frui *u, float size, float weight, float track, int flags, const char *s)
{
    return twf(u, u->f, size, weight, track, flags, s) - track;
}
static float tx(frui *u, float x, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    return txf(u, u->f, x, y, size, weight, track, flags, s, col);
}
/* baseline that centres capitals on y */
static float mid(frui *u, float y, float size) { return y + frui_font_ascent(u->f, size) * 0.5f; }
static float midd(frui *u, float y, float size) { return y + frui_font_ascent(u->fd, size) * 0.5f; }
static void txc(frui *u, float cx, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    tx(u, cx - tw(u, size, weight, track, flags, s) * 0.5f, y, size, weight, track, flags, s, col);
}
static void txr(frui *u, float rx, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    tx(u, rx - tw(u, size, weight, track, flags, s), y, size, weight, track, flags, s, col);
}
/* engraved into the metal: the cut (dark) with the lit lower edge one device pixel below */
static void engrave(frui *u, frui_font *f, float x, float y, float size, float weight, float track, const char *s,
                    uint32_t col)
{
    txf(u, f, x, y + u->hp, size, weight, track, 0, s, C_HL);
    txf(u, f, x, y, size, weight, track, 0, s, col);
}

static void reg(frui *u, int kind, int id, float x, float y, float w, float h)
{
    if (u->nw < MAXW) { frui_widget *g = &u->wg[u->nw++]; g->kind = kind; g->id = id; g->x = x; g->y = y; g->w = w; g->h = h; }
}
static void anchor(frui *u, int what, int id, float x, float y)
{
    if (u->na < MAXW) { frui_anchor *a = &u->an[u->na++]; a->what = what; a->id = id; a->x = x; a->y = y; }
}
static void region(frui *u, int which, float x, float y, float w, float h)
{
    u->rg[which][0] = x; u->rg[which][1] = y; u->rg[which][2] = w; u->rg[which][3] = h;
}
static void shell(frui *u, float x, float y, float w, float h, float r)
{
    if (u->nsh < 16) { frui_shell *s = &u->sh[u->nsh++]; s->x = x; s->y = y; s->w = w; s->h = h; s->r = r; }
}
/* the static layer (frui_set_layer): st_begin before its shapes (0: skip them), st_end after; between them the static
 * pass draws in the canvas's base clip, everywhere else in an empty one */
static int st_begin(frui *u)
{
    if (u->layer == FRUI_LAYER_DYNAMIC) return 0;
    if (u->layer == FRUI_LAYER_STATIC) frui_clip_base(u->c, u->sb[0], u->sb[1], u->sb[2], u->sb[3]);
    return 1;
}
static void st_end(frui *u)
{
    if (u->layer == FRUI_LAYER_STATIC) frui_clip_base(u->c, 0, 0, 0, 0);
}
static int is_focus(frui *u, int kind, int id) { return u->m->focus == FRUI_WID(kind, id); }
static int is_hover(frui *u, int kind, int id) { return u->m->hover == FRUI_WID(kind, id); }

/* ---- values */
static int pval(const frui_model *m, int id)
{
    switch (id) {
    case FRS_CHANNEL: return m->midi_ch;
    case FRS_VEL: return m->vel_curve;
    case FRS_FIXVEL: return m->fixed_vel;
    case FRS_PGM: return m->pgm_change;
    case FRS_TEMPO: return m->tempo_src;
    case FRS_SYNCALL: return m->sync_all;
    case FRS_SYNCDIV: return m->sync_div;
    }
    if (id == FRP_TUNE) return m->tune + 50;
    if (id == FRP_TW_KIND) return m->p[FRP_TW_SW] == 0 ? 0 : m->p[FRP_TW_SW] == 2 ? 3 : m->p[FRP_TW_MODE] ? 2 : 1;
    return id >= 0 && id < FRP_COUNT ? m->p[id] : 0;
}
static int phi(int id)
{
    switch (id) {
    case FRS_CHANNEL: return 16;
    case FRS_VEL: return 3;
    case FRS_FIXVEL: return 127;
    case FRS_TEMPO: return 2;
    case FRS_SYNCDIV: return 14;
    case FRS_PGM: case FRS_SYNCALL: return 1;
    }
    return id == FRP_TUNE ? 100 : id == FRP_TW_KIND ? 3 : id >= 0 && id < FRP_COUNT ? FRP_HI[id] : 1;
}
static int is_bars(const frui_model *m) { return m->p[FRP_TYPE] >= 4; }
static int has_motor(const frui_model *m) { return m->p[FRP_TYPE] == 4; }

static const char *const *opt_names(int id, int *n)
{
    *n = phi(id) + 1;
    static const char *const ch[17] = {"ALL", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14",
                                       "15", "16"};
    static const char *const vel[4] = {"MEDIUM", "LIGHT", "HEAVY", "FIXED"};
    static const char *const src[3] = {"HOST", "INTERNAL", "MIDI CLOCK"};
    static const char *const sall[2] = {"FREE", "TEMPO"};
    switch (id) {
    case FRS_CHANNEL: return ch;
    case FRS_VEL: return vel;
    case FRS_TEMPO: return src;
    case FRS_SYNCALL: return sall;
    case FRS_SYNCDIV: return FRN_SYNC;
    case FRP_TYPE: return FRN_TYPE;
    case FRP_TW_KIND: return FRN_TWK;
    case FRP_DRV_SW: return FRN_DRV;
    case FRP_DL_SW: return FRN_DL;
    case FRP_RV_TYPE: return FRN_RV;
    case FRP_FL_TYPE: return FRN_FLT;
    case FRP_LFO_SHAPE: return FRN_LFOS;
    case FRP_LFO_RETRIG: return FRN_RETRIG;
    case FRP_TW_SYNC: case FRP_CP_SYNC: case FRP_DL_SYNC: case FRP_PH_SYNC: case FRP_LFO_SYNC: case FRP_MOT_SYNC:
        return FRN_SYNC;
    default: return FRN_ONOFF;
    }
}

/* ---- small shapes */
/* a crisp horizontal / vertical hairline of n device pixels */
static void hair_h(frui *u, float x0, float x1, float y, uint32_t col)
{
    frui_rect(u->c, X(x0), floorf(X(y)), X(x1 - x0), 1.f, col);
}
static void hair_v(frui *u, float x, float y0, float y1, uint32_t col)
{
    frui_rect(u->c, floorf(X(x)), X(y0), 1.f, X(y1 - y0), col);
}
static void chevron(frui *u, float cx, float cy, float sz, int dir, uint32_t col)   /* dir 0 down, 1 left, 2 right */
{
    float a = sz, b = sz * 0.5f, lw = X(1.4f);
    if (dir == 0) {
        frui_line(u->c, X(cx - a), X(cy - b), X(cx), X(cy + b), lw, col);
        frui_line(u->c, X(cx), X(cy + b), X(cx + a), X(cy - b), lw, col);
    } else {
        float d = dir == 1 ? 1.f : -1.f;
        frui_line(u->c, X(cx + b * d), X(cy - a), X(cx - b * d), X(cy), lw, col);
        frui_line(u->c, X(cx - b * d), X(cy), X(cx + b * d), X(cy + a), lw, col);
    }
}
static void lamp(frui *u, float cx, float cy, float r, int on)     /* an effect's lamp: a small amber jewel */
{
    if (on) {
        frui_circle_shadow(u->c, X(cx), X(cy), X(r), X(r * 1.4f), FRUI_RGBA(255, 140, 20, 110));
        frui_circle_grad(u->c, X(cx), X(cy), X(r), FRUI_RGB(255, 206, 120), C_ACC);
    } else {
        frui_circle_grad(u->c, X(cx), X(cy), X(r), FRUI_RGB(30, 30, 32), FRUI_RGB(52, 52, 56));
    }
}
/* brushed aluminium plate with machined edges: a lit top hairline and a dark bottom one */
static void alu(frui *u, float x, float y, float w, float h, float r, uint32_t seed)
{
    frui_brushed(u->c, X(x), X(y), X(w), X(h), X(r), C_AL_T, C_AL_B, 0.045f, u->s, seed);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(r), 1.f, FRUI_RGBA(0, 0, 0, 70));
    if (h > 6.f) {
        frui_rect(u->c, X(x + r), X(y) + 1.f, X(w - 2.f * r), 1.f, FRUI_RGBA(255, 255, 255, 170));
        frui_rect(u->c, X(x + r), X(y + h) - 2.f, X(w - 2.f * r), 1.f, FRUI_RGBA(0, 0, 0, 60));
    }
}
/* a window cut into the metal: black, its upper edge in shadow, its lower edge catching the light */
static void well(frui *u, float x, float y, float w, float h, float r)
{
    frui_rrect(u->c, X(x), X(y) + 1.f, X(w), X(h), X(r), C_HL);
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(r), FRUI_RGB(58, 59, 62));
    frui_rrect(u->c, X(x) + 1.f, X(y) + 1.f, X(w) - 2.f, X(h) - 2.f, X(r) - 1.f, C_INSET);
    frui_rect(u->c, X(x + r), X(y) + 1.f, X(w - 2.f * r), X(2.f), FRUI_RGBA(0, 0, 0, 200));
}
/* the black section panels: raised from the black body (rail) or set into the plate */
static void panel_px(frui *u, float x, float y, float w, float h);
static void panel(frui *u, float x, float y, float w, float h)
{
    shell(u, x, y, w, h, R_PANEL);
    if (st_begin(u)) { panel_px(u, x, y, w, h); st_end(u); }
}
static void panel_px(frui *u, float x, float y, float w, float h)
{
    if (u->look == FRUI_LOOK_A3) {   /* barely raised from the body: a hairline lit edge on top, a flat face */
        frui_rrect(u->c, X(x), X(y) + 1.f, X(w), X(h), X(R_PANEL), FRUI_RGBA(0, 0, 0, 200));
        frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_PANEL), FRUI_RGB(40, 40, 42));
        frui_rrect_grad(u->c, X(x), X(y) + 1.f, X(w), X(h) - 1.f, X(R_PANEL), FRUI_RGB(24, 24, 25), FRUI_RGB(19, 19, 20));
        frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_PANEL), 1.f, FRUI_RGBA(255, 255, 255, 9));
        return;
    }
    if (u->look == FRUI_LOOK_PLATE) {
        frui_rrect(u->c, X(x), X(y) + 1.f, X(w), X(h), X(R_PANEL), C_HL);                 /* the chamfer's lit edge */
        frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_PANEL), FRUI_RGB(70, 71, 74));        /* the chamfer */
        frui_rrect(u->c, X(x) + 1.f, X(y) + 1.f, X(w) - 2.f, X(h) - 2.f, X(R_PANEL) - 1.f, C_PANEL);
        {
            int i;
            for (i = 0; i < 6; i++)
                frui_rect(u->c, X(x + R_PANEL), X(y) + 1.f + (float)i, X(w - 2.f * R_PANEL), 1.f,
                          FRUI_RGBA(0, 0, 0, 120 - 20 * i));
        }
    } else {
        frui_rrect_shadow(u->c, X(x), X(y + 2.f), X(w), X(h), X(R_PANEL), X(4.f), FRUI_RGBA(0, 0, 0, 160));
        frui_rrect_grad(u->c, X(x), X(y), X(w), X(h), X(R_PANEL), FRUI_RGB(23, 23, 24), FRUI_RGB(18, 18, 19));
        frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_PANEL), 1.f, FRUI_RGB(31, 31, 33));
        frui_rect(u->c, X(x + R_PANEL), X(y) + 1.f, X(w - 2.f * R_PANEL), 1.f, FRUI_RGBA(255, 255, 255, 22));
    }
}
/* A3: the plate (rounded, brushed, a machined edge) and the black body set into it under the rail */
static void a3_plate(frui *u)
{
    float W = (float)FRUI_W, H = (float)FRUI_H, bx = A3_FRAME, by = A3_BODY_Y, bw = W - 2.f * A3_FRAME,
          bh = H - A3_BODY_Y - A3_FRAME;
    shell(u, 0.f, 0.f, W, H, R_OUT);
    shell(u, bx, by, bw, bh, R_BODY);
    if (!st_begin(u)) return;
    frui_fill(u->c, FRUI_RGB(6, 6, 7));
    frui_brushed(u->c, 0.f, 0.f, X(W), X(H), X(R_OUT), FRUI_RGB(200, 202, 205), FRUI_RGB(176, 178, 182), 0.03f, u->s, 1u);
    frui_rrect_stroke(u->c, 0.5f, 0.5f, X(W) - 1.f, X(H) - 1.f, X(R_OUT), 1.f, FRUI_RGBA(0, 0, 0, 110));
    frui_rrect_stroke(u->c, 1.5f, 1.5f, X(W) - 3.f, X(H) - 3.f, X(R_OUT) - 1.f, 1.f, FRUI_RGBA(255, 255, 255, 110));
    /* the body: a chamfer, dark above and lit below, then the black with a two-pixel shade under its top edge */
    frui_rrect(u->c, X(bx), X(by) + 1.f, X(bw), X(bh), X(R_BODY), FRUI_RGBA(255, 255, 255, 150));
    frui_rrect(u->c, X(bx), X(by), X(bw), X(bh), X(R_BODY), FRUI_RGB(52, 53, 56));
    frui_rrect(u->c, X(bx) + 1.f, X(by) + 1.f, X(bw) - 2.f, X(bh) - 2.f, X(R_BODY) - 1.f, FRUI_RGB(2, 2, 3));
    frui_rrect(u->c, X(bx) + 1.f, X(by) + 3.f, X(bw) - 2.f, X(bh) - 4.f, X(R_BODY) - 1.f, FRUI_RGB(8, 8, 9));
    st_end(u);
}
/* A3: square corners outside a rounded one cut away in col (for the keys in their rounded bed); q: 0 top left, 1 top
 * right, 2 bottom right, 3 bottom left */
static void corner_cut(frui *u, float x, float y, float r, int q, uint32_t col)
{
    float sx = q == 1 || q == 2 ? -1.f : 1.f, sy = q >= 2 ? -1.f : 1.f, k = 0.5523f;
    frui_path p;
    frui_path_init(&p);
    frui_path_move(&p, X(x), X(y));
    frui_path_line(&p, X(x + sx * r), X(y));
    /* the quarter circle as two quadratics through its 45 degree point */
    frui_path_quad(&p, X(x + sx * r * (1.f - k * 0.75f)), X(y), X(x + sx * r * 0.2929f), X(y + sy * r * 0.2929f));
    frui_path_quad(&p, X(x), X(y + sy * r * (1.f - k * 0.75f)), X(x), X(y + sy * r));
    frui_path_line(&p, X(x), X(y));
    frui_path_fill(u->c, &p, col);
    frui_path_free(&p);
}
static void section_title(frui *u, float x, float cy, const char *s)
{
    anchor(u, FRA_TITLE, 0, x, cy);
    txf(u, u->fd, x, midd(u, cy, 10.f), 10.f, 400.f, 2.4f, 0, s, C_TEXT);
}

/* ---- the knob: a black skirt with a fine knurl, a spun-aluminium cap, a white index; the scale's ticks around it
 * light up to the value (from the left end, or from the top when centred); dim = the effect is off */
#define KA0 (225.f * (float)M_PI / 180.f)
#define KSPAN (270.f * (float)M_PI / 180.f)
static void knob_body(frui *u, float cx, float cy, float r, float v, int centred, int ticks, int dim, int metal, int hov)
{
    float a = KA0 - KSPAN * v, rc = r * 0.54f, ca = cosf(a), sa = sinf(a);
    int i;
    if (ticks) {
        for (i = 0; i <= 10; i++) {
            float t = (float)i / 10.f, ta = KA0 - KSPAN * t, r0 = r + 4.f, r1 = r + (i % 5 ? 7.f : 9.f);
            int on = centred ? (v >= 0.5f ? t >= 0.5f - 1e-3f && t <= v + 1e-3f : t <= 0.5f + 1e-3f && t >= v - 1e-3f)
                             : t <= v + 1e-3f && v > 0.f;
            uint32_t col;
            if (metal) col = on ? C_ENGR : FRUI_RGB(132, 133, 137);
            else col = on ? (dim ? FRUI_RGB(96, 96, 100) : C_TEXT) : dim ? FRUI_RGB(38, 38, 41) : FRUI_RGB(50, 50, 54);
            frui_line(u->c, X(cx + cosf(ta) * r0), X(cy - sinf(ta) * r0), X(cx + cosf(ta) * r1), X(cy - sinf(ta) * r1),
                      X(1.3f), col);
        }
    }
    frui_shadow_hole_circle(u->c, X(cx), X(cy), X(r));   /* (the skirt below covers it: cheaper, the same pixels) */
    frui_circle_shadow(u->c, X(cx), X(cy + r * 0.14f), X(r * 0.96f), X(r * 0.16f), FRUI_RGBA(0, 0, 0, metal ? 120 : 200));
    /* (dim: the effect is off or bypassed: the same knob, its light taken down: a darker skirt and cap, a grey index) */
    frui_circle_grad(u->c, X(cx), X(cy), X(r), dim ? FRUI_RGB(36, 36, 38) : hov ? FRUI_RGB(62, 62, 66) : FRUI_RGB(54, 54, 57),
                     FRUI_RGB(10, 10, 11));
    /* the knurl: fine ridges on the skirt's edge, lit from above */
    for (i = 0; i < 72; i++) {
        float t = (float)i * 2.f * (float)M_PI / 72.f, l = 0.5f + 0.5f * sinf(t);
        frui_line(u->c, X(cx + cosf(t) * (r - 2.6f)), X(cy - sinf(t) * (r - 2.6f)), X(cx + cosf(t) * (r - 0.7f)),
                  X(cy - sinf(t) * (r - 0.7f)), 1.f, FRUI_RGBA(255, 255, 255, (uint32_t)((10.f + 34.f * l * l) * (dim ? 0.5f : 1.f))));
    }
    frui_arc(u->c, X(cx), X(cy), X(r) - 0.5f, 1.f, 0.35f, (float)M_PI - 0.35f, FRUI_RGBA(255, 255, 255, dim ? 30 : 60));
    /* the step down to the cap, the cap and its lit rim */
    frui_circle(u->c, X(cx), X(cy), X(rc + 1.6f), FRUI_RGB(4, 4, 5));
    frui_spun(u->c, X(cx), X(cy), X(rc), dim ? FRUI_RGB(40, 41, 44) : FRUI_RGB(92, 94, 98),
              dim ? FRUI_RGB(98, 99, 102) : FRUI_RGB(222, 223, 225), 0.05f, u->s, (uint32_t)(cx * 7.f + cy));
    frui_ring(u->c, X(cx), X(cy), X(rc) - 0.5f, 1.f, FRUI_RGBA(255, 255, 255, dim ? 40 : 110));
    /* the index: white on the skirt, engraved on the cap */
    frui_line(u->c, X(cx + ca * (rc + 3.f)), X(cy - sa * (rc + 3.f)), X(cx + ca * (r - 3.2f)), X(cy - sa * (r - 3.2f)),
              X(r >= 20.f ? 2.f : 1.6f), dim ? FRUI_RGB(124, 124, 122) : FRUI_RGB(246, 246, 242));
    frui_line(u->c, X(cx + ca * rc * 0.45f), X(cy - sa * rc * 0.45f), X(cx + ca * (rc - 2.f)), X(cy - sa * (rc - 2.f)),
              X(1.4f), FRUI_RGBA(30, 30, 33, 200));
}
static void knob(frui *u, int id, const char *label, float cx, float cy, int active)
{
    const frui_model *m = u->m;
    float r = KNOB_R, v = (float)pval(m, id) / (float)phi(id);
    char val[24], unit[12];
    int hov = is_hover(u, FRW_KNOB, id), foc = is_focus(u, FRW_KNOB, id);
    if (u->look == FRUI_LOOK_A3) {   /* A3: quieter labels (smaller, wider, dimmer) under brighter values */
        txc(u, cx, cy - r - 15.f, 8.5f, 600.f, 1.5f, 0, label, m->learning == id ? C_ACC : hov || foc ? (active ? C_TEXT : C_TEXT2) :
                                                                 active ? FRUI_RGB(132, 132, 137) : FRUI_RGB(84, 84, 88));
        if (id < FRP_COUNT && m->learn_cc[id])
            frui_circle(u->c, X(cx - tw(u, 8.5f, 600.f, 1.5f, 0, label) * 0.5f - 6.f), X(cy - r - 18.f), X(1.5f), C_ACC);
    } else {
    txc(u, cx, cy - r - 15.f, T_LABEL, 0, label, m->learning == id ? C_ACC : hov || foc ? C_TEXT : C_TEXT2);
    if (id < FRP_COUNT && m->learn_cc[id])            /* a learned CC: a small amber mark before the label */
        frui_circle(u->c, X(cx - tw(u, T_LABEL, 0, label) * 0.5f - 6.f), X(cy - r - 18.5f), X(1.6f), C_ACC);
    }
    anchor(u, FRA_LABEL, id, cx, cy - r - 15.f);
    knob_body(u, cx, cy, r, v, FRP_CENTRED(id), 1, !active, 0, hov);
    if (foc || m->learning == id) frui_arc(u->c, X(cx), X(cy), X(r + 12.f), X(1.2f), KA0 - KSPAN, KA0, frui_alpha(C_ACC, 0.9f));   /* open below: the value */
    u->text(u->ctx, m, id, val, unit);
    if (m->learning == id) { strcpy(val, "LEARN"); unit[0] = 0; }
    {
        float vs = u->look == FRUI_LOOK_A3 ? 14.f : 15.f, vw = u->look == FRUI_LOOK_A3 ? 500.f : 560.f;
        float wv = tw(u, vs, vw, 0.f, FRUI_TNUM, val), wu = unit[0] ? tw(u, T_UNIT, 0, unit) + 3.f : 0.f;
        float x0 = cx - (wv + wu) * 0.5f, yb = cy + r + 24.f;
        if (m->editing == id + 1) {      /* the value being typed: a small field, the text and a caret */
            float we = tw(u, vs, vw, 0.f, FRUI_TNUM, m->edit_text), fw = we + 20.f < 64.f ? 64.f : we + 20.f;
            float fx = cx - fw * 0.5f, fy = yb - 16.f, xt = cx - we * 0.5f - 2.f;
            frui_rrect(u->c, X(fx), X(fy), X(fw), X(21.f), X(4.f), C_INSET);
            frui_rrect_stroke(u->c, X(fx) + 0.5f, X(fy) + 0.5f, X(fw) - 1.f, X(21.f) - 1.f, X(4.f), X(1.f), C_ACC);
            tx(u, xt, yb, vs, vw, 0.f, FRUI_TNUM, m->edit_text, C_TEXT);
            frui_rect(u->c, X(xt + we + 2.f), X(fy + 4.f), X(1.2f), X(13.f), C_ACC);
            anchor(u, FRA_VALUE, id, cx, yb);
        } else {
            tx(u, x0, yb, vs, vw, 0.f, FRUI_TNUM, val, m->learning == id ? C_ACC : active ? C_TEXT : C_TEXT3);
            anchor(u, FRA_VALUE, id, x0 + (wv + wu) * 0.5f, yb);
            if (unit[0]) tx(u, x0 + wv + 3.f, yb, T_UNIT, 0, unit, active ? C_TEXT3 : FRUI_RGB(72, 72, 76));
        }
    }
    reg(u, FRW_KNOB, id, cx - G_COL * 0.5f, cy - r - 28.f, G_COL, 2.f * r + 56.f);
}
/* n knobs in one half: four on the column centres, two on the middle columns, three on the half-columns */
static void knob_row(frui *u, int half, const int *ids, const char *const *labels, int n, float cy, int active)
{
    int k;
    for (k = 0; k < n; k++) {
        float x = n == 4 ? colc(half, k) : n == 2 ? colc(half, k + 1) : colc(half, 0) + G_COL * 0.5f * (float)(2 * k + 1);
        knob(u, ids[k], labels[k], x, cy, active);
    }
}

/* ---- controls */
/* a segmented switch: a dark well; the chosen segment a small aluminium key with a black legend */
static float seg_width(frui *u, const char *const *names, int n)
{
    float mw = 0.f;
    int i;
    for (i = 0; i < n; i++) { float w = tw(u, T_SEG, 0, names[i]); if (w > mw) mw = w; }
    return ceilf((mw + 20.f) / 4.f) * 4.f * (float)n;
}
static float segmented(frui *u, int id, float x, float y, float h, float w)
{
    int n, i, sel = pval(u->m, id);
    const char *const *names = opt_names(id, &n);
    float sw;
    if (w <= 0.f) w = seg_width(u, names, n);
    sw = w / (float)n;
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_CTRL), C_INSET);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_CTRL), 1.f, C_EDGE);
    for (i = 1; i < n; i++)
        if (i != sel && i != sel + 1) hair_v(u, x + sw * (float)i, y + 8.f, y + h - 8.f, C_EDGE);
    if (sel >= 0 && sel < n) {
        float sx = x + sw * (float)sel + 2.f;
        frui_rrect_shadow(u->c, X(sx), X(y + 3.f), X(sw - 4.f), X(h - 4.f), X(R_SMALL), X(1.5f), FRUI_RGBA(0, 0, 0, 200));
        alu(u, sx, y + 2.f, sw - 4.f, h - 4.f, R_SMALL, (uint32_t)id + 11u);
    }
    for (i = 0; i < n; i++)
        txc(u, x + sw * ((float)i + 0.5f), mid(u, y + h * 0.5f, 9.5f), T_SEG, 0, names[i], i == sel ? C_ENGR : C_TEXT2);
    if (is_focus(u, FRW_SEG, id))
        frui_rrect_stroke(u->c, X(x - 3.f), X(y - 3.f), X(w + 6.f), X(h + 6.f), X(R_CTRL + 3.f), X(1.f), C_ACC);
    reg(u, FRW_SEG, id, x, y, w, h);
    return w;
}
static void ctrl_box(frui *u, float x, float y, float w, float h, int hot, int open)
{
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_CTRL), hot ? FRUI_RGB(16, 16, 17) : C_INSET);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_CTRL), 1.f,
                      open ? frui_alpha(C_ACC, 0.8f) : C_EDGE);
}
/* a menu button of width w: caption, the value, a chevron */
static float menu_btn(frui *u, int id, const char *caption, float x, float y, float h, float w)
{
    int n, sel = pval(u->m, id);
    const char *const *names = opt_names(id, &n);
    float wc = tw(u, T_CAPTION, 0, caption);
    int open = u->m->menu == FRUI_WID(FRW_MENU, id);
    ctrl_box(u, x, y, w, h, open || is_hover(u, FRW_MENU, id), open);
    tx(u, x + 10.f, mid(u, y + h * 0.5f, 8.5f), T_CAPTION, 0, caption, C_TEXT3);
    tx(u, x + 10.f + (caption[0] ? wc + 7.f : 0.f), mid(u, y + h * 0.5f, 10.5f), T_MENU, 0, sel < n ? names[sel] : "", C_TEXT);
    chevron(u, x + w - 12.f, y + h * 0.5f, 3.f, 0, C_TEXT2);
    if (is_focus(u, FRW_MENU, id))
        frui_rrect_stroke(u->c, X(x - 3.f), X(y - 3.f), X(w + 6.f), X(h + 6.f), X(R_CTRL + 3.f), X(1.f), C_ACC);
    reg(u, FRW_MENU, id, x, y, w, h);
    return w;
}
/* a key on the metal: black, a lit top edge */
static void key_btn(frui *u, int id, const char *label, float x, float y, float w, float h)
{
    int hov = is_hover(u, FRW_BUTTON, id);
    if (u->look == FRUI_LOOK_A3) frui_rrect(u->c, X(x), X(y) + 1.f, X(w), X(h), X(R_CTRL), FRUI_RGBA(255, 255, 255, 120));
    else frui_rrect_shadow(u->c, X(x), X(y + 1.5f), X(w), X(h), X(R_CTRL), X(1.5f), FRUI_RGBA(0, 0, 0, 120));
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_CTRL), FRUI_RGB(6, 6, 7));
    frui_rrect_grad(u->c, X(x) + 1.f, X(y) + 1.f, X(w) - 2.f, X(h) - 2.f, X(R_CTRL) - 1.f,
                    hov ? FRUI_RGB(46, 46, 49) : FRUI_RGB(38, 38, 41), FRUI_RGB(18, 18, 19));
    frui_rect(u->c, X(x + R_CTRL), X(y) + 1.f, X(w - 2.f * R_CTRL), 1.f, FRUI_RGBA(255, 255, 255, 40));
    if (label) txc(u, x + w * 0.5f, mid(u, y + h * 0.5f, 9.5f), T_BTN, 0, label, C_TEXT);
    else if (id == FRB_GEAR) {                       /* a gear: eight teeth round a ring */
        float gx = x + w * 0.5f, gy = y + h * 0.5f;
        int i;
        for (i = 0; i < 8; i++) {
            float t = (float)i * (float)M_PI / 4.f;
            frui_line(u->c, X(gx + cosf(t) * 4.5f), X(gy - sinf(t) * 4.5f), X(gx + cosf(t) * 7.f), X(gy - sinf(t) * 7.f), X(2.4f), C_TEXT);
        }
        frui_ring(u->c, X(gx), X(gy), X(4.6f), X(1.8f), C_TEXT);
    } else if (id == FRB_CLOSE) {
        float gx = x + w * 0.5f, gy = y + h * 0.5f;
        frui_line(u->c, X(gx - 4.5f), X(gy - 4.5f), X(gx + 4.5f), X(gy + 4.5f), X(1.5f), C_TEXT);
        frui_line(u->c, X(gx - 4.5f), X(gy + 4.5f), X(gx + 4.5f), X(gy - 4.5f), X(1.5f), C_TEXT);
    } else chevron(u, x + w * 0.5f, y + h * 0.5f, 4.f, id == FRB_PREV || id == FRB_TYPE_PREV || id == FRB_BPM_DOWN ? 1 : 2, C_TEXT);
    reg(u, FRW_BUTTON, id, x, y, w, h);
}

/* ---- the engines' drawings: small line pictures in a 40 x 24 box (original: generic instruments, no marks) */
static void eicon(frui *u, int type, float x, float y, float k, uint32_t col)
{
#define P(px, py) X(x + (px) * k), X(y + (py) * k)
#define L(x0, y0, x1, y1) frui_line(u->c, P(x0, y0), P(x1, y1), X(1.25f * k), col)
#define RR(x0, y0, w0, h0, r0) frui_rrect_stroke(u->c, P(x0, y0), X((w0) * k), X((h0) * k), X((r0) * k), X(1.25f * k), col)
#define FR(x0, y0, w0, h0, r0, c0) frui_rrect(u->c, P(x0, y0), X((w0) * k), X((h0) * k), X((r0) * k), c0)
    int i;
    switch (type) {
    case 0:   /* a stage piano: the case with its keys, on four legs */
        RR(3, 5, 34, 8, 1.5f);
        for (i = 0; i < 9; i++) L(6.f + 3.5f * (float)i, 10.f, 6.f + 3.5f * (float)i, 13.f);
        L(7, 13, 5, 22); L(10, 13, 12, 22); L(33, 13, 35, 22); L(30, 13, 28, 22);
        break;
    case 1:   /* a suitcase piano: the case on its speaker cabinet */
        RR(4, 2, 32, 7, 1.5f);
        for (i = 0; i < 8; i++) L(7.f + 3.6f * (float)i, 6.f, 7.f + 3.6f * (float)i, 9.f);
        RR(6, 10, 28, 13, 1.5f);
        frui_ring(u->c, P(14, 16.5f), X(4.f * k), X(1.25f * k), col);
        frui_ring(u->c, P(26, 16.5f), X(4.f * k), X(1.25f * k), col);
        break;
    case 2:   /* felt: the tine and its tone bar, a soft round hammer below */
        FR(3, 3, 4, 10, 1, col);
        FR(7, 3.4f, 26, 2.6f, 1.2f, col);
        L(7, 11, 36, 11);
        L(22, 23, 22, 18.5f);
        frui_circle(u->c, P(22, 15.5f), X(3.2f * k), frui_alpha(col, 0.4f));
        frui_ring(u->c, P(22, 15.5f), X(3.2f * k), X(1.25f * k), col);
        break;
    case 3:   /* bell: the tine struck by a hard hammer, ringing */
        FR(3, 3, 4, 10, 1, col);
        FR(7, 3.4f, 20, 2.6f, 1.2f, col);
        L(7, 11, 28, 11);
        L(18, 23, 18, 16);
        FR(15, 13.2f, 6, 2.8f, 0.8f, col);
        frui_arc(u->c, P(28, 11), X(4.f * k), X(1.25f * k), -0.75f, 0.75f, col);
        frui_arc(u->c, P(28, 11), X(7.5f * k), X(1.25f * k), -0.7f, 0.7f, frui_alpha(col, 0.7f));
        frui_arc(u->c, P(28, 11), X(11.f * k), X(1.25f * k), -0.65f, 0.65f, frui_alpha(col, 0.45f));
        break;
    case 4:   /* vibraphone: metal bars over their resonator tubes */
        for (i = 0; i < 5; i++) {
            float bx = 3.f + 7.f * (float)i, th = 17.f - 2.f * (float)i;
            FR(bx, 2, 6, 3.5f, 1, col);
            RR(bx + 1.5f, 7, 3, th, 1.5f);
        }
        break;
    default:  /* marimba: wooden bars, long to short, on their frame */
        for (i = 0; i < 6; i++) {
            float bx = 3.f + 6.f * (float)i, bh = 17.f - 2.2f * (float)i;
            FR(bx, 3.f + (17.f - bh) * 0.5f, 4.6f, bh, 1.2f, frui_alpha(col, 0.85f));
        }
        L(2, 21, 38, 21); L(6, 21, 5, 24); L(34, 21, 35, 24);
        break;
    }
#undef P
#undef L
#undef RR
#undef FR
}

/* ---- the rail: the wordmark and the pilot lamp, the preset, the output meter and LEVEL */
static void meter(frui *u, float x, float y, float w, float h)
{
    const frui_model *m = u->m;
    int ch, i, nseg;
    float x0 = x + 18.f, bw = w - 26.f, segw = 3.f;
    region(u, FRR_METER, x, y, w, h);
    well(u, x, y, w, h, R_CTRL);
    nseg = (int)(bw / segw);
    for (ch = 0; ch < 2; ch++) {
        float by = y + h * 0.5f + (ch ? 2.f : -6.f), lv = (m->out_db[ch] + 48.f) / 48.f, pk = (m->out_peak_db[ch] + 48.f) / 48.f;
        int lit = (int)(lv * (float)nseg + 0.5f), pkk = (int)(pk * (float)nseg);
        txf(u, u->f, x + 7.f, by + 4.f, 7.f, 700.f, 0.f, 0, ch ? "R" : "L", C_TEXT3);
        for (i = 0; i < nseg; i++) {
            int hot = i >= nseg * 42 / 48;
            uint32_t col = i < lit || i == pkk ? (hot ? C_ACC : FRUI_RGB(226, 226, 222)) : FRUI_RGB(34, 34, 37);
            frui_rect(u->c, X(x0 + segw * (float)i), X(by), X(segw - 1.f), X(4.f), col);
        }
    }
}
/* SAVE's name field, in the preset box: NAME, the text (selected: the first key replaces it) and the caret, what
 * RETURN does (a new user preset, or REPLACE when a user preset of that name exists: it is overwritten) */
static int user_named(const frui_model *m, const char *s)
{
    int i;
    for (i = 0; i < m->pl_n && i < FRUI_PL_MAX; i++) {
        const char *a = m->pl[i].name, *b = s;
        if (!m->pl[i].user) continue;
        while (*a && *b && (*a >= 'a' && *a <= 'z' ? *a - 32 : *a) == (*b >= 'a' && *b <= 'z' ? *b - 32 : *b)) { a++; b++; }
        if (!*a && !*b) return 1;
    }
    return 0;
}
static void save_field(frui *u, float x, float y, float w, float h)
{
    const frui_model *m = u->m;
    float cy = y + h * 0.5f, xt = x + 50.f, wt = tw(u, 12.5f, 560.f, 0.6f, 0, m->save_text);
    int rp = m->save_text[0] && user_named(m, m->save_text);
    float avail = w - 50.f - 64.f;                   /* (a long name scrolls left: the caret stays in sight) */
    if (wt + 4.f > avail) xt -= wt + 4.f - avail;
    tx(u, x + 12.f, mid(u, cy, 8.5f), T_CAPTION, 0, "NAME", C_ACC);
    frui_clip(u->c, X(x + 46.f), X(y), X(avail + 4.f), X(h));
    if (m->save_sel && m->save_text[0])
        frui_rrect(u->c, X(xt - 3.f), X(cy - 9.f), X(wt + 6.f), X(18.f), X(R_SMALL), frui_alpha(C_ACC, 0.32f));
    tx(u, xt, mid(u, cy, 12.5f), 12.5f, 560.f, 0.6f, 0, m->save_text, C_TEXT);
    if (!m->save_sel || !m->save_text[0]) frui_rect(u->c, X(xt + wt + 2.f), X(cy - 8.f), X(1.2f), X(16.f), C_ACC);
    frui_clip_reset(u->c);
    txr(u, x + w - 12.f, mid(u, cy, 8.5f), T_CAPTION, 0, rp ? "REPLACE" : "RETURN", rp ? C_ACC : C_TEXT3);
}
static void rail(frui *u)
{
    const frui_model *m = u->m;
    float cy = u->look == FRUI_LOOK_A3 ? A3_BODY_Y * 0.5f : RAIL_H * 0.5f, y = cy - 16.f, h = 32.f, x;   /* A3: the metal's centre */
    if (u->look == FRUI_LOOK_RAIL) {
        frui_brushed(u->c, 0.f, 0.f, X((float)FRUI_W), X(RAIL_H), 0.f, C_AL_T, C_AL_B, 0.045f, u->s, 1u);
        frui_rect(u->c, 0.f, X(RAIL_H) - 1.f, X((float)FRUI_W), 1.f, FRUI_RGBA(255, 255, 255, 120));
        frui_rect(u->c, 0.f, X(RAIL_H), X((float)FRUI_W), 1.f, FRUI_RGB(0, 0, 0));
        {
            int i;
            for (i = 1; i < 8; i++) frui_rect(u->c, 0.f, X(RAIL_H) + (float)i, X((float)FRUI_W), 1.f, FRUI_RGBA(0, 0, 0, 140 - 18 * i));
        }
    } else if (u->look == FRUI_LOOK_PLATE) {   /* the plate: a machined groove under the rail */
        frui_rect(u->c, X(G_M), X(RAIL_H) - 1.f, X((float)FRUI_W - 2.f * G_M), 1.f, FRUI_RGBA(0, 0, 0, 90));
        frui_rect(u->c, X(G_M), X(RAIL_H), X((float)FRUI_W - 2.f * G_M), 1.f, FRUI_RGBA(255, 255, 255, 160));
    }
    /* FRODHES, cut into the metal, and the pilot lamp after it */
    {
        float ww = twf(u, u->fd, 17.f, 400.f, 3.2f, 0, "FRODHES") - 3.2f;
        engrave(u, u->fd, G_H0, midd(u, cy, 17.f), 17.f, 400.f, 3.2f, "FRODHES", C_ENGR);
        anchor(u, FRA_TITLE, 1, G_H0, cy);
        x = G_H0 + ww + 16.f;
        frui_circle(u->c, X(x), X(cy) + 1.f, X(6.f), C_HL);
        frui_circle(u->c, X(x), X(cy), X(6.f), FRUI_RGB(70, 71, 74));
        frui_spun(u->c, X(x), X(cy), X(5.f), FRUI_RGB(120, 122, 126), FRUI_RGB(240, 240, 240), 0.04f, u->s, 5u);
        frui_circle_shadow(u->c, X(x), X(cy), X(3.2f), X(4.f), FRUI_RGBA(255, 120, 10, 140));
        frui_circle_grad(u->c, X(x), X(cy), X(3.4f), FRUI_RGB(255, 196, 96), FRUI_RGB(214, 92, 4));
        frui_circle(u->c, X(x - 1.f), X(cy - 1.2f), X(1.1f), FRUI_RGBA(255, 244, 220, 220));
    }
    /* SETTINGS: the gear, a gutter's half before the preset */
    key_btn(u, FRB_GEAR, 0, colc(0, 2) - G_COL * 0.5f - 40.f, y, 32.f, h);
    if (m->settings) frui_rrect_stroke(u->c, X(colc(0, 2) - G_COL * 0.5f - 40.f) + 0.5f, X(y) + 0.5f, X(32.f) - 1.f, X(h) - 1.f,
                                       X(R_CTRL), 1.f, C_ACC);
    /* the preset: < [tag name (edited) v] > SAVE UNDO, from column 3 of the left half */
    x = colc(0, 2) - G_COL * 0.5f;
    key_btn(u, FRB_PREV, 0, x, y, 32.f, h);
    x += 36.f;
    {
        float w = 264.f;
        int open = m->presets || m->saving || is_hover(u, FRW_PRESET, 0);
        well(u, x, y, w, h, R_CTRL);
        if (open) frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_CTRL), 1.f,
                                    m->presets || m->saving ? C_ACC : C_EDGE);
        if (m->saving) save_field(u, x, y, w, h);
        else {
            tx(u, x + 12.f, mid(u, cy, 12.5f), 12.5f, 640.f, 0.4f, FRUI_TNUM, m->preset_tag, C_ACC);
            frui_clip(u->c, X(x + 50.f), X(y), X(w - 50.f - (m->edited ? 76.f : 26.f)), X(h));
            tx(u, x + 50.f, mid(u, cy, 12.5f), 12.5f, 560.f, 0.6f, 0, m->preset_name, C_TEXT);
            frui_clip_reset(u->c);
            if (m->edited) txr(u, x + w - 30.f, mid(u, cy, 8.5f), T_CAPTION, 0, "EDITED", C_TEXT3);
            chevron(u, x + w - 15.f, cy, 3.f, 0, m->presets ? C_ACC : C_TEXT2);
        }
        reg(u, FRW_PRESET, 0, x, y, w, h);
        x += w + 4.f;
    }
    key_btn(u, FRB_NEXT, 0, x, y, 32.f, h);
    x = colc(1, 1) - G_COL * 0.5f;                     /* 608: the right half's second column */
    key_btn(u, FRB_SAVE, "SAVE", x, y, 52.f, h);
    if (m->saving) frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(52.f) - 1.f, X(h) - 1.f, X(R_CTRL), 1.f, C_ACC);
    key_btn(u, FRB_UNDO, "UNDO", x + 56.f, y, 52.f, h);
    meter(u, colc(1, 2) - G_COL * 0.5f + 12.f, y, 100.f, h);
    /* LEVEL: the preset's volume trim (P_LEVEL), its knob's edge on the grid's right edge */
    {
        float r = KNOB_R2, kx = G_H1 + 4.f * G_COL - 16.f, v = (float)m->p[FRP_LEVEL] / 127.f;
        char val[24], unit[12], buf[40];
        u->text(u->ctx, m, FRP_LEVEL, val, unit);
        snprintf(buf, sizeof buf, "%s %s", val, unit);
        knob_body(u, kx, cy, r, v, 1, 0, 0, 1, is_hover(u, FRW_KNOB, FRP_LEVEL));
        if (is_focus(u, FRW_KNOB, FRP_LEVEL)) frui_ring(u->c, X(kx), X(cy), X(r + 4.f), X(1.f), C_ACC);
        txr(u, kx - r - 10.f, cy - 3.f, T_CAPTION, 0, "LEVEL", C_ENGR2);
        txr(u, kx - r - 10.f, cy + 10.f, 11.f, 640.f, 0.f, FRUI_TNUM, buf, C_ENGR);
        reg(u, FRW_KNOB, FRP_LEVEL, kx - 16.f, cy - 16.f, 32.f, 32.f);
    }
}

/* ---- VOICE: the instrument (a compact selector with its drawing) and its eight knobs */
static void type_selector(frui *u, float x, float y, float h)
{
    const frui_model *m = u->m;
    int t = m->p[FRP_TYPE], open = m->menu == FRUI_WID(FRW_MENU, FRP_TYPE);
    float bw = 152.f, bx = x + 32.f;
    key_btn(u, FRB_TYPE_PREV, 0, x, y, 28.f, h);
    ctrl_box(u, bx, y, bw, h, open || is_hover(u, FRW_MENU, FRP_TYPE), open);
    eicon(u, t, bx + 8.f, y + (h - 24.f * 0.75f) * 0.5f, 0.75f, C_TEXT);
    tx(u, bx + 46.f, mid(u, y + h * 0.5f, 10.5f), T_MENU, 0, FRN_TYPE[t <= 5 ? t : 0], C_TEXT);
    chevron(u, bx + bw - 12.f, y + h * 0.5f, 3.f, 0, C_TEXT2);
    if (is_focus(u, FRW_MENU, FRP_TYPE))
        frui_rrect_stroke(u->c, X(bx - 3.f), X(y - 3.f), X(bw + 6.f), X(h + 6.f), X(R_CTRL + 3.f), X(1.f), C_ACC);
    reg(u, FRW_MENU, FRP_TYPE, bx, y, bw, h);
    key_btn(u, FRB_TYPE_NEXT, 0, bx + bw + 4.f, y, 28.f, h);
}
static void voice(frui *u)
{
    const frui_model *m = u->m;
    float x = G_M, y = VO_Y, w = (float)FRUI_W - 2.f * G_M, h = VO_H, cy = y + 124.f;
    static const int ids0[4] = {FRP_TONE, FRP_BARK, FRP_BELL, FRP_HAMMER};
    static const int ids1[4] = {FRP_DECAY, FRP_RELEASE, FRP_NOISE, FRP_TUNE};
    static const char *const rh0[4] = {"TONE", "BARK", "BELL", "HAMMER"}, *const rh1[4] = {"DECAY", "RELEASE", "NOISE", "TUNE"};
    static const char *const bv0[4] = {"STRIKE", "MALLET", "TUBE", "WEIGHT"}, *const bv1[4] = {"RING", "DAMPER", "NOISE", "TUNE"};
    static const char *const bm1[4] = {"RING", "MUTE", "NOISE", "TUNE"};
    const char *cap0, *cap1;
    panel(u, x, y, w, h);
    section_title(u, G_H0, y + 22.f, "VOICE");
    type_selector(u, G_H1 + 4.f * G_COL - 216.f, y + 8.f, 28.f);
    hair_h(u, x + 1.f, x + w - 1.f, y + 44.f, FRUI_RGB(32, 32, 34));
    hair_v(u, (float)FRUI_W * 0.5f, y + 60.f, y + h - 16.f, FRUI_RGB(32, 32, 34));
    if (!is_bars(m)) { cap0 = "HOW THE TINE IS STRUCK"; cap1 = "HOW NOTES DIE AWAY"; }
    else if (has_motor(m)) { cap0 = "HOW THE MALLET STRIKES"; cap1 = "HOW BARS RING AND STOP"; }
    else { cap0 = "HOW THE MALLET STRIKES"; cap1 = "HOW BARS RING; MUTE = HAND"; }
    txc(u, G_H0 + 2.f * G_COL, mid(u, y + 60.f, 8.5f), T_CAPTION, 0, cap0, C_TEXT3);
    anchor(u, FRA_CAPTION, 0, G_H0 + 2.f * G_COL, y + 60.f);
    anchor(u, FRA_CAPTION, 1, G_H1 + 2.f * G_COL, y + 60.f);
    txc(u, G_H1 + 2.f * G_COL, mid(u, y + 60.f, 8.5f), T_CAPTION, 0, cap1, C_TEXT3);
    knob_row(u, 0, ids0, is_bars(m) ? bv0 : rh0, 4, cy, 1);
    knob_row(u, 1, ids1, !is_bars(m) ? rh1 : has_motor(m) ? bv1 : bm1, 4, cy, 1);
}

/* ---- the effect panels */
static int eff_on(const frui_model *m, int tab, int right)
{
    if (!right)
        switch (tab) {
        case FRT_MOTOR: return m->p[FRP_MOT_ON] != 0;
        case FRT_COMP: return m->p[FRP_CMP_SW] != 0;
        case FRT_EQ: return m->p[FRP_EQ_LO] != 64 || m->p[FRP_EQ_MID] != 64 || m->p[FRP_EQ_HI] != 64;
        default: return m->p[FRP_DRV_SW] != 0 && m->p[FRP_DRIVE] != 0;
        }
    switch (tab) {
    case FRT_FILTER: return m->p[FRP_FL_TYPE] != 0 && (m->p[FRP_FL_FREQ] < 127 || m->p[FRP_FL_RESO] || m->p[FRP_FL_LFO] ||
                                                   m->p[FRP_FL_TYPE] > 2);
    case FRT_TREM: return m->p[FRP_TW_SW] != 0;
    case FRT_PHASER: return m->p[FRP_PH_SW] != 0;
    case FRT_CHORUS: return m->p[FRP_CP_SW] != 0;
    case FRT_DELAY: return m->p[FRP_DL_SW] != 0;
    default: return m->p[FRP_RV_TYPE] != 0;
    }
}
static const char *const TAB_L[FRT_NL] = {"MOTOR", "COMP", "EQ", "DRIVE"};
static const char *const TAB_R[FRT_NR] = {"FILTER", "TREM/WAH", "PHASER", "CHORUS", "DELAY", "REVERB"};
/* the tabs: from the padding's left edge, equal gaps, the last ending on the padding's right edge when they fill it */
static void tabs(frui *u, int right, float x, float y, float w)
{
    const frui_model *m = u->m;
    const char *const *names = right ? TAB_R : TAB_L;
    int n = right ? FRT_NR : FRT_NL, first = right || has_motor(m) ? 0 : 1, sel = right ? m->tab_r : m->tab_l, i;
    float tot = 0.f, gap, cx, cy = y + 22.f;
    for (i = first; i < n; i++) tot += tw(u, T_TAB, 0, names[i]) + 11.f;
    gap = n - first > 1 ? (w - 2.f * G_PAD - 20.f - tot) / (float)(n - first - 1) : 0.f;   /* the keys' 10 px sides */
    if (gap > 28.f) gap = 28.f;
    cx = x + G_PAD + 10.f;
    for (i = first; i < n; i++) {
        float cw = tw(u, T_TAB, 0, names[i]) + 11.f;
        int kind = right ? FRW_TAB_R : FRW_TAB_L;
        if (i == sel) {
            float kx = cx - 10.f, kw = cw + 20.f, ky = y + 8.f, kh = 28.f;
            if (u->look != FRUI_LOOK_A3)
                frui_rrect_shadow(u->c, X(kx), X(ky + 1.5f), X(kw), X(kh), X(R_CTRL), X(2.f), FRUI_RGBA(0, 0, 0, 200));
            frui_rrect(u->c, X(kx), X(ky), X(kw), X(kh), X(R_CTRL), FRUI_RGB(4, 4, 5));
            frui_rrect_grad(u->c, X(kx) + 1.f, X(ky) + 1.f, X(kw) - 2.f, X(kh) - 2.f, X(R_CTRL) - 1.f, FRUI_RGB(44, 44, 47),
                            FRUI_RGB(26, 26, 28));
            frui_rect(u->c, X(kx + R_CTRL), X(ky) + 1.f, X(kw - 2.f * R_CTRL), 1.f, FRUI_RGBA(255, 255, 255, 46));
        }
        /* (BYPASS: every effect's lamp out; MOTOR is the instrument's own, never bypassed) */
        lamp(u, cx + 2.5f, cy, 2.5f, eff_on(m, i, right) && (!m->fx_bypass || (!right && i == FRT_MOTOR)));
        anchor(u, FRA_TAB, i, cx, mid(u, cy, 10.5f));
        tx(u, cx + 11.f, mid(u, cy, 10.5f), T_TAB, 0, names[i],
           i == sel ? C_TEXT : is_hover(u, kind, i) ? frui_mix(C_TEXT2, C_TEXT, 0.5f) : C_TEXT2);
        reg(u, kind, i, cx - gap * 0.5f, y, cw + gap, 44.f);
        cx += cw + gap;
    }
    hair_h(u, x + 1.f, x + w - 1.f, y + 44.f, FRUI_RGB(32, 32, 34));
}

/* the EQ's response (fx_eq.c: LOW shelf 120 Hz, MID bell Q 0.9 at MID FREQ, HIGH shelf 5 kHz), RBJ biquads at 44.1k */
static float eq_gain_db(int v) { int e = v ? v - 64 : -63; return (float)e * 15.f / 63.f; }
static void biq(int type, double f0, double gdb, double q, double *b, double *a)
{
    double A = pow(10.0, gdb / 40.0), w0 = 2.0 * M_PI * f0 / 44100.0, cw = cos(w0), sw = sin(w0);
    double al = sw / (2.0 * q), sA = 2.0 * sqrt(A) * al;
    if (type == 0) {         /* low shelf */
        b[0] = A * ((A + 1) - (A - 1) * cw + sA); b[1] = 2 * A * ((A - 1) - (A + 1) * cw); b[2] = A * ((A + 1) - (A - 1) * cw - sA);
        a[0] = (A + 1) + (A - 1) * cw + sA; a[1] = -2 * ((A - 1) + (A + 1) * cw); a[2] = (A + 1) + (A - 1) * cw - sA;
    } else if (type == 2) {  /* high shelf */
        b[0] = A * ((A + 1) + (A - 1) * cw + sA); b[1] = -2 * A * ((A - 1) + (A + 1) * cw); b[2] = A * ((A + 1) + (A - 1) * cw - sA);
        a[0] = (A + 1) - (A - 1) * cw + sA; a[1] = 2 * ((A - 1) - (A + 1) * cw); a[2] = (A + 1) - (A - 1) * cw - sA;
    } else {                 /* peak */
        b[0] = 1 + al * A; b[1] = -2 * cw; b[2] = 1 - al * A; a[0] = 1 + al / A; a[1] = -2 * cw; a[2] = 1 - al / A;
    }
}
static double biq_mag(const double *b, const double *a, double f)
{
    double w = 2.0 * M_PI * f / 44100.0, c1 = cos(w), s1 = sin(w), c2 = cos(2 * w), s2 = sin(2 * w);
    double nr = b[0] + b[1] * c1 + b[2] * c2, ni = -(b[1] * s1 + b[2] * s2);
    double dr = a[0] + a[1] * c1 + a[2] * c2, di = -(a[1] * s1 + a[2] * s2);
    return sqrt((nr * nr + ni * ni) / (dr * dr + di * di));
}
static void eq_curve(frui *u, float x, float y, float w, float h)
{
    const frui_model *m = u->m;
    double b[3][3], a[3][3];
    double fm = 200.0 * pow(25.0, (double)m->p[FRP_EQ_FREQ] / 127.0);
    float px = 0.f, py = 0.f, cy = y + h * 0.5f, k = (h * 0.5f - 3.f) / 15.f;
    int i, n = 160;
    static const float grid[3] = {100.f, 1000.f, 10000.f};
    static const char *const gl[3] = {"100", "1K", "10K"};
    region(u, FRR_EQ, x, y, w, h);
    ctrl_box(u, x, y, w, h, 0, 0);
    for (i = 0; i < 3; i++) {
        float gx = x + w * (log10f(grid[i] / 20.f) / 3.f);
        hair_v(u, gx, y + 4.f, y + h - 4.f, FRUI_RGB(30, 30, 32));
        tx(u, gx + 3.f, y + h - 4.f, 7.f, 600.f, 0.6f, 0, gl[i], C_TEXT3);
    }
    hair_h(u, x + 4.f, x + w - 4.f, cy, C_EDGE);
    biq(0, 120.0, eq_gain_db(m->p[FRP_EQ_LO]), 0.7071, b[0], a[0]);
    biq(1, fm, eq_gain_db(m->p[FRP_EQ_MID]), 0.9, b[1], a[1]);
    biq(2, 5000.0, eq_gain_db(m->p[FRP_EQ_HI]), 0.7071, b[2], a[2]);
    frui_clip(u->c, X(x + 1.f), X(y + 1.f), X(w - 2.f), X(h - 2.f));
    {
        frui_path p;
        frui_path_init(&p);
        frui_path_move(&p, X(x), X(cy));
        for (i = 0; i <= n; i++) {
            double f = 20.0 * pow(1000.0, (double)i / n), g = 1.0;
            int j;
            float dbv, qx, qy;
            for (j = 0; j < 3; j++) g *= biq_mag(b[j], a[j], f);
            dbv = (float)(20.0 * log10(g));
            qx = x + w * (float)i / (float)n; qy = cy - dbv * k;
            frui_path_line(&p, X(qx), X(qy));
            if (i) frui_line(u->c, X(px), X(py), X(qx), X(qy), X(1.4f), C_TEXT);
            px = qx; py = qy;
        }
        frui_path_line(&p, X(x + w), X(cy));
        frui_path_fill(u->c, &p, FRUI_RGBA(255, 255, 255, 22));
        frui_path_free(&p);
    }
    frui_clip_reset(u->c);
}

static void gr_meter(frui *u, float x, float y, float w)
{
    char buf[16];
    float g = u->m->gr_db, k = g / 12.f, bx = x + 26.f, bw = w - 26.f - 60.f, segw = 3.f;
    int i, n = (int)(bw / segw), lit = (int)(k * (float)n + 0.5f);
    region(u, FRR_GR, x, y - 10.f, w, 20.f);
    tx(u, x, mid(u, y, 8.5f), T_CAPTION, 0, "GR", C_TEXT3);
    for (i = 0; i < n; i++)
        frui_rect(u->c, X(bx + segw * (float)i), X(y - 2.f), X(segw - 1.f), X(4.f), i < lit ? C_ACC : FRUI_RGB(34, 34, 37));
    snprintf(buf, sizeof buf, g >= 0.05f ? "-%.1f dB" : "0.0 dB", (double)g);
    txr(u, x + w, mid(u, y, 10.5f), 10.5f, 600.f, 0.f, FRUI_TNUM, buf, C_TEXT);
}

static void pager(frui *u, float rx, float y, float h, int page)
{
    char buf[16];                                   /* (an int's 11 characters + " / 2": Linux gcc's
                                                     * -Wformat-truncation) */
    float w = 100.f, x = rx - w;
    ctrl_box(u, x, y, w, h, 0, 0);
    chevron(u, x + 14.f, y + h * 0.5f, 3.5f, 1, page > 0 ? C_TEXT : C_DIM);
    chevron(u, x + w - 14.f, y + h * 0.5f, 3.5f, 2, page < 1 ? C_TEXT : C_DIM);
    snprintf(buf, sizeof buf, "%d / 2", page + 1);
    txc(u, x + w * 0.5f, mid(u, y + h * 0.5f, 10.5f), T_MENU, FRUI_TNUM, buf, C_TEXT);
    reg(u, FRW_PAGER, FRT_REVERB, x, y, w, h);
}

/* a panel's control row: the padding's left edge to its right; a menu fills one column less the gutter */
#define ROW_Y (FX_Y + 56.f)
#define ROW_H 28.f
#define FX_CY (FX_Y + 144.f)
#define MENU_W (G_COL - 8.f)
static void left_panel(frui *u)
{
    const frui_model *m = u->m;
    float x = G_M, y = FX_Y, w = 456.f, h = FX_H, x0 = G_H0, rx = G_H0 + 4.f * G_COL;
    int tab = m->tab_l;
    if (tab == FRT_MOTOR && !has_motor(m)) tab = FRT_COMP;
    region(u, FRR_PANEL_L, x, y, w, h);
    panel(u, x, y, w, h);
    tabs(u, 0, x, y, w);
    switch (tab) {
    case FRT_MOTOR: {
        static const int ids[3] = {FRP_MOT_DEPTH, FRP_MOT_RATE, FRP_MOT_SHAPE};
        static const char *const lab[3] = {"DEPTH", "SPEED", "SHAPE"};
        segmented(u, FRP_MOT_ON, x0, ROW_Y, ROW_H, 0.f);
        menu_btn(u, FRP_MOT_SYNC, "SYNC", rx - MENU_W, ROW_Y, ROW_H, MENU_W);
        knob_row(u, 0, ids, lab, 3, FX_CY, m->p[FRP_MOT_ON] != 0);
        break;
    }
    case FRT_COMP: {
        static const int ids[4] = {FRP_CMP_AMT, FRP_CMP_RATIO, FRP_CMP_ATK, FRP_CMP_GAIN};
        static const char *const lab[4] = {"THRESH", "RATIO", "ATTACK", "MAKEUP"};
        float sw = segmented(u, FRP_CMP_SW, x0, ROW_Y, ROW_H, 0.f);
        gr_meter(u, G_H0 + G_COL + 4.f, ROW_Y + ROW_H * 0.5f, rx - (G_H0 + G_COL + 4.f));
        (void)sw;
        knob_row(u, 0, ids, lab, 4, FX_CY, m->p[FRP_CMP_SW] != 0 && !m->fx_bypass);
        break;
    }
    case FRT_EQ: {
        static const int ids[4] = {FRP_EQ_LO, FRP_EQ_MID, FRP_EQ_FREQ, FRP_EQ_HI};
        static const char *const lab[4] = {"LOW", "MID", "MID FREQ", "HIGH"};
        eq_curve(u, x0, ROW_Y - 4.f, rx - x0, ROW_H + 8.f);
        knob_row(u, 0, ids, lab, 4, FX_CY, !m->fx_bypass);
        break;
    }
    default: {
        static const int ids[3] = {FRP_DRIVE, FRP_DRV_TONE, FRP_DRV_MIX};
        static const char *const lab[3] = {"AMOUNT", "TONE", "MIX"};
        segmented(u, FRP_DRV_SW, x0, ROW_Y, ROW_H, rx - x0);
        knob_row(u, 0, ids, lab, 3, FX_CY, m->p[FRP_DRV_SW] != 0 && !m->fx_bypass);
        break;
    }
    }
}

static void right_panel(frui *u)
{
    const frui_model *m = u->m;
    float x = G_H1 - G_PAD, y = FX_Y, w = 456.f, h = FX_H, x0 = G_H1, rx = G_H1 + 4.f * G_COL;
    region(u, FRR_PANEL_R, x, y, w, h);
    panel(u, x, y, w, h);
    tabs(u, 1, x, y, w);
#define SYNC_BTN(id) menu_btn(u, (id), "SYNC", rx - MENU_W, ROW_Y, ROW_H, MENU_W)
    switch (m->tab_r) {
    case FRT_FILTER: {
        static const int ids[4] = {FRP_FL_FREQ, FRP_FL_RESO, FRP_FL_LFO, FRP_LFO_RATE};
        static const char *const lab[4] = {"FREQ", "RESO", "LFO", "LFO RATE"};
        menu_btn(u, FRP_FL_TYPE, "TYPE", x0, ROW_Y, ROW_H, MENU_W);
        menu_btn(u, FRP_LFO_SHAPE, "SHAPE", x0 + G_COL, ROW_Y, ROW_H, MENU_W);
        menu_btn(u, FRP_LFO_RETRIG, "TRIG", x0 + 2.f * G_COL, ROW_Y, ROW_H, MENU_W);
        SYNC_BTN(FRP_LFO_SYNC);
        knob_row(u, 1, ids, lab, 4, FX_CY, m->p[FRP_FL_TYPE] != 0 && !m->fx_bypass);
        break;
    }
    case FRT_TREM: {
        static const int ids[3] = {FRP_TW_DEPTH, FRP_TW_RATE, FRP_TW_SHAPE};
        static const char *const lab[3] = {"DEPTH", "RATE", "SHAPE"};
        segmented(u, FRP_TW_KIND, x0, ROW_Y, ROW_H, 0.f);
        SYNC_BTN(FRP_TW_SYNC);
        knob_row(u, 1, ids, lab, 3, FX_CY, m->p[FRP_TW_SW] != 0 && !m->fx_bypass);
        break;
    }
    case FRT_PHASER: {
        static const int ids[3] = {FRP_PH_DEPTH, FRP_PH_SPEED, FRP_PH_FDBK};
        static const char *const lab[3] = {"DEPTH", "SPEED", "FEEDBK"};
        segmented(u, FRP_PH_SW, x0, ROW_Y, ROW_H, 0.f);
        SYNC_BTN(FRP_PH_SYNC);
        knob_row(u, 1, ids, lab, 3, FX_CY, m->p[FRP_PH_SW] != 0 && !m->fx_bypass);
        break;
    }
    case FRT_CHORUS: {
        static const int ids[2] = {FRP_CP_DEPTH, FRP_CP_SPEED};
        static const char *const lab[2] = {"DEPTH", "SPEED"};
        segmented(u, FRP_CP_SW, x0, ROW_Y, ROW_H, 0.f);
        SYNC_BTN(FRP_CP_SYNC);
        knob_row(u, 1, ids, lab, 2, FX_CY, m->p[FRP_CP_SW] != 0 && !m->fx_bypass);
        break;
    }
    case FRT_DELAY: {
        static const int ids[4] = {FRP_DL_DEPTH, FRP_DL_TIME, FRP_DL_FDBK, FRP_DL_TONE};
        static const char *const lab[4] = {"MIX", "TIME", "FEEDBK", "TONE"};
        segmented(u, FRP_DL_SW, x0, ROW_Y, ROW_H, 0.f);
        SYNC_BTN(FRP_DL_SYNC);
        knob_row(u, 1, ids, lab, 4, FX_CY, m->p[FRP_DL_SW] != 0 && !m->fx_bypass);
        break;
    }
    default: {
        static const int ids0[3] = {FRP_RV_DEPTH, FRP_RV_DECAY, FRP_RV_TONE};
        static const char *const lab0[3] = {"MIX", "DECAY", "TONE"};
        static const int ids1[4] = {FRP_RV_PREDLY, FRP_RV_SIZE, FRP_RV_MOD, FRP_RV_LOCUT};
        static const char *const lab1[4] = {"PRE-DELAY", "SIZE", "MOD", "LOW CUT"};
        segmented(u, FRP_RV_TYPE, x0, ROW_Y, ROW_H, 0.f);
        pager(u, rx, ROW_Y, ROW_H, m->page_rv);
        if (m->page_rv) knob_row(u, 1, ids1, lab1, 4, FX_CY, m->p[FRP_RV_TYPE] != 0 && !m->fx_bypass);
        else knob_row(u, 1, ids0, lab0, 3, FX_CY, m->p[FRP_RV_TYPE] != 0 && !m->fx_bypass);
        break;
    }
    }
#undef SYNC_BTN
}

/* ---- the keyboard: C1 .. C6 (MIDI 24 .. 84), the held notes lit */
static void keyboard(frui *u)
{
    const frui_model *m = u->m;
    float x = KB_X, y = KB_Y, w = (float)FRUI_W - G_M - KB_X, h = KB_H, ww = w / 36.f, bw = ww * 0.58f, bh = h * 0.6f;
    static const int white_of[12] = {0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6};
    int n;
    if (u->look == FRUI_LOOK_PLATE) {
        frui_rrect(u->c, X(x - 2.f), X(y - 2.f) + 1.f, X(w + 4.f), X(h + 4.f), X(R_PANEL), C_HL);
        frui_rrect(u->c, X(x - 2.f), X(y - 2.f), X(w + 4.f), X(h + 4.f), X(R_PANEL), FRUI_RGB(70, 71, 74));
    }
    if (u->look == FRUI_LOOK_A3) shell(u, x, y, w, h, R_PANEL);
    frui_rrect(u->c, X(x - 1.f), X(y - 1.f), X(w + 2.f), X(h + 2.f), X(R_PANEL + (u->look == FRUI_LOOK_A3 ? 1.f : -1.f)),
               FRUI_RGB(3, 3, 4));
    frui_clip(u->c, X(x), X(y), X(w), X(h));
    for (n = 24; n <= 84; n++) {
        int pc = n % 12, oct = n / 12 - 1, wi = white_of[pc];
        if (wi >= 0) {
            float kx = x + ww * (float)((oct - 1) * 7 + wi);
            int on = m->notes[n] != 0;
            frui_rrect_grad(u->c, X(kx) + 1.f, X(y - 6.f), X(ww) - 1.f, X(h + 6.f), X(3.f),
                            on ? FRUI_RGB(246, 228, 202) : FRUI_RGB(240, 239, 234), on ? FRUI_RGB(236, 186, 122) : FRUI_RGB(206, 205, 200));
            frui_rect(u->c, X(kx) + 1.f, X(y + h - 4.f), X(ww) - 1.f, X(4.f), on ? FRUI_RGBA(140, 70, 0, 60) : FRUI_RGBA(0, 0, 0, 30));
            if (on) frui_rrect(u->c, X(kx + ww * 0.5f - 5.f), X(y + h - 9.f), X(10.f), X(2.f), X(1.f), C_ACC);
            if (pc == 0) {
                char lb[8];
                snprintf(lb, sizeof lb, "C%d", oct);
                txc(u, kx + ww * 0.5f, y + h - 14.f, 7.5f, 640.f, 0.4f, 0, lb, on ? FRUI_RGB(120, 70, 20) : FRUI_RGB(140, 139, 136));
            }
        }
    }
    for (n = 24; n <= 84; n++) {
        int pc = n % 12, oct = n / 12 - 1;
        if (white_of[pc] < 0) {
            float kx = x + ww * (float)((oct - 1) * 7 + white_of[pc - 1] + 1) - bw * 0.5f;
            int on = m->notes[n] != 0;
            frui_shadow_hole_rrect(u->c, X(kx), X(y - 6.f), X(bw), X(bh + 6.f), X(2.5f));
            frui_rrect_shadow(u->c, X(kx + 1.f), X(y), X(bw), X(bh), X(2.f), X(2.f), FRUI_RGBA(0, 0, 0, 120));
            frui_rrect(u->c, X(kx), X(y - 6.f), X(bw), X(bh + 6.f), X(2.5f), FRUI_RGB(4, 4, 5));
            frui_rrect_grad(u->c, X(kx + 1.5f), X(y - 6.f), X(bw - 3.f), X(bh + 6.f - 5.f), X(2.f),
                            FRUI_RGB(20, 20, 22), on ? FRUI_RGB(96, 56, 14) : FRUI_RGB(44, 44, 48));
            frui_rect(u->c, X(kx + 2.f), X(y + bh - 5.f), X(bw - 4.f), 1.f, FRUI_RGBA(255, 255, 255, on ? 90 : 50));
            if (on) frui_rrect(u->c, X(kx + bw * 0.5f - 3.f), X(y + bh - 10.f), X(6.f), X(2.f), X(1.f), C_ACC);
        }
    }
    {   /* the keys run under the body: a soft shadow along the top */
        int i;
        for (i = 0; i < 8; i++) frui_rect(u->c, X(x), X(y) + (float)i * u->s, X(w), u->s, FRUI_RGBA(0, 0, 0, 150 - 18 * i));
    }
    if (u->look == FRUI_LOOK_A3) {   /* the keys' bed is rounded as the sections */
        corner_cut(u, x, y, R_PANEL, 0, FRUI_RGB(3, 3, 4));
        corner_cut(u, x + w, y, R_PANEL, 1, FRUI_RGB(3, 3, 4));
        corner_cut(u, x + w, y + h, R_PANEL, 2, FRUI_RGB(3, 3, 4));
        corner_cut(u, x, y + h, R_PANEL, 3, FRUI_RGB(3, 3, 4));
    }
    frui_clip_reset(u->c);
    reg(u, FRW_KEYS, 0, x, y, w, h);
    region(u, FRR_KEYS, x - 2.f, y - 2.f, w + 4.f, h + 4.f);
}

/* ---- the switches beside the keys: SUSTAIN (latching, as the device's REC: tap to latch, tap again to let go; lit
 * while the sustain is down from any source) and FX (every effect bypassed at once, their own switches unchanged:
 * the device's FX button; lit while the effects are active) */
static void stomp(frui *u, int id, float x, float y, float w, float h, int lit, const char *label, const char *state)
{
    int hov = is_hover(u, FRW_BUTTON, id);
    if (u->look == FRUI_LOOK_A3) {   /* the switches sit in the body's corner: the sections' radius, a flat face */
        shell(u, x, y, w, h, R_PANEL);
        frui_rrect(u->c, X(x), X(y) + 1.f, X(w), X(h), X(R_PANEL), FRUI_RGBA(0, 0, 0, 200));
        frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_PANEL), FRUI_RGB(50, 50, 53));
        frui_rrect_grad(u->c, X(x), X(y) + 1.f, X(w), X(h) - 1.f, X(R_PANEL), hov ? FRUI_RGB(36, 36, 38) : FRUI_RGB(30, 30, 32),
                        FRUI_RGB(20, 20, 21));
    } else {
        frui_rrect_shadow(u->c, X(x), X(y + 2.f), X(w), X(h), X(R_CTRL), X(2.f), FRUI_RGBA(0, 0, 0, 170));
        frui_rrect(u->c, X(x), X(y), X(w), X(h), X(R_CTRL), FRUI_RGB(4, 4, 5));
        frui_rrect_grad(u->c, X(x) + 1.f, X(y) + 1.f, X(w) - 2.f, X(h) - 2.f, X(R_CTRL) - 1.f,
                        hov ? FRUI_RGB(48, 48, 51) : FRUI_RGB(40, 40, 43), FRUI_RGB(18, 18, 19));
        frui_rect(u->c, X(x + R_CTRL), X(y) + 1.f, X(w - 2.f * R_CTRL), 1.f, FRUI_RGBA(255, 255, 255, 44));
    }
    lamp(u, x + 12.f, y + h * 0.5f, 3.f, lit);
    tx(u, x + 22.f, mid(u, y + h * 0.5f - 5.f, 9.5f), T_BTN, 0, label, C_TEXT);
    tx(u, x + 22.f, mid(u, y + h * 0.5f + 7.f, 8.5f), T_CAPTION, 0, state, lit ? C_ACC : C_TEXT3);
    reg(u, FRW_BUTTON, id, x, y, w, h);
}
static void pedal_icon(frui *u, float x, float y, uint32_t col)     /* a piano pedal, side view: the foot plate on its pivot */
{
    frui_line(u->c, X(x), X(y + 12.f), X(x + 16.f), X(y + 12.f), X(1.3f), col);
    frui_line(u->c, X(x + 3.f), X(y + 9.f), X(x + 16.f), X(y + 4.f), X(2.4f), col);
    frui_circle(u->c, X(x + 4.f), X(y + 9.5f), X(1.6f), col);
    frui_line(u->c, X(x + 4.f), X(y + 9.5f), X(x + 4.f), X(y + 12.f), X(1.3f), col);
}
static void switches(frui *u)
{
    const frui_model *m = u->m;
    float x = G_M, w = KB_X - G_M - G_M, h = 36.f;
    stomp(u, FRB_SUSTAIN, x, KB_Y, w, h, m->sustain, "SUSTAIN", m->sustain ? "HELD" : "PEDAL");
    pedal_icon(u, x + w - 26.f, KB_Y + 11.f, m->sustain ? C_TEXT : C_TEXT2);
    stomp(u, FRB_FX, x, KB_Y + KB_H - h, w, h, !m->fx_bypass, "EFFECTS", m->fx_bypass ? "BYPASS" : "ON");
}

/* ---- SETTINGS: a sheet over the main view (the rail stays): MIDI on the left half, TEMPO and the CC map on the
 * right; rows of 28 every 44 px from y 148, the labels on the halves' first column, the controls from the second */
#define SH_Y VO_Y
#define SH_H (FX_Y + FX_H - VO_Y)
#define SH_ROW(k) (148.f + 44.f * (float)(k))
static void row_label(frui *u, float x, float yc, const char *s, int dim)
{
    anchor(u, FRA_ROWLABEL, 0, x, yc);
    tx(u, x, mid(u, yc, 9.5f), T_LABEL, 0, s, dim ? C_TEXT3 : C_TEXT2);
}
static void group_caption(frui *u, float x, float yc, const char *s)
{
    anchor(u, FRA_ROWLABEL, 1, x, yc);
    txf(u, u->fd, x, midd(u, yc, 8.5f), 8.5f, 400.f, 2.f, 0, s, C_TEXT);
}
static void fader(frui *u, int id, float x, float y, float w, float h, int dim)
{
    float v = (float)pval(u->m, id) / (float)phi(id);
    ctrl_box(u, x, y, w, h, is_hover(u, FRW_FADER, id), 0);
    if (v > 0.f) {
        if (dim) frui_rrect(u->c, X(x + 3.f), X(y + 3.f), X((w - 6.f) * v), X(h - 6.f), X(R_SMALL), FRUI_RGB(44, 44, 47));
        else alu(u, x + 3.f, y + 3.f, (w - 6.f) * v, h - 6.f, R_SMALL, (uint32_t)id);
    }
    reg(u, FRW_FADER, id, x, y, w, h);
}
static void info(frui *u, float x, float yc, const char *s)
{
    tx(u, x, mid(u, yc, 9.5f), 9.5f, 560.f, 0.6f, 0, s, C_TEXT2);
}
/* the default CC map (the device's: MANUAL.md section 12), for the table */
static const struct { uint8_t cc; const char *name; } CCMAP[] = {
    {12, "PHASER ON"}, {13, "PHASER DEPTH"}, {14, "PHASER SPEED"}, {15, "PHASER SYNC"}, {16, "PHASER FEEDBACK"},
    {17, "TREM/WAH"}, {18, "TREM DEPTH"}, {19, "TREM RATE"}, {20, "TREM SYNC"}, {21, "TREM MODE"}, {22, "TREM SHAPE"},
    {23, "CHORUS SYNC"}, {25, "DELAY SYNC"}, {26, "DELAY FEEDBACK"}, {27, "DELAY TONE"}, {28, "TONE"}, {29, "BARK"},
    {30, "BELL"}, {31, "HAMMER"}, {34, "EQ LOW"}, {35, "EQ MID"}, {36, "EQ MID FREQ"}, {37, "EQ HIGH"},
    {47, "MOTOR ON"}, {48, "MOTOR DEPTH"}, {49, "MOTOR SPEED"}, {50, "MOTOR SHAPE"}, {51, "MOTOR SYNC"},
    {64, "SUSTAIN"}, {68, "REVERB MOD"}, {69, "REVERB LOW CUT"}, {70, "FILTER TYPE"}, {71, "FILTER RESO"},
    {72, "FILTER LFO"}, {73, "REVERB SIZE"}, {74, "FILTER FREQ"}, {75, "LFO SHAPE"}, {76, "LFO RATE"}, {77, "LFO SYNC"},
    {78, "LFO RETRIG"}, {79, "TYPE: BARS"}, {80, "TYPE: PIANOS"}, {81, "DRIVE"}, {82, "DRIVE TONE"}, {83, "DRIVE MIX"},
    {84, "DRIVE TYPE"}, {85, "CHORUS / PHASER"}, {86, "CHORUS DEPTH"}, {87, "CHORUS SPEED"}, {88, "DELAY TYPE"},
    {89, "DELAY MIX"}, {90, "DELAY TIME"}, {91, "REVERB MIX"}, {92, "REVERB DECAY"}, {93, "REVERB TONE"},
    {94, "REVERB PRE-DELAY"}, {95, "REVERB TYPE"}, {102, "DECAY"}, {103, "RELEASE"}, {104, "NOISE"}, {105, "SPREAD"},
    {106, "COMP ON"}, {107, "COMP THRESHOLD"}, {108, "COMP ATTACK"}, {109, "COMP RELEASE"}, {110, "LEVEL"},
    {111, "ALL EFFECTS OFF"}, {112, "COMP RATIO"}, {113, "COMP MAKEUP"},
};
static const char *param_name(int id)       /* a learned parameter's name in the table: its CC map name */
{
    static const struct { int id; uint8_t cc; } P2CC[] = {
        {FRP_PH_DEPTH, 13}, {FRP_PH_SPEED, 14}, {FRP_PH_FDBK, 16}, {FRP_TW_DEPTH, 18}, {FRP_TW_RATE, 19}, {FRP_TW_SHAPE, 22},
        {FRP_TONE, 28}, {FRP_BARK, 29}, {FRP_BELL, 30}, {FRP_HAMMER, 31}, {FRP_EQ_LO, 34}, {FRP_EQ_MID, 35},
        {FRP_EQ_FREQ, 36}, {FRP_EQ_HI, 37}, {FRP_MOT_DEPTH, 48}, {FRP_MOT_RATE, 49}, {FRP_MOT_SHAPE, 50},
        {FRP_RV_MOD, 68}, {FRP_RV_LOCUT, 69}, {FRP_FL_RESO, 71}, {FRP_FL_LFO, 72}, {FRP_RV_SIZE, 73}, {FRP_FL_FREQ, 74},
        {FRP_LFO_RATE, 76}, {FRP_DRIVE, 81}, {FRP_DRV_TONE, 82}, {FRP_DRV_MIX, 83}, {FRP_CP_DEPTH, 86},
        {FRP_CP_SPEED, 87}, {FRP_DL_DEPTH, 89}, {FRP_DL_TIME, 90}, {FRP_DL_FDBK, 26}, {FRP_DL_TONE, 27},
        {FRP_RV_DEPTH, 91}, {FRP_RV_DECAY, 92}, {FRP_RV_TONE, 93}, {FRP_RV_PREDLY, 94}, {FRP_DECAY, 102},
        {FRP_RELEASE, 103}, {FRP_NOISE, 104}, {FRP_CMP_AMT, 107}, {FRP_CMP_ATK, 108}, {FRP_CMP_REL, 109},
        {FRP_LEVEL, 110}, {FRP_CMP_RATIO, 112}, {FRP_CMP_GAIN, 113},
    };
    size_t i, j;
    for (i = 0; i < sizeof P2CC / sizeof P2CC[0]; i++)
        if (P2CC[i].id == id)
            for (j = 0; j < sizeof CCMAP / sizeof CCMAP[0]; j++) if (CCMAP[j].cc == P2CC[i].cc) return CCMAP[j].name;
    return "PARAMETER";
}
static void cc_table(frui *u, float x, float y, float w, float h)
{
    const frui_model *m = u->m;
    int rows[160], learned[160], nr = 0, cc, i, first, vis = (int)(h / 24.f) - 1;
    size_t j;
    for (cc = 0; cc < 128; cc++) {                  /* the learned first at their CC, then the defaults */
        for (i = 0; i < FRP_COUNT; i++) if (m->learn_cc[i] == cc + 1 && nr < 160) { rows[nr] = i; learned[nr++] = cc; }
        for (j = 0; j < sizeof CCMAP / sizeof CCMAP[0]; j++)
            if (CCMAP[j].cc == cc && nr < 160) { rows[nr] = (int)j; learned[nr++] = -1; }
    }
    ctrl_box(u, x, y, w, h, 0, 0);
    tx(u, x + 12.f, mid(u, y + 12.f, 8.5f), T_CAPTION, 0, "CC", C_TEXT3);
    tx(u, x + 56.f, mid(u, y + 12.f, 8.5f), T_CAPTION, 0, "PARAMETER", C_TEXT3);
    txr(u, x + w - 24.f, mid(u, y + 12.f, 8.5f), T_CAPTION, 0, "SOURCE", C_TEXT3);
    hair_h(u, x + 1.f, x + w - 1.f, y + 24.f, C_EDGE);
    first = m->cc_scroll < 0 ? 0 : m->cc_scroll > nr - vis ? nr - vis : m->cc_scroll;
    for (i = 0; i < vis && first + i < nr; i++) {
        int k = first + i, l = learned[k] >= 0;
        float ry = y + 24.f + 24.f * (float)i, yc = ry + 12.f;
        char buf[12];                               /* (an int's 11 characters: Linux gcc's -Wformat-truncation) */
        snprintf(buf, sizeof buf, "%d", l ? learned[k] : CCMAP[rows[k]].cc);
        if (i) hair_h(u, x + 8.f, x + w - 20.f, ry, FRUI_RGB(22, 22, 24));
        txr(u, x + 32.f, mid(u, yc, 10.5f), T_MENU, FRUI_TNUM, buf, l ? C_ACC : C_TEXT);
        tx(u, x + 56.f, mid(u, yc, 10.5f), T_MENU, 0, l ? param_name(rows[k]) : CCMAP[rows[k]].name, C_TEXT);
        txr(u, x + w - 24.f, mid(u, yc, 8.5f), T_CAPTION, 0, l ? "LEARNED" : "DEFAULT", l ? C_ACC : C_TEXT3);
    }
    {   /* the scroll bar */
        float th = h - 32.f, sh = th * (float)vis / (float)(nr > vis ? nr : vis), sy = y + 28.f + (th - sh) * (float)first / (float)(nr > vis ? nr - vis : 1);
        frui_rrect(u->c, X(x + w - 10.f), X(y + 28.f), X(3.f), X(th), X(1.5f), FRUI_RGB(28, 28, 30));
        frui_rrect(u->c, X(x + w - 10.f), X(sy), X(3.f), X(sh), X(1.5f), FRUI_RGB(120, 120, 124));
    }
    reg(u, FRW_CCMAP, 0, x, y, w, h);
}
static void body_dim(frui *u);
static void sheet_bg(frui *u, float x, float y, float w, float h);
static void settings_sheet(frui *u)
{
    const frui_model *m = u->m;
    float x = G_M, y = SH_Y, w = (float)FRUI_W - 2.f * G_M, h = SH_H, c1 = G_H0 + G_COL, d1 = G_H1 + G_COL;
    char buf[32];
    body_dim(u);
    sheet_bg(u, x, y, w, h);
    section_title(u, G_H0, y + 22.f, "SETTINGS");
    key_btn(u, FRB_CLOSE, 0, G_H1 + 4.f * G_COL - 32.f, y + 8.f, 32.f, 28.f);
    if (m->version[0]) {   /* VERSION, read only, in the title band before the close switch (as the FM-1's card) */
        float xr = G_H1 + 4.f * G_COL - 32.f - G_M, wv = tw(u, T_CAPTION, FRUI_TNUM, m->version);
        txr(u, xr, mid(u, y + 22.f, 8.5f), T_CAPTION, FRUI_TNUM, m->version, C_TEXT);
        txr(u, xr - wv - 8.f, mid(u, y + 22.f, 8.5f), T_CAPTION, 0, "VERSION", C_TEXT3);
    }
    hair_h(u, x + 1.f, x + w - 1.f, y + 44.f, FRUI_RGB(34, 34, 36));
    hair_v(u, (float)FRUI_W * 0.5f, y + 60.f, y + h - 16.f, FRUI_RGB(34, 34, 36));
    /* MIDI */
    group_caption(u, G_H0, 132.f, "MIDI");
    row_label(u, G_H0, SH_ROW(0) + 14.f, "CHANNEL", 0);
    menu_btn(u, FRS_CHANNEL, "", c1, SH_ROW(0), 28.f, MENU_W);
    row_label(u, G_H0, SH_ROW(1) + 14.f, "VELOCITY", 0);
    segmented(u, FRS_VEL, c1, SH_ROW(1), 28.f, 3.f * G_COL);
    row_label(u, G_H0, SH_ROW(2) + 14.f, "FIXED VEL", m->vel_curve != 3);
    fader(u, FRS_FIXVEL, c1, SH_ROW(2), 2.f * G_COL - 8.f, 28.f, m->vel_curve != 3);
    snprintf(buf, sizeof buf, "%d", m->fixed_vel);
    txr(u, G_H0 + 4.f * G_COL, mid(u, SH_ROW(2) + 14.f, 15.f), T_VALUE, FRUI_TNUM, buf, m->vel_curve != 3 ? C_TEXT3 : C_TEXT);
    row_label(u, G_H0, SH_ROW(3) + 14.f, "PROGRAM CHG", 0);
    segmented(u, FRS_PGM, c1, SH_ROW(3), 28.f, 2.f * G_COL - 8.f);
    row_label(u, G_H0, SH_ROW(4) + 14.f, "PITCH BEND", 0);
    info(u, c1, SH_ROW(4) + 14.f, "\xC2\xB1" "2 SEMITONES, ELECTRIC PIANOS");
    row_label(u, G_H0, SH_ROW(5) + 14.f, "SUSTAIN", 0);
    info(u, c1, SH_ROW(5) + 14.f, "CC 64, HALF-PEDAL; SUSTAIN SWITCH LATCHES");
    tx(u, G_H0, mid(u, SH_ROW(6) + 14.f, 8.5f), T_CAPTION, 0, "RIGHT-CLICK A KNOB: MIDI LEARN, FORGET", C_TEXT3);
    /* TEMPO */
    group_caption(u, G_H1, 132.f, "TEMPO");
    row_label(u, G_H1, SH_ROW(0) + 14.f, "SOURCE", 0);
    segmented(u, FRS_TEMPO, d1, SH_ROW(0), 28.f, 3.f * G_COL);
    row_label(u, G_H1, SH_ROW(1) + 14.f, "BPM", m->tempo_src != 1);
    key_btn(u, FRB_BPM_DOWN, 0, d1, SH_ROW(1), 28.f, 28.f);
    ctrl_box(u, d1 + 32.f, SH_ROW(1), 2.f * G_COL - 8.f - 64.f, 28.f, 0, 0);
    {
        int b = m->tempo_src == 1 ? m->bpm10 : m->host_bpm10;
        snprintf(buf, sizeof buf, "%d.%d", b / 10, b % 10);
        txc(u, d1 + G_COL - 4.f, mid(u, SH_ROW(1) + 14.f, 10.5f), T_MENU, FRUI_TNUM, buf, m->tempo_src == 1 ? C_TEXT : C_TEXT2);
        reg(u, FRW_FADER, FRS_BPM, d1 + 32.f, SH_ROW(1), 2.f * G_COL - 8.f - 64.f, 28.f);
    }
    key_btn(u, FRB_BPM_UP, 0, d1 + 2.f * G_COL - 8.f - 28.f, SH_ROW(1), 28.f, 28.f);
    info(u, G_H1 + 3.f * G_COL + 8.f, SH_ROW(1) + 14.f, m->tempo_src == 0 ? "FROM THE HOST" : m->tempo_src == 2 ? "FROM MIDI CLOCK" : "INTERNAL");
    row_label(u, G_H1, SH_ROW(2) + 14.f, "SYNC ALL FX", 0);
    segmented(u, FRS_SYNCALL, d1, SH_ROW(2), 28.f, 2.f * G_COL - 8.f);
    menu_btn(u, FRS_SYNCDIV, "DIV", G_H1 + 4.f * G_COL - MENU_W, SH_ROW(2), 28.f, MENU_W);
    /* the CC map */
    group_caption(u, G_H1, 290.f, "CC MAP");
    key_btn(u, FRB_LEARN_RESET, "FORGET LEARNED", G_H1 + 4.f * G_COL - (2.f * G_COL - 8.f), 276.f, 2.f * G_COL - 8.f, 28.f);
    cc_table(u, G_H1, 312.f, 4.f * G_COL, 144.f);
}

/* ---- PRESETS: a sheet over the main view (the rail, the switches and the keys stay live, so a sound can be tried as
 * it is chosen): every preset grouped by its instrument, in the instrument menu's order, each group the factory
 * presets then the user's; columns of 17 rows of 20 px on the halves' two-column spans (x 28 244 500 716, 208 wide),
 * four columns a page; a group's title never ends a column, and a group that goes on in the next column has its title
 * again at the top */
#define PL_ROW0 128.f
#define PL_RH 20.f
#define PL_ROWS 17
#define PL_COLW 208.f
static float pl_colx(int k) { return (k & 2 ? G_H1 : G_H0) + (k & 1 ? 2.f * G_COL : 0.f); }
/* the flow: calls f for every title (idx -1, type) and entry (idx) with its slot (column * PL_ROWS + row); the slots used */
static int pl_flow(const frui_model *m, void (*f)(void *ctx, int slot, int idx, int type), void *ctx)
{
    int t, i, pass, slot = 0, n = m->pl_n < FRUI_PL_MAX ? m->pl_n : FRUI_PL_MAX;
    for (t = 0; t < 6; t++) {
        int any = 0;
        for (i = 0; i < n && !any; i++) any = m->pl[i].type == t;
        if (!any) continue;
        if (slot % PL_ROWS == PL_ROWS - 1) slot++;   /* (a title on a column's last row: to the next column) */
        if (f) f(ctx, slot, -1, t);
        slot++;
        for (pass = 0; pass < 2; pass++)             /* the factory presets, then the user's */
            for (i = 0; i < n; i++)
                if (m->pl[i].type == t && (int)m->pl[i].user == pass) {
                    if (slot % PL_ROWS == 0) { if (f) f(ctx, slot, -1, t); slot++; }   /* (a new column: the title again) */
                    if (f) f(ctx, slot, i, t);
                    slot++;
                }
    }
    return slot;
}
int frui_preset_pages(const frui_model *m)
{
    int slots = pl_flow(m, 0, 0), cols = (slots + PL_ROWS - 1) / PL_ROWS;
    return cols > 4 ? (cols + 3) / 4 : 1;
}
struct pl_find { int idx, slot; };
static void pl_find_one(void *ctx, int slot, int idx, int type)
{
    struct pl_find *f = ctx;
    (void)type;
    if (idx >= 0 && idx == f->idx) f->slot = slot;
}
int frui_preset_page_of(const frui_model *m, int idx)
{
    struct pl_find f = {idx, -1};
    if (idx < 0) return 0;
    pl_flow(m, pl_find_one, &f);
    return f.slot < 0 ? 0 : f.slot / PL_ROWS / 4;
}
static void pl_draw_one(void *ctx, int slot, int idx, int type)
{
    frui *u = ctx;
    const frui_model *m = u->m;
    int col = slot / PL_ROWS, row = slot % PL_ROWS, page = m->pl_page;
    float x, y, cy;
    if (col / 4 != page) return;
    x = pl_colx(col % 4); y = PL_ROW0 + PL_RH * (float)row; cy = y + PL_RH * 0.5f;
    if (idx < 0) {                                   /* the instrument's title: its drawing, its name, a rule */
        const char *nm = FRN_TYPE[type];
        float wn = twf(u, u->fd, 8.f, 400.f, 1.8f, 0, nm) - 1.8f;
        eicon(u, type, x + 2.f, cy - 6.f, 0.5f, C_TEXT2);
        txf(u, u->fd, x + 30.f, midd(u, cy, 8.f), 8.f, 400.f, 1.8f, 0, nm, C_TEXT);
        hair_h(u, x + 30.f + wn + 10.f, x + PL_COLW, cy, FRUI_RGB(44, 44, 47));
        return;
    }
    {
        int cur = idx == m->pl_cur, hot = is_hover(u, FRW_PITEM, idx);
        if (cur) {
            frui_rrect(u->c, X(x), X(y + 1.f), X(PL_COLW), X(PL_RH - 2.f), X(R_CTRL), FRUI_RGB(40, 40, 43));
            frui_rrect(u->c, X(x + 3.f), X(cy - 5.f), X(2.f), X(10.f), X(1.f), C_ACC);
        } else if (hot) frui_rrect(u->c, X(x), X(y + 1.f), X(PL_COLW), X(PL_RH - 2.f), X(R_CTRL), FRUI_RGB(30, 30, 33));
        tx(u, x + 12.f, mid(u, cy, 10.f), 10.f, 640.f, 0.3f, FRUI_TNUM, m->pl[idx].tag,
           cur ? C_ACC : m->pl[idx].user ? C_TEXT2 : C_TEXT3);
        frui_clip(u->c, X(x + 46.f), X(y), X(PL_COLW - 52.f), X(PL_RH));
        tx(u, x + 46.f, mid(u, cy, 10.5f), T_MENU, 0, m->pl[idx].name, cur || hot ? C_TEXT : C_TEXT2);
        frui_clip_reset(u->c);
        reg(u, FRW_PITEM, idx, x, y, PL_COLW, PL_RH);
    }
}
static void body_dim(frui *u)                        /* the main view sinks back (A3: the body only, the plate stays lit) */
{
    if (u->look == FRUI_LOOK_A3)
        frui_rrect(u->c, X(A3_FRAME) + 1.f, X(A3_BODY_Y) + 1.f, X((float)FRUI_W - 2.f * A3_FRAME) - 2.f,
                   X((float)FRUI_H - A3_BODY_Y - A3_FRAME) - 2.f, X(R_BODY) - 1.f, FRUI_RGBA(0, 0, 0, 170));
    else frui_rect(u->c, 0.f, X(RAIL_H) + 1.f, X((float)FRUI_W), X((float)FRUI_H - RAIL_H) - 1.f, FRUI_RGBA(0, 0, 0, 170));
}
static void sheet_bg(frui *u, float x, float y, float w, float h)
{
    frui_rrect_shadow(u->c, X(x), X(y + 8.f), X(w), X(h), X(R_PANEL), X(16.f), FRUI_RGBA(0, 0, 0, 230));
    frui_rrect_grad(u->c, X(x), X(y), X(w), X(h), X(R_PANEL), FRUI_RGB(25, 25, 26), FRUI_RGB(19, 19, 20));
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(R_PANEL), 1.f, FRUI_RGB(44, 44, 47));
    frui_rect(u->c, X(x + R_PANEL), X(y) + 1.f, X(w - 2.f * R_PANEL), 1.f, FRUI_RGBA(255, 255, 255, 30));
}
static void presets_sheet(frui *u)
{
    const frui_model *m = u->m;
    float x = G_M, y = VO_Y, w = (float)FRUI_W - 2.f * G_M, h = FX_Y + FX_H - VO_Y;
    int i, nf = 0, nu = 0, pages = frui_preset_pages(m);
    char buf[48];
    body_dim(u);
    sheet_bg(u, x, y, w, h);
    section_title(u, G_H0, y + 22.f, "PRESETS");
    for (i = 0; i < m->pl_n && i < FRUI_PL_MAX; i++) { if (m->pl[i].user) nu++; else nf++; }
    snprintf(buf, sizeof buf, nu == 1 ? "%d FACTORY, 1 USER" : "%d FACTORY, %d USER", nf, nu);
    tx(u, G_H0 + G_COL, mid(u, y + 22.f, 8.5f), T_CAPTION, 0, buf, C_TEXT3);
    txc(u, (float)FRUI_W * 0.5f + G_COL, mid(u, y + 22.f, 8.5f), T_CAPTION, 0, "PLAY THE KEYS TO TRY A SOUND", C_TEXT3);
    key_btn(u, FRB_CLOSE, 0, G_H1 + 4.f * G_COL - 32.f, y + 8.f, 32.f, 28.f);
    if (pages > 1) {                                 /* the pager, a gutter's half before the close key */
        float pw = 100.f, px = G_H1 + 4.f * G_COL - 32.f - 8.f - pw, py = y + 8.f, ph = 28.f;
        ctrl_box(u, px, py, pw, ph, 0, 0);
        chevron(u, px + 14.f, py + ph * 0.5f, 3.5f, 1, m->pl_page > 0 ? C_TEXT : C_DIM);
        chevron(u, px + pw - 14.f, py + ph * 0.5f, 3.5f, 2, m->pl_page < pages - 1 ? C_TEXT : C_DIM);
        snprintf(buf, sizeof buf, "%d / %d", m->pl_page + 1, pages);
        txc(u, px + pw * 0.5f, mid(u, py + ph * 0.5f, 10.5f), T_MENU, FRUI_TNUM, buf, C_TEXT);
        reg(u, FRW_PAGER, FRUI_PL_PAGER, px, py, pw, ph);
    }
    hair_h(u, x + 1.f, x + w - 1.f, y + 44.f, FRUI_RGB(34, 34, 36));
    hair_v(u, (float)FRUI_W * 0.5f, PL_ROW0, PL_ROW0 + PL_RH * (float)PL_ROWS, FRUI_RGB(34, 34, 36));
    if (m->pl_n <= 0) txc(u, (float)FRUI_W * 0.5f, mid(u, 280.f, 10.5f), T_MENU, 0, "NO PRESETS", C_TEXT3);
    pl_flow(m, pl_draw_one, u);
}

/* ---- a knob's right-click menu: MIDI LEARN, FORGET (when learned), the default value */
static void knob_menu(frui *u)
{
    const frui_model *m = u->m;
    const frui_widget *g = 0;
    int i, id, cc;
    float px, py, pw = 168.f, ih = 24.f, ph;
    char forget[32];
    const char *items[3];
    if (m->ctx_knob < 0) return;
    for (i = 0; i < u->nw; i++) if (u->wg[i].kind == FRW_KNOB && u->wg[i].id == m->ctx_knob) g = &u->wg[i];
    if (!g) return;
    id = g->id;
    cc = id < FRP_COUNT ? m->learn_cc[id] : 0;
    if (cc) snprintf(forget, sizeof forget, "FORGET CC %d", cc - 1); else snprintf(forget, sizeof forget, "FORGET (NONE LEARNED)");
    items[0] = m->learning == id ? "LEARNING: MOVE A CC" : "MIDI LEARN";
    items[1] = forget;
    items[2] = "DEFAULT VALUE";
    for (i = 0, pw = 0.f; i < 3; i++) { float t = tw(u, T_MENU, 0, items[i]) + 40.f; if (t > pw) pw = t; }
    pw = ceilf(pw / 4.f) * 4.f;
    ph = ih * 3.f + 8.f;
    px = g->x + g->w * 0.5f + 8.f; py = g->y + 52.f - 8.f;
    if (px + pw > (float)FRUI_W - G_M) px = g->x + g->w * 0.5f - 8.f - pw;
    if (py + ph > (float)FRUI_H - G_M) py = (float)FRUI_H - G_M - ph;
    frui_rrect_shadow(u->c, X(px), X(py + 6.f), X(pw), X(ph), X(R_PANEL), X(12.f), FRUI_RGBA(0, 0, 0, 200));
    frui_rrect(u->c, X(px), X(py), X(pw), X(ph), X(R_PANEL), FRUI_RGB(24, 24, 26));
    frui_rrect_stroke(u->c, X(px) + 0.5f, X(py) + 0.5f, X(pw) - 1.f, X(ph) - 1.f, X(R_PANEL), 1.f, FRUI_RGB(52, 52, 56));
    frui_rect(u->c, X(px + R_PANEL), X(py) + 1.f, X(pw - 2.f * R_PANEL), 1.f, FRUI_RGBA(255, 255, 255, 24));
    for (i = 0; i < 3; i++) {
        float iy = py + 4.f + ih * (float)i;
        int off = i == 1 && !cc, hot = m->hover == FRUI_WID(FRW_CTX, i);
        if (hot && !off) frui_rrect(u->c, X(px + 4.f), X(iy), X(pw - 8.f), X(ih), X(R_CTRL), FRUI_RGB(40, 40, 43));
        if (i == 0) lamp(u, px + 14.f, iy + ih * 0.5f, 2.5f, m->learning == id);
        tx(u, px + 24.f, mid(u, iy + ih * 0.5f, 10.5f), T_MENU, 0, items[i], off ? C_TEXT3 : i == 0 && m->learning == id ? C_ACC : C_TEXT);
    }
    reg(u, FRW_CTX, FRC_LEARN, px, py + 4.f, pw, ih);
    reg(u, FRW_CTX, FRC_FORGET, px, py + 4.f + ih, pw, ih);
    reg(u, FRW_CTX, FRC_DEFAULT, px, py + 4.f + 2.f * ih, pw, ih);
}

/* ---- an open menu, over everything (the instrument's menu shows each engine's drawing) */
static void popup(frui *u)
{
    const frui_model *m = u->m;
    int i, n, sel, cols, rows, id;
    const frui_widget *g = 0;
    const char *const *names;
    float ih = 24.f, cw, pw, ph, px, py, lx = 24.f;
    if (m->menu < 0) return;
    for (i = 0; i < u->nw; i++) if (FRUI_WID(u->wg[i].kind, u->wg[i].id) == m->menu) g = &u->wg[i];
    if (!g || g->kind != FRW_MENU) return;
    id = g->id;
    names = opt_names(id, &n);
    sel = pval(m, id);
    if (id == FRP_TYPE) { ih = 32.f; lx = 64.f; }
    cols = n > 8 ? 3 : 1;
    rows = (n + cols - 1) / cols;
    cw = 0.f;
    for (i = 0; i < n; i++) { float t = tw(u, T_MENU, 0, names[i]) + lx + 12.f; if (t > cw) cw = t; }
    if (cols == 1 && cw < g->w) cw = g->w;
    pw = cw * (float)cols + 8.f; ph = ih * (float)rows + 8.f;
    px = g->x + g->w - pw > 8.f ? g->x + g->w - pw : 8.f;
    if (cols == 1) px = g->x + pw > (float)FRUI_W - G_M - G_PAD ? (float)FRUI_W - G_M - G_PAD - pw : g->x;
    py = g->y + g->h + 4.f;
    if (py + ph > (float)FRUI_H - 8.f) py = g->y - 4.f - ph;
    frui_rrect_shadow(u->c, X(px), X(py + 6.f), X(pw), X(ph), X(R_PANEL), X(12.f), FRUI_RGBA(0, 0, 0, 200));
    frui_rrect(u->c, X(px), X(py), X(pw), X(ph), X(R_PANEL), FRUI_RGB(24, 24, 26));
    frui_rrect_stroke(u->c, X(px) + 0.5f, X(py) + 0.5f, X(pw) - 1.f, X(ph) - 1.f, X(R_PANEL), 1.f, FRUI_RGB(52, 52, 56));
    frui_rect(u->c, X(px + R_PANEL), X(py) + 1.f, X(pw - 2.f * R_PANEL), 1.f, FRUI_RGBA(255, 255, 255, 24));
    for (i = 0; i < n; i++) {
        int c = i / rows, r = i % rows;
        float ix = px + 4.f + cw * (float)c, iy = py + 4.f + ih * (float)r;
        if (i == sel) {
            frui_rrect(u->c, X(ix), X(iy), X(cw), X(ih), X(R_CTRL), FRUI_RGB(40, 40, 43));
            frui_rrect(u->c, X(ix + 4.f), X(iy + ih * 0.5f - 6.f), X(2.f), X(12.f), X(1.f), C_ACC);
        }
        if (id == FRP_TYPE) eicon(u, i, ix + 16.f, iy + (ih - 24.f) * 0.5f, 1.f, i == sel ? C_TEXT : C_TEXT2);
        tx(u, ix + lx, mid(u, iy + ih * 0.5f, 10.5f), T_MENU, 0, names[i], i == sel ? C_TEXT : C_TEXT2);
        reg(u, FRW_ITEM, i, ix, iy, cw, ih);
    }
}

frui *frui_new(frui_font *font, frui_text_fn text, void *ctx)
{
    frui *u = calloc(1, sizeof *u);
    u->f = u->fd = font; u->text = text; u->ctx = ctx;
    u->look = FRUI_LOOK_A3;
    return u;
}
void frui_free(frui *u) { free(u); }
void frui_set_look(frui *u, int look, frui_font *display)
{
    u->look = look;
    u->fd = display ? display : u->f;
}
int frui_widget_count(const frui *u) { return u->nw; }
int frui_anchor_count(const frui *u) { return u->na; }
int frui_shell_count(const frui *u) { return u->nsh; }
const frui_shell *frui_shell_get(const frui *u, int i) { return i >= 0 && i < u->nsh ? &u->sh[i] : 0; }
const frui_anchor *frui_anchor_get(const frui *u, int i) { return i >= 0 && i < u->na ? &u->an[i] : 0; }
const frui_widget *frui_widget_get(const frui *u, int i) { return i >= 0 && i < u->nw ? &u->wg[i] : 0; }

void frui_draw(frui *u, const frui_model *m, frui_canvas *c)
{
    u->c = c; u->m = m; u->nw = 0; u->na = 0; u->nsh = 0;
    memset(u->rg, 0, sizeof u->rg);
    region(u, FRR_RAIL, 0.f, 0.f, (float)FRUI_W, RAIL_H + 8.f);
    region(u, FRR_VOICE, G_M, VO_Y, (float)FRUI_W - 2.f * G_M, VO_H);
    u->s = (float)c->w / (float)FRUI_W;
    u->hp = 1.f / u->s;
    u->sb[0] = c->bx0; u->sb[1] = c->by0; u->sb[2] = c->bx1; u->sb[3] = c->by1;
    frui_clip_reset(c);
    if (u->look == FRUI_LOOK_A3) a3_plate(u);       /* (its shells always; its pixels: the static layer) */
    else if (st_begin(u)) {
        if (u->look == FRUI_LOOK_PLATE) {
            frui_fill(c, FRUI_RGB(40, 40, 42));
            frui_brushed(c, 0.f, 0.f, (float)c->w, (float)c->h, 0.f, C_AL_T, FRUI_RGB(158, 160, 164), 0.045f, u->s, 1u);
        } else frui_fill(c, C_BG);
        st_end(u);
    }
    if (u->layer == FRUI_LAYER_STATIC) frui_clip_base(c, 0, 0, 0, 0);
    rail(u);
    {
        int nrail = u->nw;
        voice(u);
        left_panel(u);
        right_panel(u);
        if (m->presets && !m->settings) {            /* PRESETS: the rail, the sheet, the switches and the keys answer */
            u->nw = nrail;
            presets_sheet(u);
            switches(u);
            keyboard(u);
        } else {
            switches(u);
            keyboard(u);
            if (m->settings) { u->nw = nrail; settings_sheet(u); }   /* only the rail and the sheet answer the mouse */
            else knob_menu(u);
        }
    }
    popup(u);
    if (u->layer == FRUI_LAYER_STATIC) frui_clip_base(c, u->sb[0], u->sb[1], u->sb[2], u->sb[3]);
}
void frui_set_layer(frui *u, int layer) { u->layer = layer; }

int frui_range(int id) { return phi(id); }

int frui_hit(const frui *u, float x, float y, frui_widget *w)
{
    int i;
    for (i = u->nw - 1; i >= 0; i--) {
        const frui_widget *g = &u->wg[i];
        if (x >= g->x && x < g->x + g->w && y >= g->y && y < g->y + g->h) { if (w) *w = *g; return i; }
    }
    return -1;
}

/* ---- the plugin window's helpers (frui.h) */
int frui_value(const frui_model *m, int id) { return pval(m, id); }
int frui_value_hi(int id) { return phi(id); }
const char *const *frui_options(int id, int *n) { return opt_names(id, n); }
void frui_region(const frui *u, int which, float r[4])
{
    int i;
    for (i = 0; i < 4; i++) r[i] = which >= 0 && which < FRR_N ? u->rg[which][i] : 0.f;
}
/* the keyboard's geometry (keyboard() above): C1 .. C6, 36 white keys */
static const int KB_WHITE_OF[12] = {0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6};
void frui_key_span(int note, float *x0, float *x1)
{
    float w = (float)FRUI_W - G_M - KB_X, ww = w / 36.f, bw = ww * 0.58f;
    int pc = note % 12, oct = note / 12 - 1;
    if (KB_WHITE_OF[pc] >= 0) { *x0 = KB_X + ww * (float)((oct - 1) * 7 + KB_WHITE_OF[pc]); *x1 = *x0 + ww; }
    else { *x0 = KB_X + ww * (float)((oct - 1) * 7 + KB_WHITE_OF[pc - 1] + 1) - bw * 0.5f; *x1 = *x0 + bw; }
}
int frui_key_at(float x, float y)
{
    float w = (float)FRUI_W - G_M - KB_X, ww = w / 36.f;
    int n, wi;
    if (x < KB_X || x >= KB_X + w || y < KB_Y || y >= KB_Y + KB_H) return -1;
    if (y < KB_Y + KB_H * 0.6f)                       /* the black keys first (they lie over the white) */
        for (n = 24; n <= 84; n++) {
            float x0, x1;
            if (KB_WHITE_OF[n % 12] >= 0) continue;
            frui_key_span(n, &x0, &x1);
            if (x >= x0 && x < x1) return n;
        }
    wi = (int)((x - KB_X) / ww);
    if (wi > 35) wi = 35;
    for (n = 24; n <= 84; n++)
        if (KB_WHITE_OF[n % 12] >= 0 && (n / 12 - 2) * 7 + KB_WHITE_OF[n % 12] == wi) return n;
    return -1;
}
int frui_seg_at(const frui_widget *w, float x)
{
    int n, i;
    (void)opt_names(w->id, &n);
    i = (int)((x - w->x) / (w->w / (float)n));
    return i < 0 ? 0 : i >= n ? n - 1 : i;
}
