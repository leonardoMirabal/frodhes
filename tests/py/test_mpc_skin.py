# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC skin (mpc/skins/, drawn by mpc/tools/make_skin.py from mpc/skin.json; docs/MPC.md): up to date with
the plugin's parameters; every control bound to a parameter that exists, under its own name; every picture present
and fitting its bounds; every page two clean blocks of four that the Q-Link maps and the focus rectangles agree with;
every parameter on a page; the pages filling the screen; the pictures reproducible byte for byte. Needs
build/mpc/params.json (make mpc); the reproducibility test also a C compiler and zlib."""
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKINS = os.path.join(ROOT, "mpc", "skins")
PARAMS = os.path.join(ROOT, "build", "mpc", "params.json")
TOOL = os.path.join(ROOT, "mpc", "tools", "make_skin.py")

pytestmark = pytest.mark.skipif(not os.path.isfile(PARAMS), reason="build/mpc/params.json missing (make mpc)")


def skin_module():
    spec = importlib.util.spec_from_file_location("make_skin", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def rect(s):
    return tuple(float(v) for v in s.split())


def inside(r, outer):
    return r[0] >= outer[0] and r[1] >= outer[1] and r[0] + r[2] <= outer[0] + outer[2] and r[1] + r[3] <= outer[1] + outer[3]


@pytest.fixture(scope="module")
def tui():
    # (the MPC's parser takes the last of repeated keys; so does json.load)
    return json.load(open(os.path.join(SKINS, "TUI.json")))["pageData"]


@pytest.fixture(scope="module")
def qlinks():
    return json.load(open(os.path.join(SKINS, "Q-Links.json")))


@pytest.fixture(scope="module")
def params():
    return {p["index"]: p for p in json.load(open(PARAMS))["params"]}


def defs_of(tui):
    return {d["key"]: d["value"] for d in tui["componentDefinitions"]["localComponentDefinitions"]}


def controls(tab):
    """the tab's bound controls: (entry, parameter index)"""
    out = []
    for e in tab["componentDefinition"]["componentsData"]:
        for m in e["handle remapping"]["map"]:
            if m["key"] == "Data" and m["value"].startswith("Parameter "):
                out.append((e, int(m["value"].split()[1])))
    return out


def test_up_to_date():
    r = subprocess.run([sys.executable, TOOL, "--check", "--params", PARAMS], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bindings(tui, params):
    defs = defs_of(tui)
    for tab in tui["tabs"]:
        for e, idx in controls(tab):
            assert idx in params, (tab["tabName"], e["componentData"]["name"])
            assert e["componentData"]["name"] == params[idx]["name"]
            assert e["componentData"]["type"] in defs
    for r in tui["replacementStrings"]:
        idx = int(r["key"].split()[1])
        assert r["value"] == params[idx]["choices"] or (params[idx]["kind"] == "bool" and r["value"] == ["OFF", "ON"])
    for tab in tui["tabs"]:   # a lamp follows its switch
        for e in tab["componentDefinition"]["componentsData"]:
            d = e["componentData"]["data"]
            if e["componentData"]["type"] == "Image" and d.get("imageType") == "FilmStrip":
                idx = int(d["handleName"].split()[1])
                assert d["numFrames"] == len(params[idx]["choices"])


def test_every_parameter_on_a_page(tui, params):
    on = {idx for tab in tui["tabs"] for _, idx in controls(tab)}
    assert on == set(params), "not on any page: %s" % sorted(params[i]["name"] for i in set(params) - on)


def test_pictures_fit(tui):
    """every picture present; drawn at its own size (the MPC does not scale film strips or buttons), so each fits
    the bounds it is placed in; the backgrounds exactly the tab's 1280 x 628"""
    defs = defs_of(tui)

    def check(children, where):
        for e in children:
            t, d, b = e["componentData"]["type"], e["componentData"]["data"], rect(e["bounds"]["bounds"])
            if t == "Knob":
                w, h = png_size(os.path.join(SKINS, d["filmStrip"]))
                assert h % d["numFrames"] == 0 and (w, h // d["numFrames"]) == (b[2], b[3]), (where, d["filmStrip"])
            elif t == "Button":
                for k in ("onImage", "offImage"):
                    w, h = png_size(os.path.join(SKINS, d[k]))
                    assert w <= b[2] and h <= b[3], (where, d[k])
            elif t == "Image":
                w, h = png_size(os.path.join(SKINS, d["image"]))
                n = d.get("numFrames", 1)
                assert h % n == 0 and (w, h // n) == (b[2], b[3]), (where, d["image"])
    for name, comp in defs.items():
        check(comp["componentsData"], name)
    for tab in tui["tabs"]:
        kids = tab["componentDefinition"]["componentsData"]
        check(kids, tab["tabName"])
        assert kids[0]["componentData"]["type"] == "Image" and rect(kids[0]["bounds"]["bounds"]) == (0, 0, 1280, 628)
        for e in kids:   # every control inside the tab's area, every part of it inside the control
            b = rect(e["bounds"]["bounds"])
            assert inside(b, (0, 0, 1280, 628)), (tab["tabName"], e["componentData"]["name"])
            for k in defs.get(e["componentData"]["type"], {}).get("componentsData", []):
                assert inside(rect(k["bounds"]["bounds"]), (0, 0, b[2], b[3])), (tab["tabName"], e["componentData"]["name"])
    used = set()
    for path, v in walk(tui):
        if path.endswith(("/filmStrip", "/image", "/onImage", "/offImage")) and v:
            used.add(v)
    files = {f for f in os.listdir(SKINS) if f.endswith(".png")}
    assert used <= files, used - files
    assert files <= used, "unused pictures: %s" % sorted(files - used)


def walk(v, path=""):
    yield path, v
    if isinstance(v, dict):
        for k, x in v.items():
            yield from walk(x, path + "/" + k)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from walk(x, "%s[%d]" % (path, i))


def test_blocks_of_four(tui, qlinks):
    """each page: two focus rectangles, each wrapping one row of four cards (one wide card counts two); the Q-Link
    map puts the top row on Q-Links 13-16 and the lower one on 9-12, left to right, banked top row first; the cards
    fill their rectangle with no gap"""
    tabs = tui["tabs"]
    maps = qlinks["Screen Mode Q-Links"]["map"]
    assert len(maps) == len(tabs)
    keys = set()
    for tab, m in zip(tabs, maps):
        assert (m["Tab"], m["SubTab"]) == (tab["fnKeyIndex"] + 1, tab["fnKeySubIndex"] + 1)
        keys.add((tab["fnKeyIndex"], tab["fnKeySubIndex"]))
        assert m["Bank Direction"] == "RowTopDown"
        rects = [rect(r) for r in tab["qlinkBoundsData"]]
        assert len(rects) == 2, tab["tabName"]
        cards = [(e, idx) for e, idx in controls(tab) if e["bounds"]["acceptsHWFocus"] == "Yes"]
        for row, r in enumerate(rects):
            mine = sorted((rect(e["bounds"]["bounds"]), idx) for e, idx in cards if inside(rect(e["bounds"]["bounds"]), r))
            assert sum(b[2] for b, _ in mine) == r[2] and all(b[1] == r[1] and b[3] == r[3] for b, _ in mine), tab["tabName"]
            x = r[0]
            for b, _ in mine:
                assert b[0] == x
                x += b[2]
            cols = []
            for b, idx in mine:
                cols += [idx] + [-1] * (int(round(b[2] / (r[2] / 4))) - 1)
            assert len(cols) == 4
            assert [m["Q-Links"]["Q-Link %d" % (13 - 4 * row + c)] for c in range(4)] == cols, tab["tabName"]
        assert all(m["Q-Links"]["Q-Link %d" % q] == -1 for q in range(1, 9))
        assert len(cards) == sum(1 for c in cards if any(inside(rect(c[0]["bounds"]["bounds"]), r) for r in rects))
    assert len(keys) == len(tabs) and max(k for k, _ in keys) < 6 and max(s for _, s in keys) < 8
    prog = qlinks["Program Mode Q-Links"]
    assert sorted(prog) == sorted("Q-Link %d" % q for q in range(1, 17)) and all(v >= 0 for v in prog.values())


def test_fills_the_screen(tui):
    """the user's rule: no large empty regions: the two panels (the cards and their headers) cover at least three
    quarters of the 1280 x 628 area, and the cards alone (the knobs, their names and values) almost two thirds"""
    for tab in tui["tabs"]:
        rects = [rect(r) for r in tab["qlinkBoundsData"]]
        cards = sum(r[2] * r[3] for r in rects)
        assert cards >= 0.6 * 1280 * 628, tab["tabName"]
        panels = cards + sum(r[2] * 48 for r in rects)
        assert panels >= 0.75 * 1280 * 628, tab["tabName"]


@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
def test_reproducible(tmp_path):
    """the same inputs draw the same pictures and the same JSON, byte for byte"""
    r = subprocess.run([sys.executable, TOOL, "--params", PARAMS, "--out", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    mine = sorted(f for f in os.listdir(SKINS) if f.endswith((".png", ".json")))
    assert sorted(os.listdir(tmp_path)) == mine
    for f in mine:
        assert open(os.path.join(SKINS, f), "rb").read() == open(tmp_path / f, "rb").read(), f
