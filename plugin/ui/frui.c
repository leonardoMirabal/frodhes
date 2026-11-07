/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the window (frui.h). Layout, widgets and the theme, all in logical pixels scaled at draw time.
 *
 *   header      FRODHES | < F09 70S PHASER > SAVE UNDO |              OUTPUT meter | LEVEL
 *   VOICE       the instrument (TYPE) and its eight cards in two groups of four, as the device's VOICE 1/2 and 2/2
 *   two panels  left: what comes before the drive (MOTOR on VIBES, COMP, EQ, DRIVE); right: the effects after it
 *               (FILTER, TREM/WAH, PHASER, CHORUS, DELAY, REVERB); one tab at a time: its switch / type, its four knobs
 *   keyboard    C1 .. C6, the held notes lit
 *
 * Colours are the device's (ui.c AMBER: the accent RGB565 0xE3C1 = 230 121 8, its fill 98 52 0, the warm white and
 * the blue-grey labels), on a near-black ground. */
#include "frui.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXW 160

struct frui {
    frui_font *f;
    frui_text_fn text;
    void *ctx;
    frui_widget wg[MAXW];
    int nw;
    float s;                       /* pixels per logical pixel */
    frui_canvas *c;
    const frui_model *m;
};

/* ---- the theme */
#define C_BG FRUI_RGB(11, 11, 12)
#define C_PANEL FRUI_RGB(20, 20, 22)
#define C_POP FRUI_RGB(30, 30, 33)
#define C_BORDER FRUI_RGB(37, 36, 40)
#define C_DIV FRUI_RGB(33, 32, 36)
#define C_TEXT FRUI_RGB(238, 238, 230)
#define C_TEXT2 FRUI_RGB(148, 146, 156)
#define C_TEXT3 FRUI_RGB(134, 132, 142)
#define C_ACC FRUI_RGB(230, 121, 8)
#define C_ACC_HI FRUI_RGB(255, 150, 52)
#define C_FILL FRUI_RGB(98, 52, 0)
#define C_CTRL FRUI_RGB(28, 28, 31)
#define C_CTRL_HI FRUI_RGB(36, 36, 40)
#define C_CTRL_B FRUI_RGB(47, 46, 51)
#define C_TRACK FRUI_RGB(46, 45, 50)
#define C_KNOB_T FRUI_RGB(62, 61, 66)
#define C_KNOB_B FRUI_RGB(34, 33, 37)
#define C_LED_OFF FRUI_RGB(58, 57, 62)
#define C_SHADOW FRUI_RGBA(0, 0, 0, 150)

/* type sizes (logical px) and weights */
#define T_TITLE 12.f, 640.f, 1.5f
#define T_TAB 11.f, 620.f, 1.0f
#define T_LABEL 10.f, 560.f, 0.9f
#define T_CAPTION 9.5f, 540.f, 1.1f
#define T_VALUE 14.5f, 520.f, 0.f
#define T_UNIT 10.5f, 480.f, 0.2f
#define T_SEG 10.f, 600.f, 0.6f
#define T_BTN 10.5f, 620.f, 1.0f

#define X(v) ((v) * u->s)

/* ---- text in logical coordinates */
static float tw(frui *u, float size, float weight, float track, int flags, const char *s)
{
    frui_style st = {size * u->s, weight, track * u->s, flags};
    return frui_text_width(u->f, &st, s) / u->s;
}
static float tx(frui *u, float x, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    frui_style st = {size * u->s, weight, track * u->s, flags};
    return frui_text(u->c, u->f, &st, X(x), X(y), s, col) / u->s;
}
/* baseline that centres capitals on y */
static float mid(frui *u, float y, float size) { return y + frui_font_ascent(u->f, size) * 0.5f; }
static void txc(frui *u, float cx, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    tx(u, cx - tw(u, size, weight, track, flags, s) * 0.5f, y, size, weight, track, flags, s, col);
}
static void txr(frui *u, float rx, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    tx(u, rx - tw(u, size, weight, track, flags, s), y, size, weight, track, flags, s, col);
}

static void reg(frui *u, int kind, int id, float x, float y, float w, float h)
{
    if (u->nw < MAXW) { frui_widget *g = &u->wg[u->nw++]; g->kind = kind; g->id = id; g->x = x; g->y = y; g->w = w; g->h = h; }
}
static int is_focus(frui *u, int kind, int id) { return u->m->focus == FRUI_WID(kind, id); }
static int is_hover(frui *u, int kind, int id) { return u->m->hover == FRUI_WID(kind, id); }

/* ---- values */
static int pval(const frui_model *m, int id)
{
    if (id == FRP_TUNE) return m->tune + 50;
    if (id == FRP_TW_KIND) return m->p[FRP_TW_SW] == 0 ? 0 : m->p[FRP_TW_SW] == 2 ? 3 : m->p[FRP_TW_MODE] ? 2 : 1;
    return id >= 0 && id < FRP_COUNT ? m->p[id] : 0;
}
static int phi(int id) { return id == FRP_TUNE ? 100 : id == FRP_TW_KIND ? 3 : id >= 0 && id < FRP_COUNT ? FRP_HI[id] : 1; }
static int is_bars(const frui_model *m) { return m->p[FRP_TYPE] >= 4; }
static int has_motor(const frui_model *m) { return m->p[FRP_TYPE] == 4; }

static const char *const *opt_names(int id, int *n)
{
    *n = phi(id) + 1;
    switch (id) {
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
static void chevron(frui *u, float cx, float cy, float sz, int dir, uint32_t col)   /* dir 0 down, 1 left, 2 right */
{
    float a = sz, b = sz * 0.5f, lw = X(1.5f);
    if (dir == 0) {
        frui_line(u->c, X(cx - a), X(cy - b), X(cx), X(cy + b), lw, col);
        frui_line(u->c, X(cx), X(cy + b), X(cx + a), X(cy - b), lw, col);
    } else {
        float d = dir == 1 ? 1.f : -1.f;
        frui_line(u->c, X(cx + b * d), X(cy - a), X(cx - b * d), X(cy), lw, col);
        frui_line(u->c, X(cx - b * d), X(cy), X(cx + b * d), X(cy + a), lw, col);
    }
}
static void panel(frui *u, float x, float y, float w, float h)
{
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(10.f), C_PANEL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(10.f), 1.f, C_BORDER);
}
static void hline(frui *u, float x0, float x1, float y, uint32_t col)
{
    frui_rect(u->c, X(x0), floorf(X(y)), X(x1 - x0), u->s >= 1.5f ? 2.f : 1.f, col);
}
static void led(frui *u, float cx, float cy, int on)
{
    if (on) {
        frui_circle_shadow(u->c, X(cx), X(cy), X(3.f), X(2.5f), FRUI_RGBA(230, 121, 8, 120));
        frui_circle(u->c, X(cx), X(cy), X(3.f), C_ACC);
    } else frui_circle(u->c, X(cx), X(cy), X(3.f), C_LED_OFF);
}

/* ---- a knob: label, ring with the value arc (from the left end, or from the top when centred), the cap and its
 * pointer, the value and unit; active = 0 draws the arc dim (the effect is off) */
#define KA0 (225.f * (float)M_PI / 180.f)
#define KSPAN (270.f * (float)M_PI / 180.f)
static void knob(frui *u, int id, const char *label, float cx, float cy, float r, int active)
{
    const frui_model *m = u->m;
    float v = (float)pval(m, id) / (float)phi(id), a = KA0 - KSPAN * v, rr = r - 2.f, cap = r - 7.5f;
    uint32_t arc = active ? C_ACC : frui_mix(C_ACC, C_TRACK, 0.6f);
    char val[24], unit[12];
    int hov = is_hover(u, FRW_KNOB, id), foc = is_focus(u, FRW_KNOB, id);
    txc(u, cx, cy - r - 13.f, T_LABEL, 0, label, hov || foc ? C_TEXT : C_TEXT2);
    frui_arc(u->c, X(cx), X(cy), X(rr), X(3.5f), KA0 - KSPAN, KA0, C_TRACK);
    if (FRP_CENTRED(id)) {
        float t = (float)M_PI * 0.5f;
        if (a < t) frui_arc(u->c, X(cx), X(cy), X(rr), X(3.5f), a, t, arc);
        else if (a > t) frui_arc(u->c, X(cx), X(cy), X(rr), X(3.5f), t, a, arc);
        else frui_circle(u->c, X(cx), X(cy - rr), X(1.75f), arc);
    } else if (v > 0.f) frui_arc(u->c, X(cx), X(cy), X(rr), X(3.5f), a, KA0, arc);
    frui_circle_shadow(u->c, X(cx), X(cy + 1.5f), X(cap), X(2.5f), C_SHADOW);
    frui_circle_grad(u->c, X(cx), X(cy), X(cap), hov ? frui_mix(C_KNOB_T, C_TEXT, 0.08f) : C_KNOB_T, C_KNOB_B);
    frui_ring(u->c, X(cx), X(cy), X(cap - 0.5f), X(1.f), FRUI_RGBA(255, 255, 255, 18));
    {
        float ca = cosf(a), sa = sinf(a);
        frui_line(u->c, X(cx + ca * cap * 0.28f), X(cy - sa * cap * 0.28f), X(cx + ca * cap * 0.76f),
                  X(cy - sa * cap * 0.76f), X(2.4f), C_TEXT);
    }
    if (foc) frui_ring(u->c, X(cx), X(cy), X(r + 3.5f), X(1.5f), frui_alpha(C_ACC_HI, 0.9f));
    u->text(u->ctx, m, id, val, unit);
    {
        float wv = tw(u, T_VALUE, FRUI_TNUM, val), wu = unit[0] ? tw(u, T_UNIT, 0, unit) + 3.f : 0.f;
        float x0 = cx - (wv + wu) * 0.5f, yb = cy + r + 22.f;
        tx(u, x0, yb, T_VALUE, FRUI_TNUM, val, C_TEXT);
        if (unit[0]) tx(u, x0 + wv + 3.f, yb, T_UNIT, 0, unit, C_TEXT2);
    }
    reg(u, FRW_KNOB, id, cx - r - 10.f, cy - r - 26.f, 2.f * r + 20.f, 2.f * r + 54.f);
}
/* four knob slots across [x0, x1], the group centred */
static void knob_row(frui *u, const int *ids, const char *const *labels, int n, float x0, float x1, float cy, float r,
                     int active)
{
    float slot = (x1 - x0) / 4.f, start, i;
    int k;
    if (slot > 110.f) slot = 110.f;
    start = 0.5f * (x0 + x1) - slot * (float)n * 0.5f;
    for (k = 0, i = 0; k < n; k++, i++) knob(u, ids[k], labels[k], start + slot * (i + 0.5f), cy, r, active);
}

/* ---- a segmented switch: equal segments; returns its width */
static float seg_width(frui *u, const char *const *names, int n)
{
    float mw = 0.f;
    int i;
    for (i = 0; i < n; i++) { float w = tw(u, T_SEG, 0, names[i]); if (w > mw) mw = w; }
    return (mw + 22.f) * (float)n;
}
static float segmented(frui *u, int id, float x, float y, float h, float w)
{
    int n, i, sel = pval(u->m, id);
    const char *const *names = opt_names(id, &n);
    float sw;
    if (w <= 0.f) w = seg_width(u, names, n);
    sw = w / (float)n;
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(6.f), C_CTRL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(6.f), 1.f, C_CTRL_B);
    for (i = 1; i < n; i++)
        if (i != sel && i != sel + 1) frui_rect(u->c, floorf(X(x + sw * (float)i)), X(y + 7.f), 1.f, X(h - 14.f), C_CTRL_B);
    if (sel >= 0 && sel < n) {
        float sx = x + sw * (float)sel + 2.f;
        frui_rrect(u->c, X(sx), X(y + 2.f), X(sw - 4.f), X(h - 4.f), X(4.5f), C_FILL);
        frui_rrect_stroke(u->c, X(sx) + 0.5f, X(y + 2.f) + 0.5f, X(sw - 4.f) - 1.f, X(h - 4.f) - 1.f, X(4.5f), 1.f,
                          frui_alpha(C_ACC, 0.85f));
    }
    for (i = 0; i < n; i++)
        txc(u, x + sw * ((float)i + 0.5f), mid(u, y + h * 0.5f, 10.f), T_SEG, 0, names[i], i == sel ? C_TEXT : C_TEXT2);
    if (is_focus(u, FRW_SEG, id))
        frui_rrect_stroke(u->c, X(x - 3.f), X(y - 3.f), X(w + 6.f), X(h + 6.f), X(8.f), X(1.5f), frui_alpha(C_ACC_HI, 0.9f));
    reg(u, FRW_SEG, id, x, y, w, h);
    return w;
}
/* a menu button: caption, the value, a chevron; returns its width */
static float menu_btn(frui *u, int id, const char *caption, float x, float y, float h)
{
    int n, sel = pval(u->m, id);
    const char *const *names = opt_names(id, &n);
    float wc = tw(u, T_SEG, 0, caption), mv = 0.f, w;
    int i, open = u->m->menu == FRUI_WID(FRW_MENU, id);
    for (i = 0; i < n; i++) { float t = tw(u, 11.f, 600.f, 0.4f, 0, names[i]); if (t > mv) mv = t; }
    w = 10.f + wc + 8.f + mv + 26.f;
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(6.f), open || is_hover(u, FRW_MENU, id) ? C_CTRL_HI : C_CTRL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(6.f), 1.f,
                      open ? frui_alpha(C_ACC, 0.85f) : C_CTRL_B);
    tx(u, x + 10.f, mid(u, y + h * 0.5f, 10.f), T_SEG, 0, caption, C_TEXT3);
    tx(u, x + 10.f + wc + 8.f, mid(u, y + h * 0.5f, 11.f), 11.f, 600.f, 0.4f, 0, sel < n ? names[sel] : "", C_TEXT);
    chevron(u, x + w - 13.f, y + h * 0.5f, 3.5f, 0, C_TEXT2);
    if (is_focus(u, FRW_MENU, id))
        frui_rrect_stroke(u->c, X(x - 3.f), X(y - 3.f), X(w + 6.f), X(h + 6.f), X(8.f), X(1.5f), frui_alpha(C_ACC_HI, 0.9f));
    reg(u, FRW_MENU, id, x, y, w, h);
    return w;
}
static float button(frui *u, int id, const char *label, float x, float y, float w, float h)
{
    int hov = is_hover(u, FRW_BUTTON, id);
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(7.f), hov ? C_CTRL_HI : C_CTRL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(7.f), 1.f, C_CTRL_B);
    if (label) txc(u, x + w * 0.5f, mid(u, y + h * 0.5f, 10.5f), T_BTN, 0, label, C_TEXT);
    else chevron(u, x + w * 0.5f, y + h * 0.5f, 5.f, id == FRB_PREV ? 1 : 2, C_TEXT2);
    reg(u, FRW_BUTTON, id, x, y, w, h);
    return w;
}

/* ---- the header */
static void header(frui *u)
{
    const frui_model *m = u->m;
    float cy = 28.f, x;
    tx(u, 22.f, mid(u, cy, 17.f), 17.f, 680.f, 2.6f, 0, "FRODHES", C_ACC);
    /* the preset: < [tag name (edited) v] > SAVE UNDO */
    x = 176.f;
    button(u, FRB_PREV, 0, x, cy - 16.f, 30.f, 32.f);
    x += 34.f;
    {
        float w = 268.f, h = 32.f, y = cy - 16.f;
        int open = m->menu == FRUI_WID(FRW_PRESET, 0);
        frui_rrect(u->c, X(x), X(y), X(w), X(h), X(7.f), open || is_hover(u, FRW_PRESET, 0) ? C_CTRL_HI : C_CTRL);
        frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(7.f), 1.f, C_CTRL_B);
        tx(u, x + 12.f, mid(u, cy, 13.f), 13.f, 640.f, 0.3f, FRUI_TNUM, m->preset_tag, C_ACC);
        tx(u, x + 50.f, mid(u, cy, 13.f), 13.f, 540.f, 0.5f, 0, m->preset_name, C_TEXT);
        if (m->edited) txr(u, x + w - 30.f, mid(u, cy, 9.f), 9.f, 600.f, 1.0f, 0, "EDITED", C_TEXT3);
        chevron(u, x + w - 15.f, cy, 3.5f, 0, C_TEXT2);
        reg(u, FRW_PRESET, 0, x, y, w, h);
        x += w + 4.f;
    }
    button(u, FRB_NEXT, 0, x, cy - 16.f, 30.f, 32.f);
    x += 30.f + 12.f;
    button(u, FRB_SAVE, "SAVE", x, cy - 16.f, 62.f, 32.f);
    x += 62.f + 6.f;
    button(u, FRB_UNDO, "UNDO", x, cy - 16.f, 62.f, 32.f);
    /* the output meter: L / R, -48 .. 0 dBFS, the peak held */
    {
        float x0 = 712.f, w = 132.f;
        int ch;
        tx(u, x0, 21.f, T_CAPTION, 0, "OUTPUT", C_TEXT3);
        for (ch = 0; ch < 2; ch++) {
            float y = 28.f + 7.f * (float)ch, lv = (m->out_db[ch] + 48.f) / 48.f, pk = (m->out_peak_db[ch] + 48.f) / 48.f;
            float wa = w * (lv < 0.f ? 0.f : lv > 1.f ? 1.f : lv), w6 = w * (42.f / 48.f);
            frui_rrect(u->c, X(x0), X(y), X(w), X(4.f), X(2.f), C_TRACK);
            if (wa > 0.f) {
                frui_rrect(u->c, X(x0), X(y), X(wa < w6 ? wa : w6), X(4.f), X(2.f), FRUI_RGB(190, 188, 180));
                if (wa > w6) frui_rrect(u->c, X(x0 + w6 - 2.f), X(y), X(wa - w6 + 2.f), X(4.f), X(2.f), C_ACC);
            }
            if (pk > 0.f && pk <= 1.f) frui_rect(u->c, X(x0 + w * pk - 1.5f), X(y), X(1.5f), X(4.f), C_TEXT);
        }
    }
    /* LEVEL: the preset's volume trim (P_LEVEL) */
    {
        float kx = 924.f, r = 14.f, v = (float)m->p[FRP_LEVEL] / 127.f, a = KA0 - KSPAN * v, t = (float)M_PI * 0.5f;
        char val[24], unit[12], buf[40];
        int foc = is_focus(u, FRW_KNOB, FRP_LEVEL);
        frui_arc(u->c, X(kx), X(cy), X(r - 1.5f), X(3.f), KA0 - KSPAN, KA0, C_TRACK);
        if (a < t) frui_arc(u->c, X(kx), X(cy), X(r - 1.5f), X(3.f), a, t, C_ACC);
        else if (a > t) frui_arc(u->c, X(kx), X(cy), X(r - 1.5f), X(3.f), t, a, C_ACC);
        frui_circle_shadow(u->c, X(kx), X(cy + 1.f), X(r - 5.5f), X(2.f), C_SHADOW);
        frui_circle_grad(u->c, X(kx), X(cy), X(r - 5.5f), C_KNOB_T, C_KNOB_B);
        frui_line(u->c, X(kx + cosf(a) * 2.f), X(cy - sinf(a) * 2.f), X(kx + cosf(a) * (r - 8.f)), X(cy - sinf(a) * (r - 8.f)),
                  X(2.f), C_TEXT);
        if (foc) frui_ring(u->c, X(kx), X(cy), X(r + 3.f), X(1.5f), frui_alpha(C_ACC_HI, 0.9f));
        u->text(u->ctx, m, FRP_LEVEL, val, unit);
        snprintf(buf, sizeof buf, "%s %s", val, unit);
        txr(u, kx - r - 10.f, 21.f, T_CAPTION, 0, "LEVEL", C_TEXT3);
        txr(u, kx - r - 10.f, 38.f, 12.f, 540.f, 0.f, FRUI_TNUM, buf, C_TEXT);
        reg(u, FRW_KNOB, FRP_LEVEL, kx - r - 4.f, cy - r - 4.f, 2.f * r + 8.f, 2.f * r + 8.f);
    }
    hline(u, 0.f, (float)FRUI_W, 54.f, C_DIV);
}

/* ---- VOICE: the TYPE and the engine's eight cards (params.c / eng_pages.h: the labels follow the engine) */
static void voice(frui *u)
{
    const frui_model *m = u->m;
    float x = 16.f, y = 66.f, w = 928.f, h = 196.f, cy = y + 132.f, gx;
    static const int ids0[4] = {FRP_TONE, FRP_BARK, FRP_BELL, FRP_HAMMER};
    static const int ids1[4] = {FRP_DECAY, FRP_RELEASE, FRP_NOISE, FRP_TUNE};
    static const char *const rh0[4] = {"TONE", "BARK", "BELL", "HAMMER"}, *const rh1[4] = {"DECAY", "RELEASE", "NOISE", "TUNE"};
    static const char *const bv0[4] = {"STRIKE", "MALLET", "TUBE", "WEIGHT"}, *const bv1[4] = {"RING", "DAMPER", "NOISE", "TUNE"};
    static const char *const bm1[4] = {"RING", "MUTE", "NOISE", "TUNE"};
    const char *cap0, *cap1;
    panel(u, x, y, w, h);
    tx(u, x + 20.f, mid(u, y + 24.f, 12.f), T_TITLE, 0, "VOICE", C_ACC);
    {
        int n;
        const char *const *names = opt_names(FRP_TYPE, &n);
        float sw = seg_width(u, names, n);
        if (sw < 560.f) sw = 560.f;
        segmented(u, FRP_TYPE, x + w - 14.f - sw, y + 11.f, 26.f, sw);
    }
    hline(u, x + 1.f, x + w - 1.f, y + 46.f, C_DIV);
    gx = x + w * 0.5f;
    frui_rect(u->c, floorf(X(gx)), X(y + 62.f), u->s >= 1.5f ? 2.f : 1.f, X(h - 78.f), C_DIV);
    if (!is_bars(m)) { cap0 = "HOW THE TINE IS STRUCK"; cap1 = "HOW NOTES DIE AWAY"; }
    else if (has_motor(m)) { cap0 = "HOW THE MALLET STRIKES"; cap1 = "HOW BARS RING AND STOP"; }
    else { cap0 = "HOW THE MALLET STRIKES"; cap1 = "HOW BARS RING; MUTE = HAND"; }
    txc(u, x + w * 0.25f, y + 70.f, T_CAPTION, 0, cap0, C_TEXT3);
    txc(u, x + w * 0.75f, y + 70.f, T_CAPTION, 0, cap1, C_TEXT3);
    knob_row(u, ids0, is_bars(m) ? bv0 : rh0, 4, x + 12.f, gx - 12.f, cy, 25.f, 1);
    knob_row(u, ids1, !is_bars(m) ? rh1 : has_motor(m) ? bv1 : bm1, 4, gx + 12.f, x + w - 12.f, cy, 25.f, 1);
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
static void tabs(frui *u, int right, float x, float y, float w)
{
    const frui_model *m = u->m;
    const char *const *names = right ? TAB_R : TAB_L;
    int n = right ? FRT_NR : FRT_NL, first = right || has_motor(m) ? 0 : 1, sel = right ? m->tab_r : m->tab_l, i;
    float tot = 0.f, gap, cx;
    for (i = first; i < n; i++) tot += tw(u, T_TAB, 0, names[i]) + 11.f;
    gap = (w - 32.f - tot) / (float)(n - first);
    if (gap > 30.f) gap = 30.f;
    cx = x + 18.f;
    for (i = first; i < n; i++) {
        float tw0 = tw(u, T_TAB, 0, names[i]), cw = tw0 + 11.f;
        int kind = right ? FRW_TAB_R : FRW_TAB_L;
        led(u, cx + 3.f, y + 21.f, eff_on(m, i, right));
        tx(u, cx + 11.f, mid(u, y + 21.f, 11.f), T_TAB, 0, names[i],
           i == sel ? C_TEXT : is_hover(u, kind, i) ? frui_mix(C_TEXT2, C_TEXT, 0.5f) : C_TEXT2);
        if (i == sel) frui_rrect(u->c, X(cx - 4.f), X(y + 40.f), X(cw + 8.f), X(2.f), X(1.f), C_ACC);
        reg(u, kind, i, cx - gap * 0.5f, y, cw + gap, 42.f);
        cx += cw + gap;
    }
    hline(u, x + 1.f, x + w - 1.f, y + 42.f, C_DIV);
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
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(6.f), C_CTRL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(6.f), 1.f, C_CTRL_B);
    for (i = 0; i < 3; i++) {
        float gx = x + w * (log10f(grid[i] / 20.f) / 3.f);
        frui_rect(u->c, floorf(X(gx)), X(y + 4.f), 1.f, X(h - 8.f), C_DIV);
        tx(u, gx + 3.f, y + h - 4.f, 8.f, 520.f, 0.5f, 0, gl[i], C_TEXT3);
    }
    frui_rect(u->c, X(x + 4.f), floorf(X(cy)), X(w - 8.f), 1.f, C_CTRL_B);
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
            if (i) frui_line(u->c, X(px), X(py), X(qx), X(qy), X(1.6f), C_ACC);
            px = qx; py = qy;
        }
        frui_path_line(&p, X(x + w), X(cy));
        frui_path_fill(u->c, &p, FRUI_RGBA(230, 121, 8, 40));
        frui_path_free(&p);
    }
    frui_clip_reset(u->c);
}

static void gr_meter(frui *u, float x, float y, float w)
{
    char buf[16];
    float g = u->m->gr_db, k = g / 12.f, bw = w - 74.f;
    tx(u, x, mid(u, y, 10.f), T_SEG, 0, "GR", C_TEXT3);
    frui_rrect(u->c, X(x + 24.f), X(y - 3.f), X(bw), X(6.f), X(3.f), C_TRACK);
    if (k > 0.f) frui_rrect(u->c, X(x + 24.f), X(y - 3.f), X(bw * (k > 1.f ? 1.f : k)), X(6.f), X(3.f), C_ACC);
    snprintf(buf, sizeof buf, g >= 0.05f ? "-%.1f dB" : "0.0 dB", (double)g);
    txr(u, x + w, mid(u, y, 11.f), 11.f, 540.f, 0.f, FRUI_TNUM, buf, C_TEXT);
}

static void pager(frui *u, float rx, float y, float h, int page)
{
    char buf[8];
    float w = 78.f, x = rx - w;
    frui_rrect(u->c, X(x), X(y), X(w), X(h), X(6.f), C_CTRL);
    frui_rrect_stroke(u->c, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(6.f), 1.f, C_CTRL_B);
    chevron(u, x + 13.f, y + h * 0.5f, 4.f, 1, page > 0 ? C_TEXT : C_CTRL_B);
    chevron(u, x + w - 13.f, y + h * 0.5f, 4.f, 2, page < 1 ? C_TEXT : C_CTRL_B);
    snprintf(buf, sizeof buf, "%d/2", page + 1);
    txc(u, x + w * 0.5f, mid(u, y + h * 0.5f, 11.f), 11.f, 600.f, 0.3f, FRUI_TNUM, buf, C_TEXT);
    reg(u, FRW_PAGER, FRT_REVERB, x, y, w, h);
}

static void left_panel(frui *u)
{
    const frui_model *m = u->m;
    float x = 16.f, y = 274.f, w = 372.f, h = 216.f, ry = y + 54.f, rh = 26.f, cy = y + 147.f, x0 = x + 16.f;
    int tab = m->tab_l;
    if (tab == FRT_MOTOR && !has_motor(m)) tab = FRT_COMP;
    panel(u, x, y, w, h);
    tabs(u, 0, x, y, w);
    switch (tab) {
    case FRT_MOTOR: {
        static const int ids[3] = {FRP_MOT_DEPTH, FRP_MOT_RATE, FRP_MOT_SHAPE};
        static const char *const lab[3] = {"DEPTH", "SPEED", "SHAPE"};
        float mw;
        segmented(u, FRP_MOT_ON, x0, ry, rh, 0.f);
        mw = tw(u, T_SEG, 0, "SYNC") + tw(u, 11.f, 600.f, 0.4f, 0, "4BAR") + 44.f;
        menu_btn(u, FRP_MOT_SYNC, "SYNC", x + w - 16.f - mw, ry, rh);
        knob_row(u, ids, lab, 3, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_MOT_ON] != 0);
        break;
    }
    case FRT_COMP: {
        static const int ids[4] = {FRP_CMP_AMT, FRP_CMP_RATIO, FRP_CMP_ATK, FRP_CMP_GAIN};
        static const char *const lab[4] = {"THRESH", "RATIO", "ATTACK", "MAKEUP"};
        float sw = segmented(u, FRP_CMP_SW, x0, ry, rh, 0.f);
        gr_meter(u, x0 + sw + 22.f, ry + rh * 0.5f, x + w - 16.f - (x0 + sw + 22.f));
        knob_row(u, ids, lab, 4, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_CMP_SW] != 0);
        break;
    }
    case FRT_EQ: {
        static const int ids[4] = {FRP_EQ_LO, FRP_EQ_MID, FRP_EQ_FREQ, FRP_EQ_HI};
        static const char *const lab[4] = {"LOW", "MID", "MID FREQ", "HIGH"};
        eq_curve(u, x0, ry - 4.f, w - 32.f, rh + 8.f);
        knob_row(u, ids, lab, 4, x + 8.f, x + w - 8.f, cy, 24.f, 1);
        break;
    }
    default: {
        static const int ids[3] = {FRP_DRIVE, FRP_DRV_TONE, FRP_DRV_MIX};
        static const char *const lab[3] = {"AMOUNT", "TONE", "MIX"};
        segmented(u, FRP_DRV_SW, x0, ry, rh, w - 32.f);
        knob_row(u, ids, lab, 3, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_DRV_SW] != 0);
        break;
    }
    }
}

static void right_panel(frui *u)
{
    const frui_model *m = u->m;
    float x = 400.f, y = 274.f, w = 544.f, h = 216.f, ry = y + 54.f, rh = 26.f, cy = y + 147.f, x0 = x + 16.f;
    float rx = x + w - 16.f;
    panel(u, x, y, w, h);
    tabs(u, 1, x, y, w);
#define SYNC_BTN(id) do { float mw_ = tw(u, T_SEG, 0, "SYNC") + tw(u, 11.f, 600.f, 0.4f, 0, "4BAR") + 44.f; \
                          menu_btn(u, (id), "SYNC", rx - mw_, ry, rh); } while (0)
    switch (m->tab_r) {
    case FRT_FILTER: {
        static const int ids[4] = {FRP_FL_FREQ, FRP_FL_RESO, FRP_FL_LFO, FRP_LFO_RATE};
        static const char *const lab[4] = {"FREQ", "RESO", "LFO", "LFO RATE"};
        float bx = x0;
        bx += menu_btn(u, FRP_FL_TYPE, "TYPE", bx, ry, rh) + 8.f;
        bx += menu_btn(u, FRP_LFO_SHAPE, "SHAPE", bx, ry, rh) + 8.f;
        bx += menu_btn(u, FRP_LFO_RETRIG, "TRIG", bx, ry, rh) + 8.f;
        SYNC_BTN(FRP_LFO_SYNC);
        knob_row(u, ids, lab, 4, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_FL_TYPE] != 0);
        break;
    }
    case FRT_TREM: {
        static const int ids[3] = {FRP_TW_DEPTH, FRP_TW_RATE, FRP_TW_SHAPE};
        static const char *const lab[3] = {"DEPTH", "RATE", "SHAPE"};
        segmented(u, FRP_TW_KIND, x0, ry, rh, 0.f);
        SYNC_BTN(FRP_TW_SYNC);
        knob_row(u, ids, lab, 3, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_TW_SW] != 0);
        break;
    }
    case FRT_PHASER: {
        static const int ids[3] = {FRP_PH_DEPTH, FRP_PH_SPEED, FRP_PH_FDBK};
        static const char *const lab[3] = {"DEPTH", "SPEED", "FEEDBK"};
        segmented(u, FRP_PH_SW, x0, ry, rh, 0.f);
        SYNC_BTN(FRP_PH_SYNC);
        knob_row(u, ids, lab, 3, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_PH_SW] != 0);
        break;
    }
    case FRT_CHORUS: {
        static const int ids[2] = {FRP_CP_DEPTH, FRP_CP_SPEED};
        static const char *const lab[2] = {"DEPTH", "SPEED"};
        segmented(u, FRP_CP_SW, x0, ry, rh, 0.f);
        SYNC_BTN(FRP_CP_SYNC);
        knob_row(u, ids, lab, 2, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_CP_SW] != 0);
        break;
    }
    case FRT_DELAY: {
        static const int ids[4] = {FRP_DL_DEPTH, FRP_DL_TIME, FRP_DL_FDBK, FRP_DL_TONE};
        static const char *const lab[4] = {"MIX", "TIME", "FEEDBK", "TONE"};
        segmented(u, FRP_DL_SW, x0, ry, rh, 0.f);
        SYNC_BTN(FRP_DL_SYNC);
        knob_row(u, ids, lab, 4, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_DL_SW] != 0);
        break;
    }
    default: {
        static const int ids0[3] = {FRP_RV_DEPTH, FRP_RV_DECAY, FRP_RV_TONE};
        static const char *const lab0[3] = {"MIX", "DECAY", "TONE"};
        static const int ids1[4] = {FRP_RV_PREDLY, FRP_RV_SIZE, FRP_RV_MOD, FRP_RV_LOCUT};
        static const char *const lab1[4] = {"PRE-DELAY", "SIZE", "MOD", "LOW CUT"};
        segmented(u, FRP_RV_TYPE, x0, ry, rh, 0.f);
        pager(u, rx, ry, rh, m->page_rv);
        if (m->page_rv) knob_row(u, ids1, lab1, 4, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_RV_TYPE] != 0);
        else knob_row(u, ids0, lab0, 3, x + 8.f, x + w - 8.f, cy, 24.f, m->p[FRP_RV_TYPE] != 0);
        break;
    }
    }
#undef SYNC_BTN
}

/* ---- the keyboard: C1 .. C6 (MIDI 24 .. 84), the held notes lit */
static void keyboard(frui *u)
{
    const frui_model *m = u->m;
    float x = 16.f, y = 502.f, w = 928.f, h = 82.f, ww = w / 36.f, bw = ww * 0.6f, bh = h * 0.6f;
    static const int white_of[12] = {0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6};
    int n;
    frui_rrect(u->c, X(x - 1.f), X(y - 1.f), X(w + 2.f), X(h + 2.f), X(7.f), FRUI_RGB(4, 4, 5));
    frui_clip(u->c, X(x), X(y), X(w), X(h));
    for (n = 24; n <= 84; n++) {
        int pc = n % 12, oct = n / 12 - 1, wi = white_of[pc];
        if (wi >= 0) {
            float kx = x + ww * (float)((oct - 1) * 7 + wi);
            int on = m->notes[n] != 0;
            frui_rrect_grad(u->c, X(kx + 0.5f), X(y - 6.f), X(ww - 1.f), X(h + 6.f), X(4.f),
                            on ? FRUI_RGB(246, 160, 70) : FRUI_RGB(224, 223, 218), on ? C_ACC : FRUI_RGB(200, 199, 194));
            if (pc == 0) {
                char lb[8];
                snprintf(lb, sizeof lb, "C%d", oct);
                txc(u, kx + ww * 0.5f, y + h - 7.f, 8.5f, 620.f, 0.3f, 0, lb, on ? FRUI_RGB(70, 34, 0) : FRUI_RGB(88, 87, 92));
            }
        }
    }
    for (n = 24; n <= 84; n++) {
        int pc = n % 12, oct = n / 12 - 1;
        if (white_of[pc] < 0) {
            float kx = x + ww * (float)((oct - 1) * 7 + white_of[pc - 1] + 1) - bw * 0.5f;
            int on = m->notes[n] != 0;
            frui_rrect(u->c, X(kx - 0.5f), X(y - 6.f), X(bw + 1.f), X(bh + 7.f), X(3.f), FRUI_RGB(8, 8, 9));
            frui_rrect_grad(u->c, X(kx + 1.f), X(y - 6.f), X(bw - 2.f), X(bh + 4.f), X(2.5f),
                            on ? C_ACC : FRUI_RGB(30, 30, 33), on ? FRUI_RGB(176, 88, 0) : FRUI_RGB(52, 51, 56));
        }
    }
    /* the strip's top edge: a soft shadow, as if the keys sit under the panel */
    {
        int i;
        for (i = 0; i < 6; i++) frui_rect(u->c, X(x), X(y + (float)i), X(w), X(1.f), FRUI_RGBA(0, 0, 0, 110 - 18 * i));
    }
    frui_clip_reset(u->c);
    reg(u, FRW_KEYS, 0, x, y, w, h);
}

/* ---- an open menu, over everything */
static void popup(frui *u)
{
    const frui_model *m = u->m;
    int i, n, sel, cols, rows, id;
    const frui_widget *g = 0;
    const char *const *names;
    float ih = 24.f, cw, pw, ph, px, py;
    if (m->menu < 0) return;
    for (i = 0; i < u->nw; i++) if (FRUI_WID(u->wg[i].kind, u->wg[i].id) == m->menu) g = &u->wg[i];
    if (!g || g->kind != FRW_MENU) return;
    id = g->id;
    names = opt_names(id, &n);
    sel = pval(m, id);
    cols = n > 8 ? 3 : 1;
    rows = (n + cols - 1) / cols;
    cw = 0.f;
    for (i = 0; i < n; i++) { float t = tw(u, 11.f, 600.f, 0.4f, 0, names[i]) + 34.f; if (t > cw) cw = t; }
    if (cols == 1 && cw < g->w) cw = g->w;
    pw = cw * (float)cols + 8.f; ph = ih * (float)rows + 8.f;
    px = g->x + g->w - pw > 8.f ? g->x + g->w - pw : 8.f;
    if (cols == 1) px = g->x;
    py = g->y + g->h + 4.f;
    if (py + ph > (float)FRUI_H - 8.f) py = g->y - 4.f - ph;
    frui_rrect_shadow(u->c, X(px), X(py + 4.f), X(pw), X(ph), X(8.f), X(10.f), FRUI_RGBA(0, 0, 0, 170));
    frui_rrect(u->c, X(px), X(py), X(pw), X(ph), X(8.f), C_POP);
    frui_rrect_stroke(u->c, X(px) + 0.5f, X(py) + 0.5f, X(pw) - 1.f, X(ph) - 1.f, X(8.f), 1.f, FRUI_RGB(54, 53, 58));
    for (i = 0; i < n; i++) {
        int c = i / rows, r = i % rows;
        float ix = px + 4.f + cw * (float)c, iy = py + 4.f + ih * (float)r;
        if (i == sel) {
            frui_rrect(u->c, X(ix), X(iy), X(cw), X(ih), X(5.f), C_FILL);
            frui_line(u->c, X(ix + 9.f), X(iy + 12.5f), X(ix + 12.f), X(iy + 15.5f), X(1.6f), C_ACC_HI);
            frui_line(u->c, X(ix + 12.f), X(iy + 15.5f), X(ix + 18.f), X(iy + 8.5f), X(1.6f), C_ACC_HI);
        }
        tx(u, ix + 24.f, mid(u, iy + ih * 0.5f, 11.f), 11.f, 600.f, 0.4f, 0, names[i], i == sel ? C_TEXT : C_TEXT2);
    }
}

frui *frui_new(frui_font *font, frui_text_fn text, void *ctx)
{
    frui *u = calloc(1, sizeof *u);
    u->f = font; u->text = text; u->ctx = ctx;
    return u;
}
void frui_free(frui *u) { free(u); }

void frui_draw(frui *u, const frui_model *m, frui_canvas *c)
{
    u->c = c; u->m = m; u->nw = 0;
    u->s = (float)c->w / (float)FRUI_W;
    frui_clip_reset(c);
    frui_fill(c, C_BG);
    header(u);
    voice(u);
    left_panel(u);
    right_panel(u);
    keyboard(u);
    popup(u);
}

int frui_hit(const frui *u, float x, float y, frui_widget *w)
{
    int i;
    for (i = u->nw - 1; i >= 0; i--) {
        const frui_widget *g = &u->wg[i];
        if (x >= g->x && x < g->x + g->w && y >= g->y && y < g->y + g->h) { if (w) *w = *g; return i; }
    }
    return -1;
}
