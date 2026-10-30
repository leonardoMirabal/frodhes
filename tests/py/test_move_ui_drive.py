# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_ui_drive.py without the Move: the device script compiles; a shot taken while display_mirror is 0 is
saved and marked STALE (the mirror's last frame: 2026-10-30 one showed the page count of a sound loaded earlier)"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_ui_drive as ud  # noqa: E402


def test_device_script_compiles():
    compile(ud.DEVICE % (ud.BENCH, ud.KEEP), "device", "exec")


def test_stale_shot_marked(tmp_path):
    fb1, fb2 = bytes([0xFF] + [0] * 1023), bytes([0x0F] * 1024)
    out = "WATCH x\nSTALE\nSHOT %s\nSHOT %s\n" % (fb1.hex(), fb2.hex())
    lines = ud.save_shots(out, ["a", "b"], str(tmp_path))
    assert len(lines) == 2
    assert "a.png (8 px lit)" in lines[0] and "STALE" in lines[0]
    assert "b.png (4096 px lit)" in lines[1] and "STALE" not in lines[1]
    assert os.path.getsize(tmp_path / "a.png") > 0 and os.path.getsize(tmp_path / "b.png") > 0
