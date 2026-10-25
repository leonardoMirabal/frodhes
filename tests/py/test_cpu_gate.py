# SPDX-License-Identifier: GPL-3.0-only
"""The CPU margin rule in the final run's tools, without the FM-1: tools/final/hil_cpu_fx.py's take parsing and
judging (the whole ISR <= 4200 us in every take, the max judged; --hurt: 3 good takes in a row), its --takes loop
end to end over a fake hil_cpu (one process per take, every take's output kept), summarize_rev.py's cpu rows (min-max,
FAIL over the limit, older one-run logs judged too), and tools/cpu_check.sh's dry run and its exit code on a breach."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FINAL = os.path.join(ROOT, "tools", "final")
sys.path.insert(0, FINAL)
import hil_cpu_fx as F  # noqa: E402

FAKE = '''import os, sys
def ports():
    raise SystemExit("no MIDI in the fake")
def main(argv):
    f = os.environ["FAKE_COUNT"]
    k = int(open(f).read()) if os.path.exists(f) else 0
    open(f, "w").write(str(k + 1))
    worst, whole, ok = os.environ["FAKE_TAKES"].split(";")[k].split(",")
    print("8 bass notes v127 + pedal, every effect on   attack 3900 us   voices shed 0")
    print(f"\\nworst {worst} us with 10 voices (67 %); design target 4500 us")
    print(f"whole ISR worst: {whole} us")
    print("hil_cpu: " + ("ok" if ok == "1" else "FAIL"))
    return dict(code=0 if ok == "1" else 1)
def exit_code(res):
    return res["code"]
'''


def take(worst, whole, verdict="ok", code=0):
    return dict(worst=worst, whole=whole, verdict=verdict, code=code)


def test_parse_take_reads_the_last_numbers():
    text = ("worst 3800 us with 10 voices\nwhole ISR worst: 4100 us\nhil_cpu: ok\n"
            "worst 3900 us with 10 voices\nwhole ISR worst: 4212 us; USB counters moved in: x\nhil_cpu: FAIL\n")
    assert F.parse_take(text) == (3900, 4212, "FAIL")
    assert F.parse_take("nothing here") == (None, None, None)
    line = F.take_line(2, 3, 3900, 4212, "FAIL", 1)
    assert F.parse_takes(line) == [dict(k=2, n=3, worst=3900, whole=4212, verdict="FAIL", code=1)]
    assert F.parse_takes(F.take_line(1, 1, None, None, None, 1))[0]["whole"] is None


def test_judge_the_max_of_the_takes():
    ok, lines = F.judge([take(3900, 4100), take(3950, 4180), take(3920, 4200)])
    assert ok and lines[0].endswith(": PASS") and "4100-4200 us (max 4200)" in lines[0]
    assert lines[-1].startswith("hil_cpu_fx: verdict: PASS (limit 4200 us, 3 takes")
    ok, lines = F.judge([take(3900, 4100), take(3950, 4212), take(3920, 4150)])    # one take over: FAIL
    assert not ok and "take 2: whole ISR 4212 us > 4200 us" in "\n".join(lines)
    assert F.judge([take(3900, 4212)], limit=4300)[0]                             # the limit is an option
    assert not F.judge([take(3900, None)])[0]                                     # not reported: cannot judge
    assert not F.judge([take(3900, 4000, "FAIL", 1)])[0]                          # hil_cpu's own verdict (4500 ...)
    assert not F.judge([take(3900, 4000, "ok", 1)])[0]                            # a USB fault: exit 1
    assert not F.judge([])[0]


def test_judge_hurt_needs_three_good_in_a_row():
    good = [take(3900, 4150)] * 3
    ok, lines = F.judge(good, hurt=True)
    assert ok and "3 good takes in a row needed, 3 in a row" in lines[0]
    assert not F.judge(good[:2], hurt=True)[0]                                    # 2 takes: not enough
    ok, lines = F.judge([take(3900, 4150), take(3900, 4224), take(3900, 4150), take(3900, 4150)], hurt=True)
    assert not ok, "a take over breaks the row: FAIL, never retried"
    assert "take 2: whole ISR 4224 us" in "\n".join(lines)


@pytest.fixture
def fake_tree(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "hil_cpu.py").write_text(FAKE)
    return tmp_path


def run_fx(tree, takes, *own, env=None):
    if (tree / "count").exists():
        (tree / "count").unlink()                                              # (the fake's take counter)
    e = dict(os.environ, TREE=str(tree), FAKE_TAKES=";".join(takes), FAKE_COUNT=str(tree / "count"))
    e.pop("CPU_TAKES", None)
    e.pop("CPU_LIMIT_US", None)
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(FINAL, "hil_cpu_fx.py"), *own, "--", "--worst"],
                          capture_output=True, text=True, env=e, cwd=str(tree), timeout=120)


def test_takes_end_to_end_pass_and_every_take_kept(fake_tree):
    r = run_fx(fake_tree, ["3900,4100,1", "3950,4190,1", "3920,4120,1"], "--takes", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [t["whole"] for t in F.parse_takes(r.stdout)] == [4100, 4190, 4120]
    assert r.stdout.count("whole ISR worst:") == 3                               # every take's own output
    assert "whole ISR 4100-4190 us (max 4190)" in r.stdout and "verdict: PASS" in r.stdout


def test_takes_end_to_end_one_over_fails(fake_tree):
    r = run_fx(fake_tree, ["3900,4100,1", "3950,4260,1", "3920,4120,1"], "--takes", "3")
    assert r.returncode == 1 and "take 2: whole ISR 4260 us > 4200 us" in r.stdout, r.stdout + r.stderr
    assert len(F.parse_takes(r.stdout)) == 3                                     # the takes after it still run


def test_one_take_is_judged_by_the_margin_rule_too(fake_tree):
    r = run_fx(fake_tree, ["3900,4212,1"])
    assert r.returncode == 1 and "verdict: FAIL (limit 4200 us, 1 takes)" in r.stdout, r.stdout + r.stderr
    r = run_fx(fake_tree, ["3900,4212,1"], env=dict(CPU_LIMIT_US="4300"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_hurt_runs_three_takes_whatever_takes_says(fake_tree):
    r = run_fx(fake_tree, ["3900,4100,1", "3950,4190,1", "3920,4120,1"], "--takes", "1", "--hurt")
    assert r.returncode == 0 and len(F.parse_takes(r.stdout)) == 3, r.stdout + r.stderr
    assert "3 good takes in a row needed, 3 in a row" in r.stdout
    r = run_fx(fake_tree, ["3900,4100,1", "3950,4100,1", "3920,4201,1"], "--hurt",
               env=dict(CPU_TAKES="3"))
    assert r.returncode == 1, r.stdout


def test_a_take_own_fail_fails(fake_tree):
    r = run_fx(fake_tree, ["3900,4100,1", "4600,4700,0"], "--takes", "2", "--limit", "5000")
    assert r.returncode == 1 and "take 2:" in r.stdout and "hil_cpu FAIL" in r.stdout, r.stdout


def summarize(tmp_path, logs, env=None):
    out = tmp_path / "out"
    out.mkdir()
    (out / "run.txt").write_text("sha256=x\n")
    (out / "steps.tsv").write_text("".join(f"{n}\t{c}\t90\tcpu\t\n" for n, c, _ in logs))
    for n, _, text in logs:
        (out / f"{n}.log").write_text(text)
    e = dict(os.environ)
    e.pop("CPU_LIMIT_US", None)
    e.update(env or {})
    subprocess.run([sys.executable, os.path.join(FINAL, "summarize_rev.py"), str(out)], capture_output=True, text=True,
                   env=e, timeout=60)
    md = (out / "SUMMARY.md").read_text()
    return {ln.split("|")[1].strip(): ln for ln in md.splitlines() if ln.startswith("| cpu_")}, md


def fx_log(wholes, hurt=False, code=0):
    takes = [take(3900, w) for w in wholes]
    text = "".join(F.take_line(i, len(takes), t["worst"], t["whole"], "ok", 0) + "\n" for i, t in enumerate(takes, 1))
    return text + "\n".join(F.judge(takes, hurt=hurt)[1]) + "\n"


def test_summary_cpu_rows(tmp_path):
    rows, md = summarize(tmp_path, [
        ("cpu_vibes", "0", fx_log([4072, 4150, 4176], hurt=True)),
        ("cpu_rhodes_spread_bend", "1", fx_log([4212, 4224, 4260], hurt=True)),
        ("cpu_marimba", "0", fx_log([4000, 4010, 4020])),
        ("cpu_rhodes_1", "0", "worst 3700 us with 10 voices\nwhole ISR worst: 4250 us\nhil_cpu: ok\n"),   # older log
        ("cpu_rhodes_2", "0", "worst 3700 us with 10 voices\nwhole ISR worst: 3950 us\nhil_cpu: ok\n"),
    ])
    assert "| PASS |" in rows["cpu_vibes"] and "whole ISR 4072-4176 us (max 4176; limit 4200 us)" in rows["cpu_vibes"]
    assert "3 good takes in a row" in rows["cpu_vibes"]
    assert "**FAIL**" in rows["cpu_rhodes_spread_bend"] and "4212-4260" in rows["cpu_rhodes_spread_bend"]
    assert "| PASS |" in rows["cpu_marimba"]
    assert "**FAIL**" in rows["cpu_rhodes_1"] and "the margin rule" in rows["cpu_rhodes_1"]
    assert "| PASS |" in rows["cpu_rhodes_2"]
    assert md.startswith("# ") and ": FAIL" in md.splitlines()[0]
    assert "cpu_rhodes: 2 runs" in md                                            # the ranges note


def test_summary_exit0_with_a_lie_fails(tmp_path):
    """a log whose verdict line says PASS but a take is over the limit (a hand-edited or older tool): FAIL here"""
    log = F.take_line(1, 1, 3900, 4300, "ok", 0) + "\nhil_cpu_fx: verdict: PASS (limit 4200 us, 1 takes)\n"
    rows, _ = summarize(tmp_path, [("cpu_vibes", "0", log)])
    assert "**FAIL**" in rows["cpu_vibes"]


def test_cpu_check_dry_run_and_a_breach(tmp_path, fake_tree):
    sh = os.path.join(ROOT, "tools", "cpu_check.sh")
    e = dict(os.environ, DRY_RUN="1", OUT=str(tmp_path / "dry"), PY=sys.executable)
    r = subprocess.run(["bash", sh], capture_output=True, text=True, env=e, cwd=ROOT, timeout=120)
    assert r.returncode == 0 and "cpu_check: PASS" in r.stdout, r.stdout + r.stderr
    for c in ("cpu_rhodes_spread_bend", "cpu_vibes", "cpu_rhodes_bend", "cpu_marimba"):
        assert f"{c}: PASS" in r.stdout
    # a breach on the fake tree (its hil_cpu scripted; no lock: CPU_CHECK_LOCKED as under tools/lock.py run)
    final = fake_tree / "tools" / "final"
    final.mkdir()
    (final / "hil_cpu_fx.py").write_text(open(os.path.join(FINAL, "hil_cpu_fx.py")).read())
    good = "3900,4100,1"
    takes = [good] * 3 + ["3900,4224,1", good, good] + [good] * 6       # cpu_vibes' 1st take over the margin
    e = dict(os.environ, CPU_CHECK_LOCKED="1", TREE=str(fake_tree), OUT=str(tmp_path / "real"), PY=sys.executable,
             FAKE_TAKES=";".join(takes), FAKE_COUNT=str(fake_tree / "count"))
    e.pop("CPU_TAKES", None)
    r = subprocess.run(["bash", sh], capture_output=True, text=True, env=e, cwd=str(fake_tree), timeout=300)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cpu_vibes: FAIL" in r.stdout and "cpu_rhodes_spread_bend: PASS" in r.stdout
    assert "cpu_marimba: PASS" in r.stdout and "take 1: whole ISR 4224 us > 4200 us" in r.stdout


def test_cpu_check_only_names(tmp_path):
    """ONLY: an unknown name is an error (exit 2, the valid names listed), never a silent skip; the short names (no
    cpu_) count; a run with 0 cases judged is never a PASS"""
    sh = os.path.join(ROOT, "tools", "cpu_check.sh")

    def run(only):
        e = dict(os.environ, DRY_RUN="1", OUT=str(tmp_path / "o"), PY=sys.executable, ONLY=only)
        return subprocess.run(["bash", sh], capture_output=True, text=True, env=e, cwd=ROOT, timeout=120)
    r = run("rhodes_spread_bnd")
    assert r.returncode == 2 and "unknown case 'cpu_rhodes_spread_bnd'" in r.stderr, r.stdout + r.stderr
    assert "cpu_rhodes_spread_bend cpu_vibes cpu_rhodes_bend cpu_marimba" in r.stderr and "PASS" not in r.stdout
    r = run("rhodes_spread_bend")                                    # the short name = cpu_rhodes_spread_bend
    assert r.returncode == 0 and "cpu_rhodes_spread_bend: PASS" in r.stdout, r.stdout + r.stderr
    assert "cpu_vibes: skipped" in r.stdout and "PASS (1 of 4 cases" in r.stdout
    r = run("cpu_vibes, marimba")
    assert r.returncode == 0 and "cpu_vibes: PASS" in r.stdout and "cpu_marimba: PASS" in r.stdout, r.stdout
    r = run(",")                                                     # nothing named: all 4 run
    assert r.returncode == 0 and "PASS (4 of 4 cases" in r.stdout, r.stdout + r.stderr


def test_cpu_check_zero_cases_is_not_a_pass(tmp_path):
    """the guard itself: with every case skipped (a case list edited out from under ONLY), exit 2, no PASS line"""
    src = open(os.path.join(ROOT, "tools", "cpu_check.sh")).read()
    for c in ("cpu_rhodes_spread_bend --spread", "cpu_vibes --", "cpu_rhodes_bend --", "cpu_marimba --"):
        assert f"case_run {c}" in src
    sh = tmp_path / "cpu_check.sh"
    sh.write_text("\n".join(ln for ln in src.splitlines() if not ln.startswith("case_run ")) + "\n")
    e = dict(os.environ, DRY_RUN="1", OUT=str(tmp_path / "o"), PY=sys.executable, TREE=ROOT)
    r = subprocess.run(["bash", str(sh)], capture_output=True, text=True, env=e, cwd=ROOT, timeout=120)
    assert r.returncode == 2 and "0 cases ran" in r.stdout and "cpu_check: PASS" not in r.stdout, r.stdout + r.stderr


def test_cpu_check_parses(tmp_path):
    for sh in ("tools/cpu_check.sh", "tools/final/run_final_rev.sh"):
        r = subprocess.run(["bash", "-n", os.path.join(ROOT, sh)], capture_output=True, text=True)
        assert r.returncode == 0, sh + ": " + r.stderr


INSTALLER = '''import os, sys
open(os.environ["FAKE_INSTALL_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
sh = os.environ.get("FAKE_REWRITE")
if sh:                                   # a checkout rewriting the running script (the FM-1 run of 2026-10-25)
    s = open(sh).read()
    open(sh, "w").write("# " + "a longer header line, all offsets move\\n# " * 40 + "\\n" + s.replace("say", "  say"))
print("installed")
'''


def real_tree(tmp_path, fake_tree):
    """the fake tree with the real cpu_check.sh, hil_cpu_fx.py and lock.py, a fake fm1_install.py and the fake hil_cpu"""
    final = fake_tree / "tools" / "final"
    final.mkdir(exist_ok=True)
    (final / "hil_cpu_fx.py").write_text(open(os.path.join(FINAL, "hil_cpu_fx.py")).read())
    for f in ("cpu_check.sh", "lock.py"):
        (fake_tree / "tools" / f).write_text(open(os.path.join(ROOT, "tools", f)).read())
    (fake_tree / "tools" / "fm1_install.py").write_text(INSTALLER)
    pkg = tmp_path / "pkg.fwsc"
    pkg.write_bytes(b"fwsc")
    (tmp_path / "locks").mkdir()
    return pkg


def run_real(tmp_path, fake_tree, takes, pkg, **env):
    if (fake_tree / "count").exists():
        (fake_tree / "count").unlink()
    e = dict(os.environ, TREE=str(fake_tree), OUT=str(tmp_path / "out"), PY=sys.executable, LOCK_WAIT="5",
             FAKE_TAKES=";".join(takes), FAKE_COUNT=str(fake_tree / "count"), FRODHES_LOCK_ROOT=str(tmp_path / "locks"),
             FAKE_INSTALL_LOG=str(tmp_path / "install.txt"))
    for k in ("CPU_TAKES", "CPU_CHECK_LOCKED", "DRY_RUN", "ONLY", "FRODHES_LOCK_DEVICE"):
        e.pop(k, None)
    e.update(env)
    return subprocess.run(["bash", str(fake_tree / "tools" / "cpu_check.sh"), str(pkg)], capture_output=True,
                          text=True, env=e, cwd=str(fake_tree), timeout=300)


def test_cpu_check_real_path_lock_install_and_cases(tmp_path, fake_tree):
    """not DRY_RUN: the DEVICE lock (tools/lock.py run, a private lock root), the install of the package (a fake
    fm1_install.py), the cases over the fake hil_cpu; then ONLY with a short name; then a breach"""
    pkg = real_tree(tmp_path, fake_tree)
    good = "3900,4100,1"
    r = run_real(tmp_path, fake_tree, [good] * 12, pkg)
    assert r.returncode == 0 and "cpu_check: PASS (4 of 4 cases" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "install.txt").read_text() == f"--yes {pkg}\n"
    assert "lock DEVICE taken" in r.stderr + r.stdout and not os.listdir(tmp_path / "locks" / "build" / "hil")
    r = run_real(tmp_path, fake_tree, [good] * 3, pkg, ONLY="rhodes_spread_bend")
    assert r.returncode == 0 and "PASS (1 of 4 cases" in r.stdout, r.stdout + r.stderr
    r = run_real(tmp_path, fake_tree, [good, "3900,4224,1", good], pkg, ONLY="rhodes_spread_bend")
    assert r.returncode == 1 and "cpu_rhodes_spread_bend: FAIL" in r.stdout, r.stdout + r.stderr


def test_cpu_check_survives_a_rewrite_while_running(tmp_path, fake_tree):
    """the script rewritten during the install (offsets moved): it runs on as it was parsed"""
    pkg = real_tree(tmp_path, fake_tree)
    r = run_real(tmp_path, fake_tree, ["3900,4100,1"] * 3, pkg, ONLY="cpu_vibes",
                 FAKE_REWRITE=str(fake_tree / "tools" / "cpu_check.sh"))
    assert r.returncode == 0 and "cpu_vibes: PASS" in r.stdout, r.stdout + r.stderr
    assert "command not found" not in r.stderr and "syntax error" not in r.stderr
    assert open(fake_tree / "tools" / "cpu_check.sh").read().startswith("# a longer header")


def test_cpu_check_install_failure_stops(tmp_path, fake_tree):
    pkg = real_tree(tmp_path, fake_tree)
    (fake_tree / "tools" / "fm1_install.py").write_text("import sys; sys.exit(3)\n")
    r = run_real(tmp_path, fake_tree, ["3900,4100,1"] * 12, pkg)
    assert r.returncode == 2 and "the install FAILED" in r.stdout and "PASS" not in r.stdout, r.stdout + r.stderr
