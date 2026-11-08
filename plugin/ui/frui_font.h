/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: text (B-M2a). A small TrueType reader for the bundled variable font (Inter Tight, SIL OFL 1.1,
 * assets/fonts/): cmap, hmtx, glyf (simple and composite glyphs), gvar for the weight axis (any weight 100..900 from
 * the one file, with IUP), GPOS pair kerning; glyphs rasterised with exact area coverage at the backing scale, cached
 * per size and quarter-pixel x position. */
#ifndef FRUI_FONT_H
#define FRUI_FONT_H
#include <stddef.h>
#include <stdint.h>
#include "frui_gfx.h"

typedef struct frui_font frui_font;

/* the font bytes must stay valid while the font is used (the plugin embeds them; the render tool reads the file) */
frui_font *frui_font_load(const uint8_t *data, size_t len);
void frui_font_free(frui_font *f);

enum { FRUI_TNUM = 1 };            /* text flags: tabular figures (every digit the width of the widest) */
typedef struct {
    float size;                    /* pixels per em, at the backing scale */
    float weight;                  /* 100 .. 900 */
    float track;                   /* extra pixels after each character */
    int flags;
} frui_style;

float frui_text_width(frui_font *f, const frui_style *s, const char *str);
/* draws str with its baseline at y, starting at x; returns the advance */
float frui_text(frui_canvas *c, frui_font *f, const frui_style *s, float x, float y, const char *str, uint32_t col);
float frui_font_ascent(frui_font *f, float size);    /* cap height in pixels (the 'H' height), for vertical centring */
#endif
