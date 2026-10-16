# SPDX-License-Identifier: GPL-3.0-only
"""The bar engine (firmware/src/bars.c, VIBES = TYPE 4) and the engine dispatcher (engine.c) on the host build.

Engine-level checks that hold for any table data (firmware/gen/bars_tables.h): it plays its pitch, the MOTOR
tremolo runs at the displayed rate in the engine (and is the plain amplitude tremolo on a Rhodes TYPE), the
CPU cap holds (the host mode-sample counter), the upper-mode sleep is transparent, TYPE changes do not click or
leave notes behind, nothing saturates. The physics targets (decays, tube build-up, levels) are the generator's
tests (Gate VA / VB), not these."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
sys.path.insert(0, os.path.join(ROOT, "tools"))
FS = 44100
VIBES = "60 midi 0xB0 79 64\n"             # CC 79: the bar types (VIBES)
RAW = "50 midi 0xB0 119 127\n"
F3B = [53, 55, 57, 59, 60, 62, 64, 65]


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def render(tag, script, env=None):
    out = os.path.join(ROOT, "build", "host", f"bars-{tag}")
    with open(out + ".txt", "w") as f:
        f.write(script)
    r = subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float)
    return x, r.stdout


def counter(log, name):
    ln = next(s for s in log.splitlines() if s.startswith(name))
    return ln


def clicks(m):                              # tools/hil_scan.py clicks(): second-difference spikes
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def peak_hz(x, f_lo, f_hi):
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)), 1 << 20))
    f = np.fft.rfftfreq(1 << 20, 1 / FS)
    s = (f > f_lo) & (f < f_hi)
    i = np.argmax(X * s)
    a, b, c = np.log(X[i - 1:i + 2] + 1e-30)
    return f[i] + 0.5 * (a - c) / (a - 2 * b + c) * (f[1] - f[0])


@pytest.mark.parametrize("note", [53, 60, 69, 77, 89])
def test_vibes_plays_its_pitch(note):
    x, log = render(f"pitch{note}", RAW + VIBES + "61 midi 0xB0 104 0\n" + f"100 midi 0x90 {note} 100\n2600 end\n")
    f0 = 440 * 2 ** ((note - 69) / 12)
    seg = x[int(0.6 * FS):int(2.5 * FS), 0]
    cents = 1200 * np.log2(peak_hz(seg, f0 * 0.97, f0 * 1.03) / f0)
    assert abs(cents) < 5.0, cents                  # the placeholder tables retune to ~2 c; the generator to 0.5 c
    assert counter(log, "sat28").split()[-1] == "0"
    assert np.max(np.abs(x)) < 32000


def am(x, t0=1.0, t1=4.0, hop=220):
    m = x[:, 0]
    env = np.array([np.sqrt(np.mean(m[i:i + hop] ** 2)) for i in range(0, len(m) - hop, hop)]) + 1e-9
    t = np.arange(len(env)) * hop / FS
    s = (t > t0) & (t < t1)
    e = 20 * np.log10(env[s])
    r = e - np.polyval(np.polyfit(t[s], e, 1), t[s])
    F = np.abs(np.fft.rfft(r * np.hanning(len(r)), 1 << 16))
    f = np.fft.rfftfreq(1 << 16, hop / FS)
    k = np.argmax(F * (f > 0.3))
    return f[k], np.percentile(r, 98) - np.percentile(r, 2)


MOTOR = "61 midi 0xB0 104 0\n62 midi 0xB0 17 64\n63 midi 0xB0 21 127\n64 midi 0xB0 18 127\n65 midi 0xB0 19 {rate}\n"


@pytest.mark.parametrize("rate", [40, 78, 110])
def test_motor_rate_is_the_displayed_rate(rate):
    x, _ = render(f"mot{rate}", RAW + VIBES + MOTOR.format(rate=rate) + "70 midi 0xB0 64 127\n"
                  "200 midi 0x90 69 100\n4200 end\n")
    hz = 0.5 * 24 ** (rate / 127)                   # FX_LFO_INC, the TREM/WAH card's Hz
    f, depth = am(x)
    assert abs(f / hz - 1) < 0.05, (f, hz)
    assert depth > 3.0, depth                       # the depth target (7-11 dB) is the generator's (VA-M)


def test_motor_off_and_depth_zero_do_not_modulate():
    x, _ = render("mot0", RAW + VIBES + MOTOR.format(rate=78).replace("18 127", "18 0") + "70 midi 0xB0 64 127\n"
                  "200 midi 0x90 69 100\n4200 end\n")
    y, _ = render("motoff", RAW + VIBES + "61 midi 0xB0 104 0\n70 midi 0xB0 64 127\n200 midi 0x90 69 100\n4200 end\n")
    assert np.array_equal(x, y)                     # discs parked open = no motor at all


def test_motor_on_a_rhodes_type_is_the_am_tremolo():
    base = "50 midi 0xB0 80 0\n51 midi 0xB0 17 64\n53 midi 0xB0 18 100\n54 midi 0xB0 19 80\n200 midi 0x90 60 100\n2000 end\n"
    a, _ = render("rh_am", base + "52 midi 0xB0 21 64\n")
    m, _ = render("rh_mot", base + "52 midi 0xB0 21 127\n")
    assert np.array_equal(a, m)


def test_motor_passes_fx_trem_on_vibes():
    """On VIBES the effects chain's tremolo is unity in MOTOR mode: with RAW off the motor's AM equals the RAW one"""
    s = VIBES + MOTOR.format(rate=78) + "70 midi 0xB0 64 127\n200 midi 0x90 69 100\n4200 end\n"
    raw, _ = render("mot_raw", RAW + s)
    fx, _ = render("mot_fx", s)
    _, d_raw = am(raw)
    _, d_fx = am(fx)
    assert abs(d_raw - d_fx) < 1.0, (d_raw, d_fx)


def edge_chord(vel=127, noise=48, end=3000):
    s = RAW + VIBES + ("62 midi 0xB0 28 127\n63 midi 0xB0 29 127\n64 midi 0xB0 30 127\n65 midi 0xB0 31 127\n"
                       f"66 midi 0xB0 104 {noise}\n70 midi 0xB0 64 127\n")
    s += "".join(f"200 midi 0x90 {n} {vel}\n" for n in F3B)
    s += "".join(f"{200 + 400 * r} midi 0x90 {n} {vel}\n" for r in range(1, 4) for n in F3B)
    return s + f"{end} end\n"


def test_cpu_cap_holds():
    """8 voices struck at the edge with everything at maximum, again and again: the live mode-equivalents per sample
    never exceed 8 pairs (3 each) + the budget's upper slots (bars.c BAR_BUDGET_MAX)."""
    src = open(os.path.join(ROOT, "firmware", "src", "bars.c")).read()
    cap = int(src.split("#define BAR_BUDGET_MAX")[1].split()[0].rstrip("u"))
    x, log = render("cap", edge_chord())
    ln = counter(log, "mode-samples")
    mx = int(ln.split("max per block")[1].split(",")[0])
    assert mx <= (8 * 3 + cap) * 256, (mx, cap)
    assert counter(log, "sat28").split()[-1] == "0"
    assert clicks(x[:, 0]) == 0


@pytest.mark.parametrize("tone", [64, 127])
def test_sleep_is_transparent(tone):
    """S12: the upper modes' sleep (absolute + relative, faded over a sub-block) against a render where nothing
    sleeps: the residual stays 50 dB under the signal in every 1 s window"""
    s = RAW + VIBES + f"62 midi 0xB0 28 {tone}\n66 midi 0xB0 104 0\n70 midi 0xB0 64 127\n"
    s += "".join(f"200 midi 0x90 {n} 100\n" for n in F3B) + "4000 end\n"
    ref, _ = render(f"s12ref{tone}", s, {"FRODHES_NOSLEEP": "1", "FRODHES_NOBUDGET": "1"})
    slp, _ = render(f"s12slp{tone}", s, {"FRODHES_NOBUDGET": "1"})
    a, b = slp[:, 0], ref[:, 0]
    worst = -999.0
    for t0 in np.arange(0.2, len(a) / FS - 1.0, 0.25):
        w = slice(int(t0 * FS), int((t0 + 1.0) * FS))
        worst = max(worst, 10 * np.log10(np.sum((a[w] - b[w]) ** 2) / np.sum(b[w] ** 2) + 1e-30))
    assert worst <= -50.0, worst


def test_budget_drops_only_quiet_detail():
    """The budget on 8 simultaneous edge strikes (the worst case it exists for): residual vs no budget <= -40 dB
    over the first second (the plan's S12 budget limit), no clicks"""
    s = RAW + VIBES + "62 midi 0xB0 28 127\n63 midi 0xB0 29 127\n66 midi 0xB0 104 0\n70 midi 0xB0 64 127\n"
    s += "".join(f"200 midi 0x90 {n} 127\n" for n in F3B) + "2000 end\n"
    ref, _ = render("budref", s, {"FRODHES_NOBUDGET": "1"})
    bud, _ = render("bud", s)
    w = slice(int(0.2 * FS), int(1.2 * FS))
    r = 10 * np.log10(np.sum((bud[w, 0] - ref[w, 0]) ** 2) / np.sum(ref[w, 0] ** 2))
    assert r <= -40.0, r
    assert clicks(bud[:, 0]) == 0


def test_type_swap_no_click_no_stuck_notes():
    s = RAW + "60 midi 0xB0 64 127\n" + "".join(f"100 midi 0x90 {n} 110\n" for n in (36, 48, 55, 64))
    s += "700 midi 0xB0 79 64\n720 midi 0x90 60 110\n720 midi 0x90 67 110\n"
    s += "1300 midi 0xB0 80 0\n1320 midi 0x90 57 110\n"
    s += "1900 midi 0xB0 79 64\n1903 midi 0xB0 80 40\n1906 midi 0xB0 79 64\n1950 midi 0x90 72 110\n"
    s += "".join(f"2500 midi 0x80 {n} 0\n" for n in (36, 48, 55, 64, 60, 67, 57, 72)) + "2550 midi 0xB0 64 0\n4500 end\n"
    x, log = render("swap", s)
    m = x[:, 0]
    assert clicks(m) == 0
    assert counter(log, "ev_drop").split()[-1] == "0"
    for t in (0.75, 1.35, 2.0):                      # the notes struck after each change sound
        w = m[int(t * FS):int((t + 0.2) * FS)]
        assert np.sqrt(np.mean(w * w)) > 100, t
    assert np.max(np.abs(m[int(4.0 * FS):])) == 0    # everything ended: exact silence


def test_noise_zero_is_noiseless():
    """NOISE 0: no contact noise, no damper knock: two renders of the same note with different noise seeds history
    (a NOISE 127 note first, then NOISE 0) end up identical to a fresh NOISE 0 render"""
    a, _ = render("nz0", RAW + VIBES + "61 midi 0xB0 104 0\n1000 midi 0x90 60 100\n2000 midi 0x80 60 0\n3000 end\n")
    b, _ = render("nz1", RAW + VIBES + "61 midi 0xB0 104 127\n100 midi 0x90 72 100\n300 midi 0x80 72 0\n"
                  "990 midi 0xB0 104 0\n1000 midi 0x90 60 100\n2000 midi 0x80 60 0\n3000 end\n")
    s = slice(int(1.0 * FS), int(3.0 * FS))
    assert np.array_equal(a[s], b[s])
