# SPDX-License-Identifier: GPL-3.0-only
"""tools/full_scan.py: the jack / USB classification ("always record jack and USB, but the JACK decides") on synthetic
hil_scan report JSON, the CPU / classic parsers, the --from summary and the device lock."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import full_scan as fs  # noqa: E402
import hil_scan as h  # noqa: E402

TOOL = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "full_scan.py")


def row(check="x", usb="+1.00", jack="+1.00", crit=">= +2 LU", status="FAIL", ok=False, src_ok=None, mm=None):
    r = dict(check=check, setting="", usb=usb, jack=jack, crit=crit, mismatch=mm, status=status, ok=ok)
    if src_ok is not None:
        r["src_ok"] = src_ok
    return r


def cls(r, takes=None):
    return [c for c, _ in fs.classify_vibes_row(r, takes or {})]


def test_src_ok_decides_on_the_jack():
    assert cls(row(src_ok=dict(usb=True, jack=False))) == ["JACK FAIL"]
    assert cls(row(src_ok=dict(usb=False, jack=False))) == ["JACK FAIL"]
    assert cls(row(src_ok=dict(usb=False, jack=True))) == ["USB-ONLY"]
    assert cls(row(status="FAIL MISMATCH", src_ok=dict(usb=False, jack=True), mm=2.4)) == ["USB-ONLY"]


def test_passing_and_skipped_rows_are_not_listed():
    assert cls(row(status="ok", ok=True)) == []
    assert cls(row(status="skip", ok=True)) == []


def test_info_and_mismatch_where_both_pass():
    assert cls(row(status="ok MISMATCH", mm=2.4)) == ["INFO"]
    assert cls(row(status="info MISMATCH", crit="information")) == ["INFO"]


def test_usb_capture_artefacts_are_tooling():
    assert cls(row(check="takes: the USB stream slipped (the jack did not)", usb="voice-default (255 ms)", jack="")) \
        == ["USB-ONLY"]
    takes = {"a": dict(tag="a", usb_fs=0, usb_clicks=2, jack_clicks=0),
             "b": dict(tag="b", usb_fs=0, usb_clicks=1, jack_clicks=3),
             "c": dict(tag="c", usb_fs=1, usb_clicks=0)}                       # recorded USB only
    out = cls(row(check="takes: full scale / clicks", usb="a, b, c", jack="", crit="none"), takes)
    assert sorted(out) == ["JACK FAIL", "UNDECIDED", "USB-ONLY"]
    assert cls(row(check="takes: full scale / clicks", usb="a", jack="", crit="none"), takes) == ["USB-ONLY"]


def test_old_json_without_src_ok_rereads_simple_criteria():
    assert cls(row(usb="3.6 dB", jack="2.7 dB", crit=">= 3 dB")) == ["JACK FAIL"]
    assert cls(row(usb="2.9 dB", jack="8.8 dB", crit=">= 3 dB")) == ["USB-ONLY"]
    # a pair: every number must pass (the motor's step at on passes, at off fails)
    assert cls(row(usb="-0.94 / +4.71 LU", jack="-0.95 / +4.74 LU", crit="|step| <= 1.5 LU")) == ["JACK FAIL"]
    assert cls(row(usb="+7.0", jack="+9.0", crit="+6 .. +12.5 LU")) == ["USB-ONLY"]
    # computed criteria (a ratio of the cell's values) and USB-only measures: no verdict from the jack
    assert cls(row(usb="0.50 / 0.69 Hz", jack="0.50 / 0.69 Hz", crit="<= 0.7x")) == ["UNDECIDED"]
    assert cls(row(usb="9.8 dB / x0.76", jack="8.5 dB / x1.49", crit=">= 3 dB and light brighter")) == ["UNDECIDED"]
    assert cls(row(usb="-2.49 LU", jack="n/a", crit="|step| <= 1.5 LU")) == ["UNDECIDED"]
    assert cls(row(usb="-2.49 LU", jack="no value", crit="|step| <= 1.5 LU")) == ["UNDECIDED"]


def test_hil_scan_rows_carry_each_sources_verdict():
    """hil_scan VSuite.check writes src_ok, which full_scan reads: the jack failing a level the USB passes"""
    class V:
        @staticmethod
        def sources():
            return ["usb", "jack"]
    S = h.VSuite("t", V)
    S.check("lvl", "", dict(usb=5.0, jack=3.0), lambda v: v >= 4, ">= +4 LU", level=True)
    r = S.rows[-1]
    assert r["src_ok"] == dict(usb=True, jack=False) and not r["ok"]
    assert cls(r) == ["JACK FAIL"]
    S.check("info", "", dict(usb=5.0, jack=3.0), lambda v: True, "information", info=True)
    assert S.rows[-1]["src_ok"] is None


def test_classic_and_cpu_parsers():
    txt = ("ok    silence       peak 0 LSB, 0 non-zero samples  (3 s)\n"
           "FAIL  keyboard      76 keys: worst tuning 23.0 cents  (78 s)\n"
           "FAIL  effects       problems: ['wah']  (57 s)\n    drive 20   USB -0.8 LU\n")
    cl = fs.parse_classic(txt)
    assert [(ok, n) for ok, n, _ in cl] == [(True, "silence"), (False, "keyboard"), (False, "effects")]
    assert fs.classify_classic("effects", "")[0] == "INFO"
    assert fs.classify_classic("keyboard", "")[0] == "UNDECIDED"
    log = ("8-note chord   attack  3600 us (62.0 %)   voices shed 1\n"
           "\nworst 3620 us of 5805 us per block (62 %); shedder at 5000 us\n")
    assert fs.parse_cpu(log) == (3620, 1)


def test_offline_summary(tmp_path):
    d = tmp_path
    (d / "report.txt").write_text("ok    silence       peak 0 LSB  (3 s)\nFAIL  presets       spread 8.2 LU  (90 s)\n")
    rep = dict(suites=dict(vibes_fx=[row(check="J", src_ok=dict(usb=True, jack=False)),
                                     row(check="U", src_ok=dict(usb=False, jack=True)),
                                     row(check="P", status="ok", ok=True)]),
               takes=[])
    (d / "vibes-report.json").write_text(json.dumps(rep))
    (d / "jack.json").write_text(json.dumps(dict(silence=dict(jack_db=-90.2), hiss=dict(jack_hf_db=-99.7, usb_hf_db=-97.0),
                                                 hiss_added_db=10.0, loud=dict(jack_peak_db=-14.9), snr_db=73.9)))
    for i, w in ((1, 3600), (2, 3700)):
        (d / f"cpu-rhodes-{i}.log").write_text(f"worst {w} us of 5805 us per block\n")
    (d / "cpu-vibes-1.log").write_text("worst 4600 us of 5805 us per block\n")
    p = subprocess.run([sys.executable, TOOL, "--from", str(d)], capture_output=True, text=True)
    assert p.returncode == 1, p.stdout + p.stderr                         # a JACK FAIL
    s = (d / "SUMMARY.md").read_text()
    assert "JACK FAIL: 1 UNDECIDED: 0 USB-ONLY: 1 INFO: 1" in s
    assert "| rhodes | 3600, 3700 | **3700** | +800 | +1100 | +1300 | 0 |" in s
    assert "| vibes | 4600 | **4600** | -100 |" in s
    assert "**73.9**" in s
    assert "| vibes_fx | 3 | 1 | 2 |" in s


def test_device_lock(tmp_path):
    lock = tmp_path / "build" / "hil" / "DEVICE.lockdir"
    with fs.DeviceLock(str(tmp_path), "test", 0):
        assert (lock / "owner").read_text().startswith("test ")
        with pytest.raises(SystemExit):                                     # held: no wait -> gives up
            with fs.DeviceLock(str(tmp_path), "other", 0):
                pass
        assert lock.exists()                                                # the loser left it alone
    assert not lock.exists()
    try:                                                                    # released after an exception too
        with fs.DeviceLock(str(tmp_path), "test", 0):
            raise RuntimeError
    except RuntimeError:
        pass
    assert not lock.exists()
