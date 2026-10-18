# SPDX-License-Identifier: GPL-3.0-only
"""pytest options for tests/py: --long runs the fuzz tests with many more seeds (test_fuzz.py).

In parallel (pytest-xdist; make pytest runs -n auto --dist loadgroup): the host build is made once, before the
workers start; each worker renders into its own build/host/<worker>/ (hostrun.HOST, hostrun.OUT); the other
binaries a test builds are built once under a file lock (hostrun.once). The tests that share a fixed output path,
or an expensive module fixture, run in one group, on one worker, one after the other (GROUPS)."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GROUPS = {                         # node id prefix -> group
    "test_bars_compat.py": "bars_compat",                           # build/bars_compat/ (the old tree, its binaries)
    "test_vibes_knock_measure.py::test_the_suite_row_on_the_host": "hil_scan_host",   # build/hil/vibes-host.json
    "test_bars_calib.py": "bars_calib",                             # its va_checks fixture (100 s) computed once
    "test_voice_calib.py": "voice_calib",                           # its vel fixture (13 s) computed once
}
# (build/gen_bars/fe_cache.json is shared by test_gen_bars and test_bars_calib, the two longest, which run apart: a
# reader that finds it half written recomputes it, and both writers write the same bytes)


def pytest_addoption(parser):
    parser.addoption("--long", action="store_true", default=False,
                     help="the fuzz tests with 10x the seeds (and every seed under the sanitizers)")
    parser.addoption("--fuzz-seeds", type=int, default=0, help="the fuzz tests' seed count per engine (0: the default)")
    parser.addoption("--race-cases", type=int, default=0,
                     help="test_race_fuzz.py: sampled cases per engine (0: 64; with --long: every pair, all gaps)")


def pytest_configure(config):
    """the controller of a parallel run builds the host binary before the workers start (no race on compiling it),
    and gives each worker one OpenBLAS thread (Linux numpy): one worker per core, each with OpenBLAS's default of a
    thread per core spinning, took test_bar_fe's top octave from 13 s to over 50 min in the arm64 container
    (2026-10-19). macOS numpy uses Accelerate, which this leaves alone (the generators' byte-exact tables)"""
    if getattr(config.option, "numprocesses", None) and not hasattr(config, "workerinput"):
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")          # (the workers inherit the environment)
        subprocess.run(["make", "-s", "-C", ROOT, "build/host/frodhes"], check=True)


FIRST = ("test_gen_bars.py", "test_bars_calib.py")  # the longest (160 s each): handed out first, not last


@pytest.hookimpl(tryfirst=True)              # (before xdist reads the groups)
def pytest_collection_modifyitems(items):
    items.sort(key=lambda it: not it.nodeid.split("tests/py/")[-1].startswith(FIRST))   # (stable: else as collected)
    for it in items:
        nid = it.nodeid.split("tests/py/")[-1]
        for prefix, group in GROUPS.items():
            if nid.startswith(prefix):
                it.add_marker(pytest.mark.xdist_group(group))
