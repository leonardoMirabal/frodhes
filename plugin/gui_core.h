/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's core (B-M2b): everything but the operating system. It keeps the frui_model the window draws
 * (the app's values through frp_ui_iface.h, the meters, the held notes, the window's own state: tabs, menus, focus),
 * turns mouse, wheel and keys into parameter changes, and repaints into an RGBA canvas only what changed (frui draws
 * the whole window through a base clip, so a partial repaint gives exactly the pixels a full one does).
 *
 * The platform views (gui_mac.m, gui_win.c) only blit frp_gui_pixels and pass input in logical coordinates (the
 * 960 x 600 layout, whatever the window's size and backing scale). Main thread only. */
#ifndef FRP_GUI_CORE_H
#define FRP_GUI_CORE_H
#include <stdint.h>
#include "frp_ui_iface.h"
#include "ui/frui.h"

/* the window's sizes, logical points (frui's layout at 75 % .. 200 %, fixed aspect 16:10) */
#define FRP_GUI_W FRUI_W
#define FRP_GUI_H FRUI_H
#define FRP_GUI_MIN_W 720
#define FRP_GUI_MAX_W 1920

typedef struct frp_gui frp_gui_t;
enum { FRP_MOUSE_DOWN, FRP_MOUSE_DRAG, FRP_MOUSE_UP, FRP_MOUSE_MOVE, FRP_MOUSE_LEAVE, FRP_MOUSE_RDOWN };
enum { FRP_MOD_SHIFT = 1, FRP_MOD_ALT = 2, FRP_MOD_CMD = 4, FRP_MOD_CTRL = 8 };
enum { FRP_KEY_TAB = 1, FRP_KEY_ESC, FRP_KEY_ENTER, FRP_KEY_LEFT, FRP_KEY_RIGHT, FRP_KEY_UP, FRP_KEY_DOWN, FRP_KEY_HOME,
       FRP_KEY_END };

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
int frp_gui_find(const frp_gui_t *g, int kind, int id, float r[4]);   /* a widget of the last draw: 1 found */
/* input, logical coordinates; clicks: 2 for a double click */
void frp_gui_mouse(frp_gui_t *g, int ev, float x, float y, int mods, int clicks);
void frp_gui_wheel(frp_gui_t *g, float x, float y, float notches, int mods);   /* + = up / away */
int frp_gui_key(frp_gui_t *g, int key, int mods);  /* 1 handled (else the host should get the key) */
/* logical points per canvas pixel (a view's point times its backing scale times this = the logical point) */
float frp_gui_px_to_logical(const frp_gui_t *g);
/* a window size the layout accepts (the nearest 16:10 size within the minimum and maximum), logical points */
void frp_gui_fit_size(uint32_t *w, uint32_t *h);
#endif
