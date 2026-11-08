/* SPDX-License-Identifier: GPL-3.0-only */
/* The window's core (plugin/gui_core.c) through its platform client, headless: frui draws into a memory canvas as a platform view would,
 * and synthetic input checks the mapping every platform shares (knob drag / SHIFT fine / wheel / double-click
 * default / the right-click menu, segments, menus and their options, the keys strip, TAB + arrows, the gear, ESC,
 * hover) and that every write is inside a gesture. Prints PASS / FAIL lines; exit 0 when all pass. */
#include <stdlib.h>
#include <time.h>
#include "gui_fake_host.h"

static int fails;
#define CHECK(c, ...) do { if (c) printf("PASS "); else { printf("FAIL "); fails++; } printf(__VA_ARGS__); printf("\n"); } while (0)

static frp_gui_t *ctl;
static const frg_client *cl;
static uint32_t *px;
static frui_canvas cv;
static int ev(int kind, int button, int mods, float x, float y, float wheel, int key)
{
    frg_input e;
    memset(&e, 0, sizeof e);
    e.kind = kind; e.button = button; e.mods = mods; e.x = x; e.y = y; e.wheel = wheel; e.key = key;
    return cl->input(cl->ctx, &e);
}
static void paint(void) { frui_canvas_init(&cv, px, cv.w, cv.h); cl->paint(cl->ctx, &cv, 1.f); }

/* the window's cache (frui_set_layer): the static layer, then the dynamic one over a copy of it, is pixel for pixel
 * one whole draw, for every look (A3 at 1x / 1.5x / 2x, the others at 1x), over models that open every pop-up and state */
static int layer_check(frui_font *f, frui_font *d)
{
    static const int sz[3] = {960, 1440, 1920};
    frui *u = frui_new(f, fh_text, 0);
    int look, k, sc, i, bad = 0, runs = 0;
    unsigned seed = 7;
    for (look = 0; look < 3; look++)
        for (k = 0; k < (look == FRUI_LOOK_A3 ? 3 : 1); k++)
            for (sc = 0; sc < 8; sc++) {
                int W = sz[k], H = W * FRUI_H / FRUI_W;
                uint32_t *a = calloc((size_t)W * H, 4), *b = calloc((size_t)W * H, 4);
                frui_canvas ca, cb;
                frui_model m;
                memset(&m, 0, sizeof m);
                m.focus = m.hover = m.menu = m.ctx_knob = m.learning = -1;
                for (i = 0; i < FRP_COUNT; i++) { seed = seed * 1103515245u + 12345u; m.p[i] = (uint8_t)((seed >> 16) % 128u); }
                m.p[FRP_TYPE] = (uint8_t)(sc % 6); m.tab_l = sc % 4; m.tab_r = sc % 6; m.page_rv = sc & 1;
                m.out_db[0] = -6.f; m.out_peak_db[1] = -2.f; m.gr_db = -5.f; m.notes[48] = 100; m.notes[61] = 90; m.sustain = sc & 1;
                if (sc == 1) m.settings = 1;
                if (sc == 2) m.menu = FRUI_WID(FRW_MENU, FRP_TYPE);
                if (sc == 3) m.ctx_knob = FRP_BARK;
                if (sc == 4) { m.fx_bypass = 1; m.hover = FRUI_WID(FRW_KNOB, FRP_TONE); m.focus = FRUI_WID(FRW_KNOB, FRP_BELL); }
                if (sc == 5) m.learning = FRP_DECAY;
                if (sc == 6) { m.settings = 1; m.menu = FRUI_WID(FRW_MENU, FRS_SYNCDIV); }
                if (sc == 7) m.learn_cc[FRP_TONE] = 21;
                frui_set_look(u, look, d);
                frui_canvas_init(&ca, a, W, H);
                frui_set_layer(u, FRUI_LAYER_ALL);
                frui_draw(u, &m, &ca);
                frui_canvas_init(&cb, b, W, H);
                frui_set_layer(u, FRUI_LAYER_STATIC);
                frui_draw(u, &m, &cb);
                frui_set_layer(u, FRUI_LAYER_DYNAMIC);
                frui_draw(u, &m, &cb);
                for (i = 0; i < W * H; i++) bad += a[i] != b[i];
                runs++;
                free(a); free(b);
            }
    frui_free(u);
    CHECK(bad == 0, "the cached static layer + the dynamic one = one whole draw: %d pictures, %d pixels differ", runs, bad);
    return bad;
}

/* the full repaint's time at 2x (1920 x 1200): every pixel redrawn (a switch moved: the layout changes), the static
 * layer from the cache; the median of 21 (printed, not checked: the build machine's load decides) */
static double ms_now(void)
{
    struct timespec t;
    timespec_get(&t, TIME_UTC);
    return (double)t.tv_sec * 1e3 + (double)t.tv_nsec * 1e-6;
}
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }
static void time_full(void)
{
    fake_host h2;
    frp_ui_host_t uh;
    frp_gui_t *g;
    double t[21], t0, first;
    int i, d[4];
    fh_init(&h2, &uh);
    if (!(g = frp_gui_new(&uh)) || frp_gui_resize(g, 1920, 1200)) { CHECK(0, "a window core at 2x"); return; }
    frp_gui_tick(g, 0.0);
    t0 = ms_now();
    frp_gui_paint(g, d);
    first = ms_now() - t0;
    for (i = 0; i < 21; i++) {
        h2.v[FRP_TYPE] = i & 1 ? 0 : 4;          /* (a Rhodes / a vibraphone: the layout changes, all of it repaints) */
        frp_gui_tick(g, 0.1 * (i + 1));
        t0 = ms_now();
        frp_gui_paint(g, d);
        t[i] = ms_now() - t0;
    }
    qsort(t, 21, sizeof t[0], cmpd);
    printf("TIME the first paint at 2x (the static layer cached): %.1f ms; a full repaint at 2x: median %.1f ms, "
           "min %.1f, max %.1f\n", first, t[10], t[0], t[20]);
    CHECK(d[2] - d[0] == 1920 && d[3] - d[1] == 1200, "the repaint was a full one (%d x %d)", d[2] - d[0], d[3] - d[1]);
    frp_gui_free(g);
}

int main(void)
{
    fake_host h;
    frp_ui_host_t ch;
    frui_font *f = frui_font_load(frp_font_text, frp_font_text_len), *d = frui_font_load(frp_font_display, frp_font_display_len);
    float x, y;
    int v, i, nonblack = 0;
    CHECK(f && d, "the embedded fonts load (%lu + %lu bytes)", frp_font_text_len, frp_font_display_len);
    if (!f || !d) return 1;
    layer_check(f, d);
    time_full();
    fh_init(&h, &ch);
    ctl = frp_gui_new(&ch);
    CHECK(ctl != 0, "the window core starts");
    if (!ctl) return 1;
    cl = frp_gui_client(ctl);
    cv.w = 1440; cv.h = 900;                       /* 1.5x */
    px = calloc((size_t)cv.w * cv.h, 4);
    paint();
    for (i = 0; i < cv.w * cv.h; i += 97) nonblack += (px[i] & 0xffffff) != 0;
    CHECK(nonblack > 1000, "the window draws at 1.5x (%d sampled pixels not black)", nonblack);

    /* a knob: drag up 100 px = half the range, SHIFT 100 px = a twentieth, the wheel, the double-click */
    CHECK(fh_find(frp_gui_frui(ctl), FRW_KNOB, FRP_BARK, &x, &y) >= 0, "the BARK knob is drawn");
    v = h.v[FRP_BARK];
    ev(FRG_DOWN, FRG_LEFT, 0, x, y, 0, 0);
    CHECK(h.gestures_open == 1, "a press on a knob begins a gesture");
    ev(FRG_MOVE, 0, 0, x, y - 50.f, 0, 0); ev(FRG_MOVE, 0, 0, x, y - 100.f, 0, 0);
    CHECK(h.v[FRP_BARK] == v + 64 || h.v[FRP_BARK] == 127, "drag up 100 px: %d -> %d (half the range)", v, h.v[FRP_BARK]);
    ev(FRG_UP, FRG_LEFT, 0, x, y - 100.f, 0, 0);
    CHECK(h.gestures_open == 0, "the release ends it");
    h.v[FRP_BARK] = 20;
    ev(FRG_DOWN, FRG_LEFT, FRG_SHIFT, x, y, 0, 0);
    ev(FRG_MOVE, 0, FRG_SHIFT, x, y - 100.f, 0, 0);
    ev(FRG_UP, FRG_LEFT, FRG_SHIFT, x, y - 100.f, 0, 0);
    CHECK(h.v[FRP_BARK] == 26, "SHIFT drag up 100 px: 20 -> %d (fine: 6)", h.v[FRP_BARK]);
    ev(FRG_WHEEL, 0, 0, x, y, 1.f, 0);
    CHECK(h.v[FRP_BARK] == 28, "a wheel notch up: +2 (%d)", h.v[FRP_BARK]);
    ev(FRG_WHEEL, 0, FRG_SHIFT, x, y, -1.f, 0);
    CHECK(h.v[FRP_BARK] == 27, "SHIFT + a notch down: -1 (%d)", h.v[FRP_BARK]);
    ev(FRG_DOWN, FRG_LEFT, 0, x, y, 0, 0); ev(FRG_UP, FRG_LEFT, 0, x, y, 0, 0);
    ev(FRG_DOUBLE, FRG_LEFT, 0, x, y, 0, 0); ev(FRG_UP, FRG_LEFT, 0, x, y, 0, 0);
    CHECK(h.v[FRP_BARK] == 64 && h.gestures_open == 0, "double-click: the default (%d), gestures closed (%d)",
          h.v[FRP_BARK], h.gestures_open);

    /* the right-click menu: DEFAULT, MIDI LEARN */
    h.v[FRP_BARK] = 3;
    ev(FRG_DOWN, FRG_RIGHT, 0, x, y, 0, 0); ev(FRG_UP, FRG_RIGHT, 0, x, y, 0, 0);
    CHECK(frp_gui_model(ctl)->ctx_knob == FRP_BARK, "right-click opens the knob's menu");
    paint();
    {
        float mx, my;
        CHECK(fh_find(frp_gui_frui(ctl), FRW_CTX, FRC_DEFAULT, &mx, &my) >= 0, "the menu's DEFAULT is drawn");
        ev(FRG_DOWN, FRG_LEFT, 0, mx, my, 0, 0); ev(FRG_UP, FRG_LEFT, 0, mx, my, 0, 0);
        CHECK(h.v[FRP_BARK] == 64 && frp_gui_model(ctl)->ctx_knob < 0, "DEFAULT sets it (%d) and closes", h.v[FRP_BARK]);
        ev(FRG_DOWN, FRG_RIGHT, 0, x, y, 0, 0); ev(FRG_UP, FRG_RIGHT, 0, x, y, 0, 0);
        paint();
        fh_find(frp_gui_frui(ctl), FRW_CTX, FRC_LEARN, &mx, &my);
        ev(FRG_DOWN, FRG_LEFT, 0, mx, my, 0, 0); ev(FRG_UP, FRG_LEFT, 0, mx, my, 0, 0);
        CHECK(h.learns == 1 && h.last_learn == FRP_UI_LEARN_START, "MIDI LEARN reaches the host");
    }
    paint();

    /* a segmented control: the last segment */
    {
        int k = fh_find(frp_gui_frui(ctl), FRW_SEG, -1, &x, &y);
        const frui_widget *g = k >= 0 ? frui_widget_get(frp_gui_frui(ctl), k) : 0;
        CHECK(g != 0, "a segmented control is drawn");
        if (g) {
            int before = h.gesture_begins;
            ev(FRG_DOWN, FRG_LEFT, 0, g->x + g->w - 2.f, y, 0, 0); ev(FRG_UP, FRG_LEFT, 0, g->x + g->w - 2.f, y, 0, 0);
            CHECK(frui_value(frp_gui_model(ctl), g->id) == frui_range(g->id) && h.gesture_begins == before + 1,
                  "a click on its last segment: %d of %d, inside one gesture", frui_value(frp_gui_model(ctl), g->id),
                  frui_range(g->id));
        }
    }
    paint();

    /* the instrument menu: open, pick the fourth option */
    CHECK(fh_find(frp_gui_frui(ctl), FRW_MENU, FRP_TYPE, &x, &y) >= 0, "the instrument menu is drawn");
    ev(FRG_DOWN, FRG_LEFT, 0, x, y, 0, 0); ev(FRG_UP, FRG_LEFT, 0, x, y, 0, 0);
    CHECK(frp_gui_model(ctl)->menu == FRUI_WID(FRW_MENU, FRP_TYPE), "a click opens it");
    paint();
    {
        float ix, iy;
        CHECK(fh_find(frp_gui_frui(ctl), FRW_ITEM, 3, &ix, &iy) >= 0, "its options are hit targets (FRW_ITEM)");
        ev(FRG_DOWN, FRG_LEFT, 0, ix, iy, 0, 0); ev(FRG_UP, FRG_LEFT, 0, ix, iy, 0, 0);
        CHECK(h.v[FRP_TYPE] == 3 && frp_gui_model(ctl)->menu < 0, "picking BELL DYNO: TYPE %d, the menu closed", h.v[FRP_TYPE]);
    }
    h.v[FRP_TYPE] = 0;
    paint();

    /* the keys strip */
    {
        int k = fh_find(frp_gui_frui(ctl), FRW_KEYS, 0, &x, &y);
        const frui_widget *g = k >= 0 ? frui_widget_get(frp_gui_frui(ctl), k) : 0;
        CHECK(g != 0, "the keys strip is drawn");
        if (g) {
            float wx = g->x + g->w / 36.f * 0.5f, wy = g->y + g->h * 0.9f;     /* the first white key, low: C1 */
            ev(FRG_DOWN, FRG_LEFT, 0, wx, wy, 0, 0);
            CHECK(h.notes_on == 1 && h.last_note == 24, "a press on the first white key plays C1 (%d)", h.last_note);
            ev(FRG_UP, FRG_LEFT, 0, wx, wy, 0, 0);
            CHECK(h.notes_off == 1, "the release lets it go");
            wx = g->x + g->w / 36.f;                                           /* the C#1 black key, high */
            ev(FRG_DOWN, FRG_LEFT, 0, wx, g->y + g->h * 0.2f, 0, 0); ev(FRG_UP, FRG_LEFT, 0, wx, g->y + g->h * 0.2f, 0, 0);
            CHECK(h.last_note == 25, "between the first two white keys, high: C#1 (%d)", h.last_note);
        }
    }

    /* the keyboard: TAB to the first control, arrows, ESC */
    i = ev(FRG_KEY, 0, 0, 0, 0, 0, FRK_TAB);
    CHECK((i & 2) && frp_gui_model(ctl)->focus >= 0, "TAB takes the focus (%04x)", frp_gui_model(ctl)->focus);
    {
        int id = frp_gui_model(ctl)->focus & 0xff;
        int was = frui_value(frp_gui_model(ctl), id);
        ev(FRG_KEY, 0, 0, 0, 0, 0, was < frui_range(id) ? FRK_UP : FRK_DOWN);
        CHECK(frui_value(frp_gui_model(ctl), id) != was, "an arrow moves the focused value (%d: %d -> %d)", id, was,
              frui_value(frp_gui_model(ctl), id));
    }
    i = ev(FRG_KEY, 0, 0, 0, 0, 0, FRK_ESCAPE);
    CHECK((i & 2) && frp_gui_model(ctl)->focus < 0, "ESC drops the focus");
    CHECK(ev(FRG_KEY, 0, 0, 0, 0, 0, ' ') == 0, "a key the window does not use is left to the host");

    /* the gear opens the settings, ESC closes */
    paint();
    CHECK(fh_find(frp_gui_frui(ctl), FRW_BUTTON, FRB_GEAR, &x, &y) >= 0, "the gear is drawn");
    ev(FRG_DOWN, FRG_LEFT, 0, x, y, 0, 0); ev(FRG_UP, FRG_LEFT, 0, x, y, 0, 0);
    CHECK(frp_gui_model(ctl)->settings == 1, "the gear opens the settings");
    paint();
    ev(FRG_KEY, 0, 0, 0, 0, 0, FRK_ESCAPE);
    CHECK(frp_gui_model(ctl)->settings == 0, "ESC closes them");
    paint();

    /* typing a value: a double-click on the value text, digits, RETURN (one gesture); ESC leaves it */
    paint();
    {
        int k, n = frui_anchor_count(frp_gui_frui(ctl)), b0 = h.gesture_begins;
        float vx = -1.f, vy = -1.f;
        for (k = 0; k < n; k++) {
            const frui_anchor *a = frui_anchor_get(frp_gui_frui(ctl), k);
            if (a->what == FRA_VALUE && a->id == FRP_BARK) { vx = a->x; vy = a->y - 6.f; }
        }
        CHECK(vx > 0.f, "BARK's value text is drawn (%.0f, %.0f)", vx, vy);
        ev(FRG_DOWN, FRG_LEFT, 0, vx, vy, 0, 0); ev(FRG_UP, FRG_LEFT, 0, vx, vy, 0, 0);
        ev(FRG_DOUBLE, FRG_LEFT, 0, vx, vy, 0, 0); ev(FRG_UP, FRG_LEFT, 0, vx, vy, 0, 0);
        CHECK(frp_gui_model(ctl)->editing == FRP_BARK + 1, "a double-click on the value opens the field");
        b0 = h.gesture_begins;
        ev(FRG_KEY, 0, 0, 0, 0, 0, '1'); ev(FRG_KEY, 0, 0, 0, 0, 0, '0'); ev(FRG_KEY, 0, 0, 0, 0, 0, 'x');
        ev(FRG_KEY, 0, 0, 0, 0, 0, '0');
        CHECK(!strcmp(frp_gui_model(ctl)->edit_text, "100"), "digits go into it, other keys not (\"%s\")",
              frp_gui_model(ctl)->edit_text);
        CHECK(ev(FRG_KEY, 0, 0, 0, 0, 0, ' ') & 2, "while it is open the window keeps every key");
        paint();
        ev(FRG_KEY, 0, 0, 0, 0, 0, FRK_RETURN);
        CHECK(h.v[FRP_BARK] == 100 && h.gesture_begins == b0 + 1 && h.gestures_open == 0 && !frp_gui_model(ctl)->editing,
              "RETURN sets 100 in one gesture and closes the field (BARK %d)", h.v[FRP_BARK]);
        paint();
        ev(FRG_DOUBLE, FRG_LEFT, 0, vx, vy, 0, 0); ev(FRG_UP, FRG_LEFT, 0, vx, vy, 0, 0);
        ev(FRG_KEY, 0, 0, 0, 0, 0, '5');
        ev(FRG_KEY, 0, 0, 0, 0, 0, FRK_ESCAPE);
        CHECK(h.v[FRP_BARK] == 100 && !frp_gui_model(ctl)->editing, "ESC leaves it, the value unchanged");
        h.v[FRP_BARK] = 64;
    }
    paint();

    /* hover */
    fh_find(frp_gui_frui(ctl), FRW_KNOB, FRP_BARK, &x, &y);
    CHECK(ev(FRG_MOVE, 0, 0, x, y, 0, 0) & 1, "moving onto a knob asks for a repaint (hover)");
    CHECK(frp_gui_model(ctl)->hover == FRUI_WID(FRW_KNOB, FRP_BARK), "and marks it");
    CHECK(ev(FRG_MOVE, 0, 0, x + 1.f, y, 0, 0) == 0, "moving within it does not");
    CHECK(h.gestures_open == 0, "every gesture was closed");
    paint();
    CHECK(cl->tick(cl->ctx) == 0, "the tick sees nothing new");
    h.v[FRP_DECAY] = 7;
    CHECK(cl->tick(cl->ctx) == 1, "a value the host moved: the tick asks for a repaint");

    frp_gui_free(ctl);
    frui_font_free(f); frui_font_free(d);
    free(px);
    printf("%s: %d failed\n", fails ? "FAIL" : "PASS", fails);
    return fails != 0;
}
