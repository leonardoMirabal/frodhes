#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The setup's two flash sectors (firmware/src/set_store.c, 0.9.5) on the FM-1: a setup change saved, the FM-1
restarted, the change still there; four times, so each sector is written twice and the load picks the newer each time
(A -> B -> A -> B, wherever it starts). The restart is a reinstall of the same package (fm1_install.py: the update
loader restarts the FM-1; the data is not touched). A power cut in the middle of a save cannot be made from here: that
case is host-proven only (tests/set_ab_test.c, every slice of a save).

    tools/hil_setup_ab.py --fwsc PKG [--rounds 4] [--dry-run]

The setup item: chord mode (CC 56, a setup item since 0.9.5's settings_record): 3 (MAJ) or 0 (OFF). One C4 at v100
on F01 (Program Change 0, NOISE CC 104 0: a clean sound; F06's drive and modulation smeared the first FM-1 run's
spectrum over six semitones), recorded on USB, aligned on its onset. The verdict is by the references: the take's
semitone-band profile (dB per semitone band, MIDI 24..108, against its loudest) nearer the ON or the OFF one. The
references must differ by >= REF_DIST_DB (else exit 2) and a take must be nearer one by >= MARGIN_DB. Information:
chord_db, the loudest band 1..11 semitones above the root against the root (one note: nothing there; a chord's
third and fifth: about 0 dB).

    preflight  CC 64 0 (the pedal up, the REC latch released), CC 56 0, 5 s; the start sound's fingerprint
               (tools/hil_midi_parity.py's: C4 v100 after a CC 10 reset); then F01 (PC 0), NOISE 0, 5 s (the
               setup's save: F01 is the sound last loaded); reference OFF, CC 56 3, 5 s, reference ON
    round k    CC 56 = 3 / 0 / 3 / 0, 5 s silent (the save 2 s after the change), restart (reinstall); NOISE 0 (a
               sound knob: never saved, F01's own after the restart), the note must sound as the value set
    the end    CC 56 0 sent and saved: chord mode off; the start sound back: Program Change 0..34 until one's
               fingerprint matches the start's (<= 0.5 dB, hil_midi_parity's rule), else the closest, said loudly;
               5 s (its save). The CHORD page's "last type" (the one EDIT turns back on) is left at MAJ: CC 56 sets
               it and it cannot be read back over MIDI

Exit 0 every round as set; 1 a FAIL; 2 the references could not be told apart. build/hil/setup_ab/report.txt
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CC_CHORD, ON, OFF = 56, 3, 0
NOTE = [(0.0, "on", 60, 100), (1.3, "off", 60, 0)]
CC_NOISE = 104
CHORD_DB = -12.0                                       # chord_db above: a chord (information)
ROOT_DB = -20.0                                        # the root: the lowest band within 20 dB of the loudest
REF_DIST_DB = 10.0                                     # the references' profiles at least this far apart (RMS dB)
MARGIN_DB = 2.0                                        # a take this much nearer one reference than the other
FLOOR_DB = -40.0                                       # the profile below the loudest band, floored here
SAVE_WAIT_S = 5.0                                      # the auto save: 2 s after the change, once silent
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "hil", "setup_ab")


def semis(x, sr):
    """x's energy (dB) per semitone band, MIDI notes 24..108 (A440: each band a semitone wide around the note, so a
    chorus or a vibrato's smear stays in its band)"""
    x = np.asarray(x, np.float64)
    P = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    notes = np.arange(24, 109)
    fc = 440.0 * 2 ** ((notes - 69) / 12)
    e = np.array([P[(f >= c * 2 ** (-1 / 24)) & (f < c * 2 ** (1 / 24))].sum() for c in fc])
    return notes, 10 * np.log10(e + 1e-20)


def chord_db(x, sr):
    """x (mono, the note's first second): the loudest semitone band 1..11 above the root's band, dB against the
    root's. The root: the lowest band within ROOT_DB of the loudest (a Rhodes note's second harmonic can be louder
    than its fundamental; a chord's root is its lowest note). One note's harmonics fall 12, 19, 24 ... semitones
    above it, none 1..11; a chord has its third and fifth there. Returns (dB, the root's frequency)"""
    notes, e = semis(x, sr)
    root = int(np.nonzero(e >= e.max() + ROOT_DB)[0][0])
    above = e[root + 1:root + 12]
    return float(above.max() - e[root]), 440.0 * 2 ** ((notes[root] - 69) / 12)


def profile(x, sr):
    _, e = semis(x, sr)
    return np.maximum(e - e.max(), FLOOR_DB)


def prof_dist(a, b, use=None):
    """RMS dB between two profiles, over the bands `use` (default: where either is above the floor)"""
    a, b = np.asarray(a), np.asarray(b)
    use = (np.maximum(a, b) > FLOOR_DB) if use is None else use
    return float(np.sqrt(np.mean((a[use] - b[use]) ** 2)))


def verdict(p, ref_on, ref_off):
    """'chord', 'one note' or 'undecided': the reference the profile p is nearer, by MARGIN_DB (over the bands the
    references have above the floor)"""
    use = np.maximum(ref_on, ref_off) > FLOOR_DB
    d_on, d_off = prof_dist(p, ref_on, use), prof_dist(p, ref_off, use)
    if abs(d_on - d_off) < MARGIN_DB:
        return "undecided", d_on, d_off
    return ("chord" if d_on < d_off else "one note"), d_on, d_off


def onset_cut(x, sr, t, secs=1.0):
    """x from the note's onset (the first sample over a tenth of the peak, from 50 ms before t) + 30 ms, secs long"""
    i0 = int(max(0.0, t - 0.05) * sr)
    seg = np.abs(x[i0:])
    k = int(np.argmax(seg >= 0.1 * seg.max())) if seg.size and seg.max() > 0 else 0
    j = i0 + k + int(0.03 * sr)
    return x[j:j + int(secs * sr)]


def is_chord(db):
    return db > CHORD_DB


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fwsc", required=True, help="the package on the FM-1 (reinstalled to restart it)")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    vals = [ON if k % 2 == 0 else OFF for k in range(a.rounds)]
    if vals[-1] != OFF:
        vals.append(OFF)                               # (always end with chord mode off, saved and restarted)
    plan = ["preflight: CC 64 0, CC 56 0, 5 s, the start sound's fingerprint; F01 (PC 0), NOISE 0, 5 s, "
            "references OFF and ON"] + \
           [f"round {k + 1}: CC 56 {v}, {SAVE_WAIT_S:g} s, restart (reinstall {os.path.basename(a.fwsc)}), the note "
            f"must sound {'a chord' if v else 'one note'}" for k, v in enumerate(vals)]
    if a.dry_run:
        print("\n".join(plan))
        print("the end: CC 56 0 (saved), the start sound back by its fingerprint (PC 0..34)")
        print(f"~{len(vals) * 100 // 60 + 2} min (an install ~80 s, the waits, the restore)")
        return 0
    if not os.path.exists(a.fwsc):
        raise SystemExit(f"no {a.fwsc}")
    import hil_midi_parity as hp
    import hil_must as hm
    a.prev = None
    m = hm.Must(a)
    os.makedirs(OUT, exist_ok=True)
    lines, fails = [time.strftime("%Y-%m-%d %H:%M:%S") + f"  {os.path.basename(a.fwsc)}, {m.info()}"], 0

    def say(t):
        lines.append(t)
        print(t, flush=True)

    def record(events, secs=3.0):
        """events [(t, kind, a, b)] played 0.6 s into a USB take: (mono x, sr, the first event's time in x)"""
        u = m.open_usb(48000, secs, any_rate=True)
        time.sleep(0.6)
        t = u.mark()
        hm.play(m.dev, events)
        time.sleep(max(0.0, secs - 0.6 - events[-1][0] - 0.2))
        x = u.stop()[:, 0]
        return x, u.sr, t

    def take(what):
        m.dev.cc(CC_NOISE, 0)
        time.sleep(0.3)
        x, sr, t = record(NOTE)
        x = onset_cut(x, sr, t)
        np.save(os.path.join(OUT, f"{what}.npy"), x)
        db, f0 = chord_db(x, sr)
        p = profile(x, sr)
        return p, db, f0, float(np.abs(x).max())

    def fp_take():
        x, sr, t = record([(0.0, "cc", 10, 127), (0.05, "on", 60, 100), (0.65, "off", 60, 0)])
        return hp.fingerprint(x, sr, t + 0.05)

    def set_chord(v):
        m.dev.cc(64, 0)
        m.dev.cc(CC_CHORD, v)
        time.sleep(SAVE_WAIT_S)                        # nothing plays: the save 2 s after the change

    fp0, code = None, 1                            # (an error on the way: FAIL)
    try:
        set_chord(OFF)
        fp0 = fp_take()
        say(f"the start sound: {fp0['level_db']:.1f} dB (its fingerprint, for the restore)")
        m.dev.send("pc", 0)                            # F01: a clean sound (CHANNEL ALL: channel 1 is heard)
        time.sleep(0.4)
        m.dev.cc(CC_NOISE, 0)
        time.sleep(SAVE_WAIT_S)                        # (the setup's save: F01 the sound last loaded)
        r_off, db, f0, pk = take("ref-off")
        say(f"ref-off: chord_db {db:+.1f} dB above the root {f0:.1f} Hz (peak {pk:.3f})")
        set_chord(ON)
        r_on, db, f0, pk = take("ref-on")
        say(f"ref-on: chord_db {db:+.1f} dB above the root {f0:.1f} Hz (peak {pk:.3f})")
        time.sleep(2.0)                                # (the note's tail)
        d = prof_dist(r_on, r_off)
        say(f"the references {d:.1f} dB apart (>= {REF_DIST_DB:g})")
        if d < REF_DIST_DB:
            say("FAIL: the references are not told apart")
            code = 2
            return code
        for k, v in enumerate(vals):
            if k:
                set_chord(v)
            m.install(a.fwsc)                          # the restart: the loader writes the app, the data stays
            p, db, f0, pk = take(f"round{k + 1}-after-restart-{'on' if v else 'off'}")
            got, d_on, d_off = verdict(p, r_on, r_off)
            ok = got == ("chord" if v else "one note")
            fails += not ok
            say(f"round {k + 1}: CC 56 {v} saved, restarted: {got} (to ON {d_on:.1f} dB, to OFF {d_off:.1f} dB; "
                f"chord_db {db:+.1f}) -> {'ok' if ok else 'FAIL'}")
        code = 1 if fails else 0
    finally:
        try:
            set_chord(OFF)                             # the player's chord mode off, saved
        except Exception as e:  # noqa: BLE001
            say(f"could not restore CC 56 0: {e!r}: set CHORD TYPE OFF by hand")
        if fp0 is not None:
            try:
                best = None
                for pr in range(hp.N_PROGRAMS):
                    m.dev.send("pc", pr)
                    time.sleep(0.4)
                    dist = hp.fp_distance(fp0, fp_take())
                    if best is None or dist < best[0]:
                        best = (dist, pr)
                    if dist <= 0.5:
                        break
                dist, pr = best
                if dist > 0.5:
                    m.dev.send("pc", pr)
                name = f"F{pr + 1:02d}" if pr < 16 else f"U{pr - 15:02d}" if pr < 32 else f"F{pr - 15:02d}"
                say(f"the start sound restored: Program Change {pr} ({name}), {dist:.2f} dB from it" if dist <= 0.5
                    else f"WARNING: the start sound matched no stored program (an unsaved edit): left on {name}, "
                         f"the closest ({dist:.1f} dB)")
                time.sleep(SAVE_WAIT_S)                # (its save: the sound last loaded)
            except Exception as e:  # noqa: BLE001
                say(f"WARNING: the start sound not restored ({e!r}): load it by hand")
        say("left: chord mode OFF (saved); the CHORD page's last type (EDIT turns it back on) is MAJ")
        with open(os.path.join(OUT, "report.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"{['PASS', 'FAIL', 'SETUP'][code]}: {len(vals)} restarts, {fails} wrong ({OUT}/report.txt)")
    return code


if __name__ == "__main__":
    sys.exit(main())
