/* SPDX-License-Identifier: GPL-3.0-only */
/* A stand-in plugin for the window's tests (plugin/tests/test_gui_ctl.c, gui_win_demo.c, gui_x11_demo.c): the
 * frp_ui_host_t (gui_host.h) over an array of values, the defaults at mid-range, every call counted. Not the real
 * parameter path (that is plugin/src/frodhes_clap.c's). */
#ifndef FRODHES_GUI_FAKE_HOST_H
#define FRODHES_GUI_FAKE_HOST_H
#include <stdio.h>
#include <string.h>
#include "gui_core.h"

extern const unsigned char frp_font_text[], frp_font_display[];
extern const unsigned long frp_font_text_len, frp_font_display_len;

typedef struct {
    int v[512];                          /* the parameters by id, FRP_TUNE (0..100), the settings FRS_* */
    int sets, gestures_open, gesture_begins, notes_on, notes_off, last_note, last_vel, learns, last_learn, learning;
    /* the preset list: n entries (the factory ones, then the user's), the one playing, the names saved */
    int np, pcur, loads, saves;
    char pname[64][24];
    int ptype[64], puser[64];
} fake_host;

static int fh_get(void *ctx, uint32_t id, double *v) { *v = ((fake_host *)ctx)->v[id]; return 0; }
static double fh_def(void *ctx, uint32_t id) { (void)ctx; return (frui_range((int)id) + 1) / 2; }
static void fh_gesture(void *ctx, uint32_t id, int begin)
{
    fake_host *h = ctx;
    (void)id;
    h->gestures_open += begin ? 1 : -1;
    if (begin) h->gesture_begins++;
}
static void fh_set(void *ctx, uint32_t id, double v) { fake_host *h = ctx; h->sets++; h->v[id] = (int)v; }
static void fh_midi(void *ctx, uint8_t st, uint8_t d1, uint8_t d2)
{
    fake_host *h = ctx;
    if ((st & 0xF0) == 0x90 && d2) { h->notes_on++; h->last_note = d1; h->last_vel = d2; }
    else if ((st & 0xF0) == 0x80 || (st & 0xF0) == 0x90) h->notes_off++;
}
static void fh_monitor(void *ctx, frp_ui_monitor_t *o) { (void)ctx; memset(o, 0, sizeof *o); }
static void fh_text(void *ctx, const struct frui_model_s *m, int id, char *val, char *unit)
{
    (void)ctx;
    unit[0] = 0;
    sprintf(val, "%d", id == FRP_TUNE ? m->tune : id >= 0 && id < FRP_COUNT ? m->p[id] : 0);
}
static void fh_preset(void *ctx, char tag[8], char name[24], int *edited)
{
    fake_host *h = ctx;
    if (h->pcur >= 0 && h->pcur < h->np) {
        snprintf(tag, 8, "%c%02d", h->puser[h->pcur] ? 'U' : 'F', h->pcur + 1);
        memcpy(name, h->pname[h->pcur], 24);
    } else { strcpy(tag, "F01"); strcpy(name, "TEST"); }
    *edited = 0;
}
static int fh_preset_entry(void *ctx, int idx, char tag[8], char name[24], int *type, int *user)
{
    fake_host *h = ctx;
    if (idx < 0 || idx >= h->np) return -1;
    snprintf(tag, 8, "%c%02d", h->puser[idx] ? 'U' : 'F', idx + 1);
    memcpy(name, h->pname[idx], 24);
    *type = h->ptype[idx]; *user = h->puser[idx];
    return 0;
}
static int fh_preset_index(void *ctx) { return ((fake_host *)ctx)->pcur; }
static int fh_preset_load(void *ctx, int idx)
{
    fake_host *h = ctx;
    if (idx < 0 || idx >= h->np) return -1;
    h->pcur = idx; h->loads++;
    return 0;
}
static int fh_preset_save_as(void *ctx, const char *name)
{
    fake_host *h = ctx;
    int i;
    h->saves++;
    for (i = 0; i < h->np; i++) if (h->puser[i] && !strcmp(h->pname[i], name)) { h->pcur = i; return i; }
    if (h->np >= 64) return -1;
    snprintf(h->pname[h->np], 24, "%s", name);
    h->ptype[h->np] = h->v[FRP_TYPE]; h->puser[h->np] = 1;
    h->pcur = h->np;
    return h->np++;
}
static int fh_setting_get(void *ctx, int key, int *v)
{
    fake_host *h = ctx;
    if (key == FRP_UI_TUNE) { *v = h->v[FRP_TUNE] - 50; return 0; }
    if (key == FRP_UI_HOST_BPM10) { *v = 1200; return 0; }
    if (key >= FRS_CHANNEL && key <= FRS_SYNCDIV) { *v = h->v[key]; return 0; }
    return -1;
}
static void fh_setting_set(void *ctx, int key, int v)
{
    fake_host *h = ctx;
    if (key == FRP_UI_TUNE) h->v[FRP_TUNE] = v + 50;
    else if (key >= FRS_CHANNEL && key <= FRS_SYNCDIV) h->v[key] = v;
}
static void fh_learn_get(void *ctx, uint8_t cc1[], int n, int *learning)
{
    memset(cc1, 0, (size_t)n);
    *learning = ((fake_host *)ctx)->learning;
}
static void fh_learn_cmd(void *ctx, int cmd, int id)
{
    fake_host *h = ctx;
    h->learns++; h->last_learn = cmd;
    h->learning = cmd == FRP_UI_LEARN_START ? id : -1;
}
static void fh_init(fake_host *h, frp_ui_host_t *uh)
{
    int i;
    memset(h, 0, sizeof *h);
    for (i = 0; i < FRP_COUNT; i++) h->v[i] = (int)fh_def(h, (uint32_t)i);
    h->v[FRP_TYPE] = 0;
    h->v[FRP_TUNE] = 50;
    h->v[FRS_FIXVEL] = 100; h->v[FRS_PGM] = 1; h->v[FRS_BPM] = 1200; h->v[FRS_SYNCDIV] = 6;
    h->learning = -1;
    {   /* twelve factory presets over the six instruments, two user presets */
        static const char *const nm[14] = {"STAGE", "SUITCASE", "FELT", "BELL", "VIBES", "MARIMBA", "STAGE TWO",
                                            "PHASER", "WARM FELT", "MOTOR VIBES", "WOOD", "DRIVEN", "MY BALLAD", "MY VIBES"};
        static const int ty[14] = {0, 1, 2, 3, 4, 5, 0, 0, 2, 4, 5, 0, 0, 4};
        for (i = 0; i < 14; i++) { snprintf(h->pname[i], 24, "%s", nm[i]); h->ptype[i] = ty[i]; h->puser[i] = i >= 12; }
        h->np = 14; h->pcur = 0;
    }
    memset(uh, 0, sizeof *uh);
    uh->ctx = h;
    uh->param_get = fh_get; uh->param_default = fh_def; uh->param_gesture = fh_gesture; uh->param_set = fh_set;
    uh->midi = fh_midi; uh->monitor = fh_monitor; uh->text = fh_text; uh->preset = fh_preset;
    uh->setting_get = fh_setting_get; uh->setting_set = fh_setting_set;
    uh->learn_get = fh_learn_get; uh->learn_cmd = fh_learn_cmd;
    uh->preset_entry = fh_preset_entry; uh->preset_index = fh_preset_index;
    uh->preset_load = fh_preset_load; uh->preset_save_as = fh_preset_save_as;
    uh->version = "DEV 1a2b";                 /* (a dev build's VERSION, as frp_ui_version_text gives it) */
}
/* the centre of the first widget of a kind (and id, -1: any) of the last draw; its index, -1 none */
static int fh_find(const frui *u, int kind, int id, float *x, float *y)
{
    int i, n = frui_widget_count(u);
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        if (g->kind == kind && (id < 0 || g->id == id)) { *x = g->x + g->w * 0.5f; *y = g->y + g->h * 0.5f; return i; }
    }
    return -1;
}
/* a knob's value text (its anchor, a little above the baseline: inside the double-click's zone); 0: not drawn */
static inline int fh_value_at(const frui *u, int id, float *x, float *y)
{
    int i, n = frui_anchor_count(u);
    for (i = 0; i < n; i++) {
        const frui_anchor *a = frui_anchor_get(u, i);
        if (a->what == FRA_VALUE && a->id == id) { *x = a->x; *y = a->y - 6.f; return 1; }
    }
    return 0;
}
#endif
