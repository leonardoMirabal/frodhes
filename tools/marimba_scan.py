#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The MARIMBA scan profile and the musicality gate (MARIMBA PLAN §10; the shared harness does not exist, so this is
the profile and tools/bar_scan.py --type 5 runs the generic S-checks, as AGENTS.md §5 says).

  tools/marimba_scan.py --smoke     the plumbing: the stub check, the selector, three stress renders (seconds; pytest)
  tools/marimba_scan.py --quick     the marimba's own stress cases (rolls, gate, MUTE, steals, the swap) + M1..M7
  tools/marimba_scan.py --full      the same, every key, and tools/bar_scan.py --type 5 --full's S1..S13 merged in
  --only M1,M5,stress   only these parts      --keep-wav   keep the renders (build/marimba_scan/wav)
  -> build/marimba_scan/report.txt, scan.json

MARIMBA is TYPE 5, selected by CC 79 96..127 (this profile sends 127; frodhes.c midi_cc) or PC 14 / 15 (F15 MARIMBA,
F16 MRMB SOFT). Every render is the host build's (tests/host.c), RAW and NOISE 0 unless the case says otherwise.

While the tables are the sync-1 STUB (firmware/gen/bars_tables.h: every per-type row of type 1 equal to type 0's,
i.e. MARIMBA plays VIBES), every MARIMBA-specific number is reported as AWAIT (awaiting sync 2 real tables) instead of
FAIL: the thresholds are the plan's and are never relaxed; only the verdict waits. The plumbing (the selection, the
renders, the report) is judged now. MARIMBA_STUB=0 / 1 overrides the detection.

The musicality gate (PLAN §10.3), host part (D = the FM-1 part: tools/hil_scan.py --marimba):
  M1 even loudness   v100 LUFS 0..1 s per key: neighbour step <= 1.0 dB in C2..C7; off the +-3-key median <= 1.0 dB
                     (<= 2 dB outside C2..C7)
  M2 even timbre     the attack centroid (0..0.3 s, MED = MALLET 42, v100): neighbour change <= 10 % except at the
                     documented knees (listed with their size); V2/V1 and V3/V1 at the onset: no step > 4 dB
  M3 no key out      peak / RMS (50..300 ms) at v127 within 3 dB of the neighbours' median
  M4 velocity        per key LUFS monotone (no drop > 0.3 dB between anchors), second difference <= 1.5 dB, range
                     v8 -> v127 26..32 dB; the attack centroid monotone (no fall > 3 %) and >= 1.4x v40 -> v117 on C4
                     MED; V2/V1 up >= 8 dB v40 -> v127 on C3 MED
  M5 rolls           LUFS over sliding 0.5 s windows (hop 0.1 s) after 0.5 s varies <= 1.5 LU; the limiter's gain
                     reduction <= 3 dB at v100 (reported at v117 / v127); clicks 0; a two-hand roll's notes within 2 LU
  M6 soft mallets    SOFT at v117 / v127: C3 V2/V1 <= -20 dB, V3/V1 <= -40 dB; C4 V3/V1 <= -35 dB; MED at v127 >= 6 dB
                     darker than HARD at v100 (the attack centroid's ratio in dB, 20 log10: our reading of the plan)
  M7 register        C2: the f0 band's bloom peak 150..300 ms after the strike (C3 120 ms, C4 60 ms +-30 %); <= 15 ms
                     from C6 up; C2-E2 with SOFT / MED: V2 <= -10 dB re V1 at the onset (5..100 ms); TUBE 64 vs 0: V1
                     T60 x1.3 or more on C2 / C3; C6-C7: TUBE 0 vs 127 V1 T60 within 10 %, level +1..+3 LU
  M8 listening       the packs (the user): not a host check
The stress cases (PLAN §10.1 'stress', §10.2): A1 clicks 0 (tools/bar_scan.py click_count), H1 sat28 0, A3 exact zero
by tail_bound_s 3 s after the last event (RAW), A5 the gate's fade at <= -70 dBFS, S4 the last non-zero sample <= 1 LSB.
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402

ROOT = bm.ROOT
FS = bm.FS
OUT = os.path.join(ROOT, "build", "marimba_scan", os.environ.get("PYTEST_XDIST_WORKER", ""))
HDR = os.path.join(ROOT, "firmware", "gen", "bars_tables.h")
STUB_REASON = "awaiting sync 2 real tables"
P4_REASON = ("P4 open: the real tables are in (sync 2); the C engine's MARIMBA numbers of the scan are lane A's P4 "
             "(MARIMBA_P4=1 runs them as plain tests)")


def p4_open():
    """the MARIMBA-specific scan numbers on the real tables await P4 (xfail, visible); MARIMBA_P4=1: gated"""
    return os.environ.get("MARIMBA_P4") != "1"


def await_reason():
    return STUB_REASON if stub_detected() else P4_REASON
TYPE = 5
CC79 = 127                                  # CC 79 96..127 = MARIMBA (bar_measure.type_cc(5))
AUTH = (36, 96)                             # C2..C7, the authentic range (PLAN §2.5)
VELS = (8, 24, 40, 64, 88, 100, 117, 127)   # the velocity anchors
SOFT, MED, DEF, HARD, RUBBER = 0, 42, 64, 85, 127   # MALLET detents (KN_FAM; the card: SOFT MED (64) HARD RUBBER)
KNEES = {84: "V3 untuned by the FE from key 84 (PLAN §2.1)"}   # documented M2 knees (+ the spec's 'knees', if any)
LIM_DBFS = 20 * math.log10(24000 / 32767)   # fx.c LIM_T: the limiter's ceiling
LK = 32                                     # fx.c: the look-ahead limiter's sub-block (the master's delay, samples)

# the profile (PLAN §10.1), with today's selector (CC 79 127) and the PCs of F15 / F16
PROFILE = dict(
    name="MARIMBA", file="bars.c",
    types=[dict(type=TYPE, name="MARIMBA", select=[("cc", 79, CC79)], pc=14, pc_soft=15)],
    keys=dict(range=(21, 108), authentic=AUTH, low=36, mid=60, high=84),
    offset_db=2.5,              # provisional (VIBES's): the P5 jack session writes the measured number (marimba.json calib)
    linear=True, attack_guard_ms=(3, 17), tail_bound_s=3.0,
    spec="docs/model/data/marimba_spec.json",
    cards=[dict(name="STRIKE", param="TONE", cc=28, enc=2, page="VOICE", vals=(0, 32, 64, 96, 127), keys=(48, 60, 84), job="timbre", thr=3.0),
           dict(name="MALLET", param="BARK", cc=29, enc=3, page="VOICE", vals=(0, 42, 64, 85, 127), keys=(48, 60, 84), job="timbre", thr=3.0, pairwise=True, vels=(100, 117, 127)),
           dict(name="TUBE", param="BELL", cc=30, enc=4, page="VOICE", vals=(0, 64, 127), keys=(36, 48, 60, 84), job="level+t60", thr=3.0, level_is_job=True, t60_mult=1.3, t60_keys=(36, 48)),
           dict(name="WEIGHT", param="HAMMER", cc=31, enc=5, page="VOICE", vals=(0, 64, 127), keys=(48, 60, 84), job="level+centroid", thr=2.5, centroid=1.05),
           dict(name="RING", param="DECAY", cc=102, enc=2, page="DECAY", vals=(0, 64, 127), keys=(36, 60, 84), job="t60_multiple", thr=2.0),
           dict(name="MUTE", param="RELEASE", cc=103, enc=3, page="DECAY", vals=(0, 64, 127), keys=(48, 72), job="release_t60", thr=3.0, zero_means_none=True),
           dict(name="NOISE", param="NOISE", cc=104, enc=4, page="DECAY", vals=(0, 64, 127), keys=(60,), job="burst_level", thr=6.0)],
    panel_only=[dict(name="TUNE", vals=(-50, 0, 50), keys=(69,), job="cents", seq="tune_lines")],
    hooks=[dict(name="MOTOR inert", on_engine="inert", setup=[("cc", 47, 127), ("cc", 48, 127)])],
    skips=[dict(check="B5 motor", reason="no motor on this type: CC 47-51 inert (tested)")],
    stereo_engine=False,
    long_tail=("C2 RING 127 TUBE 127 pedal", [("cc", 102, 127), ("cc", 30, 127), ("cc", 64, 127), ("on", 36, 100), ("off", 36, 0.3)]),
)
EVERY_FX = dict(DRIVE=30, CMP_SW=127, CMP_AMT=64, TW_SW=64, TW_DEPTH=50, CP_SW=64, CP_DEPTH=50, PH_SW=127,
                PH_DEPTH=60, DL_SW=64, DL_DEPTH=40, REV=40)       # hil_scan ALL_ON as CC values (TW / DL: mode 1)
KEEP = False


# ---------------------------------------------------------------- the stub
def _rows(src, name):
    """the top-level rows of `NAME[BAR_NTYPE]... = { ... };` (whitespace-free text each), or None"""
    m = re.search(r"\b%s\[BAR_NTYPE\][^=]*=\s*\{" % re.escape(name), src)
    if not m:
        return None
    i, depth, start, rows = m.end(), 1, m.end(), []
    while depth and i < len(src):
        c = src[i]
        if c == "{":
            if depth == 1:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 1:
                rows.append(re.sub(r"\s+", "", src[start:i + 1]))
        i += 1
    return rows


PER_TYPE = ("BAR_KEYS", "BAR_PRATE", "BAR_PAMP", "BAR_BODY", "BAR_TYPE", "KN_MASS", "KN_MASSI", "KN_TUBE", "KN_NZ")


def stub_detected(path=HDR):
    """True while MARIMBA's tables are the sync-1 stub: no type-1 rows, or every per-type array's row 1 equal to row 0
    (the header's 'Types: 0 VIBES, 1 STUB (a copy of VIBES)'). MARIMBA_STUB=0 / 1 overrides."""
    env = os.environ.get("MARIMBA_STUB")
    if env in ("0", "1"):
        return env == "1"
    src = open(path).read()
    n = re.search(r"#define BAR_NTYPE (\d+)", src)
    if not n or int(n.group(1)) < 2:
        return True
    seen = 0
    for name in PER_TYPE:
        r = _rows(src, name)
        if r is None or len(r) < 2:
            continue
        seen += 1
        if r[1] != r[0]:
            return False
    return seen > 0


# ---------------------------------------------------------------- the spec (partials' frequencies)
_SPEC = None


def spec_source():
    p = os.path.join(ROOT, PROFILE["spec"])
    if os.path.exists(p):
        return p
    if stub_detected():
        return os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")   # the stub plays VIBES's tables
    return None


def freqs(n):
    """{mode: Hz} for key n: the marimba spec when lane A has written it, VIBES's under the stub, else 1 : 4 : 9.8 on
    the equal-tempered pitch (PLAN §2.1)"""
    global _SPEC
    if _SPEC is None:
        p = spec_source()
        _SPEC = {k["midi"]: {m["name"]: m["f"] for m in k["modes"]} for k in json.load(open(p))["keys"]} if p else {}
    if n in _SPEC:
        return _SPEC[n]
    f = 440.0 * 2 ** ((n - 69) / 12)
    return dict(V1=f, V2=4.0 * f, V3=9.8 * f)


# ---------------------------------------------------------------- rendering
def base(raw=True, noise=0, mute=None, **cc):
    """MARIMBA (CC 79 127), RAW, NOISE, MUTE (CC 103) and other parameters by CC (bar_measure.CC names)"""
    ev = bm.setup_events(typ=TYPE, raw=raw, noise=noise, **cc)
    if mute is not None:
        ev.append((20, f"midi 0xB0 103 {int(mute)}"))
    return ev


VOICE_PAGE = [(200, "btn 7"), (203, "btn 8"), (206, "turn 0 1"), (209, "turn 0 1")]   # bar_cards.VOICE_PAGE
ENC = dict(TONE=2, BARK=3, BELL=4, HAMMER=5)


def panel(card_vals):
    """the VOICE page's cards turned from 64 to their values on the panel, as a player does (bar_cards.events)"""
    ev = list(VOICE_PAGE)
    for i, (c, v) in enumerate(sorted(card_vals.items())):
        if v != 64:
            ev.append((220 + 10 * i, f"turn {ENC[c]} {int(v) - 64}"))
    return ev


def render(tag, events, end_ms, env=None):
    """(mono float, stereo, log); the wav is removed unless --keep-wav (build/ stays small)"""
    d = os.path.join(OUT, "wav")
    x, log = bm.render(tag, events, end_ms, d, env)
    if not KEEP:
        for e in (".wav", ".txt"):
            try:
                os.remove(os.path.join(d, tag + e))
            except OSError:
                pass
    return bm.mono(x), x, log


# ---------------------------------------------------------------- measures
def onset(m, t_ms):
    a = int(t_ms * FS / 1000)
    i = np.flatnonzero(np.abs(m[a:a + 4096]) > 0.5)
    return a + (int(i[0]) if len(i) else 0)


def peak_db(seg, f, rel=0.03):
    nf = 1 << max(15, int(math.ceil(math.log2(max(len(seg), 2)))) + 2)
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    sel = (fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)
    return 10 * math.log10(float(S[sel].sum()) + 1e-30)


def third_oct(seg):
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    fr = np.fft.rfftfreq(len(seg), 1 / FS)
    return np.array([10 * math.log10(float(S[(fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))].sum()) + 1e-30)
                     for c in [50 * 2 ** (i / 3) for i in range(26)]])


def dist(b1, b2, floor=60.0):
    """bar_cards.dist: the level-aligned spectral distance (dB RMS) of two 1/3-octave spectra"""
    b1, b2 = np.asarray(b1), np.asarray(b2)
    r1 = b1 - 10 * np.log10(np.sum(10 ** (b1 / 10)))
    r2 = b2 - 10 * np.log10(np.sum(10 ** (b2 / 10)))
    fl = max(r1.max(), r2.max()) - floor
    use = (r1 > fl) | (r2 > fl)
    return float(np.sqrt(np.mean((np.maximum(r1, fl) - np.maximum(r2, fl))[use] ** 2)))


def rms_db(x):
    x = np.asarray(x, float)
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12)) if x.size else -240.0


def feats(m, s0, n):
    """one strike from sample s0 (bar_cards.features + the crest): lufs 0..1 s, cen_a 0..0.3 s, cen 0.32..0.82 s, v2 /
    v3 re V1 over 5..300 ms (v2o / v3o over 5..100 ms: 'at the onset'), peak dBFS, crest (peak / RMS 50..300 ms),
    bands 0..0.5 s"""
    f = freqs(n)
    att, ons = m[s0 + int(0.005 * FS):s0 + int(0.3 * FS)], m[s0 + int(0.005 * FS):s0 + int(0.1 * FS)]
    l1, l1o = peak_db(att, f["V1"]), peak_db(ons, f["V1"])
    w = m[s0 + int(0.05 * FS):s0 + int(0.3 * FS)]
    pk = float(np.abs(m[s0:s0 + int(1.0 * FS)]).max())
    return dict(lufs=float(bm.lufs(m[s0:s0 + FS])), cen_a=bm.centroid(m[s0:s0 + int(0.3 * FS)]),
                cen=bm.centroid(m[s0 + int(0.32 * FS):s0 + int(0.82 * FS)]),
                v2=peak_db(att, f["V2"]) - l1, v3=peak_db(att, f["V3"]) - l1,
                v2o=peak_db(ons, f["V2"]) - l1o, v3o=peak_db(ons, f["V3"]) - l1o,
                peak=float(20 * math.log10(pk / 32767 + 1e-12)),
                crest=float(20 * math.log10((np.abs(w).max() + 1e-9) / (np.sqrt(np.mean(w * w)) + 1e-9))),
                bands=third_oct(m[s0:s0 + int(0.5 * FS)]).tolist())


def strike_set(tag, setup, keys, vels, gap=2500, hold=1200, t0=400):
    """{(key, vel): feats}: one strike per (velocity, key), gap ms apart, held hold ms"""
    ev, t, st = list(setup), t0, []
    for v in vels:
        for n in keys:
            ev += bm.note(t, n, v, hold)
            st.append((t, n, v))
            t += gap
    m, _, _ = render(tag, ev, t + 200)
    return {(n, v): feats(m, onset(m, tt), n) for tt, n, v in st}


def partial_trace(x, f, hop=441):
    n = 8192 if f < 200 else 4096
    w = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = np.abs(fr - f) <= max(0.06 * f, 1.5 * fr[1])
    idx = np.arange(0, max(len(x) - n, 1), hop)
    e = np.array([20 * np.log10(np.abs(np.fft.rfft(x[i:i + n] * w))[sel].max() / (n / 4) / 32767 + 1e-12) for i in idx])
    return e, (idx + n / 2) / FS


def t60(x, f, hi=5.0, lo=35.0):
    """the partial's T60 (s) by a 30 dB fit, 5..35 dB under its peak (PLAN §10.2 B1: the 60 dB fit runs into the floor)"""
    e, t = partial_trace(x, f)
    ip = int(np.argmax(e))
    a = np.flatnonzero((np.arange(len(e)) > ip) & (e < e[ip] - hi))
    b = np.flatnonzero((np.arange(len(e)) > ip) & (e < e[ip] - lo))
    if not len(a):
        return float("inf")
    i0, i1 = a[0], (b[0] if len(b) else len(e) - 1)
    if i1 - i0 < 3:
        return float("nan")
    s = np.polyfit(t[i0:i1], e[i0:i1], 1)[0]
    return float(-60.0 / s) if s < 0 else float("inf")


def bloom_ms(m, s0, n, dur=0.8):
    """the f0 band's envelope peak after the strike (ms): a zero-phase band-pass (+-10 %) and its Hilbert envelope"""
    from scipy.signal import butter, hilbert, sosfiltfilt
    f = freqs(n)["V1"]
    x = m[s0:s0 + int(dur * FS)]
    y = sosfiltfilt(butter(2, [0.9 * f, 1.1 * f], "bandpass", fs=FS, output="sos"), x)
    e = np.abs(hilbert(y))
    k = max(1, int(0.002 * FS))
    e = np.convolve(e, np.ones(k) / k, "same")
    return float(1000 * np.argmax(e) / FS)


def release_rate(x, f, t_off):
    """the partial's decay (dB/s) 30..300 ms after the key-up at t_off (s)"""
    e, t = partial_trace(x, f, hop=220)
    k = (t >= t_off + 0.03) & (t <= t_off + 0.3) & (e > e.max() - 100)
    return float(-np.polyfit(t[k], e[k], 1)[0])


def sliding_lu(m, t0, t1, win=0.5, hop=0.1):
    """LUFS over sliding windows (PLAN M5: 0.5 s, hop 0.1 s) between t0 and t1 (s)"""
    out, t = [], t0
    while t + win <= t1 + 1e-9:
        out.append(float(bm.lufs(m[int(t * FS):int((t + win) * FS)])))
        t += hop
    return np.array(out)


def peak_env(m, w=441):
    n = len(m) // w
    return np.abs(m[:n * w]).reshape(n, w).max(axis=1)


def gain_reduction(master, raw, ref_master, ref_raw):
    """the limiter's work (dB): the master's 10 ms peaks re the RAW render's, against the same ratio of a quiet
    render of the same pattern (v24: the limiter idle, so the master's other fixed gains cancel); the largest drop
    over the windows where RAW is above -40 dBFS"""
    def ratio(a, b):
        pa, pb = peak_env(a[LK:]), peak_env(b)        # (the master runs one limiter sub-block late: fx.c)
        n = min(len(pa), len(pb))
        ok = pb[:n] > 32767 * 10 ** (-40 / 20)
        return 20 * np.log10((pa[:n][ok] + 1e-9) / (pb[:n][ok] + 1e-9))
    r0 = ratio(ref_master, ref_raw)
    r = ratio(master, raw)
    if not len(r0) or not len(r):
        return float("nan")
    return float(max(0.0, np.median(r0) - r.min()))


def last_nonzero(m):
    nz = np.flatnonzero(m != 0)
    return int(nz[-1]) if len(nz) else -1


# ---------------------------------------------------------------- the stress cases (PLAN §10.1 'stress')
def roll_ev(keys, rate, vel, t0, dur):
    """a roll: strikes alternating over keys at rate per second, each let go halfway to the next"""
    ev, k, dt = [], 0, 1000.0 / rate
    while k * dt < dur * 1000:
        t = int(t0 + k * dt)
        ev += bm.note(t, keys[k % len(keys)], vel, int(dt / 2))
        k += 1
    return ev


ROLLS = [("roll 8 Hz C4 v100", (60,), 8, 100, {}), ("roll 12 Hz C4 v117", (60,), 12, 117, {}),
         ("roll 12 Hz C4 v100", (60,), 12, 100, {}),
         ("roll 12 Hz C2 v127 RUBBER", (36,), 12, 127, dict(BARK=127)),
         ("two-hand roll C2+G2 12 Hz", (36, 43), 12, 117, {})]


def roll_case(name, keys, rate, vel, cards, mute=0, dur=4.0):
    """the roll RAW (clicks, sat28, the pumping, the exact end) and through the master with the effects dry, plus the
    same pattern at v24 both ways (the limiter's reference): rows"""
    T0 = 400
    pat = roll_ev(keys, rate, vel, T0, dur)
    end = int(T0 + dur * 1000 + 3200)
    raw, _, log = render(bm_tag(name, "raw"), base(raw=True, mute=mute, **cards) + pat, end)
    rows = []
    a, b = click_count(raw, [t for t, _ in pat])
    rows.append(("A1", name, a + b == 0, a + b, f"clicks {a}, HF bursts {b}", False))
    s28 = bm.counters(log)["sat28 hits"]
    rows.append(("H1", name, not s28, s28 or 0, f"sat28 hits {'n/a' if s28 is None else s28}", False))
    z = last_nonzero(raw)
    lim = (T0 + dur * 1000) / 1000 + PROFILE["tail_bound_s"]
    rows.append(("A3", name, z < lim * FS, z / FS, f"exact zero from {z / FS:.2f} s (bound {lim:.2f} s)", True))
    lu = sliding_lu(raw, T0 / 1000 + 0.5, T0 / 1000 + dur)
    pump = float(lu.max() - lu.min()) if len(lu) else float("nan")
    rows.append(("M5", name + " pumping", pump <= 1.5, pump, f"sliding 0.5 s LUFS span {pump:.2f} LU (<= 1.5)", True))
    if mute == 0:
        dry = dict(REV=0, DRIVE=0)
        ms, _, _ = render(bm_tag(name, "master"), base(raw=False, mute=mute, **dry, **cards) + pat, end)
        q = roll_ev(keys, rate, 24, T0, dur)
        qr, _, _ = render(bm_tag(name, "qraw"), base(raw=True, mute=mute, **cards) + q, end)
        qm, _, _ = render(bm_tag(name, "qmaster"), base(raw=False, mute=mute, **dry, **cards) + q, end)
        gr = gain_reduction(ms, raw, qm, qr)
        judged = vel <= 100
        rows.append(("M5", name + " limiter", (gr <= 3.0) if judged else None, gr,
                     f"gain reduction {gr:.2f} dB" + (" (<= 3 at v100)" if judged else " (reported)"), True))
    if len(keys) == 2:                                   # each hand's note alone, the same strikes
        lv = []
        for k in keys:
            alone = [e for e in pat if e[1].split()[2] == str(k)]
            x, _, _ = render(bm_tag(name, f"alone{k}"), base(raw=True, mute=mute, **cards) + alone, end)
            lv.append(float(bm.lufs(x[int((T0 / 1000 + 0.5) * FS):int((T0 / 1000 + dur) * FS)])))
        d = abs(lv[0] - lv[1])
        rows.append(("M5", name + " balance", d <= 2.0, d, f"the two notes {lv[0]:.1f} / {lv[1]:.1f} LUFS (<= 2 LU apart)", True))
    return rows


def bm_tag(*a):
    return "mrb_" + re.sub(r"[^A-Za-z0-9]+", "_", "_".join(str(x) for x in a)).strip("_")


def click_count(m, events_ms):
    import bar_scan
    return bar_scan.click_count(m, events_ms)


def gate_case(name, key, vel, rev=None):
    """A5: the silence gate's fade (the master, defaults; MUTE 0: the note rings out) happens at <= -70 dBFS: the
    peak in the 46 ms before the last non-zero sample"""
    cc = {} if rev is None else dict(REV=rev)
    m, _, _ = render(bm_tag(name), base(raw=False, noise=None, mute=0, **cc) + bm.note(300, key, vel, 400), 9000)
    z = last_nonzero(m)
    if z < 0:
        return [("A5", name, False, None, "silent", False)]
    pk = 20 * math.log10(np.abs(m[max(0, z - int(0.046 * FS)):z + 1]).max() / 32767 + 1e-12)
    return [("A5", name, pk <= -70.0, pk, f"fade window peak {pk:.1f} dBFS (<= -70), zero from {z / FS:.2f} s", True)]


def steal_cases():
    rows = []
    ev = base() + [(300, f"midi 0x90 {n} 127") for n in range(48, 80, 2)] + [(1300, f"midi 0x80 {n} 0") for n in range(48, 80, 2)]
    m, _, log = render(bm_tag("16 in one block"), ev, 6000)
    rows += steal_rows("16 notes in one block", m, log, [300, 1300], 6000)
    ev = base()
    for k in range(12):
        t = 300 + 400 * k
        ch = [(48, 55, 60, 64), (50, 57, 62, 65), (43, 50, 59, 62)][k % 3]
        for n in ch:
            ev += bm.note(t, n + (k % 2) * 12, 110, 380)
    m, _, log = render(bm_tag("12 chords"), ev, 9500)
    rows += steal_rows("12 chords at 0.4 s", m, log, [t for t, _ in ev], 9500)
    return rows


def steal_rows(name, m, log, evs, end_ms):
    a, b = click_count(m, evs)
    s28 = bm.counters(log)["sat28 hits"]
    nz = np.flatnonzero(m != 0)
    last = float(np.abs(m[nz[-1]])) if len(nz) else 0.0
    tail = m[-int(0.1 * FS):]
    return [("A1", name, a + b == 0, a + b, f"clicks {a}, HF bursts {b}", False),
            ("H1", name, not s28, s28 or 0, f"sat28 hits {'n/a' if s28 is None else s28}", False),
            ("S4", name, last <= 1 and not np.any(tail), last, f"last non-zero |x| {last:.0f} LSB, exact zero at the end", False)]


def mute_roll_case():
    """MUTE 127 on a 12 Hz roll: clicks 0 (the hand lands on every key-up), and the stop after the last key-up >= 3x
    faster than MUTE 0's"""
    name = "MUTE 127 on a roll"
    pat = roll_ev((60,), 12, 100, 400, 3.0)
    off = max(t for t, s in pat if s.startswith("midi 0x80")) / 1000
    r = {}
    rows = []
    for mu in (0, 127):
        m, _, log = render(bm_tag(name, mu), base(mute=mu) + pat, 6000)
        r[mu] = release_rate(m, freqs(60)["V1"], off)
        if mu == 127:
            a, b = click_count(m, [t for t, _ in pat])
            rows.append(("A1", name, a + b == 0, a + b, f"clicks {a}, HF bursts {b}", False))
    q = r[127] / max(r[0], 1e-9)
    rows.append(("B2", name + " stop", q >= 3.0, q, f"after the last key-up {r[0]:.0f} / {r[127]:.0f} dB/s (x{q:.1f}, >= 3)", True))
    return rows


def swap_case():
    """VIBES -> MARIMBA -> ST77 while 8 ring (pedal): no clicks outside the fade, ev_drop 0, the next note sounds,
    exact zero at the end"""
    name = "swap VIBES->MARIMBA->ST77"
    ev = [(20, "midi 0xB0 119 127"), (20, "midi 0xB0 79 0"), (20, "midi 0xB0 47 0"), (100, "midi 0xB0 64 127")]
    ev += [(200 + 20 * i, f"midi 0x90 {n} 100") for i, n in enumerate((48, 52, 55, 59, 60, 64, 67, 71))]
    # (the next notes are keys not held; a key still down across an engine change, struck again, is
    # tests/py/test_race_fuzz.py test_type_change_held_repress's subject: silent in 0.9.3, fixed in notes.c)
    ev += [(1000, f"midi 0xB0 79 {CC79}")] + bm.note(1100, 62, 100, 600)
    ev += [(2000, "midi 0xB0 80 8")] + bm.note(2100, 65, 100, 600)
    ev += [(3000, "midi 0xB0 64 0")] + [(3000, f"midi 0x80 {n} 0") for n in (48, 52, 55, 59, 60, 64, 67, 71)]
    m, _, log = render(bm_tag(name), ev, 9000)
    a, b = click_count(m, [t for t, _ in ev])
    c = bm.counters(log)
    after = rms_db(m[int(1.15 * FS):int(1.5 * FS)]), rms_db(m[int(2.15 * FS):int(2.5 * FS)])
    return [("B7", name + " clicks", a + b == 0, a + b, f"clicks {a}, HF bursts {b} (outside the 17 ms guards)", False),
            ("B7", name + " ev_drop", not c["ev_drop"], c["ev_drop"] or 0, f"ev_drop {c['ev_drop']}", False),
            ("B7", name + " next note", min(after) > -50, min(after), f"MARIMBA {after[0]:.1f}, ST77 {after[1]:.1f} dBFS (> -50)", False),
            ("B7", name + " end", not np.any(m[-int(0.1 * FS):]), 0, "exact zero at the end", False)]


def ostinato_case():
    """C2-G2 sixteenths at 240 BPM (16 notes a second) for 10 s through every effect: clicks (no HF-burst check
    through the effects), sat28"""
    name = "ostinato C2-G2 every effect"
    pat, k = [], 0
    while k * 62.5 < 10000:
        pat += bm.note(int(400 + k * 62.5), (36, 38, 40, 43)[k % 4], 110, 50)
        k += 1
    m, _, log = render(bm_tag(name), base(raw=False, noise=None, mute=0, **EVERY_FX) + pat, 13000)
    a, _ = click_count(m, [t for t, _ in pat])
    s28 = bm.counters(log)["sat28 hits"]
    fs = int((np.abs(m) >= 32700).sum())
    return [("A1", name, a == 0, a, f"clicks {a}", False),
            ("H1", name, not s28 and not fs, (s28 or 0) + fs, f"sat28 hits {s28}, samples >= 32700: {fs}", False)]


def stress(smoke=False):
    if smoke:
        return roll_case(*ROLLS[0]) + gate_case("gate fade C7 v8", 96, 8) + swap_case()
    rows = []
    for r in ROLLS:
        rows += roll_case(*r)
    rows += gate_case("gate fade C7 v8", 96, 8) + gate_case("gate fade C2 v127", 36, 127)
    rows += gate_case("quiet tail C4 v8 + reverb 127", 60, 8, rev=127)
    rows += mute_roll_case() + steal_cases() + swap_case() + ostinato_case()
    return rows


# ---------------------------------------------------------------- the musicality gate
def keys_for(full):
    return list(range(21, 109)) if full else list(range(30, 103))


def per_key(tag, keys, vel, setup=(), gap=2000, hold=900):
    """{key: feats} for one strike per key"""
    r = strike_set(tag, list(base()) + list(setup), keys, (vel,), gap=gap, hold=hold)
    return {n: r[(n, vel)] for n in keys}


def med_dev(vals, i, w=3):
    nb = [vals[j] for j in range(max(0, i - w), min(len(vals), i + w + 1))]
    return abs(vals[i] - float(np.median(nb)))


def m1(F, keys):
    lv = [F[n]["lufs"] for n in keys]
    ins = [i for i, n in enumerate(keys) if AUTH[0] <= n <= AUTH[1]]
    step = max(abs(lv[i + 1] - lv[i]) for i in ins[:-1])
    dev_in = max(med_dev(lv, i) for i in ins)
    out = [i for i in range(len(keys)) if i not in ins]
    dev_out = max((med_dev(lv, i) for i in out), default=0.0)
    worst = keys[max(ins, key=lambda i: med_dev(lv, i))]
    return [("M1", "neighbour step C2-C7", step <= 1.0, step, f"{step:.2f} dB (<= 1.0)", True),
            ("M1", "off the +-3-key median C2-C7", dev_in <= 1.0, dev_in, f"{dev_in:.2f} dB at key {worst} (<= 1.0)", True),
            ("M1", "off the median outside", dev_out <= 2.0, dev_out, f"{dev_out:.2f} dB (<= 2.0)", True),
            ("M1", "level span C2-C7", None, max(lv[i] for i in ins) - min(lv[i] for i in ins),
             f"{max(lv[i] for i in ins) - min(lv[i] for i in ins):.1f} dB (information)", True)]


def m2(F, keys):
    rows, knees = [], dict(KNEES)
    ins = [n for n in keys if AUTH[0] <= n <= AUTH[1]]
    bad, at_knee = [], []
    for a, b in zip(ins, ins[1:]):
        r = F[b]["cen_a"] / F[a]["cen_a"]
        if abs(r - 1) > 0.10:
            (at_knee if b in knees or a in knees else bad).append(f"{a}->{b} {100 * (r - 1):+.0f} %")
    rows.append(("M2", "attack centroid neighbours", not bad, len(bad), "<= 10 % between neighbours: " +
                 (", ".join(bad[:8]) or "all") + (f"; at documented knees: {', '.join(at_knee)}" if at_knee else ""), True))
    for v in ("v2o", "v3o"):
        st = [(abs(F[b][v] - F[a][v]), a) for a, b in zip(ins, ins[1:]) if max(F[a][v], F[b][v]) > -90]
        w = max(st) if st else (0.0, None)
        rows.append(("M2", f"{v[:2].upper()}/V1 at the onset, smooth", w[0] <= 4.0, w[0], f"largest step {w[0]:.1f} dB at {w[1]} (<= 4)", True))
    return rows


def m3(F, keys):
    cr = [F[n]["crest"] for n in keys]
    dev = [(abs(cr[i] - float(np.median([cr[j] for j in range(max(0, i - 3), min(len(cr), i + 4)) if j != i]))), keys[i])
           for i in range(len(keys))]
    w = max(dev)
    return [("M3", "peak / RMS at v127 vs neighbours", w[0] <= 3.0, w[0], f"{w[0]:.2f} dB at key {w[1]} (<= 3)", True)]


def m4(keys=(36, 48, 60, 72, 84, 96)):
    rows = []
    for n in keys:
        r = strike_set(bm_tag("M4", n), base(), (n,), VELS, gap=2000, hold=900)
        lv = [r[(n, v)]["lufs"] for v in VELS]
        drop = min(np.diff(lv))
        d2 = float(np.abs(np.diff(lv, 2)).max())
        rng = lv[-1] - lv[0]
        cen = [r[(n, v)]["cen_a"] for v in VELS]
        fall = min(c1 / c0 - 1 for c0, c1 in zip(cen, cen[1:]))
        rows += [("M4", f"key {n} monotone", drop >= -0.3, drop, f"smallest step {drop:+.2f} dB (no drop > 0.3)", True),
                 ("M4", f"key {n} smooth", d2 <= 1.5, d2, f"second difference {d2:.2f} dB (<= 1.5)", True),
                 ("M4", f"key {n} range v8-v127", 26 <= rng <= 32, rng, f"{rng:.1f} dB (26..32)", True),
                 ("M4", f"key {n} centroid monotone", fall >= -0.03, fall, f"largest fall {100 * fall:+.1f} % (no fall > 3 %)", True)]
    r = strike_set(bm_tag("M4 MED"), base(BARK=MED), (48, 60), (40, 117, 127), gap=2000, hold=900)
    q = r[(60, 117)]["cen_a"] / r[(60, 40)]["cen_a"]
    up = r[(48, 127)]["v2"] - r[(48, 40)]["v2"]
    rows += [("M4", "C4 MED centroid v40 -> v117", q >= 1.4, q, f"x{q:.2f} (>= 1.4)", True),
             ("M4", "C3 MED V2/V1 v40 -> v127", up >= 8.0, up, f"{up:+.1f} dB (>= 8)", True)]
    return rows


def m6():
    s = strike_set(bm_tag("M6 SOFT"), base(BARK=SOFT), (48, 60), (117, 127), gap=2000, hold=900)
    rows = []
    for v in (117, 127):
        a, b = s[(48, v)], s[(60, v)]
        rows += [("M6", f"SOFT v{v} C3 V2/V1", a["v2"] <= -20, a["v2"], f"{a['v2']:.1f} dB (<= -20)", True),
                 ("M6", f"SOFT v{v} C3 V3/V1", a["v3"] <= -40, a["v3"], f"{a['v3']:.1f} dB (<= -40)", True),
                 ("M6", f"SOFT v{v} C4 V3/V1", b["v3"] <= -35, b["v3"], f"{b['v3']:.1f} dB (<= -35)", True)]
    med = strike_set(bm_tag("M6 MED"), base(BARK=MED), (48, 60), (127,), gap=2000, hold=900)
    hard = strike_set(bm_tag("M6 HARD"), base(BARK=HARD), (48, 60), (100,), gap=2000, hold=900)
    for n in (48, 60):
        d = 20 * math.log10(hard[(n, 100)]["cen_a"] / med[(n, 127)]["cen_a"])
        rows.append(("M6", f"key {n} MED v127 darker than HARD v100", d >= 6.0, d, f"attack centroid {d:+.1f} dB (>= 6)", True))
    return rows


def ring_set(tag, setup, keys, vel=100, hold=5000, gap=6000):
    """long strikes (MUTE 0, held): per key the V1 T60 (30 dB fit), the bloom, LUFS"""
    ev, t, st = list(setup), 400, []
    for n in keys:
        ev += bm.note(t, n, vel, hold)
        st.append((t, n))
        t += gap
    m, _, _ = render(tag, ev, t + 200)
    out = {}
    for tt, n in st:
        s0 = onset(m, tt)
        seg = m[s0:s0 + int(hold / 1000 * FS)]
        out[n] = dict(t60=t60(seg, freqs(n)["V1"]), bloom=bloom_ms(m, s0, n), lufs=float(bm.lufs(m[s0:s0 + FS])))
    return out


def m7():
    rows = []
    keys = (36, 48, 60, 84, 90, 96)
    T = {b: ring_set(bm_tag("M7 tube", b), base(mute=0, BELL=b), keys) for b in (0, 64, 127)}
    tgt = {36: (150, 300), 48: (84, 156), 60: (42, 78)}
    for n, (lo, hi) in tgt.items():
        b = T[64][n]["bloom"]
        rows.append(("M7", f"key {n} bloom", lo <= b <= hi, b, f"{b:.0f} ms ({lo}..{hi})", True))
    for n in (84, 90, 96):
        b = T[64][n]["bloom"]
        rows.append(("M7", f"key {n} bloom (reflector)", b <= 15, b, f"{b:.0f} ms (<= 15)", True))
    for n in (36, 48):
        q = T[64][n]["t60"] / T[0][n]["t60"]
        rows.append(("M7", f"key {n} TUBE 0 -> 64 lengthens", q >= 1.3, q, f"V1 T60 {T[0][n]['t60']:.2f} -> {T[64][n]['t60']:.2f} s (x{q:.2f}, >= 1.3)", True))
    for n in (84, 96):
        q = T[127][n]["t60"] / T[0][n]["t60"]
        d = T[127][n]["lufs"] - T[0][n]["lufs"]
        rows += [("M7", f"key {n} TUBE 0 vs 127 T60", abs(q - 1) <= 0.10, q, f"x{q:.2f} (within 10 %)", True),
                 ("M7", f"key {n} TUBE 0 -> 127 level", 1.0 <= d <= 3.0, d, f"{d:+.1f} LU (+1..+3)", True)]
    for fam in (SOFT, MED):
        s = strike_set(bm_tag("M7 thud", fam), base(BARK=fam), (36, 38, 40), (100,), gap=2500, hold=1200)
        w = max(s[(n, 100)]["v2o"] for n in (36, 38, 40))
        rows.append(("M7", f"C2-E2 thud, MALLET {fam}", w <= -10, w, f"V2 re V1 at the onset, worst {w:.1f} dB (<= -10)", True))
    return rows


def gate(full=False, only=None):
    rows = []
    want = (lambda k: only is None or k in only)
    keys = keys_for(full)
    if want("M1") or want("M3"):
        F = per_key(bm_tag("M1"), keys, 100)
        if want("M1"):
            rows += m1(F, keys)
        if want("M3"):
            F127 = per_key(bm_tag("M3"), keys, 127)
            rows += m3(F127, keys)
    if want("M2"):
        rows += m2(per_key(bm_tag("M2"), keys, 100, [(20, f"midi 0xB0 29 {MED}")]), keys)
    if want("M4"):
        rows += m4()
    if want("M6"):
        rows += m6()
    if want("M7"):
        rows += m7()
    return rows


# ---------------------------------------------------------------- the report
def status(row, stub):
    ok, specific = row[2], row[5]
    if ok is None:
        return "info"
    if ok:
        return "ok"
    return "AWAIT" if (stub and specific) else "FAIL"


def report(rows, stub, mode, secs, extra=()):
    os.makedirs(OUT, exist_ok=True)
    st = [status(r, stub) for r in rows]
    head = [f"marimba_scan: TYPE {TYPE} MARIMBA (CC 79 {CC79}), {mode}, {len(rows)} checks, {secs:.0f} s",
            f"tables: {'STUB (sync 1: MARIMBA = VIBES) -> MARIMBA-specific verdicts are AWAIT (' + STUB_REASON + ')' if stub else 'real (sync 2)'}",
            f"spec: {os.path.relpath(spec_source(), ROOT) if spec_source() else 'none (1 : 4 : 9.8 on the ET pitch)'}",
            f"ok {st.count('ok')}, FAIL {st.count('FAIL')}, AWAIT {st.count('AWAIT')}, info {st.count('info')}", ""]
    groups = []
    for r in rows:
        if r[0] not in groups:
            groups.append(r[0])
    for g in groups:
        gs = [s for r, s in zip(rows, st) if r[0] == g]
        head.append(f"{g:4} {'FAIL' if 'FAIL' in gs else 'AWAIT' if 'AWAIT' in gs else 'PASS'}  "
                    f"{gs.count('ok')}/{len(gs) - gs.count('info')} pass" + (f", {gs.count('AWAIT')} await" if 'AWAIT' in gs else ""))
    head += ["", "per check:"]
    for r, s in zip(rows, st):
        head.append(f"  {r[0]:4} {s:5} {r[1]:42} {r[4]}")
    head += list(extra)
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(head) + "\n")
    json.dump(dict(type=TYPE, cc79=CC79, stub=stub, reason=STUB_REASON if stub else None, mode=mode,
                   rows=[dict(check=r[0], case=r[1], ok=r[2], metric=r[3], detail=r[4], specific=r[5], status=s)
                         for r, s in zip(rows, st)]),
              open(os.path.join(OUT, "scan.json"), "w"), indent=1, default=float)
    return st, head


def selector_rows():
    """the plumbing: CC 79 127 and PC 14 / 15 give TYPE 5 on the panel and in the engine (host state dump)"""
    import subprocess
    rows = []
    for name, sel in (("CC 79 127", f"midi 0xB0 79 {CC79}"), ("PC 14 (F15)", "midi 0xC0 14 0"), ("PC 15 (F16)", "midi 0xC0 15 0")):
        p = os.path.join(OUT, "wav", bm_tag("sel", name))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p + ".txt", "w").write(f"50 {sel}\n200 expect state card.2.0 MARIMBA\n200 expect state bars 1\n"
                                    "300 midi 0x90 60 100\n500 expect rms > -40\n900 midi 0x80 60 0\n1000 end\n")
        r = subprocess.run([bm.BIN, p, p + ".txt"], capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if "FAIL" in l]
        rows.append(("SEL", name, not bad and r.returncode == 0, len(bad), "; ".join(bad) or "TYPE MARIMBA, the engine plays", False))
        for e in (".txt", ".wav"):
            try:
                os.remove(p + e)
            except OSError:
                pass
    return rows


def main(argv=None):
    global KEEP
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--smoke", action="store_true")
    g.add_argument("--quick", action="store_true")
    g.add_argument("--full", action="store_true")
    ap.add_argument("--only", help="parts: SEL, stress, M1, M2, M3, M4, M6, M7 (M5 is in stress)")
    ap.add_argument("--keep-wav", action="store_true")
    a = ap.parse_args(argv)
    KEEP = a.keep_wav
    t0 = time.time()
    bm.build()
    shutil.rmtree(os.path.join(OUT, "wav"), ignore_errors=True)
    stub = stub_detected()
    only = set(a.only.split(",")) if a.only else None
    mode = "full" if a.full else "smoke" if a.smoke else "quick"
    rows = []
    if only is None or "SEL" in only:
        rows += selector_rows()
    if only is None or "stress" in only:
        rows += stress(smoke=a.smoke)
    if not a.smoke:
        rows += gate(a.full, only)
    extra = []
    if a.full and only is None:                        # the generic S-checks: tools/bar_scan.py --type 5 --full
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "bar_scan.py"), "--type", str(TYPE), "--full"],
                           capture_output=True, text=True)
        extra = ["", "tools/bar_scan.py --type 5 --full (build/bar_scan/report.txt):"] + r.stdout.splitlines()[-16:]
        if r.returncode:
            rows.append(("S", "bar_scan --type 5 --full", False, r.returncode, "failures: build/bar_scan/report.txt", False))
    st, lines = report(rows, stub, mode, time.time() - t0, extra)
    print("\n".join(lines[:4 + len({r[0] for r in rows}) + 1]))
    print("->", os.path.relpath(OUT, ROOT) + "/report.txt, scan.json")
    if not KEEP:
        shutil.rmtree(os.path.join(OUT, "wav"), ignore_errors=True)
    return 1 if "FAIL" in st else 0


if __name__ == "__main__":
    sys.exit(main())
