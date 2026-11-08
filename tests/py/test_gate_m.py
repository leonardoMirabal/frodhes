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
    assert gate_m.classify(s).startswith("the FM-1 sequencer / arpeggiator / chords (CC 52, 56, 63)")


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


def test_engines_of(tmp_path):
    """--update's guard: the engines an entry's notes sound on, by its script"""
    s = tmp_path / "a.txt"
    s.write_text("10 midi 0x90 60 100\n20 midi 0x80 60 0\n50 end\n")
    assert gate_m.engines_of(s) == {"rhodes"}
    s.write_text("5 midi 0xB0 79 0\n10 midi 0x90 60 100\n50 end\n")
    assert gate_m.engines_of(s) == {"vibes"}
    s.write_text("5 midi 0xB0 79 127\n10 midi 0x90 60 0\n12 midi 0x90 61 90\n20 midi 0xB0 80 40\n50 end\n")
    assert gate_m.engines_of(s) == {"marimba"}                  # (a note-on at velocity 0 is a note-off)
    s.write_text("5 midi 0xB0 79 0\n10 midi 0x90 60 100\n20 midi 0xB0 80 40\n30 midi 0x90 62 100\n50 end\n")
    assert gate_m.engines_of(s) == {"vibes", "rhodes"}
    s.write_text("5 midi 0xC0 3 0\n10 midi 0x90 60 100\n50 end\n")
    assert gate_m.engines_of(s) == {"preset"}
    assert gate_m.engines_of(None) == {"rhodes"}


def test_update_guard():
    """only entries sounding an allowed engine may change; a preset may be any engine; fx / all allow every entry"""
    assert gate_m.allowed({"rhodes"}, ["rhodes"])
    assert not gate_m.allowed({"vibes"}, ["rhodes"])
    assert not gate_m.allowed({"marimba"}, ["rhodes", "vibes"])
    assert gate_m.allowed({"vibes", "rhodes"}, ["rhodes"])
    assert gate_m.allowed({"preset"}, ["rhodes"])
    assert gate_m.allowed({"marimba"}, ["fx"])


def test_bar_entries_are_guarded():
    """the guard reads the bar engines' effect renders as bars only, so a Rhodes change cannot move them unseen, and
    finds the bars in their own scripts (some end on a Rhodes note: vibes_motor, the swaps)"""
    midi, _ = gate_m.entries()
    eng = {n: gate_m.engines_of(gate_m.ROOT / s) for n, s in midi}
    for n, e in eng.items():
        if n.startswith("fx/m_"):
            assert e == {"marimba"}, (n, e)
        elif n.startswith("fx/v_"):
            assert e == {"vibes"}, (n, e)
        elif n.startswith("scripts/vibes"):
            assert "vibes" in e, (n, e)
        elif n.startswith("scripts/marimba"):
            assert "marimba" in e, (n, e)
    assert sum(1 for e in eng.values() if e <= {"vibes", "marimba"}) >= 36 + 4
