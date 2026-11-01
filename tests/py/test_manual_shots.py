# SPDX-License-Identifier: GPL-3.0-only
"""The committed screenshots (docs/manual/img/*.png) are what tools/manual_shots.py draws from the code now: a PR that
changes the UI must re-run it (and a conflict on a PNG is resolved by re-running it, never by hand).

The pictures are compared pixel for pixel (decoded RGB, no tolerance: the host build is integer code and draws the same
frame everywhere). Not the file bytes: the PNG encoder's output depends on the Pillow / zlib build (CI installs Pillow
unpinned), so the same pixels can compress to other bytes; a PNG that only re-encoded is not stale. (On the
2026-10-20 macOS venv the bytes match too.)"""
import os
import re
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


# where each picture belongs: the heading (### or ##) above its first link in docs/MANUAL.md names what it shows. A
# page's shot sits under that page's heading (tools/manual_shots.py NAME, by file stem); the others are listed here.
WHERE = {"voice1": "VOICE 1/2 and 2/2", "voice2": "VOICE 1/2 and 2/2", "piano": "FRODHES (the main page)",
         "reverb-inf": "REVERB", "splash": "1. Install", "browse": "6. Presets", "save": "6. Presets",
         "bypass": "7. Effects on and off", "piano-vibes": "VIBES: the vibraphone",
         "voice1-vibes": "VIBES: the vibraphone", "voice2-vibes": "VIBES: the vibraphone",
         "voice3-vibes": "The motor: on and off", "piano-marimba": "MARIMBA", "voice1-marimba": "MARIMBA",
         "voice2-marimba": "MARIMBA"}
README_ONLY = {"overview", "thumbnail"}                      # the README's pictures (tools/manual_shots.py makes both)


def _links(name):
    """{file stem: [the heading above each of its links]} for the docs/manual/img/*.png (not move/) a doc links"""
    out, head = {}, ""
    for line in open(os.path.join(ROOT, name), encoding="utf-8"):
        if line.startswith("#"):
            head = line.lstrip("#").strip()
        for m in re.finditer(r"manual/img/([\w-]+)\.png", line):
            out.setdefault(m.group(1), []).append(head)
    return out


def test_every_screenshot_is_linked_next_to_its_text():
    """no orphan: every committed screenshot is linked; and the MANUAL's first link to it sits under the heading of
    the page (or section) it shows. The intro picture above the contents (piano.png) is skipped as a first link."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import manual_shots
    page = {v: k for k, v in manual_shots.NAME.items()}
    man, readme = _links("docs/MANUAL.md"), _links("README.md")
    unused, misplaced = [], []
    for f in sorted(os.listdir(IMG)):
        if not f.endswith(".png"):
            continue
        stem = f[:-4]
        if stem in README_ONLY:
            if stem not in readme:
                unused.append(f)
            continue
        heads = [h for h in man.get(stem, []) if h != "Frodhes: user manual"]   # the title's intro picture
        if not heads:
            unused.append(f)
            continue
        want = WHERE.get(stem, page.get(stem))
        if heads[0] != want:
            misplaced.append(f"{f} under {heads[0]!r}, expected {want!r}")
    assert not unused, f"screenshots no doc links (link them, or remove them if their page went): {unused}"
    assert not misplaced, "screenshots away from the text they show (or WHERE needs the new one):\n" + "\n".join(misplaced)
