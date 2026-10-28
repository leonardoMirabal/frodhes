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
    assert.ok(s.plugin.reads <= 12, `reads on open: ${s.plugin.reads}`);
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
    assert.ok(HOLD >= 700 && HOLD <= 1500, "about 1 s");
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
    goto(s, "TEMPO/SCALE");
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

test("the tempo: 1 BPM a detent, 0.1 with Shift, one decimal on the wire", () => {
    const s = new Screen().open();
    goto(s, "TEMPO/SCALE");
    s.knob(0, 1); s.wait(200);
    assert.deepEqual(lastWrite(s), ["s_bpm", "121.0"]);
    s.shift = true; s.knob(0, 1); s.wait(200);
    assert.deepEqual(lastWrite(s), ["s_bpm", "121.1"]);
    s.shift = false;
    s.draw();
    assert.equal(s.api.valueText(s.st(), s.api.pages[s.st().page].knobs[0]), "121.1");
});

test("no sequencer and no arpeggiator on the Move (the Move has its own): CHORD keeps the chords only", () => {
    const s = new Screen().open();
    const titles = s.api.pages.map((p) => p.title);
    assert.ok(!titles.includes("SEQUENCER") && !titles.includes("CHORD/ARP"), titles.join(","));
    const keys = s.api.pages.flatMap((p) => p.knobs.map((k) => k.key));
    assert.ok(!keys.some((k) => k.startsWith("s_sq") || k.startsWith("s_arp")), keys.join(","));
    goto(s, "CHORD");
    assert.equal(s.api.pages[s.st().page].knobs.map((k) => k.name).join(" "), "CHORD INV VOICE STRUM");
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
    assert.equal(s.api.visiblePages(0).length, 11);
    assert.equal(s.api.visiblePages(1).length, 12);
    assert.equal(s.api.visiblePages(2).length, 11);
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

test("the claimed button is the sustain pedal on every page; the screen let go releases it", () => {
    const s = new Screen().open();
    goto(s, "REVERB");
    assert.equal(s.midi(0xB0, s.api.conf.sus_cc, 127), true);
    assert.deepEqual(lastWrite(s), ["s_susbtn", "1"]);
    s.midi(0xB0, s.api.conf.sus_cc, 0);
    assert.deepEqual(lastWrite(s), ["s_susbtn", "0"]);
    s.midi(0xB0, s.api.conf.sus_cc, 127);
    s.ov.onClose(s.ctx(false), {});
    assert.deepEqual(lastWrite(s), ["s_susbtn", "0"]);
    s.plugin.v.s_sustain = 1;
    s.live();
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

test("every page, label and option fits: cards 5 characters, the large card 124 px", () => {
    const s = new Screen().open();
    const w = s.api.textW;
    for (const p of s.api.pages) {
        assert.ok(p.knobs.length >= 1 && p.knobs.length <= 8, p.title);
        assert.ok(w(p.title + " S 12/12") <= 126, p.title);
        p.knobs.forEach((k) => {
            assert.ok(w(k.name) <= 31, `${p.title} ${k.name}`);
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
                s.touch(k); assert.ok(s.draw().lit() > 200); s.touch(k, false); s.wait(HOLD + 10);
            }
        }
    }
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
    // COMP off: the hint, no bar
    goto(s, "COMPRESSOR");
    assert.equal(JSON.stringify(s.st().gr), '{"off":true,"db10":0,"pk":0}');
    let fb = s.draw();
    assert.equal(lit(fb, 64, 127, 57, 62), 0, "COMP off: no bar");
    assert.ok(lit(fb, 64, 127, 48, 54) > 20, "COMP off: the hint");
    // on, 6.0 dB, the peak held at 10.0 dB: the bar filled from the right, the peak mark beyond it
    s.plugin.gr = "60 100";
    s.live();
    assert.equal(JSON.stringify(s.st().gr), '{"off":false,"db10":60,"pk":100}');
    fb = s.draw();
    const fw = s.api.grPx(60, 62), pk = s.api.grPx(100, 62);
    assert.ok(fw > 0 && pk > fw + 2);
    for (let x = 127 - fw; x < 127; x++) assert.ok(px(fb, x, 59), `filled at x ${x}`);
    assert.ok(!px(fb, 127 - fw - 1, 59), "the fill ends");
    assert.ok(px(fb, 127 - pk, 59) && px(fb, 127 - pk + 1, 59), "the peak");
    assert.ok(!px(fb, 127 - pk - 1, 59), "nothing left of the peak");
    // it follows each live tick; 0 dB: an empty bar inside its frame
    s.plugin.gr = "0 0";
    s.live();
    fb = s.draw();
    assert.equal(lit(fb, 65, 126, 58, 61), 0);
    assert.ok(lit(fb, 64, 127, 57, 62) > 100, "the frame");
    // on another page nothing of it is drawn, and r_cmp_gr is not read
    goto(s, "REVERB");
    const before = s.plugin.reads;
    s.plugin.gr = "80 80";
    s.live();
    assert.ok(s.plugin.reads - before <= 5);
    assert.equal(s.st().gr.db10, 0, "not read off the COMPRESSOR page");
});

for (const [name, fn] of tests) {
    try { fn(); console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n     " + e.message); }
}
console.log(fail ? `${fail} of ${tests.length} failed` : `all ${tests.length} passed`);
process.exit(fail ? 1 : 0);
