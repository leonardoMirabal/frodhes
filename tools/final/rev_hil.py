#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The reverb TYPEs on the FM-1 (run_final_rev.sh; private/reverb/PLAN.md section 6 test 9, FM-1 wave 3):

  stress   stress_max (tools/hil_crackle.py, unchanged: its rig, the CC 116 counters every 0.5 s and every detector:
           repeat, stale, zero, clip, click, step and the jack-vs-USB track) with the reverb TYPE INFINITE in every
           take (CC <P_RV_TYPE> after the setup CCs): the worst-case chords with every effect heavy (HEAVY's
           RV_DECAY 127 = INFINITE frozen) at full level, then the moderate-effects recipe (DECAY as the default:
           INFINITE not frozen); every engine. --type PLATE (or any TYPE) instead of INFINITE; --quick: 20 s worst
           + 24 s recipe per engine (run_final_rev.sh's early mode). --full: hil_crackle's own takes, all of them
           (worst at LEVEL 0 and full, recipe dry / fx, the no-USB recipe; its self-test unless --no-self-test), with
           every effect as hil_cpu_fx sets it: the heaviest drive TYPE, TAPE, the heaviest reverb TYPE (--type) and
           LOW CUT wherever the take has that effect on; its report in build/hil/stress_max/<rate>/ (run_final_rev.sh
           stress_48000 / stress_44100). The report in build/hil/stress_rev/<rate>/.
  jack     takes on the jack + USB at once, judged by the same detectors, every take saved whole (USB and jack WAVs)
           in --out (default <main checkout>/private/reverb/takes/<rate>/): each reverb TYPE x 3 settings (short /
           default / long), INFINITE frozen and held 60 s (its level per 5 s: +-1 dB, no growth; its DC), and the TYPE
           switched while playing (HALL, PLATE, INFINITE, OFF in turn, every 3 s). The hiss check per take, as
           hil_jack's: the jack's 8-20 kHz band against USB's in the take's last 1.5 s (the tail) and the jack's
           level re USB from the loud part (the jack's own hiss = jack 8-20k - (USB 8-20k + that gain)); compare
           by hand with 0.9.4's build/hil/jack.json (idle floor -99.8 dBFS, SNR 74 dB). The report: rev_jack.txt /
           .json there. The listening round (PLAN section 7 wave 4) uses these WAVs.

    TREE=<release tree> rev_hil.py stress|jack [--rate 48000] [--engine rhodes] [--type T] [--quick] [--jack-dev M6] [--jack-ch 5]
                                    [--out DIR] [--seconds 20] [--dry-run] [--host]
--dry-run: the takes and their time; no MIDI, no audio (a tree without [P_RV_TYPE] in params.c: planned with the
contract's names OFF / HALL / PLATE / INFINITE and a placeholder CC, printed as such; a device run FAILs there).
--host: the host build plays (the jack simulated)."""
import argparse
import json
import os
import subprocess
import sys
import wave

import numpy as np

TREE = os.path.abspath(os.environ.get("TREE") or os.getcwd())
sys.path.insert(0, os.path.join(TREE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FS_JACK = 48000
TAIL_S = 4.0                                   # each jack take ends with this much tail (no notes, pedal up)
SETTINGS = (                                   # (name, the REVERB / REVERB 2 knobs as CC values; None: the default)
    ("short", dict(RV_DEPTH=60, RV_DECAY=30, RV_TONE=40, RV_PREDLY=0, RV_SIZE=None, RV_MOD=None, RV_LOCUT=None)),
    ("default", dict(RV_DEPTH=90, RV_DECAY=64, RV_TONE=64, RV_PREDLY=20, RV_SIZE=None, RV_MOD=None, RV_LOCUT=None)),
    ("long", dict(RV_DEPTH=127, RV_DECAY=110, RV_TONE=110, RV_PREDLY=40, RV_SIZE=127, RV_MOD=127, RV_LOCUT=40)))
CONTRACT = ["OFF", "HALL", "PLATE", "INFINITE"]
INF_STRESS_DECAY = 126                         # the stress takes' INFINITE: SUSTAIN (gain 1, the input open, the tank full
                                               # of the take's chords), not FREEZE (127: the input closed from the set-up)
FREEZE_MIN_DB = 90.3 - 50.0                    # the held freeze's every 5 s window at least -50 dBFS (here dB re 1 LSB RMS;
                                               # full scale 90.3) and within +-1 dB: a freeze that holds nothing FAILs


def main_checkout():
    try:
        c = subprocess.run(["git", "-C", TREE, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, check=True).stdout.strip()
        return os.path.dirname(c)
    except (OSError, subprocess.CalledProcessError):
        return TREE


def write_wav(path, x, fs):
    y = np.clip(np.round(np.asarray(x, float)), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1 if y.ndim == 1 else y.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(y.tobytes())


def db(x):
    return float(20 * np.log10(np.sqrt(np.mean(np.asarray(x, float) ** 2)) + 1e-12))


def band_db(x, fs, f0, f1):
    """as hil_jack.band_db (the power in the band as an RMS level, Hann power gain 0.375), at any rate"""
    x = np.asarray(x, float)
    if len(x) < 64:
        return None
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / fs)
    m = (fr >= f0) & (fr < f1)
    return float(10 * np.log10(sp[m].sum() * 2 / (len(x) ** 2 * 0.375) + 1e-30))


def reverb():
    """(cc, {TYPE name: cc value}, {knob: cc}, notes): the tree's reverb TYPE and knobs (hil_cpu_fx's parser), or
    the contract's placeholders (cc None) when the tree has none"""
    import hil_cpu_fx as F
    notes, knobs = [], {}
    for k in ("RV_DEPTH", "RV_DECAY", "RV_TONE", "RV_PREDLY", "RV_SIZE", "RV_MOD", "RV_LOCUT"):
        e = F.param_entry(k)
        if e and e[3]:
            knobs[k] = e[3]
        else:
            notes.append(f"no [P_{k}] with a CC in params.c: that knob is left as it is")
    cc, types = F.rev_cc(), {t[0]: t[3] for t in F.rev_types()}
    off = None
    e = F.param_entry("RV_TYPE")
    if cc and types:
        tab = F._band_table("P_RV_TYPE")
        off = (F.bands(tab)[0][0] + F.bands(tab)[0][1]) // 2 if tab else 0
    else:
        notes.append("no [P_RV_TYPE] in this tree's params.c: planned with the contract's TYPEs and CC ?? "
                     "(a device run FAILs)")
        types = {n: 32 * i + 16 for i, n in enumerate(CONTRACT) if i}
    types = dict(OFF=off if off is not None else 0, **types)
    return cc, types, knobs, notes, e


def stress_full(a, C):
    """hil_crackle unchanged, its own takes (every one, the self-test too unless --no-self-test), with "every effect" as
    hil_cpu_fx sets it wherever a take has that effect on: the heaviest drive TYPE (DRIVE > 0), TAPE (DL_SW > 0), the
    heaviest reverb TYPE (or --type) + LOW CUT on (REV > 0). The report in build/hil/stress_max/<rate>/ as always"""
    import hil_cpu_fx as F
    add, notes, inf_pick = [], [], False
    dt = sorted([t for t in F.drive_types() if t[2] is not None], key=lambda t: -t[2])[:1]
    if dt:
        add.append(("DRIVE", 84, dt[0][3]))
        notes.append(f"drive {dt[0][0]} (CC 84 = {dt[0][3]})")
    tv = F.tape_cc()
    if tv is not None:
        add.append(("DL_SW", 88, tv))
        notes.append(f"delay TAPE (CC 88 = {tv})")
    pick, _ = F.rev_pick(a.type)
    rc = F.rev_cc()
    if pick and rc:
        add.append(("REV", rc, pick[3]))
        notes.append(f"reverb {pick[0]} (CC {rc} = {pick[3]}, {pick[2]} us{'' if a.type else ', the heaviest'})")
        lc = F.param_entry("RV_LOCUT")
        if lc and lc[3]:
            add.append(("REV", lc[3], 64))
            notes.append(f"LOW CUT on (CC {lc[3]} = 64)")
        inf_pick = pick[0].upper().startswith("INF")
        if inf_pick:
            notes.append(f"DECAY {INF_STRESS_DECAY} where the take has 127 (INFINITE SUSTAIN: 127 is FREEZE, its input "
                         "closed before the first note, the tank empty all take)")
    elif not a.dry_run:
        print("rev_hil: FAIL: no reverb TYPE in this tree's params.c (stress --full needs [P_RV_TYPE])")
        return 1
    else:
        notes.append("no reverb TYPE in this tree (dry run: HALL as hil_crackle sends it)")
    orig = C.takes
    fold = a.fold and a.rate == "48000"
    if a.fold and not fold:
        notes.append("--fold only at 48 kHz (hil_jack's own rate): not here")

    def full_takes(eng, *x, **k):
        out = []
        for name, ccs, ev, dur in orig(eng, *x, **k):
            d = dict(ccs)
            extra = [(c, v) for key, c, v in add if d.get(C.CC[key], 0) > 0]
            if inf_pick and d.get(C.CC["REV"], 0) > 0 and d.get(C.CC["RV_DECAY"]) == 127:
                extra.append((C.CC["RV_DECAY"], INF_STRESS_DECAY))
            out.append((name, list(ccs) + extra, ev, dur))
        if fold:
            out += fold_takes(C, eng, tv, add)
        return out
    C.takes = full_takes
    print("rev_hil: stress_max, its own takes, every effect on in a take as: " + "; ".join(notes)
          + ("; + per engine the jack check (hil_jack's silence / hiss / loud chord / harmonics) and a TAPE recipe "
             "(--fold)" if fold else ""), flush=True)
    args = ["--engine", a.engine, "--rate", a.rate, "--jack-dev", a.jack_dev, "--jack-ch", str(a.jack_ch)]
    args += (["--quick"] if a.quick else []) + (["--no-self-test"] if a.no_self_test else [])
    if a.self_test_only:                              # (hil_crackle's host self-test alone, on these takes: no device)
        return C.main(args + ["--self-test"] + (["--dry-run"] if a.dry_run else []))   # (dry: the plan only)
    if a.short:                                       # (the 44.1 kHz run: 2 worst x 60 s, the recipes at 30 s)
        args += ["--worst-s", "120", "--recipe-s", "60", "--no-usb-s", "0"]
    args += (["--dry-run"] if a.dry_run else []) + (["--host"] if a.host else [])
    got = {}
    if fold and not a.dry_run and not a.host:
        orig_t = C.Device.take

        def take(self, ccs, events, dur, usb=True):
            r = orig_t(self, ccs, events, dur, usb)
            nm = next((n for n, e in take.ev if e is events), None)
            if nm:
                got[nm] = (r[0], r[1], events, self.rate, dict(self.clock))
            return r
        take.ev = []
        keep = C.takes

        def keep_ev(eng, *x, **k):
            T = keep(eng, *x, **k)
            take.ev = [(n, e) for n, c, e, d in T if n.endswith(("-jackcheck", "-tape"))]
            return T
        C.takes = keep_ev
        C.Device.take = take
    code = C.main(args)
    if fold and not a.dry_run and not a.host:
        ok = fold_report(C, got, os.path.join(C.OUT, a.rate))
        code = code or (0 if ok else 1)
    return code


JC_NOTE, JC_CHORD = 33, (36, 43, 48, 52, 55, 60, 64)       # hil_jack's quiet low note and loud chord
JC_HARM = [(n, v) for n in (33, 45) for v in (40, 80, 110, 127)]


def fold_takes(C, eng, tv, add):
    """the takes that fold hil_jack, hil_scan's silence and usb_onset, and tape_jack into stress_max (test-speed plan
    3.3 step 3; P3): per engine a jack check, with no effects (idle 3 s: USB exact zero and the jack's muted floor;
    hil_jack's low quiet note held 3 s: the hiss band; its loud chord 1.5 s: the level and SNR; its harmonics notes),
    and a TAPE recipe with the moderate effects (as tape_jack's tape-moderate) and every effect's TYPE choice"""
    ev, t = [], 3.0
    ev += [(t, "on", JC_NOTE, 30), (t + 3.0, "off", JC_NOTE, 0)]
    t += 4.0
    ev += [(t, "on", n, 127) for n in JC_CHORD] + [(t + 1.5, "off", n, 0) for n in JC_CHORD]
    t += 2.5
    for n, v in JC_HARM:
        ev += [(t, "on", n, v), (t + 0.9, "off", n, 0)]
        t += 1.2
    out = [(f"{eng}-jackcheck", C.engine_ccs(eng, {}), sorted(ev, key=lambda e: e[0]), t + 0.5)]
    if tv is not None:
        rng = np.random.default_rng(53 + C.ENGINES.index(eng))
        ev2, d = C.recipe_events(C.voices(eng), 30.0, rng)
        fx = dict(C.MODERATE, DL_SW=tv, DL_DEPTH=100, DL_TIME=100, DL_FDBK=100)
        ccs = C.engine_ccs(eng, fx)
        dd = dict(ccs)
        ccs += [(c, v) for key, c, v in add if key != "DL_SW" and dd.get(C.CC[key], 0) > 0]
        out.append((f"{eng}-tape", ccs, sorted(ev2, key=lambda e: e[0]), d))
    return out


def fold_report(C, got, od):
    """the folded measurements, as the tools they replace compute them (hil_jack: band_db / db over its windows, in
    dBFS; hil_scan silence: the USB peak 0 at idle; usb_onset: MIDI -> USB, here clock_fit's lag over every onset);
    fold.json + lines; False when USB is not exact zero at idle (hil_scan's silence gate)"""
    import hil_jack as HJ
    rep, ok = {}, True
    for nm, (u, j, ev, fs, clock) in got.items():
        eng = nm.split("-")[0]
        R = rep.setdefault(eng, {})
        if u is None or j is None:
            R[nm] = "no recording"
            ok = False
            continue
        uf, jf = np.asarray(u, float)[:, 0] / 32768.0, np.asarray(j, float) / 32768.0

        def w(x, a0, a1, f=fs):
            return x[int(a0 * f):int(a1 * f)]
        fu = C.clock_fit(u, ev, fs, rate=clock.get("usb"))
        fj = C.clock_fit(j, ev, C.FS_JACK, rate=clock.get("jack"))
        lag = dict(usb_ms=round(1000 * fu["lag"], 1), jack_ms=round(1000 * fj["lag"], 1),
                   usb_minus_jack_ms=round(1000 * (fu["lag"] - fj["lag"]), 1))
        if nm.endswith("-jackcheck"):
            sil = dict(usb_peak_lsb=float(np.abs(np.asarray(u)[int(0.3 * fs):int(2.8 * fs)]).max()),
                       jack_db=round(HJ.db(w(jf, 0.3, 2.8, C.FS_JACK)), 1),
                       jack_hf_db=round(HJ.band_db(w(jf, 0.3, 2.8, C.FS_JACK), 8000, 20000), 1))
            hu, hjk = w(uf, 3.5, 5.8), w(jf, 3.5, 5.8, C.FS_JACK)
            hiss = dict(usb_hf_db=round(band_db(hu, fs, 8000, 20000), 1),
                        jack_hf_db=round(HJ.band_db(hjk, 8000, 20000), 1))
            lu, lj = w(uf, 7.05, 8.4), w(jf, 7.05, 8.4, C.FS_JACK)
            gain = HJ.db(lj) - HJ.db(lu)
            loud = dict(usb_db=round(HJ.db(lu), 1), jack_db=round(HJ.db(lj), 1),
                        usb_peak_db=round(float(20 * np.log10(np.abs(lu).max() + 1e-12)), 1),
                        jack_peak_db=round(float(20 * np.log10(np.abs(lj).max() + 1e-12)), 1))
            harm, t = [], 9.5
            for n, v in JC_HARM:
                f0 = 440 * 2 ** ((n - 69) / 12)
                a_u, a_j = w(uf, t + 0.05, t + 0.85), w(jf, t + 0.05, t + 0.85, C.FS_JACK)
                if fs == HJ.FS:
                    hu_, hj_ = HJ.harm(a_u, f0), HJ.harm(a_j, f0)
                    harm.append(dict(note=n, vel=v, usb=hu_, jack=hj_,
                                     diff_max=float(max(abs(x - y) for x, y in zip(hu_, hj_)))))
                t += 1.2
            R.update(silence=sil, silence_ok=sil["usb_peak_lsb"] == 0, hiss=hiss, loud=loud,
                     jack_re_usb_db=round(gain, 1), snr_db=round(loud["jack_db"] - hiss["jack_hf_db"], 1),
                     hiss_added_db=round(hiss["jack_hf_db"] - (hiss["usb_hf_db"] + gain), 1), harmonics=harm,
                     onset_jackcheck=lag)
            ok &= R["silence_ok"]
        else:
            R["onset_tape"] = lag
    os.makedirs(od, exist_ok=True)
    json.dump(rep, open(os.path.join(od, "fold.json"), "w"), indent=1, default=str)
    for eng, R in rep.items():
        if "silence" in R:
            print(f"fold {eng:8s} silence: USB peak {R['silence']['usb_peak_lsb']:.0f} LSB "
                  f"({'ok' if R['silence_ok'] else 'FAIL: not exact zero'}), jack {R['silence']['jack_db']} dB "
                  f"(8-20k {R['silence']['jack_hf_db']}); hiss 8-20k jack {R['hiss']['jack_hf_db']} / USB "
                  f"{R['hiss']['usb_hf_db']} (the jack adds {R['hiss_added_db']} dB); loud USB {R['loud']['usb_db']} "
                  f"jack {R['loud']['jack_db']} dB; SNR {R['snr_db']} dB; harmonics worst diff "
                  f"{max([h['diff_max'] for h in R['harmonics']], default=float('nan')):.1f} dB", flush=True)
        for k in ("onset_jackcheck", "onset_tape"):
            if k in R:
                print(f"fold {eng:8s} {k}: MIDI -> USB {R[k]['usb_ms']} ms, -> jack {R[k]['jack_ms']} ms, USB - jack "
                      f"{R[k]['usb_minus_jack_ms']} ms (clock_fit over the take's onsets)", flush=True)
    print(f"fold: {'PASS' if ok else 'FAIL'} (hil_jack / silence / usb_onset / tape_jack folded; fold.json in {od}); "
          f"0.9.4: idle floor -99.8 dBFS, SNR 74 dB (build/hil/jack.json): by hand", flush=True)
    return ok


def stress(a, C):
    if a.full:
        return stress_full(a, C)
    cc, types, _, notes, _ = reverb()
    want = (a.type or "INFINITE").upper()
    inf = ([n for n in types if n == want] or [n for n in types if n.startswith(want) or want.startswith(n)]
           or [n for n in types if n[:3] == want[:3]])
    for s in notes:
        print("rev_hil: " + s)
    if not inf:
        print(f"rev_hil: FAIL: no reverb TYPE {want} in this tree ({', '.join(types)})")
        return 1
    tv = types[inf[0]]
    if a.quick:
        a.worst_s, a.recipe_s = 20.0, 24.0
    if cc is None and not a.dry_run:
        print("rev_hil: FAIL: no reverb TYPE CC in this tree's params.c")
        return 1
    C.OUT = os.path.join(C.ROOT, "build", "hil", "stress_rev")
    def rev_takes(eng, worst_s=60.0, recipe_s=24.0, seed=0, nv=None):
        """hil_crackle.takes' worst-case chords (HEAVY at full level: the LEVEL 0 half is CPU only, hil_cpu has it)
        and its moderate-effects recipe (the dry recipe has no reverb), each with the reverb TYPE (--type)"""
        nv = nv or C.voices(eng)
        rng = np.random.default_rng(seed + C.ENGINES.index(eng))
        out = []
        for name, fx, total, gen in (("worst", C.HEAVY, worst_s, C.worst_events),
                                     ("recipe-fx", C.MODERATE, recipe_s / 2, C.recipe_events)):
            n = max(1, int(np.ceil(total / C.TAKE_MAX)))
            for i in range(n):
                ev, dur = gen(nv, total / n, rng)
                dk = [(C.CC["RV_DECAY"], INF_STRESS_DECAY)] if inf[0].startswith("INF") and fx.get("RV_DECAY") == 127 else []
                out.append((f"{eng}-{inf[0].lower()[:5]}-{name}{i + 1}", C.engine_ccs(eng, fx) + [(cc if cc else 0, tv)] + dk,
                            sorted(ev, key=lambda e: e[0]), dur))
        return out
    C.takes = rev_takes
    print(f"rev_hil: stress_max with the reverb TYPE {inf[0]} (CC {cc} = {tv}) in every take; the worst takes "
          f"with RV_DECAY 127 ({INF_STRESS_DECAY} for INFINITE: its SUSTAIN, the input open; 127 = FREEZE would close "
          f"it before the first note), at full level")
    args = ["--engine", a.engine, "--rate", a.rate,
            "--worst-s", str(a.worst_s), "--recipe-s", str(a.recipe_s), "--no-usb-s", "0", "--no-self-test",
            "--jack-dev", a.jack_dev, "--jack-ch", str(a.jack_ch)]
    args += (["--dry-run"] if a.dry_run else []) + (["--host"] if a.host else [])
    return C.main(args)


def jack(a, C):
    cc, types, knobs, notes, _ = reverb()
    for s in notes:
        print("rev_hil: " + s)
    if cc is None and not a.dry_run:
        print("rev_hil: FAIL: no reverb TYPE CC in this tree's params.c")
        return 1
    tcc = cc if cc else 0
    run = [n for n in types if n != "OFF"]
    out = a.out or os.path.join(main_checkout(), "private", "reverb", "takes")
    C.OUT = out
    od = os.path.join(out, "host" if a.host else str(a.rate))
    meta, rec = {}, []

    def knob_ccs(st):
        return [(knobs[k], v) for k, v in st.items() if v is not None and k in knobs]

    def take_list(eng, worst_s=0.0, recipe_s=0.0, seed=0, nv=None):
        nv = nv or C.voices(eng)
        rng = np.random.default_rng(seed + 41)
        base = C.engine_ccs(eng, {})
        T = []
        for tname in run:
            for sname, st in SETTINGS:
                ev, d = C.recipe_events(nv, a.seconds, rng)
                nm = f"{eng}-{tname.lower()}-{sname}"
                T.append((nm, base + [(tcc, types[tname])] + knob_ccs(st), sorted(ev, key=lambda e: e[0]),
                          d + TAIL_S))
                meta[nm] = dict(kind="type", type=tname, setting=sname, music_end=d)
        infn = [n for n in run if n.startswith("INF")]
        if infn:                                      # INFINITE frozen: a chord in, then 60 s held
            # FREEZE (DECAY 127) closes the tank's input: it goes on while the chord rings (0.3 s before its notes
            # let go), never in the set-up, or the tank holds nothing and the take is 60 s of exact zeros (it was:
            # freeze_ok passed on silence, and the settings' autosave wrote the flash in that silence mid-take: with
            # the blocking erase of 0.9.5's first final run, missed / overruns on the counters)
            ev, d = C.recipe_events(nv, 9.0, rng)
            st = dict(SETTINGS[1][1], RV_DEPTH=110, RV_TONE=80)
            tf = max(e[0] for e in ev if e[1] == "off") - 0.3
            ev = ev + [(tf, "cc", knobs["RV_DECAY"], 127)]
            nm = f"{eng}-infinite-freeze60"
            T.append((nm, base + [(tcc, types[infn[0]])] + knob_ccs(st), sorted(ev, key=lambda e: e[0]), d + 60.0))
            meta[nm] = dict(kind="freeze", type=infn[0], setting="DECAY 127", music_end=d)
        ev, d = C.recipe_events(nv, 30.0, rng)        # the TYPE switched while playing
        order = run + ["OFF"]
        sw = [(t, "cc", tcc, types[order[k % len(order)]]) for k, t in enumerate(np.arange(3.0, d, 3.0))]
        st = dict(SETTINGS[1][1], RV_DEPTH=100)
        nm = f"{eng}-switch"
        T.append((nm, base + [(tcc, types[run[0]])] + knob_ccs(st), sorted(ev + [(float(t), k, c, v) for t, k, c, v in sw],
                                                                          key=lambda e: e[0]), d + TAIL_S))
        meta[nm] = dict(kind="switch", type=" > ".join(order), setting=f"{len(sw)} switches every 3 s", music_end=d)
        return T
    C.takes = take_list
    print(f"rev_hil: reverb TYPE CC {cc if cc else '??'}: " + ", ".join(f"{n} {v}" for n, v in types.items())
          + f"; knobs {', '.join(f'{k} CC {v}' for k, v in knobs.items())}; WAVs to {od}")

    def judge(nm, u, j, fs):
        m = meta.get(nm, {})
        r = dict(name=nm, **m)
        if u is not None and j is not None and len(u) and len(j):
            u1 = np.asarray(u, float)[:, 0] if np.ndim(u) == 2 else np.asarray(u, float)
            j1 = np.asarray(j, float)
            me = m.get("music_end", len(u1) / fs)
            lu, lj = u1[int(0.5 * fs):int(me * fs)], j1[int(0.5 * FS_JACK):int(me * FS_JACK)]
            gain = db(lj) - db(lu) if len(lu) and len(lj) else 0.0
            tu, tj = u1[-int(1.5 * fs):], j1[-int(1.5 * FS_JACK):]
            uh, jh = band_db(tu, fs, 8000, 20000), band_db(tj, FS_JACK, 8000, 20000)
            r.update(jack_re_usb_db=round(gain, 1), tail_usb_hf_db=None if uh is None else round(uh, 1),
                     tail_jack_hf_db=None if jh is None else round(jh, 1),
                     tail_hiss_added_db=None if uh is None or jh is None or uh < -200 else round(jh - (uh + gain), 1),
                     tail_usb_db=round(db(tu), 1), tail_usb_peak_lsb=float(np.abs(tu).max()) if len(tu) else None,
                     tail_usb_exact_zero=bool(len(tu) and not np.any(tu[-int(0.1 * fs):])))
            if m.get("kind") == "freeze":             # the held 60 s: its level per 5 s window, the DC
                h = u1[int((me + 2.0) * fs):]
                w = [db(h[i:i + 5 * fs]) for i in range(0, len(h) - 5 * fs + 1, 5 * fs)]
                if w:
                    r.update(freeze_db=[round(x, 2) for x in w], freeze_spread_db=round(max(w) - min(w), 2),
                             freeze_growth_db=round(w[-1] - w[0], 2), freeze_dc_lsb=round(float(np.mean(h)), 2),
                             freeze_ok=bool(min(w) >= FREEZE_MIN_DB and max(w) - min(w) <= 2.0 and
                                            w[-1] - w[0] <= 1.0 and abs(float(np.mean(h))) < 8.0))
        rec.append(r)
        line = f"rev_hil: {nm}: " + ", ".join(f"{k} {v}" for k, v in r.items() if k not in ("name", "kind", "music_end",
                                                                                           "freeze_db"))
        print(line, flush=True)

    if not a.dry_run:
        os.makedirs(od, exist_ok=True)
        if a.host:
            orig_h = C.host_take

            def host_take(name, ccs, events, dur, **k):
                u, txt = orig_h(name, ccs, events, dur, **k)
                write_wav(os.path.join(od, f"{name}-usb.wav"), u, C.FS_HOST)
                judge(name, u, C.sim_jack(u), C.FS_HOST)
                return u, txt
            C.host_take = host_take
        else:
            orig_t = C.Device.take

            def take(self, ccs, events, dur, usb=True):
                r = orig_t(self, ccs, events, dur, usb)
                nm = next((n for n, c, e, d in take.T if e is events), "take")
                if r[0] is not None:
                    write_wav(os.path.join(od, f"{nm}-usb.wav"), r[0], self.rate)
                if r[1] is not None:
                    write_wav(os.path.join(od, f"{nm}-jack.wav"), r[1], FS_JACK)
                print(f"rev_hil: saved {nm} (USB {'yes' if r[0] is not None else 'no'}, jack "
                      f"{'yes' if r[1] is not None else 'no'}) in {od}", flush=True)
                judge(nm, r[0], r[1], self.rate)
                return r
            take.T = []
            orig_list = C.takes

            def keep(eng, *x, **k):
                T = orig_list(eng, *x, **k)
                take.T = T
                return T
            C.takes = keep
            C.Device.take = take
    args = ["--engine", a.engine, "--rate", a.rate, "--no-usb-s", "0", "--no-self-test", "--jack-dev", a.jack_dev,
            "--jack-ch", str(a.jack_ch)] + (["--dry-run"] if a.dry_run else []) + (["--host"] if a.host else [])
    code = C.main(args)
    if not a.dry_run:
        fz = [r for r in rec if r.get("kind") == "freeze"]
        bad = [r["name"] for r in fz if not r.get("freeze_ok")]
        hiss = [r["tail_hiss_added_db"] for r in rec if r.get("tail_hiss_added_db") is not None]
        txt = [f"rev_jack: {'PASS' if code == 0 and not bad else 'FAIL'} ({len(rec)} takes; detectors exit {code}"
               + (f"; freeze FAILED: {', '.join(bad)}" if bad else "") + ")",
               "hiss (the jack's own 8-20 kHz in the takes' tails): " +
               (f"{min(hiss):+.1f}..{max(hiss):+.1f} dB over USB's + the jack's gain" if hiss else "not measured")
               + "; 0.9.4: idle floor -99.8 dBFS, SNR 74 dB (build/hil/jack.json): by hand"]
        txt += [f"  {r['name']:28s} " + ", ".join(f"{k} {v}" for k, v in r.items() if k not in ("name", "music_end"))
                for r in rec]
        open(os.path.join(od, "rev_jack.txt"), "w").write("\n".join(txt) + "\n")
        json.dump(rec, open(os.path.join(od, "rev_jack.json"), "w"), indent=1)
        print("\n".join(txt))
        return 1 if (code or bad) else 0
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("stress", "jack"))
    ap.add_argument("--rate", default="48000", choices=("44100", "48000"))
    ap.add_argument("--engine", default=None, choices=("rhodes", "vibes", "marimba", "all"),
                    help="stress: all (default); jack: rhodes (default)")
    ap.add_argument("--seconds", type=float, default=20.0, help="jack: the music per TYPE x setting take (+ 4 s tail)")
    ap.add_argument("--worst-s", type=float, default=60.0, help="stress: the worst-case time per engine (2 takes)")
    ap.add_argument("--recipe-s", type=float, default=24.0, help="stress: the recipe time (its moderate-fx half runs)")
    ap.add_argument("--type", default=None, help="stress: the reverb TYPE in every take (default INFINITE)")
    ap.add_argument("--quick", action="store_true", help="stress: 20 s worst + 24 s recipe per engine")
    ap.add_argument("--full", action="store_true", help="stress: hil_crackle's own full takes with every effect "
                    "(the heaviest drive, TAPE, the heaviest reverb TYPE or --type, LOW CUT): the release's stress_max")
    ap.add_argument("--no-self-test", action="store_true", help="stress --full: skip hil_crackle's host self-test")
    ap.add_argument("--self-test-only", action="store_true", help="stress --full: only hil_crackle's host self-test "
                    "on the takes as the device run has them (--fold too), then stop; no device")
    ap.add_argument("--fold", action="store_true", help="stress --full at 48 kHz: + per engine the jack check and a "
                    "TAPE recipe (hil_jack, silence, usb_onset, tape_jack folded in; fold.json)")
    ap.add_argument("--short", action="store_true", help="stress --full: 2 worst takes x 60 s + the recipes at 30 s, "
                    "no jack-alone take (the 44.1 kHz run)")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--out", default=None, help="jack: the takes' folder (default <main>/private/reverb/takes)")
    ap.add_argument("--dry-run", action="store_true", help="the takes and their time; no MIDI, no audio")
    ap.add_argument("--host", action="store_true", help="the host build plays (the jack simulated)")
    a = ap.parse_args()
    a.engine = a.engine or ("all" if a.mode == "stress" else "rhodes")
    import hil_crackle as C
    return stress(a, C) if a.mode == "stress" else jack(a, C)


if __name__ == "__main__":
    sys.exit(main())
