# SPDX-License-Identifier: GPL-3.0-only
"""tools/doc_links.py: every picture link in README.md / docs/MANUAL.md carries ?v= its file's sha256 (8 hex), so a
link changes only when its picture does; no download / 'Latest release' / 'Version' line names a version (the
version lives in the GitHub release alone)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import doc_links as dl  # noqa: E402


def test_repo_docs_links_match_their_files():
    bad = dl.problems()
    assert not bad, "run tools/doc_links.py --fix (or tools/manual_shots.py):\n" + "\n".join(bad)


def test_install_lines_use_the_stable_download():
    for name in dl.DOCS:
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert dl.DOWNLOAD in text, f"{name} does not link {dl.DOWNLOAD}"
        assert "fm1_install.py frodhes.fwsc" in text, f"{name}: the install command is not for frodhes.fwsc"


def tree(tmp_path):
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "a.png").write_bytes(b"picture a")
    (tmp_path / "docs" / "img" / "b.png").write_bytes(b"picture b")
    (tmp_path / "README.md").write_text("![a](docs/img/a.png?v=0.9.3) and ![b](docs/img/b.png)\n"
                                        "Settings saved by 0.9.3 are read as they are.\n")
    (tmp_path / "docs" / "MANUAL.md").write_text('<img src="img/b.png?v=old" width="240"> ![x](https://e.org/x.png)\n')
    return str(tmp_path)


def test_fix_then_check(tmp_path):
    r = tree(tmp_path)
    ha, hb = dl.short_hash(os.path.join(r, "docs/img/a.png")), dl.short_hash(os.path.join(r, "docs/img/b.png"))
    bad = dl.problems(r)
    assert len(bad) == 3 and "README.md:1: docs/img/a.png?v=0.9.3, the file's hash is " + ha in bad
    assert any("has no ?v=" in b for b in bad) and any(b.startswith("docs/MANUAL.md:1:") for b in bad)
    assert sorted(dl.fix(r)) == ["README.md", "docs/MANUAL.md"]
    assert dl.problems(r) == [] and dl.fix(r) == []                     # stable: a second fix changes nothing
    t = open(os.path.join(r, "README.md")).read()
    assert f"a.png?v={ha})" in t and f"b.png?v={hb})" in t and "saved by 0.9.3" in t
    assert f'src="img/b.png?v={hb}" width' in open(os.path.join(r, "docs/MANUAL.md")).read()
    (tmp_path / "docs" / "img" / "a.png").write_bytes(b"picture a, new")   # only the changed picture's link moves
    assert len(dl.problems(r)) == 1 and "a.png" in dl.problems(r)[0]


def test_version_pins_and_missing_files(tmp_path):
    r = tree(tmp_path)
    dl.fix(r)
    (tmp_path / "README.md").write_text(
        "**Latest release: [Frodhes 0.9.3](x)** `frodhes-0.9.3.fwsc`\n![g](docs/img/gone.png?v=1)\n"
        "https://github.com/o/r/releases/download/v0.9.3/x\nVersion 0.9.3.\n0.9.3 had 14 factory presets\n")
    bad = dl.problems(r)
    assert [b.split(":")[1] for b in bad] == ["2", "1", "1", "4", "3"]
    assert "does not exist" in bad[0] and "versioned package name" in bad[1]
