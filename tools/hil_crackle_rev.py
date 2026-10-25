#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_crackle.py (stress_max, the cracks test) with a reverb TYPE (0.9.5, CC 95): every take as hil_crackle
plays it, the reverb's TYPE added to its setup, and in the worst-case takes INFINITE frozen part way through, so the
frozen tank holds the chords already played while the rest goes on (a freeze from the first note would close the
input before anything went in).

  hil_crackle_rev.py [--rev-type infinite|plate|hall] [--rev-decay 100] [--freeze-at 4.0] [hil_crackle options]

  --rev-type     the TYPE in every take (default INFINITE): its CC 95 band (tools/hil_cpu_rev.py rev_type_ccs)
  --rev-decay V  DECAY (CC 92) in every take's setup, after hil_crackle's own (its HEAVY has 127); default 100
  --freeze-at S  in each worst-case take (the reverb on), DECAY 127 at S s (INFINITE: frozen from there; the other
                 TYPEs: their longest tail); 0 = no freeze event (the setup's DECAY all take)
Every hil_crackle option passes through (--engine, --rate, --source, --worst-s, --recipe-s, --quick, --host,
--dry-run, --self-test, ...). The report goes to build/hil/stress_max_rev/<type>/ (not over hil_crackle's own).

The ready-to-run commands (FM-1, sandbox off, build/hil/DEVICE.lockdir held; the MOTU M6 input 5 on the jack):
  tools/hil_crackle_rev.py --rate 48000 --dry-run       the takes and the time (~22 min a rate, every engine)
  tools/hil_crackle_rev.py --rate 48000                 every engine, USB + the jack, INFINITE frozen from 4 s
  tools/hil_crackle_rev.py --rate 44100
  tools/hil_crackle_rev.py --host --quick               the host build in the FM-1's place (a check of the wrapper)
"""
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import hil_crackle as hc  # noqa: E402
import hil_cpu_rev as hcr  # noqa: E402

OWN = {"--rev-type": 1, "--rev-decay": 1, "--freeze-at": 1, "--tree": 1}


def split(argv):
    mine, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] in OWN:
            mine[argv[i]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return mine, rest


def patch(typ="INFINITE", decay=100, freeze_at=4.0):
    """hil_crackle's takes with the TYPE: returns the notes printed (raises SystemExit without the TYPEs)"""
    ccs, c95 = hcr.rev_type_ccs()
    if not ccs:
        sys.exit("hil_crackle_rev: this tree has no reverb TYPEs (params.c without P_RV_TYPE)")
    typ = typ.upper()
    if typ not in ccs:
        sys.exit(f"--rev-type: {', '.join(n.lower() for n in ccs)}")
    add = [(c95, ccs[typ]), (hc.CC["RV_DECAY"], max(0, min(127, decay)))]
    orig_ccs, orig_takes = hc.engine_ccs, hc.takes

    def engine_ccs(eng, fx):
        return orig_ccs(eng, fx) + add

    def takes(eng, *a, **k):
        out = []
        for name, cc, ev, dur in orig_takes(eng, *a, **k):
            if freeze_at and "worst" in name and freeze_at < dur:
                ev = sorted(ev + [(float(freeze_at), "cc", hc.CC["RV_DECAY"], 127)], key=lambda e: e[0])
            out.append((name, cc, ev, dur))
        return out
    hc.engine_ccs, hc.takes = engine_ccs, takes
    hc.OUT = os.path.join(hc.ROOT, "build", "hil", "stress_max_rev", typ.lower())
    return [f"reverb TYPE {typ} (CC {c95} = {ccs[typ]}) and DECAY {decay} (CC {hc.CC['RV_DECAY']}) after every "
            f"take's setup" + (f"; the worst-case takes: DECAY 127 at {freeze_at:g} s" +
                               (" (frozen)" if typ == "INFINITE" else "") if freeze_at else ""),
            f"reports: {os.path.relpath(hc.OUT, hc.ROOT)}/"]


def main(argv=None):
    mine, rest = split(list(sys.argv[1:] if argv is None else argv))
    if "--tree" in mine:
        hcr.TREE = os.path.abspath(mine["--tree"])
    for s in patch(mine.get("--rev-type", "infinite"), int(mine.get("--rev-decay", 100)),
                   float(mine.get("--freeze-at", 4.0))):
        print("hil_crackle_rev: " + s, flush=True)
    return hc.main(rest)


if __name__ == "__main__":
    sys.exit(main())
