# SPDX-License-Identifier: GPL-3.0-only
"""tools/mpc_catalog_zip.py: the open-mpc catalogue's per-build zips of the MPC plugin (layout, manifest, checksums,
reproducible bytes, the pinned templates), on a synthetic plugin folder; and, where the 0.9.12 release folder and the
reference zips made with open-mpc's own tool are on disk (build/akai, the main checkout), the same files as those but
INSTALL.md (Frodhes' own text) and SHA256SUMS (its INSTALL.md line)."""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mpc_catalog_zip as M  # noqa: E402

PJ = {"name": "Frodhes", "manufacturer": "Frodhes", "version": "1.2.3", "format": "vst2", "kind": "instrument",
      "uid": "Frd1", "numInputs": 0, "numOutputs": 2, "category": "Synth", "file32": "VST/frodhes-arm32.so",
      "file64": "VST/frodhes-arm64.so", "description": "An electric piano.", "license": "GPL-3.0-only"}


def fake_elf(bits64, machine):
    h = bytearray(64)
    h[:4] = b"\x7fELF"
    h[4], h[5] = (2 if bits64 else 1), 1
    h[18:20] = machine.to_bytes(2, "little")
    return bytes(h) + b"code" * 100


@pytest.fixture
def folder(tmp_path, monkeypatch):
    f = tmp_path / "Frodhes - VST - Frodhes"
    (f / "VST").mkdir(parents=True)
    (f / "Plugin Skins").mkdir()
    (f / "plugin.json").write_text(json.dumps(PJ, indent=2) + "\n")
    (f / "version.xml").write_text("<plugincontent/>\n")
    (f / "Plugin Skins" / "TUI.json").write_text("{}\n")
    (f / "Plugin Skins" / "knob.png").write_bytes(b"png")
    (f / "VST" / "frodhes-arm32.so").write_bytes(fake_elf(False, 40))
    (f / "VST" / "frodhes-arm64.so").write_bytes(fake_elf(True, 183))
    t = tmp_path / "tpl"
    t.mkdir()
    texts = {"install.sh": "#!/bin/sh\n# @NAME@ @VERSION@\nSO='@SO_NAME@'; SKIN='@SKIN@'; UID_HEX='@UID@'; LEGACY_SO='@LEGACY_SO@'; ID='@ID@'\n",
             "uninstall.sh": "#!/bin/sh\nSO='@SO_NAME@'\n", "plugin_list.awk": "{ print }\n"}
    for n, s in texts.items():
        (t / n).write_text(s)
    monkeypatch.setattr(M, "TPL_SHA", {n: hashlib.sha256(s.encode()).hexdigest() for n, s in texts.items()})
    return f, t


def test_layout_manifest_and_sums(folder, tmp_path):
    f, t = folder
    made = M.build(str(f), "1.2.3", str(tmp_path / "out"), M.templates(str(t)))
    assert sorted(os.path.basename(z) for z in made.values()) == ["Frodhes-1.2.3-mpc-aarch64.zip",
                                                                  "Frodhes-1.2.3-mpc-armv7.zip"]
    for arch, so, key, other in (("armv7", "VST/frodhes-arm32.so", "file32", "VST/frodhes-arm64.so"),
                                 ("aarch64", "VST/frodhes-arm64.so", "file64", "VST/frodhes-arm32.so")):
        z = zipfile.ZipFile(made[arch])
        names = z.namelist()
        top, pdir = "Frodhes-1.2.3/", "Frodhes-1.2.3/portable/Frodhes - VST - Frodhes/"
        for n in ("INSTALL.md", "MODES", "SHA256SUMS", "install.sh", "mpc-plugin.json", "plugin_list.awk", "uninstall.sh"):
            assert top + n in names, n
        for n in ("mpc-plugin.json", "plugin-meta.xml", "plugin.json", "version.xml", "Plugin Skins/TUI.json", so):
            assert pdir + n in names, n
        assert pdir + other not in names                       # this build's binary only
        assert all(i.date_time == (2026, 1, 1, 0, 0, 0) and i.create_system == 3 for i in z.infolist())
        assert z.getinfo(top + "install.sh").external_attr >> 16 == 0o100755
        m = json.loads(z.read(top + "mpc-plugin.json"))
        assert m == json.loads(z.read(pdir + "mpc-plugin.json"))
        assert (m["id"], m["arch"], m["so"], m["uid"], m["layout"], m["os_compat"], m["param_compat"], m["vst2"],
                m["source_repo"], m["license"]) == ("frodhes", arch, so, "46726431", "portable", ["3.x"], 1, True,
                                                    "leonardoMirabal/frodhes", "GPL-3.0-only")
        pj = json.loads(z.read(pdir + "plugin.json"))
        assert pj[key] == so and ("file64" if key == "file32" else "file32") not in pj and pj["uid"] == "0x46726431"
        meta = z.read(pdir + "plugin-meta.xml").decode()
        assert 'file="%%payload-path%%/Frodhes - VST - Frodhes/%s"' % so in meta and 'uid="46726431"' in meta
        inst = z.read(top + "install.sh").decode()
        assert "@" not in inst and "SO='%s'" % so in inst and "Frodhes 1.2.3" in inst
        sums = dict(reversed(line.split("  ", 1)) for line in z.read(top + "SHA256SUMS").decode().splitlines())
        files = [n[len(top):] for n in names if not n.endswith("SHA256SUMS")]
        assert sorted(sums) == sorted(files)
        for n in files:
            assert sums[n] == hashlib.sha256(z.read(top + n)).hexdigest(), n
        assert z.read(top + "MODES") == b""


def test_reproducible(folder, tmp_path):
    f, t = folder
    a = M.build(str(f), "1.2.3", str(tmp_path / "a"), M.templates(str(t)))
    b = M.build(str(f), "1.2.3", str(tmp_path / "b"), M.templates(str(t)))
    for arch in a:
        assert open(a[arch], "rb").read() == open(b[arch], "rb").read()
        assert M.compare(a[arch], b[arch]) == []


def test_a_changed_template_is_refused(folder, tmp_path):
    f, t = folder
    (t / "install.sh").write_text("#!/bin/sh\necho other\n")
    with pytest.raises(SystemExit):
        M.templates(str(t))


def test_wrong_binary_refused(folder, tmp_path):
    f, t = folder
    (f / "VST" / "frodhes-arm64.so").write_bytes(fake_elf(False, 40))       # a 32-bit binary named as file64
    with pytest.raises(SystemExit):
        M.build(str(f), "1.2.3", str(tmp_path / "out"), M.templates(str(t)))


def main_repo():
    try:
        g = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return os.path.dirname(os.path.abspath(os.path.join(ROOT, g)))
    except OSError:
        return ROOT


def test_matches_the_reference_zips(tmp_path):
    """the 0.9.12 release folder through this tool = open-mpc's tools/sdk_release.py zips of it, but INSTALL.md (our
    text) and SHA256SUMS (that file's line); local only (the inputs are not in git)"""
    akai = os.path.join(main_repo(), "build", "akai")
    src = os.path.join(akai, "rel", "Frodhes - VST - Frodhes")
    tpl = next((d for d in (os.path.join(ROOT, "build", "mpc-catalog", "templates"),
                            os.path.join(main_repo(), "build", "mpc-catalog", "templates"))
                if os.path.isfile(os.path.join(d, "install.sh"))), None)
    refs = {a: os.path.join(akai, "catzip", "Frodhes-0.9.12-mpc-%s.zip" % a) for a in ("armv7", "aarch64")}
    if not (os.path.isdir(src) and tpl and all(map(os.path.isfile, refs.values()))):
        pytest.skip("no 0.9.12 release folder, reference zips or templates on disk (build/akai, build/mpc-catalog)")
    made = M.build(src, "0.9.12", str(tmp_path), M.templates(tpl))
    for a, ref in refs.items():
        assert M.compare(made[a], ref) == ["content differs: Frodhes-0.9.12/INSTALL.md",
                                           "content differs: Frodhes-0.9.12/SHA256SUMS"], a
        mine = zipfile.ZipFile(made[a]).read("Frodhes-0.9.12/SHA256SUMS").decode().splitlines()
        theirs = zipfile.ZipFile(ref).read("Frodhes-0.9.12/SHA256SUMS").decode().splitlines()
        assert [x for x in mine if not x.endswith("  INSTALL.md")] == [x for x in theirs if not x.endswith("  INSTALL.md")]
        assert zipfile.ZipFile(made[a]).namelist() == zipfile.ZipFile(ref).namelist()
        assert json.loads(zipfile.ZipFile(made[a]).read("Frodhes-0.9.12/mpc-plugin.json"))["max_glibc"] == "2.29"
