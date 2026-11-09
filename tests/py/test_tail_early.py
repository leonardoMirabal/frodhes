# SPDX-License-Identifier: GPL-3.0-only
"""Two cores (FR_C1): the audio ISR's tail in pieces, the first ones run early by app_audio while core 0 waits for
core 1 (platform.c APP_TAIL_EARLY, tail_piece / tail_early). The host build replaces platform.c, so Gate R never runs
this tail: here platform.c's own code (copied out of the file) runs on random blocks, the one pass of the interrupt
against tail_piece a piece at a time, both MASTER modes (usb_pre_master 0 / 1), MASTER at 0, unity and full: the jack
samples and the USB tap's samples must be equal, bit for bit. The resampler's side (a piece of 32 at the history's
start, a tap of 32) is tests/uac_rs_test.c's UAC_RS_BLOCK=3 (test_uac_lock.py). Static checks: the app reads core 1's
progress twice, keeps the early pieces off blocks the silence gate can touch, and the interrupt allows them only with
MASTER still and room in the USB ring for the whole block."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = open(os.path.join(ROOT, "firmware", "src", "platform.c")).read()
FRO = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()


def grab(start, end):
    i = SRC.index(start)
    j = SRC.index(end, i) + len(end)
    return SRC[i:j]


MODEL = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define HALF_FRAMES 256u
#define HALF_WORDS (HALF_FRAMES * 2u)
#define APP_OUT_SHIFT 8u
#define HAL_KEEP_REG(v) ((void)(v))
#define TAIL_KEEP(q) ((void)0)
#define TAIL_Q_T int32_t
#define TAIL_Q(b) tap_q
static int32_t tap_q[64], usb[2u * HALF_WORDS];
static uint32_t usb_n;
static uint8_t usb_pre_master;
static void tap(const int32_t *q) { memcpy(usb + usb_n, q, 64u * sizeof q[0]); usb_n += 64u; }
#define TAIL_TAP(q) tap((q) - 64)
#define TAIL_PQ() tap_q
#define TAIL_PTAP(q) TAIL_TAP(q)
%(defs)s
%(piece)s
static void isr_pass(int32_t *o, int32_t m)     /* the interrupt's loop, as platform.c has it */
{
    uint32_t i, b = 0;
#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))
%(loop)s
#undef TAIL_PRE
}
int main(void)
{
    static int32_t a[HALF_WORDS], b[HALF_WORDS], ua[2u * HALF_WORDS];
    static const int32_t ms[] = {0, 1, 2048, 4095, 4096};
    uint32_t seed = 12345u, k, i, n, bad = 0;
    for (k = 0; k < 400u; k++) {
        int32_t m = ms[k %% 5u];
        usb_pre_master = (uint8_t)((k / 5u) & 1u);
        for (i = 0; i < HALF_WORDS; i++) {
            seed = seed * 1664525u + 1013904223u;
            a[i] = (int32_t)(seed >> 8) %% (k & 2u ? 70000 : 33000);   /* (beyond the clip too) */
        }
        memcpy(b, a, sizeof a);
        usb_n = 0; isr_pass(a, m); memcpy(ua, usb, sizeof ua); n = usb_n;
        usb_n = 0;
        for (i = 0; i < HALF_WORDS; i += 64u) tail_piece(b + i, m);
        if (n != usb_n || memcmp(a, b, sizeof a) || memcmp(ua, usb, n * sizeof usb[0])) bad++;
    }
    printf("%%u\n", bad);
    return 0;
}
"""


def test_pieces_equal_the_one_pass(tmp_path):
    defs = grab("#define MASTER_JACK_SH", "(x) < -(32767 << APP_OUT_SHIFT) ? -(32767 << APP_OUT_SHIFT) : (x))")
    piece = grab("static __attribute__((noinline)) void tail_piece(", "#undef TAIL_PRE\n}")
    piece = piece.replace("#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))\n", "", 1)
    piece = piece.replace("#undef TAIL_PRE\n}", "}")
    piece = "#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))\n" + piece + "\n#undef TAIL_PRE\n"
    loop = grab("        for (; b < HALF_WORDS; b += 64u) {", "            TAIL_TAP(q);\n        }")
    src = tmp_path / "tail.c"
    src.write_text(MODEL % dict(defs=defs, piece=piece, loop=loop))
    exe = tmp_path / "tail"
    subprocess.run(["cc", "-O1", "-w", "-o", str(exe), str(src)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["0"], out


def test_static_rules():
    # the interrupt: early pieces only with MASTER still and room for the whole block; the rest in pieces after
    assert "tl.on = tl.m == master_prev && TAIL_ROOM();" in SRC
    assert "HALF_FRAMES + HALF_FRAMES / 8u + 2u" in SRC          # uac_tap_lock's drop test for the whole block
    assert re.search(r"if \(tl\.b\) \{.*?for \(b = tl\.b; b < HALF_WORDS; b \+= 64u\)\s*tail_piece\(o \+ b, tl\.m\);",
                     SRC, re.S)
    # the app: two cores only, the gate's state read before core 1 starts, core 1's progress read twice
    assert re.search(r"#if FR_C1 && !defined\(DINGHY_HOST\).*?#define APP_TAIL_EARLY FR_TAIL_EARLY.*?#include \"platform.c\"",
                     FRO, re.S)
    assert "gate_g == 32768 && gate_tgt == 32768 && gate_quiet + 1u < GATE_HOLD && !fx_p[FXP_RAW]" in FRO
    assert re.search(r"x = c1_fxd;\s*fr_c1_sync\(\);\s*if \(x == c1_fxd && x > done && x <= n && !\(x & 31u\)\)", FRO)
    # no flash, screen or poll calls in the early work (it runs inside the audio interrupt)
    body = re.search(r"static void tail_early\(uint32_t frames\).*?\n\}", SRC, re.S).group(0)
    body += re.search(r"static __attribute__\(\(noinline\)\) void tail_piece\(.*?\n\}", SRC, re.S).group(0)
    for bad in ("fl_", "lcd_", "draw_", "usb_poll", "platform_poll", "fm1_wdt_feed", "midi_send"):
        assert bad not in body, bad
