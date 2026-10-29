// SPDX-License-Identifier: GPL-3.0-only
// move/module/ui_chain.js on the host: Frodhes' own screen as Schwung's chain_ui (the own-screen path). The globals
// a module UI sees in COMPONENT_EDIT (fill_rect, clear_screen, the slot's host_module_get_param / _set_param shims)
// on the harness's plugin model; checks the hooks, the globals left alone, the knobs, the arrows, Back and the knob
// LEDs (Schwung's knob_leds.mjs, imported on the device; a recording stand-in here).
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { ROOT, Plugin, Frame } from "./harness.mjs";

let fails = 0;
function check(ok, what) { if (!ok) { fails++; console.log("FAIL " + what); } }

const p = new Plugin(), fb = new Frame();
let t = 1000000;
const g = { Math, JSON, String, Number, Object, Array, isFinite };
g.Date = { now: () => t };
g.globalThis = g;
g.fill_rect = (x, y, w, h, c) => { fb.calls++; fb.fill(x, y, w, h, c); };
g.clear_screen = () => fb.fill(0, 0, 128, 64, 0);
g.host_module_get_param = (k) => p.get(k);
g.host_module_set_param = (k, v) => { p.set(k, v); return true; };
g.canvas_overlay = "host's";
// the knob LEDs: what the screen hands knob_leds.mjs (updateKnobLEDs(values) each tick), and the way out
const leds = { last: null, updates: 0, resets: 0, restores: 0, clears: 0 };
g.__KNOB_LEDS = {
    updateKnobLEDs(v) { leds.last = v.slice(); leds.updates++; },
    clearKnobLEDs() { leds.clears++; leds.last = new Array(8).fill(null); },
    resetKnobLedCache() { leds.resets++; },
};
g.shadow_restore_knob_leds = () => { leds.restores++; };
// the arrows' LEDs (input_filter.mjs setButtonLED, imported on the device): every send, and what each CC shows
const btn = { sends: [], now: {} };
g.__SET_BUTTON_LED = (cc, c, force) => { btn.sends.push([cc, c, force]); btn.now[cc] = c; };
vm.createContext(g);
let src = fs.readFileSync(path.join(ROOT, "move", "module", "ui_chain.js"), "utf8");
const IMP = /^import \* as KNOB_LEDS from '\/data\/UserData\/schwung\/shared\/param_pages\/knob_leds\.mjs';$/m;
check(IMP.test(src), "ui_chain.js imports Schwung's knob_leds.mjs");
src = src.replace(IMP, "const KNOB_LEDS = globalThis.__KNOB_LEDS;");
const IMP2 = /^import \{ setButtonLED as FR_SET_BUTTON_LED \} from '\/data\/UserData\/schwung\/shared\/input_filter\.mjs';$/m;
check(IMP2.test(src), "ui_chain.js imports Schwung's setButtonLED");
src = src.replace(IMP2, "const FR_SET_BUTTON_LED = globalThis.__SET_BUTTON_LED;");
vm.runInContext(src, g);
const ui = g.chain_ui;
check(ui && ["init", "tick", "onMidiMessageInternal", "onMidiMessageExternal", "handleBack"]
    .every((f) => typeof ui[f] === "function"), "chain_ui hooks");
check(g.canvas_overlay === "host's" && !("frodhes_screen" in g), "the host's globals left as they were");
const midi = (...d) => { ui.onMidiMessageInternal(d); p.apply(); };
const pages = (() => {     // FR_PAGES as the generator wrote them (the same JSON as in screen.js)
    const m = src.match(/const FR_PAGES = (\[.*?\]);\n/);
    return JSON.parse(m[1]);
})();
const bound = (v) => v.map((x) => x !== null);
const near = (a, b) => Math.abs(a - b) < 1e-9;
// what each knob of a page should show: the plugin model's value, an enum by index / (n - 1), a number over its range
const expect = (pg) => [0, 1, 2, 3, 4, 5, 6, 7].map((i) => {
    const k = pg.knobs[i];
    if (!k) return null;
    const v = Number(p.get(k.key));
    return k.type === "enum" ? v / (k.options.length - 1) : (v - k.min) / (k.max - k.min);
});
const same = (a, b) => a.length === b.length && a.every((x, i) => x === null ? b[i] === null : near(x, b[i]));

ui.init();
ui.tick();
check(fb.lit() > 200, "the first page drawn (" + fb.lit() + " px)");
check(leds.resets === 1 && leds.updates >= 1, "LEDs: the cache reset on open, then lit");
// the bars: one height along their whole length (outline top and bottom lit in every column, 5 rows, y+19..y+23),
// nothing below the last row of the screen cut off (the second row's bars end at 60)
const at = (x, y) => fb.px[y * 128 + x];
const even = (x0, x1, y0) => { for (let x = x0; x <= x1; x++) if (!at(x, y0) || !at(x, y0 + 4)) return false; return true; };
check([0, 1, 2, 3].every((i) => even(i * 32, i * 32 + 28, 28)), "bars: the first row's bars are even, end to end");
check([0, 1, 2, 3].every((i) => !at(i * 32 + 14, 27) && !at(i * 32 + 14, 33)), "bars: 5 px tall, a clear row each side");
// the first page: TYPE SPREAD SUS FX PRESET, knobs 6-8 dark
const root = pages[0];
check(JSON.stringify(bound(leds.last)) === JSON.stringify([0, 1, 2, 3, 4, 5, 6, 7].map((i) => i < root.knobs.length)),
      "LEDs: the first page's " + root.knobs.length + " knobs lit, the rest dark (" + JSON.stringify(leds.last) + ")");
check(near(leds.last[0], 0) && near(leds.last[3], 1), "LEDs: TYPE ST77 = 0 (enum index / (n-1)), FX ON = 1");
check(near(leds.last[4], 0), "LEDs: PRESET F01 = 0");
const spread = root.knobs[1];
check(near(leds.last[1], (0 - spread.min) / (spread.max - spread.min)), "LEDs: SPREAD by its range");

midi(0x90, 0, 127);            // touch knob 1, turn it: TYPE steps (ST77 -> SUIT)
midi(0xB0, 71, 1);
check(p.writes.some(([k]) => k === "p_type"), "knob 1 writes TYPE");
ui.tick();
check(near(leds.last[0], 1 / (root.knobs[0].options.length - 1)), "LEDs: a knob turn moves its LED (TYPE 1 of " +
      (root.knobs[0].options.length - 1) + ": " + leds.last[0] + ")");
midi(0x90, 0, 0);
// the arrows' LEDs: FX on (s_fx 1) lights Left, SUS off leaves Right dark; sent with force, once
check(btn.now[62] === 0x7c && btn.now[63] === 0, "arrow LEDs: Left lit (FX on), Right dark (SUS off) " +
      JSON.stringify(btn.now));
check(btn.sends.every(([, , f]) => f === true), "arrow LEDs: sent with force (the shared cache bypassed)");
let n = btn.sends.length;
ui.tick(); ui.tick();
check(btn.sends.length === n, "arrow LEDs: nothing sent while nothing changes (" + (btn.sends.length - n) + ")");
midi(0xB0, 63, 127);           // the Right arrow: SUS
check(p.writes.some(([k, v]) => k === "s_susbtn" && v === "1"), "the Right arrow presses s_susbtn");
ui.tick();
check(btn.now[63] === 0x7c, "arrow LEDs: SUS on lights Right at once");
midi(0xB0, 62, 127);           // the Left arrow: FX
check(p.writes.some(([k, v]) => k === "s_fxbtn" && v === "1"), "the Left arrow presses s_fxbtn");
ui.tick();
check(btn.now[62] === 0, "arrow LEDs: FX off darkens Left at once");
// the live keys win once settled: the plugin says FX on, SUS off
p.external("s_fx", 1); p.external("s_sustain", 0);
t += 2000; ui.tick();
check(btn.now[62] === 0x7c && btn.now[63] === 0, "arrow LEDs: follow the live s_fx / s_sustain " +
      JSON.stringify(btn.now));
const before = fb.px.slice();
midi(0xB0, 14, 1);             // the jog: the next page (PIANO VOICE, 8 knobs)
t += 2000;
ui.tick();
check(fb.px.some((v, i) => v !== before[i]), "the jog turns the page");
check(bound(leds.last).every((x) => x), "LEDs: a page change follows it (PIANO VOICE: all 8 lit)");
const piano = leds.last.slice();
const tone = pages[1].knobs[0];
check(same(piano, expect(pages[1])), "LEDs: every PIANO VOICE knob by its value (" + tone.name + " " + piano[0] + ")");
// TYPE from elsewhere (a preset, MIDI): VIBE; the VOICE page follows, and so do the LEDs
p.external("p_type", 4);
t += 100; ui.tick();
const vibes = pages.findIndex((q) => q.title === "VIBES VOICE");
check(vibes > 0, "a VIBES VOICE page");
check(same(leds.last, expect(pages[vibes])), "LEDs: a TYPE change moves them to the VIBES VOICE page (" +
      JSON.stringify(leds.last) + " vs " + JSON.stringify(expect(pages[vibes])) + ")");
p.external("p_type", 0);
t += 100; ui.tick();

midi(0xB0, 3, 127);            // a click: the list; Back closes it, then Back is the host's
check(ui.handleBack() === true, "Back closes the page list");
const rs = leds.restores;
check(ui.handleBack() === false, "Back on a page leaves (the host's)");
check(leds.restores === rs + 1 && leds.resets >= 2, "LEDs: Back out gives the rings back to Move (restore + reset)");
check(btn.now[62] === 0 && btn.now[63] === 0, "arrow LEDs: Back out darkens both arrows");
n = btn.sends.length;
ui.init();
check(btn.now[62] === 0x7c && btn.sends.length > n, "arrow LEDs: reopening re-sends them (the cache reset)");
ui.handleBack();
delete g.shadow_restore_knob_leds;
ui.init(); ui.tick();
check(ui.handleBack() === false && leds.clears === 1, "LEDs: without the shim's restore, Back out darkens them");
const reads = p.reads;
t += 100; ui.tick();
check(p.reads > reads, "the live keys read once LIVE_MS has passed");

if (fails) { console.log(fails + " failed"); process.exit(1); }
console.log("test_chain: ok");
