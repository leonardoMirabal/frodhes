# SPDX-License-Identifier: GPL-3.0-only
"""The METERS look (private UI-SIMPLIFY-PLAN §2, §5, §10), checked on host screenshots of every page x every TYPE.

How it reads the screen: the host build (tests/host.c) runs a script per TYPE that walks every page with SELECT; at
each page it takes a screenshot (PPM: the 240 x 240 framebuffer as the firmware wrote it) and asks the firmware what
it drew, by name, with "expect state KEY ~" probes (frodhes.c host_state: an expect that fails prints the real value,
'(is "...")'). The test then measures the pixels against that state:

  title / count   the title line (y 20-42): the page name in the accent colour on the left, "n/m" grey on the right
  row0..row3      "KIND V HI" per knob row: KIND amount | detent | choice | bar | meter | none; V the value 0..HI
                  (an amount's fill fraction = V / HI; a choice's lit segment = V, its options = HI + 1; a bar: a
                  choice with more than 8 options, a position bar at V / HI)
  hot             the row with the amber frame now (0..3), or "-"
  label0..3, card0..3, page, hint: as today

Checks (plan §5):
  - no text overlaps: a value is at least 3 px clear of the label on its left; no text touches a row's border (a
    clipped value); a choice's label ends before its segments, its name starts after them;
  - an amount's (and a named detent's) row fill = V / HI of the row's width, +-1 px, with its bright edge;
  - a choice: one segment per option, the lit one = V; a bar: its accent at V / HI;
  - the amber frame only on the turned knob, for about 1 s (and the label amber with it);
  - the title amber, the count "n/m" right-aligned;
  - dynamic VOICE pages: Rhodes VOICE 1/2, 2/2, VIBES 1/3, 2/3, 3/3 with their labels, right after FRODHES, SELECT
    walking them in order both ways; no DECAY or MOTOR page; developer CC 2 (the colour candidates A B C) changes
    colours only (the same checks pass in each, the rows show the same); look C = tools/meters_palettes.py search.
The large face's small fallback (a value that does not fit 28 px) is reported, not failed.

Skips cleanly (pytest.skip) while the METERS build is not merged: host_state then has no "row0"."""
import os
import re
import warnings

import numpy as np
import pytest

import hostrun
from hostrun import OUT, cc

BIN = os.environ.get("FRODHES_HOST_BIN", hostrun.BIN)               # another build to look at (a lane's branch)


def run(tag, lines, end_ms, check=False):
    return hostrun.run(tag, lines, end_ms, binary=BIN, check=check)


TYPES = [("ST77", 80, 8), ("SUIT", 80, 40), ("FELT", 80, 72), ("DYNO", 80, 104), ("VIBE", 79, 64)]
VOICE = {"rhodes": [["TONE", "BARK", "BELL", "HAMMER"], ["DECAY", "RELEASE", "NOISE", "TUNE"]],
         "vibes": [["STRIKE", "MALLET", "TUBE", "WEIGHT"], ["RING", "DAMPER", "NOISE", "TUNE"],
                   ["MOTOR", "DEPTH", "SPEED", "SHAPE"]]}
ROW_X0, ROW_X1, ROW_Y0, ROW_DY, ROW_H = 6, 233, 46, 42, 38      # plan §2: x 6-233, y 46 + 42 k, height 38
SEG_X0, SEG_X1 = 92, 168                                          # plan §2: a choice's segments
T0, STEP, NWALK = 2500, 600, 30                                   # after the start screen and the TYPE message
PROBE = "~"
KEYS = (["title", "count", "page", "vpage", "hint", "hot"] + [f"row{k}" for k in range(4)] + [f"label{k}" for k in range(4)]
        + [f"card{k}" for k in range(4)])
SPECIAL = ("SEQUENCER",)                                          # its own layout (plan §4): title + not blank only


# ---------------------------------------------------------------- the host side

def probes(t, keys=KEYS):
    return [f"{t} expect state {k} {PROBE}" for k in keys]


def read_probes(r):
    """{t: {key: value}} from the probes' replies; a key the firmware does not know: None"""
    got = {}
    for t, k, v in re.findall(r'^\s*(\d+) ms  FAIL expect state (\S+) = "' + re.escape(PROBE) + r'" \(is "(.*)"\)$',
                              r.out, re.M):
        got.setdefault(int(t), {})[k] = v
    for t, k in re.findall(r"^\s*(\d+) ms  FAIL expect state (\S+): no such state$", r.out, re.M):
        got.setdefault(int(t), {})[k] = None
    return got


def ppm(tag, t):
    d = open(os.path.join(OUT, f"{tag}-{t}.ppm"), "rb").read()
    return np.frombuffer(d[-240 * 240 * 3:], np.uint8).reshape(240, 240, 3).astype(int)


def parse_row(s):
    """'KIND V HI' -> (kind, v, hi)"""
    p = (s or "").split()
    if not p:
        return ("none", 0, 0)
    return (p[0], int(p[1]) if len(p) > 1 else 0, int(p[2]) if len(p) > 2 else 0)


def type_lines(cc_n, v):
    return [cc(100, cc_n, v)]


def walk(tag, cc_n, v, turns=None, look=None):
    """every page by SELECT (forward to the end, then back to PERFORM); turns: {forward step: [(knob, delta)]}, turned
    at the visit and shot 1.5 s later (the frame gone); look: the colour candidate (developer CC 2) first. Returns
    [(t, image, state)] forward, then backward."""
    L, t, visits = type_lines(cc_n, v) + ([cc(50, 2, look)] if look is not None else []), T0, []
    for i in range(NWALK):
        if i:
            L.append(f"{t} turn 0 1"); t += STEP
        if turns and i in turns:
            for k, d in turns[i]:
                L.append(f"{t} turn {2 + k} {d}")
            t += 1500
        L += [f"{t} shot"] + probes(t); visits.append(("f", i, t)); t += 10
    for i in range(NWALK):
        L.append(f"{t} turn 0 -1"); t += STEP
        L += [f"{t} shot"] + probes(t, ["title", "count", "page", "vpage"]); visits.append(("b", i, t)); t += 10
    r = run(tag, L, t + 50, check=False)
    st = read_probes(r)
    fwd = [(t, ppm(tag, t), st.get(t, {})) for d, _, t in visits if d == "f"]
    bwd = [(t, None, st.get(t, {})) for d, _, t in visits if d == "b"]
    return fwd, bwd


def title_of(s):
    """the title drawn on the left: host_state "title", else "page" (the base name)"""
    return s.get("title") or s.get("page") or ""


def page_id(s):
    return (title_of(s), s.get("vpage"), s.get("count"))


def distinct(seq):
    """the pages up to the first one seen again (SELECT stops at the last page, or wraps to the first)"""
    out, seen = [], set()
    for x in seq:
        if page_id(x[2]) in seen:
            break
        seen.add(page_id(x[2]))
        out.append(x)
    return out


# ---------------------------------------------------------------- the pixels

def lowsat(im):
    return im.max(axis=2) - im.min(axis=2) <= 40


NORM = np.ones(3)                                                 # the text's tint undone (look C: set_look)


def ink(im):
    """text cores: grey to white, not tinted (the fill, the segments and the frame are amber)"""
    im = np.minimum(im * NORM, 255)
    return lowsat(im) & (im.max(axis=2) >= 90)


def white(im):
    im = np.minimum(im * NORM, 255)
    return lowsat(im) & (im.min(axis=2) >= 190)


def amberish(im):
    """any amber, the dim fill included"""
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    return (r - b >= 20) & (r >= g)


def accent_of(im):
    """the accent colour, from the title's ink (the brightest amber pixels of the title line)"""
    reg = im[20:43, 4:150].reshape(-1, 3)
    a = reg[(reg[:, 0] >= 100) & (reg[:, 0] - reg[:, 2] >= 80)]       # (look C's accent: 106 12 0)
    if len(a) < 10:
        return None
    lum = a.sum(axis=1)
    return np.median(a[lum >= np.percentile(lum, 70)], axis=0)


def is_acc(im, acc):
    return np.abs(im - acc).sum(axis=-1) <= 70


def row_box(im, k):
    """the row's rectangle (x0, x1, y0, y1 inclusive): the plan's, refined by its surface where it differs from the
    screen's background (within 3 px)"""
    bg = im[239, 0]
    x0, x1, y0 = ROW_X0, ROW_X1, ROW_Y0 + ROW_DY * k
    y1 = y0 + ROW_H - 1
    diff = np.abs(im - bg).sum(axis=-1) > 6
    for (a, b) in runs(diff[y0 - 4:y1 + 5, x0 + 2]):
        if a <= 23 <= b and abs(a - 4) <= 3 and abs(b - (ROW_H + 3)) <= 3:
            y0, y1 = y0 - 4 + a, y0 - 4 + b
    for (a, b) in runs(diff[y0 + 1]):
        if abs(a - x0) <= 3 and abs(b - x1) <= 3:
            x0, x1 = a, b
    return x0, x1, y0, y1


def runs(mask1d, gap=0):
    """[(start, end inclusive)] of True runs, merging runs separated by <= gap False columns"""
    out = []
    for i in np.nonzero(mask1d)[0]:
        if out and i - out[-1][1] <= gap + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [tuple(map(int, r)) for r in out]


def fill_end(im, box):
    """the fill's right end (exclusive) in the row's bottom lines (no text there), 0 = none"""
    x0, x1, y0, y1 = box
    band = amberish(im[y1 - 3:y1, x0:x1 + 1]).sum(axis=0) >= 2
    xs = np.nonzero(band)[0]
    return (x0 + int(xs[-1]) + 1, band) if len(xs) else (0, band)


def text_band(box):
    x0, x1, y0, y1 = box
    return y0 + 15, y1 - 5                                  # under the K tag, over the bottom lines


def check_clear_of_borders(im, box, what):
    x0, x1, y0, y1 = box
    m = ink(im)
    errs = []
    if m[y0:y1 + 1, x0:x0 + 2].any() or m[y0:y1 + 1, x1 - 1:x1 + 1].any():
        errs.append(f"{what}: text touches the row's left / right border (clipped?)")
    if m[y0, x0:x1 + 1].any() or m[y1, x0:x1 + 1].any():
        errs.append(f"{what}: text touches the row's top / bottom line (clipped?)")
    return errs


def check_value_clear(im, box, what):
    """the value (white) is >= 3 px clear of whatever ink is on its left (the label): scanning left from the value's
    first white column over its own antialiased edge, 3 empty columns must come before any other ink"""
    x0, x1, y0, y1 = box
    a, b = text_band(box)
    m = ink(im[a:b, :]).any(axis=0)
    w = np.nonzero(white(im[a:b, :]).any(axis=0)[x0 + 2:x1 - 1])[0]
    if not len(w):
        return [f"{what}: no value drawn"]
    x = x0 + 2 + int(w[0])
    while x > x0 and m[x - 1]:                              # the value's own edge (antialiasing)
        x -= 1
    free = 0
    while x > x0 + 2 and not m[x - 1]:
        free += 1; x -= 1
    if x > x0 + 2 and free < 3:
        return [f"{what}: the value is only {free} px clear of the label (x {x})"]
    return []


def value_is_small(im, box):
    a, b = box[2] + 2, box[3] - 1
    ys = np.nonzero(white(im[a:b, box[0] + 60:box[1] - 1]).any(axis=1))[0]
    return len(ys) and ys[-1] - ys[0] + 1 < 15


def segments(im, box, acc):
    """a choice's segments: [(x0, x1, lit)] in the row's segment band, and the band's columns"""
    x0, x1, y0, y1 = box
    reg = im[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    surf = np.median(im[y1 - 2, x0 + 2:x0 + 40], axis=0)   # the row surface (no fill on a choice row)
    lit = is_acc(reg, acc)
    off = (np.abs(reg - surf).sum(axis=-1) >= 12) & ~ink(reg) & ~amberish(reg)
    if off.any():                                           # the dark segments' colour: the commonest "off" colour
        cols, n = np.unique(reg[off].reshape(-1, 3), axis=0, return_counts=True)
        track = cols[np.argmax(n)]
        off = np.abs(reg - track).sum(axis=-1) <= 8
    seg = lit | off
    lines = np.nonzero(seg.sum(axis=1) >= 20)[0]
    if not len(lines):
        return [], None
    l0, l1 = lines[0], lines[-1]
    colmask = seg[l0:l1 + 1].mean(axis=0) >= 0.6
    colmask |= seg[l0] & seg[l1]                            # an outlined segment (looks A, B): its top and bottom lines
    out = []
    for a, b in runs(colmask):
        if b - a + 1 >= 3:
            out.append((x0 + 2 + a, x0 + 2 + b, bool(lit[l0:l1 + 1, a:b + 1].mean() >= 0.5)))
    return out, (y0 + 2 + l0, y0 + 2 + l1)


def frame_on(im, box, acc):
    """the 1 px amber frame around the row: each side (at the border or 1 px outside) mostly the accent"""
    x0, x1, y0, y1 = box
    m = is_acc(im, acc)

    def side(vals):
        return max(float(v.mean()) for v in vals)
    top = side([m[y, x0 + 3:x1 - 2] for y in (y0 - 1, y0)])
    bot = side([m[y, x0 + 3:x1 - 2] for y in (y1, y1 + 1)])
    lef = side([m[y0 + 3:y1 - 2, x] for x in (x0 - 1, x0)])
    rig = side([m[y0 + 3:y1 - 2, x] for x in (x1, x1 + 1)])
    return min(top, bot, lef, rig), max(top, bot)


def label_amber(im, box, acc):
    x0, x1, y0, y1 = box
    a, b = text_band(box)
    m = is_acc(im[a:b, x0 + 4:min(x0 + 84, SEG_X0 - 2)], acc)
    edge = is_acc(im[y1 - 3:y1, x0 + 4:min(x0 + 84, SEG_X0 - 2)], acc).any(axis=0)   # the fill's edge: not the label
    return int(m[:, ~edge].sum())


# ---------------------------------------------------------------- the checks of one screen

def check_screen(im, s, where, fallbacks):
    errs = []
    acc = accent_of(im)
    title = title_of(s)
    if acc is None:
        return [f"{where}: the title is not drawn in the accent colour"]
    if not re.fullmatch(r"\d+/\d+", s.get("count") or ""):
        errs.append(f"{where}: count {s.get('count')!r} is not n/m")
    cnt = ink(im[20:43, 150:238]).any(axis=0)
    xs = np.nonzero(cnt)[0]
    if not len(xs) or 150 + xs[-1] < 222:
        errs.append(f"{where}: the count is not right-aligned (ends at x {150 + xs[-1] if len(xs) else None})")
    if not (ink(im[0:19]) | amberish(im[0:19])).any():
        errs.append(f"{where}: no header")
    if s.get("page") in SPECIAL or title in SPECIAL:
        if not (ink(im[46:222]) | amberish(im[46:222])).sum() > 200:
            errs.append(f"{where}: the page is blank")
        return errs
    if s.get("hot") not in ("-", None):
        errs.append(f"{where}: hot = {s.get('hot')} with no knob turned for > 1 s")
    for k in range(4):
        kind, v, hi = parse_row(s.get(f"row{k}"))
        box = row_box(im, k)
        w = f"{where} K{k + 1} {s.get(f'label{k}')}={s.get(f'card{k}')} ({kind} {v}/{hi})"
        fo, _ = frame_on(im, box, acc)
        if fo >= 0.8:
            errs.append(f"{w}: an amber frame with no knob turned")
        if kind == "none":
            if ink(im[box[2]:box[3] + 1, box[0]:box[1] + 1]).any():
                errs.append(f"{w}: an empty row with text in it")
            continue
        if kind == "meter":
            continue
        errs += check_clear_of_borders(im, box, w)
        if kind in ("amount", "detent"):
            end, band = fill_end(im, box)
            x0, x1 = box[0], box[1]
            want = x0 + (x1 - x0 + 1) * v / hi if hi else x0
            if v <= 0:
                if end > x0 + 2:
                    errs.append(f"{w}: a fill to x {end} at 0")
            elif abs(end - want) > 1:
                errs.append(f"{w}: the fill ends at x {end}, the value says {want:.1f} (+-1)")
            elif end - x0 > 3 and not is_acc(im[box[3] - 3:box[3], end - 3:end + 1], acc).any():
                errs.append(f"{w}: the fill has no bright edge")
            errs += check_value_clear(im, box, w)
            if value_is_small(im, box) and kind == "amount":
                fallbacks.append(f"{w}: the value in the small face")
        elif kind in ("choice", "bar"):
            segs, band = segments(im, box, acc)
            a, b = text_band(box)
            if not segs:
                errs.append(f"{w}: no segments / bar")
                continue
            sx0, sx1 = segs[0][0], segs[-1][1]
            m = ink(im[a:b, :]).any(axis=0)
            if m[sx0 - 2:sx1 + 3].any():
                errs.append(f"{w}: text over or touching the segments (x {sx0}-{sx1})")
            lab = np.nonzero(m[box[0] + 2:sx0])[0]
            if len(lab) and box[0] + 2 + lab[-1] > 88:
                errs.append(f"{w}: the label runs to x {box[0] + 2 + lab[-1]} (segments from x 92)")
            if not m[sx1 + 1:box[1] - 1].any():
                errs.append(f"{w}: no choice name right of the segments")
            if kind == "choice":
                n = hi + 1
                if n > 8:
                    errs.append(f"{w}: {n} options shown as segments (more than 8: a position bar)")
                if len(segs) != n:
                    errs.append(f"{w}: {len(segs)} segments for {n} options")
                lit = [i for i, sg in enumerate(segs) if sg[2]]
                if lit != [v]:
                    errs.append(f"{w}: lit segment(s) {lit}, the value is {v}")
            else:
                if hi + 1 <= 8:
                    errs.append(f"{w}: a position bar for {hi + 1} options (8 or fewer: segments)")
                y_a, y_b = band
                acc_cols = np.nonzero(is_acc(im[y_a:y_b + 1, sx0:sx1 + 1], acc).any(axis=0))[0]
                want = sx0 + (sx1 - sx0) * v / hi if hi else sx0
                if len(acc_cols):
                    right, mid = sx0 + acc_cols[-1], sx0 + (acc_cols[0] + acc_cols[-1]) / 2
                    if min(abs(right - want), abs(mid - want)) > 3:
                        errs.append(f"{w}: the bar's mark at x {right}, the value says {want:.0f}")
                elif v:
                    errs.append(f"{w}: the bar shows nothing at {v}/{hi}")
        else:
            errs.append(f"{w}: unknown row kind {kind!r}")
    return errs


# ---------------------------------------------------------------- the runs (cached per TYPE)

_cache = {}


@pytest.fixture(scope="module")
def meters():
    """skips unless the build says what each row is (row0) and which is hot; FRODHES_METERS_FORCE=1 runs anyway (a
    lane's work in progress: the page walk checks still mean something)"""
    r = run("meters_probe", probes(T0, ["row0", "hot"]), T0 + 50, check=False)
    s = read_probes(r).get(T0, {})
    missing = [k for k in ("row0", "hot") if s.get(k, None) is None]
    if missing and not os.environ.get("FRODHES_METERS_FORCE"):
        pytest.skip(f"the METERS build is not merged (host_state has no {', '.join(missing)}: UI-SIMPLIFY lane B)")
    return True


def pages_of(name, cc_n, v):
    if name not in _cache:
        fwd, bwd = walk(f"meters_{name}", cc_n, v)
        _cache[name] = (distinct(fwd), bwd, fwd)
    return _cache[name]


def turns_for(pages):
    """the second pass: each knob turned (amounts / detents +9 detents, a choice +1, a bar +3); not TYPE, not the
    SEQUENCER, not MIDI CHANNEL (the probes are not MIDI, but keep the channel)"""
    tt = {}
    for i, (_, _, s) in enumerate(pages):
        if s.get("page") in SPECIAL or s.get("title") in SPECIAL:
            continue
        ks = []
        for k in range(4):
            kind, v, hi = parse_row(s.get(f"row{k}"))
            lab = s.get(f"label{k}")
            if kind in ("none", "meter") or lab in ("TYPE", "CHANNEL"):
                continue
            ks.append((k, 9 if kind in ("amount", "detent") else 3 if kind == "bar" else 1))
        if ks:
            tt[i] = ks
    return tt


@pytest.mark.parametrize("name,cc_n,v", TYPES, ids=[t[0] for t in TYPES])
def test_every_page_defaults(meters, name, cc_n, v):
    """every page at its defaults: geometry, fills, segments, title, no frame"""
    pages = pages_of(name, cc_n, v)[0]
    errs, fb = [], []
    for t, im, s in pages:
        errs += check_screen(im, s, f"{name} {title_of(s)} {s.get('count')} @{t}", fb)
    for f in fb:
        warnings.warn(f)
    assert not errs, "\n".join(errs)


@pytest.mark.parametrize("name,cc_n,v", TYPES, ids=[t[0] for t in TYPES])
def test_every_page_turned(meters, name, cc_n, v):
    """every page again with its knobs turned (values off their defaults), 1.5 s after (the frame gone)"""
    pages = pages_of(name, cc_n, v)[0]
    tt = turns_for(pages)
    fwd, _ = walk(f"meters_{name}_turned", cc_n, v, tt)
    errs, fb = [], []
    for t, im, s in distinct(fwd):
        errs += check_screen(im, s, f"{name} turned {title_of(s)} {s.get('count')} @{t}", fb)
    for f in fb:
        warnings.warn(f)
    assert not errs, "\n".join(errs)


def voice_pages(pages):
    return [(i, s) for i, (_, _, s) in enumerate(pages)
            if title_of(s).startswith("VOICE")]


@pytest.mark.parametrize("name,cc_n,v", TYPES, ids=[t[0] for t in TYPES])
def test_dynamic_voice_pages(meters, name, cc_n, v):
    """VOICE 1..n from the engine's table: Rhodes 2, VIBES 3, right after FRODHES, in order, with their labels;
    no DECAY or MOTOR page; the count n/m runs 1..m over the walk (or names the VOICE group)"""
    pages, bwd, fwd = pages_of(name, cc_n, v)
    eng = "vibes" if name == "VIBE" else "rhodes"
    titles = [title_of(s) for _, _, s in pages]
    assert not [t for t in titles if t in ("DECAY", "MOTOR")], titles
    vp = voice_pages(pages)
    want = VOICE[eng]
    assert len(vp) == len(want), (titles, [s.get("count") for _, _, s in pages])
    idx = [i for i, _ in vp]
    assert idx == list(range(idx[0], idx[0] + len(want))), idx                   # together
    assert title_of(pages[idx[0] - 1][2]).startswith("FRODHES"), titles          # right after FRODHES
    n = len(want)
    for j, (i, s) in enumerate(vp):
        assert [s.get(f"label{k}") for k in range(4)] == want[j], (j, s)
        shown = f"{title_of(s)} {s.get('count')} {s.get('vpage') or ''}"
        assert f"{j + 1}/{n}" in shown.replace(" / ", "/"), f"VOICE page {j + 1} shows {shown!r}"
    m = len(pages)
    for q, (_, _, s) in enumerate(pages if all(p[2].get("count") for p in pages) else []):   # a global count: 1..m
        a, b = map(int, s["count"].split("/"))
        if b != n or not title_of(s).startswith("VOICE"):
            assert (a, b) == (q + 1, m), f"page {q} {title_of(s)}: count {s['count']}, expected {q + 1}/{m}"
    ids = [page_id(s) for _, _, s in pages]                                     # SELECT back: the reverse order
    wrap = len(fwd) > m and page_id(fwd[m][2]) == ids[0]
    at = ids.index(page_id(fwd[-1][2]))
    want_back = [ids[(at - 1 - i) % m] if wrap else ids[max(at - 1 - i, 0)] for i in range(len(bwd))]
    assert [page_id(s) for _, _, s in bwd] == want_back


def test_vibes_has_one_page_more(meters):
    r = len(pages_of("ST77", 80, 8)[0])
    b = len(pages_of("VIBE", 79, 64)[0])
    assert b == r + 1, (r, b)


@pytest.mark.parametrize("eng,cc_n,v", [("rhodes", 80, 8), ("vibes", 79, 64)])
def test_hot_frame(meters, eng, cc_n, v):
    """turn K1..K4 on REVERB (four amounts), one at a time: the turned row alone gets the amber frame and an amber
    label, still there at 0.8 s, gone by 1.5 s (HOT_MS 1 s)"""
    pages = pages_of("ST77" if eng == "rhodes" else "VIBE", cc_n, v)[0]
    pos = [title_of(s) for _, _, s in pages].index("REVERB")
    L, t = type_lines(cc_n, v), T0
    for _ in range(pos):
        L.append(f"{t} turn 0 1"); t += 100
    t += 500
    marks = []
    for k in range(4):
        L.append(f"{t} turn {2 + k} 2")
        for dt in (200, 800, 1500):
            L += [f"{t + dt} shot"] + probes(t + dt, ["hot", "title", "page"]); marks.append((k, dt, t + dt))
        t += 2000
    tag = f"meters_hot_{eng}"
    r = run(tag, L, t, check=False)
    st = read_probes(r)
    errs = []
    for k, dt, tt in marks:
        im, s = ppm(tag, tt), st.get(tt, {})
        acc = accent_of(im)
        on = dt < 1000
        if title_of(s) != "REVERB":
            errs.append(f"K{k + 1} +{dt} ms: on {title_of(s)}, not REVERB")
            continue
        if s.get("hot") != (str(k) if on else "-"):
            errs.append(f"K{k + 1} +{dt} ms: hot = {s.get('hot')}")
        for j in range(4):
            box = row_box(im, j)
            fo, tb = frame_on(im, box, acc)
            lab = label_amber(im, box, acc)
            if on and j == k and (fo < 0.8 or lab < 8):
                errs.append(f"K{k + 1} +{dt} ms: row {j + 1} has no frame ({fo:.2f}) / amber label ({lab} px)")
            if (not on or j != k) and (tb >= 0.3 or lab >= 8):
                errs.append(f"K{k + 1} +{dt} ms: row {j + 1} framed ({tb:.2f}) / amber label ({lab} px)")
    assert not errs, "\n".join(errs)


LOOKS = {0: "A", 1: "B", 2: "C"}


def look_tokens(look):
    """the look's colours as the firmware has them (host_state "look"): {name: (r, g, b) as the PPM expands them}"""
    r = run(f"meters_look{look}", [cc(100, 2, look), f"{T0} expect state look ~"], T0 + 50, check=False)
    v = read_probes(r)[T0]["look"].split()
    names = ["surf", "track", "outline", "hair", "fill", "edge", "tag", "lab", "val", "dim"]
    tok = {n: int(h, 16) for n, h in zip(names, v[1:])}
    return v[0], {n: ((c >> 11) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31) for n, c in tok.items()}


@pytest.mark.parametrize("look", [0, 1, 2], ids=["A", "B", "C"])
@pytest.mark.parametrize("name,cc_n,v", [TYPES[0], TYPES[4]], ids=["ST77", "VIBE"])
def test_looks_same_geometry(meters, look, name, cc_n, v):
    """developer CC 2 (the colour candidates A / B / C, ui.c MT_LOOKS) changes colours only: every page, its knobs
    turned, passes the same checks (look C's text is tinted for the panel: NORM undoes it), and what each row shows
    is the same as in look A"""
    global NORM
    letter, tok = look_tokens(look)
    assert letter == LOOKS[look]
    a_lab = np.array(look_tokens(0)[1]["lab"], float)
    pages = pages_of(name, cc_n, v)[0]
    fwd, _ = walk(f"meters_{name}_look{look}", cc_n, v, turns_for(pages), look=look)
    ref, _ = walk(f"meters_{name}_turned", cc_n, v, turns_for(pages))
    errs, fb = [], []
    NORM = a_lab / np.maximum(np.array(tok["lab"], float), 1)
    try:
        for (t, im, s), (_, _, s0) in zip(distinct(fwd), distinct(ref)):
            errs += check_screen(im, s, f"look {letter} {name} {title_of(s)} {s.get('count')} @{t}", fb)
            for k in [f"row{k}" for k in range(4)] + ["title", "count"]:
                if s.get(k) != s0.get(k):
                    errs.append(f"look {letter} {name} {title_of(s)}: {k} = {s.get(k)!r}, look A {s0.get(k)!r}")
    finally:
        NORM = np.ones(3)
    assert not errs, "\n".join(errs)


def test_look_default_and_c_matches_the_model(meters):
    """the default look is A (no CC 2); out-of-range values clamp to C; look C's colours are the ones
    tools/meters_palettes.py search finds (the firmware and the tool agree)"""
    r = run("meters_look_default", [f"{T0} expect state look ~", cc(T0 + 10, 2, 99), f"{T0 + 50} expect state look ~"],
            T0 + 100, check=False)
    st = read_probes(r)
    assert st[T0]["look"].split()[0] == "A"
    assert st[T0 + 50]["look"].split()[0] == "C"
    import sys
    sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
    import meters_palettes as mp
    want = mp.search()
    _, tok = look_tokens(2)
    for role in ("surf", "track", "fill", "edge", "dim", "lab", "val"):
        assert tok[role] == tuple(int(x) for x in mp.expand([want[role]])[0]), (role, tok[role], want[role])
