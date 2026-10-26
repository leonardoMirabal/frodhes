// SPDX-License-Identifier: GPL-3.0-only
// The page's "Install on your FM-1" section: the release's package (frodhes.fwsc and firmware.json, published with
// the page by release.yml: GitHub's release downloads cannot be fetched from a page) or a file chosen here, checked
// (its sha256, its identity FM-1_958, the CRCs), then installed over Web MIDI with SysEx by web/fm1install.js.
// Chrome and Edge only: Safari has no Web MIDI and Firefox none without an add-on; they get the command line.
// ?installtest (tools/release.py's selftest, Chromium): a fake FM-1 (tests/web/fake_fm1.js, injected) and a
// synthetic package go through the same steps; the result is in the title.
(function () {
  "use strict";
  const X = window.FM1Install;
  const $ = (id) => document.getElementById(id);
  let pub = null, pkg = null, backend = null, dev = null, busy = false;

  function say(msg, kind) { const s = $("inst-msg"); s.textContent = msg; s.className = kind || ""; }
  function bar(v) { const b = $("inst-bar"); if (v === null) b.removeAttribute("value"); else b.value = v; }
  function buttons() {
    $("inst-go").disabled = busy || !(pkg || pub);
    $("inst-go").textContent = dev && dev.id.loader ? "Resume the install" : dev ? `Install on ${dev.id.text}` : "Find my FM-1";
    $("inst-file").disabled = busy;
  }
  function showPackage(p, from) {
    $("inst-ver").textContent = from;
    $("inst-sha").textContent = p.sha256;
  }

  async function published() {          // firmware.json + frodhes.fwsc next to the page (a release's Pages build)
    try {
      const r = await fetch("firmware.json", { cache: "no-store" });
      if (!r.ok) throw new Error();
      pub = await r.json();
      if (!pkg) $("inst-ver").textContent = `Frodhes ${pub.version} (${pub.identity}, ${pub.size} bytes)`;
      if (!pkg) $("inst-sha").textContent = pub.sha256;
    } catch (e) {
      pub = null;
      if (!pkg) {
        $("inst-ver").textContent = "none published with this page (a development build): choose a .fwsc file";
        $("inst-sha").textContent = "-";
      }
    }
    buttons();
  }
  async function packageBytes() {
    if (pkg) return pkg;
    if (!pub) throw new X.InstallError("badpkg", "no package: choose a .fwsc file");
    const r = await fetch("frodhes.fwsc", { cache: "no-store" });
    if (!r.ok) throw new X.InstallError("badpkg", `the package did not download (HTTP ${r.status})`);
    pkg = await X.loadPackage(new Uint8Array(await r.arrayBuffer()), pub.sha256);
    return pkg;
  }
  async function midi() {
    if (backend) return backend;
    const acc = await navigator.requestMIDIAccess({ sysex: true });
    backend = X.webMidiBackend(acc);
    return backend;
  }
  const STEP = { start: "Starting the update", check: "Step 1 of 2: the FM-1 checks the package", loader: "Switching to update mode",
                 reboot: "Restarting" };
  async function go() {
    if (busy) return;
    busy = true; buttons();
    try {
      const p = await packageBytes();
      const b = await midi();
      if (!dev) {
        say("Looking for the FM-1 ...");
        dev = await X.find(b);
        if (!dev) throw new X.InstallError("notfound", "FM-1 not found: connect it with a USB data cable, close other apps using its MIDI port, and try again");
        say(dev.id.loader ? `The FM-1 is in update mode (${dev.id.text}): an install was interrupted. Press "Resume the install" to finish it.`
                          : `Found ${dev.id.text}. Press "Install on ${dev.id.text}" to install ${p.product}.`);
        return;
      }
      $("inst-warn").hidden = false;
      const d = dev;
      dev = null;
      await X.install(b, d, p, (k, v) => {
        if (k === "write") { bar(v); say(`Step 2 of 2: writing ${v} %`); }
        else if (k === "done") { bar(100); say(`Done: the FM-1 runs ${v}.`, "ok"); }
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
      say("Package checked. Press the button to find the FM-1.");
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

  // ?installtest: the fake FM-1 (stock V15) and a synthetic FM-1_958 package, through go() as a click would
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
    document.title = `INSTALLTEST ${good ? "ok" : "FAIL"} ${msg} [${found}] bad=${fake.bad}`;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
