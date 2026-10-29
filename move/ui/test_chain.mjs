// SPDX-License-Identifier: GPL-3.0-only
// move/module/ui_chain.js on the host: Frodhes' own screen as Schwung's chain_ui (the own-screen path). The globals
// a module UI sees in COMPONENT_EDIT (fill_rect, clear_screen, the slot's host_module_get_param / _set_param shims)
// on the harness's plugin model; checks the hooks, the globals left alone, the knobs, the arrows and Back.
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
vm.createContext(g);
vm.runInContext(fs.readFileSync(path.join(ROOT, "move", "module", "ui_chain.js"), "utf8"), g);
const ui = g.chain_ui;
check(ui && ["init", "tick", "onMidiMessageInternal", "onMidiMessageExternal", "handleBack"]
    .every((f) => typeof ui[f] === "function"), "chain_ui hooks");
check(g.canvas_overlay === "host's" && !("frodhes_screen" in g), "the host's globals left as they were");
const midi = (...d) => { ui.onMidiMessageInternal(d); p.apply(); };

ui.init();
ui.tick();
check(fb.lit() > 200, "the first page drawn (" + fb.lit() + " px)");
midi(0x90, 0, 127);            // touch knob 1, turn it: TYPE steps
midi(0xB0, 71, 1);
check(p.writes.some(([k]) => k === "p_type"), "knob 1 writes TYPE");
midi(0x90, 0, 0);
midi(0xB0, 63, 127);           // the Right arrow: SUS
check(p.writes.some(([k, v]) => k === "s_susbtn" && v === "1"), "the Right arrow presses s_susbtn");
midi(0xB0, 62, 127);           // the Left arrow: FX
check(p.writes.some(([k, v]) => k === "s_fxbtn" && v === "1"), "the Left arrow presses s_fxbtn");
const before = fb.px.slice();
midi(0xB0, 14, 1);             // the jog: the next page
t += 2000;
ui.tick();
check(fb.px.some((v, i) => v !== before[i]), "the jog turns the page");
midi(0xB0, 3, 127);            // a click: the list; Back closes it, then Back is the host's
check(ui.handleBack() === true, "Back closes the page list");
check(ui.handleBack() === false, "Back on a page leaves (the host's)");
const reads = p.reads;
t += 100; ui.tick();
check(p.reads > reads, "the live keys read once LIVE_MS has passed");

if (fails) { console.log(fails + " failed"); process.exit(1); }
console.log("test_chain: ok");
