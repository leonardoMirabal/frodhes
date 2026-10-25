# SPDX-License-Identifier: GPL-3.0-only
"""The font diet (2026-10-24): the faces in the image keep only the glyphs the firmware can draw
(tools/font_chars.py finds them in the sources; gen_aa_font.py and gen_font.py store only those). A text with a
character the face does not have would draw '?' instead, so:

  - static: every character of every literal that may reach the screen (font_chars) has its glyph in every face
    that may draw it: AF_S (aa_font.h AF_S_MAP), the value faces (AF_L, AF_V25, AF_V24 extras), gfx.c's FONT_S
    (felucca_font.h: a glyph of its own, not the '?' it points the others at);
  - the bitmap font's header is what gen_font.py writes today (a new character in a FONT_S text: run it);
  - on the host (the default build and the SMK-37's): every preset, every page there and back, the knobs turned,
    the card variants, every value of every card (fitscan): no character drawn without a glyph (aatext.c aa_h_note,
    host_state glyphmiss) and none outside the scanned set (the scan sees what the screens show)."""
import os
import re
import sys

import pytest

import hostrun

ROOT = hostrun.ROOT
sys.path.insert(0, os.path.join(ROOT, "tools"))
import font_chars  # noqa: E402
import gen_font  # noqa: E402

GEN = os.path.join(ROOT, "firmware", "gen")


def read(name):
    return open(os.path.join(GEN, name)).read()


def aa_face(text, name):
    """the characters face AF_<name> has a glyph for"""
    m = re.search(r"static const aafont_t AF_%s = \{(.*?)\};" % name, text)
    f = [x.strip() for x in m.group(1).split(",")]
    first, last, nex = int(f[2]), int(f[3]), int(f[4])
    have = set()
    mm = re.search(r"AF_%s_MAP\[\d+\] = \{(.*?)\};" % name, text, re.S)
    if mm:
        for i, g in enumerate(int(x) for x in mm.group(1).replace("\n", " ").split(",") if x.strip()):
            if g != 255:
                have.add(chr(first + i))
    else:
        have |= {chr(c) for c in range(first, last + 1)}
    ex = re.search(r"AF_%s_EX\[\d+\] = \{(.*?)\};" % name, text).group(1)
    have |= {chr(int(x)) for x in ex.split(",")[:nex]}
    return have


def font_s_have():
    t = read("felucca_font.h")
    off = [int(x) for x in re.search(r"FONT_S_OFF\[\d+\] = \{(.*?)\}", t).group(1).split(",")]
    q = off[ord("?") - 32]
    return {chr(32 + i) for i, o in enumerate(off) if o != q} | {"?"}


def test_aa_small_face_has_every_drawn_character():
    aa, why = font_chars.aa_chars()
    have = aa_face(read("aa_font.h"), "S")
    lost = sorted(c for c in aa if 32 <= ord(c) <= 126 and c not in have)
    assert not lost, ("characters drawn in the small face without a glyph (run tools/gen_aa_font.py "
                      "firmware/gen/aa_font.h --preset inter-tight): " + "; ".join(f"{c!r}: {why.get(c, ['run time'])[:3]}" for c in lost))
    other = sorted(c for c in aa if ord(c) > 126 and c != "\x85")   # (the ellipsis draws as '.')
    assert not other, f"characters past ASCII in a text: the small face has none (they draw as '?'): {other}"


def test_section_names_are_not_drawn():
    """a section name is never on screen: __attribute__((section(...))) and a macro for one (rhodes.c / fx.c RH_SP_SEC,
    SPREAD's buffers, 0.9.5: their names made '_', 'a' .. 'z' look drawn in the small face)"""
    for line in ('static int32_t sp_g RH_SP_SEC(".data.fx_sp_g") = 32768;',
                 'static int32_t x __attribute__((section(".bss.x")));'):
        assert font_chars.NOT_DRAWN.search(line), line
    assert not font_chars.NOT_DRAWN.search('    gfx_text(x, y, "SECTION", 1);')


@pytest.mark.parametrize("hdr,name", [("aa_font.h", "L"), ("aa_font_v25.h", "V25"), ("aa_font_v24.h", "V24")])
def test_value_faces_keep_their_characters(hdr, name):
    """a value face keeps every L_CHARS character that some text has (aa_covers decides L or S by them)"""
    from gen_aa_font import L_CHARS
    want = set(font_chars.aa_l_chars(L_CHARS))
    have = aa_face(read(hdr), name)
    assert want <= have, f"AF_{name} lacks {sorted(want - have)} (run tools/gen_aa_font.py ... --value-px N)"


def test_font_s_has_every_drawn_character():
    fs, why = font_chars.font_s_chars()
    have = font_s_have()
    lost = sorted(c for c in fs if c not in have and 32 <= ord(c) <= 126)
    assert not lost, ("FONT_S texts with characters it has no glyph for (run tools/gen_font.py): "
                      + "; ".join(f"{c!r}: {why.get(c, ['run time'])[:3]}" for c in lost))


def test_font_s_header_is_generated():
    """felucca_font.h is gen_font.py's output for today's texts (the full font: tools/gen_font.py --full)"""
    text, _ = gen_font.emit(font_chars.font_s_chars()[0])
    assert text == read("felucca_font.h"), "firmware/gen/felucca_font.h is stale: run tools/gen_font.py"


def font_s_pixels(text):
    """felucca_font.h's FONT_S drawn as gfx.c cv_text draws it: code -> the set pixels {(x, y): alpha}, x from the
    bitmap's left edge (a pixel of alpha 0 is not set)"""
    def arr(name):
        return [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+|\d+",
                                              re.search(r"FONT_S_%s\[\d+\] = \{(.*?)\};" % name, text, re.S).group(1))]
    data, off, bw = arr("DATA"), arr("OFF"), arr("BW")
    h = int(re.search(r"static const felucca_font_t FONT_S = \{ (\d+),", text).group(1))
    out = {}
    for i in range(len(off)):
        bpr, px = (bw[i] + 1) // 2, {}
        for y in range(h):
            for x in range(bw[i]):
                v = data[off[i] + y * bpr + x // 2]
                v = v & 15 if x & 1 else v >> 4
                if v:
                    px[(x, y)] = v
        out[32 + i] = px
    return out


def test_font_s_draws_the_bdf_glyphs():
    """the flash fit (0.9.5): FONT_S stores each glyph trimmed to its last inked column, the glyphs overlapping in
    FONT_S_DATA (gen_font.py trim / overlap_pack); drawn as cv_text draws, every code gives exactly the pixels of the
    untrimmed, unpacked font (gen_font.emit with the same characters, packed the old way): the screens are
    pixel-identical"""
    keep = font_chars.font_s_chars()[0]
    now = font_s_pixels(read("felucca_font.h"))
    h, glyphs = gen_font.render_bdf(gen_font.BDF, 1, 32, 126)
    q = font_s_have()
    for c in range(32, 127):
        adv, bw, g = glyphs[(c if chr(c) in q else ord("?")) - 32]
        want = {(x, y): g[y * bw + x] for y in range(h) for x in range(bw) if g[y * bw + x]}
        assert now[c] == want, f"FONT_S {chr(c)!r} draws other pixels than the BDF"


def walk():
    """every preset; on each, every page forward and back; on some the knobs turned; every card variant (CC 11)"""
    ev, t = [], 100
    for variant in (0, 1, 3, 5):
        ev.append(f"{t} midi 0xB0 11 {variant}")
        t += 50
        for pr in range(28 if variant == 0 else 4):
            ev.append(f"{t} midi 0xC0 {pr if pr < 12 else pr + 4} 0")
            t += 450
            for _ in range(24):
                ev.append(f"{t} turn 0 1")
                t += 60
                if variant == 0 and pr % 6 == 0:
                    for k in range(4):
                        ev.append(f"{t} turn {2 + k} {7 * (k + 1)}")
                        t += 40
            for _ in range(24):
                ev.append(f"{t} turn 0 -1")
                t += 30
    ev += [f"{t} expect state fitscan 0", f"{t + 10} expect state glyphmiss 0"]
    return ev, t + 50


def drawn(out):
    got = {}
    for m in re.finditer(r"^GLYPHS ([SL])(.*)$", out, re.M):
        got[m.group(1)] = {chr(int(tok[:2], 16)) for tok in m.group(2).split() if int(tok[2:]) & 2}
    return got


@pytest.mark.parametrize("board", ["fm1", "smk37"])
def test_host_draws_no_missing_glyph(board):
    import test_smk_host
    binary = hostrun.BIN if board == "fm1" else test_smk_host.smk_build()
    ev, end = walk()
    r = hostrun.run(f"font_glyphs_{board}", ev, end, binary=binary, check=False)
    miss = re.findall(r"^GLYPH MISSING.*$", r.out, re.M)
    assert not miss, miss
    assert not r.fails, r.fails[:10]
    got = drawn(r.out)
    assert set(got) == {"S", "L"}, r.out[-1500:]
    aa = font_chars.aa_chars()[0]
    fold = {c.upper() for c in aa}
    assert got["S"] <= aa, f"drawn but not found by tools/font_chars.py: {sorted(got['S'] - aa)}"
    assert got["L"] <= fold, f"drawn large but not found by tools/font_chars.py: {sorted(got['L'] - fold)}"
