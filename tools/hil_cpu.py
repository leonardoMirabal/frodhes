#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's audio CPU load, measured on the device over USB-MIDI: each scenario plays, then CC 116 asks the
firmware for the longest audio block since the last query (frodhes.c midi_cc; replies on CC 116/117 = us / 4).
The block is 256 frames at 44.1 kHz = 5805 us of real time; the voice shedder starts at 5000 us.

    python3 tools/hil_cpu.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from hil_scan import CC, DRY, param_defaults  # noqa: E402

BLOCK_US = 5805


def main():
    import mido
    outs = [n for n in mido.get_output_names() if any(k in n.lower() for k in ("felucca", "fm-1"))]
    ins = [n for n in mido.get_input_names() if any(k in n.lower() for k in ("felucca", "fm-1"))]
    if not outs or not ins:
        sys.exit("no FM-1 MIDI port")
    out, inp = mido.open_output(outs[0]), mido.open_input(ins[0])

    def cc(c, v):
        out.send(mido.Message("control_change", control=c, value=int(v)))

    def reset(**kv):
        cc(120, 0)
        for c, v in param_defaults():
            cc(c, v)
        d = dict(DRY)
        d.update(kv)
        if d.get("CP_SW"):                            # CC 85 (reface CHO/PHA) switches the phaser off: send it first
            cc(CC["CP_SW"], d.pop("CP_SW"))
        for k, v in d.items():
            cc(CC[k], v)
        time.sleep(0.3)

    def query():
        for _ in inp.iter_pending():
            pass
        cc(116, 127)
        got, t0 = {}, time.monotonic()
        while time.monotonic() - t0 < 1.0 and not {115, 116, 117, 118} <= got.keys():
            for m in inp.iter_pending():
                if m.type == "control_change" and m.control in (115, 116, 117, 118):
                    got[m.control] = m.value
            time.sleep(0.005)
        if not {116, 117} <= got.keys():
            sys.exit("no profiler reply (a firmware without CC 116?)")
        return (got[116] << 7 | got[117]) * 4, got[118] * 32, got[115]

    def play(notes, vel, hold, pedal=False):
        """returns the longest block of the attack (first 0.3 s) and of the held part after it"""
        if pedal:
            cc(64, 127)
        for n in notes:
            out.send(mido.Message("note_on", note=n, velocity=vel))
        time.sleep(0.3)
        attack = query()[0]
        time.sleep(hold - 0.3)
        held = query()[0]
        for n in notes:
            out.send(mido.Message("note_off", note=n))
        return attack, held

    heavy = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
                 CP_SW=64, CP_DEPTH=127, DL_SW=127, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127)
    bass8 = [28, 31, 33, 35, 36, 38, 40, 41]
    chord8 = [48, 52, 55, 59, 62, 64, 67, 71]
    rows = []

    def run(name, fx, notes=(), vel=100, hold=1.5, pedal=False, rep=3):
        att = held = 0
        shed0 = query()[2]
        for _ in range(rep):
            reset(**fx)
            query()                                   # clears the max
            if notes:
                a, h = play(notes, vel, hold, pedal)
            else:
                time.sleep(hold)
                a = h = query()[0]
            att, held = max(att, a), max(held, h)
            cc(64, 0)
        shed = (query()[2] - shed0) % 128
        rows.append((name, max(att, held)))
        print(f"{name:44s} attack {att:5d} us ({100 * att / BLOCK_US:4.1f} %)   held {held:5d} us "
              f"({100 * held / BLOCK_US:4.1f} %)   voices shed {shed}")

    run("idle, no effects", {})
    run("idle, every effect on (heaviest)", heavy)
    run("8-note chord v100, no effects", {}, chord8)
    run("8 bass notes v127 at once, no effects", {}, bass8, 127)
    run("8-note chord v100, every effect on", heavy, chord8)
    run("8 bass notes v127 + pedal, every effect on", heavy, bass8, 127, pedal=True)
    att = shed0 = 0
    shed0 = query()[2]
    reset(**heavy)
    query()
    for i in range(12):                               # 8-note chords struck again every 100 ms (voice steals)
        play(chord8 if i % 2 else bass8, 127, 0.4)
        att = max(att, query()[0])
    shed = (query()[2] - shed0) % 128
    rows.append(("repeated", att))
    print(f"{'8-note chords struck every 0.4 s, every effect':44s} attack {att:5d} us ({100 * att / BLOCK_US:4.1f} %)"
          f"   voices shed {shed}")
    reset()
    worst = max(w for _, w in rows)
    print(f"\nworst {worst} us of {BLOCK_US} us per block ({100 * worst / BLOCK_US:.0f} %); shedder at 5000 us; "
          f"headroom to the shedder {5000 - worst} us, to real time {BLOCK_US - worst} us")


if __name__ == "__main__":
    main()
