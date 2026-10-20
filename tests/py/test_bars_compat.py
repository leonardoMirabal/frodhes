# SPDX-License-Identifier: GPL-3.0-only
"""VIBES (the first bar TYPE) keeps everything that was there (PLAN §8.5): the Rhodes types' cards, values, PERFORM
names, TREM/WAH kinds, CC 21 bands and presets are unchanged; the bar TYPE gets its own card names; MIDI CC 80 / 21
and program change reach the new values; the saved preset index is converted once; a record written with VIBES is
read safely by the firmware before VIBES, and an old record by this one.

The firmware's own functions are run by tests/c/bars_ui.c (the whole app in one unit, as on the FM-1)."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "bars_compat")
OLD_REF = os.environ.get("BARS_COMPAT_REF", "v0.9.2")      # a released firmware before VIBES
PREV_REF = os.environ.get("BARS_COMPAT_PREV", "v0.9.3")    # the release before MARIMBA (14 factory presets)


def build(src_root, exe, old=False):
    cmd = ["cc", "-O1", "-w", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"", "-Dmain=host_main",
           "-I" + os.path.join(src_root, "firmware", "src"), "-I" + os.path.join(src_root, "firmware", "gen"),
           "-o", exe, os.path.join(ROOT, "tests", "c", "bars_ui.c"), "-lm"]
    if old:
        cmd.insert(1, "-DBARS_UI_OLD")
    subprocess.run(cmd, check=True, cwd=src_root)
    return exe


def run(exe, *args):
    out = subprocess.run([exe, *map(str, args)], check=True, capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        if " " in line and not line.lstrip()[0].isdigit():
            k, v = line.split(" ", 1)
            d[k] = v
    return d


@pytest.fixture(scope="module")
def new():
    os.makedirs(OUT, exist_ok=True)
    return build(ROOT, os.path.join(OUT, "bars_ui"))


def ref_tree(ref, name, old):
    """the same harness against an older tree (git archive ref); skipped where the tag is missing (shallow CI)"""
    tree = os.path.join(OUT, name)
    tar = os.path.join(OUT, name + ".tar")
    r = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "-o", tar, ref, "firmware", "tests"],
                       capture_output=True)
    if r.returncode:
        pytest.skip(f"no {ref} in this checkout")
    os.makedirs(tree, exist_ok=True)
    subprocess.run(["tar", "-xf", tar, "-C", tree], check=True)
    return build(tree, os.path.join(OUT, "bars_ui_" + name), old=old)


@pytest.fixture(scope="module")
def old():
    return ref_tree(OLD_REF, "old", True)


@pytest.fixture(scope="module")
def prev():
    return ref_tree(PREV_REF, "prev", False)


RHODES_VOICE = ["TONE", "BARK", "BELL", "HAMMER"]
RHODES_DECAY = ["DECAY", "RELEASE", "NOISE", "TUNE"]


def labels(d, page, t):
    return [d[f"{page}{t}.{k}"].split("=")[0] for k in range(4)]


def test_rhodes_cards_unchanged(new):
    d = run(new, "labels")
    for t in range(4):
        assert d[f"type.{t}"].endswith("bars=0")
        assert labels(d, "voice", t) == RHODES_VOICE
        assert labels(d, "decay", t) == RHODES_DECAY
        assert d[f"voice{t}.hint"] == "HOW THE TINE IS STRUCK"
        assert d[f"decay{t}.hint"] == "HOW NOTES DIE AWAY"
        assert d[f"perf{t}.16"] == "TONE" and d[f"perf{t}.21"] == "RELEASE"
        assert d[f"tone{t}.0"] == "-5.1" and d[f"tone{t}.127"] == "+5.0" and d[f"bark{t}.42"] == "3.3"
        # TREM/WAH K1: OFF PAN TREM WAH, no MOTOR; fx.c gets exactly the stored switch and mode
        up = [d[f"kind{t}.up{i}"].split()[0] for i in range(6)]
        assert up == ["OFF", "PAN", "TREM", "WAH", "WAH", "WAH"]
        for i in range(4):
            f = dict(x.split("=") for x in d[f"kind{t}.up{i}"].split()[1:])
            assert f["fxsw"] == f["sw"] and f["fxmode"] == f["mode"]
        assert [d[f"kind{t}.down{i}"] for i in range(5)] == ["WAH", "TREM", "PAN", "OFF", "OFF"]


def test_vibes_cards(new):
    d = run(new, "labels")
    assert d["type.4"] == "VIBES bars=1"
    assert labels(d, "voice", 4) == ["STRIKE", "MALLET", "TUBE", "WEIGHT"]
    assert labels(d, "decay", 4) == ["RING", "DAMPER", "NOISE", "TUNE"]
    assert d["voice4.hint"] == "HOW THE MALLET STRIKES"
    assert [d[f"perf4.{i}"] for i in range(16, 22)] == ["STRIKE", "MALLET", "TUBE", "WEIGHT", "RING", "DAMPER"]
    assert (d["tone4.0"], d["tone4.64"], d["tone4.127"], d["tone4.32"]) == ("CENTRE", "L/3", "EDGE", "-2.5")
    assert [d[f"bark4.{v}"] for v in (0, 42, 85, 127, 20)] == ["SOFT", "YARN", "CORD", "RUBBER", "1.6"]
    # records v3: TREM/WAH is the same pure effect on VIBES (no MOTOR kind); the motor has its own page
    up = [d[f"kind4.up{i}"].split()[0] for i in range(6)]
    assert up == ["OFF", "PAN", "TREM", "WAH", "WAH", "WAH"]
    assert [d[f"kind4.down{i}"] for i in range(5)] == ["WAH", "TREM", "PAN", "OFF", "OFF"]
    assert labels(d, "motor", 4) == ["MOTOR", "DEPTH", "SPEED", "SHAPE"]
    assert d["motor4.hint"] == "THE VIBRAPHONE'S MOTOR"
    assert d["pages4"] == "vpages=3 count=23 comp_pos=5"            # VOICE 1/3 2/3 3/3 (MOTOR), then COMPRESSOR
    for t in range(4):
        assert d[f"pages{t}"] == "vpages=2 count=22 comp_pos=4"     # VOICE 1/2 2/2, then COMPRESSOR
    # the motor on, then a Rhodes TYPE: no tremolo comes on
    assert d["motor_on_rhodes"] == "OFF fxsw=0 fxmode=0"
    assert (d["motrate.0"], d["motrate.78"], d["motrate.120"], d["motrate.127"]) == ("0.5HZ", "3.5HZ", "10HZ", "10HZ")


def test_midi(new):
    d = run(new, "midi")
    ty = [int(d[f"cc80.{v}"]) for v in range(128)]
    assert ty[:96] == [v >> 5 for v in range(96)]                   # 0..95 as before
    assert set(ty[96:]) == {3}                                      # 96..127 DYNO, exactly as before
    assert [int(d[f"cc79.{v}"]) for v in range(128)] == [4] * 96 + [5] * 32   # CC 79: VIBES 0..95 (0.9.3: all), MARIMBA 96..127
    assert ty[104] == 3                                             # hil_scan's DYNO value
    for t in range(4):                                              # Rhodes: CC 21 exactly the old two bands
        assert [int(d[f"cc21.{t}.{v}"]) for v in range(128)] == [v * 2 >> 7 for v in range(128)]
    m = [int(d[f"cc21.4.{v}"]) for v in range(128)]                # records v3: no MOTOR band (the motor: CC 47)
    assert m == [v * 2 >> 7 for v in range(128)]
    for p in range(12):
        assert int(d[f"pc.{p}"].split()[0]) == p                    # F01..F12 unchanged
    assert d["pc.12"] == "12 type=4" and d["pc.13"] == "13 type=4"  # F13, F14
    assert d["pc.14"] == "14 type=5" and d["pc.15"] == "15 type=5"  # F15, F16 MARIMBA
    assert d["pc.16"].startswith("200") and d["pc.17"].startswith("200")   # U01, U02: nothing saved
    assert d["factory.12"] == "F13 VIBE MOTOR type=4 sw=0 mode=0 mot=1 depth=110 rate=78 shape=0 rev=45"
    assert d["factory.13"] == "F14 VIBES DRY type=4 sw=0 mode=0 mot=0 depth=110 rate=78 shape=0 rev=20"
    assert d["factory.14"].startswith("F15 MARIMBA type=5 sw=0 mode=0 mot=0 ") and d["factory.14"].endswith("rev=30")
    assert d["factory.15"].startswith("F16 MRMB SOFT type=5 ") and d["factory.15"].endswith("rev=45")


def test_preset_index_conversion(new):
    d = run(new, "conv")
    def got(k):
        return d[k].split()[0], d[k].split()[1:3]
    for pre, want in ((0, 0), (5, 5), (11, 11), (12, 16), (15, 19), (20, 24), (27, 31), (28, 28), (31, 31), (200, 200)):
        assert got(f"conv.old.{pre}") == (str(want), ["flags4=1", "flags32=1"])   # before VIBES: U01 12 -> 16
        assert d[f"conv2.old.{pre}"] == str(want)                   # once only
    for pre, want in ((0, 0), (12, 12), (13, 13), (14, 16), (20, 22), (29, 31), (30, 30), (200, 200)):
        assert got(f"conv.v093.{pre}") == (str(want), ["flags4=1", "flags32=1"])  # 0.9.3: U01 14 -> 16
        assert d[f"conv2.v093.{pre}"] == str(want)
        u = int(d[f"conv.v093.{pre}"].split("undo=")[1])           # the undo's preset (index + 1) moves with it
        assert u == (0 if pre == 200 else want + 1)
    for pre in (0, 12, 14, 20, 29, 31, 200):
        assert d[f"conv.new.{pre}"].split()[0] == str(pre)          # flags & 32 set: untouched
    assert d["fresh.flags4"] == "1" and d["fresh.flags32"] == "1"


def test_vibes_record_on_old_firmware(new, old):
    rec = os.path.join(OUT, "vibes.rec")
    run(new, "dump", rec, 4, 1, 16)                                 # VIBES, TREM AM, U01 (the new index)
    d = run(old, "load", rec)
    assert d["set.type"] == "0" and d["set.tw_mode"] == "1"         # TYPE out of the old range: the default
    assert d["set.rev"] == "33" and d["rh_type"] == "0"             # the rest kept, no crash
    # records v3: a bank of 96-byte records is not one the old firmware knows, so it shows no user presets (and
    # plays nothing it cannot read); the settings above still load
    assert d["u01.used"] == "0"
    d = run(new, "load", rec)                                       # and on this firmware it is all there
    assert (d["set.type"], d["set.tw_mode"], d["set.preset"]) == ("4", "1", "16")
    assert (d["u01.type"], d["u01.tw_mode"], d["u01.preset"]) == ("4", "1", "16")


def test_old_record_on_new_firmware(new, old):
    rec = os.path.join(OUT, "old.rec")
    run(old, "dump", rec, 3, 1, 12)                                 # DYNO, AM tremolo, U01 (the old index)
    d = run(new, "load", rec)
    assert (d["set.type"], d["set.tw_mode"], d["set.tw_sw"], d["set.rev"]) == ("3", "1", "1", "33")
    assert d["set.preset"] == "16"                                  # converted to the new U01
    assert (d["rh_type"], d["fx.tw_sw"], d["fx.tw_mode"]) == ("3", "1", "1")
    assert (d["u01.type"], d["u01.tw_mode"], d["u01.preset"]) == ("3", "1", "16")


def test_marimba_record_on_0_9_3(new, prev):
    """a record saved with MARIMBA, read by 0.9.3: TYPE 5 is out of its range, params_sanitize gives it the default
    (STAGE 77, not VIBES); the rest loads; the preset index 16 (U01 here) reads as 0.9.3's U03 (its layout); back on
    this firmware it is all there"""
    rec = os.path.join(OUT, "marimba.rec")
    run(new, "dump", rec, 5, 1, 16)
    d = run(prev, "load", rec)
    assert d["set.type"] == "0" and d["rh_type"] == "0" and d["set.rev"] == "33"
    assert d["set.preset"] == "16"
    d = run(new, "load", rec)
    assert (d["set.type"], d["set.preset"]) == ("5", "16")


def test_0_9_3_record_on_new_firmware(new, prev):
    """a 0.9.3 record (VIBES, U01 = index 14) on this firmware: VIBES kept, U01 converted to 16 once"""
    rec = os.path.join(OUT, "v093.rec")
    run(prev, "dump", rec, 4, 1, 14)
    d = run(new, "load", rec)
    assert (d["set.type"], d["set.tw_mode"], d["set.preset"]) == ("4", "1", "16")
    assert (d["u01.type"], d["u01.preset"]) == ("4", "16")


def test_marimba_cards(new):
    """MARIMBA (TYPE 5): the bar cards with its own names; MUTE on DECAY's second card; no MOTOR page (VOICE 1/2,
    2/2, then COMPRESSOR); FRODHES K2 / K3 = MALLET / TUBE (eng_pages.h)"""
    d = run(new, "labels")
    assert d["type.5"] == "MARIMBA bars=1"
    assert labels(d, "voice", 5) == ["STRIKE", "MALLET", "TUBE", "WEIGHT"]
    assert labels(d, "decay", 5) == ["RING", "MUTE", "NOISE", "TUNE"]
    assert d["decay5.hint"] == "HOW BARS RING; MUTE = HAND"
    assert d["pages5"] == "vpages=2 count=22 comp_pos=4"   # (22 with the DISPLAY page)
    assert [d[f"perf5.{i}"] for i in range(16, 23)] == ["STRIKE", "MALLET", "TUBE", "WEIGHT", "RING", "MUTE", "NOISE"]
    assert (d["tone5.0"], d["tone5.64"], d["tone5.127"]) == ("CENTRE", "L/3", "EDGE")
    assert [d[f"bark5.{v}"] for v in (0, 42, 85, 127, 20)] == ["SOFT", "MED", "HARD", "RUBBER", "1.6"]
    assert d["decay5.1"] == "MUTE=5.0"                              # P_RELEASE's number, 0.0 .. 10.0
