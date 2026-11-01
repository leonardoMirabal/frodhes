// SPDX-License-Identifier: GPL-3.0-only
// node move/ui/test_screen.mjs: Frodhes' own screen on the Move (move/module/screen.js, option F) on the host:
// the overview and the large card (touch, turn, the return after FR_CONF.hold_ms), the knob arithmetic and what is
// written, the pages (jog, the list, Back, the VOICE pages following TYPE), the sustain button, the reads (none on
// the draw path; a change from elsewhere re-read), and that every label and value fits its place on the screen.
// Exit 0 when every check passes; run by tests/py/test_move_ui.py.
import assert from "node:assert/strict";
import { Screen, Plugin } from "./harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const HOLD = new Screen().api.conf.hold_ms;
const title = (s) => s.api.pages[s.st().page].title;
const goto = (s, t) => {
    for (let i = 0; i < 30 && title(s) !== t; i++) s.jog(1);
    for (let i = 0; i < 30 && title(s) !== t; i++) s.jog(-1);
    assert.equal(title(s), t);
};
const lastWrite = (s) => s.plugin.writes[s.plugin.writes.length - 1];

test("opens on the first page, the overview, with the page's values read", () => {
    const s = new Screen().open();
    assert.equal(title(s), "FRODHES");
    assert.equal(s.api.view(s.ctx(true), s.st()), "overview");
    assert.ok(s.plugin.reads <= 13, `reads on open: ${s.plugin.reads}`);   // (13: the first page's DELAY, 2026-10-30)
    const fb = s.draw();
    assert.equal(fb.prints, 0, "the device font is not used: our font only");
    assert.ok(fb.lit() > 300);
});

test("a touch shows the large card; it stays while held and hold_ms after the release", () => {
    const s = new Screen().open();
    goto(s, "DRIVE/FILTER");
    s.touch(1);
    assert.equal(s.api.view(s.ctx(true), s.st()), "focus");
    assert.equal(s.st().focus, 1);
    s.wait(10000);
    assert.equal(s.api.view(s.ctx(true), s.st()), "focus", "held: no deadline");
    s.touch(1, false);
    s.wait(HOLD - 50);
    assert.equal(s.api.view(s.ctx(true), s.st()), "focus");
    s.wait(100);
    assert.equal(s.api.view(s.ctx(true), s.st()), "overview");
    assert.ok(HOLD >= 300 && HOLD <= 1500, "about half a second (the user, 2026-10-29: 1 s stayed too long)");
});

test("a turn without a touch shows the card until hold_ms after the last detent", () => {
    const s = new Screen().open();
    goto(s, "REVERB");
    s.knob(2, 1);
    assert.equal(s.st().focus, 2);
    s.wait(HOLD - 100); s.knob(2, 1); s.wait(HOLD - 100);
    assert.equal(s.api.view(s.ctx(true), s.st()), "focus");
    s.wait(200);
    assert.equal(s.api.view(s.ctx(true), s.st()), "overview");
});

test("another knob switches the card at once; two held: the card follows the last", () => {
    const s = new Screen().open();
    goto(s, "DRIVE/FILTER");
    s.touch(0); s.touch(5);
    assert.equal(s.st().focus, 5);
    s.touch(5, false);
    assert.equal(s.st().focus, 0, "the other knob is still held: its card");
    s.knob(3, 1);
    assert.equal(s.st().focus, 3);
});

test("turning writes the wire value: numbers step and clamp, enums by index, a fast turn moves faster", () => {
    const s = new Screen().open();
    goto(s, "DRIVE/FILTER");
    const amt = s.plugin.v.p_drive;
    s.knob(1, 1); s.wait(200);
    assert.deepEqual(lastWrite(s), ["p_drive", String(amt + 1)]);
    s.knob(1, -1); s.wait(200); s.knob(1, -1); s.wait(200);
    assert.equal(s.plugin.v.p_drive, Math.max(0, amt - 1));
    for (let i = 0; i < 200; i++) { s.knob(1, -1); s.wait(200); }
    assert.equal(s.plugin.v.p_drive, 0, "clamped at the minimum");
    s.knob(1, 1); s.wait(10); s.knob(1, 1);
    assert.equal(s.plugin.v.p_drive, 1 + s.api.conf.fast_mul, "a fast turn of a wide range");
    const n = s.api.pages[s.st().page].knobs[4].options.length;
    for (let i = 0; i < n + 3; i++) { s.knob(4, 1); s.wait(200); }
    assert.equal(s.plugin.v.p_fl_type, n - 1, "an enum stops at its last option");
    assert.match(lastWrite(s)[1], /^\d+$/);
});

test("what the Move owns is not on the Move: no sequencer, chords, scales, BPM or CLOCK (the arpeggiator is)", () => {
    const s = new Screen().open();
    const titles = s.api.pages.map((p) => p.title);
    for (const t of ["SEQUENCER", "CHORD/ARP", "CHORD", "TEMPO/SCALE"]) assert.ok(!titles.includes(t), titles.join(","));
    const keys = s.api.pages.flatMap((p) => p.knobs.filter((k) => k).map((k) => k.key));
    const gone = ["s_sq", "s_ch", "s_bpm", "s_clock", "s_scmode", "s_scale", "s_root", "s_trans"];
    assert.ok(!keys.some((k) => gone.some((g) => k.startsWith(g))), keys.join(","));
    goto(s, "TEMPO");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.name).join(" "), "TRSYNC PHSYNC DLSYNC");
    goto(s, "LFO/SETUP");
    assert.ok(!s.api.pages[s.st().page].knobs.some((k) => k.name === "CLOCK"));
});

test("the jog pages through the pages and stops at the ends", () => {
    const s = new Screen().open();
    s.jog(-1);
    assert.equal(title(s), "FRODHES");
    s.jog(1);
    assert.equal(title(s), "PIANO VOICE");
    for (let i = 0; i < 40; i++) s.jog(1);
    assert.equal(title(s), "MAPPINGS");
});

test("the VOICE pages follow TYPE (a knob here, or elsewhere)", () => {
    const s = new Screen().open();
    assert.equal(s.api.visiblePages(0).length, 12);                  // (CHORUS and DELAY apart, ARPEGGIO: 2026-10-30)
    assert.equal(s.api.visiblePages(1).length, 13);
    assert.equal(s.api.visiblePages(2).length, 12);
    for (let i = 0; i < 4; i++) { s.knob(0, 1); s.wait(200); }        // TYPE ST77 -> VIBE
    s.live();
    assert.equal(s.st().engine, 1);
    goto(s, "VIBES MOTOR");
    s.plugin.external("p_type", 5);                                  // MRMB, by MIDI or the host's page
    s.live();
    assert.equal(title(s), "MARIMBA VOICE", "the gone engine's page: the new engine's VOICE page");
});

test("the page list: a click opens it, the jog picks, a click opens the page, Back closes it", () => {
    const s = new Screen().open();
    s.click();
    assert.equal(s.api.view(s.ctx(true), s.st()), "list");
    s.jog(1); s.jog(1); s.jog(1);
    s.knob(0, 1);
    assert.equal(s.plugin.writes.length, 0, "the knobs do nothing in the list");
    s.click();
    assert.equal(s.api.view(s.ctx(true), s.st()), "overview");
    assert.equal(title(s), "DRIVE/FILTER");
    s.click();
    assert.equal(s.back(), true, "Back in the list: up one (stays in the screen)");
    assert.equal(title(s), "DRIVE/FILTER");
    assert.equal(s.back(), false, "Back on a page: the host closes the screen");
});

test("the Left arrow turns FX off / on: a press writes the other state (s_fx), a release nothing; Right is SUS (63), Left FX (62)", () => {
    const s = new Screen().open();
    assert.equal(s.api.conf.sus_cc, 63);
    assert.equal(s.api.conf.fx_cc, 62);
    goto(s, "REVERB");
    const n0 = s.plugin.writes.length;
    assert.equal(s.st().fxOn, true, "FX read on open");
    assert.equal(s.midi(0xB0, s.api.conf.fx_cc, 127), true);
    assert.deepEqual(lastWrite(s), ["s_fx", "0"]);
    assert.equal(s.midi(0xB0, s.api.conf.fx_cc, 0), true);
    assert.equal(s.plugin.writes.length, n0 + 1, "the release writes nothing");
    s.plugin.apply();
    assert.equal(s.plugin.v.s_fx, 0);
    s.midi(0xB0, s.api.conf.fx_cc, 127); s.midi(0xB0, s.api.conf.fx_cc, 0);
    assert.deepEqual(lastWrite(s), ["s_fx", "1"]);
    s.plugin.apply();
    assert.equal(s.plugin.v.s_fx, 1);
    assert.equal(s.plugin.writes.filter((w) => w[0] === "s_fxbtn").length, 0, "no flip once FX is known");
});

test("an arrow's write delivered twice or replayed lands on the same state (the user, 2026-10-30: FX came back on by itself)", () => {
    const s = new Screen().open();
    goto(s, "REVERB");
    s.wait(1000); s.live();                          // the first live read: SUS known
    s.midi(0xB0, s.api.conf.fx_cc, 127); s.midi(0xB0, s.api.conf.fx_cc, 0);
    s.midi(0xB0, s.api.conf.sus_cc, 127); s.midi(0xB0, s.api.conf.sus_cc, 0);
    const fx = s.plugin.writes.filter((w) => w[0] === "s_fx").pop(), sus = s.plugin.writes.filter((w) => w[0] === "s_sustain").pop();
    assert.deepEqual([fx, sus], [["s_fx", "0"], ["s_sustain", "1"]]);
    s.plugin.apply();
    for (let i = 0; i < 3; i++) { s.plugin.queue.push(fx, sus); s.plugin.apply(); }   // the host sends them again
    assert.equal(s.plugin.v.s_fx, 0, "FX stays off");
    assert.equal(s.plugin.v.s_sustain, 1, "SUS stays on");
    for (let i = 0; i < 8; i++) { s.wait(300); s.live(); }
    assert.equal(s.st().fxOn, false);
    assert.equal(s.st().susOn, true);
});

test("the claimed button turns SUS on or off on every page: a press writes the other state, a release sends nothing", () => {
    const s = new Screen().open();
    goto(s, "REVERB");
    const n0 = s.plugin.writes.length;
    assert.equal(s.midi(0xB0, s.api.conf.sus_cc, 127), true);
    assert.deepEqual(lastWrite(s), ["s_susbtn", "1"], "before the first read of SUS: the plugin's flip");
    assert.equal(s.st().susOn, true, "the S mark at once");
    assert.equal(s.midi(0xB0, s.api.conf.sus_cc, 0), true);
    assert.equal(s.plugin.writes.length, n0 + 1, "the release writes nothing");
    s.plugin.apply();
    assert.equal(s.plugin.v.s_sustain, 1);
    s.wait(1000); s.live();
    assert.equal(s.st().susKnown, true);
    s.midi(0xB0, s.api.conf.sus_cc, 127); s.midi(0xB0, s.api.conf.sus_cc, 0);
    assert.equal(s.st().susOn, false);
    assert.deepEqual(lastWrite(s), ["s_sustain", "0"]);
    s.plugin.apply();
    assert.equal(s.plugin.v.s_sustain, 0);
    assert.equal(s.plugin.writes.filter((w) => w[0] === "s_susbtn" && w[1] !== "1").length, 0);
    assert.equal(typeof s.ov.onClose, "undefined", "nothing to let go when the screen closes");
    s.wait(1000);
    s.plugin.v.s_sustain = 1;                        // SUS turned on elsewhere (the knob grid's SUS)
    for (let i = 0; i < 4; i++) s.live();
    assert.equal(s.st().susOn, true);
});

test("no read on the draw path; a change from elsewhere is read again, a MIDI CC within a page round", () => {
    const s = new Screen().open();
    goto(s, "DRIVE/FILTER");
    const r0 = s.plugin.reads;
    for (let i = 0; i < 50; i++) { s.wait(16); s.draw(); }
    assert.equal(s.plugin.reads, r0);
    s.plugin.external("p_drive", 99);                                 // a preset load, the host's grid: rev
    s.live();
    assert.equal(s.st().vals.p_drive, 99);
    s.plugin.midiCC("p_fl_reso", 77);                                 // a CC from a keyboard: no rev
    for (let i = 0; i < 8; i++) { s.wait(300); s.live(); }
    assert.equal(s.st().vals.p_fl_reso, 77);
    const r1 = s.plugin.reads;
    s.live();
    assert.ok(s.plugin.reads - r1 <= 5, "a live tick: the four keys and one value");
});

test("a value being turned is not overwritten by a late read", () => {
    const s = new Screen().open();
    goto(s, "DRIVE/FILTER");
    s.knob(5, -1);
    const want = s.st().vals.p_fl_freq;
    assert.equal(want, s.plugin.v.p_fl_freq);
    s.plugin.queue.push(["p_fl_freq", "3"]);                          // (a stale value, as a queued write would be)
    s.plugin.apply();
    s.plugin.rev--;                                                    // (not a change of someone else's)
    for (let i = 0; i < 8; i++) s.live();
    assert.equal(s.st().vals.p_fl_freq, want);
});

test("every page, label and option fits: card labels 4 characters (a longer label: its short form), the large card 124 px", () => {
    const s = new Screen().open();
    const w = s.api.textW;
    for (const p of s.api.pages) {
        assert.ok(p.knobs.length >= 1 && p.knobs.length <= 8, p.title);
        assert.ok(w(p.title + " S 12/12") <= 126, p.title);
        p.knobs.forEach((k) => {
            if (!k) return;                                            // (a knob with no control)
            if (k.type !== "preset") assert.ok(w(k.short || k.name) <= 31, `${p.title} ${k.short || k.name}`);
            assert.ok(w(k.name, 2) <= 124, k.name);
            const vals = k.type === "enum" ? k.options
                : k.type === "preset" ? [] : [String(k.min), String(k.max), k.step ? k.max.toFixed(1) : ""];
            for (const v of vals) {
                assert.ok(w(v) <= 31, `${p.title} ${k.name} ${v}: ${w(v)} px in a 32 px card`);
                assert.ok(w(v, 3) <= 124 || w(v, 2) <= 124, `${k.name} ${v} on the large card`);
            }
        });
    }
    const pl = new Plugin();
    for (const nm of pl.names) assert.ok(w(nm) <= 124, nm);
});

test("every page draws in both views without throwing, on every engine", () => {
    for (const type of [0, 4, 5]) {
        const s = new Screen();
        s.plugin.v.p_type = type;
        s.open();
        for (const i of s.api.visiblePages(s.st().engine)) {
            goto(s, s.api.pages[i].title);
            assert.ok(s.draw().lit() > 200);
            for (let k = 0; k < s.api.pages[i].knobs.length; k++) {
                if (!s.api.pages[i].knobs[k]) {                        // a knob with no control: no card
                    s.touch(k); assert.equal(s.api.view(s.ctx(true), s.st()), "overview"); s.touch(k, false);
                    s.knob(k, 1); assert.equal(s.api.view(s.ctx(true), s.st()), "overview"); continue;
                }
                s.touch(k); assert.ok(s.draw().lit() > 200); s.touch(k, false); s.wait(HOLD + 10);
            }
        }
    }
});

test("the first page's DELAY (the delay's MIX) on knob 8; knobs 6 and 7 free under the PRESET card's name (2026-10-30)", () => {
    const s = new Screen().open();
    const root = s.api.pages[s.st().page];
    assert.equal(JSON.stringify(root.knobs.map((k) => k && k.key)),
                 JSON.stringify(["p_type", "p_spread", "s_sustain", "s_fx", "preset", null, null, "p_delay"]));
    const d = root.knobs[7];
    assert.equal(d.name, "DELAY");
    assert.equal(d.type, "int"); assert.equal(d.min, 0); assert.equal(d.max, 127);
    assert.ok(!d.options, "a plain bar (no notches: not bipolar, not an enum)");
    s.plugin.external("p_delay", 40); s.plugin.v.p_dl_depth = 40; s.live();
    s.knob(7, 1); s.wait(200);
    assert.deepEqual(lastWrite(s), ["p_delay", "41"]);
    assert.equal(s.st().focus, 7);
    const fb = s.draw();
    assert.ok(fb.lit() > 200);
    s.wait(HOLD + 10);
    // the overview: the PRESET card's bar ends before knob 8's card; knob 8's card has its label
    const ov = s.draw(), px = (x, y) => ov.px[y * 128 + x];
    let barEnd = -1;
    for (let x = 0; x < 128; x++) if (px(x, 37 + 19)) barEnd = x;   // (the second row's bar outline, y 9 + 28 + 19)
    assert.ok(barEnd >= 96 && barEnd <= 124, `knob 8's bar on the right (${barEnd})`);
    let gap = 0;
    for (let x = 93; x <= 95; x++) gap += px(x, 37 + 19) ? 1 : 0;
    assert.equal(gap, 0, "the PRESET card's bar ends at x 92, before knob 8's card at x 96");
    // a knob with no control: no card, no write
    const n = s.plugin.writes.length;
    s.touch(5); assert.equal(s.api.view(s.ctx(true), s.st()), "overview"); s.touch(5, false);
    s.knob(6, 1); s.wait(200);
    assert.equal(s.plugin.writes.length, n, "knobs 6 and 7 write nothing");
    // the longest names fit before knob 8's card: a user slot's 16 characters
    assert.ok(s.api.textW("U16 ABCDEFGHIJKL") <= 95);
});

test("CHORUS and DELAY are two pages, each with the FM-1 page's cards in the FM-1's order (2026-10-30)", () => {
    const s = new Screen().open();
    goto(s, "CHORUS");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.key).join(" "), "p_cp_sw p_cp_depth p_cp_speed p_cp_sync");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.name).join(" "), "CHORUS DEPTH SPEED SYNC");
    goto(s, "DELAY");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.key).join(" "), "p_dl_sw p_dl_depth p_dl_time p_dl_fdbk");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.name).join(" "), "DELAY MIX TIME FEEDBK");
    assert.equal(s.api.pages[s.st().page].knobs[0].options.join(" "), "OFF DIG ANA TAPE");
    s.knob(1, 1); s.wait(200);
    assert.equal(lastWrite(s)[0], "p_dl_depth");
    const titles = s.api.pages.map((p) => p.title);
    assert.ok(!titles.includes("CHORUS/DELAY"));
    assert.ok(titles.indexOf("CHORUS") + 1 === titles.indexOf("DELAY") && titles.indexOf("DELAY") + 1 === titles.indexOf("REVERB"),
              titles.join(","));
});

test("ARPEGGIO: the FM-1 page's cards (MODE RATE OCT GATE), after TEMPO; writes go by index / as shown (2026-10-30)", () => {
    const s = new Screen().open();
    goto(s, "ARPEGGIO");
    const p = s.api.pages[s.st().page];
    assert.equal(p.knobs.map((k) => k.key).join(" "), "s_arpmode s_arprate s_arpoct s_arpgate");
    assert.equal(p.knobs.map((k) => k.name).join(" "), "MODE RATE OCT GATE");
    assert.equal(p.knobs[0].options.join(" "), "OFF UP DOWN UP-DN ORDER RAND");
    assert.equal(p.knobs[1].options.join(" "), "1/4 1/8 1/8T 1/16 1/16T 1/32");
    assert.equal(p.knobs[2].min, 1); assert.equal(p.knobs[2].max, 4);
    assert.equal(p.knobs[3].min, 10); assert.equal(p.knobs[3].max, 100);
    s.knob(0, 1); s.wait(200);
    assert.deepEqual(lastWrite(s), ["s_arpmode", "1"]);
    const titles = s.api.pages.map((x) => x.title);
    assert.equal(titles.indexOf("ARPEGGIO"), titles.indexOf("TEMPO") + 1, titles.join(","));
});

test("REVERB INF: DECAY reads SUSTAIN from 120 and FREEZE at 127, as the FM-1's card (ui.c); a number otherwise", () => {
    const s = new Screen().open();
    goto(s, "REVERB");
    const p = s.api.pages[s.st().page], dk = p.knobs.find((k) => k.key === "p_rv_decay");
    const r = s.api.conf.rv;
    assert.equal(JSON.stringify([r.inf, r.sustain_at, r.freeze_at, r.words]), JSON.stringify([3, 120, 127, ["SUSTAIN", "FREEZE"]]),
                 "ui.c RV_INF, RV_SUSTAIN_AT, RV_FREEZE_AT and its words");
    assert.equal(p.knobs[0].options[r.inf], "INF");
    const st = s.st(), vt = (v, card) => { st.vals.p_rv_decay = v; return s.api.valueText(st, dk, card); };
    st.vals.p_rv_type = r.inf;
    assert.equal(vt(119), "119");
    assert.equal(vt(120), "SUSTAIN"); assert.equal(vt(126), "SUSTAIN"); assert.equal(vt(127), "FREEZE");
    assert.equal(vt(120, true), "SUST"); assert.equal(vt(127, true), "FREZ");
    for (const w of ["SUST", "FREZ"]) assert.ok(s.api.textW(w) <= 31, w);
    st.vals.p_rv_type = 1;                                                    // HALL: the number
    assert.equal(vt(127), "127");
    // drawn: the overview's card and the large card
    s.plugin.external("p_rv_type", r.inf); s.plugin.external("p_rv_decay", 127); s.live();
    for (let i = 0; i < 20; i++) s.live();
    assert.ok(s.draw().lit() > 200);
    s.touch(2); assert.equal(s.st().focus, 2); assert.ok(s.draw().lit() > 200); s.touch(2, false);
});

let fail = 0;
test("COMPRESSOR: the gain-reduction meter (a fake r_cmp_gr), as the FM-1's COMP page shows it", () => {
    const s = new Screen().open();
    const W = 128, px = (fb, x, y) => fb.px[y * W + x];
    const lit = (fb, x0, x1, y0, y1) => { let n = 0; for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) n += px(fb, x, y) ? 1 : 0; return n; };
    // ui.c gr_px's arithmetic (GR_W 228), the same scale
    const sc = s.api.conf.gr_db10;
    assert.equal(JSON.stringify(sc), "[10,20,30,40,50,60,80,100,120,150,180,200,240,270,300]");
    const cpx = (d, w) => { let lo = 0; for (let i = 0; i < sc.length; i++) { if (d <= sc[i]) return Math.trunc((i * w + Math.trunc(w * (d - lo) / (sc[i] - lo))) / sc.length); lo = sc[i]; } return w; };
    for (const d of [0, 5, 10, 35, 60, 99, 150, 299, 300, 400]) assert.equal(s.api.grPx(d, 228), cpx(d, 228), `gr_px(${d})`);
    assert.equal(s.api.grText(0), "0.0");
    assert.equal(s.api.grText(35), "-3.5");
    // the bar: 15 segments 3 px wide, 4 apart, right-aligned (x 69..127), rows 48..62; segment i (0 = the rightmost)
    const segX = (i) => 125 - 4 * i, seg = (fb, i, y) => lit(fb, segX(i), segX(i) + 2, y, y);
    const segs = (d) => { if (!(d > 0)) return 0; for (let i = 0; i < sc.length; i++) if (d <= sc[i]) return i + 1; return sc.length; };
    // COMP off: an empty dotted outline and COMP OFF, nothing lit
    goto(s, "COMPRESSOR");
    assert.equal(JSON.stringify(s.st().gr), '{"off":true,"db10":0,"pk":0}');
    let fb = s.draw();
    for (let i = 0; i < 15; i++) assert.ok(seg(fb, i, 55) <= 1, `COMP off: segment ${i} not lit`);
    assert.ok(lit(fb, 69, 127, 48, 48) > 20 && lit(fb, 69, 127, 48, 48) < 40, "COMP off: the dotted outline");
    assert.ok(lit(fb, 80, 127, 39, 45) > 20, "COMP off: the hint");
    // on, 6.0 dB, the peak held at 10.0 dB: lit from the right, the peak a taller block beyond
    s.plugin.gr = "60 100";
    s.live();
    assert.equal(JSON.stringify(s.st().gr), '{"off":false,"db10":60,"pk":100}');
    fb = s.draw();
    const n = segs(60), pk = segs(100);
    assert.ok(n > 0 && pk > n + 1);
    for (let i = 0; i < 15; i++) {
        assert.equal(seg(fb, i, 55), i < n || i === pk - 1 ? 3 : 0, `segment ${i}`);
        assert.equal(seg(fb, i, 48), i === pk - 1 ? 3 : 0, `segment ${i}: the top row, the peak only`);
        assert.equal(seg(fb, i, 62), 3, `segment ${i}: the floor`);
        if (i < 14) assert.equal(px(fb, segX(i) - 1, 55), 0, "a gap between segments");
    }
    assert.ok(lit(fb, 80, 127, 39, 45) > 20, "the reading");
    // it follows each live tick; 0 dB: the floor only
    s.plugin.gr = "0 0";
    s.live();
    fb = s.draw();
    assert.equal(lit(fb, 69, 127, 48, 61), 0);
    assert.equal(lit(fb, 69, 127, 62, 62), 45, "the floor: 15 x 3 px");
    // on another page nothing of it is drawn, and r_cmp_gr is not read
    goto(s, "REVERB");
    const before = s.plugin.reads;
    s.plugin.gr = "80 80";
    s.live();
    assert.ok(s.plugin.reads - before <= 5);
    assert.equal(s.st().gr.db10, 0, "not read off the COMPRESSOR page");
});

// the overview card's bar of knob i, its middle row inside the outline: lit pixels as a string of # and .
const barRow = (s, i) => {
    const fb = s.draw(), x0 = (i % 4) * 32, y = 9 + Math.floor(i / 4) * 28 + 21;
    let r = "";
    for (let x = x0 + 1; x < x0 + 28; x++) r += fb.px[y * 128 + x] ? "#" : ".";
    return r;
};

test("a switch (an enum of two options) is an empty outline at its first option and full at its second", () => {
    const sw = [];
    for (const type of [0, 4]) {
        for (const v of [0, 1]) {
            const s = new Screen();
            s.plugin.v.p_type = type;
            for (const p of s.api.pages) for (const k of p.knobs) if (k && k.type === "enum" && k.options.length === 2) {
                s.plugin.v[k.key] = v;
            }
            s.open();
            for (const i of s.api.visiblePages(s.st().engine)) {
                goto(s, s.api.pages[i].title);
                s.api.pages[i].knobs.forEach((k, j) => {
                    if (!k || k.type !== "enum" || k.options.length !== 2) return;
                    if (s.api.pages[i].title === "COMPRESSOR" && j >= 6) return;
                    sw.push(k.key);
                    assert.equal(barRow(s, j), (v ? "#" : ".").repeat(27), `${s.api.pages[i].title} ${k.name} at ${k.options[v]}`);
                });
            }
        }
    }
    for (const key of ["s_sustain", "s_fx", "p_cmp_sw", "p_mot_on", "p_ph_sw", "p_cp_sw", "p_lfo_retrig"]) assert.ok(sw.includes(key), key);
});

test("an enum of three or more options and a bipolar number keep their bars (the step's share, the centre mark)", () => {
    const s = new Screen();
    s.plugin.v.p_type = 2;                                             // (TYPE: option 3 of its list)
    s.open();
    const t = s.api.pages[s.st().page].knobs[0], det = t.options.length, w = 29;
    assert.ok(det > 2);
    const fill = Math.round(w * 3 / det);
    let want = "";
    for (let x = 1; x < 28; x++) {
        const notch = det > 2 && w / det >= 4 && [...Array(det - 1).keys()].some((i) => Math.round(w * (i + 1) / det) === x);
        want += (x < fill) !== notch ? "#" : ".";
    }
    assert.equal(barRow(s, 0), want, "TYPE at its third option");
    const b = new Screen();
    b.plugin.v.p_tone = 20;
    b.open();
    goto(b, "PIANO VOICE");
    const k = b.api.pages[b.st().page].knobs[0];
    assert.ok(k.key === "p_tone" && k.min < 0 && k.max > 0, k.key);
    const f = Math.round(28 * (20 - k.min) / (k.max - k.min)) + 1, c = Math.round(28 * (-k.min) / (k.max - k.min));
    let wb = "";
    for (let x = 1; x < 28; x++) wb += (x < f) !== (x === c) ? "#" : ".";
    assert.equal(barRow(b, 0), wb, "TONE at +20, its centre marked");
});

test("every overview label fits its card with a gap: its width at most the card's less 1 px, every page and engine", () => {
    const s = new Screen().open();
    const w = s.api.textW;
    for (const p of s.api.pages) {                                     // (every engine's VOICE pages: all of them)
        p.knobs.forEach((k, i) => {
            if (!k) return;
            let cell = 29;                                             // (x .. x + 28, the bar's width; 32 px apart)
            if (k.type === "preset") {
                let j = i + 1;
                while (j % 4 && !p.knobs[j]) j++;
                cell = (j % 4 ? (j % 4) * 32 - 4 : 127) - (i % 4) * 32 - (j % 4 ? 0 : 1) + 1;
            }
            const lab = k.short || k.name;
            assert.ok(w(lab) <= cell - 1, `${p.title} ${lab}: ${w(lab)} px in a ${cell} px card`);
            assert.ok(!k.short || k.short.length >= 2, `${p.title} ${k.short}`);
        });
    }
});

for (const [name, fn] of tests) {
    try { fn(); console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n     " + e.message); }
}
console.log(fail ? `${fail} of ${tests.length} failed` : `all ${tests.length} passed`);
process.exit(fail ? 1 : 0);
