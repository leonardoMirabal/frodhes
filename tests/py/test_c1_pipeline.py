# SPDX-License-Identifier: GPL-3.0-only
"""Two cores (FR_C1, a build switch, off by default: frodhes.c app_audio, hal/fm1_core1.h, the host's twin
firmware/src/c1_host.h). Core 0 renders block k's voices while core 1 runs the effects of block k-1, so the effects
see the voices one block (256 frames) later. On the host the twin runs core 1's job on this thread, at the hand-off
(order 0) or at the wait (order 1), and injects refusals, a late job or a core 1 that never started (FRODHES_C1).

- the two orders render the same samples: nothing the voices and the effects both touch changes while both run (a
  shared variable would show here as a difference: the race test of the pipeline);
- refused jobs (core 0 renders them, core 1 stays on) render the same samples as none refused;
- the voices are those of the one-core 14-voice build, one block later (the effects bypassed by CC 119: the voice
  engine's own output), and the whole chain with every effect is as loud as the one-core build's within 0.5 dB, its
  envelope one block later;
- a late job (core 1 held for good) and a core 1 that never started keep playing (one core, FR_C1_NV1 voices).
The default build (no FR_C1) is byte-identical to before: Gate R (tools/gate_r.py) and the firmware's image."""
import os

import numpy as np
import pytest

from hostrun import ROOT, build, cc, note, out, run, rms_db

C1 = os.path.join(ROOT, "build", "host", "c1", "frodhes")
REF = os.path.join(ROOT, "build", "host", "c1ref", "frodhes")
BLK = 256


def c1_bin():
    return build(C1, ["-DFR_C1=1"])


def ref_bin():
    return build(REF, ["-DFR_VOICES=14u", "-DFR_REV_HQ=1"])


HEAVY = dict(DRIVE=(81, 90), CMP_SW=(106, 127), CMP_AMT=(107, 90), TW_SW=(17, 64), TW_DEPTH=(18, 100),
             PH_SW=(12, 127), PH_DEPTH=(13, 100), DL_SW=(88, 80), DL_DEPTH=(89, 100), DL_TIME=(90, 60),
             REV=(91, 110), FL_TYPE=(70, 45), FL_FREQ=(74, 70), FL_LFO=(72, 64), SPREAD=(105, 100))


def take(engine=0, fx=True, raw=False, end=6000):
    """a 14-note chord with the pedal, then single notes over it: CCs first (before any sound), then the notes"""
    ls = []
    if engine:
        ls.append(cc(20, 79, 0 if engine == 1 else 127))     # VIBES / MARIMBA (tools/gate_r.py's selector)
    if fx:
        for n, v in HEAVY.values():
            if n is not None:
                ls.append(cc(30, n, v))
    if raw:
        ls.append(cc(40, 119, 127))
    ls.append(cc(300, 64, 127))
    for i, k in enumerate((36, 40, 43, 47, 50, 53, 57, 60, 64, 67, 71, 74, 77, 81)):
        ls += note(400 + 3 * i, k, 70 + 4 * (i % 8), 1500)
    for i, k in enumerate((84, 79, 72, 88, 62, 55)):
        ls += note(2200 + 350 * i, k, 110, 300)
    ls.append(cc(4500, 64, 0))
    return ls


def render(tag, lines, binary, env=None, end=6000):
    r = run(tag, lines, end_ms=end, binary=binary, env=env)
    L, R = r.L, r.R
    assert len(L) > 44100 * (end - 500) // 1000, f"{tag}: short render"
    return np.stack([L, R])


@pytest.mark.parametrize("engine", [0, 1])
def test_orders_render_the_same(engine):
    b = c1_bin()
    a = render(f"c1_o0_{engine}", take(engine), b, {"FRODHES_C1": "order=0"})
    c = render(f"c1_o1_{engine}", take(engine), b, {"FRODHES_C1": "order=1"})
    assert rms_db(a[0]) > -40, "the take is silent"
    d = np.flatnonzero(np.any(a != c, axis=0))
    assert d.size == 0, f"engine {engine}: the orders differ from frame {d[0]} ({d.size} frames)"


def test_refused_jobs_render_the_same():
    b = c1_bin()
    a = render("c1_ok", take(), b, {"FRODHES_C1": "order=1"})
    c = render("c1_ref3", take(), b, {"FRODHES_C1": "order=1,refuse=3"})
    d = np.flatnonzero(np.any(a != c, axis=0))
    assert d.size == 0, f"refused jobs changed the output from frame {d[0]}"


def test_voices_as_one_core_one_block_later():
    a = render("c1_raw", take(raw=True), c1_bin())
    r = render("c1_raw_ref", take(raw=True), ref_bin())
    assert rms_db(r[0]) > -40
    n = min(a.shape[1] - BLK, r.shape[1])
    assert np.array_equal(a[:, BLK:BLK + n], r[:, :n]), "the voices are not the one-core build's, one block later"
    assert not np.any(a[:, :BLK]), "the first block is not silence"


@pytest.mark.parametrize("engine", [0, 2])
def test_every_effect_as_loud_one_block_later(engine):
    a = render(f"c1_fx_{engine}", take(engine), c1_bin())
    r = render(f"c1_fx_ref_{engine}", take(engine), ref_bin())
    n = min(a.shape[1] - BLK, r.shape[1])
    la, lr = rms_db(a[0, BLK:BLK + n]), rms_db(r[0, :n])
    assert abs(la - lr) < 0.5, f"engine {engine}: {la:.2f} dB on two cores, {lr:.2f} on one"
    hop = 512
    ea = np.sqrt(np.mean(a[0, :(a.shape[1] // hop) * hop].reshape(-1, hop) ** 2, axis=1))
    er = np.sqrt(np.mean(r[0, :(r.shape[1] // hop) * hop].reshape(-1, hop) ** 2, axis=1))
    m = min(len(ea), len(er))
    lag = max(range(-4, 5), key=lambda k: float(np.dot(ea[max(k, 0):m + min(k, 0)], er[max(-k, 0):m - max(k, 0)])))
    assert lag in (0, 1), f"engine {engine}: the envelope is {lag} hops off (one block = half a hop)"


def test_late_job_goes_on_with_one_core():
    a = render("c1_late", take(), c1_bin(), {"FRODHES_C1": "late=200"})
    t = 200 * BLK
    assert rms_db(a[0, t + 4410:t + 44100]) > -50, "silent after the late job"
    assert np.all(np.isfinite(a))


def test_core1_never_started_plays():
    a = render("c1_off", take(), c1_bin(), {"FRODHES_C1": "off=1"})
    assert rms_db(a[0, 44100:3 * 44100]) > -40


def test_soak_tool_selftest():
    """tools/hil_c1_soak.py (the device soak's reader and verdict) decodes and judges made-up replies"""
    import subprocess
    import sys
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_c1_soak.py"), "--selftest"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and "ok" in p.stdout, p.stdout + p.stderr
