# SPDX-License-Identifier: GPL-3.0-only
"""The reverb TYPEs' FM-1 tools without the FM-1: tools/hil_cpu_rev.py (hil_cpu with the TYPE, the heaviest drive,
TAPE, SPREAD), tools/hil_crackle_rev.py (the cracks test with INFINITE frozen part way) and tools/hil_rev_takes.py
(the listening takes): their --dry-run, the MIDI rewrites on a fake port, the CC values they send select the TYPE on
the host build, the takes they plan."""
import os
import subprocess
import sys

import pytest

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_cpu_rev as hcr  # noqa: E402

PY = sys.executable
HAS = bool(hcr.rev_type_ccs()[0])


def tool(*args):
    return subprocess.run([PY, os.path.join(ROOT, "tools", args[0]), *args[1:]], capture_output=True, text=True,
                          cwd=ROOT, timeout=120)


def test_cpu_dry_run():
    r = tool("hil_cpu_rev.py", "--dry-run", "--rev-type", "all", "--spread", "127", "--", "--worst", "--usb", "48000")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "hil_cpu_rev: dry run" in r.stdout and "The FM-1's audio CPU load" in r.stdout
    if HAS:
        assert "3 run(s): HALL, PLATE, INFINITE" in r.stdout and "SPREAD 127" in r.stdout
    else:
        assert "no reverb TYPEs in this tree" in r.stdout


def test_split_args():
    mine, rest = hcr.split_args(["--rev-type", "plate", "--worst", "--engine", "vibes", "--spread", "3", "--dry-run"])
    assert mine == ["--rev-type", "plate", "--spread", "3", "--dry-run"] and rest == ["--worst", "--engine", "vibes"]
    mine, rest = hcr.split_args(["--rev-type", "all", "--", "--spread", "9"])
    assert mine == ["--rev-type", "all"] and rest == ["--spread", "9"]


class FakePort:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


def test_out_rewrites():
    """CC > 0 rewritten, 0 passed, the after-CCs right behind their trigger, SPREAD before a note"""
    mido = pytest.importorskip("mido")
    p = FakePort()
    o = hcr.Out(p, {95: 112, 84: 81}, {91: [(95, 112), (69, 64)]}, spread=127)
    for c, v in ((95, 48), (95, 0), (84, 127), (91, 100), (91, 0), (7, 5)):
        o.send(mido.Message("control_change", control=c, value=v))
    o.send(mido.Message("note_on", note=60, velocity=100))
    got = [(m.control, m.value) if m.type == "control_change" else ("on", m.note) for m in p.sent]
    assert got == [(95, 112), (95, 0), (84, 81), (91, 100), (95, 112), (69, 64), (91, 0), (7, 5), (105, 127),
                   ("on", 60)]
    assert o.n == {95: 2, 84: 1, 69: 1, 105: 1}


def test_generic_bands():
    assert [hcr.generic_cc(v, 3) for v in range(4)] == [16, 48, 80, 112]
    assert all(c * 4 >> 7 == v for v, c in enumerate(hcr.generic_cc(v, 3) for v in range(4)))


@pytest.mark.skipif(not HAS, reason="no reverb TYPEs in this tree")
def test_cc_values_select_the_type_on_the_host():
    """the values the FM-1 tools send are inside each TYPE's band on the host build's own CC map
    (test_rev_types.rv_cc: every value 0..127 sent, read back)"""
    from test_rev_types import p_ids
    from hostrun import cc, run
    ccs, c95 = hcr.rev_type_ccs()
    pid = p_ids()["RV_TYPE"]
    lines = []
    for i, (name, v) in enumerate(ccs.items()):
        lines += [cc(100 + 10 * i, c95, v), f"{105 + 10 * i} expect state p.{pid} {('HALL', 'PLATE', 'INFINITE').index(name) + 1}"]
    run("hil-rev-cc", lines, 200)


@pytest.mark.skipif(not HAS, reason="no reverb TYPEs in this tree")
def test_crackle_dry_run_adds_the_type_and_the_freeze():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import hil_crackle as hc
    import hil_crackle_rev as hcrv
    base = {n: (c, e) for n, c, e, _ in hc.takes("rhodes", 20.0, 24.0, 0)}
    saved = hc.engine_ccs, hc.takes, hc.OUT
    try:
        notes = hcrv.patch("infinite", 100, 4.0)
        new = {n: (c, e) for n, c, e, _ in hc.takes("rhodes", 20.0, 24.0, 0)}
    finally:
        hc.engine_ccs, hc.takes, hc.OUT = saved
    ccs, c95 = hcr.rev_type_ccs()
    assert "INFINITE" in notes[0]
    for n, (c, e) in new.items():
        assert c[-2:] == [(c95, ccs["INFINITE"]), (92, 100)] and c[:-2] == base[n][0], n
        extra = [x for x in e if x not in base[n][1]]
        assert extra == ([(4.0, "cc", 92, 127)] if "worst" in n else []), (n, extra)
    r = tool("hil_crackle_rev.py", "--rate", "48000", "--dry-run", "--quick")
    assert r.returncode == 0 and "frozen" in r.stdout and "rhodes-worst1" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(not HAS, reason="no reverb TYPEs in this tree")
def test_takes_plan():
    import hil_rev_takes as hrt
    T = hrt.takes()
    names = [t[0] for t in T]
    assert len(names) == len(set(names))
    for typ in ("hall", "plate", "infinite"):
        for s in ("short", "default", "long"):
            assert f"{typ}-{s}" in names
    fr = dict((t[0], t) for t in T)["infinite-freeze-60s"]
    assert any(e[1] == "cc" and e[2] == 92 and e[3] == 127 for e in fr[2]) and fr[3] >= 70
    for name, setup, ev, dur in T:
        assert all(0 <= e[0] < dur for e in ev), name
        ons = sum(e[1] == "on" for e in ev)
        assert ons == sum(e[1] == "off" for e in ev), name               # every note let go
    r = tool("hil_rev_takes.py", "--dry-run")
    assert r.returncode == 0 and "takes on rhodes" in r.stdout, r.stderr


@pytest.mark.skipif(not HAS, reason="no reverb TYPEs in this tree")
def test_takes_on_the_host(tmp_path):
    """two takes rendered by the host build in the FM-1's place: the WAVs and takes.json written, sound in them"""
    import json
    import wave
    r = tool("hil_rev_takes.py", "--host", "--only", "plate-default,switch-types", "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    log = json.load(open(tmp_path / "takes.json"))
    assert [x["name"] for x in log] == ["plate-default", "switch-types"]
    for x in log:
        assert x["peak_usb"] > 1000
        w = wave.open(str(tmp_path / f"{x['name']}-usb.wav"))
        assert w.getnchannels() == 2 and w.getnframes() > 44100 * 10
