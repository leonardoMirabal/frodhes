#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""tools/fm1_install.py against a simulated FM-1 (no hardware, no mido).
The fake device is the one in web/test_web.mjs: identity on the handshake,
then "device asks, host answers" reads of the logical image. Run from the repo root:
  python3 tests/install_test.py
Also checks logical_image/product_of against web/fm1pkg.js (needs node, web/ and
build/dinghy.fwsc, skipped otherwise: Dinghy has no web installer of its own; from genkigenki/sloop-fm1 smk37), the SMK-37 Pro identities, and the 20 / 36 block
deinterleave against the stock SMK-37 Pro packages (a directory named by SMK_STOCK_DIR, skipped when unset)."""
import io
import json
import os
import socket
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_install as I  # noqa: E402

I.DELAY.update(open=0, start=0.01, reply=0, loader=0.01, reboot=0.01, retry=0.02, poll=0.05, hs=0.1,
               idle_check=0.4, idle_write=0.4, wait_loader=2, wait_reboot=2)
failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


# ---------------------------------------------------------- simulated FM-1 ---

class FakeLink:
    def __init__(self, dev, gen):
        self.dev, self.gen, self.q, self.closed = dev, gen, queue.Queue(), False

    @property
    def lost(self):
        return self.gen != self.dev.gen or not self.dev.connected

    def send(self, pkt):
        if self.lost:
            return False
        self.dev.sent.append(bytes(pkt))
        self.dev.rx(bytes(pkt))
        return True

    def read(self, timeout):
        try:
            return self.q.get(timeout=max(timeout, 0))
        except queue.Empty:
            return None

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def close(self):
        self.closed = True


class FakeFM1:
    """a MIDI backend with one FM-1 on it"""

    def __init__(self, image, identity="FM-1_015", name="FM-1", unplug_after=None, after_write="FM-1_900",
                 stall_after=None, bad_addr=None, loader=("ota-FM-1_900", "Felucca Update"), after_name="Felucca"):
        self.image, self.unplug_after, self.after_write, self.after_name = image, unplug_after, after_write, after_name
        self.loader = loader
        self.stall_after, self.bad_addr = stall_after, bad_addr
        self.served = self.bad = self.upgrades = 0
        self.sent, self.links, self.gen, self.lock = [], [], 0, threading.Lock()
        self.boot(identity, name)

    def boot(self, identity, name):
        with self.lock:
            self.gen += 1
            self.identity, self.name, self.connected = identity, name, True
            self.waiting, self.queue = None, []

    def input_names(self):
        return [self.name] if self.connected else []

    output_names = input_names

    def open(self, in_name, out_name):
        if in_name != self.name or not self.connected:
            raise IOError("no such port")
        link = FakeLink(self, self.gen)
        self.links.append(link)
        return link

    def tx(self, pkt):
        for link in self.links:
            if link.gen == self.gen and not link.closed:
                link.q.put(bytes(pkt))

    def rx(self, d):
        if d == I.HS_QUERY:
            t = self.identity.encode()
            self.tx(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + t + bytes(28 - len(t))) + b"\xF7")
        elif d == I.UPGRADE:
            self.upgrades += 1
            first = self.bad_addr if self.bad_addr is not None else 0
            self.queue = ([(first + k * 512, 512) for k in range(6)] + [(I.FINISH_WRITE, 8)]
                          if self.identity.startswith("ota-")
                          else [(0, 64), (0x40, 160), (0x1000, 512), (I.FINISH_CHECK, 8)])
            self.next()
        elif self.waiting:
            u = I.unpack7(d[1:-1])
            addr, n = self.waiting
            fin = addr >= I.FINISH_CHECK
            want = b"success\0" if fin else self.image[addr:addr + n]
            if u[14:14 + len(want)] != want or len(u) != 15 + len(want):
                self.bad += 1
            self.waiting = None
            self.served += 1
            if self.unplug_after and self.served >= self.unplug_after:
                self.connected = False
                return
            if self.stall_after and self.served >= self.stall_after:
                return
            if addr == I.FINISH_CHECK:
                threading.Timer(0.05, self.boot, self.loader).start()
            elif addr == I.FINISH_WRITE:
                threading.Timer(0.05, self.boot, (self.after_write, self.after_name)).start()
            else:
                self.next()

    def next(self):
        if not self.queue:
            return
        self.waiting = addr, n = self.queue.pop(0)
        u = bytearray([0, 0x59, 0x30, 0, 0, 0, 0]) + addr.to_bytes(4, "little") + bytes([n & 0xFF, n >> 8, 0])
        u.append(~sum(u[6:14]) & 0xFF)
        self.tx(b"\xF0" + I.pack7(u) + b"\xF7")


# ----------------------------------------------------------------- helpers ---

def package(product="FM-1_900", marker=True, size=0x2000, damage=False, blocks=20, seed=7):
    """a package with a valid header and file list (one file: 0x400 to the end), as fm1pkg_make writes"""
    img = bytearray((i * seed) & 0xFF for i in range(size))
    img[0x1800:0x1800 + 16] = I.LOADER_MARK if marker else bytes(16)
    data = bytes(img[0x400:])
    e = bytearray(0x50)
    e[0:16] = (0).to_bytes(2, "little") + (0).to_bytes(2, "little") + I._crc16(data).to_bytes(2, "little") + bytes(2) + \
        (0x400).to_bytes(4, "little") + len(data).to_bytes(4, "little")
    e[0x40:0x49] = b"flash.bin"
    lst = I._dec(bytes(e))
    hdr = bytearray(0x40)
    hdr[4:8] = size.to_bytes(4, "little")
    hdr[8:10] = (1).to_bytes(2, "little")
    hdr[2:4] = I._crc16(lst).to_bytes(2, "little")
    hdr[0:2] = I._crc16(bytes(hdr[2:0x40])).to_bytes(2, "little")
    img[0:0x40] = I._dec(bytes(hdr))
    img[0x40:0x90] = lst
    if damage:
        img[0x1000] ^= 1
    raw = bytearray()
    for i in range(blocks):
        m = (ord(product[i]) + i + 1) & 0xFF if i < len(product) else 0x7D
        raw += img[i * I.KEEP:(i + 1) * I.KEEP] + bytes([m])
    raw += img[blocks * I.KEEP:]
    return bytes(raw)


TMP = Path(tempfile.mkdtemp(prefix="dinghy-install-"))


def pkgfile(name, raw):
    p = TMP / name
    p.write_bytes(raw)
    return str(p)


def cli(args, dev, answer=True):
    out, err = io.StringIO(), io.StringIO()
    old, sys.stderr = sys.stderr, err
    try:
        rc = I.main(args, backend=dev, out=out, ask=lambda _p: answer)
    finally:
        sys.stderr = old
    return rc, out.getvalue(), err.getvalue()


# ------------------------------------------------------------------- tests ---

def wire():
    data = bytes(range(256)) * 3
    ok(I.unpack7(I.pack7(data))[:len(data)] == data, "pack7 / unpack7 round trip")
    pkt = I.response(0x12345, b"\x01\x02\x03", fl=5)
    u = I.unpack7(pkt[1:-1])
    ok(u[:3] == b"\x00\x59\x30" and int.from_bytes(u[7:11], "little") == 0x12345 and u[14:17] == b"\x01\x02\x03"
       and ~sum(u[6:17]) & 0xFF == u[17], "response: header, address, data, checksum")
    raw = package("FM-1_906")
    ok(I.product_of(raw) == "FM-1_906" and len(I.logical_image(raw)) == len(raw) - 20, "product_of / logical_image (synthetic)")


def installs():
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("ok.fwsc", raw)

    dev = FakeFM1(image)
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and dev.upgrades == 2 and dev.served == 11 and "done: the FM-1 runs FM-1_900" in out
       and "100%" in out, f"install: running -> loader -> Felucca ({dev.served} reads)")
    dev = FakeFM1(image, identity="FM-1_901", name="Clipper")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and "done: the FM-1 runs FM-1_900" in out,
       "install: an FM-1 under CLIPPER (port 'Clipper') -> found, SLOOP installed")

    # an FM-1 running Frodhes (0.9.5's USB PID: the port is "Frodhes"): found without --port, installed, found again
    dev = FakeFM1(image, identity="FM-1_958", name="Frodhes", after_name="Frodhes")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and dev.upgrades == 2 and "done: the FM-1 runs FM-1_900" in out and "(Frodhes)" in out,
       "install: an FM-1 running Frodhes (port 'Frodhes', no --port) -> found, installed, back as 'Frodhes'")
    rc, out, err = cli(["--info"], FakeFM1(image, identity="FM-1_958", name="Frodhes"))
    ok(rc == 0 and "FM-1_958" in out and "port: Frodhes" in out, "--info: an FM-1 running Frodhes, no --port")
    # 0.9.7: the port carries the model ("Frodhes FM-1"), the old name after the install back as the new one
    dev = FakeFM1(image, identity="FM-1_958", name="Frodhes", after_name="Frodhes FM-1")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and "done: the FM-1 runs FM-1_900" in out,
       "install: an FM-1 on 'Frodhes' -> installed, back as 'Frodhes FM-1' (no --port)")
    rc, out, err = cli(["--info"], FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1"))
    ok(rc == 0 and "FM-1_958" in out and "port: Frodhes FM-1" in out, "--info: an FM-1 on 'Frodhes FM-1', no --port")
    ok(all(I.PORT_RE.search(n) for n in ("Frodhes", "FRODHES", "Frodhes MIDI 1", "Frodhes FM-1", "Frodhes SMK-37 Pro", "FM-1", "Felucca", "Felucca Update")),
       "port names: Frodhes (0.9.5), Frodhes FM-1 / Frodhes SMK-37 Pro (0.9.7) and the FM-1's other names probed")

    dev = FakeFM1(image, identity="ota-FM-1_900", name="Felucca Update")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and dev.upgrades == 1 and "update mode" in out, "install: device already in update mode -> finishes the write")

    dev = FakeFM1(image)
    rc, out, err = cli([p], dev, answer=False)
    ok(rc == 1 and dev.upgrades == 0 and "cancelled" in out, "install: answer no -> nothing sent but the handshake")

    dev = FakeFM1(image, identity="FM-1_905", name="Felucca")
    rc, out, err = cli(["--info"], dev)
    ok(rc == 0 and "FM-1_905" in out and "running" in out and dev.upgrades == 0, "--info: identity of the connected FM-1")

    dev = FakeFM1(image, identity="ota-FM-1_900", name="Felucca Update")
    rc, out, err = cli(["--info"], dev)
    ok(rc == 0 and "update loader" in out, "--info: device in update mode")


def errors():
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("ok.fwsc", raw)

    rc, out, err = cli([p, "--yes"], FakeFM1(image, name="IAC Driver Bus 1"))
    ok(rc == 3 and "not found" in err and "IAC Driver" in err, "no FM-1 port -> exit 3, lists the MIDI inputs")
    dev = FakeFM1(image)
    rc, out, err = cli([p, "--yes", "--port", "Felucca"], dev)
    ok(rc == 3 and dev.upgrades == 0, "--port that matches nothing -> exit 3")
    dev = FakeFM1(image, name="My Interface")
    rc, out, err = cli(["--info", "--port", "my int"], dev)
    ok(rc == 0 and "FM-1_015" in out, "--port picks a port the default match skips")

    t0 = time.monotonic()
    rc, out, err = cli([p, "--yes"], FakeFM1(image, unplug_after=2))
    ok(rc == 4 and "disconnected" in err and "nothing was written" in err and time.monotonic() - t0 < 3,
       "unplugged in step 1 -> exit 4 'lost', nothing written")
    t0 = time.monotonic()
    rc, out, err = cli([p, "--yes"], FakeFM1(image, identity="ota-FM-1_900", name="Felucca Update", unplug_after=3))
    ok(rc == 4 and "run the install again" in err and time.monotonic() - t0 < 3, "unplugged during the write -> exit 4 at once")
    rc, out, err = cli([p, "--yes"], FakeFM1(image, identity="ota-FM-1_900", name="Felucca Update", stall_after=3))
    ok(rc == 4 and "stopped answering" in err, "loader stops answering -> exit 4 after the idle time")
    rc, out, err = cli([p, "--yes"], FakeFM1(image, identity="ota-FM-1_900", name="Felucca Update", bad_addr=len(image)))
    ok(rc == 4 and "outside the package" in err, "read past the package -> exit 4")

    rc, out, err = cli([p, "--yes"], FakeFM1(image, after_write="FM-1_015"))
    ok(rc == 6 and "reports FM-1_015" in err, "another identity after the restart -> exit 6")
    dev = FakeFM1(image, identity="XY-9_001", name="usb-midi")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0, "another model -> exit 6, nothing sent")
    dev = FakeFM1(image, identity="ota-FM-1_015", name="FM-1 Update")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0 and "another firmware" in err, "another firmware's update loader -> exit 6, nothing sent")

    class Stuck(FakeFM1):          # the loader never shows up
        def boot(self, identity, name):
            super().boot(identity, name)
            if identity.startswith("ota-"):
                self.connected = False
    rc, out, err = cli([p, "--yes"], Stuck(image))
    ok(rc == 5 and "loader did not appear" in err, "no loader after step 1 -> exit 5")

    plain = pkgfile("plain.fwsc", package(marker=False))
    dev = FakeFM1(image)
    rc, out, err = cli([plain, "--yes"], dev)
    ok(rc == 2 and "no Felucca update loader" in err and dev.sent == [], "package without the loader marker -> exit 2, no MIDI")
    dev = FakeFM1(I.logical_image(package(marker=False)))
    rc, out, err = cli([plain, "--yes", "--force"], dev)
    ok(rc == 0 and dev.bad == 0, "... installs with --force")
    dev = FakeFM1(image)
    rc, out, err = cli([pkgfile("damaged.fwsc", package(damage=True)), "--yes"], dev)
    ok(rc == 2 and "damaged package" in err and dev.sent == [], "damaged package (a file's CRC) -> exit 2, no MIDI")
    rc, out, err = cli([pkgfile("short.fwsc", b"\0" * 100), "--yes"], FakeFM1(image))
    ok(rc == 2 and "too short" in err, "not a package -> exit 2")
    rc, out, err = cli([str(TMP / "missing.fwsc"), "--yes"], FakeFM1(image))
    ok(rc == 2, "missing file -> exit 2")


def smk():
    """SMK-37 Pro: identities, port names, the 36-block stock package, SLOOP's own SMK loader"""
    ok(I.PORT_RE.search("SMK-37 Pro Midi") and I.PORT_RE.search("SMK-37 Pro-MCP 2") and I.PORT_RE.search("FM-1")
       and not I.PORT_RE.search("IAC Driver Bus 1"), "port names: SMK-37 Pro ports probed, FM-1 too, other gear not")
    ok(I.OWN_LOADER_RE.fullmatch("ota-SMK-37 Pro_900") and I.OWN_LOADER_RE.fullmatch("ota-SMK-37 Elite_900")
       and not I.OWN_LOADER_RE.fullmatch("ota-SMK-37 Elite_016") and I.OWN_LOADER_RE.fullmatch("ota-FM-1_925")
       and not I.OWN_LOADER_RE.fullmatch("ota-SMK-37 Pro_016") and not I.OWN_LOADER_RE.fullmatch("ota-FM-1_015"),
       "own loader: ota-SMK-37 Pro_9XX / ota-FM-1_9XX, not the stock ones")
    ident = I.parse_identity(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + b"SMK-37 Pro_016" + bytes(14)) + b"\xF7")
    ok(ident and ident.model == "SMK-37 Pro" and ident.version == 16 and I.model_of("ota-SMK-37 Pro_900") == "SMK-37 Pro",
       "identity SMK-37 Pro_016 parses (model SMK-37 Pro, the space kept)")

    # synthetic 36-block package (as the stock v016): detected, CRCs agree
    raw36 = package("SMK-37 Pro_900", blocks=36, size=0x3000)
    image36, n = I.deinterleave(raw36)
    ok(n == 36 and I.block_count(raw36) == 36 and len(image36) == len(raw36) - 36 and not I.package_damage(image36)
       and I.product_of(raw36) == "SMK-37 Pro_900", "36 blocks (synthetic): detected, logical image valid")
    ok(I.package_damage(I.logical_image(raw36)) is not None, "... a fixed 20-block deinterleave of it fails the CRCs")
    # a 20-block package whose next data byte is 0x7D by chance: the CRC check falls back to 20
    for seed in range(1, 256):
        raw = package("FM-1_900", seed=seed)
        if raw[20 * I.BLK + I.KEEP] == 0x7D:
            break
    image, n = I.deinterleave(raw)
    ok(I.block_count(raw) > 20 and n == 20 and image == I.logical_image(raw) and not I.package_damage(image),
       f"20 blocks with a 0x7D data byte at the 21st marker (seed {seed}): falls back to 20")

    # install on a simulated SMK-37 Pro (stock 016 -> SLOOP's SMK loader -> SMK-37 Pro_900)
    p = pkgfile("smk.fwsc", package("SMK-37 Pro_900"))
    image = I.logical_image(package("SMK-37 Pro_900"))
    dev = FakeFM1(image, identity="SMK-37 Pro_016", name="SMK-37 Pro Midi", after_write="SMK-37 Pro_900",
                  loader=("ota-SMK-37 Pro_900", "Felucca Update"))
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.bad == 0 and dev.upgrades == 2 and "runs SMK-37 Pro_900" in out,
       "install on an SMK-37 Pro: running -> loader -> SMK-37 Pro_900")
    dev = FakeFM1(image, identity="ota-SMK-37 Pro_900", name="Felucca Update", after_write="SMK-37 Pro_900")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 0 and dev.upgrades == 1, "SMK in SLOOP's update mode -> finishes the write")
    dev = FakeFM1(image, identity="ota-SMK-37 Pro_016", name="SMK-37 Pro Midi")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0 and "another firmware" in err, "SMK in the stock loader -> exit 6, nothing sent")
    fm1 = pkgfile("fm1.fwsc", package("FM-1_900"))
    dev = FakeFM1(image, identity="SMK-37 Pro_016", name="SMK-37 Pro Midi")
    rc, out, err = cli([fm1, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0, "FM-1 package on an SMK-37 Pro -> exit 6 (wrong model), nothing sent")
    dev = FakeFM1(image, identity="ota-SMK-37 Pro_900", name="Felucca Update")
    rc, out, err = cli([fm1, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0, "FM-1 package on an SMK in SLOOP's update mode -> exit 6 (wrong model), nothing sent")
    dev = FakeFM1(image, identity="FM-1_015", name="FM-1")
    rc, out, err = cli([p, "--yes"], dev)
    ok(rc == 6 and dev.upgrades == 0, "SMK package on an FM-1 -> exit 6, nothing sent")

    # the stock packages (outside this repo; read only)
    stock = __import__("pathlib").Path(__import__("os").environ.get("SMK_STOCK_DIR") or ROOT / "no-stock-packages")
    v016 = stock / "SMK-37_Pro_016.fwsc"
    if not v016.exists():
        print("stock SMK-37 Pro packages: skipped (SMK_STOCK_DIR not set)")
    else:
        raw = v016.read_bytes()
        image, n = I.deinterleave(raw)
        ok(n == 36 and I.product_of(raw) == "SMK-37 Pro_016" and len(raw) == 705252 and not I.package_damage(image),
           "stock SMK-37_Pro_016.fwsc: 36 blocks, identity SMK-37 Pro_016, CRCs ok")
        ok(I.package_damage(I.logical_image(raw)) is not None, "... a 20-block deinterleave of it is damaged")
        product, image2 = I.load_package(str(v016), True)
        ok(product == "SMK-37 Pro_016" and image2 == image, "... load_package (--force: no SLOOP loader) accepts it")
        rc, out, err = cli([str(v016), "--yes"], FakeFM1(image, identity="SMK-37 Pro_016", name="SMK-37 Pro Midi"))
        ok(rc == 2 and "no Felucca update loader" in err, "... without --force it is refused (exit 2)")
        for old in sorted(stock.glob("SMK-37_Pro_01[1-5].fwsc")):
            raw = old.read_bytes()
            image, n = I.deinterleave(raw)
            ok(not I.package_damage(image) and I.product_of(raw) == "SMK-37 Pro_" + old.stem[-3:],
               f"stock {old.name}: {n} blocks, identity {I.product_of(raw)}, CRCs ok")
    clean = ROOT / "build/dinghy.fwsc"
    if clean.exists():
        raw = clean.read_bytes()
        image, n = I.deinterleave(raw)
        ok(n == 20 and image == I.logical_image(raw) and not I.package_damage(image) and I.product_of(raw).startswith("FM-1_"),
           f"FM-1 package {clean.name} still parses: 20 blocks, {I.product_of(raw)}")


def against_js():
    clean = ROOT / "build/dinghy.fwsc"
    for f in [clean, *sorted((ROOT / "build").glob("dinghy*-smk37*.fwsc"))]:   # (every package built)
        if f.exists():
            ok(I.LOADER_MARK in f.read_bytes(), f"{f.name} carries the Felucca loader marker")
    if not shutil.which("node") or not clean.exists() or not (ROOT / "web/fm1pkg.js").exists():
        print("logical image vs fm1pkg.js: skipped (needs node, web/fm1pkg.js and build/dinghy.fwsc; Dinghy has no web/)")
        return
    js = ("import { logicalImage, productOf } from %r; import { readFileSync } from 'node:fs';"
          "const p = readFileSync(process.argv[1]); process.stderr.write(productOf(p));"
          "process.stdout.write(logicalImage(p));") % str(ROOT / "web/fm1pkg.js")
    r = subprocess.run(["node", "--input-type=module", "-e", js, str(clean)], capture_output=True, check=True)
    raw = clean.read_bytes()
    ok(I.logical_image(raw) == r.stdout and I.product_of(raw) == r.stderr.decode(),
       f"logical_image / product_of == fm1pkg.js ({clean.name}, {I.product_of(raw)})")
    ok(I.LOADER_MARK in raw, f"{clean.name} carries the Felucca loader marker")


class WithOthers:
    """a FakeFM1 plus another unit's ports (listed before or after it): opening one of those is recorded, and fails"""

    def __init__(self, fm1, others, before=True):
        self.fm1, self.others, self.before, self.probed = fm1, list(others), before, []

    def input_names(self):
        mine = self.fm1.input_names()
        return self.others + mine if self.before else mine + self.others

    output_names = input_names

    def open(self, in_name, out_name):
        if in_name in self.others or out_name in self.others:
            self.probed.append(in_name)
            raise IOError("another unit")
        return self.fm1.open(in_name, out_name)


def two_units():
    """two FM-1s on one computer: the Frodhes one is the target, the other unit's ports are never opened; without a
    Frodhes port the installer refuses unless --port names one"""
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("ok.fwsc", raw)
    for others in (["Felucca", "AMB-1 FM-1"], ["Textura", "Textura FM-1"], ["Felucca", "Textura FM-1", "FM-1"]):
        for before in (True, False):
            where = f"{'+'.join(others)} {'before' if before else 'after'}"
            b = WithOthers(FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1", after_name="Frodhes FM-1"),
                           others, before)
            rc, out, err = cli([p, "--yes"], b)
            ok(rc == 0 and b.fm1.bad == 0 and b.fm1.upgrades == 2 and "(Frodhes FM-1)" in out and not b.probed,
               f"two units ({where}): installed on 'Frodhes FM-1', the other unit never opened {b.probed}")
            b = WithOthers(FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1"), others, before)
            rc, out, err = cli(["--info"], b)
            ok(rc == 0 and "port: Frodhes FM-1" in out and not b.probed,
               f"two units ({where}): --info on 'Frodhes FM-1', the other unit never opened")
    for before in (True, False):
        b = WithOthers(FakeFM1(image), ["Felucca"], before)          # two FM-1-like ports, no Frodhes one
        rc, out, err = cli([p, "--yes"], b)
        ok(rc == 7 and "--port" in err and not b.probed and not b.fm1.links and b.fm1.upgrades == 0,
           f"two units, no Frodhes port ({'before' if before else 'after'}): refused (exit 7), nothing opened")
        rc, out, err = cli(["--info"], b)
        ok(rc == 7 and not b.probed and not b.fm1.links, "two units, no Frodhes port: --info refused too")
        b = WithOthers(FakeFM1(image, identity="FM-1_905", name="Felucca"), ["FM-1"], before)
        rc, out, err = cli([p, "--yes", "--port", "Felucca"], b)
        ok(rc == 0 and b.fm1.upgrades == 2 and not b.probed,
           "two units, --port Felucca: installed there, the other unit never opened (also in the loader waits)")
    b = WithOthers(FakeFM1(image, name="IAC Driver Bus 1"), ["Textura FM-1"])
    rc, out, err = cli([p, "--yes"], b)
    ok(rc == 3 and not b.probed, "a Textura unit alone: never a candidate (exit 3), never opened")
    rc, out, err = cli([p, "--yes", "--port", "Textura"], b)
    ok(rc == 3 and b.probed == ["Textura FM-1"] and not b.fm1.links,
       "--port Textura: only the named unit tried (a loan, named on purpose), nothing else opened")
    # a loaned unit (Textura's) beside the Frodhes FM-1: named with --port, its new USB name (build.py --usb-name)
    # with --new-port; the Frodhes FM-1 never opened, in the install nor in the loader waits; and back again
    for before in (True, False):
        b = WithOthers(FakeFM1(image, identity="FM-1_958", name="Textura FM-1", after_name="Dualcore Test"),
                       ["Frodhes FM-1"], before)
        rc, out, err = cli([p, "--yes", "--port", "Textura", "--new-port", "Dualcore"], b)
        ok(rc == 0 and b.fm1.upgrades == 2 and "done: the FM-1 runs FM-1_900" in out and not b.probed,
           f"loaned unit --port Textura --new-port Dualcore: installed, found again, the Frodhes FM-1 never opened {b.probed}")
        b = WithOthers(FakeFM1(image, identity="FM-1_958", name="Dualcore Test", after_name="Textura FM-1"),
                       ["Frodhes FM-1"], before)
        rc, out, err = cli([p, "--yes", "--port", "Dualcore", "--new-port", "Textura"], b)
        ok(rc == 0 and b.fm1.upgrades == 2 and "done: the FM-1 runs FM-1_900" in out and not b.probed,
           f"loaned unit back: --port Dualcore --new-port Textura, the Frodhes FM-1 never opened {b.probed}")


class TwoUnits:
    """two FakeFM1s on USB at their own locations (usb_devices: what ioreg lists); a port name both share opens the
    first one listed, as CoreMIDI does"""

    def __init__(self, *units):
        self.units = list(units)            # (FakeFM1, locationID)

    def input_names(self):
        return list(dict.fromkeys(n for u, _ in self.units for n in u.input_names()))   # (as MidoBackend)

    output_names = input_names

    def open(self, in_name, out_name):
        for u, _ in self.units:
            if u.connected and u.name == in_name:
                return u.open(in_name, out_name)
        raise IOError("no such port")

    def usb_devices(self):
        return [(u.name, loc) for u, loc in self.units if u.connected]


class Racing(FakeFM1):
    """a unit whose switch to update mode puts the other unit in update mode too (another install at that moment:
    2026-11-08, two loaders named 'Felucca Update' at once)"""
    other = None

    def boot(self, identity, name):
        if self.other is not None and identity.startswith("ota-"):
            self.other.boot("ota-FM-1_900", "Felucca Update")
        super().boot(identity, name)


class Locked(FakeFM1):
    """records whether the INSTALL lock was held at each UPGRADE"""
    lockdir, held = None, []

    def rx(self, d):
        if d == I.UPGRADE:
            self.held.append(os.path.isdir(self.lockdir))
        super().rx(d)


def two_loaders():
    """the unit pinned by its USB location: another unit's loader (the same name) is never taken, a shared port name
    is refused, --location asserts the unit; the INSTALL lock is held for the whole install and waited for"""
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("ok.fwsc", raw)
    for first in (True, False):
        f = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1", after_name="Frodhes FM-1")
        t = Racing(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
        t.other = f
        b = TwoUnits(*([(f, 0x20130000), (t, 0x00100000)] if first else [(t, 0x00100000), (f, 0x20130000)]))
        rc, out, err = cli([p, "--yes", "--port", "Dualcore"], b)
        ok(rc == 5 and f.served == 0 and f.upgrades == 0 and "not taken" in err and "0x20130000" in err,
           f"two loaders named 'Felucca Update' at once ({'other first' if first else 'other second'}): refused "
           f"(exit {rc}), the other unit's loader never written ({f.served} reads)")
    f = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1")
    t = FakeFM1(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
    b = TwoUnits((f, 0x20130000), (t, 0x00100000))
    rc, out, err = cli([p, "--yes", "--port", "Dualcore"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links and "usb 0x00100000" in out,
       "two units, --port Dualcore: pinned at 0x00100000, installed, the other unit never opened")
    t = FakeFM1(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
    b = TwoUnits((FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1"), 0x20130000), (t, 0x00100000))
    rc, out, err = cli([p, "--yes", "--port", "Dualcore", "--location", "0x20130000"], b)
    ok(rc == 3 and t.upgrades == 0 and "0x00100000" in err, "--location elsewhere: refused (exit 3), nothing sent")
    rc, out, err = cli([p, "--yes", "--port", "Dualcore", "--location", "0x00100000"], b)
    ok(rc == 0 and t.upgrades == 2, "--location at the unit: installed")
    rc, out, err = cli(["--info", "--port", "Dualcore"], b)
    ok(rc == 0 and "usb: 0x00100000" in out, "--info prints the unit's USB location")
    u1 = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1")
    u2 = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1")
    b = TwoUnits((u1, 0x20130000), (u2, 0x00100000))
    rc, out, err = cli([p, "--yes", "--port", "Frodhes FM-1"], b)
    ok(rc == 7 and u1.upgrades == 0 and u2.upgrades == 0 and "cannot be told apart" in err,
       "two units with one name: refused (exit 7), nothing sent")

    # the INSTALL lock (a real computer's backend: install_lock), in a private lock root
    import lock as L
    root = TMP / "locks"
    os.environ["FRODHES_LOCK_ROOT"] = str(root)
    Locked.lockdir = L.lock_dir("INSTALL", str(root))
    Locked.held = []
    t = Locked(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
    b = TwoUnits((t, 0x00100000))
    b.install_lock = True
    rc, out, err = cli([p, "--yes", "--port", "Dualcore"], b)
    ok(rc == 0 and Locked.held == [True, True] and not os.path.isdir(Locked.lockdir),
       f"INSTALL lock held at both switches {Locked.held}, released after")
    holder = subprocess.Popen(["sleep", "30"])          # another install (a live process, not ours)
    try:
        os.makedirs(Locked.lockdir)
        with open(os.path.join(Locked.lockdir, "owner.json"), "w") as fh:
            json.dump(dict(name="INSTALL", token="x" * 32, pid=holder.pid, host=socket.gethostname(),
                           who="another install", since=time.time()), fh)
        Locked.held = []
        t = Locked(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
        b = TwoUnits((t, 0x00100000))
        b.install_lock = True
        old, I.INSTALL_LOCK_WAIT_S = I.INSTALL_LOCK_WAIT_S, 0.5
        try:
            cli([p, "--yes", "--port", "Dualcore"], b)
            gave_up = False
        except SystemExit:
            gave_up = True
        finally:
            I.INSTALL_LOCK_WAIT_S = old
        ok(gave_up and t.upgrades == 0 and not t.links,
           "another install holds INSTALL: waited, gave up, the unit never opened")
    finally:
        holder.kill()
        shutil.rmtree(Locked.lockdir, ignore_errors=True)
        os.environ.pop("FRODHES_LOCK_ROOT", None)


class StaleUnits(TwoUnits):
    """TwoUnits where CoreMIDI keeps a port's old name: a unit's port shows `alias` keys while its USB product name
    is the value (the image changed its USB name, CoreMIDI cached the old one), plus ports with no unit (extra) and
    the USB devices' vendor / product ids (usb_ids, as ioreg)"""

    def __init__(self, *units, alias=None, extra=(), ids=None, devs=()):
        super().__init__(*units)
        self.alias, self.extra, self._ids, self.devs = dict(alias or {}), list(extra), ids, list(devs)

    def input_names(self):
        return super().input_names() + self.extra

    output_names = input_names

    def usb_devices(self):
        return [(self.alias.get(u.name, u.name), loc) for u, loc in self.units if u.connected] + self.devs

    def usb_ids(self):
        return self._ids


class LocUnits(StaleUnits):
    """StaleUnits on macOS with CoreMIDI's locations: ports_at(loc) gives the ports of the unit at loc (CoreMIDI's
    USBLocationID, whatever name the ports show), raw_names the port lists with duplicates (as mido)"""

    def ports_at(self, loc):
        n = [x for u, l in self.units if l == loc for x in u.input_names()]
        return n, list(n)

    def raw_names(self):
        n = [x for u, _ in self.units for x in u.input_names()] + self.extra
        return n, list(n)


def located_ports():
    """2026-11-09: unit 2 at 0x20130000 runs the candidate (USB product "Frodhes FM-1", as the user's unit at
    0x00100000) while CoreMIDI keeps its port as "Dualcore Test": --location takes the ports at that location (any
    name), checks the USB product there and the firmware's identity; the other unit is never opened"""
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("loc.fwsc", raw)
    F, D = 0x00100000, 0x20130000

    def pair(t_id="FM-1_958", t_name="Dualcore Test", usb="Frodhes FM-1", **kw):
        f = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1", after_name="Frodhes FM-1")
        t = FakeFM1(image, identity=t_id, name=t_name, after_name=t_name)
        return f, t, LocUnits((f, F), (t, D), alias={t_name: usb}, **kw)

    f, t, b = pair()
    rc, out, err = cli(["--info", "--location", "0x20130000"], b)
    ok(rc == 0 and "FM-1_958" in out and "port: Dualcore Test" in out and "usb: 0x20130000" in out
       and t.links and not f.links, f"--info --location 0x20130000, stale port name: unit 2, the other unopened ({rc})")
    f, t, b = pair()
    rc, out, err = cli(["--info", "--location", "0x20130000", "--port", "Dualcore Test"], b)
    ok(rc == 0 and "usb: 0x20130000" in out and not f.links, "the same with --port Dualcore Test")
    f, t, b = pair()
    rc, out, err = cli(["--info", "--location", "0x00100000"], b)
    ok(rc == 0 and "port: Frodhes FM-1" in out and "usb: 0x00100000" in out and f.links and not t.links,
       "--info --location 0x00100000: the user's unit, unit 2 unopened")
    f, t, b = pair()
    rc, out, err = cli(["--info", "--location", "0x00100000", "--port", "Dualcore Test"], b)
    ok(rc != 0 and not f.links and not t.links, f"--port of the other unit at --location: refused, nothing opened ({rc})")
    f, t, b = pair()
    rc, out, err = cli([p, "--yes", "--location", "0x20130000"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links, f"install --location 0x20130000: unit 2 installed, the other unopened ({rc})")
    f, t, b = pair(usb="Sonic Test")
    rc, out, err = cli(["--info", "--location", "0x20130000"], b)
    ok(rc == 3 and not t.links and not f.links and "not an FM-1" in err,
       f"the USB product there not an FM-1's: refused before anything is opened (exit {rc})")
    f, t, b = pair(t_id="SMK-37 Pro_958")
    rc, out, err = cli(["--info", "--location", "0x20130000"], b)
    ok(rc == 3 and not f.links and all(k.closed for k in t.links) and "answers 'SMK-37 Pro_958'" in err,
       f"the firmware's identity not an FM-1's: refused, the link closed (exit {rc})")
    f, t, b = pair(t_name="Frodhes FM-1")
    rc, out, err = cli(["--info", "--location", "0x20130000"], b)
    ok(rc == 7 and not t.links and not f.links, f"two ports of one name: refused, nothing opened (exit {rc})")
    f, t, b = pair()
    b.ports_at = lambda loc: ([], [])
    rc, out, err = cli(["--info", "--location", "0x20130000"], b)
    ok(rc != 0 and not t.links and not f.links, f"no ports at the location: nothing opened (exit {rc})")


def stale_names():
    """CoreMIDI's stale name (2026-11-08: the unit at 0x20130000 runs an image whose USB product is "Sonic Test",
    CoreMIDI still lists its port as "Dualcore Test"): with --location the unit is taken by exclusion (every other
    port matched by its own name elsewhere); the user's unit is never opened; anything less than a full account
    refuses with nothing sent"""
    import fm1_ports as P
    raw = package()
    image = I.logical_image(raw)
    p = pkgfile("ok.fwsc", raw)
    F, D = 0x00100000, 0x20130000
    J = (0x4C4A, 0x4155)                                  # (any one vendor / product id: both units the same)

    def pair(**kw):
        f = FakeFM1(image, identity="FM-1_958", name="Frodhes FM-1", after_name="Frodhes FM-1")
        t = FakeFM1(image, identity="FM-1_958", name="Dualcore Test", after_name="Dualcore Test")
        return f, t, StaleUnits((f, F), (t, D), alias={"Dualcore Test": "Sonic Test"}, **kw)

    f, t, b = pair(ids={F: J, D: J})
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links and "taken as 'Sonic Test' at 0x20130000" in err,
       f"stale port name, --location 0x20130000: installed by exclusion, the Frodhes FM-1 never opened (rc {rc})")
    f, t, b = pair()
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links, "the same without the vendor / product ids: installed")
    f, t, b = pair()
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x00100000"], b)
    ok(rc == 3 and not t.links and not f.links and t.upgrades == f.upgrades == 0,
       f"--location at the Frodhes FM-1 with the stale port: refused (exit {rc}), neither unit opened")
    f, t, b = pair()
    rc, out, err = cli([p, "--yes", "--port", "Frodhes FM-1", "--location", "0x20130000"], b)
    ok(rc == 3 and not f.links and not t.links, f"--port Frodhes FM-1 --location 0x20130000: refused (exit {rc}), nothing opened")
    f, t, b = pair(extra=["Mystery Synth"])
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 3 and not t.links and not f.links, f"another port without a USB device: refused (exit {rc}), nothing opened")
    f, t, b = pair(extra=["MIDISPORT 2x4 Port 1"], ids={F: J, D: J})
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links,
       f"a driver's port without a USB device, with the ids (every unit of the kind matched): installed (rc {rc})")
    f, t, b = pair(extra=["IAC Driver Bus 1"])
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 0 and t.upgrades == 2 and not f.links, "a virtual port (IAC) besides: still accounted for, installed")
    f, t, b = pair(ids={F: J, D: J, 0x14200000: J}, devs=[("FM-1", 0x14200000)])
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 3 and not t.links and not f.links,
       f"a third unit of the same ids without a port of its own: refused (exit {rc}), nothing opened")
    f, t, b = pair(devs=[("Sonic Test", 0x14200000)])
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test", "--location", "0x20130000"], b)
    ok(rc == 3 and not t.links and not f.links, f"a second device named like the unit: refused (exit {rc}), nothing opened")
    f, t, b = pair()
    rc, out, err = cli([p, "--yes", "--port", "Dualcore Test"], b)
    ok(rc != 0 and t.upgrades == 0 and f.upgrades == 0 and not f.links,
       f"without --location: refused as before (exit {rc}), nothing written, the Frodhes FM-1 never opened")

    # fm1_ports.stale_match itself, and force() (the HIL tools' --port / --location)
    devs = [("Frodhes FM-1", F), ("Sonic Test", D), ("M6", 0x01100000), ("USB2.0 Hub", 0x20100000)]
    ports = ["Frodhes FM-1", "Dualcore Test", "M6", "IAC Driver Bus 1"]
    ok(P.stale_match("Dualcore Test", D, devs, ports) == [("Sonic Test", D)], "stale_match: the unit at the location")
    ok(P.stale_match("Dualcore Test", F, devs, ports) == [], "stale_match: never the location of a matched port")
    ok(P.stale_match("Dualcore Test", None, devs, ports) == [], "stale_match: nothing without a location")
    ok(P.stale_match("Frodhes FM-1", D, devs, ports) == [], "stale_match: a port that matches by name is never stale")
    ok(P.stale_match("Dualcore Test", D, devs, ports + ["Sonic Test"]) == [],
       "stale_match: a port carrying the unit's own name: that one is the unit's, not the stale one")
    ok(P.stale_match("Dualcore Test", 0x30000000, devs, ports) == [], "stale_match: no device at the location")
    ok(P.stale_match("Dualcore Test", D, devs, ports, {F: J, D: J, 0x01100000: (1, 2)}) == [("Sonic Test", D)],
       "stale_match: other ids elsewhere do not matter")
    ok(P.stale_match("Dualcore Test", D, devs, ports, {F: J}) == [], "stale_match: the unit's ids unknown: refused")
    old = P.usb_ids, P.midi_inputs
    try:
        P.usb_ids, P.midi_inputs = (lambda: {F: J, D: J}), (lambda: ports)
        P.force("Dualcore Test", D, devs)
        ok(P.FORCED == "Dualcore Test", "force --location with a stale name: taken by exclusion")
        try:
            P.force("Dualcore Test", F, devs)
            ok(False, "force at the Frodhes FM-1's location: refused")
        except SystemExit as e:
            ok("not there" in str(e), "force at the Frodhes FM-1's location: refused")
    finally:
        P.usb_ids, P.midi_inputs = old
        P.force(None)


wire()
installs()
errors()
smk()
against_js()
two_units()
two_loaders()
stale_names()
located_ports()
shutil.rmtree(TMP, ignore_errors=True)
# the SMK-37 Pro, Elite and MKE-P37 are one keyboard: packages install across them, never across FM-1 / SMK
ok(I.same_keyboard("SMK-37 Elite_016", "SMK-37 Pro_900") and I.same_keyboard("ota-MKE-P37_900", "SMK-37 Elite_900")
   and I.same_keyboard("STARRYKEY-37 PLAY_01", "SMK-37 Pro_900") and I.same_keyboard("FM-1_015", "FM-1_900")
   and not I.same_keyboard("FM-1_015", "SMK-37 Pro_900") and not I.same_keyboard("SMK-37 Elite_016", "FM-1_900")
   and not I.same_keyboard("XY-9_001", "SMK-37 Pro_900"),
   "models: SMK-37 Pro / Elite / MKE-P37 / Starrykey are one keyboard, FM-1 is another")
ok(all(I.PORT_RE.search(n) for n in ("SMK-37 Elite", "MKE-P37", "STARRYKEY-37 PLAY", "Snoop37", "Vox37", "Felucca",
                                     "X0X FM-1", "FoMni SMK-37", "Makkuro37 Update")),
   "port names: every SMK model and every port firmware probed")

def starrykey():
    """a 21-character identity ("STARRYKEY-37 PLAY_016"): the device's and the package's (as web/fm1pkg.js)"""
    def pkg(product, blocks, size=0x3000):
        img = bytearray((i * 7) & 0xFF for i in range(size))
        data = bytes(img[0x400:])
        e = bytearray(0x50)
        e[4:6] = I._crc16(data).to_bytes(2, "little")
        e[8:16] = (0x400).to_bytes(4, "little") + len(data).to_bytes(4, "little")
        e[0x40:0x49] = b"flash.bin"
        lst = I._dec(bytes(e))
        hdr = bytearray(0x40)
        hdr[4:10] = size.to_bytes(4, "little") + (1).to_bytes(2, "little")
        hdr[2:4] = I._crc16(lst).to_bytes(2, "little")
        hdr[0:2] = I._crc16(bytes(hdr[2:0x40])).to_bytes(2, "little")
        img[0:0x90] = I._dec(bytes(hdr)) + lst
        raw = bytearray()
        for i in range(blocks):
            m = (ord(product[i]) + i + 1) & 0xFF if i < len(product) else 0x7D
            raw += img[i * I.KEEP:(i + 1) * I.KEEP] + bytes([m])
        return bytes(raw + img[blocks * I.KEEP:])

    def image(raw):
        im = I.deinterleave(raw)
        return im[0] if isinstance(im, tuple) else im

    def intact(im):
        return I.package_damage(im) is None if hasattr(I, "package_damage") else I.files_ok(im)

    ident = I.parse_identity(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + b"STARRYKEY-37 PLAY_016" + bytes(7)) + b"\xF7")
    ok(ident and ident.text == "STARRYKEY-37 PLAY_016" and ident.model == "STARRYKEY-37 PLAY" and ident.version == 16,
       "device identity STARRYKEY-37 PLAY_016: all 21 characters")
    ident = I.parse_identity(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + b"FM-1_015\0xyz" + bytes(16)) + b"\xF7")
    ok(ident and ident.text == "FM-1_015", "device identity: the text ends at the first NUL")
    raw = pkg("STARRYKEY-37 PLAY_016", 36)
    im = image(raw)
    ok(I.identity_blocks(raw) == 21 and I.block_count(raw) == 36 and len(im) == len(raw) - 36 and intact(im)
       and I.product_of(raw) == "STARRYKEY-37 PLAY_016", "36-block package with a 21-character identity: read in full")
    raw = pkg("STARRYKEY-37 PLAY_015", 20)
    ok(I.identity_blocks(raw) == 20 and I.product_of(raw) == "STARRYKEY-37 PLAY_01" and intact(image(raw)),
       "20-block package: the identity stops at 20 characters (the stock V015 layout)")
    raw = pkg("SMK-37 Pro_016", 36)
    ok(I.identity_blocks(raw) == 20 and I.block_count(raw) == 36 and I.product_of(raw) == "SMK-37 Pro_016",
       "36-block package with a short identity: unchanged")


starrykey()

print(f"INSTALL TESTS FAILED ({failed})" if failed else "install tests passed")
sys.exit(1 if failed else 0)
