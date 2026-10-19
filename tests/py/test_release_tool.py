# SPDX-License-Identifier: GPL-3.0-only
"""tools/release.py without network or device: the version logic, the argument parsing, the HIL checks, the notes
assembled from docs/changes/ (nothing bumped in README / MANUAL), the verify step, and a whole --dry-run with git /
gh / make replaced by a fake shell (it must stop before any push)."""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release as rel  # noqa: E402


def test_next_version():
    assert rel.next_version("v0.9.2", "patch") == "0.9.3"
    assert rel.next_version("v0.9.2", "minor") == "0.10.0"
    assert rel.next_version("v0.9.2", "1.0.0") == "1.0.0"
    assert rel.next_version(None, "patch") == "0.0.1"
    for bad in ("0.9.2", "0.9.1", "1.0", "x"):
        with pytest.raises(ValueError):
            rel.next_version("v0.9.2", bad)


def test_parse_args():
    a = rel.parse_args([])
    assert (a.version, a.dry_run, a.no_device, a.no_emulator, a.yes) == ("patch", False, False, False, False)
    a = rel.parse_args(["minor", "--dry-run", "--no-device", "--no-emulator", "--yes"])
    assert (a.version, a.dry_run, a.no_device, a.no_emulator, a.yes) == ("minor", True, True, True, True)
    assert rel.parse_args(["0.9.7"]).version == "0.9.7"
    with pytest.raises(SystemExit):
        rel.parse_args(["0.9"])


def test_cpu_and_jack_checks():
    ok = "a   voices shed 0\nb   voices shed 0\n\nworst 4321 us with 9 voices (74 %); design target 4500 us"
    assert "4321" in rel.check_cpu("rhodes", ok)
    with pytest.raises(rel.Stop):
        rel.check_cpu("rhodes", ok.replace("4321", "4600"))
    with pytest.raises(rel.Stop):
        rel.check_cpu("vibes", ok.replace("shed 0\nb", "shed 2\nb"))
    with pytest.raises(rel.Stop):
        rel.check_cpu("vibes", "no output")
    assert "SNR 80.0" in rel.check_jack("rhodes", dict(loud=dict(jack_peak_db=-15.2), snr_db=80.0))
    with pytest.raises(rel.Stop):
        rel.check_jack("rhodes", dict(loud=dict(jack_peak_db=-60.0), snr_db=20.0))


def fake_shell(root, calls, notes_draft=False):
    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        calls.append(s)
        replies = {
            "git tag --sort=-v:refname --list v*": "v0.9.2\nv0.9.1\n",
            "git rev-parse --abbrev-ref HEAD": "main\n",
            "git status --porcelain --untracked-files=no": "",
            "git tag --list v0.9.3": "",
            "git ls-remote --tags origin refs/tags/v0.9.3": "",
            "git rev-parse HEAD": "abcdef1234567890\n",
            "git rev-list --count HEAD..origin/main": "0\n",
            "git rev-list --count origin/main..HEAD": "1\n",
            "git log --oneline origin/main..HEAD": "abcdef1 something\n",
        }
        if s.startswith("./build.sh --release"):
            os.makedirs(os.path.join(root, "build"), exist_ok=True)
            open(os.path.join(root, "build", f"frodhes-{cmd[-1]}.fwsc"), "wb").write(b"fw")
        return 0, replies.get(s, "1 passed\n")
    return sh


MANUAL = ("# Frodhes: user manual\n\n**A physically modelled Fender Rhodes and a test bar for the M-VAVE FM-1.**\n\n"
          f"1. Download [`frodhes.fwsc`]({rel.LATEST_DL}).\n")
FRAG = "## New\n- **A new thing.**\n  Its second line.\n\n## Fixed\n- A fix, as in 0.9.2.\n"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "changes").mkdir(parents=True)
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "a.png").write_bytes(b"png")
    h = rel.doc_links.short_hash(str(tmp_path / "docs" / "img" / "a.png"))
    (tmp_path / "README.md").write_text("Latest release: [the release page](https://github.com/leonardoMirabal/frodhes/releases/latest) "
                                        f"[`frodhes.fwsc`]({rel.LATEST_DL}) ![p](docs/img/a.png?v={h})\n")
    (tmp_path / "docs" / "MANUAL.md").write_text(MANUAL)
    (tmp_path / "docs" / "changes" / "b-thing.md").write_text(FRAG)
    (tmp_path / "docs" / "changes" / "a-other.md").write_text("## Changes\n- Another change.\n\n## Fixed\n- Fix A.\n")
    monkeypatch.setattr(rel, "ROOT", str(tmp_path))
    return tmp_path


def test_dry_run_stops_before_push(tree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("--dry-run must not prompt"))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"]) == 0
    o = capsys.readouterr().out
    assert not [c for c in calls if " push" in c or "tag -a" in c or "git commit" in c or "gh release" in c
                or "git rm" in c or "git add" in c]
    assert "make test" in calls and "./build.sh --release 0.9.3" in calls
    assert not (tree / "docs" / "releases" / "v0.9.3.md").exists()       # nothing is written in a dry run
    assert sorted(os.listdir(tree / "docs" / "changes")) == ["a-other.md", "b-thing.md"]
    assert "--no-device: the FM-1 is NOT tested" in o and "stopped before any push" in o
    assert "docs/changes/: 2 fragment(s), well formed (a-other.md, b-thing.md)" in o
    # the assembled notes, shown in full: head, sections in order, the fragments in file-name order, foot
    shown = o[o.index("-" * 30 + " docs/releases/v0.9.3.md"):]
    notes = "\n".join(l[3:] for l in shown.splitlines())                 # (the log indents by 3)
    assert "**Frodhes 0.9.3**: a physically modelled Fender Rhodes and a test bar for the M-VAVE FM-1." in notes
    assert "python3 tools/fm1_install.py frodhes.fwsc" in notes
    i = [notes.index(s) for s in ("### New in 0.9.3\n- **A new thing.**\n  Its second line.",
                                  "### Changes\n- Another change.", "### Fixed\n- Fix A.\n- A fix, as in 0.9.2.",
                                  "Full manual:")]
    assert i == sorted(i) and "### Tools" not in notes
    for line in (" 4  FM-1           skipped  --no-device", " 7  publish        skipped  --dry-run",
                 " 8  emulator       skipped  --no-emulator", " 3  host           done", " 5  docs           done"):
        assert line in o


def test_bad_fragment_hand_notes_and_existing_tag(tree, monkeypatch, capsys):
    (tree / "docs" / "changes" / "c.md").write_text("> **DRAFT**\n## New\n- x\n")
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1      # a real run stops at step 2
    o = capsys.readouterr().out
    assert "malformed fragment" in o and "docs/changes/c.md:1: a DRAFT line" in o
    assert " 3  host           not run" in o and not [c for c in calls if "make" in c or "push" in c]
    (tree / "docs" / "changes" / "c.md").unlink()
    (tree / "docs" / "releases" / "v0.9.3.md").write_text("notes by hand\n")
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1
    assert "never written by hand" in capsys.readouterr().out

    def tagged(cmd, **kw):
        s = " ".join(cmd) if not isinstance(cmd, str) else cmd
        return 0, ("v0.9.3\n" if s == "git tag --list v0.9.3" else "v0.9.2\n" if "sort" in s else "")
    monkeypatch.setattr(rel, "sh", tagged)
    assert rel.main(["patch", "--dry-run"]) == 1
    assert "v0.9.3 already exists locally" in capsys.readouterr().out


def test_no_fragments_stops(tree, monkeypatch, capsys):
    for f in os.listdir(tree / "docs" / "changes"):
        (tree / "docs" / "changes" / f).unlink()
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1
    assert "no release-note fragments in docs/changes/" in capsys.readouterr().out
    assert not (tree / "docs" / "releases" / "v0.9.3.md").exists()


def release_at_docs(tree, monkeypatch, calls, dry=False):
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    r = rel.Release(rel.parse_args(["patch", "--no-device"] + (["--dry-run"] if dry else [])))
    r.ver, r.prev, r.sha = "0.9.3", "v0.9.2", "f" * 64
    r.frags = rel.changes.load(str(tree))
    return r


def test_docs_step_writes_the_notes_and_removes_the_fragments(tree, monkeypatch):
    calls = []
    r = release_at_docs(tree, monkeypatch, calls)
    ev = r.s_docs()
    notes = (tree / "docs" / "releases" / "v0.9.3.md").read_text()
    assert notes.startswith("**Frodhes 0.9.3**") and "### New in 0.9.3" in notes and "Fix A." in notes
    assert "git rm -q docs/changes/a-other.md docs/changes/b-thing.md" in calls
    assert "git add docs/releases/v0.9.3.md" in calls and any(c.startswith("git commit") for c in calls)
    assert not [c for c in calls if c.startswith("git add") and ("README" in c or "MANUAL" in c)]   # nothing bumped
    assert "doc_links: README.md / docs/MANUAL.md, 0 problem(s)" in ev


def test_docs_step_refuses_stale_links_and_the_next_version(tree, monkeypatch):
    (tree / "docs" / "img" / "a.png").write_bytes(b"a new picture")          # its link's ?v= is now stale
    calls = []
    with pytest.raises(rel.Stop, match=r"README.md:1: docs/img/a.png\?v=[0-9a-f]{8}, the file's hash is"):
        release_at_docs(tree, monkeypatch, calls).s_docs()
    st = release_at_docs(tree, monkeypatch, calls, dry=True).s_docs()           # a dry run warns
    assert st[1] == "warn" and "PROBLEM: README.md:1" in st[0]
    rel.doc_links.fix(str(tree))
    (tree / "docs" / "changes" / "b-thing.md").write_text("## New\n- New in 0.9.3: a thing.\n")
    with pytest.raises(rel.Stop, match="names 0.9.3, the coming release"):
        release_at_docs(tree, monkeypatch, calls).s_docs()
    assert not [c for c in calls if c.startswith("git rm") or c.startswith("git commit")]


def verify_shell(assets, readme, draft=False):
    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if s.startswith("gh release view"):
            return 0, json.dumps({"assets": [{"name": a} for a in assets], "isDraft": draft, "url": "https://r/v0.9.3"})
        if s.startswith("gh api"):
            return 0, readme
        if s.startswith("git tag --sort"):
            return 0, "v0.9.3\nv0.9.2\n"
        return 0, ""
    return sh


def test_verify(monkeypatch, capsys):
    good = f"[`frodhes.fwsc`]({rel.LATEST_DL}) settings saved by 0.9.2 are read as they are"
    both = ["frodhes-0.9.3.fwsc", "frodhes.fwsc"]
    tagged = "https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.3/frodhes.fwsc"
    hits = []

    def resolve(url):
        hits.append(url)
        return (tagged if url == rel.LATEST_DL else None), "a" * 64
    monkeypatch.setattr(rel, "resolve", resolve)
    r = rel.Release(rel.parse_args(["0.9.3"]))
    r.ver, r.sha = "0.9.3", "a" * 64
    monkeypatch.setattr(rel, "sh", verify_shell(both, good))
    assert "README at v0.9.3 names no version" in r.s_verify() and hits == [rel.LATEST_DL]
    for assets, readme, sha, msg in ((both[:1], good, "a" * 64, r"missing \['frodhes.fwsc'\]"),
                                     (both, good, "b" * 64, "the build's bbbb"),
                                     (both, good.replace(rel.LATEST_DL, "frodhes-0.9.3.fwsc"), "a" * 64,
                                      "versions named")):
        monkeypatch.setattr(rel, "sh", verify_shell(assets, readme))
        r.sha = sha
        with pytest.raises(rel.Stop, match=msg):
            r.s_verify()
    monkeypatch.setattr(rel, "resolve", lambda url: ("https://github.com/x/releases/download/v0.9.2/frodhes.fwsc",
                                                     "a" * 64))
    monkeypatch.setattr(rel, "sh", verify_shell(both, good))
    r.sha = "a" * 64
    with pytest.raises(rel.Stop, match="not to v0.9.3's frodhes.fwsc"):
        r.s_verify()
    # --verify-only: step 9 alone, the latest tag, the versioned asset as the reference; nothing else runs
    monkeypatch.setattr(rel, "resolve", resolve)
    hits.clear()
    assert rel.main(["--verify-only"]) == 0
    o = capsys.readouterr().out
    assert "(VERIFY-ONLY): summary" in o and " 9  verify         done" in o and " 3  host           skipped" in o
    assert hits == ["https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.3/frodhes-0.9.3.fwsc",
                    rel.LATEST_DL]
