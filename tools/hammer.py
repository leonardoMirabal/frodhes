# SPDX-License-Identifier: GPL-3.0-only
"""Hammer-felt-tine contact simulation, run on the host to generate the hammer force pulses
(IMPLEMENTATION S8; research/03 eq. 9-15; review 01 #5).

The felt is a stiffening contact: F = K e^p + Rd d(e^p)/dt for compression e > 0 (Hunt-Crossley form, p ~ 2.5).
The tine is its first few FE modes at the strike point (mass-normalised: q_m'' + 2 s_m q_m' + w_m^2 q_m =
phi_m(xc) F), so the hammer feels the tine's effective mass, recoils, and can re-contact. Integrated with RK4 at
16x the audio rate. Hammer and tine start at rest except for the hammer speed v.

    sim = contact(v, m_h, K, p, Rd, modes=[(f_hz, sigma, phi_xc), ...])
    sim["F"], sim["t"], sim["Tc"], sim["impulse"]
"""
import numpy as np

OS = 16


def contact(v, m_h, K, p=2.5, Rd=0.0, modes=(), fs=44100, t_max=0.02, os_=OS):
    """Force on the tine (N) at fs*os_, from first touch until t_max. modes: [(f_hz, sigma, phi_at_strike)].
    With no modes the tine is a rigid, immovable wall. A stiff contact that makes the explicit RK4 blow up is
    re-run at 4x the internal rate (up to 256x fs)."""
    with np.errstate(over="ignore", invalid="ignore"):
        r = _contact(v, m_h, K, p, Rd, modes, fs, t_max, os_)
    while not np.all(np.isfinite(r["F"])) and os_ < 256:
        os_ *= 4
        with np.errstate(over="ignore", invalid="ignore"):
            r = _contact(v, m_h, K, p, Rd, modes, fs, t_max, os_)
    if not np.all(np.isfinite(r["F"])):
        raise FloatingPointError("contact: unstable even at 256x fs")
    if os_ != OS:                                   # resample the force to the standard 16x grid
        step = os_ // OS
        n = len(r["F"]) // step
        r["F"] = r["F"][:n * step].reshape(n, step).mean(axis=1)
        r["dt"] = r["dt"] * step
        r["t"] = np.arange(n) * r["dt"]
    return r


def _contact(v, m_h, K, p, Rd, modes, fs, t_max, os_):
    dt = 1.0 / (fs * os_)
    nm = len(modes)
    w = np.array([2 * np.pi * m[0] for m in modes])
    s = np.array([m[1] for m in modes])
    ph = np.array([m[2] for m in modes])

    def felt(e, de):
        if e <= 0:
            return 0.0
        ep = e**p
        f = K * ep + Rd * p * e ** (p - 1) * de
        return max(f, 0.0)                         # felt can push, not pull

    def deriv(y):
        xh, vh = y[0], y[1]
        q, qd = y[2:2 + nm], y[2 + nm:]
        xt = float(ph @ q) if nm else 0.0
        vt = float(ph @ qd) if nm else 0.0
        F = felt(xh - xt, vh - vt)
        dy = np.empty_like(y)
        dy[0] = vh
        dy[1] = -F / m_h
        if nm:
            dy[2:2 + nm] = qd
            dy[2 + nm:] = ph * F - 2 * s * qd - w * w * q
        return dy, F

    y = np.zeros(2 + 2 * nm)
    y[1] = v
    n = int(t_max / dt)
    F = np.zeros(n)
    touched = False
    for i in range(n):
        k1, F[i] = deriv(y)
        k2, _ = deriv(y + 0.5 * dt * k1)
        k3, _ = deriv(y + 0.5 * dt * k2)
        k4, _ = deriv(y + dt * k3)
        y = y + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        touched |= F[i] > 0
        if touched and F[i] == 0 and y[1] < 0:      # free and moving away: stop once clearly clear of the tine
            xt = float(ph @ y[2:2 + nm]) if nm else 0.0
            if xt - y[0] > 2e-4:
                F = F[:i + 1]
                break
    nz = np.nonzero(F > 0)[0]
    Tc = (nz[-1] + 1) * dt if len(nz) else 0.0
    first_end = nz[np.argmax(np.diff(np.append(nz, nz[-1] + 2)) > 1)] + 1 if len(nz) else 0
    return dict(F=F, t=np.arange(len(F)) * dt, Tc=Tc, Tc_first=first_end * dt, impulse=float(np.sum(F) * dt),
                v_out=float(y[1]), dt=dt)


def hertz_tc(v, m_h, K, p=2.5):
    """Contact time of a mass m_h hitting a rigid wall through F = K e^p (no damping), closed form:
    Tc = 2 (m (p+1) / (2K))^{1/(p+1)} v^{(1-p)/(1+p)} * I,  I = integral_0^1 dz / sqrt(1 - z^{p+1})."""
    from scipy.integrate import quad
    I = quad(lambda z: 1 / np.sqrt(1 - z ** (p + 1)), 0, 1, limit=200)[0]
    emax = (m_h * v * v * (p + 1) / (2 * K)) ** (1 / (p + 1))
    return 2 * emax / v * I
