# SPDX-License-Identifier: GPL-3.0-only
"""tools/target_closure.py: the targets each file is compiled into, from the preprocessor. The coexistence rules of
the Move port (private plan 05, section 3): nothing of firmware/src/hq/ or move/ in the FM-1 or SMK-37 build; no
hal/ header with register access in the Move's (the two constant headers the store's layout uses are allowed);
the shared engine in every target; the FM-1-only files (USB, the screen driver, the loader) in no host build."""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import target_closure as tcl  # noqa: E402

C = tcl.closures()
HAL_CONSTANTS = {"firmware/hal/fm1_cc.h", "firmware/hal/fm1_flash_map.h"}   # (no register: store.h's layout)


def test_no_hq_or_move_code_in_the_firmware():
    for t in ("fm1", "smk"):
        bad = sorted(f for f in C[t] if f.startswith(("firmware/src/hq/", "firmware/gen/hq/", "move/")))
        assert not bad, (t, bad)


def test_no_register_access_in_the_move_build():
    hal = sorted(f for f in C["move"] | C["web"] | C["host"] if f.startswith("firmware/hal/"))
    assert set(hal) <= HAL_CONSTANTS, hal
    for f in HAL_CONSTANTS:
        assert "volatile" not in open(os.path.join(ROOT, f)).read(), f


def test_shared_and_target_only_files():
    def ts(f):
        return set(tcl.targets_of(f, C))
    for f in ("firmware/src/rhodes.c", "firmware/src/bars.c", "firmware/src/fx.c", "firmware/src/notes.c",
              "firmware/src/platform.c", "firmware/gen/rhodes_tables.h"):
        assert {"fm1", "smk", "host", "web", "move"} <= ts(f), f
    for f in ("firmware/src/usb.c", "firmware/src/lcd.c", "firmware/src/midi_uart.c", "firmware/src/ota.c",
              "firmware/src/recovery.c", "firmware/src/libc.c", "firmware/loader/loader.c", "tools/build.py"):
        assert ts(f) <= {"fm1", "smk"} and ts(f), f
    assert ts("web/web_hal.c") == {"web", "move"}
    assert ts("tests/host.c") == {"host"}
    assert ts("firmware/src/frodhes_smk.h") == {"smk"} and ts("firmware/hal/smk_input.h") == {"smk"}
    assert ts("firmware/hal/fm1_input.h") == {"fm1"}
    assert "docs" in ts("docs/MANUAL.md") and "docs" in ts("firmware/src/params.c")
    assert ts("move/module/move_ui.h") == {"move"}
    assert ts("tools/monkey.py") == set()


def test_cli():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "target_closure.py"), "--json",
                        "firmware/src/usb.c", "web/web_hal.c"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"firmware/src/usb.c": ["fm1", "smk"], "web/web_hal.c": ["web", "move"]}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "target_closure.py"), "--table"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and re.search(r"^fm1\s+\d+ files", r.stdout, re.M), r.stdout[:500]
