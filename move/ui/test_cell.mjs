// SPDX-License-Identifier: GPL-3.0-only
// node move/ui/test_cell.mjs: the gain-reduction cell on Schwung's knob grid (move/module/canvas.js, a custom widget)
// on the host: the value's three states (OFF, a reading, no answer), the bar on ui.c's segment scale, the peak, the
// reading, every frame size inside its frame, no reads. With Schwung's source at hand (SCHWUNG_SRC, default
// ../frodhes-move/research/schwung next to the repo) it also runs Frodhes' COMPRESSOR level through Schwung's own
// planner, viz resolver and Movy renderer (the device's), and writes the page as a PNG (--png FILE). Exit 0 when
// every check passes; run by tests/py/test_move_ui.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = fs.readFileSync(path.join(ROOT, "move", "module", "canvas.js"), "utf8");
const CHAIN = JSON.parse(fs.readFileSync(path.join(ROOT, "move", "module", "chain_params.json"), "utf8"));
const HIER = JSON.parse(fs.readFileSync(path.join(ROOT, "move", "module", "ui_hierarchy.json"), "utf8"));

function load() {           // as Schwung loads it: a script over the host's globals, canvas_overlay taken from them
    const g = {};
    new Function("globalThis", SRC)(g);
    return g;
}
const G = load();
const CELL = G.frodhes_gr_cell, OV = G.canvas_overlay;

// a frame of w x h: the lit pixels, and every write outside the frame counted
function frame(w, h) {
    const px = new Uint8Array(w * h);
    let out = 0, calls = 0;
    return {
        width: w, height: h, px,
        get out() { return out; }, get calls() { return calls; },
        fillRect(x, y, rw, rh, c) {
            calls++;
            for (let j = y; j < y + rh; j++)
                for (let i = x; i < x + rw; i++) {
                    if (i < 0 || j < 0 || i >= w || j >= h) { out++; continue; }
                    px[j * w + i] = c ? 1 : 0;
                }
        },
        at(x, y) { return px[y * w + x]; },
        lit() { return px.reduce((a, b) => a + b, 0); },
        row(y) { let s = ""; for (let x = 0; x < w; x++) s += px[y * w + x] ? "#" : "."; return s; },
    };
}
const GROUP = { kind: CELL.conf.kind, keys: ["r_cmp_gr", "r_cmp_pk"], roles: { gr: "r_cmp_gr", pk: "r_cmp_pk" } };
const draw = (v, w = 64, h = 15) => {
    const f = frame(w, h);
    OV.drawCell(f, { values: { r_cmp_gr: v }, group: GROUP, nowMs: 0 });
    return f;
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("the module declares the widget: the kind on r_cmp_gr (live, read-only), r_cmp_pk beside it, knobs 7-8", () => {
    assert.equal(OV.widgetKind, CELL.conf.kind);
    assert.ok(OV.widgetKind.startsWith("custom:"));
    assert.equal(typeof OV.drawCell, "function");
    const gr = CHAIN.find((p) => p.key === "r_cmp_gr"), pk = CHAIN.find((p) => p.key === "r_cmp_pk");
    assert.ok(gr && pk);
    assert.equal(gr.viz.kind, OV.widgetKind);
    assert.equal(gr.live, true);
    assert.equal(gr.access, "read");
    assert.equal(pk.access, "read");
    assert.equal(gr.viz.group, pk.viz.group);
    const comp = HIER.levels.comp;
    assert.equal(comp.knobs.indexOf("r_cmp_gr"), 6);
    assert.equal(comp.knobs.indexOf("r_cmp_pk"), 7);
    const mod = JSON.parse(fs.readFileSync(path.join(ROOT, "move", "module", "module.json"), "utf8"));
    assert.equal(mod.capabilities.canvas_script, "canvas.js");
});

test("the plugin's value: OFF, a reading, a reading without a peak, no answer", () => {
    assert.deepEqual(CELL.parse("OFF"), { off: true, db10: 0, pk: 0 });
    assert.deepEqual(CELL.parse("125 200"), { off: false, db10: 125, pk: 200 });
    assert.deepEqual(CELL.parse("0 0"), { off: false, db10: 0, pk: 0 });
    assert.deepEqual(CELL.parse("40"), { off: false, db10: 40, pk: 0 });
    assert.deepEqual(CELL.parse("999 999"), { off: false, db10: 300, pk: 300 }, "clamped to the scale");
    for (const v of [null, undefined, "", "--", "x y"]) assert.equal(CELL.parse(v), null, String(v));
});

test("the scale is ui.c's: 15 segments evenly apart, 30 dB at the end, monotonic", () => {
    const sc = CELL.conf.gr_db10;
    assert.equal(sc.length, 15);
    assert.equal(sc[sc.length - 1], 300);
    const w = 62;
    assert.equal(CELL.px(0, w), 0);
    assert.equal(CELL.px(300, w), w);
    for (let i = 0; i < sc.length; i++) assert.equal(CELL.px(sc[i], w), Math.floor((i + 1) * w / sc.length), `segment ${i}`);
    let last = 0;
    for (let d = 0; d <= 300; d++) { const x = CELL.px(d, w); assert.ok(x >= last); last = x; }
    assert.equal(CELL.reading(0), "0.0 DB");
    assert.equal(CELL.reading(125), "-12.5 DB");
    assert.equal(CELL.reading(300), "-30.0 DB");
});

test("a reading: the track, the fill from the right, the peak mark, the text right-aligned under it", () => {
    const f = draw("60 150");
    assert.equal(f.out, 0);
    const iw = 62, fw = CELL.px(60, iw), pk = CELL.px(150, iw);
    assert.ok(fw > 0 && pk > fw + 2);
    for (let x = 0; x < 64; x++) { assert.equal(f.at(x, 0), 1); assert.equal(f.at(x, 8), 1); }   // the track
    for (let x = 1; x <= 62; x++) {
        const filled = x >= 1 + iw - fw, peak = x >= 1 + iw - pk && x < 1 + iw - pk + 2;
        assert.equal(f.at(x, 4), filled || peak ? 1 : 0, `x ${x}`);
    }
    let right = 0;
    for (let y = 10; y < 15; y++) right = Math.max(right, f.row(y).replace(/\.+$/, "").length);
    assert.equal(right, 64, "the reading ends at the right edge");
    assert.ok(f.row(9).indexOf("#") < 0, "a blank row between the track and the text");
});

test("more reduction fills more; 0 dB fills nothing; 30 dB fills the track", () => {
    const fill = (v) => { const f = draw(v); let n = 0; for (let x = 1; x < 63; x++) n += f.at(x, 4); return n; };
    assert.equal(fill("0 0"), 0);
    assert.equal(fill("300 300"), 62);
    let last = -1;
    for (const d of [10, 30, 60, 100, 150, 200, 270]) { const n = fill(d + " 0"); assert.ok(n > last, d); last = n; }
});

test("the peak mark shows only above the fill", () => {
    const a = draw("150 150"), b = draw("150 0");
    assert.deepEqual([...a.px], [...b.px]);
    const c = draw("10 300");
    assert.equal(c.at(1, 4), 1, "the peak at 30 dB: the left end");
    assert.equal(c.at(2, 4), 1);
    assert.equal(c.at(10, 4), 0);
});

test("COMP off: a dotted track, no fill, COMP OFF", () => {
    const f = draw("OFF");
    assert.equal(f.out, 0);
    assert.equal(f.row(0), "#.".repeat(32));
    for (let x = 1; x < 63; x++) assert.equal(f.at(x, 4), 0);
    assert.ok(f.row(10).indexOf("#") >= 0);
    assert.notDeepEqual([...f.px], [...draw("0 0").px], "OFF is not 0 dB");
});

test("no answer yet: the track only, no reading (never a made-up 0 dB)", () => {
    const f = draw(null), z = draw("0 0");
    for (let y = 10; y < 15; y++) assert.ok(f.row(y).indexOf("#") < 0);
    assert.ok(z.row(10).indexOf("#") >= 0);
    const n = draw(undefined);
    assert.deepEqual([...n.px], [...f.px]);
    const nothing = frame(64, 15);
    OV.drawCell(nothing, {});                                 // no values, no group: still no throw
});

test("every frame size Schwung may hand it: inside the frame, no throw", () => {
    for (let w = 1; w <= 128; w += 3)
        for (let h = 1; h <= 40; h += 2)
            for (const v of ["OFF", "125 300", "0 0", null, "300 300"]) {
                const f = draw(v, w, h);
                assert.equal(f.out, 0, `${w}x${h} ${v}`);
            }
    const one = draw("60 150", 32, 15);                       // one cell (an older or narrower grid)
    assert.ok(one.lit() > 40);
});

test("no reads and no device font: the ctx it gets has fillRect only", () => {
    const f = frame(64, 15);
    const ctx = { width: 64, height: 15, fillRect: f.fillRect };
    OV.drawCell(ctx, { values: { r_cmp_gr: "120 200" }, group: GROUP });
    assert.ok(f.calls > 0);
    assert.ok(!/getParam|setParam/.test(SRC), "the cell never reads or writes");
});

// ---- Schwung's own pipeline (its planner, viz resolver, widget registry and the Movy renderer the device uses)
// (the repo next to frodhes-move/, or a worktree of it under .claude/worktrees/)
const SCHWUNG = process.env.SCHWUNG_SRC || [path.resolve(ROOT, ".."), path.resolve(ROOT, "..", "..", "..", "..")]
    .map((d) => path.join(d, "frodhes-move", "research", "schwung")).find((d) => fs.existsSync(d)) || "(not found)";
const PNG = (() => { const i = process.argv.indexOf("--png"); return i > 0 ? process.argv[i + 1] : null; })();
const haveSchwung = fs.existsSync(path.join(SCHWUNG, "src", "shared", "param_pages", "render_page_movy.mjs"));
if (haveSchwung) {
    const imp = (p) => import(pathToFileURL(path.join(SCHWUNG, p)).href);
    const [P, M, V, R, W, H] = await Promise.all([
        imp("src/shared/param_pages/page_plan.mjs"), imp("src/shared/param_pages/param_meta.mjs"),
        imp("src/shared/param_pages/viz.mjs"), imp("src/shared/param_pages/render_page_movy.mjs"),
        imp("src/shared/param_pages/widget_registry.mjs"), imp("tools/param-pages/harness.mjs")]);
    const plan = () => {
        const { pages } = P.planPages({ hierarchy: HIER, chainParams: CHAIN });
        const metaIndex = M.buildMetaIndex({ hierarchy: HIER, chainParams: CHAIN });
        const page = pages.find((p) => p.kind === P.PAGE_KNOBS && p.keys && p.keys.includes("r_cmp_gr"));
        return { pages, metaIndex, page };
    };
    const render = (vals, file) => {
        const { pages, metaIndex, page } = plan();
        const values = { p_cmp_amt: "80", p_cmp_ratio: "3", p_cmp_atk: "40", p_cmp_gain: "10", p_cmp_sw: "1",
                         p_cmp_rel: "50", r_cmp_pk: "200", ...vals };
        const { groups } = V.resolveViz({ keys: page.keys, metaIndex });
        const fb = H.createFramebuffer();
        R.renderPageMovy(H.drawContext(fb), { page, metaIndex, values, title: "FRDH", pageIndex: pages.indexOf(page),
            pageCount: pages.length, touched: -1, viz: groups,
            footer: [["JOG", "PG"], ["SHFT", "SECT"], ["CLK", "MENU"]] });
        if (file) fs.writeFileSync(file, fb.toPng(4));
        return { fb, groups, metaIndex, page };
    };
    const lit = (fb, x0, y0, x1, y1) => {
        let n = 0;
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) n += fb.getPixel ? fb.getPixel(x, y) : fb.pixels[y * 128 + x];
        return n;
    };
    test("Schwung: the COMPRESSOR page plans r_cmp_gr / r_cmp_pk in knobs 7-8, read-only, r_cmp_gr live", () => {
        const { page, metaIndex } = plan();
        assert.ok(page, "a knob page holds r_cmp_gr");
        assert.equal(page.keys.indexOf("r_cmp_gr"), 6);
        assert.equal(page.keys.indexOf("r_cmp_pk"), 7);
        for (const k of ["r_cmp_gr", "r_cmp_pk"]) {
            const m = metaIndex.getOrGuess(k);
            assert.ok(M.isReadOnly(m) && !M.isTurnable(m) && !M.opensOnClick(m), k);
        }
        assert.equal(metaIndex.getOrGuess("r_cmp_gr").live, true);
    });
    test("Schwung: without the widget the two keys stay plain cells (an older host degrades)", () => {
        W.clearWidgets();
        const { groups } = render({ r_cmp_gr: "120 200" });
        assert.ok(!groups.some((g) => g.kind === OV.widgetKind));
    });
    test("Schwung: with canvas.js registered the group spans knobs 7-8 and draws our meter", () => {
        W.clearWidgets();
        const { registered, skipped } = W.registerOverlayWidgets(OV);
        assert.deepEqual(registered, [OV.widgetKind], JSON.stringify(skipped));
        const on = render({ r_cmp_gr: "120 200" }, PNG);
        const g = on.groups.find((x) => x.kind === OV.widgetKind);
        assert.ok(g, "the custom group resolved");
        assert.equal(g.slotStart, 6);
        assert.equal(g.slotSpan, 2);
        const off = render({ r_cmp_gr: "OFF" }), zero = render({ r_cmp_gr: "0 0" });
        const region = (fb) => lit(fb, 64, 33, 127, 47);
        assert.ok(region(on.fb) > region(zero.fb) + 100, "12 dB fills the bar");
        assert.notEqual(region(off.fb), region(zero.fb));
        // knobs 1-6 are drawn the same whatever the meter shows
        assert.equal(lit(on.fb, 0, 9, 127, 32), lit(off.fb, 0, 9, 127, 32));
        W.clearWidgets();
    });
}

let fail = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log("ok    " + name); } catch (e) { fail++; console.log("FAIL  " + name + "\n      " + (e && e.message)); }
}
if (!haveSchwung) console.log("skip  Schwung's pipeline: no source at " + SCHWUNG + " (SCHWUNG_SRC)");
console.log(fail ? `${fail} of ${tests.length} failed` : `all ${tests.length} passed`);
process.exit(fail ? 1 : 0);
