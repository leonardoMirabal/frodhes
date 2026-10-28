"""Gate M's classification and its baseline file (M1-10), without a build: tools/gate_m.py --list's logic."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate_m  # noqa: E402


def test_classify(tmp_path):
    s = tmp_path / "a.txt"
    s.write_text("# x\n10 midi 0x90 60 100\n20 expect rms > -40\n30 shot\n40 dump\n50 end\n")
    assert gate_m.classify(s) is None
    s.write_text("10 midi 0x90 60 100\n20 btn 3\n30 turn 1 2\n50 end\n")
    assert gate_m.classify(s) == "FM-1 input lines: btn, turn"
    assert gate_m.classify(None).startswith("the default run")
    s.write_text("10 midi 0x90 60 100\n20 midi 0xB0 63 127\n30 midi 0xB0 52 1\n40 midi 0xB0 56 2\n50 end\n")
    assert gate_m.classify(s).startswith("the FM-1 sequencer / arpeggiator (CC 52, 63)")   # (CC 56: the chords stay)


def test_move16_matches_the_list():
    """every pinned 16-voice render is a MIDI-driven entry with a 10-voice baseline; the host-only list is current"""
    midi, host = gate_m.entries()
    names = {n for n, _ in midi}
    base = gate_m.read_sums(gate_m.BASE)
    pinned = gate_m.read_sums(gate_m.BASE16)
    assert set(pinned) <= names
    assert all(n in base and pinned[n] != base[n] for n in pinned)
    listed = {ln.split()[2].rstrip(":") for ln in gate_m.BASE16.read_text().splitlines()
              if ln.startswith("# host-only ")}
    assert listed == set(host)
    assert names | set(host) == set(base)
