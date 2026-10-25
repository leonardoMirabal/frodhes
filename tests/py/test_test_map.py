# SPDX-License-Identifier: GPL-3.0-only
"""tools/test_map.py: every tracked file maps to its test tiers (T0 host, T1 must-pass, T2 full scans), the plan's
examples, and the CLI."""
import fnmatch
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_map as tm  # noqa: E402


def tracked():
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()


def test_every_firmware_file_is_named_by_a_rule():
    """each file under firmware/ maps to T1 (at least) by a rule that names it: the catch-all is for files added
    after the map, never for the ones tracked now"""
    fw = [f for f in tracked() if f.startswith("firmware/")]
    assert len(fw) > 40
    unnamed = [f for f in fw if tm.tiers_of(f)["group"] == "firmware:unknown"]
    assert not unnamed, f"add these to tools/test_map.py RULES: {unnamed}"
    for f in fw:
        m = tm.tiers_of(f)
        assert "T0" in m["tiers"] and "T1" in m["tiers"] and m["t1"], (f, m)
        assert set(m["t1"]) <= set(tm.SCENARIOS)
        assert set(m["t2"]) <= set(tm.ENGINES_T2 + ("effects",))


def test_every_tracked_file_maps():
    for f in tracked():
        assert tm.tiers_of(f)["tiers"][0] == "T0"


def test_every_rule_pattern_is_live():
    """no stale rule: each pattern (but the catch-alls) matches a tracked file"""
    files = tracked()
    for g, pats, *_ in tm.RULES:
        for p in pats:
            if p in ("*", "firmware/*"):
                continue
            assert any(fnmatch.fnmatchcase(f, p) for f in files), (g, p)


def test_the_plans_examples():
    usb = tm.analyze(["firmware/src/usb.c"])
    assert usb["tiers"] == ["T0", "T1"] and usb["t1"] == [1, 2, 3, 4, 5, 6] and not usb["t2"]
    assert any("both rates" in e for e in usb["extras"])
    for f in ("firmware/src/platform.c", "firmware/hal/fm1_usb.h", "firmware/hal/fm1_isr.S"):
        assert tm.analyze([f])["t1"] == list(tm.ALL), f
    ui = tm.analyze(["firmware/src/ui.c"])
    assert ui["tiers"] == ["T0", "T1"] and ui["t1"] == [3, 4, 5] and not ui["t2"]
    assert any("screenshots" in e for e in ui["extras"])
    for f in ("firmware/src/params.c", "firmware/src/presets.c", "firmware/src/eng_pages.h"):
        assert tm.analyze([f])["t1"] == [3, 4, 5], f
    docs = tm.analyze(["docs/MANUAL.md", "README.md", "tools/hil_scan.py", "tests/py/test_release_tool.py",
                       ".github/workflows/ci.yml", "web/build.sh"])
    assert docs["tiers"] == ["T0"] and not docs["t1"] and not docs["t2"]
    fx = tm.analyze(["firmware/src/fx.c"])
    assert fx["tiers"] == ["T0", "T1", "T2"] and fx["t1"] == [1, 4, 6] and fx["t2"] == ["effects"]
    for f, eng in (("firmware/src/rhodes.c", ["rhodes"]), ("firmware/src/bars.c", ["vibes", "marimba"]),
                   ("firmware/gen/rhodes_tables.h", ["rhodes"]), ("firmware/gen/bars_tables.h", ["vibes", "marimba"])):
        m = tm.analyze([f])
        assert m["tiers"] == ["T0", "T1", "T2"] and m["t1"] == list(tm.ALL) and m["t2"] == eng, f
    assert tm.analyze(["firmware/src/engine.c"])["t2"] == ["rhodes", "vibes", "marimba", "effects"]
    for f in ("firmware/src/notes.c", "firmware/src/arp.c", "firmware/src/seq.c", "firmware/src/tempo.c"):
        m = tm.analyze([f])
        assert m["t1"] == [1, 3] and not m["t2"] and any("sequencer" in e for e in m["extras"]), f
    new = tm.analyze(["firmware/src/newthing.c"])                       # unknown firmware: everything
    assert new["tiers"] == ["T0", "T1", "T2"] and new["t1"] == list(tm.ALL) and len(new["t2"]) == 4
    both = tm.analyze(["firmware/src/ui.c", "firmware/src/seq.c", "docs/MANUAL.md"])     # the union
    assert both["t1"] == [1, 3, 4, 5] and set(both["groups"]) == {"ui", "notes", "host"}
    assert tm.analyze([])["tiers"] == ["T0"]


def test_key_velocity_files():
    """the converter, the key rows, the scan tick and keyvel.c -> T1 all + the velocity checks (and still the USB
    counters at both rates: the tick and the converter share the platform)"""
    for f in ("firmware/hal/fm1_adc.h", "firmware/hal/fm1_keyadc.h", "firmware/hal/fm1_input.h",
              "firmware/src/keyvel.c", "firmware/src/keyvel.h"):
        m = tm.analyze([f])
        assert m["tiers"] == ["T0", "T1"] and m["t1"] == list(tm.ALL) and not m["t2"], f
        assert set(m["groups"]) == {"keyvel"}, f
        assert any("both rates" in e for e in m["extras"]), f
        ex = [e for e in m["extras"] if "key velocity" in e]
        assert ex and all(k in ex[0] for k in ("--keyadc gate", "selftest", "soak", "jack", "record", "compare")), f
    assert not any("key velocity" in e for e in tm.analyze(["firmware/hal/fm1_usb.h"])["extras"])


def test_reason_names_the_files():
    lines = tm.reason(tm.analyze(["firmware/src/ui.c", "docs/MANUAL.md"]))
    assert lines[0].startswith("tiers T0 + T1; T1 scenarios 3/4/5 (preset / TYPE switching")
    assert any("ui:" in l and "firmware/src/ui.c" in l for l in lines)
    assert any("host:" in l and "docs/MANUAL.md" in l for l in lines)


def test_cli():
    py = sys.executable
    p = subprocess.run([py, "tools/test_map.py", "--json", "firmware/src/usb.c"], cwd=ROOT, capture_output=True,
                       text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["t1"] == [1, 2, 3, 4, 5, 6]
    p = subprocess.run([py, "tools/test_map.py", "docs/MANUAL.md"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "tiers T0; T1 scenarios none" in p.stdout
    p = subprocess.run([py, "tools/test_map.py", "--since", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "git diff HEAD..HEAD (0 file(s))" in p.stdout and "no file changed" in p.stdout
