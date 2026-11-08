/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin core's interface for the plugin's own UI (the GUI extension, B-M2b): everything the window shows and
 * every action it takes, so the GUI code never touches the bridge, the slots or the app. Implemented by
 * plugin/src/frodhes_clap.c (the instance) over plugin/src/bridge.c (the slots).
 *
 * Threads: every function here is for the main thread (CLAP's GUI thread), except where it says otherwise. They never
 * block on the audio thread: reads come from what the audio side last published, writes go through a queue the audio
 * thread drains at its next block (or at once while the plugin is not active). Every UI write is also reported to
 * the host (CLAP parameter output events, with gestures around a drag), so the host's automation and the UI never
 * disagree.
 *
 * Ids: the parameter ids are plugin/gen/params_map.h's (the app's params.c ids 0 .. FRP_NPARAM_APP - 1, the plugin's
 * own from 0x10000: FRP_ID_ENGINE, FRP_ID_TUNE, FRP_ID_PRESET, FRP_ID_SUSTAIN, FRP_ID_FX_BYPASS). Values are always the
 * app's integers 0 .. hi.
 *
 * Stability: this header is append-only. FRP_UI_IFACE_VERSION bumps only when something is added. */
#ifndef FRP_UI_IFACE_H
#define FRP_UI_IFACE_H
#include <stdint.h>

#define FRP_UI_IFACE_VERSION 3

typedef struct frp_core frp_core;            /* one plugin instance */
struct clap_plugin;
frp_core *frp_core_of(const struct clap_plugin *plugin);
void **frp_ui_gui_data(frp_core *c);         /* the GUI's own pointer for this instance (NULL until it sets one) */
int frp_ui_has_slot(const frp_core *c);      /* 0: this instance is beyond the 16 that play (it stays silent) */

/* ---- change detection: bumps whenever anything the window shows changed (a value, a preset, a setting, a learned
 * CC, the sustain, the bypass), not for the meters and the held notes (poll those at the frame rate) */
uint32_t frp_ui_rev(const frp_core *c);

/* ---- parameters */
int frp_ui_param_get(const frp_core *c, uint32_t id);        /* the value now (0..hi), -1 an unknown id */
int frp_ui_param_hi(uint32_t id);                            /* -1 an unknown id */
int frp_ui_param_default(const frp_core *c, uint32_t id);    /* a fresh instance's value (double-click) */
/* the text of value v: the number or name in val ("5.0", "1/8", "BELL DYNO", "+5") and the unit in unit ("Hz", "kHz",
 * "ms", "s", "dB", "c", or "" when none); both always terminated */
void frp_ui_param_text(const frp_core *c, uint32_t id, int v, char *val, uint32_t nval, char *unit, uint32_t nunit);
const char *frp_ui_param_name(uint32_t id);                  /* the host's long name ("Tremolo depth"), "" unknown */
/* a drag: begin, any number of sets, end (the host's undo groups it). A single click or a typed value may call set
 * alone. A set to the value it already has does nothing. */
void frp_ui_param_begin(frp_core *c, uint32_t id);
void frp_ui_param_set(frp_core *c, uint32_t id, int v);
void frp_ui_param_end(frp_core *c, uint32_t id);

/* ---- presets: the factory presets F01..F57 (F01..F19 the FM-1's, F20..F57 the Move's variants), then the user's own
 * (files in frp_ui_preset_dir(), sorted by name). Indices run 0 .. count - 1 in that order; the factory ones never
 * move, a user preset's index can change when files are added or removed (frp_ui_rev bumps). */
typedef struct {
    char tag[8];                             /* "F03", "U07" (the user's: their position in the list, 1-based) */
    char name[32];                           /* "SUITCASE", "MY BALLAD" */
    int user;                                /* 1: a user file (it can be overwritten or deleted) */
} frp_preset_info;
int frp_ui_preset_count(const frp_core *c);
int frp_ui_preset_factory_count(void);       /* 57 */
int frp_ui_preset_info(const frp_core *c, int idx, frp_preset_info *out);    /* 0 ok, -1 no such index */
/* what plays: its index (-1: a sound not in the list, e.g. a deleted user preset or a state from elsewhere), its
 * tag and name in *out, and *edited = 1 when the sound differs from the preset as stored */
int frp_ui_preset_current(const frp_core *c, frp_preset_info *out, int *edited);
int frp_ui_preset_load(frp_core *c, int idx);    /* 0 ok, -1 no such index / the file did not read */
int frp_ui_preset_step(frp_core *c, int dir);    /* the next (+1) / previous (-1), wrapping; the loaded index */
/* save the sound playing now as a user preset named name (1..24 printable characters; an existing user preset of the
 * same name is overwritten). Returns its index, -1 when the file could not be written. */
int frp_ui_preset_save(frp_core *c, const char *name);
int frp_ui_preset_delete(frp_core *c, int idx);  /* user presets only: 0 ok, -1 not a user preset / not removed */
void frp_ui_preset_rescan(frp_core *c);          /* read the folder again (files added from outside) */
const char *frp_ui_preset_dir(void);             /* the user folder (created on the first save) */
/* UNDO: the sound before the last preset load (a load, a step, a program change), as the device's UNDO: a second
 * undo goes back to the preset again */
int frp_ui_can_undo(const frp_core *c);
int frp_ui_undo(frp_core *c);                    /* 0 done, -1 nothing to undo */

/* ---- settings (saved in the host's project with the sound) */
enum { FRP_VEL_MEDIUM, FRP_VEL_LIGHT, FRP_VEL_HEAVY, FRP_VEL_FIXED };     /* the MIDI velocity curve */
enum { FRP_TEMPO_HOST, FRP_TEMPO_INTERNAL, FRP_TEMPO_MIDI };              /* the tempo source */
typedef struct {
    int midi_ch;                             /* 0 = all channels, 1..16 */
    int vel_curve;                           /* FRP_VEL_*: MEDIUM = the velocity as played; LIGHT louder, HEAVY softer
                                              * at the same touch (the device's TOUCH curves); FIXED: fixed_vel always */
    int fixed_vel;                           /* 1..127 */
    int pgm_change;                          /* 1: MIDI program changes load presets */
    int tempo_src;                           /* FRP_TEMPO_*: HOST the DAW's tempo (120 when it has none); INTERNAL
                                              * bpm10; MIDI CLOCK an incoming MIDI clock (bpm10 while none comes).
                                              * With HOST a MIDI clock sent to the plugin still leads, as on the
                                              * device's CLOCK EXT. */
    int bpm10;                               /* the internal tempo, BPM x 10, 400..2400 */
    int sync_all;                            /* 1 (TEMPO): every tempo effect's SYNC is set to sync_div now and on every
                                              * preset load; 0 (FREE): every SYNC set to FREE now, presets load as
                                              * stored. The per-effect SYNC parameters stay editable either way. */
    int sync_div;                            /* 1..13: a division (params_map.h's SYNC texts: 1/32 .. 2BAR) */
} frp_settings;
void frp_ui_settings_get(const frp_core *c, frp_settings *out);
void frp_ui_settings_set(frp_core *c, const frp_settings *s);    /* applies the fields that changed; values clamped */
int frp_ui_host_bpm10(const frp_core *c);    /* the DAW's tempo x 10 (0: it sends none) */
int frp_ui_tempo_bpm10(const frp_core *c);   /* the tempo the effects follow now, whatever its source */

/* ---- MIDI learn: a learned CC drives its parameter before the default map (the device's CC table, params_map.h's
 * cc); one CC per parameter and one parameter per CC; saved in the state. CCs that cannot be learned: 0, 32 (bank
 * select), 64 (sustain), 120..127 (channel mode). */
void frp_ui_learn_start(frp_core *c, uint32_t id);   /* the next CC that arrives is id's (any id with hi > 0) */
void frp_ui_learn_cancel(frp_core *c);
int frp_ui_learning(const frp_core *c);              /* the id waiting for a CC, -1 none */
int frp_ui_learned_cc(const frp_core *c, uint32_t id);   /* id's learned CC, -1 none */
void frp_ui_learn_forget(frp_core *c, uint32_t id);
void frp_ui_learn_forget_all(frp_core *c);
int frp_ui_default_cc(uint32_t id);                  /* the device's CC for id, -1 none */
/* what CC cc drives now: the parameter id (-1 none); *learned = 1 when by a learned mapping */
int frp_ui_cc_target(const frp_core *c, int cc, int *learned);

/* ---- performance */
void frp_ui_sustain_set(frp_core *c, int on);   /* the SUSTAIN latch: CC 64 127 / 0 into the app (a pedal's CC 64 below
                                                 * 64 lets the latch go, as on the device) */
int frp_ui_sustain(const frp_core *c);          /* the dampers are held off now (the latch, a pedal, a CC 64) */
void frp_ui_fx_bypass_set(frp_core *c, int on); /* every effect off at once (the device's FX button; their own
                                                 * switches unchanged; not saved) */
int frp_ui_fx_bypass(const frp_core *c);
void frp_ui_note(frp_core *c, int key, int vel);    /* the on-screen keyboard: vel 1..127 on, 0 off (channel 1) */

/* ---- live values (poll at the frame rate; any thread) */
void frp_ui_meters(const frp_core *c, float out_db[2], float peak_db[2], float *gr_db);   /* dBFS (-120 floor),
                                                 * the peak held ~1.5 s; the compressor's gain reduction, dB >= 0 */
void frp_ui_notes(const frp_core *c, uint8_t notes[128]);   /* the keys held now (their velocity, 0 = up) */
int frp_ui_voices(const frp_core *c);           /* the voice count of the sound path: CLASSIC 10, HQ 16 */

/* ---- (version 3) the preset list's grouping, the window's size in the project */
int frp_ui_preset_type(const frp_core *c, int idx);  /* preset idx's instrument (its TYPE, 0..5), -1 no such preset */
int frp_ui_win_w(const frp_core *c);                 /* the window's logical width saved with the state (0: never set:
                                                      * the default size) */
void frp_ui_win_w_set(frp_core *c, int w);           /* 720..1920 (0: the default); saved with the project ("win") */

/* ---- (version 2) the same functions as a CLAP extension, for out-of-process callers (the headless test host,
 * plugin/tests/clap_render.c --coretest): plugin->get_extension(plugin, FRP_UI_EXT_ID) returns a const frp_ui_vtable *.
 * The GUI inside the plugin calls the functions above directly. */
#define FRP_UI_EXT_ID "io.github.leonardomirabal.frodhes.ui-iface/2"
typedef struct frp_ui_vtable {
    uint32_t version;                            /* FRP_UI_IFACE_VERSION */
    frp_core *(*core_of)(const struct clap_plugin *plugin);
    int (*has_slot)(const frp_core *c);
    uint32_t (*rev)(const frp_core *c);
    int (*param_get)(const frp_core *c, uint32_t id);
    int (*param_default)(const frp_core *c, uint32_t id);
    void (*param_text)(const frp_core *c, uint32_t id, int v, char *val, uint32_t nval, char *unit, uint32_t nunit);
    void (*param_begin)(frp_core *c, uint32_t id);
    void (*param_set)(frp_core *c, uint32_t id, int v);
    void (*param_end)(frp_core *c, uint32_t id);
    int (*preset_count)(const frp_core *c);
    int (*preset_info)(const frp_core *c, int idx, frp_preset_info *out);
    int (*preset_current)(const frp_core *c, frp_preset_info *out, int *edited);
    int (*preset_load)(frp_core *c, int idx);
    int (*preset_step)(frp_core *c, int dir);
    int (*preset_save)(frp_core *c, const char *name);
    int (*preset_delete)(frp_core *c, int idx);
    void (*preset_rescan)(frp_core *c);
    int (*can_undo)(const frp_core *c);
    int (*undo)(frp_core *c);
    void (*settings_get)(const frp_core *c, frp_settings *out);
    void (*settings_set)(frp_core *c, const frp_settings *s);
    int (*host_bpm10)(const frp_core *c);
    int (*tempo_bpm10)(const frp_core *c);
    void (*learn_start)(frp_core *c, uint32_t id);
    void (*learn_cancel)(frp_core *c);
    int (*learning)(const frp_core *c);
    int (*learned_cc)(const frp_core *c, uint32_t id);
    void (*learn_forget)(frp_core *c, uint32_t id);
    void (*learn_forget_all)(frp_core *c);
    int (*cc_target)(const frp_core *c, int cc, int *learned);
    void (*sustain_set)(frp_core *c, int on);
    int (*sustain)(const frp_core *c);
    void (*fx_bypass_set)(frp_core *c, int on);
    int (*fx_bypass)(const frp_core *c);
    void (*note)(frp_core *c, int key, int vel);
    void (*meters)(const frp_core *c, float out_db[2], float peak_db[2], float *gr_db);
    void (*notes)(const frp_core *c, uint8_t notes[128]);
    int (*voices)(const frp_core *c);
    /* version 3 */
    int (*preset_type)(const frp_core *c, int idx);
    int (*win_w)(const frp_core *c);
    void (*win_w_set)(frp_core *c, int w);
} frp_ui_vtable;
#endif
