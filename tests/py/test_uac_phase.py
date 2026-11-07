# SPDX-License-Identifier: GPL-3.0-only
"""The EP4 poll on its own phase (0.9.9, usb.c FELUCCA_UAC_PHASE, on in platform.c): the USB audio IN endpoint holds
one packet, so the next one must be queued between the IN token that takes the last and the next frame's token. The
2 kHz poll at a fixed phase leaves 0..500 us of that frame unused, and an interrupts-off stretch (a flash slice) of
~520 us may lose a frame ("USB missed 1"). uac_tick keeps the same 2 kHz poll but moves its phase to just after the
token (an early look now and then, looks on the ticks after a late poll, a lock-on look when the stream starts or
resumes) and runs a poll whose time a stretch covered as soon as the stretch ends.

- the timing model (tests/py/uac_timing.py, usb-review's) with tests/py/uac_phase.py, the C logic step for step:
  the critical stretch ~920 us (today 520), ~620 us with 150 us of token movement (today 240); a flash slice with
  150 us of token movement loses nothing; a stream start alone counts nothing; a render changes nothing; the poll
  follows a token that drifts either way;
- usb.c itself (tests/uac_seq_test.c built with -DFELUCCA_UAC_PHASE=1, uac_tick every 0.1 ms): no frame twice or lost
  uncounted in every case (stalls, overruns, SIE time-outs), packets of 43..46 frames; under 700 us stretches every
  ~50 ms the phased poll loses no token where the fixed one loses hundreds; the extra EP4 polls stay under ~6 %."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import uac_timing as u                       # noqa: E402
import uac_phase as p                        # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "uac_test"
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
PH = p.timer5_phase


def flash_slice_us():
    import re
    h = (ROOT / "firmware" / "hal" / "fm1_flash.h").read_text()
    return int(re.search(r"#define FL_SLICE_US\s+(\d+)u", h).group(1)) + 50


# ---- the timing model


def test_critical_stretch():
    c0, c150 = u.critical(timer5=PH), u.critical(timer5=PH, jitter=150)
    print(f"phase: critical stretch {c0} us (fixed token), {c150} us (token moving 150 us); today 520 / 240")
    assert c0 >= 880 and c150 >= 580, (c0, c150)


def test_flash_slice_with_token_movement():
    worst, bad, counted, _ = u.sweep(flash_slice_us(), step=20, jitter=150, timer5=PH)
    assert bad == 0 and counted == 0 and worst >= 250, worst


def test_no_stretch_no_miss():
    worst, bad, counted, _ = u.sweep(0, step=20, timer5=PH)
    assert bad == 0 and counted == 0 and worst >= 480, worst


def test_render_nesting_changes_nothing():
    assert u.sweep(800, step=20, audio=(5100, 2500), timer5=PH)[1] == 0
    assert u.sweep(800, step=20, audio=(4900, 4900), timer5=PH)[1] == 0


def test_stream_start_alone_never_misses():
    for start in range(0, 2000, 70):
        for first in (2, 3, 5, 30):
            for ph in range(0, 1000, 90):
                r = u.simulate(u.Case(ms=40, tok_phase=ph, start_at=start, first_frame=first, timer5=PH))
                assert r.true_missed == 0 and r.counted == 0, (start, first, ph)


@pytest.mark.parametrize("drift", [0.3, -0.3])
def test_follows_a_drifting_token(drift):
    """the token drifts 0.3 us a frame (300 ppm against TIMER5), an 850 us stretch every ~37 ms at a random place:
    today loses tokens, the phased poll none"""
    import random
    rnd = random.Random(1)
    j = {n: int(drift * n) for n in range(2400)}
    cli = tuple((k * 37000 + rnd.randrange(1000), 850) for k in range(1, 60))
    ph0 = rnd.randrange(1000)
    r = u.simulate(u.Case(ms=2300, tok_phase=ph0, tok_jitter=j, cli=cli, timer5=PH))
    t = u.simulate(u.Case(ms=2300, tok_phase=ph0, tok_jitter=j, cli=cli))
    assert r.true_missed == 0 and t.true_missed > 0, (r.true_missed, t.true_missed)


# ---- usb.c itself (tests/uac_seq_test.c)


def build(phase):
    os.makedirs(OUT, exist_ok=True)
    exe = str(OUT / f"uac_seq_phase{phase}-{WORKER}")
    subprocess.run(["cc", "-O1", "-w", f"-DFELUCCA_UAC_PHASE={phase}", "-I" + str(ROOT / "firmware" / "src"),
                    "-o", exe, str(ROOT / "tests" / "uac_seq_test.c")], check=True)
    return exe


def cases(exe, *args):
    r = subprocess.run([exe, *args], capture_output=True, text=True)
    rows = []
    for ln in r.stdout.splitlines():
        if ln.startswith("case "):
            t = ln.split()[1:]
            rows.append({t[i]: float(t[i + 1]) for i in range(0, len(t), 2)})
    assert rows, r.stdout + r.stderr
    return rows, r.returncode


@pytest.fixture(scope="module")
def exes():
    return build(0), build(1)


def test_phase_frames_exact(exes):
    rows, rc = cases(exes[1], "--hours", "0.1")
    assert rc == 0 and len(rows) == 12 and all(r["phase"] == 1 for r in rows)
    assert not [r for r in rows if r["dup_uncounted"] or r["drop_uncounted"] or r["n_bad"] or r["overwritten"]]
    assert all(r["stalls"] > 0 and r["overruns"] > 0 and r["adj_up"] > 0 and r["adj_down"] > 0 for r in rows)


def test_phase_sie_timeout_waits(exes):
    rows, rc = cases(exes[1], "--hours", "0.05", "--sie-fault", "0.0005")
    assert rc == 0 and all(r["sie_to"] > 0 and r["overwritten"] == 0 and r["drop_uncounted"] == 0
                           and r["dup_uncounted"] == 0 for r in rows), rows[:1]


def test_stretches_lose_no_token(exes):
    """700 us interrupts-off stretches every ~50 ms, the token within +-10 us: the fixed 2 kHz poll loses tokens
    (counted as missed), the phased poll none; the frames stay exact in both"""
    fixed, rc0 = cases(exes[0], "--hours", "0.02", "--cli", "700", "50", "--tj", "20")
    phased, rc1 = cases(exes[1], "--hours", "0.02", "--cli", "700", "50", "--tj", "20")
    assert rc0 == 0 and rc1 == 0
    t0, t1 = sum(r["true_missed"] for r in fixed), sum(r["true_missed"] for r in phased)
    print(f"700 us stretches: fixed poll {t0:.0f} tokens lost ({sum(r['missed'] for r in fixed):.0f} counted), "
          f"phased {t1:.0f}")
    assert t0 > 50 and t1 == 0, (t0, t1)
    assert all(r["cli"] > 1000 for r in phased)


def test_extra_polls(exes):
    """the polls and looks: at most ~6 % above the fixed 2 kHz (realistic token jitter), ~15 % at +-200 us"""
    base = cases(exes[0], "--hours", "0.01", "--tj", "20")[0]
    ph = cases(exes[1], "--hours", "0.01", "--tj", "20")[0]
    ph400 = cases(exes[1], "--hours", "0.01")[0]
    b = max(r["polls_per_s"] for r in base)
    m, m400 = max(r["polls_per_s"] for r in ph), max(r["polls_per_s"] for r in ph400)
    print(f"EP4 polls a second: fixed {b:.0f}, phased {m:.0f} (+-10 us jitter), {m400:.0f} (+-200 us)")
    assert m <= 1.06 * b and m400 <= 1.15 * b, (b, m, m400)


# ---- part 2: each flash slice right after a USB packet (usb.c uac_slice_ok, fm1_flash.h FL_SLICE_OK)


def test_aligned_slice_trains(exes):
    """an erase's slice train (FL_SLICE_US-like stretches, 750 us between them) of 600 us slices, continuous: at random
    moments the fixed poll loses thousands of tokens and the phased one some; each slice held for uac_slice_ok (the
    real function, in the C model): the phased poll none, the slices still ~half as many (the erase ~2x the wall
    time, in the background)"""
    args = ("--hours", "0.005", "--cli", "600", "0.75", "--cli-exact", "--tj", "20")
    free0, _ = cases(exes[0], *args)
    free1, _ = cases(exes[1], *args)
    al1, rc = cases(exes[1], *args, "--align")
    t = [sum(r["true_missed"] for r in x) for x in (free0, free1, al1)]
    n_free, n_al = sum(r["cli"] for r in free1), sum(r["cli"] for r in al1)
    print(f"600 us slice train: tokens lost fixed {t[0]:.0f}, phased {t[1]:.0f}, phased + aligned {t[2]:.0f}; "
          f"slices aligned / free {n_al / n_free:.2f}")
    assert rc == 0 and t[0] > 100 and t[2] == 0, t
    assert n_al >= 0.45 * n_free, (n_al, n_free)
    assert not [r for r in al1 if r["dup_uncounted"] or r["drop_uncounted"] or r["n_bad"]]


def test_aligned_slices_timing_model():
    """the timing model: slices placed right after a queued packet: 800 us slices lose nothing with the phased poll
    (today's fixed poll loses tokens from ~800 us even so)"""
    bad = {}
    for name, pol in (("today", None), ("phase", PH)):
        bad[name] = 0
        for ph in range(0, 1000, 25):
            base = u.simulate(u.Case(ms=45, tok_phase=ph, timer5=pol))
            cli, t = [], 5000
            for s in base.services:
                if s >= t and len(cli) < 20:
                    cli.append((s + 2, 800))
                    t = s + 2 + 800 + 750
            bad[name] += u.simulate(u.Case(ms=45, tok_phase=ph, cli=tuple(cli), timer5=pol)).true_missed > 0
    assert bad["phase"] == 0 and bad["today"] > 0, bad


def test_save_adds_no_service_gap(exes):
    """the probe's measure (item 40, the longest time between two EP4 polls), on a settings save's slice train
    (282 us slices, 750 us apart), timing only (no host skips): at random moments a slice that starts just before a
    poll stretches the gap to 500 + 282 us (the FM-1: 759-911 us with the fixed poll, 696-783 with the phased one
    alone); each slice right after a packet (uac_slice_ok) and the phased poll: the nominal 500 us, the save adds
    nothing"""
    args = ("--hours", "0.003", "--clean", "--tj", "20", "--cli", "282", "0.75", "--cli-exact")
    free = max(r["gap_max_us"] for r in cases(exes[1], *args)[0])
    al = max(r["gap_max_us"] for r in cases(exes[1], *args, "--align")[0])
    print(f"save slices: the longest EP4 poll gap {free:.0f} us at random moments, {al:.0f} us aligned")
    assert free >= 700 and al <= 520, (free, al)


def test_sie_accesses_per_block(exes):
    """the cost inside the audio ISR (the polls nest in it; an SIE access is a CON1 round trip): the SIE accesses in
    the worst 256-frame block, +-200 us token jitter. The phased poll's looks and early looks read TXCSR1 alone (INDEX
    written only when another access changed it), so its worst block has no more accesses than the fixed poll's"""
    fixed, rc0 = cases(exes[0], "--hours", "0.02")
    phased, rc1 = cases(exes[1], "--hours", "0.02")
    assert rc0 == 0 and rc1 == 0
    m0, m1 = max(r["sie_blk_max"] for r in fixed), max(r["sie_blk_max"] for r in phased)
    print(f"SIE accesses in the worst block: fixed {m0:.0f}, phased {m1:.0f}")
    assert m1 <= m0, (m0, m1)
