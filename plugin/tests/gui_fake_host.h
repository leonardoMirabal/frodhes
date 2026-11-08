/* SPDX-License-Identifier: GPL-3.0-only */
/* A stand-in plugin for the window's tests (plugin/tests/test_gui_ctl.c, gui_win_demo.c): the values in an array,
 * the defaults at mid-range, every call counted. Not the real parameter path (that is the plugin's, B-M2b). */
#ifndef FRODHES_GUI_FAKE_HOST_H
#define FRODHES_GUI_FAKE_HOST_H
#include <stdio.h>
#include <string.h>
#include "gui_ctl.h"

extern const unsigned char frp_font_text[], frp_font_display[];
extern const unsigned long frp_font_text_len, frp_font_display_len;

typedef struct {
    int v[256];
    int sets, gestures_open, gesture_begins, buttons, last_button, notes_on, notes_off, last_note, learns, last_learn;
} fake_host;

static void fh_refresh(void *ctx, frui_model *m)
{
    fake_host *h = ctx;
    int i;
    for (i = 0; i < FRP_COUNT; i++) m->p[i] = (uint8_t)h->v[i];
    m->tune = h->v[FRP_TUNE] - 50;
    m->midi_ch = h->v[FRS_CHANNEL]; m->vel_curve = h->v[FRS_VEL]; m->fixed_vel = h->v[FRS_FIXVEL];
    m->pgm_change = h->v[FRS_PGM]; m->tempo_src = h->v[FRS_TEMPO]; m->sync_all = h->v[FRS_SYNCALL];
    m->sync_div = h->v[FRS_SYNCDIV];
    m->bpm10 = m->host_bpm10 = 1200;
    strcpy(m->preset_tag, "F01");
    strcpy(m->preset_name, "TEST");
}
static void fh_set(void *ctx, int id, int value)
{
    fake_host *h = ctx;
    h->sets++;
    if (id == FRP_TW_KIND) {             /* OFF PAN TREM WAH onto TW_SW + TW_MODE */
        h->v[FRP_TW_SW] = value == 0 ? 0 : value == 3 ? 2 : 1;
        if (value == 1 || value == 2) h->v[FRP_TW_MODE] = value == 2;
        return;
    }
    if (id >= 0 && id < 256) h->v[id] = value;
}
static void fh_gesture(void *ctx, int id, int begin)
{
    fake_host *h = ctx;
    (void)id;
    h->gestures_open += begin ? 1 : -1;
    if (begin) h->gesture_begins++;
}
static int fh_def(void *ctx, int id) { (void)ctx; return (frui_range(id) + 1) / 2; }
static void fh_button(void *ctx, int id) { fake_host *h = ctx; h->buttons++; h->last_button = id; }
static void fh_note(void *ctx, int key, int vel)
{
    fake_host *h = ctx;
    if (vel) { h->notes_on++; h->last_note = key; } else h->notes_off++;
}
static void fh_learn(void *ctx, int id, int what) { fake_host *h = ctx; (void)id; h->learns++; h->last_learn = what; }
static void fh_text(void *ctx, const frui_model *m, int id, char *val, char *unit)
{
    (void)ctx;
    unit[0] = 0;
    sprintf(val, "%d", id == FRP_TUNE ? m->tune : id >= 0 && id < FRP_COUNT ? m->p[id] : 0);
}
static void fh_init(fake_host *h, frg_ctl_host *ch)
{
    int i;
    memset(h, 0, sizeof *h);
    for (i = 0; i < FRP_COUNT; i++) h->v[i] = fh_def(h, i);
    h->v[FRP_TYPE] = 0;
    h->v[FRP_TUNE] = 50;
    h->v[FRS_FIXVEL] = 100;
    memset(ch, 0, sizeof *ch);
    ch->ctx = h;
    ch->refresh = fh_refresh; ch->set = fh_set; ch->gesture = fh_gesture; ch->def = fh_def;
    ch->button = fh_button; ch->note = fh_note; ch->learn = fh_learn; ch->text = fh_text;
}
/* the centre of the first widget of a kind (and id, -1: any) after a draw */
static int fh_find(frg_ctl *c, const frui *u, int kind, int id, float *x, float *y)
{
    int i, n = frui_widget_count(u);
    (void)c;
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        if (g->kind == kind && (id < 0 || g->id == id)) { *x = g->x + g->w * 0.5f; *y = g->y + g->h * 0.5f; return i; }
    }
    return -1;
}
#endif
