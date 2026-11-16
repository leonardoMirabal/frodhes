# SPDX-License-Identifier: GPL-3.0-only
"""RESPONSE (CC 40) on the bar engines (bars.c bar_lt_g, 0.9.10; the law 0.9.13): a level map. FULL (A) leaves VIBES
and MARIMBA as they were; LIGHT (B) lifts every strike under v127 by 0.07 dB a velocity step (7 and below as 7: +8.4
dB at v7, 0 at v127; until 0.9.12 the electric piano's old FULL soft end, +9 dB at v20, 0 from v60, which made VIBES
softer from v24 to v40: test_response_monotonic.py), on the strike's pulse and its contact noise only, so the tone is
the same:
- B / A level per velocity: bar_lt_g's integers in dB, within 0.25 dB under v127, byte-identical at v127;
- the same tone: every 1/3-octave band within 30 dB of the loudest and above -80 dBFS in A moves with the level, within
  0.25 dB. (The bar engine leaves out a mode while its strike level is under 1 LSB, bars.c's budget: B's louder soft
  strike lights the partials A's left under the 16-bit floor, as any louder strike does; at MARIMBA's v1 that is
  +0.2 dB of level and the bands A had under -80 dBFS);
- the tail after the key-up keeps its share of the note (no louder release under B), within 0.25 dB;
- the HQ build (the Move, the plugin) likewise."""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402
from hostrun import FS  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import dry_dist  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_src = open(os.path.join(ROOT, "firmware", "src", "bars.c")).read()
LT_UP = int(re.search(r"#define BAR_LT_UP (\d+)u", _src).group(1))
LT_VLO = int(re.search(r"#define BAR_LT_VLO (\d+)u", _src).group(1))


def lt_db(v):
    """bar_lt_g's lift at velocity v, dB"""
    g = 32768
    for _ in range(127 - max(v, LT_VLO) if v < 127 else 0):
        g = (g * LT_UP + 16384) >> 15
    return 20 * np.log10(g / 32768)
BARS = {"VIBES": 0, "MARIMBA": 100}                      # CC 79 (frodhes.c: VIBES 0..95, MARIMBA 96..127)
HQBIN = os.path.join(ROOT, "build", "host-hq", "frodhes")
FS_DB = 20 * np.log10(32768 * FS / 4)                    # bands(): a full-scale sine's band (the Hann window, 1 s)


def render(binary, typ, resp, key, vel):
    ev = dry_dist.type_base_lines(40) + [f"50 midi 0xB0 79 {BARS[typ]}",   # the voicing at its defaults, not F01's (dry_dist.TYPE_BASE)
           "100 midi 0xB0 111 127", f"120 midi 0xB0 40 {resp}",
          f"400 midi 0x90 {key} {vel}", f"1400 midi 0x80 {key} 0"]
    tag = f"bars_resp_{os.path.basename(os.path.dirname(binary))}_{typ}_{resp}_{key}_{vel}"
    r = hostrun.run(tag, ev, 2400, binary=binary)
    return np.stack([r.L, r.R], 1).astype(float)


def db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-12)


def bands(x):
    s = np.abs(np.fft.rfft(x.mean(1) * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / FS)
    edges = 50 * 2 ** (np.arange(0, 28) / 3)
    return np.array([10 * np.log10(s[(f >= lo) & (f < hi)].sum() + 1e-12) for lo, hi in zip(edges[:-1], edges[1:])])


def builds():
    out = [hostrun.BIN]
    if os.path.exists(HQBIN):
        out.append(HQBIN)
    return out


@pytest.mark.parametrize("typ", sorted(BARS))
@pytest.mark.parametrize("key", [48, 72])
def test_light_is_the_level_map(typ, key):
    for b in builds():
        for v in (1, 20, 30, 40, 50, 59, 60, 96, 120, 127):
            a, l = render(b, typ, 0, key, v), render(b, typ, 127, key, v)
            if v >= 127:
                assert np.array_equal(a, l), f"{typ} k{key} v{v} ({b}): LIGHT differs from FULL at v127"
                continue
            note = slice(int(0.4 * FS), int(1.4 * FS))
            want = lt_db(v)
            got = db(l[note]) - db(a[note])
            assert abs(got - want) <= 0.25, f"{typ} k{key} v{v} ({b}): LIGHT {got:+.2f} dB over FULL, the map {want:+.2f}"
            ba, bl = bands(a[note]), bands(l[note])
            m = (ba >= ba.max() - 30) & (ba >= FS_DB - 80)
            dev = np.abs((bl - ba)[m] - want)
            assert dev.max() <= 0.25, f"{typ} k{key} v{v} ({b}): a band moves {dev.max():.2f} dB off the level map"
            tail = slice(int(1.48 * FS), int(1.68 * FS))
            ka, kl = db(a[tail]) - db(a[note]), db(l[tail]) - db(l[note])
            assert kl - ka <= 0.25, f"{typ} k{key} v{v} ({b}): the tail {kl - ka:+.2f} dB louder re the note under LIGHT"
