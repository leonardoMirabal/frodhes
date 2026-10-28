// SPDX-License-Identifier: GPL-3.0-only
// A fake FM-1 behind a fake Web MIDI MIDIAccess, for web/fm1install.js's tests (tests/web/install_test.js in node,
// and the page itself in the Chromium selftest: tools/release.py injects this file). As tests/install_test.py's:
// the identity on the handshake; UPGRADE: the running firmware's step 1 reads (the header, the file list, a block,
// then FINISH_CHECK) and a restart as the update loader (new ports, the old ones disconnected); the loader's step 2
// reads the whole image in 512-byte blocks, then FINISH_WRITE and a restart as the installed identity. Every answer
// is checked against the image; unplugAfter / stallAfter cut it after that many answers. Also makePackage: a valid
// synthetic package (a header, a file list, one file with its CRC) for any identity.
(function (root) {
  "use strict";
  const I = () => (typeof module !== "undefined" && module.exports ? require("../../web/fm1install.js") : root.FM1Install);
  const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

  class Port {
    constructor(dev, name, type) { this.dev = dev; this.name = name; this.type = type; this.state = "connected"; this.ls = []; this.id = name + Math.random(); }
    addEventListener(t, f) { if (t === "midimessage") this.ls.push(f); }
    removeEventListener(t, f) { this.ls = this.ls.filter((g) => g !== f); }
    open() { return Promise.resolve(this); }
    send(b) { if (this.state === "disconnected") throw new Error("disconnected"); this.dev.rx(Array.from(b)); }
    emit(b) { for (const f of this.ls.slice()) f({ data: Uint8Array.from(b) }); }
  }

  class FakeFM1 {
    // opts: identity ("FM-1_015"), name ("FM-1"), loader (["ota-FM-1_958", "Felucca Update"]), afterWrite ("FM-1_958"),
    // afterName (the port name after the install: "FM-1"),
    // unplugAfter / stallAfter (answers), others (other MIDI ports, never answering)
    constructor(image, opts = {}) {
      this.image = image;
      this.o = Object.assign({ identity: "FM-1_015", name: "FM-1", loader: ["ota-FM-1_958", "Felucca Update"], afterWrite: "FM-1_958", afterName: "FM-1", others: [] }, opts);
      this.inputs = new Map(); this.outputs = new Map();
      this.served = 0; this.bad = 0; this.upgrades = 0; this.written = new Uint8Array(image.length); this.writes = 0;
      for (const n of this.o.others) { this.inputs.set(n, new Port(this, n, "input")); this.outputs.set(n + " out", new Port({ rx() {} }, n, "output")); }
      this.boot(this.o.identity, this.o.name);
    }
    get access() { return { inputs: this.inputs, outputs: this.outputs, sysexEnabled: true }; }
    boot(identity, name) {
      for (const p of [this.inp, this.out]) if (p) { p.state = "disconnected"; this.inputs.delete(p.id); this.outputs.delete(p.id); }
      this.identity = identity; this.connected = true; this.waiting = null; this.queue = [];
      this.inp = new Port(this, name, "input"); this.out = new Port(this, name, "output");
      this.inputs.set(this.inp.id, this.inp); this.outputs.set(this.out.id, this.out);
    }
    unplug() { this.connected = false; this.inp.state = this.out.state = "disconnected"; this.inputs.delete(this.inp.id); this.outputs.delete(this.out.id); }
    replug() {                                    // power: an install cut in step 2 comes back as the loader
      this.boot(this.wrote ? this.o.loader[0] : this.identity, this.wrote ? this.o.loader[1] : this.inp.name);
    }
    tx(b) { const p = this.inp; setTimeout(() => { if (p.state !== "disconnected") p.emit(b); }, 0); }
    rx(d) {
      const X = I();
      if (!this.connected) return;
      if (same(d, X.HS_QUERY)) {
        const t = Array.from(this.identity, (c) => c.charCodeAt(0));
        this.tx([0xF0, ...X.pack7([0, 0x59, 0x11, 0, 0, 0, ...t, ...new Array(28 - t.length).fill(0)]), 0xF7]);
      } else if (same(d, X.UPGRADE)) {
        this.upgrades++;
        if (this.identity.toLowerCase().startsWith("ota-")) {
          this.wrote = true;
          this.queue = [];
          for (let a = 0; a < this.image.length; a += 512) this.queue.push([a, Math.min(512, this.image.length - a)]);
          this.queue.push([X.FINISH_WRITE, 8]);
        } else this.queue = [[0, 64], [0x40, 160], [0x1000, 512], [X.FINISH_CHECK, 8]];
        this.next();
      } else if (this.waiting) {
        const u = X.unpack7(d.slice(1, -1));
        const [addr, n] = this.waiting;
        const fin = addr >= X.FINISH_CHECK;
        const want = fin ? Array.from("success\0", (c) => c.charCodeAt(0)) : Array.from(this.image.subarray(addr, addr + n));
        if (u.length !== 15 + want.length || !same(u.slice(14, 14 + want.length), want)) this.bad++;
        else if (!fin && this.identity.toLowerCase().startsWith("ota-")) { this.written.set(want, addr); this.writes++; }
        this.waiting = null;
        this.served++;
        if (this.o.unplugAfter && this.served >= this.o.unplugAfter) { this.o.unplugAfter = 0; this.unplug(); return; }
        if (this.o.stallAfter && this.served >= this.o.stallAfter) { this.o.stallAfter = 0; return; }
        if (addr === X.FINISH_CHECK) setTimeout(() => this.boot(...this.o.loader), 20);
        else if (addr === X.FINISH_WRITE) setTimeout(() => { this.wrote = false; this.boot(this.o.afterWrite, this.o.afterName); }, 20);
        else this.next();
      }
    }
    next() {
      const X = I();
      if (!this.queue.length) return;
      const [addr, n] = this.waiting = this.queue.shift();
      const u = [0, 0x59, 0x30, 0, 0, 0, 0, addr & 255, (addr >>> 8) & 255, (addr >>> 16) & 255, (addr >>> 24) & 255, n & 255, n >> 8, 0];
      let s = 0; for (let k = 6; k < 14; k++) s += u[k];
      u.push(~s & 0xFF);
      this.tx([0xF0, ...X.pack7(u), 0xF7]);
    }
  }

  // a package as fm1pkg_make writes one: the identity in the markers, a header, a file list, flash.bin with its CRC
  function makePackage(product = "FM-1_958", o = {}) {
    const X = I(), size = o.size || 0x2000, seed = o.seed || 7;
    const img = new Uint8Array(size);
    for (let i = 0; i < size; i++) img[i] = (i * seed) & 0xFF;
    if (o.mark !== false) img.set(Array.from(X.LOADER_MARK, (c) => c.charCodeAt(0)), 0x1800);
    const data = img.subarray(0x400);
    const e = new Uint8Array(0x50), dv = new DataView(e.buffer);
    dv.setUint16(4, X.crc16(data), true); dv.setUint32(8, 0x400, true); dv.setUint32(12, data.length, true);
    e.set(Array.from("flash.bin", (c) => c.charCodeAt(0)), 0x40);
    const lst = X.dec(e);
    const hdr = new Uint8Array(0x40), hv = new DataView(hdr.buffer);
    hv.setUint32(4, size, true); hv.setUint16(8, 1, true); hv.setUint16(2, X.crc16(lst), true);
    hv.setUint16(0, X.crc16(hdr.subarray(2)), true);
    img.set(X.dec(hdr), 0); img.set(lst, 0x40);
    if (o.damage) img[0x1000] ^= 1;
    const raw = new Uint8Array(size + 20);
    for (let i = 0; i < 20; i++) {
      raw.set(img.subarray(i * 0x2F, (i + 1) * 0x2F), i * 0x30);
      raw[i * 0x30 + 0x2F] = i < product.length ? (product.charCodeAt(i) + i + 1) & 0xFF : 0x7D;
    }
    raw.set(img.subarray(20 * 0x2F), 20 * 0x30);
    return raw;
  }

  const api = { FakeFM1, makePackage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FakeFM1Kit = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
