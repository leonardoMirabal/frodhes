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


# ---- K3: the 0.9.9 release's one-off Gate M DIFF (fx/rev127) while another process rewrote the shared build files
def test_gate_r_scripts_written_whole_and_only_when_changed(tmp_path):
    """jobs()' generated scripts: written once, then left alone (same text: no write, the file never truncated under
    a reader), and a change replaces the file whole (no temporary file left behind)"""
    import gate_r
    j = gate_r.jobs(tmp_path)
    p = j["fx/rev127"]
    assert Path(p).parent.parent == tmp_path and Path(p).read_text() == gate_r.fx_scripts()["fx/rev127"]
    ino = Path(p).stat().st_ino
    assert not gate_r.write_if_changed(p, Path(p).read_text())
    gate_r.jobs(tmp_path)
    assert Path(p).stat().st_ino == ino                     # (untouched: the same file, not rewritten)
    assert gate_r.write_if_changed(p, "10 end\n") and Path(p).read_text() == "10 end\n"
    assert Path(p).stat().st_ino != ino                     # (a new file renamed over: a reader keeps the old one)
    assert not list(tmp_path.rglob(".*.tmp"))


def test_gate_m_entries_from_its_own_scripts():
    """Gate M classifies the list from its own copy of the scripts (under ROOT: the replay list names them relative
    to it): the same entries as from Gate R's, the generated ones in its own folder"""
    import os
    import shutil
    own = ROOT / "build" / f"test_gate_m_scripts.{os.getpid()}"
    try:
        a = gate_m.entries(own)
        b = gate_m.entries()
        assert [n for n, _ in a[0]] == [n for n, _ in b[0]] and a[1] == b[1]
        fx = [s for n, s in a[0] if n.startswith("fx/")]
        assert fx and all((ROOT / s).resolve().is_relative_to(own.resolve()) for s in fx)
    finally:
        shutil.rmtree(own, ignore_errors=True)


def test_gate_m_snapshot_of_the_plugin(tmp_path):
    """the plugin replayed is a snapshot: the copy equals the source, a rebuild during the gate is seen (and the
    snapshot kept), a source that keeps changing while it is copied stops the gate, no dsp.so stops it"""
    src, dst = tmp_path / "frodhes", tmp_path / "so"
    src.mkdir()
    (src / "dsp.so").write_bytes(b"loader")
    (src / "engine.so").write_bytes(b"engine v1")
    snap = gate_m.snapshot(src, dst)
    assert (dst / "engine.so").read_bytes() == b"engine v1" and set(snap) == {"dsp.so", "engine.so"}
    assert gate_m.changed_since(snap, src) == []
    (src / "engine.so").write_bytes(b"engine v2")             # (another process: make move)
    assert gate_m.changed_since(snap, src) == ["engine.so"] and (dst / "engine.so").read_bytes() == b"engine v1"
    real = gate_m.sha
    n = [0]

    def moving(p):                                            # (every read of the source a new build)
        n[0] += 1
        return real(p) + (str(n[0]) if Path(p).parent == src else "")
    gate_m.sha = moving
    try:
        import pytest
        with pytest.raises(SystemExit, match="kept changing"):
            gate_m.snapshot(src, tmp_path / "so2")
    finally:
        gate_m.sha = real
    (src / "dsp.so").unlink()
    import pytest
    with pytest.raises(SystemExit, match="make move first"):
        gate_m.snapshot(src, tmp_path / "so3")
