#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""The bitmap fonts of gfx.c (firmware/gen/felucca_font.h) from Terminus 8x16 (assets/fonts/ter-u16n.bdf, SIL OFL 1.1,
assets/fonts/Terminus-LICENSE.txt): Felucca's tools/gen_font.py BDF path, kept for this one font.

  gen_font.py [OUT.h]          (default firmware/gen/felucca_font.h)
  gen_font.py --full OUT.h     every glyph of FONT_S (the header before the font diet: for comparisons)

  FONT_S  16 px, ASCII 32..126: the update, crash, UBOOT and USB rescue screens (platform.c, recovery.c) and the
          SMK-37's panel check (frodhes_smk.h). Only the glyphs tools/font_chars.py finds in those texts are stored
          (the font diet, 2026-10-24); every other code points at the '?' glyph, which is what gfx.c glyph() draws
          for a character outside the font, so gfx.c (Dinghy's) is unchanged.
  FONT_L  the same glyphs at x2, ASCII 32..95 (no code draws it, so it is not in the image).

Glyph format (gfx.c felucca_font_t): per glyph an advance, a bitmap width and an offset; the bitmap starts FONT_PAD
pixels left of the pen; rows top to bottom, 2 pixels per byte (high nibble first), alpha 0..15.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BDF = ROOT / "assets" / "fonts" / "ter-u16n.bdf"
OUT = ROOT / "firmware" / "gen" / "felucca_font.h"
PAD = 2
SIZES = [("S", 1, 32, 126), ("L", 2, 32, 95)]       # name, scale, first, last


def upscale(px_, w, h, scale):
    """integer nearest-neighbour enlargement of a w x h glyph"""
    if scale == 1:
        return px_
    big = []
    for y in range(h):
        row = []
        for x in range(w):
            row += [px_[y * w + x]] * scale
        big += row * scale
    return big


def render_bdf(path, scale, first, last):
    """BDF bitmap font (Terminus 8x16): exact pixels, cell = the font bounding box -> h, [(adv, bw, pixels)]"""
    lines = path.read_text(errors="replace").splitlines()
    fbb = next(ln for ln in lines if ln.startswith("FONTBOUNDINGBOX")).split()
    cw, ch, fy = int(fbb[1]), int(fbb[2]), int(fbb[4])
    base = ch + fy                                     # rows above the baseline
    glyphs, i = {}, 0
    while i < len(lines):
        if lines[i].startswith("ENCODING"):
            code = int(lines[i].split()[1])
            j = i
            while not lines[j].startswith("BBX"):
                j += 1
            bw, bh, bx, by = map(int, lines[j].split()[1:5])
            dw = cw
            k = i
            while not lines[k].startswith("BITMAP"):
                if lines[k].startswith("DWIDTH"):
                    dw = int(lines[k].split()[1])
                k += 1
            rows = [int(r, 16) for r in lines[k + 1:k + 1 + bh]]
            nbits = ((bw + 7) // 8) * 8
            cell = [0] * (cw * ch)
            top = base - (by + bh)
            for r, bits in enumerate(rows):
                for c in range(bw):
                    if bits >> (nbits - 1 - c) & 1:
                        x, y = bx + c, top + r
                        if 0 <= x < cw and 0 <= y < ch:
                            cell[y * cw + x] = 15
            glyphs[code] = (dw, cell)
            i = k + bh
        i += 1
    out = []
    for c in range(first, last + 1):
        dw, cell = glyphs.get(c, glyphs[ord("?")])
        bw = cw + 2 * PAD
        px_ = []
        for y in range(ch):
            px_ += [0] * PAD + cell[y * cw:(y + 1) * cw] + [0] * PAD
        out.append((dw * scale, bw * scale, upscale(px_, bw, ch, scale)))
    return ch * scale, out


def pack(g, h, bw, w=None):
    """rows of the w first columns (default bw) of a bw-wide glyph, 2 pixels per byte"""
    w = bw if w is None else w
    data = []
    for y in range(h):
        row = g[y * bw:y * bw + w] + [0]
        for x in range(0, w, 2):
            data.append((row[x] << 4) | row[x + 1])
    return data


def trim(g, h, bw):
    """the bitmap width FONT_S stores: up to the last inked column (0 for a blank glyph). gfx.c cv_text sets only the
    pixels with alpha > 0, so the blank columns on the right drew nothing: the screens are pixel-identical (the flash
    fit, 0.9.5; tests/py/test_font_glyphs.py decodes the header and compares every glyph with the BDF)"""
    return max([x + 1 for y in range(h) for x in range(bw) if g[y * bw + x]] + [0])


def overlap_pack(blobs):
    """code -> bytes, packed into one array where a glyph may start inside the end of the one before it (equal bytes:
    gfx.c reads only its own rows from its offset). Greedy by the longest overlap, ties in code order; every glyph
    gets its own offset (font_s_have tells the stored glyphs from the '?' by the offset); blank glyphs (no bytes) at
    the end. -> data, {code: offset}"""
    def ov(a, b):
        for k in range(min(len(a), len(b)) - 1, 0, -1):
            if a[-k:] == b[:k]:
                return k
        return 0
    full = [c for c in sorted(blobs) if blobs[c]]
    data, at = [], {}
    if full:
        order, rest = [full[0]], full[1:]
        while rest:
            a = blobs[order[-1]]
            best = max(rest, key=lambda c: (ov(a, blobs[c]), -c))
            order.append(best)
            rest.remove(best)
        for c in order:
            k = ov(data, blobs[c]) if data else 0
            at[c] = len(data) - k
            data += blobs[c][k:]
    for c in sorted(blobs):
        if not blobs[c]:
            at[c] = len(data)
    assert len(set(at.values())) == len(at)
    return data, at


def emit(keep_s):
    """the header text; keep_s: the characters FONT_S stores (None: all)"""
    note = ("FONT_S cut to ASCII 32..126, the rest shows as ?" if keep_s is None else
            "FONT_S: ASCII 32..126, only the characters tools/font_chars.py finds drawn; the others, and the rest, "
            "show as ?")
    lines = [f"/* generated by tools/gen_font.py from ter-u16n.bdf ({note}) */",
             "#pragma once", "#include <stdint.h>", f"#define FONT_PAD {PAD}  /* x scale for L, see below */", ""]
    sizes = {}
    for name, scale, first, last in SIZES:
        h, glyphs = render_bdf(BDF, scale, first, last)
        codes = list(range(first, last + 1))
        keep = set(codes) if name != "S" or keep_s is None else {c for c in codes if chr(c) in keep_s} | {ord("?")}
        data, at, bwk = [], {}, {}
        if name == "S":             # (the flash fit, 0.9.5) trimmed and overlapped: see trim / overlap_pack
            blobs = {}
            for c, (adv, bw, g) in zip(codes, glyphs):
                if c in keep:
                    bwk[c] = trim(g, h, bw)
                    blobs[c] = pack(g, h, bw, bwk[c])
            data, at = overlap_pack(blobs)
        else:
            for c, (adv, bw, g) in zip(codes, glyphs):
                if c in keep:
                    at[c] = len(data)
                    data += pack(g, h, bw)
        q = codes.index(ord("?"))
        offs = [at.get(c, at[ord("?")]) for c in codes]
        adv = [glyphs[i][0] if c in keep else glyphs[q][0] for i, c in enumerate(codes)]
        bws = [bwk.get(c, glyphs[i][1]) if c in keep else bwk.get(ord("?"), glyphs[q][1]) for i, c in enumerate(codes)]
        assert len(data) < 65536
        lines.append(f"static const uint8_t FONT_{name}_DATA[{len(data)}] = {{")
        rows = ["    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 24]) for i in range(0, len(data), 24)]
        lines.append(",\n".join(rows) + ("};" if name == "S" else ",\n};"))   # (the two layouts of the header)
        lines.append(f"static const uint16_t FONT_{name}_OFF[{len(offs)}] = {{" + ", ".join(map(str, offs)) + "};")
        lines.append(f"static const uint8_t FONT_{name}_ADV[{len(codes)}] = {{" + ", ".join(map(str, adv)) + "};")
        lines.append(f"static const uint8_t FONT_{name}_BW[{len(codes)}] = {{" + ", ".join(map(str, bws)) + "};")
        lines.append(f"static const felucca_font_t FONT_{name} = {{ {h}, {PAD * scale}, {first}, {last}, "
                     f"FONT_{name}_ADV, FONT_{name}_BW, FONT_{name}_OFF, FONT_{name}_DATA }};")
        lines.append("")
        sizes[name] = (len(keep), len(codes), len(data))
    return "\n".join(lines), sizes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default=str(OUT))
    ap.add_argument("--full", action="store_true", help="every glyph of FONT_S (no diet)")
    a = ap.parse_args()
    keep = None
    if not a.full:
        sys.path.insert(0, str(ROOT / "tools"))
        import font_chars
        keep = font_chars.font_s_chars()[0]
    text, sizes = emit(keep)
    Path(a.out).write_text(text)
    for name, (k, n, b) in sizes.items():
        print(f"font {name}: {k} of {n} glyphs stored, {b} B data")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
