#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The sequencer on the FM-1 over USB-MIDI and USB audio: live recording (REC, PLAY, three notes, the loop played
back on the QUANT grid) and step recording (stopped: three notes, two steps apart at 1/8). Clears the pattern."""
import sys, time, numpy as np, mido, sounddevice as sd
sys.path.insert(0, "tools")
from hil_scan import param_defaults, CC, DRY
KEYS = ("frodhes", "felucca", "fm-1")   # the FM-1's port names: Frodhes since the rename, Felucca before
import fm1_midi
out = fm1_midi.ChannelOut(mido.open_output([n for n in mido.get_output_names() if any(k in n.lower() for k in KEYS)][0]))
print(fm1_midi.say(out.ch), flush=True)
cc = lambda c, v: out.send(mido.Message("control_change", control=c, value=v))
note = lambda n, on: out.send(mido.Message("note_on" if on else "note_off", note=n, velocity=100))
dev = [i for i, d in enumerate(sd.query_devices()) if any(k in d["name"].lower() for k in KEYS[:2])
       and d["max_input_channels"] >= 2][0]
FS = 48000; names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
def reset():
    for c, v in param_defaults(): cc(c, v)
    for k, v in DRY.items(): cc(CC[k], v)
    cc(43, 127); cc(61, 1); cc(62, 2); cc(46, 50); time.sleep(0.3)
def onsets(x, t_from=0.0):
    x = x[:, 0].astype(float); f = int(0.004 * FS)
    e = np.array([np.sqrt(np.mean(x[i:i + f] ** 2)) for i in range(0, len(x) - f, f)])
    ons, res = [], []
    for i in range(3, len(e)):
        if e[i] > 1.8 * e[i - 3] + 30 and (not ons or i * 0.004 - ons[-1] > 0.08): ons.append(i * 0.004)
    for o in ons:
        if o < t_from: continue
        s = x[int((o + 0.015) * FS):int((o + 0.1) * FS)]; sp = np.abs(np.fft.rfft(s * np.hanning(len(s)), 8 * len(s))); fr = np.fft.rfftfreq(8 * len(s), 1 / FS)
        m = (fr > 200) & (fr < 450); fq = fr[m][np.argmax(sp[m])]; n = int(round(69 + 12 * np.log2(fq / 440)))
        res.append((o, names[n % 12] + str(n // 12 - 1)))
    return res
# 1) live: REC on, PLAY, C E (late) G; then REC off and listen to the loop
reset()
x = sd.rec(int(8 * FS), samplerate=FS, channels=2, device=dev, dtype="int16")
time.sleep(0.3); cc(63, 127); cc(60, 127); t0 = time.monotonic()
for n, at in ((60, 0.02), (64, 0.68), (67, 1.36)):
    while time.monotonic() - t0 < at: time.sleep(0.001)
    note(n, 1); time.sleep(0.2); note(n, 0)
while time.monotonic() - t0 < 2.8: time.sleep(0.01)
cc(63, 0); sd.wait(); cc(60, 0)
r = onsets(x)
print("live:", "  ".join(f"{o:.2f}s {nm}" for o, nm in r))
c = [o for o, nm in r if nm == "C4"]
if len(c) >= 3:
    L = c[2] - c[1]; base = c[2]
    print("  loop %.0f ms;" % (L * 1000), "pass 3:", "  ".join(f"{nm} +{(o - base) * 1000:.0f} ms" for o, nm in r if base - 0.01 <= o < base + L - 0.05))
# 2) step: stopped (the cursor at bar 1, step 1), REC on; C (2 steps), E (2), rest (via the next), G; REC off, PLAY
reset(); cc(60, 0); time.sleep(0.2); cc(63, 127); time.sleep(0.1)
for n in (60, 64, 67):
    note(n, 1); time.sleep(0.1); note(n, 0); time.sleep(0.15)
cc(63, 0); time.sleep(0.2)
x = sd.rec(int(4 * FS), samplerate=FS, channels=2, device=dev, dtype="int16"); time.sleep(0.2); cc(60, 127); sd.wait(); cc(60, 0)
r = onsets(x)
if r:
    b = r[0][0]
    print("step:", "  ".join(f"{nm} +{(o - b) * 1000:.0f} ms" for o, nm in r if o < b + 2.0))
cc(43, 127)
