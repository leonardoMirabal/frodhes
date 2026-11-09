# SPDX-License-Identifier: GPL-3.0-only
"""platform.c wd_poll: the watchdog is fed only while the whole app runs. Its code, copied out of platform.c, is run
against a model: the audio interrupt's count, the clock and the app's check (APP_WD_OK, FR_C1: core 1's jobs) each
stall in turn, and the feeding must stop within its limit (else a hang with the main loop alive is never reset: the
0.9.10 RC soak's 3.5 h freeze). platform_poll calls it instead of feeding, and the crash screen's wait is bounded."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = open(os.path.join(ROOT, "firmware", "src", "platform.c")).read()
FRO = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()

MODEL = r"""
#include <stdint.h>
#include <stdio.h>
#define FR_COLD
#define FM1_TICKS_PER_US 24u
static uint32_t ticks, feeds, c1_ok = 1;
static volatile uint32_t wd_isr_n;
static uint32_t fm1_ticks(void) { return ticks; }
static void fm1_wdt_feed(void) { feeds++; }
#define APP_WD_OK(now) c1_ok
%s
/* one main-loop pass every 1 ms; returns the passes until the feeding stopped (0: it never did in n passes) */
static uint32_t run(uint32_t n, int isr, int clock, int c1)
{
    uint32_t i, f;
    for (i = 1; i <= n; i++) {
        if (clock) ticks += 24000u;
        if (isr && i %% 6u == 0u) wd_isr_n++;          /* (a block every ~5.8 ms) */
        c1_ok = (uint32_t)c1;
        f = feeds;
        wd_poll();
        if (feeds == f) return i;
    }
    return 0;
}
int main(void)
{
    printf("%%u ", run(60000u, 1, 1, 1));   /* all alive, 60 s: fed throughout */
    printf("%%u ", run(10000u, 0, 1, 1));   /* the audio interrupt stopped */
    printf("%%u ", run(3000000u, 1, 0, 1)); /* the clock stopped */
    printf("%%u\n", run(10000u, 1, 1, 0));  /* the app's check failed */
    return 0;
}
"""


def test_wd_poll_stops_feeding_on_each_stall(tmp_path):
    m = re.search(r"^#define WD_STALL.*?^FR_COLD static void wd_poll\(void\)\n\{.*?^\}\n", SRC, re.S | re.M)
    assert m, "wd_poll not found in platform.c"
    code = re.sub(r"#ifndef APP_WD_OK\n#define APP_WD_OK\(now\) 1\n#endif\n", "", m.group(0))
    (tmp_path / "m.c").write_text(MODEL % code)
    subprocess.run(["cc", "-O1", "-w", "-o", str(tmp_path / "m"), str(tmp_path / "m.c")], check=True)
    alive, isr, clock, app = map(int, subprocess.run([str(tmp_path / "m")], capture_output=True, text=True,
                                                     check=True).stdout.split())
    assert alive == 0, "fed only part of the time with everything alive"
    assert 3000 <= isr <= 3010, isr            # the audio interrupt: stops after WD_STALL (3 s)
    assert 0 < clock <= 2000001, clock         # the clock: stops after WD_SPIN passes
    assert app == 1, app                       # the app's check: at once


def test_platform_poll_feeds_through_wd_poll():
    body = re.search(r"^FR_COLD static void platform_poll\(void\)\n\{(.*?)^\}\n", SRC, re.S | re.M).group(1)
    assert "wd_poll();" in body and "fm1_wdt_feed" not in body
    assert re.search(r"void fm1_alnk0_irq\(void\)[^\n]*\n\{\n    wd_isr_n = wd_isr_n \+ 1u;", SRC)
    fault = re.search(r"^FR_COLD static void fm1_fault\(.*?^\}\n", SRC, re.S | re.M).group(0)
    assert "i < 20000000u" in fault and re.search(r"fm1_reboot\(\);\n    for \(;;\)", fault)


def test_c1_jobs_gate():
    assert re.search(r"static int fr_wd_c1_ok\(uint32_t now\).*?c1\.st != C1_ON \|\| c1\.jobs != j", FRO, re.S)
    assert "#define APP_WD_OK(now) fr_wd_c1_ok(now)" in FRO
