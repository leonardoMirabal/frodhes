# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's windowed-sinc resampler (plugin/src/bridge.c rs_render: the rates frp_os.h does not take) holds its
passband to Gate D's band (0.95 x the cutoff) on loud top-octave content: tests/scripts/bar_cc_knob's 26-note burst
(MARIMBA, keys up to 108; key 105's 3.52 kHz fundamental sits under 8 kHz's 3648 Hz band edge) rendered at 8 and
22.05 kHz against the host build's 44.1 kHz render, Gate D's rates check (tools/gate_d.py band_snr: RMS <= 0.6 LSB,
peak <= 8 LSB). With the sinc's 24 zero crossings (until 0.9.12) 8 kHz was 67 LSB off: the kernel rolled off from
~3.4 kHz. Everything is built and written in pytest's tmp_path. Skipped when the plugin is not built."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
BUILD = os.environ.get("FRP_BUILD", os.path.join(ROOT, "build", "plugin"))
RENDER = os.path.join(BUILD, "frp_clap_render")
PLUGIN = os.path.join(BUILD, "bundles", "Frodhes.clap")
SCRIPT = os.path.join(ROOT, "tests", "scripts", "bar_cc_knob.txt")


@pytest.mark.skipif(not os.path.exists(RENDER) or not os.path.exists(PLUGIN), reason="the plugin is not built (make plugin)")
def test_sinc_rates_top_octave(tmp_path):
    import gate_d
    presets = tmp_path / "presets"
    presets.mkdir()
    env = {**gate_d.test_env("ms"), "FRODHES_PRESET_DIR": str(presets)}
    host = tmp_path / "host-hq"
    r = subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", *gate_d.DEFS, "-DHQ_DEFAULT=1", '-DAPP_VER="dev"',
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", str(host), os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    subprocess.run([str(host), str(tmp_path / "ref"), SCRIPT], capture_output=True, cwd=ROOT,
                   env={**os.environ, "FRODHES_PRESET_DIR": str(presets)})
    ref = tmp_path / "ref.wav"
    assert ref.exists()
    bad = []
    for rate in (8000, 22050):
        out = tmp_path / f"plug-{rate}.wav"
        p = subprocess.run([RENDER, PLUGIN, SCRIPT, str(out), "--engine", "hq", "--rate", str(rate)],
                           capture_output=True, text=True, cwd=ROOT, env=env)
        assert p.returncode == 0, (p.stdout + p.stderr)[-2000:]
        snr, rms, peak, band = gate_d.band_snr(ref, out)
        if not (rms <= 0.6 and peak <= 8.0):
            bad.append(f"{rate} Hz: error RMS {rms:.2f} LSB, peak {peak:.2f} LSB, SNR {snr:.1f} dB (0..{band:.0f} Hz)")
    assert not bad, "; ".join(bad)
