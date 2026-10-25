#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The monkey on the FM-1: tools/monkey.py's seeded random stream, its MIDI part, sent over USB-MIDI for N minutes
while the FM-1's USB audio is recorded, with the device's own glitch detector, its counters and a liveness probe.

    tools/hil_monkey.py [--minutes 10] [--seed N] [--rate 48000|44100] [--out build/hil/monkey] [--dry-run]

The stream: segments of 20 s, each one monkey seed (seed, seed + 1, ...): monkey.gen's MIDI lines only (notes on any
channel, chords, fast repeats, lost note-offs, the pedal, bend, the mod wheel, every CC, Program Change, MIDI clock
at a random tempo, Start / Stop / Continue / Song Position). The panel's keys, knobs and buttons have no MIDI path
and are left out, so are SysEx and undefined statuses (mido sends only valid messages) and the developer CCs that
change the test itself (DEV_CC: the CPU query 116, RAW 119, the key-velocity test 41, USB VOL 114, the reset 10,
the battery / screen / card-design ones 2, 3, 4, 8, 11, the mode cap 9, 42). USB VOL is held FIXED (CC 114 127), so
USB is the output before MASTER.

Each segment, one take (USB recorded at --rate, the device switched to it, as hil_must S1): the stream, then the
panic (all notes off, all sound off, pedal up, reset controllers, MIDI Stop on all 16 channels), 2 s, then the probe:
FX bypassed (CC 111 127) and VIBES' motor off (CC 47 0) on every channel, note 60 v100 on every channel for 0.6 s,
then the bypass off again. The gates, per take:
  counters  CC 116 every 0.5 s while it plays (tools/hil_must.py Poller, judged by hil_must.gate_counters): the
            replies come, the USB stream flows at --rate, underruns = missed = overruns = 0, the ring's low point
            >= one packet, the whole ISR <= 4500 us (hil_cpu's CC 116 / 117 and 107 / 108), 0 voices shed
  glitches  hil_crackle.usb_judge on the USB take (stress_max's repeat and stale detectors, the one definition the
            release judges USB audio with): 0 events. Full scale on USB (|x| >= 32767; the limiter keeps 24000):
            0 samples. Clicks are not gated: a random stream makes the parameter changes 0.9.5 still clicks on
            (private/BUGHUNT-095.md); hil_crackle's click detector counts them as information
  alive     the FM-1 answers a CC 116 query after the probe; the probe sounds on USB (> -50 dBFS, judged from the
            recorder's mark); the stream kept running (the frames recorded >= 98 % of the take's length)
Any FAIL stops the run after that take (the take is saved: OUT/fail-<seed>.npz with USB, the events and the
counters; the seed replays the stream on the host: python3 tools/monkey.py --seeds N).

--dry-run: the plan (segments, seeds, messages per kind, the time) and a check of every message (mido builds it,
no DEV_CC); no MIDI, no audio. The main session runs it on the FM-1 (USB only: the jack is not recorded here).
Report: OUT/report.md + report.json."""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monkey  # noqa: E402

ROOT = monkey.ROOT
OUT = os.path.join(ROOT, "build", "hil", "monkey")
SEG_S = monkey.RAND_MS / 1000.0
DEV_CC = {2, 3, 4, 8, 9, 10, 11, 41, 42, 114, 116, 119}
REALTIME = {0xF8, 0xFA, 0xFB, 0xFC}
PANIC_S, PROBE_AT, PROBE_LEN, TAIL_S = 0.3, 2.3, 0.6, 1.6
PROBE_DB = -50.0
CLIP = 32767


def midi_of(line):
    """a monkey stream line -> the MIDI bytes to send, or None (the panel, a developer CC, SysEx / undefined)"""
    w = line.split()
    if w[0] != "midi":
        return None
    st, d1, d2 = int(w[1], 16), int(w[2]) & 127, int(w[3]) & 127
    hi = st >> 4
    if st in REALTIME:
        return [st]
    if st == 0xF2:
        return [st, d1, d2]
    if st >= 0xF0:
        return None
    if hi == 0xB and d1 in DEV_CC:
        return None
    if hi in (0xC, 0xD):
        return [st, d1]
    return [st, d1, d2]


def segment(seed):
    """one seed's MIDI part: [(t s, bytes)] sorted, t in 0..SEG_S"""
    ev = []
    for t, line in monkey.gen(seed):
        b = midi_of(line)
        if b is not None:
            ev.append((t / 1000.0, b))
    return sorted(ev, key=lambda e: e[0])


def panic_msgs():
    out = []
    for c in range(16):
        out += [[0xB0 | c, 64, 0], [0xB0 | c, 123, 0], [0xB0 | c, 120, 0], [0xB0 | c, 121, 0]]
    return out + [[0xFC]]


def probe_msgs(on):
    """the probe's set-up (on) or its end: FX bypass and the motor on every channel, the note on / off"""
    if on:
        return ([[0xB0 | c, 111, 127] for c in range(16)] + [[0xB0 | c, 47, 0] for c in range(16)])
    return [[0xB0 | c, 111, 0] for c in range(16)]


def judge_events(seg):
    """the take's events in hil_crackle's form ((t, kind, a, b): the note-ons and the CCs place its guards)"""
    out = []
    for t, b in seg:
        hi = b[0] >> 4
        if hi == 0x9 and len(b) == 3 and b[2]:
            out.append((t, "on", b[1], b[2]))
        elif hi in (0x8, 0x9):
            out.append((t, "off", b[1], 0))
        elif hi == 0xB:
            out.append((t, "cc", b[1], b[2]))
    t = SEG_S + PROBE_AT + 0.05
    out.append((t, "on", 60, 100))
    out.append((t + PROBE_LEN, "off", 60, 0))
    return out


def take_len():
    return SEG_S + PROBE_AT + PROBE_LEN + 0.05 + TAIL_S


def plan(minutes, seed):
    n = max(1, int(np.ceil(minutes * 60 / take_len())))
    return [seed + k for k in range(n)]


def kinds(seg):
    names = {0x8: "note off", 0x9: "note on", 0xA: "poly AT", 0xB: "CC", 0xC: "program", 0xD: "chan AT",
             0xE: "bend"}
    c = {}
    for _, b in seg:
        k = names.get(b[0] >> 4, {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop",
                                  0xF2: "song position"}.get(b[0], "?"))
        c[k] = c.get(k, 0) + 1
    return c


def dry_run(a):
    import mido
    seeds = plan(a.minutes, a.seed)
    tot, bad = {}, []
    for s in seeds:
        seg = segment(s)
        for t, b in seg:
            try:
                mido.Message.from_bytes(b)
            except Exception as e:
                bad.append((s, t, b, str(e)))
            if b[0] >> 4 == 0xB and b[1] in DEV_CC:
                bad.append((s, t, b, "a developer CC"))
            if not 0 <= t <= SEG_S:
                bad.append((s, t, b, "outside the segment"))
        for k, v in kinds(seg).items():
            tot[k] = tot.get(k, 0) + v
        print(f"seed {s}: {len(seg)} messages, " + ", ".join(f"{k} {v}" for k, v in sorted(kinds(seg).items())))
    for m in panic_msgs() + probe_msgs(True) + probe_msgs(False):
        mido.Message.from_bytes(m)
    print(f"\n{len(seeds)} takes of {take_len():.1f} s at {a.rate / 1000:g} kHz: {len(seeds) * take_len() / 60:.1f} min"
          f" (+ ~3 s set-up per take); {sum(tot.values())} messages: "
          + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())))
    print("gates per take: counters (hil_must.gate_counters), USB repeat / stale = 0 (hil_crackle.usb_judge), "
          "full scale = 0, alive (CC 116 answers, the probe > -50 dBFS, frames >= 98 %)")
    if bad:
        print(f"BAD messages: {len(bad)}; the first: {bad[:3]}")
        return 1
    print("every message valid; no developer CC; no MIDI or audio opened (--dry-run)")
    return 0


def judge_audio(xi, rate, ev, flags=0):
    """the audio gates of one take: xi (n, 2) int LSB at rate, ev judge_events'; [(check, value, criterion,
    status)]. The probe is found on the recording's own clock (hil_crackle's clock fit from the onsets)"""
    import hil_crackle as hc
    rows = []
    gl, fit = hc.usb_judge(xi, rate, ev)
    rows.append(("USB repeat / stale (hil_crackle.usb_judge)", len(gl), "0", "ok" if not gl else "FAIL"))
    if gl:
        rows.append(("the first glitches", "; ".join(f"{t:.3f} s {k}: {d}" for t, k, d in gl[:3]), "information",
                     "info"))
    nclip = int((np.abs(xi) >= CLIP).sum())
    rows.append(("USB at full scale (samples)", nclip, "0", "ok" if not nclip else "FAIL"))
    p0 = fit["lag"] + (SEG_S + PROBE_AT + 0.05) * (1 + fit["rate"])     # (lag: from the recording's first frame)
    w = xi[max(0, int(p0 * rate)):max(0, int((p0 + PROBE_LEN) * rate))]
    pdb = monkey.rms_db(w) if len(w) else -200.0
    rows.append(("the probe note on USB (dBFS)", f"{pdb:.1f}", f"> {PROBE_DB:g}", "ok" if pdb > PROBE_DB else "FAIL"))
    need = int(0.98 * take_len() * rate)
    rows.append(("frames recorded", f"{len(xi)} of {int(take_len() * rate)}", ">= 98 %",
                 "ok" if len(xi) >= need and not flags else "FAIL"))
    if flags:
        rows.append(("the recorder's status flags", flags, "0 (a RIG problem, not the firmware)", "info"))
    try:
        ck = hc.det_click(xi, rate, hc._guard_mask(len(xi), rate, hc.guards(ev, (), clock=(fit["lag"], fit["rate"])),
                                                   0.0))
        rows.append(("clicks (hil_crackle det_click)", len(ck), "information (0.9.5's known clicks)", "info"))
    except Exception as e:                          # (information only: never stop the run on it)
        rows.append(("clicks (hil_crackle det_click)", f"not judged: {e!r}"[:120], "information", "info"))
    rows.append(("the clock", f"lag {fit['lag']:.3f} s, {1e6 * fit['rate']:.0f} ppm, {fit.get('how', '')}",
                 "information", "info"))
    return rows


def host_take(seed, lead_ms=500):
    """the host build plays one take as the FM-1 would get it (the stream, the panic, the probe): (xi (n, 2) at
    44.1 kHz, ev); lead_ms of silence first, as a recorder opened before the first message"""
    seg = segment(seed)
    lines = [(lead_ms + int(round(1000 * t)), "midi " + " ".join(f"0x{v:02X}" if k == 0 else str(v) for k, v in
                                                                 enumerate((b + [0, 0])[:3]))) for t, b in seg]

    def at(t, msgs):
        lines.extend((lead_ms + int(round(1000 * t)), "midi " + " ".join(f"0x{v:02X}" if k == 0 else str(v)
                                                                          for k, v in enumerate((m + [0, 0])[:3])))
                     for m in msgs)
    at(SEG_S + PANIC_S, panic_msgs())
    at(SEG_S + PROBE_AT, probe_msgs(True))
    at(SEG_S + PROBE_AT + 0.05, [[0x90 | c, 60, 100] for c in range(16)])
    at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN, [[0x80 | c, 60, 0] for c in range(16)])
    at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN + 0.8, probe_msgs(False))
    r = monkey.run(monkey.script_text([(1, "midi 0xB0 114 127")] + lines, (), lead_ms + int(1000 * take_len())))
    return r.x.astype(np.int64), judge_events(seg)


# ---- the device run ------------------------------------------------------------------------------------------------
def run_take(dev, sd, usb, a, s):
    import hil_must as hm
    seg = segment(s)
    rec = hm.Rec(usb, a.rate, 2, (0, 1), take_len() + 5.0, switch_rate=True, sd=sd)
    rows = []
    try:
        time.sleep(0.5)
        M = dev.mido.Message
        with hm.Poller(dev) as acc:
            mark = rec.mark()                   # (information: usb_judge's clock fit finds the lag)
            t0 = time.monotonic()
            for t, b in seg:
                while time.monotonic() - t0 < t:
                    time.sleep(0.0005)
                with dev.lock:
                    dev.out.send(M.from_bytes(b))

            def at(t, msgs):
                while time.monotonic() - t0 < t:
                    time.sleep(0.001)
                with dev.lock:
                    for m in msgs:
                        dev.out.send(M.from_bytes(m))
            at(SEG_S + PANIC_S, panic_msgs())
            at(SEG_S + PROBE_AT, probe_msgs(True))
            at(SEG_S + PROBE_AT + 0.05, [[0x90 | c, 60, 100] for c in range(16)])
            at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN, [[0x80 | c, 60, 0] for c in range(16)])
            at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN + 0.8, probe_msgs(False))
            at(take_len(), [])
        alive = hm.parse_reply(dev.query(0.5))
    finally:
        x = rec.stop()
    xi = np.round(np.asarray(x, float) * 32768).astype(np.int64)
    rows += hm.gate_counters(acc, a.rate)
    rows.append(("CC 116 answers after the probe", "yes" if "engine_us" in alive else "no", "yes",
                 "ok" if "engine_us" in alive else "FAIL"))
    ev = judge_events(seg)
    rows += judge_audio(xi, a.rate, ev, rec.flags)
    rows.append(("the recorder's mark", f"{mark:.3f} s", "information (the clock fit finds the lag)", "info"))
    return rows, xi, ev, acc.summary()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.dry_run:
        return dry_run(a)
    import sounddevice as sd
    import hil_must as hm
    dev = hm.Dev()
    usb = hm.find_usb(sd)
    if usb is None:
        print("no FM-1 USB audio input")
        return 2
    os.makedirs(a.out, exist_ok=True)
    dev.cc(114, 127)                                # USB VOL fixed: USB is the output before MASTER
    dev.send("pc", 0)
    time.sleep(1.0)
    report, verdict = [], "PASS"
    try:
        for s in plan(a.minutes, a.seed):
            rows, xi, ev, cnt = run_take(dev, sd, usb, a, s)
            bad = [r for r in rows if r[3] == "FAIL"]
            report.append(dict(seed=s, rows=[list(map(str, r)) for r in rows], counters=cnt))
            print(f"{time.strftime('%H:%M:%S')} seed {s}: " + ("FAIL " + "; ".join(f"{r[0]} {r[1]}" for r in bad)
                                                                if bad else "ok"), flush=True)
            if bad:
                verdict = "FAIL"
                np.savez_compressed(os.path.join(a.out, f"fail-{s}.npz"), usb=np.clip(xi, -32768, 32767).astype(
                    np.int16), fs=a.rate, events=json.dumps(ev), counters=json.dumps(cnt, default=str))
                break
    finally:
        dev.close()
    js = dict(verdict=verdict, date=time.strftime("%Y-%m-%d %H:%M"), rate=a.rate, minutes=a.minutes, takes=report)
    json.dump(js, open(os.path.join(a.out, "report.json"), "w"), indent=1, default=str)
    md = [f"# hil_monkey: {verdict}", "", f"- {js['date']}, {len(report)} takes of {take_len():.1f} s, USB at "
          f"{a.rate / 1000:g} kHz, seeds from {a.seed}", "", "| seed | check | value | criterion | result |",
          "|---|---|---|---|---|"]
    md += [f"| {t['seed']} | {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for t in report for r in t["rows"]]
    open(os.path.join(a.out, "report.md"), "w").write("\n".join(md) + "\n")
    print(f"hil_monkey {verdict}: {a.out}/report.md")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
