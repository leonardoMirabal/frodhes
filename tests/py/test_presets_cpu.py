# SPDX-License-Identifier: GPL-3.0-only
"""Every factory preset (presets.c PR_FDATA) and every user-bank starter (PR_SDATA) within 4200 us of whole-ISR CPU,
predicted by tools/cpu_budget.py from the FM-1's measured costs: the preset's engine at its firmware voice count, the
USB stream at 48 kHz, the pitch bend when the firmware has it, only the effects the preset switches on, and a drive
TYPE other than FDRV at the drive's whole budget (drive.c DRV_US_MAX) in place of FDRV's share. Also: the starters'
names, ids and values are what the firmware can load."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import cpu_budget as cb  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = os.path.join(ROOT, "firmware", "src")
PRESET_US = 4200
REV_US_LOCUT = 16          # the reverb's LOW CUT (rev_types.c REV_US_LOCUT; tools/cpu_costs.json rev_type_us)


def read(f):
    return open(os.path.join(SRC, f)).read()


def params():
    """{name: (hi, def)} from params.c PARAMS[]"""
    return {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"\[(P_[A-Z0-9_]+)\]\s*=\s*\{\"[^\"]*\",\s*(\d+),\s*(\d+),", read("params.c"))}


def records(name):
    """the (name, value) lists of a presets.c table: PR_FDATA (a row each) or PR_SDATA (0xFF ends each)"""
    s = read("presets.c")
    body = re.search(name + r"[^=]*=\s*\{(.*?)\n\};", s, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S).replace("{", " ").replace("}", " ")
    for m in re.finditer(r"#define (PR_C[A-Z]) ([^\n/]*)", s):       # the compressor macros (PR_CK, PR_CM)
        body = re.sub(r"\b" + m.group(1) + r"\b", m.group(2).strip(), body)
    toks = [t.strip() for t in body.split(",") if t.strip()]
    out, cur = [], []
    for t in toks:
        if t == "0xFF":
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    return [dict(zip(r[0::2], (int(v) for v in r[1::2]))) for r in out]


def drv_max():
    return int(re.search(r"#define DRV_US_MAX (\d+)u", read("drive.c")).group(1))


def predict(rec, P, T):
    v = {k: d for k, (h, d) in P.items()}
    v.update(rec)
    eng = cb.ENGINES[0] if v["P_TYPE"] <= 3 else cb.ENGINES[1] if v["P_TYPE"] == 4 else cb.ENGINES[2]
    on = [k for k, c in (("comp", v["P_CMP_SW"]), ("filter", v["P_FL_FREQ"] < 127 or v["P_FL_LFO"]),
                         ("drive", v["P_DRIVE"]), ("trem_wah", v["P_TW_SW"]), ("phaser", v["P_PH_SW"]),
                         ("chorus", v["P_CP_SW"]), ("delay", v["P_DL_SW"]), ("reverb", v["P_RV_DEPTH"])) if c]
    rv = ("hall", "hall", "plate", "infinite")[v["P_RV_TYPE"]]      # the reverb TYPE (OFF: no reverb at all)
    if not v["P_RV_TYPE"] and "reverb" in on:
        on.remove("reverb")
    p = cb.predict(T, eng, cb.firmware_voices()[eng], "48000", fx=on, bend=cb.firmware_has_bend(), reverb=rv)
    extra = max(0, drv_max() - cb.fx_share(T, ["drive"])) if v["P_DRIVE"] and v["P_DRV_SW"] >= 2 else 0
    extra += REV_US_LOCUT if "reverb" in on and v["P_RV_LOCUT"] else 0
    return p["whole"] + extra, on


def test_every_preset_within_4200us():
    P, T = params(), cb.load()
    worst = []
    for tab, lab in (("PR_FDATA", "F"), ("PR_SDATA", "U")):
        for i, r in enumerate(records(tab)):
            us, on = predict(r, P, T)
            worst.append((us, f"{lab}{i + 1:02d}", on))
    over = [w for w in worst if w[0] > PRESET_US]
    assert not over, over
    assert len([w for w in worst if w[1][0] == "F"]) == 19


def test_starters_loadable():
    P = params()
    s = read("presets.c")
    ns = int(re.search(r"#define PR_NS (\d+)u", s).group(1))
    names = re.findall(r'"([^"]*)"', re.search(r"PR_SNAME\[PR_NS\] = \{(.*?)\};", s, re.S).group(1))
    recs = records("PR_SDATA")
    assert len(names) == len(recs) == ns
    assert all(0 < len(n) <= 10 and n == n.upper() for n in names), names    # the header: "U01 " + 10 characters
    fnames = re.findall(r'"([^"]*)"', re.search(r"PR_FNAME\[PR_NF\] = \{(.*?)\};", s, re.S).group(1))
    assert not set(names) & set(fnames), "a starter named as a factory preset"
    for n, r in zip(names, recs):
        assert r.get("P_TYPE", 0) <= 3, n                          # Rhodes starters
        assert "P_LEVEL" in r, n                                   # each one levelled (tools/hil_scan.py presets)
        for k, val in r.items():
            assert k in P and val <= P[k][0], (n, k, val)
