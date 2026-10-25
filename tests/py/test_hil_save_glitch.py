# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_save_glitch.py (save_glitch: the worst case played during an auto save) on the host, without the FM-1:
the self-test (the host build plays the attack: 0 events clean; synthetic dropouts on USB and the jack and an
underrun's hold inside the save's window are found and FAIL the take; synthetic replies with a missed packet, the ISR
over the limit, no overlap or no save FAIL it), the gates one by one, the dry-run plan, and the final run's step
(run_final_rev.sh DRY_RUN lists it, summarize_rev.py reads its report)."""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_save_glitch as G  # noqa: E402


def test_self_test_can_fail():
    ok, lines = G.self_test(log=lambda *_: None)
    assert ok, "\n".join(lines)
    assert sum("the take FAIL: ok" in ln for ln in lines) >= 8
    assert any(ln.startswith("self-test: clean render: 0 event(s)") for ln in lines)


def test_gates():
    tc, tk = 1.0, 3.0
    ok = G.judge(G.fake_polls(tc, tk), 48000, tc, tk, audio=[])
    assert ok["verdict"] == "PASS" and ok["overlap"] and ok["isr_window"] == 3900
    for kw, row in ((dict(missed_at=2), "USB missed"), (dict(isr_win=4201), "whole ISR in the save's window"),
                    (dict(off_after=0), "overlap"), (dict(isr=4300), "whole ISR, the chord to the end")):
        r = G.judge(G.fake_polls(tc, tk, **kw), 48000, tc, tk, audio=[])
        assert r["verdict"] == "FAIL" and any(c.startswith(row) and s == "FAIL" for c, _, _, s in r["rows"]), (kw, r)
    r = G.judge(G.fake_polls(tc, tk), 44100, tc, tk, audio=[])          # the stream at 48 kHz, 44.1 asked
    assert r["verdict"] == "FAIL"
    r = G.judge(G.fake_polls(tc, tk), 48000, tc, tk, audio=[("jack", 3.1, "zero", "2 ms")])
    assert r["verdict"] == "FAIL"
    r = G.judge(G.fake_polls(tc, tk), 48000, tc, tk, audio=None)       # not recorded: never a PASS
    assert r["verdict"] == "FAIL"
    r = G.judge(G.fake_polls(tc, tk), 48000, tc, tk, audio=[], rig=["usb: input overflow"])
    assert r["verdict"] == "FAIL"


def test_attack_is_the_worst_case():
    ev = G.attack(10, 0.0)
    ons = [e for e in ev if e[1] == "on"]
    assert len({e[0] for e in ons}) == 5 and len(ons) == 50                 # 5 chords of 10 voices
    assert ev[0] == (0.0, "cc", 64, 127) and (1.7, "cc", 64, 0) in ev
    bends = [e[3] for e in ev if e[1] == "bend"]
    assert len(bends) == 201 and min(bends) <= 1 and max(bends) >= 16383 and bends[-1] == 8192
    ccs, notes = G.sound_ccs()
    d = dict(ccs)
    assert d[105] == 127 and d[114] == 127 and any("INF" in n for n in notes)


def test_dry_run_plan():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_save_glitch.py"), "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "9 takes" in r.stdout and "CC 44 (UNDO" in r.stdout and "<= 4200 us" in r.stdout
    assert "DRY RUN" in r.stdout


def test_final_run_step(tmp_path):
    sh = os.path.join(ROOT, "tools", "final", "run_final_rev.sh")
    src = open(sh).read()
    assert "step save_glitch saveglitch" in src and "save_glitch) echo" in src
    out = tmp_path / "s"
    out.mkdir()
    (out / "run.txt").write_text("fwsc=x.fwsc\nsha256=ab\nsha256_end=ab\ntree_sha=cd\nstart=s\nend=e\n")
    (out / "steps.tsv").write_text("save_glitch\t0\t150\tsaveglitch\t\n")
    (out / "save_glitch.log").write_text("# save_glitch: x\n")
    rep = "hil_save_glitch: {} (9 take(s); whole ISR in the save's window max 3900 us, limit 4200)\nself-test: PASS\n"
    md = {}
    for v in ("PASS", "FAIL"):
        (out / "save_glitch-report.txt").write_text(rep.format(v))
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "final", "summarize_rev.py"), str(out)],
                       capture_output=True)
        md[v] = (out / "SUMMARY.md").read_text()
    assert "| save_glitch | PASS |" in md["PASS"]
    assert "| save_glitch | **FAIL** |" in md["FAIL"]


def test_jack_line_and_a_wrong_drift_fit():
    """jack_line measures the clocks' line on the host's attack (the simulated jack JACK_DRIFT off); the jack judged
    on it (the FM-1's 48k-undo takes, 2026-10-25: track's own drift fit took a line 9 samples off), audio_events
    still judges on the measured line: 0 events clean, a 3 ms jack gap found"""
    import hil_crackle as C
    import numpy as np
    ccs, _ = G.sound_ccs()
    ev = [(0.3, "cc", G.INV_CC, 1)] + G.attack(C.voices("rhodes"), 2.6)
    u = G.host_render(ccs, ev, 2.6 + G.ATTACK_S, "save_glitch-line")
    j = C.sim_jack(u)
    line = G.jack_line(u, j, C.FS_HOST)
    assert line["fit"] is not None, line["text"]
    ppm = line["fit"][0] / C.FS_JACK * 1e6
    assert abs(abs(ppm) - C.JACK_DRIFT * 1e6) < 60, (ppm, line["text"])
    evs, info = G.audio_events(u, j, C.FS_HOST, ev, ccs, fit=False)
    assert not evs and "one line" in info["jack_line"], (evs, info.get("jack_line"))
    j2 = j.copy()
    a = int((2.6 + 0.06 + C.JACK_DELAY) * C.FS_JACK)
    j2[a:a + 144] = 0.0
    evs, _ = G.audio_events(u, j2, C.FS_HOST, ev, ccs, fit=False)
    assert any(s == "jack" for s, _, _, _ in evs), evs
