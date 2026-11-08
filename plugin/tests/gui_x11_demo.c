/* SPDX-License-Identifier: GPL-3.0-only */
/* The X11 view (plugin/gui_x11.c) outside a host: a top-level window holding the view over the stand-in plugin
 * (gui_fake_host.h), the event loop a host would run (poll on the view's socket, a 33 ms timer).
 *   frp_gui_demo              the window, to try by hand
 *   frp_gui_demo --selftest   (Xvfb is enough) drives the view with X events sent to its window (press / motion /
 *                             release on a knob, the wheel, a double-click, TAB + an arrow), reads its pixels back
 *                             (XGetImage), prints PASS / FAIL lines, exits 0 / 1 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <poll.h>
#include <stdlib.h>
#include "gui_fake_host.h"

static fake_host h;
static frp_gui_t *ctl;
static frg_view *view;
static int fails;
static Display *dpy;

static void say(int ok, const char *what, int a, int b)
{
    printf("%s %s (%d, %d)\n", ok ? "PASS" : "FAIL", what, a, b);
    if (!ok) fails++;
}
static void spin(int ms)               /* the host's loop for a while */
{
    int t;
    for (t = 0; t < ms; t += 10) {
        struct pollfd p = { frg_view_poll_fd(view), POLLIN, 0 };
        poll(&p, 1, 10);
        frg_view_poll(view);
    }
}
static Window child;
static int W, H;
static void xbutton(int type, unsigned button, unsigned state, float lx, float ly, Time t)
{
    XEvent e;
    float s = (float)W / (float)FRUI_W;
    memset(&e, 0, sizeof e);
    e.xbutton.type = type; e.xbutton.display = dpy; e.xbutton.window = child; e.xbutton.button = button;
    e.xbutton.state = state; e.xbutton.x = (int)(lx * s); e.xbutton.y = (int)(ly * s); e.xbutton.time = t;
    e.xbutton.same_screen = True;
    XSendEvent(dpy, child, False, type == ButtonPress ? ButtonPressMask : ButtonReleaseMask, &e);
}
static void xmotion(unsigned state, float lx, float ly)
{
    XEvent e;
    float s = (float)W / (float)FRUI_W;
    memset(&e, 0, sizeof e);
    e.xmotion.type = MotionNotify; e.xmotion.display = dpy; e.xmotion.window = child; e.xmotion.state = state;
    e.xmotion.x = (int)(lx * s); e.xmotion.y = (int)(ly * s); e.xmotion.same_screen = True;
    XSendEvent(dpy, child, False, PointerMotionMask, &e);
}
static void xkey(KeySym ks)
{
    XEvent e;
    memset(&e, 0, sizeof e);
    e.xkey.type = KeyPress; e.xkey.display = dpy; e.xkey.window = child; e.xkey.keycode = XKeysymToKeycode(dpy, ks);
    e.xkey.same_screen = True;
    XSendEvent(dpy, child, False, KeyPressMask, &e);
}

static int selftest(Window top)
{
    Window root, parent, *kids = 0;
    unsigned nk = 0;
    float x, y;
    int v, i, lit = 0;
    for (i = 0; i < 100 && !child; i++) {   /* (the view's own connection made it: wait up to 1 s for the server) */
        XSync(dpy, False);
        XQueryTree(dpy, top, &root, &parent, &kids, &nk);
        child = nk ? kids[0] : 0;
        if (kids) XFree(kids);
        kids = 0;
        if (!child) spin(10);
    }
    say(child != 0, "the view is a child window", W, H);
    if (!child) return 1;
    spin(300);
    say(fh_find(frp_gui_frui(ctl), FRW_KNOB, FRP_BARK, &x, &y) >= 0, "the first paint laid out the BARK knob", 0, 0);
    v = h.v[FRP_BARK];
    xbutton(ButtonPress, 1, 0, x, y, 1000);
    xmotion(Button1Mask, x, y - 50.f); XSync(dpy, False); spin(30);
    xmotion(Button1Mask, x, y - 100.f);
    xbutton(ButtonRelease, 1, Button1Mask, x, y - 100.f, 1100);
    XSync(dpy, False); spin(100);
    say(h.v[FRP_BARK] >= v + 62 && h.gestures_open == 0, "drag up 100 logical px: half the range, gesture closed", v, h.v[FRP_BARK]);
    v = h.v[FRP_BARK];
    xbutton(ButtonPress, 5, 0, x, y, 2000); xbutton(ButtonRelease, 5, 0, x, y, 2001);
    XSync(dpy, False); spin(60);
    say(h.v[FRP_BARK] == v - 2, "a wheel notch down: -2", v, h.v[FRP_BARK]);
    xbutton(ButtonPress, 1, 0, x, y, 3000); xbutton(ButtonRelease, 1, Button1Mask, x, y, 3050);
    xbutton(ButtonPress, 1, 0, x, y, 3200); xbutton(ButtonRelease, 1, Button1Mask, x, y, 3250);
    XSync(dpy, False); spin(60);
    say(h.v[FRP_BARK] == 64 && h.gestures_open == 0, "double-click (two presses in 200 ms): the default", h.v[FRP_BARK], h.gestures_open);
    xkey(XK_Tab); XSync(dpy, False); spin(40);
    i = frp_gui_model(ctl)->focus & 0xff;
    v = frui_value(frp_gui_model(ctl), i);
    xkey(v < frui_range(i) ? XK_Up : XK_Down); XSync(dpy, False); spin(40);
    say(frp_gui_model(ctl)->focus >= 0 && frui_value(frp_gui_model(ctl), i) != v, "TAB + an arrow moves the focused control",
        v, frui_value(frp_gui_model(ctl), i));
    spin(100);
    {
        XImage *im = XGetImage(dpy, child, 0, 0, (unsigned)W, (unsigned)H, AllPlanes, ZPixmap);
        if (im) {
            for (i = 0; i < W * H; i += 97) lit += (XGetPixel(im, i % W, i / W) & 0xffffff) != 0;
            XDestroyImage(im);
        }
        say(lit > 500, "the view's pixels read back (sampled pixels not black)", lit, W * H / 97);
    }
    say(frg_view_scale(view) >= 1.f, "the scale x100", (int)(frg_view_scale(view) * 100.f), 0);
    return fails != 0;
}

int main(int argc, char **argv)
{
    frp_ui_host_t ch;
    frui_font *f = frui_font_load(frp_font_text, frp_font_text_len), *d = frui_font_load(frp_font_display, frp_font_display_len);
    int test = argc > 1 && !strcmp(argv[1], "--selftest"), rc = 0;
    Window top;
    say(f && d, "the embedded fonts load", (int)frp_font_text_len, (int)frp_font_display_len);
    if (!f || !d || !(dpy = XOpenDisplay(0))) { say(0, "an X display", 0, 0); return 1; }
    fh_init(&h, &ch);
    ctl = frp_gui_new(&ch);
    view = frg_view_create(frp_gui_client(ctl), 1, 1);
    frg_view_default_size(view, &W, &H);
    top = XCreateSimpleWindow(dpy, DefaultRootWindow(dpy), 0, 0, (unsigned)W, (unsigned)H, 0, 0, 0);
    XStoreName(dpy, top, "Frodhes (view demo)");
    XSelectInput(dpy, top, StructureNotifyMask);
    XMapWindow(dpy, top);
    XSync(dpy, False);
    frg_view_resize(view, W, H);
    say(frg_view_set_parent(view, (void *)(uintptr_t)top) == 0, "the view goes into the window", W, H);
    if (test) rc = selftest(top);
    else for (;;) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ConfigureNotify && (e.xconfigure.width != W || e.xconfigure.height != H)) {
                W = e.xconfigure.width; H = e.xconfigure.height;
                frg_view_resize(view, W, H);
            }
        }
        spin(30);
    }
    frg_view_destroy(view);
    XDestroyWindow(dpy, top);
    XCloseDisplay(dpy);
    frp_gui_free(ctl);
    frui_font_free(f); frui_font_free(d);
    say(!fails, "selftest", fails, 0);
    return rc;
}
