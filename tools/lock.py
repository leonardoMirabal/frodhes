#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The shared locks (DEVICE = the FM-1 + the audio interface, HEAVY / HEAVY2 = the CPU slots, any other NAME), with an
owner token the holder's own tools inherit, a dead-holder reaper and a stale-lock alarm.

A lock is a directory (mkdir is atomic), in the MAIN checkout's build/ (every worktree shares it):
  DEVICE  build/hil/DEVICE.lockdir     HEAVY  build/HEAVY.lockdir     HEAVY2  build/HEAVY2.lockdir
  other   build/locks/<NAME>.lockdir
holding owner.json {name, token (random 128-bit), pid, ppid, host, who, why, session, since}, owner (one text line,
"<who> <why> pid <pid> since <date>", what the older scripts read) and heartbeat (its mtime: the holder is alive).

Inheritance: the holder's token is in the environment, FRODHES_LOCK_<NAME>=<token>. A tool that needs the lock and
finds its token equal to the owner's proceeds at once (it is the holder's child) and leaves the lock alone at the end;
a lock whose owner pid is one of the tool's ancestors (an older script that records "pid $$") counts as inherited too.
Anyone else waits. So a session holding DEVICE runs its own tools without waiting on itself.

Reaping: a lock whose owner pid is dead (on this host) AND whose heartbeat is older than 10 min is removed, logged in
build/locks/reap.log; so is an orphan (no owner, no owner.json: a heartbeat at most) older than 2 min. A live holder's
lock is never removed; a lock with an owner line but no recorded pid is never removed. Release removes the whole
directory; the heartbeat is written only in this protocol's locks (owner.json), never in an older script's.
Stale alarm: a lock with no heartbeat for 30 min (DEVICE) / 20 min (others) is reported loudly (LOCK-STALE ...) by
`status` and by every waiter, never removed while its pid lives (an older script's lock, no owner.json and so no
heartbeat, alarms only when its pid is dead or unknown).

  tools/lock.py take NAME [--why TEXT] [--pid PID] [--wait S]   prints  export FRODHES_LOCK_NAME=<token>
          (the owner pid defaults to the calling shell; in a shell that ends, e.g. one agent Bash call, pass the pid
          of what keeps running, or keep the heartbeat with `beat`; else the lock is reaped 10 min after the shell)
  tools/lock.py run NAME [--why TEXT] [--wait S] -- cmd ...       take, run cmd with the token exported, release
  tools/lock.py release NAME [--token T]                          (default: $FRODHES_LOCK_NAME); only the holder's
  tools/lock.py beat NAME [--token T]                             touch the heartbeat (the holder only)
  tools/lock.py status [NAME ...]                                 name, owner, age, heartbeat age, pid alive, STALE
  tools/lock.py reap NAME                                         remove it only if the pid is dead + no heartbeat

Python: `with lock.Lock("DEVICE", who="full_scan", wait_s=3600): ...` (lock.ensure is the same); the holder's
children inherit the token through os.environ. FRODHES_LOCK_ROOT overrides the main checkout (tests).
"""
import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATHS = {"DEVICE": ("hil", "DEVICE.lockdir"), "HEAVY": ("HEAVY.lockdir",), "HEAVY2": ("HEAVY2.lockdir",)}
STALE_S = {"DEVICE": 30 * 60}          # no heartbeat for this long: LOCK-STALE (the others: STALE_DEFAULT_S)
STALE_DEFAULT_S = 20 * 60
REAP_GRACE_S = 10 * 60                 # a dead pid's lock is reaped once its heartbeat is this old
ORPHAN_GRACE_S = 2 * 60                # a lock with no owner at all (no owner, no owner.json: only a heartbeat left, or
                                       # nothing) is reaped once it and its heartbeat are this old (a shell that has just
                                       # done mkdir writes its owner within a second)
BEAT_S = 60.0                          # a holding tool's heartbeat period


def env_var(name):
    return "FRODHES_LOCK_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


def main_repo(start=HERE):
    """the main checkout (a worktree's git common dir's parent): every worktree shares its build/ locks"""
    if os.environ.get("FRODHES_LOCK_ROOT"):
        return os.environ["FRODHES_LOCK_ROOT"]
    try:
        g = subprocess.run(["git", "-C", start, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        if g:
            return os.path.dirname(g)
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.dirname(HERE)


def lock_dir(name, root=None):
    return os.path.join(root or main_repo(), "build", *PATHS.get(name, ("locks", name + ".lockdir")))


def _read(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def pid_alive(pid):
    if not pid or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def ancestors(pid=None):
    """this process's ancestor pids (ps), nearest first"""
    out, pid = [], pid or os.getpid()
    try:
        rows = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return [os.getppid()]
    up = {}
    nums = rows.split()
    for i in range(0, len(nums) - 1, 2):
        up[int(nums[i])] = int(nums[i + 1])
    seen = set()
    while pid in up and pid not in seen and up[pid] > 1:
        seen.add(pid)
        pid = up[pid]
        out.append(pid)
    return out


def info(name, root=None):
    """the lock's state: None when free, else dict(dir, owner (json or {}), text, pid, alive, age_s, beat_s,
    stale, reapable)"""
    d = lock_dir(name, root)
    if not os.path.isdir(d):
        return None
    now = time.time()
    try:
        own = json.loads(_read(os.path.join(d, "owner.json")) or "{}")
    except ValueError:
        own = {}
    text = (_read(os.path.join(d, "owner")) or "").strip()
    pid = own.get("pid")
    if pid is None:
        m = re.search(r"\bpid (\d+)", text)
        pid = int(m.group(1)) if m else None
    since = own.get("since") or _mtime(os.path.join(d, "owner")) or _mtime(d) or now
    beat = max(filter(None, (_mtime(os.path.join(d, "heartbeat")), since)))
    host = own.get("host")
    alive = pid_alive(pid) if (host in (None, socket.gethostname())) else None
    beat_s = now - beat
    orphan = not own and not text and pid is None
    return dict(dir=d, owner=own, text=text or "(no owner file)", pid=pid, alive=alive, age_s=now - since,
                beat_s=beat_s, stale=beat_s > STALE_S.get(name, STALE_DEFAULT_S) and not (alive and not own),
                orphan=orphan, reapable=(alive is False and beat_s > REAP_GRACE_S)
                or (orphan and beat_s > ORPHAN_GRACE_S and now - since > ORPHAN_GRACE_S))


def describe(name, st):
    if st is None:
        return f"{name}: free"
    who = st["owner"].get("who") or st["text"]
    why = st["owner"].get("why")
    live = {True: "alive", False: "DEAD", None: "unknown"}[st["alive"]]
    s = (f"{name}: held by {who}{' (' + why + ')' if why else ''}, pid {st['pid'] or '?'} {live}, "
         f"age {st['age_s'] / 60:.0f} min, heartbeat {st['beat_s'] / 60:.0f} min ago")
    if st["stale"]:
        s = f"LOCK-STALE {name} age={st['age_s'] / 60:.0f}min heartbeat={st['beat_s'] / 60:.0f}min pid " \
            f"{st['pid'] or '?'} {live}: {who} ({st['dir']}; never removed by hand: `tools/lock.py reap {name}` " \
            f"removes it once the pid is dead)"
    return s


def reap(name, root=None, log=print):
    """remove the lock only when its pid is dead and its heartbeat older than REAP_GRACE_S; True when reaped"""
    st = info(name, root)
    if not st or not st["reapable"]:
        return False
    d = st["dir"]
    grave = f"{d}.reaped-{os.getpid()}-{int(time.time())}"
    try:
        os.rename(d, grave)                      # atomic: a second reaper or a new holder sees it gone or new
    except OSError:
        return False
    try:
        again = json.loads(_read(os.path.join(grave, "owner.json")) or "{}")
    except ValueError:
        again = {}
    same = (again.get("token") == st["owner"].get("token") if st["owner"].get("token") else
            (_read(os.path.join(grave, "owner")) or "(no owner file)").strip() == st["text"])
    if not same:
        try:                                     # it changed hands between the look and the rename: put it back
            os.rename(grave, d)
        except OSError:
            log(f"lock {name}: a new holder's lock was moved aside to {grave} while reaping; check it", flush=True)
        return False
    shutil.rmtree(grave, ignore_errors=True)
    msg = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} reaped {name} ({d}): owner {st['text']!r}, pid {st['pid']} dead, "
           f"heartbeat {st['beat_s'] / 60:.0f} min ago; by pid {os.getpid()} {' '.join(sys.argv[:2])}")
    rl = os.path.join(os.path.dirname(lock_dir("x", root)), "reap.log")
    try:
        os.makedirs(os.path.dirname(rl), exist_ok=True)
        with open(rl, "a") as f:
            f.write(msg + "\n")
    except OSError:
        pass
    log(f"LOCK-REAPED {msg}", flush=True)
    return True


class Lock:
    """take NAME, or proceed at once when this process inherits the holder's token; release only our own.
    Exits (SystemExit) after wait_s seconds of waiting."""

    def __init__(self, name, root=None, who=None, why="", wait_s=3600.0, poll_s=10.0, pid=None, beat_s=BEAT_S,
                 say=None):
        self.name, self.root = name, root or main_repo()
        self.dir = lock_dir(name, self.root)
        self.who = who or os.path.basename(sys.argv[0] or "python")
        self.why, self.wait_s, self.poll_s, self.pid, self.beat_s = why, wait_s, poll_s, pid, beat_s
        self.say = say or (lambda m: print(m, flush=True))
        self.token = None          # ours (we took it)
        self.inherited = False
        self.mine = None           # the owner line we wrote (the older tools' attribute)
        self._stop = None

    # the state
    def owner(self):
        return (_read(os.path.join(self.dir, "owner")) or "(no owner file)").strip()

    def owner_token(self):
        try:
            return json.loads(_read(os.path.join(self.dir, "owner.json")) or "{}").get("token")
        except ValueError:
            return None

    def held_by_us(self):
        """the env token matches the owner's, or the owner pid is one of our ancestors"""
        tok, mine = self.owner_token(), os.environ.get(env_var(self.name))
        if tok and mine and secrets.compare_digest(tok, mine):
            return True
        st = info(self.name, self.root)
        if st and st["pid"] and st["owner"].get("host") in (None, socket.gethostname()):
            return st["pid"] in ancestors()
        return False

    def beat(self):
        """touch the heartbeat: only in a lock of this protocol (owner.json). An older script's lock (its owner line
        alone, inherited by its children through the pid) gets no file from us: it releases with rm owner + rmdir,
        which a heartbeat left inside defeated (DEVICE.lockdir stayed behind, empty but for it, 2026-10-25)"""
        if not os.path.exists(os.path.join(self.dir, "owner.json")):
            return
        try:
            with open(os.path.join(self.dir, "heartbeat"), "w") as f:
                f.write(f"{time.time():.0f}\n")
        except OSError:
            pass

    # taking
    def try_take(self):
        os.makedirs(os.path.dirname(self.dir), exist_ok=True)
        try:
            os.mkdir(self.dir)
        except FileExistsError:
            return False
        tok = secrets.token_hex(16)
        pid = self.pid or os.getpid()
        now = time.time()
        own = dict(name=self.name, token=tok, pid=pid, ppid=os.getppid(), host=socket.gethostname(), who=self.who,
                   why=self.why, session=os.getcwd(), since=now, since_text=time.ctime(now))
        tmp = os.path.join(self.dir, f".owner.{os.getpid()}")
        with open(tmp, "w") as f:
            json.dump(own, f, indent=1)
        os.rename(tmp, os.path.join(self.dir, "owner.json"))
        self.mine = f"{self.who}{' ' + self.why if self.why else ''} pid {pid} since {time.ctime(now)}"
        with open(os.path.join(self.dir, "owner"), "w") as f:
            f.write(self.mine + "\n")
        self.beat()
        self.token = tok
        os.environ[env_var(self.name)] = tok        # the holder's children inherit it
        return True

    def acquire(self):
        t0, said = time.time(), -1e9
        while True:
            if self.try_take():
                self.say(f"lock {self.name} taken: {self.dir} ({env_var(self.name)} exported to the children)")
                break
            if self.held_by_us():
                self.inherited = True
                self.beat()
                self.say(f"lock {self.name}: held by this session ({self.owner()}); going on")
                break
            if reap(self.name, self.root, log=lambda m, **k: self.say(m)):
                continue
            if time.time() - t0 > self.wait_s:
                st = info(self.name, self.root)
                sys.exit(f"lock {self.name} {self.dir} still held after {self.wait_s:g} s by: {self.owner()}"
                         + (f"\n{describe(self.name, st)}" if st else ""))
            if time.time() - said > 60:
                st = info(self.name, self.root)
                if st:
                    line = describe(self.name, st)
                    if st["stale"]:
                        print(line, file=sys.stderr, flush=True)
                    self.say(f"waiting for the lock: {line}")
                said = time.time()
            time.sleep(self.poll_s)
        if self.beat_s:
            self._stop = threading.Event()
            threading.Thread(target=self._beater, daemon=True).start()
        return self

    def _beater(self):
        while not self._stop.wait(self.beat_s):
            if self.token and self.owner_token() != self.token:
                return
            self.beat()

    def release(self):
        if self._stop:
            self._stop.set()
        if self.inherited:
            self.inherited = False
            return True
        if not self.token:
            return False
        tok, self.token = self.token, None
        if os.environ.get(env_var(self.name)) == tok:
            del os.environ[env_var(self.name)]
        return release_token(self.name, tok, self.root, say=self.say)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


ensure = Lock


def release_token(name, tok, root=None, say=print):
    d = lock_dir(name, root)
    try:
        cur = json.loads(_read(os.path.join(d, "owner.json")) or "{}").get("token")
    except ValueError:
        cur = None
    if not cur or not tok or not secrets.compare_digest(cur, tok):
        if os.path.isdir(d):
            say(f"lock {name} is no longer ours ({(_read(os.path.join(d, 'owner')) or '?').strip()}); left alone")
        return False
    grave = f"{d}.released-{os.getpid()}"
    try:
        os.rename(d, grave)
        shutil.rmtree(grave, ignore_errors=True)
        say(f"lock {name} released")
        return True
    except OSError as e:
        say(f"could not remove the lock {d}: {e}")
        return False


# ---------------------------------------------------------------- the command line
def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="the main checkout holding build/ (default: this repo's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    r = sub.add_parser("run")
    for p in (t, r):
        p.add_argument("name")
        p.add_argument("--why", default="")
        p.add_argument("--who", default=None)
        p.add_argument("--wait", type=float, default=3600.0)
        p.add_argument("--poll", type=float, default=10.0)
    t.add_argument("--pid", type=int, default=None, help="the holder's pid (default: the calling shell)")
    for c in ("release", "beat"):
        p = sub.add_parser(c)
        p.add_argument("name")
        p.add_argument("--token", default=None)
    s = sub.add_parser("status")
    s.add_argument("names", nargs="*")
    p = sub.add_parser("reap")
    p.add_argument("name")
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[argv.index("--") + 1:] if "--" in argv else []
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    root = a.root or main_repo()
    err = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731

    if a.cmd == "take":
        lk = Lock(a.name, root, who=a.who or f"lock.py take ({os.path.basename(os.getcwd())})", why=a.why,
                  wait_s=a.wait, poll_s=a.poll, pid=a.pid or os.getppid(), beat_s=0, say=err)
        lk.acquire()
        if lk.inherited:
            print(f"export {env_var(a.name)}={os.environ.get(env_var(a.name), '')}")
            return 0
        lk.token = None                          # the lock outlives this process: never released here
        print(f"export {env_var(a.name)}={os.environ[env_var(a.name)]}")
        return 0
    if a.cmd == "run":
        if not cmd:
            ap.error("run NAME -- command ...")
        lk = Lock(a.name, root, who=a.who or f"lock.py run {os.path.basename(cmd[0])}", why=a.why, wait_s=a.wait,
                  poll_s=a.poll, say=err)

        def term(sig, _):
            raise SystemExit(128 + sig)
        signal.signal(signal.SIGTERM, term)
        signal.signal(signal.SIGHUP, term)
        with lk:
            p = subprocess.Popen(cmd)
            try:
                return p.wait()
            except BaseException:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                raise
    if a.cmd in ("release", "beat"):
        tok = a.token or os.environ.get(env_var(a.name))
        d = lock_dir(a.name, root)
        try:
            cur = json.loads(_read(os.path.join(d, "owner.json")) or "{}").get("token")
        except ValueError:
            cur = None
        if not tok or not cur or not secrets.compare_digest(cur, tok):
            err(f"lock {a.name}: not the holder (no matching token: {env_var(a.name)} or --token); nothing done")
            return 1
        if a.cmd == "beat":
            Lock(a.name, root).beat()
            return 0
        return 0 if release_token(a.name, tok, root, say=err) else 1
    if a.cmd == "status":
        names = a.names or sorted(set(PATHS) | {
            f[:-len(".lockdir")] for f in (os.listdir(os.path.join(root, "build", "locks"))
                                           if os.path.isdir(os.path.join(root, "build", "locks")) else [])
            if f.endswith(".lockdir")})
        stale = 0
        for n in names:
            st = info(n, root)
            stale += bool(st and st["stale"])
            (err if st and st["stale"] else print)(describe(n, st))
        return 2 if stale else 0
    if a.cmd == "reap":
        st = info(a.name, root)
        if reap(a.name, root, log=lambda m, **k: err(m)):
            return 0
        err(describe(a.name, st) + ("" if not st else "; not reaped (only a dead pid with no heartbeat for "
                                                        f"{REAP_GRACE_S // 60} min is)"))
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(cli())
