// SPDX-License-Identifier: GPL-3.0-only
// The FM-1's panel in a web page, around the Frodhes firmware compiled to WebAssembly (web_hal.c).
"use strict";

const BTN = { "OCT−": 0, "OCT+": 1, FX: 2, SEL: 3, ENV: 4, LFO: 5, EDIT: 6, GLO: 7, HOME: 8, SAVE: 9, ARP: 10, SEQ: 11, PLAY: 12, REC: 13 };
const BTN_ORDER = ["HOME", "EDIT", "ENV", "LFO", "SEQ", "SEL", "ARP", "GLO", "FX", "SAVE", "PLAY", "REC", "OCT−", "OCT+"];
const ENCS = [["SELECT", 0], ["ALGORITHM", 1], ["PRESETS", 6], ["K1", 2], ["K2", 3], ["K3", 4], ["K4", 5]];
const NKEYS = 27;                                   // F3 .. G5
const KEYMAP = "awsedrfgyhujkolp;".split("");       // F3, F#3, G3 ... chromatic
const STORE_KEY = "frodhes.settings";

let M = null, ctx = null, node = null, keysDown = 0, keysHeld = new Set();

function $(id) { return document.getElementById(id); }
function status(t) { $("status").textContent = t; }

// ---- the module (Emscripten, MODULARIZE off: a global Module)
var Module = window.Module = {
  onRuntimeInitialized() {
    M = Module;
    try {                                            // the settings record, before main() reads it
      const s = localStorage.getItem(STORE_KEY);
      if (s) {
        const b = Uint8Array.from(atob(s), c => c.charCodeAt(0));
        M.HEAPU8.set(b.subarray(0, 260), M._web_store());
      }
    } catch (e) { /* private window: no storage */ }
    M._web_master(+$("master").value);
    requestAnimationFrame(frame);
  },
  print: t => console.log(t), printErr: t => console.warn(t),
};

// ---- the screen and the LEDs, every animation frame
let img = null;
function frame() {
  const c = $("screen"), g = c.getContext("2d");
  if (!img) img = g.createImageData(240, 240);
  const fb = new Uint16Array(M.HEAPU8.buffer, M._web_fb(), 240 * 240), d = img.data;
  for (let i = 0, j = 0; i < 240 * 240; i++, j += 4) {
    const v = fb[i];                                 // RGB565
    d[j] = ((v >> 11) & 31) * 255 / 31; d[j + 1] = ((v >> 5) & 63) * 255 / 63; d[j + 2] = (v & 31) * 255 / 31; d[j + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  const led = new Uint8Array(M.HEAPU8.buffer, M._web_leds(), 64);
  for (const b of document.querySelectorAll("button.b")) b.classList.toggle("on", !!led[+b.dataset.id]);
  for (const k of document.querySelectorAll(".kb > div")) k.classList.toggle("lit", !!led[14 + (+k.dataset.k)]);
  if (M._web_store_take_dirty()) {
    try {
      const b = new Uint8Array(M.HEAPU8.buffer, M._web_store(), 260);
      localStorage.setItem(STORE_KEY, btoa(String.fromCharCode(...b)));
    } catch (e) { }
  }
  requestAnimationFrame(frame);
}

// ---- sound: the audio callback is the FM-1's audio interrupt (256-frame blocks at 44.1 kHz)
// the engine runs at 44.1 kHz; a phone that will not open a 44.1 kHz context gets the engine resampled (linear)
const ENG = 44100;
let fifo = new Float32Array(2 * 8192), fifoN = 0, frac = 0;
function pull(frames) {                              // engine frames into the fifo (L R interleaved)
  const blocks = Math.ceil(frames / 256), p = M._web_audio(blocks) >> 2, f = M.HEAPF32;
  fifo.set(f.subarray(p, p + 2 * 256 * blocks), 2 * fifoN);
  fifoN += 256 * blocks;
}
function startAudio() {                              // synchronous up to resume(): iOS wants it inside the tap
  try {
    if (navigator.audioSession) navigator.audioSession.type = "playback";   // iPhone: play with the silent switch on
  } catch (e) { }
  if (ctx) { if (ctx.state !== "running") ctx.resume(); return; }
  const AC = window.AudioContext || window.webkitAudioContext;
  try { ctx = new AC({ sampleRate: ENG, latencyHint: "interactive" }); } catch (e) { ctx = new AC(); }
  const ratio = ENG / ctx.sampleRate;
  node = ctx.createScriptProcessor(2048, 0, 2);
  node.onaudioprocess = ev => {
    const L = ev.outputBuffer.getChannelData(0), R = ev.outputBuffer.getChannelData(1), n = L.length;
    if (!M) { L.fill(0); R.fill(0); return; }
    if (ratio === 1) {
      const p = M._web_audio(n / 256) >> 2, f = M.HEAPF32;
      for (let i = 0; i < n; i++) { L[i] = f[p + 2 * i]; R[i] = f[p + 2 * i + 1]; }
      return;
    }
    const need = Math.ceil(n * ratio + frac) + 2;
    if (fifoN < need) pull(need - fifoN);
    let pos = frac;
    for (let i = 0; i < n; i++, pos += ratio) {
      const k = pos | 0, t = pos - k;
      L[i] = fifo[2 * k] + (fifo[2 * k + 2] - fifo[2 * k]) * t;
      R[i] = fifo[2 * k + 1] + (fifo[2 * k + 3] - fifo[2 * k + 1]) * t;
    }
    const used = pos | 0;
    frac = pos - used;
    fifo.copyWithin(0, 2 * used, 2 * fifoN);
    fifoN -= used;
  };
  node.connect(ctx.destination);
  const unlock = new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=");
  unlock.play().catch(() => { });                   // older iPhones: an <audio> play in the tap opens the audio session
  ctx.resume().then(() => {
    status(`Sound on (${ctx.sampleRate} Hz${ratio !== 1 ? ", resampled" : ""}). Play the keys, the computer keyboard or a MIDI keyboard.`);
    $("start").textContent = "Sound on";
  }, e => status("Sound could not start: " + e));
}
window.addEventListener("error", e => status("Error: " + e.message));
window.addEventListener("unhandledrejection", e => status("Error: " + e.reason));
$("start").onclick = startAudio;

// ---- the panel
function buildPanel() {
  const encs = $("encs");
  for (const [name, id] of ENCS) {
    const k = document.createElement("div"); k.className = "knob";
    k.innerHTML = `<div class="dial"></div><span>${name}</span>`;
    let angle = 0, lastY = null, acc = 0;
    const turn = d => { if (!M || !d) return; M._web_enc(id, d); angle += d * 15; k.firstChild.style.setProperty("--a", angle + "deg"); };
    k.addEventListener("wheel", e => { e.preventDefault(); turn(e.deltaY < 0 ? 1 : -1); }, { passive: false });
    k.addEventListener("pointerdown", e => { lastY = e.clientY; acc = 0; k.setPointerCapture(e.pointerId); });
    k.addEventListener("pointermove", e => {
      if (lastY === null) return;
      acc += lastY - e.clientY; lastY = e.clientY;
      while (Math.abs(acc) >= 8) { turn(acc > 0 ? 1 : -1); acc -= Math.sign(acc) * 8; }
    });
    k.addEventListener("pointerup", () => { lastY = null; });
    encs.appendChild(k);
  }
  const btns = $("btns");
  for (const name of BTN_ORDER) {
    const b = document.createElement("button"); b.className = "b"; b.textContent = name; b.dataset.id = BTN[name];
    const down = e => { e.preventDefault(); if (!M) return; startAudio(); M._web_btn(BTN[name], 1); b.classList.add("down"); };
    const up = () => { if (!M || !b.classList.contains("down")) return; M._web_btn(BTN[name], 0); b.classList.remove("down"); };
    b.addEventListener("pointerdown", down); b.addEventListener("pointerup", up); b.addEventListener("pointerleave", up);
    btns.appendChild(b);
  }
  $("master").oninput = e => M && M._web_master(+e.target.value);
}

function buildKeys() {
  const kb = $("kb"), whites = [];
  const black = s => [1, 3, 6, 8, 10].includes((5 + s) % 12);   // F3 = key 0 = pitch class 5
  for (let k = 0; k < NKEYS; k++) if (!black(k)) whites.push(k);
  const W = 100 / whites.length;
  for (let k = 0; k < NKEYS; k++) {
    const d = document.createElement("div"); d.dataset.k = k;
    if (black(k)) { const left = whites.indexOf(k - 1); d.className = "k"; d.style.left = `calc(${(left + 1) * W}% - ${W * 0.3}%)`; d.style.width = `${W * 0.6}%`; }
    else { d.className = "w"; d.style.left = `${whites.indexOf(k) * W}%`; d.style.width = `${W}%`; }
    d.addEventListener("pointerdown", e => { e.preventDefault(); d.setPointerCapture(e.pointerId); press(k, true); });
    d.addEventListener("pointerup", () => press(k, false));
    d.addEventListener("pointercancel", () => press(k, false));
    kb.appendChild(d);
  }
}
function press(k, down) {
  if (!M) return;
  startAudio();
  keysDown = down ? keysDown | (1 << k) : keysDown & ~(1 << k);
  M._web_keys(keysDown >>> 0);
  const el = document.querySelector(`.kb > div[data-k="${k}"]`); if (el) el.classList.toggle("down", down);
}

// ---- the computer keyboard
const SPECIAL = { z: ["btn", BTN["OCT−"]], x: ["btn", BTN["OCT+"]], " ": ["btn", BTN.REC] };
addEventListener("keydown", e => {
  if (e.repeat || e.metaKey || e.ctrlKey || !M) return;
  const key = e.key.toLowerCase(), i = KEYMAP.indexOf(key);
  if (i >= 0) { press(i, true); e.preventDefault(); return; }
  if (SPECIAL[key]) { startAudio(); M._web_btn(SPECIAL[key][1], 1); e.preventDefault(); return; }
  const turn = { arrowright: [0, 1], arrowleft: [0, -1], arrowup: [6, 1], arrowdown: [6, -1] }[key];
  if (turn) { M._web_enc(turn[0], turn[1]); e.preventDefault(); }
});
addEventListener("keyup", e => {
  if (!M) return;
  const key = e.key.toLowerCase(), i = KEYMAP.indexOf(key);
  if (i >= 0) press(i, false);
  if (SPECIAL[key]) M._web_btn(SPECIAL[key][1], 0);
});

// ---- Web MIDI in
async function setupMidi() {
  if (!navigator.requestMIDIAccess) { $("midi").disabled = true; return; }
  try {
    const acc = await navigator.requestMIDIAccess();
    const sel = $("midi"), fill = () => {
      const cur = sel.value; sel.length = 1;
      for (const inp of acc.inputs.values()) { const o = document.createElement("option"); o.value = inp.id; o.textContent = inp.name; sel.appendChild(o); }
      sel.value = cur;
    };
    fill(); acc.onstatechange = fill;
    sel.onchange = () => {
      for (const inp of acc.inputs.values()) inp.onmidimessage = null;
      const inp = acc.inputs.get(sel.value);
      if (inp) inp.onmidimessage = m => { const [st, d1 = 0, d2 = 0] = m.data; if (M && st < 0xF8) { startAudio(); M._web_midi(st, d1, d2); } };
    };
  } catch (e) { $("midi").disabled = true; }
}

buildPanel();
buildKeys();
setupMidi();

// ---- ?selftest: a note through the engine, its level and the screen's state in the title (for headless checks)
if (location.search.includes("selftest")) {
  const t0 = performance.now();
  const wait = () => {
    if (!M || performance.now() - t0 < 1500) return setTimeout(wait, 100);
    M._web_keys(1 << 7);                               // C4
    setTimeout(() => {
      const p = M._web_audio(16) >> 2, f = M.HEAPF32;
      let s = 0; for (let i = 0; i < 2 * 256 * 16; i++) s += f[p + i] * f[p + i];
      const rms = 10 * Math.log10(s / (2 * 256 * 16) + 1e-20);
      const fb = new Uint16Array(M.HEAPU8.buffer, M._web_fb(), 240 * 240);
      let lit = 0; for (const v of fb) if (v) lit++;
      document.title = `SELFTEST rms=${rms.toFixed(1)}dB screen=${lit}px`;
      M._web_keys(0);
    }, 300);
  };
  wait();
}
