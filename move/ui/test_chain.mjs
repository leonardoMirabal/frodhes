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
// Schwung's trailing pages for a module-drawn grid (shadow_ui.js componentTrailingMenus / runComponentActionFromGrid)
const host = { menuReads: 0, actions: [] };
g.shadow_component_trailing_menus = () => {
    host.menuReads++;
    return [{ name: "My Presets", entries: [{ label: "Preset", value: "(none)", action: "up_load" },
                                            { label: "Save As", action: "up_save_as" }] },
            { name: "Module", entries: [{ label: "Module Help", action: "module_help" },
                                        { label: "Add to List", value: "", action: "module_lists" },
                                        { label: "Swap Module", action: "swap_module" },
                                        { label: "Remove Module", action: "remove_module" }] }];
};
g.shadow_component_run_action = (a) => { host.actions.push(a); return a !== "up_save_as"; };
// the arrows' LEDs (input_filter.mjs setButtonLED, imported on the device): every send, and what each CC shows
const btn = { sends: [], now: {} };
g.__SET_BUTTON_LED = (cc, c, force) => { btn.sends.push([cc, c, force]); btn.now[cc] = c; };
// the screen reader: Schwung's announce (shared/screen_reader.mjs, imported on the device) records what is said;
// tts_get_enabled is Global Settings > Screen Reader (off until the screen-reader section below)
const sr = { on: false, said: [], asks: 0 };
g.__ANNOUNCE = (s) => { sr.said.push(s); };
g.tts_get_enabled = () => { sr.asks++; return sr.on; };
vm.createContext(g);
let src = fs.readFileSync(path.join(ROOT, "move", "module", "ui_chain.js"), "utf8");
const IMP = /^import \* as KNOB_LEDS from '\/data\/UserData\/schwung\/shared\/param_pages\/knob_leds\.mjs';$/m;
check(IMP.test(src), "ui_chain.js imports Schwung's knob_leds.mjs");
src = src.replace(IMP, "const KNOB_LEDS = globalThis.__KNOB_LEDS;");
const IMP2 = /^import \{ setButtonLED as FR_SET_BUTTON_LED \} from '\/data\/UserData\/schwung\/shared\/input_filter\.mjs';$/m;
check(IMP2.test(src), "ui_chain.js imports Schwung's setButtonLED");
src = src.replace(IMP2, "const FR_SET_BUTTON_LED = globalThis.__SET_BUTTON_LED;");
const IMP3 = /^import \{ announce as FR_ANNOUNCE \} from '\/data\/UserData\/schwung\/shared\/screen_reader\.mjs';$/m;
check(IMP3.test(src), "ui_chain.js imports Schwung's screen_reader.mjs announce");
src = src.replace(IMP3, "const FR_ANNOUNCE = globalThis.__ANNOUNCE;");
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
const FR = (i) => pages[i];
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
// the first page: TYPE SPREAD SUS FX PRESET, knobs 6-7 dark (the holes under the PRESET card's name), DELAY on 8
const root = pages[0];
check(JSON.stringify(bound(leds.last)) === JSON.stringify([0, 1, 2, 3, 4, 5, 6, 7].map((i) => !!root.knobs[i])),
      "LEDs: the first page's knobs lit, the holes and the rest dark (" + JSON.stringify(leds.last) + ")");
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
check(p.writes.some(([k, v]) => k === "s_fx" && v === "0") && !p.writes.some(([k]) => k === "s_fxbtn"),
      "the Left arrow writes FX off (s_fx 0), not a flip");
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

// ---- the list's module rows, SAVE TO USER, Schwung's rows, the double-tap reset
ui.init(); ui.tick();
const st = () => ui._state();
const go = (label) => {                       // the jog to the row of that label (from the top)
    for (let i = 0; i < 40; i++) midi(0xB0, 14, 127);
    for (let i = 0; i < 40 && st().rows[st().cursor].label !== label; i++) midi(0xB0, 14, 1);
    return st().rows[st().cursor].label === label;
};
const mr0 = host.menuReads;
midi(0xB0, 3, 127);                            // a click: the list
const labels = st().rows.map((r) => (r.kind === "sep" ? "-" : "") + r.label);
check(st().list && host.menuReads === mr0 + 1, "menu: the list reads Schwung's trailing pages once on opening (" + (host.menuReads - mr0) + ")");
for (const want of ["-USER PRESETS", "SAVE TO USER", "-MY PRESETS", "PRESET", "SAVE AS", "-MODULE",
                    "MODULE HELP", "ADD TO LIST", "SWAP MODULE", "REMOVE MODULE"])
    check(labels.includes(want), "menu: the list has " + want + " (" + labels.join(", ") + ")");
check(labels.indexOf("SAVE TO USER") > labels.indexOf("REVERB"), "menu: the module rows follow the pages");
ui.tick(); ui.tick();
check(host.menuReads === mr0 + 1, "menu: no read per frame (" + (host.menuReads - mr0) + ")");
for (let i = 0; i < 40; i++) midi(0xB0, 14, 1);
check(st().rows[st().cursor].kind !== "sep", "menu: the cursor never rests on a divider");
for (let i = 0; i < 40; i++) {
    midi(0xB0, 14, 127);
    if (st().rows[st().cursor].kind === "sep") { check(false, "menu: the jog stopped on a divider"); break; }
}
// SAVE TO USER: the card, the jog picks the slot, a click saves (u_save), Back leaves unsaved
check(go("SAVE TO USER"), "menu: the jog reaches SAVE TO USER");
midi(0xB0, 3, 127);
check(st().save && !st().list && st().save.slot === 0, "save: the card opens on the first empty slot (U01)");
ui.tick();
midi(0xB0, 14, 1); midi(0xB0, 14, 1);
check(st().save.slot === 2, "save: the jog picks U03 (" + st().save.slot + ")");
let nw = p.writes.length;
check(ui.handleBack() === true && !st().save, "save: Back closes the card");
check(!p.writes.slice(nw).some(([k]) => k === "u_save"), "save: Back saves nothing");
midi(0xB0, 3, 127); check(go("SAVE TO USER"), "save: back to SAVE TO USER"); midi(0xB0, 3, 127);
midi(0xB0, 71, 1); midi(0xB0, 72, 1); midi(0xB0, 73, 1);     // any knob picks too: U04
nw = p.writes.length;
midi(0xB0, 3, 127);
check(p.writes.slice(nw).some(([k, v]) => k === "u_save" && v === "4"), "save: a click writes u_save 4 (" +
      JSON.stringify(p.writes.slice(nw)) + ")");
check(!st().save && st().toast && st().toast.text === "SAVED U04", "save: SAVED U04 shown");
check(p.preset === p.nf + 3, "save: U04 is the preset now");
ui.tick();
check(fb.lit() > 300, "save: the screen drawn with the toast (" + fb.lit() + " px)");
midi(0xB0, 3, 127); check(go("SAVE TO USER"), "save: again"); midi(0xB0, 3, 127);
check(st().save.slot === 3 && st().save.names[3] === "U04 SAVED", "save: the card opens on the loaded U04, named");
ui.handleBack();
// Schwung's rows: the action is Schwung's; one that opened a screen draws nothing over it, one done in place says so
t += 2000; ui.tick();
midi(0xB0, 3, 127); check(go("MODULE HELP"), "menu: the jog reaches MODULE HELP");
midi(0xB0, 3, 127);
check(host.actions[host.actions.length - 1] === "module_help" && !st().list && !st().toast,
      "menu: MODULE HELP runs Schwung's module_help (" + host.actions.join(",") + ")");
midi(0xB0, 3, 127); go("SWAP MODULE"); midi(0xB0, 3, 127);
check(host.actions[host.actions.length - 1] === "swap_module", "menu: SWAP MODULE runs Schwung's swap_module");
midi(0xB0, 3, 127); go("SAVE AS"); midi(0xB0, 3, 127);
check(host.actions[host.actions.length - 1] === "up_save_as" && st().toast, "menu: SAVE AS runs up_save_as (in place)");
// the host reloads the screen after Load / Help: restorePage lands on the list at that section
ui.init(); ui.restorePage("My Presets");
check(st().list && st().rows[st().cursor].label === "PRESET" && st().rows[st().cursor].value === "(NONE)", "menu: restorePage(My Presets) lands on its first row (" +
      (st().rows[st().cursor] || {}).label + ")");
const r0 = host.menuReads;
ui.onPresetsChanged();
check(host.menuReads === r0 + 1, "menu: onPresetsChanged reads the rows again");
ui.handleBack();
// the double tap: a knob back to its default; not with a turn between, not slower than tap_ms, never SUS / PRESET
ui.init(); midi(0xB0, 14, 1); t += 2000; ui.tick();          // PIANO VOICE
check(FR(st().page).title === "PIANO VOICE", "reset: on PIANO VOICE");
p.external("p_tone", 20); t += 2000; ui.tick(); for (let i = 0; i < 10; i++) { t += 120; ui.tick(); }
const tap = (k) => { midi(0x90, k, 127); t += 60; midi(0x90, k, 0); t += 100; };
nw = p.writes.length;
tap(0); tap(0);
check(p.writes.slice(nw).some(([k, v]) => k === "p_tone" && v === "0") && p.v.p_tone === 0,
      "reset: a double tap sets TONE to its default 0 (" + JSON.stringify(p.writes.slice(nw)) + ")");
p.external("p_tone", 20); t += 2000; ui.tick();
nw = p.writes.length;
midi(0x90, 0, 127); midi(0xB0, 71, 1); midi(0x90, 0, 0); t += 100; tap(0);
check(!p.writes.slice(nw).some(([k, v]) => k === "p_tone" && v === "0"), "reset: not with a turn between the touches");
nw = p.writes.length;
t += 1000; tap(0); t += 500; tap(0);
check(!p.writes.slice(nw).some(([k, v]) => k === "p_tone" && v === "0"), "reset: not when the touches are slower than tap_ms");
ui.handleBack();

// ---- the pads and the steps: never ours. The Move lights them itself (2026-10-30, the pad LED report: a root pad
// stays white after its release until another pad is played; the Move's own output does that, on its stock tracks
// too). The screen must never write a note LED, never take a pad's note-on or note-off (velocity 0 too) in any mode
// (a page, the list, the save card), and its button LEDs are the arrows only.
// Every pad (68-99) and step (16-31), as each of the Move's layouts and octaves sends them (the note numbers are the
// pads', not the pitches: the Move maps them by its own SCALE / ROOT / layout, which the screen never sees)
const PAD_NOTES = [...Array(16).keys()].map((i) => 16 + i).concat([...Array(32).keys()].map((i) => 68 + i));
const padCase = (what) => {
    const s0 = JSON.stringify({ page: st().page, list: st().list, save: !!st().save, cursor: st().cursor });
    const nb = btn.sends.length, nw2 = p.writes.length, l0 = JSON.stringify(leds.last);
    let claimed = false;
    for (const n of PAD_NOTES) {
        for (const d of [[0x90, n, 100], [0xA0, n, 40], [0x80, n, 0], [0x90, n, 90], [0x90, n, 0]])
            if (ui.onMidiMessageInternal(d) !== undefined) claimed = true;
        p.apply();
    }
    ui.tick();
    check(!claimed, "pads: " + what + ": no pad or step note claimed");
    check(btn.sends.length === nb, "pads: " + what + ": no LED sent for a pad (" + JSON.stringify(btn.sends.slice(nb)) + ")");
    check(JSON.stringify(leds.last) === l0, "pads: " + what + ": the knob LEDs unchanged");
    check(p.writes.length === nw2, "pads: " + what + ": no param written (" + JSON.stringify(p.writes.slice(nw2)) + ")");
    check(JSON.stringify({ page: st().page, list: st().list, save: !!st().save, cursor: st().cursor }) === s0,
          "pads: " + what + ": the screen's state unchanged");
};
ui.init(); t += 2000; ui.tick();
padCase("a page");
midi(0x90, 0, 127); padCase("a knob held (the large card)"); midi(0x90, 0, 0);
midi(0xB0, 63, 127); t += 2000; ui.tick(); padCase("SUS on");
midi(0xB0, 3, 127); padCase("the list");
check(go("SAVE TO USER"), "pads: the jog reaches SAVE TO USER"); midi(0xB0, 3, 127); padCase("the save card");
ui.handleBack(); ui.handleBack();
// the K31 audit (2026-11-09, the user's photo: scale mode, most roots white until a pad is played): every event that
// can come with a Move pad repaint, the Move's own controls included (octave Up 55 / Down 54, Shift 49 + a pad or a
// step: its SCALE / ROOT / layout menus, Note / Session 50), a page and a preset change, the engine, the module
// reloaded (init again) and the screen left and opened again (Back, then init: a wake, a track switched back). In
// each: no note LED, no pad / step / Move-control note or CC claimed, the knob and arrow LEDs only.
const moveCase = (what) => {
    const nb = btn.sends.length, nw2 = p.writes.length;
    const s0 = JSON.stringify({ page: st().page, list: st().list, save: !!st().save });
    for (const d of [[0xB0, 55, 127], [0xB0, 55, 0], [0xB0, 54, 127], [0xB0, 54, 0], [0xB0, 50, 127], [0xB0, 50, 0]])
        ui.onMidiMessageInternal(d);
    ui.onMidiMessageInternal([0xB0, 49, 127]);
    for (const n of PAD_NOTES) { ui.onMidiMessageInternal([0x90, n, 100]); ui.onMidiMessageInternal([0x80, n, 0]); }
    ui.onMidiMessageInternal([0xB0, 49, 0]);
    p.apply(); ui.tick();
    check(btn.sends.length === nb, "pads: " + what + ": the Move's controls send no LED (" +
          JSON.stringify(btn.sends.slice(nb)) + ")");
    check(p.writes.length === nw2, "pads: " + what + ": the Move's controls write no param (" +
          JSON.stringify(p.writes.slice(nw2)) + ")");
    check(JSON.stringify({ page: st().page, list: st().list, save: !!st().save }) === s0,
          "pads: " + what + ": the Move's controls leave the screen as it was");
    padCase(what);
};
ui.init(); t += 2000; ui.tick();
moveCase("open");
midi(0xB0, 14, 1); t += 2000; ui.tick(); moveCase("the next page (jog)");
midi(0xB0, 14, 127); t += 2000; ui.tick();
p.external("p_dly_mix", 40); t += 2000; ui.tick(); moveCase("a change from outside (rev)");
p.preset = 3; p.rev++; t += 2000; ui.tick(); moveCase("a preset change");
p.external("p_type", 4); t += 2000; ui.tick(); moveCase("the engine changed (TYPE VIBE)");
p.external("p_type", 0); t += 2000; ui.tick();
ui.init(); t += 2000; ui.tick(); moveCase("the module reloaded (init again)");
ui.handleBack(); ui.init(); t += 2000; ui.tick(); moveCase("Back, then opened again (a wake, a track switched back)");
ui.handleBack();
check(btn.sends.every(([cc]) => cc === 62 || cc === 63), "LEDs: the only button LEDs written are the arrows (" +
      [...new Set(btn.sends.map(([cc]) => cc))].join(",") + ")");
check(!/move_midi_internal_send|setLED\b/.test(src), "LEDs: the screen has no path to a note LED (no setLED, no raw send)");

// ---- the screen reader (Schwung's announce, asked only while tts_get_enabled): what each event says, in words; the
// turns throttled as Schwung's grid does (one value per 120 ms a knob, then the value it ended on); nothing per tick
check(sr.said.length === 0, "screen reader off: nothing said in all of the above (" + JSON.stringify(sr.said.slice(0, 3)) + ")");
sr.on = true;
const heard = (fn) => { const n = sr.said.length; fn(); return sr.said.slice(n); };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sentence = (s) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
const presetSaid = (i) => p.names[i].slice(0, 3) + ", " + sentence(p.names[i].slice(4));
const ticks = (n, ms) => { for (let i = 0; i < n; i++) { t += ms || 100; ui.tick(); p.apply(); } };
const engine0 = pages.filter((q) => q.gate < 0 || q.gate === 0).length;
p.v.p_type = 0; p.preset = 0;
let h = heard(() => { ui.init(); });
check(eq(h, ["Frodhes, " + presetSaid(0)]), "sr: the screen opening says Frodhes and the preset " + JSON.stringify(h));
ticks(12);
let asks = sr.asks;
h = heard(() => ticks(30));
check(h.length === 0 && sr.asks === asks, "sr: idle, 30 ticks say nothing and never ask the screen reader (" +
      JSON.stringify(h) + ", " + (sr.asks - asks) + " asks)");
h = heard(() => midi(0xB0, 14, 1));
check(eq(h, ["Piano voice, 2 of " + engine0 + ", 8 controls"]), "sr: the jog says the page " + JSON.stringify(h));
h = heard(() => midi(0xB0, 14, 127));
check(eq(h, ["Frodhes, 1 of " + engine0 + ", 6 controls"]), "sr: the jog back " + JSON.stringify(h));
h = heard(() => midi(0xB0, 14, 127));
check(h.length === 0, "sr: the jog at the first page says nothing (no page change) " + JSON.stringify(h));
midi(0xB0, 14, 1); ticks(12);
// a touch: the whole name and the value; a fast turn: one value, then the last; a slow one: every value
h = heard(() => midi(0x90, 4, 127));
check(eq(h, ["Decay, " + p.v.p_decay]), "sr: a touch says the name and the value " + JSON.stringify(h));
h = heard(() => { for (let i = 0; i < 6; i++) { t += 10; midi(0xB0, 75, 1); } });
check(h.length === 1 && /^\d+$/.test(h[0]) && h[0] !== String(st().vals.p_decay),
      "sr: six detents in 60 ms say one value (the first) " + JSON.stringify(h));
const ended = String(st().vals.p_decay);
h = heard(() => ticks(3));
check(eq(h, [ended]), "sr: the turn's last value said once it stops (" + ended + ") " + JSON.stringify(h));
h = heard(() => ticks(10));
check(h.length === 0, "sr: then nothing " + JSON.stringify(h));
h = heard(() => { for (let i = 0; i < 3; i++) { t += 150; midi(0xB0, 75, 127); } });
check(h.length === 3 && h[2] === String(st().vals.p_decay), "sr: a slow turn says each value " + JSON.stringify(h));
h = heard(() => { midi(0x90, 4, 0); ticks(3); });
check(h.length === 0, "sr: the release says nothing " + JSON.stringify(h));
// words where the screen shows words: an option, INFINITE's SUSTAIN / FREEZE, a ratio, OFF / ON
p.external("p_rv_type", 3); p.external("p_rv_decay", 127); p.external("p_cmp_sw", 0); p.external("p_cmp_ratio", 3);
ticks(12);
const toPage = (title) => { for (let i = 0; i < 20 && FR(st().page).title !== title; i++) midi(0xB0, 14, 1); ticks(12); };
toPage("COMPRESSOR");
h = heard(() => { midi(0x90, 1, 127); midi(0x90, 1, 0); t += 500; midi(0x90, 4, 127); midi(0x90, 4, 0); });
check(eq(h, ["Ratio, 4 to 1", "Compressor, Off"]), "sr: RATIO and COMP in words " + JSON.stringify(h));
// the EQ: its page after COMPRESSOR; the gains and MID FREQ said with their units, as the FM-1's card shows them
p.external("p_eq_lo", 77); p.external("p_eq_mid", 64); p.external("p_eq_freq", 64); p.external("p_eq_hi", 0);
ticks(12);
h = heard(() => { midi(0xB0, 14, 1); ticks(12); });
check(eq(h, ["EQ, 5 of " + engine0 + ", 4 controls"]), "sr: the EQ page after COMPRESSOR (VELOCITY before it, 0.9.10) " + JSON.stringify(h));
h = heard(() => { for (let k = 0; k < 4; k++) { t += 500; midi(0x90, k, 127); midi(0x90, k, 0); } });
check(eq(h, ["Low, plus 3.1 decibels", "Mid, 0 decibels", "Mid frequency, 1.0 kilohertz", "High, minus 15 decibels"]),
      "sr: the EQ's names and values in words " + JSON.stringify(h));
toPage("REVERB");
h = heard(() => { midi(0x90, 0, 127); midi(0x90, 0, 0); t += 500; midi(0x90, 2, 127); });
check(eq(h, ["Reverb type, Infinite", "Decay, Freeze"]), "sr: the reverb's type and INFINITE's FREEZE " + JSON.stringify(h));
h = heard(() => { t += 200; midi(0xB0, 73, 127); midi(0x90, 2, 0); });
check(eq(h, ["Sustain"]), "sr: DECAY turned down to SUSTAIN " + JSON.stringify(h));
check(!sr.said.some((s) => /\b(DCAY|RVB|SUST|FREZ|PDLY|LCUT)\b/.test(s)), "sr: never the cards' abbreviations");
// the arrows
h = heard(() => midi(0xB0, 63, 127));
check(eq(h, ["Sustain " + (st().susOn ? "on" : "off")]), "sr: the Right arrow says the sustain " + JSON.stringify(h));
h = heard(() => midi(0xB0, 62, 127));
check(eq(h, ["Effects " + (st().fxOn ? "on" : "off")]), "sr: the Left arrow says the effects " + JSON.stringify(h));
h = heard(() => { midi(0xB0, 62, 0); midi(0xB0, 63, 0); });
check(h.length === 0, "sr: a release of an arrow says nothing " + JSON.stringify(h));
ticks(12);
// the double tap: the second touch says the reset, not the value
p.external("p_rv_depth", 50); ticks(12);
h = heard(() => { tap(1); tap(1); });
check(eq(h, ["Mix, 50", "Mix reset"]), "sr: a double tap says <name> reset " + JSON.stringify(h));
ticks(12);
// the list: the row (and its section on entering it), the click, Back; the save card: the slot, saved / not saved
h = heard(() => midi(0xB0, 3, 127));
const selN = st().rows.filter((r) => r.kind !== "sep").length;
const rvAt = st().rows.filter((r) => r.kind !== "sep").findIndex((r) => r.label === "REVERB") + 1;
check(eq(h, ["Reverb, current, " + rvAt + " of " + selN]), "sr: the list opens on the page's row " + JSON.stringify(h));
h = heard(() => midi(0xB0, 14, 1));
check(eq(h, ["Tempo, " + (rvAt + 1) + " of " + selN]), "sr: the jog says the row " + JSON.stringify(h));
h = heard(() => midi(0xB0, 14, 127));
check(eq(h, ["Reverb, current, " + rvAt + " of " + selN]), "sr: and back " + JSON.stringify(h));
go("SAVE TO USER");
const saveAt = st().rows.filter((r) => r.kind !== "sep").findIndex((r) => r.label === "SAVE TO USER") + 1;
check(sr.said[sr.said.length - 1] === "User presets, Save to user, " + saveAt + " of " + selN,
      "sr: a row in a new section says the section first (" + sr.said[sr.said.length - 1] + ")");
h = heard(() => midi(0xB0, 14, 1));
check(eq(h, ["My presets, Preset: (none), " + (saveAt + 1) + " of " + selN]), "sr: Schwung's row, its value " + JSON.stringify(h));
midi(0xB0, 14, 127);
const used = (i) => p.used[i] ? sentence(p.names[p.nf + i].slice(4)) : "empty";
h = heard(() => midi(0xB0, 3, 127));
const s0 = st().save.slot;
check(eq(h, ["Save to user, User " + (s0 + 1) + ", " + used(s0)]), "sr: the save card says its slot " + JSON.stringify(h));
h = heard(() => midi(0xB0, 14, 1));
check(eq(h, ["User " + (s0 + 2) + ", " + used(s0 + 1)]), "sr: the jog says the next slot " + JSON.stringify(h));
h = heard(() => ui.handleBack());
check(eq(h, ["Not saved"]), "sr: Back on the save card: not saved " + JSON.stringify(h));
midi(0xB0, 3, 127); go("SAVE TO USER"); midi(0xB0, 3, 127); midi(0xB0, 14, 1);
const s1 = st().save.slot;
h = heard(() => midi(0xB0, 3, 127));
check(eq(h, ["Saved to user " + (s1 + 1)]), "sr: a click saves: saved " + JSON.stringify(h));
ticks(12);
midi(0xB0, 3, 127);
h = heard(() => ui.handleBack());
check(h.length === 1 && /^Reverb, \d+ of \d+, 8 controls$/.test(h[0]), "sr: Back closes the list: the page " + JSON.stringify(h));
midi(0xB0, 3, 127); go("TEMPO");
h = heard(() => midi(0xB0, 3, 127));
check(h.length === 1 && /^Tempo, \d+ of \d+, 3 controls$/.test(h[0]), "sr: a click on a page row says the page " + JSON.stringify(h));
midi(0xB0, 3, 127); go("SAVE AS");
h = heard(() => midi(0xB0, 3, 127));
check(eq(h, ["Save As"]), "sr: a row done in place says it (Schwung's own words) " + JSON.stringify(h));
midi(0xB0, 3, 127); go("MODULE HELP");
h = heard(() => midi(0xB0, 3, 127));
check(h.length === 0, "sr: a row that opens Schwung's screen leaves the words to it " + JSON.stringify(h));
// the preset: the PRESET knob says the new preset once its name is read; a preset loaded elsewhere is said too
ui.init(); ticks(12);
p.preset = 0; p.rev++; ticks(12);
h = heard(() => midi(0xB0, 75, 1));
check(h.length === 0, "sr: the PRESET knob waits for the new name " + JSON.stringify(h));
h = heard(() => ticks(3));
check(eq(h, [presetSaid(1)]), "sr: then says it " + JSON.stringify(h));
h = heard(() => { p.preset = 5; p.rev++; ticks(3); });
check(eq(h, [presetSaid(5)]), "sr: a preset loaded elsewhere says its name " + JSON.stringify(h));
asks = sr.asks;
h = heard(() => ticks(30));
check(h.length === 0 && sr.asks === asks, "sr: idle again, nothing said or asked " + JSON.stringify(h));
// off again: nothing is said for anything
sr.on = false;
h = heard(() => {
    ui.init(); ticks(3); midi(0xB0, 14, 1); midi(0x90, 0, 127); midi(0xB0, 71, 1); midi(0x90, 0, 0); tap(1); tap(1);
    midi(0xB0, 63, 127); midi(0xB0, 62, 127); midi(0xB0, 3, 127); midi(0xB0, 14, 1); ui.handleBack(); ticks(10);
    p.preset = 2; p.rev++; ticks(3); ui.handleBack();
});
check(h.length === 0, "sr: off, nothing said " + JSON.stringify(h));
delete g.tts_get_enabled;            // (an older host without the setting: announce decides, as Schwung's own screens)
h = heard(() => { ui.init(); });
check(h.length === 1, "sr: no tts_get_enabled: announce is called " + JSON.stringify(h));
g.tts_get_enabled = () => { sr.asks++; return sr.on; };
ui.handleBack();

if (process.env.SR_LOG) console.log(sr.said.join("\n"));
if (fails) { console.log(fails + " failed"); process.exit(1); }
console.log("test_chain: ok");
