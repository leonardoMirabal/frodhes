#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's audio CPU load, measured on the device over USB-MIDI: each scenario plays, then CC 116 asks the
firmware for the longest audio block since the last query (frodhes.c midi_cc; replies on CC 116/117 = us / 4).
The block is 256 frames at 44.1 kHz = 5805 us of real time; the voice shedder starts at 5000 us.

    python3 tools/hil_cpu.py [--engine vibes] [--fx-each] [--calib]

--engine vibes selects VIBES (CC 79), with the motor on (CC 47; records v3: the engine's own) wherever the effects
are on (the tremolo too: it can run on top of the motor), off elsewhere, and adds the bar engine's worst voicings (edge strike, RUBBER, tube and weight at 127). It also prints the
bar engine's lowest mode cap and most live upper modes per scenario (CC 112 / 113 replies).
--fx-each: idle, then each effect alone (minus idle without effects) = the per-effect cost table of fx.c (FX_US_*).
--calib (VIBES): 8 RUBBER edge strikes at v127 with the mode cap forced (developer CC 9) to 0, 8, 16, 24, 32, 40, 48
upper modes, no effects, then 4 and 1 voices: fits the cost per upper mode-block and the fixed part per voice, for
bars.c BAR_NS_UNIT / BAR_NS_VOICE + BAR_NS_FORCE + BAR_NS_STRIKE.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from hil_scan import CC, DRY, param_defaults  # noqa: E402

CC = dict(CC, TONE=28, BARK=29, BELL=30, HAMMER=31)

BLOCK_US = 5805


def main():
    import mido
    vibes = ("--engine" in sys.argv and sys.argv[sys.argv.index("--engine") + 1] == "vibes") or "--calib" in sys.argv
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
        if vibes:
            cc(79, 64)                                # VIBES (the motor starts with the TYPE change)
            cc(47, 127 if d.get("TW_SW") else 0)      # the motor with the effects (records v3: its own CC), else off
            if d.get("TW_SW"):
                cc(17, 64)
        time.sleep(0.3)

    def query():  # noqa: C901
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
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.05:                # the bar engine's cap / live modes (newer firmware)
            for m in inp.iter_pending():
                if m.type == "control_change" and m.control in (112, 113):
                    got[m.control] = m.value
            time.sleep(0.005)
        query.bars = (got.get(112), got.get(113))
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

    query.bars = (None, None)
    heavy = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
                 CP_SW=64, CP_DEPTH=127, DL_SW=127, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127,
                 FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)   # the filter at its heaviest: LP18, the LFO
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
        extra = f"   modes cap/live {query.bars[0]}/{query.bars[1]}" if vibes and query.bars[0] is not None else ""
        print(f"{name:44s} attack {att:5d} us ({100 * att / BLOCK_US:4.1f} %)   held {held:5d} us "
              f"({100 * held / BLOCK_US:4.1f} %)   voices shed {shed}{extra}")

    if "--fx-each" in sys.argv:
        one = [("compressor", dict(CMP_SW=127, CMP_AMT=100)), ("filter LP18 + LFO", dict(FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)),
               ("drive", dict(DRIVE=127)), ("tremolo", dict(TW_SW=64, TW_DEPTH=127)), ("wah", dict(TW_SW=127, TW_DEPTH=127)),
               ("phaser", dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127)), ("chorus", dict(CP_SW=64, CP_DEPTH=127)),
               ("delay", dict(DL_SW=127, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127)), ("reverb", dict(REV=127))]
        base = []
        for _ in range(3):
            reset(); query(); time.sleep(1.0); base.append(query()[0])
        b = max(base)
        print(f"{'idle, no effects':24s} {b:5d} us")
        for name, fx in one:
            v = []
            for _ in range(3):
                reset(**fx); query(); time.sleep(1.0); v.append(query()[0])
            print(f"{name:24s} {max(v):5d} us   cost {max(v) - b:5d} us   (fx.c FX_US_*)")
        reset()
        return
    if "--calib" in sys.argv:
        edge = dict(TONE=127, BARK=127, BELL=127, HAMMER=127)
        bass8 = [28, 31, 33, 35, 36, 38, 40, 41]
        pts = []
        for nv, notes in ((8, bass8), (4, bass8[:4]), (1, bass8[:1])):
            for cap in ((0, 8, 16, 24, 32, 40, 48) if nv == 8 else (0, 24)):
                worst = 0
                for _ in range(3):
                    reset(**edge)
                    cc(9, cap + 1)
                    query()
                    a, h = play(notes, 127, 1.0)
                    worst = max(worst, a)
                    cc(64, 0)
                cc(9, 0)
                pts.append((nv, cap, worst, query.bars))
                print(f"{nv} voices, cap {cap:2d} upper modes: attack {worst:5d} us   (bars cap / live: {query.bars})")
        import numpy as np
        p8 = [(c, w) for nv, c, w, _ in pts if nv == 8]
        k, c0 = np.polyfit([c for c, _ in p8], [w for _, w in p8], 1)
        print(f"\nper upper mode-block (attack, the force on): {k:.1f} us -> BAR_NS_UNIT + BAR_NS_UNITF = {k / 8 * 1000:.0f} ns")
        v = {nv: w for nv, c, w, _ in pts if c == 0}
        per = (v[8] - v[1]) / 7
        print(f"per voice at the strike (pair, pulse, note-on, gain): {per:.0f} us -> BAR_NS_VOICE + FORCE + STRIKE")
        print(f"fixed (idle + noise + 1 voice): {v[1]:.0f} us; the model's base is BAR_NS_BASE + BAR_NS_NOISE")
        reset()
        return
    if vibes:
        edge = dict(TONE=127, BARK=127, BELL=127, HAMMER=127)
        run("8 bass v127, edge strike, rubber, no effects", edge, bass8, 127)
        run("8-note chord v127, edge, every effect + motor", dict(heavy, **edge), chord8, 127)
        run("8 bass v127, edge + pedal, every effect + motor", dict(heavy, **edge), bass8, 127, pedal=True, hold=3.0)
        query.bars = (None, None)
        heavy = dict(heavy, **edge)               # the repeated-chord test at the worst voicing too
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
