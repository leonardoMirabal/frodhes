#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The two-core soak (FR_C1 builds: tools/build.py --c1) on the FM-1, over USB-MIDI with the USB audio stream open:
the worst case played for a long time while the firmware's own counters are read, the pass bars judged at the end.

    tools/hil_c1_soak.py --row 274|275|276|277|278 [--minutes M] [--rate 48000] [--out build/hil/c1soak] [--dry-run]
    tools/hil_c1_soak.py --selftest            (no device: the decoding and the verdict on made-up replies)
    tools/hil_c1_soak.py --status              (the FM-1: core 1's items once, CC 116 = 30 / 31; 32 the boot / crash)
    --no-audio          no USB audio stream: sounddevice / PortAudio never imported (a second FM-1 soaked while
                        another tool records the first unit: two PortAudio users deadlocked CoreAudio). The whole
                        ISR then has no USB tap / resampler in it: lower than with the stream (not the gate's number)
    --location LOC      every MIDI port of the unit at that USB location (ioreg / CoreMIDI locationID, e.g.
                        0x20130000) and no other, by CoreMIDI's USBLocationID: two units of one name told apart

Rows (Textura's bench/fm1 rows 274 / 275, Frodhes' worst case):
  274   30 min, the Rhodes: 14-note chords (mid + top, bass, spread) at v127 every 1.2 s with the pedal held and
        lifted every 20 s, every effect at its heaviest (the drive TSH8, SPREAD 127, the delay, the phaser, the
        filter LP18 + LFO, the compressor, the EQ, REVERB HALL at the full rate, DECAY / PRE-DELAY 127)
  275   2 h: as 274, the engine turned every 10 min (Rhodes, VIBES with the motor, MARIMBA) and the REVERB TYPE
        every 2 min (HALL, PLATE, INFINITE: core 1's heaviest), a preset-like burst of CCs every 30 s
  277   "storm", 6 min (or --minutes), the Rhodes: row 276's settings and play, with the REVERB TYPE INFINITE 20 s /
        HALL 5 s / PLATE 5 s and a Program Change burst at each switch to HALL (every 30 s: 276's moment at 340 s)
  278   as 277 without the Program Change bursts (the TYPE switches alone)
  276   "max", 30 min (or --minutes): the CPU smashed on both cores. The engine turned every 10 min (Rhodes, VIBES,
        MARIMBA, in a loop), all 14 voices sounding all the time: a 14-note chord at v127 every 0.25 s (mid + top,
        14 bass, spread: steals all the time), the pedal held, lifted every 5 s; every effect at its heaviest (as
        274) with the drive TYPE turned TSH8 / KHLON / TSH8 every 20 s; the full-rate REVERB at MIX, DECAY,
        PRE-DELAY and SIZE 127 all the time, INFINITE (the heaviest) most of it: in each engine's 10 min, 5 min of
        INFINITE alone, then each minute INFINITE 40 s, HALL 10 s, PLATE 10 s (the switches: the transitions); every 100 ms a step of a sweep on the cutoff, the delay TIME, the phaser and tremolo depths, the
        compressor's threshold and the drive's amount; the bypass (CC 111) on for 0.5 s every 20 s; a Program Change
        burst (presets 1-6) every 60 s, then the heavy settings again
(Boots: power-cycle the FM-1 and run --status after each boot: core 1 live, the rails 7 / 15.)

Every 5 s: CC 116 (the app's worst block, the whole ISR, voices shed, the USB counters) and CC 116 = 30 (core 1's
state, jobs, refusals, bad reads, read-again recoveries, late jobs, holds, its longest wait and busiest block, the
rails, its stack). The pass bars (all of them; a fallback to one core is a FAIL, never a pass):
  - core 1 live (item 30 = 1) at every read, the jobs counter only growing (a reset makes it drop: FAIL "reset"),
    the FM-1 answering every query (no hang)
  - 0 bad reads (34), 0 refusals (33), 0 late jobs (44), 0 holds (45), no fault of core 1 (40 = 0)
  - the rails at 7 / 15 (38 / 39)
  - core 0: the app's worst block <= 5000 us (CC 116 / 117) and the whole ISR <= 4500 us (CC 107 / 108; rows 276 /
    277: <= 4800 us, the user 2026-11-09), 0 voices shed (CC 115); the USB counters clean (underruns, missed,
    overruns 0)
  - core 1: its busiest block (46) < 5000 us, its stack (41) under 11 KB
Read-again recoveries (42 / 43) are information (counted, not a failure: Textura's units show them now and then).
Report: OUT/<row>-report.md + .json (every read). Exit 0 PASS, 1 FAIL, 2 INCOMPLETE (stopped early)."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "hil", "c1soak")
# CC 116 = 30: id -> name (hal/fm1_core1.h FR_C1_INFO_* + 30, frodhes.c midi_cc)
ITEMS = {30: "state", 31: "jobs_lo", 32: "jobs_hi", 33: "refused", 34: "bad", 35: "wait_us", 36: "vdc14_boot",
         37: "sysvdd_boot", 38: "vdc14", 39: "sysvdd", 40: "fault_vec", 41: "stack", 42: "rec1", 43: "rec0",
         44: "late", 45: "holds", 46: "busy_us", 47: "voices"}
STATE = {0: "never started", 1: "live", 2: "held: refusals too often", 3: "held: late job", 4: "held: no answer",
         5: "held: rails not raised", 6: "held: fault of core 1", 7: "parked", 8: "held: mailbox desync"}
FREC = ("reti", "rete", "retx", "rets", "sp", "psr", "emu_msg", "dbg_msg", "etm0", "etm1", "etm2", "etm3")
FREC_ID = 50
# CC 116 = 32: the last boot's reason and core 0's last crash (frodhes.c: 14 words, 3 items each, from item 86)
BOOT = ("p3_rst", "rst_src", "crashes", "vec", "pc", "rets", "sp", "emu", "dbg", "uptime_ms", "etm0", "etm1", "etm2",
        "etm3")
BOOT_ID = 86
CC = dict(DRIVE=81, DRV_SW=84, DRV_MIX=83, CMP_SW=106, CMP_AMT=107, TW_SW=17, TW_DEPTH=18, PH_SW=12, PH_DEPTH=13,
          PH_FDBK=16, CP_SW=85, CP_DEPTH=86, DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26, REV=91, RV_DECAY=92,
          RV_PREDLY=94, RV_TYPE=95, FL_TYPE=70, FL_FREQ=74, FL_RESO=71, FL_LFO=72, EQ_LO=34, EQ_MID=35, EQ_HI=37,
          SPREAD=105, USB_VOL=114, RV_SIZE=73)
HEAVY = dict(DRIVE=127, DRV_SW=76, DRV_MIX=64, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127,
             PH_DEPTH=127, PH_FDBK=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127, RV_DECAY=127,
             RV_PREDLY=127, RV_TYPE=32, FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64, EQ_LO=127, EQ_MID=0,
             EQ_HI=127, SPREAD=127, USB_VOL=127)          # DRV_SW 76: TSH8 (params.c DRV_CC84); RV_TYPE 32 HALL
REV_TYPES = (32, 64, 96)                                 # HALL, PLATE, INFINITE (CC 95 in bands of 32)
ENGINES = ((None, "rhodes"), (0, "vibes"), (127, "marimba"))   # CC 79: VIBES 0, MARIMBA 127 (the Rhodes: CC 80)
VOICINGS = dict(midtop=list(range(48, 60)) + [105, 108], bass=[28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48, 50, 52],
                spread=[28, 40, 52, 59, 64, 71, 76, 83, 88, 95, 100, 105, 35, 47])
ROWS = {"274": dict(minutes=30, engines=1, rev_every=0), "275": dict(minutes=120, engines=3, rev_every=120),
        "276": dict(minutes=30, engines=3, rev_every=0, max=1), "277": dict(minutes=6, engines=1, rev_every=0, max=1,
                                                                    storm=1),
        "278": dict(minutes=6, engines=1, rev_every=0, max=1, storm=2)}
def storm_rev(t):
    """rows 277 / 278: the REVERB TYPE at t s: each 30 s INFINITE 20 s, HALL 5 s, PLATE 5 s (277: a Program Change
    burst at each switch to HALL: row 276's moment at 340 s, where the 0.9.10 RC and the latency build reset)"""
    u = t % 30.0
    return 96 if u < 20.0 else 32 if u < 25.0 else 64


def max_rev(t):
    """row 276: the REVERB TYPE (CC 95) at t s: in each engine's 10 min, 5 min of INFINITE, then each minute
    INFINITE 40 s, HALL 10 s, PLATE 10 s"""
    u = t % 600.0
    if u < 300.0:
        return 96
    v = u % 60.0
    return 96 if v < 40.0 else 32 if v < 50.0 else 64


MAX_DRV = (76, 70, 76)                                   # row 276: the drive TYPE in turn (CC 84): TSH8, KHLON, TSH8
MAX_SWEEP = dict(FL_FREQ=(0, 127), DL_TIME=(60, 127), PH_DEPTH=(64, 127), TW_DEPTH=(64, 127), CMP_AMT=(60, 127),
                 DRIVE=(64, 127))                        # row 276: CC -> the sweep's range (a triangle, 100 ms steps)
BARS = dict(app_us=5000, whole_us=4500, c1_busy_us=5000, stack=11 * 1024, vdc14=7, sysvdd=15)
WHOLE_ROW = {"276": 4800, "277": 4800}                   # the stress rows' whole-ISR bar (the user, 2026-11-09: the
                                                         # two-core gate 4800; tools/hil_cpu.py stays at 4500)


def bars(row=None):
    return dict(BARS, whole_us=WHOLE_ROW.get(row, BARS["whole_us"]))


def decode(msgs):
    """CC (control, value) pairs of one or more replies -> {name: value} (the core-1 items, the record's words as
    "frec": {name: word}, and the profiler's app_us / now_us / shed / whole_us / tail_us, the USB counters)"""
    acc, part, bpart, cur, msb = {}, {}, {}, None, 0
    got = {}
    for c, v in msgs:
        if c == 100:
            cur = v
        elif c == 101:
            msb = v
        elif c == 102 and cur is not None:
            val = msb << 7 | v
            if cur in ITEMS:
                acc[ITEMS[cur]] = val
            elif FREC_ID <= cur < FREC_ID + 3 * len(FREC):
                part[divmod(cur - FREC_ID, 3)] = val
            elif BOOT_ID <= cur < BOOT_ID + 3 * len(BOOT):
                bpart[divmod(cur - BOOT_ID, 3)] = val
            elif cur in (1, 2, 3):
                acc[("underruns", "missed", "overruns")[cur - 1]] = val
        elif c in (107, 108, 109, 110, 115, 116, 117, 118):
            got[c] = v
    if "jobs_lo" in acc and "jobs_hi" in acc:
        acc["jobs"] = acc.pop("jobs_hi") << 14 | acc.pop("jobs_lo")
    words = {}
    for (w, p), val in part.items():
        words.setdefault(w, 0)
        words[w] |= val << (14 * p)
    if words:
        acc["frec"] = {FREC[w]: f"{x:#010x}" for w, x in sorted(words.items()) if w < len(FREC)}
    bw = {}
    for (w, p), val in bpart.items():
        bw[w] = bw.get(w, 0) | val << (14 * p)
    if bw:
        acc["boot"] = {BOOT[w]: f"{x:#010x}" for w, x in sorted(bw.items())}
    if {116, 117} <= got.keys():
        acc["app_us"] = (got[116] << 7 | got[117]) * 4
    if 115 in got:
        acc["shed"] = got[115]
    if {107, 108} <= got.keys():
        acc["whole_us"] = (got[108] << 7 | got[107]) * 4
    return acc


def judge(reads, row=None):
    """the reads (dicts from decode, in order, each with "t") -> (verdict, [reasons]); verdict PASS / FAIL; row: its
    bars (bars(row): the stress rows' whole ISR)"""
    B = bars(row)
    bad, jobs_last, shed0 = [], -1, None
    if not reads:
        return "FAIL", ["no read"]
    for r in reads:
        t = r.get("t", 0)
        if "settled" in r:                                # (the pause after a reset: no read)
            continue
        if r.get("boot"):
            b = r["boot"]
            bad.append(f"{t:.0f} s: after the reset: reset source {b.get('p3_rst')} (bit 2 watchdog, 6 soft), crash "
                       f"record {b}")
        if r.get("noreply"):
            bad.append(f"{t:.0f} s: no reply (hang or reset)")
            continue
        st = r.get("state")
        if st != 1:
            bad.append(f"{t:.0f} s: core 1 {STATE.get(st, st)}")
        j = r.get("jobs")
        if j is not None:
            if j < jobs_last:
                bad.append(f"{t:.0f} s: the jobs counter dropped {jobs_last} -> {j} (a reset)")
            jobs_last = j
        for k, lim in (("app_us", B["app_us"]), ("whole_us", B["whole_us"]), ("busy_us", B["c1_busy_us"]),
                       ("stack", B["stack"])):
            if r.get(k, 0) > lim:
                bad.append(f"{t:.0f} s: {k} {r[k]} > {lim}")
        for k in ("bad", "refused", "late", "holds", "fault_vec", "underruns", "missed", "overruns"):
            if r.get(k, 0):
                bad.append(f"{t:.0f} s: {k} {r[k]}")
        for k in ("vdc14", "sysvdd"):
            if k in r and r[k] < B[k]:
                bad.append(f"{t:.0f} s: {k} {r[k]} < {B[k]}")
        if "shed" in r:
            if shed0 is None:
                shed0 = r["shed"]
            elif r["shed"] != shed0:
                bad.append(f"{t:.0f} s: voices shed ({shed0} -> {r['shed']})")
    seen = []
    for b in bad:                                         # one line per kind (the first time it happened)
        k = b.split(": ", 1)[1].split(" ")[0]
        if k not in [s.split(": ", 1)[1].split(" ")[0] for s in seen]:
            seen.append(b)
    return ("PASS" if not bad else "FAIL"), seen


def selftest():
    ok = [(100, 30), (101, 0), (102, 1), (100, 31), (101, 1), (102, 2), (100, 32), (101, 0), (102, 3),
          (100, 34), (101, 0), (102, 0), (100, 38), (101, 0), (102, 7), (100, 39), (101, 0), (102, 15),
          (116, 7), (117, 0), (107, 0), (108, 7), (115, 0)]
    r = decode(ok)
    assert r["state"] == 1 and r["jobs"] == 3 << 14 | 130 and r["app_us"] == 7 * 128 * 4 and r["whole_us"] == 3584, r
    assert judge([dict(r, t=0), dict(r, t=5)]) == ("PASS", []), judge([r])
    assert judge([dict(r, t=0), dict(t=3, settled=36.0), dict(r, t=40)]) == ("PASS", [])
    r2 = dict(r, t=10, jobs=5, bad=1)
    v, why = judge([dict(r, t=0), r2])
    assert v == "FAIL" and any("dropped" in w for w in why) and any("bad 1" in w for w in why), why
    assert judge([dict(r, t=0, state=3)])[0] == "FAIL"
    assert judge([dict(r, t=0, whole_us=4600)])[0] == "FAIL"
    for row in ("276", "277"):                           # (the stress rows: 4800, the user 2026-11-09)
        assert judge([dict(r, t=0, whole_us=4800)], row)[0] == "PASS"
        assert judge([dict(r, t=0, whole_us=4804)], row)[0] == "FAIL"
        assert judge([dict(r, t=0, whole_us=4700, late=1)], row)[0] == "FAIL"
        assert judge([dict(r, t=0, whole_us=4700, holds=1)], row)[0] == "FAIL"
        assert judge([dict(r, t=0, whole_us=4700, shed=0), dict(r, t=5, whole_us=4700, shed=1)], row)[0] == "FAIL"
    assert judge([dict(r, t=0, whole_us=4600)], "274")[0] == "FAIL" and judge([dict(r, t=0, whole_us=4600)], "278")[0] == "FAIL"
    assert plan("277", None)["bars"]["whole_us"] == 4800 and plan("274", None)["bars"]["whole_us"] == 4500
    import inspect                                       # (--no-audio: sounddevice imported on the audio path only)
    src = inspect.getsource(run)
    assert src.count("import sounddevice") == 1 and "if not a.no_audio:\n        import sounddevice as sd" in src
    assert judge([dict(t=0, noreply=1)])[0] == "FAIL"
    f = decode([(100, 50), (101, 1), (102, 2), (100, 51), (101, 0), (102, 1), (100, 52), (101, 0), (102, 0)])
    assert f["frec"]["reti"] == f"{(1 << 7 | 2) | (1 << 14):#010x}", f
    b = decode([(100, 86), (101, 0), (102, 4), (100, 98), (101, 3), (102, 5), (100, 99), (101, 0), (102, 2)])
    assert b["boot"]["p3_rst"] == "0x00000004" and b["boot"]["pc"] == f"{(3 << 7 | 5) | (2 << 14):#010x}", b
    assert any("after the reset" in w for w in judge([dict(r, t=0), dict(r, t=5, jobs=1, boot=b["boot"])])[1])
    pm = plan("276", 30)
    assert pm["max"] and pm["chord_every_s"] == 0.25 and pm["engines"] == ["rhodes", "vibes", "marimba"], pm
    pe = per_engine(pm, [dict(r, t=10, app_us=4000), dict(r, t=700, app_us=4400), dict(r, t=1300, app_us=4100)])
    assert pe.startswith("rhodes 4000/") and "vibes 4400/" in pe and "marimba 4100/" in pe, pe
    assert [storm_rev(x) for x in (0, 19.9, 20, 24.9, 25, 29.9, 30)] == [96, 96, 32, 32, 64, 64, 96]
    ps = plan("277", None)
    assert ps["storm"] == 1 and ps["pc_every_s"] == 30 and ps["engines"] == ["rhodes"] and ps["minutes"] == 6, ps
    assert plan("278", None)["pc_every_s"] > 3600
    assert [max_rev(x) for x in (0, 299, 300, 339, 340, 349, 350, 359, 360, 600)] == [96, 96, 96, 96, 32, 32, 64,
                                                                                      64, 96, 96]
    print("hil_c1_soak selftest: ok")


def plan(row, minutes):
    p = ROWS[row]
    m = minutes or p["minutes"]
    mx = p.get("max", 0)
    return dict(row=row, minutes=m, engines=[e[1] for e in ENGINES[:p["engines"]]], rev_every_s=p["rev_every"],
                voices=14, chord_every_s=0.25 if mx else 1.2, pedal_every_s=5 if mx else 20, read_every_s=5,
                max=mx, drive_every_s=20 if mx else 0, sweep_every_s=0.1 if mx else 0, bypass_every_s=20 if mx else 0,
                pc_every_s=(30 if p.get("storm") == 1 else 10 ** 6 if p.get("storm") else 60) if mx else 0,
                storm=p.get("storm", 0),
                reverb="each engine's 10 min: INFINITE 5 min, then per minute INFINITE 40 s / HALL 10 s / PLATE 10 s; "
                       "MIX, DECAY, PRE-DELAY, SIZE 127" if mx else "", bars=bars(row))


def loc_ports(loc, name=None):
    """(mido, out, inp): the MIDI ports of the unit at USB location loc, none other (CoreMIDI's USBLocationID,
    fm1_ports.midi_at), opened by python-rtmidi index and read by polling (no Python callback on CoreMIDI's thread);
    refused unless it has exactly one source and one destination (any name: CoreMIDI keeps a unit's old USB name;
    name: containing it), the USB product there (ioreg) is an FM-1's, and the firmware's identity reply says FM-1"""
    import mido
    import rtmidi
    import fm1_midi
    import fm1_ports
    import midi_guard
    m = fm1_ports.midi_at(loc)
    if m is None:
        sys.exit(f"--location 0x{loc:08x}: no CoreMIDI on this computer")
    src, dst = m
    if len(src) != 1 or len(dst) != 1:
        sys.exit(f"--location 0x{loc:08x}: not one FM-1 there (MIDI sources {src}, destinations {dst})")
    import fm1_install
    devs = fm1_ports.usb_devices()
    usb = fm1_ports.fm1_usb_at(loc, devs)
    if usb is None:
        sys.exit(f"--location 0x{loc:08x}: the USB device there is {fm1_ports.usb_name_at(loc, devs)!r}, "
                 "not a Frodhes FM-1 (install Frodhes first)")
    for _, n in src + dst:
        if fm1_ports._never(n) or (name and name.lower() not in n.lower()):
            sys.exit(f"--location 0x{loc:08x}: its MIDI port is {n!r}" + (f", not {name!r}" if name else ""))

    class In:
        def __init__(self, i, n):
            self.m, self.p, self.name = rtmidi.MidiIn(), mido.Parser(), n
            self.m.ignore_types(sysex=False)       # (the identity reply)
            if n not in self.m.get_ports()[i]:
                sys.exit(f"--location 0x{loc:08x}: MIDI input {i} is {self.m.get_ports()[i]!r} now, not {n!r}")
            with midi_guard.watch(f"open {n!r} (input {i}, 0x{loc:08x})"):
                self.m.open_port(i)

        def iter_pending(self):
            while True:
                e = self.m.get_message()
                if not e:
                    break
                self.p.feed(e[0])
                while self.p.pending():
                    yield self.p.get_message()

    class Out:
        def __init__(self, i, n):
            self.m, self.name = rtmidi.MidiOut(), n
            if n not in self.m.get_ports()[i]:
                sys.exit(f"--location 0x{loc:08x}: MIDI output {i} is {self.m.get_ports()[i]!r} now, not {n!r}")
            with midi_guard.watch(f"open {n!r} (output {i}, 0x{loc:08x})"):
                self.m.open_port(i)

        def send(self, msg):
            self.m.send_message(msg.bytes())

    inp, o = In(*src[0]), Out(*dst[0])
    ident = None
    for _ in range(3):                                    # the firmware's own identity (as fm1_install --info)
        while inp.m.get_message():
            pass
        o.m.send_message(list(fm1_install.HS_QUERY))
        t0 = time.monotonic()
        while ident is None:
            e = inp.m.get_message()
            if e is None:
                if time.monotonic() - t0 >= 0.5:
                    break
                time.sleep(0.005)
            elif e[0] and e[0][0] == 0xF0:
                ident = fm1_install.parse_identity(bytes(e[0]))
        if ident is not None:
            break
    if ident is None or ident.loader or ident.model != "FM-1":
        inp.m.close_port()
        o.m.close_port()
        sys.exit(f"--location 0x{loc:08x}: the unit there answers "
                 + (repr(ident.text) if ident else "no identity") + ", not a running FM-1 firmware")
    out = fm1_midi.ChannelOut(o)
    print(f"{fm1_midi.say(out.ch)}; the unit at USB 0x{loc:08x} ({usb!r}, {ident.text}): input {src[0][0]} "
          f"{src[0][1]!r}, output {dst[0][0]}", flush=True)
    return mido, out, inp


def ports(a):
    """the run's MIDI ports: the unit at --location (loc_ports), else the usual FM-1 (hil_cpu.ports: --port / FM1_PORT)"""
    if a.location is not None:
        import fm1_ports
        return loc_ports(fm1_ports.parse_loc(a.location), a.port)
    import hil_cpu
    return hil_cpu.ports()


def run(a):  # noqa: C901 (the device run)
    mido, out, inp = ports(a)
    if not a.no_audio:
        import sounddevice as sd
        import fm1_ports

    def cc(c, v):
        out.send(mido.Message("control_change", control=c, value=int(v)))

    def note(n, v):
        out.send(mido.Message("note_on" if v else "note_off", note=n, velocity=v))

    def query(v):
        for _ in inp.iter_pending():
            pass
        cc(116, v)
        msgs, t0 = [], time.monotonic()
        while time.monotonic() - t0 < (0.5 if v == 127 else 1.2):
            for m in inp.iter_pending():
                if m.type == "control_change":
                    msgs.append((m.control, m.value))
            time.sleep(0.005)
        return msgs

    p = plan(a.row, a.minutes)
    p["audio"] = not a.no_audio
    stream = None
    if a.no_audio:                                       # (the whole ISR without the USB tap: lower than the gate's)
        print("--no-audio: no USB audio stream (sounddevice not imported); the whole ISR has no USB tap in it",
              flush=True)
    else:
        devs = fm1_ports.devices(sd.query_devices(), 2)
        if not devs:
            sys.exit("no FM-1 audio input")
        ca = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True)
        stream = sd.InputStream(device=devs[0], samplerate=a.rate, channels=2, dtype="int16", blocksize=128,
                                callback=lambda *x: None, extra_settings=ca)
        stream.start()
        time.sleep(1.0)
    first = decode(query(30))
    if first.get("state") is None:
        sys.exit("no core-1 reply (CC 116 = 30): not an FR_C1 build (tools/build.py --c1)")
    print(f"core 1 {STATE.get(first['state'], first['state'])}, rails {first.get('vdc14')} / {first.get('sysvdd')}, "
          f"voices {first.get('voices')}", flush=True)
    boot0 = decode(query(32)).get("boot")              # (the boot before the run: its reason, any crash record)
    print(f"boot before the run: {boot0}", flush=True)
    jobs_prev = first.get("jobs", -1)
    def setup(i):                                         # the engine i and every effect at its heaviest
        sel = ENGINES[i][0]
        if sel is None:
            cc(80, 0)                                     # the Rhodes (STAGE77)
        else:
            cc(79, sel)
            cc(47, 127)                                   # VIBES' motor (inert on MARIMBA)
        for n, v in HEAVY.items():
            if not (p["max"] and n == "RV_TYPE"):
                cc(CC[n], v)
        if p["max"]:
            cc(CC["RV_SIZE"], 127)                        # (row 276: the largest room, not AUTO)
            cc(CC["RV_TYPE"], rev_cur)
        cc(111, 0)
        cc(64, 127)

    reads, t0, held, k, eng_i, rev_i = [], time.monotonic(), [], 0, -1, 0
    drv_i = sw_i = 0
    rev_cur = 96
    next_read = next_chord = next_ped = next_rev = next_eng = next_burst = 0.0
    next_drv = next_sweep = 0.0
    next_byp, next_pc = 10.0, (20.0 if p.get("storm") == 1 else p["pc_every_s"] if p.get("storm") else 40.0)
    dur = p["minutes"] * 60.0
    try:
        while True:
            t = time.monotonic() - t0
            if t >= dur:
                break
            if t >= next_eng:
                eng_i = (eng_i + 1) % len(p["engines"])
                cc(120, 0)
                setup(eng_i)
                next_eng = t + (600.0 if len(p["engines"]) > 1 else dur + 1)
            if p["max"]:
                rv = storm_rev(t) if p.get("storm") else max_rev(t)
                if rv != rev_cur:                         # the REVERB TYPE (INFINITE most of the time)
                    cc(CC["RV_TYPE"], rv)
                    rev_cur = rv
                if t >= next_drv:                         # the drive TYPE in turn (KHLON among them)
                    drv_i += 1
                    cc(CC["DRV_SW"], MAX_DRV[drv_i % len(MAX_DRV)])
                    next_drv = t + p["drive_every_s"]
                if t >= next_sweep:                       # a step of each sweep (a triangle over its range)
                    sw_i += 1
                    for i, (n, (lo, hi)) in enumerate(MAX_SWEEP.items()):
                        span = hi - lo
                        x = (sw_i * 3 + i * 17) % (2 * span)
                        cc(CC[n], lo + (x if x <= span else 2 * span - x))
                    next_sweep = t + p["sweep_every_s"]
                if t >= next_byp:                         # the bypass for 0.5 s (the effects keep running)
                    cc(111, 127)
                    time.sleep(0.5)
                    cc(111, 0)
                    next_byp = t + p["bypass_every_s"]
                if t >= next_pc:                          # a Program Change burst, then the heavy settings again
                    for pc in range(1, 7):
                        out.send(mido.Message("program_change", program=pc))
                        time.sleep(0.04)
                    setup(eng_i)
                    cc(CC["DRV_SW"], MAX_DRV[drv_i % len(MAX_DRV)])
                    cc(CC["RV_TYPE"], rev_cur)
                    next_pc = t + p["pc_every_s"]
            if p["rev_every_s"] and t >= next_rev:
                cc(CC["RV_TYPE"], REV_TYPES[rev_i % 3])
                rev_i += 1
                next_rev = t + p["rev_every_s"]
            if t >= next_ped:
                cc(64, 0)
                time.sleep(0.12)
                cc(64, 127)
                next_ped = t + p["pedal_every_s"]
            if not p["max"] and len(p["engines"]) > 1 and t >= next_burst:
                for n in ("DL_TIME", "PH_DEPTH", "FL_FREQ", "CMP_AMT"):   # a preset-like burst of CCs, then back
                    cc(CC[n], (k * 37) % 128)
                for n in ("DL_TIME", "PH_DEPTH", "FL_FREQ", "CMP_AMT"):
                    cc(CC[n], HEAVY[n])
                next_burst = t + 30.0
            if t >= next_chord:
                for n in held:
                    note(n, 0)
                held = VOICINGS[("midtop", "bass", "spread")[k % 3]]
                for n in held:
                    note(n, 127)
                k += 1
                next_chord = t + p["chord_every_s"]
            if t >= next_read:
                r = decode(query(127) + query(30))
                r["t"] = round(t, 1)
                if r.get("state") is None:
                    r["noreply"] = 1
                elif r.get("jobs", jobs_prev) < jobs_prev:    # a reset: why (the watchdog, or core 0's crash record)
                    r["boot"] = decode(query(32)).get("boot")
                    print(f"RESET: {r['boot']}", flush=True)
                if r.get("jobs") is not None:
                    jobs_prev = r["jobs"]
                reads.append(r)
                print(f"{t:7.0f} s  core1 {r.get('state')} jobs {r.get('jobs')} bad {r.get('bad')} ref "
                      f"{r.get('refused')} late {r.get('late')} holds {r.get('holds')} rec {r.get('rec1')}/"
                      f"{r.get('rec0')}  app {r.get('app_us')} whole {r.get('whole_us')} c1busy {r.get('busy_us')} "
                      f"wait {r.get('wait_us')} shed {r.get('shed')}", flush=True)
                next_read = t + p["read_every_s"]
                if len(reads) % 120 == 0:            # every 10 min: the report so far
                    write_report(a, p, reads, {}, False, interim=True)
                if r.get("noreply") and a.stop_on_fail:
                    break
                if (r.get("noreply") or r.get("boot")) and a.settle_s > 0:
                    # a reset (or no answer: one coming): no stimulus until the FM-1 has been up settle_s, past the
                    # boot guard's 30 s (platform.c: a reset before 30 s up counts as a failed boot; two in a row
                    # escalate to USB RESCUE, a third to the ROM's updater): the test must not drive it there
                    for n in held:
                        note(n, 0)
                    held = []
                    cc(64, 0)
                    cc(120, 0)
                    up, tw = None, time.monotonic()
                    while up is None and time.monotonic() - tw < 180.0:
                        r2 = decode(query(30))
                        if r2.get("state") is not None:
                            up = time.monotonic()
                            if r2.get("jobs") is not None:
                                jobs_prev = r2["jobs"]
                        else:
                            time.sleep(1.0)
                    if up is None:
                        print(f"{t:7.0f} s  no answer for 180 s after the reset: stopping", flush=True)
                        break
                    print(f"{t:7.0f} s  a reset: the stimulus paused {a.settle_s:.0f} s (the boot guard)", flush=True)
                    while time.monotonic() - up < a.settle_s:
                        time.sleep(0.5)
                    r2 = decode(query(30))
                    if r2.get("jobs") is not None:
                        jobs_prev = r2["jobs"]
                    reads.append(dict(t=round(time.monotonic() - t0, 1), settled=round(time.monotonic() - tw, 1)))
                    setup(eng_i)
                    if p["max"]:
                        cc(CC["DRV_SW"], MAX_DRV[drv_i % len(MAX_DRV)])
                        cc(CC["RV_TYPE"], rev_cur)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        for n in held:
            note(n, 0)
        cc(64, 0)
        cc(120, 0)
    rec = decode(query(31))
    complete = time.monotonic() - t0 >= dur - 1
    verdict = write_report(a, p, reads, rec, complete)
    code = 0 if verdict == "PASS" and complete else (1 if verdict == "FAIL" else 2)
    if stream is not None:
        close_stream(stream, code)           # (after the report: CoreAudio's stop can hang on close)
    return code


def per_engine(p, reads):
    """the worst app / whole / core-1 busy / wait per engine (the engine turned every 10 min from t 0)"""
    out = []
    for i, e in enumerate(p["engines"]):
        rs = [r for r in reads if len(p["engines"]) == 1 or int(r.get("t", 0) // 600) % len(p["engines"]) == i]
        if rs:
            out.append(e + " " + "/".join(str(max(r.get(k, 0) for r in rs)) for k in ("app_us", "whole_us", "busy_us",
                                                                                      "wait_us")))
    return "; ".join(out) + " (app / whole / c1 busy / wait, us)"


def write_report(a, p, reads, rec, complete, interim=False):
    """OUT/<row>-report.md + .json; interim (every 10 min of a run): the reads so far, no fault-record query"""
    verdict, why = judge(reads, a.row)
    if rec.get("frec"):
        verdict, why = "FAIL", why + [f"core 1's fault record: {rec['frec']}"]
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"{a.row}-report")
    json.dump(dict(plan=p, reads=reads, frec=rec.get("frec"), verdict=verdict, why=why, complete=complete),
              open(base + ".json", "w"), indent=1)
    worst = {k: max((r.get(k, 0) for r in reads), default=None) for k in ("app_us", "whole_us", "busy_us", "wait_us",
                                                                           "stack")}
    last = reads[-1] if reads else {}
    with open(base + ".md", "w") as f:
        state = f"RUNNING ({verdict} so far)" if interim else (verdict if complete else "INCOMPLETE")
        f.write(f"# Two-core soak, row {a.row}: {state}\n\n")
        f.write(f"- {p['minutes']} min, engines {', '.join(p['engines'])}, {len(reads)} reads, whole-ISR bar "
                f"{bars(a.row)['whole_us']} us" + ("" if p.get("audio", True) else ", NO USB AUDIO (--no-audio: the "
                "whole ISR without the USB tap; not the gate's number)") + "\n")
        f.write(f"- worst per engine: {per_engine(p, reads)}\n")
        f.write(f"- worst: {', '.join(f'{k} {v}' for k, v in worst.items())}\n")
        f.write(f"- last: jobs {last.get('jobs')}, bad {last.get('bad')}, refused {last.get('refused')}, late "
                f"{last.get('late')}, holds {last.get('holds')}, recoveries {last.get('rec1')} / {last.get('rec0')}\n")
        for w in why:
            f.write(f"- FAIL: {w}\n")
    if not interim:
        print(open(base + ".md").read(), flush=True)
    return verdict


def close_stream(stream, code, secs=15.0):
    """stream.stop() + close() in a thread; a hang past secs is the computer's (AudioOutputUnitStop), not the FM-1's:
    said so, and the process ends with the run's own code"""
    import threading
    th = threading.Thread(target=lambda: (stream.stop(), stream.close()), daemon=True)
    th.start()
    th.join(secs)
    if th.is_alive():
        print(f"hil_c1_soak: the USB audio stream did not close in {secs:.0f} s (a CoreAudio hang on this computer, "
              "not the FM-1): exiting with the run's verdict", flush=True)
        os._exit(code)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--row", choices=sorted(ROWS), default="274")
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--settle-s", type=float, default=35.0, help="after a reset: no stimulus until the FM-1 has been up this long (the boot guard counts a reset before 30 s up as a failed boot; 0: off)")
    ap.add_argument("--stop-on-fail", action="store_true")
    ap.add_argument("--port", metavar="NAME", help="only the unit whose MIDI port / audio device name contains NAME "
                    "(a second FM-1 with its own USB name, tools/build.py --usb-name); refused when two USB "
                    "devices share the name")
    ap.add_argument("--location", metavar="LOC", help="the unit's USB location (macOS ioreg / CoreMIDI locationID, "
                    "e.g. 0x20130000): every MIDI port opened is that unit's (two units of one name told apart); "
                    "refused when no FM-1 is there")
    ap.add_argument("--no-audio", action="store_true", help="no USB audio stream: sounddevice / PortAudio never "
                    "imported (the whole ISR then has no USB tap in it: lower than the gate's number)")
    a = ap.parse_args()
    if a.port and a.location is None:
        import fm1_ports
        fm1_ports.force(a.port, None if a.location is None else fm1_ports.parse_loc(a.location))
    if a.selftest:
        selftest()
        return 0
    if a.dry_run:
        print(json.dumps(dict(plan(a.row, a.minutes), audio=not a.no_audio, location=a.location), indent=1))
        return 0
    if a.status:
        mido, out, inp = ports(a)
        for _ in inp.iter_pending():
            pass
        msgs = []
        for v in (30, 31, 32):                            # (32: the last boot's reason, core 0's crash record)
            out.send(mido.Message("control_change", control=116, value=v))
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.2:
                msgs += [(m.control, m.value) for m in inp.iter_pending() if m.type == "control_change"]
                time.sleep(0.005)
        r = decode(msgs)
        print(json.dumps(dict(r, state_text=STATE.get(r.get("state"), "no reply (not an FR_C1 build?)")), indent=1))
        return 0 if r.get("state") == 1 else 1
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
