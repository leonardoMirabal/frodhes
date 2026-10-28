# SPDX-License-Identifier: GPL-3.0-only
"""tools/pytest_run.py: a missing test path, an empty list and "no tests collected" (pytest exit 5) all fail, under
pytest -n as well (where a missing file printed only "no tests ran"); a good list keeps pytest's own result. And the
wrappers use it: make pytest, ci_move.sh, release.py's docs step, the CI workflows' explicit lists."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUN = os.path.join(ROOT, "tools", "pytest_run.py")


@pytest.fixture
def suite(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "test_bad.py").write_text("def test_b():\n    assert False\n")
    (tmp_path / "test_none.py").write_text("X = 1\n")
    return tmp_path


def run(cwd, *args):
    r = subprocess.run([sys.executable, RUN, "-q", "-p", "no:cacheprovider", *args], cwd=cwd, capture_output=True,
                       text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def test_good_and_failing_lists_keep_pytests_result(suite):
    assert run(suite, "--", "test_ok.py")[0] == 0
    assert run(suite, "--", "test_ok.py::test_a")[0] == 0
    assert run(suite, "--", "test_ok.py", "test_bad.py")[0] == 1


@pytest.mark.parametrize("xdist", [False, True], ids=["serial", "n2"])
def test_a_missing_path_fails_loudly(suite, xdist):
    pytest.importorskip("xdist") if xdist else None
    opts = ["-n", "2"] if xdist else []
    code, out = run(suite, *opts, "--", "test_ok.py", "test_misspelt.py")
    assert code == 4 and "no such test path(s): test_misspelt.py" in out, out
    code, out = run(suite, *opts, "--", "test_ok.py::test_a", "test_gone.py::test_x")
    assert code == 4 and "test_gone.py::test_x" in out, out


@pytest.mark.parametrize("xdist", [False, True], ids=["serial", "n2"])
def test_nothing_collected_fails(suite, xdist):
    pytest.importorskip("xdist") if xdist else None
    opts = ["-n", "2"] if xdist else []
    code, out = run(suite, *opts, "--", "test_none.py")
    assert code == 1 and "no tests collected" in out, out
    code, out = run(suite, *opts, "-k", "nothing_matches", "--", "test_ok.py")
    assert code == 1 and "no tests collected" in out, out


def test_an_empty_list_fails(suite):
    code, out = run(suite, "--")
    assert code == 4 and "no test paths given" in out, out
    r = subprocess.run([sys.executable, RUN, "test_ok.py"], cwd=suite, capture_output=True, text=True)
    assert r.returncode != 0 and "after '--'" in r.stderr


def test_an_empty_shard_fails():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pytest_shard.py"), "400", "400"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "empty" in r.stderr and not r.stdout.strip()


def test_the_wrappers_use_it():
    text = lambda p: open(os.path.join(ROOT, p)).read()            # noqa: E731
    mk = text("Makefile")
    rule = mk[mk.index("\npytest:"):mk.index("\n\n", mk.index("\npytest:"))]
    assert "tools/pytest_run.py" in rule and "-m pytest" not in rule
    assert "tools/pytest_run.py" in text("tools/ci_move.sh") and "-m pytest" not in text("tools/ci_move.sh")
    assert '"tools/pytest_run.py", "-q", "--"' in text("tools/release.py")
    for wf in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        lines = [ln for ln in text(wf).splitlines() if "pytest" in ln and "tests/py/" in ln and "pip" not in ln
                 and not ln.lstrip().startswith("#")]
        assert lines and all("tools/pytest_run.py" in ln for ln in lines), lines
