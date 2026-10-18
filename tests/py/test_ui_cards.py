# SPDX-License-Identifier: GPL-3.0-only
"""The design pass's rows (developer CC 11, ui.c draw_row_cards), checked on host screenshots of every page of three
TYPEs (STAGE 77, EARLY FELT, VIBES), at their defaults and with every knob turned, at the large face's sizes (host
builds with -DUI_VAL_PX=28 / 25 / 24). Variants: 1 FADER, 2 LINE (one meter for every card); 3 A, 4 B, 5 C (the card
kinds told apart: an amount the FADER fill; a selector SEGMENTS (A, C) or PIPS (B); a toggle LED (A, B) or BACK (C)).
The rules (private/ui-simplify/design-pass; the user 2026-10-18: "a clean, solid and elegant ui", "not clear what is
a toggle or selector and what is a fader", "some margin is needed, maybe 10 % smaller"):

  - one value size: every value's ink starts on the same row and is the large face's cap height (measured on the
    screen), in every card kind;
  - one value edge (every value ends at the same x), units in their column right of it, on the value's baseline;
  - nothing crosses the text: no accent pixel (LINE: no amber at all) inside the K tag + label, value and unit boxes;
  - each card's kind: the firmware's style (rowbox STYLE) is the kind's (an amount, a selector: a choice or a synced
    knob's note value, a toggle: two options reading ON / OFF), and the screen shows it:
      FADER the fill to the amount (+-1 px), nothing amber at 0;   LINE the line to the amount;
      SEGMENTS n blocks at the foot, the first lit + 1 amber (OFF: none);   PIPS n pips, the lit one amber;
      LED a lit dot ON, a ring OFF, no fill;   BACK the whole card lit ON, nothing amber OFF;
  - every value of every card fits the large face (host_state "fitscan": 0 at each size and variant), the short
    forms used are exactly SHORT (or fewer, on the smaller faces);
  - the turned knob: the 1 px frame and an amber label, on its row only.
The default (CC 11 not sent) is today's rows: test_ui_meters.py checks those."""
import os
import re
import subprocess

import numpy as np
import pytest

import hostrun
import test_ui_meters as M
from hostrun import cc

NAMES = {1: "FADER", 2: "LINE", 3: "A", 4: "B", 5: "C"}
TYPES3 = [M.TYPES[0], M.TYPES[2], M.TYPES[4]]                     # ST77, FELT (the longest name), VIBE
W, RH = 228, 38                                                   # ui.c RW_W, RW_H (row x / y)
STYLE = {1: {"amount": "F", "select": "F", "toggle": "F"}, 2: {"amount": "L", "select": "L", "toggle": "L"},
         3: {"amount": "F", "select": "S", "toggle": "D"}, 4: {"amount": "F", "select": "P", "toggle": "D"},
         5: {"amount": "F", "select": "S", "toggle": "B"}}
# every value written in its short form (ui.c CD_LONG / CD_SHORT) in the default build (24 px, breathing):
# (page, label, short form); the other sizes: no value that does not fit
SHORT = {("ASSIGN", "PLAYS", s) for s in ("TRM/WAH", "CMP GAIN", "TRM RATE", "TRM SHP", "PHA SPD", "PHA FDBK",
                                          "CHO SPD", "DLY FDBK")}
DEF_PX = 24                                                       # aatext.c UI_VAL_PX
KEYS = M.KEYS + [f"rowbox{k}" for k in range(4)] + ["cards"]
SYNC_NAMES = {"1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D", "1/2", "1/1", "2BAR", "4BAR"}
LOOK_C = [False]


def type_lines(cc_n, v):
    return [cc(100, cc_n, v)]


def binary(px):
    """the host build with the large face at px (DEF_PX: the normal one)"""
    if px == DEF_PX:
        hostrun.build()
        return hostrun.BIN
    b = os.path.join(hostrun.ROOT, "build", "host", f"frodhes_v{px}")
    src = [os.path.join(hostrun.ROOT, "firmware", d) for d in ("src", "gen")]
    newest = max(os.path.getmtime(os.path.join(d, f)) for d in src for f in os.listdir(d))
    if not os.path.exists(b) or os.path.getmtime(b) < newest:
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"',
                        f"-DUI_VAL_PX={px}", "-Ifirmware/src", "-Ifirmware/gen", "-o", b, "firmware/src/frodhes.c",
                        "-lm"], cwd=hostrun.ROOT, check=True)
    return b


def run(tag, lines, end, px=DEF_PX):
    return hostrun.run(tag, lines, end, binary=binary(px), check=False)


def walk(tag, cc_n, v, variant, turns=None, look=None, px=DEF_PX):
    L, t, visits = [cc(50, 11, variant)] + ([cc(60, 2, look)] if look else []) + type_lines(cc_n, v), M.T0, []
    for i in range(M.NWALK):
        if i:
            L.append(f"{t} turn 0 1"); t += M.STEP
        if turns and i in turns:
            for k, d in turns[i]:
                L.append(f"{t} turn {2 + k} {d}")
            t += 1500
        L += [f"{t} shot"] + M.probes(t, KEYS); visits.append(t); t += 10
    st = M.read_probes(run(tag, L, t + 50, px))
    return M.distinct([(t, M.ppm(tag, t), st.get(t, {})) for t in visits])


def rowbox(s):
    p = (s or "").split()
    if len(p) != 13:
        return None
    b = dict(zip(["fit", "short", "fw", "vx0", "vx1", "ux0", "ux1", "lx1", "base", "cap"], map(int, p[:10])))
    b.update(style=p[10], n=int(p[11]), lit=int(p[12]))
    return b


def card_kind(kind, hi, card):
    if kind == "choice":
        return "toggle" if hi == 1 and card in ("ON", "OFF") else "select"
    return "select" if card in SYNC_NAMES else "amount"


def want_fw(kind, v, hi, card):
    """the FADER / LINE width the rules give, or None (a synced knob: its note value's place, not read here)"""
    if not hi or card == "OFF":
        return 0
    if kind == "choice":
        return W * (v + 1) // (hi + 1)
    if card in SYNC_NAMES:
        return None
    return W * v // hi


def nonbg_runs(band):
    """[(a, b)] column runs of a band that differ from black"""
    return M.runs((np.abs(band).sum(axis=-1) > 12).any(axis=0))


def check_row(im, s, k, variant, w, geo):
    errs = []
    kind, v, hi = M.parse_row(s.get(f"row{k}"))
    b = rowbox(s.get(f"rowbox{k}"))
    x0, y0 = M.ROW_X0, M.ROW_Y0 + M.ROW_DY * k
    if kind == "none":
        return errs
    if b is None:
        return [f"{w}: no rowbox state"]
    card = s.get(f"card{k}")
    acc = M.accent_of(im)
    base, cap, vr = b["base"], b["cap"], b["vx1"]
    if geo.setdefault("geo", (base, cap, vr)) != (base, cap, vr):
        errs.append(f"{w}: baseline / cap / value edge {(base, cap, vr)}, the other rows {geo['geo']}")
    if not b["fit"]:
        errs.append(f"{w}: the value does not fit its room")
    # one size: the value's ink rows (white cores) start on the cap row and are the cap height
    ys = np.nonzero(M.white(im[y0:y0 + RH, x0 + b["vx0"]:x0 + vr]).any(axis=1))[0]
    if not len(ys):
        return errs + [f"{w}: no value drawn"]
    top, h = int(ys[0]), int(ys[-1] - ys[0] + 1)
    if abs(top - (base - cap)) > 1 or h < cap - 2:
        errs.append(f"{w}: the value's ink rows {top}..{ys[-1]} (height {h}), the face's {base - cap}.. ({cap})")
    xs = np.nonzero(M.ink(im[y0 + base - cap:y0 + base, x0 + b["vx0"] - 4:x0 + vr + 2]).any(axis=0))[0]
    if len(xs) and not vr - 9 <= b["vx0"] - 4 + int(xs[-1]) <= vr + 1:
        errs.append(f"{w}: the value's ink ends at row x {b['vx0'] - 4 + int(xs[-1])}, the edge is {vr}")
    if b["ux1"]:                                                  # the unit on the value's baseline
        uy = np.nonzero(M.ink(im[y0:y0 + RH, x0 + b["ux0"]:x0 + b["ux1"] + 1]).any(axis=1))[0]
        if not len(uy) or abs(int(uy[-1]) - (base - 1)) > 1:
            errs.append(f"{w}: the unit's ink ends on row {uy[-1] if len(uy) else None}, the baseline is {base - 1}")
    boxes = [("label", 6, max(b["lx1"], 6 + 14), 4, base - 1), ("value", b["vx0"], vr, base - cap, base - 1)]
    if b["ux1"]:
        boxes.append(("unit", b["ux0"], b["ux1"], base - 10, base - 1))
    for name, a, c, ya, yb in boxes:                              # nothing crosses the text
        sub = im[y0 + ya - 1:y0 + yb + 2, x0 + a - 1:x0 + c + 2]
        bad = M.amberish(sub) if variant == 2 and not LOOK_C[0] else M.is_acc(sub, acc)
        if bad.sum():
            errs.append(f"{w}: {int(bad.sum())} accent / amber px inside the {name}")
    # the card's kind, drawn by its rule
    ck = card_kind(kind, hi, card)
    st = STYLE[variant][ck]
    if b["style"] != st:
        return errs + [f"{w}: drawn as {b['style']}, a {ck} is {st} in {NAMES[variant]}"]
    if ck != "select" or card not in SYNC_NAMES:                  # (a synced knob: its note value's index)
        want_lit = -1 if card == "OFF" else v
        if st in "SPDB" and b["lit"] != want_lit:
            errs.append(f"{w}: lit {b['lit']}, the option is {want_lit}")
        if st in "SP" and b["n"] != hi + 1:
            errs.append(f"{w}: {b['n']} options drawn, it has {hi + 1}")
    row = im[y0:y0 + RH, x0:x0 + W]
    amb = (lambda a: M.is_acc(a, acc)) if LOOK_C[0] else M.amberish
    inner = row[1:RH - 1, 1:W - 1]
    if st in "FL":
        fw = want_fw(kind, v, hi, card)
        if fw is not None and abs(b["fw"] - fw) > 1:
            errs.append(f"{w}: meter {b['fw']} px, the rule says {fw}")
        band = M.amberish(row[RH - 4:RH] if st == "F" else row[34:37]).sum(axis=0) >= 2
        end = int(np.nonzero(band)[0][-1]) + 1 if band.any() else 0
        if abs(end - b["fw"]) > 1:
            errs.append(f"{w}: the meter ends at {end} on the screen, the firmware says {b['fw']}")
        if b["fw"] == 0 and amb(inner).sum():
            errs.append(f"{w}: amber in a row at 0 (a stray edge?)")
    elif st == "S":                                               # the blocks' feet: n runs, lit + 1 amber
        foot = row[RH - 2:RH, 1:W - 1]
        r = nonbg_runs(foot)
        lit_runs = M.runs(M.amberish(foot).any(axis=0))
        if len(r) != b["n"]:
            errs.append(f"{w}: {len(r)} blocks at the foot, {b['n']} options")
        if len(lit_runs) != b["lit"] + 1 if b["lit"] >= 0 else bool(len(lit_runs)):
            errs.append(f"{w}: {len(lit_runs)} lit blocks, the option is {b['lit']}")
    elif st == "P":                                               # n pips, the lit one amber
        pips = row[base + 4:base + 7, 1:W - 1]
        r = nonbg_runs(pips)
        lr = M.runs(M.is_acc(pips, acc).any(axis=0))
        if len(r) != b["n"]:
            errs.append(f"{w}: {len(r)} pips, {b['n']} options")
        if b["lit"] >= 0 and (len(lr) != 1 or len(r) != b["n"] or lr[0][0] != r[b["lit"]][0]):
            errs.append(f"{w}: the amber pip {lr}, the option {b['lit']} of {r}")
        if b["lit"] < 0 and lr:
            errs.append(f"{w}: an amber pip at OFF")
    elif st == "D":                                               # the LED: a lit dot / a ring, no fill
        lx1 = b["vx0"] - 8
        led = row[base - cap // 2 - 6:base - cap // 2 + 6, lx1 - 12:lx1]
        n_acc, n_ring = int(M.is_acc(led, acc).sum()), int((np.abs(led).sum(axis=-1) > 60).sum())
        if b["lit"] == 1 and n_acc < 60:
            errs.append(f"{w}: ON, the LED has {n_acc} accent px")
        if b["lit"] < 0 and (n_acc or n_ring < 30):
            errs.append(f"{w}: OFF, the LED has {n_acc} accent / {n_ring} ring px")
        rest = amb(inner).copy()
        rest[base - cap // 2 - 7:base - cap // 2 + 6, lx1 - 13:lx1 - 1] = False
        if rest.sum():
            errs.append(f"{w}: amber outside the LED ({int(rest.sum())} px)")
    elif st == "B":                                               # the card lit ON, nothing OFF
        band = (np.abs(row[RH - 4:RH - 1, 1:W - 1]).sum(axis=-1) > 12).sum(axis=0) >= 2   # (the dim fill)
        if b["lit"] == 1 and not band.all():
            errs.append(f"{w}: ON, the card is lit on {int(band.sum())} of {W - 2} columns")
        if b["lit"] < 0 and (amb(inner).sum() or band.any()):
            errs.append(f"{w}: OFF, the card is lit")
        if M.is_acc(inner, acc).sum():
            errs.append(f"{w}: an accent (an edge?) in a BACK card")
    errs += M.check_value_clear(im, M.row_box(im, k), w)
    return errs


def check_page(im, s, where, variant):
    if s.get("cards") != str(variant):
        return [f"{where}: cards = {s.get('cards')}, not {variant}"]
    if s.get("page") in M.SPECIAL or M.title_of(s) in M.SPECIAL:
        return []
    errs, geo = [], {}
    for k in range(4):
        w = f"{where} K{k + 1} {s.get(f'label{k}')}={s.get(f'card{k}')} ({s.get(f'row{k}')})"
        errs += check_row(im, s, k, variant, w, geo)
    return errs


_pages = {}


def pages(name, cc_n, v, variant, px=DEF_PX):
    key = (name, variant, px)
    if key not in _pages:
        _pages[key] = walk(f"cards{variant}_{px}_{name}", cc_n, v, variant, px=px)
    return _pages[key]


def every_page(name, cc_n, v, variant, px):
    ps = pages(name, cc_n, v, variant, px)
    assert len(ps) >= 20, [M.title_of(s) for _, _, s in ps]
    errs = []
    for t, im, s in ps:
        errs += check_page(im, s, f"{NAMES[variant]} {px}px {name} {M.title_of(s)} @{t}", variant)
    turned = walk(f"cards{variant}_{px}_{name}_turned", cc_n, v, variant, M.turns_for(ps), px=px)
    for t, im, s in turned:
        errs += check_page(im, s, f"{NAMES[variant]} {px}px {name} turned {M.title_of(s)} @{t}", variant)
    assert not errs, "\n".join(errs[:40])


@pytest.mark.parametrize("variant", [1, 2, 3, 4, 5], ids=[NAMES[i] for i in range(1, 6)])
@pytest.mark.parametrize("name,cc_n,v", TYPES3, ids=[t[0] for t in TYPES3])
def test_cards_every_page(name, cc_n, v, variant):
    """every page at its defaults, then with each knob turned (amounts +9, choices +1), the default face"""
    every_page(name, cc_n, v, variant, DEF_PX)


@pytest.mark.parametrize("px", [25, 28])
@pytest.mark.parametrize("variant", [3, 4, 5], ids=["A", "B", "C"])
@pytest.mark.parametrize("name,cc_n,v", [M.TYPES[2], M.TYPES[4]], ids=["FELT", "VIBE"])
def test_cards_every_page_small_faces(name, cc_n, v, variant, px):
    """the same with the other value faces (25, 28 px)"""
    every_page(name, cc_n, v, variant, px)


@pytest.mark.parametrize("px", [24, 28])
@pytest.mark.parametrize("variant", [1, 2, 3, 4, 5], ids=[NAMES[i] for i in range(1, 6)])
def test_cards_every_choice_option(variant, px):
    """every choice card of both engines through every option: each drawn by its kind's rule at each option"""
    errs, n = [], 0
    for name, cc_n, v in (M.TYPES[0], M.TYPES[4]):
        ps = pages(name, cc_n, v, variant, px)
        L, t, marks = [cc(50, 11, variant)] + type_lines(cc_n, v), M.T0, []
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
        tag = f"cards{variant}_{px}_choices_{name}"
        st = M.read_probes(run(tag, L, t + 50, px))
        for k, tt in marks:
            s = st.get(tt, {})
            w = f"{NAMES[variant]} {px}px {name} {M.title_of(s)} K{k + 1} {s.get(f'label{k}')}={s.get(f'card{k}')} @{tt}"
            if M.parse_row(s.get(f"row{k}"))[0] != "choice" or s.get("cards") != str(variant):
                errs.append(f"{w}: row {s.get(f'row{k}')}, cards {s.get('cards')}"); continue
            errs += check_row(M.ppm(tag, tt), s, k, variant, w, {})
            n += 1
    assert n >= 60, n
    assert not errs, "\n".join(errs[:40])


@pytest.mark.parametrize("look", [1, 2], ids=["B", "C"])
@pytest.mark.parametrize("variant", [1, 2, 3, 4, 5], ids=[NAMES[i] for i in range(1, 6)])
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
            errs += check_page(im, s, f"{NAMES[variant]} look {'BC'[look - 1]} {M.title_of(s)} @{t}", variant)
    finally:
        LOOK_C[0] = False
        M.NORM = np.ones(3)
    assert not errs, "\n".join(errs[:40])


@pytest.mark.parametrize("px", [28, 25, 24])
@pytest.mark.parametrize("variant", [1, 3, 4, 5])
def test_fitscan(variant, px):
    """every value of every card (every TYPE, the switches as they are and all on, the synced note values) fits the
    large face, the LED's place too; the short forms used are SHORT in the default build"""
    r = run(f"cards_fitscan{variant}_{px}", [cc(50, 11, variant), f"{M.T0} expect state fitscan 0"], M.T0 + 50, px)
    bad = re.findall(r"^FITSCAN BAD (.*)$", r.out, re.M)
    assert not bad, sorted(set(bad))[:40]
    assert 'ok   expect state fitscan' in r.out, r.out[-2000:]
    got = set(re.findall(r"^FITSCAN SHORT (\S+)(?: \d)? K\d (.+?) = (.+?) -?\d+ -?\d+$", r.out, re.M))
    if px == DEF_PX:
        assert got == SHORT, (sorted(got - SHORT), sorted(SHORT - got))
    print(f"{NAMES[variant]} {px} px short forms: {sorted(got)}")


@pytest.mark.parametrize("variant", [1, 2, 3, 4, 5], ids=[NAMES[i] for i in range(1, 6)])
def test_cards_hot_frame(variant):
    """turn K1..K4 on REVERB one at a time: that row alone framed with an amber label while turned, none at 1.5 s"""
    ps = pages("ST77", 80, 8, variant)
    pos = [M.title_of(s) for _, _, s in ps].index("REVERB")
    L, t = [cc(50, 11, variant)] + type_lines(80, 8), M.T0
    for _ in range(pos):
        L.append(f"{t} turn 0 1"); t += 100
    t += 500
    marks = []
    for k in range(4):
        L.append(f"{t} turn {2 + k} 2")
        for dt in (200, 1500):
            L += [f"{t + dt} shot", f"{t + dt} expect state rowbox{k} ~"]; marks.append((k, dt, t + dt))
        t += 2000
    tag = f"cards{variant}_hot"
    st = M.read_probes(run(tag, L, t))
    errs = []
    for k, dt, tt in marks:
        im = M.ppm(tag, tt)
        acc = M.accent_of(im)
        b = rowbox(st.get(tt, {}).get(f"rowbox{k}"))
        base = b["base"] if b else 30
        for j in range(4):
            box = M.row_box(im, j)
            fo, tb = M.frame_on(im, box, acc)
            x0, y0 = M.ROW_X0, M.ROW_Y0 + M.ROW_DY * j               # the label's accent cores (its box)
            lab = int(M.is_acc(im[y0 + base - 10:y0 + base, x0 + 6:x0 + 6 + 40], acc).sum())
            on = dt < 1000 and j == k
            if on and (fo < 0.8 or lab < 8):
                errs.append(f"{NAMES[variant]} K{k + 1} +{dt}: row {j + 1} no frame ({fo:.2f}) / label ({lab})")
            if not on and tb >= 0.3:
                errs.append(f"{NAMES[variant]} K{k + 1} +{dt}: row {j + 1} framed ({tb:.2f})")
    assert not errs, "\n".join(errs)


def test_default_is_a():
    """no CC 11: A (cards 3), the user's pick"""
    r = run("cards_default", [f"{M.T0} expect state cards 3"], M.T0 + 50)
    assert 'ok   expect state cards' in r.out, r.out[-1500:]
