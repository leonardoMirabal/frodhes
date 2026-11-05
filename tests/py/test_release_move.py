# SPDX-License-Identifier: GPL-3.0-only
"""M7-02 (private plan 05): the Move package in the release, without network or device. tools/release.py builds it
in step 3 (make move MOVE_VER=X.Y.Z, after the Move's MT0), checks the --device-done record's "move" section in step
4 (its sha256 = the tarball's, its tiers MIN, + MT1 when Move-only code changed), records "Move package sha256" in
the release commit only when it passed, compares the published asset after the merge, and step 9 expects it only
then. release.yml builds the tarball from the tag (the job move) and tools/move_asset.sh attaches it only when the
release commit records that exact sha256; a different one stops the release."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as rel  # noqa: E402
import test_release_tool as trt  # noqa: E402
from test_release_tool import tree  # noqa: E402,F401  (the fixture)

FW, TAR = hashlib.sha256(b"fw").hexdigest(), hashlib.sha256(b"tar").hexdigest()


def move_shell(root, calls, changed=("docs/MANUAL.md", "firmware/src/usb.c"), commits=None):
    """test_release_tool's fake shell; make move writes the tarball (b"tar"); git commit's message kept"""
    base = trt.fake_shell(root, calls, changed=changed)

    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if s.startswith("make -s move MOVE_VER="):
            os.makedirs(os.path.join(root, "build", "move"), exist_ok=True)
            open(os.path.join(root, rel.MOVE_BUILT), "wb").write(b"tar")
        if s.startswith("git commit") and commits is not None:
            commits.append(input)
        if s.startswith("git diff --cached"):
            calls.append(s)
            return 0, "docs/releases/v0.9.3.md\n"
        return base(cmd, check=check, input=input, timeout=timeout, quiet=quiet)
    return sh


@pytest.fixture
def mtree(tree):  # noqa: F811
    (tree / "move").mkdir()
    (tree / "move" / "move_plugin.c").write_text("/* the plugin */\n")
    return tree


def done(path, fm1=FW, move=TAR, mtiers=("MIN",), flat=False, nomove=False):
    f = dict(sha256=fm1, tiers=["T1"], runs=["hil_scan --tier must"])
    m = dict(sha256=move, tiers=list(mtiers), runs=["move_hil cpu + crackle", "device_null"])
    rec = f if flat else dict(fm1=f, **({} if nomove else dict(move=m)))
    path.write_text(json.dumps(rec))
    return str(path)


def test_dry_run_builds_the_move_and_lists_its_tiers(mtree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rel, "sh", move_shell(str(mtree), calls))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"]) == 0
    o = capsys.readouterr().out
    assert f"env PY={rel.PY} sh tools/ci_move.sh" in calls and "make -s move MOVE_VER=0.9.3" in calls
    assert calls.index("./build.sh --release 0.9.3") < calls.index("make -s move MOVE_VER=0.9.3")
    assert (mtree / "build" / "frodhes-0.9.3-move.tar.gz").read_bytes() == b"tar"
    assert f"build/frodhes-0.9.3-move.tar.gz 3 B sha256 {TAR}" in o
    assert "the Move: tiers needed MIN (MIN on every release; MT1: no Move-only code changed)" in o
    assert "the Move: NOT tested (--no-device without --device-done): this release ships no Move package" in o
    assert not [c for c in calls if " push" in c or "tag -a" in c or "gh release" in c]


def test_dry_run_checks_the_move_record(mtree, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(rel, "sh", move_shell(str(mtree), []))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator", "--device-done", done(tmp_path / "d.json")]) == 0
    o = capsys.readouterr().out
    assert " 4  FM-1           done" in o and "tiers T1 (needed T1)" in o
    assert "the Move: tested on this exact tarball (sha256 equal), tiers MIN (needed MIN)" in o
    # a wrong sha256 stops it (step 4 FAILED, nothing after it)
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator", "--device-done",
                     done(tmp_path / "d.json", move="e" * 64)]) == 1
    o = capsys.readouterr().out
    assert " 4  FM-1           FAILED" in o and f"the Move's sha256 {'e' * 64} is not the release's" in o
    assert " 5  docs           not run" in o
    # Move-only code changed: MT1 needed too
    monkeypatch.setattr(rel, "sh", move_shell(str(mtree), [], changed=["move/move_bridge.h"]))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator", "--device-done", done(tmp_path / "d.json")]) == 1
    o = capsys.readouterr().out
    assert "missing MT1; MT1: Move-only code changed (move/move_bridge.h)" in o
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator", "--device-done",
                     done(tmp_path / "d.json", mtiers=("MIN", "MT1"))]) == 0
    assert "tiers MIN+MT1 (needed MIN+MT1)" in capsys.readouterr().out


def test_device_done_forms(mtree, monkeypatch, tmp_path):
    """the old flat record is the FM-1's (the Move then untested); a record without "move": the Move untested"""
    monkeypatch.setattr(rel, "sh", move_shell(str(mtree), []))
    for kw in (dict(flat=True), dict(nomove=True)):
        r = rel.Release(rel.parse_args(["0.9.3", "--no-device", "--device-done", done(tmp_path / "d.json", **kw)]))
        r.ver, r.prev, r.sha, r.move_sha = "0.9.3", "v0.9.2", FW, TAR
        assert r.device_done().startswith("FM-1: tested before the release on this exact build")
        assert r.move_ok is False and "no \"move\" section" in r.move_line
    with pytest.raises(rel.Stop, match="sha256"):                      # the FM-1's section still checked
        r = rel.Release(rel.parse_args(["0.9.3", "--no-device", "--device-done",
                                        done(tmp_path / "d.json", fm1="0" * 64)]))
        r.ver, r.prev, r.sha, r.move_sha = "0.9.3", "v0.9.2", FW, TAR
        r.device_done()


def test_release_commit_records_the_move_only_when_tested(mtree, monkeypatch, tmp_path):
    for passed in (True, False):
        commits = []
        monkeypatch.setattr(rel, "sh", move_shell(str(mtree), [], commits=commits))
        r = rel.Release(rel.parse_args(["0.9.3", "--no-device", "--device-done",
                                        done(tmp_path / "d.json", nomove=not passed)]))
        r.ver, r.prev, r.sha, r.move_sha = "0.9.3", "v0.9.2", FW, TAR
        r.frags = rel.changes.load(str(mtree))
        r.s_docs()
        msg = commits[-1]
        assert (f"{rel.MOVE_RECORD} {TAR} (tested on this exact tarball" in msg) == passed
        assert ("the Move: NOT tested (no Move package in this release)" in msg) != passed
        for f in ("a-other.md", "b-thing.md"):                        # (put back for the next round)
            (mtree / "docs" / "changes" / f).write_text("## Changes\n- x.\n")
        (mtree / "docs" / "releases" / "v0.9.3.md").unlink()


def test_verify_expects_the_move_only_when_recorded(monkeypatch):
    good = f"[`frodhes.fwsc`]({rel.LATEST_DL})"
    base = ["frodhes-0.9.3.fwsc", "frodhes.fwsc", *rel.SMK_ASSETS, *[rel.smk_versioned(n, "0.9.3") for n in rel.SMK_ASSETS],
            rel.LICENCE_ASSET]
    mv = ["frodhes-0.9.3-move.tar.gz", rel.MOVE_STABLE]
    tagged = "https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.3/frodhes.fwsc"
    monkeypatch.setattr(rel, "resolve", lambda url: (tagged, "a" * 64))

    def verify(assets, recorded, verify_only=False):
        r = rel.Release(rel.parse_args(["0.9.3"] + (["--verify-only"] if verify_only else [])))
        r.ver, r.sha, r.move_recorded = "0.9.3", None if verify_only else "a" * 64, recorded
        monkeypatch.setattr(rel, "sh", trt.verify_shell(assets, good))
        return r.s_verify()
    assert "the Move package: not in this release (not tested)" in verify(base, None)
    assert "frodhes-0.9.3-move.tar.gz" in verify(base + mv, TAR)
    with pytest.raises(rel.Stop, match=r"missing \['frodhes-0.9.3-move.tar.gz', 'frodhes-module.tar.gz'\]"):
        verify(base, TAR)
    with pytest.raises(rel.Stop, match="attached although not recorded as tested"):
        verify(base + mv, None)
    with pytest.raises(rel.Stop, match=r"missing \['frodhes-module.tar.gz'\]"):
        verify(base + mv[:1], None, verify_only=True)
    assert "frodhes-module.tar.gz" in verify(base + mv, None, verify_only=True)


# ---- release.yml and tools/move_asset.sh ----

def git(d, *a, **kw):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(d), *a], env=env, check=True, capture_output=True, text=True, **kw)


def asset(d, tarball):
    return subprocess.run(["sh", os.path.join(ROOT, "tools", "move_asset.sh"), "0.9.9", str(tarball), "out"], cwd=d,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("recorded, built, code, attached", [
    (TAR, b"tar", 0, True),                        # recorded and this tarball: both names
    (TAR, b"other", 1, False),                     # recorded, another tarball: stop
    (None, b"tar", 0, False)])                     # nothing recorded: no Move asset
def test_move_asset_sh(tmp_path, recorded, built, code, attached):
    git(tmp_path, "init", "-q")
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "v0.9.9.md").write_text("notes\n")
    git(tmp_path, "add", ".")
    body = (f"Tested by tools/release.py: host (...); FM-1: ...; the Move: {rel.MOVE_RECORD} {recorded} (tested on "
            f"this exact tarball ...)" if recorded else "Tested by tools/release.py: the Move: NOT tested")
    git(tmp_path, "commit", "-q", "-m", "Release 0.9.9: the notes", "-m", body)
    (tmp_path / "x.txt").write_text("a later commit\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "later")
    (tmp_path / "t.tar.gz").write_bytes(built)
    p = asset(tmp_path, tmp_path / "t.tar.gz")
    assert p.returncode == code, p.stdout + p.stderr
    out = tmp_path / "out"
    names = sorted(os.listdir(out)) if out.exists() else []
    assert names == (["frodhes-0.9.9-move.tar.gz", "frodhes-module.tar.gz"] if attached else [])
    if attached:
        assert all((out / n).read_bytes() == b"tar" for n in names)
    if code:
        assert "is not the tested Move package's" in p.stderr


def test_release_yml_builds_the_move_package():
    import yaml                          # (PyYAML, installed in CI: a missing module fails, never skips)
    j = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]
    mv = j["move"]
    assert mv["runs-on"] == "ubuntu-24.04-arm" and mv["container"] == "debian:bookworm"      # (ci.yml's move job)
    runs = " ".join(s.get("run", "") for s in mv["steps"])
    assert 'make move MOVE_RUN= MOVE_VER="${GITHUB_REF_NAME#v}"' in runs and "make test" not in runs
    up = [s for s in mv["steps"] if s.get("uses", "").startswith("actions/upload-artifact")][0]["with"]
    assert up == {"name": "move-package", "path": rel.MOVE_BUILT.replace(os.sep, "/"), "if-no-files-found": "error"}
    st = j["release"]["steps"]
    names = [s.get("name") or s.get("uses") for s in st]
    a, b, c = (names.index(n) for n in ("the Move package built by the job move", "the Move package, if tested",
                                        "draft release"))
    assert a < b < c and st[a]["with"] == {"name": "move-package", "path": "build/move-ci"}
    assert " ".join(st[b]["run"].split()) == ('sh tools/move_asset.sh "${GITHUB_REF_NAME#v}" '
                                              'build/move-ci/frodhes-module.tar.gz build/publish/move')
    assert st[0]["uses"] == "actions/checkout@v4" and st[0]["with"]["fetch-depth"] == 0     # (the release commit)


def test_release_publish_step_attaches_the_move_when_there(tmp_path):
    import yaml                          # (PyYAML, installed in CI: a missing module fails, never skips)
    steps = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    run = [s for s in steps if s.get("name") == "draft release"][0]["run"]
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gh").write_text('#!/bin/sh\n[ "$2" = view ] && exit 1\n'
                                         'for a in "$@"; do echo "$a"; done > "$GH_LOG"\n')
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "build" / "publish" / "move").mkdir(parents=True)
    for n in ("frodhes-0.9.9.fwsc", "frodhes-0.9.9-smk37.fwsc", "frodhes-0.9.9-smk37-elite.fwsc",
              "frodhes-0.9.9-smk37-mke.fwsc"):
        (tmp_path / "build" / n).write_bytes(b"x")
    for n in ("frodhes-0.9.9-move.tar.gz", "frodhes-module.tar.gz"):
        (tmp_path / "build" / "publish" / "move" / n).write_bytes(b"tar")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", GITHUB_REF_NAME="v0.9.9",
               GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(["sh", "-e", "-c", run], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    args = (tmp_path / "gh.log").read_text().splitlines()
    assert args[11].split("#")[0] == "LICENSES/Apache-2.0.txt"
    assert args[12:15] == ["build/publish/move/frodhes-0.9.9-move.tar.gz", "build/publish/move/frodhes-module.tar.gz",
                          "--title"]
