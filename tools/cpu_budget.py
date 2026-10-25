#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The audio ISR's CPU budget on the host: the whole ISR's worst block per scenario, predicted from the FM-1's measured
costs in tools/cpu_costs.json (the one table; every device CPU run updates it). tests/py/test_cpu_budget.py fails when
a release scenario's prediction exceeds the 4500 us target, so a change that adds voices, effects or ISR work shows up
in CI before any device time.

  python3 tools/cpu_budget.py              the predictions: every engine at its voice count and at 8 / 9, every effect,
                                           the USB stream at 48 / 44.1 kHz, with and without the pitch bend; exit 1
                                           when a release scenario is over the target

The model (upper bounds: each part at its measured maximum):
  whole ISR = app_audio + the tail after it (USB closed / 44.1 / 48 kHz) + the bend's retune while the wheel moves
  app_audio = the measured worst block at that voice count, or, at another count, the fixed part (idle with every
              effect) + voices x the per-voice cost ((worst - fixed) / voices of the measured count, the largest);
              without some effects their share comes off (fx_us, scaled to idle_every_fx - idle_no_fx)
The worst block and the worst tail need not land in the same block, so the sum over-predicts (Rhodes 10 + every effect,
48 kHz: 3768 against CC 116's 3516-3640 whole-ISR maximum, FM-1 batch 1 on sonic-d3df27f): a gate, not an estimate."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(ROOT, "tools", "cpu_costs.json")
ENGINES = ("rhodes", "vibes", "marimba")


def load(path=TABLE):
    with open(path) as f:
        return json.load(f)


def hi(row):
    return max(row["us"])


def firmware_voices(root=ROOT):
    """{engine: voices} as the firmware's sources set them by default: rhodes.c RH_VOICES / BAR_VOICES (one per
    engine), else FR_VOICES (both)"""
    src = open(os.path.join(root, "firmware", "src", "rhodes.c")).read()
    try:
        src += open(os.path.join(root, "firmware", "src", "bars.h")).read()
    except OSError:
        pass

    def define(name):
        m = re.search(rf"#define\s+{name}\s+(\d+)u?\b", src)
        return int(m.group(1)) if m else None
    fr = define("FR_VOICES")
    rh, bar = define("RH_VOICES") or fr, define("BAR_VOICES") or fr
    return {"rhodes": rh, "vibes": bar, "marimba": bar}


def firmware_has_bend(root=ROOT):
    """the pitch bend in the firmware (engine.c eng_bend_set): then a bending block is a release scenario"""
    d = os.path.join(root, "firmware", "src")
    return any("eng_bend_set" in open(os.path.join(d, f)).read() for f in os.listdir(d) if f.endswith((".c", ".h")))


def per_voice(t, engine):
    """the per-voice cost (us): the largest (worst - fixed) / voices over the measured counts"""
    fixed = hi(t["app"]["idle_every_fx"])
    return max((hi(r) - fixed) / int(n) for n, r in t["worst_app"][engine].items())


def fx_names(t):
    return [k for k in t["fx_us"] if not k.startswith("_")]


def fx_share(t, names):
    """the effects' part of app_audio (us): their fx_us estimates, scaled so that all of them make the measured
    idle_every_fx - idle_no_fx"""
    allfx = sum(t["fx_us"][k] for k in fx_names(t))
    return (hi(t["app"]["idle_every_fx"]) - hi(t["app"]["idle_no_fx"])) * sum(t["fx_us"][k] for k in names) / allfx


def rev_types(t):
    """the reverb TYPEs the table has a row for (rev_type_us: each one's cost over HALL, which the measured rows
    include)"""
    return [k for k in t.get("rev_type_us", {"hall": None}) if not k.startswith("_")]


def rev_delta(t, reverb="hall"):
    """a reverb TYPE's cost over HALL (us; the row's maximum), 0 for HALL"""
    r = t.get("rev_type_us", {}).get(reverb)
    return max(r["us"]) if r else 0


def predict(t, engine, voices, usb="48000", fx="every", bend=False, reverb="hall"):
    """the whole ISR's worst block (us) and its parts; reverb: the reverb TYPE in "every effect" (rev_type_us)"""
    w = t["worst_app"][engine]
    app = hi(w[str(voices)]) if str(voices) in w else hi(t["app"]["idle_every_fx"]) + voices * per_voice(t, engine)
    if fx != "every":                                  # "none" or a list of fx_us names: the others' share comes off
        on = [] if fx == "none" else list(fx)
        app -= fx_share(t, [k for k in fx_names(t) if k not in on])
    if fx == "every" or "reverb" in fx:
        app += rev_delta(t, reverb) + est_costs(t, engine)
    tail = hi(t["tail_us"][usb])
    b = hi(t["bend_us"]) if bend else 0
    return dict(engine=engine, voices=voices, usb=usb, fx=fx if isinstance(fx, str) else "+".join(fx), bend=bend, reverb=reverb, app=round(app), tail=tail, bend_us=b,
                whole=round(app + tail + b))


def scenarios(t, voices=None, bend=None, reverb="hall"):
    """the release scenarios: every engine at its firmware voice count, every effect at its heaviest, the USB stream
    open at 48 kHz (the longest tail), the pedal down (the measured worst cases); bending too when the firmware has
    the bend"""
    voices = voices or firmware_voices()
    bend = firmware_has_bend() if bend is None else bend
    out = [predict(t, e, voices[e], reverb=reverb) for e in ENGINES]
    if bend:
        out += [predict(t, e, voices[e], bend=True, reverb=reverb) for e in ENGINES]
    return out


def main():
    t = load()
    fw = firmware_voices()
    print(f"firmware voices: {fw}; the bend in the firmware: {firmware_has_bend()}; target {t['target_us']} us, "
          f"shedder {t['shed_us']} us, block {t['block_us']} us")
    print(f"{'engine':8s} {'voices':>6s} {'usb':>7s} {'fx':>6s} {'bend':>5s} {'app':>6s} {'tail':>5s} {'whole':>6s}")
    bad = 0
    for e in ENGINES:
        for n in sorted({8, 9, fw[e]}):
            for usb in ("48000", "44100"):
                for bend in (False, True):
                    p = predict(t, e, n, usb, bend=bend)
                    rel = n == fw[e] and usb == "48000" and (not bend or firmware_has_bend())
                    over = p["whole"] > t["target_us"]
                    bad += rel and over
                    print(f"{e:8s} {n:6d} {usb:>7s} {'every':>6s} {'yes' if bend else 'no':>5s} {p['app']:6d} "
                          f"{p['tail']:5d} {p['whole']:6d}" + ("  over the target" if over else "")
                          + ("  (release)" if rel else ""))
    if len(rev_types(t)) > 1:
        print("the release scenarios with each reverb TYPE (rev_type_us: its cost over HALL, in 'every effect'):")
        for r in rev_types(t)[1:]:
            for p in scenarios(t, reverb=r):
                over = p["whole"] > t["target_us"]
                bad += over
                print(f"  {r:15s} {p['engine']:8s} {p['voices']} voices, bend {'yes' if p['bend'] else 'no':3s}: "
                      f"{p['whole']} us ({rev_delta(t, r):+d})" + ("  over the target" if over else ""))
    lo, hi_ = est_savings(t)
    if hi_:
        print("estimated savings not yet measured on the FM-1 (est_savings_us, information; each engine its rows): "
              "the release scenarios less them:")
        for p in scenarios(t):
            lo, hi_ = est_savings(t, p["engine"])
            print(f"  {p['engine']:8s} {p['voices']} voices: {p['whole'] - hi_}..{p['whole'] - lo} us")
    return 1 if bad else 0


def est_costs(t, engine=None):
    """the [E] costs of est_costs_us not yet in the measured rows (us, each row's high; the reverb's work: predict
    adds them with the reverb on); a row with "engines" counts only for those"""
    return sum(max(r["us"]) for k, r in t.get("est_costs_us", {}).items() if not k.startswith("_")
               and (engine is None or engine in r.get("engines", [engine])))


def est_savings(t, engine=None):
    """the [E] savings of est_savings_us not yet in the measured rows (us): their sum, (low, high); a row with
    "engines" counts only for those (engine given), a row without one for every engine"""
    rows = [r for k, r in t.get("est_savings_us", {}).items() if not k.startswith("_")
            and (engine is None or engine in r.get("engines", [engine]))]
    return sum(min(r["us"]) for r in rows), sum(max(r["us"]) for r in rows)


if __name__ == "__main__":
    sys.exit(main())
