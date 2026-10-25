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
    s = ["20 midi 0xB0 81 0", "21 midi 0xB0 91 0", "60 midi 0xB0 79 0", "61 midi 0xB0 47 0", f"62 midi 0xB0 {NZ} {noise}",
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


# 2026-10-25 (the key-up never louder than the note, tests/py/test_release_never_louder.py): the knock is capped at a
# share of the note's level left, at NOISE 48 and 127 alike for a note held 1.5 s, and it rings at the bar's own
# pitch, inside the note's bins, where knock_db's excess cannot see it (it reads NOISE 0's own block slack). So:
# the knock is there (NOISE 127 against NOISE 0 in the same block: the samples differ only by the noises), and
# whatever the block, knock_db never reads it over the note.
@pytest.mark.parametrize("key", h.VKEYS)
def test_knock_is_there_at_noise_127(key):
    """what NOISE 127 adds over the 150 ms after the key-up: -21 .. -35 dB of NOISE 0's release there (F3 .. F6);
    at least -45 (none: -inf)"""
    r, ro = take(0, key, 0)
    b, _ = take(127, key, 0)                    # (the same script but NOISE: the same samples' times; the found
    n = min(len(r), len(b))
    i0, i1 = ro + int(1.5 * FS), ro + int(1.65 * FS)   # onsets differ by a sample or two, the noise on the strike)
    d = (b[:n] - r[:n])[i0:i1]
    share = 10 * np.log10(np.sum(d ** 2) / np.sum(r[i0:i1] ** 2) + 1e-30)
    assert share >= -45, (key, share)


@pytest.mark.parametrize("key", h.VKEYS)
@pytest.mark.parametrize("ref_ms,up_ms", [(0, 0), (0, 6), (6, 0), (3, 9), (9, 3)])
def test_knock_never_reads_over_the_note(key, ref_ms, up_ms):
    """the suite's measure (knock_db) on NOISE 127 and the default, a block apart or not: under the note it damps"""
    r, ro = take(0, key, ref_ms)
    note = h.ms_db(h.cut(r, ro, 1.4, 1.5))
    for x in (DE, 127):
        b, bo = take(x, key, up_ms)
        assert h.knock_db(b, bo, r, ro) < note, (key, ref_ms, up_ms, x)
