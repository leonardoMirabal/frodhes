# SPDX-License-Identifier: GPL-3.0-only
"""The delay's TAPE type (EFFECT = TAPE, value 3 after OFF / DIG / ANA): its plumbing. CC 88 picks the four types in
the card's order (0-31 OFF, 32-63 DIG, 64-95 ANA, 96-127 TAPE: the generic enum bands of midi_cc); the DELAY page's
K1 card has four options and stops at its ends; SEL brings the delay back as the type it last used (set.last_on[2],
TAPE too, kept across a power cycle); the settings record and user presets store the type (a byte, append-only ids),
so TAPE survives a power cycle and older records load with their DIG / ANA unchanged; a value no version wrote falls
back to OFF (the default)."""
import struct

import pytest

import test_persistence as tp
from hostrun import cc, run

DL_SW = 13                                              # params.c P_DL_SW (ids are append-only)
O_LAST_ON_DL = 22                                       # settings_t last_on[3] at 20: TREM/WAH, CHORUS, DELAY
NAMES = ["OFF", "DIG", "ANA", "TAPE"]
GO_DELAY = ["50 btn 3"]                                 # SEL: the DELAY page (from PERFORM)


def test_params_table():
    """params.c: P_DL_SW has 4 options, TAPE the fourth, CC 88, default OFF"""
    src = open(tp.os.path.join(tp.os.path.dirname(__file__), "..", "..", "firmware", "src", "params.c")).read()
    assert 'static const char *const N_DL[] = {"OFF", "DIG", "ANA", "TAPE"};' in src
    assert '[P_DL_SW] = {"DLY", 3, 0, F_ENUM, 88, N_DL},' in src
    assert tp.defaults()[DL_SW] == 0


@pytest.mark.parametrize("v,want", [(0, 0), (31, 0), (32, 1), (48, 1), (63, 1), (64, 2), (80, 2), (95, 2), (96, 3),
                                    (112, 3), (127, 3)])
def test_cc88_bands(v, want):
    """CC 88 at every band edge: the parameter and the DELAY page's card"""
    r = run(f"dltape-cc{v}", [cc(100, 88, v), "300 dump"], 400)
    s = r.state(300)
    assert s[f"p.{DL_SW}"] == str(want), (v, s[f"p.{DL_SW}"])
    assert s["card.10.0"] == NAMES[want], (v, s["card.10.0"])


def test_k1_card_four_options():
    """the DELAY page's K1: OFF -> DIG -> ANA -> TAPE, stops at TAPE, back down to OFF and stops there"""
    lines = GO_DELAY + ["200 dump"]
    t = 300
    for k in range(4):
        lines += [f"{t} turn 2 1", f"{t + 50} dump"]
        t += 100
    for k in range(4):
        lines += [f"{t} turn 2 -1", f"{t + 50} dump"]
        t += 100
    r = run("dltape-k1", lines, t + 100)
    seen = [r.state(200)["card0"]] + [r.state(300 + 100 * i + 50)["card0"] for i in range(8)]
    assert seen == ["OFF", "DIG", "ANA", "TAPE", "TAPE", "ANA", "DIG", "OFF", "OFF"], seen
    assert r.state(550)[f"p.{DL_SW}"] == "3"


def test_sel_remembers_tape():
    """SEL on the DELAY page: TAPE off, then back on as TAPE (not DIG)"""
    r = run("dltape-sel", GO_DELAY + [cc(100, 88, 127), "200 btn 3", "250 dump", "300 btn 3", "350 dump"], 450)
    assert r.state(250)[f"p.{DL_SW}"] == "0" and r.state(350)[f"p.{DL_SW}"] == "3", (
        r.state(250)[f"p.{DL_SW}"], r.state(350)[f"p.{DL_SW}"])
    assert r.state(350)["card0"] == "TAPE"


def test_tape_kept_across_power_cycle():
    """TAPE set by CC 88 is a sound change: not saved (on again F01 plays as stored, the delay off); SEL's memory of
    it is the FM-1's and is kept: SEL on the DELAY page turns the delay on as TAPE. TAPE saved in a user preset comes
    back with that preset"""
    fl = tp.flash_file("dltape-pc")
    r1 = run("dltape-pc-1", [cc(100, 88, 127), "200 btn 3", "300 btn 3", "3500 dump"], 4000, flash=fl)
    assert r1.saves and r1.state(3500)[f"p.{DL_SW}"] == "0", (r1.saves, r1.state(3500)[f"p.{DL_SW}"])
    rec = tp.settings_record(tp.read_img(fl))
    assert rec[tp.O_PANEL + DL_SW] == 0 and rec[O_LAST_ON_DL] == 3, (rec[tp.O_PANEL + DL_SW], rec[O_LAST_ON_DL])
    r2 = tp.boot("dltape-pc-2", fl, ["150 btn 3", "200 btn 3", "300 dump"], 400)
    assert r2.state(300)[f"p.{DL_SW}"] == "3" and r2.state(300)["card0"] == "TAPE", r2.state(300)["card0"]
    fl2 = tp.flash_file("dltape-pc-on")
    r = run("dltape-pc-on-1", [cc(100, 88, 127)], 3500, flash=fl2)
    assert not r.saves, r.saves                           # a knob alone: nothing written
    s = tp.boot("dltape-pc-on-2", fl2).state(100)
    assert s[f"p.{DL_SW}"] == "0", s[f"p.{DL_SW}"]          # F01 as stored
    fl3 = tp.flash_file("dltape-pc-u")
    run("dltape-pc-u-1", [cc(100, 88, 127), "200 btn 9", "300 btn 9"], 3500, flash=fl3)   # SAVE to U01
    s = tp.boot("dltape-pc-u-2", fl3).state(100)
    assert s["header"].startswith("U01") and s[f"p.{DL_SW}"] == "3" and s["card.10.0"] == "TAPE", s["header"]


@pytest.mark.parametrize("stored,want,last,want_last", [(0, 0, 1, 1), (0, 0, 3, 3), (1, 1, 2, 2), (2, 2, 1, 1), (3, 3, 3, 3),
                                                        (4, 0, 4, 1), (0xFF, 0, 0, 1)])
def test_settings_record_values(stored, want, last, want_last):
    """a saved record with the delay at each value an earlier version wrote (OFF / DIG / ANA) loads unchanged, TAPE
    too; a value no version wrote reads OFF; SEL's memory 1..3 is kept, anything else reads DIG"""
    fl = tp.flash_file(f"dltape-rec{stored}")
    run(f"dltape-rec{stored}-1", [cc(20, 114, 127)], 3500, flash=fl)   # (a setup change: a record is written)
    img = tp.read_img(fl)
    rec = tp.settings_record(img)
    rec[tp.O_PANEL + DL_SW], rec[O_LAST_ON_DL] = stored, last
    rec[tp.O_PRESET] = tp.NO_PRESET                         # (the record's own sound plays)
    tp.put_settings(img, rec)
    open(fl, "wb").write(img)
    r = tp.boot(f"dltape-rec{stored}-2", fl, ["150 btn 3", "200 btn 3", "250 dump"] if want == 0 else [], 400, check_preset=False)
    s = r.state(100)
    assert s[f"p.{DL_SW}"] == str(want) and s["card.10.0"] == NAMES[want], (s[f"p.{DL_SW}"], s["card.10.0"])
    if want == 0:                                       # SEL on DELAY: on again as the remembered type
        assert r.state(250)[f"p.{DL_SW}"] == str(want_last), (r.state(250)[f"p.{DL_SW}"], want_last)


def test_v1_and_old_v2_records_keep_the_delay():
    """records from 0.1-0.3 (version 1) and 0.4-0.7 (version 2, flags 0) with DIG and ANA: loaded unchanged"""
    for sw in (1, 2):
        fl = tp.flash_file(f"dltape-v1-{sw}")
        img = bytearray(b"\xff" * 5 * tp.SECTOR)
        tp.put_settings(img, tp.v1_record({0: 1, DL_SW: sw, DL_SW + 1: 70}, preset=27))   # (U16: empty)
        open(fl, "wb").write(img)
        s = tp.boot(f"dltape-v1-{sw}", fl, check_preset=False).state(100)
        assert s[f"p.{DL_SW}"] == str(sw) and s[f"p.{DL_SW + 1}"] == "70", (sw, s[f"p.{DL_SW}"])
        r = tp.v1_record({0: 1, DL_SW: sw}, preset=27)
        r[tp.O_VER] = 2
        img = bytearray(b"\xff" * 5 * tp.SECTOR)
        tp.put_settings(img, r)
        open(fl, "wb").write(img)
        s = tp.boot(f"dltape-v2-{sw}", fl, check_preset=False).state(100)
        assert s[f"p.{DL_SW}"] == str(sw), (sw, s[f"p.{DL_SW}"])


def test_old_preset_bank_keeps_the_delay():
    """a 0.4-0.9.2 preset bank (v2, 64-byte records) with U01 on ANA and U02 on DIG: they load as ANA and DIG"""
    dflt = tp.defaults()
    fl = tp.flash_file("dltape-bank2")
    img = bytearray(b"\xff" * 5 * tp.SECTOR)
    recs = []
    for i in range(16):
        p = bytearray(dflt[:48])
        p[DL_SW], p[DL_SW + 1] = (2, 60) if i == 0 else (1, 40) if i == 1 else (0, 0)
        recs.append(struct.pack("<BBBB", 2, 48, 1 if i < 2 else 0, 0) + b"OLD DLY     " + bytes(p))
    tp.put_bank(img, tp.BANK_A, 7, recs)
    open(fl, "wb").write(img)
    b = tp.boot("dltape-bank2", fl, ["150 midi 0xC0 16 0", "250 dump", "300 midi 0xC0 17 0", "400 dump",
                                     "400 expect preset", "400 expect sound"], 500)
    assert not b.fails, b.fails
    assert b.state(250)[f"p.{DL_SW}"] == "2" and b.state(400)[f"p.{DL_SW}"] == "1", (
        b.state(250)[f"p.{DL_SW}"], b.state(400)[f"p.{DL_SW}"])


def test_user_preset_with_tape_round_trips():
    """a user preset saved with TAPE: named "... TD", stored as 3, and after a power cycle it loads as TAPE"""
    fl = tp.flash_file("dltape-preset")
    lines = [cc(20, 88, 127), cc(21, 89, 50), "100 btn 9", "140 btn 9", "3500 dump"]
    r1 = run("dltape-preset-1", lines, 4000, flash=fl)
    assert r1.bank_saves, r1.bank_saves
    hdr = r1.state(3500)["header"]
    assert hdr.startswith("U01 ") and hdr.split()[-1] == "TD", hdr
    r2 = tp.boot("dltape-preset-2", fl, [cc(150, 88, 0), "200 midi 0xC0 16 0", "300 dump", "300 expect preset",
                                         "300 expect sound"], 400)
    assert not r2.fails, r2.fails
    s = r2.state(300)
    assert s["header"] == hdr and s[f"p.{DL_SW}"] == "3" and s["card.10.0"] == "TAPE", (s["header"], s[f"p.{DL_SW}"])


def test_factory_presets_unchanged():
    """the factory presets store the delay type (DIG 1 / ANA 2 / TAPE 3), not a CC value, so the new CC bands change
    none of them; the 0.9.5 presets F06 DRIVEN 77, F14 VIBES ECHO, F17 TAPE ECHO and F19 RADIO (the starters U10 / U12
    until 0.9.5's RC) use TAPE. The user bank's starters (PR_SDATA, 0.9.5) store the type the same way: U04 DREAM PAD
    and U08 REEDY use TAPE"""
    import re
    src = open(tp.os.path.join(tp.os.path.dirname(__file__), "..", "..", "firmware", "src", "presets.c")).read()
    fac = re.search(r"PR_FDATA\[[^=]*= \{(.*?)\n\};", src, re.S).group(1)
    sta = re.search(r"PR_SDATA\[[^=]*= \{(.*?)\n\};", src, re.S).group(1)
    for block, n_tape in ((fac, 4), (sta, 2)):
        vals = [int(v) for v in re.findall(r"P_DL_SW, (\d+)", block)]
        assert vals and all(v in (1, 2, 3) for v in vals) and vals.count(3) == n_tape, vals
    assert src.count("P_DL_SW, ") == len(re.findall(r"P_DL_SW, (\d+)", fac + sta))   # (no third table missed)
