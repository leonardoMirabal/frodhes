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


def test_cc79_row_matches_the_bar_type_bands():
    """the CC table's row 79 names every bar TYPE with its band (params.c TYPE_CC79 / TYPE_ENGINE: VIBES 0-95, MARIMBA
    96-127; MARIMBA PLAN §9.4)"""
    pc = src("params.c")

    def arr(name):
        return [int(x) for x in re.search(name + r"\[N_TYPE_N\] = \{([^}]*)\}", pc).group(1).split(",")]
    names = re.findall(r'"([^"]+)"', re.search(r"N_TYPE_LONG\[\] = \{([^}]*)\}", pc).group(1))
    bars = [t for t, e in enumerate(arr("TYPE_ENGINE")) if e == 1]
    lo = arr("TYPE_CC79")
    want = [f"{lo[t]}–{(lo[bars[i + 1]] - 1) if i + 1 < len(bars) else 127} {names[t]}" for i, t in enumerate(bars)]
    row = next(ln for ln in section("## 12. MIDI").splitlines() if ln.startswith("| 79 |"))
    assert ", ".join(want) in row, (want, row)


def test_cc84_row_matches_the_drive_type_bands():
    """the CC table's row 84 names every drive TYPE with its band, as the card shows it (params.c DRV_CC84 / N_DRV:
    0–63 OFF, 64–75 KHLON, ..., 112–127 FDRV), and the DRIVE page says the same"""
    pc = src("params.c")
    lo = [int(x) for x in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", pc).group(1).split(",")]
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", pc).group(1))
    order = sorted(range(len(lo)), key=lambda t: lo[t])
    want = [f"{lo[t]}–{lo[order[i + 1]] - 1 if i + 1 < len(order) else 127} {names[t]}" for i, t in enumerate(order)]
    row = next(ln for ln in section("## 12. MIDI").splitlines() if ln.startswith("| 84 |"))
    assert ", ".join(want) in row, (want, row)
    page = section("### DRIVE")
    page = page[:page.index("\n### ", 4)]
    assert ", ".join(want) in page.replace("\n", " "), want
    assert all(f"**{n}**" in page for n in names), names


def test_every_drive_type_is_described():
    """the DRIVE page's types table has a row per drive TYPE (params.c N_DRV, OFF aside) with its sound, AMOUNT and
    TONE, and the README's page table and DRIVE paragraph name every one"""
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", src("params.c")).group(1))[1:]
    page = section("### DRIVE")
    page = page[:page.index("\n### ", 4)]
    for n in names:
        row = next((ln for ln in page.splitlines() if ln.startswith(f"| **{n}** |")), None)
        assert row is not None, f"the DRIVE page has no row for {n}"
        assert len([c for c in row.strip("|").split("|") if c.strip()]) == 4, row
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert "| **DRIVE** | OFF / " + " / ".join(names) + " |" in readme
    para = readme[readme.index("The **DRIVE** page's K1"):]
    para = para[:para.index("\n\n")]
    assert all(f"**{n}**" in para for n in names), names


# the circuit names the drive TYPEs follow stay out of everything public (the user's rule: generic names only): the
# words are kept here only as sha256 prefixes: single words, and two names that may be written as two words (checked
# on adjacent words run together; only those two, as other pairs give false hits)
BRAND_PAIR_SHA = {"bbbfc2b63b885539", "8143ef07f80a4cfd"}
BRAND_SHA = {"fcce0f00a79c640e", "d7b45c44f4e11b8e", "68885fec0c7db0ea", "bbbfc2b63b885539", "6d7bc334f6901670",
             "6ca879b0e26d002e", "244a8ea0cab87c53", "8abec5966113c960", "8143ef07f80a4cfd", "98d9d59a907519c8",
             "d7b30cd38561a765"}


def test_no_circuit_brand_names_in_public_files():
    """README, docs/ (manual, release notes, fragments), the firmware's sources (the screen's strings) and the web
    emulator"""
    import hashlib
    import subprocess
    try:
        files = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True,
                               timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        files = []
    files = [f for f in files if f.endswith((".md", ".c", ".h", ".py", ".js", ".html", ".txt", ".json", ".sh"))
             and (f == "README.md" or f.startswith(("docs/", "firmware/src/", "web/")))]   # what users read and see
    files += [f for f in ("README.md", "docs/MANUAL.md") if f not in files]
    hits = []
    for f in files:
        try:
            words = re.findall(r"[a-z0-9]+", open(os.path.join(ROOT, f), errors="ignore").read().lower())
        except OSError:
            continue
        pairs = {hashlib.sha256((a + b).encode()).hexdigest()[:16] for a, b in zip(words, words[1:])}
        if pairs & BRAND_PAIR_SHA:
            hits.append(f)
            continue
        for w in set(words):
            if hashlib.sha256(w.encode()).hexdigest()[:16] in BRAND_SHA:
                hits.append(f)
                break
    assert not hits, f"a circuit's brand name in: {hits}"
