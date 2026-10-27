# SPDX-License-Identifier: GPL-3.0-only
"""tools/final/run_final_rev.sh's key-ADC gate follow-up (TEST-PLAN 3b): the gate's run-to-run spread (~+-100 us) is
wider than its limits (+10 idle / +60 STRESS), so an engine that FAILs on 3 runs gets 5 (keyadc_<engine>5) and those
decide its verdict. Before 0.9.7 the Rhodes only; now VIBES and MARIMBA the same way (summarize_rev.py)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SH = os.path.join(ROOT, "tools", "final", "run_final_rev.sh")


def line(eng, idle, stress):
    def v(d, lim):
        return f"{3900 + d} ({d:+d}: {'ok' if d <= lim else 'FAIL'})"
    return f"  {eng:<8} OFF 3900 us  idle {v(idle, 10)}  STRESS {v(stress, 60)}"


def gate_log(verdict, rows):
    return "\n".join(["# keyadc_gate: x", f"key ADC gate: {verdict}"] + rows + ["worst 4000 us", "whole ISR worst: 4100 us"])


def summary(tmp_path, gate_rows, fives):
    """fives: {engine: verdict of its 5 runs}; returns {step: SUMMARY.md status}"""
    out = tmp_path / "out"
    out.mkdir()
    (out / "run.txt").write_text("fwsc=x.fwsc\nsha256=ab\nsha256_end=ab\ntree_sha=cd\nstart=s\nend=e\n")
    bad = any("FAIL" in r for r in gate_rows)
    steps = [f"keyadc_gate\t{1 if bad else 0}\t400\tkeyadc\t"]
    (out / "keyadc_gate.log").write_text(gate_log("FAIL" if bad else "PASS", gate_rows) + "\n")
    for eng, v in fives.items():
        steps.append(f"keyadc_{eng}5\t{0 if v == 'PASS' else 1}\t600\tkeyadc\t")
        (out / f"keyadc_{eng}5.log").write_text(gate_log(v, [line(eng, 5 if v == "PASS" else 40, 30)]) + "\n")
    (out / "steps.tsv").write_text("\n".join(steps) + "\n")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "final", "summarize_rev.py"), str(out)],
                   capture_output=True)                  # (exit 1 when a step FAILs)
    md = (out / "SUMMARY.md").read_text().splitlines()
    res = {}
    for n in ["keyadc_gate"] + [f"keyadc_{e}5" for e in fives]:
        row = [ln for ln in md if ln.startswith(f"| {n} ")]
        assert row, (n, md)
        res[n] = row[0].split("|")[2].strip().strip("*")
    return res, md


def test_every_engine_gets_its_five_runs_in_the_script():
    s = open(SH, encoding="utf-8").read()
    assert "for eng in rhodes vibes marimba; do" in s
    assert 'grep -q -E "^  $eng .*FAIL" "$OUT/keyadc_gate.log"' in s
    assert 'step "keyadc_${eng}5" keyadc "$PY" tools/hil_cpu.py --keyadc gate --worst --runs 5 --engines "$eng"' in s
    assert "keyadc_*5) echo 600 ;;" in s


def test_the_rhodes_as_before(tmp_path):
    r, md = summary(tmp_path, [line("rhodes", 40, 30), line("vibes", 0, 20), line("marimba", 0, 20)], {"rhodes": "PASS"})
    assert r == {"keyadc_gate": "PASS", "keyadc_rhodes5": "INFO"}
    assert any("Rhodes FAILed on 3 runs, PASSed on 5 (keyadc_rhodes5; TEST-PLAN 3b)" in ln for ln in md)


def test_vibes_and_marimba_the_same_way(tmp_path):
    r, md = summary(tmp_path, [line("rhodes", 0, 20), line("vibes", 30, 20), line("marimba", 0, 90)],
                    {"vibes": "PASS", "marimba": "PASS"})
    assert r == {"keyadc_gate": "PASS", "keyadc_vibes5": "INFO", "keyadc_marimba5": "INFO"}
    assert any("MARIMBA, VIBES FAILed on 3 runs, PASSed on 5 (keyadc_marimba5, keyadc_vibes5" in ln for ln in md)


def test_a_failed_five_keeps_the_gate_failed(tmp_path):
    r, _ = summary(tmp_path, [line("rhodes", 0, 20), line("vibes", 30, 20), line("marimba", 0, 90)],
                   {"vibes": "PASS", "marimba": "FAIL"})
    assert r["keyadc_gate"] == "FAIL" and r["keyadc_marimba5"] == "FAIL" and r["keyadc_vibes5"] == "INFO"


def test_an_engine_without_its_five_keeps_the_gate_failed(tmp_path):
    r, _ = summary(tmp_path, [line("rhodes", 40, 20), line("vibes", 30, 20)], {"rhodes": "PASS"})
    assert r["keyadc_gate"] == "FAIL"


def test_an_only_run_skips_the_rows_its_left_out_step_covers(tmp_path):
    """ONLY=keyadc_*: the rows folded into stress_48000 ('covered by step stress_48000') are left out with it
    (SKIPPED), not FAILed as if stress_48000 had run and failed (0.9.7, the key-ADC rerun's SUMMARY.md said FAIL)"""
    def run(stress_code, stress_note):
        out = tmp_path / f"o{stress_code}"
        out.mkdir()
        (out / "run.txt").write_text("fwsc=x.fwsc\nsha256=ab\nsha256_end=ab\ntree_sha=cd\nstart=s\nend=e\n")
        (out / "keyadc_gate.log").write_text(gate_log("PASS", [line("rhodes", 0, 20)]) + "\n")
        (out / "stress_48000.log").write_text("# stress_48000: x\nFAIL: a click\n")
        (out / "steps.tsv").write_text(f"stress_48000\t{stress_code}\t0\tstress\t{stress_note}\n"
                                       "keyadc_gate\t0\t400\tkeyadc\t\n"
                                       "silence\tnotrun\t0\tjack\tcovered by step stress_48000: folded (x)\n")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "final", "summarize_rev.py"), str(out)],
                       capture_output=True)
        md = (out / "SUMMARY.md").read_text().splitlines()
        return md[0], [ln for ln in md if ln.startswith("| silence ")][0].split("|")[2].strip().strip("*")
    head, silence = run("notrun", "not in ONLY=keyadc_gate")
    assert silence == "SKIPPED" and head.endswith(": PARTIAL (ONLY: some steps not run)"), (head, silence)
    head, silence = run("1", "")                       # the covering step ran and FAILed: the row FAILs with it
    assert silence == "FAIL" and head.endswith(": FAIL"), (head, silence)
