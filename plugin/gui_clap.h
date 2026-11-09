/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window as CLAP's gui extension (gui_clap.c). The plugin (plugin/src/frodhes_clap.c) makes one
 * frp_gui_clap_t per instance and keeps it in frp_ui_gui_data; the window reaches the plugin core only through
 * frp_ui_iface.h's calls. */
#ifndef FRP_GUI_CLAP_H
#define FRP_GUI_CLAP_H
#include <clap/clap.h>
#include "frp_ui_iface.h"
#include "gui_host.h"

typedef struct frp_gui_clap frp_gui_clap_t;
frp_gui_clap_t *frp_gui_clap_new(const clap_host_t *host, frp_core *pc);   /* main thread, in init */
void frp_gui_clap_free(frp_gui_clap_t *c);                                          /* main thread, in destroy */
const clap_plugin_gui_t *frp_gui_clap_ext(void);   /* CLAP_EXT_GUI (NULL on an OS without a view) */
const void *frp_gui_clap_shot_ext(void);           /* FRP_EXT_GUI_SHOT (gui_shot.h) */
/* the instance's saved window size changed (a state loaded): the next window opens at it; an open one asks the host to
 * resize (CLAP gui request_resize) */
void frp_gui_clap_size_changed(frp_gui_clap_t *c);
/* Linux (X11): the host's event loop drives the view: CLAP_EXT_POSIX_FD_SUPPORT on the display's socket and
 * CLAP_EXT_TIMER_SUPPORT (~33 ms); NULL on the other platforms (their views run their own) */
const clap_plugin_posix_fd_support_t *frp_gui_clap_fd_ext(void);
const clap_plugin_timer_support_t *frp_gui_clap_timer_ext(void);
#endif
