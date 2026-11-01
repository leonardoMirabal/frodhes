#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move package in Schwung's module catalog: what Schwung Manager reads from this repository.

    tools/move_catalog.py release-json X.Y.Z [--asset NAME] [--repo OWNER/REPO] [--out FILE]
    tools/move_catalog.py check TARBALL X.Y.Z

release-json  writes release.json ({"version", "download_url"}), the file Schwung Manager fetches from the catalog
              entry's default_branch (raw.githubusercontent.com/<repo>/main/release.json) to find the latest version
              and its tarball. The URL is the tag's asset: https://github.com/<repo>/releases/download/vX.Y.Z/<asset>
              (asset default frodhes-module.tar.gz, the catalog entry's asset_name). The repository is the origin
              remote's (git remote get-url origin: git@github.com:o/r.git or https://github.com/o/r[.git]). An
              existing FILE with a newer version is left alone (a rerun of an older tag never moves the manager back);
              the same content is not rewritten. Exit 0; prints what it did.
              release.yml's job release-json runs it after the release is published, and only when the release
              carries the Move package (tools/move_asset.sh found it tested).
check         the tarball is what the manager installs: one folder frodhes/ (it extracts the tarball into
              modules/sound_generators/ and expects the module id as the folder), plain files only, no absolute or
              ".." paths, dsp.so engine.so module.json in it, and module.json with id frodhes, name Frodhes,
              component_type sound_generator (top level and in capabilities), dsp dsp.so and version X.Y.Z (make move
              MOVE_VER=X.Y.Z). Exit 1 with every problem. release.yml checks the tag's tarball with it.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tarfile

MODULE_ID = "frodhes"
ASSET = "frodhes-module.tar.gz"         # = release.py MOVE_STABLE, the catalog entry's asset_name
REQUIRED = ("dsp.so", "engine.so", "module.json")
VERSION = re.compile(r"\d+\.\d+\.\d+")


def repo_from_url(url):
    """owner/repo of a GitHub remote URL (ssh or https); ValueError otherwise"""
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https?://(?:[^@/]+@)?github\.com/)"
                 r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise ValueError(f"not a GitHub remote: {url!r}")
    return f"{m.group(1)}/{m.group(2)}"


def origin_repo(cwd=None):
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, check=True, capture_output=True,
                         text=True).stdout
    return repo_from_url(url)


def vtuple(v):
    if not VERSION.fullmatch(v or ""):
        raise ValueError(f"not a version X.Y.Z: {v!r}")
    return tuple(int(x) for x in v.split("."))


def release_json(version, repo, asset=ASSET):
    vtuple(version)
    return {"version": version,
            "download_url": f"https://github.com/{repo}/releases/download/v{version}/{asset}"}


def write_release_json(version, repo, out, asset=ASSET):
    """(changed, message)"""
    new = release_json(version, repo, asset)
    text = json.dumps(new, indent=2) + "\n"
    if os.path.exists(out):
        try:
            old = json.load(open(out))
            if vtuple(old.get("version", "")) > vtuple(version):
                return False, f"{out}: has {old['version']}, newer than {version}: left alone"
        except (ValueError, AttributeError):
            pass                                    # (unreadable: rewritten)
        if open(out).read() == text:
            return False, f"{out}: already {version}"
    with open(out, "w") as f:
        f.write(text)
    return True, f"{out}: {version} {new['download_url']}"


def check_tarball(path, version):
    """[problems] of the Move package at path for version"""
    errs = []
    try:
        t = tarfile.open(path, "r:gz")
    except (OSError, tarfile.TarError) as e:
        return [f"{path}: not a .tar.gz ({e})"]
    with t:
        names = {}
        for m in t.getmembers():
            n = m.name[2:] if m.name.startswith("./") else m.name
            if n.startswith("/") or ".." in n.split("/"):
                errs.append(f"{m.name}: an absolute or '..' path")
            if not (m.isfile() or m.isdir()):
                errs.append(f"{m.name}: not a plain file or folder")
            if n.split("/")[0] != MODULE_ID:
                errs.append(f"{m.name}: outside {MODULE_ID}/ (the tarball must unpack to {MODULE_ID}/ only)")
            names[n.rstrip("/")] = m
        for f in REQUIRED:
            if f"{MODULE_ID}/{f}" not in names:
                errs.append(f"{MODULE_ID}/{f} missing")
        mj = names.get(f"{MODULE_ID}/module.json")
        if mj is not None and mj.isfile():
            try:
                mod = json.loads(t.extractfile(mj).read().decode())
            except ValueError as e:
                return errs + [f"{MODULE_ID}/module.json: not JSON ({e})"]
            want = {"id": MODULE_ID, "name": "Frodhes", "version": version, "component_type": "sound_generator",
                    "dsp": "dsp.so"}
            for k, v in want.items():
                if mod.get(k) != v:
                    errs.append(f"module.json {k} is {mod.get(k)!r}, not {v!r}")
            if (mod.get("capabilities") or {}).get("component_type") != "sound_generator":
                errs.append("module.json capabilities.component_type is not 'sound_generator'")
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("release-json")
    r.add_argument("version")
    r.add_argument("--asset", default=ASSET)
    r.add_argument("--repo", help="owner/repo (default: the origin remote's)")
    r.add_argument("--out", default="release.json")
    c = sub.add_parser("check")
    c.add_argument("tarball")
    c.add_argument("version")
    a = ap.parse_args(argv)
    if a.cmd == "release-json":
        repo = a.repo or origin_repo()
        _, msg = write_release_json(a.version, repo, a.out, a.asset)
        print("move_catalog:", msg)
        return 0
    errs = check_tarball(a.tarball, a.version)
    for e in errs:
        print("move_catalog:", e, file=sys.stderr)
    if not errs:
        print(f"move_catalog: {a.tarball}: {MODULE_ID}/ with module.json version {a.version}: ok")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
