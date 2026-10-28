// SPDX-License-Identifier: GPL-3.0-only
// web/fm1install.js (the page's installer) against a fake FM-1 behind a fake Web MIDI (tests/web/fake_fm1.js), in
// node: a full install on the FM-1 and on each SMK-37 model (Frodhes or the stock firmware running, the package for
// the model), a device of another model and a package of another identity refused, a cut in the write and
// the resume from update mode, a stall, a corrupt package, a wrong sha256 and a package without the loader refused,
// another firmware's update loader refused, the wire format against tools/fm1_install.py's (when python3 is there).
//   node tests/web/install_test.js
"use strict";
const X = require("../../web/fm1install.js");
const { FakeFM1, makePackage } = require("./fake_fm1.js");
const { execFileSync } = require("child_process");
const path = require("path");

Object.assign(X.DELAY, { start: 5, reply: 0, loader: 5, reboot: 5, retry: 10, poll: 20, hs: 60, idleCheck: 300,
                         idleWrite: 300, waitLoader: 1000, waitReboot: 1000 });
let failed = 0;
const ok = (cond, what) => { console.log(`${what.padEnd(70)} ${cond ? "ok" : "FAIL"}`); failed += !cond; };
const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

async function run(dev, pkg) {
  const backend = X.webMidiBackend(dev.access), steps = [];
  const d = await X.find(backend);
  if (!d) return { err: "notfound", steps };
  try {
    const id = await X.install(backend, d, pkg, (k, v) => steps.push([k, v]));
    return { id, steps };
  } catch (e) {
    return { err: e.code, msg: e.message, steps };
  }
}

async function main() {
  const raw = makePackage("FM-1_958");
  const pkg = await X.loadPackage(raw);
  const image = pkg.image;

  // the wire and the package, against the Python installer (the same bytes)
  const data = Array.from({ length: 300 }, (_, i) => (i * 37) & 255);
  ok(same(X.unpack7(X.pack7(data)).slice(0, 300), data), "pack7 / unpack7 round trip");
  let py = null;
  try {
    const tmp = path.join(require("os").tmpdir(), `fm1-web-${process.pid}.fwsc`);
    require("fs").writeFileSync(tmp, raw);
    py = JSON.parse(execFileSync("python3", ["-c", `
import sys, json, hashlib; sys.path.insert(0, "tools"); import fm1_install as I
raw = open(sys.argv[1], "rb").read()
print(json.dumps({"resp": list(I.response(0x12345, bytes([1, 2, 3]), 5)), "pack": list(I.pack7(bytes(range(256)))),
                  "product": I.product_of(raw), "image": hashlib.sha256(I.deinterleave(raw)[0]).hexdigest(),
                  "damage": I.package_damage(I.deinterleave(raw)[0])}))`, tmp],
      { cwd: path.join(__dirname, "..", ".."), encoding: "utf8" }));
    require("fs").unlinkSync(tmp);
  } catch (e) { console.log("(python3 not found: the cross-check skipped)"); }
  if (py) {
    ok(same(X.response(0x12345, [1, 2, 3], 5), py.resp) && same(X.pack7(Array.from({ length: 256 }, (_, i) => i)), py.pack),
       "response / pack7: byte for byte as tools/fm1_install.py");
  }
  if (py) ok(py.product === "FM-1_958" && py.damage === null && py.image === await X.sha256hex(image),
             "the synthetic package: product, image and CRCs as tools/fm1_install.py reads them");
  ok(pkg.product === "FM-1_958" && image.length === raw.length - 20 && /^[0-9a-f]{64}$/.test(pkg.sha256),
     "loadPackage: identity FM-1_958, the logical image, the sha256");

  // a full install: running (stock V15) -> loader -> FM-1_958
  let dev = new FakeFM1(image, { others: ["IAC Driver Bus 1"] });
  let r = await run(dev, pkg);
  ok(r.id === "FM-1_958" && dev.bad === 0 && dev.upgrades === 2 && same(Array.from(dev.written), Array.from(image)),
     `full install: running -> loader -> FM-1_958 (${dev.served} answers, the image written whole)`);
  const kinds = r.steps.map((s) => s[0]);
  ok(["start", "check", "loader", "write", "reboot", "done"].every((k) => kinds.includes(k)) &&
     r.steps.some((s) => s[0] === "write" && s[1] === 100), "the steps: start, check, loader, write 0..100 %, reboot, done");
  ok(dev.served >= image.length / 512 + 4, "every block of the image read by the loader");

  // over itself (Frodhes running)
  dev = new FakeFM1(image, { identity: "FM-1_958", name: "Frodhes" });
  r = await run(dev, pkg);
  ok(r.id === "FM-1_958" && dev.bad === 0, "install over Frodhes itself");
  dev = new FakeFM1(image, { identity: "FM-1_958", name: "Frodhes FM-1" });
  r = await run(dev, pkg);
  ok(r.id === "FM-1_958" && dev.bad === 0, "install over Frodhes on the 0.9.7 port name 'Frodhes FM-1'");

  // the SMK-37 family: each model's package on its own model, over Frodhes (the 0.9.7 port names) or the stock firmware
  for (const [model, port, stock] of [["SMK-37 Pro", "Frodhes SMK-37 Pro", "SMK-37 Pro"], ["SMK-37 Elite", "Frodhes SMK-37 Elite", "SMK-37 Elite"],
                                      ["MKE-P37", "Frodhes MKE-P37", "MKE-P37"]]) {
    const want = X.productFor(model + "_470");
    ok(want && want.product === model + "_470" && want.experimental, `productFor: ${model} -> ${want && want.product}, experimental`);
    const sp = await X.loadPackage(makePackage(model + "_470"));
    for (const [what, identity, name] of [["over Frodhes", model + "_470", port], ["over the stock firmware", model + "_016", stock]]) {
      dev = new FakeFM1(sp.image, { identity, name, loader: [`ota-${model}_900`, "Felucca Update"], afterWrite: model + "_470", afterName: port });
      r = await run(dev, sp);
      ok(r.id === model + "_470" && dev.bad === 0 && dev.upgrades === 2 && same(Array.from(dev.written), Array.from(sp.image)),
         `${model} ${what} ('${name}'): installed, the image written whole`);
    }
    dev = new FakeFM1(sp.image, { identity: `ota-${model}_900`, name: "Felucca Update", afterWrite: model + "_470", afterName: port });
    r = await run(dev, sp);
    ok(r.id === model + "_470" && dev.upgrades === 1, `${model}: an install cut off resumes from its update loader`);
    dev = new FakeFM1(image, { identity: model + "_470", name: port });
    r = await run(dev, pkg);
    ok(r.err === "model" && dev.upgrades === 0, `the FM-1 package on the ${model} ('${port}') refused, nothing sent but the handshake`);
  }
  ok(X.productFor("FM-1_015").product === "FM-1_958" && X.productFor("ota-FM-1_900").product === "FM-1_958" && X.productFor("SLOOP_1") === null,
     "productFor: the FM-1 (running or its loader); an unknown model none");

  // wrong identities: an SMK-37 behind a generic port name; a package for another model; another firmware's loader
  dev = new FakeFM1(image, { identity: "SMK-37 Pro_470", name: "USB-MIDI" });
  r = await run(dev, pkg);
  ok(r.err === "model" && dev.upgrades === 0, "an SMK-37 (behind a generic port name) and the FM-1 package refused, nothing sent");
  dev = new FakeFM1(image, { identity: "FM-1_015", name: "FM-1" });
  const smk = { product: "SMK-37 Pro_470", image, sha256: "" };
  r = await run(dev, smk);
  ok(r.err === "model" && dev.upgrades === 0, "an SMK-37 package on the FM-1 refused before anything is sent");
  dev = new FakeFM1(image, { identity: "ota-FM-1_015", name: "FM-1 Update" });
  r = await run(dev, pkg);
  ok(r.err === "foreign" && dev.upgrades === 0, "another firmware's update loader refused");
  let e = null;
  try { await X.loadPackage(makePackage("SLOOP-1_001")); } catch (x) { e = x; }
  ok(e && e.code === "badpkg" && /MKE-P37_470 only/.test(e.message), "loadPackage: a package of another identity refused");
  e = null;
  try { await X.loadPackage(makePackage("SMK-37 Pro_016")); } catch (x) { e = x; }
  ok(e && e.code === "badpkg", "loadPackage: a stock SMK-37 package (not Frodhes) refused");

  // a cut in the write, then the resume from update mode
  dev = new FakeFM1(image, { unplugAfter: 4 + 6 });
  r = await run(dev, pkg);
  ok(r.err === "lost" && /press Install again/.test(r.msg), `cut mid-write: lost (${r.msg.split(".")[0]})`);
  dev.replug();
  ok(dev.identity === "ota-FM-1_958", "replugged: the FM-1 comes back in update mode");
  dev.served = 0; dev.bad = 0; dev.upgrades = 0;
  r = await run(dev, pkg);
  ok(r.id === "FM-1_958" && dev.bad === 0 && dev.upgrades === 1 && same(Array.from(dev.written), Array.from(image)),
     "resume: the write finished from update mode, the image whole");

  // a stall in step 1: nothing written
  dev = new FakeFM1(image, { stallAfter: 2 });
  r = await run(dev, pkg);
  ok(r.err === "stopped" && /nothing was written/.test(r.msg) && dev.writes === 0, "a stall in step 1: stopped, nothing written");

  // corrupt packages
  for (const [what, p, want] of [["a damaged file", makePackage("FM-1_958", { damage: true }), /damaged package/],
                                 ["no loader mark", makePackage("FM-1_958", { mark: false }), /no Felucca update loader/],
                                 ["too short", raw.subarray(0, 100), /too short/]]) {
    e = null;
    try { await X.loadPackage(p); } catch (x) { e = x; }
    ok(e && e.code === "badpkg" && want.test(e.message), `corrupt package refused: ${what}`);
  }
  e = null;
  try { await X.loadPackage(raw, "00".repeat(32)); } catch (x) { e = x; }
  ok(e && e.code === "badpkg" && /sha256/.test(e.message), "a package whose sha256 is not the published one refused");
  ok((await X.loadPackage(raw, pkg.sha256)).product === "FM-1_958", "the published sha256 accepted");

  console.log(failed ? `WEB INSTALL TESTS FAILED (${failed})` : "web install tests passed");
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
