#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Does every note reach the USB recording on time, also after a knob turn and a pause? (the FM-1 over USB-MIDI +
USB audio; the headphone jack on the MOTU M6 input 5 at once when present)

Each take: a stored parameter changes at its start (the delay's TIME with the delay off: no sound change, but the
settings are due to be saved 2 s later, at the first silence), then three notes 2.5 s apart, each released after
1.2 s, so the output is silent before the next one. A flash erase holds the interrupts off for tens of ms: no USB
audio packet goes out, and macOS then hands the recording stale input for ~250 ms (the next note lost its first
50-300 ms on USB while the jack had it at once; measured 2026-10-18). The HAL now splits every erase and program into
~0.5 ms slices (fm1_flash.h fl_split_op) and the save lands in the take's first pause while the stream runs: this
tool checks that the notes after it reach USB on time.

USB is captured as float32 (bit exact: PortAudio's int16 conversion dithers) at the device's rate. Every callback's
ADC time is kept and the MIDI sends are stamped in the same stream clock, so onsets are times after the send, not
sample indices (a capture's first sample is not the moment the stream started: by index USB reads ~60-90 ms late,
the jack ~20-35 ms).

    python3 tools/hil_usb_onset.py [--takes 3] [--rate 44100|48000|both] [--engine rhodes|vibes] [--no-touch]
                                   [--no-jack] [--settle S]

Per note: the USB onset (the first sample over 8 LSB) and the jack's (1 % of the take's jack peak), ms after the
note-on left the computer. FAIL when a USB onset comes more than 20 ms after the jack's (no jack: more than 30 ms
after the send). --no-touch leaves the settings alone (the takes then show the stream without a save). Writes
build/hil/usb_onset.json. Needs the macOS sandbox off (CoreAudio)."""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hil_scan as H  # noqa: E402

NOTES = (53, 69, 89)
T0, GAP, HOLD = 0.3, 2.5, 1.2
USB_THR = 8                    # LSB
LATE_MS = 26.0                 # a USB onset after the jack's (20 before the USB ring kept a block more: +5.8 ms)
LATE_NOJACK_MS = 36.0          # a USB onset after the send, no jack (30 before; the lost onsets were 180-260 ms)
TOUCH_CC = H.CC["DL_TIME"]     # stored, silent with the delay off (DRY)


def take(rig, jack, sr, k, touch):
    sd = rig.sd
    ev = [(0.05, "cc", TOUCH_CC, 40 + k % 2)] if touch else []
    for i, n in enumerate(NOTES):
        ev += [(T0 + i * GAP, "on", n, 100), (T0 + i * GAP + HOLD, "off", n, 0)]
    dur = ev[-1][0] + 1.4
    bufs, pos, log = {}, {}, {}

    def mk(key, rate, ch):
        bufs[key] = np.zeros((int(dur * rate), ch), np.float32)
        pos[key], log[key] = 0, []

        def cb(indata, frames, ti, status):
            p = pos[key]
            log[key].append((p, ti.inputBufferAdcTime))
            m = min(frames, len(bufs[key]) - p)
            if m > 0:
                bufs[key][p:p + m] = indata[:m]
                pos[key] = p + m
        return cb
    rates = {"usb": sr}
    st = {"usb": sd.InputStream(device=rig.dev, samplerate=sr, channels=2, dtype="float32", callback=mk("usb", sr, 2))}
    if jack:
        jdev, jn, jch = jack
        rates["jack"] = int(sd.query_devices(jdev)["default_samplerate"])
        st["jack"] = sd.InputStream(device=jdev, samplerate=rates["jack"], channels=jn, dtype="float32",
                                    callback=mk("jack", rates["jack"], jn))
    for s in st.values():
        s.start()
    M = rig.mido.Message
    t0 = time.monotonic()
    sends = []
    for t, kind, a, b in ev:
        while time.monotonic() - t0 < t:
            time.sleep(0.0005)
        stamp = {key: s.time for key, s in st.items()}
        rig.out.send(M("note_on", note=a, velocity=b) if kind == "on" else M("note_off", note=a) if kind == "off"
                     else M("control_change", control=a, value=b))
        if kind == "on":
            sends.append((a, stamp))
    while any(pos[key] < len(bufs[key]) for key in pos) and time.monotonic() - t0 < dur + 5:
        time.sleep(0.01)
    for s in st.values():
        s.stop()
        s.close()
    um = np.abs(np.round(bufs["usb"] * 32768)).max(axis=1)
    jm = np.abs(bufs["jack"][:, jch]) if jack else None
    thr = {"usb": USB_THR, "jack": 0.01 * float(jm.max()) if jack else 0}
    sig = {"usb": um, "jack": jm}

    def onset(key, t_adc):
        L = np.array(log[key], float)
        i0 = int(np.interp(t_adc - 0.03, L[:, 1], L[:, 0]))
        hit = np.nonzero(sig[key][i0:i0 + rates[key]] > thr[key])[0]
        return float(np.interp(i0 + hit[0], L[:, 0], L[:, 1]) - t_adc) * 1000 if len(hit) else None
    res = dict(rate=sr, touch=touch, notes=[])
    for n, stamp in sends:
        r = dict(note=n, usb_ms=onset("usb", stamp["usb"]))
        if jack:
            r["jack_ms"] = onset("jack", stamp["jack"])
        r["late"] = bool(r["usb_ms"] is None or (r.get("jack_ms") is not None and r["usb_ms"] - r["jack_ms"] > LATE_MS)
                         or (not jack and r["usb_ms"] > LATE_NOJACK_MS))
        res["notes"].append(r)
    res["ok"] = not any(r["late"] for r in res["notes"])
    return res


def fmt(x):
    return "none" if x is None else f"{x:.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--rate", default="44100", choices=("44100", "48000", "both"))
    ap.add_argument("--engine", default="rhodes", choices=("rhodes", "vibes"))
    ap.add_argument("--no-touch", action="store_true", help="no settings change before the takes")
    ap.add_argument("--no-jack", action="store_true")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--settle", type=float, default=3.0, help="s after the reset (its own save lands there)")
    a = ap.parse_args()
    rig = H.Rig()
    rig.persistent = False                         # (a stream per take: this tool measures what that does)
    jack = None
    if not a.no_jack:
        devs = rig.sd.query_devices()
        js = [i for i, d in enumerate(devs) if a.jack_dev.lower() in d["name"].lower() and d["max_input_channels"] > 0]
        if js:
            jack = (js[0], devs[js[0]]["max_input_channels"], a.jack_ch - 1)
        else:
            print(f"(no '{a.jack_dev}' input: USB only)")
    M = rig.mido.Message

    def cc(c, v):
        rig.out.send(M("control_change", control=c, value=int(v)))
    cc(120, 0)
    for c, v in H.param_defaults():
        if c not in (79, 80):
            cc(c, v)
    for k, v in H.DRY.items():
        rig.cc(k, v)
    if a.engine == "vibes":
        cc(79, H.VIBES_CC79)
        cc(47, 0)                                   # the motor off
    cc(H.RESET_CC, 127)
    time.sleep(0.3)
    rig.wait_silence(15.0)
    time.sleep(a.settle)
    rates = (44100, 48000) if a.rate == "both" else (int(a.rate),)
    out, ok = [], True
    for sr in rates:
        for k in range(a.takes):
            r = take(rig, jack, sr, k, not a.no_touch)
            out.append(r)
            ok &= r["ok"]
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {sr} Hz take {k + 1}{' (a setting changed)' if r['touch'] else ''}: "
                  + ", ".join(f"{x['note']}: USB {fmt(x['usb_ms'])}" + (f" / jack {fmt(x.get('jack_ms'))}" if jack else "")
                              + " ms" + (" LATE" if x["late"] else "") for x in r["notes"]), flush=True)
            time.sleep(0.5)
    os.makedirs(H.OUT, exist_ok=True)
    json.dump(out, open(os.path.join(H.OUT, "usb_onset.json"), "w"), indent=1)
    print("ok  " if ok else "FAIL", f"{len(out)} takes: every USB onset within {LATE_MS:.0f} ms of the jack's"
          f" (no jack: {LATE_NOJACK_MS:.0f} ms of the send)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
