// SPDX-License-Identifier: GPL-3.0-only
// Frodhes' own screen as the WHOLE Move UI (prototype, branch move-fullscreen): Schwung's own-screen path. The plugin
// answers no ui_hierarchy (it serves the same pages as ui_pages, for external surfaces), so Schwung's component editor
// (shadow_ui.js enterComponentEdit -> enterComponentEditFallback -> loadModuleUi) loads this file, ui_chain.js, and
// calls globalThis.chain_ui in VIEWS.COMPONENT_EDIT: init on open, tick on each redraw frame (~30 Hz), every MIDI
// event but Back in onMidiMessageInternal, Back through handleBack first (truthy = consumed).
// This adapter runs the canvas overlay of screen_core.js (above it in the same closure) unchanged: its ctx maps onto
// the host's globals (fill_rect, clear_screen) and the param shims loadModuleUi installs for this slot's synth
// (host_module_get_param / host_module_set_param, "synth:<key>" on the slot). onValues gets the four live keys the
// canvas cell's extra_keys named, every LIVE_MS, as Schwung's canvas did (fullscreen_live_ms). The screen reader: the
// screen's words go to Schwung's announce (ctx.announce, gated by ctx.srOn); screen_core.js says what and when.
const OV = globalThis.canvas_overlay;
if (PREV_OV === undefined) delete globalThis.canvas_overlay; else globalThis.canvas_overlay = PREV_OV;
if (PREV_FS === undefined) delete globalThis.frodhes_screen; else globalThis.frodhes_screen = PREV_FS;
const LIVE_MS = FR_CONF.live_ms || 100;
const LIVE_KEYS = ["rev", "ui_engine", "s_sustain", "preset_name", "s_fx"];   // (s_fx: the header's X, FX off)
const SHIFT_CC = 49;
// the arrows' own LEDs (white, CC-addressed: constants.mjs MoveWhiteLeds): Left lit while FX is on, Right while
// the sustain holds; WhiteLedBright / WhiteLedOff
const ARROW_ON = 0x7c, ARROW_OFF = 0;
const arrowLast = {};
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
    setParam(k, v) { return has("host_module_set_param") ? host_module_set_param(k, String(v)) : false; },
    clear() { clear_screen(); },
    fillRect(x, y, w, h, c) { if (w > 0 && h > 0) fill_rect(x | 0, y | 0, w | 0, h | 0, c ? 1 : 0); },
    now: () => Date.now(),
    shiftHeld,
    // the list's module rows: Schwung's own trailing pages for a module-drawn grid (API.md "Trailing pages": My
    // Presets, Module), absent on an older host; a row's action is Schwung's (true: one of its screens took over)
    menus() { return has("shadow_component_trailing_menus") ? shadow_component_trailing_menus() : []; },
    runAction(a) { return has("shadow_component_run_action") ? !!shadow_component_run_action(a) : false; },
    // SAVE TO USER: the sound into Frodhes' own user slot u (1-16, the plugin's u_save; store.bin, shared)
    userSave(u) { return ctx.setParam("u_save", String(u)); },
    // the screen reader: Schwung's shared/screen_reader.mjs announce (imported at the top of ui_chain.js as
    // FR_ANNOUNCE; it speaks only while the shadow UI is on screen), asked only while Schwung's screen reader is on
    // (tts_get_enabled, Global Settings > Screen Reader): off, the screen builds no text and calls nothing
    announce(s) { if (typeof FR_ANNOUNCE === "function") try { FR_ANNOUNCE(s); } catch (e) {} },
    srOn() {
        if (typeof FR_ANNOUNCE !== "function") return false;
        if (!has("tts_get_enabled")) return true;     // (an older host: announce decides, as its own screens do)
        try { return !!tts_get_enabled(); } catch (e) { return false; }
    },
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
// the arrows: the screen's own FX / SUS state (the live s_fx / s_sustain, and a press at once); an unread FX is dark.
// Sent with force and diffed here, as knob_leds.mjs does (the shared cache can outlive a hardware clear).
function arrowSet(cc, c) {
    if (arrowLast[cc] === c) return;
    arrowLast[cc] = c;
    FR_SET_BUTTON_LED(cc, c, true);
}
function arrowsOn() {
    const st = state.fr;
    if (typeof FR_SET_BUTTON_LED !== "function" || !st) return;
    try {
        arrowSet(FR_CONF.fx_cc, st.fxOn === true ? ARROW_ON : ARROW_OFF);
        arrowSet(FR_CONF.sus_cc, st.susOn ? ARROW_ON : ARROW_OFF);
    } catch (e) {}
}
function arrowsOff() {
    if (typeof FR_SET_BUTTON_LED !== "function") return;
    try {
        FR_SET_BUTTON_LED(FR_CONF.fx_cc, ARROW_OFF, true);
        FR_SET_BUTTON_LED(FR_CONF.sus_cc, ARROW_OFF, true);
    } catch (e) {}
    for (const k of Object.keys(arrowLast)) delete arrowLast[k];
}

// leaving: give the rings back as Schwung's grid does (shadow_ui_param_pages.mjs exitParamPages): the shim replays
// Move's own last ring values (shadow_restore_knob_leds); an older shim without it gets them dark
function ledsOff() {
    arrowsOff();                              // (dark first: the shim's replay below repaints what Move had lit)
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
        for (const k of Object.keys(state)) delete state[k];
        OV.onOpen(ctx, {});
        lastLive = Date.now();
        if (typeof KNOB_LEDS !== "undefined" && KNOB_LEDS && KNOB_LEDS.resetKnobLedCache) KNOB_LEDS.resetKnobLedCache();
        for (const k of Object.keys(arrowLast)) delete arrowLast[k];
        ledsOn();
        arrowsOn();
    },
    tick() {
        const t = Date.now();
        if (t - lastLive >= LIVE_MS) { lastLive = t; live(); }
        OV.draw(ctx);
        ledsOn();
        arrowsOn();
    },
    onMidiMessageInternal(data) {
        const d = data || [];
        if (((d[0] | 0) & 0xF0) === 0xB0 && (d[1] | 0) === SHIFT_CC) { shift = (d[2] | 0) > 0; return; }
        OV.onMidi(ctx, { source: 0, data: [d[0] | 0, d[1] | 0, d[2] | 0] });
    },
    onMidiMessageExternal() {},
    // Save / Save As (Schwung's keyboard over our screen) changed what My Presets offers
    onPresetsChanged() { OV.presetsChanged(ctx); },
    // the host reloaded us after Load / Delete / Swap / Help: land on the list at that section
    restorePage(name) { OV.restore(ctx, name); },
    _state() { return state.fr; },                // (the host tests: the screen's state)
    handleBack() {
        if (OV.handleBack(ctx)) return true;      // up a level (the page list closes): still ours
        ledsOff();                                // the host unloads the screen: the knobs go dark
        return false;
    },
};
