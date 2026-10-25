#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The level of a DRIVE TYPE matched to OFF (the drive switched off: its input), as a level per AMOUNT step times a
level per TONE step (tools/drive_gen/<type>_levels.json; the generator folds them into LVA / LVT, the firmware into
the TYPE's last biquad when a knob moves).

Measured on the firmware's own code (tools/drive_scan.py: fx_drive, 256 a block) with the tables as they are now, on
dry phrases (WAV files: the drive's input, the engine's full scale = 1.0; the private listening phrases by default):
K-weighted loudness (tools/bar_measure.lufs) of the output re the input, the mean over the phrases. The correction
is a pure gain at the TYPE's output, so one pass lands it (a second pass shows what is left).

  tools/drive_gen/calib_levels.py TSH8 [WAV ...]      then tools/gen_drive_tables.py
  tools/drive_gen/calib_levels.py TSH8 --check        the error left on a grid (no file written)
"""
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
import bar_measure as bm  # noqa: E402
import drive_scan as ds  # noqa: E402

def _private():
    """private/ of this checkout, or of the main checkout this worktree hangs from (.claude/worktrees/<name>)"""
    d = ROOT
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "private", "drive")):
            return os.path.join(d, "private")
        d = os.path.dirname(d)
    return os.path.join(ROOT, "private")


DEF = [os.path.join(_private(), "drive", "p0", "renders", f"dry_{p}.wav") for p in ("riff", "bass", "vibes")]
SKIP = 8192                                          # the start-up fades (FDRV out, the TYPE in) left out


def rd(p):
    w = wave.open(p)
    d = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())[:, 0]
    return d.astype(np.int64)                        # Q15: the engine's full scale


def lu(y):
    return bm.lufs(y[SKIP:].astype(float))


def delta(exe, typ, xs, a, t):
    return float(np.mean([lu(ds.render(exe, x, ds.params_for(typ, a, t))[0]) - lu(x) for x in xs]))


def main():
    typ = sys.argv[1].upper()
    check = "--check" in sys.argv
    wavs = [a for a in sys.argv[2:] if not a.startswith("--")] or DEF
    xs = [rd(p) for p in wavs]
    exe = ds.build(tag="calib")
    try:
        if check:
            e = [(a, t, delta(exe, typ, xs, a, t)) for a in (1, 32, 64, 96, 127) for t in (0, 32, 64, 96, 127)]
            for a, t, d in e:
                print(f"AMOUNT {a:3d} TONE {t:3d}: {d:+.2f} LU re OFF")
            print(f"worst {max(abs(d) for _, _, d in e):.2f} LU")
            return 0
        fn = os.path.join(HERE, f"{typ.lower()}_levels.json")
        old = json.load(open(fn)) if os.path.exists(fn) else {"amount": [1.0] * 128, "tone": [1.0] * 128}
        d64 = delta(exe, typ, xs, 64, 64)
        tone = [old["tone"][t] * 10 ** (-(delta(exe, typ, xs, 64, t) - d64) / 20) if t != 64 else old["tone"][64]
                for t in range(128)]
        amount = [old["amount"][a] * 10 ** (-delta(exe, typ, xs, a, 64) / 20) for a in range(1, 128)]
        amount = [amount[0]] + amount                # (AMOUNT 0 never runs: dry)
        json.dump({"note": f"{typ}: the level per AMOUNT step and per TONE step, matched to OFF "
                           "(tools/drive_gen/calib_levels.py)",
                   "amount": [round(v, 6) for v in amount], "tone": [round(v, 6) for v in tone]},
                  open(fn, "w"), indent=0)
        print("wrote", os.path.relpath(fn, ROOT), f"(at 64 / 64 it was {d64:+.2f} LU re OFF)")
    finally:
        os.remove(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
