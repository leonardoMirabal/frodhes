# SPDX-License-Identifier: GPL-3.0-only
"""rhodes.c rh_hd_flux (the harmonic decay glide's flux values, rh_hd_step) gives rh_hermite_i's integers at every gap,
the clamp's edge included, and rh_hd_step uses it (not the 64-bit form whose clamp the FM-1 faulted on: a predicated
64-bit load from the stack, 0.9.10 RC soak rows 276 / 277). tests/c/rh_hd_flux.c runs both, copied out of rhodes.c."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()


def body(name):
    m = re.search(r"^static inline __attribute__\(\(always_inline\)\) int32_t " + name + r"\(.*?^\}\n", SRC, re.S | re.M)
    assert m, name
    return m.group(0)


def test_hd_flux_same_integers(tmp_path):
    core = tmp_path / "core.h"
    defs = [re.search(r"^#define " + d + r" .*$", SRC, re.M).group(0) for d in ("RH_S_MAX_HI", "RH_HD_QMAX")]
    core.write_text("\n".join(defs) + "\n" + body("rh_hermite_i") + body("rh_hd_flux"))
    exe = tmp_path / "rh_hd_flux"
    subprocess.run(["cc", "-O2", "-Wall", "-fsanitize=undefined", "-fno-sanitize-recover", f'-DRH_HD_CORE="{core}"',
                    "-o", str(exe), os.path.join(ROOT, "tests", "c", "rh_hd_flux.c")], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"checked (\d+), clamped (\d+), differences 0", r.stdout)
    assert m and int(m.group(2)) > 1000, r.stdout


def test_hd_step_uses_hd_flux():
    m = re.search(r"^static FR_RAM __attribute__\(\(noinline\)\) int32_t rh_hd_step\(.*?^\}\n", SRC, re.S | re.M)
    assert m
    assert "rh_hd_flux(T, q[i])" in m.group(0) and "rh_hermite" not in m.group(0)
