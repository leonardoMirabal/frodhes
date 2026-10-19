# SPDX-License-Identifier: GPL-3.0-only
"""hal/fm1_input.h's LED table (DISPLAY BRIGHTNESS): fm1_led_build works the 16 scan frames out in the main loop and
fm1_input_tick only reads them. The host build has no HAL (tests/host.c stands in), so this compiles the HAL's own
functions on the computer, drives them with random LED and BRIGHTNESS changes, and checks every table entry after
every build against the rule applied per tick: a duty of 1 .. 16 lights a group's LEDs on that many of the 16 frames,
spread evenly, 0 on none. And at full brightness (16 / 16) the table is what the tick read before DISPLAY: fm1_led."""
import os
import re
import subprocess

from hostrun import ROOT, out

HAL = os.path.join(ROOT, "firmware", "hal", "fm1_input.h")

CHECK = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define FM1_NCOL 11u
%(keymap)s
static uint8_t fm1_led[FM1_NCOL], fm1_led_dim[FM1_NCOL], fm1_led_grpmask[FM1_NCOL], fm1_led_duty[2] = {16u, 16u};
static uint8_t fm1_led_tab[FM1_NCOL][16];
%(hal)s
static uint32_t ref_on(uint32_t d, uint32_t f) { return d >= 16u || ((f * d) & 15u) < d; }
int main(void)
{
    static const uint8_t KEY[10] = {%(key)s}, BTN[10] = {%(btn)s};
    unsigned t, j, p, f, bad = 0, full_bad = 0;
    srand(1);
    fm1_led_group_ids(14, 40);                         /* the note keys (frodhes.c: KEY_LED(0) .. KEY_LED(26)) */
    for (t = 0; t < 50000u; t++) {
        unsigned m = (unsigned)rand() %% 8u, s;
        for (j = 0; j < m; j++) fm1_led_key((uint32_t)(rand() %% 41), rand() & 1);
        if (rand() %% 40 == 0) { s = (unsigned)rand() %% 10u; fm1_led_duty[0] = KEY[s]; fm1_led_duty[1] = BTN[s]; }
        fm1_led_build();
        for (p = 0; p < FM1_NCOL; p++)
            for (f = 0; f < 16u; f++) {
                uint32_t k = fm1_led_grpmask[p], d0 = fm1_led_duty[0], d1 = fm1_led_duty[1], l = 0;
                if (d0 && ref_on(d0, f)) l |= fm1_led[p] & k;
                if (d1 && ref_on(d1, f)) l |= fm1_led[p] & ~k;
                if (fm1_led_tab[p][f] != l) bad++;
                if (d0 == 16u && d1 == 16u && fm1_led_tab[p][f] != fm1_led[p]) full_bad++;
            }
    }
    printf("%%u %%u\n", bad, full_bad);
    return 0;
}
"""


def _between(s, start, end):
    i = s.index(start)
    return s[i:s.index(end, i)]


def test_led_tables_match_the_per_tick_rule():
    s = open(HAL).read()
    keymap = re.search(r"static const int8_t FM1_KEYMAP\[[^;]*;", s, re.S).group(0)
    hal = (_between(s, "static void fm1_led_group_ids", "static void fm1_led_key(uint32_t id, int on)")
           + _between(s, "static void fm1_led_key(uint32_t id, int on)", "\n}\n") + "\n}\n")
    hal = re.sub(r'__attribute__\(\(section\("[^"]*"\)\)\)', "", hal)   # (the firmware's own sections)
    ui = open(os.path.join(ROOT, "firmware", "src", "ui.c")).read()
    key = re.search(r"dim_key\[DIM_N\] = \{([^}]*)\}", ui).group(1)
    btn = re.search(r"dim_btn\[DIM_N\] = \{([^}]*)\}", ui).group(1)
    src, exe = out("led_tables") + ".c", out("led_tables")
    with open(src, "w") as fh:
        fh.write(CHECK % {"keymap": keymap, "hal": hal, "key": key, "btn": btn})
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-function", "-o", exe, src], check=True)
    bad, full_bad = map(int, subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split())
    assert (bad, full_bad) == (0, 0)
