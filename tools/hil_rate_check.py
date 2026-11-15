#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's USB audio stream timing in ~2 minutes: the rig (the host's capture) or the firmware's USB stream?

    tools/hil_rate_check.py [--secs 25] [--wait-lock S]

No MIDI, no notes: the FM-1 only has to run Frodhes (silence is enough: the check reads the blocks' ADC times, not
the audio). Three recordings of the FM-1's USB input through hil_scan's own recorder (Capture / Ring.cut, tol 6 ms):

  A  48k matched     the device switched to 48 kHz, the stream at 48 kHz (hil_scan's Rig since the fix; hil_cpu)
  B  48k converted   the device switched to 44.1 kHz first, then a stream at 48 kHz WITHOUT the switch: Core Audio
                     converts (what hil_scan --tier full did after stress_max, 0.9.10 / 0.9.11 T2, 2026-11-12)
  C  44.1k matched   the device and the stream at 44.1 kHz (stress_max's own recorder)

For each: the gaps Ring.cut reports, the largest ADC-time step, the callback block size, and the beat (the period of
the same-sign steps; the T2 logs: -10 / +10 ms pairs every 0.134 s = 7.44 Hz). The device ends at 48 kHz.

Reading it:
  A and C clean, B gapped at ~0.134 s   the rig: Core Audio's rate conversion; fixed in hil_scan (Rig.capture opens
                                        at FS with the device switched). The firmware's USB stream is fine
  A or C gapped too                     not the conversion: the FM-1's USB stream (or the host). Install 0.9.9 and
                                        run this again: gapped there too = host / USB port / hub; clean = firmware
  B clean too                           this Mac does not show the conversion beat: look elsewhere (hil_scan's T2
                                        log, Capture.add's blocksize, another app holding the FM-1)

Before it: no music software running (no host holding the M6 or the FM-1), no other USB-audio tool on any FM-1 (one audio tool at a time),
FM1_LOCATION=0x... to pin the unit when two FM-1s are plugged in. Takes the DEVICE lock (tools/lock.py).
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm1_ports  # noqa: E402
import hil_scan as hs  # noqa: E402

TOL = 0.006          # Ring.cut's own re-anchor threshold


def beat(gaps):
    """{'n', 'period' (s, median between consecutive same-sign steps), 'step_ms' (median |step| signed by the first
    step's sign)} from Ring.gaps [(adc time, step s)], or None with fewer than 3 gaps"""
    if len(gaps) < 3:
        return None
    t = np.array([a for a, _ in gaps])
    g = np.array([s for _, s in gaps])
    per = []
    for sgn in (-1, 1):
        ts = t[np.sign(g) == sgn]
        if len(ts) > 1:
            per += list(np.diff(ts))
    if not per:
        return None
    return dict(n=len(gaps), period=float(np.median(per)), step_ms=float(np.sign(g[0]) * np.median(np.abs(g)) * 1000))


def analyse(ring, t0, dur):
    """(gaps, worst ADC step s, median block frames, beat) of the recording [t0, t0 + dur] on the stream clock"""
    ring.cut(t0, dur, tol=TOL)
    gaps = list(ring.gaps)
    L, _ = ring._snap(t0)
    L = L[(L[:, 1] >= t0) & (L[:, 1] <= t0 + dur)]
    df = np.diff(L[:, 0])
    step = np.diff(L[:, 1]) - df / ring.sr
    return gaps, float(np.max(np.abs(step))) if len(step) else 0.0, float(np.median(df)) if len(df) else 0.0, \
        beat(gaps)


def record(sd, dev, rate, switch, secs):
    cap = hs.Capture(sd)
    try:
        ring = cap.add("usb", dev, (0, 1), 2, secs=secs + 10, sr=rate, switch_rate=switch)
        time.sleep(0.5)
        t0 = cap.now()["usb"]
        time.sleep(secs + 0.3)
        return analyse(ring, t0, secs)
    finally:
        cap.close()


def switch_to(sd, dev, rate):
    """the device itself to rate (a short stream with change_device_parameters, then closed)"""
    cap = hs.Capture(sd)
    try:
        cap.add("usb", dev, (0, 1), 2, secs=2.0, sr=rate, switch_rate=True)
        time.sleep(0.5)
    finally:
        cap.close()


def verdict(res):
    bad = {k: bool(v[0]) for k, v in res.items()}
    if not bad["A"] and not bad["C"] and bad["B"]:
        return "RIG: the gaps come from Core Audio's rate conversion (B only); the FM-1's USB stream is clean"
    if bad["A"] or bad["C"]:
        return ("NOT THE CONVERSION: a matched stream gapped too: the FM-1's USB stream or the host; install 0.9.9 "
                "and run again (gapped there too = host / port / hub; clean = firmware)")
    return "NO GAPS anywhere: this Mac does not show the conversion beat; look at the T2 run itself"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--secs", type=float, default=25.0, help="each recording's length (default 25 s)")
    ap.add_argument("--wait-lock", type=float, default=600.0, help="wait this long for the DEVICE lock")
    a = ap.parse_args(argv)
    import lock
    import sounddevice as sd
    import lock
    lock.usb_audio()                # (one audio tool at a time: tools/lock.py)
    with lock.Lock("DEVICE", who="hil_rate_check", why="USB stream timing check", wait_s=a.wait_lock):
        devs = fm1_ports.devices(sd.query_devices(), 2, hs.PORT_KEYS[:2])
        if not devs:
            sys.exit("no FM-1 audio input")
        dev = devs[0]
        print(f"FM-1 USB audio: {sd.query_devices(dev)['name']!r} (device {dev}); PortAudio's cached default "
              f"{sd.query_devices(dev)['default_samplerate']:.0f} Hz", flush=True)
        res = {}
        try:
            plan = (("A", 48000, 48000, True, "48k matched (device switched to 48 kHz)"),
                    ("B", 48000, 44100, False, "48k converted (device at 44.1 kHz)"),
                    ("C", 44100, 44100, True, "44.1k matched (device switched to 44.1 kHz)"))
            for key, rate, pre, switch, what in plan:
                switch_to(sd, dev, pre)
                gaps, worst, blk, b = res[key] = record(sd, dev, rate, switch, a.secs)
                bt = (f"; beat {b['period'] * 1000:.0f} ms ({1 / b['period']:.2f} Hz), step {b['step_ms']:+.1f} ms"
                      if b else "")
                print(f"  {key}  {what:46s} gaps {len(gaps):4d}  worst ADC step {worst * 1000:5.1f} ms  "
                      f"block {blk:.0f} frames{bt}", flush=True)
        finally:
            switch_to(sd, dev, 48000)                 # (the rate hil_cpu and hil_scan use)
    print(verdict(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
