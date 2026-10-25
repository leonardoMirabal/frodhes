# SPDX-License-Identifier: GPL-3.0-only
"""hal/fm1_input.h's scan tick on the model of the FM-1's ports, 74HC595 chain and key matrix (tests/c/tick_595_test.c,
the HAL copied as tests/py/test_keyadc.py does: every register access a call of the model): the one-bit shift leaves
the chain exactly as the whole 16-bit word did (every latch column n's word, the LED lines dark at the latch, the lit
table after each tick, every key read right, the glow pulses their full time), every tick's dark part holds one
shift-clock edge (every column as bright), and it costs one edge on 10 ticks of 11."""
import os
import re
import subprocess

from hostrun import ROOT, out
from test_keyadc import fake_hal


def build_run(*args):
    d = out("tick595")
    hal = os.path.join(d, "hal")
    fake_hal(hal)
    exe = os.path.join(d, "tick_595")
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-function", "-I" + hal, "-I" + os.path.join(ROOT, "tests", "c"),
                    "-o", exe, os.path.join(ROOT, "tests", "c", "tick_595_test.c")], check=True, cwd=ROOT)
    r = subprocess.run([exe, *args], capture_output=True, text=True)
    stats = {m.group(1): int(m.group(2)) for m in re.finditer(r"^stat (\S+) (\d+)$", r.stdout, re.M)}
    return r, stats


def test_tick_595_model():
    r, st = build_run()
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    assert r.returncode == 0 and not fails, r.stdout
    for what in ("idle", "lit", "glow", "keys"):
        assert st[what + "_whole"] * 11 <= st[what + "_ticks"], r.stdout          # 16 edges once a frame
        assert st[what + "_one_bit"] + st[what + "_whole"] + 1 == st[what + "_ticks"], r.stdout   # (+ init's)
    assert st["idle_acc_mean"] <= 40, r.stdout        # register accesses per idle tick (the 16-bit shift: ~120)
