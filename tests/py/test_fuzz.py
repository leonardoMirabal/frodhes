# SPDX-License-Identifier: GPL-3.0-only
"""Fuzz (PLAN P11.5): seeded random streams of MIDI (notes with lost note-offs, CCs, program changes, clock, start /
stop / continue, SysEx and garbage), the FM-1's keys, buttons (taps and holds) and knobs, for 3 s, on the Rhodes
and on VIBES; then the panic every DAW sends (CC 123 and the transport off, on all 16 channels) and two probes.
Each seed must:

  - run to the end without a crash, and without any undefined behaviour or memory error (a second build with
    UBSan + ASan runs a share of the seeds; left shifts of negative numbers are allowed: the chip shifts
    arithmetically), and without the host's event ring dropping an event;
  - never put out more than the limiter's ceiling (24576 = the master clip's knee; CC 119 RAW is not fuzzed);
  - go silent after the panic (the engine alone, RAW, within 3 s), with no voice left (the Rhodes; VIBES can keep a
    silent voice, a reported bars.c bug, test_persistence.py);
  - sound when a note is played afterwards over MIDI (and from a key when KEY VEL allows), without a click (the
    second-difference detector of tools/bar_measure.py / hil_scan) and nothing near clipping;
  - keep each engine's CPU proxy under the block: the Rhodes' (the host's estimate of the voices alone, engine.c,
    at the P0 costs) + 1700 us for every effect and the rest under the 5805 us block; VIBES' (bars.c's cost model,
    the whole block with the effects already in it) under its own 4800 us gate (BAR_NS_GATE). Until 2026-10-18 the
    VIBES proxy got the 1700 us a second time, and a run that switched engines (program changes) compared one
    maximum for both.

200 seeds per engine by default (the sanitizer build runs every 5th); --long: 2000 per engine, all sanitized;
--fuzz-seeds N sets the count. A failing seed prints its script (build/host/py/fz-*.txt) to replay it."""
import os
import random
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from hostrun import BIN, FS, OUT, ROOT, build, env_rms, rms_db, run

SAN = os.path.join(ROOT, "build", "host", "frodhes-san")
SAN_FLAGS = ["-fsanitize=undefined,address", "-fno-sanitize=shift-base", "-fno-omit-frame-pointer"]
CHAOS_MS = 3000
KNOWN_CC = sorted({int(c) for c in re.findall(r'\[P_\w+\] = \{"[^"]*", \d+, \d+, F_\w+, (\d+)',
                                               open(os.path.join(ROOT, "firmware", "src", "params.c")).read())}
                  | {64, 85, 86, 87, 111, 114, 120, 123, 43, 44, 45, 46, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 79})
ALL16 = range(16)


def chaos(seed, eng):
    """the random part: (t, line) events, t in 50..CHAOS_MS"""
    rng = random.Random(seed * 7919 + (eng == "vibes"))
    ev = []
    held, t, pressed = [], 50, set()
    if eng == "vibes":
        ev.append((10, "midi 0xB0 79 0"))

    def ch():
        return 0 if rng.random() < 0.7 else rng.randrange(16)

    while t < CHAOS_MS:
        k = rng.random()
        if k < 0.25:                                                  # a note on (velocity 0 now and then: an off)
            n = rng.randint(21, 108) if rng.random() < 0.9 else rng.randrange(128)
            c = ch()
            ev.append((t, f"midi 0x{0x90 | c:02X} {n} {rng.randint(1, 127) if rng.random() < 0.95 else 0}"))
            held.append((n, c))
        elif k < 0.40 and held:                                       # an off (and some are never sent)
            n, c = held.pop(rng.randrange(len(held)))
            if rng.random() < 0.9:
                ev.append((t, f"midi 0x{0x80 | c:02X} {n} {rng.randrange(128)}"))
        elif k < 0.58:                                                # a CC, mostly one the firmware knows
            num = rng.choice(KNOWN_CC) if rng.random() < 0.7 else rng.randrange(128)
            if num == 119:
                num = 118                                             # (RAW: the developer's bypass, not fuzzed)
            if eng == "vibes" and num == 80:
                num = 79
            val = rng.randrange(128) if rng.random() < 0.95 else rng.randrange(128, 256)
            ev.append((t, f"midi 0x{0xB0 | ch():02X} {num} {val}"))
        elif k < 0.61:                                                # a program change
            p = rng.randrange(40) if rng.random() < 0.9 else rng.randrange(256)
            if eng == "vibes" and p < 12:
                p = 12 + p % 2
            ev.append((t, f"midi 0x{0xC0 | ch():02X} {p} 0"))
        elif k < 0.64:                                                # MIDI clock for a while, start / stop
            gap = rng.randint(8, 40)
            for i in range(rng.randint(5, 40)):
                ev.append((t + i * gap, "midi 0xF8 0 0"))
            ev.append((t + rng.randint(0, 200), f"midi 0x{rng.choice([0xFA, 0xFB, 0xFC]):02X} 0 0"))
        elif k < 0.67:                                                # SysEx bytes and garbage statuses
            st, d1 = rng.randrange(0x80, 0x100), rng.randrange(256)
            if st >> 4 == 0xB and d1 == 119:
                d1 = 118                                              # (not RAW: it skips the limiter by design)
            ev.append((t, f"midi 0x{st:02X} {d1} {rng.randrange(256)}"))
        elif k < 0.75:                                                # the keys
            m = 0
            for _ in range(rng.randint(0, 4)):
                m |= 1 << rng.randrange(27)
            ev.append((t, f"keys {m}"))
        elif k < 0.86:                                                # buttons: taps, and holds (SAVE, SEQ, REC ...)
            b = rng.randrange(14)
            if rng.random() < 0.6:
                ev.append((t, f"btn {b}"))
            elif b not in pressed:
                pressed.add(b)
                ev.append((t, f"press {b}"))
                ev.append((t + rng.randint(10, 1500), f"release {b}"))
        else:                                                         # the knobs
            d = rng.choice([-5, -3, -2, -1, 1, 1, 2, 3, 5])
            ev.append((t, f"turn {rng.randrange(7)} {d}"))
        t += rng.randint(1, 20)
    return [(min(tt, CHAOS_MS), line) for tt, line in ev]


def settle(eng):
    """the panic, then the probes; returns (lines, marks)"""
    t0 = CHAOS_MS + 50
    L = [(t0 - 17, "dump"), (t0 - 7, "dump")]                         # 40 ms after the last event (twice: a note-off
                                                                      # in flight for one block is not a stuck note)
    L += [(t0, "keys 0")] + [(t0, f"release {b}") for b in range(14)] + [(t0 + 2, "btn 8")]   # (HOME: no save mode)
    t = t0 + 5
    for c in ALL16:                                                   # transport, arp, sustain, bypass off; all off
        for num, v in ((64, 0), (60, 0), (63, 0), (45, 0), (52, 0), (111, 0), (123, 0)):
            L.append((t, f"midi 0x{0xB0 | c:02X} {num} {v}"))
        L.append((t, f"midi 0x{0xC0 | c:02X} {12 if eng == 'vibes' else 0} 0"))   # F13 / F01: the effects' tails go
        t += 1
    for c in ALL16:
        L.append((t0 + 1000 + c, f"midi 0x{0xB0 | c:02X} 123 0"))      # (a strum still starting notes)
        L.append((t0 + 1000 + c, f"midi 0x{0xC0 | c:02X} {12 if eng == 'vibes' else 0} 0"))   # (after a PRESETS
                                                                       # turn's pending load, 400 ms)
        L.append((t0 + 1500 + c, f"midi 0x{0xB0 | c:02X} 119 127"))    # RAW: the engine alone
    quiet = (t0 + 4000, t0 + 4500)                                    # silent (a damped note at RELEASE 10 still
    gone = t0 + 12000                                                 # fades below -90 dBFS for seconds: the voices
    L += [(gone, "dump")] + [(gone + 10 + c, f"midi 0x{0xB0 | c:02X} 119 0") for c in ALL16]   # end by 12 s)
    p0 = gone + 500                                                   # a note over MIDI, every channel
    L += [(p0 + c, f"midi 0x{0x90 | c:02X} 60 100") for c in ALL16] + [(p0 + 700 + c, f"midi 0x{0x80 | c:02X} 60 0") for c in ALL16]
    L += [(p0 + 800, "dump"), (p0 + 1000, "keys 128"), (p0 + 1500, "keys 0")]
    return L, dict(before=(t0 - 17, t0 - 7), quiet=quiet, gone=gone, probe=(p0 + 250, p0 + 650), key=(p0 + 1200, p0 + 1450), end=p0 + 2000, dump=p0 + 800)


def clicks(x):
    """tools/bar_measure.py clicks (hil_scan): second-difference spikes far above the local level"""
    d2 = np.abs(np.diff(x, 2))
    lvl = np.convolve(np.abs(x), np.ones(441) / 441, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def one(seed, eng, sanitized):
    lines, mk = settle(eng)
    ls = [f"{t} {l}" for t, l in chaos(seed, eng) + lines]
    tag = f"fz-{eng}-{seed}" + ("-san" if sanitized else "")
    r = run(tag, ls, mk["end"], binary=SAN if sanitized else BIN, check=False,
            env={"ASAN_OPTIONS": "detect_leaks=0", "UBSAN_OPTIONS": "print_stacktrace=0"})
    bad = []
    if r.code != 0:
        bad.append(f"exit {r.code}: {r.out[-600:]}")
    if "runtime error" in r.out or "AddressSanitizer" in r.out:
        bad.append("sanitizer: " + "; ".join(sorted(set(re.findall(r"\S+:\d+:\d+: runtime error: [^\n]*", r.out))))[:600]
                   + ("ASan" if "AddressSanitizer" in r.out else ""))
    if bad or not len(r.L):
        return tag, bad or ["no sound file"]
    x = np.maximum(np.abs(r.L), np.abs(r.R))
    if x[:int(mk["quiet"][0] / 1000 * FS)].max() > 24576:
        bad.append(f"over the limiter: {x.max():.0f} at {np.argmax(x) / FS:.3f} s")
    q = slice(int((mk["gone"] - 500) / 1000 * FS), int(mk["gone"] / 1000 * FS))
    if x[q].max() > 2:
        bad.append(f"not silent after the panic (RAW): peak {x[q].max():.0f}")
    b0, b1 = (r.state(t) for t in mk["before"])                     # a stuck note before the panic: a voice held
    if all(int(b["held"]) > int(b["pitches"]) for b in (b0, b1)):   # that no key, MIDI note or arp step holds
        bad.append(f"{b0['held']} voices held for {b0['pitches']} notes held (a stuck note before the panic)")
    st = r.state(mk["gone"])
    if st["held"] != "0":                                           # a stuck note: a voice still held down
        bad.append(f"{st['held']} voices still held after the panic")
    if st["srcs"] != "0" or st["refs"] != "0":
        bad.append(f"notes still held after the panic: srcs {st['srcs']} refs {st['refs']}")
    p = slice(int(mk["probe"][0] / 1000 * FS), int(mk["probe"][1] / 1000 * FS))
    if rms_db(r.L[p]) < -45:
        bad.append(f"a MIDI note after the panic: {rms_db(r.L[p]):.1f} dBFS")
    if clicks(r.L[p]):
        bad.append(f"{clicks(r.L[p])} clicks in the probe note")
    if x[p].max() > 24576:
        bad.append(f"the probe note near clipping: {x[p].max():.0f}")
    kv = int(r.state(mk["dump"])["card.2.3"])                       # KEY VEL
    kq = slice(int(mk["key"][0] / 1000 * FS), int(mk["key"][1] / 1000 * FS))
    if kv >= 20 and rms_db(r.L[kq]) < -55:
        bad.append(f"the C4 key (KEY VEL {kv}): {rms_db(r.L[kq]):.1f} dBFS")
    if r.state(mk["dump"])["drops"] != "0":                         # (the engine's ring and the backlog before it)
        bad.append(f"{r.state(mk['dump'])['drops']} note / pedal events dropped")
    if r.cpu_proxy_rh is not None and r.cpu_proxy_rh + 1700 > 5805:         # (program changes switch engines:
        bad.append(f"Rhodes CPU proxy {r.cpu_proxy_rh} + 1700 us over the block")   # each engine's own proxy)
    if r.cpu_proxy_bars is not None and r.cpu_proxy_bars > 4800:            # the effects are in the bars model
        bad.append(f"VIBES CPU proxy {r.cpu_proxy_bars} us over the bars gate (4800)")
    if not bad:                                                     # a passing seed keeps nothing (3.4 MB a WAV:
        for ext in (".wav", ".txt", "-1000.ppm", "-3000.ppm"):   # (the host's two screenshots too)                                # 10,000 seeds filled the disk, 2026-10-18)
            try:
                os.remove(os.path.join(OUT, tag) + ext)
            except OSError:
                pass
    return tag, bad


def seeds(request, default):
    n = request.config.getoption("--fuzz-seeds") or (default * 10 if request.config.getoption("--long") else default)
    return list(range(n))


@pytest.fixture(scope="module")
def sanitizer_build():
    return build(SAN, SAN_FLAGS)


@pytest.mark.parametrize("eng", ["rhodes", "vibes"])
def test_fuzz(eng, request, sanitizer_build):
    long = request.config.getoption("--long")
    jobs = [(s, eng, long or s % 5 == 0) for s in seeds(request, 200)]
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        results = list(ex.map(lambda a: one(*a), jobs))
    failed = [(tag, bad) for tag, bad in results if bad]
    msg = "\n".join(f"{tag} ({os.path.join(OUT, tag)}.txt): {'; '.join(bad)}" for tag, bad in failed[:12])
    assert not failed, f"{len(failed)} of {len(jobs)} seeds failed:\n{msg}"


HOT_CHORD = ["1309 turn 1 5", "1765 midi 0xB0 17 119", "1992 midi 0xBF 56 33", "2288 turn 1 5", "2473 turn 1 5",
             "2496 midi 0xB0 85 73", "2505 midi 0x9D 101 99", "2661 midi 0x9C 25 108"]


def test_chorus_hot_chord(sanitizer_build):
    """fuzz rhodes 2390-san (10,000 seeds, 2026-10-18; 2570 5580 5860 7690 7945 9215 the same), minimised: three
    TYPE turns, TREM 119, chord mode MIN6 (CC 56 on channel 16), the chorus on, two chords at v99 / v108: the
    chorus got more than 61856 (about 1.9 x full scale) and l * its gain (up to 34718) overflowed int32 (fx.c; on the
    chip it wraps: a full-scale spike of the wrong sign), as did (tl - l) * mix from 131072. The gain is now applied as
    l + (l * (gain - 1)) >> 15 (the same bits) and the mix's product is 64-bit"""
    r = run("chorus-hot-chord-san", HOT_CHORD, 4000, binary=SAN, check=False,
            env={"ASAN_OPTIONS": "detect_leaks=0", "UBSAN_OPTIONS": "print_stacktrace=0"})
    assert r.code == 0 and "runtime error" not in r.out, re.findall(r"\S+: runtime error: [^\n]*", r.out)[:2]


HOT_DELAY = ["808 btn 3", "1042 midi 0xC0 12 0", "1771 keys 48", "1783 midi 0xB0 106 96", "1962 midi 0xB2 113 108",
             "2104 turn 2 5", "2122 midi 0xB0 56 117", "2711 midi 0x98 102 125"]


def test_delay_hot_line(sanitizer_build):
    """fuzz vibes 4300-san (10,000 seeds, 2026-10-18), minimised: F13, the DELAY page and its K1 turned (TAPE), the
    compressor, chord mode MIN6, a v125 chord: a full-scale swing in the delay line made r - lp (its tone filter) and
    f (the feedback) reach 2^17, and their products with TONE / FEEDBACK overflowed int32 (fx.c, 124921 * 18800); fx.c
    mulq15w gives the same bits without the overflow"""
    r = run("delay-hot-line-san", HOT_DELAY, 4500, binary=SAN, check=False,
            env={"ASAN_OPTIONS": "detect_leaks=0", "UBSAN_OPTIONS": "print_stacktrace=0"})
    assert r.code == 0 and "runtime error" not in r.out, re.findall(r"\S+: runtime error: [^\n]*", r.out)[:2]


def test_fuzz_reaches_every_drive_type():
    """the default seeds send CC 84 into every band of the drive's TYPE (params.c DRV_CC84: OFF, FDRV, KHLON ...)
    on both engines, so the checks above run with each type on"""
    lo = [int(x) for x in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}",
                                    open(os.path.join(ROOT, "firmware", "src", "params.c")).read()).group(1).split(",")]
    for eng in ("rhodes", "vibes"):
        hit = set()
        for seed in range(200):
            for _, line in chaos(seed, eng):
                f = line.split()
                if f[0] == "midi" and int(f[1], 16) >> 4 == 0xB and f[2] == "84" and int(f[3]) < 128:
                    hit.add(max((t for t in range(len(lo)) if lo[t] <= int(f[3])), key=lambda t: lo[t]))
        assert hit == set(range(len(lo))), (eng, hit)
