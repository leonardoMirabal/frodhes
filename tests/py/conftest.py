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
    "test_uac_ring.py": "uac_ring",                                 # build/uac_test/ (old.tar, old/, the binaries)
    # (the 0.9.8 PR's CI: a worker building a fixed path while another ran it, "Text file busy" / "Permission denied")
    "test_uac_seq.py": "uac_ring",                                  # build/uac_test/uac_seq_sie, planted/
    "test_uac_fir.py": "uac_ring",                                  # build/uac_test/uac_fir_test
    "test_uac_phase.py": "uac_ring",                                # build/uac_test/uac_seq_phase*
    "test_eq.py": "eq",                                             # build/host/eq_test (its module fixture)
    "test_rh_coef.py": "rh_coef",                                   # build/rh_coef/rh_coef_<tag>
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


# ---- memo: the pure-Python tests (no host binary) skipped while every input is unchanged since they passed here.
# The slowest of the suite are deterministic functions of code and data (test_gen_bars' regeneration alone is 800 s of
# a 34 min `make pytest -n 2`, 2026-11-08). Their inputs: the test file, every local module it imports (tools/,
# tests/py/, followed through their own imports), docs/model/, firmware/gen/, tests/py/data/, tests/baselines/,
# tools/*.json, and the Python / numpy / scipy versions. A pass writes build/memo/<test>.<hash>; the next run with the
# same hash skips it ("memo: ..."). Off in CI (CI or GITHUB_ACTIONS set: every run runs everything), with
# FRODHES_NO_MEMO=1, and for --long. A test listed here must read nothing else (no host build, no device, no clock).
MEMO_FILES = ("test_gen_bars.py", "test_hil_crackle.py", "test_marimba_calib.py", "test_hil_determinism.py",
              "test_bars_calib.py", "test_gen_rhodes_portable.py", "test_marimba_hil_measures.py")
MEMO_DATA = ("docs/model", "firmware/gen", "tests/py/data", "tests/baselines")
_memo_hash = {}


def _memo_on(config):
    return not (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or os.environ.get("FRODHES_NO_MEMO")
                or config.getoption("--long"))


def _local_imports(path, seen):
    import ast
    if path in seen or not os.path.exists(path):
        return
    seen.add(path)
    try:
        tree = ast.parse(open(path).read())
    except SyntaxError:
        return
    for n in ast.walk(tree):
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
        for name in names:
            top = name.split(".")[0]
            for d in ("tools", os.path.join("tests", "py")):
                _local_imports(os.path.join(ROOT, d, top + ".py"), seen)


def _memo_key(fspath):
    import hashlib
    import platform
    if fspath in _memo_hash:
        return _memo_hash[fspath]
    files = set()
    _local_imports(fspath, files)
    files.add(os.path.join(ROOT, "tests", "py", "conftest.py"))
    for d in MEMO_DATA:
        for dp, _, fs in os.walk(os.path.join(ROOT, d)):
            files.update(os.path.join(dp, f) for f in fs if "__pycache__" not in dp)
    files.update(os.path.join(ROOT, "tools", f) for f in os.listdir(os.path.join(ROOT, "tools")) if f.endswith(".json"))
    h = hashlib.sha256()
    try:
        import numpy
        import scipy
        h.update(f"{platform.python_version()} {numpy.__version__} {scipy.__version__}".encode())
    except ImportError:
        pass
    for f in sorted(files):
        h.update(os.path.relpath(f, ROOT).encode() + b"\0" + open(f, "rb").read() + b"\0")
    _memo_hash[fspath] = h.hexdigest()[:16]
    return _memo_hash[fspath]


def _memo_stamp(item):
    nid = item.nodeid.split("tests/py/")[-1]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in nid)
    return os.path.join(ROOT, "build", "memo", f"{safe}.{_memo_key(str(item.fspath))}")


def _memo_item(item):
    return os.path.basename(str(item.fspath)) in MEMO_FILES and _memo_on(item.config)


def pytest_runtest_setup(item):
    if _memo_item(item) and os.path.exists(_memo_stamp(item)):
        pytest.skip("memo: every input unchanged since this test passed here (build/memo; FRODHES_NO_MEMO=1 runs it)")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    if call.when == "call" and rep.passed and _memo_item(item):
        p = _memo_stamp(item)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(item.nodeid + "\n")
