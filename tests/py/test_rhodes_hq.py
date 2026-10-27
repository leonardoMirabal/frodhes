# SPDX-License-Identifier: GPL-3.0-only
"""The HQ Rhodes (FR_HQ, the Move; private plan 06, firmware/src/hq/): parity with Classic, Gate H, the top keys.

The HQ voice is the Classic voice with the exact hammer contact at every velocity and, on keys >= 85, the pickup
at 2x. Both builds here are the Move's settings (16 voices, FR_HQ); HQ_DEFAULT says whether new notes play HQ."""
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
import gate_h  # noqa: E402

D = os.path.join(ROOT, "build", "hq_test")
DELAY = 31                                     # the 2x pickup's delay (rhodes_hq.c: 2P - 1)


def binary(hq):
    b = os.path.join(D, "hq" if hq else "classic")
    return hostrun.once(b, lambda p: gate_h.build(hq, pathlib.Path(p)))


def render(hq, script, tag):
    out = os.path.join(D, f"{tag}-{'hq' if hq else 'cl'}")
    subprocess.run([binary(hq), out, script], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    raw = w.readframes(w.getnframes())
    return raw, np.frombuffer(raw, dtype="<i2").astype(float).reshape(-1, 2)[:, 0]


def test_parity_below_the_2x_keys_at_the_layers():
    """keys 21..84 at the 8 layer velocities, every TYPE, the pedal: HQ is Classic, byte for byte"""
    s = os.path.join(ROOT, "tests/hq/hq_parity.txt")
    assert render(True, s, "parity")[0] == render(False, s, "parity")[0]


def test_gate_h():
    got = gate_h.sums(binary(True))
    want = {ln.split()[1]: ln.split()[0] for ln in gate_h.BASE.read_text().splitlines()
            if ln and not ln.startswith("#")}
    assert got == want, "Gate H differs: a change to the HQ sound needs tools/gate_h.py --save (and its reasons)"


def test_top_keys_same_level_band_limited(tmp_path):
    """keys 85..107 on STAGE 77 at the layer velocities 120 and 104 (the same pulse as Classic): the 2x pickup keeps
    each note's level within 0.1 dB, and differs from Classic only by the band the half-band takes away (the top
    of the spectrum and Classic's aliasing), at least 40 dB under the note"""
    t, L = 0, ["0 midi 0xB0 80 0"]
    notes = []
    for n in range(85, 109, 2):
        for v in (120, 104):
            L += [f"{t + 20} midi 0x90 {n} {v}", f"{t + 320} midi 0x80 {n} 0"]
            notes.append((n, v, t + 20)); t += 350
    L.append(f"{t + 300} end")
    s = tmp_path / "top.txt"
    s.write_text("\n".join(L) + "\n")
    _, c = render(False, str(s), "top")
    _, h = render(True, str(s), "top")
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
