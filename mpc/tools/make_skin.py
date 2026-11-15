#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC skin (docs/MPC.md): mpc/skin.json's pages drawn in the desktop plugin's look into mpc/skins/.

    python3 mpc/tools/make_skin.py [--params build/mpc/params.json] [--preview DIR]   render mpc/skins/
    python3 mpc/tools/make_skin.py --check [--params ...]                              mpc/skins/ up to date?

What it makes (mpc/skins/, copied verbatim into the plugin folder's "Plugin Skins/" by mpc/build.py):
    TUI.json, Q-Links.json   the MPC's scripted UI: one tab per page, the Q-Link maps
    bg_<page>.png            each page's 1280 x 628 picture: the aluminium plate, the black body, two panels with
                             their titles and the cards' labels (everything that does not move)
    knob_uni.png, knob_bi.png  the knob, 127 frames (the scale lit from the left, or from the top when centred)
    lamp<n>.png              a section's lamp, one frame per step of its switch (off, then on)
    cseg_*, hseg_*           the segmented switches' keys, one picture per choice, lit and unlit, the name cut in

The pictures are painted by mpc/tools/skin_draw.c with the plugin's own drawing code (plugin/ui/frui_gfx.c,
frui_font.c, the fonts in assets/fonts) at twice the plugin's logical size, so the plate, the panels, the knob, the
switches and the lamps are the plugin's; the moving text (the values) is the MPC's own font (Roboto), as the MPC
draws text itself. The page layout: the plugin's A3 look (a rounded aluminium plate, an 8 px frame round a black body,
the panels 8 px in, every corner concentric) with a slim rail for the wordmark; per page two panels, each one Q-Link
group of four cards in a row (the MPC's focus rectangle wraps it), the switches in the panel's header as on the
plugin. The MPC draws its own header (track, plugin, preset) above and the function-key tabs below this area.

--preview DIR composes each page (the background, each control's picture at its bounds, the default values' text)
into DIR/<page>.png: what the MPC shows, for checking on a computer (Pillow needed). The same inputs give the same
files, byte for byte.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MPC = os.path.join(ROOT, "mpc")
SKINS = os.path.join(MPC, "skins")
SPEC = os.path.join(MPC, "skin.json")
FONT = os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf")
DFONT = os.path.join(ROOT, "assets", "fonts", "Michroma-Regular.ttf")

# ---- the geometry, in screen pixels (the tab's area: 1280 x 628 under the MPC's header); S = pixels per plugin
# logical pixel
W, H, S = 1280, 628, 2
CATALOG_BOX = (0, 102, 1280, 742)   # (preview: the VOICE page's 1280 x 640 crop, mpc-catalog.png)
R_OUT, FRAME, RAIL = 44, 16, 44          # the plate's corners, its frame round the body, the rail (the body's top)
GAP, R_PANEL = 16, 12                     # the panels' inset in the body; their corners (concentric: 44-16-16)
PX, PW = FRAME + GAP, W - 2 * (FRAME + GAP)
PH = (H - RAIL - FRAME - 3 * GAP) // 2    # 260
PY = (RAIL + GAP, RAIL + 2 * GAP + PH)
HH = 48                                   # the panel's header
CW, CH = PW // 4, PH - HH                 # a card: 304 x 212
LY = 24                                   # in a card: the label's centre, the knob's centre, its value's box
KR = 27                                   # the knob's radius, logical (the plugin's 24, a touch larger)
KF = 2 * (KR + 12) * S                    # a knob frame: 156 px (the scale's ticks reach KR + 9)
KY = 110
VY, VH = 170, 40
FRAMES = 127
SEG_H_CARD, SEG_H_HDR = 64, 40
MENU_W = 216
LAMP_F, LAMP_X = 28, 28                   # a lamp's frame, px; its centre from the panel's left edge

C_TEXT = "ffeeede8"
C_LABEL = "ff848489"                      # the A3 knob labels: (132, 132, 137)
C_TEXT2 = "ff96969a"
C_TEXT3 = "ff68686d"
C_ENGR = "ff222326"
C_HAIR = "ff202022"
BG = "ff060607"
VALUE_FONT = ("Roboto", "Regular", 34.0)
MENU_FONT = ("Roboto", "Regular", 22.0)
WIDE_FONT = ("Roboto", "Regular", 36.0)
T_LABEL = (9.0, 620, 1.4)                 # the cards' labels (logical size, weight, tracking)
T_CAPTION = (8.5, 600, 1.6)
T_TITLE = (10.0, 400, 2.4)                # the panels' titles, in the display face
T_SEG = (9.5, 640, 0.8)
T_SEG_CARD = (11.0, 640, 0.9)


def L(px):
    """screen pixels to logical"""
    return "%.4g" % (px / S)


def slug(name):
    return "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")


# ---- ordered JSON with repeated keys (the MPC's parser wants the overlays' two "version" keys and a fixed order)
class Obj(list):
    pass


def obj(*pairs):
    return Obj(pairs)


def dump(v, ind=0):
    pad, inner = " " * ind, " " * (ind + 4)
    if isinstance(v, Obj):
        return "{\n" + ",\n".join(inner + json.dumps(k) + ": " + dump(x, ind + 4) for k, x in v) + "\n" + pad + "}" if v else "{}"
    if isinstance(v, (list, tuple)):
        return "[\n" + ",\n".join(inner + dump(x, ind + 4) for x in v) + "\n" + pad + "]" if v else "[]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


# ---- the parameters and the layout
class Param:
    def __init__(self, p):
        self.index, self.name, self.kind = p["index"], p["name"], p.get("kind", "float")
        self.choices = p.get("choices") or (["OFF", "ON"] if self.kind == "bool" else [])
        self.default, self.display = p.get("default", 0.0), p.get("display", "")

    @property
    def handle(self):
        return "Parameter %d" % self.index

    @property
    def discrete(self):
        return self.kind in ("choice", "bool")

    @property
    def named(self):
        """a list of names (a menu reads better than a knob overlay): every choice but the numeric ones"""
        return self.discrete and not all(c.replace("-", "").split(" ")[0].isdigit() for c in self.choices)


def load(params_path):
    params = {p["name"]: Param(p) for p in json.load(open(params_path))["params"]}
    spec = json.load(open(SPEC))
    errs = []

    def get(name, where):
        if name not in params:
            errs.append("%s: unknown parameter %r" % (where, name))
            return None
        return params[name]

    pages = []
    for ki, key in enumerate(spec["keys"]):
        if len(key) > 8:
            errs.append("key %d: more than 8 pages" % (ki + 1))
        for si, pg in enumerate(key):
            page = {"name": pg["name"], "key": ki, "sub": si, "nsub": len(key), "panels": []}
            if len(pg["name"]) > 8:
                errs.append("page %s: a tab name of 8 characters at most" % pg["name"])
            if len(pg["panels"]) != 2:
                errs.append("page %s: two panels" % pg["name"])
            for pi, pn in enumerate(pg["panels"]):
                where = "page %s panel %d" % (pg["name"], pi + 1)
                sw = pn.get("switch", [])
                sw = [sw] if isinstance(sw, str) else sw
                panel = {"title": pn["title"], "caption": pn.get("caption", ""),
                         "lamp": get(pn["lamp"], where) if "lamp" in pn else None,
                         "switches": [get(s, where) for s in sw],
                         "menu": get(pn["menu"], where) if "menu" in pn else None,
                         "menuCaption": pn.get("menuCaption", ""), "cards": [], "x": PX, "y": PY[pi]}
                col = 0
                for c in pn["cards"]:
                    p = get(c["param"], where)
                    span = 2 if c.get("wide") else 1
                    kind = "wide" if span == 2 else ("seg" if p and p.discrete and len(p.choices) <= 4 else "knob")
                    if p and kind == "knob" and p.discrete and len(p.choices) < 2:
                        errs.append("%s: %s has one choice" % (where, p.name))
                    panel["cards"].append({"param": p, "label": c["label"], "bi": bool(c.get("bi")), "kind": kind,
                                           "col": col, "span": span})
                    col += span
                if col != 4:
                    errs.append("%s: the cards fill %d columns of 4" % (where, col))
                for s in panel["switches"]:
                    if s and (not s.discrete or len(s.choices) > 4):
                        errs.append("%s: switch %s needs 2 to 4 choices" % (where, s.name))
                if panel["lamp"] and not panel["lamp"].discrete:
                    errs.append("%s: lamp %s is not a switch" % (where, panel["lamp"].name))
                page["panels"].append(panel)
            pages.append(page)
    if len(spec["keys"]) > 6:
        errs.append("more than six function keys")
    names = [p["name"] for p in pages]
    if len(set(names)) != len(names):
        errs.append("page names repeat")
    program = [get(n, "program") for n in spec["program"]]
    if len(program) != 16:
        errs.append("program: 16 parameters")
    if errs:
        sys.exit("make_skin: " + "\n  ".join(errs))
    return params, pages, program, spec


# ---- what goes where (shared by the pictures, TUI.json and the preview)
def seg_cells(p, card):
    """a segmented switch's cells: (x, y, w, h) per choice, in the card's or the header's coordinates (the header's
    relative to the switch's own left edge and the header's top); in a card four choices go in two rows of two"""
    n = len(p.choices)
    if card and n == 4:
        sw, h = (CW - 48) // 2, SEG_H_CARD * 7 // 8
        x0, y0 = (CW - 2 * sw) // 2, KY - h
        return [(x0 + (i % 2) * sw, y0 + (i // 2) * h, sw, h) for i in range(n)]
    t = seg_type(p, card)
    widest = max(text_width(c, t) for c in p.choices)
    pad, mn = (28, 64) if card else (20, 48)
    sw = int(max(mn, -(-(widest + pad) // 4) * 4)) * S   # logical, rounded up to 4; in px
    if card:
        sw = min(sw, (CW - 32) // n)
    h = SEG_H_CARD if card else SEG_H_HDR
    if card:
        x0, y0 = (CW - sw * n) // 2, KY - h // 2
    else:
        x0, y0 = 0, (HH - h) // 2
    return [(x0 + i * sw, y0, sw, h) for i in range(n)]


def seg_type(p, card):
    return T_SEG_CARD if card and len(p.choices) < 4 else T_SEG


def text_width(s, t):
    """a caption's width in the plugin's text face, logical px: an estimate from its length (capitals of Inter Tight
    average about 0.72 em), so the layout does not depend on a font library; it only sizes the segments, rounded up
    to 4 logical px, and the caption is centred in its segment"""
    return len(s) * t[0] * 0.72 + t[2] * (len(s) - 1)


def header_layout(panel):
    """the header's controls from the right edge: the menu, then the switches; (kind, param, x, y, w, h) in tab px"""
    x = panel["x"] + PW - 24
    out = []
    if panel["menu"]:
        x -= MENU_W
        out.append(("menu", panel["menu"], x, panel["y"] + (HH - SEG_H_HDR) // 2, MENU_W, SEG_H_HDR))
        x -= 16
    for p in reversed(panel["switches"]):
        cells = seg_cells(p, False)
        w = cells[-1][0] + cells[-1][2]
        x -= w
        out.append(("switch", p, x, panel["y"], w, HH))
        x -= 16
    return out


def card_rect(panel, c):
    return panel["x"] + c["col"] * CW, panel["y"] + HH, CW * c["span"], CH


# ---- the pictures (skin_draw's script)
def fonts_text(face, t, al, x, cy, col, s, eng=0, tnum=0):
    """x in px, or "+N": N logical px after the end of the text drawn before"""
    return 'text %d %g %g %g %s %s %s %s %d %d "%s"\n' % (face, t[0], t[1], t[2], al, x if isinstance(x, str) else L(x),
                                                       L(cy), col, eng, tnum, s)


def page_script(page, out):
    s = "img %s %d %d %d 0\nfill %s\n" % (out, W, H, S, BG)
    s += "plate 0 0 %s %s %s %s %s\n" % (L(W), L(H), L(R_OUT), L(FRAME), L(RAIL))
    # the rail: the wordmark cut into the metal and its pilot lamp; the page's name and place on the right
    cy = RAIL / 2 + 1
    s += fonts_text(1, (13.0, 400, 3.0), "l", 40, cy, C_ENGR, "FRODHES", eng=1)
    s += "pilot +15 %s\n" % L(cy)                     # (after the last text's end)
    right = W - 40
    if page["nsub"] > 1:
        s += fonts_text(0, (8.5, 650, 1.6), "r", right, cy, C_ENGR, "%d / %d" % (page["sub"] + 1, page["nsub"]), eng=1, tnum=1)
        right -= 70
    s += fonts_text(1, (9.0, 400, 2.4), "r", right, cy, C_ENGR, page["name"], eng=1)
    for panel in page["panels"]:
        x, y = panel["x"], panel["y"]
        s += "panel %s %s %s %s %s\n" % (L(x), L(y), L(PW), L(PH), L(R_PANEL))
        tx = x + 24
        if panel["lamp"]:
            tx += 24
        s += fonts_text(1, T_TITLE, "l", tx, y + HH / 2, C_TEXT, panel["title"])
        if panel["caption"]:
            s += fonts_text(0, T_CAPTION, "l", "+11", y + HH / 2, C_TEXT3, panel["caption"])
        s += "hline %s %s %s %s\n" % (L(x + 1), L(x + PW - 1), L(y + HH), C_HAIR)
        for kind, p, hx, hy, hw, hh in header_layout(panel):
            if kind == "menu":
                s += "well %s %s %s %s 4\n" % (L(hx), L(hy), L(hw), L(hh))
                s += fonts_text(0, T_CAPTION, "l", hx + 20, hy + hh / 2, C_TEXT3, panel["menuCaption"])
                s += "chev %s %s 3.5 0 %s\n" % (L(hx + hw - 24), L(hy + hh / 2), C_TEXT2)
            else:
                cells = seg_cells(p, False)
                s += seg_well(hx, hy, cells)
        for c in panel["cards"]:
            cx0, cy0, cw, _ = card_rect(panel, c)
            s += fonts_text(0, T_LABEL, "c", cx0 + cw / 2, cy0 + LY, C_LABEL, c["label"])
            if c["kind"] == "seg":
                s += seg_well(cx0, cy0, seg_cells(c["param"], True))
            elif c["kind"] == "wide":
                bx, by, bw, bh = wide_box(cx0, cy0)
                s += "well %s %s %s %s 4\n" % (L(bx), L(by), L(bw), L(bh))
                s += "chev %s %s 4.5 0 %s\n" % (L(bx + bw - 32), L(by + bh / 2), C_TEXT2)
    return s + "end\n"


def wide_box(cx0, cy0):
    return cx0 + 32, cy0 + KY - 48, 2 * CW - 64, 96


def seg_well(ox, oy, cells):
    x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
    x1, y1 = max(c[0] + c[2] for c in cells), max(c[1] + c[3] for c in cells)
    s = "well %s %s %s %s 4\n" % (L(ox + x0), L(oy + y0), L(x1 - x0), L(y1 - y0))
    for (cx, cy, cw, ch) in cells:
        ins = 8 * ch / 40
        if cx > x0:
            s += "vline %s %s %s ff262628\n" % (L(ox + cx), L(oy + cy + ins), L(oy + cy + ch - ins))
        if cy > y0:
            s += "hline %s %s %s ff262628\n" % (L(ox + cx + 12), L(ox + cx + cw - 12), L(oy + cy))
    return s


def knob_script(path, centred):
    s = "img %s %d %d %d 1\n" % (path, KF, KF * FRAMES, S)
    for f in range(FRAMES):
        s += "knob %s %s %g %.6f %d\n" % (L(KF / 2), L(f * KF + KF / 2), KR, f / (FRAMES - 1), centred)
    return s + "end\n"


def lamp_script(path, n):
    s = "img %s %d %d %d 1\n" % (path, LAMP_F, LAMP_F * n, S)
    for f in range(n):
        s += "lamp %s %s 3.4 %d\n" % (L(LAMP_F / 2), L(f * LAMP_F + LAMP_F / 2), 1 if f else 0)
    return s + "end\n"


def seg_script(path, w, h, caption, on, t):
    s = "img %s %d %d %d 1\n" % (path, w, h, S)
    if on:
        s += "key 2 2 %s %s 2 %d\n" % (L(w - 4), L(h - 4), (sum(map(ord, caption)) % 97) + 11)
    s += fonts_text(0, t, "c", w / 2, h / 2, C_ENGR if on else C_TEXT2, caption)
    return s + "end\n"


def seg_files(p, card):
    pre = "cseg" if card else "hseg"
    return [("%s_%d_%d_on.png" % (pre, p.index, i), "%s_%d_%d_off.png" % (pre, p.index, i)) for i in range(len(p.choices))]


def bg_file(page):
    return "bg_%s.png" % slug(page["name"])


def lamp_file(p):
    return "lamp%d.png" % len(p.choices)


def all_pictures(pages):
    """(file, script) for every picture"""
    out = [("knob_uni.png", lambda f: knob_script(f, 0)), ("knob_bi.png", lambda f: knob_script(f, 1))]
    seen = set()
    for page in pages:
        out.append((bg_file(page), lambda f, pg=page: page_script(pg, f)))
        for panel in page["panels"]:
            if panel["lamp"] and lamp_file(panel["lamp"]) not in seen:
                seen.add(lamp_file(panel["lamp"]))
                out.append((lamp_file(panel["lamp"]), lambda f, n=len(panel["lamp"].choices): lamp_script(f, n)))
            segs = [(p, False) for p in panel["switches"]] + [(c["param"], True) for c in panel["cards"] if c["kind"] == "seg"]
            for p, card in segs:
                cells = seg_cells(p, card)
                for (on, off), cap, (_, _, w, h) in zip(seg_files(p, card), p.choices, cells):
                    t = seg_type(p, card)
                    out.append((on, lambda f, a=(w, h, cap, 1, t): seg_script(f, *a)))
                    out.append((off, lambda f, a=(w, h, cap, 0, t): seg_script(f, *a)))
    return out


# ---- TUI.json
def action(on, handler, handle, extra=""):
    return obj(("version", 1), ("onAction", on), ("handler", handler), ("handleName", handle), ("additionalData", extra))


def bgdata(colour):
    side = lambda: obj(("version", 1), ("colour", colour), ("image", ""))   # noqa: E731
    return obj(("version", 1), ("focussed", side()), ("unfocussed", side()))


def comp(actions, children, colour="00000000", ignore=False):
    return obj(("version", 2), ("actions", actions), ("backgroundData", bgdata(colour)), ("ignoreMousePresses", ignore),
               ("disableCoarseDataWheel", False), ("componentsData", children))


def entry(name, typ, data, bounds, focus="No", invalid="Show", when="Always", remap=()):
    return obj(("version", 2),
               ("componentData", obj(("version", 1), ("name", name), ("type", typ), ("data", data))),
               ("handle remapping", obj(("version", 1), ("map", [obj(("key", k), ("value", v)) for k, v in remap]))),
               ("bounds", obj(("version", 1), ("acceptsHWFocus", focus), ("showWhenDataModelInvalid", invalid),
                              ("whenVisible", when), ("boundsType", "Absolute"), ("bounds", bounds))))


def rect(x, y, w, h):
    return "%d %d %d %d" % (x, y, w, h)


def label(typ, font, colour, just, text=None, handle="Data"):
    pairs = [("version", 1),
             ("textStyle", obj(("version", 1), ("font", obj(("version", 1), ("name", font[0]), ("style", font[1]),
                                                            ("height", font[2]))),
                               ("colour", colour), ("justification", just), ("case", "Original"))),
             ("type", typ), ("handleName", handle)]
    if text is not None:
        pairs.append(("text", text))
    return Obj(pairs)


def focus(w, h, accent):
    return entry("focus", "Focus", obj(("version", 1), ("backgroundColour", "14ffffff"), ("outlineColour", accent),
                                       ("backgroundInset", 6.0), ("outlineThickness", 2.0)),
                 rect(0, 0, w, h), when="WhenFocussed")


def button(on, off, bid, n):
    return obj(("version", 2), ("onImage", on), ("offImage", off), ("buttonId", bid), ("numButtonsInGroup", n),
               ("handleName", "Data"), ("gestureBehaviour", "Instant"))


def build_tui(pages, accent):
    defs = []                       # (name, component)
    have = set()

    def define(name, make):
        if name not in have:
            have.add(name)
            defs.append((name, make()))
        return name

    def card_actions(overlay, dbl=True):
        a = [action("Mouse Down", "Q-Link", "Data")]
        if dbl:
            a.append(action("Double Click", "Show Overlay", "Data", overlay))
        return a + [action("Enter Pressed", "Show Overlay", "Data", overlay)]

    def knob_def(c):
        bi, menu = c["bi"], c["param"].named
        name = "card knob %s%s" % ("bi" if bi else "uni", " menu" if menu else "")
        ov = "menu overlay" if menu else ("knob overlay bipolar" if bi else "knob overlay")
        kx = (CW - KF) // 2
        return define(name, lambda: comp(card_actions(ov), [
            focus(CW, CH, accent),
            entry("knob", "Knob", obj(("version", 2), ("knobType", "FilmStrip"), ("filmStrip", "knob_bi.png" if bi else "knob_uni.png"),
                                      ("numFrames", FRAMES), ("invert", False), ("handleName", "Data")),
                  rect(kx, KY - KF // 2, KF, KF)),
            entry("value", "Label", label("Value", VALUE_FONT, C_TEXT, "horizontallyCentred verticallyCentred"),
                  rect(0, VY, CW, VH))]))

    def seg_def(p, card):
        name = "%s %d" % ("card switch" if card else "header switch", p.index)
        cells = seg_cells(p, card)
        files = seg_files(p, card)
        kids = [entry("seg %d" % i, "Button", button(on, off, i, len(cells)), rect(*cell))
                for i, (cell, (on, off)) in enumerate(zip(cells, files))]
        if card:
            return define(name, lambda: comp(card_actions("menu overlay", dbl=False), [focus(CW, CH, accent)] + kids))
        return define(name, lambda: comp([], kids))

    def wide_def():
        bx, by, bw, bh = wide_box(0, 0)
        return define("card wide", lambda: comp(card_actions("menu overlay"), [
            focus(2 * CW, CH, accent),
            entry("value", "Label", label("Value", WIDE_FONT, C_TEXT, "horizontallyCentred verticallyCentred"),
                  rect(bx + 48, by, bw - 96, bh))]))

    def menu_def():
        return define("header menu", lambda: comp([action("Mouse Down", "Show Overlay", "Data", "menu overlay"),
                                                   action("Enter Pressed", "Show Overlay", "Data", "menu overlay")], [
            entry("value", "Label", label("Value", MENU_FONT, C_TEXT, "right verticallyCentred"),
                  rect(80, 0, MENU_W - 80 - 44, SEG_H_HDR))]))

    tabs, discrete = [], {}
    for page in pages:
        kids = [entry("background", "Image", obj(("version", 2), ("imageType", "Regular"), ("colour", "0"),
                                                 ("image", bg_file(page))), rect(0, 0, W, H))]
        bounds = []
        for panel in page["panels"]:
            if panel["lamp"]:
                p = panel["lamp"]
                discrete[p.index] = p
                kids.append(entry("lamp %s" % panel["title"].lower(), "Image",
                                  obj(("version", 2), ("imageType", "FilmStrip"), ("colour", "0"), ("image", lamp_file(p)),
                                      ("numFrames", len(p.choices)), ("handleName", p.handle)),
                                  rect(panel["x"] + LAMP_X - LAMP_F // 2, panel["y"] + HH // 2 - LAMP_F // 2, LAMP_F, LAMP_F)))
            for kind, p, hx, hy, hw, hh in header_layout(panel):
                discrete[p.index] = p
                d = menu_def() if kind == "menu" else seg_def(p, False)
                kids.append(entry(p.name, d, obj(("version", 1), ("handleName", "Data")), rect(hx, hy, hw, hh),
                                  focus="No", invalid="Hide", remap=[("Data", p.handle)]))
            for c in panel["cards"]:
                p = c["param"]
                if p.discrete:
                    discrete[p.index] = p
                d = {"knob": knob_def, "seg": lambda c: seg_def(c["param"], True), "wide": lambda c: wide_def()}[c["kind"]](c)
                kids.append(entry(p.name, d, obj(("version", 1), ("handleName", "Data")), rect(*card_rect(panel, c)),
                                  focus="Yes", invalid="Hide", remap=[("Data", p.handle)]))
            bounds.append(rect(panel["x"], panel["y"] + HH, PW, CH))
        tabs.append(obj(("version", 1), ("tabName", page["name"]), ("fnKeyIndex", page["key"]),
                        ("fnKeySubIndex", page["sub"]), ("componentDefinition", comp([], kids, BG)),
                        ("qlinkBoundsData", bounds)))

    def overlay(typ, data):
        return comp([], [entry("overlay", typ, data, rect(0, 0, 1280, 800))], BG, ignore=True)

    lost = [action("Focus Lost", "Focus Lost", "Focus Lost")]
    for bi in (False, True):
        defs.append(("knob overlay bipolar" if bi else "knob overlay", overlay("KnobOverlay", obj(
            ("version", 2), ("version", 1), ("actions", lost), ("overlayType", "Knob With Header"), ("scaleType", "Linear"),
            ("polarityType", "Bipolar" if bi else "Unipolar"), ("showMinusSign", True), ("handleName", "Data")))))
    defs.append(("menu overlay", overlay("MenuOverlay", obj(("version", 1), ("version", 1), ("actions", lost),
                                                            ("withNumbers", False), ("handleName", "Data")))))
    rs = [obj(("key", p.handle), ("value", list(p.choices))) for _, p in sorted(discrete.items())]
    return obj(("pageData", obj(
        ("version", 1),
        ("componentDefinitions", obj(("version", 2), ("importFiles", []),
                                     ("localComponentDefinitions", [obj(("key", k), ("value", v)) for k, v in defs]))),
        ("replacementStrings", rs),
        ("info", obj(("version", 1), ("type", "CompleteDescription"))),
        ("tabs", tabs))))


def qlink_map(page):
    """Q-Link n -> parameter: the top panel's cards on 13-16, the lower one's on 9-12 (a wide card on its first
    column; the knob under its second column stays free)"""
    m = {q: -1 for q in range(1, 17)}
    for row, panel in enumerate(page["panels"]):
        for c in panel["cards"]:
            m[13 - 4 * row + c["col"]] = c["param"].index
    return m


def build_qlinks(pages, program):
    entries = [obj(("Tab", pg["key"] + 1), ("SubTab", pg["sub"] + 1), ("Bank Direction", "RowTopDown"),
                   ("Q-Links", Obj(("Q-Link %d" % q, v) for q, v in sorted(qlink_map(pg).items())))) for pg in pages]
    return obj(("version", 4), ("info", obj(("version", 1), ("type", "CompleteDescription"))),
               ("Screen Mode Q-Links", obj(("version", 4), ("map", entries))),
               ("Program Mode Q-Links", Obj(("Q-Link %d" % (i + 1), p.index) for i, p in enumerate(program))))


def json_files(pages, program, spec):
    return {"TUI.json": dump(build_tui(pages, spec["accent"])) + "\n", "Q-Links.json": dump(build_qlinks(pages, program)) + "\n"}


# ---- the painter
def painter():
    out = os.path.join(ROOT, "build", "mpc", "skin_draw")
    srcs = [os.path.join(MPC, "tools", "skin_draw.c")] + [os.path.join(ROOT, "plugin", "ui", f) for f in
                                                          ("frui_gfx.c", "frui_font.c")]
    hdrs = [os.path.join(ROOT, "plugin", "ui", f) for f in ("frui_gfx.h", "frui_font.h")]
    if not os.path.isfile(out) or os.path.getmtime(out) < max(os.path.getmtime(f) for f in srcs + hdrs):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run(["cc", "-O2", "-std=c99", "-D_DEFAULT_SOURCE", "-D_DARWIN_C_SOURCE", "-ffp-contract=off", "-Wall",
                        "-o", out] + srcs + ["-lz", "-lm"], check=True)
    return out


def render(pages, program, spec, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".png") or f.endswith(".json"):
            os.remove(os.path.join(out_dir, f))
    pics = all_pictures(pages)
    script = "".join(make(os.path.join(out_dir, f)) for f, make in pics)
    subprocess.run([painter(), FONT, DFONT], input=script.encode(), check=True)
    for name, text in json_files(pages, program, spec).items():
        with open(os.path.join(out_dir, name), "w", newline="\n") as f:
            f.write(text)
    return [f for f, _ in pics]


# ---- the preview: each page as the MPC composes it
def preview(pages, out_dir, skins=SKINS):
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(out_dir, exist_ok=True)

    def font(size, weight=430):
        f = ImageFont.truetype(FONT, int(round(size)))
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f

    def frame_of(p):
        if p.discrete:
            i = p.choices.index(p.display) if p.display in p.choices else 0
            return i, i / max(1, len(p.choices) - 1)
        return None, p.default

    def text(d, box, s, f, just):
        x, y, w, h = box
        tw = d.textlength(s, font=f)
        asc = f.getbbox("H")[3] - f.getbbox("H")[1]
        tx = x + (w - tw) / 2 if just == "c" else (x + w - tw if just == "r" else x)
        d.text((tx, y + (h - asc) / 2 - f.getbbox("H")[1]), s, font=f, fill=(238, 237, 232))

    made = []
    for page in pages:
        im = Image.open(os.path.join(skins, bg_file(page))).convert("RGBA")
        d = ImageDraw.Draw(im)
        for panel in page["panels"]:
            if panel["lamp"]:
                p = panel["lamp"]
                i, _ = frame_of(p)
                strip = Image.open(os.path.join(skins, lamp_file(p))).convert("RGBA")
                fr = strip.crop((0, i * LAMP_F, LAMP_F, (i + 1) * LAMP_F))
                im.alpha_composite(fr, (panel["x"] + LAMP_X - LAMP_F // 2, panel["y"] + HH // 2 - LAMP_F // 2))
            for kind, p, hx, hy, hw, hh in header_layout(panel):
                i, _ = frame_of(p)
                if kind == "menu":
                    text(d, (hx + 80, hy, MENU_W - 124, hh), p.display, font(MENU_FONT[2]), "r")
                else:
                    for k, ((cx, cy, cw, ch), (on, off)) in enumerate(zip(seg_cells(p, False), seg_files(p, False))):
                        b = Image.open(os.path.join(skins, on if k == i else off)).convert("RGBA")
                        im.alpha_composite(b, (hx + cx, hy + cy))
            for c in panel["cards"]:
                p = c["param"]
                x0, y0, cw, _ = card_rect(panel, c)
                i, v = frame_of(p)
                if c["kind"] == "knob":
                    strip = Image.open(os.path.join(skins, "knob_bi.png" if c["bi"] else "knob_uni.png")).convert("RGBA")
                    f = int(round(v * (FRAMES - 1)))
                    im.alpha_composite(strip.crop((0, f * KF, KF, (f + 1) * KF)), (x0 + (CW - KF) // 2, y0 + KY - KF // 2))
                    text(d, (x0, y0 + VY, CW, VH), p.display, font(VALUE_FONT[2]), "c")
                elif c["kind"] == "seg":
                    for k, ((cx, cy, cw2, ch), (on, off)) in enumerate(zip(seg_cells(p, True), seg_files(p, True))):
                        b = Image.open(os.path.join(skins, on if k == i else off)).convert("RGBA")
                        im.alpha_composite(b, (x0 + cx, y0 + cy))
                else:
                    bx, by, bw, bh = wide_box(x0, y0)
                    text(d, (bx + 48, by, bw - 96, bh), p.display, font(WIDE_FONT[2]), "c")
        # the MPC's own header and function keys round the tab's area, for context
        full = Image.new("RGBA", (W, 800), (24, 24, 26, 255))
        full.alpha_composite(im, (0, 110))
        fd = ImageDraw.Draw(full)
        fd.text((24, 40), "TRACK 1      Frodhes      F01 STAGE 77", font=font(26, 500), fill=(160, 160, 166))
        keys = sorted({(p["key"], p["sub"], p["name"]) for p in pages})
        names = {}
        for k, s, n in keys:
            names.setdefault(k, n if s == 0 else names.get(k, n))
        for k in range(6):
            x = 8 + k * 212
            sel = k == page["key"]
            fd.rounded_rectangle((x, 748, x + 200, 792), 6, fill=(60, 60, 64) if sel else (36, 36, 38))
            lab = page["name"] if sel else names.get(k, "")
            if lab:
                fd.text((x + 100 - fd.textlength(lab, font=font(20, 600)) / 2, 758), lab, font=font(20, 600),
                        fill=(240, 240, 240) if sel else (150, 150, 154))
        path = os.path.join(out_dir, "mpc-%s.png" % slug(page["name"]))
        full.convert("RGB").save(path, optimize=True)
        made.append(path)
        if slug(page["name"]) == "voice":           # the plugin catalogue's screenshot (user 2026-11-14): 2:1, the
            path = os.path.join(out_dir, "mpc-catalog.png")   # Frodhes area alone, no MPC header or tabs
            full.convert("RGB").crop(CATALOG_BOX).save(path, optimize=True)
            made.append(path)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--params", default=os.path.join(ROOT, "build", "mpc", "params.json"),
                    help="the parameters as the host sees them (mpc/build.py writes it)")
    ap.add_argument("--out", default=SKINS)
    ap.add_argument("--check", action="store_true", help="exit 1 when mpc/skins/ is not what mpc/skin.json makes")
    ap.add_argument("--preview", metavar="DIR", help="also compose each page into DIR (Pillow)")
    a = ap.parse_args()
    params, pages, program, spec = load(a.params)
    if a.check:
        bad = []
        for name, text in json_files(pages, program, spec).items():
            p = os.path.join(a.out, name)
            if not os.path.isfile(p) or open(p).read() != text:
                bad.append(name)
        bad += [f for f, _ in all_pictures(pages) if not os.path.isfile(os.path.join(a.out, f))]
        if bad:
            print("make_skin: mpc/skins/ is out of date (%s): python3 mpc/tools/make_skin.py" % ", ".join(bad[:6]))
            return 1
        print("make_skin: mpc/skins/ is up to date (%d pages)" % len(pages))
        return 0
    files = render(pages, program, spec, a.out)
    print("make_skin: %d pages, %d pictures -> %s" % (len(pages), len(files), os.path.relpath(a.out, ROOT)))
    if a.preview:
        for p in preview(pages, a.preview, a.out):
            print("  preview " + os.path.relpath(p, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
