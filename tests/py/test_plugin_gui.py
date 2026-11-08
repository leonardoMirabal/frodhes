# SPDX-License-Identifier: GPL-3.0-only
"""The plugin window (B-M2b), macOS: the built Frodhes.clap opens its window in a real NSWindow through CLAP's gui
extension (plugin/tests/gui_shot.m), is driven as a user drives it (knob drags with one host gesture each, shift for
fine, the wheel, the arrow keys, double click for the default, a menu, the tabs, the on-screen keyboard, the knob's
right-click menu, the gear), follows host automation and the host's notes, resizes at 16:10 and closes / opens again.
Every picture the window showed (read back from its own surface) must be, pixel for pixel, what the reference
renderer (plugin/ui/tools/frui_render.c) draws from the same frui_model: the window adds nothing and loses nothing.

Needs the plugin built (make plugin; FRP_BUILD = its build directory). FRP_GUI_HEADLESS=1 runs without a window server
(the window's core, painted by the test, instead of the NSView)."""
import os
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

from hostrun import ROOT

BUILD = os.environ.get("FRP_BUILD", os.path.join(ROOT, "build", "plugin"))
if not os.path.isabs(BUILD):
    BUILD = os.path.join(ROOT, BUILD)
UI = os.path.join(ROOT, "plugin", "ui")


def _png_pixels(path):
    """(width, height, the raw scanlines): both writers use filter 0 on every row, so equal pixels = equal bytes"""
    with open(path, "rb") as f:
        d = f.read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, w, h = 8, b"", 0, 0
    while i < len(d):
        n, t = struct.unpack(">I4s", d[i:i + 8])
        body = d[i + 8:i + 8 + n]
        if t == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif t == b"IDAT":
            idat += body
        i += 12 + n
    return w, h, zlib.decompress(idat)


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS window (Windows: B-M4)")
def test_window_matches_the_renderer(tmp_path):
    shot = os.path.join(BUILD, "frp_gui_shot")
    clap = os.path.join(BUILD, "bundles", "Frodhes.clap")
    if not (os.path.exists(shot) and os.path.isdir(clap)):
        pytest.skip("the plugin is not built (make plugin)")
    cc = shutil.which("cc")
    ref = str(tmp_path / "frui_render")
    srcs = [os.path.join(UI, f) for f in ("frui.c", "frui_font.c", "frui_gfx.c", "frui_text.c",
                                          os.path.join("tools", "frui_render.c"))]
    # -ffp-contract=off as the plugin's build (plugin/CMakeLists.txt): no fused multiply-adds, the same float results
    r = subprocess.run([cc, "-O2", "-ffp-contract=off", "-std=c99", "-D_DEFAULT_SOURCE", "-D_DARWIN_C_SOURCE", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-const-variable", "-o", ref] + srcs + ["-lz", "-lm"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "shots"
    out.mkdir()
    args = [shot, clap, str(out)] + (["--headless"] if os.environ.get("FRP_GUI_HEADLESS") else [])
    p = subprocess.run(args, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0 and "FAIL" not in p.stdout, p.stdout + p.stderr
    shots = [ln.split()[1:] for ln in p.stdout.splitlines() if ln.startswith("shot ")]
    assert len(shots) >= 7, p.stdout
    font = os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf")
    dfont = os.path.join(ROOT, "assets", "fonts", "Michroma-Regular.ttf")
    for name, size in shots:
        rdir = tmp_path / ("ref-" + name)
        rdir.mkdir()
        q = subprocess.run([ref, font, str(rdir), dfont], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, FRUI_MODEL=str(out / (name + ".model")), FRUI_SIZE=size))
        assert q.returncode == 0, q.stderr
        a, b = _png_pixels(out / (name + ".png")), _png_pixels(rdir / "model.png")
        assert a[:2] == b[:2], (name, a[:2], b[:2])
        assert a[2] == b[2], "%s: the window's picture differs from the renderer's (%s)" % (name, size)
