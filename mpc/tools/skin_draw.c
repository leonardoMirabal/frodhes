/* SPDX-License-Identifier: GPL-3.0-only */
/* The Akai MPC skin's painter (mpc/tools/make_skin.py drives it): the desktop plugin's own drawing primitives
 * (plugin/ui/frui_gfx.c, frui_font.c, the fonts in assets/fonts) and copies of its look's parts (plugin/ui/frui.c:
 * the A3 plate and body, the panels, the knob, the segmented switch's well and key, the lamps, engraved text), so the
 * MPC's pictures are the plugin's, pixel for pixel at the same scale.
 *
 *   skin_draw <text font> <display font> < script
 *
 * The script, one command per line, coordinates in logical pixels (the plugin's units), colours AARRGGBB in hex:
 *   img <path> <w px> <h px> <scale> <alpha>    start a picture (alpha 1: drawn on black and on white, the alpha
 *                                                worked out from the two, written as RGBA; 0: opaque RGB)
 *   end                                         write it
 *   fill <col> | plate x y w h rout frame bodyy | panel x y w h r | hline x0 x1 y col | vline x y0 y1 col
 *   rrect x y w h r col | stroke x y w h r col | well x y w h r | key x y w h r seed | lamp cx cy r on
 *   pilot cx cy | knob cx cy r v centred | chev cx cy size dir col
 *   text face size weight track align x cy col engrave tnum "string"
 *        (face 0 text, 1 display; align l c r; cy the line's centre on the capitals; an x of "+N" in text and
 *        pilot: N after where the last text ended)
 * The output depends only on the script and the fonts (no time, no randomness beyond the plugin's seeded grain). */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>
#include "../../plugin/ui/frui_font.h"
#include "../../plugin/ui/frui_gfx.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static frui_font *ft[2];
static frui_canvas *C;
static float S = 1.f;
static float last_x1;              /* where the last text ended (logical): an x of "+N" is N after it */
static float xpos(const char *a) { return a[0] == '+' ? last_x1 + strtof(a + 1, 0) : strtof(a, 0); }
#define X(v) ((v) * S)

/* ---- PNG (8-bit RGB or RGBA, zlib) */
static uint32_t crc_tab[256];
static uint32_t crc(uint32_t c, const uint8_t *p, size_t n)
{
    size_t i;
    if (!crc_tab[1]) {
        uint32_t k, j, v;
        for (k = 0; k < 256; k++) { v = k; for (j = 0; j < 8; j++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1; crc_tab[k] = v; }
    }
    for (i = 0; i < n; i++) c = crc_tab[(c ^ p[i]) & 255u] ^ (c >> 8);
    return c;
}
static void chunk(FILE *f, const char *t, const uint8_t *d, uint32_t n)
{
    uint8_t b[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n};
    uint32_t c;
    fwrite(b, 1, 4, f); fwrite(t, 1, 4, f); if (n) fwrite(d, 1, n, f);
    c = crc(crc(0xFFFFFFFFu, (const uint8_t *)t, 4), d, n) ^ 0xFFFFFFFFu;
    b[0] = (uint8_t)(c >> 24); b[1] = (uint8_t)(c >> 16); b[2] = (uint8_t)(c >> 8); b[3] = (uint8_t)c;
    fwrite(b, 1, 4, f);
}
/* px: 0xAARRGGBB; ch 3 or 4 */
static int write_png(const char *path, const uint32_t *px, int w, int h, int ch)
{
    size_t rl = (size_t)w * (size_t)ch + 1, raw_n = rl * (size_t)h;
    uint8_t *raw = malloc(raw_n), hdr[13];
    uLongf zn = compressBound((uLong)raw_n);
    uint8_t *z = malloc(zn);
    FILE *f;
    int x, y;
    for (y = 0; y < h; y++) {
        uint8_t *r = raw + rl * (size_t)y;
        r[0] = 0;
        for (x = 0; x < w; x++) {
            uint32_t c = px[(size_t)y * (size_t)w + (size_t)x];
            uint8_t *q = r + 1 + ch * x;
            q[0] = (uint8_t)(c >> 16); q[1] = (uint8_t)(c >> 8); q[2] = (uint8_t)c;
            if (ch == 4) q[3] = (uint8_t)(c >> 24);
        }
    }
    compress2(z, &zn, raw, (uLong)raw_n, 9);
    f = fopen(path, "wb");
    if (!f) { free(raw); free(z); return -1; }
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    hdr[0] = (uint8_t)(w >> 24); hdr[1] = (uint8_t)(w >> 16); hdr[2] = (uint8_t)(w >> 8); hdr[3] = (uint8_t)w;
    hdr[4] = (uint8_t)(h >> 24); hdr[5] = (uint8_t)(h >> 16); hdr[6] = (uint8_t)(h >> 8); hdr[7] = (uint8_t)h;
    hdr[8] = 8; hdr[9] = ch == 4 ? 6 : 2; hdr[10] = hdr[11] = hdr[12] = 0;
    chunk(f, "IHDR", hdr, 13);
    chunk(f, "IDAT", z, (uint32_t)zn);
    chunk(f, "IEND", 0, 0);
    fclose(f);
    free(raw); free(z);
    return 0;
}

/* ---- the look (plugin/ui/frui.c, A3) */
#define C_INSET FRUI_RGB(9, 9, 10)
#define C_EDGE FRUI_RGB(38, 38, 40)
#define C_ACC FRUI_RGB(255, 152, 38)
#define C_AL_T FRUI_RGB(204, 206, 209)
#define C_AL_B FRUI_RGB(170, 172, 176)
#define C_ENGR FRUI_RGB(34, 35, 38)
#define C_HL FRUI_RGBA(255, 255, 255, 150)

static float text_w(int face, float size, float weight, float track, int flags, const char *s)
{
    frui_style st = {size * S, weight, track * S, flags};
    return frui_text_width(ft[face], &st, s) / S - track;
}
static void text_at(int face, float x, float y, float size, float weight, float track, int flags, const char *s, uint32_t col)
{
    frui_style st = {size * S, weight, track * S, flags};
    frui_text(C, ft[face], &st, X(x), X(y), s, col);
}
static void alu(float x, float y, float w, float h, float r, uint32_t seed)
{
    frui_brushed(C, X(x), X(y), X(w), X(h), X(r), C_AL_T, C_AL_B, 0.045f, S, seed);
    frui_rrect_stroke(C, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(r), 1.f, FRUI_RGBA(0, 0, 0, 70));
    if (h > 6.f) {
        frui_rect(C, X(x + r), X(y) + 1.f, X(w - 2.f * r), 1.f, FRUI_RGBA(255, 255, 255, 170));
        frui_rect(C, X(x + r), X(y + h) - 2.f, X(w - 2.f * r), 1.f, FRUI_RGBA(0, 0, 0, 60));
    }
}
static void plate(float x, float y, float W, float H, float rout, float frame, float body_y)
{
    float rb = rout - frame, bx = x + frame, by = y + body_y, bw = W - 2.f * frame, bh = H - body_y - frame;
    frui_brushed(C, X(x), X(y), X(W), X(H), X(rout), FRUI_RGB(200, 202, 205), FRUI_RGB(176, 178, 182), 0.03f, S, 1u);
    frui_rrect_stroke(C, X(x) + 0.5f, X(y) + 0.5f, X(W) - 1.f, X(H) - 1.f, X(rout), 1.f, FRUI_RGBA(0, 0, 0, 110));
    frui_rrect_stroke(C, X(x) + 1.5f, X(y) + 1.5f, X(W) - 3.f, X(H) - 3.f, X(rout) - 1.f, 1.f, FRUI_RGBA(255, 255, 255, 110));
    frui_rrect(C, X(bx), X(by) + 1.f, X(bw), X(bh), X(rb), FRUI_RGBA(255, 255, 255, 150));
    frui_rrect(C, X(bx), X(by), X(bw), X(bh), X(rb), FRUI_RGB(52, 53, 56));
    frui_rrect(C, X(bx) + 1.f, X(by) + 1.f, X(bw) - 2.f, X(bh) - 2.f, X(rb) - 1.f, FRUI_RGB(2, 2, 3));
    frui_rrect(C, X(bx) + 1.f, X(by) + 3.f, X(bw) - 2.f, X(bh) - 4.f, X(rb) - 1.f, FRUI_RGB(8, 8, 9));
}
static void panel(float x, float y, float w, float h, float r)
{
    frui_rrect(C, X(x), X(y) + 1.f, X(w), X(h), X(r), FRUI_RGBA(0, 0, 0, 200));
    frui_rrect(C, X(x), X(y), X(w), X(h), X(r), FRUI_RGB(40, 40, 42));
    frui_rrect_grad(C, X(x), X(y) + 1.f, X(w), X(h) - 1.f, X(r), FRUI_RGB(24, 24, 25), FRUI_RGB(19, 19, 20));
    frui_rrect_stroke(C, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(r), 1.f, FRUI_RGBA(255, 255, 255, 9));
}
static void well(float x, float y, float w, float h, float r)   /* the segmented switch's and the menus' dark well */
{
    frui_rrect(C, X(x), X(y), X(w), X(h), X(r), C_INSET);
    frui_rrect_stroke(C, X(x) + 0.5f, X(y) + 0.5f, X(w) - 1.f, X(h) - 1.f, X(r), 1.f, C_EDGE);
}
static void key(float x, float y, float w, float h, float r, uint32_t seed)   /* the chosen segment */
{
    frui_rrect_shadow(C, X(x), X(y + 1.f), X(w), X(h), X(r), X(1.5f), FRUI_RGBA(0, 0, 0, 200));
    alu(x, y, w, h, r, seed);
}
static void lamp(float cx, float cy, float r, int on)
{
    if (on) {
        frui_circle_shadow(C, X(cx), X(cy), X(r), X(r * 1.4f), FRUI_RGBA(255, 140, 20, 110));
        frui_circle_grad(C, X(cx), X(cy), X(r), FRUI_RGB(255, 206, 120), C_ACC);
    } else {
        frui_circle_grad(C, X(cx), X(cy), X(r), FRUI_RGB(30, 30, 32), FRUI_RGB(52, 52, 56));
    }
}
static void pilot(float x, float cy)   /* the wordmark's pilot lamp in its spun bezel */
{
    frui_circle(C, X(x), X(cy) + 1.f, X(6.f), C_HL);
    frui_circle(C, X(x), X(cy), X(6.f), FRUI_RGB(70, 71, 74));
    frui_spun(C, X(x), X(cy), X(5.f), FRUI_RGB(120, 122, 126), FRUI_RGB(240, 240, 240), 0.04f, S, 5u);
    frui_circle_shadow(C, X(x), X(cy), X(3.2f), X(4.f), FRUI_RGBA(255, 120, 10, 140));
    frui_circle_grad(C, X(x), X(cy), X(3.4f), FRUI_RGB(255, 196, 96), FRUI_RGB(214, 92, 4));
    frui_circle(C, X(x - 1.f), X(cy - 1.2f), X(1.1f), FRUI_RGBA(255, 244, 220, 220));
}
static void chevron(float cx, float cy, float sz, int dir, uint32_t col)   /* dir 0 down, 1 left, 2 right */
{
    float a = sz, b = sz * 0.5f, lw = X(1.4f);
    if (dir == 0) {
        frui_line(C, X(cx - a), X(cy - b), X(cx), X(cy + b), lw, col);
        frui_line(C, X(cx), X(cy + b), X(cx + a), X(cy - b), lw, col);
    } else {
        float d = dir == 1 ? 1.f : -1.f;
        frui_line(C, X(cx + b * d), X(cy - a), X(cx - b * d), X(cy), lw, col);
        frui_line(C, X(cx - b * d), X(cy), X(cx + b * d), X(cy + a), lw, col);
    }
}
/* the knob (frui.c knob_body, lit, not hovered): a black skirt with a fine knurl, a spun-aluminium cap, a white
 * index; the scale's ticks light up to the value (from the left end, or from the top when centred) */
#define KA0 (225.f * (float)M_PI / 180.f)
#define KSPAN (270.f * (float)M_PI / 180.f)
static void knob(float cx, float cy, float r, float v, int centred)
{
    float a = KA0 - KSPAN * v, rc = r * 0.54f, ca = cosf(a), sa = sinf(a);
    int i;
    for (i = 0; i <= 10; i++) {
        float t = (float)i / 10.f, ta = KA0 - KSPAN * t, r0 = r + 4.f, r1 = r + (i % 5 ? 7.f : 9.f);
        int on = centred ? (v >= 0.5f ? t >= 0.5f - 1e-3f && t <= v + 1e-3f : t <= 0.5f + 1e-3f && t >= v - 1e-3f)
                         : t <= v + 1e-3f && v > 0.f;
        uint32_t col = on ? FRUI_RGB(238, 237, 232) : FRUI_RGB(50, 50, 54);
        frui_line(C, X(cx + cosf(ta) * r0), X(cy - sinf(ta) * r0), X(cx + cosf(ta) * r1), X(cy - sinf(ta) * r1), X(1.3f), col);
    }
    frui_circle_shadow(C, X(cx), X(cy + r * 0.14f), X(r * 0.96f), X(r * 0.16f), FRUI_RGBA(0, 0, 0, 200));
    frui_circle_grad(C, X(cx), X(cy), X(r), FRUI_RGB(54, 54, 57), FRUI_RGB(10, 10, 11));
    for (i = 0; i < 72; i++) {
        float t = (float)i * 2.f * (float)M_PI / 72.f, l = 0.5f + 0.5f * sinf(t);
        frui_line(C, X(cx + cosf(t) * (r - 2.6f)), X(cy - sinf(t) * (r - 2.6f)), X(cx + cosf(t) * (r - 0.7f)),
                  X(cy - sinf(t) * (r - 0.7f)), 1.f, FRUI_RGBA(255, 255, 255, (uint32_t)(10.f + 34.f * l * l)));
    }
    frui_arc(C, X(cx), X(cy), X(r) - 0.5f, 1.f, 0.35f, (float)M_PI - 0.35f, FRUI_RGBA(255, 255, 255, 60));
    frui_circle(C, X(cx), X(cy), X(rc + 1.6f), FRUI_RGB(4, 4, 5));
    frui_spun(C, X(cx), X(cy), X(rc), FRUI_RGB(92, 94, 98), FRUI_RGB(222, 223, 225), 0.05f, S, 7u);
    frui_ring(C, X(cx), X(cy), X(rc) - 0.5f, 1.f, FRUI_RGBA(255, 255, 255, 110));
    frui_line(C, X(cx + ca * (rc + 3.f)), X(cy - sa * (rc + 3.f)), X(cx + ca * (r - 3.2f)), X(cy - sa * (r - 3.2f)),
              X(r >= 20.f ? 2.f : 1.6f), FRUI_RGB(246, 246, 242));
    frui_line(C, X(cx + ca * rc * 0.45f), X(cy - sa * rc * 0.45f), X(cx + ca * (rc - 2.f)), X(cy - sa * (rc - 2.f)),
              X(1.4f), FRUI_RGBA(30, 30, 33, 200));
}

/* ---- the script */
static uint32_t hexcol(const char *s) { return (uint32_t)strtoul(s, 0, 16); }

static void run_line(char *ln)
{
    char cmd[16], a[12][64];
    float f[12];
    int n = 0, i;
    char *p = ln, *q;
    if (sscanf(p, "%15s", cmd) != 1) return;
    p += strlen(cmd);
    if (!strcmp(cmd, "text")) {   /* the string is the quoted tail */
        char *s0 = strchr(p, '"'), *s1 = s0 ? strrchr(s0 + 1, '"') : 0;
        int face, eng, tnum;
        float size, wt, tr, x, cy, w;
        char al[4], col[16], xs[24];
        if (!s0 || !s1) { fprintf(stderr, "skin_draw: bad text line\n"); exit(1); }
        *s0 = 0; *s1 = 0;
        if (sscanf(p, "%d %f %f %f %3s %23s %f %15s %d %d", &face, &size, &wt, &tr, al, xs, &cy, col, &eng, &tnum) != 10) {
            fprintf(stderr, "skin_draw: bad text arguments\n"); exit(1);
        }
        x = xpos(xs);
        w = text_w(face, size, wt, tr, tnum ? FRUI_TNUM : 0, s0 + 1);
        if (al[0] == 'c') x -= w * 0.5f;
        else if (al[0] == 'r') x -= w;
        cy += frui_font_ascent(ft[face], size * S) / S * 0.5f;
        if (eng) text_at(face, x, cy + 1.f / S, size, wt, tr, tnum ? FRUI_TNUM : 0, s0 + 1, C_HL);
        text_at(face, x, cy, size, wt, tr, tnum ? FRUI_TNUM : 0, s0 + 1, hexcol(col));
        last_x1 = x + w;
        return;
    }
    while (n < 12 && (q = strtok(n ? 0 : p, " \t\r\n"))) { snprintf(a[n], sizeof a[n], "%s", q); f[n] = strtof(q, 0); n++; }
    for (i = n; i < 12; i++) f[i] = 0.f;
    if (!strcmp(cmd, "fill")) frui_fill(C, hexcol(a[0]));
    else if (!strcmp(cmd, "plate")) plate(f[0], f[1], f[2], f[3], f[4], f[5], f[6]);
    else if (!strcmp(cmd, "panel")) panel(f[0], f[1], f[2], f[3], f[4]);
    else if (!strcmp(cmd, "hline")) frui_rect(C, X(f[0]), floorf(X(f[2])), X(f[1] - f[0]), 1.f, hexcol(a[3]));
    else if (!strcmp(cmd, "vline")) frui_rect(C, floorf(X(f[0])), X(f[1]), 1.f, X(f[2] - f[1]), hexcol(a[3]));
    else if (!strcmp(cmd, "rrect")) frui_rrect(C, X(f[0]), X(f[1]), X(f[2]), X(f[3]), X(f[4]), hexcol(a[5]));
    else if (!strcmp(cmd, "stroke"))
        frui_rrect_stroke(C, X(f[0]) + 0.5f, X(f[1]) + 0.5f, X(f[2]) - 1.f, X(f[3]) - 1.f, X(f[4]), 1.f, hexcol(a[5]));
    else if (!strcmp(cmd, "well")) well(f[0], f[1], f[2], f[3], f[4]);
    else if (!strcmp(cmd, "key")) key(f[0], f[1], f[2], f[3], f[4], (uint32_t)f[5]);
    else if (!strcmp(cmd, "lamp")) lamp(f[0], f[1], f[2], (int)f[3]);
    else if (!strcmp(cmd, "pilot")) pilot(xpos(a[0]), f[1]);
    else if (!strcmp(cmd, "knob")) knob(f[0], f[1], f[2], f[3], (int)f[4]);
    else if (!strcmp(cmd, "chev")) chevron(f[0], f[1], f[2], (int)f[3], hexcol(a[4]));
    else { fprintf(stderr, "skin_draw: unknown command %s\n", cmd); exit(1); }
}

static uint8_t *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    uint8_t *d;
    long len;
    if (!f) { fprintf(stderr, "skin_draw: cannot open %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); len = ftell(f); fseek(f, 0, SEEK_SET);
    d = malloc((size_t)len);
    if (fread(d, 1, (size_t)len, f) != (size_t)len) { fprintf(stderr, "skin_draw: cannot read %s\n", path); exit(1); }
    fclose(f);
    *n = (size_t)len;
    return d;
}

int main(int argc, char **argv)
{
    static char line[4096];
    char **lines = 0, path[1024] = "";
    int nl = 0, cap = 0, w = 0, h = 0, alpha = 0, k;
    size_t n0, n1;
    uint8_t *d0, *d1;
    if (argc != 3) { fprintf(stderr, "usage: skin_draw <text font> <display font> < script\n"); return 2; }
    d0 = slurp(argv[1], &n0); d1 = slurp(argv[2], &n1);
    ft[0] = frui_font_load(d0, n0); ft[1] = frui_font_load(d1, n1);
    if (!ft[0] || !ft[1]) { fprintf(stderr, "skin_draw: bad font\n"); return 1; }
    while (fgets(line, sizeof line, stdin)) {
        if (!strncmp(line, "img ", 4)) {
            float sc;
            if (sscanf(line + 4, "%1023s %d %d %f %d", path, &w, &h, &sc, &alpha) != 5 || w <= 0 || h <= 0) {
                fprintf(stderr, "skin_draw: bad img line\n"); return 1;
            }
            S = sc; nl = 0;
        } else if (!strncmp(line, "end", 3)) {
            size_t np = (size_t)w * (size_t)h, i;
            uint32_t *px[2];
            frui_canvas cv[2];
            for (k = 0; k < 1 + alpha; k++) {
                int j;
                px[k] = malloc(np * sizeof(uint32_t));
                frui_canvas_init(&cv[k], px[k], w, h);
                C = &cv[k];
                frui_fill(C, k ? FRUI_RGB(255, 255, 255) : FRUI_RGB(0, 0, 0));
                for (j = 0; j < nl; j++) { char tmp[4096]; snprintf(tmp, sizeof tmp, "%s", lines[j]); run_line(tmp); }
            }
            if (alpha) {   /* on black: c * a; on white: c * a + (1 - a): a from the difference, c from the black */
                for (i = 0; i < np; i++) {
                    uint32_t b = px[0][i], wt = px[1][i], o = 0;
                    int ch, am = 0, cs[3];
                    for (ch = 0; ch < 3; ch++) {
                        int cb = (int)((b >> (16 - 8 * ch)) & 255u), cw = (int)((wt >> (16 - 8 * ch)) & 255u);
                        int aa = 255 - (cw - cb);
                        if (aa > am) am = aa;
                        cs[ch] = cb;
                    }
                    if (am > 255) am = 255;
                    if (am > 0)
                        for (ch = 0; ch < 3; ch++) {
                            int c = (cs[ch] * 255 + am / 2) / am;
                            o |= (uint32_t)(c > 255 ? 255 : c) << (16 - 8 * ch);
                        }
                    px[0][i] = o | ((uint32_t)am << 24);
                }
                free(px[1]);
            }
            if (write_png(path, px[0], w, h, alpha ? 4 : 3)) { fprintf(stderr, "skin_draw: cannot write %s\n", path); return 1; }
            free(px[0]);
            for (k = 0; k < nl; k++) free(lines[k]);
            nl = 0;
        } else if (line[0] != '#' && line[0] != '\n') {
            if (nl == cap) { cap = cap ? cap * 2 : 256; lines = realloc(lines, sizeof(char *) * (size_t)cap); }
            lines[nl++] = strdup(line);
        }
    }
    return 0;
}
