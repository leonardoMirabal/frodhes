/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* Anti-aliased text (Felucca firmware/src/gfx.c, commit a53ecf4, adapted for Frodhes 0.9.3): 4-bit alpha glyphs made
 * at build time by tools/gen_aa_font.py (Inter Tight, SIL OFL 1.1, assets/fonts/OFL-InterTight.txt), drawn into the
 * canvas of gfx.c. A glyph is trimmed to its ink box, advances are in 1/16 px, each glyph is rasterised at 1 << psh
 * horizontal phases; blending uses a 16-entry ramp per (ink, background) pair, cached. Names carry an aa_ prefix so
 * they sit next to gfx.c's bitmap fonts (FONT_S / FONT_L, text_w, cv_text). Included by ui.c after gfx.c. */
typedef struct { uint16_t off, adv; int8_t bx; uint8_t by, bw, bh; } aag_t;
typedef struct {
    uint8_t h, asc, first, last, nex;   /* line height, baseline; ASCII range; extras */
    uint16_t nk;                        /* kerning pairs */
    const aag_t *g;
    const uint8_t *data;
    const uint16_t *kkey;               /* a << 8 | b, sorted */
    const int8_t *kd;                   /* 1/16 px */
    const uint8_t *ex;                  /* codes of the extra glyphs */
    uint8_t psh;                        /* log2 of the phases per glyph */
    const uint8_t *hc;                  /* data's Huffman code (aa_alpha_hc), 0 = 2 px per byte (aa_alpha) */
} aafont_t;
#include "aa_font.h"                    /* AF_S 13 px / 500 (all small text), AF_L 28 px / 600 (values:
                                         * gen_aa_font.py L_CHARS; aa_covers) */
#define AA_ELLIPSIS '\x85'

/* the blend of ink over background in 16 steps (a light-on-dark coverage curve), byte-swapped for the panel */
static const uint8_t AA_CURVE[16] = {0, 24, 43, 62, 80, 97, 114, 130, 147, 163, 178, 194, 210, 225, 240, 255};
/* aa_text_ov: the ink blended over each pixel the canvas already holds (a value over a row's fill and its edge), not
 * over one background colour */
static uint8_t aa_ov;
static uint16_t aa_ov_fg;
static uint16_t aa_ov_px(uint16_t d, uint32_t a)
{
    static const uint8_t SH[3] = {11, 5, 0}, MK[3] = {31, 63, 31};
    uint32_t c = swap16(d), out = 0, k;
    int32_t w = AA_CURVE[a];
    for (k = 0; k < 3u; k++) {
        int32_t x = (int32_t)((c >> SH[k]) & MK[k]), y = (aa_ov_fg >> SH[k]) & MK[k];
        out |= (uint32_t)(x + (((y - x) * w + 128) >> 8)) << SH[k];
    }
    return swap16(out);
}
#define AA_NRAMP 8u
static struct { uint16_t fg[AA_NRAMP], bg[AA_NRAMP], v[AA_NRAMP][16]; uint8_t next, n; } aa_rc;
static const uint16_t *aa_ramp(uint16_t fg, uint16_t bg)
{
    static const uint8_t SH[3] = {11, 5, 0}, MK[3] = {31, 63, 31};
    uint32_t i, a, k;
    uint16_t *v;
    for (i = 0; i < aa_rc.n; i++)
        if (aa_rc.fg[i] == fg && aa_rc.bg[i] == bg)
            return aa_rc.v[i];
    i = aa_rc.next;
    aa_rc.next = (uint8_t)((i + 1u) % AA_NRAMP);
    if (aa_rc.n < AA_NRAMP) aa_rc.n++;
    aa_rc.fg[i] = fg;
    aa_rc.bg[i] = bg;
    v = aa_rc.v[i];
    for (a = 0; a < 16u; a++) {
        uint32_t out = 0;
        int32_t w = AA_CURVE[a];
        for (k = 0; k < 3u; k++) {
            int32_t x = (bg >> SH[k]) & MK[k], y = (fg >> SH[k]) & MK[k];
            out |= (uint32_t)(x + (((y - x) * w + 128) >> 8)) << SH[k];
        }
        v[a] = swap16(out);
    }
    return v;
}

static void aa_alpha(int32_t x, int32_t y, uint32_t w, uint32_t h, const uint8_t *d, const uint16_t *rv)
{
    uint32_t k = 0, gx, gy;
    y += cv_oy;
    for (gy = 0; gy < h; gy++) {
        int32_t py = y + (int32_t)gy;
        uint16_t *row;
        if (py < 0 || py >= (int32_t)cv_h) { k += w; continue; }
        row = cv_px + (uint32_t)py * cv_w;
        for (gx = 0; gx < w; gx++, k++) {
            uint32_t v = (d[k >> 1] >> ((k & 1u) ? 0 : 4)) & 15u;
            int32_t px = x + (int32_t)gx;
            if (v && (uint32_t)px < cv_w) row[px] = aa_ov ? aa_ov_px(row[px], v) : rv[v];
        }
    }
}

/* the same from a glyph's Huffman-coded bit stream (gen_aa_font.py huff_pack); a 256-entry table per font in RAM
 * (one: only L is Huffman-coded; another coded face would share the slot, rebuilt when the face changes) */
#define AA_HC_FONTS 1
static struct { const uint8_t *hc; uint16_t lut[256]; } aa_hc_tab[AA_HC_FONTS] POOL;
static const uint16_t *aa_hc_lut(const uint8_t *hc)
{
    uint32_t k, len, code = 0, sym = 15, i;
    for (k = 0; k < AA_HC_FONTS - 1u && aa_hc_tab[k].hc && aa_hc_tab[k].hc != hc; k++)
        ;
    if (aa_hc_tab[k].hc != hc) {
        aa_hc_tab[k].hc = hc;
        for (i = 0; i < 256u; i++) aa_hc_tab[k].lut[i] = 0;
        for (len = 1; len <= 8u; len++, code <<= 1)
            for (i = 0; i < hc[len - 1u]; i++, code++, sym++) {
                uint32_t a = code << (8u - len), n = 1u << (8u - len);
                while (n--) aa_hc_tab[k].lut[a + n] = (uint16_t)(hc[sym] | len << 8);
            }
    }
    return aa_hc_tab[k].lut;
}
static void aa_alpha_hc(int32_t x, int32_t y, uint32_t w, uint32_t h, const uint8_t *d, const uint8_t *hc,
                        const uint16_t *rv)
{
    const uint16_t *lut = aa_hc_lut(hc);
    uint32_t gy, v = 0, run = 0, acc = 0, nb = 0;
    y += cv_oy;
    for (gy = 0; gy < h; gy++) {
        int32_t py = y + (int32_t)gy;
        uint16_t *row = py < 0 || py >= (int32_t)cv_h ? 0 : cv_px + (uint32_t)py * cv_w;
        uint32_t gx = 0;
        while (gx < w) {
            uint32_t n;
            if (!run) {
                uint32_t e, s;
                while (nb < 16u) { acc = acc << 8 | *d++; nb += 8; }
                e = lut[(acc >> (nb - 8u)) & 255u];
                if (e) { nb -= e >> 8; s = e & 255u; }
                else {
                    uint32_t code = 0, first = 0, idx = 0, len = 0;
                    for (;;) {
                        code |= (acc >> --nb) & 1u;
                        if (code - first < hc[len]) break;
                        idx += hc[len];
                        first = (first + hc[len]) << 1;
                        code <<= 1;
                        len++;
                    }
                    s = hc[15u + idx + code - first];
                }
                v = s & 15u;
                run = (s >> 4) + 1u;
            }
            n = run < w - gx ? run : w - gx;
            if (v && row) {
                int32_t px = x + (int32_t)gx;
                uint32_t q;
                for (q = 0; q < n; q++, px++) if ((uint32_t)px < cv_w) row[px] = aa_ov ? aa_ov_px(row[px], v) : rv[v];
            }
            gx += n;
            run -= n;
        }
    }
}

static int32_t aa_glyph_at(const aafont_t *f, uint32_t ch)
{
    uint32_t i;
    if (ch >= f->first && ch <= f->last) return (int32_t)(ch - f->first);
    for (i = 0; i < f->nex; i++) if (f->ex[i] == ch) return (int32_t)(f->last - f->first + 1u + i);
    return -1;
}
static uint32_t aa_glyph(const aafont_t *f, uint32_t ch)
{
    int32_t k;
    if (ch >= 'a' && ch <= 'z' && f->last < 'a') ch -= 32u;           /* L has capitals only */
    if ((k = aa_glyph_at(f, ch)) < 0 && (ch != (uint8_t)AA_ELLIPSIS || (k = aa_glyph_at(f, '.')) < 0) &&
        (k = aa_glyph_at(f, '?')) < 0)
        k = 0;
    return (uint32_t)k << f->psh;
}
static int32_t aa_kern(const aafont_t *f, uint32_t a, uint32_t b)
{
    uint32_t key = (a << 8) | b, lo = 0, hi = f->nk;
    while (lo < hi) {
        uint32_t m = (lo + hi) / 2u;
        if (f->kkey[m] == key) return f->kd[m];
        if (f->kkey[m] < key) lo = m + 1u; else hi = m;
    }
    return 0;
}
static uint32_t aa_fold(const aafont_t *f, uint32_t c) { return c >= 'a' && c <= 'z' && f->last < 'a' ? c - 32u : c; }
/* 1 when the face has a glyph for every character of s (L is sparse: a value with another character is set in S) */
static int aa_covers(const aafont_t *f, const char *s)
{
    for (; *s; s++) if (aa_glyph_at(f, aa_fold(f, (uint8_t)*s)) < 0) return 0;
    return 1;
}

static int32_t aa_text_w(const aafont_t *f, const char *s)
{
    int32_t pen = 0;
    uint32_t prev = 0;
    for (; *s; s++) {
        uint32_t c = aa_fold(f, (uint8_t)*s);
        if (prev) pen += aa_kern(f, prev, c);
        pen += f->g[aa_glyph(f, c)].adv;
        prev = c;
    }
    return (pen + 8) >> 4;
}

/* y = the top of the line box (the baseline is y + f->asc); fg on bg (what lies under the text); the pen x at the end */
static int32_t aa_text(int32_t x, int32_t y, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{
    const uint16_t *rv = aa_ov ? 0 : aa_ramp(fg, bg);
    int32_t pen = 0, err = 0;
    const int32_t step = 16 >> f->psh;
    uint32_t prev = 0;
    aa_ov_fg = fg;
    for (; *s; s++) {
        uint32_t c = aa_fold(f, (uint8_t)*s);
        const aag_t *g;
        int32_t pos, d0, d1;
        if (prev) pen += aa_kern(f, prev, c);
        g = &f->g[aa_glyph(f, c)];
        if (f->psh && g[1].off != g->off) {
            pos = pen - (pen & (step - 1));
            d0 = pos - pen - err;
            d1 = d0 + step;
            if ((d0 < 0 ? -d0 : d0) > (d1 < 0 ? -d1 : d1)) pos += step;
            g += ((uint32_t)pos >> (4u - f->psh)) & ((1u << f->psh) - 1u);
        } else {
            pos = (pen + 8) & ~15;
        }
        err = pos - pen;
        if (g->bw) {
            int32_t gx = x + (pos >> 4) + g->bx, gy = y + g->by;
            if (f->hc) aa_alpha_hc(gx, gy, g->bw, g->bh, f->data + g->off, f->hc, rv);
            else aa_alpha(gx, gy, g->bw, g->bh, f->data + g->off, rv);
        }
        pen += g->adv;
        prev = c;
    }
    return x + ((pen + 8) >> 4);
}
static int32_t aa_text_r(int32_t xr, int32_t y, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{
    int32_t w = aa_text_w(f, s);
    aa_text(xr - w, y, f, s, fg, bg);
    return xr - w;
}
static void aa_text_c(int32_t x, int32_t y, int32_t w, const aafont_t *f, const char *s, uint16_t fg, uint16_t bg)
{
    aa_text(x + (w - aa_text_w(f, s)) / 2, y, f, s, fg, bg);
}
/* the same over what the canvas holds (aa_ov_px): left / right-aligned / centred in w */
static int32_t aa_text_ov(int32_t x, int32_t y, const aafont_t *f, const char *s, uint16_t fg)
{
    int32_t r;
    aa_ov = 1;
    r = aa_text(x, y, f, s, fg, 0);
    aa_ov = 0;
    return r;
}
static int32_t aa_text_ovr(int32_t xr, int32_t y, const aafont_t *f, const char *s, uint16_t fg)
{
    int32_t w = aa_text_w(f, s);
    aa_text_ov(xr - w, y, f, s, fg);
    return xr - w;
}
static void aa_text_ovc(int32_t x, int32_t y, int32_t w, const aafont_t *f, const char *s, uint16_t fg)
{
    aa_text_ov(x + (w - aa_text_w(f, s)) / 2, y, f, s, fg);
}
