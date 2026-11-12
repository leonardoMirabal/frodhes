# SPDX-License-Identifier: GPL-3.0-only
"""The two-core package's note starts from silence (FR_C1_LAT: 128-frame interrupts, a note-on starting at the next
interrupt): a note that starts in a 256-frame block's second half reaches the output without the silence gate's
fade-in (fx.c: the gate's ramp is reckoned from the block's start, where one core's ramp has it; the note's first
frames take the full gain), the first half's and the 256-frame build's with it (64 frames from 0 to 1). Does the
skipped fade-in click?

Single notes from digital silence (the gate shut: 2.5 s apart, released after 0.3 s), arrivals over every phase of
the block, on each engine (Rhodes, VIBES, MARIMBA) at a soft and a hard velocity, the boot sound (its effects), in
four host builds: the 256-frame two-core build and the package, each also with the gate never shutting (GATE_HOLD
huge: the same note with no fade-in at all, as every note played while the gate is open). Per onset (the first
non-zero frame after the arrival):
  - glitch events: tests/py/glitch_lpc.py (the FM-1 takes' click detector: a short excess of the LPC residual) in
    the 50 ms from the onset, either channel
  - jump: the first non-zero sample (the step out of digital silence) less the gate-open twin's step at that frame
    (the same note, the same timing), LSB: a gain that comes on at once over frames already there shows here
  - step: the largest sample-to-sample step in the first 1.45 ms (the fade's span), LSB, and the twin's
Pass: jump <= 3 LSB, step <= 1.3 x the twin's + 2 (a fade times a rising onset may steepen it a little: the 256-frame
build's own fade does), no more glitch events than the twin.
Found (cand3 d202b740): the second half's skipped fade-in is harmless (those onsets are the gate-open twin's, sample
for sample: a note played while the gate is open never had a fade-in), but the package opened the gate late on soft
notes whose first frames stayed under the threshold (sent muted, then the full gain at once: MARIMBA soft, a jump of
16 LSB where the twin's is 2), and the bar engines' threshold was 16, not one core's 32 (fx.c GATE_LO_BARS)."""
import os

import numpy as np
import pytest

import glitch_lpc
from hostrun import ROOT, build, cc, run

FS = 44100
TAIL = 64                                           # (the package's tail: its output 64 frames later)
ENGINES = {"rhodes": [], "vibes": [cc(20, 79, 0)], "marimba": [cc(20, 79, 127)]}
VELS = {"soft": 24, "hard": 127}
N = 24


def bins():
    d = {"b256": ["-DFR_C1=1"], "lat": ["-DFR_C1=1", "-DFR_C1_LAT=1"]}
    d.update({k + "_open": v + ["-DGATE_HOLD=4000000000u"] for k, v in list(d.items())})
    return {k: build(os.path.join(ROOT, "build", "host", f"gateon_{k}", "frodhes"), v) for k, v in d.items()}


def arrivals():
    return [1500 + 2500 * i + (i * 37) % 97 for i in range(N)]


def render(name, eng, vel, binary):
    ts = arrivals()
    lines = list(ENGINES[eng])
    for i, t in enumerate(ts):
        n = (48, 60, 72, 84)[i % 4] + i % 3
        lines += [f"{t} midi 0x90 {n} {VELS[vel]}", f"{t + 300} midi 0x80 {n} 0"]
    r = run(f"gateon_{name}_{eng}_{vel}", lines, binary=binary, end_ms=ts[-1] + 1500)
    return np.stack([r.L, r.R]).T.astype(int)


def onsets(x, w):
    """x a gated take, w its gate-open twin: per note its onset k, silence before, glitch events, jump, step (x, w)"""
    nz = np.flatnonzero(np.any(x != 0, axis=1))
    ev = [glitch_lpc.events(x[:, c].astype(float), FS) for c in (0, 1)]
    ew = [glitch_lpc.events(w[:, c].astype(float), FS) for c in (0, 1)]
    out = []
    for t in arrivals():
        f0 = int(t * FS / 1000) - 2048                   # (the host renders an interrupt ahead of its ms)
        k = int(nz[np.searchsorted(nz, f0)])
        g = lambda es: sum(int(np.any((e >= k - 50) & (e < k + 2205))) for e in es)
        st = lambda y: int(np.abs(np.diff(y[k - 1:k + 64], axis=0)).max())
        out.append(dict(k=k, quiet=not np.any(x[max(k - 2205, 0):k]), glitch=g(ev), glitch_w=g(ew),
                        jump=int((np.abs(x[k]) - np.abs(w[k] - w[k - 1])).max()), step=st(x), step_w=st(w)))
    return out


@pytest.fixture(scope="module")
def results():
    b = bins()
    res = {}
    for eng in ENGINES:
        for vel in VELS:
            for name in ("b256", "lat"):
                res[(name, eng, vel)] = onsets(render(name, eng, vel, b[name]),
                                               render(name + "_open", eng, vel, b[name + "_open"]))
    return res


def half(name, k):
    return ((k - (TAIL if name.startswith("lat") else 0)) % 256) // 128


@pytest.mark.parametrize("vel", list(VELS))
@pytest.mark.parametrize("eng", list(ENGINES))
def test_gate_onset_no_click(results, eng, vel):
    for name in ("b256", "lat"):
        o = results[(name, eng, vel)]
        assert all(r["quiet"] for r in o), f"{name}: a note did not start from silence"
        for h in (0, 1):
            rs = [r for r in o if half(name, r["k"]) == h]
            if not rs:
                continue
            m = lambda k: max(r[k] for r in rs)
            print(f"{eng:8s} {vel:4s} {name:4s} half {h}: n {len(rs):2d}  jump {m('jump'):3d}  step {m('step'):4d} "
                  f"(open {m('step_w'):4d})  glitch {sum(r['glitch'] for r in rs)} (open {sum(r['glitch_w'] for r in rs)})")
            for r in rs:
                assert r["jump"] <= 3 and r["step"] <= 1.3 * r["step_w"] + 2, (name, r)
            assert sum(r["glitch"] for r in rs) <= sum(r["glitch_w"] for r in rs)
    assert sum(half("lat", r["k"]) for r in results[("lat", eng, vel)]) >= 4, "too few second-half starts"
