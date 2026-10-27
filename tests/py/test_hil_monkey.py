# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_monkey.py without the FM-1: the plan (--dry-run), the stream's filter (MIDI only, no developer CC), and
the audio gates on host renders of its takes (the stream, the panic, the probe as the FM-1 gets them): clean takes
pass; injected faults (an underrun's held frames, a block played twice, a dead probe, a short recording, full
scale) fail their own row; at 48 kHz full scale fails from 0.9.7 (usb.c's knee); before it (--old-48k) the resampler's overshoot (#38's
fail-1022) is information and an audible clamp (a run, a spike) still fails. About 15 s."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_crackle as hc  # noqa: E402
import hil_monkey as hm  # noqa: E402


def test_dry_run_plans_and_checks_every_message(capsys):
    pytest.importorskip("mido")                         # (the dry run checks every message with mido; CI has none)
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
    return hm.host_take(1000, lead_ms=int(1000 * hm.LEAD_S))          # (as the device run cuts its takes)


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


# fail-1022 (#38): 48 kHz, L at 14.9627 s and its neighbours, a 6-12 kHz burst the limiter kept at 24000 that
# usb.c's resampler overshot to full scale (the one clamped sample: -32768)
F1022 = [-24398, -23680, 9181, 20179, 19925, -8837, -32768, -596, 22072, 22175, 10616, -27963, -19568]


def _burst(rate, n=4800, at=2400):
    """a quiet take (-40 dBFS) with fail-1022's burst: 2 ms of a 9 kHz tone at 26000, its peak sample clamped"""
    t = np.arange(n) / rate
    x = np.round(100 * np.sin(2 * np.pi * 440 * t)).astype(np.int64)
    w = slice(at - rate // 1000, at + rate // 1000)
    x[w] = np.round(26000 * np.sin(2 * np.pi * 9000 * t[w])).astype(np.int64)
    x[at - 6:at + 7] = F1022
    return np.stack([x, x], axis=1)


def test_the_resamplers_overshoot_is_information_at_48k_before_097():
    rows = hm.clamp_rows(_burst(48000), 48000, strict48=False)
    assert _status(rows, "USB at full scale") == "info" and not [r for r in rows if r[3] == "FAIL"], rows


def test_full_scale_fails_at_48k_from_097():
    """usb.c's knee (#38b): fail-1022's clamped sample is a FAIL now (the default, STRICT48)"""
    rows = hm.clamp_rows(_burst(48000), 48000)
    assert hm.STRICT48 and _status(rows, "USB at full scale") == "FAIL"
    x = _burst(48000)
    x[x == -32768] = -32000
    assert _status(hm.clamp_rows(x, 48000), "USB at full scale") == "ok"


def test_the_same_clamp_fails_at_44k1():
    assert _status(hm.clamp_rows(_burst(44100), 44100), "USB at full scale") == "FAIL"


def test_a_spike_fails_at_48k():
    x = _burst(48000)
    x[4000, 0] = 32767                                    # in the quiet: no loud passage around it
    rows = hm.clamp_rows(x, 48000)
    assert _status(rows, "USB isolated clamps") == "FAIL" and _status(rows, "USB clamped run") == "ok"


def test_an_overload_fails_at_48k():
    t = np.arange(4800) / 48000
    x = np.clip(np.round(60000 * np.sin(2 * np.pi * 1000 * t)), -32768, 32767).astype(np.int64)
    rows = hm.clamp_rows(np.stack([x, x], axis=1), 48000)
    assert _status(rows, "USB clamped run") == "FAIL" and _status(rows, "USB clamped frames in 10 ms") == "FAIL"


def test_a_clamp_now_and_then_in_a_loud_high_passage_fails_when_dense():
    t = np.arange(4800) / 48000
    x = np.round(26000 * np.sin(2 * np.pi * 9000 * t)).astype(np.int64)
    x[::48] = 32767                                       # 10 per 10 ms, each alone
    rows = hm.clamp_rows(np.stack([x, x], axis=1), 48000)
    assert _status(rows, "USB clamped frames in 10 ms") == "FAIL" and _status(rows, "USB clamped run") == "ok"


def test_rejudge_reads_a_saved_take(take, tmp_path, capsys):
    xi, ev = take
    p = tmp_path / "fail-1000.npz"
    np.savez_compressed(p, usb=xi.astype(np.int16), fs=44100, events=hm.json.dumps(ev), counters="{}", lead=hm.LEAD_S)
    assert hm.main(["--rejudge", str(p)]) == 0
    assert "fail-1000.npz: ok" in capsys.readouterr().out


@pytest.fixture(scope="module")
def take1012():
    hm.monkey.build()
    xi, ev = hm.host_take(1012, lead_ms=int(1000 * hm.LEAD_S))
    x = xi.copy()
    x[:int((hm.LEAD_S + 0.75) * 44100)] = 0     # the FM-1's take (2026-10-26): the first note-on (0.05 s) silent,
    return x, ev                                # the first sound the second's (0.756 s)


def test_a_silent_first_note_does_not_move_the_probe(take1012):
    x, ev = take1012
    rows = hm.judge_audio(x, 44100, ev)
    assert _status(rows, "the probe note") == "ok", rows
    assert [r[1] for r in rows if r[0].startswith("note-ons silent")] == [1]
    assert not [r for r in rows if r[3] == "FAIL"], rows


def test_a_dead_probe_after_a_silent_first_note_fails(take1012):
    x, ev = take1012
    x = x.copy()
    x[int((hm.SEG_S + hm.PROBE_AT) * 44100):] = 0
    assert _status(hm.judge_audio(x, 44100, ev), "the probe note") == "FAIL"


def test_a_clean_take_keeps_its_fit(take):
    xi, ev = take
    assert not [r for r in hm.judge_audio(xi, 44100, ev) if r[0].startswith("note-ons silent")]
