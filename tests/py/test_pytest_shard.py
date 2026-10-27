# SPDX-License-Identifier: GPL-3.0-only
"""tools/pytest_shard.py (ci.yml's host-pytest jobs): the 4 shards together are exactly `pytest tests/py`, by
pytest's own collection (every test once, none missing), and the split is deterministic."""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pytest_shard as ps  # noqa: E402


def collect(paths):
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return [l.strip() for l in r.stdout.splitlines() if "::" in l]


def test_the_shards_are_the_whole_suite():
    full = collect(["tests/py"])
    shards = ps.split(ps.test_files(), 4)
    got = [t for s in shards for t in collect(s)]
    assert all(shards) and len(got) == len(full) and sorted(got) == sorted(full)


def test_a_partition_and_deterministic():
    files = ps.test_files()
    for n in (1, 3, 4, 6):
        sh = ps.split(files, n)
        flat = [f for s in sh for f in s]
        assert sorted(flat) == files and len(flat) == len(set(flat))
        assert sh == ps.split(list(reversed(files)), n)            # the order given does not matter
    w = ps.weights()
    assert w and all(f in files for f in w)                        # no stale names in the weights
    # an unknown file (a new test) counts as the median and still lands in exactly one shard
    sh = ps.split(files + ["tests/py/test_new.py"], 4)
    assert sum(s.count("tests/py/test_new.py") for s in sh) == 1


def test_cli(capsys):
    assert ps.main(["--check", "4"]) == 0
    assert "ok: every test file in exactly one shard" in capsys.readouterr().out
    assert ps.main(["2", "4"]) == 0
    assert capsys.readouterr().out.split() == ps.split(ps.test_files(), 4)[1]
    assert ps.main(["5", "4"]) == 2
