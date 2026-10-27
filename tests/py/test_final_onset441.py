# SPDX-License-Identifier: GPL-3.0-only
"""tools/final/run_final_rev.sh's usb_onset_44100 step: the 44.1 kHz MIDI -> sound onset (stress_48000's fold measures
48 kHz only), planned in the run, timed, and summarised from its JSON."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SH = os.path.join(ROOT, "tools", "final", "run_final_rev.sh")


def test_the_step_is_in_the_run():
    s = open(SH, encoding="utf-8").read()
    assert "step usb_onset_44100 onset \"$PY\" tools/hil_usb_onset.py --rate 44100" in s
    assert "copy \"$TREE/build/hil/usb_onset.json\" usb_onset_44100.json" in s
    assert "usb_onset_44100) echo 90 ;;" in s
    # after the 44.1 kHz stress, before T1
    assert s.index("stress_full 44100") < s.index("step usb_onset_44100") < s.index("step t1_s3s4")
    assert "(44.1 kHz: step usb_onset_44100)" in s


def test_the_summary_row(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "run.txt").write_text("fwsc=x.fwsc\nsha256=ab\nsha256_end=ab\ntree_sha=cd\nstart=s\nend=e\n")
    (out / "steps.tsv").write_text("usb_onset_44100\t0\t60\tonset\t\n")
    notes = [{"note": 60, "usb_ms": 13.1, "jack_ms": 10.0, "late": False},
             {"note": 64, "usb_ms": 15.2, "jack_ms": 11.4, "late": False}]
    (out / "usb_onset_44100.json").write_text(json.dumps([{"rate": 44100, "ok": True, "touch": True, "notes": notes}]))
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "final", "summarize_rev.py"), str(out)],
                   check=True, capture_output=True)
    row = [ln for ln in (out / "SUMMARY.md").read_text().splitlines() if ln.startswith("| usb_onset_44100 ")]
    assert row and "| PASS |" in row[0]
    assert "44100 Hz: USB median 14.1 ms (13.1-15.2), jack 10.7 ms, USB - jack 3.4 ms, 2 notes" in row[0]


def test_every_step_line_carries_the_host_load():
    """run_final_rev.sh: each step's start / exit lines and its log carry the 1-minute load (a busy host starves the
    recorder: the rig's problem, not the FM-1's)"""
    s = open(SH, encoding="utf-8").read()
    fn = [ln for ln in s.splitlines() if ln.startswith("load1() {")]
    assert fn
    r = subprocess.run(["bash", "-c", fn[0] + "\nload1"], capture_output=True, text=True, check=True)
    assert float(r.stdout.strip()) >= 0
    assert 'host load ${l0:-?}): $*"' in s and '# host load before / after: ${l0:-?} / $(load1)' in s
