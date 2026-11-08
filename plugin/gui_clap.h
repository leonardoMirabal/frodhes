/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window as CLAP's gui extension (gui_clap.c). The plugin (plugin/src/frodhes_clap.c) owns one
 * frp_gui_clap_t per instance, fills in the frp_ui_host_t the window uses, and answers frp_plugin_gui. */
#ifndef FRP_GUI_CLAP_H
#define FRP_GUI_CLAP_H
#include <clap/clap.h>
#include "frp_ui_iface.h"

typedef struct frp_gui_clap frp_gui_clap_t;
frp_gui_clap_t *frp_gui_clap_new(const clap_host_t *host, const frp_ui_host_t *uh);   /* main thread, in init */
void frp_gui_clap_free(frp_gui_clap_t *c);                                          /* main thread, in destroy */
const clap_plugin_gui_t *frp_gui_clap_ext(void);   /* CLAP_EXT_GUI (NULL on an OS without a view) */
const void *frp_gui_clap_shot_ext(void);           /* FRP_EXT_GUI_SHOT (gui_shot.h) */
/* Linux (X11): the host's event loop drives the view: CLAP_EXT_POSIX_FD_SUPPORT on the display's socket and
 * CLAP_EXT_TIMER_SUPPORT (~33 ms); NULL on the other platforms (their views run their own) */
const clap_plugin_posix_fd_support_t *frp_gui_clap_fd_ext(void);
const clap_plugin_timer_support_t *frp_gui_clap_timer_ext(void);
/* the plugin's: the instance's window state */
frp_gui_clap_t *frp_plugin_gui(const clap_plugin_t *plugin);
#endif
