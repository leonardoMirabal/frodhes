#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The complete device scan of a connected FM-1, both engines, in one command, and one summary.

The rule: always record the headphone jack and USB, but the JACK decides (the user plays through the jack; USB is
for development). Runs, in order, each as a subprocess with its own log under build/hil/full/<timestamp>/:

  info          tools/fm1_install.py --info            (which firmware the FM-1 reports; installs nothing)
  cpu-*         tools/hil_cpu.py, then --engine vibes  (2 runs each; the worst counts)
  classic       tools/hil_scan.py                      (every default suite; USB only -> report.json / .txt)
  vibes         tools/hil_scan.py --vibes --source both --wav   (USB and the jack side by side -> vibes-report.*)
  jack-*        tools/hil_jack.py, then --engine vibes (hiss / SNR / headroom through the interface -> jack*.json)
  seq           tools/hil_seq.py                       (only with --seq)

Each tool's JSON / report is copied into the run directory once it has written it, and SUMMARY.md is built from
those: per suite ok / FAIL counts, every failing row classified
  JACK FAIL   real: the jack fails it (the VIBES rows carry each source's verdict, hil_scan check() src_ok)
  UNDECIDED   failing with no jack value to decide on (the classic suites are USB only; a USB-only measure)
  USB-ONLY    the jack passes, or a USB capture artefact (the USB stream slipped, USB-only clicks): tooling
  INFO        an information row, a USB / jack MISMATCH where both pass, a known stale classic limit (AGENTS.md)
the CPU worst per engine with its margins to 4500 / 4800 / 5000 us, hiss / SNR per engine.

It never installs firmware. It takes the device lock (tools/lock.py DEVICE: <root>/build/hil/DEVICE.lockdir; a
caller holding it, FRODHES_LOCK_DEVICE, passes at once; waits while another session holds it; released in finally).

  tools/full_scan.py [--seq] [--skip cpu,jack] [--jack-dev M6] [--jack-ch 5] [--root MAIN_REPO] [--wait-lock 3600]
  tools/full_scan.py --from build/hil/full/<ts>         rebuild SUMMARY.md from that run's logs / JSON (no device)
"""
import argparse
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock  # noqa: E402

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
HIL = os.path.join(REPO, "build", "hil")

CPU_TARGET, CPU_GATE, CPU_SHED, CPU_BLOCK = 4500, 4800, 5000, 5805
# AGENTS.md's "known stale limits" (types, effects, bypass, fx_all, fx_toggle, presets) used to be information here.
# Since 2026-10-24 none is: types / effects / bypass / fx_toggle / presets pass in every report since 2026-10-18, and
# fx_all's limits follow what each effect does (hil_scan.py FXALL_EXPECT), so a classic FAIL is a finding again
# (fx_all's wah rows were fx.c's stale wah depth law, a defect fixed 2026-10-24). Names added here are reported INFO, not FAIL
CLASSIC_STALE = set()
CLASSES = ("JACK FAIL", "UNDECIDED", "USB-ONLY", "INFO")


# ---------------------------------------------------------------- classification
CELL = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?(?:\s*/\s*[-+]?\d+(?:\.\d+)?)*\s*(?:dB|LU|ms|cents)?\s*(?:\(floor\))?\s*$")


def _nums(text):
    """a plain value cell ('+2.31', '3.6 dB', '+2.31 / -2.86 LU') -> its numbers; None for anything else (a ratio,
    'x1.49', Hz pairs, '->' sequences: the criterion is about something computed from them)"""
    if not CELL.match(str(text)):
        return None
    return [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", str(text))]


def judge(crit, value_text):
    """the simple criteria ('>= 3 dB', '< 1 dB', '|step| <= 1.5 LU', '|cents| <= 2 ...', '+6 .. +12.5 LU') on a plain
    value cell, every number in it: True / False; None when not machine-readable (a row without src_ok: UNDECIDED)"""
    vs = _nums(value_text)
    if not vs:
        return None
    c = str(crit).strip()
    m = re.match(r"^\|[a-z ]+\|\s*(<=|<)\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB|LU|ms)?(?:\s*$|\s*\()", c)
    if m:
        lim = float(m.group(2))
        return all(abs(v) <= lim if m.group(1) == "<=" else abs(v) < lim for v in vs)
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*\.\.\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB|LU|ms)?(?:\s*$|\s*\()", c)
    if m:
        return all(float(m.group(1)) <= v <= float(m.group(2)) for v in vs)
    m = re.match(r"^(>=|<=|>|<)\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB|LU|ms)?(?:\s*$|\s*\()", c)
    if m:
        lim = float(m.group(2))
        return all({">=": v >= lim, "<=": v <= lim, ">": v > lim, "<": v < lim}[m.group(1)] for v in vs)
    return None


def classify_vibes_row(row, takes):
    """one VIBES report row -> [(class, reason)] (empty for a passing / skipped row); takes {tag: take dict}"""
    if row.get("ok") or row.get("status") == "skip":
        return []
    check, st = row.get("check", ""), row.get("status", "")
    if check.startswith("takes: the USB stream slipped"):
        return [("USB-ONLY", "USB capture artefact: the USB stream slipped, the jack did not")]
    if check.startswith("takes: full scale / clicks"):
        tags = [t.strip() for t in str(row.get("usb", "")).split(",") if t.strip()]
        jack_bad = [t for t in tags if (takes.get(t) or {}).get("jack_clicks")]
        no_jack = [t for t in tags if t not in jack_bad and "jack_clicks" not in (takes.get(t) or {})]
        usb_only = [t for t in tags if t not in jack_bad and t not in no_jack]
        out = []
        if jack_bad:
            out.append(("JACK FAIL", "clicks on the jack: " + ", ".join(
                f"{t} ({takes[t]['jack_clicks']})" for t in jack_bad)))
        if no_jack:
            out.append(("UNDECIDED", "USB full scale / clicks, no jack recording of: " + ", ".join(no_jack)))
        if usb_only:
            out.append(("USB-ONLY", "USB-only full scale / clicks (the jack clean): " + ", ".join(
                f"{t} (USB fs {takes[t].get('usb_fs', 0)}, clicks {takes[t].get('usb_clicks', 0)})" for t in usb_only)))
        return out
    if check == "suite error":
        return [("UNDECIDED", f"suite error: {row.get('usb', '')}")]
    if st.startswith("info"):
        return [("INFO", "information row" + (", USB / jack MISMATCH" if "MISMATCH" in st else ""))]
    if st.startswith("ok"):
        mm = row.get("mismatch")
        return [("INFO", "both pass; USB and jack disagree" + (f" by {mm:+.2f} dB" if isinstance(mm, (int, float)) else ""))]
    src = row.get("src_ok")
    if isinstance(src, dict) and "jack" in src:
        if not src["jack"]:
            return [("JACK FAIL", "the jack fails" + ("" if src.get("usb", True) else " (USB too)"))]
        return [("USB-ONLY", "the jack passes, USB fails" if not src.get("usb", True) else
                 "both pass the criterion; failed on another condition")]
    jack = str(row.get("jack", "")).strip()
    if jack in ("", "-", "n/a") or jack.startswith("no value"):
        return [("UNDECIDED", "no jack value (a USB-only measure, or the jack was not recorded)")]
    j = judge(row.get("crit", ""), jack)
    if j is None:
        return [("UNDECIDED", "the jack has a value but the criterion is not machine-readable (old JSON, no src_ok)")]
    return [("USB-ONLY", "the jack passes (criterion re-read)")] if j else [("JACK FAIL", "the jack fails (criterion re-read)")]


CLASSIC_LINE = re.compile(r"^(ok|FAIL)\s+(\S+)\s+(.*?)\s+\((\d+) s\)\s*$")


def parse_classic(text):
    """report.txt / the classic log -> [(ok, name, msg)]"""
    return [(m.group(1) == "ok", m.group(2), m.group(3)) for m in map(CLASSIC_LINE.match, text.splitlines()) if m]


def classify_classic(name, msg):
    if name in CLASSIC_STALE:
        return "INFO", "known stale limit (AGENTS.md); USB only"
    if "slip" in msg.lower():
        return "USB-ONLY", "USB stream slip"
    return "UNDECIDED", "USB only (the classic suites do not record the jack): re-check on the jack"


CPU_WORST = re.compile(r"^worst (\d+) us of")
CPU_SHEDS = re.compile(r"voices shed (\d+)")


def parse_cpu(text):
    """an hil_cpu log -> (worst us or None, voices shed in total)"""
    worst, shed = None, 0
    for ln in text.splitlines():
        m = CPU_WORST.match(ln.strip())
        if m:
            worst = int(m.group(1))
        shed += sum(int(x) for x in CPU_SHEDS.findall(ln))
    return worst, shed


# ---------------------------------------------------------------- summary
def _read(path):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return None


def _json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def build_summary(d):
    """SUMMARY.md from the run directory d; returns (text, counts {class: n}, step errors)"""
    meta = _json(os.path.join(d, "steps.json")) or {}
    steps = meta.get("steps", [])
    L = [f"# Full device scan {os.path.basename(os.path.normpath(d))}", "",
         "The rule: always record the jack and USB, but the JACK decides. JACK FAIL = real; UNDECIDED = failing with "
         "no jack value; USB-ONLY = the jack passes or a USB capture artefact (tooling, not product); INFO.", ""]
    info = _read(os.path.join(d, "info.log"))
    fw = [ln for ln in (info or "").splitlines() if ln.strip() and not ln.startswith("$ ")]
    L += [f"- FM-1 reports: {fw[-1].strip() if fw else '(no --info output)'}"]
    if meta.get("git"):
        L += [f"- tools at: {meta['git']}"]
    L += [""]
    errors = [s for s in steps if s.get("rc") not in (0, None) and s["name"] not in ("classic", "vibes")]
    if steps:
        L += ["## Steps", "", "| step | exit | time | log |", "|---|---|---|---|"]
        L += [f"| {s['name']} | {s.get('rc')} | {s.get('secs', 0):.0f} s | {s['name']}.log |" for s in steps]
        L += [""]

    # CPU
    L += ["## CPU (worst audio block of 2 runs)", "",
          f"| engine | runs (us) | worst | to {CPU_TARGET} | to {CPU_GATE} | to {CPU_SHED} (shedder) | voices shed |",
          "|---|---|---|---|---|---|---|"]
    for eng in ("rhodes", "vibes"):
        runs = []
        for f in sorted(os.listdir(d)):
            if f.startswith(f"cpu-{eng}-") and f.endswith(".log"):
                runs.append(parse_cpu(_read(os.path.join(d, f)) or ""))
        ws = [w for w, _ in runs if w is not None]
        if not ws:
            L.append(f"| {eng} | (no result) | | | | | |")
            continue
        w = max(ws)
        L.append(f"| {eng} | {', '.join(str(x) for x in ws)} | **{w}** | {CPU_TARGET - w:+d} | {CPU_GATE - w:+d} | "
                 f"{CPU_SHED - w:+d} | {sum(s for _, s in runs)} |")
    L += [""]

    # hiss / SNR
    L += ["## Hiss / SNR (tools/hil_jack.py, re the interface's full scale)", "",
          "| engine | jack floor (muted) dB | hiss 8-20k jack dB | hiss USB dBFS | jack adds dB | loud jack peak dB | SNR dB |",
          "|---|---|---|---|---|---|---|"]
    for eng, fn in (("rhodes", "jack.json"), ("vibes", "jack-vibes.json")):
        j = _json(os.path.join(d, fn))
        if not j:
            L.append(f"| {eng} | (no result) | | | | | |")
            continue
        g = lambda *k: _dig(j, *k)  # noqa: E731
        L.append(f"| {eng} | {_f(g('silence', 'jack_db'))} | {_f(g('hiss', 'jack_hf_db'))} | {_f(g('hiss', 'usb_hf_db'))} "
                 f"| {_f(g('hiss_added_db'))} | {_f(g('loud', 'jack_peak_db'))} | **{_f(g('snr_db'))}** |")
    L += [""]

    # suites
    rows = []                                      # (class, suite, check, setting, usb, jack, crit, reason)
    L += ["## Suites", "", "| suite | checks | ok | FAIL / flagged |", "|---|---|---|---|"]
    ctext = _read(os.path.join(d, "report.txt")) or _read(os.path.join(d, "classic.log"))
    if ctext:
        cl = parse_classic(ctext)
        for ok, name, msg in cl:
            L.append(f"| classic {name} | 1 | {int(ok)} | {int(not ok)} |")
            if not ok:
                c, why = classify_classic(name, msg)
                rows.append((c, f"classic {name}", msg[:160], "", "(USB)", "-", "", why))
    else:
        L.append("| classic | (no report) | | |")
    v = _json(os.path.join(d, "vibes-report.json"))
    if v:
        takes = {t.get("tag"): t for t in v.get("takes", [])}
        for sname, srows in v.get("suites", {}).items():
            live = [r for r in srows if r.get("status") != "skip"]
            nf = sum(not r.get("ok") for r in live)
            L.append(f"| {sname} | {len(live)} | {len(live) - nf} | {nf} |")
            for r in srows:
                for c, why in classify_vibes_row(r, takes):
                    rows.append((c, sname, r.get("check", ""), r.get("setting", ""), str(r.get("usb", ""))[:120],
                                 str(r.get("jack", "")), r.get("crit", ""), why))
    else:
        L.append("| vibes | (no report) | | |")
    seq = _read(os.path.join(d, "seq.log"))
    if seq is not None:
        tail = [ln for ln in seq.splitlines() if ln.strip() and not ln.startswith("$ ")][-3:]
        L += ["", "Sequencer (hil_seq, read by eye): " + " / ".join(x.strip() for x in tail)]
    L += [""]

    counts = {c: sum(r[0] == c for r in rows) for c in CLASSES}
    L += ["## Verdict", "", " ".join(f"{c}: {counts[c]}" for c in CLASSES) +
          (f"; step errors: {', '.join(s['name'] for s in errors)}" if errors else ""), ""]
    for c in CLASSES:
        sel = [r for r in rows if r[0] == c]
        if not sel:
            continue
        L += [f"## {c} ({len(sel)})", "", "| suite | check | setting | USB | jack | criterion | why |",
              "|---|---|---|---|---|---|---|"]
        L += ["| " + " | ".join(_cell(x) for x in r[1:]) + " |" for r in sel]
        L += [""]
    return "\n".join(L), counts, errors


def _dig(j, *k):
    for x in k:
        if not isinstance(j, dict) or x not in j:
            return None
        j = j[x]
    return j


def _f(x):
    return f"{x:.1f}" if isinstance(x, (int, float)) else "-"


def _cell(x):
    return str(x).replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------- the device lock
def main_repo(start):
    try:
        g = subprocess.run(["git", "-C", start, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        if g:
            return os.path.dirname(g)
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def DeviceLock(root, who, wait_s, poll_s=10.0):
    """build/hil/DEVICE.lockdir through tools/lock.py: the holder's token (FRODHES_LOCK_DEVICE) passes at once, a
    dead holder is reaped, a stale one reported; released at the end only when still ours"""
    return lock.Lock("DEVICE", root=root, who=who, wait_s=wait_s, poll_s=poll_s)


# ---------------------------------------------------------------- running
def run_step(d, name, argv, timeout, copies=()):
    """one tool as a subprocess, its output to <d>/<name>.log and the console; copies [(build/hil file, as)]"""
    before = {src: _mtime(os.path.join(HIL, src)) for src, _ in copies}
    log = os.path.join(d, name + ".log")
    print(f"\n=== {name}: {' '.join(argv)}", flush=True)
    t0, rc = time.time(), None
    with open(log, "w") as f:
        f.write("$ " + " ".join(argv) + "\n")
        f.flush()
        try:
            p = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            dog = threading.Timer(timeout, p.kill)        # a hung tool (no output) is killed too
            dog.start()
            try:
                for ln in p.stdout:
                    f.write(ln)
                    sys.stdout.write(ln)
                rc = p.wait(timeout=30)
                if not dog.is_alive() and time.time() - t0 >= timeout:
                    f.write(f"\n[full_scan] killed after {timeout} s\n")
            except BaseException:
                p.kill()
                raise
            finally:
                dog.cancel()
        except OSError as e:
            f.write(f"[full_scan] could not start: {e}\n")
            rc = -1
    for src, dst in copies:
        sp = os.path.join(HIL, src)
        if _mtime(sp) and _mtime(sp) != before[src]:
            shutil.copy2(sp, os.path.join(d, dst))
        else:
            print(f"[full_scan] {name}: build/hil/{src} was not (re)written", flush=True)
    secs = time.time() - t0
    print(f"=== {name}: exit {rc}, {secs:.0f} s", flush=True)
    return dict(name=name, argv=argv, rc=rc, secs=secs)


def _mtime(p):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return None


def plan(a):
    py, t = sys.executable, lambda n: os.path.join(TOOLS, n)
    jack = ["--jack-dev", a.jack_dev, "--jack-ch", str(a.jack_ch)]
    S = [("info", [py, t("fm1_install.py"), "--info"], 60, ())]
    for eng, extra in (("rhodes", []), ("vibes", ["--engine", "vibes"])):
        for i in (1, 2):
            S.append((f"cpu-{eng}-{i}", [py, t("hil_cpu.py")] + extra, 900, ()))
    S.append(("classic", [py, t("hil_scan.py")], 3600, (("report.json", "report.json"), ("report.txt", "report.txt"))))
    S.append(("vibes", [py, t("hil_scan.py"), "--vibes", "--source", "both", "--wav"] + jack, 5400,
              (("vibes-report.json", "vibes-report.json"), ("vibes-report.md", "vibes-report.md"))))
    S.append(("jack-rhodes", [py, t("hil_jack.py"), "--dev", a.jack_dev, "--ch", str(a.jack_ch)], 900,
              (("jack.json", "jack.json"),)))
    S.append(("jack-vibes", [py, t("hil_jack.py"), "--dev", a.jack_dev, "--ch", str(a.jack_ch), "--engine", "vibes"],
              900, (("jack-vibes.json", "jack-vibes.json"),)))
    if a.seq:
        S.append(("seq", [py, t("hil_seq.py")], 300, ()))
    skip = set(filter(None, (a.skip or "").split(",")))
    return [s for s in S if not any(s[0] == k or s[0].startswith(k + "-") for k in skip)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", "--offline", dest="offline", metavar="DIR",
                    help="rebuild SUMMARY.md from an earlier run directory; no device")
    ap.add_argument("--root", help="the main repo holding build/hil/DEVICE.lockdir (default: this repo's main checkout)")
    ap.add_argument("--wait-lock", type=int, default=3600, help="seconds to wait for the device lock")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--seq", action="store_true", help="also tools/hil_seq.py (clears the FM-1's pattern)")
    ap.add_argument("--skip", help="steps to leave out, comma-separated: info, cpu, cpu-vibes, classic, vibes, jack ...")
    ap.add_argument("--who", default=f"full_scan@{socket.gethostname()}", help="the lock owner's name")
    a = ap.parse_args()
    if a.offline:
        d = a.offline
    else:
        d = os.path.join(HIL, "full", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(d, exist_ok=True)
        git = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%h %s"], capture_output=True, text=True).stdout.strip()
        steps = []
        meta = dict(git=git, steps=steps)
        with DeviceLock(a.root or main_repo(REPO), a.who, a.wait_lock):
            for name, argv, timeout, copies in plan(a):
                steps.append(run_step(d, name, argv, timeout, copies))
                json.dump(meta, open(os.path.join(d, "steps.json"), "w"), indent=1)
                if name == "info" and steps[-1]["rc"] != 0:
                    print("[full_scan] fm1_install --info found no FM-1 running Frodhes: stopping", flush=True)
                    break
    text, counts, errors = build_summary(d)
    out = os.path.join(d, "SUMMARY.md")
    open(out, "w").write(text + "\n")
    print("\n" + " ".join(f"{c}: {counts[c]}" for c in CLASSES) + (f"; step errors: {len(errors)}" if errors else ""))
    print(f"summary: {os.path.relpath(out, REPO) if out.startswith(REPO) else out}")
    return 1 if counts["JACK FAIL"] or errors else 0


if __name__ == "__main__":
    sys.exit(main())
