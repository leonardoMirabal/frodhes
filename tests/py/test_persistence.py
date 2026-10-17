# SPDX-License-Identifier: GPL-3.0-only
"""Power cycles on the host (PLAN P11.4): the host build keeps its flash in a file (FRODHES_FLASH), so a second run
is the FM-1 switched on again with exactly what the firmware saved. The settings, the user presets and the
sequencer's pattern survive; the saves wait 2 s after the last change (1.5 s for the pattern) and for silence; a
corrupt record falls back (the settings to F01, a preset bank or pattern sector to the other copy); old record
versions convert. The flash file is the FM-1's five sectors: settings ([magic DNGY][len][crc32] + the 256-byte
record, platform.c), preset bank A / B ({magic FRPB, seq, len, crc} + 16 records, presets.c), pattern A / B
({magic FSQ1, seq, n, crc} + 8-byte events, seq.c)."""
import os
import struct
import zlib

import numpy as np
import pytest

from hostrun import FS, OUT, cc, note, run

SECTOR = 4096
SET, BANK_A, BANK_B, SEQ_A, SEQ_B = range(5)
# settings_t offsets (params.c; append-only, so these never move)
O_VER, O_FLAGS, O_OCT, O_PRESET, O_BPM, O_TUNE, O_TRANS, O_PANEL, O_UNDO_VALID, O_PANEL2 = 0, 1, 2, 3, 4, 10, 19, 24, 88, 89
P = dict(TYPE=0, DRIVE=1, TW_SW=2, TW_DEPTH=3, TW_RATE=4, TW_MODE=6, CP_SW=8, CP_DEPTH=9, CP_SPEED=10, CP_SYNC=11,
         CP_FDBK=12, RV_DEPTH=19, PH_SW=28, PH_DEPTH=29, PH_SPEED=30, PH_SYNC=31, PH_FDBK=32, LEVEL=37, CMP_RATIO=38,
         CMP_GAIN=39, FL_TYPE=40, FL_FREQ=41, LFO_RATE=45)


def flash_file(tag):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, tag + ".flash")
    if os.path.exists(p):
        os.remove(p)
    return p


def read_img(path):
    d = bytearray(open(path, "rb").read())
    assert len(d) == 5 * SECTOR
    return d


def sector(img, k):
    return img[k * SECTOR:(k + 1) * SECTOR]


def put_sector(img, k, data):
    data = bytes(data) + b"\xff" * (SECTOR - len(data))
    img[k * SECTOR:(k + 1) * SECTOR] = data


def settings_record(img):
    magic, n, crc = struct.unpack("<III", sector(img, SET)[:12])
    assert magic == 0x59474E44 and n == 256 and zlib.crc32(bytes(sector(img, SET)[12:12 + n])) == crc
    return bytearray(sector(img, SET)[12:12 + n])


def put_settings(img, rec):
    put_sector(img, SET, struct.pack("<III", 0x59474E44, len(rec), zlib.crc32(bytes(rec))) + bytes(rec))


def put_bank(img, k, seq, records):
    body = b"".join(records)
    put_sector(img, k, struct.pack("<IIII", 0x42505246, seq, len(body), zlib.crc32(body)) + body)


def boot(tag, flash, lines=(), end=400, check_preset=True):
    """switch on with this flash: the state at 100 ms (before anything is touched)"""
    first = ["100 dump", "100 expect sound"] + (["100 expect preset"] if check_preset else [])
    return run(tag, first + list(lines), end, flash=flash, check=False)


# VIBES with NOISE 0: a short VIBES note with the damper noise on can leave a voice alive in exact silence, and then
# nothing saves (test_vibes_notes_let_go_so_it_saves, reported)
ENGINES = {"rhodes": [], "vibes": [cc(10, 79, 64), cc(11, 104, 0)]}


def test_vibes_notes_let_go_so_it_saves():
    """VIBES: a C4 held 200 ms, let go: the engine goes quiet (no voice left) and a change made then is saved 2 s
    later, as on the Rhodes. (A released note used to keep a silent voice alive forever, so nothing ever saved; fixed
    by the bar engine's kill under 1 LSB and the noise envelope reaching zero, 5bce504.)"""
    r = run("vibes-quiet", [cc(10, 79, 64), "300 midi 0x90 60 100", "500 midi 0x80 60 0", "3000 dump", cc(3000, 81, 40)],
            6000, flash=flash_file("vibes-quiet"))
    assert r.state(3000)["voices"] == "0" and r.state(3000)["quiet"] == "1", (r.state(3000)["voices"], r.state(3000)["quiet"])
    assert any(5000 <= t <= 5300 for t in r.saves), r.saves


@pytest.mark.parametrize("eng", ENGINES)
def test_power_cycle_keeps_everything(eng):
    """change the settings, save a user preset, record a pattern, let it go silent, switch off: on again, all of it
    is back (and the header names what plays)"""
    fl = flash_file(f"pc-{eng}")
    lines = ENGINES[eng] + [cc(20, 81, 90), cc(21, 91, 40),           # the panel: an edit of F01 (DRIVE, REVERB)
                            "30 btn 1",                                 # OCT+ (the FM-1's)
                            "40 btn 6", "45 turn 0 1", "50 turn 5 7",   # DECAY page, TUNE +7
                            "60 btn 7", "65 turn 2 -12",                # TEMPO, BPM 108
                            cc(70, 53, 2), cc(71, 56, 4), cc(72, 61, 2), cc(73, 46, 60),   # ARP RATE, chord, BARS, SWING
                            "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9",   # SAVE to U03
                            cc(200, 63, 127)]                           # step-record three notes
    for i, n in enumerate((60, 64, 67)):
        lines += [f"{300 + 100 * i} midi 0x90 {n} 100", f"{340 + 100 * i} midi 0x80 {n} 0"]
    lines += [cc(700, 63, 0), "6000 dump"]
    r1 = run(f"pc1-{eng}", lines, 6500, flash=fl)
    s1 = r1.state(6000)
    assert s1["header"] == "U03 " + ("VIBE" if eng == "vibes" else "ST77") and s1["notes"] == "9"   # (chord mode: triads)
    assert r1.saves and r1.bank_saves and r1.seq_saves, (r1.saves, r1.bank_saves, r1.seq_saves)
    r2 = boot(f"pc2-{eng}", fl, ["150 keys 0x80", "600 keys 0", "700 expect rms > -45"], 900)
    assert not r2.fails, r2.fails
    s2 = r2.state(100)
    for k in ("header", "oct", "bpm", "notes", "bars", "u.3", "card.4.3", "card.15.1", "card.13.0", "card.14.0",
              "card.14.2"):
        assert s2[k] == s1[k], (k, s1[k], s2[k])
    assert all(s2[f"p.{i}"] == s1[f"p.{i}"] for i in range(48))
    assert (s2["oct"], s2["card.4.3"], s2["bpm"], s2["card.14.0"]) == ("1", "+7", "108.0", "2")


def test_save_timing():
    """the settings are saved 2 s after the last change, and only once it is silent (the erase would stall the
    audio): a change with nothing playing saves at once after 2 s; a change while a note rings waits for silence; a
    user preset saved while a note rings, too; the pattern 1.5 s after its change while stopped and silent"""
    fl = flash_file("timing")
    lines = [cc(1000, 81, 40),                                          # silent: saved 2 s later
             "4000 midi 0x90 60 100", cc(4100, 81, 60), "8000 midi 0x80 60 0",   # a change under a note
             "9000 midi 0x90 64 100", "9100 btn 9", "9200 btn 9", "12000 midi 0x80 64 0",   # SAVE while it rings
             cc(14000, 63, 127), "14100 midi 0x90 67 100", "14150 midi 0x80 67 0", cc(14200, 63, 0),   # a step
             cc(19000, 43, 127)]                                                 # CLEAR, silent
    r = run("timing", lines, 21000, flash=fl)
    L = r.L
    quiet = lambda t: np.max(np.abs(L[max(0, int((t - 0.05) * FS)):int((t + 0.05) * FS)])) < 16   # -66 dBFS
    assert r.saves and 3000 <= r.saves[0] <= 3150, r.saves
    later = [t for t in r.saves if t > 4100]
    assert later and later[0] > 8000 and all(quiet(t / 1000) for t in later), (r.saves, [quiet(t / 1000) for t in later])
    assert len(r.bank_saves) == 1 and r.bank_saves[0] > 12000 and quiet(r.bank_saves[0] / 1000), r.bank_saves
    assert len(r.seq_saves) == 2 and r.seq_saves[0] - 14150 >= 1500 and quiet(r.seq_saves[0] / 1000), r.seq_saves
    assert 20500 <= r.seq_saves[1] <= 20600, r.seq_saves                  # the CLEAR: 1.5 s later
    assert all(quiet(t / 1000) for t in r.saves), r.saves


def saved_flash(tag, lines, end=4000):
    fl = flash_file(tag)
    run(tag + "-1", lines, end, flash=fl)
    return fl, read_img(fl)


def test_corrupt_settings_fall_back_to_f01():
    """a settings record with a bad CRC, a bad magic, another length, or a version this firmware cannot read: the
    FM-1 starts as new (F01, and it plays F01), the user presets stay"""
    lines = [cc(20, 81, 90), "100 btn 9", "200 btn 9", "300 midi 0xC0 6 0"]   # U01 saved, then F07 shown
    fl, img = saved_flash("corrupt", lines)
    good = settings_record(img)
    assert good[O_PRESET] == 6
    bad = []
    a = bytearray(img); a[SECTOR * SET + 12 + 30] ^= 0x40; bad.append(("crc", a))          # a byte flipped
    a = bytearray(img); a[SECTOR * SET + 0] ^= 1; bad.append(("magic", a))
    a = bytearray(img); r = good[:255]; put_sector(a, SET, struct.pack("<III", 0x59474E44, 255, zlib.crc32(bytes(r))) + bytes(r))
    bad.append(("length", a))
    a = bytearray(img); r = bytearray(good); r[O_VER] = 3; put_settings(a, r); bad.append(("version 3", a))
    a = bytearray(img); r = bytearray(good); r[O_VER] = 0; put_settings(a, r); bad.append(("version 0", a))
    for name, a in bad:
        open(fl, "wb").write(a)
        b = boot("corrupt-" + name.replace(" ", ""), fl)
        s = b.state(100)
        assert s["header"] == "F01 STAGE 77" and not b.fails, (name, s["header"], b.fails)
        assert s["u.1"] == "U01 ST77", (name, s["u.1"])


def test_corrupt_bank_and_pattern_use_the_other_copy():
    """the preset bank and the pattern are written to two sectors in turn, the header last: a broken newer copy
    loads the older one, both broken: no user presets / no pattern (and nothing else is lost)"""
    lines = [cc(20, 81, 90), "100 btn 9", "200 btn 9",                 # U01 = DRIVE 90 (bank seq 1)
             cc(3000, 81, 30), "3100 btn 9", "3200 btn 9",            # U01 = DRIVE 30 (seq 2, the other sector)
             cc(6000, 63, 127), "6100 midi 0x90 60 100", "6150 midi 0x80 60 0", cc(6200, 63, 0),   # pattern: 1 note
             cc(9000, 63, 127), "9100 midi 0x90 64 100", "9150 midi 0x80 64 0", cc(9200, 63, 0),   # 2 notes
             "12000 midi 0xC0 0 0"]
    fl, img = saved_flash("banks", lines, 13000)
    hdr = lambda k: struct.unpack("<IIII", sector(img, k)[:16])
    (_, sa, _, _), (_, sb, _, _) = hdr(BANK_A), hdr(BANK_B)
    newer, older = (BANK_A, BANK_B) if sa > sb else (BANK_B, BANK_A)
    qa, qb = struct.unpack("<IIII", sector(img, SEQ_A)[:16])[1], struct.unpack("<IIII", sector(img, SEQ_B)[:16])[1]
    snew, sold = (SEQ_A, SEQ_B) if qa > qb else (SEQ_B, SEQ_A)
    cases = [("intact", [], "30", "2"), ("newer", [newer, snew], "90", "1"), ("both", [BANK_A, BANK_B, SEQ_A, SEQ_B], None, "0")]
    for name, broken, drive, notes in cases:
        a = bytearray(img)
        for k in broken:
            a[k * SECTOR + 20] ^= 0x10                       # a record byte: the CRC no longer matches
        open(fl, "wb").write(a)
        b = boot("banks-" + name, fl, ["150 midi 0xC0 16 0", "250 dump"] + (["250 expect preset"] if drive else []),
                 check_preset=False)
        s = b.state(250)
        assert b.state(100)["notes"] == notes, (name, b.state(100)["notes"])
        if drive is None:                                   # the bank is gone: U01's name with it ("U01 ----"); the
            assert b.state(100)["u.1"] == "U01 ----" and s["header"] == "U01 ----", (name, s["header"])   # settings
            assert s["p.1"] == "30" and not b.fails, (s["p.1"], b.fails)        # still play the last sound, unchanged
        else:
            assert s["header"] == "U01 ST77" and s["p.1"] == drive and not b.fails, (name, s["p.1"], b.fails)


def v1_record(panel, preset, extra=None):
    """a version 1 settings record (0.1-0.3): 32 parameters, the rest of the bytes 0"""
    r = bytearray(256)
    r[O_VER], r[O_FLAGS], r[O_PRESET] = 1, 0, preset
    struct.pack_into("<H", r, O_BPM, 1000)
    r[6], r[8] = 1, 96                                     # clock, key velocity
    r[20:23] = b"\x01\x01\x01"
    for i, v in panel.items():
        r[O_PANEL + i] = v
    r[O_UNDO_VALID] = 1
    for k, v in (extra or {}).items():
        r[k] = v
    return r


def defaults():
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "src", "params.c")).read()
    return [int(d) for d in re.findall(r'\[P_\w+\] = \{"[^"]*", \d+, (\d+),', src)]


def test_old_settings_records_convert():
    """version 1 (0.1-0.3: 32 parameters, the phaser as CHO/PHA = 2 sharing the chorus' knobs, 12 factory presets):
    the phaser is split out, the new parameters get their defaults, the preset index moves past F13 / F14, the undo
    copy is dropped. Version 2 records from before 0.8 / 0.9.3 / VIBES (flags bits 1, 2, 8, 4 clear): LEVEL, the
    compressor's ratio and makeup and the filter get their defaults, the index is moved once."""
    dflt = defaults()
    fl = flash_file("v1")
    img = bytearray(b"\xff" * 5 * SECTOR)
    panel = {P["TYPE"]: 2, P["DRIVE"]: 50, P["CP_SW"]: 2, P["CP_DEPTH"]: 70, P["CP_SPEED"]: 33, P["CP_SYNC"]: 4, P["CP_FDBK"]: 21,
             P["RV_DEPTH"]: 60}
    put_settings(img, v1_record(panel, preset=12, extra={O_OCT: 1}))       # 12 = U01 then
    open(fl, "wb").write(img)
    b = boot("v1", fl)
    s = b.state(100)
    want = dict(TYPE=2, DRIVE=50, CP_SW=0, PH_SW=1, PH_DEPTH=70, PH_SPEED=33, PH_SYNC=4, PH_FDBK=21, RV_DEPTH=60)
    for k, v in want.items():
        assert s[f"p.{P[k]}"] == str(v), (k, s[f"p.{P[k]}"], v)
    for k in ("LEVEL", "CMP_RATIO", "CMP_GAIN", "FL_TYPE", "FL_FREQ", "LFO_RATE"):
        assert s[f"p.{P[k]}"] == str(dflt[P[k]]), k
    assert s["preset"] == "14" and s["undo"] == "0" and s["oct"] == "1" and s["bpm"] == "100.0", (s["preset"], s["undo"])
    # a version 2 record from 0.4-0.7 (flags 0): LEVEL / ratio / makeup / filter were not there
    img2 = bytearray(b"\xff" * 5 * SECTOR)
    r = v1_record({P["TYPE"]: 1, P["DRIVE"]: 20}, preset=13)
    r[O_VER] = 2
    for i in range(16):
        r[O_PANEL2 + i] = 99                                # garbage where the new parameters now live
    put_settings(img2, r)
    open(fl, "wb").write(img2)
    s = boot("v2old", fl).state(100)
    for k in ("LEVEL", "CMP_RATIO", "CMP_GAIN", "FL_TYPE", "FL_FREQ", "LFO_RATE"):
        assert s[f"p.{P[k]}"] == str(dflt[P[k]]), (k, s[f"p.{P[k]}"])
    assert (s["p.0"], s["p.1"], s["preset"]) == ("1", "20", "15"), (s["p.0"], s["p.1"], s["preset"])


def test_old_preset_bank_converts():
    """a 0.1-0.3 preset bank (48-byte records, 32 parameters, the phaser as CHO/PHA = 2): U01 loads with the phaser
    split out and the new parameters at their defaults; the panel is then what the header names"""
    dflt = defaults()
    fl = flash_file("bank1")
    img = bytearray(b"\xff" * 5 * SECTOR)
    recs = []
    for i in range(16):
        p = bytearray(dflt[:32])
        p[P["TYPE"]], p[P["DRIVE"]], p[P["CP_SW"]], p[P["CP_DEPTH"]], p[P["CP_SPEED"]] = 3, 77, 2, 90, 44
        name = b"OLD PHA     " if i == 0 else b" " * 12
        recs.append(struct.pack("<BBBB", 1, 32, 1 if i == 0 else 0, 0) + name + bytes(p))
    assert len(recs[0]) == 48
    put_bank(img, BANK_A, 5, recs)
    open(fl, "wb").write(img)
    b = boot("bank1", fl, ["150 midi 0xC0 16 0", "250 dump", "250 expect preset", "250 expect sound"])
    assert not b.fails, b.fails
    s = b.state(250)
    assert s["header"] == "U01 OLD PHA" and s["u.2"] == "U02 ----"
    got = {k: s[f"p.{P[k]}"] for k in ("TYPE", "DRIVE", "CP_SW", "PH_SW", "PH_DEPTH", "PH_SPEED", "LEVEL", "FL_FREQ")}
    assert got == dict(TYPE="3", DRIVE="77", CP_SW="0", PH_SW="1", PH_DEPTH="90", PH_SPEED="44",
                       LEVEL=str(dflt[P["LEVEL"]]), FL_FREQ=str(dflt[P["FL_FREQ"]])), got
