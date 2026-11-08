# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's packaging and CI (docs/PLUGIN.md "Packages"): tools/plugin_asset.sh (which packages a release
attaches), the workflows' wiring (plugin.yml, release.yml's plugin jobs), plugin/deps.lock's form and
tools/plugin_depcheck.py's allow-lists. Host only: no build here (CI's plugin jobs and tools/plugin_pack.sh build)."""
import importlib.util
import os
import re
import subprocess

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(ROOT, ".github", "workflows")
NAMES = {"macos": ["frodhes-plugin-macos.pkg", "frodhes-plugin-macos.zip"],
         "windows": ["frodhes-plugin-windows.zip"],
         "linux-x86_64": ["frodhes-plugin-linux-x86_64.zip"],
         "linux-aarch64": ["frodhes-plugin-linux-aarch64.zip"]}


def git(d, *a):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(d), *a], env=env, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("line, have, code, attached", [
    (None, "all", 0, []),                                               # not recorded: no plugin asset
    ("Plugin tested: macos linux-x86_64", "all", 0, ["macos", "linux-x86_64"]),
    ("Plugin tested: macos windows linux-x86_64 linux-aarch64", "all", 0, list(NAMES)),
    ("Plugin tested: macos linux-aarch64", "no-aarch64", 1, []),       # listed but not built: stop
    ("Plugin tested: beos", "all", 1, [])])                             # unknown platform: stop
def test_plugin_asset_sh(tmp_path, line, have, code, attached):
    git(tmp_path, "init", "-q")
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "v0.9.9.md").write_text("notes\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "Release 0.9.9: the notes", *(["-m", line] if line else []))
    (tmp_path / "x.txt").write_text("a later commit\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "later")
    (tmp_path / "in").mkdir()
    for p, files in NAMES.items():
        if have == "no-aarch64" and p == "linux-aarch64":
            continue
        for f in files:
            (tmp_path / "in" / f).write_text(p)
    r = subprocess.run(["sh", os.path.join(ROOT, "tools", "plugin_asset.sh"), "0.9.9", "in", "out"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == code, r.stdout + r.stderr
    got = sorted(os.listdir(tmp_path / "out")) if (tmp_path / "out").is_dir() else []
    assert got == sorted(f for p in attached for f in NAMES[p])


def test_plugin_workflow_artifacts_are_the_fixed_names():
    wf = yaml.safe_load(open(os.path.join(WF, "plugin.yml")))
    on = wf[True]
    assert set(on["workflow_call"]["inputs"]) == {"version", "sign"}
    assert {"pull_request", "push", "workflow_dispatch", "workflow_call"} <= set(on)
    paths = []
    for job in wf["jobs"].values():
        for st in job.get("steps", []):
            if st.get("uses", "").startswith("actions/upload-artifact"):
                assert st["with"]["name"].startswith("plugin-")
                paths += [p.strip() for p in st["with"]["path"].splitlines() if p.strip()]
    names = {os.path.basename(p).replace("${{ matrix.arch }}", a) for p in paths for a in ("x86_64", "aarch64")}
    assert {f for fs in NAMES.values() for f in fs} <= names
    arches = [m["arch"] for m in wf["jobs"]["linux"]["strategy"]["matrix"]["include"]]
    assert arches == ["x86_64", "aarch64"]
    linux = yaml.dump(wf["jobs"]["linux"])                          # the same script as a local build (Ubuntu 20.04)
    assert all(f"tools/plugin_linux.sh {k}" in linux for k in ("build", "check", "test", "pack"))
    sh = open(os.path.join(ROOT, "tools", "plugin_linux.sh")).read()
    assert "FROM ubuntu:20.04" in open(os.path.join(ROOT, "plugin", "tests", "docker", "linux.Dockerfile")).read()
    assert "plugin_depcheck.py linux" in sh and "--glibc 2.31" in sh
    for j in ("macos", "windows"):                                   # every platform runs the dependency check
        assert "tools/plugin_depcheck.py" in yaml.dump(wf["jobs"][j])
    assert "win-x64-clangcl.cmake" in yaml.dump(wf["jobs"]["windows"])   # one Windows toolchain, CI and local


def test_release_yml_attaches_the_plugin_without_holding_the_release():
    j = yaml.safe_load(open(os.path.join(WF, "release.yml")))["jobs"]
    assert j["plugin"]["uses"] == "./.github/workflows/plugin.yml"
    assert j["plugin"]["with"] == {"version": "${{ github.ref_name }}", "sign": True}
    assert j["plugin-release"]["needs"] == ["release", "plugin"]
    assert "plugin" not in j["release"]["needs"]                     # the firmware never waits for the plugin
    run = yaml.dump(j["plugin-release"])
    assert "tools/plugin_asset.sh" in run and "gh release upload" in run
    co = j["plugin-release"]["steps"][0]
    assert co["uses"].startswith("actions/checkout") and co["with"]["fetch-depth"] == 0


def test_deps_lock_form():
    for ln in open(os.path.join(ROOT, "plugin", "deps.lock")):
        if ln.startswith("#") or not ln.strip():
            continue
        kind, name, url, ref, pin, lic = ln.split()
        assert kind in ("git", "zip"), ln
        assert re.fullmatch(r"[a-z0-9-]+(@(macos|linux|windows))?", name), ln
        assert url.startswith("https://"), ln
        assert re.fullmatch(r"[0-9a-f]{40}" if kind == "git" else r"[0-9a-f]{64}", pin), ln
        assert lic, ln
    names = [ln.split()[1] for ln in open(os.path.join(ROOT, "plugin", "deps.lock")) if ln[:1] not in "#\n"]
    for tool in ("clap-validator", "pluginval"):                     # the validators for every CI system
        assert {f"{tool}@{o}" for o in ("macos", "linux", "windows")} <= set(names)


def test_depcheck_allow_lists():
    spec = importlib.util.spec_from_file_location("dc", os.path.join(ROOT, "tools", "plugin_depcheck.py"))
    dc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dc)
    for ok in ("KERNEL32.dll", "user32.dll", "gdi32.dll", "ole32.dll"):
        assert dc.WIN_OK.match(ok)
    for bad in ("VCRUNTIME140.dll", "MSVCP140.dll", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll",
                "libc++.dll", "libwinpthread-1.dll", "libgcc_s_seh-1.dll"):
        assert not dc.WIN_OK.match(bad)
    for ok in ("libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "libX11.so.6", "libxcb.so.1",
               "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"):
        assert dc.LINUX_OK.match(ok)
    for bad in ("libstdc++.so.6", "libgcc_s.so.1", "libgtk-3.so.0", "libasound.so.2", "libGL.so.1"):
        assert not dc.LINUX_OK.match(bad)
    for ok in ("/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib",
               "/System/Library/Frameworks/AudioToolbox.framework/Versions/A/AudioToolbox"):
        assert dc.MAC_OK.match(ok)
    for bad in ("@rpath/libfoo.dylib", "/opt/homebrew/lib/libz.dylib", "/usr/local/lib/libx.dylib"):
        assert not dc.MAC_OK.match(bad)
