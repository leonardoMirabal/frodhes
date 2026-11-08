/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on an OS without a view yet (Linux: an X11 view is a later milestone): no CLAP gui extension, so
 * the host shows its generic parameter view. */
#include <stddef.h>
#include "gui_view.h"

const char *const FRP_VIEW_API = NULL;
frp_view_t *frp_view_new(frp_gui_t *core, uint32_t w, uint32_t h) { (void)core; (void)w; (void)h; return NULL; }
void frp_view_free(frp_view_t *v) { (void)v; }
int frp_view_attach(frp_view_t *v, void *parent) { (void)v; (void)parent; return -1; }
void frp_view_size(frp_view_t *v, uint32_t w, uint32_t h) { (void)v; (void)w; (void)h; }
void frp_view_show(frp_view_t *v, int show) { (void)v; (void)show; }
void frp_view_scale(frp_view_t *v, double scale) { (void)v; (void)scale; }
const uint32_t *frp_view_grab(frp_view_t *v, int *w, int *h) { (void)v; (void)w; (void)h; return NULL; }
