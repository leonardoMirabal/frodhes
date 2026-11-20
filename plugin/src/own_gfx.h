/* SPDX-License-Identifier: GPL-3.0-only */
/* The screen calls of the plugin's own-code build (FR_OWN_HAL): what firmware/src/ui.c draws through, as no-ops.
 * A plugin slot shows no frame (the plugin's window draws from the slot's published values), so nothing is kept: no
 * canvas, no fonts, no glyph tables. Written from the names and contracts ui.c and frodhes.c call.
 *   colours   RGB(), C_BLACK / C_WHITE, the five-step screen palette pal[] (C_LINE .. C_HI) and PALETTES[]
 *   canvas    cv_begin, cv_blit, cv_pset, cv_rect (the lcd_* calls are own_hal.c's)
 *   text      aafont_t with the two faces AF_S and AF_L (line height, baseline, capital box), aa_text and its
 *             right / centred / over-the-canvas forms, aa_text_w, aa_covers
 * Every text is 0 px wide, every face covers every character, so "does it fit" is always yes; nothing the app keeps or
 * publishes is decided by a width (the drawing code only places pixels with them: tests/py/test_own_gfx.py). */
#ifndef FRODHES_PLUGIN_OWN_GFX_H
#define FRODHES_PLUGIN_OWN_GFX_H

#include <stdint.h>

/* ---- colours: 16-bit 5-6-5 */
#define RGB(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | (((b) & 0xFF) >> 3)))
#define C_BLACK 0x0000u
#define C_WHITE 0xFFFFu
typedef struct { const char *name; uint16_t c[5]; } palette_t;
static const palette_t PALETTES[] = {           /* five steps, darkest to brightest (the app reads step 3, and 0..4 as a set) */
    {"A", {RGB(24, 24, 28), RGB(56, 56, 64), RGB(104, 104, 116), RGB(176, 176, 190), RGB(232, 232, 240)}},
    {"B", {RGB(48, 24, 0), RGB(96, 48, 0), RGB(160, 80, 0), RGB(224, 120, 8), RGB(255, 170, 48)}},
};
#define NPALETTES (sizeof(PALETTES) / sizeof(PALETTES[0]))
static uint16_t pal[5];
#define C_LINE pal[0]
#define C_DIM pal[1]
#define C_GRAY pal[2]
#define C_AMB pal[3]
#define C_HI pal[4]
static void palette_set(uint32_t i) { uint32_t k; for (k = 0; k < 5u; k++) pal[k] = PALETTES[i % NPALETTES].c[k]; }

/* ---- a layout switch the drawing code reads (more room around a value); nothing is drawn with it here */
#define UI_BREATHE 1

/* ---- the canvas: nothing is drawn */
static void cv_begin(uint32_t w, uint32_t h, uint16_t bg) { (void)w; (void)h; (void)bg; }
static void cv_blit(uint32_t x, uint32_t y) { (void)x; (void)y; }
static inline void cv_pset(int32_t x, int32_t y, uint16_t c) { (void)x; (void)y; (void)c; }
static void cv_rect(int32_t x, int32_t y, int32_t w, int32_t h, uint16_t c) { (void)x; (void)y; (void)w; (void)h; (void)c; }

/* ---- text: a face is its line box, baseline and capital box (the app centres on the capitals) */
typedef struct { uint8_t h, asc; } aafont_t;
static const aafont_t AF_S = {16, 12}, AF_L = {32, 25};
#define AF_S_CAP_Y 4
#define AF_S_CAP_H 9
#define AF_L_CAP_Y 6
#define AF_L_CAP_H 19
static int32_t aa_text(int32_t x, int32_t y, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{ (void)y; (void)f; (void)s; (void)fg; (void)bg; return x; }
static int32_t aa_text_r(int32_t xr, int32_t y, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{ (void)y; (void)f; (void)s; (void)fg; (void)bg; return xr; }
static void aa_text_c(int32_t x, int32_t y, int32_t w, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{ (void)x; (void)y; (void)w; (void)f; (void)s; (void)fg; (void)bg; }
static int32_t aa_text_ov(int32_t x, int32_t y, const aafont_t *f, const char *s, uint16_t fg)
{ (void)y; (void)f; (void)s; (void)fg; return x; }
static int32_t aa_text_ovr(int32_t xr, int32_t y, const aafont_t *f, const char *s, uint16_t fg)
{ (void)y; (void)f; (void)s; (void)fg; return xr; }
static int32_t aa_text_w(const aafont_t *f, const char *s) { (void)f; (void)s; return 0; }
static int aa_covers(const aafont_t *f, const char *s) { (void)f; (void)s; return 1; }
#endif
