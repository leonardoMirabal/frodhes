# SPDX-License-Identifier: GPL-3.0-only
"""M6-02 / M5-01: Frodhes' own screen on the Move (move/module/screen.js, option F) and the FX on the pages.
The screen's behaviour runs under node (move/ui/test_screen.mjs: the overview, the large card and its return, the
knob arithmetic, the pages, the sustain button, the reads); the host renderer (tools/move_ui_shots.mjs) draws every
page in the framebuffer's two colours; the screen's pages are the hierarchy's levels; the effects are all on pages,
eight knobs at most, and the FX button (the bypass) is off by default as on the FM-1."""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_move_module as gen  # noqa: E402

OUT, M, REACH = gen.outputs()
HIER = json.loads(OUT["ui_hierarchy.json"])["levels"]
CHAIN = {c["key"]: c for c in json.loads(OUT["chain_params.json"])}
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="no node")


def screen_pages():
    js = OUT["screen.js"]
    i = js.index("const FR_PAGES = ") + len("const FR_PAGES = ")
    return json.JSONDecoder().raw_decode(js[i:])[0]


@needs_node
def test_screen_behaviour():
    r = subprocess.run([NODE, os.path.join(ROOT, "move", "ui", "test_screen.mjs")], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@needs_node
def test_chain_ui_behaviour():
    """Frodhes' screen as the whole Move UI (move/module/ui_chain.js, move/ui/test_chain.mjs): the hooks, the knobs,
    the arrows and their LEDs, Back, the menu, SAVE TO USER, the double tap; the pads and steps never touched (no
    note claimed, no LED, no param, in every mode: the 2026-10-30 pad LED report)"""
    r = subprocess.run([NODE, os.path.join(ROOT, "move", "ui", "test_chain.mjs")], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@needs_node
def test_grid_through_schwungs_controller():
    """Frodhes' pages through Schwung's own knob-grid controller (move/ui/test_grid.mjs): the VOICE pages follow TYPE
    (a knob, a preset, a program change), no PEDAL cell, PAGES opens, the FX switch, the labels fit"""
    r = subprocess.run([NODE, os.path.join(ROOT, "move", "ui", "test_grid.mjs")], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@needs_node
def test_grid_gr_cell():
    """the GR meter on Schwung's own knob grid (move/module/canvas.js, a custom widget on COMPRESSOR knobs 7-8):
    move/ui/test_cell.mjs, through Schwung's planner and Movy renderer too when its source is at hand"""
    r = subprocess.run([NODE, os.path.join(ROOT, "move", "ui", "test_cell.mjs")], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def read_png(path):
    with open(path, "rb") as f:
        b = f.read()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", b[16:26])
    pos, idat = 8, b""
    while pos < len(b):
        n = struct.unpack(">I", b[pos:pos + 4])[0]
        if b[pos + 4:pos + 8] == b"IDAT":
            idat += b[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = zlib.decompress(idat)
    return w, h, depth, ctype, {raw[y * (w + 1) + 1 + x] for y in range(h) for x in range(w)}


@needs_node
def test_shots_every_page_in_true_colours(tmp_path):
    r = subprocess.run([NODE, os.path.join(ROOT, "tools", "move_ui_shots.mjs"), "--out", str(tmp_path), "--scale",
                        "1"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    pages = screen_pages()
    slug = lambda t: re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", t.lower()))    # noqa: E731
    shots = {f[:-4] for f in os.listdir(tmp_path) if f.endswith(".png") and f != "sheet.png"}
    for p in pages:                     # every page's overview, and the large card of each knob with a control
        assert slug(p["title"]) in shots, p["title"]
        for i, k in enumerate(p["knobs"]):
            assert (f"{slug(p['title'])}-k{i + 1}" in shots) == (k is not None), (p["title"], i)
    docs = {f[len("move-"):-4] for f in os.listdir(os.path.join(ROOT, "docs", "manual", "img", "move"))}
    assert docs <= shots, docs - shots  # the docs' pictures are drawn too (the same names)
    for f in shots:
        w, h, depth, ctype, vals = read_png(os.path.join(tmp_path, f + ".png"))
        assert (w, h, depth, ctype) == (128, 64, 8, 0), f
        assert vals <= {0, 255} and 255 in vals, f                   # the two colours of the screen, nothing else
    assert os.path.exists(os.path.join(tmp_path, "sheet.png"))


@needs_node
def test_move_doc_shots_current():
    """every picture in docs/manual/img/move/ is what tools/move_ui_shots.mjs draws from today's ui_chain.js (the
    file the Move runs), none missing or left over, and every link to one in docs/MOVE.md, docs/MOVE-DEV.md,
    README.md and docs/MANUAL.md carries its hash: a change to the screen without `--docs` fails here"""
    r = subprocess.run([NODE, os.path.join(ROOT, "tools", "move_ui_shots.mjs"), "--check"], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def test_screen_pages_are_the_levels():
    pages = screen_pages()
    assert [p["title"] for p in pages] == [lvl["name"] for lvl in HIER.values()]
    for p, (lk, lvl) in zip(pages, HIER.items()):
        it = lambda k: next(i for i in lvl["params"] if i.get("key") == k)    # noqa: E731
        # (a canvas cell is no knob on the screen; nor the GR meter's read-only cells: the screen draws its own meter;
        # VERSION is: a card of text, gen.VERSION_KEY)
        knobs = [k for k in lvl["knobs"] if k is not None and it(k)["type"] != "canvas"
                 and (it(k).get("access") != "read" or k in gen.VERSION_KEY)]
        got = [k["key"] for k in p["knobs"] if k is not None and k["type"] != "preset"]
        assert got == knobs, lk
        assert p["gate"] == lvl.get("visible_if", {}).get("equals", -1)
        for k in p["knobs"]:
            if k is None or k["type"] == "preset":
                continue
            c = CHAIN[k["key"]]
            if k["key"] in gen.VERSION_KEY:              # (read only: the screen's text card, the grid's string cell)
                assert k["type"] == "text" and k["name"] == c["name"] and c["access"] == "read"
                continue
            assert k["name"] == c["name"] and k["type"] == c["type"]
            assert k.get("options") == c.get("options") and k.get("min") == c.get("min")
    # (the PRESET card in the screen cell's place, its name over the holes 6-7, DELAY's mix on knob 8: 2026-10-30)
    assert pages[0]["root"] and [k and k["type"] for k in pages[0]["knobs"][4:]] == ["preset", None, None, "int"]
    assert pages[0]["knobs"][7]["key"] == "p_delay" and pages[0]["knobs"][7]["name"] == "DELAY"


def test_screen_cell_on_the_first_page():
    cell = next(p for p in HIER["root"]["params"] if p.get("key") == gen.SCREEN["key"])
    assert cell["type"] == "canvas" and cell["canvas_script"] == "screen.js" and cell["enterable"] is True
    assert cell["show_footer"] is False and len(cell["extra_keys"]) <= 4          # (the host reads at most four)
    assert cell["fullscreen_live_ms"] >= 50
    assert gen.SCREEN["key"] not in CHAIN, "a screen, not a value: no key in the plugin"
    assert "screen.js" in gen.FILES and "screen.js" in open(os.path.join(ROOT, "Makefile")).read()
    js = OUT["screen.js"]
    assert js.count("globalThis.") <= 3 and "(function () {" in js, "the screen keeps to its closure"
    assert '"sus_cc":%d' % gen.SUS_BTN_CC in js


FX_PAGES = ("COMPRESSOR", "DRIVE/FILTER", "TREM/PHASER", "CHORUS", "DELAY", "REVERB", "LFO/SETUP")
FX_PARAMS = ("P_CMP_", "P_DRV_", "P_DRIVE", "P_FL_", "P_TW_", "P_PH_", "P_CP_", "P_DL_", "P_RV_", "P_LFO_")


def test_fx_pages_hold_every_effect():
    """M5-01: every effect parameter is on an effect page (or reached through another key, or MIDI-only with a
    reason), each page of at most 8 knobs"""
    names = {lvl["name"]: lvl for lvl in HIER.values()}
    for t in FX_PAGES:
        assert t in names and 1 <= len(names[t]["knobs"]) <= 8, t
    for p in M["pids"]:
        if not p.startswith(FX_PARAMS):
            continue
        where = REACH.get(p, "")
        ok = where in FX_PAGES or (p.endswith("_SYNC") and where == "TEMPO")   # (the syncs: with the tempo)
        assert ok or where.startswith("via ") or p in gen.MIDI_ONLY, (p, where)


def test_fx_button_default_on():
    """M5-01: the FM-1's FX button (CC 111, the bypass) on the first page; its default, as on the FM-1, is the
    effects on (not bypassed). OFF / ON, so 1 = on: Schwung's Movy draws a two-option switch on at option 1 (0.9.8
    candidate: ON / OFF drew the switch off while the effects played)"""
    c = CHAIN["s_fx"]
    assert c["options"] == ["OFF", "ON"] and gen.EXTRA["s_fx"]["cc"] == 111
    assert "s_fx" in HIER["root"]["knobs"]
    fx = open(os.path.join(ROOT, "firmware", "src", "fx.c")).read()
    assert "static volatile uint8_t fx_bypass;" in fx, "the bypass starts off (a zeroed static)"
    br = open(os.path.join(ROOT, "move", "move_bridge.h")).read()
    assert "k->cc == 111u ? fx_bypass == 0" in br, "s_fx reads the bypass back (1: the effects on)"


def test_move_doc_pictures():
    """docs/MOVE.md's pictures exist and their ?v= is the file's hash (tools/move_ui_shots.mjs --docs)"""
    import hashlib
    import re
    doc = open(os.path.join(ROOT, "docs", "MOVE.md")).read()
    links = re.findall(r"\]\((manual/img/move/move-[\w-]+\.png)\?v=([0-9a-f]+)\)", doc)
    assert len(links) >= 5
    for rel, v in links:
        with open(os.path.join(ROOT, "docs", rel), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest()[:8] == v, rel
        w, h, *_ = read_png(os.path.join(ROOT, "docs", rel))
        assert (w, h) == (512, 256), rel


def test_fx_button_through_the_plugin():
    """M5-01 on dsp.so: s_fx reads 1 (the effects on) at the start, 0 (bypassed) after a write of 0 or MIDI CC 111 >= 64
    (the FM-1's bypass CC), 1 again"""
    sys.path.insert(0, os.path.dirname(__file__))
    import test_move_params as tmp
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join([
        "render 20", "get s_fx", "set s_fx 0", "render 5", "get s_fx", "set s_fx 1", "render 5", "get s_fx",
        "midi 2 176 111 127", "render 5", "get s_fx", "midi 2 176 111 0", "render 5", "get s_fx"]) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    got = [ln.split("\t")[1] for ln in r.stdout.splitlines() if ln.startswith("s_fx\t")]
    assert got == ["1", "0", "1", "0", "1"], r.stdout[-2000:] + r.stderr[-2000:]
