#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""hil_c1_burn: the two-core build's load limit on the FM-1, found by forcing it (an FR_FAULT image only).

    tools/hil_c1_burn.py [--core c0|c1|both] [--kind spin|flash] [--steps 0:15] [--step-min 5] [--past 0]
                         [--row 277] [--no-audio --location 0x20130000] [--out build/hil/c1burn] [--dry-run]
    tools/hil_c1_burn.py --selftest            (no device: the schedule, the stop rule and the summary)

The row's stimulus is tools/hil_c1_soak.py's, unchanged (its run(): row 277 = every effect at its heaviest, the
REVERB TYPE storm, Program Change bursts, 14-voice chords every 0.25 s, a read every 5 s). On top, extra load on
core 0 (CC 117) and / or core 1 (CC 118) of an FR_FAULT image (frodhes.c flt_burn), stepped up every --step-min
minutes by 100 us from --steps' first value to its last (in 100 us units: 0:15 = 0 ... 1500 us), sent at each step.
After a reset or no reply: burn 0 at once and no burn CC while the unit boots and settles (a crash before 30 s up
counts as a failed boot and escalates to USB RESCUE); the burn is re-armed when the soak resumes its stimulus
(--settle-s after the unit answers again).

Stops after the first step with a late job, a hold for good or core 1 not live (the core is out until a reset: the
steps above would not test two cores); a step with a crash (a reset, or no reply) runs to its end (its crash rate),
then --past more steps, then stops. The report (OUT/burn-report.md + .json, and the soak's own OUT/<row>-report.*):
per step the burn, the worst app / whole / core-1 busy / wait (us), shed, late, holds, crashes, crashes per minute;
the first step that failed and how. The burn runs inside the measured ISR (core 0) or core 1's job: whole / c1busy
include it.

Needs an image built with APP_DEFS=-DFR_FAULT (a release build ignores CC 117 / 118: every step reads like step 0;
the report says so when whole never moves). The device steps are the user's to run.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_c1_soak as S  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "hil", "c1burn")
CC_C0, CC_C1, FLASH = 117, 118, 64


def parse_steps(s):
    a, _, b = s.partition(":")
    a, b = int(a), int(b or a)
    if not (0 <= a <= 15 and 0 <= b <= 15 and a <= b):
        raise argparse.ArgumentTypeError("steps: FIRST:LAST in 100 us units, 0..15, rising")
    return list(range(a, b + 1))


def cc_values(step, core, kind):
    """[(cc, value)] for a step (100 us units) on core c0 / c1 / both, kind spin / flash"""
    v = step | (FLASH if kind == "flash" and step else 0)
    return [(c, v) for c in ((CC_C0,) if core == "c0" else (CC_C1,) if core == "c1" else (CC_C0, CC_C1))]


class Sweep:
    """the schedule and the stop rule: step(t) the step index at t s; see(r) each decoded read; stop() whether the
    run must end now"""

    def __init__(self, steps, step_s, past=0):
        self.steps, self.step_s, self.past = steps, step_s, past
        self.first_bad = None            # (index, why): the first step with a crash, a late job or a hold
        self.out = False                 # core 1 out (late / hold / not live): stop at once
        self.seen = False                # a read seen (the boot record before the run is not a crash)
        self.done = False                # the sweep over: replies after it are not judged

    def step(self, t):
        return min(int(t // self.step_s), len(self.steps) - 1)

    def see(self, r, t):
        """a decoded reply at t s: the soak's reads (query 127 + 30: whole_us and state), its boot record queries
        (32: only "boot"; the first one, before any read, is the boot before the run) and its waits after a reset
        (30); an empty one after the first read = no reply"""
        if self.done:
            return r
        i, empty = self.step(t), not r
        r["burn_us"] = self.steps[i] * 100
        r["step"] = i
        why = None
        if "whole_us" in r or "app_us" in r:
            self.seen = True
        if not self.seen:
            return r
        if r.get("boot") and "state" not in r:
            why = "crash (reset)"
        elif empty:
            why = "no reply (hang or reset)"
        elif r.get("late") or r.get("holds") or r.get("state", 1) != 1:
            why = (f"core 1 {S.STATE.get(r.get('state'), r.get('state'))}, late {r.get('late')}, holds "
                   f"{r.get('holds')}")
            self.out = True
        if why and self.first_bad is None:
            self.first_bad = (i, why)
        return r

    def stop(self, t):
        if self.out:
            return True
        if t >= self.step_s * len(self.steps):
            return True
        if self.first_bad is not None:
            return self.step(t) > self.first_bad[0] + self.past
        return False


def summarize(reads, steps, step_s):
    """per step: burn, worst app / whole / busy / wait, shed, late, holds, crashes (+ per minute)"""
    rows = []
    for i, st in enumerate(steps):
        rs = [r for r in reads if r.get("step") == i and "settled" not in r]
        if not rs:
            continue
        sh = [r["shed"] for r in rs if "shed" in r]
        crashes = sum(1 for r in rs if r.get("boot"))
        hangs = sum(1 for r in rs if r.get("noreply"))
        rows.append(dict(step=i, burn_us=st * 100, reads=len(rs),
                         **{k: max((r.get(k, 0) for r in rs), default=0)
                            for k in ("app_us", "whole_us", "busy_us", "wait_us", "late", "holds")},
                         shed=(max(sh) - min(sh)) if sh else 0, crashes=crashes, noreply=hangs,
                         crash_per_min=round(crashes / (step_s / 60.0), 3)))
    return rows


def write(out, meta, rows, sweep):
    os.makedirs(out, exist_ok=True)
    fb = sweep.first_bad
    json.dump(dict(meta, steps=rows, first_bad=fb), open(os.path.join(out, "burn-report.json"), "w"), indent=1)
    lines = [f"# Two-core burn sweep, row {meta['row']}: core {meta['core']}, {meta['kind']}, {meta['step_min']} min a "
             f"step" + ("" if meta["audio"] else ", NO USB AUDIO"), ""]
    if fb is None:
        lines.append(f"- no crash, late job or hold up to {rows[-1]['burn_us'] if rows else 0} us extra")
    else:
        lines.append(f"- first failure: step {fb[0]} ({meta['steps'][fb[0]] * 100} us extra): {fb[1]}")
        ok = [r for r in rows if r["step"] < fb[0]]
        if ok:
            lines.append(f"- last clean step: {ok[-1]['burn_us']} us extra: worst whole {ok[-1]['whole_us']}, core-1 "
                         f"busy {ok[-1]['busy_us']} us")
    if rows and len({r["whole_us"] for r in rows}) == 1 and len(rows) > 2:
        lines.append("- WARNING: whole never moved: not an FR_FAULT image? (a release build ignores CC 117 / 118)")
    lines += ["", "| burn us | reads | app | whole | c1 busy | wait | shed | late | holds | crashes | no reply | "
              "crashes/min |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r[k]) for k in ("burn_us", "reads", "app_us", "whole_us", "busy_us",
                                                          "wait_us", "shed", "late", "holds", "crashes", "noreply",
                                                          "crash_per_min")) + " |")
    open(os.path.join(out, "burn-report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


class BurnOut:
    """the soak's MIDI output with the burn on top: the step's CCs before a message when the step changed (or the
    burn is re-armed); KeyboardInterrupt (the soak's own way to end a run: it writes its report) when the sweep stops.
    After a reset (reset_seen: a crash or no reply) burn 0 at once and no burn CC at all while the unit boots and
    settles (a crash before 30 s up counts as a failed boot: RESCUE): re-armed only when the soak resumes its
    stimulus, a message other than its CC 116 queries, settle_s after the unit's first answer (answered)"""

    def __init__(self, out, sweep, core, kind, mido, clock=None, settle_s=35.0):
        self.o, self.sw, self.core, self.kind, self.mido = out, sweep, core, kind, mido
        self.clock = clock or (lambda: time.monotonic())
        self.t0, self.sent_step, self.ended = None, None, False
        self.settle_s, self.paused, self.up_t, self.resets = settle_s, False, None, 0

    def __getattr__(self, k):
        return getattr(self.o, k)

    def t(self):
        if self.t0 is None:
            self.t0 = self.clock()
        return self.clock() - self.t0

    def burn(self, step):
        for c, v in cc_values(step, self.core, self.kind):
            self.o.send(self.mido.Message("control_change", control=c, value=v))

    def reset_seen(self):
        if self.paused:
            return
        self.paused, self.up_t, self.resets = True, None, self.resets + 1
        print(f"{self.t():7.0f} s  a reset / no reply: burn 0, no burn until the soak resumes", flush=True)
        self.burn(0)

    def answered(self):
        if self.paused and self.up_t is None:
            self.up_t = self.t()

    def send(self, msg):
        t = self.t()
        if not self.ended:
            if self.sw.stop(t):
                self.ended = self.sw.done = True
                self.burn(0)
                raise KeyboardInterrupt
            query = getattr(msg, "type", None) == "control_change" and getattr(msg, "control", None) == 116
            if self.paused and self.up_t is not None and not query and t - self.up_t >= self.settle_s - 0.5:
                self.paused, self.sent_step = False, None
                print(f"{t:7.0f} s  the soak resumed: burn re-armed", flush=True)
            i = self.sw.step(t)
            if not self.paused and i != self.sent_step:
                print(f"{t:7.0f} s  burn step {i}: {self.sw.steps[i] * 100} us extra on core {self.core} "
                      f"({self.kind})", flush=True)
                self.burn(self.sw.steps[i])
                self.sent_step = i
        self.o.send(msg)


def run(a):
    sw = Sweep(a.steps, a.step_min * 60.0, a.past)
    box = {}
    orig_ports, orig_decode = S.ports, S.decode

    def ports(sa):
        mido, out, inp = orig_ports(sa)
        box["out"] = BurnOut(out, sw, a.core, a.kind, mido, settle_s=a.settle_s)
        box["mido"] = mido
        return mido, box["out"], inp

    def decode(msgs):
        r = orig_decode(msgs)
        o = box.get("out")
        if o is not None and o.t0 is not None:
            empty = not r
            sw.see(r, o.t())
            if sw.seen and not sw.done and ((r.get("boot") and "state" not in r) or empty):
                o.reset_seen()                          # (a crash or no reply: burn 0, no burn while it boots)
            elif r.get("state") is not None:
                o.answered()
        return r

    S.ports, S.decode = ports, decode
    sa = argparse.Namespace(row=a.row, minutes=a.step_min * len(a.steps) + 1.0, rate=a.rate, out=a.out,
                            no_audio=a.no_audio, location=a.location, port=a.port, settle_s=a.settle_s,
                            stop_on_fail=False)
    try:
        S.run(sa)
    finally:
        S.ports, S.decode = orig_ports, orig_decode
        if "out" in box:
            box["out"].ended = sw.done = True
            box["out"].burn(0)
    rep = json.load(open(os.path.join(a.out, f"{a.row}-report.json")))
    rows = summarize(rep["reads"], a.steps, a.step_min * 60.0)
    write(a.out, dict(row=a.row, core=a.core, kind=a.kind, step_min=a.step_min, steps=a.steps, audio=not a.no_audio,
                      location=a.location), rows, sw)
    return 0 if sw.first_bad is None else 1


def selftest():
    sw = Sweep([0, 1, 2, 3], 60.0, past=0)
    assert [sw.step(t) for t in (0, 59, 60, 179, 500)] == [0, 0, 1, 2, 3]
    sw.see(dict(boot={"p3_rst": "0x4"}), 1.0)                                    # (the boot before the run)
    sw.see({}, 1.5)
    assert sw.first_bad is None
    assert cc_values(5, "c1", "flash") == [(118, 69)] and cc_values(0, "both", "flash") == [(117, 0), (118, 0)]
    assert cc_values(3, "c0", "spin") == [(117, 3)]
    r = sw.see(dict(state=1, whole_us=4000, late=0, holds=0), 70.0)
    assert r["burn_us"] == 100 and sw.first_bad is None and not sw.stop(70.0)
    sw.see(dict(boot={"p3_rst": "0x4"}), 130.0)                                   # a crash in step 2
    assert sw.first_bad == (2, "crash (reset)") and not sw.stop(150.0) and sw.stop(180.0)
    sw2 = Sweep([0, 1, 2], 60.0)
    sw2.see(dict(state=1, whole_us=4000), 1.0)
    sw2.see(dict(state=3, late=1, holds=1, whole_us=5000), 61.0)
    assert sw2.out and sw2.stop(61.0) and sw2.first_bad[0] == 1
    sw3 = Sweep([0, 1], 60.0, past=1)
    sw3.see(dict(state=1, whole_us=4000), 5.0)
    sw3.see({}, 10.0)
    assert sw3.first_bad == (0, "no reply (hang or reset)") and not sw3.stop(100.0) and sw3.stop(120.0)
    sw3.done = True
    sw4 = Sweep([0], 60.0)
    sw4.seen, sw4.done = True, True
    sw4.see({}, 10.0)
    assert sw4.first_bad is None                                                  # (the fault-record query at the end)
    reads = [dict(step=0, whole_us=4000, busy_us=3000, shed=5), dict(step=0, whole_us=4100, shed=5),
             dict(step=1, whole_us=4300, boot={}, shed=5), dict(step=1, settled=30), dict(step=1, whole_us=4200, shed=7)]
    rows = summarize(reads, [0, 1], 300.0)
    assert rows[0]["whole_us"] == 4100 and rows[0]["shed"] == 0 and rows[0]["crashes"] == 0
    assert rows[1]["crashes"] == 0 and rows[1]["shed"] == 2 and rows[1]["reads"] == 2      # ({} boot: no record)
    reads[2]["boot"] = {"p3_rst": "0x4"}
    assert summarize(reads, [0, 1], 300.0)[1]["crash_per_min"] == 0.2

    class O:
        def __init__(self):
            self.m = []

        def send(self, m):
            self.m.append(m)

    class M:
        @staticmethod
        def Message(t, **k):
            return (t, k.get("control"), k.get("value"))

    clk = [0.0]
    o, sw5 = O(), Sweep([0, 4], 10.0)
    b = BurnOut(o, sw5, "c1", "spin", M, clock=lambda: clk[0])
    b.send("x")
    clk[0] = 2.5
    b.send("z")
    clk[0] = 11.0
    b.send("w")
    assert o.m == [("control_change", 118, 0), "x", "z", ("control_change", 118, 4), "w"], o.m
    clk[0] = 21.0
    try:
        b.send("v")
        raise AssertionError("no stop")
    except KeyboardInterrupt:
        pass
    assert o.m[-1] == ("control_change", 118, 0)
    b.send("off")                                                                   # (after the stop: passed through)
    assert o.m[-1] == "off"
    print("hil_c1_burn selftest: ok")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", choices=("c0", "c1", "both"), default="c1")
    ap.add_argument("--kind", choices=("spin", "flash"), default="flash")
    ap.add_argument("--steps", type=parse_steps, default=parse_steps("0:15"), help="FIRST:LAST in 100 us (0:15)")
    ap.add_argument("--step-min", type=float, default=5.0)
    ap.add_argument("--past", type=int, default=0, help="steps to run after the first one with a crash (rates)")
    ap.add_argument("--row", choices=sorted(S.ROWS), default="277")
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--settle-s", type=float, default=35.0)
    ap.add_argument("--port", metavar="NAME")
    ap.add_argument("--location", metavar="LOC")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return 0
    if a.dry_run:
        print(json.dumps(dict(core=a.core, kind=a.kind, steps_us=[s * 100 for s in a.steps], step_min=a.step_min,
                              past=a.past, total_min=a.step_min * len(a.steps), row=a.row, audio=not a.no_audio,
                              location=a.location, ccs=[cc_values(s, a.core, a.kind) for s in a.steps]), indent=1))
        return 0
    if a.port and a.location is None:
        import fm1_ports
        fm1_ports.force(a.port, None)
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
