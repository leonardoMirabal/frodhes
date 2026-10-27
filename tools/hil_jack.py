#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's headphone jack against its USB audio: hiss, signal-to-noise, headroom, harmonic match.

Records the USB audio and an audio interface input (the jack) at the same time while MIDI plays the FM-1.

  hil_jack.py [--dev NAME] [--ch N] [--engine rhodes|vibes|marimba] [--dry-run]
                                         (default: the interface "M6", input 5 -> --ch 5)

--engine marimba runs its own session (ST77, MARIMBA, ST77 again: SNR within 1 dB of ST77, the drift; the muted and
idle floors, USB exact zero at idle; the silence gate on MARIMBA tails, TEST-PLAN A5) -> build/hil/jack-marimba.json.
--dry-run: no device (the host build plays the MIDI; the jack is simulated): checks the procedure, writes build/hil/dry/.

Reports (all jack figures re the interface's full scale, so they depend on its input gain; the differences are
what matter):
  silence        jack floor with the codec muted (the interface's own floor)
  hiss           8-20 kHz under a low quiet note (no music there): jack vs USB -> the jack's own hiss
  max level      a loud chord's peak and RMS on the jack (and on USB)
  snr            loud-chord RMS minus the hiss (A-weighting not applied)
  harmonics      A1 / A2 at 4 velocities: H2..H6 re H1 on USB and on the jack (distortion added by the jack)
Results also go to build/hil/jack.json (jack-vibes.json with --engine vibes; --engine marimba: jack-marimba.json and its
tail takes, USB + jack, in build/hil/jack-tails/*.npz).
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
        to FS afterwards. With the rig's persistent recorder (hs.Rig.persistent, the default): slices of its streams,
        open for the whole run (no stream opened per take: the FM-1 keeps its automatic flash saves back while a
        computer reads its USB audio, and a save inside a take dropped ~250 ms of USB)"""
        if getattr(self.r, "persistent", False):
            self.r.add_jack(self.jdev, self.jch)
            d = self.r.both(dur, events, jack=True)
            return d["usb"][:, 0], d["jack"][:, 0]
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


class DryJack:
    """--dry-run: the same calls without the device. USB = the host build playing the same MIDI (hil_scan.HostRig:
    the panel state replayed, 44.1 -> 48 kHz); the 'jack' = USB - 3 dB + a seeded white floor at -100 dBFS while USB
    is not exact zero and -110 dBFS while it is (a codec mute that follows the output exactly). Every jack figure of a
    dry run is therefore SIMULATED: the run checks the procedure, the events and the measures, never the FM-1"""

    def __init__(self, seed=1):
        self.r = hs.HostRig(seed=seed, reset=False, preroll=(300, 301), lat=(5, 6), tag="jackdry")
        self.rng = np.random.default_rng(seed)

    def both(self, dur, events):
        x = self.r._rec(dur, sorted(events, key=lambda e: e[0]))
        u = x[:, 0] / 32768.0
        fl = np.where(u != 0, 10 ** (-100 / 20), 10 ** (-110 / 20)) * np.sqrt(3)
        return u, u * 10 ** (-3 / 20) + fl * self.rng.uniform(-1, 1, len(u))


def frames_db(x, ms=10):
    n = int(FS * ms / 1000)
    m = len(x) // n
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x[:m * n]).reshape(m, n) ** 2, axis=1)) + 1e-15)


def lufs48(x):
    """BS.1770 integrated over the whole slice (no gating), K-weighted at FS"""
    import bar_measure as bm
    from scipy.signal import sosfilt
    y = sosfilt(bm.k_weight_sos(FS), np.asarray(x, float))
    return -0.691 + 10 * np.log10(float(np.mean(y * y)) + 1e-30)


def hp20(x):
    """a 20 Hz 2nd-order high-pass (zero phase): the jack's after-mute check looks at the audible band only (the
    codec's unmute DC step at a strike settles through the AC coupling ~100 ms later: -87 dBFS below 20 Hz, seen
    on a 0.1 s C7 note as a 'bump' after its end)"""
    from scipy.signal import butter, sosfiltfilt
    return sosfiltfilt(butter(2, 20, "highpass", fs=FS, output="sos"), np.asarray(x, float))


def gate_tail(u, j, t_note):
    """TEST-PLAN A5 on one tail: USB (full scale 1.0) and the jack. The mute instant z = the first sample after which
    USB is exact zero to the end. 'gate' when the 200 ms before z - 46 ms stayed <= 16 LSB (fx.c: a 46 ms linear fade
    after 203 ms under 16 LSB ends at z), else 'decayed' (the tail itself rounded to zero first, e.g. a hand mute);
    on a gate the 50 ms before the fade start [z - 96 ms, z - 46 ms] peak <= 16 LSB. Jack, 10 ms frames from z: none
    of the first 100 ms more than 6 dB over the muted floor (z + 300 .. 1000 ms): the codec's own mute transient;
    hiss_step = the 100 ms before z - 50 ms minus that floor (what stops when the codec mutes).
    A tail that ends by its own decay (a MARIMBA top note: C7's V1 T60 is 0.11 s, so a v8 note reaches exact zero
    ~0.1 s after the strike, before the gate's 203 ms) never meets the gate: its fixed windows then lie over the note
    itself (pre_fade_peak_lsb is the strike, jack_hiss_step_db the note, not hiss: hiss_window_has_note). What tells a
    cut from a decay is end_peak_lsb, USB's peak in the last 10 ms before z: <= 16 LSB on a decayed tail (a voice
    freed while still sounding ends above it), as the gate's own limit"""
    nz = np.flatnonzero(u != 0)
    if not len(nz) or nz[-1] >= len(u) - int(1.05 * FS):
        return dict(ok=False, why="no exact zero (with 1 s after it) inside the take: record longer")
    z = int(nz[-1]) + 1
    ms = lambda t: int(t * FS / 1000)                                  # noqa: E731
    pre = float(np.abs(u[max(0, z - ms(96)):z - ms(46)]).max() * 32768)
    w = u[max(0, z - ms(246)):z - ms(46)]
    gated = bool(np.abs(w).max() * 32768 <= 16.5 and np.any(w != 0))   # (all zero: before the note, z under 246 ms)
    last60 = np.flatnonzero(np.abs(u) > 10 ** (-60 / 20))
    floor = float(np.median(frames_db(j[z + ms(300):z + ms(1000)])))
    jh = hp20(j)                                       # the codec's unmute step settles below 20 Hz (inaudible)
    after = float(frames_db(jh[z:z + ms(100)]).max() - float(np.median(frames_db(jh[z + ms(300):z + ms(1000)]))))
    hiss = float(np.median(frames_db(j[max(0, z - ms(150)):z - ms(50)])) - floor)
    m16 = u * 32768
    end = float(np.abs(u[max(0, z - ms(10)):z]).max() * 32768)
    clk = int((np.abs(np.diff(m16, 2)) > 6 * (np.convolve(np.abs(m16), np.ones(480) / 480, "same")[1:-1] + 50) + 2000).sum())
    return dict(kind="gate" if gated else "decayed", zero_s=round(z / FS - t_note, 3),
                last_over_60dbfs_s=round(float(last60[-1]) / FS - t_note, 3) if len(last60) else None,
                pre_fade_peak_lsb=round(pre, 1), end_peak_lsb=round(end, 1), jack_after_mute_over_floor_db=round(after, 1),
                jack_hiss_step_db=round(hiss, 1), jack_floor_db=round(floor, 1), usb_clicks=clk,
                hiss_window_has_note=bool(len(last60) and last60[-1] >= z - ms(150)),
                ok=bool((pre <= 16.5 if gated else end <= 16.5) and after <= 6.0 and clk == 0))


def marimba(J, a, out):
    """--engine marimba (PLAN §12.5, TEST-PLAN §6.3 / A5): ST77, MARIMBA, ST77 again in one session (the drift check);
    SNR = the loud chord's jack RMS (and LUFS) over the jack's 8-20 kHz hiss. A MARIMBA note does not sustain (a low
    quiet one ends in the gate and the codec mutes inside the hiss window), so MARIMBA's SNR is taken over the ST77
    hiss of the same session (the codec's analog hiss is the engine's no more); MARIMBA's own hiss under a pedalled
    RING 127 note is reported with the fraction of the window the codec was live. Then the muted floor, the silence
    gate on MARIMBA tails (A5, MUTE 0: the tails ring out), the idle floor and USB exact zero at idle.
    Results to build/hil/jack-marimba.json"""
    M = J.r.mido.Message
    dry0 = J.r.dry

    def sel(engine):
        def dry(**kv):
            dry0(**kv)
            if engine == "marimba":
                J.r.out.send(M("control_change", control=79, value=127))    # MARIMBA (no motor: CC 47 inert)
            time.sleep(0.3)
        J.r.dry = dry

    def block(engine):
        sel(engine)
        J.r.dry()
        u, j = J.both(1.5, [])
        sil = dict(jack_db=db(j), jack_hf_db=band_db(j, 8000, 20000), usb_nonzero=int(np.count_nonzero(u)))
        ev = [(0.1, "on", 33, 30)]
        if engine == "marimba":                    # (the pedal and RING 127 keep it sounding as long as they can)
            ev = [(0.0, "cc", 102, 127), (0.0, "cc", 64, 127)] + ev
        u, j = J.both(2.5, ev)
        J.r.out.send(M("note_off", note=33))
        J.r.out.send(M("control_change", control=64, value=0))
        s = slice(int(0.6 * FS), int(2.4 * FS))
        hiss = dict(usb_hf_db=band_db(u[s], 8000, 20000), jack_hf_db=band_db(j[s], 8000, 20000), usb_db=db(u[s]),
                    jack_db=db(j[s]), usb_live_frac=round(float(np.mean(u[s] != 0)), 3))
        J.r.dry()
        ch = (36, 43, 48, 52, 55, 60, 64)
        u, j = J.both(2.0, [(0.1, "on", n, 127) for n in ch] + [(1.6, "off", n, 0) for n in ch])
        s = slice(int(0.15 * FS), int(1.5 * FS))
        loud = dict(usb_peak_db=float(20 * np.log10(np.abs(u).max() + 1e-12)), usb_db=db(u[s]),
                    jack_peak_db=float(20 * np.log10(np.abs(j).max() + 1e-12)), jack_db=db(j[s]),
                    usb_lufs=lufs48(u[s]), jack_lufs=lufs48(j[s]), usb_full_scale=int(np.sum(np.abs(u) >= 32767 / 32768)))
        return dict(silence=sil, hiss=hiss, loud=loud, jack_re_usb_db=loud["jack_db"] - loud["usb_db"])

    rep = dict(engine="marimba", dry_run=bool(a.dry_run))
    rep["st77"] = block("rhodes")
    rep["marimba"] = block("marimba")
    rep["st77_again"] = block("rhodes")
    hs_ = (rep["st77"]["hiss"]["jack_hf_db"] + rep["st77_again"]["hiss"]["jack_hf_db"]) / 2   # the session's hiss
    for k in ("st77", "marimba", "st77_again"):
        q = rep[k]
        q["snr_db"] = q["loud"]["jack_db"] - hs_
        q["snr_lufs_db"] = q["loud"]["jack_lufs"] - hs_
        q["hiss_added_db"] = q["hiss"]["jack_hf_db"] - (q["hiss"]["usb_hf_db"] + q["jack_re_usb_db"])
    st = (rep["st77"]["snr_db"] + rep["st77_again"]["snr_db"]) / 2
    stl = (rep["st77"]["snr_lufs_db"] + rep["st77_again"]["snr_lufs_db"]) / 2
    rep["session_hiss_db"] = hs_
    rep["snr_vs_st77_db"] = rep["marimba"]["snr_db"] - st
    rep["snr_lufs_vs_st77_db"] = rep["marimba"]["snr_lufs_db"] - stl
    rep["st77_drift_db"] = rep["st77_again"]["snr_db"] - rep["st77"]["snr_db"]
    rep["snr_ok"] = bool(abs(rep["snr_vs_st77_db"]) <= 1.0)
    rep["hiss_equal_db"] = rep["marimba"]["hiss"]["jack_hf_db"] - hs_
    # the silence gate on MARIMBA tails (A5), MUTE 0 (rings out): the longest (marimba_scan PROFILE long_tail), C7 v8
    # (the codec's mute on the shortest, quietest tail), C4 v100
    sel("marimba")
    mute0 = [(0.0, "cc", 103, 0)]
    tails = {"C2 RING 127 TUBE 127 pedal": (10.0, mute0 + [(0.0, "cc", 102, 127), (0.0, "cc", 30, 127), (0.0, "cc", 64, 127),
                                                           (0.1, "on", 36, 100), (0.4, "off", 36, 0)]),
             "C7 v8 MUTE 0": (3.0, mute0 + [(0.1, "on", 96, 8), (0.3, "off", 96, 0)]),
             "C4 v100 MUTE 0": (4.5, mute0 + [(0.1, "on", 60, 100), (0.6, "off", 60, 0)])}
    rep["tails"] = {}
    for nm, (dur, ev) in tails.items():
        J.r.dry()
        u, j = J.both(dur, ev)
        J.r.out.send(M("control_change", control=64, value=0))
        rep["tails"][nm] = gate_tail(u, j, 0.1)
        td = os.path.join(os.path.dirname(out), "jack-tails")          # the takes, for a look at the mute instant
        os.makedirs(td, exist_ok=True)
        np.savez_compressed(os.path.join(td, "marimba-" + "".join(c if c.isalnum() else "_" for c in nm) + ".npz"),
                            usb=u.astype(np.float32), jack=np.asarray(j, np.float32), fs=FS)
    rep["tails_ok"] = all(q.get("ok") for q in rep["tails"].values())
    # idle: everything off, 3 s; USB exact zero, the jack at the codec-muted floor
    J.r.dry()
    u, j = J.both(3.0, [])
    rep["idle"] = idle_judge(int(np.count_nonzero(u)), db(j), band_db(j, 8000, 20000),
                             [rep[k]["silence"] for k in ("st77", "st77_again", "marimba")])
    rep["idle_ok"] = rep["idle"]["ok"]
    J.r.dry = dry0
    J.r.dry()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, "w"), indent=1)
    tag = "DRY RUN (host build; the jack SIMULATED) " if a.dry_run else ""
    print(f"{tag}MARIMBA jack session -> {out}")
    for k in ("st77", "marimba", "st77_again"):
        q = rep[k]
        print(f"  {k:10s} muted floor jack {q['silence']['jack_db']:.1f} dB (8-20k {q['silence']['jack_hf_db']:.1f}); hiss "
              f"8-20k jack {q['hiss']['jack_hf_db']:.1f} / USB {q['hiss']['usb_hf_db']:.1f} (codec live "
              f"{100 * q['hiss']['usb_live_frac']:.0f} %); loud chord USB rms {q['loud']['usb_db']:.1f} peak "
              f"{q['loud']['usb_peak_db']:.1f}, jack rms {q['loud']['jack_db']:.1f} LUFS {q['loud']['jack_lufs']:.1f}; "
              f"SNR {q['snr_db']:.1f} dB (LUFS {q['snr_lufs_db']:.1f})")
    print(f"  SNR MARIMBA - ST77 {rep['snr_vs_st77_db']:+.2f} dB (within 1 dB: {'yes' if rep['snr_ok'] else 'NO'}), "
          f"on LUFS {rep['snr_lufs_vs_st77_db']:+.2f}; ST77 drift {rep['st77_drift_db']:+.2f} dB (TEST-PLAN: +-0.3); "
          f"MARIMBA's hiss re the session's {rep['hiss_equal_db']:+.1f} dB")
    for nm, q in rep["tails"].items():
        print(f"  tail {nm:28s} " + ", ".join(f"{k} {v}" for k, v in q.items()))
    print(f"  idle         USB nonzero samples {rep['idle']['usb_nonzero']}; jack {rep['idle']['jack_db']:.1f} dB "
          f"({rep['idle']['jack_re_muted_db']:+.1f} re the muted floor) -> {'ok' if rep['idle_ok'] else 'FAIL'}")
    return rep


def idle_judge(usb_nonzero, jack_db, jack_hf_db, silences):
    """the idle gate (TEST-PLAN A5): USB exact zero and the jack at the session's muted floor. The reference is the
    median of the session's silent takes (all exact zero on USB): one 3 s take of the interface's analog floor
    wanders by ~2 dB (0.9.7 T2: -91.3 / -90.9 / -93.2 dB, the same digital zero), so a single take as the reference
    failed a silent FM-1. Noise from the FM-1 (a dither left on, hiss) raises the 8-20 kHz band too: gated as well."""
    ref = float(np.median([q["jack_db"] for q in silences]))
    ref_hf = float(np.median([q["jack_hf_db"] for q in silences]))
    d = dict(usb_nonzero=usb_nonzero, jack_db=jack_db, jack_hf_db=jack_hf_db, ref_db=ref, ref_hf_db=ref_hf,
             jack_re_muted_db=jack_db - ref, jack_hf_re_muted_db=jack_hf_db - ref_hf)
    d["ok"] = bool(usb_nonzero == 0 and abs(d["jack_re_muted_db"]) <= 1.0 and d["jack_hf_re_muted_db"] <= 1.0)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--engine", default="rhodes", choices=("rhodes", "vibes", "marimba"))
    ap.add_argument("--dry-run", action="store_true", help="no device: the host build plays, the jack is simulated; "
                    "results to build/hil/dry/")
    a = ap.parse_args()
    J = DryJack() if a.dry_run else Jack(a.dev, a.ch)
    if a.engine == "marimba":
        return marimba(J, a, os.path.join(hs.ROOT, "build", "hil", *(["dry"] if a.dry_run else []), "jack-marimba.json"))
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
    hd = os.path.join(hs.ROOT, "build", "hil", *(["dry"] if a.dry_run else []))
    os.makedirs(hd, exist_ok=True)
    json.dump(rep, open(os.path.join(hd, "jack.json" if a.engine == "rhodes" else "jack-vibes.json"), "w"), indent=1)
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
