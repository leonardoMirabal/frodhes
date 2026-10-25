#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""A TAPE delay recording on the headphone jack and USB at once, judged by stress_max's detectors (run_final.sh).

Neither hil_jack.py nor hil_scan's jack sessions select the delay TYPE, so this runs tools/hil_crackle.py unchanged
(its Device: USB at --rate + the jack on the MOTU M6 input 5, the CC 116 counters every 0.5 s, every detector:
repeat, stale, zero, clip, click, step and the jack-vs-USB track) with its takes replaced by TAPE ones, on the Rhodes:
  tape-alone     the user's recipe (notes added one by one to every voice + 3, under the pedal), only the TAPE delay
                 on (DEPTH 127, TIME 90, FDBK 110): the echoes, the wow / flutter and the feedback's tail in the clear
  tape-moderate  the same with stress_max's moderate effects and the delay TAPE (DEPTH 100, TIME 100, FDBK 100)
  tape-worst     stress_max's worst-case chords with every effect heavy, the delay TAPE, the heaviest drive TYPE
Every take is saved whole (USB and jack WAVs) in build/hil/tape_jack/<rate>/; the report there (report.txt / .json).

    TREE=<release tree> tape_jack.py [--rate 48000] [--seconds 60] [--jack-dev M6] [--jack-ch 5] [--dry-run]"""
import argparse
import os
import sys
import wave

import numpy as np

TREE = os.path.abspath(os.environ.get("TREE") or os.getcwd())
sys.path.insert(0, os.path.join(TREE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def write_wav(path, x, fs):
    y = np.clip(np.round(np.asarray(x, float)), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1 if y.ndim == 1 else y.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(y.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rate", default="48000", choices=("44100", "48000"))
    ap.add_argument("--seconds", type=float, default=60.0, help="per take (hil_crackle keeps takes <= 60 s)")
    ap.add_argument("--engine", default="rhodes", choices=("rhodes", "vibes", "marimba"))
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="the takes and their time; no MIDI, no audio")
    ap.add_argument("--host", action="store_true", help="the host build plays (the jack simulated): the detectors on clean TAPE renders")
    a = ap.parse_args()
    import hil_crackle as C
    import hil_cpu_fx as F
    tv = F.tape_cc()
    if tv is None:
        print("tape_jack: FAIL: this tree's params.c has no TAPE delay (N_DL)")
        return 1
    types = F.drive_types()
    heavy_drv = sorted([t for t in types if t[2] is not None], key=lambda t: -t[2])[:1]
    C.OUT = os.path.join(C.ROOT, "build", "hil", "tape_jack")
    names = {}

    def tape_takes(eng, worst_s=0.0, recipe_s=0.0, seed=0, nv=None):
        nv = nv or C.voices(eng)
        rng = np.random.default_rng(seed + 31)
        dur = min(a.seconds, C.TAKE_MAX)
        out = []
        for name, fx, gen, extra in (
                ("tape-alone", dict(DL_SW=tv, DL_DEPTH=127, DL_TIME=90, DL_FDBK=110), C.recipe_events, []),
                ("tape-moderate", dict(C.MODERATE, DL_SW=tv, DL_DEPTH=100, DL_TIME=100, DL_FDBK=100),
                 C.recipe_events, []),
                ("tape-worst", dict(C.HEAVY, DL_SW=tv), C.worst_events,
                 [(84, heavy_drv[0][3])] if heavy_drv else [])):
            ev, d = gen(nv, dur, rng)
            ev = sorted(ev, key=lambda e: e[0])
            ccs = C.engine_ccs(eng, fx) + extra
            names[id(ev)] = f"{eng}-{name}"
            out.append((f"{eng}-{name}", ccs, ev, d))
        return out
    C.takes = tape_takes
    print(f"tape_jack: delay TAPE = CC 88 {tv}" + (f"; tape-worst with the drive TYPE {heavy_drv[0][0]} (CC 84 "
                                                    f"{heavy_drv[0][3]})" if heavy_drv else "; no drive TYPEs"))
    if not a.dry_run and not a.host:
        orig = C.Device.take

        def take(self, ccs, events, dur, usb=True):
            r = orig(self, ccs, events, dur, usb)
            od = os.path.join(C.OUT, str(self.rate))
            os.makedirs(od, exist_ok=True)
            nm = names.get(id(events), "take")
            if r[0] is not None:
                write_wav(os.path.join(od, f"{nm}-usb.wav"), r[0], self.rate)
            if r[1] is not None:
                write_wav(os.path.join(od, f"{nm}-jack.wav"), r[1], C.FS_JACK)
            print(f"tape_jack: saved {nm} (USB {'yes' if r[0] is not None else 'no'}, jack "
                  f"{'yes' if r[1] is not None else 'no'}) in {os.path.relpath(od, C.ROOT)}", flush=True)
            return r
        C.Device.take = take
    args = ["--engine", a.engine, "--rate", a.rate, "--no-usb-s", "0", "--no-self-test", "--jack-dev", a.jack_dev,
            "--jack-ch", str(a.jack_ch)] + (["--dry-run"] if a.dry_run else []) + (["--host"] if a.host else [])
    return C.main(args)


if __name__ == "__main__":
    sys.exit(main())
