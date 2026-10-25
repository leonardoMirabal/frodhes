# SPDX-License-Identifier: GPL-3.0-only
"""tools/final/run_final_rev.sh's pipeline (suite-speed): hil_crackle's host self-test of stress_48000 runs as its own
step stress_48000_selftest in the background while the FM-1 records. Nothing is dropped: the DRY RUN with the
pipeline lists every step, command and gate of the DRY RUN without it (SELFTEST_BG=0, the old order), the one device
command differing only by --no-self-test, and the self-test step is that same command with --self-test-only; the
summary FAILs stress_48000 when its self-test did not PASS (the old rule: no stress PASS with unproven detectors)."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SH = os.path.join(ROOT, "tools", "final", "run_final_rev.sh")
SUM = os.path.join(ROOT, "tools", "final", "summarize_rev.py")


def dry(tmp_path, tag, **env):
    fw = tmp_path / "pkg.fwsc"
    fw.write_bytes(b"not a package")
    prev = tmp_path / "prev.fwsc"
    prev.write_bytes(b"prev")
    out = tmp_path / tag
    e = dict(os.environ, DRY_RUN="1", OUT=str(out), PY=sys.executable, TREE=ROOT, PREV=str(prev), **env)
    for k in ("ONLY", "RESUME", "SKIP_COVERED", "PYTEST_GREEN", "QUICK", "REV_TYPE", "CPU_HURT"):
        e.pop(k, None)
    r = subprocess.run(["bash", SH, str(fw)], capture_output=True, text=True, env=e, cwd=ROOT, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    d = out / "dry-run"
    rows = [ln.split("\t") for ln in (d / "steps.tsv").read_text().splitlines() if ln.strip()]
    logs = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".log") and f != "summarize.log":
            # the commands, the plans and the gate texts; not the clock lines
            logs[f[:-4]] = [ln for ln in (d / f).read_text().splitlines() if not re.match(r"# \w{3} \w{3} ", ln)]
    return rows, logs, (d / "SUMMARY.md").read_text()


def test_nothing_dropped(tmp_path):
    old_rows, old_logs, _ = dry(tmp_path, "old", SELFTEST_BG="0")
    new_rows, new_logs, md = dry(tmp_path, "new")
    key = lambda rows: [(r[0], r[1], r[3], r[4] if len(r) > 4 else "") for r in rows]  # noqa: E731
    # every step (name, code, kind, note) as before, in order, plus the self-test step
    assert [k for k in key(new_rows) if k[0] != "stress_48000_selftest"] == key(old_rows)
    assert [k for k in key(new_rows) if k[0] == "stress_48000_selftest"] == [("stress_48000_selftest", "0",
                                                                               "selftest", "")]
    assert "stress_48000_selftest" not in old_logs
    assert set(new_logs) == set(old_logs) | {"stress_48000_selftest"}
    for n in old_logs:                               # every other step's command, plan and gates: the same
        if n != "stress_48000":
            assert new_logs[n] == old_logs[n], n
    oc, nc, sc = old_logs["stress_48000"][0], new_logs["stress_48000"][0], new_logs["stress_48000_selftest"][0]
    assert "--self-test" not in oc and "--no-self-test" in nc
    assert nc.replace(" --no-self-test", "") == oc                     # the device step: only the self-test moved
    args = oc.split(": ", 1)[1]
    assert sc == "# stress_48000_selftest: nice -n 10 " + args.replace(" --dry-run", " --self-test-only --dry-run")
    assert new_logs["stress_48000"][1:] == old_logs["stress_48000"][1:]  # the takes and the gate texts
    assert any(ln.startswith("gates:") for ln in old_logs["stress_48000"])
    assert "| stress_48000_selftest | DRY OK |" in md and "| stress_48000 | DRY OK |" in md
    # stress_44100 had --no-self-test already (the self-test is the rate-free host proof): no second one
    assert "stress_44100_selftest" not in new_logs


def summarize(tmp_path, st_code, st_log):
    out = tmp_path / ("s" + st_code + str(len(st_log)))
    out.mkdir()
    (out / "run.txt").write_text("fwsc=x.fwsc\nsha256=ab\nsha256_end=ab\ntree_sha=cd\nstart=s\nend=e\n")
    (out / "steps.tsv").write_text(f"stress_48000_selftest\t{st_code}\t300\tselftest\t\n"
                                   "stress_48000\t0\t1300\tstress\t\n"
                                   "t1_s2\tnotrun\t0\tmust\tcovered by step stress_48000: its no-usb takes\n")
    (out / "stress_48000_selftest.log").write_text("# stress_48000_selftest: x\n" + st_log)
    (out / "stress_48000.log").write_text("# stress_48000: x\nstress_max: USB 48000 Hz, both: PASS\n")
    (out / "stress_48000-report.txt").write_text("stress_max: USB 48000 Hz, both: PASS\n")
    subprocess.run([sys.executable, SUM, str(out)], capture_output=True)
    md = (out / "SUMMARY.md").read_text()
    return {ln.split("|")[1].strip(): ln.split("|")[2].strip() for ln in md.splitlines()
            if ln.startswith("| ") and ln.count("|") >= 4}


def test_the_stress_step_needs_its_self_test(tmp_path):
    ok = "self-test rhodes: clean renders 0 event(s)  ok\nself-test: PASS\n"
    r = summarize(tmp_path, "0", ok)
    assert r["stress_48000_selftest"] == "PASS" and r["stress_48000"] == "PASS" and r["t1_s2"] == "COVERED"
    for code, log in (("1", "self-test: FAIL\n"), ("0", "self-test rhodes: ...\n"), ("142", ok)):
        r = summarize(tmp_path, code, log)
        assert r["stress_48000_selftest"] == "**FAIL**", (code, log, r)
        assert r["stress_48000"] == "**FAIL**", (code, log, r)
        assert r["t1_s2"] == "**FAIL**"


def test_the_run_waits_for_the_background_steps():
    s = open(SH, encoding="utf-8").read()
    f = s[s.index("\nfinish() {"):]
    assert f.splitlines()[2].strip() == "wait_bg"                      # before the summary, always
    assert 'BG_PIDS+=("$!")' in s and "--self-test-only $D ) &" in s
    assert "stress_*_selftest) echo 600 ;;" in s
