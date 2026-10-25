# SPDX-License-Identifier: GPL-3.0-only
"""2-DOF tine-tonebar model (IMPLEMENTATION S3, S10; review 01 #4).

The tine (mass 1, normalised) and the tonebar (mass mu) are coupled by a spring k. The hammer and the damper act
on the tine only, and the pickup reads the tine only. The two normal modes therefore come with consistent
frequencies, decays and tine-to-tine weights (residues): no hand-set weights, and no pitch drift, because the
retune puts the long-sustaining mode exactly on f0.

    tb = TineBar(f_t, f_b, k_rel, mu, sigma_t, sigma_b)
    modes = tb.modes()          # [(f_hz, sigma_per_s, residue_complex), ...] sorted by frequency
    tb2 = retune(tb, f0)        # scaled so the mode dominating the late sustain sits exactly on f0
    rel = tb2.with_damper(sigma_damp).modes()   # the same system with the felt on the tine

h_tine(t) = sum_m 2 Re(r_m e^{lambda_m t}) is the tine displacement for a unit force impulse on the tine.
"""
import numpy as np


class TineBar:
    def __init__(self, f_t, f_b, k_rel, mu, sigma_t, sigma_b):
        """f_t, f_b: uncoupled tine / bar frequencies (Hz); k_rel: coupling stiffness relative to w_t^2;
        mu: bar mass / tine mass; sigma_t, sigma_b: uncoupled decay rates (1/s, amplitude e^{-sigma t})."""
        self.f_t, self.f_b, self.k_rel, self.mu = float(f_t), float(f_b), float(k_rel), float(mu)
        self.sigma_t, self.sigma_b = float(sigma_t), float(sigma_b)

    def _ss(self):
        wt, wb = 2 * np.pi * self.f_t, 2 * np.pi * self.f_b
        k = self.k_rel * wt * wt
        M = np.diag([1.0, self.mu])
        C = np.diag([2 * self.sigma_t, 2 * self.mu * self.sigma_b])
        K = np.array([[wt * wt + k, -k], [-k, self.mu * wb * wb + k]])
        Mi = np.linalg.inv(M)
        A = np.block([[np.zeros((2, 2)), np.eye(2)], [-Mi @ K, -Mi @ C]])
        B = np.concatenate([np.zeros(2), Mi @ np.array([1.0, 0.0])])
        Cout = np.array([1.0, 0.0, 0.0, 0.0])
        return A, B, Cout

    def modes(self):
        A, B, Cout = self._ss()
        lam, V = np.linalg.eig(A)
        Vi = np.linalg.inv(V)
        res = (Cout @ V) * (Vi @ B)
        out = [(abs(l.imag) / (2 * np.pi), -l.real, r) for l, r in zip(lam, res) if l.imag > 0]
        return sorted(out, key=lambda m: m[0])

    def impulse(self, fs, n):
        """Tine displacement for a unit force impulse, from the modal sum (for tests and plots)."""
        t = np.arange(n) / fs
        h = np.zeros(n)
        for f, s, r in self.modes():
            lam = -s + 2j * np.pi * f
            h += 2 * np.real(r * np.exp(lam * t))
        return h

    def with_damper(self, sigma_damp):
        return TineBar(self.f_t, self.f_b, self.k_rel, self.mu, self.sigma_t + sigma_damp, self.sigma_b)

    def scaled(self, c):
        """All frequencies (and the coupling) scaled by c; damping unchanged."""
        return TineBar(self.f_t * c, self.f_b * c, self.k_rel, self.mu, self.sigma_t, self.sigma_b)


def sustain_mode(modes, t_late=2.0):
    """Index of the mode with the larger late amplitude |r| e^{-sigma t_late}."""
    return int(np.argmax([abs(r) * np.exp(-s * t_late) for _, s, r in modes]))


def tine_mode(modes, f_t):
    """Index of the tine-like mode: the one nearest (in log frequency) to the uncoupled tine f_t. With a bar tuned
    far below the tine (keys 104-108: -3779 cents) and short T60s, sustain_mode() picks the BAR mode (its late
    amplitude wins although its tine weight is 10^-4 of the tine mode's): the retune then put a near-silent mode on
    f0 and the real tine mode at 24-30 kHz. On keys 21-103 both rules pick the same mode."""
    return int(np.argmin([abs(np.log(f / f_t)) for f, _, _ in modes]))


def retune(tb, f0, t_late=2.0, iters=6, pick="sustain"):
    """Scale tb so its sustain mode (pick="sustain") or its tine mode (pick="tine") is exactly f0 (frequency
    ratios are scale-invariant without damping; a few fixed-point iterations absorb the damping's tiny pull)."""
    for _ in range(iters):
        m = tb.modes()
        i = sustain_mode(m, t_late) if pick == "sustain" else tine_mode(m, tb.f_t)
        tb = tb.scaled(f0 / m[i][0])
    return tb
