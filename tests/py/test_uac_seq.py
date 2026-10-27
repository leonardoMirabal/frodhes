# SPDX-License-Identifier: GPL-3.0-only
"""The 44.1 kHz USB audio IN path frame by frame (0.9.7, T18: a single stereo frame seen twice on one FM-1 monkey
take, every counter clean): tests/uac_seq_test.c runs usb.c's own uac_render_start / uac_tap / uac_packet AND
uac_service (the EP4 queue against a model of its TXCSR1) for simulated hours, with TIMER5 preempting the render
inside usb.c at every RING_PUBLISH, the host's IN token jittered +-200 us, skipped now and then, gone for up to 60 ms
in a row (stalls, overruns), and landing inside uac_service's own SIE accesses. Each frame is a counter.

- This tree: no frame twice unless its packet counted an underrun, no frame missing unless an overrun counted it
  (or a stall re-primed the ring), packets of 43..46 frames, in every case.
- The check is not blind: usb.c with a planted bug (ua_r one short once per 100000 packets: one frame sent twice,
  no counter) fails it, as seed 3024's copy would.
- An SIE access that times out (the INDEX write, the TXCSR1 read: sie_rd returns 0): this tree takes it as "still
  queued" and counts uac.sie_to (CC 116 item 21), so nothing is lost; v0.9.6 (git archive; skipped where the tag is
  missing) took the 0 for "free" and queued a new packet over the waiting one: its frames lost, no counter."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "uac_test")
HOURS = os.environ.get("UAC_SEQ_HOURS", "0.25")


def build(src, exe, *defs):
    os.makedirs(OUT, exist_ok=True)
    subprocess.run(["cc", "-O1", "-w", *defs, "-I" + src, "-o", exe, os.path.join(ROOT, "tests", "uac_seq_test.c")],
                   check=True)
    return exe


def cases(exe, *args):
    p = subprocess.run([exe, *args], capture_output=True, text=True)
    rows = []
    for ln in p.stdout.splitlines():
        if ln.startswith("case "):
            t = ln.split()[1:]
            rows.append({t[i]: float(t[i + 1]) for i in range(0, len(t), 2)})
    assert rows, p.stdout + p.stderr
    return rows, p.returncode


@pytest.fixture(scope="module")
def new():
    return cases(build(os.path.join(ROOT, "firmware", "src"), os.path.join(OUT, "uac_seq_test")), "--hours", HOURS)


def test_no_frame_twice_or_lost_uncounted(new):
    rows, rc = new
    assert len(rows) == 12
    bad = [r for r in rows if r["dup_uncounted"] or r["drop_uncounted"] or r["n_bad"]]
    assert not bad and rc == 0, bad[:1]


def test_the_model_reaches_every_path(new):
    rows, _ = new
    assert all(r["stalls"] > 0 and r["overruns"] > 0 and r["gaps_counted"] > 0 for r in rows), rows[:1]
    assert all(r["adj_up"] > 0 and r["adj_down"] > 0 for r in rows)
    assert all(43 <= r["nmin"] and r["nmax"] <= 46 for r in rows)


def test_a_planted_copy_is_caught():
    tree = os.path.join(OUT, "planted", "firmware")
    shutil.rmtree(os.path.join(OUT, "planted"), ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "firmware", "src"), os.path.join(tree, "src"))
    shutil.copytree(os.path.join(ROOT, "firmware", "hal"), os.path.join(tree, "hal"))
    p = os.path.join(tree, "src", "usb.c")
    s = open(p).read()
    s2 = s.replace("    ua_r = r + k;\n", "    ua_r = r + k - (k && uac.pkts % 100000u == 7u);\n")
    assert s2 != s, "uac_packet's index update moved: update this test"
    open(p, "w").write(s2)
    rows, rc = cases(build(os.path.join(tree, "src"), os.path.join(OUT, "uac_seq_planted")), "--hours", "0.1")
    assert rc != 0 and all(r["dup_uncounted"] > 0 for r in rows), rows[:1]


def test_sie_timeout_waits_and_is_counted():
    exe = build(os.path.join(ROOT, "firmware", "src"), os.path.join(OUT, "uac_seq_sie"))   # (its own: xdist)
    rows, rc = cases(exe, "--hours", "0.1", "--sie-fault", "0.0005")
    assert rc == 0 and all(r["sie_to"] > 0 and r["overwritten"] == 0 and r["drop_uncounted"] == 0
                           and r["dup_uncounted"] == 0 for r in rows), rows[:1]


def test_sie_timeout_lost_a_packet_in_096():
    tree = os.path.join(OUT, "v096")
    tar = os.path.join(OUT, "v096.tar")
    os.makedirs(OUT, exist_ok=True)
    if subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "-o", tar, "v0.9.6", "firmware"],
                      capture_output=True).returncode:
        pytest.skip("no v0.9.6 in this checkout")
    shutil.rmtree(tree, ignore_errors=True)
    os.makedirs(tree)
    subprocess.run(["tar", "-xf", tar, "-C", tree], check=True)
    exe = build(os.path.join(tree, "firmware", "src"), os.path.join(OUT, "uac_seq_v096"), "-DUAC_OLD")
    rows, rc = cases(exe, "--hours", "0.05", "--sie-fault", "0.0005")
    assert rc != 0 and all(r["overwritten"] > 0 and r["drop_uncounted"] > 0 and r["dup_uncounted"] == 0
                           for r in rows), rows[:1]
