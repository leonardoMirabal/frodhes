# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_midi_parity.py (USB-MIDI against the TRS MIDI input) on the host: the script covers every event kind,
the comparison passes two equal paths and fails a path 1 dB quieter, one that drops the pitch bend and one that
plays a note on another channel; the start-sound fingerprint matches itself and not another program; the dry run."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_midi_parity as MP  # noqa: E402

FS = 22050


def _m(**kw):
    x, marks, sends = MP.synth(FS, **kw)
    return MP.measure(x, FS, marks, sends)


def test_script_covers_every_event():
    ev, marks, dur = MP.script()
    kinds = {m[0] for _, _, m, _ in ev}
    assert kinds == {"on", "off", "cc", "pc", "bend"}
    assert {k for _, k, _ in marks} == {"note", "release", "sustain", "shape", "echo", "bend", "silent"}
    assert any(m == ("off", 60, 10) for _, _, m, _ in ev) and any(m == ("off", 60, 127) for _, _, m, _ in ev)
    assert [d for _, d, _, _ in ev].count("wrong") == 2 and dur < 40


@pytest.fixture(scope="module")
def ref():
    return _m(seed=1)


@pytest.mark.parametrize("kw,want,word", ((dict(seed=2, lat=0.014), "PASS", None),
                                         (dict(seed=3, gain_db=-1.0), "FAIL", "dB"),
                                         (dict(seed=3, gain_db=-0.3), "PASS", None),
                                         (dict(seed=4, no_bend=True), "FAIL", "bend"),
                                         (dict(seed=5, channel_heard=True), "FAIL", "another channel")))
def test_compare(ref, kw, want, word):
    v, rows, probs, med = MP.compare(ref, _m(**kw))
    assert v == want, probs
    if word:
        assert any(word in p for p in probs)


def test_channel_all_is_manual(ref):
    v, rows, probs, med = MP.compare(ref, _m(seed=5, channel_heard=True), channel_all=True)
    row = next(r for r in rows if r["event"] == "channel")
    assert row["ok"] is None and "MANUAL" not in v and v == "PASS"


def test_fingerprint_distance():
    x, marks, sends = MP.synth(FS, seed=1)
    a = MP.fingerprint(x, FS, sends["vel127"])
    b = MP.fingerprint(x, FS, sends["program"])
    assert MP.fp_distance(a, a) == 0.0 and MP.fp_distance(a, b) > 0.5


def test_dry_run_and_selftest(capsys):
    assert MP.main(["--dry-run", "--port", "X"]) == 0
    assert "bend_up" in capsys.readouterr().out
    assert MP.selftest(verbose=False, fs=FS)


@pytest.mark.parametrize("echo_db,want", ((1.0, "FAIL"), (-1.0, "FAIL"), (0.2, "PASS")))
def test_delay_echo_against_its_dry(ref, echo_db, want):
    """a TRS echo 1 dB louder (or quieter) than USB's, the dry notes equal: the delay row FAILs; 0.2 dB passes"""
    v, rows, probs, med = MP.compare(ref, _m(seed=6, echo_db=echo_db))
    row = next(r for r in rows if r["event"] == "delay")
    assert (row["ok"] and v == "PASS") == (want == "PASS"), (row, probs)
    assert len(row["per_probe_db"]) == MP.DLY_PROBES
