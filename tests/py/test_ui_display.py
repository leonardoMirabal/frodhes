# SPDX-License-Identifier: GPL-3.0-only
"""DISPLAY (0.9.4): COLOR, the screen's palette (ui.c COLOR_ACC: AMBER, then 25 pastel hues); BRIGHTNESS, 10 .. 1, the screen
and every light dimmed together (ui.c dim_scr for the screen, dim_key / dim_btn for the LEDs' duty); LEDS. Shots of the DISPLAY page:
the AMBER accent and its fill everywhere at first and nowhere after one turn of K1, PEACH's accent and fill (cd_shade
45) then; at BRIGHTNESS 8 the accent, the fill and the values' near-white are exactly the 73 % shades. The three are saved with
the settings and come back after a power cycle (FRODHES_FLASH): the cards read the same, the HAL's duties are set
again and every key is lit (LEDS ALL) before anything is touched."""
import numpy as np

import test_ui_meters as M
from hostrun import run
from test_persistence import boot, flash_file

DISPLAY = ["100 btn 7", "200 btn 7", "300 btn 7", "400 btn 7"]   # GLO four times (ends the splash too)
AMBER, PEACH, VAL = 0xE3C1, 0xFD0E, 0xEF7C                     # ui.c COLOR_ACC[0], [1]; MT_LOOKS[0].val RGB(236, 236, 230)
PG = 21                                                        # ui.c PG_DISPLAY (host: card.<page>.<k>)
DIM_PCT = [100, 86, 73, 63, 54, 46, 40, 34, 29, 25]   # ui.c dim_scr
DUTY = ['16 16', '12 14', '9 12', '7 10', '6 8', '5 6', '4 5', '3 4', '2 3', '1 2']   # ui.c dim_key, dim_btn per step down


def rgb(c):
    """an RGB565 word as the PPM expands it (tests/host.c write_ppm)"""
    return ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


def shade(c, pct):
    """ui.c cd_shade"""
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r * pct // 100) << 11 | (g * pct // 100) << 5 | (b * pct // 100)


def count(im, c):
    return int(np.sum((im == np.array(rgb(c))).all(axis=-1)))


def amber(im):
    """pixels of the AMBER palette's hue at any step, the title's anti-aliased edges included: an orange with
    green about half the red and almost no blue (the pastels' dark steps keep their own blue share)"""
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    return int(np.sum((r >= 40) & (b <= r * 0.12) & (g >= r * 0.40) & (g <= r * 0.62)))


def test_color_and_bright_redraw_the_page():
    r = run("display", DISPLAY + ["500 shot", "500 dump", "550 turn 2 1", "700 shot", "700 dump",
                                  "750 turn 3 -1", "800 turn 3 -1", "950 shot", "950 dump"], 1100)
    s = r.state(500)
    assert (s["page"], s["label0"], s["card0"], s["card1"], s["card2"]) == ("DISPLAY", "COLOR", "AMBER", "10", "ROOTS")
    before, peach, dim = M.ppm("display", 500), M.ppm("display", 700), M.ppm("display", 950)
    assert count(before, AMBER) > 300 and count(before, shade(AMBER, 45)) > 300 and count(before, PEACH) == 0
    assert count(before, VAL) > 300                                       # the values, full
    assert r.state(700)["card0"] == "PEACH"
    assert count(peach, PEACH) > 300 and count(peach, shade(PEACH, 45)) > 300, (count(peach, PEACH), count(peach, shade(PEACH, 45)))
    assert count(peach, AMBER) == 0 and amber(peach) == 0, (count(peach, AMBER), amber(peach))
    assert r.state(950)["card1"] == "8" and r.state(950)["ledduty"] == DUTY[2]
    a3 = shade(PEACH, DIM_PCT[2])
    assert a3 != PEACH
    assert count(dim, a3) > 300 and count(dim, shade(a3, 45)) > 300 and count(dim, PEACH) == 0, (count(dim, a3), count(dim, PEACH))
    assert count(dim, shade(VAL, DIM_PCT[2])) > 300 and count(dim, VAL) == 0, (count(dim, shade(VAL, DIM_PCT[2])), count(dim, VAL))


def test_saved_and_back_after_a_power_cycle():
    fl = flash_file("display-pc")
    r1 = run("display-pc1", DISPLAY + ["500 turn 2 1", "600 turn 3 -1", "650 turn 3 -1", "800 turn 4 1", "4500 dump"], 4700, flash=fl)
    s1 = r1.state(4500)
    assert (s1["card0"], s1["card1"], s1["card2"], s1["ledduty"]) == ("PEACH", "8", "ALL", DUTY[2])
    assert r1.saves, "the settings were not saved"
    r2 = boot("display-pc2", fl, ["1800 shot"], 2000)            # (the splash: 1600 ms)
    s2 = r2.state(100)
    assert (s2[f"card.{PG}.0"], s2[f"card.{PG}.1"], s2[f"card.{PG}.2"]) == ("PEACH", "8", "ALL")
    assert s2["ledduty"] == DUTY[2] and s2["led.14"] == "1" and s2["led.40"] == "1"
    im = M.ppm("display-pc2", 1800)
    a3 = shade(PEACH, DIM_PCT[2])
    n = count(im, a3) + count(im, shade(a3, 45))                 # (the first page: fewer accent pixels than DISPLAY)
    assert n > 300 and amber(im) == 0 and count(im, VAL) == 0, (n, amber(im), count(im, VAL))
