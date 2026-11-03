#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// tools/move_ui_shots.mjs [--out DIR] [--scale K] [--docs | --check]: Frodhes' screen on the Move, drawn on the host
// by the file the Move runs, move/module/ui_chain.js (Schwung's chain_ui: the whole Move UI for Frodhes), on a
// 128 x 64 one-bit framebuffer in its true colours (white on black). The values are the parameters' defaults (the
// FM-1's) unless a shot says otherwise; the engine pages once per engine (ST77 on F01, VIBE on F13, MRMB on F15).
//   DIR/<page>.png            every page's overview (128 x 64)
//   DIR/<page>-k<N>.png       the large card of each knob that has a control
//   DIR/<shot>.png            the docs' states (DOC_SHOTS below: FX off, FREEZE, a toast, the list, the menu ...)
//   DIR/*-<K>x.png            the same K times larger (default 4; nearest neighbour, the pixels stay square)
//   DIR/sheet.png             every page in one picture: a row per page, the overview then the cards (2x)
// Default DIR: build/move/ui-shots.
// --docs  writes the pictures the docs show (DOC_SHOTS, 4x, 512 x 256) to docs/manual/img/move/move-<name>.png,
//         removes any other move-*.png there, and sets the ?v= of every docs/manual/img/move/ link in
//         docs/MOVE.md, docs/MOVE-DEV.md, README.md and docs/MANUAL.md to the picture's hash (tools/doc_links.py's rule)
// --check changes nothing: exit 1 when a docs picture differs from what this tool draws (stale), one is missing or
//         left over (orphan), a doc links a move picture that does not exist, or a link's ?v= is not its hash.
//         tests/py/test_move_ui.py runs it.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import crypto from "node:crypto";
import { ROOT, W, H, png, grey, Plugin, Frame, CHAIN_MENUS } from "../move/ui/harness.mjs";

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d; };
const CHECK = args.includes("--check"), DOCS_OUT = args.includes("--docs");
const K = Number(opt("--scale", "4"));
const OUT = CHECK ? null : path.resolve(opt("--out", path.join(ROOT, "build", "move", "ui-shots")));
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const IMG = path.join(ROOT, "docs", "manual", "img", "move");
const LINKED = ["docs/MOVE.md", "docs/MOVE-DEV.md", "README.md", "docs/MANUAL.md"];
const SRC = fs.readFileSync(path.join(ROOT, "move", "module", "ui_chain.js"), "utf8");
const FR_PAGES = JSON.parse(SRC.match(/const FR_PAGES = (\[.*?\]);\n/)[1]);
const PRESET_OF = { 0: 0, 4: 12, 5: 14 };            // TYPE -> the factory sound shown with it (F01, F13, F15)

// ui_chain.js as Schwung's component editor runs it: the globals of a module UI (fill_rect, clear_screen, the slot's
// param shims) on the harness's plugin model, Schwung's trailing menus, a clock of our own. The three imports (the knob
// rings, the arrows' LEDs, the screen reader's announce) are left out: they light LEDs or speak, not the screen.
class Chain {
    constructor(setup) {
        this.p = new Plugin();
        this.fb = new Frame();
        this.t = 1000000;
        if (setup) setup(this.p);
        const self = this, g = { Math, JSON, String, Number, Object, Array, isFinite };
        g.Date = { now: () => self.t };
        g.globalThis = g;
        g.fill_rect = (x, y, w, h, c) => self.fb.fill(x, y, w, h, c);
        g.clear_screen = () => self.fb.fill(0, 0, W, H, 0);
        g.host_module_get_param = (k) => self.p.get(k);
        g.host_module_set_param = (k, v) => { self.p.set(k, v); return true; };
        g.shadow_component_trailing_menus = () => CHAIN_MENUS;
        g.shadow_component_run_action = () => true;
        vm.createContext(g);
        const src = SRC.replace(/^import \* as KNOB_LEDS from .*$/m, "const KNOB_LEDS = undefined;")
            .replace(/^import \{ setButtonLED as FR_SET_BUTTON_LED \} from .*$/m, "const FR_SET_BUTTON_LED = undefined;")
            .replace(/^import \{ announce as FR_ANNOUNCE \} from .*$/m, "const FR_ANNOUNCE = undefined;");
        vm.runInContext(src, g, { filename: "ui_chain.js" });
        this.ui = g.chain_ui;
        this.ui.init();
        this.settle();
    }
    st() { return this.ui._state(); }
    midi(...d) { this.ui.onMidiMessageInternal(d); this.p.apply(); return this; }
    jog(d) { return this.midi(0xB0, 14, d >= 0 ? d : 128 + d); }
    click() { return this.midi(0xB0, 3, 127); }
    touch(k, down) { return this.midi(0x90, k, down ? 127 : 0); }
    back() { this.ui.handleBack(); return this; }
    wait(ms) { this.t += ms; return this; }
    settle() { for (let i = 0; i < 12; i++) { this.t += 120; this.ui.tick(); } return this; }   // the live reads land
    page(title) {
        const want = FR_PAGES.findIndex((p) => p.title === title);
        for (let n = 0; n < 40 && this.st().page !== want; n++) this.jog(this.st().page < want ? 1 : -1);
        if (this.st().page !== want) throw new Error("no page " + title);
        return this;
    }
    draw() { this.ui.tick(); return this.fb; }
}
// (every setting starts at its chain_params default, the FM-1's first boot: the arpeggiator's from arp.c arp_defaults)
const engine = (type) => (p) => { p.v.p_type = type; p.preset = PRESET_OF[type]; };
// the list (a jog click) with the cursor on the row labelled `label`
const listAt = (label) => {
    const c = new Chain(engine(0)).click();
    const at = c.st().rows.findIndex((r) => r.label === label);
    if (at < 0) throw new Error("no row " + label);
    while (c.st().cursor < at) c.jog(1);
    return c;
};

// the docs' pictures: name -> how to reach it (the Chain is new for each, so no shot depends on another)
const DOC_SHOTS = {
    // the pages, one overview each (the VOICE pages with their engine)
    "frodhes": () => new Chain(engine(0)),
    "piano-voice": () => new Chain(engine(0)).page("PIANO VOICE"),
    "vibes-voice": () => new Chain(engine(4)).page("VIBES VOICE"),
    "vibes-motor": () => new Chain(engine(4)).page("VIBES MOTOR"),
    "marimba-voice": () => new Chain(engine(5)).page("MARIMBA VOICE"),
    "compressor": () => new Chain(engine(0)).page("COMPRESSOR"),
    "compressor-gr": () => new Chain((p) => { engine(0)(p); p.v.p_cmp_sw = 1; p.gr = "120 200"; }).page("COMPRESSOR").settle(),
    "eq": () => new Chain(engine(0)).page("EQ"),
    // the EQ set as the FM-1's EQ picture (docs/manual/img/eq.png): LOW +3.1, MID -4.0 at 551 Hz, HIGH +1.9
    "eq-set": () => new Chain((p) => { engine(0)(p); Object.assign(p.v, { p_eq_lo: 77, p_eq_mid: 47, p_eq_freq: 40, p_eq_hi: 72 }); })
        .page("EQ"),
    "eq-set-k3": () => new Chain((p) => { engine(0)(p); Object.assign(p.v, { p_eq_lo: 77, p_eq_mid: 47, p_eq_freq: 40, p_eq_hi: 72 }); })
        .page("EQ").touch(2, true),
    "drive-filter": () => new Chain(engine(0)).page("DRIVE/FILTER"),
    "trem-phaser": () => new Chain(engine(0)).page("TREM/PHASER"),
    "chorus": () => new Chain(engine(0)).page("CHORUS"),
    "delay": () => new Chain(engine(0)).page("DELAY"),
    "reverb": () => new Chain(engine(0)).page("REVERB"),
    "tempo": () => new Chain(engine(0)).page("TEMPO"),
    "arpeggio": () => new Chain(engine(0)).page("ARPEGGIO"),
    "lfo-setup": () => new Chain(engine(0)).page("LFO/SETUP"),
    "mappings": () => new Chain(engine(0)).page("MAPPINGS"),
    // the large card: a number, an option, the preset
    "drive-filter-k2": () => new Chain(engine(0)).page("DRIVE/FILTER").touch(1, true),
    "reverb-k1": () => new Chain(engine(0)).page("REVERB").touch(0, true),
    "frodhes-k5": () => new Chain(engine(0)).touch(4, true),
    // REVERB INF: DECAY at 127 is FREEZE (FREZ on the overview)
    "reverb-freeze": () => new Chain((p) => { engine(0)(p); p.v.p_rv_type = 3; p.v.p_rv_decay = 127; }).page("REVERB"),
    "reverb-freeze-k3": () => new Chain((p) => { engine(0)(p); p.v.p_rv_type = 3; p.v.p_rv_decay = 127; })
        .page("REVERB").touch(2, true),
    // the header: X (the effects off, the Left arrow) and S (SUS on, the Right arrow)
    "header-fx-sus": () => new Chain((p) => { engine(0)(p); p.v.s_fx = 0; p.v.s_sustain = 1; }),
    // a double tap on SPREAD (at 50): back to 0, the toast
    "reset": () => {
        const c = new Chain((p) => { engine(0)(p); p.v.p_spread = 50; });
        c.touch(1, true).wait(60).touch(1, false).wait(100).touch(1, true).wait(60).touch(1, false);
        return c.wait(600);
    },
    // the list: on the first page, the cursor four pages on (DRIVE/FILTER); then the menu below the pages
    "pages": () => new Chain(engine(0)).click().jog(1).jog(1).jog(1).jog(1),
    "menu": () => listAt("SAVE TO USER"),
    "menu-module": () => listAt("SWAP MODULE"),
    // SAVE TO USER: U03 picked (empty). (No shot after the click: the plugin names the slot from the TYPE and the
    // effects, presets.c preset_autoname, which the plugin model does not compute.)
    "save": () => listAt("SAVE TO USER").click().jog(1).jog(1),
};

const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const big = (fb, k) => png(grey(fb, k), W * k, H * k);
const snap = (fb) => ({ px: Uint8Array.from(fb.px) });
const save = (name, fb) => {
    fs.writeFileSync(path.join(OUT, name + ".png"), png(grey(fb, 1), W, H));
    if (K > 1) fs.writeFileSync(path.join(OUT, `${name}-${K}x.png`), big(fb, K));
};
const hash = (buf) => crypto.createHash("sha256").update(buf).digest("hex").slice(0, 8);

// the docs' pictures, 4x (always drawn: --check compares them, --docs writes them)
const docs = {};
for (const [n, make] of Object.entries(DOC_SHOTS)) docs[n] = big(make().draw(), 4);

if (CHECK) {
    const bad = [];
    const have = fs.existsSync(IMG) ? fs.readdirSync(IMG).filter((f) => f.endsWith(".png")) : [];
    for (const [n, b] of Object.entries(docs)) {
        const f = path.join(IMG, `move-${n}.png`);
        if (!fs.existsSync(f)) bad.push(`missing: docs/manual/img/move/move-${n}.png`);
        else if (!fs.readFileSync(f).equals(b)) bad.push(`stale: docs/manual/img/move/move-${n}.png`);
    }
    for (const f of have) if (!(f.replace(/^move-|\.png$/g, "") in docs)) bad.push(`orphan: docs/manual/img/move/${f}`);
    const used = new Set();
    for (const d of LINKED) {
        const p = path.join(ROOT, d);
        if (!fs.existsSync(p)) continue;
        const text = fs.readFileSync(p, "utf8");
        for (const m of text.matchAll(/((?:docs\/)?manual\/img\/move\/move-[\w-]+\.png)(?:\?v=([0-9a-f]*))?/g)) {
            used.add(m[1].replace(/^.*\/move-|\.png$/g, ""));
            const f = path.join(path.dirname(p), m[1]);
            if (!fs.existsSync(f)) { bad.push(`${d}: ${m[1]} does not exist`); continue; }
            const h = hash(fs.readFileSync(f));
            if (m[2] !== h) bad.push(`${d}: ${m[1]}?v=${m[2] || ""}, the file's hash is ${h}`);
        }
    }
    for (const n of Object.keys(docs)) if (!used.has(n)) bad.push(`orphan: move-${n}.png is linked from none of ${LINKED.join(", ")}`);
    for (const b of bad) console.log(b);
    console.log(`move_ui_shots --check: ${Object.keys(docs).length} docs pictures, ${bad.length} problem(s)` +
                (bad.length ? " (run node tools/move_ui_shots.mjs --docs)" : ""));
    process.exit(bad.length ? 1 : 0);
}

// every page per engine: the overview and each knob's large card
const rows = [];          // [[title, [frames]]]
const done = new Set();
let written = 0;
for (const type of [0, 4, 5]) {
    const eng = new Chain(engine(type)).st().engine;         // (each shot from a fresh screen)
    for (const pg of FR_PAGES.filter((p) => p.gate < 0 || p.gate === eng)) {
        if (done.has(pg.title)) continue;
        done.add(pg.title);
        const name = slug(pg.title);
        const ov = new Chain(engine(type)).page(pg.title).draw();
        save(name, ov); written++;
        const frames = [snap(ov)];
        pg.knobs.forEach((k, i) => {
            if (!k) return;                                  // (no control on that knob: no card)
            const fb = new Chain(engine(type)).page(pg.title).touch(i, true).draw();
            save(`${name}-k${i + 1}`, fb); written++;
            frames.push(snap(fb));
        });
        rows.push([pg.title, frames]);
    }
}
for (const n of Object.keys(DOC_SHOTS)) {
    if (fs.existsSync(path.join(OUT, n + ".png"))) continue;   // (a page or a card already written above)
    save(n, DOC_SHOTS[n]().draw()); written++;
}

// the sheet: 2x, a 4 px grey gap between the screens (the gap is not part of any screen)
const SK = 2, GAP = 4, cols = Math.max(...rows.map((r) => r[1].length));
const SW = cols * (W * SK + GAP) + GAP, SH = rows.length * (H * SK + GAP) + GAP;
const sheet = new Uint8Array(SW * SH).fill(96);
rows.forEach(([, frames], r) => frames.forEach((f, c) => {
    const ox = GAP + c * (W * SK + GAP), oy = GAP + r * (H * SK + GAP);
    for (let y = 0; y < H * SK; y++)
        for (let x = 0; x < W * SK; x++)
            sheet[(oy + y) * SW + ox + x] = f.px[Math.floor(y / SK) * W + Math.floor(x / SK)] ? 255 : 0;
}));
fs.writeFileSync(path.join(OUT, "sheet.png"), png(sheet, SW, SH));

if (DOCS_OUT) {
    // (a folder of their own: docs/manual/img/ is tools/manual_shots.py's, test_manual_shots.py checks it whole)
    fs.mkdirSync(IMG, { recursive: true });
    for (const f of fs.readdirSync(IMG)) if (/^move-.*\.png$/.test(f) && !(f.replace(/^move-|\.png$/g, "") in docs))
        fs.unlinkSync(path.join(IMG, f));
    for (const [n, b] of Object.entries(docs)) fs.writeFileSync(path.join(IMG, `move-${n}.png`), b);
    // the links' ?v= = the picture's sha256, first 8 hex digits (tools/doc_links.py's rule); only the move/ links
    for (const d of LINKED) {
        const p = path.join(ROOT, d);
        if (!fs.existsSync(p)) continue;
        const text = fs.readFileSync(p, "utf8");
        const out = text.replace(/((?:docs\/)?manual\/img\/move\/move-[\w-]+\.png)(?:\?v=[0-9a-f]*)?/g, (m, rel) => {
            const f = path.join(path.dirname(p), rel);
            return fs.existsSync(f) ? `${rel}?v=${hash(fs.readFileSync(f))}` : m;
        });
        if (out !== text) fs.writeFileSync(p, out);
    }
}
console.log(`${written} screens + sheet.png (${rows.length} pages) in ${path.relative(process.cwd(), OUT) || "."}` +
            (DOCS_OUT ? `; ${Object.keys(docs).length} docs pictures in docs/manual/img/move` : ""));
