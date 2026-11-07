/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the pixel canvas and its anti-aliased primitives (B-M2a).
 * Plain C99, float allowed (plugin/ only). Every shape is drawn from an exact signed distance (circles, arcs, rounded
 * rectangles, capsules: coverage = clamp(0.5 - d, 0, 1) per pixel centre) or from exact area coverage (paths and
 * glyphs, frui_path_fill), so edges are clean at any scale. Colours are 0xAARRGGBB, straight alpha; the canvas is
 * opaque and blends in sRGB (as the device's screen and the manual's pictures look). */
#ifndef FRUI_GFX_H
#define FRUI_GFX_H
#include <stdint.h>

typedef struct {
    uint32_t *px;                  /* w * h pixels, 0xFFRRGGBB */
    int w, h;
    int cx0, cy0, cx1, cy1;        /* clip rectangle, pixels: x0 <= x < x1 */
} frui_canvas;

#define FRUI_A(c) ((uint32_t)(c) >> 24)
#define FRUI_RGB(r, g, b) (0xFF000000u | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b))
#define FRUI_RGBA(r, g, b, a) (((uint32_t)(a) << 24) | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b))

void frui_canvas_init(frui_canvas *c, uint32_t *px, int w, int h);
void frui_clip(frui_canvas *c, float x, float y, float w, float h);   /* intersect with the full canvas */
void frui_clip_reset(frui_canvas *c);

uint32_t frui_mix(uint32_t a, uint32_t b, float t);                   /* a .. b, alpha too */
uint32_t frui_alpha(uint32_t c, float a);                             /* c with its alpha x a */
void frui_blend(frui_canvas *c, int x, int y, uint32_t col, float cov);

void frui_fill(frui_canvas *c, uint32_t col);
void frui_rect(frui_canvas *c, float x, float y, float w, float h, uint32_t col);   /* AA edges at fractions */
void frui_rrect(frui_canvas *c, float x, float y, float w, float h, float r, uint32_t col);
void frui_rrect_grad(frui_canvas *c, float x, float y, float w, float h, float r, uint32_t top, uint32_t bot);
void frui_rrect_stroke(frui_canvas *c, float x, float y, float w, float h, float r, float lw, uint32_t col);
void frui_rrect_shadow(frui_canvas *c, float x, float y, float w, float h, float r, float blur, uint32_t col);
void frui_circle(frui_canvas *c, float cx, float cy, float r, uint32_t col);
void frui_circle_grad(frui_canvas *c, float cx, float cy, float r, uint32_t top, uint32_t bot);
void frui_ring(frui_canvas *c, float cx, float cy, float r, float lw, uint32_t col);
void frui_circle_shadow(frui_canvas *c, float cx, float cy, float r, float blur, uint32_t col);
/* an arc of radius r, line width lw, round caps, from angle a0 to a1 (radians, a0 < a1; 0 = 3 o'clock, counter-
 * clockwise positive, as on paper with y up) */
void frui_arc(frui_canvas *c, float cx, float cy, float r, float lw, float a0, float a1, uint32_t col);
void frui_line(frui_canvas *c, float x0, float y0, float x1, float y1, float lw, uint32_t col);   /* round caps */

/* paths: closed polygons (curves flattened by the caller or by frui_path_quad), non-zero winding, exact coverage */
typedef struct {
    float *xy;                     /* points, x y pairs */
    int n, cap;
    int *starts;                   /* contour start indices */
    int nc, ccap;
} frui_path;
void frui_path_init(frui_path *p);
void frui_path_free(frui_path *p);
void frui_path_clear(frui_path *p);
void frui_path_move(frui_path *p, float x, float y);
void frui_path_line(frui_path *p, float x, float y);
void frui_path_quad(frui_path *p, float cx, float cy, float x, float y);   /* flattened to ~0.1 px */
void frui_path_fill(frui_canvas *c, const frui_path *p, uint32_t col);
/* coverage into a w x h float mask (cleared by the caller); used by the glyph rasteriser */
void frui_path_mask(const frui_path *p, float *mask, int w, int h);
#endif
