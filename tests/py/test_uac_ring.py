# SPDX-License-Identifier: GPL-3.0-only
"""The USB audio IN ring (usb.c) under the FM-1's timing: tests/uac_test.c runs the firmware's own uac_render_start /
uac_tap / uac_packet against a model of the audio ISR (a render of 0.5-5.4 ms, then the tap and the 48 kHz
resampler), TIMER5's 2 kHz USB poll (nested in the render) and the host's 1 ms IN tokens, the FM-1 450-540 ppm fast,
exact and slow, at 44.1 and 48 kHz.

- This tree: 0 underruns, 0 missed packets, 0 overruns, the 44.1 kHz stream exact frame by frame, a packet and more
  in the ring at its lowest, in every case.
- v0.9.4 (git archive; skipped where the tag is missing): the same model must underrun once the render is long (the
  user's clicks), and not at a short render (the model is not simply harsh).
- The latency the fix adds (Little's law: the mean fill over the rate), at a short render where 0.9.4 is clean:
  one block, 5.8 ms."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "uac_test")
OLD_REF = os.environ.get("UAC_OLD_REF", "v0.9.4")      # the last release with the render-start band


def build(src_root, exe):
    subprocess.run(["cc", "-O1", "-w", "-I" + os.path.join(src_root, "firmware", "src"), "-o", exe,
                    os.path.join(ROOT, "tests", "uac_test.c")], check=True)
    return exe


def cases(exe, *args):
    out = subprocess.run([exe, *args], check=True, capture_output=True, text=True).stdout
    rows = []
    for ln in out.splitlines():
        if ln.startswith("case "):
            t = ln.split()[1:]
            rows.append({t[i]: float(t[i + 1]) for i in range(0, len(t), 2)})
    assert rows, out
    return rows


@pytest.fixture(scope="module")
def new():
    os.makedirs(OUT, exist_ok=True)
    return cases(build(ROOT, os.path.join(OUT, "uac_test")))


@pytest.fixture(scope="module")
def old():
    tree = os.path.join(OUT, "old")
    tar = os.path.join(OUT, "old.tar")
    os.makedirs(OUT, exist_ok=True)
    r = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "-o", tar, OLD_REF, "firmware"],
                       capture_output=True)
    if r.returncode:
        pytest.skip(f"no {OLD_REF} in this checkout")
    shutil.rmtree(tree, ignore_errors=True)          # (a stale tree from another UAC_OLD_REF)
    os.makedirs(tree)
    subprocess.run(["tar", "-xf", tar, "-C", tree], check=True)
    return cases(build(tree, os.path.join(OUT, "uac_test_old")), "--quick")


def test_ring_never_runs_dry(new):
    bad = [r for r in new if r["underruns"] or r["missed"] or r["overruns"] or r["seq_bad"]]
    assert not bad, f"{len(bad)} cases glitch, e.g. {bad[0]}"
    assert len(new) == 2 * 4 * 12 * 5


def test_packet_in_hand_at_the_low_point(new):
    low = min(r["low"] for r in new)
    assert low >= 49, f"the ring's low point {low} frames: less than a packet in hand"


def test_old_band_fails_at_long_renders(old):
    long_ = [r for r in old if r["rend"] + r["tail"] >= 2500 and r["ppm"] > 0]
    assert long_ and all(r["underruns"] > 0 for r in long_), \
        [r for r in long_ if not r["underruns"]][:1]
    short = [r for r in old if r["rend"] <= 500]
    assert short and not any(r["underruns"] for r in short)


def test_added_latency_is_one_block(new, old):
    for rate in (44100, 48000):
        def mean_fill(rows):
            v = [r["fill_mean"] for r in rows if r["rate"] == rate and r["rend"] == 500 and r["ppm"] == 540]
            return sum(v) / len(v)
        ms = (mean_fill(new) - mean_fill(old)) / rate * 1000
        assert 5.3 < ms < 6.3, f"{rate} Hz: the ring adds {ms:.2f} ms"
