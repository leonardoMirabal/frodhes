#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Install the Frodhes module on the Move (M1-19), with the previous one kept and a rollback. DRY RUN by default:
it prints every command; --go runs them.

    tools/move_install.py [--go] install [--src DIR]    build/move/frodhes/ (dsp.so, module.json, help.json, sustain.js, screen.js) -> the Move
    tools/move_install.py [--go] rollback               the previous module back (the current one kept aside)
    tools/move_install.py info                          the installed version, dsp.so's sha256, the kept modules
    --host USER@HOST (default $MOVE_HOST, else ableton@move.local); --target-dir DIR: a local folder stands in for
    the Move's sound generator folder (the tests; nothing goes over the network)

How an install switches (never a loaded dsp.so overwritten in place: scp truncates the mapped file and the host
process crashes; the probe's install.sh found the same rule):
  1. the files go into a staging folder beside the module (.frodhes.staging-<time>), and their sha256 is checked
     there against the local files;
  2. files the running module keeps in its own folder that are not part of the package (a settings or pattern file,
     if the data folder turns out to be the module's: D1) are copied into the staging folder;
  3. two renames: frodhes -> .frodhes.prev-<time>, the staging folder -> frodhes. A running instance keeps its
     mapping of the old file (the inode lives on in the kept folder); the next load takes the new one;
  4. kept folders beyond --keep (default 2) are deleted, oldest first.
A rollback is the same two renames the other way: frodhes -> .frodhes.rolledback-<time>, the newest kept -> frodhes.
Unload Frodhes from every slot before an install or a rollback (the device checklist says so too)."""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS = "/data/UserData/schwung/modules/sound_generators"
MODULE_ID = "frodhes"
PACKAGE = ("dsp.so", "module.json", "help.json", "sustain.js", "screen.js")
REQUIRED = ("dsp.so", "module.json")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Target:
    """where the commands go: the Move over ssh / scp, or a local folder (--target-dir). run(cmd) runs a POSIX shell
    command there; put(local, remote) copies a file there; in a dry run both only print"""

    def __init__(self, host=None, local_dir=None, go=False, out=print):
        self.host, self.local, self.go, self.out = host, local_dir, go, out
        self.mods = local_dir if local_dir else MODS
        self.log = []

    def _exec(self, argv):
        self.log.append(argv)
        self.out("+ " + " ".join(shlex.quote(a) for a in argv))
        if not self.go:
            return 0, ""
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode:
            self.out("  (exit %d) %s" % (r.returncode, (r.stderr or r.stdout).strip()[-500:]))
        return r.returncode, r.stdout

    def run(self, cmd):
        if self.local:
            return self._exec(["sh", "-c", cmd])
        return self._exec(["ssh", *SSH_OPTS, self.host, cmd])

    def put(self, local, remote):
        if self.local:
            return self._exec(["cp", local, remote])
        return self._exec(["scp", *SSH_OPTS, "-q", local, "%s:%s" % (self.host, remote)])

    def need(self, cmd, what):
        code, out = self.run(cmd)
        if code:
            raise SystemExit("move_install: %s failed: stop (nothing switched)" % what)
        return out


def q(p):
    return shlex.quote(p)


def check_src(src):
    for f in REQUIRED:
        if not os.path.isfile(os.path.join(src, f)):
            raise SystemExit("move_install: %s missing: build first (make move)" % os.path.join(src, f))
    mod = json.load(open(os.path.join(src, "module.json")))
    if mod.get("id") != MODULE_ID:
        raise SystemExit("move_install: module.json id is %r, not %r" % (mod.get("id"), MODULE_ID))
    return mod, [f for f in PACKAGE if os.path.isfile(os.path.join(src, f))]


def install(t, src, keep=2, stamp=None):
    mod, files = check_src(src)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    m, cur = t.mods, t.mods + "/" + MODULE_ID
    stage, prev = "%s/.%s.staging-%s" % (m, MODULE_ID, stamp), "%s/.%s.prev-%s" % (m, MODULE_ID, stamp)
    t.out("install %s %s (%s) -> %s" % (MODULE_ID, mod.get("version"), ", ".join(files), cur))
    t.need("test -d %s && mkdir %s" % (q(m), q(stage)), "the staging folder")
    for f in files:
        if t.put(os.path.join(src, f), stage + "/" + f)[0]:
            t.run("rm -rf %s" % q(stage))
            raise SystemExit("move_install: copying %s failed: stop (staging removed, nothing switched)" % f)
    check = "cd %s && " % q(stage) + " && ".join(
        '[ "$(sha256sum %s | cut -d" " -f1)" = %s ]' % (q(f), sha256(os.path.join(src, f))) for f in files)
    if t.run(check)[0]:
        t.run("rm -rf %s" % q(stage))
        raise SystemExit("move_install: the staged files' sha256 differ: stop (staging removed, nothing switched)")
    pkg = " ".join(q(f) for f in files)
    t.need("if [ -d %s ]; then cd %s && for f in * .[!.]*; do [ -e \"$f\" ] || continue; case \" %s \" in "
           "*\" $f \"*) ;; *) cp -pR \"$f\" %s/ ;; esac; done; fi" % (q(cur), q(cur), pkg, q(stage)),
           "carrying the module folder's own files over")
    t.need("cd %s && if [ -d %s ]; then mv %s %s; fi && mv %s %s" % (
        q(m), MODULE_ID, MODULE_ID, q(os.path.basename(prev)), q(os.path.basename(stage)), MODULE_ID), "the switch")
    if keep > 0:                                   # (POSIX tail +N: the Move's busybox and macOS alike)
        t.run("cd %s && ls -1d .%s.prev-* 2>/dev/null | sort -r | tail -n +%d | while read d; do rm -rf \"$d\"; done"
              % (q(m), MODULE_ID, keep + 1))
    return cur, prev


def rollback(t, stamp=None):
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    m = t.mods
    t.need("cd %s && p=$(ls -1d .%s.prev-* 2>/dev/null | sort | tail -n 1) && [ -n \"$p\" ] && "
           "if [ -d %s ]; then mv %s .%s.rolledback-%s; fi && mv \"$p\" %s" % (
               q(m), MODULE_ID, MODULE_ID, MODULE_ID, MODULE_ID, stamp, MODULE_ID),
           "the rollback (is there a kept module?)")


def info(t):
    m, cur = t.mods, t.mods + "/" + MODULE_ID
    code, out = t.run("cd %s 2>/dev/null && { cat %s/module.json 2>/dev/null | grep '\"version\"'; "
                      "sha256sum %s/dsp.so 2>/dev/null; ls -1d .%s.* 2>/dev/null; } || echo 'not installed'"
                      % (q(m), MODULE_ID, MODULE_ID, MODULE_ID))
    if out:
        t.out(out.rstrip())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("install", "rollback", "info"))
    ap.add_argument("--go", action="store_true", help="run the commands (default: print them)")
    ap.add_argument("--src", default=os.path.join(ROOT, "build", "move", MODULE_ID))
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--target-dir", help="a local folder in place of the Move's sound generator folder (tests)")
    ap.add_argument("--keep", type=int, default=2, help="previous modules kept (default 2)")
    a = ap.parse_args(argv)
    go = a.go or a.cmd == "info"                       # (info only reads)
    t = Target(host=None if a.target_dir else a.host, local_dir=a.target_dir, go=go)
    if not go:
        print("(dry run: add --go to execute; %s)" % (a.target_dir or a.host))
    if a.cmd == "install":
        install(t, a.src, a.keep)
    elif a.cmd == "rollback":
        rollback(t)
    else:
        info(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
