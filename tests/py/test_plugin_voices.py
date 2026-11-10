# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's 24 voices (the HQ sound, every engine), heard through the built CLAP plugin
(plugin/tests/clap_render, 44.1 kHz, the engine alone: CC 119 RAW). The voice counts themselves (24 keys held: 24
voices sound and 24 are held, the 25th steals one) are clap_render --coretest's (tests/py/test_plugin_core.py) and,
on the host build, make testv V=24 (tests/scripts24).

- The 24th key sounds: a 24-key chord against the same chord without its top key: the top key's fundamental is there
  (>= 6 dB over the 23-key chord at that frequency, where the octave below's second harmonic sounds too) and nothing was stolen for it (the other 23 keys render the same
  but for that key: the difference is the top key alone, no fade of another voice).
- A steal is clean (pristine): the 25th key over 24 held, against the 24 held keys (no 25th) plus the 25th key alone.
  The residual is the stolen voice fading out over a block (the engine's own steal). The largest second difference of
  the steal render in the steal's window stays under 1.5 x that of the same music without the steal (the two parts'
  sum; a hard cut of a voice is a step: a second difference as large as the voice itself). The detector is proven on the same renders: the residual
  cut at the fade's end in place of faded fails it.
Skipped when the plugin is not built (make plugin)."""
import os
import subprocess
import wave

import numpy as np
import pytest

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import dry_dist  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD = os.environ.get("FRP_BUILD", os.path.join(ROOT, "build", "plugin"))
RENDER = os.path.join(BUILD, "frp_clap_render")
CLAP = os.path.join(BUILD, "bundles", "Frodhes.clap")
pytestmark = pytest.mark.skipif(not os.path.exists(RENDER) or not os.path.exists(CLAP),
                                reason="the plugin is not built (make plugin)")

FS = 44100
KEYS = [36 + 2 * k for k in range(24)]          # 36, 38 .. 82: every engine's range
K25 = 86
T_ON, T_STEAL, T_END = 100, 250, 1200           # ms (the 25th early: MARIMBA's top keys ring short)
VEL = 70                                        # (24 keys at once stay under the 16-bit clip)
ENGINES = {                                      # the TYPE's CCs (VIBES: its motor off)
    "stage77": ["50 midi 0xB0 80 0"],
    "vibes": ["50 midi 0xB0 79 0", "51 midi 0xB0 47 0"],
    "marimba": ["50 midi 0xB0 79 127"],
}


def render(tmp_path, name, engine, keys, k25=False, only25=False):
    lines = dry_dist.type_base_lines(30) + ["40 midi 0xB0 119 127"] + ENGINES[engine]   # the engine alone (RAW), the
                                                                  # voicing at its defaults (not F01's)
    if not only25:
        lines += [f"{T_ON} midi 0x90 {k} {VEL}" for k in keys]
    if k25 or only25:
        lines += [f"{T_STEAL} midi 0x90 {K25} {VEL}"]
    lines += [f"{T_END} end"]
    script = tmp_path / f"{name}.txt"
    script.write_text("\n".join(lines) + "\n")
    out = tmp_path / f"{name}.wav"
    r = subprocess.run([RENDER, CLAP, str(script), str(out), "--engine", "hq"], capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    with wave.open(str(out)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float64)
    return x.mean(axis=1)


def band_db(x, f, t0, t1):
    seg = x[int(t0 * FS / 1000):int(t1 * FS / 1000)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1 / FS)
    m = (fr > f * 0.985) & (fr < f * 1.015)
    return 20 * np.log10(spec[m].max() + 1e-9)


@pytest.mark.parametrize("engine", list(ENGINES))
def test_the_24th_key_sounds(tmp_path, engine):
    a24 = render(tmp_path, "a24", engine, KEYS)
    a23 = render(tmp_path, "a23", engine, KEYS[:-1])
    top = render(tmp_path, "top", engine, KEYS[-1:])
    f = 440.0 * 2 ** ((KEYS[-1] - 69) / 12)
    d24, d23 = band_db(a24, f, T_ON + 20, T_ON + 300), band_db(a23, f, T_ON + 20, T_ON + 300)
    assert d24 >= d23 + 6, f"{engine}: the 24th key's fundamental {d24:.1f} dB, without it {d23:.1f} dB"
    # nothing stolen for the 24th key: 24 keys = 23 keys + the top key alone (the engine sums its voices)
    n = min(len(a24), len(a23), len(top))
    res = a24[:n] - a23[:n] - top[:n]
    assert np.abs(res).max() <= 4.0, f"{engine}: 24 keys differ from 23 + the top key by {np.abs(res).max():.1f} LSB"


def d2max(x):
    return np.abs(np.diff(x, 2)).max()


@pytest.mark.parametrize("engine", list(ENGINES))
def test_steal_is_clean(tmp_path, engine):
    a = render(tmp_path, "steal", engine, KEYS, k25=True)      # 24 held, the 25th steals one
    b = render(tmp_path, "held", engine, KEYS)                 # the 24 held, no 25th
    c = render(tmp_path, "k25", engine, KEYS, only25=True)     # the 25th alone
    n = min(len(a), len(b), len(c))
    a, b, c = a[:n], b[:n], c[:n]
    w0, w1 = int((T_STEAL - 20) * FS / 1000), int((T_STEAL + 80) * FS / 1000)
    res = a - b - c                                            # the stolen voice's fade-out (negated)
    big = np.abs(res[w0:w1]).max()
    assert big > 50, f"{engine}: no voice was stolen? residual {big:.1f} LSB"
    assert np.abs(a).max() < 32000 and np.abs(b + c).max() < 32000, f"{engine}: the renders clip"
    bound = 1.5 * d2max((b + c)[w0:w1]) + 2.0                 # (the same music without the steal)
    got = d2max(a[w0:w1])
    assert got <= bound, f"{engine}: a click at the steal: second difference {got:.1f}, the bound {bound:.1f}"
    # the detector: the same residual cut at the fade's end (a voice stopped dead) must fail
    on = np.nonzero(np.abs(res[w0:w1]) > 0.01 * big)[0]
    t1 = w0 + on[0] + 256                                      # (the fade: one engine block)
    cut = res.copy()
    cut[:t1] = 0.0
    bad = d2max((b + c + cut)[w0:w1])
    assert bad > bound, f"{engine}: the detector misses a hard cut ({bad:.1f} <= {bound:.1f})"
    print(f"{engine}: steal second difference {got:.1f} (bound {bound:.1f}), a hard cut {bad:.1f}; "
          f"the stolen voice {big:.0f} LSB")
