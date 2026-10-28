"""docs/MOVE.md names only the pages and controls the Move module has (M1-20)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "MOVE.md").read_text()
LEVELS = json.loads((ROOT / "move" / "module" / "ui_hierarchy.json").read_text())["levels"]


def doc_pages():
    rows = re.findall(r"^\| \*\*(.+?)\*\* \| (.+?) \|$", DOC, re.M)
    return {name: ctl.split(" · ") for name, ctl in rows}


def test_pages_match_module():
    want = {l["name"]: [p["name"] for p in l["params"] if "key" in p] for l in LEVELS.values()}
    assert doc_pages() == want


def test_unverified_marked():
    assert "This port is in development and not released yet." in DOC
    assert DOC.count("*(not yet verified on a device)*") >= 5


def test_module_name_generic():
    mod = json.loads((ROOT / "move" / "module" / "module.json").read_text())
    assert mod["name"] == "Frodhes" and mod["id"] == "frodhes"
    assert "Frodhes" in DOC and "FRDH" in DOC


# ---- the Move's docs against the code (M7-01): the install, the package, the voices, the CCs that do nothing

def _src(*p):
    return (ROOT.joinpath(*p)).read_text()


README = (ROOT / "README.md").read_text()
MANUAL = (ROOT / "docs" / "MANUAL.md").read_text()


def test_package_names_are_the_release_ones():
    rel = _src("tools", "release.py")
    stable = re.search(r'^MOVE_STABLE = "([^"]+)"', rel, re.M).group(1)
    versioned = re.search(r'^MOVE_VERSIONED = "([^"]+)"', rel, re.M).group(1).replace("{v}", "X.Y.Z")
    assert stable in _src("Makefile") and stable in _src("tools", "move_asset.sh")
    assert f"**`{stable}`**" in DOC and f"`{versioned}`" in DOC
    assert f"| `{stable}` |" in README


def test_package_contents():
    """the files MOVE.md lists are the ones make move puts in the tarball (and move_install.py installs)"""
    mk = _src("Makefile")
    tar = re.search(r"ls (frodhes/dsp\.so[^)]*?) 2>/dev/null", mk).group(1).split()
    files = [f.split("/", 1)[1] for f in tar]
    para = DOC[DOC.index("The module is one file"):DOC.index("A release carries it")]
    assert sorted(re.findall(r"`([\w.]+\.(?:so|json|js)|LICENSE)`", para)) == sorted(files)
    inst = _src("tools", "move_install.py")
    pkg = re.search(r"^PACKAGE = \(([^)]*)\)", inst, re.M).group(1)
    assert sorted(re.findall(r'"([^"]+)"', pkg)) == sorted(f for f in files if f != "LICENSE")


def test_install_paths():
    inst = _src("tools", "move_install.py")
    mods = re.search(r'^MODS = "([^"]+)"', inst, re.M).group(1)
    assert f"-C {mods}'" in DOC and f"{mods}/frodhes" in DOC
    assert "ableton@move.local" in inst and "ableton@move.local" in DOC
    data = re.search(r'snprintf\(mb\.dir, sizeof mb\.dir, "(/data[^"]+)"\)', _src("move", "move_bridge.h")).group(1)
    assert f"`{data}`" in DOC


def test_voices_and_hq():
    defs = re.search(r"^MOVE_DEFS \?= (.+)$", _src("Makefile"), re.M).group(1)
    v = int(re.search(r"-DFR_VOICES=(\d+)u", defs).group(1))
    assert "-DFR_HQ=1" in defs
    assert f"**{v} voices** on every engine" in DOC
    sec = MANUAL[MANUAL.index("## 16. Appendix: the Ableton Move"):]
    assert f"{v} voices on every instrument" in sec and f"{v} voices on every" in README
    assert "hq_mode = 1;" in _src("move", "move_bridge.h")     # HQ at every load: the Move's one version


def test_dropped_ccs():
    m = re.search(r"#define MB_CC_DROP\(cc\) \(\(\(cc\) >= (\d+)u && \(cc\) <= (\d+)u\) \|\| \(\(cc\) >= (\d+)u && "
                  r"\(cc\) <= (\d+)u\)\)", _src("move", "move_bridge.h"))
    a, b, c, d = m.groups()
    assert f"({a}-{b}, {c}-{d}) do nothing on the Move" in DOC
    assert f"({a}–{b}, {c}–{d}), which do nothing there" in MANUAL


def test_sustain_button_is_claimed():
    gen = _src("tools", "gen_move_module.py")
    cc = int(re.search(r"^SUS_BTN_CC = (\d+)", gen, re.M).group(1))
    mod = json.loads((ROOT / "move" / "module" / "module.json").read_text())
    assert mod["capabilities"]["claims_ccs"] == [cc]
    assert "**The Capture button**" in DOC and "each press turns SUS on or off" in DOC


def test_manual_and_readme_point_here():
    assert "[MOVE.md](MOVE.md)" in MANUAL[MANUAL.index("## 16. Appendix: the Ableton Move"):]
    assert "16. [Appendix: the Ableton Move (in development)](#16-appendix-the-ableton-move-in-development)" in MANUAL
    assert "[docs/MOVE.md](docs/MOVE.md)" in README


def test_screen_hold_and_fast_step():
    gen = _src("tools", "gen_move_module.py")
    hold = int(re.search(r'"hold_ms": (\d+)', gen).group(1))
    mul = int(re.search(r'"fast_mul": (\d+)', gen).group(1))
    assert hold == 1000 and "About a second after you let go" in DOC
    assert {4: "four"}[mul] + " steps a detent" in DOC
