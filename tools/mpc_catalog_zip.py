#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The open-mpc catalogue's per-build zips of the Frodhes MPC plugin (docs/MPC.md "From the open-mpc catalogue").

    tools/mpc_catalog_zip.py [FOLDER] --version X.Y.Z [-o build/mpc] [--templates DIR | --fetch]
    tools/mpc_catalog_zip.py --compare A.zip B.zip      the names and contents of two zips, a line per difference

FOLDER is the plugin folder mpc/build.py makes (default build/mpc/"Frodhes - VST - Frodhes": plugin.json naming
frodhes-arm32.so as file32 and frodhes-arm64.so as file64, both at its root, version.xml, Plugin Skins/). Writes
Frodhes-X.Y.Z-mpc-armv7.zip and Frodhes-X.Y.Z-mpc-aarch64.zip in the layout of the catalogue's spec (open-mpc
docs/CATALOG_SPEC.md "Release zip", docs/RELEASING.md "From an Akai SDK build"), the zips the catalogue, its Plugin
Manager and its installers install: one top folder Frodhes-X.Y.Z/ holding
    mpc-plugin.json    the catalogue manifest (schema 1)
    install.sh, uninstall.sh, plugin_list.awk
                       the catalogue's own installer templates (open-mpc tools/release/, pinned below by commit and
                       sha256; @NAME@ ... filled in for this build); fetched, never kept in this repository
    INSTALL.md         Frodhes' instructions for the scripted and the manual install
    MODES              the plugin folder's executable files and symlinks (none: an empty file)
    SHA256SUMS         every other file's sha256
    portable/Frodhes - VST - Frodhes/
                       the plugin folder with this build's binary only, where plugin.json names it, rewritten for the
                       build (file32 or file64), plugin-meta.xml (the MPC OS 3.9.1 plugin-list entry, file= with the
                       installer's %payload-path%), a copy of mpc-plugin.json
The zips are reproducible: sorted names, a fixed date (2026-01-01), unix modes, deflate. os_compat is ["3.x"]: the
catalogue's 2.x check needs its table of MPC OS 2.15.1's skins, and the spec lets a release narrow it, never widen.

--templates DIR reads the three templates from DIR; --fetch downloads them from the pinned commit into
build/mpc-catalog/templates (the release workflow does this); either way their sha256 must be the pinned one.
Exit 0 ok, 1 a check failed, 2 bad input."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "Frodhes - VST - Frodhes"
ID, REPO = "frodhes", "leonardoMirabal/frodhes"
ARCHES = (("file32", "armv7"), ("file64", "aarch64"))
TPL_REPO, TPL_COMMIT = "open-mpc/mpc-vst-plugins", "762661c5286c2d60a424907d0f8375fc02713f84"
TPL_SHA = {"install.sh": "fdfc6f931389a2e5c27b37b0e2557b2c324d08ef97856b790192b5420acfb200",
           "uninstall.sh": "6b90c7f7ade4752b70a39b44656c049969ecc7425d01024af858ecf5bff4cf99",
           "plugin_list.awk": "a6df58b45fc6baf98d5f3a1c7e126d1c3dee42031883d84164b8b28d815534c2"}
TPL_CACHE = os.path.join(ROOT, "build", "mpc-catalog", "templates")
DATE = (2026, 1, 1, 0, 0, 0)


def die(msg, code=2):
    print("mpc_catalog_zip: " + msg, file=sys.stderr)
    sys.exit(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def templates(src=None, fetch=False):
    """{name: text} of the three installer templates, each checked against its pinned sha256"""
    d = src or TPL_CACHE
    out = {}
    for n, want in TPL_SHA.items():
        p = os.path.join(d, n)
        if fetch and not src and not (os.path.isfile(p) and sha(open(p, "rb").read()) == want):
            os.makedirs(d, exist_ok=True)
            url = "https://raw.githubusercontent.com/%s/%s/tools/release/%s" % (TPL_REPO, TPL_COMMIT, n)
            with urllib.request.urlopen(url, timeout=60) as r:
                open(p, "wb").write(r.read())
        if not os.path.isfile(p):
            die("no template %s in %s (--fetch, or --templates DIR)" % (n, d))
        data = open(p, "rb").read()
        if sha(data) != want:
            die("template %s: sha256 %s is not the pinned %s (open-mpc %s)" % (n, sha(data), want, TPL_COMMIT[:12]), 1)
        out[n] = data.decode("utf-8")
    return out


def uid_hex(uid):
    """plugin.json's uid (a four-character code, a decimal or 0x hex) as the plugin list's 8 hex digits"""
    u = str(uid).strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]{1,8}", u):
        return "%08x" % int(u, 16)
    if re.fullmatch(r"[0-9]{1,10}", u):
        return "%08x" % (int(u) & 0xffffffff)
    if len(u) == 4 and all(32 <= ord(c) < 127 for c in u):
        return "".join("%02x" % ord(c) for c in u)
    die("plugin.json uid %r: a four-character code, a decimal or a 0x value" % uid)


def elf_arch(data):
    if data[:4] != b"\x7fELF":
        return None
    m = int.from_bytes(data[18:20], "little")
    return {40: "armv7", 183: "aarch64"}.get(m) if (m != 40 or data[4:6] == b"\x01\x01") else None


def max_glibc(data):
    """the newest GLIBC_x.y the ELF needs, from its version-needs section (SHT_GNU_verneed; never a string search)"""
    if data[:4] != b"\x7fELF":
        return None
    b64, end = data[4] == 2, "little" if data[5] == 1 else "big"
    n = lambda o, k: int.from_bytes(data[o:o + k], end)   # noqa: E731
    if b64:
        shoff, shentsize, shnum = n(40, 8), n(58, 2), n(60, 2)
    else:
        shoff, shentsize, shnum = n(32, 4), n(46, 2), n(48, 2)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        if b64:
            secs.append(dict(type=n(o + 4, 4), off=n(o + 24, 8), link=n(o + 40, 4), info=n(o + 44, 4)))
        else:
            secs.append(dict(type=n(o + 4, 4), off=n(o + 16, 4), link=n(o + 24, 4), info=n(o + 28, 4)))
    best = None
    for s in secs:
        if s["type"] != 0x6ffffffe:                    # SHT_GNU_verneed
            continue
        strs = secs[s["link"]]["off"]
        o = s["off"]
        for _ in range(s["info"]):
            cnt, aux, nxt = n(o + 2, 2), n(o + 8, 4), n(o + 12, 4)
            a = o + aux
            for _ in range(cnt):
                name_off, anext = n(a + 8, 4), n(a + 12, 4)
                e = data.index(b"\0", strs + name_off)
                m = re.fullmatch(rb"GLIBC_(\d+(?:\.\d+)+)", data[strs + name_off:e])
                if m:
                    v = tuple(int(x) for x in m.group(1).split(b"."))
                    best = max(best or v, v)
                if not anext:
                    break
                a += anext
            if not nxt:
                break
            o += nxt
    return ".".join(map(str, best)) if best else None


INSTALL_MD = """# {name} {ver}

{about}

This is the {gen} build of Frodhes for the Akai MPC (`{so}`). The Plugin Manager of the open-mpc catalogue
(https://open-mpc.github.io) installs it for you; this file is for installing it from a computer over SSH.
Frodhes' own guide: https://github.com/{repo}/blob/main/docs/MPC.md

## What you need

{needs}

## Install with the script

1. Unzip and copy the folder `{top}` to the MPC: `scp -r {top} root@<device-ip>:/tmp/`
2. Run `ssh root@<device-ip> sh /tmp/{top}/install.sh{targs}`

The script checks the device, stops MPC (save your project first), copies `portable/{skin}/` into
{synths_word}, backs up `MPC.settings`, adds Frodhes to MPC's plugin list (the line in `plugin-meta.xml`) and starts MPC
again. Run it again to upgrade. `-y` answers its question for you, `-t <folder>` names another Synths folder.

Then load **{name}** on a track from MPC's {where}.

## Remove it

`ssh root@<device-ip> sh /tmp/{top}/uninstall.sh` removes the folder and the plugin-list line (MPC restarts). Projects
that used Frodhes open without it.

## Install by hand

1. Copy `portable/{skin}/` to `{synths}/{skin}/`.
2. Stop MPC: `systemctl stop acvs` (where there is no `acvs` service: `systemctl stop inmusic-mpc`).
3. Make a copy of `MPC.settings` first.
4. In `MPC.settings`, inside `<VALUE name="{listkey}"><KNOWNPLUGINS>`, add the line from `plugin-meta.xml` with
   `%payload-path%` replaced by `{synths}` (no `{listkey}` value yet: add `<VALUE name="{listkey}"><KNOWNPLUGINS>`,
   the line, `</KNOWNPLUGINS></VALUE>` just before `</PROPERTIES>`).
5. Start MPC: `systemctl start acvs` (or `inmusic-mpc`). If MPC comes up with default settings, put your copy back.

On MPC OS 3.10.1 and later, copying `portable/{skin}/` into the drive's `VST2 Plugins` folder is enough: MPC
reads its `plugin.json`.

## Files

`SHA256SUMS` lists every file's sha256. Frodhes is free software (GPL-3.0-only); the source is at
https://github.com/{repo}.
"""
NEEDS = {
    "armv7": "- A first-generation MPC OS standalone (32-bit ARM: the Force, MPC Live / Live II, One, X, Key 61).\n"
             "- Root access over SSH (a modded unit) for the script or the manual install; the Plugin Manager needs none.",
    "aarch64": "- A Gen2 MPC OS standalone (64-bit ARM: MPC Live III and the other 64-bit models), MPC OS 3.x.\n"
               "- Root access over SSH (a modded unit) for the script or the manual install; the Plugin Manager needs none.\n"
               "- Its Synths folder: `/nvme/Synths` on a Live III, `/storage/Synths` on an MPC One (one of the folders under\n"
               "  `SynthContentLocations` in `/data/Settings/MPC/MPC.settings`). Use it where this file says\n"
               "  `<your Synths folder>`.",
}


def walk(top):
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            yield os.path.join(d, f)


def rel(p, start):
    return os.path.relpath(p, start).replace(os.sep, "/")


def build(folder, version, out, tpl):
    """the per-build zips from the plugin folder; returns {arch: zip path}"""
    fname = os.path.basename(os.path.normpath(folder))
    pj = json.load(open(os.path.join(folder, "plugin.json")))
    name, mfr = pj["name"], pj["manufacturer"]
    if fname != "%s - VST - %s" % (mfr, name):
        die("folder %r is not plugin.json's '%s - VST - %s'" % (fname, mfr, name))
    for need in ("version.xml", "Plugin Skins/TUI.json"):
        if not os.path.isfile(os.path.join(folder, need)):
            die("the plugin folder has no " + need)
    kind = "effect" if str(pj.get("kind", "")).lower() == "effect" else "instrument"
    builds = [(k, arch, pj[k]) for k, arch in ARCHES if pj.get(k)]
    if not builds:
        die("plugin.json names no file32 / file64")
    bins = {p for _, _, p in builds}
    about = pj.get("description", "")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    top = "%s-%s" % (slug, version)
    uid = uid_hex(pj["uid"])
    made = {}
    os.makedirs(out, exist_ok=True)
    for key, arch, so_path in builds:
        so_data = open(os.path.join(folder, so_path), "rb").read()
        if elf_arch(so_data) != arch:
            die("%s is not an %s ELF" % (so_path, arch), 1)
        stage = tempfile.mkdtemp()
        try:
            root = os.path.join(stage, top)
            pdir = os.path.join(root, "portable", fname)
            shutil.copytree(folder, pdir, symlinks=True)
            for b in bins - {so_path}:
                os.remove(os.path.join(pdir, b))
            for junk in (".DS_Store", "Thumbs.db"):
                for p in list(walk(pdir)):
                    if os.path.basename(p) == junk:
                        os.remove(p)
            esc = lambda t: str(t).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
            ins = int(pj.get("numInputs", 2 if kind == "effect" else 0))
            outs = int(pj.get("numOutputs", 2))
            cat = pj.get("category") or ("Effect" if kind == "effect" else "Synth")
            open(os.path.join(pdir, "plugin-meta.xml"), "w", newline="\n").write(
                '<PLUGIN name="%s" descriptiveName="%s" format="VST" category="%s" manufacturer="%s" version="%s" '
                'file="%%payload-path%%/%s/%s" uid="%s" isInstrument="%d" fileTime="0" infoUpdateTime="0" numInputs="%d" '
                'numOutputs="%d" isShell="0"/>\n' % (esc(name), esc(name), esc(cat), esc(mfr), esc(version), fname, so_path,
                                                     uid, kind == "instrument", ins, outs))
            sub = {"@NAME@": name, "@SO_NAME@": so_path, "@SKIN@": fname, "@EXTRAS@": "", "@VERSION@": version,
                   "@UID@": uid, "@LEGACY_SO@": "/sdcard/vst/" + os.path.basename(so_path), "@USER_DATA@": "",
                   "@RETIRE@": "", "@ID@": ID}
            for s in ("install.sh", "uninstall.sh"):
                t = tpl[s]
                for k, v in sub.items():
                    t = t.replace(k, v)
                open(os.path.join(root, s), "w", newline="\n").write(t)
                os.chmod(os.path.join(root, s), 0o755)
            open(os.path.join(root, "plugin_list.awk"), "w", newline="\n").write(tpl["plugin_list.awk"])
            gen2 = arch == "aarch64"
            synths = "<your Synths folder>" if gen2 else "/sdcard/Synths"
            open(os.path.join(root, "INSTALL.md"), "w", newline="\n").write(INSTALL_MD.format(
                name=name, ver=version, about=about, gen="Gen2 (64-bit, aarch64)" if gen2 else "Gen1 (32-bit ARM, armv7)",
                so=so_path, repo=REPO, needs=NEEDS[arch], top=top, targs=" -t '<your Synths folder>'" if gen2 else "",
                skin=fname, synths=synths, synths_word="your Synths folder" if gen2 else "`/sdcard/Synths`",
                listkey="pluginList-arm-64bit" if gen2 else "pluginList-arm",
                where="Instrument plugins" if kind == "instrument" else "Insert effects"))
            glibc = max_glibc(so_data)
            manifest = {"schema": 1, "id": ID, "name": name, "version": version, "kind": kind, "uid": uid,
                        "manufacturer": mfr, "layout": "portable", "so": so_path, "skin": fname,
                        "folder": "portable/" + fname, "extras": [], "user_data": [], "retire": [], "user_files": [],
                        "arch": arch, "max_glibc": glibc, "os_compat": ["3.x"],
                        "param_compat": int(version.split(".")[0]), "about": about, "requires": "",
                        "source_repo": REPO, "license": pj.get("license"), "cpu": None, "vst2": True}
            npj = {"name": name, "manufacturer": mfr, "version": version, "format": "vst2", "kind": kind,
                   "uid": "0x" + uid, "category": cat, "numInputs": ins, "numOutputs": outs, key: so_path}
            for k, v in (("description", about), ("license", pj.get("license")), ("url", "https://github.com/" + REPO)):
                if v:
                    npj[k] = v
            open(os.path.join(pdir, "plugin.json"), "w", newline="\n").write(json.dumps(npj, indent=2) + "\n")
            mj = json.dumps(manifest, indent=2) + "\n"
            for d in (root, pdir):
                open(os.path.join(d, "mpc-plugin.json"), "w", newline="\n").write(mj)
            modes = []
            for p in walk(pdir):
                if os.path.islink(p):
                    modes.append("l\t%s\t%s" % (rel(p, pdir), os.readlink(p)))
                elif os.stat(p).st_mode & stat.S_IXUSR:
                    modes.append("x\t" + rel(p, pdir))
            open(os.path.join(root, "MODES"), "w", newline="\n").write("\n".join(modes) + ("\n" if modes else ""))
            sums = ["%s  %s" % (sha(open(p, "rb").read()), rel(p, root)) for p in walk(root) if not os.path.islink(p)]
            open(os.path.join(root, "SHA256SUMS"), "w", newline="\n").write("\n".join(sums) + "\n")
            zpath = os.path.join(out, "%s-mpc-%s.zip" % (top, arch))
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in walk(root):
                    zi = zipfile.ZipInfo(rel(p, stage), date_time=DATE)
                    zi.create_system = 3
                    if os.path.islink(p):
                        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                        z.writestr(zi, os.readlink(p))
                    else:
                        zi.compress_type = zipfile.ZIP_DEFLATED
                        x = os.stat(p).st_mode & stat.S_IXUSR
                        zi.external_attr = (stat.S_IFREG | (0o755 if x else 0o644)) << 16
                        z.writestr(zi, open(p, "rb").read())
            made[arch] = zpath
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return made


def compare(a, b):
    """[difference lines] between two zips: names, modes, contents"""
    za, zb = zipfile.ZipFile(a), zipfile.ZipFile(b)
    ia, ib = {i.filename: i for i in za.infolist()}, {i.filename: i for i in zb.infolist()}
    out = ["only in %s: %s" % (os.path.basename(a), n) for n in sorted(set(ia) - set(ib))]
    out += ["only in %s: %s" % (os.path.basename(b), n) for n in sorted(set(ib) - set(ia))]
    for n in sorted(set(ia) & set(ib)):
        if ia[n].external_attr != ib[n].external_attr:
            out.append("mode differs: %s (%o / %o)" % (n, ia[n].external_attr >> 16, ib[n].external_attr >> 16))
        if za.read(n) != zb.read(n):
            out.append("content differs: %s" % n)
    if [i.filename for i in za.infolist()] != [i.filename for i in zb.infolist()] and not out:
        out.append("same files, another order")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=os.path.join(ROOT, "build", "mpc", FOLDER))
    ap.add_argument("--version")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "build", "mpc"))
    ap.add_argument("--templates")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--compare", nargs=2, metavar="ZIP")
    a = ap.parse_args(argv)
    if a.compare:
        d = compare(*a.compare)
        print("\n".join(d) if d else "identical: names, modes and contents")
        return 1 if d else 0
    if not a.version or not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        die("--version X.Y.Z")
    if not os.path.isdir(a.folder):
        die("no plugin folder %s (make mpc)" % a.folder)
    made = build(a.folder, a.version, a.out, templates(a.templates, a.fetch))
    for arch, z in made.items():
        print("%-7s %s  %.1f MB  sha256 %s" % (arch, os.path.relpath(z, ROOT), os.path.getsize(z) / 1e6,
                                             sha(open(z, "rb").read())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
