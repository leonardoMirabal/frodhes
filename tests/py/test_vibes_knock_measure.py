# SPDX-License-Identifier: GPL-3.0-only
"""vibes_decay's "NOISE knock 48 -> 127" row (tools/hil_scan.py knock_db) on host renders whose key-up moves against
the NOISE 0 reference take, as it does on the FM-1: the engine takes a MIDI event at the next 256-frame block (5.8 ms),
so a 1.5 s hold lasts 1.5 s +- one block from take to take. The row failed at random (the FM-1 scan of 2026-10-18
19:35, A4: USB -0.15 dB, jack +0.23 dB; it passed at 15:37 on another build with the same render; the host scan with
seeds 2 5 6 7 fails at 6ed3e22 and at dcbdac1 alike): when the reference's key-up came a block earlier, the NOISE
take's window held a few ms more of the undamped note than the reference's, an excess of -25 dB re the strike at
NOISE 48 and 127 alike, which buried the knock. The measure now compares with the reference's windows shifted over
that slack (the per-bin maximum), so the note no longer counts and the knock does; a knock that is not there (NOISE 0
against NOISE 0, shifted) is still not found."""
import json
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

import hostrun  # noqa: E402  (tests/py: the per-worker render paths)
from scipy.signal import resample_poly

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_scan as h  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = h.FS
NZ = h.vp()["NOISE"]["cc"]
DE = h.vp()["NOISE"]["de"]


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def take(noise, key, up_ms):
    """VRig.reset (DRY, VIBES, the motor off, NOISE) + CC 10, key v100 at 0.5 s, let go up_ms ms after 2.0 s;
    resampled to FS: (mono, the onset index as the suite finds it)"""
    s = ["20 midi 0xB0 81 0", "21 midi 0xB0 91 0", "60 midi 0xB0 79 64", "61 midi 0xB0 47 0", f"62 midi 0xB0 {NZ} {noise}",
         "450 midi 0xB0 10 127", f"500 midi 0x90 {key} 100", f"{2000 + up_ms} midi 0x80 {key} 0", "2600 end"]
    out = hostrun.out(f"knock-{noise}-{key}-{up_ms}")
    with open(out + ".txt", "w") as f:
        f.write("\n".join(s) + "\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0].astype(float)
    m = resample_poly(x, 160, 147)
    return m, h.align(m, [0.5])[0][0]


@pytest.mark.parametrize("seed", (2, 5, 6, 7))
def test_the_suite_row_on_the_host(seed):
    """the suite itself on the host build: the seeds whose latencies put a key-up a block later than the reference's
    (seed 2: A4 let go at 5508 ms in the NOISE 0 take, 5509 in the NOISE 48 one, a block boundary between)"""
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_scan.py"), "--host", str(seed), "--only", "vibes_decay"],
                   capture_output=True, cwd=ROOT)        # (exit 1 on a failed row: the rows say which)
    rows = json.load(open(os.path.join(ROOT, "build", "hil", "vibes-host.json")))["suites"]["vibes_decay"]
    bad = [(r["check"], r["setting"], r["usb"]) for r in rows if r["check"].startswith("NOISE") and not r["ok"]]
    assert not bad, (seed, bad)


@pytest.mark.parametrize("key", h.VKEYS)
@pytest.mark.parametrize("ref_ms,up_ms", [(0, 0), (0, 6), (6, 0), (3, 9), (9, 3)])
def test_knock_grows_with_noise_whatever_the_block(key, ref_ms, up_ms):
    r, ro = take(0, key, ref_ms)
    a, ao = take(DE, key, up_ms)
    b, bo = take(127, key, up_ms)
    d = h.knock_db(b, bo, r, ro) - h.knock_db(a, ao, r, ro)
    assert d >= 6, (key, ref_ms, up_ms, d)


@pytest.mark.parametrize("key", h.VKEYS)
@pytest.mark.parametrize("ref_ms,up_ms", [(0, 6), (6, 0)])
def test_no_knock_is_not_found(key, ref_ms, up_ms):
    """NOISE 0 against NOISE 0 a block apart: what the measure finds is far under the knock at NOISE 127"""
    r, ro = take(0, key, ref_ms)
    z, zo = take(0, key, up_ms)
    b, bo = take(127, key, up_ms)
    assert h.knock_db(b, bo, r, ro) - h.knock_db(z, zo, r, ro) >= 6, (key, ref_ms, up_ms)
