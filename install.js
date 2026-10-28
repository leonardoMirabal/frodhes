// SPDX-License-Identifier: GPL-3.0-only
// The page's "Install Frodhes" section: the device is found first, then the release's package for its model
// (firmware.json lists them: the FM-1's frodhes.fwsc and the three SMK-37 builds, published with the page by
// release.yml, as GitHub's release downloads cannot be fetched from a page) or a file chosen here, checked (its
// sha256, its identity, the CRCs), then installed over Web MIDI with SysEx by web/fm1install.js.
// Chrome and Edge only: Safari has no Web MIDI and Firefox none without an add-on; they get the command line.
// ?installtest (tools/release.py's selftest, Chromium): a fake FM-1 (tests/web/fake_fm1.js, injected) and a
// synthetic package go through the same steps; the result is in the title.
(function () {
  "use strict";
  const X = window.FM1Install;
  const $ = (id) => document.getElementById(id);
  let pub = null, pkg = null, fetched = null, backend = null, dev = null, busy = false;   // pkg: a file chosen here

  function say(msg, kind) { const s = $("inst-msg"); s.textContent = msg; s.className = kind || ""; }
  function bar(v) { const b = $("inst-bar"); if (v === null) b.removeAttribute("value"); else b.value = v; }
  function buttons() {
    $("inst-go").disabled = busy || !(pkg || pub);
    $("inst-go").textContent = dev && dev.id.loader ? "Resume the install" : dev ? `Install on ${dev.id.text}` : "Find my FM-1 or SMK-37";
    $("inst-file").disabled = busy;
  }
  function showPackage(p, from) {
    $("inst-ver").textContent = from;
    $("inst-sha").textContent = p.sha256;
  }

  // firmware.json's packages (an older one has only the FM-1's, at its top level)
  const packages = () => (pub.packages || [{ identity: pub.identity, file: pub.file || "frodhes.fwsc", size: pub.size, sha256: pub.sha256 }]);
  async function published() {          // firmware.json + the packages next to the page (a release's Pages build)
    try {
      const r = await fetch("firmware.json", { cache: "no-store" });
      if (!r.ok) throw new Error();
      pub = await r.json();
      if (!pkg) $("inst-ver").textContent = `Frodhes ${pub.version} for ${packages().map((e) => e.identity.split("_")[0]).join(", ")}: the package for your model is chosen when it is found`;
      if (!pkg) $("inst-sha").textContent = "-";
    } catch (e) {
      pub = null;
      if (!pkg) {
        $("inst-ver").textContent = "none published with this page (a development build): choose a .fwsc file";
        $("inst-sha").textContent = "-";
      }
    }
    buttons();
  }
  async function packageFor(id) {        // the chosen file, or the published package for the device's model
    if (pkg) return pkg;
    const want = X.productFor(id.text);
    const e = pub && want && packages().find((p) => p.identity === want.product);
    if (!e) throw new X.InstallError("badpkg", `no package for the ${X.modelOf(id.text)} on this page: choose its .fwsc file`);
    if (fetched && fetched.product === e.identity) return fetched;
    const r = await fetch(e.file, { cache: "no-store" });
    if (!r.ok) throw new X.InstallError("badpkg", `the package did not download (HTTP ${r.status})`);
    fetched = await X.loadPackage(new Uint8Array(await r.arrayBuffer()), e.sha256);
    if (fetched.product !== e.identity) throw new X.InstallError("badpkg", `${e.file} is ${fetched.product}, not ${e.identity}`);
    showPackage(fetched, `Frodhes ${pub.version} for the ${want.model} (${e.identity}, ${e.size} bytes${want.experimental ? ", experimental" : ""})`);
    return fetched;
  }
  async function midi() {
    if (backend) return backend;
    const acc = await navigator.requestMIDIAccess({ sysex: true });
    backend = X.webMidiBackend(acc);
    return backend;
  }
  const STEP = { start: "Starting the update", check: "Step 1 of 2: the device checks the package", loader: "Switching to update mode",
                 reboot: "Restarting" };
  async function go() {
    if (busy) return;
    busy = true; buttons();
    try {
      if (!pkg && !pub) throw new X.InstallError("badpkg", "no package: choose a .fwsc file");
      const b = await midi();
      if (!dev) {
        say("Looking for the FM-1 or SMK-37 ...");
        const d = await X.find(b);
        if (!d) throw new X.InstallError("notfound", "No FM-1 or SMK-37 found: connect it with a USB data cable, close other apps using its MIDI port, and try again");
        let p;
        try { p = await packageFor(d.id); } catch (e) { d.link.close(); throw e; }
        if (X.modelOf(d.id.text) !== X.modelOf(p.product)) {
          d.link.close();
          throw new X.InstallError("model", `the device is ${d.id.text}; this package is for the ${X.modelOf(p.product)} (${p.product}): nothing was sent`);
        }
        dev = d;
        const unit = `the ${X.modelOf(dev.id.text)}`;
        say(dev.id.loader ? `${unit[0].toUpperCase() + unit.slice(1)} is in update mode (${dev.id.text}): an install was interrupted. Press "Resume the install" to finish it.`
                          : `Found ${dev.id.text}. Press "Install on ${dev.id.text}" to install ${p.product}${X.productFor(p.product).experimental ? " (experimental)" : ""}.`);
        return;
      }
      const p = await packageFor(dev.id);
      $("inst-warn").hidden = false;
      const d = dev;
      dev = null;
      await X.install(b, d, p, (k, v) => {
        if (k === "write") { bar(v); say(`Step 2 of 2: writing ${v} %`); }
        else if (k === "done") { bar(100); say(`Done: the ${X.modelOf(v)} runs ${v}.`, "ok"); }
        else { bar(null); say(STEP[k] + (k === "start" ? ` on ${v}` : "") + " ..."); }
      });
    } catch (e) {
      dev = null;
      say(e.code ? e.message : e.name === "SecurityError" || e.name === "NotAllowedError"
        ? "MIDI with SysEx was not allowed: allow it for this page (the lock icon) and try again" : String(e.message || e), "err");
      bar(0);
    } finally {
      busy = false; $("inst-warn").hidden = true; buttons();
    }
  }
  async function chooseFile(ev) {
    const f = ev.target.files[0];
    if (!f) return;
    try {
      pkg = await X.loadPackage(new Uint8Array(await f.arrayBuffer()));
      showPackage(pkg, `${f.name}: ${pkg.product}, ${f.size} bytes (chosen here)`);
      say("Package checked. Press the button to find the device.");
    } catch (e) {
      pkg = null;
      say(e.message, "err");
    }
    buttons();
  }

  function init() {
    if (!$("install")) return;
    if (!navigator.requestMIDIAccess && !location.search.includes("installtest")) {
      $("inst-ui").hidden = true;
      $("inst-none").hidden = false;
      return;
    }
    $("inst-go").onclick = go;
    $("inst-file").onchange = chooseFile;
    published();
    if (location.search.includes("installtest")) selftest();
  }

  // ?installtest: the fake FM-1 (stock V15) and a synthetic FM-1_958 package chosen as a file, then a fake SMK-37 Pro
  // (Frodhes) with its package picked from a firmware.json, through go() as a click would
  async function selftest() {
    const K = window.FakeFM1Kit;
    if (!K) { document.title = "INSTALLTEST FAIL no fake"; return; }
    Object.assign(X.DELAY, { start: 5, reply: 0, loader: 5, reboot: 5, retry: 20, poll: 30, hs: 80, idleCheck: 500,
                             idleWrite: 500, waitLoader: 2000, waitReboot: 2000 });
    const raw = K.makePackage("FM-1_958");
    pkg = await X.loadPackage(raw);
    const fake = new K.FakeFM1(pkg.image);
    backend = X.webMidiBackend(fake.access);
    showPackage(pkg, "the selftest's synthetic package");
    await go();                                   // find
    const found = $("inst-go").textContent;
    await go();                                   // install
    const msg = $("inst-msg").textContent;
    const good = /Install on FM-1_015/.test(found) && /synthetic/.test($("inst-ver").textContent) && $("inst-sha").textContent === pkg.sha256 && /Done: the FM-1 runs FM-1_958/.test(msg) &&
                 fake.bad === 0 && $("inst-bar").value === 100;
    if (!good) { document.title = `INSTALLTEST FAIL ${msg} [${found}] bad=${fake.bad}`; return; }
    // then an SMK-37 Pro running Frodhes, nothing chosen: the page picks its package from firmware.json
    const sraw = K.makePackage("SMK-37 Pro_470"), sp = await X.loadPackage(sraw);
    pkg = null; fetched = null;
    pub = { version: "selftest", packages: [{ identity: "FM-1_958", file: "frodhes.fwsc", size: raw.length, sha256: "00".repeat(32) },
                                             { identity: "SMK-37 Pro_470", file: "smk.fwsc", size: sraw.length, sha256: sp.sha256 }] };
    const realFetch = window.fetch;
    window.fetch = (u, o) => (u === "smk.fwsc" ? Promise.resolve(new Response(sraw)) : realFetch(u, o));
    const sfake = new K.FakeFM1(sp.image, { identity: "SMK-37 Pro_470", name: "Frodhes SMK-37 Pro", loader: ["ota-SMK-37 Pro_900", "Felucca Update"],
                                            afterWrite: "SMK-37 Pro_470", afterName: "Frodhes SMK-37 Pro" });
    backend = X.webMidiBackend(sfake.access);
    await go();
    const sfound = $("inst-go").textContent, sver = $("inst-ver").textContent, ssha = $("inst-sha").textContent;
    await go();
    window.fetch = realFetch;
    const smsg = $("inst-msg").textContent;
    const sgood = /Install on SMK-37 Pro_470/.test(sfound) && /for the SMK-37 Pro .*experimental/.test(sver) && ssha === sp.sha256 &&
                  /Done: the SMK-37 Pro runs SMK-37 Pro_470/.test(smsg) && sfake.bad === 0 && sfake.upgrades === 2;
    document.title = `INSTALLTEST ${sgood ? "ok" : "FAIL"} ${msg} / ${smsg} [${found}] [${sfound}] bad=${fake.bad}+${sfake.bad}`;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
