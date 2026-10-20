# SPDX-License-Identifier: GPL-3.0-only
"""tools/changes.py: the release-note fragments in docs/changes/ (one per change, so two PRs never edit the same
notes file) are well formed, and tools/release.py assembles them in a stable order."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import changes as ch  # noqa: E402


def test_repo_fragments_are_well_formed():
    frags = ch.load()                                   # raises with every problem
    ch.tagline()                                        # the notes' head line comes from the manual
    notes = ch.assemble("9.9.9", frags) if frags else ""
    assert not frags or notes.startswith("**Frodhes 9.9.9**: a physically modelled ")


def test_parse():
    secs = ch.parse("<!-- topic: x -->\n## New\n- a\n  more of a\n  - a sub-item\n\n- b\n## Tools\n- t\n")
    assert secs == {"New": "- a\n  more of a\n  - a sub-item\n\n- b", "Tools": "- t"}
    for text, msg in (("## New\n- a\n## New\n- b\n", "a second time"),
                      ("## Fixed\n- a\n## New\n- b\n", "after '## Fixed'"),
                      ("## Fixes\n- a\n", "heading '## Fixes'"),
                      ("### New\n- a\n", "heading '### New'"),
                      ("Intro.\n## New\n- a\n", "text before the first section"),
                      ("## New\nplain text\n", "not a list line"),
                      ("## New\n\n## Tools\n- t\n", "'## New' is empty"),
                      ("> **DRAFT** x\n## New\n- a\n", "a DRAFT line"),
                      ("", "no section")):
        with pytest.raises(ch.Bad, match=msg):
            ch.parse(text)


def write(tmp_path, files):
    d = tmp_path / "docs" / "changes"
    d.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "MANUAL.md").write_text("**A physically modelled thing for the M-VAVE FM-1.** More.\n")
    for k, v in files.items():
        (d / k).write_text(v)
    return str(tmp_path)


def test_load_and_assemble_in_file_name_order(tmp_path):
    r = write(tmp_path, {"zeta.md": "## New\n- Z new\n## Fixed\n- Z fix\n", "alpha.md": "## Fixed\n- A fix\n",
                         "m-1.md": "## Changes\n- M change\n"})
    frags = ch.load(r)
    assert [f for f, _ in frags] == ["alpha.md", "m-1.md", "zeta.md"]
    n = ch.assemble("1.2.3", frags, r)
    assert n.startswith("**Frodhes 1.2.3**: a physically modelled thing for the M-VAVE FM-1. Every note")
    assert "`frodhes.fwsc` below (the same file as `frodhes-1.2.3.fwsc`" in n and ch.DOWNLOAD in n
    order = [n.index(s) for s in ("### Install", "### New in 1.2.3\n- Z new", "### Changes\n- M change",
                                  "### Fixed\n- A fix\n- Z fix", "Full manual:")]
    assert order == sorted(order) and "### Tools" not in n and "### Tested" not in n
    assert ch.assemble("1.2.3", ch.load(r), r) == n                      # stable


def test_refusals(tmp_path):
    with pytest.raises(ch.Bad, match="no release-note fragments"):
        ch.assemble("1.2.3", ch.load(write(tmp_path, {})), str(tmp_path))
    r = write(tmp_path, {"Bad_Name.md": "## New\n- x\n", "ok.md": "## New\n- x\n", "two.md": "## Nope\n- x\n"})
    with pytest.raises(ch.Bad) as e:
        ch.load(r)
    assert "Bad_Name.md: file name" in str(e.value) and "two.md:1: heading" in str(e.value)
    r = write(tmp_path / "b", {"x.md": "## New\n- The 1.2.3 thing\n- settings saved by 1.2.2 load\n"})
    with pytest.raises(ch.Bad, match="names 1.2.3, the coming release"):
        ch.assemble("1.2.3", ch.load(r), r)
    assert "settings saved by 1.2.2 load" in ch.assemble("1.2.4", ch.load(r), r)      # history is fine
    r = write(tmp_path / "c", {"x.md": "## New\n- see 1.2.30 and 11.2.3\n"})
    assert "1.2.30" in ch.assemble("1.2.3", ch.load(r), r)              # another version, not the coming one
