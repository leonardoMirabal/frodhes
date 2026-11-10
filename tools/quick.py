#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""make quick: the small, focused checks that found every real fault of 0.9.10 (2026-11-08: v127 distortion, the
limiter on the effects, the FX bypass level, KEY VEL saturating, the headphone level), in under 2 minutes warm, on
every change, before anything else. One line per engine / TYPE / effect: PASS or FAIL with the number against its
limit. The big grids (make release-diff, the long make pytest) are the net before a candidate, not the first check.

  dry       tools/dry_dist.py: H2..H6 re H1 in the strike's window (aH*) and the body's, the inharmonic residue, the
            chords' IMD, the FM-1's keys through the key path at their hardest; against 0.9.9 (H2 + 1.5 dB, the rest
            + 3 dB, H3 <= -25 dB at v127). One line per config / TYPE / RESPONSE
  keys      tests/scripts/keyvel*.txt: the key path's velocities (KEY VEL 127: medium 107, floor 122, ...)
  fx        tools/fx_pristine.py (the FM-1 build): every effect but DRIVE at MIX / DEPTH 0, 64, 127: the limiter idle,
            no clamp, the level at 0 equal to the bypass, no added non-linearity. One line per effect
  bypass    tests/py/test_bypass_level.py: FX off keeps the compressor; FX off = FX on at MIX 0 (level and width)
  level     the absolute output: every TYPE (dry_dist's TYPE base) and each factory preset by name, a v127 chord and a
            v127 note, critical-band loudness within 1.5 dB of 0.9.9's (RMS shown) and the peak at most 0.9.9's + 1 dB (USB's digital scale = the jack's at MASTER full: the
            headphones' headroom). tests/baselines/quick_level_099.json
  midi      MIDI velocity reaches the engine untouched: the same MIDI notes (v1 / 40 / 96 / 127) render byte for byte
            the same at every TOUCH (LIGHT MEDIUM HEAVY FIXED) and KEY VEL (1 96 127), and the setting did change
  pristine  tools/release_diff.py --grid fast: every TYPE x FM-1 / HQ x effects off / RAW / FX: the pristine ceilings
            (distortion, aliasing, the key-up click post-vs-pre, noise, clips, never louder after the key-up) and the
            moves against 0.9.9 (tests/expected_changes.yaml). One line per TYPE
  crackle   tools/hil_crackle.py --host: the worst case (every voice, every effect, the pedal) through the glitch
            detectors, per engine
The Move (K27; its HQ host build, MOVE_DEFS + HQ_DEFAULT=1: the plugin as it loads, 16 voices):
  move-dry  tools/dry_dist.py --hq: the same cells against tests/baselines/dry_dist_099_hq.json (0.9.9's Move build)
  move-fx   tools/fx_pristine.py --builds hq: every effect but DRIVE, as fx, on the Move's build
  move-byp  tests/py/test_move_bypass_comp.py: the Move's FX off keeps the compressor, at the level of FX on
  move-lvl  level's cases on the Move's build: RMS within 1.5 dB of 0.9.9's Move build, the peak at most + 1 dB
            (tests/baselines/quick_level_099_hq.json)
  move-ui   Gate U (tools/gen_move_module.py --check) and move/ui/test_chain.mjs (the screen; no pad or step LED)

  tools/quick.py [--fm1 BIN] [--hq BIN] [--only dry,fx,...]      exit 1 on any FAIL; build/quick/report.txt
  tools/quick.py --save-level --fm1 BIN                          the level baseline (ONLY on the last release's build)
  tools/quick.py --save-level --hq BIN                           the Move's level baseline (the last release's Move
                                                                 build: tools/dry_dist.py --build-hq OUT --src TREE)
  tools/quick.py --save-new-level --fm1 BIN --hq BIN             the presets 0.9.9 did not have: their own reference,
                                                                 on the build they came with (tests/baselines/
                                                                 quick_level_new*.json)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
OUT = os.path.join(ROOT, "build", "quick")
PY = sys.executable
LEVEL_BASE = os.path.join(ROOT, "tests", "baselines", "quick_level_099.json")
LEVEL_BASE_HQ = os.path.join(ROOT, "tests", "baselines", "quick_level_099_hq.json")
FS = 44100
LVL_TOL, PEAK_TOL = 1.5, 1.0


def sh(cmd, timeout=600):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def row(ok, area, name, detail):
    return (bool(ok), area, name, detail)


# ---------------------------------------------------------------- dry: the dry engine's distortion
def check_dry(b, key="fm1", area="dry"):
    import dry_dist
    cells = dry_dist.measure(b[key], os.path.join(OUT, area))
    rows = dry_dist.judge(cells, json.load(open(dry_dist.BASE_HQ if key == "hq" else dry_dist.BASE)))
    by = {}
    for c, m, v, base, lim, ok in rows:
        tag = c.split()[0]
        w = by.setdefault(tag, [True, None])
        w[0] &= ok
        if lim is not None and (w[1] is None or lim - v < w[1][0]):
            w[1] = (lim - v, c.split()[1], m, v, lim)
    out = []
    for tag, (ok, worst) in sorted(by.items()):
        d = f"worst {worst[1]} {worst[2]} {worst[3]:.1f} dB (limit {worst[4]:.1f})" if worst else "-"
        out.append(row(ok, area, tag, d + ("" if ok else f"; {sum(1 for r in rows if r[0].startswith(tag) and not r[5])} over")))
    return out


def check_keys(b):
    out = []
    for s in sorted(f for f in os.listdir(os.path.join(ROOT, "tests", "scripts")) if f.startswith("keyvel")):
        o = os.path.join(OUT, "keys", s[:-4])
        os.makedirs(os.path.dirname(o), exist_ok=True)
        code, txt = sh([b["fm1"], o, os.path.join("tests", "scripts", s)])
        fails = re.findall(r"FAIL (.*)", txt)
        out.append(row(code == 0, "keys", s[:-4], "every expect" if code == 0 else f"{code} expect(s) failed: "
                       + "; ".join(fails[:2])))
    return out


# ---------------------------------------------------------------- fx: every effect but DRIVE
def stage_fx(b):
    """the two builds into fx_pristine's folder (build/fxp/one, build/fxp/hq), before any check's thread starts: a
    copy made while other threads fork (a multiprocessing pool's workers never exec, and keep every descriptor) left
    the file open for writing in a child, and fx_pristine's exec of it failed 'Text file busy' (Linux arm64 CI,
    rc2-0910 e4628db5). Each goes in under a temporary name and is renamed into place."""
    import fx_pristine
    os.makedirs(fx_pristine.OUT, exist_ok=True)
    for key, fb in (("fm1", "one"), ("hq", "hq")):
        tmp = os.path.join(fx_pristine.OUT, fb + ".stage")
        shutil.copy2(b[key], tmp)
        os.replace(tmp, os.path.join(fx_pristine.OUT, fb))
    b["fx_staged"] = True


def check_fx(b, key="fm1", area="fx"):
    import fx_pristine
    fb = "hq" if key == "hq" else "one"           # (fx_pristine's build names: one = the FM-1, hq = MOVE_DEFS)
    if b"fx-clip delay" not in open(b[key], "rb").read():
        # (fx_pristine reads a build without the counters as the limiter acting everywhere: say what it is instead)
        return [row(False, area, "counters", "this build has no fx-clip counters (engine.c fx_h_clip, 523d8fa7): "
                    "the limiter and the clamps are not measured")]
    if not b.get("fx_staged"):                     # (main() stages them before its threads: see stage_fx)
        stage_fx(b)
    code, txt = sh([PY, "tools/fx_pristine.py", "--builds", fb])
    bad = {}
    for m in re.finditer(r"^\s+%s (\S+)@(\d+): (.*)$" % fb, txt, re.M):
        bad.setdefault(m.group(1), []).append(f"@{m.group(2)} {m.group(3)}")
    out = [row(name not in bad, area, name, "clean at 0 / 64 / 127" if name not in bad else "; ".join(bad[name][:3]))
           for name in fx_pristine.EFFECTS]
    if code and not bad:
        out.append(row(False, area, "run", txt.strip().splitlines()[-1][:120]))
    return out


def check_bypass(b):
    """the test compiles its own builds from a source tree: this one, or --tree (a copy of the test, hostrun and the
    Makefile put into it)"""
    tree = b.get("tree", ROOT)
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/py/test_bypass_level.py"]
    if tree != ROOT:
        os.makedirs(os.path.join(tree, "tests", "py"), exist_ok=True)
        for f in ("tests/py/test_bypass_level.py", "tests/py/hostrun.py", "Makefile"):
            shutil.copy2(os.path.join(ROOT, f), os.path.join(tree, f))
        p = subprocess.run(cmd + ["--noconftest"], cwd=tree, capture_output=True, text=True, timeout=600)
        code, txt = p.returncode, p.stdout + p.stderr
    else:
        code, txt = sh(cmd)
    last = [l for l in txt.splitlines() if "passed" in l or "failed" in l or "error" in l]
    fails = re.findall(r"^FAILED (\S+)", txt, re.M)
    return [row(code == 0, "bypass", "FX off = FX on at MIX 0", (last[-1] if last else txt[-120:])
                + ("" if not fails else "; " + ", ".join(f.split("::")[-1] for f in fails)))]


# ---------------------------------------------------------------- level: the absolute output against 0.9.9
# (2026-11-10, the proposed 19: the presets by NAME, not by slot, and judged by critical-band loudness (tools/cb_loud.py,
# the level matching the presets were made with), RMS kept as information. THRESHOLD: cb loudness within +-1.5 dB of
# 0.9.9's preset of the same name (was RMS +-1.5 dB by slot); the peak at most 0.9.9's + 1 dB as before. A preset 0.9.9
# did not have is judged against its own reference, measured on the build it came with (LEVEL_NEW / LEVEL_NEW_HQ).)
LEVEL_TYPES = (("TYPE-ST77", 80, 0), ("TYPE-SUIT", 80, 32), ("TYPE-FELT", 80, 64), ("TYPE-DYNO", 80, 96),
               ("TYPE-VIBES", 79, 0), ("TYPE-MARIMBA", 79, 127))
LEVEL_NEW = os.path.join(ROOT, "tests", "baselines", "quick_level_new.json")
LEVEL_NEW_HQ = os.path.join(ROOT, "tests", "baselines", "quick_level_new_hq.json")


def preset_names(tree=ROOT):
    """a tree's factory preset names in slot order (presets.c PR_FNAME)"""
    s = open(os.path.join(tree, "firmware", "src", "presets.c")).read()
    return re.findall(r'"([^"]*)"', re.search(r"PR_FNAME\[PR_NF\] = \{(.*?)\};", s, re.S).group(1))


def level_cases(tree=ROOT):
    """[(case, setup lines, bars)]: every TYPE on dry_dist's TYPE base (not F01's voicing), then each factory preset
    by name, loaded by its program change in that tree (0..15 F01..F16, 32.. F17..)"""
    import dry_dist
    out = [(n, dry_dist.type_base_lines(95) + [f"100 midi 0xB0 {c} {v}"], c == 79) for n, c, v in LEVEL_TYPES]
    for k, name in enumerate(preset_names(tree)):
        out.append((name, [f"100 midi 0xC0 {k if k < 16 else k + 16}"], 12 <= k < 16))
    return out


def level_one(binary, name, setup, bars, sub="level"):
    import cb_loud
    import release_diff as rd
    chord, note = ((65, 69, 72, 77), 69) if bars else ((48, 55, 60, 64), 45)
    ls = list(setup) + [f"1000 midi 0x90 {n} 127" for n in chord] + [f"1800 midi 0x80 {n} 0" for n in chord]
    ls += [f"3500 midi 0x90 {note} 127", "4300 midi 0x80 {} 0".format(note), "6000 end"]
    x = rd.render(binary, os.path.join(OUT, sub, name.replace(" ", "_")), sorted(ls, key=lambda l: int(l.split()[0])))
    seg = lambda a, b: x[int(a * FS):int(b * FS)]
    lv = lambda s: float(10 * np.log10(np.mean(np.square(s / 32768.0)) + 1e-20))
    pk = lambda s: float(20 * np.log10(np.abs(s).max() / 32768.0 + 1e-12))
    bd = lambda s: [float(v) for v in cb_loud.bands(s.mean(1) / 32768.0)]
    return {"chord": {"level": lv(seg(1.0, 1.8)), "peak": pk(seg(1.0, 3.4)), "bands": bd(seg(1.0, 1.8))},
            "note": {"level": lv(seg(3.5, 4.3)), "peak": pk(seg(3.5, 5.9)), "bands": bd(seg(3.5, 4.3))}}


def level_all(binary, sub="level", tree=ROOT, only=None):
    cs = [c for c in level_cases(tree) if only is None or c[0] in only]
    with ThreadPoolExecutor(4) as ex:
        return dict(zip([c[0] for c in cs], ex.map(lambda c: level_one(binary, *c, sub), cs)))


def cb_db(bands, ref_bands):
    """the critical-band loudness of bands re ref_bands, dB (the gain that brings ref to bands' loudness)"""
    import cb_loud
    return float(cb_loud.match_db(np.array(ref_bands), cb_loud.loud(np.array(bands))))


def check_level(b, key="fm1", area="level"):
    import fnmatch
    import release_diff as rd
    base = json.load(open(LEVEL_BASE_HQ if key == "hq" else LEVEL_BASE))["cases"]
    try:
        base_new = json.load(open(LEVEL_NEW_HQ if key == "hq" else LEVEL_NEW))["cases"]
    except OSError:
        base_new = {}
    got = level_all(b[key], area)
    # a level difference listed in tests/expected_changes.yaml ("quick-level/<fm1|hq>/<case>/<chord|note>", metric
    # "level", min / max: the move against 0.9.9 in dB, critical-band) passes; its reason says why
    listed = [e for e in rd.load_expected(rd.EXPECTED) if str(e.get("match", "")).startswith("quick-level/")]
    out = []
    for name, segs in got.items():
        ref, new = (base, False) if name in base else (base_new, True)
        if name not in ref:
            out.append(row(False, area, name, "no reference: a preset 0.9.9 did not have needs its own "
                           "(tools/quick.py --save-new-level on the build it came with)"))
            continue
        bad, worst = [], None
        for s, m in segs.items():
            br = ref[name][s]
            if "bands" not in br:
                bad.append(f"{s}: the reference has no critical bands (re-save it on the last release's build)")
                continue
            dl, dp, dr = cb_db(m["bands"], br["bands"]), m["peak"] - br["peak"], m["level"] - br["level"]
            ok = any(fnmatch.fnmatch(f"quick-level/{key}/{name}/{s}", e["match"]) and "level" in str(e.get("metric", "")).split()
                     and e.get("min", -99) <= dl <= e.get("max", 99) and str(e.get("reason", "")).strip() for e in listed)
            if abs(dl) > LVL_TOL and not ok:
                bad.append(f"{s} cb {dl:+.1f} dB")
            if dp > PEAK_TOL:
                bad.append(f"{s} peak {m['peak']:.1f} dBFS ({dp:+.1f})")
            if worst is None or abs(dl) > abs(worst[1]):
                worst = (s, dl, m["peak"], dp, dr)
        tag = " (new: its own reference)" if new else ""
        out.append(row(not bad, area, name, (f"{worst[0]} cb {worst[1]:+.1f} dB (limit +-{LVL_TOL}; RMS {worst[4]:+.1f}), "
                       f"peak {worst[2]:.1f} dBFS ({worst[3]:+.1f}, limit +{PEAK_TOL})" if not bad else "; ".join(bad)) + tag))
    return out


# ---------------------------------------------------------------- midi: velocity untouched by TOUCH / KEY VEL
TOUCHES = (("LIGHT", -1), ("MEDIUM", 0), ("HEAVY", 1), ("FIXED", 2))
KEYVELS = ((1, -200), (96, 0), (127, 200))   # (a large turn: to the end, whatever the acceleration)


def check_midi(b):
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    hostrun._built[b["fm1"]] = True
    hostrun.OUT = os.path.join(OUT, "midi")
    notes, t = [], 1000
    for n in (48, 60, 72):
        for v in (1, 40, 96, 127):
            notes += [f"{t} midi 0x90 {n} {v}", f"{t + 400} midi 0x80 {n} 0"]
            t += 500
    end = t

    def one(arg):
        (tn, td), (kv, kd) = arg
        # the velocity page (the last VOICE page, VOICE 3/3 on the electric piano): HOME to FRODHES, SELECT three
        # times; TOUCH is K2 (encoder 3), KEY VEL K3 (encoder 4)
        ls = ["60 btn 8", "80 turn 0 1", "90 turn 0 1", "100 turn 0 1"]
        ls += [f"{120 + 10 * i} turn 3 {1 if td > 0 else -1}" for i in range(abs(td))]
        ls += ["200 dump"] + ([f"300 turn 4 {kd}"] if kd else []) + ["700 dump"] + notes
        r = hostrun.run(f"midi-{tn}-{kv}", ls, end + 600, binary=b["fm1"], check=False)
        a = np.stack([r.L, r.R])[:, int(0.9 * FS):int(end / 1000 * FS)]
        return tn, kv, r.state(200).get("card1"), r.state(700).get("card2"), hashlib.sha1(a.tobytes()).hexdigest()
    combos = [(t_, k_) for t_ in TOUCHES for k_ in KEYVELS]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(one, combos))
    ref = res[[i for i, c in enumerate(combos) if c[0][0] == "MEDIUM" and c[1][0] == 96][0]][4]
    out = []
    for tn, kv, st, sk, h in res:
        set_ok = st == tn and sk == str(kv)
        same = h == ref
        out.append(row(set_ok and same, "midi", f"TOUCH {tn:6s} KEY VEL {kv:3d}",
                       ("MIDI v1/40/96/127 byte-identical to TOUCH MEDIUM KEY VEL 96" if same else
                        "MIDI notes render DIFFERENTLY: the key path touches MIDI velocity")
                       + ("" if set_ok else f"; the setting did not take (TOUCH {st}, KEY VEL {sk})")))
    return out


# ---------------------------------------------------------------- pristine: the fast release diff
def check_pristine(b):
    import release_diff as rd
    cand = rd.measure_all(b, "fast", "quick-fast")
    base = json.load(open(rd.BASE_FAST))["cases"]
    fails, moves, stale = rd.judge(cand, base, rd.load_expected(rd.EXPECTED))
    out = []
    types = [t for t, _, _ in rd.TYPES]
    for t in types:
        f = [x for x in fails if x[0].split("/")[1] == t]
        ceil = [x for x in f if x[6].startswith("CEILING")]
        if not f:
            out.append(row(True, "pristine", t, "every ceiling and move within limits (FM-1 + HQ, FXOFF/RAW/FX, A/B)"))
            continue
        w = max(ceil, key=lambda x: x[3] - x[5]) if ceil else f[0]
        d = (f"{len(ceil)} over a ceiling, worst {w[0].split('/', 1)[1]}/{w[1]} {w[2]} {w[3]:.1f} (limit {w[5]:.1f}): "
             f"{w[6][9:]}" if ceil else f"{len(f)} unlisted move(s), e.g. {w[0]}/{w[1]} {w[2]}: {w[6]}")
        clk = [x for x in ceil if x[2] in ("clk", "clk_off")]
        out.append(row(False, "pristine", t, d + (f"; key-up / body clicks {len(clk)}" if clk else "")))
    return out


# ---------------------------------------------------------------- crackle: the worst case through the detectors
def check_crackle(b):
    args = ["--host", "--quick", "--worst-s", "20", "--recipe-s", "10", "--no-usb-s", "0"]
    wrap = ("import sys; sys.path[:0] = [%r, %r]; import hostrun; B = %r; hostrun._built[B] = True; o = hostrun.run; "
            "hostrun.run = lambda *a, **k: o(*a, **dict(k, binary=k.get('binary', B))); import hil_crackle; "
            "sys.exit(hil_crackle.main(%r))" % (os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests", "py"),
                                                 b["fm1"], args))
    code, txt = sh([PY, "-c", wrap], timeout=900)       # (hil_crackle's host takes on the given FM-1 build)
    out = []
    for eng in ("rhodes", "vibes", "marimba"):
        m = re.search(rf"^{eng}\s+(ok|FAIL|\S+)\s+(.*)$", txt, re.M)
        out.append(row(bool(m) and m.group(1) == "ok", "crackle", eng, (m.group(2)[:110] if m else txt[-150:])))
    return out


# ---------------------------------------------------------------- the Move (K27): its HQ build, the same limits
def check_move_bypass(b):
    code, txt = sh([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/py/test_move_bypass_comp.py"])
    last = [l for l in txt.splitlines() if "passed" in l or "failed" in l or "error" in l]
    fails = re.findall(r"^FAILED (\S+)", txt, re.M)
    return [row(code == 0, "move-byp", "FX off keeps the comp, = FX on", (last[-1] if last else txt[-120:])
                + ("" if not fails else "; " + ", ".join(f.split("::")[-1] for f in fails)))]


def check_move_ui(b):
    code, txt = sh([PY, "tools/gen_move_module.py", "--check"])
    out = [row(code == 0, "move-ui", "Gate U (move/module current)", "current" if code == 0 else txt.strip()[-120:])]
    code, txt = sh(["node", os.path.join("move", "ui", "test_chain.mjs")])
    fails = re.findall(r"^FAIL (.*)$", txt, re.M)
    out.append(row(code == 0, "move-ui", "the screen (test_chain.mjs)", "ok: no pad or step LED, the arrows only"
                   if code == 0 else f"{len(fails)} failed: " + "; ".join(fails[:2])[:120]))
    return out


CHECKS = {"dry": check_dry, "keys": check_keys, "fx": check_fx, "bypass": check_bypass, "level": check_level,
          "midi": check_midi, "pristine": check_pristine, "crackle": check_crackle,
          "move-dry": lambda b: check_dry(b, "hq", "move-dry"), "move-fx": lambda b: check_fx(b, "hq", "move-fx"),
          "move-byp": check_move_bypass, "move-lvl": lambda b: check_level(b, "hq", "move-lvl"),
          "move-ui": check_move_ui}


def host_bins():
    """this tree's FM-1 and HQ host builds (make's rules)"""
    code, txt = sh(["make", "-s", "build/host/frodhes", "build/host-hq/frodhes"])
    if code:
        raise SystemExit("quick: the host builds failed\n" + txt[-2000:])
    return {"fm1": os.path.join(ROOT, "build", "host", "frodhes"), "hq": os.path.join(ROOT, "build", "host-hq", "frodhes")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fm1")
    ap.add_argument("--hq")
    ap.add_argument("--only", help="a comma list of: " + ", ".join(CHECKS))
    ap.add_argument("--tree", help="with --fm1/--hq of another tree: that tree (test_bypass_level compiles from it)")
    ap.add_argument("--save-level", action="store_true")
    ap.add_argument("--save-new-level", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    if a.save_level or a.save_new_level:
        if not a.fm1 and not a.hq:
            raise SystemExit("--save-level / --save-new-level --fm1 BIN | --hq BIN")
        import release_diff as rd
        if a.save_level:                                # (the last release's build, its own presets' names)
            tree = os.path.abspath(a.tree) if a.tree else rd.last_tree()
            for binary, base, sub in ((a.fm1, LEVEL_BASE, "level"), (a.hq, LEVEL_BASE_HQ, "move-lvl")):
                if binary:
                    json.dump({"release": rd.LAST, "cases": level_all(os.path.abspath(binary), sub, tree)},
                              open(base, "w"), indent=1, sort_keys=True)
                    print(base)
            return 0
        for binary, base, ref, sub in ((a.fm1, LEVEL_NEW, LEVEL_BASE, "level"), (a.hq, LEVEL_NEW_HQ, LEVEL_BASE_HQ, "move-lvl")):
            if binary:                                  # (this tree's presets 0.9.9 did not have, on this build)
                old = json.load(open(ref))["cases"]
                new = [n for n in preset_names() if n not in old]
                rev = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short=8", "HEAD"], capture_output=True,
                                     text=True).stdout.strip()
                json.dump({"measured_on": rev, "cases": level_all(os.path.abspath(binary), sub, ROOT, set(new))},
                          open(base, "w"), indent=1, sort_keys=True)
                print(base, ", ".join(new))
        return 0
    b = {"fm1": os.path.abspath(a.fm1), "hq": os.path.abspath(a.hq)} if a.fm1 and a.hq else host_bins()
    b["tree"] = os.path.abspath(a.tree) if a.tree else ROOT
    names = a.only.split(",") if a.only else list(CHECKS)
    rows, times = [], {}

    def run(n):
        s = time.time()
        try:
            r = CHECKS[n](b)
        except Exception as e:                          # (a check that cannot run is a FAIL, never a skip)
            r = [row(False, n, "error", f"{type(e).__name__}: {str(e)[:150]}")]
        times[n] = time.time() - s
        return r
    if {"fx", "move-fx"} & set(names):
        stage_fx(b)
    with ThreadPoolExecutor(len(names)) as ex:
        for r in ex.map(run, names):
            rows += r
    lines = [f"{'PASS' if ok else 'FAIL'}  {area:8s} {name:28s} {d}" for ok, area, name, d in rows]
    bad = [r for r in rows if not r[0]]
    lines.append(f"make quick: {'PASS' if not bad else 'FAIL'}: {len(rows) - len(bad)} of {len(rows)} lines pass "
                 f"({time.time() - t0:.0f} s; " + ", ".join(f"{n} {times[n]:.0f} s" for n in names) + ")")
    text = "\n".join(lines)
    open(os.path.join(OUT, "report.txt"), "w").write(text + "\n")
    print(text)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
