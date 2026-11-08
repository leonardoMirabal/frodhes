/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's platform layer (B-M2b): one small interface, one file per platform behind it
 *   plugin/gui_win.c     Windows: a child HWND, a DIB section that is the frui canvas itself (0xFFRRGGBB is GDI's
 *                        32-bit BGRA byte order), per-monitor DPI (1x / 1.5x / 2x ...), a UI-thread timer
 *   plugin/gui_mac.m     macOS: an NSView (agent P2)
 *   plugin/gui_x11.c     Linux: an X11 child window (libX11 only), XPutImage of the canvas; the host's event loop
 *                        drives it (CLAP posix-fd-support on the display's socket + timer-support: frg_view_poll)
 * The platform layer knows nothing of parameters or widgets: it owns the native view and the pixel buffer, asks its
 * client to draw the whole window into the buffer at the backing scale, and hands it the input in logical pixels
 * (FRUI_W x FRUI_H space, whatever the window's size). The client (plugin/gui_ctl.c) maps the input onto frui's
 * widgets and the parameters. Every call: the host's main (UI) thread only, as CLAP's gui extension. */
#ifndef FRODHES_GUI_PLATFORM_H
#define FRODHES_GUI_PLATFORM_H
#include <stdint.h>
#include "ui/frui_gfx.h"

/* input, in logical pixels of the 960 x 600 layout */
enum { FRG_DOWN, FRG_UP, FRG_MOVE, FRG_DOUBLE, FRG_WHEEL, FRG_KEY, FRG_LEAVE, FRG_FOCUS_LOST };
enum { FRG_LEFT = 0, FRG_RIGHT = 1, FRG_MIDDLE = 2 };
enum { FRG_SHIFT = 1, FRG_CTRL = 2, FRG_ALT = 4, FRG_CMD = 8 };     /* CMD: macOS only (Windows: CTRL) */
/* keys: printable ASCII as itself; the rest */
enum { FRK_LEFT = 0x100, FRK_RIGHT, FRK_UP, FRK_DOWN, FRK_HOME, FRK_END, FRK_PGUP, FRK_PGDN, FRK_TAB, FRK_RETURN,
       FRK_ESCAPE, FRK_DELETE, FRK_BACKSPACE };
typedef struct {
    int kind;                      /* FRG_* */
    int button;                    /* DOWN / UP / DOUBLE: FRG_LEFT .. */
    int mods;                      /* FRG_SHIFT | ... */
    float x, y;                    /* logical pixels */
    float wheel;                   /* WHEEL: notches, up / away from the user positive (fractions on touchpads) */
    int key;                       /* KEY: FRK_* or ASCII */
} frg_input;

typedef struct {
    void *ctx;
    /* draw the whole window into c (c->w x c->h = the logical size x scale); called from the platform's paint */
    void (*paint)(void *ctx, frui_canvas *c, float scale);
    /* one input event; returns 1 if it changed what is drawn (the platform repaints) */
    int (*input)(void *ctx, const frg_input *ev);
    /* the repaint timer (~30 Hz, UI thread): returns 1 if anything drawn has changed (meters, held notes, values the
     * host or the app moved) */
    int (*tick)(void *ctx);
} frg_client;

/* Sizes are in the host's units for the platform, as CLAP's gui extension: physical pixels on Windows (the win32
 * API), points on macOS (cocoa). The canvas is always physical pixels; frui draws the layout at canvas width /
 * FRUI_W, so any size is sharp. The default size is FRUI_W x FRUI_H times frg_view_scale() on Windows, FRUI_W x
 * FRUI_H on macOS: frg_view_default_size(). */
typedef struct frg_view frg_view;
void frg_view_default_size(const frg_view *v, int *w, int *h);
/* a view of w x h (host units), not yet in a window */
frg_view *frg_view_create(const frg_client *cl, int w, int h);
void frg_view_destroy(frg_view *v);
/* into the host's window: an HWND (Windows), an NSView * (macOS); 0 ok, -1 failed */
int frg_view_set_parent(frg_view *v, void *native_parent);
void frg_view_show(frg_view *v, int visible);
/* the window's size (host units: the host's resize); the buffer follows in physical pixels */
void frg_view_resize(frg_view *v, int w, int h);
/* ask for a repaint (asynchronous: the platform's next paint) */
void frg_view_repaint(frg_view *v);
/* the backing scale now (1.0, 1.5, 2.0 ... : the monitor's DPI / 96 on Windows, the backing factor on macOS) */
float frg_view_scale(const frg_view *v);
/* the host's own scale request (CLAP gui set_scale; Windows hosts that are not per-monitor aware); <= 0: the
 * platform's own value again */
void frg_view_set_scale(frg_view *v, float scale);
/* the host's event loop (Linux): the file descriptor to watch for reading (CLAP posix-fd-support), -1 when the
 * platform runs its own (Windows: the window's messages and SetTimer; macOS: the run loop); frg_view_poll() when it
 * is readable and on the host's timer (CLAP timer-support, ~33 ms): the pending events, then the client's tick.
 * Windows / macOS: a no-op. */
int frg_view_poll_fd(const frg_view *v);
void frg_view_poll(frg_view *v);
#endif
