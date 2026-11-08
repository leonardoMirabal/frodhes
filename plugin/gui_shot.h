/* SPDX-License-Identifier: GPL-3.0-only */
/* A private CLAP extension for the window's tests (plugin/tests/gui_shot.m): what the open window shows, input into
 * it, and a window without a native view for headless runs. Hosts never ask for it. */
#ifndef FRP_GUI_SHOT_H
#define FRP_GUI_SHOT_H
#include <stdbool.h>
#include <stdint.h>
#include <clap/clap.h>

#define FRP_EXT_GUI_SHOT "io.github.leonardomirabal.frodhes.gui-shot/1"
typedef struct {
    /* paints what changed now (as the view's timer does) */
    bool (*tick)(const clap_plugin_t *plugin);
    /* the canvas as last painted and the frui_model it shows (model: frui.h's struct, model_size its sizeof); view: 1 =
     * read back from the native view's surface instead (what the window shows), when there is one */
    bool (*grab)(const clap_plugin_t *plugin, int view, const uint32_t **px, int *w, int *h, const void **model,
                 uint32_t *model_size);
    /* input, as the native view passes it (gui_core.h: FRP_MOUSE_*, logical coordinates) */
    bool (*mouse)(const clap_plugin_t *plugin, int ev, float x, float y, int mods, int clicks);
    bool (*wheel)(const clap_plugin_t *plugin, float x, float y, float notches, int mods);
    bool (*key)(const clap_plugin_t *plugin, int key, int mods);
    /* a window core without a native view (no gui create needed), its canvas pw x ph pixels */
    bool (*headless)(const clap_plugin_t *plugin, int pw, int ph);
    /* a widget of the last draw (frui.h: kind FRW_*, id), its rectangle in logical points */
    bool (*find)(const clap_plugin_t *plugin, int kind, int id, float r[4]);
} frp_gui_shot_t;
#endif
