// SPDX-License-Identifier: GPL-3.0-only
// The host side of Frodhes' own screen (move/module/screen.js): the script run as the Move's shadow UI runs it (a
// canvas overlay's hooks with a ctx), on a 128 x 64 one-bit framebuffer, against a model of the plugin's keys (the
// values, rev, ui_engine, the presets). Used by tools/move_ui_shots.mjs (the PNGs) and move/ui/test_screen.mjs.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const W = 128, H = 64;
const MOD = path.join(ROOT, "move", "module");
const TYPE_UI = [0, 0, 0, 0, 1, 2];                 // eng_pages.h TYPE_UI: ST77 SUIT FELT DYNO -> 0, VIBE 1, MRMB 2

export function loadScreen(file = path.join(MOD, "screen.js")) {
    const g = {};
    g.globalThis = g;
    g.Date = Date;
    g.Math = Math;
    vm.createContext(g);
    vm.runInContext(fs.readFileSync(file, "utf8"), g, { filename: file });
    return { overlay: g.canvas_overlay, api: g.frodhes_screen, globals: g };
}

// the plugin's keys as the bridge serves them: wire text, enums by index; a write is applied later (queued) and
// counts one rev, as move_bridge.h does
export class Plugin {
    constructor() {
        const chain = JSON.parse(fs.readFileSync(path.join(MOD, "chain_params.json"), "utf8"));
        this.meta = {};
        this.v = {};
        for (const c of chain) {
            this.meta[c.key] = c;
            if (c.default !== undefined) this.v[c.key] = c.default;
            else if (c.type === "enum") this.v[c.key] = 0;
            else this.v[c.key] = c.min > 0 ? c.min : Math.max(0, c.min);
        }
        this.v.s_bpm = 120.0;
        this.rev = 0;
        this.preset = 0;
        // the factory names (presets.c PR_FNAME), the user slots empty, as preset_name() spells them
        const src = fs.readFileSync(path.join(ROOT, "firmware", "src", "presets.c"), "utf8");
        const fn = [...src.match(/PR_FNAME\[PR_NF\] = \{([^}]*)\}/)[1].matchAll(/"([^"]*)"/g)].map((m) => m[1]);
        const nm = (c, i, n) => (c + String(i + 1).padStart(2, "0") + " " + n).slice(0, 16).replace(/ +$/, "");
        this.names = fn.map((n, i) => nm("F", i, n));
        for (let i = 0; i < 16; i++) this.names.push(nm("U", i, "----"));
        this.queue = [];
        this.reads = 0;
        this.writes = [];
        this.sus = 0;
    }
    get(key) {
        this.reads++;
        if (key === "rev") return String(this.rev);
        if (key === "ui_engine") return String(TYPE_UI[this.v.p_type | 0] | 0);
        if (key === "preset") return String(this.preset);
        if (key === "preset_count") return String(this.names.length);
        if (key === "preset_name") return this.names[this.preset];
        if (key === "s_sustain") return String(this.v.s_sustain || this.sus ? 1 : 0);
        if (!(key in this.v)) return null;
        const m = this.meta[key];
        if (m && m.type === "float") return Number(this.v[key]).toFixed(1);
        if (m && m.access === "write") return "0";
        return String(this.v[key]);
    }
    set(key, val) { this.writes.push([key, val]); this.queue.push([key, val]); }
    apply() {                                        // the bridge's step: the queued writes land
        for (const [key, val] of this.queue) {
            if (key === "preset") { this.preset = Number(val); this.rev++; continue; }
            if (key === "s_susbtn") { this.sus = Number(val); this.rev++; continue; }
            if (!(key in this.v)) continue;
            const m = this.meta[key];
            if (m && m.access === "write") { this.rev++; continue; }
            this.v[key] = m && m.type === "float" ? Number(val) : Number(val) | 0;
            this.rev++;
        }
        this.queue = [];
    }
    external(key, value) { this.v[key] = value; this.rev++; }  // a change the screen did not make (a preset, the grid)
    midiCC(key, value) { this.v[key] = value; }                 // a change by MIDI CC (no rev)
}

export class Frame {
    constructor() { this.px = new Uint8Array(W * H); this.calls = 0; this.prints = 0; }
    fill(x, y, w, h, c) {
        x = Math.round(x); y = Math.round(y); w = Math.round(w); h = Math.round(h);
        for (let yy = Math.max(0, y); yy < Math.min(H, y + h); yy++)
            for (let xx = Math.max(0, x); xx < Math.min(W, x + w); xx++) this.px[yy * W + xx] = c ? 1 : 0;
    }
    lit() { let n = 0; for (const p of this.px) n += p; return n; }
}

// one screen: the overlay, its state, the clock, the plugin
export class Screen {
    constructor(opts = {}) {
        const s = loadScreen(opts.file);
        this.ov = s.overlay;
        this.api = s.api;
        this.plugin = opts.plugin || new Plugin();
        this.t = 1000000;
        this.shift = false;
        this.state = {};
        this.fb = new Frame();
    }
    ctx(draw) {
        const fb = this.fb, self = this;
        const c = {
            width: W, height: H, state: this.state,
            clear() { fb.fill(0, 0, W, H, 0); },
            fillRect(x, y, w, h, v) { fb.calls++; fb.fill(x, y, w, h, v); },
            setPixel(x, y, v) { fb.fill(x, y, 1, 1, v); },
            print() { fb.prints++; },
            now: () => self.t,
            shiftHeld: () => self.shift,
            measureText: (t) => String(t).length * 6,
        };
        if (!draw) {
            c.getParam = (k) => self.plugin.get(k);
            c.setParam = (k, v) => { self.plugin.set(k, String(v)); return true; };
        }
        return c;
    }
    open() { this.ov.onOpen(this.ctx(false), {}); return this; }
    midi(...data) { const r = this.ov.onMidi(this.ctx(false), { source: 0, data }); this.plugin.apply(); return r; }
    touch(k, down = true) { return this.midi(0x90, k, down ? 127 : 0); }
    knob(k, d) { return this.midi(0xB0, 71 + k, d >= 0 ? d : 128 + d); }
    jog(d) { return this.midi(0xB0, 14, d >= 0 ? d : 128 + d); }
    click() { return this.midi(0xB0, 3, 127); }
    back() { return this.ov.handleBack(this.ctx(false)); }
    live() {                                          // one fullscreen_live_ms cycle: the four extra keys
        const values = {};
        for (const k of ["rev", "ui_engine", "s_sustain", "preset_name"]) values[k] = this.plugin.get(k);
        this.ov.onValues(this.ctx(false), { values, nowMs: this.t });
        this.plugin.apply();
    }
    wait(ms) { this.t += ms; }
    draw() { this.ov.draw(this.ctx(true)); return this.fb; }
    st() { return this.state.fr; }
}

// ---- PNG (8-bit grey: the framebuffer's two colours, white on black, as the Move's screen shows them)
const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
})();
function crc32(buf) { let c = 0xFFFFFFFF; for (const b of buf) c = CRC[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
function chunk(type, data) {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
    return Buffer.concat([len, td, crc]);
}
export function png(grey, w, h) {
    const raw = Buffer.alloc((w + 1) * h);
    for (let y = 0; y < h; y++) { raw[y * (w + 1)] = 0; for (let x = 0; x < w; x++) raw[y * (w + 1) + 1 + x] = grey[y * w + x]; }
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 0; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr),
                          chunk("IDAT", zlib.deflateSync(raw, { level: 9 })), chunk("IEND", Buffer.alloc(0))]);
}
// a framebuffer -> grey pixels, k times larger (nearest neighbour: the pixels stay square)
export function grey(fb, k = 1) {
    const out = new Uint8Array(W * k * H * k);
    for (let y = 0; y < H * k; y++)
        for (let x = 0; x < W * k; x++) out[y * W * k + x] = fb.px[Math.floor(y / k) * W + Math.floor(x / k)] ? 255 : 0;
    return out;
}
