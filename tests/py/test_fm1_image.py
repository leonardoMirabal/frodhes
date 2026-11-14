"""fm1_ports.image / lat_half / image_voices: every FM-1 tool's limits and voice count follow the image under test,
the two-core FM-1 package by default (0.9.12: storm soaks and the CPU gate ran on the 256-frame limits and 10 voices
when FRODHES_LAT_HALF was not set)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import fm1_ports  # noqa: E402


@pytest.mark.parametrize("env, img, half, voices", [
    ({}, "c1", 128, 14),
    ({"FRODHES_IMAGE": "c1"}, "c1", 128, 14),
    ({"FRODHES_IMAGE": "one"}, "one", 256, 10),
    ({"FRODHES_IMAGE": "c1", "FRODHES_LAT_HALF": "64"}, "c1", 64, 14),
])
def test_image_defaults(monkeypatch, env, img, half, voices):
    for k in ("FRODHES_IMAGE", "FRODHES_LAT_HALF"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert (fm1_ports.image(), fm1_ports.lat_half(), fm1_ports.image_voices(10)) == (img, half, voices)


def test_bad_image_refused(monkeypatch):
    monkeypatch.setenv("FRODHES_IMAGE", "two")
    with pytest.raises(SystemExit):
        fm1_ports.image()


def test_tools_take_the_image():
    """no tool reads FRODHES_LAT_HALF with its own 256 default"""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
    for t in ("hil_cpu.py", "hil_crackle.py", "hil_c1_soak.py"):
        src = open(os.path.join(root, t)).read()
        assert 'FRODHES_LAT_HALF", "256"' not in src, t
        assert "fm1_ports.lat_half()" in src, t
