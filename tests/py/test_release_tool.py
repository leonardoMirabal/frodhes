# SPDX-License-Identifier: GPL-3.0-only
"""tools/release.py without network or device: the version logic, the argument parsing, the HIL checks, the notes
assembled from docs/changes/ (nothing bumped in README / MANUAL), the verify step, and a whole --dry-run with git /
gh / make replaced by a fake shell (it must stop before any push)."""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release as rel  # noqa: E402


def test_next_version():
    assert rel.next_version("v0.9.2", "patch") == "0.9.3"
    assert rel.next_version("v0.9.2", "minor") == "0.10.0"
    assert rel.next_version("v0.9.2", "1.0.0") == "1.0.0"
    assert rel.next_version(None, "patch") == "0.0.1"
    for bad in ("0.9.2", "0.9.1", "1.0", "x"):
        with pytest.raises(ValueError):
            rel.next_version("v0.9.2", bad)


def test_parse_args():
    a = rel.parse_args([])
    assert (a.version, a.dry_run, a.no_device, a.no_emulator, a.yes) == ("patch", False, False, False, False)
    a = rel.parse_args(["minor", "--dry-run", "--no-device", "--no-emulator", "--yes"])
    assert (a.version, a.dry_run, a.no_device, a.no_emulator, a.yes) == ("minor", True, True, True, True)
    assert rel.parse_args(["0.9.7"]).version == "0.9.7"
    with pytest.raises(SystemExit):
        rel.parse_args(["0.9"])


def test_cpu_and_jack_checks():
    ok = "a   voices shed 0\nb   voices shed 0\n\nworst 4321 us with 9 voices (74 %); design target 4500 us"
    assert "4321" in rel.check_cpu("rhodes", ok)
    with pytest.raises(rel.Stop):
        rel.check_cpu("rhodes", ok.replace("4321", "4600"))
    with pytest.raises(rel.Stop):
        rel.check_cpu("vibes", ok.replace("shed 0\nb", "shed 2\nb"))
    with pytest.raises(rel.Stop):
        rel.check_cpu("vibes", "no output")
    assert "SNR 80.0" in rel.check_jack("rhodes", dict(loud=dict(jack_peak_db=-15.2), snr_db=80.0))
    with pytest.raises(rel.Stop):
        rel.check_jack("rhodes", dict(loud=dict(jack_peak_db=-60.0), snr_db=20.0))


def fake_shell(root, calls, notes_draft=False, changed=("docs/MANUAL.md", "firmware/src/usb.c")):
    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        calls.append(s)
        replies = {
            "git tag --sort=-v:refname --list v*": "v0.9.2\nv0.9.1\n",
            "git rev-parse --abbrev-ref HEAD": "main\n",
            "git status --porcelain --untracked-files=no": "",
            "git tag --list v0.9.3": "",
            "git ls-remote --tags origin refs/tags/v0.9.3": "",
            "git rev-parse HEAD": "abcdef1234567890\n",
            "git rev-list --count HEAD..origin/main": "0\n",
            "git rev-list --count origin/main..HEAD": "1\n",
            "git log --oneline origin/main..HEAD": "abcdef1 something\n",
            "git diff --name-only v0.9.2..HEAD": "".join(f + "\n" for f in changed),
        }
        if s.startswith("./build.sh --release"):
            os.makedirs(os.path.join(root, "build"), exist_ok=True)
            open(os.path.join(root, "build", f"frodhes-{cmd[-1]}.fwsc"), "wb").write(b"fw")
        return 0, replies.get(s, "1 passed\n")
    return sh


MANUAL = ("# Frodhes: user manual\n\n**A physically modelled electric piano and a test bar for the M-VAVE FM-1.**\n\n"
          f"1. Download [`frodhes.fwsc`]({rel.LATEST_DL}).\n")
FRAG = "## New\n- **A new thing.**\n  Its second line.\n\n## Fixed\n- A fix, as in 0.9.2.\n"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "changes").mkdir(parents=True)
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "a.png").write_bytes(b"png")
    h = rel.doc_links.short_hash(str(tmp_path / "docs" / "img" / "a.png"))
    (tmp_path / "README.md").write_text("Latest release: [the release page](https://github.com/leonardoMirabal/frodhes/releases/latest) "
                                        f"[`frodhes.fwsc`]({rel.LATEST_DL}) ![p](docs/img/a.png?v={h})\n")
    (tmp_path / "docs" / "MANUAL.md").write_text(MANUAL)
    (tmp_path / "docs" / "changes" / "b-thing.md").write_text(FRAG)
    (tmp_path / "docs" / "changes" / "a-other.md").write_text("## Changes\n- Another change.\n\n## Fixed\n- Fix A.\n")
    monkeypatch.setattr(rel, "ROOT", str(tmp_path))
    return tmp_path


def test_dry_run_stops_before_push(tree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("--dry-run must not prompt"))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"]) == 0
    o = capsys.readouterr().out
    assert not [c for c in calls if " push" in c or "tag -a" in c or "git commit" in c or "gh release" in c
                or "git rm" in c or "git add" in c]
    assert "make test" in calls and "./build.sh --release 0.9.3" in calls
    assert not (tree / "docs" / "releases" / "v0.9.3.md").exists()       # nothing is written in a dry run
    assert sorted(os.listdir(tree / "docs" / "changes")) == ["a-other.md", "b-thing.md"]
    assert "--no-device: the FM-1 is NOT tested" in o and "stopped before any push" in o
    assert "docs/changes/: 2 fragment(s), well formed (a-other.md, b-thing.md)" in o
    # the assembled notes, shown in full: head, sections in order, the fragments in file-name order, foot
    shown = o[o.index("-" * 30 + " docs/releases/v0.9.3.md"):]
    notes = "\n".join(l[3:] for l in shown.splitlines())                 # (the log indents by 3)
    assert "**Frodhes 0.9.3**: a physically modelled electric piano and a test bar for the M-VAVE FM-1." in notes
    assert "python3 tools/fm1_install.py frodhes.fwsc" in notes
    i = [notes.index(s) for s in ("### New in 0.9.3\n- **A new thing.**\n  Its second line.",
                                  "### Changes\n- Another change.", "### Fixed\n- Fix A.\n- A fix, as in 0.9.2.",
                                  "Full manual:")]
    assert i == sorted(i) and "### Tools" not in notes
    for line in (" 4  FM-1           skipped  --no-device", " 7  publish        skipped  --dry-run",
                 " 8  emulator       skipped  --no-emulator", " 3  host           done", " 5  docs           done"):
        assert line in o


def test_bad_fragment_hand_notes_and_existing_tag(tree, monkeypatch, capsys):
    (tree / "docs" / "changes" / "c.md").write_text("> **DRAFT**\n## New\n- x\n")
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1      # a real run stops at step 2
    o = capsys.readouterr().out
    assert "malformed fragment" in o and "docs/changes/c.md:1: a DRAFT line" in o
    assert " 3  host           not run" in o and not [c for c in calls if "make" in c or "push" in c]
    (tree / "docs" / "changes" / "c.md").unlink()
    (tree / "docs" / "releases" / "v0.9.3.md").write_text("notes by hand\n")
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1
    assert "never written by hand" in capsys.readouterr().out

    def tagged(cmd, **kw):
        s = " ".join(cmd) if not isinstance(cmd, str) else cmd
        return 0, ("v0.9.3\n" if s == "git tag --list v0.9.3" else "v0.9.2\n" if "sort" in s else "")
    monkeypatch.setattr(rel, "sh", tagged)
    assert rel.main(["patch", "--dry-run"]) == 1
    assert "v0.9.3 already exists locally" in capsys.readouterr().out


def test_no_fragments_stops(tree, monkeypatch, capsys):
    for f in os.listdir(tree / "docs" / "changes"):
        (tree / "docs" / "changes" / f).unlink()
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1
    assert "no release-note fragments in docs/changes/" in capsys.readouterr().out
    assert not (tree / "docs" / "releases" / "v0.9.3.md").exists()


def release_at_docs(tree, monkeypatch, calls, dry=False):
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    r = rel.Release(rel.parse_args(["patch", "--no-device"] + (["--dry-run"] if dry else [])))
    r.ver, r.prev, r.sha = "0.9.3", "v0.9.2", "f" * 64
    r.frags = rel.changes.load(str(tree))
    return r


def test_docs_step_writes_the_notes_and_removes_the_fragments(tree, monkeypatch):
    calls = []
    r = release_at_docs(tree, monkeypatch, calls)
    ev = r.s_docs()
    notes = (tree / "docs" / "releases" / "v0.9.3.md").read_text()
    assert notes.startswith("**Frodhes 0.9.3**") and "### New in 0.9.3" in notes and "Fix A." in notes
    assert "git rm -q docs/changes/a-other.md docs/changes/b-thing.md" in calls
    assert "git add docs/releases/v0.9.3.md" in calls and any(c.startswith("git commit") for c in calls)
    assert not [c for c in calls if c.startswith("git add") and ("README" in c or "MANUAL" in c)]   # nothing bumped
    assert "doc_links: README.md / docs/MANUAL.md, 0 problem(s)" in ev


def test_docs_step_refuses_stale_links_and_the_next_version(tree, monkeypatch):
    (tree / "docs" / "img" / "a.png").write_bytes(b"a new picture")          # its link's ?v= is now stale
    calls = []
    with pytest.raises(rel.Stop, match=r"README.md:1: docs/img/a.png\?v=[0-9a-f]{8}, the file's hash is"):
        release_at_docs(tree, monkeypatch, calls).s_docs()
    st = release_at_docs(tree, monkeypatch, calls, dry=True).s_docs()           # a dry run warns
    assert st[1] == "warn" and "PROBLEM: README.md:1" in st[0]
    rel.doc_links.fix(str(tree))
    (tree / "docs" / "changes" / "b-thing.md").write_text("## New\n- New in 0.9.3: a thing.\n")
    with pytest.raises(rel.Stop, match="names 0.9.3, the coming release"):
        release_at_docs(tree, monkeypatch, calls).s_docs()
    assert not [c for c in calls if c.startswith("git rm") or c.startswith("git commit")]


def verify_shell(assets, readme, draft=False):
    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if s.startswith("gh release view"):
            return 0, json.dumps({"assets": [{"name": a} for a in assets], "isDraft": draft, "url": "https://r/v0.9.3"})
        if s.startswith("gh api"):
            return 0, readme
        if s.startswith("git tag --sort"):
            return 0, "v0.9.3\nv0.9.2\n"
        return 0, ""
    return sh


def test_verify(monkeypatch, capsys):
    good = f"[`frodhes.fwsc`]({rel.LATEST_DL}) settings saved by 0.9.2 are read as they are"
    both = ["frodhes-0.9.3.fwsc", "frodhes.fwsc", *rel.SMK_ASSETS, rel.LICENCE_ASSET]
    tagged = "https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.3/frodhes.fwsc"
    hits = []

    def resolve(url):
        hits.append(url)
        return (tagged if url == rel.LATEST_DL else None), "a" * 64
    monkeypatch.setattr(rel, "resolve", resolve)
    r = rel.Release(rel.parse_args(["0.9.3"]))
    r.ver, r.sha = "0.9.3", "a" * 64
    monkeypatch.setattr(rel, "sh", verify_shell(both, good))
    assert "README at v0.9.3 names no version" in r.s_verify() and hits == [rel.LATEST_DL]
    for assets, readme, sha, msg in ((both[:1] + both[2:], good, "a" * 64, r"missing \['frodhes.fwsc'\]"),
                                     (both[:4] + both[5:], good, "a" * 64, r"missing \['frodhes-smk37-mke-experimental.fwsc'\]"),
                                     (both, good, "b" * 64, "the build's bbbb"),
                                     (both, good.replace(rel.LATEST_DL, "frodhes-0.9.3.fwsc"), "a" * 64,
                                      "versions named")):
        monkeypatch.setattr(rel, "sh", verify_shell(assets, readme))
        r.sha = sha
        with pytest.raises(rel.Stop, match=msg):
            r.s_verify()
    monkeypatch.setattr(rel, "resolve", lambda url: ("https://github.com/x/releases/download/v0.9.2/frodhes.fwsc",
                                                     "a" * 64))
    monkeypatch.setattr(rel, "sh", verify_shell(both, good))
    r.sha = "a" * 64
    with pytest.raises(rel.Stop, match="not to v0.9.3's frodhes.fwsc"):
        r.s_verify()
    # --verify-only: step 9 alone, the latest tag, the versioned asset as the reference; nothing else runs
    monkeypatch.setattr(rel, "resolve", resolve)
    hits.clear()
    assert rel.main(["--verify-only"]) == 0
    o = capsys.readouterr().out
    assert "(VERIFY-ONLY): summary" in o and " 9  verify         done" in o and " 3  host           skipped" in o
    assert hits == ["https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.3/frodhes-0.9.3.fwsc",
                    rel.LATEST_DL]


@pytest.mark.parametrize("changed, extra, need, why", [
    (["firmware/src/usb.c", "docs/MANUAL.md"], [], "T0 + T1", "T2: the map: no engine / DSP file changed"),
    (["firmware/src/ui.c"], [], "T0 + T1", "T2: the map: no engine / DSP file changed"),
    (["docs/MANUAL.md", "tools/hil_scan.py"], [], "T0 + T1", "T2: the map: no engine / DSP file changed"),
    (["firmware/src/rhodes.c", "firmware/src/ui.c"], [], "T0 + T1 + T2", "T2: the map: engine:rhodes changed"),
    (["firmware/src/ui.c"], ["--full"], "T0 + T1 + T2", "T2: --full (the map: no engine / DSP file changed)")])
def test_dry_run_lists_the_device_plan(tree, monkeypatch, capsys, changed, extra, need, why):
    """--no-device lists the tiers from the diff since the last tag (tools/test_map.py), why, and step 4's commands
    for them: T1 (hil_scan --tier must) on every release, T2 (today's full plan) only when the map or --full asks"""
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), [], changed=changed))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"] + extra) == 0
    o = capsys.readouterr().out
    assert f"tiers needed: {need} (git diff v0.9.2..HEAD, {len(changed)} file(s); T1 on every release; {why})" in o
    for f in changed:
        assert f in o[o.index("tiers needed:"):]                        # each file, with its group and why
    full = "T2" in need
    plan = rel.device_plan("0.9.3", ["T0", "T1"] + (["T2"] if full else []))
    for line in plan:
        assert line in o
    py = rel.PY
    t1 = [f"echo y | {py} tools/fm1_install.py build/frodhes-0.9.3.fwsc", f"{py} tools/hil_scan.py --tier must"]
    t2 = [f"{py} tools/hil_cpu.py --worst",
          f"{py} tools/hil_cpu.py --worst --engine vibes",
          f"{py} tools/hil_cpu.py --worst --engine marimba",
          f"{py} tools/hil_scan.py --only {rel.SCAN_SET}",
          f"{py} tools/hil_scan.py --only vibes_vel --source both",
          f"{py} tools/hil_scan.py --marimba --source both",
          f"{py} tools/hil_jack.py --engine rhodes",
          f"{py} tools/hil_jack.py --engine vibes",
          f"{py} tools/hil_jack.py --engine marimba",
          f"{py} tools/vib_sampleback.py --engine marimba"]
    assert [p.split("   (")[0] for p in plan] == t1 + (t2 if full else [])
    if not full:
        assert "hil_cpu.py --worst" not in o


def fake_fm1(hil, calls, changed):
    jack = dict(loud=dict(jack_peak_db=-15.0), snr_db=80.0)
    for n in ("jack.json", "jack-vibes.json"):
        (hil / n).write_text(json.dumps(jack))
    (hil / "jack-marimba.json").write_text(json.dumps(dict(marimba=dict(loud=dict(jack_peak_db=-15.3), snr_db=70.5),
                                                          snr_vs_st77_db=-3.8, tails={"C4": dict(ok=True)},
                                                          idle_ok=True, idle={})))

    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if s.startswith("git diff --name-only"):
            return 0, changed
        calls.append(s)
        if "hil_cpu" in s:
            return 0, "a   voices shed 0\n\nworst 3400 us with 9 voices"
        if "--marimba" in s:
            (hil / "marimba-report.json").write_text(json.dumps(m_report()))
            return 1, "FAIL  marimba_jump"                        # (exits 1 on any FAIL row: judged by the report)
        if "vib_sampleback" in s:
            return 0, "61 notes, 0 outside Gate VD-M."
        if "--tier must" in s:
            return 0, "T1 must-pass: 6 scenarios, every counter 0"
        return 0, "ok\n"
    return sh


@pytest.mark.parametrize("changed, full", [("firmware/src/bars.c\n", True), ("firmware/src/usb.c\n", False),
                                           ("docs/MANUAL.md\n", False)])
def test_device_step_runs_the_plan(tree, monkeypatch, changed, full):
    """s_device with a fake FM-1: T1 every time, then the T2 commands only when the diff needs them, in device_plan's
    order, and every check passes"""
    hil = tree / "build" / "hil"
    hil.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(rel, "sh", fake_fm1(hil, calls, changed))
    r = rel.Release(rel.parse_args(["0.9.3"]))
    r.ver, r.prev = "0.9.3", "v0.9.2"
    ev = r.s_device()
    need = ["T0", "T1"] + (["T2"] if full else [])
    assert calls == [p.split("   (")[0] for p in rel.device_plan("0.9.3", need)]
    assert ev.startswith(f"tiers needed: {' + '.join(need)}") and "T1 hil_scan --tier must: exit 0" in ev
    if full:
        assert "hil_cpu --worst marimba: worst 3400" in ev and "vib_sampleback marimba: 61 keys" in ev
        assert "hil_jack marimba: SNR 70.5" in ev and "every row ok" in ev
    else:
        assert "T2 not run (the map: no engine / DSP file changed; --full forces it)" in ev
        assert not [c for c in calls if "hil_cpu" in c or "hil_jack" in c]
    assert not (hil / "DEVICE.lockdir").exists()


def m_row(check, ok=True, known=None, setting=""):
    r = dict(check=check, setting=setting, usb="1", jack="1", crit="c", status="ok" if ok else "FAIL", ok=ok)
    if known:
        r.update(status="KNOWN", ok=True, known=known)
    return r


def m_report(**extra):
    s = {n: [m_row("x")] for n in rel.MARIMBA_MUST + rel.MARIMBA_FX}
    s["marimba_jump"] += [m_row("comp (-20 dB 4:1, MKUP 15): on vs off", ok=False),       # a stale effect limit
                          m_row("MARIMBA re ST77, A3-E4-A4 v100")]
    s["marimba_roll"].append(m_row("pumping", known="USER DECISION"))
    s["marimba_vel"].append(m_row("keys C2..C7: level span", ok=False))
    for n, rows in extra.items():
        s[n] = s[n] + rows
    return dict(suites=s, takes=[])


def test_marimba_scan_gate():
    ev = rel.check_marimba_scan(m_report())
    assert "1 KNOWN" in ev and "1 other row(s) failed (not gating)" in ev and "level span" in ev
    for suite, row in (("marimba_voice", m_row("STRIKE (CC 28) 0 vs 64", ok=False, setting="key 60 v100")),
                       ("marimba_fx", m_row("takes: full scale / clicks", ok=False)),
                       ("marimba_jump", m_row("drive (DRV ON, DRIVE 40): clicks at the switches", ok=False)),
                       ("marimba_jump", m_row("MARIMBA re ST77, A3-E4-A4 v100", ok=False)),
                       ("marimba_jump", m_row("FX bypass (CC 111) with everything on", ok=False)),
                       ("marimba_fx", m_row("suite error", ok=False))):
        with pytest.raises(rel.Stop, match="must pass"):
            rel.check_marimba_scan(m_report(**{suite: [row]}))
    rep = m_report()
    del rep["suites"]["marimba_decay"]
    with pytest.raises(rel.Stop, match="missing"):
        rel.check_marimba_scan(rep)


def test_marimba_jack_and_sampleback_checks():
    rep = dict(marimba=dict(loud=dict(jack_peak_db=-15.3), snr_db=70.5), snr_vs_st77_db=-3.8,
               tails={"C4": dict(ok=True)}, idle_ok=True, idle={})
    assert "SNR 70.5" in rel.check_jack_marimba(rep)
    for bad in (dict(tails={"C7 v8": dict(ok=False)}), dict(idle_ok=False),
                dict(marimba=dict(loud=dict(jack_peak_db=-60.0), snr_db=20.0))):
        with pytest.raises(rel.Stop):
            rel.check_jack_marimba(dict(rep, **bad))
    assert "61 keys, 0 outside" in rel.check_sampleback("...\n61 notes, 0 outside Gate VD-M. Recordings: x")
    for bad in ("61 notes, 2 outside Gate VD-M.", "Traceback"):
        with pytest.raises(rel.Stop):
            rel.check_sampleback(bad)


def test_device_done_needs_the_same_build(tmp_path, monkeypatch):
    """--no-device --device-done: the commit says the FM-1 tests ran on this exact build only when the sha256 matches
    (0.9.4's release commit read "NOT tested" although the full FM-1 scan had run on the same binary) and the tiers
    that ran cover the ones the diff needs"""
    changed = ["firmware/src/ui.c"]
    monkeypatch.setattr(rel, "sh", lambda cmd, **kw: (0, "".join(f + "\n" for f in changed)))
    j = tmp_path / "done.json"

    def release(tiers, sha="ab" * 32, extra=()):
        d = dict(sha256="ab" * 32, runs=["hil_scan --tier must", "hil_cpu"])
        if tiers is not None:
            d["tiers"] = tiers
        j.write_text(json.dumps(d))
        r = rel.Release(rel.parse_args(["0.9.9", "--no-device", "--device-done", str(j)] + list(extra)))
        r.sha, r.prev = sha, "v0.9.8"
        return r
    assert release(["T1"]).device_done() == ("FM-1: tested before the release on this exact build (sha256 equal), "
                                             "tiers T1 (needed T1): hil_scan --tier must; hil_cpu")
    assert "tiers T1+T2 (needed T1)" in release(["T1", "T2"]).device_done()
    with pytest.raises(rel.Stop, match="sha256"):
        release(["T1"], sha="cd" * 32).device_done()
    with pytest.raises(rel.Stop, match=r"tiers \(none recorded\) do not cover the needed \['T1'\]"):
        release(None).device_done()                                    # the old format: no tiers recorded
    with pytest.raises(rel.Stop, match=r"missing T2.*T2: --full"):
        release(["T1"], extra=["--full"]).device_done()
    changed[:] = ["firmware/src/rhodes.c"]
    with pytest.raises(rel.Stop, match=r"missing T2.*T2: the map: engine:rhodes changed"):
        release(["T1"]).device_done()
    assert "needed T1+T2" in release(["T1", "T2"]).device_done()
    assert rel.Release(rel.parse_args(["0.9.9", "--no-device"])).device_done() is None


def test_device_done_is_checked_at_step_4(tree, monkeypatch, capsys):
    """a dry run with --device-done checks it at step 4, once the build's sha256 is known (not skipped)"""
    import hashlib
    j = tree / "done.json"
    sha = hashlib.sha256(b"fw").hexdigest()                           # (the fake build writes b"fw")
    j.write_text(json.dumps(dict(sha256=sha, tiers=["T1"], runs=["hil_scan --tier must"])))
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), []))
    assert rel.main(["patch", "--dry-run", "--no-device", "--device-done", str(j), "--no-emulator"]) == 0
    o = capsys.readouterr().out
    assert " 4  FM-1           done     tiers needed: T0 + T1" in o and "tiers T1 (needed T1)" in o
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), [], changed=["firmware/src/fx.c"]))
    assert rel.main(["patch", "--dry-run", "--no-device", "--device-done", str(j), "--no-emulator"]) == 1
    o = capsys.readouterr().out
    assert " 4  FM-1           FAILED" in o and "missing T2" in o and " 5  docs           not run" in o


def test_no_session_links_in_the_repo():
    """the repo is public: no Claude session links or 'Generated with' footers in any tracked file (the user, 2026-10-20)"""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split("\n")
    bad = []
    for f in files:
        if not f or f == "CLAUDE.md":
            continue
        try:
            t = open(os.path.join(root, f), encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if "claude.ai/" + "code/session" in t or "Generated with [" + "Claude" in t:
            bad.append(f)
    assert not bad, bad


# ---- step 7: a green ci.yml run on a commit with the identical git tree is reused (no second ~38 min run) ----

M, Q, OTHER = "m" * 40, "a" * 40, "b" * 40      # main's merge commit M has the PR head Q's tree
TREE = "7" * 40
GREEN_JOBS = [{"name": "host", "conclusion": "success"}, {"name": "generators-macos", "conclusion": "success"}]


def gh_shell(trees, runs, views, calls):
    """git rev-parse <sha>^{tree} / gh api commits/<sha> answer from trees (local: only M), gh run list --status
    success answers runs, gh run view <id> answers views[id]; gh run watch must not be reached for a reused run"""
    def fake(cmd, check=True, input=None, timeout=None, quiet=False):
        calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            sha = cmd[-1].split("^")[0]
            return (0, trees[sha] + "\n") if sha == M else (128, "")
        if cmd[:2] == ["gh", "api"]:
            sha = cmd[2].rsplit("/", 1)[1]
            return (0, trees[sha] + "\n") if sha in trees else (1, "HTTP 404")
        if cmd[:3] == ["gh", "run", "list"]:
            assert "--status" in cmd and cmd[cmd.index("--workflow") + 1] == "ci.yml"
            return 0, json.dumps(runs)
        if cmd[:3] == ["gh", "run", "view"]:
            return 0, json.dumps(views[cmd[3]])
        pytest.fail(f"unexpected command {cmd}")
    return fake


def ci_row(i, sha, conclusion="success", status="completed"):
    return {"databaseId": i, "headSha": sha, "headBranch": "release-095", "status": status,
            "conclusion": conclusion, "event": "pull_request"}


def test_same_tree_ci_run_is_reused(monkeypatch, capsys):
    calls = []
    runs = [ci_row(5, OTHER), ci_row(4, Q)]
    views = {"4": {"conclusion": "success", "jobs": GREEN_JOBS, "url": "https://github.com/x/actions/runs/4"}}
    monkeypatch.setattr(rel, "sh", gh_shell({M: TREE, Q: TREE, OTHER: "8" * 40}, runs, views, calls))
    ev = rel.same_tree_run("ci.yml", M)
    assert ev and "runs/4: success on aaaaaaaaaa (pull_request, release-095)" in ev
    assert f"git tree {TREE[:10]} is identical to {M[:10]}'s" in ev and "reused" in ev
    assert "reused" in capsys.readouterr().out                       # printed: which run and why
    assert not any(c[:3] == ["gh", "run", "watch"] for c in calls)


def test_different_tree_waits_for_a_run_on_the_sha(monkeypatch):
    calls = []
    runs = [ci_row(4, Q)]
    monkeypatch.setattr(rel, "sh", gh_shell({M: TREE, Q: "9" * 40}, runs, {}, calls))
    assert rel.same_tree_run("ci.yml", M) is None
    assert not any(c[:3] == ["gh", "run", "view"] for c in calls)
    # step 7 then falls back to wait_run on M itself
    waited = []
    monkeypatch.setattr(rel, "wait_run", lambda wf, sha, **kw: waited.append((wf, sha)) or f"{wf} waited on {sha}")
    assert (rel.same_tree_run("ci.yml", M) or rel.wait_run("ci.yml", M)) == f"ci.yml waited on {M}"
    assert waited == [("ci.yml", M)]


@pytest.mark.parametrize("runs,views,trees", [
    ([ci_row(4, Q, conclusion="failure")], {}, {M: TREE, Q: TREE}),                 # not success
    ([ci_row(4, Q, status="in_progress")], {}, {M: TREE, Q: TREE}),                 # not finished
    ([ci_row(4, M)], {}, {M: TREE}),                                                # the sha itself: wait_run's job
    ([ci_row(4, Q)], {}, {M: TREE}),                                                # Q's tree unknown
    ([ci_row(4, Q)], {"4": {"conclusion": "success", "url": "u", "jobs": [GREEN_JOBS[0], {
        "name": "generators-macos", "conclusion": "skipped"}]}}, {M: TREE, Q: TREE}),  # a job not green
    ([ci_row(4, Q)], {"4": {"conclusion": "success", "url": "u", "jobs": []}}, {M: TREE, Q: TREE}),  # no jobs
])
def test_same_tree_needs_everything_green(monkeypatch, runs, views, trees):
    monkeypatch.setattr(rel, "sh", gh_shell(trees, runs, views, []))
    assert rel.same_tree_run("ci.yml", M) is None


# ---- step 8: the emulator ----

def test_release_yml_deploys_the_emulator(tmp_path):
    assert rel.workflow_deploys_pages()                      # this repo's release.yml has the pages deploy
    (tmp_path / "old.yml").write_text("jobs: {release: {steps: [{run: gh release create}]}}\n")
    assert not rel.workflow_deploys_pages(str(tmp_path / "old.yml"))
    assert not rel.workflow_deploys_pages(str(tmp_path / "missing.yml"))


def test_emulator_step_picks_verify_or_the_local_fallback(monkeypatch):
    r = rel.Release(rel.parse_args(["0.9.9"]))
    monkeypatch.setattr(r, "emulator_verify", lambda: "verified")
    monkeypatch.setattr(r, "emulator_local", lambda: "local")
    monkeypatch.setattr(rel, "workflow_deploys_pages", lambda: True)
    assert r.s_emulator() == "verified"
    monkeypatch.setattr(rel, "workflow_deploys_pages", lambda: False)
    assert r.s_emulator() == "local"


def g(cwd, *a):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pages_origin(tmp_path, monkeypatch):
    """ROOT: a clone whose origin has the tag v0.9.9 (web/index.html, web/app.js) and gh-pages (that emulator)"""
    import subprocess
    src = tmp_path / "src"
    (src / "web").mkdir(parents=True)
    g(src, "init", "-q", "-b", "main")
    for f in ("index.html", "app.js"):
        (src / "web" / f).write_text("page " + f)
    g(src, "add", "-A")
    g(src, "commit", "-q", "-m", "main")
    g(src, "tag", "v0.9.9")
    g(src, "checkout", "-q", "--orphan", "gh-pages")
    g(src, "rm", "-r", "-q", "-f", ".")
    pages = {"index.html": "page index.html", "app.js": "page app.js", "frodhes.js": "js", "frodhes.wasm": "wasm",
             "version.js": 'window.FRODHES_VERSION = "0.9.9";\n', ".nojekyll": ""}
    for f, t in pages.items():
        (src / f).write_text(t)
    g(src, "add", "-A")
    g(src, "commit", "-q", "-m", "Emulator 0.9.9")
    g(src, "checkout", "-q", "main")
    subprocess.run(["git", "clone", "-q", str(src), str(tmp_path / "root")], check=True)
    g(tmp_path / "root", "fetch", "-q", "--tags")
    monkeypatch.setattr(rel, "ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(rel.time, "sleep", lambda s: None)
    live = {f: __import__("hashlib").sha256(t.encode()).hexdigest() for f, t in pages.items()}
    monkeypatch.setattr(rel, "fetch_sha", lambda url: live.get(url.rsplit("/", 1)[1]))
    return src, live


def test_emulator_verify_checks_gh_pages_and_the_live_files(pages_origin, monkeypatch):
    src, live = pages_origin
    r = rel.Release(rel.parse_args(["0.9.9"]))
    r.ver = "0.9.9"
    ev = r.emulator_verify()
    assert "verify only" in ev and "Emulator 0.9.9" in ev and "version.js says 0.9.9" in ev
    assert "every file's sha256 = the expected one" in ev
    live["frodhes.wasm"] = "0" * 64                                  # the CDN serves something else
    with pytest.raises(rel.Stop, match="still differs.*frodhes.wasm"):
        r.emulator_verify()
    r.ver = "0.9.10"                                                 # gh-pages has another version's page
    with pytest.raises(rel.Stop):
        r.emulator_verify()


def test_emulator_verify_refuses_a_page_not_from_the_tag(pages_origin):
    src, _ = pages_origin
    g(src, "checkout", "-q", "gh-pages")
    (src / "app.js").write_text("something else")
    g(src, "commit", "-qam", "Emulator 0.9.9")
    g(src, "checkout", "-q", "main")
    r = rel.Release(rel.parse_args(["0.9.9"]))
    r.ver = "0.9.9"
    with pytest.raises(rel.Stop, match="app.js is not v0.9.9's web/app.js"):
        r.emulator_verify()


def test_dist_version_and_the_selftest_version_gate(tmp_path, monkeypatch):
    assert rel.dist_version(str(tmp_path)) is None
    (tmp_path / "version.js").write_text('window.FRODHES_VERSION = "0.9.9";\n')
    assert rel.dist_version(str(tmp_path)) == "0.9.9"
    (tmp_path / "version.js").write_text('window.FRODHES_VERSION = "dev";\n')
    assert rel.dist_version(str(tmp_path)) == "dev"
    with pytest.raises(rel.Stop, match="says 'dev', not '0.9.9'"):            # before any browser starts
        rel.web_selftest(str(tmp_path), "0.9.9")
    (tmp_path / "version.js").write_text("window.FRODHES_VERSION = 0;\n")
    with pytest.raises(rel.Stop, match="malformed"):
        rel.web_selftest(str(tmp_path))


def test_main_web_selftest(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(rel, "web_selftest", lambda d, v=None: seen.append((d, v)) or "selftest ok")
    assert rel.main(["--web-selftest", "web/dist", "0.9.9"]) == 0
    assert seen == [(os.path.abspath("web/dist"), "0.9.9")] and "selftest ok" in capsys.readouterr().out

    def bad(d, v=None):
        raise rel.Stop("no sound")
    monkeypatch.setattr(rel, "web_selftest", bad)
    assert rel.main(["--web-selftest", "web/dist"]) == 1 and "FAIL: no sound" in capsys.readouterr().out
