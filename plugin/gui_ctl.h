/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's controller (B-M2b): the frg_client (plugin/gui_platform.h) that every platform view drives.
 * Owns the frui and its model; maps the input onto frui's widgets the same way on every platform:
 *   knob / fader   drag (vertical for knobs, along the fader for faders), SHIFT fine (x 1/10), the wheel (SHIFT: one
 *                  step), double-click the default, right-click the knob menu (MIDI LEARN, FORGET, DEFAULT)
 *   segments       a click picks the segment under the mouse
 *   menu           a click opens it, a click on an option picks it, anywhere else (or ESC) closes; the wheel steps
 *   tabs, pager    a click selects; buttons: a click (the gear and the sheet's CLOSE here, the rest to the host)
 *   keys strip     press / release plays the note under the mouse (the host decides what a UI note does)
 *   keyboard       TAB / SHIFT-TAB the focus, arrows / PAGE / HOME / END the focused value, RETURN opens a menu,
 *                  ESC closes; unused keys are left to the host (the platform passes them on)
 * Every value goes through the host's set() between gesture begin / end, so host automation and the UI never
 * disagree; the model's values are re-read from the host on every tick. */
#ifndef FRODHES_GUI_CTL_H
#define FRODHES_GUI_CTL_H
#include "gui_platform.h"
#include "ui/frui.h"

/* what the controller needs from the plugin (main thread). Ids are frui's: the parameters (FRP_*), FRP_TUNE,
 * FRP_TW_KIND and the settings' FRS_* (the host maps the pseudo ids onto what they stand for) */
typedef struct {
    void *ctx;
    /* fill the model: values, meters, notes, preset, settings (everything but the UI's own state: tabs, focus,
     * hover, menus); called before every draw and on every tick */
    void (*refresh)(void *ctx, frui_model *m);
    void (*set)(void *ctx, int id, int value);
    void (*gesture)(void *ctx, int id, int begin);       /* begin 1 / end 0 around a drag, a click's write */
    int (*def)(void *ctx, int id);                       /* the default value (a double-click, DEFAULT) */
    void (*button)(void *ctx, int id);                   /* FRB_* the controller does not handle itself */
    void (*note)(void *ctx, int key, int velocity);      /* the keys strip (velocity 0: release) */
    void (*learn)(void *ctx, int id, int what);          /* the knob menu: FRC_LEARN (id -1: cancel), FRC_FORGET */
    void (*preset_menu)(void *ctx, float x, float y);    /* the preset name clicked (logical point below it) */
    frui_text_fn text;                                   /* the value texts (frui_new's) */
} frg_ctl_host;

typedef struct frg_ctl frg_ctl;
/* font / display: as frui_new / frui_set_look; look FRUI_LOOK_* */
frg_ctl *frg_ctl_new(const frg_ctl_host *host, frui_font *font, frui_font *display, int look);
void frg_ctl_free(frg_ctl *c);
/* the frg_client to give the platform view */
const frg_client *frg_ctl_client(frg_ctl *c);
/* the current model (tests, the platform's accessibility later) */
const frui_model *frg_ctl_model(const frg_ctl *c);
const frui *frg_ctl_frui(const frg_ctl *c);           /* (the widgets of the last draw: tests) */
#endif
