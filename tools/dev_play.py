#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Live-play check on the FM-1 (about 2 minutes): record what the user plays on USB audio AND the headphone jack (the
MOTU M6, input 5) at once, with the FM-1's own note-ons (USB MIDI), then per note: velocity, peak, H2..H5 re H1 on
both paths, the jack-minus-USB level, the jack's own distortion (jack H minus USB H) and jack clipping.

The jack is the reference (the user plays the jack): its verdict decides. Both paths are recorded by
tools/hil_jack.py's recorder (one callback stream per device, open together; two sd.rec calls stop each other and
left the jack at -240 dB, 2026-11-08).

Flags per note:
  DIST      USB or jack H3 over -25 dB re H1 (tools/dry_dist.py's v127 ceiling; a Rhodes' H2 is its pickup's own,
            -6..-17 dB, so it is reported, not flagged), or a USB peak over -1 dBFS (the engine itself)
  JACK+     a jack harmonic more than 3 dB over USB's (and above -70 dB re H1): the analog path adds distortion
  JCLIP     the jack within 0.1 dB of the interface's full scale
  OFFSET    the jack-minus-USB level more than 1 dB off the calibrated offset (build/hil/dev_play_cal.json,
            --calibrate writes it: the median over a take of clean notes)

  tools/dev_play.py SECONDS [--out build/hil/play/NAME] [--dev M6] [--ch 5] [--calibrate]
  tools/dev_play.py SECONDS --dry-run     no device: the host plays a demo (v40..127), the jack simulated (hil_jack)
Exit 1 when any note is flagged. Writes OUT.json (the rows) and OUT.npz (USB, jack, the note-ons).
"""
import argparse
import json
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_jack  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FS = hil_jack.FS
CAL = os.path.join(ROOT, "build", "hil", "dev_play_cal.json")
W0, W1 = 0.06, 0.36                 # s after the onset: the harmonic window (the attack's bark included)
H3_DIST, JACK_ADD, H_FLOOR, OFFSET_TOL = -25.0, 3.0, -70.0, 1.0


def midi_listener(secs, ev, t0):
    """the FM-1's note-ons, (t re t0[0], note, velocity), from its USB MIDI port"""
    import mido
    import fm1_ports
    names = fm1_ports.pick(mido.get_input_names())
    if not names:
        sys.exit("no FM-1 MIDI input")
    with mido.open_input(names[0]) as p:
        while t0[0] is None:
            time.sleep(0.001)
        while time.monotonic() < t0[0] + secs:
            for m in p.iter_pending():
                if m.type == "note_on" and m.velocity > 0:
                    ev.append((time.monotonic() - t0[0], m.note, m.velocity))
            time.sleep(0.0005)


def onset(x, t, look=(-0.05, 0.3)):
    """the note's onset near t (s): the first sample over 10 % of the window's peak"""
    a, b = max(0, int((t + look[0]) * FS)), int((t + look[1]) * FS)
    seg = np.abs(x[a:b])
    if not len(seg) or seg.max() == 0:
        return t
    return (a + int(np.argmax(seg > 0.1 * seg.max()))) / FS


def harmonics(seg, f0):
    """H2..H5 re H1 (dB) and the H1 level (dBFS) of a segment"""
    w = np.blackman(len(seg))
    sp = np.abs(np.fft.rfft(seg * w, 1 << 17))
    fr = np.fft.rfftfreq(1 << 17, 1 / FS)

    def pk(f):
        m = (fr > f * 0.97) & (fr < f * 1.03)
        return sp[m].max() if m.any() else 1e-12
    h1 = pk(f0)
    return [float(20 * np.log10(pk(f0 * k) / h1 + 1e-12)) for k in (2, 3, 4, 5)], \
        float(20 * np.log10(2 * h1 / w.sum() + 1e-12))


def analyse(usb, jack, ev, cal=None):
    rows = []
    for i, (t, note, vel) in enumerate(ev):
        t = onset(usb, t)
        s, e = int((t + W0) * FS), int((t + W1) * FS)
        if i + 1 < len(ev):
            e = min(e, int(ev[i + 1][0] * FS))          # (monophonic windows: the next note ends this one)
        if e - s < 4096:
            continue
        f0 = 440 * 2 ** ((note - 69) / 12)
        hu, lu = harmonics(usb[s:e], f0)
        hj, lj = harmonics(jack[s:e], f0)
        n0 = int(t * FS)
        pu = float(20 * np.log10(np.abs(usb[n0:n0 + FS // 2]).max() + 1e-12))
        pj = float(20 * np.log10(np.abs(jack[n0:n0 + FS // 2]).max() + 1e-12))
        off = lj - lu
        add = [j - u if j > H_FLOOR else 0.0 for j, u in zip(hj, hu)]
        flags = []
        if max(hu[1], hj[1]) > H3_DIST or pu > -1.0:
            flags.append("DIST")
        if max(add) > JACK_ADD:
            flags.append("JACK+")
        if pj > -0.1:
            flags.append("JCLIP")
        if cal is not None and abs(off - cal) > OFFSET_TOL:
            flags.append("OFFSET")
        rows.append(dict(t=round(t, 2), note=note, vel=vel, usb_peak=round(pu, 1), jack_peak=round(pj, 1),
                         offset=round(off, 2), h_usb=[round(v, 1) for v in hu], h_jack=[round(v, 1) for v in hj],
                         jack_add=[round(v, 1) for v in add], flags=flags))
    return rows


def table(rows, cal):
    out = [f"{len(rows)} notes measured; jack-USB offset calibrated {cal if cal is None else round(cal, 2)} dB",
           "     t note vel  USBpk  JACKpk  offset  H2..H5 USB              H2..H5 jack-USB        flags"]
    for r in rows:
        out.append(f"{r['t']:6.2f} {r['note']:4d} {r['vel']:3d} {r['usb_peak']:6.1f} {r['jack_peak']:7.1f} "
                   f"{r['offset']:7.2f}  " + " ".join(f"{v:5.1f}" for v in r["h_usb"]) + "   "
                   + " ".join(f"{v:+5.1f}" for v in r["jack_add"]) + "   " + " ".join(r["flags"]))
    bad = [r for r in rows if r["flags"]]
    out.append(f"verdict (the jack is the reference): {'PASS' if not bad else 'FAIL'}: {len(bad)} flagged note(s)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seconds", type=float)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "hil", "play", time.strftime("play-%H%M%S")))
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--calibrate", action="store_true", help="write the median jack-USB offset as the calibration")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    ev, t0 = [], [None]
    if a.dry_run:
        demo = [(0.5 + 0.7 * i, n, v) for i, (n, v) in enumerate((n, v) for n in (45, 57, 69) for v in (40, 96, 127))]
        events = [(t, "on", n, v) for t, n, v in demo] + [(t + 0.6, "off", n, 0) for t, n, v in demo]
        usb, jack = hil_jack.DryJack().both(a.seconds, events)
        ev = list(demo)
        cal_path = os.path.join(ROOT, "build", "hil", "dry", "dev_play_cal.json")
    else:
        rec = hil_jack.Jack(a.dev, a.ch)
        th = threading.Thread(target=midi_listener, args=(a.seconds, ev, t0), daemon=True)
        th.start()
        print(f"recording {a.seconds:.0f} s: play the FM-1 now (USB + the jack on {a.dev} input {a.ch})", flush=True)
        t0[0] = time.monotonic()
        usb, jack = rec.both(a.seconds, [])
        th.join(timeout=2)
        cal_path = CAL
    cal = json.load(open(cal_path))["offset"] if os.path.exists(cal_path) and not a.calibrate else None
    rows = analyse(np.asarray(usb, float), np.asarray(jack, float), sorted(ev), cal)
    if a.calibrate and rows:
        clean = [r["offset"] for r in rows if not r["flags"]] or [r["offset"] for r in rows]
        os.makedirs(os.path.dirname(cal_path), exist_ok=True)
        json.dump({"offset": float(np.median(clean)), "notes": len(clean), "when": time.strftime("%Y-%m-%d %H:%M")},
                  open(cal_path, "w"))
        cal = float(np.median(clean))
        print(f"calibrated: jack - USB = {cal:.2f} dB ({cal_path})")
    np.savez_compressed(a.out + ".npz", usb=np.asarray(usb, np.float32), jack=np.asarray(jack, np.float32),
                        notes=np.array(ev, float).reshape(-1, 3))
    json.dump(rows, open(a.out + ".json", "w"), indent=1)
    text = table(rows, cal)
    print(text)
    return 0 if all(not r["flags"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
