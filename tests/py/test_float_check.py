# SPDX-License-Identifier: GPL-3.0-only
"""tools/build.py float_check(): the FM-1 / SMK-37 app, preprocessed with the build's defines, has no float or
double, no HQ / Move file and none of the host / Move / HQ switches. Today's tree passes (host cc as the
preprocessor; the build itself uses the JieLi clang's); an injected float, an included hq/ file and a stray FR_HQ
each fail it, while a float in a comment, in a string or in a system header does not."""
import os
import shutil
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which(os.environ.get("CC", "cc")), reason="no C compiler")


def test_todays_tree_passes():
    for board in ("fm1", "smk37"):
        assert build.float_check(board=board) == [], board


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    d = tmp_path_factory.mktemp("fc")
    for sub in ("src", "gen", "hal"):
        shutil.copytree(os.path.join(ROOT, "firmware", sub), d / "firmware" / sub)
    shutil.copytree(os.path.join(ROOT, "tests"), d / "tests", ignore=shutil.ignore_patterns("py", "baselines"))
    shutil.copytree(os.path.join(ROOT, "web"), d / "web", ignore=shutil.ignore_patterns("dist"))
    return d


def test_a_copy_passes_and_ignores_comments_and_strings(tree):
    f = tree / "firmware" / "src" / "notes.c"
    orig = f.read_text()
    try:
        f.write_text(orig + '\n/* a float here */\nstatic const char *const fc_s = "double";\n#include <math.h>\n')
        assert build.float_check(board="fm1", root=str(tree)) == []
    finally:
        f.write_text(orig)


@pytest.mark.parametrize("inject,extra,want", [
    ("\nstatic float fc_x;\n", (), "'float' in the fm1 build, from firmware/src/notes.c"),
    ("\nstatic int fc_y(int a) { return (int)(a * 0.5); }\nstatic double fc_z;\n", (), "'double'"),
    ('\n#include "hq/fc_hq.c"\n', (), "firmware/src/hq/fc_hq.c is in the fm1 build"),
    ("", ("-DFR_HQ=1",), "FR_HQ is defined"),
    ("\n#define DINGHY_MOVE 1\n", (), "DINGHY_MOVE is defined"),
])
def test_an_injection_fails(tree, inject, extra, want):
    f = tree / "firmware" / "src" / "notes.c"
    hq = tree / "firmware" / "src" / "hq"
    hq.mkdir(exist_ok=True)
    (hq / "fc_hq.c").write_text("static int fc_hq;\n")
    orig = f.read_text()
    try:
        f.write_text(orig + inject)
        errs = build.float_check(board="fm1", root=str(tree), extra=extra)
        assert any(want in e for e in errs), errs
    finally:
        f.write_text(orig)
