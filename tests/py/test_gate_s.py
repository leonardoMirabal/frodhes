# SPDX-License-Identifier: GPL-3.0-only
"""tools/gate_s.py with fake packages and a fake builder (no toolchain, no Docker, no lock)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate_s  # noqa: E402

NAMES = [n for n, _ in gate_s.PACKAGES]


def put(d, content):
    d.mkdir(parents=True, exist_ok=True)
    for n in NAMES:
        (d / n).write_bytes(content(n))


def test_equal_packages_pass(tmp_path, capsys):
    put(tmp_path / "a", lambda n: b"pkg " + n.encode())
    put(tmp_path / "b", lambda n: b"pkg " + n.encode())
    assert gate_s.main(["--cur", str(tmp_path / "a"), "--base", str(tmp_path / "b")]) == 0
    out = capsys.readouterr().out
    assert "Gate S: PASS (4 packages byte-identical)" in out and out.count("equal") == 4


def test_one_byte_differs(tmp_path, capsys):
    put(tmp_path / "a", lambda n: b"pkg " + n.encode())
    put(tmp_path / "b", lambda n: b"pkg " + n.encode() + (b"!" if "elite" in n else b""))
    assert gate_s.main(["--cur", str(tmp_path / "a"), "--base", str(tmp_path / "b")]) == 1
    out = capsys.readouterr().out
    assert "DIFFERS  frodhes-smk37-elite.fwsc" in out and "FAIL (1 of 4" in out


def test_missing_package_fails(tmp_path):
    put(tmp_path / "a", lambda n: b"x")
    put(tmp_path / "b", lambda n: b"x")
    (tmp_path / "a" / "frodhes-smk37-mke.fwsc").unlink()
    rows = gate_s.compare(tmp_path / "a", tmp_path / "b")
    bad = [r for r in rows if not r[3]]
    assert [r[0] for r in bad] == ["frodhes-smk37-mke.fwsc"] and bad[0][1] is None
    text, ok = gate_s.report(rows)
    assert not ok and "HEAD missing" in text


def test_build_both_with_a_fake_builder(tmp_path):
    """the order (HEAD first, then the base worktree), the build arguments, nice, the worktree removed after"""
    cur, base = tmp_path / "cur_tree", tmp_path / "base_tree"
    cur.mkdir()
    calls = []

    def run(cmd, cwd=None, env=None, **kw):
        calls.append((cmd, Path(cwd)))
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[4]).mkdir(parents=True)
        elif cmd[:3] == ["git", "worktree", "remove"]:
            import shutil
            shutil.rmtree(cmd[4])
        elif "./build.sh" in cmd:
            assert cmd[:3] == ["nice", "-n", "19"] and env["JIELI_TOOLCHAIN"] and env["AC79_SDK"]
            args = cmd[4:]
            name = next(n for n, a in gate_s.PACKAGES if a == args)
            (Path(cwd) / "build").mkdir(exist_ok=True)
            (Path(cwd) / "build" / name).write_bytes(b"same " + name.encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    c, b = gate_s.build_both("HEAD", tmp_path / "out", run=run, take_lock=False, cur_tree=cur, base_tree=base)
    builds = [(cmd[4:], cwd) for cmd, cwd in calls if "./build.sh" in cmd]
    assert [a for a, _ in builds] == [a for _, a in gate_s.PACKAGES] * 2
    assert {cwd for _, cwd in builds[:4]} == {cur} and {cwd for _, cwd in builds[4:]} == {base}
    assert not base.exists()
    text, ok = gate_s.report(gate_s.compare(c, b))
    assert ok, text


def test_failed_build_stops(tmp_path):
    def run(cmd, cwd=None, env=None, **kw):
        return SimpleNamespace(returncode=2 if "./build.sh" in cmd else 0, stdout="", stderr="")
    try:
        gate_s.build_tree(tmp_path, tmp_path / "out", run=run, env={})
    except SystemExit as e:
        assert gate_s.PACKAGES[0][0] in str(e)        # (the first build named)
    else:
        raise AssertionError("a failed build must stop Gate S")


def test_no_args_is_an_error(capsys):
    try:
        gate_s.main([])
    except SystemExit as e:
        assert e.code == 2


def test_the_fm1_builds_last():
    """each ./build.sh rewrites build/frodhes.bin: the FM-1's last, so the checkout ends as ./build.sh leaves it"""
    assert gate_s.PACKAGES[-1] == ("frodhes.fwsc", [])
