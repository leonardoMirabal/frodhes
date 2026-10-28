# SPDX-License-Identifier: GPL-3.0-only
"""The HQ bars (FR_HQ, the Move; private plan 07 items B1 to B4, firmware/src/hq/bars_hq.c, bars_contact.c): VIBES and
MARIMBA as every Move user hears them.

- B1, the upper slots in float: with the extra modes and the contact off (HQB_EXTRA 0) the HQ voice is Classic within
  2 LSB (the sleep off: its decisions at the threshold may flip on a rounding).
- B2, the extra modes: never louder than Classic's peak by more than a little, and their generator is current.
- B3 / B4, the live contact: at rest it gives the tables' level (MARIMBA's top keys strike through it every time),
  on a ringing bar its level stays near the tables' superposition, its steps are bounded, its at-rest force scale
  is the header's.
- The engine rules on the HQ build itself: never louder after the key-up (the release must-pass), the velocity's
  dynamic range at the extremes.
The builds are the Move's settings (16 voices, FR_HQ; tools/gate_h.py build), HQ_DEFAULT says whether notes play HQ."""
import os
import pathlib
import subprocess
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostrun  # noqa: E402
import test_release_never_louder as nl  # noqa: E402

ROOT = hostrun.ROOT
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_h  # noqa: E402

D = os.path.join(ROOT, "build", "hq_bars_test")
FS = 44100
VARIANTS = {"classic": (False, ()), "hq": (True, ()), "hq-x0": (True, ("-DHQB_EXTRA=0",)),
            "hq-table": (True, ("-DHQB_CONTACT=0",))}


def binary(name):
    hq, extra = VARIANTS[name]
    return hostrun.once(os.path.join(D, name), lambda p: gate_h.build(hq, pathlib.Path(p), extra))


def render(name, script, tag, nosleep=False):
    """the take's left channel (float) from the variant's build; script: a path or a list of lines"""
    os.makedirs(D, exist_ok=True)
    if not isinstance(script, str):
        path = os.path.join(D, tag + ".txt")
        open(path, "w").write("\n".join(script) + "\n")
        script = path
    out = os.path.join(D, f"{tag}-{name}")
    env = dict(os.environ)
    env.pop("FRODHES_NOSLEEP", None)
    if nosleep:
        env["FRODHES_NOSLEEP"] = "1"
    subprocess.run([binary(name), out, script], check=True, capture_output=True, env=env)
    w = wave.open(out + ".wav")
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)[:, 0]


def db(x):
    return 20 * np.log10(max(float(x), 1e-9))


def test_parity_float_slots():
    """B1: the float upper slots are Classic's integers within 2 LSB (RAW, every card's ends, the pedal, re-strikes,
    the motor), the sleep off"""
    s = os.path.join(ROOT, "tests/hq/bars_parity.txt")
    a, b = render("classic", s, "parity", True), render("hq-x0", s, "parity", True)
    assert len(a) == len(b) and np.abs(a - b).max() <= 2, np.abs(a - b).max()


def test_parity_script_peak():
    """B2: the extra modes never take the parity script's loudest moment over Classic's by more than 1 dB, nor any
    100 ms window's peak by more than 3 dB (the extra modes are the bar's own, under V4's ceiling)"""
    s = os.path.join(ROOT, "tests/hq/bars_parity.txt")
    a, b = render("classic", s, "parity"), render("hq", s, "parity")
    assert db(np.abs(b).max()) - db(np.abs(a).max()) <= 1.0
    W = FS // 10
    n = min(len(a), len(b)) // W
    pa, pb = np.abs(a[:n * W]).reshape(n, W).max(1), np.abs(b[:n * W]).reshape(n, W).max(1)
    loud = pa > 1000
    rise = 20 * np.log10((pb[loud] + 1) / (pa[loud] + 1))
    assert rise.max() <= 3.0, (rise.max(), int(np.argmax(rise)))


def strikes(ty, notes, gap_ms=600, pedal=False):
    """RAW, the motor off: each (key, vel, then (delay, vel2) or None) from silence; -> (lines, [(t2 or t, key, vel)])"""
    L = ["0 midi 0xB0 119 127", "1 midi 0xB0 47 0", f"2 midi 0xB0 79 {ty}"] + (["3 midi 0xB0 64 127"] if pedal else [])
    t, ev = 20, []
    for k, v, re_ in notes:
        L += [f"{t} midi 0x90 {k} {v}", f"{t + 10} midi 0x80 {k} 0"]
        if re_:
            d, v2 = re_
            L += [f"{t + d} midi 0x90 {k} {v2}", f"{t + d + 10} midi 0x80 {k} 0"]
            ev.append((t + d, k, v2))
        else:
            ev.append((t, k, v))
        t += gap_ms
        L += [f"{t} midi 0xB0 123 0"] + ([f"{t + 1} midi 0xB0 64 127"] if pedal else [])
        t += 20
    L.append(f"{t + 200} end")
    return L, ev


def levels(x, ev, ms=300):
    out = []
    for t, k, v in ev:
        i = int(t * FS / 1000)
        seg = x[i:i + int(ms * FS / 1000)]
        out.append((k, v, db(np.sqrt(np.mean(seg ** 2))), db(np.abs(seg).max())))
    return out


def test_top_keys_contact_is_the_tables_level():
    """B4: MARIMBA's top keys (C6 up) strike through the live contact every time; at rest it gives the tables' level
    within 1 dB (rms and peak over 300 ms) at every velocity"""
    notes = [(k, v, None) for k in (84, 88, 91, 96, 100, 103, 108) for v in (1, 8, 40, 100, 127)]
    L, ev = strikes(127, notes, 450)
    a, b = levels(render("hq-table", L, "top"), ev), levels(render("hq", L, "top"), ev)
    bad = [(k, v, round(r2 - r1, 2), round(p2 - p1, 2)) for (k, v, r1, p1), (_, _, r2, p2) in zip(a, b)
           if abs(r2 - r1) > 1.0 or abs(p2 - p1) > 1.0]
    assert not bad, bad


RESTRIKE = [(k, 100, (d, v)) for k in (53, 60, 67, 74, 81, 88) for d in (40, 150, 400) for v in (40, 100, 127)]


@pytest.mark.parametrize("ty", [0, 127], ids=["VIBES", "MARIMBA"])
def test_restrike_level_near_the_tables(ty):
    """B3: a key struck again while its bar rings (the pedal down): the live contact's level within 6 dB of the
    tables' superposition (rms over 300 ms; the mallet meets the bar moving, so the phase decides), never over the
    take's loudest by more than 1 dB"""
    notes = [(k - (12 if ty else 0), v, r) for k, v, r in RESTRIKE]
    L, ev = strikes(ty, notes, 700, pedal=True)
    xa, xb = render("hq-table", L, f"re{ty}"), render("hq", L, f"re{ty}")
    a, b = levels(xa, ev), levels(xb, ev)
    d = [r2 - r1 for (_, _, r1, _), (_, _, r2, _) in zip(a, b)]
    assert max(abs(x) for x in d) <= 6.0, [(e, round(x, 1)) for e, x in zip(ev, d) if abs(x) > 6.0]
    assert db(np.abs(xb).max()) - db(np.abs(xa).max()) <= 1.0
    assert np.mean(np.abs(d)) > 0.1                 # (it ran: the contact changes the re-strikes)


def contact_tool():
    exe = os.path.join(D, "ckgen")

    def make(p):
        subprocess.run([os.environ.get("CC", "cc"), "-O2", "-Wall", "-ffp-contract=off", "-Ifirmware/gen",
                        "-Ifirmware/src", "-o", p, "tests/hq/bars_contact_gen.c", "-lm"], cwd=ROOT, check=True)
    return hostrun.once(exe, make)


def test_contact_steps_and_rest_scale():
    """the contact at rest on every key, law and every 6th velocity: at most 512 steps (the A72 cost: about 45 us at
    500), and its force scale the header's HQB_CK_FPK within 0.2 dB (linear between its 17 velocities)"""
    import re
    hdr = open(os.path.join(ROOT, "firmware/gen/hq/bars_hq_contact.h")).read()
    body = hdr[hdr.index("HQB_CK_FPK[BAR_NTYPE]"):]
    body = body[body.index("=") + 1:body.index("/* sha256")]
    fpk = np.array([float(x) for x in re.findall(r"[-0-9.e+]+(?=f)", body)]).reshape(2, 88, 6, 17)
    fv = [1 + 8 * i for i in range(16)] + [127]
    z = " ".join(["0"] * 16)
    cases = [(t, k, l, v) for t in (0, 1) for k in range(88) for l in range(6 if t == 0 else 5) for v in range(1, 128, 6)]
    p = subprocess.run([contact_tool(), "-s"], input="\n".join(f"{t} {k} {l} {v} {z}" for t, k, l, v in cases) + "\n",
                       capture_output=True, text=True, check=True)
    rows = p.stdout.splitlines()
    assert len(rows) == len(cases)
    steps = np.array([int(r.split()[0]) for r in rows])
    assert steps.min() > 0 and steps.max() <= 512, steps.max()
    got = np.array([float(r.split()[2]) for r in rows])
    want = np.array([np.interp(v, fv, fpk[t, k, l]) for t, k, l, v in cases])
    err = np.abs(20 * np.log10(got / want))
    assert err.max() <= 0.2, (err.max(), cases[int(np.argmax(err))])


def test_generators_current():
    """the committed HQ headers are what their generators make (the specs, the FE pins, bars_contact.c)"""
    for g in ("tools/gen_bars_hq.py", "tools/gen_bars_hq_contact.py"):
        p = subprocess.run([sys.executable, g, "--check"], cwd=ROOT, capture_output=True, text=True)
        assert p.returncode == 0 and "equal" in p.stdout, (g, p.stdout[-400:], p.stderr[-400:])


BARS = [t for t in nl.TYPES if t[1] == "79"]
NLGRID = [(ty, nz, ped, h) for ty in BARS for nz in (0, 127) for ped in (False, True) for h in (200, 2000)]


@pytest.mark.parametrize("ty,noise,pedal,hold", NLGRID,
                         ids=[f"{t[0]}-N{n}-{'ped' if p else 'up'}-{h}" for t, n, p, h in NLGRID])
def test_hq_never_louder_after_key_up(ty, noise, pedal, hold, monkeypatch):
    """the release must-pass (tests/py/test_release_never_louder.py) on the HQ build: its grid for VIBES and MARIMBA"""
    hq = binary("hq")
    monkeypatch.setattr(nl, "run", lambda *a, **k: hostrun.run(*a, binary=hq, **k))
    nl.test_never_louder_after_key_up(ty, noise, pedal, hold)


@pytest.mark.parametrize("ty", [0, 127], ids=["VIBES", "MARIMBA"])
def test_velocity_range_at_the_extremes(ty):
    """the velocity's dynamic range on the HQ build: v1 to v127 at least Classic's range less 1 dB on every key,
    and v127 never more than 1.5 dB over Classic's (rms over 300 ms, the default cards)"""
    keys = (53, 60, 72, 84, 96) if ty == 0 else (36, 48, 60, 72, 84, 96, 103)
    notes = [(k, v, None) for k in keys for v in (1, 127)]
    L, ev = strikes(ty, notes, 500)
    a, b = levels(render("classic", L, f"vr{ty}"), ev), levels(render("hq", L, f"vr{ty}"), ev)
    bad = []
    for i in range(0, len(ev), 2):
        ra, rb = a[i + 1][2] - a[i][2], b[i + 1][2] - b[i][2]
        if rb < ra - 1.0 or b[i + 1][2] - a[i + 1][2] > 1.5:
            bad.append((ev[i][1], round(ra, 1), round(rb, 1), round(b[i + 1][2] - a[i + 1][2], 2)))
    assert not bad, bad
