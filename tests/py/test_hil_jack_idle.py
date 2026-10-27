# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_jack.py's idle gate (TEST-PLAN A5) on the numbers of real sessions: the reference is the median of the
session's silent takes, not one take (0.9.7 T2 on ecf62e54 failed a silent FM-1 on a quiet reference take)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
import hil_jack  # noqa: E402


def sil(db, hf):
    return dict(jack_db=db, jack_hf_db=hf, usb_nonzero=0)


T2_097 = [sil(-91.344, -99.874), sil(-90.895, -99.814), sil(-93.196, -99.755)]     # st77, st77 again, MARIMBA
OCT20 = [sil(-92.131, -100.010), sil(-92.538, -99.867), sil(-92.781, -99.965)]


def test_the_097_t2_session_passes_now():
    d = hil_jack.idle_judge(0, -90.810, -99.669, T2_097)
    assert d["ok"] and abs(d["jack_re_muted_db"] - 0.534) < 0.01, d
    assert abs(-90.810 - T2_097[2]["jack_db"]) > 1.0           # the old rule (MARIMBA's take alone) FAILed it


def test_the_oct20_session_still_passes():
    assert hil_jack.idle_judge(0, -92.124, -99.877, OCT20)["ok"]


def test_fm1_noise_still_fails():
    assert not hil_jack.idle_judge(0, -88.0, -99.7, T2_097)["ok"]       # 3 dB over the floor
    assert not hil_jack.idle_judge(0, -91.0, -97.0, T2_097)["ok"]       # hiss: the 8-20 kHz band up 2.9 dB
    assert not hil_jack.idle_judge(5, -91.3, -99.8, T2_097)["ok"]       # USB not exact zero
