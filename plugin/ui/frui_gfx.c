/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: canvas and anti-aliased primitives (frui_gfx.h). */
#include "frui_gfx.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

void frui_canvas_init(frui_canvas *c, uint32_t *px, int w, int h)
{
    c->px = px; c->w = w; c->h = h;
    c->hole = 0;
    frui_clip_base(c, 0, 0, w, h);
}
void frui_clip_base(frui_canvas *c, int x0, int y0, int x1, int y1)
{
    c->bx0 = x0 < 0 ? 0 : x0; c->by0 = y0 < 0 ? 0 : y0;
    c->bx1 = x1 > c->w ? c->w : x1; c->by1 = y1 > c->h ? c->h : y1;
    frui_clip_reset(c);
}
void frui_clip_reset(frui_canvas *c) { c->cx0 = c->bx0; c->cy0 = c->by0; c->cx1 = c->bx1; c->cy1 = c->by1; }
void frui_clip(frui_canvas *c, float x, float y, float w, float h)
{
    int x0 = (int)floorf(x), y0 = (int)floorf(y), x1 = (int)ceilf(x + w), y1 = (int)ceilf(y + h);
    c->cx0 = x0 < c->bx0 ? c->bx0 : x0; c->cy0 = y0 < c->by0 ? c->by0 : y0;
    c->cx1 = x1 > c->bx1 ? c->bx1 : x1; c->cy1 = y1 > c->by1 ? c->by1 : y1;
}

uint32_t frui_mix(uint32_t a, uint32_t b, float t)
{
    uint32_t r = 0;
    int s;
    t = clampf(t, 0.f, 1.f);
    for (s = 0; s < 32; s += 8) {
        float x = (float)((a >> s) & 255u), y = (float)((b >> s) & 255u);
        r |= (uint32_t)(x + (y - x) * t + 0.5f) << s;
    }
    return r;
}
uint32_t frui_alpha(uint32_t c, float a)
{
    float x = (float)FRUI_A(c) * clampf(a, 0.f, 1.f);
    return (c & 0xFFFFFFu) | ((uint32_t)(x + 0.5f) << 24);
}
static inline void blend_px(uint32_t *d, uint32_t col, float cov)
{
    float a = cov * (float)FRUI_A(col) * (1.f / 255.f);
    uint32_t o;
    if (a <= 0.f) return;
    if (a >= 1.f) { *d = col | 0xFF000000u; return; }
    o = *d;
    {
        float r0 = (float)((o >> 16) & 255u), g0 = (float)((o >> 8) & 255u), b0 = (float)(o & 255u);
        float r1 = (float)((col >> 16) & 255u), g1 = (float)((col >> 8) & 255u), b1 = (float)(col & 255u);
        *d = FRUI_RGB((uint32_t)(r0 + (r1 - r0) * a + 0.5f), (uint32_t)(g0 + (g1 - g0) * a + 0.5f),
                      (uint32_t)(b0 + (b1 - b0) * a + 0.5f));
    }
}
void frui_blend(frui_canvas *c, int x, int y, uint32_t col, float cov)
{
    if (x < c->cx0 || y < c->cy0 || x >= c->cx1 || y >= c->cy1 || cov <= 0.f) return;
    blend_px(&c->px[(size_t)y * (size_t)c->w + (size_t)x], col, cov);
}
void frui_fill(frui_canvas *c, uint32_t col)
{
    int x, y;
    for (y = c->cy0; y < c->cy1; y++)
        for (x = c->cx0; x < c->cx1; x++) c->px[(size_t)y * (size_t)c->w + (size_t)x] = col | 0xFF000000u;
}

/* ---- shapes by signed distance: d < 0 inside, in pixels */
typedef float (*sdf_fn)(const void *u, float x, float y);
typedef uint32_t (*col_fn)(const void *u, float x, float y);
typedef struct { float cx, cy, hw, hh, r; } rr_t;
static float sd_rrect(const void *u, float x, float y)
{
    const rr_t *s = u;
    float r = s->r, qx = fabsf(x - s->cx) - (s->hw - r), qy = fabsf(y - s->cy) - (s->hh - r);
    float ox = qx > 0.f ? qx : 0.f, oy = qy > 0.f ? qy : 0.f, in = qx > qy ? qx : qy;
    return sqrtf(ox * ox + oy * oy) + (in < 0.f ? in : 0.f) - r;
}
/* A soft edge's erfc, skipped where its value is known exactly (the same pixels as computing it): beyond erf_out[a]
 * (d / blur) a colour of alpha a moves no channel (coverage x alpha under 1/1024 changes a value by less than a
 * quarter, which the blend's rounding drops); below erf_in1, 0.5 erfc is exactly 1.0f. Found once by bisection on
 * erfcf itself (monotonic). */
static float erf_out[256], erf_in1;
static int erf_ok[256], erf_in_ok;
static float erf_bisect(int (*hit)(float, int), int arg, float lo, float hi)   /* hit(lo) false, hit(hi) true */
{
    int i;
    for (i = 0; i < 60; i++) {
        float m = 0.5f * (lo + hi);
        if (m <= lo || m >= hi) break;
        if (hit(m, arg)) hi = m; else lo = m;
    }
    return hi;
}
static int erf_small(float x, int a) { return 0.5f * erfcf(x) * (float)a * (1.f / 255.f) < 1.f / 1024.f; }
static int erf_one(float x, int a) { (void)a; return 0.5f * erfcf(-x) == 1.f; }
static void erf_limits(int a, float *out, float *in1)
{
    if (!erf_ok[a]) { erf_out[a] = erf_bisect(erf_small, a, 0.f, 16.f); erf_ok[a] = 1; }
    if (!erf_in_ok) { erf_in1 = -erf_bisect(erf_one, 0, 0.f, 16.f); erf_in_ok = 1; }
    *out = erf_out[a]; *in1 = erf_in1;
}
static float sd_circle(const void *u, float x, float y);
static uint32_t col_vgrad(const void *u, float x, float y);
static rr_t mk_rr(float x, float y, float w, float h, float r);
static void shade(frui_canvas *c, float bx0, float by0, float bx1, float by1, sdf_fn f, const void *u, float lw,
                  float blur, uint32_t col, col_fn cf, const void *cu)
{
    int x0 = (int)floorf(bx0) - 1, y0 = (int)floorf(by0) - 1, x1 = (int)ceilf(bx1) + 1, y1 = (int)ceilf(by1) + 1, x, y;
    float out = 0.f, in1 = 0.f;
    int hole = blur > 0.f ? c->hole : 0;           /* (frui_shadow_hole: this shadow's) */
    rr_t hr;
    struct { float cx, cy, r; } hc = { 0.f, 0.f, 0.f };   /* (set only for hole 2: Linux gcc's -Wmaybe-uninitialized) */
    if (blur > 0.f) c->hole = 0;
    if (hole == 1) hr = mk_rr(c->hx, c->hy, c->hw, c->hh, c->hr);
    if (hole == 2) { hc.cx = c->hx; hc.cy = c->hy; hc.r = c->hr; }
    if (x0 < c->cx0) x0 = c->cx0;
    if (y0 < c->cy0) y0 = c->cy0;
    if (x1 > c->cx1) x1 = c->cx1;
    if (y1 > c->cy1) y1 = c->cy1;
    if (x0 >= x1 || y0 >= y1) return;
    if (blur > 0.f) erf_limits(cf ? 255 : (int)FRUI_A(col), &out, &in1);   /* (cf: any alpha, the limits of 255's) */
    for (y = y0; y < y1; y++) {
        uint32_t *row = c->px + (size_t)y * (size_t)c->w;
        float py = (float)y + 0.5f;
        uint32_t rc = col;
        col_fn rf = cf;
        int sx0 = x1, sx1 = x1;                     /* the row's deep interior (a rounded rectangle) */
        if (cf == col_vgrad) { rc = cf(cu, 0.f, py); rf = 0; }   /* (a vertical gradient: one colour per row) */
        if (f == sd_rrect && blur <= 0.f) {
            /* inside the core (both of sd_rrect's q <= 0) d = max(qx, qy) - r; a pixel with max(qx, qy) <= b is more
             * than a pixel inside the edge: a fill's coverage there is exactly 1, a stroke's exactly 0 (a pixel of
             * margin for the rounding: the same pixels as evaluating d) */
            const rr_t *q = u;
            float chw = q->hw - q->r, b = (lw > 0.f ? q->r - lw * 0.5f : q->r) - 1.5f - 1.f, qy;
            if (b > 0.f) b = 0.f;
            qy = fabsf(py - q->cy) - (q->hh - q->r);
            if (qy <= b && chw + b > 0.f) {
                sx0 = (int)ceilf(q->cx - (chw + b) - 0.5f) + 1;
                sx1 = (int)floorf(q->cx + (chw + b) - 0.5f);
                if (sx0 < x0) sx0 = x0;
                if (sx1 > x1) sx1 = x1;
                if (sx1 <= sx0) sx0 = sx1 = x1;
            }
        }
        for (x = x0; x < x1; x++) {
            if (x == sx0) {
                if (lw <= 0.f)
                    for (; x < sx1; x++) blend_px(row + x, rf ? rf(cu, (float)x + 0.5f, py) : rc, 1.f);
                x = sx1;
                if (x >= x1) break;
            }
            float px = (float)x + 0.5f, d = f(u, px, py), cov;
            if (lw > 0.f) d = fabsf(d) - lw * 0.5f;     /* a stroke centred on the edge */
            if (blur > 0.f) {                           /* a soft shadow: a Gaussian edge */
                float v = d / blur;
                if (v >= out) continue;
                if (hole && (hole == 1 ? sd_rrect(&hr, px, py) : sd_circle(&hc, px, py)) <= -0.5f) continue;
                cov = v <= in1 ? 1.f : 0.5f * erfcf(v);
            } else cov = clampf(0.5f - d, 0.f, 1.f);
            if (cov > 0.f) blend_px(row + x, rf ? rf(cu, px, py) : rc, cov);
        }
    }
}
static rr_t mk_rr(float x, float y, float w, float h, float r)
{
    rr_t s;
    float m = (w < h ? w : h) * 0.5f;
    s.cx = x + w * 0.5f; s.cy = y + h * 0.5f; s.hw = w * 0.5f; s.hh = h * 0.5f; s.r = r > m ? m : r < 0.f ? 0.f : r;
    return s;
}
void frui_rect(frui_canvas *c, float x, float y, float w, float h, uint32_t col)
{
    rr_t s = mk_rr(x, y, w, h, 0.f);
    shade(c, x, y, x + w, y + h, sd_rrect, &s, 0.f, 0.f, col, 0, 0);
}
void frui_rrect(frui_canvas *c, float x, float y, float w, float h, float r, uint32_t col)
{
    rr_t s = mk_rr(x, y, w, h, r);
    shade(c, x, y, x + w, y + h, sd_rrect, &s, 0.f, 0.f, col, 0, 0);
}
typedef struct { float y0, y1; uint32_t a, b; } grad_t;
static uint32_t col_vgrad(const void *u, float x, float y)
{
    const grad_t *g = u;
    (void)x;
    return frui_mix(g->a, g->b, (y - g->y0) / (g->y1 - g->y0));
}
void frui_rrect_grad(frui_canvas *c, float x, float y, float w, float h, float r, uint32_t top, uint32_t bot)
{
    rr_t s = mk_rr(x, y, w, h, r);
    grad_t g = {y, y + h, top, bot};
    shade(c, x, y, x + w, y + h, sd_rrect, &s, 0.f, 0.f, 0, col_vgrad, &g);
}
void frui_rrect_stroke(frui_canvas *c, float x, float y, float w, float h, float r, float lw, uint32_t col)
{
    rr_t s = mk_rr(x, y, w, h, r);
    shade(c, x - lw, y - lw, x + w + lw, y + h + lw, sd_rrect, &s, lw, 0.f, col, 0, 0);
}
void frui_rrect_shadow(frui_canvas *c, float x, float y, float w, float h, float r, float blur, uint32_t col)
{
    rr_t s = mk_rr(x, y, w, h, r);
    shade(c, x - 3.f * blur, y - 3.f * blur, x + w + 3.f * blur, y + h + 3.f * blur, sd_rrect, &s, 0.f, blur, col, 0, 0);
}

typedef struct { float cx, cy, r; } ci_t;
static float sd_circle(const void *u, float x, float y)
{
    const ci_t *s = u;
    float dx = x - s->cx, dy = y - s->cy;
    return sqrtf(dx * dx + dy * dy) - s->r;
}
void frui_circle(frui_canvas *c, float cx, float cy, float r, uint32_t col)
{
    ci_t s = {cx, cy, r};
    shade(c, cx - r, cy - r, cx + r, cy + r, sd_circle, &s, 0.f, 0.f, col, 0, 0);
}
void frui_circle_grad(frui_canvas *c, float cx, float cy, float r, uint32_t top, uint32_t bot)
{
    ci_t s = {cx, cy, r};
    grad_t g = {cy - r, cy + r, top, bot};
    shade(c, cx - r, cy - r, cx + r, cy + r, sd_circle, &s, 0.f, 0.f, 0, col_vgrad, &g);
}
void frui_ring(frui_canvas *c, float cx, float cy, float r, float lw, uint32_t col)
{
    ci_t s = {cx, cy, r};
    shade(c, cx - r - lw, cy - r - lw, cx + r + lw, cy + r + lw, sd_circle, &s, lw, 0.f, col, 0, 0);
}
void frui_shadow_hole_rrect(frui_canvas *c, float x, float y, float w, float h, float r)
{
    c->hole = 1; c->hx = x; c->hy = y; c->hw = w; c->hh = h; c->hr = r;
}
void frui_shadow_hole_circle(frui_canvas *c, float cx, float cy, float r)
{
    c->hole = 2; c->hx = cx; c->hy = cy; c->hr = r; c->hw = c->hh = 0.f;
}
void frui_circle_shadow(frui_canvas *c, float cx, float cy, float r, float blur, uint32_t col)
{
    ci_t s = {cx, cy, r};
    float e = r + 3.f * blur;
    shade(c, cx - e, cy - e, cx + e, cy + e, sd_circle, &s, 0.f, blur, col, 0, 0);
}

typedef struct { float cx, cy, r, a0, a1, hw; float e0x, e0y, e1x, e1y; } arc_t;
static float sd_arc(const void *u, float x, float y)
{
    const arc_t *s = u;
    float dx = x - s->cx, dy = s->cy - y, a = atan2f(dy, dx), d0, d1;
    float am = 0.5f * (s->a0 + s->a1), span = 0.5f * (s->a1 - s->a0), rel = a - am;
    while (rel > (float)M_PI) rel -= 2.f * (float)M_PI;
    while (rel < -(float)M_PI) rel += 2.f * (float)M_PI;
    if (fabsf(rel) <= span) return fabsf(sqrtf(dx * dx + dy * dy) - s->r) - s->hw;
    d0 = sqrtf((x - s->e0x) * (x - s->e0x) + (y - s->e0y) * (y - s->e0y));
    d1 = sqrtf((x - s->e1x) * (x - s->e1x) + (y - s->e1y) * (y - s->e1y));
    return (d0 < d1 ? d0 : d1) - s->hw;
}
void frui_arc(frui_canvas *c, float cx, float cy, float r, float lw, float a0, float a1, uint32_t col)
{
    arc_t s;
    float e = r + lw;
    if (a1 - a0 < 1e-4f) a1 = a0 + 1e-4f;
    s.cx = cx; s.cy = cy; s.r = r; s.a0 = a0; s.a1 = a1; s.hw = lw * 0.5f;
    s.e0x = cx + r * cosf(a0); s.e0y = cy - r * sinf(a0);
    s.e1x = cx + r * cosf(a1); s.e1y = cy - r * sinf(a1);
    shade(c, cx - e, cy - e, cx + e, cy + e, sd_arc, &s, 0.f, 0.f, col, 0, 0);
}
typedef struct { float x0, y0, x1, y1, hw; } seg_t;
static float sd_seg(const void *u, float x, float y)
{
    const seg_t *s = u;
    float px = x - s->x0, py = y - s->y0, bx = s->x1 - s->x0, by = s->y1 - s->y0, bb = bx * bx + by * by;
    float h = bb > 0.f ? clampf((px * bx + py * by) / bb, 0.f, 1.f) : 0.f;
    px -= bx * h; py -= by * h;
    return sqrtf(px * px + py * py) - s->hw;
}
void frui_line(frui_canvas *c, float x0, float y0, float x1, float y1, float lw, uint32_t col)
{
    seg_t s = {x0, y0, x1, y1, lw * 0.5f};
    float e = lw;
    shade(c, (x0 < x1 ? x0 : x1) - e, (y0 < y1 ? y0 : y1) - e, (x0 > x1 ? x0 : x1) + e, (y0 > y1 ? y0 : y1) + e,
          sd_seg, &s, 0.f, 0.f, col, 0, 0);
}

/* ---- paths: exact signed-area accumulation (the method of font-rs / libart: each edge adds its covered area to the
 * cells it crosses; a running sum along each row gives the coverage) */
void frui_path_init(frui_path *p) { memset(p, 0, sizeof *p); }
void frui_path_free(frui_path *p) { free(p->xy); free(p->starts); frui_path_init(p); }
void frui_path_clear(frui_path *p) { p->n = 0; p->nc = 0; }
static void pt_add(frui_path *p, float x, float y)
{
    if (p->n == p->cap) { p->cap = p->cap ? p->cap * 2 : 64; p->xy = realloc(p->xy, sizeof(float) * 2u * (size_t)p->cap); }
    p->xy[2 * p->n] = x; p->xy[2 * p->n + 1] = y; p->n++;
}
void frui_path_move(frui_path *p, float x, float y)
{
    if (p->nc == p->ccap) { p->ccap = p->ccap ? p->ccap * 2 : 8; p->starts = realloc(p->starts, sizeof(int) * (size_t)p->ccap); }
    p->starts[p->nc++] = p->n;
    pt_add(p, x, y);
}
void frui_path_line(frui_path *p, float x, float y) { pt_add(p, x, y); }
void frui_path_quad(frui_path *p, float cx, float cy, float x, float y)
{
    float x0 = p->xy[2 * p->n - 2], y0 = p->xy[2 * p->n - 1];
    float ddx = x0 - 2.f * cx + x, ddy = y0 - 2.f * cy + y, dd = sqrtf(ddx * ddx + ddy * ddy);
    int n = 1 + (int)sqrtf(dd * 2.5f), i;   /* error ~ dd / (8 n^2) <= 0.05 px */
    if (n > 64) n = 64;
    for (i = 1; i <= n; i++) {
        float t = (float)i / (float)n, u = 1.f - t;
        pt_add(p, u * u * x0 + 2.f * u * t * cx + t * t * x, u * u * y0 + 2.f * u * t * cy + t * t * y);
    }
}
static void acc_line(float *a, int w, int h, float x0, float y0, float x1, float y1)
{
    float dir = 1.f, dxdy, x;
    int y, ystart, yend;
    if (fabsf(y0 - y1) < 1e-6f) return;
    if (y0 > y1) { float t; dir = -1.f; t = x0; x0 = x1; x1 = t; t = y0; y0 = y1; y1 = t; }
    dxdy = (x1 - x0) / (y1 - y0);
    x = x0;
    ystart = y0 < 0.f ? 0 : (int)y0;
    if (y0 < 0.f) x -= y0 * dxdy;
    yend = (int)ceilf(y1);
    if (yend > h) yend = h;
    for (y = ystart; y < yend; y++) {
        float *row = a + (size_t)y * (size_t)(w + 2);
        float ytop = (float)y > y0 ? (float)y : y0, ybot = (float)(y + 1) < y1 ? (float)(y + 1) : y1;
        float dy = ybot - ytop, xnext = x + dxdy * dy, d = dy * dir;
        float xa = x < xnext ? x : xnext, xb = x < xnext ? xnext : x;
        float xaf, xbc;
        int xai, xbi;
        if (xa < 0.f) xa = 0.f;
        if (xb < 0.f) xb = 0.f;
        if (xa > (float)w) xa = (float)w;
        if (xb > (float)w) xb = (float)w;
        xaf = floorf(xa); xai = (int)xaf; xbc = ceilf(xb); xbi = (int)xbc;
        if (xbi <= xai + 1) {
            float xm = 0.5f * (xa + xb) - xaf;
            row[xai] += d - d * xm;
            row[xai + 1] += d * xm;
        } else {
            float s = 1.f / (xb - xa), x0f = xa - xaf, a0 = 0.5f * s * (1.f - x0f) * (1.f - x0f);
            float x1f = xb - xbc + 1.f, am = 0.5f * s * x1f * x1f;
            int xi;
            row[xai] += d * a0;
            if (xbi == xai + 2) row[xai + 1] += d * (1.f - a0 - am);
            else {
                float a1 = s * (1.5f - x0f), a2;
                row[xai + 1] += d * (a1 - a0);
                for (xi = xai + 2; xi < xbi - 1; xi++) row[xi] += d * s;
                a2 = a1 + (float)(xbi - xai - 3) * s;
                row[xbi - 1] += d * (1.f - a2 - am);
            }
            row[xbi] += d * am;
        }
        x = xnext;
    }
}
/* mask: w x h coverage out; internally rows of w + 2 accumulators */
void frui_path_mask(const frui_path *p, float *mask, int w, int h)
{
    float *a = calloc((size_t)(w + 2) * (size_t)h, sizeof(float));
    int c, i, x, y;
    for (c = 0; c < p->nc; c++) {
        int s = p->starts[c], e = c + 1 < p->nc ? p->starts[c + 1] : p->n;
        for (i = s; i < e; i++) {
            int j = i + 1 < e ? i + 1 : s;
            acc_line(a, w, h, p->xy[2 * i], p->xy[2 * i + 1], p->xy[2 * j], p->xy[2 * j + 1]);
        }
    }
    for (y = 0; y < h; y++) {
        float sum = 0.f, *row = a + (size_t)y * (size_t)(w + 2);
        for (x = 0; x < w; x++) {
            float v;
            sum += row[x];
            v = fabsf(sum);
            mask[(size_t)y * (size_t)w + (size_t)x] = v > 1.f ? 1.f : v;
        }
    }
    free(a);
}
void frui_path_fill(frui_canvas *c, const frui_path *p, uint32_t col)
{
    float minx = 1e9f, miny = 1e9f, maxx = -1e9f, maxy = -1e9f, *m;
    int i, x0, y0, w, h, x, y;
    frui_path q;
    if (p->n < 3) return;
    for (i = 0; i < p->n; i++) {
        float px = p->xy[2 * i], py = p->xy[2 * i + 1];
        if (px < minx) minx = px;
        if (px > maxx) maxx = px;
        if (py < miny) miny = py;
        if (py > maxy) maxy = py;
    }
    x0 = (int)floorf(minx) - 1; y0 = (int)floorf(miny) - 1;
    w = (int)ceilf(maxx) - x0 + 2; h = (int)ceilf(maxy) - y0 + 2;
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384) return;
    q = *p;
    q.xy = malloc(sizeof(float) * 2u * (size_t)p->n);
    for (i = 0; i < p->n; i++) { q.xy[2 * i] = p->xy[2 * i] - (float)x0; q.xy[2 * i + 1] = p->xy[2 * i + 1] - (float)y0; }
    m = calloc((size_t)w * (size_t)h, sizeof(float));
    frui_path_mask(&q, m, w, h);
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++) frui_blend(c, x0 + x, y0 + y, col, m[(size_t)y * (size_t)w + (size_t)x]);
    free(m);
    free(q.xy);
}

/* ---- materials */
static float hashf(uint32_t v)                 /* -1 .. 1 */
{
    v ^= v >> 16; v *= 0x7feb352du; v ^= v >> 15; v *= 0x846ca68bu; v ^= v >> 16;
    return (float)(v & 0xFFFFFFu) / (float)0x800000 - 1.f;
}
static float vnoise(float x, uint32_t row)     /* smooth 1-D value noise along x, one sequence per row */
{
    float fx = floorf(x), t = x - fx, a, b;
    uint32_t i = (uint32_t)(int32_t)fx;
    a = hashf(i * 0x9E3779B1u + row * 0x85EBCA77u);
    b = hashf((i + 1u) * 0x9E3779B1u + row * 0x85EBCA77u);
    t = t * t * (3.f - 2.f * t);
    return a + (b - a) * t;
}
static uint32_t scale_rgb(uint32_t c, float k)
{
    float r = (float)((c >> 16) & 255u) * k, g = (float)((c >> 8) & 255u) * k, b = (float)(c & 255u) * k;
    r = clampf(r, 0.f, 255.f); g = clampf(g, 0.f, 255.f); b = clampf(b, 0.f, 255.f);
    return FRUI_RGB((uint32_t)(r + 0.5f), (uint32_t)(g + 0.5f), (uint32_t)(b + 0.5f));
}
typedef struct {
    float x0, y0, w, h, grain, sc;
    uint32_t top, bot, seed;
    /* the values that change only per row or per column, computed once (the same arithmetic: the same pixels) */
    float ly;                      /* the row cached (NAN: none) */
    uint32_t row, base;
    float hr, hx, s;
    int cx0, ncol;                 /* the columns cached: pixel centres cx0 + 0.5 .. */
    float *sh;
} brush_t;
static float brush_sheen(const brush_t *b, float x)
{
    float xn = (x - b->x0) / b->w;
    return 0.055f * expf(-((xn - 0.3f) * (xn - 0.3f)) / 0.05f) + 0.03f * expf(-((xn - 0.8f) * (xn - 0.8f)) / 0.03f) - 0.02f;
}
static uint32_t col_brushed(const void *u, float x, float y)
{
    brush_t *b = (brush_t *)(uintptr_t)u;
    float g, sh;
    int ix;
    if (!(y == b->ly)) {
        b->ly = y;
        b->row = (uint32_t)(int32_t)floorf(y) + b->seed * 7919u;
        b->base = frui_mix(b->top, b->bot, (y - b->y0) / b->h);
        b->hr = hashf(b->row);
        b->hx = 13.7f * hashf(b->row ^ 0xA5A5u);
        b->s = hashf(b->row ^ 0x5bd1e995u);
    }
    g = 0.55f * b->hr + 0.45f * vnoise(x / (36.f * b->sc) + b->hx, b->row);
    if (b->s > 0.86f) g += 1.6f * (b->s - 0.86f) / 0.14f * (0.5f + 0.5f * vnoise(x / (140.f * b->sc), b->row ^ 77u));
    if (b->s < -0.9f) g -= 1.2f * (-0.9f - b->s) / 0.1f * (0.5f + 0.5f * vnoise(x / (120.f * b->sc), b->row ^ 91u));
    ix = (int)floorf(x) - b->cx0;
    sh = b->sh && ix >= 0 && ix < b->ncol && x == (float)(ix + b->cx0) + 0.5f ? b->sh[ix] : brush_sheen(b, x);
    return scale_rgb(b->base, 1.f + b->grain * g + sh);
}
void frui_brushed(frui_canvas *c, float x, float y, float w, float h, float r, uint32_t top, uint32_t bot, float grain,
                  float sc, uint32_t seed)
{
    rr_t s = mk_rr(x, y, w, h, r);
    brush_t b = {x, y, w, h, grain, sc, top, bot, seed, NAN, 0, 0, 0.f, 0.f, 0.f, 0, 0, 0};
    int x0 = (int)floorf(x) - 1, x1 = (int)ceilf(x + w) + 1, i;
    if (x0 < c->cx0) x0 = c->cx0;
    if (x1 > c->cx1) x1 = c->cx1;
    if (x1 > x0 && (b.sh = malloc(sizeof(float) * (size_t)(x1 - x0)))) {
        b.cx0 = x0; b.ncol = x1 - x0;
        for (i = 0; i < b.ncol; i++) b.sh[i] = brush_sheen(&b, (float)(x0 + i) + 0.5f);
    }
    shade(c, x, y, x + w, y + h, sd_rrect, &s, 0.f, 0.f, 0, col_brushed, &b);
    free(b.sh);
}
typedef struct { float cx, cy, r, grain, sc; uint32_t lo, hi, seed; } spun_t;
static uint32_t col_spun(const void *u, float x, float y)
{
    const spun_t *p = u;
    float dx = x - p->cx, dy = p->cy - y, rho = sqrtf(dx * dx + dy * dy), th = atan2f(dy, dx), k, g;
    uint32_t ring = (uint32_t)(int32_t)floorf(rho * 2.f / (p->sc > 1.f ? p->sc : 1.f)) + p->seed * 131u;
    k = 0.5f + 0.5f * cosf(2.f * (th - 2.35f));          /* brightest towards the upper left and the lower right */
    k = k * k * (3.f - 2.f * k);
    k = 0.15f + 0.85f * k * (0.75f + 0.25f * cosf(th - 2.35f));
    g = 0.6f * hashf(ring) + 0.4f * vnoise(th * 6.f, ring);
    return scale_rgb(frui_mix(p->lo, p->hi, k), 1.f + p->grain * g);
}
void frui_spun(frui_canvas *c, float cx, float cy, float r, uint32_t lo, uint32_t hi, float grain, float sc, uint32_t seed)
{
    ci_t s = {cx, cy, r};
    spun_t p = {cx, cy, r, grain, sc, lo, hi, seed};
    shade(c, cx - r, cy - r, cx + r, cy + r, sd_circle, &s, 0.f, 0.f, 0, col_spun, &p);
}
