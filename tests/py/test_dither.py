# SPDX-License-Identifier: GPL-3.0-only
"""The TPDF dither where the engines' Q20 mix is cut to Q15 (rhodes.c RH_DITH, rh_q15 and the side's last stage;
bars.c bar_render; GitHub #16). Rounding alone left a decaying held note's cut error tied to the note: its odd
harmonics stood at a fixed level (the FM-1's jack: H3 / H5 at ~-113 dBFS while H1 fell -42 -> -76 dBFS). Dithered,
they fall into a steady noise floor. The silence stays exact digital zero (the codec's mute, the silence gate,
rh_quiet / bar_quiet / fx_quiet), before a note and once it has ended, SPREAD's side included."""
import numpy as np
import pytest
import hostrun as H

FS = 44100
F_A2 = 440 * 2 ** ((45 - 69) / 12)


def held(eng, vel, hold_s, extra=()):
    lines = ["50 midi 0xB0 119 127"] + list(extra)
    if eng == "vibes":
        lines.append("60 midi 0xB0 79 0")
    elif eng == "marimba":
        lines.append("60 midi 0xB0 79 127")
    lines += [f"300 midi 0x90 45 {vel}", f"{300 + int(hold_s * 1000)} midi 0x80 45 0"]
    end = 300 + int(hold_s * 1000) + 2500
    r = H.run(f"dither-{eng}-{vel}-{len(extra)}", lines, end)
    return np.asarray(r.L, float), np.asarray(r.R, float)


def harmonics(x, t, w=1.0, ks=(1, 3, 5)):
    """dBFS of harmonics ks of the note in the 1 s window from t (Hann; f0 found within 3 % of A2)"""
    seg = x[int(t * FS):int((t + w) * FS)]
    win = np.hanning(len(seg))
    X = np.abs(np.fft.rfft(seg * win)) / (np.sum(win) / 2) / 32768
    f = np.fft.rfftfreq(len(seg), 1 / FS)
    m = (f > F_A2 * 0.97) & (f < F_A2 * 1.03)
    f1 = f[m][np.argmax(X[m])]
    return [20 * np.log10(X[(f > k * f1 * 0.995) & (f < k * f1 * 1.005)].max() + 1e-12) for k in ks]


def test_a_decaying_held_notes_cut_error_is_noise():
    """a held Rhodes A2 at velocity 30, 14 s: where H1 has fallen to -75 .. -90 dBFS, H3 and H5 lie at the dither's
    noise (under -125 dBFS a bin, 1 s Hann). Rounded without dither they rose to -105 .. -107 dBFS there (H1 -85),
    a fixed distortion of the cut that the note's decay does not take down"""
    L, R = held("rhodes", 30, 14.0)
    seen = 0
    for t in np.arange(8.0, 13.5, 1.0):
        h1, h3, h5 = harmonics(L, 0.3 + t)
        if -90 < h1 < -75:
            seen += 1
            assert h3 < -125 and h5 < -125, (t, h1, h3, h5)
    assert seen >= 2


@pytest.mark.parametrize("eng,extra", [("rhodes", ("70 midi 0xB0 105 107",)), ("vibes", ()), ("marimba", ())])
def test_silence_is_still_exact_zero(eng, extra):
    """before the note and once it has ended every sample is exactly 0 on both channels (Rhodes with SPREAD: the
    side's own dither stops with the engine)"""
    L, R = held(eng, 127, 1.5, extra)
    pre = slice(int(0.1 * FS), int(0.29 * FS))
    assert not np.any(L[pre]) and not np.any(R[pre])
    tail = slice(len(L) - int(0.4 * FS), len(L))
    assert not np.any(L[tail]) and not np.any(R[tail]), (eng, np.abs(L[tail]).max(), np.abs(R[tail]).max())
    assert np.abs(L[int(0.4 * FS):int(0.8 * FS)]).max() > 1000              # (and the note did sound)
