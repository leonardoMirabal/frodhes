# SPDX-License-Identifier: GPL-3.0-only
"""A critical-band loudness proxy for level matching (the level make-ups: tools/drive_makeup.py,
tools/velmap_makeup.py). Per critical band (Zwicker's 24) the mean square (Parseval), an upward spread of masking of
-25 dB per band, Zwicker's specific-loudness law over Terhardt's threshold in quiet, summed (sone-like); 0 dBFS sine =
CAL dB SPL. For relative comparisons only (not a standard loudness). A uniform gain scales every band's power, so the
gain that matches a reference's loudness is solved on the band powers alone (no re-render)."""
import math

import numpy as np

FS = 44100
EDGES = [20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400,
         5300, 6400, 7700, 9500, 12000, 15500]
CAL = 100.0
_FC = np.sqrt(np.array(EDGES[:-1], float) * np.array(EDGES[1:], float))
_K = _FC / 1000.0
_ET = 10 ** ((3.64 * _K ** -0.8 - 6.5 * np.exp(-0.6 * (_K - 3.3) ** 2) + 1e-3 * _K ** 4) / 10)   # threshold, power
_N = len(EDGES) - 1
_SPREAD = np.array([[10 ** (-2.5 * (j - i)) if j > i else (1.0 if i == j else 0.0) for j in range(_N)]
                    for i in range(_N)])


def bands(seg, nfft=1 << 16):
    """the band mean squares of a segment (full scale 1.0)"""
    seg = np.asarray(seg, float)
    nfft = max(nfft, len(seg))
    w = np.hanning(len(seg))
    X = np.abs(np.fft.rfft(seg * w, nfft)) ** 2 * 2.0 / (nfft * np.sum(w * w))
    f = np.fft.rfftfreq(nfft, 1.0 / FS)
    return np.array([X[(f >= lo) & (f < hi)].sum() + 1e-30 for lo, hi in zip(EDGES[:-1], EDGES[1:])])


def loud(b, gain_db=0.0):
    """the sone-like total of band powers b with a gain (dB) applied"""
    e = np.asarray(b, float) * 10 ** (gain_db / 10) * 10 ** ((CAL + 3.01) / 10)
    es = e @ _SPREAD
    return float(np.maximum(0.08 * _ET ** 0.23 * ((0.5 + 0.5 * es / _ET) ** 0.23 - 1), 0).sum())


def phon(b, gain_db=0.0):
    """loud() as phon-like (10 log2 sone)"""
    return 10 * math.log2(max(loud(b, gain_db), 1e-6))


def match_db(b, target):
    """the gain (dB) that brings band powers b to the loudness target (a loud() value): bisection (monotonic)"""
    lo, hi = -40.0, 40.0
    for _ in range(40):
        m = 0.5 * (lo + hi)
        if loud(b, m) < target:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)
