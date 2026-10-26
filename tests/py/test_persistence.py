# SPDX-License-Identifier: GPL-3.0-only
"""Power cycles on the host (PLAN P11.4): the host build keeps its flash in a file (FRODHES_FLASH), so a second run
is the FM-1 switched on again with exactly what the firmware saved. The settings (the system setup and the preset
last loaded or saved, which plays again as it is stored: knobs turned after it are not kept, test_autosave_setup.py),
the user presets and the sequencer's pattern survive; the saves wait 2 s after the last change (1.5 s for the pattern) and for silence; a
corrupt record falls back (to the other copy; both: F01); old record versions convert; 0.9.5's flash migrates.
The flash file (tests/host.c HOST_SECS) is the FM-1's sectors: [0] 0xFC000 (0.9.5's settings: [magic DNGY][len][crc32]
+ 256 bytes; 0.9.6's record A: the same header + 256 + 1536 bytes, [seq][~seq]), [1] [2] 0.9.5's preset bank A / B
({magic FRPB, seq, len, crc} + 16 records, presets.c), [3] [4] 0.9.5's pattern A / B ({magic FSQ1, seq, n, crc} +
8-byte events, seq.c), [5] 0xFD000 the record B, [6] 0xFE000 the pattern (0.9.6). A five-sector file is 0.9.5's flash."""
import os
import struct
import zlib

import numpy as np
import pytest

from hostrun import FS, OUT, cc, note, run

SECTOR = 4096
SET, BANK_A, BANK_B, SEQ_A, SEQ_B, REC_B, PAT = range(7)    # (SET: 0.9.5's settings and 0.9.6's record A)
NSEC = 7
MAGIC = 0x59474E44
REC_N = 256 + 16 * 96                                      # the record: the settings, the 16 user presets (v3)
# settings_t offsets (params.c; append-only, so these never move)
O_VER, O_FLAGS, O_OCT, O_PRESET, O_BPM, O_TUNE, O_TRANS, O_PANEL, O_UNDO_VALID, O_PANEL2 = 0, 1, 2, 3, 4, 10, 19, 24, 88, 89
P = dict(TYPE=0, DRIVE=1, TW_SW=2, TW_DEPTH=3, TW_RATE=4, TW_MODE=6, CP_SW=8, CP_DEPTH=9, CP_SPEED=10, CP_SYNC=11,
         CP_FDBK=12, RV_DEPTH=19, PH_SW=28, PH_DEPTH=29, PH_SPEED=30, PH_SYNC=31, PH_FDBK=32, LEVEL=37, CMP_RATIO=38,
         CMP_GAIN=39, FL_TYPE=40, FL_FREQ=41, LFO_RATE=45)


# a record that names an empty user slot (U16, never a starter): the boot plays the record's own sound (snd_base:
# the last preset loaded or saved), so the record's panel and its conversions are what the tests see
NO_PRESET = 34


def flash_file(tag):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, tag + ".flash")
    if os.path.exists(p):
        os.remove(p)
    return p


def read_img(path):
    d = bytearray(open(path, "rb").read())
    assert len(d) in (5 * SECTOR, NSEC * SECTOR), len(d)
    return d + b"\xff" * (NSEC * SECTOR - len(d))


def sector(img, k):
    return img[k * SECTOR:(k + 1) * SECTOR]


def put_sector(img, k, data):
    data = bytes(data) + b"\xff" * (SECTOR - len(data))
    if len(img) < (k + 1) * SECTOR:
        img += b"\xff" * ((k + 1) * SECTOR - len(img))
    img[k * SECTOR:(k + 1) * SECTOR] = data


def record_at(img, k):
    """the record in sector k (0.9.6's: n = REC_N): (seq, data) or None"""
    s = sector(img, k)
    if len(s) < SECTOR:
        return None
    magic, n, crc = struct.unpack("<III", s[:12])
    if magic != MAGIC or n != REC_N or zlib.crc32(bytes(s[12:12 + n])) != crc:
        return None
    q, nq = struct.unpack("<II", s[12 + n:20 + n])
    return (q if q == nq ^ 0xFFFFFFFF else 0), bytearray(s[12:12 + n])


def newest_record(img):
    """the sector of the record the load takes (set_store.c: the newer seq, equal: A) and its data"""
    a, b = record_at(img, SET), record_at(img, REC_B)
    if a and (not b or ((b[0] - a[0]) & 0xFFFFFFFF) == 0 or ((b[0] - a[0]) & 0x80000000)):
        return SET, a[1]
    assert b, "no record"
    return REC_B, b[1]


def settings_record(img):
    return newest_record(img)[1][:256]


def user_presets(img):
    """the record's 16 user presets (96 bytes each, v3)"""
    return newest_record(img)[1][256:]


def put_record(img, k, data, seq):
    data = bytes(data)
    put_sector(img, k, struct.pack("<III", MAGIC, len(data), zlib.crc32(data)) + data +
               struct.pack("<II", seq, seq ^ 0xFFFFFFFF))


def put_settings(img, rec):
    """the settings into the record the load takes (its presets, its seq kept); no record: 0.9.5's settings sector"""
    if record_at(img, SET) or record_at(img, REC_B):
        k, data = newest_record(img)
        put_record(img, k, bytes(rec) + bytes(data[256:]), record_at(img, k)[0])
    else:
        put_sector(img, SET, struct.pack("<III", MAGIC, len(rec), zlib.crc32(bytes(rec))) + bytes(rec))


def put_bank(img, k, seq, records):
    body = b"".join(records)
    put_sector(img, k, struct.pack("<IIII", 0x42505246, seq, len(body), zlib.crc32(body)) + body)


def boot(tag, flash, lines=(), end=400, check_preset=True):
    """switch on with this flash: the state at 100 ms (before anything is touched)"""
    first = ["100 dump", "100 expect sound"] + (["100 expect preset"] if check_preset else [])
    return run(tag, first + list(lines), end, flash=flash, check=False)


# VIBES with NOISE 0: a short VIBES note with the damper noise on can leave a voice alive in exact silence, and then
# nothing saves (test_vibes_notes_let_go_so_it_saves, reported)
ENGINES = {"rhodes": [], "vibes": [cc(10, 79, 0), cc(11, 104, 0)]}


def test_vibes_notes_let_go_so_it_saves():
    """VIBES: a C4 held 200 ms, let go: the engine goes quiet (no voice left) and a change made then is saved 2 s
    later, as on the Rhodes. (A released note used to keep a silent voice alive forever, so nothing ever saved; fixed
    by the bar engine's kill under 1 LSB and the noise envelope reaching zero, 5bce504.)"""
    r = run("vibes-quiet", [cc(10, 79, 0), "300 midi 0x90 60 100", "500 midi 0x80 60 0", "3000 dump", cc(3000, 114, 127)],
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
                            "40 turn 0 1", "42 turn 0 1", "45 turn 0 1", "50 turn 5 7",   # DECAY page, TUNE +7
                            "60 btn 7", "65 turn 2 -12",                # TEMPO, BPM 108
                            cc(70, 53, 2), cc(71, 56, 4), cc(72, 61, 2), cc(73, 46, 60),   # ARP RATE, chord, BARS, SWING
                            "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9",   # SAVE to U03
                            cc(200, 63, 127)]                           # step-record three notes
    for i, n in enumerate((60, 64, 67)):
        lines += [f"{300 + 100 * i} midi 0x90 {n} 100", f"{340 + 100 * i} midi 0x80 {n} 0"]
    lines += [cc(700, 63, 0), "6000 dump"]
    r1 = run(f"pc1-{eng}", lines, 6500, flash=fl)
    s1 = r1.state(6000)
    assert s1["header"] == "U03 " + ("VIBE MOT" if eng == "vibes" else "ST77") and s1["notes"] == "9"   # (VIBES: the motor on)
    assert r1.saves and r1.bank_saves and r1.seq_saves, (r1.saves, r1.bank_saves, r1.seq_saves)
    r2 = boot(f"pc2-{eng}", fl, ["150 keys 0x80", "600 keys 0", "700 expect rms > -45"], 900)
    assert not r2.fails, r2.fails
    s2 = r2.state(100)
    for k in ("header", "oct", "bpm", "notes", "bars", "u.3", "vcard.1.3", "card.15.1", "card.13.0", "card.14.0",
              "card.14.2"):
        assert s2[k] == s1[k], (k, s1[k], s2[k])
    assert all(s2[k] == s1[k] for k in s1 if k.startswith("p."))
    assert (s2["oct"], s2["vcard.1.3"], s2["bpm"], s2["card.14.0"]) == ("1", "+7", "108.0", "2")


def test_save_timing():
    """the settings are saved 2 s after the last setup change (USB VOL, CC 114), and only once it is silent (the erase
    would stall the audio): a change with nothing playing saves at once after 2 s; a change while a note rings waits
    for silence; a
    user preset saved while a note rings, too; the pattern 1.5 s after its change while stopped and silent"""
    fl = flash_file("timing")
    lines = [cc(1000, 114, 127),                                        # silent: saved 2 s later
             "4000 midi 0x90 60 100", cc(4100, 114, 0), "8000 midi 0x80 60 0",   # a change under a note
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
    """0.9.6: the settings and the user presets are one record in two copies. The newer copy with a bad CRC, a bad
    magic or another length: the other copy loads (the settings and the presets as they were before the last save);
    both broken: the FM-1 starts as new (F01, and it plays F01; the presets are gone with the record); a version this
    firmware cannot read: F01, the user presets stay"""
    lines = [cc(20, 81, 90), "100 btn 9", "200 btn 9", "300 midi 0xC0 6 0"]   # U01 saved, then F07 shown
    fl, img = saved_flash("corrupt", lines)
    k, data = newest_record(img)
    good = data[:256]
    other = REC_B if k == SET else SET
    assert good[O_PRESET] == 6 and settings_record(img)[O_PRESET] == 6
    assert record_at(img, other)[1][O_PRESET] == 0                         # (the preset save's copy: F01, U01 in it)

    def broken(img, k, how):
        a = bytearray(img)
        if how == "crc":
            a[SECTOR * k + 12 + 30] ^= 0x40
        elif how == "magic":
            a[SECTOR * k] ^= 1
        else:
            r = sector(img, k)[12:12 + 255]
            put_sector(a, k, struct.pack("<III", MAGIC, 255, zlib.crc32(bytes(r))) + bytes(r))
        return a
    for how in ("crc", "magic", "length"):
        a = broken(img, k, how)
        open(fl, "wb").write(a)
        b = boot("corrupt-" + how, fl)
        s = b.state(100)
        assert s["header"] == "F01 STAGE 77" and s["u.1"] == "U01 ST77" and not b.fails, (how, s["header"], s["u.1"])
        open(fl, "wb").write(broken(a, other, how))
        b = boot("corrupt-both-" + how, fl)
        s = b.state(100)
        assert s["header"] == "F01 STAGE 77" and s["u.1"] != "U01 ST77" and not b.fails, (how, s["header"], s["u.1"])
    for ver in (3, 0):
        a = bytearray(img); r = bytearray(good); r[O_VER] = ver; put_settings(a, r)
        open(fl, "wb").write(a)
        b = boot(f"corrupt-version{ver}", fl)
        s = b.state(100)
        assert s["header"] == "F01 STAGE 77" and s["u.1"] == "U01 ST77" and not b.fails, (ver, s["header"], b.fails)


def test_corrupt_bank_and_pattern_use_the_other_copy():
    """0.9.6: the record (the settings and the presets) is written to two sectors in turn, the header last: a broken
    newer copy loads the older one, both broken: no user presets (the settings' defaults too). The FM-1's pattern
    has one sector (three free sectors only, frodhes_store.h): broken, no pattern, and nothing else is lost"""
    lines = [cc(20, 81, 90), "100 btn 9", "200 btn 9",                 # U01 = DRIVE 90
             cc(3000, 81, 30), "3100 btn 9", "3200 btn 9",            # U01 = DRIVE 30 (the other sector)
             cc(6000, 63, 127), "6100 midi 0x90 60 100", "6150 midi 0x80 60 0", cc(6200, 63, 0),   # pattern: 1 note
             cc(9000, 63, 127), "9100 midi 0x90 64 100", "9150 midi 0x80 64 0", cc(9200, 63, 0),   # 2 notes
             "12000 midi 0xC0 0 0"]
    fl, img = saved_flash("banks", lines, 13000)
    newer = newest_record(img)[0]
    older = REC_B if newer == SET else SET
    was = record_at(img, older)[1][256 + 16 + P["DRIVE"]]  # U01's DRIVE in the older copy (90, or 30 if a settings
    assert was in (90, 30)                                   # save came after the second preset save)
    cases = [("intact", [], "30", "2"), ("newer", [newer], str(was), "2"), ("pattern", [PAT], "30", "0"),
             ("both", [SET, REC_B], None, "2")]
    for name, broken, drive, notes in cases:
        a = bytearray(img)
        for k in broken:
            a[k * SECTOR + 20] ^= 0x10                       # a record byte: the CRC no longer matches
        open(fl, "wb").write(a)
        b = boot("banks-" + name, fl, ["150 midi 0xC0 16 0", "250 dump"] + (["250 expect preset"] if drive else []),
                 check_preset=False)
        s = b.state(250)
        assert b.state(100)["notes"] == notes, (name, b.state(100)["notes"])
        if drive is None:                                   # the record is gone: U01's name with it
            assert b.state(100)["u.1"] != "U01 ST77" and s["header"] != "U01 ST77" and not b.fails, (name, s["header"])
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
    the phaser is split out, the new parameters get their defaults, the preset index moves past F13..F19 (U01 19), the
    undo copy is dropped. Version 2 records from before 0.8 / 0.9.3 / VIBES (flags bits 1, 2, 8, 4 clear): LEVEL, the
    compressor's ratio and makeup and the filter get their defaults, the index is moved once."""
    dflt = defaults()
    fl = flash_file("v1")
    img = bytearray(b"\xff" * 5 * SECTOR)
    panel = {P["TYPE"]: 2, P["DRIVE"]: 50, P["CP_SW"]: 2, P["CP_DEPTH"]: 70, P["CP_SPEED"]: 33, P["CP_SYNC"]: 4, P["CP_FDBK"]: 21,
             P["RV_DEPTH"]: 60}
    put_settings(img, v1_record(panel, preset=27, extra={O_OCT: 1}))       # 27 = U16 then (empty: its sound plays)
    open(fl, "wb").write(img)
    b = boot("v1", fl)
    s = b.state(100)
    want = dict(TYPE=2, DRIVE=50, CP_SW=0, PH_SW=1, PH_DEPTH=70, PH_SPEED=33, PH_SYNC=4, PH_FDBK=21, RV_DEPTH=60)
    for k, v in want.items():
        assert s[f"p.{P[k]}"] == str(v), (k, s[f"p.{P[k]}"], v)
    for k in ("LEVEL", "CMP_RATIO", "CMP_GAIN", "FL_TYPE", "FL_FREQ", "LFO_RATE"):
        assert s[f"p.{P[k]}"] == str(dflt[P[k]]), k
    assert s["preset"] == str(NO_PRESET) and s["undo"] == "0" and s["oct"] == "1" and s["bpm"] == "100.0", (s["preset"], s["undo"])
    # a version 2 record from 0.4-0.7 (flags 0): LEVEL / ratio / makeup / filter were not there
    img2 = bytearray(b"\xff" * 5 * SECTOR)
    r = v1_record({P["TYPE"]: 1, P["DRIVE"]: 20}, preset=27)              # U16 of 12 factory presets (empty)
    r[O_VER] = 2
    for i in range(16):
        r[O_PANEL2 + i] = 99                                # garbage where the new parameters now live
    put_settings(img2, r)
    open(fl, "wb").write(img2)
    s = boot("v2old", fl).state(100)
    for k in ("LEVEL", "CMP_RATIO", "CMP_GAIN", "FL_TYPE", "FL_FREQ", "LFO_RATE"):
        assert s[f"p.{P[k]}"] == str(dflt[P[k]]), (k, s[f"p.{P[k]}"])
    assert (s["p.0"], s["p.1"], s["preset"]) == ("1", "20", str(NO_PRESET)), (s["p.0"], s["p.1"], s["preset"])
    for i in range(48, len(dflt)):                          # records v3: ids 48.. (panel3) at their defaults
        assert s[f"p.{i}"] == str(dflt[i]), (i, s[f"p.{i}"])


def test_094_settings_record_preset_index():
    """a 0.9.4 / 0.9.5-RC settings record (16 factory presets: U01 = 16; flags2 0) on 0.9.5 (19: F17..F19 before the
    user bank): F01..F16 stay, U01..U16 move by 3, once; the preset it names then plays as stored (an empty one: the
    record's panel)"""
    fl = flash_file("v094")
    for pre, want in ((0, 0), (15, 15), (16, 19), (31, 34)):
        img = bytearray(b"\xff" * 5 * SECTOR)
        r = v1_record({P["TYPE"]: 1, P["DRIVE"]: 20}, preset=pre)
        r[O_VER], r[O_FLAGS] = 2, 255                       # every flag up to 0.9.5's RC, flags2 (0.9.5) clear
        put_settings(img, r)
        open(fl, "wb").write(img)
        b = boot(f"v094-{pre}", fl, check_preset=want != NO_PRESET)
        s = b.state(100)
        assert s["preset"] == str(want) and not b.fails, (pre, s["preset"], b.fails)
        if want == NO_PRESET:
            assert (s["p.0"], s["p.1"]) == ("1", "20"), (pre, s["p.0"], s["p.1"])


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


V3_LEN = 96                                                # presets.c preset_t v3: 4 + 12 name + 64 values + 16 spare


def bank_len(img, k):
    magic, seq, n, crc = struct.unpack("<IIII", sector(img, k)[:16])
    return n if magic == 0x42505246 else None


def test_v2_bank_converts_to_v3_and_round_trips():
    """records v3 (P_SLOTS 64): a 0.4-0.9.2 bank (64-byte records, 48 parameters) loads with the new ids at their
    defaults and plays exactly as before; saving writes a v3 bank (96-byte records), which loads back to the same
    parameters and the same sound after a power cycle"""
    dflt = defaults()
    assert len(dflt) > 48
    fl = flash_file("bank2")
    img = bytearray(b"\xff" * 5 * SECTOR)
    recs = []
    for i in range(16):
        p = bytearray(dflt[:48])
        p[P["TYPE"]], p[P["DRIVE"]], p[P["RV_DEPTH"]], p[P["TW_SW"]], p[P["TW_MODE"]], p[P["TW_DEPTH"]] = 1, 33, 40, 1, 1, 70
        name = b"OLD V2      " if i == 0 else b" " * 12
        recs.append(struct.pack("<BBBB", 2, 48, 1 if i == 0 else 0, 0) + name + bytes(p))
    assert len(recs[0]) == 64
    put_bank(img, BANK_A, 7, recs)
    open(fl, "wb").write(img)
    play = ["150 midi 0xC0 16 0", "250 dump", "250 expect preset", "250 expect sound", "300 midi 0x90 60 100",
            "900 midi 0x80 60 0", "300 midi 0x90 67 90", "900 midi 0x80 67 0"]
    a = boot("bank2-a", fl, play + ["4000 btn 9", "4100 btn 9", "6000 dump"], end=6500)   # SAVE to U01 again
    assert not a.fails, a.fails
    s = a.state(250)
    assert s["header"] == "U01 OLD V2"
    for k, v in dict(TYPE=1, DRIVE=33, RV_DEPTH=40, TW_SW=1, TW_MODE=1, TW_DEPTH=70).items():
        assert s[f"p.{P[k]}"] == str(v), k
    for i in range(48, len(dflt)):
        assert s[f"p.{i}"] == str(dflt[i]), (i, s[f"p.{i}"], dflt[i])
    out = read_img(fl)
    assert a.bank_saves and len(user_presets(out)) == 16 * V3_LEN, a.bank_saves     # (0.9.6: in the record)
    assert bank_len(out, BANK_A) == 16 * 64 and bank_len(out, BANK_B) is None         # 0.9.5's bank: as it was
    rec = bytes(user_presets(out)[:V3_LEN])
    assert rec[0] == 3 and rec[1] == len(dflt) and rec[4:16] == b"SUIT TRM    ", rec[:16]   # (saving names it)
    b = boot("bank2-b", fl, play, end=2000)
    assert not b.fails, b.fails
    assert all(b.state(250)[k] == s[k] for k in s if k.startswith("p."))
    n = int(1.5 * FS)
    assert np.array_equal(a.L[:n], b.L[:n]) and np.abs(a.L[int(0.3 * FS):n]).max() > 300   # the same sound, sample for sample


MOT_ON, MOT_DEPTH, MOT_RATE, MOT_SHAPE, MOT_SYNC = 54, 55, 56, 57, 58   # params.c P_MOT_* (records v3)


def motor_v2(p, type_):
    """a 0.9.3-draft panel with TREM/WAH = MOTOR (SW 1, MODE 2): depth 90, rate 70, shape 30, sync 0"""
    p[P["TYPE"]], p[P["TW_SW"]], p[P["TW_MODE"]], p[P["TW_DEPTH"]], p[P["TW_RATE"]], p[7] = type_, 1, 2, 90, 70, 30


def test_v2_motor_becomes_the_motor_page():
    """records v3: a v2 settings record or user preset whose TREM/WAH was MOTOR on VIBES gets MOT_ON with that depth
    / rate / shape, and TREM/WAH off; on a Rhodes TYPE (where MOTOR played the AM tremolo) it stays the AM tremolo"""
    dflt = defaults()
    fl = flash_file("motv2")
    img = bytearray(b"\xff" * 5 * SECTOR)
    r = v1_record({}, preset=14 + 15)                        # U16 of 14 factory presets: empty, its sound plays
    r[O_VER], r[O_FLAGS] = 2, 1 | 2 | 4 | 8                  # a 0.9.3-draft record (no flags bit 16)
    p = bytearray(dflt[:48])
    motor_v2(p, 4)
    r[O_PANEL:O_PANEL + 32] = p[:32]
    r[O_PANEL2:O_PANEL2 + 16] = p[32:48]
    put_settings(img, r)
    recs = []
    for i in range(16):
        q = bytearray(dflt[:48])
        motor_v2(q, 4 if i == 0 else 2)
        name = b"U1          " if i == 0 else b"U2          "
        recs.append(struct.pack("<BBBB", 2, 48, 1 if i < 2 else 0, 0) + name + bytes(q))
    put_bank(img, BANK_A, 3, recs)
    open(fl, "wb").write(img)
    b = boot("motv2", fl, ["150 dump", "200 midi 0xC0 16 0", "300 dump", "400 midi 0xC0 17 0", "500 dump"], 600,
             check_preset=False)
    want = {MOT_ON: 1, MOT_DEPTH: 90, MOT_RATE: 70, MOT_SHAPE: 30, MOT_SYNC: 0, P["TW_SW"]: 0}
    for t in (150, 300):                                       # the panel, then U01 (VIBES)
        s = b.state(t)
        assert {k: int(s[f"p.{k}"]) for k in want} == want, (t, {k: s[f"p.{k}"] for k in want})
        assert s["vcard.2.0"] == "ON" and s["card.7.0"] == "OFF"
    s = b.state(500)                                           # U02, FELT with MOTOR: still the AM tremolo
    assert (s["p.0"], s[f"p.{P['TW_SW']}"], s[f"p.{P['TW_MODE']}"], s["card.7.0"]) == ("2", "1", "1", "TREM")


def test_motor_off_by_hand_survives_a_power_cycle():
    """the MOTOR page's ON card switched off is kept (a setup choice: no motor when the TYPE turns to VIBES again)"""
    fl = flash_file("motoff")
    lines = ["100 turn 1 1", "110 turn 1 1", "120 turn 1 1", "130 turn 1 1", "200 turn 0 1", "250 turn 0 1", "300 turn 0 1",
             "400 turn 0 1", "500 turn 2 -1", "600 turn 1 -1", "700 dump"]
    r1 = run("motoff-1", lines, 4000, flash=fl)
    assert r1.state(700)[f"p.{MOT_ON}"] == "0" and r1.saves             # (a Rhodes TYPE now: no VOICE 3)
    r2 = boot("motoff-2", fl, [cc(200, 79, 0), "300 dump"], 400)          # (F01 plays: VIBES by CC 79)
    assert r2.state(300)["bars"] == "1" and r2.state(300)["vcard.2.0"] == "OFF"


O_PANEL3 = 141                                              # settings_t panel3 (records v3: ids 48..63)
DRV_SW = 50                                                 # params.c P_DRV_SW: 0 OFF, 1 FDRV ("ON" before), 2..5


def test_drive_type_survives_and_old_records_read_as_classic():
    """the drive's TYPE (P_DRV_SW) BHLUE (5, the last) is kept in the settings and in a user preset across a power
    cycle; a record from before (1 = ON) reads as FDRV, the classic drive; a value above the top (what an older
    firmware sees in a record with a new TYPE: its top was 1) falls back to the default, FDRV (params.c
    params_sanitize; presets.c the same per value), so a downgrade plays the classic drive where the TYPE was"""
    fl = flash_file("drvtype")
    lines = [cc(20, 84, 105), cc(21, 81, 70), "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9",   # U03
             "4000 dump"]
    r1 = run("drvtype-1", lines, 4500, flash=fl)
    assert r1.saves and r1.bank_saves and r1.state(4000)[f"p.{DRV_SW}"] == "5"
    rec = settings_record(read_img(fl))
    assert rec[O_PANEL3 + DRV_SW - 48] == 5
    r2 = boot("drvtype-2", fl, [cc(150, 84, 0), "160 midi 0xC0 18 0", "700 dump"], 900)
    s, s2 = r2.state(100), r2.state(700)
    assert (s[f"p.{DRV_SW}"], s["card.19.0"]) == ("5", "BHLUE"), (s[f"p.{DRV_SW}"], s["card.19.0"])
    assert s2["header"].startswith("U03") and s2[f"p.{DRV_SW}"] == "5" and s2["fxdrv"] == "5", s2["header"]
    for v, want, card in ((1, "1", "FDRV"), (0, "0", "OFF"), (2, "2", "KHLON"), (6, "1", "FDRV"), (127, "1", "FDRV")):
        img = read_img(fl)
        rec[O_PANEL3 + DRV_SW - 48] = v
        rec[O_PRESET] = NO_PRESET                           # (the record's own sound plays, not U03)
        put_settings(img, rec)
        open(fl, "wb").write(img)
        s = boot(f"drvtype-v{v}", fl, check_preset=False).state(100)
        assert (s[f"p.{DRV_SW}"], s["card.19.0"], s["fxdrv"]) == (want, card, want), (v, s[f"p.{DRV_SW}"], s["card.19.0"])


def test_save_behind_the_shut_gate():
    """BUGHUNT #27: F06 (KHLON + COMP) with TREM on, a note played and let go: the silence gate shuts the output,
    but an idle residue under it (KHLON's offset, moved by the tremolo) kept fx_quiet() false, so a SAVE into a user
    slot showed its name and wrote nothing, and the settings waited forever (0.9.5 too). Now it saves, and the next
    boot plays the saved preset (tests/scripts/quiet_gated.txt: the same without the power cycle)"""
    fl = flash_file("gated-save")
    lines = ["100 midi 0xC2 5 0", "600 btn 5", "630 turn 2 1", "800 turn 1 1",    # F06, the LFO page: TREM
             "1400 midi 0x90 60 100", "2000 midi 0x80 60 0",
             "8100 btn 9", "8200 btn 9", "11000 dump"]
    r1 = run("gated-save-1", lines, 11500, flash=fl)
    s1 = r1.state(11000)
    assert s1["quiet"] == "1" and s1["dirty"] == "0", (s1["quiet"], s1["dirty"])
    assert r1.saves and r1.bank_saves, (r1.saves, r1.bank_saves)
    s2 = boot("gated-save-2", fl).state(100)
    assert s2["header"] == s1["header"] and s2["header"].startswith("U"), (s1["header"], s2["header"])
    assert all(s2[k] == s1[k] for k in s1 if k.startswith("p.")), [k for k in s1 if k.startswith("p.") and s2[k] != s1[k]]


def test_record_naming_an_empty_slot_runs_its_synced_rates():
    """BUGHUNT #32: a settings record naming a user slot that is empty (its bank broken, or never saved) boots with
    the record's own sound; its tempo-synced rates (TREM/WAH SYNC here) run as the panel says. They stayed 0 (free,
    standing still) until something re-published them: the boot published them only through a preset load"""
    fl, img = saved_flash("emptyslot", [cc(20, 17, 64), cc(21, 18, 100), cc(22, 20, 64),   # TREM on, DEPTH, SYNC
                                        "100 btn 9", "140 btn 9", "3000 dump"])          # SAVE to U01
    rec = settings_record(img)
    assert rec[O_PRESET] == 19 and rec[O_PANEL + 2] == 1, (rec[O_PRESET], rec[O_PANEL + 2])   # U01, its sound
    rec[O_PRESET] = NO_PRESET                                       # the record names U16: empty (never a starter)
    put_settings(img, rec)
    open(fl, "wb").write(img)
    b = boot("emptyslot-2", fl, ["300 expect sound", "2000 expect sound"], 2100, check_preset=False)
    s = b.state(100)
    assert s["p.5"] != "0" and s["p.2"] == "1", (s["p.5"], s["p.2"])  # the record's sound: TREM on, synced
    assert not b.fails, b.fails                                     # (0.9.5: "synced rate 0 is 0, the panel says ...")
