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


def contact_batch(cases, fs=44100, t_max=0.03, os_=32):
    """Many contacts at once (numpy over cases), RK4 at os_ x fs. cases: list of dicts with v, m_h, K, p, Rd and
    modes [(f_hz, sigma, phi_at_strike), ...] (the same number of modes for every case). Returns per case the
    force on the 16x fs grid (area-preserving average of the os_ grid), Tc, the contact count, v_out, and the
    final modal state (q, qd) for diagnostics."""
    N = len(cases)
    nm = len(cases[0]["modes"])
    v0 = np.array([c["v"] for c in cases], float)
    M = np.array([c["m_h"] for c in cases], float)
    K = np.array([c["K"] for c in cases], float)
    P = np.array([c["p"] for c in cases], float)
    Rd = np.array([c.get("Rd", 0.0) for c in cases], float)
    AL = np.array([c.get("alpha", 0.0) for c in cases], float)     # Hunt-Crossley: F = K e^p (1 + alpha de/dt)
    w = np.array([[2 * np.pi * m[0] for m in c["modes"]] for c in cases])
    s = np.array([[m[1] for m in c["modes"]] for c in cases])
    ph = np.array([[m[2] for m in c["modes"]] for c in cases])
    w2 = w * w
    dt = 1.0 / (fs * os_)
    n = int(t_max / dt)

    def deriv(xh, vh, q, qd):
        e = xh - np.sum(ph * q, axis=1)
        de = vh - np.sum(ph * qd, axis=1)
        ep = np.where(e > 0, np.abs(e), 0.0)
        F = K * ep ** P * (1 + AL * de) + Rd * P * ep ** (P - 1) * de
        F = np.where(e > 0, np.maximum(F, 0.0), 0.0)
        return vh, -F / M, qd, ph * F[:, None] - 2 * s * qd - w2 * q, F

    xh = np.zeros(N); vh = v0.copy(); q = np.zeros((N, nm)); qd = np.zeros((N, nm))
    step = os_ // OS
    Fout = np.zeros((N, n // step + 1))
    acc = np.zeros(N)
    touched = np.zeros(N, bool)
    done = np.zeros(N, bool)
    last = np.zeros(N, int)
    ncont = np.zeros(N, int)
    prev_on = np.zeros(N, bool)
    vout = np.zeros(N)
    for i in range(n):
        a1 = deriv(xh, vh, q, qd)
        a2 = deriv(xh + 0.5 * dt * a1[0], vh + 0.5 * dt * a1[1], q + 0.5 * dt * a1[2], qd + 0.5 * dt * a1[3])
        a3 = deriv(xh + 0.5 * dt * a2[0], vh + 0.5 * dt * a2[1], q + 0.5 * dt * a2[2], qd + 0.5 * dt * a2[3])
        a4 = deriv(xh + dt * a3[0], vh + dt * a3[1], q + dt * a3[2], qd + dt * a3[3])
        F = a1[4]
        live = ~done
        xh = np.where(live, xh + dt / 6 * (a1[0] + 2 * a2[0] + 2 * a3[0] + a4[0]), xh)
        vh = np.where(live, vh + dt / 6 * (a1[1] + 2 * a2[1] + 2 * a3[1] + a4[1]), vh)
        q = np.where(live[:, None], q + dt / 6 * (a1[2] + 2 * a2[2] + 2 * a3[2] + a4[2]), q)
        qd = np.where(live[:, None], qd + dt / 6 * (a1[3] + 2 * a2[3] + 2 * a3[3] + a4[3]), qd)
        F = np.where(live, F, 0.0)
        on = F > 0
        ncont += on & ~prev_on
        prev_on = on
        touched |= on
        last = np.where(on, i, last)
        acc += F
        if (i + 1) % step == 0:
            Fout[:, (i + 1) // step - 1] = acc / step
            acc[:] = 0
        clear = touched & ~on & (vh < 0) & (np.sum(ph * q, axis=1) - xh > 2e-4)
        newly = clear & ~done
        vout = np.where(newly, vh, vout)
        done |= clear
        if done.all():
            break
    vout = np.where(done, vout, vh)
    out = []
    for c in range(N):
        nlast = last[c] // step + 1
        out.append(dict(F=Fout[c, :nlast + 1], dt=dt * step, Tc=(last[c] + 1) * dt, ncont=int(ncont[c]),
                        v_out=float(vout[c]), impulse=float(np.sum(Fout[c, :nlast + 1]) * dt * step),
                        q=q[c].copy(), qd=qd[c].copy()))
    return out
