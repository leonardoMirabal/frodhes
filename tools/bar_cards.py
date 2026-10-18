#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""What the VIBES VOICE cards (STRIKE / MALLET / TUBE / WEIGHT = TONE / BARK / BELL / HAMMER) do to the sound, set
from the panel as a player would (tests/host.c: EDIT, then K1..K4 turned from the default 64), measured on the host
build's RAW output. Shared by tests/py/test_bars_cards_audible.py.

  tools/bar_cards.py                 every card at 0 / 32 / 64 / 96 / 127 (MALLET at its detents 0 42 64 85 127),
                                     keys 53 / 69 / 89, v100 and v127: a table, build/bar_cards/report.txt

Per strike (one key, 2.5 s apart, held 1.2 s, NOISE 0 unless asked):
  lufs    BS.1770 K-weighted, 0..1 s (the S9 measure)
  cen_a   spectral centroid 0..0.3 s (the attack, where the mallet and the strike point speak)
  cen     spectral centroid 0.32..0.82 s
  v2, v3  the 4th / 10th partial re the fundamental (dB), 0.005..0.3 s, the spec's own frequencies
  bands   1/3-octave band levels (dB) 0..0.5 s, 50 Hz..16 kHz (spectral distance: dist())
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402

OUT = os.path.join(bm.ROOT, "build", "bar_cards")
KEYS = (53, 69, 89)
VELS = (100, 127)
CARDS = ("TONE", "BARK", "BELL", "HAMMER")          # K1..K4 on VOICE (VIBES: STRIKE MALLET TUBE WEIGHT)
ENC = dict(TONE=2, BARK=3, BELL=4, HAMMER=5)
GAP, HOLD, T0 = 2500, 1200, 400
BANDS = [50 * 2 ** (i / 3) for i in range(26)]        # 50 Hz .. 15.9 kHz centres
_SPEC = None


def spec():
    """the spec's keys (the partials' frequencies); BAR_CARDS_SPEC overrides the path (with BAR_CARDS_BIN: another
    build, e.g. the tables of before, to show a test failing there)"""
    global _SPEC
    if _SPEC is None:
        p = os.environ.get("BAR_CARDS_SPEC") or os.path.join(bm.ROOT, "docs", "model", "data", "bars_spec.json")
        _SPEC = {k["midi"]: k for k in json.load(open(p))["keys"]}
    return _SPEC


VOICE_PAGE = [(200, "btn 7"), (203, "btn 8"), (206, "turn 0 1"), (209, "turn 0 1")]   # GLO, HOME: PERFORM from any
# page; SELECT twice: VOICE (EDIT is CHORD since 0.9.3)


def events(card_vals, keys=KEYS, vels=VELS, noise=0):
    """VIBES, RAW, then the VOICE page and each card turned from 64 to its value; strikes from T0"""
    ev = bm.setup_events(t0=20, typ=4, raw=True, noise=noise)
    ev += VOICE_PAGE
    for i, (c, v) in enumerate(sorted(card_vals.items())):
        if v != 64:
            ev.append((220 + 10 * i, f"turn {ENC[c]} {int(v) - 64}"))
    t, strikes = T0, []
    for v in vels:
        for n in keys:
            ev += bm.note(t, n, v, HOLD)
            strikes.append((t, n, v))
            t += GAP
    return ev, t + 200, strikes


def render(card_vals, tag=None, **kw):
    ev, end, strikes = events(card_vals, **kw)
    tag = tag or "cards_" + ("_".join(f"{c}{v}" for c, v in sorted(card_vals.items())) or "default")
    if os.environ.get("BAR_CARDS_BIN"):
        bm.BIN = os.environ["BAR_CARDS_BIN"]
    x, log = bm.render(tag, ev, end, OUT)
    return bm.mono(x), strikes, log


def onset(m, t_ms):
    """the first sample of the strike after its event (the host's one-block latency)"""
    a = int(t_ms * bm.FS / 1000)
    seg = np.abs(m[a:a + 2048])
    i = np.flatnonzero(seg > 0.5)
    return a + (int(i[0]) if len(i) else 0)


def peak_db(seg, f, rel=0.03):
    """a partial's level (dB): the energy within +-rel of f (Hann, zero-padded)"""
    nf = 1 << max(15, int(math.ceil(math.log2(len(seg)))) + 2)
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / bm.FS)
    sel = (fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)
    return 10 * math.log10(float(S[sel].sum()) + 1e-30)


def third_oct(seg):
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    fr = np.fft.rfftfreq(len(seg), 1 / bm.FS)
    out = []
    for c in BANDS:
        sel = (fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))
        out.append(10 * math.log10(float(S[sel].sum()) + 1e-30))
    return np.array(out)


def features(m, s0, n):
    fs = bm.FS
    k = spec()[n]
    f = {md["name"]: md["f"] for md in k["modes"]}
    a = m[s0:s0 + int(0.3 * fs)]
    att = m[s0 + int(0.005 * fs):s0 + int(0.3 * fs)]
    l1 = peak_db(att, f["V1"])
    return dict(lufs=float(bm.lufs(m[s0:s0 + int(1.0 * fs)])),
                cen_a=bm.centroid(a), cen=bm.centroid(m[s0 + int(0.32 * fs):s0 + int(0.82 * fs)]),
                v2=peak_db(att, f["V2"]) - l1, v3=peak_db(att, f["V3"]) - l1,
                peak=float(20 * math.log10(np.abs(m[s0:s0 + int(1.0 * fs)]).max() / 32767 + 1e-12)),
                bands=third_oct(m[s0:s0 + int(0.5 * fs)]).tolist())


def measure(card_vals, **kw):
    """{(key, vel): features} for one setting of the cards (the others at 64)"""
    m, strikes, log = render(card_vals, **kw)
    return {(n, v): features(m, onset(m, t), n) for t, n, v in strikes}, log


def dist(b1, b2, floor=60.0):
    """spectral distance (dB RMS) of two 1/3-octave spectra, level-aligned: each re its own total energy, the bands
    floored at floor dB under the louder spectrum's strongest band, the RMS over the bands either has above it"""
    b1, b2 = np.asarray(b1), np.asarray(b2)
    t1, t2 = 10 * np.log10(np.sum(10 ** (b1 / 10))), 10 * np.log10(np.sum(10 ** (b2 / 10)))
    r1, r2 = b1 - t1, b2 - t2
    fl = max(r1.max(), r2.max()) - floor
    use = (r1 > fl) | (r2 > fl)
    d = np.maximum(r1, fl) - np.maximum(r2, fl)
    return float(np.sqrt(np.mean(d[use] ** 2)))


SWEEP = dict(TONE=(0, 32, 64, 96, 127), BARK=(0, 42, 64, 85, 127), BELL=(0, 32, 64, 96, 127),
             HAMMER=(0, 32, 64, 96, 127))


LISTEN = [("mallet", "BARK", ((0, "SOFT"), (42, "YARN"), (64, "default"), (85, "CORD"), (127, "RUBBER"))),
          ("strike", "TONE", ((0, "centre"), (64, "L3-default"), (127, "edge"))),
          ("tube", "BELL", ((0, "none"), (64, "default"), (127, "full"))),
          ("weight", "HAMMER", ((0, "light"), (64, "default"), (127, "heavy")))]


def listen(out=os.path.join(bm.ROOT, "build", "listen", "cards")):
    """the A/B set: the same phrase (A4, a C major chord C4 E4 G4, F3) at v100 and v117 for each card setting,
    VIBES with the panel's defaults otherwise (effects as the default panel: not RAW), one WAV each + README.txt"""
    import shutil
    os.makedirs(out, exist_ok=True)
    rows = []
    for vel in (100, 117):
        for name, card, vals in LISTEN:
            for v, lab in vals:
                ev = bm.setup_events(t0=20, typ=4, raw=False, noise=None) + VOICE_PAGE
                if v != 64:
                    ev.append((220, f"turn {ENC[card]} {v - 64}"))
                t = 500
                ev += bm.note(t, 69, vel, 1500)
                for n in (60, 64, 67):
                    ev += bm.note(t + 2500, n, vel, 2000)
                ev += bm.note(t + 6000, 53, vel, 2500)
                tag = f"{name}_{v:03d}_{lab}_v{vel}"
                bm.render(tag, ev, t + 10500, OUT)
                shutil.copy(os.path.join(OUT, tag + ".wav"), os.path.join(out, tag + ".wav"))
                rows.append(f"{tag}.wav  {bm.BAR_NAME[card]} {v} ({lab}), velocity {vel}")
    open(os.path.join(out, "README.txt"), "w").write(
        "VIBES VOICE cards, A/B listening set (host build, tools/bar_cards.py --listen).\n"
        "Each file: A4 (0.5 s), a C major chord C4-E4-G4 (3.0 s), F3 (6.5 s); VIBES, the default panel (effects as\n"
        "default), one card turned on the VOICE page (K1..K4), the others at 64. v117 = the FM-1 keys at KEY VEL 117.\n"
        "Compare within a group: mallet_* (MALLET: SOFT 0, YARN 42, the default 64, CORD 85, RUBBER 127), strike_*\n"
        "(STRIKE 0 centre / 64 / 127 edge), tube_* (TUBE 0 / 64 / 127), weight_* (WEIGHT 0 light / 64 / 127 heavy).\n\n"
        + "\n".join(rows) + "\n")
    print("\n".join(rows))


def main():
    if "--listen" in sys.argv:
        bm.build()
        return listen()
    bm.build()
    os.makedirs(OUT, exist_ok=True)
    rows, res = [], {}
    for c in CARDS:
        for v in SWEEP[c]:
            res[(c, v)], _ = measure({c: v})
    for c in CARDS:
        rows.append(f"== {c}")
        for n in KEYS:
            for vel in VELS:
                ref = res[(c, 64)][(n, vel)]
                s = []
                for v in SWEEP[c]:
                    r = res[(c, v)][(n, vel)]
                    s.append(f"{v:3d}: {r['lufs']:6.1f} LU pk {r['peak']:5.1f} cA {r['cen_a']:5.0f} c {r['cen']:5.0f} "
                             f"V2 {r['v2']:6.1f} V3 {r['v3']:6.1f} d {dist(r['bands'], ref['bands']):4.1f}")
                lo, hi = res[(c, SWEEP[c][0])][(n, vel)], res[(c, SWEEP[c][-1])][(n, vel)]
                rows.append(f"  key {n} v{vel}  ends: d {dist(lo['bands'], hi['bands']):.1f} dB, "
                            f"level {hi['lufs'] - lo['lufs']:+.1f} LU")
                rows += ["      " + x for x in s]
    if CARDS:
        rows.append("== MALLET detents pairwise (dB RMS)")
        dets = (0, 42, 85, 127)
        for n in KEYS:
            for vel in VELS:
                rows.append(f"  key {n} v{vel}: " + " ".join(
                    f"{a}-{b} {dist(res[('BARK', a)][(n, vel)]['bands'], res[('BARK', b)][(n, vel)]['bands']):.1f}"
                    for i, a in enumerate(dets) for b in dets[i + 1:]))
    txt = "\n".join(rows)
    open(os.path.join(OUT, "report.txt"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
