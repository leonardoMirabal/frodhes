#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The dry distortion gate on the Move, over the plugin's tap (dev_play, move_hil.TapRig): hil_jack.py --dist's Move
version, with the device's cells compared with the Move's host build cell by cell.

    tools/move_dist.py [--only TAG,TAG] [--ws URL] [--host USER@HOST] [--out DIR]

The same scripts as the host gate (tools/dry_dist.py): FXOFF (CC 111) and RAW (CC 119), the four Rhodes TYPEs, A and
B; VIBES (CC 79 0, the motor off) and MARIMBA (CC 79 127) on the same scripts without the TYPE. Per tag: the device's
state set as the host build boots (prep: hil_scan's dry state, then the compressor on, COMP_ON), the take on the
tap, dry_dist's cells. Rhodes cells are judged by dry_dist's limits against its HQ baseline (BASE_HQ). Every tag:
the device against the host build's render of the same script (move_hil.host_binary, HQ), H1 within EQ_H1_DB per
cell. Exit 0 PASS, 1 FAIL, 2 no device. Takes the MOVE lock. report.json in build/move/hil/<time>-dist/.

Never run from a test or a gate without the user's OK to use the Move."""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist as dd  # noqa: E402
import hil_scan as hs  # noqa: E402
import move_hil as mh  # noqa: E402

ENG = {"VIBE": ((79, 0), (47, 0)), "MRMB": ((79, 127),)}   # (the engine's CC 79; VIBES' motor off, CC 47 0)
# The host build boots with F01's compressor on, and dry_dist.TYPE_BASE leaves its switch as the boot has it.
# hil_scan's dry state turns it off (DRY CMP_SW 0), and FX OFF keeps the compressor (FR_BYP_KEEP_CMP): with it off,
# every FXOFF cell on the device read H1 1.45 dB under the host's (all engines; RAW, which skips it, was exact;
# 2026-11-15). So the device's prep ends with the compressor on, as the host has it
COMP_ON = (106, 127)
EQ_H1_DB = 0.5                       # the device's H1 against the host's, per cell (the same code: the tap is exact)
H1_FLOOR_DB = dd.LSB_DB              # ... judged where the host's H1 is over one 16-bit step: MARIMBA's key 93 (over
                                     # its bars' range) reads H1 -134 .. -180 dB, rounding, and differed by 2.1 dB there


class _Sent(hs.RigBase):
    """records what dry() sends (prep_ccs)"""
    reset = False

    def __init__(self):
        self.sent = []

    def _send(self, kind, a, b=0):
        self.sent.append((kind, int(a), int(b)))

    def wait_silence(self, *a, **k):
        return True


def prep_ccs():
    """the device's state before a take, as messages [(kind, a, b)]: hil_scan's dry state, then COMP_ON"""
    s = _Sent()
    s.dry()
    return s.sent + [("cc",) + COMP_ON]


def prep(r):
    """the rig r (move_hil.TapRig) to the host build's boot state"""
    for m in prep_ccs():
        r._send(*m)
    time.sleep(0.05)


def tags():
    t = list(dd.tags())
    for c, ccc in dd.CONFIGS:
        for e in ENG:
            for m, mcc in dd.METHODS:
                t.append((f"{c}-{e}-{m}", ccc, e, mcc))
    return t


def script(tag):
    """dry_dist's script for tag (VIBE / MRMB: without the TYPE's CC 80, the engine's CCs at 110 ms)"""
    _, ccc, ty, mcc = next(x for x in tags() if x[0] == tag)
    if ty in ENG:
        ls = [x for x in dd.script(ccc, 0, mcc, keys=False) if " 0xB0 80 " not in x]
        return ls + ["%d midi 0xB0 %d %d" % (110 + i, c, v) for i, (c, v) in enumerate(ENG[ty])]
    return dd.script(ccc, ty, mcc, keys=False)


def events(tag):
    ev = []
    for line in script(tag):
        f = line.split()
        if f[1] == "end":
            return sorted(ev, key=lambda e: e[0]), int(f[0]) / 1000 + 0.3
        st, a, b = int(f[2], 16), int(f[3]), int(f[4])
        ev.append((int(f[0]) / 1000, {0xB0: "cc", 0x90: "on", 0x80: "off"}[st], a, b))


def h1_diffs(dev, host):
    """[(label, device H1 - host H1)] over the cells both have where the host's H1 is over H1_FLOOR_DB"""
    return [(lab, dev[lab]["h1"] - host[lab]["h1"]) for lab in dev if "h1" in dev[lab] and lab in host
            and "h1" in host[lab] and host[lab]["h1"] > H1_FLOOR_DB]


def run(a, out=print):
    d = a.out or os.path.join(mh.OUT, time.strftime("%Y%m%d-%H%M%S") + "-dist")
    os.makedirs(d, exist_ok=True)
    order = [t for t in tags() if not a.only or t[0] in a.only.split(",")]
    binary = mh.host_binary(1)
    host = {t[0]: dd.measure_wave(dd.render(binary, t[0], script(t[0]), os.path.join(d, "host")), 0.0, dd.FS,
                                  keys=False) for t in order}
    r = mh.TapRig(a.ws, mh.scp_fetch(a.host, os.path.join(d, "tap")), 1)
    cells, worst = {}, {}
    with mh.KeptState(a.ws, 0) as ks:
        for tag, *_ in order:
            prep(r)
            ty = tag.split("-")[1]
            if ty in ENG:                         # (the engine set in a quiet take of its own: a TYPE change waits)
                for c, v in ENG[ty]:
                    r._send("cc", c, v)
                r._send("cc", 123, 0)
                r.rec(0.6, [])
            ev, dur = events(tag)
            take = r.rec(dur, ev)
            u, sr = take["x"].mean(1), take["sr"]
            for cc in (111, 119):
                r._send("cc", cc, 0)
            first = min(e[0] for e in ev if e[1] == "on")
            s0 = int((first - 0.005) * sr)
            hit = np.flatnonzero(np.abs(u[s0:]) > 30)
            t0 = (s0 + hit[0]) / sr - first if len(hit) else 0.0
            cells[tag] = dd.measure_wave(u, t0, sr)
            df = h1_diffs(cells[tag], host[tag])
            worst[tag] = max(df, key=lambda x: abs(x[1])) if df else ("-", 0.0)
            out("  %-14s H1 device - host: worst %s %+.2f dB" % (tag, worst[tag][0], worst[tag][1]), flush=True)
        prep(r)
    base = json.load(open(dd.BASE_HQ))
    rows = dd.judge({t: c for t, c in cells.items() if t in base}, base)
    fails = [[c, m, float(v), b, lim] for c, m, v, b, lim, ok in rows if not ok]
    eq_bad = {t: w for t, w in worst.items() if abs(w[1]) > EQ_H1_DB}
    ok = not fails and not eq_bad
    rep = dict(cells=cells, host=host, h1_worst=worst, eq_bad=eq_bad, fails=fails, ok=ok, state=ks.report)
    json.dump(rep, open(os.path.join(d, "report.json"), "w"), indent=1, default=str)
    if rows:
        out(dd.table(rows, only_bad=bool(fails), worst=30))
    out("device vs host H1: %s" % ("all within %.1f dB" % EQ_H1_DB if not eq_bad else "OFF %s" % eq_bad))
    out("dry distortion (Move tap): %s; %s" % ("PASS" if ok else "FAIL", d))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="tags, comma-separated (e.g. FXOFF-ST77-B,RAW-VIBE-A)")
    ap.add_argument("--ws", default=os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui"))
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    import move_install as mi
    with mi.move_lock("move_dist"):
        return run(a)


if __name__ == "__main__":
    sys.exit(main())
