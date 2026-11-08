# SPDX-License-Identifier: GPL-3.0-only
"""The plugin UI (plugin/ui, B-M2a): its copy of the parameter ids and ranges matches firmware/src/params.c (ids are
append-only: a mismatch would draw one parameter's value on another's knob), its option names are params.c's, and the
headless render tool builds and draws every state at the exact pixel sizes (1x, 2x, the smallest and largest window),
twice with the same bytes (the drawing is deterministic, so pixel tests and documentation pictures can rely on it)."""
import hashlib
import os
import re
import shutil
import subprocess

import pytest

from hostrun import ROOT

UI = os.path.join(ROOT, "plugin", "ui")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(s):
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def _enum(src, prefix):
    m = re.search(r"enum\s*\{\s*(" + prefix + r"TYPE\b.*?" + prefix + r"COUNT)\s*\}", _strip_comments(src), re.S)
    assert m, "no parameter enum"
    return [t.strip()[len(prefix):] for t in m.group(1).split(",") if t.strip()]


def test_ids_match_params_c():
    fw = _read(os.path.join(ROOT, "firmware", "src", "params.c"))
    ui = _read(os.path.join(UI, "frui_params.h"))
    assert _enum(ui, "FRP_") == _enum(fw, "P_")


def test_ranges_match_params_c():
    fw = _strip_comments(_read(os.path.join(ROOT, "firmware", "src", "params.c")))
    ui = _strip_comments(_read(os.path.join(UI, "frui_params.h")))
    hi_fw = {k: int(v) for k, v in re.findall(r"\[P_(\w+)\]\s*=\s*\{\"[^\"]*\",\s*(\d+),", fw)}
    hi_ui = {k: int(v) for k, v in re.findall(r"\[FRP_(\w+)\]\s*=\s*(\d+)", ui.split("FRP_HI")[1].split("};")[0])}
    assert hi_ui == hi_fw


@pytest.mark.parametrize("ui_name,fw_name", [("FRN_DRV", "N_DRV"), ("FRN_DL", "N_DL"), ("FRN_RV", "N_RV_LONG"),
                                             ("FRN_FLT", "N_FLT"), ("FRN_LFOS", "N_LFOS"), ("FRN_RETRIG", "N_RETRIG"),
                                             ("FRN_SYNC", "N_SYNC"), ("FRN_TYPE", "N_TYPE_LONG")])
def test_option_names_match_params_c(ui_name, fw_name):
    def names(src, n):
        m = re.search(r"\b" + n + r"\[\]\s*=\s*\{(.*?)\};", src, re.S)
        assert m, n
        return re.findall(r"\"([^\"]*)\"", m.group(1))
    fw = _read(os.path.join(ROOT, "firmware", "src", "params.c"))
    ui = _read(os.path.join(UI, "frui_params.h"))
    assert names(ui, ui_name) == names(fw, fw_name)


def test_renders(tmp_path):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "frui_render")
    srcs = [os.path.join(UI, f) for f in ("frui.c", "frui_font.c", "frui_gfx.c", os.path.join("tools", "frui_render.c"))]
    r = subprocess.run([cc, "-O2", "-std=c99", "-D_DEFAULT_SOURCE", "-D_DARWIN_C_SOURCE", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-const-variable", "-o", exe] + srcs + ["-lz", "-lm"], capture_output=True, text=True)
    if r.returncode and "zlib.h" in r.stderr:
        pytest.skip("no zlib headers")
    assert r.returncode == 0, r.stderr
    font = os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf")
    digests = []
    for run in ("a", "b"):
        out = tmp_path / run
        out.mkdir()
        p = subprocess.run([exe, font, str(out)], capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr
        sizes = dict(re.findall(r"/([^/\s]+)\.png\s+(\d+x\d+)", p.stdout))
        assert sizes["main-960x600@1x"] == "960x600" and sizes["main-960x600@2x"] == "1920x1200"
        assert sizes["size-min-720x450@1x"] == "720x450" and sizes["size-max-1920x1200@2x"] == "3840x2400"
        assert len(sizes) >= 17
        digests.append({k: hashlib.sha256((out / (k + ".png")).read_bytes()).hexdigest() for k in sizes})
    assert digests[0] == digests[1]


def test_layout_grid_and_looks(tmp_path):
    """Both looks (the aluminium rail over a black body, the aluminium plate with black sections) with the display
    font: every widget on the 4 px grid, every knob on a column centre (or a half-column) and its row, every control
    row one height (frui_render's layout check), and the look's comparison renders written."""
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "frui_render")
    srcs = [os.path.join(UI, f) for f in ("frui.c", "frui_font.c", "frui_gfx.c", os.path.join("tools", "frui_render.c"))]
    r = subprocess.run([cc, "-O2", "-std=c99", "-D_DEFAULT_SOURCE", "-D_DARWIN_C_SOURCE", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-const-variable", "-o", exe] + srcs + ["-lz", "-lm"], capture_output=True, text=True)
    if r.returncode and "zlib.h" in r.stderr:
        pytest.skip("no zlib headers")
    assert r.returncode == 0, r.stderr
    font = os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf")
    dfont = os.path.join(ROOT, "assets", "fonts", "Michroma-Regular.ttf")
    (tmp_path / "directions").mkdir()
    for env in ({}, {"FRUI_DIRECTIONS": "1"}):
        p = subprocess.run([exe, font, str(tmp_path), dfont], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, **env))
        assert p.returncode == 0, p.stderr
        checks = re.findall(r"layout check (\S+): (.*)", p.stdout)
        assert len(checks) >= 6
        assert all(v == "all on the grid" for _, v in checks), p.stdout
    for f in ("A1-rail-main", "A1-rail-tab-comp-delay", "A2-plate-main", "A2-plate-tab-comp-delay", "compare"):
        assert (tmp_path / "directions" / (f + ".png")).stat().st_size > 10000
