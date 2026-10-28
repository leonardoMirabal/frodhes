# SPDX-License-Identifier: GPL-3.0-only
"""tools/test_map.py's per-target part (tools/target_closure.py): fm1 / smk / move / hq, Gate S, shared code."""
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_map as tm  # noqa: E402

FAKE = {"fm1": {"firmware/src/fx.c", "firmware/src/usb.c"}, "smk": {"firmware/src/fx.c", "firmware/src/usb.c"},
        "move": {"firmware/src/fx.c", "move/move_bridge.h", "firmware/src/hq/x.c"}, "hq": {"firmware/src/hq/x.c"}}


def test_move_only_needs_gate_s():
    pt = tm.per_target(["move/move_bridge.h"], FAKE)
    assert pt["gate_s"] and not pt["shared"]
    assert pt["fm1"]["files"] == [] and pt["smk"]["files"] == []
    assert pt["move"]["files"] == ["move/move_bridge.h"] and "tools/ci_move.sh" in pt["move"]["host"][0]
    lines = tm.target_lines(pt)
    assert sum(tm.GATE_S in l for l in lines) == 2


def test_hq_file():
    pt = tm.per_target(["firmware/src/hq/x.c"], FAKE)
    assert pt["gate_s"] and pt["hq"]["files"] == ["firmware/src/hq/x.c"] and "HQ" in pt["hq"]["device"]


def test_shared_dsp_gets_both():
    pt = tm.per_target(["firmware/src/fx.c"], FAKE)
    assert not pt["gate_s"] and pt["shared"] == ["firmware/src/fx.c"]
    assert pt["fm1"]["host"] == ["tiers T0 + T1 + T2"] and "effects" in pt["fm1"]["device"]
    assert pt["smk"]["files"] and pt["move"]["files"] and not pt["hq"]["files"]


def test_firmware_only_no_gate_s():
    pt = tm.per_target(["firmware/src/usb.c"], FAKE)
    assert not pt["gate_s"] and not pt["move"]["files"] and pt["move"]["device"] == "none"


def test_docs_only_touches_nothing():
    pt = tm.per_target(["docs/MOVE.md"], FAKE)
    assert not pt["gate_s"] and all(not pt[t]["files"] for t in tm.PER_TARGET)


def test_cli_real_closures():
    py = sys.executable
    p = subprocess.run([py, "tools/test_map.py", "--json", "firmware/src/fx.c"], cwd=ROOT, capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stderr
    t = json.loads(p.stdout)["targets"]
    assert t["fm1"]["files"] == t["smk"]["files"] == t["move"]["files"] == ["firmware/src/fx.c"]
    assert not t["gate_s"]
    p = subprocess.run([py, "tools/test_map.py", "move/module/move_ui.h"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "per target:" in p.stdout and "Gate S" in p.stdout
    p = subprocess.run([py, "tools/test_map.py", "--no-targets", "docs/MOVE.md"], cwd=ROOT, capture_output=True,
                       text=True)
    assert p.returncode == 0 and "per target:" not in p.stdout
