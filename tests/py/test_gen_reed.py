# SPDX-License-Identifier: GPL-3.0-only
"""The WHURLI tables (tools/gen_reed.py -> firmware/gen/reed_tables.h): the header is current, fits the flash budget,
compiles with the engine's types at the interface's sizes, keeps every word out of the chip-ROM window, and the
physics is sane: mode 1 on the key's pitch, modes ordered, decays positive and shorter up the keyboard, the damper
faster than the sustain, louder and wider with velocity, warm when soft and barking when hard in the middle, nearly
pure in the treble, the pickup table monotone and in the loop's bounds, and the pickup's aliasing small."""
import math
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_reed as gw  # noqa: E402
import reed_model as rm  # noqa: E402
import rh_coefs as rc  # noqa: E402


@pytest.fixture(scope="module")
def spec():
    return gw.build()


@pytest.fixture(scope="module")
def hdr():
    return open(gw.OUT).read()


def test_header_current():
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_reed.py"), "--check"]).returncode == 0


def test_size_budget():
    assert sum(gw.sizes().values()) <= gw.BUDGET


def test_compiles_at_the_interface_sizes(tmp_path):
    c = tmp_path / "t.c"
    c.write_text("""#include <stdint.h>
#include <stdio.h>
typedef struct { int32_t th[3]; uint32_t d[3], drel[3]; int32_t b[3]; int32_t gain, kill; uint16_t life, pad; } rd_key_t;
typedef struct { int32_t off_delta; int32_t bark, bark_g; int32_t bell, ham; uint8_t nbody, drive_row; } rh_type_t;
typedef struct { int32_t bark_max, off_max; uint16_t pamp_max, pad; } rd_lim_t;
#define RH_PLEN 64
#include "%s"
int main(void) { printf("%%zu %%zu %%zu %%zu %%zu\\n", sizeof RD_KEYS, sizeof RD_PRATE + sizeof RD_PAMP + sizeof RD_PGAIN,
                        sizeof RD_KGRP, sizeof RD_PULSE, sizeof RD_PHIM); return 0; }
""" % os.path.abspath(gw.OUT))
    exe = tmp_path / "t"
    subprocess.run(["cc", "-Wall", "-Werror", "-Wno-unused-const-variable", "-Wno-unused-variable", str(c), "-o",
                    str(exe)], check=True)
    got = [int(x) for x in subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()]
    sz = gw.sizes()
    assert got == [sz["RD_KEYS"], sz["RD_PRATE+PAMP+PGAIN"], sz["RD_KGRP"], sz["RD_PULSE"], sz["RD_PHIM"]]


def test_rom_window(hdr):
    ws = gw.words(hdr)
    assert len(ws) > 3000                 # (the compact rows: 3922 words; the parse found the arrays)
    bad = [v for v in ws if gw.ROM_LO <= (v & 0xFFFFFFFF) <= gw.ROM_HI]
    assert not bad, bad[:5]


def test_mode1_on_pitch_and_modes_ordered(spec):
    for k in spec["keys"]:
        f = [m["f"] for m in k["modes"]]
        assert f[0] < f[1] < 18000 and f[2] > f[1], (k["midi"], f)   # (a mode over 18 kHz is left out: th = 0)
        w = rc.mode_coefs(f[0], k["modes"][0]["t60"], k["modes"][0]["t60_rel"])
        f_th = w["th"] / (1 << 29) * gw.FS / (2 * math.pi)        # the engine's pitch word
        assert abs(1200 * math.log2(f_th / k["f0"])) < 0.5, (k["midi"], f_th, k["f0"])
        assert 5.5 < f[1] / f[0] < 10.0, (k["midi"], f[1] / f[0])  # a clamped bar with a tip mass: 6.27 and up
        assert k["reed"]["m_tip_g"] > 0                            # tuned by adding solder, never removing steel


def test_decays(spec):
    t1 = [k["modes"][0]["t60"] for k in spec["keys"]]
    assert all(t > 0 for k in spec["keys"] for t in (m["t60"] for m in k["modes"]))
    assert all(a >= b for a, b in zip(t1, t1[1:])), "mode-1 sustain must not grow up the keyboard"
    for k in spec["keys"]:
        for m in k["modes"]:
            assert 0 < m["t60_rel"] <= m["t60"]
            assert m["Q_sup"] > 0 and m["Q"] > 50
        assert k["modes"][1]["t60"] < k["modes"][0]["t60"]
        assert 0 < k["life_blocks"] <= 6891


def test_velocity_laws(spec):
    for k in spec["keys"]:
        pa = k["pulse"]["pamp"]
        assert all(1 <= a <= 32767 for a in pa) and all(a <= b for a, b in zip(pa, pa[1:])), (k["midi"], pa)
        sw = [t[0] for t in k["tip_mm"]]
        assert all(a < b for a, b in zip(sw, sw[1:])), (k["midi"], sw)
        assert all(256 <= r <= 65535 for r in k["pulse"]["prate"])


def test_pickup_table_monotone_in_bounds(spec):
    P, M = spec["pick"]["P"], spec["pick"]["M"]
    assert len(P) == gw.PU_N + 1
    assert all(a <= b for a, b in zip(P, P[1:])), "C(s) falls away from the plate's mid-plane"
    assert all(0 <= p < (1 << 29) for p in P) and all(abs(m) < (1 << 23) for m in M)
    assert P[0] < P[-1]


def test_bark_limit_keeps_states_unsaturated(spec):
    assert spec["lim"]["x_peak"] * spec["lim"]["bark_max"] / gw.Q16 <= gw.X_MAX + 1e-9
    assert gw.Q16 <= spec["lim"]["bark_max"] <= 2 * gw.Q16


def _h(spec, key, vel):
    s = next(k for k in spec["keys"] if k["midi"] == key)
    y = rm.render_batch([s], spec["pick"], [vel], 0.2, rlc=spec["rlc"])[0]
    return rm.harmonics(y, s["f0"], 0.03, 0.13)


def test_warm_soft_bark_hard_pure_treble(spec):
    for key in (57, 60, 64):
        soft, hard = _h(spec, key, 24), _h(spec, key, 127)
        assert soft[1] < -15 and soft[2] < -30, (key, soft[:4])     # soft: the fundamental, a little H2
        assert hard[1] > -6 and hard[2] > -12, (key, hard[:4])     # hard: H2, H3 up with the fundamental
    assert _h(spec, 93, 127)[1] < -10                               # the top stays nearly a sine
    lo, hi = _h(spec, 45, 64), _h(spec, 45, 127)
    assert sum(10 ** (x / 10) for x in hi[1:6]) > 4 * sum(10 ** (x / 10) for x in lo[1:6])   # harder = richer


def test_pickup_aliasing(spec):
    from scipy.signal import resample_poly
    pk = spec["pick"]
    sc = spec["lim"]["bark_max"] / gw.Q16
    for key in (40, 57):
        s = next(k for k in spec["keys"] if k["midi"] == key)
        sp = dict(s, modes=[dict(m, b=m["b"] * sc) for m in s["modes"]])
        tip = rm.modes_batch([sp], [127], 11025)[0]
        u = tip + s["off"]
        d = np.diff(gw.hermite(pk["P"], pk["M"], u * u))
        tu = resample_poly(tip, 8, 1) + s["off"]
        cd = resample_poly(gw.hermite(pk["P"], pk["M"], tu * tu), 1, 8)
        dd = np.diff(cd)
        e = (d - dd)[1000:-1000]
        assert 10 * np.log10(np.sum(e ** 2) / np.sum(dd[1000:-1000] ** 2)) < -50, key
