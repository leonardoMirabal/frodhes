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
    # the SMK-37's pictures (docs/manual/img/smk/, the SMK host build), the same rule
    smk = os.path.join(IMG, "smk")
    made = sorted(f for f in os.listdir(tmp_path / "smk") if f.endswith(".png"))
    assert made == sorted(f for f in os.listdir(smk) if f.endswith(".png")), "img/smk/: run tools/manual_shots.py"
    stale = [f for f in made if not np.array_equal(np.asarray(Image.open(tmp_path / "smk" / f).convert("RGB")),
                                                   np.asarray(Image.open(os.path.join(smk, f)).convert("RGB")))]
    assert not stale, f"stale SMK-37 screenshots {stale}: run tools/manual_shots.py"


# where each picture belongs: the heading (### or ##) above its first link in docs/MANUAL.md names what it shows. A
# page's shot sits under that page's heading (tools/manual_shots.py NAME, by file stem); the others are listed here.
WHERE = {"voice1": "VOICE 1/3 and 2/3", "voice2": "VOICE 1/3 and 2/3", "voice3": "VOICE 3/3: velocity",
         "voice4-vibes": "VOICE 3/3: velocity", "voice3-marimba": "VOICE 3/3: velocity", "piano": "FRODHES (the main page)",
         "reverb-inf": "REVERB", "splash": "Your first sound", "browse": "6. Presets", "save": "6. Presets",
         "bypass": "7. Effects on and off", "piano-vibes": "VIBES: the vibraphone",
         "voice1-vibes": "VIBES: the vibraphone", "voice2-vibes": "VIBES: the vibraphone",
         "voice3-vibes": "The motor: on and off", "piano-marimba": "MARIMBA", "voice1-marimba": "MARIMBA",
         "voice2-marimba": "MARIMBA"}
README_ONLY = {"overview", "thumbnail"}                      # the README's pictures (tools/manual_shots.py makes both;
                                                             # the social preview, thumbnail, is linked from docs/BUILD.md)


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
    man, readme = _links("docs/MANUAL.md"), {**_links("docs/BUILD.md"), **_links("README.md")}
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


def test_every_device_picture_is_linked_from_its_manual():
    """the SMK-37's pictures (img/smk/) are linked from docs/SMK37.md, the Move's (img/move/) from docs/MOVE.md;
    the MANUAL's appendices 15 and 16 show one each"""
    man = open(os.path.join(ROOT, "docs", "MANUAL.md"), encoding="utf-8").read()
    for folder, doc, app in (("smk", "SMK37.md", "## 15. "), ("move", "MOVE.md", "## 16. ")):
        text = open(os.path.join(ROOT, "docs", doc), encoding="utf-8").read()
        for f in sorted(os.listdir(os.path.join(IMG, folder))):
            if f.endswith(".png"):
                assert f"manual/img/{folder}/{f}?v=" in text, f"img/{folder}/{f} is not linked from docs/{doc}"
        sec = man[man.index(app):]
        sec = sec[:sec.find("\n## ", 1)] if "\n## " in sec[1:] else sec
        assert f"manual/img/{folder}/" in sec and f"({doc})" in sec, f"MANUAL {app.strip()}: a picture and {doc}"
