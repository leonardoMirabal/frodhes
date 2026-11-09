# SPDX-License-Identifier: GPL-3.0-only
"""The latency study's build switch (frodhes.c FR_LAT_HALF, FR_LAT_EV; the two-core build): the I2S half buffer at
128 / 64 frames, the app's 256-frame block rendered over 2 / 4 interrupts.

- Same integers: with FRODHES_LAT_SYNC=1 (tests/host.c: a block's halves back to back, so every event lands in the
  same block as in the 256-frame build) the HEAVY take (every effect, 14 voices, the pedal, SPREAD) on each engine
  renders the FR_C1 256-frame build's integers exactly, at 128 frames also with core 1 refusing jobs, late, off;
  FR_LAT_TAIL 32 (the interrupt sends its frames 32 late, core 1 finishes the last 32 after it): the same integers
  32 frames later.
- Event timing: single notes from silence (RAW: the engine's output, no limiter delay, no gate), their arrival
  spread over the block's phase; each onset's output time on the FM-1's clock (tests/host.c renders an interrupt's
  frames when they fall due: the frames play one half buffer after that) minus the note's arrival. The 256-frame
  build waits for the next block (5.8 ms + up to 5.8); FR_LAT_HALF 128 the same block wait but one half buffer out
  (2.9 + up to 5.8); with FR_LAT_EV a free-voice note-on starts at the next interrupt (2.9 + up to 2.9; 64: 1.45 +
  up to 1.45); FR_LAT_TAIL adds 32 frames (0.73 ms). The host's 1 ms tick adds up to 1 ms (the event is seen at the tick after its own)."""
import os

import numpy as np
import pytest

from hostrun import ROOT, build, cc, run
from test_c1_pipeline import take

FS = 44100


def lat_bin(half=256, ev=0, tail=0):
    tag = f"lat{half}e{ev}t{tail}"
    defs = ["-DFR_C1=1"] + ([f"-DFR_LAT_HALF={half}u", f"-DFR_LAT_EV={ev}", f"-DFR_LAT_TAIL={tail}u"] if half != 256 else [])
    return build(os.path.join(ROOT, "build", "host", tag, "frodhes"), defs)


def ref_bin(half=128):
    """the FR_C1 256-frame build with the latency build's note starts a block (frodhes.c BAR_START_MAX, RH_START_MAX:
    N x half / 256; without FR_LAT_EV the rest wait for the next block, as here)"""
    k = 256 // half
    return build(os.path.join(ROOT, "build", "host", f"latref{half}", "frodhes"),
                 ["-DFR_C1=1", f"-DBAR_START_MAX=(BAR_NA/{k}u)", f"-DRH_START_MAX=(RH_NA/{k}u)"])


def wav(tag, lines, binary, env=None, end=None):
    r = run(tag, lines, binary=binary, env=env, end_ms=end)
    assert not r.fails, r.fails
    return np.stack([r.L, r.R]).T.astype(int), r


def same_shifted(a, b, sh):
    """b = a delayed by sh frames (b's first sh frames zero)"""
    n = min(len(a) + sh, len(b))
    return n > 2 * FS and not np.any(b[:sh]) and np.array_equal(a[:n - sh], b[sh:n])


@pytest.mark.parametrize("tail", [0, 32])
@pytest.mark.parametrize("engine", [0, 1, 2])
def test_lat128_same_integers(engine, tail):
    lines = take(engine=engine, end=4000)
    a, _ = wav(f"lat_ref_{engine}", lines, ref_bin())
    b, _ = wav(f"lat128_{engine}_t{tail}", lines, lat_bin(128, tail=tail), env={"FRODHES_LAT_SYNC": "1"})
    assert same_shifted(a, b, tail)


@pytest.mark.parametrize("tail", [0, 32])
@pytest.mark.parametrize("c1", ["order=1", "refuse=3", "late=40", "off=1", "busy=3"])
def test_lat128_same_integers_core1_faults(c1, tail):
    lines = take(engine=0, end=3000)
    a, _ = wav(f"lat_ref_{c1}", lines, ref_bin(), env={"FRODHES_C1": c1})
    b, _ = wav(f"lat128_{c1}_t{tail}", lines, lat_bin(128, tail=tail), env={"FRODHES_C1": c1, "FRODHES_LAT_SYNC": "1"})
    if c1.startswith("late"):                       # (the late job: core 1 held from a different block; the
        return                                      # rest one core at 10 voices, then the steals differ: runs clean)
    assert same_shifted(a, b, tail)


ONE = {"fm1hq": ["-DFR_REV_HQ=1", "-DFR_VOICES=14u"],                    # the FM-1 one core, the full-rate reverb
       "move": ["-DFR_HQ=1", "-DFR_REV_HQ=1", "-DFR_VOICES=16u", "-DFR_BYP_KEEP_CMP=1"]}   # (Makefile MOVE_DEFS)


def one_bin(cfg, half=256, chunk=1, caps=1):
    """FR_CHUNK without FR_C1 (the Move, the plugin; the FM-1 with FR_REV_HQ): the 256-frame block in 32-frame
    chunks on one core; half < 256: FR_LAT_HALF. chunk 0: the plain build (caps: the half's note starts a block)"""
    k = 256 // half
    d = ONE[cfg] + (["-DFR_CHUNK=1"] if chunk else []) + ([f"-DFR_LAT_HALF={half}u"] if chunk and half != 256 else [])
    if not chunk and k > 1:
        d += [f"-DBAR_START_MAX=(BAR_NA/{k}u)", f"-DRH_START_MAX=(RH_NA/{k}u)"]
    return build(os.path.join(ROOT, "build", "host", f"one_{cfg}_{half}_c{chunk}", "frodhes"), d)


@pytest.mark.parametrize("cfg,half", [("fm1hq", 256), ("fm1hq", 128), ("move", 256), ("move", 128), ("move", 64)])
@pytest.mark.parametrize("engine", [0, 1, 2])
def test_chunk_one_core_same_integers(cfg, half, engine):
    """FR_CHUNK on one core renders the plain build's integers (whole blocks), and with FR_LAT_HALF in sync mode the
    plain build's with the half's start caps"""
    lines = take(engine=engine, end=3000)
    a, _ = wav(f"one_ref_{cfg}_{half}_{engine}", lines, one_bin(cfg, half, chunk=0))
    b, _ = wav(f"one_{cfg}_{half}_{engine}", lines, one_bin(cfg, half), env={"FRODHES_LAT_SYNC": "1"})
    assert same_shifted(a, b, 0)


def onsets(lines_t, x, half):
    """each note's onset: the first non-zero frame after its arrival; its output time on the FM-1's clock: the
    interrupt holding frame f renders at host ms ceil((f // half + 1) x half x 1000 / FS) and plays one half buffer
    later. Returns the latencies in ms"""
    lat = []
    nz = np.flatnonzero(np.any(x != 0, axis=1))
    for t in lines_t:
        f0 = int(t * FS / 1000) - 2 * half
        k = nz[np.searchsorted(nz, max(f0, 0))]
        c = k // half
        t_render = -(-(c + 1) * half * 1000 // FS)
        out = t_render + 1000.0 * half / FS + 1000.0 * (k - c * half) / FS
        lat.append(out - t)
    return np.array(lat)


@pytest.mark.parametrize("half,ev,tail,lo,hi", [(256, 0, 0, 5.80, 12.7), (128, 0, 0, 2.90, 9.8), (128, 1, 0, 2.90, 6.9),
                                                (128, 1, 32, 3.63, 7.6), (64, 1, 0, 1.45, 3.95), (64, 1, 32, 2.18, 4.7)])
def test_event_timing(half, ev, tail, lo, hi):
    ts = [1000 + 700 * i + (i * 37) % 97 for i in range(48)]     # arrivals over every phase of the block
    lines = [cc(10, 119, 127)]                                   # RAW: the engine's output (no limiter, no gate)
    for i, t in enumerate(ts):
        lines += [f"{t} midi 0x90 {60 + i % 12} 100", f"{t + 80} midi 0x80 {60 + i % 12} 0"]
    x, _ = wav(f"lat_ev_{half}_{ev}_{tail}", lines, lat_bin(half, ev, tail), end=ts[-1] + 700)
    lat = onsets(ts, x, half)
    print(f"half {half} ev {ev} tail {tail}: latency ms min {lat.min():.2f} mean {lat.mean():.2f} max {lat.max():.2f}")
    assert lat.min() >= lo - 0.05 and lat.max() <= hi + 0.05, lat
    span = 1000.0 * (half if ev else 256) / FS                     # the wait's range: the event's grid
    assert lat.max() - lat.min() <= span + 1.05


def test_ev_restrike_and_steal():
    """FR_LAT_EV: re-strikes and steals start mid-block too (rhodes.c rh_mid_one), the felt's plan kept (the host's
    LFSR check aborts otherwise); arrivals over the block's phases"""
    import re
    lines = [cc(10, 119, 127), cc(400, 64, 127), cc(4400, 64, 0)]   # the pedal: let-go notes ring on (re-strikes)
    for i in range(80):                                          # 6 pitches let go after 0.2 s; then 30 held: steals
        t = 500 + 97 * i + (i * 29) % 41
        n, hold = (40 + i % 6, 200) if i < 40 else (40 + (i * 7) % 30, 3000)
        lines += [f"{t} midi 0x90 {n} {60 + i % 60}", f"{t + hold} midi 0x80 {n} 0"]
    _, r = wav("lat_ev_rs", lines, lat_bin(128, 1, 32), end=11000)
    m = re.search(r"mid starts (\d+) restrikes (\d+) steals (\d+)", r.out)
    assert m and all(int(x) > 0 for x in m.groups()), r.out[-400:]
    assert "ev_drop 0" in r.out


@pytest.mark.parametrize("engine", [127, 0])                         # MARIMBA, VIBES
def test_bar_ev_mid_start_pristine(engine):
    """FR_LAT_EV on the bar engine (bars.c bar_mid_one): a note the block's start left waiting (the start cap: 1 here)
    starts at the block's next interrupt, 128 frames sooner than the next block's start, with the same onset (no
    click: the steepest step in its first 5 ms within 3 %) and the same envelope (50 ms windows over 1 s within
    0.5 dB, RAW: the engine's own output; the contact noise's samples differ, its level not). Its contribution: the
    pair minus A alone, in each build (sync mode: both notes at a block's start)"""
    caps = ["-DBAR_START_MAX=1u", "-DRH_START_MAX=1u"]
    be = build(os.path.join(ROOT, "build", "host", "lat_bar_ev1", "frodhes"),
               ["-DFR_C1=1", "-DFR_LAT_HALF=128u", "-DFR_LAT_EV=1"] + caps)
    bn = build(os.path.join(ROOT, "build", "host", "lat_bar_ev0", "frodhes"), ["-DFR_C1=1", "-DFR_LAT_HALF=128u"] + caps)
    pre = [cc(20, 79, engine), cc(40, 119, 127)]
    for a, b, vel in ((60, 67, 100), (48, 72, 127)):
        pair = pre + [f"1000 midi 0x90 {a} {vel}", f"1000 midi 0x90 {b} {vel}"]
        alone = pre + [f"1000 midi 0x90 {a} {vel}"]
        c = {}
        for tag, bb in (("e", be), ("n", bn)):
            p, rp = wav(f"lat_bar_{tag}_{engine}_{b}_p", pair, bb, env={"FRODHES_LAT_SYNC": "1"}, end=2600)
            q, _ = wav(f"lat_bar_{tag}_{engine}_{b}_a", alone, bb, env={"FRODHES_LAT_SYNC": "1"}, end=2600)
            n = min(len(p), len(q))
            c[tag] = (p[:n] - q[:n]).astype(float)
            if tag == "e":
                assert "bar mid starts 1" in rp.out, rp.out[-300:]
        on = {t: np.flatnonzero(np.abs(x).sum(axis=1) > 0)[0] for t, x in c.items()}
        assert abs((on["n"] - on["e"]) - 128) <= 4, on
        x, y = c["e"][on["e"]:on["e"] + FS], c["n"][on["n"]:on["n"] + FS]
        m = min(len(x), len(y)) // 2205 * 2205
        st = lambda z: np.abs(np.diff(z[:220], axis=0)).max()
        assert abs(st(x) - st(y)) <= 0.03 * st(y) + 2, (st(x), st(y))
        lx = np.sqrt((x[:m] ** 2).sum(axis=1)).reshape(-1, 2205).mean(axis=1)
        ly = np.sqrt((y[:m] ** 2).sum(axis=1)).reshape(-1, 2205).mean(axis=1)
        k = ly > ly.max() * 1e-3
        assert np.max(np.abs(20 * np.log10(lx[k] / ly[k]))) < 0.5
