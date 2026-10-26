#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's audio CPU load, measured on the device over USB-MIDI: each scenario plays, then CC 116 asks the
firmware for the longest audio block since the last query (frodhes.c midi_cc; replies on CC 116/117 = us / 4).
The block is 256 frames at 44.1 kHz = 5805 us of real time; the voice shedder starts at 5000 us.

    python3 tools/hil_cpu.py [--engine vibes|marimba] [--voices N] [--worst] [--fx-each] [--calib] [--stages] [--tail]
                             [--bend-sweep] [--cc-sweep CC[:LO:HI:STEP_MS]]

--voices N (default: the engine's own count, rhodes.c RH_VOICES 10 / BAR_VOICES 10) plays N-note chords and N bass
notes (for a build with -DRH_VOICES=N, -DBAR_VOICES=N or -DFR_VOICES=N; more notes than the firmware's voices steal). The lists: bass 28 31 33 35 36 38 40 41 43 45 47 48,
chord 48 52 55 59 62 64 67 71 74 76 79 83 (the first N).
--scenario TEXT[,TEXT] (with --worst) runs only the scenarios whose name contains one of them (the key ADC gate on
the worst ones: --keyadc gate --worst --scenario pedal; run_final_rev.sh). --worst runs only the worst-case scenarios (N bass v127 + pedal, every effect; VIBES: its edge-strike versions too)
and the idle-with-every-effect one (the fixed part), 3 takes each as usual.

--engine vibes selects VIBES (CC 79), with the motor on (CC 47; records v3: the engine's own) wherever the effects
are on (the tremolo too: it can run on top of the motor), off elsewhere, and adds the bar engine's worst voicings (edge strike, RUBBER, tube and weight at 127). It also prints the
bar engine's lowest mode cap and most live upper modes per scenario (CC 112 / 113 replies).
--fx-each: idle, then each effect alone (minus idle without effects) = the per-effect cost table of fx.c (FX_US_*).
--calib (VIBES): 8 RUBBER edge strikes at v127 with the mode cap forced (developer CC 9) to 0, 8, 16, 24, 32, 40, 48
upper modes, no effects, then 4 and 1 voices: fits the cost per upper mode-block and the fixed part per voice, for
bars.c BAR_NS_UNIT / BAR_NS_VOICE + BAR_NS_FORCE + BAR_NS_STRIKE.
--stages: a developer build (APP_DEFS=-DRH_PROF_STAGES ./build.sh) also answers each stage's longest time (frodhes.c
PROF_STAMP: 1 the engine, 2..14 fx_process's stages when it is stamped, 15 the rest): printed per scenario.
--tick: a developer build (APP_DEFS=-DFM1_PROF_TICK ./build.sh) also answers the timer tick's time (platform.c
tick_prof_reply, CC 100 items 13..16: the longest tick, the most tick time inside one audio block, the mean per block,
the mean tick): printed per scenario (maxima over its queries; the means as the highest of its queries).
--tail: prints the ISR tail per scenario (CC 110/109 = the longest tail after app_audio, CC 108/107 = the longest
whole ISR, us / 4: every build since the USB ring fix answers them; before, only a developer build).
--keyadc off|idle|stress: the key converter's developer mode (firmware/src/keyvel.h, CC 41: OFF = no key
conversions, only MASTER's and the battery's slots; idle = NORMAL, variant B, nobody at the keys; STRESS = a key
conversion, its drive and dark LEDs on every free tick, no key landing) sent after every reset; the run then prints
the firmware's items (CC 42 0) and fails when the mode is not taken or STRESS did not convert.
--keyadc gate [--engines rhodes,vibes,marimba] [--runs 3]: every engine x run x mode (OFF, idle, STRESS, rotated),
each a whole run with the other options (--worst, --usb RATE / --no-usb-stream, --voices N: by default each engine's
own count); the MIDI ports and the USB stream are opened once for all of them. The gates: idle <= OFF + 10 us and
STRESS <= OFF + 60 us on each engine's worst block over its runs (each scenario's difference printed too); every run
is also judged as below (4500 us, a shed voice, a USB fault). Exit 1 on any FAIL.
--usb RATE (44100 or 48000, default 48000): records the FM-1's USB audio at RATE (blocksize 128, as a DAW) during
every scenario,
so the USB tap, the 48 kHz resampler and the ring run as they do in use. Every scenario prints the firmware's USB
counters (CC 100 id / 101-102 value: underrun packets, missed, overruns, adj up / down, the fill at render start,
the true low point before the tap, the ISR entry spacing): any underrun, missed packet or overrun is a FAIL, and
the run exits 1. The stream is open by default (B, 2026-10-24: the stream's own work, the 48 kHz resampler after
app_audio and uac_service in the render, was never in the numbers before); --no-usb-stream measures without it
(the counters then stay 0 and say only that).
--bend-sweep: while every scenario plays (and idles), a MIDI pitch bend (0xE0) every 5 ms sweeps -2 .. +2 semitones
and back (a triangle, 0.5 s each way): each block where the bend changed retunes every sounding Rhodes tine (rhodes.c
rh_retune), so with --worst this is the bend's CPU gate (VIBES / MARIMBA ignore the bend: their numbers must not move).
The bend goes back to 0 after each scenario.
--cc-sweep CC[:LO:HI:STEP_MS] (default LO 0, HI 127, STEP_MS 5): the same for a control change: while every scenario
plays (and idles), CC moves LO .. HI and back (a triangle, 0.5 s each way), one message every STEP_MS ms (only when its
value changed). E.g. 73:1:127:200 turns the reverb SIZE every 200 ms, so its fades (0.9.6: 186 ms on PLATE and
INFINITE) run back to back: tools/cpu_check.sh's vibes_size cases. The CC is not put back after a scenario (the next
one's reset sets it). Works with --bend-sweep (both every pass of the wait).
Every scenario also asks 0.4 s after its note-offs and 0.4 s after the pedal is let go, before anything resets (the
release blocks: a damper on every voice). --worst adds 'slow paths': every effect plus the drive's TONE and MIX off
their defaults, the reverb's pre-delay, LEVEL off unity, the FX bypass crossfade switched during the chord, and steals
under the pedal (12 notes on the engine's voices). Exit 1 when the worst block or the whole ISR is over 4500 us, a
voice was shed, or a USB counter that must stay 0 moved (any scenario), or (--keyadc) the key converter's check
failed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_scan import CC, DRY, param_defaults  # noqa: E402

PORT_KEYS = ("frodhes", "felucca", "fm-1")                       # the FM-1's MIDI port / audio device names
USB_BAD_RUNS = []                                     # scenarios with a USB underrun, missed packet or overrun
USB_ITEMS = {1: "underruns", 2: "missed", 3: "overruns", 4: "adj_up", 5: "adj_down", 6: "fill_lo", 7: "fill_hi",
             8: "fill_low", 9: "per_min", 10: "per_max", 11: "stalls", 12: "state"}
TICK_ITEMS = {13: "tick_max", 14: "blk_max", 15: "blk_mean", 16: "tick_mean"}   # (--tick: FM1_PROF_TICK builds)
USB_SUM = ("underruns", "missed", "overruns", "adj_up", "adj_down", "stalls")    # deltas: summed over a scenario
USB_MIN = ("fill_lo", "fill_low", "per_min")
USB_BAD = ("underruns", "missed", "overruns")


def usb_merge(acc, got):
    """one CC 116 reply's USB items into a scenario's totals (sums, minima, maxima)"""
    for k, v in got.items():
        if k in USB_SUM:
            acc[k] = acc.get(k, 0) + v
        elif k in USB_MIN:
            if not (k == "fill_low" and v == 16383) and not (k == "fill_lo" and v == 16383):
                acc[k] = min(acc.get(k, v), v)
        elif k == "state":
            acc[k] = v
        else:
            acc[k] = max(acc.get(k, v), v)
    return acc


def usb_line(acc):
    if not acc:
        return "USB counters: no reply (a firmware before the USB ring fix)"
    st = acc.get("state", 0)
    s = "stream " + ("off" if not st & 2 else ("48 kHz" if st & 4 else "44.1 kHz"))
    keys = ("underruns", "missed", "overruns", "adj_up", "adj_down", "fill_lo", "fill_hi", "fill_low", "per_min",
            "per_max")
    return "USB " + s + ": " + ", ".join(f"{k} {acc[k]}" for k in keys if k in acc)

CC = dict(CC, TONE=28, BARK=29, BELL=30, HAMMER=31, DRV_TONE=82, DRV_MIX=83, RV_PREDLY=94)
TARGET_US = 4500

BLOCK_US = 5805
STAGES = {1: "engine (eng_render)", 2: "fx copy", 3: "compressor", 4: "filter + LFO", 5: "drive", 6: "trem / wah",
          7: "phaser", 8: "chorus", 9: "delay", 10: "reverb", 11: "bypass / DC / LEVEL", 12: "limiter", 13: "gate",
          14: "fx (other)", 15: "after the last stamp"}


def default_voices(eng):
    """the firmware's voice count for an engine: rhodes.c RH_VOICES (the Rhodes) or BAR_VOICES (VIBES, MARIMBA)"""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "firmware", "src", "rhodes.c")).read()
    key = "RH_VOICES" if eng == "rhodes" else "BAR_VOICES"
    return int(re.search(r"#define " + key + r" (\d+)u", src).group(1))


KEYADC = dict(off=1, idle=0, stress=2)                # --keyadc MODE -> CC 41 (firmware/src/keyvel.h KV_DEV_*)
KEYADC_GATE = dict(idle=10, stress=60)                # us over OFF (the plan's variant B gates)
KEYADC_BAD = []                                       # the key converter's mode not taken, conversions missing
_PORTS, _USB = [], {}                                 # opened once per process (the gate runs many measurements)


def ports():
    """(mido, out, inp): the FM-1's MIDI ports, opened at the first call (tests replace this)"""
    if not _PORTS:
        import mido
        outs = [n for n in mido.get_output_names() if any(k in n.lower() for k in PORT_KEYS)]
        ins = [n for n in mido.get_input_names() if any(k in n.lower() for k in PORT_KEYS)]
        if not outs or not ins:
            sys.exit("no FM-1 MIDI port")
        _PORTS[:] = [mido, mido.open_output(outs[0]), mido.open_input(ins[0])]
    return tuple(_PORTS)


def keyadc_check(mido, out, inp, kmode):
    """after a --keyadc run: the firmware's developer items (CC 42 0: mode, DRIVE, WAIT, TOUCH, conversions, timeouts,
    battery timeouts, MASTER) printed; the mode must be the one asked for and STRESS must have converted"""
    if kmode is None:
        return None
    import hil_keyvel as kv
    q = kv.named(kv.Dev(mido, out, inp).query((0,)))
    out.send(mido.Message("control_change", control=41, value=KEYADC["idle"]))   # NORMAL again after the run
    if not q:
        KEYADC_BAD.append(f"--keyadc {kmode}: no CC 42 reply (a firmware without key velocity)")
        print(f"{'':44s} key ADC: no CC 42 reply")
        return None
    print(f"{'':44s} key ADC {kmode}: mode {q['mode']}, conversions {q['convs']} (14-bit, saturated), timeouts "
          f"{q['timeouts']}, battery timeouts {q['batt_to']}, MASTER {q['master']}")
    if q["mode"] != KEYADC[kmode]:
        KEYADC_BAD.append(f"--keyadc {kmode}: the FM-1 reports mode {q['mode']}, not {KEYADC[kmode]}")
    if kmode == "stress" and q["convs"] < 1000:
        KEYADC_BAD.append(f"--keyadc stress: only {q['convs']} conversions (the stress mode is not converting)")
    return q


def keyadc_gate(argv):
    """--keyadc gate: each engine (--engines, default rhodes,vibes,marimba) x --runs (default 3) x OFF / idle / STRESS
    (the order rotated per run, so a drift spreads over the modes), every measurement a whole hil_cpu run with the
    other options as given (--worst: its scenarios only; the USB stream, open by default, stays open for all of them;
    --voices: by default each engine's own count). Per engine and mode the worst block over the runs; the gates:
    idle <= OFF + 10 us, STRESS <= OFF + 60 us. Each scenario's own difference (its worst over the runs) is printed
    too. Returns (True when every engine passes, the worst exit code of the runs' own verdicts)"""
    i = argv.index("--keyadc")
    base = argv[:i] + argv[i + 2:]
    engines = ["rhodes", "vibes", "marimba"]
    runs = 3
    for flag in ("--engines", "--runs", "--engine"):
        if flag in base:
            j = base.index(flag)
            if flag == "--engines":
                engines = base[j + 1].split(",")
            elif flag == "--runs":
                runs = int(base[j + 1])
            base = base[:j] + base[j + 2:]
    modes = ["off", "idle", "stress"]
    res = {}                                          # (engine, mode) -> [run result]
    for r in range(runs):
        for e in engines:
            order = modes[r % 3:] + modes[:r % 3]
            for m in order:
                print(f"\n=== key ADC gate: run {r + 1}/{runs}, {e}, --keyadc {m}")
                argv0 = sys.argv
                try:
                    res.setdefault((e, m), []).append(run_cpu(base + ["--engine", e, "--keyadc", m]))
                finally:
                    sys.argv = argv0
    ok = True
    print(f"\nkey ADC gate ({runs} runs per engine and mode; worst block over the runs; gates idle <= OFF + "
          f"{KEYADC_GATE['idle']} us, STRESS <= OFF + {KEYADC_GATE['stress']} us)")
    summary = {}
    for e in engines:
        w = {m: max(x["worst"] for x in res[(e, m)]) for m in modes}
        d = {m: w[m] - w["off"] for m in ("idle", "stress")}
        good = {m: d[m] <= KEYADC_GATE[m] for m in d}
        ok &= all(good.values())
        sc = {}
        for m in modes:
            for x in res[(e, m)]:
                for name, us in x["rows"]:
                    sc.setdefault(name, {}).setdefault(m, []).append(us)
        per = {n: {m: max(v[m]) - max(v["off"]) for m in ("idle", "stress") if m in v and "off" in v}
               for n, v in sc.items()}
        summary[e] = dict(worst=w, delta=d, ok=good, per_scenario=per)
        print(f"  {e:8s} OFF {w['off']:5d} us   idle {w['idle']:5d} ({d['idle']:+d}: "
              f"{'PASS' if good['idle'] else 'FAIL'})   STRESS {w['stress']:5d} ({d['stress']:+d}: "
              f"{'PASS' if good['stress'] else 'FAIL'})")
        for n, v in per.items():
            print(f"      {n:44s} idle {v.get('idle', 0):+5d}  stress {v.get('stress', 0):+5d} us")
    keyadc_gate.summary = summary
    code = max(x["code"] for v in res.values() for x in v)
    if code:
        print("key ADC gate: a run's own verdict FAILED (over 4500 us, a shed voice or a USB fault): see 'hil_cpu: FAIL'")
    print(f"key ADC gate: {'PASS' if ok and not KEYADC_BAD else 'FAIL'}")
    return ok and not KEYADC_BAD, code


def close_usb():
    """the USB stream (opened once per process by run_cpu) stopped and closed"""
    if _USB.get("stream") is not None:
        _USB["stream"].stop()
        _USB["stream"].close()
    _USB.clear()


def cc_sweep_arg(argv):
    """--cc-sweep CC[:LO:HI:STEP_MS] -> (cc, lo, hi, step in s), or None; exits on a bad value"""
    if "--cc-sweep" not in argv:
        return None
    i = argv.index("--cc-sweep")
    try:
        f = [int(x) for x in argv[i + 1].split(":")]
        if len(f) not in (1, 4):
            raise ValueError
    except (IndexError, ValueError):
        sys.exit("hil_cpu: --cc-sweep CC[:LO:HI:STEP_MS], integers (e.g. 73:1:127:200)")
    c, lo, hi, ms = f if len(f) == 4 else (f[0], 0, 127, 5)
    if not (0 <= c <= 127 and 0 <= lo <= 127 and 0 <= hi <= 127 and ms >= 1):
        sys.exit("hil_cpu: --cc-sweep: CC, LO, HI 0..127, STEP_MS >= 1")
    return c, lo, hi, ms / 1000.0


def main(argv=None):
    """one run (or, --keyadc gate, the gate's runs); returns the run's dict(rows, worst, keyadc, code), the gate's
    dict(gate, code): code is the run's verdict (4500 us, a shed voice, a USB fault), exit_code() adds KEYADC_BAD"""
    argv = list(sys.argv if argv is None else argv)
    if "-h" in argv or "--help" in argv:              # the options only: no device opened
        print(__doc__)
        return dict(code=0)
    argv0 = sys.argv
    try:
        if "--keyadc" in argv and argv[argv.index("--keyadc") + 1:argv.index("--keyadc") + 2] == ["gate"]:
            ok, code = keyadc_gate(argv)
            if not ok:
                KEYADC_BAD.append("the key ADC gate")
            return dict(gate=ok, code=code)
        return run_cpu(argv)
    finally:
        sys.argv = argv0
        close_usb()


def exit_code(res):
    """the process's exit code: 1 when the run's verdict failed (res code), the key converter's check failed
    (KEYADC_BAD) or a USB counter moved (USB_BAD_RUNS); each reason printed"""
    if KEYADC_BAD:
        print("FAIL: the key converter: " + "; ".join(KEYADC_BAD))
    if USB_BAD_RUNS:
        print("FAIL: USB underruns / missed packets / overruns in: " + ", ".join(USB_BAD_RUNS))
    return 1 if (res or {}).get("code") or KEYADC_BAD or USB_BAD_RUNS else 0


def run_cpu(argv):  # noqa: C901
    """one hil_cpu run (argv as the command line's); returns dict(rows [(scenario, worst us)], worst, keyadc (the
    key converter's items or None), code (the verdict: 0, or 1 over 4500 us / a shed voice / a USB fault))"""
    sys.argv = list(argv)                             # (the options as the command line's: main() restores it)
    eng = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "rhodes"
    marimba = eng == "marimba"                        # the second bar TYPE (CC 79 96..127): no motor
    vibes = eng in ("vibes", "marimba") or "--calib" in sys.argv
    kmode = sys.argv[sys.argv.index("--keyadc") + 1] if "--keyadc" in sys.argv else None
    if kmode is not None and kmode not in KEYADC:
        sys.exit("--keyadc: off, idle, stress or gate")
    mido, out, inp = ports()                          # (after the option checks: a bad option touches no device)

    def cc(c, v):
        out.send(mido.Message("control_change", control=c, value=int(v)))

    def reset(**kv):
        cc(120, 0)
        for c, v in param_defaults():
            cc(c, v)
        d = dict(DRY)
        d.update(kv)
        if d.get("CP_SW"):                            # CC 85 (the combined CHO/PHA) switches the phaser off: send it first
            cc(CC["CP_SW"], d.pop("CP_SW"))
        for k, v in d.items():
            cc(CC[k], v)
        if marimba:
            cc(79, 127)                              # MARIMBA (no motor; CC 47 is inert)
        elif vibes:
            cc(79, 0)                                # VIBES (the motor starts with the TYPE change)
            cc(47, 127 if d.get("TW_SW") else 0)      # the motor with the effects (records v3: its own CC), else off
            if d.get("TW_SW"):
                cc(17, 64)
        if kmode is not None:                         # the key converter's developer mode (keyvel.h CC 41), again
            cc(41, KEYADC[kmode])                     # after every reset (it is not a parameter: CC 120 keeps it)
        time.sleep(0.3)

    def query():  # noqa: C901
        for _ in inp.iter_pending():
            pass
        cc(116, 127)
        got, st = {}, dict(cur=None, msb=0)
        usb, uid = {}, dict(id=None, msb=0)

        def take():                                        # every reply: the profiler (115-118), the bar engine's
            for m in inp.iter_pending():                   # cap / live modes (112 / 113), the ISR tail (107-110),
                if m.type != "control_change":             # the stage profiler (97 stage, 98 / 99 us)
                    continue
                if m.control in (115, 116, 117, 118, 112, 113, 107, 108, 109, 110):
                    got[m.control] = m.value
                elif m.control == 100:
                    uid["id"] = m.value
                elif m.control == 101:
                    uid["msb"] = m.value
                elif m.control == 102 and uid["id"] in USB_ITEMS:
                    usb[USB_ITEMS[uid["id"]]] = uid["msb"] << 7 | m.value
                elif m.control == 102 and uid["id"] in TICK_ITEMS:
                    k = TICK_ITEMS[uid["id"]]
                    query.tick[k] = max(query.tick.get(k, 0), uid["msb"] << 7 | m.value)
                elif m.control == 97:
                    st["cur"] = m.value
                elif m.control == 98:
                    st["msb"] = m.value
                elif m.control == 99 and st["cur"] is not None:
                    query.st[st["cur"]] = max(query.st.get(st["cur"], 0), st["msb"] << 7 | m.value)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0 and not {115, 116, 117, 118} <= got.keys():
            take()
            time.sleep(0.005)
        if not {116, 117} <= got.keys():
            sys.exit("no profiler reply (a firmware without CC 116?)")
        t0 = time.monotonic()
        while time.monotonic() - t0 < (0.15 if "--stages" in sys.argv or "--tick" in sys.argv else 0.08):   # the replies after 115-118
            take()
            time.sleep(0.005)
        query.bars = (got.get(112), got.get(113))
        usb_merge(query.usb, usb)
        if {107, 108, 109, 110} <= got.keys():
            query.tail = (max(query.tail[0], (got[110] << 7 | got[109]) * 4),
                          max(query.tail[1], (got[108] << 7 | got[107]) * 4))
        if {107, 108} <= got.keys():
            query.whole = max(query.whole, (got[108] << 7 | got[107]) * 4)   # (the run's, never cleared)
        return (got[116] << 7 | got[117]) * 4, got[118] * 32, got[115]

    bend_on = "--bend-sweep" in sys.argv
    ccs = cc_sweep_arg(sys.argv)                      # (cc, lo, hi, step_s) or None
    ccs_at = [0.0, None]                              # the last CC sweep message: when, the value

    def bend(v14):
        out.send(mido.Message("pitchwheel", pitch=int(v14) - 8192))

    def wait(sec):
        """sleep sec; with --bend-sweep, a pitch bend every 5 ms meanwhile (a 1 s triangle over the whole range);
        with --cc-sweep, its CC every STEP_MS (the same triangle, LO .. HI)"""
        if not bend_on and not ccs:
            time.sleep(sec)
            return
        t_end = time.monotonic() + sec
        while True:
            now = time.monotonic()
            if now >= t_end:
                break
            ph = (now % 1.0) * 2                          # 0..2: up, then down
            tri = ph if ph < 1 else 2 - ph
            if bend_on:
                bend(round(tri * 16383))
            if ccs and now - ccs_at[0] >= ccs[3]:
                v = round(ccs[1] + tri * (ccs[2] - ccs[1]))
                if v != ccs_at[1]:
                    cc(ccs[0], v)
                    ccs_at[1] = v
                ccs_at[0] = now
            time.sleep(min(0.005, max(0.0, t_end - now)))

    def play(notes, vel, hold, pedal=False, bypass=False, release=True):
        """returns the longest block of the attack (first 0.3 s) and of the rest: the held part, the release (0.4 s
        after the note-offs) and, with the pedal, the pedal's release (0.4 s after letting it go). bypass: the FX
        bypass switched on and off during the chord (the crossfade's blocks); release=False: no release queries (the
        repeated chords, the calibration: their timing as before)"""
        if pedal:
            cc(64, 127)
        for n in notes:
            out.send(mido.Message("note_on", note=n, velocity=vel))
        wait(0.3)
        attack = query()[0]
        if bypass:
            cc(111, 127)
            wait(0.1)
            cc(111, 0)
        wait(max(0.0, hold - 0.3 - (0.1 if bypass else 0.0)))
        held = query()[0]
        for n in notes:
            out.send(mido.Message("note_off", note=n))
        if bend_on:
            bend(8192)
        if not release:
            return attack, held
        time.sleep(0.4)
        held = max(held, query()[0])                       # the release blocks (before any reset)
        if pedal:
            cc(64, 0)
            time.sleep(0.4)
            held = max(held, query()[0])                   # the pedal's release: a damper on every voice at once
        return attack, held

    query.bars = (None, None)
    query.st, query.tail, query.usb, query.whole, query.tick = {}, (0, 0), {}, 0, {}
    usb_bad = []                                      # this run's scenarios with a USB fault (also USB_BAD_RUNS)
    if "--no-usb-stream" not in sys.argv:             # a DAW-like USB audio stream for the whole run (the default)
        import sounddevice as sd
        rate = int(sys.argv[sys.argv.index("--usb") + 1]) if "--usb" in sys.argv else 48000
        devs = [i for i, d in enumerate(sd.query_devices()) if any(k in d["name"].lower() for k in PORT_KEYS)
                and d["max_input_channels"] >= 2]
        if not devs:
            sys.exit("--usb: no FM-1 audio input")
        # the device itself switched to RATE (as rh_devcmp): without it Core Audio keeps the FM-1 at its current
        # nominal rate and converts, and the firmware runs the other rate's path
        ca = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True)
        if _USB.get("rate") != rate:              # (open once: --keyadc gate runs many measurements on it)
            close_usb()
            stream = sd.InputStream(device=devs[0], samplerate=rate, channels=2, dtype="int16", blocksize=128,
                                    callback=lambda *a: None, extra_settings=ca)
            stream.start()
            _USB.update(rate=rate, stream=stream)
            time.sleep(1.0)
        query.usb = {}
        query()                                       # the firmware's own view of the stream (CC 100 id 12)
        st = query.usb.get("state")
        if st is not None and (not st & 2 or bool(st & 4) != (rate == 48000)):
            sys.exit(f"--usb {rate}: the FM-1 reports the stream {usb_line(query.usb)} (state {st}): not at {rate} Hz")
        print(f"USB audio open at {rate} Hz, blocksize 128 (the FM-1 confirms: {usb_line(query.usb).split(':')[0]})")
    heavy = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
                 CP_SW=64, CP_DEPTH=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127,
                 FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)   # the filter at its heaviest: LP18, the LFO
    nv = int(sys.argv[sys.argv.index("--voices") + 1]) if "--voices" in sys.argv else default_voices(eng)
    if not 1 <= nv <= 12:
        sys.exit("--voices: 1..12")
    bass8 = [28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48][:nv]          # the first 8: the lists as before
    chord8 = [48, 52, 55, 59, 62, 64, 67, 71, 74, 76, 79, 83][:nv]
    worst_only = "--worst" in sys.argv
    rows = []

    sheds = []

    def verdict(worst):
        """the run's exit code: the worst block (and the whole ISR when reported) <= TARGET_US, nothing shed, no
        scenario of this run with a USB underrun, missed packet or overrun (also kept in USB_BAD_RUNS)"""
        USB_BAD_RUNS.extend(usb_bad)
        whole = query.whole or None
        print(f"whole ISR worst: {str(whole) + ' us' if whole else 'not reported by this firmware'}" +
              (f"; USB counters moved in: {', '.join(usb_bad)}" if usb_bad else ""))
        bad = worst > TARGET_US or (whole or 0) > TARGET_US or any(sheds) or bool(usb_bad)
        print("hil_cpu: " + ("FAIL" if bad else "ok"))
        return 1 if bad else 0

    scen = sys.argv[sys.argv.index("--scenario") + 1].split(",") if "--scenario" in sys.argv else None

    def run(name, fx, notes=(), vel=100, hold=1.5, pedal=False, rep=3, bypass=False):
        if scen and not any(k in name for k in scen):
            return                                    # (--scenario: only the scenarios whose name has one of these)
        att = held = 0
        shed0 = query()[2]
        query.st, query.tail, query.usb, query.tick = {}, (0, 0), {}, {}
        for _ in range(rep):
            reset(**fx)
            query()                                   # clears the max
            if notes:
                a, h = play(notes, vel, hold, pedal, bypass)
            else:
                wait(hold)
                a = h = query()[0]
                if bend_on:
                    bend(8192)
            att, held = max(att, a), max(held, h)
            cc(64, 0)
        shed = (query()[2] - shed0) % 128
        sheds.append(shed)
        rows.append((name, max(att, held)))
        extra = f"   modes cap/live {query.bars[0]}/{query.bars[1]}" if vibes and query.bars[0] is not None else ""
        print(f"{name:44s} attack {att:5d} us ({100 * att / BLOCK_US:4.1f} %)   held {held:5d} us "
              f"({100 * held / BLOCK_US:4.1f} %)   voices shed {shed}{extra}")
        if "--tail" in sys.argv or query.tail[1]:
            print(f"{'':44s} ISR tail after app_audio {query.tail[0]:4d} us, whole ISR {query.tail[1]:5d} us")
        print(f"{'':44s} {usb_line(query.usb)}")
        if any(query.usb.get(k) for k in USB_BAD):
            usb_bad.append(name)
        if "--tick" in sys.argv:
            t = query.tick
            print(f"{'':44s} " + (f"tick: longest {t.get('tick_max', 0) / 24:.2f} us, mean {t.get('tick_mean', 0) / 24:.2f} us; "
                                  f"per audio block max {t.get('blk_max', 0)} us, mean {t.get('blk_mean', 0) / 4:.1f} us"
                                  if t else "tick: no reply (not an FM1_PROF_TICK build)"))
        if "--stages" in sys.argv and query.st:
            print("    " + ", ".join(f"{STAGES.get(i, i)} {us}" for i, us in sorted(query.st.items())) + " (us, max)")

    if "--fx-each" in sys.argv:
        one = [("compressor", dict(CMP_SW=127, CMP_AMT=100)), ("filter LP18 + LFO", dict(FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)),
               ("drive", dict(DRIVE=127)), ("tremolo", dict(TW_SW=64, TW_DEPTH=127)), ("wah", dict(TW_SW=127, TW_DEPTH=127)),
               ("phaser", dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127)), ("chorus", dict(CP_SW=64, CP_DEPTH=127)),
               ("delay", dict(DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127)), ("reverb", dict(REV=127))]
        base = []
        for _ in range(3):
            reset(); query(); time.sleep(1.0); base.append(query()[0])
        b = max(base)
        print(f"{'idle, no effects':24s} {b:5d} us")
        for name, fx in one:
            v = []
            for _ in range(3):
                reset(**fx); query(); time.sleep(1.0); v.append(query()[0])
            print(f"{name:24s} {max(v):5d} us   cost {max(v) - b:5d} us   (fx.c FX_US_*)")
        reset()
        return dict(rows=[], worst=None, keyadc=keyadc_check(mido, out, inp, kmode), code=0)
    if "--calib" in sys.argv:
        edge = dict(TONE=127, BARK=127, BELL=127, HAMMER=127)
        bass8 = [28, 31, 33, 35, 36, 38, 40, 41]                         # the fit is for 8 voices
        pts = []
        for nv, notes in ((8, bass8), (4, bass8[:4]), (1, bass8[:1])):
            for cap in ((0, 8, 16, 24, 32, 40, 48) if nv == 8 else (0, 24)):
                worst = 0
                for _ in range(3):
                    reset(**edge)
                    cc(9, cap + 1)
                    query()
                    a, h = play(notes, 127, 1.0, release=False)
                    worst = max(worst, a)
                    cc(64, 0)
                cc(9, 0)
                pts.append((nv, cap, worst, query.bars))
                print(f"{nv} voices, cap {cap:2d} upper modes: attack {worst:5d} us   (bars cap / live: {query.bars})")
        import numpy as np
        p8 = [(c, w) for nv, c, w, _ in pts if nv == 8]
        k, c0 = np.polyfit([c for c, _ in p8], [w for _, w in p8], 1)
        print(f"\nper upper mode-block (attack, the force on): {k:.1f} us -> BAR_NS_UNIT + BAR_NS_UNITF = {k / 8 * 1000:.0f} ns")
        v = {nv: w for nv, c, w, _ in pts if c == 0}
        per = (v[8] - v[1]) / 7
        print(f"per voice at the strike (pair, pulse, note-on, gain): {per:.0f} us -> BAR_NS_VOICE + FORCE + STRIKE")
        print(f"fixed (idle + noise + 1 voice): {v[1]:.0f} us; the model's base is BAR_NS_BASE + BAR_NS_NOISE")
        reset()
        return dict(rows=[], worst=None, keyadc=keyadc_check(mido, out, inp, kmode), code=0)
    N = str(nv)
    print(f"{nv} voices (--voices {nv}): bass {bass8}, chord {chord8}" +
          (", a pitch bend sweep every 5 ms (--bend-sweep)" if bend_on else "") +
          (f", CC {ccs[0]} swept {ccs[1]}..{ccs[2]} every {round(ccs[3] * 1000)} ms (--cc-sweep)" if ccs else ""))
    if vibes:
        edge = dict(TONE=127, BARK=127, BELL=127, HAMMER=127)
        if not worst_only:
            run(N + " bass v127, edge strike, rubber, no effects", edge, bass8, 127)
        run(N + "-note chord v127, edge, every effect + motor", dict(heavy, **edge), chord8, 127)
        run(N + " bass v127, edge + pedal, every effect + motor", dict(heavy, **edge), bass8, 127, pedal=True, hold=3.0)
        query.bars = (None, None)
        heavy = dict(heavy, **edge)               # the repeated-chord test at the worst voicing too
    if not worst_only:
        run("idle, no effects", {})
    run("idle, every effect on (heaviest)", heavy)
    if not worst_only:
        run(N + "-note chord v100, no effects", {}, chord8)
        run(N + " bass notes v127 at once, no effects", {}, bass8, 127)
        run(N + "-note chord v100, every effect on", heavy, chord8)
    run(N + " bass notes v127 + pedal, every effect on", heavy, bass8, 127, pedal=True)
    slow = dict(heavy, DRV_TONE=110, DRV_MIX=64, RV_PREDLY=127, LEVEL=20)
    steal = [28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48]       # 12 notes on the engine's voices: steals
    run("slow paths: + drive tone / mix, pre-delay, LEVEL, bypass xfade, 12 notes + pedal", slow, steal, 127,
        pedal=True, bypass=True)
    if worst_only:
        reset()
        allrows = list(rows)
        worst = max(w for n, w in rows if not n.startswith("idle"))
        print(f"\nworst {worst} us with {nv} voices ({100 * worst / BLOCK_US:.0f} %); design target 4500 us "
              f"(margin {4500 - worst}), shedder 5000 us (margin {5000 - worst})")
        code = verdict(worst)
        return dict(rows=allrows, worst=worst, keyadc=keyadc_check(mido, out, inp, kmode), code=code)
    att = shed0 = 0
    shed0 = query()[2]
    reset(**heavy)
    query()
    query.usb = {}
    for i in range(12):                               # N-note chords struck again every 0.4 s (voice steals)
        play(chord8 if i % 2 else bass8, 127, 0.4, release=False)
        att = max(att, query()[0])
    shed = (query()[2] - shed0) % 128
    sheds.append(shed)
    rows.append(("repeated", att))
    print(f"{N + '-note chords struck every 0.4 s, every effect':44s} attack {att:5d} us ({100 * att / BLOCK_US:4.1f} %)"
          f"   voices shed {shed}")
    print(f"{'':44s} {usb_line(query.usb)}")
    if any(query.usb.get(k) for k in USB_BAD):
        usb_bad.append("repeated")
    reset()
    worst = max(w for _, w in rows)
    print(f"\nworst {worst} us of {BLOCK_US} us per block ({100 * worst / BLOCK_US:.0f} %) with {nv} voices; shedder at "
          f"5000 us; headroom to the shedder {5000 - worst} us, to real time {BLOCK_US - worst} us; to the 4500 us "
          f"design target {4500 - worst} us")
    code = verdict(worst)
    return dict(rows=list(rows), worst=worst, keyadc=keyadc_check(mido, out, inp, kmode), code=code)


if __name__ == "__main__":
    sys.exit(exit_code(main()))
