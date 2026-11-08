#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The two-core soak (FR_C1 builds: tools/build.py --c1) on the FM-1, over USB-MIDI with the USB audio stream open:
the worst case played for a long time while the firmware's own counters are read, the pass bars judged at the end.

    tools/hil_c1_soak.py --row 274|275 [--minutes M] [--rate 48000] [--out build/hil/c1soak] [--dry-run]
    tools/hil_c1_soak.py --selftest            (no device: the decoding and the verdict on made-up replies)
    tools/hil_c1_soak.py --status              (the FM-1: core 1's items once, CC 116 = 30 / 31)

Rows (Textura's bench/fm1 rows 274 / 275, Frodhes' worst case):
  274   30 min, the Rhodes: 14-note chords (mid + top, bass, spread) at v127 every 1.2 s with the pedal held and
        lifted every 20 s, every effect at its heaviest (the drive TSH8, SPREAD 127, the delay, the phaser, the
        filter LP18 + LFO, the compressor, the EQ, REVERB HALL at the full rate, DECAY / PRE-DELAY 127)
  275   2 h: as 274, the engine turned every 10 min (Rhodes, VIBES with the motor, MARIMBA) and the REVERB TYPE
        every 2 min (HALL, PLATE, INFINITE: core 1's heaviest), a preset-like burst of CCs every 30 s
(Boots: power-cycle the FM-1 and run --status after each boot: core 1 live, the rails 7 / 15.)

Every 5 s: CC 116 (the app's worst block, the whole ISR, voices shed, the USB counters) and CC 116 = 30 (core 1's
state, jobs, refusals, bad reads, read-again recoveries, late jobs, holds, its longest wait and busiest block, the
rails, its stack). The pass bars (all of them; a fallback to one core is a FAIL, never a pass):
  - core 1 live (item 30 = 1) at every read, the jobs counter only growing (a reset makes it drop: FAIL "reset"),
    the FM-1 answering every query (no hang)
  - 0 bad reads (34), 0 refusals (33), 0 late jobs (44), 0 holds (45), no fault of core 1 (40 = 0)
  - the rails at 7 / 15 (38 / 39)
  - core 0: the app's worst block <= 5000 us (CC 116 / 117) and the whole ISR <= 4500 us (CC 107 / 108), 0 voices
    shed (CC 115); the USB counters clean (underruns, missed, overruns 0)
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
CC = dict(DRIVE=81, DRV_SW=84, DRV_MIX=83, CMP_SW=106, CMP_AMT=107, TW_SW=17, TW_DEPTH=18, PH_SW=12, PH_DEPTH=13,
          PH_FDBK=16, CP_SW=85, CP_DEPTH=86, DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26, REV=91, RV_DECAY=92,
          RV_PREDLY=94, RV_TYPE=95, FL_TYPE=70, FL_FREQ=74, FL_RESO=71, FL_LFO=72, EQ_LO=34, EQ_MID=35, EQ_HI=37,
          SPREAD=105, USB_VOL=114)
HEAVY = dict(DRIVE=127, DRV_SW=76, DRV_MIX=64, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127,
             PH_DEPTH=127, PH_FDBK=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127, RV_DECAY=127,
             RV_PREDLY=127, RV_TYPE=32, FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64, EQ_LO=127, EQ_MID=0,
             EQ_HI=127, SPREAD=127, USB_VOL=127)          # DRV_SW 76: TSH8 (params.c DRV_CC84); RV_TYPE 32 HALL
REV_TYPES = (32, 64, 96)                                 # HALL, PLATE, INFINITE (CC 95 in bands of 32)
ENGINES = ((None, "rhodes"), (0, "vibes"), (127, "marimba"))   # CC 79: VIBES 0, MARIMBA 127 (the Rhodes: CC 80)
VOICINGS = dict(midtop=list(range(48, 60)) + [105, 108], bass=[28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48, 50, 52],
                spread=[28, 40, 52, 59, 64, 71, 76, 83, 88, 95, 100, 105, 35, 47])
ROWS = {"274": dict(minutes=30, engines=1, rev_every=0), "275": dict(minutes=120, engines=3, rev_every=120)}
BARS = dict(app_us=5000, whole_us=4500, c1_busy_us=5000, stack=11 * 1024, vdc14=7, sysvdd=15)


def decode(msgs):
    """CC (control, value) pairs of one or more replies -> {name: value} (the core-1 items, the record's words as
    "frec": {name: word}, and the profiler's app_us / now_us / shed / whole_us / tail_us, the USB counters)"""
    acc, part, cur, msb = {}, {}, None, 0
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
    if {116, 117} <= got.keys():
        acc["app_us"] = (got[116] << 7 | got[117]) * 4
    if 115 in got:
        acc["shed"] = got[115]
    if {107, 108} <= got.keys():
        acc["whole_us"] = (got[108] << 7 | got[107]) * 4
    return acc


def judge(reads):
    """the reads (dicts from decode, in order, each with "t") -> (verdict, [reasons]); verdict PASS / FAIL"""
    bad, jobs_last, shed0 = [], -1, None
    if not reads:
        return "FAIL", ["no read"]
    for r in reads:
        t = r.get("t", 0)
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
        for k, lim in (("app_us", BARS["app_us"]), ("whole_us", BARS["whole_us"]), ("busy_us", BARS["c1_busy_us"]),
                       ("stack", BARS["stack"])):
            if r.get(k, 0) > lim:
                bad.append(f"{t:.0f} s: {k} {r[k]} > {lim}")
        for k in ("bad", "refused", "late", "holds", "fault_vec", "underruns", "missed", "overruns"):
            if r.get(k, 0):
                bad.append(f"{t:.0f} s: {k} {r[k]}")
        for k in ("vdc14", "sysvdd"):
            if k in r and r[k] < BARS[k]:
                bad.append(f"{t:.0f} s: {k} {r[k]} < {BARS[k]}")
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
    r2 = dict(r, t=10, jobs=5, bad=1)
    v, why = judge([dict(r, t=0), r2])
    assert v == "FAIL" and any("dropped" in w for w in why) and any("bad 1" in w for w in why), why
    assert judge([dict(r, t=0, state=3)])[0] == "FAIL"
    assert judge([dict(r, t=0, whole_us=4600)])[0] == "FAIL"
    assert judge([dict(t=0, noreply=1)])[0] == "FAIL"
    f = decode([(100, 50), (101, 1), (102, 2), (100, 51), (101, 0), (102, 1), (100, 52), (101, 0), (102, 0)])
    assert f["frec"]["reti"] == f"{(1 << 7 | 2) | (1 << 14):#010x}", f
    print("hil_c1_soak selftest: ok")


def plan(row, minutes):
    p = ROWS[row]
    m = minutes or p["minutes"]
    return dict(row=row, minutes=m, engines=[e[1] for e in ENGINES[:p["engines"]]], rev_every_s=p["rev_every"],
                voices=14, chord_every_s=1.2, pedal_every_s=20, read_every_s=5, bars=BARS)


def run(a):  # noqa: C901 (the device run)
    import hil_cpu
    mido, out, inp = hil_cpu.ports()
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
    reads, t0, held, k, eng_i, rev_i = [], time.monotonic(), [], 0, -1, 0
    next_read = next_chord = next_ped = next_rev = next_eng = next_burst = 0.0
    dur = p["minutes"] * 60.0
    try:
        while True:
            t = time.monotonic() - t0
            if t >= dur:
                break
            if t >= next_eng:
                eng_i = (eng_i + 1) % len(p["engines"])
                sel = ENGINES[eng_i][0]
                cc(120, 0)
                if sel is None:
                    cc(80, 0)                             # the Rhodes (STAGE77)
                else:
                    cc(79, sel)
                    cc(47, 127)                           # VIBES' motor (inert on MARIMBA)
                for n, v in HEAVY.items():
                    cc(CC[n], v)
                cc(64, 127)
                next_eng = t + (600.0 if len(p["engines"]) > 1 else dur + 1)
            if p["rev_every_s"] and t >= next_rev:
                cc(CC["RV_TYPE"], REV_TYPES[rev_i % 3])
                rev_i += 1
                next_rev = t + p["rev_every_s"]
            if t >= next_ped:
                cc(64, 0)
                time.sleep(0.12)
                cc(64, 127)
                next_ped = t + p["pedal_every_s"]
            if p["engines"] and len(p["engines"]) > 1 and t >= next_burst:
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
                reads.append(r)
                print(f"{t:7.0f} s  core1 {r.get('state')} jobs {r.get('jobs')} bad {r.get('bad')} ref "
                      f"{r.get('refused')} late {r.get('late')} holds {r.get('holds')} rec {r.get('rec1')}/"
                      f"{r.get('rec0')}  app {r.get('app_us')} whole {r.get('whole_us')} c1busy {r.get('busy_us')} "
                      f"wait {r.get('wait_us')} shed {r.get('shed')}", flush=True)
                next_read = t + p["read_every_s"]
                if r.get("noreply") and a.stop_on_fail:
                    break
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        for n in held:
            note(n, 0)
        cc(64, 0)
        cc(120, 0)
        stream.stop()
        stream.close()
    rec = decode(query(31))
    complete = time.monotonic() - t0 >= dur - 1
    verdict, why = judge(reads)
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
        f.write(f"# Two-core soak, row {a.row}: {verdict if complete else 'INCOMPLETE'}\n\n")
        f.write(f"- {p['minutes']} min, engines {', '.join(p['engines'])}, {len(reads)} reads\n")
        f.write(f"- worst: {', '.join(f'{k} {v}' for k, v in worst.items())}\n")
        f.write(f"- last: jobs {last.get('jobs')}, bad {last.get('bad')}, refused {last.get('refused')}, late "
                f"{last.get('late')}, holds {last.get('holds')}, recoveries {last.get('rec1')} / {last.get('rec0')}\n")
        for w in why:
            f.write(f"- FAIL: {w}\n")
    print(open(base + ".md").read())
    return 0 if verdict == "PASS" and complete else (1 if verdict == "FAIL" else 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--row", choices=sorted(ROWS), default="274")
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop-on-fail", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if a.dry_run:
        print(json.dumps(plan(a.row, a.minutes), indent=1))
        return 0
    if a.status:
        import hil_cpu
        mido, out, inp = hil_cpu.ports()
        for _ in inp.iter_pending():
            pass
        msgs = []
        for v in (30, 31):
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
