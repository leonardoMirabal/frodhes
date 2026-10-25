#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""Build Frodhes (on Dinghy / Felucca / SLOOP's platform): the app, the update loader and an installable .fwsc package.

  tools/build.py [--release X.Y.Z] [--board fm1|smk37] [--model pro|elite|mke]

Outputs in build/: frodhes.bin (app), loader/ota.bin (update loader),
frodhes.fwsc (package). See README.md for the toolchain and the SDK.

--board smk37 (or BOARD=smk37) builds for the M-Vave SMK-37 (docs/SMK37.md): -DBOARD_SMK37=1 for the app and the
loader, the app at 7 voices (-DFR_VOICES=7u), identity SMK-37 Pro_470 (every version, as the FM-1's FM-1_958: PR #3's,
so a unit that ran 0.9.2-SMK updates in place; not tested on hardware), the store family FRODHES (the owner tag), the
SMK flash layout (fm1pkg_make.py, app slot 0x97400 = 619,520 B), the package build/frodhes-smk37.fwsc. --model elite /
mke (or MODEL=) with smk37: the same build under the SMK-37 Elite's or the MKE-P37's model name (identity SMK-37
Elite_470 / MKE-P37_470, the loader's and the update record's name too; -DSMK_MODEL), so M-Vave's updater still takes
that unit's factory package: frodhes-smk37-elite.fwsc, -mke.fwsc. The default stays the FM-1, built as before
(FM-1_958, 9 voices).

The JieLi toolchain is Linux x86-64 only. JIELI_TOOLCHAIN points at it; on
macOS (or with JIELI_DOCKER=1) each tool runs in a linux/amd64 container.
"""
import argparse
import fcntl
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
FW = SRC / "firmware"
OUT = SRC / "build"
GEN = OUT / "gen"
LDR = OUT / "loader"
sys.path.insert(0, str(SRC / "tools"))
import fm1pkg_make  # noqa: E402
import lz4blk  # noqa: E402

APP_XIP = 0x02000120                # app.bin offset 0 in the XIP map; the SPL jumps here
APP_SLOT = fm1pkg_make.APP_SLOT
LOADER_LOAD = 0x01C0A800
LOADER_NAME = b"usb_hid_ota.bin"    # the file name the SPL looks for
DOCKER_IMAGE = os.environ.get("JIELI_DOCKER_IMAGE", "debian:bookworm-slim")
CFLAGS = ["-Os", "-ffunction-sections", "-fno-builtin", "-Wall", "-Wno-unused-function"]   # keep -Os: with -O2 (unity
                                    # build, usb.c inside) the FM-1's USB-MIDI IN babbled on the device (OberVave, 2026-10-13)
APP_OPT = []                        # --o2 (the O13 probe, never a release): the app at -O2 without unrolling (plain
                                    # -O2 overflows the XIP slot by 8.7 KB); the loader stays -Os
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t(.*)$")

# SDK files of AC79NN_SDK_V1.2.1_2023-12-13 (the tested version)
SDK_SHA256 = {
    "uboot.boot": "4e3b4c220dc96641cb5a723f41e68ce41d5261ae9434bb33fbd7f2c59976ded4",
    "cfg_tool.bin": "276579954f076886a6a7694f65dc71c034a63a2c204b76749065c0ac7b010d1b",
    "cfg/eq_cfg_hw.bin": "41167491bffed4651750719c973d2758adeb9021a5670d02d6a53c85ed80ea7d",
}

BOARD = "fm1"                       # fm1 (default) or smk37: BOARD env or --board
BOARD_PREFIX = {"smk37": "SMK-37 Pro_47"}   # Frodhes' SMK-37 identity: prefix + 0 = SMK-37 Pro_470 for every version
                                    # and model name (PR #3's; Dinghy's own is _99N); the FM-1's is PRODUCT below
BOARD_DEFS = {"fm1": [], "smk37": ["-DBOARD_SMK37=1"]}
BOARD_APP_DEFS = {"fm1": [], "smk37": ["-DFR_VOICES=7u"]}   # the app only (not the loader): the SMK at 7 voices
MODELS = {"pro": "SMK-37 Pro", "elite": "SMK-37 Elite", "mke": "MKE-P37"}   # smk37: one keyboard, three names
DATA_FAMILY = "FRODHES"             # the SMK store's owner tag (store.h): 1..7 of A-Z 0-9
SCREEN_NAME = "FRODHES"             # the update loader's screen (FM-1, ldr_screen.c): the name and its colour (RGB565)
SCREEN_COLOUR = 0xE3C1              # AMBER, the screen's default accent (ui.c COLOR step 0)
PRODUCT = "FM-1_958"                # Frodhes, every version and dev builds: one identity (the hub's installer wants
                                    # FM-1_9NN; 958 is not used by another firmware on thefirmwarehub.com, 2026-10).
                                    # The version is only text (APP_VER), so releases never run out of numbers
VERSION = None                      # APP_VER for release builds


def toolchain():
    tc = os.environ.get("JIELI_TOOLCHAIN")
    if not tc or not (Path(tc) / "pi32v2" / "bin" / "clang").exists():
        raise SystemExit("JIELI_TOOLCHAIN must point at the JieLi Linux toolchain "
                         "(the directory with pi32v2/ and common/; see tools/get_toolchain.sh)")
    return Path(tc).resolve()


def use_docker():
    native = platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64")
    return os.environ.get("JIELI_DOCKER", "0" if native else "1") == "1"


# Docker on a Mac runs the x86 toolchain emulated: many builds at once (one per worktree) load the whole machine. So a
# build holds one lock shared by every worktree of the repo (in the common .git), and each container gets DOCKER_CPUS.
# main() takes it before any thread starts (two threads of one process each taking it would wait on each other).
DOCKER_CPUS = os.environ.get("JIELI_DOCKER_CPUS", "2")
_docker_lock = None


def docker_lock():
    global _docker_lock
    if _docker_lock is not None or not use_docker():
        return
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=SRC, capture_output=True, text=True)
    common = Path(SRC, r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else Path(SRC, "build")
    common.mkdir(parents=True, exist_ok=True)
    _docker_lock = open(common / "frodhes-docker.lock", "w")
    try:
        fcntl.flock(_docker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("build: another worktree's Docker build is running; waiting for it", file=sys.stderr, flush=True)
        fcntl.flock(_docker_lock, fcntl.LOCK_EX)


def tc(tool, *args):
    """run a toolchain binary (pi32v2/bin/..., common/bin/...) with cwd SRC; paths relative to SRC"""
    rel = [str(Path(a).resolve().relative_to(SRC)) if isinstance(a, Path) else a for a in args]
    if tool == "cc":                # the toolchain's cc wrapper needs python3; call clang directly
        tool, rel = "pi32v2/bin/clang", ["-target", "pi32v2", *rel]
    if use_docker():
        docker_lock()
        cmd = ["docker", "run", "--rm", "--platform", "linux/amd64", "--cpus", DOCKER_CPUS, "-v", f"{SRC}:/work",
               "-v", f"{toolchain()}:/opt/jieli:ro", "-w", "/work", DOCKER_IMAGE, f"/opt/jieli/{tool}", *rel]
    else:
        cmd = [str(toolchain() / tool), *rel]
    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"build: {tool} failed")
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    return r.stdout


def tc_all(*cmds):
    with ThreadPoolExecutor(min(len(cmds), 2) if use_docker() else len(cmds)) as ex:
        return list(ex.map(lambda c: tc(*c), cmds))


# ---- update loader

def crc16(d, c=0):
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else c << 1
        c &= 0xFFFF
    return c


def ldr_head(dcrc, a, b, attr, name):
    body = struct.pack("<HIIBBH16s", dcrc, a, b, attr, 0, 0, name)
    return struct.pack("<H", crc16(body)) + body


def ldr_wrap(image):
    """ota.bin = outer JLFS head (32 B) + inner head (32 B, load address) +
    blocks [clen u32][dlen u32][LZ4 block], dlen 4096 except the last"""
    blocks = bytearray()
    for i in range(0, len(image), 4096):
        raw = image[i:i + 4096]
        c = lz4blk.compress(raw)
        if len(c) > 4096 or lz4blk.decompress(c) != raw:
            raise SystemExit(f"loader: block {i // 4096} does not compress below 4096 B")
        blocks += struct.pack("<II", len(c), len(raw)) + c
    inner = ldr_head(crc16(image), len(image), LOADER_LOAD, 0, LOADER_NAME)
    body = inner + bytes(blocks)
    return ldr_head(crc16(body), 0x20, len(body), 0x41, LOADER_NAME) + body


def build_loader():
    LDR.mkdir(parents=True, exist_ok=True)
    src = FW / "loader"
    flags = [*CFLAGS, *BOARD_DEFS[BOARD], "-Ifirmware/hal", "-Ifirmware/src", f'-DLDR_NAME="{SCREEN_NAME}"',
             f"-DLDR_COLOUR={SCREEN_COLOUR:#x}u", '-DLDR_FOOT=""']   # (no site name at the foot: the no-brand rule)
    tc_all(("cc", "-c", src / "crt0_ldr.S", "-o", LDR / "crt0_ldr.o"),
           ("cc", *flags, "-c", src / "loader.c", "-o", LDR / "loader.o"))
    elf = LDR / "loader.elf"
    tc("pi32v2/bin/ld", "--gc-sections", "-e", "_start", "-T", src / "loader.ld",
       LDR / "crt0_ldr.o", LDR / "loader.o", "-o", elf)
    _, dis, hdr, syms = tc_all(("common/bin/objcopy", "-O", "binary", "-j", ".text", elf, LDR / "loader.bin"),
                               ("common/bin/objdump", "-d", elf),
                               ("common/bin/objdump", "-h", elf),
                               ("common/bin/objdump", "-t", elf))
    (LDR / "loader.dis").write_text(dis)
    for ln in hdr.splitlines():     # the loader rewrites the flash: nothing may run from XIP
        p = ln.split()
        if len(p) > 4 and p[1].startswith(".") and int(p[3], 16) >= 0x02000000 and int(p[2], 16):
            raise SystemExit(f"loader: section {p[1]} at {p[3]} is outside RAM")
    image = (LDR / "loader.bin").read_bytes()
    if not image or len(image) > 0x14000:
        raise SystemExit("loader: image empty or too big")
    ota = ldr_wrap(image)
    (LDR / "ota.bin").write_bytes(ota)
    bss = [int(ln.split()[0], 16) for ln in syms.splitlines() if ln.rstrip().endswith("_bss_end")]
    print(f"loader: image {len(image)} B at {LOADER_LOAD:#x}" + (f", bss end {bss[0]:#x}" if bss else ""))
    print(f"loader: ota.bin {len(ota)} B")
    return ota


# ---- app

def build_app():
    flags = [*CFLAGS, *APP_OPT, *BOARD_DEFS[BOARD], *BOARD_APP_DEFS[BOARD], "-Ifirmware/hal", "-Ifirmware/src", "-Ifirmware/gen",
             f'-DFELUCCA_ID="{PRODUCT}"', f'-DAPP_VER="{VERSION or "0.0"}"']
    if BOARD == "smk37":
        if not re.fullmatch(r"[A-Z0-9]{1,7}", DATA_FAMILY):
            raise SystemExit("DATA_FAMILY: 1..7 of A-Z 0-9")
        flags.append(f'-DDATA_FAMILY="{DATA_FAMILY}"')
    flags += os.environ.get("APP_DEFS", "").split()      # developer builds only, e.g. APP_DEFS=-DRH_PROF_STAGES
    flags += os.environ.get("DINGHY_DEFS", "").split()   # (a test build: DINGHY_DEFS=-DFELUCCA_UAC=0 ./build.sh)
    cmds = [("cc", "-c", FW / "crt0.S", "-o", OUT / "crt0.o"),
            ("cc", "-c", FW / "hal" / "fm1_vec.S", "-o", OUT / "fm1_vec.o"),
            ("cc", "-c", FW / "hal" / "fm1_isr.S", "-o", OUT / "fm1_isr.o"),
            ("cc", *flags, "-c", FW / "src" / "frodhes.c", "-o", OUT / "frodhes.o")]
    tc_all(*cmds)
    elf = OUT / "frodhes.elf"
    ld = FW / "app.ld"
    if APP_SLOT != 0x8DFBC:          # another board's app slot (SMK-37): the same script, its XIP length
        txt = ld.read_text()
        if txt.count("LENGTH = 0x8DFBC") != 1:
            raise SystemExit("app.ld: the XIP LENGTH line was not found")
        ld = OUT / "app.ld"
        ld.write_text(txt.replace("LENGTH = 0x8DFBC", f"LENGTH = {APP_SLOT:#X}".replace("0X", "0x")))
    tc("pi32v2/bin/ld", "--gc-sections", "-e", "_start", "-T", ld, OUT / "crt0.o", OUT / "fm1_vec.o",
       OUT / "fm1_isr.o", OUT / "frodhes.o", "-o", elf)
    for sect in ("text.bin", "data.bin", "ramtext.bin"):
        (OUT / sect).unlink(missing_ok=True)
    *_, syms, dis, rt = tc_all(("common/bin/objcopy", "-O", "binary", "-j", ".text", elf, OUT / "text.bin"),
                               ("common/bin/objcopy", "-O", "binary", "-j", ".data", elf, OUT / "data.bin"),
                               ("common/bin/objcopy", "-O", "binary", "-j", ".ram_text", elf, OUT / "ramtext.bin"),
                               ("common/bin/objdump", "-t", elf),
                               ("common/bin/objdump", "-d", elf),
                               ("common/bin/objdump", "-d", "-j", ".ram_text", elf))
    (OUT / "frodhes.dis").write_text(dis)

    def symv(name):
        return int(re.search(r"^([0-9a-f]+) .*\s" + name + r"$", syms, re.M).group(1), 16)
    img = bytearray((OUT / "text.bin").read_bytes())
    # .ram_text and .data follow .text at their load addresses; crt0 copies them by words
    for sect, lname in (("ramtext.bin", "_rt_load"), ("data.bin", "_data_load")):
        load = symv(lname)
        if load % 4:
            raise SystemExit(f"{lname} {load:#x} is not word aligned")
        blob = (OUT / sect).read_bytes() if (OUT / sect).exists() else b""
        if blob:
            if load - APP_XIP < len(img):
                raise SystemExit(f"{lname} overlaps the image")
            img += b"\xff" * (load - APP_XIP - len(img))
            img += blob
    img += b"\xff" * (-len(img) % 4)
    (OUT / "frodhes.bin").write_bytes(img)
    return bytes(img), syms, dis, rt


AUDIO_HELPERS = ("rh_render", "rh_mode_f1_l", "rh_mode_f", "rh_mode_p1_l", "rh_mode_p", "rh_pickup", "rh_pickup_s", "rh_pickup_fx",
                 "rh_voice_sub", "rh_df1", "rh_df1_s", "rh_df1_sq", "rh_q15")   # rhodes.c noinline (called from app_audio)


def hot_loops(dis, fn="app_audio"):
    """the innermost loops of app_audio with its noinline helpers (AUDIO_HELPERS), or of the function fn alone (a
    backward branch with no other one inside it): dicts with the instruction
    count, stack stores / loads, the error-feedback masks (& 0x3FFFFFFF), the 64-bit >>= 30 shifts (the Rhodes MC3
    mode loops have two), the 64-bit >>= 22 shifts (the pickup's Hermite has three) and calls. None if app_audio is not in the disassembly"""
    body = re.search(r"^" + fn + r":\n(.*?)\n\n[A-Za-z_][\w.]*:\n", dis, re.S | re.M)
    if not body:
        return None
    text = body.group(1)
    for h in AUDIO_HELPERS if fn == "app_audio" else ():   # the audio path's noinline functions count as app_audio's
        mm = re.search(r"^" + h + r":\n(.*?)\n\n[A-Za-z_][\w.]*:\n", dis, re.S | re.M)
        if mm:
            text += "\n" + mm.group(1)
    lines = [(int(mm.group(1), 16), mm.group(3)) for mm in (LINE.match(ln) for ln in text.splitlines()) if mm]
    br = []
    for a, t in lines:
        mm = re.search(r"goto -\d+ <[^:>]*: ([0-9a-f]+) >", t)
        if mm:
            br.append((int(mm.group(1), 16), a))
    loops = []
    for lo, hi in br:
        if any(lo <= lo2 and hi2 < hi for lo2, hi2 in br if (lo2, hi2) != (lo, hi)):
            continue
        loop = [t for a, t in lines if lo <= a <= hi]
        loops.append(dict(
            lo=lo, n=len(loop),
            st=sum(bool(re.search(r"(?:^|\s)[dh]?\[sp(?:\+\d+)?\] = ", t)) for t in loop),   # [sp] too
            ld=sum(bool(re.search(r"= [dh]?\[sp(?:\+\d+)?\]", t)) for t in loop),
            masks=sum("& 0x3FFFFFFF" in t for t in loop),
            sh22=sum(bool(re.search(r"r\d+_r\d+ >>= 22", t)) for t in loop),
            sh30=sum(bool(re.search(r"r\d+_r\d+ >>= 30", t)) for t in loop),
            calls=[c.strip() for t in loop for c in re.findall(r"call -?\d+ <([^:>]*)", t)]))
    return loops


MODE_MAX = 32       # the Rhodes MC3 mode loops (RHODES-CPU-PLAN R1: 39 -> 29 insns); a spill there cost ~35 us
# the bar engine's loops (bars.c, 0.9.5 bars-10): function, the most instructions its inner loop may take (a turn: 4
# samples for the upper slots, 2 for the pair, 1 for the output and the pulse), what it is. bars.c's cost model
# (BAR_NS_*) is derived from these counts: a loop that grows past its limit fails the build
BAR_LOOPS = [("bar_run_lo", 32, "upper slot Q32, 4 samples"), ("bar_run_hi", 32, "upper slot Q30, 4 samples"),
             ("bar_run_lo_f", 40, "upper slot Q32 + force, 4 samples"),
             ("bar_run_hi_f", 40, "upper slot Q30 + force, 4 samples"),
             ("bar_pair_0", 58, "V1 + tube pair, 2 samples"), ("bar_pair_1", 70, "V1 + tube pair + force, 2 samples"),
             ("bar_pair_0h", 70, "pair, the tube in Q30, 2 samples"),
             ("bar_pair_1h", 72, "pair + force, the tube in Q30, 2 samples"),
             ("bar_out_rad", 11, "output + radiation"), ("bar_out", 7, "output"),
             ("bar_fade_rad", 21, "output + radiation, steal fade"), ("bar_fade", 17, "output, steal fade"),
             ("bar_felt_pass", 15, "felt noise"), ("bar_force", 16, "the pulse")]


def check(img, syms, dis, rt):
    errors, notes = [], []
    m = re.search(r"^([0-9a-f]+) .*\s_start$", syms, re.M)
    if not m or int(m.group(1), 16) != APP_XIP:
        errors.append(f"_start is not at {APP_XIP:#x}")
    if img[:4] != bytes.fromhex("04818000"):
        errors.append(f"image starts with {img[:4].hex()}, not the entry stub")
    rt_calls = [ln for ln in rt.splitlines() if re.search(r"\bcall\b", ln)]
    if rt_calls:                    # RAM code runs with the flash off: no calls into XIP
        errors.append(f".ram_text contains calls: {rt_calls[:3]}")
    else:
        notes.append(f".ram_text: {len([ln for ln in rt.splitlines() if LINE.match(ln)])} insns, no calls")
    for ln in dis.splitlines():     # nothing may call or load an address in the chip ROM
        mm = LINE.match(ln)
        if not mm:
            continue
        for v in re.findall(r"= (-?\d+) <|call -?\d+ <[^:>]*: ([0-9a-f]+) >", mm.group(3)):
            val = (int(v[0]) & 0xFFFFFFFF) if v[0] else int(v[1], 16) & 0xFFFFFFFF
            if 0xFFC00000 <= val < 0xFFD00000:
                errors.append(f"reference to ROM address {val:#010x}")
    if len(img) > APP_SLOT:
        errors.append(f"image {len(img)} B exceeds the app slot")
    loops = hot_loops(dis)
    mode = [lp for lp in loops or [] if lp["sh30"] == 2]      # out of line since 0.9.5 (rh_mode_*: AUDIO_HELPERS); the
                                    # two Q30 shifts (x, y) of a sample: since rh-trim the remainders' masks are out of
                                    # the loop (X = x 2^30 + ex)
    if not mode:
        errors.append("the Rhodes mode loop (MC3) not found in app_audio and its helpers")
    for lp in mode:
        msg = f"Rhodes mode loop: {lp['n']} insns, {lp['st']} stack stores, {lp['ld']} stack loads"
        if lp["st"] or lp["n"] > MODE_MAX:
            errors.append(msg + f" (limit {MODE_MAX} / 0: a global-merge split of rh_F / rh_x? see engine.c eq)")
        else:
            notes.append(msg)
    for lp in loops or []:          # the pickup loop(s): the Hermite (inlined, 3 shifts) or its call
        if lp["sh22"] >= 3 or "rh_hermite" in lp["calls"]:
            notes.append(f"Rhodes pickup loop: {lp['n']} insns, {lp['st']} stack stores, {lp['ld']} stack loads"
                         + (f", calls {' '.join(lp['calls'])}" if lp["calls"] else ""))
    for fn, lim, what in BAR_LOOPS:     # the bar engine's (VIBES / MARIMBA) hot loops, each out of line in bars.c
        lps = hot_loops(dis, fn) or []
        if not lps:
            errors.append(f"the bar loop {fn} not found ({what})")
            continue
        lp = max(lps, key=lambda x: x["n"])
        msg = f"bar {what} ({fn}): {lp['n']} insns, {lp['st']} stack stores, {lp['ld']} stack loads"
        if lp["n"] > lim or (lim <= 40 and lp["st"]):
            errors.append(msg + f" (limit {lim}" + (" / no stack stores" if lim <= 40 else "") + ": a register spill?)")
        else:
            notes.append(msg)

    def sym(name):
        mm = re.search(r"^([0-9a-f]+) .*\s" + name + r"$", syms, re.M)
        return int(mm.group(1), 16) if mm else 0
    ram = sym("_bss_end") - 0x01C08000
    pool = sym("_pool_end") - 0x01C20000
    notes.append(f"image {len(img)} B of {APP_SLOT}; RAM .data+.bss {ram} B of 98304; pool {pool} B of 344064")
    # the fonts' tables in the image (UI-SIMPLIFY: the flash they take; a face the code no longer uses is gone):
    # AF_<N>* (aa_font.h, gen_aa_font.py) and FONT_<N>* (felucca_font.h, gfx.c), by objdump -t sizes
    font = {}
    for mm in re.finditer(r"^[0-9a-f]+ .*\s([0-9a-f]{8})\s+((?:AF|FONT)_[A-Z]\d*)(?:_\w+)?$", syms, re.M):
        font[mm.group(2)] = font.get(mm.group(2), 0) + int(mm.group(1), 16)
    notes.append("fonts " + ", ".join(f"{k} {v}" for k, v in sorted(font.items())) + f" = {sum(font.values())} B")
    if ram > 96 * 1024:
        errors.append(f"RAM .data+.bss {ram} B > 96 KiB")
    return errors, notes


# Every hardware register access lives in hal/. In src/ and loader/ (comments stripped):
#  - no volatile pointer cast, except of a C object's address (`*(volatile T *)&x`: a read-once
#    of a RAM flag shared with an ISR, e.g. usb.c ota_wire_send);
#  - no literal in a register or reserved window (core SFRs 0x10000-0x13FFF, SFC 0x40000-0x43FFF,
#    GPIO/IOMAP 0x50000-0x51FFF, CPU 0x1EE0000-0x1EEFFFF, RAM top 0x01C7F000- (boot info,
#    mailbox, vectors), XIP 0x02000000-0x020FFFFF);
#  - no inline asm, except the empty compiler barrier RING_PUBLISH() (emits no instruction);
#  - no HAL register macro (a hal/ #define that is, or expands to, a volatile access).
# Linker symbols (_bss_start[], _rt_load[], ...) are plain C objects and pass.
MMIO_LIT = re.compile(r"\b0x0*(1[0-3][0-9a-f]{3}|4[0-3][0-9a-f]{3}|5[01][0-9a-f]{3}|1ee[0-9a-f]{4}|"
                      r"1c7f[0-9a-f]{3}|20[0-9a-f]{5})u?l?\b", re.I)
MMIO_CAST = re.compile(r"\(\s*(?:const\s+)?volatile\b[^()]*\*\s*\)(?!\s*&)")
MMIO_ASM = re.compile(r"\b(?:__asm__|asm)\b(?!\s+volatile\s*\(\s*\"\"\s*:::\s*\"memory\"\s*\))")


def mmio_check():
    def strip(s):
        s = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), s, flags=re.S)
        return re.sub(r"//[^\n]*", "", s)
    defs = {}
    for h in sorted((FW / "hal").glob("*.h")):
        for m in re.finditer(r"^#define\s+(\w+)(?:\([^)]*\))?\s+(.*)$", strip(h.read_text()), re.M):
            defs[m.group(1)] = m.group(2)
    regs = {n for n, b in defs.items() if re.search(r"\bvolatile\b", b)}
    while True:                                   # macros built on register macros (FM1_WR_LIMIT_H -> FM1_X2)
        more = {n for n, b in defs.items() if n not in regs and set(re.findall(r"\w+", b)) & regs}
        if not more:
            break
        regs |= more
    errors = []
    for f in sorted([*(FW / "src").glob("*.[ch]"), *(FW / "loader").glob("*.c")]):
        for no, ln in enumerate(strip(f.read_text()).splitlines(), 1):
            where = f"{f.relative_to(FW)}:{no}"
            for rx, what in ((MMIO_LIT, "register/window address"), (MMIO_CAST, "volatile pointer cast"),
                             (MMIO_ASM, "inline asm")):
                m = rx.search(ln)
                if m:
                    errors.append(f"{where}: {what} outside hal/ ({m.group(0).strip()}); add a hal/ helper")
            used = set(re.findall(r"\b[A-Z_][A-Z0-9_]*\b", ln)) & regs
            if used:
                errors.append(f"{where}: HAL register macro {sorted(used)[0]} outside hal/; use a hal/ helper")
    return errors


def package(board="fm1", model="pro", release=None):
    """(identity, file name) of a build. The FM-1's identity is PRODUCT for every version and dev build (the version
    is only text); a board's model name (SMK-37) changes its identity and the file name"""
    suffix = "" if board == "fm1" else "-" + board
    ident = PRODUCT
    if board != "fm1":
        ident = BOARD_PREFIX[board].replace(MODELS["pro"], MODELS[model]) + "0"
        if model != "pro":
            suffix += "-" + model
    return ident, f"frodhes-{release}{suffix}.fwsc" if release else f"frodhes{suffix}.fwsc"


def main():
    global PRODUCT, VERSION, BOARD, APP_SLOT, APP_OPT
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", metavar="X.Y.Z", help="release build: version string X.Y.Z, file frodhes-X.Y.Z.fwsc")
    ap.add_argument("--board", choices=sorted(BOARD_DEFS), default=None,
                    help="target board: fm1 (default) or smk37 (default: $BOARD, else fm1)")
    ap.add_argument("--model", choices=sorted(MODELS), default=None,
                    help="smk37: the model name, pro (default), elite or mke (default: $MODEL, else pro)")
    ap.add_argument("--sdk", type=Path, help="JieLi AC79 SDK checkout (default: $AC79_SDK)")
    ap.add_argument("--o2", action="store_true",
                    help="probe: the app at -O2 -fno-unroll-loops instead of -Os (not with --release; the USB-MIDI "
                         "babbled at -O2 once, 2026-10-13: run the USB checks on such a build)")
    a = ap.parse_args()
    if a.o2:
        if a.release:
            raise SystemExit("--o2 is a probe: not with --release")
        APP_OPT = ["-O2", "-fno-unroll-loops"]
    docker_lock()
    BOARD = (a.board or os.environ.get("BOARD") or "fm1").lower().replace("-", "")
    if BOARD not in BOARD_DEFS:
        raise SystemExit(f"BOARD={BOARD}: use fm1 or smk37")
    model = (a.model or os.environ.get("MODEL") or "pro").lower()
    if model not in MODELS or (model != "pro" and BOARD != "smk37"):
        raise SystemExit(f"MODEL={model}: pro, elite or mke, with BOARD=smk37")
    fm1pkg_make.set_board(BOARD)
    APP_SLOT = fm1pkg_make.APP_SLOT
    if model != "pro":              # another model name: identity, loader and update record (fm1_cc.h SMK_MODEL)
        BOARD_DEFS[BOARD] = [*BOARD_DEFS[BOARD], f'-DSMK_MODEL="{MODELS[model]}"']
    if a.release:
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}(\.\d{1,3})?(-[A-Za-z0-9]+)?", a.release):
            raise SystemExit(f"--release {a.release}: use X.Y, X.Y.Z or X.Y.Z-suffix")
        VERSION = a.release
    PRODUCT, name = package(BOARD, model, a.release)
    fm1pkg_make.SDK = a.sdk
    for rel, sha in SDK_SHA256.items():          # fail early without the SDK
        if hashlib.sha256(fm1pkg_make.sdk_file(rel)).hexdigest() != sha:
            print(f"warning: SDK {rel} differs from AC79NN_SDK_V1.2.1; the package will not match the reference")
    OUT.mkdir(parents=True, exist_ok=True)
    ota = build_loader()
    img, syms, dis, rt = build_app()
    errors, notes = check(img, syms, dis, rt)
    hal_err = mmio_check()
    errors += hal_err
    if not hal_err:
        notes.append("register access: hal/ only (src/, loader/ clean)")
    if APP_OPT:
        notes.append("app built with " + " ".join(APP_OPT) + " (--o2 probe)")
    for n in notes:
        print("  ok   ", n)
    for e in errors:
        print("  FAIL ", e)
    if errors:
        raise SystemExit("build: checks failed")
    pkg = fm1pkg_make.ufw(fm1pkg_make.flash_image(img, fm1pkg_make.KEY), ota, PRODUCT)
    (OUT / name).write_bytes(pkg)
    print(f"app      {OUT / 'frodhes.bin'}  {len(img)} B")
    print(f"loader   {LDR / 'ota.bin'}  {len(ota)} B")
    print(f"package  {OUT / name}  {len(pkg)} B, identity {PRODUCT}" + (f", board {BOARD}" if BOARD != "fm1" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
