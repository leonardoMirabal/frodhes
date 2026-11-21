# SPDX-License-Identifier: GPL-3.0-only
"""The pitch bend's retune (rhodes.c rh_retune) is rewritten under FR_OWN (the plugin's own-code build): the FR_OWN=0 and
FR_OWN=1 host builds must render the same bytes. tests/hq/own_retune.txt (a Gate O entry) covers the bend under the
pedal, key-ups, DECAY / RELEASE changes and new notes; this test adds what the plugin's render does not take, TUNE moved
under held notes and under the bend ("tune" lines, host only), and checks that the bend did something in the take."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(ROOT)
import gate_d  # noqa: E402

TUNES = ((1200, -30), (2100, 25), (3200, 40), (3600, -50), (3850, 0))


def test_retune_equal_with_tune_moves(tmp_path):
    base = [d for d in gate_d.DEFS if not d.startswith("-DFR_OWN=")]
    bins = {v: gate_d.host_build(f"ownrt{v}", ["-DHQ_DEFAULT=1"], defs=base + [f"-DFR_OWN={v}"]) for v in (0, 1)}
    src = [ln for ln in open(os.path.join(ROOT, "tests/hq/own_retune.txt")) if ln.strip() and not ln.startswith("#")]
    ev = [(int(ln.split()[0]), ln.rstrip("\n")) for ln in src] + [(t, f"{t} tune {c}") for t, c in TUNES]
    ev.sort(key=lambda e: e[0])
    script = tmp_path / "own_retune_tune.txt"
    script.write_text("\n".join(e[1] for e in ev) + "\n")
    out = [gate_d.host_render(bins[v], f"own_retune_tune_{v}", str(script)) for v in (0, 1)]
    a, b = (o.read_bytes() for o in out)
    assert len(a) > 44 and a == b
    # the bend is in the take: the same script without its bend events renders differently
    nobend = tmp_path / "nobend.txt"
    nobend.write_text("\n".join(e[1] for e in ev if " 0xE0 " not in e[1]) + "\n")
    c = gate_d.host_render(bins[1], "own_retune_nobend", str(nobend)).read_bytes()
    assert c != b
