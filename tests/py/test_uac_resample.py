# SPDX-License-Identifier: GPL-3.0-only
"""The independent proof of the locked USB stream's resampler (usb.c uac_tap_lock, FELUCCA_UAC_LOCK 1) against exact
references (tests/py/uacrs.py): every output's input position replayed in exact integers from the ppm schedule, a
sine's reference the sine itself there, any other signal's a Kaiser-windowed sinc interpolation there.

1. A sweep of 30 tones, 20 Hz .. 20 kHz (log steps), at 0, +450, +600 and -600 ppm, both host rates (tests/uac_rs_test.c):
   the THD+N of each tone against its exact reference is no more than 1 dB worse than the path before the lock (uac_tap48
   at exactly 160 / 147, 48 kHz: tests/uac_rs_alt_test.c -DFELUCCA_UAC_LOCK=0) through the same measure. Both tables
   are printed and written to build/uac_test/uac_resample_sweep_<rate>.<worker>.txt.
2. A ramp of the FM-1's clock, -600 -> +600 ppm (10 s; 60 s with --long or UAC_RS_LONG=1), the step set every block,
   with a 1 kHz tone, an 8 kHz tone (both TPDF-dithered: no rounding spurs of the test signal) and the host's piano
   render (build/host/frodhes.wav):
   - no click: no glitch-detector event (tests/py/glitch_lpc.py, the FM-1 takes' detector) that the exact reference
     confirms, and no sample whose error against the reference stands out of the held runs' error at the same place;
     with --long the detector's own rate <= 1.4 events a minute (a clean take) too. The check is shown to find a bad,
     a repeated, a dropped and a zeroed sample (test_click_check_catches_injected);
   - no zipper: the error in 0.5 s windows of the input along the ramp within 1 dB of the same window held at -600
     or +600 ppm (the tones' against the exact tone, the piano's against its sinc reference);
   - no sideband within +-1 kHz of a tone above the limit (1 s windows, Blackman-Harris; test_ramp_sidebands):
     -78 dBc at 44.1 kHz, -94 dBc at 48 kHz (the measured worst + 3 dB; SIDEBAND_LIMIT says why).
3. The outputs' count: exactly the replayed count, within 2 of a perfect stream's (the rate over the ppm), at both
   lengths of a run (no drift).
4. The harness tells a bad resampler: the diagnostic (FELUCCA_UAC_LOCK 2, one frame dropped or repeated over 32)
   fails test 1 at 10 kHz.

  python3 -m pytest -q -n 2 tests/py/test_uac_resample.py               (fast: ~15 s with 2 workers)
  python3 -m pytest -q -n 2 tests/py/test_uac_resample.py --long -rA    (the 60 s ramp; -rA or -s: the tables)
"""
import os

import numpy as np
import pytest
from scipy.ndimage import maximum_filter1d

import glitch_lpc
import uacrs as u

FREQS = [int(f) for f in np.round(np.geomspace(20, 20000, 30))]
PPMS = [0, 450, 600, -600]
RATES = [48000, 44100]
# The sideband limits (dBc within +-1 kHz of a tone): the measured worst + 3 dB of margin, the resampler kept as it is
# for 0.9.9. Measured (2026-11-07, uac_tap_lock at a0025e99), worst of the ramp and the held runs, 10 s and 60 s:
# - 44.1 kHz: -81.6 (held +5 ppm) / -81.8 (the 60 s ramp), -84 to -85 held at +-600 ppm: the 12-tap FIR's image of
#   the tone (at fs - f) folded back to 44100 x ppm from it (20 - 27 Hz at 450 - 600 ppm) as the fraction of a phase
#   sweeps; the path before the lock had no resampler at 44.1 kHz (none);
# - 48 kHz: -97.6 (held +5 ppm, 8 kHz) / -99.5 (the 60 s ramp, 8 kHz), spurs a few hundred Hz from the tone (the
#   int16 phase table); the path before the lock -104.
# Both sit within the tone's critical band, where its masking is about -25 dB: inaudible. More image rejection costs
# CPU the audio ISR does not have. A limit stricter than -100 dBc wants a longer FIR (or a finer table) first.
SIDEBAND_LIMIT = {44100: -78.0, 48000: -94.0}


@pytest.fixture(scope="module")
def long_run(request):
    return bool(request.config.getoption("--long", default=False)) or os.environ.get("UAC_RS_LONG") == "1"


@pytest.fixture(scope="module")
def drv():
    return u.drivers()


@pytest.fixture(scope="module")
def sweep_sig():
    return u.sweep(FREQS)


@pytest.fixture(scope="module")
def baseline(drv, sweep_sig):
    """the path before the lock: uac_tap48 at 160 / 147, through the same measure"""
    x, spans = sweep_sig
    y, _ = u.run(drv["old"], x, 48000, 0)
    return u.sweep_thdn(y, u.nominal_positions(len(y), 48000, 0), FREQS, spans)


def table(title, rows):
    lines = [title, "  Hz     " + "".join(f"{f:>7d}" for f in FREQS)]
    lines += [f"  {lab:<7s}" + "".join(f"{v:7.1f}" for v in vals) for lab, vals in rows]
    return "\n".join(lines)


@pytest.mark.parametrize("rate", RATES)
def test_sweep_vs_path_before_lock(drv, sweep_sig, baseline, rate):
    x, spans = sweep_sig
    rows, worst = [("before", baseline)], []
    for ppm in PPMS:
        y, _ = u.run(drv["lock"], x, rate, ppm)
        t, n = u.positions(len(x), rate, ppm)
        assert n == len(y), "the replayed time base and the driver disagree on the outputs' count"
        r = u.sweep_thdn(y, t, FREQS, spans)
        rows.append((f"{ppm:+d}", r))
        worst += [(v - b, f, ppm, v, b) for f, v, b in zip(FREQS, r, baseline)]
    txt = table(f"THD+N dB against the exact reference, the lock at {rate} Hz (before: uac_tap48 160/147 at 48 kHz)",
                rows)
    d, f, ppm, v, b = max(worst)
    txt += f"\n  worst against before: {d:+.2f} dB at {f} Hz, {ppm:+d} ppm ({v:.1f} against {b:.1f})"
    print("\n" + txt)
    os.makedirs(u.OUT, exist_ok=True)
    with open(os.path.join(u.OUT, f"uac_resample_sweep_{rate}.{u.WORKER}.txt"), "w") as fh:
        fh.write(txt + "\n")
    assert d <= 1.0, txt


@pytest.mark.parametrize("rate", RATES)
def test_diagnostic_fails_at_10k(drv, rate):
    """the drop-or-repeat diagnostic is plainly worse than the path before the lock at 10 kHz (the harness discriminates);
    the lock, through the same measure, is not"""
    x = u.sine(10000, int(u.FS * 1.5))
    y0, _ = u.run(drv["old"], x, 48000, 0)
    t0 = u.nominal_positions(len(y0), 48000, 0)
    i = u.span(t0, 0.25 * u.FS, 1.25 * u.FS)
    base = u.thdn(y0[i], t0[i], 10000)
    for ppm in (450, 600, -600):
        y, err = u.run(drv["diag"], x, rate, ppm)
        t = u.nominal_positions(len(y), rate, ppm)
        i = u.span(t, 0.25 * u.FS, 1.25 * u.FS)
        bad = u.thdn(y[i], t[i], 10000)
        yl, _ = u.run(drv["lock"], x, rate, ppm)
        tl, _ = u.positions(len(x), rate, ppm)
        i = u.span(tl, 0.25 * u.FS, 1.25 * u.FS)
        good = u.thdn(yl[i], tl[i], 10000)
        print(f"{rate} {ppm:+d} ppm 10 kHz: before {base:.1f}  diagnostic {bad:.1f} ({err.strip()})  lock {good:.1f}")
        assert bad > base + 1.0, f"the harness did not catch the diagnostic at {ppm} ppm: {bad:.1f} against {base:.1f}"
        assert good <= base + 1.0


_RUNS = {}


def ramp_runs(drv, rate, sig, dur):
    """the input, the ramp, the two held ends and a held +5 ppm (the fraction of a phase nearly fixed over a 0.5 s
    window: the error at each fraction, which the ramp near 0 ppm also dwells on): {label: (output, input position of
    each output)} (once a worker)"""
    key = (rate, sig, dur)
    if key not in _RUNS:
        n = int(u.FS * dur)
        x = u.piano(n) if sig == "piano" else dithered_sine(1000 if sig == "1k" else 8000, n, 7)
        out = {}
        for lab, args in (("ramp", (-600, 600, dur)), ("-600", (-600,)), ("+600", (600,)), ("+5", (5,))):
            y, _ = u.run(drv["lock"], x, rate, *args)
            t, cnt = u.positions(len(x), rate, *args)
            assert cnt == len(y)
            out[lab] = (y, t)
        _RUNS[key] = (x, out)
    return _RUNS[key]


def ref_error(y, t, x, sig, d):
    """output - exact reference: the sine at the replayed positions (fitted in each 0.5 s window of the input), or
    the piano's sinc reference at them (delay d)"""
    if sig == "piano":
        return y - u.sinc_ref(x, t - d)
    f = 1000 if sig == "1k" else 8000
    e = np.full(len(y), np.nan)
    for i in u.windows(t, 0.5):
        e[i] = u.fit_tone(y[i], t[i], f)[1]
    return e


def clicks(y, t, runs, x, sig, rate, d):
    """the ramp's output y (input positions t) checked for clicks two ways (not in its first or last 0.1 s):
    - the glitch detector (glitch_lpc): its events (input positions, s); those it does not also find on the exact
      reference (rounded: the ideal resampler's output; the detector can score on the signal itself, a piano's quiet
      decay, where the output is the reference to the LSB) nor on the input itself (its attacks); and those the
      reference confirms (the error within 3 ms
      above 4 LSB and 6x its robust level in the 20 ms before);
    - the reference: where |error| exceeds 3x the held runs' error (their running maximum over +-2 ms, at the same
      input position: the same content, the step held) + 4 LSB (a bad, repeated, dropped or zeroed sample; a jump)
    and the detector's count on the input itself"""
    e = ref_error(y, t, x, sig, d)
    lo, hi = rate // 10, len(y) - rate // 10
    env = np.zeros(len(y))
    for lab in ("-600", "+600", "+5"):
        yh, th = runs[lab]
        eh = maximum_filter1d(np.nan_to_num(np.abs(ref_error(yh, th, x, sig, d))), int(0.004 * rate) + 1)
        env = np.maximum(env, np.interp(t, th[1:], eh[1:]))
    over = np.nonzero(np.nan_to_num(np.abs(e)) > 3.0 * env + 4.0)[0]
    over = over[(over > lo) & (over < hi)]
    ref = []
    for k in over:
        if not ref or t[k] - ref[-1] > 0.003 * u.FS:
            ref.append(float(t[k]))
    ev = glitch_lpc.events(y, rate)
    ev = ev[(ev > lo) & (ev < hi)]
    ideal = np.round(np.nan_to_num(y - e))                 # (the exact reference, rounded as the output is)
    evi = t[glitch_lpc.events(ideal, rate)]
    evx = glitch_lpc.events(np.asarray(x, dtype=float), u.FS)    # (2026-11-08: the input's own, at input positions:
    h, raw, real, extra = int(0.003 * rate), [], [], []          #  0.9.10's sharper attacks score on the piano itself)
    for k in ev:
        raw.append(round(float(t[k]) / u.FS, 3))
        if not (len(evi) and np.min(np.abs(evi - t[k])) < 0.003 * u.FS) and \
                not (len(evx) and np.min(np.abs(evx - t[k])) < 0.003 * u.FS):
            extra.append(raw[-1])
        before = np.abs(e[max(0, k - 7 * h):k - h])
        if np.nanmax(np.abs(e[k - h:k + h])) > max(4.0, 6.0 * 1.4826 * np.nanmedian(before)):
            real.append(raw[-1])
    return raw, extra, real, [round(p / u.FS, 3) for p in ref], len(evx)


def piano_delay(runs, x, rate):
    y, t = runs["+600"]
    m = slice(rate, rate + rate // 2)
    return u.fit_delay(y[m], x, t[m])


def dithered_sine(f, n, seed):
    rng = np.random.default_rng(seed)
    v = 0.5 * 32767 * np.sin(2 * np.pi * f / u.FS * np.arange(n) + 0.3) + rng.random(n) - rng.random(n)
    return np.round(v)


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("sig", ["1k", "8k", "piano"])
def test_ramp_no_click_no_zipper(drv, long_run, rate, sig):
    dur = 60.0 if long_run else 10.0
    x, runs = ramp_runs(drv, rate, sig, dur)
    y, t = runs["ramp"]
    d = piano_delay(runs, x, rate) if sig == "piano" else None
    raw, extra, real, ref, nx = clicks(y, t, runs, x, sig, rate, d)
    if sig == "piano":
        e = {lab: u.snr_windows(yy, u.sinc_ref(x, tt - d), tt) for lab, (yy, tt) in runs.items()}
        what = f"delay {d:.4f} frames; error against the sinc reference"
    else:
        f = 1000 if sig == "1k" else 8000
        e = {lab: u.thdn_windows(yy, tt, f) for lab, (yy, tt) in runs.items()}
        what = "THD+N against the exact tone"
    held = ("-600", "+600", "+5")
    nw = min(len(v) for v in e.values())
    if sig == "piano":                                   # the same window of the piano held (its content sets the error)
        ref_e = np.fmax.reduce(np.stack([e[lab][:nw] for lab in held]), 0)
    else:                                                # a tone: the worst held window (any fraction of a phase)
        ref_e = np.full(nw, max(np.nanmax(e[lab]) for lab in held))
    worst = np.nanmax(e["ramp"][:nw] - ref_e)
    allowed = int(1.4 * dur / 60.0)
    print(f"{rate} {sig} {dur:.0f}s: {what}, 0.5 s windows: ramp worst {np.nanmax(e['ramp']):.1f} dB, held worst "
          f"{np.nanmax(ref_e):.1f} dB, ramp - held worst {worst:+.2f} dB; glitch detector {raw} (on the input itself "
          f"{nx}), not on the ideal output {extra} ({len(extra) / dur * 60:.1f}/min), confirmed by the reference {real}; "
          f"clicks against the reference {ref}")
    assert not real and not ref, f"clicks at {real} / {ref} s (input positions)"
    assert len(extra) <= allowed, f"the glitch detector: {extra} (more than 1.4 a minute)"
    assert worst <= 1.0, f"a zipper: a window {worst:.2f} dB worse than held"


@pytest.mark.parametrize("sig", ["1k", "piano"])
def test_click_check_catches_injected(drv, sig):
    """the click check finds what it must: one bad sample, two repeated, one dropped, one zeroed (glitch.py's
    --inject kinds), put into the 48 kHz ramp's output one at a time, are each found (by the detector and confirmed, or against the reference)"""
    x, runs = ramp_runs(drv, 48000, sig, 10.0)
    y, t = runs["ramp"]
    d = piano_delay(runs, x, 48000) if sig == "piano" else None
    missed = []
    rms = np.sqrt(np.convolve(y ** 2, np.ones(1001) / 1001, "same"))
    for j, p in enumerate((60000, 150000, 250000, 380000)):
        p += int(np.nonzero(rms[p:] > 1000)[0][0]) + 500         # (in the piano: where it sounds; a sample dropped in
        z, kind = y.copy(), j % 4                            # its silence changes nothing; 2026-11-08: + the window's
                                                             # half, past the onset: 0.9.10's sharper attack put a drop
                                                             # 4 ms before one, found where it starts, 6.744 s)
        amp = np.std(z[p - 500:p + 500]) + 50
        if kind == 0: z[p] += amp * 1.5
        elif kind == 1: z[p:p + 2] = z[p - 2:p]
        elif kind == 2: z[p:] = np.concatenate([z[p + 1:], z[-1:]])
        else: z[p] = 0
        raw, extra, real, ref, _ = clicks(z, t, runs, x, sig, 48000, d)
        if not any(abs(r * u.FS - t[p]) < 0.003 * u.FS for r in real + ref):
            missed.append((kind, round(t[p] / u.FS, 3), raw, real, ref))
    assert not missed, missed


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("sig", ["1k", "8k"])
def test_ramp_sidebands(drv, long_run, rate, sig):
    """no component within +-1 kHz of the tone above SIDEBAND_LIMIT, along the ramp and in the held runs"""
    dur = 60.0 if long_run else 10.0
    f = 1000 if sig == "1k" else 8000
    x, runs = ramp_runs(drv, rate, sig, dur)
    k0 = rate // 10
    sb = {lab: u.sidebands(yy[k0:], tt[k0:], f, rate, None, rate) for lab, (yy, tt) in runs.items()}
    yo, _ = u.run(drv["old"], x, 48000, 0)
    so = u.sidebands(yo[4800:], u.nominal_positions(len(yo), 48000, 0)[4800:], f, 48000, None, 48000).max()
    print(f"{rate} {sig} {dur:.0f}s: sidebands (+-1 kHz, 1 s windows) ramp worst {sb['ramp'].max():.1f} dB, held "
          f"-600 {sb['-600'].max():.1f}, +600 {sb['+600'].max():.1f}, +5 {sb['+5'].max():.1f}; the path before the lock "
          f"(48 kHz) {so:.1f}")
    lab = max(sb, key=lambda k: sb[k].max())
    assert sb[lab].max() < SIDEBAND_LIMIT[rate], f"a sideband at {sb[lab].max():.1f} dB of the tone ({lab})"


@pytest.mark.parametrize("rate", RATES)
def test_count_nominal_no_drift(drv, long_run, rate):
    """the outputs' count is the replayed one and a perfect stream's to within 2, after a run and after twice it"""
    secs = (30.0, 60.0) if long_run else (5.0, 10.0)
    rng = np.random.default_rng(3)
    for sched in ([0], [450], [600], [-600], [-600, 600, secs[0]]):
        errs = []
        for s in secs:
            n = int(u.FS * s)
            x = np.round(rng.standard_normal(n) * 3000)
            y, _ = u.run(drv["lock"], x, rate, *sched)
            _, cnt = u.positions(n, rate, *sched)
            nom = u.nominal_count(n, rate, *sched)
            assert len(y) == cnt, (sched, s, len(y), cnt)
            errs.append(len(y) - nom)
        print(f"{rate} {sched}: outputs - perfect stream after {secs[0]:.0f} s {errs[0]:+.3f}, after {secs[1]:.0f} s "
              f"{errs[1]:+.3f}")
        assert max(abs(e) for e in errs) <= 2.0 and abs(errs[1] - errs[0]) <= 2.0, (sched, errs)


def test_reference_exact():
    """the sinc reference of a sine at fractional positions is the sine (< -110 dB): the references are exact enough"""
    t = np.arange(500, 40000) * 0.918734 + 0.37
    for f in (100, 5000, 15000):
        x = 0.5 * 32767 * np.sin(2 * np.pi * f / u.FS * np.arange(44100) + 0.3)
        ex = 0.5 * 32767 * np.sin(2 * np.pi * f / u.FS * t + 0.3)
        r = u.sinc_ref(x, t)
        assert 10 * np.log10(((r - ex) ** 2).mean() / (ex ** 2).mean()) < -110.0, f
