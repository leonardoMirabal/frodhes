# SPDX-License-Identifier: GPL-3.0-only
"""LCD_IDLE (frodhes.c FR_LCD_IDLE, app_idle; lcd.c calls it while a strip's DMA runs): the notes, from the keys and
MIDI, go to the engine during a screen draw, the rest waits for the main loop.

tests/host.c FRODHES_LCDMS=N: each lcd_sync lasts N ms (the clock, the script and the audio run on), as the FM-1's
main loop waits there for a strip. On the SEQUENCER page playing (the playhead redrawn ~25 Hz) with MIDI notes and
key presses spread over every phase, a CC stream and a button between them:
- no note lost (every note sounds, every key's note too; the engine's rings drop nothing), no state lost (the CCs and
  the button land: the same state as without the waits), the screen at the end the same pixels;
- the notes start as without the waits: the output is the same samples (the build without LCD_IDLE waits for the
  strip: its notes later).
Without FRODHES_LCDMS (every other host test) app_idle never runs."""
import os

import numpy as np

from hostrun import OUT, ROOT, build, cc, run

LCDMS = 8


def bins():
    return (build(os.path.join(ROOT, "build", "host", "idle1", "frodhes"), ["-DFR_LCD_IDLE=1"]),
            build(os.path.join(ROOT, "build", "host", "idle0", "frodhes"), ["-DFR_LCD_IDLE=0"]))


def take():
    ts = [1500 + 211 * i + (i * 37) % 23 for i in range(30)]
    lines = ["100 btn 11", "400 btn 12", cc(300, 119, 127)]          # SEQUENCER, PLAY (an empty loop), RAW
    for i, t in enumerate(ts):
        n = 48 + (i * 5) % 30
        lines += [f"{t} midi 0x90 {n} 100", f"{t + 90} midi 0x80 {n} 0"]
    for i in range(10):                                            # the panel keys (C4 = key 7 ..)
        t = 1600 + 600 * i + 97
        lines += [f"{t} keys 0x{1 << (3 + i % 12):x}", f"{t + 120} keys 0"]
    for i in range(40):                                            # a CC stream: DELAY DEPTH up and down
        lines.append(cc(1520 + 150 * i, 89, (i * 13) % 128))
    lines += [cc(8100, 89, 77), "8300 btn 12", "9000 shot", "9001 dump"]   # PLAY: stop, the screen, the state
    return ts, lines


def test_idle_notes_during_draws():
    b1, b0 = bins()
    _, lines = take()
    env = {"FRODHES_LCDMS": str(LCDMS)}
    r1 = run("idle1", lines, end_ms=9100, env=env, binary=b1)
    r0 = run("idle0", lines, end_ms=9100, env=env, binary=b0)
    rn = run("idle1_nowait", lines, end_ms=9100, binary=b1)
    for r in (r1, r0, rn):
        assert "ev_drop 0" in r.out, r.tag
    s1, s0, sn = (r.state(9001) for r in (r1, r0, rn))
    for k in ("p.14", "play", "page", "refs", "pitches", "drops"):   # (p.14: DELAY DEPTH, the CC stream's last)
        assert s1[k] == sn[k] == s0[k], (k, s1[k], s0[k], sn[k])
    assert s1["p.14"] == "77"
    a, b, c = (open(os.path.join(OUT, f"{t}-9000.ppm"), "rb").read() for t in ("idle1", "idle0", "idle1_nowait"))
    assert a == b == c, "the screen differs at the end"
    w1, w0, wn = (np.stack([r.L, r.R]) for r in (r1, r0, rn))
    assert w1.shape == wn.shape and np.array_equal(w1, wn), "LCD_IDLE: the notes not as without the waits"
    d = np.flatnonzero(np.any(w0 != wn, axis=0)) if w0.shape == wn.shape else [0]
    print(f"without LCD_IDLE: {len(d)} frames differ from no waits (the notes later), from {d[0] / 44.1:.0f} ms")
    assert len(d) > 0
