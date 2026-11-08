/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on Linux (plugin/gui_platform.h): an X11 child window in the host's window. libX11 only.
 *   connection  one Display per view (XOpenDisplay): the view never shares the host's connection or its threads;
 *               the host watches the connection's socket (frg_view_poll_fd, CLAP posix-fd-support) and calls
 *               frg_view_poll when it is readable and on its timer (CLAP timer-support, ~33 ms)
 *   pixels      an XImage over the canvas: frui's 0xFFRRGGBB words are the usual 24-bit TrueColor visual's pixels
 *               (red 0xff0000, green 0xff00, blue 0xff in a 32-bit unit, in the machine's byte order), so XPutImage
 *               sends the canvas as it is; another visual gets a converted copy. No MIT-SHM (that needs libXext):
 *               a full 1920 x 1200 frame is ~9 MB over the local socket, sent only when something drawn changed.
 *   scale       the host's set_scale, else Xft.dpi / 96 from the screen's resources (the desktop's text scale), else
 *               1; sizes are physical pixels (CLAP's x11 API)
 *   input       buttons 1-3 (press / release; a second press within 400 ms and 4 px is the double-click: X has
 *               none), 4/5 the wheel, motion, leave, keys (XLookupString) once the view has the focus (a click
 *               takes it, as the other platforms) */
#include <X11/Xlib.h>
#include <X11/Xresource.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "gui_platform.h"
#include "ui/frui.h"

#define DOUBLE_MS 400u
#define DOUBLE_PX 4
#define TICK_MS 33u

struct frg_view {
    frg_client cl;
    Display *dpy;
    Window win, parent;
    GC gc;
    Visual *visual;
    int depth;
    int direct;                    /* the visual takes the canvas words as they are */
    int w, h;                      /* physical pixels */
    uint32_t *px;                  /* the canvas */
    uint32_t *conv;                /* (another visual: the converted copy) */
    int bw, bh;
    XImage *img;
    int dirty, exposed;
    float scale_override;
    unsigned buttons;              /* held, bit per FRG_ button */
    Time last_press; int last_button, last_x, last_y;
    uint32_t last_tick_ms;
    int visible;
};

static uint32_t now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000u + (uint32_t)(t.tv_nsec / 1000000));
}

static float xft_scale(Display *dpy)
{
    char *rms = dpy ? XResourceManagerString(dpy) : 0;
    float s = 1.f;
    if (rms) {
        XrmDatabase db;
        XrmValue val;
        char *type = 0;
        XrmInitialize();
        db = XrmGetStringDatabase(rms);
        if (db) {
            if (XrmGetResource(db, "Xft.dpi", "Xft.Dpi", &type, &val) && val.addr) {
                float dpi = (float)atof(val.addr);
                if (dpi >= 48.f && dpi <= 480.f) s = dpi / 96.f;
            }
            XrmDestroyDatabase(db);
        }
    }
    return s;
}

float frg_view_scale(const frg_view *v)
{
    float s;
    Display *d;
    if (v && v->scale_override > 0.f) return v->scale_override;
    if (v && v->dpy) return xft_scale(v->dpy);
    d = XOpenDisplay(0);           /* (no view yet: the default size) */
    s = xft_scale(d);
    if (d) XCloseDisplay(d);
    return s;
}
void frg_view_set_scale(frg_view *v, float scale) { v->scale_override = scale > 0.f ? scale : 0.f; }
void frg_view_default_size(const frg_view *v, int *w, int *h)
{
    float s = frg_view_scale(v);
    *w = (int)(FRUI_W * s + 0.5f);
    *h = (int)(FRUI_H * s + 0.5f);
}

static void buf_free(frg_view *v)
{
    if (v->img) { v->img->data = 0; XDestroyImage(v->img); }   /* (the pixels are ours: not XDestroyImage's) */
    free(v->px); free(v->conv);
    v->img = 0; v->px = v->conv = 0; v->bw = v->bh = 0;
}
static int buf_ensure(frg_view *v)
{
    if (v->px && v->bw == v->w && v->bh == v->h) return 0;
    buf_free(v);
    if (v->w <= 0 || v->h <= 0 || !v->dpy) return -1;
    v->px = malloc((size_t)v->w * (size_t)v->h * 4u);
    if (!v->px) return -1;
    if (!v->direct) v->conv = malloc((size_t)v->w * (size_t)v->h * 4u);
    v->img = XCreateImage(v->dpy, v->visual, (unsigned)v->depth, ZPixmap, 0, (char *)(v->direct ? v->px : v->conv),
                          (unsigned)v->w, (unsigned)v->h, 32, v->w * 4);
    if (!v->img || (!v->direct && !v->conv)) { buf_free(v); return -1; }
    v->bw = v->w; v->bh = v->h;
    v->dirty = 1;
    return 0;
}

static int lowbit(unsigned long m) { int i = 0; if (!m) return 0; while (!(m & 1ul)) { m >>= 1; i++; } return i; }
static int bits(unsigned long m) { int i = 0; while (m) { i += (int)(m & 1ul); m >>= 1; } return i; }
static void convert(frg_view *v)               /* any TrueColor visual from 0xFFRRGGBB */
{
    unsigned long rm = v->visual->red_mask, gm = v->visual->green_mask, bm = v->visual->blue_mask;
    int rs = lowbit(rm), gs = lowbit(gm), bs = lowbit(bm), rb = bits(rm), gb = bits(gm), bb = bits(bm), x, y;
    for (y = 0; y < v->bh; y++)
        for (x = 0; x < v->bw; x++) {
            uint32_t c = v->px[(size_t)y * (size_t)v->bw + (size_t)x];
            unsigned long p = ((((c >> 16) & 0xffu) >> (8 - rb)) << rs) | ((((c >> 8) & 0xffu) >> (8 - gb)) << gs) |
                              (((c & 0xffu) >> (8 - bb)) << bs);
            XPutPixel(v->img, x, y, p);
        }
}

static void present(frg_view *v)
{
    if (!v->win || buf_ensure(v)) return;
    if (v->dirty) {
        frui_canvas c;
        frui_canvas_init(&c, v->px, v->bw, v->bh);
        if (v->cl.paint) v->cl.paint(v->cl.ctx, &c, frg_view_scale(v));
        if (!v->direct) convert(v);
        v->dirty = 0;
    }
    XPutImage(v->dpy, v->win, v->gc, v->img, 0, 0, 0, 0, (unsigned)v->bw, (unsigned)v->bh);
    XFlush(v->dpy);
    v->exposed = 0;
}

static int mods_of(unsigned state)
{
    int m = 0;
    if (state & ShiftMask) m |= FRG_SHIFT;
    if (state & ControlMask) m |= FRG_CTRL;
    if (state & Mod1Mask) m |= FRG_ALT;
    return m;
}
static void send(frg_view *v, frg_input *e)
{
    if (v->cl.input && (v->cl.input(v->cl.ctx, e) & 1)) v->dirty = 1;
}
static void at(const frg_view *v, int px, int py, frg_input *e)
{
    float s = v->w > 0 ? (float)FRUI_W / (float)v->w : 1.f;
    e->x = ((float)px + 0.5f) * s;
    e->y = ((float)py + 0.5f) * s;
}
static int key_of(KeySym k)
{
    switch (k) {
    case XK_Left: case XK_KP_Left: return FRK_LEFT;
    case XK_Right: case XK_KP_Right: return FRK_RIGHT;
    case XK_Up: case XK_KP_Up: return FRK_UP;
    case XK_Down: case XK_KP_Down: return FRK_DOWN;
    case XK_Home: case XK_KP_Home: return FRK_HOME;
    case XK_End: case XK_KP_End: return FRK_END;
    case XK_Prior: case XK_KP_Prior: return FRK_PGUP;
    case XK_Next: case XK_KP_Next: return FRK_PGDN;
    case XK_Tab: case XK_ISO_Left_Tab: return FRK_TAB;
    case XK_Return: case XK_KP_Enter: return FRK_RETURN;
    case XK_Escape: return FRK_ESCAPE;
    case XK_Delete: case XK_KP_Delete: return FRK_DELETE;
    case XK_BackSpace: return FRK_BACKSPACE;
    default: return k >= 0x20 && k < 0x7f ? (int)k : 0;
    }
}

static void handle(frg_view *v, XEvent *ev)
{
    frg_input e;
    memset(&e, 0, sizeof e);
    switch (ev->type) {
    case Expose:
        if (ev->xexpose.count == 0) v->exposed = 1;
        break;
    case ConfigureNotify:
        if (ev->xconfigure.width != v->w || ev->xconfigure.height != v->h) {
            v->w = ev->xconfigure.width; v->h = ev->xconfigure.height;
            v->dirty = 1; v->exposed = 1;
        }
        break;
    case ButtonPress: {
        unsigned b = ev->xbutton.button;
        e.mods = mods_of(ev->xbutton.state);
        at(v, ev->xbutton.x, ev->xbutton.y, &e);
        if (b == 4 || b == 5) { e.kind = FRG_WHEEL; e.wheel = b == 4 ? 1.f : -1.f; send(v, &e); break; }
        if (b < 1 || b > 3) break;
        e.button = b == 1 ? FRG_LEFT : b == 3 ? FRG_RIGHT : FRG_MIDDLE;
        XSetInputFocus(v->dpy, v->win, RevertToParent, ev->xbutton.time);
        e.kind = FRG_DOWN;
        if (e.button == v->last_button && ev->xbutton.time - v->last_press < DOUBLE_MS &&
            abs(ev->xbutton.x - v->last_x) <= DOUBLE_PX && abs(ev->xbutton.y - v->last_y) <= DOUBLE_PX) {
            e.kind = FRG_DOUBLE;
            v->last_press = 0;     /* (a third press is a new first one) */
        } else {
            v->last_press = ev->xbutton.time;
        }
        v->last_button = e.button; v->last_x = ev->xbutton.x; v->last_y = ev->xbutton.y;
        v->buttons |= 1u << e.button;
        send(v, &e);
        break;
    }
    case ButtonRelease: {
        unsigned b = ev->xbutton.button;
        if (b < 1 || b > 3) break;
        e.kind = FRG_UP;
        e.button = b == 1 ? FRG_LEFT : b == 3 ? FRG_RIGHT : FRG_MIDDLE;
        e.mods = mods_of(ev->xbutton.state);
        at(v, ev->xbutton.x, ev->xbutton.y, &e);
        v->buttons &= ~(1u << e.button);
        send(v, &e);
        break;
    }
    case MotionNotify: {
        XEvent next;              /* the latest of a burst of moves only */
        while (XCheckTypedWindowEvent(v->dpy, v->win, MotionNotify, &next)) *ev = next;
        e.kind = FRG_MOVE;
        e.mods = mods_of(ev->xmotion.state);
        at(v, ev->xmotion.x, ev->xmotion.y, &e);
        send(v, &e);
        break;
    }
    case LeaveNotify:
        if (v->buttons) break;    /* (dragging out of the window: the implicit grab keeps the moves coming) */
        e.kind = FRG_LEAVE;
        send(v, &e);
        break;
    case FocusOut:
        e.kind = FRG_FOCUS_LOST;
        v->buttons = 0;
        send(v, &e);
        break;
    case KeyPress: {
        KeySym ks = 0;
        char buf[8];
        XLookupString(&ev->xkey, buf, sizeof buf, &ks, 0);
        e.kind = FRG_KEY;
        e.mods = mods_of(ev->xkey.state);
        e.key = key_of(ks);
        if (e.key) send(v, &e);
        break;
    }
    default: break;
    }
}

int frg_view_poll_fd(const frg_view *v) { return v && v->dpy ? ConnectionNumber(v->dpy) : -1; }
void frg_view_poll(frg_view *v)
{
    uint32_t t;
    if (!v || !v->dpy) return;
    while (XPending(v->dpy)) {
        XEvent ev;
        XNextEvent(v->dpy, &ev);
        if (ev.xany.window == v->win) handle(v, &ev);
    }
    t = now_ms();
    if (t - v->last_tick_ms >= TICK_MS - 3u) {
        v->last_tick_ms = t;
        if (v->cl.tick && v->cl.tick(v->cl.ctx)) v->dirty = 1;
    }
    if ((v->dirty || v->exposed) && v->visible) present(v);
}

frg_view *frg_view_create(const frg_client *cl, int w, int h)
{
    frg_view *v = calloc(1, sizeof *v);
    if (!v) return 0;
    v->cl = *cl;
    v->w = w; v->h = h;
    v->dirty = 1;
    v->last_button = -1;
    return v;
}
void frg_view_destroy(frg_view *v)
{
    if (!v) return;
    buf_free(v);
    if (v->dpy) {
        if (v->gc) XFreeGC(v->dpy, v->gc);
        if (v->win) XDestroyWindow(v->dpy, v->win);
        XCloseDisplay(v->dpy);
    }
    free(v);
}
int frg_view_set_parent(frg_view *v, void *native_parent)
{
    Window parent = (Window)(uintptr_t)native_parent;
    XSetWindowAttributes a;
    XWindowAttributes pa;
    if (!parent) return -1;
    if (!v->dpy && !(v->dpy = XOpenDisplay(0))) return -1;
    if (v->win) { XReparentWindow(v->dpy, v->win, parent, 0, 0); v->parent = parent; XFlush(v->dpy); return 0; }
    if (!XGetWindowAttributes(v->dpy, parent, &pa)) return -1;
    /* the parent's visual and depth (a host window with an ARGB visual: the canvas is opaque, alpha 0xFF) */
    v->visual = pa.visual;
    v->depth = pa.depth;
    v->direct = v->visual->class == TrueColor && (v->depth == 24 || v->depth == 32) && v->visual->red_mask == 0xff0000ul &&
                v->visual->green_mask == 0xff00ul && v->visual->blue_mask == 0xfful &&
                ImageByteOrder(v->dpy) == (*(const unsigned char *)&(uint32_t){1} ? LSBFirst : MSBFirst);
    memset(&a, 0, sizeof a);
    a.event_mask = ExposureMask | StructureNotifyMask | ButtonPressMask | ButtonReleaseMask | PointerMotionMask |
                   LeaveWindowMask | KeyPressMask | FocusChangeMask;
    a.border_pixel = 0;
    a.background_pixmap = None;
    a.colormap = pa.colormap;
    v->win = XCreateWindow(v->dpy, parent, 0, 0, (unsigned)(v->w > 0 ? v->w : 1), (unsigned)(v->h > 0 ? v->h : 1), 0,
                           v->depth, InputOutput, v->visual, CWEventMask | CWBorderPixel | CWBackPixmap | CWColormap, &a);
    if (!v->win) return -1;
    v->parent = parent;
    v->gc = XCreateGC(v->dpy, v->win, 0, 0);
    XMapWindow(v->dpy, v->win);
    v->visible = 1;
    XFlush(v->dpy);
    return 0;
}
void frg_view_show(frg_view *v, int visible)
{
    v->visible = visible;
    if (!v->win) return;
    if (visible) XMapWindow(v->dpy, v->win); else XUnmapWindow(v->dpy, v->win);
    XFlush(v->dpy);
}
void frg_view_resize(frg_view *v, int w, int h)
{
    v->w = w; v->h = h;
    v->dirty = 1;
    if (v->win) { XResizeWindow(v->dpy, v->win, (unsigned)w, (unsigned)h); XFlush(v->dpy); }
}
void frg_view_repaint(frg_view *v)
{
    v->dirty = 1;
    if (v->win && v->visible) present(v);
}

const char *const FRG_VIEW_API = "x11";
const uint32_t *frg_view_grab(frg_view *v, int *w, int *h)
{
    if (!v->px) return 0;
    *w = v->bw; *h = v->bh;
    return v->px;
}
