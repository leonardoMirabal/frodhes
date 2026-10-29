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
const LIVE_KEYS = ["rev", "ui_engine", "s_sustain", "preset_name"];
const SHIFT_CC = 49;
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
};

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
    },
    tick() {
        const t = Date.now();
        if (t - lastLive >= LIVE_MS) { lastLive = t; live(); }
        OV.draw(ctx);
    },
    onMidiMessageInternal(data) {
        const d = data || [];
        if (((d[0] | 0) & 0xF0) === 0xB0 && (d[1] | 0) === SHIFT_CC) { shift = (d[2] | 0) > 0; return; }
        OV.onMidi(ctx, { source: 0, data: [d[0] | 0, d[1] | 0, d[2] | 0] });
    },
    onMidiMessageExternal() {},
    handleBack() { return !!OV.handleBack(ctx); },
};
