#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SUMMARY.md from run_final_rev.sh's (and run_final.sh's) steps.tsv and logs: PASS / FAIL per step with its key numbers, the CPU
ranges, the hands-on rows; and device-done.json for tools/release.py --no-device --device-done (only when every
T1 step passed).

    summarize.py OUT_DIR"""
import json
import os
import re
import statistics
import sys

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)))
LIMIT = int(os.environ.get("CPU_LIMIT_US") or 4200)   # the margin rule (the user's): the whole ISR, every worst case


def read(p):
    try:
        return open(p, errors="replace").read()
    except OSError:
        return ""


def ints(pat, text):
    return [int(x) for x in re.findall(pat, text, re.M)]


def cpu(text):
    """(worst, whole, shed, verdict, rewrites) of one hil_cpu run's log"""
    w = ints(r"^worst (\d+) us", text)
    wh = ints(r"whole ISR worst: (\d+) us", text)
    shed = sum(ints(r"voices shed (\d+)", text))
    v = re.findall(r"^hil_cpu: (ok|FAIL)", text, re.M)
    rw = re.findall(r"^hil_cpu_fx: (rewrites sent: .*|heaviest: .*|delay TAPE: .*|no drive TYPEs.*|reverb TYPE: .*|"
                    r"SPREAD: .*|dry run: \d+ take.*)$", text, re.M)
    return (w[-1] if w else None), (wh[-1] if wh else None), shed, (v[-1] if v else None), rw


def cpu_takes(text):
    """(takes [dict(worst, whole, verdict, code)], the verdict line's (PASS|FAIL, limit, hurt) or None) of a
    hil_cpu_fx step's log: its 'hil_cpu_fx: take K/N' lines and its 'hil_cpu_fx: verdict:' line"""
    takes = []
    for m in re.finditer(r"^hil_cpu_fx: take (\d+)/(\d+): worst (\d+|\?) us, whole ISR (\d+|\?) us, hil_cpu (ok|FAIL|\?), "
                         r"exit (-?\d+)", text, re.M):
        w, wh = m.group(3), m.group(4)
        takes.append(dict(worst=None if w == "?" else int(w), whole=None if wh == "?" else int(wh),
                          verdict=m.group(5), code=int(m.group(6))))
    v = re.findall(r"^hil_cpu_fx: verdict: (PASS|FAIL) \(limit (\d+) us, \d+ takes(, hurt[^)]*)?\)", text, re.M)
    return takes, ((v[-1][0], int(v[-1][1]), bool(v[-1][2])) if v else None)


def rng(xs):
    xs = [x for x in xs if x is not None]
    return "-" if not xs else f"{min(xs)}" if min(xs) == max(xs) else f"{min(xs)}-{max(xs)}"


def must(text, rep_md):
    v = re.findall(r"^T1 (PASS|FAIL|INCOMPLETE) \((\d+) s\)", text, re.M)
    fails = [ln for ln in rep_md.splitlines() if "**FAIL**" in ln]
    skips = [ln for ln in rep_md.splitlines() if ln.rstrip().endswith("| SKIP |")]
    manual = [ln for ln in rep_md.splitlines() if ln.rstrip().endswith("| MANUAL |")]
    return (v[-1] if v else (None, None)), fails, skips, manual


def main():
    rows, notes = [], []
    steps = []
    for ln in read(os.path.join(OUT, "steps.tsv")).splitlines():
        p = ln.split("\t")
        if len(p) >= 4:
            steps.append(dict(name=p[0], code=p[1], secs=p[2], kind=p[3], note=p[4] if len(p) > 4 else ""))
    meta = dict(ln.split("=", 1) for ln in read(os.path.join(OUT, "run.txt")).splitlines() if "=" in ln)
    cpu_r = {}
    t1_ok = True
    t1_ran = set()
    for s in steps:
        log = read(os.path.join(OUT, s["name"] + ".log"))
        code, kind, name = s["code"], s["kind"], s["name"]
        key, res = "", None
        tail = [x for x in log.strip().splitlines() if x.strip() and not x.startswith("# ")]
        if code == "notrun" and s["note"].startswith("covered by step "):
            res, key = "COVERED?", s["note"]          # (resolved below: COVERED when every step it names PASSed here)
            if name == "t1_s2":
                t1_ran.add("S2")
        elif code == "notrun" and s["note"].startswith("covered by"):
            ok = re.match(r"covered by \S+: exit 0 ", s["note"]) is not None   # (SKIP_COVERED=1: an earlier run has it)
            res, key = ("COVERED" if ok else "FAIL"), s["note"] + ("" if ok else "; the covering run did not PASS it")
            t1_ran.add({"s5_install": "S5", "keyadc_gate": "S6"}.get(name, "-"))
            notes.append(f"{name}: not run here, {s['note']}")
        elif code == "notrun":
            res, key = ("SKIPPED" if s["note"].startswith(("not in ONLY", "hil_cpu has no")) else "NOT RUN"), s["note"]
        elif meta.get("dry_run") == "1" and kind != "manual":
            pick = (cpu(log)[4] if kind == "cpu" else
                    [x for x in tail if re.search(r"~[\d.]+ min|total [\d.]+ min", x)] or tail[-1:])
            res, key = ("DRY OK" if code == "0" else "DRY FAIL"), ("; ".join(x.strip() for x in pick)[:300] or
                                                                  "(no output)")
        elif kind == "info":
            res, key = ("PASS" if code == "0" else "FAIL"), (tail[-1][:200] if tail else "(no output)")
        elif kind == "manual":
            res, key = "MANUAL", s["note"]
        elif kind == "cpu":
            w, wh, shed, v, rw = cpu(log)
            takes, fxv = cpu_takes(log)
            lim = fxv[1] if fxv else LIMIT
            grp = cpu_r.setdefault(name.rstrip("0123456789_").replace("_bend", "") if "bend" not in name else name, [])
            if takes:                                 # (hil_cpu_fx --takes: every take, the max judged)
                grp += [(t["worst"], t["whole"]) for t in takes]
                whs = [t["whole"] for t in takes]
                over = [x for x in whs if x is None or x > lim]
                hurt = bool(fxv and fxv[2])
                res = ("PASS" if code == "0" and fxv and fxv[0] == "PASS" and not over and (len(takes) >= 3 or not hurt)
                       else "FAIL")
                key = (f"{len(takes)} take(s): worst {rng([t['worst'] for t in takes])} us, whole ISR {rng(whs)} us "
                       f"(max {max([x for x in whs if x is not None], default='?')}; limit {lim} us), shed {shed}"
                       + ("; over the margin before: 3 good takes in a row" if hurt else ""))
                if over:
                    key += f"; whole ISR over {lim} us in {len(over)} take(s): FAIL (the margin rule)"
            else:                                     # (an older log: one run; the margin rule judged here too)
                grp.append((w, wh))
                over = wh is None or wh > LIMIT
                res = "PASS" if code == "0" and v == "ok" and not (over and meta.get("dry_run") != "1") else "FAIL"
                key = (f"worst {w} us, whole ISR {wh} us, shed {shed}" +
                       (f"; whole ISR {'not reported' if wh is None else f'> {LIMIT} us'}: FAIL (the margin rule)"
                        if over else ""))
            if rw:
                key += "; " + "; ".join(rw)
        elif kind == "keyadc":
            g = re.findall(r"^key ADC gate: (PASS|FAIL)", log, re.M)
            eng = re.findall(r"^  (\w+)\s+OFF\s+(\d+) us\s+idle\s+(\d+) \(([+-]\d+): (\w+)\)\s+STRESS\s+(\d+) "
                             r"\(([+-]\d+): (\w+)\)", log, re.M)
            res = "PASS" if code == "0" and g and g[-1] == "PASS" else "FAIL"
            key = "; ".join(f"{e} OFF {o} / idle {di} ({ri}) / STRESS {ds} ({rs}) us" for e, o, _, di, ri, _, ds, rs
                            in eng)
            wsts = ints(r"^worst (\d+) us", log)
            whs = ints(r"whole ISR worst: (\d+) us", log)
            key += f"; its {len(wsts)} hil_cpu --worst runs: worst {rng(wsts)} us, whole ISR {rng(whs)} us"
            if "a run's own verdict FAILED" in log:
                key += "; a run's own verdict FAILED (4500 us / shed / USB)"
            if s["note"]:
                key += "; " + s["note"]
            t1_ran.add("S6")
        elif kind == "stress":
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and first.endswith("PASS") else "FAIL"
            per = re.findall(r"^(\w+)\s+(ok  |FAIL) (\d+) event\(s\) in (\d+) takes; (?:engine block (\d+) us, whole "
                             r"ISR (\d+) us)?", rep, re.M)
            key = "; ".join(f"{e} {st.strip()} {ev} events" + (f", engine {eb} / whole ISR {wi} us" if eb else "")
                            for e, st, ev, _, eb, wi in per) or (first or log.strip().splitlines()[-1:] or ["?"])[0]
            t1_ran.add("S1")
            t1_ok &= res == "PASS"
        elif kind == "must":
            (v, secs), fails, skips, manual = must(log, read(os.path.join(OUT, name + "-report.md")))
            res = {"PASS": "PASS", "FAIL": "FAIL", "INCOMPLETE": "INCOMPLETE"}.get(v, "FAIL")
            if code not in ("0", "1", "2"):
                res = "FAIL"
            key = f"T1 {v} in {secs} s; {len(fails)} FAIL row(s), {len(skips)} SKIP, {len(manual)} MANUAL"
            key += "".join(f"<br>FAIL: {re.sub(r'[*]', '', f)[:160]}" for f in fails[:6])
            key += "".join(f"<br>SKIP: {f[:160]}" for f in skips[:4])
            if name.startswith("s5"):
                m = re.findall(r"\| the saved panel after the update[^|]*\| ([^|]+) \|", read(os.path.join(
                    OUT, name + "-report.md")))
                if m:
                    key += f"; saved panel after the update: {m[0].strip()} (<= 0.5 LU, <= 5 %)"
                t1_ran.add("S5")
            else:
                t1_ran.update(("S2", "S3", "S4"))
            t1_ok &= res == "PASS"
            notes += [f"{name}: {re.sub(r'[*]', '', m)[:200]}" for m in manual]
        elif kind == "release":
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and ": PASS" in first else "FAIL"
            bad = [ln for ln in rep.splitlines()[2:] if "FAIL" in ln]
            key = (first or (tail[-1] if tail else "(no report)"))[:240]
            key += "".join(f"<br>{re.sub(' +', ' ', b)[:160]}" for b in bad[:6])
        elif kind == "clock":                         # hil_clock.py: a report per path (usb, trs)
            reps = [(v, read(os.path.join(OUT, f"{name}-{v}-report.txt"))) for v in ("usb", "trs")]
            reps = [(v, r) for v, r in reps if r]
            firsts = [r.splitlines()[0] for _, r in reps]
            res = "PASS" if code == "0" and firsts and all(": PASS" in f for f in firsts) else "FAIL"   # (PASS (MANUAL: ...))
            bad = [ln for _, r in reps for ln in r.splitlines() if ln.startswith("problem:")]
            key = ("; ".join(firsts) or (tail[-1] if tail else "(no report)"))[:240]
            key += "".join(f"<br>{b[:160]}" for b in bad[:6])
            tab = read(os.path.join(OUT, f"{name}-summary.txt"))
            if tab:
                key += "<br>" + "<br>".join(ln.rstrip() for ln in tab.splitlines()[2:])
        elif kind == "autosave":
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and first.startswith("hil_autosave: PASS") else "FAIL"
            tk = [ln for ln in log.splitlines() if re.match(r"^take \S+: (PASS|FAIL)", ln)]
            bad = [ln.strip() for ln in rep.splitlines() if ln.strip().startswith("FAIL")]
            key = (first or (tail[-1] if tail else "(no report)"))[:240]
            key += "".join(f"<br>{t[:160]}" for t in tk[:4]) + "".join(f"<br>{b[:160]}" for b in bad[:6])
        elif kind == "saveglitch":                    # hil_save_glitch.py: the worst case during an auto save
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and first.startswith("hil_save_glitch: PASS") else "FAIL"
            tk = [ln for ln in rep.splitlines() if re.match(r"^take \S+: (PASS|FAIL)", ln)]
            bad = [ln.strip() for ln in rep.splitlines() if ln.strip().startswith("FAIL")]
            st = [ln for ln in rep.splitlines() if ln.startswith(("self-test:", "restore:"))]
            key = (first or (tail[-1] if tail else "(no report)"))[:240]
            key += "".join(f"<br>{x[:160]}" for x in st[:3])
            key += "".join(f"<br>{t[:160]}" for t in tk if "FAIL" in t)[:1200] + "".join(f"<br>{b[:160]}" for b in bad[:6])
        elif kind == "tape":
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and first.endswith("PASS") else "FAIL"
            per = re.findall(r"^\s+(\S+-tape-\S+)\s+(\d+) event\(s\)(?:, CPU (\d+) us)?", log, re.M)
            key = "; ".join(f"{n} {e} events" + (f" (CPU {c} us)" if c else "") for n, e, c in per) or first
            key += "; WAVs in build/hil/tape_jack/ (listen: the echoes, wow / flutter, the feedback tail)"
        elif kind == "revjack":
            rep = read(os.path.join(OUT, name + "-report.txt"))
            first = rep.splitlines()[0] if rep else ""
            res = "PASS" if code == "0" and first.startswith("rev_jack: PASS") else "FAIL"
            per = re.findall(r"^\s+(\S+-(?:hall|plate|inf|infinite|switch)\S*)\s+(\d+) event\(s\)(?:, CPU (\d+) us)?", log, re.M)
            key = (first + "; " if first else "") + "; ".join(f"{n} {e} events" + (f" (CPU {c} us)" if c else "")
                                                             for n, e, c in per)
            hiss = [ln for ln in rep.splitlines() if ln.startswith("hiss")]
            if hiss:
                key += "; " + hiss[0]
            fz = re.findall(r"freeze_spread_db (\S+), freeze_growth_db (\S+), freeze_dc_lsb (\S+)", rep)
            if fz:
                key += f"; freeze 60 s: spread {fz[0][0]} dB, growth {fz[0][1]} dB, DC {fz[0][2]} LSB"
            m = re.findall(r"WAVs to (\S+)", log)
            key += f"; WAVs in {m[-1] if m else 'private/reverb/takes/'} (the listening round)"
        elif kind == "onset":
            js = read(os.path.join(OUT, name + ".json"))
            res = "PASS" if code == "0" else "FAIL"
            try:
                takes = json.loads(js)
                by = {}
                for t in takes:
                    for n in t["notes"]:
                        by.setdefault(t["rate"], []).append(n)
                parts = []
                for r, ns in sorted(by.items()):
                    u = [n["usb_ms"] for n in ns if n.get("usb_ms") is not None]
                    j = [n["jack_ms"] for n in ns if n.get("jack_ms") is not None]
                    d = [n["usb_ms"] - n["jack_ms"] for n in ns if n.get("usb_ms") is not None and
                         n.get("jack_ms") is not None]
                    parts.append(f"{r} Hz: USB median {statistics.median(u):.1f} ms ({min(u):.1f}-{max(u):.1f})"
                                 + (f", jack {statistics.median(j):.1f} ms, USB - jack {statistics.median(d):.1f} ms"
                                    if j and d else "") + f", {len(ns)} notes")
                key = "; ".join(parts) + " (MANUAL's 'About 18 ms from a MIDI note to the USB audio': the USB median)"
            except Exception as e:  # noqa: BLE001
                key = f"no usb_onset.json ({e!r})"
        elif kind == "selftest":                      # (the stress steps' host self-test, run in the background)
            res = "PASS" if code == "0" and any(x.strip() == "self-test: PASS" for x in tail) else "FAIL"
            key = "; ".join(x.strip() for x in tail if x.startswith("self-test"))[-600:] or "(no self-test output)"
        else:
            res = "PASS" if code == "0" else "FAIL"
        if s["note"] and code != "notrun":
            key = (key + "; " if key else "") + s["note"]          # (DONE in an earlier attempt / a timeout)
            if s["note"].startswith("timeout"):
                res = "FAIL"
        if kind == "stress":
            fold = [ln.strip() for ln in log.splitlines() if ln.startswith("fold")]
            if fold:
                key += "<br>" + "<br>".join(fold)
                if name == "stress_48000":
                    notes += fold
        rows.append((name, res, key, s["secs"]))
    names = [r[0] for r in rows]
    # the pipelined host self-test (run_final_rev.sh: stress_<rate>_selftest in the background while the FM-1
    # records): stress_<rate> PASSes only with its self-test PASSed (the detectors proven), as when it ran inside it
    for i, (n, r, k, sec) in enumerate(rows):
        if n.endswith("_selftest") and n[:-len("_selftest")] in names:
            j = names.index(n[:-len("_selftest")])
            sn, sr, sk, ss = rows[j]
            if r not in ("PASS", "DRY OK") and sr in ("PASS", "DRY OK"):
                rows[j] = (sn, "DRY FAIL" if sr == "DRY OK" else "FAIL",
                           sk + f"; its host self-test {n} did not PASS: the detectors are not proven", ss)
    for i, (n, r, k, sec) in enumerate(rows):
        if r == "COVERED?":
            by = k[len("covered by step "):].split(":")[0].split(",")
            ok = all(any(x[0] == b and x[1] in ("PASS", "DRY OK") for x in rows) for b in by)
            rows[i] = (n, "COVERED" if ok else "FAIL", k + ("" if ok else f"; {', '.join(by)} did not PASS"), sec)
            if not ok:
                t1_ok = False
    if "keyadc_gate" in names and "keyadc_rhodes5" in names:      # TEST-PLAN 3b: 5 Rhodes runs before a FAIL counts
        g, r5 = rows[names.index("keyadc_gate")], rows[names.index("keyadc_rhodes5")]
        glog = read(os.path.join(OUT, "keyadc_gate.log"))
        bad = set(re.findall(r"^  (\w+)\s+OFF.*FAIL", glog, re.M))
        own = "a run's own verdict FAILED" in glog
        if g[1] == "FAIL" and bad == {"rhodes"} and not own and r5[1] == "PASS":
            rows[names.index("keyadc_gate")] = (g[0], "PASS", g[2] + "; the Rhodes FAILed on 3 runs, PASSed on 5 "
                                                "(keyadc_rhodes5; TEST-PLAN 3b)", g[3])
        rows[names.index("keyadc_rhodes5")] = (r5[0], "INFO" if r5[1] == "PASS" else r5[1], r5[2], r5[3])
    for k, v in cpu_r.items():
        if len(v) > 1:
            notes.insert(0, f"{k}: {len(v)} runs, worst {rng([a for a, _ in v])} us, whole ISR {rng([b for _, b in v])}"
                            f" us (the margin rule: whole ISR <= {LIMIT} us, the max of the takes; design target 4500 us)")
    for s in ("S1", "S2", "S3", "S4", "S5", "S6"):
        if s not in t1_ran:
            t1_ok = False
    t1_ok &= all(r[1] in ("PASS", "INFO", "SKIPPED", "COVERED") for r in rows if r[0].startswith(("cpu_", "keyadc")))
    t1_ok &= all(any(r[0] == f"stress_{hz}" and r[1] == "PASS" for r in rows) for hz in (48000, 44100))
    verdict = ("DRY RUN" if meta.get("dry_run") == "1" else
               "FAIL" if any(r[1] in ("FAIL", "NOT RUN") for r in rows) else
               "INCOMPLETE" if any(r[1] == "INCOMPLETE" for r in rows) else
               "PARTIAL (ONLY: some steps not run)" if any(r[1] == "SKIPPED" and r[2].startswith("not in ONLY")
                                                          for r in rows) else "PASS")
    early = meta.get("mode") == "early"
    md = [f"# {'Early reverb FM-1 run (ONLY=rev_early)' if early else 'Final FM-1 run (with the reverb TYPEs)'}: {verdict}", "",
          f"- package `{meta.get('fwsc', '?')}` sha256 `{meta.get('sha256', '?')}` (over `{meta.get('prev', '?')}`); "
          f"expected: {meta.get('sha256_expected', '?')}; at the end: "
          + ("the same" if meta.get("sha256_end") == meta.get("sha256") else
             f"**CHANGED to `{meta.get('sha256_end', '?')}`**"),
          f"- tree HEAD `{meta.get('tree_sha', '?')}`; PYTEST_GREEN: {meta.get('pytest_green', 'no')}",
          f"- tools from `{meta.get('tree', '?')}` at `{meta.get('tree_head', '?')}`; started {meta.get('start', '?')}, "
          f"ended {meta.get('end', '?')}",
          f"- {read(os.path.join(OUT, 'reverb-types.txt')).strip() or 'reverb TYPEs: not listed'}; the jack takes in "
          f"`{meta.get('takes', '?')}`",
          f"- the gates: each tool's own (hil_cpu: worst and whole ISR <= 4500 us, 0 shed, USB counters clean; the CPU "
          f"steps: also the whole ISR <= {LIMIT} us in every take (the margin rule; hil_cpu_fx --takes, the max judged; "
          f"CPU_HURT's steps 3 good takes in a row); "
          "stress_max / T1: the CC 116 counters first, then 0 audio events). PASS here = the tool's exit 0 and its "
          "verdict line.", "",
          "| step | result | key numbers | s |", "|---|---|---|---|"]
    md += [f"| {n} | {'**' + r + '**' if r not in ('PASS', 'INFO', 'SKIPPED', 'DRY OK', 'COVERED') else r} | "
           f"{re.sub(r' *[|] *', ' / ', k.strip()).strip(' /')} | {s} |" for n, r, k, s in rows]
    md += ["", "## Ranges and notes", ""] + [f"- {n}" for n in notes]
    md += ["", "## By hand (the user / the coordinator; never reported as passed here)", "",
           "- Before the run: the sustain preflight (TEST-PLAN §3; not in the tools yet): REC sustain latch off, no "
           "pedal down; the MOTU M6 on input 5 for the jack; MASTER and the MOTU gain untouched.",
           "- S5's MANUAL rows: user presets (U01) and the pattern after the update (no MIDI read-back), the update "
           "loader's screen, the OCT- rescue (once per release).",
           "- The pattern-save power-cycle test (the two flash sectors A 0x99000 / B 0x9A000), see pattern_power_cycle."
           " T3: the user's 10 minutes with the release binary."]
    open(os.path.join(OUT, "SUMMARY.md"), "w").write("\n".join(md) + "\n")
    if verdict == "PASS" and t1_ok and meta.get("sha256") and not early:
        json.dump(dict(sha256=meta["sha256"], tiers=["T1"],
                       runs=[f"{n}: {r}: {k}" for n, r, k, _ in rows]), open(os.path.join(OUT, "device-done.json"),
                                                                              "w"), indent=1)
    print("\n".join(md))
    if verdict == "DRY RUN":
        return 1 if any(r[1] == "DRY FAIL" for r in rows) else 0
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
