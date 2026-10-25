#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Which release tests a change needs: every tracked file -> its test tiers (private/TEST-PLAN.md v2).

    tools/test_map.py [--since TAG] [--json] [FILE ...]

With no FILE: the files changed in `git diff --name-only TAG..HEAD` (TAG: the latest v* tag by default). Prints the
tiers, the T1 scenarios, the T2 suites and the host extras, and why (each group with its files).

 T0 host        every push (CI): make test, make pytest, Gate R, the build. Always needed.
 T1 must-pass   the real-use scenarios on the FM-1 (`hil_scan.py --tier must`), the firmware's counters gated:
                1 DAW recording, stress_max (USB 44.1 + 48 kHz)   2 live on the jack   3 preset / TYPE switching
                4 controls under load   5 update / rescue   6 CPU per engine
 T2 full scans  an engine or the DSP changed: today's hil_scan suites, VIBES / MARIMBA scans, sample-back, the jack

The map (first match wins; a firmware file no rule names -> T1 all + T2, so nothing new slips through untested):
 fm1_adc.h, fm1_keyadc.h, fm1_input.h, keyvel.c / .h -> T1 all + the USB counters + the key velocity checks
 usb.c, platform.c, hal/*, the startup  -> T1 all + the USB counters at both rates
 frodhes.c, libc.c, build.sh, build.py  -> T1 all (the glue; T2 waits for a DSP change: Gate R proves the renders)
 ota.c, recovery.c, loader/*, packaging -> T1 5
 rhodes.c, bars.c, engine.c, the tables -> T1 all + T2 (that engine's scans + sample-back)
 fx.c, fx_tables.h                      -> T1 1/4/6 + T2 effects suites
 drive.c, drv_*.c, drive_tables.h       -> T1 1/4/6 + T2 effects suites (the DRIVE's TYPEs)
 rev_types.c, rev_*.c                   -> T1 1/4/6 + T2 effects suites (the REVERB's TYPEs)
 ui.c, params.c, presets.c, eng_pages.h -> T1 3/4/5 + the screenshots / docs tests (no audio scans)
 notes.c, arp.c, seq.c, tempo.c         -> T1 1/3 + the sequencer / arp scripts
 docs / tools / tests only              -> T0 only
"""
import argparse
import fnmatch
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCENARIOS = {1: "DAW recording: stress_max (USB 44.1 + 48 kHz)", 2: "live on the jack", 3: "preset / TYPE switching",
             4: "controls under load", 5: "update / rescue", 6: "CPU per engine"}
ALL = (1, 2, 3, 4, 5, 6)
ENGINES_T2 = ("rhodes", "vibes", "marimba")
SHOTS = "the screenshots / docs tests (tools/manual_shots.py, test_manual_shots, test_docs_vs_code)"
SEQ = "the sequencer / arp scripts (tests/scripts/seq.txt, arp*.txt; tools/hil_seq.py)"
USB2 = "the USB counters at both rates (44.1 and 48 kHz)"
KEYVEL = ("the key velocity checks: hil_cpu.py --keyadc gate (OFF / idle / STRESS: idle <= +10 us, STRESS <= +60 us, "
          "3 engines x 3 runs), hil_keyvel.py selftest, soak (10 min, zero edges), jack (no 455 / 909 Hz / 10 kHz "
          "line, idle SNR within 0.5 dB of OFF; USB and battery power), record (the user plays: separation, evenness, "
          "extremes, latency) and compare (battery vs USB power)")

# (group, patterns, T1 scenarios, T2 suites, host extras, why). First match wins: the specific rules first.
RULES = [
    ("keyvel", ["firmware/hal/fm1_adc.h", "firmware/hal/fm1_keyadc.h", "firmware/hal/fm1_input.h",
                "firmware/src/keyvel.c", "firmware/src/keyvel.h"], ALL, (), (USB2, KEYVEL),
     "the key velocity: the converter's scheduler (MASTER, the battery), the key rows' drive and LED blanking in the "
     "scan tick, the velocity itself"),
    ("usb", ["firmware/src/usb.c", "firmware/src/uac_fir.h"], ALL, (), (USB2,),
     "the USB audio / MIDI stack (the ring the DAW hears)"),
    ("platform", ["firmware/src/platform.c", "firmware/src/store.h", "firmware/src/frodhes_store.h",
                  "firmware/src/set_store.c", "firmware/src/smk_wheel.h", "firmware/hal/*", "firmware/crt0.S", "firmware/app.ld"], ALL, (),
     (USB2,), "the platform: the ISRs, the clocks, the memory map (every scenario rides on it)"),
    # (the app's glue, not its DSP: the voices and effects are rhodes.c / bars.c / fx.c, and Gate R on the host
    # proves every render byte-identical, so the full scans wait for a DSP change)
    ("app", ["firmware/src/frodhes.c", "firmware/src/frodhes_smk.h", "firmware/src/libc.c"], ALL, (), (USB2,),
     "the app's glue: the audio ISR's wiring, the main loop, the MIDI CC map"),
    ("engine:rhodes", ["firmware/src/rhodes.c", "firmware/gen/rhodes_tables.h", "firmware/gen/rhodes_phim.h", "firmware/gen/rh_tone_g.h"], ALL, ("rhodes",), (),
     "the Rhodes engine (its DSP or tables)"),
    ("engine:bars", ["firmware/src/bars.c", "firmware/src/bars.h", "firmware/gen/bars_tables.h"], ALL,
     ("vibes", "marimba"), (), "the bar engine (VIBES, MARIMBA: its DSP or tables)"),
    ("engine:dispatch", ["firmware/src/engine.c", "firmware/src/fxp.h"], ALL, ENGINES_T2 + ("effects",), (),
     "the engine dispatcher / the fixed-point helpers (every engine's DSP)"),
    ("fx", ["firmware/src/fx.c", "firmware/src/dly_tape.c", "firmware/gen/fx_tables.h"], (1, 4, 6), ("effects",), (),
     "the effects chain (its DSP or tables)"),
    ("fx:drive", ["firmware/src/drive.c", "firmware/src/drv_*.c", "firmware/gen/drive_tables.h"], (1, 4, 6),
     ("effects",), (), "the DRIVE's TYPEs (the framework, a TYPE's kernel or its tables)"),
    ("fx:reverb", ["firmware/src/rev_types.c", "firmware/src/rev_*.c"], (1, 4, 6), ("effects",), (),
     "the REVERB's TYPEs (the framework or a TYPE's kernel)"),
    ("ui", ["firmware/src/ui.c", "firmware/src/params.c", "firmware/src/presets.c", "firmware/src/eng_pages.h",
            "firmware/src/aatext.c", "firmware/src/gfx.c", "firmware/src/lcd.c", "firmware/gen/*font*.h"],
     (3, 4, 5), (), (SHOTS,), "the panel, the parameters, the presets, the screen"),
    ("notes", ["firmware/src/notes.c", "firmware/src/arp.c", "firmware/src/seq.c", "firmware/src/tempo.c",
               "firmware/src/midi_uart.c"], (1, 3), (), (SEQ,), "the notes, the arp, the sequencer, the tempo, MIDI in"),
    ("update", ["firmware/src/ota.c", "firmware/src/recovery.c", "firmware/src/bootguard.h", "firmware/loader/*"],
     (5,), (), (), "the update / recovery path and the loader"),
    ("build", ["build.sh", "tools/build.py"], ALL, (), (), "the firmware build itself (flags, sections, checks)"),
    ("package", ["tools/fm1pkg_make.py", "tools/lz4blk.py"], (5,), (), (),
     "the package builder (what the update installs)"),
    ("firmware:unknown", ["firmware/*"], ALL, ENGINES_T2 + ("effects",), (USB2,),
     "a firmware file no rule names: everything, until the map names it"),
    ("host", ["*"], (), (), (), "docs / tools / tests / CI only: the host tests"),
]


def rule_for(path):
    for r in RULES:
        if any(fnmatch.fnmatchcase(path, p) for p in r[1]):
            return r
    raise AssertionError("the last rule matches everything")


def tiers_of(path):
    """one file -> {'group', 'tiers', 't1', 't2', 'extras', 'why'}"""
    g, _, t1, t2, extras, why = rule_for(path)
    tiers = ["T0"] + (["T1"] if t1 else []) + (["T2"] if t2 else [])
    return dict(group=g, tiers=tiers, t1=list(t1), t2=list(t2), extras=list(extras), why=why)


def analyze(files):
    """files -> the union: {'tiers', 't1', 't2', 'extras', 'groups': {group: {'why', 'files'}}, 'files'}"""
    tiers, t1, t2, extras, groups = {"T0"}, set(), [], [], {}
    for f in files:
        m = tiers_of(f)
        tiers.update(m["tiers"])
        t1.update(m["t1"])
        t2 += [s for s in m["t2"] if s not in t2]
        extras += [e for e in m["extras"] if e not in extras]
        g = groups.setdefault(m["group"], dict(why=m["why"], tiers=m["tiers"], files=[]))
        g["files"].append(f)
    return dict(tiers=sorted(tiers), t1=sorted(t1), t2=t2, extras=extras, groups=groups, files=list(files))


def t1_text(t1):
    if not t1:
        return "none"
    return "all (1-6)" if tuple(t1) == ALL else "/".join(map(str, t1)) + " (" + ", ".join(SCENARIOS[i] for i in t1) + ")"


def reason(res):
    """the tiers and why, as lines"""
    lines = [f"tiers {' + '.join(res['tiers'])}; T1 scenarios {t1_text(res['t1'])}; "
             f"T2 suites {', '.join(res['t2']) or 'none'}"]
    if res["extras"]:
        lines.append("host extras: " + "; ".join(res["extras"]))
    if not res["files"]:
        lines.append("no file changed")
    for g, d in res["groups"].items():
        fs = d["files"]
        lines.append(f"  {'+'.join(d['tiers']):9s} {g}: {d['why']}: "
                     + ", ".join(fs[:6]) + (f" (+{len(fs) - 6} more)" if len(fs) > 6 else ""))
    return lines


def latest_tag(root=ROOT):
    tags = subprocess.run(["git", "tag", "--sort=-v:refname", "--list", "v*"], cwd=root, capture_output=True,
                          text=True).stdout.split()
    return tags[0] if tags else None


def changed_since(tag, root=ROOT):
    p = subprocess.run(["git", "diff", "--name-only", f"{tag}..HEAD"], cwd=root, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"git diff {tag}..HEAD failed: {p.stderr.strip()}")
    return [l for l in p.stdout.splitlines() if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="the test tiers a change needs (T0 host, T1 must-pass, T2 full scans)")
    ap.add_argument("files", nargs="*", help="the changed files (default: git diff --name-only TAG..HEAD)")
    ap.add_argument("--since", metavar="TAG", help="the tag to diff from (default: the latest v* tag)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    if a.files:
        files, src = a.files, "the files given"
    else:
        tag = a.since or latest_tag()
        if not tag:
            raise SystemExit("no v* tag to diff from (pass --since REF)")
        files, src = changed_since(tag), f"git diff {tag}..HEAD ({{}} file(s))"
        src = src.format(len(files))
    res = analyze(files)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(src + ":")
        print("\n".join(reason(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
