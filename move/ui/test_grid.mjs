// SPDX-License-Identifier: GPL-3.0-only
// node move/ui/test_grid.mjs: Frodhes' pages through Schwung's own knob-grid controller (page_controller.mjs, the
// device's code, from SCHWUNG_SRC, default ../frodhes-move/research/schwung next to the repo) against a model of the
// plugin's answers (move_bridge.h mb_get). The 0.9.8 candidate's grid bugs, each a check that failed on its contract:
//   - the VOICE pages did not follow TYPE: the levels were gated on ui_engine, which the grid never re-reads after a
//     TYPE change (it re-plans a visible_if only on its own write of the gate key or a pad press), so the knobs kept
//     the previous engine's parameters. Now the plugin serves the sounding engine's pages and answers is_loading 1 for
//     a moment after the engine moves; the grid re-reads the contract on the 1 -> 0 edge.
//   - PEDAL (s_susbtn) was a cell whose click left the sustain held: no page carries s_susbtn now.
//   - PAGES (s_screen) answered nothing for its value line: "OPEN"; a knob touch and a click open screen.js.
//   - FX drew its switch off while the effects were on (Movy's switch is on at option 1): OFF / ON.
// Exit 0 when every check passes; run by tests/py/test_move_ui.py. No Schwung source: skipped (exit 0).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const MOD = path.join(ROOT, "move", "module");
const CHAIN = JSON.parse(fs.readFileSync(path.join(MOD, "chain_params.json"), "utf8"));
const FULL = JSON.parse(fs.readFileSync(path.join(MOD, "ui_hierarchy.json"), "utf8"));
const SCHWUNG = process.env.SCHWUNG_SRC || [path.resolve(ROOT, ".."), path.resolve(ROOT, "..", "..", "..", "..")]
    .map((d) => path.join(d, "frodhes-move", "research", "schwung")).find((d) => fs.existsSync(d)) || "(not found)";
const PP = path.join(SCHWUNG, "src", "shared", "param_pages");
if (!fs.existsSync(path.join(PP, "page_controller.mjs"))) {
    console.log("skip  Schwung's controller: no source at " + SCHWUNG + " (SCHWUNG_SRC)");
    process.exit(0);
}
const imp = (f) => import(pathToFileURL(path.join(PP, f)).href);
const [C, M, R] = await Promise.all([imp("page_controller.mjs"), imp("param_meta.mjs"), imp("render_page_movy.mjs")]);

// the texts move_ui.h serves for get_param("ui_hierarchy"), one per engine (C string pieces joined and unescaped)
function servedTexts() {
    const h = fs.readFileSync(path.join(MOD, "move_ui.h"), "utf8");
    const out = [];
    for (let e = 0; ; e++) {
        const m = h.match(new RegExp("static const char MOVE_UI_HIERARCHY_E" + e + "\\[\\] =\\n([\\s\\S]*?);\\n"));
        if (!m) break;
        out.push([...m[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((x) => JSON.parse('"' + x[1] + '"')).join(""));
    }
    return out;
}
const SERVED = servedTexts();
const TYPE_ENG = [0, 0, 0, 0, 1, 2];        // eng_pages.h TYPE_UI
const ENG_MOVED_MS = 600;                   // move_bridge.h MB_ENG_MOVED_MS

// the plugin as the grid sees it: a parameter store, the per-engine pages, is_loading (mb_engine_moved); `old`: the
// 0.9.8 candidate's answers (one hierarchy with visible_if on ui_engine, is_loading not served)
function plugin({ old = false } = {}) {
    const p = { clock: 1000, store: { p_type: "0", p_spread: "0", s_sustain: "0", s_fx: "1", preset: "0",
                                        preset_count: "35", preset_name: "F01 ST77" },
                writes: [], reads: [], seen: null, movedAt: -1e9 };
    const engine = () => TYPE_ENG[Number(p.store.p_type)] || 0;
    p.get = (k) => {
        const b = String(k).replace(/^[^:]+:/, "");
        p.reads.push(b);
        if (b === "ui_hierarchy") return old ? JSON.stringify(FULL) : SERVED[engine()];
        if (b === "chain_params") return JSON.stringify(CHAIN);
        if (b === "ui_engine") return String(engine());
        if (b === "s_screen") return old ? "" : "OPEN";
        if (b === "is_loading") {
            if (old) return "";
            if (p.seen === null) p.seen = engine();
            if (engine() !== p.seen) { p.seen = engine(); p.movedAt = p.clock; }
            return p.clock - p.movedAt < ENG_MOVED_MS ? "1" : "0";
        }
        return b in p.store ? p.store[b] : "";
    };
    p.set = (k, v) => { const b = String(k).replace(/^[^:]+:/, ""); p.writes.push([b, String(v)]); p.store[b] = String(v); };
    return p;
}

// Schwung's grid: the controller, ticked at ~42 Hz, with the host's is_loading poll (shadow_ui_param_pages.mjs
// tickParamPages: every LOADING_POLL_TICKS = 8 ticks, "" = not served (stop asking), reloadIfChanged on 1 -> 0)
function grid(p) {
    const ctl = C.createController({ getParam: p.get, setParam: p.set, announce: () => {}, now: () => p.clock });
    if (ctl.setLayout && C.LAYOUT_MOVY) ctl.setLayout(C.LAYOUT_MOVY);
    // the device's visible_if evaluator (shadow_ui.js evaluateVisibilityCondition on the grid): the grid's own value
    // first, else a read of the key (equals / not_equals / lt / gt as Schwung's compareConditionValue)
    const visible = (c) => {
        if (!c || typeof c !== "object" || !c.param) return true;
        const held = ctl.state.values[c.param];
        const raw = held !== undefined ? held : p.get("synth:" + c.param);
        if (raw === null || raw === undefined || raw === "") return true;
        if (c.equals !== undefined) return Number(raw) === c.equals;
        if (c.not_equals !== undefined) return Number(raw) !== c.not_equals;
        if (c.lt !== undefined) return Number(raw) < c.lt;
        if (c.gt !== undefined) return Number(raw) > c.gt;
        return true;
    };
    ctl.load({ prefix: "synth", visible });
    let poll = 0, interval = 8, was = false;
    const g = {
        ctl,
        tick(n = 1) {
            for (let i = 0; i < n; i++) {
                p.clock += 24;
                if (++poll >= interval) {
                    poll = 0;
                    const raw = p.get("synth:is_loading");
                    if (raw === "") interval = Infinity;
                    else { const now = raw === "1"; if (!now && was) ctl.reloadIfChanged({ visible }); was = now; }
                }
                ctl.tick();
            }
        },
        pages: () => ctl.state.pages.map((q) => q.name || q.label || ""),
        page: () => ctl.state.pages[ctl.state.pageIndex],
        goto(key) {
            const i = ctl.state.pages.findIndex((q) => q.keys && q.keys.includes(key));
            assert.ok(i >= 0, "a page holds " + key);
            ctl.goToPage(i);
            g.tick(2);
            return ctl.state.pages[i].keys.indexOf(key);
        },
        turn(key, dir, n) {             // a hand on the knob: touch, n detents, let go, the writes flushed
            const slot = g.goto(key);
            ctl.onKnobTouch(slot, true);
            for (let i = 0; i < n; i++) { p.clock += 40; ctl.onKnobTurn(slot, dir, p.clock); ctl.tick(); }
            ctl.onKnobTouch(slot, false);
            g.tick(30);
        },
    };
    g.tick(20);
    return g;
}
const VOICES = ["PIANO VOICE", "VIBES VOICE", "VIBES MOTOR", "MARIMBA VOICE"];
const voices = (g) => g.pages().filter((n) => VOICES.some((v) => n.startsWith(v)));
const OWN = [["PIANO VOICE"], ["VIBES VOICE", "VIBES MOTOR"], ["MARIMBA VOICE"]];
const sameVoices = (g, e) => assert.deepEqual([...new Set(voices(g).map((n) => VOICES.find((v) => n.startsWith(v))))], OWN[e]);
// a TYPE turn to option t
function turnType(g, p, t) {
    for (let i = 0; i < 40 && Number(p.store.p_type) !== t; i++) g.turn("p_type", Number(p.store.p_type) < t ? 1 : -1, 1);
    assert.equal(Number(p.store.p_type), t);
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("served: one text per engine, its VOICE levels only, no visible_if, no link to a level not served", () => {
    assert.equal(SERVED.length, 3);
    SERVED.forEach((t, e) => {
        const h = JSON.parse(t);
        assert.ok(!t.includes("visible_if"));
        const names = Object.values(h.levels).map((l) => l.name);
        assert.deepEqual(names.filter((n) => VOICES.includes(n)), OWN[e]);
        for (const l of Object.values(h.levels))
            for (const x of l.params) if (x && x.level) assert.ok(x.level in h.levels, x.level);
    });
});

test("the 0.9.8 candidate's contract: TYPE to VIBE keeps the PIANO VOICE pages (the bug, as on the device)", () => {
    const p = plugin({ old: true }), g = grid(p);
    sameVoices(g, 0);
    turnType(g, p, 4);
    g.tick(200);
    assert.ok(voices(g).includes("PIANO VOICE") && !voices(g).includes("VIBES VOICE"), voices(g).join());
});

test("TYPE on its knob: the VOICE pages follow within half a second, and the grid stays on the TYPE page", () => {
    const p = plugin(), g = grid(p);
    sameVoices(g, 0);
    for (const [t, e] of [[4, 1], [5, 2], [1, 0], [4, 1], [0, 0]]) {
        turnType(g, p, t);
        const t0 = p.clock;
        g.tick(40);
        sameVoices(g, e);
        assert.ok(g.page().keys.includes("p_type"), "still on the TYPE page");
        assert.ok(p.clock - t0 < 1100);
    }
});

test("a sound change from outside the grid (a preset, a program change): the VOICE pages follow", () => {
    const p = plugin(), g = grid(p);
    g.goto("p_tone");
    p.store.p_type = "5";                        // a program change to a MARIMBA preset (MIDI, not the grid)
    g.tick(60);
    sameVoices(g, 2);
    p.store.p_type = "4";
    g.ctl.selectionChanged();                    // the list editor's preset browser books the settle
    g.tick(60);
    sameVoices(g, 1);
});

test("the knobs of the VOICE page turn the sounding engine's parameters", () => {
    const p = plugin(), g = grid(p);
    turnType(g, p, 4);
    g.tick(40);
    p.writes.length = 0;
    g.turn("p_strike", 1, 3);
    assert.ok(p.writes.some(([k]) => k === "p_strike"), JSON.stringify(p.writes));
    assert.ok(!g.ctl.state.pages.some((q) => q.keys && q.keys.includes("p_tone")), "no PIANO knob left");
});

test("idle, is_loading costs one read in 8 ticks and nothing re-plans", () => {
    const p = plugin(), g = grid(p);
    const fp = g.ctl.state.fingerprint;
    p.reads.length = 0;
    g.tick(80);
    assert.equal(p.reads.filter((k) => k === "is_loading").length, 10);
    assert.equal(p.reads.filter((k) => k === "ui_hierarchy").length <= 10, true);
    assert.equal(g.ctl.state.fingerprint, fp);
});

test("no page carries s_susbtn (PEDAL), and no canvas key has a chain_params entry (Schwung's merge would win)", () => {
    for (const t of SERVED.concat([JSON.stringify(FULL)])) {
        const h = JSON.parse(t);
        for (const l of Object.values(h.levels)) {
            assert.ok(!l.knobs.includes("s_susbtn"), l.name);
            for (const x of l.params) if (x && x.type === "canvas") assert.ok(!CHAIN.some((c) => c.key === x.key), x.key);
        }
    }
    assert.ok(!CHAIN.some((c) => c.key === "s_susbtn"));
});

test("PAGES: its value line reads OPEN, and a knob touch with a jog click opens screen.js", () => {
    const p = plugin(), g = grid(p);
    const slot = g.goto("s_screen");
    g.tick(20);
    assert.equal(g.ctl.state.values.s_screen, "OPEN");
    g.ctl.onKnobTouch(slot, true);
    const opened = g.ctl.onClick(slot);
    const r = opened ? g.ctl.takePending() : null;
    g.ctl.onKnobTouch(slot, false);
    assert.ok(r && r.action === "open", JSON.stringify(r));
    const meta = M.buildMetaIndex({ hierarchy: JSON.parse(SERVED[0]), chainParams: CHAIN }).getOrGuess("s_screen");
    assert.equal(meta.canvas_script, "screen.js");
    assert.ok(!p.writes.some(([k]) => k === "s_screen" || k === "s_susbtn"), JSON.stringify(p.writes));
});

test("FX: the switch reads OFF / ON, so Movy's switch (on at option 1) is on while the effects play", () => {
    const fx = CHAIN.find((c) => c.key === "s_fx");
    assert.deepEqual(fx.options, ["OFF", "ON"]);
});

test("labels: no knob's name is cut on Schwung's grid (its proportional font, 29 px: render_page_movy labelSqueezed)", () => {
    const mi = M.buildMetaIndex({ hierarchy: FULL, chainParams: CHAIN });
    const cut = [];
    for (const l of Object.values(FULL.levels))
        for (const k of l.knobs) {
            const name = mi.getOrGuess(k).name || k;
            if (R.labelSqueezed(name)) cut.push(`${l.name}: ${name} -> ${R.labelForCell(name)}`);
        }
    assert.deepEqual(cut, []);
    assert.ok(Object.values(JSON.parse(SERVED[0]).levels).some((l) => l.knobs.includes("p_spread")));
    assert.equal(R.labelForCell("SPREAD"), "SPREAD", "SPREAD in full");
});

let fail = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log("ok    " + name); } catch (e) { fail++; console.log("FAIL  " + name + "\n      " + (e && e.message)); }
}
console.log(fail ? `${fail} of ${tests.length} failed` : `all ${tests.length} passed`);
process.exit(fail ? 1 : 0);
