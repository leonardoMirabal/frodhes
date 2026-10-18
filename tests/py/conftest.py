# SPDX-License-Identifier: GPL-3.0-only
"""pytest options for tests/py: --long runs the fuzz tests with many more seeds (test_fuzz.py)."""


def pytest_addoption(parser):
    parser.addoption("--long", action="store_true", default=False,
                     help="the fuzz tests with 10x the seeds (and every seed under the sanitizers)")
    parser.addoption("--fuzz-seeds", type=int, default=0, help="the fuzz tests' seed count per engine (0: the default)")
    parser.addoption("--race-cases", type=int, default=0,
                     help="test_race_fuzz.py: sampled cases per engine (0: 64; with --long: every pair, all gaps)")
