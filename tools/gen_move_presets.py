#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move-only factory presets (move/move_presets.h) from the factory preset review's records.

The review (tools/preset_review.py, AGENTS.md "The factory preset review") rendered every factory preset F01-F19 as
presets.c has it (A) and two variants of it (B, C). The user's pick (2026-11-01): the Move gets every variant, the
FM-1 keeps its presets as they are. So on the Move F01-F19 stay the A presets (presets.c, the FM-1's, untouched) and
the 38 variants follow as F20-F57, in the review's order (F01 B, F01 C, F02 B, ... F19 C). They live in their own
table, compiled into the Move build only (move/move_bridge.h includes the header; the FM-1 image never sees it), so
presets.c's PR_FDATA can change its format without touching this.

  tools/gen_move_presets.py           write move/move_presets.h, docs/MOVE.md's F20.. table and plugin/ui/tools/frui_fact.h
  tools/gen_move_presets.py --check   exit 1 if the committed header or table is not what the sources give
  tools/gen_move_presets.py --sync    first bring proposed_records.txt to presets.c (sync() below), then write

--sync: the records' A are presets.c's factory records (over the ids a record has: params.c's later ids sit at their
defaults); where presets.c moved a value since, A takes it and so do its B / C, the rule every LEVEL change has
followed by hand (af5202d5, 30599124, ...): P_LEVEL by the same step, any other id the new value unless the
variant's "set" names it. A comment line in the records says what moved. No render: the variants' own make-up stays.

The sources (copies of the review's output, committed): move/presets/proposed_records.txt (every variant's full
id = value list, the defaults included, read back from the host build) and move/presets/variants.json (what each
variant changes). The check here: every B / C in variants.json has a record, and a record differs from its A only
in the ids its "set" names, P_LEVEL (the level solver) aside. The names are the Move's own (variants.json's "name"): generic,
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
NEW_VARIANTS = os.path.join(SRC_DIR, "new_variants.json")   # B / C for a preset new in presets.c, by name (--sync)
OUT = os.path.join(ROOT, "move", "move_presets.h")
DOC = os.path.join(ROOT, "docs", "MOVE.md")                 # its appendix table of F20-F57 (doc_table below)
FACT = os.path.join(ROOT, "plugin", "ui", "tools", "frui_fact.h")   # the plugin UI renderer's PRESETS sheet list
FIRST = 20                                  # F20: the first Move-only preset (after presets.c's 19)
NAME_MAX = 12                               # presets.c preset_t name[12]: "F20 " + 12 = the 16 the screen shows

# Each variant's name and its line for docs/MOVE.md live in variants.json ("name", "line"), with the variant: --sync
# carries a preset's variants by its name, so a reordered presets.c takes them along. The variant's character, in
# the FM-1's words.
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
            if not var[base][v].get("name") or not var[base][v].get("line"):
                raise SystemExit("%s %s has no name / line in variants.json" % (base, v))
            pairs = rec[(base, v)][1]
            moved = {n for n, _, x in pairs if a.get(n) != x} - {"P_LEVEL"}
            named = set(var[base][v].get("set", {}))
            if not moved <= named:
                raise SystemExit("%s %s changes %s, which its variants.json set does not name" % (base, v, sorted(moved - named)))
            for n, _, x in pairs:
                if n in var[base][v].get("set", {}) and var[base][v]["set"][n] != x:
                    raise SystemExit("%s %s: %s is %d in the record, %d in variants.json" % (base, v, n, x, var[base][v]["set"][n]))
            name, line = var[base][v]["name"], var[base][v]["line"]
            rows.append((FIRST + len(rows), name, line, base, v, pairs))
    extra = sorted(set(b for b, _ in rec) - set(var))
    if extra:
        raise SystemExit("proposed_records.txt has %s, variants.json has no entry for it" % ", ".join(extra))
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


def doc_text(cur):
    """docs/MOVE.md with its appendix table ("| F20 | NAME | F01 B: line |" rows) from the records"""
    rows = ["| F%02d | %s | %s %s: %s |" % (num, name, base, v, line) for num, name, line, base, v, _ in table()]
    ls = cur.split("\n")
    idx = [i for i, ln in enumerate(ls) if re.match(r"^\| F\d\d \| ", ln)]
    if not idx or idx != list(range(idx[0], idx[-1] + 1)):
        raise SystemExit("docs/MOVE.md: no single F20.. table to write")
    return "\n".join(ls[:idx[0]] + rows + ls[idx[-1] + 1:])


def fact_text():
    """plugin/ui/tools/frui_fact.h: F01..F57's names and instruments (P_TYPE) for tools/frui_render.c's PRESETS sheet"""
    recs, rows, names = presets_c()
    ls = [(n, r.get("P_TYPE", rows["P_TYPE"][0])) for n, r in zip(names, recs)]
    ls += [(name, dict((n, x) for n, _, x in pairs)["P_TYPE"]) for _, name, _, _, _, pairs in table()]
    out = ["/* SPDX-License-Identifier: GPL-3.0-only */",
           "/* GENERATED by tools/gen_move_presets.py from firmware/src/presets.c and the Move's records - do not edit.",
           " * The factory presets' names and instruments (F01..F%02d), for frui_render.c's PRESETS sheet. */" % len(ls),
           "#define FACT_N %d" % len(ls),
           "static const struct { const char *name; int type; } FACT[FACT_N] = {"]
    cur = "   "
    for n, t in ls:
        it = ' {"%s", %d},' % (n, t)
        if len(cur) + len(it) > 118:
            out.append(cur)
            cur = "   "
        cur += it
    out += [cur, "};"]
    return "\n".join(out) + "\n"


def presets_c():
    """presets.c's factory records [{P_X: v}] (PR_FDATA: id / value pairs, 0xFF ends each), params.c's
    {P_X: (default, hi)} and presets.c's names (PR_FNAME)"""
    src = os.path.join(ROOT, "firmware", "src")
    s = open(os.path.join(src, "presets.c")).read()
    body = re.sub(r"/\*.*?\*/", "", re.search(r"PR_FDATA\[\] = \{(.*?)\n\};", s, re.S).group(1), flags=re.S)
    recs, cur = [], []
    for x in (t.strip() for t in body.split(",")):
        if not x:
            continue
        if x == "0xFF":
            recs.append(dict(zip(cur[0::2], (int(v) for v in cur[1::2]))))
            cur = []
        else:
            cur.append(x)
    names = re.findall(r'"([^"]*)"', re.search(r"PR_FNAME\[PR_NF\] = \{(.*?)\};", s, re.S).group(1))
    if len(names) != len(recs):
        raise SystemExit("presets.c: %d names, %d records" % (len(names), len(recs)))
    p = open(os.path.join(src, "params.c")).read()
    rows = {m.group(1): (int(m.group(3)), int(m.group(2)))     # (default, hi)
            for m in re.finditer(r"\[(P_[A-Z0-9_]+)\] = \{\"[^\"]*\", (\d+), (\d+), F_\w+, (\d+)", p)}
    return recs, rows, names


def sync(today=None):
    """proposed_records.txt and variants.json to presets.c (the module's docstring), by preset NAME: a preset that
    moved slot takes its records and its variants along; a preset presets.c dropped loses them (git keeps them); a new
    preset gets its A, and its B / C from new_variants.json when that names it (else A only). Returns what moved."""
    import time
    recs, rows, pnames = presets_c()
    rec = records()
    with open(VARIANTS) as f:
        var = json.load(f)
    try:
        with open(NEW_VARIANTS) as f:
            nvar = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except OSError:
        nvar = {}
    old = {}                                        # name -> old base
    for (b, v), (bname, _) in rec.items():
        if v == "A":
            old[bname] = b
    ids = [(n, i) for n, i, _ in rec[("F01", "A")][1]]
    new_rec, new_var, moved, order = {}, {}, [], []
    for k, r in enumerate(recs):
        base, name = "F%02d" % (k + 1), pnames[k]
        unknown = set(r) - {n for n, _ in ids}
        if unknown:
            raise SystemExit("%s in presets.c sets %s, which the records do not have" % (base, sorted(unknown)))
        a_new = {n: r.get(n, rows[n][0]) for n, _ in ids}
        if name not in old:                         # a new preset
            e = nvar.get(name)
            new_rec[(base, "A")] = (name, [(n, i, a_new[n]) for n, i in ids])
            ent = {"note": (e or {}).get("note", "new in presets.c %s" % (today or time.strftime("%Y-%m-%d")))}
            for v in sorted(x for x in (e or {}) if re.fullmatch(r"[B-Z]", x)):
                vals = dict(a_new, **e[v].get("set", {}))
                vals["P_LEVEL"] = max(0, min(rows["P_LEVEL"][1], a_new["P_LEVEL"] + int(e[v].get("level_delta", 0))))
                new_rec[(base, v)] = (name, [(n, i, vals[n]) for n, i in ids])
                ent[v] = {x: y for x, y in e[v].items() if x != "level_delta"}
            for x in ("pick", "why"):
                if e and x in e:
                    ent[x] = e[x]
            new_var[base] = ent
            vs = [v for v in ent if re.fullmatch(r"[B-Z]", v)]
            moved.append("%s %s new (%s)" % (base, name, "with " + " / ".join(vs) if vs else "A only"))
            order.append(base)
            continue
        ob = old[name]
        a_old = {n: v for n, _, v in rec[(ob, "A")][1]}
        d = {n: a_new[n] - a_old[n] for n in a_old if a_new[n] != a_old[n]}
        new_var[base] = var.get(ob, {})
        what = []
        if ob != base:
            what.append("from %s" % ob)
        if d:
            what.append(" ".join("%s %+d" % (n[2:], x) for n, x in sorted(d.items())))
        if what:
            moved.append("%s %s %s" % (base, name, ", ".join(what)))
        for (b, v), (bname, pairs) in sorted(rec.items()):
            if b != ob:
                continue
            setn = var.get(ob, {}).get(v, {}).get("set", {}) if v != "A" else {}
            out = []
            for n, i, x in pairs:
                if n in d:
                    if v == "A":
                        x = a_new[n]
                    elif n == "P_LEVEL":
                        x = max(0, min(rows[n][1], x + d[n]))
                    elif n not in setn:
                        x = a_new[n]
                out.append((n, i, x))
            new_rec[(base, v)] = (bname, out)
        order.append(base)
    for name, ob in sorted(old.items(), key=lambda t: t[1]):
        if name not in pnames:
            vs = [v for v in var.get(ob, {}) if re.fullmatch(r"[B-Z]", v)]
            moved.append("%s %s dropped (its %s with it)" % (ob, name, " / ".join(vs) if vs else "A"))
    if not moved:
        return []
    lines = open(RECORDS).read().splitlines()
    first = next(i for i, ln in enumerate(lines) if LINE.match(ln.strip()))
    head = lines[:first]
    while head and not head[-1].strip():
        head.pop()
    head.append("# %s (tools/gen_move_presets.py --sync): with presets.c, by name, the B / C records too: %s." % (
        today or time.strftime("%Y-%m-%d"), "; ".join(moved)))
    body = []
    for base in order:
        body.append("")
        for v in sorted(v for b, v in new_rec if b == base):
            bname, pairs = new_rec[(base, v)]
            body.append("%s %s (%s): %s" % (base, v, bname, " ".join("%s[%d]=%d" % p for p in pairs)))
    with open(RECORDS, "w") as f:
        f.write("\n".join(head + body) + "\n")
    with open(VARIANTS, "w") as f:
        f.write(json.dumps(new_var, indent=1, ensure_ascii=False) + "\n")
    return moved


def main():
    if "--sync" in sys.argv[1:]:
        moved = sync()
        print("proposed_records.txt / variants.json: %s" % ("; ".join(moved) if moved else "every A is presets.c's already"))
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
        doc = open(DOC).read()
        if doc_text(doc) != doc:
            print("docs/MOVE.md's F20.. table is stale: run tools/gen_move_presets.py")
            return 1
        if not os.path.exists(FACT) or open(FACT).read() != fact_text():
            print("plugin/ui/tools/frui_fact.h is stale: run tools/gen_move_presets.py")
            return 1
        print("move/move_presets.h is current (%d Move-only presets)" % len(table()))
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    doc = open(DOC).read()
    with open(DOC, "w") as f:
        f.write(doc_text(doc))
    with open(FACT, "w") as f:
        f.write(fact_text())
    print("wrote move/move_presets.h and docs/MOVE.md's table (%d Move-only presets)" % len(table()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
