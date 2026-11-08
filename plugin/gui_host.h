/* SPDX-License-Identifier: GPL-3.0-only */
/* What the window's controller (plugin/gui_core.c) asks of its host: a table of calls, so the controller runs over
 * the plugin core (plugin/gui_clap.c binds it to plugin/frp_ui_iface.h's frp_ui_* calls) or over a test's stand-in
 * (plugin/tests/gui_fake_host.h) alike.
 *
 * The window owns no sound state: it reads the app's values and asks for changes through this table. Every call is
 * made on the main thread. The calls marked "core" are always there; for the others a NULL pointer means the window
 * keeps a local value for the session (it still draws and answers the control, nothing is saved).
 *
 * Ids: the parameters are params.c's ids (the CLAP ids, plugin/gen/params_map.h; values 0..hi as the app's PV). The
 * settings use frui.h's FRS_* ids and the FRP_UI_* keys below. */
#ifndef FRP_GUI_HOST_H
#define FRP_GUI_HOST_H
#include <stdint.h>

struct frui_model_s;                 /* (frui.h's frui_model; the text backend fills a value text from it) */

/* what plays now, for the meters and the keyboard (the audio thread publishes it; any thread reads) */
typedef struct {
    float peak[2];                   /* the output's peak since the last read, linear (1.0 = 0 dBFS) */
    float gr_db;                     /* the compressor's gain reduction now, dB (0 when unknown) */
    uint8_t notes[128];              /* held notes (from the host, MIDI and the window's keyboard): velocity, 0 = up */
    int sustain;                     /* the sustain is down (CC 64 >= 64 from any source) */
} frp_ui_monitor_t;

/* settings keys beyond frui.h's FRS_* (CHANNEL VEL FIXVEL PGM TEMPO BPM SYNCALL SYNCDIV) */
enum {
    FRP_UI_TUNE = 300,               /* cents -50 .. +50 */
    FRP_UI_FX_BYPASS,                /* every effect bypassed at once (the FX switch; their own switches kept) */
    FRP_UI_HOST_BPM10,               /* read only: the host's tempo x 10 (0: none) */
    FRP_UI_WIN_W,                    /* the window's logical width (saved with the state; 720 .. 1920) */
};
enum { FRP_UI_PRESET_PREV, FRP_UI_PRESET_NEXT, FRP_UI_PRESET_SAVE, FRP_UI_PRESET_UNDO };
enum { FRP_UI_LEARN_START, FRP_UI_LEARN_CANCEL, FRP_UI_LEARN_FORGET, FRP_UI_LEARN_FORGET_ALL };

typedef struct frp_ui_host {
    void *ctx;
    /* ---- core */
    int (*param_get)(void *ctx, uint32_t id, double *v);   /* the app's value now (host automation included): 0 ok */
    double (*param_default)(void *ctx, uint32_t id);
    /* a change from the window: gesture begin (1) / end (0) around a drag, the values in between; a click is one
     * set inside its own begin / end. Thread-safe: queued to the audio thread, sent to the host as CLAP parameter
     * events (gestures too, for the host's undo and automation recording) */
    void (*param_gesture)(void *ctx, uint32_t id, int begin);
    void (*param_set)(void *ctx, uint32_t id, double v);
    void (*midi)(void *ctx, uint8_t st, uint8_t d1, uint8_t d2);   /* the keyboard's notes, the SUSTAIN switch (CC 64) */
    void (*monitor)(void *ctx, frp_ui_monitor_t *out);    /* takes (and resets) the peaks */
    /* ---- the rest (NULL: the window keeps a local value) */
    /* the value text for a widget id (a parameter, FRP_TUNE) from the app (params.c param_text); NULL: the window's
     * own copy of that arithmetic (plugin/ui/frui_text.c) */
    void (*text)(void *ctx, const struct frui_model_s *m, int id, char *val, char *unit);
    void (*preset)(void *ctx, char tag[8], char name[24], int *edited);   /* the current preset */
    void (*preset_cmd)(void *ctx, int cmd);                              /* FRP_UI_PRESET_* */
    int (*setting_get)(void *ctx, int key, int *v);                      /* FRS_* / FRP_UI_*: 0 ok, -1 not known */
    void (*setting_set)(void *ctx, int key, int v);
    /* MIDI learn: the learned CC + 1 per parameter (0: the default map only) and the parameter waiting for a CC */
    void (*learn_get)(void *ctx, uint8_t cc1[], int n, int *learning);
    void (*learn_cmd)(void *ctx, int cmd, int id);                       /* FRP_UI_LEARN_* (id: the parameter) */
    /* the preset list (the PRESETS sheet; NULL: it stays empty): entry idx's tag, name, instrument (TYPE 0..5) and
     * whether it is the user's (0 ok, -1 past the end; the factory presets first, then the user's); the entry playing
     * (-1 none); load one (0 ok) */
    int (*preset_entry)(void *ctx, int idx, char tag[8], char name[24], int *type, int *user);
    int (*preset_index)(void *ctx);
    int (*preset_load)(void *ctx, int idx);
} frp_ui_host_t;

#endif
