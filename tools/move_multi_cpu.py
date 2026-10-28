#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""PROTOTYPE (move-multi cost gate): N Frodhes instances (the loader build, build/move-multi/frodhes) in slots 1..N,
each playing usb-stress's worst take (16 voices, every effect heavy, the pedal) at the same time over the tap.
Reads per instance: the plugin's own callback ring (inst/<k>/cpu-ring.log); for the whole frame: Schwung's
/dev/shm/schwung-perf (per-slot synth render avg / max, the frame's pre-ioctl total, the overrun counter), sampled
once a second; the process RSS before and after the loads; the click detector on every tap.

    tools/move_multi_cpu.py --n 1 2 3 4 [--secs 20]     (takes the MOVE lock; slots 1..4 must be free or Frodhes)

Slots above N are emptied for a run (synth:module ""), slots 1..N get "frodhes". The caller restores the set."""
import argparse
import json
import os
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import hil_crackle as hc  # noqa: E402
import move_hil as mh  # noqa: E402
import move_install as mi  # noqa: E402
import move_ws  # noqa: E402

HOST = os.environ.get("MOVE_HOST", "ableton@move.local")
URL = move_ws.URL
INST = "/data/UserData/schwung/module_data/frodhes/inst"
# /schwung-perf offsets (schwung-manager perf_shm.go, version 1)
P_PERIOD, P_TOT_AVG, P_TOT_MAX, P_PRE_AVG, P_PRE_MAX = 24, 32, 40, 48, 56
P_SYN_AVG, P_SYN_MAX, P_RND_MAX, P_OVR = 544, 576, 512, 844


def ssh(cmd, timeout=30):
    r = subprocess.run(["ssh", *mi.SSH_OPTS, HOST, cmd], capture_output=True, timeout=timeout)
    return r.stdout


def perf_decode(b):
    u64 = lambda o: struct.unpack_from("<Q", b, o)[0]   # noqa: E731
    return {"period": u64(P_PERIOD), "tot_avg": u64(P_TOT_AVG), "tot_max": u64(P_TOT_MAX),
            "pre_avg": u64(P_PRE_AVG), "pre_max": u64(P_PRE_MAX),
            "syn_avg": [u64(P_SYN_AVG + 8 * i) for i in range(4)], "syn_max": [u64(P_SYN_MAX + 8 * i) for i in range(4)],
            "rnd_max": [u64(P_RND_MAX + 8 * i) for i in range(4)],
            "overruns": struct.unpack_from("<I", b, P_OVR)[0]}


class Perf(threading.Thread):
    """/dev/shm/schwung-perf once a second over one ssh"""
    def __init__(self):
        super().__init__(daemon=True)
        self.rows, self.stop = [], False
        self.p = subprocess.Popen(["ssh", *mi.SSH_OPTS, HOST,
                                   "while :; do cat /dev/shm/schwung-perf; sleep 1; done"], stdout=subprocess.PIPE)

    def run(self):
        while not self.stop:
            b = self.p.stdout.read(4096)
            if len(b) < 4096:
                break
            self.rows.append((time.time(), perf_decode(b)))

    def end(self):
        self.stop = True
        self.p.kill()


def rss_kb():
    out = ssh("grep VmRSS /proc/$(pidof MoveOriginal | cut -d' ' -f1)/status").decode()
    return int(out.split()[1]) if out.strip() else -1


def slot_modules(w):
    m = {}
    for s in range(4):
        st = w.subscribe(s)
        m[s] = (w.info.get(s) or {}).get("synth", "")
    return m


def set_module(w, s, mod, wait=5.0):
    w.set_param(s, "synth:module", mod)
    w.pump(wait)


def fetch_any(local):
    """fetch(take) for the TapRig: the tap file in whichever inst/<k>/tap holds it"""
    def fetch(take):
        d = ssh("for d in %s/*/tap; do test -f $d/%s.json && echo $d; done" % (INST, take)).decode().strip()
        if not d:
            return None
        os.makedirs(local, exist_ok=True)
        js, wav = os.path.join(local, take + ".json"), os.path.join(local, take + ".wav")
        for f, l in ((take + ".json", js), (take + ".wav", wav)):
            if subprocess.run(["scp", *mi.SSH_OPTS, "-q", "%s:%s/%s" % (HOST, d, f), l]).returncode:
                return None
        ssh("rm -f %s/%s.wav %s/%s.json" % (d, take, d, take))
        return wav, json.load(open(js))
    return fetch


def run_n(n, secs, outdir, seed=1):
    res = {"n": n}
    with move_ws.Remote(URL) as w:
        t0 = time.time()
        rss0 = rss_kb()
        for s in range(4):
            set_module(w, s, "frodhes" if s < n else "", 6.0 if s < n else 2.0)
        res["load_s"] = round(time.time() - t0, 1)
        res["rss_kb"] = rss_kb()
        res["rss0_kb"] = rss0
        res["inst"] = ssh("ls -d %s/*/ 2>/dev/null; cat %s/*/load.txt 2>/dev/null" % (INST, INST)).decode()
    ssh("rm -f %s/*/cpu-ring.log" % INST)
    rigs = [mh.TapRig(URL, fetch_any(os.path.join(outdir, "n%d" % n)), slot=s) for s in range(n)]
    ccs = hc.engine_ccs("rhodes", hc.HEAVY)
    takes, evs = [None] * n, [None] * n
    for s, r in enumerate(rigs):
        r._send("cc", 120, 0)
        for c, v in ccs:
            r._send("cc", c, v)
        r._set("synth:dev_cpu_log", 1)
        ev, dur = hc.worst_events(16, secs, np.random.default_rng(seed + s))
        evs[s] = (sorted(ev, key=lambda e: e[0]), dur)
    perf = Perf()
    perf.start()
    time.sleep(3)
    base = list(perf.rows)

    def play(s):
        try:
            takes[s] = rigs[s].rec(evs[s][1], evs[s][0])
        except Exception as e:     # noqa: BLE001
            takes[s] = {"error": str(e)}
    th = [threading.Thread(target=play, args=(s,)) for s in range(n)]
    tp0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    perf.end()
    for r in rigs:
        r._set("synth:dev_cpu_log", 0)
    time.sleep(0.6)
    # the callback rings, one an instance
    rings = {}
    for d in ssh("ls %s/*/cpu-ring.log 2>/dev/null" % INST).decode().split():
        rows = mh.parse_ring(ssh("cat %s" % d).decode())
        rings[d.split("/")[-2]] = mh.cpu_summary(rows)
    res["rings"] = rings
    during = [p for t, p in perf.rows if t >= tp0 + 3]
    if during:
        res["perf"] = {"tot_max": max(p["tot_max"] for p in during), "pre_max": max(p["pre_max"] for p in during),
                       "pre_avg": max(p["pre_avg"] for p in during), "period": during[-1]["period"],
                       "syn_max": [max(p["syn_max"][i] for p in during) for i in range(4)],
                       "syn_avg": [max(p["syn_avg"][i] for p in during) for i in range(4)],
                       "overruns": during[-1]["overruns"] - (base[-1][1]["overruns"] if base else during[0]["overruns"])}
    res["takes"] = []
    for s in range(n):
        tk = takes[s]
        if not tk or "error" in tk:
            res["takes"].append({"slot": s + 1, "error": (tk or {}).get("error", "none")})
            continue
        g = mh.glitches(tk["x"], tk["sr"], evs[s][0], ccs=ccs)
        gated = [e for e in g if e[1] in hc.GATED]
        res["takes"].append({"slot": s + 1, "secs": round(evs[s][1], 1), "gated": len(gated), "events": len(g),
                             "first": [list(e) for e in gated[:5]], "xruns": tk["xruns"],
                             "peak_dbfs": round(20 * np.log10(np.abs(tk["x"]).max() / 32767 + 1e-12), 1)})
    for r in rigs:
        r.remote.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--secs", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join(mh.ROOT if hasattr(mh, "ROOT") else ".", "build", "move-multi", "hil"))
    a = ap.parse_args()
    out = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    allr = []
    with mi.move_lock("move_multi_cpu"):
        for n in a.n:
            r = run_n(n, a.secs, out)
            print(json.dumps(r, indent=1, default=str), flush=True)
            allr.append(r)
            json.dump(allr, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
    print("report:", os.path.join(out, "report.json"))


if __name__ == "__main__":
    main()
