# SPDX-License-Identifier: GPL-3.0-only
"""The design pass's rows (developer CC 11: 1 FADER, 2 LINE; ui.c draw_row_cards), checked on host screenshots of
every page of three TYPEs (STAGE 77, EARLY FELT, VIBES), at their defaults and with every knob turned. The rules
(private/ui-simplify/design-pass, the user 2026-10-18: "a clean, solid and elegant ui"):

  - one value size: every value's ink starts on the same row and is the large face's cap height (measured on the
    screen), in every card kind (amount, named detent, choice, ON / OFF);
  - one value edge: every value ends at the same x (ui.c CD_VR), units in their column right of it;
  - one baseline: a unit's ink ends on the value's baseline;
  - nothing crosses the text: no accent pixel (FADER: the fill's bright edge; LINE: any amber) inside the K tag + label,
    the value or the unit boxes;
  - the meter: an amount's fraction, a choice's option at (i + 1) / n, OFF empty; the screen's fill / line ends where
    the firmware says (+-1 px); at 0 nothing amber in the row (no stray edge);
  - every value of every card fits in the large face (host_state "fitscan": 0), the short forms used are exactly
    SHORT;
  - the turned knob: the 1 px frame and an amber label, on its row only.
The default (CC 11 not sent) is today's rows: test_ui_meters.py checks those."""
import re

import numpy as np
import pytest

import test_ui_meters as M
from hostrun import cc

VARIANTS = {1: "FADER", 2: "LINE"}
TYPES3 = [M.TYPES[0], M.TYPES[2], M.TYPES[4]]                     # ST77, FELT (the longest name), VIBE
VR, BASE, W = 197, 30, 228                                        # ui.c CD_VR, CD_BASE, RW_W (row x)
CAP_TOP = BASE - 28 + 7                                           # AF_L: asc 28, capitals from row 7 of the line
CAP_H = 21
# every value written in its short form (ui.c CD_LONG / CD_SHORT), where: (page, label, short form)
SHORT = {("FRODHES", "TYPE", "FELT")} | {("ASSIGN", "PLAYS", s) for s in
                                          ("TRM/WAH", "CMP THR", "CMP GAIN", "TRM RATE", "TRM SHP", "PHA SPD",
                                           "PHA FDBK", "CHO SPD", "DLY FDBK")}
KEYS = M.KEYS + [f"rowbox{k}" for k in range(4)] + ["cards"]
SYNC_NAMES = {"1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D", "1/2", "1/1", "2BAR", "4BAR"}


def walk(tag, cc_n, v, variant, turns=None, look=None):
    L, t, visits = [cc(50, 11, variant)] + ([cc(60, 2, look)] if look else []) + M.type_lines(cc_n, v), M.T0, []
    for i in range(M.NWALK):
        if i:
            L.append(f"{t} turn 0 1"); t += M.STEP
        if turns and i in turns:
            for k, d in turns[i]:
                L.append(f"{t} turn {2 + k} {d}")
            t += 1500
        L += [f"{t} shot"] + M.probes(t, KEYS); visits.append(t); t += 10
    r = M.run(tag, L, t + 50, check=False)
    st = M.read_probes(r)
    return M.distinct([(t, M.ppm(tag, t), st.get(t, {})) for t in visits])


def rowbox(s):
    p = [int(x) for x in (s or "").split()]
    return dict(zip(["fit", "short", "fw", "vx0", "vx1", "ux0", "ux1", "lx1", "base"], p)) if len(p) == 9 else None


def want_fw(kind, v, hi, card, label):
    """the meter's width the rules give, or None (a synced knob: its note value's place, not read here)"""
    if not hi or card == "OFF":
        return 0
    if kind == "choice":
        return W * (v + 1) // (hi + 1)
    if card in SYNC_NAMES:
        return None
    return W * v // hi


def check_row(im, s, k, variant, w):
    errs = []
    kind, v, hi = M.parse_row(s.get(f"row{k}"))
    b = rowbox(s.get(f"rowbox{k}"))
    x0, y0 = M.ROW_X0, M.ROW_Y0 + M.ROW_DY * k
    if kind == "none":
        return errs
    if b is None:
        return [f"{w}: no rowbox state"]
    acc = M.accent_of(im)
    if not b["fit"]:
        errs.append(f"{w}: the value does not fit its room")
    if b["vx1"] != VR or b["base"] != BASE:
        errs.append(f"{w}: value edge {b['vx1']} / baseline {b['base']}, the rule {VR} / {BASE}")
    # one size: the value's ink rows (white cores) start on the cap row and are the cap height
    reg = M.white(im[y0:y0 + M.ROW_H, x0 + b["vx0"]:x0 + b["vx1"]])
    ys = np.nonzero(reg.any(axis=1))[0]
    if not len(ys):
        return errs + [f"{w}: no value drawn"]
    top, h = int(ys[0]), int(ys[-1] - ys[0] + 1)
    if abs(top - CAP_TOP) > 1 or h < CAP_H - 2:
        errs.append(f"{w}: the value's ink rows {top}..{ys[-1]} (height {h}), the large face's {CAP_TOP}.. (height {CAP_H})")
    xs = np.nonzero(M.ink(im[y0 + CAP_TOP:y0 + BASE, x0 + b["vx0"] - 4:x0 + b["vx1"] + 2]).any(axis=0))[0]
    if len(xs) and not VR - 9 <= b["vx0"] - 4 + int(xs[-1]) <= VR + 1:
        errs.append(f"{w}: the value's ink ends at row x {b['vx0'] - 4 + int(xs[-1])}, the edge is {VR}")
    # the unit on the value's baseline
    if b["ux1"]:
        u = M.ink(im[y0:y0 + M.ROW_H, x0 + b["ux0"]:x0 + b["ux1"] + 1])
        uy = np.nonzero(u.any(axis=1))[0]
        if not len(uy) or abs(int(uy[-1]) - (BASE - 1)) > 1:
            errs.append(f"{w}: the unit's ink ends on row {uy[-1] if len(uy) else None}, the baseline is {BASE - 1}")
    # nothing crosses the text: the boxes (grown by 1 px) hold no accent (FADER: the edge) / no amber at all (LINE)
    boxes = [("label", 6, max(b["lx1"], 6 + 14), 4, BASE - 1), ("value", b["vx0"], b["vx1"], CAP_TOP, BASE - 1)]
    if b["ux1"]:
        boxes.append(("unit", b["ux0"], b["ux1"], BASE - 10, BASE - 1))
    for name, a, c, ya, yb in boxes:
        sub = im[y0 + ya - 1:y0 + yb + 2, x0 + a - 1:x0 + c + 2]
        bad = M.amberish(sub) if variant == 2 and not LOOK_C[0] else M.is_acc(sub, acc)   # (look C tints its text)
        if bad.sum():
            errs.append(f"{w}: {int(bad.sum())} {'accent' if variant == 1 else 'amber'} px inside the {name}")
    # the meter: the firmware's width is the rule's, the screen's the firmware's
    fw = want_fw(kind, v, hi, s.get(f"card{k}"), s.get(f"label{k}"))
    if fw is not None and abs(b["fw"] - fw) > 1:
        errs.append(f"{w}: meter {b['fw']} px, the rule says {fw}")
    if variant == 1:
        band = M.amberish(im[y0 + M.ROW_H - 4:y0 + M.ROW_H, x0:x0 + W]).sum(axis=0) >= 2
    else:
        band = M.amberish(im[y0 + 34:y0 + 37, x0:x0 + W]).sum(axis=0) >= 2
    end = int(np.nonzero(band)[0][-1]) + 1 if band.any() else 0
    if abs(end - b["fw"]) > 1:
        errs.append(f"{w}: the meter ends at {end} on the screen, the firmware says {b['fw']}")
    inner = im[y0 + 1:y0 + M.ROW_H - 1, x0 + 1:x0 + W - 1]
    if b["fw"] == 0 and (M.is_acc(inner, acc) if LOOK_C[0] else M.amberish(inner)).sum():   # (look C: tinted text)
        errs.append(f"{w}: amber in a row at 0 (a stray edge?)")
    errs += M.check_value_clear(im, M.row_box(im, k), w)
    return errs


def check_page(im, s, where, variant):
    if s.get("cards") != str(variant):
        return [f"{where}: cards = {s.get('cards')}, not {variant}"]
    if s.get("page") in M.SPECIAL or M.title_of(s) in M.SPECIAL:
        return []
    errs = []
    for k in range(4):
        w = f"{where} K{k + 1} {s.get(f'label{k}')}={s.get(f'card{k}')} ({s.get(f'row{k}')})"
        errs += check_row(im, s, k, variant, w)
    return errs


LOOK_C = [False]
_pages = {}


def pages(name, cc_n, v, variant):
    key = (name, variant)
    if key not in _pages:
        _pages[key] = walk(f"cards{variant}_{name}", cc_n, v, variant)
    return _pages[key]


@pytest.mark.parametrize("variant", [1, 2], ids=["FADER", "LINE"])
@pytest.mark.parametrize("name,cc_n,v", TYPES3, ids=[t[0] for t in TYPES3])
def test_cards_every_page(name, cc_n, v, variant):
    """every page at its defaults, then with each knob turned (amounts +9, choices +1)"""
    ps = pages(name, cc_n, v, variant)
    assert len(ps) >= 20, [M.title_of(s) for _, _, s in ps]
    errs = []
    for t, im, s in ps:
        errs += check_page(im, s, f"{VARIANTS[variant]} {name} {M.title_of(s)} @{t}", variant)
    turned = walk(f"cards{variant}_{name}_turned", cc_n, v, variant, M.turns_for(ps))
    for t, im, s in turned:
        errs += check_page(im, s, f"{VARIANTS[variant]} {name} turned {M.title_of(s)} @{t}", variant)
    assert not errs, "\n".join(errs[:40])


@pytest.mark.parametrize("variant", [1, 2], ids=["FADER", "LINE"])
def test_cards_every_choice_option(variant):
    """every choice card of both engines through every option: the same rules at each (the fill / line at its place,
    OFF empty, the name the large face)"""
    errs, n = [], 0
    for name, cc_n, v in (M.TYPES[0], M.TYPES[4]):
        ps = pages(name, cc_n, v, variant)
        L, t, marks = [cc(50, 11, variant)] + M.type_lines(cc_n, v), M.T0, []
        for i, (_, _, s) in enumerate(ps):
            if i:
                L.append(f"{t} turn 0 1"); t += 400
            if s.get("page") in M.SPECIAL or M.title_of(s) in M.SPECIAL:
                continue
            for k in range(4):
                kind, v0, hi = M.parse_row(s.get(f"row{k}"))
                if kind != "choice" or s.get(f"label{k}") in ("TYPE", "CHANNEL"):
                    continue
                for _ in range(hi + 1):
                    L.append(f"{t} turn {2 + k} -1"); t += 20
                t += 1100
                for o in range(hi + 1):
                    if o:
                        L.append(f"{t} turn {2 + k} 1"); t += 1100
                    L += [f"{t} shot"] + M.probes(t, ["cards", "title", "page", f"row{k}", f"rowbox{k}", f"label{k}",
                                                       f"card{k}"]); marks.append((k, t)); t += 10
                for d in [-1] * (hi + 1) + [1] * v0:
                    L.append(f"{t} turn {2 + k} {d}"); t += 20
                t += 1100
        tag = f"cards{variant}_choices_{name}"
        st = M.read_probes(M.run(tag, L, t + 50, check=False))
        for k, tt in marks:
            s = st.get(tt, {})
            w = f"{VARIANTS[variant]} {name} {M.title_of(s)} K{k + 1} {s.get(f'label{k}')}={s.get(f'card{k}')} @{tt}"
            if M.parse_row(s.get(f"row{k}"))[0] != "choice" or s.get("cards") != str(variant):
                errs.append(f"{w}: row {s.get(f'row{k}')}, cards {s.get('cards')}"); continue
            errs += check_row(M.ppm(tag, tt), s, k, variant, w)
            n += 1
    assert n >= 60, n
    assert not errs, "\n".join(errs[:40])


@pytest.mark.parametrize("look", [1, 2], ids=["B", "C"])
@pytest.mark.parametrize("variant", [1, 2], ids=["FADER", "LINE"])
def test_cards_other_looks(variant, look):
    """the colour candidates B and C (developer CC 2) with each variant: the same rules on every page (VIBES, turned)"""
    ps = pages("VIBE", 79, 64, variant)
    LOOK_C[0] = look == 2
    a_lab = np.array(M.look_tokens(0)[1]["lab"], float)
    lab = np.array(M.look_tokens(look)[1]["lab"], float)
    try:
        errs = []
        M.NORM = a_lab / np.maximum(lab, 1)                       # (look C's text tint undone, as test_ui_meters)
        for t, im, s in walk(f"cards{variant}_look{look}", 79, 64, variant, M.turns_for(ps), look=look):
            errs += check_page(im, s, f"{VARIANTS[variant]} look {'BC'[look - 1]} {M.title_of(s)} @{t}", variant)
    finally:
        LOOK_C[0] = False
        M.NORM = np.ones(3)
    assert not errs, "\n".join(errs[:40])


def test_fitscan():
    """every value of every card (every TYPE, the switches as they are and all on, the synced note values) fits the
    large face; the short forms used are exactly SHORT"""
    r = M.run("cards_fitscan", [f"{M.T0} expect state fitscan 0"], M.T0 + 50, check=False)
    bad = re.findall(r"^FITSCAN BAD (.*)$", r.out, re.M)
    assert not bad, sorted(set(bad))[:40]
    assert re.search(r'expect state fitscan = "0"', r.out) or "ok   expect state fitscan" in r.out, r.out[-2000:]
    got = set()
    for page, label, val in re.findall(r"^FITSCAN SHORT (\S+)(?: \d)? K\d (.+?) = (.+?) -?\d+ -?\d+$", r.out, re.M):
        got.add((page, label, val))
    assert got == SHORT, (sorted(got - SHORT), sorted(SHORT - got))


@pytest.mark.parametrize("variant", [1, 2], ids=["FADER", "LINE"])
def test_cards_hot_frame(variant):
    """turn K1..K4 on REVERB one at a time: that row alone framed with an amber label while turned, none at 1.5 s"""
    ps = pages("ST77", 80, 8, variant)
    pos = [M.title_of(s) for _, _, s in ps].index("REVERB")
    L, t = [cc(50, 11, variant)] + M.type_lines(80, 8), M.T0
    for _ in range(pos):
        L.append(f"{t} turn 0 1"); t += 100
    t += 500
    marks = []
    for k in range(4):
        L.append(f"{t} turn {2 + k} 2")
        for dt in (200, 1500):
            L += [f"{t + dt} shot"] + M.probes(t + dt, ["hot", "title", "page"]); marks.append((k, dt, t + dt))
        t += 2000
    tag = f"cards{variant}_hot"
    M.run(tag, L, t, check=False)
    errs = []
    for k, dt, tt in marks:
        im = M.ppm(tag, tt)
        acc = M.accent_of(im)
        for j in range(4):
            box = M.row_box(im, j)
            fo, tb = M.frame_on(im, box, acc)
            x0, y0 = M.ROW_X0, M.ROW_Y0 + M.ROW_DY * j               # the label's accent cores (its box)
            lab = int(M.is_acc(im[y0 + BASE - 10:y0 + BASE, x0 + 6:x0 + 6 + 40], acc).sum())
            on = dt < 1000 and j == k
            if on and (fo < 0.8 or lab < 8):
                errs.append(f"{VARIANTS[variant]} K{k + 1} +{dt}: row {j + 1} no frame ({fo:.2f}) / label ({lab})")
            if not on and tb >= 0.3:
                errs.append(f"{VARIANTS[variant]} K{k + 1} +{dt}: row {j + 1} framed ({tb:.2f})")
    assert not errs, "\n".join(errs)


def test_default_is_today():
    """no CC 11: the rows as today (cards 0), whatever the build's variants"""
    r = M.run("cards_default", [f"{M.T0} expect state cards 0"], M.T0 + 50, check=False)
    assert 'ok   expect state cards' in r.out, r.out[-1500:]
