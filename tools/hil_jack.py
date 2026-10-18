#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's headphone jack against its USB audio: hiss, signal-to-noise, headroom, harmonic match.

Records the USB audio and an audio interface input (the jack) at the same time while MIDI plays the FM-1.

  hil_jack.py [--dev NAME] [--ch N]      (default: the interface "M6", input 5 -> --ch 5)

Reports (all jack figures re the interface's full scale, so they depend on its input gain; the differences are
what matter):
  silence        jack floor with the codec muted (the interface's own floor)
  hiss           8-20 kHz under a low quiet note (no music there): jack vs USB -> the jack's own hiss
  max level      a loud chord's peak and RMS on the jack (and on USB)
  snr            loud-chord RMS minus the hiss (A-weighting not applied)
  harmonics      A1 / A2 at 4 velocities: H2..H6 re H1 on USB and on the jack (distortion added by the jack)
Results also go to build/hil/jack.json (jack-vibes.json with --engine vibes).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_scan as hs  # noqa: E402

FS = 48000


class Jack:
    def __init__(self, devname, ch):
        import sounddevice as sd
        self.sd = sd
        self.r = hs.Rig()
        self.jdev = [i for i, d in enumerate(sd.query_devices()) if d["name"] == devname][0]
        self.jch = ch - 1
        self.jn = sd.query_devices(self.jdev)["max_input_channels"]

    def both(self, dur, events):
        """USB and the jack at once, by callback, each device at its own rate (PortAudio resampling the M6's 48 kHz to
        44.1 while the FM-1 streamed made the USB capture skip ~200 ms in some takes, 2026-10-18); the jack resampled
        to FS afterwards"""
        sd = self.sd
        jsr = int(sd.query_devices(self.jdev)["default_samplerate"])
        usr = int(sd.query_devices(self.r.dev)["default_samplerate"])   # each device at its own rate (no conversion)
        n, nu, nj = int(dur * FS), int(dur * usr), int(dur * jsr)
        bufs = {"u": np.zeros((nu, 2), np.int16), "j": np.zeros((nj, self.jn), np.float32)}
        pos, lim = {"u": 0, "j": 0}, {"u": nu, "j": nj}

        def mk(key):
            def cb(indata, frames, ti, status):
                p = pos[key]
                k = min(frames, lim[key] - p)
                if k > 0:
                    bufs[key][p:p + k] = indata[:k]
                    pos[key] = p + k
            return cb
        s1 = sd.InputStream(device=self.r.dev, samplerate=usr, channels=2, dtype="int16", callback=mk("u"))
        s2 = sd.InputStream(device=self.jdev, samplerate=jsr, channels=self.jn, dtype="float32", callback=mk("j"))
        s1.start()
        s2.start()
        t0, M = time.monotonic(), self.r.mido.Message
        for t, kind, a, b in sorted(events, key=lambda e: e[0]):
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            self.r.out.send(M("note_on", note=a, velocity=b) if kind == "on" else
                            M("note_off", note=a) if kind == "off" else M("control_change", control=a, value=b))
        while (pos["u"] < nu or pos["j"] < nj) and time.monotonic() - t0 < dur + 5:
            time.sleep(0.01)
        s1.stop(); s1.close(); s2.stop(); s2.close()
        return hs.to_fs(bufs["u"][:, 0] / 32768.0, usr, dur), hs.to_fs(bufs["j"][:, self.jch].astype(float), jsr, dur)


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-15)


def band_db(x, f0, f1):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    m = (fr >= f0) & (fr < f1)
    # power in the band as an RMS level (Hann power gain 0.375)
    return 10 * np.log10(sp[m].sum() * 2 / (len(x) ** 2 * 0.375) + 1e-30)


def harm(x, f0):
    i = int(np.argmax(np.abs(x) > 0.003 * np.abs(x).max()))
    s = x[i + int(0.15 * FS):i + int(0.65 * FS)]
    sp = np.abs(np.fft.rfft(s * np.hanning(len(s))))
    fr = np.fft.rfftfreq(len(s), 1 / FS)
    H = [sp[(fr > k * f0 * 0.98) & (fr < k * f0 * 1.02)].max() for k in range(1, 7)]
    return [float(20 * np.log10(H[k] / H[0] + 1e-12)) for k in range(1, 6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--engine", default="rhodes", choices=("rhodes", "vibes"))
    a = ap.parse_args()
    J = Jack(a.dev, a.ch)
    if a.engine == "vibes":                       # every reset then selects VIBES (CC 79)
        dry0 = J.r.dry
        def dry(**kv):
            dry0(**kv)
            J.r.out.send(J.r.mido.Message("control_change", control=79, value=64))
            J.r.out.send(J.r.mido.Message("control_change", control=47, value=0))   # the motor off (it starts with VIBES)
            time.sleep(0.3)
        J.r.dry = dry
    rep = {}
    J.r.dry()
    # silence: the codec muted (exact digital zero)
    u, j = J.both(1.5, [])
    rep["silence"] = dict(jack_db=db(j), jack_hf_db=band_db(j, 8000, 20000))
    # hiss: under a low quiet note held (the codec unmuted; nothing of the music above 8 kHz)
    u, j = J.both(2.5, [(0.1, "on", 33, 30)])
    J.r.out.send(J.r.mido.Message("note_off", note=33))
    s = slice(int(0.6 * FS), int(2.4 * FS))
    rep["hiss"] = dict(usb_hf_db=band_db(u[s], 8000, 20000), jack_hf_db=band_db(j[s], 8000, 20000),
                       usb_db=db(u[s]), jack_db=db(j[s]))
    # max level: a loud chord
    J.r.dry()
    ch = (36, 43, 48, 52, 55, 60, 64)
    u, j = J.both(2.0, [(0.1, "on", n, 127) for n in ch] + [(1.6, "off", n, 0) for n in ch])
    s = slice(int(0.15 * FS), int(1.5 * FS))
    rep["loud"] = dict(usb_peak_db=float(20 * np.log10(np.abs(u).max() + 1e-12)), usb_db=db(u[s]),
                       jack_peak_db=float(20 * np.log10(np.abs(j).max() + 1e-12)), jack_db=db(j[s]))
    gain = rep["loud"]["jack_db"] - rep["loud"]["usb_db"]                 # jack re USB (interface gain etc.)
    rep["jack_re_usb_db"] = gain
    rep["snr_db"] = rep["loud"]["jack_db"] - rep["hiss"]["jack_hf_db"]
    rep["hiss_added_db"] = rep["hiss"]["jack_hf_db"] - (rep["hiss"]["usb_hf_db"] + gain)
    # harmonics
    rows = []
    for n in (33, 45):
        f0 = 440 * 2 ** ((n - 69) / 12)
        for v in (40, 80, 110, 127):
            u, j = J.both(1.2, [(0.1, "on", n, v), (1.0, "off", n, 0)])
            hu, hj = harm(u, f0), harm(j, f0)
            rows.append(dict(note=n, vel=v, usb=hu, jack=hj, diff_max=float(max(abs(x - y) for x, y in zip(hu, hj)))))
    rep["harmonics"] = rows
    J.r.dry()
    os.makedirs(os.path.join(hs.ROOT, "build", "hil"), exist_ok=True)
    json.dump(rep, open(os.path.join(hs.ROOT, "build", "hil", "jack.json" if a.engine == "rhodes" else "jack-vibes.json"), "w"), indent=1)
    print(f"silence      jack {rep['silence']['jack_db']:.1f} dB (8-20k {rep['silence']['jack_hf_db']:.1f})")
    print(f"hiss         8-20 kHz under a quiet low note: jack {rep['hiss']['jack_hf_db']:.1f} dB, USB "
          f"{rep['hiss']['usb_hf_db']:.1f} dBFS -> the jack adds {rep['hiss_added_db']:.1f} dB over the music's own")
    print(f"loud chord   USB peak {rep['loud']['usb_peak_db']:.1f} dBFS rms {rep['loud']['usb_db']:.1f}; jack peak "
          f"{rep['loud']['jack_peak_db']:.1f} rms {rep['loud']['jack_db']:.1f} (jack re USB {gain:.1f} dB)")
    print(f"SNR (jack)   loud chord RMS over the 8-20 kHz hiss: {rep['snr_db']:.1f} dB")
    for q in rows:
        print(f"  {q['note']} v{q['vel']:3d}  USB {' '.join('%5.1f' % x for x in q['usb'])} | jack "
              f"{' '.join('%5.1f' % x for x in q['jack'])} | worst diff {q['diff_max']:.1f} dB")


if __name__ == "__main__":
    main()
