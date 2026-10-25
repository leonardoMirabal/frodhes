# SPDX-License-Identifier: GPL-3.0-only
"""platform.c's audio ISR tail (MASTER, the clip, the USB tap, the codec's shift: one pass per 32 frames) on the host,
bit for bit against the three passes it replaced (tests/c/isr_tail_test.c): the output and the USB tap's samples for
MASTER 0 .. 4088, USB VOL on / off, silence, the clip and far over it."""
import os
import re
import subprocess

from hostrun import ROOT, out


def test_isr_tail_bit_exact():
    s = open(os.path.join(ROOT, "firmware", "src", "platform.c")).read()
    a, b = s.index("static int32_t abuf["), s.index("extern void isr_alnk0(void);")
    tail = re.sub(r"__attribute__\(\(section\(\"[^\"]*\"\)\)\)", "", s[a:b])   # (Mach-O has no ELF sections)
    d = out("isr_tail")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "tail.inc"), "w").write(tail)
    exe = os.path.join(d, "isr_tail")
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-function", "-I" + d, "-o", exe,
                    os.path.join(ROOT, "tests", "c", "isr_tail_test.c")], check=True, cwd=ROOT)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("ok"), r.stdout + r.stderr
