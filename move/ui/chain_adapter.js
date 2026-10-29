// SPDX-License-Identifier: GPL-3.0-only
// Frodhes' own screen as the WHOLE Move UI (prototype, branch move-fullscreen): Schwung's own-screen path. The plugin
// answers no ui_hierarchy (it serves the same pages as ui_pages, for external surfaces), so Schwung's component editor
// (shadow_ui.js enterComponentEdit -> enterComponentEditFallback -> loadModuleUi) loads this file, ui_chain.js, and
// calls globalThis.chain_ui in VIEWS.COMPONENT_EDIT: init on open, tick on each redraw frame (~30 Hz), every MIDI
// event but Back in onMidiMessageInternal, Back through handleBack first (truthy = consumed).
// This adapter runs the canvas overlay of screen_core.js (above it in the same closure) unchanged: its ctx maps onto
// the host's globals (fill_rect, clear_screen) and the param shims loadModuleUi installs for this slot's synth
// (host_module_get_param / host_module_set_param, "synth:<key>" on the slot). onValues gets the four live keys the
// canvas cell's extra_keys named, every LIVE_MS, as Schwung's canvas did (fullscreen_live_ms).
const OV = globalThis.canvas_overlay;
if (PREV_OV === undefined) delete globalThis.canvas_overlay; else globalThis.canvas_overlay = PREV_OV;
if (PREV_FS === undefined) delete globalThis.frodhes_screen; else globalThis.frodhes_screen = PREV_FS;
const LIVE_MS = FR_CONF.live_ms || 100;
const LIVE_KEYS = ["rev", "ui_engine", "s_sustain", "preset_name", "s_fx"];   // (s_fx: the header's X, FX off)
const SHIFT_CC = 49;
// a diagnostic (the device's debug.log): every button event the screen gets and each s_* write, while this file exists
const LOG_FLAG = "/data/UserData/frodhes-bench/ui_log_on";
let LOG = false;
try { LOG = typeof host_file_exists === "function" && !!host_file_exists(LOG_FLAG); } catch (e) {}
let shift = false, lastLive = 0;
const state = {};

function has(fn) { return typeof globalThis[fn] === "function"; }
function shiftHeld() { return has("shadow_get_shift_held") ? !!shadow_get_shift_held() : shift; }

const ctx = {
    width: W, height: H, state,
    getParam(k) {
        if (!has("host_module_get_param")) return null;
        const v = host_module_get_param(k);
        return v === undefined ? null : v;
    },
    setParam(k, v) {
        const r = has("host_module_set_param") ? host_module_set_param(k, String(v)) : false;
        if (LOG && k.startsWith("s_")) console.log("frodhes ui: set " + k + "=" + v + " -> " + r);
        return r;
    },
    clear() { clear_screen(); },
    fillRect(x, y, w, h, c) { if (w > 0 && h > 0) fill_rect(x | 0, y | 0, w | 0, h | 0, c ? 1 : 0); },
    now: () => Date.now(),
    shiftHeld,
};

// The knob LEDs, as Schwung's grid lights them (shared/param_pages/knob_leds.mjs, imported at the top of ui_chain.js
// as KNOB_LEDS): knobs 1-4 white, 5-8 amber, brighter with the value; dark = no control on that knob (or its value
// not read yet). The value is the card's bar (fraction: an enum by index / (n - 1), a number over its range, bipolar
// included; the preset by its number). Restated each tick; the module keeps its own diff cache, so a tick with
// nothing changed sends nothing.
function knobLeds() {
    const st = state.fr;
    const out = [];
    for (let i = 0; i < 8; i++) {
        const k = st ? knobs(st)[i] : undefined;
        out.push(k ? fraction(st, k) : null);
    }
    return out;
}
function ledsOn() {
    if (typeof KNOB_LEDS !== "undefined" && KNOB_LEDS) try { KNOB_LEDS.updateKnobLEDs(knobLeds()); } catch (e) {}
}
// leaving: give the rings back as Schwung's grid does (shadow_ui_param_pages.mjs exitParamPages): the shim replays
// Move's own last ring values (shadow_restore_knob_leds); an older shim without it gets them dark
function ledsOff() {
    if (typeof KNOB_LEDS === "undefined" || !KNOB_LEDS) return;
    try {
        if (has("shadow_restore_knob_leds")) shadow_restore_knob_leds();
        else KNOB_LEDS.clearKnobLEDs();
        KNOB_LEDS.resetKnobLedCache();
    } catch (e) {}
}

function live() {
    const values = {};
    for (const k of LIVE_KEYS) values[k] = ctx.getParam(k);
    OV.onValues(ctx, { values, nowMs: Date.now() });
}

globalThis.chain_ui = {
    init() {
        if (LOG) console.log("frodhes ui: init");
        for (const k of Object.keys(state)) delete state[k];
        OV.onOpen(ctx, {});
        lastLive = Date.now();
        if (typeof KNOB_LEDS !== "undefined" && KNOB_LEDS && KNOB_LEDS.resetKnobLedCache) KNOB_LEDS.resetKnobLedCache();
        ledsOn();
    },
    tick() {
        const t = Date.now();
        if (t - lastLive >= LIVE_MS) { lastLive = t; live(); }
        OV.draw(ctx);
        ledsOn();
    },
    onMidiMessageInternal(data) {
        const d = data || [];
        if (LOG && !(((d[0] | 0) & 0xF0) === 0xB0 && (((d[1] | 0) >= 71 && (d[1] | 0) <= 78) || (d[1] | 0) === 14)))
            console.log("frodhes ui: in " + [d[0] | 0, d[1] | 0, d[2] | 0].join(" "));
        if (((d[0] | 0) & 0xF0) === 0xB0 && (d[1] | 0) === SHIFT_CC) { shift = (d[2] | 0) > 0; return; }
        OV.onMidi(ctx, { source: 0, data: [d[0] | 0, d[1] | 0, d[2] | 0] });
    },
    onMidiMessageExternal() {},
    handleBack() {
        if (OV.handleBack(ctx)) return true;      // up a level (the page list closes): still ours
        ledsOff();                                // the host unloads the screen: the knobs go dark
        return false;
    },
};
