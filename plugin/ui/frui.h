/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the window (B-M2a). One fixed layout in logical pixels (960 x 600, aspect 16:10), drawn at any
 * scale (0.75 .. 2.0 of the logical size, times the backing scale: 1x / 2x) straight into an RGBA canvas. The UI owns
 * no sound state: it draws a frui_model (the app's parameter values and what the host shows) and reports the
 * widget under a point (frui_hit) for the plugin's input handling (B-M2b). */
#ifndef FRUI_H_INCLUDED
#define FRUI_H_INCLUDED
#include <stdint.h>
#include "frui_font.h"
#include "frui_gfx.h"
#include "frui_params.h"

#define FRUI_W 960                 /* logical size */
#define FRUI_H 600

/* the two effect panels' tabs */
enum { FRT_MOTOR, FRT_COMP, FRT_EQ, FRT_DRIVE, FRT_NL };                                   /* left: before the drive */
enum { FRT_FILTER, FRT_TREM, FRT_PHASER, FRT_CHORUS, FRT_DELAY, FRT_REVERB, FRT_NR };      /* right: after it */

typedef struct {
    uint8_t p[FRP_COUNT];          /* the app's parameter values (params.c PV) */
    int tune;                      /* cents, -50 .. +50 */
    char preset_tag[8];            /* "F09", "U01" */
    char preset_name[24];
    int edited;                    /* the sound differs from the stored preset */
    float out_db[2], out_peak_db[2];   /* the output meter, dBFS */
    float gr_db;                   /* the compressor's gain reduction now, dB (>= 0) */
    uint8_t notes[128];            /* held notes (velocity, 0 = up) */
    int tab_l, tab_r, page_rv;     /* the selected tabs; REVERB's page (0, 1) */
    int focus;                     /* widget id with the keyboard focus ring, or -1 */
    int hover;                     /* widget id under the mouse, or -1 */
    int menu;                      /* widget id whose menu is open, or -1 */
    int sustain;                   /* the sustain is down: the PEDAL button latched, a CC 64 or a real pedal */
    int fx_bypass;                 /* every effect bypassed at once (their own switches unchanged; not saved) */
    int ctx_knob;                  /* the knob whose right-click menu (MIDI LEARN ...) is open, or -1 */
    int learning;                  /* the knob waiting for a CC (MIDI LEARN), or -1 */
    uint8_t learn_cc[FRP_COUNT];   /* a learned CC per parameter, the CC + 1 (0: the default map only) */
    /* SETTINGS (the sheet over the main view, from the rail's gear) */
    int settings;                  /* the sheet is open */
    int midi_ch;                   /* 0 = all channels, 1..16 */
    int vel_curve;                 /* MIDI velocity: MEDIUM LIGHT HEAVY FIXED (the device's TOUCH curves) */
    int fixed_vel;                 /* 1..127: the velocity on FIXED (the device's KEY VEL) */
    int pgm_change;                /* program changes load presets */
    int tempo_src;                 /* HOST, INTERNAL, MIDI CLOCK */
    int bpm10;                     /* the internal tempo, BPM x 10; host_bpm10: the host's (shown when it leads) */
    int host_bpm10;
    int sync_all, sync_div;        /* new: every tempo effect synced (FREE / TEMPO), at this FRN_SYNC division */
    int cc_scroll;                 /* the CC map's first row */
} frui_model;

/* the settings' pseudo parameter ids (for the controls and the widget ids) */
enum { FRS_CHANNEL = 200, FRS_VEL, FRS_FIXVEL, FRS_PGM, FRS_TEMPO, FRS_BPM, FRS_SYNCALL, FRS_SYNCDIV };

/* the app's text for a value: the value ("5.0", "1/8", "BELL DYNO") and its unit ("Hz", "dB", "" if none) */
typedef void (*frui_text_fn)(void *ctx, const frui_model *m, int id, char *val, char *unit);

enum { FRW_KNOB, FRW_SEG, FRW_MENU, FRW_TAB_L, FRW_TAB_R, FRW_BUTTON, FRW_PAGER, FRW_PRESET, FRW_KEYS, FRW_FADER, FRW_CTX,
       FRW_CCMAP };
enum { FRB_PREV, FRB_NEXT, FRB_SAVE, FRB_UNDO, FRB_TYPE_PREV, FRB_TYPE_NEXT, FRB_GEAR, FRB_CLOSE, FRB_SUSTAIN, FRB_FX,
       FRB_BPM_DOWN, FRB_BPM_UP, FRB_LEARN_RESET };
/* the knob menu's items (FRW_CTX ids) */
enum { FRC_LEARN, FRC_FORGET, FRC_DEFAULT };
typedef struct {
    int kind, id;                  /* kind FRW_*; id: the parameter (FRP_*), the tab, the button (FRB_*) */
    float x, y, w, h;              /* logical pixels */
} frui_widget;

typedef struct frui frui;
frui *frui_new(frui_font *font, frui_text_fn text, void *ctx);
void frui_free(frui *u);
/* the look: the brushed-aluminium name rail over a black body, or one aluminium plate with black sections set into
 * it; display is the wordmark's and the section titles' font (0: the text font) */
enum { FRUI_LOOK_RAIL, FRUI_LOOK_PLATE };
void frui_set_look(frui *u, int look, frui_font *display);
/* the widgets of the last draw, for the input handling and the layout checks */
int frui_widget_count(const frui *u);
const frui_widget *frui_widget_get(const frui *u, int i);
/* the text anchors of the last draw (for the layout check): what (FRA_*), its widget id, the text's centre or left x
 * and its baseline, logical pixels */
enum { FRA_LABEL, FRA_VALUE, FRA_TAB, FRA_TITLE, FRA_RAIL, FRA_CAPTION, FRA_ROWLABEL };
typedef struct { int what, id; float x, y; } frui_anchor;
int frui_anchor_count(const frui *u);
const frui_anchor *frui_anchor_get(const frui *u, int i);
/* draws the whole window into c (c->w x c->h pixels = FRUI_W x FRUI_H logical times the scale) */
void frui_draw(frui *u, const frui_model *m, frui_canvas *c);
/* the widget at logical point (x, y) after the last draw, or -1; *w gets its description */
int frui_hit(const frui *u, float x, float y, frui_widget *w);
/* widget ids for focus / hover / menu: (kind << 8) | id */
#define FRUI_WID(kind, id) (((kind) << 8) | (id))
#endif
