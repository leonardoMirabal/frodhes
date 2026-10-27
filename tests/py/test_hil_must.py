# SPDX-License-Identifier: GPL-3.0-only
"""hil_scan --tier must (tools/hil_must.py), T1 of the release test plan, on the host (no FM-1): the plan fits its
20 minutes, the stimuli are what the scenarios say (9 then 12 notes, the pedal re-pressed every 20 s, every note
released, every control swept, F01..F16 and every TYPE), the counters' gate fails a 0.9.4-like firmware (no USB
counters), an underrun, a missed packet, an overrun, a ring low point under one packet, a whole ISR over 4500 us and a
shed voice, and the repeated-frame detector finds injected repeats and nothing on clean audio."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_must as m  # noqa: E402
import hil_scan as hs  # noqa: E402


def args(*extra):
    return m.parse_args(["--tier", "must", *extra])


def test_default_plan_fits_20_min():
    tot = sum(s for _, _, s in m.plan(args("--fwsc", "x.fwsc")))
    assert tot <= m.BUDGET_S, tot / 60
    assert {sc for sc, _, _ in m.plan(args())} == set(m.SCENARIOS)
    # every engine at both rates in S1
    cases = [c for sc, c, _ in m.plan(args()) if sc == "S1"]
    for e in m.ENGINES:
        for r in ("48", "44.1"):
            assert any(c.startswith(f"{e} {r} kHz worst") for c in cases)


def notes_held(ev):
    held, peak = set(), 0
    for t, k, a, b in sorted(ev, key=lambda e: (e[0], e[1] != "off")):
        if k == "on":
            held.add(a)
        elif k == "off":
            held.discard(a)
        peak = max(peak, len(held))
    return held, peak


def test_stim_worst():
    ev = m.stim_worst(60.0)
    held, peak = notes_held(ev)
    assert not held and peak == m.NV
    ped = [t for t, k, a, b in ev if k == "cc" and a == 64 and b == 0]
    assert len(ped) >= 3                              # re-pressed every 20 s, up at the end
    ons = sorted({t for t, k, *_ in ev if k == "on"})
    gaps = np.diff(ons)
    assert gaps.min() >= 0.15 - 1e-9 and gaps.max() <= 0.4 + 1e-9
    assert {b for t, k, a, b in ev if k == "on"} == {100, 127}
    assert ev[-1][1] == "cc" and ev[-1][2] == 64 and ev[-1][3] == 0


def test_stim_recipe_reaches_9_then_12():
    ev = m.stim_recipe(20.0)
    held, peak = notes_held(ev)
    assert not held and peak == 12
    ons = sorted(t for t, k, *_ in ev if k == "on")[:12]
    assert ons[9] - ons[8] > 1.0                      # held at 9 before the last three


def test_stim_switch_and_sweep():
    ev, sw, rel = m.stim_switch()
    pcs = [a for t, k, a, b in ev if k == "pc"]
    assert pcs[:16] == list(range(16)) and pcs[-1] == 0
    types = {(a, b) for t, k, a, b in ev if k == "cc" and a in (79, 80)}
    assert types == set(m.TYPE_SEQ)
    assert len(sw) == 16 + 2 * len(m.TYPE_SEQ) + 1 and max(sw) < rel
    assert not notes_held(ev)[0]
    ev, ccs = m.stim_sweep()
    want = [c for c, _ in hs.param_defaults() if c not in m.SWEEP_SKIP]
    assert ccs == want and 80 not in ccs
    for c, de in hs.param_defaults():
        if c in ccs:
            vals = [b for t, k, a, b in ev if k == "cc" and a == c]
            assert 127 in vals and vals[-1] == de      # swept to 127, back to the default
    assert not notes_held(ev)[0]


def reply(isr=3000, eng=2800, shed=0, usb=None):
    msgs = [(116, (eng // 4) >> 7), (117, (eng // 4) & 127), (118, 10), (115, shed),
            (108, (isr // 4) >> 7), (107, (isr // 4) & 127), (110, 0), (109, 40)]
    ids = {v: k for k, v in m.USB_ITEMS.items()}
    for k, v in (usb or {}).items():
        msgs += [(100, ids[k]), (101, v >> 7), (102, v & 127)]
    return msgs


GOOD = dict(underruns=0, missed=0, overruns=0, adj_up=1, adj_down=0, fill_lo=120, fill_hi=200, fill_low=90,
            per_min=5790, per_max=5820, stalls=0, state=2 | 4 | 8 | 1)


def acc_of(*replies):
    a = m.Acc()
    for r in replies:
        a.add(m.parse_reply(r))
    return a


def fails(rows):
    return [r[0] for r in rows if r[3] == "FAIL"]


def test_counters_gate():
    good = acc_of(*[reply(usb=GOOD) for _ in range(4)])
    assert fails(m.gate_counters(good, 48000)) == []
    # 0.9.4: the profiler answers, the USB items and the whole ISR do not
    old = m.Acc()
    for _ in range(3):
        old.add(m.parse_reply([(116, 8), (117, 0), (118, 3), (115, 0)]))
    f = fails(m.gate_counters(old, 48000))
    assert "USB counters answer" in f and "whole ISR max (us)" in f
    for k, v, name in (("underruns", 3, "USB underruns"), ("missed", 1, "USB missed"), ("overruns", 1, "USB overruns"),
                       ("fill_low", 40, "USB ring low point (frames)")):
        a = acc_of(reply(usb=GOOD), reply(usb=dict(GOOD, **{k: v})))
        assert fails(m.gate_counters(a, 48000)) == [name], k
    assert fails(m.gate_counters(acc_of(reply(usb=GOOD), reply(isr=4600, usb=GOOD)), 48000)) == ["whole ISR max (us)"]
    # 0.9.7: item 21, the EP4 polls whose SIE access timed out (summed; absent in older firmware: no row)
    to = acc_of(reply(usb=dict(GOOD, sie_to=0)), reply(usb=dict(GOOD, sie_to=2)), reply(usb=dict(GOOD, sie_to=1)))
    assert to.summary()["usb"]["sie_to"] == 3
    assert fails(m.gate_counters(to, 48000)) == ["USB SIE timeouts (EP4 polls)"]
    assert "USB SIE timeouts (EP4 polls)" not in [r[0] for r in m.gate_counters(good, 48000)]
    assert fails(m.gate_counters(acc_of(reply(usb=dict(GOOD, sie_to=0))), 48000)) == []
    assert fails(m.gate_counters(acc_of(reply(usb=GOOD, shed=5), reply(usb=GOOD, shed=6)), 48000)) == ["voices shed"]
    # the shed counter wraps mod 128
    assert acc_of(reply(usb=GOOD, shed=127), reply(usb=GOOD, shed=1)).shed == 2
    # the stream state: 44.1 asked, 48 flowing; S2 (no stream) with one flowing
    assert fails(m.gate_counters(good, 44100)) == ["USB stream state"]
    assert fails(m.gate_counters(good, None)) == ["USB stream state"]
    off = acc_of(reply(usb=dict(GOOD, state=0, fill_low=16383)))
    assert fails(m.gate_counters(off, None)) == []
    assert fails(m.gate_counters(m.Acc(), 48000)) == ["counters answer"]
    assert m.packet(44100) == 45 and m.packet(48000) == 48


def music(n=48000 * 3, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 48000
    x = sum(a * np.sin(2 * np.pi * f * t + p) for a, f, p in
            zip(rng.uniform(1000, 4000, 8), rng.uniform(80, 3000, 8), rng.uniform(0, 6, 8)))
    x = x * np.exp(-t / 2)
    return np.round(np.stack([x, 0.8 * x], 1)).astype(np.int64)


def usb_count(x, sr=48000, ev=()):
    r = {row[0]: row for row in m.usb_rows("USB", x, sr, list(ev), 0.0)}
    return r[f"USB: {m.USB_ROW}"]


def test_usb_audio_judged_by_hil_crackle(monkeypatch):
    """T1 judges USB audio with ONE definition, hil_crackle's (repeat + stale with the seam rule, the recording's own
    clock): no detector of hil_must's own, audio_rows (S3, S4, S1's fallback) goes through hil_crackle.usb_judge"""
    assert not hasattr(m, "repeats")
    calls = []
    real = m.hc.usb_judge

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(m.hc, "usb_judge", spy)
    ev, _ = m.stim_sweep()
    rows = m.audio_rows("USB", music().astype(float) / 32768, 48000, ev, 0.0)
    assert len(calls) == 1 and calls[0][2] == ev
    assert rows[0][0] == f"USB: {m.USB_ROW}" and rows[0][3] == "ok"
    assert not any(r[0].startswith("USB: repeated") for r in m.audio_rows("jack", music()[:, 0], 48000, ev, 0.0,
                                                                          repeats_gate=False))
    assert len(calls) == 1                                   # (the jack: no USB detector)


def test_repeats_detector():
    x = music()
    assert usb_count(x)[1:] == (0, "0", "ok")
    y = x.copy()
    at = np.linspace(9000, len(x) - 9000, 10).astype(int)    # (stale's look-back and repeat's context clear)
    for i in at:
        j = i + int(np.argmax(np.abs(np.diff(y[i:i + 400, 0]))))   # on a slope
        y[j + 1:] = y[j:-1].copy()                               # frame j sent twice: the stream slips one frame
    assert usb_count(y)[1] == 10 and usb_count(y)[3] == "FAIL"
    z = np.zeros((48000, 2), np.int64)                           # silence is not a repeat
    assert usb_count(z)[1] == 0


def test_s4_vibes_restrike_is_no_repeat():
    """the device's S4 VIBES 'exact repeated frames on a slope' (1-2 on the FM-1, every counter clean): the host
    render of the same panel and sweep has 2 too (2026-10-24), both in the chord's re-strikes: 2.048 s (48 ms into
    the strike at 2.0: -5848, -5843, -5843, -5795 on both channels, steps +5 / +48) and 23.997 s (its first frames:
    795, 802, 802, 864, steps +7 / +62), the attack's own turn. The old check (2 equal frames, > 40 LSB across them)
    counted them; hil_crackle's repeat does not (steps that unlike are no copy's slope)"""
    import hil_crackle as hc
    params = [p for p in hs.param_defaults() if p[0] not in m.SWEEP_SKIP][:5]     # the sweep's first 2.65 s
    ev, _ = m.stim_sweep(params)
    ccs = [(c, v) for c, v in m.panel_msgs("vibes") if c != 120]
    u, _ = hc.host_take("s4-vibes-restrike", ccs, ev, m.dur_of(ev, 0.5), tag="must")
    q = np.round(u).astype(np.int64)
    i = np.arange(2, len(q) - 1)                                 # the old criterion, here only as the witness
    same = np.all(q[i] == q[i - 1], axis=1) & (np.abs(q[i]).max(axis=1) > 300)
    b, a = q[i - 1] - q[i - 2], q[i + 1] - q[i]
    old = i[same & np.any((np.abs(q[i + 1] - q[i - 1]) > 40) & ((np.sign(b) == np.sign(a)) | (b == 0)), axis=1)]
    assert len(old) >= 1 and any(abs(k / hc.FS_HOST - 2.0) < 0.1 for k in old), old / hc.FS_HOST
    assert usb_count(q, hc.FS_HOST, ev)[1:] == (0, "0", "ok")


def test_0_9_4_repeats_still_fail():
    """the 0.9.4 FM-1 snippets hil_crackle saved (build/crkfm1/rel48/*-repeat.wav, 48 kHz, USB left; local files, not
    in git: skipped where missing) fail through T1's USB rows too"""
    import glob
    from scipy.io import wavfile
    cand = [os.path.join(ROOT, "build", "crkfm1", "rel48"),
            os.path.join(ROOT, "..", "..", "..", "build", "crkfm1", "rel48")]     # (from a worktree)
    files = next((g for g in (sorted(glob.glob(os.path.join(c, "*-repeat.wav"))) for c in cand) if g), [])
    if not files:
        pytest.skip("build/crkfm1/rel48/*-repeat.wav not here")
    for p in files:
        sr, x = wavfile.read(p)
        q = np.stack([x, x], 1).astype(np.int64) if x.ndim == 1 else x.astype(np.int64)
        r = usb_count(q, sr)
        assert r[3] == "FAIL" and r[1] >= 1, os.path.basename(p)


def test_audio_rows_guards():
    x = np.zeros((48000, 2))
    x[24000, :] = 0.5                                 # a click at 0.5 s
    ev = [(0.49, "on", 60, 100)]
    rows = {r[0]: r for r in m.audio_rows("USB", x, 48000, ev, 0.0)}
    assert rows["USB: clicks outside the guards"][3] == "ok"       # under the note-on's guard
    rows = {r[0]: r for r in m.audio_rows("USB", x, 48000, [(0.2, "on", 60, 100)], 0.0)}
    assert rows["USB: clicks outside the guards"][3] == "FAIL"
    rows = {r[0]: r for r in m.audio_rows("USB", x, 48000, [], 0.0, switch_times=[0.4])}
    assert rows["USB: clicks outside the guards"][3] == "ok"
    assert rows["USB: clicks inside the switches' 300 ms"][1] >= 1
    assert m.audio_rows("jack", None, 48000, [], 0.0)[0][3] == "SKIP"


def test_cpu_rows():
    ok = ("2-note ...\n      ISR tail after app_audio  300 us, whole ISR  4200 us\nvoices shed 0\n"
          "worst 4100 us with 9 voices (71 %)\n")
    assert fails(m.cpu_rows(0, ok)) == []
    bad = ok.replace("whole ISR  4200", "whole ISR  4620") + "FAIL: USB underruns / missed packets / overruns in: x\n"
    assert set(fails(m.cpu_rows(1, bad))) == {"whole ISR max (us)", "hil_cpu USB counters", "hil_cpu exit"}


def test_report_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "OUT", str(tmp_path))
    r = m.Report()
    r.add("S1", "a", [("x", 0, "0", "ok")], acc_of(reply(usb=GOOD)).summary())
    assert r.verdict() == "PASS"
    r.add("S5", "update", [("update over USB", "no --fwsc", "", "SKIP")])
    assert r.verdict() == "INCOMPLETE"
    r.add("S2", "b", [("y", 1, "0", "FAIL")])
    assert r.write(dict(firmware="test")) == "FAIL"
    md = open(tmp_path / "report.md").read()
    assert "**FAIL**" in md and "The counters per case" in md


def test_cli_dry_run():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "hil_scan.py"), "--tier", "must", "--dry-run",
                        "--fwsc", "x.fwsc"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "total" in p.stdout and "S6" in p.stdout
    tot = float(p.stdout.split("total ")[1].split(" min")[0])
    assert tot <= 20.0


def test_panel_msgs():
    msgs = m.panel_msgs("vibes", m.HEAVY, m.HEAVY_CC)
    ccs = [c for c, _ in msgs]
    last = {c: i for i, c in enumerate(ccs)}
    assert ccs[0] == 120 and last[85] < last[12]                     # CC 85 before the phaser's switch
    assert msgs[-1] == (47, 127) and (79, hs.type_cc(4)) in msgs
    assert m.panel_msgs("marimba")[-1] == (79, 127)
    assert m.panel_msgs("rhodes")[-1] == (80, hs.type_cc(0))


def test_repeats_finds_the_users_cracks():
    """the user's own DAW take on 0.9.4 (48 kHz / 128, pedal + 9 voices; workstream A counted 153 repeated frames
    on slopes, one every ~29 ms): the detector must find them (a local file, not in git: skipped where missing)"""
    import pytest
    cand = [os.path.join(ROOT, "build", "crackle-user", "user1.wav"),
            os.path.join(ROOT, "..", "..", "..", "build", "crackle-user", "user1.wav")]   # (from a worktree)
    path = next((p for p in cand if os.path.exists(p)), None)
    if not path:
        pytest.skip("build/crackle-user/user1.wav not here")
    import warnings
    from scipy.io import wavfile
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sr, x = wavfile.read(path)
    q = x.astype(np.int64) >> 16 if x.dtype == np.int32 else x.astype(np.int64)
    ev, _ = m.hc.usb_judge(q, sr, [])                 # T1's USB detector (hil_crackle's): 195 here, 2026-10-24
    assert usb_count(q, sr)[3] == "FAIL"
    r = np.array([round(t * sr) for t, _, _ in ev])
    assert 120 <= len(r) <= 220, len(r)
    gap_ms = np.median(np.diff(r)) / sr * 1000
    assert 20 <= gap_ms <= 60, gap_ms                 # ~one per 29 ms beat (some beats without one)


# ---- the reconnection after an install (fakes: no FM-1, no audio, no MIDI) -------------------------------------------
# The FM-1 re-enumerates on USB after every install: a new Core Audio id, the device indexes shifted. sounddevice keeps
# PortAudio's list from its first init, so the old index opens a gone device ('Internal PortAudio error' -9986, AUHAL
# -10851): the 2026-10-24 T1 run left the FM-1 on 0.9.4 that way. The fakes model exactly that.

class FakePAError(Exception):
    pass


class FakeStream:
    def __init__(self, world, device, samplerate, callback=None, channels=2, **kw):
        world.check(device, samplerate)
        self.time, self.closed, self.cb, self.sr, self.ch = 0.0, False, callback, int(samplerate), channels
        self.signal = getattr(world, "signal", None)

    def start(self):                                  # the whole take at once: a tone, so the S5 measures run
        class Ti:
            inputBufferAdcTime = 0.0
            currentTime = 0.0
        t = np.arange(4 * self.sr) / self.sr
        x = np.repeat((0.1 * np.sin(2 * np.pi * 440 * t))[:, None], self.ch, axis=1).astype(np.float32)
        if self.signal is not None and self.ch == 2:
            x = self.signal(self.sr).astype(np.float32)
        self.cb(x, len(x), Ti, 0)

    def stop(self):
        pass

    def close(self):
        self.closed = True


class World:
    """the computer's audio + MIDI as the FM-1 changes under it; sd(): a sounddevice look-alike over it"""

    def __init__(self, rates=(48000, 44100), name="Frodhes", midi="Frodhes"):
        self.gen, self.others, self.fm1 = 0, ["MacBook Microphone"], dict(name=name, rates=set(rates))
        self.gone_inits = 0           # PortAudio inits that still find no FM-1 (re-enumerating)
        self.midi, self.midi_down = midi, 0
        self.fail_opens = 0           # opens that fail anyway (Core Audio not ready yet)
        self.snap, self.inits = None, 0
        self.log = []                 # every MIDI message any FDev sent (the devices are made again after installs)

    def devices(self):
        d = [dict(name=n, max_input_channels=2, default_samplerate=48000) for n in self.others]
        if self.gone_inits <= 0:
            d.append(dict(name=f"{self.fm1['name']} Audio", max_input_channels=2, default_samplerate=44100))
        return d

    def reenumerate(self, gone=2, rates=None, name=None, midi_down=2):
        """an install: the FM-1 off the bus for a while, back at a new index (another device enumerated meanwhile)"""
        self.gen += 1
        self.others.insert(0, f"Other {self.gen}")
        self.gone_inits, self.midi_down = gone, midi_down
        if rates is not None:
            self.fm1["rates"] = set(rates)
        if name is not None:
            self.fm1["name"] = self.midi = name

    def init(self):
        self.inits += 1
        self.gone_inits -= 1
        self.snap = (self.gen, self.devices())

    def check(self, device, samplerate):
        gen, devs = self.snap
        if gen != self.gen:
            raise FakePAError("Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
        if self.fail_opens > 0:
            self.fail_opens -= 1
            raise FakePAError("Internal PortAudio error")
        if "Audio" in devs[device]["name"] and int(samplerate) not in self.fm1["rates"]:
            raise FakePAError("Invalid sample rate")

    def sd(self):
        w = self

        class SD:
            PortAudioError = FakePAError

            @staticmethod
            def CoreAudioSettings(**kw):
                return kw

            @staticmethod
            def _terminate():
                pass

            @staticmethod
            def _initialize():
                w.init()

            @staticmethod
            def query_devices(i=None):
                return w.snap[1] if i is None else w.snap[1][i]

            @staticmethod
            def check_input_settings(device=None, samplerate=None, **kw):
                w.check(device, samplerate)

            @staticmethod
            def InputStream(device=None, samplerate=None, **kw):
                return FakeStream(w, device, samplerate, **kw)
        w.init()
        return SD

    def dev_factory(self):
        w = self

        class FDev:
            def __init__(self):
                if w.midi_down > 0:
                    w.midi_down -= 1
                    raise m.NoDevice("no FM-1 MIDI port")
                if not any(k in w.midi.lower() for k in hs.PORT_KEYS):
                    raise m.NoDevice("no FM-1 MIDI port")
                self.sent = []

            def close(self):
                pass

            def send(self, kind, a, b=0):
                self.sent.append((kind, a, b))
                w.log.append((kind, a, b))

            def cc(self, c, v):
                self.send("cc", c, v)

            def panel(self, msgs):
                for c, v in msgs:
                    self.cc(c, v)

            def query(self, wait=0.25):
                return []
        return FDev


class _T:
    """time without the waits (the loops' deadlines still count: monotonic advances by each sleep)"""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 1e-3)

    def time(self):
        return self.t

    @staticmethod
    def strftime(f):
        import time
        return time.strftime(f)


def must(monkeypatch, tmp_path, world, *extra, installer=None):
    monkeypatch.setattr(m, "time", _T())
    monkeypatch.setattr(m, "OUT", str(tmp_path))
    a = args("--jack-dev", "none", *extra)
    return m.Must(a, sd=world.sd(), dev_factory=world.dev_factory(), installer=installer,
                  info=lambda: "fake FM-1")


def usb_index(world):
    return [i for i, d in enumerate(world.snap[1]) if "Audio" in d["name"]][0]


def test_reopen_after_reenumeration(monkeypatch, tmp_path):
    w = World()
    mu = must(monkeypatch, tmp_path, w)
    assert mu.usb == usb_index(w) == 1
    r = mu.open_usb(48000, 0.1)
    assert r.sr == 48000
    r.stop()
    w.reenumerate(gone=3)
    # the stale index fails (as on the FM-1); the retries restart PortAudio, wait for the device, find it by name
    r = mu.open_usb(48000, 0.1)
    assert mu.usb == usb_index(w) == 2 and r.sr == 48000
    r.stop()
    # an open that fails while Core Audio settles: retried with backoff
    w.fail_opens = 2
    assert mu.open_usb(44100, 0.1).sr == 44100


def test_refresh_waits_and_gives_up(monkeypatch, tmp_path):
    w = World()
    mu = must(monkeypatch, tmp_path, w)
    w.reenumerate(gone=4)
    mu.refresh()
    assert mu.usb == usb_index(w)
    w.reenumerate(gone=10 ** 6)                       # never back
    with pytest.raises(m.NoDevice):
        mu.refresh(wait=5.0)


def test_refresh_keeps_open_streams(monkeypatch, tmp_path):
    w = World()
    mu = must(monkeypatch, tmp_path, w)
    r = mu.open_usb(48000, 0.1)
    n = w.inits
    mu.refresh()
    assert w.inits == n                               # a stream of ours open: PortAudio is not restarted under it
    r.stop()
    mu.refresh()
    assert w.inits == n + 1


def test_rate_not_offered(monkeypatch, tmp_path):
    w = World(rates=(44100,))
    mu = must(monkeypatch, tmp_path, w)
    with pytest.raises(RuntimeError, match="offers none of 48 kHz"):
        mu.open_usb(48000, 0.1)
    assert mu.open_usb(48000, 0.1, any_rate=True).sr == 44100


def test_reconnect_after_install_felucca_name(monkeypatch, tmp_path):
    """an install over a firmware named Felucca: the MIDI port and audio come back under either name"""
    w = World(name="Felucca", midi="Felucca")
    done = []

    def inst(pkg):
        done.append(pkg)
        w.reenumerate(gone=3, midi_down=3, name="Frodhes")
        return 0, "ok"
    mu = must(monkeypatch, tmp_path, w, "--fwsc", "new.fwsc", installer=inst)
    mu.install("new.fwsc")
    assert done == ["new.fwsc"] and not mu.not_new and mu.usb == usb_index(w)
    assert mu.open_usb(48000, 0.1).sr == 48000


def s5_run(monkeypatch, tmp_path, codes):
    """S5 with --prev (44.1 kHz only), then S2 (stubbed: records it ran); codes: the installs' exit codes in order"""
    w = World()
    done, codes = [], list(codes)

    def inst(pkg):
        done.append(os.path.basename(pkg))
        c = codes.pop(0)
        if c == 0:
            w.reenumerate(gone=2, rates=(44100, 48000) if "new" in pkg else (44100,))
        return c, "out"
    mu = must(monkeypatch, tmp_path, w, "--scenarios", "S5,S2", "--fwsc", "new.fwsc", "--prev", "old.fwsc",
              installer=inst)
    ran = []
    mu.s2 = lambda: ran.append("S2")
    v = mu.run()
    return v, done, ran, mu


def rows_of(mu, case):
    return [r for r in mu.rep.rows if r["case"] == case]


def test_s5_restores_new_after_a_failed_install(monkeypatch, tmp_path):
    v, done, ran, mu = s5_run(monkeypatch, tmp_path, [0, 1, 0])     # old ok, new fails, new again ok
    assert done == ["old.fwsc", "new.fwsc", "new.fwsc"]
    assert v == "FAIL" and not mu.not_new and mu.stranded is None
    assert rows_of(mu, "restore")[0]["status"] == "ok"
    assert any(r["scenario"] == "S5" and r["case"] == "error" and r["status"] == "FAIL" for r in mu.rep.rows)
    assert ran == ["S2"]                                            # back on NEW: the tier goes on


def test_s5_stranded_stops_the_tier(monkeypatch, tmp_path, capsys):
    v, done, ran, mu = s5_run(monkeypatch, tmp_path, [0, 1, 1, 1])  # new never installs
    assert done == ["old.fwsc", "new.fwsc", "new.fwsc", "new.fwsc"]
    assert v == "FAIL" and mu.stranded and ran == []
    assert rows_of(mu, "restore")[0]["status"] == "FAIL"
    nr = rows_of(mu, "not run")
    assert nr and nr[0]["scenario"] == "S2" and nr[0]["status"] == "FAIL"
    assert "LEFT ON old.fwsc" in capsys.readouterr().err


def test_s5_old_firmware_44k_only(monkeypatch, tmp_path):
    """the takes on --prev open at a rate it offers (44.1 kHz here), the install of NEW goes ahead"""
    v, done, ran, mu = s5_run(monkeypatch, tmp_path, [0, 0])
    assert done == ["old.fwsc", "new.fwsc"] and not mu.not_new and ran == ["S2"]
    assert not rows_of(mu, "restore")
    assert not [r for r in mu.rep.rows if r["case"] == "error"], mu.rep.rows


def test_s5_expects_a_stored_preset(monkeypatch, tmp_path):
    """0.9.5 design: after the update the FM-1 plays a stored preset, not the edited panel. S5: on --prev, F03 by
    Program Change, then the edits; after the install the take with nothing sent, then every factory preset by
    Program Change (F01..F16 = PC 0..15, F17..F19 = 32..34), no edits; a match passes (the fake's takes are all the
    same tone: every preset matches)"""
    w = World()
    done = []

    def inst(pkg):
        done.append(os.path.basename(pkg))
        w.reenumerate(gone=2)
        return 0, "out"
    mu = must(monkeypatch, tmp_path, w, "--scenarios", "S5", "--fwsc", "new.fwsc", "--prev", "old.fwsc",
              installer=inst)
    marks = []
    orig = mu.install

    def install(pkg):
        marks.append(len(w.log))
        orig(pkg)
    mu.install = install
    mu.run()
    assert done == ["old.fwsc", "new.fwsc"]
    old, new = w.log[marks[0]:marks[1]], w.log[marks[1]:]
    pcs = [i for i, e in enumerate(old) if e[0] == "pc"]
    assert [old[i] for i in pcs] == [("pc", m.S5_PC, 0)]
    assert [e for e in old[pcs[0]:] if e[0] == "cc" and e[1] in (81, 70, 74)], old
    i = next(k for k, e in enumerate(new) if e[0] == "pc")
    assert all(e[0] in ("on", "off") or e[:2] == ("cc", 116) for e in new[:i])     # the take after: nothing set
    j = next(k for k, e in enumerate(new) if k > i and e[0] == "cc" and e[1] == 120)  # (the defaults' panel)
    assert [e[1] for e in new[i:j] if e[0] == "pc"] == list(range(16)) + [32, 33, 34]
    assert not [e for e in new[i:j] if e[0] == "cc" and e[1] in (81, 70, 74)]       # the references: no edits
    up = {r["check"]: r for r in rows_of(mu, "update")}
    row = up["after the update vs the factory presets loaded on it (F01..F19, Program Change)"]
    assert row["status"] == "ok" and row["value"].startswith("matches F01, F02"), row
    assert not [r for r in mu.rep.rows if r["status"] == "FAIL"], mu.rep.rows


def test_s5_fails_when_no_preset_matches(monkeypatch, tmp_path):
    """the take after the update unlike every factory preset (a broken boot / migration): FAIL"""
    w = World()
    n = [0]

    def sig(sr):                                      # the 2nd take (after the update) louder than all the others
        n[0] += 1
        t = np.arange(4 * sr) / sr
        a = 0.4 if n[0] == 2 else 0.1
        return np.repeat((a * np.sin(2 * np.pi * 440 * t))[:, None], 2, axis=1)
    w.signal = sig

    def inst(pkg):
        w.reenumerate(gone=2)
        return 0, "out"
    mu = must(monkeypatch, tmp_path, w, "--scenarios", "S5", "--fwsc", "new.fwsc", "--prev", "old.fwsc",
              installer=inst)
    mu.run()
    up = {r["check"]: r for r in rows_of(mu, "update")}
    row = up["after the update vs the factory presets loaded on it (F01..F19, Program Change)"]
    assert row["status"] == "FAIL" and row["value"].startswith("matches none"), row


def test_dev_matches_felucca_port(monkeypatch):
    class Port:
        def close(self):
            pass

    class Mido:
        names = ["Felucca MIDI 1"]

        @classmethod
        def get_output_names(cls):
            return cls.names

        @classmethod
        def get_input_names(cls):
            return cls.names

        @staticmethod
        def open_output(n):
            return Port()

        @staticmethod
        def open_input(n):
            return Port()
    monkeypatch.setitem(sys.modules, "mido", Mido)
    assert isinstance(m.Dev(), m.Dev)
    Mido.names = ["IAC Driver Bus 1"]
    with pytest.raises(m.NoDevice):
        m.Dev()


def test_s4_keeps_the_take_with_an_event(monkeypatch, tmp_path):
    """S4 (as every case with a USB event or a click) saves the whole take, npz, and --rejudge judges it again"""
    def with_repeat(sr):
        y = music(4 * sr).astype(float)
        j = 2 * sr + int(np.argmax(np.abs(np.diff(y[2 * sr:2 * sr + 400, 0]))))
        y[j + 1:] = y[j:-1].copy()                               # an underrun's copy, on a slope
        return y / 32768
    w = World()
    w.signal = with_repeat
    monkeypatch.setattr(m, "POLL_S", 0.05)
    mu = must(monkeypatch, tmp_path, w, "--engines", "vibes", "--scenarios", "S4")
    mu.s4()
    rows = {r["check"]: r for r in mu.rep.rows}
    assert rows[f"USB: {m.USB_ROW}"]["status"] == "FAIL" and rows[f"USB: {m.USB_ROW}"]["value"] == "1"
    p = os.path.join(ROOT, rows["the whole take (npz: judge it again on the host)"]["value"])
    assert os.path.exists(p) and os.path.realpath(p).startswith(os.path.realpath(str(tmp_path)))
    z = np.load(p)
    assert z["usb"].shape[1] == 2 and int(z["fs_usb"]) == 48000 and str(z["scenario"]) == "S4"
    again = {r[0]: r for r in m.rejudge(p)}
    assert again[f"USB: {m.USB_ROW}"][1:] == (1, "0", "FAIL")
    assert m.tier_main(["--tier", "must", "--rejudge", p]) == 1
    # a clean take keeps nothing
    w.signal = lambda sr: music(4 * sr).astype(float) / 32768
    mu = must(monkeypatch, tmp_path / "clean", w, "--engines", "vibes", "--scenarios", "S4")
    mu.s4()
    rows = {r["check"]: r for r in mu.rep.rows}                  # (the fake answers no counters: those FAIL)
    assert rows[f"USB: {m.USB_ROW}"]["status"] == "ok"
    assert not os.path.exists(tmp_path / "clean" / "takes")
