#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's key velocity on the device, over USB-MIDI (the developer protocol in firmware/src/keyvel.h: CC 41 sets a
mode, CC 42 g queries items g*8 .. g*8+7, each answered as CC 38 item, CC 39 value >> 7, CC 40 value & 127).

    hil_keyvel.py record [--phases soft,medium,hard] [--seconds 40] [--power usb|battery] [--label NAME] [--out FILE]
    hil_keyvel.py analyse FILE                  the analysis of a saved session again
    hil_keyvel.py compare A.json B.json         the same presses on two power sources (or two builds)
    hil_keyvel.py soak [--minutes 10] [--mode normal|stress|storm]
    hil_keyvel.py selftest                      every sampled row driven low must read 0
    hil_keyvel.py status                        every item (mode, counters, MASTER, battery, floors)
    hil_keyvel.py jack [--dev M6] [--ch 5] [--power usb|battery] [--dry-run]

record: the user plays. The per-press report is switched on (CC 41 8: before each of the FM-1's own note-ons, CC 33
key, CC 34 raw >> 3, CC 35 (raw & 7) | src << 3, CC 36 its wait in ms; src 0 a reading, 1 timed out, 2 let go first,
3 a top key, 4 FIXED), then each phase asks for one kind of press (soft: every analog key F3..C5 softly, a few times
each; medium: every key at an ordinary touch, for the evenness; hard: every key hard) and lasts --seconds or until
Enter. Saved to build/hil/keyvel/<time>-<label>.json, then analysed:
  separation   soft vs hard on the analog keys: the median velocities' and readings' gap, and the share of
               (soft, hard) pairs in the right order (1.0 = no overlap); gate: gap >= 30 and order >= 0.90
  evenness     per-key median velocity in the medium phase (keys with >= 3 presses): spread max - min <= 30,
               the keys more than 15 from the overall median named
  extremes     the medium phase's share of velocity 127 and of velocity 1: each <= 5 %
  latency      the analog keys' wait (CC 36): 0..4 ms expected (gate: the longest <= 4 ms), timeouts <= 2 %
compare: per key and phase, the medians of A and B (a session on USB power and one on battery: the same presses);
gate: every phase's overall median within 5, a key more than 12 apart named.
soak: the mode for --minutes with nobody touching the FM-1: no note-key press (item 12), no button press (item 13:
kv_dev_edges, a row driven low that reads OCT- / OCT+), no note from the FM-1; the scan alive (item 17).
selftest: CC 41 4 for 2 s: the highest reading of each row driven low (items 9 / 10) must be 0 (--st-max), with
readings taken (item 11) and no key / button edge; then NORMAL.
jack: the headphone jack in OFF, NORMAL (idle), STRESS and OFF again (tools/hil_jack.py's recorder; --dry-run: the
host build plays and the jack is simulated): no line of the scan's drive or converter at 454.5 Hz (2 frames), 909.1 Hz
(a frame) or 10 kHz (the tick) in idle that OFF does not have (+3 dB at most, the muted codec and under a quiet note),
and idle's SNR (a loud chord's RMS over the 8-20 kHz hiss) within 0.5 dB of OFF's. STRESS is reported, not gated
(every free tick drives a row: a developer load, not a use case). -> build/hil/keyvel-jack-<power>.json.
Every mode is lost at power-off and nothing is saved on the FM-1. Exit status 1 when a gate fails.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil")
PORT_KEYS = ("frodhes", "felucca", "fm-1")

CC_MODE, CC_QUERY, CC_ITEM, CC_HI, CC_LO = 41, 42, 38, 39, 40     # keyvel.h KV_CC_*
CC_REP = (33, 34, 35, 36)                                          # the per-press report
MODES = dict(normal=0, idle=0, off=1, stress=2, storm=3, selftest=4)
REPORT_ON, REPORT_OFF = 8, 9
ITEMS = {0: "mode", 1: "drive", 2: "wait", 3: "touch", 4: "convs", 5: "timeouts", 6: "batt_to", 7: "master",
         8: "batt", 9: "st_pa7", 10: "st_pa8", 11: "st_n", 12: "notes", 13: "buttons", 14: "analog", 15: "fallback",
         16: "tick_max", 17: "frames"}
ITEMS.update({32 + k: f"floor{k}" for k in range(20)})
SRC = {0: "reading", 1: "timeout", 2: "let go first", 3: "top key", 4: "fixed"}
TOUCH = ("LIGHT", "MEDIUM", "HEAVY", "FIXED")
NK, NANALOG = 27, 20                                               # keys 0..19 (F3..C5) have a reading
_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def key_name(k):
    """key 0 = F3 .. 26 = G5"""
    n = 53 + k
    return f"{_NAMES[n % 12]}{n // 12 - 1}"


LIMITS = dict(sep_gap=30, sep_order=0.90, even_spread=30, even_off=15, even_min=3, ext_frac=0.05, lat_max=4,
              timeout_frac=0.02, power_med=5, power_key=12, st_max=0, line_db=3.0, snr_db=0.5)


# ---- the device: ports and the developer protocol
def open_ports():
    """(mido, out, inp) of the FM-1 (tests replace this)"""
    import mido
    outs = [n for n in mido.get_output_names() if any(k in n.lower() for k in PORT_KEYS)]
    ins = [n for n in mido.get_input_names() if any(k in n.lower() for k in PORT_KEYS)]
    if not outs or not ins:
        sys.exit("no FM-1 MIDI port")
    return mido, mido.open_output(outs[0]), mido.open_input(ins[0])


class Dev:
    """the developer protocol over an open port pair; every other message the FM-1 sends is kept in self.msgs as
    (t, msg) (the report and its note-ons, a spurious note) for the caller to read with take()"""

    def __init__(self, mido, out, inp):
        self.mido, self.out, self.inp = mido, out, inp
        self.msgs, self._cur, self._hi, self.vals = [], None, 0, {}

    def cc(self, c, v):
        self.out.send(self.mido.Message("control_change", control=int(c), value=int(v)))

    def mode(self, v):
        self.cc(CC_MODE, MODES[v] if isinstance(v, str) else v)

    def poll(self):
        t = time.monotonic()
        for m in self.inp.iter_pending():
            if m.type == "control_change" and m.control == CC_ITEM:
                self._cur = m.value
            elif m.type == "control_change" and m.control == CC_HI and self._cur is not None:
                self._hi = m.value
            elif m.type == "control_change" and m.control == CC_LO and self._cur is not None:
                self.vals[self._cur] = self._hi << 7 | m.value
                self._cur = None
            else:
                self.msgs.append((t, m))

    def take(self):
        m, self.msgs = self.msgs, []
        return m

    def query(self, groups=(0, 1, 2), timeout=1.0):
        """{item: value} for every item of the groups (None when the firmware does not answer: no key velocity).
        One group at a time: the FM-1 sends one group's reply per query and drops a query that arrives while a reply
        is still going out, so back-to-back queries for several groups lose all but one."""
        self.poll()
        out = {}
        for g in groups:
            want = {g * 8 + i for i in range(8)}
            for i in want:
                self.vals.pop(i, None)
            self.cc(CC_QUERY, g)
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout and not want <= self.vals.keys():
                time.sleep(0.005)
                self.poll()
            if not want <= self.vals.keys():
                return None
            out.update({i: self.vals[i] for i in want})
        return dict(sorted(out.items()))

    def wait(self, s):
        """s seconds, the port read meanwhile"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < s:
            time.sleep(0.005)
            self.poll()


def named(vals):
    return {ITEMS.get(i, str(i)): v for i, v in (vals or {}).items()}


# ---- the per-press report -> presses
def parse(msgs, phase=None):
    """(t, msg) list -> presses: dict(key, raw, src, wait, vel, note, t, phase); a note-on without a report before it
    gets key None (another source, a firmware without the report); the extra note-ons of one press (CHORD) are
    counted in the first press's 'extra'"""
    out, cur, pend = [], {}, []
    for t, m in msgs:
        if m.type == "control_change" and m.control in CC_REP:
            if m.control == 33:
                cur = dict(key=m.value, t=t, phase=phase)
            elif m.control == 34 and "key" in cur:
                cur["raw"] = m.value << 3
            elif m.control == 35 and "key" in cur:
                cur["raw"] = cur.get("raw", 0) | (m.value & 7)
                cur["src"] = m.value >> 3
            elif m.control == 36 and "key" in cur:
                cur["wait"] = m.value
                pend.append(cur)
                cur = {}
        elif m.type == "note_on" and m.velocity > 0:
            if pend:
                p = pend.pop(0)
                p.update(vel=m.velocity, note=m.note, extra=0)
                out.append(p)
            elif out and t - out[-1]["t"] < 0.002 and out[-1]["key"] is not None:
                out[-1]["extra"] += 1
            else:
                out.append(dict(key=None, raw=None, src=None, wait=None, vel=m.velocity, note=m.note, t=t,
                                phase=phase, extra=0))
    return out


# ---- analysis
def stats(v):
    v = sorted(v)
    if not v:
        return dict(n=0)

    def q(f):
        x = f * (len(v) - 1)
        i = int(x)
        return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (x - i)
    return dict(n=len(v), min=v[0], q1=round(q(0.25), 1), median=round(q(0.5), 1), q3=round(q(0.75), 1), max=v[-1],
                mean=round(sum(v) / len(v), 1))


def order_share(lo, hi):
    """the share of (lo, hi) pairs with hi > lo (ties half): 1.0 = the two never overlap"""
    if not lo or not hi:
        return None
    s = sum((h > l_) + 0.5 * (h == l_) for l_ in lo for h in hi)
    return round(s / (len(lo) * len(hi)), 3)


def analyse(presses, lim=None):
    lim = dict(LIMITS, **(lim or {}))
    P = [p for p in presses if p.get("vel")]
    an = [p for p in P if p["key"] is not None and p["key"] < NANALOG and p["src"] in (0, 1, 2)]   # (not FIXED)
    rd = [p for p in an if p["src"] == 0]
    res = dict(n=len(P), unreported=sum(p["key"] is None for p in P),
               src={SRC.get(s, str(s)): sum(p["src"] == s for p in P) for s in SRC},
               phases={ph: sum(p["phase"] == ph for p in P) for ph in dict.fromkeys(p["phase"] for p in P)})
    gates = {}
    # latency: the analog keys' wait for their reading
    w = [p["wait"] for p in rd]
    to = sum(p["src"] == 1 for p in an)
    res["latency"] = dict(stats(w), timeouts=to, let_go_first=sum(p["src"] == 2 for p in an),
                          timeout_frac=round(to / len(an), 3) if an else None)
    if an:
        gates["latency"] = bool((not w or max(w) <= lim["lat_max"]) and to / len(an) <= lim["timeout_frac"])
    # separation: soft vs hard
    soft = [p for p in rd if p["phase"] == "soft"]
    hard = [p for p in rd if p["phase"] == "hard"]
    if soft and hard:
        sv, hv = [p["vel"] for p in soft], [p["vel"] for p in hard]
        sr, hr = [p["raw"] for p in soft], [p["raw"] for p in hard]
        gap = stats(hv)["median"] - stats(sv)["median"]
        rgap = stats(hr)["median"] - stats(sr)["median"]
        res["separation"] = dict(soft=stats(sv), hard=stats(hv), gap=round(gap, 1), order=order_share(sv, hv),
                                 raw_soft=stats(sr), raw_hard=stats(hr), raw_gap=round(rgap, 1),
                                 raw_order=max(order_share(sr, hr), order_share(hr, sr)))
        gates["separation"] = bool(gap >= lim["sep_gap"] and res["separation"]["order"] >= lim["sep_order"])
    # evenness: per key in the medium phase (else every phase)
    med = [p for p in rd if p["phase"] == "medium"] or ([] if soft or hard else rd)
    if med:
        allm = stats([p["vel"] for p in med])["median"]
        per = {}
        for k in range(NANALOG):
            v = [p["vel"] for p in med if p["key"] == k]
            if len(v) >= lim["even_min"]:
                per[key_name(k)] = stats(v)["median"]
        if per:
            spread = max(per.values()) - min(per.values())
            res["evenness"] = dict(median=allm, per_key=per, spread=round(spread, 1),
                                   off=[k for k, v in per.items() if abs(v - allm) > lim["even_off"]],
                                   missing=[key_name(k) for k in range(NANALOG) if key_name(k) not in per])
            gates["evenness"] = bool(spread <= lim["even_spread"])
        v = [p["vel"] for p in med]
        res["extremes"] = dict(at127=round(sum(x >= 127 for x in v) / len(v), 3),
                               at1=round(sum(x <= 1 for x in v) / len(v), 3), n=len(v))
        gates["extremes"] = bool(res["extremes"]["at127"] <= lim["ext_frac"] and res["extremes"]["at1"] <= lim["ext_frac"])
    allv = [p["vel"] for p in rd]
    if allv:
        res["all_phases"] = dict(stats(allv), at127=round(sum(x >= 127 for x in allv) / len(allv), 3),
                                 at1=round(sum(x <= 1 for x in allv) / len(allv), 3))
    top = [p["vel"] for p in P if p["key"] is not None and p["key"] >= NANALOG]
    if top:
        res["top_keys"] = stats(top)                                 # CONTEXT velocity (no converter on PA6)
    res["gates"] = gates
    res["ok"] = all(gates.values()) if gates else None
    return res


def per_key_phase(presses):
    """{phase: {key name: median velocity}} of the readings"""
    out = {}
    for p in presses:
        if p.get("vel") and p["key"] is not None and p["key"] < NANALOG and p["src"] == 0:
            out.setdefault(p["phase"], {}).setdefault(key_name(p["key"]), []).append(p["vel"])
    return {ph: {k: stats(v)["median"] for k, v in d.items()} for ph, d in out.items()}


def compare(a, b, lim=None):
    """two sessions (dicts as saved): the same presses on two power sources"""
    lim = dict(LIMITS, **(lim or {}))
    pa, pb = per_key_phase(a["presses"]), per_key_phase(b["presses"])
    res = dict(a=dict(label=a.get("label"), power=a.get("power")), b=dict(label=b.get("label"), power=b.get("power")),
               phases={})
    ok = True
    for ph in [p for p in pa if p in pb]:
        va = [x["vel"] for x in a["presses"] if x.get("phase") == ph and x.get("src") == 0 and x.get("key") is not None
              and x["key"] < NANALOG and x.get("vel")]
        vb = [x["vel"] for x in b["presses"] if x.get("phase") == ph and x.get("src") == 0 and x.get("key") is not None
              and x["key"] < NANALOG and x.get("vel")]
        d = round(stats(vb)["median"] - stats(va)["median"], 1)
        keys = {k: round(pb[ph][k] - pa[ph][k], 1) for k in pa[ph] if k in pb[ph]}
        far = {k: v for k, v in keys.items() if abs(v) > lim["power_key"]}
        res["phases"][ph] = dict(median_a=stats(va)["median"], median_b=stats(vb)["median"], diff=d, per_key=keys,
                                 far=far, ok=bool(abs(d) <= lim["power_med"]))
        ok &= res["phases"][ph]["ok"]
    res["ok"] = bool(ok and res["phases"])
    res["same_power"] = a.get("power") == b.get("power")
    return res


# ---- the commands
def _enter():
    """Enter pressed on a terminal (non-blocking; never on a pipe)"""
    try:
        import select
        if not sys.stdin.isatty():
            return False
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            sys.stdin.readline()
            return True
    except (OSError, ValueError):
        pass
    return False


PHASE_TEXT = dict(soft="play every key F3..C5 SOFTLY, 3-5 times each (the top keys C#5..G5 too, once or twice)",
                  medium="play every key F3..C5 at an ORDINARY touch, 4-6 times each (the evenness)",
                  hard="play every key F3..C5 HARD, 3-5 times each")


def record(dev, phases, seconds, power, label, out=None):
    st0 = dev.query((0, 1, 2, 4, 5, 6))
    if st0 is None:
        sys.exit("no CC 42 reply: a firmware without key velocity")
    dev.mode(REPORT_ON)
    presses = []
    for ph in phases:
        print(f"[{ph}] {PHASE_TEXT.get(ph, 'play')}: {seconds} s, Enter ends it", flush=True)
        dev.take()
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds and not _enter():
            time.sleep(0.005)
            dev.poll()
        dev.wait(0.05)
        got = parse(dev.take(), ph)
        presses += got
        print(f"  {len(got)} presses", flush=True)
    dev.mode(REPORT_OFF)
    st1 = dev.query((0, 1, 2, 4, 5, 6))
    rep = dict(label=label, power=power, time=time.strftime("%Y-%m-%d %H:%M:%S"), status_before=named(st0),
               status_after=named(st1), touch=TOUCH[st0[3] & 3], presses=presses)
    rep["analysis"] = analyse(presses)
    if out is None:
        out = os.path.join(OUT, "keyvel", time.strftime("%Y%m%d-%H%M%S") + f"-{label or power}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, "w"), indent=1)
    print_analysis(rep)
    print(f"-> {out}")
    return rep


def _pass(ok):
    return "not measured" if ok is None else ("PASS" if ok else "FAIL")


def print_analysis(rep):
    a = rep["analysis"]
    print(f"{rep.get('label') or ''} power {rep.get('power')}, TOUCH {rep.get('touch')}: {a['n']} presses "
          f"({', '.join(f'{k} {v}' for k, v in a['phases'].items())}); sources "
          + ", ".join(f"{k} {v}" for k, v in a["src"].items() if v) + (f"; {a['unreported']} unreported note-ons"
                                                                        if a["unreported"] else ""))
    g = a["gates"]
    la = a["latency"]
    if la.get("n"):
        print(f"  latency     analog wait {la['min']}..{la['max']} ms (median {la['median']}), timeouts {la['timeouts']}"
              f" ({100 * (la['timeout_frac'] or 0):.1f} %), let go first {la['let_go_first']}: {_pass(g.get('latency'))}")
    if "separation" in a:
        s = a["separation"]
        print(f"  separation  soft median {s['soft']['median']} (q1 {s['soft']['q1']} q3 {s['soft']['q3']}), hard "
              f"{s['hard']['median']} (q1 {s['hard']['q1']} q3 {s['hard']['q3']}): gap {s['gap']}, order {s['order']};"
              f" raw soft {s['raw_soft']['median']} hard {s['raw_hard']['median']} (gap {s['raw_gap']}, order "
              f"{s['raw_order']}): {_pass(g.get('separation'))}")
    else:
        print("  separation  not measured (no soft and hard phase)")
    if "evenness" in a:
        e = a["evenness"]
        print(f"  evenness    per-key medians spread {e['spread']} around {e['median']} ("
              + " ".join(f"{k} {v:g}" for k, v in e["per_key"].items()) + ")"
              + (f"; off by more than {LIMITS['even_off']}: {', '.join(e['off'])}" if e["off"] else "")
              + (f"; missing {', '.join(e['missing'])}" if e["missing"] else "") + f": {_pass(g.get('evenness'))}")
    if "extremes" in a:
        x = a["extremes"]
        print(f"  extremes    {100 * x['at127']:.1f} % at 127, {100 * x['at1']:.1f} % at 1 ({x['n']} presses): "
              f"{_pass(g.get('extremes'))}")
    if "top_keys" in a:
        t = a["top_keys"]
        print(f"  top keys    CONTEXT velocity {t['min']}..{t['max']} (median {t['median']}, {t['n']} presses)")
    print(f"  overall: {_pass(a['ok'])}")


def print_compare(c):
    print(f"A {c['a']['label']} ({c['a']['power']}) vs B {c['b']['label']} ({c['b']['power']})"
          + ("   NOTE: both sessions name the same power" if c["same_power"] else ""))
    for ph, d in c["phases"].items():
        print(f"  {ph:7s} median {d['median_a']} -> {d['median_b']} (B - A {d['diff']:+g}): {_pass(d['ok'])}; per key "
              + " ".join(f"{k} {v:+g}" for k, v in d["per_key"].items())
              + (f"; apart by more than {LIMITS['power_key']}: {', '.join(d['far'])}" if d["far"] else ""))
    print(f"  overall: {_pass(c['ok'] if c['phases'] else None)}")


def soak(dev, minutes, mode="normal", every=10.0):
    """the mode for minutes, nobody at the keys: zero edges"""
    dev.mode(mode)
    dev.wait(0.3)
    dev.take()
    end = time.monotonic() + 60 * minutes
    bad, rows, last = [], [], None
    while True:
        q = dev.query((0, 1, 2))
        t = round(minutes * 60 - (end - time.monotonic()), 1)
        if q is None:
            bad.append(f"{t} s: no CC 42 reply")
            break
        last = named(q)
        notes = [m for _, m in dev.take() if m.type in ("note_on", "note_off")]
        rows.append(dict(t=t, notes=last["notes"], buttons=last["buttons"], midi_notes=len(notes),
                         timeouts=last["timeouts"], batt_to=last["batt_to"], frames=last["frames"], mode=last["mode"]))
        if last["mode"] != MODES[mode]:
            bad.append(f"{t} s: mode {last['mode']}, not {MODES[mode]} ({mode})")
        if last["notes"] or last["buttons"] or notes:
            bad.append(f"{t} s: {last['notes']} key presses, {last['buttons']} button presses, {len(notes)} notes out")
        if not last["frames"]:
            bad.append(f"{t} s: the scan reports no frames")
        if bad or time.monotonic() >= end:
            break
        dev.wait(min(every, max(0.0, end - time.monotonic())))
    dev.mode("normal")
    res = dict(mode=mode, minutes=minutes, rows=rows, last=last, bad=bad, ok=not bad)
    print(f"soak {mode} {minutes} min: " + (f"{len(rows)} polls, 0 edges; timeouts {last['timeouts']}, battery "
                                             f"timeouts {last['batt_to']}, convs {last['convs']}" if last else "")
          + f": {'PASS' if not bad else 'FAIL: ' + '; '.join(bad)}")
    return res


def selftest(dev, lim=None, secs=2.0):
    lim = dict(LIMITS, **(lim or {}))
    dev.mode("selftest")
    dev.take()
    dev.wait(secs)
    q = dev.query((0, 1))
    notes = [m for _, m in dev.take() if m.type in ("note_on", "note_off")]
    dev.mode("normal")
    if q is None:
        print("selftest: no CC 42 reply: FAIL")
        return dict(ok=False, why="no reply")
    v = named(q)
    ok = (v["mode"] == MODES["selftest"] and v["st_n"] > 0 and v["st_pa7"] <= lim["st_max"]
          and v["st_pa8"] <= lim["st_max"] and not v["notes"] and not v["buttons"] and not notes)
    print(f"selftest: {v['st_n']} readings driven low, highest PA7 {v['st_pa7']} PA8 {v['st_pa8']} (must be <= "
          f"{lim['st_max']}); key presses {v['notes']}, button presses {v['buttons']}, notes out {len(notes)}: "
          f"{'PASS' if ok else 'FAIL'}")
    return dict(v, midi_notes=len(notes), ok=bool(ok))


# ---- the jack: the scan's lines and the SNR, per mode
JACK_LINES = (10000 / 22, 10000 / 11, 10000.0)      # 2 frames (rows alternate, MASTER), a frame (11 ticks), the tick


def line_db(x, f, fs=48000, half=1.5, ref=(10.0, 80.0)):
    """the line at f over the local floor (dB): Welch power (0.5 s Hann segments, 2 Hz bins), the strongest bin
    within +-half Hz over the median of the bins ref Hz away (either side). On white noise over 3.8 s (14 segments)
    one take minus the larger of two others stays under +2.9 dB (320 trials; 1.8 s: 3 % over +3 dB), hence the
    jack takes' length"""
    import numpy as np
    x = np.asarray(x, float)
    n = min(len(x), fs // 2)
    w = np.hanning(n)
    segs = [x[i:i + n] for i in range(0, len(x) - n + 1, n // 2)]
    p = np.mean([np.abs(np.fft.rfft((s - s.mean()) * w)) ** 2 for s in segs], axis=0)
    d = np.abs(np.fft.rfftfreq(n, 1 / fs) - f)
    pk = p[d <= half].max()
    flo = np.median(p[(d >= ref[0]) & (d <= ref[1])])
    return float(10 * np.log10((pk + 1e-30) / (flo + 1e-30)))


def jack_check(J, power="usb", lim=None, out=None, dry_run=False):
    """J: hil_jack.Jack / DryJack (or a stand-in): .r.dry(), .r.out.send(), .r.mido, .both(dur, events) -> (usb, jack)"""
    import numpy as np
    import hil_jack as hj
    lim = dict(LIMITS, **(lim or {}))
    FS = hj.FS
    M = J.r.mido.Message

    def mode(v):
        J.r.out.send(M("control_change", control=CC_MODE, value=MODES[v]))
        time.sleep(0.3)

    def block(m):
        J.r.dry()
        mode(m)
        u, j = J.both(4.0, [])
        sil = dict(jack_db=hj.db(j), lines={f"{f:.1f}": line_db(j, f, FS) for f in JACK_LINES})
        u, j = J.both(4.5, [(0.1, "on", 33, 30)])
        J.r.out.send(M("note_off", note=33))
        s = slice(int(0.6 * FS), int(4.4 * FS))
        hiss = dict(jack_hf_db=hj.band_db(j[s], 8000, 20000), usb_hf_db=hj.band_db(u[s], 8000, 20000),
                    lines={f"{f:.1f}": line_db(j[s], f, FS) for f in JACK_LINES})
        J.r.dry()
        mode(m)
        ch = (36, 43, 48, 52, 55, 60, 64)
        u, j = J.both(2.0, [(0.1, "on", n, 127) for n in ch] + [(1.6, "off", n, 0) for n in ch])
        s = slice(int(0.15 * FS), int(1.5 * FS))
        loud = dict(jack_db=hj.db(j[s]), usb_db=hj.db(u[s]))
        return dict(silence=sil, hiss=hiss, loud=loud, snr_db=loud["jack_db"] - hiss["jack_hf_db"])

    rep = dict(power=power, dry_run=bool(dry_run))
    for k, m in (("off", "off"), ("idle", "normal"), ("stress", "stress"), ("off_again", "off")):
        rep[k] = block(m)
    mode("normal")
    J.r.dry()
    off_snr = (rep["off"]["snr_db"] + rep["off_again"]["snr_db"]) / 2
    rep["off_drift_db"] = rep["off_again"]["snr_db"] - rep["off"]["snr_db"]
    rep["snr_idle_vs_off_db"] = rep["idle"]["snr_db"] - off_snr
    rep["snr_stress_vs_off_db"] = rep["stress"]["snr_db"] - off_snr
    new = {}
    for take in ("silence", "hiss"):
        for f in rep["idle"][take]["lines"]:
            ref = max(rep["off"][take]["lines"][f], rep["off_again"][take]["lines"][f])
            new[f"{take} {f} Hz"] = dict(idle=round(rep["idle"][take]["lines"][f] - ref, 2),
                                         stress=round(rep["stress"][take]["lines"][f] - ref, 2))
    rep["lines_re_off_db"] = new
    rep["lines_ok"] = bool(all(v["idle"] <= lim["line_db"] for v in new.values()))
    rep["snr_ok"] = bool(abs(rep["snr_idle_vs_off_db"]) <= lim["snr_db"])
    rep["ok"] = rep["lines_ok"] and rep["snr_ok"]
    if out is None:
        out = os.path.join(OUT, *(["dry"] if dry_run else []), f"keyvel-jack-{power}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, "w"), indent=1)
    tag = "DRY RUN (host build; the jack SIMULATED) " if dry_run else ""
    print(f"{tag}jack, power {power} -> {out}")
    for k in ("off", "idle", "stress", "off_again"):
        q = rep[k]
        print(f"  {k:9s} SNR {q['snr_db']:.2f} dB (loud {q['loud']['jack_db']:.1f}, hiss 8-20k {q['hiss']['jack_hf_db']:.1f}"
              f"); lines muted " + " ".join(f"{f} {v:.1f}" for f, v in q["silence"]["lines"].items())
              + " / under a note " + " ".join(f"{f} {v:.1f}" for f, v in q["hiss"]["lines"].items()) + " dB")
    print(f"  SNR idle - OFF {rep['snr_idle_vs_off_db']:+.2f} dB (within {lim['snr_db']}: "
          f"{'yes' if rep['snr_ok'] else 'NO'}), stress - OFF {rep['snr_stress_vs_off_db']:+.2f} (reported); OFF drift "
          f"{rep['off_drift_db']:+.2f}")
    print("  lines re OFF (idle / stress, dB): " + "; ".join(f"{k} {v['idle']:+.1f} / {v['stress']:+.1f}"
                                                         for k, v in new.items())
          + f": {'PASS' if rep['lines_ok'] else 'FAIL (a new line in idle)'}")
    print(f"  overall: {'PASS' if rep['ok'] else 'FAIL'}")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="the FM-1's key velocity on the device (developer CC 41 / 42)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--phases", default="soft,medium,hard")
    r.add_argument("--seconds", type=float, default=40.0)
    r.add_argument("--power", default="usb", choices=("usb", "battery"))
    r.add_argument("--label", default="")
    r.add_argument("--out")
    a_ = sub.add_parser("analyse")
    a_.add_argument("file")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    s = sub.add_parser("soak")
    s.add_argument("--minutes", type=float, default=10.0)
    s.add_argument("--mode", default="normal", choices=("normal", "stress", "storm"))
    st = sub.add_parser("selftest")
    st.add_argument("--st-max", type=int, default=LIMITS["st_max"])
    sub.add_parser("status")
    j = sub.add_parser("jack")
    j.add_argument("--dev", default="M6")
    j.add_argument("--ch", type=int, default=5)
    j.add_argument("--power", default="usb", choices=("usb", "battery"))
    j.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "analyse":
        rep = json.load(open(a.file))
        rep["analysis"] = analyse(rep["presses"])
        print_analysis(rep)
        return 0 if rep["analysis"]["ok"] is not False else 1
    if a.cmd == "compare":
        res = compare(json.load(open(a.a)), json.load(open(a.b)))
        print_compare(res)
        return 0 if res["ok"] else 1
    if a.cmd == "jack":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import hil_jack as hj
        J = hj.DryJack() if a.dry_run else hj.Jack(a.dev, a.ch)
        return 0 if jack_check(J, a.power, dry_run=a.dry_run)["ok"] else 1
    dev = Dev(*open_ports())
    if a.cmd == "status":
        q = dev.query((0, 1, 2, 4, 5, 6))
        if q is None:
            print("no CC 42 reply: a firmware without key velocity")
            return 1
        for k, v in named(q).items():
            print(f"  {k:10s} {v}" + (f" ({TOUCH[v & 3]})" if k == "touch" else ""))
        return 0
    if a.cmd == "soak":
        return 0 if soak(dev, a.minutes, a.mode)["ok"] else 1
    if a.cmd == "selftest":
        return 0 if selftest(dev, dict(st_max=a.st_max))["ok"] else 1
    rep = record(dev, [p for p in a.phases.split(",") if p], a.seconds, a.power, a.label, a.out)
    return 0 if rep["analysis"]["ok"] is not False else 1


if __name__ == "__main__":
    sys.exit(main())
