# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_monkey.py without the FM-1: the plan (--dry-run), the stream's filter (MIDI only, no developer CC), and
the audio gates on host renders of its takes (the stream, the panic, the probe as the FM-1 gets them): clean takes
pass; injected faults (an underrun's held frames, a block played twice, a dead probe, a short recording, full
scale) fail their own row. About 15 s."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_crackle as hc  # noqa: E402
import hil_monkey as hm  # noqa: E402


def test_dry_run_plans_and_checks_every_message(capsys):
    assert hm.main(["--dry-run", "--minutes", "1"]) == 0
    out = capsys.readouterr().out
    assert "every message valid" in out and "no MIDI or audio opened" in out


def test_the_stream_is_midi_only_without_developer_ccs():
    assert hm.midi_of("turn 1 3") is None and hm.midi_of("btn 2") is None and hm.midi_of("keys 5") is None
    assert hm.midi_of("midi 0xB0 116 127") is None and hm.midi_of("midi 0xB3 119 127") is None
    assert hm.midi_of("midi 0xF0 1 2") is None
    assert hm.midi_of("midi 0xC2 7 0") == [0xC2, 7] and hm.midi_of("midi 0xF8 0 0") == [0xF8]
    seg = hm.segment(1000)
    assert seg == hm.segment(1000) and all(0 <= t <= hm.SEG_S for t, _ in seg)
    assert not [b for _, b in seg if b[0] >> 4 == 0xB and b[1] in hm.DEV_CC]
    assert any(b[0] >> 4 == 0x9 for _, b in seg) and any(b[0] == 0xF8 for _, b in seg)


@pytest.fixture(scope="module")
def take():
    hm.monkey.build()
    return hm.host_take(1000)


def _status(rows, check):
    return [r[3] for r in rows if r[0].startswith(check)][0]


def test_a_clean_take_passes(take):
    xi, ev = take
    rows = hm.judge_audio(xi, 44100, ev)
    assert not [r for r in rows if r[3] == "FAIL"], rows


@pytest.mark.parametrize("kind", ["hold", "block256"])
def test_an_injected_usb_glitch_fails(take, kind):
    xi, ev = take
    u, _, marks = hc.inject(kind, xi, None, ev, np.random.default_rng(3), k=3)
    rows = hm.judge_audio(np.round(u).astype(np.int64), 44100, ev)
    assert _status(rows, "USB repeat / stale") == "FAIL"


def test_a_dead_probe_fails(take):
    xi, ev = take
    x = xi.copy()
    x[int((hm.SEG_S + hm.PROBE_AT) * 44100):] = 0
    assert _status(hm.judge_audio(x, 44100, ev), "the probe note") == "FAIL"


def test_a_stream_that_stopped_fails(take):
    xi, ev = take
    assert _status(hm.judge_audio(xi[:len(xi) // 2], 44100, ev), "frames recorded") == "FAIL"


def test_full_scale_fails(take):
    xi, ev = take
    x = xi.copy()
    x[44100 * 5, 0] = 32767
    assert _status(hm.judge_audio(x, 44100, ev), "USB at full scale") == "FAIL"
