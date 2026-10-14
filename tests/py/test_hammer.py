# SPDX-License-Identifier: GPL-3.0-only
"""tools/hammer.py: the contact simulation must match the closed-form Hertz contact on a rigid wall, conserve
energy without felt damping, and give the T_c ~ v^-(p-1)/(p+1) law (shorter, brighter contact when harder)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hammer as h  # noqa: E402

M_H, K, P = 0.011, 5e8, 2.5        # 11 g hammer; K chosen for a ~1 ms contact at 1 m/s (ESTIMATE prior)


def test_rigid_wall_matches_hertz():
    for v in (0.5, 1.0, 4.0):
        sim = h.contact(v, M_H, K, P, t_max=0.01)
        ref = h.hertz_tc(v, M_H, K, P)
        assert abs(sim["Tc"] / ref - 1) < 0.01, (v, sim["Tc"], ref)


def test_energy_conserved_without_damping():
    sim = h.contact(2.0, M_H, K, P, t_max=0.01)
    assert abs(-sim["v_out"] / 2.0 - 1) < 1e-3


def test_contact_time_power_law():
    vs = np.array([0.25, 0.5, 1.0, 2.0, 4.0])
    tc = np.array([h.contact(v, M_H, K, P, t_max=0.02)["Tc"] for v in vs])
    slope = np.polyfit(np.log(vs), np.log(tc), 1)[0]
    assert abs(slope - (-(P - 1) / (P + 1))) < 0.01, slope


def test_tine_mode_takes_momentum():
    # a light mode (large phi) at the strike point: the hammer must transfer momentum and recoil less than off a wall
    modes = [(110.0, 1.0, 1 / np.sqrt(0.03))]      # effective mass 30 g
    sim = h.contact(1.0, M_H, K, P, modes=modes, t_max=0.01)
    assert sim["impulse"] > 0 and -sim["v_out"] < 0.99
