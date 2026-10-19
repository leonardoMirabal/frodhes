# SPDX-License-Identifier: GPL-3.0-only
"""tools/mrb_ref_measure.py (MARIMBA PLAN D10, numbers only) on SYNTHETIC notes (no recording is read here): the pack
parser (an Ableton Sampler preset's zones), the in-place AIFF reader, the refusal of protected (non-PCM) pack audio,
and measure()'s numbers against a note whose f0, partials, levels and decays are known. Plus the device tools' dry
runs (vib_sampleback --engine marimba, hil_jack --engine marimba: the host build in the device's place)."""
import gzip
import json
import math
import os
import struct
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mrb_ref_measure as mr  # noqa: E402

FS = 48000
F0 = 261.6255653005986
PARTS = [(1.0, 0.0, 0.8), (4.0, -20.0, 0.30), (9.8, -30.0, 0.10)]     # (ratio, dB re V1, T60 s)


def note(dur=3.0, cents=+3.0):
    t = np.arange(int(dur * FS)) / FS
    f0 = F0 * 2 ** (cents / 1200)
    x = sum(10 ** (d / 20) * np.exp(-6.9078 * t / T) * np.sin(2 * np.pi * r * f0 * t) for r, d, T in PARTS)
    x *= np.minimum(1.0, t / 0.001)
    x += 10 ** (-95 / 20) * np.random.default_rng(1).standard_normal(len(t))
    return 0.5 * x / np.abs(x).max()


def ext80(v):
    e = int(math.floor(math.log2(v)))
    m = int(v / 2 ** e * (1 << 63))
    return struct.pack(">HQ", e + 16383, m)


def write_aiff(path, x, start, comp=None):
    """24-bit stereo AIFF (or AIFC with a compression id) with x at frame start"""
    y = np.zeros(start + len(x) + 1000)
    y[start:start + len(x)] = x
    v = np.round(np.clip(y, -1, 1 - 2 ** -23) * (1 << 23)).astype(np.int32)
    v = np.stack([v, v], 1).ravel()
    b = np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], 1).astype(np.uint8).tobytes()
    comm = struct.pack(">hIh", 2, len(y), 24) + ext80(FS) + ((comp + b"\x00\x00") if comp else b"")
    ssnd = struct.pack(">II", 0, 0) + b
    body = b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    open(path, "wb").write(b"FORM" + struct.pack(">I", 4 + len(body)) + (b"AIFC" if comp else b"AIFF") + body)


def write_preset(path, fname, root, start, end):
    xml = f"""<?xml version="1.0"?><Ableton><MultiSamplePart><Name Value="x" />
<KeyRange><Min Value="{root}" /><Max Value="{root}" /><CrossfadeMin Value="{root}" /><CrossfadeMax Value="{root}" /></KeyRange>
<VelocityRange><Min Value="62" /><Max Value="104" /><CrossfadeMin Value="66" /><CrossfadeMax Value="100" /></VelocityRange>
<RootKey Value="{root}" /><Detune Value="0" /><Volume Value="1.99526584" /><SampleStart Value="{start}" /><SampleEnd Value="{end}" />
<SampleRef><FileRef><Name Value="{fname}" /></FileRef><DefaultSampleRate Value="{FS}" /></SampleRef></MultiSamplePart></Ableton>"""
    with gzip.open(path, "wb") as f:
        f.write(xml.encode())


def pack(tmp, comp=None):
    os.makedirs(tmp / "Sounds" / "Marimba")
    os.makedirs(tmp / "Samples" / "Combined")
    x = note()
    write_aiff(str(tmp / "Samples" / "Combined" / "Test Marimba.aif"), x, 12345, comp)
    write_preset(str(tmp / "Sounds" / "Marimba" / "Test Marimba.adg"), "Test Marimba.aif", 60, 12345, 12345 + len(x))
    return str(tmp)


def test_pack_zone_read_in_place_and_measured(tmp_path):
    zones, rows, notes, infos = mr.measure_pack(pack(tmp_path), (36, 96))
    assert len(zones) == 1 and zones[0]["root"] == 60 and zones[0]["vel_full"] == (66, 100) and not notes
    r = rows[0]
    assert abs(r["cents"] - 3.0) < 0.3
    assert abs(r["v2_ratio"] - 4.0) < 0.003 and abs(r["v3_ratio"] - 9.8) < 0.01
    assert abs(r["t60_v1"] / 0.8 - 1) < 0.02                 # the wide band: unbiased
    assert 1.03 < r["t60_v1_30db"] / 0.8 < 1.09               # the gates' +-3 % band: ~6 % long at C4 (documented)
    assert abs(r["v2_t60"] / 0.30 - 1) < 0.10
    assert r["f0_peak_ms"] <= 10 and r["attack_ms"] < 2.0
    # the 5-300 ms energy ratio of two exponentials: (A2/A1)^2 x the ratio of their integrated energies
    def e(T):
        a = 2 * 6.9078 / T
        return (math.exp(-a * 0.005) - math.exp(-a * 0.3)) / a
    want = -20.0 + 10 * math.log10(e(0.30) / e(0.8))
    assert abs(r["v2_db"] - want) < 1.0
    assert mr.layer_for(rows, 100)["Test Marimba.adg"][60] is r


def test_protected_audio_is_not_read(tmp_path):
    zones, rows, notes, infos = mr.measure_pack(pack(tmp_path, comp=b"able"), (36, 96))
    assert len(zones) == 1 and not rows
    assert any("'able'" in n and "not read" in n for n in notes)
    with pytest.raises(mr.Protected):
        mr.aiff_read(str(tmp_path / "Samples" / "Combined" / "Test Marimba.aif"), 0, 10)


def test_plain_wav_named_with_its_note(tmp_path):
    import wave
    x = note()
    p = str(tmp_path / "Marimba C3.wav")                  # named an octave low: the octave check finds C4
    w = wave.open(p, "wb")
    w.setnchannels(1), w.setsampwidth(2), w.setframerate(FS)
    w.writeframes(np.round(x * 32767).astype("<i2").tobytes())
    w.close()
    rows = mr.measure_files([p], (21, 108))
    assert rows[0]["midi"] == 60 and abs(rows[0]["cents"] - 3.0) < 0.3


def run(args):
    r = subprocess.run([sys.executable] + args, cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert r.returncode in (0, 1), r.stderr[-2000:]
    return r


def test_sampleback_marimba_dry_run():
    r = run(["tools/vib_sampleback.py", "--engine", "marimba", "--dry-run", "--keys", "48-60", "--step", "12",
             "--round", "901"])
    assert "DRY RUN" in r.stdout and "| C3 |" in r.stdout and "| C4 |" in r.stdout
    assert "0 outside Gate VD-M" in r.stdout          # the host against itself (another latency): within the gate


def test_jack_marimba_dry_run():
    r = run(["tools/hil_jack.py", "--engine", "marimba", "--dry-run"])
    js = json.load(open(os.path.join(ROOT, "build", "hil", "dry", "jack-marimba.json")))
    assert js["dry_run"] and js["idle"]["usb_nonzero"] == 0 and js["idle_ok"]
    assert abs(js["st77_drift_db"]) <= 0.3
    # (the tail hovers at the gate's 16 LSB at its end: on e849a38 the gate ended it at 5.724 s, on P4's tables (C2
    #  retuned +9 c) the voice's own end did at 5.713 s, a sample over 16 LSB inside the gate's 200 ms; both pristine)
    assert js["tails"]["C2 RING 127 TUBE 127 pedal"]["kind"] in ("gate", "decayed")
    assert js["tails"]["C2 RING 127 TUBE 127 pedal"]["ok"] and js["tails"]["C2 RING 127 TUBE 127 pedal"]["usb_clicks"] == 0
    assert js["tails"]["C2 RING 127 TUBE 127 pedal"]["pre_fade_peak_lsb"] <= 16.5
    assert "SNR MARIMBA - ST77" in r.stdout
