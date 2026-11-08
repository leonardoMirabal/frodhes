# SPDX-License-Identifier: GPL-3.0-only
"""No test compiles a program into a fixed path under build/ that another worker can run half-written: it builds
through hostrun.once (a temporary name, then an atomic rename, under a file lock) or into a name of its own (the
process id, the xdist worker, pytest's tmp_path). Main's CI 2026-11-05 (run 42032978889): test_eq compiled
build/host/eq_test in place and running it failed "Permission denied", although its file ran in one xdist group."""
import glob
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CC = re.compile(r'\[\s*"(cc|gcc|clang)",')                  # a compiler call: subprocess.run(["cc", ...
SAFE = ("once(", "getpid", "WORKER", "PYTEST_XDIST_WORKER", "tmp_path", "tmpdir", "mkdtemp", "flock",
        "-fsyntax-only")                                    # (a syntax-only compile writes no file)


def unsafe(src):
    """a file that runs a compiler, writes under build/ and has none of the safe forms"""
    return bool(CC.search(src)) and '"build"' in src and not any(s in src for s in SAFE)


def test_every_compiling_test_builds_atomically():
    bad = []
    for f in sorted(glob.glob(os.path.join(ROOT, "tests", "py", "*.py"))):
        if unsafe(open(f).read()):
            bad.append(os.path.basename(f))
    assert not bad, f"compiles into a fixed build/ path: use hostrun.once or a per-process name: {bad}"


def test_the_check_finds_the_old_eq_fixture():
    old = '''EXE = os.path.join(ROOT, "build", "host", "eq_test")
def exe():
    subprocess.run(["cc", "-O2", "tests/c/eq_test.c", "-o", EXE], cwd=ROOT, check=True)'''
    assert unsafe(old)
    assert not unsafe(old.replace('subprocess.run(["cc"', 'once(EXE, lambda exe: subprocess.run(["cc"'))
    assert not unsafe('x = os.path.join(ROOT, "build", "a.json")')        # no compiler: not this check's business
