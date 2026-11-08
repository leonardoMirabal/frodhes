#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move-only factory presets (move/move_presets.h) from the factory preset review's records.

The review (tools/preset_review.py, AGENTS.md "The factory preset review") rendered every factory preset F01-F19 as
presets.c has it (A) and two variants of it (B, C). The user's pick (2026-11-01): the Move gets every variant, the
FM-1 keeps its presets as they are. So on the Move F01-F19 stay the A presets (presets.c, the FM-1's, untouched) and
the 38 variants follow as F20-F57, in the review's order (F01 B, F01 C, F02 B, ... F19 C). They live in their own
table, compiled into the Move build only (move/move_bridge.h includes the header; the FM-1 image never sees it), so
presets.c's PR_FDATA can change its format without touching this.

  tools/gen_move_presets.py           write move/move_presets.h
  tools/gen_move_presets.py --check   exit 1 if the committed header is not what the sources give

The sources (copies of the review's output, committed): move/presets/proposed_records.txt (every variant's full
id = value list, the defaults included, read back from the host build) and move/presets/variants.json (what each
variant changes). The check here: every B / C in variants.json has a record, and a record differs from its A only
in the ids its "set" names, P_LEVEL (the level solver) aside. The names are the Move's own (NAMES below): generic,
at most 12 characters (the FM-1's user preset name), no brand names.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "move", "presets")
RECORDS = os.path.join(SRC_DIR, "proposed_records.txt")
VARIANTS = os.path.join(SRC_DIR, "variants.json")
OUT = os.path.join(ROOT, "move", "move_presets.h")
FIRST = 20                                  # F20: the first Move-only preset (after presets.c's 19)
NAME_MAX = 12                               # presets.c preset_t name[12]: "F20 " + 12 = the 16 the screen shows

# (base preset, variant) -> (name, one line for docs/MOVE.md). The variant's character, in the FM-1's words.
NAMES = {
    ("F01", "B"): ("STAGE77 ECHO", "a soft dark analog echo on the quarter note, a little more hall"),
    ("F01", "C"): ("STAGE77 TAPE", "a tape echo on the dotted 1/8, a slightly bigger hall"),
    ("F02", "B"): ("SUITCASE DRY", "dry, a touch less cabinet noise, the level re-set"),
    ("F02", "C"): ("SUITCASE AMP", "the amp's warmth (BHLUE drive) and a short dark analog slap"),
    ("F03", "B"): ("FELT HALL", "dry, a bigger and softer hall"),
    ("F03", "C"): ("FELT ECHO", "the bigger hall and a soft dark analog echo on the quarter note"),
    ("F04", "B"): ("BELL SOFT", "dry, the top edge softer, a little more plate"),
    ("F04", "C"): ("BELL ECHO", "a short digital echo on the dotted 1/8, a little more plate"),
    ("F05", "B"): ("TREM ECHO", "a soft analog echo on the quarter note, the level re-set"),
    ("F05", "C"): ("TREM DEEP", "a slower, deeper vibrato, more warmth and plate"),
    ("F06", "B"): ("DRIVEN77 LVL", "as F06, the level re-set (half way between chord and phrase)"),
    ("F06", "C"): ("DRIVEN BODY", "more drive and body, a longer tape slap"),
    ("F07", "B"): ("BALLAD TIGHT", "less boom in the long plate's tail (LOW CUT 80)"),
    ("F07", "C"): ("BALLAD DARK", "a longer, darker analog echo tail, a slower chorus"),
    ("F08", "B"): ("DYNO80S LVL", "as F08, the level re-set"),
    ("F08", "C"): ("DYNO80S SOFT", "the echo softer and darker, less chorus"),
    ("F09", "B"): ("PHASER LVL", "as F09, the level re-set"),
    ("F09", "C"): ("PHASER SLOW", "a slower, more liquid sweep, a longer analog echo"),
    ("F10", "B"): ("FUNK WAH LVL", "as F10, the level re-set"),
    ("F10", "C"): ("FUNK PLUCK", "less squash, more pluck (2:1, slower attack), a short dark slap"),
    ("F11", "B"): ("RESO SPACE", "more echo and hall"),
    ("F11", "C"): ("RESO SMOOTH", "a smoother peak (less resonance), a wider sweep"),
    ("F12", "B"): ("AUTOWAH ECHO", "a digital echo on the quarter note"),
    ("F12", "C"): ("AUTOWAH TAPE", "a tape echo on the dotted 1/8 and a plate"),
    ("F13", "B"): ("VIBES HALL", "dry, a little more hall"),
    ("F13", "C"): ("VIBES MOTOR", "the slower, deeper motor, more hall"),
    ("F14", "B"): ("VIBES CLEAN", "a cleaner echo (fewer repeats)"),
    ("F14", "C"): ("VIBES DRIFT", "the motor on, slow and shallow, under the echo"),
    ("F15", "B"): ("MARIMBA HALL", "dry, more hall and pre-delay"),
    ("F15", "C"): ("MARIMBA ECHO", "a soft analog echo on the quarter note"),
    ("F16", "B"): ("WOOD PLAIN", "the chorus off"),
    ("F16", "C"): ("WOOD LIGHT", "a lighter chorus, less echo and hall"),
    ("F17", "B"): ("TAPE WIDE", "tamer repeats and a touch of chorus"),
    ("F17", "C"): ("TAPE DARK", "the echo on the quarter note, darker repeats"),
    ("F18", "B"): ("DREAM CLEAR", "less chorus wobble under the hall"),
    ("F18", "C"): ("DREAM DEEP", "a slower, deeper pan, the echo on the dotted 1/4"),
    ("F19", "B"): ("RADIO LVL", "as F19, the level re-set"),
    ("F19", "C"): ("RADIO LOW", "the band a little lower: more body"),
}
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "py"))
from test_repo_hygiene import BRANDS as HYGIENE_BRANDS  # noqa: E402  (the repo's banned names, one list)
OTHER_NAMES = re.compile(r"RHODES|WURLI|ROLAND|KORG|MOVE|DX7|CP70", re.I)   # product names kept out of preset names


def has_brand(n):
    return bool(HYGIENE_BRANDS.search(n) or OTHER_NAMES.search(n))


NAME_CHARS = re.compile(r"^[A-Z0-9][A-Z0-9 \-]*[A-Z0-9]$")
LINE = re.compile(r"^(F\d\d) ([A-Z]) \(([^)]*)\): (.*)$")
PAIR = re.compile(r"^(P_\w+)\[(\d+)\]=(\d+)$")


def records():
    """{(F, V): (base name, [(name, id, value), ...])} in the file's order"""
    out = {}
    with open(RECORDS) as f:
        for ln in f:
            m = LINE.match(ln.strip())
            if not m:
                continue
            pairs = []
            for tok in m.group(4).split():
                p = PAIR.match(tok)
                if not p:
                    raise SystemExit("%s: bad pair %r in %s %s" % (RECORDS, tok, m.group(1), m.group(2)))
                pairs.append((p.group(1), int(p.group(2)), int(p.group(3))))
            ids = [i for _, i, _ in pairs]
            if ids != list(range(len(ids))):
                raise SystemExit("%s %s: the ids are not 0..%d in order" % (m.group(1), m.group(2), len(ids) - 1))
            out[(m.group(1), m.group(2))] = (m.group(3), pairs)
    return out


NOT_IN_RECORDS = ("P_EQ_",)                 # parameters the records predate: the EQ (P_EQ_*, added after the review;
# the Move presets load it at its defaults, flat); any other parameter missing from a record stops the tool


def check_params(rec):
    """every record against params.c as it is now (tools/gen_move_module.py's parse): the same parameters at the same
    ids, every value within the parameter's range. A parameter added, renumbered or given a smaller range (a sound
    change: the 0.9.10 engine work) stops the generator here, before a Move preset could load a value the parameter
    no longer has; then update proposed_records.txt (the new parameter at its default, or the value re-set) and rerun"""
    import gen_move_module as gm
    params = gm.model()["params"]
    want = [n for n in sorted(params, key=lambda n: params[n]["id"]) if not n.startswith(NOT_IN_RECORDS)]
    for (base, v), (_, pairs) in rec.items():
        names = [n for n, _, _ in pairs]
        if names != want:
            odd = sorted(set(names) ^ set(want)) or [next(a for a, b in zip(names, want) if a != b) + " moved"]
            raise SystemExit("%s %s: the record's parameters are not params.c's (%s); update %s" % (
                base, v, ", ".join(odd), RECORDS))
        for n, i, x in pairs:
            if i != params[n]["id"]:
                raise SystemExit("%s %s: %s is id %d in the record, %d in params.c" % (base, v, n, i, params[n]["id"]))
            if not 0 <= x <= params[n]["hi"]:
                raise SystemExit("%s %s: %s is %d, over its range 0..%d in params.c; update %s" % (
                    base, v, n, x, params[n]["hi"], RECORDS))


def table():
    """the Move-only presets in order: [(number, name, line, base, variant, pairs)]"""
    rec = records()
    check_params(rec)
    with open(VARIANTS) as f:
        var = json.load(f)
    rows = []
    for base in sorted(var):
        if (base, "A") not in rec:
            raise SystemExit("no A record for %s" % base)
        a = {n: v for n, _, v in rec[(base, "A")][1]}
        for v in sorted(k for k in var[base] if re.fullmatch(r"[B-Z]", k)):
            if (base, v) not in rec:
                raise SystemExit("variants.json has %s %s, proposed_records.txt has no record of it" % (base, v))
            if (base, v) not in NAMES:
                raise SystemExit("%s %s has no name: add it to NAMES" % (base, v))
            pairs = rec[(base, v)][1]
            moved = {n for n, _, x in pairs if a.get(n) != x} - {"P_LEVEL"}
            named = set(var[base][v].get("set", {}))
            if not moved <= named:
                raise SystemExit("%s %s changes %s, which its variants.json set does not name" % (base, v, sorted(moved - named)))
            for n, _, x in pairs:
                if n in var[base][v].get("set", {}) and var[base][v]["set"][n] != x:
                    raise SystemExit("%s %s: %s is %d in the record, %d in variants.json" % (base, v, n, x, var[base][v]["set"][n]))
            name, line = NAMES[(base, v)]
            rows.append((FIRST + len(rows), name, line, base, v, pairs))
    if len(rows) != len(NAMES):
        raise SystemExit("NAMES has %d entries, the records give %d variants" % (len(NAMES), len(rows)))
    names = [r[1] for r in rows]
    for n in names:
        if len(n) > NAME_MAX or not NAME_CHARS.match(n) or has_brand(n):
            raise SystemExit("bad name %r: at most %d of A-Z 0-9 space -, no brand" % (n, NAME_MAX))
    if len(set(names)) != len(names):
        raise SystemExit("two Move-only presets share a name")
    return rows


def header():
    rows = table()
    width = max(len(r[5]) for r in rows) * 2 + 1
    ids = {}
    for r in rows:
        for n, i, _ in r[5]:
            ids[n] = i
    out = ["/* SPDX-License-Identifier: GPL-3.0-only */",
           "/* GENERATED by tools/gen_move_presets.py from move/presets/proposed_records.txt and variants.json - do not",
           " * edit. The Move-only factory presets F%02d-F%02d: the factory preset review's B and C variants of F01-F19"
           % (FIRST, FIRST + len(rows) - 1),
           " * (the user, 2026-11-01: the Move gets every variant, the FM-1 keeps presets.c as it is). Each record is its",
           " * full parameter list, (id, value) pairs, 0xFF at the end. Included by move/move_bridge.h only: never in",
           " * the FM-1 image. */",
           "#ifndef MOVE_PRESETS_H", "#define MOVE_PRESETS_H",
           "#define MB_PX_N %du                       /* F%02d.. */" % (len(rows), FIRST),
           "#define MB_PX_FIRST %du                   /* the first one's number (F%02d) */" % (FIRST, FIRST),
           "#define MB_PX_REC %du                    /* bytes a record: the pairs, then 0xFF */" % width,
           "/* the ids as the records were read back (params.c's ids are append-only) */"]
    names = sorted(ids, key=lambda n: ids[n])
    for k in range(0, len(names), 4):
        part = names[k:k + 4]
        out.append("_Static_assert(%s, \"params.c ids\");" % " && ".join("%s == %d" % (n, ids[n]) for n in part))
    out.append("static const char *const MB_PX_NAME[MB_PX_N] = {")
    for k in range(0, len(rows), 6):
        out.append("    " + " ".join('"%s",' % r[1] for r in rows[k:k + 6]))
    out.append("};")
    out.append("static const uint8_t MB_PX_DATA[MB_PX_N][MB_PX_REC] = {")
    for num, name, line, base, v, pairs in rows:
        out.append("    /* F%02d %s: %s %s, %s */" % (num, name, base, v, line))
        body = ["%s, %d" % (n, x) for n, _, x in pairs] + ["0xFF"]
        cur = "    {"
        for k, b in enumerate(body):
            item = b + ("," if k < len(body) - 1 else "},")
            if len(cur) + len(item) + 1 > 118:
                out.append(cur.rstrip())
                cur = "     "
            cur += item + " "
        out.append(cur.rstrip())
    out.append("};")
    out.append("#endif")
    return "\n".join(out) + "\n"


def main():
    text = header()
    if "--check" in sys.argv[1:]:
        try:
            with open(OUT) as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != text:
            print("move/move_presets.h is stale: run tools/gen_move_presets.py")
            return 1
        print("move/move_presets.h is current (%d Move-only presets)" % len(table()))
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote move/move_presets.h (%d Move-only presets)" % len(table()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
