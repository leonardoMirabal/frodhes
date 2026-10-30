// SPDX-License-Identifier: GPL-3.0-only
// Frodhes' own screen on the Move (M6-02, private plan 05 M6-01 option F), the hand-written part of
// move/module/screen.js: tools/gen_move_module.py writes FR_PAGES (the pages, from params.c / eng_pages.h / ui.c,
// the same levels as ui_hierarchy.json) and FR_CONF above this text.
//
// A fullscreen canvas the root page's PAGES cell opens (enterable: the jog click is ours, Back asks handleBack).
//   overview   one page = the 8 knobs: 2 x 4 cards (label, value, bar), knobs 1-4 on top, 5-8 below
//   focus      touching or turning a knob shows one large card for it (label 10x14, value 15x21, a wide bar, the
//              K1-K8 strip); turning changes the value live; FR_CONF.hold_ms after the knob is let go (or after the
//              last turn without a touch) the overview of the same page comes back; another knob switches the card
//   pages      the jog wheel browses the pages (the VOICE pages of the engine that plays only); a jog click opens
//              the page list (the jog picks, a click opens the page, Back closes the list); Back on a page closes
//              the screen (the host's own way out; Shift + jog too)
//   menu       below the pages the list has the module's rows when the ctx offers them (the whole-screen path,
//              chain_adapter.js): SAVE TO USER (Frodhes' own U01-U16, u_save; a card picks the slot) and Schwung's
//              own trailing pages for a module-drawn grid (MY PRESETS: load / save / save as / delete; MODULE: help,
//              swap, remove ...), each row run by Schwung (ctx.runAction); a section's name is a divider row
//   reset      a double tap on a knob's touch (two touches within FR_CONF.tap_ms, no turn between) sets it to its
//              default, as Schwung's grid does
//   sustain    the claimed button (FR_CONF.sus_cc, the Right arrow) turns SUS on or off on every page: a press flips it (the
//              plugin flips it, s_susbtn 1; a release sends nothing), the header's S shows it
//   meter      COMPRESSOR: the gain reduction in the free cards' place (knobs 7-8), as the FM-1's COMP page shows it:
//              the bar filled from the right on ui.c's segment scale (FR_CONF.gr_db10), the held peak, the reading;
//              the plugin keeps the FM-1's ballistics (r_cmp_gr), the screen reads it once a live tick
// Everything is drawn with fillRect and the font below (the device's print font is not used), so the host renderer
// (tools/move_ui_shots.mjs) draws exactly what the Move draws. Reads (getParam, ~3 ms each) happen in the event
// hooks only, never in draw: a page's values on open and on a page change (cached until something else changes
// them), then one value per live tick (onValues) so a change from MIDI shows within a few seconds.
const W = 128, H = 64;
const KNOB_CC = 71, JOG_CC = 14, CLICK_CC = 3, LEFT_CC = 62, RIGHT_CC = 63;

// ---- a 5x7 font of our own (upper case; lower case is drawn as upper), 6 px advance; '.' ':' ',' "'" narrow
const GLYPHS = {
    "A": ".###.#...##...#######...##...##...#", "B": "####.#...##...#####.#...##...#####.",
    "C": ".###.#...##....#....#....#...#.###.", "D": "####.#...##...##...##...##...#####.",
    "E": "######....#....####.#....#....#####", "F": "######....#....####.#....#....#....",
    "G": ".###.#...##....#.####...##...#.####", "H": "#...##...##...#######...##...##...#",
    "I": ".###...#....#....#....#....#...###.", "J": "..###...#....#....#....#.#..#..##..",
    "K": "#...##..#.#.#..##...#.#..#..#.#...#", "L": "#....#....#....#....#....#....#####",
    "M": "#...###.###.#.##.#.##...##...##...#", "N": "#...##...###..##.#.##..###...##...#",
    "O": ".###.#...##...##...##...##...#.###.", "P": "####.#...##...#####.#....#....#....",
    "Q": ".###.#...##...##...##.#.##..#..##.#", "R": "####.#...##...#####.#.#..#..#.#...#",
    "S": ".#####....#.....###.....#....#####.", "T": "#####..#....#....#....#....#....#..",
    "U": "#...##...##...##...##...##...#.###.", "V": "#...##...##...##...##...#.#.#...#..",
    "W": "#...##...##...##.#.##.#.##.#.#.#.#.", "X": "#...##...#.#.#...#...#.#.#...##...#",
    "Y": "#...##...#.#.#...#....#....#....#..", "Z": "#####....#...#...#...#...#....#####",
    "0": ".###.#...##..###.#.###..##...#.###.", "1": "..#...##....#....#....#....#...###.",
    "2": ".###.#...#....#...#...#...#...#####", "3": "#####...#...#.....#.....##...#.###.",
    "4": "...#...##..#.#.#..#.#####...#....#.", "5": "######....####.....#....##...#.###.",
    "6": "..##..#...#....####.#...##...#.###.", "7": "#####....#...#...#...#....#....#...",
    "8": ".###.#...##...#.###.#...##...#.###.", "9": ".###.#...##...#.####....#...#..##..",
    "-": "................###................", "+": ".......#....#..#####..#....#.......",
    "/": "....#....#...#...#...#...#....#....", "#": ".#.#..#.#.#####.#.#.#####.#.#..#.#.",
    "%": "##...##..#...#...#...#...#..##...##", "&": ".##..#..#.#.#...#...#.#.##..#..##.#",
    "<": "...#...#...#...#.....#.....#.....#.", ">": ".#.....#.....#.....#...#...#...#...",
    "=": "..........#####.....#####..........", "?": ".###.#...#....#...#...#.......#....",
    "!": "..#....#....#....#....#.........#..", "(": "...#...#...#....#....#.....#.....#.",
    ")": ".#.....#.....#....#....#...#...#...", "_": "..............................#####",
    "*": ".......#..#.#.#.###.#.#.#..#.......", " ": "...................................",
};
const NARROW = { ".": ["..", "..", "..", "..", "..", "##", "##"], ":": ["..", "##", "##", "..", "##", "##", ".."],
                 ",": ["..", "..", "..", "..", "##", ".#", "#."], "'": ["##", ".#", "#.", "..", "..", "..", ".."] };
const glyphCache = {};

function glyph(ch) {
    let c = String(ch).toUpperCase();
    if (glyphCache[c]) return glyphCache[c];
    let rows;
    if (NARROW[c]) rows = NARROW[c];
    else {
        const g = GLYPHS[c] || GLYPHS["?"];
        rows = [];
        for (let i = 0; i < 7; i++) rows.push(g.slice(i * 5, i * 5 + 5));
    }
    // the rows as runs [x, length] so a scaled glyph costs one fillRect per run
    const runs = rows.map((r) => {
        const out = [];
        let x = 0;
        while (x < r.length) {
            if (r[x] === "#") { let n = 1; while (r[x + n] === "#") n++; out.push([x, n]); x += n; } else x++;
        }
        return out;
    });
    glyphCache[c] = { w: rows[0].length, runs };
    return glyphCache[c];
}

function textW(s, k) {
    k = k || 1;
    s = String(s);
    let w = 0;
    for (const c of s) w += (glyph(c).w + 1) * k;
    return s.length ? w - k : 0;
}

function text(ctx, x, y, s, k, color) {
    k = k || 1;
    const col = color === undefined ? 1 : color;
    for (const c of String(s)) {
        const g = glyph(c);
        g.runs.forEach((row, gy) => {
            for (const [gx, n] of row) ctx.fillRect(x + gx * k, y + gy * k, n * k, k, col);
        });
        x += (g.w + 1) * k;
    }
}

function textR(ctx, xr, y, s, k, color) { text(ctx, xr - textW(s, k) + 1, y, s, k, color); }

function hdots(ctx, x0, x1, y) { for (let x = x0; x <= x1; x += 2) ctx.fillRect(x, y, 1, 1, 1); }

function frame(ctx, x0, y0, x1, y1) {
    ctx.fillRect(x0, y0, x1 - x0 + 1, 1, 1);
    ctx.fillRect(x0, y1, x1 - x0 + 1, 1, 1);
    ctx.fillRect(x0, y0, 1, y1 - y0 + 1, 1);
    ctx.fillRect(x1, y0, 1, y1 - y0 + 1, 1);
}

// a fader, one height along its whole length (x0..x1, rows y0..y1): a 1 px outline, solid from the left up to the
// value. An enum's steps (3 or more) are notches in the inside rows (cut out of the fill, drawn into the empty part), where a
// step is 4 px or wider; a number whose range spans 0 (bipolar) has its centre marked the same way. f null: the
// outline alone (the value not read yet).
function bar(ctx, st, k, x0, y0, x1, y1) {
    frame(ctx, x0, y0, x1, y1);
    const f = fraction(st, k);
    if (f === null) return;
    const w = x1 - x0 + 1, h = y1 - y0 + 1, det = detents(st, k);
    let fill;
    if (det) fill = Math.round(w * (Math.round(f * (det - 1)) + 1) / det);
    else fill = Math.max(1, Math.round((w - 1) * f) + 1);
    ctx.fillRect(x0, y0, Math.min(w, fill), h, 1);
    const mark = (x) => {
        if (x <= x0 || x >= x1) return;
        ctx.fillRect(x, y0 + 1, 1, h - 2, x < x0 + fill ? 0 : 1);
    };
    if (det > 2 && w / det >= 4) for (let i = 1; i < det; i++) mark(x0 + Math.round(w * i / det));
    if (!det && k.type !== "preset" && k.min < 0 && k.max > 0) mark(x0 + Math.round((w - 1) * (-k.min) / (k.max - k.min)));
}

// ---- the model: pages, values, the knob arithmetic

function nowOf(ctx) { return ctx && typeof ctx.now === "function" ? ctx.now() : Date.now(); }

function visiblePages(engine) {
    const out = [];
    FR_PAGES.forEach((p, i) => { if (p.gate < 0 || p.gate === engine) out.push(i); });
    return out;
}

function knobs(st) { return FR_PAGES[st.page].knobs; }

function asNum(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return isFinite(n) ? n : null;
}

// the value as its knob card shows it: an option name, a number, the tempo with one decimal; "--" when unknown
function valueText(st, k) {
    if (k.type === "preset") {
        const nm = st.presetName ? String(st.presetName) : "";
        return nm ? nm.split(" ")[0] : "--";
    }
    const v = st.vals[k.key];
    if (v === null || v === undefined) return "--";
    if (k.type === "enum") return k.options[Math.max(0, Math.min(k.options.length - 1, v | 0))];
    if (k.step && k.step < 1) return v.toFixed(1);
    return String(v);
}

// where the bar stands: 0..1, or null when the value is unknown
function fraction(st, k) {
    const v = k.type === "preset" ? st.preset : st.vals[k.key];
    if (v === null || v === undefined) return null;
    const lo = k.type === "enum" ? 0 : k.min, hi = k.type === "enum" ? k.options.length - 1 : k.max;
    if (k.type === "preset") return st.presetCount > 1 ? v / (st.presetCount - 1) : 0;
    return hi > lo ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) : 0;
}

function detents(st, k) {
    if (k.type === "enum") return k.options.length;
    return 0;
}

function wire(k, v) { return k.step && k.step < 1 ? v.toFixed(1) : String(Math.round(v)); }

function readKey(ctx, st, k) {
    if (k.type === "preset") {
        st.preset = asNum(ctx.getParam("preset"));
        st.presetName = ctx.getParam("preset_name");
        return;
    }
    const v = asNum(ctx.getParam(k.key));
    st.vals[k.key] = v;
}

// the gain-reduction meter (COMPRESSOR): r_cmp_gr = "OFF", or "<dB x 10> <the held peak, dB x 10>"
function isComp(st) { return FR_PAGES[st.page].title === "COMPRESSOR"; }
function readGr(ctx, st) {
    const v = ctx.getParam("r_cmp_gr");
    if (v === null || v === undefined) { st.gr = null; return; }
    if (String(v) === "OFF") { st.gr = { off: true, db10: 0, pk: 0 }; return; }
    const [a, b] = String(v).split(" ").map(Number);
    st.gr = isFinite(a) ? { off: false, db10: a | 0, pk: isFinite(b) ? b | 0 : 0 } : null;
}
// a reduction's place on a bar w wide, from its right end: ui.c gr_px, its segments evenly apart
function grPx(db10, w) {
    const sc = FR_CONF.gr_db10;
    let lo = 0;
    for (let i = 0; i < sc.length; i++) {
        if (db10 <= sc[i]) return Math.floor((i * w + Math.floor(w * (db10 - lo) / (sc[i] - lo))) / sc.length);
        lo = sc[i];
    }
    return w;
}
function grText(db10) { return db10 ? "-" + Math.floor(db10 / 10) + "." + (db10 % 10) : "0.0"; }

function readPage(ctx, st) {
    if (isComp(st)) readGr(ctx, st);
    for (const k of knobs(st)) if (!(k.key in st.vals) || st.vals[k.key] === null || k.type === "preset") readKey(ctx, st, k);
}

function write(ctx, st, k, v) {
    st.written[k.key] = nowOf(ctx);
    st.expectRev++;
    if (k.type === "preset") {
        st.preset = v;
        st.presetName = null;
        ctx.setParam("preset", String(v));
        st.presetDirty = true;
        return;
    }
    ctx.setParam(k.key, wire(k, v));
    if (k.write) { st.flash = { key: k.key, until: nowOf(ctx) + FR_CONF.hold_ms }; return; }
    st.vals[k.key] = v;
}

// one knob turn: d detents (+ clockwise). An enum steps an option a detent, a number a step (Shift: the fine step
// of a tempo; a fast turn of a wide range moves faster), a trigger (CLEAR, UNDO) fires on a turn to the right.
function turn(ctx, st, slot, d) {
    const ks = knobs(st);
    if (slot >= ks.length || !d) return;
    const k = ks[slot];
    const t = nowOf(ctx);
    const fast = st.lastTurn[slot] !== undefined && t - st.lastTurn[slot] < FR_CONF.fast_ms;
    st.lastTurn[slot] = t;
    if (k.write) { if (d > 0) write(ctx, st, k, 1); return; }
    if (k.type === "preset") {
        if (st.preset === null || st.preset === undefined) readKey(ctx, st, k);
        const n = st.presetCount || 1;
        const v = Math.max(0, Math.min(n - 1, (st.preset | 0) + Math.sign(d)));
        if (v !== st.preset) write(ctx, st, k, v);
        return;
    }
    let cur = st.vals[k.key];
    if (cur === null || cur === undefined) { readKey(ctx, st, k); cur = st.vals[k.key]; }
    if (cur === null || cur === undefined) return;
    let v;
    if (k.type === "enum") v = Math.max(0, Math.min(k.options.length - 1, cur + Math.sign(d)));
    else {
        const shift = typeof ctx.shiftHeld === "function" && ctx.shiftHeld();
        let step = k.step && k.step < 1 ? (shift ? k.step : 1) : 1;
        if (fast && (k.max - k.min) > 24 && !shift) step *= FR_CONF.fast_mul;
        v = Math.max(k.min, Math.min(k.max, cur + d * step));
        if (k.step && k.step < 1) v = Math.round(v / k.step) * k.step;
    }
    if (v !== cur) write(ctx, st, k, v);
}

// a knob back to its default (FR_PAGES' def; a trigger, the preset and a key without one are left alone)
function reset(ctx, st, slot) {
    const k = knobs(st)[slot];
    if (!k || k.write || k.type === "preset" || k.def === undefined) return;
    write(ctx, st, k, k.def);
    toast(ctx, st, (k.short || k.name) + " RESET");
}

function focus(ctx, st, slot, touched) {
    if (slot >= knobs(st).length) return;
    st.focus = slot;
    st.until = touched ? 0 : nowOf(ctx) + FR_CONF.hold_ms;
}

function release(ctx, st, slot) {
    st.touch[slot] = false;
    if (st.focus === slot) {
        const other = st.touch.lastIndexOf(true);
        if (other >= 0) st.focus = other;
        else st.until = nowOf(ctx) + FR_CONF.hold_ms;
    }
}

function setPage(ctx, st, idx) {
    if (idx === st.page) return;
    st.page = idx;
    st.focus = -1;
    st.touch = [false, false, false, false, false, false, false, false];
    readPage(ctx, st);
}

function pageStep(ctx, st, d) {
    const vis = visiblePages(st.engine);
    let at = vis.indexOf(st.page);
    if (at < 0) at = 0;
    const to = Math.max(0, Math.min(vis.length - 1, at + d));
    setPage(ctx, st, vis[to]);
}

function engineChanged(ctx, st, e) {
    st.engine = e;
    const vis = visiblePages(e);
    if (vis.indexOf(st.page) < 0) {
        // the VOICE page of the engine that went: the new engine's first VOICE page, else the first page
        const old = FR_PAGES[st.page];
        let to = vis.find((i) => FR_PAGES[i].gate >= 0 && FR_PAGES[i].part === old.part);
        if (to === undefined) to = vis.find((i) => FR_PAGES[i].gate >= 0);
        setPage(ctx, st, to !== undefined ? to : vis[0]);
    }
}

function rel(v) { return v === 0 ? 0 : v < 64 ? v : v - 128; }

function sustainBtn(ctx, st) {
    st.susOn = !st.susOn;                 // (shown at once; the next live read of s_sustain confirms it)
    st.written.s_sustain = nowOf(ctx);
    ctx.setParam("s_susbtn", "1");
    st.expectRev++;
}

// the Left arrow: FX off / on (every effect but the compressor). Shown at once in the header (X) on every page, as
// the S of the sustain, since a press on any page bypasses the effects; the live read of s_fx confirms it
function fxBtn(ctx, st) {
    if (st.fxOn !== null) st.fxOn = !st.fxOn;
    st.written.s_fx = nowOf(ctx);
    delete st.vals.s_fx;                  // (the first page's FX card reads it again)
    ctx.setParam("s_fxbtn", "1");
    st.expectRev++;
}

// ---- the views

function header(ctx, title, right) {
    ctx.fillRect(0, 0, W, 8, 1);
    text(ctx, 1, 0, title, 1, 0);
    textR(ctx, 126, 0, right, 1, 0);
}

function pageNum(st) {
    const vis = visiblePages(st.engine);
    return (vis.indexOf(st.page) + 1) + "/" + vis.length;
}

function susMark(st) { return (st.fxOn === false ? "X " : "") + (st.susOn ? "S " : ""); }

function drawOverview(ctx, st) {
    const p = FR_PAGES[st.page];
    header(ctx, p.title, susMark(st) + pageNum(st));
    p.knobs.forEach((k, i) => {
        const x = (i % 4) * 32, y = 9 + Math.floor(i / 4) * 28;
        // the preset's card (the first page) takes the rest of its row: the sound's full name
        const wide = k.type === "preset" ? 127 - x - 1 : 28;
        text(ctx, x, y + 1, k.short || k.name);
        text(ctx, x, y + 11, k.type === "preset" ? (st.presetName ? String(st.presetName) : "--") : valueText(st, k));
        // the bar: 5 px (rows y+19..y+23, a row clear of the value)
        bar(ctx, st, k, x, y + 19, x + wide, y + 23);
    });
    if (p.knobs.length > 4) hdots(ctx, 0, 127, 36);
    if (isComp(st)) drawGr(ctx, st);
}

// the meter in knobs 7-8's place (x 64..127, y 37..63), the knob grid's meter drawn large (gr_cell.js): GR and the
// reading small on the top line, then a thick bar of ui.c's segments (GR_DB10), lit from the right as the reduction
// grows (a lit segment a solid block, an unlit one its floor line), the held peak a taller block on its own; COMP off:
// the bar's outline dotted and COMP OFF
function grSegs(db10) {
    const sc = FR_CONF.gr_db10;
    if (!(db10 > 0)) return 0;
    for (let i = 0; i < sc.length; i++) if (db10 <= sc[i]) return i + 1;
    return sc.length;
}
function drawGr(ctx, st) {
    const x0 = 64, x1 = 127, y = 37;
    const n = FR_CONF.gr_db10.length, pitch = Math.floor((x1 - x0 + 2) / n), segw = pitch - 1;
    const bx0 = x1 + 1 - (n * pitch - 1), by0 = y + 11, by1 = y + 25;     // the bar: 15 px tall
    text(ctx, x0, y + 2, "GR");
    if (!st.gr) return;
    if (st.gr.off) {
        for (let x = bx0; x <= x1; x += 2) { ctx.fillRect(x, by0, 1, 1, 1); ctx.fillRect(x, by1, 1, 1, 1); }
        for (let yy = by0 + 2; yy < by1; yy += 2) { ctx.fillRect(bx0, yy, 1, 1, 1); ctx.fillRect(x1, yy, 1, 1, 1); }
        textR(ctx, x1, y + 2, "COMP OFF");
        return;
    }
    textR(ctx, x1, y + 2, grText(st.gr.db10) + " DB");
    const lit = grSegs(st.gr.db10), pk = st.gr.pk ? grSegs(st.gr.pk) : 0;
    for (let i = 0; i < n; i++) {
        const x = x1 + 1 - (i + 1) * pitch + 1;
        if (i < lit) ctx.fillRect(x, by0 + 2, segw, by1 - by0 - 1, 1);       // lit: a solid block on the floor
        else if (i === pk - 1) ctx.fillRect(x, by0, segw, by1 - by0 + 1, 1); // the held peak: taller, alone
        else ctx.fillRect(x, by1, segw, 1, 1);                               // unlit: the floor line
    }
}

function drawFocus(ctx, st) {
    const p = FR_PAGES[st.page];
    const k = p.knobs[st.focus];
    header(ctx, p.title, "K" + (st.focus + 1));
    let label = k.name;
    if (k.type === "preset" && st.presetName) {           // the preset: its name above, its number large
        const nm = String(st.presetName), sp = nm.indexOf(" ");
        if (sp > 0) label = nm.slice(sp + 1);
    }
    if (textW(label, 2) <= 124) text(ctx, 2, 11, label, 2);
    else text(ctx, 2, 14, label, 1);
    const flash = st.flash && st.flash.key === k.key && nowOf(ctx) < st.flash.until;
    const val = flash ? k.options[1] : valueText(st, k);
    if (textW(val, 3) <= 124) textR(ctx, 125, 26, val, 3);
    else textR(ctx, 125, 30, val, 2);
    // the bar: 6 px (rows 49..54; two rows clear of the value above, three of the knob marks below)
    bar(ctx, st, k, 0, 49, 127, 54);
    for (let i = 0; i < p.knobs.length; i++) {
        const x = 2 + i * 16;
        if (i === st.focus) ctx.fillRect(x, 58, 12, 6, 1);
        else frame(ctx, x, 60, x + 11, 63);
    }
}

// the list's rows: the pages, then (when the ctx has them) SAVE TO USER and Schwung's trailing pages. A row:
// {kind: "page", idx} | {kind: "sep", label} (a divider, the cursor skips it) | {kind: "save"} | {kind: "act", label,
// action}. Schwung's menus are read once when the list opens (an IPC read, never per frame) and again after
// onPresetsChanged.
function listRows(ctx, st) {
    const rows = visiblePages(st.engine).map((idx) => ({ kind: "page", idx, label: FR_PAGES[idx].title }));
    if (typeof ctx.userSave === "function") {
        rows.push({ kind: "sep", label: "USER PRESETS" });
        rows.push({ kind: "save", label: "SAVE TO USER" });
    }
    if (st.menus === null && typeof ctx.menus === "function") {
        let m = [];
        try { m = ctx.menus() || []; } catch (e) { m = []; }
        st.menus = Array.isArray(m) ? m : [];
    }
    for (const pg of st.menus || []) {
        const es = (pg && pg.entries || []).filter((e) => e && e.action);
        if (!es.length) continue;
        rows.push({ kind: "sep", label: String(pg.name || "").toUpperCase() });
        for (const e of es) {
            const v = e.value !== undefined && e.value !== null ? String(e.value).toUpperCase() : "";
            rows.push({ kind: "act", label: String(e.label || "").toUpperCase(), value: v, action: e.action });
        }
    }
    return rows;
}
function rowStep(rows, i, d) {
    let j = i;
    for (;;) {
        const n = j + d;
        if (n < 0 || n >= rows.length) return i;
        j = n;
        if (rows[j].kind !== "sep") return j;
    }
}
function openList(ctx, st, at) {
    st.list = true;
    st.menus = null;
    st.rows = listRows(ctx, st);
    st.cursor = Math.max(0, st.rows.findIndex((r) => r.kind === "page" && r.idx === st.page));
    if (at) {
        const i = st.rows.findIndex((r) => r.kind === "sep" && r.label === at.toUpperCase());
        if (i >= 0) st.cursor = rowStep(st.rows, i, 1);
    }
}
function clickRow(ctx, st) {
    const r = st.rows[st.cursor];
    st.list = false;
    if (!r) return;
    if (r.kind === "page") { setPage(ctx, st, r.idx); return; }
    if (r.kind === "save") { openSave(ctx, st); return; }
    if (r.kind === "act" && typeof ctx.runAction === "function") {
        let opened = false;
        try { opened = !!ctx.runAction(r.action); } catch (e) { opened = false; }
        st.menus = null;                       // (Save / Save As change what the rows say)
        if (!opened) toast(ctx, st, r.label);
    }
}

function drawList(ctx, st) {
    const rows = st.rows || [];
    const sel = rows.filter((r) => r.kind !== "sep");
    header(ctx, "PAGES", (sel.indexOf(rows[st.cursor]) + 1) + "/" + sel.length);
    const n = 6, top = Math.max(0, Math.min(st.cursor - 2, rows.length - n));
    for (let r = 0; r < n && top + r < rows.length; r++) {
        const row = rows[top + r], y = 10 + r * 9;
        if (row.kind === "sep") {                       // a divider: the section's name between dotted rules
            const w = textW(row.label, 1);
            hdots(ctx, 0, 62 - (w >> 1) - 3, y + 3);
            text(ctx, 64 - (w >> 1), y, row.label);
            hdots(ctx, 64 + (w >> 1) + 3, 127, y + 3);
            continue;
        }
        const on = top + r === st.cursor;
        if (on) ctx.fillRect(0, y - 1, W, 9, 1);
        // a row's value (Schwung's: the loaded preset, the clip) on the right; the label cut to the room it leaves
        let lab = row.label;
        const val = row.value || "", room = 124 - (val ? textW(val, 1) + 6 : 0);
        while (lab.length > 1 && textW(lab, 1) > room) lab = lab.slice(0, -1);
        text(ctx, 2, y, lab, 1, on ? 0 : 1);
        if (val) textR(ctx, 125, y, val, 1, on ? 0 : 1);
        if (row.kind === "page" && row.idx === st.page) textR(ctx, 125, y, "*", 1, on ? 0 : 1);
    }
}

// ---- SAVE TO USER: a card picks U01-U16 (the jog or any knob), a click saves the sound there, Back leaves unsaved
function userNames(ctx) {
    const v = ctx.getParam("u_names");
    const out = v === null || v === undefined ? [] : String(v).split("|");
    while (out.length < 16) out.push("");
    return out.slice(0, 16);
}
function openSave(ctx, st) {
    const names = userNames(ctx);
    const nf = (st.presetCount || 35) - 16;               // (F01-F19, then U01-U16)
    let slot = st.preset !== null && st.preset >= nf ? st.preset - nf : names.indexOf("");
    st.save = { slot: Math.max(0, Math.min(15, slot)), names };
}
function saveStep(st, d) { st.save.slot = Math.max(0, Math.min(15, st.save.slot + Math.sign(d))); }
function saveNow(ctx, st) {
    const u = st.save.slot + 1;
    st.save = null;
    ctx.userSave(u);
    st.expectRev++;
    st.preset = (st.presetCount || 35) - 16 + u - 1;        // (the plugin loads the slot it saved)
    st.presetName = null;
    st.presetDirty = true;
    toast(ctx, st, "SAVED U" + (u < 10 ? "0" : "") + u);
}
function drawSave(ctx, st) {
    const u = st.save.slot + 1, nm = st.save.names[st.save.slot];
    header(ctx, "SAVE TO USER", u + "/16");
    text(ctx, 2, 13, "U" + (u < 10 ? "0" : "") + u, 3);
    const rest = nm ? nm.slice(nm.indexOf(" ") + 1) : "EMPTY";
    if (textW(rest, 2) <= 124) text(ctx, 2, 37, rest, 2); else text(ctx, 2, 40, rest, 1);
    hdots(ctx, 0, 127, 54);
    text(ctx, 2, 57, nm ? "CLICK: REPLACE" : "CLICK: SAVE");
    textR(ctx, 125, 57, "BACK: NO");
}

// a word in a box over the screen for a second (SAVED U05; a Schwung row that did its work in place)
function toast(ctx, st, msg) { st.toast = { text: String(msg), until: nowOf(ctx) + 1200 }; }
function drawToast(ctx, st) {
    if (!st.toast || nowOf(ctx) >= st.toast.until) { st.toast = null; return; }
    const w = Math.min(124, textW(st.toast.text, 1) + 8), x = (W - w) >> 1;
    ctx.fillRect(x - 1, 22, w + 2, 15, 0);
    frame(ctx, x, 23, x + w - 1, 35);
    text(ctx, x + 4, 26, st.toast.text);
}

function view(ctx, st) {
    if (st.save) return "save";
    if (st.list) return "list";
    if (st.focus >= 0 && (st.until === 0 || nowOf(ctx) < st.until)) return "focus";
    return "overview";
}

// ---- the hooks (Schwung's canvas overlay)

function newState() {
    return { page: 0, engine: 0, vals: {}, written: {}, lastTurn: {}, focus: -1, until: 0,
             touch: [false, false, false, false, false, false, false, false], list: false, cursor: 0,
             preset: null, presetName: null, presetCount: 0, presetDirty: false, rev: null, expectRev: 0,
             susOn: false, fxOn: null, flash: null, rr: 0, gr: null, rows: [], menus: null, save: null,
             toast: null, tapAt: [] };
}

globalThis.canvas_overlay = {
    onOpen(ctx) {
        const st = ctx.state.fr = newState();
        st.engine = asNum(ctx.getParam("ui_engine")) | 0;
        st.presetCount = asNum(ctx.getParam("preset_count")) | 0;
        st.presetName = ctx.getParam("preset_name");
        st.preset = asNum(ctx.getParam("preset"));
        st.rev = asNum(ctx.getParam("rev"));
        st.expectRev = st.rev === null ? 0 : st.rev;
        const fx = ctx.getParam("s_fx");
        st.fxOn = fx === null || fx === undefined ? null : fx === "1" || fx === "ON";
        st.page = visiblePages(st.engine)[0];
        readPage(ctx, st);
    },
    onMidi(ctx, msg) {
        const st = ctx.state.fr;
        if (!st) return false;
        const d = (msg && msg.data) || [];
        const type = (d[0] | 0) & 0xF0, d1 = d[1] | 0, d2 = d[2] | 0;
        if (st.save) {                                              // the save card owns the controls
            if (type === 0xB0 && (d1 === JOG_CC || (d1 >= KNOB_CC && d1 < KNOB_CC + 8))) { saveStep(st, rel(d2)); return true; }
            if (type === 0xB0 && d1 === CLICK_CC && d2 > 0) { saveNow(ctx, st); return true; }
            if (type === 0xB0 || type === 0x90 || type === 0x80) return true;
        }
        if (type === 0xB0 && d1 === FR_CONF.sus_cc) { if (d2 > 0) sustainBtn(ctx, st); return true; }
        if (type === 0xB0 && d1 === FR_CONF.fx_cc) { if (d2 > 0) fxBtn(ctx, st); return true; }
        if (type === 0x90 && d1 < 8) {                               // a knob's touch (velocity >= 64: down)
            if (st.list) return true;
            if (d2 >= 64) {
                st.touch[d1] = true;
                const t = nowOf(ctx), was = st.tapAt[d1];
                st.tapAt[d1] = t;
                if (was !== undefined && t - was < FR_CONF.tap_ms) { st.tapAt[d1] = undefined; reset(ctx, st, d1); }
                focus(ctx, st, d1, true);
            } else release(ctx, st, d1);
            return true;
        }
        if (type === 0x80 && d1 < 8) { release(ctx, st, d1); return true; }
        if (type !== 0xB0) return false;
        if (d1 >= KNOB_CC && d1 < KNOB_CC + 8) {
            if (st.list) return true;
            const slot = d1 - KNOB_CC;
            st.tapAt[slot] = undefined;                           // (a turn between two touches is no double tap)
            turn(ctx, st, slot, rel(d2));
            if (st.presetDirty) { st.presetName = ctx.getParam("preset_name"); st.presetDirty = st.presetName === null; }
            focus(ctx, st, slot, st.touch[slot]);
            return true;
        }
        if (d1 === JOG_CC) {
            const r = rel(d2);
            if (st.list) st.cursor = rowStep(st.rows, st.cursor, Math.sign(r));
            else pageStep(ctx, st, Math.sign(r));
            return true;
        }
        if (d1 === CLICK_CC && d2 > 0) {
            if (st.list) clickRow(ctx, st); else openList(ctx, st);
            return true;
        }
        if ((d1 === LEFT_CC || d1 === RIGHT_CC) && d2 > 0 && !st.list) {
            pageStep(ctx, st, d1 === LEFT_CC ? -1 : 1);
            return true;
        }
        return false;
    },
    handleBack(ctx) {
        const st = ctx.state.fr;
        if (st && st.save) { st.save = null; toast(ctx, st, "NOT SAVED"); return true; }   // the save card: unsaved
        if (st && st.list) { st.list = false; return true; }     // up: the list closes, the page stays
        return false;                                             // the top: the host closes the screen
    },
    // the host reloaded the screen after one of its own screens (Load, Delete, Swap, Help): back on the list there
    restore(ctx, name) { const st = ctx.state.fr; if (st) openList(ctx, st, name); },
    presetsChanged(ctx) { const st = ctx.state.fr; if (st && st.list) { st.menus = null; st.rows = listRows(ctx, st); } },
    onValues(ctx, p) {
        const st = ctx.state.fr;
        if (!st) return;
        const v = (p && p.values) || {};
        if (v.s_sustain !== undefined && v.s_sustain !== null && nowOf(ctx) - (st.written.s_sustain || 0) >= FR_CONF.settle_ms)
            st.susOn = v.s_sustain === "1" || v.s_sustain === "ON";
        if (v.s_fx !== undefined && v.s_fx !== null && nowOf(ctx) - (st.written.s_fx || 0) >= FR_CONF.settle_ms)
            st.fxOn = v.s_fx === "1" || v.s_fx === "ON";
        if (v.preset_name !== undefined && v.preset_name !== null) st.presetName = v.preset_name;
        const e = asNum(v.ui_engine);
        if (e !== null && (e | 0) !== st.engine) engineChanged(ctx, st, e | 0);
        const rev = asNum(v.rev);
        if (rev !== null) {
            if (rev > st.expectRev) {                             // a change we did not make: a preset, a state,
                st.vals = {};                                     // the host's own page: read the page again
                st.expectRev = rev;
                st.preset = asNum(ctx.getParam("preset"));
                readPage(ctx, st);
                return;
            }
        }
        if (isComp(st)) readGr(ctx, st);                       // the meter: every tick
        // one value a tick, round the page, so a change by MIDI CC (no rev) shows too; never one being turned
        const ks = knobs(st);
        if (!ks.length) return;
        st.rr = (st.rr + 1) % ks.length;
        const k = ks[st.rr];
        if (k.type === "preset" || k.write) return;
        if (nowOf(ctx) - (st.written[k.key] || 0) < FR_CONF.settle_ms) return;
        readKey(ctx, st, k);
    },
    draw(ctx) {
        ctx.clear();
        const st = ctx.state.fr;
        if (!st) return;
        const v = view(ctx, st);
        if (v === "save") drawSave(ctx, st);
        else if (v === "list") drawList(ctx, st);
        else if (v === "focus") drawFocus(ctx, st);
        else { st.focus = -1; drawOverview(ctx, st); }
        drawToast(ctx, st);
    },
};
// (for the host tests: the font's width and the view, not used on the device)
globalThis.frodhes_screen = { textW, view, visiblePages, valueText, grPx, grText, pages: FR_PAGES, conf: FR_CONF };
