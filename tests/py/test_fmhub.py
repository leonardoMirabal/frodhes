# SPDX-License-Identifier: GPL-3.0-only
"""fmhub.json, the card The Firmware Hub shows (read from main about every 3 h): within the hub's limits, and its
picture names no version (the manual's start screen showed VERSION DEV)."""
import json
import os
import re

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMG = os.path.join(ROOT, "docs", "manual", "img")


def test_fmhub_json_within_the_hubs_limits():
    d = json.load(open(os.path.join(ROOT, "fmhub.json")))
    assert set(d) <= {"what", "author", "kinds", "play", "editor", "cheat", "image", "color", "sounds", "smk37"}
    assert 0 < len(d["what"]) <= 300 and "<" not in d["what"] and "http" not in d["what"]
    assert len(d["author"]) <= 60
    assert d["kinds"] and set(d["kinds"]) <= {"synth", "groove", "drums", "chords", "tracker", "other"}
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", d["color"]) and sum(int(d["color"][i:i + 2], 16) for i in (1, 3, 5)) > 200
    assert d["sounds"] == "none"                                       # no samples, ever
    assert set(d["smk37"]) <= {"what", "image", "install", "guide"} and len(d["smk37"]["what"]) <= 300
    assert all(d["smk37"][k].startswith("https://") for k in ("install", "guide"))
    for p in {d["image"], d["smk37"]["image"]}:
        f = os.path.join(ROOT, p)
        w, h = Image.open(f).size
        assert w == h and 240 <= w <= 1024 and os.path.getsize(f) <= 300 * 1024 and p.endswith((".png", ".webp", ".jpg"))


def test_the_start_screen_pictures_name_no_version():
    """splash.png (the manual) and thumbnail.png (the hub card) are the start screen without its VERSION line: the
    rows where the firmware draws it (210..229 at 240 px) are background"""
    for n in ("splash.png", "thumbnail.png"):
        im = Image.open(os.path.join(IMG, n)).convert("RGB").resize((240, 240), Image.NEAREST)
        bg = im.getpixel((0, 239))
        assert all(im.getpixel((x, y)) == bg for y in range(205, 236) for x in range(240)), n
