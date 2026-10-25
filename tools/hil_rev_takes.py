#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The reverb TYPEs' listening takes (0.9.5): the FM-1 played over MIDI, recorded on USB and on the headphone jack
(the MOTU M6 input 5) at once, every take a pair of WAVs for the user's listening round (the plan's wave 4: 3 TYPEs x 3
settings, the hold for 60 s (FREEZE and SUSTAIN), TYPE switching while playing, REVERB 2's knobs). The firmware's counters are read
every 0.5 s as in the cracks test (tools/hil_crackle.py Device: CPU, USB underruns), and printed per take.

  tools/hil_rev_takes.py [--engine rhodes|vibes|marimba] [--out DIR] [--only NAME,..] [--rate 48000]
                         [--source both|usb] [--jack-dev M6] [--jack-ch 5] [--dry-run] [--host] [--list]

  --out DIR   where the WAVs go (default private/reverb/takes/<engine>/): <take>-usb.wav (stereo, the USB rate) and
              <take>-jack.wav (mono, 48 kHz), and takes.json (each take's settings, events, counters, peaks)
  --only      the takes to play (names from --list)
  --host      the host build in the FM-1's place (USB only, 44.1 kHz): a check of the script, no device
  --dry-run   the takes, their settings and the time; no MIDI, no audio
Every take starts from every parameter's default (hil_crackle.engine_ccs: CC 120, the defaults, USB VOL FIXED, the
developer reset), then the reverb's own CCs (from params.c: TYPE CC 95's bands, SIZE / MOD / LOW CUT their CCs).

The ready-to-run command (FM-1, sandbox off, build/hil/DEVICE.lockdir held; MASTER fully up and untouched):
  tools/hil_rev_takes.py --dry-run                                     the plan (~6 min on the Rhodes)
  tools/hil_rev_takes.py                                               the Rhodes -> private/reverb/takes/rhodes/
  tools/hil_rev_takes.py --engine vibes; tools/hil_rev_takes.py --engine marimba   (~6 min each)
"""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import hil_crackle as hc  # noqa: E402
import hil_cpu_rev as hcr  # noqa: E402

ROOT = os.path.dirname(TOOLS)
CC_MIX, CC_DECAY, CC_TONE, CC_PDLY = 91, 92, 93, 94
SETTINGS = dict(short=dict(MIX=60, DECAY=32, TONE=64), default=dict(MIX=80, DECAY=64, TONE=64),
                long=dict(MIX=100, DECAY=110, TONE=90))
# a phrase in C (no samples: the FM-1 plays it): chords under the pedal, then a melody, then the tail
CHORDS = [(0.0, [48, 55, 64, 67, 71]), (2.0, [45, 52, 60, 64, 67]), (4.0, [50, 57, 60, 65, 69]),
          (6.0, [43, 50, 59, 62, 65])]
MELODY = [(8.0, 72), (8.4, 74), (8.8, 76), (9.2, 79), (9.8, 77), (10.2, 76), (10.6, 74), (11.2, 72)]


def knob_ccs():
    """{name: CC} of the reverb's controls from params.c (TYPE's CC and its bands: hil_cpu_rev)"""
    out = dict(MIX=CC_MIX, DECAY=CC_DECAY, TONE=CC_TONE, PDLY=CC_PDLY)
    for k, p in (("SIZE", "RV_SIZE"), ("MOD", "RV_MOD"), ("LOCUT", "RV_LOCUT")):
        pl = hcr.pline(p)
        if pl:
            out[k] = pl[2]
    return out


def phrase(t0=0.0, vel=(96, 110)):
    """[(t, kind, a, b)]: the chords (each let go when the next comes, the pedal down), the melody, everything off"""
    ev = [(t0 + 0.05, "cc", 64, 127)]
    held = []
    for t, ch in CHORDS:
        ev += [(t0 + t - 0.01, "off", n, 0) for n in held]
        ev += [(t0 + t + 0.01 * i, "on", n, vel[i % 2]) for i, n in enumerate(ch)]
        held = ch
    ev += [(t0 + 7.9, "off", n, 0) for n in held] + [(t0 + 7.95, "cc", 64, 0)]
    for t, n in MELODY:
        ev += [(t0 + t, "on", n, 100), (t0 + t + 0.35, "off", n, 0)]
    return ev


def takes():
    """[(name, reverb setup [(cc, v)], events, dur s)]"""
    ccs, c95 = hcr.rev_type_ccs()
    k = knob_ccs()
    if not ccs:
        sys.exit("hil_rev_takes: this tree has no reverb TYPEs (params.c without P_RV_TYPE)")
    T = []
    for typ in ("HALL", "PLATE", "INFINITE"):
        for sname, s in SETTINGS.items():
            setup = [(c95, ccs[typ]), (k["MIX"], s["MIX"]), (k["DECAY"], s["DECAY"]), (k["TONE"], s["TONE"])]
            tail = 10.0 if typ == "INFINITE" and sname == "long" else 6.0
            T.append((f"{typ.lower()}-{sname}", setup, phrase(), 12.0 + tail))
    # the hold, both of rev_types.c's modes (the plan's listening question: hold what is in, or keep adding): chords in
    # at DECAY 100, held from 8 s (127 FREEZE: the input closed; 123 SUSTAIN: open), notes over it, 60 s held
    for name, dv in (("infinite-freeze-60s", 127), ("infinite-sustain-60s", 123)):
        ev = phrase() + [(8.0, "cc", k["DECAY"], dv)] + [(20.0 + 2 * i, "on", 60 + 2 * i, 90) for i in range(6)]
        ev += [(20.3 + 2 * i, "off", 60 + 2 * i, 0) for i in range(6)] + [(70.0, "cc", k["DECAY"], 64)]
        T.append((name, [(c95, ccs["INFINITE"]), (k["MIX"], 90), (k["DECAY"], 100)], ev, 80.0))
    # TYPE switching while playing: a chord every second, the TYPE every 4 s
    ev, seq = [], ["HALL", "PLATE", "INFINITE", "HALL", "INFINITE", "PLATE", "OFF"]
    for i in range(28):
        ch = CHORDS[i % 4][1]
        ev += [(0.2 + i, "on", n, 100) for n in ch] + [(0.2 + i + 0.8, "off", n, 0) for n in ch]
    for i, typ in enumerate(seq):
        ev.append((4.0 * i + 0.5, "cc", c95, 0 if typ == "OFF" else ccs[typ]))
    T.append(("switch-types", [(c95, ccs["HALL"]), (k["MIX"], 90)], ev, 34.0))
    # REVERB 2 on each TYPE: SIZE small / large, MOD 0 / 127, LOW CUT 100, one phrase each
    if all(x in k for x in ("SIZE", "MOD", "LOCUT")):
        for typ in ("HALL", "PLATE", "INFINITE"):
            for name, kv in (("size-small", {"SIZE": 10}), ("size-large", {"SIZE": 127}), ("mod-0", {"MOD": 0}),
                             ("mod-127", {"MOD": 127}), ("lowcut-100", {"LOCUT": 100})):
                setup = [(c95, ccs[typ]), (k["MIX"], 90), (k["DECAY"], 80)] + [(k[a], v) for a, v in kv.items()]
                T.append((f"{typ.lower()}-{name}", setup, phrase()[:-len(MELODY) * 2], 14.0))
    return T


def write_wav(path, x, fs):
    y = np.clip(np.round(np.asarray(x, float)), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1 if y.ndim == 1 else y.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(y.tobytes())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="rhodes", choices=hc.ENGINES)
    ap.add_argument("--out")
    ap.add_argument("--only")
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--source", default="both", choices=("both", "usb"))
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    T = takes()
    if a.only:
        want = set(a.only.split(","))
        T = [t for t in T if t[0] in want]
        missing = want - {t[0] for t in T}
        if missing:
            sys.exit(f"--only: no take {', '.join(sorted(missing))} (--list)")
    if a.list or a.dry_run:
        total = 0.0
        for name, setup, ev, dur in T:
            total += dur + 1.5
            print(f"  {name:24s} {dur:5.1f} s  setup " + " ".join(f"CC{c}={v}" for c, v in setup)
                  + f"  {sum(1 for e in ev if e[1] == 'on')} notes"
                  + "".join(f"  @{e[0]:g}s CC{e[2]}={e[3]}" for e in ev if e[1] == "cc" and e[2] != 64))
        if a.dry_run:
            print(f"hil_rev_takes: {len(T)} takes on {a.engine}, USB {a.rate} Hz"
                  + (" + the jack" if a.source == "both" and not a.host else "") + f"; ~{total / 60:.1f} min"
                  + (" (the host build)" if a.host else " on the FM-1"))
        return 0
    od = a.out or os.path.join(ROOT, "private", "reverb", "takes", a.engine + ("-host" if a.host else ""))
    os.makedirs(od, exist_ok=True)
    dev = None if a.host else hc.Device(a.rate, a.source == "both", a.jack_dev, a.jack_ch)
    log = []
    try:
        for name, setup, ev, dur in T:
            ccs = hc.engine_ccs(a.engine, {}) + setup
            ev = sorted(ev, key=lambda e: e[0])
            if dev:
                u, j, counters, rig = dev.take(ccs, ev, dur)
                fs = dev.rate
            else:
                u, _ = hc.host_take(f"rev-{name}", ccs, ev, dur, tag="rev_takes")
                j, counters, rig, fs = None, None, [], hc.FS_HOST
            rec = dict(name=name, engine=a.engine, setup=setup, events=ev, dur=dur, rig=rig,
                       peak_usb=None if u is None else float(np.abs(u).max()),
                       peak_jack=None if j is None else float(np.abs(j).max()))
            if u is not None:
                write_wav(os.path.join(od, f"{name}-usb.wav"), u, fs)
            if j is not None:
                write_wav(os.path.join(od, f"{name}-jack.wav"), j, hc.FS_JACK)
            if counters:
                sm = counters["summary"]
                rec["counters"] = dict(engine_max_us=sm["engine_max_us"], isr_max_us=sm["isr_max_us"],
                                       shed=sm["shed"], usb={k: sm["usb"].get(k, 0) for k in
                                                             ("underruns", "missed", "overruns")},
                                       fails=[r for r in counters["rows"] if r[3] == "FAIL"])
            log.append(rec)
            c = rec.get("counters")
            print(f"  {name:24s} USB peak {rec['peak_usb'] or 0:7.0f}" +
                  (f"  jack peak {rec['peak_jack']:7.0f}" if j is not None else "") +
                  (f"  CPU {c['engine_max_us']} / whole {c['isr_max_us']} us, shed {c['shed']}, USB {c['usb']}"
                   + (f"  FAIL {c['fails']}" if c["fails"] else "") if c else "") +
                  (f"  RIG {rig[:2]}" if rig else ""), flush=True)
            time.sleep(0.5)
    finally:
        if dev:
            dev.cap.close()
        json.dump(log, open(os.path.join(od, "takes.json"), "w"), indent=1, default=str)
    print(f"hil_rev_takes: {len(log)} takes in {os.path.relpath(od, ROOT)}/")
    bad = [r["name"] for r in log if r.get("rig") or (r.get("counters") or {}).get("fails")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
