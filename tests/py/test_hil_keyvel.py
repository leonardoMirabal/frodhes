# SPDX-License-Identifier: GPL-3.0-only
"""The key-velocity device tools without a device: tools/hil_keyvel.py (record / analyse / compare / soak / selftest /
jack) and tools/hil_cpu.py --keyadc off|idle|stress|gate, against a fake FM-1 that speaks the developer protocol of
firmware/src/keyvel.h (CC 41 modes, CC 42 queries answered on CC 38 / 39 / 40, the per-press report CC 33..36 before
each note-on, the profiler's CC 116 replies) on a fake clock (no sleeping), and a fake jack (synthetic audio)."""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_cpu as hc  # noqa: E402
import hil_keyvel as kv  # noqa: E402
import hil_scan as hs  # noqa: E402

M = hs._Msg


class Clock:
    """time.monotonic / sleep / strftime on a fake clock: a 10-minute soak runs in milliseconds"""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 1e-4)

    def strftime(self, fmt):
        return "20261024-120000" if "%H%M%S" in fmt else "2026-10-24 12:00:00"


class Mido:
    Message = M


class FM1:
    """the FM-1's developer side. presses: [(t from start, key, raw, src, wait, vel)] played when the clock passes
    them (with the report when it is on); spurious: [(t, 'button' | 'note' | 'midi')]; cpu: mode -> us added to the
    profiler's base; mode_ok False: CC 41 is ignored (an old firmware's mode stays 0); reply False: no CC 42 answer"""

    def __init__(self, clock, presses=(), spurious=(), cpu=None, base=3000, mode_ok=True, reply=True, st=(0, 0)):
        self.c, self.t0 = clock, clock.t
        self.presses, self.spurious = sorted(presses), sorted(spurious)
        self.cpu = cpu or {1: 0, 0: 4, 2: 40}
        self.base, self.mode_ok, self.reply, self.st = base, mode_ok, reply, st
        self.mode, self.report, self.q, self.mode_t = 0, False, [], clock.t
        self.notes = self.buttons = 0
        self.sent, self.rng = [], np.random.default_rng(1)

    # the port pair
    def send(self, m):
        self.sent.append(m)
        if m.type != "control_change":
            return
        if m.control == 41:
            if m.value == 8 or m.value == 9:
                self.report = m.value == 8
            elif m.value <= 4 and self.mode_ok:
                self.mode, self.mode_t, self.notes, self.buttons = m.value, self.c.t, 0, 0
        elif m.control == 42 and self.reply:
            for i in range(m.value * 8, m.value * 8 + 8):
                x = min(16383, self.item(i))
                self.q += [M("control_change", control=38, value=i), M("control_change", control=39, value=x >> 7),
                           M("control_change", control=40, value=x & 127)]
        elif m.control == 116:
            us = self.base + self.cpu.get(self.mode, 0) + int(self.rng.integers(0, 2)) * 4
            self.q += [M("control_change", control=115, value=0), M("control_change", control=116, value=us // 4 >> 7),
                       M("control_change", control=117, value=us // 4 & 127), M("control_change", control=118, value=3)]

    def item(self, i):
        dt = self.c.t - self.mode_t
        return {0: self.mode, 1: 1, 2: 1, 3: 1, 4: int(dt * (9000 if self.mode == 2 else 900)), 5: 0, 6: 0, 7: 512,
                8: 700, 9: self.st[0], 10: self.st[1], 11: int(dt * 100) if self.mode == 4 else 0, 12: self.notes,
                13: self.buttons, 16: 120, 17: int(dt * 909)}.get(i, 0)

    def iter_pending(self):
        now = self.c.t - self.t0
        while self.presses and self.presses[0][0] <= now:
            _, k, raw, src, wait, vel = self.presses.pop(0)
            self.notes += 1
            if self.report:
                self.q += [M("control_change", control=33, value=k), M("control_change", control=34, value=raw >> 3),
                           M("control_change", control=35, value=(raw & 7) | src << 3),
                           M("control_change", control=36, value=wait)]
            self.q += [M("note_on", note=53 + k, velocity=vel), M("note_off", note=53 + k, velocity=0)]
        while self.spurious and self.spurious[0][0] <= now:
            _, kind = self.spurious.pop(0)
            if kind == "button":
                self.buttons += 1
            elif kind == "note":
                self.notes += 1
            else:
                self.q.append(M("note_on", note=60, velocity=64))
        q, self.q = self.q, []
        return iter(q)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(kv, "time", c)
    monkeypatch.setattr(hc, "time", c)
    monkeypatch.setattr(kv, "_enter", lambda: False)
    return c


def dev_of(fm):
    return kv.Dev(Mido, fm, fm)


def session(rng, soft=30, hard=95, medium=64, spread=3, n=4, offset=None, phases=("soft", "medium", "hard"),
            wait=(0, 4), src=0, t0=0.0, dt=0.25):
    """synthetic presses: every analog key n times per phase (velocity ~ the phase's centre +- spread; raw falls as
    the press gets harder), plus two top-key presses per phase. offset: {key: velocity added}"""
    cen = dict(soft=soft, medium=medium, hard=hard)
    out, t = [], t0
    for ph in phases:
        ph_ps = []
        for k in range(20):
            for _ in range(n):
                v = int(np.clip(cen[ph] + (offset or {}).get(k, 0) + rng.integers(-spread, spread + 1), 1, 127))
                ph_ps.append((k, max(0, 1000 - 6 * v), src, int(rng.integers(wait[0], wait[1] + 1)), v))
        ph_ps += [(21, 0, 3, 0, 64), (25, 0, 3, 0, 70)]
        out.append([(t + i * dt, *p) for i, p in enumerate(ph_ps)])
        t += len(ph_ps) * dt + 5.0
    return out


def presses_of(blocks, phases=("soft", "medium", "hard")):
    """the same synthetic presses as parse() would hand them over"""
    return [dict(key=k, raw=raw, src=src, wait=w, vel=v, note=53 + k, t=t, phase=ph, extra=0)
            for ph, b in zip(phases, blocks) for t, k, raw, src, w, v in b]


# ---- the analysis
def test_a_good_session_passes():
    a = kv.analyse(presses_of(session(np.random.default_rng(1))))
    assert a["ok"] is True, a["gates"]
    assert a["separation"]["gap"] >= 60 and a["separation"]["order"] == 1.0 and a["separation"]["raw_order"] == 1.0
    assert a["evenness"]["spread"] <= 6 and not a["evenness"]["off"] and not a["evenness"]["missing"]
    assert a["extremes"]["at127"] == 0 and a["latency"]["max"] <= 4 and a["top_keys"]["n"] == 6
    assert a["src"]["top key"] == 6 and a["phases"] == dict(soft=82, medium=82, hard=82)


def test_overlapping_soft_and_hard_fail():
    a = kv.analyse(presses_of(session(np.random.default_rng(2), soft=55, hard=70, spread=12)))
    assert a["gates"]["separation"] is False and a["ok"] is False


def test_an_uneven_key_is_named():
    a = kv.analyse(presses_of(session(np.random.default_rng(3), offset={7: 40})))
    assert a["gates"]["evenness"] is False and a["evenness"]["off"] == [kv.key_name(7)] == ["C4"]


def test_extremes_and_latency():
    a = kv.analyse(presses_of(session(np.random.default_rng(4), medium=126, spread=3)))
    assert a["extremes"]["at127"] > 0.05 and a["gates"]["extremes"] is False
    a = kv.analyse(presses_of(session(np.random.default_rng(5), wait=(0, 6))))
    assert a["gates"]["latency"] is False and a["latency"]["max"] == 6
    ps = presses_of(session(np.random.default_rng(6)))
    for p in ps[:10]:
        p["src"] = 1                                                   # timed out (CONTEXT)
    a = kv.analyse(ps)
    assert a["latency"]["timeouts"] == 10 and a["gates"]["latency"] is False


def test_fixed_touch_measures_nothing():
    a = kv.analyse(presses_of(session(np.random.default_rng(7), src=4)))
    assert a["ok"] is None and "separation" not in a and a["src"]["fixed"] == 240


def test_parse_keeps_reports_and_notes_apart():
    msgs = [(0.0, M("control_change", control=33, value=5)), (0.0, M("control_change", control=34, value=100)),
            (0.0, M("control_change", control=35, value=3 | 0 << 3)), (0.0, M("control_change", control=36, value=2)),
            (0.001, M("note_on", note=58, velocity=77)), (0.0012, M("note_on", note=62, velocity=77)),   # CHORD
            (0.5, M("note_on", note=70, velocity=40)),                                                  # no report
            (0.6, M("note_on", note=70, velocity=0))]
    p = kv.parse(msgs, "soft")
    assert p[0] == dict(key=5, raw=803, src=0, wait=2, vel=77, note=58, t=0.0, phase="soft", extra=1)
    assert p[1]["key"] is None and p[1]["vel"] == 40 and len(p) == 2


def test_query_reads_items_among_other_messages(clock):
    fm = FM1(clock, presses=[(0.0, 3, 500, 0, 1, 90)])
    d = dev_of(fm)
    q = d.query((0, 1))
    assert q[0] == 0 and q[8] == 700 and len(q) == 16
    assert [m.type for _, m in d.take()] == ["note_on", "note_off"]
    assert dev_of(FM1(clock, reply=False)).query((0,)) is None


# ---- the commands on the fake FM-1
def test_record_end_to_end(clock, tmp_path, capsys):
    blocks = session(np.random.default_rng(8), dt=0.15)
    phase_s = 20.0                                                      # each phase 20 s on the fake clock
    flat = [(i * phase_s + 0.5 + (t - b[0][0]), *rest) for i, b in enumerate(blocks) for t, *rest in b]
    assert all(b[-1][0] - b[0][0] < phase_s - 1 for b in blocks)         # each phase's presses inside it
    fm = FM1(clock, presses=flat)
    out = tmp_path / "s.json"
    rep = kv.record(dev_of(fm), ["soft", "medium", "hard"], phase_s, "usb", "t1", str(out))
    assert rep["analysis"]["ok"] is True and rep["touch"] == "MEDIUM"
    assert len(rep["presses"]) == 246 and rep["analysis"]["phases"] == dict(soft=82, medium=82, hard=82)
    vals = [m.value for m in fm.sent if m.type == "control_change" and m.control == 41]
    assert vals == [8, 9]                                               # the report on, then off; no mode changed
    saved = json.load(open(out))
    assert saved["status_before"]["batt"] == 700 and saved["power"] == "usb"
    assert "overall: PASS" in capsys.readouterr().out
    assert kv.main(["analyse", str(out)]) == 0


def test_compare_power_sources(tmp_path, capsys):
    usb = dict(label="usb", power="usb", presses=presses_of(session(np.random.default_rng(9))))
    bat = dict(label="bat", power="battery", presses=presses_of(session(np.random.default_rng(10), soft=33, medium=66,
                                                                         hard=97)))
    c = kv.compare(usb, bat)
    assert c["ok"] and set(c["phases"]) == {"soft", "medium", "hard"} and not c["same_power"]
    far = dict(label="bat", power="battery",
               presses=presses_of(session(np.random.default_rng(11), medium=76, offset={3: 20})))
    c = kv.compare(usb, far)
    assert not c["ok"] and not c["phases"]["medium"]["ok"] and kv.key_name(3) in c["phases"]["soft"]["far"]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    json.dump(usb, open(a, "w"))
    json.dump(far, open(b, "w"))
    assert kv.main(["compare", str(a), str(b)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_soak_clean_and_spurious(clock, monkeypatch, capsys):
    fm = FM1(clock)
    t0 = clock.t
    r = kv.soak(dev_of(fm), 10, "normal")
    assert r["ok"] and clock.t - t0 >= 600 and len(r["rows"]) >= 60
    assert [m.value for m in fm.sent if m.type == "control_change" and m.control == 41] == [0, 0]
    for kind in ("button", "note", "midi"):
        fm = FM1(clock, spurious=[(185.0, kind)])
        r = kv.soak(dev_of(fm), 10, "stress")
        assert not r["ok"] and 185 <= r["rows"][-1]["t"] <= 200, kind      # stops at the first poll that sees it
        assert fm.mode == 0                                               # NORMAL again
    r = kv.soak(dev_of(FM1(clock, mode_ok=False)), 1, "storm")
    assert not r["ok"] and "mode 0" in r["bad"][0]


def test_soak_cli(clock, monkeypatch):
    fm = FM1(clock)
    monkeypatch.setattr(kv, "open_ports", lambda: (Mido, fm, fm))
    assert kv.main(["soak", "--minutes", "2", "--mode", "stress"]) == 0
    assert kv.main(["status"]) == 0


def test_selftest(clock, monkeypatch):
    assert kv.selftest(dev_of(FM1(clock)))["ok"]
    r = kv.selftest(dev_of(FM1(clock, st=(0, 3))))
    assert not r["ok"] and r["st_pa8"] == 3
    assert kv.selftest(dev_of(FM1(clock, st=(0, 3))), dict(st_max=3))["ok"]
    assert not kv.selftest(dev_of(FM1(clock, spurious=[(1.0, "button")])))["ok"]
    assert not kv.selftest(dev_of(FM1(clock, mode_ok=False)))["ok"]
    fm = FM1(clock)
    monkeypatch.setattr(kv, "open_ports", lambda: (Mido, fm, fm))
    assert kv.main(["selftest"]) == 0 and fm.mode == 0


# ---- the jack
class FakeJack:
    """hil_jack's interface on synthetic audio: USB = sines for the notes; the jack = USB - 3 dB + white noise at
    -100 dBFS, plus (per developer mode) a line at line_hz / a noise change"""
    FS = 48000

    def __init__(self, line=None, noise_db=None, seed=1):
        self.mode, self.line, self.noise_db = 1, line or {}, noise_db or {}
        self.rng = np.random.default_rng(seed)
        jack = self

        class R:
            mido = Mido

            def dry(self):
                pass

            class out:
                @staticmethod
                def send(m):
                    if m.type == "control_change" and m.control == 41:
                        jack.mode = m.value
        self.r = R()

    def both(self, dur, events):
        n = int(dur * self.FS)
        t = np.arange(n) / self.FS
        u = np.zeros(n)
        for ts, kind, a, b in events:
            if kind == "on":
                f = 440 * 2 ** ((a - 69) / 12)
                u += (b / 127) * 0.05 * np.sin(2 * np.pi * f * t) * (t >= ts)
        j = u * 10 ** (-3 / 20) + 10 ** ((-100 + self.noise_db.get(self.mode, 0)) / 20) * self.rng.standard_normal(n)
        for f, lvl in self.line.get(self.mode, {}).items():
            j += 10 ** (lvl / 20) * np.sin(2 * np.pi * f * t)
        return u, j


def test_line_db():
    rng = np.random.default_rng(3)
    t = np.arange(48000 * 2) / 48000
    x = 1e-5 * rng.standard_normal(len(t))
    assert all(kv.line_db(x, f) < 5 for f in kv.JACK_LINES)
    y = x + 2e-6 * np.sin(2 * np.pi * 909.09 * t)                      # a line 14 dB under the RMS floor
    assert kv.line_db(y, 10000 / 11) > 10 and kv.line_db(y, 10000 / 22) < 5


def test_jack_check(monkeypatch, tmp_path, clock):
    r = kv.jack_check(FakeJack(), "usb", out=str(tmp_path / "j.json"))
    assert r["ok"] and abs(r["snr_idle_vs_off_db"]) < 0.2 and abs(r["off_drift_db"]) < 0.2
    assert json.load(open(tmp_path / "j.json"))["lines_ok"] is True
    r = kv.jack_check(FakeJack(line={0: {10000 / 11: -112}}), "battery", out=str(tmp_path / "j2.json"))
    assert not r["lines_ok"] and r["lines_re_off_db"]["hiss 909.1 Hz"]["idle"] > 5 and r["power"] == "battery"
    r = kv.jack_check(FakeJack(line={2: {10000.0: -110}}), "usb", out=str(tmp_path / "j3.json"))
    assert r["ok"] and r["lines_re_off_db"]["silence 10000.0 Hz"]["stress"] > 5   # STRESS: reported, not gated
    r = kv.jack_check(FakeJack(noise_db={0: 1.0}), "usb", out=str(tmp_path / "j4.json"))
    assert not r["snr_ok"] and -1.2 < r["snr_idle_vs_off_db"] < -0.6


# ---- hil_cpu --keyadc
def cpu_run(monkeypatch, clock, argv, usb=False, **fm_kw):
    """hil_cpu.main against the fake FM-1; usb False: --no-usb-stream (no audio device on the host)"""
    fm = FM1(clock, **fm_kw)
    monkeypatch.setattr(hc, "ports", lambda: (Mido, fm, fm))
    monkeypatch.setattr(hc, "KEYADC_BAD", [])
    monkeypatch.setattr(hc, "USB_BAD_RUNS", [])
    return hc.main(["hil_cpu.py"] + argv + ([] if usb else ["--no-usb-stream"])), fm


def test_hil_cpu_keyadc_modes(monkeypatch, clock, capsys):
    for mode, want in (("off", 1), ("idle", 0), ("stress", 2)):
        r, fm = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", mode])
        assert r["keyadc"]["mode"] == want and not hc.KEYADC_BAD
        assert r["worst"] == pytest.approx(3000 + fm.cpu[want], abs=4)
        sent = [m.value for m in fm.sent if m.type == "control_change" and m.control == 41]
        assert len(sent) > 3 and set(sent[:-1]) == {want} and sent[-1] == 0   # after every reset; NORMAL at the end
    r, fm = cpu_run(monkeypatch, clock, ["--worst"])                     # without --keyadc: no CC 41 at all
    assert r["keyadc"] is None and not [m for m in fm.sent if m.type == "control_change" and m.control == 41]
    r, fm = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "stress"], mode_ok=False)
    assert hc.KEYADC_BAD and "not 2" in hc.KEYADC_BAD[0]
    r, fm = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "idle"], reply=False)
    assert hc.KEYADC_BAD and "no CC 42 reply" in hc.KEYADC_BAD[0]
    with pytest.raises(SystemExit):
        cpu_run(monkeypatch, clock, ["--keyadc", "loud"])


def test_hil_cpu_full_run_keeps_its_rows(monkeypatch, clock):
    r, _ = cpu_run(monkeypatch, clock, ["--keyadc", "idle", "--engine", "vibes"])
    names = [n for n, _ in r["rows"]]
    assert "idle, no effects" in names and "repeated" in names and len(names) == 11   # + the slow paths
    assert any(n.startswith("slow paths") for n in names) and r["code"] == 0 and hc.exit_code(r) == 0


def test_hil_cpu_exit_code_combines_the_verdict_and_the_key_check(monkeypatch, clock, capsys):
    """the exit code: the run's verdict (over 4500 us, a shed voice, a USB fault) and the key converter's check"""
    r, _ = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "idle"])
    assert r["code"] == 0 and hc.exit_code(r) == 0
    r, _ = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "idle"], base=4600)       # over the 4500 us target
    assert r["code"] == 1 and not hc.KEYADC_BAD and hc.exit_code(r) == 1
    r, _ = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "stress"], mode_ok=False)  # the key check alone
    assert r["code"] == 0 and hc.KEYADC_BAD and hc.exit_code(r) == 1
    r, _ = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "gate", "--engines", "rhodes", "--runs", "1"],
                   base=4600)                                            # the gate passes, the runs' verdicts fail
    assert r == dict(gate=True, code=1) and hc.exit_code(r) == 1
    assert "a run's own verdict FAILED" in capsys.readouterr().out


def test_hil_cpu_voices_default_per_engine(monkeypatch, clock, capsys):
    """--voices defaults to the engine's own count (rhodes.c RH_VOICES / BAR_VOICES), in the gate too"""
    cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "gate", "--engines", "rhodes,vibes", "--runs", "1"])
    out = capsys.readouterr().out
    assert f"{hc.default_voices('rhodes')} voices (--voices {hc.default_voices('rhodes')})" in out
    assert f"{hc.default_voices('vibes')} voices (--voices {hc.default_voices('vibes')})" in out


def test_hil_cpu_keyadc_gate(monkeypatch, clock, capsys):
    r, fm = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "gate"])
    out = capsys.readouterr().out
    assert not hc.KEYADC_BAD and "key ADC gate: PASS" in out
    s = hc.keyadc_gate.summary
    assert set(s) == {"rhodes", "vibes", "marimba"} and all(all(e["ok"].values()) for e in s.values())
    assert out.count("=== key ADC gate: run") == 27                     # 3 engines x 3 runs x 3 modes
    assert all(abs(e["delta"]["stress"] - 40) <= 4 for e in s.values())
    runs = [l.split("--keyadc ")[1] for l in out.splitlines() if l.startswith("=== key ADC gate: run 2/3, rhodes")]
    assert runs == ["idle", "stress", "off"]                             # rotated per run
    r, fm = cpu_run(monkeypatch, clock, ["--worst", "--keyadc", "gate", "--engines", "rhodes", "--runs", "2"],
                    cpu={1: 0, 0: 20, 2: 80})
    out = capsys.readouterr().out
    assert hc.KEYADC_BAD == ["the key ADC gate"] and "key ADC gate: FAIL" in out
    assert out.count("=== key ADC gate: run") == 6
    e = hc.keyadc_gate.summary["rhodes"]
    assert e["ok"] == dict(idle=False, stress=False)


def test_jack_check_dry_run_on_the_host(tmp_path):
    """the whole procedure through hil_jack's DryJack (the host build plays every take; the jack simulated): the
    developer CC 41 goes through the host's midi_cc like any CC, and OFF / idle / STRESS render the same"""
    import hil_jack as hj
    r = kv.jack_check(hj.DryJack(), "usb", out=str(tmp_path / "dry.json"), dry_run=True)
    assert r["ok"] and r["dry_run"] and abs(r["snr_idle_vs_off_db"]) < 0.5


def test_hil_cpu_help_and_bad_options_touch_no_device(monkeypatch, capsys):
    """--help prints the options and a bad --keyadc mode stops before any MIDI port or USB stream is opened"""
    def no_device():
        raise AssertionError("a device was opened")
    monkeypatch.setattr(hc, "ports", no_device)
    assert hc.main(["hil_cpu.py", "--help"]) == dict(code=0) and "--keyadc gate" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="--keyadc"):
        hc.main(["hil_cpu.py", "--keyadc", "loud"])
