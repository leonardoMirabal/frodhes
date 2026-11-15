# SPDX-License-Identifier: GPL-3.0-only
"""The HQ Rhodes (FR_HQ, the Move; private plan 06, firmware/src/hq/): parity with Classic, Gate H, the top keys.

The HQ voice is the Classic voice with the exact hammer contact at every velocity, on keys >= 85 the pickup at
2x, and on keys >= 60 a re-strike's live contact (the hammer against the ringing tine); under the 2x keys the pickup
in closed form, its soft notes' decay glide run past Classic's table limit, and above v120 the pulse's force at the
fundamental matched to Classic's (0.9.12, the HQ uplift). Both builds here are the Move's settings (16 voices, FR_HQ); HQ_DEFAULT says whether new notes play HQ."""
import os
import pathlib
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostrun  # noqa: E402

ROOT = hostrun.ROOT
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist  # noqa: E402
import gate_h  # noqa: E402

D = os.path.join(ROOT, "build", "hq_test")
DELAY = 31                                     # the 2x pickup's delay (rhodes_hq.c: 2P - 1)


def binary(hq, live=True, ext=True):
    b = os.path.join(D, (("hq" if live else "hq-nolive") + ("" if ext else "-noext")) if hq else "classic")
    x = (() if live else ("-DHQ_LIVE_MAX=0u",)) + (() if ext else ("-DHQ_HD_EXT=0",))
    return hostrun.once(b, lambda p: gate_h.build(hq, pathlib.Path(p), x))


def render(hq, script, tag, live=True, ext=True):
    out = os.path.join(D, f"{tag}-{('hq' if live else 'hqt') if hq else 'cl'}{'' if ext else 'x'}")
    subprocess.run([binary(hq, live, ext), out, script], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    raw = w.readframes(w.getnframes())
    return raw, np.frombuffer(raw, dtype="<i2").astype(float).reshape(-1, 2)[:, 0]


def test_parity_below_the_2x_keys_at_the_layers():
    """keys 21..84 at the 8 layer velocities, every TYPE, the pedal: HQ with the glide stopped at Classic's limit
    (HQ_HD_EXT 0) is Classic to the table's error: the exact pickup against Classic's Hermite table, at most 8 LSB
    and 1.5 LSB rms over the take (measured 5 and ~1.0; until 0.9.12 the same pickup, byte for byte)"""
    s = os.path.join(ROOT, "tests/hq/hq_parity.txt")
    h = np.frombuffer(render(True, s, "parity", ext=False)[0], dtype="<i2").astype(float)
    c = np.frombuffer(render(False, s, "parity")[0], dtype="<i2").astype(float)
    assert len(h) == len(c)
    d = h - c
    assert np.max(np.abs(d)) <= 8, np.max(np.abs(d))
    assert np.sqrt(np.mean(d ** 2)) <= 1.5, np.sqrt(np.mean(d ** 2))


def _held(tmp_path, notes, hold, tag, cc=None):
    """each (key, vel) held `hold` s and released, one after another; the voicing at its defaults, FX off (CC 80 0),
    or `cc`'s lines: Classic's and HQ's renders and each note's start / key-up sample"""
    t, L, at = 0, cc or (dry_dist.type_base_lines(0) + ["0 midi 0xB0 80 0"]), []
    for n, v in notes:
        L += [f"{t + 20} midi 0x90 {n} {v}", f"{t + 20 + int(hold * 1000)} midi 0x80 {n} 0"]
        at.append((n, v, int((t + 20) * 44.1), int((t + 20 + hold * 1000) * 44.1)))
        t += int(hold * 1000) + 1500
    L.append(f"{t} end")
    s = tmp_path / f"{tag}.txt"
    s.write_text("\n".join(L) + "\n")
    return render(False, str(s), tag)[1], render(True, str(s), tag)[1], at


def _harm(x, n, k):
    """the level (dB) of harmonic k of key n in x (8192 points, Hann; the band +-25 % of the fundamental)"""
    f = 440.0 * 2 ** ((n - 69) / 12.0)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / 44100.0)
    return 10 * np.log10(np.sum(S[(fr > (k - 0.25) * f) & (fr < (k + 0.25) * f)]) + 1e-30)


def _t60(x, n, k, a, b):
    """harmonic k's T60 (s) from its level over [a, b) s, a least-squares line through 0.25 s steps"""
    t = np.arange(a, b, 0.25)
    y = [_harm(x[int(q * 44100):int(q * 44100) + 8192], n, k) for q in t]
    return -60.0 / np.polyfit(t, y, 1)[0], np.array(y)


def test_soft_glide_second_harmonic(tmp_path):
    """the soft notes' glide past Classic's limit (0.9.12), STAGE 77 RAW (the sonic check's voicing): v20 and v40 held
    8 s on keys 28, 48, 64. Over 0.5..7.5 s HQ's 2nd harmonic decays at least 1.5x slower than Classic's (Classic
    reaches its table's limit in the first seconds; build/sonic/check0910.py's T60 H2/H1 soft row, to the noise floor:
    0.54 -> 0.84, measured 0.65..0.93 per note), the fundamental's T60 is Classic's within 2 %, H2/H1 rises at most
    8 dB over its 0.1 s value (measured 7.1 at key 28: the bloom, bounded); v64 (out of the glide's range) keeps
    Classic's 2nd harmonic within 5 %; never louder after the key-up"""
    notes = [(n, v) for n in (28, 48, 64) for v in (20, 40, 64)]
    c, h, at = _held(tmp_path, notes, 8.0, "glide", ["0 midi 0xB0 119 127", "0 midi 0xB0 104 0"])
    for n, v, a, up in at:
        t0 = a / 44100.0
        c1, _ = _t60(c, n, 1, t0 + 0.5, t0 + 7.5)
        h1, y1 = _t60(h, n, 1, t0 + 0.5, t0 + 7.5)
        c2, _ = _t60(c, n, 2, t0 + 0.5, t0 + 7.5)
        h2, y2 = _t60(h, n, 2, t0 + 0.5, t0 + 7.5)
        assert abs(h1 / c1 - 1) < 0.02, (n, v, c1, h1)
        if v <= 40:
            assert h2 / c2 >= 1.5, (n, v, c2, h2)
            w = h[a + 4410:a + 4410 + 8192]
            r0 = _harm(w, n, 2) - _harm(w, n, 1)
            assert np.max(y2 - y1) - r0 <= 8.0, (n, v, np.max(y2 - y1) - r0)
        else:
            assert abs(h2 / c2 - 1) < 0.05, (n, v, c2, h2)
        r = [np.sqrt(np.mean(h[up + i:up + i + 441] ** 2)) for i in range(0, 22050, 441)]
        assert max(r[1:]) <= r[0], (n, v, "louder after the key-up")


def test_top_velocities_fundamental_is_classic(tmp_path):
    """v121..127 (0.9.12): the exact pulse's force at the fundamental matched to Classic's (hq_ir_tab), so the
    fundamental's level over 0.05..0.45 s (release_diff's window) is Classic's within 0.05 dB on keys 33..84 (before:
    -0.06 dB, -0.4 on key 96, the dynamics row's slope under the FM-1 build's)"""
    notes = [(n, v) for n in (33, 45, 60, 72, 84) for v in (121, 124, 127)]
    c, h, at = _held(tmp_path, notes, 0.6, "top")
    for n, v, a, up in at:
        w0, w1 = a + int(0.05 * 44100), a + int(0.45 * 44100)
        d = _harm(h[w0:w1], n, 1) - _harm(c[w0:w1], n, 1)
        assert abs(d) < 0.05, (n, v, d)


def test_gate_h():
    got = gate_h.sums(binary(True), pathlib.Path(D))   # (the test's own folder, not build/gate_h)
    want = {ln.split()[1]: ln.split()[0] for ln in gate_h.BASE.read_text().splitlines()
            if ln and not ln.startswith("#")}
    assert got == want, "Gate H differs: a change to the HQ sound needs tools/gate_h.py --save (and its reasons)"


def test_top_keys_same_level_band_limited(tmp_path):
    """keys 85..107 on STAGE 77 at the layer velocities 120 and 104 (the same pulse as Classic): the 2x pickup keeps
    each note's level within 0.1 dB, and differs from Classic only by the band the half-band takes away (the top
    of the spectrum and Classic's aliasing), at least 40 dB under the note (the live contact off: it is the pickup's
    test, and the second strike of each key meets a ringing tine)"""
    t, L = 0, dry_dist.type_base_lines(0) + ["0 midi 0xB0 80 0"]   # (the voicing at its defaults, not F01's)
    notes = []
    for n in range(85, 109, 2):
        for v in (120, 104):
            L += [f"{t + 20} midi 0x90 {n} {v}", f"{t + 320} midi 0x80 {n} 0"]
            notes.append((n, v, t + 20)); t += 350
    L.append(f"{t + 300} end")
    s = tmp_path / "top.txt"
    s.write_text("\n".join(L) + "\n")
    _, c = render(False, str(s), "top")
    _, h = render(True, str(s), "top", live=False)     # (each key twice: the second strike's tine still rings)
    h = h[DELAY:]
    for n, v, t0 in notes:
        a = int(t0 * 44.1); b = a + int(270 * 44.1)          # (to before the key-up)
        C, H = c[a:b], h[a:b]
        lv = 10 * np.log10(np.sum(H ** 2) / np.sum(C ** 2))
        er = 10 * np.log10(np.sum((H - C) ** 2) / np.sum(C ** 2))
        assert abs(lv) < 0.1, (n, v, lv)
        assert er < -40.0, (n, v, er)


def test_dense_table_equals_the_layers(tmp_path):
    """the dense table (firmware/gen/hq/rh_hq_pulse.h) at the 8 layer velocities is RH_PULSE / RH_PRATE exactly"""
    c = tmp_path / "t.c"
    c.write_text("""#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct { int32_t sh, ch, th, d, drel, cr, b; } rh_mode_t;
typedef struct { rh_mode_t m[5]; int32_t off, gain, kill, hc, pk; uint16_t prate, life; uint8_t band, fam, res, pad; } rh_key_t;
typedef struct { int32_t off_delta, bark, bark_g, bell, ham; uint8_t nbody, drive_row; } rh_type_t;
#include "rhodes_tables.h"
#include "hq/rh_hq_pulse.h"
int main(void) {
    static const int lv[8] = {8, 24, 40, 56, 72, 88, 104, 120}, ai[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    int bad = 0;
    for (int k = 0; k < RH_NKEY; k++)
        for (int l = 0; l < 8; l++) {
            if (memcmp(RH_HQ_PULSE[k][lv[l] - 1], RH_PULSE[k][l], sizeof RH_PULSE[k][l])) bad++;
            if (RH_HQ_PRATE[k][lv[l] - 1] != (uint32_t)RH_PRATE_B[k] + RH_PRATE[k][ai[l]]) bad++;
        }
    printf("%d\\n", bad);
    return bad != 0;
}
""")
    exe = tmp_path / "t"
    subprocess.run(["cc", "-O0", "-w", "-I" + os.path.join(ROOT, "firmware/gen"), "-o", str(exe), str(c)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "0", r.stdout


def test_flux_constants_are_the_tables():
    """rhodes_hq.c's closed-form flux uses each family's R and the Classic table's scale Q28 / A"""
    import re
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gen_rhodes as gr
    src = open(os.path.join(ROOT, "firmware/src/hq/rhodes_hq.c")).read()
    R = [float(x.rstrip("f")) for x in re.search(r"HQ_PU_R\[2\] = \{([^}]*)\}", src).group(1).split(",")]
    K = [float(x.rstrip("f")) for x in re.search(r"HQ_PU_K\[2\] = \{([^}]*)\}", src).group(1).split(",")]
    p = gr.load_params()
    assert R == list(p["pickup"]["R"])
    for r, k in zip(R, K):
        assert k == (1 << 28) / gr.pickup_tables(r)[2]


def _gen_arrays():
    """the dense table's pulses and rates, the live contact's at-rest peak forces (the generated headers)"""
    import re
    s = open(os.path.join(ROOT, "firmware/gen/hq/rh_hq_pulse.h")).read()
    i = s.index("RH_HQ_PRATE")
    rows = re.findall(r"\{(-?[0-9,-]+)\}", s[s.index("= {") + 3:i])
    tab = np.array([[int(x) for x in r.split(",")] for r in rows]).reshape(88, 127, -1)
    pr = np.array([int(x) for x in re.findall(r"\d+", s[s.index("*/", i) + 2:])[:88 * 127]]).reshape(88, 127)
    c = open(os.path.join(ROOT, "firmware/gen/hq/rh_hq_contact.h")).read()
    j, k = c.index("HQ_CK_FPK"), c.index("HQ_CK_VH")
    fpk = np.array([float(x) for x in re.findall(r"([-0-9.e+]+)f", c[c.index("{", j):k])]).reshape(88, 127)
    return tab, pr, fpk


def test_live_contact_at_rest_is_the_table(tmp_path):
    """hq_contact.c from a tine at rest (keys 60..108, 8 velocities) makes the dense table's pulse: the shape within
    -37 dB everywhere (-46 dB on 9 in 10, a median under -50 dB; seen -42.5 / -47.6 / -52.9), the rate within 0.5 %,
    the peak force within 1 %. The rate is quantised (the contact's length in cells: one step is ~1.2 % at key 86):
    a contact that ends right at a step's edge may land on the next step, which is the table's rate of a velocity
    1-2 away; the rate and shape are then checked against that velocity's cell (0.9.10's longer decay: key 86
    v90 gives the v88 cell, 49345 against 49932: its shape -37.8 dB against that cell, so the worst case is -37 dB)"""
    exe = tmp_path / "ct"
    subprocess.run(["cc", "-O2", "-ffp-contract=off", "-w", "-I" + os.path.join(ROOT, "firmware/src"),
                    "-I" + os.path.join(ROOT, "firmware/gen"), "-o", str(exe),
                    os.path.join(ROOT, "tests/hq/contact_test.c"), "-lm"], check=True)
    tab, pr, fpk = _gen_arrays()
    cases = [(k, v) for k in range(39, 88) for v in (1, 20, 40, 64, 90, 110, 120, 127)]
    inp = "".join(f"{k} {v} " + " ".join(["0"] * 12) + "\n" for k, v in cases)
    out = subprocess.run([str(exe)], input=inp, capture_output=True, text=True, check=True).stdout.split("\n")
    errs = []
    for (k, v), ln in zip(cases, out):
        f = ln.split()
        j = min(range(max(0, v - 3), min(127, v + 2)), key=lambda c: (abs(int(f[1]) / pr[k, c] - 1), abs(c - v + 1)))
        sh, t = np.array([int(x) for x in f[3:]], float), tab[k, j, :64].astype(float)
        errs.append(20 * np.log10(max(np.sqrt(np.mean((sh - t) ** 2)) / np.sqrt(np.mean(t ** 2)), 1e-9)))
        assert abs(int(f[1]) / pr[k, j] - 1) < 0.005, (k + 21, v, f[1], pr[k, v - 1])
        assert abs(float(f[2]) / fpk[k, v - 1] - 1) < 0.01, (k + 21, v)
    errs = np.sort(errs)
    assert errs[-1] < -37.0 and errs[int(0.9 * len(errs))] < -46.0 and np.median(errs) < -50.0, errs[-5:]


def _restrikes():
    """tests/hq/hq_restrike.txt's cases: (key, v1, v2, the re-strike's ms, its key-up's ms)"""
    ev = [ln.split() for ln in open(os.path.join(ROOT, "tests/hq/hq_restrike.txt")) if ln[0].isdigit()]
    on = [(int(e[0]), int(e[3]), int(e[4])) for e in ev if e[1] == "midi" and e[2] == "0x90"]
    off = [int(e[0]) for e in ev if e[1] == "midi" and e[2] == "0x80"]
    return [(a[1], a[2], b[2], b[0], off[2 * i + 1]) for i, (a, b) in enumerate(zip(on[::2], on[1::2]))]


def test_restrike_live_contact():
    """tests/hq/hq_restrike.txt with the live contact against the table's pulse superposed (HQ_LIVE_MAX 0): it changes
    the re-strikes (by -20 dB or more on some), keeps each re-strike's level within 2 dB of the table's, and the
    sound never grows after the key-up (the 10 ms level under the key-up's, both builds)"""
    s = os.path.join(ROOT, "tests/hq/hq_restrike.txt")
    _, live = render(True, s, "restrike")
    _, tab = render(True, s, "restrike", live=False)
    diffs = []
    for n, v1, v2, t, up in _restrikes():
        a, b = int(t * 44.1), int(up * 44.1)
        L, T = live[a:b], tab[a:b]
        lv = 10 * np.log10(np.sum(L ** 2) / np.sum(T ** 2))
        diffs.append(10 * np.log10(np.sum((L - T) ** 2) / np.sum(T ** 2) + 1e-30))
        assert abs(lv) < 2.0, (n, v1, v2, lv)
        for x in (live, tab):
            r = [np.sqrt(np.mean(x[b + i:b + i + 441] ** 2)) for i in range(0, int(0.5 * 44100), 441)]
            assert max(r[1:]) <= r[0], (n, v1, v2, "louder after the key-up")
    assert max(diffs) > -20.0, diffs
