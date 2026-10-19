# SPDX-License-Identifier: GPL-3.0-only
"""The bar-engine scans' plumbing (tools/bar_scan.py, tools/bar_freq_scan.py), run on a Rhodes TYPE through the host
build: they render, measure and report. The bar TYPE's own pass / fail is the scan's job (PLAN P6), not this test's."""
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bar_measure as bm  # noqa: E402


def test_type_bands_follow_the_firmware():
    assert bm.n_types() >= 5
    assert [bm.type_cc(t) for t in range(4)] == [8, 40, 72, 104]           # hil_scan's values for the Rhodes TYPEs
    assert all(bm.type_ccnum(t) == 79 and 0 <= bm.type_cc(t) <= 127 for t in range(4, bm.n_types()))
    assert bm.type_name(4) == "VIBES" and bm.spec_for_type(4)[1] and not bm.spec_for_type(0)[1]


def test_lufs_k_weighting_matches_bs1770_at_48k():
    sos = bm.k_weight_sos(48000)
    assert abs(sos[0][1] + 2.69169618940638) < 1e-6 and abs(sos[1][4] + 1.99004745483398) < 1e-6


def test_bar_freq_scan_on_stage77():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "bar_freq_scan.py"), "--type", "0", "--keys", "60,69",
                        "--dur", "4200"], capture_output=True, text=True)
    out = os.path.join(ROOT, "build", "bar_freq_scan")
    assert os.path.exists(os.path.join(out, "curves.png"))
    rows = [l.split(",") for l in open(os.path.join(out, "scan.csv")).read().splitlines()[1:]]
    v1 = [x for x in rows if x[1] == "V1"]
    assert len(v1) == 2 and all(abs(float(x[4])) < 1.0 for x in v1)        # the C engine plays its spec's pitch
    assert all(abs(float(x[6]) / float(x[5]) - 1) < 0.1 for x in v1)       # and its T60
    assert r.returncode in (0, 1)


def test_bar_scan_smoke_on_stage77():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "bar_scan.py"), "--type", "0", "--smoke"],
                       capture_output=True, text=True)
    out = os.path.join(ROOT, "build", "bar_scan")
    assert r.returncode in (0, 1), r.stderr
    s = json.load(open(os.path.join(out, "scan.json")))
    assert s["summary"]["S1"]["judged"] >= 5 and s["summary"]["S2"]["fails"] == 0
    assert s["summary"]["S9"]["judged"] >= 3
    assert os.path.exists(os.path.join(out, "summary.png"))
    assert "S13" in open(os.path.join(out, "report.txt")).read()


def test_strike_windows_and_click_guards_follow_the_block(tmp_path):
    """bar_scan's strike windows and S1's guards are where the strike sounds: the start of its block (bar_scan.onset_ms,
    0..5.8 ms before the script's ms), also late in a long render (the HF-burst windows are 44 samples: indexed by the
    ms they drifted 2.3 ms a second, 107 ms at 46.7 s). MARIMBA RUBBER v127 on A4 at 0.2 / 36.2 / 46.7 s: each onset is
    the block start bar_scan computes, and no click or burst is counted (before the fix: 2..15 bursts on steps BARK)"""
    import bar_scan as bs
    ts = (200, 36200, 46700)
    ev = bm.setup_events(typ=5, noise=0, BARK=127)
    for t in ts:
        ev += bm.note(t, 69, 127, 300)
    x, _ = bm.render("onsets", ev, 47400, str(tmp_path))
    m = bm.mono(x)
    for t in ts:
        a = int(bs.onset_ms(t) * bm.FS / 1000)
        assert a % 256 == 0 and a == (t * 44100 // 256000) * 256
        assert not m[a - 600:a].any() and m[a:a + 8].any(), t       # silent up to the block, sounding from it
    assert bs.click_count(x, [t for t, _ in ev]) == (0, 0)
