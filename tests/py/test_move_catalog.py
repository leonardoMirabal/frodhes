# SPDX-License-Identifier: GPL-3.0-only
"""The Move package in Schwung's module catalog (tools/move_catalog.py): release.json, the file Schwung Manager reads
from main to find the latest version and its tarball, and the tarball as the manager installs it (one folder
frodhes/, module.json's version the release's). No network, no device."""
import io
import json
import os
import re
import subprocess
import sys
import tarfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_catalog as mc  # noqa: E402
import release as rel  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "move_catalog.py")
REPO = "leonardoMirabal/frodhes"


def run(*a, cwd=None):
    return subprocess.run([sys.executable, TOOL, *a], cwd=cwd, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("url", ["git@github.com:leonardoMirabal/frodhes.git", "git@github.com:leonardoMirabal/frodhes",
                                 "https://github.com/leonardoMirabal/frodhes.git", "https://github.com/leonardoMirabal/frodhes",
                                 "https://x-access-token:abc@github.com/leonardoMirabal/frodhes/\n",
                                 "ssh://git@github.com/leonardoMirabal/frodhes.git"])
def test_repo_from_remote_url(url):
    assert mc.repo_from_url(url) == REPO


def test_repo_not_github():
    with pytest.raises(ValueError):
        mc.repo_from_url("https://gitlab.com/a/b.git")


@pytest.mark.parametrize("version, asset", [("0.9.8", "frodhes-module.tar.gz"), ("1.2.3", "frodhes-1.2.3-move.tar.gz")])
def test_release_json_for_a_version_and_asset(tmp_path, version, asset):
    out = tmp_path / "release.json"
    p = run("release-json", version, "--asset", asset, "--repo", REPO, "--out", str(out))
    assert p.returncode == 0, p.stderr
    d = json.loads(out.read_text())                              # (valid JSON)
    assert d == {"version": version,
                 "download_url": f"https://github.com/{REPO}/releases/download/v{version}/{asset}"}
    assert out.read_text().endswith("}\n")


def test_release_json_default_asset_is_the_stable_name():
    assert mc.ASSET == rel.MOVE_STABLE == "frodhes-module.tar.gz"
    assert mc.release_json("0.9.8", REPO)["download_url"].endswith("/v0.9.8/frodhes-module.tar.gz")


def test_release_json_repo_from_origin(tmp_path):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", f"git@github.com:{REPO}.git"], check=True,
                   env=env)
    p = run("release-json", "0.9.8", cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    assert json.loads((tmp_path / "release.json").read_text())["download_url"] == \
        f"https://github.com/{REPO}/releases/download/v0.9.8/frodhes-module.tar.gz"


def test_release_json_never_goes_back_and_reruns_are_no_ops(tmp_path):
    out = str(tmp_path / "release.json")
    assert mc.write_release_json("0.9.9", REPO, out)[0]
    text = open(out).read()
    changed, msg = mc.write_release_json("0.9.8", REPO, out)       # a rerun of an older tag
    assert not changed and "newer than 0.9.8" in msg and open(out).read() == text
    changed, msg = mc.write_release_json("0.9.9", REPO, out)       # the same tag again
    assert not changed and "already 0.9.9" in msg
    assert mc.write_release_json("0.9.10", REPO, out)[0]           # (numeric, not text: 0.9.10 > 0.9.9)
    assert json.load(open(out))["version"] == "0.9.10"


def test_release_json_refuses_a_bad_version(tmp_path):
    p = run("release-json", "v0.9.8", "--repo", REPO, "--out", str(tmp_path / "r.json"))
    assert p.returncode != 0 and not (tmp_path / "r.json").exists()


# ---- the tarball ----

def module_json(version="0.9.8", **over):
    m = json.loads(open(os.path.join(ROOT, "move", "module", "module.json")).read())
    m["version"] = version
    m.update(over)
    return m


def tarball(path, files):
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            if data is None:
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.DIRTYPE
                t.addfile(ti)
                continue
            data = data if isinstance(data, bytes) else json.dumps(data).encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return str(path)


def good(**over):
    f = {"frodhes/dsp.so": b"so", "frodhes/engine.so": b"so", "frodhes/module.json": module_json(),
         "frodhes/help.json": b"{}", "frodhes/LICENSE": b"GPL"}
    f.update(over)
    return f


def test_check_a_good_package(tmp_path):
    assert mc.check_tarball(tarball(tmp_path / "t.tar.gz", good()), "0.9.8") == []
    p = run("check", str(tmp_path / "t.tar.gz"), "0.9.8")
    assert p.returncode == 0 and "frodhes/ with module.json version 0.9.8: ok" in p.stdout


@pytest.mark.parametrize("files, problem", [
    (good(**{"other/x": b"x"}), "outside frodhes/"),                          # more than the module's folder
    ({k.replace("frodhes/", "Frodhes/"): v for k, v in good().items()}, "outside frodhes/"),
    (good(**{"frodhes/module.json": module_json("0.0.0-dev")}), "version is '0.0.0-dev', not '0.9.8'"),
    (good(**{"frodhes/module.json": module_json(id="frodhes-dev")}), "id is 'frodhes-dev'"),
    (good(**{"frodhes/module.json": module_json(component_type="audio_fx")}), "component_type is 'audio_fx'"),
    (good(**{"frodhes/module.json": b"{not json"}), "not JSON"),
    ({k: v for k, v in good().items() if k != "frodhes/engine.so"}, "frodhes/engine.so missing"),
    (good(**{"frodhes/../evil": b"x"}), "'..' path")])
def test_check_finds_the_problem(tmp_path, files, problem):
    errs = mc.check_tarball(tarball(tmp_path / "t.tar.gz", files), "0.9.8")
    assert any(problem in e for e in errs), errs
    assert run("check", str(tmp_path / "t.tar.gz"), "0.9.8").returncode == 1


def test_check_not_a_tarball(tmp_path):
    (tmp_path / "t.tar.gz").write_bytes(b"not gzip")
    assert "not a .tar.gz" in mc.check_tarball(str(tmp_path / "t.tar.gz"), "0.9.8")[0]


def test_module_json_carries_the_version_and_the_catalog_fields():
    """the committed module.json: make move swaps its "0.0.0-dev" for MOVE_VER (the release's X.Y.Z); the fields
    Schwung's manager reads when it installs (id = the folder, component_type = the install folder, dsp)"""
    src = open(os.path.join(ROOT, "move", "module", "module.json")).read()
    mk = open(os.path.join(ROOT, "Makefile")).read()
    assert "sed 's/\"version\": \"0.0.0-dev\"/\"version\": \"$(MOVE_VER)\"/' move/module/module.json" in mk
    assert src.count('"version": "0.0.0-dev"') == 1
    m = json.loads(src)
    assert (m["id"], m["name"], m["component_type"], m["capabilities"]["component_type"], m["dsp"]) == \
        ("frodhes", "Frodhes", "sound_generator", "sound_generator", "dsp.so")
    assert "min_host_version" not in m          # (a catalog entry's field, not the module's)
    # the tarball is built from build/move with the paths frodhes/...: it unpacks to frodhes/
    assert re.search(r"tar -C build/move .*\n.*ls frodhes/dsp\.so frodhes/engine\.so frodhes/module\.json", mk)


def test_the_built_package():
    """build/move/frodhes-module.tar.gz, when built (tools/ci_move.sh builds it before it runs this file)"""
    tar = os.path.join(ROOT, rel.MOVE_BUILT)
    if not os.path.exists(tar):
        pytest.skip(f"{rel.MOVE_BUILT} not built (make move; tools/ci_move.sh builds it)")
    with tarfile.open(tar) as t:
        version = json.loads(t.extractfile("frodhes/module.json").read())["version"]
    assert mc.check_tarball(tar, version) == []


def test_release_yml_checks_the_package_and_writes_release_json():
    import yaml                          # (PyYAML, installed in CI: a missing module fails, never skips)
    j = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]
    st = j["release"]["steps"]
    names = [s.get("name") or s.get("uses") for s in st]
    chk = [s for s in st if s.get("id") == "move-pkg"][0]
    assert names.index("the Move package, if tested") < names.index(chk["name"]) < names.index("draft release")
    assert 'tools/move_catalog.py check build/move-ci/frodhes-module.tar.gz "${GITHUB_REF_NAME#v}"' in chk["run"]
    assert "build/publish/move/frodhes-module.tar.gz" in chk["run"] and "shipped=true" in chk["run"]
    assert j["release"]["outputs"]["move"] == "${{ steps.move-pkg.outputs.shipped }}"
    rj = j["release-json"]
    assert rj["needs"] == "release" and "needs.release.outputs.move == 'true'" in rj["if"]
    assert rj["steps"][0]["with"]["ref"] == "main"
    run_ = rj["steps"][-1]["run"]
    assert 'tools/move_catalog.py release-json "$V"' in run_ and "git push origin HEAD:main" in run_
    assert run_.index("curl -fsIL") < run_.index("git commit")     # (the URL downloads before it is committed)
