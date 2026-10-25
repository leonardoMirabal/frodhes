# SPDX-License-Identifier: GPL-3.0-only
"""The public tree stays clean (the 0.9.5 provenance audit): no TRACKED file is
  (a) audio (a recording or sample, by extension), or a binary / very large file outside the known asset folders;
  (b) under a third-party code-copy path (*/sources/*, */vendor/*, */third_party/*, ...) unless allow-listed with
      its licence file;
  (c) carrying a vendor / product name that was replaced by generic wording (a curated list).
The user's own panel names KHLON, TSH8, OHCD, BHLUE and DYNO are allowed (his decision). The research notes, the
third-party copies and the reviewers' scratch moved to private/ in 0.9.5; this keeps them from coming back."""
import fnmatch
import os
import re
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

AUDIO_EXT = {".raw", ".wav", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".sf2", ".sfz",
             ".nki", ".ncw", ".npno", ".exs"}
# binary files may only live here (rendered screenshots, the fonts)
BINARY_OK = ["docs/manual/img/*.png", "assets/fonts/*.ttf", "assets/fonts/*.otf"]
BIG = 1 << 20                                   # text files over 1 MiB: only generated tables / the engines' data
BIG_OK = ["docs/model/data/*.json", "firmware/gen/*.h"]
# third-party code copies: never committed, unless a folder is listed here with its licence file
THIRD_PARTY = re.compile(r"(^|/)(sources|vendor|third_party|third-party|external|extern|ext|deps)/")
THIRD_PARTY_OK = {}                             # "path/prefix/": "path/prefix/LICENSE"
# names replaced by generic wording in 0.9.5 (comments, tools, tests, public text)
BRANDS = re.compile(r"reface|yamaha|ableton|arturia|pigments|kontakt|orchestral mallets|jrhodes|fender", re.I)
FENDER_OK = re.compile(r"trademark", re.I)      # the non-affiliation / trademark lines may name the owners
BRAND_EXEMPT = [
    "tests/py/test_repo_hygiene.py",            # this list
    "docs/model/*",                             # the historical design record (plans, reviews, the data's _doc
                                                # citations): factual references, kept as written
    "docs/releases/v0.[0-8].*.md", "docs/releases/v0.9.[0-4].md",   # release notes as they were published
]
ALLOWED_NAMES = ("KHLON", "TSH8", "OHCD", "BHLUE", "DYNO")          # the user's call: not checked here


def tracked(root=ROOT):
    p = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True)
    if p.returncode:
        pytest.skip("not a git checkout")
    return [f for f in p.stdout.decode("utf-8", "replace").split("\0") if f]


def _match(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def problems(files, root=ROOT):
    bad = []
    for f in files:
        p = os.path.join(root, f)
        if not os.path.isfile(p):
            continue
        if THIRD_PARTY.search(f):
            ok = [k for k in THIRD_PARTY_OK if f.startswith(k)]
            if not ok or not os.path.isfile(os.path.join(root, THIRD_PARTY_OK[ok[0]])):
                bad.append(f"{f}: third-party copy path (allow-list it with its licence file, or keep it private)")
        ext = os.path.splitext(f)[1].lower()
        if ext in AUDIO_EXT:
            bad.append(f"{f}: audio file ({ext}) in the tree")
            continue
        head = open(p, "rb").read(8192)
        if b"\0" in head and not _match(f, BINARY_OK):
            bad.append(f"{f}: binary file outside {BINARY_OK}")
            continue
        if os.path.getsize(p) > BIG and not _match(f, BIG_OK + BINARY_OK):
            bad.append(f"{f}: {os.path.getsize(p)} bytes, outside {BIG_OK}")
        if b"\0" in head or _match(f, BRAND_EXEMPT):
            continue
        for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            for m in BRANDS.finditer(line):
                if m.group(0).lower() == "fender" and FENDER_OK.search(line):
                    continue
                bad.append(f"{f}:{n}: brand name {m.group(0)!r} (use generic wording)")
    return bad


def test_tracked_tree_is_clean():
    bad = problems(tracked())
    assert not bad, "\n".join(bad)


def _repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, data in files.items():
        q = tmp_path / name
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(data)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_a_raw_sample_put_back_fails(tmp_path):
    r = _repo(tmp_path, {"README.md": b"# x\n", "docs/model/research/sources/stk/rawwaves/marmstk1.raw": b"\x01\x02"})
    bad = problems(tracked(r), r)
    assert any("marmstk1.raw: audio file" in b for b in bad)
    assert any("third-party copy path" in b for b in bad)


def test_each_rule_fires(tmp_path):
    r = _repo(tmp_path, {
        "docs/manual/img/ok.png": b"\x89PNG\0\0",                       # allowed binary
        "tools/blob.bin": b"\0" * 10,                                    # binary outside the asset folders
        "tools/huge.txt": b"a" * (BIG + 1),                              # very large text outside the data
        "vendor/lib/x.c": b"int x;\n",                                   # third-party copy
        "firmware/src/a.c": b"/* the Yamaha reface CP chain */\n",       # replaced brand
        "tools/m.py": b"# an Ableton pack\n",
        "LICENSING.md": b"\"Fender\" and \"Rhodes\" are trademarks of their owners\n",   # allowed
        "firmware/src/p.c": b"/* KHLON TSH8 OHCD BHLUE DYNO */\n",       # the user's names: allowed
        "docs/releases/v0.9.4.md": b"a physically modelled Fender Rhodes\n",            # published notes: exempt
    })
    bad = problems(tracked(r), r)
    joined = "\n".join(bad)
    assert "tools/blob.bin: binary" in joined and "tools/huge.txt" in joined and "vendor/lib/x.c" in joined
    assert "firmware/src/a.c:1: brand name 'Yamaha'" in joined and "'reface'" in joined and "tools/m.py:1" in joined
    assert "LICENSING.md" not in joined and "p.c" not in joined and "ok.png" not in joined and "v0.9.4" not in joined
