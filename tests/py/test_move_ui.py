# SPDX-License-Identifier: GPL-3.0-only
"""M6-02 / M5-01: Frodhes' own screen on the Move (move/module/screen.js, option F) and the FX on the pages.
The screen's behaviour runs under node (move/ui/test_screen.mjs: the overview, the large card and its return, the
knob arithmetic, the pages, the sustain button, the reads); the host renderer (tools/move_ui_shots.mjs) draws every
page in the framebuffer's two colours; the screen's pages are the hierarchy's levels; the effects are all on pages,
eight knobs at most, and the FX button (the bypass) is off by default as on the FM-1."""
import json
import os
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
    want = len(pages) + sum(len(p["knobs"]) for p in pages) + 1        # the overviews, the cards, the list
    shots = [f for f in os.listdir(tmp_path) if f.endswith(".png") and f != "sheet.png"]
    assert len(shots) == want, (len(shots), want)
    for f in shots:
        w, h, depth, ctype, vals = read_png(os.path.join(tmp_path, f))
        assert (w, h, depth, ctype) == (128, 64, 8, 0), f
        assert vals <= {0, 255} and 255 in vals, f                   # the two colours of the screen, nothing else
    assert os.path.exists(os.path.join(tmp_path, "sheet.png"))


def test_screen_pages_are_the_levels():
    pages = screen_pages()
    assert [p["title"] for p in pages] == [lvl["name"] for lvl in HIER.values()]
    for p, (lk, lvl) in zip(pages, HIER.items()):
        knobs = [k for k in lvl["knobs"] if next(i for i in lvl["params"] if i.get("key") == k)["type"] != "canvas"]
        got = [k["key"] for k in p["knobs"] if k["type"] != "preset"]
        assert got == knobs, lk
        assert p["gate"] == lvl.get("visible_if", {}).get("equals", -1)
        for k in p["knobs"]:
            if k["type"] == "preset":
                continue
            c = CHAIN[k["key"]]
            assert k["name"] == c["name"] and k["type"] == c["type"]
            assert k.get("options") == c.get("options") and k.get("min") == c.get("min")
    assert pages[0]["root"] and pages[0]["knobs"][-1]["type"] == "preset"


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


FX_PAGES = ("COMPRESSOR", "DRIVE/FILTER", "TREM/PHASER", "CHORUS/DELAY", "REVERB", "LFO/SETUP")
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
        ok = where in FX_PAGES or (p.endswith("_SYNC") and where == "TEMPO/SCALE")   # (the syncs: with the tempo)
        assert ok or where.startswith("via ") or p in gen.MIDI_ONLY, (p, where)


def test_fx_button_default_on():
    """M5-01: the FM-1's FX button (CC 111, the bypass) on the first page; its default, as on the FM-1, is the
    effects on (index 0, not bypassed)"""
    c = CHAIN["s_fx"]
    assert c["options"] == ["ON", "OFF"] and gen.EXTRA["s_fx"]["cc"] == 111
    assert "s_fx" in HIER["root"]["knobs"]
    fx = open(os.path.join(ROOT, "firmware", "src", "fx.c")).read()
    assert "static volatile uint8_t fx_bypass;" in fx, "the bypass starts off (a zeroed static)"
    br = open(os.path.join(ROOT, "move", "move_bridge.h")).read()
    assert "k->cc == 111u ? fx_bypass != 0" in br, "s_fx reads the bypass back"


def test_move_doc_pictures():
    """docs/MOVE.md's pictures exist and their ?v= is the file's hash (tools/move_ui_shots.mjs --docs)"""
    import hashlib
    import re
    doc = open(os.path.join(ROOT, "docs", "MOVE.md")).read()
    links = re.findall(r"\]\((manual/img/move-[\w-]+\.png)\?v=([0-9a-f]+)\)", doc)
    assert len(links) >= 5
    for rel, v in links:
        with open(os.path.join(ROOT, "docs", rel), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest()[:8] == v, rel
        w, h, *_ = read_png(os.path.join(ROOT, "docs", rel))
        assert (w, h) == (512, 256), rel


def test_fx_button_through_the_plugin():
    """M5-01 on dsp.so: s_fx reads 0 (the effects on) at the start, 1 after a write or MIDI CC 111 >= 64, 0 again"""
    sys.path.insert(0, os.path.dirname(__file__))
    import test_move_params as tmp
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join([
        "render 20", "get s_fx", "set s_fx 1", "render 5", "get s_fx", "set s_fx 0", "render 5", "get s_fx",
        "midi 2 176 111 127", "render 5", "get s_fx", "midi 2 176 111 0", "render 5", "get s_fx"]) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    got = [ln.split("\t")[1] for ln in r.stdout.splitlines() if ln.startswith("s_fx\t")]
    assert got == ["0", "1", "0", "1", "0"], r.stdout[-2000:] + r.stderr[-2000:]
