/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's core (B-M2b): everything but the operating system. It keeps the frui_model the window draws
 * (the app's values through frp_ui_iface.h, the meters, the held notes, the window's own state: tabs, menus, focus),
 * turns mouse, wheel and keys into parameter changes, and repaints into an RGBA canvas only what changed (frui draws
 * the whole window through a base clip, so a partial repaint gives exactly the pixels a full one does).
 *
 * The platform views (gui_platform.h: gui_mac.m, gui_win.c, gui_x11.c) drive it through frp_gui_client: they own the
 * native window and its pixel buffer, ask the core to bring the buffer up to date and pass input in logical
 * coordinates (the 960 x 600 layout, whatever the window's size and backing scale). Main thread only. */
#ifndef FRP_GUI_CORE_H
#define FRP_GUI_CORE_H
#include <stdint.h>
#include "frp_ui_iface.h"
#include "gui_platform.h"
#include "ui/frui.h"

/* the window's sizes, logical points (frui's layout at 75 % .. 200 %, fixed aspect 16:10) */
#define FRP_GUI_W FRUI_W
#define FRP_GUI_H FRUI_H
#define FRP_GUI_MIN_W 720
#define FRP_GUI_MAX_W 1920

typedef struct frp_gui frp_gui_t;

frp_gui_t *frp_gui_new(const frp_ui_host_t *h);    /* NULL: out of memory or the fonts did not load */
void frp_gui_free(frp_gui_t *g);
/* the canvas for a view of pw x ph pixels (the window's size times the backing scale): the largest 16:10 size that
 * fits (the view shows it from the top left; a host that keeps another aspect gets a dark margin); 0 ok */
int frp_gui_resize(frp_gui_t *g, int pw, int ph);
/* reads the app's values and the meters (now: seconds, monotonic); 1 when a repaint is due */
int frp_gui_tick(frp_gui_t *g, double now);
/* repaints what changed since the last paint; 1 when pixels changed, *dirty = their rectangle x0 y0 x1 y1 (pixels) */
int frp_gui_paint(frp_gui_t *g, int dirty[4]);
const uint32_t *frp_gui_pixels(const frp_gui_t *g, int *w, int *h);   /* the canvas, 0xFFRRGGBB, w x h */
const frui_model *frp_gui_model(const frp_gui_t *g);
const frui *frp_gui_frui(const frp_gui_t *g);       /* (the widgets of the last draw: tests) */
int frp_gui_find(const frp_gui_t *g, int kind, int id, float r[4]);   /* a widget of the last draw: 1 found */
/* one input event (gui_platform.h, logical coordinates): bit 0 set when what is drawn may have changed, bit 1 when the
 * window used the event (a key it does not use goes to the host) */
int frp_gui_input(frp_gui_t *g, const frg_input *e);
/* the frg_client for a platform view (gui_platform.h): paint = tick + repaint what changed + copy into the view's
 * buffer (a buffer of another aspect than 16:10 gets the window at its top left and a dark margin) */
const frg_client *frp_gui_client(frp_gui_t *g);
/* logical points per canvas pixel (a view's point times its backing scale times this = the logical point) */
float frp_gui_px_to_logical(const frp_gui_t *g);
/* a window size the layout accepts (the nearest 16:10 size within the minimum and maximum), logical points */
void frp_gui_fit_size(uint32_t *w, uint32_t *h);
#endif
