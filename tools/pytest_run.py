#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""pytest on a list of test paths, failing loudly where pytest stays quiet.

    tools/pytest_run.py [pytest options ...] -- PATH [PATH ...]

Every PATH (a file, a folder, or FILE::TEST) must exist, and at least one must be given: else exit 4, naming the
missing ones (pytest -n N with a missing file prints only "no tests ran" and exits 0 or 5, so a misspelt name in a
gate's list ran nothing and passed). pytest's exit 5 (no tests collected) becomes 1: a list that selects nothing is a
failure. Every other exit code is pytest's own. The interpreter is this one (sys.executable). Used by `make pytest`
(its shards, tools/pytest_shard.py), tools/ci_move.sh, tools/release.py and the CI workflows' explicit lists."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def split(argv):
    """argv -> (pytest options, paths); the paths come after '--'"""
    if "--" not in argv:
        raise SystemExit("pytest_run: give the test paths after '--' (tools/pytest_run.py [options] -- PATH ...)")
    i = argv.index("--")
    return argv[:i], argv[i + 1:]


def missing(paths, cwd=None):
    cwd = cwd or os.getcwd()
    return [p for p in paths if not os.path.exists(os.path.join(cwd, p.split("::", 1)[0]))]


def main(argv=None, cwd=None):
    opts, paths = split(list(sys.argv[1:] if argv is None else argv))
    if not paths:
        print("pytest_run: FAIL: no test paths given (an empty shard or list runs nothing)", file=sys.stderr)
        return 4
    gone = missing(paths, cwd)
    if gone:
        print("pytest_run: FAIL: no such test path(s): " + ", ".join(gone), file=sys.stderr)
        return 4
    code = subprocess.run([sys.executable, "-m", "pytest", *opts, *paths], cwd=cwd).returncode
    if code == 5:
        print(f"pytest_run: FAIL: no tests collected from {len(paths)} path(s) (pytest exit 5)", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
