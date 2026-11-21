# SPDX-License-Identifier: GPL-3.0-only
"""tools/own_check.py, the closure guard of the plugin's own-code build: it passes on the tree; it fails when a file
that the plugin must not compile (firmware/src/gfx.c) is put back into the closure, and when a fingerprinted line is
pasted into a file of the FR_OWN path. The two failures run in a temporary copy of the sources, never in the tree."""
import io
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import own_check as oc  # noqa: E402
import target_closure as tcl  # noqa: E402

COPY = ("firmware", "plugin", "tools", "assets/fonts", "move/move_presets.h")


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """a copy of what the check reads (the sources, the tools, the allow-lists), made once"""
    dst = tmp_path_factory.mktemp("own_check")
    for p in COPY:
        s, d = os.path.join(ROOT, p), os.path.join(str(dst), p)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(s, d)
    return str(dst)


def check_in(root):
    r = subprocess.run([sys.executable, os.path.join(root, "tools", "own_check.py"), "-v"], capture_output=True,
                       text=True)
    return r.returncode, r.stdout + r.stderr


class Edit:
    """change a file of the copy for the length of a with block"""
    def __init__(self, root, rel, fn):
        self.path, self.fn = os.path.join(root, rel), fn

    def __enter__(self):
        self.old = open(self.path).read()
        open(self.path, "w").write(self.fn(self.old))

    def __exit__(self, *a):
        open(self.path, "w").write(self.old)


def test_passes_on_the_tree():
    out = io.StringIO()
    assert oc.run(out=out) == 0, out.getvalue()
    text = out.getvalue()
    assert "(a) allow-list: ok" in text and "(b) copyright lines: ok" in text
    assert re.search(r"\(c\) .*: ok; 0 pending \(none\)", text), text
    assert "(b) copyright lines: ok, 0 pending" in text, text


def test_the_closure_is_the_plugins():
    c = tcl.plugin_closure()
    for f in ("plugin/src/engine_unit.c", "plugin/src/bridge.c", "plugin/src/frodhes_clap.c", "plugin/gui_core.c",
              "plugin/ui/frui.c", "plugin/src/coro/coro_arm64.S", "plugin/gen/params_map.h", "firmware/src/fx.c",
              "tools/gen_plugin_params.py", "tools/gen_bend_own.py"):
        assert f in c, f
    # the screen stack, the browser layer and the FM-1 parts are not compiled by the plugin
    for f in ("firmware/src/gfx.c", "firmware/src/aatext.c", "firmware/src/keyvel.c", "firmware/src/store.h",
              "firmware/src/platform.c", "web/web_hal.c"):
        assert f not in c, f
    assert "plugin" in tcl.targets_of("plugin/src/bridge.c", tcl.closures())


def test_fails_when_the_classic_drive_is_put_back(tree):
    """the FDRV kernel (fx_fel.c part 4) compiled in the own-code build: (a) and (c) both fail"""
    old = "#if !FR_OWN                                     /* the classic drive (FDRV): fx_fel.c part 4;"
    with Edit(tree, "firmware/src/fx.c", lambda t: t.replace(old, "#if 1" + old[len("#if !FR_OWN"):], 1)):
        rc, out = check_in(tree)
    assert rc == 1, out
    assert "fx_fel.c" in out and "fdrv" in out, out
    rc, out = check_in(tree)
    assert rc == 0, out


def test_fails_when_gfx_c_is_put_back_in_the_closure(tree):
    with Edit(tree, "plugin/src/engine_unit.c", lambda t: t + '\n#include "../../firmware/src/gfx.c"\n'):
        rc, out = check_in(tree)
    assert rc == 1, out
    assert "not in plugin/own_allow.txt: firmware/src/gfx.c" in out
    assert "felucca-gfx" in out                          # (c) sees its lines too
    rc, out = check_in(tree)                             # and the copy is whole again
    assert rc == 0, out


def fingerprinted_line():
    """a line of firmware/src/keyvel.c (a third-party block) that is a fingerprint and has no macro in it"""
    fps = oc.load_fingerprints()
    for l in oc.strip_comments(open(os.path.join(ROOT, "firmware/src/keyvel.c")).read()).splitlines():
        n = oc.norm(l)
        if (n and not n.startswith("#") and oc.significant(n) and oc.fp(n) in fps and not re.search(r"\b[A-Z][A-Z_0-9]{2,}", l)
                and fps[oc.fp(n)] == "keyvel"):
            return l.strip()
    raise AssertionError("no usable keyvel.c fingerprint line")


def test_fails_when_a_fingerprinted_line_is_pasted_into_an_own_path(tree):
    line = fingerprinted_line()
    with Edit(tree, "firmware/src/fx_own.h", lambda t: t.replace("#endif", line + "\n#endif", 1) if "#endif" in t
              else t + line + "\n"):
        rc, out = check_in(tree)
    assert rc == 1, out
    assert "keyvel: firmware/src/fx_own.h:" in out, out
