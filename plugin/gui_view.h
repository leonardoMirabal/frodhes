/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's native view (B-M2b): one per operating system (gui_mac.m: an NSView with a layer; gui_win.c: a
 * child HWND with a DIB). It blits gui_core's canvas, passes input in logical coordinates and runs the repaint timer
 * (~60 Hz, the main thread; a frame paints only what changed). Main thread only. */
#ifndef FRP_GUI_VIEW_H
#define FRP_GUI_VIEW_H
#include <stdint.h>
#include "gui_core.h"

typedef struct frp_view frp_view_t;
extern const char *const FRP_VIEW_API;              /* CLAP_WINDOW_API_COCOA / _WIN32, NULL: no view on this OS */
frp_view_t *frp_view_new(frp_gui_t *core, uint32_t w, uint32_t h);   /* w x h: logical points */
void frp_view_free(frp_view_t *v);
int frp_view_attach(frp_view_t *v, void *parent);   /* the host's NSView * / HWND: 0 ok */
void frp_view_size(frp_view_t *v, uint32_t w, uint32_t h);
void frp_view_show(frp_view_t *v, int show);
void frp_view_scale(frp_view_t *v, double scale);   /* the host's scale (Windows; macOS reads the backing scale) */
/* what the view shows now, read back from the native surface (the test's real-window picture): w x h, 0xFFRRGGBB,
 * valid until the next call; NULL when it cannot be read */
const uint32_t *frp_view_grab(frp_view_t *v, int *w, int *h);
#endif
