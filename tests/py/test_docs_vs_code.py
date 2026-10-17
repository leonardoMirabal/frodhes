# SPDX-License-Identifier: GPL-3.0-only
"""The manual against the code: every MIDI CC the firmware handles is in the manual's CC table, the ASSIGN list is
PERF_T's, the factory preset table is PR_FNAME's. Added 2026-10-17 after the docs missed the polyphony, four CCs and
four ASSIGN choices: the docs are tested like the code."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = os.path.join(ROOT, "firmware", "src")
MANUAL = open(os.path.join(ROOT, "docs", "MANUAL.md")).read()


def src(name):
    return open(os.path.join(SRC, name)).read()


def section(title):
    i = MANUAL.index(title)
    j = MANUAL.find("\n## ", i + len(title))
    return MANUAL[i:j if j > 0 else len(MANUAL)]


def code_ccs():
    """every CC number the firmware reacts to: PARAMS[].cc, and the explicit ones in frodhes.c midi_cc"""
    ccs = {int(c) for c in re.findall(r'\[P_\w+\] = \{"[^"]*", \d+, \d+, F_\w+, (\d+)', src("params.c"))}
    body = src("frodhes.c")
    body = body[body.index("static void midi_cc("):]
    body = body[:body.index("\n}\n")]
    ccs |= {int(c) for c in re.findall(r"cc == (\d+)u", body)}
    for a, b in re.findall(r"cc >= (\d+)u && cc <= (\d+)u", body):
        ccs |= set(range(int(a), int(b) + 1))
    return {c for c in ccs if c != 0}                 # 0: "no CC"


def manual_ccs():
    """the CC numbers listed in the MIDI section's table (first and third columns: "86 / 87", "28-31", "12 / 13")"""
    got = set()
    for line in section("## 12. MIDI").splitlines():
        if not line.startswith("| ") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        for cell in cells[0::2]:
            for a, b in re.findall(r"(\d+)\s*[–-]\s*(\d+)", cell):
                got |= set(range(int(a), int(b) + 1))
            got |= {int(n) for n in re.findall(r"\d+", re.sub(r"\d+\s*[–-]\s*\d+", "", cell))}
    return got


def test_every_cc_is_in_the_manual():
    missing = sorted(code_ccs() - manual_ccs())
    assert not missing, f"CCs the firmware handles but the manual's CC table does not list: {missing}"


def test_assign_list_matches_perf_t():
    names = re.findall(r'\{[PS]_\w+, "([^"]+)"', src("ui.c")[src("ui.c").index("PERF_T[] = {"):])
    names = names[:names.index("LFO RATE") + 1]
    para = section("### ASSIGN")
    para = para[para.index("Each knob can play any of:"):para.index("The\nchoice")]
    listed = [n.strip().rstrip(".") for n in re.split(r",|\band\b", para.split(":", 1)[1].replace("\n", " ")) if n.strip()]
    assert listed == names, (listed, names)


def test_factory_presets_listed():
    names = re.findall(r'"([^"]+)"', src("presets.c")[src("presets.c").index("PR_FNAME[PR_NF] = {"):].split("};")[0])
    table = section("## 6. Presets")
    missing = [f"F{i + 1:02d} {n}" for i, n in enumerate(names) if f"F{i + 1:02d} {n}" not in table]
    assert not missing, missing


def test_manual_links_resolve():
    """every in-page link (#anchor) points at a heading that exists (GitHub's anchor rule: lower case, spaces to -,
    punctuation dropped); sections were renumbered when VIBES got its own (5)"""
    heads = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
             for h in re.findall(r"^#{2,4} (.+)$", MANUAL, re.M)}
    bad = sorted({a for a in re.findall(r"\]\(#([^)]+)\)", MANUAL) if a not in heads})
    assert not bad, bad
