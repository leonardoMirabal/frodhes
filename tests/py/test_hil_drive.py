# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_drive.py without a device: the plan (every drive TYPE, CC 84 inside its band, the switch partner), the
device lock's protocol, the run log's parsing and the summary."""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_drive as hd  # noqa: E402


def bands():
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", src).group(1))
    lo = [int(v) for v in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", src).group(1).split(",")]
    ends = sorted(lo) + [128]
    return {n: (lo[i], ends[ends.index(lo[i]) + 1] - 1) for i, n in enumerate(names)}


def test_types_and_cc84_inside_their_bands():
    b = bands()
    t = hd.drive_types()
    assert [n for n, _, _ in t] == [n for n in b if n != "OFF"]
    for n, _, cc in t:
        assert b[n][0] <= cc <= b[n][1], (n, cc, b[n])
    assert dict((n, cc) for n, _, cc in t)["FDRV"] == 127


def test_cpu_plan_covers_every_type_variant_and_run():
    names = [n for n, _, _ in hd.drive_types()]
    p = hd.cpu_plan(["rhodes", "vibes"], names, list(hd.VARIANTS), 3, 48000)
    assert len(p) == 2 * len(names) * len(hd.VARIANTS) * 3
    for x in p:
        assert x["argv"][1:5] == ["--worst", "--usb", "48000", "--engine"]
        if x["variant"] == "switch":
            assert x["other"] != x["type"] and x["other"] != "FDRV" and x["other_cc84"] >= 64
        else:
            assert x["other"] is None


def test_jack_plan_is_the_p1x1_grid():
    names = [n for n, _, _ in hd.drive_types()]
    p = hd.jack_plan(names, ["riff"])
    assert p[0]["type"] == "OFF" and p[0]["cc84"] == 0
    assert {(x["setting"], x["amount"], x["tone"]) for x in p if x["type"] == "TSH8"} == set(hd.GRID)
    assert len([x for x in p if x["type"] == "KHLON"]) == len(hd.GRID_KHLON)
    assert all(1 <= x["amount"] <= 127 for x in p if x["type"] != "OFF")    # AMOUNT 0 would be dry
    for ph in hd.PHRASES.values():
        ev, dur = ph()
        assert ev and max(t for t, *_ in ev) < dur
        assert all(k in ("on", "off", "cc") for _, k, _, _ in ev)


def test_device_lock(tmp_path):
    a = hd.DeviceLock(str(tmp_path), "test", 0.0, poll_s=0.01)
    with a:
        assert os.path.isdir(a.dir) and a.owner().startswith("test ")
        with hd.DeviceLock(str(tmp_path), "child", 0.0, poll_s=0.01) as c:     # the holder's own tool: goes on
            assert c.inherited
        assert os.path.isdir(a.dir)                    # and leaves it to the holder
        tok = os.environ.pop("FRODHES_LOCK_DEVICE")    # another session (no token): waits, gives up
        b = hd.DeviceLock(str(tmp_path), "other", 0.05, poll_s=0.01)
        try:
            with b:
                raise AssertionError("a second holder got the lock")
        except SystemExit as e:
            assert "still held" in str(e)
        os.environ["FRODHES_LOCK_DEVICE"] = tok
    assert not os.path.exists(a.dir)
    os.makedirs(a.dir)                                 # someone else's lock: never removed by us
    open(os.path.join(a.dir, "owner"), "w").write("someone\n")
    c = hd.DeviceLock(str(tmp_path), "test", 0.0, poll_s=0.01)
    c.mine = "test x"
    c.__exit__(None, None, None)
    assert os.path.isdir(a.dir)


def test_dry_run_touches_no_device(capsys):
    assert hd.main(["--cpu", "--jack", "--dry-run", "--engines", "rhodes", "--runs", "1"]) == 0
    out = capsys.readouterr().out
    assert "DEVICE.lockdir" in out and "hil_cpu runs" in out


def test_parse_and_summary():
    text = ("x  attack 4100 us (70 %)   held 4200 us (72 %)   voices shed 0\n"
            "y  attack 4000 us (69 %)   held 4300 us (74 %)   voices shed 0\nwhole ISR worst: 4388 us\nhil_cpu: ok\n")
    assert hd.parse_run(text) == (4388, 0, True)
    assert hd.parse_run("whole ISR worst: not reported by this firmware\nhil_cpu: ok\n") == (None, 0, True)
    rows = [dict(engine="rhodes", type=t, variant="base", other=None, run=1, whole_us=w, app_us=w - 40, shed=0,
                 verdict="ok") for t, w in (("FDRV", 4300), ("TSH8", 4330))]
    md = hd.cpu_summary(rows)
    assert "| rhodes | TSH8 | base | 4330 | 4330 | 170 | +30 |" in md
