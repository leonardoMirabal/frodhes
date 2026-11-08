/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's controller (plugin/gui_ctl.h): frui's widgets driven by the platform's input. Plain C99, no
 * platform header: the same code behind the Windows and the macOS view, and testable headless. */
#include "gui_ctl.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define DRAG_PX 200.f              /* logical pixels of vertical drag for a knob's whole range (SHIFT: x 10) */
#define FINE 10.f

struct frg_ctl {
    frg_ctl_host h;
    frg_client cl;
    frui *u;
    frui_model m;
    int drawn;                     /* frui has drawn once (its widgets are known) */
    /* the drag in progress */
    int drag_kind, drag_id;        /* -1: none */
    float drag_x, drag_y, drag_v;  /* where it started / the last point; the value as a float */
    float fad_x, fad_w;            /* (a fader: its left edge and width) */
    int note;                      /* the keys strip's note down, or -1 */
};

static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

static void refresh(frg_ctl *c)
{
    if (c->h.refresh) c->h.refresh(c->h.ctx, &c->m);
}
static void set_val(frg_ctl *c, int id, int v, int gesture)
{
    v = clampi(v, 0, frui_range(id));
    if (gesture && c->h.gesture) c->h.gesture(c->h.ctx, id, 1);
    if (c->h.set) c->h.set(c->h.ctx, id, v);
    if (gesture && c->h.gesture) c->h.gesture(c->h.ctx, id, 0);
    refresh(c);
}

/* the keys strip: the note under a logical point (frui.c keyboard(): C1 .. C6, 36 white keys, the black keys over
 * the upper 60 %) */
static int key_at(const frui_widget *g, float x, float y)
{
    static const int white_of[12] = {0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6};
    static const int white_pc[7] = {0, 2, 4, 5, 7, 9, 11};
    float ww = g->w / 36.f, bw = ww * 0.58f, bh = g->h * 0.6f;
    int n, wi;
    if (y < g->y + bh)
        for (n = 25; n <= 84; n++) {
            int pc = n % 12, oct = n / 12 - 1;
            float kx;
            if (white_of[pc] >= 0) continue;
            kx = g->x + ww * (float)((oct - 1) * 7 + white_of[pc - 1] + 1) - bw * 0.5f;
            if (x >= kx && x < kx + bw) return n;
        }
    wi = (int)floorf((x - g->x) / ww);
    if (wi < 0 || wi > 35) return -1;
    return 24 + 12 * (wi / 7) + white_pc[wi % 7];
}

static int widget_at(frg_ctl *c, float x, float y, frui_widget *w)
{
    if (!c->drawn) return -1;
    return frui_hit(c->u, x, y, w);
}
static int is_value(int kind) { return kind == FRW_KNOB || kind == FRW_FADER || kind == FRW_SEG || kind == FRW_MENU; }

/* the focus order: the widgets of the last draw that take a value, in drawing order */
static int focus_step(frg_ctl *c, int dir)
{
    int n = frui_widget_count(c->u), i, cur = -1, k;
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(c->u, i);
        if (FRUI_WID(g->kind, g->id) == c->m.focus) cur = i;
    }
    for (k = 1; k <= n; k++) {
        i = ((cur < 0 ? (dir > 0 ? -1 : 0) : cur) + dir * k + n * 2) % n;
        if (is_value(frui_widget_get(c->u, i)->kind)) {
            const frui_widget *g = frui_widget_get(c->u, i);
            c->m.focus = FRUI_WID(g->kind, g->id);
            return 1;
        }
    }
    return 0;
}

static int close_popups(frg_ctl *c)
{
    int r = c->m.menu >= 0 || c->m.ctx_knob >= 0;
    c->m.menu = -1;
    c->m.ctx_knob = -1;
    return r;
}

static int on_down(frg_ctl *c, const frg_input *e)
{
    frui_widget w;
    int i = widget_at(c, e->x, e->y, &w);
    refresh(c);                          /* (the values as they are now, not as of the last tick) */
    /* an open pop-up: its options, or a click anywhere else closes it (and does nothing more) */
    if (c->m.menu >= 0 || c->m.ctx_knob >= 0) {
        if (i >= 0 && w.kind == FRW_ITEM && c->m.menu >= 0 && e->button == FRG_LEFT) {
            int id = c->m.menu & 0xff;
            c->m.menu = -1;
            set_val(c, id, w.id, 1);
            return 1;
        }
        if (i >= 0 && w.kind == FRW_CTX && e->button == FRG_LEFT) {
            int id = c->m.ctx_knob;
            c->m.ctx_knob = -1;
            if (w.id == FRC_DEFAULT) { if (c->h.def) set_val(c, id, c->h.def(c->h.ctx, id), 1); }
            else if (c->h.learn) c->h.learn(c->h.ctx, id, w.id);
            refresh(c);
            return 1;
        }
        close_popups(c);
        return 1;
    }
    if (i < 0) return 0;
    if (e->button == FRG_RIGHT) {
        if (w.kind == FRW_KNOB && w.id < FRS_CHANNEL) { c->m.ctx_knob = w.id; c->m.focus = FRUI_WID(w.kind, w.id); return 1; }
        return 0;
    }
    if (e->button != FRG_LEFT) return 0;
    if (c->m.learning >= 0 && c->h.learn) c->h.learn(c->h.ctx, -1, FRC_LEARN);   /* any click cancels a learn */
    switch (w.kind) {
    case FRW_KNOB: case FRW_FADER:
        c->m.focus = FRUI_WID(w.kind, w.id);
        c->drag_kind = w.kind; c->drag_id = w.id;
        c->drag_x = e->x; c->drag_y = e->y;
        c->drag_v = (float)frui_value(&c->m, w.id);
        c->fad_x = w.x; c->fad_w = w.w;
        if (c->h.gesture) c->h.gesture(c->h.ctx, w.id, 1);
        if (w.kind == FRW_FADER && w.w > 0.f) {   /* a fader jumps to the click */
            int hi = frui_range(w.id);
            c->drag_v = floorf((e->x - w.x) / w.w * (float)hi + 0.5f);
            if (c->h.set) c->h.set(c->h.ctx, w.id, clampi((int)c->drag_v, 0, hi));
            refresh(c);
        }
        return 1;
    case FRW_SEG: {
        int n = frui_range(w.id) + 1, s = (int)floorf((e->x - w.x) / (w.w / (float)n));
        c->m.focus = FRUI_WID(w.kind, w.id);
        set_val(c, w.id, clampi(s, 0, n - 1), 1);
        return 1;
    }
    case FRW_MENU:
        c->m.focus = FRUI_WID(w.kind, w.id);
        c->m.menu = FRUI_WID(FRW_MENU, w.id);
        return 1;
    case FRW_TAB_L: c->m.tab_l = w.id; return 1;
    case FRW_TAB_R: c->m.tab_r = w.id; return 1;
    case FRW_PAGER: c->m.page_rv = !c->m.page_rv; return 1;
    case FRW_PRESET:
        if (c->h.preset_menu) c->h.preset_menu(c->h.ctx, w.x, w.y + w.h);
        return 1;
    case FRW_BUTTON:
        if (w.id == FRB_GEAR) { c->m.settings = !c->m.settings; return 1; }
        if (w.id == FRB_CLOSE) { c->m.settings = 0; return 1; }
        if (c->h.button) c->h.button(c->h.ctx, w.id);
        refresh(c);
        return 1;
    case FRW_KEYS: {
        int n = key_at(&w, e->x, e->y);
        if (n >= 0 && c->h.note) {
            /* velocity from the height on the key: softer at the top, as a real key struck near its pivot */
            float t = (e->y - w.y) / (w.h > 0.f ? w.h : 1.f);
            c->h.note(c->h.ctx, n, clampi((int)(40.f + 87.f * t), 1, 127));
            c->note = n;
        }
        return 1;
    }
    default: return 0;
    }
}

static int on_drag(frg_ctl *c, const frg_input *e)
{
    int hi = frui_range(c->drag_id), v0 = frui_value(&c->m, c->drag_id), v;
    float scale = e->mods & FRG_SHIFT ? 1.f / FINE : 1.f;
    if (c->drag_kind == FRW_KNOB) c->drag_v += (c->drag_y - e->y) * scale * (float)hi / DRAG_PX;
    else c->drag_v += (e->x - c->drag_x) * scale * (float)hi / (c->fad_w > 0.f ? c->fad_w : DRAG_PX);
    c->drag_x = e->x; c->drag_y = e->y;
    if (c->drag_v < 0.f) c->drag_v = 0.f;
    if (c->drag_v > (float)hi) c->drag_v = (float)hi;
    v = (int)floorf(c->drag_v + 0.5f);
    if (v == v0) return 0;
    if (c->h.set) c->h.set(c->h.ctx, c->drag_id, v);
    refresh(c);
    return 1;
}

static int step_value(frg_ctl *c, int id, int kind, int d)
{
    int v = frui_value(&c->m, id), hi = frui_range(id);
    if (kind != FRW_KNOB && kind != FRW_FADER && kind != FRW_SEG && kind != FRW_MENU) return 0;
    if (clampi(v + d, 0, hi) == v) return 0;
    set_val(c, id, v + d, 1);
    return 1;
}

static int on_key(frg_ctl *c, const frg_input *e)
{
    int k = e->key, id = c->m.focus & 0xff, kind = c->m.focus >= 0 ? c->m.focus >> 8 : -1, hi, big;
    if (k == FRK_ESCAPE) {
        if (close_popups(c)) return 3;
        if (c->m.settings) { c->m.settings = 0; return 3; }
        if (c->m.focus >= 0) { c->m.focus = -1; return 3; }
        return 0;
    }
    if (k == FRK_TAB) { focus_step(c, e->mods & FRG_SHIFT ? -1 : 1); return 3; }
    if (kind < 0) return 0;
    hi = frui_range(id);
    big = hi > 20 ? 10 : 1;
    switch (k) {
    case FRK_UP: case FRK_RIGHT: return step_value(c, id, kind, e->mods & FRG_SHIFT && hi > 20 ? 10 : 1) | 2;
    case FRK_DOWN: case FRK_LEFT: return step_value(c, id, kind, e->mods & FRG_SHIFT && hi > 20 ? -10 : -1) | 2;
    case FRK_PGUP: return step_value(c, id, kind, big) | 2;
    case FRK_PGDN: return step_value(c, id, kind, -big) | 2;
    case FRK_HOME: return step_value(c, id, kind, -hi) | 2;
    case FRK_END: return step_value(c, id, kind, hi) | 2;
    case FRK_RETURN:
        if (kind == FRW_MENU) { c->m.menu = c->m.menu >= 0 ? -1 : FRUI_WID(FRW_MENU, id); return 3; }
        return 0;
    case FRK_DELETE: case FRK_BACKSPACE:
        if (c->h.def) { set_val(c, id, c->h.def(c->h.ctx, id), 1); return 3; }
        return 0;
    default: return 0;
    }
}

/* ---- the frg_client */
static void cl_paint(void *ctx, frui_canvas *cv, float scale)
{
    frg_ctl *c = ctx;
    (void)scale;
    refresh(c);
    frui_draw(c->u, &c->m, cv);
    c->drawn = 1;
}
static int cl_input(void *ctx, const frg_input *e)
{
    frg_ctl *c = ctx;
    frui_widget w;
    int i, r = 0;
    switch (e->kind) {
    case FRG_DOWN: return on_down(c, e) ? 3 : 0;
    case FRG_UP:
        if (c->drag_kind >= 0) {
            if (c->h.gesture) c->h.gesture(c->h.ctx, c->drag_id, 0);
            c->drag_kind = -1;
            r = 3;
        }
        if (c->note >= 0) { if (c->h.note) c->h.note(c->h.ctx, c->note, 0); c->note = -1; r = 3; }
        return r;
    case FRG_MOVE:
        if (c->drag_kind >= 0) return on_drag(c, e) ? 3 : 2;
        i = widget_at(c, e->x, e->y, &w);
        r = i >= 0 ? FRUI_WID(w.kind, w.id) : -1;
        if (r == c->m.hover) return 0;
        c->m.hover = r;
        return 1;
    case FRG_LEAVE:
        if (c->m.hover < 0) return 0;
        c->m.hover = -1;
        return 1;
    case FRG_DOUBLE:
        if (e->button != FRG_LEFT) return 0;
        i = widget_at(c, e->x, e->y, &w);
        if (i >= 0 && (w.kind == FRW_KNOB || w.kind == FRW_FADER) && c->h.def) {
            if (c->drag_kind >= 0) {      /* (the first click's press began a drag: end it) */
                if (c->h.gesture) c->h.gesture(c->h.ctx, c->drag_id, 0);
                c->drag_kind = -1;
            }
            set_val(c, w.id, c->h.def(c->h.ctx, w.id), 1);
            return 3;
        }
        return on_down(c, e) ? 3 : 0;     /* (elsewhere: a second click) */
    case FRG_WHEEL: {
        int d;
        if (c->drag_kind >= 0) return 2;
        refresh(c);
        i = widget_at(c, e->x, e->y, &w);
        if (i < 0) return 0;
        if (w.kind == FRW_CCMAP) {
            c->m.cc_scroll = c->m.cc_scroll - (e->wheel > 0.f ? 1 : -1);
            if (c->m.cc_scroll < 0) c->m.cc_scroll = 0;
            return 3;
        }
        if (w.kind == FRW_ITEM || w.kind == FRW_CTX) return 2;
        d = e->wheel > 0.f ? 1 : e->wheel < 0.f ? -1 : 0;
        if (!d) return 0;
        if ((w.kind == FRW_KNOB || w.kind == FRW_FADER) && frui_range(w.id) > 20 && !(e->mods & FRG_SHIFT)) d *= 2;
        return step_value(c, w.id, w.kind, d) ? 3 : 2;
    }
    case FRG_KEY: refresh(c); return on_key(c, e);
    case FRG_FOCUS_LOST:
        if (c->drag_kind >= 0) { if (c->h.gesture) c->h.gesture(c->h.ctx, c->drag_id, 0); c->drag_kind = -1; }
        if (c->note >= 0) { if (c->h.note) c->h.note(c->h.ctx, c->note, 0); c->note = -1; }
        return 1;
    default: return 0;
    }
}
static int cl_tick(void *ctx)
{
    frg_ctl *c = ctx;
    frui_model before = c->m;
    refresh(c);
    return memcmp(&before, &c->m, sizeof before) != 0;
}

frg_ctl *frg_ctl_new(const frg_ctl_host *host, frui_font *font, frui_font *display, int look)
{
    frg_ctl *c = calloc(1, sizeof *c);
    if (!c) return 0;
    c->h = *host;
    c->u = frui_new(font, host->text, host->ctx);
    if (!c->u) { free(c); return 0; }
    frui_set_look(c->u, look, display);
    c->m.focus = c->m.hover = c->m.menu = c->m.ctx_knob = c->m.learning = -1;
    c->drag_kind = -1;
    c->note = -1;
    c->cl.ctx = c;
    c->cl.paint = cl_paint;
    c->cl.input = cl_input;
    c->cl.tick = cl_tick;
    refresh(c);
    return c;
}
void frg_ctl_free(frg_ctl *c)
{
    if (!c) return;
    frui_free(c->u);
    free(c);
}
const frg_client *frg_ctl_client(frg_ctl *c) { return &c->cl; }
const frui_model *frg_ctl_model(const frg_ctl *c) { return &c->m; }
const frui *frg_ctl_frui(const frg_ctl *c) { return c->u; }
