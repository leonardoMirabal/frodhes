# SPDX-License-Identifier: GPL-3.0-only
"""FM-1 key velocity (firmware/src/keyvel.c) on the host.

The host has no converter: "T vkey K RAW" (tests/host.c) hands key K's next press the driven reading RAW, as the
FM-1's scan would; a key without one plays at once at CONTEXT (KEY VEL until an analog key has played), so every
render from before key velocity is unchanged (Gate R). tests/c/keyvel_test.c checks the integer curve against floating
point and the ISR's state machine in a model of the scan tick (the wait, the fallback, a key let go before its reading,
bounces, storms); here: its build and run, the column table against the HAL's keymap, the velocities
tests/scripts/keyvel.txt gets against the same curve written in Python, the velocity reaching chord mode, the
arpeggiator, the sequencer's recording and MIDI out, short notes, TOUCH from the settings record (0 = MEDIUM, every
older record; out of range: MEDIUM) and the developer query."""
import math
import os
import re
import subprocess

from hostrun import HOST, ROOT, run
from test_persistence import boot, flash_file, put_settings, read_img, settings_record

O_KEY_VEL, O_VEL_CURVE = 8, 9                     # settings_t: clock 6, midi_ch 7, key_vel 8, vel_curve 9

# keyvel.c's constants (test_constants_are_keyvel_c checks them)
K = dict(KV_DBASE=185, KV_FLOOR0=450, KV_FLOOR_MIN=217, KV_FLOOR_STEP=48, KV_CEIL=1000, KV_UMID=15073)
GAMMA = [115 / 256, 179 / 256, 282 / 256]         # LIGHT MEDIUM HEAVY
LIGHT, MEDIUM, HEAVY, FIXED = range(4)


def curve(d, fl, touch, kvel):
    """keyvel.c kv_curve in floating point"""
    if touch == FIXED:
        return kvel
    fl = min(max(fl, K["KV_FLOOR_MIN"]), K["KV_CEIL"] - 1)
    b, c = K["KV_DBASE"], K["KV_CEIL"]
    s = 1.0 if d <= fl else 0.0 if d >= c else 1 - math.log2((d - b) / (fl - b)) / math.log2((c - b) / (fl - b))
    u = s ** GAMMA[touch] if s > 0 else 0.0
    um = K["KV_UMID"] / 32768
    v = 1 + (kvel - 1) * u / um if u <= um else kvel + (127 - kvel) * (u - um) / (1 - um)
    return min(max(v, 1), 127)


def src(name):
    return open(os.path.join(ROOT, name)).read()


def cc(t, n, v):
    return f"{t} midi 0xB0 {n} {v}"


def test_constants_are_keyvel_c():
    s = src("firmware/src/keyvel.c")
    for n, v in K.items():
        assert re.search(rf"#define {n} {v}u\b", s), n
    assert "static const uint16_t KV_GAMMA[3] = {115, 179, 282};" in s
    assert "github.com/charlesvestal/fm1-velocity" in s and "8f62ce0" in s and "Charles Vestal" in s


def test_no_float_in_keyvel_c():
    s = re.sub(r"/\*.*?\*/", "", src("firmware/src/keyvel.c"), flags=re.S)
    assert not re.search(r"\b(float|double)\b|\d\.\d", s)


def test_keyvel_c():
    """tests/c/keyvel_test.c: the curve within 1 of floating point everywhere, the state machine in the tick model"""
    os.makedirs(HOST, exist_ok=True)
    exe = os.path.join(HOST, "keyvel_test")
    subprocess.run(["cc", "-O2", "-Wall", "-Werror", "-Wno-unused-function", "-I" + os.path.join(ROOT, "firmware", "src"),
                    os.path.join(ROOT, "tests", "c", "keyvel_test.c"), "-lm", "-o", exe], check=True)
    p = subprocess.run([exe], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout
    assert "0 failed" in p.stdout and p.stdout.count("\nok ") >= 45, p.stdout


def keymap():
    s = src("firmware/hal/fm1_input.h")
    body = re.search(r"FM1_KEYMAP\[6\]\[FM1_NCOL\] = \{(.*?)\n\};", s, re.S).group(1)
    return [[int(x) for x in re.findall(r"-?\d+", row)] for row in re.findall(r"\{([^}]*)\}", body)]


def test_colkey_is_the_keymap():
    """KV_COLKEY (keyvel.c) and the C test's copy are FM1_KEYMAP's rows PA7 / PA8 as note numbers"""
    km = keymap()
    want = [[km[3 + r][c] - 14 if km[3 + r][c] >= 14 else 0xFF for r in (0, 1)] for c in range(11)]
    body = re.search(r"KV_COLKEY\[KV_NCOL\]\[2\] = \{(.*?)\};", src("firmware/src/keyvel.c"), re.S).group(1)
    got = [[int(x, 0) for x in re.findall(r"0x[0-9A-F]+|\d+", row)] for row in re.findall(r"\{([^}]*)\}", body)]
    assert got == want
    t = src("tests/c/keyvel_test.c")
    body = re.search(r"KEYMAP\[6\]\[NCOL\] = \{.*?\n(.*?)\n\};", t, re.S).group(1)
    assert [[int(x) for x in re.findall(r"-?\d+", row)] for row in re.findall(r"\{([^}]*)\}", body)][1:] == km[1:]
    analog = {k for row in want for k in row if k != 0xFF}
    assert analog == set(range(20))               # F3..C5; the top 7 (C#5..G5) have no converter
    h = src("firmware/src/keyvel.h")
    assert "#define KV_ANALOG 0x000FFFFFu" in h and "#define KV_TOP 0x07F00000u" in h


def note_ons(r, t0=0, t1=10 ** 9):
    return [(t, n, v) for t, s, n, v in r.midi_out if s & 0xF0 == 0x90 and v and t0 <= t <= t1]


def expect_script(lines, touch=MEDIUM, kvel=96):
    """the note-ons a vkey / keys script should give: the curve, the floor learned downward only, CONTEXT the running
    average (a quarter of the way, integer x 256 as keyvel.c) of the analog keys' velocities"""
    floor, ctx, want, vk = {}, None, [], {}
    for l in lines:
        f = l.split()
        t = int(f[0])
        if f[1] == "vkey":
            if int(f[2]) < 20:
                vk[int(f[2])] = int(f[3])
        elif f[1] == "keys":
            m = int(f[2], 0)
            for k in range(27):
                if not m >> k & 1:
                    continue
                if touch == FIXED:
                    v = kvel
                elif k in vk:
                    d = vk.pop(k)
                    fl = floor.get(k, K["KV_FLOOR0"])
                    v = round(curve(d, fl, touch, kvel))
                    if d < fl:
                        floor[k] = max(d, fl - K["KV_FLOOR_STEP"], K["KV_FLOOR_MIN"])
                    ctx = v << 8 if ctx is None else ctx + int((v * 256 - ctx) / 4)
                else:
                    v = kvel if ctx is None else (ctx + 128) >> 8
                want.append((t, 53 + k, v))
            vk = {k: d for k, d in vk.items() if not m >> k & 1}
    return want


def check(got, want, tol=1):
    assert [(t, n) for t, n, _ in got] == [(t, n) for t, n, _ in want], (got, want)
    bad = [(g, w) for g, w in zip(got, want) if abs(g[2] - w[2]) > tol]
    assert not bad, bad


def test_script_is_the_curve():
    """tests/scripts/keyvel.txt's velocities (MIDI out) = the curve in floating point (+-1), with the floor learned
    downward only and CONTEXT the running average of the analog keys"""
    lines = [l.strip() for l in open(os.path.join(ROOT, "tests", "scripts", "keyvel.txt")) if l.strip() and l[0] != "#"]
    r = run("keyvel-script", [l for l in lines if " end" not in l], 3000)
    got, want = note_ons(r), expect_script(lines)
    check(got, want)
    # every press of the script plays, once
    assert len(got) == 12


def test_chord_arp_seq_get_the_velocity():
    """a key's velocity reaches every note of its chord, the arpeggiator, the sequencer's live recording (played back
    over MIDI out with it) and MIDI out (the key echo)"""
    r = run("keyvel-chord", [cc(100, 56, 3), "200 vkey 7 500", "210 keys 0x80", "400 keys 0"], 600)
    on = note_ons(r, 200, 300)
    v = round(curve(500, 450, MEDIUM, 96))
    assert len(on) == 3 and all(abs(x[2] - v) <= 1 for x in on), on          # MAJ: C E G, the key's velocity
    r = run("keyvel-arp", [cc(100, 52, 1), "200 vkey 7 900", "210 keys 0x80", "1500 keys 0"], 1700)
    on = note_ons(r, 200, 1500)
    v = round(curve(900, 450, MEDIUM, 96))
    assert len(on) >= 3 and all(abs(x[2] - v) <= 1 for x in on), on          # every step at the key's velocity
    r = run("keyvel-seq", [cc(100, 61, 1), cc(100, 62, 0), cc(200, 60, 127), cc(210, 63, 127),
                           "300 vkey 9 950", "310 keys 0x200", "500 keys 0", cc(1000, 63, 0)], 6000)
    v = round(curve(950, 450, MEDIUM, 96))
    on = note_ons(r, 1000, 6000)
    assert on and all(n == 62 and abs(x - v) <= 1 for _, n, x in on), (v, on)   # the loop plays it back at its velocity
    assert v < 60


def test_short_press_plays():
    """a press of 1 ms with its reading plays once and stops; one without plays KEY VEL once and stops"""
    r = run("keyvel-short", ["200 vkey 3 600", "210 keys 0x8", "211 keys 0", "300 keys 0x10", "301 keys 0"], 400)
    ons = note_ons(r)
    offs = [x for x in r.midi_out if x[1] & 0xF0 == 0x80]
    assert [(t, n) for t, n, _ in ons] == [(210, 56), (300, 57)]
    assert abs(ons[0][2] - curve(600, 450, MEDIUM, 96)) <= 1
    assert ons[1][2] == ons[0][2]                                            # CONTEXT = the one analog press
    assert [(t, n) for t, _, n, _ in offs] == [(211, 56), (301, 57)]


def test_no_reading_plays_key_vel():
    """without a reading every key plays KEY VEL (as before key velocity); KEY VEL from the settings record moves it"""
    r = run("keyvel-none", ["200 keys 0x7FFFFFF", "300 keys 0"], 400)
    assert len(note_ons(r)) == 27 and {v for _, _, v in note_ons(r)} == {96}
    assert set(_touch(0, kvel=56, lines=["200 keys 0x7FFFFFF", "300 keys 0"])) == {56}


def _touch(vel_curve, kvel=96, lines=None, tag=None):
    """boot with vel_curve (and KEY VEL) in the settings record, play lines: the note-on velocities"""
    tag = tag or f"kv-touch{vel_curve}-{kvel}"
    fl = flash_file(tag)
    run(tag + "-mk", [cc(100, 114, 127), "2200 dump"], 4000, flash=fl)        # a setup change, saved 2 s later
    img = read_img(fl)
    rec = settings_record(img)
    assert rec[O_KEY_VEL] == 96 and rec[O_VEL_CURVE] == 0                    # a fresh record: MEDIUM
    rec[O_VEL_CURVE], rec[O_KEY_VEL] = vel_curve, kvel
    put_settings(img, rec)
    open(fl, "wb").write(img)
    r = boot(tag, fl, lines=lines or [], end=2000, check_preset=False)
    return [v for _, _, v in note_ons(r, 150)]


PRESSES = ["200 vkey 7 743", "210 keys 0x80", "300 keys 0", "400 vkey 7 520", "410 keys 0x80", "500 keys 0",
           "600 vkey 7 940", "610 keys 0x80", "700 keys 0", "800 keys 0x400000", "900 keys 0"]


def test_settings_touch():
    """TOUCH lives in set.vel_curve: 0 (every record before key velocity) = MEDIUM, 3 LIGHT, 1 HEAVY, 2 FIXED; a value
    out of range reads MEDIUM (params_sanitize); each plays its curve, and KEY VEL is a medium press's velocity"""
    for vc, touch in ((0, MEDIUM), (3, LIGHT), (1, HEAVY), (2, FIXED), (9, MEDIUM)):
        for kvel in (96, 64):
            got = _touch(vc, kvel, PRESSES + [f"950 expect state touch {['LIGHT', 'MEDIUM', 'HEAVY', 'FIXED'][touch]}"])
            want = [v for _, _, v in expect_script(PRESSES, touch, kvel)]
            assert len(got) == 4 and all(abs(g - w) <= 1 for g, w in zip(got, want)), (vc, kvel, got, want)
            if touch == FIXED:
                assert got == [kvel] * 4
            if touch == MEDIUM:
                assert abs(got[0] - kvel) <= 1                                # 743: a medium press
    light, medium, heavy = (_touch(vc, 96, PRESSES) for vc in (3, 0, 1))
    assert light[2] > medium[2] > heavy[2]                                    # the same soft press


def test_dev_query():
    """developer CC 42 0 / 1 / 4 answer in CC 38 (item) / 39 / 40 triples: WAIT 1, TOUCH MEDIUM, the presses counted,
    the floors; CC 41 24 / 25 set WAIT, 32 the floors back, 1 (OFF) and 0 count again"""
    r = run("keyvel-dev", [cc(100, 41, 0), "200 vkey 7 300", "210 keys 0x80", "300 keys 0", "310 keys 0x400000",
                           "320 keys 0", "330 btn 7", cc(400, 42, 0), cc(500, 42, 1), cc(600, 42, 4),
                           cc(700, 41, 25), cc(700, 41, 32), cc(710, 42, 0), cc(720, 42, 4),
                           cc(800, 41, 1), cc(810, 42, 1)], 900)
    def items(t0, t1):
        out, cur, hi = {}, None, 0
        for t, s, n, v in r.midi_out:
            if not t0 <= t <= t1 or s != 0xB0:
                continue
            if n == 38:
                cur = v
            elif n == 39:
                hi = v
            elif n == 40 and cur is not None:
                out[cur] = hi << 7 | v
        return out
    a = items(400, 600)
    assert a[2] == 1 and a[3] == 1                                            # WAIT 1, TOUCH MEDIUM
    assert a[12] == 2 and a[13] == 1 and a[14] == 1 and a[15] == 0, a         # 2 presses, 1 button, 1 reading, 0 fell back
    assert a[32 + 7] == 450 - 48 and a[32 + 6] == 450, a                      # C4's floor a step down, the others' 450
    b = items(710, 720)
    assert b[2] == 2 and b[32 + 7] == 450, b                                  # WAIT 2; the floors back
    c = items(810, 810)
    assert c[12] == 0 and c[14] == 0, c                                       # a mode: the counters start again
