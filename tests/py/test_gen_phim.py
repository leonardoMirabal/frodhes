# SPDX-License-Identifier: GPL-3.0-only
"""tools/gen_phim.py: firmware/gen/rhodes_phim.h is up to date with rhodes_tables.h (rerun the tool after
tools/gen_rhodes.py), it interleaves exactly RH_PHI / RH_SLOPE (rhodes.c's pickup reads p0, m0, p1, m1 from one
base), and the values stay in the range rh_hermite_u's c3 / c2 rewrite assumes (|Phi| < 2^29, |slope| < 2^23)."""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_phim  # noqa: E402


def test_header_up_to_date():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_phim.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_interleave_and_range():
    src = open(gen_phim.SRC).read()
    t = gen_phim.tables(src)
    (prow, pptr), (srow, sptr) = t["RH_PHI"], t["RH_SLOPE"]
    out = open(gen_phim.OUT).read()
    rows = {int(m.group(1)): [int(v) for v in m.group(2).split(",")]
            for m in re.finditer(r"RH_PHIM_(\d+)\[2 \* \(RH_PU_N \+ 1\)\] = \{([^}]*)\};", out)}
    ptr = [int(x) for x in re.findall(r"RH_PHIM_(\d+)", re.search(r"\*const RH_PHIM\[\d+\] = \{([^}]*)\}", out).group(1))]
    assert len(ptr) == len(pptr)
    for f, j in enumerate(ptr):
        assert rows[j][0::2] == prow[pptr[f]] and rows[j][1::2] == srow[sptr[f]]
    for r in rows.values():
        assert max(abs(v) for v in r[0::2]) < 1 << 29 and max(abs(v) for v in r[1::2]) < 1 << 23


def test_two_pickup_families():
    """S8: the 1/16" family (0, R = 0.5) and the 1/32" family (1, R = 1.0) are two distinct interleaved rows, and the
    keys <= 32 and >= 96 point at family 0 (params.json pickup.family0_keys)."""
    import json
    out = open(gen_phim.OUT).read()
    assert re.search(r"\*const RH_PHIM\[2\] = \{RH_PHIM_0, RH_PHIM_1\}", out)
    p = json.load(open(os.path.join(ROOT, "docs", "model", "data", "params.json")))
    assert p["pickup"]["R"] == [0.5, 1.0] and p["pickup"]["family0_keys"] == [[21, 32], [96, 108]]
    spec = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
    for k in spec["keys"]:
        assert k["fam"] == (0 if k["midi"] <= 32 or k["midi"] >= 96 else 1), k["midi"]
