#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tests/py split into N shards for parallel CI jobs: whole files, deterministic, balanced by their measured time.

    python3 tools/pytest_shard.py K N        the test files of shard K (1..N), one per line (ci.yml: make pytest SHARD=K/N)
    python3 tools/pytest_shard.py --check N  every test file in exactly one of the N shards (exit 1 otherwise)

The weights are tests/py/shard_weights.json (seconds per file, from `pytest --durations=0`; a file not in it counts
as the median). Files are taken heaviest first (then by name) and each goes to the shard with the least time so far
(ties: the lower shard), so the split depends only on the file list and the weights. A whole file stays together:
its module fixtures and conftest.py's xdist groups (one worker each) are unchanged. Every shard together is exactly
`pytest tests/py`; tests/py/test_pytest_shard.py checks that against pytest --collect-only."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests", "py")
WEIGHTS = os.path.join(TESTS, "shard_weights.json")


def test_files(tests=TESTS):
    """tests/py's test files, as pytest collects them (test_*.py), relative to the repository"""
    return sorted(f"tests/py/{f}" for f in os.listdir(tests) if f.startswith("test_") and f.endswith(".py"))


def weights(path=WEIGHTS):
    try:
        with open(path) as f:
            return {k: float(v) for k, v in json.load(f).items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


def split(files, n, w=None):
    """[[files of shard 1], ..., [files of shard n]] (each list sorted)"""
    if n < 1:
        raise ValueError("n >= 1")
    w = weights() if w is None else w
    known = sorted(w[f] for f in files if f in w)
    median = known[len(known) // 2] if known else 1.0
    cost = {f: w.get(f, median) for f in files}
    shards, tot = [[] for _ in range(n)], [0.0] * n
    for f in sorted(files, key=lambda f: (-cost[f], f)):
        k = min(range(n), key=lambda i: (tot[i], i))
        shards[k].append(f)
        tot[k] += cost[f]
    return [sorted(s) for s in shards]


def main(argv):
    if len(argv) == 2 and argv[0] == "--check":
        n = int(argv[1])
        files = test_files()
        shards = split(files, n)
        flat = [f for s in shards for f in s]
        ok = sorted(flat) == files and len(flat) == len(set(flat)) and all(shards)
        w = weights()
        for k, s in enumerate(shards, 1):
            print(f"shard {k}/{n}: {len(s)} files, ~{sum(w.get(f, 0) for f in s):.0f} s")
        print("ok: every test file in exactly one shard" if ok else "FAIL: the shards are not a partition")
        return 0 if ok else 1
    if len(argv) != 2:
        print(__doc__)
        return 2
    k, n = int(argv[0]), int(argv[1])
    if not 1 <= k <= n:
        print(f"shard {k} of {n}: 1..{n}", file=sys.stderr)
        return 2
    print("\n".join(split(test_files(), n)[k - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
