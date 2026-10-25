# SPDX-License-Identifier: GPL-3.0-only
"""Key velocity end to end on the host: the real converter scheduler (hal/fm1_adc.h), key rows (hal/fm1_keyadc.h),
scan tick (hal/fm1_input.h) and velocity (src/keyvel.c) built together as the FM-1 builds them (keyvel.h's FM-1
side, not DINGHY_HOST), on a model of the hardware (tests/c/fm1_hw_model.h: V2's ports, 595 chain and key matrix;
a SARADC in tests/c/keyvel_e2e_test.c). The HAL headers and keyvel.h are copied with every register access a call of
fake_reg(), the FM-1's sections dropped and the main loop's cli / sti empty (test_keyadc.fake_hal and below).
It checks: soft / hard readings give soft / hard note-on velocities (the curve's own values), every analog key's
place in the matrix, the top 7 at CONTEXT, TOUCH, held chords debounce, MASTER's and the battery's staleness bounds
idle and while mashing, OCT- / OCT+ never read pressed in any mode, BRIGHTNESS 1 never swallowed, the fallback after
6 ms, a lost conversion picked again, a key released before its velocity, SELFTEST's dropped samples, STRESS /
STORM, the developer CCs (41 forwarded, 42 answered once per item by its owner) and a slow main loop."""
import os
import re
import subprocess

from hostrun import ROOT, out
from test_keyadc import fake_hal


def build(d):
    hal = os.path.join(d, "hal")
    fake_hal(hal)
    p = os.path.join(hal, "fm1_input.h")
    s = open(p).read()
    assert s.count('__asm__ volatile("cli" ::: "memory");') == 1 and s.count('"csync\\n\\tsti"') == 1
    s = s.replace('__asm__ volatile("cli" ::: "memory");', '__asm__ volatile("" ::: "memory");')
    s = s.replace('__asm__ volatile("csync\\n\\tsti" ::: "memory");', '__asm__ volatile("" ::: "memory");')
    open(p, "w").write(s)
    kh = open(os.path.join(ROOT, "firmware", "src", "keyvel.h")).read()
    old = '#define KV_BSS(name) __attribute__((section(".bss." #name)))'
    assert kh.count(old) == 1
    open(os.path.join(hal, "keyvel.h"), "w").write(kh.replace(old, "#define KV_BSS(name) __attribute__((used))"))
    exe = os.path.join(d, "keyvel_e2e")
    subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-I" + hal,
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-o", exe,
                    os.path.join(ROOT, "tests", "c", "keyvel_e2e_test.c")], check=True, cwd=ROOT)
    return exe


def test_keyvel_end_to_end():
    exe = build(out("keyvel_e2e"))
    r = subprocess.run([exe], capture_output=True, text=True, timeout=600)
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    assert r.returncode == 0 and not fails, r.stdout
    assert r.stdout.count("ok   ") >= 60, r.stdout
    stats = {m.group(1): int(m.group(2)) for m in re.finditer(r"^stat (\S+) (\d+)$", r.stdout, re.M)}
    assert stats["latency_ms"] <= 4
    assert stats["master_stale_ticks_mash"] <= 28 and stats["batt_stale_ticks_mash"] <= 2835
