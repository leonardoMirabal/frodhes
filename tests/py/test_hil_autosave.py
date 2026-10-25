# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_autosave.py (autosave_stream) on the host, without the FM-1: the CC 116 reply parsing (item 17 included),
the save's detection (item 17, else item 10's spacing), the gates (missed / the deadline / the interrupts-off limit /
the stream state), the old firmware's 60 s save failing with missed > 0 (the teeth), the sound take (0.9.5: a sound
change saves nothing), the plan of --dry-run."""
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_autosave as A  # noqa: E402


def reply(items, isr_us=3000):
    """a CC 116 reply as the firmware sends it (frodhes.c: CC 116/117 .., CC 108/107, then CC 100/101/102 items)"""
    m = [(116, 10), (117, 0), (118, 5), (115, 0), (108, (isr_us // 4) >> 7), (107, (isr_us // 4) & 127)]
    for i, v in items.items():
        m += [(100, i), (101, v >> 7), (102, v & 127)]
    return m


def base(rate=48000, kw=None):
    it = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 8: 200, 9: 5790, 10: 5820, 11: 0, 12: 2 | 1 | 8 | (4 if rate == 48000 else 0)}
    it.update(kw or {})
    return it


def take(saves, rate=48000, new=True, end=20.0, t_change=4.5):
    """polls every 0.5 s from 0 to end; saves {t: dict of items in that poll}; new: the firmware sends item 17"""
    polls, t = [], 0.0
    while t <= end + 1e-9:
        it = base(rate)
        if new:
            it[17] = 0
        it.update(saves.get(round(t, 1), {}))
        polls.append((t, A.parse(reply(it))))
        t += 0.5
    return polls


def status(res, check):
    return [r[3] for r in res["rows"] if r[0] == check]


def test_parse_item17_and_isr():
    p = A.parse(reply(base(kw={17: 612}), isr_us=4100))
    assert p["answered"] and p["items"][17] == 612 and p["items"][12] & 4 and p["isr_us"] == 4100
    assert not A.parse([(100, 1), (101, 0), (102, 3)])["answered"]


def test_new_firmware_clean_save_passes():
    res = A.analyse(take({7.0: {17: 480}}), 48000, 4.5, A.DEADLINE_NEW)
    assert res["verdict"] == "PASS", res["rows"]
    assert res["save"][0] == "item 17" and abs(res["save"][2] - 2.5) < 1e-9
    assert status(res, "flash interrupts off, longest (us)") == ["ok"]


def test_44100_rate_and_wrong_rate():
    ok = A.analyse(take({7.0: {17: 480}}, rate=44100), 44100, 4.5, A.DEADLINE_NEW)
    assert ok["verdict"] == "PASS"
    bad = A.analyse(take({7.0: {17: 480}}, rate=48000), 44100, 4.5, A.DEADLINE_NEW)
    assert status(bad, "USB stream state") == ["FAIL"] and bad["verdict"] == "FAIL"


def test_missed_anywhere_fails():
    res = A.analyse(take({1.0: {2: 1}, 7.0: {17: 480}}), 48000, 4.5, A.DEADLINE_NEW)
    assert status(res, "USB missed") == ["FAIL"] and res["verdict"] == "FAIL"


def test_late_save_and_no_save_fail():
    late = A.analyse(take({16.0: {17: 480}}), 48000, 4.5, A.DEADLINE_NEW)
    assert status(late, "save seen") == ["FAIL"]
    none = A.analyse(take({}), 48000, 4.5, A.DEADLINE_NEW)
    assert status(none, "save seen") == ["FAIL"] and none["save"] is None
    assert "item 17 never > 0" in [r[1] for r in none["rows"] if r[0] == "save seen"][0]


def test_off_limit_gate():
    res = A.analyse(take({7.0: {17: 2400}}), 48000, 4.5, A.DEADLINE_NEW)
    assert status(res, "flash interrupts off, longest (us)") == ["FAIL"]


def test_a_save_before_the_change_is_not_the_save():
    res = A.analyse(take({2.0: {17: 400}}), 48000, 4.5, A.DEADLINE_NEW)
    assert res["save"] is None


def test_old_firmware_teeth():
    """353d023: no item 17; the erase holds the interrupts off ~24 ms at the 60 s save: the ISR spacing jumps (item
    10) and packets are missed. --old-rule: the save is seen in time, the take FAILS on missed: the teeth"""
    polls = take({66.0: {10: 29800, 2: 18}}, new=False, end=72.0)
    res = A.analyse(polls, 48000, 4.5, A.DEADLINE_OLD)
    assert res["save"][0] == "item 10" and abs(res["save"][2] - 61.5) < 1e-9
    assert status(res, "save seen") == ["ok"] and status(res, "USB missed") == ["FAIL"]
    assert res["verdict"] == "FAIL" and res["missed_at_save"] == 18
    assert A.teeth_ok([res])
    clean = A.analyse(take({7.0: {17: 480}}), 48000, 4.5, A.DEADLINE_NEW)
    assert not A.teeth_ok([clean])
    # the new rule on the old firmware: late too
    assert status(A.analyse(polls, 48000, 4.5, A.DEADLINE_NEW), "save seen") == ["FAIL"]


def test_no_usb_items_fails():
    polls = [(t * 0.5, A.parse([(116, 1), (117, 0)])) for t in range(10)]
    res = A.analyse(polls, 48000, 1.0, A.DEADLINE_NEW)
    assert status(res, "USB counters answer") == ["FAIL"]


def test_sound_take_passes_without_a_save_and_fails_with_one():
    """0.9.5: a sound change (TONE) is never auto-saved: the sound take PASSES with no save seen, FAILS with one"""
    ok = A.analyse(take({}), 48000, 4.5, A.DEADLINE_NEW, expect_save=False)
    assert ok["verdict"] == "PASS" and status(ok, "no save after a sound change") == ["ok"], ok["rows"]
    bad = A.analyse(take({7.0: {17: 480}}), 48000, 4.5, A.DEADLINE_NEW, expect_save=False)
    assert bad["verdict"] == "FAIL" and status(bad, "no save after a sound change") == ["FAIL"], bad["rows"]


def test_silent_from():
    fs = 48000
    x = np.zeros(fs * 3)
    x[fs // 2:fs] = 0.1
    assert abs(A.silent_from(x, fs) - 1.0) < 0.01
    x[-10:] = 0.1
    assert A.silent_from(x, fs) is None


def test_dry_run():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_autosave.py"), "--dry-run", "--rate",
                          "both"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "48 kHz, 44.1 kHz" in out.stdout and "within 10 s" in out.stdout and "DRY RUN" in out.stdout
    assert "takes setup + sound" in out.stdout and "CC 57" in out.stdout
    old = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_autosave.py"), "--dry-run", "--old-rule",
                          "--teeth"], capture_output=True, text=True, timeout=60)
    assert old.returncode == 0 and "within 70 s" in old.stdout and "teeth" in old.stdout and "takes legacy" in old.stdout
