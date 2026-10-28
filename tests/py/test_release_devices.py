# SPDX-License-Identifier: GPL-3.0-only
"""tools/release_devices.py (plan 05 M7-04): the plans against the table in plan section 6.6, the reuse of a record
on the same sha256, the parallel run with stand-ins (no device), the locks, the record's sections."""
import json
import os
import subprocess
import sys
import tarfile
import threading
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_devices as rd  # noqa: E402
import target_closure  # noqa: E402
import test_map  # noqa: E402


@pytest.fixture(scope="module")
def clos():
    return target_closure.closures()


@pytest.fixture
def pkgs(tmp_path):
    (tmp_path / "f.fwsc").write_bytes(b"fm1 package")
    (tmp_path / "m.tar.gz").write_bytes(b"move package")
    return {"fm1": str(tmp_path / "f.fwsc"), "move": str(tmp_path / "m.tar.gz")}


def record(d, name, **targets):
    os.makedirs(d, exist_ok=True)
    json.dump({"targets": targets}, open(os.path.join(d, name), "w"))


def mk(files, pkgs, clos, records=(), **kw):
    return rd.plan("9.9.9", files, pkgs, list(records), py="py", pt=test_map.per_target(files, clos), **kw)


HQ = "firmware/src/hq/rhodes_hq.c"
# plan 6.6's table (the null test proven): (row, files, which package has a record, FM-1 min, Move min, wall)
TABLE = [
    ("docs / tools / tests only", ["README.md", "tools/test_map.py"], ("fm1", "move"), 0, 0, 0),
    ("FM-1-only code", ["firmware/src/usb.c"], ("move",), 25, 0, 25),
    ("Move-only code", ["move/move_bridge.h"], ("fm1",), 0, 30, 30),
    ("HQ-only code", [HQ], ("fm1",), 0, 35, 35),
    ("shared app code", ["firmware/src/notes.c"], (), 22, 17, 22),
    ("shared DSP", ["firmware/src/rhodes.c"], (), 30, 17, 30),
    ("everything", ["firmware/src/usb.c", "firmware/src/rhodes.c", "move/move_bridge.h"], (), 30, 30, 35),
]


@pytest.mark.parametrize("row", TABLE, ids=[r[0] for r in TABLE])
def test_plans_match_the_table(row, pkgs, clos, tmp_path):
    """every row of 6.6 within 5 min (the table's numbers are estimates, refined by the rehearsal)"""
    name, files, have, fm, mv, wall = row
    recs = str(tmp_path / "rec")
    for t in have:
        need = rd.need_of(test_map.per_target(files, clos), files, null_proven=True)[t]["tiers"]
        record(recs, f"device-done-{t}.json", **{t: dict(sha256=rd.sha256(pkgs[t]), tiers=need, verdict="PASS")})
    pl = mk(files, pkgs, clos, [recs], null_proven=True)
    got = {t: v["minutes"] for t, v in pl["targets"].items()}
    assert abs(got["fm1"] - fm) <= 5 and abs(got["move"] - mv) <= 5 and abs(pl["wall"] - wall) <= 5, (got, pl["wall"])
    for t in have:
        assert pl["targets"][t]["reuse"] and not pl["targets"][t]["steps"]


def test_drift_guard_and_t2(pkgs, clos):
    """--full: T1 + the whole T2 on the FM-1 (~95), MT1 + MT2-lite on the Move (~45); without the null test proven a
    DSP change gets T2 (release.py's suites), with it the null step, which is not available yet"""
    pl = mk(["README.md"], pkgs, clos, full=True)
    f, m = pl["targets"]["fm1"], pl["targets"]["move"]
    assert abs(f["minutes"] - 95) <= 5 and abs(m["minutes"] - 45) <= 5 and f["tiers"] == ["T1", "T2"]
    assert m["tiers"] == ["MMIN", "MT1", "MT2"]
    names = [s.name for s in f["steps"]]
    assert names[:2] == ["install", "t1"] and "t2-marimba" in names and "t2-jack-marimba" in names
    pl = mk(["firmware/src/rhodes.c"], pkgs, clos)
    assert pl["targets"]["fm1"]["tiers"] == ["T1", "T2"] and "null" not in [s.name for s in pl["targets"]["fm1"]["steps"]]
    pl = mk(["firmware/src/rhodes.c"], pkgs, clos, null_proven=True)
    null = [s for s in pl["targets"]["fm1"]["steps"] if s.name == "null"]
    assert null and null[0].cmd is None


def test_move_steps(pkgs, clos, tmp_path):
    """the Move's minimum is d2 over the tap with only the null test, the stress (CPU) and the restrike, HQ on and
    off; MT1 the whole d2; HQ adds the long restrike"""
    pl = mk(["firmware/src/notes.c"], pkgs, clos, out_dir=str(tmp_path))
    st = pl["targets"]["move"]["steps"]
    assert [s.name for s in st] == ["install", "min-hq1", "min-hq0"]
    assert "--tests" in st[1].cmd and rd.MOVE_MIN_TESTS in st[1].cmd and "--hq" in st[1].cmd
    pl = mk([HQ, "move/move_bridge.h"], pkgs, clos, out_dir=str(tmp_path))
    assert [s.name for s in pl["targets"]["move"]["steps"]] == ["install", "mt1-hq1", "mt1-hq0", "hq-stress"]


def test_reuse_rules(pkgs, clos, tmp_path):
    """a record is reused only on the same sha256, PASS, with the tiers covering the need; never a fake run's; an
    older record (the FM-1's alone, at the top) counts for the FM-1"""
    recs = str(tmp_path / "r")
    sha = rd.sha256(pkgs["fm1"])
    files = ["firmware/src/rhodes.c"]                                      # (FM-1 needs T1 + T2)
    record(recs, "device-done-a.json", fm1=dict(sha256=sha, tiers=["T1"], verdict="PASS"))
    assert not mk(files, pkgs, clos, [recs])["targets"]["fm1"]["reuse"]  # (T2 missing)
    record(recs, "device-done-b.json", fm1=dict(sha256="0" * 64, tiers=["T1", "T2"], verdict="PASS"))
    assert not mk(files, pkgs, clos, [recs])["targets"]["fm1"]["reuse"]  # (another sha256)
    record(recs, "device-done-c.json", fm1=dict(sha256=sha, tiers=["T1", "T2"], verdict="FAIL"))
    assert not mk(files, pkgs, clos, [recs])["targets"]["fm1"]["reuse"]
    json.dump({"fake": True, "targets": {"fm1": dict(sha256=sha, tiers=["T1", "T2"], verdict="PASS")}},
              open(os.path.join(recs, "device-done-d.json"), "w"))
    assert not mk(files, pkgs, clos, [recs])["targets"]["fm1"]["reuse"]
    json.dump({"sha256": sha, "tiers": ["T1", "T2"], "runs": ["T1 ok"]}, open(os.path.join(recs, "device-done-e.json"), "w"))
    pl = mk(files, pkgs, clos, [recs])
    assert pl["targets"]["fm1"]["reuse"].endswith("device-done-e.json") and pl["targets"]["fm1"]["minutes"] == 0


def test_missing_package_stops(pkgs, clos):
    os.remove(pkgs["move"])
    with pytest.raises(ValueError, match="Move: no package"):
        mk(["README.md"], pkgs, clos)


def test_run_parallel_locks_record(pkgs, clos, tmp_path, monkeypatch):
    """both targets at once with stand-ins: the wall time is the longer plan's, not the sum; both locks are held
    while the steps run and released after; the record has a section per target and the FM-1's at the top (what
    release.py --device-done reads)"""
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path / "locks"))
    pl = mk(["firmware/src/notes.c"], pkgs, clos)
    seen, lk = [], {}

    def runner(step, log):
        lk.setdefault("held", [os.path.isdir(rd.lock.lock_dir(n)) for n in ("DEVICE", "MOVE")])
        seen.append((threading.current_thread().name, step.name))
        time.sleep(0.3)
        return 0, "ok"
    t0 = time.time()
    rec = rd.run(pl, str(tmp_path / "out"), runner, say=lambda m: None)
    dt = time.time() - t0
    n = sum(len(v["steps"]) for v in pl["targets"].values())
    assert dt < 0.3 * n - 0.25, dt                                       # (in parallel: under the sum)
    assert lk["held"] == [True, True]
    assert not os.path.isdir(rd.lock.lock_dir("DEVICE")) and not os.path.isdir(rd.lock.lock_dir("MOVE"))
    assert rec["targets"]["fm1"]["verdict"] == rec["targets"]["move"]["verdict"] == "PASS"
    assert rec["sha256"] == rd.sha256(pkgs["fm1"]) and rec["tiers"] == ["T1"]
    assert rec["targets"]["move"]["tiers"] == ["MMIN"] and len(rec["targets"]["move"]["runs"]) == 3
    assert rd.code_of(rec) == 0


def test_a_failure_ends_its_target_only(pkgs, clos, tmp_path, monkeypatch):
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path / "locks"))
    pl = mk(["firmware/src/notes.c"], pkgs, clos)
    rec = rd.run(pl, str(tmp_path / "out"), rd.fake_runner({"min-hq1"}), say=lambda m: None)
    m = rec["targets"]["move"]
    assert m["verdict"] == "FAIL" and m["tiers"] == [] and len(m["runs"]) == 2       # (min-hq0 never ran)
    assert rec["targets"]["fm1"]["verdict"] == "PASS" and rd.code_of(rec) == 1


def test_a_check_failing_fails_the_step(pkgs, clos, tmp_path, monkeypatch):
    """exit 0 is not enough where release.py checks the output (the CPU limit, the MARIMBA report): the check's
    Stop is the step's FAIL"""
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path / "locks"))
    pl = mk(["firmware/src/rhodes.c"], pkgs, clos)
    rec = rd.run(pl, str(tmp_path / "out"), lambda s, log: (0, "no numbers here"), say=lambda m: None)
    f = rec["targets"]["fm1"]
    assert f["verdict"] == "FAIL" and "t2-cpu-rhodes" in f["runs"][-1] and "exit 0" not in f["runs"][-1], f["runs"]


def test_unavailable_step_is_incomplete(pkgs, clos, tmp_path, monkeypatch):
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path / "locks"))
    pl = mk(["firmware/src/rhodes.c"], pkgs, clos, null_proven=True)
    rec = rd.run(pl, str(tmp_path / "out"), rd.fake_runner(), say=lambda m: None)
    assert rec["targets"]["fm1"]["verdict"] == "INCOMPLETE" and rd.code_of(rec) == 2 and rec["tiers"] == []


def test_a_held_lock_stops_before_anything(pkgs, clos, tmp_path, monkeypatch):
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path / "locks"))
    other = rd.lock.Lock("MOVE", who="another session", wait_s=0, poll_s=0)
    env = dict(os.environ)
    other.acquire()
    os.environ.clear()
    os.environ.update(env)                                  # (not its token: this process is not the holder's child)
    ran = []
    try:
        with pytest.raises(RuntimeError, match="Move is locked"):
            rd.run(mk(["firmware/src/notes.c"], pkgs, clos), str(tmp_path / "out"),
                   lambda s, log: ran.append(s) or (0, ""), say=lambda m: None)
    finally:
        os.environ[rd.lock.env_var("MOVE")] = other.token
        other.release()
    assert not ran and not os.path.isdir(rd.lock.lock_dir("DEVICE"))


def test_move_tarball_check(tmp_path):
    src = tmp_path / "frodhes"
    src.mkdir()
    (src / "dsp.so").write_bytes(b"so one")
    tar = tmp_path / "m.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(src / "dsp.so", "frodhes/dsp.so")
    assert rd.move_tar_matches(str(tar), str(src))[0]
    (src / "dsp.so").write_bytes(b"so two")
    assert not rd.move_tar_matches(str(tar), str(src))[0]


def test_cli_dry_run_and_fake(pkgs, tmp_path):
    env = dict(os.environ, FRODHES_LOCK_ROOT=str(tmp_path / "locks"))
    base = [sys.executable, os.path.join(ROOT, "tools", "release_devices.py"), "9.9.9", "--files", "firmware/src/notes.c",
            "--fm1", pkgs["fm1"], "--move", pkgs["move"], "--records", str(tmp_path / "rec")]
    r = subprocess.run(base + ["--dry-run"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0 and "wall about 23 min" in r.stdout and "min-hq1" in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "rec").exists()
    r = subprocess.run(base + ["--fake"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.load(open(tmp_path / "rec" / "device-done-9.9.9-fake.json"))
    assert rec["fake"] and set(rec["targets"]) == {"fm1", "move"}
    r = subprocess.run(base + ["--dry-run"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert "REUSE" not in r.stdout                                       # (a fake record is never reused)
