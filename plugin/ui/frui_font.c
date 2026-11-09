/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: a small TrueType reader and glyph rasteriser (frui_font.h). Written for the one font the plugin
 * bundles (Inter Tight, a variable TrueType font with a weight axis); the format follows the OpenType specification
 * (cmap 4 / 12, glyf, gvar with IUP, GPOS lookup type 2, possibly inside extension lookups). */
#include "frui_font.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define GC_N 8192                  /* glyph bitmap cache entries (open addressing) */
#define AC_N 2048                  /* advance cache entries */
#define KC_N 2048                  /* kerning cache entries */

typedef struct { uint32_t key; int32_t sz; int x0, y0, w, h; uint8_t *bm; } gcache_t;
typedef struct { uint32_t key; float adv; } acache_t;
typedef struct { uint32_t key; int16_t k; } kcache_t;

struct frui_font {
    const uint8_t *d;
    size_t len;
    uint32_t glyf, loca, hmtx, cmap, gvar, gpos, os2;
    int upem, locfmt, nglyphs, nhm, cmapfmt;
    float wmin, wdef, wmax;
    int gv_axes, gv_shared_n, gv_glyphs, gv_long;
    uint32_t gv_shared, gv_data;
    int cap;                       /* cap height, font units */
    gcache_t *gc;
    acache_t *ac;
    kcache_t *kc;
};

static uint32_t u16(const uint8_t *p) { return (uint32_t)p[0] << 8 | p[1]; }
static int32_t s16(const uint8_t *p) { return (int16_t)(uint16_t)(p[0] << 8 | p[1]); }
static uint32_t u32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

frui_font *frui_font_load(const uint8_t *d, size_t len)
{
    frui_font *f;
    uint32_t n, i, head = 0, maxp = 0, hhea = 0, fvar = 0;
    if (len < 12) return 0;
    f = calloc(1, sizeof *f);
    f->d = d; f->len = len;
    n = u16(d + 4);
    for (i = 0; i < n; i++) {
        const uint8_t *t = d + 12 + 16 * i;
        uint32_t off = u32(t + 8);
        if (!memcmp(t, "glyf", 4)) f->glyf = off;
        else if (!memcmp(t, "loca", 4)) f->loca = off;
        else if (!memcmp(t, "hmtx", 4)) f->hmtx = off;
        else if (!memcmp(t, "cmap", 4)) f->cmap = off;
        else if (!memcmp(t, "gvar", 4)) f->gvar = off;
        else if (!memcmp(t, "GPOS", 4)) f->gpos = off;
        else if (!memcmp(t, "OS/2", 4)) f->os2 = off;
        else if (!memcmp(t, "head", 4)) head = off;
        else if (!memcmp(t, "maxp", 4)) maxp = off;
        else if (!memcmp(t, "hhea", 4)) hhea = off;
        else if (!memcmp(t, "fvar", 4)) fvar = off;
    }
    if (!f->glyf || !f->loca || !f->hmtx || !f->cmap || !head || !maxp || !hhea) { free(f); return 0; }
    f->upem = (int)u16(d + head + 18);
    f->locfmt = s16(d + head + 50);
    f->nglyphs = (int)u16(d + maxp + 4);
    f->nhm = (int)u16(d + hhea + 34);
    {   /* cmap: prefer format 12 (3, 10), else format 4 (3, 1) */
        uint32_t nt = u16(d + f->cmap + 2), best = 0;
        int bf = 0;
        for (i = 0; i < nt; i++) {
            const uint8_t *r = d + f->cmap + 4 + 8 * i;
            uint32_t pid = u16(r), eid = u16(r + 2), so = f->cmap + u32(r + 4), fmt = u16(d + so);
            if ((pid == 3 || pid == 0) && fmt == 12) { best = so; bf = 12; }
            else if ((pid == 3 && eid == 1) && fmt == 4 && bf != 12) { best = so; bf = 4; }
        }
        f->cmap = best; f->cmapfmt = bf;
    }
    f->wmin = f->wdef = f->wmax = 400.f;
    if (fvar) {
        const uint8_t *a = d + fvar + u16(d + fvar + 4);
        if (!memcmp(a, "wght", 4)) {
            f->wmin = (float)s16(a + 4) + (float)u16(a + 6) / 65536.f;
            f->wdef = (float)s16(a + 8) + (float)u16(a + 10) / 65536.f;
            f->wmax = (float)s16(a + 12) + (float)u16(a + 14) / 65536.f;
        }
    }
    if (f->gvar) {
        const uint8_t *g = d + f->gvar;
        f->gv_axes = (int)u16(g + 4);
        f->gv_shared_n = (int)u16(g + 6);
        f->gv_shared = f->gvar + u32(g + 8);
        f->gv_glyphs = (int)u16(g + 12);
        f->gv_long = (int)(u16(g + 14) & 1u);
        f->gv_data = f->gvar + u32(g + 16);
        if (f->gv_axes != 1) f->gvar = 0;           /* only the weight axis is read */
    }
    f->cap = f->os2 && u16(d + f->os2) >= 2 ? s16(d + f->os2 + 88) : f->upem * 7 / 10;
    f->gc = calloc(GC_N, sizeof(gcache_t));
    f->ac = calloc(AC_N, sizeof(acache_t));
    f->kc = calloc(KC_N, sizeof(kcache_t));
    return f;
}
void frui_font_free(frui_font *f)
{
    int i;
    if (!f) return;
    for (i = 0; i < GC_N; i++) free(f->gc[i].bm);
    free(f->gc); free(f->ac); free(f->kc); free(f);
}

static int glyph_of(const frui_font *f, uint32_t cp)
{
    const uint8_t *t = f->d + f->cmap;
    if (f->cmapfmt == 12) {
        uint32_t n = u32(t + 12), lo = 0, hi = n;
        while (lo < hi) {
            uint32_t m = (lo + hi) / 2;
            const uint8_t *g = t + 16 + 12 * m;
            if (cp < u32(g)) hi = m;
            else if (cp > u32(g + 4)) lo = m + 1;
            else return (int)(u32(g + 8) + cp - u32(g));
        }
        return 0;
    }
    if (f->cmapfmt == 4) {
        uint32_t segx2 = u16(t + 6), i;
        const uint8_t *ends = t + 14, *starts = ends + segx2 + 2, *deltas = starts + segx2, *ros = deltas + segx2;
        if (cp > 0xFFFF) return 0;
        for (i = 0; i < segx2; i += 2) {
            uint32_t e = u16(ends + i), s = u16(starts + i), ro = u16(ros + i);
            if (cp > e) continue;
            if (cp < s) return 0;
            if (!ro) return (int)((cp + u16(deltas + i)) & 0xFFFFu);
            {
                uint32_t g = u16(ros + i + ro + 2 * (cp - s));
                return g ? (int)((g + u16(deltas + i)) & 0xFFFFu) : 0;
            }
        }
    }
    return 0;
}

static uint32_t glyph_off(const frui_font *f, int g, uint32_t *len)
{
    uint32_t a, b;
    if (g < 0 || g >= f->nglyphs) { *len = 0; return 0; }
    if (f->locfmt) { a = u32(f->d + f->loca + 4 * g); b = u32(f->d + f->loca + 4 * g + 4); }
    else { a = 2 * u16(f->d + f->loca + 2 * g); b = 2 * u16(f->d + f->loca + 2 * g + 2); }
    *len = b - a;
    return f->glyf + a;
}
static void hmetrics(const frui_font *f, int g, int *adv, int *lsb)
{
    const uint8_t *h = f->d + f->hmtx;
    if (g < f->nhm) { *adv = (int)u16(h + 4 * g); *lsb = s16(h + 4 * g + 2); }
    else { *adv = (int)u16(h + 4 * (f->nhm - 1)); *lsb = s16(h + 4 * f->nhm + 2 * (g - f->nhm)); }
}

/* ---- gvar: the deltas of glyph g at the normalised weight nc, for np points (contour points + 4 phantom); simple
 * glyphs get IUP over their contours (ends[], nends) from the default coordinates ox / oy */
static const uint8_t *packed_points(const uint8_t *q, uint16_t *pts, int *n, int maxn)
{
    int cnt = q[0], got = 0;
    uint32_t last = 0;
    q++;
    if (cnt & 0x80) { cnt = ((cnt & 0x7F) << 8) | q[0]; q++; }
    if (!cnt) { *n = -1; return q; }                  /* all points */
    while (got < cnt) {
        int c = *q++, run = (c & 0x7F) + 1, j;
        for (j = 0; j < run && got < cnt; j++) {
            if (c & 0x80) { last += u16(q); q += 2; } else last += *q++;
            if (got < maxn) pts[got] = (uint16_t)last;
            got++;
        }
    }
    *n = cnt < maxn ? cnt : maxn;
    return q;
}
static const uint8_t *packed_deltas(const uint8_t *q, float *out, int cnt)
{
    int got = 0;
    while (got < cnt) {
        int c = *q++, run = (c & 0x3F) + 1, j;
        for (j = 0; j < run && got < cnt; j++) {
            if (c & 0x80) out[got] = 0.f;
            else if (c & 0x40) { out[got] = (float)s16(q); q += 2; }
            else { out[got] = (float)(int8_t)*q++; }
            got++;
        }
    }
    return q;
}
static float iup1(float c, float c1, float d1, float c2, float d2)
{
    if (c1 == c2) return d1 == d2 ? d1 : 0.f;
    if (c1 > c2) { float t = c1; c1 = c2; c2 = t; t = d1; d1 = d2; d2 = t; }
    if (c <= c1) return d1;
    if (c >= c2) return d2;
    return d1 + (c - c1) * (d2 - d1) / (c2 - c1);
}
static void iup(const float *o, float *d, const uint8_t *touched, int s, int e)
{
    int i, first = -1, prev, cnt = 0;
    for (i = s; i <= e; i++) if (touched[i]) { cnt++; if (first < 0) first = i; }
    if (!cnt) return;
    if (cnt == 1) { for (i = s; i <= e; i++) if (!touched[i]) d[i] = d[first]; return; }
    prev = first;
    for (i = 1; i <= e - s + 1; i++) {
        int k = s + (first - s + i) % (e - s + 1);
        if (touched[k]) {
            int j = s + (prev - s + 1) % (e - s + 1);
            while (j != k) { d[j] = iup1(o[j], o[prev], d[prev], o[k], d[k]); j = s + (j - s + 1) % (e - s + 1); }
            prev = k;
        }
    }
}
static void gvar_apply(const frui_font *f, int g, float nc, int np, const float *ox, const float *oy, float *x,
                       float *y, const int *ends, int nends)
{
    const uint8_t *gv, *hdr, *ser;
    uint32_t a, b;
    int count, i, nshared = -2;
    uint16_t shared[512], priv[512];
    float *tx, *ty;
    uint8_t *touched;
    if (!f->gvar || g >= f->gv_glyphs || nc == 0.f) return;
    gv = f->d + f->gvar + 20;
    if (f->gv_long) { a = u32(gv + 4 * g); b = u32(gv + 4 * g + 4); }
    else { a = 2 * u16(gv + 2 * g); b = 2 * u16(gv + 2 * g + 2); }
    if (a == b) return;
    hdr = f->d + f->gv_data + a;
    count = (int)(u16(hdr) & 0x0FFFu);
    ser = hdr + u16(hdr + 2);
    if (u16(hdr) & 0x8000u) ser = packed_points(ser, shared, &nshared, 512);
    hdr += 4;
    tx = calloc((size_t)np * 2, sizeof(float)); ty = tx + np;
    touched = calloc((size_t)np, 1);
    for (i = 0; i < count; i++) {
        uint32_t size = u16(hdr), ti = u16(hdr + 2);
        float peak, st, en, sc;
        const uint8_t *q = ser;
        int npts, k, n;
        uint16_t *pl;
        hdr += 4;
        if (ti & 0x8000u) { peak = (float)s16(hdr) / 16384.f; hdr += 2; }
        else peak = (float)s16(f->d + f->gv_shared + 2 * (ti & 0x0FFFu)) / 16384.f;
        if (ti & 0x4000u) { st = (float)s16(hdr) / 16384.f; en = (float)s16(hdr + 2) / 16384.f; hdr += 4; }
        else { st = peak < 0.f ? peak : 0.f; en = peak > 0.f ? peak : 0.f; }
        ser += size;
        if (peak == 0.f) sc = 1.f;
        else if (nc < st || nc > en) sc = 0.f;
        else if (nc == peak) sc = 1.f;
        else if (nc < peak) sc = (nc - st) / (peak - st);
        else sc = (en - nc) / (en - peak);
        if (sc == 0.f) continue;
        if (ti & 0x2000u) { q = packed_points(q, priv, &npts, 512); pl = priv; }
        else { npts = nshared; pl = shared; }
        n = npts < 0 ? np : npts;
        {
            float *dx = calloc((size_t)n * 2, sizeof(float)), *dy = dx + n;
            q = packed_deltas(q, dx, n);
            packed_deltas(q, dy, n);
            if (npts < 0) for (k = 0; k < np; k++) { x[k] += sc * dx[k]; y[k] += sc * dy[k]; }
            else {
                memset(tx, 0, sizeof(float) * (size_t)np * 2);
                memset(touched, 0, (size_t)np);
                for (k = 0; k < n; k++) if (pl[k] < np) { tx[pl[k]] = dx[k]; ty[pl[k]] = dy[k]; touched[pl[k]] = 1; }
                if (ends) {
                    int c, s = 0;
                    for (c = 0; c < nends; c++) { iup(ox, tx, touched, s, ends[c]); iup(oy, ty, touched, s, ends[c]); s = ends[c] + 1; }
                }
                for (k = 0; k < np; k++) { x[k] += sc * tx[k]; y[k] += sc * ty[k]; }
            }
            free(dx);
        }
    }
    free(tx); free(touched);
}

/* ---- outlines: points in font units (y up), on-curve flags, contour ends; composite glyphs flattened */
typedef struct { float *x, *y; uint8_t *on; int n, cap; int *ends; int ne, ecap; } outl_t;
static void ol_pt(outl_t *o, float x, float y, int on)
{
    if (o->n == o->cap) {
        o->cap = o->cap ? o->cap * 2 : 128;
        o->x = realloc(o->x, sizeof(float) * (size_t)o->cap); o->y = realloc(o->y, sizeof(float) * (size_t)o->cap);
        o->on = realloc(o->on, (size_t)o->cap);
    }
    o->x[o->n] = x; o->y[o->n] = y; o->on[o->n] = (uint8_t)on; o->n++;
}
static void ol_end(outl_t *o)
{
    if (o->ne == o->ecap) { o->ecap = o->ecap ? o->ecap * 2 : 16; o->ends = realloc(o->ends, sizeof(int) * (size_t)o->ecap); }
    o->ends[o->ne++] = o->n - 1;
}
static void ol_free(outl_t *o) { free(o->x); free(o->y); free(o->on); free(o->ends); }

static float norm_w(const frui_font *f, float w)
{
    if (w < f->wmin) w = f->wmin;
    if (w > f->wmax) w = f->wmax;
    if (w < f->wdef) return (w - f->wdef) / (f->wdef - f->wmin);
    if (w > f->wdef) return (w - f->wdef) / (f->wmax - f->wdef);
    return 0.f;
}
/* appends glyph g (offset by dx, dy) to o (o may be 0: metrics only); returns the advance in font units */
static float load_glyph(const frui_font *f, int g, float nc, outl_t *o, float dx, float dy, int depth)
{
    uint32_t len, off = glyph_off(f, g, &len);
    int adv, lsb, nco;
    const uint8_t *p;
    hmetrics(f, g, &adv, &lsb);
    if (!len) {                                       /* an empty glyph (the space): the phantom points only */
        float ox[4] = {0, (float)adv, 0, 0}, oy[4] = {0, 0, 0, 0}, x[4], y[4];
        memcpy(x, ox, sizeof x); memcpy(y, oy, sizeof y);
        gvar_apply(f, g, nc, 4, ox, oy, x, y, 0, 0);
        return x[1] - x[0];
    }
    p = f->d + off;
    nco = s16(p);
    if (nco >= 0) {
        const uint8_t *ep = p + 10, *fl;
        int n = nco ? (int)u16(ep + 2 * (nco - 1)) + 1 : 0, i, k, np = n + 4;
        float *ox = malloc(sizeof(float) * 4u * (size_t)np), *oy = ox + np, *x = oy + np, *y = x + np;
        uint8_t *flags = malloc((size_t)n + 1);
        int *ends = malloc(sizeof(int) * (size_t)(nco + 1));
        int32_t v;
        float xmin = (float)s16(p + 2);
        for (i = 0; i < nco; i++) ends[i] = (int)u16(ep + 2 * i);
        fl = ep + 2 * nco;
        fl += 2 + u16(fl);                            /* the instructions */
        for (i = 0; i < n;) {
            uint8_t c = *fl++;
            flags[i++] = c;
            if (c & 8) { int r = *fl++; while (r-- && i < n) flags[i++] = c; }
        }
        for (v = 0, i = 0; i < n; i++) {
            uint8_t c = flags[i];
            if (c & 2) { v += (c & 16) ? *fl : -(int32_t)*fl; fl++; }
            else if (!(c & 16)) { v += s16(fl); fl += 2; }
            ox[i] = (float)v;
        }
        for (v = 0, i = 0; i < n; i++) {
            uint8_t c = flags[i];
            if (c & 4) { v += (c & 32) ? *fl : -(int32_t)*fl; fl++; }
            else if (!(c & 32)) { v += s16(fl); fl += 2; }
            oy[i] = (float)v;
        }
        ox[n] = xmin - (float)lsb; oy[n] = 0.f;
        ox[n + 1] = ox[n] + (float)adv; oy[n + 1] = 0.f;
        ox[n + 2] = ox[n + 3] = oy[n + 2] = oy[n + 3] = 0.f;
        memcpy(x, ox, sizeof(float) * (size_t)np); memcpy(y, oy, sizeof(float) * (size_t)np);
        gvar_apply(f, g, nc, np, ox, oy, x, y, ends, nco);
        if (o) {
            for (k = 0, i = 0; i < n; i++) {
                ol_pt(o, x[i] - x[n] + dx, y[i] + dy, flags[i] & 1);
                if (k < nco && i == ends[k]) { ol_end(o); k++; }
            }
        }
        {
            float a = x[n + 1] - x[n];
            free(ox); free(flags); free(ends);
            return a;
        }
    }
    {   /* composite: the components' offsets are the points gvar moves (no IUP) */
        const uint8_t *c = p + 10;
        int nc2 = 0, i, more;
        int gids[64];
        float cx[64 + 4], cy[64 + 4], ox[64 + 4], oy[64 + 4];
        int useme = -1;
        do {
            uint32_t fl = u16(c);
            int gi = (int)u16(c + 2);
            float a1, a2;
            c += 4;
            if (fl & 1u) { a1 = (float)s16(c); a2 = (float)s16(c + 2); c += 4; }
            else { a1 = (float)(int8_t)c[0]; a2 = (float)(int8_t)c[1]; c += 2; }
            if (!(fl & 2u)) a1 = a2 = 0.f;            /* point-matched components: not in this font's Latin */
            if (fl & 8u) c += 2;                      /* scales: not used by the glyphs drawn here */
            else if (fl & 0x40u) c += 4;
            else if (fl & 0x80u) c += 8;
            if (fl & 0x200u) useme = nc2;
            more = (int)(fl & 0x20u);
            if (nc2 < 64) { gids[nc2] = gi; ox[nc2] = a1; oy[nc2] = a2; nc2++; }
        } while (more);
        ox[nc2] = (float)s16(p + 2) - (float)lsb; oy[nc2] = 0.f;
        ox[nc2 + 1] = ox[nc2] + (float)adv; oy[nc2 + 1] = 0.f;
        ox[nc2 + 2] = ox[nc2 + 3] = oy[nc2 + 2] = oy[nc2 + 3] = 0.f;
        memcpy(cx, ox, sizeof cx); memcpy(cy, oy, sizeof cy);
        gvar_apply(f, g, nc, nc2 + 4, ox, oy, cx, cy, 0, 0);
        {
            float a = cx[nc2 + 1] - cx[nc2];
            for (i = 0; i < nc2; i++) {
                float ca = depth < 4 ? load_glyph(f, gids[i], nc, o, dx + cx[i] - cx[nc2], dy + cy[i], depth + 1) : 0.f;
                if (i == useme) a = ca;
            }
            return a;
        }
    }
}

/* ---- GPOS pair kerning (lookup type 2, also inside type 9 extensions); font units */
static int cov_index(const uint8_t *c, int g)
{
    uint32_t fmt = u16(c), n = u16(c + 2), lo = 0, hi = n;
    if (fmt == 1) {
        while (lo < hi) {
            uint32_t m = (lo + hi) / 2, v = u16(c + 4 + 2 * m);
            if ((uint32_t)g < v) hi = m; else if ((uint32_t)g > v) lo = m + 1; else return (int)m;
        }
    } else if (fmt == 2) {
        while (lo < hi) {
            uint32_t m = (lo + hi) / 2;
            const uint8_t *r = c + 4 + 6 * m;
            if ((uint32_t)g < u16(r)) hi = m; else if ((uint32_t)g > u16(r + 2)) lo = m + 1;
            else return (int)(u16(r + 4) + (uint32_t)g - u16(r));
        }
    }
    return -1;
}
static int class_of(const uint8_t *c, int g)
{
    uint32_t fmt = u16(c);
    if (fmt == 1) {
        uint32_t s = u16(c + 2), n = u16(c + 4);
        return (uint32_t)g >= s && (uint32_t)g < s + n ? (int)u16(c + 6 + 2 * ((uint32_t)g - s)) : 0;
    }
    if (fmt == 2) {
        uint32_t n = u16(c + 2), lo = 0, hi = n;
        while (lo < hi) {
            uint32_t m = (lo + hi) / 2;
            const uint8_t *r = c + 4 + 6 * m;
            if ((uint32_t)g < u16(r)) hi = m; else if ((uint32_t)g > u16(r + 2)) lo = m + 1; else return (int)u16(r + 4);
        }
    }
    return 0;
}
static int popc(uint32_t v) { int n = 0; while (v) { n += (int)(v & 1u); v >>= 1; } return n; }
static int pair_adj(const uint8_t *s, int g1, int g2, int *val)
{
    uint32_t fmt = u16(s), vf1 = u16(s + 4), vf2 = u16(s + 6);
    int ci = cov_index(s + u16(s + 2), g1), s1 = 2 * popc(vf1), s2 = 2 * popc(vf2), xo = 2 * popc(vf1 & 3u);
    if (ci < 0) return 0;
    if (fmt == 1) {
        const uint8_t *ps = s + u16(s + 10 + 2 * ci);
        uint32_t n = u16(ps), lo = 0, hi = n, rs = 2u + (uint32_t)(s1 + s2);
        while (lo < hi) {
            uint32_t m = (lo + hi) / 2, sg = u16(ps + 2 + rs * m);
            if ((uint32_t)g2 < sg) hi = m; else if ((uint32_t)g2 > sg) lo = m + 1;
            else { *val = (vf1 & 4u) ? s16(ps + 2 + rs * m + 2 + xo) : 0; return 1; }
        }
        return 0;
    }
    if (fmt == 2) {
        int c1 = class_of(s + u16(s + 8), g1), c2 = class_of(s + u16(s + 10), g2);
        uint32_t n2 = u16(s + 14);
        const uint8_t *r = s + 16 + ((uint32_t)c1 * n2 + (uint32_t)c2) * (uint32_t)(s1 + s2);
        *val = (vf1 & 4u) ? s16(r + xo) : 0;
        return 1;
    }
    return 0;
}
static int kern_calc(const frui_font *f, int g1, int g2)
{
    const uint8_t *gp, *ll;
    uint32_t n, i, total = 0;
    if (!f->gpos) return 0;
    gp = f->d + f->gpos;
    ll = gp + u16(gp + 8);
    n = u16(ll);
    for (i = 0; i < n; i++) {
        const uint8_t *lk = ll + u16(ll + 2 + 2 * i);
        uint32_t type = u16(lk), ns = u16(lk + 4), j;
        for (j = 0; j < ns; j++) {
            const uint8_t *s = lk + u16(lk + 6 + 2 * j);
            uint32_t t = type;
            int v;
            if (t == 9) { t = u16(s + 2); s += u32(s + 4); }
            if (t != 2) break;
            if (pair_adj(s, g1, g2, &v)) { total += (uint32_t)v; break; }
        }
    }
    return (int)total;
}
static int kern(frui_font *f, int g1, int g2)
{
    uint32_t key = ((uint32_t)g1 << 16 | (uint32_t)g2) + 1u, h = (key * 2654435761u) >> 21;
    kcache_t *e = &f->kc[h % KC_N];
    if (e->key != key) { e->key = key; e->k = (int16_t)kern_calc(f, g1, g2); }
    return e->k;
}

static float advance(frui_font *f, int g, float weight)
{
    uint32_t key = ((uint32_t)g << 12 | (uint32_t)(weight + 0.5f)) + 1u, h = (key * 2654435761u) >> 20;
    int i;
    for (i = 0; i < AC_N; i++) {
        acache_t *e = &f->ac[(h + (uint32_t)i) % AC_N];
        if (e->key == key) return e->adv;
        if (!e->key) { e->key = key; e->adv = load_glyph(f, g, norm_w(f, weight), 0, 0.f, 0.f, 0); return e->adv; }
    }
    return load_glyph(f, g, norm_w(f, weight), 0, 0.f, 0.f, 0);
}

/* a glyph's bitmap at a size (pixels per em, 1/16 steps), weight and quarter-pixel x phase */
static gcache_t *glyph_bm(frui_font *f, int g, float size, float weight, int sub)
{
    int32_t sz = (int32_t)(size * 16.f + 0.5f);
    uint32_t key = ((uint32_t)g << 14 | (uint32_t)(weight + 0.5f) << 2 | (uint32_t)sub) + 1u;
    uint32_t h = (key * 2654435761u ^ (uint32_t)sz * 40503u) % GC_N;
    int i;
    gcache_t *e = 0;
    for (i = 0; i < GC_N; i++) {
        gcache_t *c = &f->gc[(h + (uint32_t)i) % GC_N];
        if (c->key == key && c->sz == sz) return c;
        if (!c->key) { e = c; break; }
    }
    if (!e) {                                         /* full: start again (the UI uses a few hundred) */
        for (i = 0; i < GC_N; i++) { free(f->gc[i].bm); f->gc[i].bm = 0; f->gc[i].key = 0; }
        e = &f->gc[h];
    }
    e->key = key; e->sz = sz; e->bm = 0; e->w = e->h = 0;
    {
        outl_t o;
        float sc = (float)sz / 16.f / (float)f->upem, minx = 1e9f, miny = 1e9f, maxx = -1e9f, maxy = -1e9f;
        float phase = (float)sub * 0.25f;
        int c, s;
        memset(&o, 0, sizeof o);
        load_glyph(f, g, norm_w(f, weight), &o, 0.f, 0.f, 0);
        if (o.n) {
            frui_path p;
            float *mask;
            int k;
            for (k = 0; k < o.n; k++) {
                o.x[k] = o.x[k] * sc + phase; o.y[k] = -o.y[k] * sc;
                if (o.x[k] < minx) minx = o.x[k];
                if (o.x[k] > maxx) maxx = o.x[k];
                if (o.y[k] < miny) miny = o.y[k];
                if (o.y[k] > maxy) maxy = o.y[k];
            }
            e->x0 = (int)floorf(minx) - 1; e->y0 = (int)floorf(miny) - 1;
            e->w = (int)ceilf(maxx) - e->x0 + 2; e->h = (int)ceilf(maxy) - e->y0 + 2;
            frui_path_init(&p);
            for (c = 0, s = 0; c < o.ne; c++) {
                int en = o.ends[c], np = en - s + 1, j, st;
                float sx, sy;
                if (np < 2) { s = en + 1; continue; }
#define PX(i) (o.x[s + ((i) % np)] - (float)e->x0)
#define PY(i) (o.y[s + ((i) % np)] - (float)e->y0)
#define ON(i) (o.on[s + ((i) % np)])
                for (st = 0; st < np && !ON(st); st++) {}
                if (st == np) { sx = 0.5f * (PX(0) + PX(1)); sy = 0.5f * (PY(0) + PY(1)); st = 0; }
                else { sx = PX(st); sy = PY(st); }
                frui_path_move(&p, sx, sy);
                for (j = 1; j <= np; j++) {
                    int i0 = st + j;
                    if (ON(i0)) frui_path_line(&p, PX(i0), PY(i0));
                    else {
                        float ex, ey;
                        if (ON(i0 + 1)) { ex = PX(i0 + 1); ey = PY(i0 + 1); j++; }
                        else { ex = 0.5f * (PX(i0) + PX(i0 + 1)); ey = 0.5f * (PY(i0) + PY(i0 + 1)); }
                        frui_path_quad(&p, PX(i0), PY(i0), ex, ey);
                    }
                }
#undef PX
#undef PY
#undef ON
                s = en + 1;
            }
            mask = calloc((size_t)e->w * (size_t)e->h, sizeof(float));
            frui_path_mask(&p, mask, e->w, e->h);
            e->bm = malloc((size_t)e->w * (size_t)e->h);
            for (k = 0; k < e->w * e->h; k++) e->bm[k] = (uint8_t)(mask[k] * 255.f + 0.5f);
            free(mask);
            frui_path_free(&p);
        }
        ol_free(&o);
    }
    return e;
}

static int next_cp(const char **s)                   /* UTF-8 */
{
    const uint8_t *p = (const uint8_t *)*s;
    int c = *p++;
    if (c >= 0xF0) { c = (c & 7) << 18 | (p[0] & 63) << 12 | (p[1] & 63) << 6 | (p[2] & 63); p += 3; }
    else if (c >= 0xE0) { c = (c & 15) << 12 | (p[0] & 63) << 6 | (p[1] & 63); p += 2; }
    else if (c >= 0xC0) { c = (c & 31) << 6 | (p[0] & 63); p += 1; }
    *s = (const char *)p;
    return c;
}
static float tnum_cell(frui_font *f, float w)
{
    float m = 0.f;
    int d;
    for (d = '0'; d <= '9'; d++) { float a = advance(f, glyph_of(f, (uint32_t)d), w); if (a > m) m = a; }
    return m;
}
static float run(frui_canvas *c, frui_font *f, const frui_style *st, float x, float y, const char *s, uint32_t col)
{
    float sc = st->size / (float)f->upem, x0 = x, cell = st->flags & FRUI_TNUM ? tnum_cell(f, st->weight) : 0.f;
    int prev = -1;
    float yb = floorf(y + 0.5f);
    while (*s) {
        int cp = next_cp(&s), g = glyph_of(f, (uint32_t)cp), isd = cp >= '0' && cp <= '9' && cell > 0.f;
        float a = advance(f, g, st->weight), off = 0.f;
        if (prev >= 0 && !isd) x += (float)kern(f, prev, g) * sc;
        if (isd) { off = (cell - a) * 0.5f * sc; a = cell; }
        if (c) {
            float gx = x + off, fx = floorf(gx);
            int sub = (int)((gx - fx) * 4.f + 0.5f), ix = (int)fx, i, j;
            gcache_t *e;
            if (sub == 4) { sub = 0; ix++; }
            e = glyph_bm(f, g, st->size, st->weight, sub);
            if (e->bm)
                for (j = 0; j < e->h; j++)
                    for (i = 0; i < e->w; i++) {
                        uint8_t v = e->bm[j * e->w + i];
                        if (v) frui_blend(c, ix + e->x0 + i, (int)yb + e->y0 + j, col, (float)v / 255.f);
                    }
        }
        x += a * sc + st->track;
        prev = isd ? -1 : g;
    }
    return x - x0 - (x > x0 ? st->track : 0.f);
}
float frui_text_width(frui_font *f, const frui_style *s, const char *str) { return run(0, f, s, 0.f, 0.f, str, 0); }
float frui_text(frui_canvas *c, frui_font *f, const frui_style *s, float x, float y, const char *str, uint32_t col)
{
    return run(c, f, s, x, y, str, col);
}
float frui_font_ascent(frui_font *f, float size) { return (float)f->cap * size / (float)f->upem; }
