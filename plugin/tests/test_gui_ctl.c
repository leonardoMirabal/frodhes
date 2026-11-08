/* SPDX-License-Identifier: GPL-3.0-only */
/* The window's core (plugin/gui_core.c) through its platform client, headless: frui draws into a memory canvas as a platform view would,
 * and synthetic input checks the mapping every platform shares (knob drag / SHIFT fine / wheel / double-click
 * default / the right-click menu, segments, menus and their options, the keys strip, TAB + arrows, the gear, ESC,
 * hover) and that every write is inside a gesture. Prints PASS / FAIL lines; exit 0 when all pass. */
#include <stdlib.h>
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

int main(void)
{
    fake_host h;
    frp_ui_host_t ch;
    frui_font *f = frui_font_load(frp_font_text, frp_font_text_len), *d = frui_font_load(frp_font_display, frp_font_display_len);
    float x, y;
    int v, i, nonblack = 0;
    CHECK(f && d, "the embedded fonts load (%lu + %lu bytes)", frp_font_text_len, frp_font_display_len);
    if (!f || !d) return 1;
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
