# SPDX-License-Identifier: GPL-3.0-only
"""The EQ on the Move (0.9.9): its page (after COMPRESSOR, before DRIVE/FILTER, as on the FM-1), the texts its cards
show (the FM-1's: tools/gen_move_module.py's port of params.c, checked here against the C at every value), and the
compatibility of what an older Move build saved: a state and a store.bin written by the main branch's plugin before
the EQ (move/tests/main097/, store.bin as hex, made with move/tests/test_bridge.c script's state_dump from main's dsp.so: SUS on CHAN 5,
TUNE -7, TONE 44, SPREAD 77, REVERB MIX 90, LEVEL 70, COMP on saved into U05, then TONE 50 and DELAY MIX 33 in the
state) load unchanged with the EQ flat; the Move-only presets and the factory ones load the EQ flat; a user preset
saved with the EQ keeps it. The screen's own checks: move/ui/test_screen.mjs and test_chain.mjs (the screen reader)."""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import gen_move_module as gen  # noqa: E402
from hostrun import once  # noqa: E402

FIX = os.path.join(ROOT, "move", "tests", "main097")
EQ_KEYS = ["p_eq_lo", "p_eq_mid", "p_eq_freq", "p_eq_hi"]
M = gen.model()
LEVELS = gen.build(M)[0]


def test_eq_level_in_the_fm1_order():
    keys = list(LEVELS)
    assert keys.index("eq") == keys.index("comp") + 1 and keys.index("drv_flt") == keys.index("eq") + 1
    lv = LEVELS["eq"]
    assert lv["name"] == "EQ" and lv["knobs"] == EQ_KEYS
    assert [p["name"] for p in lv["params"]] == ["LOW", "MID", "FREQ", "HIGH"]
    for p in lv["params"]:
        assert (p["type"], p["min"], p["max"], p["default"]) == ("int", 0, 127, 64), p
    assert "EQ" not in gen.FM1_ONLY_PAGES and not [p for p in gen.MIDI_ONLY if p.startswith("P_EQ_")]
    # the FM-1's order of the pages the Move shows (ui.c PG_ORDER): COMPRESSOR, EQ, DRIVE
    order = [M["pages"][pg]["title"] for pg in M["order"]]
    assert order.index("EQ") == order.index("COMPRESSOR") + 1 and order.index("DRIVE") == order.index("EQ") + 1


def test_eq_screen_cards():
    pages = {p["title"]: p for p in gen.screen_pages(LEVELS)}
    ks = pages["EQ"]["knobs"]
    assert [k.get("short", k["name"]) for k in ks] == ["LOW", "MID", "FREQ", "HIGH"]
    assert [k.get("fmt") for k in ks] == ["eqdb", "eqdb", "eqhz", "eqdb"]
    assert [k.get("zero") for k in ks] == [64, 64, None, 64]
    say = gen.say_conf(LEVELS)
    assert [say["names"][k] for k in EQ_KEYS] == ["Low", "Mid", "Mid frequency", "High"]
    assert say["pages"]["EQ"] == "EQ"


@pytest.fixture(scope="module")
def c_texts():
    exe = once(os.path.join(ROOT, "build", "host", "eq_text"), lambda exe: subprocess.run(
        ["cc", "-O1", "-w", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"", "-Dmain=host_main",
         "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
         "-o", exe, os.path.join(ROOT, "tests", "c", "eq_text.c"), "-lm"], check=True))
    out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    d = {}
    for pid, v, t in re.findall(r"^eq (\d+) (\d+) (\S+)$", out, re.M):
        d.setdefault(int(pid), []).append((int(v), t))
    return d


def test_eq_texts_are_the_firmware_s(c_texts):
    """the Move's texts (FR_CONF.fmt) are params.c param_text's at every value of every EQ card"""
    assert len(c_texts) == 4
    conf = gen.fmt_conf()
    for p in ("P_EQ_LO", "P_EQ_MID", "P_EQ_FREQ", "P_EQ_HI"):
        f = gen.FMT[M["params"][p]["fmt"]][0]
        got = [t for _, t in sorted(c_texts[M["params"][p]["id"]])]
        assert len(got) == 128 and conf[f]["text"] == got, (p, [(v, a, b) for v, (a, b) in
                                                               enumerate(zip(conf[f]["text"], got)) if a != b][:8])
    t = conf["eqdb"]["text"]
    assert (t[0], t[1], t[64], t[77], t[127]) == ("-15", "-15", "+0.0", "+3.1", "+15")
    h = conf["eqhz"]["text"]
    assert (h[0], h[64], h[127]) == ("200", "1.0K", "5.0K")
    assert all(len(x) <= gen.VALUE_MAX for x in t + h)


# ---- the plugin (Docker or Linux arm64): what main's plugin saved loads with the EQ flat

def _bridge(lines, tb_dir=None):
    inner = "make -s move-tools-native >/dev/null 2>&1 && " + ("TB_DIR=%s " % tb_dir if tb_dir else "") + \
            "build/move/t/test_bridge script build/move/frodhes/dsp.so"
    if platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64"):
        cmd = ["sh", "-c", inner]
    elif shutil.which("docker") and subprocess.run(["docker", "image", "ls", "-q", "frodhes-move:bookworm"],
                                                    capture_output=True, text=True).stdout.strip():
        cmd = ["docker", "run", "--rm", "-i", "--cpus=2", "--platform", "linux/arm64", "-v", ROOT + ":/src", "-w",
               "/src", "frodhes-move:bookworm", "sh", "-c", inner]
    else:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    r = subprocess.run(cmd, input="\n".join(lines) + "\n", capture_output=True, text=True, cwd=ROOT, timeout=1800)
    got = {}
    for ln in r.stdout.splitlines():
        if ln.count("\t") == 1:
            k, v = ln.split("\t")
            got.setdefault(k, []).append(v)
    return got, r


def _rel(p):
    return os.path.relpath(p, ROOT)


def _eq_off():
    """the EQ away from flat first, so a load that leaves it alone would show"""
    return ["set p_eq_lo 10", "set p_eq_mid 100", "set p_eq_freq 20", "set p_eq_hi 120", "render 10"]


def _get_eq():
    return ["get " + k for k in EQ_KEYS]


def test_main_state_loads_unchanged_eq_flat():
    st = open(os.path.join(FIX, "state.json")).read()
    assert len(re.search(r'"base":"([0-9a-f]*)"', st).group(1)) == 2 * 64, "the fixture is a state from before the EQ"
    os.makedirs(os.path.join(ROOT, "build", "move", "t"), exist_ok=True)
    out = _rel(os.path.join(ROOT, "build", "move", "t", "eq_state_%d.json" % os.getpid()))
    lines = (_eq_off() + ["state_file " + _rel(os.path.join(FIX, "state.json")), "render 20"] + _get_eq()
             + ["get " + k for k in ("p_tone", "p_dl_depth", "p_spread", "p_rv_depth", "p_level", "p_cmp_sw",
                                     "s_mch", "s_tune", "s_sustain", "s_fx")]
             + ["state_dump " + out]
             # a state saved with the EQ keeps it
             + ["set p_eq_lo 90", "set p_eq_freq 30", "render 5", "state_save", "set p_eq_lo 64", "set p_eq_freq 64",
                "render 5", "state_load", "render 20", "get p_eq_lo", "get p_eq_freq"])
    got, r = _bridge(lines)
    try:
        assert got.get("state_file") == ["ok"], r.stdout[-2000:] + r.stderr[-2000:]
        assert [got[k][0] for k in EQ_KEYS] == ["64"] * 4, got
        want = {"p_tone": "50", "p_dl_depth": "33", "p_spread": "77", "p_rv_depth": "90", "p_level": "70",
                "p_cmp_sw": "1", "s_mch": "5", "s_tune": "-7", "s_sustain": "0", "s_fx": "1"}
        assert {k: got[k][0] for k in want} == want
        new = open(os.path.join(ROOT, out)).read()
        assert len(re.search(r'"base":"([0-9a-f]*)"', new).group(1)) == 2 * M["defs"]["P_SLOTS"]
        assert (got["p_eq_lo"][1], got["p_eq_freq"][1]) == ("90", "30"), got
    finally:
        if os.path.exists(os.path.join(ROOT, out)):
            os.remove(os.path.join(ROOT, out))


def test_main_store_user_preset_and_factory_presets_load_eq_flat():
    os.makedirs(os.path.join(ROOT, "build", "move", "t"), exist_ok=True)
    d = tempfile.mkdtemp(prefix="eq_store.", dir=os.path.join(ROOT, "build", "move", "t"))
    native = platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64")
    try:
        with open(os.path.join(d, "store.bin"), "wb") as f:      # (kept as hex: the repo holds no binary file here)
            f.write(bytes.fromhex("".join(open(os.path.join(FIX, "store.hex")).read().split())))
        lines = ["get s_mch", "get s_tune"]
        # U05 (main's, before the EQ), F01, the Move-only F20 and F57: each with the EQ off flat first
        for idx in (23, 0, 35, 72):
            lines += _eq_off() + ["set preset %d" % idx, "render 10", "get preset_name"] + _get_eq() + ["get p_tone",
                                                                                                    "get p_spread"]
        # a user preset saved with the EQ keeps it (U06)
        lines += ["set p_eq_lo 90", "set p_eq_hi 20", "render 5", "set u_save 6", "render 20", "sleep 200",
                  "render 100", "set preset 0", "render 10", "set preset 24", "render 10", "get preset_name",
                  "get p_eq_lo", "get p_eq_hi"]
        got, r = _bridge(lines, tb_dir=d if native else "/src/" + _rel(d))
        assert got.get("s_mch") == ["5"] and got.get("s_tune") == ["-7"], (got, r.stdout[-2000:], r.stderr[-2000:])
        names = got["preset_name"]
        assert names[0] == "U05 ST77" and names[1].startswith("F01 ") and names[2].startswith("F20 ") \
            and names[3].startswith("F57 "), names
        for i in range(4):
            assert [got[k][i] for k in EQ_KEYS] == ["64"] * 4, (names[i], got)
        assert (got["p_tone"][0], got["p_spread"][0]) == ("44", "77"), "U05 as main saved it"
        assert names[4].startswith("U06 ") and (got["p_eq_lo"][4], got["p_eq_hi"][4]) == ("90", "20"), got
    finally:
        shutil.rmtree(d, ignore_errors=True)
