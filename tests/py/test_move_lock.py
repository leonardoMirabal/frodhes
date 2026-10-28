# SPDX-License-Identifier: GPL-3.0-only
"""The MOVE lock: every tool that drives the Move stops at once while another session holds it (naming the holder),
goes on under the holder's own token, and the offline modes (a fake, a dry run, a local folder) never take it."""
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_hil as mh  # noqa: E402

T = lambda *p: os.path.join(ROOT, "tools", *p)              # noqa: E731


@pytest.fixture
def held(tmp_path):
    """MOVE held by another process (a sleeper), under a lock root of the test's own: (env without the token, the
    holder's token)"""
    env = dict(os.environ, FRODHES_LOCK_ROOT=str(tmp_path / "locks"))
    env.pop("FRODHES_LOCK_MOVE", None)
    env.pop("FRODHES_PROBE_LOCKED", None)
    sl = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    r = subprocess.run([sys.executable, T("lock.py"), "take", "MOVE", "--why", "another session's test", "--pid",
                        str(sl.pid), "--wait", "0"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    tok = re.search(r"FRODHES_LOCK_MOVE=(\w+)", r.stdout).group(1)
    yield env, tok
    subprocess.run([sys.executable, T("lock.py"), "release", "MOVE", "--token", tok], env=env, capture_output=True)
    sl.kill()


DRIVE = [
    [T("move_install.py"), "install", "--go", "--host", "nobody@127.0.0.1"],
    [T("move_install.py"), "info", "--host", "nobody@127.0.0.1"],
    [T("move_hil.py"), "d2", "--path", "tap", "--ws", "ws://127.0.0.1:9/x", "--host", "nobody@127.0.0.1"],
    [T("move_hil.py"), "usb-note", "--path", "tap", "--ws", "ws://127.0.0.1:9/x"],
    [T("move_hil.py"), "usb-levels", "--ws", "ws://127.0.0.1:9/x"],
    [T("move_hil.py"), "install", "--go", "--host", "nobody@127.0.0.1"],
    [T("move_ws.py"), "info", "--url", "ws://127.0.0.1:9/x", "--timeout", "1"],
]


@pytest.mark.parametrize("cmd", DRIVE, ids=[" ".join(os.path.basename(c[0]) for c in [x]) + " " + x[1] for x in DRIVE])
def test_held_lock_stops_at_once(held, cmd):
    env, _ = held
    t0 = time.time()
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "lock MOVE" in out and "another session's test" in out, out[-1500:]
    assert time.time() - t0 < 30


def test_probe_script_takes_it(held):
    env, _ = held
    r = subprocess.run(["sh", os.path.join(ROOT, "move", "probe", "install.sh"), "--go", "facts"], capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=60)
    assert r.returncode != 0 and "lock MOVE" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_holders_token_goes_on(held):
    """a child of the holder (its token in the environment, as tools/release_devices.py's steps) is not stopped"""
    env, tok = held
    env = dict(env, FRODHES_LOCK_MOVE=tok)
    r = subprocess.run([sys.executable, T("move_ws.py"), "info", "--url", "ws://127.0.0.1:9/x", "--timeout", "1"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
    assert r.returncode == 2 and "schwung ws://127.0.0.1:9/x" in r.stdout and "still held" not in r.stderr, r.stdout + r.stderr


def test_offline_modes_never_take_it(held, tmp_path):
    env, _ = held
    for cmd in ([T("move_install.py"), "install", "--host", "nobody@127.0.0.1"],                    # (a dry run)
                [T("move_install.py"), "info", "--target-dir", str(tmp_path / "mods")],
                [T("move_hil.py"), "usb-note", "--fake", "--out", str(tmp_path / "n")],
                [T("move_hil.py"), "crackle"]):
        r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
        assert "lock MOVE" not in r.stdout + r.stderr, (cmd, r.stdout[-800:] + r.stderr[-800:])


def test_drives_the_move_table():
    a = lambda cmd, **k: SimpleNamespace(**dict(dict(cmd=cmd, fake=False, analyze=None, target_dir=None, file=None,  # noqa: E731
                                                     go=False), **k))
    yes = [a("d2"), a("usb-stress"), a("usb-levels"), a("install", go=True), a("info"), a("cpu", go=True)]
    no = [a("d2", fake=True), a("usb-note", fake=True), a("usb-devices"), a("usb-host", analyze="x"),
          a("install"), a("cpu", file="r.log"), a("logs", go=True, target_dir="d"), a("crackle"), a("must"), a("null")]
    assert all(mh.drives_the_move(x) for x in yes) and not any(mh.drives_the_move(x) for x in no)
