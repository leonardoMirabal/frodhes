# SPDX-License-Identifier: GPL-3.0-only
"""The USB audio IN hand-off's timing (tests/py/uac_timing.py: TIMER5's 10 kHz ticks folded by an interrupts-off
stretch, platform.c's 2 kHz poll and its nesting in the render, the host's 1 ms IN tokens, EP4's TxPktRdy and sticky
UnderRun, uac_service's count). The 0.9.9 candidate's gate failures ("USB missed 1", T1 S3 and monkey seed 3000):
each take also had a late audio ISR entry (122 / 144 us: interrupts held off, a flash slice), and the ring's high
point one packet up (a token that took nothing). Today's logic:

- no stretch: no miss at any token phase, at least ~500 us of slack before each packet's IN token;
- a stream (re)start alone never counts a miss;
- the shortest stretch that loses a token at the worst placement is ~520 us, ~240 us when the host's token moves
  150 us within the frame; a flash slice (fm1_flash.h FL_SLICE_US + 50 us) leaves ~220 us of slack, none at 150 us
  of token movement; tests/flash_split_test.c's "USB ISR wait <= 700 us" is above the limit;
- a long stall counts 1 however many tokens it loses (the UnderRun bit latches).

A fix to the poll / service logic: give simulate() / sweep() / critical() its own `timer5` (the shape of
timer5_today) and compare; timer5_every_tick is a reference candidate (10 kHz service: ~920 us)."""
import re
from pathlib import Path

import uac_timing as u

ROOT = Path(__file__).resolve().parents[2]


def flash_slice_us():
    h = (ROOT / "firmware" / "hal" / "fm1_flash.h").read_text()
    return int(re.search(r"#define FL_SLICE_US\s+(\d+)u", h).group(1)) + 50   # "never off longer than ~SLICE + 50 us"


def test_nominal_margin():
    worst, bad, counted, n = u.sweep(0, step=20)
    assert bad == 0 and counted == 0, (bad, counted)
    assert worst >= 480, worst                 # a service every 500 us: at least ~500 us before the next token


def test_render_nesting_changes_nothing():
    """the service nested in a render (usb_due only) and the poll it owes after: the same margins"""
    assert u.sweep(300, step=20, audio=(5100, 2500))[:3] == u.sweep(300, step=20)[:3]
    assert u.sweep(300, step=20, audio=(4900, 4900))[1] == 0


def test_stream_start_alone_never_misses():
    for start in range(0, 2000, 70):
        for first in (2, 3, 5, 30):
            for ph in range(0, 1000, 90):
                r = u.simulate(u.Case(ms=40, tok_phase=ph, start_at=start, first_frame=first))
                assert r.true_missed == 0 and r.counted == 0, (start, first, ph)


def test_today_reproduces_missed_1():
    """one interrupts-off stretch of 600 us at an unlucky place: one IN token takes nothing, the firmware counts 1
    (the gate's "USB missed 1"); the same stretch at a lucky place: nothing"""
    r = u.simulate(u.Case(ms=14, tok_phase=20, cli=((5500, 600),)))
    assert (r.true_missed, r.counted) == (1, 1), r
    r = u.simulate(u.Case(ms=14, tok_phase=520, cli=((5500, 600),)))
    assert (r.true_missed, r.counted) == (0, 0), r


def test_today_critical_stretch():
    c0 = u.critical()
    c150 = u.critical(jitter=150)
    print(f"today: critical stretch {c0} us (fixed token), {c150} us (token moving 150 us)")
    assert 480 <= c0 <= 560, c0
    assert 200 <= c150 <= 280, c150
    assert u.sweep(700, step=20)[1] > 0          # flash_split_test's 700 us "USB ISR wait" budget loses tokens


def test_today_flash_slice_margin():
    L = flash_slice_us()
    worst, bad, _, _ = u.sweep(L, step=20)
    print(f"today: a {L} us flash slice: worst slack {worst} us")
    assert bad == 0 and 150 <= worst <= 300, worst
    assert u.sweep(L, step=20, jitter=150)[1] > 0   # no margin left for 150 us of token movement


def test_counter_latches_once():
    """a 3.2 ms stall loses 3 tokens; the firmware counts 1 (one UnderRun latch, read when the next packet goes)"""
    r = u.simulate(u.Case(ms=20, tok_phase=300, cli=((5100, 3200),)))
    assert r.true_missed == 3 and r.counted == 1, r


def test_ten_khz_candidate_margin():
    c0 = u.critical(timer5=u.timer5_every_tick)
    worst, bad, _, _ = u.sweep(flash_slice_us(), step=20, jitter=150, timer5=u.timer5_every_tick)
    print(f"10 kHz service: critical stretch {c0} us; a flash slice with 150 us token movement: slack {worst} us")
    assert c0 >= 880 and bad == 0, (c0, worst)
