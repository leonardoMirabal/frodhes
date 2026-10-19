# SPDX-License-Identifier: GPL-3.0-only
"""tools/release.py without network or device: the version logic, the docs bump, the argument parsing, the HIL
checks, and a whole --dry-run with git / gh / make replaced by a fake shell (it must stop before any push)."""
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


def test_bump_docs():
    t = ("**Latest release: [Frodhes 0.9.2](x)** `frodhes-0.9.2.fwsc`\nVersion 0.9.3.\n"
         "![a](img/piano.png?v=0.9.3) ![b](img/x.png?v=old) frodhes-0.9.1.fwsc\n")
    n = rel.bump_docs(t, "0.9.4")
    assert "0.9.2" not in n and "0.9.3" not in n and "0.9.1" not in n and "v=old" not in n
    assert n.count("0.9.4") == 6


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


def fake_shell(root, calls, notes_draft=False):
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
        }
        if s.startswith("./build.sh --release"):
            os.makedirs(os.path.join(root, "build"), exist_ok=True)
            open(os.path.join(root, "build", f"frodhes-{cmd[-1]}.fwsc"), "wb").write(b"fw")
        return 0, replies.get(s, "1 passed\n")
    return sh


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "README.md").write_text("Latest release: [Frodhes 0.9.2](x) `frodhes-0.9.2.fwsc` ![p](a.png?v=0.9.2)\n")
    (tmp_path / "docs" / "MANUAL.md").write_text("Version 0.9.2. `frodhes-0.9.2.fwsc`\n")
    monkeypatch.setattr(rel, "ROOT", str(tmp_path))
    return tmp_path


def test_dry_run_stops_before_push(tree, monkeypatch, capsys):
    (tree / "docs" / "releases" / "v0.9.3.md").write_text("**Frodhes 0.9.3**: notes\n")
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("--dry-run must not prompt"))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"]) == 0
    o = capsys.readouterr().out
    assert not [c for c in calls if " push" in c or "tag -a" in c or "git commit" in c or "gh release" in c]
    assert "make test" in calls and "./build.sh --release 0.9.3" in calls
    assert "0.9.2" in (tree / "README.md").read_text()            # the docs are not written in a dry run
    assert "--no-device: the FM-1 is NOT tested" in o and "stopped before any push" in o
    for line in (" 4  FM-1           skipped  --no-device", " 7  publish        skipped  --dry-run",
                 " 8  emulator       skipped  --no-emulator", " 3  host           done"):
        assert line in o


def test_draft_banner_and_existing_tag(tree, monkeypatch, capsys):
    (tree / "docs" / "releases" / "v0.9.3.md").write_text("> **DRAFT, not released yet.**\n")
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1      # a real run stops at step 2
    o = capsys.readouterr().out
    assert "DRAFT banner" in o and " 3  host           not run" in o
    assert not [c for c in calls if "make" in c or "push" in c]

    def tagged(cmd, **kw):
        s = " ".join(cmd) if not isinstance(cmd, str) else cmd
        return 0, ("v0.9.3\n" if s == "git tag --list v0.9.3" else "v0.9.2\n" if "sort" in s else "")
    monkeypatch.setattr(rel, "sh", tagged)
    assert rel.main(["patch", "--dry-run"]) == 1
    assert "v0.9.3 already exists locally" in capsys.readouterr().out


def test_missing_notes_writes_a_draft(tree, monkeypatch):
    calls = []
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), calls))
    assert rel.main(["patch", "--no-device", "--no-emulator", "--yes"]) == 1
    draft = (tree / "docs" / "releases" / "v0.9.3.md").read_text()
    assert draft.startswith("> **DRAFT") and "frodhes-0.9.3.fwsc" in draft


def test_dry_run_lists_the_device_plan(tree, monkeypatch, capsys):
    """--no-device lists step 4's commands: the Rhodes / VIBES ones as before, then MARIMBA's"""
    (tree / "docs" / "releases" / "v0.9.3.md").write_text("**Frodhes 0.9.3**: notes\n")
    monkeypatch.setattr(rel, "sh", fake_shell(str(tree), []))
    assert rel.main(["patch", "--dry-run", "--no-device", "--no-emulator"]) == 0
    o = capsys.readouterr().out
    plan = rel.device_plan("0.9.3")
    for line in plan:
        assert line in o
    py = rel.PY
    assert [p.split("   (")[0] for p in plan] == [
        f"echo y | {py} tools/fm1_install.py build/frodhes-0.9.3.fwsc",
        f"{py} tools/hil_cpu.py --worst",
        f"{py} tools/hil_cpu.py --worst --engine vibes",
        f"{py} tools/hil_cpu.py --worst --engine marimba",
        f"{py} tools/hil_scan.py --only {rel.SCAN_SET}",
        f"{py} tools/hil_scan.py --only vibes_vel --source both",
        f"{py} tools/hil_scan.py --marimba --source both",
        f"{py} tools/hil_jack.py --engine rhodes",
        f"{py} tools/hil_jack.py --engine vibes",
        f"{py} tools/hil_jack.py --engine marimba",
        f"{py} tools/vib_sampleback.py --engine marimba"]


def test_device_step_runs_the_plan(tree, monkeypatch):
    """s_device with a fake FM-1: every command of device_plan, in its order, and every check passes"""
    import json
    hil = tree / "build" / "hil"
    hil.mkdir(parents=True)
    jack = dict(loud=dict(jack_peak_db=-15.0), snr_db=80.0)
    for n in ("jack.json", "jack-vibes.json"):
        (hil / n).write_text(json.dumps(jack))
    (hil / "jack-marimba.json").write_text(json.dumps(dict(marimba=dict(loud=dict(jack_peak_db=-15.3), snr_db=70.5),
                                                          snr_vs_st77_db=-3.8, tails={"C4": dict(ok=True)},
                                                          idle_ok=True, idle={})))
    calls = []

    def sh(cmd, check=True, input=None, timeout=None, quiet=False):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        calls.append(s)
        if "hil_cpu" in s:
            return 0, "a   voices shed 0\n\nworst 3400 us with 9 voices"
        if "--marimba" in s:
            (hil / "marimba-report.json").write_text(json.dumps(m_report()))
            return 1, "FAIL  marimba_jump"                        # (exits 1 on any FAIL row: judged by the report)
        if "vib_sampleback" in s:
            return 0, "61 notes, 0 outside Gate VD-M."
        return 0, "ok\n"
    monkeypatch.setattr(rel, "sh", sh)
    r = rel.Release(rel.parse_args(["0.9.3"]))
    r.ver = "0.9.3"
    ev = r.s_device()
    assert calls == [p.split("   (")[0] for p in rel.device_plan("0.9.3")]
    assert "hil_cpu --worst marimba: worst 3400" in ev and "vib_sampleback marimba: 61 keys" in ev
    assert "hil_jack marimba: SNR 70.5" in ev and "every row ok" in ev
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
