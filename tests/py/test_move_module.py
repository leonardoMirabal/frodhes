# SPDX-License-Identifier: GPL-3.0-only
"""Gate U: the Move module's description (move/module/) is what tools/gen_move_module.py makes from params.c,
eng_pages.h and ui.c (rerun the tool after a parameter, card or page change), and it keeps the host's limits: knob
levels of <= 8, cell labels of <= 5 characters, ASCII, help lines of <= 20 characters, module.json under the
loader's 8 KB. Every parameter is on a level, reached through another key, or listed MIDI-only with a reason; every
engine card is on its engine's level; the C table compiles against the firmware (P_* / S_* exist)."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_move_module as gen  # noqa: E402

OUT, M, REACH = gen.outputs()
HIER = json.loads(OUT["ui_hierarchy.json"])["levels"]
CHAIN = json.loads(OUT["chain_params.json"])


def items(lvl):
    return [p for p in lvl["params"] if "key" in p]


def test_committed_files_are_the_generators_output():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_move_module.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(os.listdir(gen.OUT)) == sorted(gen.FILES), "a file in move/module/ the generator does not write"


def test_every_parameter_reachable_or_midi_only():
    missing = [p for p in M["pids"] if p not in REACH and p not in gen.MIDI_ONLY]
    assert not missing, f"parameters on no Move level and not listed MIDI-only (gen_move_module.MIDI_ONLY): {missing}"
    stale = [p for p in gen.MIDI_ONLY if p in REACH]
    assert not stale, f"listed MIDI-only but on a level: {stale}"
    assert all(gen.MIDI_ONLY[p].strip() for p in gen.MIDI_ONLY), "a MIDI-only parameter needs its reason"
    assert all(M["params"][p]["cc"] for p in gen.MIDI_ONLY), "a MIDI-only parameter needs a CC"
    for p, (key, why) in gen.VIA.items():
        assert any(c["key"] == key for c in CHAIN) and why


def test_levels_keep_the_hosts_limits():
    for name, lvl in HIER.items():
        assert 1 <= len(lvl["knobs"]) <= 8, (name, lvl["knobs"])
        keys = [p["key"] for p in items(lvl)]
        assert [k for k in lvl["knobs"] if k is not None] == keys, name   # every item on a knob, in order (None: a hole)
        assert lvl["knobs"][-1] is not None, name
        for p in items(lvl):
            assert len(p["name"]) <= gen.LABEL_MAX and p["name"].isascii() and p["name"].strip(), (name, p)
            if p["type"] == "canvas":                          # (a screen the module draws: its script ships)
                assert p["canvas_script"] in gen.FILES, (name, p)
            elif p.get("access") == "read":                    # (the GR meter's cells: the module's widget)
                assert p["key"] in gen.METER and name == "comp", (name, p)
            elif p["type"] == "enum":
                assert p["options"] and all(o.isascii() and o for o in p["options"]), (name, p)
                assert all(len(o) <= gen.VALUE_MAX for o in p["options"]), (name, p)
                assert len(set(p["options"])) == len(p["options"]), (name, p)
            else:
                assert p["min"] < p["max"], (name, p)
        for link in lvl["params"]:
            if "level" in link:
                assert link["level"] in HIER, link
    assert "root" in HIER and HIER["root"]["list_param"] == "preset"
    assert "s_sustain" in HIER["root"]["knobs"], "SUSTAIN (CC 64) on the first page"
    assert not any("s_susbtn" in lvl["knobs"] for lvl in HIER.values()), "the Capture button's key is on no page"


def test_sustain_button_claimed():
    """M1-13b: module.json claims the sustain button (a CC the host lets a module claim) and asks for the touch edges
    (the direct route); Frodhes' own screen (screen.js) turns that CC's press into s_susbtn 1, which flips SUS"""
    mod = json.loads(OUT["module.json"])
    caps = mod["capabilities"]
    assert caps["claims_ccs"] == [gen.SUS_BTN_CC, gen.FX_BTN_CC] and caps["touch_observe"] is True
    assert (gen.SUS_BTN_CC, gen.FX_BTN_CC) == (63, 62)          # Right SUS, Left FX; Up / Down (55 / 54) stay the Move's
    host_owned = {49, 50, 51, 14, 3, 71, 72, 73, 74, 75, 76, 77, 78, 79, 88, 40, 41, 42, 43, 114, 115}
    assert gen.SUS_BTN_CC not in host_owned and gen.FX_BTN_CC not in host_owned   # (Schwung shadow_ui.js CC_CLAIM_DENIED)
    assert "sustain.js" not in OUT and "sustain.js" not in gen.FILES, "the PEDAL screen is gone (a toggle now)"
    js = OUT["screen.js"]
    assert 'ctx.setParam("s_susbtn", "1")' in js and 'ctx.setParam("s_susbtn", "0")' not in js
    # (once SUS / FX are read, an arrow writes the state it shows next: a repeated write cannot flip it back)
    assert 'ctx.setParam("s_sustain", st.susOn ? "1" : "0")' in js and 'ctx.setParam("s_fx", st.fxOn ? "1" : "0")' in js


def test_ranges_and_options_are_the_parameters():
    by_key = {c["key"]: c for c in CHAIN}
    assert len(by_key) == len(CHAIN), "a key twice in chain_params"
    rows = re.findall(r'\{"(\w+)", (\w+), (\d+), (MK_\w+), (\d+), (-?\d+)\}', OUT["move_ui.h"])
    # (the GR cells: no row; the Capture button's key: a row, no chain_params entry, which Schwung's merge would
    # turn into a knob of its own)
    assert [r[0] for r in rows] == [c["key"] for c in CHAIN if c["key"] not in gen.METER] + gen.HIDDEN
    for key, slot, cc, kind, scale, off in rows:
        if key in gen.HIDDEN:
            assert kind == "MK_BTN" and key not in by_key
            continue
        c = by_key[key]
        if slot.startswith("P_"):
            p = M["params"][slot]
            assert int(cc) == p["cc"] and kind == "MK_PARAM"
            if c["type"] == "enum":
                assert c["options"] == [gen.short_value(o) for o in M["names"][p["names"]][:p["hi"] + 1]]
            else:
                assert (c["min"], c["max"]) == (-int(off), p["hi"] - int(off)) and c["default"] == p["def"] - int(off)
        elif slot.startswith("S_") and slot != "S_NONE":
            hi = M["slot_hi"][slot]
            n = len(c["options"]) - 1 if c["type"] == "enum" else round(c["max"] * int(scale) + int(off))
            assert n == hi, (key, n, hi)                       # ui.c slot_val's range
            assert slot not in gen.FM1_ONLY_SLOTS
        else:
            assert key in gen.EXTRA and int(cc) == gen.EXTRA[key]["cc"]
    assert by_key["p_type"]["options"] == M["names"]["N_TYPE"]
    assert by_key["s_sustain"]["options"] == ["OFF", "ON"] and gen.EXTRA["s_sustain"]["cc"] == 64


def test_engine_pages_follow_type():
    """each eng_pages.h engine's cards are on levels gated by ui_engine == its ENG_UI row, in the table's order,
    and every TYPE's row has levels"""
    for e, ctls in enumerate(M["engines"]):
        lv = [lvl for lvl in HIER.values() if lvl.get("visible_if") == {"param": "ui_engine", "equals": e}]
        got = [p for lvl in lv for p in items(lvl)]
        slots = [next(r[1] for r in re.findall(r'\{"(\w+)", (\w+),', OUT["move_ui.h"]) if r[0] == p["key"])
                 for p in got]
        assert [s for s in slots if s in {c["slot"] for c in ctls}] == [c["slot"] for c in ctls], e
    assert set(M["type_ui"]) == set(range(len(M["engines"])))
    gates = {json.dumps(lvl.get("visible_if")) for lvl in HIER.values() if lvl.get("visible_if")}
    assert all(json.loads(g)["param"] == "ui_engine" for g in gates)


def test_module_json_and_help():
    mod = json.loads(OUT["module.json"])
    assert (mod["id"], mod["name"], mod["api_version"], mod["dsp"]) == ("frodhes", "Frodhes", 2, "dsp.so")
    assert mod["capabilities"]["component_type"] == "sound_generator"
    assert len(OUT["module.json"]) < 8192 and "ui_hierarchy" not in OUT["module.json"]
    hlp = json.loads(OUT["help.json"])
    assert hlp["children"]

    def walk(n):
        for ln in n.get("lines", []):
            assert len(ln) <= gen.HELP_MAX and ln.isascii(), ln
        for c in n.get("children", []):
            walk(c)
    walk(hlp)


def test_no_brand_names():
    """the public strings follow the doc test's rule (test_docs_vs_code BRAND_SHA)"""
    import hashlib
    spec = importlib.util.spec_from_file_location("tdv", os.path.join(ROOT, "tests", "py", "test_docs_vs_code.py"))
    tdv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tdv)
    for n, t in OUT.items():
        words = re.findall(r"[a-z0-9]+", t.lower())
        assert not {hashlib.sha256(w.encode()).hexdigest()[:16] for w in set(words)} & tdv.BRAND_SHA, n
        assert "rhodes" not in words or n == "move_ui.h", n    # (generic: "electric piano"; the header: file names)


@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
def test_c_table_compiles_against_the_firmware():
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "probe.c")
        with open(probe, "w") as f:
            f.write('#include "firmware/src/frodhes.c"\n#include "move/module/move_ui.h"\n'
                    "int move_ui_probe(void) { return (int)MOVE_NKEYS + MOVE_KEYS[0].slot + "
                    "(int)sizeof MOVE_UI_HIERARCHY + (int)sizeof MOVE_CHAIN_PARAMS; }\n")
        r = subprocess.run(["cc", "-fsyntax-only", "-DDINGHY_HOST", "-I" + ROOT, "-I" + os.path.join(ROOT, "firmware", "src"),
                            "-I" + os.path.join(ROOT, "firmware", "gen"), "-Wall", "-Wno-unused-function", probe],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-3000:]
