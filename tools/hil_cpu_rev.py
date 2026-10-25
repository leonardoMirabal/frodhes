#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_cpu.py with the reverb TYPE (0.9.5: CC 95, OFF / HALL / PLATE / INFINITE), the heaviest drive TYPE (CC
84) and the TAPE delay (CC 88) in its effect scenarios: the release's CPU run on every engine x every reverb TYPE.

hil_cpu has no option for these TYPEs: every reset sends param_defaults() (the reverb's TYPE at HALL, the drive at
FDRV) and its HEAVY sets DL_SW=80 (ANA). This wrapper runs hil_cpu unchanged but filters what it sends to the FM-1:
  CC 95 > 0  -> the chosen reverb TYPE's band (params.c P_RV_TYPE: a band table if params.c has one, else the
                frodhes.c midi_cc banding v * (hi + 1) >> 7, the band's middle); 0 (OFF) passes unchanged
  CC 84 > 0  -> the heaviest drive TYPE's band (params.c DRV_CC84; DRV_US_<T> under its AA switch)
  CC 88 > 0  -> TAPE's band
  --spread N   CC 105 (SPREAD) = N before the notes (it is set at each strike; the Rhodes only)
  --rev-decay V / --rev-size V / --rev-mod V / --rev-locut V: the reverb's DECAY (CC 92) / SIZE / MOD / LOW CUT set
               after every reset that has the reverb on (MIX, CC 91 > 0): --rev-decay 127 on INFINITE is the freeze.
               LOW CUT is on (64) by default with a TYPE chosen: its filter costs (rev_types.c REV_US_LOCUT), and
               the CPU run is "every effect at its heaviest"; --rev-locut 0 leaves it off.
The CCs of SIZE / MOD / LOW CUT come from params.c (their PARAMS[] lines).

    hil_cpu_rev.py [--dry-run] [--rev-type hall|plate|infinite|heaviest|all] [--rev-decay V] [--rev-size V]
                   [--rev-mod V] [--rev-locut V] [--drive-type NAME] [--no-tape] [--spread N] [--tree DIR]
                   [-- <hil_cpu options>]
--rev-type all: one whole hil_cpu run per TYPE (HALL, PLATE, INFINITE; the MIDI ports and the USB stream opened once),
then a table of each run's worst block against HALL's (the gate: HALL + 200 us; the release rule: whole ISR <= 4200
us). heaviest (the default): the TYPE with the largest REV_US_* in the tree (rev_plate.c / rev_inf.c), INFINITE when
none is known. A tree without the reverb TYPEs (params.c has no P_RV_TYPE): CC 95 is left alone and that is printed.
--dry-run prints the choices, the rewrites, the planned runs and their time (hil_cpu --worst: ~4 min a run; a full
run ~15 min) and hil_cpu's --help (no MIDI, no audio).
--tree DIR (or TREE=DIR): read params.c and the kernels from another checkout (the release tree); default this one.

The ready-to-run commands (FM-1, sandbox off, the coordinator's slot; build/hil/DEVICE.lockdir held):
  every engine x every TYPE, the worst case, USB at 48 kHz, 3 takes each (hil_cpu's own):
    for e in rhodes vibes marimba; do tools/hil_cpu_rev.py --rev-type all -- --engine $e --worst --usb 48000; done
  the tight case first (the plan): the Rhodes, SPREAD 127, the bend, INFINITE frozen:
    tools/hil_cpu_rev.py --rev-type infinite --rev-decay 127 --spread 127 -- --worst --usb 48000 --bend-sweep
"""
import os
import re
import sys
import time

TREE = os.path.abspath(os.environ.get("TREE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)                              # (hil_cpu, hil_scan: this checkout's; --tree's params.c is
                                                       # read here, and the TYPE is sent after every reverb CC 91 > 0
                                                       # whatever param_defaults() sends)
REV_TYPES = ("HALL", "PLATE", "INFINITE")              # P_RV_TYPE 1, 2, 3 (0 OFF)
CC_MIX, CC_DECAY, CC_TYPE_DEFAULT = 91, 92, 95
RUN_MIN = dict(worst=4.5, full=15.0)                   # a hil_cpu run's minutes (--worst: 3 scenarios x 3 takes)


def _src(rel):
    p = os.path.join(TREE, rel)
    return open(p).read() if os.path.exists(p) else ""


def _int(s):
    return int(re.sub(r"[uUlL]+$", "", s), 0)


def _eval(expr, defs):
    """a C preprocessor condition of plain integer macros: defined(X), !, &&, ||, comparisons"""
    e = re.sub(r"/\*.*?\*/|/\*.*$|//.*", "", expr)
    e = re.sub(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", lambda m: "1" if (m.group(1) or m.group(2)) in defs
               else "0", e)
    e = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]*\b", r"\1", e)
    e = re.sub(r"\b[A-Za-z_]\w*\b", lambda m: str(defs.get(m.group(0), 0)), e)
    e = e.replace("&&", " and ").replace("||", " or ")
    e = re.sub(r"!(?!=)", " not ", e)
    try:
        return bool(eval(e, {"__builtins__": {}}))  # noqa: S307 (integers and operators only, from our own sources)
    except Exception:
        return False


def defines(text, defs, want=None):
    """walk text's #if / #ifdef / #ifndef / #elif / #else / #endif; add the active integer #defines to defs; returns
    the value of #define `want` where it is active (or None)"""
    stack, found = [], None                           # (this branch active, a branch of this #if was taken)
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"#\s*(if|ifdef|ifndef|elif|else|endif|define)\b\s*(.*)", s)
        if not m:
            continue
        kw, rest = m.groups()
        live = all(a for a, _ in stack)
        if kw in ("if", "ifdef", "ifndef"):
            name = rest.split()[0] if rest.split() else ""
            c = (_eval(rest, defs) if kw == "if" else (name in defs) if kw == "ifdef" else (name not in defs))
            stack.append((live and c, c))
        elif kw == "elif" and stack:
            _, taken = stack.pop()
            outer = all(a for a, _ in stack)
            c = not taken and _eval(rest, defs)
            stack.append((outer and c, taken or c))
        elif kw == "else" and stack:
            _, taken = stack.pop()
            outer = all(a for a, _ in stack)
            stack.append((outer and not taken, True))
        elif kw == "endif" and stack:
            stack.pop()
        elif kw == "define" and live:
            d = re.match(r"(\w+)\s+\(?\s*(0x[0-9a-fA-F]+|\d+)[uUlL]*\s*\)?\s*(?:/[*/].*)?$", rest)
            if d:
                defs[d.group(1)] = _int(d.group(2))
                if d.group(1) == want:
                    found = defs[d.group(1)]
    return found


def bands(table):
    """[(lo, hi)] per TYPE index from a band-start table (the CC 79 / CC 84 way: a TYPE's band runs to the next start)"""
    ends = sorted(table) + [128]
    return [(lo, ends[ends.index(lo) + 1] - 1) for lo in table]


def pline(name):
    """(hi, def, cc) of params.c's [P_<name>] line, or None"""
    m = re.search(r"\[P_" + name + r"\] = \{\"[^\"]*\", (\d+), (\d+), F_\w+, (\d+)", _src("firmware/src/params.c"))
    return tuple(int(v) for v in m.groups()) if m else None


def generic_cc(v, hi):
    """the middle of the CC band that frodhes.c midi_cc maps to value v (x = cc * (hi + 1) >> 7)"""
    vs = [c for c in range(128) if c * (hi + 1) >> 7 == v]
    return vs[len(vs) // 2] if vs else None


def rev_type_ccs():
    """{name: CC value} for HALL / PLATE / INFINITE on P_RV_TYPE's CC (and the CC), or ({}, None) without them"""
    pl = pline("RV_TYPE")
    if not pl:
        return {}, None
    hi, _, c = pl
    p = _src("firmware/src/params.c")
    m = re.search(r"\bRV_CC\w*\[\d*\]\s*=\s*\{([^}]*)\}", p)
    if m:                                             # a band-start table (the CC 84 way)
        b = bands([int(v) for v in m.group(1).split(",") if v.strip()])
        return {n: (b[i + 1][0] + b[i + 1][1]) // 2 for i, n in enumerate(REV_TYPES)}, c
    return {n: generic_cc(i + 1, hi) for i, n in enumerate(REV_TYPES)}, c


def rev_costs():
    """{name: us} the kernels' REV_US_<T> (rev_plate.c REV_US_PLATE, rev_inf.c REV_US_INF, under their own #if
    switches, e.g. REV_INF_QUARTER), HALL 0 (the reference); None where the tree does not say"""
    out = dict(HALL=0, PLATE=None, INFINITE=None)
    for name, f, d in (("PLATE", "rev_plate.c", "REV_US_PLATE"), ("INFINITE", "rev_inf.c", "REV_US_INF")):
        t = _src(f"firmware/src/{f}")
        out[name] = defines(t, {}, d) if t else None
    return out


def heaviest():
    c = rev_costs()
    known = [(us, n) for n, us in c.items() if us is not None and n != "HALL"]
    return max(known)[1] if known else "INFINITE"


def drive_types():
    """[(name, index, us, cc84 value)] for the circuit TYPEs (index >= 2) of the tree, or []"""
    p = _src("firmware/src/params.c")
    m = re.search(r"DRV_CC84\[\d*\]\s*=\s*\{([^}]*)\}", p)
    n = re.search(r"N_DRV\[\]\s*=\s*\{([^}]*)\}", p)
    if not m or not n:
        return []
    table = [int(v) for v in m.group(1).split(",") if v.strip()]
    names = re.findall(r'"([^"]*)"', n.group(1))
    hdr = {}
    defines(_src("firmware/gen/drive_tables.h"), hdr)
    out = []
    for i, name in enumerate(names):
        if i < 2:
            continue                                  # OFF, FDRV (the drive before: not a circuit kernel)
        c = _src(f"firmware/src/drv_{name.lower()}.c")
        us = defines(c, dict(hdr), f"DRV_US_{name}") if c else None
        lo, hi = bands(table)[i]
        out.append((name, i, us, (lo + hi) // 2))
    return out


def tape_cc():
    """the CC 88 value for TAPE (params.c N_DL; frodhes.c midi_cc: v * (hi + 1) >> 7), or None"""
    p = _src("firmware/src/params.c")
    n = re.search(r"N_DL\[\]\s*=\s*\{([^}]*)\}", p)
    h = re.search(r"\[P_DL_SW\]\s*=\s*\{\"[^\"]*\",\s*(\d+)", p)
    if not n or not h or "TAPE" not in n.group(1):
        return None
    return generic_cc(re.findall(r'"([^"]*)"', n.group(1)).index("TAPE"), int(h.group(1)))


class Out:
    """the MIDI output with CC values rewritten (counts kept): rw {cc: value} for values > 0; after: {cc: [(cc, v)]}
    sent right after a CC (> 0) passes; spread: CC 105 before the notes"""

    def __init__(self, port, rw, after=None, spread=None):
        self.port, self.rw, self.after, self.spread, self.t = port, dict(rw), dict(after or {}), spread, 0.0
        self.n = {}

    def _count(self, c):
        self.n[c] = self.n.get(c, 0) + 1

    def send(self, msg):
        extra = []
        if msg.type == "control_change" and msg.value > 0:
            if msg.control in self.rw:
                msg = msg.copy(value=self.rw[msg.control])
                self._count(msg.control)
            extra = self.after.get(msg.control, [])
        if self.spread is not None and msg.type == "note_on" and time.monotonic() - self.t > 0.5:
            self.port.send(msg.__class__("control_change", channel=msg.channel, control=105, value=self.spread))
            self.t = time.monotonic()                  # SPREAD (CC 105) before the notes: it is set at each strike
            self._count(105)
        self.port.send(msg)
        for c, v in extra:
            self.port.send(msg.__class__("control_change", channel=msg.channel, control=c, value=v))
            self._count(c)

    def __getattr__(self, k):
        return getattr(self.port, k)


def opt(argv, name, conv=int, default=None):
    return conv(argv[argv.index(name) + 1]) if name in argv else default


OWN = {"--rev-type": 1, "--rev-decay": 1, "--rev-size": 1, "--rev-mod": 1, "--rev-locut": 1, "--drive-type": 1,
       "--spread": 1, "--tree": 1, "--dry-run": 0, "--no-tape": 0}


def split_args(argv):
    """(this tool's options, hil_cpu's): hil_cpu's after "--", or everything that is not ours"""
    if "--" in argv:
        k = argv.index("--")
        return argv[:k], argv[k + 1:]
    mine, rest, i = [], [], 0
    while i < len(argv):
        n = OWN.get(argv[i])
        if n is None:
            rest.append(argv[i])
            i += 1
        else:
            mine += argv[i:i + 1 + n]
            i += 1 + n
    return mine, rest


def plan(mine):
    """the runs: [(label, rw, after, notes)] and the shared notes (what the tree says)"""
    global TREE
    if "--tree" in mine:
        TREE = os.path.abspath(opt(mine, "--tree", str))
    notes, rw, after = [], {}, {}
    types = drive_types()
    force = opt(mine, "--drive-type", str)
    if types:
        for name, i, us, v in types:
            notes.append(f"drive TYPE {name} (index {i}): DRV_US {us if us is not None else '?'} us, CC 84 = {v}")
        pick = ([t for t in types if t[0] == force.upper()] if force else
                sorted([t for t in types if t[2] is not None], key=lambda t: -t[2])[:1]) or types[-1:]
        name, i, us, v = pick[0]
        rw[84] = v
        notes.append(f"heaviest drive: {name} ({us} us, compiled estimate) -> every CC 84 > 0 sent as {v}")
    else:
        notes.append("no drive TYPEs in this tree (params.c has no DRV_CC84): CC 84 left as hil_cpu sends it")
    tv = tape_cc() if "--no-tape" not in mine else None
    if tv is not None:
        rw[88] = tv
        notes.append(f"delay TAPE: every CC 88 > 0 sent as {tv}")
    elif "--no-tape" not in mine:
        notes.append("no TAPE delay in this tree's params.c: CC 88 left as hil_cpu sends it")
    ccs, c95 = rev_type_ccs()
    runs = []
    if not ccs:
        notes.append("no reverb TYPEs in this tree (params.c has no P_RV_TYPE): the reverb as hil_cpu sends it (HALL)")
        return [("HALL (no TYPEs)", rw, after)], notes
    cost = rev_costs()
    notes.append(f"reverb TYPE on CC {c95}: " + ", ".join(f"{n} = {v} (REV_US {cost[n] if cost[n] is not None else '?'})"
                                                          for n, v in ccs.items()))
    want = (opt(mine, "--rev-type", str) or "heaviest").upper()
    names = list(REV_TYPES) if want == "ALL" else [heaviest() if want == "HEAVIEST" else want]
    for n in names:
        if n not in ccs:
            sys.exit(f"--rev-type: hall, plate, infinite, heaviest or all (not {n.lower()})")
    knobs = []
    for o, p in (("--rev-decay", "RV_DECAY"), ("--rev-size", "RV_SIZE"), ("--rev-mod", "RV_MOD"),
                 ("--rev-locut", "RV_LOCUT")):
        v = opt(mine, o)
        if v is None and o == "--rev-locut":
            v = 64
        pl = pline(p)
        if v is not None and pl:
            knobs.append((pl[2], max(0, min(127, v)), p))
    for n in names:
        r = dict(rw)
        r[c95] = ccs[n]
        r.update({c: v for c, v, _ in knobs if v > 0})
        runs.append((n, r, {CC_MIX: [(c95, ccs[n])] + [(c, v) for c, v, _ in knobs]}))
    notes.append(f"after every CC {CC_MIX} > 0 (the reverb on): the TYPE (CC {c95})" +
                 "".join(f", {p} CC {c} = {v}" for c, v, p in knobs) +
                 "; the same CCs > 0 rewritten to these values wherever hil_cpu sends them")
    return runs, notes


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mine, rest = split_args(argv)
    runs, notes = plan(mine)
    spread = opt(mine, "--spread")
    for s in notes:
        print("hil_cpu_rev: " + s, flush=True)
    worst = "--worst" in rest
    mins = len(runs) * (RUN_MIN["worst"] if worst else RUN_MIN["full"])
    print(f"hil_cpu_rev: {len(runs)} run(s): {', '.join(n for n, _, _ in runs)}; hil_cpu {' '.join(rest)}"
          + (f"; SPREAD {spread} before the notes" if spread is not None else "") + f"; ~{mins:.0f} min", flush=True)
    import hil_cpu
    if "--dry-run" in mine:
        print("hil_cpu_rev: dry run (no MIDI, no audio); hil_cpu's --help:", flush=True)
        hil_cpu.main(["hil_cpu.py", "--help"])
        return 0
    orig = hil_cpu.ports
    box, rw_now, after_now = {}, {}, {}                 # (the Out keeps these dicts: each run refills them)

    def ports():
        mido, out, inp = orig()
        if "out" not in box:
            box["out"] = Out(out, {}, {}, spread)
            box["out"].rw, box["out"].after = rw_now, after_now
        return mido, box["out"], inp
    hil_cpu.ports = ports
    res, code = [], 0
    try:
        for name, rw, after in runs:
            print(f"\nhil_cpu_rev: ---- reverb TYPE {name} ----", flush=True)
            hil_cpu.USB_BAD_RUNS[:] = []
            rw_now.clear(), rw_now.update(rw), after_now.clear(), after_now.update(after)
            if "out" in box:
                box["out"].n = {}
            r = hil_cpu.main(["hil_cpu.py"] + rest)
            c = hil_cpu.exit_code(r)
            code |= c
            o = box.get("out")
            print(f"hil_cpu_rev: {name}: rewrites sent: " +
                  (", ".join(f"CC {k} x{v}" for k, v in sorted(o.n.items())) if o else "none (no MIDI opened)"),
                  flush=True)
            res.append((name, (r or {}).get("worst"), c))
    finally:
        hil_cpu.ports = orig
    if len(res) > 1:
        hall = next((w for n, w, _ in res if n == "HALL"), None)
        print("\nhil_cpu_rev: the TYPEs (app_audio's worst block; the gate: HALL + 200 us; the whole ISR in each "
              "run's own lines above, the release rule <= 4200 us):")
        for n, w, c in res:
            d = "" if hall is None or w is None else f"  {w - hall:+d} us over HALL" + (
                "  OVER THE GATE" if w - hall > 200 else "")
            print(f"  {n:9s} {w if w is not None else '-':>5} us  {'ok' if not c else 'FAIL'}{d}")
            if hall is not None and w is not None and w - hall > 200:
                code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
