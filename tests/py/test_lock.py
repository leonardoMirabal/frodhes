# SPDX-License-Identifier: GPL-3.0-only
"""tools/lock.py: the holder's children pass on its token, a stranger waits, a dead holder is reaped, a live one never,
a lock without a heartbeat raises the stale alarm (and stays)"""
import json
import os
import subprocess
import sys
import time

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
sys.path.insert(0, TOOLS)
import lock  # noqa: E402

LOCK = os.path.join(TOOLS, "lock.py")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("FRODHES_LOCK_ROOT", str(tmp_path))
    for n in ("DEVICE", "HEAVY", "HEAVY2", "T"):
        monkeypatch.delenv(lock.env_var(n), raising=False)
    return tmp_path


def cli(*args, env=None, timeout=30):
    return subprocess.run([sys.executable, LOCK, *args], capture_output=True, text=True, timeout=timeout,
                          env=env or os.environ.copy())


@pytest.fixture
def holder(root):
    """a live holder: a sleeping process, the lock taken for it by `lock.py take --pid`"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    r = cli("take", "DEVICE", "--why", "test", "--pid", str(p.pid), "--wait", "0")
    assert r.returncode == 0, r.stderr
    var, tok = r.stdout.strip().split(" ", 1)[1].split("=", 1)
    assert var == "FRODHES_LOCK_DEVICE" and len(tok) == 32
    yield p, tok
    p.kill()
    p.wait()


def age(root, name, s):
    """make the lock look s seconds old, its heartbeat too"""
    d = lock.lock_dir(name, str(root))
    own = json.load(open(os.path.join(d, "owner.json")))
    own["since"] -= s
    json.dump(own, open(os.path.join(d, "owner.json"), "w"))
    t = time.time() - s
    os.utime(os.path.join(d, "heartbeat"), (t, t))


CHILD = ("import sys; sys.path.insert(0, %r); import lock\n"
         "with lock.Lock('DEVICE', wait_s=float(sys.argv[1]), poll_s=0.05, who='child') as l:\n"
         "    print('INHERITED' if l.inherited else 'TOOK', flush=True)\n") % os.path.abspath(TOOLS)


def child(wait, env):
    return subprocess.run([sys.executable, "-c", CHILD, str(wait)], capture_output=True, text=True, timeout=30,
                          env=env)


def test_paths(root):
    assert lock.lock_dir("DEVICE") == os.path.join(str(root), "build", "hil", "DEVICE.lockdir")
    assert lock.lock_dir("HEAVY2") == os.path.join(str(root), "build", "HEAVY2.lockdir")
    assert lock.lock_dir("DOCKER") == os.path.join(str(root), "build", "locks", "DOCKER.lockdir")


def test_child_with_the_token_passes_a_stranger_waits(root, holder):
    p, tok = holder
    d = lock.lock_dir("DEVICE")
    own = (open(os.path.join(d, "owner")).read())
    assert f"pid {p.pid} " in own                                   # what the older scripts parse
    env = dict(os.environ, FRODHES_LOCK_DEVICE=tok)
    r = child(0, env)
    assert r.returncode == 0 and "INHERITED" in r.stdout, r.stdout + r.stderr
    assert os.path.isdir(d) and json.load(open(os.path.join(d, "owner.json")))["token"] == tok   # left to the holder
    env = dict(os.environ)
    env.pop("FRODHES_LOCK_DEVICE", None)
    t0 = time.time()
    r = child(0.5, env)                                             # a stranger: waits, then gives up
    assert r.returncode != 0 and "still held" in r.stderr and time.time() - t0 >= 0.5, r.stdout + r.stderr
    r = child(0.3, dict(env, FRODHES_LOCK_DEVICE="0" * 32))        # a wrong token is a stranger too
    assert r.returncode != 0 and "still held" in r.stderr
    assert cli("release", "DEVICE").returncode == 1                 # only the holder releases
    assert os.path.isdir(d)
    assert cli("release", "DEVICE", "--token", tok).returncode == 0
    assert not os.path.exists(d)


def test_run_exports_the_token_and_releases(root):
    r = cli("run", "HEAVY", "--why", "t", "--", sys.executable, "-c",
            "import os, sys; sys.path.insert(0, %r); import lock; d = lock.lock_dir('HEAVY');"
            "t = __import__('json').load(open(d + '/owner.json'))['token'];"
            "print('OK' if os.environ['FRODHES_LOCK_HEAVY'] == t else 'BAD')" % os.path.abspath(TOOLS))
    assert r.returncode == 0 and r.stdout.strip() == "OK", r.stdout + r.stderr
    assert not os.path.exists(lock.lock_dir("HEAVY"))
    r = cli("run", "HEAVY", "--", sys.executable, "-c", "raise SystemExit(7)")
    assert r.returncode == 7 and not os.path.exists(lock.lock_dir("HEAVY"))


def test_dead_pid_is_reaped_live_never(root, holder):
    p, tok = holder
    d = lock.lock_dir("DEVICE")
    age(root, "DEVICE", 3600)                       # an hour without a heartbeat: alarm, but the pid lives
    st = lock.info("DEVICE")
    assert st["alive"] is True and st["stale"] and not st["reapable"]
    assert cli("reap", "DEVICE").returncode == 1 and os.path.isdir(d)
    env = dict(os.environ)
    env.pop("FRODHES_LOCK_DEVICE", None)
    r = child(0.3, env)
    assert r.returncode != 0 and os.path.isdir(d)                   # waited on it, never removed it
    assert "LOCK-STALE DEVICE" in r.stderr
    p.kill()
    p.wait()
    age(root, "DEVICE", 0)                          # dead, but a fresh heartbeat (a hand lock between commands)
    assert not lock.info("DEVICE")["reapable"] and cli("reap", "DEVICE").returncode == 1
    age(root, "DEVICE", lock.REAP_GRACE_S + 5)      # dead and no heartbeat for > 10 min: a waiter reaps it, takes it
    r = child(5, env)
    assert r.returncode == 0 and "TOOK" in r.stdout and "LOCK-REAPED" in r.stdout, r.stdout + r.stderr
    assert not os.path.exists(d)
    assert "reaped DEVICE" in open(os.path.join(str(root), "build", "locks", "reap.log")).read()


def test_lock_without_a_pid_is_never_reaped(root):
    d = lock.lock_dir("HEAVY")
    os.makedirs(d)
    open(os.path.join(d, "owner"), "w").write("agent-x by hand\n")
    t = time.time() - 7200
    os.utime(os.path.join(d, "owner"), (t, t))
    st = lock.info("HEAVY")
    assert st["pid"] is None and st["stale"] and not st["reapable"]
    assert not lock.reap("HEAVY") and os.path.isdir(d)


def test_stale_alarm_in_status(root, holder):
    r = cli("status")
    assert r.returncode == 0 and "DEVICE: held by" in r.stdout and "HEAVY: free" in r.stdout, r.stdout + r.stderr
    age(root, "DEVICE", 31 * 60)
    r = cli("status", "DEVICE")
    assert r.returncode == 2 and "LOCK-STALE DEVICE" in r.stderr and "pid" in r.stderr
    assert os.path.isdir(lock.lock_dir("DEVICE"))                   # reported, never removed
    p, tok = holder
    assert cli("beat", "DEVICE", "--token", tok).returncode == 0    # the holder's heartbeat clears it
    assert not lock.info("DEVICE")["stale"]


def test_ancestor_pid_counts_as_the_holder(root):
    """an older script's lock (owner 'pid $$', no token): its own children go on"""
    d = lock.lock_dir("DEVICE")
    os.makedirs(d)
    open(os.path.join(d, "owner"), "w").write(f"run_final_rev.sh x pid {os.getpid()} since now\n")
    t = time.time() - 7200
    os.utime(os.path.join(d, "owner"), (t, t))
    assert not lock.info("DEVICE")["stale"]              # no heartbeat protocol, a live pid: no alarm
    env = dict(os.environ)
    env.pop("FRODHES_LOCK_DEVICE", None)
    r = child(0, env)
    assert r.returncode == 0 and "INHERITED" in r.stdout, r.stdout + r.stderr
    assert os.path.isdir(d)


def test_in_process_take_and_release(root):
    with lock.Lock("T", wait_s=0, poll_s=0.01, beat_s=0) as a:
        assert not a.inherited and os.environ[lock.env_var("T")] == a.token
        with lock.Lock("T", wait_s=0, poll_s=0.01) as b:            # our own env token: inherited
            assert b.inherited
        assert os.path.isdir(a.dir)
    assert not os.path.exists(a.dir) and lock.env_var("T") not in os.environ


def test_an_inherited_older_lock_gets_no_heartbeat(root):
    """an older script's lock (owner 'pid $$', no owner.json): its child goes on without writing into it, so the
    script's own release (rm owner; rmdir) still removes the directory"""
    d = lock.lock_dir("DEVICE")
    os.makedirs(d)
    open(os.path.join(d, "owner"), "w").write(f"run_final_rev.sh x pid {os.getpid()} since now\n")
    env = dict(os.environ)
    env.pop("FRODHES_LOCK_DEVICE", None)
    r = child(0, env)
    assert r.returncode == 0 and "INHERITED" in r.stdout, r.stdout + r.stderr
    assert os.listdir(d) == ["owner"], os.listdir(d)
    os.remove(os.path.join(d, "owner"))
    os.rmdir(d)                                     # the older scripts' release: works


def test_release_removes_the_whole_directory(root):
    with lock.Lock("DEVICE", wait_s=0, poll_s=0.01, beat_s=0.01) as a:
        time.sleep(0.1)                             # the beater has written the heartbeat
        assert os.path.exists(os.path.join(a.dir, "heartbeat"))
    time.sleep(0.1)
    assert not os.path.exists(a.dir) and not [f for f in os.listdir(os.path.dirname(a.dir)) if "DEVICE" in f]


def test_heartbeat_only_lock_is_reaped(root):
    """an orphan (no owner, no owner.json, only a heartbeat left): reaped once older than ORPHAN_GRACE_S, then taken;
    a fresh empty one (a shell between its mkdir and its owner line) never"""
    d = lock.lock_dir("DEVICE")
    os.makedirs(d)
    assert not lock.info("DEVICE")["reapable"] and not lock.reap("DEVICE")       # just made: left alone
    open(os.path.join(d, "heartbeat"), "w").write("1\n")
    t = time.time() - lock.ORPHAN_GRACE_S - 5
    os.utime(os.path.join(d, "heartbeat"), (t, t))
    assert not lock.info("DEVICE")["reapable"]      # the directory itself is new
    os.utime(d, (t, t))
    st = lock.info("DEVICE")
    assert st["orphan"] and st["pid"] is None and st["reapable"]
    env = dict(os.environ)
    env.pop("FRODHES_LOCK_DEVICE", None)
    r = child(5, env)
    assert r.returncode == 0 and "TOOK" in r.stdout and "LOCK-REAPED" in r.stdout, r.stdout + r.stderr
    assert not os.path.exists(d)
