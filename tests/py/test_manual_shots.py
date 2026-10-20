# SPDX-License-Identifier: GPL-3.0-only
"""The committed screenshots (docs/manual/img/*.png) are what tools/manual_shots.py draws from the code now: a PR that
changes the UI must re-run it (and a conflict on a PNG is resolved by re-running it, never by hand).

The pictures are compared pixel for pixel (decoded RGB, no tolerance: the host build is integer code and draws the same
frame everywhere). Not the file bytes: the PNG encoder's output depends on the Pillow / zlib build (CI installs Pillow
unpinned), so the same pixels can compress to other bytes; a PNG that only re-encoded is not stale. (On the
2026-10-20 macOS venv the bytes match too.)"""
import os
import subprocess
import sys

import numpy as np
from PIL import Image

import hostrun

ROOT = hostrun.ROOT
IMG = os.path.join(ROOT, "docs", "manual", "img")


def test_screenshots_are_what_the_code_draws(tmp_path):
    hostrun.build()
    env = dict(os.environ, FRODHES_HOST_BIN=hostrun.BIN)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "manual_shots.py"), "--out", str(tmp_path)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    made = sorted(f for f in os.listdir(tmp_path) if f.endswith(".png"))
    have = sorted(f for f in os.listdir(IMG) if f.endswith(".png"))
    assert made == have, (f"pictures made but not committed {sorted(set(made) - set(have))}, committed but no longer "
                          f"made {sorted(set(have) - set(made))}: run tools/manual_shots.py (git add / git rm)")
    stale = []
    for f in made:
        a, b = Image.open(tmp_path / f), Image.open(os.path.join(IMG, f))
        if a.size != b.size or not np.array_equal(np.asarray(a.convert("RGB")), np.asarray(b.convert("RGB"))):
            stale.append(f)
    assert not stale, f"stale screenshots {stale}: run tools/manual_shots.py and commit docs/manual/img and the docs"
