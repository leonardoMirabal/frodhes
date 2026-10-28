// SPDX-License-Identifier: GPL-3.0-only
// Frodhes: the install on an FM-1 or an SMK-37 (Pro, Elite, MKE-P37) over USB-MIDI, in the browser (web/install.js)
// and in node (tests/web/install_test.js).
// The same update as tools/fm1_install.py, written again in JS: the package checks (its identity, the loader mark,
// the CRCs), then step 1 (the running firmware reads parts of the package and restarts as the update loader) and
// step 2 (the loader reads the whole package, writes it and restarts), the device asking and this answering; then
// the identity is checked. A device already in update mode (an install cut off) gets step 2 only.
//
// The MIDI side is a backend: { inputNames(), outputNames(), open(inName, outName) -> link }, a link being
// { send(bytes) -> bool, read(ms) -> Promise<bytes | null>, drain(), lost (getter), close() }. install.js makes one of
// Web MIDI; the tests one of a fake FM-1.
(function (root) {
  "use strict";

  const HS_QUERY = [0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7];
  const UPGRADE = [0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7];
  const FINISH_CHECK = 0xE0000000, FINISH_WRITE = 0xF0000000;
  const MAXDATA = 512, BLOCKS = 20, BLK = 0x30, KEEP = 0x2F, MAX_BLOCKS = 64;
  const LOADER_MARK = "FELUCCA-LOADER-1";
  const PRODUCT = "FM-1_958";                         // the FM-1's package
  // the packages this page installs, by the device's model (the identity before the "_"); the SMK-37 builds are
  // experimental, as on the release page. file: the fixed name next to the page (tools/web_firmware.sh)
  const PRODUCTS = [
    { model: "FM-1", product: "FM-1_958", file: "frodhes.fwsc" },
    { model: "SMK-37 Pro", product: "SMK-37 Pro_470", file: "frodhes-smk37-experimental.fwsc", experimental: true },
    { model: "SMK-37 Elite", product: "SMK-37 Elite_470", file: "frodhes-smk37-elite-experimental.fwsc", experimental: true },
    { model: "MKE-P37", product: "MKE-P37_470", file: "frodhes-smk37-mke-experimental.fwsc", experimental: true },
  ];
  const PORT_RE = /fm-1|frodhes|smk|elite|mke-p37|felucca|sloop|ota|usb-midi/i;   // never ask other gear
  const OWN_LOADER_RE = /^ota-(FM-1|SMK-37 Pro|SMK-37 Elite|MKE-P37)_9\d\d$/i;   // our update loader (an install not finished)
  // ms; the tests shorten them
  const DELAY = { start: 2000, reply: 10, loader: 3000, reboot: 3000, retry: 1000, poll: 1000, hs: 1000,
                  idleCheck: 8000, idleWrite: 180000, waitLoader: 30000, waitReboot: 40000 };

  class InstallError extends Error {
    constructor(code, msg) { super(msg); this.code = code; }
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const now = () => Date.now();

  // ---- the wire: 8-bit data in 7-bit SysEx bytes, least significant bits first
  function pack7(data) {
    const out = [];
    let acc = 0, nb = 0;
    for (const b of data) {
      acc |= b << nb; nb += 8;
      while (nb >= 7) { out.push(acc & 0x7F); acc >>= 7; nb -= 7; }
    }
    if (nb) out.push(acc & 0x7F);
    return out;
  }
  function unpack7(s) {
    const out = [];
    let acc = 0, nb = 0;
    for (const b of s) {
      acc |= (b & 0x7F) << nb; nb += 7;
      while (nb >= 8) { out.push(acc & 0xFF); acc >>= 8; nb -= 8; }
    }
    return out;
  }
  const sum = (a, i, j) => { let s = 0; for (let k = i; k < j; k++) s += a[k]; return s; };
  const le = (a, i, n) => { let v = 0; for (let k = n - 1; k >= 0; k--) v = v * 256 + a[i + k]; return v; };
  const leBytes = (v, n) => { const o = []; for (let k = 0; k < n; k++) { o.push(v % 256); v = Math.floor(v / 256); } return o; };

  function response(addr, data, fl = 0) {
    const n = data.length;
    const body = [0x00, 0x59, 0x30, (n + 8) & 0xFF, ((n + 8) >> 8) & 0xFF, 0, fl, ...leBytes(addr, 4), n & 0xFF, (n >> 8) & 0xFF, 0];
    for (const b of data) body.push(b);
    body.push(~sum(body, 6, body.length) & 0xFF);
    return [0xF0, ...pack7(body), 0xF7];
  }
  function parseRequest(pkt) {               // -> {fl, addr, n} or null
    if (pkt.length < 2 || pkt[0] !== 0xF0 || pkt[pkt.length - 1] !== 0xF7) return null;
    const u = unpack7(pkt.slice(1, -1));
    if (u.length !== 15 || u[0] !== 0 || u[1] !== 0x59 || u[2] !== 0x30 || (~sum(u, 6, 14) & 0xFF) !== u[14]) return null;
    return { fl: u[6], addr: le(u, 7, 4), n: le(u, 11, 3) };
  }
  function parseIdentity(pkt) {              // -> {text, model, version, loader} or null
    if (pkt.length < 2 || pkt[0] !== 0xF0 || pkt[pkt.length - 1] !== 0xF7) return null;
    const d = unpack7(pkt.slice(1, -1));
    if (d.length !== 34 || d[0] !== 0 || d[1] !== 0x59 || d[2] !== 0x11) return null;
    let text = "";
    for (let i = 6; i < 33 && d[i]; i++) text += String.fromCharCode(d[i]);
    const m = /^([^_]+)_(\d+)$/.exec(text);
    return m ? { text, model: m[1], version: +m[2], loader: m[1].toLowerCase().startsWith("ota-") } : null;
  }
  const modelOf = (text) => text.split("_")[0].replace(/^ota-/i, "");
  const productFor = (text) => PRODUCTS.find((p) => p.model.toLowerCase() === modelOf(text).toLowerCase()) || null;

  // ---- the package: 20 interleave blocks (the identity in their marker bytes), then the update image
  function idChar(m, i) { const c = (m - i - 1) & 0xFF; return c >= 0x20 && c < 0x7F; }
  function identityBlocks(raw) {
    const marker = (k) => ((k + 1) * BLK <= raw.length ? raw[k * BLK + KEEP] : null);
    let n = BLOCKS, m;
    while (n < MAX_BLOCKS && (m = marker(n)) !== null && m !== 0x7D && idChar(m, n)) n++;
    return n > BLOCKS && marker(n) === 0x7D ? n : BLOCKS;
  }
  function blockCount(raw) {
    let n = identityBlocks(raw);
    while (n < MAX_BLOCKS && (n + 1) * BLK <= raw.length && raw[n * BLK + KEEP] === 0x7D) n++;
    return n;
  }
  function logicalImage(raw, blocks = BLOCKS) {
    const out = new Uint8Array(raw.length - blocks);
    let o = 0;
    for (let i = 0; i < blocks; i++) { out.set(raw.subarray(i * BLK, i * BLK + KEEP), o); o += KEEP; }
    out.set(raw.subarray(blocks * BLK), o);
    return out;
  }
  function deinterleave(raw) {
    const n = blockCount(raw), image = logicalImage(raw, n);
    if (n !== BLOCKS && packageDamage(image)) {
      const alt = logicalImage(raw, BLOCKS);
      if (!packageDamage(alt)) return [alt, BLOCKS];
    }
    return [image, n];
  }
  function productOf(raw) {
    if (raw.length < BLOCKS * BLK) throw new InstallError("badpkg", "not a Frodhes package (too short)");
    const ib = identityBlocks(raw), n = ib === BLOCKS ? BLOCKS : Math.min(ib, deinterleave(raw)[1]);
    let s = "";
    for (let i = 0; i < n; i++) { const m = raw[i * BLK + KEEP]; if (m !== 0x7D) s += String.fromCharCode((m - i - 1) & 0xFF); }
    return s;
  }
  function crc16(data, crc = 0) {            // CRC-16/XMODEM, as the package and the device use
    for (const b of data) {
      crc ^= b << 8;
      for (let k = 0; k < 8; k++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
    }
    return crc;
  }
  function dec(b) {                          // the JieLi ENC stream cipher (its own inverse)
    const out = Uint8Array.from(b);
    let key = 0xFFFF;
    for (let i = 0; i < out.length; i++) {
      out[i] ^= key & 0xFF;
      key = ((key << 1) ^ (key & 0x8000 ? 0x1021 : 0)) & 0xFFFF;
    }
    return out;
  }
  function packageDamage(image) {            // what is wrong with the update image, or null
    if (image.length < 0x40) return "too short";
    const hdr = dec(image.subarray(0, 0x40));
    if (crc16(hdr.subarray(2, 0x40)) !== le(hdr, 0, 2)) return "header CRC";
    const count = le(hdr, 8, 2);
    if (count < 1 || count > 8 || image.length < 0x40 + 0x50 * count) return "file list";
    const lst = image.subarray(0x40, 0x40 + 0x50 * count);
    if (crc16(lst) !== le(hdr, 2, 2)) return "file list CRC";
    for (let i = 0; i < count; i++) {
      const e = dec(lst.subarray(i * 0x50, (i + 1) * 0x50));
      const dcrc = le(e, 4, 2), off = le(e, 8, 4), size = le(e, 12, 4), typ = le(e, 0, 2);
      let name = "";
      for (let k = 0x40; k < 0x50 && e[k]; k++) name += String.fromCharCode(e[k]);
      if (off + size > image.length) return `${name}: cut short`;
      if ((typ === 0 || typ === 100) && crc16(image.subarray(off, off + size)) !== dcrc) return `${name}: CRC`;
    }
    return null;
  }
  function hasMark(raw) {
    const m = Array.from(LOADER_MARK, (c) => c.charCodeAt(0));
    outer: for (let i = 0; i + m.length <= raw.length; i++) {
      if (raw[i] !== m[0]) continue;
      for (let k = 1; k < m.length; k++) if (raw[i + k] !== m[k]) continue outer;
      return true;
    }
    return false;
  }
  async function sha256hex(bytes) {
    const subtle = (root.crypto && root.crypto.subtle) || require("crypto").webcrypto.subtle;
    const h = new Uint8Array(await subtle.digest("SHA-256", bytes));
    return Array.from(h, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // the package: its identity, the loader mark, the CRCs, and (given) its sha256 -> {product, image, sha256}
  async function loadPackage(raw, wantSha) {
    raw = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    const sha = await sha256hex(raw);
    if (wantSha && sha !== wantSha.toLowerCase())
      throw new InstallError("badpkg", `the package's sha256 is ${sha}, not the published ${wantSha}: download it again`);
    const product = productOf(raw);
    if (!PRODUCTS.some((p) => p.product === product))
      throw new InstallError("badpkg", `this package is ${product || "not a Frodhes package"}; this page installs ${PRODUCTS.map((p) => p.product).join(", ")} only`);
    if (!hasMark(raw)) throw new InstallError("badpkg", "no Felucca update loader in this package");
    const [image] = deinterleave(raw);
    const bad = packageDamage(image);
    if (bad) throw new InstallError("badpkg", `damaged package (${bad}); nothing was written. Download it again`);
    return { product, image, sha256: sha };
  }

  // ---- the update
  function pairOutput(name, outs) {
    if (outs.includes(name)) return name;
    const base = (s) => s.replace(/\s+\d+$/, "");
    const same = outs.filter((o) => base(o) === base(name));
    if (same.length === 1) return same[0];
    return outs.length === 1 ? outs[0] : null;
  }
  async function handshake(link, tries = 3) {
    link.drain();
    for (let t = 0; t < tries; t++) {
      if (!link.send(HS_QUERY)) return null;
      const end = now() + DELAY.hs;
      for (let left; (left = end - now()) > 0;) {
        const p = await link.read(left);
        if (!p) break;
        const id = parseIdentity(p);
        if (id) return id;
      }
    }
    return null;
  }
  async function find(backend, want) {      // the first FM-1- or SMK-37-looking port that answers (and want(identity))
    const outs = backend.outputNames();
    for (const name of backend.inputNames().filter((n) => PORT_RE.test(n))) {
      const out = pairOutput(name, outs);
      if (!out) continue;
      let link;
      try { link = backend.open(name, out); } catch (e) { continue; }
      const id = await handshake(link, 2);
      if (id && (!want || want(id))) return { link, id, name };
      link.close();
    }
    return null;
  }
  async function waitFor(backend, want, ms) {
    const end = now() + ms;
    while (now() < end) {
      const dev = await find(backend, want);
      if (dev) return dev;
      await sleep(DELAY.retry);
    }
    return null;
  }
  // answer the device's reads until it asks for `finish` -> {served, finished, lost}
  async function serve(link, image, finish, idle, progress) {
    let served = 0, last = now();
    for (;;) {
      const pkt = await link.read(DELAY.poll);
      if (!pkt) {
        if (link.lost) return { served, finished: false, lost: true };
        if (now() - last > idle) return { served, finished: false, lost: false };
        continue;
      }
      const r = parseRequest(pkt);
      if (!r) continue;
      last = now();
      if (r.addr === FINISH_CHECK || r.addr === FINISH_WRITE) {
        link.send(response(r.addr, [0x73, 0x75, 0x63, 0x63, 0x65, 0x73, 0x73, 0]));   // "success\0"
        if (r.addr === finish) return { served, finished: true, lost: false };
        continue;
      }
      if (r.n > MAXDATA || r.addr + r.n > image.length)
        throw new InstallError("badreq", `the device asked for 0x${r.addr.toString(16)}+${r.n}, outside the package`);
      if (DELAY.reply) await sleep(DELAY.reply);
      if (!link.send(response(r.addr, image.subarray(r.addr, r.addr + r.n), r.fl))) return { served, finished: false, lost: true };
      served++;
      progress(served, r.addr + r.n);
    }
  }
  // the whole install on a device find() gave: running -> loader -> the new firmware. step(kind, value) reports:
  // "start" id, "check" reads, "loader", "write" percent, "reboot", "done" id
  async function install(backend, dev, pkg, step = () => {}) {
    const close = () => { try { dev.link.close(); } catch (e) { /* gone */ } };
    const unit = `the ${modelOf(dev.id.text)}`;
    if (modelOf(dev.id.text) !== modelOf(pkg.product)) {
      close();
      throw new InstallError("model", `the device is ${dev.id.text}; this package is for the ${modelOf(pkg.product)} (${pkg.product})`);
    }
    if (dev.id.loader && !OWN_LOADER_RE.test(dev.id.text)) {
      close();
      throw new InstallError("foreign", `${unit} is in the update mode of another firmware (${dev.id.text}): finish that update with its own updater, then install again`);
    }
    let ota = dev;
    if (!dev.id.loader) {
      step("start", dev.id.text);
      dev.link.send(UPGRADE);
      await sleep(DELAY.start);
      const r = await serve(dev.link, pkg.image, FINISH_CHECK, DELAY.idleCheck, (k) => step("check", k));
      close();
      if (!r.finished)
        throw new InstallError(r.lost ? "lost" : "stopped", `${unit} ${r.lost ? "was disconnected" : "stopped answering"} after ${r.served} requests; nothing was written`);
      step("loader");
      await sleep(DELAY.loader);
      ota = await waitFor(backend, (i) => i.loader, DELAY.waitLoader);
      if (!ota) throw new InstallError("noloader", `the update loader did not appear. Replug the USB cable and press Install again: ${unit} stays in update mode until it is done`);
    }
    step("write", 0);
    ota.link.send(UPGRADE);
    await sleep(DELAY.start);
    const len = pkg.image.length;
    const w = await serve(ota.link, pkg.image, FINISH_WRITE, DELAY.idleWrite, (k, end) => step("write", Math.min(99, Math.floor(end * 100 / len))));
    try { ota.link.close(); } catch (e) { /* gone */ }
    if (!w.finished)
      throw new InstallError(w.lost ? "lost" : "stopped", `the update loader ${w.lost ? "was disconnected" : "stopped answering"} after ${w.served} requests. Replug the USB cable and press Install again to finish`);
    step("write", 100);
    step("reboot");
    await sleep(DELAY.reboot);
    const back = await waitFor(backend, (i) => !i.loader, DELAY.waitReboot);
    if (!back) throw new InstallError("noreturn", `${unit} did not come back: switch it off and on`);
    back.link.close();
    if (back.id.text !== pkg.product) throw new InstallError("mismatch", `written, but ${unit} reports ${back.id.text} (expected ${pkg.product})`);
    step("done", back.id.text);
    return back.id.text;
  }

  // ---- Web MIDI as a backend (navigator.requestMIDIAccess({sysex: true})'s MIDIAccess; the tests: a fake one)
  function webMidiBackend(access) {
    const live = (m) => Array.from(m.values()).filter((p) => p.state !== "disconnected");
    return {
      inputNames: () => live(access.inputs).map((p) => p.name),
      outputNames: () => live(access.outputs).map((p) => p.name),
      open(inName, outName) {
        const inp = live(access.inputs).find((p) => p.name === inName), out = live(access.outputs).find((p) => p.name === outName);
        if (!inp || !out) throw new Error("no such port");
        const q = [], waiters = [];
        const onmsg = (e) => {
          const d = e.data;
          if (!d || d[0] !== 0xF0) return;              // SysEx only (the FM-1 also sends notes, clock ...)
          const b = Array.from(d);
          if (waiters.length) waiters.shift()(b); else q.push(b);
        };
        inp.addEventListener("midimessage", onmsg);
        if (inp.open) inp.open();
        let closed = false;
        return {
          send(b) {
            if (closed || out.state === "disconnected") return false;
            try { out.send(b); return true; } catch (e) { return false; }
          },
          read(ms) {
            if (q.length) return Promise.resolve(q.shift());
            return new Promise((res) => {
              const w = (b) => { clearTimeout(t); res(b); };
              const t = setTimeout(() => { const i = waiters.indexOf(w); if (i >= 0) waiters.splice(i, 1); res(null); }, Math.max(ms, 0));
              waiters.push(w);
            });
          },
          drain() { q.length = 0; },
          get lost() { return inp.state === "disconnected" || !live(access.inputs).includes(inp); },
          close() { closed = true; inp.removeEventListener("midimessage", onmsg); },
        };
      },
    };
  }

  const api = { webMidiBackend, HS_QUERY, UPGRADE, FINISH_CHECK, FINISH_WRITE, LOADER_MARK, PRODUCT, PRODUCTS, modelOf, productFor, DELAY, InstallError, pack7, unpack7,
                response, parseRequest, parseIdentity, productOf, logicalImage, deinterleave, packageDamage, crc16, dec,
                sha256hex, loadPackage, handshake, find, waitFor, install };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FM1Install = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
