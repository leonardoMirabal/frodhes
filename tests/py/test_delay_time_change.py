# SPDX-License-Identifier: GPL-3.0-only
"""BUGHUNT #3 (0.9.6): a DELAY TIME change under ringing echoes.

- DIG glided its time one sample a sample: while TIME rose the read head stood still (one held sample, a DC level,
  the repeats lost for over a second); falling, the echoes played an octave up. Now the old tap and the new one
  crossfade over 23 ms (dly_tape.c dly_fade, dly_tx).
- ANA's time followed (target - d) / 4096 a sample: a big jump scrubbed the line at several times speed, a burst
  far over the echoes (+26 dB). Its slew is now at most 1/16 sample a sample while echoes ring: a bend.

A staccato chord, MIX 127, FDBK 100, TIME 50; at 1.5 s TIME 127 (or 127 -> 50). 100 ms RMS windows."""
import numpy as np
import pytest

import hostrun as hr

W = int(0.1 * hr.FS)


def env(r):
    x = np.diff((r.L + r.R) / 2)                       # (the first difference: a held sample, DC, counts as silence)
    n = len(x) // W
    return 20 * np.log10(np.sqrt(np.mean(x[:n * W].reshape(n, W) ** 2, 1)) + 1e-3)


def take(tag, eff, t0, t1, binary=hr.BIN):
    """a staccato chord every 120 ms from 0.3 to 1.4 s (the line full of echoes), MIX 127, FDBK 100, TIME t0; at 1.5 s
    TIME t1"""
    ls = [hr.cc(20, 88, eff), hr.cc(22, 89, 127), hr.cc(24, 26, 100), hr.cc(26, 90, t0)]
    for k in range(10):
        for n in (48, 55, 60, 64):
            ls += hr.note(300 + 120 * k, n + (k % 3) * 2, 100, 60)
    if t1 is not None:
        ls.append(hr.cc(1500, 90, t1))
    return env(hr.run(tag, ls, 3500, binary=binary))


@pytest.mark.parametrize("t0,t1", [(64, 100), (100, 64)])
def test_dig_time_change_keeps_the_echoes(t0, t1):
    """the 0.8 s after the change: no 100 ms window more than 12 dB under the same echoes' mean level with no change
    (0.9.5: 64 -> 100 -31 dB, the read head standing still: a held sample)"""
    a, b = take(f"dlt_dig_{t0}_{t1}", 48, t0, t1), take(f"dlt_dig_{t0}", 48, t0, None)
    d = a[16:24] - b[16:24].mean()
    assert d.min() > -12, d.round(1)


@pytest.mark.parametrize("t0,t1", [(64, 127), (127, 64), (127, 30)])
def test_ana_time_change_is_a_bend_not_a_burst(t0, t1):
    """no 100 ms window after the change more than 3 dB over the loudest of the same takes held at either TIME (0.9.5:
    127 -> 64 +22 dB, the line scrubbed at several times speed)"""
    e = take(f"dlt_ana_{t0}_{t1}", 80, t0, t1)
    ref = max(take(f"dlt_ana_{t}", 80, t, None)[3:34].max() for t in (t0, t1))
    assert e[15:34].max() < ref + 3, (e[15:34].max(), ref)
