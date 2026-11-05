# SPDX-License-Identifier: GPL-3.0-only
"""tools/isr_diff.py: a jump table's label inside a function (.GJTIL / .GJTIS, the cases after it) belongs to that
function, so its calls stay in the ISR call tree (2026-11-01: drv_run's body was cut at its first jump table, and the
drive TYPEs' kh_load / bh_row / ts_row, called from the audio ISR, were missing from the tree)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import isr_diff  # noqa: E402

DIS = """\
fm1_alnk0_irq:
 2000000:    bf ea 00 00       \tcall -4 <drv_run : 2000100 >

drv_run:
 2000100:    7f 04             \t[--sp] = {rets, r15-r4}
.GJTIL136_0_0_:
 2000104:    bf ea 00 00       \tcall -4 <kh_load : 2000200 >
.GJTIS141_0_0_:
 2000108:    bf ea 00 00       \tcall -4 <bh_row : 2000300 >

kh_load:
 2000200:    7f 04             \t[--sp] = {rets, r15-r4}

bh_row:
 2000300:    7f 04             \t[--sp] = {rets, r15-r4}

ui_draw:
 2000400:    7f 04             \t[--sp] = {rets, r15-r4}
"""


def test_jump_table_labels_stay_in_their_function(tmp_path):
    p = tmp_path / "a.dis"
    p.write_text(DIS)
    f = isr_diff.parse(p)
    assert not any(k.startswith(".GJTI") for k in f), sorted(f)
    t = isr_diff.tree(f, isr_diff.ROOTS)
    assert {"drv_run", "kh_load", "bh_row"} <= t and "ui_draw" not in t, sorted(t)
