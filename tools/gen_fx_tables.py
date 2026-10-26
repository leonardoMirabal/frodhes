#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Constant tables for the effects (IMPLEMENTATION-v3 §9): -> firmware/gen/fx_tables.h

  FX_SINE[1025]     Q15 sine over one cycle (+ guard), osc_sine()
  FX_TANH[257]      Q15 tanh over [-4, 4) in steps of 1/32, softclip()
  FX_LFO_INC[128]   Q32 phase steps per sample: 0.5 .. 12 Hz, exponential (TREM RATE, CHO/PHA SPEED)
  FX_TREM_LT[128]   the tremolo's amp law (SHAPE above 64): -log2 of the dip's floor per DEPTH, Q12 (the floor is the
                    linear law's 1 - depth, held at -30 dB)
  FX_TREM_MK[128]   its make-up gain per DEPTH, Q12: the same mean power as the linear law's (with its make-up)
  FX_DLY_T[128]     delay TIME in samples: 20 ms .. 1480 ms, exponential
  FX_PH_A[33]       phaser allpass coefficients (Q14) for corner frequencies 150 Hz .. 4.8 kHz (log), 33 points
  FX_WAH[64][3]     wah SVF rows (g Q14 for 300 Hz .. 2.5 kHz, log); the resonance k is applied at run time
  FX_CMP_*, FX_LOG2F, FX_EXP2F   the compressor's detector and gain computer
  FX_FL_G[258]      the FILTER's g = tan(pi fc / fs) in Q24 for fc 30 Hz .. 16 kHz (log, 256 steps + guards)
  FX_FL_K[128]      its damping k = 1 / Q in Q24: Q 0.707 .. 20 (RESO, exponential)
  FX_FL_HZ[128]     FREQ's value in Hz (the card)
  FX_DLY_WET[128]   the delay's echo gain per MIX (Q15)
  FX_DLY_FB[128]    DIG / ANA's regeneration per FDBK (Q15 of the half-level line: loop gain 2 fb / 32768)
  FX_TAPE_SAT[257]  the delay's TAPE: the record head + tape transfer curve over the line's +-65536 (steps of 512)
  FX_TAPE_HF[129]   its playback head's high-frequency loss: one-pole coefficients (Q15) of a two-pole cascade with its
                    -3 dB corner at 250 Hz * 2^(i / 16); and the TP_* constants (firmware/src/dly_tape.c)
"""
import math
import os

FS = 44100.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROM_LO, ROM_HI = 0xFFC00000, 0xFFCFFFFF


def arr(name, ctype, vals, per=16):
    for v in vals:
        assert not (ROM_LO <= (v & 0xFFFFFFFF) <= ROM_HI), (name, v)
    rows = [",".join(str(v) for v in vals[i:i + per]) for i in range(0, len(vals), per)]
    return f"static const {ctype} {name}[{len(vals)}] = {{\n  " + ",\n  ".join(rows) + "\n};\n"


# ---- the delay's TAPE (firmware/src/dly_tape.c): a single-head tape echo. TIME sets the tape speed (fixed head
# distance): T_REF is the delay at the reference speed, the speed moves +-S_MAX octaves around it.
TAPE = dict(
    T_REF=0.190,        # s: the delay at the reference speed: the middle (geometric) of a single-head unit's 60..600 ms
    S_MAX=1.66,         # octaves of speed range around it (60..600 ms; the tape speed of such units: 12..40 cm/s)
    F_BUMP=60.0,        # Hz: the playback head's low-frequency bump at the reference speed (moves with the speed)
    Q_BUMP=1.0,         # the 2nd-order high-pass's Q: a +1.25 dB bump (the loop's gain peaks there: the FDBK range)
    F_HF=6000.0,        # Hz: the -3 dB corner of the gap / spacing loss at the reference speed (moves with it)
    TONE_OCT=1.5,       # octaves the TONE knob moves that corner from 70 to 127 (the same rate below)
    TURNS=8.5,          # capstan turns per transit (record to playback head): its rate is TURNS / delay
    P_CAP=0.0010,       # the capstan's peak speed (pitch) deviation, its 2nd and 3rd harmonics
    P_CAP2=0.00035,
    P_CAP3=0.00043,
    F_DRIFT=1.0,        # Hz: the random drift's two low-passes (the speed drift's cutoff), and its rms deviation
    P_DRIFT=0.0008,
    F_DEPTH=0.3,        # Hz: the flutter depth's drift, and its rms (fraction of the depth)
    D_DEPTH=0.25,
    TAU_UP=1.89,        # s: the motor's speed follows TIME: a longer delay (slowing down), a shorter one
    TAU_DN=1.07,
    SAT_N=2.25,         # the transfer curve y = x / (1 + |x / SAT_X0|^N)^(1/N) (line units, int16 out): 3rd
    SAT_X0=26376.0,     # harmonic 0.18 % at the reference level (line peak 4096), 3 % at +11.5 dB
    WET_DB=-0.5,        # the echo's level against ANA (dB)
    FADE_MS=46.0,       # a TYPE switch: the fade out, then in
)


def tape_noise_rms(kn, km, steps=200000):
    """dly_tape.c tp_mod's noise: an LCG through two one-poles (kn) and one (km), integer as in C; their rms"""
    nz, n1, n2, n3, s2, s3, c = 0x2545F491, 0, 0, 0, 0.0, 0.0, 0
    for i in range(steps):
        nz = (nz * 1664525 + 1013904223) & 0xFFFFFFFF
        wn = (nz - (1 << 32) if nz >= 1 << 31 else nz) >> 16
        n1 += (((wn << 5) - n1) * kn) >> 16
        n2 += ((n1 - n2) * kn) >> 16
        n3 += (((wn << 5) - n3) * km) >> 16
        if i >= steps // 10:
            s2 += n2 * n2; s3 += n3 * n3; c += 1
    return math.sqrt(s2 / c), math.sqrt(s3 / c)


def DLY_OSC(f):
    """the loop gain in the self-oscillation zone (ANA, TAPE: FDBK 115..127)"""
    return 1.0 + 0.0025 * (f - 115)


def tape_tables():
    t, out, ctl = TAPE, [], 16
    sat = [x / (1 + abs(x / t["SAT_X0"]) ** t["SAT_N"]) ** (1 / t["SAT_N"]) for x in (-65536 + 512 * i for i in range(257))]
    out.append(arr("FX_TAPE_SAT", "int16_t", [max(-32767, min(32767, int(round(v)))) for v in sat]))
    hf = []
    for i in range(129):
        fp = 250 * 2 ** (i / 16) / math.sqrt(math.sqrt(2) - 1)     # each pole of the pair, for -3 dB at the corner
        hf.append(min(32767, int(round(32768 * (1 - math.exp(-2 * math.pi * fp / FS))))))
    out.append(arr("FX_TAPE_HF", "int16_t", hf))
    # FDBK -> the regeneration (Q15 of the full-level echo written to the half-level line: loop gain 2 fb / 32768 at
    # 1 kHz, where the head's response is ~1): at least ANA's at MIX 127 (0.0109 per step) up to 64, then up to a peak
    # loop gain (at the bump) of 0.97 at 119 (the tail ends), 0.995 at 120, 1.0 .. 1.03 at 121..127: a build-up held by the
    # saturation (the units self-oscillate at the top of INTENSITY)
    qb = t["Q_BUMP"]
    pk = qb / math.sqrt(1 - 1 / (4 * qb * qb)) if qb > 0.7072 else 1.0
    # (0.9.5, the user: the build-up from about FDBK 115, as ANA's: 0.97 at 113, 0.995 at 114, then 1.0 + 0.0025 a step:
    # 1.03 at 127 as before)
    def loop(f):
        if f <= 64:
            return 0.0109 * f
        if f <= 113:
            return 0.0109 * 64 + (0.97 / pk - 0.0109 * 64) * ((f - 64) / 49) ** 0.8
        return (0.995 if f == 114 else DLY_OSC(f)) / pk
    out.append(arr("FX_TAPE_FB", "uint16_t", [int(round(16384 * loop(f))) for f in range(128)], 12))
    # DIG / ANA (fx.c fx_delay_t, 0.9.5): FDBK alone sets the regeneration (it was MIX x FDBK: at MIX 64 the repeats died
    # fast), the loop gain 2 fb / 32768: as MIX 127 had it (0.0109 a step) up to 64, then to the audit's 0.85 at 114
    # (DIG and every tail stay there: their echoes always die); ANA from 115 above 1, a build-up held by its softclip
    def dly_fb(f):
        if f <= 64:
            return 0.0109 * f
        if f <= 114:
            return 0.0109 * 64 + (0.85 - 0.0109 * 64) * ((f - 64) / 50) ** 0.8
        return 1.03 * DLY_OSC(f)                   # (+3 %: the tone low-pass and the DC block's loss around the loop,
                                                    # ~0.97 at TONE 70: so ANA too builds up from about 115)
    # MIX (0.9.5, the user): the echo's gain (Q15), 0.875 at 64 (the first echo as loud as the eased dry, 32768 - 64^2),
    # linear below; then up to 32767 at 127 (+5.9 dB re the dry, as before)
    out.append(arr("FX_DLY_WET", "uint16_t", [d * 448 if d <= 64 else 28672 + (d - 64) * 65 for d in range(128)], 12))
    out.append(arr("FX_DLY_FB", "uint16_t", [min(13926, int(16384 * dly_fb(f))) if f <= 114 else
                                              int(round(16384 * dly_fb(f))) for f in range(128)], 12))
    q = t["Q_BUMP"]
    peak = q / math.sqrt(1 - 1 / (4 * q * q)) if q > 0.7072 else 1.0      # the 2nd-order high-pass's peak gain
    kn = 2 * math.pi * t["F_DRIFT"] * ctl / FS
    km = 2 * math.pi * t["F_DEPTH"] * ctl / FS
    # the rms of the two noise low-passes as dly_tape.c computes them (integer, over 10 minutes; recomputed here)
    rms2, rms3 = tape_noise_rms(int(round(kn * 65536)), int(round(km * 65536)))
    fc = t["TURNS"] / t["T_REF"]                                              # the capstan's rate at the reference
    amp = lambda p, f: int(round(p * FS / (2 * math.pi * f) * 4096))       # samples (Q12) for a peak pitch deviation
    d = dict(
        TP_LT_REF=int(round(math.log2(t["T_REF"] * FS) * 4096)), TP_S_MAX=int(round(t["S_MAX"] * 4096)),
        TP_KF_REF=int(round(2 * math.sin(math.pi * t["F_BUMP"] / FS) * 2 ** 24)), TP_KQ=int(round(16384 / q)),
        TP_LH_REF=int(round(math.log2(t["F_HF"] / 250) * 4096)), TP_TONE_K=int(round(t["TONE_OCT"] * 4096 / 57)),
        TP_INC1=int(round(fc * ctl / FS * 2 ** 32)),
        TP_A1=amp(t["P_CAP"], fc), TP_A2=amp(t["P_CAP2"], 2 * fc), TP_A3=amp(t["P_CAP3"], 3 * fc),
        TP_KUP=int(round(ctl / (t["TAU_UP"] * FS) * 65536)), TP_KDN=int(round(ctl / (t["TAU_DN"] * FS) * 65536)),
        TP_KN=int(round(kn * 65536)), TP_KM=int(round(km * 65536)),
        TP_DRIFT=int(round(t["P_DRIFT"] * 2 ** 24 * 65536 / rms2)), TP_DM=int(round(t["D_DEPTH"] * 2 ** 30 / rms3)),
        TP_FB_MAX=int(13926 / peak), TP_WET=int(round(4096 * 10 ** (t["WET_DB"] / 20))),
        TP_FADE=max(1, int(round(32768 / (t["FADE_MS"] * FS / 1000)))), TP_XBLK=max(1, int(round(t["FADE_MS"] * FS / 1000 / 256))),
    )
    return out + [f"#define {k} {v}" for k, v in d.items()] + [""]


def trem_tables():
    """the tremolo's amp law (fx.c fx_trem, SHAPE 65..127): the gain moves evenly in dB between unity and the floor
    T = max(1 - D, 1/32) (D = DEPTH x 258 / 32768: the linear law's own dip), g = T^((1 - s) / 2) for the LFO s; its
    make-up gives it the linear law's mean power (that law with its make-up (254 + DEPTH) / 254), so moving SHAPE
    into the amp region keeps the level"""
    lt, mk = [], []
    n = 4096
    sn = [math.sin(2 * math.pi * (i + 0.5) / n) for i in range(n)]
    for d in range(128):
        D = d * 258 / 32768
        t = max(1 - D, 1 / 32)
        lt.append(int(round(-math.log2(t) * 4096)))
        y = lt[-1] / 4096
        pl = sum(((1 - D * (1 - s) / 2) * (254 + d) / 254) ** 2 for s in sn) / n
        pe = sum((2 ** (-y * (1 - s) / 2)) ** 2 for s in sn) / n
        mk.append(int(round(4096 * math.sqrt(pl / pe))))
    return [arr("FX_TREM_LT", "uint16_t", lt, 16), arr("FX_TREM_MK", "uint16_t", mk, 16)]


def main():
    out = ["/* SPDX-License-Identifier: GPL-3.0-only */",
           "/* GENERATED by tools/gen_fx_tables.py - do not edit. */", ""]
    out.append(arr("FX_SINE", "int16_t", [int(round(32767 * math.sin(2 * math.pi * i / 1024))) for i in range(1025)]))
    out.append(arr("FX_TANH", "int16_t", [int(round(32767 * math.tanh(-4 + i / 32))) for i in range(257)]))
    out.append(arr("FX_LFO_INC", "uint32_t",
                   [int(round(0.5 * 24 ** (v / 127) / FS * 2 ** 32)) for v in range(128)], 8))
    out += trem_tables()
    out.append(arr("FX_DLY_T", "uint16_t", [min(65535 - 256, int(round(0.020 * 74 ** (v / 127) * FS))) for v in range(128)], 12))
    pa = []
    for i in range(33):
        f = 150 * (4800 / 150) ** (i / 32)
        t = math.tan(math.pi * f / FS)
        pa.append(int(round((t - 1) / (t + 1) * 16384)))       # first-order allpass a = (t - 1)/(t + 1), Q14
    out.append(arr("FX_PH_A", "int16_t", pa))
    wah = []
    for i in range(64):
        f = 300 * (2500 / 300) ** (i / 63)
        wah.append(int(round(2 * math.sin(math.pi * f / FS) * 16384)))   # Chamberlin SVF f coefficient, Q14
    out.append(arr("FX_WAH_F", "int16_t", wah))
    # knob laws (Q16): 2^((v-64)/64) and its inverse (BARK tip scale and gain compensation), 2^((v-64)/32) (BELL),
    # 2^(-(v-64)/128) (HAMMER: contact-rate multiplier, x2^(-0.5) .. x2^(+0.5))
    # (0.9.6, the flash: none of it stored. v >= 64 is FX_EXP2F(4 (v - 64)), v < 64 half of FX_EXP2F(4 v), rounded up
    # where the mask's bit v says: the same rounded numbers)
    p64 = [int(round(65536 * 2 ** ((v - 64) / 64))) for v in range(128)]
    e2 = [int(round(65536 * 2 ** (i / 256))) for i in range(256)]
    assert all(p64[64 + j] == e2[4 * j] for j in range(64))
    assert all(p64[j] in (e2[4 * j] >> 1, (e2[4 * j] >> 1) + 1) for j in range(64))
    rb = sum(1 << j for j in range(64) if p64[j] != e2[4 * j] >> 1)
    out.append("/* KN_P64(v) = 2^((v-64)/64) in Q16 (v 0..127), from FX_EXP2F (below; macros expand where used) */\n"
               f"#define KN_P64_RB(v) (((uint32_t)(v) < 32u ? 0x{rb & 0xFFFFFFFF:08X}u >> (uint32_t)(v) : "
               f"0x{rb >> 32:08X}u >> ((uint32_t)(v) - 32u)) & 1u)\n"
               "#define KN_P64(v) ((uint32_t)(v) >= 64u ? FX_EXP2F(((uint32_t)(v) - 64u) << 2) : "
               "(FX_EXP2F((uint32_t)(v) << 2) >> 1) + KN_P64_RB(v))\n")
    # (0.9.5, the flash: the inverse and BELL's law are not stored: 2^(-(v-64)/64) = KN_P64(128 - v) and 2^((v-64)/32)
    # = rhodes_tables.h DECAY_K[128 - v] for v >= 1, the same rounded numbers; tests/py/test_fx_tables_derived.py)
    out.append("/* KN_P64I(v) = 2^(-(v-64)/64), KN_P32(v) = 2^((v-64)/32) in Q16 (v 0..127): mirrors of KN_P64 and of DECAY_K\n"
               " * (rhodes_tables.h, included first), not stored */\n"
               "#define KN_P64I(v) ((v) ? KN_P64(128u - (uint32_t)(v)) : 131072u)\n"
               "#define KN_P32(v) ((v) ? DECAY_K[128u - (uint32_t)(v)] : 16384u)\n")
    # (0.9.6, the flash: stored less 32768 in 16 bits, 46341 .. 92682: the same numbers; KN_HAM(v) adds it back)
    ham = [int(round(65536 * 2 ** ((v - 64) / 128))) for v in range(128)]
    assert all(0 <= h - 32768 <= 65535 for h in ham)
    out.append(arr("KN_HAM_O", "uint16_t", [h - 32768 for h in ham], 8))
    out.append("#define KN_HAM(v) ((uint32_t)KN_HAM_O[v] + 32768u)\n")
    # NOISE knob: damper-thump force (Q15 re the v = 127 strike) and felt-noise level (Q15 output): -60 + 36 v/127 dB
    out.append(arr("KN_THUMP", "uint16_t", [0] + [int(round(32767 * 10 ** ((-60 + 36 * v / 127 - 6) / 20) * 40)) for v in range(1, 128)], 12))
    out.append(arr("KN_FELT", "uint16_t", [0] + [int(round(32767 * 10 ** ((-60 + 36 * v / 127 - 20) / 20))) for v in range(1, 128)], 12))
    # the key-down knock (S11, rhodes.c rh_knock_strike): the strike's force pulse into the mix at the NOISE level - 10 dB
    # (Q15 re full scale at the v = 127 strike's peak force)
    out.append(arr("KN_KNOCK", "uint16_t", [0] + [int(round(32767 * 10 ** ((-60 + 36 * v / 127 - 10) / 20))) for v in range(1, 128)], 12))
    # the pedal's thud (S12, rhodes.c rh_noise): its noise burst's gain at the NOISE level + 6 dB (Q15 re full scale;
    # the one-pole's output is about -20 dB re full scale rms): the thud's peak -33 dBFS at NOISE 127, -56 at 48
    out.append(arr("KN_PEDAL", "uint16_t", [0] + [int(round(32767 * 10 ** ((-60 + 36 * v / 127 + 6) / 20))) for v in range(1, 128)], 12))
    # compressor: detector coefficients (Q16, 1 - exp(-1/(t fs))): attack 0.1 .. 50 ms, release 20 ms .. 1.5 s;
    # log2(1 + i/256) in Q12 and 2^(i/256) in Q16 for the gain computer
    out.append(arr("FX_CMP_ATK", "uint16_t", [max(1, int(round(65536 * (1 - math.exp(-1 / (0.0001 * 500 ** (v / 127) * FS)))))) for v in range(128)], 8))
    out.append(arr("FX_CMP_REL", "uint8_t", [max(1, int(round(65536 * (1 - math.exp(-1 / (0.020 * 75 ** (v / 127) * FS)))))) for v in range(128)], 8))
    out.append(arr("FX_LOG2F", "uint16_t", [int(round(4096 * math.log2(1 + i / 256))) for i in range(256)], 16))
    # (0.9.6, the flash: stored less 65536 in 16 bits, the same numbers; FX_EXP2F(i) adds it back)
    out.append(arr("FX_EXP2F_O", "uint16_t", [int(round(65536 * 2 ** (i / 256))) - 65536 for i in range(256)], 8))
    out.append("#define FX_EXP2F(i) ((uint32_t)FX_EXP2F_O[i] + 65536u)\n")
    # drive make-up trim (Q15): the measured +0.09 dB per DRIVE step taken back, so DRIVE is level-neutral; and (0.9.3,
    # the drive's gain up to ~40x: it did nothing audible, TODO #4) the loudness its saturation adds, measured on the
    # host (A3 v80 / v110, A2, A4, a chord v110 / v70, LUFS, the mean) at these AMOUNT anchors, taken back too
    DRV_RISE = [(0, 0.0), (16, -0.71), (24, -0.48), (32, 0.11), (40, 0.9), (48, 1.77), (56, 2.62), (64, 3.39), (72, 4.04), (80, 4.55), (88, 4.91), (96, 5.11), (104, 5.15), (112, 5.03), (120, 4.76), (127, 4.39)]
    def drv_rise(v):
        for (a, ra), (b, rb) in zip(DRV_RISE, DRV_RISE[1:]):
            if a <= v <= b:
                return ra + (rb - ra) * (v - a) / (b - a)
        return DRV_RISE[-1][1]
    out.append(arr("FX_DRV_LVL", "uint16_t", [int(round(32767 * 10 ** ((-0.0896 * v - (drv_rise(v) if v > 16 else 0.0)) / 20))) for v in range(128)], 12))   # (<= 16: as before)
    # LEVEL (the preset's volume trim): Q12 gain, 64 = 0 dB, 0.1875 dB a step
    out.append(arr("FX_LVL", "uint16_t", [int(round(4096 * 10 ** ((v - 64) * 0.1875 / 20))) for v in range(128)], 12))
    # FILTER (a TPT state-variable filter, fx.c fx_filter): cutoff 30 Hz .. 16 kHz over 256 log steps
    fl = lambda i: 30 * (16000 / 30) ** (min(i, 256) / 256)
    out.append(arr("FX_FL_G", "uint32_t", [int(round(math.tan(math.pi * fl(i) / FS) * 2 ** 24)) for i in range(258)], 8))
    out.append(arr("FX_FL_K", "uint32_t", [int(round(2 ** 24 / (0.7071 * (20 / 0.7071) ** (v / 127)))) for v in range(128)], 8))
    out.append(arr("FX_FL_HZ", "uint16_t", [int(round(fl(v * 256 / 127))) for v in range(128)], 12))
    out += tape_tables()
    os.makedirs(os.path.join(ROOT, "firmware/gen"), exist_ok=True)
    open(os.path.join(ROOT, "firmware/gen/fx_tables.h"), "w").write("\n".join(out))


if __name__ == "__main__":
    main()
