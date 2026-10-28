#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// tools/move_ui_shots.mjs [--out DIR] [--scale K]: Frodhes' own screen on the Move (move/module/screen.js) drawn on
// the host, every page: its overview and the large card of each knob, the page list, for review and for the docs.
// The screen's own code runs (move/ui/harness.mjs), on a 128 x 64 one-bit framebuffer in its true colours (white on
// black). The values are the parameters' defaults (the FM-1's), the engine pages once per engine.
//   DIR/<page>.png            the overview (128 x 64)
//   DIR/<page>-k<N>.png       knob N's large card
//   DIR/pages.png             the page list
//   DIR/*-<K>x.png            the same K times larger (default 4; nearest neighbour)
//   DIR/sheet.png             every page in one picture: a row per page, the overview then the cards (2x)
// Default DIR: build/move/ui-shots. --docs also copies the pictures docs/MOVE.md shows (4x) to docs/manual/img/move/move-*.png and
// sets their links' ?v= to the pictures' hashes (as tools/doc_links.py does for the manual).
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { Screen, ROOT, W, H, png, grey } from "../move/ui/harness.mjs";

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d; };
const OUT = path.resolve(opt("--out", path.join(ROOT, "build", "move", "ui-shots")));
const K = Number(opt("--scale", "4"));
fs.mkdirSync(OUT, { recursive: true });

const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const save = (name, fb) => {
    fs.writeFileSync(path.join(OUT, name + ".png"), png(grey(fb, 1), W, H));
    if (K > 1) fs.writeFileSync(path.join(OUT, `${name}-${K}x.png`), png(grey(fb, K), W * K, H * K));
};
const snap = (fb) => ({ px: Uint8Array.from(fb.px) });

const rows = [];          // [[title, [frames]]]
const done = new Set();
const written = [];
for (const type of [0, 4, 5]) {                      // ST77 (the piano's pages), VIBE, MRMB
    const s = new Screen();
    s.plugin.v.p_type = type;
    s.plugin.preset = type === 0 ? 3 : type === 4 ? 12 : 15;
    s.open();
    const vis = s.api.visiblePages(s.st().engine);
    for (let n = 0; n < vis.length; n++) {
        const page = vis[n];
        while (s.st().page !== page) s.jog(s.st().page < page ? 1 : -1);
        const title = s.api.pages[page].title;
        if (done.has(title)) continue;
        done.add(title);
        const frames = [];
        s.wait(5000);
        save(slug(title), s.draw()); written.push(slug(title));
        frames.push(snap(s.fb));
        const nk = s.api.pages[page].knobs.length;
        for (let k = 0; k < nk; k++) {
            s.touch(k, true);
            save(`${slug(title)}-k${k + 1}`, s.draw()); written.push(`${slug(title)}-k${k + 1}`);
            frames.push(snap(s.fb));
            s.touch(k, false);
            s.wait(5000);
        }
        rows.push([title, frames]);
    }
}
{   // COMPRESSOR with the compressor on and the meter reading (12.0 dB, the peak held at 20.0 dB)
    const s = new Screen();
    s.plugin.v.p_cmp_sw = 1;
    s.plugin.gr = "120 200";
    s.open();
    const page = s.api.pages.findIndex((p) => p.title === "COMPRESSOR");
    while (s.st().page !== page) s.jog(s.st().page < page ? 1 : -1);
    s.live(); s.wait(5000); s.live();
    save("compressor-gr", s.draw()); written.push("compressor-gr");
}
{   // the page list: opened on the first page, the cursor moved three pages on
    const s = new Screen().open();
    s.click(); s.jog(1); s.jog(1); s.jog(1);
    save("pages", s.draw()); written.push("pages");
}

// the sheet: 2x, a 4 px grey gap between the screens (the gap is not part of any screen)
const SK = 2, GAP = 4, cols = 1 + Math.max(...rows.map((r) => r[1].length - 1));
const SW = cols * (W * SK + GAP) + GAP, SH = rows.length * (H * SK + GAP) + GAP;
const sheet = new Uint8Array(SW * SH).fill(96);
rows.forEach(([, frames], r) => frames.forEach((f, c) => {
    const ox = GAP + c * (W * SK + GAP), oy = GAP + r * (H * SK + GAP);
    for (let y = 0; y < H * SK; y++)
        for (let x = 0; x < W * SK; x++)
            sheet[(oy + y) * SW + ox + x] = f.px[Math.floor(y / SK) * W + Math.floor(x / SK)] ? 255 : 0;
}));
fs.writeFileSync(path.join(OUT, "sheet.png"), png(sheet, SW, SH));
if (args.includes("--docs")) {
    // (a folder of their own: docs/manual/img/ is tools/manual_shots.py's, test_manual_shots.py checks it whole)
    const DOCS = path.join(ROOT, "docs", "manual", "img", "move");
    fs.mkdirSync(DOCS, { recursive: true });
    for (const n of ["frodhes", "drive-filter", "drive-filter-k2", "reverb-k1", "pages", "compressor-gr"]) {
        const src = path.join(OUT, K === 4 ? `${n}-4x.png` : `${n}.png`);
        fs.copyFileSync(src, path.join(DOCS, `move-${n}.png`));
    }
    // the links' ?v= = the picture's sha256, first 8 hex digits (tools/doc_links.py's rule, for docs/MOVE.md)
    const md = path.join(ROOT, "docs", "MOVE.md");
    const text = fs.readFileSync(md, "utf8").replace(/\((manual\/img\/move\/move-[\w-]+\.png)(?:\?v=[0-9a-f]*)?\)/g, (m, rel) =>
        `(${rel}?v=${crypto.createHash("sha256").update(fs.readFileSync(path.join(ROOT, "docs", rel))).digest("hex").slice(0, 8)})`);
    fs.writeFileSync(md, text);
}
console.log(`${written.length} screens + sheet.png (${rows.length} pages) in ${path.relative(process.cwd(), OUT) || "."}`);
