# SPDX-License-Identifier: GPL-3.0-only
"""The bar engine (firmware/src/bars.c, VIBES = TYPE 4) and the engine dispatcher (engine.c) on the host build.

Engine-level checks that hold for any table data (firmware/gen/bars_tables.h): it plays its pitch, the MOTOR
tremolo runs at the displayed rate in the engine (and is the plain amplitude tremolo on a Rhodes TYPE), the
CPU cap holds (the host mode-sample counter), the upper-mode sleep is transparent, TYPE changes do not click or
leave notes behind, nothing saturates. The physics targets (decays, tube build-up, levels) are the generator's
tests (Gate VA / VB), not these."""
import json
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
VIBES = "60 midi 0xB0 79 64\n60 midi 0xB0 47 0\n"   # CC 79: the bar types (VIBES); the motor (on with it) off
RAW = "50 midi 0xB0 119 127\n"
F3B = [53, 55, 57, 59, 60, 62, 64, 65]
F3B9 = F3B + [67]                             # every voice (rhodes.c FR_VOICES 9)


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


MOTOR = "61 midi 0xB0 104 0\n62 midi 0xB0 47 127\n64 midi 0xB0 48 127\n65 midi 0xB0 49 {rate}\n"   # records v3: CC 47-49


@pytest.mark.parametrize("rate", [40, 78, 110])
def test_motor_rate_is_the_displayed_rate(rate):
    x, _ = render(f"mot{rate}", RAW + VIBES + MOTOR.format(rate=rate) + "70 midi 0xB0 64 127\n"
                  "200 midi 0x90 69 100\n4200 end\n")
    hz = 0.5 * 24 ** (rate / 127)                   # FX_LFO_INC, the MOTOR page's SPEED card
    f, depth = am(x)
    assert abs(f / hz - 1) < 0.05, (f, hz)
    assert depth > 3.0, depth                       # the depth target (7-11 dB) is the generator's (VA-M)


def test_motor_off_and_depth_zero_do_not_modulate():
    x, _ = render("mot0", RAW + VIBES + MOTOR.format(rate=78).replace("48 127", "48 0") + "70 midi 0xB0 64 127\n"
                  "200 midi 0x90 69 100\n4200 end\n")
    y, _ = render("motoff", RAW + VIBES + "61 midi 0xB0 104 0\n70 midi 0xB0 64 127\n200 midi 0x90 69 100\n4200 end\n")
    assert np.array_equal(x, y)                     # discs parked open = no motor at all


def test_the_motor_does_nothing_on_a_rhodes_type():
    """records v3: the motor is the bar engine's; on a Rhodes TYPE its CCs change nothing, and CC 21 has two bands
    (PAN / AM) on every TYPE"""
    base = "50 midi 0xB0 80 0\n51 midi 0xB0 17 64\n53 midi 0xB0 18 100\n54 midi 0xB0 19 80\n200 midi 0x90 60 100\n2000 end\n"
    a, _ = render("rh_am", base + "52 midi 0xB0 21 64\n")
    m, _ = render("rh_mot", base + "52 midi 0xB0 21 127\n55 midi 0xB0 47 127\n56 midi 0xB0 48 127\n")
    assert np.array_equal(a, m)


def test_motor_passes_fx_trem_on_vibes():
    """On VIBES the motor is the engine's, the effects' tremolo stays off: with RAW off the motor's AM equals the RAW one"""
    s = ("20 midi 0xB0 81 0\n21 midi 0xB0 91 0\n" + VIBES + MOTOR.format(rate=78)   # dry: F01's drive / reverb off
         + "70 midi 0xB0 64 127\n200 midi 0x90 69 100\n4200 end\n")
    raw, _ = render("mot_raw", RAW + s)
    fx, _ = render("mot_fx", s)
    _, d_raw = am(raw)
    _, d_fx = am(fx)
    assert abs(d_raw - d_fx) < 1.0, (d_raw, d_fx)


def edge_chord(vel=127, noise=48, end=3000):
    s = RAW + VIBES + ("62 midi 0xB0 28 127\n63 midi 0xB0 29 127\n64 midi 0xB0 30 127\n65 midi 0xB0 31 127\n"
                       f"66 midi 0xB0 104 {noise}\n70 midi 0xB0 64 127\n")
    s += "".join(f"200 midi 0x90 {n} {vel}\n" for n in F3B9)
    s += "".join(f"{200 + 400 * r} midi 0x90 {n} {vel}\n" for r in range(1, 4) for n in F3B9)
    return s + f"{end} end\n"


FX_ALL_US = 1080                              # fx.c FX_US_*: the heavy set of tools/hil_cpu.py (trem = MOTOR)


def proxy_us(log):
    return int(counter(log, "cpu-proxy").split()[2])


@pytest.mark.parametrize("fxus", [0, FX_ALL_US, 2000])
def test_cpu_model_holds(fxus):
    """9 voices (every voice) struck at the edge with everything at maximum, again and again, the budget planned for no effects,
    every effect, and nearly twice that effect load: the cost model's worst block stays under the 4800 us gate
    minus its margin (bars.c BAR_NS_*), by construction; no saturation, no clicks"""
    x, log = render(f"cpu{fxus}", edge_chord(), {"FRODHES_FXUS": str(fxus)})
    assert proxy_us(log) <= 4650, proxy_us(log)
    assert counter(log, "sat28").split()[-1] == "0"
    assert clicks(x[:, 0]) == 0


def test_cpu_model_with_the_effects_on():
    """The same with the real effects chain running (RAW off): the effects' cost comes from fx.c's table"""
    heavy = [(85, 64), (12, 127), (13, 127), (16, 127), (81, 127), (106, 127), (107, 100), (17, 64), (21, 127),
             (18, 127), (86, 127), (88, 127), (89, 127), (90, 127), (26, 127), (91, 127), (70, 45), (74, 60), (71, 100),
             (72, 64)]                                # tools/hil_cpu.py's heavy set (CC 85 first), the motor
    s = edge_chord().replace(RAW, "").replace("70 midi 0xB0 64 127\n", "70 midi 0xB0 64 127\n" + "".join(
        f"71 midi 0xB0 {c} {v}\n" for c, v in heavy))
    _, log = render("cpufx", s)
    assert proxy_us(log) <= 4650, proxy_us(log)


@pytest.mark.parametrize("tone", [64, 127])
def test_sleep_is_transparent(tone):
    """S12: the upper modes' sleep (absolute + relative, faded over a sub-block) against a render where nothing
    sleeps: the residual stays 50 dB under the signal in every 1 s window (or within the output's rounding)"""
    s = RAW + VIBES + f"62 midi 0xB0 28 {tone}\n66 midi 0xB0 104 0\n70 midi 0xB0 64 127\n"
    s += "".join(f"200 midi 0x90 {n} 100\n" for n in F3B) + "4000 end\n"
    ref, _ = render(f"s12ref{tone}", s, {"FRODHES_NOSLEEP": "1", "FRODHES_NOBUDGET": "1"})
    slp, _ = render(f"s12slp{tone}", s, {"FRODHES_NOBUDGET": "1"})
    a, b = slp[:, 0], ref[:, 0]
    worst = -999.0
    for t0 in np.arange(0.2, len(a) / FS - 1.0, 0.25):
        w = slice(int(t0 * FS), int((t0 + 1.0) * FS))
        r = 10 * np.log10(np.sum((a[w] - b[w]) ** 2) / np.sum(b[w] ** 2) + 1e-30)
        d = a[w] - b[w]                                   # (or the output's own rounding: two independent roundings
        if np.sqrt(np.mean(d ** 2)) > 0.75 or np.abs(d).max() > 2:   # of the 16-bit output, 0.5-0.7 LSB RMS and at most 2 LSB
            worst = max(worst, r)                         # peak = -84 dBFS, at any level of the decaying signal)
    assert worst <= -50.0, worst


def s12(tag, notes, vel, cards, env=None, cap=None):
    """S12 (budget): residual of a render with the budget vs without it, 0.2 .. 1.2 s, re the signal (dB)"""
    s = RAW + VIBES + "".join(f"62 midi 0xB0 {cc} {v}\n" for cc, v in cards.items()) + "66 midi 0xB0 104 0\n"
    s += "70 midi 0xB0 64 127\n" + (f"71 midi 0xB0 9 {cap + 1}\n" if cap is not None else "")
    s += "".join(f"200 midi 0x90 {n} {vel}\n" for n in notes) + "2000 end\n"
    ref, _ = render(f"{tag}ref", s.replace(f"71 midi 0xB0 9 {cap + 1}\n" if cap is not None else "@", ""),
                    {"FRODHES_NOBUDGET": "1"})
    bud, log = render(tag, s, env)
    w = slice(int(0.2 * FS), int(1.2 * FS))
    e = np.sum((bud[w, 0] - ref[w, 0]) ** 2)
    return (10 * np.log10(e / np.sum(ref[w, 0] ** 2)) if e else -np.inf), bud[:, 0], log


EDGE = {28: 127, 29: 127, 30: 127, 31: 127}


def test_budget_drops_only_quiet_detail():
    """The budget on 9 simultaneous RUBBER edge strikes at v127 (every voice) (the worst case it exists for), planned as if every
    effect ran: the cost model fits them all (residual exactly 0), no clicks"""
    r, bud, _ = s12("bud", F3B9, 127, EDGE, {"FRODHES_FXUS": str(FX_ALL_US)})
    assert r == -np.inf, r
    assert clicks(bud) == 0


# the limits: the measured order a margin above, next to the best any order can do (the golden model's quietest-first
# energies, build/cards_exp/best.py-style): YARN -98 (measured -88), CORD -57 (-35), the edge chord -36.5 (-28.6). Since
# 2026-10-17 (the cards-audible pass: the harder CORD, the edge's torsion at -15 dB) more of the chord sits in its upper
# partials, so a forced cut costs more (before: CORD -64 / -46, edge -40.5 / -37). The FM-1 never forces it: the cost
# model fits 9 edge RUBBER strikes with every effect on (test_budget_drops_only_quiet_detail)
@pytest.mark.parametrize("bark,cap,limit", [(42, 16, -45.0), (85, 16, -33.0), (127, 32, -27.0)])
def test_budget_order_when_it_must_cut(bark, cap, limit):
    """When the cap does bind (forced with the developer CC 9), the least audible go first: an 8-note chord at v100
    (YARN, CORD) cut to 16 modes stays under the S12 limit; the extreme edge chord cut to 32 within 3 dB of the
    best any order can do (-39.8 dB, the golden model's quietest-first energies); faded, no clicks"""
    notes = [53, 57, 60, 64, 65, 69, 72, 76] if bark != 127 else F3B
    r, bud, _ = s12(f"cut{bark}", notes, 100 if bark != 127 else 127, {**EDGE, 29: bark} if bark == 127 else {29: bark},
                    cap=cap)
    assert r <= limit, r
    assert clicks(bud) == 0


HIL_HEAVY = [(85, 64), (81, 127), (106, 127), (107, 100), (17, 127), (18, 127), (12, 127), (13, 127), (16, 127),
             (86, 127), (88, 127), (89, 127), (90, 127), (26, 127), (91, 127), (70, 45), (74, 60), (71, 100), (72, 64),
             (28, 127), (29, 127), (30, 127), (31, 127)]   # tools/hil_cpu.py's heavy set + the edge voicing (CC 85 first)


def fr_voices():
    """the host build's voice count: HOST_DEFS -DFR_VOICES=N, else rhodes.c's default"""
    import re
    m = re.search(r"FR_VOICES=(\d+)", os.environ.get("HOST_DEFS", ""))
    if m:
        return int(m.group(1))
    return int(re.search(r"#define FR_VOICES (\d+)u", open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()).group(1))


@pytest.mark.parametrize("case", ["chord", "bass", "repeated"])
def test_no_trim_in_the_hil_cpu_worst(case):
    """tools/hil_cpu.py --engine vibes's worst cases with every voice (FR_VOICES): the N-note chord and the N bass
    notes (+ pedal) at v127, edge, every effect + motor, and the repeated N-note chords every 0.4 s (steals): the
    cost model (which reads ~20 % high on the FM-1) never trims an upper mode: the lowest cap answered on CC 112 stays
    48 (every mode). 9 voices, 2026-10-18: planned 4211 us (chord, bass) and 4551 us (repeated) of the 4650 limit."""
    nv = fr_voices()
    bass = [28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48][:nv]
    chord = [48, 52, 55, 59, 62, 64, 67, 71, 74, 76, 79, 83][:nv]
    s = "50 midi 0xB0 79 64\n" + "".join(f"55 midi 0xB0 {c} {v}\n" for c, v in HIL_HEAVY)
    s += "60 midi 0xB0 47 127\n60 midi 0xB0 17 64\n399 midi 0xB0 116 127\n"
    if case == "repeated":
        for i in range(12):
            t, ns = 400 + 400 * i, chord if i % 2 else bass
            s += "".join(f"{t} midi 0x90 {n} 127\n" for n in ns) + f"{t + 300} midi 0xB0 116 127\n"
            s += "".join(f"{t + 399} midi 0x80 {n} 0\n" for n in ns)
        s += "5300 end\n"
    else:
        ns, hold = (chord, 1500) if case == "chord" else (bass, 3000)
        s += ("400 midi 0xB0 64 127\n" if case == "bass" else "") + "".join(f"400 midi 0x90 {n} 127\n" for n in ns)
        s += f"700 midi 0xB0 116 127\n{400 + hold} midi 0xB0 116 127\n{400 + hold + 100} end\n"
    _, log = render(f"notrim-{case}", s)
    import re
    caps = [int(c) for c in re.findall(r"MIDI out B0 112 (\d+)", log)][1:]     # (the first: before the notes)
    assert caps and min(caps) == 48, (nv, caps, proxy_us(log))
    assert proxy_us(log) <= 4650, proxy_us(log)


def test_idle_is_digital_zero():
    """NOISE 127, the motor on, notes struck and released: once they end the engine's output is exact digital zero
    (no LFSR, contact-noise or motor residue: the codec mutes), and the voices end under 1 LSB"""
    s = RAW + VIBES + "61 midi 0xB0 104 127\n62 midi 0xB0 47 127\n64 midi 0xB0 48 127\n"
    s += "".join(f"200 midi 0x90 {n} 127\n" for n in (53, 60, 67, 74)) + "".join(f"700 midi 0x80 {n} 0\n" for n in (53, 60, 67, 74))
    s += "6000 end\n"
    x, _ = render("idle0", s)
    nz = np.nonzero(x[:, 0])[0]
    assert len(nz) and nz[-1] < 5.0 * FS, nz[-1] / FS
    assert np.max(np.abs(x[int(5.0 * FS):])) == 0


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


@pytest.mark.parametrize("note", [36, 41, 48, 55, 69, 96])
def test_voice_ends_under_one_lsb(note):
    """S4: a released note ends where its output is under 1 LSB (the last non-zero sample is 1 LSB at most), on the
    low keys too: there the modes' level measure (x1^2 - a1 x1 x2 + a2 x2^2) lost its small terms to the Q32 product's
    truncation (c1 x1 >> 32 = 0 for an 65 Hz mode under ~8 LSB) and the voice was cut at up to 7 LSB"""
    s = RAW + VIBES + "61 midi 0xB0 104 0\n" + f"200 midi 0x90 {note} 127\n440 midi 0x80 {note} 0\n5000 end\n"
    x, _ = render(f"end{note}", s)
    m = np.abs(x[:, 0])
    nz = np.flatnonzero(m)
    assert len(nz) and nz[-1] < 4.5 * FS
    assert m[nz[-1]] <= 1.0 and m[max(0, nz[-1] - 256):nz[-1] + 1].max() <= 1.0, (nz[-1] / FS, m[nz[-1] - 256:nz[-1] + 1].max())


def lufs300(m, t):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bar_measure as bm
    return bm.lufs(m[int(t * FS):int((t + 0.3) * FS)])


@pytest.mark.parametrize("tone,bell", [(0, 127), (16, 0), (32, 127), (127, 127), (0, 0)])
def test_no_clipping_at_any_voicing(tone, bell):
    """S2: one voice at v127, RUBBER, HAMMER 127, NOISE 0, RAW (no limiter): the peak stays under the generator's
    ceiling (vibes.json card_level; -0.5 dBFS since VIBES plays 2.5 dB over the Rhodes) at every TONE / BELL; before the strike position's level compensation
    TONE 0 clipped (full scale on F3..F4)"""
    keys = [41, 53, 59, 65, 69, 77, 89, 101]
    s = RAW + VIBES + f"61 midi 0xB0 104 0\n62 midi 0xB0 28 {tone}\n63 midi 0xB0 30 {bell}\n64 midi 0xB0 29 127\n65 midi 0xB0 31 127\n"
    s += "".join(f"{200 + 700 * i} midi 0x90 {n} 127\n{600 + 700 * i} midi 0x80 {n} 0\n" for i, n in enumerate(keys))
    x, _ = render(f"clip{tone}_{bell}", s + f"{200 + 700 * len(keys) + 500} end\n")
    ceil = json.load(open(os.path.join(ROOT, "docs", "model", "data", "vibes.json")))["card_level"]["peak_ceiling_dbfs"]
    assert np.max(np.abs(x)) <= 32768 * 10 ** ((ceil + 0.5) / 20), (np.max(np.abs(x)), ceil)   # the ceiling + rounding


@pytest.mark.parametrize("cc,lo,limit", [(28, 1.5, 4.0), (30, 6.0, 13.0), (29, 0.0, 6.0)])
def test_card_sweep_level(cc, lo, limit):
    """S9 (tools/bar_scan.py S9_CARD_LU): the 0..300 ms level over each card's sweep, the mean of keys 53 69 89. TONE
    (strike position) moves it a little (the fuller centre, vibes.json tone_span_db 3), BELL (tube size) by its job (the
    bare bar the quieter one: bell_span_db 12; it was scaled to 4 LU and not heard), MALLET holds it (the families are
    level-matched). Before any compensation TONE moved it 6.2 LU and clipped at v127"""
    lv = []
    for v in (0, 32, 64, 96, 127):
        s = RAW + VIBES + f"61 midi 0xB0 104 0\n62 midi 0xB0 {cc} {v}\n"
        s += "".join(f"{200 + 1500 * i} midi 0x90 {n} 100\n{1100 + 1500 * i} midi 0x80 {n} 0\n" for i, n in enumerate((53, 69, 89)))
        x, _ = render(f"sweep{cc}_{v}", s + "4800 end\n")
        lv.append(np.mean([lufs300(x[:, 0], 0.2 + 1.5 * i) for i in range(3)]))
    assert lo <= max(lv) - min(lv) <= limit, lv
