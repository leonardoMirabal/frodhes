#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Several Frodhes on the Move at once (the loader build: dsp.so + engine.so, move/move_loader.c, as installed by
tools/move_install.py): N instances in slots 1..N, each playing usb-stress's worst take (16 voices, every effect
heavy, the pedal) at the same time over the tap. Reads per instance: the plugin's own callback ring
(inst/<k>/cpu-ring.log); for the whole frame: Schwung's /dev/shm/schwung-perf (per-slot synth render avg / max, the
frame's pre-ioctl total, the overrun counter), sampled once a second; the process RSS before and after the loads; the
click detector on every tap (and on the USB-C audio with --usb).

    tools/move_multi_cpu.py --n 2 3 [--secs 30] [--usb]   (takes the MOVE lock; slots 1..4 must be free or Frodhes)

Slots above N are emptied for a run (synth:module ""), slots 1..N get "frodhes". Another module in any slot stops the
run before anything changes (emptying it loses its sound: a re-pick starts on the module's defaults and Schwung saves
that over the set), unless --take-slots (empties them) or --keep-others (leaves them as they are, idle, and runs the
instances in the free slots only). At the end, also after a failure, ctrl-C or SIGTERM, every slot the run changed
gets its module back, and a slot that held Frodhes its parameters (move_hil.state_restore).

Every write to Schwung is paced: a module change waits for the slot_info that confirms it, and a write the manager
refuses with "web param set ring full" (its set ring: 32 entries, one a frame) is sent again after a growing pause
(RING_BACKOFF_S); a take's upload that hits it is uploaded again whole (the 0.9.9 run, 2026-11-08: the third slot's
upload hit the ring, the other two broke on the barrier, and Frodhes stayed in slots 3-4)."""
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
INST = mi.INST
# /schwung-perf offsets (schwung-manager perf_shm.go, version 1)
P_PERIOD, P_TOT_AVG, P_TOT_MAX, P_PRE_AVG, P_PRE_MAX = 24, 32, 40, 48, 56
P_SYN_AVG, P_SYN_MAX, P_RND_MAX, P_OVR = 544, 576, 512, 844


def ssh(cmd, timeout=30):
    r = subprocess.run(["ssh", *mi.SSH_OPTS, HOST, cmd], capture_output=True, timeout=timeout)
    return r.stdout


# Schwung writes debug.log (its spi_timing and spi-tally lines, read below) only while this file is there, and never
# trims the log: on since 2026-10-28 it had grown to 43 MB by 10-30. The run makes it and takes it away again; a flag
# the user had set before stays
LOG_FLAG = "/data/UserData/schwung/debug_log_on"


def log_on(sh=None):
    """debug_log_on there for the run: True when this run made it (log_off takes it away)"""
    out = (sh or ssh)('[ -f %s ] && echo kept || { touch %s && echo made; }' % (LOG_FLAG, LOG_FLAG))
    return b"made" in (out or b"")


def log_off(made, sh=None):
    if made:
        (sh or ssh)("rm -f %s" % LOG_FLAG)


def perf_decode(b):
    u64 = lambda o: struct.unpack_from("<Q", b, o)[0]   # noqa: E731
    return {"period": u64(P_PERIOD), "tot_avg": u64(P_TOT_AVG), "tot_max": u64(P_TOT_MAX),
            "pre_avg": u64(P_PRE_AVG), "pre_max": u64(P_PRE_MAX),
            "syn_avg": [u64(P_SYN_AVG + 8 * i) for i in range(4)], "syn_max": [u64(P_SYN_MAX + 8 * i) for i in range(4)],
            "rnd_max": [u64(P_RND_MAX + 8 * i) for i in range(4)],
            "overruns": struct.unpack_from("<I", b, P_OVR)[0],
            "sec_max": [u64(96 + 16 * i + 8) for i in range(24)], "sec_avg": [u64(96 + 16 * i) for i in range(24)],
            "post_max": u64(88)}


SECTIONS = ["MIDI monitor", "Forward MIDI", "Mix audio", "UI requests", "Param requests", "Forward CC",
            "Process MIDI", "JACK stash", "Drain DSP", "JACK wake", "Mix buffer", "TTS", "Display", "Clear LEDs",
            "JACK MIDI out", "UI MIDI out", "Flush LEDs", "Screen reader", "JACK pre", "JACK display", "CPU pin",
            "Post MIDI scan", "Post drain DSP", "Post render"]


def top_sections(rows, thr=150):
    if not rows:
        return {}
    return {SECTIONS[i]: max(p["sec_max"][i] for p in rows) for i in range(24)
            if max(p["sec_max"][i] for p in rows) >= thr}


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
        w.subscribe(s)
        m[s] = (w.info.get(s) or {}).get("synth", "")
    return m


RING_BACKOFF_S = (0.3, 0.6, 1.2, 2.4, 4.8)     # the pauses before each new try after "ring full"


def ring_full(e):
    return isinstance(e, move_ws.SchwungError) and "ring full" in str(e)


def paced(fn, sleep=time.sleep, backoff=RING_BACKOFF_S, log=None):
    """fn() again after each pause in backoff while the manager answers "ring full"; any other error, or the last
    try's ring full, is raised"""
    for k in range(len(backoff) + 1):
        try:
            return fn()
        except move_ws.SchwungError as e:
            if not ring_full(e) or k == len(backoff):
                raise
            if log is not None:
                log.append("ring full: try %d after %.1f s" % (k + 2, backoff[k]))
            sleep(backoff[k])


def set_module(w, s, mod, wait=8.0, settle=0.5, sleep=time.sleep, log=None):
    """slot s to module mod ("" empties it): written, then the slot_info that confirms it awaited (up to wait s), then
    settle s for the manager's re-read; "ring full" paced. True when Schwung confirmed it"""
    def once():
        w.subscribe(s)
        if (w.info.get(s) or {}).get("synth", "") == mod:
            return True
        w.set_param(s, "synth:module", mod)
        try:
            w.until(lambda r: (r.info.get(s) or {}).get("synth", "") == mod, wait)
        except TimeoutError:
            return False
        w.pump(settle)
        return True
    return paced(once, sleep=sleep, log=log)


class Slots:
    """with Slots(url, slots) as ks: the modules of slots (and the parameters of those holding Frodhes) read first,
    put back at the end: on an exception, ctrl-C, SIGTERM (raised as SystemExit) and at interpreter exit (atexit)
    if the block never ended. ks.report: {"verdict", "modules", "params"} once restored. remote / state_read /
    state_restore: injectable (tests)"""

    def __init__(self, url, slots, remote=None, state_read=None, state_restore=None, sleep=time.sleep):
        self.url, self.slots, self.report, self.before, self.state = url, list(slots), None, None, {}
        self.remote = remote or (lambda: move_ws.Remote(url))
        self.state_read = state_read or mh.state_read
        self.state_restore = state_restore or mh.state_restore
        self.sleep = sleep

    def __enter__(self):
        import atexit
        import signal
        with self.remote() as w:
            mods = slot_modules(w)
        self.before = {s: mods.get(s, "") for s in self.slots}
        for s, m in self.before.items():
            if m == "frodhes":
                self.state[s] = self.state_read(self.url, s)
        atexit.register(self.restore)
        self._sig = None
        try:
            self._sig = signal.signal(signal.SIGTERM, self._term)
        except ValueError:                           # (not the main thread)
            pass
        return self

    @staticmethod
    def _term(signum, frame):
        raise SystemExit(128 + signum)

    def restore(self):
        if self.report is not None or self.before is None:
            return self.report
        bad, log, params = [], [], {}
        try:
            with self.remote() as w:
                now = slot_modules(w)
                for s, m in sorted(self.before.items()):
                    if now.get(s, "") != m and not set_module(w, s, m, sleep=self.sleep, log=log):
                        bad.append("slot %d: %r, not %r" % (s + 1, now.get(s, ""), m))
                now = slot_modules(w)
            for s, st in sorted(self.state.items()):
                if now.get(s) != "frodhes":
                    continue
                written, diff = paced(lambda: self.state_restore(self.url, s, st), sleep=self.sleep, log=log)
                params[s + 1] = {"written": len(written), "differ": sorted(diff)}
                if diff:
                    bad.append("slot %d: %d param(s) differ (%s)" % (s + 1, len(diff), ", ".join(sorted(diff)[:6])))
        except Exception as e:                       # (Schwung gone: say so loudly, never pass)
            bad.append("not restored: %s: %s" % (type(e).__name__, e))
        self.report = {"verdict": "PASS" if not bad else "FAIL: " + "; ".join(bad),
                       "modules": {s + 1: m for s, m in self.before.items()}, "params": params, "paced": log}
        print("slots: " + self.report["verdict"], flush=True)
        return self.report

    def __exit__(self, *exc):
        import atexit
        import signal
        try:
            self.restore()
        finally:
            atexit.unregister(self.restore)
            if self._sig is not None:
                try:
                    signal.signal(signal.SIGTERM, self._sig)
                except ValueError:
                    pass
        return False


def paced_play(remote, up, bar, sleep=time.sleep, backoff=RING_BACKOFF_S, log=None):
    """remote.play with the uploads one at a time (the manager's set ring), paced: a "ring full" during an upload
    uploads the take again whole after a pause ("begin" starts it over); then every "go" together (bar: a
    threading.Barrier; an upload that fails for good aborts it, so no slot waits on it)"""
    orig_set = remote.set_param

    def play(slot, take, dur_ms, events, lead_ms=100, pace=0.025):
        with up:
            def no_go(sl, key, value):
                if key.endswith(":dev_play") and str(value).startswith("go"):
                    return
                orig_set(sl, key, value)

            def upload():
                remote.set_param = no_go
                try:
                    move_ws.Remote.play(remote, slot, take, dur_ms, events, lead_ms, pace)
                finally:
                    remote.set_param = orig_set
                remote.pump(0.5)
            try:
                paced(upload, sleep=sleep, backoff=backoff, log=log)
            except Exception:
                bar.abort()
                raise
        bar.wait()
        paced(lambda: (orig_set(slot, "synth:dev_play", "go %d" % int(lead_ms)), remote.pump(0.2)),
              sleep=sleep, backoff=backoff, log=log)
    remote.play = play


JS = {}
FREE = [0, 1, 2, 3]                              # the slots a run may change (--keep-others: those empty or Frodhes)
SLOTS = [0, 1, 2, 3]                             # instance i plays in slot SLOTS[i]
USB = False
LOW = True                                       # (LEVEL -12 dB: four slots at unity clip the Move's mix)


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
        JS[take] = dict(json.load(open(js)), inst_dir=d)
        return wav, JS[take]
    return fetch


def run_n(n, secs, outdir, seed=1):
    res = {"n": n}
    with move_ws.Remote(URL) as w:
        t0 = time.time()
        rss0 = rss_kb()
        paced_log, unconfirmed = [], []
        for s in FREE:
            on = s in SLOTS[:n]
            if not set_module(w, s, "frodhes" if on else "", 12.0 if on else 4.0, log=paced_log):
                unconfirmed.append(s + 1)
        if unconfirmed:
            raise RuntimeError("Schwung never confirmed the module of slot(s) %s" % unconfirmed)
        res["load_s"] = round(time.time() - t0, 1)
        res["paced"] = paced_log
        res["rss_kb"] = rss_kb()
        res["rss0_kb"] = rss0
        res["inst"] = ssh("ls -d %s/*/ 2>/dev/null; cat %s/*/load.txt 2>/dev/null" % (INST, INST)).decode()
    ssh("rm -f %s/*/cpu-ring.log" % INST)
    rigs = [mh.TapRig(URL, fetch_any(os.path.join(outdir, "n%d" % n)), slot=SLOTS[s]) for s in range(n)]
    for s, r in enumerate(rigs):
        r.stamp = "%s%d" % (r.stamp, s)              # (one take id an instance)
    ccs = hc.engine_ccs("rhodes", dict(hc.HEAVY, LEVEL=0) if LOW else hc.HEAVY)
    takes, evs = [None] * n, [None] * n
    for s, r in enumerate(rigs):
        r._send("cc", 120, 0)
        for c, v in ccs:
            r._send("cc", c, v)
        paced(lambda: (r._set("synth:dev_cpu_log", 1), r.remote.pump(1.5)))   # (the manager's set ring drains: 32
        #                                                                        entries, one a frame)
        ev, dur = hc.worst_events(16, secs, np.random.default_rng(seed + s))
        evs[s] = (sorted(ev, key=lambda e: e[0]), dur)
    perf = Perf()
    perf.start()
    ssh("touch /data/UserData/schwung/spi_tally_on")
    made_log = log_on()
    time.sleep(12)                                  # (the idle baseline: loaded, nothing playing)
    base = list(perf.rows)
    idle = [p for t, p in base[2:]]
    if idle:
        res["idle"] = {"pre_max": max(p["pre_max"] for p in idle), "tot_max": max(p["tot_max"] for p in idle),
                       "overruns_per_s": round((idle[-1]["overruns"] - idle[0]["overruns"]) / max(1, len(idle) - 1), 1),
                       "syn_max": [max(p["syn_max"][i] for p in idle) for i in range(4)],
                       "sections": top_sections(idle),
                       "series_pre_max": [p["pre_max"] for p in idle]}
    log0 = int(ssh("wc -c < /data/UserData/schwung/debug.log").decode().strip() or 0)

    up = threading.Lock()
    bar = threading.Barrier(n, timeout=120)          # (a slot whose upload failed never hangs the others)

    for r in rigs:
        paced_play(r.remote, up, bar, log=res.setdefault("paced", []))

    def play(s):
        try:
            takes[s] = rigs[s].rec(evs[s][1], evs[s][0])
        except Exception as e:     # noqa: BLE001
            takes[s] = {"error": "%s: %s" % (type(e).__name__, e)}
            print("slot %d: %s" % (SLOTS[s] + 1, takes[s]["error"]), flush=True)
            bar.abort()
    usb = {}

    def usb_rec(secs):
        import sounddevice as sd
        dev = [i for i, d in enumerate(sd.query_devices()) if "Move" in d["name"] and d["max_input_channels"] > 0]
        if dev:
            usb["x"] = sd.rec(int(secs * 44100), samplerate=44100, channels=2, dtype="int16", device=dev[0], blocking=True)
    ut = threading.Thread(target=usb_rec, args=(max(e[1] for e in evs) + 8.0,))
    if USB:
        ut.start()
    th = [threading.Thread(target=play, args=(s,)) for s in range(n)]
    tp0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    perf.end()
    if USB:
        ut.join()
        if "x" in usb:
            import wave
            x = usb["x"]
            wf = wave.open(os.path.join(outdir, "usb-n%d.wav" % n), "wb")
            wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(44100); wf.writeframes(x.tobytes()); wf.close()
            xf = x.astype(float)
            g = mh.glitches(xf, 44100, [], ccs=ccs)
            res["usb"] = {"peak_dbfs": round(20 * np.log10(np.abs(xf).max() / 32767 + 1e-12), 1),
                          "gated": len([e for e in g if e[1] in hc.GATED]), "events": [list(e) for e in g[:20]]}
    log = ssh("tail -c +%d /data/UserData/schwung/debug.log | grep -E 'spi-tally|spi_timing\\] Frame'" % (log0 + 1)).decode()
    res["tally"] = [ln for ln in log.splitlines() if "spi-tally" in ln][-40:]
    res["late_lines"] = sum(1 for ln in log.splitlines() if "LATE" in ln)
    res["frame_lines"] = [ln.split("Frame(us): ")[-1][:80] for ln in log.splitlines() if "Frame(us)" in ln][-12:]
    ssh("rm -f /data/UserData/schwung/spi_tally_on")
    log_off(made_log)
    for r in rigs:
        try:
            paced(lambda: r._set("synth:dev_cpu_log", 0))
        except move_ws.SchwungError as e:            # (the rings are read all the same)
            print("slot %d: dev_cpu_log 0: %s" % (r.slot + 1, e), flush=True)
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
                       "overruns": during[-1]["overruns"] - (base[-1][1]["overruns"] if base else during[0]["overruns"]),
                       "overruns_per_s": round((during[-1]["overruns"] - during[0]["overruns"]) / max(1, len(during) - 1), 1),
                       "sections": top_sections(during),
                       "series_pre_max": [p["pre_max"] for p in during],
                       "series_syn_sum_max": [sum(p["syn_max"]) for p in during],
                       "series_post_render_max": [p["sec_max"][23] for p in during],
                       "post_render_avg": max(p["sec_avg"][23] for p in during),
                       "series_tot_max": [p["tot_max"] for p in during]}
    res["takes"] = []
    for s in range(n):
        tk = takes[s]
        if not tk or "error" in tk:
            res["takes"].append({"slot": SLOTS[s] + 1, "tap": {k: v for k, v in JS.get("d%s-%03d" % (rigs[s].stamp, rigs[s].n), {}).items()
                                     if k in ("played", "events", "dropped", "late", "bad", "aborted", "inst_dir")}, "error": (tk or {}).get("error", "none")})
            continue
        g = mh.glitches(tk["x"], tk["sr"], evs[s][0], ccs=ccs)
        gated = [e for e in g if e[1] in hc.GATED]
        res["takes"].append({"slot": SLOTS[s] + 1, "tap": {k: v for k, v in JS.get("d%s-%03d" % (rigs[s].stamp, rigs[s].n), {}).items()
                                     if k in ("played", "events", "dropped", "late", "bad", "aborted", "inst_dir")}, "secs": round(evs[s][1], 1), "gated": len(gated), "events": len(g),
                             "first": [list(e) for e in gated[:5]], "xruns": tk["xruns"],
                             "peak_dbfs": round(20 * np.log10(np.abs(tk["x"]).max() / 32767 + 1e-12), 1)})
    for r in rigs:
        r.remote.close()
    return res


# The pass bars (the user, 2026-11-02: 16 voices stay, three Frodhes at the worst case guaranteed): one instance within
# U12's working limit (tools/move_hil.py U12_LIMIT_US); two or three instances each within a third of the 128-frame
# block (2902 us / 3: ~967 us); four is reported only. REF_1_US: one instance's worst on 0.9.9 (573 us), the reference
# a new engine is compared with (over it by more than REF_TOL: a regression to explain, not a failure by itself)
BAR_US = {1: mh.U12_LIMIT_US, 2: 967, 3: 967}
REF_1_US, REF_TOL = 573, 0.15


def verdict(r, n):
    """(PASS / FAIL / INFO, text) for one run_n result: every instance's ring worst within BAR_US[n], no ring gaps,
    no Schwung overrun during the loads, no gated click on any tap (or on the USB-C audio)"""
    rings = r.get("rings") or {}
    worst = max((s.get("worst", 0) for s in rings.values()), default=None)
    bad = []
    if worst is None or len(rings) < n:
        bad.append("rings: %d of %d instances read" % (len(rings), n))
    elif n in BAR_US and worst > BAR_US[n]:
        bad.append("worst %d us > %d us" % (worst, BAR_US[n]))
    if any(s.get("missing") for s in rings.values()):
        bad.append("ring gaps")
    ovr = (r.get("perf") or {}).get("overruns")
    if ovr:
        bad.append("%d Schwung overruns" % ovr)
    clicks = sum(t.get("gated", 0) for t in r.get("takes", [])) + (r.get("usb") or {}).get("gated", 0)
    if clicks:
        bad.append("%d gated clicks" % clicks)
    if any("error" in t for t in r.get("takes", [])):
        bad.append("a take failed")
    txt = "n=%d: worst %s us (bar %s us)" % (n, worst, BAR_US.get(n, "none"))
    if n == 1 and worst:
        txt += ", %+.0f %% re 0.9.9's %d us%s" % (100.0 * (worst / REF_1_US - 1), REF_1_US,
                                                  " (over +%d %%: explain)" % (100 * REF_TOL)
                                                  if worst > REF_1_US * (1 + REF_TOL) else "")
    if n not in BAR_US:
        return "INFO", txt + ("; " + "; ".join(bad) if bad else "")
    return ("FAIL" if bad else "PASS"), txt + ("; " + "; ".join(bad) if bad else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--secs", type=float, default=20.0)
    ap.add_argument("--usb", action="store_true", help="also record the Move's USB-C audio (Main Out) and check it")
    ap.add_argument("--keep-others", action="store_true", help="leave slots with other modules alone; use the free ones")
    ap.add_argument("--take-slots", action="store_true", help="run even if a slot holds another module (it is emptied)")
    ap.add_argument("--out", default=os.path.join(mh.ROOT if hasattr(mh, "ROOT") else ".", "build", "move", "hil", "multi"))
    a = ap.parse_args()
    global USB
    USB = a.usb
    out = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    allr = []
    with mi.move_lock("move_multi_cpu"):
        with move_ws.Remote(URL) as w:
            other = {s + 1: m for s, m in slot_modules(w).items() if m not in ("", "frodhes")}
        global FREE, SLOTS
        if other and a.keep_others:
            FREE = [s for s in range(4) if s + 1 not in other]
            SLOTS = FREE[:]
            if max(a.n) > len(SLOTS):
                sys.exit("move_multi_cpu: --n %d needs %d free slots; %d are free" % (max(a.n), max(a.n), len(SLOTS)))
        elif other and not a.take_slots:
            sys.exit("move_multi_cpu: slots %s hold %s; the run would empty them. Load a test set, or --take-slots"
                     % (", ".join(map(str, other)), ", ".join(sorted(set(other.values())))))
        with Slots(URL, FREE) as ks:                 # (every slot the run may change: as it was, also on a crash)
            for n in a.n:
                try:
                    r = run_n(n, a.secs, out)
                except Exception as e:               # (the report keeps what ran; the slots are restored below)
                    r = {"n": n, "error": "%s: %s" % (type(e).__name__, e)}
                    print("n %d: %s" % (n, r["error"]), flush=True)
                print(json.dumps(r, indent=1, default=str), flush=True)
                allr.append(r)
                json.dump(allr, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
        allr.append({"slots_restored": ks.report})
        json.dump(allr, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
    print("report:", os.path.join(out, "report.json"))
    vs = [verdict(r, n) for r, n in zip(allr, a.n)]   # (an "error" run has no rings: FAIL)
    for v, txt in vs:
        print("%-4s %s" % (v, txt))
    if ks.report["verdict"] != "PASS":
        print("FAIL the slots not restored: %s" % json.dumps(ks.report, default=str))
        return 1
    return 1 if any(v == "FAIL" for v, _ in vs) else 0


if __name__ == "__main__":
    sys.exit(main())
