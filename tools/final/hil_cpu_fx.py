#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_cpu.py with the heaviest drive TYPE, the TAPE delay and the heaviest reverb TYPE in its effect scenarios
(build/final/run_final.sh, run_final_rev.sh).

hil_cpu has no option for the drive TYPE (CC 84), the delay TYPE (CC 88) or the reverb TYPE: its HEAVY sets DL_SW=80
(ANA: CC 88 maps v * 4 >> 7, so TAPE needs >= 96), REV=127 (CC 91) and leaves CC 84 / the reverb TYPE at
param_defaults() (FDRV / HALL). This wrapper runs hil_cpu unchanged but filters what it sends to the FM-1: every
CC 84 with a value > 0 becomes the middle of the heaviest drive TYPE's band (params.c DRV_CC84), every CC 88 > 0
TAPE's band, every reverb TYPE CC > 0 the heaviest reverb TYPE's band, and after every CC 91 > 0 (the reverb's MIX
on) the reverb TYPE CC is sent too. CC 84 / 88 / 91 = 0 (the dry scenarios) pass unchanged.

The heaviest drive TYPE is read from the tree's sources: each drv_<type>.c's DRV_US_<TYPE> (us per block, compiled
estimate) under its own anti-aliasing switch (#if <T>_AA, from firmware/gen/drive_tables.h or the .c). The heaviest
reverb TYPE the same way: params.c [P_RV_TYPE] (its CC, its names, its CC band table when frodhes.c maps it by band,
else v * (hi + 1) >> 7), the cost from rev_types.c's REV_US[] table (the framework's per-TYPE cost, its macros from
fx.c, rev_types.c, rev_plate.c, rev_inf.c evaluated under their #if switches: REV_INF_QUARTER ...), else the macro
REV_US_<NAME> (or its 3 / 4 letter prefix; HALL: fx.c FX_US_REVERB). With a reverb TYPE, LOW CUT (params.c
[P_RV_LOCUT]) is turned on too after every CC 91 > 0 (its one-pole is in the cost; --no-locut: off). No TYPEs in the
tree: that CC is left alone and that is printed.

    TREE=<release tree> hil_cpu_fx.py [--dry-run] [--drive-type NAME] [--rev-type NAME] [--no-rev] [--no-locut] [--no-tape]
                                      [--spread V] [--takes N] [--limit US] [--hurt] -- <hil_cpu options>
    hil_cpu_fx.py --list-rev        the reverb TYPEs, one per line: NAME INDEX US CC VALUE, the heaviest first
--dry-run prints the choice and the rewrites and hil_cpu's --help (no MIDI, no audio). --rev-type: HALL, PLATE,
INFINITE (or a prefix: INF); --no-rev: the reverb TYPE as hil_cpu sends it (param_defaults: HALL).

The margin rule (the user's): the whole ISR <= 4200 us on every worst case (every engine, every effect, 10 voices); 4500
us stays the design target (hil_cpu's own gate, printed). --takes N (default $CPU_TAKES, else 1) runs hil_cpu N times,
each take a process of its own (its MIDI, its USB stream, its counters), every take's output kept, and judges the MAX
of the takes: any take with its whole ISR over --limit US (default $CPU_LIMIT_US, else 4200), or not reported, or
with hil_cpu's own verdict FAIL (4500 us, a shed voice, a USB fault) is a FAIL, exit 1. --hurt: a worst case that has
been over the margin before (run_final_rev.sh CPU_HURT): it must give 3 good takes IN A ROW (at least 3 takes
whatever N, each whole ISR <= the limit; one take over = FAIL, no retry). The lines run_final_rev.sh / cpu_check.sh /
summarize_rev.py read:
    hil_cpu_fx: take K/N: worst W us, whole ISR H us, hil_cpu ok|FAIL, exit C
    hil_cpu_fx: takes N: worst A-B us, whole ISR C-D us (max D) vs the limit L us ...: PASS|FAIL
    hil_cpu_fx: verdict: PASS|FAIL (limit L us, N takes[, hurt: 3 in a row])"""
import os
import re
import sys
import time

TREE = os.path.abspath(os.environ.get("TREE") or os.getcwd())
sys.path.insert(0, os.path.join(TREE, "tools"))


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
    idx, hi = re.findall(r'"([^"]*)"', n.group(1)).index("TAPE"), int(h.group(1))
    vs = [v for v in range(128) if v * (hi + 1) >> 7 == idx]
    return vs[len(vs) // 2] if vs else None


def param_entry(name):
    """(hi, default, format, cc, the names array) of params.c's [P_<name>] = {"..", hi, de, F_.., cc, names}, or None"""
    m = re.search(r"\[P_" + name + r"\]\s*=\s*\{\s*\"[^\"]*\"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*(\d+)\s*(?:,\s*(\w+))?",
                  _src("firmware/src/params.c"))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4)), m.group(5)


def _names(ident):
    m = re.search(r"\b" + re.escape(ident) + r"\[\]\s*=\s*\{([^}]*)\}", _src("firmware/src/params.c")) if ident else None
    return re.findall(r'"([^"]*)"', m.group(1)) if m else []


def _band_table(pid):
    """the CC band-start table frodhes.c maps parameter pid by (`id == pid) x = fn(v)`: fn's table in params.c), or
    None (the plain v * (hi + 1) >> 7)"""
    p = _src("firmware/src/params.c")
    m = re.search(r"id\s*==\s*" + pid + r"\s*\)\s*x\s*=\s*(?:\(\w+\)\s*)?(\w+)\s*\(", _src("firmware/src/frodhes.c"))
    idents = []
    if m:
        f = re.search(r"\b" + m.group(1) + r"\s*\([^)]*\)\s*\{(.*?)\n\}", p, re.S)
        if f:
            idents = re.findall(r"\b([A-Z][A-Z0-9_]*)\s*\[", f.group(1))
    for ident in idents:
        t = re.search(r"\b" + ident + r"\[\d*\]\s*=\s*\{([^}]*)\}", p)
        if t:
            return [int(v, 0) for v in t.group(1).split(",") if v.strip()]
    return None


def _arith(expr, defs):
    """an integer C expression of known macros (+ - * / >> << parentheses), or None"""
    e = re.sub(r"/\*.*?\*/|/\*.*$|//.*", "", expr)
    e = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]*\b", r"\1", e)
    names = re.findall(r"\b[A-Za-z_]\w*\b", e)
    if any(n not in defs for n in names):
        return None
    e = re.sub(r"\b[A-Za-z_]\w*\b", lambda m: str(defs[m.group(0)]), e).replace("/", "//")
    if not re.fullmatch(r"[\d\s()+\-*/<>x a-fA-F]*", e):
        return None
    try:
        return int(eval(e, {"__builtins__": {}}))  # noqa: S307 (integers and operators only, from our own sources)
    except Exception:
        return None


def rev_costs():
    """{TYPE index: us} from rev_types.c's REV_US[] table (the framework's own per-TYPE cost, fx_cost_us), its
    macros evaluated (plain ones under their #if switches, then the expressions), plus the macro dict"""
    texts = [_src(f) for f in ("firmware/src/fx.c", "firmware/src/rev_types.c", "firmware/src/rev_plate.c",
                               "firmware/src/rev_inf.c")]
    defs = {}
    for t in texts:
        defines(t, defs)
    exprs = re.findall(r"^\s*#\s*define\s+(\w+)\s+(\(.*?\)|[^\s/]+)\s*(?:/[*/].*)?$", "\n".join(texts), re.M)
    for _ in range(4):
        for k, e in exprs:
            if k not in defs:
                v = _arith(e, defs)
                if v is not None:
                    defs[k] = v
    out = {}
    m = re.search(r"\bREV_US\s*\[\s*\w*\s*\]\s*=\s*\{([^}]*)\}", texts[1])
    if m:
        for i, e in enumerate(m.group(1).split(",")):
            v = _arith(e, defs)
            if v is not None:
                out[i] = v
    return out, defs


def rev_types():
    """[(name, index, us, cc value)] for the reverb TYPEs that run (index >= 1: HALL, PLATE, INFINITE ...), or []"""
    e = param_entry("RV_TYPE")
    if not e:
        return []
    hi, _, _, cc, ident = e
    names = _names(ident) or ["OFF", "HALL", "PLATE", "INFINITE"][:hi + 1]
    table = _band_table("P_RV_TYPE")
    if table and len(table) == len(names):
        mids = [(lo + h) // 2 for lo, h in bands(table)]
    else:
        mids = []
        for i in range(len(names)):
            vs = [v for v in range(128) if v * (hi + 1) >> 7 == i]
            mids.append(vs[len(vs) // 2] if vs else None)
    table_us, defs = rev_costs()
    out = []
    for i, name in enumerate(names):
        if i < 1 or mids[i] is None:
            continue                                  # OFF
        us = table_us.get(i)                          # (rev_types.c REV_US[i], else the macro by name)
        for k in [] if us is not None else [f"REV_US_{name}", f"REV_US_{name[:4]}", f"REV_US_{name[:3]}"] + (["FX_US_REVERB"] if name == "HALL"
                                                                                   else []):
            if k in defs and k != "REV_US_MAX":
                us = defs[k]
                break
        out.append((name, i, us, mids[i]))
    return out


def rev_cc():
    """the reverb TYPE's CC (params.c [P_RV_TYPE]), or None"""
    e = param_entry("RV_TYPE")
    return e[3] if e and e[3] else None


def rev_pick(force=None):
    """(the chosen reverb TYPE (name, index, us, v), all of them, the heaviest first) for --rev-type force or the
    heaviest by the compiled estimate (no estimate: the last TYPE, INFINITE)"""
    types = rev_types()
    if not types:
        return None, []
    order = sorted([t for t in types if t[2] is not None], key=lambda t: -t[2]) + [t for t in reversed(types)
                                                                                   if t[2] is None]
    if force:
        f = force.upper()
        pick = ([t for t in types if t[0] == f] or [t for t in types if t[0].startswith(f) or f.startswith(t[0])]
                or [t for t in types if t[0][:3] == f[:3]])
        if not pick:
            sys.exit(f"hil_cpu_fx: --rev-type {force}: not one of {', '.join(t[0] for t in types)}")
        return pick[0], order
    return order[0], order


LIMIT_US = 4200                                       # the user's margin rule: the whole ISR, every worst case
TARGET_US = 4500                                      # the design target (hil_cpu's own gate)
HURT_ROW = 3                                          # a worst case over the margin before: this many good in a row


def parse_take(text):
    """(worst, whole, verdict) of one hil_cpu run's output: its last 'worst N us', 'whole ISR worst: N us' and
    'hil_cpu: ok|FAIL' (None where missing)"""
    w = re.findall(r"^worst (\d+) us", text, re.M)
    wh = re.findall(r"whole ISR worst: (\d+) us", text)
    v = re.findall(r"^hil_cpu: (ok|FAIL)", text, re.M)
    return (int(w[-1]) if w else None), (int(wh[-1]) if wh else None), (v[-1] if v else None)


def take_line(k, n, worst, whole, verdict, code):
    return (f"hil_cpu_fx: take {k}/{n}: worst {worst if worst is not None else '?'} us, whole ISR "
            f"{whole if whole is not None else '?'} us, hil_cpu {verdict or '?'}, exit {code}")


def parse_takes(text):
    """[dict(k, n, worst, whole, verdict, code)] from the 'hil_cpu_fx: take K/N: ...' lines"""
    out = []
    for m in re.finditer(r"^hil_cpu_fx: take (\d+)/(\d+): worst (\d+|\?) us, whole ISR (\d+|\?) us, hil_cpu (ok|FAIL|\?), "
                         r"exit (-?\d+)", text, re.M):
        k, n, w, wh, v, c = m.groups()
        out.append(dict(k=int(k), n=int(n), worst=None if w == "?" else int(w), whole=None if wh == "?" else int(wh),
                        verdict=None if v == "?" else v, code=int(c)))
    return out


def _rng(xs):
    xs = [x for x in xs if x is not None]
    return "?" if not xs else f"{min(xs)}" if min(xs) == max(xs) else f"{min(xs)}-{max(xs)}"


def judge(takes, limit=LIMIT_US, hurt=False):
    """(ok, [lines]) for the takes [dict(worst, whole, verdict, code)]: every take's whole ISR reported and <= limit,
    hil_cpu's own verdict ok and exit 0; hurt: also at least HURT_ROW takes, the good ones in a row (any bad take =
    FAIL: the row is broken, never retried)"""
    bad = []
    for i, t in enumerate(takes, 1):
        why = []
        if t.get("whole") is None:
            why.append("whole ISR not reported")
        elif t["whole"] > limit:
            why.append(f"whole ISR {t['whole']} us > {limit} us")
        if t.get("code", 1) != 0 or t.get("verdict") != "ok":
            why.append(f"hil_cpu {t.get('verdict') or '?'} / exit {t.get('code')} (4500 us, a shed voice or a USB fault)")
        if why:
            bad.append(f"take {i}: " + ", ".join(why))
    row = 0
    best = 0
    for i, t in enumerate(takes):
        good = not any(b.startswith(f"take {i + 1}:") for b in bad)
        row = row + 1 if good else 0
        best = max(best, row)
    need = HURT_ROW if hurt else 1
    ok = bool(takes) and not bad and len(takes) >= need
    wh = [t.get("whole") for t in takes]
    mx = max([x for x in wh if x is not None], default=None)
    s = (f"hil_cpu_fx: takes {len(takes)}: worst {_rng([t.get('worst') for t in takes])} us, whole ISR {_rng(wh)} us "
         f"(max {mx if mx is not None else '?'}) vs the limit {limit} us (the margin rule; design target {TARGET_US} us)")
    if hurt:
        s += f"; over the margin before: {HURT_ROW} good takes in a row needed, {best} in a row"
    lines = [s + ": " + ("PASS" if ok else "FAIL")]
    lines += [f"hil_cpu_fx: FAIL {b}" for b in bad]
    if not takes:
        lines.append("hil_cpu_fx: FAIL no take ran")
    elif len(takes) < need:
        lines.append(f"hil_cpu_fx: FAIL only {len(takes)} take(s): {HURT_ROW} in a row needed")
    lines.append(f"hil_cpu_fx: verdict: {'PASS' if ok else 'FAIL'} (limit {limit} us, {len(takes)} takes"
                 + (f", hurt: {HURT_ROW} in a row" if hurt else "") + ")")
    return ok, lines


class Tee:
    """stdout copied into a buffer (one take's output, parsed after it)"""

    def __init__(self, f):
        self.f, self.buf = f, []

    def write(self, s):
        self.buf.append(s)
        return self.f.write(s)

    def flush(self):
        self.f.flush()

    def __getattr__(self, k):
        return getattr(self.f, k)


def run_takes(own, rest, n, limit, hurt):
    """n takes, each this script again with --takes 1 (its own process: hil_cpu's counters start fresh), its output
    streamed through and its take line collected; then the judgement; the exit code"""
    import subprocess
    val = ("--takes", "--take-of", "--limit")
    base = [a for i, a in enumerate(own) if a not in val + ("--hurt",) and not (i and own[i - 1] in val)]
    takes = []
    for k in range(1, n + 1):
        print(f"hil_cpu_fx: ---- take {k}/{n} ----", flush=True)
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__)] + base +
                             ["--takes", "1", "--take-of", f"{k}/{n}", "--limit", str(limit), "--"] + rest,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        text = []
        for ln in p.stdout:
            sys.stdout.write(ln)
            sys.stdout.flush()
            text.append(ln)
        code = p.wait()
        got = parse_takes("".join(text))
        if got:
            t = got[-1]
            t["code"] = code if code else t["code"]
        else:
            w, wh, v = parse_take("".join(text))
            t = dict(worst=w, whole=wh, verdict=v, code=code)
            print(take_line(k, n, w, wh, v, code), flush=True)
        takes.append(t)
    ok, lines = judge(takes, limit, hurt)
    for s in lines:
        print(s, flush=True)
    return 0 if ok else 1


class Out:
    """the MIDI output with CC values rewritten, and CCs sent after others (after: {cc: [(cc2, v2)]} after cc > 0);
    counts kept"""

    def __init__(self, port, rw, spread=None, after=None):
        self.port, self.rw, self.n, self.spread, self.t = port, rw, {c: 0 for c in rw}, spread, 0.0
        self.after = after or {}

    def send(self, msg):
        if msg.type == "control_change" and msg.control in self.rw and msg.value > 0:
            msg = msg.copy(value=self.rw[msg.control])
            self.n[msg.control] += 1
        if self.spread is not None and msg.type == "note_on" and time.monotonic() - self.t > 0.5:
            self.port.send(msg.__class__("control_change", channel=msg.channel, control=105, value=self.spread))
            self.t = time.monotonic()                  # SPREAD (CC 105) before the notes: it is set at each strike
            self.n[105] = self.n.get(105, 0) + 1
        self.port.send(msg)
        if msg.type == "control_change" and msg.value > 0 and msg.control in self.after:
            for c, v in self.after[msg.control]:      # (the reverb TYPE after its MIX: whatever hil_cpu's reset sent)
                self.port.send(msg.__class__("control_change", channel=msg.channel, control=c, value=v))
                self.n[f"{c} after {msg.control}"] = self.n.get(f"{c} after {msg.control}", 0) + 1

    def __getattr__(self, k):
        return getattr(self.port, k)


def main():
    argv = sys.argv[1:]
    own, rest = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, None)

    def opt(name):
        return own[own.index(name) + 1] if name in own and own.index(name) + 1 < len(own) else None
    if "--list-rev" in own:
        pick, order = rev_pick(opt("--rev-type"))
        for name, i, us, v in ([pick] if pick else []) + [t for t in order if t != pick]:
            print(f"{name} {i} {us if us is not None else '?'} {rev_cc()} {v}")
        return 0
    dry = "--dry-run" in own
    hurt = "--hurt" in own
    try:
        n = int(opt("--takes") or os.environ.get("CPU_TAKES") or 1)
        limit = int(opt("--limit") or os.environ.get("CPU_LIMIT_US") or LIMIT_US)
    except ValueError:
        sys.exit("hil_cpu_fx: --takes / --limit (CPU_TAKES / CPU_LIMIT_US): an integer")
    if n < 1:
        sys.exit("hil_cpu_fx: --takes N: N >= 1")
    if hurt and n < HURT_ROW:
        n = HURT_ROW                                  # (over the margin before: 3 good in a row, whatever N)
    take_of = opt("--take-of")
    tape = "--no-tape" not in own
    force = (opt("--drive-type") or "").upper() or None
    spread = int(opt("--spread")) if opt("--spread") is not None else None
    if rest is None:                                  # (no "--": everything not ours goes to hil_cpu)
        skip = {"--dry-run", "--no-tape", "--no-rev", "--no-locut", "--hurt"}
        val = {"--spread", "--drive-type", "--rev-type", "--takes", "--limit", "--take-of"}
        rest = [a for i, a in enumerate(own) if a not in skip | val and not (i and own[i - 1] in val)]
        own = [a for i, a in enumerate(own) if a in skip | val or (i and own[i - 1] in val)]
    if n > 1 and not dry:
        return run_takes(own, rest, n, limit, hurt)
    rw, after, notes = {}, {}, []
    types = drive_types()
    if types:
        for name, i, us, v in types:
            notes.append(f"drive TYPE {name} (index {i}): DRV_US {us if us is not None else '?'} us, CC 84 = {v}")
        pick = ([t for t in types if t[0] == force] if force else
                sorted([t for t in types if t[2] is not None], key=lambda t: -t[2])[:1]) or types[-1:]
        name, i, us, v = pick[0]
        rw[84] = v
        notes.append(f"heaviest: {name} ({us} us, compiled estimate) -> every CC 84 > 0 sent as {v}")
    else:
        notes.append("no drive TYPEs in this tree (params.c has no DRV_CC84): CC 84 left as hil_cpu sends it")
    tv = tape_cc() if tape else None
    if tv is not None:
        rw[88] = tv
        notes.append(f"delay TAPE: every CC 88 > 0 sent as {tv}")
    elif tape:
        notes.append("no TAPE delay in this tree's params.c: CC 88 left as hil_cpu sends it")
    if "--no-rev" in own:
        notes.append("reverb TYPE: as hil_cpu sends it (--no-rev)")
    else:
        rpick, rorder = rev_pick(opt("--rev-type"))
        rc = rev_cc()
        if rpick and rc:
            for name, i, us, v in rorder:
                notes.append(f"reverb TYPE {name} (index {i}): REV_US {us if us is not None else '?'} us, CC {rc} = {v}")
            name, i, us, v = rpick
            rw[rc] = v
            after[91] = [(rc, v)]
            lc = param_entry("RV_LOCUT")
            if lc and lc[3] and "--no-locut" not in own:
                after[91].append((lc[3], 64))         # LOW CUT on: its one-pole is in the cost (REV_US_LOCUT)
                notes.append(f"reverb LOW CUT: CC {lc[3]} = 64 after every CC 91 > 0 (--no-locut: off)")
            how = ("--rev-type" if opt("--rev-type") else "the heaviest, compiled estimate" if us is not None else
                   "no cost estimate found: the last TYPE")
            notes.append(f"reverb TYPE: {name} ({us if us is not None else '?'} us, {how}) -> every CC {rc} > 0 sent "
                         f"as {v}, and CC {rc} = {v} after every CC 91 > 0")
        else:
            notes.append("reverb TYPE: none in this tree (params.c has no [P_RV_TYPE]): HALL as hil_cpu sends it")
    if spread is not None:
        notes.append(f"SPREAD: CC 105 = {spread} before the notes")
    for s in notes:
        print("hil_cpu_fx: " + s, flush=True)
    import hil_cpu
    if dry:
        print(f"hil_cpu_fx: dry run: {n} take(s), each judged: whole ISR <= {limit} us (the margin rule, the max of the "
              f"takes), hil_cpu's own verdict (design target {TARGET_US} us)" +
              (f"; over the margin before: {HURT_ROW} good takes in a row" if hurt else ""), flush=True)
        print("hil_cpu_fx: dry run, hil_cpu " + " ".join(rest) + " (not run); its --help:", flush=True)
        hil_cpu.main(["hil_cpu.py", "--help"])
        return 0
    orig = hil_cpu.ports
    box = {}

    def ports():
        mido, out, inp = orig()
        if "out" not in box:
            box["out"] = Out(out, rw, spread, after)
        return mido, box["out"], inp
    hil_cpu.ports = ports
    code = 1
    tee = Tee(sys.stdout)
    sys.stdout = tee
    try:
        code = hil_cpu.exit_code(hil_cpu.main(["hil_cpu.py"] + rest))
    finally:
        sys.stdout = tee.f
        o = box.get("out")
        sent = [f"CC {c} -> {rw[c]} x{k}" if c in rw else f"CC {c} -> {spread} x{k}" if c == 105 else f"CC {c} x{k}"
                for c, k in o.n.items()] if o else []
        print("hil_cpu_fx: rewrites sent: " + (", ".join(sent) if o else "none (no MIDI opened)"), flush=True)
    if "--help" in rest or "-h" in rest:
        return code
    w, wh, v = parse_take("".join(tee.buf))
    print(take_line(*(take_of.split("/") if take_of else (1, 1)), w, wh, v, code), flush=True)
    if take_of:
        return code                                   # (one take of run_takes: it judges them all)
    ok, lines = judge([dict(worst=w, whole=wh, verdict=v, code=code)], limit, hurt)
    for s in lines:
        print(s, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
