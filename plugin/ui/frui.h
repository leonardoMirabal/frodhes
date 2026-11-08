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
#define FRUI_PL_MAX 320            /* the PRESETS sheet's entries: the 57 factory presets and the first user ones */
#define FRUI_PL_PAGER 100          /* the PRESETS sheet's pager (an FRW_PAGER id beside REVERB's) */

/* the two effect panels' tabs */
enum { FRT_MOTOR, FRT_COMP, FRT_EQ, FRT_DRIVE, FRT_NL };                                   /* left: before the drive */
enum { FRT_FILTER, FRT_TREM, FRT_PHASER, FRT_CHORUS, FRT_DELAY, FRT_REVERB, FRT_NR };      /* right: after it */

typedef struct frui_model_s {
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
    /* typing a value (a double-click on a knob's value): the knob's id + 1 (0: none) and the text so far */
    int editing;
    char edit_text[12];
    /* PRESETS: a sheet over the main view (a click on the preset's name), every preset grouped by its instrument, the
     * factory ones, then the user's; a click loads one and leaves the sheet open, so the keys can try it */
    int presets;                   /* the sheet is open */
    int pl_n, pl_cur, pl_page;     /* the entries; the one playing (-1: none); the page shown */
    struct { char tag[8]; char name[24]; uint8_t type, user; } pl[FRUI_PL_MAX];   /* type: the instrument (TYPE) */
} frui_model;

/* the settings' pseudo parameter ids (for the controls and the widget ids) */
enum { FRS_CHANNEL = 200, FRS_VEL, FRS_FIXVEL, FRS_PGM, FRS_TEMPO, FRS_BPM, FRS_SYNCALL, FRS_SYNCDIV };

/* the app's text for a value: the value ("5.0", "1/8", "BELL DYNO") and its unit ("Hz", "dB", "" if none) */
typedef void (*frui_text_fn)(void *ctx, const frui_model *m, int id, char *val, char *unit);

enum { FRW_KNOB, FRW_SEG, FRW_MENU, FRW_TAB_L, FRW_TAB_R, FRW_BUTTON, FRW_PAGER, FRW_PRESET, FRW_KEYS, FRW_FADER, FRW_CTX,
       FRW_CCMAP, FRW_ITEM, FRW_PITEM };   /* FRW_ITEM: an item of the open menu (id: the option's value); FRW_PITEM:
                                            * a preset in the PRESETS sheet (id: its index in pl) */
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
/* the look: the brushed-aluminium name rail over a black body (A1), one aluminium plate with black sections set into
 * it (A2), or the default (A3): a rounded aluminium plate carrying the name rail and a thin frame round one rounded
 * black body, the sections in it on concentric radii; display is the wordmark's and the section titles' font (0: the
 * text font) */
enum { FRUI_LOOK_RAIL, FRUI_LOOK_PLATE, FRUI_LOOK_A3 };
void frui_set_look(frui *u, int look, frui_font *display);
/* what frui_draw paints (the window's cache, plugin/gui_core.c): everything (the default); only the static layer (the
 * background and the section panels: the same for every model at a size); or everything but it. The static layer drawn
 * once, then the dynamic one over a copy of it, gives exactly the pixels of one whole draw (each static shape comes
 * before every dynamic one that touches its pixels: plugin/tests/test_gui_ctl.c checks it) */
enum { FRUI_LAYER_ALL, FRUI_LAYER_STATIC, FRUI_LAYER_DYNAMIC };
void frui_set_layer(frui *u, int layer);
/* the rounded shapes of the last draw that nest (plate, body, sections, keyboard, corner switches), for the layout
 * check: nested corners must be concentric (inner radius = outer radius - the gap); logical pixels */
typedef struct { float x, y, w, h, r; } frui_shell;
int frui_shell_count(const frui *u);
const frui_shell *frui_shell_get(const frui *u, int i);
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

/* ---- for the plugin window's input handling and partial repaints (B-M2b) */
/* a control's value as drawn (the parameters, TUNE, TREM/WAH's EFFECT, the settings) and its range 0..hi */
int frui_value(const frui_model *m, int id);
int frui_value_hi(int id);
int frui_range(int id);                 /* (= frui_value_hi) */
/* the option names of a switch / menu (n = hi + 1) */
const char *const *frui_options(int id, int *n);
/* the MIDI note of the on-screen key at logical (x, y), -1 none; a key's span along x (logical pixels) */
int frui_key_at(float x, float y);
void frui_key_span(int note, float *x0, float *x1);
/* the segment of a segmented switch under logical x */
int frui_seg_at(const frui_widget *w, float x);
/* regions of the last draw that change without the layout changing (logical rectangles; w = 0 when not drawn) */
enum { FRR_METER, FRR_GR, FRR_EQ, FRR_RAIL, FRR_PANEL_L, FRR_PANEL_R, FRR_KEYS, FRR_VOICE, FRR_N };
void frui_region(const frui *u, int which, float r[4]);
/* the PRESETS sheet's pages (four columns of 17 rows each) for the model's list */
int frui_preset_pages(const frui_model *m);
int frui_preset_page_of(const frui_model *m, int idx);   /* the page that shows entry idx (0 when none) */
#endif
